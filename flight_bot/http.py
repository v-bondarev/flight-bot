"""Транспорт для табло: напрямую → свой RU-прокси → scrape.do.

dme.ru не отвечает с зарубежного адреса сервера бота, с российского —
отвечает. Свой прокси — SSH-туннель с SOCKS до RU-хоста (RU_PROXY_URL,
например socks5://127.0.0.1:1080), scrape.do (geoCode=ru) — платный резерв.
Провал ступени запоминаем на процесс, но только когда есть следующая: иначе
каждый опрос платил бы таймаутом соединения. Последнюю ступень не хороним —
её пробуем каждый раз (туннель может подняться), ошибка уходит наружу.
Сброс — перезапуском.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Dict, Optional

import httpx

SCRAPEDO_URL = "https://api.scrape.do/"
# Одновременных запросов к scrape.do на процесс: кредиты платные, а поллер
# опрашивает подписки параллельно. Семафор — по event loop (тесты гоняют
# каждый свой), выставляется registry.configure из SCRAPEDO_CONCURRENCY.
SCRAPEDO_LIMIT = 2
_sems: Dict[int, asyncio.Semaphore] = {}


def _scrapedo_semaphore() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _sems:
        _sems[key] = asyncio.Semaphore(SCRAPEDO_LIMIT)
    return _sems[key]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_HOP_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ProxyError)


class Fetcher:
    def __init__(self, scrapedo_api_key: str = "", ru_proxy_url: str = "",
                 timeout: float = 20.0, connect_timeout: float = 6.0,
                 client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient):
        self.api_key = scrapedo_api_key
        self.ru_proxy_url = ru_proxy_url
        self._timeout = timeout
        self._connect = connect_timeout
        self._factory = client_factory
        self.direct_blocked = False
        self.proxy_blocked = False

    async def _via(self, proxy: Optional[str], url: str, params: Optional[dict]) -> str:
        async with self._factory(proxy=proxy, headers={"User-Agent": UA},
                                 follow_redirects=True) as client:
            r = await client.get(url, params=params,
                                 timeout=httpx.Timeout(self._timeout, connect=self._connect))
            r.raise_for_status()
            return r.text

    async def get(self, url: str, params: Optional[dict] = None) -> str:
        if not self.direct_blocked:
            try:
                return await self._via(None, url, params)
            except _HOP_ERRORS:
                if not (self.ru_proxy_url or self.api_key):
                    raise
                self.direct_blocked = True
        if self.ru_proxy_url and not self.proxy_blocked:
            try:
                return await self._via(self.ru_proxy_url, url, params)
            except _HOP_ERRORS:
                if not self.api_key:
                    raise
                self.proxy_blocked = True
        target = str(httpx.URL(url, params=params or {}))
        async with _scrapedo_semaphore():
            async with self._factory(headers={"User-Agent": UA}) as client:
                r = await client.get(SCRAPEDO_URL,
                                     params={"token": self.api_key, "url": target, "geoCode": "ru"},
                                     timeout=httpx.Timeout(max(self._timeout, 60.0)))
                r.raise_for_status()
                return r.text
