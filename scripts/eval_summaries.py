#!/usr/bin/env python3
"""Eval саммари встреч: v1 против v2 на одном наборе транскриптов.

Набор — JSON-файлы в каталоге (по умолчанию data/eval/summaries, data/ в .gitignore —
реальные транскрипты в git не попадают):

    {"transcript": "...",
     "context": {"meeting_at": "2026-10-21T11:00:00+03:00", "title": "...", "attendees": [...],
                 "owner": "...", "people": [{"name": "...", "aliases": [...]}]},   # необязательно
     "expected": {"tasks": [{"task": "...", "assignee": "...", "due_iso": "..."}],   # необязательно
                  "decisions": ["..."]}}

Без expected считаются только «самодостаточные» метрики (доля задач с цитатой из транскрипта,
с исполнителем, с датой). С expected — ещё recall/precision задач и точность исполнителя/срока.

Набор можно собрать из локальной базы заметок (эталон заполнить руками):

    .venv/bin/python scripts/eval_summaries.py --export-from-notes USER_ID --limit 15

Эталон от независимой сильной модели (не участвует в v1/v2) — вместо черновика из текущих саммари:

    .venv/bin/python scripts/eval_summaries.py --annotate openai/gpt-5.5

Эталон из ручной разметки (data/eval/drafts/note_N.md: «- [x] задача | исполнитель | срок»):

    .venv/bin/python scripts/eval_summaries.py --gold-md data/eval/drafts --pipelines v1,v2

Прогон (ходит в OpenRouter, нужен .env):

    .venv/bin/python scripts/eval_summaries.py --pipelines v1,v2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DIR = ROOT / "data" / "eval" / "summaries"
TASK_MATCH_MIN = 0.55
SEMANTIC_MATCH = True  # --string-match выключает


def _norm(s: str) -> str:
    from assistant.lib.summary_normalize import _norm as n

    return n(s)


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _context(raw: dict[str, Any] | None):
    from assistant.lib.meeting_context import MeetingContext

    if not raw:
        return None
    people = list(raw.get("people") or [])
    if raw.get("owner") and not any(p.get("owner") for p in people):
        people.append({"name": raw["owner"], "aliases": [], "key": "", "owner": True})
    return MeetingContext(
        meeting_at=str(raw.get("meeting_at") or ""),
        timezone=str(raw.get("timezone") or ""),
        title=str(raw.get("title") or ""),
        attendees=list(raw.get("attendees") or []),
        owner=str(raw.get("owner") or ""),
        glossary=[p.get("name", "") for p in people if p.get("name")],
        previous=str(raw.get("previous") or ""),
        people=people,
    )


def _run(pipeline: str, case: dict[str, Any]) -> tuple[dict[str, Any] | None, float]:
    from assistant.nlu import llm

    os.environ["SUMMARY_PIPELINE"] = pipeline
    t0 = time.time()
    if pipeline == "v1":
        out = llm._summarize_recording_v1(case["transcript"], speakers_detected=True)
    else:
        out = llm._summarize_recording_v2(
            case["transcript"], speakers_detected=True, context=_context(case.get("context"))
        )
    return out, time.time() - t0


def _match(expected: list[dict[str, Any]], got: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Жадное сопоставление эталонных задач с полученными по похожести текста."""
    pairs = sorted(
        ((_sim(e.get("task", ""), g.get("task", "")), i, j) for i, e in enumerate(expected) for j, g in enumerate(got)),
        reverse=True,
    )
    used_e, used_g, out = set(), set(), []
    for s, i, j in pairs:
        if s < TASK_MATCH_MIN:
            break
        if i in used_e or j in used_g:
            continue
        used_e.add(i)
        used_g.add(j)
        out.append((i, j))
    return out


