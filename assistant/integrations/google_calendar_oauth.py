"""Per-user Google Calendar OAuth (web application flow).

Общий модуль для bot.py и usage_server.py: state хранится в файлах, чтобы работало
при двух отдельных процессах.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from assistant.config import ROOT

HERE = Path(__file__).resolve().parent

SCOPES = ["https://www.googleapis.com/auth/calendar"]

STATE_TTL_SEC = int(os.getenv("GOOGLE_OAUTH_STATE_TTL_SEC", "900") or "900")


def _tokens_dir() -> Path:
    raw = os.getenv("GOOGLE_CALENDAR_USER_TOKENS_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "google_user_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pending_dir() -> Path:
    raw = os.getenv("GOOGLE_OAUTH_PENDING_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "google_oauth_pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_token_path(telegram_user_id: int) -> Path:
    return _tokens_dir() / f"{int(telegram_user_id)}.json"


def web_client_secrets_path() -> Path:
    raw = os.getenv("GOOGLE_WEB_CLIENT_SECRETS_PATH", "").strip()
    if not raw:
        raise RuntimeError(
            "Не задан GOOGLE_WEB_CLIENT_SECRETS_PATH (JSON OAuth client типа Web application)."
        )
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # .env задаёт путь от корня проекта (рядом с bot.py), не от integrations/
        p = (ROOT / p).resolve()
    return p


def redirect_uri() -> str:
    u = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if not u:
        raise RuntimeError(
            "Не задан GOOGLE_OAUTH_REDIRECT_URI (должен совпадать с Authorized redirect URIs в Google Cloud)."
        )
    return u


def register_oauth_state(telegram_user_id: int) -> str:
    """Создаёт state и файл привязки state → telegram_user_id. Возвращает state."""
    state = secrets.token_urlsafe(32)
    now = time.time()
    path = _pending_dir() / f"{state}.json"
    path.write_text(
        json.dumps(
            {"telegram_user_id": int(telegram_user_id), "expires_at": now + STATE_TTL_SEC},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _purge_stale_pending(now)
    return state


def _purge_stale_pending(now: float) -> None:
    try:
        d = _pending_dir()
        for p in d.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                exp = float(data.get("expires_at") or 0)
                if exp < now:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
            except (json.JSONDecodeError, TypeError, ValueError):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
    except OSError:
        pass


def _validate_oauth_state_file(state: str) -> tuple[int, Path] | None:
    """Проверяет pending и возвращает (telegram_user_id, path) без удаления."""
    st = (state or "").strip()
    if not st or "/" in st or ".." in st:
        return None
    path = _pending_dir() / f"{st}.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
        uid = int(data.get("telegram_user_id"))
        exp = float(data.get("expires_at") or 0)
        if time.time() > exp:
            return None
        return uid, path
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def build_authorization_url(state: str) -> str:
    from google_auth_oauthlib.flow import Flow

    st = (state or "").strip()
    pending_path = _pending_dir() / f"{st}.json"
    if not pending_path.exists():
        raise RuntimeError(
            "Сессия OAuth не найдена. Запросите ссылку снова: /calendar_auth"
        )

    secrets_path = web_client_secrets_path()
    if not secrets_path.is_file():
        raise RuntimeError(
            "Файл OAuth-клиента Google (Web application) не найден на сервере: "
            f"{secrets_path}. Файл не входит в git — скопируйте JSON с Google Cloud "
            "и задайте GOOGLE_WEB_CLIENT_SECRETS_PATH в .env рядом с bot.py / usage_server."
        )

    # Без PKCE: иначе code_verifier привязан к одному экземпляру Flow в боте, а токен
    # меняет usage_server в другом процессе — Google отвечает Missing code verifier.
    # Для OAuth Web client с client_secret PKCE не обязателен.
    flow = Flow.from_client_secrets_file(
        str(secrets_path),
        scopes=SCOPES,
        redirect_uri=redirect_uri(),
        autogenerate_code_verifier=False,
    )
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        include_granted_scopes="true",
    )
    return url


def exchange_code_and_save_token(code: str, state: str) -> int:
    """Обменивает code на токен и сохраняет в google_user_tokens/{id}.json."""
    from google_auth_oauthlib.flow import Flow

    parsed = _validate_oauth_state_file(state)
    if parsed is None:
        raise ValueError(
            "Сессия авторизации устарела или неверная. Запросите ссылку снова: /calendar_auth"
        )
    uid, pending_path = parsed

    flow = Flow.from_client_secrets_file(
        str(web_client_secrets_path()),
        scopes=SCOPES,
        redirect_uri=redirect_uri(),
        autogenerate_code_verifier=False,
    )
    try:
        flow.fetch_token(code=code)
    except Exception:
        raise
    else:
        try:
            pending_path.unlink(missing_ok=True)
        except OSError:
            pass
    creds = flow.credentials
    path = user_token_path(uid)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        from assistant.lib.calendar_user_lookup import register_user_calendar_email

        register_user_calendar_email(uid)
    except Exception as e:
        print(f"[google_calendar_oauth] email_index uid={uid} err={e!r}")
    return uid


def remove_user_token(telegram_user_id: int) -> bool:
    p = user_token_path(telegram_user_id)
    try:
        if p.exists():
            p.unlink()
            try:
                from assistant.lib.calendar_user_lookup import invalidate_email_index

                invalidate_email_index()
            except Exception:
                pass
            return True
    except OSError:
        pass
    return False
