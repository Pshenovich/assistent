"""Сценарии «откуда → если → то»: личные правила пользователя (сначала — для рабочих чатов).

Общий движок на всю Орбиту: source_type — откуда события (сейчас только «chat», дальше
mail / meeting / task / schedule), source_id — конкретный источник (id чата).
Курсор — до какого события источник уже разобран; hits — что сценарий нашёл
(UNIQUE по событию — один и тот же источник дважды не срабатывает).

Живёт в базе документов (local_notes) — попадает в ночной бэкап и Litestream.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.lib import sqlite_conn

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()
    _CONN = sqlite_conn.connect(path)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS scenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            instruction TEXT NOT NULL DEFAULT '',
            conditions_json TEXT NOT NULL DEFAULT '{}',
            action TEXT NOT NULL DEFAULT 'show',
            action_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scenarios_src ON scenarios(user_id, source_type, source_id);
        CREATE TABLE IF NOT EXISTS scenario_cursor (
            scenario_id INTEGER PRIMARY KEY,
            last_ref INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scenario_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (scenario_id, source_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_scenario_hits_src ON scenario_hits(user_id, source_type, source_id, created_at);
        """
    )
    _CONN.commit()
    return _CONN


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k, name in (("conditions_json", "conditions"), ("action_json", "action_data"), ("data_json", "data")):
        if k in d:
            try:
                v = json.loads(d.pop(k) or "{}")
            except (TypeError, ValueError):
                v = {}
            d[name] = v if isinstance(v, dict) else {}
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    return d


def create(
    user_id: int,
    *,
    source_type: str,
    source_id: str,
    title: str,
    instruction: str,
    conditions: dict[str, Any],
    action: str,
    action_data: Optional[dict[str, Any]] = None,
    enabled: bool = True,
    cursor: Optional[int] = None,
) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO scenarios (user_id, source_type, source_id, title, instruction, conditions_json,
                                   action, action_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(user_id), source_type, str(source_id), title, instruction,
             json.dumps(conditions or {}, ensure_ascii=False), action,
             json.dumps(action_data or {}, ensure_ascii=False), 1 if enabled else 0, now, now),
        )
        sid = int(cur.lastrowid)
        if cursor is not None:
            conn.execute("INSERT OR REPLACE INTO scenario_cursor (scenario_id, last_ref) VALUES (?, ?)", (sid, int(cursor)))
        conn.commit()
    return get(user_id, sid) or {}


def get(user_id: int, scenario_id: int) -> Optional[dict[str, Any]]:
    """Сценарий пользователя; чужой — None."""
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM scenarios WHERE id = ? AND user_id = ?", (int(scenario_id), int(user_id))
        ).fetchone()
    return _row(r) if r else None


_UPDATABLE = {"title", "instruction", "conditions", "action", "action_data", "enabled"}


def update(user_id: int, scenario_id: int, **fields: Any) -> Optional[dict[str, Any]]:
    sets: list[str] = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in _UPDATABLE or v is None:
            continue
        if k == "conditions":
            sets.append("conditions_json = ?")
            vals.append(json.dumps(v or {}, ensure_ascii=False))
        elif k == "action_data":
            sets.append("action_json = ?")
            vals.append(json.dumps(v or {}, ensure_ascii=False))
        elif k == "enabled":
            sets.append("enabled = ?")
            vals.append(1 if v else 0)
        else:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return get(user_id, scenario_id)
    sets.append("updated_at = ?")
    vals.append(_now())
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            f"UPDATE scenarios SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            (*vals, int(scenario_id), int(user_id)),
        )
        conn.commit()
        if not cur.rowcount:
            return None
    return get(user_id, scenario_id)


def delete(user_id: int, scenario_id: int) -> bool:
    with _LOCK:
        conn = _conn()
        cur = conn.execute("DELETE FROM scenarios WHERE id = ? AND user_id = ?", (int(scenario_id), int(user_id)))
        if cur.rowcount:
            conn.execute("DELETE FROM scenario_cursor WHERE scenario_id = ?", (int(scenario_id),))
            conn.execute("DELETE FROM scenario_hits WHERE scenario_id = ?", (int(scenario_id),))
        conn.commit()
        return bool(cur.rowcount)


def list_for_source(user_id: int, source_type: str, source_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute(
            "SELECT * FROM scenarios WHERE user_id = ? AND source_type = ? AND source_id = ? ORDER BY id",
            (int(user_id), source_type, str(source_id)),
        ).fetchall()
    return [_row(r) for r in rs]


def list_for_user(user_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute("SELECT * FROM scenarios WHERE user_id = ? ORDER BY id", (int(user_id),)).fetchall()
    return [_row(r) for r in rs]


def list_enabled(source_type: str) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute(
            "SELECT * FROM scenarios WHERE source_type = ? AND enabled = 1 ORDER BY id", (source_type,)
        ).fetchall()
    return [_row(r) for r in rs]


def get_cursor(scenario_id: int) -> Optional[int]:
    with _LOCK:
        r = _conn().execute("SELECT last_ref FROM scenario_cursor WHERE scenario_id = ?", (int(scenario_id),)).fetchone()
    return int(r[0]) if r else None


def set_cursor(scenario_id: int, ref: int) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("INSERT OR REPLACE INTO scenario_cursor (scenario_id, last_ref) VALUES (?, ?)", (int(scenario_id), int(ref)))
        conn.commit()


def add_hit(sc: dict[str, Any], source_ref: str, summary: str, data: dict[str, Any]) -> Optional[int]:
    """Запомнить срабатывание. Уже было по этому событию — None (без дублей)."""
    now = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO scenario_hits (scenario_id, user_id, source_type, source_id, source_ref,
                                                 summary, data_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (int(sc["id"]), int(sc["user_id"]), sc["source_type"], str(sc["source_id"]), str(source_ref),
             summary, json.dumps(data or {}, ensure_ascii=False), now, now),
        )
        conn.commit()
        return int(cur.lastrowid) if cur.rowcount else None


def hits_for_source(user_id: int, source_type: str, source_id: str, *, since: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute(
            """
            SELECT h.*, s.title AS scenario_title, s.action AS scenario_action
            FROM scenario_hits h JOIN scenarios s ON s.id = h.scenario_id
            WHERE h.user_id = ? AND h.source_type = ? AND h.source_id = ? AND h.created_at >= ?
            ORDER BY h.created_at DESC, h.id DESC LIMIT ?
            """,
            (int(user_id), source_type, str(source_id), since, int(limit)),
        ).fetchall()
    return [_row(r) for r in rs]


def get_hit(user_id: int, hit_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute("SELECT * FROM scenario_hits WHERE id = ? AND user_id = ?", (int(hit_id), int(user_id))).fetchone()
    return _row(r) if r else None


def set_hit_status(user_id: int, hit_id: int, status: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "UPDATE scenario_hits SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (status, _now(), int(hit_id), int(user_id)),
        )
        conn.commit()
        if not cur.rowcount:
            return None
    return get_hit(user_id, hit_id)


def update_hit(user_id: int, hit_id: int, *, status: Optional[str] = None, data: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Статус и/или данные срабатывания (data сливается с прежними)."""
    h = get_hit(user_id, hit_id)
    if not h:
        return None
    merged = {**(h.get("data") or {}), **(data or {})}
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE scenario_hits SET status = ?, data_json = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (status or h["status"], json.dumps(merged, ensure_ascii=False), _now(), int(hit_id), int(user_id)),
        )
        conn.commit()
    return get_hit(user_id, hit_id)
