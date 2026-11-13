"""Наследование проекта: встреча/серия/тема → саммари → задачи; подзадачи; задача от Лео в документе проекта."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from tests.test_posted_tasks import ASSIGNEE, OWNER, TZ, store  # noqa: F401

START = datetime(2026, 11, 20, 15, 0, tzinfo=TZ)
OTHER = 55


@pytest.fixture()
def env(store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("MEETING_AUTO_SHARE", "0")
    from assistant.stores import meeting_links, meeting_topics, orgs_store
    from assistant.stores import notes as notes_store
    from assistant.stores import projects as projects_store

    notes_store._CONN = None  # type: ignore[attr-defined]
    meeting_links._CONN = None  # type: ignore[attr-defined]
    meeting_topics._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(orgs_store, "orgs_for_user", lambda uid: [])
    from assistant.services import meeting_share

    monkeypatch.setattr(meeting_share, "schedule_auto_share", lambda *a, **k: None)
    yield {"projects": projects_store, "notes": notes_store, "links": meeting_links, "tasks": store}
    notes_store._CONN = None  # type: ignore[attr-defined]
    meeting_links._CONN = None  # type: ignore[attr-defined]
    meeting_topics._CONN = None  # type: ignore[attr-defined]


def _summary(env, uid, title):
    return env["notes"].create_note(uid, title, "<p>итоги</p>")


def _task(env, uid, title, note_id="", parent_id=None):
    return env["tasks"].create_task(uid, title=title, start_at=START, tz=TZ, note_id=str(note_id), parent_id=parent_id)


def _kinds(env, pid):
    return sorted((i["item_kind"], i["item_id"]) for i in env["projects"].get(pid)["items"])


def test_meeting_then_summary_then_tasks(env):
    P = env["projects"]
    p = P.create(OWNER, "БГПТ")
    P.add_items(OWNER, p["id"], [("event", "primary:ev1", "Созвон с БГПТ")])
    # саммари появилось позже и привязалось к встрече — попало в проект
    n = _summary(env, OWNER, "Созвон с БГПТ")
    env["links"].link(OWNER, n["id"], {"calendar_id": "primary", "event_id": "ev1", "summary": "Созвон с БГПТ"})
    assert ("note", str(n["id"])) in _kinds(env, p["id"])
    # задача из саммари — тоже, в том числе поставленная другим человеком
    t = _task(env, OTHER, "Прислать КП", note_id=n["id"])
    assert ("task", str(t["id"])) in _kinds(env, p["id"])
    # и участник проекта может её прочитать: добавлена от имени постановщика
    from assistant.services import project_access

    P.set_member(OWNER, p["id"], f"u:{ASSIGNEE}", "viewer")
    assert project_access.viewable_task(ASSIGNEE, t["id"])["project_view"] is True


def test_adding_meeting_pulls_existing_summary_and_tasks(env):
    P = env["projects"]
    n = _summary(env, OWNER, "Планёрка")
    env["links"].link(OWNER, n["id"], {"calendar_id": "primary", "event_id": "ev2", "summary": "Планёрка"})
    t = _task(env, OWNER, "Обновить план", note_id=n["id"])
    p = P.create(OWNER, "Внутреннее")
    P.add_items(OWNER, p["id"], [("event", "primary:ev2", "Планёрка")])
    assert _kinds(env, p["id"]) == sorted([("event", "primary:ev2"), ("note", str(n["id"])), ("task", str(t["id"]))])


def test_series_takes_past_and_future_occurrences(env):
    P = env["projects"]
    past = _summary(env, OWNER, "1:1 прошлый")
    env["links"].link(OWNER, past["id"], {"calendar_id": "primary", "event_id": "rec9_20261106T100000Z", "summary": "1:1"})
    other = _summary(env, OWNER, "Другая встреча")
    env["links"].link(OWNER, other["id"], {"calendar_id": "primary", "event_id": "rec99_20261106T100000Z", "summary": "1:1"})
    p = P.create(OWNER, "1:1 с Артёмом")
    P.add_items(OWNER, p["id"], [("series", "primary:rec9", "1:1")])
    assert ("note", str(past["id"])) in _kinds(env, p["id"])
    assert ("note", str(other["id"])) not in _kinds(env, p["id"])  # rec99 — не та серия
    future = _summary(env, OWNER, "1:1 следующий")
    env["links"].link(OWNER, future["id"], {"calendar_id": "primary", "event_id": "rec9_20261120T100000Z", "summary": "1:1"})
    assert ("note", str(future["id"])) in _kinds(env, p["id"])
    # серия встреч Орбиты: orb-5 и orb-5_<время>
    P.add_items(OWNER, p["id"], [("series", "orbita:orb-5", "Синк")])
    orb = _summary(env, OWNER, "Синк")
    env["links"].link(OWNER, orb["id"], {"calendar_id": "orbita", "event_id": "orb-5_20261121T090000Z", "summary": "Синк"})
    assert ("note", str(orb["id"])) in _kinds(env, p["id"])


def test_topic_brings_meetings_by_title(env):
    P = env["projects"]
    from assistant.stores import meeting_topics

    topic_id = meeting_topics.confirm(OWNER, [{"kind": "title", "value": meeting_topics.norm_title("Созвон БГПТ"),
                                               "label": "Созвон БГПТ"}], name="БГПТ")
    if isinstance(topic_id, dict):
        topic_id = topic_id.get("topic_id") or topic_id.get("id")
    p = P.create(OWNER, "БГПТ")
    P.add_items(OWNER, p["id"], [("topic", str(topic_id), "БГПТ")])
    n = _summary(env, OWNER, "Созвон БГПТ")
    env["links"].link(OWNER, n["id"], {"calendar_id": "primary", "event_id": "x1", "summary": "Созвон БГПТ"})
    assert ("note", str(n["id"])) in _kinds(env, p["id"])


def test_subtask_and_archived_project(env):
    P = env["projects"]
    p = P.create(OWNER, "П")
    top = _task(env, OWNER, "Запуск")
    P.add_items(OWNER, p["id"], [("task", str(top["id"]), "")])
    sub = _task(env, OWNER, "Тексты", parent_id=top["id"])
    assert ("task", str(sub["id"])) in _kinds(env, p["id"])
    P.update(OWNER, p["id"], {"archived": True})
    sub2 = _task(env, OWNER, "Домен", parent_id=top["id"])
    assert ("task", str(sub2["id"])) not in _kinds(env, p["id"])  # архив не растёт


def test_leo_task_from_open_project_note(env, monkeypatch):
    P = env["projects"]
    from assistant.services import gpt_calendar_tools as gcal

    monkeypatch.setattr(gcal, "_tz", lambda uid: TZ)
    n = _summary(env, OWNER, "План запуска")
    p = P.create(OWNER, "Запуск", items=[("note", str(n["id"]), "")])
    ctx = gcal.GptCalendarContext(uid=OWNER)
    ctx.open_note_id = str(n["id"])
    out = json.loads(gcal.tool_task_create(ctx, {"title": "Свёрстать", "start": "2026-11-20T15:00"}))
    assert out["project"] == "Запуск"
    assert ("task", str(out["task_id"])) in _kinds(env, p["id"])
