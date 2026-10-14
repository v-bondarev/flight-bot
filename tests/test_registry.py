"""Выбор дат для подписки: прошедшие отрезаем, по одной кнопке на дату;
даты, которых нет на табло, добираем из расписания на HORIZON_DAYS вперёд."""
import asyncio
import dataclasses
import types

import pytest
from datetime import date

from flight_bot import registry, storage
from flight_bot.models import FlightSnapshot, Leg
from flight_bot.registry import upcoming

TODAY = date(2026, 10, 14)


def _snap(d, dest="BAH", source=""):
    return FlightSnapshot(flight="GF013", date=d, direction="departure",
                          origin_iata="SVO", dest_iata=dest, status="", source=source)


class _Schedule:
    """Расписание-заглушка: считает вызовы, чтобы проверить кэш."""

    name = "aerodatabox.com"

    def __init__(self, snaps):
        self.snaps, self.calls = snaps, 0

    async def fetch(self, flight_no, date=None, direction="departure"):
        self.calls += 1
        return self.snaps


def _setup(monkeypatch, boards, schedule):
    async def probe(flight_no, date=None):
        return registry.Found(boards)
    monkeypatch.setattr(registry, "probe", probe)
    monkeypatch.setattr(registry, "SCHEDULE", schedule)
    monkeypatch.setattr(registry, "_schedule_cache", {})
    monkeypatch.setattr(registry, "_schedule_inflight", {})
    monkeypatch.setattr(registry, "_budget_conn", storage.connect(":memory:"))


def _spent():
    return registry._budget_conn.execute("SELECT COUNT(*) FROM schedule_calls").fetchone()[0]


def _clock(monkeypatch):
    """Подменяет время только в registry: у цикла asyncio своё."""
    clock = types.SimpleNamespace(t=1000.0)
    monkeypatch.setattr(registry, "time", types.SimpleNamespace(monotonic=lambda: clock.t))
    return clock


def test_upcoming_drops_past_dates_keeps_today_and_dedups():
    snaps = [_snap("2026-09-04"), _snap("2026-09-05", "TBS"),
             _snap("2026-09-05", "TBS"), _snap("2026-09-06", "TBS")]
    got = upcoming(snaps, today=date(2026, 9, 5))
    assert [s.date for s in got] == ["2026-09-05", "2026-09-06"]


def test_upcoming_empty_when_all_past():
    assert upcoming([_snap("2026-09-04")], today=date(2026, 9, 5)) == []


def test_search_adds_schedule_dates_missing_on_boards_within_horizon(monkeypatch):
    days = [f"2026-10-{d}" for d in range(13, 21)]          # 13…20: вчера … +6
    sched = _Schedule([_snap(d, source="aerodatabox.com") for d in days])
    _setup(monkeypatch, [_snap("2026-10-14", source="dme.ru"),
                         _snap("2026-10-15", source="dme.ru")], sched)
    got = asyncio.run(registry.search("GF013", today=TODAY))
    assert [s.date for s in got] == [f"2026-10-{d}" for d in range(14, 20)]   # сегодня +5
    assert [s.source for s in got[:2]] == ["dme.ru", "dme.ru"]   # табло богаче — оно первое
    assert {s.source for s in got[2:]} == {"aerodatabox.com"}


def test_search_without_schedule_key_is_boards_only(monkeypatch):
    _setup(monkeypatch, [_snap("2026-10-14")], None)
    got = asyncio.run(registry.search("GF013", today=TODAY))
    assert [s.date for s in got] == ["2026-10-14"]


def test_schedule_is_cached_and_failure_is_not_fatal(monkeypatch):
    sched = _Schedule([_snap("2026-10-16")])
    _setup(monkeypatch, [], sched)
    asyncio.run(registry.search("GF013", today=TODAY))
    asyncio.run(registry.search("GF013", today=TODAY))
    assert sched.calls == 1                                  # запрос платный — кэш

    class _Broken:

        name = "aerodatabox.com"

        async def fetch(self, *a, **kw):
            raise RuntimeError("429")
    _setup(monkeypatch, [_snap("2026-10-14")], _Broken())
    assert [s.date for s in asyncio.run(registry.search("GF013", today=TODAY))] == ["2026-10-14"]


def test_schedule_failure_is_cached_briefly(monkeypatch):
    class _Broken:
        name = "aerodatabox.com"

        calls = 0

        async def fetch(self, *a, **kw):
            self.calls += 1
            raise RuntimeError("429")
    broken = _Broken()
    _setup(monkeypatch, [], broken)
    clock = _clock(monkeypatch)
    assert asyncio.run(registry.schedule("GF013")) == []
    assert asyncio.run(registry.schedule("GF013")) == []
    assert broken.calls == 1                         # не ждём таймаут на каждом поиске
    clock.t += registry.SCHEDULE_FAIL_TTL_SEC + 1
    asyncio.run(registry.schedule("GF013"))
    assert broken.calls == 2                         # но и не прячем расписание на 6 ч


