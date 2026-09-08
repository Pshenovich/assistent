"""Отправка сообщений пользователю Telegram (HTTP API). Для usage_server / webhooks."""

from __future__ import annotations

import os
from typing import Any, Optional

import requests


def _bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()


def _bot_api_url(method: str) -> str:
    token = _bot_token()
    custom = (os.getenv("TELEGRAM_BOT_API_BASE_URL", "") or "").strip().rstrip("/")
    if custom:
        return f"{custom}/bot{token}/{method}"
    return f"https://api.telegram.org/bot{token}/{method}"


def _rich_api_url(method: str) -> str:
    """Rich Message API: по умолчанию облако (локальный Bot API часто без sendRichMessage)."""
    token = _bot_token()
    custom = (os.getenv("TELEGRAM_RICH_BOT_API_BASE_URL", "") or "").strip().rstrip("/")
    if not custom:
        custom = "https://api.telegram.org"
    return f"{custom}/bot{token}/{method}"


def send_rich_message_return_id(
    telegram_user_id: int,
    body: str,
    *,
    markdown: bool = False,
    reply_to_message_id: int | None = None,
    reply_markup: dict | None = None,
    disable_web_page_preview: bool = True,
) -> int | None:
    """sendRichMessage через HTTP API. Возвращает message_id или None."""
    content = (body or "").strip()
    if not content:
        return None
    rich_payload = {"markdown": content} if markdown else {"html": content}
    payload: dict = {
        "chat_id": int(telegram_user_id),
        "rich_message": rich_payload,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(_rich_api_url("sendRichMessage"), json=payload, timeout=25)
        if r.ok:
            try:
                data = r.json()
            except Exception:
                data = {}
            if isinstance(data, dict) and data.get("ok"):
                result = data.get("result")
                if isinstance(result, dict) and result.get("message_id") is not None:
                    return int(result["message_id"])
                # Sent successfully but API omitted message_id (0 = sent, unknown id)
                return 0
            print(
                f"[telegram_notify] rich_unexpected uid={telegram_user_id} "
                f"body={(r.text or '')[:200]!r}"
            )
            return None
        print(
            f"[telegram_notify] rich_failed uid={telegram_user_id} "
            f"status={r.status_code} body={(r.text or '')[:300]!r}"
        )
    except requests.RequestException as e:
        print(f"[telegram_notify] rich_error uid={telegram_user_id} err={e!r}")
    return None


def send_rich_message(
    telegram_user_id: int,
    body: str,
    *,
    markdown: bool = False,
    reply_to_message_id: int | None = None,
    reply_markup: dict | None = None,
    disable_web_page_preview: bool = True,
) -> bool:
    """sendRichMessage через HTTP API (html или markdown)."""
    mid = send_rich_message_return_id(
        telegram_user_id,
        body,
        markdown=markdown,
        reply_to_message_id=reply_to_message_id,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )
    return mid is not None


def send_user_message_return_id(
    telegram_user_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = False,
    reply_markup: dict | None = None,
) -> int | None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token or not text.strip():
        return None
    payload: dict = {
        "chat_id": int(telegram_user_id),
        "text": text.strip(),
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(
            _bot_api_url("sendMessage"),
            json=payload,
            timeout=25,
        )
        if not r.ok:
            print(f"[telegram_notify] send_failed uid={telegram_user_id} status={r.status_code}")
            return None
        data = r.json()
        if data.get("ok") and isinstance(data.get("result"), dict):
            mid = data["result"].get("message_id")
            if mid is not None:
                return int(mid)
        return None
    except requests.RequestException as e:
        print(f"[telegram_notify] send_error uid={telegram_user_id} err={e!r}")
        return None


def send_user_message(
    telegram_user_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = False,
    reply_markup: dict | None = None,
) -> bool:
    return (
        send_user_message_return_id(
            telegram_user_id,
            text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup,
        )
        is not None
    )


def edit_user_message(
    telegram_user_id: int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = False,
) -> bool:
    token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    body = (text or "").strip()
    if not token or not body or not message_id:
        return False
    payload: dict = {
        "chat_id": int(telegram_user_id),
        "message_id": int(message_id),
        "text": body,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(
            _bot_api_url("editMessageText"),
            json=payload,
            timeout=25,
        )
        if r.ok:
            return True
        err = (r.text or "").lower()
        if "message is not modified" in err:
            return True
        print(
            f"[telegram_notify] edit_failed uid={telegram_user_id} "
            f"mid={message_id} status={r.status_code}"
        )
        return False
    except requests.RequestException as e:
        print(f"[telegram_notify] edit_error uid={telegram_user_id} err={e!r}")
        return False


def _status_parse_mode(text: str) -> str | None:
    body = text or ""
    if "**" in body:
        return "Markdown"
    return None


def upsert_user_status_message(
    telegram_user_id: int,
    text: str,
    *,
    message_id: int | None = None,
) -> int | None:
    """Отредактировать статусное сообщение или отправить новое. Возвращает message_id."""
    body = (text or "").strip()
    if not body:
        return message_id
    parse_mode = _status_parse_mode(body)
    if message_id and edit_user_message(
        telegram_user_id, message_id, body, parse_mode=parse_mode
    ):
        return int(message_id)
    mid = send_user_message_return_id(
        telegram_user_id, body, parse_mode=parse_mode
    )
    if mid:
        return mid
    if parse_mode:
        if message_id and edit_user_message(telegram_user_id, message_id, body):
            return int(message_id)
        return send_user_message_return_id(telegram_user_id, body)
    return None


def send_user_long_text(
    telegram_user_id: int,
    text: str,
    *,
    header: str = "",
    disable_web_page_preview: bool = True,
) -> bool:
    """Разбивает длинный текст на несколько сообщений Telegram."""
    body = (text or "").strip()
    if header:
        body = f"{header.strip()}\n\n{body}" if body else header.strip()
    if not body:
        return False
    ok = True
    chunk_size = 3800
    for i in range(0, len(body), chunk_size):
        if not send_user_message(
            telegram_user_id,
            body[i : i + chunk_size],
            disable_web_page_preview=disable_web_page_preview,
        ):
            ok = False
    return ok


def send_user_html_long_text(
    telegram_user_id: int,
    html: str,
    *,
    header: str = "",
    disable_web_page_preview: bool = True,
    reply_markup: dict | None = None,
) -> bool:
    """Rich/HTML сообщение несколькими частями."""
    from assistant.lib.telegram_html import (
        html_to_plain,
        prepare_classic_html,
        prepare_rich_html,
        split_telegram_html,
    )

    body = (html or "").strip()
    if header:
        hdr = (header or "").strip()
        if hdr.startswith("<"):
            body = f"{hdr}\n{body}" if body else hdr
        else:
            body = f"{hdr}\n\n{body}" if body else hdr
    if not body:
        return False
    rich_body = prepare_rich_html(body)
    if send_rich_message(
        int(telegram_user_id),
        rich_body,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    ):
        return True
    body = prepare_classic_html(body)
    if not body:
        return False
    chunk_size = int(os.getenv("TELEGRAM_HTML_CHUNK_SIZE", "3800") or "3800")
    ok = True
    chunks = split_telegram_html(body, chunk_size) or [body[:chunk_size]]
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        sent = send_user_message(
            int(telegram_user_id),
            chunk,
            parse_mode="HTML",
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=markup,
        )
        if not sent:
            sent = send_user_message(
                int(telegram_user_id),
                html_to_plain(chunk),
                disable_web_page_preview=disable_web_page_preview,
                reply_markup=markup,
            )
        if not sent:
            ok = False
    return ok


def send_user_document(
    telegram_user_id: int,
    data: bytes,
    *,
    filename: str,
    caption: str | None = None,
) -> bool:
    token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token or not data:
        return False
    files = {"document": (filename or "file.pdf", data, "application/pdf")}
    form: dict = {"chat_id": str(int(telegram_user_id))}
    cap = (caption or "").strip()
    if cap:
        form["caption"] = cap[:1024]
    try:
        r = requests.post(
            _bot_api_url("sendDocument"),
            data=form,
            files=files,
            timeout=120,
        )
        if not r.ok:
            print(
                f"[telegram_notify] document_failed uid={telegram_user_id} "
                f"status={r.status_code} body={r.text[:300]!r}"
            )
            return False
        return True
    except requests.RequestException as e:
        print(f"[telegram_notify] document_error uid={telegram_user_id} err={e!r}")
        return False


def telegram_api_base(*, bot_token: str | None = None) -> str:
    api_base = os.getenv("TELEGRAM_BOT_API_BASE_URL", "").strip().rstrip("/")
    token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        return ""
    if api_base:
        return f"{api_base}/bot{token}"
    return f"https://api.telegram.org/bot{token}"


def send_message(
    chat_id: int | str,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: dict[str, Any] | None = None,
    disable_web_page_preview: bool = True,
    bot_token: str | None = None,
) -> bool:
    """Совместимая обёртка для миниаппа: HTML-уведомления участникам заметки."""
    if bot_token:
        api = telegram_api_base(bot_token=bot_token)
        if not api:
            return False
        payload: dict[str, Any] = {
            "chat_id": int(chat_id),
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip() or None
        try:
            r = requests.post(
                f"{api}/sendMessage",
                json=payload,
                timeout=15,
                proxies={"http": proxy, "https": proxy} if proxy else None,
            )
            return bool(r.ok)
        except Exception as e:
            print(f"[telegram_notify] send chat={chat_id} err={e!r}")
            return False
    return send_user_message(
        int(chat_id),
        text,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
        reply_markup=reply_markup,
    )


def get_user_profile_photo_bytes(
    user_id: int, *, bot_token: str | None = None
) -> Optional[bytes]:
    api = telegram_api_base(bot_token=bot_token)
    if not api:
        return None
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.post(
            f"{api}/getUserProfilePhotos",
            json={"user_id": int(user_id), "limit": 1},
            timeout=15,
            proxies=proxies,
        )
        data = r.json() if r.content else {}
        photos = ((data.get("result") or {}).get("photos") or [])
        if not photos:
            return None
        sizes = photos[0] or []
        if not sizes:
            return None
        file_id = str((sizes[-1] or {}).get("file_id") or "").strip()
        if not file_id:
            return None
        fr = requests.post(
            f"{api}/getFile",
            json={"file_id": file_id},
            timeout=15,
            proxies=proxies,
        )
        file_path = str(((fr.json() or {}).get("result") or {}).get("file_path") or "")
        if not file_path:
            return None
        if file_path.startswith("http://") or file_path.startswith("https://"):
            url = file_path
        else:
            url = f"{api.replace('/bot', '/file/bot', 1)}/{file_path}"
            if "/file/bot" not in url:
                token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
                url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        img = requests.get(url, timeout=20, proxies=proxies)
        if img.ok and img.content:
            return img.content
    except Exception as e:
        print(f"[telegram_notify] photo uid={user_id} err={e!r}")
    return None
