"""Описание задачи: наш markdown-lite (как пишет WWA) → BBCode Битрикс24."""

from __future__ import annotations

import re

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
# Заголовки разделов WWA пишутся простой строкой — в трекере они жирные.
_SECTION = re.compile(
    r"^\s*(зачем|почему|что сделать|что нужно сделать|критерии приёмки|критерии приемки|why|what|acceptance(?: criteria)?)\s*:?\s*$",
    re.IGNORECASE,
)


def _inline(text: str) -> str:
    return _BOLD.sub(r"[B]\1[/B]", text)


def md_to_bbcode(text: str) -> str:
    out: list[str] = []
    in_list = False
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        m = _BULLET.match(raw)
        if m:
            if not in_list:
                out.append("[LIST]")
                in_list = True
            out.append("[*]" + _inline(m.group(1).strip()))
            continue
        if in_list:
            out.append("[/LIST]")
            in_list = False
        if _SECTION.match(raw):
            out.append("[B]" + raw.strip().rstrip(":") + "[/B]")
            continue
        h = _HEADING.match(raw)
        if h:
            out.append("[B]" + h.group(1).replace("**", "") + "[/B]")
            continue
        out.append(_inline(raw.rstrip()))
    if in_list:
        out.append("[/LIST]")
    return "\n".join(out).strip()
