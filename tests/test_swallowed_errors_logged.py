"""Сбои, которые раньше глотались молча, теперь видны в логе, а поведение прежнее."""

from __future__ import annotations

from unittest.mock import patch

from assistant.services import task_reminders
from assistant.stores import notes as notes_store


def test_task_reminder_users_survive_tasks_store_failure(capsys):
    with patch.object(task_reminders.mr, "iter_calendar_user_ids", return_value=[2, 1]), patch.object(
        task_reminders.calendar_tasks_store, "list_involved_user_ids", side_effect=OSError("db locked")
    ):
        assert task_reminders.iter_task_user_ids() == [1, 2]
    assert "[task_reminder] list task users failed err=OSError('db locked')" in capsys.readouterr().out


def test_delete_note_survives_cascade_failures(capsys):
    note = notes_store.create_note(930001, "X", "y")
    from assistant.stores import note_members, teams_store

    with patch.object(note_members, "delete_all_for_note", side_effect=RuntimeError("members")), patch.object(
        teams_store, "delete_all_for_note", side_effect=RuntimeError("teams")
    ):
        assert notes_store.delete_note(930001, note["id"]) is True

    assert notes_store.get_note_by_id(note["id"]) is None
    out = capsys.readouterr().out
    assert f"[notes] delete members failed note={note['id']} err=RuntimeError('members')" in out
    assert f"[notes] delete team shares failed note={note['id']} err=RuntimeError('teams')" in out


def test_duplicate_note_survives_sheet_copy_failure(capsys):
    from assistant.stores import note_sheets

    note = notes_store.create_note(930002, "Src", "body")
    with patch.object(note_sheets, "note_allows_sheets", return_value=True), patch.object(
        note_sheets, "copy_sheets", side_effect=RuntimeError("sheets")
    ):
        dup = notes_store.duplicate_note(930002, note["id"])

    assert dup and dup["id"] != note["id"]
    assert "[notes] duplicate sheets failed" in capsys.readouterr().out
