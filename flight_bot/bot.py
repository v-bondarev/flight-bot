"""Telegram-бот (aiogram v3). Поток простой, без FSM:

  • пользователь шлёт номер рейса → бот ищет его на табло и показывает даты
    кнопками (у номера бывает несколько суток);
  • тап по дате → подписка + карточка текущего статуса;
  • /list — активные подписки с кнопкой «Отписаться».

conn прокидывается через workflow-data диспетчера (см. main.py), поэтому
хендлеры получают его аргументом — без глобалей.
"""
from __future__ import annotations

import re
import sqlite3

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from flight_bot import registry, storage
from flight_bot.render import endpoints, status_card

router = Router()

# Номер рейса: код 2 знака (буквы/цифры) + 1–4 цифры, пробел не обязателен.
FLIGHT_RE = re.compile(r"^[A-Za-z0-9]{2}\s?\d{1,4}$")

HELP = (
    "Пришлите номер рейса (например <b>SU2128</b>) — покажу ближайшие даты, "
    "выберите нужную, и я буду присылать все изменения: задержку, смену "
    "терминала или выхода, вылет, прилёт, отмену.\n\n"
    "/list — ваши подписки."
)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer("Слежу за рейсами ✈️\n\n" + HELP)


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("list"))
async def list_subs(message: Message, conn: sqlite3.Connection) -> None:
    rows = storage.list_for_chat(conn, message.chat.id)
    if not rows:
        await message.answer("Подписок нет. Пришлите номер рейса, чтобы добавить.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❌ {r['flight']} {r['date'][8:10]}.{r['date'][5:7]}",
                              callback_data=f"del|{r['id']}")]
        for r in rows
    ])
    await message.answer("Ваши подписки:", reply_markup=kb)


@router.message(F.text.regexp(FLIGHT_RE))
async def on_flight_number(message: Message) -> None:
    flight = message.text.strip().upper().replace(" ", "")
    snaps = registry.upcoming(await registry.probe(flight))
    if not snaps:
        await message.answer(
            f"Рейс {flight} не нашёл на доступных табло на ближайшие дни. "
            "Проверьте номер или попробуйте позже."
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{s.date[8:10]}.{s.date[5:7]} · {'→'.join(endpoints(s))}",
            callback_data=f"sub|{flight}|{s.date}|{s.direction}|{s.source}")]
        for s in snaps
    ])
    await message.answer(f"Нашёл {flight}. На какую дату следить?", reply_markup=kb)


@router.callback_query(F.data.startswith("sub|"))
async def on_subscribe(cb: CallbackQuery, conn: sqlite3.Connection) -> None:
    parts = cb.data.split("|")
    flight, date, direction = parts[1], parts[2], parts[3]
    source = parts[4] if len(parts) > 4 else ""
    storage.add(conn, cb.message.chat.id, flight, date, direction, source)
    snap = await registry.fetch_for(flight, date, direction, prefer=source)
    text = "✅ Подписал. Текущий статус:\n\n" + status_card(snap) if snap \
        else f"✅ Подписал на {flight} {date}."
    await cb.message.edit_text(text)
    await cb.answer()


@router.callback_query(F.data.startswith("del|"))
async def on_unsubscribe(cb: CallbackQuery, conn: sqlite3.Connection) -> None:
    sub_id = int(cb.data.split("|", 1)[1])
    storage.remove(conn, cb.message.chat.id, sub_id)
    await cb.answer("Отписал")
    await cb.message.edit_text("Отписка выполнена. /list — что осталось.")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Не похоже на номер рейса. " + HELP)
