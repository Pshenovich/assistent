"""Подключение Miro по OAuth2 (код авторизации) — для картинок при вставке схем из Miro на доску.

Картинки качает браузер (api.miro.com открыт для сайтов, CORS), а сервер только меняет код на ключ
и обновляет его. Сервер Орбиты Miro не пускает (403 по стране), поэтому обмен идёт через прокси
Obuchat: MIRO_API_BASE — адрес, который проксирует https://api.miro.com (без завершающего «/»).

.env: MIRO_OAUTH_CLIENT_ID, MIRO_OAUTH_CLIENT_SECRET, MIRO_OAUTH_REDIRECT_URI
(как в настройках приложения Miro → Redirect URI), MIRO_API_BASE.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests

HERE = Path(__file__).resolve().parent

MIRO_AUTHORIZE_URL = "https://miro.com/oauth/authorize"
STATE_TTL_SEC = 900
TOKEN_EXPIRY_SKEW_SEC = 120


def _dir(env: str, default: str) -> Path:
    raw = os.getenv(env, "").strip()
    p = Path(raw).expanduser() if raw else HERE / default
    if not p.is_absolute():
        p = (HERE / p).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _tokens_dir() -> Path:
    return _dir("MIRO_USER_TOKENS_DIR", "miro_user_tokens")


def _pending_dir() -> Path:
    return _dir("MIRO_OAUTH_PENDING_DIR", "miro_oauth_pending")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def is_configured() -> bool:
    return bool(_env("MIRO_OAUTH_CLIENT_ID") and _env("MIRO_OAUTH_CLIENT_SECRET") and _env("MIRO_OAUTH_REDIRECT_URI"))


def api_base() -> str:
    return (_env("MIRO_API_BASE") or "https://api.miro.com").rstrip("/")


def _token_url() -> str:
    return api_base() + "/v1/oauth/token"


def user_token_path(user_id: int) -> Path:
    return _tokens_dir() / f"{int(user_id)}.json"


def register_oauth_state(user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    now = time.time()
    (_pending_dir() / f"{state}.json").write_text(
        json.dumps({"user_id": int(user_id), "expires_at": now + STATE_TTL_SEC}), encoding="utf-8"
    )
    for p in _pending_dir().glob("*.json"):
        try:
            if float(json.loads(p.read_text(encoding="utf-8")).get("expires_at") or 0) < now:
                p.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            p.unlink(missing_ok=True)
    return state


def build_authorization_url(state: str) -> str:
    if not is_configured():
        raise RuntimeError("Подключение Miro не настроено на сервере")
    q = {
        "response_type": "code",
        "client_id": _env("MIRO_OAUTH_CLIENT_ID"),
        "redirect_uri": _env("MIRO_OAUTH_REDIRECT_URI"),
        "state": state,
    }
    return f"{MIRO_AUTHORIZE_URL}?{urlencode(q)}"


def _take_state(state: str) -> Optional[int]:
    st = (state or "").strip()
    if not st or "/" in st or ".." in st:
        return None
    path = _pending_dir() / f"{st}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
        if time.time() > float(data.get("expires_at") or 0):
            return None
        return int(data.get("user_id"))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _token_request(params: dict[str, str]) -> dict[str, Any]:
    """POST /v1/oauth/token: Miro принимает параметры в строке запроса."""
    q = dict(params, client_id=_env("MIRO_OAUTH_CLIENT_ID"), client_secret=_env("MIRO_OAUTH_CLIENT_SECRET"))
    try:
        r = requests.post(_token_url() + "?" + urlencode(q), timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f"Miro не ответил: {type(e).__name__}") from e
    if r.status_code >= 400:
        # Тело ответа без параметров запроса: в нём нет секрета, но и пользователю оно не нужно целиком.
        raise RuntimeError(f"Miro OAuth: {r.status_code} {(r.text or '')[:200]}")
    try:
        body = r.json()
    except ValueError as e:
        raise RuntimeError("Miro OAuth: ответ не JSON") from e
    if not isinstance(body, dict) or not str(body.get("access_token") or "").strip():
        raise RuntimeError("Miro OAuth: в ответе нет ключа")
    return body


def _store_from(body: dict[str, Any], prev: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    store: dict[str, Any] = {"access_token": str(body["access_token"]).strip(), "saved_at": time.time()}
    rt = str(body.get("refresh_token") or "").strip() or str((prev or {}).get("refresh_token") or "")
    if rt:
        store["refresh_token"] = rt
    # Бессрочный ключ (в приложении Miro выключено «Expire user authorization token»): Miro присылает
    # срок 0 или не присылает вовсе — срока нет. Иначе ключ считался истёкшим сразу после входа.
    try:
        exp_in = int(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        exp_in = 0
    if exp_in > TOKEN_EXPIRY_SKEW_SEC and rt:
        store["expires_at"] = time.time() + exp_in - TOKEN_EXPIRY_SKEW_SEC
    for k in ("team_id", "user_id", "scope"):
        if body.get(k) is not None:
            store[k] = str(body[k])
    return store


def _save(user_id: int, store: dict[str, Any]) -> None:
    path = user_token_path(user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def exchange_code_and_save_token(code: str, state: str) -> int:
    uid = _take_state(state)
    if uid is None:
        raise ValueError("Ссылка подключения Miro устарела — нажмите «Подключить Miro» ещё раз.")
    body = _token_request({
        "grant_type": "authorization_code",
        "code": (code or "").strip(),
        "redirect_uri": _env("MIRO_OAUTH_REDIRECT_URI"),
    })
    _save(uid, _store_from(body))
    return uid


def _load(user_id: int) -> Optional[dict[str, Any]]:
    try:
        store = json.loads(user_token_path(user_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return store if isinstance(store, dict) and store.get("access_token") else None


def is_connected(user_id: int) -> bool:
    return _load(user_id) is not None


def get_access_token(user_id: int) -> Optional[str]:
    """Действующий ключ (при сроке — обновляет через refresh_token); None — не подключено или ключ отозван."""
    store = _load(user_id)
    if not store:
        return None
    exp = store.get("expires_at")
    rt = str(store.get("refresh_token") or "")
    # Обновить нечем — отдаём как есть: просрочен ли ключ, решит сам Miro (доска тогда попросит подключить заново).
    if exp is not None and rt and time.time() >= float(exp):
        try:
            store = _store_from(_token_request({"grant_type": "refresh_token", "refresh_token": rt}), store)
        except RuntimeError:
            return None
        _save(user_id, store)
    return str(store["access_token"])


def remove_user_token(user_id: int) -> bool:
    try:
        user_token_path(user_id).unlink()
        return True
    except OSError:
        return False
