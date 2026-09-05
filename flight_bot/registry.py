"""Реестр источников и проба «номер рейса → аэропорт/снимок».

Пока набор табло узкий (Москва), резолвер — это опрос настроенных источников:
табло само по номеру отдаёт маршрут. Оба направления пробуем, т.к. заранее не
знаем, вылетает рейс из нашего аэропорта или прилетает. Yandex/AeroDataBox
подключим сюда же, когда набор перерастёт Москву.
"""
from __future__ import annotations

from typing import List, Optional

from flight_bot.config import Settings
from flight_bot.models import FlightRoute, FlightSnapshot
from flight_bot.sources.airlabs import AirlabsSource
from flight_bot.sources.base import AirportResolver, FlightSource
from flight_bot.sources.svo import SvoSource

DIRECTIONS = ("departure", "arrival")

# Настроенные табло. Добавление источника = одна строка здесь.
SOURCES: List[FlightSource] = [SvoSource()]


def configure(settings: Settings) -> None:
    """Источники с ключами — в конец списка: табло богаче, они первые."""
    if settings.airlabs_api_key:
        SOURCES.append(AirlabsSource(settings.airlabs_api_key))


async def probe(flight_no: str, date: Optional[str] = None) -> List[FlightSnapshot]:
    """Найти рейс на любом настроенном табло. Возвращает первые непустые снимки."""
    for src in SOURCES:
        for direction in DIRECTIONS:
            try:
                snaps = await src.fetch(flight_no, date, direction)
            except Exception:  # noqa: BLE001 — источник мог отвалиться, пробуем следующий
                continue
            if snaps:
                return snaps
    return []


async def fetch_for(flight_no: str, date: str, direction: str) -> Optional[FlightSnapshot]:
    """Снимок конкретного рейса на дату и направление — для опроса подписки."""
    for src in SOURCES:
        try:
            snaps = await src.fetch(flight_no, date, direction)
        except Exception:  # noqa: BLE001
            continue
        for s in snaps:
            if s.date == date:
                return s
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
