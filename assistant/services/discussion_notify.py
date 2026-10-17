"""Уведомления о новых сообщениях в обсуждениях: Telegram и Web Push."""

from __future__ import annotations

import base64
import json
import os
from html import escape as html_escape
from typing import Any
from urllib.parse import quote

from assistant.stores import discussion_presence
from assistant.stores import user_prefs

_VAPID_PRIVATE_CACHE: str | None = None


def vapid_public_key() -> str:
    return (os.getenv("VAPID_PUBLIC_KEY") or "").strip()


def _normalize_pem(raw: str) -> str:
    """В .env PEM часто лежит с литеральными \\n — pywebpush так не читает."""
    s = (raw or "").strip().strip('"').strip("'")
    if "BEGIN" in s and "\n" not in s:
        s = s.replace("\\n", "\n")
    return s


def vapid_private_key() -> str:
    """Ключ в формате, который понимает pywebpush (raw urlsafe base64 или PEM с \\n).

    В проде VAPID_PRIVATE_KEY часто = PEM с литеральными \\n. pywebpush/Vapid.from_string
    тогда падает с Invalid base64 — конвертируем PEM → 32-байтовый raw urlsafe b64.
    """
    global _VAPID_PRIVATE_CACHE
    if _VAPID_PRIVATE_CACHE is not None:
        return _VAPID_PRIVATE_CACHE
    raw = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
    if not raw:
        _VAPID_PRIVATE_CACHE = ""
        return ""
    pem = _normalize_pem(raw)
    if "BEGIN" in pem:
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            key = load_pem_private_key(pem.encode("utf-8"), password=None)
            raw32 = key.private_numbers().private_value.to_bytes(32, "big")
            _VAPID_PRIVATE_CACHE = base64.urlsafe_b64encode(raw32).decode("ascii").rstrip("=")
            return _VAPID_PRIVATE_CACHE
        except Exception as e:
            print(f"[discuss_notify] vapid_pem_convert_fail err={e!r}")
            _VAPID_PRIVATE_CACHE = pem
            return pem
    _VAPID_PRIVATE_CACHE = pem
    return pem


def vapid_subject() -> str:
    return (os.getenv("VAPID_SUBJECT") or "mailto:leo@assistent.networ.ru").strip()


def vapid_configured() -> bool:
    return bool(vapid_public_key() and vapid_private_key())


