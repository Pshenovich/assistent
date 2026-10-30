"""Разовый расчёт онбординга для тех, у кого его ещё нет: кто уже пользовался Орбитой и что уже освоил.

Действующим пользователям тур не показываем (ставим все шаги пройденными), а функции, следы которых
есть в данных, считаем освоенными — подсказки к ним не нужны. Каждая проверка независима: сбой одной
хранилки не ломает остальные.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


def _try(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        log.debug("onboarding backfill check failed", exc_info=True)
        return default


def _notes(uid: int) -> list[dict[str, Any]]:
    from assistant.stores import notes

    return notes.list_notes(uid, limit=200)


def _sheet_kinds(note_list: list[dict[str, Any]]) -> set[str]:
    from assistant.stores import note_sheets

    kinds: set[str] = set()
    for n in note_list[:200]:
        for s in note_sheets.list_extra_sheets(int(n["id"])):
            kinds.add(str(s.get("kind") or "doc"))
    return kinds


def _custom_agents(uid: int) -> bool:
    from assistant.stores import user_agents

    return any(str(a.get("kind") or "") == user_agents.CUSTOM_KIND for a in user_agents.list_agents(uid))


def _usage_calls(uid: int) -> int:
    from assistant.lib import usage_store

    return int(usage_store.user_lifetime_stats(str(uid)).get("n_calls") or 0)


def initial_state(user_id: int) -> dict[str, Any]:
    uid = int(user_id)
    used: set[str] = set()

    note_list = _try(lambda: _notes(uid), []) or []
    if note_list:
        used.add("notes")
    kinds = _try(lambda: _sheet_kinds(note_list), set()) or set()
    if "board" in kinds:
        used.add("board")
    if "web" in kinds:
        used.add("presentation")

    def _transcriptions() -> bool:
        from assistant.stores import notes

        return bool(notes.list_transcription_notes(uid, limit=1))

    if _try(_transcriptions, False):
        used.add("transcriptions")

    def _threads() -> bool:
        from assistant.stores import chat_threads

        return bool(chat_threads.list_threads(uid, limit=1))

    has_threads = bool(_try(_threads, False))
    if has_threads:
        used.add("chats")

    def _tasks() -> bool:
        from assistant.stores import calendar_tasks

        return bool(calendar_tasks.list_recent_owner_tasks(uid, "1970-01-01", 1))

    has_tasks = bool(_try(_tasks, False))
    if has_tasks:
        used.add("create_task")

    def _posted() -> bool:
        from assistant.stores import calendar_tasks

        return calendar_tasks.count_active_posted_tasks(uid) > 0

    if _try(_posted, False):
        used.add("posted_tasks")

    def _org() -> bool:
        from assistant.stores import orgs_store

        return orgs_store.current_org(uid) is not None

    if _try(_org, False):
        used.add("org")

    def _google() -> bool:
        from assistant.services import calendar

        return bool(calendar.has_google(uid))

    if _try(_google, False):
        used.add("google")

    def _mail() -> bool:
        from assistant.stores import mail_accounts

        return bool(mail_accounts.list_accounts(uid))

    if _try(_mail, False):
        used.add("mail")

    def _bitrix() -> bool:
        from assistant.stores import trackers

        return bool(trackers.get_credentials(uid, "bitrix24"))

    if _try(_bitrix, False):
        used.add("bitrix")

    if _try(lambda: _custom_agents(uid), False):
        used.add("agents")

    def _bot_onboarded() -> bool:
        from assistant.stores import user_prefs

        return user_prefs.onboarding_completed(uid)

    calls = _try(lambda: _usage_calls(uid), 0) or 0
    existing = bool(note_list or has_threads or has_tasks or calls > 0 or _try(_bot_onboarded, False))

    now = time.time()
    st: dict[str, Any] = {"used": {f: now for f in sorted(used)}, "hint_shown": {}, "snoozed": {}, "screens_seen": {}}
    if existing:
        from assistant.onboarding import screen_ids

        st["intro_done_at"] = st["team_step_done_at"] = st["tour_done_at"] = now
        # Разделы им знакомы — знакомство не показываем, только подсказки к неосвоенному.
        st["screens_seen"] = {s: now for s in sorted(screen_ids())}
        st["backfilled"] = True
    return st
