"""Telegram-бот (aiogram v3). Поток простой, без FSM:

  • пользователь шлёт номер рейса → бот ищет его на табло и показывает даты
    кнопками (у номера бывает несколько суток);
  • тап по дате → подписка + карточка текущего статуса;
  • /list — активные подписки с кнопкой «Отписаться».

В группе Telegram (режим приватности) бот видит только команды, поэтому там
номер шлют командой `/track EK770`; обновления идут в тот чат (и тему форума),
где подписались.
OWNER_IDS — чьи сообщения и кнопки бот принимает; остальных молча игнорирует.

conn прокидывается через workflow-data диспетчера (см. main.py), поэтому
хендлеры получают его аргументом — без глобалей.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from typing import Dict, FrozenSet, Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from flight_bot import registry, storage
from flight_bot.models import snap_from_dict
from flight_bot.render import endpoints, status_card

router = Router()

# Задаётся в main из OWNER_IDS: кто управляет ботом в группах. Личка открыта всем.
OWNERS: FrozenSet[int] = frozenset()


def allowed(chat_type: Optional[str], user_id: Optional[int]) -> bool:
    return chat_type == "private" or not OWNERS or user_id in OWNERS


# Фильтр на весь роутер: чужие сообщения в группе не доходят до хендлеров.
router.message.filter(lambda m: allowed(m.chat.type, m.from_user.id if m.from_user else None))


@router.callback_query(lambda c: not allowed(c.message.chat.type if c.message else None,
                                             c.from_user.id if c.from_user else None))
async def foreign_tap(cb: CallbackQuery) -> None:
    """Чужое нажатие: молча гасим «часики» на кнопке, ничего не делаем."""
    await cb.answer()

# Номер рейса: код 2 знака (буквы/цифры, хотя бы одна буква — «10 30» это время,
# а поиск платный) + 1–4 цифры, пробел не обязателен.
FLIGHT_RE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9]{2}\s?\d{1,4}$")

HELP = (
    "Пришли номер рейса (например <b>SU2128</b>), и я покажу ближайшие даты. "
    "Выбери нужную, и я буду писать обо всех изменениях: задержке, смене "
    "терминала или выхода на посадку, вылете, прилёте, отмене.\n\n"
    "/list — рейсы, за которыми я слежу.\n"
    "В группе: /track SU2128 — новости по рейсу придут в эту группу."
)


GREETING = (
    "👋 Привет! Я Гейти!\n"
    "✈️ Помогу тебе не пропустить свой рейс.\n"
    "📡 Отслеживаю статус авиарейсов в реальном времени.\n"
    "🔔 Сообщаю о задержках, изменениях расписания и назначении гейта.\n"
    "Отправь номер рейса (например, EK132), чтобы начать отслеживание."
)


# Задаётся в main из GREET_NAMES: username → обращение на «вы».
NAMES: Dict[str, str] = {}


def greeting(name: str) -> str:
    return (f"👋 Здравствуйте, {html.escape(name)}! Я Гейти!\n"
            "✈️ Помогу Вам не пропустить свой рейс.\n"
            "📡 Отслеживаю статус авиарейсов в реальном времени.\n"
            "🔔 Сообщаю о задержках, изменениях расписания и назначении гейта.\n"
            "Отправьте номер рейса (например, EK132), чтобы начать отслеживание.")


@router.message(CommandStart())
async def start(message: Message) -> None:
    user = (message.from_user.username or "").lower() if message.from_user else ""
    await message.answer(greeting(NAMES[user]) if user in NAMES else GREETING)


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("list"))
async def list_subs(message: Message, conn: sqlite3.Connection) -> None:
    rows = storage.list_for_chat(conn, message.chat.id)
    if not rows:
        await message.answer("Пока ни за одним рейсом не слежу. Пришли номер, например SU2128.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❌ {r['flight']} {r['date'][8:10]}.{r['date'][5:7]}{_route(r)}",
                              callback_data=f"del|{r['id']}")]
        for r in rows
    ])
    await message.answer("Слежу за этими рейсами. Нажми на рейс, чтобы перестать следить:",
                         reply_markup=kb)


def _route(row: sqlite3.Row) -> str:
    """« · SVO→AYT» из последнего снимка — подписки туда и обратно иначе не отличить."""
    if not row["last_json"]:
        return ""
    o, d = endpoints(snap_from_dict(json.loads(row["last_json"])))
    return f" · {o}→{d}" if o or d else ""


def flight_number(text: Optional[str]) -> Optional[str]:
    """«ek 770» → «EK770»; не номер рейса — None."""
    text = (text or "").strip()
    return text.upper().replace(" ", "") if FLIGHT_RE.match(text) else None


@router.message(Command("track", ignore_case=True))
async def track(message: Message, command: CommandObject) -> None:
    flight = flight_number(command.args)
    if flight is None:
        await message.answer("Напиши номер рейса после команды, например: /track SU2128")
        return
    await offer_dates(message, flight)


# Голый номер — только в личке: в группе «10 30» тоже похоже на рейс, а поиск платный.
@router.message(F.chat.type == "private", F.text.regexp(FLIGHT_RE))
async def on_flight_number(message: Message) -> None:
    await offer_dates(message, flight_number(message.text))


async def offer_dates(message: Message, flight: str) -> None:
    # Табло и расписание отвечают до 15–20 с — пусть чат показывает «печатает».
    await message.bot.send_chat_action(
        message.chat.id, "typing",
        message_thread_id=message.message_thread_id if message.is_topic_message else None)
    snaps = await registry.search(flight)
    down = f"табло {', '.join(sorted(snaps.down))} сейчас не отвечают" if snaps.down else ""
    if not snaps:
        await message.answer(
            f"Не нашёл рейс {flight} на ближайшие дни"
            + (f", а {down}. Попробуй через несколько минут." if down
               else ". Проверь номер или попробуй позже.")
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{s.date[8:10]}.{s.date[5:7]} · {'→'.join(endpoints(s))}",
            callback_data=f"sub|{flight}|{s.date}|{s.direction}|{s.source}")]
        for s in snaps
    ])
    await message.answer(f"Нашёл {flight}. На какую дату следить?"
                         + (f"\n⚠️ {down[0].upper()}{down[1:]}, показываю, что есть." if down else ""),
                         reply_markup=kb)


@router.callback_query(F.data.startswith("sub|"))
async def on_subscribe(cb: CallbackQuery, conn: sqlite3.Connection) -> None:
    parts = cb.data.split("|")
    flight, date, direction = parts[1], parts[2], parts[3]
    source = parts[4] if len(parts) > 4 else ""
    # Тема форума — только у сообщения в теме; у ответа в обычной группе
    # message_thread_id тоже есть, но это не тема.
    thread_id = cb.message.message_thread_id if cb.message.is_topic_message else None
    storage.add(conn, cb.message.chat.id, flight, date, direction, source, thread_id)
    # Часики гасим сразу: табло через scrape.do отвечает до минуты, Telegram
    # ждёт ответа на callback ~30 с, и зависшая кнопка нажимается повторно.
    await cb.answer()
    when = f"{date[8:10]}.{date[5:7]}"
    await cb.message.edit_text(f"⏳ Начинаю следить за {flight} {when}, смотрю табло…")
    snap = await registry.fetch_for(flight, date, direction, prefer=source)
    planned = None if snap else registry.scheduled(flight, date)
    if snap:
        text = "✅ Готово! Напишу, как только что-то изменится. Сейчас так:\n\n" + status_card(snap)
    elif planned:
        text = ("✅ Готово! Пока знаю только расписание:\n\n" + status_card(planned)
                + "\n\nНа табло рейса ещё нет. Начну присылать изменения, как только "
                  "он там появится (не раньше чем за сутки до вылета).")
    else:
        text = f"✅ Готово, слежу за {flight} {when}. Напишу, как только рейс появится на табло."
    await cb.message.edit_text(text)


@router.callback_query(F.data.startswith("del|"))
async def on_unsubscribe(cb: CallbackQuery, conn: sqlite3.Connection) -> None:
    sub_id = int(cb.data.split("|", 1)[1])
    storage.remove(conn, cb.message.chat.id, sub_id)
    await cb.answer("Больше не слежу")
    await cb.message.edit_text("Готово, за этим рейсом больше не слежу. Остальные: /list")


@router.message(F.chat.type == "private")   # в группе посторонний текст не наш
async def fallback(message: Message) -> None:
    await message.answer("Не узнал номер рейса. Он выглядит так: SU2128 или EK 770.\n\n" + HELP)
