import json
import os
import tempfile
import unittest

from assistant.lib import usage_store


class TranscriptionSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        usage_store.init_db()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_search_transcriptions(self) -> None:
        usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="j1",
            usage={
                "text": "Совещание про филиалы в Куркино и график работы",
                "source": "telegram",
            },
            telegram_user_id="99",
        )
        usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="j2",
            usage={"text": "Другой разговор про отпуск", "source": "telegram"},
            telegram_user_id="99",
        )
        hits = usage_store.search_transcriptions("99", "филиалы куркино")
        self.assertEqual(len(hits), 1)
        self.assertIn("Куркино", hits[0]["text"])


if __name__ == "__main__":
    unittest.main()
