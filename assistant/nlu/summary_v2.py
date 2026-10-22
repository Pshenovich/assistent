"""Саммари v2, A2: окна → кандидаты задач → сведение; факты длинной встречи синтезом по окнам.

Короткая встреча (одно окно): основной вызов SUMMARY_EXTRACT_SYSTEM (факты + его задачи как
кандидаты) и вызов кандидатов параллельно, затем сведение.
Длинная: по каждому окну — кандидаты и факты, затем синтез фактов и сведение задач параллельно.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from assistant.lib import summary_normalize as sn
from assistant.nlu.prompts import (
    SUMMARY_EXTRACT_SYSTEM,
    SUMMARY_SYNTH_SYSTEM,
    TASK_CANDIDATES_SYSTEM,
    TASKS_CONSOLIDATE_SYSTEM,
    WINDOW_FACTS_SYSTEM,
)

WINDOW_CHARS = 14000
WINDOW_OVERLAP = 1500
SINGLE_PASS_MAX = 40000  # до этой длины факты берём одним вызовом по всему транскрипту
TRANSCRIPT_MAX = 200000  # ~3 часа речи
MAX_WORKERS = 4

Chat = Callable[..., str]


def split_windows(text: str, size: int = WINDOW_CHARS, overlap: int = WINDOW_OVERLAP) -> list[str]:
    """Режем по репликам (\\n\\n); соседние окна перекрываются хвостом предыдущего."""
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    blocks: list[str] = []
    for b in text.split("\n\n"):
        while len(b) > size:  # одна гигантская реплика — режем по предложению
            cut = b.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            blocks.append(b[:cut].strip())
            b = b[cut:].strip()
        if b.strip():
            blocks.append(b.strip())
    windows: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for b in blocks:
        if cur and cur_len + len(b) + 2 > size:
            windows.append("\n\n".join(cur))
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(cur):
                if tail_len + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev) + 2
            cur, cur_len = tail, tail_len
        cur.append(b)
        cur_len += len(b) + 2
    if cur:
        windows.append("\n\n".join(cur))
    return windows


def _call_json(chat: Chat, system: str, payload: dict[str, Any], *, operation: str, model: str,
               max_tokens: int, temperature: float = 0.0) -> Optional[dict[str, Any]]:
    from assistant.nlu.llm import _json_obj

    try:
        raw = chat(system, json.dumps(payload, ensure_ascii=False), operation=operation, model=model,
                   timeout=300, temperature=temperature, max_tokens=max_tokens)
    except Exception as e:
        print(f"[summary_v2] {operation} failed: {e!r}")
        return None
    return _json_obj(raw)


def merge_speaker_maps(maps: list[Any], transcript: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for m in maps:
        for label, info in sn.clean_speaker_map(m, transcript).items():
            if label not in out or info["confidence"] > out[label]["confidence"]:
                out[label] = info
    return out


def consolidate_tasks(
    chat: Chat, candidates: list[dict[str, Any]], *, ctx_payload: dict[str, Any], model: str,
    transcript_norm: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Шаг 2: модель сводит кандидатов; код подтягивает цитаты и сроки из источников."""
    stats: dict[str, Any] = {"candidates": len(candidates)}
    if not candidates:
        return [], stats
    numbered = [{"id": i + 1, **{k: c.get(k) for k in ("task", "assignee", "speaker", "deadline", "due_iso", "completed", "quote")}}
                for i, c in enumerate(candidates)]
    obj = _call_json(chat, TASKS_CONSOLIDATE_SYSTEM, {"context": ctx_payload, "candidates": numbered},
                     operation="summarize_tasks_consolidate", model=model, max_tokens=6000)
    items = (obj or {}).get("tasks")
    if not isinstance(items, list):
        stats["consolidate_failed"] = True
        return sn.merge_raw_tasks(candidates, []), stats
    out: list[dict[str, Any]] = []
    for item in items:
        t = sn.raw_task(item)
        if not t:
            continue
        src_ids = [int(x) for x in (item.get("sources") or []) if str(x).isdigit() and 1 <= int(x) <= len(candidates)]
        srcs = [candidates[i - 1] for i in src_ids]
        # цитата — лучшая подтверждённая из источников
        best_q, best_s = "", 0.0
        for c in srcs:
            q = c.get("quote") or ""
            s = sn.locate_quote(q, transcript_norm) if q else 0.0
            if s > best_s:
                best_q, best_s = q, s
        t["quote"] = best_q or next((c.get("quote") for c in srcs if c.get("quote")), "")
        for k in ("speaker", "deadline", "due_iso"):
            if not t.get(k):
                t[k] = next((c.get(k) for c in srcs if c.get(k)), "")
        if not t.get("assignee"):
            named = next((c.get("assignee") for c in srcs if c.get("assignee") and c.get("assignee_basis") == "named"), "")
            if named:
                t["assignee"], t["assignee_basis"] = named, "named"
        if not t.get("assignee") and t.get("speaker"):
            t["assignee_basis"] = "self" if any(c.get("assignee_basis") == "self" for c in srcs) else t["assignee_basis"]
        t["sources"] = len(srcs)
        out.append(t)
    stats["tasks"] = len(out)
    stats["dropped"] = len([x for x in (obj.get("dropped") or []) if str(x).isdigit()])
    return out, stats


