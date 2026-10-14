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


def test_resubscribe_polls_at_once_not_after_old_interval():
    conn = _conn()
    sid = storage.add(conn, 42, "SU2128", "2026-09-06")
    storage.set_next(conn, sid, 10**10)              # часовой интервал «до вылета далеко»
    storage.remove(conn, 42, sid)
    storage.add(conn, 42, "SU2128", "2026-09-06")    # вернул подписку — опрос сразу
    assert [r["id"] for r in storage.due(conn, 1)] == [sid]


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


def test_schedule_budget_is_a_sliding_window_and_survives_reconnect(tmp_path):
    path = str(tmp_path / "budget.sqlite")
    conn = storage.connect(path)
    assert [storage.spend_schedule_call(conn, 2, 100, now=t) for t in (0, 10, 20)] == [True, True, False]
    conn.close()
    conn = storage.connect(path)                                    # рестарт бюджет не обнуляет
    assert storage.spend_schedule_call(conn, 2, 100, now=99) is False
    assert storage.spend_schedule_call(conn, 2, 100, now=100) is True  # вызов в 0 вышел из окна


def test_deactivate_chat_hits_only_that_chat():
    conn = _conn()
    storage.add(conn, 42, "SU2128", "2026-09-06")
    storage.add(conn, 42, "SU100", "2026-09-06")
    storage.add(conn, 7, "SU2128", "2026-09-06")
    assert storage.deactivate_chat(conn, 42) == 2
    assert storage.list_for_chat(conn, 42) == []
    assert len(storage.list_for_chat(conn, 7)) == 1
    assert storage.deactivate_chat(conn, 42) == 0   # повтор ничего не трогает


def test_migrate_chat_moves_subs_without_duplicates():
    conn = _conn()
    storage.add(conn, -1, "SU2128", "2026-09-06", thread_id=None)
    storage.add(conn, -1, "SU100", "2026-09-06")
    storage.add(conn, -1001, "SU100", "2026-09-06")   # в супергруппе такая уже есть
    assert storage.migrate_chat(conn, -1, -1001) == 1
    assert storage.list_for_chat(conn, -1) == []
    got = [(r["flight"], r["date"]) for r in storage.list_for_chat(conn, -1001)]
    assert got == [("SU100", "2026-09-06"), ("SU2128", "2026-09-06")]
    assert len(storage.active_all(conn)) == 2           # дубль из старого чата погашен
    assert storage.migrate_chat(conn, -1, -1001) == 0   # повтор — 0 строк


def test_thread_id_kept_and_migrated_into_old_db():
    import sqlite3
    old = sqlite3.connect(":memory:")
    old.row_factory = sqlite3.Row
    old.execute("""CREATE TABLE subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL, flight TEXT NOT NULL, date TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    old.execute("INSERT INTO subscriptions (chat_id, flight, date, created_at) VALUES (1,'SU1','2026-09-06','x')")
    for col, ddl in storage._MIGRATIONS.items():
        if col == "thread_id":
            old.execute(ddl)
    assert old.execute("SELECT thread_id FROM subscriptions").fetchone()["thread_id"] is None

    conn = _conn()
    storage.add(conn, -1001, "SU2128", "2026-09-06", thread_id=7)
    assert storage.active_all(conn)[0]["thread_id"] == 7
    storage.add(conn, -1001, "SU2128", "2026-09-06")    # из General — тема снимается
    assert storage.active_all(conn)[0]["thread_id"] is None


def test_migrate_chat_keeps_live_sub_when_new_chat_has_it_inactive():
    conn = _conn()
    storage.add(conn, -1, "SU1", "2026-09-06")
    storage.remove(conn, -1, storage.list_for_chat(conn, -1)[0]["id"])   # снятая
    storage.add(conn, -1, "SU2", "2026-09-06")                            # живая
    sid = storage.add(conn, -1001, "SU2", "2026-09-06")
    storage.remove(conn, -1001, sid)                                      # в новом — снятая
    assert storage.migrate_chat(conn, -1, -1001) == 1
    got = [(r["chat_id"], r["flight"]) for r in storage.active_all(conn)]
    assert got == [(-1001, "SU2")]                                        # живая не потерялась
    assert storage.migrate_chat(conn, -1, -1001) == 0


def test_snapshot_from_newer_version_with_unknown_field_loads():
    from flight_bot.models import FlightSnapshot, snap_from_dict, snap_to_dict
    d = snap_to_dict(FlightSnapshot(flight="EK132", date="2026-10-15", direction="departure",
                                    origin_iata="DME", dest_iata="DXB", status=""))
    d["field_from_future"] = "x"
    assert snap_from_dict(d).flight == "EK132"
