"""sd_notify без зависимостей: тик поллера шлёт systemd «WATCHDOG=1».

Restart=on-failure ловит только падение процесса; зависший event loop (сеть,
SQLite) снаружи не виден. С WatchdogSec в юните systemd перезапустит бота,
если тики пропали. Без NOTIFY_SOCKET (запуск руками) ничего не делает.
"""
from __future__ import annotations

import os
import socket


def notify(state: str = "WATCHDOG=1") -> bool:
    addr = os.getenv("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):                 # абстрактный сокет
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr)
        s.sendall(state.encode())
    return True
