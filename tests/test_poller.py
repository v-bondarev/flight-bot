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

    async def __call__(self, chat_id, text):
        self.msgs.append((chat_id, text))


def _fixed(snap, monkeypatch):
    async def fake(flight_no, date, direction):
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
    assert "129 → 141" in send.msgs[0][1]


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

    async def none(flight_no, date, direction):
        return None
    monkeypatch.setattr(registry, "fetch_for", none)
    _run(conn, send, datetime(2026, 9, 7, 23, 0, tzinfo=MSK))     # ещё ждём
    assert len(storage.active_all(conn)) == 1
    _run(conn, send, datetime(2026, 9, 8, 0, 1, tzinfo=MSK))      # сутки прошли
    assert storage.active_all(conn) == []
    assert send.msgs == []
