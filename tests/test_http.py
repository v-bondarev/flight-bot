"""Fetcher: напрямую → RU-прокси → scrape.do; провал ступени запоминается."""
import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from flight_bot.http import SCRAPEDO_URL, Fetcher


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


class _Net:
    """Фейковая сеть: кто отвечает напрямую / через прокси. Пишет журнал (proxy, url)."""

    def __init__(self, direct_ok=False, proxy_ok=True):
        self.direct_ok, self.proxy_ok, self.calls = direct_ok, proxy_ok, []

    def factory(self, proxy=None, **kw):
        net = self

        class Client:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False

            async def get(self_, url, params=None, timeout=None):
                net.calls.append((proxy, url, dict(params or {})))
                if url.startswith(SCRAPEDO_URL):
                    return _Resp("via scrape.do")
                if proxy is None and net.direct_ok:
                    return _Resp("direct")
                if proxy is not None and net.proxy_ok:
                    return _Resp("via proxy")
                raise httpx.ConnectTimeout("blocked")
        return Client()


URL = "https://www.dme.ru/book/live-board/"
P = {"searchText": "S7 1055", "direction": "D"}


def test_direct_when_it_works():
    net = _Net(direct_ok=True)
    f = Fetcher("KEY", "socks5://127.0.0.1:1080", client_factory=net.factory)
    assert asyncio.run(f.get(URL, P)) == "direct"
    assert [c[0] for c in net.calls] == [None]


def test_falls_to_ru_proxy_and_remembers():
    net = _Net()
    f = Fetcher("KEY", "socks5://127.0.0.1:1080", client_factory=net.factory)
    assert asyncio.run(f.get(URL, P)) == "via proxy"
    assert [c[0] for c in net.calls] == [None, "socks5://127.0.0.1:1080"]
    assert f.direct_blocked and not f.proxy_blocked
    asyncio.run(f.get(URL, P))
    assert net.calls[-1][0] == "socks5://127.0.0.1:1080" and len(net.calls) == 3   # без повтора напрямую


def test_falls_to_scrapedo_when_proxy_dead_and_encodes_target():
    net = _Net(proxy_ok=False)
    f = Fetcher("KEY", "socks5://127.0.0.1:1080", client_factory=net.factory)
    assert asyncio.run(f.get(URL, P)) == "via scrape.do"
    proxy, url, p = net.calls[-1]
    assert url == SCRAPEDO_URL and p["token"] == "KEY" and p["geoCode"] == "ru"
    u = urlsplit(p["url"])
    assert (u.netloc, u.path) == ("www.dme.ru", "/book/live-board/")
    assert parse_qs(u.query) == {"searchText": ["S7 1055"], "direction": ["D"]}
    assert f.direct_blocked and f.proxy_blocked


def test_scrapedo_directly_when_no_proxy_configured():
    net = _Net()
    f = Fetcher("KEY", client_factory=net.factory)
    assert asyncio.run(f.get(URL, P)) == "via scrape.do"
    assert [c[0] for c in net.calls] == [None, None]


def test_no_fallback_configured_raises_original_error():
    net = _Net()
    with pytest.raises(httpx.ConnectTimeout):
        asyncio.run(Fetcher(client_factory=net.factory).get(URL))


def test_last_hop_is_retried_not_buried():
    net = _Net(proxy_ok=False)
    f = Fetcher(ru_proxy_url="socks5://127.0.0.1:1080", client_factory=net.factory)
    with pytest.raises(httpx.ConnectTimeout):
        asyncio.run(f.get(URL))            # прокси умер, резерва нет — ошибка наружу
    assert f.direct_blocked and not f.proxy_blocked
    with pytest.raises(httpx.ConnectTimeout):
        asyncio.run(f.get(URL))
    # напрямую больше не ходим, прокси пробуем снова: туннель может подняться
    assert [c[0] for c in net.calls] == [None, "socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"]