def _b64_pad(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return t
    return t + ("=" * ((4 - len(t) % 4) % 4))


def discussion_webapp_url(kind: str, item_id: str) -> str:
    from assistant.lib.webapp_public import webapp_entry_url

    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    kind = (kind or "").strip()
    raw = str(item_id or "").strip()
    if kind == "local":
        return f"{base}{sep}note={quote(raw, safe='')}"
    if kind == "chat":
        return f"{base}{sep}chat={quote(raw, safe='')}"
    return base


def discussion_item_title(owner: str, kind: str, item_id: str) -> str:
    kind = (kind or "").strip()
    if kind == "local":
        from assistant.stores import notes as notes_store

        try:
            note = notes_store.get_note(owner, int(item_id))
        except (TypeError, ValueError):
            note = None
        if note:
            return str(note.get("title") or note.get("content") or "Заметка").strip() or "Заметка"
        return "Заметка"
    if kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store

        thread = chat_threads_store.get_thread(owner, item_id)
        if thread:
            return str(thread.get("title") or "Чат").strip() or "Чат"
        return "Чат"
    return "Обсуждение"


def list_discussion_participants(
    owner: str, kind: str, item_id: str
) -> list[dict[str, Any]]:
    kind = (kind or "").strip()
    members: list[dict[str, Any]] = []
    if kind == "local":
        from assistant.stores import note_members

        try:
            members = note_members.list_members_public(owner, int(item_id))
        except (TypeError, ValueError):
            members = []
    elif kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store

        members = chat_threads_store.list_members_public(owner, item_id)
    if members:
        return members
    owner_id = str(owner or "").strip()
    if kind in ("local", "chat") and owner_id:
        return [{"user_id": owner_id, "name": "Автор", "username": ""}]
    return []


def _preview(body: str, *, limit: int = 180) -> str:
    text = " ".join(str(body or "").split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or "Новое сообщение"


def _send_telegram(user_id: int, text: str) -> None:
    from assistant.lib.telegram_notify import send_message

    send_message(int(user_id), text)


def send_web_push(user_id: int | str, payload: dict[str, Any]) -> int:
    """Отправить Web Push на все подписки пользователя. Возвращает число успешных."""
    if not vapid_configured():
        return 0
    from assistant.stores import push_subscriptions

    try:
        from pywebpush import webpush
    except ImportError:
        print("[discuss_notify] pywebpush is not installed")
        return 0

    rows = push_subscriptions.list_for_user(user_id)
    if not rows:
        return 0
    data = json.dumps(payload, ensure_ascii=False)
    claims = {"sub": vapid_subject()}
    private = vapid_private_key()
    if not private:
        return 0
    sent = 0
    for row in rows:
        try:
            webpush(
                subscription_info={
                    "endpoint": row["endpoint"],
                    "keys": {
                        "p256dh": _b64_pad(str(row.get("p256dh") or "")),
                        "auth": _b64_pad(str(row.get("auth") or "")),
                    },
                },
                data=data,
                vapid_private_key=private,
                vapid_claims=claims,
            )
            sent += 1
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                push_subscriptions.delete(row["endpoint"])
            else:
                print(f"[discuss_notify] webpush_fail uid={user_id} err={e!r}")
    return sent


def notify_discussion_comment(
    *,
    owner: str,
    kind: str,
    item_id: str,
    author_user_id: str,
    author_name: str,
    author_username: str | None = None,
    body: str = "",
    is_assistant: bool = False,
) -> list[str]:
    """Пуш участникам, у которых сейчас не открыто это обсуждение.

    Возвращает user_id тех, кому ушло хотя бы в один канал.
    """
    from assistant.stores import note_members

    members = list_discussion_participants(owner, kind, item_id)
    if not members:
        return []
    present = discussion_presence.present_user_ids(owner, kind, item_id)
    skip = set(present)
    if not is_assistant:
        skip.add(str(author_user_id))
    title = discussion_item_title(owner, kind, item_id)
    url = discussion_webapp_url(kind, item_id)
    who_plain = str(author_name or "").strip() or ("GPT" if is_assistant else "Участник")
    if is_assistant:
        who_html = html_escape(who_plain)
    else:
        who_html = note_members.requester_label(
            author_username, author_name, str(author_user_id)
        )
    preview = _preview(body)
    link = f'<a href="{html_escape(url, quote=True)}">{html_escape(title or "Обсуждение")}</a>'
    telegram_text = f"{who_html} в обсуждении {link}:\n{html_escape(preview)}"
    web_payload = {
        "title": who_plain,
        "body": f"{title}: {preview}",
        "url": url,
    }
    notified: list[str] = []
    for m in members:
        mid = str(m.get("user_id") or "").strip()
        if not mid or mid in skip:
            continue
        try:
            uid = int(mid)
        except (TypeError, ValueError):
            continue
        sent = False
        try:
            want_tg = user_prefs.discussion_notify_telegram(uid)
            want_wp = user_prefs.discussion_notify_web_push(uid)
        except Exception:
            want_tg = True
            want_wp = True
        if not want_tg and not want_wp:
            continue
        if want_tg:
            try:
                _send_telegram(uid, telegram_text)
                sent = True
            except Exception as e:
                print(f"[discuss_notify] telegram_fail uid={mid} err={e!r}")
        if want_wp:
            try:
                if send_web_push(uid, web_payload):
                    sent = True
            except Exception as e:
                print(f"[discuss_notify] webpush_uid_fail uid={mid} err={e!r}")
        if sent:
            notified.append(mid)
    return notified


def notify_discussion_comment_safe(**kwargs: Any) -> list[str]:
    try:
        return notify_discussion_comment(**kwargs)
    except Exception as e:
        print(f"[discuss_notify] fail err={e!r}")
        return []
