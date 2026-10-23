"""Индекс писем: заголовки и короткий фрагмент (полные тексты не храним), FTS для поиска, 90 дней."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

RETENTION = timedelta(days=90)
SNIPPET = 500

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS mail_messages (
            owner_user_id TEXT NOT NULL,
            mail_key TEXT NOT NULL,
            account_id TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT 'INBOX',
            uid INTEGER NOT NULL DEFAULT 0,
            message_id TEXT NOT NULL DEFAULT '',
            thread_key TEXT NOT NULL DEFAULT '',
            in_reply_to TEXT NOT NULL DEFAULT '',
            refs TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT 'in',
            from_addr TEXT NOT NULL DEFAULT '',
            from_name TEXT NOT NULL DEFAULT '',
            to_json TEXT NOT NULL DEFAULT '[]',
            cc_json TEXT NOT NULL DEFAULT '[]',
            reply_to TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            date_utc TEXT NOT NULL DEFAULT '',
            snippet TEXT NOT NULL DEFAULT '',
            bulk INTEGER NOT NULL DEFAULT 0,
            has_attachments INTEGER NOT NULL DEFAULT 0,
            analyzed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (owner_user_id, mail_key)
        );
        CREATE INDEX IF NOT EXISTS mail_messages_thread ON mail_messages (owner_user_id, thread_key);
        CREATE INDEX IF NOT EXISTS mail_messages_msgid ON mail_messages (owner_user_id, message_id);
        CREATE INDEX IF NOT EXISTS mail_messages_date ON mail_messages (owner_user_id, date_utc);
        CREATE VIRTUAL TABLE IF NOT EXISTS mail_fts USING fts5(
            owner_user_id UNINDEXED, mail_key UNINDEXED, subject, people, snippet, tokenize = 'unicode61'
        );
        CREATE TABLE IF NOT EXISTS mail_sync_state (
            owner_user_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            folder TEXT NOT NULL,
            uidvalidity INTEGER NOT NULL DEFAULT 0,
            last_uid INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (owner_user_id, account_id, folder)
        );
        """
    )
    _CONN.commit()
    return _CONN


def mail_key(account_id: str, message_id: str, folder: str, uid: int) -> str:
    base = message_id or f"{folder}:{uid}"
    return f"{account_id}:" + hashlib.sha1(base.encode("utf-8", errors="replace")).hexdigest()[:16]


def thread_key_for(message_id: str, in_reply_to: str, refs: list[str]) -> str:
    root = (refs[0] if refs else "") or in_reply_to or message_id
    return hashlib.sha1(root.encode("utf-8", errors="replace")).hexdigest()[:16] if root else ""


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["to"] = json.loads(d.pop("to_json") or "[]")
    d["cc"] = json.loads(d.pop("cc_json") or "[]")
    d["refs"] = [x for x in str(d.get("refs") or "").split() if x]
    d["bulk"] = bool(d.get("bulk"))
    return d


