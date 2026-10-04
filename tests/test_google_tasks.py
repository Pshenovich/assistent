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
