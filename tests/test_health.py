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
