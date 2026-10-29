"""Календарь для ИИ в обсуждениях мини-приложения (tool calling).

Чтение (встречи, слоты, занятость, статистика) — сразу; создание и перенос встреч,
задачи и напоминания — сразу, с записью в журнал (кнопка «Отменить» в ответе);
удаление — только предложение, пользователь подтверждает кнопкой.

Всегда работает с календарём спрашивающего (uid из авторизации), а не владельца заметки.
Тяжёлые зависимости usage_server (синхронизация задач с Google, уведомления) передаются
через GptCalendarContext — модуль не импортирует usage_server.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Optional

MAX_RANGE_DAYS = 62
MAX_EVENTS_OUT = 120
MAX_CARDS = 20


# ---------------------------------------------------------------- журнал действий

def _conn() -> sqlite3.Connection:
    from assistant.stores import notes as notes_store

    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gpt_calendar_actions (
            id TEXT PRIMARY KEY,
            owner_uid INTEGER NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            before_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def _lock():
    from assistant.stores import notes as notes_store

    return notes_store._LOCK  # type: ignore[attr-defined]


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def record_action(
    owner_uid: int,
    kind: str,
    status: str,
    *,
    title: str,
    payload: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    aid = uuid.uuid4().hex[:16]
    now = _now_iso()
    with _lock():
        conn = _conn()
        conn.execute(
            "INSERT INTO gpt_calendar_actions (id, owner_uid, kind, status, title, payload_json, before_json, result_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                aid,
                int(owner_uid),
                kind,
                status,
                title[:300],
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                json.dumps(before or {}, ensure_ascii=False, default=str),
                json.dumps(result or {}, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )
        conn.commit()
    return aid


def get_action(action_id: str) -> dict[str, Any] | None:
    with _lock():
        row = _conn().execute(
            "SELECT * FROM gpt_calendar_actions WHERE id = ?", (str(action_id or ""),)
        ).fetchone()
    if not row:
        return None
    keys = [
        "id", "owner_uid", "kind", "status", "title",
        "payload_json", "before_json", "result_json", "created_at", "updated_at",
    ]
    d = dict(zip(keys, tuple(row)))
    for k in ("payload_json", "before_json", "result_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "{}")
        except (TypeError, ValueError):
            d[k[:-5]] = {}
    return d


def set_action_status(action_id: str, status: str, *, result: dict[str, Any] | None = None) -> None:
    with _lock():
        conn = _conn()
        if result is None:
            conn.execute(
                "UPDATE gpt_calendar_actions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now_iso(), action_id),
            )
        else:
            conn.execute(
                "UPDATE gpt_calendar_actions SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(result, ensure_ascii=False, default=str), _now_iso(), action_id),
            )
        conn.commit()


def public_action(a: dict[str, Any], *, viewer_uid: int | None = None) -> dict[str, Any]:
    """Карточка для клиента — без снимков «до»."""
    from assistant.lib.webapp_public import webapp_event_url

    res = a.get("result") or {}
    p = a.get("payload") or {}
    kind = a["kind"]
    open_ref: dict[str, Any] = {}
    if kind in ("show_event", "show_task", "show_reminder"):
        open_ref = {"type": kind[5:], "id": p.get("target_id"), "date": p.get("date"), "calendar_id": p.get("calendar_id")}
    elif res.get("event_id") and a.get("status") == "done":
        open_ref = {"type": "event", "id": res.get("event_id"), "date": res.get("date"), "calendar_id": res.get("calendar_id")}
    elif res.get("task_id") and a.get("status") == "done":
        open_ref = {"type": "task", "id": res.get("task_id")}
    link = ""
    ev_id = str((open_ref or {}).get("id") or res.get("event_id") or "").strip()
    if (open_ref or {}).get("type") == "event" and ev_id:
        link = webapp_event_url(
            ev_id,
            calendar_id=str((open_ref or {}).get("calendar_id") or res.get("calendar_id") or "primary"),
            day=str((open_ref or {}).get("date") or res.get("date") or "")[:10] or None,
        )
    out = {
        "id": a["id"],
        "kind": kind,
        "status": a["status"],
        "title": a.get("title") or "",
        "when": res.get("when") or p.get("when") or "",
        "detail": res.get("detail") or p.get("detail") or "",
        "link": link,
        "open": open_ref or None,
        "mine": viewer_uid is not None and int(a.get("owner_uid") or 0) == int(viewer_uid),
    }
    if kind == "focus_item":
        # Карточка разбора пункта Фокуса: живое состояние пунктов, а не снимок на момент ответа.
        from assistant.services import focus_items, meeting_share

        rows = [focus_items.get(a.get("owner_uid"), k) for k in p.get("keys") or []]
        out["focus"] = [focus_items.public(r) for r in rows if r]
        try:  # задачи встречи, которые уже поставил или выполнил кто-то из участников
            out["focus"] = meeting_share.overlay(int(a.get("owner_uid")), out["focus"])
        except Exception as e:
            print(f"[focus] shared overlay failed action={a.get('id')} err={e!r}")
        out["card_key"] = p.get("card_key") or ""
    if kind in ("email_thread", "email_draft"):
        from assistant.services import mail_reply

        try:
            out.update(mail_reply.public_extra(a))
        except Exception as e:
            print(f"[mail] public action={a.get('id')} err={e!r}")
    return out


# ---------------------------------------------------------------- контекст

@dataclass
class GptCalendarContext:
    uid: int
    telegram_username: str | None = None
    display_name: str = ""
    sync_task_google: Optional[Callable[[int, dict, str], Any]] = None
    notify_task_assignee: Optional[Callable[..., Any]] = None
    actions: list[str] = field(default_factory=list)
    created_events: list[dict[str, Any]] = field(default_factory=list)
    updated_events: list[dict[str, Any]] = field(default_factory=list)
    # Что модель смотрела — для автоматических карточек, если она сама их не показала.
    seen_items: list[dict[str, Any]] = field(default_factory=list)
    slot_offers: list[dict[str, Any]] = field(default_factory=list)
    cards_shown: bool = False
    slots_offered: bool = False
    wrote: bool = False


_CAL_INTENT_RE = None


def looks_like_calendar_question(text: str) -> bool:
    """Вопрос про встречи/задачи/напоминания/время — тогда первый шаг модели обязан вызвать инструмент."""
    import re

    global _CAL_INTENT_RE
    if _CAL_INTENT_RE is None:
        _CAL_INTENT_RE = re.compile(
            r"(?i)(встреч|созвон|календар|задач|напомин|слот|свобод|занят|расписан|планёрк|планерк|"
            r"\bмитинг|сегодня|завтра|послезавтра|понедельник|вторник|сред[аеуы]\b|четверг|пятниц|суббот|"
            r"воскресен|на неделе|недел[юи]|в \d{1,2}[:.]\d{2}|во сколько|когда у меня|с кем|сколько времени|"
            r"запланир|перенеси встреч|что решили|договорил|обещал|итоги встреч|\b1:1\b|транскрипц)"
        )
    return bool(_CAL_INTENT_RE.search(str(text or "")))


def has_google_calendar(uid: int) -> bool:
    try:
        from assistant.integrations.google_calendar_oauth import user_token_path

        return user_token_path(int(uid)).is_file()
    except Exception:
        return False


def _tz(uid: int):
    from assistant.services.calendar import _tz_for

    return _tz_for(int(uid))


def now_prompt_line(uid: int) -> str:
    tz = _tz(uid)
    now = datetime.now(tz)
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    short = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    ahead = ", ".join(
        f"{short[d.weekday()]} {d.strftime('%Y-%m-%d')}"
        for d in (now.date() + timedelta(days=i) for i in range(0, 14))
    )
    acc = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
    nearest = "; ".join(
        f"«в {acc[d.weekday()]}» = {d.strftime('%Y-%m-%d')}"
        for d in (now.date() + timedelta(days=i) for i in range(1, 8))
    )
    return (
        f"Сейчас {now.strftime('%Y-%m-%d %H:%M')} ({days[now.weekday()]}), "
        f"часовой пояс пользователя {getattr(tz, 'key', str(tz))}.\n"
        f"Ближайшие дни недели (бери отсюда, не считай сам): {nearest}. «Сегодня» = {now.strftime('%Y-%m-%d')}, "
        f"«завтра» = {(now.date() + timedelta(days=1)).strftime('%Y-%m-%d')}. "
        f"«Следующий/на следующей неделе» — на 7 дней позже ближайшего.\n"
        f"Даты на две недели вперёд: {ahead}."
    )


SYSTEM_ADDON = """У тебя есть доступ к календарю пользователя через инструменты (calendar_*, task_create, reminder_create, contacts_find, propose_delete).
- На вопросы про встречи, задачи, напоминания, занятость, свободное время, «с кем», «на что уходит время» — сначала посмотри данные инструментами, не выдумывай.
- Встречу/перенос/задачу/напоминание создавай сразу инструментом, если просьба понятна (время, участники). Если инструмент вернул конфликт или «контакт не найден» — не повторяй вслепую: предложи пользователю варианты времени из ответа или попроси уточнить.
- Повтор (каждый день / по будням / еженедельно / до даты): один вызов с recurrence (freq + byday/until). НЕ создавай серию отдельными вызовами — иначе пользователь не сможет удалить всё сразу.
- Действия из прошлых сообщений уже выполнены (в истории они помечены «Уже выполнено»). Не повторяй их: создавай/меняй только то, о чём просит последнее сообщение. На вопросы «какие у меня…» ничего не создавай — смотри calendar_events.
- Удаление никогда не выполняй сам: вызови propose_delete — пользователь подтвердит кнопкой. id бери из calendar_events/reminders_list; если не знаешь id — передай title (название), найду сам.
- id встреч для изменения бери из calendar_events, не придумывай.
- Участников указывай именами из контактов; если не уверен — contacts_find.
- У встреч бывают материалы (транскрипции с саммари, заметки) — в calendar_events это поле materials. Что было/решили на встрече — meeting_materials; что решали с человеком, что он обещал/не сделал, подготовка к встрече с ним — person_history (там и задачи, поставленные ему, — tasks_given). Отвечай по материалам, называй встречу и дату.
- Вопрос по содержанию прошлых обсуждений без известной встречи/даты («что решили по ценам?», «где обсуждали интеграцию?») — memory_search; отвечай по найденным фрагментам и называй источник с датой. Ничего не нашлось — так и скажи.
- Вопрос про продукт, проект, клиента, сервис или термин пользователя («для чего нужен <название>?», «что у нас по <теме>?», «как работает <наш продукт>?») — сначала memory_search: у пользователя почти наверняка есть заметка или документ об этом. Если нашлось — отвечай по найденному тексту (можно своими словами, но смысл — из заметки), назови заметку и дай её ссылку (поле url). Общими знаниями «как обычно бывает» заменять найденное нельзя; общее — только если в материалах ничего нет, и скажи об этом прямо.
- «Найди / открой / дай ссылку на заметку <название>» — notes_find: в ответе название и ссылка (url) каждой найденной заметки. Ссылку бери только из результата инструмента, не придумывай.
- Обязательства и хвосты («что я обещал?», «что мне должны?», «что просрочено после встреч?») — commitments; у каждого пункта источник (встреча/заметка/чат) и цитата — называй источник.
- Задачи за любое время («ставил X задачу про …, что с ней?», «чем закончилось …») — tasks_search (тема и/или исполнитель; calendar_events видит только окно дат). В ответе — статус, когда поставлена и срок, результат (поле result), если он есть, и что договорились в переписке (discussion); покажи найденное через show_calendar_cards.
- Когда перечисляешь встречи/задачи/напоминания — вызови show_calendar_cards с id ВСЕХ перечисляемых (до 20): под ответом появятся карточки, которые можно открыть. Текстом дай короткий итог, без длинного списка.
- Если в контексте уже есть id задач («task id N», например из карточки Фокуса) — передай их все в show_calendar_cards, не ищи задачи заново и не ограничивайся одной.
- Когда подбираешь время встречи — найди окна (calendar_free_slots) и вызови offer_slots с 3–6 лучшими вариантами (и участниками): пользователь выберет карточку, встреча создастся сама.
- Карточка Фокуса в истории («пункт Фокуса … (focus key: …)») — пользователь разбирает этот пункт. Правки («исполнитель не Иван, а Дима», «срок пятница», «это решение», «напомни завтра», «уже сделано», ссылка на задачу) применяй инструментом focus_item_update с этим key — не создавай новую задачу и не переспрашивай.
- После действий коротко напиши, что сделал (время в формате «чт 16 окт, 15:00–15:30»). Кнопки «Отменить»/«Удалить» пользователь увидит сам под ответом."""


# ---------------------------------------------------------------- схемы инструментов

def _fn(name: str, desc: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


_DATE = {"type": "string", "description": "Дата YYYY-MM-DD"}
_DT = {"type": "string", "description": "Дата и время начала YYYY-MM-DDTHH:MM (локальное время пользователя)"}
_PEOPLE = {"type": "array", "items": {"type": "string"}, "description": "Имена или @ники из контактов"}
_RECURRENCE = {
    "type": "object",
    "description": (
        "Повтор одной серией (не отдельными вызовами). "
        "Пример: по будням до даты — {freq: weekly, byday: weekdays, until: YYYY-MM-DD}."
    ),
    "properties": {
        "freq": {
            "type": "string",
            "enum": ["daily", "weekly", "monthly"],
            "description": "daily / weekly / monthly",
        },
        "interval": {"type": "integer", "description": "Каждые N периодов, по умолчанию 1"},
        "byday": {
            "description": "Дни: weekdays / будни, или MO,TU,WE,TH,FR / пн,вт,…",
            "type": "string",
        },
        "until": {**_DATE, "description": "Повторять до этой даты включительно"},
        "count": {"type": "integer", "description": "Число повторов (если нет until)"},
        "rrule": {"type": "string", "description": "Готовый RRULE (если удобнее)"},
    },
}

TOOLS: list[dict[str, Any]] = [
    _fn("calendar_events", "Встречи и задачи пользователя за период (включительно).",
        {"start_date": _DATE, "end_date": _DATE, "query": {"type": "string", "description": "Фильтр по названию (необязательно)"}},
        ["start_date", "end_date"]),
    _fn("calendar_free_slots", "Свободные окна в рабочий день; с участниками — общие свободные окна.",
        {"date": _DATE, "duration_min": {"type": "integer", "default": 30}, "with_people": _PEOPLE}, ["date"]),
    _fn("calendar_availability", "Занятость пользователя и людей из контактов в день (без названий чужих встреч).",
        {"date": _DATE, "people": _PEOPLE}, ["date", "people"]),
    _fn("calendar_time_stats", "Статистика времени во встречах за период: часы, по людям, по названиям, по дням недели.",
        {"start_date": _DATE, "end_date": _DATE}, ["start_date", "end_date"]),
    _fn("contacts_find", "Найти контакты пользователя по имени/нику (пусто — все).",
        {"query": {"type": "string"}}),
    _fn("reminders_list", "Предстоящие напоминания пользователя.", {}),
    _fn("calendar_create_event", "Создать встречу в Google Календаре (сразу). Проверяет конфликты. Для серии — один вызов с recurrence.",
        {"title": {"type": "string"}, "start": _DT, "duration_min": {"type": "integer", "default": 30},
         "attendees": _PEOPLE, "description": {"type": "string"}, "location": {"type": "string"},
         "online": {"type": "string", "enum": ["auto", "zoom", "telemost", "none"], "default": "auto"},
         "force": {"type": "boolean", "description": "true — создать, даже если время занято (только если пользователь явно согласился)"},
         "recurrence": _RECURRENCE},
        ["title", "start"]),
    _fn("calendar_update_event", "Изменить встречу (перенос, название, участники) — сразу.",
        {"event_id": {"type": "string"}, "calendar_id": {"type": "string"}, "start": _DT,
         "duration_min": {"type": "integer"}, "title": {"type": "string"},
         "attendees": {**_PEOPLE, "description": "Полный новый список участников (заменяет)"},
         "add_attendees": {**_PEOPLE, "description": "Добавить к текущим"},
         "description": {"type": "string"}, "location": {"type": "string"}},
        ["event_id"]),
    _fn("task_create", "Поставить задачу (себе или контакту) на дату/время — сразу. Серию — с recurrence одним вызовом.",
        {"title": {"type": "string"}, "start": _DT, "duration_min": {"type": "integer", "default": 30},
         "all_day": {"type": "boolean"}, "assignee": {"type": "string", "description": "Имя/ник из контактов; пусто — себе"},
         "description": {"type": "string"}, "recurrence": _RECURRENCE},
        ["title", "start"]),
    _fn("reminder_create", "Создать напоминание в Telegram — сразу. Серию — с recurrence одним вызовом.",
        {"text": {"type": "string"}, "when": _DT, "recurrence": _RECURRENCE}, ["text", "when"]),
    _fn("show_calendar_cards", "Показать пользователю карточки встреч/задач/напоминаний под ответом (их можно открыть). Вызывай, когда перечисляешь их.",
        {"items": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["event", "task", "reminder"]},
            "id": {"type": "string"}, "calendar_id": {"type": "string"}}, "required": ["type", "id"]}}},
        ["items"]),
    _fn("offer_slots", "Показать варианты времени встречи карточками — пользователь выберет и встреча создастся. Вызывай, когда предлагаешь время.",
        {"slots": {"type": "array", "items": _DT, "description": "3–6 вариантов начала"},
         "duration_min": {"type": "integer", "default": 30}, "attendees": _PEOPLE,
         "title": {"type": "string", "description": "Название будущей встречи"}},
        ["slots"]),
    _fn("meeting_materials", "Материалы встречи: привязанные транскрипции (саммари, задачи, решения) и заметки. Вызывай, когда спрашивают, что было/решили на встрече.",
        {"event_id": {"type": "string"}, "calendar_id": {"type": "string"},
         "title": {"type": "string", "description": "Название встречи, если id неизвестен"},
         "date": {**_DATE, "description": "Дата встречи (вместе с title)"}}),
    _fn("person_history", "История с человеком: встречи с ним за период и их материалы (саммари, задачи, решения), заметки с его участием. Для вопросов «что решили с X», «что X не сделал», «подготовь 1:1 с X».",
        {"person": {"type": "string", "description": "Имя или @ник из контактов"},
         "start_date": {**_DATE, "description": "По умолчанию — 60 дней назад"}, "end_date": _DATE},
        ["person"]),
    _fn("tasks_search", "Поиск задач Leo за всё время (поставленные пользователем другим, его собственные и поставленные ему): по теме и/или исполнителю. Возвращает статус, описание, результат, чек-лист и последние сообщения переписки с исполнителем (discussion). Для вопросов «ставил X задачу про …, что с ней», «какие задачи у X», «чем закончилась задача …».",
        {"query": {"type": "string", "description": "Ключевые слова темы (например «документы клиент»); пусто — все задачи исполнителя"},
         "assignee": {"type": "string", "description": "Имя/ник исполнителя или постановщика (необязательно)"},
         "status": {"type": "string", "enum": ["any", "open", "done"], "default": "any"}}),
    _fn("meeting_tasks_search", "Задачи из встреч пользователя (таблица «Задачи» в саммари, в т.ч. встреч, которыми с ним поделились) за последние недели: статус пункта (Не разобрано / Взял / Поручено · ждёт / Принято / В работе / Вопрос / Отказ / В трекере / Сделано / Не задача), ответственный, срок, встреча. Для вопросов «какой статус по задаче …», «кто взял …», «что не разобрано после встречи …». Нашлось несколько похожих — уточни у пользователя, о какой речь.",
        {"query": {"type": "string", "description": "Примерное название задачи или слова из неё; пусто — все пункты"},
         "meeting": {"type": "string", "description": "Слова из названия встречи (необязательно)"},
         "days": {"type": "integer", "description": "За сколько дней искать встречи, по умолчанию 42"}}),
    _fn("commitments", "Реестр обязательств из встреч, заметок и чатов (одна договорённость — один пункт, даже если звучала в нескольких местах). Для вопросов «что я обещал», «что мне должны / что за ними», «что просрочено», «какие хвосты после встреч».",
        {"direction": {"type": "string", "enum": ["any", "mine", "theirs", "unknown"], "default": "any",
                       "description": "mine — за пользователем; theirs — за другими; unknown — исполнитель не ясен"},
         "status": {"type": "string", "enum": ["open", "overdue", "closed", "snoozed", "any"], "default": "open"},
         "who": {"type": "string", "description": "Фильтр по человеку (необязательно)"}}),
    _fn("memory_search", "Поиск по смыслу во всех материалах пользователя: саммари и транскрипты встреч, заметки и все их листы, документы Базы знаний, задачи с перепиской исполнителя и итогом, дайджесты чатов, переписка. Для вопросов «что решили по …», «где обсуждали …», «что говорил X про …», а также про продукты, проекты, клиентов и термины пользователя («для чего нужен …», «как у нас устроено …»). У найденных заметок есть url.",
        {"query": {"type": "string", "description": "Суть вопроса своими словами (тема, о чём речь)"}},
        ["query"]),
    _fn("notes_find", "Найти заметки, транскрипции и документы Базы знаний пользователя по названию (и словам из текста): id, название, дата правки и ссылка url для открытия. Для «найди заметку …», «дай ссылку на заметку …», «где моя заметка про …».",
        {"query": {"type": "string", "description": "Название заметки или слова из него"}},
        ["query"]),
    _fn("focus_item_update", "Разбор пункта Фокуса (инсайта): отметить выполненным, связать со ссылкой, отложить, скрыть, сменить тип, поправить поля. key — из истории (focus key).",
        {"key": {"type": "string", "description": "focus key пункта; если не знаешь — передай text"},
         "text": {"type": "string", "description": "Текст пункта, если key неизвестен"},
         "op": {"type": "string", "enum": ["update", "done", "link", "snooze", "dismiss", "retype", "undo"]},
         "assignee": {"type": "string"}, "task": {"type": "string", "description": "Новая формулировка задачи"},
         "deadline": _DATE, "comment": {"type": "string"},
         "url": {"type": "string", "description": "Ссылка на внешнюю задачу (op=link)"},
         "until": {"type": "string", "description": "До когда отложить / когда напомнить: YYYY-MM-DDTHH:MM (op=snooze или retype=reminder)"},
         "reason": {"type": "string", "enum": ["not_a_task", "not_relevant", "duplicate", "recognition_error"]},
         "type": {"type": "string", "enum": ["task", "promise", "decision", "reminder", "thought"]}},
        ["op"]),
    _fn("propose_delete", "Предложить удалить встречу/задачу/напоминание. НЕ удаляет — пользователь подтвердит кнопкой.",
        {"kind": {"type": "string", "enum": ["event", "task", "reminder"]}, "id": {"type": "string"},
         "title": {"type": "string", "description": "Название, если id неизвестен"},
         "calendar_id": {"type": "string"}},
        ["kind"]),
]


# ---------------------------------------------------------------- хелперы

def _parse_day(s: Any) -> date:
    return date.fromisoformat(str(s or "").strip()[:10])


def _window(uid: int, start_date: Any, end_date: Any) -> tuple[datetime, datetime]:
    tz = _tz(uid)
    d0 = _parse_day(start_date)
    d1 = _parse_day(end_date or start_date)
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days > MAX_RANGE_DAYS:
        d1 = d0 + timedelta(days=MAX_RANGE_DAYS)
    return datetime.combine(d0, time.min, tzinfo=tz), datetime.combine(d1 + timedelta(days=1), time.min, tzinfo=tz)


def _fmt_dt(dt: datetime | None, uid: int) -> str:
    if not isinstance(dt, datetime):
        return ""
    return dt.astimezone(_tz(uid)).strftime("%Y-%m-%d %H:%M")


_WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _iso_local(uid: int, raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = _tz(uid)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def human_when(start: datetime | None, end: datetime | None, uid: int) -> str:
    if not isinstance(start, datetime):
        return ""
    tz = _tz(uid)
    s = start.astimezone(tz)
    out = f"{_WD[s.weekday()]} {s.day} {_MON[s.month - 1]}, {s.strftime('%H:%M')}"
    if isinstance(end, datetime):
        out += "–" + end.astimezone(tz).strftime("%H:%M")
    return out


def _contacts(ctx: GptCalendarContext) -> list[dict[str, Any]]:
    try:
        from assistant.stores import contacts_store

        return list(
            contacts_store.load_contacts(
                telegram_user_id=int(ctx.uid), telegram_username=ctx.telegram_username
            )
            or []
        )
    except Exception as e:
        print(f"[gpt_calendar] contacts_load uid={ctx.uid} err={e!r}")
        return []


def _email_names(ctx: GptCalendarContext) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in _contacts(ctx):
        em = str(c.get("email") or "").strip().lower()
        if em:
            out[em] = str(c.get("name") or em)
    return out


def _leo_email_for_contact(ctx: GptCalendarContext, name: str) -> str:
    """Контакт без почты, но человек в Leo с Google Календарём — берём почту его аккаунта."""
    try:
        from assistant.lib.calendar_attendees import find_contact_by_name
        from assistant.stores import telegram_registry

        c = find_contact_by_name(ctx.uid, name, telegram_username=ctx.telegram_username)
        if not c:
            return ""
        uid = int(c.get("telegram_user_id") or 0) or telegram_registry.lookup_user_id(
            str(c.get("telegram_username") or "")
        )
        if not uid or not has_google_calendar(int(uid)):
            return ""
        from assistant.lib.calendar_user_lookup import account_email_for_user

        return str(account_email_for_user(int(uid)) or "").strip().lower()
    except Exception as e:
        print(f"[gpt_calendar] leo_email name={name!r} err={e!r}")
        return ""


def _resolve_people(ctx: GptCalendarContext, names: list[str]) -> tuple[list[str], list[str]]:
    from assistant.lib.calendar_attendees import resolve_attendee_names

    clean = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not clean:
        return [], []
    att, missing = resolve_attendee_names(ctx.uid, clean, telegram_username=ctx.telegram_username)
    emails = [a["email"] for a in att if a.get("email")]
    still: list[str] = []
    for n in missing:
        em = _leo_email_for_contact(ctx, n)
        if em and em not in emails:
            emails.append(em)
        elif not em:
            still.append(n)
    return emails, still


def _parse_start(ctx: GptCalendarContext, raw: Any) -> datetime:
    s = str(raw or "").strip().replace(" ", "T")
    dt = datetime.fromisoformat(s)
    tz = _tz(ctx.uid)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _ok(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= 24000 else text[:24000] + "…(обрезано)"


# ---------------------------------------------------------------- чтение

def _events_in_window(ctx: GptCalendarContext, t0: datetime, t1: datetime, query: str = "") -> list[dict[str, Any]]:
    """Встречи в окне: Google (если подключён) + встречи Орбиты — в одном виде."""
    from assistant.services import calendar_sources, orbita_calendar

    out: list[dict[str, Any]] = []
    if has_google_calendar(ctx.uid):
        out = list(calendar_sources.list_events_in_window(ctx.uid, t0, t1, query=query or None))
    q = str(query or "").strip().lower()
    try:
        for ev in orbita_calendar.events_for(ctx.uid, t0, t1):
            hay = " ".join([str(ev.get("summary") or ""), str(ev.get("description") or "")]
                           + [str(a.get("email") or "") for a in ev.get("attendees") or []]).lower()
            if q and q not in hay:
                continue
            out.append({
                "event_id": ev["id"],
                "calendar_id": ev["_calendarId"],
                "summary": ev.get("summary") or "",
                "start": datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(_tz(ctx.uid)),
                "end": datetime.fromisoformat(ev["end"]["dateTime"]).astimezone(_tz(ctx.uid)),
                "raw": ev,
            })
    except Exception as e:
        print(f"[gpt_calendar] orbita events err={e!r}")
    out.sort(key=lambda e: e["start"] if isinstance(e.get("start"), datetime) else datetime.max.replace(tzinfo=_tz(ctx.uid)))
    return out


def tool_calendar_events(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    t0, t1 = _window(ctx.uid, args.get("start_date"), args.get("end_date"))
    names = _email_names(ctx)
    out: list[dict[str, Any]] = []
    meeting_keys: list[tuple[dict[str, Any], str]] = []
    # встречи: Google (если подключён) + Орбиты
    for ev in _events_in_window(ctx, t0, t1, str(args.get("query") or "")):
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        atts = []
        for a in raw.get("attendees") or []:
            if not isinstance(a, dict) or a.get("self"):
                continue
            em = str(a.get("email") or "").strip().lower()
            atts.append(names.get(em) or str(a.get("displayName") or em))
        item = {
            "type": "meeting",
            "id": ev.get("event_id"),
            "calendar_id": ev.get("calendar_id"),
            "title": ev.get("summary"),
            "start": _fmt_dt(ev.get("start"), ctx.uid),
            "end": _fmt_dt(ev.get("end"), ctx.uid),
        }
        if atts:
            item["attendees"] = atts[:12]
        if raw.get("location"):
            item["location"] = str(raw.get("location"))[:120]
        out.append(item)
        meeting_keys.append((item, f"{item['calendar_id'] or 'primary'}:{item['id']}"))
        ctx.seen_items.append({"type": "event", "id": item["id"], "calendar_id": item["calendar_id"], "title": item["title"]})
    try:
        from assistant.stores import calendar_tasks

        for t in calendar_tasks.list_tasks_in_window(ctx.uid, t0, t1):
            out.append(
                {
                    "type": "task",
                    "id": str(t.get("id")),
                    "title": t.get("title"),
                    "start": _fmt_dt(_iso_local(ctx.uid, t.get("start_at")), ctx.uid),
                    "done": bool(t.get("done")),
                    "assignee": t.get("assignee_name") or "",
                    **({"result": str(t.get("result"))[:300]} if t.get("result") else {}),
                }
            )
            ctx.seen_items.append({"type": "task", "id": str(t.get("id")), "title": t.get("title")})
    except Exception as e:
        print(f"[gpt_calendar] tasks_list err={e!r}")
    _mark_materials(ctx, meeting_keys)
    out.sort(key=lambda x: str(x.get("start") or ""))
    note = None if has_google_calendar(ctx.uid) else "Google Календарь не подключён — показаны встречи и задачи Орбиты."
    res: dict[str, Any] = {"items": out[:MAX_EVENTS_OUT], "total": len(out)}
    if note:
        res["note"] = note
    return _ok(res)


def tool_calendar_free_slots(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import calendar as cal

    day = _parse_day(args.get("date")).isoformat()
    emails, missing = _resolve_people(ctx, args.get("with_people") or [])
    dur = int(args.get("duration_min") or 30)
    slots = cal.joint_free_slots_day(ctx.uid, day, emails, duration_min=dur)
    if slots:
        ctx.slot_offers.append(
            {"date": day, "duration_min": dur, "people": [str(p).strip() for p in args.get("with_people") or [] if str(p).strip()],
             "starts": [s0.get("start") for s0 in slots if s0.get("start")]}
        )
    res: dict[str, Any] = {"date": day, "duration_min": dur, "slots": slots[:24]}
    if missing:
        res["not_found_in_contacts"] = missing
    if not slots:
        res["note"] = "Свободных окон нет (или выходной)."
    return _ok(res)


def tool_calendar_availability(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import calendar as cal

    day = _parse_day(args.get("date")).isoformat()
    emails, missing = _resolve_people(ctx, args.get("people") or [])
    data = cal.availability_for_attendees(ctx.uid, day, emails, telegram_username=ctx.telegram_username)
    if missing:
        data["not_found_in_contacts"] = missing
    return _ok(data)


def tool_calendar_time_stats(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    t0, t1 = _window(ctx.uid, args.get("start_date"), args.get("end_date"))
    names = _email_names(ctx)
    total_min = 0
    count = 0
    by_person: Counter = Counter()
    by_title: Counter = Counter()
    by_weekday: Counter = Counter()
    longest: list[tuple[int, str, str]] = []
    for ev in _events_in_window(ctx, t0, t1):
        st, en = ev.get("start"), ev.get("end")
        if not isinstance(st, datetime) or not isinstance(en, datetime):
            continue
        mins = int((en - st).total_seconds() // 60)
        if mins <= 0 or mins >= 24 * 60:  # события на весь день не считаем
            continue
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        count += 1
        total_min += mins
        title = str(ev.get("summary") or "Встреча").strip()
        by_title[title] += mins
        by_weekday[_WD[st.astimezone(_tz(ctx.uid)).weekday()]] += mins
        for a in raw.get("attendees") or []:
            if not isinstance(a, dict) or a.get("self"):
                continue
            em = str(a.get("email") or "").strip().lower()
            by_person[names.get(em) or str(a.get("displayName") or em)] += mins
        longest.append((mins, title, _fmt_dt(st, ctx.uid)))
    workdays = sum(1 for i in range((t1 - t0).days) if (t0 + timedelta(days=i)).weekday() < 5)
    hours = round(total_min / 60, 1)
    res = {
        "period": f"{t0.date()} — {(t1 - timedelta(days=1)).date()}",
        "meetings": count,
        "hours_in_meetings": hours,
        "share_of_workdays_8h": f"{round(100 * total_min / max(1, workdays * 480))}%",
        "top_people_hours": [[k, round(v / 60, 1)] for k, v in by_person.most_common(10)],
        "top_titles_hours": [[k, round(v / 60, 1)] for k, v in by_title.most_common(10)],
        "by_weekday_hours": {k: round(v / 60, 1) for k, v in by_weekday.items()},
        "longest": [[t, d, m] for m, t, d in sorted(longest, reverse=True)[:5]],
    }
    return _ok(res)


def _yo(s: str) -> str:
    return str(s or "").lower().replace("ё", "е")


def tool_contacts_find(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    q = _yo(str(args.get("query") or "").strip().lstrip("@"))
    stem = q[:-1] if len(q) > 4 else q  # «Ульяну» → «ульян», «Артема» → «артем»
    fuzzy = None
    if q:
        try:
            from assistant.lib.calendar_attendees import find_contact_by_name

            fuzzy = find_contact_by_name(ctx.uid, q, telegram_username=ctx.telegram_username)
        except Exception:
            fuzzy = None
    rows = []
    for c in _contacts(ctx):
        hay = _yo(
            " ".join(
                [str(c.get("name") or ""), str(c.get("telegram_username") or ""), " ".join(c.get("aliases") or [])]
            )
        )
        is_fuzzy = fuzzy is not None and (
            str(c.get("email") or "") == str(fuzzy.get("email") or "")
            and str(c.get("name") or "") == str(fuzzy.get("name") or "")
        )
        if q and stem not in hay and not is_fuzzy:
            continue
        rows.append(
            {
                "name": c.get("name"),
                "telegram": ("@" + str(c.get("telegram_username"))) if c.get("telegram_username") else "",
                "has_email": bool(str(c.get("email") or "").strip()),
            }
        )
    return _ok({"contacts": rows[:50], "total": len(rows)})


def tool_reminders_list(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import reminders_store

    now = datetime.now(_tz(ctx.uid))
    items = []
    for r in reminders_store.reminders_for_user(ctx.uid):
        when = _iso_local(ctx.uid, r.get("when_iso"))
        if r.get("done") or not when or when < now:
            continue
        items.append({"id": r.get("id"), "text": r.get("task"), "when": _fmt_dt(when, ctx.uid)})
    items.sort(key=lambda x: str(x.get("when") or ""))
    return _ok({"reminders": items[:50]})


# ---------------------------------------------------------------- изменения

def _attach_online(ctx: GptCalendarContext, created: dict[str, Any], online: str) -> str:
    from assistant.lib import telemost_gcal, zoom_gcal

    online = (online or "auto").lower()
    try:
        if online == "zoom" and zoom_gcal.user_has_zoom(ctx.uid):
            zoom_gcal.attach_zoom_to_event(
                ctx.uid, created["event_id"], created["start"], created["end"],
                str(created.get("summary") or ""), calendar_id=created.get("calendar_id"),
            )
            return "Zoom"
        if online == "telemost" and telemost_gcal.user_has_telemost(ctx.uid):
            telemost_gcal.attach_telemost_to_event(
                ctx.uid, created["event_id"], str(created.get("summary") or ""),
                calendar_id=created.get("calendar_id"),
            )
            return "Телемост"
        if online == "auto":
            zoom_gcal.try_auto_attach_after_create(ctx.uid, created)
            telemost_gcal.try_auto_attach_after_create(ctx.uid, created)
    except Exception as e:
        print(f"[gpt_calendar] online_attach err={e!r}")
    return ""


def tool_calendar_create_event(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.wrote = True
    from assistant.lib import recurrence as recur
    from assistant.services import calendar as cal

    title = str(args.get("title") or "").strip() or "Встреча"
    try:
        start = _parse_start(ctx, args.get("start"))
    except (TypeError, ValueError):
        return _err("Неверное время начала, нужно YYYY-MM-DDTHH:MM.")
    dur = max(15, min(8 * 60, int(args.get("duration_min") or 30)))
    people = [str(p).strip() for p in (args.get("attendees") or []) if str(p).strip()]
    rrule = recur.build_rrule_from_args(args)
    if rrule:
        rrule = recur.ensure_until_or_count(rrule, start=start)
    parsed: dict[str, Any] = {
        "title": title,
        "start": start.isoformat(),
        "duration_min": dur,
        "attendee_names": people,
    }
    if rrule:
        parsed["recurrence"] = [rrule]
    if args.get("description"):
        parsed["description"] = str(args["description"])
    if args.get("location"):
        parsed["location"] = str(args["location"])
    missing = cal.unresolved_attendee_names(ctx.uid, parsed, telegram_username=ctx.telegram_username)
    if missing:
        return _err(
            "Не нашёл почту в контактах для: " + ", ".join(missing)
            + ". Уточни у пользователя или создай без них (уберёт из attendees)."
        )
    if not args.get("force") and not rrule:
        # Для серии конфликт первого слота не блокируем жёстко — Google сам разнесёт.
        check = cal.validate_meeting_slot(ctx.uid, parsed, start, telegram_username=ctx.telegram_username)
        if not check.get("ok"):
            alts = [_fmt_dt(a, ctx.uid) for a in (check.get("alt_starts") or [])[:6]]
            return _ok(
                {
                    "created": False,
                    "conflict": True,
                    "owner_busy": bool(check.get("owner_conflict")),
                    "attendees_busy": check.get("attendee_busy") or [],
                    "alternatives": alts,
                    "hint": "Предложи пользователю другое время из alternatives или спроси, ставить ли поверх (force=true).",
                }
            )
    try:
        created = cal.create_event(ctx.uid, parsed, telegram_username=ctx.telegram_username)
    except RuntimeError as e:
        return _err(str(e))
    online = _attach_online(ctx, created, str(args.get("online") or "auto"))
    when = human_when(created.get("start"), created.get("end"), ctx.uid)
    who = ", ".join(people)
    recur_h = recur.human_recurrence(rrule)
    detail = " · ".join(x for x in [("с " + who) if who else "", online, recur_h] if x)
    aid = record_action(
        ctx.uid, "event_create", "done",
        title=str(created.get("summary") or title),
        payload={"when": when, "detail": detail},
        result={
            "event_id": created.get("event_id"),
            "calendar_id": created.get("calendar_id"),
            "html_link": created.get("html_link"),
            "when": when,
            "detail": detail,
            "date": start.date().isoformat(),
            "recurrence": rrule or "",
        },
    )
    ctx.actions.append(aid)
    ctx.created_events.append(created)
    return _ok({
        "created": True,
        "event_id": created.get("event_id"),
        "when": when,
        "attendees": people,
        "online": online,
        "recurrence": recur_h or None,
    })


def _event_snapshot(ctx: GptCalendarContext, event_id: str, calendar_id: str | None) -> dict[str, Any]:
    from assistant.services import calendar as cal

    ev = cal.get_event(ctx.uid, event_id, calendar_id=calendar_id)
    return {
        "title": ev.get("summary") or "",
        "start": (ev.get("start") or {}).get("dateTime") or "",
        "end": (ev.get("end") or {}).get("dateTime") or "",
        "description": ev.get("description") or "",
        "location": ev.get("location") or "",
        "attendees": cal.event_attendee_emails(ev),
    }


def tool_calendar_update_event(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.wrote = True
    from assistant.lib import zoom_gcal
    from assistant.services import calendar as cal

    eid = str(args.get("event_id") or "").strip()
    if not has_google_calendar(ctx.uid) and not eid.startswith("orb-"):
        return _err("Google Календарь не подключён — менять можно только встречи Орбиты (id orb-…).")
    cal_id = str(args.get("calendar_id") or "").strip() or None
    if not eid:
        return _err("Нужен event_id — найди встречу через calendar_events.")
    try:
        before = _event_snapshot(ctx, eid, cal_id)
    except Exception as e:
        return _err(f"Встреча не найдена: {e}")
    parsed: dict[str, Any] = {}
    if args.get("title"):
        parsed["title"] = str(args["title"]).strip()
    if args.get("start"):
        try:
            st = _parse_start(ctx, args.get("start"))
        except (TypeError, ValueError):
            return _err("Неверное время, нужно YYYY-MM-DDTHH:MM.")
        parsed["start"] = st.isoformat()
        if args.get("duration_min"):
            parsed["duration_min"] = int(args["duration_min"])
        else:
            try:
                b0 = datetime.fromisoformat(str(before["start"]).replace("Z", "+00:00"))
                b1 = datetime.fromisoformat(str(before["end"]).replace("Z", "+00:00"))
                parsed["duration_min"] = max(15, int((b1 - b0).total_seconds() // 60))
            except (TypeError, ValueError):
                parsed["duration_min"] = 60
    for k in ("description", "location"):
        if k in args and args.get(k) is not None:
            parsed[k] = str(args.get(k) or "")
    names = None
    if args.get("attendees") is not None:
        names = [str(p).strip() for p in args.get("attendees") or [] if str(p).strip()]
    if args.get("add_attendees"):
        add = [str(p).strip() for p in args.get("add_attendees") or [] if str(p).strip()]
        add_emails, missing = _resolve_people(ctx, add)
        if missing:
            return _err("Не нашёл почту в контактах для: " + ", ".join(missing))
        parsed["attendees"] = sorted(set(before["attendees"]) | set(add_emails))
    if names is not None:
        emails, missing = _resolve_people(ctx, names)
        if missing:
            return _err("Не нашёл почту в контактах для: " + ", ".join(missing))
        parsed["attendees"] = emails
    if not parsed:
        return _err("Нечего менять.")
    ev = cal.update_event(ctx.uid, eid, parsed, calendar_id=cal_id)
    try:
        zoom_gcal.sync_zoom_after_calendar_update(ctx.uid, eid, parsed, calendar_id=cal_id)
    except Exception as e:
        print(f"[gpt_calendar] zoom_sync err={e!r}")
    when = human_when(ev.get("start"), ev.get("end"), ctx.uid)
    aid = record_action(
        ctx.uid, "event_update", "done",
        title=str(ev.get("summary") or before.get("title") or "Встреча"),
        payload={"when": when, "detail": "перенесено" if parsed.get("start") else "изменено"},
        before={**before, "event_id": eid, "calendar_id": cal_id},
        result={"event_id": eid, "calendar_id": cal_id, "html_link": ev.get("html_link"), "when": when,
                "detail": "перенесено" if parsed.get("start") else "изменено"},
    )
    ctx.actions.append(aid)
    ctx.updated_events.append(ev)
    return _ok({"updated": True, "when": when})


def _resolve_assignee(ctx: GptCalendarContext, name: str) -> tuple[int, str, str] | str:
    """(uid, email, display) или текст ошибки."""
    raw = str(name or "").strip()
    if not raw or raw.lower() in {"я", "мне", "себе", "self", "me"}:
        return ctx.uid, "", ctx.display_name or "Я"
    from assistant.lib.calendar_attendees import contact_display_name, find_contact_by_name
    from assistant.stores import note_members

    contact = find_contact_by_name(ctx.uid, raw, telegram_username=ctx.telegram_username)
    if not contact:
        return f"Контакт «{raw}» не найден."
    try:
        assignee_uid, contact = note_members.resolve_contact_telegram_id(
            owner_user_id=ctx.uid,
            email=str(contact.get("email") or "") or None,
            telegram_user_id=contact.get("telegram_user_id"),
            telegram_username=contact.get("telegram_username"),
            owner_username=ctx.telegram_username,
        )
    except ValueError:
        return f"У контакта «{contact_display_name(contact)}» нет Telegram в Orbita — задачу ему не поставить."
    return int(assignee_uid), str(contact.get("email") or ""), contact_display_name(contact)


def tool_task_create(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.wrote = True
    from assistant.lib import recurrence as recur
    from assistant.stores import calendar_tasks

    title = str(args.get("title") or "").strip()
    if not title:
        return _err("Нужно название задачи.")
    try:
        start = _parse_start(ctx, args.get("start"))
    except (TypeError, ValueError):
        return _err("Неверное время, нужно YYYY-MM-DDTHH:MM.")
    who = _resolve_assignee(ctx, str(args.get("assignee") or ""))
    if isinstance(who, str):
        return _err(who)
    a_uid, a_email, a_name = who
    dur = max(15, int(args.get("duration_min") or 30))
    rrule = recur.build_rrule_from_args(args)
    if rrule:
        rrule = recur.ensure_until_or_count(rrule, start=start)
    row = calendar_tasks.create_task(
        ctx.uid,
        title=title,
        description=str(args.get("description") or ""),
        start_at=start.isoformat(),
        end_at=(start + timedelta(minutes=dur)).isoformat(),
        assignee_user_id=a_uid,
        assignee_email=a_email,
        assignee_name=a_name,
        all_day=bool(args.get("all_day")),
        tz=_tz(ctx.uid),
        recurrence=rrule or "",
    )
    if ctx.sync_task_google and not rrule:
        # Серию в Google Tasks не пушим — одна запись в Leo, иначе плодятся отдельные задачи.
        try:
            row = ctx.sync_task_google(ctx.uid, row, "create") or row
        except Exception as e:
            print(f"[gpt_calendar] task_sync err={e!r}")
    delegated = int(a_uid) != int(ctx.uid)
    if delegated and ctx.notify_task_assignee:
        try:
            ctx.notify_task_assignee(assignee_user_id=a_uid, task=row, assigner_name=ctx.display_name or "Участник")
        except Exception as e:
            print(f"[gpt_calendar] task_notify err={e!r}")
    when = human_when(start, None if args.get("all_day") else start + timedelta(minutes=dur), ctx.uid)
    recur_h = recur.human_recurrence(rrule)
    detail = " · ".join(
        x for x in [(("для " + a_name) if delegated else "себе"), recur_h] if x
    )
    aid = record_action(
        ctx.uid, "task_create", "done", title=title,
        payload={"when": when, "detail": detail},
        result={"task_id": row.get("id"), "when": when, "detail": detail, "recurrence": rrule or ""},
    )
    ctx.actions.append(aid)
    return _ok({
        "created": True,
        "task_id": row.get("id"),
        "when": when,
        "assignee": a_name,
        "recurrence": recur_h or None,
    })


def tool_reminder_create(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.wrote = True
    from assistant.lib import recurrence as recur
    from assistant.stores import reminders_store

    text = str(args.get("text") or "").strip()
    if not text:
        return _err("Нужен текст напоминания.")
    try:
        when = _parse_start(ctx, args.get("when"))
    except (TypeError, ValueError):
        return _err("Неверное время, нужно YYYY-MM-DDTHH:MM.")
    rrule = recur.build_rrule_from_args(args)
    if rrule:
        rrule = recur.ensure_until_or_count(rrule, start=when)
    rid = reminders_store.add_reminder(
        user_id=ctx.uid,
        chat_id=ctx.uid,
        task=text,
        when_iso=when.isoformat(),
        checklist=None,
        recurrence=rrule or "",
    )
    hw = human_when(when, None, ctx.uid)
    recur_h = recur.human_recurrence(rrule)
    detail = " · ".join(x for x in ["напоминание в Telegram", recur_h] if x)
    aid = record_action(
        ctx.uid, "reminder_create", "done", title=text,
        payload={"when": hw, "detail": detail},
        result={"reminder_id": rid, "when": hw, "detail": detail, "recurrence": rrule or ""},
    )
    ctx.actions.append(aid)
    return _ok({"created": True, "when": hw, "recurrence": recur_h or None})


def _find_target_by_title(ctx: GptCalendarContext, kind: str, title: str) -> tuple[str, str | None] | None:
    """Найти ближайшую встречу/задачу/напоминание по названию (на 60 дней вперёд и 7 назад)."""
    q = str(title or "").strip().lower()
    if not q:
        return None
    tz = _tz(ctx.uid)
    t0 = datetime.now(tz) - timedelta(days=7)
    t1 = datetime.now(tz) + timedelta(days=60)
    if kind == "task":
        from assistant.stores import calendar_tasks

        for t in calendar_tasks.list_tasks_in_window(ctx.uid, t0, t1):
            if q in str(t.get("title") or "").lower():
                return str(t.get("id")), None
    elif kind == "event":
        for ev in _events_in_window(ctx, t0, t1):
            if q in str(ev.get("summary") or "").lower():
                return str(ev.get("event_id")), str(ev.get("calendar_id") or "") or None
    elif kind == "reminder":
        from assistant.stores import reminders_store

        for rm in reminders_store.reminders_for_user(ctx.uid):
            if not rm.get("done") and q in str(rm.get("task") or "").lower():
                return str(rm.get("id")), None
    return None


def tool_propose_delete(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.wrote = True
    kind = str(args.get("kind") or "").strip()
    oid = str(args.get("id") or "").strip()
    if kind not in ("event", "task", "reminder"):
        return _err("Нужен kind: event|task|reminder.")
    if kind == "task" and oid and not oid.isdigit():
        oid = ""
    if not oid or (kind == "task" and not _task_exists(ctx, oid)):
        found = _find_target_by_title(ctx, kind, str(args.get("title") or args.get("id") or ""))
        if not found:
            return _err("Не нашёл, что удалять — уточни название или посмотри calendar_events.")
        oid, cal_found = found
        if cal_found and not args.get("calendar_id"):
            args = {**args, "calendar_id": cal_found}
    title, when = "", ""
    payload: dict[str, Any] = {"target_id": oid}
    try:
        if kind == "event":
            from assistant.services import calendar as cal

            cal_id = str(args.get("calendar_id") or "").strip() or None
            ev = cal.get_event(ctx.uid, oid, calendar_id=cal_id)
            title = str(ev.get("summary") or "Встреча")
            st = datetime.fromisoformat(str((ev.get("start") or {}).get("dateTime") or "").replace("Z", "+00:00"))
            en = datetime.fromisoformat(str((ev.get("end") or {}).get("dateTime") or "").replace("Z", "+00:00"))
            when = human_when(st, en, ctx.uid)
            payload["calendar_id"] = cal_id
        elif kind == "task":
            from assistant.stores import calendar_tasks

            t = calendar_tasks.get_task_for_user(ctx.uid, oid)
            if not t:
                return _err("Задача не найдена.")
            title = str(t.get("title") or "Задача")
            when = human_when(_iso_local(ctx.uid, t.get("start_at")), None, ctx.uid)
        else:
            from assistant.stores import reminders_store

            r = reminders_store.get_reminder_by_id(oid)
            if not r or int(r.get("user_id") or 0) != ctx.uid:
                return _err("Напоминание не найдено.")
            title = str(r.get("task") or "Напоминание")
            when = human_when(_iso_local(ctx.uid, r.get("when_iso")), None, ctx.uid)
    except ValueError:
        pass
    except Exception as e:
        return _err(f"Не нашёл, что удалять: {e}")
    payload.update({"when": when, "detail": "удалить?"})
    aid = record_action(ctx.uid, f"{kind}_delete", "proposed", title=title or "Без названия", payload=payload)
    ctx.actions.append(aid)
    return _ok({"proposed": True, "note": "Пользователь подтвердит удаление кнопкой под ответом."})


def _task_exists(ctx: GptCalendarContext, oid: str) -> bool:
    try:
        from assistant.stores import calendar_tasks

        return bool(calendar_tasks.get_task_for_user(ctx.uid, oid))
    except Exception:
        return False


def _card_for(ctx: GptCalendarContext, item: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """(kind, title, payload) для карточки «открыть»."""
    typ = str(item.get("type") or "").strip()
    oid = str(item.get("id") or "").strip()
    if not oid:
        return None
    if typ == "event":
        from assistant.services import calendar as cal

        cal_id = str(item.get("calendar_id") or "").strip() or None
        ev = cal.get_event(ctx.uid, oid, calendar_id=cal_id)
        st = _iso_local(ctx.uid, (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"))
        en = _iso_local(ctx.uid, (ev.get("end") or {}).get("dateTime") or "")
        names = _email_names(ctx)
        who = [
            names.get(str(a.get("email") or "").lower()) or str(a.get("displayName") or a.get("email") or "")
            for a in ev.get("attendees") or []
            if isinstance(a, dict) and not a.get("self")
        ]
        detail = ("с " + ", ".join(w for w in who if w)[:120]) if who else ""
        return "show_event", str(ev.get("summary") or "Встреча"), {
            "when": human_when(st, en, ctx.uid), "detail": detail, "target_id": oid,
            "calendar_id": cal_id,
            "date": st.date().isoformat() if st else "",
        }
    if typ == "task":
        from assistant.stores import calendar_tasks

        t = calendar_tasks.get_task_for_user(ctx.uid, oid)
        if not t:
            return None
        st = _iso_local(ctx.uid, t.get("start_at"))
        who = str(t.get("assignee_name") or "")
        detail = ("✓ выполнена" if t.get("done") else "") or (("для " + who) if who and int(t.get("assignee_user_id") or 0) != ctx.uid else "")
        return "show_task", str(t.get("title") or "Задача"), {
            "when": human_when(st, None, ctx.uid), "detail": detail, "target_id": oid,
            "date": st.date().isoformat() if st else "",
        }
    if typ == "reminder":
        from assistant.stores import reminders_store

        rm = reminders_store.get_reminder_by_id(oid)
        if not rm or int(rm.get("user_id") or 0) != ctx.uid:
            return None
        st = _iso_local(ctx.uid, rm.get("when_iso"))
        return "show_reminder", str(rm.get("task") or "Напоминание"), {
            "when": human_when(st, None, ctx.uid), "detail": "", "target_id": oid,
            "date": st.date().isoformat() if st else "",
        }
    return None


def tool_show_calendar_cards(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.cards_shown = True
    shown = 0
    for item in (args.get("items") or [])[:MAX_CARDS]:
        if not isinstance(item, dict):
            continue
        try:
            card = _card_for(ctx, item)
        except Exception as e:
            print(f"[gpt_calendar] card err={e!r}")
            card = None
        if not card:
            continue
        kind, title, payload = card
        ctx.actions.append(record_action(ctx.uid, kind, "info", title=title, payload=payload))
        shown += 1
    return _ok({"shown": shown, "note": "Карточки показаны под ответом — не повторяй подробный список текстом."})


def tool_offer_slots(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    ctx.slots_offered = True
    people = [str(p).strip() for p in (args.get("attendees") or []) if str(p).strip()]
    dur = max(15, min(8 * 60, int(args.get("duration_min") or 30)))
    title = str(args.get("title") or "").strip() or ("Встреча с " + ", ".join(people) if people else "Встреча")
    group = uuid.uuid4().hex[:10]
    offered = 0
    for raw in (args.get("slots") or [])[:6]:
        try:
            st = _parse_start(ctx, raw)
        except (TypeError, ValueError):
            continue
        payload = {
            "start": st.isoformat(), "duration_min": dur, "attendees": people, "group": group,
            "when": human_when(st, st + timedelta(minutes=dur), ctx.uid),
            "detail": ("с " + ", ".join(people)) if people else "",
            "date": st.date().isoformat(),
        }
        ctx.actions.append(record_action(ctx.uid, "slot", "proposed", title=title, payload=payload))
        offered += 1
    if not offered:
        return _err("Нужны варианты в формате YYYY-MM-DDTHH:MM.")
    return _ok({"offered": offered, "note": "Пользователь выберет вариант карточкой — встреча создастся сама."})


def _pick_slots(ctx: GptCalendarContext, starts: list[str], answer: str, limit: int = 6) -> list[str]:
    """Слоты для карточек: сначала те, что модель назвала в ответе (по «ЧЧ:ММ»), иначе — равномерно по дню."""
    tz = _tz(ctx.uid)
    parsed: list[tuple[datetime, str]] = []
    for raw in starts:
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        parsed.append((dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz), str(raw)))
    if not parsed:
        return []
    text = str(answer or "")
    mentioned = [raw for dt, raw in parsed if dt.strftime("%H:%M") in text or dt.strftime("%-H:%M") in text]
    if mentioned:
        return mentioned[:limit]
    if len(parsed) <= limit:
        return [raw for _, raw in parsed]
    step = len(parsed) / float(limit)
    return [parsed[int(i * step)][1] for i in range(limit)]


_QUOTED_TASK_RE = re.compile(r"«([^»]+)»[^«;]*?\(task id (\d+)\)")


def seed_quoted_tasks(ctx: GptCalendarContext, quotes: list[str]) -> None:
    """Задачи из цитаты (карточка Фокуса: «Название» … (task id N)) — кандидаты в автокарточки,
    даже если модель не стала искать их через calendar_events."""
    for q in quotes:
        for title, oid in _QUOTED_TASK_RE.findall(str(q or "")):
            ctx.seen_items.append({"type": "task", "id": oid, "title": title})


def finalize_auto_cards(ctx: GptCalendarContext, answer: str) -> None:
    """Если модель посмотрела календарь/слоты, но не показала карточки — показать их самим."""
    text = str(answer or "")
    low = text.lower()
    if ctx.slot_offers and not ctx.slots_offered and not ctx.created_events:
        offer = ctx.slot_offers[-1]
        picked = _pick_slots(ctx, offer.get("starts") or [], text)
        if picked:
            tool_offer_slots(
                ctx,
                {"slots": picked, "duration_min": offer.get("duration_min"), "attendees": offer.get("people") or []},
            )
    if ctx.seen_items and not ctx.cards_shown and not ctx.wrote:
        seen: dict[str, dict[str, Any]] = {}
        for it in ctx.seen_items:
            seen.setdefault(f"{it.get('type')}:{it.get('id')}", it)
        items = list(seen.values())
        named = [it for it in items if str(it.get("title") or "").strip() and str(it.get("title")).strip().lower() in low]
        chosen = named or (items if len(items) <= MAX_CARDS else [])
        if chosen:
            tool_show_calendar_cards(
                ctx,
                {"items": [{"type": it["type"], "id": it["id"], "calendar_id": it.get("calendar_id")} for it in chosen[:MAX_CARDS]]},
            )


def apply_slot(a: dict[str, Any], *, telegram_username: str | None = None) -> dict[str, Any]:
    """Выбранный слот → встреча (участникам уйдут приглашения)."""
    from assistant.services import calendar as cal

    uid = int(a["owner_uid"])
    p = a.get("payload") or {}
    ctx = GptCalendarContext(uid=uid, telegram_username=telegram_username)
    emails, missing = _resolve_people(ctx, p.get("attendees") or [])
    parsed: dict[str, Any] = {
        "title": a.get("title") or "Встреча",
        "start": p.get("start"),
        "duration_min": int(p.get("duration_min") or 30),
        "attendees": emails,
    }
    created = cal.create_event(uid, parsed, telegram_username=telegram_username)
    _attach_online(ctx, created, "auto")
    when = human_when(created.get("start"), created.get("end"), uid)
    st = created.get("start")
    return {
        "event_id": created.get("event_id"),
        "calendar_id": created.get("calendar_id"),
        "html_link": created.get("html_link"),
        "when": when,
        "detail": (p.get("detail") or "") + (" · без " + ", ".join(missing) if missing else ""),
        "date": st.date().isoformat() if isinstance(st, datetime) else "",
        "_created": created,
    }


def dismiss_slot_siblings(a: dict[str, Any]) -> None:
    group = (a.get("payload") or {}).get("group")
    if not group:
        return
    with _lock():
        rows = _conn().execute(
            "SELECT id, payload_json FROM gpt_calendar_actions WHERE owner_uid = ? AND kind = 'slot' AND status = 'proposed'",
            (int(a["owner_uid"]),),
        ).fetchall()
    for rid, pj in rows:
        try:
            if json.loads(pj or "{}").get("group") == group and rid != a["id"]:
                set_action_status(rid, "dismissed")
        except (TypeError, ValueError):
            continue


def _focus_row_for(ctx: GptCalendarContext, args: dict[str, Any]) -> dict[str, Any] | None:
    from assistant.services import focus_items

    key = str(args.get("key") or "").strip()
    if key:
        row = focus_items.get(ctx.uid, key)
        if row:
            return row
    rows = sorted(focus_items.rows(ctx.uid), key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    text = str(args.get("text") or key or "").strip()
    if text:
        scored = [(focus_items._score(text, f"{r.get('who') or ''} {r.get('text') or ''}"), r) for r in rows[:60]]
        scored = [x for x in scored if x[0] >= 0.5]
        if scored:
            return max(scored, key=lambda x: x[0])[1]
    return rows[0] if rows and not text else None


def tool_focus_item_update(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import focus_items

    row = _focus_row_for(ctx, args)
    if not row:
        return _err("Пункт Фокуса не найден — попроси пользователя нажать «Уточнить» на карточке")
    op = str(args.get("op") or "update")
    kw: dict[str, Any] = {}
    if op == "update":
        kw["fields"] = {k: args[k] for k in ("assignee", "task", "deadline", "comment") if str(args.get(k) or "").strip()}
        if not kw["fields"]:
            return _err("Нечего менять: передай assignee, task, deadline или comment")
    elif op == "link":
        kw["url"] = args.get("url")
    elif op in ("snooze", "retype"):
        if args.get("until"):
            dt = _iso_local(ctx.uid, args.get("until"))
            kw["until"] = dt.isoformat() if dt else None
        kw["type"] = args.get("type")
    elif op == "dismiss":
        kw["reason"] = args.get("reason") or "not_relevant"
    try:
        saved = focus_items.apply(ctx.uid, row["item_key"], op, by=ctx.display_name or "", **kw)
    except ValueError as e:
        return _err(str(e))
    ctx.wrote = True
    ctx.actions.append(record_action(
        ctx.uid, "focus_item", "info", title=str(saved.get("text") or "")[:200],
        payload={"keys": [saved["item_key"]], "card_key": saved.get("card_key") or ""},
    ))
    return _ok({"ok": True, "item": focus_items.public(saved),
                "note": "Карточка с новым состоянием показана под ответом; коротко скажи, что поменял."})


EXECUTORS: dict[str, Callable[[GptCalendarContext, dict[str, Any]], str]] = {
    "focus_item_update": tool_focus_item_update,
    "calendar_events": tool_calendar_events,
    "calendar_free_slots": tool_calendar_free_slots,
    "calendar_availability": tool_calendar_availability,
    "calendar_time_stats": tool_calendar_time_stats,
    "contacts_find": tool_contacts_find,
    "reminders_list": tool_reminders_list,
    "calendar_create_event": tool_calendar_create_event,
    "calendar_update_event": tool_calendar_update_event,
    "task_create": tool_task_create,
    "reminder_create": tool_reminder_create,
    "propose_delete": tool_propose_delete,
    "show_calendar_cards": tool_show_calendar_cards,
    "offer_slots": tool_offer_slots,
}


def make_runner(ctx: GptCalendarContext) -> Callable[[str, dict[str, Any]], str]:
    def run(name: str, args: dict[str, Any]) -> str:
        fn = EXECUTORS.get(name)
        if not fn:
            return _err(f"Неизвестный инструмент {name}")
        try:
            out = fn(ctx, args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001 — ошибка календаря уходит модели текстом
            print(f"[gpt_calendar] tool={name} failed uid={ctx.uid} err={e!r}")
            out = _err(f"Не получилось: {e}")
        failed = out.startswith('{"error')
        print(f"[gpt_calendar] tool={name} uid={ctx.uid} ok={not failed}")
        return out

    return run


# ---------------------------------------------------------------- отмена / подтверждение

def undo_action(a: dict[str, Any]) -> dict[str, Any]:
    from assistant.services import calendar as cal

    uid = int(a["owner_uid"])
    res = a.get("result") or {}
    kind = a["kind"]
    if kind in ("event_create", "slot"):
        cal.delete_event(uid, str(res.get("event_id")), calendar_id=res.get("calendar_id"))
    elif kind == "event_update":
        b = a.get("before") or {}
        parsed: dict[str, Any] = {"title": b.get("title") or "", "attendees": list(b.get("attendees") or [])}
        if b.get("start"):
            parsed["start"] = b["start"]
            parsed["end"] = b.get("end") or ""
        parsed["description"] = b.get("description") or ""
        parsed["location"] = b.get("location") or ""
        cal.update_event(uid, str(b.get("event_id")), parsed, calendar_id=b.get("calendar_id"))
    elif kind == "task_create":
        from assistant.stores import calendar_tasks

        task = calendar_tasks.get_task_for_user(uid, res.get("task_id"))
        if task:
            try:
                cal.delete_task_event(uid, task)
            except Exception as e:
                print(f"[gpt_calendar] undo task gcal err={e!r}")
            calendar_tasks.delete_task(uid, res.get("task_id"))
    elif kind == "reminder_create":
        from assistant.stores import reminders_store

        reminders_store.remove_reminder(str(res.get("reminder_id")))
    else:
        raise ValueError("Это действие нельзя отменить")
    return {**res, "detail": "отменено"}


def apply_delete(a: dict[str, Any]) -> dict[str, Any]:
    uid = int(a["owner_uid"])
    p = a.get("payload") or {}
    oid = str(p.get("target_id") or "")
    kind = a["kind"]
    if kind == "event_delete":
        from assistant.services import calendar as cal

        cal.delete_event(uid, oid, calendar_id=p.get("calendar_id"))
    elif kind == "task_delete":
        from assistant.services import calendar as cal
        from assistant.stores import calendar_tasks

        task = calendar_tasks.get_task_for_user(uid, oid)
        if task:
            try:
                cal.delete_task_event(uid, task)
            except Exception as e:
                print(f"[gpt_calendar] delete task gcal err={e!r}")
        calendar_tasks.delete_task(uid, oid)
    elif kind == "reminder_delete":
        from assistant.stores import reminders_store

        reminders_store.remove_reminder(oid)
    else:
        raise ValueError("Неизвестное действие")
    return {"when": p.get("when") or "", "detail": "удалено"}


def actions_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        a = get_action(i)
        if a:
            out.append(a)
    return out



# ---------------------------------------------------------------- материалы встреч

def _mark_materials(ctx: GptCalendarContext, pairs: list[tuple[dict[str, Any], str]]) -> None:
    if not pairs:
        return
    try:
        from assistant.stores import meeting_links

        kinds = meeting_links.kinds_for_events(ctx.uid, [k for _, k in pairs])
    except Exception as e:
        print(f"[gpt_calendar] materials mark err={e!r}")
        return
    for item, key in pairs:
        k = kinds.get(key)
        if k:
            item["materials"] = {"transcriptions": k.get("transcription", 0), "notes": k.get("note", 0)}


def _material_digest(ctx: GptCalendarContext, note_id: int, *, full: bool) -> dict[str, Any] | None:
    from assistant.lib.telegram_html import html_to_plain
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    note = notes_store.get_accessible_note(ctx.uid, int(note_id), with_sharing=False)
    if not note:
        return None
    is_tr = str(note.get("role") or "") == "transcription"
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    out: dict[str, Any] = {"note_id": int(note["id"]), "kind": "transcription" if is_tr else "note", "title": note.get("title")}
    if is_tr:
        if meta.get("main_topic"):
            out["main_topic"] = str(meta.get("main_topic"))[:300]
        from assistant.services import meeting_tasks

        # статус пункта — как в таблице задач встречи (живой, из связанной задачи)
        state = {x["id"]: x for x in meeting_tasks.rows(note)["items"]} if meta.get("tasks") else {}
        tasks = []
        for t in meta.get("tasks") or []:
            if isinstance(t, dict):
                row = {k: str(t.get(k) or "")[:200] for k in ("assignee", "task", "deadline", "comment") if t.get(k)}
                x = state.get(str(t.get("id") or ""))
                if x:
                    row["status"] = x["label"]
                    if x["code"] in ("not_task", "duplicate"):
                        row["status"] += " — не заводить"
                    elif x["code"] != "open":
                        row["responsible"] = x["who"]
                        if x["due"]:
                            row["deadline"] = x["due"]
                elif t.get("completed"):
                    row["status"] = "выполнена" + (f" ({t.get('completed_by')}, {str(t.get('completed_at') or '')[:10]})" if t.get("completed_by") else "")
                if isinstance(t.get("link"), dict) and t["link"].get("url"):
                    row["link"] = str(t["link"]["url"])[:300]
                tasks.append(row)
        if tasks:
            out["tasks"] = tasks[:15]
        if meta.get("decisions"):
            out["decisions"] = [str(x)[:300] for x in meta.get("decisions") or []][:12]
        if meta.get("open_questions"):
            out["open_questions"] = [str(x)[:300] for x in meta.get("open_questions") or []][:8]
        summary = tnotes.summary_html(note)
        text = summary or (tnotes.transcript_html(note) if full else "")
    else:
        text = str(note.get("body") or "")
    from assistant.services.focus_items import marks_to_text

    text = marks_to_text(text)
    try:
        plain = html_to_plain(text).strip() if text else ""
    except Exception:
        plain = re.sub(r"<[^>]+>", " ", text or "")
    limit = 6000 if full else 1200
    if plain:
        out["text"] = plain[:limit] + ("…" if len(plain) > limit else "")
    return out


def _meeting_people(link: dict[str, Any], people: list[dict[str, Any]]) -> list[str]:
    names = [str(a.get("name") or a.get("email") or "") for a in link.get("attendees") or []]
    names += [str(p.get("name") or p.get("email") or "") for p in people]
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen[:20]


def tool_meeting_materials(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import meeting_links

    eid = str(args.get("event_id") or "").strip()
    cal = str(args.get("calendar_id") or "primary").strip() or "primary"
    if not eid:
        title = str(args.get("title") or "").strip().lower()
        if not title:
            return _err("Укажи event_id или title встречи")
        day = args.get("date")
        t0, t1 = _window(ctx.uid, day or (datetime.now(_tz(ctx.uid)) - timedelta(days=30)).date().isoformat(),
                         day or datetime.now(_tz(ctx.uid)).date().isoformat())
        cands = [ev for ev in _events_in_window(ctx, t0, t1) if title in str(ev.get("summary") or "").lower()]
        if not cands:
            return _err("Встреча не найдена — уточни дату или посмотри calendar_events")
        ev = cands[-1]
        eid, cal = str(ev.get("event_id") or ""), str(ev.get("calendar_id") or "primary")
    rows = meeting_links.notes_for_event(ctx.uid, meeting_links.event_key(cal, eid))
    if not rows:
        return _ok({"event_id": eid, "materials": [], "note": "К этой встрече ничего не привязано (нет записи и заметок)."})
    mats = []
    meeting = None
    for r in rows:
        d = _material_digest(ctx, int(r["note_id"]), full=len(rows) <= 2)
        if d:
            mats.append(d)
        if meeting is None:
            link = meeting_links.get_for_note(ctx.uid, int(r["note_id"]))
            if link:
                meeting = {
                    "title": link.get("summary"),
                    "when": human_when(_iso_local(ctx.uid, link.get("start_iso")), _iso_local(ctx.uid, link.get("end_iso")), ctx.uid),
                    "people": _meeting_people(link, meeting_links.people_for_note(ctx.uid, int(r["note_id"]))),
                }
    return _ok({"meeting": meeting, "materials": mats})


def tool_person_history(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import contacts_store
    from assistant.stores import meeting_links

    who = str(args.get("person") or "").strip()
    if not who:
        return _err("Укажи человека")
    emails, missing = _resolve_people(ctx, [who])
    contact = None
    q = _yo(who.lstrip("@"))
    for c in _contacts(ctx):
        hay = _yo(" ".join([str(c.get("name") or ""), str(c.get("telegram_username") or ""), str(c.get("email") or "")]))
        if (emails and str(c.get("email") or "").lower() == emails[0].lower()) or (q and q[:-1 if len(q) > 4 else None] in hay):
            contact = c
            break
    email = (emails[0] if emails else str((contact or {}).get("email") or "")).strip().lower()
    if not email and not contact:
        return _err(f"Не нашёл «{who}» в контактах — уточни имя (contacts_find)")
    now = datetime.now(_tz(ctx.uid))
    t0, t1 = _window(
        ctx.uid,
        args.get("start_date") or (now - timedelta(days=60)).date().isoformat(),
        args.get("end_date") or (now + timedelta(days=14)).date().isoformat(),
    )
    meetings: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    if email:
        evs = []
        for ev in _events_in_window(ctx, t0, t1, email):
            raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
            if not any(str(a.get("email") or "").lower() == email for a in raw.get("attendees") or [] if isinstance(a, dict)):
                continue
            evs.append(ev)
        keys = [meeting_links.event_key(ev.get("calendar_id"), ev.get("event_id")) for ev in evs]
        mats = meeting_links.materials_for_events(ctx.uid, keys)
        budget = 6  # полный текст материалов — для последних встреч, остальное кратко
        for ev, key in sorted(zip(evs, keys), key=lambda p: str(p[0].get("start_iso") or p[0].get("start") or ""), reverse=True):
            seen_keys.add(key)
            item: dict[str, Any] = {
                "id": ev.get("event_id"),
                "calendar_id": ev.get("calendar_id"),
                "title": ev.get("summary"),
                "start": _fmt_dt(ev.get("start"), ctx.uid),
            }
            digests = []
            for m in mats.get(key, []):
                d = _material_digest(ctx, int(m["note_id"]), full=False) if budget > 0 else {"note_id": m["note_id"], "kind": m["kind"], "title": m["title"]}
                budget -= 1
                if d:
                    digests.append(d)
            if digests:
                item["materials"] = digests
            meetings.append(item)
            ctx.seen_items.append({"type": "event", "id": item["id"], "calendar_id": item["calendar_id"], "title": item["title"]})
    keys = [str((contact or {}).get("email") or "").lower(), str((contact or {}).get("name") or "").strip().lower()]
    if contact:
        keys.append(contacts_store.contact_key(contact))
    manual = []
    for row in meeting_links.notes_for_person(ctx.uid, keys):
        if row.get("event_id") and meeting_links.event_key(row.get("calendar_id"), row.get("event_id")) in seen_keys:
            continue
        d = _material_digest(ctx, int(row["note_id"]), full=False)
        if d:
            d["meeting"] = row.get("summary") or ""
            manual.append(d)
    res: dict[str, Any] = {
        "person": (contact or {}).get("name") or who,
        "meetings": meetings[:25],
        "meetings_total": len(meetings),
    }
    if manual:
        res["other_materials"] = manual[:10]
    try:
        from assistant.stores import calendar_tasks

        match_person = _person_matcher(ctx, (contact or {}).get("name") or who)
        tasks = [
            _task_brief(ctx, t)
            for t in calendar_tasks.list_involved_tasks(ctx.uid, limit=1000)
            if match_person and str(t.get("assignee_user_id") or "") != str(ctx.uid)
            and match_person(str(t.get("assignee_user_id") or ""), str(t.get("assignee_email") or ""), str(t.get("assignee_name") or ""))
        ]
        if tasks:
            res["tasks_given"] = tasks[:15]
            res["tasks_given_total"] = len(tasks)
            for it in tasks[:15]:
                ctx.seen_items.append({"type": "task", "id": it["id"], "title": it["title"]})
    except Exception as e:
        print(f"[gpt_calendar] person_history tasks err={e!r}")
    if not email:
        res["note"] = "У контакта нет почты — встречи из календаря не найти, показаны только материалы, куда он добавлен вручную."
    elif not has_google_calendar(ctx.uid):
        res["note"] = "Google Календарь не подключён — показаны встречи Орбиты."
    return _ok(res)


_STOP_STEMS = {"для", "про", "что", "как", "это", "все", "при", "над", "под", "без", "или", "его", "она", "они", "мне", "мой", "мои", "задач"}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-я0-9]{3,}", _yo(text))} - _STOP_STEMS


def _person_matcher(ctx: GptCalendarContext, who: str) -> Callable[[str, str, str], bool] | None:
    """Совпадение (user_id, email, имя) с человеком из запроса: контакт + падеж в имени («Ульяне» → «Ульяна»)."""
    raw = str(who or "").strip().lstrip("@")
    if not raw:
        return None
    ids: set[str] = set()
    emails: set[str] = set()
    try:
        res = _resolve_assignee(ctx, raw)
        if isinstance(res, tuple):
            ids.add(str(res[0]))
            if res[1]:
                emails.add(res[1].lower())
    except Exception:
        pass
    words = [w for w in re.findall(r"[a-zа-я0-9]+", _yo(raw)) if len(w) >= 3]
    stems = [w[:-1] if len(w) > 4 else w[: max(3, len(w) - 1)] for w in words]

    def match(uid: str, email: str, name: str) -> bool:
        if uid and uid in ids:
            return True
        if email and email.lower() in emails:
            return True
        hay = _yo(name)
        return bool(stems) and all(st in hay for st in stems)

    return match


def _task_brief(ctx: GptCalendarContext, t: dict[str, Any]) -> dict[str, Any]:
    owner = str(t.get("owner_user_id") or "")
    assignee = str(t.get("assignee_user_id") or "")
    mine = str(ctx.uid)
    item: dict[str, Any] = {
        "type": "task",
        "id": str(t.get("id")),
        "title": t.get("title"),
        "status": "выполнена" if t.get("done") else "не выполнена",
        "when": _fmt_dt(_iso_local(ctx.uid, t.get("start_at")), ctx.uid),
        "created": _fmt_dt(_iso_local(ctx.uid, t.get("created_at")), ctx.uid),
        "assignee": "я" if assignee in ("", mine) else (t.get("assignee_name") or "контакт"),
    }
    if owner != mine:
        item["set_by_other"] = True
    if t.get("done"):
        item["updated"] = _fmt_dt(_iso_local(ctx.uid, t.get("updated_at")), ctx.uid)
    if t.get("description"):
        item["description"] = str(t.get("description"))[:400]
    if t.get("result"):
        item["result"] = str(t.get("result"))[:800]
    cl = [c for c in t.get("checklist") or [] if isinstance(c, dict) and str(c.get("text") or "").strip()]
    if cl:
        item["checklist"] = [("✓ " if c.get("done") else "· ") + str(c.get("text"))[:120] for c in cl[:12]]
    if t.get("assignee_status_note") and t.get("assignee_status") in ("question", "in_progress"):
        item["assignee_note"] = str(t.get("assignee_status_note"))[:300]
    if t.get("owner_archived"):
        item["archived"] = True
    if t.get("recurrence"):
        item["recurring"] = True
    return item


def tool_tasks_search(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.stores import calendar_tasks

    query = str(args.get("query") or "").strip()
    status = str(args.get("status") or "any").strip().lower()
    match_person = _person_matcher(ctx, str(args.get("assignee") or ""))
    q = _stems(query)
    hits: list[tuple[float, dict[str, Any]]] = []
    tasks = calendar_tasks.list_involved_tasks(ctx.uid)
    talk = calendar_tasks.discussion_by_task([int(t["id"]) for t in tasks if t.get("assignee_user_id")])
    for t in tasks:
        if status == "open" and t.get("done"):
            continue
        if status == "done" and not t.get("done"):
            continue
        if match_person is not None:
            assignee_ok = match_person(str(t.get("assignee_user_id") or ""), str(t.get("assignee_email") or ""), str(t.get("assignee_name") or ""))
            owner_ok = str(t.get("owner_user_id") or "") != str(ctx.uid) and match_person(str(t.get("owner_user_id") or ""), "", "")
            if not (assignee_ok or owner_ok):
                continue
        score = 1.0
        if q:
            hay = _stems(" ".join([
                str(t.get("title") or ""),
                str(t.get("description") or ""),
                str(t.get("result") or ""),
                " ".join(str(c.get("text") or "") for c in t.get("checklist") or [] if isinstance(c, dict)),
                " ".join(m["text"] for m in talk.get(int(t["id"])) or []),
            ]))
            title = _stems(str(t.get("title") or ""))
            found = len(q & hay)
            if not found:
                continue
            score = found / len(q) + 0.25 * len(q & title) / len(q)
        hits.append((score, t))
    # list_involved_tasks уже свежие первыми — sort стабилен.
    hits.sort(key=lambda p: -p[0])
    top = [t for _, t in hits[:15]]
    out = []
    for t in top:
        it = _task_brief(ctx, t)
        msgs = talk.get(int(t["id"])) or []
        if msgs:
            # Последние сообщения: там часто итоговое решение по задаче.
            it["discussion"] = [
                f"{m.get('actor_name') or ('исполнитель' if m['actor_user_id'] == str(t.get('assignee_user_id') or '') else 'постановщик')}"
                f" ({m['at'][:10]}): {m['text'].strip()[:500]}"
                for m in msgs[-8:]
            ]
        out.append(it)
    for it in out:
        ctx.seen_items.append({"type": "task", "id": it["id"], "title": it["title"]})
    res: dict[str, Any] = {"items": out, "total": len(hits)}
    if not hits:
        res["note"] = "Ничего не нашёл — попробуй другие слова темы или без темы, только по исполнителю."
    return _ok(res)


def tool_meeting_tasks_search(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import meeting_tasks
    from assistant.stores import notes as notes_store
    from assistant.stores.note_members import note_webapp_url

    q = _stems(str(args.get("query") or ""))
    mq = _stems(str(args.get("meeting") or ""))
    try:
        days = max(1, min(int(args.get("days") or 42), 365))
    except (TypeError, ValueError):
        days = 42
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    hits: list[tuple[float, dict[str, Any]]] = []
    for note in notes_store.list_transcription_notes(ctx.uid, limit=200):
        if str(note.get("created_at") or "") < since:
            continue
        tasks = (note.get("meta") or {}).get("tasks") if isinstance(note.get("meta"), dict) else None
        if not tasks:
            continue
        if mq and not (mq & _stems(str(note.get("title") or ""))):
            continue
        cand = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            hay = _stems(f"{t.get('task') or ''} {t.get('assignee') or ''}")
            score = len(q & hay) / len(q) if q else 1.0
            if score > 0:
                cand.append((score, str(t.get("id") or "")))
        if not cand:
            continue
        state = {x["id"]: x for x in meeting_tasks.rows(note)["items"]}
        for score, iid in cand:
            x = state.get(iid)
            if not x:
                continue
            hits.append((score, {
                "task": x["task"], "status": x["label"], "responsible": x["who"] or "не назначен",
                "due": x["due"] or "", "decided_by": x["by"] if x["code"] not in ("open",) else "",
                "meeting": str(note.get("title") or ""), "meeting_date": str(note.get("created_at") or "")[:10],
                "url": note_webapp_url(int(note["id"])),
            }))
    hits.sort(key=lambda p: -p[0])
    best = hits[0][0] if hits else 0
    out = [h for s, h in hits if s >= best * 0.6][:12] if q else [h for _, h in hits][:30]
    res: dict[str, Any] = {"items": out, "total": len(hits)}
    if not hits:
        res["note"] = "В таблицах задач встреч за этот период ничего похожего — попробуй tasks_search (задачи Лео) или другие слова."
    elif q and len(out) > 1:
        res["note"] = "Похожих пунктов несколько — если вопрос про один, уточни у пользователя, какой."
    return _ok(res)


EXECUTORS["meeting_tasks_search"] = tool_meeting_tasks_search
EXECUTORS["meeting_materials"] = tool_meeting_materials
EXECUTORS["person_history"] = tool_person_history
EXECUTORS["tasks_search"] = tool_tasks_search


def tool_commitments(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.lib.summary_normalize import match_person
    from assistant.services import focus_items

    direction = str(args.get("direction") or "any")
    status = str(args.get("status") or "open")
    items = focus_items.commitments(
        ctx.uid, direction="" if direction == "any" else direction, status="" if status == "any" else status, limit=200
    )
    who = str(args.get("who") or "").strip()
    if who:
        items = [c for c in items if c["who"] and match_person(c["who"], [{"name": who, "aliases": []}])
                 or who.lower() in str(c["who"]).lower()]
    labels = {"mine": "за мной", "theirs": "за другими", "unknown": "исполнитель не ясен"}
    out = [
        {
            "text": c["text"], "who": c["who"], "direction": labels.get(c["direction"], c["direction"]),
            "status": c["status"], "deadline": c["deadline"] or c["due_iso"],
            "sources": [s.get("label") or s.get("title") for s in c["sources"]][:3],
            **({"quote": c["quote"][:200]} if c["quote"] else {}),
            **({"task_id": c["link"].get("task_id")} if (c["link"] or {}).get("task_id") else {}),
        }
        for c in items[:40]
    ]
    return _ok({"commitments": out, "total": len(items)})


EXECUTORS["commitments"] = tool_commitments


def tool_memory_search(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.lib import embed_client
    from assistant.services import semantic_memory

    if not embed_client.enabled():
        return _err("Поиск по памяти не включён")
    try:
        hits = semantic_memory.search(ctx.uid, str(args.get("query") or ""), k=8)
    except Exception as e:
        print(f"[gpt_calendar] memory_search uid={ctx.uid} err={e!r}")
        return _err("Поиск по памяти сейчас недоступен")
    res: dict[str, Any] = {"results": hits, "total": len(hits)}
    try:
        st = semantic_memory.status(ctx.uid)
        if st.get("total") and st["indexed"] < st["total"]:
            # честно сказать пользователю, что часть материалов ещё не в памяти
            res["coverage"] = {
                "indexed": st["indexed"], "total": st["total"], "not_yet_searchable": st["pending"],
                "note": "Часть материалов ещё индексируется — если ответа нет, скажи об этом пользователю.",
            }
    except Exception as e:
        print(f"[gpt_calendar] memory status uid={ctx.uid} err={e!r}")
    return _ok(res)


EXECUTORS["memory_search"] = tool_memory_search


_NOTES_FIND_FILLER = re.compile(
    r"^(дай|дайте|найди|найдите|открой|покажи|скинь|пришли|ссылк\w*|заметк\w*|документ\w*|транскрипц\w*|на|мне|пожалуйста|мою|мой|моя|про|где)$"
)


def tool_notes_find(ctx: GptCalendarContext, args: dict[str, Any]) -> str:
    """Заметки по названию: обычные, транскрипции и документы Базы знаний — со ссылками."""
    from assistant.lib.kb_search import extract_note_search_query, score_note_against_query
    from assistant.lib.telegram_html import html_to_plain
    from assistant.stores import notes as notes_store
    from assistant.stores.note_members import note_webapp_url

    raw = str(args.get("query") or "").strip()
    q = extract_note_search_query(raw) or raw
    # «дай ссылку на заметку Вебапп» → «Вебапп»: служебные слова мешают совпадению с названием.
    q = " ".join(w for w in q.split() if not _NOTES_FIND_FILLER.match(w.strip("«»\"',.!?").lower())) or q
    if not q:
        return _err("Укажите название заметки")
    uid = int(ctx.uid)
    pools = [("note", notes_store.list_notes(uid, limit=500))]
    for kind, loader in (("transcription", lambda: notes_store.list_transcription_notes(uid, limit=500)),
                         ("knowledge", lambda: notes_store.list_knowledge_notes(uid))):
        try:
            pools.append((kind, loader()))
        except Exception as e:
            print(f"[gpt_calendar] notes_find {kind} uid={uid} err={e!r}")
    scored = []
    seen: set[int] = set()
    for kind, rows in pools:
        for n in rows:
            nid = int(n.get("id") or 0)
            if not nid or nid in seen:
                continue
            seen.add(nid)
            title = str(n.get("title") or "")
            body = str(n.get("body") or n.get("description") or "")[:4000]
            try:
                plain = html_to_plain(body) if "<" in body else body
            except Exception:
                plain = body
            score = score_note_against_query(q, title, plain)
            if score > 0:
                scored.append((score, str(n.get("updated_at") or ""), {"id": nid, "title": title or "Без названия", "kind": kind,
                                                                     "updated": str(n.get("updated_at") or "")[:10],
                                                                     "url": note_webapp_url(nid)}))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    items = [x[2] for x in scored[:8]]
    return _ok({"notes": items, "total": len(items)} if items else {"notes": [], "note": "Заметок с таким названием не нашлось"})


EXECUTORS["notes_find"] = tool_notes_find


def active_tools(uid: Optional[int] = None) -> list[dict[str, Any]]:
    """Инструменты для модели: без выключенных функций (memory_search — только с SEMANTIC_SEARCH_ENABLED,
    mail_* — только у пользователя с подключённой почтой)."""
    from assistant.lib import embed_client
    from assistant.services import mail_tools

    off: set[str] = set()
    if not embed_client.enabled():
        off.add("memory_search")
    if uid is None or not mail_tools.has_mail(uid):
        off.update(mail_tools.MAIL_TOOL_NAMES)
    return [t for t in TOOLS if t["function"]["name"] not in off]


from assistant.services import mail_tools as _mail_tools  # noqa: E402,F401 — регистрирует mail_* в TOOLS/EXECUTORS
