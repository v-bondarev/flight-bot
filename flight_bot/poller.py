"""Фоновый опрос подписок: снять снимок → сравнить с прошлым → пуш изменений.

send — async callable(chat_id, text): развязывает поллер с aiogram (и делает
его тестируемым без сети). Хранилище синхронное, вызовы копеечные.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Awaitable, Callable

from flight_bot import registry, storage
from flight_bot.config import Settings
from flight_bot.diff import diff_snapshots
from flight_bot.render import update_message

log = logging.getLogger("flight_bot.poller")

Send = Callable[[int, str], Awaitable[None]]


async def poll_once(conn: sqlite3.Connection, send: Send) -> None:
    for row in storage.active_all(conn):
        curr = await registry.fetch_for(row["flight"], row["date"], row["direction"])
        if curr is None:
            continue  # рейса пока нет на табло — не ошибка, ждём следующего круга
        prev = storage.get_last(conn, row["id"])
        storage.set_last(conn, row["id"], curr)
        if prev is not None:
            changes = diff_snapshots(prev, curr)
            if changes:
                await send(row["chat_id"], update_message(curr, changes))
        # Прилетел — рейс завершён, снимаем подписку, чтобы не опрашивать зря.
        if curr.arrival.fact:
            storage.deactivate(conn, row["id"])


async def run(conn: sqlite3.Connection, settings: Settings, send: Send) -> None:
    while True:
        try:
            await poll_once(conn, send)
        except Exception:  # noqa: BLE001 — один сбой не должен ронять цикл
            log.exception("poll_once упал")
        await asyncio.sleep(settings.poll_interval_sec)
