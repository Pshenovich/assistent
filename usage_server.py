#!/usr/bin/env python3
"""Веб-дашборд расхода OpenRouter: агрегаты по дням (UTC) и детализация по операциям."""

from __future__ import annotations

import html as html_lib
import ipaddress
import json
import os
from functools import partial
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, unquote

import requests
from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from assistant.stores import reminders_store  # noqa: E402
from assistant.lib.usage_store import (
    daily_summary,
    distinct_operations,
    events_for_date,
    events_for_user_date,
    operation_label_ru,
    period_totals,
    summary_by_operation,
    usage_journal_meta,
    user_daily_summary,
    user_daily_usage_no_cost,
    user_expenses_by_day,
    user_journal_entries,
    delete_user_journal_event,
    find_summary_event_for_transcript,
    update_user_journal_event,
    get_user_usage_event,
    journal_text_from_raw,
    user_lifetime_stats,
    user_summary_by_operation,
    users_summary,
)  # noqa: E402
from assistant.lib.telegram_webapp_auth import user_payload_from_init_data  # noqa: E402
from assistant.lib.webapp_public import webapp_entry_url  # noqa: E402

app = FastAPI(title="OpenRouter usage")


def _default_security_csp(*, allow_any_frame_ancestor: bool = False) -> str:
    """CSP для мини-приложения / Zoom Home URL (in-client browser)."""
    extra = (os.getenv("SECURITY_CSP_EXTRA", "") or "").strip()
    ancestors = (
        "*"
        if allow_any_frame_ancestor
        else (
            "'self' https://*.zoom.us https://zoom.us "
            "https://web.telegram.org https://*.telegram.org"
        )
    )
    base = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "worker-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https:; connect-src 'self' https:; "
        "frame-src 'self' https://oauth.telegram.org https://telegram.org; "
        f"frame-ancestors {ancestors}"
    )
    if extra:
        return f"{base}; {extra}"
    return base


def _is_local_dev_request(request: Request) -> bool:
    """Локальный uvicorn / Simple Browser в Cursor — без жёсткого frame lock."""
    mode = (os.getenv("MINIAPP_DEV_MODE", "") or "").strip().lower()
    if mode not in ("1", "true", "yes", "on"):
        return False
    host = (request.headers.get("host") or request.url.hostname or "").split(":")[0].lower()
    return host in ("127.0.0.1", "localhost", "::1", "[::1]")


class _OwaspSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Zoom Marketplace и OWASP: обязательные заголовки на Home URL и всём HTTPS-сайте."""

    @staticmethod
    def _request_is_https(request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        xf = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        return xf == "https"

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        if self._request_is_https(request) or os.getenv(
            "SECURITY_HSTS_ON_HTTP", ""
        ).strip().lower() in ("1", "true", "yes"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        local_dev = _is_local_dev_request(request)
        csp = (os.getenv("SECURITY_CSP", "") or "").strip() or _default_security_csp(
            allow_any_frame_ancestor=local_dev
        )
        response.headers["Content-Security-Policy"] = csp
        if local_dev:
            # Cursor/VS Code Simple Browser открывает страницу во iframe — SAMEORIGIN даёт пустой экран.
            if "X-Frame-Options" in response.headers:
                del response.headers["X-Frame-Options"]
        else:
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response


app.add_middleware(_OwaspSecurityHeadersMiddleware)

# Префикс URL для HTML/API дашборда. Корень сайта отдан публичному лендингу.
_usage_dashboard_prefix_env = (os.getenv("USAGE_DASHBOARD_URL_PREFIX", "") or "").strip().rstrip("/")
if not _usage_dashboard_prefix_env:
    _USAGE_DASHBOARD_PREFIX = "/dashboard"
elif _usage_dashboard_prefix_env.startswith("/"):
    _USAGE_DASHBOARD_PREFIX = _usage_dashboard_prefix_env
else:
    _USAGE_DASHBOARD_PREFIX = "/" + _usage_dashboard_prefix_env
dash = APIRouter(prefix=_USAGE_DASHBOARD_PREFIX)
_docs_static_dir = Path(__file__).resolve().parent / "docs"
_landing_page_path = _docs_static_dir / "landing.html"

# Сколько календарных UTC-дней назад от «сегодня» брать по умолчанию на /users и в карточке пользователя (включительно).
_DASH_DEFAULT_USER_RANGE_DAYS = 29

# Чтобы браузер/proxy не отдавал старую HTML-страницу дашборда из кэша.
_DASH_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def _dash_html(content: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        content=content,
        status_code=status_code,
        headers=dict(_DASH_NO_CACHE_HEADERS),
    )


def _dash_url(path: str) -> str:
    """Абсолютный path для ссылок внутри дашборда (например /users, /day/2025-01-01)."""
    p = (path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    if not _USAGE_DASHBOARD_PREFIX:
        return p
    if p == "/":
        return _USAGE_DASHBOARD_PREFIX + "/"
    return _USAGE_DASHBOARD_PREFIX + p


def _dash_journal_meta_html() -> str:
    """Подвал: путь к SQLite и число строк — диагностика «бот пишет не туда»."""
    try:
        m = usage_journal_meta()
        db_esc = html_lib.escape(str(m.get("db_path") or ""))
        n = int(m.get("n_events") or 0)
        return (
            '<p style="margin-top:1rem;color:var(--muted2);font-size:0.8rem;line-height:1.45;">'
            f"Журнал расхода: <code>{db_esc}</code> · записей: <strong>{n}</strong>. "
            "Бот и этот сервис должны использовать один и тот же файл "
            "(<code>USAGE_DB_PATH</code> или каталог проекта)."
            "</p>"
        )
    except Exception as e:
        return (
            '<p style="margin-top:1rem;color:#f87171;font-size:0.85rem;">'
            f"Метаданные журнала: {html_lib.escape(str(e))}"
            "</p>"
        )


@app.get("/oauth/google/callback")
@app.get("/oauth/google/callback/", include_in_schema=False)
async def google_calendar_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> HTMLResponse:
    """OAuth redirect от Google (без USAGE_DASHBOARD_TOKEN — открывается из браузера пользователя)."""
    del request  # сигнатура для совместимости / расширений
    if error:
        msg = html_lib.escape(str(error))
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>Google: {msg}</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><head><meta charset=\"utf-8\"/></head>"
            "<body><p>Не хватает параметров авторизации.</p></body></html>",
            status_code=400,
        )
    try:
        from assistant.integrations.google_calendar_oauth import exchange_code_and_save_token
        from assistant.lib.user_timezone import fetch_google_timezone

        uid = exchange_code_and_save_token(code, state)
        from assistant.bot.access_gate import is_user_allowed
        from assistant.integrations.google_calendar_oauth import remove_user_token

        if not is_user_allowed(int(uid), None):
            remove_user_token(int(uid))
            return HTMLResponse(
                "<html><head><meta charset=\"utf-8\"/></head>"
                "<body><p>Доступ к боту не одобрен. Подключение календаря отменено.</p></body></html>",
                status_code=403,
            )
        try:
            fetch_google_timezone(int(uid))
        except Exception as tz_err:
            print(f"[oauth] timezone_fetch err={tz_err!r}")
    except ValueError as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\"/><title>Календарь подключён</title></head>"
        "<body><p>Google Calendar подключён к боту. Можно закрыть вкладку и вернуться в Telegram.</p></body></html>"
    )


@app.get("/oauth/todoist/callback")
@app.get("/oauth/todoist/callback/", include_in_schema=False)
async def todoist_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> HTMLResponse:
    """OAuth redirect от Todoist."""
    del request
    if error:
        msg = html_lib.escape(str(error))
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>Todoist: {msg}</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><head><meta charset=\"utf-8\"/></head>"
            "<body><p>Не хватает параметров авторизации.</p></body></html>",
            status_code=400,
        )
    try:
        from assistant.integrations.todoist_oauth import exchange_code_and_save_token

        exchange_code_and_save_token(code, state)
    except ValueError as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\"/><title>Todoist подключён</title></head>"
        "<body><p>Todoist подключён к боту. Можно закрыть вкладку и вернуться в Telegram.</p></body></html>"
    )


@app.get("/oauth/yandex-disk/callback")
@app.get("/oauth/yandex-disk/callback/", include_in_schema=False)
async def yandex_disk_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> HTMLResponse:
    """OAuth redirect от Яндекс ID."""
    del request
    if error:
        msg = html_lib.escape(str(error))
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>Яндекс Диск: {msg}</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><head><meta charset=\"utf-8\"/></head>"
            "<body><p>Не хватает параметров авторизации.</p></body></html>",
            status_code=400,
        )
    try:
        from assistant.integrations.yandex_disk_oauth import exchange_code_and_save_token

        exchange_code_and_save_token(code, state)
    except ValueError as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\"/><title>Яндекс Диск подключён</title></head>"
        "<body><p>Яндекс Диск подключён к Leo. Записи Zoom-встреч будут сохраняться в папку "
        "«Leo/Записи встреч». Вернитесь в мини-приложение → Профиль → Яндекс Диск.</p></body></html>"
    )


@app.get("/oauth/telemost/callback")
@app.get("/oauth/telemost/callback/", include_in_schema=False)
async def telemost_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> HTMLResponse:
    """OAuth redirect от Яндекс ID для Телемоста."""
    del request
    if error:
        msg = html_lib.escape(str(error))
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>Телемост: {msg}</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><head><meta charset=\"utf-8\"/></head>"
            "<body><p>Не хватает параметров авторизации.</p></body></html>",
            status_code=400,
        )
    try:
        from assistant.integrations.telemost_oauth import exchange_code_and_save_token

        uid = exchange_code_and_save_token(code, state)
        from assistant.integrations.telemost_oauth import org_likely, read_token_store

        store = read_token_store(uid) or {}
        org_hint = ""
        if org_likely(uid) is False:
            org_hint = (
                "<p><strong>Личный @yandex.ru:</strong> Телемост в браузере работает, "
                "но API для Leo доступен только с аккаунтом на домене организации "
                "(ivan@company.ru). Создавайте ссылки на "
                "<a href=\"https://telemost.yandex.ru\">telemost.yandex.ru</a> "
                "и вставляйте в календарь.</p>"
            )
        who = html_lib.escape(
            str(store.get("yandex_display_name") or store.get("yandex_email") or "")
        )
    except ValueError as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\"/><title>Телемост подключён</title></head>"
        f"<body><p>Телемост подключён{(' — ' + who) if who else ''}. "
        "Вернитесь в мини-приложение → Профиль → Телемост.</p>"
        f"{org_hint}</body></html>"
    )


def _zoom_oauth_query_params(request: Request) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Парсит query из сырого query_string.

    Starlette/FastAPI для query превращают «+» в значениях в пробел (form-urlencoded).
    У Zoom authorization code в параметре code часто содержит «+» (base64-подобная строка);
    тогда обмен на токен даёт invalid_grant. Здесь декодируем только %XX через unquote, не unquote_plus.
    """
    raw = (request.scope.get("query_string") or b"").decode("utf-8", errors="replace")
    code, state, err = None, None, None
    for part in raw.split("&"):
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = unquote(k, errors="replace")
        val = unquote(v, errors="replace")
        if key == "code":
            code = val
        elif key == "state":
            state = val
        elif key == "error":
            err = val
    return code, state, err


@app.get("/oauth/zoom/callback")
@app.get("/oauth/zoom/callback/", include_in_schema=False)
async def zoom_oauth_callback(request: Request) -> HTMLResponse:
    """OAuth redirect от Zoom."""
    code, state, error = _zoom_oauth_query_params(request)
    if error:
        msg = html_lib.escape(str(error))
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>Zoom: {msg}</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><head><meta charset=\"utf-8\"/></head>"
            "<body><p>Не хватает параметров авторизации.</p></body></html>",
            status_code=400,
        )
    try:
        from assistant.integrations.zoom_oauth import exchange_code_and_save_token

        cb_base = str(request.url).split("?", 1)[0].split("#", 1)[0]
        uid = exchange_code_and_save_token(code, state, redirect_uri_override=cb_base)
        from assistant.integrations.zoom_oauth import (
            obf_scope_granted,
            obf_scope_setup_message,
            user_token_path,
        )

        try:
            store = json.loads(user_token_path(uid).read_text(encoding="utf-8"))
        except Exception:
            store = {}
        if isinstance(store, dict) and not obf_scope_granted(store):
            msg = html_lib.escape(
                obf_scope_setup_message(granted_scope=str(store.get("scope") or ""))
            ).replace("\n", "<br/>")
            return HTMLResponse(
                f"<html><head><meta charset=\"utf-8\"/><title>Zoom — нужен scope</title></head>"
                f"<body><p>{msg}</p></body></html>",
                status_code=200,
            )
    except ValueError as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=400,
        )
    except Exception as e:
        return HTMLResponse(
            f"<html><head><meta charset=\"utf-8\"/></head><body><p>{html_lib.escape(str(e))}</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(
        "<html><head><meta charset=\"utf-8\"/><title>Zoom подключён</title></head>"
        "<body><p>Zoom подключён. Закройте вкладку и вернитесь в Telegram "
        "(чат с Leo или мини-приложение → Профиль → Zoom).</p></body></html>"
    )


def _public_webapp_base() -> str:
    base = (
        os.getenv("WEBAPP_PUBLIC_URL", "") or os.getenv("APP_URL", "") or ""
    ).strip().rstrip("/")
    if base:
        return base
    redir = (os.getenv("ZOOM_OAUTH_REDIRECT_URI", "") or "").strip()
    if redir:
        from urllib.parse import urlparse

        p = urlparse(redir)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    return ""


