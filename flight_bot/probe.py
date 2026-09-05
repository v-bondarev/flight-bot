"""Проверка источников с сервера, без Telegram:

    cd /opt/flight-bot && .venv/bin/python -m flight_bot.probe SU2128 [2026-09-06]

Печатает, кто из источников знает рейс, и карточку по каждому снимку — так
проверяем покрытие (в т.ч. РФ на AirLabs) до того, как верить боту.
"""
from __future__ import annotations

import asyncio
import os
import sys

from flight_bot import config, registry
from flight_bot.render import status_card


async def main(argv: list) -> None:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    config.load_env(os.getenv("FLIGHT_BOT_ENV", ".env"))
    registry.configure(config.load())
    flight, date = argv[1], (argv[2] if len(argv) > 2 else None)
    for src in registry.SOURCES:
        for direction in registry.DIRECTIONS:
            try:
                snaps = await src.fetch(flight, date, direction)
            except Exception as e:  # noqa: BLE001 — показать, а не упасть
                print(f"{src.name}/{direction}: ошибка {e!r}")
                continue
            print(f"{src.name}/{direction}: {len(snaps)} снимков")
            for s in snaps:
                print(status_card(s), end="\n\n")


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
