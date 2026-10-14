"""Отправка пуша: кик/«chat not found» гасят подписки чата, миграция группы
переносит их на новый chat_id, удалённая тема — пуш в General."""
import asyncio

from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramMigrateToChat)

from flight_bot import main, storage


class _Tg:
    """Фейковый Bot: падает заданными ошибками по очереди, потом шлёт."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.sent = []

    async def send_message(self, chat_id, text, message_thread_id=None):
        self.sent.append((chat_id, message_thread_id))
        if self.errors:
            raise self.errors.pop(0)


def _setup(*errors):
    conn = storage.connect(":memory:")
    storage.add(conn, -1, "SU2128", "2026-09-06")
    storage.add(conn, -1, "SU100", "2026-09-06")
    storage.add(conn, 42, "SU2128", "2026-09-06")
    tg = _Tg(*errors)
    return conn, tg, main.make_send(tg, conn)


def test_kicked_bot_deactivates_chat_subs():
    conn, tg, send = _setup(TelegramForbiddenError(None, "Forbidden: bot was kicked from the group chat"))
    asyncio.run(send(-1, "x"))
    assert storage.list_for_chat(conn, -1) == []
    assert len(storage.list_for_chat(conn, 42)) == 1


def test_chat_not_found_deactivates_chat_subs():
    conn, tg, send = _setup(TelegramBadRequest(None, "Bad Request: chat not found"))
    asyncio.run(send(-1, "x"))
    assert storage.list_for_chat(conn, -1) == []


def test_other_errors_keep_subs():
    conn, tg, send = _setup(TelegramBadRequest(None, "Bad Request: can't parse entities"),
                            RuntimeError("сеть"))
    asyncio.run(send(-1, "x"))
    asyncio.run(send(-1, "x"))
    assert len(storage.list_for_chat(conn, -1)) == 2


def test_migrated_group_moves_subs_and_resends():
    conn, tg, send = _setup(TelegramMigrateToChat(None, "Bad Request: group chat was upgraded", -1001))
    asyncio.run(send(-1, "x"))
    assert tg.sent == [(-1, None), (-1001, None)]
    assert storage.list_for_chat(conn, -1) == []
    assert len(storage.list_for_chat(conn, -1001)) == 2


def test_push_goes_to_topic_and_falls_back_to_general():
    conn, tg, send = _setup(TelegramBadRequest(None, "Bad Request: message thread not found"))
    storage.add(conn, -1, "SU7", "2026-09-06", thread_id=7)
    storage.add(conn, -1, "SU8", "2026-09-06", thread_id=8)
    asyncio.run(send(-1, "x", 7))
    assert tg.sent == [(-1, 7), (-1, None)]
    assert len(storage.list_for_chat(conn, -1)) == 4
    # тему забыли: следующие пуши сразу в General, чужую тему не тронули
    threads = {r["flight"]: r["thread_id"] for r in storage.list_for_chat(conn, -1)}
    assert threads["SU7"] is None and threads["SU8"] == 8


def test_request_urls_with_keys_do_not_reach_the_log(caplog):
    """httpx пишет INFO «HTTP Request: GET …?api_key=…» — ключи AirLabs/scrape.do в URL."""
    import logging
    main.setup_logging()
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info("HTTP Request: GET https://airlabs.co/api/v9/flight?api_key=SECRET")
    assert "SECRET" not in caplog.text
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").warning("connection pool full")
    assert "connection pool full" in caplog.text            # предупреждения httpx не теряем


def test_log_formatter_redacts_keys_even_in_tracebacks():
    import logging
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "опрос упал: %s", ("api_key=SECRET",), None)
    try:
        raise RuntimeError("for url 'https://api.scrape.do/?token=TOKEN42&url=y'")
    except RuntimeError:
        import sys
        rec.exc_info = sys.exc_info()
    out = main._RedactingFormatter("%(message)s").format(rec)
    assert "SECRET" not in out and "TOKEN42" not in out and "[скрыто]" in out
