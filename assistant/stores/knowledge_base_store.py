"""Корпоративные базы знаний: SQLite + ACL owner/member."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

SourceType = Literal["google_docs", "notion", "yandex_disk", "bitrix24_knowledge"]
MemberRole = Literal["owner", "member"]
SyncStatus = Literal["pending", "syncing", "ok", "error"]


def _db_path() -> Path:
    raw = (os.getenv("KNOWLEDGE_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "data" / "knowledge_bases.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id TEXT PRIMARY KEY,
            owner_telegram_user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_meta_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL DEFAULT '',
            last_sync_at TEXT,
            sync_status TEXT NOT NULL DEFAULT 'pending',
            sync_error TEXT NOT NULL DEFAULT '',
            chunk_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_owner ON knowledge_bases(owner_telegram_user_id);

        CREATE TABLE IF NOT EXISTS kb_documents (
            doc_id TEXT NOT NULL,
            kb_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            modified_at TEXT,
            PRIMARY KEY (kb_id, doc_id),
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kb_docs_kb ON kb_documents(kb_id);

        CREATE TABLE IF NOT EXISTS kb_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kb_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            heading TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_kb ON kb_chunks(kb_id);

        CREATE TABLE IF NOT EXISTS kb_members (
            kb_id TEXT NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            granted_by INTEGER,
            granted_at TEXT NOT NULL,
            PRIMARY KEY (kb_id, telegram_user_id),
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kb_members_user ON kb_members(telegram_user_id);
        """
    )
    _CONN.commit()
    _ensure_kb_documents_url_column(_CONN)
    return _CONN