def load_gold_md(path: Path) -> list[dict[str, Any]]:
    """Отмеченные строки разметки → эталонные задачи.

    Отметка — «- [x] …» в начале строки или «[x]» / «- [x]» дописанные в конце строки кандидата.
    """
    import re

    mark = r"[xXхХ✓✔+]"
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not re.match(r"^\s*-\s*\[", line):
            continue
        lead = re.match(rf"^\s*-\s*\[{mark}\]\s*(.*)$", line)
        trail = re.search(rf"\s*-?\s*\[{mark}\]\s*$", line)
        if lead:
            body = lead.group(1)
            body = re.sub(rf"\s*-?\s*\[{mark}\]\s*$", "", body)
        elif trail:
            body = re.sub(r"^\s*-\s*\[[^\]]*\]\s*", "", line[: trail.start()])
        else:
            continue
        body = re.sub(r"\s*\((?:[С12ц]+,?)+\)\s*$", "", body).strip()
        parts = [p.strip() for p in body.split("|")]
        task = parts[0] if parts else ""
        if not task:
            continue
        who = parts[1] if len(parts) > 1 else ""
        if who.endswith("?") or who.lower().startswith("спикер"):
            who = ""  # метка спикера — не исполнитель
        out.append({"task": task, "assignee": who, "deadline": parts[2] if len(parts) > 2 else ""})
    return out


MATCH_SYSTEM = """Сопоставь задачи. Даны gold — эталонный список реальных задач встречи — и got — задачи, которые нашла система.
Для каждого элемента got укажи номер задачи gold, которой он соответствует по смыслу (то же действие, тот же объект; формулировка может отличаться), или null, если такой задачи в gold нет.
Несколько элементов got могут соответствовать одной задаче gold — это повторы. Верни ТОЛЬКО JSON: {"match":[номер gold или null для каждого got по порядку]}"""


def _llm_match(gold: list[dict[str, Any]], got: list[dict[str, Any]]) -> Optional[list[Optional[int]]]:
    """Сопоставление по смыслу (модель видит только тексты задач, без транскрипта)."""
    from assistant.nlu import llm

    payload = {"gold": [{"n": i + 1, "task": g.get("task", "")} for i, g in enumerate(gold)],
               "got": [t.get("task", "") for t in got]}
    try:
        obj = llm._json_obj(llm._chat(MATCH_SYSTEM, json.dumps(payload, ensure_ascii=False), operation="eval_match",
                                      model=llm._model_summary(), timeout=120, temperature=0.0, max_tokens=2000)) or {}
    except Exception as ex:
        print(f"[eval] match failed: {ex!r}")
        return None
    m = obj.get("match")
    if not isinstance(m, list) or len(m) != len(got):
        return None
    out: list[Optional[int]] = []
    for x in m:
        try:
            k = int(x) - 1
        except (TypeError, ValueError):
            k = -1
        out.append(k if 0 <= k < len(gold) else None)
    return out


