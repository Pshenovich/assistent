"""Telemost link parser tests."""

import unittest

from assistant.lib.telemost_link import find_telemost_url, parse_telemost_url


class TelemostLinkTests(unittest.TestCase):
    def test_parse_join_url(self) -> None:
        link = parse_telemost_url("https://telemost.yandex.ru/j/abc123_45")
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link.conference_id, "abc123_45")

    def test_find_in_list(self) -> None:
        link = find_telemost_url(["https://example.com", "https://telemost.yandex.ru/j/x"])
        self.assertIsNotNone(link)


if __name__ == "__main__":
    unittest.main()
