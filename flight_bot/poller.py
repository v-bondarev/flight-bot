"""Фоновый опрос подписок: снять снимок → сравнить с прошлым → пуш изменений.

Частота адаптивная, по состоянию рейса: далеко до вылета — раз в час, в окне
у вылета — раз в `near` (POLL_INTERVAL_SEC), с начала посадки — раз в 2 мин.
Цикл тикает раз в минуту и берёт только те подписки, чей `next_at` наступил.

Подписка снимается сама: после прилёта, при отмене и через сутки после даты
рейса — об этом последнем случае пользователю приходит пуш, молча снятая
подписка выглядит как потерянная. Рейс, которого на табло ещё нет,
до 00:00 МСК накануне даты не опрашиваем: табло держат только сегодня/завтра.

send — async callable(chat_id, text, thread_id): развязывает поллер с aiogram и делает
его тестируемым без сети; `now` прокидывается по той же причине.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from flight_bot import health, registry, storage, watchdog
from flight_bot.config import Settings
from flight_bot.diff import diff_snapshots
from flight_bot.models import FlightSnapshot, is_cancelled
from flight_bot.render import update_message

log = logging.getLogger("flight_bot.poller")

Send = Callable[[int, str, Optional[int]], Awaitable[None]]

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
    # Без офсета пояс вылета неизвестен (не обязательно МСК) — не угадываем.
    if dep and dep.tzinfo and dep - now > timedelta(hours=FAR_HOURS):
        return FAR_SEC
    return near


def wake_at(date: str, epoch: int, near: int) -> int:
    """Когда снова искать рейс, которого нет на табло: не раньше суток до даты."""
    day_before = datetime.fromisoformat(date).replace(tzinfo=MSK) - timedelta(days=1)
    return max(epoch + near, int(day_before.timestamp()))


def expired(date: str, now: datetime) -> bool:
    day = datetime.fromisoformat(date).replace(tzinfo=MSK)
    return now >= day + timedelta(days=1 + EXPIRE_DAYS)


def expiry_message(row: sqlite3.Row, seen: bool) -> str:
    when = f"{row['date'][8:10]}.{row['date'][5:7]}"
    why = ("прошли сутки, а прилёта на табло так и не было." if seen
           else "рейс так и не появился на табло. Если он ещё нужен, пришли номер заново.")
    return f"▸ Перестал следить за {row['flight']} {when}: {why}"


async def _fetch(conn: sqlite3.Connection, row: sqlite3.Row) -> Optional[FlightSnapshot]:
    try:
        last = storage.get_last(conn, row["id"])
        return await registry.fetch_for(row["flight"], row["date"], row["direction"],
                                        prefer=row["source"], key=last.key if last else None)
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
            await send(row["chat_id"], expiry_message(row, bool(row["last_json"])), row["thread_id"])
        else:
            rows.append(row)
    # Опрашиваем параллельно: табло через scrape.do отвечает десятки секунд,
    # по очереди одна медленная подписка тормозила бы все остальные.
    snaps = await asyncio.gather(*(_fetch(conn, r) for r in rows))
    for row, curr in zip(rows, snaps, strict=True):
        try:
            await _handle(conn, send, row, curr, now, epoch, near)
        except Exception:  # noqa: BLE001 — одна подписка не должна ронять весь круг
            log.exception("подписка %s упала", row["id"])
            try:
                storage.set_next(conn, row["id"], epoch + near)
            except Exception:  # noqa: BLE001 — БД легла: остальные подписки всё равно пробуем
                log.exception("подписка %s: не записался следующий опрос", row["id"])


async def _handle(conn: sqlite3.Connection, send: Send, row: sqlite3.Row,
                  curr: Optional[FlightSnapshot], now: datetime, epoch: int, near: int) -> None:
    sub_id = row["id"]
    if curr is None:
        # рейса пока нет на табло — не ошибка, ждём следующего круга
        storage.set_next(conn, sub_id, wake_at(row["date"], epoch, near))
        return
    prev = storage.get_last(conn, sub_id)
    if (prev is not None and curr.source == "dme.ru" and prev.gate and not curr.gate
            and not curr.departure.fact):
        # Бегущая строка табло (DME) показывает выход не на каждом опросе: пропавший
        # выход не стираем, иначе его возвращение пришло бы повторным пушем.
        curr = dataclasses.replace(curr, gate=prev.gate)
    if prev is not None:
        changes = diff_snapshots(prev, curr)
        if changes:
            await send(row["chat_id"], update_message(curr, changes), row["thread_id"])
    # Снимок — после пуша: упавший дифф повторится на следующем круге. Ошибку
    # отправки send глотает сам (main.py) — такой пуш не повторяется.
    storage.set_last(conn, sub_id, curr)
    # Прилетел или отменён — рейс завершён, опрашивать дальше нечего.
    if curr.arrival.fact or is_cancelled(curr):
        storage.deactivate(conn, sub_id)
        return
    storage.set_next(conn, sub_id, epoch + interval_for(curr, now, near))


async def run(conn: sqlite3.Connection, settings: Settings, send: Send) -> None:
    while True:
        try:
            watchdog.notify()
        except OSError:
            log.exception("sd_notify не ушёл")
        if settings.airlabs_key_expires:
            health.HEALTH.remind_expiry("AirLabs", settings.airlabs_key_expires,
                                        datetime.now(MSK).date())
        try:
            await poll_once(conn, send, near=settings.poll_interval_sec)
        except Exception:  # noqa: BLE001 — один сбой не должен ронять цикл
            log.exception("poll_once упал")
        await asyncio.sleep(TICK_SEC)