def _ensure_kb_documents_url_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(kb_documents)")}
    if "url" not in cols:
        conn.execute("ALTER TABLE kb_documents ADD COLUMN url TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _kb_row_to_dict(row: sqlite3.Row, *, role: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    try:
        raw = json.loads(row["source_meta_json"] or "{}")
        if isinstance(raw, dict):
            meta = raw
    except json.JSONDecodeError:
        pass
    out: dict[str, Any] = {
        "id": str(row["id"]),
        "owner_telegram_user_id": int(row["owner_telegram_user_id"]),
        "title": str(row["title"] or ""),
        "source_type": str(row["source_type"] or ""),
        "source_url": str(row["source_url"] or ""),
        "source_meta": meta,
        "content_hash": str(row["content_hash"] or ""),
        "last_sync_at": row["last_sync_at"],
        "sync_status": str(row["sync_status"] or "pending"),
        "sync_error": str(row["sync_error"] or ""),
        "chunk_count": int(row["chunk_count"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if role is not None:
        out["role"] = role
    return out


def create_knowledge_base(
    *,
    owner_telegram_user_id: int,
    title: str,
    source_type: SourceType,
    source_url: str,
    source_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kb_id = str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(source_meta or {}, ensure_ascii=False)
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO knowledge_bases (
                id, owner_telegram_user_id, title, source_type, source_url,
                source_meta_json, sync_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                kb_id,
                int(owner_telegram_user_id),
                (title or "").strip() or "База знаний",
                source_type,
                (source_url or "").strip(),
                meta_json,
                now,
                now,
            ),
        )
        c.execute(
            """
            INSERT INTO kb_members (kb_id, telegram_user_id, role, granted_by, granted_at)
            VALUES (?, ?, 'owner', ?, ?)
            """,
            (kb_id, int(owner_telegram_user_id), int(owner_telegram_user_id), now),
        )
        c.commit()
    kb = get_knowledge_base(kb_id)
    if not kb:
        raise RuntimeError("Не удалось создать базу знаний")
    return kb


def get_knowledge_base(kb_id: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM knowledge_bases WHERE id = ?",
            ((kb_id or "").strip(),),
        ).fetchone()
    if not row:
        return None
    return _kb_row_to_dict(row)


def list_knowledge_bases_for_user(telegram_user_id: int) -> list[dict[str, Any]]:
    uid = int(telegram_user_id)
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT kb.*, m.role AS member_role
            FROM knowledge_bases kb
            JOIN kb_members m ON m.kb_id = kb.id
            WHERE m.telegram_user_id = ?
            ORDER BY kb.updated_at DESC
            """,
            (uid,),
        ).fetchall()
    return [_kb_row_to_dict(r, role=str(r["member_role"])) for r in rows]


def user_has_accessible_kbs(telegram_user_id: int) -> bool:
    uid = int(telegram_user_id)
    with _LOCK:
        row = _conn().execute(
            "SELECT 1 FROM kb_members WHERE telegram_user_id = ? LIMIT 1",
            (uid,),
        ).fetchone()
    return row is not None


def user_can_access_kb(telegram_user_id: int, kb_id: str) -> bool:
    uid = int(telegram_user_id)
    with _LOCK:
        row = _conn().execute(
            """
            SELECT 1 FROM kb_members
            WHERE kb_id = ? AND telegram_user_id = ?
            """,
            ((kb_id or "").strip(), uid),
        ).fetchone()
    return row is not None


def user_is_kb_owner(telegram_user_id: int, kb_id: str) -> bool:
    uid = int(telegram_user_id)
    with _LOCK:
        row = _conn().execute(
            """
            SELECT 1 FROM kb_members
            WHERE kb_id = ? AND telegram_user_id = ? AND role = 'owner'
            """,
            ((kb_id or "").strip(), uid),
        ).fetchone()
    return row is not None


def delete_knowledge_base(kb_id: str, *, owner_telegram_user_id: int) -> bool:
    if not user_is_kb_owner(owner_telegram_user_id, kb_id):
        return False
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM kb_chunks WHERE kb_id = ?", (kb_id,))
        c.execute("DELETE FROM kb_documents WHERE kb_id = ?", (kb_id,))
        c.execute("DELETE FROM kb_members WHERE kb_id = ?", (kb_id,))
        cur = c.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
        c.commit()
    return cur.rowcount > 0


def update_sync_status(
    kb_id: str,
    *,
    sync_status: SyncStatus,
    sync_error: str = "",
    content_hash_value: str | None = None,
    chunk_count: int | None = None,
) -> None:
    now = _now_iso()
    fields = ["sync_status = ?", "sync_error = ?", "updated_at = ?"]
    params: list[Any] = [sync_status, (sync_error or "").strip(), now]
    if sync_status == "ok":
        fields.append("last_sync_at = ?")
        params.append(now)
    if content_hash_value is not None:
        fields.append("content_hash = ?")
        params.append(content_hash_value)
    if chunk_count is not None:
        fields.append("chunk_count = ?")
        params.append(int(chunk_count))
    params.append(kb_id)
    with _LOCK:
        _conn().execute(
            f"UPDATE knowledge_bases SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        _conn().commit()


def list_all_active_kbs() -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT * FROM knowledge_bases
            WHERE sync_status != 'error' OR last_sync_at IS NOT NULL
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [_kb_row_to_dict(r) for r in rows]


def replace_kb_index(
    kb_id: str,
    documents: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    *,
    aggregate_hash: str,
) -> None:
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM kb_chunks WHERE kb_id = ?", (kb_id,))
        c.execute("DELETE FROM kb_documents WHERE kb_id = ?", (kb_id,))
        for doc in documents:
            c.execute(
                """
                INSERT INTO kb_documents (doc_id, kb_id, title, content_hash, modified_at, url)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(doc["doc_id"]),
                    kb_id,
                    str(doc.get("title") or ""),
                    str(doc.get("content_hash") or ""),
                    doc.get("modified_at"),
                    str(doc.get("url") or ""),
                ),
            )
        for ch in chunks:
            c.execute(
                """
                INSERT INTO kb_chunks (kb_id, doc_id, heading, text, chunk_index)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    kb_id,
                    str(ch["doc_id"]),
                    str(ch.get("heading") or ""),
                    str(ch.get("text") or ""),
                    int(ch.get("chunk_index") or 0),
                ),
            )
        c.execute(
            """
            UPDATE knowledge_bases
            SET content_hash = ?, chunk_count = ?, sync_status = 'ok',
                sync_error = '', last_sync_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (aggregate_hash, len(chunks), _now_iso(), _now_iso(), kb_id),
        )
        c.commit()


def search_kb_chunks(
    kb_ids: list[str],
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    from assistant.lib.kb_search import score_query_against_haystack, tokenize_query

    if not kb_ids:
        return []
    if not tokenize_query(query):
        return []
    placeholders = ",".join("?" * len(kb_ids))
    with _LOCK:
        rows = _conn().execute(
            f"""
            SELECT c.id, c.kb_id, c.doc_id, c.heading, c.text, c.chunk_index,
                   d.title AS doc_title, d.url AS doc_url,
                   kb.title AS kb_title, kb.source_url AS kb_source_url,
                   kb.source_type AS kb_source_type
            FROM kb_chunks c
            JOIN kb_documents d ON d.kb_id = c.kb_id AND d.doc_id = c.doc_id
            JOIN knowledge_bases kb ON kb.id = c.kb_id
            WHERE c.kb_id IN ({placeholders})
            """,
            tuple(kb_ids),
        ).fetchall()
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        hay = f"{row['heading']} {row['text']} {row['doc_title']}"
        score = score_query_against_haystack(query, hay)
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "id": int(row["id"]),
                    "kb_id": str(row["kb_id"]),
                    "kb_title": str(row["kb_title"] or ""),
                    "kb_source_url": str(row["kb_source_url"] or ""),
                    "kb_source_type": str(row["kb_source_type"] or ""),
                    "doc_id": str(row["doc_id"]),
                    "doc_title": str(row["doc_title"] or ""),
                    "doc_url": str(row["doc_url"] or ""),
                    "heading": str(row["heading"] or ""),
                    "text": str(row["text"] or ""),
                    "chunk_index": int(row["chunk_index"] or 0),
                    "score": score,
                },
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1]["chunk_index"]))
    return [item for _, item in scored[:limit]]


