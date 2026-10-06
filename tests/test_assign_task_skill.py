"""Парсинг фраз постановки Leo-задачи другому человеку."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from assistant.skills.assign_task import (
    is_assign_task_request,
    parse_assign_task_request,
)


@pytest.mark.parametrize(
    "text,ok",
    [
        ("поставь задачу Ульяне надо сделать презентацию завтра к 10:00", True),
        ("поставь ульяне на сегодня задачу сделать презентацию", True),
        ("Лев, поставь Ульяне задачу собрать отчёт", True),
        ("закинь Артему в задаче собрать отчет по выполненным работам", True),
        ("передай Андрею задачу посчитать метрики по продукту", True),
        ("создай задачу Ульяне протестировать веб приложение", True),
        ("поставь Ульяне на сегодня сделать презентацию", True),
        ("поставь встречу с Андреем завтра", False),
        ("создай задачу в битрикс для Ивана", False),
        ("напомни купить молоко", False),
    ],
)
def test_is_assign_task_request(text, ok):
    assert is_assign_task_request(text) is ok


def test_long_text_becomes_description():
    from assistant.skills.assign_task import resolve_title_and_description, word_count

    short = "Сделать отчёт"
    title, desc = resolve_title_and_description(short)
    assert title.startswith("С")
    assert desc == ""

    long = "протестировать веб приложение на предмет ошибок и багов"
    assert word_count(long) > 5
    title2, desc2 = resolve_title_and_description(long)
    assert desc2 == long
    assert word_count(title2) <= 8
    assert title2[0].isupper()


def test_parse_examples():
    a = parse_assign_task_request(
        "поставь задачу Ульяне надо сделать презентацию завтра к 10:00"
    )
    assert a is not None
    assert a["assignee_name"].lower().startswith("ульян")
    assert "презентацию" in a["title"].lower()

    b = parse_assign_task_request(
        "закинь Артему в задаче собрать отчет по выполненным работам"
    )
    assert b is not None
    assert b["assignee_name"].lower().startswith("артем")
    assert "отчет" in b["title"].lower()

    c = parse_assign_task_request(
        "передай Андрею задачу посчитать метрики по продукту"
    )
    assert c is not None
    assert c["assignee_name"].lower().startswith("андрей") or c[
        "assignee_name"
    ].lower().startswith("андрею")
    assert "метрик" in c["title"].lower()

    d = parse_assign_task_request(
        "поставь ульяне на сегодня задачу сделать презентацию"
    )
    assert d is not None
    assert d["assignee_name"].lower().startswith("ульян")
    assert "презентац" in d["title"].lower()
    assert "сегодня" not in d["title"].lower()
    assert "задач" not in d["title"].lower()


def test_capitalize_and_urgency():
    from assistant.skills.assign_task import (
        apply_urgency_emoji,
        capitalize_title,
        strip_urgency_prefix,
    )

    assert capitalize_title("тестирование веб-приложения") == "Тестирование веб-приложения"
    assert capitalize_title("ABC") == "ABC"
    assert strip_urgency_prefix("🚨 Тест") == "Тест"
    assert apply_urgency_emoji("тест", "❗️") == "❗️ тест"
    assert apply_urgency_emoji("🚨 старое", "😴") == "😴 старое"


def test_bitrix_does_not_steal_assign():
    from assistant.nlu.regex import parse_bitrix_intent, regex_route

    text = "поставь ульяне на сегодня задачу сделать презентацию"
    assert parse_bitrix_intent(text) is None
    r = regex_route(text)
    assert r is not None and r.skill == "assign_task"

    text2 = "создай задачу Ульяне протестировать веб приложение полностью"
    assert parse_bitrix_intent(text2) is None
    r2 = regex_route(text2)
    assert r2 is not None and r2.skill == "assign_task"


@pytest.fixture()
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "calendar_tasks.sqlite"
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(db))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]


def test_delegated_hidden_from_owner_calendar_and_posted(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 10, 7, 15, 0, tzinfo=tz)
    personal = store.create_task(11, title="Себе", start_at=start, tz=tz)
    delegated = store.create_task(
        11,
        title="Ульяне",
        start_at=start,
        assignee_user_id=22,
        assignee_name="Ульяна",
        tz=tz,
    )
    all_day = store.create_task(
        11,
        title="Без времени",
        start_at=start,
        assignee_user_id=22,
        all_day=True,
        tz=tz,
    )

    assert store.task_is_delegated(delegated) is True
    assert store.task_is_delegated(personal) is False
    assert store.task_visible_in_calendar(delegated, 11) is False
    assert store.task_visible_in_calendar(delegated, 22) is True
    assert store.task_visible_in_calendar(personal, 11) is True

    posted = store.list_posted_tasks(11)
    assert {t["id"] for t in posted} == {delegated["id"], all_day["id"]}
    assert [t["id"] for t in posted][0] == all_day["id"]  # all-day earlier on day

    ev = store.as_calendar_event(all_day, tz=tz, viewer_id=22)
    assert ev["all_day"] is True
    assert "date" in ev["start"]
    assert "dateTime" not in ev["start"]


def test_push_skips_delegated_for_owner(tasks_db, monkeypatch):
    from assistant.services import google_tasks

    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    row = store.create_task(
        11,
        title="Делегированная",
        start_at=datetime(2026, 10, 7, 15, 0, tzinfo=tz),
        assignee_user_id=22,
        tz=tz,
    )
    called = []

    def _boom(user_id):
        called.append(user_id)
        raise AssertionError("should not open Google for delegated")

    monkeypatch.setattr(google_tasks, "_service", _boom)
    out = google_tasks.push_task(11, row)
    assert out == row
    assert called == []


def test_task_webapp_url():
    from assistant.lib.task_notify import task_title_link_html, task_webapp_url

    url = task_webapp_url(42)
    assert "task=42" in url
    html = task_title_link_html("Hi", 42)
    assert "task=42" in html
    assert "Hi" in html
