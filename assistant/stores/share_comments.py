"""Комментарии к расшаренным заметкам / записям журнала."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store
from assistant.stores import share_links as share_links_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_VALID_KINDS = frozenset({"local", "journal", "chat"})
MAX_BODY_LEN = 4000
MAX_GPT_BODY_LEN = 24000
MAX_QUOTE_LEN = 2000
MAX_CTX_LEN = 80


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS share_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            author_user_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_username TEXT,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            quote TEXT NOT NULL DEFAULT '',
            prefix TEXT NOT NULL DEFAULT '',
            suffix TEXT NOT NULL DEFAULT '',
            parent_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_comments_item "
        "ON share_comments(owner_user_id, item_kind, item_id, created_at)"
    )
    _ensure_anchor_columns(conn)
    conn.commit()
    return conn


def is_paie_comment(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    uname = str(row.get("author_username") or "").strip().lower()
    name = str(row.get("author_name") or "").strip().upper()
    return uname == "paie" or name == "CHAIR"


GPT_PREFIX = "__gpt__"
GPT_AUTHOR_NAME = "GPT"
GPT_AUTHOR_USERNAME = "gpt"
RESEARCH_PREFIX = "__research__"
RESEARCH_AUTHOR_NAME = "Research"
RESEARCH_AUTHOR_USERNAME = "research"
AGENT_PREFIX = "__agent__:"
AGENT_AUTHOR_USERNAME = "agent"


def is_gpt_comment(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    uname = str(row.get("author_username") or "").strip().lower()
    name = str(row.get("author_name") or "").strip().upper()
    return uname == "gpt" or name == "GPT"


def is_gpt_turn(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    if is_gpt_comment(row):
        return True
    return str(row.get("prefix") or "").strip() == GPT_PREFIX


def is_research_comment(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    uname = str(row.get("author_username") or "").strip().lower()
    name = str(row.get("author_name") or "").strip()
    return uname == RESEARCH_AUTHOR_USERNAME or name == RESEARCH_AUTHOR_NAME


def is_research_turn(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    if is_research_comment(row):
        return True
    return str(row.get("prefix") or "").strip() == RESEARCH_PREFIX


def agent_id_of(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    raw = str(row.get("prefix") or "").strip()
    if raw.startswith(AGENT_PREFIX):
        return raw[len(AGENT_PREFIX) :].strip()
    return ""


def is_agent_turn(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    if agent_id_of(row):
        return True
    uname = str(row.get("author_username") or "").strip().lower()
    return uname == AGENT_AUTHOR_USERNAME


def parent_id_of(row: dict[str, Any] | None) -> int | None:
    if not row:
        return None
    raw = row.get("parent_id")
    if raw in (None, "", 0, "0"):
        return None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def paie_thread(comments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = list(comments or [])
    ids: set[int] = set()
    for row in rows:
        if is_paie_comment(row):
            ids.add(int(row["id"]))
            pid = parent_id_of(row)
            if pid:
                ids.add(pid)
    changed = True
    while changed:
        changed = False
        for row in rows:
            cid = int(row["id"])
            if cid in ids:
                continue
            if is_gpt_turn(row) or is_research_turn(row) or is_agent_turn(row):
                continue
            pid = parent_id_of(row)
            if pid and pid in ids:
                ids.add(cid)
                changed = True
    return [
        row
        for row in rows
        if int(row["id"]) in ids
        and not is_gpt_turn(row)
        and not is_research_turn(row)
        and not is_agent_turn(row)
    ]


def paie_thread_root_id(comments: list[dict[str, Any]] | None) -> int | None:
    thread = paie_thread(comments)
    for row in thread:
        if is_paie_comment(row) and parent_id_of(row) is None:
            return int(row["id"])
    if thread:
        return int(thread[0]["id"])
    return None


def _ensure_anchor_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(share_comments)")}
    for col in ("quote", "prefix", "suffix"):
        if col not in cols:
            conn.execute(
                f"ALTER TABLE share_comments ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE share_comments ADD COLUMN parent_id INTEGER")
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def _kind_id(item_kind: str, item_id: str | int) -> tuple[str, str]:
    kind = (item_kind or "").strip()
    if kind not in _VALID_KINDS:
        raise ValueError("Некорректный тип записи")
    iid = str(item_id).strip()
    if not iid:
        raise ValueError("Не указан идентификатор записи")
    return kind, iid


def _clip(raw: str | None, limit: int) -> str:
    return str(raw or "").replace("\u0000", "")[:limit]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "item_kind": str(row["item_kind"] or ""),
        "item_id": str(row["item_id"] or ""),
        "author_user_id": str(row["author_user_id"] or ""),
        "author_name": str(row["author_name"] or ""),
        "author_username": str(row["author_username"] or "") or None,
        "body": str(row["body"] or ""),
        "created_at": row["created_at"],
        "quote": str(row["quote"] if "quote" in keys else "") or "",
        "prefix": str(row["prefix"] if "prefix" in keys else "") or "",
        "suffix": str(row["suffix"] if "suffix" in keys else "") or "",
        "parent_id": (
            int(row["parent_id"])
            if "parent_id" in keys and row["parent_id"] not in (None, "")
            else None
        ),
    }


def list_comments(
    owner_user_id: int | str, item_kind: str, item_id: str | int
) -> list[dict[str, Any]]:
    kind, iid = _kind_id(item_kind, item_id)
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM share_comments
            WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (uid, kind, iid),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_comment(comment_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM share_comments WHERE id = ?",
            (int(comment_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _resolve_parent_id(
    conn: sqlite3.Connection,
    owner: str,
    kind: str,
    iid: str,
    parent_id: int | str | None,
) -> int | None:
    if parent_id in (None, "", 0, "0"):
        return None
    try:
        pid = int(parent_id)
    except (TypeError, ValueError) as e:
        raise ValueError("Некорректный комментарий для ответа") from e
    if pid <= 0:
        return None
    cur = conn.execute(
        """
        SELECT id FROM share_comments
        WHERE id = ? AND owner_user_id = ? AND item_kind = ? AND item_id = ?
        """,
        (pid, owner, kind, iid),
    )
    if cur.fetchone() is None:
        raise ValueError("Комментарий для ответа не найден")
    return pid


def add_comment(
    owner_user_id: int | str,
    item_kind: str,
    item_id: str | int,
    *,
    author_user_id: int | str,
    author_name: str,
    author_username: str | None = None,
    body: str,
    quote: str = "",
    prefix: str = "",
    suffix: str = "",
    parent_id: int | str | None = None,
) -> dict[str, Any]:
    kind, iid = _kind_id(item_kind, item_id)
    text = (body or "").strip()
    if not text:
        raise ValueError("Введите текст комментария")
    uname = str(author_username or "").strip().lower()
    pre = str(prefix or "").strip()
    is_long = (
        pre == GPT_PREFIX
        or pre == RESEARCH_PREFIX
        or pre.startswith(AGENT_PREFIX)
        or uname == GPT_AUTHOR_USERNAME
        or uname == RESEARCH_AUTHOR_USERNAME
        or uname == AGENT_AUTHOR_USERNAME
    )
    limit = MAX_GPT_BODY_LEN if is_long else MAX_BODY_LEN
    if len(text) > limit:
        raise ValueError(f"Комментарий слишком длинный (максимум {limit} символов)")
    q = _clip(quote, MAX_QUOTE_LEN).strip()
    pre = _clip(prefix, MAX_CTX_LEN)
    suf = _clip(suffix, MAX_CTX_LEN)
    owner = _uid(owner_user_id)
    author = _uid(author_user_id)
    name = (author_name or "").strip() or "Пользователь"
    uname = (author_username or "").strip().lstrip("@") or None
    ts = _now_iso()
    with _LOCK:
        conn = _conn()
        pid = _resolve_parent_id(conn, owner, kind, iid, parent_id)
        cur = conn.execute(
            """
            INSERT INTO share_comments (
                owner_user_id, item_kind, item_id,
                author_user_id, author_name, author_username,
                body, created_at, quote, prefix, suffix, parent_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (owner, kind, iid, author, name[:120], uname, text, ts, q, pre, suf, pid),
        )
        conn.commit()
        cid = int(cur.lastrowid)
    item = get_comment(cid)
    if not item:
        raise RuntimeError("Не удалось сохранить комментарий")
    return item


