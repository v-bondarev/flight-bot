"""Ежедневная копия базы подписок с ротацией (запускает deploy/flight-bot-backup.timer).

    cd /opt/flight-bot && .venv/bin/python -m flight_bot.backup

Копия — через sqlite3 backup API (консистентна при работающем боте). Храним
KEEP_DAYS дней; удаляем только свои файлы db-YYYYMMDD.sqlite в каталоге копий.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from flight_bot import config

MSK = timezone(timedelta(hours=3))
KEEP_DAYS = 14
_OWN = re.compile(r"^db-\d{8}\.sqlite$")


def run(db_path: str, out_dir: str, keep_days: int = KEEP_DAYS, stamp: str = "") -> str:
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    stamp = stamp or datetime.now(MSK).strftime("%Y%m%d")
    target = os.path.join(out_dir, f"db-{stamp}.sqlite")
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.chmod(target, 0o600)
    cutoff = time.time() - keep_days * 86400
    for name in os.listdir(out_dir):
        path = os.path.join(out_dir, name)
        if _OWN.match(name) and path != target and os.path.getmtime(path) < cutoff:
            os.remove(path)
    return target


if __name__ == "__main__":
    config.load_env(os.getenv("FLIGHT_BOT_ENV", ".env"))
    out = os.getenv("BACKUP_DIR", os.path.expanduser("~/flight-bot-backup/daily"))
    print(run(config.load().db_path, out), file=sys.stdout)
