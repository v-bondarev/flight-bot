"""Нормализованные модели рейса — чистые dataclass'ы, без сети и БД.

Снапшот — то, что бот показывает пассажиру и по чему считает изменения.
Один снапшот = один рейс на одну дату с точки зрения одного источника.
Состав полей повторяет flight.json страницы flight.vbondarev.ru: она и есть
эталон подачи.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Leg:
    """Триплет план/оценка/факт по одной стороне (вылет или прилёт).

    Времена храним как ISO-строки ровно в том виде, как их отдал источник:
    приводить к datetime здесь незачем, а лишняя нормализация теряет офсет.
    """
    plan: Optional[str] = None
    est: Optional[str] = None
    fact: Optional[str] = None


@dataclass(frozen=True)
class Checkin:
    desks: str = ""                      # «312,314-316,319-346»
    start_plan: Optional[str] = None
    finish_plan: Optional[str] = None
    start_fact: Optional[str] = None
    finish_fact: Optional[str] = None


@dataclass(frozen=True)
class Boarding:
    start_fact: Optional[str] = None
    finish_fact: Optional[str] = None
    start_min: str = ""                  # «за N мин до вылета», пока посадки нет


@dataclass(frozen=True)
class FlightSnapshot:
    """Плоский снимок рейса. Сравнение двух снапшотов даёт список изменений."""
    flight: str            # "SU2128"
    date: str              # "2026-09-04" (дата рейса, YYYY-MM-DD)
    direction: str         # departure | arrival — с чьей стороны смотрит источник
    origin_iata: str
    dest_iata: str
    status: str            # человеческий статус источника ("Регистрация идет"…)
    departure: Leg = field(default_factory=Leg)
    arrival: Leg = field(default_factory=Leg)
    checkin: Checkin = field(default_factory=Checkin)
    boarding: Boarding = field(default_factory=Boarding)
    airline: str = ""
    origin_city: str = ""
    dest_city: str = ""
    aircraft: str = ""
    terminal: str = ""
    gate: str = ""
    gate_prev: str = ""    # прошлый выход, если сменили — самое дорогое для пассажира
    gate_terminal: str = ""  # выход бывает в другом терминале, чем стойки
    baggage_belt: str = ""
    source: str = ""       # "svo.aero", "dme.ru"…
    key: str = ""          # стабильный id рейса у источника, для сопоставления опросов


def is_cancelled(s: FlightSnapshot) -> bool:
    """Отмену табло отдаёт текстом статуса; ключевое слово стабильно."""
    return "отмен" in (s.status or "").lower()


def snap_to_dict(s: FlightSnapshot) -> dict:
    return dataclasses.asdict(s)


def snap_from_dict(d: dict) -> FlightSnapshot:
    d = dict(d)
    d["departure"] = Leg(**(d.get("departure") or {}))
    d["arrival"] = Leg(**(d.get("arrival") or {}))
    d["checkin"] = Checkin(**(d.get("checkin") or {}))
    d["boarding"] = Boarding(**(d.get("boarding") or {}))
    return FlightSnapshot(**d)


@dataclass(frozen=True)
class FlightRoute:
    """Маршрут рейса: чем отвечает резолвер «номер рейса → аэропорты»."""
    flight: str
    date: str
    origin_iata: str
    dest_iata: str
    airline: str = ""
