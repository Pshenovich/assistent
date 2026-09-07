"""SQLite-хранилище Executive Board."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assistant.board.models import ACTIVE_STATUSES
from assistant.board.numbers import safe_float

_lock = threading.RLock()
_CONN: sqlite3.Connection | None = None


def _project_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def db_path() -> Path:
    raw = os.getenv("BOARD_DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_project_dir() / p).resolve()
        else:
            p = p.resolve()
        return p
    return (_project_dir() / "data" / "board.sqlite").resolve()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _CONN = conn
    return conn


def reset_connection() -> None:
    global _CONN
    with _lock:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return default


def init_db() -> None:
    with _lock:
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                chat_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS company_context (
                company_id TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                business_model TEXT DEFAULT '',
                products TEXT DEFAULT '',
                customers TEXT DEFAULT '',
                kpis TEXT DEFAULT '',
                team TEXT DEFAULT '',
                org_structure TEXT DEFAULT '',
                financials TEXT DEFAULT '',
                goals TEXT DEFAULT '',
                projects TEXT DEFAULT '',
                constraints TEXT DEFAULT '',
                raw_text TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_context (
                user_id TEXT PRIMARY KEY,
                role TEXT DEFAULT '',
                goals TEXT DEFAULT '',
                decision_style TEXT DEFAULT '',
                preferences TEXT DEFAULT '',
                priorities TEXT DEFAULT '',
                raw_text TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id INTEGER,
                title TEXT DEFAULT '',
                original_question TEXT NOT NULL,
                status TEXT NOT NULL,
                current_round INTEGER NOT NULL DEFAULT 1,
                max_rounds INTEGER NOT NULL DEFAULT 4,
                message_count INTEGER NOT NULL DEFAULT 0,
                analysis_json TEXT,
                decision_id TEXT,
                forum_topic_id INTEGER,
                extra_instruction TEXT DEFAULT '',
                started_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS meeting_messages (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                round INTEGER NOT NULL,
                telegram_message_id INTEGER,
                reply_to_message_id INTEGER,
                content TEXT NOT NULL,
                structured_response TEXT,
                confidence REAL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meeting_rounds (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                round INTEGER NOT NULL,
                mode TEXT DEFAULT 'DISCUSSION',
                summary TEXT DEFAULT '',
                consensus TEXT DEFAULT '',
                disagreements TEXT DEFAULT '',
                open_questions TEXT DEFAULT '',
                assumptions TEXT DEFAULT '',
                scores_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                company_id TEXT,
                chat_id TEXT,
                user_id TEXT,
                problem TEXT DEFAULT '',
                decision TEXT DEFAULT '',
                why TEXT,
                confidence REAL,
                kpis TEXT,
                risks TEXT,
                assumptions TEXT,
                open_questions TEXT,
                do_not_do TEXT,
                actions_json TEXT,
                decision_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_actions (
                id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                action TEXT NOT NULL,
                owner TEXT DEFAULT '',
                deadline TEXT DEFAULT '',
                success_metric TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_followups (
                id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id INTEGER,
                user_id TEXT,
                due_at TEXT NOT NULL,
                sent_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                prompt TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_reviews (
                id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                meeting_id TEXT,
                user_results TEXT DEFAULT '',
                review_json TEXT,
                verdict TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS company_documents (
                id TEXT PRIMARY KEY,
                company_id TEXT NOT NULL,
                filename TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'general',
                mime TEXT DEFAULT '',
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_company_docs ON company_documents(company_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_meetings_chat ON meetings(chat_id, status);
            CREATE INDEX IF NOT EXISTS idx_meetings_user ON meetings(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_meeting ON meeting_messages(meeting_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_chat ON decisions(chat_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_followups_due ON decision_followups(status, due_at);
            CREATE INDEX IF NOT EXISTS idx_companies_chat ON companies(chat_id);
            """
        )
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(company_context)")}
        if "source_url" not in cols:
            conn.execute(
                "ALTER TABLE company_context ADD COLUMN source_url TEXT DEFAULT ''"
            )
        conn.commit()


