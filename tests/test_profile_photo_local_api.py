"""Фото профиля через локальный Bot API: getFile отдаёт путь в контейнере — читаем файл с диска."""

from __future__ import annotations


class _Resp:
    def __init__(self, data):
        self._data = data
        self.content = b"x"

    def json(self):
        return self._data


def test_profile_photo_read_from_local_bot_api_dir(tmp_path, monkeypatch):
    from assistant.lib import telegram_notify

    token = "123:ABC"
    photo = tmp_path / token / "profile_photos" / "file_7.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"\xff\xd8\xff" + b"0" * 2000)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_BOT_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(telegram_notify, "telegram_api_base", lambda **_: f"http://127.0.0.1:8081/bot{token}")

    def _post(url, json=None, **_):
        if url.endswith("/getUserProfilePhotos"):
            return _Resp({"result": {"photos": [[{"file_id": "f1"}]]}})
        return _Resp({"result": {"file_path": f"/var/lib/telegram-bot-api/{token}/profile_photos/file_7.jpg"}})

    def _get(*a, **k):
        raise AssertionError("в локальном режиме по HTTP не качаем")

    monkeypatch.setattr(telegram_notify.requests, "post", _post)
    monkeypatch.setattr(telegram_notify.requests, "get", _get)
    assert telegram_notify.get_user_profile_photo_bytes(42) == photo.read_bytes()
