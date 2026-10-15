"""Формат карточки и пуша — спецификация.

Данные — реальный снимок SU5822 на 16.08.2026.
"""
from datetime import datetime, timedelta, timezone

from flight_bot.diff import diff_snapshots
from flight_bot.models import Checkin, FlightSnapshot, Leg
from flight_bot.render import countdown, status_card, update_message

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 8, 16, 21, 15, tzinfo=MSK)   # за 2 ч 10 мин до вылета


def _su5822(**kw) -> FlightSnapshot:
    base = dict(
        flight="SU5822", date="2026-08-16", direction="departure",
        origin_iata="SVO", dest_iata="UUS", origin_city="Москва",
        dest_city="Южно-Сахалинск", airline="Аэрофлот", aircraft="Airbus A330",
        status="Регистрация идет",
        departure=Leg(plan="2026-08-16T23:25:00+03:00"),
        arrival=Leg(plan="2026-08-17T08:00:00+03:00"),
        checkin=Checkin(desks="312,314-316,319-346,355-364",
                        start_plan="2026-08-16T17:25:00+03:00",
                        finish_plan="2026-08-16T22:45:00+03:00",
                        start_fact="2026-08-16T17:26:04+03:00"),
        terminal="B", gate="101", gate_prev="114", gate_terminal="B",
        baggage_belt="B2",
    )
    base.update(kw)
    return FlightSnapshot(**base)


def test_departure_card_matches_page():
    assert status_card(_su5822(), NOW) == (
        "<b>SU5822</b> · Аэрофлот · Airbus A330\n"
        "▸ 16 августа\n"
        "⇗ SVO Москва → UUS Южно-Сахалинск\n"
        "◷ Вылет 23:25 · Прилёт 08:00\n"
        "\n"
        "● Регистрация идет\n"
        "⧗ До вылета 2 ч 10 мин\n"
        "\n"
        "▸ Стойки 312, 314-316, 319-346, 355-364 · идёт до 22:45\n"
        "▸ Выход 101 · изменён, был <s>114</s>\n"
        "▸ Терминал B · лента багажа B2\n"
        "\n"
        "<b>Хронология</b>\n"
        "✓ Регистрация · 17:26\n"
        "○ Регистрация закрыта · 22:45\n"
        "○ Посадка · ждём табло\n"
        "○ Вылет · 23:25\n"
        "○ Прилёт · 08:00"
    )


def test_delay_strikes_plan_and_turns_yellow():
    s = _su5822(departure=Leg(plan="2026-08-16T23:25:00+03:00",
                              est="2026-08-17T00:10:00+03:00"))
    card = status_card(s, NOW)
    assert "Вылет <s>23:25</s> 00:10 · Прилёт 08:00" in card
    assert "◷ Регистрация идет" in card
    assert "До вылета 2 ч 55 мин" in card          # отсчёт по оценке, не по плану


def test_arrival_card_has_belt_and_two_steps():
    s = _su5822(direction="arrival", origin_iata="UUS", dest_iata="SVO",
                origin_city="Южно-Сахалинск", dest_city="Москва",
                status="Ожидается", gate="", gate_prev="", baggage_belt="",
                departure=Leg(plan="2026-08-16T23:25:00+03:00",
                              fact="2026-08-16T23:30:00+03:00"),
                arrival=Leg(plan="2026-08-17T08:00:00+03:00"))
    card = status_card(s, datetime(2026, 8, 17, 6, 0, tzinfo=MSK))
    assert "⇗ Ожидается\n⧗ В пути, до прилёта 2 ч\n" in card
    assert "▸ Лента багажа — · появится ближе к прилёту\n" in card
    assert "▸ Гейт прилёта — · назначат при заходе\n" in card
    assert card.endswith("✓ Вылет · Южно-Сахалинск · 23:30\n○ Прилёт · 08:00")


def test_via_line_for_multi_stop_flight():
    card = status_card(_su5822(dest_iata="BAH", dest_city="Бахрейн", via="TBS Тбилиси"), NOW)
    assert "⇗ SVO Москва → BAH Бахрейн\n⤷ через TBS Тбилиси\n◷ Вылет 23:25" in card
    assert "через" not in status_card(_su5822(), NOW)


def test_update_message_format():
    prev = _su5822()
    curr = _su5822(gate="103", gate_prev="101",
                   departure=Leg(plan="2026-08-16T23:25:00+03:00",
                                 est="2026-08-16T23:55:00+03:00"))
    assert update_message(curr, diff_snapshots(prev, curr)) == (
        "<b>SU5822 · Москва → Южно-Сахалинск · 16 августа</b>\n"
        "◷ Вылет позже на 30 мин: теперь 23:55, было 23:25\n"
        "⇄ Новый выход на посадку в SVO: 103 (был 101)"
    )


def test_update_message_header_falls_back_to_codes():
    s = _su5822(origin_city="", dest_city="")
    assert update_message(s, ["x"]).startswith("<b>SU5822 · SVO → UUS · 16 августа</b>\n")


def test_countdown_without_offset_is_not_guessed():
    # Пояс без офсета неизвестен (вылет не из Москвы — местное время):
    # отсчёт не угадываем и не падаем; карточка строится.
    s = _su5822(departure=Leg(plan="2026-08-16T23:25:00"))
    assert countdown(s, NOW) == ""
    assert "SU5822" in status_card(s, NOW)


def test_zone_shown_instead_of_terminal_when_no_terminal():
    s = _su5822(terminal="", zone="E")
    card = status_card(s, NOW)
    assert "▸ Зона вылета E" in card and "Терминал" not in card


def test_checkin_tile_after_planned_start_without_fact():
    from flight_bot.models import Checkin
    s = _su5822(checkin=Checkin(desks="215-223", start_plan="2026-08-16T20:10:00+03:00"))
    assert "по плану с 20:10" in status_card(s, NOW)              # 21:15 — уже после 20:10
    early = NOW.replace(hour=19)
    assert "откроется в 20:10" in status_card(s, early)


def test_arrival_board_without_departure_data_does_not_claim_in_flight():
    s = FlightSnapshot(flight="GF13", date="2026-08-16", direction="arrival", origin_iata="SVO",
                       dest_iata="BAH", status="По расписанию",
                       arrival=Leg(plan="2026-08-16T23:20:00+03:00"))
    assert countdown(s, NOW) == "До прилёта 2 ч 5 мин"


def test_non_moscow_times_get_msk_in_brackets():
    s = FlightSnapshot(flight="EK770", date="2026-10-16", direction="departure", origin_iata="DXB",
                       dest_iata="CPT", status="",
                       departure=Leg(plan="2026-10-16T09:10:00+04:00"),
                       arrival=Leg(plan="2026-10-16T16:40:00+02:00"))
    assert "◷ Вылет 09:10 (08:10 МСК) · Прилёт 16:40 (17:40 МСК)" in status_card(s, NOW)
    moscow = _su5822()
    assert "МСК" not in status_card(moscow, NOW).split("\n")[3]          # московский рейс как был
