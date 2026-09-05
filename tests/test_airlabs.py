"""Парсер AirLabs: маршрут, восстановление офсета, статус, дата."""
from flight_bot.models import is_cancelled
from flight_bot.sources import airlabs


def _payload(**over):
    r = {
        "flight_iata": "SU2128", "airline_iata": "SU", "airline_name": "Aeroflot",
        "aircraft_icao": "A321", "status": "scheduled",
        "dep_iata": "SVO", "dep_time": "2026-09-06 00:25", "dep_time_utc": "2026-09-05 21:25",
        "dep_estimated": "2026-09-06 01:10", "dep_terminal": "C", "dep_gate": "129",
        "arr_iata": "AYT", "arr_time": "2026-09-06 05:35", "arr_time_utc": "2026-09-06 02:35",
        "arr_baggage": "7",
    }
    r.update(over)
    return {"response": r}


def test_parse_route_and_times_with_restored_offsets():
    s = airlabs.parse(_payload(), "su 2128")
    assert (s.flight, s.origin_iata, s.dest_iata) == ("SU2128", "SVO", "AYT")
    assert s.date == "2026-09-06"
    assert s.departure.plan == "2026-09-06T00:25:00+03:00"   # офсет из local−utc
    assert s.departure.est == "2026-09-06T01:10:00+03:00"
    assert s.departure.fact is None
    assert s.arrival.plan == "2026-09-06T05:35:00+03:00"
    assert (s.terminal, s.gate, s.baggage_belt) == ("C", "129", "7")
    assert s.status == "Ожидается"
    assert s.source == "airlabs.co"


def test_parse_status_mapping_and_cancellation():
    assert airlabs.parse(_payload(status="en-route"), "SU2128").status == "В воздухе"
    assert airlabs.parse(_payload(status="landed"), "SU2128").status == "Приземлился"
    assert is_cancelled(airlabs.parse(_payload(status="cancelled"), "SU2128"))


def test_parse_actual_times_become_facts():
    s = airlabs.parse(_payload(dep_actual="2026-09-06 00:31", status="en-route"), "SU2128")
    assert s.departure.fact == "2026-09-06T00:31:00+03:00"


def test_parse_empty_response_is_none():
    assert airlabs.parse({"response": None}, "SU2128") is None
    assert airlabs.parse({"response": []}, "SU2128") is None
