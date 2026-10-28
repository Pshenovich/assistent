"""Общее открытие SQLite: WAL и ожидание блокировки вместо «database is locked»."""

from __future__ import annotations

import multiprocessing as mp
import sqlite3

from assistant.lib import sqlite_conn


def test_connect_enables_wal_and_busy_timeout(tmp_path):
    conn = sqlite_conn.connect(tmp_path / "a.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == sqlite_conn.BUSY_TIMEOUT_MS
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_existing_rollback_db_switches_and_keeps_data(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE t(a)")
    old.execute("INSERT INTO t VALUES (42)")
    old.commit()
    old.close()
    conn = sqlite_conn.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("SELECT a FROM t").fetchall() == [(42,)]
    # Старый код (обычный sqlite3.connect) читает базу в WAL как раньше.
    assert sqlite3.connect(path).execute("SELECT a FROM t").fetchall() == [(42,)]


def _writer(path: str, tag: str, n: int) -> None:
    conn = sqlite_conn.connect(path)
    for i in range(n):
        conn.execute("INSERT INTO t(tag, i) VALUES (?, ?)", (tag, i))
        conn.commit()
    conn.close()


def test_two_processes_write_without_locked_errors(tmp_path):
    path = str(tmp_path / "shared.sqlite")
    conn = sqlite_conn.connect(path)
    conn.execute("CREATE TABLE t(tag TEXT, i INT)")
    conn.commit()
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(path, tag, 200)) for tag in ("bot", "web")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0, 0]
    rows = dict(conn.execute("SELECT tag, COUNT(*) FROM t GROUP BY tag").fetchall())
    assert rows == {"bot": 200, "web": 200}
