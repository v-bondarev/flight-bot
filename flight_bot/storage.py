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
CREATE TABLE IF NOT EXISTS source_health (   -- серия ошибок источника (health.py), переживает рестарт
    source    TEXT PRIMARY KEY,
    fails     INTEGER NOT NULL,
    since     REAL    NOT NULL,   -- epoch первой ошибки серии
    since_msk TEXT    NOT NULL,
    alerted   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (       -- когда последний раз напоминали о сроке ключа
    name TEXT PRIMARY KEY,
    day  TEXT NOT NULL
);

-- Ограничения аэропортов (Росавиация, БПЛА). Посты — сырьё, события — разбор,
-- state — текущий режим, alerts_sent — дедуп отправок.
CREATE TABLE IF NOT EXISTS airport_posts (
    post_id      INTEGER PRIMARY KEY,
    at           TEXT NOT NULL,          -- ISO, UTC
    text_hash    TEXT NOT NULL,          -- ловим правки поста
    reply_to     INTEGER,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS airport_events (
    post_id INTEGER NOT NULL,
    iata    TEXT    NOT NULL,
    kind    TEXT    NOT NULL,            -- closed | partial | lifted
    at      TEXT    NOT NULL,
    episode INTEGER,                     -- id поста введения, для lifted
    PRIMARY KEY (post_id, iata, kind)
);
CREATE TABLE IF NOT EXISTS airport_state (
    iata       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,            -- closed | partial | open
    since      TEXT NOT NULL,
    post_id    INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS airport_alerts_sent (
    chat_id INTEGER NOT NULL,
    sub_id  INTEGER NOT NULL,
    iata    TEXT    NOT NULL,
    post_id INTEGER NOT NULL,            -- id эпизода = пост введения
    kind    TEXT    NOT NULL,            -- closed | partial | lifted | suppressed
    sent_at TEXT    NOT NULL,
    PRIMARY KEY (chat_id, sub_id, iata, post_id, kind)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Колонки, добавленные после первого релиза: CREATE IF NOT EXISTS их в живую
# базу не принесёт, поэтому доливаем ALTER'ом.
_MIGRATIONS = {
    "source": "ALTER TABLE subscriptions ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "thread_id": "ALTER TABLE subscriptions ADD COLUMN thread_id INTEGER",
    "origin_iata": "ALTER TABLE subscriptions ADD COLUMN origin_iata TEXT NOT NULL DEFAULT ''",
    "dest_iata": "ALTER TABLE subscriptions ADD COLUMN dest_iata TEXT NOT NULL DEFAULT ''",
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
        thread_id: Optional[int] = None,
        origin_iata: str = "", dest_iata: str = "") -> int:
    """Подписать (или реактивировать, если та же уже была). Возвращает id.
    Повторная подписка переносит пуши в тему, откуда подписались последний раз,
    и опрашивается сразу: старый next_at (до часа) оживлённой строке не нужен."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO subscriptions
               (chat_id, flight, date, direction, active, created_at, source,
                thread_id, origin_iata, dest_iata)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
           ON CONFLICT(chat_id, flight, date)
           DO UPDATE SET active=1, direction=excluded.direction, source=excluded.source,
                         thread_id=excluded.thread_id, next_at=0,
                         origin_iata=CASE WHEN excluded.origin_iata<>''
                                          THEN excluded.origin_iata
                                          ELSE subscriptions.origin_iata END,
                         dest_iata=CASE WHEN excluded.dest_iata<>''
                                        THEN excluded.dest_iata
                                        ELSE subscriptions.dest_iata END""",
        (chat_id, flight.upper(), date, direction, now, source, thread_id,
         (origin_iata or "").upper(), (dest_iata or "").upper()),
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
        """UPDATE subscriptions
              SET last_json=?,
                  origin_iata=COALESCE(NULLIF(?, ''), origin_iata),
                  dest_iata=COALESCE(NULLIF(?, ''), dest_iata)
            WHERE id=?""",
        (json.dumps(snap_to_dict(snap), ensure_ascii=False),
         (snap.origin_iata or "").upper(), (snap.dest_iata or "").upper(), sub_id),
    )
    conn.commit()


def set_route(conn: sqlite3.Connection, sub_id: int,
              origin_iata: str, dest_iata: str) -> None:
    conn.execute(
        """UPDATE subscriptions
              SET origin_iata=COALESCE(NULLIF(?, ''), origin_iata),
                  dest_iata=COALESCE(NULLIF(?, ''), dest_iata)
            WHERE id=?""",
        ((origin_iata or "").upper(), (dest_iata or "").upper(), sub_id),
    )
    conn.commit()


def subs_for_airport(conn: sqlite3.Connection, iata: str) -> List[sqlite3.Row]:
    """Активные подписки, у которых этот аэропорт — вылет или прилёт."""
    return conn.execute(
        """SELECT * FROM subscriptions
            WHERE active=1 AND (origin_iata=? OR dest_iata=?)""",
        ((iata or "").upper(), (iata or "").upper()),
    ).fetchall()


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


def load_health(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM source_health").fetchall()


def save_health(conn: sqlite3.Connection, source: str, fails: int, since: float,
                since_msk: str, alerted: bool) -> None:
    conn.execute(
        """INSERT INTO source_health (source, fails, since, since_msk, alerted)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source) DO UPDATE SET fails=excluded.fails, since=excluded.since,
                 since_msk=excluded.since_msk, alerted=excluded.alerted""",
        (source, fails, since, since_msk, int(alerted)),
    )
    conn.commit()


def clear_health(conn: sqlite3.Connection, source: str) -> None:
    conn.execute("DELETE FROM source_health WHERE source=?", (source,))
    conn.commit()


def reminder_day(conn: sqlite3.Connection, name: str) -> Optional[str]:
    row = conn.execute("SELECT day FROM reminders WHERE name=?", (name,)).fetchone()
    return row["day"] if row else None


def set_reminder_day(conn: sqlite3.Connection, name: str, day: str) -> None:
    conn.execute("INSERT INTO reminders (name, day) VALUES (?, ?) "
                 "ON CONFLICT(name) DO UPDATE SET day=excluded.day", (name, day))
    conn.commit()


# --- Ограничения аэропортов -------------------------------------------------

def meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def past_hash(conn: sqlite3.Connection, post_id: int) -> Optional[str]:
    row = conn.execute(
        "SELECT text_hash FROM airport_posts WHERE post_id=?", (post_id,)
    ).fetchone()
    return row["text_hash"] if row else None


def save_post(conn: sqlite3.Connection, post_id: int, at: str, text_hash: str,
              reply_to: Optional[int], processed_at: str) -> None:
    conn.execute(
        """INSERT INTO airport_posts (post_id, at, text_hash, reply_to, processed_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(post_id) DO UPDATE SET
               at=excluded.at, text_hash=excluded.text_hash,
               reply_to=excluded.reply_to, processed_at=excluded.processed_at""",
        (post_id, at, text_hash, reply_to, processed_at),
    )
    conn.commit()


def replace_events(conn: sqlite3.Connection, post_id: int, events: list) -> None:
    """Пересобрать события поста: правки поста меняют и разбор."""
    conn.execute("DELETE FROM airport_events WHERE post_id=?", (post_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO airport_events (post_id, iata, kind, at, episode) "
        "VALUES (?, ?, ?, ?, ?)",
        [(e.post_id, e.iata, e.kind, e.at, e.episode) for e in events],
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, iata: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM airport_state WHERE iata=?", (iata,)
    ).fetchone()


def set_state(conn: sqlite3.Connection, iata: str, kind: str, since: str,
              post_id: int, updated_at: str) -> None:
    conn.execute(
        """INSERT INTO airport_state (iata, kind, since, post_id, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(iata) DO UPDATE SET
               kind=excluded.kind, since=excluded.since,
               post_id=excluded.post_id, updated_at=excluded.updated_at""",
        (iata, kind, since, post_id, updated_at),
    )
    conn.commit()


def alert_sent(conn: sqlite3.Connection, chat_id: int, sub_id: int, iata: str,
               post_id: int, kind: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM airport_alerts_sent
            WHERE chat_id=? AND sub_id=? AND iata=? AND post_id=? AND kind=?""",
        (chat_id, sub_id, iata, post_id, kind),
    ).fetchone()
    return row is not None


def alert_sent_any(conn: sqlite3.Connection, chat_id: int, sub_id: int, iata: str,
                   post_id: int, kinds: tuple) -> bool:
    marks = ",".join("?" for _ in kinds)
    row = conn.execute(
        f"""SELECT 1 FROM airport_alerts_sent
             WHERE chat_id=? AND sub_id=? AND iata=? AND post_id=?
               AND kind IN ({marks})""",
        (chat_id, sub_id, iata, post_id, *kinds),
    ).fetchone()
    return row is not None


def mark_alert(conn: sqlite3.Connection, chat_id: int, sub_id: int, iata: str,
               post_id: int, kind: str, sent_at: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO airport_alerts_sent
               (chat_id, sub_id, iata, post_id, kind, sent_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (chat_id, sub_id, iata, post_id, kind, sent_at),
    )
    conn.commit()
