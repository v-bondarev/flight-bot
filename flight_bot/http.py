"""Транспорт для табло: напрямую, а если хост не пускает — через scrape.do.

dme.ru с riga не отвечает (адрес riga у РФ в чёрных списках), с kz — отвечает.
scrape.do с geoCode=ru даёт российский выход; тот же приём, что в cian_bots.
После первого провала напрямую запоминаем `via_proxy`, иначе каждый опрос
платил бы таймаутом соединения. Сброс — перезапуском процесса.
"""
from __future__ import annotations

from typing import Optional

import httpx

SCRAPEDO_URL = "https://api.scrape.do/"
_DIRECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)


class Fetcher:
    def __init__(self, scrapedo_api_key: str = "", timeout: float = 20.0,
                 connect_timeout: float = 6.0):
        self.api_key = scrapedo_api_key
        self._timeout = timeout
        self._connect = connect_timeout
        self.via_proxy = False

    async def get(self, client: httpx.AsyncClient, url: str,
                  params: Optional[dict] = None) -> str:
        if not self.via_proxy:
            try:
                r = await client.get(url, params=params,
                                     timeout=httpx.Timeout(self._timeout, connect=self._connect))
                r.raise_for_status()
                return r.text
            except _DIRECT_ERRORS:
                if not self.api_key:
                    raise
                self.via_proxy = True
        target = str(httpx.URL(url, params=params or {}))
        r = await client.get(SCRAPEDO_URL,
                             params={"token": self.api_key, "url": target, "geoCode": "ru"},
                             timeout=httpx.Timeout(max(self._timeout, 60.0)))
        r.raise_for_status()
        return r.text
