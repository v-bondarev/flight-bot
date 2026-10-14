"""sd_notify для systemd WatchdogSec: тик поллера отмечается, зависший процесс перезапустят."""
import asyncio
import socket

import pytest

from flight_bot import poller, watchdog


def test_notify_sends_watchdog_datagram(tmp_path, monkeypatch):
    path = str(tmp_path / "notify.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.settimeout(1)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    assert watchdog.notify() is True
    assert srv.recv(64) == b"WATCHDOG=1"
    monkeypatch.delenv("NOTIFY_SOCKET")
    assert watchdog.notify() is False          # запуск руками, без systemd — тихо


def test_poller_tick_pings_watchdog(monkeypatch):
    pings = []
    monkeypatch.setattr(watchdog, "notify", lambda: pings.append(1))

    async def stop(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(poller.asyncio, "sleep", stop)

    async def once(*a, **kw):
        return None
    monkeypatch.setattr(poller, "poll_once", once)
    from flight_bot import config
    settings = config.load()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(poller.run(None, settings, once))
    assert pings == [1]
