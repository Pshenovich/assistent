"""Журнал действий операторов в админке: кто, что, над кем, когда."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]


def _conn():
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    return conn


def log(actor: str, action: str, target: str = "", **detail: Any) -> None:
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO admin_audit (ts, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
            (ts, str(actor), action, str(target), json.dumps(detail, ensure_ascii=False)),
        )
        conn.commit()


def recent(*, limit: int = 200, target: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM admin_audit"
    params: list[Any] = []
    if target:
        sql += " WHERE target = ?"
        params.append(target)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail"] or "{}")
        except (TypeError, ValueError):
            detail = {}
        out.append({"id": r["id"], "ts": r["ts"], "actor": r["actor"], "action": r["action"],
                    "target": r["target"], "detail": detail})
    return out
