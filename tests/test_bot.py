"""Доступ только владельцу и разбор /track — чистые функции, без Telegram."""
import os

from flight_bot import bot, config


def test_owner_filter_allows_only_owners_and_everyone_when_unset(monkeypatch):
    monkeypatch.setattr(bot, "OWNERS", frozenset({620773}))
    assert bot.allowed("supergroup", 620773)
    assert not bot.allowed("supergroup", 2844343)
    assert not bot.allowed("group", None)        # от имени чата (sender_chat) — без from_user
    assert bot.allowed("private", 2844343)       # личка открыта всем
    monkeypatch.setattr(bot, "OWNERS", frozenset())
    assert bot.allowed("supergroup", 2844343)    # OWNER_IDS пуст — как раньше


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


def _feed(text, user_id, chat_type, monkeypatch, username=None, search="empty"):
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

    async def typing(self, *a, **kw):
        return True
    monkeypatch.setattr(Bot, "send_chat_action", typing)
    if search == "empty":
        async def empty(flight):
            return registry.Found()
        monkeypatch.setattr(registry, "search", empty)

    chat_id = user_id if chat_type == "private" else -100500
    msg = Message(message_id=1, date=datetime.now(), text=text,
                  chat=Chat(id=chat_id, type=chat_type),
                  from_user=User(id=user_id, is_bot=False, first_name="u", username=username))
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
    assert _feed("EK770", 2844343, "private", monkeypatch)[0].startswith("Не нашёл рейс EK770")  # личка — всем
    assert _feed("/track EK770", 2844343, "supergroup", monkeypatch) == []
    track = _feed("/track EK770", 620773, "supergroup", monkeypatch)
    assert len(track) == 1 and "EK770" in track[0]                        # дошло до поиска
    assert _feed("всем привет", 620773, "supergroup", monkeypatch) == []  # группа: не отвечаем
    assert _feed("10 30", 620773, "supergroup", monkeypatch) == []        # похоже на рейс — не ищем
    assert len(_feed("/Track EK770", 620773, "supergroup", monkeypatch)) == 1
    assert _feed("всем привет", 620773, "private", monkeypatch)[0].startswith("Не узнал номер рейса")
    assert _feed("/track", 620773, "private", monkeypatch) == [
        "Напиши номер рейса после команды, например: /track SU2128"]


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

def test_subscribe_in_forum_topic_keeps_thread(monkeypatch):
    import asyncio
    from datetime import datetime

    from aiogram.types import CallbackQuery, Chat, Message, User

    from flight_bot import registry, storage

    async def noop(self, *a, **kw):
        return None
    monkeypatch.setattr(CallbackQuery, "answer", noop)
    monkeypatch.setattr(Message, "edit_text", noop)

    async def none(*a, **kw):
        return None
    monkeypatch.setattr(registry, "fetch_for", none)
    monkeypatch.setattr(registry, "scheduled", lambda *a: None)

    def tap(**topic):
        msg = Message(message_id=1, date=datetime.now(), text="Нашёл",
                      chat=Chat(id=-100500, type="supergroup", is_forum=True), **topic)
        cb = CallbackQuery(id="1", from_user=User(id=620773, is_bot=False, first_name="u"),
                           chat_instance="x", message=msg,
                           data="sub|EK770|2026-10-16|departure|aerodatabox.com")
        asyncio.run(bot.on_subscribe(cb, conn))
        return storage.active_all(conn)[0]["thread_id"]

    conn = storage.connect(":memory:")
    assert tap(message_thread_id=7, is_topic_message=True) == 7
    assert tap() is None                                   # General: темы нет
    # ответ на сообщение в обычной группе тоже несёт message_thread_id — это не тема
    assert tap(message_thread_id=9) is None


def test_greet_names_from_env(monkeypatch):
    monkeypatch.setattr(os, "environ", {"GREET_NAMES": "@MBondareva81=Марина Юрьевна, x=Y"})
    assert config.load().greet_names == {"mbondareva81": "Марина Юрьевна", "x": "Y"}
    monkeypatch.setattr(os, "environ", {"GREET_NAMES": "без-знака-равно"})
    import pytest
    with pytest.raises(SystemExit, match="GREET_NAMES"):
        config.load()


