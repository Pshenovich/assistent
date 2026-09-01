"""LLM-классификация skill/sub-intent для Telegram-бота."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

import requests

from assistant.lib.llm_json import strip_json_from_markdown
from assistant.integrations.openrouter_client import (
    is_llm_configured,
    openrouter_chat_completion,
)

Skill = Literal[
    "calendar",
    "reminder",
    "todoist_note",
    "note_search",
    "transcribe_search",
    "transcribe",
    "summary_search",
    "summary_latest",
    "summary",
    "zoom_record",
    "journal_qa",
    "knowledge_qa",
    "bitrix",
    "ask",
    "none",
]
RouteSource = Literal["regex", "llm"]


@dataclass(frozen=True)
class IntentContext:
    text: str
    chat_type: str = "private"
    has_reply: bool = False
    has_url: bool = False
    has_attachment: bool = False
    regex_hint: dict[str, str] | None = None


@dataclass
class Route:
    skill: Skill
    sub_intent: str = ""
    body: str = ""
    confidence: float = 1.0
    source: RouteSource = "regex"
    calendar_kind: str | None = None
    bitrix_all_projects: bool = False
    reason: str = ""


def router_enabled() -> bool:
    """По умолчанию вкл.; выключить: INTENT_ROUTER_ENABLED=0."""
    v = os.getenv("INTENT_ROUTER_ENABLED", "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return v in ("1", "true", "yes", "on", "")


def _router_model() -> str:
    return os.getenv("OPENROUTER_MODEL_ROUTER", "").strip() or "google/gemini-2.5-flash"


def _override_confidence() -> float:
    try:
        return float(os.getenv("INTENT_ROUTER_OVERRIDE_CONFIDENCE", "0.82") or "0.82")
    except ValueError:
        return 0.82


def _min_confidence() -> float:
    try:
        return float(os.getenv("INTENT_ROUTER_MIN_CONFIDENCE", "0.65") or "0.65")
    except ValueError:
        return 0.65


INTENT_ROUTER_SYSTEM = """Ты классификатор намерений пользователя Telegram-бота-ассистента.
Верни ТОЛЬКО JSON: skill, sub_intent, confidence, body, reason.

skill (одно значение): calendar | reminder | todoist_note | note_search | transcribe_search | transcribe | summary_search | summary | zoom_record | journal_qa | knowledge_qa | bitrix | ask | none.

Правила (русские формулировки и синонимы; смысл важнее точной орфографии и падежа):
- zoom_record: запись Zoom-встречи по ссылке join (zoom.us/j/…), «запиши встречу» + ссылка, опечатки (запипши) — если есть ссылка на встречу, это запись, не создание
- reminder: напомни, напоминание, будильник, «не забудь», «уведоми в …»
- calendar: встреча/встрече/встречу, созвон, календарь, слоты, «поставь встречу на 12 сегодня», перенеси/передвинь/отмени встречу, «что у меня завтра», опечатки — всё равно calendar если по смыслу про календарь
  - sub_intent для calendar: create | update | delete | free_slots | free (обзор дня/встреч) | zoom | zoom_update | zoom_delete | telemost | telemost_update | telemost_delete | contacts
  - zoom: онлайн-встреча Zoom — «зум», «дай ссылку на zoom» → sub_intent=zoom (мгновенная ссылка)
  - «перенеси зум на 15:00» → sub_intent=zoom_update; «удали зум» → sub_intent=zoom_delete
  - telemost: Yandex Telemost — «телемост», «дай ссылку на telemost» → sub_intent=telemost
  - «перенеси телемост на 15:00» → sub_intent=telemost_update; «удали телемост» → sub_intent=telemost_delete
  - «перенеси встречу про X на 18:00» → skill=calendar, sub_intent=update (календарь, не zoom)