def delete_comment(
    comment_id: int, *, requester_user_id: int | str
) -> bool:
    item = get_comment(comment_id)
    if not item:
        return False
    req = _uid(requester_user_id)
    if req != item["author_user_id"] and req != item["owner_user_id"]:
        return False
    try:
        from assistant.stores import comment_files

        comment_files.delete_for_comment(int(comment_id))
    except Exception:
        pass
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM share_comments WHERE id = ?",
            (int(comment_id),),
        )
        _conn().commit()
        return cur.rowcount > 0


def delete_all_for_item(
    owner_user_id: int | str, item_kind: str, item_id: str | int
) -> int:
    try:
        kind, iid = _kind_id(item_kind, item_id)
    except ValueError:
        return 0
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            DELETE FROM share_comments
            WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?
            """,
            (uid, kind, iid),
        )
        _conn().commit()
        return int(cur.rowcount or 0)


def comments_allowed_for_link(link: dict[str, Any] | None) -> bool:
    if not link:
        return False
    return share_links_store.normalize_access(link.get("access")) == "comment"


def comments_visible_for_link(link: dict[str, Any] | None) -> bool:
    return bool(link)


def _comment_is_assistant(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return bool(
        is_gpt_turn(row)
        or is_paie_comment(row)
        or is_research_turn(row)
        or is_agent_turn(row)
    )


def latest_briefs(
    keys: list[tuple[str, str, str]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Latest comment meta for (owner_user_id, item_kind, item_id) triples."""
    want: set[tuple[str, str, str]] = set()
    for owner, kind, item_id in keys:
        o = str(owner or "").strip()
        k = str(kind or "").strip()
        i = str(item_id or "").strip()
        if not o or k not in _VALID_KINDS or not i:
            continue
        want.add((o, k, i))
    if not want:
        return {}
    owners = sorted({o for o, _, _ in want})
    kinds = sorted({k for _, k, _ in want})
    item_ids = sorted({i for _, _, i in want})
    own_ph = ",".join("?" for _ in owners)
    kind_ph = ",".join("?" for _ in kinds)
    id_ph = ",".join("?" for _ in item_ids)
    with _LOCK:
        cur = _conn().execute(
            f"""
            SELECT owner_user_id, item_kind, item_id, MAX(id) AS mid
            FROM share_comments
            WHERE owner_user_id IN ({own_ph})
              AND item_kind IN ({kind_ph})
              AND item_id IN ({id_ph})
            GROUP BY owner_user_id, item_kind, item_id
            """,
            (*owners, *kinds, *item_ids),
        )
        max_rows = [
            (str(r["owner_user_id"]), str(r["item_kind"]), str(r["item_id"]), int(r["mid"]))
            for r in cur.fetchall()
            if (str(r["owner_user_id"]), str(r["item_kind"]), str(r["item_id"])) in want
        ]
        if not max_rows:
            return {}
        mids = [mid for *_, mid in max_rows]
        mid_ph = ",".join("?" for _ in mids)
        cur = _conn().execute(
            f"SELECT * FROM share_comments WHERE id IN ({mid_ph})",
            mids,
        )
        by_id = {int(r["id"]): _row_to_dict(r) for r in cur.fetchall()}
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for owner, kind, item_id, mid in max_rows:
        row = by_id.get(mid)
        if not row:
            continue
        out[(owner, kind, item_id)] = {
            "id": int(row["id"]),
            "author_user_id": str(row.get("author_user_id") or ""),
            "author_name": str(row.get("author_name") or ""),
            "created_at": row.get("created_at"),
            "is_assistant": _comment_is_assistant(row),
        }
    return out


def attach_discuss_latest(
    items: list[dict[str, Any]],
    *,
    kind: str,
    owner_key: str = "owner_user_id",
    id_key: str = "id",
) -> None:
    """Mutate items in place, setting discuss_latest from share_comments."""
    keys: list[tuple[str, str, str]] = []
    for item in items:
        owner = str(item.get(owner_key) or item.get("user_id") or "").strip()
        iid = str(item.get(id_key) or "").strip()
        if owner and iid:
            keys.append((owner, kind, iid))
    briefs = latest_briefs(keys)
    for item in items:
        owner = str(item.get(owner_key) or item.get("user_id") or "").strip()
        iid = str(item.get(id_key) or "").strip()
        item["discuss_latest"] = briefs.get((owner, kind, iid)) if owner and iid else None