def upsert(owner_user_id: Any, row: dict[str, Any]) -> bool:
    """Записать письмо; True — новое."""
    owner = str(owner_user_id)
    key = row["mail_key"]
    people = " ".join([str(row.get("from_name") or ""), str(row.get("from_addr") or "")]
                      + [str(a) for a in row.get("to") or []] + [str(a) for a in row.get("cc") or []])
    with _LOCK:
        conn = _conn()
        exists = conn.execute(
            "SELECT 1 FROM mail_messages WHERE owner_user_id = ? AND mail_key = ?", (owner, key)
        ).fetchone()
        if exists:
            return False
        conn.execute(
            """
            INSERT INTO mail_messages (owner_user_id, mail_key, account_id, folder, uid, message_id, thread_key,
                in_reply_to, refs, direction, from_addr, from_name, to_json, cc_json, reply_to, subject, date_utc,
                snippet, bulk, has_attachments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (owner, key, row["account_id"], row.get("folder") or "INBOX", int(row.get("uid") or 0),
             row.get("message_id") or "", row.get("thread_key") or "", row.get("in_reply_to") or "",
             " ".join(row.get("refs") or []), row.get("direction") or "in", row.get("from_addr") or "",
             row.get("from_name") or "", json.dumps(row.get("to") or []), json.dumps(row.get("cc") or []),
             row.get("reply_to") or "", (row.get("subject") or "")[:300], row.get("date_utc") or "",
             (row.get("snippet") or "")[:SNIPPET], int(bool(row.get("bulk"))), int(bool(row.get("has_attachments")))),
        )
        if not row.get("bulk"):
            conn.execute(
                "INSERT INTO mail_fts (owner_user_id, mail_key, subject, people, snippet) VALUES (?, ?, ?, ?, ?)",
                (owner, key, row.get("subject") or "", people, (row.get("snippet") or "")[:SNIPPET]),
            )
        conn.commit()
    return True


def get(owner_user_id: Any, key: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM mail_messages WHERE owner_user_id = ? AND mail_key = ?", (str(owner_user_id), key)
        ).fetchone()
    return _row(r) if r else None


def thread(owner_user_id: Any, thread_key: str) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM mail_messages WHERE owner_user_id = ? AND thread_key = ? ORDER BY date_utc",
            (str(owner_user_id), thread_key),
        ).fetchall()
    return [_row(r) for r in rows]


def recent(owner_user_id: Any, *, since: datetime, direction: Optional[str] = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM mail_messages WHERE owner_user_id = ? AND date_utc >= ?"
    args: list[Any] = [str(owner_user_id), since.isoformat()]
    if direction:
        q += " AND direction = ?"
        args.append(direction)
    with _LOCK:
        rows = _conn().execute(q + " ORDER BY date_utc", args).fetchall()
    return [_row(r) for r in rows]


def unanalyzed(owner_user_id: Any, *, since: datetime) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM mail_messages WHERE owner_user_id = ? AND analyzed = 0 AND bulk = 0 AND date_utc >= ?"
            " ORDER BY date_utc",
            (str(owner_user_id), since.isoformat()),
        ).fetchall()
    return [_row(r) for r in rows]


def mark_analyzed(owner_user_id: Any, keys: list[str], value: int = 1) -> None:
    if not keys:
        return
    with _LOCK:
        conn = _conn()
        conn.executemany(
            "UPDATE mail_messages SET analyzed = ? WHERE owner_user_id = ? AND mail_key = ?",
            [(value, str(owner_user_id), k) for k in keys],
        )
        conn.commit()


def correspondents(owner_user_id: Any) -> set[str]:
    """Адреса, которым пользователь сам писал (из «Отправленных»)."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT to_json, cc_json FROM mail_messages WHERE owner_user_id = ? AND direction = 'out'",
            (str(owner_user_id),),
        ).fetchall()
    out: set[str] = set()
    for r in rows:
        out.update(json.loads(r["to_json"] or "[]"))
        out.update(json.loads(r["cc_json"] or "[]"))
    return out


