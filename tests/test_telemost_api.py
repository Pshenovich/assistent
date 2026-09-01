"""Telemost API client tests."""

import unittest
from unittest import mock

from assistant.integrations import telemost_api


class TelemostApiTests(unittest.TestCase):
    def test_org_restricted_error(self) -> None:
        exc = telemost_api.TelemostApiError(
            "x",
            status_code=403,
            error_code="ApiRestrictedToOrganizations",
        )
        self.assertTrue(telemost_api.is_org_restricted_error(exc))

    @mock.patch("assistant.integrations.telemost_api.requests.post")
    def test_create_conference(self, post: mock.MagicMock) -> None:
        resp = mock.MagicMock()
        resp.ok = True
        resp.json.return_value = {
            "id": "abc123",
            "join_url": "https://telemost.yandex.ru/j/abc123",
        }
        post.return_value = resp
        data = telemost_api.create_conference("token", auto_summarization=True)
        self.assertEqual(data["id"], "abc123")
        payload = post.call_args.kwargs["json"]
        self.assertTrue(payload.get("is_auto_summarization_enabled"))
        self.assertEqual(payload.get("waiting_room_level"), "PUBLIC")


if __name__ == "__main__":
    unittest.main()
