"""Точка входа: бот + фоновый поллер в одном event loop.

    cd /opt/flight-bot && python3 -m flight_bot.main
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramMigrateToChat)

from flight_bot import bot as handlers
from flight_bot import config, poller, registry, storage

log = logging.getLogger("flight_bot")


def make_send(tg: Bot, conn: sqlite3.Connection) -> poller.Send:
    """send для поллера. Ошибки Telegram цикл не роняют; по ним чиним подписки:
    бота выгнали / чата нет — гасим подписки чата, группа стала супергруппой —
    переносим их на новый chat_id и шлём туда, темы нет — шлём в General."""

    async def send(chat_id: int, text: str, thread_id: Optional[int] = None) -> None:
        try:
            await tg.send_message(chat_id, text, message_thread_id=thread_id)
        except TelegramMigrateToChat as e:
            moved = storage.migrate_chat(conn, chat_id, e.migrate_to_chat_id)
            log.warning("чат %s стал %s, перенёс подписок: %d",
                        chat_id, e.migrate_to_chat_id, moved)
            await send(e.migrate_to_chat_id, text, thread_id)
        except TelegramForbiddenError as e:
            _gone(chat_id, e)
        except TelegramBadRequest as e:
            if "chat not found" in e.message:
                _gone(chat_id, e)
            elif thread_id is not None and "thread not found" in e.message:
                await send(chat_id, text)
            else:
                log.exception("не отправилось в чат %s", chat_id)
        except Exception:  # noqa: BLE001 — сеть и прочее; цикл не роняем
            log.exception("не отправилось в чат %s", chat_id)

    def _gone(chat_id: int, e: Exception) -> None:
        off = storage.deactivate_chat(conn, chat_id)
        log.warning("чат %s недоступен (%s), снял подписок: %d", chat_id, e, off)

    return send


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.load_env(os.getenv("FLIGHT_BOT_ENV", ".env"))
    settings = config.load()
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")
    registry.configure(settings)
    handlers.OWNERS = settings.owner_ids
    if not handlers.OWNERS:
        log.warning("OWNER_IDS пуст — бот принимает команды от всех")

    conn = storage.connect(settings.db_path)
    tg = Bot(settings.bot_token,
             default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(handlers.router)

    send = make_send(tg, conn)
    background = set()  # держим ссылку, чтобы задачу не собрал GC

    async def on_startup() -> None:
        background.add(asyncio.create_task(poller.run(conn, settings, send)))
        log.info("поллер запущен, интервал %ds", settings.poll_interval_sec)

    dp.startup.register(on_startup)
    # conn уходит в workflow-data: хендлеры получают его аргументом.
    await dp.start_polling(tg, conn=conn)


if __name__ == "__main__":
    asyncio.run(main())
