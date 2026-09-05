"""Формат карточки статуса и пуша — закреплён как спецификация."""
from flight_bot.diff import diff_snapshots
from flight_bot.models import FlightSnapshot, Leg
from flight_bot.render import status_card, update_message


def _snap(**kw):
    base = dict(flight="SU2128", date="2026-09-06", direction="departure",
                origin_iata="SVO", dest_iata="AYT", origin_city="Москва",
                dest_city="Анталья", status="Регистрация в 18:25~служебный хвост",
                departure=Leg(plan="2026-09-06T00:25:00+03:00"),
                arrival=Leg(plan="2026-09-06T05:35:00+03:00"),
                terminal="C", gate="129")
    base.update(kw)
    return FlightSnapshot(**base)


def test_status_card_format():
    assert status_card(_snap()) == (
        "✈️ SU2128 · SVO→AYT · 06.09\n"
        "Москва → Анталья\n"
        "Статус: Регистрация в 18:25\n"     # хвост после '~' отрезан
        "Вылет: 00:25\n"
        "Прилёт: 05:35\n"
        "Терминал C, выход 129"             # регистр терминала сохранён
    )


def test_status_card_shows_estimate_and_fact():
    s = _snap(departure=Leg(plan="2026-09-06T00:25:00+03:00",
                            est="2026-09-06T01:10:00+03:00"),
              arrival=Leg(plan="2026-09-06T05:35:00+03:00",
                          fact="2026-09-06T05:34:00+03:00"))
    card = status_card(s)
    assert "Вылет: 00:25 → ожид. 01:10" in card
    assert "Прилёт: 05:35 → факт 05:34" in card


def test_update_message_format():
    prev = _snap()
    curr = _snap(gate="141", departure=Leg(plan="2026-09-06T00:25:00+03:00",
                                           est="2026-09-06T00:55:00+03:00"))
    assert update_message(curr, diff_snapshots(prev, curr)) == (
        "🔔 SU2128 · SVO→AYT · 06.09\n"
        "🚪 Выход: 129 → 141\n"
        "⏱ Вылет: 00:25 → 00:55"
    )
