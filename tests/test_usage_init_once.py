"""usage_store.init_db: миграция один раз на файл базы, а не на каждый вызов."""

from __future__ import annotations

from assistant.lib import usage_store


def test_init_db_runs_once_per_file(tmp_path, monkeypatch):
    db = tmp_path / "usage.sqlite"
    monkeypatch.setenv("USAGE_DB_PATH", str(db))
    calls = []
    real = usage_store._init_db_now
    monkeypatch.setattr(usage_store, "_init_db_now", lambda: (calls.append(1), real())[1])
    for _ in range(5):
        usage_store.init_db()
    assert len(calls) == 1
    db.unlink()
    usage_store.init_db()  # файл пропал — таблицы надо создать заново
    assert len(calls) == 2
    with usage_store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 0
        idx = {r[1] for r in conn.execute("PRAGMA index_list(usage_events)").fetchall()}
    assert "idx_usage_events_user_ts" in idx
