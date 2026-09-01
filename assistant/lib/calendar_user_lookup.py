"""Сопоставление email Google Calendar ↔ telegram_user_id бота."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from assistant.integrations import google_calendar_oauth

_lock = threading.RLock()
_mem_index: dict[str, int] | None = None


def _index_path() -> Path:
    return google_calendar_oauth._tokens_dir() / "_calendar_email_index.json"


def invalidate_email_index() -> None:
    global _mem_index
    with _lock:
        _mem_index = None
    path = _index_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _load_index_file() -> dict[str, int]:
    path = _index_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        out: dict[str, int] = {}
        for k, v in data.items():
            em = str(k or "").strip().lower()
            try:
                uid = int(v)
            except (TypeError, ValueError):
                continue
            if em and "@" in em and uid > 0:
                out[em] = uid
        return out
    except Exception:
        return {}


def _save_index_file(idx: dict[str, int]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({k: v for k, v in sorted(idx.items())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _email_index() -> dict[str, int]:
    global _mem_index
    with _lock:
        if _mem_index is not None:
            return dict(_mem_index)
        idx = _load_index_file()
        for path in google_calendar_oauth._tokens_dir().glob("*.json"):
            stem = path.stem
            if not stem.isdigit():
                continue
            uid = int(stem)
            em = account_email_for_user(uid)
            if em:
                idx[em.lower()] = uid
        _mem_index = idx
        _save_index_file(idx)
        return dict(idx)


def account_email_for_user(user_id: int) -> str | None:
    """Email аккаунта Google (id primary-календаря или из prefs)."""
    from assistant.stores import user_prefs

    uid = int(user_id)
    prefs = user_prefs.load_prefs(uid)
    cached = str(prefs.get("google_account_email") or "").strip().lower()
    if cached and "@" in cached:
        return cached
    path = google_calendar_oauth.user_token_path(uid)
    if not path.is_file():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(path))
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        res = svc.calendarList().list().execute()
        for item in res.get("items") or []:
            if not isinstance(item, dict) or not item.get("primary"):
                continue
            cid = str(item.get("id") or "").strip().lower()
            if "@" in cid:
                prefs = user_prefs.load_prefs(uid)
                prefs["google_account_email"] = cid
                user_prefs.save_prefs(uid, prefs)
                return cid
    except Exception as e:
        print(f"[calendar_user_lookup] account_email uid={uid} err={e!r}")
    return None


def register_user_calendar_email(user_id: int) -> str | None:
    """После OAuth: закешировать email и обновить индекс."""
    em = account_email_for_user(int(user_id))
    if not em:
        return None
    global _mem_index
    with _lock:
        idx = _email_index()
        idx[em.lower()] = int(user_id)
        _mem_index = idx
        _save_index_file(idx)
    return em


def lookup_user_id_by_calendar_email(email: str) -> int | None:
    em = (email or "").strip().lower()
    if not em or "@" not in em:
        return None
    return _email_index().get(em)


def index_calendar_users() -> dict[str, int]:
    return _email_index()
