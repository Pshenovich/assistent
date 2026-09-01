"""Распознавание отмены текущей операции в боте."""

from __future__ import annotations

_CANCEL_PHRASES: frozenset[str] = frozenset(
    {
        "cancel",
        "/cancel",
        "стоп",
        "stop",
        "отмена",
        "отменить",
        "отмени",
        "обой",
        "хватит",
        "прервать",
        "не надо",
    }
)

_POLITE_TAIL: frozenset[str] = frozenset({"пожалуйста", "pls", "please"})

_PENDING_KIND_LABELS: dict[str, str] = {
    "await_pick_time": "выбор времени встречи",
    "await_clarify": "выбор встречи из списка",
    "await_contact_details": "добавление контакта участника",
    "missing_attendees": "уточнение участников встречи",
    "await_zoom_pick": "выбор Zoom-встречи",
    "await_zoom_api_pick": "выбор Zoom-встречи",
    "await_telemost_pick": "выбор встречи в Телемосте",
    "await_reminder_when": "уточнение времени напоминания",
}


def is_cancel_command(text: str) -> bool:
    """Короткая команда отмены шага, не «отмени встречу …»."""
    s = " ".join((text or "").strip().lower().split())
    if not s or len(s) > 24:
        return False
    if s in _CANCEL_PHRASES:
        return True
    parts = s.split()
    if len(parts) > 3:
        return False
    for p in _CANCEL_PHRASES:
        if not s.startswith(p):
            continue
        tail = s[len(p) :].strip()
        if not tail or tail in _POLITE_TAIL:
            return True
    return False


def pending_kind_label(kind: str) -> str:
    k = (kind or "").strip()
    return _PENDING_KIND_LABELS.get(k, "текущая операция")
