"""Задачи в проектах: проект при создании и правке, участник проекта видит задачу (только смотрит), Лео."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from tests.test_canvas_endpoints import _headers
from tests.test_posted_tasks import ASSIGNEE, OWNER, TZ, client, sent, store  # noqa: F401

VIEWER = 44


@pytest.fixture()
def projects(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.stores import notes as notes_store
    from assistant.stores import orgs_store
    from assistant.stores import projects as projects_store

    notes_store._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(orgs_store, "orgs_for_user", lambda uid: [])
    yield projects_store
    notes_store._CONN = None  # type: ignore[attr-defined]


def _body(**kw):
    return {"title": "Созвониться с БГПТ", "start": datetime(2026, 11, 20, 15, 0, tzinfo=TZ).isoformat(), **kw}


def test_create_task_into_project_and_member_views_read_only(client, projects):
    p = projects.create(OWNER, "БГПТ")
    r = client.post("/api/miniapp/calendar/tasks", json=_body(project_id=p["id"]), headers=_headers(OWNER))
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["projects"] == [{"id": p["id"], "title": "БГПТ"}]
    tid = task["id"]
    # не участник — задачи не видит
    assert client.get(f"/api/miniapp/calendar/tasks/{tid}", headers=_headers(VIEWER)).status_code == 404
    projects.set_member(OWNER, p["id"], f"u:{VIEWER}", "viewer")
    seen = client.get(f"/api/miniapp/calendar/tasks/{tid}", headers=_headers(VIEWER))
    assert seen.status_code == 200
    assert seen.json()["task"]["read_only"] is True
    # но поменять не может
    patch = client.patch(f"/api/miniapp/calendar/tasks/{tid}", json={"title": "Взлом"}, headers=_headers(VIEWER))
    assert patch.status_code == 404


def test_create_into_foreign_project_refused(client, projects):
    p = projects.create(ASSIGNEE, "Чужой")
    r = client.post("/api/miniapp/calendar/tasks", json=_body(project_id=p["id"]), headers=_headers(OWNER))
    assert r.status_code == 400
    projects.set_member(ASSIGNEE, p["id"], f"u:{OWNER}", "viewer")  # только смотрит
    r = client.post("/api/miniapp/calendar/tasks", json=_body(project_id=p["id"]), headers=_headers(OWNER))
    assert r.status_code == 400


def test_patch_moves_task_between_projects(client, projects):
    a = projects.create(OWNER, "A")
    b = projects.create(OWNER, "B")
    tid = client.post("/api/miniapp/calendar/tasks", json=_body(project_id=a["id"]), headers=_headers(OWNER)).json()["task"]["id"]
    r = client.patch(f"/api/miniapp/calendar/tasks/{tid}", json={"project_id": b["id"]}, headers=_headers(OWNER))
    assert r.status_code == 200, r.text
    assert [x["title"] for x in r.json()["task"]["projects"]] == ["B"]
    r = client.patch(f"/api/miniapp/calendar/tasks/{tid}", json={"project_id": 0}, headers=_headers(OWNER))
    assert r.json()["task"]["projects"] == []


def test_leo_puts_task_into_project_by_name(store, projects, monkeypatch):
    from assistant.services import gpt_calendar_tools as gcal

    projects.create(OWNER, "БГПТ")
    projects.create(OWNER, "Поддержка клиентов")
    projects.create(OWNER, "Поддержка партнёров")
    ctx = gcal.GptCalendarContext(uid=OWNER)
    monkeypatch.setattr(gcal, "_tz", lambda uid: TZ)
    start = "2026-11-20T15:00"
    out = json.loads(gcal.tool_task_create(ctx, {"title": "Счёт", "start": start, "project": "бгпт"}))
    assert out["project"] == "БГПТ"
    assert projects.ids_with_item("task", out["task_id"])
    out = json.loads(gcal.tool_task_create(ctx, {"title": "Ответить", "start": start, "project": "поддержка"}))
    assert out["project"] is None and "несколько" in out["project_note"]
    out = json.loads(gcal.tool_task_create(ctx, {"title": "Ещё", "start": start, "project": "нет такого"}))
    assert out["project"] is None
    assert projects.ids_with_item("task", out["task_id"]) == []