def test_concurrent_searches_share_one_paid_request(monkeypatch):
    class _Slow(_Schedule):
        async def fetch(self, *a, **kw):
            self.calls += 1
            await self.release.wait()
            return self.snaps
    sched = _Slow([_snap("2026-10-16")])
    _setup(monkeypatch, [], sched)

    async def run():
        sched.release = asyncio.Event()
        a, b, c = (asyncio.create_task(registry.schedule("GF013")) for _ in range(3))
        await asyncio.sleep(0)
        c.cancel()                                   # ушедший ждать не отменяет общий запрос
        sched.release.set()
        return await a, await b
    ra, rb = asyncio.run(run())
    assert sched.calls == 1
    assert ra == rb and [s.date for s in ra] == ["2026-10-16"]
    assert registry._schedule_inflight == {}
    assert _spent() == 1                             # ждавшие бюджет не тратят


def test_request_cancelled_before_start_does_not_poison_number(monkeypatch):
    sched = _Schedule([_snap("2026-10-16")])
    _setup(monkeypatch, [], sched)

    async def run():
        waiter = asyncio.create_task(registry.schedule("GF013"))
        await asyncio.sleep(0)                       # задача запроса создана, но не стартовала
        assert sched.calls == 0
        registry._schedule_inflight["GF013"].cancel()   # так бывает при остановке цикла
        with pytest.raises(asyncio.CancelledError):
            await waiter
        return await registry.schedule("GF013")
    assert [s.date for s in asyncio.run(run())] == ["2026-10-16"]
    assert sched.calls == 1


def test_schedule_cache_is_bounded_and_drops_expired(monkeypatch):
    _setup(monkeypatch, [], _Schedule([_snap("2026-10-16")]))
    monkeypatch.setattr(registry, "SCHEDULE_CACHE_MAX", 2)
    clock = _clock(monkeypatch)
    for no in ("AA1", "BB2", "CC3"):
        asyncio.run(registry.schedule(no))
    assert list(registry._schedule_cache) == ["BB2", "CC3"]    # вытеснен самый старый
    clock.t += registry.SCHEDULE_TTL_SEC + 1
    assert registry.scheduled("BB2", "2026-10-16") is None     # протухшее не показываем
    asyncio.run(registry.schedule("DD4"))
    assert list(registry._schedule_cache) == ["DD4"]           # протухшие ушли при записи


def test_schedule_budget_spent_only_on_real_requests(monkeypatch):
    sched = _Schedule([_snap("2026-10-16")])
    _setup(monkeypatch, [], sched)
    monkeypatch.setattr(registry, "SCHEDULE_BUDGET", 2)
    for no in ("AA1", "AA1", "BB2", "CC3"):
        asyncio.run(registry.schedule(no))
    assert sched.calls == 2                          # кэш-хит бесплатен, CC3 — сверх бюджета
    assert "CC3" not in registry._schedule_cache     # отказ не кэшируем: окно освободится


def test_failed_schedule_request_spends_budget(monkeypatch):
    class _Broken:
        name = "aerodatabox.com"

        async def fetch(self, *a, **kw):
            raise RuntimeError("timeout")
    _setup(monkeypatch, [], _Broken())
    asyncio.run(registry.schedule("AA1"))
    assert _spent() == 1                             # тарифицируют ли ошибки — не знаем


def test_schedule_without_budget_db_never_pays(monkeypatch):
    sched = _Schedule([_snap("2026-10-16")])
    _setup(monkeypatch, [], sched)
    monkeypatch.setattr(registry, "_budget_conn", None)
    assert asyncio.run(registry.schedule("AA1")) == []
    assert sched.calls == 0


def test_budget_db_error_keeps_board_search_alive(monkeypatch):
    sched = _Schedule([_snap("2026-10-16")])
    _setup(monkeypatch, [_snap("2026-10-14")], sched)
    registry._budget_conn.close()                    # диск/блокировка: списать не можем
    got = asyncio.run(registry.search("GF013", today=TODAY))
    assert [s.date for s in got] == ["2026-10-14"]   # табло живо, платного запроса нет
    assert sched.calls == 0


