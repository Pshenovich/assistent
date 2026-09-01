"""Per-user Todoist OAuth2 (authorization code). Общий модуль для bot.py и usage_server.py."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

HERE = Path(__file__).resolve().parent

TODOIST_AUTHORIZE_URL = "https://app.todoist.com/oauth/authorize"
TODOIST_ACCESS_TOKEN_URL = "https://api.todoist.com/oauth/access_token"

STATE_TTL_SEC = int(os.getenv("TODOIST_OAUTH_STATE_TTL_SEC", "900") or "900")

# Запас до истечения access_token (секунды).
TOKEN_EXPIRY_SKEW_SEC = int(os.getenv("TODOIST_TOKEN_EXPIRY_SKEW_SEC", "120") or "120")


def _tokens_dir() -> Path:
    raw = os.getenv("TODOIST_USER_TOKENS_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "todoist_user_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pending_dir() -> Path:
    raw = os.getenv("TODOIST_OAUTH_PENDING_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "todoist_oauth_pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_token_path(telegram_user_id: int) -> Path:
    return _tokens_dir() / f"{int(telegram_user_id)}.json"


def client_id() -> str:
    v = os.getenv("TODOIST_OAUTH_CLIENT_ID", "").strip()
    if not v:
        raise RuntimeError("Не задан TODOIST_OAUTH_CLIENT_ID в .env")
    return v


def client_secret() -> str:
    v = os.getenv("TODOIST_OAUTH_CLIENT_SECRET", "").strip()
    if not v:
        raise RuntimeError("Не задан TODOIST_OAUTH_CLIENT_SECRET в .env")
    return v


def redirect_uri() -> str:
    u = os.getenv("TODOIST_OAUTH_REDIRECT_URI", "").strip()
    if not u:
        raise RuntimeError(
            "Не задан TODOIST_OAUTH_REDIRECT_URI (как в Todoist App Management → OAuth redirect URLs)."
        )
    return u


def oauth_scope() -> str:
    return os.getenv("TODOIST_OAUTH_SCOPE", "data:read_write").strip() or "data:read_write"


def register_oauth_state(telegram_user_id: int) -> str:
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
    st = (state or "").strip()
    pending_path = _pending_dir() / f"{st}.json"
    if not pending_path.exists():
        raise RuntimeError("Сессия OAuth не найдена. Запросите ссылку снова: /todoist_auth")

    q = {
        "client_id": client_id(),
        "scope": oauth_scope(),
        "state": state,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
    }
    return f"{TODOIST_AUTHORIZE_URL}?{urlencode(q)}"


def _persist_token_store(telegram_user_id: int, store: dict[str, object]) -> None:
    path = user_token_path(telegram_user_id)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_token_response_to_store(
    store: dict[str, object], body: dict[str, object]
) -> None:
    at = body.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise RuntimeError(f"Todoist OAuth: нет access_token в ответе: {body!r}")
    store["access_token"] = at.strip()
    rt = body.get("refresh_token")
    if isinstance(rt, str) and rt.strip():
        store["refresh_token"] = rt.strip()
    exp_in = body.get("expires_in")
    if exp_in is not None:
        try:
            store["expires_at"] = time.time() + int(exp_in) - TOKEN_EXPIRY_SKEW_SEC
        except (TypeError, ValueError):
            pass
    sc = body.get("scope")
    if isinstance(sc, str) and sc.strip():
        store["scope"] = sc.strip()


def exchange_code_and_save_token(code: str, state: str) -> int:
    parsed = _validate_oauth_state_file(state)
    if parsed is None:
        raise ValueError(
            "Сессия авторизации устарела или неверная. Запросите ссылку снова: /todoist_auth"
        )
    uid, pending_path = parsed

    try:
        r = requests.post(
            TODOIST_ACCESS_TOKEN_URL,
            data={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "code": (code or "").strip(),
                "redirect_uri": redirect_uri(),
            },
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Todoist OAuth: обмен code не удался: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Todoist OAuth: ответ не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Todoist OAuth: неожиданный ответ: {body!r}")

    store: dict[str, object] = {}
    _apply_token_response_to_store(store, body)
    _persist_token_store(uid, store)

    try:
        pending_path.unlink(missing_ok=True)
    except OSError:
        pass
    return uid


def _refresh_store(path: Path, store: dict[str, object]) -> None:
    rt = store.get("refresh_token")
    if not isinstance(rt, str) or not rt.strip():
        raise RuntimeError("Todoist: нет refresh_token, выполните /todoist_auth снова.")
    try:
        r = requests.post(
            TODOIST_ACCESS_TOKEN_URL,
            data={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "grant_type": "refresh_token",
                "refresh_token": rt.strip(),
            },
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Todoist: обновление токена не удалось: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Todoist: ответ refresh не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Todoist: неожиданный ответ refresh: {body!r}")

    prev_rt = store.get("refresh_token")
    _apply_token_response_to_store(store, body)
    # Grace window: Todoist может не вернуть refresh_token в повторном ответе — сохраняем старый.
    if "refresh_token" not in body or not (
        isinstance(body.get("refresh_token"), str) and str(body.get("refresh_token")).strip()
    ):
        if isinstance(prev_rt, str) and prev_rt.strip():
            store["refresh_token"] = prev_rt.strip()

    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def get_access_token_for_user(telegram_user_id: int) -> str:
    """Возвращает действующий access_token; при необходимости обновляет через refresh_token."""
    path = user_token_path(telegram_user_id)
    if not path.exists():
        raise RuntimeError(
            "Todoist не подключён. Выполните /todoist_auth и откройте ссылку в браузере."
        )
    try:
        raw = path.read_text(encoding="utf-8")
        store = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError) as e:
        raise RuntimeError(f"Todoist: не удалось прочитать токен: {e}") from e
    if not isinstance(store, dict):
        raise RuntimeError("Todoist: повреждённый файл токена.")

    at = store.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise RuntimeError("Todoist: в файле нет access_token, выполните /todoist_auth.")

    exp = store.get("expires_at")
    need_refresh = False
    if exp is not None:
        try:
            need_refresh = time.time() >= float(exp)
        except (TypeError, ValueError):
            need_refresh = False

    if need_refresh:
        _refresh_store(path, store)
        at2 = store.get("access_token")
        if isinstance(at2, str) and at2.strip():
            return at2.strip()

    return at.strip()


def remove_user_token(telegram_user_id: int) -> bool:
    p = user_token_path(telegram_user_id)
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False