def list_kb_chunks_for_doc(kb_id: str, doc_id: str) -> list[dict[str, Any]]:
    kb_id = (kb_id or "").strip()
    doc_id = (doc_id or "").strip()
    if not kb_id or not doc_id:
        return []
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT c.id, c.kb_id, c.doc_id, c.heading, c.text, c.chunk_index,
                   d.title AS doc_title, d.url AS doc_url,
                   kb.title AS kb_title, kb.source_url AS kb_source_url,
                   kb.source_type AS kb_source_type
            FROM kb_chunks c
            JOIN kb_documents d ON d.kb_id = c.kb_id AND d.doc_id = c.doc_id
            JOIN knowledge_bases kb ON kb.id = c.kb_id
            WHERE c.kb_id = ? AND c.doc_id = ?
            ORDER BY c.chunk_index ASC
            """,
            (kb_id, doc_id),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "kb_id": str(row["kb_id"]),
            "kb_title": str(row["kb_title"] or ""),
            "kb_source_url": str(row["kb_source_url"] or ""),
            "kb_source_type": str(row["kb_source_type"] or ""),
            "doc_id": str(row["doc_id"]),
            "doc_title": str(row["doc_title"] or ""),
            "doc_url": str(row["doc_url"] or ""),
            "heading": str(row["heading"] or ""),
            "text": str(row["text"] or ""),
            "chunk_index": int(row["chunk_index"] or 0),
            "score": 0.0,
        }
        for row in rows
    ]


def list_kb_members(kb_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT telegram_user_id, role, granted_by, granted_at
            FROM kb_members
            WHERE kb_id = ?
            ORDER BY role DESC, granted_at ASC
            """,
            ((kb_id or "").strip(),),
        ).fetchall()
    return [
        {
            "telegram_user_id": int(r["telegram_user_id"]),
            "role": str(r["role"]),
            "granted_by": int(r["granted_by"]) if r["granted_by"] is not None else None,
            "granted_at": r["granted_at"],
        }
        for r in rows
    ]


def add_kb_member(
    kb_id: str,
    *,
    member_telegram_user_id: int,
    granted_by: int,
) -> bool:
    if not user_is_kb_owner(granted_by, kb_id):
        return False
    uid = int(member_telegram_user_id)
    if user_is_kb_owner(uid, kb_id):
        return True
    now = _now_iso()
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO kb_members (kb_id, telegram_user_id, role, granted_by, granted_at)
            VALUES (?, ?, 'member', ?, ?)
            ON CONFLICT(kb_id, telegram_user_id) DO UPDATE SET
                role = excluded.role,
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            WHERE kb_members.role != 'owner'
            """,
            (kb_id, uid, int(granted_by), now),
        )
        c.commit()
    return True


def remove_kb_member(
    kb_id: str,
    *,
    member_telegram_user_id: int,
    revoked_by: int,
) -> bool:
    if not user_is_kb_owner(revoked_by, kb_id):
        return False
    uid = int(member_telegram_user_id)
    if user_is_kb_owner(uid, kb_id):
        return False
    with _LOCK:
        cur = _conn().execute(
            """
            DELETE FROM kb_members
            WHERE kb_id = ? AND telegram_user_id = ? AND role = 'member'
            """,
            (kb_id, uid),
        )
        _conn().commit()
    return cur.rowcount > 0


def detect_source_type(url: str) -> SourceType | None:
    u = (url or "").strip().lower()
    if not u:
        return None
    if "docs.google.com/document" in u or "docs.google.com/spreadsheets" in u:
        return "google_docs"
    if "notion.so" in u or "notion.site" in u:
        return "notion"
    if "disk.yandex" in u or "yadi.sk" in u:
        return "yandex_disk"
    if "/knowledge/" in u and re.search(r"bitrix24\.(ru|com|de|es|fr|pl|ua|by|kz)", u):
        return "bitrix24_knowledge"
    return None
