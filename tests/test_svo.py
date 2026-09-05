"""Парсер табло SVO на боевой фикстуре (рейс SU2128, вылет)."""
import json
from pathlib import Path

from flight_bot.sources import svo

FIXTURE = Path(__file__).parent / "fixtures" / "svo_su2128.json"


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_finds_flight_and_route():
    snaps = svo.parse(_payload(), "SU2128", "departure")
    assert snaps, "рейс должен найтись в фикстуре"
    for s in snaps:
        assert s.flight == "SU2128"
        assert s.direction == "departure"
        assert s.origin_iata == "SVO"      # вылет из Шереметьево
        assert s.dest_iata                 # встречный аэропорт заполнен
        assert s.source == "svo.aero"
        assert s.key                       # стабильный id для сопоставления опросов


def test_parse_maps_times_and_terminal():
    s = svo.parse(_payload(), "SU2128", "departure")[0]
    assert s.departure.plan, "плановое время вылета из SVO должно быть"
    assert s.airline == "Аэрофлот"
    assert s.terminal                       # у SU2128 в фикстуре терминал 'C'


def test_parse_maps_checkin_boarding_and_belt_fields():
    snaps = svo.parse(_payload(), "SU2128", "departure")
    # У будущих дат табло уже держит плановое окно регистрации.
    assert any(s.checkin.start_plan for s in snaps)
    for s in snaps:
        assert isinstance(s.checkin.desks, str)
        assert isinstance(s.boarding.start_min, str)
        assert isinstance(s.baggage_belt, str)


def _multi_stop_item():
    # GF013: SVO → TBS (посадка) → BAH (конечная). Табло кладёт цепочку в mar2…mar5.
    return {"co": {"code": "GF", "name": "Gulf Air"}, "flt": "013",
            "dat": "2026-09-06T00:00:00+03:00", "i_id": "7",
            "mar1": {"iata": "SVO", "city": "Москва"},
            "mar2": {"iata": "TBS", "city": "Тбилиси"},
            "mar3": {"iata": "BAH", "city": "Бахрейн"},
            "mar4": {}, "mar5": None}


def test_parse_multi_stop_departure_uses_final_destination():
    s = svo.parse({"items": [_multi_stop_item()]}, "GF013", "departure")[0]
    assert (s.origin_iata, s.dest_iata) == ("SVO", "BAH")
    assert s.dest_city == "Бахрейн"
    assert s.via == "TBS Тбилиси"


def test_parse_arrival_chain_is_in_flight_order():
    # Табло прилёта: mar1 — откуда летит, SVO — последний (SU1325 MMK→SVO).
    item = {"co": {"code": "SU"}, "flt": "1325", "dat": "2026-09-05T00:00:00+03:00",
            "mar1": {"iata": "MMK", "city": "Мурманск"},
            "mar2": {"iata": "SVO", "city": "Москва"},
            "t_st": "2026-09-05T21:15:00+03:00",          # своя сторона = прилёт в SVO
            "t_st_mar": "2026-09-05T19:00:00+03:00"}      # встречная = вылет из MMK
    s = svo.parse({"items": [item]}, "SU1325", "arrival")[0]
    assert (s.origin_iata, s.dest_iata) == ("MMK", "SVO")
    assert s.via == ""
    assert s.departure.plan == "2026-09-05T19:00:00+03:00"
    assert s.arrival.plan == "2026-09-05T21:15:00+03:00"


def test_parse_multi_stop_arrival_via_in_the_middle():
    item = dict(_multi_stop_item(), mar1={"iata": "BAH", "city": "Бахрейн"},
                mar3={"iata": "SVO", "city": "Москва"})
    s = svo.parse({"items": [item]}, "GF013", "arrival")[0]
    assert (s.origin_iata, s.dest_iata) == ("BAH", "SVO")
    assert s.via == "TBS Тбилиси"


def test_parse_filters_by_exact_number():
    # 'search' на табло нестрогий — чужой номер не должен просочиться.
    assert svo.parse(_payload(), "SU9999", "departure") == []


def test_parse_normalises_spacing_and_case():
    assert svo.parse(_payload(), "su 2128", "departure")
