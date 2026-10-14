"""Источник: табло Бахрейна (BAH). JSON-API сайта аэропорта, ключ не нужен.

    POST https://www.bahrainairport.bh/api/FlightProxy/departures|arrivals
         {"date": "дд/мм/гггг"}
    → {"data": [ {flight "GF 013", scheduled/estimated/actualDate+Time (местное, +03:00),
        remarks On Time|Delayed|Early|Departed|Landed…, gate, checkinCounter (зона стоек «E»),
        baggage (лента «A04»), originAirportCode, destinationAirportCode}, … ]}

Даёт сегодня и завтра. Ловушка: на завтра API бывает отдаёт «Departed/Landed» с фактом
позже текущего момента — такой факт не принимаем (иначе ложный пуш и снятие подписки).
Ответ на дату один на все рейсы: держим DAY_TTL_SEC, одновременные запросы склеиваем.
Статусы распознаём по смыслу (cancel/land/arriv/depart): полный словарь табло неизвестен.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from flight_bot.http import SSL_CONTEXT, UA, SharedFetch
from flight_bot.models import Checkin, FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource
from flight_bot.sources.led import canon

BASE_URL = "https://www.bahrainairport.bh/api/FlightProxy/"
AST = timezone(timedelta(hours=3))
DAY_TTL_SEC = 60.0
FUTURE_SLACK = timedelta(minutes=10)   # часы сервера и табло расходятся на минуты

# (ключевое слово, текст): первое совпадение по порядку. Полного словаря табло нет.
WORDS = (("final", "Посадка заканчивается"), ("gate clos", "Посадка закончена"),
         ("closed", "Посадка закончена"), ("board", "Посадка"), ("gate open", "Посадка"),
         ("check", "Регистрация"), ("divert", "Ушёл на запасной"), ("delay", "Задерживается"),
         ("early", "Раньше расписания"), ("on time", "По расписанию"), ("schedul", "По расписанию"),
         ("estimat", "Ожидается"), ("expect", "Ожидается"))


def _status(raw: str) -> tuple:
    """(текст для карточки, событие свершилось?) по смыслу, без точного словаря."""
    low = raw.lower()
    if "cancel" in low:
        return "Отменён", False
    if "land" in low or "arriv" in low:
        return "Приземлился", True
    if "depart" in low or "airborne" in low:
        return "Вылетел", True
    return next((text for word, text in WORDS if word in low), raw), False


def _at(day: str, hhmm: str) -> Optional[str]:
    """«14/10/2026», «19:20» → «2026-10-14T19:20:00+03:00»."""
    if not day or not hhmm:
        return None
    try:
        return datetime.strptime(f"{day} {hhmm}", "%d/%m/%Y %H:%M").replace(tzinfo=AST).isoformat()
    except ValueError:
        return None


def build(x: dict, wanted: str, direction: str, now: datetime) -> FlightSnapshot:
    plan = _at(x.get("scheduledDate") or "", x.get("scheduledTime") or "")
    est = _at(x.get("estimatedDate") or "", x.get("estimatedTime") or "")
    fact = _at(x.get("actualDate") or "", x.get("actualTime") or "")
    status, done = _status(x.get("remarks") or "")
    if fact and datetime.fromisoformat(fact) > now + FUTURE_SLACK:   # «факт» из будущего — не факт
        fact = None
        if done:
            status, done = "Ожидается", False
    if not done:
        fact = None
    own = Leg(plan=plan, est=est if est != plan else None, fact=fact)
    departure, arrival = (own, Leg()) if direction == "departure" else (Leg(), own)
    return FlightSnapshot(
        flight=wanted, date=(plan or "")[:10], direction=direction,
        origin_iata=x.get("originAirportCode") or "", dest_iata=x.get("destinationAirportCode") or "",
        status=status, departure=departure, arrival=arrival,
        airline=x.get("airlineName") or "", gate=x.get("gate") or "",
        zone=(x.get("checkinCounter") or "") if direction == "departure" else "",
        checkin=Checkin(), baggage_belt=x.get("baggage") or "",
        source="bahrainairport.bh", key=f"{wanted}:{(plan or '')[:10]}",
    )


def parse(data: list, flight_no: str, direction: str, now: Optional[datetime] = None) -> List[FlightSnapshot]:
    """Чистый парсер ответа — без сети."""
    wanted = canon(flight_no)
    now = now or datetime.now(timezone.utc)
    if not wanted or not isinstance(data, list):
        return []
    return [build(x, wanted, direction, now) for x in data
            if isinstance(x, dict) and canon(x.get("flight") or "") == wanted]


class BahSource(FlightSource):
    name = "bahrainairport.bh"

    def __init__(self, timeout: float = 20.0):
        self._timeout = timeout
        self._days = SharedFetch(DAY_TTL_SEC)

    async def _board(self, url: str, day: str) -> list:
        async with httpx.AsyncClient(timeout=self._timeout, verify=SSL_CONTEXT,
                                     headers={"User-Agent": UA}) as client:
            resp = await client.post(url, json={"date": day})
            resp.raise_for_status()
            return json.loads(resp.text).get("data") or []

    async def fetch(self, flight_no: str, date: Optional[str] = None,
                    direction: str = "departure") -> List[FlightSnapshot]:
        today = datetime.now(AST).date()
        days = [datetime.fromisoformat(date).date()] if date else [today, today + timedelta(days=1)]
        url = BASE_URL + ("departures" if direction == "departure" else "arrivals")
        boards = await asyncio.gather(*(
            self._days.get((url, d), lambda d=d: self._board(url, d.strftime("%d/%m/%Y")))
            for d in days))
        out = [s for data in boards for s in parse(data, flight_no, direction)]
        return [s for s in out if not date or s.date == date]
