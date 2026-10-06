from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from assistant.services import task_reminders as tr


@pytest.fixture()
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "calendar_tasks.sqlite"
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(db))
    monkeypatch.setenv("MEETING_REMINDERS_SENT_PATH", str(tmp_path / "sent.json"))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]


def test_format_task_reminder_message():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 10, 7, 15, 0, tzinfo=tz)
    end = start + timedelta(minutes=30)
    text = tr.format_reminder_message(
        {
            "title": "Позвонить клиенту",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
        user_id=1,
        minutes_before=15,
    )
    assert "Через 15 минут у вас задача:" in text
    assert "Позвонить клиенту" in text
    assert "15:00" in text


def test_collect_due_task_in_window(tasks_db):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=tz)
    row = tasks_db.create_task(
        42, title="Позвонить", start_at=now + timedelta(minutes=15), tz=tz
    )
    due = tr.collect_due_reminders(42, now=now)
    assert [t["id"] for t in due] == [row["id"]]


def test_collect_due_skips_done_and_sent(tasks_db):
    from assistant.stores import meeting_reminders_sent as sent_store

    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=tz)
    done = tasks_db.create_task(
        42, title="Готово", start_at=now + timedelta(minutes=15), tz=tz
    )
    tasks_db.update_task(42, done["id"], done=True)
    pending = tasks_db.create_task(
        42, title="Ждёт", start_at=now + timedelta(minutes=15), tz=tz
    )
    sent_store.mark_sent(
        42, tr.TASK_REMINDER_CAL, f"task-{pending['id']}", pending["start_at"]
    )
    assert tr.collect_due_reminders(42, now=now) == []


def test_collect_due_for_assignee(tasks_db):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=tz)
    row = tasks_db.create_task(
        11,
        title="Для исполнителя",
        start_at=now + timedelta(minutes=15),
        assignee_user_id=22,
        tz=tz,
    )
    due_assignee = tr.collect_due_reminders(22, now=now)
    due_owner = tr.collect_due_reminders(11, now=now)
    assert [t["id"] for t in due_assignee] == [row["id"]]
    assert [t["id"] for t in due_owner] == [row["id"]]
    assert 22 in tr.iter_task_user_ids()
