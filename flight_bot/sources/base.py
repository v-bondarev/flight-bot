"""Контракт источника статуса рейса и резолвера «номер → аэропорт».

Источники подключаются как плагины: бизнес-логика (подписка, диффер, пуш)
не знает, откуда пришли данные — с табло аэропорта, из Yandex или AeroDataBox.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from flight_bot.models import FlightRoute, FlightSnapshot


class FlightSource(ABC):
    """Источник, умеющий отдать снимок(и) рейса по номеру и дате."""

    name: str = "base"

    @abstractmethod
    async def fetch(
        self,
        flight_no: str,
        date: Optional[str] = None,
        direction: str = "departure",
    ) -> List[FlightSnapshot]:
        """Снимки рейса `flight_no` (например "SU2128").

        date: YYYY-MM-DD или None (тогда — что источник держит на табло).
        Возвращает список: у номера бывает несколько дат/сегментов сразу.
        Пустой список — источник рейс не знает (не ошибка).
        """
        ...


class AirportResolver(ABC):
    """«Номер рейса → аэропорты». Нужен, чтобы понять, чьё табло опрашивать.

    Табло аэропорта само по номеру возвращает маршрут, поэтому для узкого
    набора (Москва) резолвером служит опрос настроенных источников. Для
    произвольного рейса нужен реестр расписаний (Yandex — бесплатно,
    AeroDataBox — план B).
    """

    name: str = "base"

    @abstractmethod
    async def resolve(
        self, flight_no: str, date: Optional[str] = None
    ) -> List[FlightRoute]:
        """Маршруты рейса. Пустой список — резолвер рейс не нашёл."""
        ...