def my_thread_keys(owner_user_id: Any) -> set[str]:
    """Ветки, в которых пользователь писал."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT DISTINCT thread_key FROM mail_messages WHERE owner_user_id = ? AND direction = 'out' AND thread_key != ''",
            (str(owner_user_id),),
        ).fetchall()
    return {str(r["thread_key"]) for r in rows}


def replied(owner_user_id: Any, row: dict[str, Any]) -> bool:
    """Пользователь ответил на письмо (или писал в ветку позже него)."""
    mid = str(row.get("message_id") or "")
    with _LOCK:
        conn = _conn()
        if mid:
            hit = conn.execute(
                "SELECT 1 FROM mail_messages WHERE owner_user_id = ? AND direction = 'out' AND (in_reply_to = ? OR refs LIKE ?)",
                (str(owner_user_id), mid, f"%{mid}%"),
            ).fetchone()
            if hit:
                return True
        if row.get("thread_key"):
            hit = conn.execute(
                "SELECT 1 FROM mail_messages WHERE owner_user_id = ? AND direction = 'out' AND thread_key = ? AND date_utc > ?",
                (str(owner_user_id), row["thread_key"], str(row.get("date_utc") or "")),
            ).fetchone()
            if hit:
                return True
    return False


def answered_by_them(owner_user_id: Any, row: dict[str, Any]) -> bool:
    """На письмо пользователя ответили (для «Слежу за ответом»)."""
    if not row.get("thread_key"):
        return False
    with _LOCK:
        hit = _conn().execute(
            "SELECT 1 FROM mail_messages WHERE owner_user_id = ? AND direction = 'in' AND thread_key = ? AND date_utc > ?",
            (str(owner_user_id), row["thread_key"], str(row.get("date_utc") or "")),
        ).fetchone()
    return bool(hit)


def with_people(owner_user_id: Any, emails: list[str], *, since: datetime, limit: int = 3) -> list[dict[str, Any]]:
    """Последние ветки переписки с этими людьми (по одному письму на ветку)."""
    em = [e.strip().lower() for e in emails if e and "@" in e]
    if not em:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in reversed(recent(owner_user_id, since=since)):
        if r.get("bulk"):
            continue
        people = {r.get("from_addr")} | set(r.get("to") or []) | set(r.get("cc") or [])
        if not (people & set(em)):
            continue
        tk = r.get("thread_key") or r["mail_key"]
        if tk in seen:
            continue
        seen.add(tk)
        out.append(r)
        if len(out) >= limit:
            break
    return out


_STOP = {"что", "про", "как", "где", "все", "мне", "мой", "моя", "мои", "это", "его", "её", "они", "писал", "писала",
         "писали", "письмо", "письма", "почта", "почте", "найди", "покажи", "был", "была", "было", "когда", "какой"}


def _fts_query(q: str) -> str:
    """Слова через OR, с грубой основой: «смету» найдёт «смета», «сметой»."""
    words = [w for w in re.findall(r"[\w@.-]{3,}", q.lower()) if w not in _STOP]
    stems = []
    for w in words[:8]:
        stem = w if "@" in w or "." in w else w[: max(4, len(w) - 2)]
        stems.append('"' + stem.replace('"', "") + '"*')
    return " OR ".join(stems)


def search(owner_user_id: Any, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    fq = _fts_query(query)
    if not fq:
        return []
    with _LOCK:
        conn = _conn()
        try:
            keys = [str(r["mail_key"]) for r in conn.execute(
                "SELECT mail_key FROM mail_fts WHERE mail_fts MATCH ? AND owner_user_id = ? ORDER BY rank LIMIT ?",
                (fq, str(owner_user_id), limit * 3),
            ).fetchall()]
        except sqlite3.OperationalError:
            return []
    rows = [r for r in (get(owner_user_id, k) for k in keys) if r]
    rows.sort(key=lambda r: str(r.get("date_utc") or ""), reverse=True)
    return rows[:limit]


def sync_state(owner_user_id: Any, account_id: str, folder: str) -> tuple[int, int]:
    with _LOCK:
        r = _conn().execute(
            "SELECT uidvalidity, last_uid FROM mail_sync_state WHERE owner_user_id = ? AND account_id = ? AND folder = ?",
            (str(owner_user_id), account_id, folder),
        ).fetchone()
    return (int(r["uidvalidity"]), int(r["last_uid"])) if r else (0, 0)


def set_sync_state(owner_user_id: Any, account_id: str, folder: str, uidvalidity: int, last_uid: int) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO mail_sync_state (owner_user_id, account_id, folder, uidvalidity, last_uid) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, account_id, folder) DO UPDATE SET uidvalidity = excluded.uidvalidity,
                last_uid = excluded.last_uid
            """,
            (str(owner_user_id), account_id, folder, int(uidvalidity), int(last_uid)),
        )
        conn.commit()


def purge_account(owner_user_id: Any, account_id: str) -> None:
    owner = str(owner_user_id)
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM mail_fts WHERE owner_user_id = ? AND mail_key LIKE ?", (owner, f"{account_id}:%"))
        conn.execute("DELETE FROM mail_messages WHERE owner_user_id = ? AND account_id = ?", (owner, account_id))
        conn.execute("DELETE FROM mail_sync_state WHERE owner_user_id = ? AND account_id = ?", (owner, account_id))
        conn.commit()


def purge_old(now: Optional[datetime] = None) -> int:
    cutoff = ((now or datetime.now(timezone.utc)) - RETENTION).isoformat()
    with _LOCK:
        conn = _conn()
        keys = conn.execute(
            "SELECT owner_user_id, mail_key FROM mail_messages WHERE date_utc < ? AND direction = 'in'", (cutoff,)
        ).fetchall()
        conn.executemany("DELETE FROM mail_fts WHERE owner_user_id = ? AND mail_key = ?",
                         [(r["owner_user_id"], r["mail_key"]) for r in keys])
        conn.execute("DELETE FROM mail_messages WHERE date_utc < ? AND direction = 'in'", (cutoff,))
        # исходящие храним дольше (180 дней) — по ним «знакомые отправители»
        out_cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=180)).isoformat()
        old_out = conn.execute(
            "SELECT owner_user_id, mail_key FROM mail_messages WHERE date_utc < ? AND direction = 'out'", (out_cutoff,)
        ).fetchall()
        conn.executemany("DELETE FROM mail_fts WHERE owner_user_id = ? AND mail_key = ?",
                         [(r["owner_user_id"], r["mail_key"]) for r in old_out])
        conn.execute("DELETE FROM mail_messages WHERE date_utc < ? AND direction = 'out'", (out_cutoff,))
        conn.commit()
    return len(keys)


