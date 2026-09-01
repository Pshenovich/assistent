"""Маршрутизация Zoom по regex."""

import unittest

from assistant.nlu.regex import parse_zoom_route, parse_zoom_instant


class TestZoomRegex(unittest.TestCase):
    def test_instant_phrases(self) -> None:
        for text in (
            "дай зум",
            "создай зум",
            "сделай зум",
            "зум",
            "zoom",
            "дай ссылку на зум",
        ):
            r = parse_zoom_route(text)
            self.assertIsNotNone(r, text)
            assert r is not None
            self.assertEqual(r.sub_intent, "zoom")

    def test_update_route(self) -> None:
        r = parse_zoom_route("перенеси зум на 15:00")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.sub_intent, "zoom_update")

    def test_delete_route(self) -> None:
        r = parse_zoom_route("удали зум")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.sub_intent, "zoom_delete")
        r_en = parse_zoom_route("delete zoom")
        self.assertIsNotNone(r_en)
        assert r_en is not None
        self.assertEqual(r_en.sub_intent, "zoom_delete")

    def test_delete_zoom_not_instant(self) -> None:
        r = parse_zoom_route("delete zoom")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.sub_intent, "zoom_delete")
        self.assertNotEqual(r.sub_intent, "zoom")

    def test_reschedule_route_english(self) -> None:
        r = parse_zoom_route("reschedule zoom to 15:00 tomorrow")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.sub_intent, "zoom_update")

    def test_calendar_create_not_zoom(self) -> None:
        r = parse_zoom_route("встреча завтра в 15:00")
        self.assertIsNone(r)

    def test_parse_zoom_instant_helper(self) -> None:
        self.assertTrue(parse_zoom_instant("создай зум"))


if __name__ == "__main__":
    unittest.main()
