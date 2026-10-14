"""Реестр источников и проба «номер рейса → аэропорт/снимок».

Пока набор табло узкий (Москва), резолвер — это опрос настроенных источников:
табло само по номеру отдаёт маршрут. Оба направления пробуем, т.к. заранее не
знаем, вылетает рейс из нашего аэропорта или прилетает. AirLabs и AeroDataBox
подключены сюда же для рейсов вне наших табло.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from flight_bot import airports, health, storage
from flight_bot.config import Settings
from flight_bot.http import describe
from flight_bot.models import FlightRoute, FlightSnapshot
from flight_bot.sources.aerodatabox import HORIZON_DAYS, AeroDataBoxSource
from flight_bot.sources.airlabs import AirlabsSource
from flight_bot.sources.bah import BahSource
from flight_bot.sources.base import AirportResolver, FlightSource
from flight_bot.sources.dme import DmeSource
from flight_bot.sources.dxb import DxbSource
from flight_bot.sources.led import LedSource
from flight_bot.sources.svo import SvoSource
from flight_bot.sources.vko import VkoSource

DIRECTIONS = ("departure", "arrival")


class Found(List[FlightSnapshot]):
    """Результат поиска: снимки плюс имена источников, которые упали или не
    уложились в таймаут, — «рейса нет» и «табло не отвечают» для пользователя
    разные ответы."""

    down: set

    def __init__(self, snaps=(), down=()):
        super().__init__(snaps)
        self.down = set(down)

log = logging.getLogger("flight_bot.registry")

# Настроенные табло. Добавление источника = одна строка здесь.
SOURCES: List[FlightSource] = [SvoSource(), DmeSource(), LedSource(), VkoSource(),
                               DxbSource(), BahSource()]

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
BOARD_IATA = {"SVO", "DME", "VKO", "LED", "DXB", "BAH"}

# Поиск в чате: табло медленнее этого считаем пустым, а не ждём его минутами
# (Пулково через scrape.do — около минуты). Опрос подписок без лимита.
PROBE_TIMEOUT_SEC = 15.0
# Ответ источника на (рейс, дата, направление) общий для всех подписок и поиска:
# 10 подписок на один рейс = один запрос за цикл. Одновременные — склеиваем.
FETCH_TTL_SEC = 60.0
_clock = time.monotonic
_fetch_cache: Dict[tuple, Tuple[float, List[FlightSnapshot]]] = {}
_fetch_inflight: Dict[tuple, "asyncio.Future"] = {}


async def cached_fetch(src: FlightSource, flight_no: str, date: Optional[str],
                       direction: str) -> List[FlightSnapshot]:
    """src.fetch с кэшем FETCH_TTL_SEC и склейкой одновременных запросов.
    Ошибку не запоминаем: следующий вызов спросит источник снова."""
    key = (src.name, flight_no, date, direction)
    hit = _fetch_cache.get(key)
    if hit and _clock() - hit[0] < FETCH_TTL_SEC:
        return hit[1]
    task = _fetch_inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(src.fetch(flight_no, date, direction))
        _fetch_inflight[key] = task

        def done(t: "asyncio.Future") -> None:
            _fetch_inflight.pop(key, None)
            if t.cancelled():
                return
            if t.exception() is not None:
                health.HEALTH.fail(src.name, t.exception())
                return
            health.HEALTH.ok(src.name)
            now = _clock()
            for k in [k for k, (ts, _) in _fetch_cache.items() if now - ts >= FETCH_TTL_SEC]:
                del _fetch_cache[k]
            _fetch_cache[key] = (now, t.result())
        task.add_done_callback(done)
    # shield: таймаут поиска не должен отменить запрос, которого ждёт и опрос.
    return await asyncio.shield(task)


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
        SCHEDULE = AeroDataBoxSource(settings.aerodatabox_api_key, proxy=settings.schedule_proxy_url)
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
            except Exception as exc:  # noqa: BLE001 — обогащение не должно ломать основной путь
                health.HEALTH.fail(airlabs.name, exc)   # ошибку не запоминаем: спросим в следующий раз
        o, d = _iata_cache.get(s.flight, ("", ""))
        known_ok = (s.origin_iata and s.origin_iata == o) or (s.dest_iata and s.dest_iata == d)
        if known_ok:
            s = _fill_places(dataclasses.replace(s, origin_iata=s.origin_iata or o,
                                                 dest_iata=s.dest_iata or d))
        out.append(s)
    return out


async def _ask(src: FlightSource, flight_no: str, date: Optional[str],
               direction: str) -> Optional[List[FlightSnapshot]]:
    """Снимки источника для поиска; None — источник упал или не уложился в таймаут."""
    try:
        return await asyncio.wait_for(cached_fetch(src, flight_no, date, direction),
                                      PROBE_TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001 — источник отвалился или тормозит: для поиска пусто
        log.warning("проба %s %s %s: %s", src.name, flight_no, direction, describe(exc))
        return None


async def probe(flight_no: str, date: Optional[str] = None) -> Found:
    """Найти рейс на настроенных табло. Табло опрашиваем разом (время поиска —
    самое медленное табло, а не сумма), результат берём по порядку SOURCES.
    Платные источники с ключом (AirLabs) — только если все табло пусты, и одним
    запросом: к аэропорту они не привязаны, направление им не важно."""
    boards = [s for s in SOURCES if not isinstance(s, AirlabsSource)]
    keyed = [s for s in SOURCES if isinstance(s, AirlabsSource)]
    down = set()
    for tier, dirs in ((boards, DIRECTIONS), (keyed, DIRECTIONS[:1])):
        pairs = [(src, d) for src in tier for d in dirs]
        found = await asyncio.gather(*(_ask(src, flight_no, date, d) for src, d in pairs))
        down |= {src.name for (src, _), snaps in zip(pairs, found, strict=True) if snaps is None}
        for snaps in found:
            if snaps:
                return Found(await enrich_iata(snaps), down)
    return Found((), down)


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


def _spend_budget() -> bool:
    """Попытку списываем до запроса: тарифицирует ли RapidAPI ошибки — не знаем.
    Не смогли списать (нет базы, диск, блокировка) — не платим."""
    if _budget_conn is None:
        return False
    try:
        return storage.spend_schedule_call(_budget_conn, SCHEDULE_BUDGET,
                                           SCHEDULE_BUDGET_WINDOW_SEC)
    except sqlite3.Error:
        log.exception("бюджет расписания не списать — без платного запроса")
        return False


async def _load(flight_no: str) -> List[FlightSnapshot]:
    if not _spend_budget():
        log.warning("нет бюджета расписания (%d за 31 день): %s только по табло",
                    SCHEDULE_BUDGET, flight_no)
        return []
    try:
        snaps = [_fill_places(s) for s in await SCHEDULE.fetch(flight_no)]
    except Exception as exc:  # noqa: BLE001 — лимит/сеть: остаёмся с табло
        log.warning("расписание %s недоступно", flight_no, exc_info=True)
        health.HEALTH.fail(SCHEDULE.name, exc)
        snaps, ttl = [], SCHEDULE_FAIL_TTL_SEC
    else:
        health.HEALTH.ok(SCHEDULE.name)
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


async def search(flight_no: str, today: Optional[date] = None) -> Found:
    """Даты для подписки: сегодня…+HORIZON_DAYS. Табло богаче (стойки, выход),
    поэтому его снимок на дату главнее; расписание добирает даты, которых на
    табло ещё нет (табло держит только вчера/сегодня/завтра)."""
    today = today or datetime.now(MSK).date()
    last = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    found, planned = await asyncio.gather(probe(flight_no), schedule(flight_no))
    boards = upcoming(found, today)
    have = {s.date for s in boards}
    extra = [s for s in upcoming([_board_side(s) for s in planned], today)
             if s.date not in have]
    return Found(sorted((s for s in boards + extra if s.date <= last), key=lambda s: s.date),
                 found.down)


async def fetch_for(flight_no: str, date: str, direction: str,
                    prefer: Optional[str] = None,
                    key: Optional[str] = None) -> Optional[FlightSnapshot]:
    """Снимок конкретного рейса на дату и направление — для опроса подписки.

    prefer — имя источника, где рейс нашли при подписке: его опрашиваем первым,
    остальные — только если он рейс потерял. Иначе с ростом числа табло каждый
    опрос ходил бы по всем подряд.
    key — id записи у источника из прошлого снимка: на одну дату у табло бывает
    две записи одного номера (DXB), опрос не должен прыгать между ними.
    """
    ordered = sorted(SOURCES, key=lambda s: 0 if s.name == prefer else 1)
    for src in ordered:
        try:
            snaps = await cached_fetch(src, flight_no, date, direction)
        except Exception as exc:  # noqa: BLE001 — источник отвалился, пробуем следующий
            log.warning("опрос %s %s %s %s: %s", src.name, flight_no, date, direction, describe(exc))
            continue
        same_day = [s for s in snaps if s.date == date]
        if same_day:
            pinned = next((s for s in same_day if key and s.key == key), same_day[0])
            return (await enrich_iata([pinned]))[0]
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