def _match_sql(pattern: str) -> tuple[str, str]:
    """Условие на from_addr для шаблона адреса или домена (@firm.ru)."""
    if pattern.startswith("@"):
        return "from_addr LIKE ?", "%" + pattern
    return "from_addr = ?", pattern


def bury(owner_user_id: Any, pattern: str) -> list[str]:
    """Письма скрытого отправителя: в «отсеянные», из поиска."""
    cond, arg = _match_sql(pattern)
    with _LOCK:
        conn = _conn()
        keys = [str(r["mail_key"]) for r in conn.execute(
            f"SELECT mail_key FROM mail_messages WHERE owner_user_id = ? AND direction = 'in' AND {cond}",
            (str(owner_user_id), arg),
        ).fetchall()]
        conn.executemany("DELETE FROM mail_fts WHERE owner_user_id = ? AND mail_key = ?",
                         [(str(owner_user_id), k) for k in keys])
        conn.execute(
            f"UPDATE mail_messages SET bulk = 1, analyzed = 2 WHERE owner_user_id = ? AND direction = 'in' AND {cond}",
            (str(owner_user_id), arg),
        )
        conn.commit()
    return keys


def bury_keys(owner_user_id: Any, keys: list[str]) -> None:
    with _LOCK:
        conn = _conn()
        conn.executemany("DELETE FROM mail_fts WHERE owner_user_id = ? AND mail_key = ?", [(str(owner_user_id), k) for k in keys])
        conn.executemany("UPDATE mail_messages SET bulk = 1, analyzed = 2 WHERE owner_user_id = ? AND mail_key = ?",
                         [(str(owner_user_id), k) for k in keys])
        conn.commit()


def filtered_rows(owner_user_id: Any, pattern: str, *, since: datetime) -> list[dict[str, Any]]:
    cond, arg = _match_sql(pattern)
    with _LOCK:
        rows = _conn().execute(
            f"SELECT * FROM mail_messages WHERE owner_user_id = ? AND direction = 'in' AND bulk = 1 AND date_utc >= ? AND {cond}",
            (str(owner_user_id), since.isoformat(), arg),
        ).fetchall()
    return [_row(r) for r in rows]


def revive(owner_user_id: Any, key: str, snippet: str) -> None:
    """Отправителя разрешили: письмо снова «от человека», с текстом, в поиске и на разбор."""
    row = get(owner_user_id, key)
    if not row:
        return
    people = " ".join([row.get("from_name") or "", row.get("from_addr") or ""] + row.get("to", []) + row.get("cc", []))
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE mail_messages SET bulk = 0, analyzed = 0, snippet = ? WHERE owner_user_id = ? AND mail_key = ?",
            (snippet[:SNIPPET], str(owner_user_id), key),
        )
        conn.execute("DELETE FROM mail_fts WHERE owner_user_id = ? AND mail_key = ?", (str(owner_user_id), key))
        conn.execute(
            "INSERT INTO mail_fts (owner_user_id, mail_key, subject, people, snippet) VALUES (?, ?, ?, ?, ?)",
            (str(owner_user_id), key, row.get("subject") or "", people, snippet[:SNIPPET]),
        )
        conn.commit()


def filtered_senders(owner_user_id: Any, *, since: datetime, limit: int = 100) -> list[dict[str, Any]]:
    """«Отсеяно Лео»: отправители отсеянных входящих — сколько писем, последняя тема."""
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT from_addr, MAX(from_name) AS from_name, COUNT(*) AS n, MAX(date_utc) AS last_date
            FROM mail_messages WHERE owner_user_id = ? AND direction = 'in' AND bulk = 1 AND date_utc >= ? AND from_addr != ''
            GROUP BY from_addr ORDER BY last_date DESC LIMIT ?
            """,
            (str(owner_user_id), since.isoformat(), limit),
        ).fetchall()
        out = []
        for r in rows:
            last = _conn().execute(
                "SELECT subject FROM mail_messages WHERE owner_user_id = ? AND from_addr = ? AND bulk = 1 ORDER BY date_utc DESC LIMIT 1",
                (str(owner_user_id), r["from_addr"]),
            ).fetchone()
            out.append({"addr": r["from_addr"], "name": r["from_name"] or "", "count": int(r["n"]),
                        "last_date": r["last_date"], "last_subject": (last["subject"] if last else "") or ""})
    return out
