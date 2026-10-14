"""Источник: табло Бахрейна (BAH). JSON-API сайта аэропорта, ключ не нужен.

    POST https://www.bahrainairport.bh/api/FlightProxy/departures|arrivals
         {"date": "дд/мм/гггг"}
    → {"data": [ {flight "GF 013", scheduled/estimated/actualDate+Time (местное, +03:00),
        remarks On Time|Delayed|Early|Departed|Landed…, gate, checkinCounter (зона стоек «E»),
        baggage (лента «A04»), originAirportCode, destinationAirportCode}, … ]}

Даёт сегодня и завтра. Ловушка: на завтра API бывает отдаёт «Departed/Landed» с фактом
позже текущего момента — такой факт не принимаем (иначе ложный пуш и снятие подписки).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flight_bot.http import Fetcher
from flight_bot.models import Checkin, FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource
from flight_bot.sources.led import canon

BASE_URL = "https://www.bahrainairport.bh/api/FlightProxy/"
AST = timezone(timedelta(hours=3))
DONE = {"Departed", "Landed"}

STATUS = {"On Time": "По расписанию", "Early": "Раньше расписания", "Delayed": "Задерживается",
          "Departed": "Вылетел", "Landed": "Приземлился", "Cancelled": "Отменён"}


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
    raw = x.get("remarks") or ""
    if fact and datetime.fromisoformat(fact) > now:      # «факт» из будущего — не факт
        fact = None
        if raw in DONE:
            raw = "Estimated"
    if raw not in DONE:
        fact = None
    own = Leg(plan=plan, est=est if est != plan else None, fact=fact)
    status = "Ожидается" if raw == "Estimated" else STATUS.get(raw, raw)
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

    async def fetch(self, flight_no: str, date: Optional[str] = None,
                    direction: str = "departure") -> List[FlightSnapshot]:
        import httpx

        today = datetime.now(AST).date()
        days = [datetime.fromisoformat(date).date()] if date else [today, today + timedelta(days=1)]
        out: List[FlightSnapshot] = []
        url = BASE_URL + ("departures" if direction == "departure" else "arrivals")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for day in days:
                resp = await client.post(url, json={"date": day.strftime("%d/%m/%Y")})
                resp.raise_for_status()
                out += parse(json.loads(resp.text).get("data") or [], flight_no, direction)
        return [s for s in out if not date or s.date == date]
