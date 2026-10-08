"""Изоляция тестов от реального окружения.

Модуль выполняется pytest до импорта тестов, поэтому:
- `.env` не подгружается (`assistant.config` и `usage_server` грузят его с
  override=True, иначе боевые токены и пути перетёрли бы тестовые);
- все хранилища (SQLite/JSON/каталоги токенов) переадресуются во временный
  каталог сессии через их штатные переменные окружения.

Тест может переопределить любую переменную сам (monkeypatch.setenv и т.п.).
CONTACTS_PATH / CONTACTS_USER_DIR не задаются: тесты contacts_store
изолируются подменой ROOT, а переменная окружения имеет приоритет над ним.
Отключить изоляцию для ручной отладки: LEO_TESTS_USE_DOTENV=1.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

if os.getenv("LEO_TESTS_USE_DOTENV", "").strip() != "1":
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False

_SANDBOX = Path(tempfile.mkdtemp(prefix="leo-tests-"))

_FILE_ENV = {
    "BOARD_DB_PATH": "data/board.sqlite",
    "MEETING_RECORDINGS_DB_PATH": "data/meeting_recordings.sqlite",
    "CHAT_DIGEST_DB_PATH": "data/chat_digest.sqlite",
    "KNOWLEDGE_DB_PATH": "data/knowledge_bases.sqlite",
    "NOTES_DB_PATH": "notes.sqlite",
    "CALENDAR_TASKS_DB_PATH": "data/calendar_tasks.sqlite",
    "CHAT_THREADS_DB_PATH": "data/chat_threads.sqlite",
    "USER_AGENTS_DB_PATH": "data/user_agents.sqlite",
    "USAGE_DB_PATH": "usage.sqlite",
    "TELEGRAM_REGISTRY_PATH": "data/telegram_registry.json",
    "MEETING_INVITES_NOTIFIED_PATH": "data/meeting_invites_notified.json",
    "MEETING_REMINDERS_SENT_PATH": "data/meeting_reminders_sent.json",
    "REMINDERS_STORE_PATH": "reminders.json",
    "RESEARCH_JOBS_PATH": "data/research_jobs.json",
    "PAEI_JOBS_PATH": "data/paei_jobs.json",
    "CALENDAR_PENDING_STORE_PATH": "calendar_pending.json",
    "REMINDER_PREFERENCES_PATH": "reminder_preferences.json",
    "TELEGRAM_ACCESS_ALLOWLIST_PATH": "allowed_telegram_access.json",
    "TELEGRAM_ACCESS_DONATELLO_ALLOWLIST_PATH": "allowed_telegram_access_donatello.json",
    "TELEGRAM_ACCESS_REQUESTS_PATH": "access_requests.json",
    "TELEGRAM_ACCESS_DONATELLO_REQUESTS_PATH": "access_requests_donatello.json",
}

_DIR_ENV = {
    "USER_PREFS_DIR": "data/users",
    "YANDEX_DISK_USER_TOKENS_DIR": "tokens/yandex_disk",
    "YANDEX_DISK_OAUTH_PENDING_DIR": "pending/yandex_disk",
    "ZOOM_USER_TOKENS_DIR": "tokens/zoom",
    "ZOOM_OAUTH_PENDING_DIR": "pending/zoom",
    "GOOGLE_CALENDAR_USER_TOKENS_DIR": "tokens/google",
    "GOOGLE_OAUTH_PENDING_DIR": "pending/google",
    "TELEMOST_USER_TOKENS_DIR": "tokens/telemost",
    "TELEMOST_OAUTH_PENDING_DIR": "pending/telemost",
    "TELEMOST_MAIL_STATE_DIR": "telemost_mail",
    "BITRIX_MCP_USER_TOKENS_DIR": "tokens/bitrix_mcp",
    "TODOIST_USER_TOKENS_DIR": "tokens/todoist",
    "TODOIST_OAUTH_PENDING_DIR": "pending/todoist",
    "TELEGRAM_BOT_API_DATA_DIR": "telegram-bot-api",
}

_SANDBOX_ENV: dict[str, str] = {}

for _key, _rel in _FILE_ENV.items():
    _path = _SANDBOX / _rel
    _path.parent.mkdir(parents=True, exist_ok=True)
    _SANDBOX_ENV[_key] = str(_path)

for _key, _rel in _DIR_ENV.items():
    _path = _SANDBOX / _rel
    _path.mkdir(parents=True, exist_ok=True)
    _SANDBOX_ENV[_key] = str(_path)

os.environ.update(_SANDBOX_ENV)


def pytest_runtest_setup(item) -> None:
    # Часть тестов в teardown делает os.environ.pop(...) для своих путей,
    # из-за чего следующие тесты писали бы в реальные data/*.sqlite.
    for key, value in _SANDBOX_ENV.items():
        os.environ.setdefault(key, value)


def pytest_unconfigure(config) -> None:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
