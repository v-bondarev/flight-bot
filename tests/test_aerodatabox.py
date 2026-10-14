"""Парсер AeroDataBox на реальном ответе /flights/number/EK770/{с}/{по}."""
import json
from pathlib import Path

from flight_bot.sources import aerodatabox

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "adb_ek770.json").read_text())


def test_parse_one_snapshot_per_date_with_route_and_plan():
    snaps = aerodatabox.parse(PAYLOAD, "ek 770")
    assert [s.date for s in snaps] == ["2026-10-14", "2026-10-15", "2026-10-16"]
    s = snaps[2]
    assert (s.flight, s.origin_iata, s.dest_iata) == ("EK770", "DXB", "CPT")
    assert s.departure.plan == "2026-10-16T09:10:00+04:00"   # локальное время с офсетом
    assert s.arrival.plan == "2026-10-16T16:40:00+02:00"
    assert (s.airline, s.aircraft, s.terminal, s.gate) == ("Emirates", "Boeing 777-300ER", "3", "")
    assert s.status == "Ожидается"
    assert (s.source, s.key, s.direction) == ("aerodatabox.com", "EK770:2026-10-16", "departure")


def test_parse_live_flight_takes_off_time_as_fact_and_predicted_arrival_as_estimate():
    s = aerodatabox.parse(PAYLOAD, "EK770")[0]
    assert s.status == "В воздухе"
    assert s.departure.fact == "2026-10-14T09:41:00+04:00"
    assert s.departure.est is None                 # revised == scheduled — не оценка
    assert s.arrival.est == "2026-10-14T17:09:00+02:00"
    assert s.arrival.fact is None
    assert (s.gate, s.baggage_belt) == ("A6b", "2")


def test_parse_uncertain_cancel_is_not_cancel_and_arrived_never_fakes_fact_from_plan():
    from flight_bot.models import is_cancelled
    uncertain = [dict(PAYLOAD[2], status="CanceledUncertain")]
    assert not is_cancelled(aerodatabox.parse(uncertain, "EK770")[0])
    assert is_cancelled(aerodatabox.parse([dict(PAYLOAD[2], status="Canceled")], "EK770")[0])
    bare = {k: v for k, v in PAYLOAD[2]["arrival"].items()
            if k not in ("revisedTime", "predictedTime", "runwayTime")}
    arrived = aerodatabox.parse([dict(PAYLOAD[2], status="Arrived", arrival=bare)], "EK770")[0]
    assert arrived.arrival.fact is None                  # ни runwayTime, ни оценки — факта нет


def test_parse_skips_other_numbers_and_empty_payload():
    assert aerodatabox.parse(PAYLOAD, "EK771") == []
    assert aerodatabox.parse([], "EK770") == []


def test_fetch_goes_through_schedule_proxy(monkeypatch):
    import asyncio

    import httpx
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            seen["url"] = url
            return httpx.Response(200, json=PAYLOAD, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    src = aerodatabox.AeroDataBoxSource("k", proxy="socks5://127.0.0.1:1081")
    snaps = asyncio.run(src.fetch("EK770", "2026-10-16"))
    assert seen["proxy"] == "socks5://127.0.0.1:1081"
    assert seen["url"].endswith("/flights/number/EK770/2026-10-16/2026-10-16")
    assert len(snaps) == 3
