"""Состояние онбординга пользователя в user_prefs (ключ «onboarding»).

{intro_done_at, team_step_done_at, tour_done_at, used: {feat: ts}, hint_shown: {feat: n}, snoozed: {feat: ts},
 screens_seen: {screen: ts}, reset_at}
"""

from __future__ import annotations

import os
import time
from typing import Any

from assistant.onboarding import feature_ids, screen_ids
from assistant.stores import user_prefs

KEY = "onboarding"
STEP_FLAGS = ("intro_done_at", "team_step_done_at", "tour_done_at")
EVENT_KINDS = ("used", "hint_shown", "snooze", "screen_seen", "intro_done", "team_done", "tour_done")
MAPS = ("used", "hint_shown", "snoozed", "screens_seen")


def _blank() -> dict[str, Any]:
    return {k: {} for k in MAPS}


def _norm(raw: Any) -> dict[str, Any]:
    st = _blank()
    if isinstance(raw, dict):
        for k in STEP_FLAGS + ("reset_at",):
            if raw.get(k):
                st[k] = raw[k]
        for k in MAPS:
            if isinstance(raw.get(k), dict):
                st[k] = dict(raw[k])
    return st


def has_state(user_id: int) -> bool:
    return isinstance(user_prefs.load_prefs(user_id).get(KEY), dict)


def get_state(user_id: int) -> dict[str, Any]:
    """Состояние; при первом обращении — разовый расчёт по данным пользователя (backfill)."""
    prefs = user_prefs.load_prefs(user_id)
    raw = prefs.get(KEY)
    if isinstance(raw, dict):
        return _norm(raw)
    from assistant.onboarding.backfill import initial_state

    st = _norm(initial_state(user_id))
    prefs = user_prefs.load_prefs(user_id)
    if not isinstance(prefs.get(KEY), dict):
        prefs[KEY] = st
        user_prefs.save_prefs(user_id, prefs)
    return _norm(prefs[KEY])


def _save(user_id: int, st: dict[str, Any]) -> dict[str, Any]:
    prefs = user_prefs.load_prefs(user_id)
    prefs[KEY] = st
    user_prefs.save_prefs(user_id, prefs)
    return st


def apply_event(user_id: int, kind: str, feature: str = "") -> dict[str, Any]:
    kind = str(kind or "").strip()
    if kind not in EVENT_KINDS:
        raise ValueError("unknown kind")
    st = get_state(user_id)
    now = time.time()
    if kind in ("intro_done", "team_done", "tour_done"):
        flag = {"intro_done": "intro_done_at", "team_done": "team_step_done_at", "tour_done": "tour_done_at"}[kind]
        st[flag] = st.get(flag) or now
        if kind == "tour_done":
            # Тур завершает онбординг: и предыдущие шаги считаем пройденными (например, после «Пропустить»).
            st["intro_done_at"] = st.get("intro_done_at") or now
            st["team_step_done_at"] = st.get("team_step_done_at") or now
        return _save(user_id, st)
    fid = str(feature or "").strip()
    if kind == "screen_seen":
        if fid not in screen_ids():
            raise ValueError("unknown screen")
        st["screens_seen"][fid] = st["screens_seen"].get(fid) or now
        return _save(user_id, st)
    if fid not in feature_ids():
        raise ValueError("unknown feature")
    if kind == "used":
        if fid in st["used"]:
            return st
        st["used"][fid] = now
    elif kind == "hint_shown":
        st["hint_shown"][fid] = int(st["hint_shown"].get(fid) or 0) + 1
    elif kind == "snooze":
        st["snoozed"][fid] = now
    return _save(user_id, st)


def mark_used(user_id: int, feature: str) -> None:
    """Серверная отметка «воспользовался» (из эндпоинтов и инструментов); ошибки не мешают основному действию."""
    try:
        apply_event(user_id, "used", feature)
    except Exception:
        pass


def reset(user_id: int) -> dict[str, Any]:
    st = _blank()
    st["reset_at"] = time.time()
    return _save(user_id, st)


def reset_usernames() -> set[str]:
    raw = os.getenv("ONBOARDING_RESET_USERNAMES", "pshenovich")
    return {s.strip().lstrip("@").lower() for s in raw.split(",") if s.strip()}


def can_reset(username: str | None) -> bool:
    return str(username or "").strip().lstrip("@").lower() in reset_usernames()


def public_state(user_id: int, username: str | None) -> dict[str, Any]:
    st = get_state(user_id)
    st["can_reset"] = can_reset(username)
    return st
