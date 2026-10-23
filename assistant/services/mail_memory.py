"""Память переписки: тексты писем от людей, чтобы Лео знал контекст общения («что мы обсуждали с X?»).

Храним только письма, прошедшие воронку (люди, не рассылки, не скрытые отправители), и только то, что написал
автор — без цитат и подписей. Письма группируются в ветки; ветка — источник семантической памяти Лео
(semantic_memory, kind=mail_thread) и полнотекстового поиска здесь же (FTS — если векторы выключены).
Новые письма попадают сразу при синхронизации, история за 12 месяцев догружается порциями.
Всё стирается при отключении ящика, «Не показывать» и выключенной «Памяти переписки».
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

BACKFILL_DAYS = 365
BACKFILL_WINDOW_DAYS = 30
BACKFILL_MAX = 150  # писем за проход на папку — сервер слабый
MAX_TEXT = 6000
THREAD_TEXT = 20000

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
        CREATE TABLE IF NOT EXISTS mail_memory (
            owner_user_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            thread_key TEXT NOT NULL,
            direction TEXT NOT NULL,
            from_addr TEXT NOT NULL DEFAULT '',
            from_name TEXT NOT NULL DEFAULT '',
            to_json TEXT NOT NULL DEFAULT '[]',
            subject TEXT NOT NULL DEFAULT '',
            date_utc TEXT NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS mail_memory_thread ON mail_memory (owner_user_id, thread_key);
        CREATE VIRTUAL TABLE IF NOT EXISTS mail_memory_fts USING fts5(
            owner_user_id UNINDEXED, message_id UNINDEXED, subject, people, text, tokenize = 'unicode61'
        );
        CREATE TABLE IF NOT EXISTS mail_memory_cursor (
            owner_user_id TEXT NOT NULL, account_id TEXT NOT NULL, folder TEXT NOT NULL, before_date TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, account_id, folder)
        );
        """
    )
    _CONN.commit()
    return _CONN


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled(uid: int) -> bool:
    from assistant.stores import mail_accounts

    return bool(mail_accounts.settings(uid).get("memory_enabled", True))


# ---------------------------------------------------------------- запись

def add_message(uid: int, row: dict[str, Any], text: str) -> bool:
    """Письмо от/к человеку → в память (только авторский текст). True — новое."""
    from assistant.integrations import mail_imap

    if row.get("bulk") or not row.get("message_id"):
        return False
    clean = mail_imap.strip_quoted(text or "").strip()
    if len(clean) < 2:
        return False
    owner = str(uid)
    people = " ".join([str(row.get("from_name") or ""), str(row.get("from_addr") or "")] + list(row.get("to") or []))
    with _LOCK:
        conn = _conn()
        if conn.execute("SELECT 1 FROM mail_memory WHERE owner_user_id = ? AND message_id = ?",
                        (owner, row["message_id"])).fetchone():
            return False
        conn.execute(
            """INSERT INTO mail_memory (owner_user_id, message_id, account_id, thread_key, direction, from_addr, from_name,
                   to_json, subject, date_utc, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner, row["message_id"], row.get("account_id") or "", row.get("thread_key") or row["message_id"],
             row.get("direction") or "in", row.get("from_addr") or "", row.get("from_name") or "",
             json.dumps(list(row.get("to") or [])), row.get("subject") or "", row.get("date_utc") or _now().isoformat(),
             clean[:MAX_TEXT]),
        )
        conn.execute(
            "INSERT INTO mail_memory_fts (owner_user_id, message_id, subject, people, text) VALUES (?, ?, ?, ?, ?)",
            (owner, row["message_id"], row.get("subject") or "", people, clean[:MAX_TEXT]),
        )
        conn.commit()
    return True


def _delete_where(uid: int, where: str, args: tuple) -> int:
    with _LOCK:
        conn = _conn()
        ids = [r["message_id"] for r in conn.execute(
            f"SELECT message_id FROM mail_memory WHERE owner_user_id = ? AND {where}", (str(uid),) + args).fetchall()]
        conn.executemany("DELETE FROM mail_memory_fts WHERE owner_user_id = ? AND message_id = ?",
                         [(str(uid), i) for i in ids])
        conn.execute(f"DELETE FROM mail_memory WHERE owner_user_id = ? AND {where}", (str(uid),) + args)
        conn.commit()
    if ids:
        _reindex(uid)
    return len(ids)


def forget_messages(uid: int, message_ids: list[str]) -> int:
    n = 0
    for mid in message_ids:
        n += _delete_where(uid, "message_id = ?", (mid,))
    return n


def forget_sender(uid: int, pattern: str) -> int:
    """«Не показывать»: стереть письма отправителя (адрес или @домен) из памяти."""
    if pattern.startswith("@"):
        return _delete_where(uid, "direction = 'in' AND from_addr LIKE ?", ("%" + pattern,))
    return _delete_where(uid, "direction = 'in' AND from_addr = ?", (pattern,))


def forget_account(uid: int, account_id: str) -> int:
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM mail_memory_cursor WHERE owner_user_id = ? AND account_id = ?", (str(uid), account_id))
        conn.commit()
    return _delete_where(uid, "account_id = ?", (account_id,))


def forget_all(uid: int) -> int:
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM mail_memory_cursor WHERE owner_user_id = ?", (str(uid),))
        conn.commit()
    return _delete_where(uid, "1 = 1", ())


def _reindex(uid: int) -> None:
    try:
        from assistant.services import semantic_memory

        semantic_memory.note_changed(uid)
    except Exception as e:
        print(f"[mail_memory] reindex uid={uid} err={e!r}")


# ---------------------------------------------------------------- ветки для семантической памяти

def threads(uid: int) -> list[dict[str, Any]]:
    """Ветки памяти: тема, участники, даты, текст (письма по порядку: дата, кто, что написал)."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM mail_memory WHERE owner_user_id = ? ORDER BY thread_key, date_utc", (str(uid),)
        ).fetchall()
    by: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by.setdefault(r["thread_key"], []).append(r)
    out = []
    for tk, msgs in by.items():
        people: dict[str, str] = {}
        lines = []
        for m in msgs:
            who = "Я" if m["direction"] == "out" else (m["from_name"] or m["from_addr"])
            if m["direction"] == "in" and m["from_addr"]:
                people[m["from_addr"]] = m["from_name"] or m["from_addr"]
            for a in json.loads(m["to_json"] or "[]"):
                people.setdefault(a, a)
            lines.append(f"[{str(m['date_utc'])[:10]}] {who}: {m['text']}")
        subject = next((m["subject"] for m in msgs if m["subject"]), "без темы")
        out.append({
            "thread_key": tk, "subject": subject, "people": people,
            "first_date": str(msgs[0]["date_utc"])[:10], "last_date": str(msgs[-1]["date_utc"])[:10],
            "text": ("\n\n".join(lines))[-THREAD_TEXT:], "count": len(msgs),
        })
    return out


