#!/usr/bin/env python3
"""Eval ответов Лео (operation=ask), дешёвый вариант: варианты против ответов, которые Лео уже дал.

Образец «как сейчас» — сохранённый ответ Лео и действия, которые он тогда сделал (журнал gpt_calendar_actions);
нынешнюю модель заново не спрашиваем. Рукава:
  auto  — вопросы, которые правило route_auto отдаёт дешёвой модели: отвечает дешёвая модель;
  probe — несколько вопросов, которые правило оставило на сильной: тоже дешёвая (не слишком ли правило осторожно);
  trim  — та же модель, что ответила тогда, с урезанной подсказкой (только там, где урезание что-то меняет).
Судья вслепую, в обоих порядках, сравнивает пары и отдельно помечает каждый вопрос «простой / сложный» —
так проверяется само правило «Авто».

Шаги (на сервере, данные в data/eval/ask/, data/ в .gitignore):

    python scripts/eval_ask.py usage  --uid 106278723
    python scripts/eval_ask.py export --uid 106278723 --limit 50
    python scripts/eval_ask.py run    --arms auto,probe,trim
    python scripts/eval_ask.py judge
    python scripts/eval_ask.py report        # отчёт + review.json для ручной оценки

При прогоне инструменты-«чтения» (календарь, поиск задач/заметок/почты) выполняются по-настоящему с датой
вопроса (журнал действий — в памяти), всё, что меняет данные (встречи, задачи, напоминания, правки, письма),
подменено заглушкой: вызов и аргументы записываются, ничего не выполняется. Расход eval в usage_events не пишется.
"""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ASSISTANT_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUT = ROOT / "data" / "eval" / "ask"
CASES = OUT / "cases.jsonl"
RUNS = OUT / "runs.jsonl"
JUDGE = OUT / "judge.jsonl"

CHEAP_MODEL = "google/gemini-2.5-flash"

READ_TOOLS = {
    "calendar_events", "calendar_free_slots", "calendar_availability", "calendar_time_stats",
    "contacts_find", "reminders_list", "meeting_materials", "person_history", "tasks_search",
    "meeting_tasks_search", "commitments", "memory_search", "notes_find", "mail_search", "mail_thread",
}
REFUSAL_RE = re.compile(
    r"(не могу|не имею доступа|нет доступа|не получилось|не удалось|i can.?t|i cannot|не располагаю)", re.I
)


