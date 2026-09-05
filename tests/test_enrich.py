"""IATA-обогащение через AirLabs для табло без кодов (DME) и лимит scrape.do."""
import asyncio

from flight_bot import http, registry
from flight_bot.http import SCRAPEDO_URL, Fetcher
from flight_bot.models import FlightSnapshot
from flight_bot.sources.airlabs import AirlabsSource


def _snap(**kw):
    base = dict(flight="S71055", date="2026-09-05", direction="departure",
                origin_iata="DME", dest_iata="", dest_city="Казань", status="")
    base.update(kw)
    return FlightSnapshot(**base)


class _Airlabs(AirlabsSource):
    def __init__(self, route):
        super().__init__("k")
        self.route, self.calls = route, 0

    async def fetch(self, flight_no, date=None, direction="departure"):
        self.calls += 1
        o, d = self.route
        return [_snap(origin_iata=o, dest_iata=d, source="airlabs.co")]


def test_enrich_fills_missing_iata_and_caches(monkeypatch):
    al = _Airlabs(("DME", "KZN"))
    monkeypatch.setattr(registry, "SOURCES", [al])
    registry._iata_cache.clear()
    out = asyncio.run(registry.enrich_iata([_snap(), _snap(date="2026-09-06")]))
    assert [s.dest_iata for s in out] == ["KZN", "KZN"]
    assert out[0].dest_city == "Казань"          # город не трогаем
    assert al.calls == 1                          # второй снимок — из кэша


def test_enrich_distrusts_other_leg(monkeypatch):
    al = _Airlabs(("SVO", "KZN"))                 # AirLabs знает другое плечо
    monkeypatch.setattr(registry, "SOURCES", [al])
    registry._iata_cache.clear()
    out = asyncio.run(registry.enrich_iata([_snap()]))
    assert out[0].dest_iata == ""                 # известная сторона не совпала — не доверяем


def test_enrich_noop_without_airlabs(monkeypatch):
    monkeypatch.setattr(registry, "SOURCES", [])
    assert asyncio.run(registry.enrich_iata([_snap()]))[0].dest_iata == ""


def test_scrapedo_concurrency_limited(monkeypatch):
    monkeypatch.setattr(http, "SCRAPEDO_LIMIT", 1)
    http._sems.clear()
    state = {"now": 0, "max": 0}

    class Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, params=None, timeout=None):
            if not url.startswith(SCRAPEDO_URL):
                import httpx
                raise httpx.ConnectTimeout("blocked")
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            await asyncio.sleep(0.01)
            state["now"] -= 1

            class R:
                text, status_code = "ok", 200
                def raise_for_status(self): pass
            return R()

    f = Fetcher("KEY", client_factory=lambda **kw: Client())

    async def run():
        await asyncio.gather(*(f.get("https://www.dme.ru/x") for _ in range(4)))
    asyncio.run(run())
    assert state["max"] == 1
