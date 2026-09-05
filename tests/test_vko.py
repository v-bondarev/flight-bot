"""Парсер отрендеренного табло Внуково на боевой фикстуре (вылеты, 5 сентября)."""
from datetime import date
from pathlib import Path

from flight_bot.sources import vko

FX = Path(__file__).parent / "fixtures" / "vko_board_departure.html"
TODAY = date(2026, 9, 5)


def _page():
    return FX.read_text(encoding="utf-8")


def test_canon_handles_cyrillic_codes():
    assert vko.canon("ЮТ 571") == "UT571"
    assert vko.canon("В2 2271") == "B22271"       # кириллическая В
    assert vko.canon("СУ 6181") == "SU6181"       # транслитерация, не CY
    assert vko.canon("ФВ 6181") == "FV6181" and vko.canon("ДР 158") == "DP158"
    assert vko.canon("DP 741") == "DP741"
    assert vko.canon("su 025") == "SU25"


def test_board_rows_have_all_columns():
    rows = vko.parse_board(_page(), TODAY)
    assert rows and vko.direction_of(_page()) == "departure"
    r = next(x for x in rows if "DP741" in x["numbers"])
    assert (r["id"], r["time"], r["date"]) == ("101133366", "21:20", "2026-09-05")
    assert (r["city"], r["iata"]) == ("Ташкент", "TAS")
    assert (r["airline"], r["terminal"], r["gate"]) == ("Победа", "А", "26A")
    assert r["status"] == "Посадка закончена"


def test_codeshare_row_matches_both_numbers():
    assert vko.parse(_page(), "UT571", TODAY)
    assert vko.parse(_page(), "B2 2271", TODAY)
    assert vko.parse(_page(), "UT572", TODAY) == []


def test_departure_snapshot_and_status_phases():
    s = vko.parse(_page(), "DP741", TODAY)[0]
    assert (s.origin_iata, s.dest_iata, s.dest_city, s.direction) == ("VKO", "TAS", "Ташкент", "departure")
    assert s.departure.plan == "2026-09-05T21:20:00+03:00"
    assert s.status == "Посадка закончена"
    assert s.boarding.start_fact is None and s.boarding.finish_fact is None   # времён фаз табло не даёт — не выдумываем
    assert (s.terminal, s.gate, s.source, s.key) == ("А", "26A", "vnukovo.ru", "101133366")

    by_status = {x.status: x for x in (vko.build(r, "departure", "X") for r in vko.parse_board(_page(), TODAY))}
    # табло держит сегодня и завтра одной страницей — дата берётся из строки
    assert by_status["Регистрация с 22:00"].checkin.start_plan == "2026-09-06T22:00:00+03:00"
    assert by_status["Идёт регистрация"].checkin.desks == "129"     # «…Стойка 129» вынута из статуса
    assert by_status["Посадка с 20:55"].boarding.start_min == "40"  # вылет 21:35 − посадка 20:55


def test_status_times_become_fact_or_estimate():
    row = dict(id="1", time="21:20", date="2026-09-05", city="Сочи", iata="AER", numbers=["DP1"],
               raw_number="DP 1", airline="Победа", terminal="A", gate="", status="Вылетел в 21:32")
    assert vko.build(row, "departure", "DP1").departure.fact == "2026-09-05T21:32:00+03:00"
    row["status"] = "Задержан до 23:10"
    assert vko.build(row, "departure", "DP1").departure.est == "2026-09-05T23:10:00+03:00"
    row["status"] = "Прибыл в 00:15"
    a = vko.build(row, "arrival", "DP1")
    assert a.arrival.fact == "2026-09-05T00:15:00+03:00"          # время из статуса на дату строки
    assert (a.origin_iata, a.dest_iata) == ("AER", "VKO")
