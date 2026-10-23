"""Разбор и ответ на письма из чата Лео.

Карточки — те же действия ИИ, что у календаря (gpt_calendar_actions):
- email_thread (info) — разбор письма: от кого, тема, суть; кнопки «Ответить», «Подготовить ответ», «Саммари».
- email_draft (proposed → sending → sent | failed; dismissed) — черновик ответа, его открывает окно письма.

Безопасность: письмо уходит только по кнопке («Отправить» → op send) и только после 10 секунд на «Отменить».
Адресатов по умолчанию подставляет код из исходного письма; модель адресатов не задаёт.
Новые адреса (не из ветки) пользователь может добавить сам — отправка тогда требует подтверждения.
Полный текст ветки читается по IMAP в момент запроса и не хранится.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, format_datetime, make_msgid
from typing import Any, Optional

from assistant.integrations import mail_imap, mail_smtp
from assistant.services import mail_source
from assistant.stores import mail_accounts, mail_attachments, mail_index

UNDO_SECONDS = 10
MAX_THREAD = 6
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")

_TIMERS: dict[str, threading.Timer] = {}
_SEND_LOCK = threading.Lock()


class ReplyError(Exception):
    pass


def _gcal():
    from assistant.services import gpt_calendar_tools

    return gpt_calendar_tools


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- ветка письма

def message_row(uid: int, mail_key: str) -> dict[str, Any]:
    row = mail_index.get(uid, mail_key)
    if not row:
        raise ReplyError("Письмо не найдено — возможно, оно старше 90 дней или ящик отключён.")
    return row


def _account(uid: int, account_id: str) -> dict[str, Any]:
    acc = mail_accounts.get(uid, account_id)
    if not acc:
        raise ReplyError("Ящик этого письма отключён — подключите его в профиле → Почта.")
    return acc


def _fetch_full(uid: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """{mail_key: email.message.Message} без отметки «прочитано».

    Сначала — по номеру письма в папке (UID из синхронизации, одним запросом) со сверкой Message-ID;
    не нашлось — поиском по Message-ID (Яндекс не всегда находит письмо поиском по заголовку, поэтому это запасной путь).
    Письма, отправленные из Orbita (папка «sent»), ищутся в «Отправленных» ящика."""
    out: dict[str, Any] = {}
    by_acc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_acc.setdefault(str(r["account_id"]), []).append(r)
    for aid, items in by_acc.items():
        acc = mail_accounts.get(uid, aid)
        if not acc:
            continue
        try:
            mail = mail_source.open_imap(uid, acc)
        except Exception as e:
            print(f"[mail] fetch login uid={uid} acc={aid} err={e!r}")
            continue
        try:
            sent_name = None
            by_folder: dict[str, list[dict[str, Any]]] = {}
            for r in items:
                folder = str(r.get("folder") or "INBOX")
                if folder == "sent":
                    sent_name = sent_name or mail_imap.sent_folder(mail) or ""
                    folder = sent_name
                if folder:
                    by_folder.setdefault(folder, []).append(r)
            for folder, frows in by_folder.items():
                try:
                    mail_imap.select(mail, folder)
                except Exception:
                    continue
                with_uid = [r for r in frows if int(r.get("uid") or 0) > 0]
                got = mail_imap.fetch(mail, [int(r["uid"]) for r in with_uid], "BODY.PEEK[]") if with_uid else {}
                for r in frows:
                    msg = None
                    hit = got.get(int(r.get("uid") or 0))
                    if hit:
                        msg = mail_imap.parse(hit[1])
                        mid = (mail_imap.msg_ids(msg.get("Message-ID")) or [""])[0]
                        if r.get("message_id") and mid and mid != r["message_id"]:
                            msg = None  # номер сменился (UIDVALIDITY) — ищем по Message-ID
                    if msg is None and r.get("message_id"):
                        u = mail_imap.find_by_message_id(mail, r["message_id"])
                        if u:
                            g2 = mail_imap.fetch(mail, [int(u)], "BODY.PEEK[]")
                            if g2:
                                msg = mail_imap.parse(next(iter(g2.values()))[1])
                    if msg is not None:
                        out[r["mail_key"]] = msg
        except Exception as e:
            print(f"[mail] fetch uid={uid} acc={aid} err={e!r}")
        finally:
            try:
                mail.logout()
            except Exception:
                pass
    return out


def thread_messages(uid: int, mail_key: str, *, limit: int = MAX_THREAD) -> list[dict[str, Any]]:
    """Последние письма ветки с текстом (только то, что написал автор, без цитат)."""
    row = message_row(uid, mail_key)
    rows = mail_index.thread(uid, row["thread_key"]) if row.get("thread_key") else [row]
    rows = rows[-limit:] or [row]
    full = _fetch_full(uid, rows)
    out = []
    for r in rows:
        msg = full.get(r["mail_key"])
        text = mail_imap.strip_quoted(mail_imap.body_text(msg)) if msg is not None else str(r.get("snippet") or "")
        out.append({
            "mail_key": r["mail_key"], "direction": r["direction"],
            "from": f"{r.get('from_name') or ''} <{r.get('from_addr')}>".strip(), "to": r.get("to") or [],
            "cc": r.get("cc") or [], "subject": r.get("subject") or "", "date": r.get("date_utc") or "",
            "text": text[:4000], "attachments": mail_imap.attachments(msg) if msg is not None else [],
        })
    return out


def style_samples(uid: int, addr: str, *, limit: int = 2) -> list[str]:
    """Как пользователь сам писал этому адресату — чтобы черновик звучал как он."""
    rows = [r for r in mail_index.recent(uid, since=_now() - timedelta(days=180), direction="out") if addr in (r.get("to") or [])]
    rows = rows[-limit:]
    if not rows:
        return []
    stored = mail_index.bodies(uid, [r["mail_key"] for r in rows])  # сохранённые письма — без входа в ящик
    out = [str(b.get("text") or "")[:800] for b in stored.values() if b.get("text")]
    missing = [r for r in rows if r["mail_key"] not in stored]
    if not missing:
        return out
    try:
        full = _fetch_full(uid, missing)
    except Exception as e:
        print(f"[mail] style samples uid={uid} err={e!r}")
        return out
    return out + [mail_imap.strip_quoted(mail_imap.body_text(m))[:800] for m in full.values() if m is not None]


def _who(row: dict[str, Any]) -> str:
    return str(row.get("from_name") or row.get("from_addr") or "")


def _day_label(iso: str, uid: int) -> str:
    from assistant.services.calendar import _tz_for

    try:
        d = datetime.fromisoformat(iso).astimezone(_tz_for(uid))
    except ValueError:
        return ""
    return d.strftime("%d.%m %H:%M")


def thread_card(uid: int, mail_key: str) -> str:
    """Карточка разбора письма в чате (id действия)."""
    row = message_row(uid, mail_key)
    gist = re.sub(r"\s+", " ", str(row.get("snippet") or "")).strip()[:220]
    return _gcal().record_action(
        uid, "email_thread", "info", title=str(row.get("subject") or "Без темы"),
        payload={"mail_key": mail_key, "from": _who(row), "from_addr": row.get("from_addr") or "",
                 "date": row.get("date_utc") or "", "when": _day_label(str(row.get("date_utc") or ""), uid),
                 "gist": gist, "direction": row.get("direction"), "has_attachments": bool(row.get("has_attachments"))},
    )


# ---------------------------------------------------------------- черновик

def thread_people(uid: int, row: dict[str, Any]) -> set[str]:
    """Все адреса ветки — им можно отвечать без подтверждения."""
    rows = mail_index.thread(uid, row["thread_key"]) if row.get("thread_key") else [row]
    out: set[str] = set()
    for r in rows or [row]:
        out.add(str(r.get("from_addr") or ""))
        if r.get("reply_to"):
            out.add(str(r["reply_to"]))
        out.update(r.get("to") or [])
        out.update(r.get("cc") or [])
    return {a.lower() for a in out if a}


def default_recipients(row: dict[str, Any], mine: set[str], *, reply_all: bool) -> tuple[list[str], list[str]]:
    """Кому/Копия по правилам почты: Reply-To, иначе From; «Ответить всем» — плюс To и CC исходного письма."""
    if row.get("direction") == "out":
        to = [a for a in row.get("to") or [] if a not in mine]
    else:
        to = [str(row.get("reply_to") or row.get("from_addr") or "")]
    to = [a for a in to if a]
    cc: list[str] = []
    if reply_all:
        for a in list(row.get("to") or []) + list(row.get("cc") or []):
            if a not in mine and a not in to and a not in cc:
                cc.append(a)
    return to, cc


def _subject(subj: str) -> str:
    subj = (subj or "").strip()
    return subj if re.match(r"(?i)^(re|ответ)\s*:", subj) else f"Re: {subj}".strip()


def create_draft(uid: int, mail_key: str, *, body: str = "", reply_all: bool = False, by: str = "user") -> str:
    row = message_row(uid, mail_key)
    acc = _account(uid, str(row["account_id"]))
    mine = mail_source.my_addresses(uid)
    to, cc = default_recipients(row, mine, reply_all=reply_all)
    refs = list(row.get("refs") or [])
    if row.get("message_id") and row["message_id"] not in refs:
        refs.append(row["message_id"])
    payload = {
        "mail_key": mail_key, "account_id": acc["account_id"], "from": acc["email"], "to": to, "cc": cc,
        "reply_all": reply_all, "subject": _subject(str(row.get("subject") or "")), "body": body.strip(),
        "in_reply_to": row.get("message_id") or "", "references": refs, "attachments": [], "by": by,
        "orig_from": _who(row), "orig_date": row.get("date_utc") or "",
        "send_enabled": bool(acc.get("send_enabled")),
    }
    return _gcal().record_action(uid, "email_draft", "proposed", title=payload["subject"], payload=payload)


def _draft(uid: int, action_id: str) -> dict[str, Any]:
    a = _gcal().get_action(action_id)
    if not a or int(a.get("owner_uid") or 0) != int(uid) or a.get("kind") != "email_draft":
        raise LookupError("Черновик не найден")
    return a


def _clean_addrs(raw: Any) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", str(raw or ""))
    out = []
    for x in items:
        a = str(x or "").strip().strip("<>").lower()
        if not a:
            continue
        if not EMAIL_RE.match(a):
            raise ReplyError(f"Похоже, в адресе «{a}» опечатка.")
        if a not in out:
            out.append(a)
    return out


def update_draft(uid: int, action_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Правки из окна письма. Адресатов проверяет код; новые (не из ветки) помечаются для подтверждения."""
    a = _draft(uid, action_id)
    if a["status"] not in ("proposed", "failed"):
        raise ReplyError("Письмо уже отправляется или отправлено.")
    p = dict(a.get("payload") or {})
    if "reply_all" in patch and bool(patch["reply_all"]) != bool(p.get("reply_all")):
        row = message_row(uid, p["mail_key"])
        p["to"], p["cc"] = default_recipients(row, mail_source.my_addresses(uid), reply_all=bool(patch["reply_all"]))
        p["reply_all"] = bool(patch["reply_all"])
    for k in ("to", "cc"):
        if k in patch:
            p[k] = _clean_addrs(patch[k])
    if "subject" in patch:
        p["subject"] = str(patch["subject"] or "").strip()[:300]
    if "body" in patch:
        p["body"] = str(patch["body"] or "")[:50000]
    if "attachments" in patch:
        p["attachments"] = [str(x) for x in (patch["attachments"] or [])][:20]
        mail_attachments.check_total(uid, p["attachments"])
    if "account_id" in patch and mail_accounts.get(uid, str(patch["account_id"])):
        p["account_id"] = str(patch["account_id"])
        p["from"] = mail_accounts.get(uid, p["account_id"])["email"]
    _save_payload(action_id, p, title=p.get("subject") or "Ответ", status="proposed")
    return _gcal().get_action(action_id) or a


