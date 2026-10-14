"""Парсер AeroDataBox на реальном ответе /flights/number/EK770/{с}/{по}."""
import json
from pathlib import Path

from flight_bot.sources import aerodatabox

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "adb_ek770.json").read_text())


def test_parse_one_snapshot_per_date_with_route_and_plan():
    snaps = aerodatabox.parse(PAYLOAD, "ek 770")
    assert [s.date for s in snaps] == ["2026-10-14", "2026-10-15", "2026-10-16"]
    s = snaps[2]
    assert (s.flight, s.origin_iata, s.dest_iata) == ("EK770", "DXB", "CPT")
    assert s.departure.plan == "2026-10-16T09:10:00+04:00"   # локальное время с офсетом
    assert s.arrival.plan == "2026-10-16T16:40:00+02:00"
    assert (s.airline, s.aircraft, s.terminal, s.gate) == ("Emirates", "Boeing 777-300ER", "3", "")
    assert s.status == "Ожидается"
    assert (s.source, s.key, s.direction) == ("aerodatabox.com", "EK770:2026-10-16", "departure")


def test_parse_live_flight_takes_off_time_as_fact_and_predicted_arrival_as_estimate():
    s = aerodatabox.parse(PAYLOAD, "EK770")[0]
    assert s.status == "В воздухе"
    assert s.departure.fact == "2026-10-14T09:41:00+04:00"
    assert s.departure.est is None                 # revised == scheduled — не оценка
    assert s.arrival.est == "2026-10-14T17:09:00+02:00"
    assert s.arrival.fact is None
    assert (s.gate, s.baggage_belt) == ("A6b", "2")


def test_parse_skips_other_numbers_and_empty_payload():
    assert aerodatabox.parse(PAYLOAD, "EK771") == []
    assert aerodatabox.parse([], "EK770") == []
