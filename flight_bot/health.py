"""Мониторинг источников: серия ошибок подряд → предупреждение владельцу, одно на сбой.

Пулково молчало почти двое суток (12–13.10.2026), и никто не заметил — табло
ломаются тихо (TLS, гео-блок, смена API). Ошибка — только исключение источника;
пустой ответ («рейса нет») — это успех. Порог: не меньше `fails` ошибок подряд
и не меньше `after_sec` с первой из них — короткий сбой сети не тревожит.
Состояние в памяти процесса: после рестарта считаем заново.

Срок ключа (`remind_expiry`): за неделю до даты истечения и после неё — одно
напоминание владельцу в день; рестарт может повторить его в тот же день.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Optional, Set

from flight_bot.http import describe

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
        self._reminded: Dict[str, date] = {}
        self._tasks: Set["asyncio.Future"] = set()

    def _send(self, text: str) -> None:
        log.warning(text)
        if self.notify is not None:
            task = asyncio.ensure_future(self.notify(text))
            self._tasks.add(task)                  # держим ссылку, иначе задачу соберёт GC
            task.add_done_callback(self._tasks.discard)

    def fail(self, source: str, exc: BaseException) -> None:
        st = self._state.setdefault(source, _State())
        if st.fails == 0:
            st.since, st.since_msk = self._clock(), datetime.now(MSK).strftime("%d.%m %H:%M")
        st.fails += 1
        if (not st.alerted and st.fails >= self.fails_needed
                and self._clock() - st.since >= self.after_sec):
            st.alerted = True
            self._send(f"⚠️ Источник {source} не отвечает с {st.since_msk} МСК "
                       f"({st.fails} ошибок подряд): {describe(exc)}")

    def ok(self, source: str) -> None:
        st = self._state.get(source)
        if st is None or st.fails == 0:
            return
        if st.alerted:
            self._send(f"✅ Источник {source} снова отвечает (сбой с {st.since_msk} МСК)")
        self._state[source] = _State()

    def remind_expiry(self, name: str, expires: date, today: date, days: int = 7) -> None:
        left = (expires - today).days
        if left > days or self._reminded.get(name) == today:
            return
        self._reminded[name] = today
        when = f"{expires:%d.%m.%Y}"
        if left > 0:
            self._send(f"⏳ Ключ {name} истекает {when} — осталось {left} дн. Продлите и обновите .env")
        elif left == 0:
            self._send(f"⏳ Ключ {name} истекает сегодня, {when}. Продлите и обновите .env")
        else:
            self._send(f"⚠️ Ключ {name} истёк {when}. Продлите и обновите .env")


# Общий на процесс; main подключает отправку владельцу.
HEALTH = Health()
