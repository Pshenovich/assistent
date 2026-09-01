"""Ingest Yandex Mail messages with Telemost summaries/transcripts."""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import Any

from assistant.integrations import telemost_oauth
from assistant.services import meeting_record_pipeline as mrp
from assistant.stores import user_prefs

HERE = Path(__file__).resolve().parent
_IMAP_HOST = "imap.yandex.ru"
_IMAP_PORT = 993
_TELEMOST_FROM_MARKERS = ("telemost", "yandex-team", "calendar.yandex")
_TELEMOST_SUBJECT_MARKERS = (
    "телемост",
    "telemost",
    "конспект",
    "транскрипт",
    "transcript",
    "summary",
    "саммари",
)
_CONF_RE = re.compile(r"telemost\.yandex\.(?:ru|com)/j/([A-Za-z0-9_-]+)", re.I)


def enabled() -> bool:
    raw = os.getenv("TELEMOST_MAIL_INGEST_ENABLED", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def poll_interval_sec() -> float:
    try:
        return max(60.0, float(os.getenv("TELEMOST_MAIL_POLL_SEC", "300") or "300"))
    except ValueError:
        return 300.0


def _state_dir() -> Path:
    raw = os.getenv("TELEMOST_MAIL_STATE_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "telemost_mail_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path(telegram_user_id: int) -> Path:
    return _state_dir() / f"{int(telegram_user_id)}.json"


def _read_state(telegram_user_id: int) -> dict[str, Any]:
    path = _state_path(telegram_user_id)
    if not path.is_file():
        return {"processed_uids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"processed_uids": []}
    return data if isinstance(data, dict) else {"processed_uids": []}


def _write_state(telegram_user_id: int, state: dict[str, Any]) -> None:
    uids = state.get("processed_uids") or []
    if isinstance(uids, list) and len(uids) > 500:
        state["processed_uids"] = uids[-500:]
    _state_path(telegram_user_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _has_mail_scope(telegram_user_id: int) -> bool:
    store = telemost_oauth.read_token_store(telegram_user_id) or {}
    scope = str(store.get("scope") or "")
    return "mail:imap_ro" in scope.split()


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts: list[str] = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def _message_text(msg: Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks).strip()


def _looks_like_telemost_mail(from_hdr: str, subject: str) -> bool:
    f = (from_hdr or "").lower()
    s = (subject or "").lower()
    if not any(m in f for m in _TELEMOST_FROM_MARKERS):
        if not any(m in s for m in _TELEMOST_SUBJECT_MARKERS):
            return False
    return any(m in s for m in _TELEMOST_SUBJECT_MARKERS) or "telemost" in f


def _extract_topic(subject: str, body: str) -> str:
    subj = re.sub(r"^\s*(re|fwd):\s*", "", subject or "", flags=re.I).strip()
    if subj:
        return subj[:120]
    first = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    return (first or "Встреча Телемост")[:120]


def _split_summary_transcript(body: str) -> tuple[str, str]:
    text = (body or "").strip()
    if not text:
        return "", ""
    low = text.lower()
    for marker in ("транскрипт", "transcript", "расшифровка"):
        idx = low.find(marker)
        if idx > 0:
            return text[:idx].strip(), text[idx:].strip()
    if len(text) > 4000:
        return text[:2000].strip(), text[2000:].strip()
    return text, ""


def _build_xoauth2(email_addr: str, access_token: str) -> str:
    auth_str = f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01"
    import base64

    return base64.b64encode(auth_str.encode()).decode()


def _connect_imap(email_addr: str, access_token: str) -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
    mail.authenticate("XOAUTH2", lambda _: _build_xoauth2(email_addr, access_token))
    return mail


def _process_message(telegram_user_id: int, msg: Message, *, subject: str) -> bool:
    body = _message_text(msg)
    if not body:
        return False
    topic = _extract_topic(subject, body)
    summary_part, transcript_part = _split_summary_transcript(body)
    conf_match = _CONF_RE.search(body)
    if conf_match:
        topic = topic or f"Телемост {conf_match.group(1)}"
    if not summary_part and transcript_part:
        from assistant.nlu import llm as llm_mod

        summary_result = llm_mod.summarize_recording(transcript_part, speakers_detected=False)
        if summary_result:
            from assistant.lib.telegram_markdown import prepare_summary_markdown

            summary_part = prepare_summary_markdown(
                str(summary_result.get("summary") or ""),
                tasks=summary_result.get("tasks"),
            )
    if not summary_part:
        return False
    processed = {
        "summary_text": summary_part,
        "transcript": transcript_part or summary_part,
        "participant_names": [],
        "meta": {"content_type": "meeting", "source": "telemost_mail"},
        "result": None,
    }
    tr_id, sum_id = mrp.persist_meeting_artifacts_for_user(
        user_id=int(telegram_user_id),
        username=None,
        processed=processed,
        topic=topic,
        source_url=None,
        filename=None,
    )
    mrp.notify_summary_ready(
        user_id=int(telegram_user_id),
        topic=topic,
        summary_text=summary_part,
        summary_event_id=sum_id,
        transcript_event_id=tr_id if transcript_part else None,
    )
    return True


def poll_user_mail(telegram_user_id: int) -> int:
    """Проверить почту пользователя. Возвращает число обработанных писем."""
    uid = int(telegram_user_id)
    if not telemost_oauth.has_connection(uid):
        return 0
    if not _has_mail_scope(uid):
        return 0
    if not user_prefs.telemost_auto_record_enabled(uid):
        return 0
    store = telemost_oauth.read_token_store(uid) or {}
    email_addr = str(store.get("yandex_email") or store.get("yandex_login") or "").strip()
    if not email_addr:
        return 0
    token = telemost_oauth.get_valid_access_token(uid)
    state = _read_state(uid)
    processed = set(str(x) for x in (state.get("processed_uids") or []))
    handled = 0
    mail: imaplib.IMAP4_SSL | None = None
    try:
        mail = _connect_imap(email_addr, token)
        mail.select("INBOX")
        typ, data = mail.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return 0
        for num in data[0].split():
            num_s = num.decode() if isinstance(num, bytes) else str(num)
            key = f"uid:{num_s}"
            if key in processed:
                continue
            typ2, fetched = mail.fetch(num, "(RFC822)")
            if typ2 != "OK" or not fetched or not fetched[0]:
                continue
            raw = fetched[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw)
            from_hdr = _decode_header_value(msg.get("From"))
            subj = _decode_header_value(msg.get("Subject"))
            if not _looks_like_telemost_mail(from_hdr, subj):
                processed.add(key)
                continue
            if _process_message(uid, msg, subject=subj):
                handled += 1
            processed.add(key)
    except Exception as e:
        print(f"[telemost_mail] poll user={uid} err={e!r}")
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass
    state["processed_uids"] = sorted(processed)
    _write_state(uid, state)
    return handled


def poll_all_users() -> int:
    if not enabled():
        return 0
    total = 0
    from assistant.integrations.telemost_oauth import user_token_path

    tokens_dir = user_token_path(0).parent
    for path in tokens_dir.glob("*.json"):
        try:
            uid = int(path.stem)
        except ValueError:
            continue
        total += poll_user_mail(uid)
    return total
