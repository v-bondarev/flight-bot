import asyncio
import json
from pathlib import Path

import httpx
import pytest

from flight_bot.sources import vko
from flight_bot.vko_client import VkoProtocolError

FX = Path(__file__).parent / 'fixtures'


def rows(kind):
    return json.loads((FX / ('vko_api_' + kind + '.json')).read_text())['results']['flights']


def test_api_departure_times_and_remote_timezone():
    s = vko.parse_api(rows('dep'), 'UT571', 'departure')[0]
    assert (s.origin_iata, s.dest_iata, s.dest_city) == ('VKO', 'KJA', 'Красноярск')
    assert s.departure.fact == '2026-09-04T21:53:00+03:00'
    assert s.arrival.fact == '2026-09-05T02:09:00+07:00'
    assert s.checkin.start_fact == '2026-09-04T15:25:00+03:00'
    assert s.checkin.finish_plan == '2026-09-04T20:45:00+03:00'
    assert s.aircraft == 'Boeing 737-800'
    assert s.boarding.start_fact is None  # gates_st is not a confirmed actual time
    assert (s.flight, s.source, s.key) == ('UT571', 'vnukovo.ru', '101132896')
    assert vko.parse_api(rows('dep'), 'B22271', 'departure')
    assert vko.parse_api(rows('dep'), 'UT572', 'departure') == []
    assert vko.parse_api(rows('dep'), 'UT571', 'arrival') == []


def test_arrival_codeshare_belt_and_missing_checkin():
    s = vko.parse_api(rows('arr'), 'SU6181', 'arrival')[0]
    assert (s.origin_iata, s.dest_iata, s.flight) == ('LED', 'VKO', 'SU6181')
    assert s.arrival.fact == '2026-09-04T20:31:00+03:00'
    assert s.departure.fact == '2026-09-04T19:14:00+03:00'
    assert s.baggage_belt == 'А04'
    assert s.checkin.start_fact is None and s.boarding.start_fact is None


def test_lists_cancellation_transfers_and_unknown_timezone():
    r = dict(rows('dep')[0], cancelled=True, checkins=['61–70', '93'], gates=['12','13'],
             fls_time_difference_hours_utc=None, transfers=[{'fls_iata':'KUT', 'fls_city_name':'Кутаиси'}])
    s = vko.parse_api([r], 'UT571', 'departure')[0]
    assert s.status == 'Отменён'
    assert s.checkin.desks == '61–70,93' and s.gate == '12,13'
    assert s.via == 'KUT Кутаиси'
    assert s.arrival.plan is None


def test_invalid_matching_row_is_failure():
    with pytest.raises(VkoProtocolError):
        vko.parse_api([dict(rows('dep')[0], st='broken')], 'UT571', 'departure')


class Api:
    def __init__(self, data=None, fail=False):
        self.data, self.fail = data, fail
    async def flights(self, flight, direction):
        if self.fail:
            raise VkoProtocolError('changed challenge')
        return self.data


class Render:
    enabled = True
    def __init__(self): self.calls = []
    async def render(self, url, **kw):
        self.calls.append(kw)
        return (FX / ('vko_board_arrival.html' if kw.get('click') else 'vko_board_departure.html')).read_text()


def test_source_http_works_without_renderer_and_filters_date():
    src = vko.VkoSource(api=Api(rows('dep')))
    result = asyncio.run(src.fetch('UT571', '2026-09-06'))
    assert len(result) == 1 and result[0].date == '2026-09-06'


def test_valid_empty_result_does_not_render():
    renderer = Render()
    src = vko.VkoSource(renderer=renderer, api=Api([]))
    assert asyncio.run(src.fetch('UT571')) == []
    assert renderer.calls == []


def test_api_failure_falls_back_to_correct_direction():
    renderer = Render()
    src = vko.VkoSource(renderer=renderer, api=Api(fail=True))
    got = asyncio.run(src.fetch('SU6181', direction='arrival'))
    assert got and got[0].origin_iata == 'LED'
    assert renderer.calls[0]['click'] == vko.ARRIVAL_CLICK


def test_failure_without_renderer_propagates():
    with pytest.raises(VkoProtocolError):
        asyncio.run(vko.VkoSource(api=Api(fail=True)).fetch('UT571'))


def test_registry_configures_vko_http_proxy_and_cache(monkeypatch):
    from dataclasses import replace
    from flight_bot import config, registry
    src = vko.VkoSource()
    monkeypatch.setattr(registry, 'SOURCES', [src])
    settings = replace(config.load(), ru_proxy_url='socks5://localhost:1080',
                       render_cache_sec=42, airlabs_api_key='')
    registry.configure(settings)
    assert src.api.ru_proxy_url == 'socks5://localhost:1080'
    assert src.api.cache_sec == 42
