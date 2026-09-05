"""Форматирование рейса в текст Telegram (parse_mode=HTML).

Раскладка и тексты — со страницы flight.vbondarev.ru (эталон): шапка с
маршрутом и зачёркнутым планом при сдвиге, статус с точкой и обратным
отсчётом, плитки с подписями, хронология. Города не склоняем: приходят в
именительном и для произвольного города не склоняются.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flight_bot.models import FlightSnapshot, Leg, is_cancelled

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)


def _hhmm(iso: Optional[str]) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else ""


def _dt(iso: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _day(iso: str) -> str:
    d = _dt(iso)
    return f"{d.day} {MONTHS[d.month - 1]}" if d else ""


def human(delta: timedelta) -> str:
    m = int(round(delta.total_seconds() / 60))
    if m < 1:
        return "меньше минуты"
    h, r = divmod(m, 60)
    if h and r:
        return f"{h} ч {r} мин"
    return f"{h} ч" if h else f"{m} мин"


def _leg_time(leg: Leg) -> str:
    """Текущее время; если уехало от плана — план зачёркнут рядом."""
    cur = leg.fact or leg.est or leg.plan
    if not cur:
        return "—"
    if leg.plan and leg.plan != cur:
        return f"<s>{_hhmm(leg.plan)}</s> {_hhmm(cur)}"
    return _hhmm(cur)


def _desks(desks: str) -> str:
    return re.sub(r",\s*", ", ", desks) if desks else "—"


def route_line(s: FlightSnapshot) -> str:
    return f"{s.flight} · {s.origin_iata}→{s.dest_iata} · {s.date[8:10]}.{s.date[5:7]}"


def _dot(s: FlightSnapshot) -> str:
    if is_cancelled(s):
        return "🔴"
    if s.arrival.fact:
        return "🟢"
    leg = s.arrival if s.direction == "arrival" else s.departure
    if leg.est and leg.est != leg.plan:
        return "🟡"                                   # задержка важнее прочего
    if s.departure.fact:
        return "🔵"                                   # уже в воздухе
    if s.direction != "arrival" and s.checkin.start_fact and not s.checkin.finish_fact:
        return "🔵"                                   # регистрация идёт
    return "⚪"


def countdown(s: FlightSnapshot, now: datetime) -> str:
    if s.arrival.fact or is_cancelled(s):
        return ""
    if s.direction == "arrival":
        if not s.departure.fact:
            dp = _dt(s.departure.est or s.departure.plan)
            if dp and dp > now:
                return "До вылета " + human(dp - now)
        ar = _dt(s.arrival.est or s.arrival.plan)
        if ar and ar > now:
            return "В пути, до прилёта " + human(ar - now)
        return "Заходит на посадку"
    ci = s.checkin
    if not ci.start_fact and ci.start_plan:
        st = _dt(ci.start_plan)
        if st and st > now:
            return "До регистрации " + human(st - now)
    if not s.boarding.finish_fact and not s.departure.fact:
        dep = _dt(s.departure.est or s.departure.plan)
        if dep and dep > now:
            return "До вылета " + human(dep - now)
    arr = _dt(s.arrival.est or s.arrival.plan)
    if arr and arr > now:
        return "В пути, до посадки " + human(arr - now)
    return ""


def _tiles_departure(s: FlightSnapshot) -> List[str]:
    ci, b = s.checkin, s.boarding
    live = ci.start_fact and not ci.finish_fact
    if ci.finish_fact:
        desks_note = "закрыта в " + _hhmm(ci.finish_fact)
    elif live:
        desks_note = "идёт до " + _hhmm(ci.finish_plan)
    elif ci.start_plan:
        desks_note = "откроется в " + _hhmm(ci.start_plan)
    else:
        desks_note = "время не объявлено"

    gate_changed = s.gate_prev and not s.departure.fact
    if gate_changed:
        gate_note = f"изменён, был <s>{_e(s.gate_prev)}</s>"
    elif b.finish_fact:
        gate_note = "посадка закончена в " + _hhmm(b.finish_fact)
    elif b.start_fact:
        gate_note = "посадка идёт с " + _hhmm(b.start_fact)
    elif s.gate:
        gate_note = f"посадка за {b.start_min or '40'} мин до вылета"
    else:
        gate_note = "назначат ближе к вылету"

    term = ["Терминал " + (_e(s.terminal) or "—")]
    if s.gate_terminal and s.gate_terminal != s.terminal:
        term.append("выход в терминале " + _e(s.gate_terminal))
    if s.baggage_belt:
        term.append("лента багажа " + _e(s.baggage_belt))
    return [
        f"Стойки {_e(_desks(ci.desks))} · {desks_note}",
        f"Выход {_e(s.gate) or '—'} · {gate_note}",
        " · ".join(term),
    ]


def _tiles_arrival(s: FlightSnapshot) -> List[str]:
    belt = s.baggage_belt
    if belt:
        belt_note = "багаж здесь" if s.arrival.fact else "подадут сюда"
    else:
        belt_note = "ленту вот-вот объявят" if s.arrival.fact else "появится ближе к прилёту"
    gate = f"Гейт прилёта {_e(s.gate)}" if s.gate else "Гейт прилёта — · назначат при заходе"
    return [
        f"Лента багажа {_e(belt) or '—'} · {belt_note}",
        gate,
        "Терминал " + (_e(s.terminal) or "—"),
    ]


def _steps(s: FlightSnapshot) -> List[str]:
    if s.direction == "arrival":
        rows = [
            ("Вылет" + (f" · {_e(s.origin_city)}" if s.origin_city else ""),
             s.departure.fact or s.departure.est or s.departure.plan, bool(s.departure.fact)),
            ("Прилёт", s.arrival.fact or s.arrival.est or s.arrival.plan, bool(s.arrival.fact)),
        ]
    else:
        ci, b = s.checkin, s.boarding
        rows = [
            ("Регистрация", ci.start_fact or ci.start_plan, bool(ci.start_fact)),
            ("Регистрация закрыта", ci.finish_fact or ci.finish_plan, bool(ci.finish_fact)),
            ("Посадка", b.start_fact, bool(b.start_fact)),
            ("Вылет", s.departure.fact or s.departure.est or s.departure.plan, bool(s.departure.fact)),
            ("Прилёт", s.arrival.fact or s.arrival.est or s.arrival.plan, bool(s.arrival.fact)),
        ]
    return [f"{k} · {_hhmm(v) or 'ждём табло'}{' ✓' if done else ''}" for k, v, done in rows]


def status_card(s: FlightSnapshot, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    head = [_e(s.flight), _e(s.airline), _e(s.aircraft)]
    lines = [
        "<b>" + head[0] + "</b>" + "".join(f" · {h}" for h in head[1:] if h),
        _day(s.departure.plan or s.date),
        f"{_e(s.origin_iata)} {_e(s.origin_city)} → {_e(s.dest_iata)} {_e(s.dest_city)}".replace("  ", " "),
        f"Вылет {_leg_time(s.departure)} · Прилёт {_leg_time(s.arrival)}",
        "",
        f"{_dot(s)} {_e(s.status.split('~', 1)[0].strip()) or 'Статус не объявлен'}",
    ]
    cd = countdown(s, now)
    if cd:
        lines.append(cd)
    lines.append("")
    lines += _tiles_arrival(s) if s.direction == "arrival" else _tiles_departure(s)
    lines += ["", "<b>Хронология</b>"] + _steps(s)
    return "\n".join(line for line in lines if line is not None)


def update_message(s: FlightSnapshot, changes: List[str]) -> str:
    return f"🔔 <b>{_e(route_line(s))}</b>\n" + "\n".join(changes)
