"""Превью брифов к встречам: старая и новая логика рядом, с источниками. Ничего не пишет и не шлёт.

Локально:
    python3 scripts/brief_preview.py --uid 106278723 --match БГПТ --past 7 --n 4 --assume-topics

На проде без деплоя (новый код едет внутри скрипта, база открывается только на чтение):
    python3 scripts/brief_preview.py --bundle | ssh -i ~/.ssh/leo_server root@HOST \\
        'cd /opt/assistant && .venv/bin/python - --uid 106278723 --match БГПТ --n 4 --assume-topics'

--assume-topics — похожие встречи и чаты считаются связанными (как после «Связать» в Фокусе).
Для прошлой встречи бриф собирается «как за 30 минут до неё»: всё, что позже начала, не видно.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Новый код, который в режиме --bundle едет внутри скрипта (на проде его ещё нет).
BUNDLED = [
    ("assistant.stores.meeting_topics", "assistant/stores/meeting_topics.py"),
    ("assistant.services.meeting_topics", "assistant/services/meeting_topics.py"),
    ("assistant.services.meeting_brief", "assistant/services/meeting_brief.py"),
]
PROMPTS = ("MEETING_BRIEF_SYSTEM", "MEETING_BRIEF_RELATED_SYSTEM")


def bundle() -> str:
    import ast

    sources = {name: (ROOT / path).read_text() for name, path in BUNDLED}
    tree = ast.parse((ROOT / "assistant/nlu/prompts.py").read_text())
    prompts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in PROMPTS:
            prompts[node.targets[0].id] = ast.literal_eval(node.value)
    me = Path(__file__).read_text()
    at = me.rindex("\nif __name__ == ")  # последнее вхождение — сам запуск, не эта строка
    return me[:at] + f"\nBUNDLED_SOURCES = {sources!r}\nBUNDLED_PROMPTS = {prompts!r}\n" + me[at:]


def _install_bundle() -> None:
    """Старый meeting_brief → old_mb; новые модули — из BUNDLED_SOURCES."""
    import importlib
    import types

    g = globals()
    old = importlib.import_module("assistant.services.meeting_brief")
    old_prompt = importlib.import_module("assistant.nlu.prompts").MEETING_BRIEF_SYSTEM
    g["OLD_MB"], g["OLD_PROMPT"] = old, old_prompt
    for name, _ in BUNDLED:
        mod = types.ModuleType(name)
        mod.__file__ = f"<bundle {name}>"
        sys.modules[name] = mod
        parent, _, child = name.rpartition(".")
        setattr(importlib.import_module(parent), child, mod)
        exec(compile(g["BUNDLED_SOURCES"][name], mod.__file__, "exec"), mod.__dict__)


def _read_only() -> None:
    """Темы не трогаем в базе: ни таблиц, ни строк."""
    from assistant.stores import meeting_topics as store

    store.topic_for_title = lambda *a, **k: None
    store.decided = lambda *a, **k: set()
    store.topics_for_user = lambda *a, **k: []


def _events(uid: int, past_days: int, ahead_days: int, match: str) -> list[dict]:
    from assistant.services import calendar_sources as cal_sources
    from assistant.services.calendar import _tz_for
    from assistant.services import meeting_brief as mb

    tz = _tz_for(uid)
    now = datetime.now(tz)
    evs = cal_sources.list_events_in_window(uid, now - timedelta(days=past_days), now + timedelta(days=ahead_days))
    m = match.lower()
    out = []
    for ev in evs:
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        if not isinstance(ev.get("start"), datetime) or not mb._attendees(raw):
            continue
        if m and m not in str(ev.get("summary") or "").lower().replace("-", "") and m not in str(ev.get("summary") or "").lower():
            continue
        out.append(ev)
    return sorted(out, key=lambda e: e["start"])


def _old_brief(uid: int, ev: dict) -> str:
    g = globals()
    old = g.get("OLD_MB")
    if old is None:
        return "(старая логика доступна только в режиме --bundle на проде)"
    from assistant.nlu import prompts

    new_prompt = prompts.MEETING_BRIEF_SYSTEM
    prompts.MEETING_BRIEF_SYSTEM = g["OLD_PROMPT"]
    try:
        return old.build_brief_message(uid, ev) or "(не отправили бы)"
    finally:
        prompts.MEETING_BRIEF_SYSTEM = new_prompt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", action="store_true", help="напечатать самодостаточный скрипт для прода")
    ap.add_argument("--uid", type=int)
    ap.add_argument("--match", default="")
    ap.add_argument("--past", type=int, default=14)
    ap.add_argument("--ahead", type=int, default=2)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--assume-topics", action="store_true")
    ap.add_argument("--no-old", action="store_true")
    a = ap.parse_args()
    if a.bundle:
        sys.stdout.write(bundle())
        return
    sys.path.insert(0, str(Path.cwd()))
    import assistant.config  # noqa: F401  .env

    if "BUNDLED_SOURCES" in globals():
        _install_bundle()
        from assistant.nlu import prompts

        for k, v in globals()["BUNDLED_PROMPTS"].items():
            setattr(prompts, k, v)
    _read_only()
    from assistant.services import meeting_brief as mb

    evs = _events(a.uid, a.past, a.ahead, a.match)[-a.n:]
    print(f"встреч: {len(evs)}\n")
    for ev in evs:
        title = ev.get("summary")
        print("=" * 80)
        print(f"{ev['start']:%d.%m %H:%M}  {title}")
        print("=" * 80)
        dbg: dict = {}
        facts = mb.collect_facts(a.uid, ev, assume_topics=a.assume_topics, debug=dbg)
        start = mb._start_dt(ev)
        facts["now"] = (start - timedelta(minutes=30)).isoformat(timespec="minutes") if start else ""
        t = dbg.get("topic") or {}
        print("Тема — встречи:", [f"{m['start']:%d.%m} {m['title']}" for m in t.get("meetings", [])])
        print("Тема — чаты:", [c.get("title") for c in t.get("chats", [])])
        print("Кандидаты связать:", json.dumps(t.get("candidates"), ensure_ascii=False))
        print("Ссылка созвона:", facts.get("join_url") or "—")
        print("Описание после чистки:", repr(facts["meeting"]["description"][:300]))
        print("series:", json.dumps(facts["series"], ensure_ascii=False)[:1500])
        print("stuck:", json.dumps(facts["stuck"], ensure_ascii=False))
        print("chats:", json.dumps(facts["chats"], ensure_ascii=False)[:1500])
        print("related (поиск → проверка):")
        for r in dbg.get("related", []):
            print("   ", json.dumps(r, ensure_ascii=False))
        print("related принято:", json.dumps(facts["related"], ensure_ascii=False))
        if not a.no_old:
            print("\n--- СТАРЫЙ БРИФ ---")
            print(_old_brief(a.uid, ev))
        print("\n--- НОВЫЙ БРИФ ---")
        if not mb.has_substance(facts):
            print("(не отправили бы: кроме «кто и когда виделись» сказать нечего)")
        else:
            print(mb.build_brief_message(a.uid, ev, facts=facts))
        print()


if __name__ == "__main__":
    main()
