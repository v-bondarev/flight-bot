"""Справочник аэропортов и заполнение «код + город» в снимках."""
import asyncio

from flight_bot import airports, registry
from flight_bot.models import FlightSnapshot


def test_lookup_both_ways():
    assert airports.city("KZN") == "Казань" and airports.city("kzn") == "Казань"
    assert airports.iata("Казань") == "KZN" and airports.iata(" казань ") == "KZN"
    assert airports.iata("Москва") == "SVO"                    # главный код города
    assert airports.city("XXX") is None and airports.iata("Нигде") is None


def _snap(**kw):
    base = dict(flight="S71055", date="2026-09-05", direction="departure",
                origin_iata="DME", dest_iata="", origin_city="Москва", dest_city="Казань", status="")
    base.update(kw)
    return FlightSnapshot(**base)


def test_fill_places_without_airlabs(monkeypatch):
    monkeypatch.setattr(registry, "SOURCES", [])
    out = asyncio.run(registry.enrich_iata([_snap()]))[0]
    assert (out.dest_iata, out.dest_city) == ("KZN", "Казань")           # код по городу
    al = asyncio.run(registry.enrich_iata([_snap(origin_iata="SVO", origin_city="",
                                                dest_iata="AYT", dest_city="")]))[0]
    assert (al.origin_city, al.dest_city) == ("Москва", "Анталья")      # город по коду (AirLabs-карточки)
