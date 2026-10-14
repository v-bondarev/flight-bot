"""Подписки и последний снимок рейса — в SQLite. Синхронно: запросы копеечные.

Одна подписка = (chat_id, рейс, дата, направление); thread_id — тема форума,
куда слать пуши (NULL — General или обычный чат). Last-снимок храним прямо в
строке (JSON) — отдельная таблица снапшотов на таком объёме избыточна.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import List, Optional

from flight_bot.models import FlightSnapshot, snap_from_dict, snap_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    flight     TEXT    NOT NULL,
    date       TEXT    NOT NULL,
    direction  TEXT    NOT NULL DEFAULT 'departure',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL,
    last_json  TEXT,
    next_at    INTEGER NOT NULL DEFAULT 0,   -- epoch следующего опроса (адаптивный)
    source     TEXT    NOT NULL DEFAULT '',  -- где рейс нашли при подписке (опрашиваем первым)
    thread_id  INTEGER,                      -- тема форума, где подписались
    UNIQUE(chat_id, flight, date)
);
CREATE TABLE IF NOT EXISTS schedule_calls (
    at INTEGER NOT NULL   -- epoch платного запроса расписания (бюджет квоты AeroDataBox)
);
"""

# Колонки, добавленные после первого релиза: CREATE IF NOT EXISTS их в живую
# базу не принесёт, поэтому доливаем ALTER'ом.
_MIGRATIONS = {
    "source": "ALTER TABLE subscriptions ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "thread_id": "ALTER TABLE subscriptions ADD COLUMN thread_id INTEGER",
}


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(subscriptions)")}
    for col, ddl in _MIGRATIONS.items():
        if col not in have:
            conn.execute(ddl)
    conn.commit()
    return conn


def add(conn: sqlite3.Connection, chat_id: int, flight: str, date: str,
        direction: str = "departure", source: str = "",
        thread_id: Optional[int] = None) -> int:
    """Подписать (или реактивировать, если та же уже была). Возвращает id.
    Повторная подписка переносит пуши в тему, откуда подписались последний раз."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO subscriptions (chat_id, flight, date, direction, active, created_at, source, thread_id)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?)
           ON CONFLICT(chat_id, flight, date)
           DO UPDATE SET active=1, direction=excluded.direction, source=excluded.source,
                         thread_id=excluded.thread_id""",
        (chat_id, flight.upper(), date, direction, now, source, thread_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM subscriptions WHERE chat_id=? AND flight=? AND date=?",
        (chat_id, flight.upper(), date),
    ).fetchone()
    return int(row["id"])


def list_for_chat(conn: sqlite3.Connection, chat_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM subscriptions WHERE chat_id=? AND active=1 ORDER BY date, flight",
        (chat_id,),
    ).fetchall()


def active_all(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM subscriptions WHERE active=1").fetchall()


def due(conn: sqlite3.Connection, now_epoch: int) -> List[sqlite3.Row]:
    """Активные подписки, которым пора опрашиваться."""
    return conn.execute(
        "SELECT * FROM subscriptions WHERE active=1 AND next_at<=?", (now_epoch,)
    ).fetchall()


def set_next(conn: sqlite3.Connection, sub_id: int, epoch: int) -> None:
    conn.execute("UPDATE subscriptions SET next_at=? WHERE id=?", (epoch, sub_id))
    conn.commit()


def remove(conn: sqlite3.Connection, chat_id: int, sub_id: int) -> bool:
    cur = conn.execute(
        "UPDATE subscriptions SET active=0 WHERE id=? AND chat_id=?", (sub_id, chat_id)
    )
    conn.commit()
    return cur.rowcount > 0


def deactivate(conn: sqlite3.Connection, sub_id: int) -> None:
    conn.execute("UPDATE subscriptions SET active=0 WHERE id=?", (sub_id,))
    conn.commit()


def deactivate_chat(conn: sqlite3.Connection, chat_id: int) -> int:
    """Снять все подписки чата (бота выгнали, чат удалён). Возвращает число строк."""
    cur = conn.execute(
        "UPDATE subscriptions SET active=0 WHERE chat_id=? AND active=1", (chat_id,)
    )
    conn.commit()
    return cur.rowcount


def migrate_chat(conn: sqlite3.Connection, old_chat_id: int, new_chat_id: int) -> int:
    """Группа стала супергруппой — перенести подписки на новый chat_id.

    Переносим только живые. Если в новом чате такая подписка уже есть (UNIQUE),
    оживляем её, а старую гасим: две живые копии дали бы двойные пуши.
    Возвращает, сколько живых подписок оказалось в новом чате благодаря переносу.
    """
    moved = conn.execute(
        "UPDATE OR IGNORE subscriptions SET chat_id=? WHERE chat_id=? AND active=1",
        (new_chat_id, old_chat_id),
    ).rowcount
    revived = conn.execute(
        """UPDATE subscriptions SET active=1
           WHERE chat_id=? AND active=0 AND (flight, date) IN
                 (SELECT flight, date FROM subscriptions WHERE chat_id=? AND active=1)""",
        (new_chat_id, old_chat_id),
    ).rowcount
    conn.execute(
        "UPDATE subscriptions SET active=0 WHERE chat_id=? AND active=1", (old_chat_id,)
    )
    conn.commit()
    return moved + revived


def clear_thread(conn: sqlite3.Connection, chat_id: int, thread_id: int) -> int:
    """Тему форума удалили — её подписки дальше шлют в General."""
    cur = conn.execute(
        "UPDATE subscriptions SET thread_id=NULL WHERE chat_id=? AND thread_id=?",
        (chat_id, thread_id),
    )
    conn.commit()
    return cur.rowcount


def get_last(conn: sqlite3.Connection, sub_id: int) -> Optional[FlightSnapshot]:
    row = conn.execute(
        "SELECT last_json FROM subscriptions WHERE id=?", (sub_id,)
    ).fetchone()
    if not row or not row["last_json"]:
        return None
    return snap_from_dict(json.loads(row["last_json"]))


def set_last(conn: sqlite3.Connection, sub_id: int, snap: FlightSnapshot) -> None:
    conn.execute(
        "UPDATE subscriptions SET last_json=? WHERE id=?",
        (json.dumps(snap_to_dict(snap), ensure_ascii=False), sub_id),
    )
    conn.commit()


def spend_schedule_call(conn: sqlite3.Connection, limit: int, window_sec: int,
                        now: Optional[int] = None) -> bool:
    """Списать платный запрос расписания, если за последние window_sec их меньше
    limit. В базе, а не в памяти: рестарт бота бюджет не обнуляет."""
    now = int(time.time()) if now is None else now
    conn.execute("DELETE FROM schedule_calls WHERE at <= ?", (now - window_sec,))
    used = conn.execute("SELECT COUNT(*) FROM schedule_calls").fetchone()[0]
    if used < limit:
        conn.execute("INSERT INTO schedule_calls (at) VALUES (?)", (now,))
    conn.commit()
    return used < limit
