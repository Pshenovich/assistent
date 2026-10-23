"""Раздел «Почта» в «Дайджесте»: переписка с людьми как мессенджер.

Список веток — из индекса (письма от людей за 90 дней, рассылки и скрытые отправители уже отсеяны),
метки Лео — из разбора писем. Ветка — тексты читаются из ящика в момент открытия (IMAP, без «прочитано»),
HTML чистится (mail_html). «Прочитано» Orbita ведёт сама; с ящиком синхронизирует mail_sync (если есть права).
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.stores import mail_accounts, mail_index

LIST_DAYS = 90
MAX_THREADS = 200

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
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS mail_reads (owner_user_id TEXT NOT NULL, mail_key TEXT NOT NULL, read_at TEXT NOT NULL,"
        " PRIMARY KEY (owner_user_id, mail_key))"
    )
    _CONN.commit()
    return _CONN


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read_keys(uid: int) -> set[str]:
    with _LOCK:
        return {r["mail_key"] for r in _conn().execute(
            "SELECT mail_key FROM mail_reads WHERE owner_user_id = ?", (str(uid),)).fetchall()}


def set_read(uid: int, keys: list[str], read: bool = True) -> None:
    with _LOCK:
        conn = _conn()
        if read:
            conn.executemany(
                "INSERT OR IGNORE INTO mail_reads (owner_user_id, mail_key, read_at) VALUES (?, ?, ?)",
                [(str(uid), k, _now().isoformat()) for k in keys])
        else:
            conn.executemany("DELETE FROM mail_reads WHERE owner_user_id = ? AND mail_key = ?", [(str(uid), k) for k in keys])
        conn.commit()


def visible(row: dict[str, Any], mine_threads: set[str]) -> bool:
    """Письмо в «Почте»: не отсеянное — или отсеянное, но из ветки, где пользователь сам писал
    (переписка с info@ / support@ видна целиком). Приглашения из календаря — никогда."""
    from assistant.services import mail_filters

    if mail_filters.CALENDAR_SUBJECT.match(str(row.get("subject") or "")):
        return False
    return not row.get("bulk") or (row.get("thread_key") or "") in mine_threads


def clean_subject(s: str) -> str:
    return re.sub(r"^(\s*(re|fwd?|ответ|пересл\.?)\s*(\[\d+\])?\s*:\s*)+", "", s or "", flags=re.I).strip() or "Без темы"


def _labels(uid: int) -> dict[str, list[dict[str, str]]]:
    """thread_key → метки Лео: ждёт ответа / вы обещали / срок."""
    from assistant.services import mail_source

    out: dict[str, list[dict[str, str]]] = {}
    try:
        for q in mail_source.pending_requests(uid):
            row = mail_index.get(uid, q["mail_key"])
            if row:
                out.setdefault(row.get("thread_key") or row["mail_key"], []).append({"kind": "awaiting", "text": "ждёт ответа"})
        for mrow, it in mail_source._insights(uid):
            tk = mrow.get("thread_key") or mrow["mail_key"]
            if it.get("kind") == "promise":
                out.setdefault(tk, []).append({"kind": "promise", "text": "вы обещали: " + str(it.get("text") or "")[:60]})
            elif it.get("kind") == "deadline" and it.get("due"):
                out.setdefault(tk, []).append({"kind": "deadline", "text": "срок " + ".".join(reversed(str(it["due"]).split("-")[1:]))})
    except Exception as e:
        print(f"[mail_inbox] labels uid={uid} err={e!r}")
    for tk, ls in out.items():  # без повторов
        seen, uniq = set(), []
        for lab in ls:
            if lab["kind"] not in seen:
                seen.add(lab["kind"])
                uniq.append(lab)
        out[tk] = uniq
    return out


def threads(uid: int, *, filt: str = "all", account_id: str = "") -> dict[str, Any]:
    from assistant.services import mail_source

    mine = mail_source.my_addresses(uid)
    rows = mail_index.recent(uid, since=_now() - timedelta(days=LIST_DAYS))
    reads = read_keys(uid)
    mine_threads = mail_index.my_thread_keys(uid)
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if not visible(r, mine_threads) or (account_id and r["account_id"] != account_id):
            continue
        by.setdefault(r.get("thread_key") or r["mail_key"], []).append(r)
    labels = _labels(uid)
    out = []
    for tk, msgs in by.items():
        msgs.sort(key=lambda r: str(r.get("date_utc") or ""))
        people: dict[str, str] = {}
        for m in msgs:
            for a, n in [(m.get("from_addr"), m.get("from_name"))] + [(x, "") for x in (m.get("to") or []) + (m.get("cc") or [])]:
                if a and a not in mine:
                    if n or a not in people:
                        people[a] = n or people.get(a) or ""
        last = msgs[-1]
        unread = [m["mail_key"] for m in msgs if m["direction"] == "in" and m["mail_key"] not in reads]
        out.append({
            "thread_key": tk, "subject": clean_subject(next((m.get("subject") for m in reversed(msgs) if m.get("subject")), "")),
            "people": [{"addr": a, "name": n} for a, n in people.items()][:8], "count": len(msgs), "unread": len(unread),
            "account_id": last["account_id"], "labels": labels.get(tk, []),
            "last": {"mail_key": last["mail_key"], "direction": last["direction"],
                     "from": last.get("from_name") or last.get("from_addr") or "",
                     "snippet": re.sub(r"\s+", " ", str(last.get("snippet") or ""))[:160], "date": last.get("date_utc") or ""},
        })
    out.sort(key=lambda t: t["last"]["date"], reverse=True)
    warm_async(uid, [t["thread_key"] for t in out[:WARM_TOP]])  # открытие верхних веток — без ожидания ящика
    counts = {"all": len(out), "awaiting": sum(1 for t in out if any(lab["kind"] == "awaiting" for lab in t["labels"])),
              "unread": sum(1 for t in out if t["unread"])}
    if filt == "awaiting":
        out = [t for t in out if any(lab["kind"] == "awaiting" for lab in t["labels"])]
    elif filt == "unread":
        out = [t for t in out if t["unread"]]
    return {"threads": out[:MAX_THREADS], "counts": counts,
            "accounts": [{"account_id": a["account_id"], "email": a["email"]} for a in mail_accounts.list_accounts(uid)]}


def _attachment_parts(msg) -> list[Any]:
    """Файлы письма (без встроенных картинок, которые уже в тексте)."""
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = str(part.get("Content-Disposition") or "").lower()
        fn = part.get_filename()
        inline_img = part.get("Content-ID") and (part.get_content_type() or "").startswith("image/") and "attachment" not in disp
        if (fn or "attachment" in disp) and not inline_img:
            out.append(part)
    return out


_THREAD_CACHE: dict[tuple[int, str], tuple[float, str, dict[str, Any]]] = {}
THREAD_TTL = 1800.0
WARM_TOP = 20
_WARM_LOCK = threading.Lock()
_WARMING: dict[int, tuple[set[str], threading.Event]] = {}


def _rows(uid: int, thread_key: str) -> list[dict[str, Any]]:
    return [r for r in mail_index.thread(uid, thread_key) if visible(r, mail_index.my_thread_keys(uid))]


def _cached(uid: int, thread_key: str, rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    import time

    sig = ",".join(r["mail_key"] for r in rows)
    hit = _THREAD_CACHE.get((int(uid), thread_key))
    if not (hit and hit[1] == sig and time.time() - hit[0] < THREAD_TTL and all(m.get("loaded") for m in hit[2]["messages"])):
        return None
    out = dict(hit[2])
    reads = read_keys(uid)
    out["messages"] = [{**m, "unread": m["direction"] == "in" and m["mail_key"] not in reads} for m in out["messages"]]
    return out


def _store(uid: int, thread_key: str, rows: list[dict[str, Any]], res: dict[str, Any]) -> None:
    import time

    _THREAD_CACHE[(int(uid), thread_key)] = (time.time(), ",".join(r["mail_key"] for r in rows), res)
    if len(_THREAD_CACHE) > 300:
        for k in sorted(_THREAD_CACHE, key=lambda k: _THREAD_CACHE[k][0])[:60]:
            _THREAD_CACHE.pop(k, None)


def thread(uid: int, thread_key: str) -> dict[str, Any]:
    """Ветка с текстами из ящика. Кэш на 30 минут (пока в ветке не появились новые письма);
    верхние ветки списка подгружаются заранее (warm), открытие — без входа в ящик."""
    rows = _rows(uid, thread_key)
    hit = _cached(uid, thread_key, rows)
    if hit:
        return hit
    with _WARM_LOCK:
        warming = _WARMING.get(int(uid))
    if warming and thread_key in warming[0]:
        warming[1].wait(timeout=20)  # эту ветку уже достают из ящика — ждём, а не входим второй раз
        hit = _cached(uid, thread_key, rows)
        if hit:
            return hit
    res = _build(uid, thread_key, rows, None)
    _store(uid, thread_key, rows, res)
    return res


def warm(uid: int, thread_keys: list[str]) -> int:
    """Подгрузить ветки заранее: один вход в ящик на все письма, затем разбор каждой ветки."""
    from assistant.services import mail_reply

    todo: dict[str, list[dict[str, Any]]] = {}
    for tk in thread_keys:
        rows = _rows(uid, tk)
        if rows and _cached(uid, tk, rows) is None:
            todo[tk] = rows
    if not todo:
        return 0
    ev = threading.Event()
    with _WARM_LOCK:
        if int(uid) in _WARMING:
            return 0
        _WARMING[int(uid)] = (set(todo), ev)
    try:
        all_rows = [r for rows in todo.values() for r in rows]
        try:
            full = mail_reply._fetch_full(uid, all_rows)
        except Exception as e:
            print(f"[mail_inbox] warm fetch uid={uid} err={e!r}")
            return 0
        for tk, rows in todo.items():
            try:
                _store(uid, tk, rows, _build(uid, tk, rows, full))
            except Exception as e:
                print(f"[mail_inbox] warm build uid={uid} err={e!r}")
        return len(todo)
    finally:
        ev.set()
        with _WARM_LOCK:
            _WARMING.pop(int(uid), None)


def warm_async(uid: int, thread_keys: list[str]) -> None:
    if thread_keys:
        threading.Thread(target=warm, args=(int(uid), list(thread_keys)), daemon=True).start()


def _build(uid: int, thread_key: str, rows: list[dict[str, Any]], full: Optional[dict[str, Any]]) -> dict[str, Any]:
    from assistant.integrations import mail_imap
    from assistant.lib import mail_html
    from assistant.services import mail_reply, mail_source

    if not rows:
        raise LookupError("Переписка не найдена")
    if full is None or any(r["mail_key"] not in full for r in rows):
        try:
            full = {**(full or {}), **mail_reply._fetch_full(uid, [r for r in rows if r["mail_key"] not in (full or {})])}
        except Exception as e:
            print(f"[mail_inbox] fetch thread uid={uid} err={e!r}")
            full = full or {}
    by_mid = {r.get("message_id"): r["mail_key"] for r in rows if r.get("message_id")}
    mine = mail_source.my_addresses(uid)
    reads = read_keys(uid)
    msgs = []
    for r in rows:
        msg = full.get(r["mail_key"])
        text = mail_imap.strip_quoted(mail_imap.body_text(msg)) if msg is not None else str(r.get("snippet") or "")
        rendered = mail_html.render(msg, text) if msg is not None else {
            "html": mail_html.text_to_html(text), "html_full": "", "remote_images": False, "rich": False}
        files = []
        if msg is not None:
            for i, part in enumerate(_attachment_parts(msg)):
                data = part.get_payload(decode=True) or b""
                files.append({"part": i, "name": mail_imap.decode_hdr(part.get_filename()) or "файл",
                              "size": len(data), "type": part.get_content_type()})
        msgs.append({
            "mail_key": r["mail_key"], "direction": r["direction"], "from_addr": r.get("from_addr") or "",
            "from": r.get("from_name") or r.get("from_addr") or "", "to": r.get("to") or [], "cc": r.get("cc") or [],
            "subject": r.get("subject") or "", "date": r.get("date_utc") or "", "text": text[:20000],
            "html": rendered["html"], "html_full": rendered["html_full"], "remote_images": rendered["remote_images"],
            "rich": bool(rendered.get("rich")),
            "attachments": files, "reply_to_key": by_mid.get(r.get("in_reply_to") or "") or "",
            "unread": r["direction"] == "in" and r["mail_key"] not in reads, "loaded": msg is not None,
        })
    people = {}
    for m in msgs:
        for a in [m["from_addr"]] + m["to"] + m["cc"]:
            if a and a not in mine:
                people.setdefault(a, m["from"] if a == m["from_addr"] else "")
    return {"thread_key": thread_key, "subject": clean_subject(next((m["subject"] for m in msgs if m["subject"]), "")),
            "people": [{"addr": a, "name": n} for a, n in people.items()], "messages": msgs,
            "labels": _labels(uid).get(thread_key, []), "account_id": rows[-1]["account_id"]}


def attachment(uid: int, mail_key: str, part: int) -> dict[str, Any]:
    from assistant.integrations import mail_imap
    from assistant.services import mail_reply

    row = mail_index.get(uid, mail_key)
    if not row:
        raise LookupError("Письмо не найдено")
    msg = mail_reply._fetch_full(uid, [row]).get(mail_key)
    if msg is None:
        raise LookupError("Не удалось загрузить письмо из ящика")
    parts = _attachment_parts(msg)
    if not 0 <= part < len(parts):
        raise LookupError("Вложение не найдено")
    p = parts[part]
    return {"name": mail_imap.decode_hdr(p.get_filename()) or "файл", "type": p.get_content_type() or "application/octet-stream",
            "data": p.get_payload(decode=True) or b""}


def open_in_mail_url(uid: int, mail_key: str) -> str:
    """Ссылка «Открыть в почте»: Gmail — поиск по Message-ID, Яндекс — по теме."""
    from urllib.parse import quote

    row = mail_index.get(uid, mail_key)
    if not row:
        return ""
    acc = mail_accounts.get(uid, row["account_id"]) or {}
    if acc.get("provider") == "gmail" and row.get("message_id"):
        return "https://mail.google.com/mail/u/0/#search/" + quote("rfc822msgid:" + row["message_id"].strip("<>"))
    if acc.get("provider") == "yandex":
        return "https://mail.yandex.ru/#search?request=" + quote(clean_subject(row.get("subject") or ""))
    if acc.get("provider") == "mailru":
        return "https://e.mail.ru/search/?q_query=" + quote(clean_subject(row.get("subject") or ""))
    return ""


def save_to_note(uid: int, thread_key: str) -> dict[str, Any]:
    """«Сохранить в заметки»: переписка целиком — заметка (явное действие пользователя)."""
    import html as html_lib

    from assistant.stores import notes as notes_store

    t = thread(uid, thread_key)
    parts = ["<p><b>Участники:</b> " + html_lib.escape(", ".join(p["name"] or p["addr"] for p in t["people"])) + "</p>"]
    for m in t["messages"]:
        who = "Я" if m["direction"] == "out" else (m["from"] or m["from_addr"])
        parts.append(f"<h3>{html_lib.escape(who)} · {html_lib.escape(str(m['date'])[:16].replace('T', ' '))}</h3>")
        parts.append(re.sub(r"<img\b[^>]*>", "", m["html"]))
    return notes_store.create_note(uid, f"Переписка «{t['subject']}»", "".join(parts), meta={"source": "mail", "thread_key": thread_key})
