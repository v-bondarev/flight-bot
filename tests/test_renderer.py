"""Renderer: параметры запроса к рендер-сервису и выключенное состояние."""
import asyncio

from flight_bot.http import Renderer


class _Client:
    calls = []

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def get(self, url, params=None, timeout=None):
        _Client.calls.append((url, dict(params or {})))

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
