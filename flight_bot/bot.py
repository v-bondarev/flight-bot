"""Telegram-бот (aiogram v3). Поток простой, без FSM:

  • пользователь шлёт номер рейса → бот ищет его на табло и показывает даты
    кнопками (у номера бывает несколько суток);
  • тап по дате → подписка + карточка текущего статуса;
  • /list — активные подписки с кнопкой «Отписаться».

В группе Telegram (режим приватности) бот видит только команды, поэтому там
номер шлют командой `/track EK770`; обновления идут в тот чат, где подписались.
OWNER_IDS — чьи сообщения и кнопки бот принимает; остальных молча игнорирует.

conn прокидывается через workflow-data диспетчера (см. main.py), поэтому
хендлеры получают его аргументом — без глобалей.
"""
from __future__ import annotations

import re
import sqlite3
from typing import FrozenSet, Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from flight_bot import registry, storage
from flight_bot.render import endpoints, status_card

router = Router()

# Задаётся в main из OWNER_IDS. Пусто — бот открыт всем, как раньше.
OWNERS: FrozenSet[int] = frozenset()


def allowed(user_id: Optional[int]) -> bool:
    return not OWNERS or user_id in OWNERS


# Фильтр на весь роутер: чужие сообщения не доходят до хендлеров.
router.message.filter(lambda m: allowed(m.from_user.id if m.from_user else None))


@router.callback_query(lambda c: not allowed(c.from_user.id if c.from_user else None))
async def foreign_tap(cb: CallbackQuery) -> None:
    """Чужое нажатие: молча гасим «часики» на кнопке, ничего не делаем."""
    await cb.answer()

# Номер рейса: код 2 знака (буквы/цифры) + 1–4 цифры, пробел не обязателен.
FLIGHT_RE = re.compile(r"^[A-Za-z0-9]{2}\s?\d{1,4}$")

HELP = (
    "Пришлите номер рейса (например <b>SU2128</b>) — покажу ближайшие даты, "
    "выберите нужную, и я буду присылать все изменения: задержку, смену "
    "терминала или выхода, вылет, прилёт, отмену.\n\n"
    "/list — ваши подписки.\n"
    "В группе: /track SU2128 — обновления придут в эту группу."
)


GREETING = (
    "👋 Привет! Я Гейти!\n"
    "✈️ Помогу тебе не пропустить свой рейс.\n"
    "📡 Отслеживаю статус авиарейсов в реальном времени.\n"
    "🔔 Сообщаю о задержках, изменениях расписания и назначении гейта.\n"
    "Отправь номер рейса (например, EK132), чтобы начать отслеживание."
)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(GREETING)


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


def flight_number(text: Optional[str]) -> Optional[str]:
    """«ek 770» → «EK770»; не номер рейса — None."""
    text = (text or "").strip()
    return text.upper().replace(" ", "") if FLIGHT_RE.match(text) else None


@router.message(Command("track", ignore_case=True))
async def track(message: Message, command: CommandObject) -> None:
    flight = flight_number(command.args)
    if flight is None:
        await message.answer("Укажите номер рейса: /track SU2128")
        return
    await offer_dates(message, flight)


# Голый номер — только в личке: в группе «10 30» тоже похоже на рейс, а поиск платный.
@router.message(F.chat.type == "private", F.text.regexp(FLIGHT_RE))
async def on_flight_number(message: Message) -> None:
    await offer_dates(message, flight_number(message.text))


async def offer_dates(message: Message, flight: str) -> None:
    snaps = await registry.search(flight)
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
    planned = None if snap else registry.scheduled(flight, date)
    if snap:
        text = "✅ Подписал. Текущий статус:\n\n" + status_card(snap)
    elif planned:
        text = ("✅ Подписал. По расписанию:\n\n" + status_card(planned)
                + "\n\nНа табло рейса пока нет — изменения начну присылать, "
                  "как только он там появится (не раньше суток до вылета).")
    else:
        text = f"✅ Подписал на {flight} {date}."
    await cb.message.edit_text(text)
    await cb.answer()


@router.callback_query(F.data.startswith("del|"))
async def on_unsubscribe(cb: CallbackQuery, conn: sqlite3.Connection) -> None:
    sub_id = int(cb.data.split("|", 1)[1])
    storage.remove(conn, cb.message.chat.id, sub_id)
    await cb.answer("Отписал")
    await cb.message.edit_text("Отписка выполнена. /list — что осталось.")


@router.message(F.chat.type == "private")   # в группе посторонний текст не наш
async def fallback(message: Message) -> None:
    await message.answer("Не похоже на номер рейса. " + HELP)
