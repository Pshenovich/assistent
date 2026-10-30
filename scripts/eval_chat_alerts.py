#!/usr/bin/env python3
"""Стенд алертов из чатов: «разбор для каждого человека» (A, как на проде) против «один разбор на чат» (B).

Берёт чаты Дайджеста пользователя за последние N дней, режет их на пачки так, как их видит
задача раз в 3 минуты, и прогоняет каждую пачку обоими вариантами на модели прода.
Состояние (уже слали сегодня, топик заглушен) отключено — сравниваются решения модели и проверки кода.

  .venv/bin/python scripts/eval_chat_alerts.py --uid 106278723 --days 14 --out data/eval/chat_alerts.json

Выход: JSON со всеми найденными алертами и сводкой (общие / только A / только B, вызовы, $).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import assistant.config  # noqa: E402,F401  (.env)
from assistant.integrations import openrouter_client as oc  # noqa: E402
from assistant.nlu import llm  # noqa: E402
from assistant.services import chat_alerts as ca  # noqa: E402
from assistant.stores import chat_digest as digest_store  # noqa: E402
from assistant.stores import user_prefs  # noqa: E402

TICK_SEC = 180


def batches(chat_id: int, since: str, max_batches: int) -> list[list[dict]]:
    start = digest_store.max_rowid(chat_id, since_utc=since)
    msgs: list[dict] = []
    while True:
        part = digest_store.messages_after_rowid(chat_id, start, limit=400)
        if not part:
            break
        msgs += part
        start = int(part[-1]["rid"])
    out: list[list[dict]] = []
    cur: list[dict] = []
    window_end: float | None = None
    for m in msgs:
        try:
            ts = datetime.fromisoformat(str(m.get("ts_utc")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if window_end is None or ts > window_end:
            if cur:
                out.append(cur)
            cur = []
            window_end = ts + TICK_SEC
        cur.append(m)
    if cur:
        out.append(cur)
    return out[-max_batches:]


def run_variant(uid: int, chat_id: int, new: list[dict], shared: bool, cost: dict) -> list[dict]:
    if not any(m.get("user_id") is not None and int(m["user_id"]) != uid and not ca._is_auto(m) for m in new):
        return []
    chat = digest_store.get_chat(chat_id) or {}
    payload = {
        "chat": str(chat.get("title") or f"Чат {chat_id}"),
        "now_utc": str(new[-1].get("ts_utc") or ""),
        "messages": ca.build_transcript(None if shared else uid, chat_id, new),
    }
    seen = {}
    orig = oc.openrouter_chat_completion

    def wrap(p, **kw):
        d = orig(p, **kw)
        seen["usd"] = float((d.get("usage") or {}).get("cost") or 0)
        return d

    llm.openrouter_chat_completion = wrap
    try:
        raw = llm._chat(
            ca.SYSTEM_SHARED if shared else ca.SYSTEM,
            json.dumps(payload, ensure_ascii=False),
            operation="eval_chat_alerts",
            model=llm._model_chat_digest(),
            timeout=60,
            temperature=0.0,
            max_tokens=600,
        )
    finally:
        llm.openrouter_chat_completion = orig
    cost["calls"] += 1
    cost["usd"] += seen.get("usd", 0.0)
    return ca.verify(uid, chat_id, new, ca.parse_alerts(raw))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", type=int, required=True)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max-batches", type=int, default=150, help="на чат, самые свежие")
    ap.add_argument("--out", default="data/eval/chat_alerts.json")
    a = ap.parse_args()

    # Состояние прода не мешает сравнению.
    ca._sent_today = lambda uid, now: 0
    ca._topic_blocked = lambda uid, chat_id, tid, now: False

    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).isoformat()
    res = {"A": [], "B": []}
    cost = {"A": {"calls": 0, "usd": 0.0}, "B": {"calls": 0, "usd": 0.0}}
    t0 = time.time()
    with oc.usage_user(a.uid):
        for chat_id in user_prefs.digest_chat_ids(a.uid):
            title = str((digest_store.get_chat(int(chat_id)) or {}).get("title") or chat_id)
            bs = batches(int(chat_id), since, a.max_batches)
            print(f"[eval] chat={title!r} batches={len(bs)}", flush=True)
            for new in bs:
                for key, shared in (("A", False), ("B", True)):
                    try:
                        found = run_variant(a.uid, int(chat_id), new, shared, cost[key])
                    except Exception as e:
                        print(f"[eval] {key} chat={chat_id} err={e!r}", flush=True)
                        continue
                    for x in found:
                        x["chat_title"] = title
                        x["batch_last_ts"] = str(new[-1].get("ts_utc") or "")
                        x["text"] = next((str(m.get("text") or "")[:400] for m in new
                                          if int(m["message_id"]) == x["message_id"]), "")
                        res[key].append(x)
    key = lambda x: (x["chat_id"], x["message_id"])  # noqa: E731
    a_ids, b_ids = {key(x) for x in res["A"]}, {key(x) for x in res["B"]}
    summary = {
        "both": len(a_ids & b_ids),
        "only_A": len(a_ids - b_ids),
        "only_B": len(b_ids - a_ids),
        "cost": {k: {"calls": v["calls"], "usd": round(v["usd"], 4)} for k, v in cost.items()},
        "minutes": round((time.time() - t0) / 60, 1),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, **res}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
