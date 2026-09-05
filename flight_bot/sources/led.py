"""Источник: табло Пулково (LED). JSON-API, ключ не нужен — самый полный источник.

    GET https://pulkovoairport.ru/api/?type=departure|arrival&when=-1|0|1&search=<цифры>
    → [ {OD_STD/OD_ETD/OD_ATD, OD_FLIGHT_NUMBER "FV 6599", OD_FLIGHT_NUMBER_K1 (кодшер),
        OD_COUNTERS, OD_COUNTER_BEGIN/END_PLAN|ACTUAL, OD_GATES,
        OD_BOARDING_BEGIN/END_PLAN|ACTUAL, OD_RTRM_CODE (терминал),
        OD_RAP_CODE_DESTINATION + _NAME_RU, OD_RAP_CODE_NEXT (промежуточная),
        OD_RAP_CODE_DESTINATION_STA, OD_STATUS_RU, OD_RACT_ICAO_CODE …}, … ]
    прилёт — те же поля с префиксом OA_: STA/ETA/ATA, BAGGAGEBELTS,
    RAP_CODE_ORIGIN (+STD/ATD оттуда), RAP_CODE_PREVIOUS, RTRM_CODE, STATUS_RU.

`when` — день целиком (вчера/сегодня/завтра), `search` — только цифры номера
(«FV 6599» не находит, «6599» — да), поэтому код авиакомпании сверяем сами,
с учётом кодшера (_K1). Номера табло дополняет нулями/пробелами («SU  025») —
сравниваем по коду + числу без ведущих нулей. Времена — местные (МСК), без
офсета в строке; добавляем +03:00.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from flight_bot.http import Fetcher
from flight_bot.models import Boarding, Checkin, FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource

BASE_URL = "https://pulkovoairport.ru/api/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MSK = timezone(timedelta(hours=3))
_NUM_RE = re.compile(r"^([A-Z0-9]{2})\s*0*(\d{1,4})$")


def canon(flight_no: str) -> Optional[str]:
    """«SU  025» / «su 25» / «SU-025» → «SU25». None — не похоже на номер."""
    m = _NUM_RE.match(re.sub(r"[\s-]", "", flight_no or "").upper())
    return f"{m.group(1)}{int(m.group(2))}" if m else None


def _iso(local: Optional[str]) -> Optional[str]:
    """«2026-09-05T19:10:00.000» → «2026-09-05T19:10:00+03:00»."""
    if not local:
        return None
    try:
        return datetime.fromisoformat(local.split(".")[0]).replace(tzinfo=MSK).isoformat()
    except ValueError:
        return None


def _matches(item: dict, prefix: str, wanted: str) -> bool:
    own = canon(item.get(f"{prefix}FLIGHT_NUMBER") or "")
    share = canon(item.get(f"{prefix}FLIGHT_NUMBER_K1") or "")
    return wanted in (own, share)


def _via(item: dict, final_key: str, stop_key: str, stop_name_key: str) -> str:
    """Промежуточная посадка: NEXT ≠ DESTINATION (вылет) / PREVIOUS ≠ ORIGIN (прилёт)."""
    final, stop = item.get(final_key) or "", item.get(stop_key) or ""
    if not stop or stop == final:
        return ""
    return " ".join(filter(None, (stop, item.get(stop_name_key) or "")))


def build_departure(it: dict, wanted: str) -> FlightSnapshot:
    dep = Leg(plan=_iso(it.get("OD_STD")), est=_iso(it.get("OD_ETD")), fact=_iso(it.get("OD_ATD")))
    arr = Leg(plan=_iso(it.get("OD_RAP_CODE_DESTINATION_STA")))
    return FlightSnapshot(
        flight=wanted, date=(dep.plan or "")[:10], direction="departure",
        origin_iata="LED", dest_iata=it.get("OD_RAP_CODE_DESTINATION") or "",
        status=it.get("OD_STATUS_RU") or "",
        departure=dep, arrival=arr,
        checkin=Checkin(desks=it.get("OD_COUNTERS") or "",
                        start_plan=_iso(it.get("OD_COUNTER_BEGIN_PLAN")),
                        finish_plan=_iso(it.get("OD_COUNTER_END_PLAN")),
                        start_fact=_iso(it.get("OD_COUNTER_BEGIN_ACTUAL")),
                        finish_fact=_iso(it.get("OD_COUNTER_END_ACTUAL"))),
        boarding=Boarding(start_fact=_iso(it.get("OD_BOARDING_BEGIN_ACTUAL")),
                          finish_fact=_iso(it.get("OD_BOARDING_END_ACTUAL")),
                          start_min=_minutes_before(it.get("OD_BOARDING_BEGIN_PLAN"), it.get("OD_STD"))),
        airline=it.get("OD_RAL_NAME_RUS") or "", origin_city="Санкт-Петербург",
        dest_city=it.get("OD_RAP_DESTINATION_NAME_RU") or "",
        via=_via(it, "OD_RAP_CODE_DESTINATION", "OD_RAP_CODE_NEXT", "OD_RAP_NEXT_NAME_RU"),
        aircraft=it.get("OD_RACT_ICAO_CODE") or "", terminal=it.get("OD_RTRM_CODE") or "",
        gate=it.get("OD_GATES") or "", source="pulkovoairport.ru", key=str(it.get("OD_ID") or ""),
    )


def build_arrival(it: dict, wanted: str) -> FlightSnapshot:
    arr = Leg(plan=_iso(it.get("OA_STA")), est=_iso(it.get("OA_ETA")), fact=_iso(it.get("OA_ATA")))
    dep = Leg(plan=_iso(it.get("OA_RAP_CODE_ORIGIN_STD")), fact=_iso(it.get("OA_RAP_CODE_ORIGIN_ATD")))
    return FlightSnapshot(
        flight=wanted, date=(arr.plan or "")[:10], direction="arrival",
        origin_iata=it.get("OA_RAP_CODE_ORIGIN") or "", dest_iata="LED",
        status=it.get("OA_STATUS_RU") or "",
        departure=dep, arrival=arr,
        airline=it.get("OA_RAL_NAME_RUS") or "",
        origin_city=it.get("OA_RAP_ORIGIN_NAME_RU") or "", dest_city="Санкт-Петербург",
        via=_via(it, "OA_RAP_CODE_ORIGIN", "OA_RAP_CODE_PREVIOUS", "OA_RAP_PREVIOUS_NAME_RU"),
        aircraft=it.get("OA_RACT_ICAO_CODE") or "", terminal=it.get("OA_RTRM_CODE") or "",
        baggage_belt=it.get("OA_BAGGAGEBELTS") or "",
        source="pulkovoairport.ru", key=str(it.get("OA_ID") or ""),
    )


def _minutes_before(boarding_plan: Optional[str], std: Optional[str]) -> str:
    a, b = _iso(boarding_plan), _iso(std)
    if not a or not b:
        return ""
    m = int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() // 60)
    return str(m) if m > 0 else ""


def parse(payload: list, flight_no: str, direction: str) -> List[FlightSnapshot]:
    """Чистый парсер ответа API — без сети."""
    wanted = canon(flight_no)
    if not wanted or not isinstance(payload, list):
        return []
    prefix = "OD_" if direction == "departure" else "OA_"
    build = build_departure if direction == "departure" else build_arrival
    return [build(it, wanted) for it in payload if isinstance(it, dict) and _matches(it, prefix, wanted)]


class LedSource(FlightSource):
    name = "pulkovoairport.ru"

    def __init__(self, scrapedo_api_key: str = "", timeout: float = 20.0):
        self.fetcher = Fetcher(scrapedo_api_key, timeout=timeout)

    async def fetch(
        self,
        flight_no: str,
        date: Optional[str] = None,
        direction: str = "departure",
    ) -> List[FlightSnapshot]:
        import json

        wanted = canon(flight_no)
        if not wanted:
            return []
        digits = re.sub(r"\D", "", wanted[2:])
        today = datetime.now(MSK).date()
        # `when` — конкретный день; без даты берём вчера/сегодня/завтра, как табло.
        if date:
            whens = [max(-1, min(1, (datetime.fromisoformat(date).date() - today).days))]
        else:
            whens = [0, 1, -1]
        out: List[FlightSnapshot] = []
        for when in whens:
            text = await self.fetcher.get(BASE_URL, {
                "type": direction, "when": str(when), "search": digits})
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            out += [s for s in parse(payload, wanted, direction) if not date or s.date == date]
        return out
