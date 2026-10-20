"""Поле «Результат» у задачи и поиск задач ассистентом (tasks_search)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from assistant.services import gpt_calendar_tools as g

TZ = ZoneInfo("Europe/Moscow")
ME = 11
ULYANA = 22


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    from assistant.stores import calendar_tasks as s
    import assistant.services.calendar as cal

    s._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(cal, "_tz_for", lambda uid: TZ)
    monkeypatch.setattr(g, "_resolve_assignee", lambda ctx, name: "не найден")
    yield s
    s._CONN = None  # type: ignore[attr-defined]


def _ctx() -> g.GptCalendarContext:
    return g.GptCalendarContext(uid=ME)


def test_result_saved_and_kept_on_partial_update(store):
    row = store.create_task(ME, title="Счёт", start_at=datetime(2026, 9, 1, 10, 0, tzinfo=TZ), tz=TZ)
    assert row["result"] == ""
    row = store.update_task(ME, row["id"], result="Счёт оплачен", done=True, tz=TZ)
    assert row["result"] == "Счёт оплачен"
    row = store.update_task(ME, row["id"], title="Счёт клиенту", tz=TZ)
    assert row["result"] == "Счёт оплачен"
    assert store.as_calendar_event(row, tz=TZ)["result"] == "Счёт оплачен"


def test_assignee_can_write_result(store):
    row = store.create_task(
        ME, title="Документы", start_at=datetime(2026, 9, 1, 10, 0, tzinfo=TZ),
        assignee_user_id=ULYANA, assignee_name="Ульяна", tz=TZ,
    )
    row = store.update_task(ULYANA, row["id"], result="Отправила договор", tz=TZ)
    assert row and row["result"] == "Отправила договор"


def test_tasks_search_by_person_case_and_topic(store):
    old = store.create_task(
        ME, title="Подготовить документы для клиента Ромашка",
        start_at=datetime(2026, 3, 2, 10, 0, tzinfo=TZ),
        assignee_user_id=ULYANA, assignee_name="Ульяна Петрова", tz=TZ,
    )
    store.update_task(ME, old["id"], done=True, result="Пакет документов отправлен 5 марта", tz=TZ)
    store.create_task(
        ME, title="Созвон с клиентом", start_at=datetime(2026, 3, 3, 10, 0, tzinfo=TZ),
        assignee_user_id=ULYANA, assignee_name="Ульяна Петрова", tz=TZ,
    )
    store.create_task(
        ME, title="Документы для бухгалтерии", start_at=datetime(2026, 3, 4, 10, 0, tzinfo=TZ),
        assignee_user_id=33, assignee_name="Дмитрий", tz=TZ,
    )
    ctx = _ctx()
    res = json.loads(g.tool_tasks_search(ctx, {"query": "документы клиент", "assignee": "Ульяне"}))
    assert [it["id"] for it in res["items"]][0] == str(old["id"])
    top = res["items"][0]
    assert top["status"] == "выполнена"
    assert top["result"] == "Пакет документов отправлен 5 марта"
    assert top["assignee"] == "Ульяна Петрова"
    assert all("Дмитрий" != it["assignee"] for it in res["items"])
    assert any(s["type"] == "task" and s["id"] == str(old["id"]) for s in ctx.seen_items)


def test_tasks_search_status_filter_and_empty(store):
    store.create_task(ME, title="Купить бумагу", start_at=datetime(2026, 3, 2, 10, 0, tzinfo=TZ), tz=TZ)
    res = json.loads(g.tool_tasks_search(_ctx(), {"query": "бумага", "status": "done"}))
    assert res["items"] == [] and res["note"]
    res = json.loads(g.tool_tasks_search(_ctx(), {"query": "бумагу", "status": "open"}))
    assert len(res["items"]) == 1


def test_tasks_search_registered():
    assert "tasks_search" in g.EXECUTORS
    assert any(t["function"]["name"] == "tasks_search" for t in g.TOOLS)
