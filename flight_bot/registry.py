"""Реестр источников и проба «номер рейса → аэропорт/снимок».

Пока набор табло узкий (Москва), резолвер — это опрос настроенных источников:
табло само по номеру отдаёт маршрут. Оба направления пробуем, т.к. заранее не
знаем, вылетает рейс из нашего аэропорта или прилетает. Yandex/AeroDataBox
подключим сюда же, когда набор перерастёт Москву.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from flight_bot import airports, storage
from flight_bot.config import Settings
from flight_bot.models import FlightRoute, FlightSnapshot
from flight_bot.sources.aerodatabox import HORIZON_DAYS, AeroDataBoxSource
from flight_bot.sources.airlabs import AirlabsSource
from flight_bot.sources.base import AirportResolver, FlightSource
from flight_bot.sources.dme import DmeSource
from flight_bot.sources.led import LedSource
from flight_bot.sources.svo import SvoSource
from flight_bot.sources.vko import VkoSource

DIRECTIONS = ("departure", "arrival")

log = logging.getLogger("flight_bot.registry")

# Настроенные табло. Добавление источника = одна строка здесь.
SOURCES: List[FlightSource] = [SvoSource(), DmeSource(), LedSource(), VkoSource()]

# Расписание на даты вперёд (AeroDataBox). Не в SOURCES: запрос платный, в опрос
# подписок не ходит — только в поиск дат (search), с кэшем на SCHEDULE_TTL_SEC.
SCHEDULE: Optional[FlightSource] = None
SCHEDULE_TTL_SEC = 6 * 3600
SCHEDULE_FAIL_TTL_SEC = 600     # лимит/сеть: не ждать таймаут на каждом поиске
SCHEDULE_CACHE_MAX = 1000
# Бюджет квоты: не больше SCHEDULE_BUDGET платных запросов за скользящие 31 день —
# так любой месячный цикл RapidAPI укладывается в лимит, когда бы он ни сбрасывался.
SCHEDULE_BUDGET = 60
SCHEDULE_BUDGET_WINDOW_SEC = 31 * 86400
_budget_conn: Optional[sqlite3.Connection] = None   # без базы платных запросов нет
# Аэропорты наших табло: рейс сюда (а не отсюда) ищем на табло прилёта.
BOARD_IATA = {"SVO", "DME", "VKO", "LED"}


def configure(settings: Settings, conn: Optional[sqlite3.Connection] = None) -> None:
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
            src.renderer = Renderer(settings.render_url, cache_sec=settings.render_cache_sec)
        if isinstance(src, VkoSource):
            from flight_bot.vko_client import VkoClient
            src.api = VkoClient(ru_proxy_url=settings.ru_proxy_url,
                                cache_sec=settings.render_cache_sec)
    if settings.airlabs_api_key:
        SOURCES.append(AirlabsSource(settings.airlabs_api_key))
    global SCHEDULE, SCHEDULE_BUDGET, _budget_conn
    if settings.aerodatabox_api_key:
        SCHEDULE = AeroDataBoxSource(settings.aerodatabox_api_key)
    SCHEDULE_BUDGET, _budget_conn = settings.aerodatabox_budget, conn


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


# Расписание по номеру: (когда протухнет, снимки на сегодня…+HORIZON_DAYS).
# Ошибка — [] на SCHEDULE_FAIL_TTL_SEC. Порядок ключей — порядок записи.
_schedule_cache: Dict[str, Tuple[float, List[FlightSnapshot]]] = {}
# Идущий запрос по номеру: одновременные поиски ждут его, а не платят дважды.
_schedule_inflight: Dict[str, "asyncio.Task[List[FlightSnapshot]]"] = {}


def _cached(flight_no: str) -> Optional[List[FlightSnapshot]]:
    hit = _schedule_cache.get(flight_no)
    return hit[1] if hit and time.monotonic() < hit[0] else None


def _remember(flight_no: str, snaps: List[FlightSnapshot], ttl: float) -> None:
    """Запись в кэш: протухшие выкидываем, сверх SCHEDULE_CACHE_MAX — самые старые."""
    now = time.monotonic()
    _schedule_cache.pop(flight_no, None)
    for no in [no for no, (expires, _) in _schedule_cache.items() if expires <= now]:
        del _schedule_cache[no]
    while len(_schedule_cache) >= SCHEDULE_CACHE_MAX:
        del _schedule_cache[next(iter(_schedule_cache))]
    _schedule_cache[flight_no] = (now + ttl, snaps)


async def _load(flight_no: str) -> List[FlightSnapshot]:
    # Попытку списываем до запроса: тарифицирует ли RapidAPI ошибки — не знаем.
    if _budget_conn is None or not storage.spend_schedule_call(
            _budget_conn, SCHEDULE_BUDGET, SCHEDULE_BUDGET_WINDOW_SEC):
        log.warning("бюджет расписания исчерпан (%d за 31 день): %s только по табло",
                    SCHEDULE_BUDGET, flight_no)
        return []
    try:
        snaps = [_fill_places(s) for s in await SCHEDULE.fetch(flight_no)]
    except Exception:  # noqa: BLE001 — лимит/сеть: остаёмся с табло
        log.warning("расписание %s недоступно", flight_no, exc_info=True)
        snaps, ttl = [], SCHEDULE_FAIL_TTL_SEC
    else:
        ttl = SCHEDULE_TTL_SEC
    _remember(flight_no, snaps, ttl)
    return snaps


async def schedule(flight_no: str) -> List[FlightSnapshot]:
    """Даты рейса из расписания; ошибка расписания не ломает поиск по табло."""
    if SCHEDULE is None:
        return []
    snaps = _cached(flight_no)
    if snaps is not None:
        return snaps
    task = _schedule_inflight.get(flight_no)
    if task is None:
        task = _schedule_inflight[flight_no] = asyncio.ensure_future(_load(flight_no))
        # Снаружи, а не finally в _load: отменённая до старта задача finally не выполнит.
        task.add_done_callback(lambda _: _schedule_inflight.pop(flight_no, None))
    return await asyncio.shield(task)   # отмена одного ждущего не отменяет общий запрос


def _board_side(s: FlightSnapshot) -> FlightSnapshot:
    """Расписание смотрит со стороны вылета. Рейс к нам — на табло прилёта, а
    там дата рейса — дата прилёта: приводим к тому же, иначе кнопка поведёт
    опрос не на то табло, а дата не совпадёт с табловой."""
    if s.dest_iata in BOARD_IATA and s.origin_iata not in BOARD_IATA and s.arrival.plan:
        day = s.arrival.plan[:10]
        return dataclasses.replace(s, direction="arrival", date=day, key=f"{s.flight}:{day}")
    return s


def scheduled(flight_no: str, date: str) -> Optional[FlightSnapshot]:
    """Снимок из уже взятого при поиске расписания — для карточки при подписке.
    Нового платного запроса не делает; в опрос не идёт: застывший план выдал
    бы себя за живой статус и сбил бы диффер на стыке с табло."""
    return next((s for s in map(_board_side, _cached(flight_no) or []) if s.date == date), None)


async def search(flight_no: str, today: Optional[date] = None) -> List[FlightSnapshot]:
    """Даты для подписки: сегодня…+HORIZON_DAYS. Табло богаче (стойки, выход),
    поэтому его снимок на дату главнее; расписание добирает даты, которых на
    табло ещё нет (табло держит только вчера/сегодня/завтра)."""
    today = today or datetime.now(MSK).date()
    last = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    boards = upcoming(await probe(flight_no), today)
    have = {s.date for s in boards}
    extra = [s for s in upcoming([_board_side(s) for s in await schedule(flight_no)], today)
             if s.date not in have]
    return sorted((s for s in boards + extra if s.date <= last), key=lambda s: s.date)


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