def _save_payload(action_id: str, payload: dict[str, Any], *, title: str, status: str) -> None:
    import json

    g = _gcal()
    with g._lock():
        conn = g._conn()
        conn.execute(
            "UPDATE gpt_calendar_actions SET payload_json = ?, title = ?, status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False, default=str), title[:300], status, g._now_iso(), action_id),
        )
        conn.commit()


def new_recipients(uid: int, a: dict[str, Any]) -> list[str]:
    p = a.get("payload") or {}
    row = mail_index.get(uid, str(p.get("mail_key") or ""))
    allowed = thread_people(uid, row) if row else set()
    return [x for x in list(p.get("to") or []) + list(p.get("cc") or []) if x.lower() not in allowed]


# ---------------------------------------------------------------- отправка

def build_message(uid: int, p: dict[str, Any]) -> EmailMessage:
    from assistant.stores import note_members

    acc = _account(uid, str(p["account_id"]))
    name = ""
    try:
        name = note_members.profile_display_name(note_members.get_profile(uid) or {}, fallback="")
    except Exception:
        pass
    msg = EmailMessage()
    msg["From"] = formataddr((name, acc["email"])) if name else acc["email"]
    msg["To"] = ", ".join(p.get("to") or [])
    if p.get("cc"):
        msg["Cc"] = ", ".join(p["cc"])
    msg["Subject"] = p.get("subject") or ""
    msg["Date"] = format_datetime(_now())
    domain = str(acc["email"]).rsplit("@", 1)[-1]
    msg["Message-ID"] = make_msgid(domain=domain)
    if p.get("in_reply_to"):
        msg["In-Reply-To"] = p["in_reply_to"]
    if p.get("references"):
        msg["References"] = " ".join(p["references"])
    text = str(p.get("body") or "").rstrip()
    sig = mail_accounts.signature(uid)
    if sig and sig not in text:
        text += "\n\n--\n" + sig
    quote = _quote(uid, p)
    if quote:
        text += "\n\n" + quote
    msg.set_content(text + "\n")
    for att in mail_attachments.load(uid, list(p.get("attachments") or [])):
        maintype, _, subtype = (att["type"] or "application/octet-stream").partition("/")
        msg.add_attachment(att["data"], maintype=maintype, subtype=subtype or "octet-stream", filename=att["name"])
    return msg


