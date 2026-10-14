"""Табло Дубая (DXB) на реальном flights-data.json: «Z» у времён рейсов — на деле местное +04:00."""
import json
from pathlib import Path

from flight_bot.models import is_cancelled
from flight_bot.sources import dxb

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "dxb_flights.json").read_text())


def test_departure_ek770_times_are_local_plus4_not_utc():
    snaps = dxb.parse(PAYLOAD, "EK770", "departure")
    assert [s.date for s in snaps] == ["2026-10-13", "2026-10-14", "2026-10-15"]
    s = snaps[1]
    assert (s.origin_iata, s.dest_iata, s.terminal, s.gate) == ("DXB", "CPT", "3", "A6b")
    assert s.departure.plan == "2026-10-14T09:10:00+04:00"
    assert s.departure.est == "2026-10-14T09:30:00+04:00"
    assert s.departure.fact == "2026-10-14T09:21:13+04:00"     # уход со стоянки
    assert s.status == "Вылетел" and s.source == "dubaiairports.ae"
    assert snaps[2].departure.fact is None and snaps[2].status == "Ожидается"


def test_arrival_ek771_with_belt_and_landing():
    snaps = dxb.parse(PAYLOAD, "ek 771", "arrival")
    landed = next(s for s in snaps if s.date == "2026-10-14")
    assert (landed.origin_iata, landed.dest_iata, landed.baggage_belt) == ("CPT", "DXB", "6")
    assert landed.arrival.plan == "2026-10-14T05:50:00+04:00"
    assert landed.arrival.fact == "2026-10-14T05:33:20+04:00"
    assert landed.status == "Прилетел" and landed.direction == "arrival"
    assert dxb.parse(PAYLOAD, "EK771", "departure") == []        # из DXB он не вылетает


def test_cancelled_and_codeshare_lookup():
    rx = next(f for f in PAYLOAD["Flights"] if f["flightNumber"] == "RX 244")
    assert is_cancelled(dxb.parse(PAYLOAD, "RX244", "departure")[0])
    assert [s.flight for s in dxb.parse(PAYLOAD, "SA7157", "departure")][:1] == ["SA7157"]  # кодшер EK770