def extract(
    transcript: str, *, speakers_detected: bool, ctx_payload: dict[str, Any], model: str, chat: Chat,
) -> Optional[dict[str, Any]]:
    """Возвращает сырые факты: obj (как SUMMARY_EXTRACT), tasks (сведённые), speaker_map, stats."""
    text = transcript[:TRANSCRIPT_MAX]
    tn = sn._norm(text)
    windows = split_windows(text)
    total = len(windows)
    single = len(text) <= SINGLE_PASS_MAX

    def candidates_for(i: int, w: str) -> list[dict[str, Any]]:
        obj = _call_json(chat, TASK_CANDIDATES_SYSTEM,
                         {"context": ctx_payload, "part": i + 1, "total": total, "transcript": w},
                         operation="summarize_task_candidates", model=model, max_tokens=4096)
        return [t for t in (sn.raw_task(x) for x in (obj or {}).get("tasks") or []) if t]

    def facts_for(i: int, w: str) -> dict[str, Any]:
        return _call_json(chat, WINDOW_FACTS_SYSTEM, {"part": i + 1, "total": total, "transcript": w},
                          operation="summarize_window_facts", model=model, max_tokens=4096) or {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        cand_f = [pool.submit(candidates_for, i, w) for i, w in enumerate(windows)]
        if single:
            main_f = pool.submit(_call_json, chat, SUMMARY_EXTRACT_SYSTEM,
                                 {"context": ctx_payload, "speakers_detected": bool(speakers_detected), "transcript": text},
                                 operation="summarize_extract_llm", model=model, max_tokens=8192, temperature=0.1)
            facts_f = []
        else:
            facts_f = [pool.submit(facts_for, i, w) for i, w in enumerate(windows)]
        candidates: list[dict[str, Any]] = []
        for f in cand_f:
            candidates.extend(f.result())

        if single:
            obj = main_f.result()
            if not obj:
                return None
            candidates.extend(t for t in (sn.raw_task(x) for x in obj.get("tasks") or []) if t)
            speaker_map = sn.clean_speaker_map(obj.get("speaker_map"), text)
            tasks, stats = consolidate_tasks(chat, candidates, ctx_payload=ctx_payload, model=model, transcript_norm=tn)
        else:
            facts = [f.result() for f in facts_f]
            speaker_map = merge_speaker_maps([fa.get("speaker_map") for fa in facts], text)
            fragments = [{k: fa.get(k) for k in ("points", "decisions", "open_questions", "risks", "next_steps", "participants")}
                         for fa in facts]
            synth_f = pool.submit(_call_json, chat, SUMMARY_SYNTH_SYSTEM, {"context": ctx_payload, "fragments": fragments},
                                  operation="summarize_synth", model=model, max_tokens=8192, temperature=0.1)
            tasks, stats = consolidate_tasks(chat, candidates, ctx_payload=ctx_payload, model=model, transcript_norm=tn)
            obj = synth_f.result()
            if not obj:
                return None
    stats["windows"] = total
    stats["truncated"] = len(transcript) > TRANSCRIPT_MAX
    return {"obj": obj, "tasks": tasks, "speaker_map": speaker_map, "stats": stats, "text": text}
