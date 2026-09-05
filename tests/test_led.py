"""Парсер API Пулково на боевых фикстурах: вылет FV 6599 (кодшер SU 6599), прилёт SU 735."""
import json
from pathlib import Path

from flight_bot.sources import led

FX = Path(__file__).parent / "fixtures"


def _fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def test_canon_normalises_board_and_user_forms():
    assert led.canon("SU  025") == "SU25"
    assert led.canon("su 25") == "SU25"
    assert led.canon("FV-6599") == "FV6599"
    assert led.canon("S72050") == "S72050"
    assert led.canon("привет") is None


def test_departure_snapshot_is_the_richest():
    s = led.parse(_fx("led_departure_fv6599.json"), "FV6599", "departure")[0]
    assert (s.flight, s.date, s.direction) == ("FV6599", "2026-09-05", "departure")
    assert (s.origin_iata, s.dest_iata, s.dest_city) == ("LED", "KZN", "Казань")
    assert s.departure.plan == "2026-09-05T19:10:00+03:00"
    assert s.departure.fact == "2026-09-05T19:17:00+03:00"
    assert s.arrival.plan == "2026-09-05T21:30:00+03:00"
    assert s.checkin.desks == "38-44"
    assert s.checkin.start_fact == "2026-09-05T17:10:00+03:00"
    assert s.checkin.finish_fact == "2026-09-05T18:30:00+03:00"
    assert s.boarding.start_fact == "2026-09-05T18:34:48+03:00"
    assert s.boarding.finish_fact == "2026-09-05T18:48:00+03:00"
    assert s.boarding.start_min == "40"                      # план посадки за 40 мин до STD
    assert (s.gate, s.terminal, s.aircraft) == ("7", "1", "SU95")
    assert s.status == "Отправлен" and s.airline == "Россия"
    assert s.via == ""                                       # NEXT == DESTINATION
    assert s.source == "pulkovoairport.ru" and s.key == "3195458"


def test_codeshare_number_matches_too():
    assert led.parse(_fx("led_departure_fv6599.json"), "SU 6599", "departure")
    assert led.parse(_fx("led_departure_fv6599.json"), "SU6598", "departure") == []


def test_arrival_snapshot():
    s = led.parse(_fx("led_arrival_su735.json"), "SU735", "arrival")[0]
    assert (s.origin_iata, s.origin_city, s.dest_iata) == ("SSH", "Шарм-Эль-Шейх", "LED")
    assert s.arrival.plan == "2026-09-05T19:30:00+03:00"
    assert s.arrival.est == "2026-09-05T18:27:00+03:00"
    assert s.arrival.fact == "2026-09-05T18:24:00+03:00"
    assert s.departure.plan == "2026-09-05T12:30:00+03:00"   # вылет из пункта отправления
    assert s.departure.fact == "2026-09-05T12:31:00+03:00"
    assert (s.baggage_belt, s.terminal, s.status) == ("6", "1", "Прибыл")


def test_via_when_next_differs_from_destination():
    it = dict(_fx("led_departure_fv6599.json")[0],
              OD_RAP_CODE_NEXT="KZN", OD_RAP_NEXT_NAME_RU="Казань",
              OD_RAP_CODE_DESTINATION="UUS", OD_RAP_DESTINATION_NAME_RU="Южно-Сахалинск")
    s = led.parse([it], "FV6599", "departure")[0]
    assert s.dest_iata == "UUS" and s.via == "KZN Казань"
