"""Поручение «человеку без аккаунта», а аккаунт у него есть.

Задачу ставят по почте (участник встречи из Google) или просто по имени. Если человек
есть в Орбите под другой почтой или по Telegram, задача висит отдельной карточкой
(«АД» рядом с настоящим Артёмом) и до него не доходит. Здесь такие задачи переводятся
на аккаунт, если это однозначно:

- почта задачи — почта входа (или запасная) аккаунта, или его почта Google;
- в контактах постановщика карточка с этой почтой и Telegram, у которого есть аккаунт;
- без почты: постановщик уже ставил задачи ровно одному аккаунту с тем же именем и фамилией.
"""

from __future__ import annotations

import re
import time
import threading
from typing import Any, Optional

_GOOGLE_LOCK = threading.Lock()
_GOOGLE_CACHE: dict[str, Any] = {"at": 0.0, "map": {}}
_GOOGLE_TTL = 120.0


def _norm_name(n: Any) -> str:
    n = re.sub(r"\s+", " ", str(n or "").strip().lower().replace("ё", "е"))
    return n if " " in n else ""


def _google_owners() -> dict[str, list[int]]:
    """Почта Google → аккаунты, где она подключена (кэш на пару минут: читаются файлы настроек)."""
    with _GOOGLE_LOCK:
        if time.time() - float(_GOOGLE_CACHE["at"]) < _GOOGLE_TTL:
            return _GOOGLE_CACHE["map"]
    out: dict[str, list[int]] = {}
    try:
        from assistant.stores import account_merge, user_prefs

        for p in account_merge._prefs_dir().glob("*.json"):  # type: ignore[attr-defined]
            if not re.fullmatch(r"-?\d+\.json", p.name):
                continue
            uid = int(p.stem)
            try:
                em = str(user_prefs.load_prefs(uid).get("google_account_email") or "").strip().lower()
            except Exception:
                continue
            if em:
                out.setdefault(em, []).append(uid)
    except Exception as e:
        print(f"[assignee_resolve] google scan err={e!r}")
    with _GOOGLE_LOCK:
        _GOOGLE_CACHE.update(at=time.time(), map=out)
    return out


def reset_cache() -> None:
    with _GOOGLE_LOCK:
        _GOOGLE_CACHE.update(at=0.0, map={})


def _live(uid: int) -> int:
    """Старый id после склейки → основной."""
    try:
        from assistant.stores import accounts

        with accounts._LOCK:  # type: ignore[attr-defined]
            r = accounts._conn().execute(  # type: ignore[attr-defined]
                "SELECT new_user_id FROM account_aliases WHERE old_user_id = ?", (str(int(uid)),)
            ).fetchone()
        return int(r[0]) if r else int(uid)
    except Exception:
        return int(uid)


def _by_email(owner: int, email: str) -> Optional[int]:
    from assistant.stores import accounts

    acc = accounts.get_by_email(email)
    if acc:
        return int(acc["user_id"])
    ids = {_live(u) for u in _google_owners().get(email, [])}
    if len(ids) == 1:
        return ids.pop()
    try:
        from assistant.stores import contacts_store

        tgs = set()
        for c in contacts_store.load_contacts(telegram_user_id=int(owner)):
            if str(c.get("email") or "").strip().lower() != email:
                continue
            raw = str(c.get("telegram_user_id") or "").strip()
            if raw.isdigit() and int(raw) > 0:
                tgs.add(accounts.account_for_tg(int(raw)))
        if len(tgs) == 1:
            return tgs.pop()
    except Exception as e:
        print(f"[assignee_resolve] contacts err={e!r}")
    return None


def resolve(owner: int | str, task: dict[str, Any], names: dict[str, set[str]]) -> Optional[int]:
    """Аккаунт настоящего исполнителя или None. names — имя → id, кому постановщик уже ставил задачи."""
    owner_i = int(owner)
    email = str(task.get("assignee_email") or "").strip().lower()
    uid: Optional[int] = None
    if email:
        uid = _by_email(owner_i, email)
    else:
        ids = names.get(_norm_name(task.get("assignee_name")), set())
        if len(ids) == 1:
            uid = int(next(iter(ids)))
    if uid is None or uid == owner_i:
        return None
    return uid


def names_of(tasks: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t in tasks:
        n = _norm_name(t.get("assignee_name"))
        a = str(t.get("assignee_user_id") or "")
        if n and a and a != str(t.get("owner_user_id") or "") and not t.get("assignee_external"):
            out.setdefault(n, set()).add(a)
    return out
