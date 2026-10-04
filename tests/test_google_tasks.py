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
