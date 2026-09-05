"""Поллер: первый круг молчит, изменение шлёт пуш, прилёт снимает подписку."""
import asyncio

from flight_bot import poller, registry, storage
from flight_bot.models import FlightSnapshot, Leg


def _snap(**kw):
    base = dict(flight="SU2128", date="2026-09-06", direction="departure",
                origin_iata="SVO", dest_iata="AYT", status="")
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


def test_first_poll_is_silent_then_change_notifies(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()

    _fixed(_snap(gate="129"), monkeypatch)
    asyncio.run(poller.poll_once(conn, send))
    assert send.msgs == []                      # первый снимок — молча запомнили

    _fixed(_snap(gate="141"), monkeypatch)
    asyncio.run(poller.poll_once(conn, send))
    assert len(send.msgs) == 1
    chat_id, text = send.msgs[0]
    assert chat_id == 42
    assert "129 → 141" in text


def test_arrival_deactivates_subscription(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()

    _fixed(_snap(arrival=Leg(plan="2026-09-06T05:35:00+03:00")), monkeypatch)
    asyncio.run(poller.poll_once(conn, send))       # первый снимок

    _fixed(_snap(arrival=Leg(plan="2026-09-06T05:35:00+03:00",
                             fact="2026-09-06T05:34:00+03:00")), monkeypatch)
    asyncio.run(poller.poll_once(conn, send))       # прилетел
    assert any("Прилёт состоялся" in t for _, t in send.msgs)
    assert storage.active_all(conn) == []           # подписка снята


def test_missing_flight_is_skipped(monkeypatch):
    conn = storage.connect(":memory:")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    send = _Sends()

    async def none(flight_no, date, direction):
        return None
    monkeypatch.setattr(registry, "fetch_for", none)
    asyncio.run(poller.poll_once(conn, send))
    assert send.msgs == []
    assert len(storage.active_all(conn)) == 1        # подписка жива
