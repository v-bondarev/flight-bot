"""Фоновый опрос подписок: снять снимок → сравнить с прошлым → пуш изменений.

Частота адаптивная, по состоянию рейса: далеко до вылета — раз в час, в окне
у вылета — раз в `near` (POLL_INTERVAL_SEC), с начала посадки — раз в 2 мин.
Цикл тикает раз в минуту и берёт только те подписки, чей `next_at` наступил.

Подписка снимается сама: после прилёта, при отмене и через сутки после даты
рейса (если он так и не появился на табло).

send — async callable(chat_id, text): развязывает поллер с aiogram и делает
его тестируемым без сети; `now` прокидывается по той же причине.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from flight_bot import registry, storage
from flight_bot.config import Settings
from flight_bot.diff import diff_snapshots
from flight_bot.models import FlightSnapshot, is_cancelled
from flight_bot.render import update_message

log = logging.getLogger("flight_bot.poller")

Send = Callable[[int, str], Awaitable[None]]

TICK_SEC = 60
FAR_SEC = 3600
BOARDING_SEC = 120
FAR_HOURS = 6
EXPIRE_DAYS = 1                      # сутки после даты рейса
MSK = timezone(timedelta(hours=3))   # дата рейса на табло — московская


def _dt(iso: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def interval_for(snap: Optional[FlightSnapshot], now: datetime, near: int) -> int:
    if snap is None:
        return near
    if snap.boarding.start_fact and not snap.departure.fact:
        return BOARDING_SEC
    if snap.departure.fact:
        return near                          # в воздухе: следим за прилётом
    dep = _dt(snap.departure.est or snap.departure.plan)
    if dep and dep - now > timedelta(hours=FAR_HOURS):
        return FAR_SEC
    return near


def expired(date: str, now: datetime) -> bool:
    day = datetime.fromisoformat(date).replace(tzinfo=MSK)
    return now >= day + timedelta(days=1 + EXPIRE_DAYS)


async def _fetch(row: sqlite3.Row) -> Optional[FlightSnapshot]:
    try:
        return await registry.fetch_for(row["flight"], row["date"], row["direction"],
                                        prefer=row["source"])
    except Exception:  # noqa: BLE001 — один источник не должен ронять весь круг
        log.exception("опрос %s %s упал", row["flight"], row["date"])
        return None


async def poll_once(conn: sqlite3.Connection, send: Send,
                    now: Optional[datetime] = None, near: int = 300) -> None:
    now = now or datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    rows = []
    for row in storage.due(conn, epoch):
        if expired(row["date"], now):
            storage.deactivate(conn, row["id"])
        else:
            rows.append(row)
    # Опрашиваем параллельно: табло через scrape.do отвечает десятки секунд,
    # по очереди одна медленная подписка тормозила бы все остальные.
    snaps = await asyncio.gather(*(_fetch(r) for r in rows))
    for row, curr in zip(rows, snaps):
        sub_id = row["id"]
        if curr is None:
            # рейса пока нет на табло — не ошибка, ждём следующего круга
            storage.set_next(conn, sub_id, epoch + near)
            continue
        prev = storage.get_last(conn, sub_id)
        storage.set_last(conn, sub_id, curr)
        if prev is not None:
            changes = diff_snapshots(prev, curr)
            if changes:
                await send(row["chat_id"], update_message(curr, changes))
        # Прилетел или отменён — рейс завершён, опрашивать дальше нечего.
        if curr.arrival.fact or is_cancelled(curr):
            storage.deactivate(conn, sub_id)
            continue
        storage.set_next(conn, sub_id, epoch + interval_for(curr, now, near))


async def run(conn: sqlite3.Connection, settings: Settings, send: Send) -> None:
    while True:
        try:
            await poll_once(conn, send, near=settings.poll_interval_sec)
        except Exception:  # noqa: BLE001 — один сбой не должен ронять цикл
            log.exception("poll_once упал")
        await asyncio.sleep(TICK_SEC)
