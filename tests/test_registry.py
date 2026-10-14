"""Выбор дат для подписки: прошедшие отрезаем, по одной кнопке на дату;
даты, которых нет на табло, добираем из расписания на HORIZON_DAYS вперёд."""
import asyncio
import dataclasses
import types
from datetime import date

from flight_bot import registry
from flight_bot.models import FlightSnapshot, Leg
from flight_bot.registry import upcoming

TODAY = date(2026, 10, 14)


def _snap(d, dest="BAH", source=""):
    return FlightSnapshot(flight="GF013", date=d, direction="departure",
                          origin_iata="SVO", dest_iata=dest, status="", source=source)


class _Schedule:
    """Расписание-заглушка: считает вызовы, чтобы проверить кэш."""

    def __init__(self, snaps):
        self.snaps, self.calls = snaps, 0

    async def fetch(self, flight_no, date=None, direction="departure"):
        self.calls += 1
        return self.snaps


def _setup(monkeypatch, boards, schedule):
    async def probe(flight_no, date=None):
        return boards
    monkeypatch.setattr(registry, "probe", probe)
    monkeypatch.setattr(registry, "SCHEDULE", schedule)
    monkeypatch.setattr(registry, "_schedule_cache", {})
    monkeypatch.setattr(registry, "_schedule_inflight", {})


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
        async def fetch(self, *a, **kw):
            raise RuntimeError("429")
    _setup(monkeypatch, [_snap("2026-10-14")], _Broken())
    assert [s.date for s in asyncio.run(registry.search("GF013", today=TODAY))] == ["2026-10-14"]


def test_schedule_failure_is_cached_briefly(monkeypatch):
    class _Broken:
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
        flight="EK133", date="2026-10-16", direction="departure", origin_iata="DXB",
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
