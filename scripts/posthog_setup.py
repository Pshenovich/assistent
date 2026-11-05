"""Настройка PostHog для продуктовой аналитики Орбиты (повторный запуск безопасен).

Создаёт/обновляет:
- фильтр своих аккаунтов (person.internal = true) в «Filter out internal and test users»;
- действие «Создал материал» (заметка / транскрипция) для воронки активации;
- дашборд «Орбита — продукт» с отчётами: DAU/WAU/MAU, Retention, время, платформы,
  функции, стили презентаций, модели, функции бота, дайджесты, Фокус, источники,
  воронка активации.

События шлёт сервер (assistant/lib/analytics_forward.py). Отчёты матчатся по имени
и обновляются на месте.

Запуск: .venv/bin/python scripts/posthog_setup.py
Нужно в .env: POSTHOG_PERSONAL_API_KEY (права: insight, dashboard, action, project).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

HOST = os.getenv("POSTHOG_APP_HOST", "https://eu.posthog.com").rstrip("/")
DASHBOARD = "Орбита — продукт"
MARK = "[orbita-setup]"

# Активность человека — любое событие, кроме фоновых (доставка дайджеста и т. п.).
HUMAN = [{"key": "platform", "value": ["server"], "operator": "is_not", "type": "event"}]
D30 = {"date_from": "-30d"}


def _key() -> str:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    k = os.getenv("POSTHOG_PERSONAL_API_KEY", "").strip()
    if not k:
        sys.exit("Нет POSTHOG_PERSONAL_API_KEY в .env")
    return k


def api(method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        HOST + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — адрес PostHog
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} → {e.code}: {e.read()[:500]!r}") from e


def ev(event: str | None, name: str, math: str = "total", props: list | None = None, **extra: Any) -> dict:
    node = {"kind": "EventsNode", "event": event, "name": name, "custom_name": name, "math": math}
    if props:
        node["properties"] = props
    node.update(extra)
    return node


def prop(key: str, value: Any, operator: str = "exact") -> dict:
    return {"key": key, "value": value if isinstance(value, list) else [value], "operator": operator, "type": "event"}


def trends(series: list, *, display: str = "ActionsLineGraph", interval: str = "day", date: dict | None = None,
           breakdown: str | None = None, formula: str | None = None, compare: bool = False) -> dict:
    src: dict[str, Any] = {
        "kind": "TrendsQuery",
        "series": series,
        "interval": interval,
        "dateRange": date or D30,
        "filterTestAccounts": True,
        "trendsFilter": {"display": display, **({"formula": formula} if formula else {})},
    }
    if breakdown:
        src["breakdownFilter"] = {"breakdown": breakdown, "breakdown_type": "event", "breakdown_limit": 15}
    if compare:
        src["compareFilter"] = {"compare": True}
    return {"kind": "InsightVizNode", "source": src}


def retention(breakdown: str | None = None) -> dict:
    src: dict[str, Any] = {
        "kind": "RetentionQuery",
        "dateRange": {"date_from": "-63d"},
        "filterTestAccounts": True,
        "properties": HUMAN,
        "retentionFilter": {
            "period": "Week",
            "totalIntervals": 9,
            "retentionType": "retention_first_time",
            "targetEntity": {"id": None, "name": "All events", "type": "events"},
            "returningEntity": {"id": None, "name": "All events", "type": "events"},
        },
    }
    if breakdown:
        src["breakdownFilter"] = {"breakdown": breakdown, "breakdown_type": "person"}
    return {"kind": "InsightVizNode", "source": src}


def funnel(steps: list, window_days: int = 14) -> dict:
    return {
        "kind": "InsightVizNode",
        "source": {
            "kind": "FunnelsQuery",
            "series": steps,
            "dateRange": {"date_from": "-60d"},
            "filterTestAccounts": True,
            "funnelsFilter": {"funnelWindowInterval": window_days, "funnelWindowIntervalUnit": "day"},
        },
    }


FEATURES = [
    ("note.create", "Заметки", None),
    ("sheet.edit", "Правка заметок и листов", None),
    ("presentation.generate", "Презентации", None),
    ("sheet.create", "Доски руками", [prop("kind", "board")]),
    ("board.ai_apply", "Доски ИИ", None),
    ("ai.ask", "ИИ в заметке", [prop("context", "note")]),
    ("ai.ask", "Чаты с ИИ", [prop("context", "chat")]),
    ("transcription.create", "Транскрипции", None),
    ("summary.create", "Саммари", None),
    ("focus.view", "Фокус", None),
    ("digest.view", "Дайджест чатов", [prop("kind", "chats")]),
    ("digest.view", "Почта", [prop("kind", "mail")]),
    ("digest.view", "Дайджест рынка", [prop("kind", "market")]),
    ("kb.add_doc", "База знаний", None),
    ("task.create", "Задачи", None),
    ("calendar.event_create", "Встречи в календаре", None),
    ("contact.create", "Контакты", None),
]


def insights(action_id: int) -> list[tuple[str, str, dict]]:
    created = {"kind": "ActionsNode", "id": action_id, "name": "Создал материал"}
    return [
        ("Активные: DAU / WAU / MAU", "Уникальные люди с действием (без фоновых событий).", trends([
            ev(None, "DAU", "dau", HUMAN), ev(None, "WAU", "weekly_active", HUMAN), ev(None, "MAU", "monthly_active", HUMAN),
        ], date={"date_from": "-90d"})),
        ("Retention по неделям", "Доля вернувшихся на N-й неделе после первого действия.", retention()),
        ("Retention по первой платформе", "Удержание в разрезе платформы первого входа.", retention("first_platform")),
        ("Минут в продукте на человека в день", "Сумма длительностей сессий / активные люди.", trends([
            ev("session", "Минуты", "sum", math_property="duration_min"), ev("session", "Люди", "dau"),
        ], formula="A / B")),
        ("Длина сессии (медиана, мин)", "", trends([ev("session", "Медиана", "median", math_property="duration_min")])),
        ("Сессий на человека в день", "", trends([ev("session", "Сессии"), ev("session", "Люди", "dau")], formula="A / B")),
        ("Платформы: люди", "Где работают: веб, PWA, миниапп, бот.", trends(
            [ev(None, "Люди", "dau", HUMAN)], display="ActionsPie", breakdown="platform")),
        ("Платформы: время", "Минуты в сессиях по платформам.", trends(
            [ev("session", "Минуты", "sum", math_property="duration_min")], display="ActionsBarValue", breakdown="platform")),
        ("Функции: сколько людей за 30 дней", "Уникальные пользователи функции.", trends(
            [ev(e, n, "dau", p) for e, n, p in FEATURES], display="ActionsBarValue")),
        ("Функции: действий на человека", "Среднее число действий на пользователя функции.", trends(
            [ev(e, n, "avg_count_per_actor", p) for e, n, p in FEATURES], display="ActionsBarValue")),
        ("Функции по неделям", "Уникальные пользователи по неделям.", trends(
            [ev(e, n, "dau", p) for e, n, p in FEATURES[:10]], interval="week", date={"date_from": "-90d"})),
        ("Первое использование функций", "Какие функции люди пробуют.", trends(
            [ev("feature.first_use", "Первое использование")], display="ActionsBarValue", breakdown="feature")),
        ("Стили презентаций", "Новые презентации по стилю.", trends(
            [ev("presentation.generate", "Создано", props=[prop("action", "create")])], display="ActionsPie", breakdown="theme")),
        ("Модели: какая ответила", "Ответы ИИ по фактической модели.", trends(
            [ev("ai.ask", "Ответы")], display="ActionsBarValue", breakdown="model_used")),
        ("Модели: что выбрано", "Выбор в интерфейсе (leo/auto — «Авто»).", trends(
            [ev("ai.ask", "Ответы")], display="ActionsPie", breakdown="model_requested")),
        ("Где спрашивают ИИ", "Заметка / чат / без контекста.", trends(
            [ev("ai.ask", "Вопросы")], display="ActionsPie", breakdown="context")),
        ("Бот: команды", "", trends([ev("bot.command", "Команды")], display="ActionsBarValue", breakdown="name")),
        ("Бот: навыки", "Куда ушёл текст: задача, напоминание, календарь, чат с ИИ…", trends(
            [ev("bot.skill", "Навык")], display="ActionsBarValue", breakdown="skill")),
        ("Бот: голос, файлы, кнопки", "", trends([
            ev("bot.voice", "Голосовые"), ev("bot.document", "Файлы/аудио/видео"),
            ev("bot.callback", "Кнопки"), ev("bot.message", "Текст"),
        ], display="ActionsBarValue")),
        ("Транскрипции по источнику", "", trends(
            [ev("transcription.create", "Транскрипции")], display="ActionsPie", breakdown="source")),
        ("Дайджесты: доставлено и открыто", "Читают ли дайджесты и брифы.", trends([
            ev("digest.delivered", "Доставлено", "dau"), ev("digest.view", "Открыли", "dau"),
        ], display="ActionsBarValue")),
        ("Дайджесты: доставлено по видам", "", trends(
            [ev("digest.delivered", "Доставлено", "dau")], display="ActionsBarValue", breakdown="kind")),
        ("Фокус: сделано и отклонено", "Качество подсказок Фокуса.", trends([
            ev("focus.done", "Сделано"), ev("focus.dismiss", "Отклонено"),
        ], display="ActionsBarValue")),
        ("ИИ правит или человек", "Правки листов: manual / ai.", trends(
            [ev("sheet.edit", "Правки")], display="ActionsPie", breakdown="source")),
        ("Источники прихода (/start)", "Метка из t.me/<бот>?start=<метка>.", trends(
            [ev("bot.start", "Старты", "dau")], display="ActionsBarValue", breakdown="ref")),
        ("Команды и шаринг", "Орги, приглашения, общий доступ.", trends([
            ev("org.create", "Создали орг"), ev("org.join", "Вступили в орг"), ev("org.invite", "Пригласили"),
            ev("note.share", "Поделились заметкой"), ev("chat.share", "Позвали в чат"),
        ], display="ActionsBarValue")),
        ("Интеграции", "Что подключают.", trends(
            [ev("integration.connect", "Подключения", "dau")], display="ActionsBarValue", breakdown="provider")),
        ("Онбординг", "Шаги интро / команда / тур.", trends(
            [ev("onboarding.step", "Шаг", "dau")], display="ActionsBarValue", breakdown="step")),
        ("Знакомство в боте: что выбирают", "Кнопка на /start: day / talk / chats.", trends(
            [ev("onboarding.pick", "Выбор", "dau")], display="ActionsPie", breakdown="choice")),
        ("Знакомство в боте: воронка", "/start → выбрал боль → первый результат → следующая сессия, окно 14 дней.", funnel([
            ev("bot.start", "/start"), ev("onboarding.pick", "Выбрал сценарий"),
            ev("onboarding.first_value", "Первый результат"), ev("session", "Следующая сессия"),
        ])),
        ("Воронка активации", "Создал материал → спросил ИИ → вернулся (новая сессия), окно 14 дней.", funnel([
            created, ev("ai.ask", "Спросил ИИ"), ev("session", "Следующая сессия"),
        ])),
    ]


def ensure_action(pid: int) -> int:
    name = "Создал материал"
    for a in api("GET", f"/api/projects/{pid}/actions/?limit=200")["results"]:
        if a["name"] == name:
            return int(a["id"])
    body = {"name": name, "description": MARK + " заметка или транскрипция",
            "steps": [{"event": "note.create"}, {"event": "transcription.create"}]}
    return int(api("POST", f"/api/projects/{pid}/actions/", body)["id"])


def ensure_internal_filter(pid: int, project: dict) -> None:
    filters = list(project.get("test_account_filters") or [])
    if any(f.get("key") == "internal" for f in filters):
        return
    filters.append({"key": "internal", "value": ["true"], "operator": "is_not", "type": "person"})
    api("PATCH", f"/api/projects/{pid}/", {"test_account_filters": filters, "test_account_filters_default_checked": True})
    print("фильтр своих аккаунтов: internal = true исключаются")


def ensure_dashboard(pid: int) -> int:
    for d in api("GET", f"/api/projects/{pid}/dashboards/?limit=200")["results"]:
        if d["name"] == DASHBOARD and not d.get("deleted"):
            return int(d["id"])
    body = {"name": DASHBOARD, "description": MARK + " события шлёт сервер; свои аккаунты отфильтрованы", "pinned": True}
    return int(api("POST", f"/api/projects/{pid}/dashboards/", body)["id"])


def main() -> None:
    project = api("GET", "/api/projects/@current/")
    pid = int(project["id"])
    print(f"проект {project['name']} ({pid})")
    ensure_internal_filter(pid, project)
    action_id = ensure_action(pid)
    dash = ensure_dashboard(pid)
    existing = {
        i["name"]: int(i["id"])
        for i in api("GET", f"/api/projects/{pid}/insights/?dashboards=[{dash}]&limit=200")["results"]
        if not i.get("deleted")
    }
    ok = failed = 0
    for name, desc, query in insights(action_id):
        body = {"name": name, "description": desc, "query": query, "dashboards": [dash], "saved": True}
        try:
            if name in existing:
                api("PATCH", f"/api/projects/{pid}/insights/{existing[name]}/", body)
            else:
                api("POST", f"/api/projects/{pid}/insights/", body)
            ok += 1
        except RuntimeError as e:
            failed += 1
            print(f"  ✗ {name}: {e}")
    print(f"отчётов: {ok} готово, {failed} с ошибкой")
    print(f"дашборд: {HOST}/project/{pid}/dashboard/{dash}")


if __name__ == "__main__":
    KEY = _key()
    main()
