import os
import tempfile
import unittest
from unittest import mock

from assistant.services import transcription_notes as tnotes
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store


class TranscriptionNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_transcript_only_hidden_from_notes(self) -> None:
        note = tnotes.create_transcript_only(
            1, title="Созвон", transcript="Привет команда", meta={"source": "telegram"}
        )
        self.assertEqual(note["role"], "transcription")
        self.assertFalse(note["has_summary"])
        self.assertEqual(note["primary_sheet_title"], "Транскрипции")
        self.assertEqual(notes_store.list_notes(1), [])
        listed = notes_store.list_transcription_notes(1)
        self.assertEqual(len(listed), 1)
        listed_sheets = sheets.list_sheets(note)
        self.assertEqual(len(listed_sheets), 1)
        self.assertEqual(listed_sheets[0]["title"], "Транскрипции")
        self.assertIn("Привет", listed_sheets[0]["body"])

    def test_create_with_summary_two_sheets(self) -> None:
        note = tnotes.create_with_summary(
            2,
            title="Weekly",
            transcript="Говорили про релиз",
            summary="Договорились выкатить в пятницу",
            meta={"main_topic": "Weekly"},
        )
        self.assertTrue(note["has_summary"])
        self.assertEqual(note["primary_sheet_title"], "Саммари")
        extras = sheets.list_extra_sheets(int(note["id"]))
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0]["title"], "Транскрипции")
        self.assertIn("релиз", extras[0]["body"])
        self.assertIn("пятницу", note["body"])

    def test_begin_make_summary_moves_transcript(self) -> None:
        note = tnotes.create_transcript_only(3, title="Файл", transcript="Сырой текст")
        promoted = tnotes.begin_make_summary(3, int(note["id"]))
        self.assertTrue(promoted["has_summary"])
        self.assertTrue(promoted["summary_generating"])
        self.assertFalse(tnotes._plain(promoted.get("body") or ""))
        extras = sheets.list_extra_sheets(int(note["id"]))
        self.assertEqual(extras[0]["title"], "Транскрипции")
        self.assertIn("Сырой текст", extras[0]["body"])
        finished = tnotes.finish_make_summary(3, int(note["id"]), "Кратко: ок")
        self.assertFalse(finished["summary_generating"])
        self.assertIn("Кратко", finished["body"])

    def test_generate_summary_uses_llm(self) -> None:
        note = tnotes.create_transcript_only(4, title="Meet", transcript="Обсудили сроки")
        tnotes.begin_make_summary(4, int(note["id"]))
        with mock.patch(
            "assistant.nlu.llm.summarize_recording",
            return_value={
                "summary": "Сроки сдвинули",
                "main_topic": "Meet",
                "tasks": [],
                "decisions": ["сдвинуть"],
            },
        ):
            out = tnotes.generate_summary_for_note(4, int(note["id"]))
        assert out is not None
        self.assertIn("Сроки", tnotes._plain(out["body"]))
        self.assertEqual((out.get("meta") or {}).get("decisions"), ["сдвинуть"])

    def test_search_prefers_notes(self) -> None:
        tnotes.create_with_summary(
            5,
            title="Bitrix",
            transcript="Интеграция Bitrix API",
            summary="Решили подключить Bitrix",
            meta={"decisions": ["подключить Bitrix"]},
        )
        hits = tnotes.search_summaries(5, "Bitrix", field="decisions")
        self.assertTrue(hits)
        self.assertTrue(hits[0].get("is_transcription_note"))
        tr = tnotes.search_transcripts(5, "Интеграция")
        self.assertTrue(tr)
        latest = tnotes.get_latest_summary(5)
        assert latest is not None
        self.assertEqual(latest.get("note_id"), hits[0].get("note_id"))

    def test_list_for_user_bundle_shape(self) -> None:
        tnotes.create_transcript_only(8, title="Голос", transcript="Сырой текст")
        tnotes.create_with_summary(
            8,
            title="Встреча",
            transcript="Полный текст",
            summary="Кратко о встрече",
        )
        rows = tnotes.list_for_user(8)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["role"], "transcription")
            self.assertTrue(row["is_transcription"])
            self.assertIn("has_summary", row)
            self.assertIn("primary_sheet_title", row)
            self.assertIn("preview", row)
        with_sum = next(r for r in rows if r["has_summary"])
        only_tr = next(r for r in rows if not r["has_summary"])
        self.assertEqual(with_sum["primary_sheet_title"], "Саммари")
        self.assertIn("Кратко", with_sum["preview"])
        self.assertEqual(only_tr["primary_sheet_title"], "Транскрипции")
        self.assertIn("Сырой", only_tr["preview"])
        self.assertEqual(notes_store.list_notes(8), [])