def get_or_create_company_for_chat(chat_id: str | int, name: str = "") -> dict[str, Any]:
    cid = str(chat_id)
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM companies WHERE chat_id = ? ORDER BY created_at LIMIT 1",
            (cid,),
        ).fetchone()
        if row:
            return _row(row) or {}
        company_id = new_id()
        now = _now()
        conn.execute(
            "INSERT INTO companies (id, name, chat_id, created_at) VALUES (?, ?, ?, ?)",
            (company_id, name or f"chat:{cid}", cid, now),
        )
        conn.execute(
            """INSERT INTO company_context (company_id, raw_text, updated_at)
               VALUES (?, '', ?)""",
            (company_id, now),
        )
        conn.commit()
        return {
            "id": company_id,
            "name": name or f"chat:{cid}",
            "chat_id": cid,
            "created_at": now,
        }


def get_company_context(company_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM company_context WHERE company_id = ?", (company_id,)
        ).fetchone()
    return _row(row)


def upsert_company_context(company_id: str, raw_text: str | None = None, **fields: str) -> None:
    now = _now()
    allowed = {
        "description",
        "business_model",
        "products",
        "customers",
        "kpis",
        "team",
        "org_structure",
        "financials",
        "goals",
        "projects",
        "constraints",
        "source_url",
    }
    with _lock:
        conn = _connect()
        existing = conn.execute(
            "SELECT company_id FROM company_context WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        if existing:
            updates = ["updated_at = ?"]
            params: list[Any] = [now]
            if raw_text is not None:
                updates.append("raw_text = ?")
                params.append(raw_text)
            for key, val in fields.items():
                if key in allowed:
                    updates.append(f"{key} = ?")
                    params.append(val)
            params.append(company_id)
            conn.execute(
                f"UPDATE company_context SET {', '.join(updates)} WHERE company_id = ?",
                params,
            )
        else:
            conn.execute(
                """INSERT INTO company_context (
                    company_id, raw_text, description, updated_at
                ) VALUES (?, ?, ?, ?)""",
                (
                    company_id,
                    raw_text or "",
                    fields.get("description", raw_text or ""),
                    now,
                ),
            )
            extra = {k: v for k, v in fields.items() if k in allowed and k != "description"}
            if extra:
                sets = ", ".join(f"{k} = ?" for k in extra)
                conn.execute(
                    f"UPDATE company_context SET {sets} WHERE company_id = ?",
                    [*extra.values(), company_id],
                )
        conn.commit()


MAX_COMPANY_DOCS = 8


def add_company_document(
    *,
    company_id: str,
    filename: str,
    kind: str,
    text: str,
    mime: str = "",
) -> dict[str, Any]:
    did = new_id()
    now = _now()
    kind = kind if kind in {"products", "team", "general"} else "general"
    with _lock:
        conn = _connect()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM company_documents WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        count = int(n["n"] if n else 0)
        if count >= MAX_COMPANY_DOCS:
            oldest = conn.execute(
                """SELECT id FROM company_documents
                   WHERE company_id = ? ORDER BY created_at ASC LIMIT 1""",
                (company_id,),
            ).fetchone()
            if oldest:
                conn.execute("DELETE FROM company_documents WHERE id = ?", (oldest["id"],))
        conn.execute(
            """INSERT INTO company_documents (
                id, company_id, filename, kind, mime, text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (did, company_id, filename or "document", kind, mime or "", text, now),
        )
        conn.commit()
    if kind in {"products", "team"}:
        upsert_company_context(company_id, **{kind: text})
    return {
        "id": did,
        "company_id": company_id,
        "filename": filename or "document",
        "kind": kind,
        "text": text,
        "created_at": now,
    }


def list_company_documents(company_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM company_documents
               WHERE company_id = ? ORDER BY created_at ASC""",
            (company_id,),
        ).fetchall()
    return [_row(r) or {} for r in rows]


def delete_company_documents(company_id: str) -> int:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM company_documents WHERE company_id = ?",
            (company_id,),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def get_company_pack(company_id: str) -> dict[str, Any] | None:
    ctx = get_company_context(company_id)
    docs = list_company_documents(company_id)
    if not ctx and not docs:
        return None
    out = dict(ctx or {"company_id": company_id})
    out["_documents"] = docs
    return out


def get_user_context(user_id: str | int) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM user_context WHERE user_id = ?", (str(user_id),)
        ).fetchone()
    return _row(row)


