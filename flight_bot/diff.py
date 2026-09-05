"""Diff двух снапшотов рейса → список человеческих изменений для пуша.

События — по хронологии рейса: регистрация открыта →
закрыта → посадка → вылет → прилёт. Ловим по появлению факта, а не по тексту
статуса: тот шумный и менялся бы на пустом месте. Единственное текстовое —
отмена: у табло она живёт только в статусе, ключевое слово стабильно.

Сдвиг времени шлём от порога `shift_min`: табло дёргает оценку на минуту-две,
«мелкий сдвиг пугать незачем» (правило со страницы).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from flight_bot.models import FlightSnapshot, Leg, is_cancelled


def _hhmm(iso: Optional[str]) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else "—"


def _dt(iso: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _shift_min(a: Optional[str], b: Optional[str]) -> Optional[int]:
    da, db = _dt(a), _dt(b)
    if not da or not db:
        return None
    return int(round((db - da).total_seconds() / 60))


def _leg_changes(label: str, done: str, a: Leg, b: Leg, shift_min: int) -> List[str]:
    if b.fact and not a.fact:
        return [f"{done} в {_hhmm(b.fact)} (план {_hhmm(b.plan)})"]
    if b.fact:
        return []
    a_eff = a.est or a.plan
    b_eff = b.est or b.plan
    delta = _shift_min(a_eff, b_eff)
    if delta is None or abs(delta) < shift_min:
        return []
    sign = "+" if delta > 0 else "−"
    return [f"⏱ {label}: {_hhmm(a_eff)} → {_hhmm(b_eff)} ({sign}{abs(delta)} мин)"]


def diff_snapshots(prev: FlightSnapshot, curr: FlightSnapshot,
                   shift_min: int = 5) -> List[str]:
    """Что изменилось между двумя опросами одного рейса. Пусто — без изменений."""
    out: List[str] = []

    if is_cancelled(curr) and not is_cancelled(prev):
        return ["❌ Рейс отменён"]   # остальное после отмены уже неважно

    # Хронология — в том же порядке, что на странице.
    pc, cc = prev.checkin, curr.checkin
    if cc.start_fact and not pc.start_fact:
        where = f", стойки {curr.checkin.desks}" if curr.checkin.desks else ""
        term = f", терминал {curr.terminal}" if curr.terminal else ""
        out.append(f"🛎 Регистрация открыта{where}{term}")
    if cc.finish_fact and not pc.finish_fact:
        out.append(f"Регистрация закрыта в {_hhmm(cc.finish_fact)}")
    pb, cb = prev.boarding, curr.boarding
    if cb.start_fact and not pb.start_fact:
        gate = f", выход {curr.gate}" if curr.gate else ""
        out.append(f"🚶 Посадка началась{gate}")
    if cb.finish_fact and not pb.finish_fact:
        out.append(f"Посадка закончена в {_hhmm(cb.finish_fact)}")
    out += _leg_changes("Вылет", "✈️ Вылетел", prev.departure, curr.departure, shift_min)
    out += _leg_changes("Прилёт", "🛬 Прилетел", prev.arrival, curr.arrival, shift_min)

    # Плитки. Смену выхода показываем только до вылета: после — уже история.
    if curr.gate and curr.gate != prev.gate and not curr.departure.fact:
        out.append(f"🚪 Выход: {prev.gate or '—'} → {curr.gate}")
    if curr.terminal and curr.terminal != prev.terminal:
        out.append(f"🏢 Терминал: {prev.terminal or '—'} → {curr.terminal}")
    if curr.baggage_belt and curr.baggage_belt != prev.baggage_belt:
        out.append(f"🧳 Лента багажа {curr.baggage_belt}")
    # У табло без времён фаз (VKO) смена фазы живёт только в статусе. Шлём её,
    # если больше ничего не изменилось и статус без цифр — статусы с временем
    # (SVO: «Регистрация в 18:25», «Прибыл … 06:07») дёргаются и не нужны.
    if not out and curr.status and curr.status != prev.status and not any(c.isdigit() for c in curr.status):
        out.append(f"ℹ️ {curr.status}")
    return out
