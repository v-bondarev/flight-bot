"""Renderer: параметры запроса к рендер-сервису и выключенное состояние."""
import asyncio

from flight_bot.http import Renderer


class _Client:
    calls = []

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def get(self, url, params=None, timeout=None):
        _Client.calls.append((url, dict(params or {})))
        await asyncio.sleep(0)          # уступить loop — чтобы одновременные запросы реально совпали

        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"url": "https://x/final", "html": "<html>ok</html>"}
        return R()


def test_render_request_shape():
    _Client.calls.clear()
    r = Renderer("http://127.0.0.1:8765/", client_factory=lambda **kw: _Client())
    assert r.enabled
    html = asyncio.run(r.render("https://www.vnukovo.ru/x", wait_ms=12000, selector="table",
                                click='button:has-text("Прилёт")'))
    assert html == "<html>ok</html>"
    url, p = _Client.calls[0]
    assert url == "http://127.0.0.1:8765/render"
    assert p == {"url": "https://www.vnukovo.ru/x", "wait": "12000", "selector": "table",
                 "click": 'button:has-text("Прилёт")'}


def test_disabled_without_base_url():
    assert not Renderer("").enabled


def test_cache_ttl_and_inflight_merge():
    now = [1000.0]
    _Client.calls.clear()
    r = Renderer("http://127.0.0.1:8765", cache_sec=75, client_factory=lambda **kw: _Client(),
                 clock=lambda: now[0])
    asyncio.run(r.render("https://x/tablo"))
    asyncio.run(r.render("https://x/tablo"))                       # в TTL — из кэша
    assert len(_Client.calls) == 1
    asyncio.run(r.render("https://x/tablo", click="button"))        # другой ключ — новый рендер
    assert len(_Client.calls) == 2
    now[0] += 76
    asyncio.run(r.render("https://x/tablo"))                       # TTL вышел — снова рендер
    assert len(_Client.calls) == 3

    async def burst():                                              # 5 одновременных → 1 рендер
        await asyncio.gather(*(r.render("https://x/other") for _ in range(5)))
    asyncio.run(burst())
    assert len(_Client.calls) == 4