- todoist_note: заметка, запиши в заметки (создание новой)
- note_search: найди/поиск в заметках, «найди заметку про …», «поиск в заметках …»
- transcribe_search: найди/поиск в транскрипциях, «найди транскрипцию про …», «поиск в транскрипциях …»
- transcribe: транскрипция, транскрибируй, расшифруй, сделай транскрайб, выведи текст — расшифровка аудио/видео/файла/ссылки (часто reply или вложение)
- summary_search: явный поиск документа — «найди саммари про …», «поиск в саммари …» (вернуть сам документ)
- journal_qa: вопрос по архиву с ответом своими словами — «о чем договорились на встрече про …», «какие задачи на встрече вчера», «перечисли жалобы клиента», «итоги работы по интеграциям», «что решили по …» (не «найди саммари»)
- knowledge_qa: вопрос по корпоративной базе знаний — «найди в базе …», «в базе знаний …», «по базе …», «должностные регламенты в базе», «инфа про интеграции в базе» (не личный архив заметок/встреч)
- bitrix: Битрикс24 — задачи, сделки, CRM, воронки, стадии, чек-листы, дедлайны, результаты, пользовательские поля; примеры: «создай задачу в битрикс», «найди задачи Ивана», «дай описание задачи по переносу на кордекса», «получить описание этой задачи», «перемести сделку», «добавь пункт чек-листа», «убери дедлайн задачи»
- summary: краткий пересказ, саммари, протокол, выжимка текста (часто с reply или файлом/ссылкой)
- ask: вопрос, «что означает», «объясни», «поясни» (особенно с reply или контекстом)
- none: ничего из перечисленного; confidence низкая

Подсказка: если указан regex_hint в user JSON — почти всегда согласуйся с ним, если нет противоречия в тексте (skill как в regex_hint при равном смысле).