def _jl_read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _jl_append(path: Path, row: dict[str, Any], lock: threading.Lock | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    if lock:
        with lock:
            with path.open("a") as f:
                f.write(line)
    else:
        with path.open("a") as f:
            f.write(line)


def _parse_ts(raw: Any) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ usage


def cmd_usage(a: argparse.Namespace) -> None:
    path = ROOT / "assistant" / "lib" / "usage.sqlite"
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    since = (datetime.utcnow() - timedelta(days=a.days)).strftime("%Y-%m-%d")
    out: dict[str, Any] = {"days": a.days, "since": since}

    def q(sql: str, *args: Any) -> list[dict[str, Any]]:
        cur = c.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    out["ask_all_by_model"] = q(
        "SELECT model, COUNT(*) n, ROUND(SUM(COALESCE(cost_usd,0)),3) cost,"
        " CAST(AVG(prompt_tokens) AS INT) avg_prompt, CAST(AVG(completion_tokens) AS INT) avg_completion"
        " FROM usage_events WHERE operation='ask' AND date_utc>=? GROUP BY model ORDER BY cost DESC", since)
    out["ask_user_by_model"] = q(
        "SELECT model, COUNT(*) n, ROUND(SUM(COALESCE(cost_usd,0)),3) cost, CAST(AVG(prompt_tokens) AS INT) avg_prompt,"
        " MAX(prompt_tokens) max_prompt FROM usage_events WHERE operation='ask' AND date_utc>=? AND telegram_user_id=?"
        " GROUP BY model ORDER BY cost DESC", since, str(a.uid))
    out["all_ops"] = q(
        "SELECT operation, COUNT(*) n, ROUND(SUM(COALESCE(cost_usd,0)),3) cost FROM usage_events"
        " WHERE date_utc>=? GROUP BY operation ORDER BY cost DESC LIMIT 40", since)
    for m in a.bg_models.split(","):
        out[f"ops_on_{m}"] = q(
            "SELECT operation, COUNT(*) n, ROUND(SUM(COALESCE(cost_usd,0)),3) cost,"
            " CAST(AVG(prompt_tokens) AS INT) avg_prompt, CAST(AVG(completion_tokens) AS INT) avg_completion,"
            " COUNT(DISTINCT telegram_user_id) users"
            " FROM usage_events WHERE date_utc>=? AND (model=? OR model LIKE ?) GROUP BY operation ORDER BY cost DESC",
            since, m, "%/" + m)
    out["total_cost"] = q("SELECT ROUND(SUM(COALESCE(cost_usd,0)),3) cost FROM usage_events WHERE date_utc>=?", since)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "usage.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ export


def _plain(text: str) -> str:
    from assistant.lib.telegram_html import html_to_plain, uses_html_markup

    t = str(text or "")
    return html_to_plain(t) if uses_html_markup(t) else t


WRITE_KIND = {
    "calendar_create_event": "event_create", "calendar_update_event": "event_update", "task_create": "task_create",
    "reminder_create": "reminder_create", "propose_delete": "delete", "offer_slots": "slot",
    "focus_item_update": "focus_item", "mail_draft_reply": "email_draft",
}


def _norm_kind(kind: str) -> str:
    return "delete" if kind.endswith("_delete") else kind


def _orig_actions(conn: sqlite3.Connection, body: str) -> list[dict[str, Any]]:
    """Действия, которые Лео сделал тогда («[[leo-cal:id,…]]» в конце ответа) — из журнала, только чтение."""
    from assistant.services.leo_agent import CAL_MARKER_RE

    m = CAL_MARKER_RE.search(body)
    if not m:
        return []
    ids = [x for x in m.group(1).split(",") if x]
    out = []
    for aid in ids:
        r = conn.execute(
            "SELECT kind, status, title, payload_json FROM gpt_calendar_actions WHERE id=?", (aid,)
        ).fetchone()
        if not r:
            continue
        kind = _norm_kind(str(r[0]))
        if kind not in set(WRITE_KIND.values()):
            continue  # карточки show_*, найденные письма — это не действия
        try:
            pl = json.loads(r[3] or "{}")
        except ValueError:
            pl = {}
        out.append({"kind": kind, "status": r[1], "title": r[2], "when": str(pl.get("when") or pl.get("start") or "")})
    return out


def _label_to_model(label: str) -> str:
    from assistant.integrations.openrouter_client import GPT_PICKER_MODELS, PAIE_REASONING_MODELS

    lab = label.strip()
    short = lab[4:].lower() if lab.lower().startswith("gpt-") else lab.lower()  # «GPT-claude-opus-5.5», «GPT-o3»
    for mid, name in (*GPT_PICKER_MODELS, *PAIE_REASONING_MODELS):
        if name == lab or mid.split("/")[-1] in (short, lab.lower()):
            return mid
    if lab.lower().startswith("gpt-"):
        return "openai/" + lab.lower()
    return ""


def cmd_export(a: argparse.Namespace) -> None:
    from assistant.services import leo_agent
    from assistant.stores import chat_threads, share_comments as sc, user_agents, user_prefs
    from assistant.stores import notes as notes_store
    from assistant.integrations.openrouter_client import sanitize_openrouter_model_id

    uid = int(a.uid)
    suid = str(uid)
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM share_comments WHERE owner_user_id=? AND item_kind IN ('local','chat')"
        " ORDER BY item_kind, item_id, created_at, id", (suid,))]
    by_item: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_item.setdefault((r["item_kind"], r["item_id"]), []).append(r)

    agent = user_agents.runtime_config(uid, "gpt")
    web_model = sanitize_openrouter_model_id(str(agent.get("model") or "")) or ""
    tg_model = user_prefs.get_tg_leo_model(uid)
    system_base = leo_agent.gpt_system_text(agent)

    from assistant.skills.leo_chat import CHANNEL_HINT, IDLE_RESET

    cases: list[dict[str, Any]] = []
    skipped = {"foreign": 0, "not_owner": 0, "attach": 0, "old": 0}
    for (kind, iid), items in by_item.items():
        origin = "web"
        note_title = note_text = ""
        if kind == "chat":
            th = chat_threads.get_thread(uid, iid)
            if not th:
                skipped["not_owner"] += 1
                continue
            if th.get("origin") == chat_threads.TELEGRAM_ORIGIN:
                origin = "telegram"
        else:
            try:
                note = notes_store.get_accessible_note(uid, int(iid))
            except (TypeError, ValueError):
                note = None
            if not note or str(note.get("owner_user_id") or note.get("user_id") or "") != suid:
                skipped["not_owner"] += 1
                continue
            note_title = str(note.get("title") or "")
            note_text = _plain(str(note.get("body") or ""))[:60000]
        per_item = 0
        for i in range(len(items) - 1, 0, -1):
            q, ans = items[i - 1], items[i]
            if not (sc.is_gpt_comment(ans) and not sc.is_agent_turn(ans) and not sc.is_research_turn(ans)):
                continue
            if sc.is_gpt_comment(q) or sc.is_agent_turn(q) or str(q.get("author_user_id")) != suid:
                continue
            if str(q.get("created_at") or "") < since:
                skipped["old"] += 1
                continue
            body = str(q.get("body") or "").strip()
            if not body or body == "📎":
                skipped["attach"] += 1
                continue
            prior = items[: i - 1]
            # Только диалоги самого пользователя: чужие реплики в истории — пропускаем случай.
            if any(str(p.get("author_user_id")) != suid for p in prior):
                skipped["foreign"] += 1
                continue
            qts = _parse_ts(q["created_at"])
            history = leo_agent.history_from_comments(
                prior, idle_reset=IDLE_RESET if origin == "telegram" else None, now=qts
            )
            cases.append({
                "id": f"{kind}:{iid}:{q['id']}",
                "uid": uid,
                "kind": kind,
                "item_id": iid,
                "origin": origin,
                "asked_at": qts.isoformat(),
                "question": body,
                "quote": str(q.get("quote") or ""),
                "history": history,
                "note_title": note_title,
                "note_text": note_text,
                "orig_answer": leo_agent.strip_cal_marker(str(ans.get("body") or "")),
                "orig_label": str(ans.get("author_name") or ""),
                "orig_model": _label_to_model(str(ans.get("author_name") or "")),
                "orig_actions": _orig_actions(conn, str(ans.get("body") or "")),
                "current_model": tg_model if origin == "telegram" else web_model,
                "system_base": system_base,
                "channel_hint": CHANNEL_HINT if origin == "telegram" else "",
                "temperature": agent.get("temperature"),
                "web": bool(agent.get("web_search")),
            })
            per_item += 1
            if per_item >= a.per_item:
                break
    cases.sort(key=lambda c: c["asked_at"], reverse=True)
    cases = cases[: a.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    CASES.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    from collections import Counter

    print(json.dumps({
        "cases": len(cases), "skipped": skipped,
        "by_kind": Counter(f"{c['kind']}/{c['origin']}" for c in cases),
        "orig_models": Counter(c["orig_model"] or c["orig_label"] for c in cases),
        "auto_routes": Counter(" / ".join(route_auto(c)) for c in cases),
        "with_actions": sum(1 for c in cases if c["orig_actions"]),
        "current_model": {"web": web_model, "telegram": tg_model},
        "range": [cases[-1]["asked_at"] if cases else "", cases[0]["asked_at"] if cases else ""],
    }, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ правила вариантов

_EDIT_RE = re.compile(
    r"(исправ|перепиш|допиш|добав|удали|убери|замени|сократи|переформулир|оформи|структурир|заполни|"
    r"таблиц|внеси|встав|перенеси\s+в\s+замет|поправь|отредакт|раздел|пункт|абзац|в\s+заметк|в\s+документ)",
    re.I,
)
_CANVAS_RE = re.compile(r"(презентац|слайд|\bдек\b|канвас|canvas|deck)", re.I)
_RESEARCH_RE = re.compile(
    r"(проанализ|анализ|сравни|исслед|стратег|разбер|оцени|обоснуй|посчитай|рассчитай|прогноз|"
    r"бизнес-?план|составь\s+план|юридич|договор|\bкод\b|скрипт|sql|формул)",
    re.I,
)
_TOOLISH_RE = re.compile(
    r"(встреч|задач|напомин|календар|созвон|что\s+решили|обещал|договорил|найди|найти|поищи|ссылк|"
    r"почт|письм|расписан|свобод|занят|перенеси|поставь|запланируй|отмени)",
    re.I,
)


_DOC_RE = re.compile(
    r"(подготов|собер|собра|состав|сдела|напиш|напис|оформ|распис|опиш).{0,40}"
    r"(документ|описани|план|текст|письм|отч[её]т|стать|резюме|\bТЗ\b|спецификац|концепц)",
    re.I,
)


def route_auto(case: dict[str, Any]) -> tuple[str, str]:
    """«Авто»: (cheap|strong, причина). Сомнение — в пользу сильной модели."""
    from assistant.lib import board_ai, chat_links
    from assistant.services import gpt_calendar_tools as g, mail_tools

    q = case["question"]
    hist = case.get("history") or []
    if hist:
        last_bot = next((m["content"] for m in reversed(hist) if m["role"] == "assistant"), "")
        if "[Уже выполнено" in last_bot or "[В чате показаны" in last_bot:
            return "strong", "продолжение действий в календаре"
        if len(q) < 60:  # «Мыздриков», «Пятница 14:00» — смысл в предыдущем вопросе
            prev = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
            if prev and route_auto({**case, "question": prev, "history": []})[0] == "strong":
                return "strong", "продолжение сложного вопроса"
    if _DOC_RE.search(q):
        return "strong", "создать документ"
    if chat_links.find_links(q):
        return "strong", "ссылка"
    if g.looks_like_calendar_question(q) or mail_tools.looks_like_mail_question(q):
        return "strong", "календарь/почта"
    if _TOOLISH_RE.search(q):
        return "strong", "нужны инструменты"
    if board_ai.looks_like_board_request(q) or _CANVAS_RE.search(q):
        return "strong", "доска/презентация"
    if case["kind"] == "local" and _EDIT_RE.search(q):
        return "strong", "правка документа"
    if _RESEARCH_RE.search(q):
        return "strong", "анализ"
    if len(q) > 350 or len(case.get("quote") or "") > 1500:
        return "strong", "длинный запрос"
    return "cheap", "простой вопрос"


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-яё0-9]{4,}", str(text or "").lower())}


TRIM_KEEP_LAST = 6
TRIM_HISTORY_CHARS = 8000


def trim_history(history: list[dict[str, str]], question: str) -> list[dict[str, str]]:
    """Последние 6 реплик — всегда; старые — только если пересекаются с вопросом по словам; ≤ 8000 знаков."""
    if len(history) <= TRIM_KEEP_LAST:
        keep = list(history)
    else:
        qs = _stems(question)
        tail = history[-TRIM_KEEP_LAST:]
        old = history[:-TRIM_KEEP_LAST]
        keep = []
        for j in range(0, len(old) - 1, 2):  # пары user→assistant
            pair = old[j: j + 2]
            if len(qs & _stems(" ".join(p["content"] for p in pair))) >= 2:
                keep.extend(pair)
        keep += tail
    total, out = 0, []
    for m in reversed(keep):
        total += len(m["content"])
        if total > TRIM_HISTORY_CHARS and out:
            break
        out.append(m)
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


# ------------------------------------------------------------------ сборка запроса (как /gpt/chat и run_turn)


def build_request(case: dict[str, Any], *, trim: bool) -> tuple[dict[str, Any], dict[str, int]]:
    from assistant.lib import board_ai, note_edits, web_canvas
    from assistant.services import leo_agent

    uid = int(case["uid"])
    q = case["question"]
    system = leo_agent.append_system(case["system_base"], case.get("channel_hint") or "")
    prompt = q
    parts: dict[str, int] = {}
    local = case["kind"] == "local"
    edit = bool(_EDIT_RE.search(q))
    if local:
        if not trim or edit:
            add = note_edits.system_addon()
            system = (system.rstrip() + "\n\n" + add).strip()
            block = note_edits.prompt_block(case["note_text"] or "(лист пуст)")
            prompt = (prompt.rstrip() + "\n\n" + block).strip()
            parts["note_edits_addon"] = len(add)
            parts["doc_view"] = len(block)
        if not trim or _CANVAS_RE.search(q):
            add = web_canvas.canvas_system_addon()
            system = (system.rstrip() + "\n\n" + add).strip()
            parts["canvas_addon"] = len(add)
        if not trim or board_ai.looks_like_board_request(q):
            add = board_ai.system_addon()
            system = (system.rstrip() + "\n\n" + add).strip()
            parts["board_addon"] = len(add)
    cal = leo_agent.calendar_addon(uid)
    system = leo_agent.append_system(system, cal)
    parts["calendar_addon"] = len(cal)
    history = trim_history(case["history"], q) if trim else list(case["history"])
    parts["history"] = sum(len(m["content"]) for m in history)
    parts["history_turns"] = len(history)
    parts["note_text"] = min(len(case["note_text"]), 8000) if local else 0
    parts["system_total"] = len(system)
    kw: dict[str, Any] = {
        "question": prompt,
        "context": "",
        "history": history or None,
        "note_title": case["note_title"] if local else "",
        "note_text": case["note_text"] if local else "",
        "quote": case.get("quote") or "",
        "system": system,
        "temperature": case.get("temperature"),
        "web": bool(case.get("web")),
        "max_tokens": 16384 if local else None,
        "intent_text": q,
    }
    return kw, parts


# ------------------------------------------------------------------ песочница


_FROZEN: contextvars.ContextVar[datetime | None] = contextvars.ContextVar("eval_now", default=None)
_USAGE: contextvars.ContextVar[list | None] = contextvars.ContextVar("eval_usage", default=None)
_SANDBOX_DONE = False


def install_sandbox() -> None:
    """Время вопроса вместо «сейчас», журнал действий в памяти, расход — в eval, картинки не рисуем."""
    global _SANDBOX_DONE
    if _SANDBOX_DONE:
        return
    from assistant.integrations import openrouter_client as orc
    from assistant.nlu import llm
    from assistant.services import gpt_calendar_tools as g

    real_dt = g.datetime

    class _Meta(type):
        def __instancecheck__(cls, obj: Any) -> bool:  # isinstance(x, datetime) в модуле — как раньше
            return isinstance(obj, real_dt)

    class FrozenDT(real_dt, metaclass=_Meta):  # type: ignore[misc,valid-type]
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            f = _FROZEN.get()
            if f is None:
                return real_dt.now(tz)
            return f.astimezone(tz) if tz else f.astimezone().replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            f = _FROZEN.get()
            return real_dt.utcnow() if f is None else f.astimezone(timezone.utc).replace(tzinfo=None)

    g.datetime = FrozenDT  # type: ignore[misc]

    mem = sqlite3.connect(":memory:", check_same_thread=False)
    mem_lock = threading.RLock()

    def _mem_conn() -> sqlite3.Connection:
        mem.execute(
            "CREATE TABLE IF NOT EXISTS gpt_calendar_actions (id TEXT PRIMARY KEY, owner_uid INTEGER NOT NULL,"
            " kind TEXT NOT NULL, status TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL"
            " DEFAULT '{}', before_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}',"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        return mem

    g._conn = _mem_conn  # type: ignore[assignment]
    g._lock = lambda: mem_lock  # type: ignore[assignment]

    def _capture(**kw: Any) -> None:
        bucket = _USAGE.get()
        if bucket is not None:
            bucket.append({"model": kw.get("model"), **(kw.get("usage") or {})})

    orc.insert_usage_event = _capture  # type: ignore[assignment]
    llm._materialize_generated_media = lambda text, images, **_: (text, images)  # type: ignore[assignment]
    _SANDBOX_DONE = True


def make_recorder(case: dict[str, Any], cache: dict[str, str], cache_lock: threading.Lock):
    from assistant.services import leo_agent
    from assistant.services import gpt_calendar_tools as g

    ctx = leo_agent.make_calendar_context(int(case["uid"]), telegram_username=None, display_name="Пользователь")
    real = g.make_runner(ctx)
    calls: list[dict[str, Any]] = []

    def run(name: str, args: dict[str, Any]) -> str:
        calls.append({"name": name, "args": args})
        if name in READ_TOOLS:
            key = name + json.dumps(args, ensure_ascii=False, sort_keys=True)
            with cache_lock:
                hit = cache.get(key)
            if hit is not None:
                return hit
            out = real(name, args)
            with cache_lock:
                cache.setdefault(key, out)
            return out
        return json.dumps(
            {"ok": True, "id": f"stub-{len(calls)}", "note": "Выполнено. Коротко скажи пользователю, что сделал."},
            ensure_ascii=False,
        )

    return run, calls


def run_arm(case: dict[str, Any], arm: str, model: str, *, trim: bool, cache: dict[str, str],
            cache_lock: threading.Lock) -> dict[str, Any]:
    from assistant.nlu import llm
    from assistant.services import leo_agent

    _FROZEN.set(_parse_ts(case["asked_at"]))
    bucket: list[dict[str, Any]] = []
    _USAGE.set(bucket)
    kw, parts = build_request(case, trim=trim)
    runner, calls = make_recorder(case, cache, cache_lock)
    tk = leo_agent.tool_kwargs(leo_agent.make_calendar_context(int(case["uid"]), telegram_username=None,
                                                               display_name="Пользователь"), case["question"])
    t0 = time.time()
    row: dict[str, Any] = {"case_id": case["id"], "arm": arm, "model": model, "parts": parts}
    try:
        out = llm.answer_with_context_result(
            kw.pop("question"), kw.pop("context"), model=model, tools=tk["tools"], tool_runner=runner,
            tool_choice_first=tk["tool_choice_first"], **kw,
        )
        row["answer"] = str(out.get("answer") or "")
        row["used_model"] = out.get("model")
    except Exception as e:  # noqa: BLE001
        row["error"] = repr(e)[:500]
        row["answer"] = ""
    row["latency_s"] = round(time.time() - t0, 1)
    row["tool_calls"] = calls
    row["steps"] = len(bucket)
    row["prompt_tokens"] = sum(int(u.get("prompt_tokens") or 0) for u in bucket)
    row["completion_tokens"] = sum(int(u.get("completion_tokens") or 0) for u in bucket)
    row["cached_tokens"] = sum(int(u.get("cached_tokens") or 0) for u in bucket)
    row["cost"] = round(sum(float(u.get("cost") or 0) for u in bucket), 6)
    return row


def trim_cut(case: dict[str, Any]) -> float:
    """Какую долю знаков подсказки убирает урезание на этом вопросе (без вызова модели)."""
    _kw, full = build_request(case, trim=False)
    _kw, cut = build_request(case, trim=True)

    def size(p: dict[str, int]) -> int:
        return p["system_total"] + p["history"] + p.get("doc_view", 0)

    return 1 - size(cut) / max(1, size(full))


def base_row(case: dict[str, Any]) -> dict[str, Any]:
    """Ответ Лео, который пользователь тогда получил, — образец «как сейчас»."""
    return {
        "case_id": case["id"], "arm": "orig", "model": case.get("orig_model") or "",
        "answer": case["orig_answer"],
        "tool_calls": [{"name": x["kind"], "args": {"title": x["title"], "when": x["when"]}} for x in case["orig_actions"]],
        "write_kinds": sorted(x["kind"] for x in case["orig_actions"]),
    }


def fix_row(row: dict[str, Any]) -> dict[str, Any]:
    """Как /gpt/chat: блок ```leo-note``` — это правка заметки (действие), а не текст ответа."""
    if "```leo-note" not in row.get("answer", "").lower() or row.get("note_edit_fixed"):
        return row
    from assistant.lib import note_edits

    clean, edits = note_edits.parse_blocks(row["answer"])
    row["answer"] = clean or "Внёс изменения в заметку — проверьте подсвеченное."
    row["tool_calls"] = [*row["tool_calls"], {"name": "note_edit", "args": {"edits": len(edits),
                         "text": json.dumps(edits, ensure_ascii=False)[:600]}}]
    row["note_edit_fixed"] = True
    return row


def _variant_kinds(calls: list[dict[str, Any]]) -> list[str]:
    return sorted(WRITE_KIND[c["name"]] for c in calls if c["name"] in WRITE_KIND)


def cmd_run(a: argparse.Namespace) -> None:
    from assistant.integrations.openrouter_client import model_emits_images, tg_leo_model_allowed

    install_sandbox()
    cases = _jl_read(CASES)
    if a.only:
        cases = cases[: a.only]
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    done = {(r["case_id"], r["arm"]) for r in _jl_read(RUNS) if not r.get("error")}
    lock = threading.Lock()
    caches: dict[str, dict[str, str]] = {}
    cache_locks: dict[str, threading.Lock] = {}
    for c in cases:
        c.setdefault("uid", int(a.uid))
        caches[c["id"]] = {}
        cache_locks[c["id"]] = threading.Lock()
    strong = [c for c in cases if route_auto(c)[0] == "strong"]
    probe_ids = {c["id"] for c in random.Random(7).sample(strong, min(a.probe, len(strong)))}
    trim_pool = sorted(
        (c for c in cases if c.get("orig_model") and tg_leo_model_allowed(c["orig_model"])
         and not model_emits_images(c["orig_model"]) and trim_cut(c) >= 0.2),
        key=lambda c: c["asked_at"], reverse=True,
    )
    trim_ids = {c["id"] for c in trim_pool[: a.trim_max]}

    jobs: list[tuple[dict[str, Any], str, str, bool]] = []
    for c in cases:
        route = route_auto(c)[0]
        for arm in arms:
            if arm == "auto" and route == "cheap":
                jobs.append((c, arm, a.cheap_model, False))
            elif arm.startswith("auto:") and route == "cheap":
                jobs.append((c, arm, arm[5:], False))
            elif arm == "probe" and c["id"] in probe_ids:
                jobs.append((c, arm, a.cheap_model, False))
            elif arm == "trim" and c["id"] in trim_ids:
                jobs.append((c, arm, c["orig_model"], True))
    jobs = [j for j in jobs if (j[0]["id"], j[1]) not in done]
    print(f"[eval_ask] cases={len(cases)} cheap={len(cases) - len(strong)} probe={len(probe_ids)} "
          f"trim={len(trim_ids)} jobs={len(jobs)}", flush=True)

    def work(job: tuple[dict[str, Any], str, str, bool]) -> None:
        c, arm, model, trim = job
        row = run_arm(c, arm, model, trim=trim, cache=caches[c["id"]], cache_lock=cache_locks[c["id"]])
        if trim:
            row["parts_full"] = build_request(c, trim=False)[1]
        _jl_append(RUNS, row, lock)
        print(f"[eval_ask] {c['id']} {arm} {model} ${row['cost']} p={row['prompt_tokens']} "
              f"tools={[t['name'] for t in row['tool_calls']]} err={row.get('error', '')[:80]}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, jobs))


# ------------------------------------------------------------------ судья

JUDGE_MODEL = "openai/gpt-5.5"

JUDGE_SYSTEM = """Ты — строгий оценщик ответов ИИ-ассистента «Лео» (рабочие заметки, календарь, задачи).
Тебе дают контекст запроса и два ответа A и B на одно и то же сообщение пользователя, а также какие
действия сделал каждый (создать встречу, задачу и т. п.). Ответы могли быть получены в разные дни, поэтому
мелкие расхождения в данных календаря сами по себе не ошибка — ошибка, если ответ не по делу, выдумывает,
не делает нужного действия или делает лишнее/неверное.
Оцени, какой ответ лучше для пользователя: верность фактам из контекста, выполнение просьбы (нужное действие
с правильными параметрами), полнота без воды, ясность, отсутствие выдумок.
Разная длина или стиль сами по себе — не причина. Если ответы по сути равноценны — tie.
Верни только JSON: {"winner":"A"|"B"|"tie","severity":0-3,"reason":"одно предложение по-русски"}
severity: насколько заметна разница (0 — нет, 1 — мелочь, 2 — заметно, 3 — серьёзная ошибка в худшем)."""

LABEL_SYSTEM = """Ассистент «Лео» может ответить на сообщение быстрой дешёвой моделью или сильной дорогой.
Реши по сообщению пользователя (и контексту), справится ли быстрая модель так же хорошо.
simple — короткий вопрос или просьба, ответ из общих знаний или из уже данного текста: объяснить, перевести,
переформулировать короткий текст, поболтать, коротко ответить.
complex — нужно работать с данными пользователя (календарь, задачи, заметки, почта, встречи) или действовать
(создать/перенести встречу, задачу, напоминание), править документ, делать презентацию или доску, разбирать
длинный текст, анализировать, считать, писать код, рассуждать в несколько шагов.
Верни только JSON: {"label":"simple"|"complex","reason":"коротко по-русски"}"""


def _tools_brief(calls: list[dict[str, Any]]) -> str:
    if not calls:
        return "(действий нет)"
    return "\n".join(f"- {c['name']}({json.dumps(c['args'], ensure_ascii=False)[:300]})" for c in calls[:10])


def _context_block(case: dict[str, Any], note_chars: int = 3000) -> str:
    hist = case["history"][-4:]
    h = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in hist) or "(нет)"
    note = ""
    if case["kind"] == "local":
        note = f"\n\nЗАМЕТКА «{case['note_title']}» (начало):\n{case['note_text'][:note_chars]}"
    quote = f"\n\nВЫДЕЛЕННЫЙ ТЕКСТ:\n{case['quote'][:1000]}" if case.get("quote") else ""
    return (
        f"Канал: {'Telegram' if case['origin'] == 'telegram' else 'приложение'}. Вопрос задан {case['asked_at'][:16]}."
        f"{note}{quote}\n\nПОСЛЕДНИЕ РЕПЛИКИ ДИАЛОГА:\n{h}\n\nСООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:\n{case['question']}"
    )


def _judge_prompt(case: dict[str, Any], ra: dict[str, Any], rb: dict[str, Any]) -> str:
    return (
        _context_block(case)
        + f"\n\n=== ОТВЕТ A ===\nДействия:\n{_tools_brief(_writes(ra))}\nТекст:\n{ra['answer'][:4000]}"
        + f"\n\n=== ОТВЕТ B ===\nДействия:\n{_tools_brief(_writes(rb))}\nТекст:\n{rb['answer'][:4000]}"
    )


def _writes(row: dict[str, Any]) -> list[dict[str, Any]]:
    if row.get("arm") == "orig":
        return row["tool_calls"]
    return [c for c in row["tool_calls"] if c["name"] in WRITE_KIND or c["name"] == "note_edit"]


def _llm_json(model: str, system: str, prompt: str, max_tokens: int = 600) -> dict[str, Any]:
    from assistant.integrations.openrouter_client import openrouter_chat_completion
    from assistant.lib.llm_json import strip_json_from_markdown

    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    data = openrouter_chat_completion(payload, operation="eval_judge", timeout=180)
    raw = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    try:
        return json.loads(strip_json_from_markdown(raw))
    except (ValueError, TypeError):
        m = re.search(r'"(?:winner|label)"\s*:\s*"(\w+)"', raw)
        return {"winner": m.group(1) if m else "tie", "label": m.group(1) if m else "complex",
                "severity": 0, "reason": raw[:200]}


def cmd_judge(a: argparse.Namespace) -> None:
    install_sandbox()
    cases = {c["id"]: c for c in _jl_read(CASES)}
    runs = {(r["case_id"], r["arm"]): fix_row(r) for r in _jl_read(RUNS) if not r.get("error")}
    done = {(j["case_id"], j["arm"]) for j in _jl_read(JUDGE)}
    lock = threading.Lock()
    jobs: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for (cid, arm), r in runs.items():
        if (cid, arm) not in done and cid in cases:
            jobs.append(("pair", cases[cid], r))
    for cid, c in cases.items():
        if (cid, "label") not in done:
            jobs.append(("label", c, None))
    print(f"[judge] jobs={len(jobs)}", flush=True)

    def work(job: tuple[str, dict[str, Any], dict[str, Any] | None]) -> None:
        what, case, var = job
        bucket: list[dict[str, Any]] = []
        _USAGE.set(bucket)
        if what == "label":
            try:
                v = _llm_json(a.judge_model, LABEL_SYSTEM, _context_block(case, 1500), 300)
            except Exception as e:  # noqa: BLE001
                v = {"label": "complex", "reason": f"error {e!r}"[:200]}
            row = {"case_id": case["id"], "arm": "label", "label": str(v.get("label") or "complex"),
                   "reason": str(v.get("reason") or "")}
        else:
            assert var is not None
            base = base_row(case)
            votes = []
            for order in ("bv", "vb"):  # оба порядка — гасим перекос «первый лучше»
                ra, rb = (base, var) if order == "bv" else (var, base)
                try:
                    v = _llm_json(a.judge_model, JUDGE_SYSTEM, _judge_prompt(case, ra, rb))
                except Exception as e:  # noqa: BLE001
                    v = {"winner": "tie", "severity": 0, "reason": f"error {e!r}"[:200]}
                w = str(v.get("winner") or "tie")
                pick = "tie" if w not in ("A", "B") else ("base" if (w == "A") == (order == "bv") else "variant")
                votes.append({"order": order, "pick": pick, "severity": int(v.get("severity") or 0),
                              "reason": str(v.get("reason") or "")})
            score = sum(1 if v["pick"] == "variant" else -1 if v["pick"] == "base" else 0 for v in votes)
            row = {"case_id": case["id"], "arm": var["arm"], "votes": votes, "score": score}
        row["judge_model"] = a.judge_model
        row["judge_cost"] = round(sum(float(u.get("cost") or 0) for u in bucket), 6)
        _jl_append(JUDGE, row, lock)
        print(f"[judge] {case['id']} {row['arm']} {row.get('score', row.get('label'))}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, jobs))


# ------------------------------------------------------------------ отчёт


def is_worse(j: dict[str, Any]) -> bool:
    """Хуже: оба порядка за образец, или один за него с заметной разницей, а другой — «одинаково»."""
    if j["score"] <= -2:
        return True
    if j["score"] == -1:
        return any(v["pick"] == "base" and v["severity"] >= 2 for v in j["votes"])
    return False


def _cost_per_call(usage: dict[str, Any]) -> dict[str, float]:
    out = {}
    for r in usage.get("ask_user_by_model") or []:
        if r.get("n"):
            out[str(r["model"]).split("/")[-1]] = float(r["cost"] or 0) / int(r["n"])
    return out


def cmd_report(a: argparse.Namespace) -> None:
    from collections import Counter

    install_sandbox()
    cases = {c["id"]: c for c in _jl_read(CASES)}
    runs = {(r["case_id"], r["arm"]): fix_row(r) for r in _jl_read(RUNS) if not r.get("error")}
    judged = _jl_read(JUDGE)
    pairs = {(j["case_id"], j["arm"]): j for j in judged if j["arm"] != "label"}
    labels = {j["case_id"]: j for j in judged if j["arm"] == "label"}
    usage = json.loads((OUT / "usage.json").read_text()) if (OUT / "usage.json").is_file() else {}
    cpc = _cost_per_call(usage)
    ask_user_14d = sum(float(r["cost"] or 0) for r in usage.get("ask_user_by_model") or [])
    rep: dict[str, Any] = {
        "cases": len(cases),
        "run_errors": sum(1 for r in _jl_read(RUNS) if r.get("error")),
        "judge_cost": round(sum(j.get("judge_cost", 0) for j in judged), 3),
        "run_cost": round(sum(r.get("cost", 0) for r in runs.values()), 3),
        "orig_models": Counter(c.get("orig_model") or c["orig_label"] for c in cases.values()),
        "ask_user_spend_14d": round(ask_user_14d, 2),
    }
    routes = {cid: route_auto(c) for cid, c in cases.items()}
    rep["auto_routes"] = Counter(f"{r[0]}: {r[1]}" for r in routes.values())

    def arm_stats(arm: str) -> dict[str, Any] | None:
        js = [(cid, j) for (cid, ar), j in pairs.items() if ar == arm and (cid, arm) in runs]
        if not js:
            return None
        n = len(js)
        worse = [cid for cid, j in js if is_worse(j)]
        kinds_same = [cid for cid, _ in js
                      if base_row(cases[cid])["write_kinds"] == _variant_kinds(runs[(cid, arm)]["tool_calls"])]
        refus = [cid for cid, _ in js if REFUSAL_RE.search(runs[(cid, arm)]["answer"][:300])
                 and not REFUSAL_RE.search(cases[cid]["orig_answer"][:300])]
        lr = sorted(len(runs[(cid, arm)]["answer"]) / max(1, len(cases[cid]["orig_answer"])) for cid, _ in js)
        return {
            "pairs": n, "not_worse": n - len(worse), "not_worse_share": round((n - len(worse)) / n, 3),
            "better": sum(1 for _, j in js if j["score"] >= 1),
            "any_vote_for_orig": sum(1 for _, j in js if j["score"] < 0),
            "actions_same": len(kinds_same),
            "actions_differ": [{"id": cid, "q": cases[cid]["question"][:120],
                                "orig": base_row(cases[cid])["write_kinds"],
                                "variant": _variant_kinds(runs[(cid, arm)]["tool_calls"])}
                               for cid, _ in js if cid not in kinds_same],
            "new_refusals": len(refus),
            "len_ratio_median": round(lr[n // 2], 2),
            "worse": [{"id": cid, "q": cases[cid]["question"][:160], "reason": pairs[(cid, arm)]["votes"][0]["reason"]}
                      for cid in worse],
            "variant_cost": round(sum(runs[(cid, arm)]["cost"] for cid, _ in js), 4),
        }

    arm_names = sorted({ar for (_cid, ar) in pairs})
    for arm in arm_names:
        st = arm_stats(arm)
        if st:
            rep[arm] = st

    # «Авто»: доля и экономия. Оценка стоимости ответа нынешней модели — средняя цена её вызова × число шагов.
    def base_est(cid: str) -> float:
        c = cases[cid]
        per = cpc.get((c.get("orig_model") or "").split("/")[-1], 0.0)
        r = runs.get((cid, "auto")) or runs.get((cid, "probe"))
        steps = r["steps"] if r else (2 if c["orig_actions"] else 1)
        return per * max(1, steps)

    cheap_ids = [cid for cid, r in routes.items() if r[0] == "cheap"]
    total_est = sum(base_est(cid) for cid in cases)
    for arm in arm_names:
        if not (arm == "auto" or arm.startswith("auto:")) or arm not in rep:
            continue
        saved = sum(base_est(cid) - runs[(cid, arm)]["cost"] for cid in cheap_ids if (cid, arm) in runs)
        rep[arm]["cheap_share_questions"] = round(len(cheap_ids) / max(1, len(cases)), 3)
        rep[arm]["saving_share_of_ask"] = round(saved / total_est, 3) if total_est else None
        rep[arm]["saving_usd_month_user"] = round(ask_user_14d * (saved / total_est) * 30 / 14, 2) if total_est else None

    # правило «Авто» против разметки судьи
    conf: Counter = Counter()
    miss = []
    for cid, r in routes.items():
        lab = labels.get(cid, {}).get("label")
        if lab not in ("simple", "complex"):
            continue
        conf[f"{r[0]}/{lab}"] += 1
        if (r[0] == "cheap") != (lab == "simple"):
            miss.append({"id": cid, "route": r[0], "why": r[1], "judge": lab, "q": cases[cid]["question"][:160],
                         "judge_reason": labels[cid].get("reason", "")})
    rep["route_vs_judge"] = {
        "cheap/simple (верно)": conf["cheap/simple"], "cheap/complex (опасно)": conf["cheap/complex"],
        "strong/simple (недосэкономили)": conf["strong/simple"], "strong/complex (верно)": conf["strong/complex"],
        "mismatches": miss,
    }

    if "trim" in rep:
        ids = [cid for (cid, ar) in pairs if ar == "trim" and (cid, "trim") in runs]

        def size(p: dict[str, int]) -> int:
            return p["system_total"] + p["history"] + p.get("doc_view", 0)

        full = sum(size(runs[(cid, "trim")]["parts_full"]) for cid in ids)
        cut = sum(size(runs[(cid, "trim")]["parts"]) for cid in ids)
        rep["trim"]["prompt_chars_cut_share"] = round(1 - cut / max(1, full), 3)
        rep["trim"]["applies_to_share"] = round(sum(1 for c in cases.values() if trim_cut(c) >= 0.2) / max(1, len(cases)), 3)
        rep["trim"]["avg_prompt_tokens_after"] = int(sum(runs[(cid, "trim")]["prompt_tokens"] for cid in ids) / max(1, len(ids)))

    parts: Counter = Counter()
    for c in cases.values():
        parts.update({k: v for k, v in build_request(c, trim=False)[1].items() if k != "history_turns"})
    rep["prompt_parts_avg_chars"] = {k: int(v / max(1, len(cases))) for k, v in parts.most_common()}
    (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1))

    # спорные пары — на ручную оценку: «хуже», голос за образец, разные действия, потом остальные
    pool = []
    for (cid, arm), j in pairs.items():
        if (cid, arm) not in runs:
            continue
        v = runs[(cid, arm)]
        kinds_diff = base_row(cases[cid])["write_kinds"] != _variant_kinds(v["tool_calls"])
        prio = 0 if is_worse(j) else 1 if j["score"] < 0 else 2 if kinds_diff else 3 if j["score"] == 0 else 4
        pool.append((prio, random.Random(cid + arm).random(), cid, arm, j, v))
    pool.sort()
    rnd = random.Random(11)
    items, key = [], {}
    for n, (_p, _r, cid, arm, j, v) in enumerate(pool[: a.review]):
        c = cases[cid]
        b = base_row(c)
        left_is_base = rnd.random() < 0.5
        L, R = (b, v) if left_is_base else (v, b)
        pid = f"p{n + 1}"
        key[pid] = {"case_id": cid, "arm": arm, "A": "orig" if left_is_base else arm,
                    "B": arm if left_is_base else "orig", "judge_score": j["score"], "route": routes[cid][0]}
        items.append({
            "id": pid,
            "channel": "Telegram" if c["origin"] == "telegram" else "Приложение",
            "note_title": c["note_title"] if c["kind"] == "local" else "",
            "note_excerpt": c["note_text"][:1500] if c["kind"] == "local" else "",
            "quote": c.get("quote", "")[:800],
            "history": [{"role": m["role"], "content": m["content"][:600]} for m in c["history"][-4:]],
            "question": c["question"],
            "A": {"answer": L["answer"], "actions": [w["name"] for w in _writes(L)]},
            "B": {"answer": R["answer"], "actions": [w["name"] for w in _writes(R)]},
        })
    (OUT / "review.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    (OUT / "review_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))
    print(f"[report] review items={len(items)} → {OUT / 'review.json'}")


# ------------------------------------------------------------------ main


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("usage")
    u.add_argument("--uid", default="106278723")
    u.add_argument("--days", type=int, default=14)
    u.add_argument("--bg-models", default="gpt-4o")
    e = sub.add_parser("export")
    e.add_argument("--uid", default="106278723")
    e.add_argument("--limit", type=int, default=50)
    e.add_argument("--days", type=int, default=60)
    e.add_argument("--per-item", type=int, default=5)
    r = sub.add_parser("run")
    r.add_argument("--uid", default="106278723")
    r.add_argument("--arms", default="auto,probe,trim")
    r.add_argument("--cheap-model", default=CHEAP_MODEL)
    r.add_argument("--probe", type=int, default=10, help="сколько «сложных» вопросов тоже дать дешёвой модели")
    r.add_argument("--trim-max", type=int, default=20)
    r.add_argument("--only", type=int, default=0)
    r.add_argument("--workers", type=int, default=3)
    j = sub.add_parser("judge")
    j.add_argument("--judge-model", default=JUDGE_MODEL)
    j.add_argument("--workers", type=int, default=3)
    rp = sub.add_parser("report")
    rp.add_argument("--review", type=int, default=26)
    a = p.parse_args()
    {"usage": cmd_usage, "export": cmd_export, "run": cmd_run, "judge": cmd_judge, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
