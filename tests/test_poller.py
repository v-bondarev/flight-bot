"""Поллер: тишина на первом круге, пуш на изменение, адаптивный интервал,
снятие подписки после прилёта / отмены / протухания."""
import asyncio
from datetime import datetime, timedelta, timezone

from flight_bot import poller, registry, storage
from flight_bot.models import Boarding, FlightSnapshot, Leg

MSK = timezone(timedelta(hours=3))
DEP = "2026-09-06T23:25:00+03:00"
ARR = "2026-09-07T08:00:00+03:00"
NOW = datetime(2026, 9, 6, 21, 0, tzinfo=MSK)   # за 2 ч 25 мин до вылета


def _snap(**kw):
    base = dict(flight="SU2128", date="2026-09-06", direction="departure",
                origin_iata="SVO", dest_iata="AYT", status="",
                departure=Leg(plan=DEP), arrival=Leg(plan=ARR))
    base.update(kw)
    return FlightSnapshot(**base)


class _Sends:
    def __init__(self):
        self.msgs = []
        self.threads = []

    async def __call__(self, chat_id, text, thread_id=None):
        self.msgs.append((chat_id, text))
        self.threads.append(thread_id)


def _fixed(snap, monkeypatch):
    async def fake(flight_no, date, direction, prefer=None, key=None):
        return snap
    monkeypatch.setattr(registry, "fetch_for", fake)


def _run(conn, send, now=NOW):
    asyncio.run(poller.poll_once(conn, send, now=now))


