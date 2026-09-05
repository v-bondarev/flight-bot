"""Реестр источников и проба «номер рейса → аэропорт/снимок».

Пока набор табло узкий (Москва), резолвер — это опрос настроенных источников:
табло само по номеру отдаёт маршрут. Оба направления пробуем, т.к. заранее не
знаем, вылетает рейс из нашего аэропорта или прилетает. Yandex/AeroDataBox
подключим сюда же, когда набор перерастёт Москву.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from flight_bot import airports
from flight_bot.config import Settings
from flight_bot.models import FlightRoute, FlightSnapshot
from flight_bot.sources.airlabs import AirlabsSource
from flight_bot.sources.base import AirportResolver, FlightSource
from flight_bot.sources.dme import DmeSource
from flight_bot.sources.led import LedSource
from flight_bot.sources.svo import SvoSource
from flight_bot.sources.vko import VkoSource

DIRECTIONS = ("departure", "arrival")

# Настроенные табло. Добавление источника = одна строка здесь.
SOURCES: List[FlightSource] = [SvoSource(), DmeSource(), LedSource(), VkoSource()]


def configure(settings: Settings) -> None:
    """Источники с ключами — в конец списка: табло богаче, они первые.
    Табло, ходящие через Fetcher, получают ключ scrape.do как запасной транспорт."""
    from flight_bot import http
    http.SCRAPEDO_LIMIT = settings.scrapedo_concurrency
    from flight_bot.http import Renderer
    for src in SOURCES:
        if hasattr(src, "fetcher"):
            src.fetcher.api_key = settings.scrapedo_api_key
            src.fetcher.ru_proxy_url = settings.ru_proxy_url
        if hasattr(src, "renderer"):
            src.renderer = Renderer(settings.render_url)
    if settings.airlabs_api_key:
        SOURCES.append(AirlabsSource(settings.airlabs_api_key))


# Маршрут по номеру у AirLabs: (origin, dest). Кэш на процесс — маршрут у
# номера стабилен, а платить вызовом за каждый опрос незачем.
_iata_cache: Dict[str, Tuple[str, str]] = {}


def _fill_places(s: FlightSnapshot) -> FlightSnapshot:
    """Код по городу и город по коду из справочника — везде «KZN Казань»."""
    return dataclasses.replace(
        s,
        origin_iata=s.origin_iata or airports.iata(s.origin_city) or "",
        dest_iata=s.dest_iata or airports.iata(s.dest_city) or "",
        origin_city=s.origin_city or airports.city(s.origin_iata) or "",
        dest_city=s.dest_city or airports.city(s.dest_iata) or "",
    )


async def enrich_iata(snaps: List[FlightSnapshot]) -> List[FlightSnapshot]:
    """Дополнить код/город: сперва справочник, затем — для кода — AirLabs.

    AirLabs доверяем только если известная нам сторона совпала (для вылета из
    DME он тоже должен показать DME отправлением) — иначе это другое плечо.
    """
    snaps = [_fill_places(s) for s in snaps]
    airlabs = next((s for s in SOURCES if isinstance(s, AirlabsSource)), None)
    if airlabs is None:
        return snaps
    out = []
    for s in snaps:
        if s.origin_iata and s.dest_iata:
            out.append(s)
            continue
        if s.flight not in _iata_cache:
            try:
                al = await airlabs.fetch(s.flight)
                _iata_cache[s.flight] = (al[0].origin_iata, al[0].dest_iata) if al else ("", "")
            except Exception:  # noqa: BLE001 — обогащение не должно ломать основной путь
                _iata_cache[s.flight] = ("", "")
        o, d = _iata_cache[s.flight]
        known_ok = (s.origin_iata and s.origin_iata == o) or (s.dest_iata and s.dest_iata == d)
        if known_ok:
            s = _fill_places(dataclasses.replace(s, origin_iata=s.origin_iata or o,
                                                 dest_iata=s.dest_iata or d))
        out.append(s)
    return out


async def probe(flight_no: str, date: Optional[str] = None) -> List[FlightSnapshot]:
    """Найти рейс на любом настроенном табло. Возвращает первые непустые снимки."""
    for src in SOURCES:
        for direction in DIRECTIONS:
            try:
                snaps = await src.fetch(flight_no, date, direction)
            except Exception:  # noqa: BLE001 — источник мог отвалиться, пробуем следующий
                continue
            if snaps:
                return await enrich_iata(snaps)
    return []


MSK = timezone(timedelta(hours=3))   # дата рейса на табло — московская


def upcoming(snaps: List[FlightSnapshot], today: Optional[date] = None) -> List[FlightSnapshot]:
    """Первый снимок на каждую дату, не раньше сегодняшней: табло держит и
    вчерашние рейсы, а подписываться на прошедшее незачем."""
    today = today or datetime.now(MSK).date()
    seen = {}
    for s in snaps:
        if s.date >= today.isoformat():
            seen.setdefault(s.date, s)
    return list(seen.values())


async def fetch_for(flight_no: str, date: str, direction: str,
                    prefer: Optional[str] = None) -> Optional[FlightSnapshot]:
    """Снимок конкретного рейса на дату и направление — для опроса подписки.

    prefer — имя источника, где рейс нашли при подписке: его опрашиваем первым,
    остальные — только если он рейс потерял. Иначе с ростом числа табло каждый
    опрос ходил бы по всем подряд.
    """
    ordered = sorted(SOURCES, key=lambda s: 0 if s.name == prefer else 1)
    for src in ordered:
        try:
            snaps = await src.fetch(flight_no, date, direction)
        except Exception:  # noqa: BLE001
            continue
        for s in snaps:
            if s.date == date:
                return (await enrich_iata([s]))[0]
    return None


class BoardProbeResolver(AirportResolver):
    """«Номер → аэропорт» через опрос настроенных табло (бесплатно, без ключа)."""

    name = "board-probe"

    async def resolve(self, flight_no: str, date: Optional[str] = None) -> List[FlightRoute]:
        return [
            FlightRoute(flight=s.flight, date=s.date, origin_iata=s.origin_iata,
                        dest_iata=s.dest_iata, airline=s.airline)
            for s in await probe(flight_no, date)
        ]
