from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from assistant.services import google_tasks as gt


def test_push_task_inserts_and_subtasks(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "t.sqlite"))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    tz = ZoneInfo("UTC")
    row = store.create_task(
        1,
        title="Купить",
        start_at=datetime(2026, 10, 7, 15, 0, tzinfo=tz),
        checklist=[{"text": "молоко", "done": False}],
    )
    svc = MagicMock()
    svc.tasklists().list().execute.return_value = {"items": [{"id": "L1", "title": "My Tasks"}]}
    svc.tasks().insert().execute.return_value = {"id": "G1"}
    svc.tasks().list().execute.return_value = {"items": []}
    svc.tasks().insert().execute.return_value = {"id": "G1"}

    inserts = []

    def _insert(**kwargs):
        inserts.append(kwargs)
        res = MagicMock()
        res.execute.return_value = {"id": "G1" if "parent" not in kwargs else "C1"}
        return res

    svc.tasks().insert.side_effect = _insert

    with patch.object(gt, "_service", return_value=svc):
        out = gt.push_task(1, row)
    assert out is not None
    assert any("parent" in kw for kw in inserts)
    store._CONN = None  # type: ignore[attr-defined]


def test_delete_remote_noops_without_service():
    with patch.object(gt, "_service", return_value=None):
        gt.delete_remote(1, {"google_task_id": "x", "google_tasklist_id": "L"})


def test_push_skips_without_scope():
    with patch.object(gt, "_service", return_value=None):
        task = {"id": 1, "title": "X"}
        assert gt.push_task(1, task) is task


def test_due_calendar_date_uses_date_part():
    assert gt.due_calendar_date("2026-10-04T00:00:00.000Z").isoformat() == "2026-10-04"
    assert gt.due_calendar_date("") is None
    assert gt.is_tasks_calendar_name("Задачи")
    assert gt.is_tasks_calendar_name("My Tasks")
    assert gt.is_tasks_calendar_name("Inbox", "foo#tasks@group.v.calendar.google.com")
    assert not gt.is_tasks_calendar_name("Work")
    tz = ZoneInfo("Europe/Moscow")
    start = gt.start_from_google_task(
        {"notes": "Время: 2026-10-07T15:00:00+03:00 — 2026-10-07T15:30:00+03:00"},
        due_day=datetime(2026, 10, 7, tzinfo=tz).date(),
        tz=tz,
    )
    assert start.hour == 15
    assert "Время:" not in gt.notes_without_leo_meta(
        "Купить\nВремя: 2026-10-07T15:00:00+03:00 — x\nИсполнитель: Я"
    )


def test_pull_into_leo_reads_all_tasklists(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "t.sqlite"))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    gt._reset_pull_state_for_tests()
    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    end = datetime(2026, 10, 8, tzinfo=timezone.utc)
    svc = MagicMock()
    svc.tasklists().list().execute.return_value = {
        "items": [{"id": "L-leo", "title": "Leo"}, {"id": "L-my", "title": "My Tasks"}]
    }

    def _list(**kwargs):
        res = MagicMock()
        if kwargs.get("tasklist") == "L-my":
            res.execute.return_value = {
                "items": [
                    {
                        "id": "T1",
                        "title": "Из календаря",
                        "due": "2026-10-07T00:00:00.000Z",
                    }
                ]
            }
        else:
            res.execute.return_value = {"items": []}
        return res

    svc.tasks().list.side_effect = _list
    with patch.object(gt, "_service", return_value=svc):
        imported = gt.pull_into_leo(9030, start, end, force=True)
    assert [t["title"] for t in imported] == ["Из календаря"]
    store._CONN = None  # type: ignore[attr-defined]
    gt._reset_pull_state_for_tests()


def test_pull_into_leo_skips_when_recent():
    gt._reset_pull_state_for_tests()
    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    end = datetime(2026, 10, 8, tzinfo=timezone.utc)
    with patch.object(gt, "_service") as svc:
        svc.return_value = None
        assert gt.pull_into_leo(9021, start, end) == []
        assert svc.call_count == 1
        assert gt.pull_into_leo(9021, start, end) == []
        assert svc.call_count == 1
        assert gt.pull_into_leo(9021, start, end, force=True) == []
        assert svc.call_count == 2
    gt._reset_pull_state_for_tests()


def test_sync_task_google_deletes_leftover_event_and_pushes_task(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "t.sqlite"))
    from assistant.stores import calendar_tasks as store
    from usage_server import _sync_task_google

    store._CONN = None  # type: ignore[attr-defined]
    tz = ZoneInfo("UTC")
    row = store.create_task(
        1,
        title="В GCal",
        start_at=datetime(2026, 10, 7, 15, 0, tzinfo=tz),
        tz=tz,
    )
    store.update_task(1, row["id"], google_event_id="ev1", google_calendar_id="primary")
    row = store.get_task(1, row["id"])
    pushed = []

    def _push(_uid, task):
        pushed.append(task)
        return task

    with (
        patch("assistant.services.calendar.delete_task_event") as delete_ev,
        patch("assistant.services.calendar.upsert_task_event") as upsert_ev,
        patch("assistant.services.google_tasks.push_task", _push),
    ):
        out = _sync_task_google(1, row, "update")
    assert upsert_ev.call_count == 0
    delete_ev.assert_called_once()
    assert pushed
    assert out is not None
    assert not str(out.get("google_event_id") or "")
    store._CONN = None  # type: ignore[attr-defined]


def test_schedule_pull_runs_once_in_background():
    import time

    gt._reset_pull_state_for_tests()
    started: list[int] = []

    def _pull(*_a, **_k):
        started.append(1)
        return []

    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    end = datetime(2026, 10, 8, tzinfo=timezone.utc)
    with patch.object(gt, "pull_into_leo", _pull):
        gt.schedule_pull(9022, start, end)
        gt.schedule_pull(9022, start, end)
        deadline = time.monotonic() + 1.0
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
    assert started == [1]
    gt._reset_pull_state_for_tests()
