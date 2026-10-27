"""Транскрипт из сегментов: реплики по спикерам, абзацы по паузам, таймкоды.

Без LLM: смена спикера — новая реплика; пауза дольше PAUSE_SEC или абзац
длиннее MAX_PARA_SEC — новый абзац внутри реплики.
"""

from __future__ import annotations

import html
from typing import Any, Optional

PAUSE_SEC = 2.0
MAX_PARA_SEC = 60.0


def fmt_time(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    total = max(0, int(sec))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def speaker_names(speaker_map: Optional[dict[str, Any]]) -> dict[str, str]:
    """Подтверждённые имена: «Спикер 2» → «Дима». Догадки LLM в текст не попадают."""
    out: dict[str, str] = {}
    for label, v in (speaker_map or {}).items():
        if isinstance(v, dict) and v.get("confirmed") and str(v.get("name") or "").strip():
            out[str(label)] = str(v["name"]).strip()
    return out


def turns(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{speaker, start, paras: [текст, …]}, …]."""
    out: list[dict[str, Any]] = []
    para_start: Optional[float] = None
    prev_end: Optional[float] = None
    for seg in segments or []:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        sp = str(seg.get("speaker") or "")
        start = seg.get("start")
        end = seg.get("end")
        if not out or out[-1]["speaker"] != sp:
            out.append({"speaker": sp, "start": start, "paras": [text]})
            para_start = start
        else:
            gap = (start - prev_end) if start is not None and prev_end is not None else 0.0
            span = (start - para_start) if start is not None and para_start is not None else 0.0
            if gap > PAUSE_SEC or span > MAX_PARA_SEC:
                out[-1]["paras"].append(text)
                para_start = start
            else:
                out[-1]["paras"][-1] += " " + text
        if end is not None:
            prev_end = end if prev_end is None else max(prev_end, end)
    return out


def render_html(segments: list[dict[str, Any]], speaker_map: Optional[dict[str, Any]] = None) -> str:
    names = speaker_names(speaker_map)
    parts: list[str] = []
    for t in turns(segments):
        paras = [html.escape(p) for p in t["paras"]]
        label = names.get(t["speaker"], t["speaker"])
        if label:
            ts = fmt_time(t["start"])
            head = f"<strong>{html.escape(label)}</strong>" + (f" · {ts}" if ts else "")
            paras[0] = f"{head}<br>\n{paras[0]}"
        parts.extend(f"<p>{p}</p>" for p in paras)
    return "\n".join(parts)


def samples(segments: list[dict[str, Any]], *, per_speaker: int = 3, max_chars: int = 220) -> dict[str, list[dict[str, Any]]]:
    """Характерные реплики каждого спикера — для «Кто есть кто» и угадывания имён.

    Берём самые длинные (в коротких «да», «угу» узнать человека нельзя), в порядке времени."""
    by: dict[str, list[dict[str, Any]]] = {}
    for seg in segments or []:
        sp = str(seg.get("speaker") or "")
        text = str(seg.get("text") or "").strip()
        if sp and text:
            by.setdefault(sp, []).append({"start": seg.get("start"), "text": text[:max_chars]})
    out: dict[str, list[dict[str, Any]]] = {}
    for sp, items in by.items():
        top = sorted(items, key=lambda x: len(x["text"]), reverse=True)[:per_speaker]
        out[sp] = sorted(top, key=lambda x: x["start"] if x["start"] is not None else 0)
    return out