def test_first_poll_silent_then_change_notifies(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()
    _fixed(_snap(gate="129"), monkeypatch)
    _run(conn, send)
    assert send.msgs == []                          # первый снимок — молча запомнили

    _fixed(_snap(gate="141"), monkeypatch)
    _run(conn, send, NOW + timedelta(minutes=10))   # next_at уже наступил
    assert len(send.msgs) == 1 and send.msgs[0][0] == 42
    assert "141 (был 129)" in send.msgs[0][1]


def test_push_goes_to_subscription_topic(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, -1001, "SU2128", "2026-09-06", thread_id=7)
    send = _Sends()
    _fixed(_snap(gate="129"), monkeypatch)
    _run(conn, send)
    _fixed(_snap(gate="141"), monkeypatch)
    _run(conn, send, NOW + timedelta(minutes=10))
    assert send.threads == [7]


def test_not_due_is_skipped(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()
    _fixed(_snap(gate="129"), monkeypatch)
    _run(conn, send)
    _fixed(_snap(gate="141"), monkeypatch)
    _run(conn, send, NOW + timedelta(minutes=1))    # рано: интервал у вылета 5 мин
    assert send.msgs == []


def test_interval_adapts_to_flight_state():
    far = _snap(departure=Leg(plan="2026-09-07T12:00:00+03:00"))
    assert poller.interval_for(far, NOW, near=300) == poller.FAR_SEC
    assert poller.interval_for(_snap(), NOW, near=300) == 300
    boarding = _snap(boarding=Boarding(start_fact="2026-09-06T22:45:00+03:00"))
    assert poller.interval_for(boarding, NOW, near=300) == poller.BOARDING_SEC
    airborne = _snap(departure=Leg(plan=DEP, fact="2026-09-06T23:30:00+03:00"))
    assert poller.interval_for(airborne, NOW, near=300) == 300


def test_arrival_and_cancellation_deactivate(monkeypatch):
    for final in (_snap(arrival=Leg(plan=ARR, fact="2026-09-07T07:58:00+03:00")),
                  _snap(status="Рейс отменен")):
        conn = storage.connect(":memory:")
        storage.add(conn, 42, "SU2128", "2026-09-06")
        send = _Sends()
        _fixed(_snap(), monkeypatch)
        _run(conn, send)
        _fixed(final, monkeypatch)
        _run(conn, send, NOW + timedelta(minutes=10))
        assert len(send.msgs) == 1
        assert storage.active_all(conn) == []


def test_expires_a_day_after_flight_date(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()

    async def none(flight_no, date, direction, prefer=None, key=None):
        return None
    monkeypatch.setattr(registry, "fetch_for", none)
    _run(conn, send, datetime(2026, 9, 7, 23, 0, tzinfo=MSK))     # ещё ждём
    assert len(storage.active_all(conn)) == 1
    _run(conn, send, datetime(2026, 9, 8, 0, 1, tzinfo=MSK))      # сутки прошли
    assert storage.active_all(conn) == []
    assert len(send.msgs) == 1 and send.msgs[0][0] == 42          # сняли — сказали
    assert "SU2128" in send.msgs[0][1] and "не появился" in send.msgs[0][1]


def test_expiry_of_seen_flight_says_tracking_stopped(monkeypatch):
    conn = storage.connect(":memory:")
    sid = storage.add(conn, -1001, "SU2128", "2026-09-06", thread_id=7)
    storage.set_last(conn, sid, _snap(departure=Leg(plan=DEP, fact=DEP)))   # вылетел, прилёта так и не было
    send = _Sends()
    _fixed(None, monkeypatch)
    _run(conn, send, datetime(2026, 9, 8, 0, 1, tzinfo=MSK))
    assert storage.active_all(conn) == []
    assert len(send.msgs) == 1 and "не появился" not in send.msgs[0][1]
    assert "перестал следить" in send.msgs[0][1].lower() and send.threads == [7]


def test_far_future_unseen_flight_sleeps_until_day_before(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "EK770", "2026-09-10")

    async def none(flight_no, date, direction, prefer=None, key=None):
        return None
    monkeypatch.setattr(registry, "fetch_for", none)
    _run(conn, _Sends())
    wake = datetime(2026, 9, 9, 0, 0, tzinfo=MSK)           # 00:00 МСК накануне
    assert storage.due(conn, int(wake.timestamp()) - 1) == []
    assert len(storage.due(conn, int(wake.timestamp()))) == 1


def test_interval_with_naive_departure_does_not_guess_zone():
    # Пояс вылета неизвестен (не обязательно МСК) — не угадываем, опрашиваем часто.
    naive = _snap(departure=Leg(plan="2026-09-07T12:00:00"))
    assert poller.interval_for(naive, NOW, near=300) == 300


def test_one_failing_subscription_does_not_break_the_round(monkeypatch):
    conn = storage.connect(":memory:")
    bad = storage.add(conn, 1, "SU2128", "2026-09-06")
    good = storage.add(conn, 2, "SU2130", "2026-09-06")
    send = _Sends()
    _fixed(_snap(gate="129"), monkeypatch)
    _run(conn, send)                                  # первый круг: запомнили оба

    real_diff = poller.diff_snapshots

    def flaky(prev, curr, *a, **kw):
        if flaky.calls == 0:
            flaky.calls += 1
            raise TypeError("boom")
        return real_diff(prev, curr, *a, **kw)
    flaky.calls = 0
    monkeypatch.setattr(poller, "diff_snapshots", flaky)
    _fixed(_snap(gate="141"), monkeypatch)
    later = NOW + timedelta(minutes=10)
    _run(conn, send, later)

    assert [c for c, _ in send.msgs] == [2]           # вторая подписка получила пуш
    by_id = {r["id"]: r for r in storage.active_all(conn)}
    epoch = int(later.timestamp())
    assert by_id[bad]["next_at"] > epoch and by_id[good]["next_at"] > epoch
    assert storage.get_last(conn, bad).gate == "129"  # снимок упавшей не перезаписан
    assert storage.get_last(conn, good).gate == "141"


def test_gate_flicker_on_board_does_not_repush(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()
    for i, gate in enumerate(["E14", "", "E14"]):
        _fixed(_snap(gate=gate, source="dme.ru"), monkeypatch)
        _run(conn, send, NOW + timedelta(minutes=10 * i))
    assert send.msgs == []                         # выход не «пропадал» и не «назначался» заново


def test_other_sources_may_clear_gate(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    for i, gate in enumerate(["129", ""]):
        _fixed(_snap(gate=gate, source="svo.aero"), monkeypatch)
        _run(conn, _Sends(), NOW + timedelta(minutes=10 * i))
    assert storage.get_last(conn, storage.active_all(conn)[0]["id"]).gate == ""
