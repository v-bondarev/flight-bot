"""Источник: онлайн-табло Шереметьево (SVO).

Публичный Bitrix-JSON под табло:
    GET https://www.svo.aero/bitrix/timetable/?search=<номер>&direction=<dir>&perPage=50
    → {"items": [ {co:{code}, flt, dat, mar1, mar2, t_st/t_et/..., term, gate_id} ]}

Ключ не нужен, антибота нет. Поиск на стороне табло нестрогий — фильтруем сами.
Маппинг план/оценка/факт по сторонам выверен на боевом flight_watch.py сайта.
"""
from __future__ import annotations

import urllib.parse
from typing import List, Optional

from flight_bot.models import Boarding, Checkin, FlightSnapshot, Leg
from flight_bot.sources.base import FlightSource

BASE_URL = "https://www.svo.aero/bitrix/timetable/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _port(node: Optional[dict]) -> dict:
    node = node or {}
    return {
        "iata": (node.get("iata") or "").strip(),
        "city": (node.get("city") or "").strip(),
    }


def _shape(item: dict, direction: str) -> FlightSnapshot:
    """Один item табло → нормализованный снапшот.

    mar1 у табло — всегда сторона SVO, mar2…mar5 — остальная цепочка от SVO
    наружу: у рейса с промежуточной посадкой (GF013 SVO→TBS→BAH) mar2 — это
    Тбилиси, а конечная точка — последняя непустая. Что origin, а что dest,
    зависит от направления; так же переставляются триплеты времён (своя
    сторона t_st/t_et vs встречная t_st_mar/marArrivalEt — они уже про
    конечную точку).
    """
    co = item.get("co") or {}
    svo = _port(item.get("mar1"))
    chain = [_port(item.get(f"mar{i}")) for i in range(2, 6)]
    chain = [p for p in chain if p["iata"]]
    other = chain[-1] if chain else _port(None)
    via = ", ".join(" ".join(filter(None, (p["iata"], p["city"]))) for p in chain[:-1])
    gate = (item.get("gate_id") or "").strip()
    old_gate = (item.get("old_gate_id") or "").strip()

    own = Leg(plan=item.get("t_st"), est=item.get("t_et"),
              fact=item.get("t_otpr") if direction == "departure" else item.get("t_at"))
    mar = Leg(plan=item.get("t_st_mar"), est=item.get("marArrivalEt"),
              fact=item.get("t_at_mar"))

    if direction == "arrival":
        origin, dest = other, svo
        departure, arrival = mar, own
    else:
        origin, dest = svo, other
        departure, arrival = own, mar

    return FlightSnapshot(
        flight=f"{co.get('code', '')}{item.get('flt', '')}".strip(),
        date=(item.get("dat") or "")[:10],
        direction=direction,
        origin_iata=origin["iata"],
        dest_iata=dest["iata"],
        origin_city=origin["city"],
        dest_city=dest["city"],
        via=via,
        status=(item.get("vip_status_rus") or item.get("vip_status") or "").strip(),
        departure=departure,
        arrival=arrival,
        checkin=Checkin(
            desks=(item.get("chin_id") or "").strip(),
            start_plan=item.get("estimated_chin_start"),
            finish_plan=item.get("estimated_chin_finish"),
            start_fact=item.get("t_chin_start"),
            finish_fact=item.get("t_chin_finish"),
        ),
        boarding=Boarding(
            start_fact=item.get("t_boarding_start"),
            finish_fact=item.get("t_bording_finish"),   # sic: опечатка в API табло
            start_min=str(item.get("planed_board_start") or ""),
        ),
        airline=(co.get("name") or "").strip(),
        aircraft=(item.get("aircraft_type_name") or "").strip(),
        terminal=(item.get("term") or "").strip(),
        gate=gate,
        gate_prev=old_gate if old_gate and old_gate != gate else "",
        gate_terminal=(item.get("term_gate") or "").strip(),
        baggage_belt=(item.get("bbel_id") or "").strip(),
        source="svo.aero",
        key=str(item.get("i_id") or item.get("id") or ""),
    )


def parse(payload: dict, flight_no: str, direction: str = "departure") -> List[FlightSnapshot]:
    """Чистый парсер ответа табло — без сети, отсюда тестируемость."""
    wanted = flight_no.replace(" ", "").upper()
    out: List[FlightSnapshot] = []
    for item in payload.get("items") or []:
        code = (item.get("co") or {}).get("code", "")
        if f"{code}{item.get('flt', '')}".upper() == wanted:
            out.append(_shape(item, direction))
    return out


class SvoSource(FlightSource):
    name = "svo.aero"

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    async def fetch(
        self,
        flight_no: str,
        date: Optional[str] = None,
        direction: str = "departure",
    ) -> List[FlightSnapshot]:
        import httpx  # ленивый импорт: тесты парсера не тянут сеть

        query = urllib.parse.urlencode(
            {"search": flight_no, "direction": direction, "perPage": "50"}
        )
        headers = {"Accept": "application/json", "User-Agent": UA}
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.get(f"{BASE_URL}?{query}")
            resp.raise_for_status()
            payload = resp.json()
        items = parse(payload, flight_no, direction)
        # Табло держит несколько суток; если дату задали — оставляем только её.
        return [s for s in items if not date or s.date == date]
