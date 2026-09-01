#!/usr/bin/env python3
"""Сверка Google OAuth с Google Cloud: redirect_uri и client_id (без client_secret).

Запуск на сервере из корня проекта:
  cd /opt/assistant && ./.venv/bin/python3 scripts/google_oauth_env_check.py

Или с явным путём к .env:
  ./.venv/bin/python3 scripts/google_oauth_env_check.py /opt/assistant/.env
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Корень репозитория: …/assistant/scripts/this_file.py → parent.parent
REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    env_path = REPO / ".env"
    if len(sys.argv) > 1:
        env_path = Path(sys.argv[1]).expanduser().resolve()

    try:
        from dotenv import load_dotenv
    except ImportError:
        print("Нужен python-dotenv: pip install python-dotenv", file=sys.stderr)
        return 1

    if not env_path.is_file():
        print(f"Нет файла .env: {env_path}", file=sys.stderr)
        return 1

    load_dotenv(env_path, override=True)

    raw_r = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
    sent = raw_r.strip()
    print("GOOGLE_OAUTH_REDIRECT_URI (repr):", repr(raw_r))
    print("→ в запрос к Google уходит (после .strip()):", repr(sent))
    if raw_r != sent:
        print("  (!) В .env были пробелы/переносы — в Google Console добавьте именно строку после strip.")

    raw_p = os.getenv("GOOGLE_WEB_CLIENT_SECRETS_PATH", "").strip()
    if not raw_p:
        print("GOOGLE_WEB_CLIENT_SECRETS_PATH: не задан")
        return 1

    p = Path(raw_p).expanduser()
    if not p.is_absolute():
        p = (REPO / p).resolve()
    print("JSON клиента:", p)
    print("файл существует:", p.is_file())
    if not p.is_file():
        return 1

    data = json.loads(p.read_text(encoding="utf-8"))
    if "web" in data:
        print('Тип клиента в JSON: Web application ("web") — подходит для GOOGLE_OAUTH_REDIRECT_URI.')
        web = data["web"]
    elif "installed" in data:
        print(
            'Тип клиента в JSON: Desktop ("installed") — для веб-редиректа https://… обычно '
            "нужен отдельный OAuth client типа Web application в Google Cloud и его JSON."
        )
        web = data["installed"]
    else:
        print("Неизвестная структура JSON (нет web/install).")
        web = {}

    cid = (web.get("client_id") or "").strip()
    print("client_id:", cid)
    uris = web.get("redirect_uris") or []
    if uris:
        print("redirect_uris внутри JSON (как при скачивании):", uris)
    print()
    print("В Google Cloud Console откройте Credentials → этот OAuth 2.0 Client ID.")
    print("В блоке «Authorized redirect URIs» должна быть строка, совпадающая с:")
    print(" ", sent)
    print("побайтно: https, хост, путь, слэш в конце — как в .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
