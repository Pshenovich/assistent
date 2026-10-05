"""Уведомления и сохранение записи Zoom-встречи."""

from unittest import mock

from assistant.integrations.transcribe import TranscribeResult
from assistant.services.meeting_record_pipeline import (
    notify_summary_ready,
    persist_meeting_artifacts_for_user,
)


def test_notify_includes_yandex_disk_footer() -> None:
    summary = "<b>📌 Кратко</b><br><br>Текст"
    yandex_url = "https://disk.yandex.ru/d/DVUJdOWKg9TMWw"
    with mock.patch(
        "assistant.lib.telegram_notify.send_rich_message",
        return_value=True,
    ) as rich_send, mock.patch(
        "assistant.lib.telegram_notify.send_user_message"
    ) as msg_send, mock.patch(
        "assistant.skills.journal_pdf.pdf_download_inline_keyboard",
        return_value=None,
    ):
        notify_summary_ready(
            user_id=1,
            topic="T",
            summary_text=summary,
            yandex_disk_url=yandex_url,
        )
    rich_send.assert_called_once()
    body = rich_send.call_args.args[1]
    assert yandex_url in body
    assert "Запись встречи" in body
    msg_send.assert_not_called()


def test_notify_includes_yandex_disk_saved_footer_without_url() -> None:
    summary = "<b>📌 Кратко</b><br><br>Текст"
    with mock.patch(
        "assistant.lib.telegram_notify.send_rich_message",
        return_value=True,
    ) as rich_send, mock.patch(
        "assistant.lib.telegram_notify.send_user_message"
    ), mock.patch(
        "assistant.skills.journal_pdf.pdf_download_inline_keyboard",
        return_value=None,
    ):
        notify_summary_ready(
            user_id=1,
            topic="T",
            summary_text=summary,
            yandex_disk_saved=True,
        )
    body = rich_send.call_args.args[1]
    assert "Запись на Яндекс Диске" in body
    assert "отключить" in body.lower()


def test_notify_sends_full_summary_with_pdf_buttons() -> None:
    summary = "<b>📌 Кратко</b><br><br>Длинный текст " + ("x" * 1200)
    with mock.patch(
        "assistant.lib.telegram_notify.send_rich_message",
        return_value=True,
    ) as rich_send, mock.patch(
        "assistant.lib.telegram_notify.send_user_html_long_text"
    ) as html_send, mock.patch(
        "assistant.lib.telegram_notify.send_user_message"
    ) as msg_send, mock.patch(
        "assistant.skills.journal_pdf.pdf_download_inline_keyboard",
        side_effect=lambda eid: {"inline_keyboard": [[{"callback_data": f"jmpdf:{eid}"}]]},
    ):
        notify_summary_ready(
            user_id=1,
            topic="T",
            summary_text=summary,
            summary_event_id=5,
            transcript_event_id=9,
        )
    rich_send.assert_called_once()
    assert summary in rich_send.call_args.args[1]
    assert rich_send.call_args.kwargs.get("reply_markup")
    html_send.assert_not_called()
    msg_send.assert_called_once()
    assert "Транскрипции" in msg_send.call_args.args[1]
    assert msg_send.call_args.kwargs.get("reply_markup")


def test_persist_creates_transcription_note(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.stores import notes as notes_store
    from assistant.stores import note_sheets as sheets

    notes_store._CONN = None  # type: ignore[attr-defined]
    processed = {
        "summary_text": "<b>ok</b>",
        "transcript": "Спикер 1:\nПривет",
        "participant_names": ["Спикер 1"],
        "meta": {"main_topic": "T"},
        "result": TranscribeResult(plain_text="x", formatted_text="Спикер 1:\nПривет"),
    }
    note = persist_meeting_artifacts_for_user(
        user_id=42,
        username=None,
        processed=processed,
        topic="T",
        source_url="https://zoom.us/j/1",
        filename="meeting.wav",
    )
    assert note["role"] == "transcription"
    assert note["has_summary"] is True
    assert note["primary_sheet_title"] == "Саммари"
    assert "ok" in (note.get("body") or "")
    extras = sheets.list_extra_sheets(int(note["id"]))
    assert len(extras) == 1
    assert extras[0]["title"] == "Транскрипции"
    assert "Привет" in extras[0]["body"]
    listed = notes_store.list_notes(42)
    assert listed == []
    trans = notes_store.list_transcription_notes(42)
    assert len(trans) == 1