def semantic_sources(uid: int) -> list[dict[str, Any]]:
    """Источники для semantic_memory.sources_for_user (kind=mail_thread)."""
    try:
        from assistant.stores import mail_accounts

        if not mail_accounts.list_accounts(uid) or not enabled(uid):
            return []
    except Exception:
        return []
    out = []
    for t in threads(uid):
        names = ", ".join(list(t["people"].values())[:5])
        out.append({
            "key": f"mail:{t['thread_key']}", "kind": "mail_thread", "title": f"Переписка «{t['subject']}» ({names})",
            "date": t["last_date"], "text": t["text"],
            "ref": {"thread_key": t["thread_key"], "people": list(t["people"].keys())[:10], "kind": "mail_thread"},
        })
    return out


def thread_text(uid: int, thread_key: str) -> Optional[dict[str, Any]]:
    return next((t for t in threads(uid) if t["thread_key"] == thread_key), None)


# ---------------------------------------------------------------- поиск

def _stem_query(q: str) -> str:
    stop = {"что", "про", "как", "где", "все", "мне", "мы", "там", "когда", "то", "это", "о", "об", "с", "со", "и", "в",
            "на", "по", "найди", "обсуждали", "договаривался", "договаривались", "писал", "писали", "переписка"}
    words = [w for w in re.findall(r"[\w@.-]{3,}", q.lower()) if w not in stop]
    return " OR ".join('"' + (w if "@" in w else w[: max(4, len(w) - 2)]).replace('"', "") + '"*' for w in words[:10])


def search_text(uid: int, query: str, *, people: Optional[list[str]] = None, limit: int = 10) -> list[dict[str, Any]]:
    """Полнотекстовый поиск по памяти (если векторы выключены — основной). people — фильтр по участникам."""
    fq = _stem_query(query)
    with _LOCK:
        conn = _conn()
        if fq:
            try:
                ids = [r["message_id"] for r in conn.execute(
                    "SELECT message_id FROM mail_memory_fts WHERE mail_memory_fts MATCH ? AND owner_user_id = ? ORDER BY rank LIMIT 200",
                    (fq, str(uid))).fetchall()]
            except sqlite3.OperationalError:
                ids = []
        else:
            ids = [r["message_id"] for r in conn.execute(
                "SELECT message_id FROM mail_memory WHERE owner_user_id = ? ORDER BY date_utc DESC LIMIT 200", (str(uid),)).fetchall()]
        rows = [conn.execute("SELECT * FROM mail_memory WHERE owner_user_id = ? AND message_id = ?", (str(uid), i)).fetchone()
                for i in ids]
    ppl = {p.lower() for p in people or []}
    out, seen = [], set()
    for r in rows:
        if not r:
            continue
        if ppl:
            involved = {r["from_addr"].lower()} | {a.lower() for a in json.loads(r["to_json"] or "[]")}
            if not involved & ppl:
                continue
        if r["thread_key"] in seen:
            continue
        seen.add(r["thread_key"])
        out.append({"thread_key": r["thread_key"], "subject": r["subject"], "date": str(r["date_utc"])[:10],
                    "from": r["from_name"] or r["from_addr"], "direction": r["direction"], "snippet": r["text"][:500]})
        if len(out) >= limit:
            break
    return out


