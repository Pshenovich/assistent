from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from assistant.bot.handlers import is_stale_update


def test_stale_update_threshold(monkeypatch):
    monkeypatch.delenv("BOT_STALE_UPDATE_MIN", raising=False)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    fresh = SimpleNamespace(message=SimpleNamespace(date=now - timedelta(minutes=3)))
    old = SimpleNamespace(message=SimpleNamespace(date=now - timedelta(minutes=20)))
    assert not is_stale_update(fresh, now=now)
    assert is_stale_update(old, now=now)
    assert not is_stale_update(SimpleNamespace(message=None, callback_query=object()), now=now)
    monkeypatch.setenv("BOT_STALE_UPDATE_MIN", "30")
    assert not is_stale_update(old, now=now)
