"""Точка входа: бот + фоновый поллер в одном event loop.

    cd /opt/flight-bot && python3 -m flight_bot.main
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from flight_bot import bot as handlers
from flight_bot import config, poller, storage

log = logging.getLogger("flight_bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.load_env(os.getenv("FLIGHT_BOT_ENV", ".env"))
    settings = config.load()
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")

    conn = storage.connect(settings.db_path)
    tg = Bot(settings.bot_token,
             default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(handlers.router)

    async def send(chat_id: int, text: str) -> None:
        try:
            await tg.send_message(chat_id, text)
        except Exception:  # noqa: BLE001 — чат мог заблокировать бота; цикл не роняем
            log.exception("не отправилось в чат %s", chat_id)

    background = set()  # держим ссылку, чтобы задачу не собрал GC

    async def on_startup() -> None:
        background.add(asyncio.create_task(poller.run(conn, settings, send)))
        log.info("поллер запущен, интервал %ds", settings.poll_interval_sec)

    dp.startup.register(on_startup)
    # conn уходит в workflow-data: хендлеры получают его аргументом.
    await dp.start_polling(tg, conn=conn)


if __name__ == "__main__":
    asyncio.run(main())
