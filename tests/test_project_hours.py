"""Часы по проектам: встречи и задачи по неделям, деление между проектами, «сколько ушло», видимость."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_posted_tasks import ASSIGNEE, OWNER, TZ, store  # noqa: F401

UTC = timezone.utc


@pytest.fixture()
def env(store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.services import project_hours
    from assistant.stores import meeting_links, orgs_store
    from assistant.stores import notes as notes_store
    from assistant.stores import projects as projects_store

    notes_store._CONN = None  # type: ignore[attr-defined]
    meeting_links._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(orgs_store, "orgs_for_user", lambda uid: [])
    monkeypatch.setattr(orgs_store, "current_org", lambda uid: None)
    monkeypatch.setattr("assistant.services.calendar._tz_for", lambda uid: TZ)
    monkeypatch.setattr("assistant.stores.orbita_events.list_for_user", lambda uid, s, e: [])
    state = {"mirror": {}}
    monkeypatch.setattr("assistant.stores.calendar_mirror.events",
                        lambda uid, s, e: [r for r in state["mirror"].get(str(uid), []) if r["start"] < e and r["end"] > s])
    yield {"projects": projects_store, "tasks": store, "hours": project_hours, "state": state, "notes": notes_store}
    notes_store._CONN = None  # type: ignore[attr-defined]
    meeting_links._CONN = None  # type: ignore[attr-defined]


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)  # пятница; неделя 9–15 ноя
MON = datetime(2026, 11, 9, 0, 0, tzinfo=TZ)


def _meet(eid, start, hours, series="", others=2, title="Встреча"):
    return {"calendar_id": "primary", "event_id": eid, "series_id": series, "title": title, "start": start.astimezone(UTC),
            "end": (start + timedelta(hours=hours)).astimezone(UTC), "kind": "meeting", "others": others,
            "all_day": 0, "my_response": "accepted", "transparent": 0}


def test_meetings_by_project_series_and_split(env):
    P, H = env["projects"], env["hours"]
    a = P.create(OWNER, "БГПТ")
    b = P.create(OWNER, "Поддержка")
    P.add_items(OWNER, a["id"], [("series", "primary:rec1", "Синк")])
    P.add_items(OWNER, a["id"], [("event", "primary:both", "Общая")])
    P.add_items(OWNER, b["id"], [("event", "primary:both", "Общая")])
    env["state"]["mirror"][str(OWNER)] = [
        _meet("rec1_a", MON + timedelta(days=1, hours=10), 1.5, series="rec1"),
        _meet("rec1_prev", MON - timedelta(days=6, hours=-10), 1, series="rec1"),  # прошлая неделя
        _meet("both", MON + timedelta(days=2, hours=12), 2),
        _meet("solo", MON + timedelta(days=3, hours=9), 1),
        _meet("alone", MON + timedelta(days=3, hours=15), 3, others=0),  # без других участников — не встреча
    ]
    rep = H.report(OWNER, now=NOW)
    rows = {r["title"]: r for r in rep["rows"]}
    assert rows["БГПТ"]["meet"] == 2.5 and rows["БГПТ"]["prev"] == 1.0  # 1,5 серии + половина общей
    assert rows["Поддержка"]["meet"] == 1.0
    assert rows["Без проекта"]["meet"] == 1.0
    assert rep["weeks"][-1]["by"][str(a["id"])] == 2.5 and rep["week_label"] == "9–15 ноя"


def test_tasks_spent_calendar_and_no_hours(env):
    from assistant.stores import task_spent

    P, H, T = env["projects"], env["hours"], env["tasks"]
    p = P.create(OWNER, "Поддержка")
    timed = T.create_task(OWNER, title="Ответить клиентам", start_at=MON + timedelta(days=1, hours=14),
                          end_at=MON + timedelta(days=1, hours=16), tz=TZ)
    spent = T.create_task(OWNER, title="Возвраты", start_at=MON + timedelta(days=2), tz=TZ, all_day=True)
    T.update_task(OWNER, spent["id"], done=True)
    task_spent.set_spent(spent["id"], OWNER, task_spent.clean_minutes("1 ч 30 мин"))
    nohours = T.create_task(OWNER, title="Без времени", start_at=MON + timedelta(days=2), tz=TZ, all_day=True)
    T.update_task(OWNER, nohours["id"], done=True)
    P.add_items(OWNER, p["id"], [("task", str(timed["id"]), ""), ("task", str(spent["id"]), "")])
    rep = H.report(OWNER, now=_now_in_week(spent))
    row = next(r for r in rep["rows"] if r["title"] == "Поддержка")
    assert row["task"] == 3.5  # 2 ч в календаре + 1,5 «сколько ушло»
    assert rep["no_hours"] == 1


def _now_in_week(task):
    """«Сейчас» в неделе, когда задачу сдали (done_at — настоящее время теста)."""
    from assistant.stores import calendar_tasks

    with calendar_tasks._LOCK:  # type: ignore[attr-defined]
        done_at = calendar_tasks._conn().execute("SELECT done_at FROM calendar_tasks WHERE id = ?", (task["id"],)).fetchone()[0]  # type: ignore[attr-defined]
    return datetime.fromisoformat(done_at).astimezone(TZ)


def test_other_projects_hidden_and_people_visibility(env):
    P, H = env["projects"], env["hours"]
    foreign = P.create(ASSIGNEE, "Секретный")
    P.add_items(ASSIGNEE, foreign["id"], [("event", "primary:x1", "X")])
    env["state"]["mirror"][str(OWNER)] = [_meet("x1", MON + timedelta(days=1, hours=10), 1)]
    rep = H.report(OWNER, now=NOW)
    assert [r["title"] for r in rep["rows"]] == ["Другие проекты"]
    # чужие часы без прав не видны: просим ASSIGNEE — получаем только себя
    rep2 = H.report(OWNER, now=NOW, people=[str(ASSIGNEE)])
    assert rep2["selected_people"] == [str(OWNER)]
    with pytest.raises(ValueError):
        H.report(OWNER, now=NOW, project_id=foreign["id"])


def test_by_person_and_project_filter(env):
    P, H = env["projects"], env["hours"]
    a = P.create(OWNER, "A")
    P.add_items(OWNER, a["id"], [("event", "primary:e1", "E")])
    env["state"]["mirror"][str(OWNER)] = [_meet("e1", MON + timedelta(days=1, hours=10), 2), _meet("e2", MON + timedelta(days=2, hours=10), 1)]
    rep = H.report(OWNER, now=NOW)
    people = H.by_person(rep)
    assert people[0]["total"] == 3.0 and {x["title"] for x in people[0]["projects"]} == {"A", "Без проекта"}
    only = H.report(OWNER, now=NOW, project_id=a["id"])
    assert [r["title"] for r in only["rows"]] == ["A"]


def test_clean_minutes_and_hint(env):
    from assistant.stores import task_spent

    assert task_spent.clean_minutes("1,5") == 90
    assert task_spent.clean_minutes("45 мин") == 45
    assert task_spent.clean_minutes("2ч 15м") == 135
    assert task_spent.clean_minutes("день") == 480
    assert task_spent.clean_minutes("90") == 90
    assert task_spent.clean_minutes("что-то") is None
    t = {"start_at": "2026-11-10T11:00:00+00:00", "end_at": "2026-11-10T13:00:00+00:00", "all_day": False}
    assert env["hours"].spent_hint(OWNER, t) == {"minutes": 120, "why": "calendar"}
    assert env["hours"].spent_hint(OWNER, {"all_day": True})["minutes"] is None


def test_leo_project_hours_tool(env):
    import json

    from assistant.services import gpt_calendar_tools as gcal

    P = env["projects"]
    a = P.create(OWNER, "БГПТ")
    P.add_items(OWNER, a["id"], [("event", "primary:e1", "E")])
    env["state"]["mirror"][str(OWNER)] = [_meet("e1", datetime.now(TZ).replace(hour=10, minute=0) - timedelta(hours=0), 1.5)]
    out = json.loads(gcal.tool_project_hours(gcal.GptCalendarContext(uid=OWNER), {"project": "бгпт"}))
    assert out["rows"][0]["project"] == "БГПТ" and out["rows"][0]["meetings_h"] == 1.5
    miss = json.loads(gcal.tool_project_hours(gcal.GptCalendarContext(uid=OWNER), {"person": "Ульяна"}))
    assert miss["found"] is False  # чужих часов без прав не видно


def test_spent_saved_on_submit(env, monkeypatch):
    from fastapi.testclient import TestClient

    import usage_server
    from assistant.bot import access_gate
    from assistant.stores import task_spent
    from tests.test_canvas_endpoints import _headers
    from tests.test_posted_tasks import BOT_TOKEN

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_sync_task_google", lambda *a, **k: None)
    monkeypatch.setattr("assistant.lib.telegram_notify.send_message", lambda *a, **k: True)
    c = TestClient(usage_server.app, raise_server_exceptions=False)
    t = env["tasks"].create_task(OWNER, title="Отчёт", start_at=MON + timedelta(days=1, hours=11),
                                 end_at=MON + timedelta(days=1, hours=13), tz=TZ, assignee_user_id=ASSIGNEE, assignee_name="Ульяна")
    hint = c.get(f"/api/miniapp/calendar/tasks/{t['id']}/spent", headers=_headers(ASSIGNEE)).json()
    assert hint["hint"] == {"minutes": 120, "why": "calendar"} and hint["minutes"] is None
    r = c.patch(f"/api/miniapp/calendar/tasks/{t['id']}", json={"done": True, "result": "готово", "spent": "1,5"},
                headers=_headers(ASSIGNEE))
    assert r.status_code == 200, r.text
    assert task_spent.get(t["id"]) == 90
    bad = c.patch(f"/api/miniapp/calendar/tasks/{t['id']}", json={"spent": "непонятно"}, headers=_headers(ASSIGNEE))
    assert bad.status_code == 400


def test_docs_minutes_and_project_scoped_people_and_no_hours(env, monkeypatch, tmp_path):
    from assistant.stores import doc_time

    monkeypatch.setenv("DOC_TIME_DB_PATH", str(tmp_path / "doc_time.sqlite"))
    doc_time._CONN = None  # type: ignore[attr-defined]
    P, H, T = env["projects"], env["hours"], env["tasks"]
    n = env["notes"].create_note(OWNER, "План", "<p>x</p>")
    p = P.create(OWNER, "Контент завод", items=[("note", str(n["id"]), "")])
    other = P.create(OWNER, "Другой")
    base = MON + timedelta(days=1, hours=10)
    for i in range(30):  # 30 активных минут в документе проекта
        doc_time.touch(OWNER, n["id"], at=(base + timedelta(minutes=i)).timestamp())
    doc_time.touch(OWNER, n["id"], at=(base + timedelta(minutes=3)).timestamp())  # та же минута — не дважды
    # сданная без часов задача другого проекта — в отчёт проекта не попадает
    t = T.create_task(OWNER, title="Чужое", start_at=MON + timedelta(days=2), tz=TZ, all_day=True)
    T.update_task(OWNER, t["id"], done=True)
    P.add_items(OWNER, other["id"], [("task", str(t["id"]), "")])
    rep = H.report(OWNER, now=_now_in_week(t), project_id=p["id"])
    row = rep["rows"][0]
    assert row["title"] == "Контент завод" and row["docs"] == 0.5 and row["total"] == 0.5
    assert rep["weeks"][-1]["by_kind"]["docs"] == 0.5
    assert rep["no_hours"] == 0
    assert [x["user_id"] for x in rep["people"]] == [str(OWNER)]
    full = H.report(OWNER, now=_now_in_week(t))
    assert full["no_hours"] == 1 and full["no_hours_tasks"][0]["title"] == "Чужое"
    doc_time._CONN = None  # type: ignore[attr-defined]