def search_semantic(uid: int, query: str, *, people: Optional[list[str]] = None, limit: int = 6) -> list[dict[str, Any]]:
    """Смысловой поиск по веткам переписки (semantic_memory), фильтр по участникам."""
    from assistant.lib import embed_client
    from assistant.services import semantic_memory

    if not embed_client.enabled():
        return []
    hits = semantic_memory.search(uid, query, k=40)
    ppl = {p.lower() for p in people or []}
    out, seen = [], set()
    for h in hits:
        ref = h.get("ref") or {}
        if ref.get("kind") != "mail_thread":
            continue
        if ppl and not ppl & {p.lower() for p in ref.get("people") or []}:
            continue
        if ref["thread_key"] in seen:
            continue
        seen.add(ref["thread_key"])
        out.append({"thread_key": ref["thread_key"], "subject": h["title"], "date": h["date"], "snippet": h["snippet"],
                    "score": h["score"]})
        if len(out) >= limit:
            break
    return out


def people_for(uid: int, who: str) -> list[str]:
    """Адреса человека по имени или адресу: контакты, отправители из индекса и памяти."""
    w = (who or "").strip().lower()
    if not w:
        return []
    if "@" in w:
        return [w]
    out: list[str] = []
    try:
        from assistant.lib.calendar_attendees import resolve_attendee_names

        found, _missing = resolve_attendee_names(int(uid), [who])
        out += [a["email"].lower() for a in found if a.get("email")]
    except Exception:
        pass
    stem = w[: max(4, len(w) - 2)]
    with _LOCK:
        rows = _conn().execute(
            "SELECT DISTINCT from_addr, from_name FROM mail_memory WHERE owner_user_id = ? AND direction = 'in'", (str(uid),)
        ).fetchall()
    for r in rows:
        name = (r["from_name"] or "").lower()
        if r["from_addr"] and (stem in name or stem in r["from_addr"].lower()) and r["from_addr"] not in out:
            out.append(r["from_addr"].lower())
    try:
        from assistant.stores import mail_index

        for r in mail_index.recent(uid, since=_now() - timedelta(days=90), direction="in"):
            name = (r.get("from_name") or "").lower()
            addr = (r.get("from_addr") or "").lower()
            if addr and (stem in name or stem in addr) and addr not in out:
                out.append(addr)
    except Exception:
        pass
    return out[:6]


# ---------------------------------------------------------------- догрузка истории

def _cursor(uid: int, account_id: str, folder: str) -> Optional[datetime]:
    with _LOCK:
        r = _conn().execute(
            "SELECT before_date FROM mail_memory_cursor WHERE owner_user_id = ? AND account_id = ? AND folder = ?",
            (str(uid), account_id, folder)).fetchone()
    return datetime.fromisoformat(r["before_date"]) if r else None


def _set_cursor(uid: int, account_id: str, folder: str, d: datetime) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO mail_memory_cursor (owner_user_id, account_id, folder, before_date) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(owner_user_id, account_id, folder) DO UPDATE SET before_date = excluded.before_date",
            (str(uid), account_id, folder, d.isoformat()))
        conn.commit()


