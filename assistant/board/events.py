"""In-process события Executive Board."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

Listener = Callable[[str, dict[str, Any]], None]

MEETING_CREATED = "MEETING_CREATED"
AGENT_RESPONSE_CREATED = "AGENT_RESPONSE_CREATED"
AGENT_RESPONSE_PUBLISHED = "AGENT_RESPONSE_PUBLISHED"
ROUND_COMPLETED = "ROUND_COMPLETED"
CHALLENGE_STARTED = "CHALLENGE_STARTED"
MEETING_READY_FOR_SYNTHESIS = "MEETING_READY_FOR_SYNTHESIS"
DECISION_CREATED = "DECISION_CREATED"
FOLLOWUP_DUE = "FOLLOWUP_DUE"
MEETING_STOPPED = "MEETING_STOPPED"
USER_INPUT_RECEIVED = "USER_INPUT_RECEIVED"

_listeners: dict[str, list[Listener]] = defaultdict(list)


def on(event: str, fn: Listener) -> None:
    _listeners[event].append(fn)


def emit(event: str, **payload: Any) -> None:
    mid = payload.get("meeting_id") or payload.get("decision_id") or ""
    print(f"[board.event] {event} {mid}")
    for fn in list(_listeners.get(event, [])) + list(_listeners.get("*", [])):
        try:
            fn(event, payload)
        except Exception as e:
            print(f"[board.event] listener_err event={event} err={e!r}")