def _quote(uid: int, p: dict[str, Any]) -> str:
    """Цитата исходного письма под ответом: «22.10.2026 10:00, Иван пишет:» и текст с «> »."""
    row = mail_index.get(uid, str(p.get("mail_key") or ""))
    if not row:
        return ""
    try:
        full = _fetch_full(uid, [row]).get(row["mail_key"])
        text = mail_imap.body_text(full) if full is not None else str(row.get("snippet") or "")
    except Exception:
        text = str(row.get("snippet") or "")
    if not text.strip():
        return ""
    head = f"{_day_label(str(row.get('date_utc') or ''), uid)}, {_who(row)} пишет:".strip(", ")
    return head + "\n" + "\n".join("> " + ln for ln in text.strip().splitlines()[:200])


def request_send(uid: int, action_id: str, *, confirm_new: bool = False) -> dict[str, Any]:
    """«Отправить»: проверить, перевести в sending и отправить через UNDO_SECONDS (можно отменить)."""
    a = _draft(uid, action_id)
    if a["status"] in ("sending", "sent"):
        return a
    if a["status"] not in ("proposed", "failed"):
        raise ReplyError("Черновик закрыт.")
    p = a.get("payload") or {}
    acc = _account(uid, str(p.get("account_id") or ""))
    if not acc.get("send_enabled"):
        raise ReplyError("Отправка из Orbita выключена — включите «Отвечать из Orbita» в профиле → Почта.")
    if not p.get("to"):
        raise ReplyError("Укажите, кому отправить.")
    if not str(p.get("body") or "").strip():
        raise ReplyError("Письмо пустое — напишите текст.")
    fresh = new_recipients(uid, a)
    if fresh and not confirm_new:
        raise NeedsConfirm(fresh)
    send_at = _now() + timedelta(seconds=UNDO_SECONDS)
    _gcal().set_action_status(action_id, "sending", result={"send_at": send_at.isoformat()})
    t = threading.Timer(UNDO_SECONDS, _send_now, args=(uid, action_id))
    t.daemon = True
    _TIMERS[action_id] = t
    t.start()
    return _gcal().get_action(action_id) or a