def test_scheduled_card_from_cache_but_polling_never_sees_schedule(monkeypatch):
    sched = _Schedule([_snap("2026-10-17", source="aerodatabox.com")])
    _setup(monkeypatch, [], sched)
    monkeypatch.setattr(registry, "SOURCES", [])
    asyncio.run(registry.search("GF013", today=TODAY))
    assert registry.scheduled("GF013", "2026-10-17").source == "aerodatabox.com"
    assert registry.scheduled("GF013", "2026-10-18") is None
    # опрос: застывший план не выдаём за живой статус → поллер уснёт до накануне
    assert asyncio.run(registry.fetch_for("GF013", "2026-10-17", "departure")) is None
    assert sched.calls == 1


def test_inbound_flight_goes_to_arrival_board_dated_by_arrival(monkeypatch):
    inbound = FlightSnapshot(
        flight="EK133", date="2026-10-16", direction="departure", origin_iata="IST",
        dest_iata="DME", status="", source="aerodatabox.com",
        departure=Leg(plan="2026-10-16T23:50:00+04:00"),
        arrival=Leg(plan="2026-10-17T04:10:00+03:00"))
    board = dataclasses.replace(inbound, direction="arrival", date="2026-10-15", source="dme.ru")
    _setup(monkeypatch, [board], _Schedule([dataclasses.replace(inbound, date="2026-10-14",
                                                                arrival=Leg(plan="2026-10-15T04:10:00+03:00")),
                                            inbound]))
    got = asyncio.run(registry.search("EK133", today=TODAY))
    assert [(s.date, s.direction, s.source) for s in got] == [
        ("2026-10-15", "arrival", "dme.ru"),                # табло, без дубля из расписания
        ("2026-10-17", "arrival", "aerodatabox.com")]       # дата прилёта, табло прилёта


class _Board:
    """Табло-заглушка: задержка, ответ, счётчик вызовов."""

    def __init__(self, name, snaps=(), delay=0.0):
        self.name, self.snaps, self.delay, self.calls = name, list(snaps), delay, 0

    async def fetch(self, flight_no, date=None, direction="departure"):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return [s for s in self.snaps if s.direction == direction and (not date or s.date == date)]


def test_probe_asks_boards_in_parallel_keeps_priority_and_skips_slow(monkeypatch):
    import time as _time
    monkeypatch.setattr(registry, "_fetch_cache", {})
    monkeypatch.setattr(registry, "PROBE_TIMEOUT_SEC", 0.5)
    slow = _Board("slow", [_snap("2026-10-14", source="slow")], delay=5)
    first = _Board("first", [_snap("2026-10-14", source="first")], delay=0.2)
    second = _Board("second", [_snap("2026-10-15", source="second")], delay=0.1)
    monkeypatch.setattr(registry, "SOURCES", [slow, first, second])
    t = _time.monotonic()
    got = asyncio.run(registry.probe("GF013"))
    assert _time.monotonic() - t < 1.5                       # не 5 с медленного и не сумма
    assert [s.source for s in got] == ["first"]              # приоритет — порядок SOURCES


def test_probe_asks_airlabs_only_when_boards_are_empty(monkeypatch):
    monkeypatch.setattr(registry, "_fetch_cache", {})
    board = _Board("svo.aero", [_snap("2026-10-14", source="svo.aero")])
    from flight_bot.sources.airlabs import AirlabsSource

    class _Airlabs(_Board, AirlabsSource):
        pass
    airlabs = _Airlabs("airlabs.co", [_snap("2026-10-14", source="airlabs.co")])
    monkeypatch.setattr(registry, "SOURCES", [board, airlabs])
    assert asyncio.run(registry.probe("GF013"))[0].source == "svo.aero"
    assert airlabs.calls == 0                                # платный — только если табло пусты


def test_ten_concurrent_polls_of_one_flight_make_one_request(monkeypatch):
    monkeypatch.setattr(registry, "_fetch_cache", {})
    board = _Board("svo.aero", [_snap("2026-10-14", source="svo.aero")], delay=0.05)
    monkeypatch.setattr(registry, "SOURCES", [board])

    async def ten():
        return await asyncio.gather(*(registry.fetch_for("GF013", "2026-10-14", "departure")
                                      for _ in range(10)))
    got = asyncio.run(ten())
    assert all(s is not None for s in got) and board.calls == 1
    asyncio.run(registry.fetch_for("GF013", "2026-10-14", "departure"))
    assert board.calls == 1                                  # в пределах TTL — из кэша


