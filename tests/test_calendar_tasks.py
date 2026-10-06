"""CRUD календарных задач, дефолт +30 мин, slash-парсер."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture()
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "calendar_tasks.sqlite"
    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(db))
    from assistant.stores import calendar_tasks as store

    store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]


def test_create_defaults_end_plus_30(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 10, 7, 15, 0, tzinfo=tz)
    row = store.create_task(11, title="Позвонить", start_at=start, tz=tz)
    assert row["title"] == "Позвонить"
    assert row["assignee_user_id"] == "11"
    end = datetime.fromisoformat(row["end_at"])
    assert (end - datetime.fromisoformat(row["start_at"])).total_seconds() == 30 * 60
    ev = store.as_calendar_event(row, tz=tz)
    assert ev["entry_type"] == "task"
    assert ev["kind"] == "Задача"
    assert ev["id"] == f"task-{row['id']}"


def test_all_day_naive_datetime_uses_calendar_tz(tasks_db):
    """Naive midnight на UTC-сервере не должен уезжать на вчера у MSK."""
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    row = store.create_task(
        11,
        title="All day",
        start_at=datetime(2026, 10, 6, 0, 0, 0),  # naive
        all_day=True,
        tz=tz,
    )
    assert row["all_day"] is True
    # 6 окт 00:00 MSK → 5 окт 21:00 UTC
    assert row["start_at"].startswith("2026-10-05T21:00:00")
    ev = store.as_calendar_event(row, tz=tz, viewer_id=11)
    assert ev["start"]["date"] == "2026-10-06"
    assert store.chip_label(row, tz=tz) == "6 окт"


def test_list_window_and_update(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    a = store.create_task(
        5, title="A", start_at=datetime(2026, 10, 7, 10, 0, tzinfo=tz), tz=tz
    )
    store.create_task(
        5, title="B", start_at=datetime(2026, 10, 8, 10, 0, tzinfo=tz), tz=tz
    )
    win = store.list_tasks_in_window(
        5,
        datetime(2026, 10, 7, 0, 0, tzinfo=tz),
        datetime(2026, 10, 8, 0, 0, tzinfo=tz),
    )
    assert [t["title"] for t in win] == ["A"]
    updated = store.update_task(5, a["id"], title="A2", checklist=[{"text": "шаг"}])
    assert updated is not None
    assert updated["title"] == "A2"
    assert updated["checklist"][0]["text"] == "шаг"
    assert store.delete_task(5, a["id"]) is True
    assert store.get_task(5, a["id"]) is None


def test_parse_slash_today_tomorrow_weekday(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=tz)  # Wednesday
    today = store.parse_slash_when("/сегодня 15:00 созвон", tz=tz, now=now)
    assert today["title"] == "созвон"
    assert today["start"].hour == 15
    assert today["start"].date().isoformat() == "2026-10-07"
    assert (today["end"] - today["start"]).total_seconds() == 30 * 60

    tomorrow = store.parse_slash_when("завтра 10:00", tz=tz, now=now)
    assert tomorrow["start"].date().isoformat() == "2026-10-08"
    assert tomorrow["start"].hour == 10
    assert tomorrow["title"] == "Задача"

    tuesday = store.parse_slash_when("/вторник 14:30 план", tz=tz, now=now)
    assert tuesday["start"].weekday() == 1
    assert tuesday["start"].hour == 14
    assert tuesday["start"].minute == 30
    assert "план" in tuesday["title"]

    short = store.parse_slash_when("/7 окт 15:00 созвон", tz=tz, now=now)
    assert short["start"].date().isoformat() == "2026-10-07"
    assert short["start"].hour == 15
    assert short["title"] == "созвон"

    named = store.parse_slash_when("/7 октября 15:00 созвон", tz=tz, now=now)
    assert named["start"].date().isoformat() == "2026-10-07"
    assert named["start"].hour == 15
    assert named["title"] == "созвон"

    dotted = store.parse_slash_when("/7.10 10:00", tz=tz, now=now)
    assert dotted["start"].date().isoformat() == "2026-10-07"
    assert dotted["start"].hour == 10
    assert dotted["title"] == "Задача"

    chip = store.parse_slash_when("5 окт, 14:00", tz=tz, now=now)
    assert chip["start"].date().isoformat() == "2026-10-05"
    assert chip["start"].hour == 14
    assert chip["start"].minute == 0

    full = store.parse_slash_when("/19.05.2026 09:30 план", tz=tz, now=now)
    assert full["start"].date().isoformat() == "2026-05-19"
    assert full["start"].hour == 9
    assert full["start"].minute == 30
    assert "план" in full["title"]


def test_chip_html_roundtrip(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    row = store.create_task(
        3, title="X", start_at=datetime(2026, 10, 7, 15, 0, tzinfo=tz), tz=tz
    )
    chip = store.task_chip_html(row, tz=tz)
    assert 'data-leo-task-id="%s"' % row["id"] in chip
    assert "note-task-chip" in chip
    assert "note-task-chip-label" in chip
    assert "Задача " not in chip
    assert "7 окт, 15:00" in chip
    assert store.chip_label(row, tz=tz) == "7 окт, 15:00"
    assert store.task_id_from_chip_html(chip) == int(row["id"])
    assert store.task_id_from_chip_html("<p>без чипа</p>") is None


def test_list_tasks_for_note_and_reuse(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    a = store.create_task(
        8,
        title="Разобрать паттерны",
        start_at=datetime(2026, 10, 5, 15, 30, tzinfo=tz),
        note_id="81",
        tz=tz,
    )
    store.create_task(
        8,
        title="Другая",
        start_at=datetime(2026, 10, 5, 11, 0, tzinfo=tz),
        note_id="82",
        tz=tz,
    )
    rows = store.list_tasks_for_note(8, "81")
    assert [r["id"] for r in rows] == [a["id"]]
    from usage_server import _find_reusable_note_task

    reused = _find_reusable_note_task(
        8, "81", "Разобрать паттерны", "2026-10-05T09:00:00+03:00", tz
    )
    assert reused is not None
    assert reused["id"] == a["id"]
    by_time = _find_reusable_note_task(
        8, "81", "", "2026-10-05T15:30:00+03:00", tz
    )
    assert by_time is not None
    assert by_time["id"] == a["id"]


def test_assignee_sees_and_can_edit_task(tasks_db):
    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 10, 7, 15, 0, tzinfo=tz)
    row = store.create_task(
        11,
        title="Для Андрея",
        start_at=start,
        assignee_user_id=22,
        assignee_email="a@example.com",
        assignee_name="Андрей",
        tz=tz,
    )
    win_start = datetime(2026, 10, 7, 0, 0, tzinfo=tz)
    win_end = datetime(2026, 10, 8, 0, 0, tzinfo=tz)
    assert [t["id"] for t in store.list_tasks_in_window(11, win_start, win_end)] == [row["id"]]
    assert [t["id"] for t in store.list_tasks_in_window(22, win_start, win_end)] == [row["id"]]
    assert store.list_tasks_in_window(33, win_start, win_end) == []

    assert store.get_task(11, row["id"]) is not None
    assert store.get_task(22, row["id"]) is None
    assert store.get_task_for_user(22, row["id"]) is not None
    assert store.get_task_for_user(33, row["id"]) is None

    updated = store.update_task(22, row["id"], title="Обновил исполнитель")
    assert updated is not None
    assert updated["title"] == "Обновил исполнитель"
    assert updated["owner_user_id"] == "11"
    assert store.update_task(33, row["id"], title="Чужой") is None

    ev_owner = store.as_calendar_event(updated, tz=tz, viewer_id=11)
    ev_assignee = store.as_calendar_event(updated, tz=tz, viewer_id=22)
    assert ev_owner["is_owner"] is True
    assert ev_assignee["is_owner"] is False
    assert ev_assignee["owner_user_id"] == "11"

    assert sorted(store.list_involved_user_ids()) == [11, 22]
    assert store.delete_task(33, row["id"]) is False
    assert store.delete_task(22, row["id"]) is True
    assert store.get_task_for_user(11, row["id"]) is None


def test_push_task_skips_non_owner(tasks_db, monkeypatch):
    from assistant.services import google_tasks

    store = tasks_db
    tz = ZoneInfo("Europe/Moscow")
    row = store.create_task(
        11,
        title="Не пушить исполнителю",
        start_at=datetime(2026, 10, 7, 15, 0, tzinfo=tz),
        assignee_user_id=22,
        tz=tz,
    )
    called = []

    def _boom(user_id):
        called.append(user_id)
        raise AssertionError("should not open Google as assignee")

    monkeypatch.setattr(google_tasks, "_service", _boom)
    out = google_tasks.push_task(22, row)
    assert out == row
    assert called == []
    # Delegated: even owner does not push to Google Tasks
    owner_called = []

    def _none(user_id):
        owner_called.append(user_id)
        return None

    monkeypatch.setattr(google_tasks, "_service", _none)
    google_tasks.push_task(11, row)
    assert owner_called == []

    personal = store.create_task(
        11,
        title="Себе",
        start_at=datetime(2026, 10, 7, 16, 0, tzinfo=tz),
        tz=tz,
    )
    owner_called.clear()
    google_tasks.push_task(11, personal)
    assert owner_called == [11]
