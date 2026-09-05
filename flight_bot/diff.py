"""Diff двух снапшотов рейса → список человеческих изменений для пуша.

Считаем по структурным полям (время/выход/терминал), а НЕ по сырому тексту
статуса табло: он шумный ("Прибыл в Анталья 06:07~Рейс за 04.09.26") и менялся
бы на пустом месте. Событие вылета/прилёта ловим по появлению факта.
"""
from __future__ import annotations

from typing import List

from flight_bot.models import FlightSnapshot, Leg


def _hhmm(iso: str) -> str:
    """ISO '2026-09-04T00:25:00+03:00' → '00:25'. Пусто — прочерк."""
    return iso[11:16] if iso and len(iso) >= 16 else "—"


def _leg_changes(label: str, a: Leg, b: Leg) -> List[str]:
    out: List[str] = []
    if b.fact and not a.fact:
        out.append(f"✈️ {label} состоялся в {_hhmm(b.fact)} (план {_hhmm(b.plan)})")
        return out  # факт перекрывает сдвиг оценки — не дублируем
    a_eff = a.est or a.plan
    b_eff = b.est or b.plan
    if not b.fact and b_eff and b_eff != a_eff:
        out.append(f"⏱ {label}: {_hhmm(a_eff)} → {_hhmm(b_eff)}")
    return out


def diff_snapshots(prev: FlightSnapshot, curr: FlightSnapshot) -> List[str]:
    """Что изменилось между двумя опросами одного рейса. Пусто — без изменений."""
    out: List[str] = []
    if curr.gate and curr.gate != prev.gate:
        out.append(f"🚪 Выход: {prev.gate or '—'} → {curr.gate}")
    if curr.terminal and curr.terminal != prev.terminal:
        out.append(f"🏢 Терминал: {prev.terminal or '—'} → {curr.terminal}")
    out += _leg_changes("Вылет", prev.departure, curr.departure)
    out += _leg_changes("Прилёт", prev.arrival, curr.arrival)
    return out