body: краткая «суть» запроса для скилла (без воды); если нечего менять — дословная выжимка исходного текста пользователя."""


def _valid_skill(s: str) -> Skill:
    v = (s or "").strip().lower()
    skills = {
        "calendar",
        "reminder",
        "todoist_note",
        "note_search",
        "transcribe_search",
        "transcribe",
        "summary_search",
        "summary_latest",
        "summary",
        "zoom_record",
        "journal_qa",
        "knowledge_qa",
        "bitrix",
        "ask",
        "none",
    }
    return v if v in skills else "none"  # type: ignore[return-value]


def _parse_llm_route(obj: dict[str, Any], fallback_text: str) -> Route | None:
    if not isinstance(obj, dict):
        return None
    skill = _valid_skill(str(obj.get("skill") or ""))
    if skill == "none":
        return None
    try:
        conf = float(obj.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    sub = str(obj.get("sub_intent") or "").strip().lower()
    body = str(obj.get("body") or obj.get("text") or "").strip() or fallback_text
    cal_kind = None
    if skill == "calendar":
        m = {
            "create": "create",
            "update": "update",
            "delete": "delete",
            "free_slots": "free",
            "free": "free",
        }
        cal_kind = m.get(sub)
        body_low = fallback_text.lower()
        zoom_in_text = "zoom" in body_low or "зум" in body_low
        telemost_in_text = "telemost" in body_low or "телемост" in body_low
        if sub in ("zoom", "instant", "zoom_update", "zoom_delete"):
            cal_kind = "zoom"
        elif sub in ("telemost", "telemost_update", "telemost_delete"):
            cal_kind = "telemost"
        elif sub in ("update", "delete") and zoom_in_text:
            cal_kind = "zoom"
            sub = "zoom_update" if sub == "update" else "zoom_delete"
        elif sub in ("update", "delete") and telemost_in_text:
            cal_kind = "telemost"
            sub = "telemost_update" if sub == "update" else "telemost_delete"
        if sub == "contacts":
            cal_kind = "contacts"
    reason = str(obj.get("reason") or "").strip()
    return Route(
        skill=skill,
        sub_intent=sub,
        body=body,
        confidence=conf,
        source="llm",
        calendar_kind=cal_kind,
        reason=reason,
    )


def log_route_resolution(
    regex: Route | None,
    llm: Route | None,
    chosen: Route | None,
    *,
    reason: str,
) -> None:
    rx = f"{regex.skill}/{regex.sub_intent}" if regex else "—"
    lx = (
        f"{llm.skill}/{llm.sub_intent}@{llm.confidence:.2f}"
        if llm
        else "—"
    )
    ch = (
        f"{chosen.skill}/{chosen.sub_intent} src={chosen.source}"
        if chosen
        else "none"
    )
    print(f"[intent_route] regex={rx} llm={lx} chosen={ch} why={reason}")


def classify_intent(ctx: IntentContext, *, force: bool = False) -> Route | None:
    """force=True — вызов даже при INTENT_ROUTER_ENABLED=0 (мягкий fallback для календаря)."""
    if not is_llm_configured():
        return None
    if not force and not router_enabled():
        return None
    text = (ctx.text or "").strip()
    if len(text) < 2:
        return None
    user_obj = {"text": text, "chat_type": ctx.chat_type, "has_reply": ctx.has_reply, "has_url": ctx.has_url, "has_attachment": ctx.has_attachment}
    if ctx.regex_hint:
        user_obj["regex_hint"] = ctx.regex_hint
    payload = {"model": _router_model(), "messages": [{"role": "system", "content": INTENT_ROUTER_SYSTEM}, {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)}], "temperature": 0}
    try:
        data = openrouter_chat_completion(payload, operation="intent_route", timeout=45)
    except (requests.Timeout, requests.RequestException) as e:
        print(f"[intent_route] llm_error={e!r}")
        return None
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return None
    try:
        obj = json.loads(strip_json_from_markdown(content))
    except json.JSONDecodeError:
        print("[intent_route] json_parse_failed")
        return None
    return _parse_llm_route(obj if isinstance(obj, dict) else {}, text)


def resolve_route(regex: Route | None, llm: Route | None) -> Route | None:
    min_c = _min_confidence()
    if regex is None and llm is None:
        log_route_resolution(regex, llm, None, reason="empty")
        return None
    if regex is not None and regex.skill == "zoom_record":
        log_route_resolution(regex, llm, regex, reason="zoom_record_regex_wins")
        return regex
    if regex is None:
        chosen = llm if llm and llm.confidence >= min_c else None
        log_route_resolution(
            regex,
            llm,
            chosen,
            reason="llm_only" if chosen else "llm_below_min",
        )
        return chosen
    if llm is None:
        log_route_resolution(regex, llm, regex, reason="regex_only")
        return regex
    if regex.skill == llm.skill:
        same_sub = (regex.sub_intent or "") == (llm.sub_intent or "")
        same_cal = (regex.calendar_kind or "") == (llm.calendar_kind or "")
        if same_sub and same_cal:
            merged = regex
            if llm.body and (not merged.body or len(llm.body) > len(merged.body)):
                merged.body = llm.body
            if llm.reason:
                merged.reason = llm.reason
            log_route_resolution(regex, llm, merged, reason="agree")
            return merged
        if llm.confidence >= min_c:
            log_route_resolution(regex, llm, llm, reason="llm_sub_on_conflict")
            return llm
        if llm.confidence >= _override_confidence():
            log_route_resolution(regex, llm, llm, reason="llm_sub_override_high")
            return llm
        log_route_resolution(regex, llm, regex, reason="regex_sub_low_llm")
        return regex
    if llm.confidence >= min_c:
        log_route_resolution(regex, llm, llm, reason="llm_min_on_conflict")
        return llm
    if llm.confidence >= _override_confidence():
        log_route_resolution(regex, llm, llm, reason="llm_override_high")
        return llm
    log_route_resolution(regex, llm, regex, reason="regex_low_llm_conf")
    return regex
