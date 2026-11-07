"""Показатели работы сотрудника: правила честности для сроков, качество, постановщик."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assistant.services import people_metrics as pm

UTC = timezone.utc
OWNER, ME = "10", "20"
# Понедельник, 2 ноября 2026, 10:00 UTC — середина рабочего дня.
MON = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def iso(d: datetime) -> str:
    return d.isoformat()


def task(tid: int, *, created: datetime, deadline: datetime, done_at: datetime | None = None, **kw) -> dict:
    t = {
        "id": tid, "owner_user_id": OWNER, "assignee_user_id": ME,
        "created_at": iso(created), "start_at": iso(deadline - timedelta(minutes=30)), "end_at": iso(deadline),
        "done": done_at is not None, "done_at": iso(done_at) if done_at else "",
        "schedule_log": [], "outcome": "", "accepted_at": "", "assignee_triage": "accepted",
        "assignee_status": "", "assignee_seen_at": "", "owner_archived": False,
    }
    t.update(kw)
    return t


def move(at: datetime, by: str, frm: datetime, to: datetime) -> dict:
    return {"at": iso(at), "by_user_id": by, "from_end": iso(frm), "to_end": iso(to)}


def verdict(t: dict, events: list | None = None, now: datetime | None = None, absences=()) -> dict:
    return pm.deadline_verdict(t, events or [], now=now or MON + timedelta(days=10), absences=absences)


def test_done_before_deadline_is_on_time() -> None:
    t = task(1, created=MON, deadline=MON + timedelta(days=2), done_at=MON + timedelta(days=1))
    assert verdict(t)["status"] == "on_time"


def test_late_without_warning() -> None:
    t = task(1, created=MON, deadline=MON + timedelta(days=2), done_at=MON + timedelta(days=3))
    assert verdict(t)["status"] == "late"


def test_owner_moved_deadline_is_not_late() -> None:
    first, later = MON + timedelta(days=2), MON + timedelta(days=4)
    t = task(1, created=MON, deadline=later, done_at=MON + timedelta(days=3),
             schedule_log=[move(MON + timedelta(days=1), OWNER, first, later)])
    v = verdict(t)
    assert v["status"] == "on_time" and v["reason"] == "owner_moved"


def test_assignee_moved_deadline_still_late_but_warned() -> None:
    first, later = MON + timedelta(days=2), MON + timedelta(days=4)
    t = task(1, created=MON, deadline=later, done_at=MON + timedelta(days=3),
             schedule_log=[move(MON + timedelta(days=1), ME, first, later)])
    assert verdict(t)["status"] == "late_warned"


def test_unlogged_deadline_change_counts_for_assignee() -> None:
    # Срок сдвинула синхронизация/ИИ без автора: в журнале пусто, в задаче — новый срок.
    t = task(1, created=MON, deadline=MON + timedelta(days=4), done_at=MON + timedelta(days=3))
    assert verdict(t)["status"] == "on_time"


def test_question_wait_stops_the_clock() -> None:
    deadline = MON + timedelta(days=2)
    events = [
        {"at": iso(MON + timedelta(days=1)), "kind": "message", "actor_user_id": ME, "text": "Какой формат?"},
        {"at": iso(MON + timedelta(days=3)), "kind": "message", "actor_user_id": OWNER, "text": "PDF"},
        {"at": iso(MON + timedelta(days=3, hours=12)), "kind": "done", "actor_user_id": ME},
    ]
    t = task(1, created=MON, deadline=deadline, done_at=MON + timedelta(days=3, hours=12))
    v = verdict(t, events)
    assert v["status"] == "on_time" and v["reason"] == "question_wait"


def test_short_notice_is_excluded() -> None:
    t = task(1, created=MON, deadline=MON + timedelta(hours=3), done_at=MON + timedelta(hours=5))
    v = verdict(t)
    assert v["status"] == "excluded" and v["reason"] == "short_notice"


def test_deadline_during_vacation_is_excluded() -> None:
    t = task(1, created=MON, deadline=MON + timedelta(days=3), done_at=MON + timedelta(days=8))
    v = verdict(t, absences=[{"from": "2026-11-04", "to": "2026-11-08", "kind": "vacation"}])
    assert v["status"] == "excluded" and v["reason"] == "absence"


def test_open_task_before_deadline_is_pending() -> None:
    t = task(1, created=MON, deadline=MON + timedelta(days=5))
    assert verdict(t, now=MON + timedelta(days=1))["status"] == "pending"


def test_first_done_counts_even_after_return() -> None:
    deadline = MON + timedelta(days=2)
    events = [
        {"at": iso(MON + timedelta(days=1)), "kind": "done", "actor_user_id": ME},
        {"at": iso(MON + timedelta(days=1, hours=2)), "kind": "returned", "actor_user_id": OWNER, "text": "Добавь итоги"},
        {"at": iso(MON + timedelta(days=4)), "kind": "done", "actor_user_id": ME},
    ]
    t = task(1, created=MON, deadline=deadline, done_at=MON + timedelta(days=4))
    assert verdict(t, events)["status"] == "on_time"


def test_work_hours_skip_weekend_and_night() -> None:
    fri_evening = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
    mon_morning = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    assert pm.work_hours_between(fri_evening, mon_morning) == pytest.approx(2.0)


def _many(n: int, *, late: int = 0) -> list[dict]:
    out = []
    for i in range(n):
        created = MON - timedelta(days=14) + timedelta(hours=i)
        deadline = created + timedelta(days=2)
        done = deadline + (timedelta(days=1) if i < late else -timedelta(hours=1))
        out.append(task(i + 1, created=created, deadline=deadline, done_at=done))
    return out


def test_small_sample_shows_no_percent() -> None:
    tasks = _many(3)
    m = pm.assignee_metrics(ME, tasks, {}, start=MON - timedelta(days=28), end=MON, now=MON)
    assert m["deadlines"] == 3 and m["on_time_pct"] is None and m["enough_data"] is False


def test_on_time_percent_and_cancelled_ignored() -> None:
    tasks = _many(6, late=1)
    tasks.append(task(99, created=MON - timedelta(days=10), deadline=MON - timedelta(days=5), outcome="cancelled"))
    m = pm.assignee_metrics(ME, tasks, {}, start=MON - timedelta(days=28), end=MON, now=MON)
    assert m["deadlines"] == 6 and m["on_time"] == 5 and m["on_time_pct"] == 83


def test_first_time_acceptance_and_setter_view() -> None:
    tasks = _many(6)
    for t in tasks:
        t.update(outcome="accepted", accepted_at=iso(MON - timedelta(days=3)), owner_archived=True)
    events = {1: [{"at": iso(MON - timedelta(days=4)), "kind": "returned", "actor_user_id": OWNER, "text": ""}]}
    m = pm.assignee_metrics(ME, tasks, events, start=MON - timedelta(days=28), end=MON, now=MON)
    assert m["accepted"] == 6 and m["first_time"] == 5 and m["returns"] == 1
    s = pm.setter_metrics(OWNER, tasks, events, start=MON - timedelta(days=28), end=MON, now=MON)
    assert s["posted"] == 6 and s["silent_returns"] == 1


@pytest.fixture()
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]


def test_store_writes_created_and_done_events_for_delegated_tasks(tasks_db) -> None:
    store = tasks_db
    t = store.create_task(10, title="Отчёт", start_at=MON, assignee_user_id=20)
    own = store.create_task(10, title="Себе", start_at=MON)
    store.update_task(20, t["id"], done=True)
    store.update_task(10, own["id"], done=True)
    kinds = [e["kind"] for e in store.events_by_task([t["id"]])[t["id"]]]
    assert kinds == ["created", "done"]
    assert own["id"] not in store.events_by_task([own["id"]])


def test_metrics_me_endpoint_and_absences(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import usage_server
    from assistant.bot import access_gate
    from assistant.stores import calendar_tasks
    from tests.test_canvas_endpoints import BOT_TOKEN, _headers

    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "calendar_tasks.sqlite"))
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path / "users"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_sync_task_google", lambda *a, **k: None)
    calendar_tasks._CONN = None  # type: ignore[attr-defined]
    try:
        calendar_tasks.create_task(11, title="Отчёт", start_at=datetime.now(UTC) + timedelta(days=3), assignee_user_id=22)
        client = TestClient(usage_server.app, raise_server_exceptions=False)
        r = client.get("/api/miniapp/metrics/me?days=28", headers=_headers(22))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["assignee"]["received"] == 1 and body["assignee"]["on_time_pct"] is None
        assert [t["title"] for t in body["tasks"]] == ["Отчёт"]
        r = client.put("/api/miniapp/me/absences", json={"absences": [{"from": "2026-12-01", "to": "2026-12-10"}, {"from": "bad"}]},
                       headers=_headers(22))
        assert r.status_code == 200 and r.json()["absences"] == [{"from": "2026-12-01", "to": "2026-12-10", "kind": "other"}]
        assert client.get("/api/miniapp/metrics/me?days=3", headers=_headers(22)).status_code == 422
    finally:
        calendar_tasks._CONN = None  # type: ignore[attr-defined]
