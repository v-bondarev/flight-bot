"""Diff двух снапшотов рейса → список человеческих изменений для пуша.

События — по хронологии рейса: регистрация открыта →
закрыта → посадка → вылет → прилёт. Ловим по появлению факта, а не по тексту
статуса: тот шумный и менялся бы на пустом месте. Единственное текстовое —
отмена: у табло она живёт только в статусе, ключевое слово стабильно.

Сдвиг времени шлём от порога `shift_min`: табло дёргает оценку на минуту-две,
«мелкий сдвиг пугать незачем».
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flight_bot.models import FlightSnapshot, Leg, is_cancelled


def _hhmm(iso: Optional[str]) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else "—"


MSK = timezone(timedelta(hours=3))


def with_msk(iso: Optional[str]) -> str:
    """«09:10» местного; если аэропорт не в поясе МСК — «09:10 (08:10 МСК)».
    Время без офсета (пояс неизвестен) — как есть, не угадываем."""
    hhmm = _hhmm(iso)
    d = _dt(iso)
    if d is None or d.utcoffset() in (None, timedelta(hours=3)):
        return hhmm
    return f"{hhmm} ({d.astimezone(MSK):%H:%M} МСК)"


def _dt(iso: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _shift_min(a: Optional[str], b: Optional[str]) -> Optional[int]:
    da, db = _dt(a), _dt(b)
    if not da or not db:
        return None
    # Время без офсета — местное того же аэропорта, что и у пары: берём её пояс.
    if da.tzinfo is None:
        da = da.replace(tzinfo=db.tzinfo)
    elif db.tzinfo is None:
        db = db.replace(tzinfo=da.tzinfo)
    return int(round((db - da).total_seconds() / 60))


def _leg_changes(label: str, done: str, a: Leg, b: Leg, shift_min: int,
                 tail: str = "") -> List[str]:
    if b.fact and not a.fact:
        return [f"{done} в {with_msk(b.fact)} (по расписанию {_hhmm(b.plan)}){tail}"]
    if b.fact:
        return []
    a_eff = a.est or a.plan
    b_eff = b.est or b.plan
    delta = _shift_min(a_eff, b_eff)
    if delta is None or abs(delta) < shift_min:
        return []
    when = "позже" if delta > 0 else "раньше"
    return [f"⏱ {label} {when} на {abs(delta)} мин: теперь {with_msk(b_eff)}, было {_hhmm(a_eff)}"]


def _gate_change(prev: FlightSnapshot, curr: FlightSnapshot) -> List[str]:
    """Смена выхода — с тем, что это за выход и в каком аэропорту."""
    if not curr.gate or curr.gate == prev.gate or curr.departure.fact:
        return []      # после вылета смена выхода — уже история
    if prev.source and curr.source and prev.source != curr.source:
        return []      # выходы разных источников несравнимы: «смена» была бы ложной
    if curr.direction == "arrival":
        what, where = "гейт прилёта", curr.dest_iata or curr.dest_city
    else:
        what, where = "выход на посадку", curr.origin_iata or curr.origin_city
    at = f" в {where}" if where else ""
    if prev.gate:
        return [f"🚪 Новый {what}{at}: {curr.gate} (был {prev.gate})"]
    return [f"🚪 Назначен {what}{at}: {curr.gate}"]


def diff_snapshots(prev: FlightSnapshot, curr: FlightSnapshot,
                   shift_min: int = 5) -> List[str]:
    """Что изменилось между двумя опросами одного рейса. Пусто — без изменений."""
    out: List[str] = []

    if is_cancelled(curr) and not is_cancelled(prev):
        # Остальное после отмены уже неважно.
        return ["❌ Рейс отменён. Уточни варианты у авиакомпании, а я больше за ним не слежу."]

    # Хронология — по порядку событий рейса.
    pc, cc = prev.checkin, curr.checkin
    if cc.start_fact and not pc.start_fact:
        where = ", ".join(filter(None, (
            f"терминал {curr.terminal}" if curr.terminal else "",
            "стойки " + re.sub(r",\s*", ", ", cc.desks) if cc.desks else "")))
        out.append("🛎 Началась регистрация" + (f": {where}" if where else ""))
    if cc.finish_fact and not pc.finish_fact:
        out.append(f"🔒 Регистрация закончилась в {_hhmm(cc.finish_fact)}")
    pb, cb = prev.boarding, curr.boarding
    if cb.start_fact and not pb.start_fact:
        gate = f", выход {curr.gate}" if curr.gate else ""
        out.append(f"🚶 Началась посадка{gate}. Пора к выходу!")
    if cb.finish_fact and not pb.finish_fact:
        out.append(f"🔒 Посадка закончилась в {_hhmm(cb.finish_fact)}")
    out += _leg_changes("Вылет", "✈️ Самолёт взлетел", prev.departure, curr.departure, shift_min)
    out += _leg_changes("Прилёт", "🛬 Самолёт приземлился", prev.arrival, curr.arrival, shift_min,
                        ". С прибытием!")

    # Плитки.
    out += _gate_change(prev, curr)
    if curr.terminal and curr.terminal != prev.terminal:
        out.append(f"🏢 Сменился терминал: теперь {curr.terminal}, был {prev.terminal}" if prev.terminal
                   else f"🏢 Назначен терминал {curr.terminal}")
    if curr.baggage_belt and curr.baggage_belt != prev.baggage_belt:
        out.append(f"🧳 Багаж выдадут на ленте {curr.baggage_belt}")
    # У табло без времён фаз (VKO) смена фазы живёт только в статусе. Шлём её,
    # если больше ничего не изменилось и статус без цифр — статусы с временем
    # (SVO: «Регистрация в 18:25», «Прибыл … 06:07») дёргаются и не нужны.
    if not out and curr.status and curr.status != prev.status and not any(c.isdigit() for c in curr.status):
        out.append(f"ℹ️ {curr.status}")
    return out