def test_fetch_cache_expires_and_errors_are_not_cached(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(registry, "_fetch_cache", {})
    monkeypatch.setattr(registry, "_clock", lambda: now[0])
    board = _Board("svo.aero", [_snap("2026-10-14", source="svo.aero")])
    monkeypatch.setattr(registry, "SOURCES", [board])
    asyncio.run(registry.fetch_for("GF013", "2026-10-14", "departure"))
    now[0] += registry.FETCH_TTL_SEC + 1
    asyncio.run(registry.fetch_for("GF013", "2026-10-14", "departure"))
    assert board.calls == 2

    class _Flaky(_Board):
        async def fetch(self, *a, **kw):
            self.calls += 1
            raise RuntimeError("502")
    flaky = _Flaky("dme.ru")
    monkeypatch.setattr(registry, "SOURCES", [flaky])
    asyncio.run(registry.fetch_for("GF013", "2026-10-14", "departure"))
    asyncio.run(registry.fetch_for("GF013", "2026-10-14", "departure"))
    assert flaky.calls == 2                                  # ошибку не запоминаем


def test_search_timeout_does_not_cancel_shared_request_for_polling(monkeypatch):
    monkeypatch.setattr(registry, "_fetch_cache", {})
    monkeypatch.setattr(registry, "PROBE_TIMEOUT_SEC", 0.05)
    board = _Board("svo.aero", [_snap("2026-10-14", source="svo.aero")], delay=0.2)
    monkeypatch.setattr(registry, "SOURCES", [board])

    async def both():
        poll = asyncio.ensure_future(registry.fetch_for("GF013", "2026-10-14", "departure"))
        found = await registry.probe("GF013", "2026-10-14")   # поиск сдаётся по таймауту
        return found, await poll
    found, polled = asyncio.run(both())
    assert found == [] and polled is not None
    assert board.calls == 2          # вылет — один общий запрос, плюс прилёт из поиска


def test_fetch_for_sticks_to_the_record_key_when_a_date_has_two(monkeypatch):
    import dataclasses as dc
    monkeypatch.setattr(registry, "_fetch_cache", {})
    a = _snap("2026-10-15", source="dubaiairports.ae")
    first, second = dc.replace(a, key="96"), dc.replace(a, key="97")
    monkeypatch.setattr(registry, "SOURCES", [_Board("dubaiairports.ae", [first, second])])
    got = asyncio.run(registry.fetch_for("GF013", "2026-10-15", "departure", key="97"))
    assert got.key == "97"
    assert asyncio.run(registry.fetch_for("GF013", "2026-10-15", "departure")).key == "96"


def test_source_errors_feed_health_and_empty_answer_is_success(monkeypatch):
    from flight_bot import health
    seen = []

    class _H:
        def fail(self, name, exc): seen.append(("fail", name))
        def ok(self, name): seen.append(("ok", name))
    monkeypatch.setattr(health, "HEALTH", _H())
    monkeypatch.setattr(registry, "_fetch_cache", {})

    class _Broken(_Board):
        async def fetch(self, *a, **kw):
            raise RuntimeError("TLS")
    monkeypatch.setattr(registry, "SOURCES", [_Broken("pulkovoairport.ru"), _Board("svo.aero")])
    asyncio.run(registry.fetch_for("GF013", "2026-10-14", "departure"))
    assert ("fail", "pulkovoairport.ru") in seen and ("ok", "svo.aero") in seen   # пусто — не сбой


def test_probe_asks_airlabs_once_not_per_direction(monkeypatch):
    monkeypatch.setattr(registry, "_fetch_cache", {})
    from flight_bot.sources.airlabs import AirlabsSource

    class _Airlabs(_Board, AirlabsSource):
        pass
    airlabs = _Airlabs("airlabs.co", [_snap("2026-10-14", source="airlabs.co")])
    monkeypatch.setattr(registry, "SOURCES", [_Board("svo.aero"), airlabs])
    assert asyncio.run(registry.probe("EK770"))[0].source == "airlabs.co"
    assert airlabs.calls == 1                                # направление AirLabs не важно


def test_search_reports_sources_that_failed(monkeypatch):
    """«Не нашёл» и «табло лежат» — разные ответы пользователю."""
    monkeypatch.setattr(registry, "_fetch_cache", {})
    monkeypatch.setattr(registry, "SCHEDULE", None)

    class _Broken(_Board):
        async def fetch(self, *a, **kw):
            raise RuntimeError("TLS")
    monkeypatch.setattr(registry, "SOURCES", [_Broken("pulkovoairport.ru"), _Board("svo.aero")])
    got = asyncio.run(registry.search("GF013", today=TODAY))
    assert got == [] and got.down == {"pulkovoairport.ru"}
    monkeypatch.setattr(registry, "SOURCES", [_Board("svo.aero", [_snap("2026-10-14", source="svo.aero")])])
    got = asyncio.run(registry.search("GF014", today=TODAY))
    assert len(got) == 1 and got.down == set()
