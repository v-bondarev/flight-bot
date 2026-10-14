"""Ежедневный бэкап базы: консистентная копия + ротация только своих файлов."""
import os
import sqlite3
import time

from flight_bot import backup


def test_backup_copies_db_and_rotates_only_own_old_files(tmp_path):
    db = tmp_path / "bot.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (42)")
    c.commit()
    out = tmp_path / "daily"
    out.mkdir()
    old = out / "db-20260901.sqlite"
    old.write_text("old")
    foreign = out / "notes.txt"
    foreign.write_text("keep")
    week_ago = time.time() - 20 * 86400
    os.utime(old, (week_ago, week_ago))
    os.utime(foreign, (week_ago, week_ago))

    made = backup.run(str(db), str(out), keep_days=14, stamp="20261014")
    assert made == str(out / "db-20261014.sqlite")
    assert sqlite3.connect(made).execute("SELECT x FROM t").fetchone() == (42,)
    assert not old.exists()                      # своя старая копия — удалена
    assert foreign.exists()                      # чужое не трогаем
    assert oct(os.stat(made).st_mode & 0o777) == "0o600"