def upsert_user_context(user_id: str | int, raw_text: str, **fields: str) -> None:
    uid = str(user_id)
    now = _now()
    with _lock:
        conn = _connect()
        existing = conn.execute(
            "SELECT user_id FROM user_context WHERE user_id = ?", (uid,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE user_context SET raw_text = ?, role = ?, goals = ?,
                   decision_style = ?, preferences = ?, priorities = ?,
                   updated_at = ? WHERE user_id = ?""",
                (
                    raw_text,
                    fields.get("role", ""),
                    fields.get("goals", ""),
                    fields.get("decision_style", ""),
                    fields.get("preferences", ""),
                    fields.get("priorities", ""),
                    now,
                    uid,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO user_context (user_id, raw_text, role, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (uid, raw_text, fields.get("role", ""), now),
            )
        conn.commit()


def create_meeting(
    *,
    user_id: str | int,
    chat_id: str | int,
    question: str,
    company_id: str | None = None,
    thread_id: int | None = None,
    title: str = "",
    max_rounds: int = 4,
    extra_instruction: str = "",
) -> dict[str, Any]:
    mid = new_id()
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO meetings (
                id, company_id, user_id, chat_id, thread_id, title,
                original_question, status, current_round, max_rounds,
                extra_instruction, started_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'CREATED', 1, ?, ?, ?, ?, ?)""",
            (
                mid,
                company_id,
                str(user_id),
                str(chat_id),
                thread_id,
                title,
                question,
                int(max_rounds),
                extra_instruction,
                now,
                now,
                now,
            ),
        )
        conn.commit()
    return get_meeting(mid) or {}


def get_meeting(meeting_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
    data = _row(row)
    if data:
        data["analysis"] = _loads(data.get("analysis_json"), {})
    return data


def update_meeting(meeting_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [meeting_id]
    with _lock:
        conn = _connect()
        conn.execute(f"UPDATE meetings SET {cols} WHERE id = ?", vals)
        conn.commit()


def set_meeting_status(meeting_id: str, status: str, **extra: Any) -> None:
    payload = dict(extra)
    payload["status"] = status
    if status in {"COMPLETED", "STOPPED", "ERROR"} and "completed_at" not in payload:
        payload["completed_at"] = _now()
    update_meeting(meeting_id, **payload)


def set_analysis(meeting_id: str, analysis: dict[str, Any]) -> None:
    title = str(analysis.get("title") or "")[:80]
    update_meeting(
        meeting_id,
        analysis_json=json.dumps(analysis, ensure_ascii=False),
        title=title,
        status="ANALYZING",
    )


def get_active_meeting(chat_id: str | int, thread_id: int | None = None) -> dict[str, Any] | None:
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    sql = (
        f"SELECT * FROM meetings WHERE chat_id = ? AND status IN ({placeholders}) "
        "ORDER BY created_at DESC LIMIT 1"
    )
    params: list[Any] = [str(chat_id), *ACTIVE_STATUSES]
    with _lock:
        row = _connect().execute(sql, params).fetchone()
    data = _row(row)
    if not data:
        return None
    if thread_id is not None and data.get("thread_id") not in (None, thread_id):
        return data if data.get("forum_topic_id") == thread_id else data
    data["analysis"] = _loads(data.get("analysis_json"), {})
    return data


def meeting_number(meeting_id: str) -> int:
    with _lock:
        row = _connect().execute(
            """SELECT COUNT(*) AS n FROM meetings
               WHERE created_at <= (SELECT created_at FROM meetings WHERE id = ?)""",
            (meeting_id,),
        ).fetchone()
    return int(row["n"] if row else 1)


def add_message(
    *,
    meeting_id: str,
    agent: str,
    content: str,
    round: int,
    structured: dict[str, Any] | None = None,
    telegram_message_id: int | None = None,
    reply_to_message_id: int | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    mid = new_id()
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO meeting_messages (
                id, meeting_id, agent, round, telegram_message_id,
                reply_to_message_id, content, structured_response, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                meeting_id,
                agent,
                int(round),
                telegram_message_id,
                reply_to_message_id,
                content,
                json.dumps(structured, ensure_ascii=False) if structured else None,
                confidence,
                now,
            ),
        )
        conn.execute(
            """UPDATE meetings SET message_count = message_count + 1, updated_at = ?
               WHERE id = ?""",
            (now, meeting_id),
        )
        conn.commit()
    return get_message(mid) or {}


def get_message(message_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM meeting_messages WHERE id = ?", (message_id,)
        ).fetchone()
    data = _row(row)
    if data:
        data["structured"] = _loads(data.get("structured_response"), {})
    return data


def list_messages(meeting_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM meeting_messages WHERE meeting_id = ?
               ORDER BY created_at ASC""",
            (meeting_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = _row(r) or {}
        d["structured"] = _loads(d.get("structured_response"), {})
        out.append(d)
    return out


def set_message_telegram_id(message_id: str, telegram_message_id: int) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE meeting_messages SET telegram_message_id = ? WHERE id = ?",
            (int(telegram_message_id), message_id),
        )
        conn.commit()


def add_round_summary(
    *,
    meeting_id: str,
    round: int,
    mode: str,
    summary: str,
    consensus: str = "",
    disagreements: str = "",
    open_questions: str = "",
    assumptions: str = "",
    scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rid = new_id()
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO meeting_rounds (
                id, meeting_id, round, mode, summary, consensus, disagreements,
                open_questions, assumptions, scores_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                meeting_id,
                int(round),
                mode,
                summary,
                consensus,
                disagreements,
                open_questions,
                assumptions,
                json.dumps(scores or {}, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            "UPDATE meetings SET current_round = ?, updated_at = ? WHERE id = ?",
            (int(round), now, meeting_id),
        )
        conn.commit()
    return {"id": rid, "meeting_id": meeting_id, "round": round}


def list_rounds(meeting_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM meeting_rounds WHERE meeting_id = ?
               ORDER BY round ASC""",
            (meeting_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = _row(r) or {}
        d["scores"] = _loads(d.get("scores_json"), {})
        out.append(d)
    return out


def save_decision(
    *,
    meeting_id: str,
    payload: dict[str, Any],
    company_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    did = new_id()
    now = _now()
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO decisions (
                id, meeting_id, company_id, chat_id, user_id, problem, decision,
                why, confidence, kpis, risks, assumptions, open_questions,
                do_not_do, actions_json, decision_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                did,
                meeting_id,
                company_id,
                chat_id,
                user_id,
                str(payload.get("problem") or ""),
                str(payload.get("decision") or ""),
                json.dumps(payload.get("why") or [], ensure_ascii=False),
                safe_float(payload.get("confidence"), 0.0),
                json.dumps(payload.get("kpis") or [], ensure_ascii=False),
                json.dumps(payload.get("risks") or [], ensure_ascii=False),
                json.dumps(payload.get("assumptions") or [], ensure_ascii=False),
                json.dumps(payload.get("open_questions") or [], ensure_ascii=False),
                json.dumps(payload.get("do_not_do") or [], ensure_ascii=False),
                json.dumps(actions, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                now,
            ),
        )
        for act in actions:
            if not isinstance(act, dict):
                continue
            text = str(act.get("action") or "").strip()
            if not text:
                continue
            conn.execute(
                """INSERT INTO decision_actions (
                    id, decision_id, action, owner, deadline, success_metric,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    new_id(),
                    did,
                    text,
                    str(act.get("owner") or ""),
                    str(act.get("deadline") or ""),
                    str(act.get("success_metric") or ""),
                    now,
                ),
            )
        conn.execute(
            "UPDATE meetings SET decision_id = ?, updated_at = ? WHERE id = ?",
            (did, now, meeting_id),
        )
        conn.commit()
    return get_decision(did) or {}


def get_decision(decision_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
    return _hydrate_decision(_row(row))


def get_decision_by_meeting(meeting_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM decisions WHERE meeting_id = ? ORDER BY created_at DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
    return _hydrate_decision(_row(row))


def _hydrate_decision(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    for key in ("why", "kpis", "risks", "assumptions", "open_questions", "do_not_do", "actions_json"):
        data[key.replace("_json", "")] = _loads(data.get(key), [])
    data["payload"] = _loads(data.get("decision_json"), {})
    return data


def list_decisions_for_chat(chat_id: str | int, limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM decisions WHERE chat_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (str(chat_id), int(limit)),
        ).fetchall()
    return [d for r in rows if (d := _hydrate_decision(_row(r)))]


def list_all_decisions(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [d for r in rows if (d := _hydrate_decision(_row(r)))]


def search_decisions(chat_id: str | int, query: str, limit: int = 8) -> list[dict[str, Any]]:
    q = f"%{(query or '').strip()}%"
    if q == "%%":
        return list_decisions_for_chat(chat_id, limit=limit)
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM decisions
               WHERE chat_id = ? AND (
                 problem LIKE ? OR decision LIKE ? OR decision_json LIKE ?
               )
               ORDER BY created_at DESC LIMIT ?""",
            (str(chat_id), q, q, q, int(limit)),
        ).fetchall()
    return [d for r in rows if (d := _hydrate_decision(_row(r)))]


def list_actions(decision_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM decision_actions WHERE decision_id = ? ORDER BY created_at",
            (decision_id,),
        ).fetchall()
    return [_row(r) or {} for r in rows]


def update_action_status(action_id: str, status: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE decision_actions SET status = ? WHERE id = ?",
            (status, action_id),
        )
        conn.commit()


def create_followup(
    *,
    decision_id: str,
    chat_id: str | int,
    user_id: str | int | None,
    due_at: str,
    prompt: str = "",
    thread_id: int | None = None,
) -> dict[str, Any]:
    fid = new_id()
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO decision_followups (
                id, decision_id, chat_id, thread_id, user_id, due_at,
                status, prompt, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                fid,
                decision_id,
                str(chat_id),
                thread_id,
                str(user_id) if user_id is not None else None,
                due_at,
                prompt,
                now,
            ),
        )
        conn.commit()
    return get_followup(fid) or {}


def get_followup(followup_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM decision_followups WHERE id = ?", (followup_id,)
        ).fetchone()
    return _row(row)


def due_followups(now_iso: str | None = None) -> list[dict[str, Any]]:
    now = now_iso or _now()
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM decision_followups
               WHERE status = 'pending' AND due_at <= ?
               ORDER BY due_at ASC""",
            (now,),
        ).fetchall()
    return [_row(r) or {} for r in rows]


def mark_followup_sent(followup_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE decision_followups SET status = 'sent', sent_at = ? WHERE id = ?",
            (_now(), followup_id),
        )
        conn.commit()


def get_awaiting_followup(chat_id: str | int) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            """SELECT * FROM decision_followups
               WHERE chat_id = ? AND status = 'sent'
               ORDER BY sent_at DESC LIMIT 1""",
            (str(chat_id),),
        ).fetchone()
    return _row(row)


def mark_followup_answered(followup_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE decision_followups SET status = 'answered' WHERE id = ?",
            (followup_id,),
        )
        conn.commit()


def save_review(
    *,
    decision_id: str,
    user_results: str,
    review: dict[str, Any],
    meeting_id: str | None = None,
) -> dict[str, Any]:
    rid = new_id()
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO decision_reviews (
                id, decision_id, meeting_id, user_results, review_json, verdict, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                decision_id,
                meeting_id,
                user_results,
                json.dumps(review, ensure_ascii=False),
                str(review.get("verdict") or ""),
                now,
            ),
        )
        conn.commit()
    return {"id": rid, "decision_id": decision_id}


def list_reviews(decision_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM decision_reviews WHERE decision_id = ?
               ORDER BY created_at DESC""",
            (decision_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = _row(r) or {}
        d["review"] = _loads(d.get("review_json"), {})
        out.append(d)
    return out


def list_meetings_for_chat(chat_id: str | int, limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            """SELECT * FROM meetings WHERE chat_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (str(chat_id), int(limit)),
        ).fetchall()
    return [_row(r) or {} for r in rows]


def dashboard_stats() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        n_meetings = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        n_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        n_open = conn.execute(
            "SELECT COUNT(*) FROM decision_actions WHERE status = 'open'"
        ).fetchone()[0]
        n_follow = conn.execute(
            "SELECT COUNT(*) FROM decision_followups WHERE status IN ('pending','sent')"
        ).fetchone()[0]
    return {
        "meetings": int(n_meetings),
        "decisions": int(n_decisions),
        "open_actions": int(n_open),
        "active_followups": int(n_follow),
    }
