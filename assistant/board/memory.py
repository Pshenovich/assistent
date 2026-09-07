"""Память решений: поиск прошлых совещаний по теме."""

from __future__ import annotations

import re
from typing import Any

from assistant.board import store


_STOP = {
    "и",
    "в",
    "на",
    "с",
    "по",
    "для",
    "как",
    "что",
    "это",
    "мы",
    "ли",
    "не",
    "а",
    "или",
    "the",
    "a",
    "to",
    "of",
}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-я0-9]{3,}", (text or "").lower())
    return [w for w in words if w not in _STOP]


def find_related_decisions(
    chat_id: str | int,
    question: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    tokens = _tokens(question)
    if not tokens:
        return store.list_decisions_for_chat(chat_id, limit=limit)
    seen: dict[str, dict[str, Any]] = {}
    for tok in tokens[:8]:
        for d in store.search_decisions(chat_id, tok, limit=limit):
            did = str(d.get("id") or "")
            if did and did not in seen:
                seen[did] = d
    ranked = list(seen.values())

    def score(d: dict[str, Any]) -> int:
        blob = f"{d.get('problem','')} {d.get('decision','')}".lower()
        return sum(1 for t in tokens if t in blob)

    ranked.sort(key=score, reverse=True)
    return ranked[:limit]


def history_mentions_past(question: str) -> bool:
    q = (question or "").lower()
    keys = (
        "уже обсуждали",
        "мы уже решали",
        "прошлое решение",
        "раньше решали",
        "already discussed",
        "last time",
    )
    return any(k in q for k in keys)
