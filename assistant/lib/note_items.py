"""ID строк заметки: data-leo-item на строке (p / li / заголовок / ячейка).

Задача саммари и пункт Фокуса, вытащенный Лео из заметки, получают ID своей строки.
По нему пункт находит строку, как бы ни правили её текст и название задачи: чип задачи
встаёт в эту строку, повторный разбор заметки узнаёт тот же пункт. Редактор хранит атрибут
при правке строки и не копирует его на новую строку при Enter.
"""

from __future__ import annotations

import html as _h
import re
import secrets
from html.parser import HTMLParser
from typing import Any, Optional

ITEM_ATTR = "data-leo-item"
ITEM_ID_RE = re.compile(r"^i[0-9a-f]{10}$")
BLOCKS = ("li", "p", "h1", "h2", "h3", "h4", "h5", "h6", "td")
HTML_BODY_RE = re.compile(r"\s*<(p|h[1-6]|ul|ol|div|table|blockquote|pre)\b", re.I)


def new_item_id() -> str:
    return "i" + secrets.token_hex(5)


def is_html_body(body: str) -> bool:
    return bool(HTML_BODY_RE.match(str(body or "")))


class _Lines(HTMLParser):
    """Строки документа: текст, место для значка в конце и элемент, на котором живёт ID.

    У пункта списка текст лежит в <p> внутри <li>; ID ставим на <li> — это и есть строка."""

    def __init__(self, src: str) -> None:
        super().__init__(convert_charrefs=True)
        self.src = src
        self._line_starts = [0]
        for m in re.finditer("\n", src):
            self._line_starts.append(m.end())
        self.stack: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self._skip = 0  # внутри значка Фокуса: его текст — не текст строки

    def _off(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "span" and (self._skip or "note-focus-mark" in str(dict(attrs).get("class") or "")):
            self._skip += 1
            return
        if tag in ("ul", "ol") and self.stack and self.stack[-1]["tag"] == "li" and self.stack[-1]["at"] is None:
            self.stack[-1]["at"] = self._off()
        if tag not in BLOCKS:
            return
        start = self._off()
        raw = self.get_starttag_text() or ""
        entry = {
            "tag": tag,
            "text": "",
            "at": None,
            "start": start,
            "open_end": start + len(raw),
            "self_closing": raw.rstrip().endswith("/>"),
            "item": dict(attrs).get(ITEM_ATTR) or "",
            "owner": None,
            "has_child_block": False,
        }
        parent = self.stack[-1] if self.stack else None
        if tag == "p" and parent and parent["tag"] == "li" and not parent["has_child_block"] and not parent["text"].strip():
            entry["owner"] = parent
        if parent:
            parent["has_child_block"] = True
        self.stack.append(entry)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._skip:
            self._skip -= 1
            return
        if tag not in BLOCKS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                b = self.stack.pop(i)
                if b["at"] is None:
                    b["at"] = self._off()
                self.blocks.append(b)
                return

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self.stack and self.stack[-1]["at"] is None:
            self.stack[-1]["text"] += data


def lines(html_text: str) -> list[dict[str, Any]]:
    """Непустые строки в порядке документа: text, at (конец текста), item (ID строки), owner."""
    src = str(html_text or "")
    p = _Lines(src)
    try:
        p.feed(src)
        p.close()
    except Exception:
        return []
    out = []
    for b in sorted(p.blocks, key=lambda x: x["start"]):
        if not b["text"].strip():
            continue
        owner = b["owner"] or b
        out.append({**b, "text": " ".join(b["text"].split()), "item": owner["item"] or b["item"], "owner": owner})
    return out


def numbered_text(html_text: str, *, limit: int = 400) -> tuple[str, list[dict[str, Any]]]:
    """Текст для Лео: «[3] строка» — по номеру он указывает, из какой строки пункт."""
    rows = lines(html_text)[:limit]
    return "\n".join(f"[{i + 1}] {r['text']}" for i, r in enumerate(rows)), rows


def stamp(html_text: str, targets: list[tuple[dict[str, Any], str]]) -> tuple[str, list[str]]:
    """Поставить ID строкам. targets — (строка из lines(), желаемый ID).

    Если у строки ID уже есть, остаётся он (его и возвращаем)."""
    src = str(html_text or "")
    result: list[str] = []
    edits: dict[int, str] = {}
    for line, want in targets:
        owner = line["owner"]
        if owner["item"]:
            result.append(owner["item"])
            continue
        pos = owner["open_end"] - (2 if owner["self_closing"] else 1)
        if pos in edits:
            result.append(edits[pos])
            continue
        edits[pos] = want
        result.append(want)
    for pos in sorted(edits, reverse=True):
        src = src[:pos] + f' {ITEM_ATTR}="{edits[pos]}"' + src[pos:]
    return src, result


def find_line(html_text: str, item_id: str) -> Optional[dict[str, Any]]:
    if not item_id:
        return None
    for line in lines(html_text):
        if line["item"] == item_id:
            return line
    return None


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", _h.unescape(str(s or "")).lower().replace("ё", "е")))


def stamp_summary_tasks(html_text: str, tasks: list[dict[str, Any]]) -> str:
    """Каждой задаче саммари — ID (в meta.tasks и на её строке чеклиста)."""
    from assistant.lib.telegram_rich import _task_label

    rows = [r for r in lines(html_text) if r["owner"]["tag"] == "li"]
    used: set[int] = set()
    targets = []
    owners: list[dict[str, Any]] = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        if not ITEM_ID_RE.match(str(t.get("id") or "")):
            t["id"] = new_item_id()
        want = _norm(_task_label(t))
        if not want:
            continue
        for i, r in enumerate(rows):
            if i not in used and _norm(r["text"]) == want:
                used.add(i)
                targets.append((r, t["id"]))
                owners.append(t)
                break
    if not targets:
        return html_text
    out, ids = stamp(html_text, targets)
    for t, got in zip(owners, ids):
        t["id"] = got  # у строки уже был ID — задача берёт его
    return out
