"""Доступ только владельцу и разбор /track — чистые функции, без Telegram."""
import os

from flight_bot import bot, config


def test_owner_filter_allows_only_owners_and_everyone_when_unset(monkeypatch):
    monkeypatch.setattr(bot, "OWNERS", frozenset({620773}))
    assert bot.allowed(620773)
    assert not bot.allowed(2844343)
    assert not bot.allowed(None)                 # от имени чата (sender_chat) — без from_user
    monkeypatch.setattr(bot, "OWNERS", frozenset())
    assert bot.allowed(2844343)                  # OWNER_IDS пуст — как раньше


def test_flight_number_from_text_or_track_args():
    assert bot.flight_number("ek 770") == "EK770"
    assert bot.flight_number(" SU2128 ") == "SU2128"
    assert bot.flight_number("привет") is None
    assert bot.flight_number(None) is None       # /track без аргумента


def test_owner_ids_from_env(monkeypatch):
    monkeypatch.setattr(os, "environ", {"OWNER_IDS": "620773, 181447747,"})
    assert config.load().owner_ids == frozenset({620773, 181447747})
    monkeypatch.setattr(os, "environ", {})
    assert config.load().owner_ids == frozenset()
    monkeypatch.setattr(os, "environ", {"OWNER_IDS": "620773; @me"})
    import pytest
    with pytest.raises(SystemExit, match="OWNER_IDS"):
        config.load()


def _feed(text, user_id, chat_type, monkeypatch):
    """Прогнать апдейт через настоящий Dispatcher; вернуть, что бот ответил."""
    import asyncio
    from datetime import datetime

    from aiogram import Bot, Dispatcher
    from aiogram.types import Chat, Message, Update, User

    from flight_bot import registry

    said = []

    async def answer(self, text, **kw):
        said.append(text)
    monkeypatch.setattr(Message, "answer", answer)

    async def search(flight):
        return []
    monkeypatch.setattr(registry, "search", search)

    chat_id = user_id if chat_type == "private" else -100500
    msg = Message(message_id=1, date=datetime.now(), text=text,
                  chat=Chat(id=chat_id, type=chat_type),
                  from_user=User(id=user_id, is_bot=False, first_name="u"))
    dp = Dispatcher()
    dp.include_router(bot.router)
    try:
        asyncio.run(dp.feed_update(Bot("1:x"), Update(update_id=1, message=msg)))
    finally:
        dp.sub_routers.remove(bot.router)       # роутер — модульный, отцепляем
        bot.router._parent_router = None
    return said


def test_dispatcher_owner_only_track_in_group_and_silence(monkeypatch):
    monkeypatch.setattr(bot, "OWNERS", frozenset({620773}))
    assert _feed("EK770", 2844343, "private", monkeypatch) == []          # чужой — тишина
    assert _feed("/track EK770", 2844343, "supergroup", monkeypatch) == []
    track = _feed("/track EK770", 620773, "supergroup", monkeypatch)
    assert len(track) == 1 and "EK770" in track[0]                        # дошло до поиска
    assert _feed("всем привет", 620773, "supergroup", monkeypatch) == []  # группа: не отвечаем
    assert _feed("10 30", 620773, "supergroup", monkeypatch) == []        # похоже на рейс — не ищем
    assert len(_feed("/Track EK770", 620773, "supergroup", monkeypatch)) == 1
    assert _feed("всем привет", 620773, "private", monkeypatch)[0].startswith("Не похоже")
    assert _feed("/track", 620773, "private", monkeypatch) == ["Укажите номер рейса: /track SU2128"]


def test_foreign_tap_is_answered_and_ignored(monkeypatch):
    import asyncio
    from datetime import datetime

    from aiogram import Bot, Dispatcher
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from flight_bot import storage
    monkeypatch.setattr(bot, "OWNERS", frozenset({620773}))
    answered, added = [], []

    async def answer(self, *a, **kw):
        answered.append(self.from_user.id)
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(storage, "add", lambda *a, **kw: added.append(a))

    msg = Message(message_id=1, date=datetime.now(), text="Нашёл",
                  chat=Chat(id=-100500, type="supergroup"))
    cb = CallbackQuery(id="1", from_user=User(id=2844343, is_bot=False, first_name="u"),
                       chat_instance="x", message=msg,
                       data="sub|EK770|2026-10-16|departure|aerodatabox.com")
    dp = Dispatcher()
    dp.include_router(bot.router)
    try:
        asyncio.run(dp.feed_update(Bot("1:x"), Update(update_id=2, callback_query=cb), conn=None))
    finally:
        dp.sub_routers.remove(bot.router)
        bot.router._parent_router = None
    assert answered == [2844343] and added == []          # часики погашены, подписки нет


def test_start_greets_as_gatey(monkeypatch):
    monkeypatch.setattr(bot, "OWNERS", frozenset({620773}))
    said = _feed("/start", 620773, "private", monkeypatch)
    assert said == [bot.GREETING] and said[0].startswith("👋 Привет! Я Гейти!")
