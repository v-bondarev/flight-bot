"""Мониторинг источников: серия ошибок подряд → предупреждение владельцу, одно на сбой.

Пулково молчало почти двое суток (12–13.10.2026), и никто не заметил — табло
ломаются тихо (TLS, гео-блок, смена API). Ошибка — только исключение источника;
пустой ответ («рейса нет») — это успех. Порог: не меньше `fails` ошибок подряд
и не меньше `after_sec` с первой из них — короткий сбой сети не тревожит.
Состояние в памяти процесса: после рестарта считаем заново.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Optional

log = logging.getLogger("flight_bot.health")
MSK = timezone(timedelta(hours=3))

Notify = Callable[[str], Awaitable[None]]


@dataclass
class _State:
    fails: int = 0
    since: float = 0.0
    since_msk: str = ""
    alerted: bool = False


class Health:
    def __init__(self, notify: Optional[Notify] = None, clock: Callable[[], float] = time.monotonic,
                 fails: int = 6, after_sec: float = 1800):
        self.notify, self._clock = notify, clock
        self.fails_needed, self.after_sec = fails, after_sec
        self._state: Dict[str, _State] = {}

    def _send(self, text: str) -> None:
        log.warning(text)
        if self.notify is not None:
            asyncio.ensure_future(self.notify(text))

    def fail(self, source: str, exc: BaseException) -> None:
        st = self._state.setdefault(source, _State())
        if st.fails == 0:
            st.since, st.since_msk = self._clock(), datetime.now(MSK).strftime("%d.%m %H:%M")
        st.fails += 1
        if (not st.alerted and st.fails >= self.fails_needed
                and self._clock() - st.since >= self.after_sec):
            st.alerted = True
            self._send(f"⚠️ Источник {source} не отвечает с {st.since_msk} МСК "
                       f"({st.fails} ошибок подряд): {type(exc).__name__}: {str(exc)[:200]}")

    def ok(self, source: str) -> None:
        st = self._state.get(source)
        if st is None or st.fails == 0:
            return
        if st.alerted:
            self._send(f"✅ Источник {source} снова отвечает (сбой с {st.since_msk} МСК)")
        self._state[source] = _State()


# Общий на процесс; main подключает отправку владельцу.
HEALTH = Health()
