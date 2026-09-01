"""Отмена текущей операции в боте."""

import unittest

from assistant.lib.operation_cancel import is_cancel_command, pending_kind_label


class TestOperationCancel(unittest.TestCase):
    def test_cancel_phrases(self) -> None:
        for text in (
            "отмена",
            "Отменить",
            "стоп",
            "обой",
            "хватит",
            "cancel",
            "/cancel",
            "не надо",
        ):
            self.assertTrue(is_cancel_command(text), text)

    def test_not_cancel(self) -> None:
        self.assertFalse(is_cancel_command("встреча завтра"))
        self.assertFalse(is_cancel_command("отмени встречу с Иваном"))

    def test_kind_label(self) -> None:
        self.assertEqual(pending_kind_label("await_pick_time"), "выбор времени встречи")


if __name__ == "__main__":
    unittest.main()
