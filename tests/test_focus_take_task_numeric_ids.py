"""«В задачу» на карточке Фокуса: id исполнителя из контактов приходит числом — задача всё равно ставится.

Раньше POST /calendar/tasks падал с «Input should be a valid string» (assignee_user_id: 123).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture()
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]


def test_body_accepts_numeric_ids():
    import usage_server as us

    body = us._MiniappCalendarTaskBody(title="x", assignee_user_id=123456789, note_id=42, meeting_item=None)
    assert body.assignee_user_id == "123456789"
    assert body.note_id == "42"


def test_create_task_with_numeric_assignee(tasks_db, monkeypatch: pytest.MonkeyPatch):
    import usage_server as us

    monkeypatch.setattr(us, "_track", lambda *a, **k: None)
    monkeypatch.setattr(us, "_sync_task_google", lambda *a, **k: None)
    monkeypatch.setattr(us, "_find_reusable_note_task", lambda *a, **k: None)
    uid = 123456789
    # Тело как у «В задачу» из стопки Фокуса: исполнитель — я, id числом.
    body = us._MiniappCalendarTaskBody.model_validate(
        {
            "title": "Прислать отчёт",
            "description": "Из «Созвон»",
            "assignee_user_id": uid,
            "assignee_email": "",
            "assignee_name": "Вова",
            "note_id": "",
            "start": "2026-11-10T12:00:00",
            "end": "2026-11-10T13:00:00",
            "all_day": True,
        }
    )
    principal = us._MiniappPrincipal(uid, {"id": uid, "first_name": "Вова"})
    res = asyncio.run(us.miniapp_calendar_tasks_create(body, principal))
    assert res["ok"] is True
    assert res["task"]["title"] == "Прислать отчёт"
    assert str(res["task"]["assignee_user_id"]) == str(uid)
