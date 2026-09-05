"""Выбор дат для подписки: прошедшие отрезаем, по одной кнопке на дату."""
from datetime import date

from flight_bot.models import FlightSnapshot
from flight_bot.registry import upcoming


def _snap(d, dest="BAH"):
    return FlightSnapshot(flight="GF013", date=d, direction="departure",
                          origin_iata="SVO", dest_iata=dest, status="")


def test_upcoming_drops_past_dates_keeps_today_and_dedups():
    snaps = [_snap("2026-09-04"), _snap("2026-09-05", "TBS"),
             _snap("2026-09-05", "TBS"), _snap("2026-09-06", "TBS")]
    got = upcoming(snaps, today=date(2026, 9, 5))
    assert [s.date for s in got] == ["2026-09-05", "2026-09-06"]


def test_upcoming_empty_when_all_past():
    assert upcoming([_snap("2026-09-04")], today=date(2026, 9, 5)) == []
