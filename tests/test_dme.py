"""Парсер табло DME на боевых фикстурах: вылет S7 1055 и отменённый прилёт U6 2770."""
from datetime import date
from pathlib import Path

from flight_bot.models import is_cancelled
from flight_bot.sources import dme

FX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 5)


def _fx(name):
    return (FX / name).read_text(encoding="utf-8")


def test_board_departure_row():
    rows = dme.parse_board(_fx("dme_row_departure.html"), "S71055", TODAY)
    assert len(rows) == 1
    r = rows[0]
    assert r["flight"] == "S71055" and r["airline"] == "S7" and r["id"] == "11312869"
    assert r["plan"] == "2026-09-05T17:10:00+03:00"
    assert r["fact"] == "2026-09-05T18:08:00+03:00"
    assert r["city"] == "Казань" and r["status"] == "Рейс вылетел" and r["zone"] == "C"
    assert r["cancelled"] is False


def test_board_filters_by_number_with_or_without_space():
    assert dme.parse_board(_fx("dme_row_departure.html"), "s7 1055", TODAY)
    assert dme.parse_board(_fx("dme_row_departure.html"), "S71056", TODAY) == []


def test_detailed_departure():
    d = dme.parse_detailed(_fx("dme_detailed_departure.html"))
    assert (d["origin"], d["dest"]) == ("Москва(Домодедово)", "КАЗАНЬ")
    assert d["aircraft"] == "Airbus 320-200"
    assert (d["dep"], d["dep_fact"], d["arr"], d["arr_fact"]) == ("17:10", "18:08", "18:45", "")


def test_build_departure_snapshot():
    row = dme.parse_board(_fx("dme_row_departure.html"), "S71055", TODAY)[0]
    det = dme.parse_detailed(_fx("dme_detailed_departure.html"))
    s = dme.build(row, det, "departure")
    assert (s.flight, s.date, s.direction) == ("S71055", "2026-09-05", "departure")
    assert (s.origin_iata, s.origin_city, s.dest_iata, s.dest_city) == ("DME", "Москва", "", "Казань")
    assert s.departure.plan == "2026-09-05T17:10:00+03:00"
    assert s.departure.fact == "2026-09-05T18:08:00+03:00"      # «Рейс вылетел» → факт
    assert s.departure.est is None
    assert s.arrival.plan == "2026-09-05T18:45:00+03:00"        # из Detailed, на дату строки
    assert s.arrival.fact is None
    assert (s.zone, s.terminal, s.aircraft, s.source, s.key) == ("C", "", "Airbus 320-200", "dme.ru", "11312869")
    assert not is_cancelled(s)


def test_build_cancelled_arrival_snapshot():
    row = dme.parse_board(_fx("dme_row_arrival_cancelled.html"), "U62770", TODAY)[0]
    det = dme.parse_detailed(_fx("dme_detailed_arrival_cancelled.html"))
    s = dme.build(row, det, "arrival")
    assert (s.origin_iata, s.origin_city, s.dest_iata, s.dest_city) == ("", "Куляб", "DME", "Москва")
    assert s.arrival.plan == "2026-09-05T16:15:00+03:00"
    assert s.departure.plan == "2026-09-05T11:35:00+03:00"      # из Detailed
    assert s.arrival.fact is None and s.departure.fact is None
    assert is_cancelled(s) and s.zone == "E" and s.terminal == ""


def test_arrival_next_day_rolls_over():
    # Вылет 23:30, прибытие 01:10 — это уже следующий день.
    assert dme._at("2026-09-05T23:30:00+03:00", "01:10", not_before="2026-09-05T23:30:00+03:00") \
        == "2026-09-06T01:10:00+03:00"


def test_when_december_to_january():
    assert dme._when("1 янв 09:00", date(2026, 12, 31)) == "2027-01-01T09:00:00+03:00"


def _ek132():
    rows = dme.parse_board(_fx("dme_rows_ek132.html"), "EK132", date(2026, 10, 14))
    return [dme.build(r, {}, "departure") for r in rows]


def test_zone_is_not_a_terminal():
    """У DME один терминал; «Зона вылета E» — сектор выходов, а не терминал."""
    s = _ek132()[1]
    assert (s.terminal, s.zone) == ("", "E")


def test_status_ticker_gives_desks_checkin_gate_and_boarding():
    closed, today, tomorrow = _ek132()
    assert closed.gate == "E14" and closed.status == "Регистрация закрыта"
    assert today.checkin.desks == "215-223"
    assert today.checkin.start_plan == "2026-10-14T20:10:00+03:00"
    assert today.boarding.start_min == "66"                  # 22:04 → вылет 23:10
    assert today.gate == ""
    assert tomorrow.checkin.desks == "" and tomorrow.checkin.start_plan == "2026-10-15T20:10:00+03:00"


def test_checkin_before_midnight_for_night_flight_is_previous_day():
    page = _fx("dme_rows_ek132.html").replace("15 окт 23:10", "16 окт 01:10")
    s = dme.build(dme.parse_board(page, "EK132", date(2026, 10, 14))[2], {}, "departure")
    assert s.checkin.start_plan == "2026-10-15T20:10:00+03:00"


def test_boarding_minutes_from_estimate_and_gate_formats():
    t = dme._ticker("Начало посадки в 22:04 · Выход EA14", "2026-10-14T23:10:00+03:00",
                    "2026-10-14T23:40:00+03:00")
    assert t["boarding_min"] == "96" and t["gate"] == "EA14"   # задержка на 30 мин учтена
    assert dme._ticker("Выход 12", "2026-10-14T23:10:00+03:00")["gate"] == "12"
    assert dme._ticker("Выход C12a", "2026-10-14T23:10:00+03:00")["gate"] == "C12a"