@app.get("/zoom/home")
@app.get("/zoom/home/", include_in_schema=False)
async def zoom_app_home() -> HTMLResponse:
    """Home URL для Zoom Marketplace (in-client browser)."""
    webapp = _public_webapp_base()
    webapp_link = webapp_entry_url() if webapp else "/webapp/"
    bot_user = (os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
    tg_link = f"https://t.me/{bot_user}" if bot_user else ""
    tg_html = (
        f'<p><a href="{html_lib.escape(tg_link)}">Открыть бота в Telegram</a></p>'
        if tg_link
        else ""
    )
    docs = f"{webapp}/docs/zoom.html" if webapp else "/docs/zoom.html"
    privacy = f"{webapp}/webapp/legal/privacy-en.html" if webapp else "/webapp/legal/privacy-en.html"
    support = f"{webapp}/docs/support.html" if webapp else "/docs/support.html"
    body = (
        "<h1>Obuchat Assistant</h1>"
        "<p>Create, reschedule, and delete Zoom meetings from Telegram. "
        "Auto-attach links to Google Calendar and view recordings in the Mini App profile.</p>"
        f"{tg_html}"
        f'<p><a href="{html_lib.escape(webapp_link)}">Mini App</a></p>'
        f'<p><a href="{html_lib.escape(docs)}">Zoom guide</a> · '
        f'<a href="{html_lib.escape(privacy)}">Privacy</a> · '
        f'<a href="{html_lib.escape(support)}">Support</a></p>'
        '<p class="muted">Connect Zoom: Mini App → Profile, or send /zoom_auth in the bot.</p>'
    )
    html = (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>"
        "<title>Obuchat Assistant</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:36rem;margin:2rem auto;padding:0 1rem;"
        "color:#eee;background:#0b0b14}a{color:#4d8eff}.muted{color:#888;font-size:.9rem}</style>"
        "</head><body>" + body + "</body></html>"
    )
    return HTMLResponse(html)


async def _zoom_webhook_dispatch(request: Request) -> JSONResponse:
    from assistant.integrations.zoom_webhook import handle_webhook_request

    body = await request.body()
    sig = request.headers.get("x-zm-signature")
    ts = request.headers.get("x-zm-request-timestamp")
    status, out = handle_webhook_request(body, signature=sig, timestamp=ts)
    return JSONResponse(content=out, status_code=status)


@app.post("/webhook")
@app.post("/webhook/")
async def zoom_webhook_endpoint(request: Request) -> JSONResponse:
    """Zoom Event Subscriptions. URL: ZOOM_WEBHOOK_URL."""
    return await _zoom_webhook_dispatch(request)


@app.post("/webhook/deauthorize")
@app.post("/webhook/deauthorize/")
async def zoom_deauthorize_webhook_endpoint(request: Request) -> JSONResponse:
    """Zoom Deauthorization Notification Endpoint (app_deauthorized)."""
    return await _zoom_webhook_dispatch(request)


async def _vexa_webhook_dispatch(request: Request) -> JSONResponse:
    from assistant.integrations.vexa_webhook import handle_webhook_request

    body = await request.body()
    auth = request.headers.get("authorization")
    status, out = handle_webhook_request(body, authorization=auth)
    return JSONResponse(content=out, status_code=status)


@app.post("/webhook/vexa")
@app.post("/webhook/vexa/")
async def vexa_webhook_endpoint(request: Request) -> JSONResponse:
    """Vexa meeting bot webhooks (recording.completed, meeting.status_change)."""
    return await _vexa_webhook_dispatch(request)


def _apple_reminders_page_html(
    *,
    page_title: str,
    heading: str,
    subtitle: str,
    body_html: str,
    tone: str = "default",
) -> str:
    """Единый современный layout для экранов Apple Reminders (CalDAV). tone: default | error | success."""
    accent = "#0a84ff"
    if tone == "error":
        accent = "#ff453a"
    elif tone == "success":
        accent = "#30d158"
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="color-scheme" content="dark light"/>
  <title>{html_lib.escape(page_title)}</title>
  <style>
    :root {{
      --bg0: #0c0e12;
      --bg1: #12151c;
      --card: rgba(22, 26, 36, 0.92);
      --card-border: rgba(255, 255, 255, 0.08);
      --text: #f2f4f8;
      --muted: #9aa3b2;
      --input-bg: rgba(0, 0, 0, 0.35);
      --input-border: rgba(255, 255, 255, 0.12);
      --accent: {accent};
      --accent-dim: color-mix(in srgb, var(--accent) 35%, transparent);
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
      --radius: 16px;
      --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg0: #f0f2f7;
        --bg1: #e4e8f0;
        --card: rgba(255, 255, 255, 0.92);
        --card-border: rgba(15, 20, 30, 0.08);
        --text: #0f1419;
        --muted: #5c6675;
        --input-bg: #fff;
        --input-border: rgba(15, 20, 30, 0.12);
        --shadow: 0 20px 60px rgba(15, 20, 30, 0.12);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: var(--font);
      color: var(--text);
      background:
        radial-gradient(1200px 600px at 80% -10%, var(--accent-dim), transparent 55%),
        radial-gradient(900px 500px at -10% 110%, rgba(88, 86, 214, 0.18), transparent 50%),
        linear-gradient(165deg, var(--bg0), var(--bg1));
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px 16px 40px;
    }}
    .wrap {{ width: 100%; max-width: 440px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      padding: 28px 28px 26px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 22px;
    }}
    .icon {{
      width: 48px;
      height: 48px;
      border-radius: 14px;
      background: linear-gradient(145deg, var(--accent), color-mix(in srgb, var(--accent) 55%, #5856d6));
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      flex-shrink: 0;
      box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 40%, transparent);
    }}
    .icon svg {{ display: block; }}
    h1 {{
      font-size: 1.35rem;
      font-weight: 650;
      letter-spacing: -0.02em;
      line-height: 1.25;
      margin: 0 0 6px;
    }}
    .subtitle {{
      margin: 0;
      font-size: 0.9rem;
      color: var(--muted);
      line-height: 1.45;
    }}
    .callout {{
      margin: 18px 0 22px;
      padding: 12px 14px;
      border-radius: 12px;
      font-size: 0.8125rem;
      line-height: 1.5;
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
      color: var(--text);
    }}
    .callout a {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}
    .callout a:hover {{ text-decoration: underline; }}
    form {{ margin: 0; }}
    .field {{ margin-bottom: 16px; }}
    label {{
      display: block;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    input[type="email"],
    input[type="password"],
    input[type="url"] {{
      width: 100%;
      padding: 12px 14px;
      font-size: 1rem;
      font-family: inherit;
      color: var(--text);
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      border-radius: 10px;
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    input::placeholder {{ color: color-mix(in srgb, var(--muted) 70%, transparent); }}
    input:focus {{
      border-color: color-mix(in srgb, var(--accent) 65%, var(--input-border));
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent);
    }}
    .btn-row {{ margin-top: 22px; }}
    button[type="submit"] {{
      width: 100%;
      padding: 14px 18px;
      font-size: 0.95rem;
      font-weight: 600;
      font-family: inherit;
      color: #fff;
      border: none;
      border-radius: 12px;
      cursor: pointer;
      background: linear-gradient(180deg,
        color-mix(in srgb, var(--accent) 108%, #fff 0%),
        var(--accent));
      box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 35%, transparent);
      transition: transform 0.12s, filter 0.12s;
    }}
    button[type="submit"]:hover {{ filter: brightness(1.06); }}
    button[type="submit"]:active {{ transform: scale(0.98); }}
    .footer {{
      margin-top: 20px;
      text-align: center;
      font-size: 0.75rem;
      color: var(--muted);
      line-height: 1.4;
    }}
    .prose p {{
      margin: 0 0 12px;
      font-size: 0.95rem;
      line-height: 1.55;
      color: var(--text);
    }}
    .prose p:last-child {{ margin-bottom: 0; }}
    .prose code {{
      font-size: 0.88em;
      padding: 2px 6px;
      border-radius: 6px;
      background: color-mix(in srgb, var(--muted) 18%, transparent);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    .badge-dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 10px var(--accent);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="brand">
        <div class="icon" aria-hidden="true">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M8 2v3M16 2v3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
            <path d="M4.5 8.5h15a1.5 1.5 0 011.5 1.5v10a2 2 0 01-2 2h-14a2 2 0 01-2-2v-10a1.5 1.5 0 011.5-1.5z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
            <path d="M4 11h16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
            <circle cx="12" cy="16" r="1.4" fill="currentColor"/>
          </svg>
        </div>
        <div>
          <div class="badge"><span class="badge-dot"></span> CalDAV</div>
          <h1>{html_lib.escape(heading)}</h1>
          <p class="subtitle">{html_lib.escape(subtitle)}</p>
        </div>
      </div>
      {body_html}
      <p class="footer">Секреты не логируются. Закройте вкладку после завершения.</p>
    </div>
  </div>
</body>
</html>"""


@app.get("/oauth/apple-reminders/setup")
@app.get("/oauth/apple-reminders/setup/", include_in_schema=False)
async def apple_reminders_setup_get(
    request: Request,
    state: Optional[str] = None,
) -> HTMLResponse:
    """Форма привязки iCloud CalDAV (пароль приложения Apple ID). Не OAuth."""
    del request
    if not state or not str(state).strip():
        body = (
            "<div class=\"prose\"><p>Не указан параметр <code>state</code>. "
            "Откройте ссылку из Telegram: команда <code>/apple_reminders_auth</code>.</p></div>"
        )
        return HTMLResponse(
            _apple_reminders_page_html(
                page_title="Ошибка",
                heading="Ссылка неполная",
                subtitle="Запросите новую ссылку в боте.",
                body_html=body,
                tone="error",
            ),
            status_code=400,
        )
    try:
        from assistant.compat.apple_reminders_caldav import peek_setup_telegram_user_id

        uid = peek_setup_telegram_user_id(str(state).strip())
    except Exception as e:
        body = f"<div class=\"prose\"><p>{html_lib.escape(str(e))}</p></div>"
        return HTMLResponse(
            _apple_reminders_page_html(
                page_title="Ошибка",
                heading="Не удалось проверить сессию",
                subtitle="Попробуйте позже или запросите новую ссылку в боте.",
                body_html=body,
                tone="error",
            ),
            status_code=500,
        )
    if uid is None:
        body = (
            "<div class=\"prose\"><p>Сессия привязки недействительна или истекла. "
            "В Telegram выполните <code>/apple_reminders_auth</code> ещё раз.</p></div>"
        )
        return HTMLResponse(
            _apple_reminders_page_html(
                page_title="Сессия истекла",
                heading="Нужна новая ссылка",
                subtitle="Одноразовые ссылки действуют ограниченное время.",
                body_html=body,
                tone="error",
            ),
            status_code=400,
        )
    st_esc = html_lib.escape(str(state).strip(), quote=True)
    form_body = (
        "<div class=\"callout\">Введите <strong>пароль приложения</strong> Apple ID "
        "(не обычный пароль). Создать: "
        "<a href=\"https://appleid.apple.com/\" target=\"_blank\" rel=\"noopener noreferrer\">"
        "appleid.apple.com</a> → раздел паролей приложений.</div>"
        "<form method=\"post\" action=\"/oauth/apple-reminders/setup\" autocomplete=\"on\">"
        f'<input type="hidden" name="state" value="{st_esc}"/>'
        "<div class=\"field\"><label for=\"apple_id\">Apple ID</label>"
        "<input id=\"apple_id\" type=\"email\" name=\"apple_id\" required "
        'autocomplete="username" placeholder="name@icloud.com"/></div>'
        "<div class=\"field\"><label for=\"app_password\">Пароль приложения</label>"
        "<input id=\"app_password\" type=\"password\" name=\"app_password\" required "
        'autocomplete="current-password" placeholder="xxxx-xxxx-xxxx-xxxx"/></div>'
        "<div class=\"field\"><label for=\"caldav_url\">CalDAV URL <span style=\"font-weight:400;text-transform:none;letter-spacing:0;color:var(--muted)\">— необязательно</span></label>"
        "<input id=\"caldav_url\" type=\"url\" name=\"caldav_url\" "
        'placeholder="https://caldav.icloud.com/"/></div>'
        "<div class=\"field\"><label for=\"calendar_url\">URL списка напоминаний <span style=\"font-weight:400;text-transform:none;letter-spacing:0;color:var(--muted)\">— необязательно</span></label>"
        "<input id=\"calendar_url\" type=\"url\" name=\"calendar_url\" "
        'placeholder="если автоматический выбор списка не сработал"/></div>'
        '<div class="btn-row"><button type="submit">Сохранить и проверить</button></div>'
        "</form>"
    )
    return HTMLResponse(
        _apple_reminders_page_html(
            page_title="Apple Напоминания",
            heading="Привязка CalDAV",
            subtitle="Данные используются только для создания напоминаний в вашем iCloud.",
            body_html=form_body,
            tone="default",
        )
    )


@app.post("/oauth/apple-reminders/setup")
@app.post("/oauth/apple-reminders/setup/", include_in_schema=False)
async def apple_reminders_setup_post(
    request: Request,
    state: str = Form(...),
    apple_id: str = Form(...),
    app_password: str = Form(...),
    caldav_url: Optional[str] = Form(None),
    calendar_url: Optional[str] = Form(None),
) -> HTMLResponse:
    del request
    from assistant.compat.apple_reminders_caldav import apply_setup_form

    cu = (calendar_url or "").strip() or None
    du = (caldav_url or "").strip() or None
    try:
        apply_setup_form(
            str(state).strip(),
            apple_id=str(apple_id).strip(),
            app_password=str(app_password).strip(),
            caldav_url=du,
            calendar_url=cu,
        )
    except ValueError as e:
        body = f"<div class=\"prose\"><p>{html_lib.escape(str(e))}</p></div>"
        return HTMLResponse(
            _apple_reminders_page_html(
                page_title="Ошибка",
                heading="Не удалось сохранить",
                subtitle="Проверьте введённые данные и попробуйте снова.",
                body_html=body,
                tone="error",
            ),
            status_code=400,
        )
    except Exception as e:
        body = (
            f"<div class=\"prose\"><p>{html_lib.escape(str(e))}</p>"
            "<p>Проверьте Apple ID, пароль приложения и при необходимости URL списка. "
            "Запросите новую ссылку в боте: <code>/apple_reminders_auth</code>.</p></div>"
        )
        return HTMLResponse(
            _apple_reminders_page_html(
                page_title="Ошибка подключения",
                heading="CalDAV не ответил",
                subtitle="Сервер или учётные данные не подошли.",
                body_html=body,
                tone="error",
            ),
            status_code=500,
        )
    body_ok = (
        "<div class=\"prose\"><p>Соединение с iCloud проверено, данные сохранены.</p>"
        "<p>В Telegram: <code>/set_reminders</code> → выберите <strong>Apple (CalDAV)</strong>, "
        "если ещё не выбрали.</p></div>"
    )
    return HTMLResponse(
        _apple_reminders_page_html(
            page_title="Готово",
            heading="Привязка выполнена",
            subtitle="Можно закрыть эту вкладку.",
            body_html=body_ok,
            tone="success",
        )
    )


@app.get("/favicon.ico")
async def favicon() -> Response:
    """Браузер всегда запрашивает иконку; без маршрута сыпятся 404 в лог."""
    return Response(status_code=204)


def _usd_to_rub() -> float:
    try:
        return float(os.getenv("USD_TO_RUB", "92").strip() or "92")
    except ValueError:
        return 92.0


def _host_header_hostname(request: Request) -> str:
    h = (request.headers.get("host") or "").strip()
    if not h:
        return ""
    return h.rsplit(":", 1)[0].strip().lower()


def _is_loopback_addr(addr: str) -> bool:
    addr = (addr or "").strip()
    if not addr:
        return False
    if addr in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _is_local_dashboard_request(request: Request) -> bool:
    """Локальный доступ без токена: loopback у клиента ИЛИ Host 127.0.0.1/localhost (прокси/Simple Browser)."""
    if os.getenv("USAGE_DASHBOARD_FORCE_AUTH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    ):
        return False
    client = request.client
    if client is not None:
        ch = (client.host or "").strip()
        if ch and _is_loopback_addr(ch):
            return True
    hh = _host_header_hostname(request)
    if hh and _is_loopback_addr(hh):
        return True
    return False


def _require_token(request: Request) -> None:
    if _is_local_dashboard_request(request):
        return
    expected = os.getenv("USAGE_DASHBOARD_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Задайте USAGE_DASHBOARD_TOKEN в .env (или откройте с 127.0.0.1 без токена)",
        )
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        got = auth[7:].strip()
        if got == expected:
            return
    q = (request.query_params.get("access_token") or "").strip()
    if q == expected:
        return
    raise HTTPException(status_code=401, detail="Нужен Bearer token или access_token")


def _access_qs(request: Request) -> str:
    t = (request.query_params.get("access_token") or "").strip()
    return f"?access_token={quote(t)}" if t else ""


@dash.get("/board")
@dash.get("/board/")
async def board_dashboard_index(request: Request) -> HTMLResponse:
    _require_token(request)
    from assistant.board.dashboard import render_index

    return _dash_html(
        render_index(board_base=_dash_url("/board"), qs=_access_qs(request))
    )


@dash.get("/board/{decision_id}")
async def board_dashboard_decision(decision_id: str, request: Request) -> HTMLResponse:
    _require_token(request)
    from assistant.board.dashboard import render_decision

    html_page = render_decision(
        decision_id, board_base=_dash_url("/board"), qs=_access_qs(request)
    )
    if not html_page:
        raise HTTPException(status_code=404, detail="Решение не найдено")
    return _dash_html(html_page)


@dash.get("/api/summary")
async def api_summary(request: Request) -> JSONResponse:
    _require_token(request)
    rate = _usd_to_rub()
    rows = daily_summary()
    out = []
    for r in rows:
        usd = float(r.get("cost_usd") or 0)
        out.append(
            {
                **r,
                "cost_rub": round(usd * rate, 4),
                "usd_to_rub": rate,
            }
        )
    return JSONResponse({"days": out, "usd_to_rub": rate}, headers=dict(_DASH_NO_CACHE_HEADERS))


@dash.get("/api/day/{date}")
async def api_day(date: str, request: Request) -> JSONResponse:
    _require_token(request)
    rate = _usd_to_rub()
    evs = events_for_date(date)
    for e in evs:
        usd = float(e.get("cost_usd") or 0)
        e["cost_rub"] = round(usd * rate, 4)
    return JSONResponse(
        {"date_utc": date, "events": evs, "usd_to_rub": rate},
        headers=dict(_DASH_NO_CACHE_HEADERS),
    )


@dash.get("/api/reconcile/{date}")
async def api_reconcile(date: str, request: Request) -> JSONResponse:
    """Сверка суммы за день с OpenRouter /api/v1/activity (нужен Management key)."""
    _require_token(request)
    mgmt = os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip()
    if not mgmt:
        raise HTTPException(
            status_code=400,
            detail="Задайте OPENROUTER_MANAGEMENT_KEY для сверки",
        )
    url = "https://openrouter.ai/api/v1/activity"
    try:
        r = requests.get(
            url,
            params={"date": date},
            headers={"Authorization": f"Bearer {mgmt}"},
            timeout=60,
        )
        r.raise_for_status()
        payload = r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter activity: {e}") from e

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        data = []

    or_usd = sum(float(row.get("usage") or 0) for row in data if isinstance(row, dict))
    or_prompt = sum(int(row.get("prompt_tokens") or 0) for row in data if isinstance(row, dict))
    or_compl = sum(
        int(row.get("completion_tokens") or 0) for row in data if isinstance(row, dict)
    )
    or_reqs = sum(int(row.get("requests") or 0) for row in data if isinstance(row, dict))

    local_rows = events_for_date(date)
    loc_usd = sum(float(x.get("cost_usd") or 0) for x in local_rows)
    loc_prompt = sum(int(x.get("prompt_tokens") or 0) for x in local_rows)
    loc_compl = sum(int(x.get("completion_tokens") or 0) for x in local_rows)

    return JSONResponse(
        {
            "date_utc": date,
            "openrouter": {
                "usd_total": or_usd,
                "prompt_tokens": or_prompt,
                "completion_tokens": or_compl,
                "requests": or_reqs,
                "rows": len(data),
            },
            "local_log": {
                "usd_total": loc_usd,
                "prompt_tokens": loc_prompt,
                "completion_tokens": loc_compl,
                "calls": len(local_rows),
            },
            "delta_usd": round(or_usd - loc_usd, 6),
            "note": "Activity API агрегирует по endpoint/model; локальный журнал — по каждому вызову из бота.",
        },
        headers=dict(_DASH_NO_CACHE_HEADERS),
    )


def _html_page_start(title: str) -> str:
    t = html_lib.escape(title)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #09090b;
  --bg-elevated: #0c0c0f;
  --surface: #141417;
  --surface-hover: #1c1c21;
  --border: #2a2a32;
  --text: #f4f4f5;
  --muted: #a1a1aa;
  --muted2: #71717a;
  --accent: #22d3ee;
  --accent-soft: rgba(34, 211, 238, 0.12);
  --radius: 14px;
  --radius-sm: 8px;
  --font: "DM Sans", system-ui, -apple-system, sans-serif;
  --shadow: 0 24px 48px -12px rgba(0, 0, 0, 0.45);
}}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{
  margin: 0;
  min-height: 100vh;
  font-family: var(--font);
  font-size: 15px;
  line-height: 1.5;
  color: var(--text);
  background: var(--bg);
  background-image:
    radial-gradient(ellipse 120% 80% at 50% -20%, rgba(34, 211, 238, 0.08), transparent),
    radial-gradient(ellipse 80% 50% at 100% 50%, rgba(139, 92, 246, 0.05), transparent);
}}
.app {{
  max-width: 1120px;
  margin: 0 auto;
  padding: clamp(1.25rem, 4vw, 2.5rem);
}}
.header {{
  margin-bottom: 2rem;
}}
.header h1 {{
  margin: 0 0 0.35rem;
  font-size: clamp(1.5rem, 4vw, 1.85rem);
  font-weight: 700;
  letter-spacing: -0.02em;
}}
.header .subtitle {{
  margin: 0;
  color: var(--muted);
  font-size: 0.95rem;
}}
.header .pill {{
  display: inline-block;
  margin-top: 0.75rem;
  padding: 0.25rem 0.65rem;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 999px;
}}
.metrics {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}}
.metric {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.1rem 1.25rem;
  box-shadow: var(--shadow);
}}
.metric .label {{
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--muted2);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.35rem;
}}
.metric .value {{
  font-size: 1.35rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}}
.metric .value small {{
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--muted);
}}
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow);
}}
.table-wrap {{
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}}
thead th {{
  text-align: left;
  font-weight: 600;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted2);
  background: var(--bg-elevated);
  padding: 0.85rem 1rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}}
tbody td {{
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover td {{ background: var(--surface-hover); }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
a {{
  color: var(--accent);
  text-decoration: none;
  font-weight: 500;
}}
a:hover {{ text-decoration: underline; }}
.back {{
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 1.25rem;
  padding: 0.45rem 0.85rem;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  transition: background 0.15s, border-color 0.15s;
}}
.back:hover {{
  background: var(--surface-hover);
  border-color: var(--muted2);
  text-decoration: none;
}}
code {{
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: 0.78rem;
  color: #e879f9;
  background: var(--bg-elevated);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  word-break: break-all;
}}
.empty {{
  padding: 2.5rem 1.5rem;
  text-align: center;
  color: var(--muted);
}}
.footer {{
  margin-top: 2rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--border);
  font-size: 0.8rem;
  color: var(--muted2);
}}
.footer code {{ font-size: 0.75rem; }}
.op-badge {{
  display: inline-block;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}}
</style>
</head>
<body>
<div class="app">
"""


def _html_page_end(footer_html: str = "") -> str:
    foot = footer_html or (
        "<p>API: <code>"
        + html_lib.escape(_dash_url("/api/summary"))
        + "</code>, <code>"
        + html_lib.escape(_dash_url("/api/day/{date}"))
        + "</code>, <code>"
        + html_lib.escape(_dash_url("/api/reconcile/{date}"))
        + "</code> — заголовок <code>Authorization: Bearer …</code>.</p>"
    )
    meta = _dash_journal_meta_html()
    return f"""
<footer class="footer">{foot}{meta}</footer>
</div>
</body>
</html>
"""


@dash.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    _require_token(request)
    rate = _usd_to_rub()
    rows = daily_summary()
    qs = _access_qs(request)
    total_usd = sum(float(r.get("cost_usd") or 0) for r in rows)
    total_calls = sum(int(r.get("n_calls") or 0) for r in rows)
    total_tok = sum(int(r.get("total_tokens") or 0) for r in rows)
    total_rub = total_usd * rate
    total_tok_fmt = f"{total_tok:,}".replace(",", " ")

    parts: list[str] = [
        _html_page_start("OpenRouter — расход"),
        '<header class="header">',
        "<h1>Расход OpenRouter</h1>",
        '<p class="subtitle">Агрегаты по календарным дням в UTC. Стоимость в рублях: '
        f"<strong>{html_lib.escape(str(rate))}</strong> ₽ за 1 USD (<code>USD_TO_RUB</code>).</p>",
        '<span class="pill">Локальный журнал бота</span>',
        f'<p style="margin:0.75rem 0 0;"><a href="{_dash_url("/users")}{qs}" style="color:var(--accent);text-decoration:none;font-weight:600;">Пользователи</a></p>',
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{total_tok_fmt}</div></div>',
        f'<div class="metric"><div class="label">Сумма USD</div><div class="value">{total_usd:.4f}</div></div>',
        f'<div class="metric"><div class="label">Сумма ₽</div><div class="value">{total_rub:.2f} <small>≈</small></div></div>',
        "</section>",
        '<section class="card">',
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Дата (UTC)</th>",
        '<th class="num">Вызовов</th>',
        '<th class="num">Prompt</th>',
        '<th class="num">Completion</th>',
        '<th class="num">Всего</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]
    for r in rows:
        d = html_lib.escape(str(r.get("date_utc") or ""))
        usd = float(r.get("cost_usd") or 0)
        rub = usd * rate
        parts.append(
            "<tr>"
            f'<td><a href="{_dash_url("/day/" + d)}{qs}">{d}</a></td>'
            f'<td class="num">{int(r.get("n_calls") or 0)}</td>'
            f'<td class="num">{int(r.get("prompt_tokens") or 0):,}</td>'.replace(",", " ")
            + f'<td class="num">{int(r.get("completion_tokens") or 0):,}</td>'.replace(
                ",", " "
            )
            + f'<td class="num">{int(r.get("total_tokens") or 0):,}</td>'.replace(",", " ")
            + f'<td class="num">{usd:.6f}</td>'
            f'<td class="num">{rub:.2f}</td>'
            "</tr>"
        )
    if not rows:
        parts.append(
            '</tbody></table></div><div class="empty">Пока нет записей — сделайте вызовы GPT из бота.</div></section>'
        )
    else:
        parts.append("</tbody></table></div></section>")
    parts.append(_html_page_end())
    return _dash_html("".join(parts))


@dash.get("/day/{date}", response_class=HTMLResponse)
async def day_users_page(date: str, request: Request) -> HTMLResponse:
    """Пользователи с расходом за один UTC-день (клик с главной «сводка по дням»)."""
    _require_token(request)
    rate = _usd_to_rub()
    access_token = (request.query_params.get("access_token") or "").strip()
    try:
        day_d = _parse_utc_date(date)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Неверный формат date. Ожидается YYYY-MM-DD.",
        )

    filter_op = _dash_filter_operation(request)
    totals = period_totals(day_d, day_d, operation=filter_op)
    total_usd = float(totals.get("cost_usd") or 0)
    total_rub = total_usd * rate
    total_tok = int(totals.get("total_tokens") or 0)
    total_tok_fmt = f"{total_tok:,}".replace(",", " ")
    total_calls = int(totals.get("n_calls") or 0)

    users = users_summary(day_d, day_d, operation=filter_op)
    ops_summary = summary_by_operation(day_d, day_d)
    ops_in_period = distinct_operations(day_d, day_d)

    qs = _access_qs(request)
    day_qs = _dash_range_qs(
        day_d, day_d, access_token=access_token, operation=filter_op
    )
    day_esc = html_lib.escape(day_d)

    acc_hidden = (
        f'<input type="hidden" name="access_token" value="{_html_escape(access_token)}"/>'
        if access_token
        else ""
    )

    filter_note = ""
    if filter_op:
        filter_note = (
            f' <span class="pill">Фильтр: {_html_escape(operation_label_ru(filter_op))}</span>'
        )

    op_options = ['<option value="">Все операции</option>']
    for op_code in ops_in_period:
        sel = ' selected="selected"' if op_code == filter_op else ""
        op_options.append(
            f'<option value="{_html_escape(op_code)}"{sel}>'
            f"{_html_escape(operation_label_ru(op_code))}</option>"
        )

    ops_link = f"{_dash_url('/day/' + quote(day_d) + '/operations')}?{day_qs}"

    parts: list[str] = [
        _html_page_start(f"Пользователи — {day_d}"),
        f'<a class="back" href="{_dash_url("/")}{qs}">← Сводка по дням</a>',
        '<header class="header">',
        f"<h1>{day_esc}</h1>",
        '<p class="subtitle">Расход по пользователям за один день (UTC). '
        f'<a href="{ops_link}" style="color:var(--accent);">Все операции за день</a></p>',
        f'<span class="pill">За день: {total_rub:.2f} ₽</span>',
        filter_note,
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{total_tok_fmt}</div></div>',
        f'<div class="metric"><div class="label">Сумма USD</div><div class="value">{total_usd:.4f}</div></div>',
        f'<div class="metric"><div class="label">Сумма ₽</div><div class="value">{total_rub:.2f} <small>≈</small></div></div>',
        "</section>",
        '<section class="card">',
        '<form method="get">',
        acc_hidden,
        '<div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end;">',
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">Операция</div>',
        f'<select name="operation" style="min-width:12rem;">{"".join(op_options)}</select>',
        "</div>",
        '<button type="submit" style="cursor:pointer;background:var(--accent);border:none;color:#061218;padding:0.55rem 0.85rem;border-radius:10px;font-weight:700;">Применить</button>',
        "</div>",
        "</form>",
        "</section>",
        _dash_ops_summary_table(ops_summary, rate),
        '<section class="card" style="margin-top:1rem;">',
        "<h2 style=\"margin:0 0 0.75rem;font-size:1.05rem;\">По пользователям</h2>",
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Пользователь</th>",
        '<th class="num">Вызовов</th>',
        '<th class="num">Токенов</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]

    if not users:
        parts.append(
            '</tbody></table></div><div class="empty">Нет данных за этот день.</div></section>'
        )
        parts.append(_html_page_end(""))
        return _dash_html("".join(parts))

    user_day_qs = f"from={quote(day_d)}&to={quote(day_d)}"
    if filter_op:
        user_day_qs += f"&operation={quote(filter_op)}"
    if access_token:
        user_day_qs += f"&access_token={quote(access_token)}"

    for u in users:
        uid = str(u.get("telegram_user_id") or "").strip()
        uname = str(u.get("telegram_username") or "").strip()
        label = _user_label(uid, uname)
        cost_usd = float(u.get("cost_usd") or 0)
        cost_rub = cost_usd * rate
        tokens = int(u.get("total_tokens") or 0)
        tokens_fmt = f"{tokens:,}".replace(",", " ")
        user_link = (
            f"{_dash_url('/user/' + quote(uid) + '/day/' + quote(day_d))}?{user_day_qs}"
        )
        parts.append(
            "<tr>"
            f'<td><a href="{user_link}">{_html_escape(label)}</a></td>'
            f'<td class="num">{int(u.get("n_calls") or 0)}</td>'
            f'<td class="num">{tokens_fmt}</td>'
            f'<td class="num">{cost_usd:.6f}</td>'
            f'<td class="num">{cost_rub:.2f}</td>'
            "</tr>"
        )

    parts.append("</tbody></table></div></section>")
    parts.append(_html_page_end(""))
    return _dash_html("".join(parts))


@dash.get("/day/{date}/operations", response_class=HTMLResponse)
async def day_operations_page(date: str, request: Request) -> HTMLResponse:
    _require_token(request)
    rate = _usd_to_rub()
    try:
        day_d = _parse_utc_date(date)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Неверный формат date. Ожидается YYYY-MM-DD.",
        )
    evs = events_for_date(day_d)
    qs = _access_qs(request)
    day_esc = html_lib.escape(day_d)
    day_usd = sum(float(e.get("cost_usd") or 0) for e in evs)
    day_rub = day_usd * rate

    parts: list[str] = [
        _html_page_start(f"Операции — {day_d}"),
        f'<a class="back" href="{_dash_url("/day/" + quote(day_d))}{qs}">← Пользователи за день</a>',
        '<header class="header">',
        f"<h1>{day_esc}</h1>",
        '<p class="subtitle">Каждая строка — один вызов OpenRouter из бота (UTC).</p>',
        f'<span class="pill">За день: {day_usd:.4f} USD · {day_rub:.2f} ₽</span>',
        "</header>",
        '<section class="card">',
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Время (UTC)</th>",
        "<th>Операция</th>",
        "<th>Модель</th>",
        "<th>Generation id</th>",
        '<th class="num">Prompt</th>',
        '<th class="num">Compl</th>',
        '<th class="num">Всего</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]
    for e in evs:
        usd = float(e.get("cost_usd") or 0)
        rub = usd * rate
        ts = html_lib.escape(str(e.get("ts_utc") or ""))
        op_raw = str(e.get("operation") or "")
        md = html_lib.escape(str(e.get("model") or ""))
        gid = html_lib.escape(str(e.get("generation_id") or ""))
        parts.append(
            "<tr>"
            f"<td>{ts}</td>"
            f"<td>{_dash_op_cell(op_raw)}</td>"
            f'<td><span class="op-badge" title="{md}">{md}</span></td>'
            f"<td><code>{gid}</code></td>"
            f'<td class="num">{e.get("prompt_tokens")}</td>'
            f'<td class="num">{e.get("completion_tokens")}</td>'
            f'<td class="num">{e.get("total_tokens")}</td>'
            f'<td class="num">{usd:.6f}</td>'
            f'<td class="num">{rub:.2f}</td>'
            "</tr>"
        )
    if not evs:
        parts.append(
            '</tbody></table></div><div class="empty">Нет событий за этот день.</div></section>'
        )
    else:
        parts.append("</tbody></table></div></section>")
    parts.append(_html_page_end(""))
    return _dash_html("".join(parts))


def _parse_utc_date(date_str: str) -> str:
    s = (date_str or "").strip()
    if not s:
        raise ValueError("empty date")
    # Ждём формат YYYY-MM-DD.
    dt = datetime.fromisoformat(s)
    return dt.date().isoformat()


def _html_escape(s: str) -> str:
    return html_lib.escape(s or "")


UNKNOWN_USER_KEY = "__unknown__"


def _user_label(uid: str, username: str) -> str:
    """Отображаемая метка пользователя для UI."""
    u = (uid or "").strip()
    un = (username or "").strip()
    if u == UNKNOWN_USER_KEY:
        return "unknown"
    return un if un else f"ID {u}"


def _dash_filter_operation(request: Request) -> str | None:
    op = (request.query_params.get("operation") or "").strip()
    return op or None


def _dash_range_qs(
    from_d: str,
    to_d: str,
    *,
    access_token: str = "",
    operation: str | None = None,
) -> str:
    parts = [f"from={quote(from_d)}", f"to={quote(to_d)}"]
    if operation:
        parts.append(f"operation={quote(operation)}")
    if access_token:
        parts.append(f"access_token={quote(access_token)}")
    return "&".join(parts)


def _dash_op_cell(op_raw: str) -> str:
    op_code = str(op_raw or "").strip()
    label = _html_escape(operation_label_ru(op_code))
    code = _html_escape(op_code)
    return f'<span class="op-badge" title="{code}">{label}</span>'


def _dash_ops_summary_table(rows: list[dict[str, Any]], rate: float) -> str:
    """HTML-таблица сводки по операциям."""
    if not rows:
        return ""
    lines = [
        '<section class="card" style="margin-top:1rem;">',
        "<h2 style=\"margin:0 0 0.75rem;font-size:1.05rem;\">По типам операций</h2>",
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Операция</th>",
        '<th class="num">Вызовов</th>',
        '<th class="num">Токенов</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]
    for r in rows:
        op = str(r.get("operation") or "").strip()
        usd = float(r.get("cost_usd") or 0)
        rub = usd * rate
        tok = int(r.get("total_tokens") or 0)
        tok_fmt = f"{tok:,}".replace(",", " ")
        lines.append(
            "<tr>"
            f"<td>{_dash_op_cell(op)}</td>"
            f'<td class="num">{int(r.get("n_calls") or 0)}</td>'
            f'<td class="num">{tok_fmt}</td>'
            f'<td class="num">{usd:.6f}</td>'
            f'<td class="num">{rub:.2f}</td>'
            "</tr>"
        )
    lines.append("</tbody></table></div></section>")
    return "".join(lines)


@dash.get("/users", response_class=HTMLResponse)
async def users_page(request: Request) -> HTMLResponse:
    _require_token(request)
    rate = _usd_to_rub()
    access_token = (request.query_params.get("access_token") or "").strip()

    today = datetime.now(timezone.utc).date()
    to_raw = request.query_params.get("to") or today.isoformat()
    from_raw = request.query_params.get("from") or (
        today - timedelta(days=_DASH_DEFAULT_USER_RANGE_DAYS)
    ).isoformat()
    try:
        to_d = _parse_utc_date(to_raw)
        from_d = _parse_utc_date(from_raw)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Неверный формат from/to. Ожидается YYYY-MM-DD.",
        )

    if from_d > to_d:
        from_d, to_d = to_d, from_d

    filter_op = _dash_filter_operation(request)
    totals = period_totals(from_d, to_d, operation=filter_op)
    total_usd = float(totals.get("cost_usd") or 0)
    total_rub = total_usd * rate
    total_tok = int(totals.get("total_tokens") or 0)
    total_tok_fmt = f"{total_tok:,}".replace(",", " ")
    total_calls = int(totals.get("n_calls") or 0)

    users = users_summary(from_d, to_d, operation=filter_op)
    ops_in_period = distinct_operations(from_d, to_d)
    ops_summary = summary_by_operation(from_d, to_d)

    acc_hidden = (
        f'<input type="hidden" name="access_token" value="{_html_escape(access_token)}"/>'
        if access_token
        else ""
    )
    range_q = _dash_range_qs(
        from_d, to_d, access_token=access_token, operation=filter_op
    )
    range_link_q = "?" + range_q

    filter_note = ""
    if filter_op:
        filter_note = (
            f' <span class="pill">Фильтр: {_html_escape(operation_label_ru(filter_op))}</span>'
        )

    op_options = ['<option value="">Все операции</option>']
    for op_code in ops_in_period:
        sel = ' selected="selected"' if op_code == filter_op else ""
        op_options.append(
            f'<option value="{_html_escape(op_code)}"{sel}>'
            f"{_html_escape(operation_label_ru(op_code))}</option>"
        )

    parts: list[str] = [
        _html_page_start("Пользователи — расход OpenRouter"),
        '<header class="header">',
        "<h1>Расход OpenRouter по пользователям</h1>",
        '<p class="subtitle">Период в UTC (по умолчанию последние '
        f"{_DASH_DEFAULT_USER_RANGE_DAYS + 1} дней). В таблице только те, кто дал хотя бы один вызов "
        "GPT/транскрипции (задачи в Битрикс без ИИ сюда не попадают). Расход в ₽ через "
        "<code>USD_TO_RUB</code>.</p>",
        '<span class="pill">Сводка</span>',
        filter_note,
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{total_tok_fmt}</div></div>',
        f'<div class="metric"><div class="label">Сумма USD</div><div class="value">{total_usd:.4f}</div></div>',
        f'<div class="metric"><div class="label">Сумма ₽</div><div class="value">{total_rub:.2f} <small>≈</small></div></div>',
        "</section>",
        '<section class="card">',
        '<form method="get">',
        acc_hidden,
        '<div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end;">',
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">С даты</div>',
        f'<input type="date" name="from" value="{_html_escape(from_d)}"/>',
        "</div>",
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">По дату</div>',
        f'<input type="date" name="to" value="{_html_escape(to_d)}"/>',
        "</div>",
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">Операция</div>',
        f'<select name="operation" style="min-width:12rem;">{"".join(op_options)}</select>',
        "</div>",
        '<button type="submit" style="cursor:pointer;background:var(--accent);border:none;color:#061218;padding:0.55rem 0.85rem;border-radius:10px;font-weight:700;">Применить</button>',
        "</div>",
        "</form>",
        "</section>",
        _dash_ops_summary_table(ops_summary, rate),
        '<section class="card" style="margin-top:1rem;">',
        "<h2 style=\"margin:0 0 0.75rem;font-size:1.05rem;\">По пользователям</h2>",
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Пользователь</th>",
        '<th class="num">Всего вызовов</th>',
        '<th class="num">Токенов всего</th>',
        '<th class="num">Сумма USD</th>',
        '<th class="num">Сумма ₽</th>',
        "</tr></thead><tbody>",
    ]

    if not users:
        parts.append(
            '</tbody></table></div><div class="empty">Нет данных за выбранный период.</div></section>'
        )
        parts.append(_html_page_end(""))
        return _dash_html("".join(parts))

    for u in users:
        uid = str(u.get("telegram_user_id") or "").strip()
        uname = str(u.get("telegram_username") or "").strip()
        label = _user_label(uid, uname)
        cost_usd = float(u.get("cost_usd") or 0)
        cost_rub = cost_usd * rate
        tokens = int(u.get("total_tokens") or 0)
        tokens_fmt = f"{tokens:,}".replace(",", " ")
        parts.append(
            "<tr>"
            f'<td><a href="{_dash_url("/user/" + quote(uid))}{range_link_q}">{_html_escape(label)}</a></td>'
            f'<td class="num">{int(u.get("n_calls") or 0)}</td>'
            f'<td class="num">{tokens_fmt}</td>'
            + f'<td class="num">{cost_usd:.6f}</td>'
            f'<td class="num">{cost_rub:.2f}</td>'
            "</tr>"
        )

    parts.append("</tbody></table></div></section>")
    parts.append(_html_page_end(""))
    return _dash_html("".join(parts))


@dash.get("/user/{telegram_user_id}", response_class=HTMLResponse)
async def user_page(telegram_user_id: str, request: Request) -> HTMLResponse:
    _require_token(request)
    rate = _usd_to_rub()
    access_token = (request.query_params.get("access_token") or "").strip()

    today = datetime.now(timezone.utc).date()
    to_raw = request.query_params.get("to") or today.isoformat()
    from_raw = request.query_params.get("from") or (
        today - timedelta(days=_DASH_DEFAULT_USER_RANGE_DAYS)
    ).isoformat()
    try:
        to_d = _parse_utc_date(to_raw)
        from_d = _parse_utc_date(from_raw)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Неверный формат from/to. Ожидается YYYY-MM-DD.",
        )

    if from_d > to_d:
        from_d, to_d = to_d, from_d

    uid = str(telegram_user_id or "").strip()
    display_label = _user_label(uid, "")
    filter_op = _dash_filter_operation(request)
    days = user_daily_summary(uid, from_d, to_d)
    by_op = user_summary_by_operation(uid, from_d, to_d)

    total_calls = sum(int(d.get("n_calls") or 0) for d in days)
    total_tok = sum(int(d.get("total_tokens") or 0) for d in days)
    total_usd = sum(float(d.get("cost_usd") or 0) for d in days)
    total_rub = total_usd * rate
    total_tok_fmt = f"{total_tok:,}".replace(",", " ")

    acc_hidden = (
        f'<input type="hidden" name="access_token" value="{_html_escape(access_token)}"/>'
        if access_token
        else ""
    )
    qs = _dash_range_qs(
        from_d, to_d, access_token=access_token, operation=filter_op
    )
    back_link = f"{_dash_url('/users')}?{qs}" if qs else _dash_url("/users")
    op_hidden = (
        f'<input type="hidden" name="operation" value="{_html_escape(filter_op)}"/>'
        if filter_op
        else ""
    )

    parts: list[str] = [
        _html_page_start(f"Пользователь {display_label} — расход OpenRouter"),
        f'<a class="back" href="{back_link}">← К пользователям</a>',
        '<header class="header">',
        f"<h1>Клиент { _html_escape(display_label) }</h1>",
        f'<p class="subtitle">Период в UTC: <code>{_html_escape(from_d)}</code>…<code>{_html_escape(to_d)}</code>.</p>',
        f'<span class="pill">Итого: {total_rub:.2f} ₽</span>',
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{total_tok_fmt}</div></div>',
        f'<div class="metric"><div class="label">Сумма USD</div><div class="value">{total_usd:.4f}</div></div>',
        f'<div class="metric"><div class="label">Сумма ₽</div><div class="value">{total_rub:.2f} <small>≈</small></div></div>',
        "</section>",
        '<section class="card">',
        '<form method="get">',
        acc_hidden,
        op_hidden,
        '<div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end;">',
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">С даты</div>',
        f'<input type="date" name="from" value="{_html_escape(from_d)}"/>',
        "</div>",
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">По дату</div>',
        f'<input type="date" name="to" value="{_html_escape(to_d)}"/>',
        "</div>",
        '<button type="submit" style="cursor:pointer;background:var(--accent);border:none;color:#061218;padding:0.55rem 0.85rem;border-radius:10px;font-weight:700;">Применить</button>',
        "</div>",
        "</form>",
        "</section>",
        _dash_ops_summary_table(by_op, rate),
        '<section class="card" style="margin-top:1rem;">',
        "<h2 style=\"margin:0 0 0.75rem;font-size:1.05rem;\">По дням</h2>",
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Дата (UTC)</th>",
        '<th class="num">Вызовов</th>',
        '<th class="num">Prompt</th>',
        '<th class="num">Completion</th>',
        '<th class="num">Всего</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]

    if not days:
        parts.append(
            '</tbody></table></div><div class="empty">Нет событий по дням за выбранный период.</div></section>'
        )
        parts.append(_html_page_end(""))
        return _dash_html("".join(parts))

    day_qs = qs
    for d in days:
        date_utc = str(d.get("date_utc") or "").strip()
        n_calls = int(d.get("n_calls") or 0)
        pt = int(d.get("prompt_tokens") or 0)
        ct = int(d.get("completion_tokens") or 0)
        tt = int(d.get("total_tokens") or 0)
        usd = float(d.get("cost_usd") or 0)
        rub = usd * rate
        pt_fmt = f"{pt:,}".replace(",", " ")
        ct_fmt = f"{ct:,}".replace(",", " ")
        tt_fmt = f"{tt:,}".replace(",", " ")

        day_link = (
            f"{_dash_url('/user/' + quote(uid) + '/day/' + quote(date_utc))}?{day_qs}"
        )

        parts.append(
            "<tr>"
            f'<td><a href="{day_link}">{_html_escape(date_utc)}</a></td>'
            f'<td class="num">{n_calls}</td>'
            f'<td class="num">{pt_fmt}</td>'
            f'<td class="num">{ct_fmt}</td>'
            f'<td class="num">{tt_fmt}</td>'
            f'<td class="num">{usd:.6f}</td>'
            f'<td class="num">{rub:.2f}</td>'
            "</tr>"
        )

    parts.append("</tbody></table></div></section>")
    parts.append(_html_page_end(""))
    return _dash_html("".join(parts))


@dash.get("/user/{telegram_user_id}/day/{date}", response_class=HTMLResponse)
async def user_day_ops_page(
    telegram_user_id: str, date: str, request: Request
) -> HTMLResponse:
    """Страница 3: операции конкретного клиента за один день."""
    _require_token(request)
    rate = _usd_to_rub()
    access_token = (request.query_params.get("access_token") or "").strip()

    today = datetime.now(timezone.utc).date()
    to_raw = request.query_params.get("to") or today.isoformat()
    from_raw = request.query_params.get("from") or (
        today - timedelta(days=_DASH_DEFAULT_USER_RANGE_DAYS)
    ).isoformat()
    try:
        to_d = _parse_utc_date(to_raw)
        from_d = _parse_utc_date(from_raw)
        day_d = _parse_utc_date(date)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Неверный формат from/to/date. Ожидается YYYY-MM-DD.",
        )

    if from_d > to_d:
        from_d, to_d = to_d, from_d

    uid = str(telegram_user_id or "").strip()
    display_label = _user_label(uid, "")
    if not (from_d <= day_d <= to_d):
        # В рамках архитектуры период фильтрует и операции.
        parts: list[str] = [
            _html_page_start("Операции — вне периода"),
            '<a class="back" href="' + _dash_url("/users") + '">← К пользователям</a>',
            '<section class="card">',
            '<div class="empty">Выбранный день выходит за границы периода.</div>',
            "</section>",
            _html_page_end(""),
        ]
        return _dash_html("".join(parts))

    events = events_for_user_date(uid, day_d)
    filter_op = _dash_filter_operation(request)
    total_calls = len(events)
    total_usd = sum(float(e.get("cost_usd") or 0) for e in events)
    total_rub = total_usd * rate
    total_tok = sum(int(e.get("total_tokens") or 0) for e in events)
    total_tok_fmt = f"{total_tok:,}".replace(",", " ")

    qs = _dash_range_qs(
        from_d, to_d, access_token=access_token, operation=filter_op
    )
    back_link = f"{_dash_url('/user/' + quote(uid))}?{qs}"

    acc_hidden = (
        f'<input type="hidden" name="access_token" value="{_html_escape(access_token)}"/>'
        if access_token
        else ""
    )
    op_hidden = (
        f'<input type="hidden" name="operation" value="{_html_escape(filter_op)}"/>'
        if filter_op
        else ""
    )

    parts: list[str] = [
        _html_page_start(f"Операции клиента {display_label} — {day_d}"),
        f'<a class="back" href="{back_link}">← Клиент / дни</a>',
        '<header class="header">',
        f"<h1>Операции</h1>",
        f'<p class="subtitle">Клиент: <code>{_html_escape(display_label)}</code>. День (UTC): <code>{_html_escape(day_d)}</code>.</p>',
        f'<span class="pill">Итого: {total_rub:.2f} ₽</span>',
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{total_tok_fmt}</div></div>',
        f'<div class="metric"><div class="label">Сумма USD</div><div class="value">{total_usd:.4f}</div></div>',
        f'<div class="metric"><div class="label">Сумма ₽</div><div class="value">{total_rub:.2f} <small>≈</small></div></div>',
        "</section>",
        '<section class="card">',
        '<form method="get">',
        acc_hidden,
        op_hidden,
        '<div style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:flex-end;">',
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">С даты</div>',
        f'<input type="date" name="from" value="{_html_escape(from_d)}"/>',
        "</div>",
        '<div>',
        '<div style="font-size:0.8rem;color:var(--muted2);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.35rem;">По дату</div>',
        f'<input type="date" name="to" value="{_html_escape(to_d)}"/>',
        "</div>",
        '<button type="submit" style="cursor:pointer;background:var(--accent);border:none;color:#061218;padding:0.55rem 0.85rem;border-radius:10px;font-weight:700;">Применить</button>',
        "</div>",
        "</form>",
        "</section>",
        '<section class="card" style="margin-top:1rem;">',
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Время (UTC)</th>",
        "<th>Операция</th>",
        "<th>Модель</th>",
        "<th>Generation id</th>",
        '<th class="num">Prompt</th>',
        '<th class="num">Completion</th>',
        '<th class="num">Всего</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]

    if not events:
        parts.append(
            '</tbody></table></div><div class="empty">Нет операций для этого клиента в выбранный день.</div></section>'
        )
        parts.append(_html_page_end(""))
        return _dash_html("".join(parts))

    for e in events:
        usd = float(e.get("cost_usd") or 0)
        rub = usd * rate
        ts = html_lib.escape(str(e.get("ts_utc") or ""))
        op_raw = str(e.get("operation") or "")
        md = html_lib.escape(str(e.get("model") or ""))
        gid = html_lib.escape(str(e.get("generation_id") or ""))
        pt = int(e.get("prompt_tokens") or 0)
        ct = int(e.get("completion_tokens") or 0)
        tt = int(e.get("total_tokens") or 0)
        pt_fmt = f"{pt:,}".replace(",", " ")
        ct_fmt = f"{ct:,}".replace(",", " ")
        tt_fmt = f"{tt:,}".replace(",", " ")
        parts.append(
            "<tr>"
            f"<td>{ts}</td>"
            f"<td>{_dash_op_cell(op_raw)}</td>"
            f'<td><span class="op-badge" title="{md}">{md}</span></td>'
            f"<td><code>{gid}</code></td>"
            f'<td class="num">{pt_fmt}</td>'
            f'<td class="num">{ct_fmt}</td>'
            f'<td class="num">{tt_fmt}</td>'
            f'<td class="num">{usd:.6f}</td>'
            f'<td class="num">{rub:.2f}</td>'
            "</tr>"
        )

    parts.append("</tbody></table></div></section>")
    parts.append(_html_page_end(""))
    return _dash_html("".join(parts))


class _MiniappPrincipal:
    """Результат проверки initData для мини-приложения."""

    __slots__ = ("telegram_user_id", "user")

    def __init__(self, telegram_user_id: int, user: dict) -> None:
        self.telegram_user_id = telegram_user_id
        self.user = user


def _tma_init_data_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "tma":
        return None
    return parts[1] if parts[1] else None


def _browser_session_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2:
        return None
    scheme = parts[0].lower()
    if scheme in ("session", "bearer"):
        return parts[1].strip() if parts[1] else None
    return None


def _browser_session_token_from_request(request: Request) -> str | None:
    from assistant.lib.telegram_login_auth import MINIAPP_SESSION_COOKIE

    raw = _browser_session_from_header(request.headers.get("Authorization"))
    if not raw:
        raw = (request.cookies.get(MINIAPP_SESSION_COOKIE) or "").strip()
    return raw or None


def _principal_from_browser_session_request(request: Request) -> _MiniappPrincipal | None:
    from assistant.lib.telegram_login_auth import verify_browser_session

    raw = _browser_session_token_from_request(request)
    if not raw:
        return None
    parsed = verify_browser_session(raw)
    if parsed is None:
        return None
    uid, user = parsed
    return _MiniappPrincipal(uid, user)


def _attach_miniapp_session_cookie(response: Response, token: str) -> None:
    from assistant.lib.telegram_login_auth import (
        MINIAPP_SESSION_COOKIE,
        session_cookie_max_age,
    )

    response.set_cookie(
        key=MINIAPP_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=session_cookie_max_age(),
        path="/",
    )


def _clear_miniapp_session_cookie(response: Response) -> None:
    from assistant.lib.telegram_login_auth import MINIAPP_SESSION_COOKIE

    response.delete_cookie(key=MINIAPP_SESSION_COOKIE, path="/")


def _miniapp_dev_principal_if_allowed(request: Request) -> _MiniappPrincipal | None:
    """Локальная отладка в браузере: MINIAPP_DEV_MODE + Authorization: Bearer <MINIAPP_DEV_BEARER>."""
    mode = (os.getenv("MINIAPP_DEV_MODE", "") or "").strip().lower()
    if mode not in ("1", "true", "yes"):
        return None
    auth = (request.headers.get("Authorization") or "").strip()
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    expected = (os.getenv("MINIAPP_DEV_BEARER", "miniapp-local-dev") or "miniapp-local-dev").strip()
    if parts[1].strip() != expected:
        return None
    raw_uid = (os.getenv("MINIAPP_DEV_TELEGRAM_USER_ID", "") or "").strip()
    try:
        uid = int(raw_uid)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        raise HTTPException(
            status_code=503,
            detail="MINIAPP_DEV_TELEGRAM_USER_ID не задан или неверен (нужен ваш числовой Telegram id).",
        )
    user = {
        "id": uid,
        "username": "dev_browser",
        "first_name": "Dev",
        "language_code": "ru",
    }
    return _MiniappPrincipal(uid, user)


async def _resolve_miniapp_principal(
    request: Request, *, enforce_access: bool
) -> _MiniappPrincipal:
    dev_principal = _miniapp_dev_principal_if_allowed(request)
    if dev_principal is not None:
        return _enforce_miniapp_access(dev_principal) if enforce_access else dev_principal
    auth_header = (request.headers.get("Authorization") or "").strip()
    # Mini App в Telegram: initData важнее браузерной session-cookie.
    if auth_header.lower().startswith("tma "):
        raw = _tma_init_data_from_header(auth_header)
        if raw:
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            if not bot_token:
                raise HTTPException(
                    status_code=503,
                    detail="TELEGRAM_BOT_TOKEN не задан на сервере.",
                )
            try:
                user = user_payload_from_init_data(raw, bot_token=bot_token)
                uid = int(user.get("id"))
            except ValueError as e:
                raise HTTPException(status_code=401, detail=str(e)) from e
            principal = _MiniappPrincipal(uid, user)
            return _enforce_miniapp_access(principal) if enforce_access else principal
    session_principal = _principal_from_browser_session_request(request)
    if session_principal is not None:
        return (
            _enforce_miniapp_access(session_principal)
            if enforce_access
            else session_principal
        )
    raw = _tma_init_data_from_header(auth_header)
    if raw is None and request.method in ("POST", "PUT", "PATCH"):
        ct = (request.headers.get("content-type") or "").lower()
        if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
            try:
                form = await request.form()
                fb = (form.get("initData") or form.get("init_data") or "").strip()
                if fb:
                    raw = fb
            except Exception:
                pass
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Нужен заголовок Authorization: tma <initData> (откройте из Telegram).",
        )
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(
            status_code=503,
            detail="TELEGRAM_BOT_TOKEN не задан на сервере.",
        )
    try:
        user = user_payload_from_init_data(raw, bot_token=bot_token)
        uid = int(user.get("id"))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    principal = _MiniappPrincipal(uid, user)
    return _enforce_miniapp_access(principal) if enforce_access else principal


async def require_miniapp_user(request: Request) -> _MiniappPrincipal:
    return await _resolve_miniapp_principal(request, enforce_access=True)


async def require_share_commenter(request: Request) -> _MiniappPrincipal:
    """Telegram-сессия для комментариев по ссылке: без allowlist мини-приложения."""
    return await _resolve_miniapp_principal(request, enforce_access=False)


def _enforce_miniapp_access(principal: _MiniappPrincipal) -> _MiniappPrincipal:
    from assistant.bot.access_gate import miniapp_access_message

    u = principal.user
    allowed, msg = miniapp_access_message(
        user_id=int(principal.telegram_user_id),
        username=u.get("username"),
        first_name=str(u.get("first_name") or ""),
        last_name=str(u.get("last_name") or ""),
    )
    if allowed:
        return principal
    raise HTTPException(status_code=403, detail=msg)


class _MiniappReminderPatch(BaseModel):
    task: Optional[str] = None
    when_iso: Optional[str] = None
    done: Optional[bool] = None
    checklist: Optional[list[dict[str, Any]]] = None


class _MiniappReminderCreate(BaseModel):
    task: str = ""
    when_iso: str = ""
    checklist: list[dict[str, Any]] = Field(default_factory=list)


class _MiniappCalendarEventUpdate(BaseModel):
    calendar_id: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None


class _MiniappCalendarExcludedBody(BaseModel):
    excluded_ids: list[str] = []


class _MiniappSettingsPatch(BaseModel):
    meeting_reminders_enabled: Optional[bool] = None
    zoom_auto_record_enabled: Optional[bool] = None
    telemost_auto_record_enabled: Optional[bool] = None


class _MiniappBillingPromoBody(BaseModel):
    code: str = ""


class _MiniappTodoistPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class _MiniappTodoistCreate(BaseModel):
    title: str = ""
    description: str = ""
    sync_todoist: bool = True


class _MiniappLocalNoteCreate(BaseModel):
    title: str = ""
    description: str = ""
    sync_todoist: bool = False


class _MiniappLocalNotePatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    sync_todoist: Optional[bool] = None


class _MiniappJournalPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class _MiniappTagCreate(BaseModel):
    name: str = ""


class _MiniappTagPatch(BaseModel):
    name: str = ""


class _MiniappItemTagsBody(BaseModel):
    tag_ids: list[int] = Field(default_factory=list)


class _MiniappShareCreate(BaseModel):
    access: Optional[str] = None


class _MiniappShareCommentCreate(BaseModel):
    body: str = ""
    quote: str = ""
    prefix: str = ""
    suffix: str = ""


class _MiniappGptChatTurn(BaseModel):
    role: str
    content: str


class _MiniappGptChatBody(BaseModel):
    message: str = ""
    context: str = ""
    history: list[_MiniappGptChatTurn] = Field(default_factory=list)


class _MiniappBookingAssistantConfigBody(BaseModel):
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    owner_display_name: Optional[str] = None
    owner_phone: Optional[str] = None
    assistant_gender: Optional[str] = None


class _MiniappBookingAssistantLoginStartBody(BaseModel):
    phone: str = ""


class _MiniappBookingAssistantLoginConfirmBody(BaseModel):
    code: str = ""
    password: Optional[str] = None


_JOURNAL_OPS_MINIAPP = frozenset(
    {"obuchat_transcribe", "summarize", "format_note", "answer_with_context"}
)


def _journal_preview_from_raw(raw: str) -> str:
    import re

    from assistant.lib.usage_store import journal_meta_from_raw

    meta = journal_meta_from_raw(raw if isinstance(raw, str) else None)
    main_topic = str(meta.get("main_topic") or "").strip()
    if main_topic:
        return main_topic[:400]
    text = journal_text_from_raw(raw)
    if not text:
        return ""
    plain = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n{2,}", "\n", plain).strip()
    for prefix in ("📌 Кратко", "📌 Краткое описание", "📝 Краткое содержание"):
        if plain.startswith(prefix):
            plain = plain[len(prefix) :].strip(" :—-")
            break
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:400] if plain else ""


def _journal_summary_title_from_item(item: dict[str, Any]) -> str:
    title = str(item.get("main_topic") or "").strip()
    if title:
        return title[:120]
    preview = str(item.get("preview") or "").strip()
    if preview:
        import re

        preview = re.sub(r"^#{1,6}\s+", "", preview).strip()
        return preview[:120]
    return ""


def _enrich_transcription_summary_links(
    uid: str,
    transcriptions: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> None:
    from assistant.lib.usage_store import (
        find_summary_event_for_transcript,
        get_user_usage_event,
    )

    by_transcript: dict[int, dict[str, Any]] = {}
    for summary in summaries:
        tid = summary.get("transcript_event_id")
        if tid is None:
            continue
        try:
            by_transcript[int(tid)] = summary
        except (TypeError, ValueError):
            continue
    for row in transcriptions:
        eid = row.get("id")
        if eid is None:
            continue
        try:
            transcript_id = int(eid)
        except (TypeError, ValueError):
            continue
        linked = by_transcript.get(transcript_id)
        if not linked:
            summary_id = find_summary_event_for_transcript(uid, transcript_id)
            if summary_id is None:
                continue
            srow = get_user_usage_event(uid, summary_id)
            if not srow:
                continue
            linked = _journal_row_api(dict(srow))
            linked["id"] = summary_id
        row["related_summary_id"] = linked.get("id")
        title = _journal_summary_title_from_item(linked)
        if title:
            row["related_summary_title"] = title


def _journal_row_api(row: dict[str, Any]) -> dict[str, Any]:
    from assistant.lib.usage_store import journal_meta_from_raw, journal_source_links_from_raw

    raw = row.get("raw_usage_json")
    preview = ""
    meta: dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        preview = _journal_preview_from_raw(raw)
        meta = journal_meta_from_raw(raw)
    out: dict[str, Any] = {
        "id": row.get("id"),
        "ts_utc": row.get("ts_utc"),
        "date_utc": row.get("date_utc"),
        "operation": row.get("operation"),
        "model": row.get("model"),
        "preview": preview,
    }
    if meta:
        ct = str(meta.get("content_type") or "").strip()
        if ct:
            out["content_type"] = ct
        mt = str(meta.get("main_topic") or "").strip()
        if mt:
            out["main_topic"] = mt
        meeting_topic = str(meta.get("meeting_topic") or "").strip()
        if meeting_topic:
            out["meeting_topic"] = meeting_topic
        transcript_event_id = meta.get("transcript_event_id")
        if transcript_event_id is not None:
            try:
                out["transcript_event_id"] = int(transcript_event_id)
            except (TypeError, ValueError):
                pass
    links = journal_source_links_from_raw(raw if isinstance(raw, str) else None)
    out.update(links)
    return out


_SHARE_KIND_LABELS = {
    "local": "Заметка",
    "summarize": "Саммари",
    "obuchat_transcribe": "Транскрипция",
    "format_note": "Заметка",
    "answer_with_context": "Ответ",
}


def _public_share_url(token: str) -> str:
    from assistant.lib.webapp_public import public_share_url

    return public_share_url(token)


def _share_response(link: dict[str, Any]) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    token = str(link.get("token") or "")
    return {
        "shared": True,
        "token": token,
        "url": _public_share_url(token),
        "created_at": link.get("created_at"),
        "access": share_links_store.normalize_access(link.get("access")),
    }


def _comment_author_from_principal(principal: _MiniappPrincipal) -> tuple[str, str, str | None]:
    u = principal.user or {}
    first = str(u.get("first_name") or "").strip()
    last = str(u.get("last_name") or "").strip()
    name = " ".join(p for p in (first, last) if p).strip()
    username = str(u.get("username") or "").strip() or None
    if not name:
        name = f"@{username}" if username else "Пользователь"
    return str(int(principal.telegram_user_id)), name, username


def _comment_api(row: dict[str, Any], *, viewer_uid: str | None = None) -> dict[str, Any]:
    owner = str(row.get("owner_user_id") or "")
    author = str(row.get("author_user_id") or "")
    can_delete = bool(viewer_uid) and viewer_uid in {owner, author}
    return {
        "id": int(row["id"]),
        "author_user_id": author,
        "author_name": str(row.get("author_name") or "Пользователь"),
        "author_username": row.get("author_username"),
        "body": str(row.get("body") or ""),
        "created_at": row.get("created_at"),
        "quote": str(row.get("quote") or ""),
        "prefix": str(row.get("prefix") or ""),
        "suffix": str(row.get("suffix") or ""),
        "can_delete": can_delete,
    }


def _optional_share_viewer(request: Request) -> _MiniappPrincipal | None:
    try:
        dev = _miniapp_dev_principal_if_allowed(request)
        if dev is not None:
            return dev
    except HTTPException:
        pass
    session_principal = _principal_from_browser_session_request(request)
    if session_principal is not None:
        return session_principal
    raw = _tma_init_data_from_header((request.headers.get("Authorization") or "").strip())
    if not raw:
        return None
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        return None
    try:
        user = user_payload_from_init_data(raw, bot_token=bot_token)
        return _MiniappPrincipal(int(user.get("id")), user)
    except (TypeError, ValueError):
        return None


def _share_viewer_public(principal: _MiniappPrincipal | None) -> dict[str, Any] | None:
    if principal is None:
        return None
    u = principal.user or {}
    return {
        "id": int(principal.telegram_user_id),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
    }


def _safe_share_return_path(raw: str | None) -> str | None:
    from urllib.parse import urlparse

    value = (raw or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    path = parsed.path or ""
    if not path.startswith("/share/"):
        return None
    token = path[len("/share/") :].split("/", 1)[0].split("?", 1)[0]
    if not token or len(token) < 16 or len(token) > 80:
        return None
    if any(
        c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for c in token
    ):
        return None
    if parsed.netloc:
        base = _public_webapp_base()
        if base:
            base_host = urlparse(base).netloc
            if parsed.netloc != base_host:
                return None
        elif parsed.netloc not in {"127.0.0.1:8080", "localhost:8080"}:
            return None
    return f"/share/{token}"


def _public_share_payload(link: dict[str, Any]) -> dict[str, Any] | None:
    from assistant.stores import share_links as share_links_store

    kind = str(link.get("item_kind") or "")
    item_id = str(link.get("item_id") or "").strip()
    uid = str(link.get("user_id") or "").strip()
    if not item_id or not uid:
        return None
    if kind == "local":
        from assistant.stores import notes as notes_store

        try:
            note = notes_store.get_note(uid, int(item_id))
        except (TypeError, ValueError):
            return None
        if not note:
            return None
        title = str(note.get("title") or "").strip() or "Без названия"
        return {
            "kind": "local",
            "operation": None,
            "label": _SHARE_KIND_LABELS["local"],
            "title": title[:500],
            "body": str(note.get("body") or "")[:120000],
            "updated_at": note.get("updated_at"),
            "access": share_links_store.normalize_access(link.get("access")),
        }
    if kind == "journal":
        try:
            event_id = int(item_id)
        except (TypeError, ValueError):
            return None
        row = get_user_usage_event(uid, event_id)
        if not row:
            return None
        op = str(row.get("operation") or "")
        if op not in _JOURNAL_OPS_MINIAPP:
            return None
        from assistant.lib.usage_store import journal_has_content_text

        raw = row.get("raw_usage_json")
        raw_s = raw if isinstance(raw, str) else None
        if op == "summarize" and not journal_has_content_text(raw_s):
            return None
        item = _journal_row_api(dict(row))
        title = (
            str(item.get("main_topic") or "").strip()
            or str(item.get("meeting_topic") or "").strip()
            or str(item.get("preview") or "").strip()
            or "Без названия"
        )
        return {
            "kind": "journal",
            "operation": op,
            "label": _SHARE_KIND_LABELS.get(op, "Документ"),
            "title": title[:500],
            "body": journal_text_from_raw(raw_s)[:120000],
            "updated_at": item.get("ts_utc"),
            "access": share_links_store.normalize_access(link.get("access")),
        }
    return None


def _owner_can_share_item(uid: str, kind: str, item_id: str) -> bool:
    fake = {"user_id": uid, "item_kind": kind, "item_id": item_id}
    return _public_share_payload(fake) is not None


def _event_meet_url(ev: dict[str, Any]) -> Optional[str]:
    h = ev.get("hangoutLink")
    if isinstance(h, str) and h.strip().lower().startswith(("http://", "https://")):
        return h.strip()
    cd = ev.get("conferenceData") or {}
    if isinstance(cd, dict):
        for ep in cd.get("entryPoints") or []:
            if not isinstance(ep, dict):
                continue
            u = ep.get("uri")
            if isinstance(u, str) and u.strip().lower().startswith(("http://", "https://")):
                return u.strip()
    loc = ev.get("location")
    if isinstance(loc, str) and loc.strip().lower().startswith(("http://", "https://")):
        return loc.strip()
    desc = str(ev.get("description") or "")
    for line in desc.splitlines():
        t = line.strip()
        if t.lower().startswith(("http://", "https://")):
            return t.split()[0]
    return None


def _gcal_json_time_fragment(src: Any) -> dict[str, str]:
    """Только строки в start/end — иначе FastAPI/JSON может падать на datetime и объектах Google API."""
    if not isinstance(src, dict):
        return {}
    out: dict[str, str] = {}
    for k in ("dateTime", "date", "timeZone"):
        if k not in src:
            continue
        v = src.get(k)
        if v is None:
            continue
        if hasattr(v, "isoformat"):
            try:
                out[k] = v.isoformat()
            except Exception:
                out[k] = str(v)
        else:
            s = str(v).strip()
            if s:
                out[k] = s
    return out


def _serialize_calendar_event(ev: dict[str, Any]) -> dict[str, Any]:
    from assistant.lib.calendar_event_utils import calendar_entry_kind_label

    cal_id = str(ev.get("_calendarId") or "primary").strip() or "primary"
    st_raw = ev.get("start") or {}
    en_raw = ev.get("end") or {}
    st = _gcal_json_time_fragment(st_raw if isinstance(st_raw, dict) else {})
    en = _gcal_json_time_fragment(en_raw if isinstance(en_raw, dict) else {})
    return {
        "id": str(ev.get("id") or ""),
        "calendar_id": cal_id,
        "summary": str(ev.get("summary") or "").strip(),
        "kind": calendar_entry_kind_label(ev),
        "start": st,
        "end": en,
        "html_link": str(ev.get("htmlLink") or "").strip(),
        "meet_url": _event_meet_url(ev),
        "description": str(ev.get("description") or "")[:8000],
        "location": str(ev.get("location") or "").strip(),
    }


def _miniapp_dev_mode_on() -> bool:
    return (os.getenv("MINIAPP_DEV_MODE", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _prepare_miniapp_voice_audio(
    data: bytes,
    filename: str,
    content_type: str | None,
) -> tuple[bytes, str]:
    """Browser MediaRecorder often produces WebM/Opus; convert it for Obuchat."""
    import shutil
    import subprocess
    import tempfile

    lower_name = filename.lower()
    lower_type = (content_type or "").lower()
    is_webm = lower_name.endswith(".webm") or "webm" in lower_type
    if not is_webm:
        return data, filename
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("На сервере не установлен ffmpeg для конвертации webm-аудио")
    with tempfile.TemporaryDirectory(prefix="miniapp_voice_") as td:
        src = Path(td) / "voice.webm"
        dst = Path(td) / "voice.wav"
        src.write_bytes(data)
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(dst),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError("Не удалось конвертировать webm-аудио: " + (err or "ffmpeg error"))
        out = dst.read_bytes()
        if not out:
            raise RuntimeError("Конвертация webm-аудио вернула пустой файл")
        return out, "voice.wav"


def _local_dev_calendar_events(day_iso: str, timezone_name: str) -> list[dict[str, Any]]:
    """Тестовые встречи для локального миниаппа без Google OAuth."""
    tz_suffix = "+03:00"
    if timezone_name == "Europe/Moscow":
        tz_suffix = "+03:00"

    def slot(h0: int, m0: int, h1: int, m1: int) -> tuple[str, str]:
        start = f"{day_iso}T{h0:02d}:{m0:02d}:00{tz_suffix}"
        end = f"{day_iso}T{h1:02d}:{m1:02d}:00{tz_suffix}"
        return start, end

    specs = [
        (
            "local-dev-ev-1",
            "Синк с командой",
            "Google Meet",
            10,
            0,
            10,
            30,
            "https://meet.google.com/abc-defg-hij",
            "",
        ),
        (
            "local-dev-ev-2",
            "Обед с партнёром",
            "Встреча",
            12,
            0,
            13,
            0,
            "",
            "Кафе на Тверской",
        ),
        (
            "local-dev-ev-3",
            "Демо mini app",
            "Google Meet",
            15,
            0,
            16,
            0,
            "https://meet.google.com/xyz-uvwx-rst",
            "",
        ),
        (
            "local-dev-ev-4",
            "1:1 с менеджером",
            "Zoom",
            17,
            30,
            18,
            0,
            "https://zoom.us/j/1234567890",
            "",
        ),
    ]
    out: list[dict[str, Any]] = []
    for eid, summary, kind, h0, m0, h1, m1, meet, loc in specs:
        start, end = slot(h0, m0, h1, m1)
        out.append(
            {
                "id": eid,
                "calendar_id": "primary",
                "summary": summary,
                "kind": kind,
                "start": {"dateTime": start, "timeZone": timezone_name},
                "end": {"dateTime": end, "timeZone": timezone_name},
                "html_link": f"https://calendar.google.com/calendar/event?eid={eid}",
                "meet_url": meet or None,
                "description": "[Локальный тестовый слот]",
                "location": loc,
            }
        )
    return out


def _calendar_today_payload(
    telegram_user_id: int, date_iso: Optional[str] = None
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_free_slots
    from assistant.integrations import google_calendar_oauth
    from assistant.services.calendar import _tz_for

    uid = int(telegram_user_id)
    tz = _tz_for(uid)
    has_token = google_calendar_oauth.user_token_path(uid).is_file()
    raw = (date_iso or "").strip()
    if raw:
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            day_iso = raw
        except ValueError:
            day_iso = datetime.now(tz).date().isoformat()
    else:
        day_iso = datetime.now(tz).date().isoformat()
    if not has_token:
        if _miniapp_dev_mode_on():
            return {
                "connected": True,
                "date": day_iso,
                "timezone": str(tz),
                "events": _local_dev_calendar_events(day_iso, str(tz)),
                "dev_fixtures": True,
            }
        return {
            "connected": False,
            "date": day_iso,
            "timezone": str(tz),
            "events": [],
            "error": "Подключите Google Calendar в боте: /calendar_auth",
        }
    try:
        payload = calendar_free_slots(
            {"free_slots_date": day_iso},
            telegram_user_id=int(telegram_user_id),
            include_event_details=True,
        )
    except Exception as e:
        err = str(e).strip() or "Ошибка загрузки календаря"
        if _miniapp_dev_mode_on():
            return {
                "connected": True,
                "date": day_iso,
                "timezone": str(tz),
                "events": _local_dev_calendar_events(day_iso, str(tz)),
                "dev_fixtures": True,
                "error_ignored": err,
            }
        return {
            "connected": False,
            "date": day_iso,
            "timezone": str(tz),
            "events": [],
            "error": err,
        }
    try:
        events_raw = [e for e in (payload.get("events") or []) if isinstance(e, dict)]
        ser: list[dict[str, Any]] = []
        for e in events_raw:
            try:
                ser.append(_serialize_calendar_event(e))
            except Exception as ex:
                print(f"[miniapp_calendar] skip_event id={e.get('id')!r} err={ex!r}")

        def _sort_key(d: dict[str, Any]) -> str:
            st = d.get("start") or {}
            if not isinstance(st, dict):
                return ""
            return str(st.get("dateTime") or st.get("date") or "")

        ser_sorted = sorted(ser, key=_sort_key)
        if not ser_sorted and _miniapp_dev_mode_on():
            ser_sorted = _local_dev_calendar_events(day_iso, str(tz))
        return {
            "connected": True,
            "date": day_iso,
            "timezone": str(tz),
            "events": ser_sorted[:80],
            **({"dev_fixtures": True} if not events_raw and _miniapp_dev_mode_on() else {}),
        }
    except Exception as e:
        print(f"[miniapp_calendar] serialize_failed err={e!r}")
        return {
            "connected": True,
            "date": day_iso,
            "timezone": str(tz),
            "events": [],
            "error": str(e),
        }


def _todoist_notes_for_miniapp(telegram_user_id: int) -> list[dict[str, Any]]:
    from assistant.compat.miniapp_shims import TODOIST_DEFAULT_LABEL, fetch_todoist_notes

    explicit = (os.getenv("TODOIST_MINIAPP_LABEL") or "").strip()
    label = explicit or (TODOIST_DEFAULT_LABEL or "").strip() or "разобрать"
    return fetch_todoist_notes(
        100, telegram_user_id=int(telegram_user_id), label_filter=label
    )


_miniapp_get_me_done = False
_miniapp_bot_username_from_token: str | None = None


def _miniapp_resolve_bot_username() -> str | None:
    """Имя бота для deep link: из getMe по TELEGRAM_BOT_TOKEN (совпадает с подписью initData), иначе TELEGRAM_BOT_USERNAME."""
    global _miniapp_get_me_done, _miniapp_bot_username_from_token
    if not _miniapp_get_me_done:
        _miniapp_get_me_done = True
        token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
        if token:
            try:
                r = requests.get(
                    f"https://api.telegram.org/bot{token}/getMe",
                    timeout=5,
                )
                j = r.json()
                if j.get("ok") and isinstance(j.get("result"), dict):
                    u = (j["result"].get("username") or "").strip().lstrip("@")
                    if u:
                        _miniapp_bot_username_from_token = u
            except Exception as ex:
                print(f"[miniapp] getMe failed: {ex!r}")
    if _miniapp_bot_username_from_token:
        return _miniapp_bot_username_from_token
    un = (os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
    return un or None


miniapp_router = APIRouter(prefix="/api/miniapp")


def _telegram_bot_id_from_token() -> int | None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if ":" not in token:
        return None
    try:
        return int(token.split(":", 1)[0])
    except (TypeError, ValueError):
        return None


@miniapp_router.get("/auth/config")
async def miniapp_auth_config() -> dict[str, Any]:
    """Публично: имя бота и bot_id для Telegram Login в обычном браузере."""
    username = _miniapp_resolve_bot_username()
    if not username:
        env_u = (os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
        username = env_u or None
    bot_id = _telegram_bot_id_from_token()
    token_ok = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    return {
        "telegram_login_enabled": bool(username and token_ok and bot_id),
        "bot_username": username,
        "bot_id": bot_id,
    }


class _TelegramLoginBody(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str


def _miniapp_telegram_login_from_mapping(
    data: dict[str, Any],
) -> tuple[int, dict[str, Any], str, int]:
    from assistant.lib.telegram_login_auth import (
        issue_browser_session,
        verify_login_widget_payload,
    )

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(
            status_code=503,
            detail="TELEGRAM_BOT_TOKEN не задан на сервере.",
        )
    try:
        user = verify_login_widget_payload(data, bot_token=bot_token)
        uid = int(user["id"])
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    try:
        token, exp = issue_browser_session(uid, user)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return uid, user, token, exp


@miniapp_router.get("/auth/telegram/callback")
async def miniapp_auth_telegram_callback(request: Request) -> RedirectResponse:
    """OAuth return_to с query-параметрами (редко; обычно Telegram шлёт #tgAuthResult на return_to)."""
    data = {k: request.query_params[k] for k in request.query_params}
    share_next = _safe_share_return_path(
        data.pop("return_to", None) or request.cookies.get("leo_login_return")
    )
    webapp_url = webapp_entry_url()
    next_url = share_next or webapp_url
    if not data.get("hash"):
        return RedirectResponse(url=next_url, status_code=302)
    _, _, token, _exp = _miniapp_telegram_login_from_mapping(data)
    resp = RedirectResponse(url=next_url, status_code=302)
    _attach_miniapp_session_cookie(resp, token)
    return resp


@miniapp_router.post("/auth/telegram")
async def miniapp_auth_telegram(body: _TelegramLoginBody) -> JSONResponse:
    """Обмен данных Telegram Login Widget / popup на сессию для браузера."""
    payload = body.model_dump(exclude_none=True)
    _uid, user, token, exp = _miniapp_telegram_login_from_mapping(payload)
    resp = JSONResponse(
        {
            "session_token": token,
            "expires_at": exp,
            "user": user,
        }
    )
    _attach_miniapp_session_cookie(resp, token)
    return resp


@miniapp_router.post("/auth/session")
async def miniapp_auth_session(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> JSONResponse:
    """Issue/refresh browser session after Telegram WebApp initData auth."""
    from assistant.lib.telegram_login_auth import issue_browser_session

    uid = int(principal.telegram_user_id)
    user = dict(principal.user or {})
    user.setdefault("id", uid)
    try:
        token, exp = issue_browser_session(uid, user)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    resp = JSONResponse(
        {
            "session_token": token,
            "expires_at": exp,
            "user": user,
        }
    )
    _attach_miniapp_session_cookie(resp, token)
    return resp


@miniapp_router.post("/auth/logout")
async def miniapp_auth_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    _clear_miniapp_session_cookie(resp)
    return resp


@miniapp_router.get("/me")
async def miniapp_me(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict:
    u = principal.user
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "language_code": u.get("language_code"),
        "bot_username": _miniapp_resolve_bot_username(),
    }


@miniapp_router.get("/integrations")
async def miniapp_integrations(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    import json

    tid = int(principal.telegram_user_id)
    from assistant.integrations.google_calendar_oauth import user_token_path as google_user_token_path
    from assistant.integrations.todoist_oauth import user_token_path as todoist_user_token_path
    from assistant.integrations.yandex_disk_oauth import (
        needs_scope_refresh as yandex_disk_needs_scope_refresh,
        user_token_path as yandex_disk_user_token_path,
    )
    from assistant.integrations.telemost_oauth import user_token_path as telemost_user_token_path
    from assistant.integrations.zoom_oauth import user_token_path as zoom_user_token_path
    from assistant.integrations.bitrix_mcp_token import is_connected as bitrix_mcp_is_connected

    google_on = google_user_token_path(tid).is_file()
    todoist_on = todoist_user_token_path(tid).is_file()
    yandex_path = yandex_disk_user_token_path(tid)
    yandex_on = yandex_path.is_file()
    yandex_needs_scope_refresh = yandex_on and yandex_disk_needs_scope_refresh(tid)
    yandex_login = ""
    yandex_display_name = ""
    if yandex_on:
        try:
            ystore = json.loads(yandex_path.read_text(encoding="utf-8"))
            if isinstance(ystore, dict):
                yandex_login = str(ystore.get("yandex_login") or ystore.get("yandex_email") or "")
                yandex_display_name = str(ystore.get("yandex_display_name") or yandex_login or "")
        except (OSError, json.JSONDecodeError):
            pass
    zoom_path = zoom_user_token_path(tid)
    zoom_on = zoom_path.is_file()
    zoom_email = ""
    zoom_display_name = ""
    if zoom_on:
        try:
            zstore = json.loads(zoom_path.read_text(encoding="utf-8"))
            if isinstance(zstore, dict):
                zoom_email = str(zstore.get("zoom_email") or "")
                zoom_display_name = str(zstore.get("zoom_display_name") or "")
        except (OSError, json.JSONDecodeError):
            pass
    telemost_path = telemost_user_token_path(tid)
    telemost_on = telemost_path.is_file()
    telemost_email = ""
    telemost_display_name = ""
    telemost_org_likely: bool | None = None
    if telemost_on:
        try:
            tstore = json.loads(telemost_path.read_text(encoding="utf-8"))
            if isinstance(tstore, dict):
                telemost_email = str(tstore.get("yandex_email") or "")
                telemost_display_name = str(tstore.get("yandex_display_name") or "")
                val = tstore.get("telemost_org_likely")
                if isinstance(val, bool):
                    telemost_org_likely = val
        except (OSError, json.JSONDecodeError):
            pass

    bitrix_on = bitrix_mcp_is_connected(tid)

    return {
        "google": google_on,
        "google_connected": google_on,
        "todoist": todoist_on,
        "todoist_connected": todoist_on,
        "zoom": zoom_on,
        "zoom_connected": zoom_on,
        "zoom_email": zoom_email,
        "zoom_display_name": zoom_display_name,
        "telemost": telemost_on,
        "telemost_connected": telemost_on,
        "telemost_email": telemost_email,
        "telemost_display_name": telemost_display_name,
        "telemost_org_likely": telemost_org_likely,
        "yandex-disk": yandex_on,
        "yandex_disk": yandex_on,
        "yandex_disk_connected": yandex_on,
        "yandex_disk_login": yandex_login,
        "yandex_disk_display_name": yandex_display_name,
        "yandex_disk_needs_scope_refresh": yandex_needs_scope_refresh,
        "booking_assistant": False,
        "booking_whatsapp": False,
        "booking_assistant_can_disconnect": False,
        "bitrix": bitrix_on,
        "bitrix_connected": bitrix_on,
    }


@miniapp_router.get("/usage")
async def miniapp_usage(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    days: int = 30,
) -> dict[str, Any]:
    uid_str = str(principal.telegram_user_id)
    n = min(max(int(days), 1), 366)
    return {
        "lifetime": user_lifetime_stats(uid_str),
        "daily": user_daily_usage_no_cost(uid_str, last_n_days=n),
        "daily_window_days": n,
    }


@miniapp_router.get("/usage/expenses")
async def miniapp_usage_expenses(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    days: int = 7,
) -> dict[str, Any]:
    uid_str = str(principal.telegram_user_id)
    n = min(max(int(days), 1), 366)
    try:
        rate = float(os.getenv("MINIAPP_USD_TO_RUB", "100").strip())
    except (TypeError, ValueError):
        rate = 100.0
    rows = await run_in_threadpool(
        partial(user_expenses_by_day, uid_str, last_n_days=n)
    )
    total = round(sum(float(d.get("total_rub") or 0) for d in rows), 2)
    return {
        "days_window": n,
        "total_rub": total,
        "days": rows,
        "usd_to_rub": rate,
        "rub_multiplier": 5,
        "formula": "cost_rub = cost_usd * MINIAPP_USD_TO_RUB * 5",
    }


@miniapp_router.get("/reminders")
async def miniapp_reminders_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    items = await run_in_threadpool(
        reminders_store.reminders_for_user, int(principal.telegram_user_id)
    )
    return {"items": items}


@miniapp_router.post("/reminders")
async def miniapp_reminders_create(
    body: _MiniappReminderCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    task = str(body.task or "").strip()
    when_iso = str(body.when_iso or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="Укажите текст напоминания")
    if not when_iso:
        raise HTTPException(status_code=400, detail="Укажите дату и время")

    rid = await run_in_threadpool(
        partial(
            reminders_store.add_reminder,
            user_id=int(principal.telegram_user_id),
            chat_id=int(principal.telegram_user_id),
            task=task,
            when_iso=when_iso,
            checklist=body.checklist,
        )
    )
    item = await run_in_threadpool(reminders_store.get_reminder_by_id, rid)
    if not item:
        raise HTTPException(status_code=502, detail="Напоминание не сохранено")
    return {"item": item}


@miniapp_router.patch("/reminders/{reminder_id}")
async def miniapp_reminders_patch(
    reminder_id: str,
    body: _MiniappReminderPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    existing = await run_in_threadpool(reminders_store.get_reminder_by_id, reminder_id)
    if not existing or int(existing.get("user_id") or 0) != int(principal.telegram_user_id):
        raise HTTPException(status_code=404, detail="Напоминание не найдено")
    updated = await run_in_threadpool(
        partial(
            reminders_store.patch_reminder,
            reminder_id,
            task=body.task,
            when_iso=body.when_iso,
            done=body.done,
            checklist=body.checklist,
        )
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Напоминание не найдено")
    return {"item": updated}


@miniapp_router.delete("/reminders/{reminder_id}")
async def miniapp_reminders_delete(
    reminder_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    existing = await run_in_threadpool(reminders_store.get_reminder_by_id, reminder_id)
    if not existing or int(existing.get("user_id") or 0) != int(principal.telegram_user_id):
        raise HTTPException(status_code=404, detail="Напоминание не найдено")
    await run_in_threadpool(reminders_store.remove_reminder, reminder_id)
    return {"ok": True}


class _MiniappContactBody(BaseModel):
    name: str = ""
    email: str = ""
    telegram_username: Optional[str] = None
    aliases: Optional[list[str]] = None


class _MiniappContactPatch(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    telegram_username: Optional[str] = None
    aliases: Optional[list[str]] = None
    clear_telegram: bool = False


def _miniapp_tg_username(principal: _MiniappPrincipal) -> str | None:
    u = principal.user.get("username")
    return str(u).strip() if u else None


@miniapp_router.get("/contacts")
async def miniapp_contacts_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import contacts_store

    items = await run_in_threadpool(
        partial(
            contacts_store.load_contacts,
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=_miniapp_tg_username(principal),
        )
    )
    return {"items": items}


@miniapp_router.post("/contacts")
async def miniapp_contacts_create(
    body: _MiniappContactBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import contacts_store

    item, status = await run_in_threadpool(
        partial(
            contacts_store.create_contact_for_user,
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=_miniapp_tg_username(principal),
            name=body.name,
            email=body.email,
            telegram_username_contact=body.telegram_username,
            aliases=body.aliases,
        )
    )
    if status == "invalid":
        raise HTTPException(status_code=400, detail="Укажите имя и корректный email")
    if item is None:
        raise HTTPException(status_code=400, detail="Не удалось сохранить контакт")
    return {"item": item, "status": status}


@miniapp_router.put("/contacts/{email}")
async def miniapp_contacts_update(
    email: str,
    body: _MiniappContactPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import contacts_store

    old_email = unquote(email or "").strip()
    if not old_email:
        raise HTTPException(status_code=400, detail="Некорректный email")
    item = await run_in_threadpool(
        partial(
            contacts_store.update_contact_for_user,
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=_miniapp_tg_username(principal),
            old_email=old_email,
            name=body.name,
            email=body.email,
            telegram_username_contact=body.telegram_username,
            aliases=body.aliases,
            clear_telegram=body.clear_telegram,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    return {"item": item}


@miniapp_router.delete("/contacts/{email}")
async def miniapp_contacts_delete(
    email: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import contacts_store

    target = unquote(email or "").strip()
    ok = await run_in_threadpool(
        partial(
            contacts_store.delete_contact_for_user,
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=_miniapp_tg_username(principal),
            email=target,
        )
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    return {"ok": True}


@miniapp_router.get("/settings")
async def miniapp_settings_get(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import meeting_reminders as mr
    from assistant.services import meeting_record_schedule as mrec
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        return {
            "meeting_reminders_enabled": user_prefs.meeting_reminders_enabled(uid),
            "meeting_reminder_minutes_before": mr.reminder_minutes_before(),
            "zoom_auto_record_enabled": user_prefs.zoom_auto_record_enabled(uid),
            "telemost_auto_record_enabled": user_prefs.telemost_auto_record_enabled(uid),
            "meeting_bot_available": mrec.service_available(),
        }

    return await run_in_threadpool(_run)


@miniapp_router.patch("/settings")
async def miniapp_settings_patch(
    body: _MiniappSettingsPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import meeting_reminders as mr
    from assistant.services import meeting_record_schedule as mrec
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        if body.meeting_reminders_enabled is not None:
            user_prefs.set_meeting_reminders_enabled(
                uid, bool(body.meeting_reminders_enabled)
            )
        if body.zoom_auto_record_enabled is not None:
            user_prefs.set_zoom_auto_record_enabled(
                uid, bool(body.zoom_auto_record_enabled)
            )
        if body.telemost_auto_record_enabled is not None:
            user_prefs.set_telemost_auto_record_enabled(
                uid, bool(body.telemost_auto_record_enabled)
            )
        return {
            "ok": True,
            "meeting_reminders_enabled": user_prefs.meeting_reminders_enabled(uid),
            "meeting_reminder_minutes_before": mr.reminder_minutes_before(),
            "zoom_auto_record_enabled": user_prefs.zoom_auto_record_enabled(uid),
            "telemost_auto_record_enabled": user_prefs.telemost_auto_record_enabled(uid),
            "meeting_bot_available": mrec.service_available(),
        }

    return await run_in_threadpool(_run)


@miniapp_router.get("/calendar/sources")
async def miniapp_calendar_sources(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import calendar_sources as cs

    def _run() -> dict[str, Any]:
        uid = int(principal.telegram_user_id)
        return {"calendars": cs.calendar_sources_for_miniapp(uid)}

    try:
        return await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@miniapp_router.put("/calendar/sources/excluded")
async def miniapp_calendar_sources_excluded(
    body: _MiniappCalendarExcludedBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import calendar_sources as cs

    def _run() -> dict[str, Any]:
        uid = int(principal.telegram_user_id)
        cs.set_calendar_excluded(uid, list(body.excluded_ids or []))
        return {"ok": True, "calendars": cs.calendar_sources_for_miniapp(uid)}

    try:
        return await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@miniapp_router.get("/calendar/today")
async def miniapp_calendar_today(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    date: Optional[str] = Query(
        None,
        description="День в формате YYYY-MM-DD (календарная TZ пользователя); по умолчанию сегодня",
    ),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _calendar_today_payload, int(principal.telegram_user_id), date
    )


@miniapp_router.post("/calendar/events")
async def miniapp_calendar_event_create(
    body: _MiniappCalendarEventUpdate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_create_event

    title = str(body.title or "").strip()
    start = str(body.start or "").strip()
    calendar_id = str(body.calendar_id or "").strip() or None
    if not title:
        raise HTTPException(status_code=400, detail="Укажите название встречи")
    if not start:
        raise HTTPException(status_code=400, detail="Укажите начало встречи")
    parsed: dict[str, Any] = {
        "title": title,
        "start": start,
        "duration_min": 60,
    }
    end = str(body.end or "").strip()
    if end:
        parsed["end"] = end
    if body.description is not None:
        parsed["description"] = str(body.description)
    if body.location is not None:
        parsed["location"] = str(body.location).strip()
    uname = (
        principal.user.get("username")
        if isinstance(principal.user, dict)
        else None
    )

    def _run() -> dict[str, Any]:
        return calendar_create_event(
            parsed,
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=str(uname).strip() or None,
            calendar_id=calendar_id,
        )

    try:
        created = await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    event_id = str(created.get("event_id") or created.get("id") or "").strip()
    if not event_id:
        raise HTTPException(status_code=502, detail="Пустой ответ календаря")
    start_dt = created.get("start")
    end_dt = created.get("end")
    ser = {
        "id": event_id,
        "calendar_id": str(created.get("calendar_id") or body.calendar_id or "primary"),
        "summary": str(created.get("summary") or title),
        "kind": "Встреча",
        "start": {"dateTime": start_dt.isoformat() if hasattr(start_dt, "isoformat") else start},
        "end": {"dateTime": end_dt.isoformat() if hasattr(end_dt, "isoformat") else (end or "")},
        "html_link": str(created.get("html_link") or ""),
        "meet_url": "",
        "description": str(body.description or ""),
        "location": str(body.location or "").strip(),
    }
    return {"event": ser}


@miniapp_router.put("/calendar/events/{event_id}")
async def miniapp_calendar_event_update(
    event_id: str,
    body: _MiniappCalendarEventUpdate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_update_event

    parsed: dict[str, Any] = {}
    if body.title is not None and str(body.title).strip():
        parsed["title"] = str(body.title).strip()
    if body.start is not None and str(body.start).strip():
        parsed["start"] = str(body.start).strip()
    if body.end is not None and str(body.end).strip():
        parsed["end"] = str(body.end).strip()
    if body.description is not None:
        parsed["description"] = str(body.description)
    if body.location is not None:
        parsed["location"] = str(body.location).strip()
    if not parsed:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    uname = (
        principal.user.get("username")
        if isinstance(principal.user, dict)
        else None
    )

    def _run() -> dict[str, Any]:
        return calendar_update_event(
            event_id,
            parsed,
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=str(uname).strip() or None,
            calendar_id=body.calendar_id,
        )

    try:
        ev = await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not isinstance(ev, dict):
        raise HTTPException(status_code=502, detail="Пустой ответ календаря")
    ser = _serialize_calendar_event(ev)
    if body.calendar_id and str(body.calendar_id).strip():
        ser["calendar_id"] = str(body.calendar_id).strip()
    return {"event": ser}


@miniapp_router.delete("/calendar/events/{event_id}")
async def miniapp_calendar_event_delete(
    event_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    calendar_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_delete_event

    def _run() -> None:
        calendar_delete_event(
            event_id,
            telegram_user_id=int(principal.telegram_user_id),
            calendar_id=calendar_id,
        )

    try:
        await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"ok": True}


@miniapp_router.post("/calendar/events/{event_id}/zoom-link")
async def miniapp_calendar_event_zoom_link(
    event_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    calendar_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_attach_zoom_link

    def _run() -> dict[str, Any]:
        return calendar_attach_zoom_link(
            event_id,
            telegram_user_id=int(principal.telegram_user_id),
            calendar_id=calendar_id,
        )

    try:
        data = await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    join = str(data.get("join_url") or "").strip()
    if not join:
        raise HTTPException(status_code=502, detail="Нет ссылки Zoom")
    return {"join_url": join, "already": bool(data.get("already"))}


@miniapp_router.post("/calendar/events/{event_id}/telemost-link")
async def miniapp_calendar_event_telemost_link(
    event_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    calendar_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_attach_telemost_link

    def _run() -> dict[str, Any]:
        return calendar_attach_telemost_link(
            event_id,
            telegram_user_id=int(principal.telegram_user_id),
            calendar_id=calendar_id,
        )

    try:
        data = await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    join = str(data.get("join_url") or "").strip()
    if not join:
        raise HTTPException(status_code=502, detail="Нет ссылки Телемост")
    return {"join_url": join, "already": bool(data.get("already"))}


@miniapp_router.post("/gpt/chat")
async def miniapp_gpt_chat(
    body: _MiniappGptChatBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import gpt_openrouter_answer_with_context

    q = (body.message or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    hist: list[dict[str, str]] = []
    for it in body.history[:50]:
        r = (it.role or "").strip().lower()
        if r not in ("user", "assistant"):
            continue
        c = (it.content or "").strip()
        if not c:
            continue
        hist.append({"role": r, "content": c[:24000]})

    tg_uname: str | None = None
    if isinstance(principal.user, dict):
        ru = principal.user.get("username")
        if ru:
            tg_uname = str(ru).strip() or None

    def _run() -> dict[str, Any] | None:
        from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user

        uid = int(principal.telegram_user_id)
        try:
            set_openrouter_usage_telegram_user(
                telegram_user_id=uid,
                telegram_username=tg_uname,
            )
            return gpt_openrouter_answer_with_context(
                q,
                (body.context or "").strip(),
                history=hist or None,
            )
        finally:
            set_openrouter_usage_telegram_user(telegram_user_id=None)

    try:
        out = await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not out:
        raise HTTPException(status_code=502, detail="Пустой ответ модели")
    return {
        "answer": str(out.get("answer") or ""),
        "bullets": out.get("bullets") if isinstance(out.get("bullets"), list) else [],
    }


@miniapp_router.post("/voice/transcribe")
async def miniapp_voice_transcribe(
    file: UploadFile = File(...),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.integrations import transcribe as obu
    from assistant.lib.usage_store import insert_usage_event

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой аудиофайл")
    max_bytes = 50 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Аудио слишком большое")
    filename = str(file.filename or "voice.webm").strip() or "voice.webm"
    source_filename = filename
    source_size = len(data)
    content_type = file.content_type
    try:
        data, filename = await run_in_threadpool(
            _prepare_miniapp_voice_audio,
            data,
            filename,
            content_type,
        )
    except RuntimeError as e:
        print(
            "[miniapp_voice_transcribe] prepare_failed "
            f"filename={source_filename!r} content_type={content_type!r} "
            f"bytes={source_size} err={e!r}"
        )
        raise HTTPException(status_code=502, detail=str(e)) from e
    username = None
    if isinstance(principal.user, dict) and principal.user.get("username"):
        username = str(principal.user.get("username") or "").strip() or None

    def _run() -> dict[str, Any]:
        result = obu.transcribe_bytes(data, filename)
        transcript = (result.formatted_text or result.plain_text or "").strip()
        if not transcript:
            raise RuntimeError("(пустая транскрипция)")
        event_id = insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id=result.job_id,
            usage={
                "text": transcript,
                "source": "miniapp_voice",
                "filename": filename,
            },
            telegram_user_id=str(int(principal.telegram_user_id)),
            telegram_username=username,
        )
        return {
            "text": transcript,
            "plain_text": result.plain_text,
            "job_id": result.job_id,
            "event_id": event_id,
        }

    try:
        return await run_in_threadpool(_run)
    except RuntimeError as e:
        print(
            "[miniapp_voice_transcribe] failed "
            f"source_filename={source_filename!r} filename={filename!r} "
            f"content_type={content_type!r} source_bytes={source_size} "
            f"bytes={len(data)} err={e!r}"
        )
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        print(
            "[miniapp_voice_transcribe] failed "
            f"source_filename={source_filename!r} filename={filename!r} "
            f"content_type={content_type!r} source_bytes={source_size} "
            f"bytes={len(data)} err={e!r}"
        )
        raise HTTPException(status_code=502, detail=f"Транскрибация: {e}") from e


@miniapp_router.get("/notes")
async def miniapp_notes_bundle(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    limit: int = 120,
) -> dict[str, Any]:
    uid = int(principal.telegram_user_id)
    lim = min(max(int(limit), 1), 300)
    journal: list[dict[str, Any]] = []
    journal_err: str | None = None
    try:
        journal = await run_in_threadpool(
            partial(user_journal_entries, str(uid), limit=lim)
        )
    except Exception:
        journal_err = "Не удалось загрузить журнал. Попробуйте обновить страницу позже."
    local_err: str | None = None
    tags_err: str | None = None
    all_tags: list[dict[str, Any]] = []
    try:
        from assistant.stores import notes as notes_store

        local_notes = await run_in_threadpool(
            partial(notes_store.list_notes, uid, limit=lim)
        )
    except Exception as e:
        local_notes = []
        local_err = str(e)
    out_journal: list[dict[str, Any]] = []
    transcriptions: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for row in journal:
        if not isinstance(row, dict):
            continue
        item = _journal_row_api(dict(row))
        out_journal.append(item)
        op = str(row.get("operation") or "")
        if op == "obuchat_transcribe":
            transcriptions.append(item)
        elif op == "summarize":
            summaries.append(item)

    _enrich_transcription_summary_links(str(uid), transcriptions, summaries)

    def _attach_tags() -> list[dict[str, Any]]:
        from assistant.stores import tags as tags_store

        tag_items: list[tuple[str, str]] = []
        for n in local_notes:
            tag_items.append(("local", str(n.get("id"))))
        for row in transcriptions + summaries:
            tag_items.append(("journal", str(row.get("id"))))
        mapping = tags_store.tags_by_items(uid, tag_items)
        for n in local_notes:
            n["tags"] = mapping.get(("local", str(n.get("id"))), [])
        for row in transcriptions:
            row["tags"] = mapping.get(("journal", str(row.get("id"))), [])
        for row in summaries:
            row["tags"] = mapping.get(("journal", str(row.get("id"))), [])
        return tags_store.list_tags(uid)

    try:
        all_tags = await run_in_threadpool(_attach_tags)
    except Exception as e:
        tags_err = str(e)
        for n in local_notes:
            n.setdefault("tags", [])
        for row in transcriptions + summaries:
            row.setdefault("tags", [])

    return {
        "journal": out_journal,
        "transcriptions": transcriptions,
        "summaries": summaries,
        "journal_error": journal_err,
        "local_notes": local_notes,
        "local_error": local_err,
        "tags": all_tags,
        "tags_error": tags_err,
        "todoist_notes": [],
        "todoist_error": None,
    }


@miniapp_router.get("/tags")
async def miniapp_tags_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import tags as tags_store

    uid = int(principal.telegram_user_id)
    items = await run_in_threadpool(partial(tags_store.list_tags, uid))
    return {"tags": items}


@miniapp_router.post("/tags")
async def miniapp_tag_create(
    body: _MiniappTagCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import tags as tags_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        return tags_store.create_tag(uid, body.name)

    try:
        item = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "tag": item}


@miniapp_router.patch("/tags/{tag_id}")
async def miniapp_tag_patch(
    tag_id: int,
    body: _MiniappTagPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import tags as tags_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        return tags_store.update_tag(uid, tag_id, name=body.name)

    try:
        item = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not item:
        raise HTTPException(status_code=404, detail="Тег не найден")
    return {"ok": True, "tag": item}


@miniapp_router.delete("/tags/{tag_id}")
async def miniapp_tag_delete(
    tag_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import tags as tags_store

    uid = int(principal.telegram_user_id)

    def _run() -> bool:
        return tags_store.delete_tag(uid, tag_id)

    ok = await run_in_threadpool(_run)
    if not ok:
        raise HTTPException(status_code=404, detail="Тег не найден")
    return {"ok": True}


@miniapp_router.put("/notes/{item_kind}/{item_id}/tags")
async def miniapp_item_tags_set(
    item_kind: str,
    item_id: str,
    body: _MiniappItemTagsBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store
    from assistant.stores import tags as tags_store

    uid = int(principal.telegram_user_id)
    kind = (item_kind or "").strip().lower()
    if kind not in ("local", "journal"):
        raise HTTPException(status_code=400, detail="Недопустимый тип записи")

    if kind == "local":
        try:
            nid = int(item_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Некорректный id заметки") from e

        def _check_local() -> bool:
            return notes_store.get_note(uid, nid) is not None

        if not await run_in_threadpool(_check_local):
            raise HTTPException(status_code=404, detail="Заметка не найдена")
    else:
        row = await run_in_threadpool(
            get_user_usage_event, str(uid), int(item_id)
        )
        if not row:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        op = str(row.get("operation") or "")
        if op not in _JOURNAL_OPS_MINIAPP:
            raise HTTPException(status_code=404, detail="Запись недоступна")

    def _run() -> list[dict[str, Any]]:
        return tags_store.set_item_tags(uid, kind, item_id, body.tag_ids)

    try:
        tags = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "tags": tags}


@miniapp_router.get("/notes/journal/{event_id}")
async def miniapp_journal_item(
    event_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    uid = str(int(principal.telegram_user_id))
    row = await run_in_threadpool(get_user_usage_event, uid, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    op = str(row.get("operation") or "")
    if op not in _JOURNAL_OPS_MINIAPP:
        raise HTTPException(status_code=404, detail="Запись недоступна")
    from assistant.lib.usage_store import (
        find_summary_event_for_transcript,
        journal_has_content_text,
        journal_meta_from_raw,
        journal_source_links_from_raw,
    )

    raw = row.get("raw_usage_json")
    raw_s = raw if isinstance(raw, str) else None
    if op == "summarize" and not journal_has_content_text(raw_s):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    body = journal_text_from_raw(raw_s)
    item = _journal_row_api(dict(row))
    item["body"] = body[:120000]
    item.update(journal_source_links_from_raw(raw_s))
    meta = journal_meta_from_raw(raw_s)
    if op == "summarize":
        transcript_event_id = meta.get("transcript_event_id")
        if transcript_event_id is not None:
            try:
                item["transcript_event_id"] = int(transcript_event_id)
            except (TypeError, ValueError):
                pass
    elif op == "obuchat_transcribe":
        meeting_topic = str(meta.get("meeting_topic") or "").strip()
        if meeting_topic:
            item["meeting_topic"] = meeting_topic
        related_summary_id = find_summary_event_for_transcript(uid, event_id)
        if related_summary_id is not None:
            item["related_summary_id"] = related_summary_id
            srow = get_user_usage_event(uid, related_summary_id)
            if srow:
                stitle = _journal_summary_title_from_item(_journal_row_api(dict(srow)))
                if stitle:
                    item["related_summary_title"] = stitle
    from assistant.stores import tags as tags_store

    item["tags"] = await run_in_threadpool(
        partial(tags_store.get_item_tags, uid, "journal", str(event_id))
    )
    return {"item": item}


@miniapp_router.delete("/notes/journal/{event_id}")
async def miniapp_journal_delete(
    event_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    uid = str(int(principal.telegram_user_id))

    def _run() -> bool:
        return delete_user_journal_event(uid, event_id)

    ok = await run_in_threadpool(_run)
    if not ok:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    from assistant.stores import share_links as share_links_store

    await run_in_threadpool(share_links_store.revoke_share, uid, "journal", event_id)
    from assistant.stores import share_comments as share_comments_store

    await run_in_threadpool(share_comments_store.delete_all_for_item, uid, "journal", event_id)
    return {"ok": True}


@miniapp_router.patch("/notes/journal/{event_id}")
async def miniapp_journal_patch(
    event_id: int,
    body: _MiniappJournalPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    if body.title is None and body.description is None:
        raise HTTPException(status_code=400, detail="Укажите title и/или description")
    uid = str(int(principal.telegram_user_id))

    def _run() -> dict[str, Any] | None:
        from assistant.lib.usage_store import (
            journal_has_content_text,
            journal_source_links_from_raw,
        )

        row = update_user_journal_event(
            uid,
            event_id,
            text=body.description,
            main_topic=body.title,
        )
        if not row:
            return None
        op = str(row.get("operation") or "")
        if op not in _JOURNAL_OPS_MINIAPP:
            return None
        raw = row.get("raw_usage_json")
        raw_s = raw if isinstance(raw, str) else None
        if op == "summarize" and not journal_has_content_text(raw_s):
            return None
        item = _journal_row_api(dict(row))
        item["body"] = journal_text_from_raw(raw_s)[:120000]
        item.update(journal_source_links_from_raw(raw_s))
        from assistant.stores import tags as tags_store

        item["tags"] = tags_store.get_item_tags(uid, "journal", str(event_id))
        return item

    item = await run_in_threadpool(_run)
    if not item:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"ok": True, "item": item}


def _miniapp_send_journal_pdf_task(telegram_user_id: int, event_id: int) -> None:
    from assistant.skills.journal_pdf import deliver_journal_pdf_sync

    deliver_journal_pdf_sync(
        telegram_user_id=int(telegram_user_id),
        event_id=int(event_id),
        notify_on_fail=True,
    )


@miniapp_router.post("/notes/journal/{event_id}/pdf")
async def miniapp_journal_pdf(
    event_id: int,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    uid = str(int(principal.telegram_user_id))
    row = await run_in_threadpool(get_user_usage_event, uid, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    op = str(row.get("operation") or "")
    if op not in _JOURNAL_OPS_MINIAPP or op not in ("obuchat_transcribe", "summarize"):
        raise HTTPException(status_code=404, detail="PDF недоступен для этой записи")
    from assistant.lib.usage_store import journal_has_content_text

    raw_s = row.get("raw_usage_json")
    if op == "summarize" and not journal_has_content_text(
        raw_s if isinstance(raw_s, str) else None
    ):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    background_tasks.add_task(
        _miniapp_send_journal_pdf_task,
        int(principal.telegram_user_id),
        int(event_id),
    )
    return {
        "ok": True,
        "message": "Файл генерируется, по готовности будет отправлен в чат",
    }


@miniapp_router.post("/notes/local")
async def miniapp_local_note_create(
    body: _MiniappLocalNoteCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Укажите заголовок заметки")
    desc = (body.description or "").strip()
    uid = int(principal.telegram_user_id)

    def _local() -> dict[str, Any]:
        return notes_store.create_note(uid, title, desc)

    item = await run_in_threadpool(_local)
    return {"ok": True, "id": item["id"], "item": item}


@miniapp_router.patch("/notes/local/{note_id}")
async def miniapp_local_note_patch(
    note_id: int,
    body: _MiniappLocalNotePatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    if body.title is None and body.description is None and body.sync_todoist is None:
        raise HTTPException(status_code=400, detail="Укажите title и/или description")
    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        item = notes_store.update_note(
            uid,
            note_id,
            title=body.title,
            body=body.description,
        )
        if item is None:
            return None
        if body.sync_todoist and item.get("todoist_id"):
            from assistant.compat.miniapp_shims import set_todoist_task_content

            set_todoist_task_content(
                str(item["todoist_id"]),
                content=body.title,
                description=body.description,
                telegram_user_id=uid,
            )
        elif body.sync_todoist and not item.get("todoist_id"):
            from assistant.compat.miniapp_shims import add_todoist_note

            tid = add_todoist_note(
                item["title"],
                item.get("body") or "",
                telegram_user_id=uid,
            )
            item = notes_store.update_note(uid, note_id, todoist_id=tid) or item
        return item

    item = await run_in_threadpool(_run)
    if not item:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "item": item}


@miniapp_router.delete("/notes/local/{note_id}")
async def miniapp_local_note_delete(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> bool:
        return notes_store.delete_note(uid, note_id)

    ok = await run_in_threadpool(_run)
    if not ok:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    from assistant.stores import share_links as share_links_store

    await run_in_threadpool(share_links_store.revoke_share, uid, "local", note_id)
    from assistant.stores import share_comments as share_comments_store

    await run_in_threadpool(share_comments_store.delete_all_for_item, uid, "local", note_id)
    return {"ok": True}


@miniapp_router.get("/notes/{kind}/{item_id}/share")
async def miniapp_share_get(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    uid = str(int(principal.telegram_user_id))
    link = await run_in_threadpool(share_links_store.get_active_share, uid, kind, item_id)
    if not link:
        return {"shared": False, "url": None, "token": None, "access": None}
    return _share_response(link)


@miniapp_router.post("/notes/{kind}/{item_id}/share")
async def miniapp_share_create(
    kind: str,
    item_id: str,
    body: Optional[_MiniappShareCreate] = None,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    uid = str(int(principal.telegram_user_id))
    if not await run_in_threadpool(_owner_can_share_item, uid, kind, item_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    access = (body.access if body else None)
    try:
        link = await run_in_threadpool(
            share_links_store.create_or_get_share, uid, kind, item_id, access
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _share_response(link)


@miniapp_router.delete("/notes/{kind}/{item_id}/share")
async def miniapp_share_revoke(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    uid = str(int(principal.telegram_user_id))
    revoked = await run_in_threadpool(share_links_store.revoke_share, uid, kind, item_id)
    return {"shared": False, "revoked": bool(revoked), "access": None}


@miniapp_router.get("/notes/{kind}/{item_id}/comments")
async def miniapp_share_comments_list(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    if not await run_in_threadpool(_owner_can_share_item, uid, kind, item_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    rows = await run_in_threadpool(share_comments_store.list_comments, uid, kind, item_id)
    return {"comments": [_comment_api(r, viewer_uid=uid) for r in rows]}


@miniapp_router.post("/notes/{kind}/{item_id}/comments")
async def miniapp_share_comments_create(
    kind: str,
    item_id: str,
    body: _MiniappShareCommentCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    if not await run_in_threadpool(_owner_can_share_item, uid, kind, item_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    author_id, author_name, author_username = _comment_author_from_principal(principal)
    try:
        row = await run_in_threadpool(
            share_comments_store.add_comment,
            uid,
            kind,
            item_id,
            author_user_id=author_id,
            author_name=author_name,
            author_username=author_username,
            body=body.body,
            quote=body.quote,
            prefix=body.prefix,
            suffix=body.suffix,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "comment": _comment_api(row, viewer_uid=uid)}


@miniapp_router.delete("/notes/{kind}/{item_id}/comments/{comment_id}")
async def miniapp_share_comments_delete(
    kind: str,
    item_id: str,
    comment_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    if not await run_in_threadpool(_owner_can_share_item, uid, kind, item_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    row = await run_in_threadpool(share_comments_store.get_comment, comment_id)
    if (
        not row
        or str(row.get("owner_user_id") or "") != uid
        or str(row.get("item_kind") or "") != kind
        or str(row.get("item_id") or "") != str(item_id)
    ):
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    deleted = await run_in_threadpool(
        share_comments_store.delete_comment, comment_id, requester_user_id=uid
    )
    if not deleted:
        raise HTTPException(status_code=403, detail="Нельзя удалить этот комментарий")
    return {"ok": True}


@miniapp_router.post("/notes/{kind}/{item_id}/paei")
async def miniapp_note_paei_start(
    kind: str,
    item_id: str,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.board.note_paei import begin_job, run_note_paei_job

    uid = str(int(principal.telegram_user_id))
    if not await run_in_threadpool(_owner_can_share_item, uid, kind, item_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    job, started = begin_job(uid, kind, item_id)
    if started:
        background_tasks.add_task(
            run_note_paei_job, int(principal.telegram_user_id), kind, item_id
        )
    return {
        "ok": True,
        "status": str(job.get("status") or "running"),
        "comment_id": job.get("comment_id"),
        "error": job.get("error"),
    }


@miniapp_router.get("/notes/{kind}/{item_id}/paei")
async def miniapp_note_paei_status(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.board.note_paei import get_job

    uid = str(int(principal.telegram_user_id))
    if not await run_in_threadpool(_owner_can_share_item, uid, kind, item_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    job = get_job(uid, kind, item_id)
    if not job:
        return {"ok": True, "status": "idle"}
    return {
        "ok": True,
        "status": str(job.get("status") or "idle"),
        "comment_id": job.get("comment_id"),
        "error": job.get("error"),
        "meeting_id": job.get("meeting_id"),
    }


@miniapp_router.post("/notes/todoist")
async def miniapp_todoist_note_create(
    body: _MiniappTodoistCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.integrations import todoist_api
    from assistant.stores import notes as notes_store

    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Укажите заголовок заметки")
    desc = (body.description or "").strip()
    uid = int(principal.telegram_user_id)
    tid: str | None = None
    if body.sync_todoist:

        def _todoist() -> str:
            return add_todoist_note(title, desc, telegram_user_id=uid)

        try:
            tid = await run_in_threadpool(_todoist)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    def _local() -> dict[str, Any]:
        return notes_store.create_note(uid, title, desc, todoist_id=tid)

    item = await run_in_threadpool(_local)
    return {
        "ok": True,
        "id": tid or str(item["id"]),
        "item": {
            "id": tid or str(item["id"]),
            "local_id": item["id"],
            "title": title,
            "content": title,
            "description": desc,
        },
    }


@miniapp_router.patch("/notes/todoist/{task_id}")
async def miniapp_todoist_note_patch(
    task_id: str,
    body: _MiniappTodoistPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    if body.title is None and body.description is None:
        raise HTTPException(status_code=400, detail="Укажите title и/или description")
    from assistant.compat.miniapp_shims import set_todoist_task_content

    tid = (task_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="Пустой task_id")

    def _run() -> None:
        set_todoist_task_content(
            tid,
            content=body.title,
            description=body.description,
            telegram_user_id=int(principal.telegram_user_id),
        )

    try:
        await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@miniapp_router.delete("/notes/todoist/{task_id}")
async def miniapp_todoist_note_delete(
    task_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import delete_todoist_task

    tid = (task_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="Пустой task_id")

    def _run() -> None:
        delete_todoist_task(tid, telegram_user_id=int(principal.telegram_user_id))

    try:
        await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@miniapp_router.get("/billing")
async def miniapp_billing(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        __import__(
            "assistant.lib.billing_store", fromlist=["billing_snapshot_for_api"]
        ).billing_snapshot_for_api,
        int(principal.telegram_user_id),
    )


@miniapp_router.post("/billing/redeem-promo")
async def miniapp_billing_redeem_promo(
    body: _MiniappBillingPromoBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    ok, msg = await run_in_threadpool(
        __import__(
            "assistant.lib.billing_store", fromlist=["apply_promo_code"]
        ).apply_promo_code,
        int(principal.telegram_user_id),
        body.code,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    snap = await run_in_threadpool(
        __import__(
            "assistant.lib.billing_store", fromlist=["billing_snapshot_for_api"]
        ).billing_snapshot_for_api,
        int(principal.telegram_user_id),
    )
    return {"ok": True, "message": msg, **snap}


@miniapp_router.post("/oauth/google/start")
async def miniapp_oauth_google_start(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.google_calendar_oauth import build_authorization_url, register_oauth_state

    try:
        state = register_oauth_state(int(principal.telegram_user_id))
        return {"url": build_authorization_url(state)}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@miniapp_router.post("/oauth/todoist/start")
async def miniapp_oauth_todoist_start(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.todoist_oauth import build_authorization_url, register_oauth_state

    state = register_oauth_state(int(principal.telegram_user_id))
    return {"url": build_authorization_url(state)}


@miniapp_router.post("/oauth/zoom/start")
async def miniapp_oauth_zoom_start(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.zoom_oauth import build_authorization_url, register_oauth_state

    state = register_oauth_state(int(principal.telegram_user_id))
    return {"url": build_authorization_url(state)}


@miniapp_router.post("/oauth/telemost/start")
async def miniapp_oauth_telemost_start(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.telemost_oauth import (
        build_authorization_url,
        register_oauth_state,
        uses_verification_code_flow,
        verification_code_instructions,
    )

    try:
        state = register_oauth_state(int(principal.telegram_user_id))
        out: dict[str, Any] = {
            "url": build_authorization_url(state),
        }
        if uses_verification_code_flow():
            out["verification_code_flow"] = True
            out["instructions"] = verification_code_instructions()
        return out
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@miniapp_router.post("/oauth/telemost/code")
async def miniapp_oauth_telemost_code(
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.telemost_oauth import exchange_verification_code

    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Не указан code")
    try:
        exchange_verification_code(code, int(principal.telegram_user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"connected": True}


@miniapp_router.post("/oauth/telemost/disconnect")
async def miniapp_oauth_telemost_disconnect(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.telemost_oauth import disconnect_user

    return {"disconnected": disconnect_user(int(principal.telegram_user_id))}


@miniapp_router.get("/telemost/status")
async def miniapp_telemost_status(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.integrations.telemost_oauth import org_likely, read_token_store

    tid = int(principal.telegram_user_id)
    store = read_token_store(tid)
    if not store:
        return {"connected": False}
    return {
        "connected": True,
        "telemost_email": str(store.get("yandex_email") or ""),
        "telemost_display_name": str(store.get("yandex_display_name") or ""),
        "telemost_org_likely": org_likely(tid),
        "mail_scope_granted": "mail:imap_ro" in str(store.get("scope") or "").split(),
    }


@miniapp_router.post("/oauth/yandex-disk/start")
async def miniapp_oauth_yandex_disk_start(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.yandex_disk_oauth import (
        build_authorization_url,
        register_oauth_state,
        uses_verification_code_flow,
        verification_code_instructions,
    )

    try:
        state = register_oauth_state(int(principal.telegram_user_id))
        out: dict[str, Any] = {"url": build_authorization_url(state)}
        if uses_verification_code_flow():
            out["verification_code_flow"] = True
            out["instructions"] = verification_code_instructions()
        return out
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@miniapp_router.post("/oauth/yandex-disk/code")
async def miniapp_oauth_yandex_disk_code(
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.yandex_disk_oauth import exchange_verification_code

    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Не указан code")
    try:
        exchange_verification_code(code, int(principal.telegram_user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"connected": True}


@miniapp_router.post("/oauth/google/disconnect")
async def miniapp_oauth_google_disconnect(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.google_calendar_oauth import remove_user_token

    return {"disconnected": remove_user_token(int(principal.telegram_user_id))}


@miniapp_router.post("/oauth/todoist/disconnect")
async def miniapp_oauth_todoist_disconnect(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.todoist_oauth import remove_user_token

    return {"disconnected": remove_user_token(int(principal.telegram_user_id))}


@miniapp_router.post("/oauth/zoom/disconnect")
async def miniapp_oauth_zoom_disconnect(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.zoom_oauth import disconnect_user

    return {"disconnected": disconnect_user(int(principal.telegram_user_id))}


@miniapp_router.post("/oauth/yandex-disk/disconnect")
async def miniapp_oauth_yandex_disk_disconnect(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.yandex_disk_oauth import disconnect_user

    return {"disconnected": disconnect_user(int(principal.telegram_user_id))}


@miniapp_router.post("/bitrix-mcp/connect")
async def miniapp_bitrix_mcp_connect(
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.bitrix_mcp_client import validate_token_detail
    from assistant.integrations.bitrix_mcp_token import save_user_token

    token = str(body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Не указан токен")
    try:
        await validate_token_detail(token)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    try:
        save_user_token(int(principal.telegram_user_id), token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"connected": True}


@miniapp_router.post("/bitrix-mcp/disconnect")
async def miniapp_bitrix_mcp_disconnect(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.integrations.bitrix_mcp_token import remove_user_token

    return {"disconnected": remove_user_token(int(principal.telegram_user_id))}


def _kb_public_item(kb: dict[str, Any]) -> dict[str, Any]:
    meta = kb.get("source_meta") if isinstance(kb.get("source_meta"), dict) else {}
    safe_meta = {k: v for k, v in meta.items() if k != "notion_token"}
    return {
        "id": kb.get("id"),
        "title": kb.get("title"),
        "source_type": kb.get("source_type"),
        "source_url": kb.get("source_url"),
        "source_meta": safe_meta,
        "role": kb.get("role"),
        "owner_telegram_user_id": kb.get("owner_telegram_user_id"),
        "last_sync_at": kb.get("last_sync_at"),
        "sync_status": kb.get("sync_status"),
        "sync_error": kb.get("sync_error"),
        "chunk_count": kb.get("chunk_count"),
    }


@miniapp_router.get("/knowledge-bases")
async def miniapp_knowledge_bases_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import knowledge_base_store as kb_store

    items = [_kb_public_item(kb) for kb in kb_store.list_knowledge_bases_for_user(int(principal.telegram_user_id))]
    return {"items": items}


@miniapp_router.post("/knowledge-bases")
async def miniapp_knowledge_bases_create(
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    import asyncio

    from assistant.services import knowledge_sync
    from assistant.stores import knowledge_base_store as kb_store

    tid = int(principal.telegram_user_id)
    title = str(body.get("title") or "").strip()
    url = str(body.get("source_url") or body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Укажите ссылку на базу знаний")
    source_type = str(body.get("source_type") or "").strip().lower()
    if not source_type:
        detected = kb_store.detect_source_type(url)
        if not detected:
            raise HTTPException(
                status_code=400,
                detail="Не удалось определить тип ссылки (Google Docs, Notion, Яндекс Диск, Битрикс24)",
            )
        source_type = detected
    meta: dict[str, Any] = {}
    notion_token = str(body.get("notion_token") or "").strip()
    if source_type == "notion":
        if not notion_token:
            raise HTTPException(status_code=400, detail="Для Notion нужен Integration Token")
        meta["notion_token"] = notion_token
    kb = kb_store.create_knowledge_base(
        owner_telegram_user_id=tid,
        title=title or "База знаний",
        source_type=source_type,  # type: ignore[arg-type]
        source_url=url,
        source_meta=meta,
    )
    try:
        kb = await asyncio.to_thread(knowledge_sync.sync_knowledge_base, str(kb["id"]))
    except Exception as e:
        kb = kb_store.get_knowledge_base(str(kb["id"])) or kb
        kb = dict(kb)
        kb["sync_error"] = str(e)
    return {"item": _kb_public_item(kb)}


@miniapp_router.delete("/knowledge-bases/{kb_id}")
async def miniapp_knowledge_bases_delete(
    kb_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import knowledge_base_store as kb_store

    ok = kb_store.delete_knowledge_base(kb_id, owner_telegram_user_id=int(principal.telegram_user_id))
    if not ok:
        raise HTTPException(status_code=403, detail="Нет прав удалить эту базу знаний")
    return {"deleted": True}


@miniapp_router.post("/knowledge-bases/{kb_id}/sync")
async def miniapp_knowledge_bases_sync(
    kb_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    import asyncio

    from assistant.services import knowledge_sync
    from assistant.stores import knowledge_base_store as kb_store

    tid = int(principal.telegram_user_id)
    if not kb_store.user_can_access_kb(tid, kb_id):
        raise HTTPException(status_code=403, detail="Нет доступа к базе знаний")
    if not kb_store.user_is_kb_owner(tid, kb_id):
        raise HTTPException(status_code=403, detail="Синхронизацию может запускать только владелец")
    try:
        kb = await asyncio.to_thread(knowledge_sync.sync_knowledge_base, kb_id)
    except Exception as e:
        kb = kb_store.get_knowledge_base(kb_id)
        if not kb:
            raise HTTPException(status_code=404, detail="База знаний не найдена") from e
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"item": _kb_public_item(kb)}


@miniapp_router.get("/knowledge-bases/{kb_id}/members")
async def miniapp_knowledge_bases_members(
    kb_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import knowledge_base_store as kb_store, telegram_registry

    tid = int(principal.telegram_user_id)
    if not kb_store.user_is_kb_owner(tid, kb_id):
        raise HTTPException(status_code=403, detail="Только владелец может видеть список участников")
    members = []
    for m in kb_store.list_kb_members(kb_id):
        uid = int(m["telegram_user_id"])
        members.append(
            {
                **m,
                "username": telegram_registry.lookup_username(uid),
            }
        )
    return {"members": members}


@miniapp_router.post("/knowledge-bases/{kb_id}/members")
async def miniapp_knowledge_bases_member_add(
    kb_id: str,
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import knowledge_base_store as kb_store, telegram_registry

    tid = int(principal.telegram_user_id)
    username = str(body.get("username") or body.get("telegram_username") or "").strip()
    member_id = body.get("telegram_user_id")
    uid: int | None = None
    if member_id is not None:
        try:
            uid = int(member_id)
        except (TypeError, ValueError):
            uid = None
    if uid is None:
        uid = telegram_registry.lookup_user_id(username)
    if not uid:
        raise HTTPException(status_code=400, detail="Пользователь не найден. Он должен хотя бы раз написать боту.")
    if not kb_store.add_kb_member(kb_id, member_telegram_user_id=uid, granted_by=tid):
        raise HTTPException(status_code=403, detail="Нет прав выдать доступ")
    return {
        "member": {
            "telegram_user_id": uid,
            "username": telegram_registry.lookup_username(uid),
            "role": "member",
        }
    }


@miniapp_router.delete("/knowledge-bases/{kb_id}/members/{member_id}")
async def miniapp_knowledge_bases_member_remove(
    kb_id: str,
    member_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import knowledge_base_store as kb_store

    tid = int(principal.telegram_user_id)
    ok = kb_store.remove_kb_member(
        kb_id,
        member_telegram_user_id=int(member_id),
        revoked_by=tid,
    )
    if not ok:
        raise HTTPException(status_code=403, detail="Не удалось отозвать доступ")
    return {"removed": True}


@miniapp_router.get("/zoom/status")
async def miniapp_zoom_status(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    import json

    from assistant.integrations.zoom_oauth import user_token_path

    tid = int(principal.telegram_user_id)
    path = user_token_path(tid)
    if not path.is_file():
        return {"connected": False}
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        store = {}
    if not isinstance(store, dict):
        store = {}
    return {
        "connected": True,
        "zoom_email": str(store.get("zoom_email") or ""),
        "zoom_display_name": str(store.get("zoom_display_name") or ""),
    }


@miniapp_router.get("/zoom/recordings")
async def miniapp_zoom_recordings(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import meeting_recordings_store as mrs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        items = []
        for row in mrs.list_jobs_for_user(uid, limit=50):
            items.append(
                {
                    "id": row.get("id"),
                    "topic": row.get("topic") or "Встреча Zoom",
                    "status": row.get("status") or "",
                    "meeting_url": row.get("meeting_url") or "",
                    "start_at_utc": row.get("start_at_utc"),
                    "created_at_utc": row.get("created_at_utc"),
                    "journal_event_id": row.get("journal_event_id"),
                    "error_message": row.get("error_message"),
                }
            )
        return {"items": items}

    return await run_in_threadpool(_run)


app.include_router(miniapp_router)


@app.get("/", include_in_schema=False)
async def landing_index_route() -> FileResponse:
    return FileResponse(_landing_page_path, media_type="text/html")


app.include_router(dash)


@app.get(_USAGE_DASHBOARD_PREFIX, include_in_schema=False)
async def _dashboard_no_trailing_slash() -> RedirectResponse:
    return RedirectResponse(url=_dash_url("/"), status_code=307)


class _WebappStaticFiles(StaticFiles):
    """HTML/SW без кэша; версионированные ассеты — долгий immutable-кэш."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith(".html") or path in ("", "/") or path.endswith("sw.js"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        elif path.endswith(".webmanifest"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.endswith((".js", ".css", ".png", ".svg", ".ico", ".woff", ".woff2", ".jpg", ".jpeg", ".webp")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class _PublicHtmlStaticFiles(StaticFiles):
    """Публичные HTML-страницы без авторизации и без агрессивного кэша."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith(".html") or path in ("", "/", "."):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        elif path.endswith((".css", ".js", ".png", ".svg", ".ico", ".jpg", ".jpeg", ".webp")):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response


_SHARE_PAGE_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


@app.get("/api/public/share/{token}")
async def public_share_json(token: str) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    link = await run_in_threadpool(share_links_store.resolve_share, token)
    if not link:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    payload = await run_in_threadpool(_public_share_payload, link)
    if not payload:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    return payload


async def _resolve_public_share_or_404(token: str) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    link = await run_in_threadpool(share_links_store.resolve_share, token)
    if not link:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    payload = await run_in_threadpool(_public_share_payload, link)
    if not payload:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    return link


@app.get("/api/public/share/{token}/comments")
async def public_share_comments_list(token: str, request: Request) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    link = await _resolve_public_share_or_404(token)
    if not share_comments_store.comments_allowed_for_link(link):
        raise HTTPException(status_code=403, detail="Комментарии недоступны")
    rows = await run_in_threadpool(
        share_comments_store.list_comments,
        link["user_id"],
        link["item_kind"],
        link["item_id"],
    )
    viewer = _optional_share_viewer(request)
    viewer_uid = str(int(viewer.telegram_user_id)) if viewer else None
    return {"comments": [_comment_api(r, viewer_uid=viewer_uid) for r in rows]}


@app.get("/api/public/share/{token}/me")
async def public_share_me(token: str, request: Request) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    link = await _resolve_public_share_or_404(token)
    if not share_comments_store.comments_allowed_for_link(link):
        raise HTTPException(status_code=403, detail="Комментарии недоступны")
    return {"user": _share_viewer_public(_optional_share_viewer(request))}


@app.post("/api/public/share/{token}/comments")
async def public_share_comments_create(
    token: str,
    body: _MiniappShareCommentCreate,
    principal: _MiniappPrincipal = Depends(require_share_commenter),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    link = await _resolve_public_share_or_404(token)
    if not share_comments_store.comments_allowed_for_link(link):
        raise HTTPException(status_code=403, detail="Комментарии недоступны")
    author_id, author_name, author_username = _comment_author_from_principal(principal)
    try:
        row = await run_in_threadpool(
            share_comments_store.add_comment,
            link["user_id"],
            link["item_kind"],
            link["item_id"],
            author_user_id=author_id,
            author_name=author_name,
            author_username=author_username,
            body=body.body,
            quote=body.quote,
            prefix=body.prefix,
            suffix=body.suffix,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "comment": _comment_api(row, viewer_uid=author_id)}


@app.delete("/api/public/share/{token}/comments/{comment_id}")
async def public_share_comments_delete(
    token: str,
    comment_id: int,
    principal: _MiniappPrincipal = Depends(require_share_commenter),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    link = await _resolve_public_share_or_404(token)
    if not share_comments_store.comments_allowed_for_link(link):
        raise HTTPException(status_code=403, detail="Комментарии недоступны")
    row = await run_in_threadpool(share_comments_store.get_comment, comment_id)
    if (
        not row
        or str(row.get("owner_user_id") or "") != str(link.get("user_id") or "")
        or str(row.get("item_kind") or "") != str(link.get("item_kind") or "")
        or str(row.get("item_id") or "") != str(link.get("item_id") or "")
    ):
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    uid = str(int(principal.telegram_user_id))
    deleted = await run_in_threadpool(
        share_comments_store.delete_comment, comment_id, requester_user_id=uid
    )
    if not deleted:
        raise HTTPException(status_code=403, detail="Нельзя удалить этот комментарий")
    return {"ok": True}


@app.get("/share/{token}")
async def public_share_page(token: str) -> HTMLResponse:
    from assistant.stores import share_links as share_links_store

    share_html_path = Path(__file__).resolve().parent / "webapp" / "share.html"
    if not share_html_path.is_file():
        raise HTTPException(status_code=404, detail="Страница недоступна")
    link = await run_in_threadpool(share_links_store.resolve_share, token)
    payload = await run_in_threadpool(_public_share_payload, link) if link else None
    if not payload:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    html = share_html_path.read_text(encoding="utf-8")
    html = html.replace("{{TITLE}}", html_lib.escape(str(payload.get("title") or "Документ")))
    html = html.replace("{{LABEL}}", html_lib.escape(str(payload.get("label") or "Документ")))
    return HTMLResponse(content=html, headers=dict(_SHARE_PAGE_NO_CACHE))


_news_static_dir = Path(__file__).resolve().parent / "news"
if _news_static_dir.is_dir():

    @app.api_route("/news", methods=["GET", "HEAD"], include_in_schema=False)
    async def news_redirect_to_slash(request: Request) -> RedirectResponse:
        qs = request.url.query
        target = "/news/" + (f"?{qs}" if qs else "")
        return RedirectResponse(url=target, status_code=307)

    app.mount(
        "/news",
        _PublicHtmlStaticFiles(directory=str(_news_static_dir), html=True),
        name="news_static",
    )


_webapp_static_dir = Path(__file__).resolve().parent / "webapp"
if _webapp_static_dir.is_dir():
    _webapp_index_path = _webapp_static_dir / "index.html"

    @app.get("/webapp", include_in_schema=False)
    async def webapp_redirect_to_slash(request: Request) -> RedirectResponse:
        """Без завершающего / относительные app.js/styles.css резолвятся в /app.js → 404."""
        qs = request.url.query
        target = "/webapp/" + (f"?{qs}" if qs else "")
        return RedirectResponse(url=target, status_code=307)

    @app.get("/webapp/", include_in_schema=False)
    async def webapp_index_route() -> FileResponse:
        return FileResponse(
            _webapp_index_path,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    app.mount(
        "/webapp",
        _WebappStaticFiles(directory=str(_webapp_static_dir), html=True),
        name="webapp_static",
    )

if _docs_static_dir.is_dir():
    app.mount(
        "/docs",
        StaticFiles(directory=str(_docs_static_dir), html=True),
        name="docs_static",
    )
