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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DIR = ROOT / "data" / "eval" / "summaries"
TASK_MATCH_MIN = 0.55


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


def _metrics(case: dict[str, Any], out: dict[str, Any] | None) -> dict[str, Any]:
    from assistant.lib.summary_normalize import _norm as n, locate_quote, match_person

    if not out:
        return {"failed": 1}
    tasks = [t for t in out.get("tasks") or [] if isinstance(t, dict)]
    tn = n(case["transcript"])
    m: dict[str, Any] = {"failed": 0, "tasks": len(tasks)}
    if tasks:
        m["with_assignee"] = sum(1 for t in tasks if str(t.get("assignee_name", t.get("assignee")) or "").strip()) / len(tasks)
        m["with_due_iso"] = sum(1 for t in tasks if t.get("due_iso")) / len(tasks)
        quoted = [t for t in tasks if t.get("quote")]
        m["quote_grounded"] = (
            sum(1 for t in quoted if locate_quote(t["quote"], tn) >= 0.8) / len(tasks) if quoted else 0.0
        )
    exp = (case.get("expected") or {}).get("tasks") or []
    if exp:
        pairs = _match(exp, tasks)
        m["recall"] = len(pairs) / len(exp)
        m["precision"] = len(pairs) / len(tasks) if tasks else 0.0
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--pipelines", default="v1,v2")
    ap.add_argument("--export-from-notes", type=int, metavar="USER_ID")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--json", action="store_true", help="печать сырых метрик по кейсам")
    args = ap.parse_args()

    if args.export_from_notes:
        export_from_notes(args.export_from_notes, args.limit, args.dir)
        return 0

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    files = sorted(args.dir.glob("*.json"))
    if not files:
        print(f"нет кейсов в {args.dir}", file=sys.stderr)
        return 1
    pipelines = [p.strip() for p in args.pipelines.split(",") if p.strip()]
    rows: dict[str, list[dict[str, Any]]] = {p: [] for p in pipelines}
    for f in files[: args.limit]:
        case = json.loads(f.read_text())
        for p in pipelines:
            out, sec = _run(p, case)
            m = _metrics(case, out)
            m["sec"] = sec
            rows[p].append(m)
            if args.json:
                print(json.dumps({"case": f.name, "pipeline": p, **m}, ensure_ascii=False))
    keys = ["failed", "tasks", "recall", "precision", "assignee_acc", "due_acc", "with_assignee", "with_due_iso", "quote_grounded", "sec"]
    print("metric".ljust(16) + "".join(p.ljust(10) for p in pipelines))
    for k in keys:
        print(k.ljust(16) + "".join(_avg(rows[p], k).ljust(10) for p in pipelines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
