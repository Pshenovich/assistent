"""Ручной / будущий авто-дайджест выбранных чатов пользователя."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
from assistant.nlu import llm as nlu_llm
from assistant.stores import chat_digest as digest_store
from assistant.stores import user_prefs

_refresh_lock = threading.Lock()
_refreshing: set[int] = set()


def digest_tz() -> ZoneInfo:
    raw = (os.getenv("CHAT_DIGEST_TZ") or "").strip() or "Europe/Moscow"
    try:
        return ZoneInfo(raw)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def default_report_date(*, now: datetime | None = None) -> str:
    """Вчера по TZ дайджеста."""
    tz = digest_tz()
    when = now or datetime.now(tz)
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    else:
        when = when.astimezone(tz)
    return (when.date() - timedelta(days=1)).isoformat()


def digest_today(*, now: datetime | None = None) -> str:
    tz = digest_tz()
    when = now or datetime.now(tz)
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    else:
        when = when.astimezone(tz)
    return when.date().isoformat()


def _next_day(ymd: str) -> str:
    d = datetime.strptime(ymd, "%Y-%m-%d").date()
    return (d + timedelta(days=1)).isoformat()


def _days_span(date_from: str, date_to: str) -> int:
    a = datetime.strptime(date_from, "%Y-%m-%d").date()
    b = datetime.strptime(date_to, "%Y-%m-%d").date()
    return (b - a).days


def max_catchup_days() -> int:
    raw = (os.getenv("CHAT_DIGEST_MAX_CATCHUP_DAYS") or "").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 14
    return max(1, min(n, 60))


def dates_to_analyze_for_chat(chat_id: int, *, end_day: str) -> list[str]:
    """Дни с сообщениями с последнего отчёта (не включая его) до end_day включительно."""
    end = (end_day or "").strip()
    if not end:
        return []
    last = digest_store.latest_report_date(int(chat_id))
    if last:
        start = _next_day(last)
    else:
        start = digest_store.earliest_message_date(int(chat_id)) or end
    if start > end:
        return []
    # Ограничиваем окно снизу, чтобы не разбирать слишком длинную историю.
    span = _days_span(start, end)
    max_days = max_catchup_days()
    if span > max_days:
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        start = (end_d - timedelta(days=max_days)).isoformat()
    return digest_store.message_dates_between(int(chat_id), start, end)


def try_begin_refresh(user_id: int) -> bool:
    uid = int(user_id)
    with _refresh_lock:
        if uid in _refreshing:
            return False
        _refreshing.add(uid)
        return True


def end_refresh(user_id: int) -> None:
    uid = int(user_id)
    with _refresh_lock:
        _refreshing.discard(uid)


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        who = (
            str(m.get("display_name") or "").strip()
            or (f"@{m['username']}" if m.get("username") else "")
            or (f"id:{m['user_id']}" if m.get("user_id") else "unknown")
        )
        ts = str(m.get("ts_utc") or "")
        clock = ts[11:16] if len(ts) >= 16 else ""
        prefix = f"[{clock}] {who}" if clock else who
        text = str(m.get("text") or "").strip()
        if text:
            lines.append(f"{prefix}: {text}")
    return "\n".join(lines)


def _empty_summary(*, note: str = "") -> dict[str, Any]:
    return {
        "brief": note or "За выбранный день в чате не было текстовых сообщений.",
        "decisions": [],
        "next_steps": [],
        "deadlines": [],
        "open_questions": [],
        "context_topics": [],
    }


def demo_summary(chat_title: str, report_date: str) -> dict[str, Any]:
    title = (chat_title or "чат").strip()
    return {
        "brief": (
            f"Демо-отчёт за {report_date} по «{title}»: обсуждали статус задач, "
            "согласовали следующий шаг и зафиксировали открытый вопрос."
        ),
        "decisions": [
            {"text": "Демо: берём текущий план без изменений до пятницы"},
        ],
        "next_steps": [
            {
                "assignee": "не назначен",
                "task": "Подготовить короткий статус к синку",
                "deadline": report_date,
            }
        ],
        "deadlines": [
            {
                "item": "Статус к синку",
                "when": report_date,
                "assignee": "",
            }
        ],
        "open_questions": [
            {"text": "Нужно ли подключать ещё один чат к дайджесту?"},
        ],
        "context_topics": ["демо-данные", "настройка Дайджеста"],
        "is_demo": True,
    }


def run_digest_for_user(
    user_id: int,
    *,
    report_date: str | None = None,
    telegram_username: str | None = None,
) -> dict[str, Any]:
    """Разбирает выбранные чаты. Без date — все дни с последнего отчёта до сегодня."""
    uid = int(user_id)
    if not user_prefs.digest_enabled(uid):
        return {"ok": False, "error": "digest_disabled", "reports_updated": 0}
    selected = user_prefs.digest_chat_ids(uid)
    if not selected:
        return {"ok": False, "error": "no_chats_selected", "reports_updated": 0}

    explicit_day = (report_date or "").strip()
    end_day = explicit_day or digest_today()
    set_openrouter_usage_telegram_user(
        telegram_user_id=uid,
        telegram_username=telegram_username,
    )
    updated = 0
    skipped = 0
    errors: list[str] = []
    days_touched: list[str] = []
    model = nlu_llm.chat_digest_model_name()

    for chat_id in selected:
        chat = digest_store.get_chat(chat_id)
        title = (chat or {}).get("title") or f"Чат {chat_id}"
        if explicit_day:
            days = [explicit_day]
        else:
            days = dates_to_analyze_for_chat(int(chat_id), end_day=end_day)
        if not days:
            skipped += 1
            continue
        for day in days:
            messages = digest_store.messages_for_chat_date(chat_id, day)
            if not messages:
                skipped += 1
                continue
            transcript = _format_transcript(messages)
            try:
                summary = nlu_llm.digest_chat_day(
                    chat_title=str(title),
                    report_date=day,
                    transcript=transcript,
                )
            except Exception as e:
                errors.append(f"{chat_id}/{day}: {e!r}")
                continue
            if not summary:
                errors.append(f"{chat_id}/{day}: empty_llm")
                continue
            digest_store.upsert_report(
                report_date=day,
                chat_id=int(chat_id),
                summary=summary,
                message_count=len(messages),
                model=model,
                is_demo=False,
            )
            updated += 1
            if day not in days_touched:
                days_touched.append(day)

    return {
        "ok": True,
        "report_date": explicit_day or (days_touched[-1] if days_touched else end_day),
        "report_dates": days_touched,
        "reports_updated": updated,
        "skipped_empty": skipped,
        "errors": errors,
        "model": model,
    }


def seed_demo_for_user(user_id: int, *, report_date: str | None = None) -> dict[str, Any]:
    """Демо-отчёты без LLM (для MINIAPP_DEV_MODE)."""
    uid = int(user_id)
    day = (report_date or "").strip() or default_report_date()
    selected = user_prefs.digest_chat_ids(uid)
    if not selected:
        raw = (os.getenv("CHAT_DIGEST_DEMO_CHAT_IDS") or "").strip()
        demo_ids = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                demo_ids.append(int(part))
            except ValueError:
                continue
        if not demo_ids:
            demo_ids = [-1001, -1002]
        digest_store.ensure_demo_chats(uid, demo_ids)
        user_prefs.set_digest_enabled(uid, True)
        user_prefs.set_digest_chat_ids(uid, demo_ids)
        selected = demo_ids
    else:
        user_prefs.set_digest_enabled(uid, True)

    n = 0
    for chat_id in selected:
        chat = digest_store.get_chat(chat_id)
        if chat is None:
            digest_store.upsert_chat(
                int(chat_id), title=f"Чат {chat_id}", chat_type="supergroup"
            )
            digest_store.upsert_member(int(chat_id), uid)
            chat = digest_store.get_chat(chat_id)
        title = (chat or {}).get("title") or f"Чат {chat_id}"
        summary = demo_summary(str(title), day)
        digest_store.upsert_report(
            report_date=day,
            chat_id=int(chat_id),
            summary=summary,
            message_count=0,
            model="demo",
            is_demo=True,
        )
        n += 1
    return {"ok": True, "report_date": day, "reports_updated": n, "is_demo": True}
