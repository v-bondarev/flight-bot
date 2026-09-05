"""Источник и резолвер AirLabs (airlabs.co) — рейс по номеру вне наших табло.

    GET https://airlabs.co/api/v9/flight?flight_iata=SU2128&api_key=…
    → {"response": {dep_iata, arr_iata, dep_time, dep_time_utc, dep_estimated,
       dep_actual, dep_terminal, dep_gate, arr_*, arr_baggage, status, …}}

Отдаёт ОДИН ближайший рейс (live/scheduled/landed), дату не выбрать — сверяем
сами. Времена — локальные для каждого аэропорта, без офсета; офсет
восстанавливаем по паре local/utc, иначе снапшот несравним с табло (там ISO
с офсетом) и отсчёт «до вылета» упал бы на naive-datetime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flight_bot.models import FlightRoute, FlightSnapshot, Leg
from flight_bot.sources.base import AirportResolver, FlightSource

BASE_URL = "https://airlabs.co/api/v9/flight"

STATUS = {
    "scheduled": "Ожидается",
    "active": "В воздухе",
    "en-route": "В воздухе",
    "landed": "Приземлился",
    "cancelled": "Отменён",
}


def _parse(s: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s) if s else None   # «2026-09-06 00:25»
    except ValueError:
        return None


def _offset(local: Optional[str], utc: Optional[str]) -> timedelta:
    a, b = _parse(local), _parse(utc)
    return a - b if a and b else timedelta(0)


def _iso(local: Optional[str], off: timedelta) -> Optional[str]:
    d = _parse(local)
    return d.replace(tzinfo=timezone(off)).isoformat() if d else None


def parse(payload: dict, flight_no: str) -> Optional[FlightSnapshot]:
    """Чистый парсер ответа — без сети."""
    r = payload.get("response") or {}
    if isinstance(r, list):
        r = r[0] if r else {}
    if not r:
        return None
    dep_off = _offset(r.get("dep_time"), r.get("dep_time_utc"))
    arr_off = _offset(r.get("arr_time"), r.get("arr_time_utc"))
    departure = Leg(plan=_iso(r.get("dep_time"), dep_off),
                    est=_iso(r.get("dep_estimated"), dep_off),
                    fact=_iso(r.get("dep_actual"), dep_off))
    arrival = Leg(plan=_iso(r.get("arr_time"), arr_off),
                  est=_iso(r.get("arr_estimated"), arr_off),
                  fact=_iso(r.get("arr_actual"), arr_off))
    raw = (r.get("status") or "").lower()
    flight = (r.get("flight_iata") or flight_no).replace(" ", "").upper()
    date = (departure.plan or r.get("flight_date") or "")[:10]
    return FlightSnapshot(
        flight=flight,
        date=date,
        direction="departure",
        origin_iata=(r.get("dep_iata") or "").upper(),
        dest_iata=(r.get("arr_iata") or "").upper(),
        status=STATUS.get(raw, raw),
        departure=departure,
        arrival=arrival,
        airline=r.get("airline_name") or r.get("airline_iata") or "",
        aircraft=r.get("aircraft_icao") or "",
        terminal=r.get("dep_terminal") or "",
        gate=r.get("dep_gate") or "",
        baggage_belt=r.get("arr_baggage") or "",
        source="airlabs.co",
        key=f"{flight}:{date}",
    )


class AirlabsSource(FlightSource):
    name = "airlabs.co"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self._key = api_key
        self._timeout = timeout

    async def fetch(
        self,
        flight_no: str,
        date: Optional[str] = None,
        direction: str = "departure",   # AirLabs не привязан к аэропорту — не важно
    ) -> List[FlightSnapshot]:
        import httpx  # ленивый импорт: тесты парсера не тянут сеть

        params = {"flight_iata": flight_no.replace(" ", "").upper(), "api_key": self._key}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(BASE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
        snap = parse(payload, flight_no)
        if snap is None:
            return []
        return [snap] if not date or snap.date == date else []


class AirlabsResolver(AirportResolver):
    """«Номер → аэропорт» через AirLabs — для рейсов вне наших табло."""

    name = "airlabs"

    def __init__(self, source: AirlabsSource):
        self._src = source

    async def resolve(self, flight_no: str, date: Optional[str] = None) -> List[FlightRoute]:
        return [
            FlightRoute(flight=s.flight, date=s.date, origin_iata=s.origin_iata,
                        dest_iata=s.dest_iata, airline=s.airline)
            for s in await self._src.fetch(flight_no, date)
        ]
