"""HTTP client for VKO's public timetable and its known JS cookie challenge.

The challenge is translated, never evaluated. Cookies belong to one transport;
unknown challenges and invalid API payloads fail so the source can use Renderer.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import re
import time
from typing import Callable, Dict, List
from urllib.parse import quote

import httpx

from flight_bot.http import UA

BOARD_URL = 'https://www.vnukovo.ru/ru/for-passengers/reysi/online-tablo/'
API_URL = 'https://www.vnukovo.ru/rest/flights/online'
# Public fallback used by the site's JS response decoder (not a credential).
DEFAULT_KEY = 'd87ea039-6704-4aec-84c3-c7b40b997f08'
_HASH_JS = ('function get_jhash(b) {var x = 123456789;var i = 0; var k = 0;'
            'for (i = 0; i < 1677696; i++) {'
            'x = ((x + b) ^ (x + (x % 3) + (x % 17) + b) ^ i) % 16776960;'
            'if (x % 117 == 0) { k = (k + 1) % 1111; }}return k;}')


class VkoProtocolError(ValueError):
    """The response cannot safely be treated as flight data."""


def jhash(code: int) -> int:
    """Equivalent of get_jhash, including JS signed int32 XOR/remainder."""
    x, k = 123456789, 0
    for i in range(1677696):
        mod3 = x % 3 if x >= 0 else -((-x) % 3)
        mod17 = x % 17 if x >= 0 else -((-x) % 17)
        x = ((x + code) ^ (x + mod3 + mod17 + code) ^ i) & 0xffffffff
        if x >= 0x80000000:
            x -= 0x100000000
        x = x % 16776960 if x >= 0 else -((-x) % 16776960)
        if x % 117 == 0:
            k = (k + 1) % 1111
    return k


def decode_payload(payload: dict, key: str) -> dict:
    try:
        if not isinstance(payload, dict):
            raise ValueError('not an object')
        if isinstance(payload.get('object'), str):
            if not key or len(key) > 256:
                raise ValueError('invalid decoder key')
            raw = base64.b64decode(payload['object'], validate=True)
            # JS atob -> charCodeAt XOR -> JSON.parse. Non-ASCII is escaped
            # in the upstream JSON, so decode bytes as JS character codes.
            payload = json.loads(''.join(chr(b ^ ord(key[i % len(key)]))
                                         for i, b in enumerate(raw)))
        if not isinstance(payload, dict):
            raise ValueError('not an object')
        return payload
    except (ValueError, TypeError, UnicodeError) as exc:
        raise VkoProtocolError('Invalid VKO API encoding') from exc


class VkoClient:
    def __init__(self, ru_proxy_url: str = '', cache_sec: float = 75,
                 client_factory: Callable = httpx.AsyncClient,
                 clock: Callable = time.monotonic):
        self.ru_proxy_url = ru_proxy_url
        self.cache_sec = cache_sec
        self._factory = client_factory
        self._clock = clock
        self._cookies: Dict[str, httpx.Cookies] = {}
        self._keys: Dict[str, str] = {}
        self._cache: Dict[tuple, tuple] = {}
        self._lock = None
        self._loop = None
        self._direct_retry_at = 0.0

    async def _get(self, client: httpx.AsyncClient, url: str, params=None) -> str:
        for attempt in range(2):
            response = await client.get(url, params=params)
            page = response.text
            if 'get_jhash' not in page and '__js_p_' not in page:
                response.raise_for_status()
                return page
            known = re.sub(r'\s+', '', _HASH_JS)
            if attempt or known not in re.sub(r'\s+', '', page):
                raise VkoProtocolError('Unknown or repeated VKO challenge')
            host = response.url.host
            cookie = next((c for c in client.cookies.jar
                           if c.name == '__js_p_' and c.domain.lstrip('.') == host), None)
            try:
                values = cookie.value.split(',') if cookie else []
                code, age, secure = map(int, values[:3])
                if not (0 <= code <= 2147483647 and 0 < age <= 31536000 and secure in (0, 1)):
                    raise ValueError('out of range')
            except (ValueError, TypeError) as exc:
                raise VkoProtocolError('Invalid VKO challenge cookie') from exc
            # CPU work must not stall Telegram polling. Honour the page's 1 s
            # delay, overlapping computation with that wait.
            value, _ = await asyncio.gather(
                asyncio.get_running_loop().run_in_executor(None, jhash, code),
                asyncio.sleep(1))
            for name, val in [('__jhash_', str(value)), ('__jua_', quote(UA, safe='~.-_'))]:
                result = copy.copy(cookie)
                result.name, result.value = name, val
                result.path, result.path_specified = '/', True
                result.secure, result.expires = bool(secure), int(time.time()) + age
                result.discard = False
                client.cookies.jar.set_cookie(result)
        raise VkoProtocolError('VKO challenge failed')

    async def _via(self, proxy: str, flight: str, direction: str) -> List[dict]:
        async with self._factory(proxy=proxy or None, headers={'User-Agent': UA},
                                 cookies=self._cookies.get(proxy), follow_redirects=True,
                                 timeout=httpx.Timeout(20, connect=6)) as client:
            try:
                if proxy not in self._keys:
                    page = await self._get(client, BOARD_URL)
                    match = re.search(r'var appConfig=(\{.*?\});?\s*</script>', page, re.S)
                    if not match:
                        raise VkoProtocolError('Missing VKO appConfig')
                    config = json.loads(match[1])
                    self._keys[proxy] = config.get('yandexMapApiKey') or DEFAULT_KEY
                rows = []
                for _ in range(20):
                    raw = await self._get(client, API_URL, {
                        'lang': 'ru', 'bound': direction, 'search': flight,
                        'disableFixLayout': 'true', 'start': len(rows), 'limit': 100})
                    payload = decode_payload(json.loads(raw), self._keys[proxy])
                    result = payload.get('results')
                    total = payload.get('total')
                    batch = result.get('flights') if isinstance(result, dict) else None
                    if (payload.get('success') is False or not isinstance(batch, list)
                            or not isinstance(total, int) or total < 0
                            or any(not isinstance(row, dict) for row in batch)):
                        raise VkoProtocolError('Invalid VKO flight list')
                    if not batch and len(rows) < total:
                        raise VkoProtocolError('Incomplete VKO flight list')
                    rows.extend(batch)
                    if len(rows) >= total:
                        return rows
                raise VkoProtocolError('VKO pagination limit exceeded')
            except (ValueError, TypeError) as exc:
                self._keys.pop(proxy, None)
                raise VkoProtocolError('VKO response could not be read') from exc
            finally:
                self._cookies[proxy] = client.cookies

    async def flights(self, flight_no: str, direction: str) -> List[dict]:
        if direction not in ('departure', 'arrival'):
            raise ValueError('Invalid direction')
        key = (flight_no, direction)
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._lock = loop, asyncio.Lock()
        # Serialise cookie negotiation and collapse concurrent identical reads
        # through the cache. Different flights share the authenticated cookies.
        async with self._lock:
            now = self._clock()
            self._cache = {k: hit for k, hit in self._cache.items()
                           if now - hit[0] < self.cache_sec}
            if key in self._cache:
                return self._cache[key][1]
            proxies = ['']
            if self.ru_proxy_url:
                proxies = ([''] if now >= self._direct_retry_at else []) + [self.ru_proxy_url]
            for proxy in proxies:
                try:
                    rows = await self._via(proxy, flight_no, direction)
                    if len(self._cache) >= 256:
                        self._cache.pop(next(iter(self._cache)))
                    self._cache[key] = (self._clock(), rows)
                    return rows
                except (httpx.HTTPError, VkoProtocolError):
                    if proxy or not self.ru_proxy_url:
                        raise
                    self._direct_retry_at = self._clock() + 300
        raise VkoProtocolError('VKO transport unavailable')
