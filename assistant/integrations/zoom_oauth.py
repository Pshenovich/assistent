"""Per-user Zoom OAuth2 (authorization code). Общий модуль для bot.py и usage_server.py."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import unquote, urlencode

import requests

HERE = Path(__file__).resolve().parent

ZOOM_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_REVOKE_URL = "https://zoom.us/oauth/revoke"

STATE_TTL_SEC = int(os.getenv("ZOOM_OAUTH_STATE_TTL_SEC", "900") or "900")
TOKEN_EXPIRY_SKEW_SEC = int(os.getenv("ZOOM_TOKEN_EXPIRY_SKEW_SEC", "120") or "120")

_REAUTH_MSG = "Выполните /zoom_auth и заново подключите Zoom."


def _tokens_dir() -> Path:
    raw = os.getenv("ZOOM_USER_TOKENS_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "zoom_user_tokens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pending_dir() -> Path:
    raw = os.getenv("ZOOM_OAUTH_PENDING_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
    else:
        p = HERE / "zoom_oauth_pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_token_path(telegram_user_id: int) -> Path:
    return _tokens_dir() / f"{int(telegram_user_id)}.json"


def has_api_connection(telegram_user_id: int) -> bool:
    """Есть сохранённые OAuth-токены Zoom (API), без проверки срока."""
    return user_token_path(int(telegram_user_id)).is_file()


def client_id() -> str:
    v = (os.getenv("ZOOM_CLIENT_ID", "") or "").strip().strip("\ufeff")
    if not v:
        raise RuntimeError("Не задан ZOOM_CLIENT_ID в .env")
    return v


def client_secret() -> str:
    v = (os.getenv("ZOOM_CLIENT_SECRET", "") or "").strip().strip("\ufeff")
    if not v:
        raise RuntimeError("Не задан ZOOM_CLIENT_SECRET в .env")
    return v


def redirect_uri() -> str:
    u = (os.getenv("ZOOM_OAUTH_REDIRECT_URI", "") or "").strip().strip("\ufeff")
    if not u:
        raise RuntimeError(
            "Не задан ZOOM_OAUTH_REDIRECT_URI (как в Zoom Marketplace → Redirect URL for OAuth)."
        )
    return u


def _host_index_path() -> Path:
    return _tokens_dir() / "_host_index.json"


def _load_host_index() -> dict[str, int]:
    path = _host_index_path()
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        out: dict[str, int] = {}
        for k, v in data.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        return out
    except Exception:
        return {}


def _save_host_index(index: dict[str, int]) -> None:
    path = _host_index_path()
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def register_zoom_host(zoom_user_id: str, telegram_user_id: int) -> None:
    zid = (zoom_user_id or "").strip()
    if not zid:
        return
    idx = _load_host_index()
    idx[zid] = int(telegram_user_id)
    _save_host_index(idx)


def unregister_telegram_user(telegram_user_id: int) -> None:
    uid = int(telegram_user_id)
    idx = _load_host_index()
    kept = {k: v for k, v in idx.items() if int(v) != uid}
    if kept != idx:
        _save_host_index(kept)


def find_telegram_user_by_zoom_host(host_id: str) -> int | None:
    hid = (host_id or "").strip()
    if not hid:
        return None
    hit = _load_host_index().get(hid)
    if hit is not None:
        return int(hit)
    # Fallback: scan token files
    try:
        for p in _tokens_dir().glob("*.json"):
            if p.name.startswith("_"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            zid = str(data.get("zoom_user_id") or "").strip()
            if zid == hid:
                try:
                    return int(p.stem)
                except ValueError:
                    continue
    except OSError:
        pass
    return None


def fetch_and_store_user_profile(telegram_user_id: int, access_token: str) -> dict[str, object]:
    from assistant.integrations.zoom_api import fetch_current_user

    profile = fetch_current_user(access_token)
    path = user_token_path(telegram_user_id)
    try:
        raw = path.read_text(encoding="utf-8")
        store = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        store = {}
    if not isinstance(store, dict):
        store = {}
    zid = str(profile.get("id") or "").strip()
    email = str(profile.get("email") or "").strip()
    if zid:
        store["zoom_user_id"] = zid
        register_zoom_host(zid, telegram_user_id)
    if email:
        store["zoom_email"] = email
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    if first or last:
        store["zoom_display_name"] = " ".join(x for x in (first, last) if x).strip()
    _persist_token_store(telegram_user_id, store)
    return store


def oauth_scope() -> str:
    default = (
        "meeting:write:meeting meeting:read:list_meetings meeting:update:meeting "
        "meeting:delete:meeting user:read:user user:read:token"
    )
    return (os.getenv("ZOOM_OAUTH_SCOPE", default).strip() or default)


def _basic_auth_header() -> str:
    raw = f"{client_id()}:{client_secret()}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def register_oauth_state(telegram_user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    now = time.time()
    path = _pending_dir() / f"{state}.json"
    path.write_text(
        json.dumps(
            {
                "telegram_user_id": int(telegram_user_id),
                "expires_at": now + STATE_TTL_SEC,
                "oauth_client_id": client_id(),
            },
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


def _validate_oauth_state_file(state: str) -> tuple[int, Path, dict[str, object]] | None:
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
        if not isinstance(data, dict):
            return None
        uid = int(data.get("telegram_user_id"))
        exp = float(data.get("expires_at") or 0)
        if time.time() > exp:
            return None
        stored_cid = str(data.get("oauth_client_id") or "").strip()
        if stored_cid and stored_cid != client_id():
            return None
        return uid, path, data
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _recent_token_for_user(telegram_user_id: int, *, max_age_sec: int = 180) -> bool:
    path = user_token_path(telegram_user_id)
    if not path.is_file():
        return False
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(store, dict):
        return False
    if str(store.get("oauth_client_id") or "").strip() != client_id():
        return False
    at = store.get("access_token")
    if not isinstance(at, str) or not at.strip():
        return False
    saved_at = store.get("saved_at")
    try:
        saved_ts = float(saved_at)
    except (TypeError, ValueError):
        return False
    return time.time() - saved_ts <= max_age_sec


def build_authorization_url(state: str) -> str:
    st = (state or "").strip()
    pending_path = _pending_dir() / f"{st}.json"
    if not pending_path.exists():
        raise RuntimeError("Сессия OAuth не найдена. Запросите ссылку снова: /zoom_auth")

    q = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "state": state,
        "scope": oauth_scope(),
    }
    return f"{ZOOM_AUTHORIZE_URL}?{urlencode(q)}"


def _stamp_oauth_client_id(store: dict[str, object]) -> None:
    store["oauth_client_id"] = client_id()


def _persist_token_store(telegram_user_id: int, store: dict[str, object]) -> None:
    _stamp_oauth_client_id(store)
    store["saved_at"] = time.time()
    path = user_token_path(telegram_user_id)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _apply_token_response_to_store(store: dict[str, object], body: dict[str, object]) -> None:
    at = body.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise RuntimeError(f"Zoom OAuth: нет access_token в ответе: {body!r}")
    store["access_token"] = at.strip()
    rt = body.get("refresh_token")
    if isinstance(rt, str) and rt.strip():
        store["refresh_token"] = rt.strip()
    exp_in = body.get("expires_in")
    if exp_in is not None:
        try:
            store["expires_at"] = time.time() + int(exp_in) - TOKEN_EXPIRY_SKEW_SEC
        except (TypeError, ValueError):
            store["expires_at"] = time.time() + 3600 - TOKEN_EXPIRY_SKEW_SEC
    else:
        store["expires_at"] = time.time() + 3600 - TOKEN_EXPIRY_SKEW_SEC
    sc = body.get("scope")
    if isinstance(sc, str) and sc.strip():
        store["scope"] = sc.strip()


def exchange_code_and_save_token(
    code: str,
    state: str,
    *,
    redirect_uri_override: str | None = None,
) -> int:
    parsed = _validate_oauth_state_file(state)
    if parsed is None:
        raise ValueError(
            "Сессия авторизации устарела или неверная. Запросите ссылку снова: /zoom_auth"
        )
    uid, pending_path, pending_data = parsed

    if pending_data.get("exchanged") and _recent_token_for_user(uid):
        try:
            pending_path.unlink(missing_ok=True)
        except OSError:
            pass
        return uid

    raw_code = unquote((code or "").strip())
    # Должен совпадать с параметром redirect_uri в ссылке /oauth/authorize (из .env).
    redir = redirect_uri()
    if redirect_uri_override:
        cb = (redirect_uri_override or "").strip().strip("\ufeff").split("?", 1)[0].split("#", 1)[0]
        if cb != redir:
            print(
                f"[zoom_oauth] WARN URL колбэка {cb!r} != ZOOM_OAUTH_REDIRECT_URI {redir!r} "
                f"— в обмене кода всё равно используется .env; исправьте один из них."
            )

    try:
        r = requests.post(
            ZOOM_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": raw_code,
                "redirect_uri": redir,
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        if not r.ok:
            try:
                err_body = r.json()
            except Exception:
                err_body = (r.text or "")[:800]
            hint = ""
            if r.status_code == 400 and isinstance(err_body, dict):
                err = str(err_body.get("error") or "")
                if err == "invalid_client":
                    hint = (
                        " Проверьте ZOOM_CLIENT_ID и ZOOM_CLIENT_SECRET в .env — "
                        "пара должна быть из одной вкладки Zoom Marketplace (Development или Production)."
                    )
                elif err == "invalid_grant":
                    hint = (
                        " Частые причины: ссылка из старого сообщения в чате или превью Telegram "
                        "уже использовали код — снова /zoom_auth и кнопку «Подключить»; "
                        "обновили страницу колбэка; Client ID/Secret из другой вкладки "
                        "Development/Production; redirect_uri не совпадает с зарегистрированным в Zoom."
                    )
            if (
                r.status_code == 400
                and isinstance(err_body, dict)
                and str(err_body.get("error") or "") == "invalid_grant"
                and _recent_token_for_user(uid)
            ):
                try:
                    pending_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return uid
            raise RuntimeError(
                f"Zoom OAuth: обмен code: HTTP {r.status_code}: {err_body!r}{hint}"
            )
    except requests.RequestException as e:
        raise RuntimeError(f"Zoom OAuth: обмен code не удался: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Zoom OAuth: ответ не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Zoom OAuth: неожиданный ответ: {body!r}")

    store: dict[str, object] = {}
    _apply_token_response_to_store(store, body)
    _persist_token_store(uid, store)
    try:
        pending_data["exchanged"] = True
        pending_path.write_text(json.dumps(pending_data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    try:
        fetch_and_store_user_profile(uid, str(store.get("access_token") or ""))
    except Exception as e:
        print(f"[zoom_oauth] profile_fetch_failed uid={uid} err={e!r}")

    try:
        pending_path.unlink(missing_ok=True)
    except OSError:
        pass
    return uid


def _refresh_error_message(err_body: object, *, http_status: int) -> str:
    if isinstance(err_body, dict):
        err = str(err_body.get("error") or "")
        if err == "invalid_client":
            return (
                "Zoom: неверная пара Client ID / Client Secret на сервере. "
                "В Zoom Marketplace скопируйте Client ID и Client Secret из одной вкладки "
                "(Development или Production) в ZOOM_CLIENT_ID и ZOOM_CLIENT_SECRET."
            )
        if err == "invalid_grant":
            return f"Zoom: сессия Zoom устарела. {_REAUTH_MSG}"
    return f"Zoom: обновление токена: HTTP {http_status}: {err_body!r}"


def _refresh_store(path: Path, store: dict[str, object]) -> None:
    rt = store.get("refresh_token")
    if not isinstance(rt, str) or not rt.strip():
        raise RuntimeError(f"Zoom: нет refresh_token. {_REAUTH_MSG}")
    try:
        r = requests.post(
            ZOOM_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt.strip(),
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        if not r.ok:
            try:
                err_body = r.json()
            except Exception:
                err_body = (r.text or "")[:800]
            msg = _refresh_error_message(err_body, http_status=r.status_code)
            if isinstance(err_body, dict) and err_body.get("error") in (
                "invalid_grant",
                "invalid_client",
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise RuntimeError(msg)
    except requests.RequestException as e:
        raise RuntimeError(f"Zoom: обновление токена не удалось: {e}") from e
    try:
        body = r.json()
    except Exception as e:
        raise RuntimeError(f"Zoom: ответ refresh не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(body, dict):
        raise RuntimeError(f"Zoom: неожиданный ответ refresh: {body!r}")

    prev_rt = store.get("refresh_token")
    _apply_token_response_to_store(store, body)
    if "refresh_token" not in body or not (
        isinstance(body.get("refresh_token"), str) and str(body.get("refresh_token")).strip()
    ):
        if isinstance(prev_rt, str) and prev_rt.strip():
            store["refresh_token"] = prev_rt.strip()

    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_scope_string(raw: str | None) -> set[str]:
    return {s.strip() for s in (raw or "").split() if s.strip()}


def obf_scope_granted(store: dict[str, object] | None) -> bool:
    scopes = _parse_scope_string(str((store or {}).get("scope") or ""))
    return "user:read:token" in scopes or "user:read:token:admin" in scopes


def user_has_granted_scope(telegram_user_id: int, scope_name: str) -> bool:
    """True, если в сохранённом токене пользователя есть указанный scope."""
    want = (scope_name or "").strip()
    if not want:
        return False
    try:
        raw = user_token_path(int(telegram_user_id)).read_text(encoding="utf-8")
        store = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(store, dict):
        return False
    return want in _parse_scope_string(str(store.get("scope") or ""))


def obf_scope_setup_message(*, granted_scope: str | None = None) -> str:
    granted = (granted_scope or "").strip()
    lines = [
        "⚠️ Zoom подключён, но **без scope user:read:token** — Leo не может войти на встречи по новым правилам Zoom (OBF).",
        "",
        "В Zoom Marketplace → ваше приложение Leo:",
        "1. Scopes → добавьте **user:read:token** (User).",
        "2. Сохраните и нажмите **Activate** / пересоберите приложение.",
        "3. В мини-приложении отключите Zoom и подключите снова (/zoom_auth).",
        "",
    ]
    if granted:
        lines.append(f"Сейчас Zoom выдал только: {granted}")
        lines.append("(в списке нет user:read:token — значит scope не включён в Marketplace).")
    else:
        lines.append("Повторный /zoom_auth без шага 1–2 не поможет.")
    return "\n".join(lines)


def obf_reauth_message(telegram_user_id: int | None = None) -> str:
    granted = ""
    if telegram_user_id is not None:
        try:
            raw = user_token_path(int(telegram_user_id)).read_text(encoding="utf-8")
            store = json.loads(raw)
            if isinstance(store, dict):
                granted = str(store.get("scope") or "").strip()
                if obf_scope_granted(store):
                    return (
                        "⚠️ Не удалось получить OBF-токен для этой встречи.\n"
                        "Scope user:read:token есть — возможно, вы не хост встречи "
                        "или встреча создана вне вашего Zoom-аккаунта."
                    )
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return obf_scope_setup_message(granted_scope=granted or None)


def mint_obf_token(telegram_user_id: int, native_meeting_id: str) -> str | None:
    """OBF для Vexa Zoom SDK (scope user:read:token). None — нет scope или ошибка API."""
    mid = (native_meeting_id or "").strip()
    if not mid:
        return None
    try:
        access = get_access_token_for_user(telegram_user_id)
    except RuntimeError as e:
        print(f"[zoom_oauth] mint_obf uid={telegram_user_id}: {e}")
        return None
    try:
        r = requests.get(
            "https://api.zoom.us/v2/users/me/token",
            params={"type": "onbehalf", "meeting_id": mid},
            headers={"Authorization": f"Bearer {access}"},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"[zoom_oauth] mint_obf request err uid={telegram_user_id}: {e!r}")
        return None
    if not r.ok:
        print(
            f"[zoom_oauth] mint_obf HTTP {r.status_code} uid={telegram_user_id}: "
            f"{(r.text or '')[:400]!r}"
        )
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    tok = body.get("token")
    if isinstance(tok, str) and tok.strip():
        return tok.strip()
    return None


def get_access_token_for_user(telegram_user_id: int) -> str:
    """Возвращает действующий access_token; при необходимости обновляет через refresh_token."""
    path = user_token_path(telegram_user_id)
    if not path.exists():
        raise RuntimeError(
            "Zoom не подключён. Выполните /zoom_auth и откройте ссылку в браузере."
        )
    try:
        raw = path.read_text(encoding="utf-8")
        store = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError) as e:
        raise RuntimeError(f"Zoom: не удалось прочитать токен: {e}") from e
    if not isinstance(store, dict):
        raise RuntimeError("Zoom: повреждённый файл токена.")

    stored_cid = str(store.get("oauth_client_id") or "").strip()
    current_cid = client_id()
    if stored_cid and stored_cid != current_cid:
        remove_user_token(telegram_user_id)
        raise RuntimeError(
            f"Zoom: учётные данные приложения изменились. {_REAUTH_MSG}"
        )

    at = store.get("access_token")
    if not isinstance(at, str) or not at.strip():
        raise RuntimeError("Zoom: в файле нет access_token, выполните /zoom_auth.")

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


def revoke_access_token(access_token: str) -> bool:
    """Best-effort revoke at Zoom. Returns True on HTTP success."""
    token = (access_token or "").strip()
    if not token:
        return False
    try:
        r = requests.post(
            ZOOM_REVOKE_URL,
            data={"token": token},
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        if r.ok:
            return True
        print(f"[zoom_oauth] revoke HTTP {r.status_code}: {(r.text or '')[:400]!r}")
    except requests.RequestException as e:
        print(f"[zoom_oauth] revoke failed: {e!r}")
    return False


def remove_user_token(telegram_user_id: int) -> bool:
    unregister_telegram_user(telegram_user_id)
    p = user_token_path(telegram_user_id)
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def disconnect_user(telegram_user_id: int, *, revoke: bool = True) -> bool:
    """Revoke Zoom tokens (if possible) and delete all locally stored Zoom credentials."""
    uid = int(telegram_user_id)
    path = user_token_path(uid)
    if path.is_file() and revoke:
        try:
            raw = path.read_text(encoding="utf-8")
            store = json.loads(raw)
            if isinstance(store, dict):
                at = store.get("access_token")
                if isinstance(at, str) and at.strip():
                    revoke_access_token(at.strip())
        except Exception as e:
            print(f"[zoom_oauth] disconnect revoke uid={uid} err={e!r}")
    return remove_user_token(uid)


def deauthorize_by_zoom_user_id(zoom_user_id: str) -> bool:
    """Purge local Zoom data after Zoom app_deauthorized webhook."""
    tg_uid = find_telegram_user_by_zoom_host((zoom_user_id or "").strip())
    if tg_uid is None:
        print(f"[zoom_oauth] deauth: no telegram user for zoom_user_id={zoom_user_id!r}")
        return False
    return disconnect_user(tg_uid, revoke=False)