class NeedsConfirm(Exception):
    def __init__(self, addrs: list[str]) -> None:
        super().__init__("Новый получатель: " + ", ".join(addrs) + " — отправить?")
        self.addrs = addrs


def cancel_send(uid: int, action_id: str) -> dict[str, Any]:
    a = _draft(uid, action_id)
    with _SEND_LOCK:
        a = _gcal().get_action(action_id) or a
        if a["status"] != "sending":
            return a
        t = _TIMERS.pop(action_id, None)
        if t:
            t.cancel()
        _gcal().set_action_status(action_id, "proposed", result={})
    return _gcal().get_action(action_id) or a


def _send_now(uid: int, action_id: str) -> None:
    with _SEND_LOCK:
        a = _gcal().get_action(action_id)
        if not a or a["status"] != "sending":
            return  # отменили
        _gcal().set_action_status(action_id, "sending", result={**(a.get("result") or {}), "sending_now": True})
    _TIMERS.pop(action_id, None)
    p = a.get("payload") or {}
    try:
        acc = _account(uid, str(p["account_id"]))
        msg = build_message(uid, p)
        mail_smtp.send(acc, msg, **mail_source.credentials(uid, acc))
    except (mail_smtp.MailSendError, ReplyError) as e:
        _gcal().set_action_status(action_id, "failed", result={"error": str(e)})
        return
    except Exception as e:
        print(f"[mail] send uid={uid} action={action_id} err={e!r}")
        _gcal().set_action_status(action_id, "failed", result={"error": "Не удалось отправить письмо. Попробуйте ещё раз."})
        return
    sent_at = _now()
    _gcal().set_action_status(action_id, "sent", result={"sent_at": sent_at.isoformat(), "message_id": msg["Message-ID"]})
    try:
        _after_send(uid, acc, p, msg, sent_at)
    except Exception as e:
        print(f"[mail] after send uid={uid} err={e!r}")


