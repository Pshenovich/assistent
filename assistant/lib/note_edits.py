"""Правки ИИ в открытой заметке.

Клиент присылает «вид» открытого листа (текст с пронумерованными таблицами),
модель отвечает блоком ```leo-note {"edits":[…]}```. Правки применяет клиент
в редакторе: подсвечивает изменённое и просит подтвердить («Ок» / «Отменить»).
Сервер только готовит промпт и разбирает/проверяет ответ.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

MAX_VIEW_CHARS = 60000
MAX_EDITS = 300
MAX_TEXT = 4000
_FNS = ("sum", "avg", "min", "max", "diff", "product")

_BLOCK_RE = re.compile(r"```leo-note\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def system_addon() -> str:
    return """
## Editing the open note (Leo)
The user has a note sheet open; its content is given between <open-note> tags (tables are numbered,
rows and columns are 1-based, row 1 is the header row). When the user asks to CHANGE the note itself
(rename, replace, fill in, fix, add, calculate into the table), do it by emitting ONE fenced block:
```leo-note
{"edits":[…]}
```
and AFTER the fence one short Russian sentence about what you changed. Leo highlights your changes
and asks the user to confirm them. If the user only asks a question, answer normally without leo-note.

Operations:
- {"op":"replace","find":"exact text","replace":"new text","all":true}
  Copy "find" exactly from the note, within one line or one cell. "all":true replaces every occurrence
  (e.g. rename a speaker everywhere); without it only the first one. "replace":"" deletes the text.
- {"op":"set_cell","table":1,"row":2,"col":4,"value":"text"} — write one table cell.
- {"op":"compute","table":1,"fn":"sum","target":{"col":4},"cols":[2,3],"rows":"all"}
  Leo calculates exactly: for every row in "rows", cell(row, target.col) = fn(cells in "cols").
  {"op":"compute","table":1,"fn":"sum","target":{"row":6},"rows":[2,3,4,5],"cols":[2,3,4]}
  — for every column in "cols", cell(target.row, col) = fn(cells in "rows").
  fn: sum | avg | min | max | diff (first minus the rest) | product. "rows":"all" = every row except
  row 1 and the target row. ALWAYS use compute for arithmetic over table cells — never calculate yourself.
- {"op":"insert_after","anchor":"exact text of a paragraph","text":"new paragraph"}
  Inserts after the paragraph that contains anchor; "anchor":"" appends to the end. Use \\n for several paragraphs.

Rules: change only what the user asked; never re-type the whole note; at most 300 edits;
keep the language and formatting of the note.
""".strip()


def prompt_block(view: str) -> str:
    view = str(view or "").strip()
    if not view:
        return ""
    if len(view) > MAX_VIEW_CHARS:
        view = view[:MAX_VIEW_CHARS] + "\n… (дальше заметка обрезана; replace с all:true всё равно применится ко всей заметке)"
    return f"<open-note>\n{view}\n</open-note>"


def _int(v: Any) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _int_list(v: Any) -> Any:
    if isinstance(v, str) and v.strip().lower() == "all":
        return "all"
    if isinstance(v, list):
        out = [n for n in (_int(x) for x in v[:1000]) if n]
        return out or None
    return None


def _text(v: Any) -> str:
    return str(v if v is not None else "")[:MAX_TEXT]


def clean_edit(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    op = str(raw.get("op") or "").strip().lower()
    if op in ("replace", "delete"):
        find = _text(raw.get("find"))
        if not find.strip():
            return None
        return {"op": "replace", "find": find, "replace": "" if op == "delete" else _text(raw.get("replace")), "all": bool(raw.get("all"))}
    if op == "set_cell":
        t, r, c = _int(raw.get("table")), _int(raw.get("row")), _int(raw.get("col"))
        if not (t and r and c):
            return None
        return {"op": "set_cell", "table": t, "row": r, "col": c, "value": _text(raw.get("value"))}
    if op == "compute":
        t = _int(raw.get("table"))
        fn = str(raw.get("fn") or "sum").strip().lower()
        target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
        tcol, trow = _int(target.get("col")), _int(target.get("row"))
        rows, cols = _int_list(raw.get("rows")), _int_list(raw.get("cols"))
        if not t or fn not in _FNS or bool(tcol) == bool(trow) or rows is None or cols is None:
            return None
        return {"op": "compute", "table": t, "fn": fn, "target": {"col": tcol} if tcol else {"row": trow}, "rows": rows, "cols": cols}
    if op in ("insert_after", "append"):
        text = _text(raw.get("text"))
        if not text.strip():
            return None
        return {"op": "insert_after", "anchor": "" if op == "append" else _text(raw.get("anchor")), "text": text}
    return None


def parse_blocks(answer: str) -> tuple[str, list[dict[str, Any]]]:
    """(ответ без блоков leo-note, проверенные правки)."""
    edits: list[dict[str, Any]] = []
    for m in _BLOCK_RE.finditer(answer or ""):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        items = data.get("edits") if isinstance(data, dict) else data if isinstance(data, list) else []
        for raw in items or []:
            e = clean_edit(raw)
            if e:
                edits.append(e)
    clean = _BLOCK_RE.sub("", answer or "").strip()
    return clean, edits[:MAX_EDITS]
