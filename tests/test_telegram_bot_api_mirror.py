"""Зеркало Bot API вне РФ (TELEGRAM_BOT_API_BASE_URL не localhost): без local mode, облачные лимиты."""

from assistant import config
from assistant.lib import telegram_bot_api, telegram_notify


def test_loopback_detection(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    assert config.telegram_bot_api_is_loopback()
    assert config.telegram_local_bot_api_enabled()
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "https://tg-mirror.example.org")
    assert not config.telegram_bot_api_is_loopback()
    assert not config.telegram_local_bot_api_enabled()
    assert config.telegram_api_root() == "https://tg-mirror.example.org"
    monkeypatch.delenv("TELEGRAM_BOT_API_BASE_URL")
    assert config.telegram_api_root() == "https://api.telegram.org"


class _Builder:
    def __init__(self):
        self.calls = {}

    def base_url(self, v):
        self.calls["base_url"] = v
        return self

    def base_file_url(self, v):
        self.calls["base_file_url"] = v
        return self

    def local_mode(self, v):
        self.calls["local_mode"] = v
        return self


def test_mirror_builder_no_local_mode(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "https://tg-mirror.example.org")
    b = telegram_bot_api.attach_local_bot_api(_Builder(), log_prefix="t")
    assert b.calls == {
        "base_url": "https://tg-mirror.example.org/bot",
        "base_file_url": "https://tg-mirror.example.org/file/bot",
    }


def test_notify_urls_use_mirror(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "https://tg-mirror.example.org")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.delenv("TELEGRAM_RICH_BOT_API_BASE_URL", raising=False)
    assert telegram_notify._bot_api_url("getMe") == "https://tg-mirror.example.org/bot1:x/getMe"
    assert telegram_notify._rich_api_url("sendRichMessage").startswith("https://tg-mirror.example.org/bot")
