"""Умный поиск задач Bitrix24: MCP title + REST fallback + фильтры."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from assistant.integrations.bitrix24_rest import (
    default_client_group_id,
    resolve_stage_ids,
    search_tasks_by_title,
    stage_title,
)
from assistant.integrations.bitrix_mcp_client import call_tool, call_tool_in_session, mcp_session
from assistant.lib.kb_search import normalize_ru_word, tokenize_query, word_matches_haystack

_STOP_WORDS = {
    "дай",
    "описание",
    "описани",
    "задачи",
    "задачу",
    "задача",
    "задач",
    "получить",
    "получи",
    "покажи",
    "найди",
    "найти",
    "поиск",
    "список",
    "эту",
    "этой",
    "этого",
    "эта",
    "про",
    "по",
    "на",
    "в",
    "и",
    "или",
    "что",
    "какая",
    "какой",
    "какие",
    "мне",
    "нужно",
    "хочу",
    "битрикс",
    "bitrix",
    "bitrix24",
    "статус",
    "статусе",
    "стадия",
    "стадии",
    "клиентские",
    "клиентская",
    "клиентских",
    "клиентск",
    "оценка",
    "оценке",
    "оценку",
    "перечисли",
    "выведи",
    "какие",
    "где",
    "есть",
    "все",
    "всех",
    "мои",
    "моих",
    "мою",
    "внутри",
    "чата",
    "чате",
    "чат",
    "запросу",
    "реквизитов",
    "реквизиты",
}


def is_task_list_request(text: str) -> bool:
    s = " ".join((text or "").strip().lower().split())
    if not s or "задач" not in s:
        return False
    if re.search(r"(описан|детал|подробн)", s):
        return False
    if re.search(r"\b(найди|найти|покажи|список|перечисли|выведи|какие|поиск)\b", s):
        return True
    return bool(re.search(r"\bзадач[аиуеё]?\b", s))


def extract_task_filters(text: str) -> dict[str, Any]:
    s = (text or "").strip().lower()
    status_hint = ""
    group_hint = ""
    if re.search(r"\bоценк", s):
        status_hint = "оценка"
    if re.search(r"\bклиентск", s):
        group_hint = "клиентские задачи"
    if re.search(r"\bпродукт", s):
        group_hint = (group_hint + " продуктовые").strip()
    group_id = default_client_group_id() if group_hint or re.search(r"\bobuchat\b", s) else None
    stage_ids: list[int] = []
    if group_id and (status_hint or group_hint):
        stage_ids = resolve_stage_ids(group_id, status_hint=status_hint, group_hint=group_hint)
    return {
        "status_hint": status_hint,
        "group_hint": group_hint,
        "group_id": group_id,
        "stage_ids": stage_ids,
    }


def _mentions_boostra_llm(text: str) -> bool:
    q = (text or "").lower()
    return ("бустра" in q or "boostra" in q) and ("llm" in q or "ллм" in q)


def extract_search_keywords(text: str) -> list[str]:
    raw = (text or "").strip()
    s = raw.lower()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[а-яёА-ЯЁ]{3,}", raw, flags=re.IGNORECASE)
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        v = (value or "").strip()
        if len(v) < 2:
            return
        key = v.lower()
        if key in _STOP_WORDS or key in seen:
            return
        seen.add(key)
        out.append(v)

    for w in words:
        add(w)

    if "кордекса" in s or "кордекс" in s:
        add("Кордекса")
        add("Кордекс")
    if "boostra" in s or "бустра" in s:
        if _mentions_boostra_llm(raw):
            add("Бустра LLM")
        else:
            add("Бустра LLM")
            add("BoostraGPT")
            add("Бустра")
    if "перенос" in s:
        add("Перенос")
    if "gpt" in s:
        add("GPT")

    return out[:12]


def extract_search_queries(text: str) -> list[str]:
    """Строки для поиска: от более конкретных к общим."""
    raw = (text or "").strip()
    s = raw.lower()
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        q = " ".join((value or "").strip().split())
        key = q.lower()
        if len(key) < 2 or key in seen:
            return
        seen.add(key)
        queries.append(q)

    m_tasks = re.search(
        r"(?:найди|найти|покажи|поиск|выведи|перечисли)\s+(?:мне\s+)?"
        r"(?:в\s+)?задач\w*\s+про\s+(.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m_tasks:
        add((m_tasks.group(1) or "").strip(" .,!?\"'«»"))

    for m in re.finditer(
        r"(?:про|по|о|об|на\s+тему|с\s+названием|задач[аиуеё]?\s+(?:про|по|о|об))\s+"
        r"(.+?)(?:\s+(?:на|со?\s+статус|в\s+стадии|у\s+меня)\b|$)",
        s,
        flags=re.IGNORECASE,
    ):
        phrase = (m.group(1) or "").strip(" .,!?\"'«»")
        if phrase:
            add(phrase)

    keywords = extract_search_keywords(raw)
    if len(keywords) >= 2:
        add(" ".join(keywords[:8]))
    for kw in keywords:
        add(kw)
        stem = normalize_ru_word(kw.lower())
        if len(stem) >= 3 and stem != kw.lower():
            add(stem)

    if re.search(r"\bбот\w*\b", s):
        add("бот")
    if re.search(r"\bоплат\w*\b", s):
        add("оплат")

    if _mentions_boostra_llm(raw):
        add("Бустра LLM")

    if not queries and raw:
        cleaned = re.sub(
            r"^(?:найди|найти|покажи|список|поиск|выведи|перечисли)\s+",
            "",
            s,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"(?:мне\s+)?(?:в\s+)?задач\w*\s*",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^(?:про|по|о|об)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = " ".join(cleaned.split())
        if len(cleaned) >= 3:
            add(cleaned)

    return queries[:20]


def _score_title(title: str, keywords: list[str], user_text: str, queries: list[str]) -> int:
    t = title or ""
    t_low = t.lower()
    q = (user_text or "").lower()
    score = 0

    for query in queries:
        tokens = tokenize_query(query)
        if not tokens:
            continue
        hits = sum(1 for w in tokens if word_matches_haystack(w, t_low))
        score += hits * 12
        if hits == len(tokens) and len(tokens) >= 2:
            score += 28
        elif hits == len(tokens):
            score += 10

    matched_kw = 0
    for kw in keywords:
        if word_matches_haystack(kw, t_low):
            matched_kw += 1
            score += 12
    if keywords and matched_kw == len(keywords) and len(keywords) >= 2:
        score += 20

    if _mentions_boostra_llm(user_text):
        if "бустра llm" in t_low or ("boostra" in t_low and "llm" in t_low):
            score += 30
    if "кордекса" in q and "кордекс" in t_low:
        score += 25
    if "перенос" in q and "перенос" in t_low:
        score += 20
    if "boostra" in q and "boostra" in t_low:
        score += 15
    if "бустра" in q and "бустра" in t_low:
        score += 15
    if "llm" in q and "llm" in t_low:
        score += 10
    if re.search(r"\[бустра llm\]", t_low):
        score += 20
    return score


def _min_match_score(user_text: str) -> int:
    return 4 if is_task_list_request(user_text) else 8


async def _mcp_task_search(
    token: str,
    *,
    title: str = "",
    description: str = "",
    session: Any | None = None,
) -> list[dict[str, Any]]:
    payload: dict[str, str] = {}
    if title.strip():
        payload["title"] = title.strip()
    if description.strip():
        payload["description"] = description.strip()
    if not payload:
        return []
    try:
        if session is not None:
            raw = await call_tool_in_session(session, "search_tasks", payload)
        else:
            raw = await call_tool(token, "search_tasks", payload)
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, RuntimeError):
        return []
    except BaseException:
        return []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return []
    out: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        tid = item.get("taskId")
        name = item.get("title") or ""
        if tid is None:
            continue
        out.append({"taskId": int(tid), "title": str(name), "source": "mcp"})
    return out


async def find_task_candidates(
    token: str,
    user_text: str,
    *,
    session: Any | None = None,
) -> list[dict[str, Any]]:
    keywords = extract_search_keywords(user_text)
    queries = extract_search_queries(user_text)
    filters = extract_task_filters(user_text)
    merged: dict[int, dict[str, Any]] = {}
    min_score = _min_match_score(user_text)

    async def collect(items: list[dict[str, Any]]) -> None:
        for item in items:
            tid = int(item["taskId"])
            prev = merged.get(tid)
            if prev is None:
                merged[tid] = dict(item)
            else:
                prev.update({k: v for k, v in item.items() if v not in (None, "", 0)})
                if item.get("source") == "rest":
                    prev["source"] = "rest"

    gid = filters.get("group_id")
    stage_ids = filters.get("stage_ids") or None

    for query in queries:
        if not query:
            continue
        await collect(await _mcp_task_search(token, title=query, session=session))
        if len(query) >= 3:
            await collect(
                await _mcp_task_search(token, description=query, session=session)
            )
        await collect(
            await asyncio.to_thread(
                search_tasks_by_title,
                query,
                group_id=gid,
                stage_ids=stage_ids,
                limit=50,
            )
        )

    if not merged and gid is not None:
        for query in queries[:5]:
            if not query:
                continue
            await collect(
                await asyncio.to_thread(
                    search_tasks_by_title,
                    query,
                    group_id=None,
                    stage_ids=None,
                    limit=50,
                )
            )

    ranked: list[dict[str, Any]] = []
    for item in merged.values():
        title = item.get("title") or ""
        score = _score_title(title, keywords, user_text, queries)
        if score < min_score:
            continue
        ranked.append({**item, "score": score})
    ranked.sort(key=lambda x: (-int(x.get("score") or 0), -int(x.get("taskId") or 0)))
    return ranked[:25]


def has_clear_winner(candidates: list[dict[str, Any]], user_text: str) -> bool:
    if is_task_list_request(user_text):
        return False
    if not candidates:
        return False
    if len(candidates) == 1:
        return int(candidates[0].get("score") or 0) >= 8
    best = candidates[0]
    second = candidates[1]
    best_score = int(best.get("score") or 0)
    second_score = int(second.get("score") or 0)
    if best_score >= second_score + 10:
        return True
    title_low = str(best.get("title") or "").lower()
    keywords = extract_search_keywords(user_text)
    hits = sum(1 for k in keywords if word_matches_haystack(k, title_low))
    return hits >= 2 and best_score >= 20


async def find_tasks_for_list_query(
    token: str,
    user_text: str,
    *,
    session: Any | None = None,
) -> list[dict[str, Any]]:
    """Все подходящие задачи для запросов «найди задачи …»."""
    return await find_task_candidates(token, user_text, session=session)
