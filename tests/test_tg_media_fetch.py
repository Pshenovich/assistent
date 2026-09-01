"""Тесты лимитов скачивания медиа из Telegram."""

from __future__ import annotations

import pytest

from assistant.config import (
    TELEGRAM_CLOUD_BOT_FILE_LIMIT_BYTES,
    effective_telegram_download_limit_bytes,
)
from assistant.lib.tg_media_fetch import (
    MediaTooLargeError,
    _check_size_before_download,
    _host_path_candidates,
    _remap_container_path,
    _relative_media_path,
    format_media_too_large_message,
)


def test_cloud_limit_is_20mb(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_API_BASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_MAX_DOWNLOAD_MB", "50")
    limit = effective_telegram_download_limit_bytes()
    assert limit == TELEGRAM_CLOUD_BOT_FILE_LIMIT_BYTES


def test_local_api_raises_limit(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("TELEGRAM_BOT_MAX_DOWNLOAD_MB", "100")
    monkeypatch.setenv("TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB", "200")
    monkeypatch.setenv("URL_MAX_DOWNLOAD_MB", "500")
    limit = effective_telegram_download_limit_bytes()
    assert limit == 100 * 1024 * 1024


def test_check_size_raises(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_API_BASE_URL", raising=False)
    with pytest.raises(MediaTooLargeError):
        _check_size_before_download(25 * 1024 * 1024)


def test_format_message_mentions_link():
    msg = format_media_too_large_message(30 * 1024 * 1024, 20 * 1024 * 1024)
    assert "ссылк" in msg.lower()
    assert "20" in msg


def test_relative_path_from_mangled_url():
    token = "123:ABC"
    raw = (
        "https://api.telegram.org/file/bot123:ABC"
        "//var/lib/telegram-bot-api/123:ABC/videos/file_0.mp4"
    )
    assert _relative_media_path(raw, token) == "videos/file_0.mp4"


def test_relative_path_from_absolute():
    token = "123:ABC"
    raw = "/var/lib/telegram-bot-api/123:ABC/videos/file_0.mp4"
    assert _relative_media_path(raw, token) == "videos/file_0.mp4"


def test_remap_container_path(monkeypatch, tmp_path):
    data_dir = tmp_path / "tg-data"
    data_dir.mkdir()
    monkeypatch.setenv("TELEGRAM_BOT_API_DATA_DIR", str(data_dir))
    raw = "/var/lib/telegram-bot-api/123:ABC/voice/file_0.oga"
    assert _remap_container_path(raw) == str(data_dir / "123:ABC/voice/file_0.oga")


def test_host_path_candidates_include_remapped(monkeypatch, tmp_path):
    token = "123:ABC"
    data_dir = tmp_path / "tg-data"
    voice = data_dir / token / "voice"
    voice.mkdir(parents=True)
    f = voice / "file_0.oga"
    f.write_bytes(b"x")
    monkeypatch.setenv("TELEGRAM_BOT_API_DATA_DIR", str(data_dir))
    raw = f"/var/lib/telegram-bot-api/{token}/voice/file_0.oga"
    candidates = _host_path_candidates(raw, token)
    assert any(p.is_file() for p in candidates)