def backfill_step(uid: int, acc: dict[str, Any], mail, folders: list[str], *, now: Optional[datetime] = None) -> int:
    """Один шаг догрузки истории: по месяцу назад на папку, пока не дошли до 12 месяцев."""
    from assistant.integrations import mail_imap
    from assistant.services import mail_source
    from assistant.stores import mail_senders

    now = now or _now()
    floor = now - timedelta(days=BACKFILL_DAYS)
    mine = {str(acc["email"]).lower()}
    rules = mail_senders.rules_map(uid)
    from assistant.stores import mail_index

    known = mail_index.correspondents(uid) - mine
    added = 0
    for folder in folders:
        before = _cursor(uid, acc["account_id"], folder) or (now - timedelta(days=mail_source.FIRST_SYNC_DAYS))
        if before <= floor:
            continue
        since = max(floor, before - timedelta(days=BACKFILL_WINDOW_DAYS))
        mail_imap.select(mail, folder)
        typ, data = mail.uid("SEARCH", "SINCE", since.strftime("%d-%b-%Y"), "BEFORE", before.strftime("%d-%b-%Y"))
        uids = sorted(int(x) for x in (data[0].split() if typ == "OK" and data and data[0] else []) if x.isdigit())
        uids = uids[-BACKFILL_MAX:]
        heads = mail_imap.fetch(mail, uids, "BODY.PEEK[HEADER]") if uids else {}
        want = []
        for u, (size, raw) in heads.items():
            msg = mail_imap.parse(raw)
            frm = (mail_imap.addresses(msg, "From") or [("", "")])[0][1]
            if size <= mail_source.MAX_BODY_SIZE and not mail_source.is_filtered(msg, frm, mine=mine, rules=rules, known=known):
                want.append(u)
        full = mail_imap.fetch(mail, want, "BODY.PEEK[]") if want else {}
        for u, (_size, raw) in full.items():
            msg = mail_imap.parse(raw)
            body = mail_imap.body_text(msg)
            row = mail_source._row_from(acc, folder, u, msg, mine=mine, body=body, rules=rules, known=known)
            if add_message(uid, row, body):
                added += 1
        _set_cursor(uid, acc["account_id"], folder, since)
    if added:
        _reindex(uid)
    return added


def status(uid: int) -> dict[str, Any]:
    with _LOCK:
        r = _conn().execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT thread_key) AS t, MIN(date_utc) AS d FROM mail_memory WHERE owner_user_id = ?",
            (str(uid),)).fetchone()
    return {"messages": int(r["n"] or 0), "threads": int(r["t"] or 0), "since": str(r["d"] or "")[:10]}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower().replace("ё", "е"))}


def live_search(uid: int, addrs: list[str], query: str, *, limit: int = 3, per_folder: int = 40) -> list[dict[str, Any]]:
    """Живой поиск в ящике (вся история): письма от/к этим людям, по словам вопроса. Ничего не сохраняет."""
    from assistant.integrations import mail_imap
    from assistant.services import mail_source
    from assistant.stores import mail_accounts, mail_index

    if not addrs:
        return []
    q = _stems(query)
    found: list[dict[str, Any]] = []
    for acc in mail_accounts.list_accounts(uid):
        try:
            mail = mail_source.open_imap(uid, acc)
        except Exception as e:
            print(f"[mail_memory] live login uid={uid} err={e!r}")
            continue
        try:
            folders = [("INBOX", True)]
            sent = mail_imap.sent_folder(mail)
            if sent:
                folders.append((sent, False))
            for folder, inbox in folders:
                mail_imap.select(mail, folder)
                uids = mail_imap.search_people(mail, addrs, inbox=inbox)[-per_folder:]
                for _u, (_size, raw) in mail_imap.fetch(mail, uids, "BODY.PEEK[]").items():
                    msg = mail_imap.parse(raw)
                    text = mail_imap.strip_quoted(mail_imap.body_text(msg))
                    subj = mail_imap.decode_hdr(msg.get("Subject"))
                    mid = (mail_imap.msg_ids(msg.get("Message-ID")) or [""])[0]
                    irt = (mail_imap.msg_ids(msg.get("In-Reply-To")) or [""])[0]
                    frm = (mail_imap.addresses(msg, "From") or [("", "")])[0]
                    dt = mail_imap.date_utc(msg)
                    found.append({
                        "thread_key": mail_index.thread_key_for(mid, irt, mail_imap.msg_ids(msg.get("References"))),
                        "subject": subj, "date": dt.date().isoformat() if dt else "", "from": frm[0] or frm[1],
                        "direction": "in" if inbox else "out", "text": text[:1500],
                        "score": len(q & _stems(subj + " " + text)) / max(1, len(q)),
                    })
        except Exception as e:
            print(f"[mail_memory] live search uid={uid} err={e!r}")
        finally:
            try:
                mail.logout()
            except Exception:
                pass
    found.sort(key=lambda x: (x["score"], x["date"]), reverse=True)
    out, seen = [], set()
    for f in found:
        if f["thread_key"] in seen or (q and f["score"] == 0 and out):
            continue
        seen.add(f["thread_key"])
        thread = sorted([x for x in found if x["thread_key"] == f["thread_key"]], key=lambda x: x["date"])
        out.append({"thread_key": f["thread_key"], "subject": f["subject"], "date": f["date"], "source": "ящик",
                    "messages": [{k: m[k] for k in ("date", "from", "direction", "text")} for m in thread[-6:]]})
        if len(out) >= limit:
            break
    return out
