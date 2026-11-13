"""Лео и проекты: факты (код), паспорт (кэш), бриф, project_overview, карточки Фокуса."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_posted_tasks import ASSIGNEE, OWNER, TZ, store  # noqa: F401

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def env(store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.stores import notes as notes_store
    from assistant.stores import orgs_store
    from assistant.stores import projects as projects_store

    notes_store._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(orgs_store, "orgs_for_user", lambda uid: [])
    monkeypatch.setattr("assistant.services.project_sources._google_rows", lambda uid, s, e: [])
    monkeypatch.setattr("assistant.services.project_sources._orbita_rows", lambda uid, s, e: [])
    yield {"projects": projects_store, "notes": notes_store, "tasks": store}
    notes_store._CONN = None  # type: ignore[attr-defined]


def _task(env, title, days, done=False, assignee=OWNER):
    t = env["tasks"].create_task(OWNER, title=title, start_at=NOW + timedelta(days=days),
                                 end_at=NOW + timedelta(days=days, hours=1), tz=TZ,
                                 assignee_user_id=assignee, assignee_name={OWNER: "Вова", ASSIGNEE: "Ульяна"}[assignee])
    if done:
        env["tasks"].update_task(OWNER, t["id"], done=True)
    return t


def _project_with_overdue(env, n=3, old=False):
    P = env["projects"]
    p = P.create(OWNER, "БГПТ", lead_user_id=OWNER)
    ids = [_task(env, f"Просрочка {i}", -(10 if old else 1) - i)["id"] for i in range(n)]
    ids.append(_task(env, "Впереди", 3)["id"])
    P.add_items(OWNER, p["id"], [("task", str(i), "") for i in ids])
    return p


def test_facts_count_and_stuck(env):
    from assistant.services import project_facts

    p = _project_with_overdue(env, n=3)
    f = project_facts.facts(OWNER, p["id"])
    assert f["tasks_open"] == 4 and f["overdue_total"] == 3 and f["stuck"] is True
    assert f["overdue"][0]["title"] == "Просрочка 2"  # самая старая — первой
    text = project_facts.text(f)
    assert "Задачи: открыто 4, просрочено 3" in text and "буксует" in text
    assert project_facts.facts(ASSIGNEE, p["id"]) is None  # не участник


def test_one_overdue_is_not_stuck_unless_old(env):
    from assistant.services import project_facts

    p = _project_with_overdue(env, n=1)
    assert project_facts.facts(OWNER, p["id"])["stuck"] is False
    p2 = _project_with_overdue(env, n=1, old=True)
    assert project_facts.facts(OWNER, p2["id"])["stuck"] is True


def test_passport_cached_until_facts_change(env, monkeypatch):
    from assistant.services import project_facts

    calls = []
    monkeypatch.setattr("assistant.nlu.llm._chat", lambda system, user, **kw: calls.append(user) or "Главное — договор.")
    project_facts._PASSPORT_CACHE.clear()
    p = _project_with_overdue(env, n=2)
    a = project_facts.passport(OWNER, p["id"])
    b = project_facts.passport(OWNER, p["id"])
    assert a["text"] == "Главное — договор." and b["cached"] is True and len(calls) == 1
    env["projects"].add_items(OWNER, p["id"], [("task", str(_task(env, "Новая", 2)["id"]), "")])
    project_facts.passport(OWNER, p["id"])
    assert len(calls) == 2  # факты изменились — пересчитали
    empty = env["projects"].create(OWNER, "Пустой")
    assert project_facts.passport(OWNER, empty["id"])["text"] == "" and len(calls) == 2  # без модели


def test_brief_gets_project_of_series(env):
    from assistant.services import meeting_brief

    p = _project_with_overdue(env, n=3)
    env["projects"].add_items(OWNER, p["id"], [("series", "primary:rec7", "Синк")])
    ev = {"calendar_id": "primary", "event_id": "rec7_20261120T100000Z", "raw": {"recurringEventId": "rec7"}}
    proj = meeting_brief.project_context(OWNER, ev)
    assert proj and proj[0]["title"] == "БГПТ" and proj[0]["stuck"] is True
    assert "просрочено 3" in proj[0]["facts"]
    assert meeting_brief.has_substance({"project": proj})
    assert meeting_brief.project_context(OWNER, {"calendar_id": "primary", "event_id": "other"}) == []


def test_leo_project_overview(env, monkeypatch):
    from assistant.services import gpt_calendar_tools as gcal

    monkeypatch.setattr("assistant.lib.webapp_public.webapp_entry_url", lambda: "https://x/webapp/")
    p = _project_with_overdue(env, n=1)
    n = env["notes"].create_note(OWNER, "План пилота", "<p>x</p>")
    env["projects"].add_items(OWNER, p["id"], [("note", str(n["id"]), "")])
    monkeypatch.setattr("assistant.services.semantic_memory.search", lambda uid, q, k=8, embed=None: [
        {"title": "План пилота", "snippet": "старт 1 декабря", "ref": {"note_id": n["id"]}},
        {"title": "Чужое", "snippet": "не из проекта", "ref": {"note_id": 99999}},
    ])
    out = json.loads(gcal.tool_project_overview(gcal.GptCalendarContext(uid=OWNER), {"project": "бгпт", "query": "старт"}))
    assert out["found"] and out["project_url"] == f"https://x/webapp/?project={p['id']}"
    assert "просрочено 1" in out["facts"]
    assert [h["title"] for h in out["found_in_project"]] == ["План пилота"]
    none = json.loads(gcal.tool_project_overview(gcal.GptCalendarContext(uid=ASSIGNEE), {"project": "бгпт"}))
    assert none["found"] is False


def test_focus_stuck_card_only_for_owner_or_lead(env):
    from assistant.services import project_focus

    p = _project_with_overdue(env, n=3)
    env["projects"].set_member(OWNER, p["id"], f"u:{ASSIGNEE}", "editor")
    cards = project_focus.stuck_cards(OWNER, NOW, TZ)
    assert [c["type"] for c in cards] == ["project_stuck"]
    assert cards[0]["actions"][0]["payload"] == {"project_id": p["id"]}
    assert project_focus.stuck_cards(ASSIGNEE, NOW, TZ) == []  # просто участник — без карточки


def test_focus_suggest_matches_title_and_respects_reject(env, monkeypatch):
    from assistant.services import project_focus

    P = env["projects"]
    p = P.create(OWNER, "БГПТ")
    P.create(OWNER, "Найм дизайнера")
    _task(env, "Позвонить в БГПТ про договор", 1)
    _task(env, "Купить молоко", 1)
    env["notes"].create_note(OWNER, "Созвон: найм дизайнера, финал", "<p>x</p>")
    evening = datetime.now(TZ).replace(hour=18)
    cards = project_focus.suggest_cards(OWNER, evening, TZ, set())
    by = {c["project"]["title"]: c for c in cards}
    assert set(by) == {"БГПТ", "Найм дизайнера"}
    assert [i["title"] for i in by["БГПТ"]["actions"][0]["payload"]["items"]] == ["Позвонить в БГПТ про договор"]
    assert project_focus.suggest_cards(OWNER, evening.replace(hour=10), TZ, set()) == []  # только вечером
    items = by["БГПТ"]["actions"][0]["payload"]["items"]
    rejected = {project_focus.item_reject_key(p["id"], i["kind"], i["id"]) for i in items}
    again = project_focus.suggest_cards(OWNER, evening, TZ, rejected)
    assert "БГПТ" not in {c["project"]["title"] for c in again}
    # уже в проекте — не предлагаем
    P.add_items(OWNER, p["id"], [(i["kind"], i["id"], "") for i in items])
    assert "БГПТ" not in {c["project"]["title"] for c in project_focus.suggest_cards(OWNER, evening, TZ, set())}
