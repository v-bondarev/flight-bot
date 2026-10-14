"""Источник: табло Дубая (DXB). Статический JSON всего аэропорта, ключ не нужен.

    GET https://dubaiairports.ae/docs/passengerslibraries/flights-library/flights-data.json
    → {"Flights": [ {flightNumber "EK 770", arrivalDepartureFlag D|A, aircraftTerminal,
        gateNumber, baggageBeltNumber, flightStatus, origin_iata, destination_iata,
        scheduled/estimated/actualOffBlockTime (вылет), scheduled/estimatedOnBlockTime,
        actualOnBlockTime (прилёт), jointFlightNumber «SA 7157\\tDE 5947» (кодшеры)}, … ],
       "LastFetchedAt": …}

Вчера/сегодня/завтра, вылеты и прилёты. Ловушка: у времён рейсов суффикс «Z», но это
местное время Дубая (+04:00) — EK770 «09:10:00Z» вылетает в 09:10 местного. Настоящий
UTC — только LastFetchedAt. Файл ~3,7 МБ на весь аэропорт — держим его FILE_TTL_SEC.
Стоек и посадки табло не даёт.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from flight_bot.http import Fetcher
from flight_bot.models import FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource
from flight_bot.sources.led import canon

URL = "https://dubaiairports.ae/docs/passengerslibraries/flights-library/flights-data.json"
GST = timezone(timedelta(hours=4))
FILE_TTL_SEC = 60.0

STATUS = {
    "Estimated": "Ожидается", "Delayed": "Задерживается", "Gate Open": "Посадка",
    "Final Call": "Посадка заканчивается", "Gate Closed": "Посадка закончена",
    "Departed": "Вылетел", "Landed": "Приземлился", "Arrived": "Прилетел",
    "Cancelled": "Отменён",
}


def _iso(value: Optional[str]) -> Optional[str]:
    """«2026-10-14T09:10:00Z» (на деле местное) → «2026-10-14T09:10:00+04:00»."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=GST).isoformat()
    except ValueError:
        return None


def _numbers(f: dict) -> List[str]:
    joint = (f.get("jointFlightNumber") or "").split("\t")
    return [n for n in (canon(x) for x in [f.get("flightNumber") or ""] + joint) if n]


def build(f: dict, wanted: str, direction: str) -> FlightSnapshot:
    raw = f.get("flightStatus") or ""
    side = "OffBlockTime" if direction == "departure" else "OnBlockTime"
    plan = _iso(f.get("scheduledoffblockTime" if direction == "departure" else "scheduledOnblockTime"))
    est = _iso(f.get("estimated" + side))
    own = Leg(plan=plan, est=est if est != plan else None, fact=_iso(f.get("actual" + side)))
    departure, arrival = (own, Leg()) if direction == "departure" else (Leg(), own)
    return FlightSnapshot(
        flight=wanted, date=(own.plan or "")[:10], direction=direction,
        origin_iata=f.get("origin_iata") or "", dest_iata=f.get("destination_iata") or "",
        status=STATUS.get(raw, raw), departure=departure, arrival=arrival,
        airline=f.get("airlineName") or "", terminal=f.get("aircraftTerminal") or "",
        gate=(f.get("gateNumber") or "") if direction == "departure" else "",
        baggage_belt=(f.get("baggageBeltNumber") or "") if direction == "arrival" else "",
        source="dubaiairports.ae", key=str(f.get("aodbUniqueField") or ""),
    )


def parse(payload: dict, flight_no: str, direction: str) -> List[FlightSnapshot]:
    """Чистый парсер файла — без сети. Ищем и по номеру оператора, и по кодшерам."""
    wanted = canon(flight_no)
    flag = "D" if direction == "departure" else "A"
    if not wanted or not isinstance(payload, dict):
        return []
    found = [f for f in payload.get("Flights") or []
             if isinstance(f, dict) and f.get("arrivalDepartureFlag") == flag and wanted in _numbers(f)]
    return sorted((build(f, wanted, direction) for f in found), key=lambda s: s.date)


class DxbSource(FlightSource):
    name = "dubaiairports.ae"

    def __init__(self, scrapedo_api_key: str = "", timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.fetcher = Fetcher(scrapedo_api_key, timeout=timeout)
        self._clock = clock
        self._file: Optional[tuple] = None          # (ts, payload): один файл на все рейсы

    async def _payload(self) -> dict:
        import json
        if self._file and self._clock() - self._file[0] < FILE_TTL_SEC:
            return self._file[1]
        payload = json.loads(await self.fetcher.get(URL))
        self._file = (self._clock(), payload)
        return payload

    async def fetch(self, flight_no: str, date: Optional[str] = None,
                    direction: str = "departure") -> List[FlightSnapshot]:
        snaps = parse(await self._payload(), flight_no, direction)
        return [s for s in snaps if not date or s.date == date]
