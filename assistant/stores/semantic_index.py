"""Индекс семантической памяти (B6): куски текстов пользователя и их векторы, поиск перебором."""

from __future__ import annotations

import math
import os
import sqlite3
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


def _db_path() -> Path:
    raw = (os.getenv("SEMANTIC_DB_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "data" / "semantic.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    path = str(_db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS sem_sources (
            owner_user_id TEXT NOT NULL, source_key TEXT NOT NULL, content_hash TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', date TEXT NOT NULL DEFAULT '',
            ref_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, source_key)
        );
        CREATE TABLE IF NOT EXISTS sem_chunks (
            owner_user_id TEXT NOT NULL, source_key TEXT NOT NULL, chunk_no INTEGER NOT NULL,
            text TEXT NOT NULL, vec BLOB NOT NULL,
            PRIMARY KEY (owner_user_id, source_key, chunk_no)
        );
        """
    )
    _CONN.commit()
    return _CONN


def _pack(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _unpack(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


def source_hashes(owner: Any) -> dict[str, str]:
    with _LOCK:
        rs = _conn().execute("SELECT source_key, content_hash FROM sem_sources WHERE owner_user_id = ?", (str(owner),)).fetchall()
    return {r["source_key"]: r["content_hash"] for r in rs}


def source_meta(owner: Any) -> dict[str, tuple[str, str, str]]:
    """{key: (content_hash, title, ref_json)} — понять, что поменялось только название / ссылка."""
    with _LOCK:
        rs = _conn().execute(
            "SELECT source_key, content_hash, title, ref_json FROM sem_sources WHERE owner_user_id = ?", (str(owner),)
        ).fetchall()
    return {r["source_key"]: (r["content_hash"], r["title"], r["ref_json"]) for r in rs}


def update_meta(owner: Any, key: str, *, title: str, date: str, ref_json: str) -> None:
    """Переименовали заметку / лист: обновить подпись источника без пересчёта векторов."""
    with _LOCK:
        c = _conn()
        c.execute(
            "UPDATE sem_sources SET title = ?, date = ?, ref_json = ? WHERE owner_user_id = ? AND source_key = ?",
            (title, date, ref_json, str(owner), key),
        )
        c.commit()


def first_chunks(owner: Any) -> list[dict[str, Any]]:
    """Первый кусок каждого источника с подписью — для совпадения по названию («дай заметку Вебапп»)."""
    with _LOCK:
        rs = _conn().execute(
            """SELECT c.source_key, c.text, s.kind, s.title, s.date, s.ref_json
               FROM sem_chunks c JOIN sem_sources s
                 ON s.owner_user_id = c.owner_user_id AND s.source_key = c.source_key
               WHERE c.owner_user_id = ? AND c.chunk_no = 0""",
            (str(owner),),
        ).fetchall()
    return [dict(r) for r in rs]


def upsert_source(owner: Any, key: str, *, content_hash: str, kind: str, title: str, date: str, ref_json: str,
                  chunks: list[str], vecs: list[list[float]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM sem_chunks WHERE owner_user_id = ? AND source_key = ?", (str(owner), key))
        c.executemany(
            "INSERT INTO sem_chunks (owner_user_id, source_key, chunk_no, text, vec) VALUES (?, ?, ?, ?, ?)",
            [(str(owner), key, i, t, _pack(v)) for i, (t, v) in enumerate(zip(chunks, vecs))],
        )
        c.execute(
            """INSERT INTO sem_sources (owner_user_id, source_key, content_hash, kind, title, date, ref_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(owner_user_id, source_key) DO UPDATE SET content_hash = excluded.content_hash,
               kind = excluded.kind, title = excluded.title, date = excluded.date, ref_json = excluded.ref_json,
               updated_at = excluded.updated_at""",
            (str(owner), key, content_hash, kind, title, date, ref_json, now),
        )
        c.commit()


def delete_source(owner: Any, key: str) -> None:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM sem_chunks WHERE owner_user_id = ? AND source_key = ?", (str(owner), key))
        c.execute("DELETE FROM sem_sources WHERE owner_user_id = ? AND source_key = ?", (str(owner), key))
        c.commit()


def search(owner: Any, qvec: list[float], *, k: int = 40) -> list[dict[str, Any]]:
    """Косинусная близость по всем кускам пользователя (векторы нормированы не всегда — нормируем)."""
    with _LOCK:
        rs = _conn().execute(
            """SELECT c.source_key, c.chunk_no, c.text, c.vec, s.kind, s.title, s.date, s.ref_json
               FROM sem_chunks c JOIN sem_sources s
                 ON s.owner_user_id = c.owner_user_id AND s.source_key = c.source_key
               WHERE c.owner_user_id = ?""",
            (str(owner),),
        ).fetchall()
    if not rs:
        return []
    qn = math.sqrt(sum(x * x for x in qvec)) or 1.0
    try:
        import numpy as np

        m = np.frombuffer(b"".join(r["vec"] for r in rs), dtype=np.float32).reshape(len(rs), -1)
        q = np.asarray(qvec, dtype=np.float32) / qn
        norms = np.linalg.norm(m, axis=1)
        norms[norms == 0] = 1.0
        scores = (m @ q) / norms
        order = list(np.argsort(-scores)[:k])
        scored = [(float(scores[i]), rs[i]) for i in order]
    except ImportError:
        scored = []
        for r in rs:
            v = _unpack(r["vec"])
            vn = math.sqrt(sum(x * x for x in v)) or 1.0
            scored.append((sum(a * b for a, b in zip(v, qvec)) / (vn * qn), r))
        scored.sort(key=lambda p: -p[0])
        scored = scored[:k]
    return [
        {"score": s, "source_key": r["source_key"], "chunk_no": r["chunk_no"], "text": r["text"], "kind": r["kind"],
         "title": r["title"], "date": r["date"], "ref_json": r["ref_json"]}
        for s, r in scored
    ]


def stats(owner: Any) -> dict[str, int]:
    with _LOCK:
        c = _conn()
        s = c.execute("SELECT COUNT(*) FROM sem_sources WHERE owner_user_id = ?", (str(owner),)).fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM sem_chunks WHERE owner_user_id = ?", (str(owner),)).fetchone()[0]
    return {"sources": int(s), "chunks": int(n)}
