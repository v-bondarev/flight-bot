"""Источник AeroDataBox (через RapidAPI) — расписание рейса по номеру на даты вперёд.

    GET https://aerodatabox.p.rapidapi.com/flights/number/EK770/2026-10-14/2026-10-19
        ?dateLocalRole=Departure          заголовки X-RapidAPI-Key / X-RapidAPI-Host
    → [ {number, status, departure:{airport:{iata}, scheduledTime:{local}, revisedTime,
         runwayTime, terminal, gate}, arrival:{…, predictedTime, baggageBelt},
         aircraft:{model}, airline:{name}} ]   — по элементу на дату

Табло держат только вчера/сегодня/завтра, а этот метод отдаёт и будущие даты,
по всему миру. RapidAPI отвечает 451 сети сервера бота (AS215540 считает
«embargoed»), поэтому запросы идут через SCHEDULE_PROXY_URL (deploy/dk-proxy.service).
Зато дорог: один запрос = 6 единиц из 400 бесплатных в месяц,
поэтому зовём его только при поиске рейса (registry.search, с кэшем), а не в опросе.
Дата рейса — локальная дата вылета, как и у табло.
"""
from __future__ import annotations

from datetime import date as Date, datetime, timedelta, timezone
from typing import List, Optional

from flight_bot.models import FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource

HOST = "aerodatabox.p.rapidapi.com"
BASE_URL = f"https://{HOST}/flights/number"
HORIZON_DAYS = 5
MSK = timezone(timedelta(hours=3))

STATUS = {
    "Expected": "Ожидается",
    "CheckIn": "Регистрация",
    "Boarding": "Посадка",
    "GateClosed": "Посадка закончена",
    "Delayed": "Задерживается",
    "Departed": "Вылетел",
    "EnRoute": "В воздухе",
    "Approaching": "Заходит на посадку",
    "Arrived": "Прилетел",
    "Diverted": "Ушёл на запасной",
    "Canceled": "Отменён",
    "CanceledUncertain": "Статус не подтверждён",   # без «отмен»: is_cancelled снял бы подписку
}


def _iso(node: Optional[dict]) -> Optional[str]:
    """{"local": "2026-10-14 09:10+04:00"} → ISO с офсетом."""
    local = (node or {}).get("local")
    try:
        return datetime.fromisoformat(local).isoformat() if local else None
    except ValueError:
        return None


def _leg(side: dict, fact_key: Optional[str]) -> Leg:
    plan = _iso(side.get("scheduledTime"))
    est = _iso(side.get("predictedTime")) or _iso(side.get("revisedTime"))
    return Leg(plan=plan,
               est=est if est != plan else None,
               fact=_iso(side.get(fact_key)) if fact_key else None)


def _canon(no: str) -> str:
    return (no or "").replace(" ", "").upper()


def parse(payload: list, flight_no: str) -> List[FlightSnapshot]:
    """Чистый парсер ответа — без сети. Чужие номера (кодшеры) отбрасываем."""
    wanted = _canon(flight_no)
    out = []
    for f in payload or []:
        if _canon(f.get("number")) != wanted:
            continue
        dep, arr = f.get("departure") or {}, f.get("arrival") or {}
        raw = f.get("status") or ""
        arrived = raw == "Arrived"
        departure = _leg(dep, "runwayTime")
        arrival = _leg(arr, None)
        if arrived:     # прилетел: последняя оценка и есть факт; план фактом не выдаём
            arrival = Leg(plan=arrival.plan, fact=_iso(arr.get("runwayTime")) or arrival.est)
        day = (departure.plan or "")[:10]
        out.append(FlightSnapshot(
            flight=wanted,
            date=day,
            direction="departure",
            origin_iata=((dep.get("airport") or {}).get("iata") or "").upper(),
            dest_iata=((arr.get("airport") or {}).get("iata") or "").upper(),
            status=STATUS.get(raw, raw),
            departure=departure,
            arrival=arrival,
            airline=(f.get("airline") or {}).get("name") or "",
            aircraft=(f.get("aircraft") or {}).get("model") or "",
            terminal=dep.get("terminal") or "",
            gate=dep.get("gate") or "",
            baggage_belt=arr.get("baggageBelt") or "",
            source="aerodatabox.com",
            key=f"{wanted}:{day}",
        ))
    return out


class AeroDataBoxSource(FlightSource):
    name = "aerodatabox.com"

    def __init__(self, api_key: str, proxy: str = "", timeout: float = 20.0):
        self._key = api_key
        self._proxy = proxy or None     # RapidAPI режет адрес сервера бота (451) — выход через DK
        self._timeout = timeout

    async def fetch(
        self,
        flight_no: str,
        date: Optional[str] = None,
        direction: str = "departure",   # расписание по номеру, аэропорт не важен
    ) -> List[FlightSnapshot]:
        import httpx  # ленивый импорт: тесты парсера не тянут сеть

        if date:
            start = end = Date.fromisoformat(date)
        else:
            start = datetime.now(MSK).date()
            end = start + timedelta(days=HORIZON_DAYS)
        no = _canon(flight_no)
        headers = {"X-RapidAPI-Key": self._key, "X-RapidAPI-Host": HOST}
        params = {"dateLocalRole": "Departure", "withAircraftImage": "false",
                  "withLocation": "false"}
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers,
                                     proxy=self._proxy) as client:
            resp = await client.get(f"{BASE_URL}/{no}/{start}/{end}", params=params)
            if resp.status_code == 204:        # рейса нет в эти даты
                return []
            resp.raise_for_status()
            payload = resp.json()
        return parse(payload, no)
