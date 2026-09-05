"""Подписки и последний снимок рейса — в SQLite. Синхронно: запросы копеечные.

Одна подписка = (chat_id, рейс, дата, направление). Last-снимок храним прямо в
строке (JSON) — отдельная таблица снапшотов на таком объёме избыточна.
"""
from __future__ import annotations

import json
import sqlite3
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
    UNIQUE(chat_id, flight, date)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def add(conn: sqlite3.Connection, chat_id: int, flight: str, date: str,
        direction: str = "departure") -> int:
    """Подписать (или реактивировать, если та же уже была). Возвращает id."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO subscriptions (chat_id, flight, date, direction, active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)
           ON CONFLICT(chat_id, flight, date)
           DO UPDATE SET active=1, direction=excluded.direction""",
        (chat_id, flight.upper(), date, direction, now),
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


def remove(conn: sqlite3.Connection, chat_id: int, sub_id: int) -> bool:
    cur = conn.execute(
        "UPDATE subscriptions SET active=0 WHERE id=? AND chat_id=?", (sub_id, chat_id)
    )
    conn.commit()
    return cur.rowcount > 0


def deactivate(conn: sqlite3.Connection, sub_id: int) -> None:
    conn.execute("UPDATE subscriptions SET active=0 WHERE id=?", (sub_id,))
    conn.commit()


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
