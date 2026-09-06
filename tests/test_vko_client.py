import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from flight_bot import vko_client as v

FX = Path(__file__).parent / 'fixtures'
CHALLENGE = (FX / 'vko_challenge.html').read_text()
DATA = json.loads((FX / 'vko_api_dep.json').read_text())


def client(handler, **kwargs):
    return v.VkoClient(client_factory=lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), **kw), **kwargs)


@pytest.mark.parametrize('code,expected', [(0, 489), (1, 856), (12345, 928), (2147483647, 218)])
def test_jhash_matches_original_javascript(code, expected):
    assert v.jhash(code) == expected


def test_challenge_cookies_reused_and_renewed():
    calls = []
    stage = [0]

    def handler(req):
        calls.append(req)
        cookies = req.headers.get('cookie', '')
        if req.url.path != '/rest/flights/online':
            return httpx.Response(200, text='<script>var appConfig={};</script>')
        if stage[0] == 0:
            stage[0] = 1
            return httpx.Response(200, text=CHALLENGE,
                                  headers={'set-cookie': '__js_p_=1,600,1,0,1; Path=/; Secure'})
        expected = '856' if stage[0] == 1 else '928'
        if stage[0] == 2:
            stage[0] = 3
            return httpx.Response(200, text=CHALLENGE,
                                  headers={'set-cookie': '__js_p_=12345,600,1,0,1; Path=/; Secure'})
        assert '__jhash_=' + expected in cookies
        assert '__jua_=' + quote(v.UA, safe='~.-_') in cookies
        assert '__js_p_=' in cookies
        return httpx.Response(200, json=DATA)

    c = client(handler, cache_sec=0)
    async def run():
        assert len(await c.flights('UT571', 'departure')) == 3
        assert len(await c.flights('UT571', 'departure')) == 3
        stage[0] = 2
        assert len(await c.flights('UT571', 'departure')) == 3
    asyncio.run(run())
    assert len([r for r in calls if r.url.path == '/rest/flights/online']) == 5


@pytest.mark.parametrize('body,cookie', [(CHALLENGE, None), (CHALLENGE, 'bad'),
    (CHALLENGE.replace('1677696;', '17;'), '1,600,1,0,1')])
def test_unrecognised_challenge_fails_without_loop(body, cookie):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, text=body, headers={
            'set-cookie': '__js_p_=' + cookie + '; Path=/'} if cookie else {})
    c = client(handler)
    with pytest.raises(v.VkoProtocolError):
        asyncio.run(c.flights('UT571', 'departure'))
    assert len(calls) == 1


def test_repeated_challenge_has_bounded_retries():
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, text=CHALLENGE,
                              headers={'set-cookie': '__js_p_=1,600,1,0,1; Path=/'})
    with pytest.raises(v.VkoProtocolError):
        asyncio.run(client(handler).flights('UT571', 'departure'))
    assert len(calls) <= 3


def test_decode_real_encoded_response():
    encoded = json.loads((FX / 'vko_api_encoded.json').read_text())
    decoded = v.decode_payload(encoded, v.DEFAULT_KEY)
    assert decoded['results']['flights'][0]['flt_number'] == 'ЮТ 571'
    assert decoded['results']['flights'][0]['fls_iata'] == 'KJA'
    with pytest.raises(v.VkoProtocolError):
        v.decode_payload({'object': 'not base64!'}, v.DEFAULT_KEY)


def test_pagination_cache_and_concurrent_requests():
    starts = []
    now = [100.0]
    def handler(req):
        if req.url.path != '/rest/flights/online':
            return httpx.Response(200, text='<script>var appConfig={};</script>')
        assert req.url.params['bound'] == 'departure'
        assert req.url.params['search'] == 'UT571'
        start = int(req.url.params['start'])
        starts.append(start)
        return httpx.Response(200, json={'total': 3, 'results': {
            'flights': DATA['results']['flights'][start:start+2]}})
    c = client(handler, clock=lambda: now[0])
    async def run():
        results = await asyncio.gather(*(c.flights('UT571', 'departure') for _ in range(5)))
        assert all(len(r) == 3 for r in results)
        assert starts == [0, 2]
        now[0] += 76
        assert len(await c.flights('UT571', 'departure')) == 3
        assert starts == [0, 2, 0, 2]
    asyncio.run(run())


@pytest.mark.parametrize('payload', [{}, {'results': {'flights': []}, 'total': 3},
    {'success': False}, {'results': {'flights': 'bad'}, 'total': 1}])
def test_invalid_or_incomplete_payload_is_failure(payload):
    def handler(req):
        if req.url.path != '/rest/flights/online':
            return httpx.Response(200, text='<script>var appConfig={};</script>')
        return httpx.Response(200, json=payload)
    with pytest.raises(v.VkoProtocolError):
        asyncio.run(client(handler).flights('UT571', 'departure'))


def test_proxy_recovery_uses_separate_cookie_jar():
    seen = []
    def factory(**kw):
        proxy = kw.pop('proxy', None)
        def handler(req):
            seen.append(proxy)
            if proxy is None:
                raise httpx.ConnectError('offline', request=req)
            assert '__jhash_' not in req.headers.get('cookie', '')
            if req.url.path != '/rest/flights/online':
                return httpx.Response(200, text='<script>var appConfig={};</script>')
            return httpx.Response(200, json={'results': {'flights': []}, 'total': 0})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    c = v.VkoClient(ru_proxy_url='socks5://localhost:1080', client_factory=factory)
    assert asyncio.run(c.flights('UT571', 'departure')) == []
    assert seen[0] is None and seen[-1] == 'socks5://localhost:1080'
