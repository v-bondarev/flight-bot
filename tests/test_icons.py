"""Значки из пака: id по порядку, без пака — обычные эмодзи; загрузка при старте."""
import asyncio
from types import SimpleNamespace

import pytest

from flight_bot import icons, main


@pytest.fixture(autouse=True)
def _no_pack():
    icons.load([])
    yield
    icons.load([])


def test_without_pack_plain_fallback_emoji():
    assert icons.ic("aircraft-takeoff") == "✈️" and icons.ic("checkin-desk-change") == "↔️"


def test_pack_ids_map_by_order_into_tg_emoji_with_fallback():
    ids = [str(1000 + i) for i in range(len(icons.FALLBACK))]
    assert icons.load(ids)
    assert icons.ic("checkin-start") == '<tg-emoji emoji-id="1000">🛎️</tg-emoji>'
    assert icons.ic("status-cancelled") == f'<tg-emoji emoji-id="{ids[-1]}">❌</tg-emoji>'


def test_pack_of_other_size_is_not_guessed():
    assert not icons.load(["1", "2"])
    assert icons.ic("flight-header") == "🔔"


class _Tg:
    def __init__(self, stickers=None, error=None):
        self.stickers, self.error, self.asked = stickers, error, []

    async def get_sticker_set(self, name):
        self.asked.append(name)
        if self.error:
            raise self.error
        return SimpleNamespace(stickers=self.stickers)


def test_load_icons_at_startup():
    st = [SimpleNamespace(custom_emoji_id=str(i)) for i in range(len(icons.FALLBACK))]
    tg = _Tg(st)
    asyncio.run(main.load_icons(tg, "FlightStatusMinimal"))
    assert tg.asked == ["FlightStatusMinimal"] and icons.ic("flight-header").startswith("<tg-emoji")


def test_load_icons_failure_or_off_keeps_plain():
    asyncio.run(main.load_icons(_Tg(error=RuntimeError("нет сети")), "FlightStatusMinimal"))
    assert icons.ic("flight-header") == "🔔"
    tg = _Tg([])
    asyncio.run(main.load_icons(tg, ""))
    assert tg.asked == []
