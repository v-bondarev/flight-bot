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


def test_parse_filters_by_exact_number():
    # 'search' на табло нестрогий — чужой номер не должен просочиться.
    assert svo.parse(_payload(), "SU9999", "departure") == []


def test_parse_normalises_spacing_and_case():
    assert svo.parse(_payload(), "su 2128", "departure")
