"""Значки пушей и карточки — кастомные эмодзи пака FlightStatusMinimal.

Ключи и порядок — как в паке (mapping.csv при его сборке): id эмодзи берём при
старте из getStickerSet по порядку и в репозитории не храним. Пак не загрузился
(нет Premium у владельца, сеть, другое число значков) — пишем запасное обычное
эмодзи; оно же внутри тега: его показывают уведомления и старые клиенты.
"""
from __future__ import annotations

import logging
from typing import Dict, List

log = logging.getLogger("flight_bot.icons")

# ключ → запасное эмодзи; порядок = порядок значков в паке.
FALLBACK: Dict[str, str] = {
    "checkin-start": "🛎️", "checkin-desk-change": "↔️", "checkin-closed": "🔒",
    "boarding-start": "🚪", "boarding-closed": "🔐", "time-change": "🕒",
    "aircraft-takeoff": "✈️", "aircraft-landing": "🛬", "gate-change": "🚪",
    "terminal": "🏢", "aircraft-change": "✈️", "airport-zone": "📍",
    "baggage-claim": "🧳", "flight-cancelled": "❌", "tracking-stopped": "🔕",
    "flight-header": "🔔", "flight-date": "📅", "flight-route": "✈️",
    "stopover": "🗺️", "flight-time": "🕒", "countdown": "⌛",
    "timeline-done": "✅", "timeline-wait": "⚪", "status-planned": "⚪",
    "status-checkin": "🛎️", "status-delay": "🕒", "status-airborne": "✈️",
    "status-arrived": "✅", "status-cancelled": "❌",
}
_ids: Dict[str, str] = {}


def load(ids: List[str]) -> bool:
    """id эмодзи пака по порядку → ключи. Число не совпало — пак другой, не угадываем."""
    _ids.clear()
    if len(ids) != len(FALLBACK) or not all(ids):
        log.warning("пак эмодзи: %d значков вместо %d — пишу обычные эмодзи", len(ids), len(FALLBACK))
        return False
    _ids.update(zip(FALLBACK, ids, strict=True))
    return True


def ic(key: str) -> str:
    """Значок для текста с parse_mode=HTML."""
    alt = FALLBACK[key]
    eid = _ids.get(key)
    return f'<tg-emoji emoji-id="{eid}">{alt}</tg-emoji>' if eid else alt
