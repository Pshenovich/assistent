"""Маршрутизация Telemost по regex."""

import unittest

from assistant.nlu.regex import parse_telemost_instant, parse_telemost_route


class TestTelemostRegex(unittest.TestCase):
    def test_instant_phrases(self) -> None:
        for text in (
            "дай телемост",
            "создай телемост",
            "телемост",
            "telemost",
            "дай ссылку на телемост",
        ):
            r = parse_telemost_route(text)
            self.assertIsNotNone(r, text)
            assert r is not None
            self.assertEqual(r.sub_intent, "telemost")

    def test_update_route(self) -> None:
        r = parse_telemost_route("перенеси телемост на 15:00")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.sub_intent, "telemost_update")

    def test_delete_route(self) -> None:
        r = parse_telemost_route("удали телемост")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.sub_intent, "telemost_delete")

    def test_parse_telemost_instant_helper(self) -> None:
        self.assertTrue(parse_telemost_instant("создай telemost"))


if __name__ == "__main__":
    unittest.main()
