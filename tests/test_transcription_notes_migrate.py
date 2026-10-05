import os
import tempfile
import unittest

from assistant.lib import usage_store
from assistant.services.transcription_notes_migrate import migrate_user
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store


class TranscriptionNotesMigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        usage_store.init_db()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_migrate_pair_and_orphan(self) -> None:
        tr_id = usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="t1",
            usage={"text": "Транскрипт встречи про GPT", "source": "telegram"},
            telegram_user_id="7",
        )
        usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="s1",
            usage={
                "text": "Договорились внедрить GPT",
                "transcript": "Транскрипт встречи про GPT",
                "meta": {"main_topic": "GPT", "transcript_event_id": tr_id},
            },
            telegram_user_id="7",
        )
        usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="t2",
            usage={"text": "Только голос без саммари", "filename": "voice.ogg"},
            telegram_user_id="7",
        )
        usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="s2",
            usage={
                "text": "Сиротское саммари",
                "transcript": "Встроенный транскрипт",
                "meta": {"main_topic": "Сирота"},
            },
            telegram_user_id="7",
        )
        stats = migrate_user(7)
        self.assertEqual(stats["created"], 3)
        notes = notes_store.list_transcription_notes(7)
        self.assertEqual(len(notes), 3)
        paired = next(n for n in notes if n.get("title") == "GPT")
        self.assertTrue(paired.get("has_summary"))
        extras = sheets.list_extra_sheets(int(paired["id"]))
        self.assertEqual(len(extras), 1)
        only_tr = next(n for n in notes if "голос" in (n.get("body") or "").lower())
        self.assertFalse(only_tr.get("has_summary"))
        orphan = next(n for n in notes if n.get("title") == "Сирота")
        self.assertTrue(orphan.get("has_summary"))
        leftover = usage_store.user_journal_entries("7", limit=50)
        self.assertEqual(leftover, [])
        again = migrate_user(7)
        self.assertEqual(again["created"], 0)
        self.assertEqual(len(notes_store.list_transcription_notes(7)), 3)