def test_named_user_is_greeted_formally(monkeypatch):
    monkeypatch.setattr(bot, "OWNERS", frozenset({620773}))
    monkeypatch.setattr(bot, "NAMES", {"mbondareva81": "Марина Юрьевна"})
    said = _feed("/start", 555, "private", monkeypatch, username="MBondareva81")
    assert said == [bot.greeting("Марина Юрьевна")]
    assert said[0].startswith("👋 Здравствуйте, Марина Юрьевна! Я Гейти!")
    assert "Отправьте номер рейса" in said[0] and "тебе" not in said[0]
    assert _feed("/start", 620773, "private", monkeypatch) == [bot.GREETING]   # остальным — как было
    assert _feed("/start", 777, "private", monkeypatch, username="someone") == [bot.GREETING]


def test_greeting_escapes_html_in_name():
    assert "Иван &amp; Пётр" in bot.greeting("Иван & Пётр")


def test_flight_number_needs_a_letter_in_airline_code():
    assert bot.flight_number("10 30") is None       # время, а не рейс — поиск платный
    assert bot.flight_number("S7 1055") == "S71055"
    assert bot.flight_number("7R123") == "7R123"


def test_search_tells_when_boards_are_down(monkeypatch):
    from flight_bot import registry

    def search_returning(found, down):
        async def search(flight):
            got = registry.Found(found)
            got.down = down
            return got
        monkeypatch.setattr(registry, "search", search)
    search_returning([], {"dme.ru", "svo.aero"})
    said = _feed("EK770", 1, "private", monkeypatch, search=None)
    assert "не отвечают" in said[0] and "dme.ru" in said[0] and "svo.aero" in said[0]
    search_returning([], set())
    assert _feed("EK770", 1, "private", monkeypatch, search=None)[0].startswith("Не нашёл рейс EK770")


def test_subscribe_answers_callback_before_slow_board(monkeypatch):
    """Часики на кнопке гаснут сразу, а не после опроса табло (до минуты через scrape.do)."""
    import asyncio
    from datetime import datetime

    from aiogram.types import CallbackQuery, Chat, Message, User

    from flight_bot import registry, storage
    order = []

    async def answer(self, *a, **kw):
        order.append("answer")

    async def edit(self, text, **kw):
        order.append(("edit", text))
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", edit)

    async def slow(*a, **kw):
        order.append("fetch")
        return None
    monkeypatch.setattr(registry, "fetch_for", slow)
    monkeypatch.setattr(registry, "scheduled", lambda *a: None)
    msg = Message(message_id=1, date=datetime.now(), text="Нашёл", chat=Chat(id=1, type="private"))
    cb = CallbackQuery(id="1", from_user=User(id=1, is_bot=False, first_name="u"),
                       chat_instance="x", message=msg, data="sub|EK770|2026-10-16|departure|")
    asyncio.run(bot.on_subscribe(cb, storage.connect(":memory:")))
    assert order[0] == "answer" and order[1][0] == "edit" and "⏳" in order[1][1]
    assert order[2] == "fetch" and order[3][1].startswith("✅ Готово")


def test_list_shows_route_from_last_snapshot(monkeypatch):
    import asyncio
    from datetime import datetime

    from aiogram.types import Chat, Message

    from flight_bot import storage
    from flight_bot.models import FlightSnapshot
    said = []

    async def answer(self, text, reply_markup=None, **kw):
        said.append([b.text for row in reply_markup.inline_keyboard for b in row])
    monkeypatch.setattr(Message, "answer", answer)
    conn = storage.connect(":memory:")
    sid = storage.add(conn, 42, "SU2128", "2026-09-06")
    storage.set_last(conn, sid, FlightSnapshot(flight="SU2128", date="2026-09-06", direction="departure",
                                               origin_iata="SVO", dest_iata="AYT", status=""))
    storage.add(conn, 42, "SU2129", "2026-09-07")       # ещё не опрашивалась — без маршрута
    msg = Message(message_id=1, date=datetime.now(), text="/list", chat=Chat(id=42, type="private"))
    asyncio.run(bot.list_subs(msg, conn))
    assert said == [["❌ SU2128 06.09 · SVO→AYT", "❌ SU2129 07.09"]]