def _metrics(case: dict[str, Any], out: dict[str, Any] | None) -> dict[str, Any]:
    from assistant.lib.summary_normalize import _norm as n, locate_quote, match_person

    if not out:
        return {"failed": 1}
    tasks = [t for t in out.get("tasks") or [] if isinstance(t, dict)]
    tn = n(case["transcript"])
    m: dict[str, Any] = {"failed": 0, "tasks": len(tasks)}
    for k, v in (out.get("summary_stats") or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            m[f"st_{k}"] = v
    if tasks:
        m["with_assignee"] = sum(1 for t in tasks if str(t.get("assignee_name", t.get("assignee")) or "").strip()) / len(tasks)
        m["with_due_iso"] = sum(1 for t in tasks if t.get("due_iso")) / len(tasks)
        quoted = [t for t in tasks if t.get("quote")]
        m["quote_grounded"] = (
            sum(1 for t in quoted if locate_quote(t["quote"], tn) >= 0.8) / len(tasks) if quoted else 0.0
        )
    exp = (case.get("expected") or {}).get("tasks") or []
    if exp and SEMANTIC_MATCH and tasks:
        mm = _llm_match(exp, tasks)
        if mm is not None:
            hit = {k for k in mm if k is not None}
            m["recall"] = len(hit) / len(exp)
            m["precision"] = sum(1 for k in mm if k is not None) / len(tasks)  # дубль реальной задачи — не мусор
            m["dup_rate"] = (sum(1 for k in mm if k is not None) - len(hit)) / len(tasks)
            m["junk_rate"] = sum(1 for k in mm if k is None) / len(tasks)
            m["distinct_precision"] = len(hit) / len(tasks)
            return m
    if exp:
        pairs = _match(exp, tasks)
        m["recall"] = len(pairs) / len(exp)
        m["precision"] = len(pairs) / len(tasks) if tasks else 0.0
        # дубль — лишняя задача, похожая на уже сопоставленную эталонную
        matched_e = {i for i, _ in pairs}
        matched_g = {j for _, j in pairs}
        dups = sum(
            1 for j, g in enumerate(tasks)
            if j not in matched_g and any(_sim(exp[i].get("task", ""), g.get("task", "")) >= TASK_MATCH_MIN for i in matched_e)
        )
        m["dup_rate"] = dups / len(tasks) if tasks else 0.0
        people = list((case.get("context") or {}).get("people") or [])
        ok_who = ok_due = n_who = n_due = 0
        for i, j in pairs:
            e, g = exp[i], tasks[j]
            if e.get("assignee"):
                n_who += 1
                got_who = str(g.get("assignee_name", g.get("assignee")) or "")
                pe, pg = match_person(e["assignee"], people), match_person(got_who, people)
                if (pe and pg and pe.get("name") == pg.get("name")) or (got_who and _sim(e["assignee"], got_who) >= 0.8):
                    ok_who += 1
            if e.get("due_iso"):
                n_due += 1
                ok_due += int(g.get("due_iso") == e["due_iso"])
        if n_who:
            m["assignee_acc"] = ok_who / n_who
        if n_due:
            m["due_acc"] = ok_due / n_due
    return m


def _avg(rows: list[dict[str, Any]], key: str) -> str:
    vals = [r[key] for r in rows if key in r]
    if not vals:
        return "—"
    v = sum(vals) / len(vals)
    return f"{v:.2f}" if key not in ("tasks", "sec") else f"{v:.1f}"


def export_from_notes(user_id: int, limit: int, out_dir: Path) -> None:
    from assistant.lib.meeting_context import context_for_note
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for note in notes_store.list_transcription_notes(user_id, limit=limit * 3):
        full = notes_store.get_note(str(user_id), int(note["id"])) or note
        transcript = tnotes._plain(tnotes.transcript_html(full))
        if len(transcript) < 800:
            continue
        ctx = context_for_note(user_id, full)
        meta = full.get("meta") if isinstance(full.get("meta"), dict) else {}
        case = {
            "note_id": full["id"],
            "title": full.get("title"),
            "transcript": transcript,
            "context": {**ctx.to_payload(), "people": ctx.people},
            # черновик эталона из текущего саммари — проверить и поправить руками
            "expected": {
                "tasks": [
                    {"task": t.get("task", ""), "assignee": t.get("assignee", ""), "due_iso": t.get("due_iso", "")}
                    for t in meta.get("tasks") or [] if isinstance(t, dict)
                ],
                "_draft": True,
            },
        }
        (out_dir / f"note_{full['id']}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2))
        n += 1
        if n >= limit:
            break
    print(f"exported {n} cases → {out_dir}")


ANNOTATE_SYSTEM = """Ты — аккуратный аннотатор эталона для оценки саммари встреч. По транскрипту составь
ПОЛНЫЙ и ТОЧНЫЙ список поручений (action items) и принятых решений. Верни ТОЛЬКО JSON:
{"tasks":[{"task":"","assignee":"","due_iso":""}],"decisions":[""]}

- Задача — конкретное действие, о котором договорились или которое поручили (не идея, не обсуждение, не «было бы хорошо»).
  Включай каждое отдельное поручение, не объединяй разные.
- task — коротко, в инфинитиве.
- assignee — кто делает: имя, если исполнителя назвали или он себя назвал и это однозначно видно из текста;
  если человек взял задачу на себя, а его имя неизвестно — метка спикера («Спикер 2»); если не ясно — "".
- due_iso — дата YYYY-MM-DD, только если срок прозвучал и однозначно считается от meeting_at; иначе "".
- decisions — только принятые решения."""


def annotate(model: str, cases_dir: Path, limit: int) -> None:
    from assistant.nlu import llm

    for f in sorted(cases_dir.glob("*.json"))[:limit]:
        case = json.loads(f.read_text())
        ctx = case.get("context") or {}
        payload = json.dumps(
            {"meeting_at": ctx.get("meeting_at", ""), "attendees": ctx.get("attendees") or [],
             "transcript": case["transcript"][:120000]},
            ensure_ascii=False,
        )
        raw = llm._chat(ANNOTATE_SYSTEM, payload, operation="eval_annotate", model=model,
                        timeout=600, temperature=0.0, max_tokens=8000)
        obj = llm._json_obj(raw) or {}
        tasks = [
            {"task": str(t.get("task") or ""), "assignee": str(t.get("assignee") or ""), "due_iso": str(t.get("due_iso") or "")}
            for t in obj.get("tasks") or [] if isinstance(t, dict) and str(t.get("task") or "").strip()
        ]
        case["expected"] = {"tasks": tasks, "decisions": [str(d) for d in obj.get("decisions") or []],
                            "_annotated_by": model}
        f.write_text(json.dumps(case, ensure_ascii=False, indent=2))
        print(f"{f.name}: {len(tasks)} tasks")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--pipelines", default="v1,v2")
    ap.add_argument("--export-from-notes", type=int, metavar="USER_ID")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--json", action="store_true", help="печать сырых метрик по кейсам")
    ap.add_argument("--annotate", metavar="MODEL", help="переписать expected эталоном от этой модели")
    ap.add_argument("--gold-md", type=Path, metavar="DIR", help="эталон из ручной разметки; прогон только по размеченным")
    ap.add_argument("--string-match", action="store_true", help="сопоставлять задачи по буквам, без модели")
    args = ap.parse_args()

    if args.export_from_notes:
        export_from_notes(args.export_from_notes, args.limit, args.dir)
        return 0

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if args.annotate:
        annotate(args.annotate, args.dir, args.limit)
        return 0
    files = sorted(args.dir.glob("*.json"))
    if not files:
        print(f"нет кейсов в {args.dir}", file=sys.stderr)
        return 1
    pipelines = [p.strip() for p in args.pipelines.split(",") if p.strip()]
    rows: dict[str, list[dict[str, Any]]] = {p: [] for p in pipelines}
    global SEMANTIC_MATCH
    SEMANTIC_MATCH = not args.string_match
    if args.gold_md:
        files = [f for f in files if (args.gold_md / (f.stem + ".md")).is_file()]
    for f in files[: args.limit]:
        case = json.loads(f.read_text())
        if args.gold_md:
            case["expected"] = {"tasks": load_gold_md(args.gold_md / (f.stem + ".md"))}
        for p in pipelines:
            out, sec = _run(p, case)
            m = _metrics(case, out)
            m["sec"] = sec
            rows[p].append(m)
            if args.json:
                print(json.dumps({"case": f.name, "pipeline": p, **m}, ensure_ascii=False))
    keys = ["failed", "tasks", "recall", "distinct_precision", "dup_rate", "junk_rate", "precision", "assignee_acc", "due_acc", "with_assignee", "with_due_iso",
            "quote_grounded", "st_candidates", "st_dropped", "st_windows", "sec"]
    print("metric".ljust(16) + "".join(p.ljust(10) for p in pipelines))
    for k in keys:
        print(k.ljust(16) + "".join(_avg(rows[p], k).ljust(10) for p in pipelines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
