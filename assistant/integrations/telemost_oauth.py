"""Per-user Yandex Telemost OAuth2 (authorization code)."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

HERE = Path(__file__).resolve().parent

YANDEX_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_USER_INFO_URL = "https://login.yandex.ru/info"

STATE_TTL_SEC = int(os.getenv("TELEMOST_OAUTH_STATE_TTL_SEC", "900") or "900")
TOKEN_EXPIRY_SKEW_SEC = int(os.getenv("TELEMOST_TOKEN_EXPIRY_SKEW_SEC", "120") or "120")

_REAUTH_MSG = "Выполните /telemost_auth и заново подключите Телемост."
_VERIFICATION_CODE_REDIRECT = "https://oauth.yandex.ru/verification_code"

_PERSONAL_EMAIL_SUFFIXES = ("@yandex.ru", "@ya.ru", "@narod.ru")


def _env_clean(raw: str) -> str:
    v = (raw or "").strip().strip("\ufeff")
    while v.startswith("="):
        v = v[1:].strip()
    return v


def _env_first(*keys: str) -> str:
    for key in keys:
        v = _env_clean(os.getenv(key, ""))
        if v:
            return v
    return ""


def uses_verification_code_flow() -> bool:
    uri = redirect_uri().lower()
    return "verification_code" in uri or uri.endswith("/oob") or uri == "oob"


def _tokens_dir() -> Path:
    raw = os.getenv("TELEMOST_USER_TOKENS_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "telemost_user_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pending_dir() -> Path:
    raw = os.getenv("TELEMOST_OAUTH_PENDING_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "telemost_oauth_pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_token_path(telegram_user_id: int) -> Path:
    return _tokens_dir() / f"{int(telegram_user_id)}.json"


def has_connection(telegram_user_id: int) -> bool:
    return user_token_path(int(telegram_user_id)).is_file()


def client_id() -> str:
    v = _env_first(
        "TELEMOST_OAUTH_CLIENT_ID",
        "YANDEX_DISK_OAUTH_CLIENT_ID",
        "YANDEX_OAUTH_CLIENT_ID",
    )
    if not v:
        raise RuntimeError("Не задан TELEMOST_OAUTH_CLIENT_ID в .env")
    return v


def client_secret() -> str:
    v = _env_first(
        "TELEMOST_OAUTH_CLIENT_SECRET",
        "YANDEX_DISK_OAUTH_CLIENT_SECRET",
        "YANDEX_OAUTH_CLIENT_SECRET",
    )
    if not v:
        raise RuntimeError("Не задан TELEMOST_OAUTH_CLIENT_SECRET в .env")
    return v


def redirect_uri() -> str:
    u = _env_first("TELEMOST_OAUTH_REDIRECT_URI", "YANDEX_REDIRECT_URI")
    if not u:
        raise RuntimeError(
            "Не задан TELEMOST_OAUTH_REDIRECT_URI — как в oauth.yandex.ru → Redirect URI."
        )
    return u


def oauth_scope() -> str:
    raw = (
        os.getenv(
            "TELEMOST_OAUTH_SCOPE",
            "telemost-api:conferences.create telemost-api:conferences.read "
            "telemost-api:conferences.update telemost-api:conferences.delete",
        ).strip()
        or "telemost-api:conferences.create telemost-api:conferences.read "
        "telemost-api:conferences.update telemost-api:conferences.delete"
    )
    return raw


def mail_ingest_scope() -> str:
    return (os.getenv("TELEMOST_MAIL_OAUTH_SCOPE", "mail:imap_ro") or "mail:imap_ro").strip()


def omit_scope_in_authorize() -> bool:
    """Не передавать scope в URL — Яндекс выдаст права из настроек oauth.yandex.ru."""
    raw = os.getenv("TELEMOST_OAUTH_OMIT_SCOPE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def oauth_scope_with_mail() -> str:
    parts = oauth_scope().split()
    for part in mail_ingest_scope().split():
        if part and part not in parts:
            parts.append(part)
    return " ".join(parts)


def _user_pending_path(telegram_user_id: int) -> Path:
    return _pending_dir() / f"user_{int(telegram_user_id)}.json"


def register_oauth_state(telegram_user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    now = time.time()
    payload = {
        "telegram_user_id": int(telegram_user_id),
        "expires_at": now + STATE_TTL_SEC,
    }
    path = _pending_dir() / f"{state}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if uses_verification_code_flow():
        _user_pending_path(int(telegram_user_id)).write_text(
            json.dumps({**payload, "state": state}, ensure_ascii=False),
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


def _validate_user_pending(telegram_user_id: int) -> Path | None:
    path = _user_pending_path(int(telegram_user_id))
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
        exp = float(data.get("expires_at") or 0)
        if time.time() > exp:
            return None
        if int(data.get("telegram_user_id")) != int(telegram_user_id):
            return None
        return path
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _clear_user_pending(telegram_user_id: int) -> None:
    try:
        _user_pending_path(int(telegram_user_id)).unlink(missing_ok=True)
    except OSError:
        pass


def _token_exchange_data(code: str) -> dict[str, str]:
    data = {
        "grant_type": "authorization_code",
        "code": (code or "").strip(),
        "client_id": client_id(),
        "client_secret": client_secret(),
    }
    uri = redirect_uri()
    if uri:
        data["redirect_uri"] = uri
    return data


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


def build_authorization_url(state: str, *, include_mail_scope: bool = False) -> str:
    st = (state or "").strip()
    pending_path = _pending_dir() / f"{st}.json"
    if not pending_path.exists():
        raise RuntimeError(
            "Сессия OAuth не найдена. Запросите ссылку снова: /telemost_auth"
        )
    q = {
        "client_id": client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "state": state,
    }
    if not omit_scope_in_authorize():
        scope = oauth_scope_with_mail() if include_mail_scope else oauth_scope()
        if scope:
            q["scope"] = scope
    return f"{YANDEX_AUTHORIZE_URL}?{urlencode(q)}"


def _persist_token_store(telegram_user_id: int, store: dict[str, object]) -> None:
    path = user_token_path(telegram_user_id)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_token_response_to_store(
    store: dict[str, object], body: dict[str, object]
) -> None:
    at = body.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise RuntimeError(f"Yandex OAuth: нет access_token в ответе: {body!r}")
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
    store["saved_at"] = time.time()


def _email_looks_personal(email: str) -> bool:
    low = (email or "").strip().lower()
    return any(low.endswith(sfx) for sfx in _PERSONAL_EMAIL_SUFFIXES)


def fetch_user_profile(access_token: str) -> dict[str, str]:
    try:
        r = requests.get(
            YANDEX_USER_INFO_URL,
            params={"format": "json"},
            headers={"Authorization": f"OAuth {access_token.strip()}"},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Yandex: не удалось получить профиль: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex: профиль не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Yandex: неожиданный профиль: {body!r}")
    login = str(body.get("login") or body.get("default_email") or "").strip()
    email = str(body.get("default_email") or body.get("login") or "").strip()
    display = str(body.get("display_name") or body.get("real_name") or login or email).strip()
    org_likely = not _email_looks_personal(email or login)
    return {
        "yandex_login": login,
        "yandex_email": email,
        "yandex_display_name": display,
        "telemost_org_likely": org_likely,
    }


def _save_token_for_user(telegram_user_id: int, body: dict[str, object]) -> int:
    uid = int(telegram_user_id)
    store: dict[str, object] = {}
    _apply_token_response_to_store(store, body)
    try:
        profile = fetch_user_profile(str(store["access_token"]))
        store.update(profile)
    except RuntimeError as e:
        print(f"[telemost_oauth] profile fetch failed uid={uid}: {e!r}")
    _persist_token_store(uid, store)
    _clear_user_pending(uid)
    return uid


def exchange_code_and_save_token(code: str, state: str) -> int:
    parsed = _validate_oauth_state_file(state)
    if parsed is None:
        raise ValueError(
            "Сессия авторизации устарела или неверная. Запросите ссылку снова: /telemost_auth"
        )
    uid, pending_path = parsed
    try:
        r = requests.post(
            YANDEX_TOKEN_URL,
            data=_token_exchange_data(code),
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Yandex OAuth: обмен code не удался: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex OAuth: ответ не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Yandex OAuth: неожиданный ответ: {body!r}")
    saved_uid = _save_token_for_user(uid, body)
    try:
        pending_path.unlink(missing_ok=True)
    except OSError:
        pass
    return saved_uid


def exchange_verification_code(code: str, telegram_user_id: int) -> int:
    uid = int(telegram_user_id)
    pending = _validate_user_pending(uid)
    if pending is None and not uses_verification_code_flow():
        raise ValueError(
            "Сначала запросите ссылку: /telemost_auth (сессия устарела)."
        )
    if pending is None:
        raise ValueError(
            "Сначала нажмите «Подключить» или выполните /telemost_auth — сессия устарела."
        )
    try:
        r = requests.post(
            YANDEX_TOKEN_URL,
            data=_token_exchange_data(code),
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Yandex OAuth: обмен code не удался: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex OAuth: ответ не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Yandex OAuth: неожиданный ответ: {body!r}")
    state = ""
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
        state = str(data.get("state") or "")
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    saved_uid = _save_token_for_user(uid, body)
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass
    if state:
        try:
            (_pending_dir() / f"{state}.json").unlink(missing_ok=True)
        except OSError:
            pass
    return saved_uid


def verification_code_instructions() -> str:
    return (
        "После входа Яндекс покажет код на странице. "
        "Вставьте его в мини-приложении: Профиль → Телемост."
    )


def _refresh_store(path: Path, store: dict[str, object]) -> None:
    rt = store.get("refresh_token")
    if not isinstance(rt, str) or not rt.strip():
        raise RuntimeError(f"Yandex: нет refresh_token, {_REAUTH_MSG}")
    try:
        r = requests.post(
            YANDEX_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt.strip(),
                "client_id": client_id(),
                "client_secret": client_secret(),
            },
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Yandex: обновление токена не удалось: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex: ответ refresh не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Yandex: неожиданный ответ refresh: {body!r}")
    prev_rt = store.get("refresh_token")
    _apply_token_response_to_store(store, body)
    if "refresh_token" not in body or not (
        isinstance(body.get("refresh_token"), str) and str(body.get("refresh_token")).strip()
    ):
        if isinstance(prev_rt, str) and prev_rt.strip():
            store["refresh_token"] = prev_rt.strip()
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def get_valid_access_token(telegram_user_id: int) -> str:
    path = user_token_path(telegram_user_id)
    if not path.exists():
        raise RuntimeError(f"Телемост не подключён. {_REAUTH_MSG}")
    try:
        raw = path.read_text(encoding="utf-8")
        store = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError) as e:
        raise RuntimeError(f"Yandex: не удалось прочитать токен: {e}") from e
    if not isinstance(store, dict):
        raise RuntimeError("Yandex: повреждённый файл токена.")
    at = store.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise RuntimeError(f"Yandex: в файле нет access_token, {_REAUTH_MSG}")
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


def get_access_token_for_user(telegram_user_id: int) -> str:
    return get_valid_access_token(telegram_user_id)


def read_token_store(telegram_user_id: int) -> dict[str, object] | None:
    path = user_token_path(telegram_user_id)
    if not path.is_file():
        return None
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return store if isinstance(store, dict) else None


def org_likely(telegram_user_id: int) -> bool | None:
    store = read_token_store(telegram_user_id)
    if not store:
        return None
    val = store.get("telemost_org_likely")
    if isinstance(val, bool):
        return val
    email = str(store.get("yandex_email") or store.get("yandex_login") or "")
    if email:
        return not _email_looks_personal(email)
    return None


def disconnect_user(telegram_user_id: int) -> bool:
    p = user_token_path(telegram_user_id)
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False
