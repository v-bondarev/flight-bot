"""Мониторинг источников: предупреждение владельцу после серии ошибок, одно на сбой."""
import asyncio

from flight_bot import health


def _tracker(now):
    sent = []

    async def notify(text):
        sent.append(text)
    return health.Health(notify, clock=lambda: now[0], fails=6, after_sec=1800), sent


def _run(coro_fn):
    async def go():
        coro_fn()
        await asyncio.sleep(0)            # уведомление уходит задачей
    asyncio.run(go())


def test_alert_after_six_failures_and_half_an_hour_once_then_recovery():
    now = [0.0]
    h, sent = _tracker(now)
    for i in range(10):
        now[0] = i * 300                  # ошибка каждые 5 мин
        _run(lambda: h.fail("pulkovoairport.ru", RuntimeError("TLS")))
    assert len(sent) == 1 and "pulkovoairport.ru" in sent[0] and "TLS" in sent[0]
    _run(lambda: h.ok("pulkovoairport.ru"))
    assert len(sent) == 2 and sent[1].startswith("✅")
    _run(lambda: h.ok("pulkovoairport.ru"))
    assert len(sent) == 2                 # «снова работает» — один раз


def test_short_burst_or_success_in_between_does_not_alert():
    now = [0.0]
    h, sent = _tracker(now)
    for i in range(10):                   # 10 ошибок за 9 минут — меньше 30 мин
        now[0] = i * 60
        _run(lambda: h.fail("dme.ru", TimeoutError()))
    assert sent == []
    _run(lambda: h.ok("dme.ru"))
    for i in range(10):                   # чередуются с успехом — серия сбрасывается
        now[0] = 3600 + i * 600
        _run(lambda: h.fail("dme.ru", TimeoutError()))
        _run(lambda: h.ok("dme.ru"))
    assert sent == []


def test_alert_and_log_do_not_leak_keys_from_httpx_urls(caplog):
    import logging

    import httpx

    from flight_bot.http import describe, redact
    req = httpx.Request("GET", "https://airlabs.co/api/v9/flight?flight_iata=SU1&api_key=SECRET123")
    exc = httpx.HTTPStatusError("Client error '401' for url '%s'" % req.url, request=req,
                                response=httpx.Response(401, request=req))
    assert describe(exc) == "HTTP 401 airlabs.co"
    now = [0.0]
    h, sent = _tracker(now)
    with caplog.at_level(logging.WARNING):
        for i in range(7):
            now[0] = i * 400
            _run(lambda: h.fail("airlabs.co", exc))
    assert len(sent) == 1 and "SECRET123" not in sent[0] and "SECRET123" not in caplog.text
    assert redact("GET https://api.scrape.do/?token=ABC123&url=x") == \
        "GET https://api.scrape.do/?token=[скрыто]&url=x"


def test_redact_bot_token_and_proxy_credentials():
    from flight_bot.http import redact
    assert redact("POST https://api.telegram.org/bot8641528072:AAH-x_y/sendMessage") == \
        "POST https://api.telegram.org/bot[скрыто]/sendMessage"
    assert redact("socks5://user:p4ss@127.0.0.1:1080") == "socks5://[скрыто]@127.0.0.1:1080"


def test_key_expiry_reminder_last_week_once_a_day():
    from datetime import date
    sent = []

    async def notify(text):
        sent.append(text)
    h = health.Health(notify)
    exp = date(2026, 11, 5)
    _run(lambda: h.remind_expiry("AirLabs", exp, date(2026, 10, 28)))   # 8 дней — рано
    assert sent == []
    _run(lambda: h.remind_expiry("AirLabs", exp, date(2026, 10, 29)))   # 7 дней
    _run(lambda: h.remind_expiry("AirLabs", exp, date(2026, 10, 29)))   # тот же день — молчим
    assert len(sent) == 1 and "AirLabs" in sent[0] and "05.11.2026" in sent[0] and "7 дн" in sent[0]
    _run(lambda: h.remind_expiry("AirLabs", exp, date(2026, 11, 5)))
    assert "сегодня" in sent[1]
    _run(lambda: h.remind_expiry("AirLabs", exp, date(2026, 11, 6)))
    assert "истёк" in sent[2] and len(sent) == 3


def test_key_expiry_setting_parsed_and_validated(monkeypatch):
    from datetime import date

    import pytest

    from flight_bot import config
    monkeypatch.setenv("AIRLABS_KEY_EXPIRES", "2026-11-05")
    assert config.load().airlabs_key_expires == date(2026, 11, 5)
    monkeypatch.setenv("AIRLABS_KEY_EXPIRES", "")
    assert config.load().airlabs_key_expires is None                  # пусто — без напоминания
    monkeypatch.setenv("AIRLABS_KEY_EXPIRES", "05.11.2026")
    with pytest.raises(SystemExit, match="AIRLABS_KEY_EXPIRES"):
        config.load()
