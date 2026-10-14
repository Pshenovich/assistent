"""Чаты обсуждения заметки: несколько чатов с ИИ и общий чат «Комментарии».

Сообщения живут в share_comments; поле chat_id указывает чат:
- COMMENTS_CHAT_ID (-1) — «Комментарии», общий для всех участников заметки;
- id из note_chats — чат с ИИ, видимость «Только я» (private) или «Все участники» (shared);
- NULL — сообщение до появления чатов; разносится по чатам при первом чтении
  (migrate_legacy).
Чаты есть только у заметок (item_kind == "local").
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store
from assistant.stores import share_comments as sc

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

COMMENTS_CHAT_ID = -1
CHAT_KINDS = frozenset({"local"})
VIS_PRIVATE = "private"
VIS_SHARED = "shared"
MAX_TITLE_LEN = 120

def _conn():
    return sc._conn()  # type: ignore[attr-defined]  # creates note_chats and share_comments.chat_id


def ensure_schema() -> None:
    with _LOCK:
        _conn()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_visibility(raw: Any) -> str:
    return VIS_SHARED if str(raw or "").strip().lower() in (VIS_SHARED, "all", "public") else VIS_PRIVATE


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "owner_user_id": str(r["owner_user_id"]),
        "item_kind": str(r["item_kind"]),
        "item_id": str(r["item_id"]),
        "author_user_id": str(r["author_user_id"]),
        "title": str(r["title"] or ""),
        "visibility": normalize_visibility(r["visibility"]),
        "origin": str(r["origin"] or ""),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def supports_chats(item_kind: str) -> bool:
    return str(item_kind or "") in CHAT_KINDS


def get_chat(chat_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute("SELECT * FROM note_chats WHERE id = ?", (int(chat_id),)).fetchone()
    return _row(r) if r else None


def can_view(chat: Optional[dict[str, Any]], viewer_uid: Optional[str]) -> bool:
    if not chat:
        return False
    if chat["visibility"] == VIS_SHARED:
        return True
    return bool(viewer_uid) and str(viewer_uid) == chat["author_user_id"]


def chat_for_item(chat_id: Any, owner: str, kind: str, item_id: str) -> Optional[dict[str, Any]]:
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return None
    chat = get_chat(cid)
    if not chat or chat["owner_user_id"] != str(owner) or chat["item_kind"] != kind or chat["item_id"] != str(item_id):
        return None
    return chat


def create_chat(
    owner: str, kind: str, item_id: str, *, author: str, title: str = "", visibility: str = VIS_PRIVATE, origin: str = ""
) -> dict[str, Any]:
    if not supports_chats(kind):
        raise ValueError("Чаты есть только у заметок")
    ts = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO note_chats (owner_user_id, item_kind, item_id, author_user_id, title,
                                    visibility, origin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(owner), kind, str(item_id), str(author), str(title or "").strip()[:MAX_TITLE_LEN],
             normalize_visibility(visibility), origin, ts, ts),
        )
        conn.commit()
        cid = int(cur.lastrowid)
    chat = get_chat(cid)
    assert chat is not None
    return chat


def update_chat(chat: dict[str, Any], *, title: Optional[str] = None, visibility: Optional[str] = None) -> dict[str, Any]:
    sets, args = [], []
    if title is not None:
        sets.append("title = ?")
        args.append(str(title).strip()[:MAX_TITLE_LEN])
    if visibility is not None:
        sets.append("visibility = ?")
        args.append(normalize_visibility(visibility))
    if not sets:
        return chat
    sets.append("updated_at = ?")
    args.append(_now())
    args.append(int(chat["id"]))
    with _LOCK:
        conn = _conn()
        conn.execute(f"UPDATE note_chats SET {', '.join(sets)} WHERE id = ?", args)
        conn.commit()
    return get_chat(int(chat["id"])) or chat


def _is_assistant_side(row: dict[str, Any]) -> bool:
    return bool(sc.is_gpt_turn(row) or sc.is_research_turn(row) or sc.is_agent_turn(row) or sc.is_paie_comment(row))


def migrate_legacy(owner: str, kind: str, item_id: str) -> None:
    """Разнести сообщения без chat_id: комментарии к тексту → «Комментарии», остальное → чат «Обсуждение».

    Ответы наследуют чат родителя. Новые сообщения от старых клиентов (без chat_id)
    попадают туда же — в последний чат-наследник или в «Комментарии».
    """
    if not supports_chats(kind):
        return
    with _LOCK:
        conn = _conn()
        rows = [
            sc._row_to_dict(r)  # type: ignore[attr-defined]
            for r in conn.execute(
                "SELECT * FROM share_comments WHERE owner_user_id = ? AND item_kind = ? AND item_id = ? "
                "ORDER BY id ASC",
                (str(owner), kind, str(item_id)),
            ).fetchall()
        ]
        pending = [r for r in rows if r.get("chat_id") is None]
        if not pending:
            return
        paie_ids = {int(r["id"]) for r in sc.paie_thread(rows)}
        chat_of: dict[int, int] = {int(r["id"]): int(r["chat_id"]) for r in rows if r.get("chat_id") is not None}
        legacy = conn.execute(
            "SELECT id FROM note_chats WHERE owner_user_id = ? AND item_kind = ? AND item_id = ? "
            "AND origin IN ('legacy', 'legacy-split') ORDER BY id DESC LIMIT 1",
            (str(owner), kind, str(item_id)),
        ).fetchone()
        legacy_id = int(legacy["id"]) if legacy else None
        updates: list[tuple[int, int]] = []
        for r in pending:
            rid = int(r["id"])
            pid = sc.parent_id_of(r)
            if pid and pid in chat_of and chat_of[pid] == COMMENTS_CHAT_ID and not _is_assistant_side(r):
                target = COMMENTS_CHAT_ID
            elif (
                not _is_assistant_side(r)
                and rid not in paie_ids
                and str(r.get("quote") or "").strip()
                and (not pid or chat_of.get(pid) in (None, COMMENTS_CHAT_ID))
            ):
                target = COMMENTS_CHAT_ID
            else:
                if legacy_id is None:
                    ts = _now()
                    cur = conn.execute(
                        """
                        INSERT INTO note_chats (owner_user_id, item_kind, item_id, author_user_id, title,
                                                visibility, origin, created_at, updated_at)
                        VALUES (?, ?, ?, ?, '', 'shared', 'legacy', ?, ?)
                        """,
                        (str(owner), kind, str(item_id), str(owner), str(r.get("created_at") or ts), ts),
                    )
                    legacy_id = int(cur.lastrowid)
                target = legacy_id
            chat_of[rid] = target
            updates.append((target, rid))
        conn.executemany("UPDATE share_comments SET chat_id = ? WHERE id = ?", updates)
        conn.commit()


def split_legacy(chat: dict[str, Any], after_comment_id: int, *, requester: str) -> Optional[dict[str, Any]]:
    """Сброс контекста из браузера (до чатов) → отдельный чат: сообщения после after_comment_id."""
    if chat.get("origin") != "legacy":
        return None
    with _LOCK:
        conn = _conn()
        moved = conn.execute(
            "SELECT COUNT(*) FROM share_comments WHERE chat_id = ? AND id > ?",
            (int(chat["id"]), int(after_comment_id)),
        ).fetchone()[0]
        before = conn.execute(
            "SELECT COUNT(*) FROM share_comments WHERE chat_id = ? AND id <= ?",
            (int(chat["id"]), int(after_comment_id)),
        ).fetchone()[0]
        # Помечаем разбор сделанным в любом случае, чтобы не повторять.
        conn.execute("UPDATE note_chats SET origin = 'legacy-done' WHERE id = ?", (int(chat["id"]),))
        conn.commit()
        if not moved or not before:
            return None
        ts = _now()
        cur = conn.execute(
            """
            INSERT INTO note_chats (owner_user_id, item_kind, item_id, author_user_id, title,
                                    visibility, origin, created_at, updated_at)
            VALUES (?, ?, ?, ?, '', 'shared', 'legacy-split', ?, ?)
            """,
            (chat["owner_user_id"], chat["item_kind"], chat["item_id"], str(requester), ts, ts),
        )
        new_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE share_comments SET chat_id = ? WHERE chat_id = ? AND id > ?",
            (new_id, int(chat["id"]), int(after_comment_id)),
        )
        conn.commit()
    return get_chat(new_id)


def visible_comments(owner: str, kind: str, item_id: str, viewer_uid: Optional[str]) -> list[dict[str, Any]]:
    """Сообщения заметки без чужих личных чатов (viewer_uid=None — только общие)."""
    migrate_legacy(owner, kind, item_id)
    rows = sc.list_comments(owner, kind, item_id)
    if not supports_chats(kind):
        return rows
    hidden = hidden_chat_ids(owner, kind, item_id, viewer_uid)
    if not hidden:
        return rows
    return [r for r in rows if r.get("chat_id") not in hidden]


def chat_context_comments(
    owner: str, kind: str, item_id: str, viewer_uid: Optional[str], chat_id: Optional[int]
) -> list[dict[str, Any]]:
    """Контекст для агентов (CHAIR, Research): видимое запустившему и только из его чата."""
    if not supports_chats(kind) or (viewer_uid is None and chat_id is None):
        return sc.list_comments(owner, kind, item_id)
    rows = visible_comments(owner, kind, item_id, viewer_uid)
    if chat_id is None:
        return rows
    return [r for r in rows if r.get("chat_id") == int(chat_id)]


def hidden_chat_ids(owner: str, kind: str, item_id: str, viewer_uid: Optional[str]) -> set[int]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT id, author_user_id FROM note_chats WHERE owner_user_id = ? AND item_kind = ? "
            "AND item_id = ? AND visibility = 'private'",
            (str(owner), kind, str(item_id)),
        ).fetchall()
    return {int(r["id"]) for r in rows if not viewer_uid or str(r["author_user_id"]) != str(viewer_uid)}


def comment_visible(row: Optional[dict[str, Any]], viewer_uid: Optional[str]) -> bool:
    if not row:
        return False
    cid = row.get("chat_id")
    if cid is None or int(cid) == COMMENTS_CHAT_ID:
        return True
    return can_view(get_chat(int(cid)), viewer_uid)


READ_BASE_CHAT_ID = 0


def read_marks(owner: str, kind: str, item_id: str, viewer_uid: str) -> tuple[int, dict[int, int]]:
    """(точка отсчёта, {chat_id: id последнего прочитанного}) для пользователя.

    При первом обращении точкой отсчёта становится последнее сообщение заметки —
    старые сообщения не считаются новыми.
    """
    key = (str(viewer_uid), str(owner), kind, str(item_id))
    with _LOCK:
        conn = _conn()
        marks = {
            int(r["chat_id"]): int(r["last_seen_id"])
            for r in conn.execute(
                "SELECT chat_id, last_seen_id FROM note_chat_reads "
                "WHERE user_id = ? AND owner_user_id = ? AND item_kind = ? AND item_id = ?",
                key,
            ).fetchall()
        }
        if READ_BASE_CHAT_ID not in marks:
            row = conn.execute(
                "SELECT MAX(id) AS m FROM share_comments WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?",
                key[1:],
            ).fetchone()
            base = int((row and row["m"]) or 0)
            conn.execute(
                "INSERT OR IGNORE INTO note_chat_reads VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, READ_BASE_CHAT_ID, base, _now()),
            )
            conn.commit()
            marks[READ_BASE_CHAT_ID] = base
    base = marks.pop(READ_BASE_CHAT_ID)
    return base, marks


def mark_read(owner: str, kind: str, item_id: str, viewer_uid: str, chat_id: int, last_id: int) -> int:
    """Запомнить, что пользователь видел чат до сообщения last_id. Возвращает итоговую отметку."""
    key = (str(viewer_uid), str(owner), kind, str(item_id), int(chat_id))
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT MAX(id) AS m FROM share_comments WHERE owner_user_id = ? AND item_kind = ? AND item_id = ? AND chat_id = ?",
            key[1:],
        ).fetchone()
        last_id = min(int(last_id), int((row and row["m"]) or 0))
        conn.execute(
            "INSERT INTO note_chat_reads VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, owner_user_id, item_kind, item_id, chat_id) DO UPDATE SET "
            "last_seen_id = MAX(last_seen_id, excluded.last_seen_id), updated_at = excluded.updated_at",
            (*key, max(0, last_id), _now()),
        )
        conn.commit()
        got = conn.execute(
            "SELECT last_seen_id FROM note_chat_reads WHERE user_id = ? AND owner_user_id = ? "
            "AND item_kind = ? AND item_id = ? AND chat_id = ?",
            key,
        ).fetchone()
    return int(got["last_seen_id"]) if got else 0


def list_chats(owner: str, kind: str, item_id: str, viewer_uid: str) -> list[dict[str, Any]]:
    """Видимые чаты с ИИ (без «Комментариев»), свежие сверху, со статистикой."""
    migrate_legacy(owner, kind, item_id)
    with _LOCK:
        conn = _conn()
        chats = [
            _row(r)
            for r in conn.execute(
                "SELECT * FROM note_chats WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?",
                (str(owner), kind, str(item_id)),
            ).fetchall()
        ]
        stats = {
            int(r["chat_id"]): (int(r["n"]), r["last_at"], int(r["first_id"]))
            for r in conn.execute(
                "SELECT chat_id, COUNT(*) AS n, MAX(created_at) AS last_at, MIN(id) AS first_id "
                "FROM share_comments WHERE owner_user_id = ? AND item_kind = ? AND item_id = ? "
                "AND chat_id IS NOT NULL GROUP BY chat_id",
                (str(owner), kind, str(item_id)),
            ).fetchall()
        }
        firsts = {}
        ids = [v[2] for v in stats.values()]
        if ids:
            q = ",".join("?" * len(ids))
            for r in conn.execute(f"SELECT id, body FROM share_comments WHERE id IN ({q})", ids).fetchall():
                firsts[int(r["id"])] = str(r["body"] or "")
    out = []
    for c in chats:
        if not can_view(c, viewer_uid):
            continue
        n, last_at, first_id = stats.get(c["id"], (0, None, 0))
        c["message_count"] = n
        c["last_at"] = last_at or c["created_at"]
        c["first_message"] = " ".join(firsts.get(first_id, "").split())[:140]
        c["is_mine"] = c["author_user_id"] == str(viewer_uid)
        out.append(c)
    out.sort(key=lambda c: str(c["last_at"] or ""), reverse=True)
    comments = stats.get(COMMENTS_CHAT_ID, (0, None, 0))
    out.append(
        {
            "id": COMMENTS_CHAT_ID,
            "title": "Комментарии",
            "visibility": VIS_SHARED,
            "message_count": comments[0],
            "last_at": comments[1],
            "is_comments": True,
        }
    )
    base, marks = read_marks(owner, kind, item_id, viewer_uid)
    for c in out:
        c["last_seen_id"] = max(base, marks.get(int(c["id"]), 0))
    return out
