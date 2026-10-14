"""Табло Бахрейна (BAH) на реальных ответах /api/FlightProxy: времена местные +03:00."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flight_bot.sources import bah

FX = json.loads((Path(__file__).parent / "fixtures" / "bah_flights.json").read_text())
NOW = datetime(2026, 10, 14, 10, 45, tzinfo=timezone(timedelta(hours=3)))


def test_arrival_gf013_gate_and_belt():
    s = bah.parse(FX["arrivals 14/10"]["data"], "GF13", "arrival", NOW)[0]
    assert (s.flight, s.date, s.direction) == ("GF13", "2026-10-14", "arrival")
    assert (s.origin_iata, s.dest_iata, s.gate, s.baggage_belt) == ("SVO", "BAH", "18A", "A04")
    assert s.arrival.plan == "2026-10-14T19:20:00+03:00" and s.arrival.fact is None
    assert s.status == "По расписанию" and s.source == "bahrainairport.bh"


def test_departure_fact_and_counter_zone():
    s = bah.parse(FX["departures 14/10"]["data"], "FZ24", "departure", NOW)[0]
    assert s.departure.fact == "2026-10-14T08:32:00+03:00" and s.status == "Вылетел"
    gf = bah.parse(FX["departures 15/10"]["data"], "GF012", "departure", NOW)[0]
    assert (gf.zone, gf.gate, gf.dest_iata) == ("E", "14B", "TBS")


def test_fact_from_the_future_is_not_trusted():
    """API отдаёт на завтра «Departed/Landed» с фактом позже текущего момента — это не факт."""
    dep = bah.parse(FX["departures 15/10"]["data"], "GF101", "departure", NOW)[0]
    assert dep.departure.fact is None and dep.status == "Ожидается"
    arr = bah.parse(FX["arrivals 15/10"]["data"], "GF283", "arrival", NOW)[0]
    assert arr.arrival.fact is None and arr.status == "Ожидается"


def test_status_by_meaning_cancel_and_arrived():
    from flight_bot.models import is_cancelled
    row = dict(FX["arrivals 14/10"]["data"][0])
    for word in ("Cancelled", "Canceled", "Flight Cancelled"):
        assert is_cancelled(bah.parse([dict(row, remarks=word)], "GF13", "arrival", NOW)[0])
    landed = dict(FX["arrivals 14/10"]["data"][1], remarks="Arrived")
    s = bah.parse([landed], "GF6", "arrival", NOW)[0]
    assert s.arrival.fact == "2026-10-14T06:31:00+03:00" and s.status == "Приземлился"


def test_english_statuses_translated_by_meaning():
    row = dict(FX["departures 15/10"]["data"][0])
    cases = {"Boarding": "Посадка", "Gate Open": "Посадка", "Final Call": "Посадка заканчивается",
             "Gate Closed": "Посадка закончена", "Check-in Open": "Регистрация",
             "Delayed": "Задерживается", "Diverted": "Ушёл на запасной", "Something New": "Something New"}
    for raw, text in cases.items():
        assert bah.parse([dict(row, remarks=raw)], "GF12", "departure", NOW)[0].status == text, raw


def test_status_order_pitfalls():
    row = dict(FX["departures 15/10"]["data"][0])

    def st(raw):
        return bah.parse([dict(row, remarks=raw)], "GF12", "departure", NOW)[0].status
    assert st("Check-in Closed") == "Регистрация закрыта"
    assert st("Delayed Departure") == "Задерживается"
    assert st("Estimated Arrival") == "Ожидается"
    assert st("Arrived Early") == "Приземлился"
