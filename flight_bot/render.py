"""Форматирование рейса в текст Telegram. Общий для бота и поллера."""
from __future__ import annotations

from typing import List

from flight_bot.models import FlightSnapshot, Leg


def _dm(date: str) -> str:
    return f"{date[8:10]}.{date[5:7]}" if len(date) >= 10 else date


def _hhmm(iso: str) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else "—"


def _clean_status(status: str) -> str:
    # Табло SVO подмешивает служебный хвост через '~' — в карточке он лишний.
    return status.split("~", 1)[0].strip()


def _leg(leg: Leg) -> str:
    plan = _hhmm(leg.plan)
    if leg.fact:
        return f"{plan} → факт {_hhmm(leg.fact)}"
    if leg.est and leg.est != leg.plan:
        return f"{plan} → ожид. {_hhmm(leg.est)}"
    return plan


def route_line(s: FlightSnapshot) -> str:
    return f"{s.flight} · {s.origin_iata}→{s.dest_iata} · {_dm(s.date)}"


def status_card(s: FlightSnapshot) -> str:
    lines = [f"✈️ {route_line(s)}"]
    if s.origin_city or s.dest_city:
        lines.append(f"{s.origin_city} → {s.dest_city}".strip(" →"))
    st = _clean_status(s.status)
    if st:
        lines.append(f"Статус: {st}")
    lines.append(f"Вылет: {_leg(s.departure)}")
    lines.append(f"Прилёт: {_leg(s.arrival)}")
    tail = []
    if s.terminal:
        tail.append(f"терминал {s.terminal}")
    if s.gate:
        tail.append(f"выход {s.gate}")
    if tail:
        joined = ", ".join(tail)
        # не capitalize(): он опустил бы регистр терминала ("Терминал c")
        lines.append(joined[0].upper() + joined[1:])
    return "\n".join(lines)


def update_message(s: FlightSnapshot, changes: List[str]) -> str:
    return f"🔔 {route_line(s)}\n" + "\n".join(changes)
