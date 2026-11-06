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

from assistant.lib import sqlite_conn

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
    _CONN = sqlite_conn.connect(path)
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
        CREATE INDEX IF NOT EXISTS sem_sources_key ON sem_sources (source_key, content_hash);
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


_IN_BATCH = 500  # разделов в одном IN (…) — далеко от лимита параметров SQLite


def _rows_many(sql: str, owners: list[Any]) -> list[sqlite3.Row]:
    """sql с «{owners}» на месте списка разделов — по пачкам, строки всех пачек вместе."""
    keys = list(dict.fromkeys(str(o) for o in owners))
    out: list[sqlite3.Row] = []
    with _LOCK:
        c = _conn()
        for i in range(0, len(keys), _IN_BATCH):
            part = keys[i:i + _IN_BATCH]
            out += c.execute(sql.format(owners=",".join("?" * len(part))), part).fetchall()
    return out


def first_chunks_many(owners: list[Any]) -> list[dict[str, Any]]:
    """first_chunks по нескольким разделам (свой + документы, открытые человеку); owner_user_id — в строке."""
    rs = _rows_many(
        """SELECT c.owner_user_id, c.source_key, c.text, s.kind, s.title, s.date, s.ref_json
           FROM sem_chunks c JOIN sem_sources s
             ON s.owner_user_id = c.owner_user_id AND s.source_key = c.source_key
           WHERE c.owner_user_id IN ({owners}) AND c.chunk_no = 0""",
        owners,
    )
    return [dict(r) for r in rs]


def source_hashes_many(owners: list[Any]) -> dict[tuple[str, str], str]:
    rs = _rows_many(
        "SELECT owner_user_id, source_key, content_hash FROM sem_sources WHERE owner_user_id IN ({owners})", owners
    )
    return {(r["owner_user_id"], r["source_key"]): r["content_hash"] for r in rs}


def find_copy(keys: list[str], content_hash: str, *, exclude_prefix: str) -> Optional[tuple[str, str]]:
    """Готовые векторы того же текста под одним из ключей keys в другом разделе (перенос без пересчёта)."""
    if not keys:
        return None
    with _LOCK:
        r = _conn().execute(
            f"""SELECT owner_user_id, source_key FROM sem_sources
                WHERE source_key IN ({",".join("?" * len(keys))}) AND content_hash = ?
                  AND substr(owner_user_id, 1, ?) != ?
                LIMIT 1""",
            (*keys, content_hash, len(exclude_prefix), exclude_prefix),
        ).fetchone()
    return (r["owner_user_id"], r["source_key"]) if r else None


def copy_source(src_owner: Any, src_key: str, owner: Any, key: str, *, content_hash: str, kind: str, title: str,
                date: str, ref_json: str) -> int:
    """Скопировать куски и векторы источника в другой раздел под новым ключом. Возвращает число кусков."""
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM sem_chunks WHERE owner_user_id = ? AND source_key = ?", (str(owner), key))
        n = c.execute(
            """INSERT INTO sem_chunks (owner_user_id, source_key, chunk_no, text, vec)
               SELECT ?, ?, chunk_no, text, vec FROM sem_chunks WHERE owner_user_id = ? AND source_key = ?""",
            (str(owner), key, str(src_owner), src_key),
        ).rowcount
        c.execute(
            """INSERT INTO sem_sources (owner_user_id, source_key, content_hash, kind, title, date, ref_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(owner_user_id, source_key) DO UPDATE SET content_hash = excluded.content_hash,
               kind = excluded.kind, title = excluded.title, date = excluded.date, ref_json = excluded.ref_json,
               updated_at = excluded.updated_at""",
            (str(owner), key, content_hash, kind, title, date, ref_json, now),
        )
        c.commit()
    return int(n or 0)


def owners_with_prefix(prefix: str) -> list[str]:
    with _LOCK:
        rs = _conn().execute(
            "SELECT DISTINCT owner_user_id FROM sem_sources WHERE substr(owner_user_id, 1, ?) = ?", (len(prefix), prefix)
        ).fetchall()
    return [r[0] for r in rs]


def sources_outside(prefix: str) -> list[tuple[str, str]]:
    """(раздел, ключ) всех источников вне разделов с prefix — найти старые копии документов."""
    with _LOCK:
        rs = _conn().execute(
            "SELECT owner_user_id, source_key FROM sem_sources WHERE substr(owner_user_id, 1, ?) != ?",
            (len(prefix), prefix),
        ).fetchall()
    return [(r[0], r[1]) for r in rs]


def delete_owner(owner: Any) -> None:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM sem_chunks WHERE owner_user_id = ?", (str(owner),))
        c.execute("DELETE FROM sem_sources WHERE owner_user_id = ?", (str(owner),))
        c.commit()


def totals() -> dict[str, int]:
    with _LOCK:
        c = _conn()
        s = c.execute("SELECT COUNT(*) FROM sem_sources").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0]
    return {"sources": int(s), "chunks": int(n)}


def search(owner: Any, qvec: list[float], *, k: int = 40) -> list[dict[str, Any]]:
    """Косинусная близость по всем кускам пользователя (векторы нормированы не всегда — нормируем)."""
    return search_many([owner], qvec, k=k)


def search_many(owners: list[Any], qvec: list[float], *, k: int = 40) -> list[dict[str, Any]]:
    """Как search, но по нескольким разделам сразу; owner_user_id — в каждом попадании."""
    rs = _rows_many(
        """SELECT c.owner_user_id, c.source_key, c.chunk_no, c.text, c.vec, s.kind, s.title, s.date, s.ref_json
           FROM sem_chunks c JOIN sem_sources s
             ON s.owner_user_id = c.owner_user_id AND s.source_key = c.source_key
           WHERE c.owner_user_id IN ({owners})""",
        owners,
    )
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
        {"score": s, "owner": r["owner_user_id"], "source_key": r["source_key"], "chunk_no": r["chunk_no"], "text": r["text"], "kind": r["kind"],
         "title": r["title"], "date": r["date"], "ref_json": r["ref_json"]}
        for s, r in scored
    ]


def stats(owner: Any) -> dict[str, int]:
    with _LOCK:
        c = _conn()
        s = c.execute("SELECT COUNT(*) FROM sem_sources WHERE owner_user_id = ?", (str(owner),)).fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM sem_chunks WHERE owner_user_id = ?", (str(owner),)).fetchone()[0]
    return {"sources": int(s), "chunks": int(n)}