def _after_send(uid: int, acc: dict[str, Any], p: dict[str, Any], msg: EmailMessage, sent_at: datetime) -> None:
    """Письмо — в индекс (ветка «я ответил»), копия — в «Отправленные», если сервер не положил сам; вложения — стереть."""
    mid = str(msg["Message-ID"])
    refs = list(p.get("references") or [])
    mail_index.upsert(uid, {
        "mail_key": mail_index.mail_key(str(acc["account_id"]), mid, "sent", 0), "account_id": acc["account_id"],
        "folder": "sent", "message_id": mid, "thread_key": mail_index.thread_key_for(mid, p.get("in_reply_to") or "", refs),
        "in_reply_to": p.get("in_reply_to") or "", "refs": refs, "direction": "out", "from_addr": acc["email"],
        "to": p.get("to") or [], "cc": p.get("cc") or [], "subject": p.get("subject") or "",
        "date_utc": sent_at.isoformat(), "snippet": str(p.get("body") or "")[: mail_index.SNIPPET],
    })
    mail_attachments.remove(uid, list(p.get("attachments") or []))
    threading.Timer(8, _ensure_in_sent, args=(uid, acc, msg)).start()


def _ensure_in_sent(uid: int, acc: dict[str, Any], msg: EmailMessage) -> None:
    import imaplib
    import time

    try:
        mail = mail_source.open_imap(uid, acc)
    except Exception as e:
        print(f"[mail] sent copy login uid={uid} err={e!r}")
        return
    try:
        folder = mail_imap.sent_folder(mail)
        if not folder:
            return
        mail.select(folder)
        if mail_imap.find_by_message_id(mail, str(msg["Message-ID"])):
            return  # сервер положил сам (Gmail, Яндекс)
        mail.append(folder, "(\\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    except Exception as e:
        print(f"[mail] sent copy uid={uid} err={e!r}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def resume_stuck(uid: int, a: dict[str, Any]) -> None:
    """После перезапуска сервера таймер пропал: «sending» дольше минуты — отправляем сразу."""
    if a.get("kind") != "email_draft" or a.get("status") != "sending" or a.get("id") in _TIMERS:
        return
    try:
        send_at = datetime.fromisoformat(str((a.get("result") or {}).get("send_at") or ""))
    except ValueError:
        return
    if _now() - send_at > timedelta(seconds=30) and not (a.get("result") or {}).get("sending_now"):
        threading.Thread(target=_send_now, args=(uid, a["id"]), daemon=True).start()


# ---------------------------------------------------------------- для клиента

def public_extra(a: dict[str, Any]) -> dict[str, Any]:
    """Поля карточек email_* для клиента (в public_action)."""
    p = a.get("payload") or {}
    res = a.get("result") or {}
    if a["kind"] == "email_thread":
        return {"mail": {k: p.get(k) for k in ("mail_key", "from", "from_addr", "when", "gist", "direction", "has_attachments")}}
    uid = int(a.get("owner_uid") or 0)
    if a.get("status") == "sending":
        resume_stuck(uid, a)
    accounts = [{"account_id": x["account_id"], "email": x["email"], "send_enabled": x["send_enabled"]}
                for x in mail_accounts.list_accounts(uid)]
    draft = {k: p.get(k) for k in ("mail_key", "account_id", "from", "to", "cc", "reply_all", "subject", "body",
                                   "orig_from", "orig_date", "by")}
    draft["attachments"] = mail_attachments.describe(uid, list(p.get("attachments") or []))
    draft["accounts"] = accounts
    draft["send_at"] = res.get("send_at") or ""
    draft["sent_at"] = res.get("sent_at") or ""
    draft["error"] = res.get("error") or ""
    draft["created_at"] = a.get("created_at") or ""
    try:
        draft["new_recipients"] = new_recipients(uid, a)
    except Exception:
        draft["new_recipients"] = []
    return {"draft": draft}
