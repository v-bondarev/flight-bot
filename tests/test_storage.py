"""Хранилище подписок: подписка/список/снятие и round-trip last-снимка."""
from flight_bot import storage
from flight_bot.models import FlightSnapshot, Leg


def _conn():
    return storage.connect(":memory:")


def test_add_lists_and_reactivates():
    conn = _conn()
    sid = storage.add(conn, 42, "SU2128", "2026-09-06")
    assert storage.list_for_chat(conn, 42)
    # повторная подписка на тот же рейс не плодит дублей и возвращает тот же id
    assert storage.add(conn, 42, "su2128", "2026-09-06") == sid
    assert len(storage.list_for_chat(conn, 42)) == 1


def test_remove_hides_from_list_and_active():
    conn = _conn()
    sid = storage.add(conn, 42, "SU2128", "2026-09-06")
    assert storage.remove(conn, 42, sid) is True
    assert storage.list_for_chat(conn, 42) == []
    assert storage.active_all(conn) == []
    # чужую подписку снять нельзя
    sid2 = storage.add(conn, 42, "SU100", "2026-09-06")
    assert storage.remove(conn, 999, sid2) is False


def test_source_is_kept_and_migrated_into_old_db():
    import sqlite3
    old = sqlite3.connect(":memory:")
    old.execute("""CREATE TABLE subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL, flight TEXT NOT NULL, date TEXT NOT NULL,
        direction TEXT NOT NULL DEFAULT 'departure', active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, last_json TEXT, next_at INTEGER NOT NULL DEFAULT 0,
        UNIQUE(chat_id, flight, date))""")
    old.execute("INSERT INTO subscriptions (chat_id, flight, date, created_at) VALUES (1,'SU1','2026-09-06','x')")
    old.commit()
    # прогоняем миграцию через тот же путь, что и connect(), но на живом соединении
    old.row_factory = sqlite3.Row
    have = {r["name"] for r in old.execute("PRAGMA table_info(subscriptions)")}
    for col, ddl in storage._MIGRATIONS.items():
        if col not in have:
            old.execute(ddl)
    assert old.execute("SELECT source FROM subscriptions").fetchone()["source"] == ""

    conn = _conn()
    sid = storage.add(conn, 42, "S71055", "2026-09-05", "departure", "dme.ru")
    assert storage.active_all(conn)[0]["source"] == "dme.ru"
    storage.add(conn, 42, "S71055", "2026-09-05", "departure", "svo.aero")  # реактивация обновляет источник
    assert storage.active_all(conn)[0]["source"] == "svo.aero" and storage.active_all(conn)[0]["id"] == sid


def test_last_snapshot_roundtrip():
    conn = _conn()
    sid = storage.add(conn, 42, "SU2128", "2026-09-06")
    assert storage.get_last(conn, sid) is None
    snap = FlightSnapshot(
        flight="SU2128", date="2026-09-06", direction="departure",
        origin_iata="SVO", dest_iata="AYT", status="Регистрация",
        departure=Leg(plan="2026-09-06T00:25:00+03:00"), gate="129",
    )
    storage.set_last(conn, sid, snap)
    got = storage.get_last(conn, sid)
    assert got == snap  # frozen dataclass → сравнение по значению
