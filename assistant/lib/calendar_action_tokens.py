"""Короткие токены для callback «удалить встречу»."""

from __future__ import annotations

from assistant.lib import calendar_pending_store as cps


def register(
    user_id: int, event_id: str, *, calendar_id: str = "primary"
) -> str:
    return cps.put_action_token(user_id, event_id, calendar_id=calendar_id)


def consume(token: str, *, user_id: int) -> tuple[str, str] | None:
    return cps.pop_action_token(token, user_id=user_id)
