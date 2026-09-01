import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from assistant.lib import usage_store
from assistant.skills import journal_qa as journal_qa_skill


class JournalQaSkillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        usage_store.init_db()

    async def asyncTearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_update(self, *, text: str, user_id: int = 42):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        return update

    @patch("assistant.skills.journal_qa.take_work_status", new_callable=AsyncMock)
    @patch("assistant.skills.journal_qa.post_status", new_callable=AsyncMock)
    @patch("assistant.skills.journal_qa.retrieve_journal_context")
    @patch("assistant.skills.journal_qa.nlu_llm.parse_journal_qa_query")
    @patch("assistant.skills.journal_qa.nlu_llm.answer_from_journal_context")
    async def test_empty_context_message(
        self,
        mock_answer,
        mock_parse,
        mock_retrieve,
        mock_post_status,
        mock_take_status,
    ) -> None:
        mock_post_status.return_value = None
        mock_parse.return_value = {"search_query": "GPT", "focus": "general", "sources": ["summaries"]}
        mock_retrieve.return_value = ([], "")
        update = self._make_update(text="О чем договорились про GPT?")
        context = MagicMock()
        await journal_qa_skill.handle(update, context, "О чем договорились про GPT?")
        update.message.reply_text.assert_awaited()
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("не нашёл", reply.lower())
        mock_answer.assert_not_called()

    @patch("assistant.skills.journal_qa.reply_formatted", new_callable=AsyncMock)
    @patch("assistant.skills.journal_qa.take_work_status", new_callable=AsyncMock)
    @patch("assistant.skills.journal_qa.post_status", new_callable=AsyncMock)
    @patch("assistant.skills.journal_qa.retrieve_journal_context")
    @patch("assistant.skills.journal_qa.nlu_llm.parse_journal_qa_query")
    @patch("assistant.skills.journal_qa.nlu_llm.answer_from_journal_context")
    async def test_answer_from_context(
        self,
        mock_answer,
        mock_parse,
        mock_retrieve,
        mock_post_status,
        mock_take_status,
        mock_reply_formatted,
    ) -> None:
        mock_post_status.return_value = None
        mock_parse.return_value = {"search_query": "GPT", "focus": "decisions", "sources": ["summaries"]}
        mock_retrieve.return_value = (
            [MagicMock(source_type="summary", headline="GPT")],
            "[Саммари | 2026-06-17 | GPT]\nДоговорились внедрить GPT",
        )
        mock_answer.return_value = "## 📌 Кратко\n\nНа встрече договорились **внедрить GPT**."
        update = self._make_update(text="О чем договорились про GPT?")
        context = MagicMock()
        await journal_qa_skill.handle(update, context, "О чем договорились про GPT?")
        mock_answer.assert_called_once()
        mock_reply_formatted.assert_awaited()
        reply_body = mock_reply_formatted.await_args.args[1]
        self.assertIn("внедрить GPT", reply_body)
        self.assertTrue(mock_reply_formatted.await_args.kwargs.get("rich_markdown"))


if __name__ == "__main__":
    unittest.main()
