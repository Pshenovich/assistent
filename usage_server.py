#!/usr/bin/env python3
"""Веб-дашборд расхода OpenRouter: агрегаты по дням (UTC) и детализация по операциям."""

from __future__ import annotations

import asyncio
import html as html_lib
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import secrets
import time
from functools import partial
from pathlib import Path
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote, unquote

import requests
from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from assistant.lib.sentry_init import init_sentry  # noqa: E402

init_sentry("web")

from assistant.stores import reminders_store  # noqa: E402
from assistant.services.task_google_sync import (  # noqa: E402
    clear_task_google_ids as _clear_task_google_ids,
    sync_task_google as _sync_task_google,
    task_owner_uid as _task_owner_uid,
)
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

# Swagger/ReDoc/OpenAPI раскрывают карту всех маршрутов — только для локальной разработки.
_API_DOCS = os.getenv("USAGE_API_DOCS", "").strip().lower() in ("1", "true", "yes", "on")
app = FastAPI(
    title="OpenRouter usage",
    docs_url="/docs" if _API_DOCS else None,
    redoc_url="/redoc" if _API_DOCS else None,
    openapi_url="/openapi.json" if _API_DOCS else None,
)


@app.on_event("startup")
async def _bind_realtime_hub() -> None:
    from assistant.realtime.hub import HUB

    HUB.bind_loop(asyncio.get_running_loop())


@app.on_event("startup")
def _recover_note_paei_jobs() -> None:
    try:
        from assistant.board.note_paei import recover_jobs_after_restart

        recover_jobs_after_restart()
    except Exception as exc:
        print(f"[board.paei] recover_fail err={exc!r}")
    try:
        from assistant.board.note_research import recover_jobs_after_restart as recover_research

        recover_research()
    except Exception as exc:
        print(f"[board.research] recover_fail err={exc!r}")


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
        "img-src 'self' data: blob: https:; connect-src 'self' https: wss:; "
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
        t0 = time.perf_counter()
        response = await call_next(request)
        _log_slow_request(request, response, time.perf_counter() - t0)
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


def _slow_request_sec() -> float:
    try:
        return float(os.getenv("SLOW_REQUEST_LOG_SEC", "1.5") or "1.5")
    except ValueError:
        return 1.5


def _log_slow_request(request: Request, response: Any, dur: float) -> None:
    """Медленные запросы API — в журнал: путь, время и занятость пула потоков (ждут ли запросы очереди)."""
    if dur < _slow_request_sec() or not request.url.path.startswith("/api/"):
        return
    try:
        import anyio.to_thread

        lim = anyio.to_thread.current_default_thread_limiter()
        pool = f"{int(lim.borrowed_tokens)}/{int(lim.total_tokens)}"
    except Exception:
        pool = "?"
    print(
        f"[slow] {request.method} {request.url.path} {getattr(response, 'status_code', '?')} "
        f"{dur:.2f}s threads={pool}",
        flush=True,
    )


app.add_middleware(_OwaspSecurityHeadersMiddleware)

# Пульс бота старше этого — бот считается вставшим (он пишет раз в 60 с).
_HEALTH_BOT_STALE_SEC = 300.0


def _health_snapshot() -> dict[str, Any]:
    import sqlite3

    from assistant.lib import heartbeat
    from assistant.stores import notes as notes_store

    db_ok = False
    try:
        # Своё короткое соединение: общий _CONN хранилища занят под его замком.
        # mode=rw — не создавать пустой файл, если базы нет.
        conn = sqlite3.connect(f"file:{notes_store._db_path()}?mode=rw", uri=True, timeout=5)
        try:
            conn.execute("SELECT 1").fetchone()
            db_ok = True
        finally:
            conn.close()
    except Exception as e:
        print(f"[healthz] db failed err={e!r}", flush=True)
    bot_age = heartbeat.age_sec("bot")
    # Нет пульса вообще — бот здесь не запущен (локальная разработка), это не авария.
    bot_ok = bot_age is None or bot_age < _HEALTH_BOT_STALE_SEC
    return {
        "ok": db_ok and bot_ok,
        "web": True,
        "db": db_ok,
        "bot_heartbeat_age_sec": None if bot_age is None else round(bot_age, 1),
    }


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    snap = await run_in_threadpool(_health_snapshot)
    return JSONResponse(
        snap,
        status_code=200 if snap["ok"] else 503,
        headers={"Cache-Control": "no-store"},
    )

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


def _fmt_int(n: Any) -> str:
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        v = 0
    return f"{v:,}".replace(",", " ")


def _dash_nav(request: Request, active: str) -> str:
    qs = _access_qs(request)
    items = (
        ("users", "Пользователи", "/"),
        ("days", "По дням", "/days"),
        ("board", "PAIE", "/board"),
    )
    links: list[str] = []
    for key, label, path in items:
        href = html_lib.escape(_dash_url(path) + qs, quote=True)
        cls = ' class="active"' if key == active else ""
        links.append(f'<a href="{href}"{cls}>{html_lib.escape(label)}</a>')
    return '<nav class="dash-nav">' + "".join(links) + "</nav>"


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
        print(f"[oauth] google_callback err={e!r}")
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
        print(f"[oauth] todoist_callback err={e!r}")
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
        print(f"[oauth] yandex_disk_callback err={e!r}")
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
        print(f"[oauth] telemost_callback err={e!r}")
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
        print(f"[oauth] zoom_callback err={e!r}")
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
        # Только тип: сообщение CalDAV может содержать Apple ID пользователя.
        print(f"[apple_reminders] setup_failed err={type(e).__name__}")
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


def _forwarded_client_ips(request: Request) -> list[str]:
    """Все IP из заголовков reverse-proxy (X-Forwarded-For целиком и X-Real-IP).

    Начало X-Forwarded-For клиент может прислать сам, nginx лишь дописывает в конец.
    """
    ips = [p.strip() for p in (request.headers.get("X-Forwarded-For") or "").split(",")]
    ips.append((request.headers.get("X-Real-IP") or "").strip())
    return [ip for ip in ips if ip]


def _is_local_dashboard_request(request: Request) -> bool:
    """Локальный доступ без токена: прямой loopback, не публичный nginx-прокси.

    Заголовки Host и X-Forwarded-For задаёт клиент, поэтому их loopback-значения
    сами по себе доступ не дают.
    """
    if os.getenv("USAGE_DASHBOARD_FORCE_AUTH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    ):
        return False
    client = request.client
    if client is None or not _is_loopback_addr(client.host or ""):
        return False
    return all(_is_loopback_addr(ip) for ip in _forwarded_client_ips(request))


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


def _html_page_start(
    title: str, request: Request | None = None, active: str = ""
) -> str:
    t = html_lib.escape(title)
    nav = _dash_nav(request, active) if request is not None else ""
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
.dash-nav {{
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1.5rem;
}}
.dash-nav a {{
  padding: 0.4rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font-weight: 600;
  font-size: 0.85rem;
  text-decoration: none;
}}
.dash-nav a.active,
.dash-nav a:hover {{
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
  text-decoration: none;
}}
</style>
</head>
<body>
<div class="app">
{nav}
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


@dash.get("/days", response_class=HTMLResponse)
async def days_page(request: Request) -> HTMLResponse:
    _require_token(request)
    rate = _usd_to_rub()
    rows = daily_summary()
    qs = _access_qs(request)
    total_usd = sum(float(r.get("cost_usd") or 0) for r in rows)
    total_calls = sum(int(r.get("n_calls") or 0) for r in rows)
    total_tok = sum(int(r.get("total_tokens") or 0) for r in rows)
    total_cached = sum(int(r.get("cached_tokens") or 0) for r in rows)
    total_rub = total_usd * rate

    parts: list[str] = [
        _html_page_start("OpenRouter — по дням", request, "days"),
        '<header class="header">',
        "<h1>Расход OpenRouter по дням</h1>",
        '<p class="subtitle">Агрегаты по календарным дням в UTC. Стоимость в рублях: '
        f"<strong>{html_lib.escape(str(rate))}</strong> ₽ за 1 USD (<code>USD_TO_RUB</code>).</p>",
        '<span class="pill">Локальный журнал бота</span>',
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{_fmt_int(total_tok)}</div></div>',
        f'<div class="metric"><div class="label">Кэш prompt</div><div class="value">{_fmt_int(total_cached)}</div></div>',
        f'<div class="metric"><div class="label">Сумма USD</div><div class="value">{total_usd:.4f}</div></div>',
        f'<div class="metric"><div class="label">Сумма ₽</div><div class="value">{total_rub:.2f} <small>≈</small></div></div>',
        "</section>",
        '<section class="card">',
        '<div class="table-wrap">',
        "<table><thead><tr>",
        "<th>Дата (UTC)</th>",
        '<th class="num">Вызовов</th>',
        '<th class="num">Prompt</th>',
        '<th class="num">Кэш</th>',
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
            f'<td class="num">{_fmt_int(r.get("prompt_tokens"))}</td>'
            f'<td class="num">{_fmt_int(r.get("cached_tokens"))}</td>'
            f'<td class="num">{_fmt_int(r.get("completion_tokens"))}</td>'
            f'<td class="num">{_fmt_int(r.get("total_tokens"))}</td>'
            f'<td class="num">{usd:.6f}</td>'
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
    total_cached = int(totals.get("cached_tokens") or 0)
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
        _html_page_start(f"Пользователи — {day_d}", request, "days"),
        f'<a class="back" href="{_dash_url("/days")}{qs}">← Сводка по дням</a>',
        '<header class="header">',
        f"<h1>{day_esc}</h1>",
        '<p class="subtitle">Расход по пользователям за один день (UTC). '
        f'<a href="{ops_link}" style="color:var(--accent);">Все операции за день</a></p>',
        f'<span class="pill">За день: {total_rub:.2f} ₽</span>',
        filter_note,
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{_fmt_int(total_tok)}</div></div>',
        f'<div class="metric"><div class="label">Кэш prompt</div><div class="value">{_fmt_int(total_cached)}</div></div>',
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
        '<th class="num">Кэш</th>',
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
        user_link = (
            f"{_dash_url('/user/' + quote(uid) + '/day/' + quote(day_d))}?{user_day_qs}"
        )
        parts.append(
            "<tr>"
            f'<td><a href="{user_link}">{_html_escape(label)}</a></td>'
            f'<td class="num">{int(u.get("n_calls") or 0)}</td>'
            f'<td class="num">{_fmt_int(u.get("total_tokens"))}</td>'
            f'<td class="num">{_fmt_int(u.get("cached_tokens"))}</td>'
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
        _html_page_start(f"Операции — {day_d}", request, "days"),
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
        '<th class="num">Кэш</th>',
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
            f'<td class="num">{_fmt_int(e.get("prompt_tokens"))}</td>'
            f'<td class="num">{_fmt_int(e.get("cached_tokens"))}</td>'
            f'<td class="num">{_fmt_int(e.get("completion_tokens"))}</td>'
            f'<td class="num">{_fmt_int(e.get("total_tokens"))}</td>'
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
        '<th class="num">Кэш</th>',
        '<th class="num">USD</th>',
        '<th class="num">₽</th>',
        "</tr></thead><tbody>",
    ]
    for r in rows:
        op = str(r.get("operation") or "").strip()
        usd = float(r.get("cost_usd") or 0)
        rub = usd * rate
        lines.append(
            "<tr>"
            f"<td>{_dash_op_cell(op)}</td>"
            f'<td class="num">{int(r.get("n_calls") or 0)}</td>'
            f'<td class="num">{_fmt_int(r.get("total_tokens"))}</td>'
            f'<td class="num">{_fmt_int(r.get("cached_tokens"))}</td>'
            f'<td class="num">{usd:.6f}</td>'
            f'<td class="num">{rub:.2f}</td>'
            "</tr>"
        )
    lines.append("</tbody></table></div></section>")
    return "".join(lines)


@dash.get("/", response_class=HTMLResponse)
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
    total_cached = int(totals.get("cached_tokens") or 0)
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
        _html_page_start("Пользователи — расход OpenRouter", request, "users"),
        '<header class="header">',
        "<h1>Расход OpenRouter по пользователям</h1>",
        '<p class="subtitle">Период в UTC (по умолчанию последние '
        f"{_DASH_DEFAULT_USER_RANGE_DAYS + 1} дней). В таблице только те, кто дал хотя бы один вызов "
        "GPT/транскрипции (задачи в Битрикс без ИИ сюда не попадают). Расход в ₽ через "
        "<code>USD_TO_RUB</code>. Колонка «Кэш» — prompt tokens, прочитанные из prompt cache.</p>",
        '<span class="pill">Сводка</span>',
        filter_note,
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{_fmt_int(total_tok)}</div></div>',
        f'<div class="metric"><div class="label">Кэш prompt</div><div class="value">{_fmt_int(total_cached)}</div></div>',
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
        '<th class="num">Кэш prompt</th>',
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
        parts.append(
            "<tr>"
            f'<td><a href="{_dash_url("/user/" + quote(uid))}{range_link_q}">{_html_escape(label)}</a></td>'
            f'<td class="num">{int(u.get("n_calls") or 0)}</td>'
            f'<td class="num">{_fmt_int(u.get("total_tokens"))}</td>'
            f'<td class="num">{_fmt_int(u.get("cached_tokens"))}</td>'
            f'<td class="num">{cost_usd:.6f}</td>'
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
    total_cached = sum(int(d.get("cached_tokens") or 0) for d in days)
    total_usd = sum(float(d.get("cost_usd") or 0) for d in days)
    total_rub = total_usd * rate

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
        _html_page_start(f"Пользователь {display_label} — расход OpenRouter", request, "users"),
        f'<a class="back" href="{back_link}">← К пользователям</a>',
        '<header class="header">',
        f"<h1>Клиент { _html_escape(display_label) }</h1>",
        f'<p class="subtitle">Период в UTC: <code>{_html_escape(from_d)}</code>…<code>{_html_escape(to_d)}</code>.</p>',
        f'<span class="pill">Итого: {total_rub:.2f} ₽</span>',
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{_fmt_int(total_tok)}</div></div>',
        f'<div class="metric"><div class="label">Кэш prompt</div><div class="value">{_fmt_int(total_cached)}</div></div>',
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
        '<th class="num">Кэш</th>',
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
        usd = float(d.get("cost_usd") or 0)
        rub = usd * rate

        day_link = (
            f"{_dash_url('/user/' + quote(uid) + '/day/' + quote(date_utc))}?{day_qs}"
        )

        parts.append(
            "<tr>"
            f'<td><a href="{day_link}">{_html_escape(date_utc)}</a></td>'
            f'<td class="num">{n_calls}</td>'
            f'<td class="num">{_fmt_int(d.get("prompt_tokens"))}</td>'
            f'<td class="num">{_fmt_int(d.get("cached_tokens"))}</td>'
            f'<td class="num">{_fmt_int(d.get("completion_tokens"))}</td>'
            f'<td class="num">{_fmt_int(d.get("total_tokens"))}</td>'
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
            _html_page_start("Операции — вне периода", request, "users"),
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
    total_cached = sum(int(e.get("cached_tokens") or 0) for e in events)

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
        _html_page_start(f"Операции клиента {display_label} — {day_d}", request, "users"),
        f'<a class="back" href="{back_link}">← Клиент / дни</a>',
        '<header class="header">',
        f"<h1>Операции</h1>",
        f'<p class="subtitle">Клиент: <code>{_html_escape(display_label)}</code>. День (UTC): <code>{_html_escape(day_d)}</code>.</p>',
        f'<span class="pill">Итого: {total_rub:.2f} ₽</span>',
        "</header>",
        '<section class="metrics">',
        f'<div class="metric"><div class="label">Всего вызовов</div><div class="value">{total_calls}</div></div>',
        f'<div class="metric"><div class="label">Токенов всего</div><div class="value">{_fmt_int(total_tok)}</div></div>',
        f'<div class="metric"><div class="label">Кэш prompt</div><div class="value">{_fmt_int(total_cached)}</div></div>',
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
        '<th class="num">Кэш</th>',
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
        parts.append(
            "<tr>"
            f"<td>{ts}</td>"
            f"<td>{_dash_op_cell(op_raw)}</td>"
            f'<td><span class="op-badge" title="{md}">{md}</span></td>'
            f"<td><code>{gid}</code></td>"
            f'<td class="num">{_fmt_int(e.get("prompt_tokens"))}</td>'
            f'<td class="num">{_fmt_int(e.get("cached_tokens"))}</td>'
            f'<td class="num">{_fmt_int(e.get("completion_tokens"))}</td>'
            f'<td class="num">{_fmt_int(e.get("total_tokens"))}</td>'
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


def _decode_tma_init_data_token(scheme: str, token: str) -> str | None:
    """Достаёт raw initData из Authorization: tma|tma-b64 <…>."""
    raw = (token or "").strip()
    if not raw:
        return None
    if scheme == "tma-b64":
        import base64

        try:
            pad = "=" * (-len(raw) % 4)
            return base64.urlsafe_b64decode(raw + pad).decode("utf-8")
        except Exception:
            try:
                pad = "=" * (-len(raw) % 4)
                return base64.b64decode(raw + pad).decode("utf-8")
            except Exception:
                return None
    return raw


def _tma_init_data_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2:
        return None
    scheme = parts[0].lower()
    if scheme not in ("tma", "tma-b64"):
        return None
    return _decode_tma_init_data_token(scheme, parts[1])


def _tma_init_data_from_request(request: Request) -> str | None:
    """initData: Authorization tma/tma-b64, либо X-Telegram-Init-Data (без порчи UTF-8)."""
    raw = _tma_init_data_from_header(request.headers.get("Authorization"))
    if raw:
        return raw
    alt = (request.headers.get("X-Telegram-Init-Data") or "").strip()
    return alt or None


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


async def _finish_miniapp_principal(
    principal: _MiniappPrincipal, *, enforce_access: bool
) -> _MiniappPrincipal:
    try:
        await run_in_threadpool(_remember_principal_profile, principal)
    except Exception:
        pass
    return _enforce_miniapp_access(principal) if enforce_access else principal


async def _resolve_miniapp_principal(
    request: Request, *, enforce_access: bool
) -> _MiniappPrincipal:
    dev_principal = _miniapp_dev_principal_if_allowed(request)
    if dev_principal is not None:
        return await _finish_miniapp_principal(
            dev_principal, enforce_access=enforce_access
        )
    # Mini App в Telegram: initData важнее браузерной session-cookie.
    tma_error: str | None = None
    raw = _tma_init_data_from_request(request)
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
            # Устаревший/битый initData — пробуем cookie/session, не режем сразу.
            tma_error = str(e)
            print(f"[miniapp_auth] initData rejected: {tma_error!r}")
        else:
            principal = _MiniappPrincipal(uid, user)
            return await _finish_miniapp_principal(
                principal, enforce_access=enforce_access
            )
    session_principal = _principal_from_browser_session_request(request)
    if session_principal is not None:
        return await _finish_miniapp_principal(
            session_principal, enforce_access=enforce_access
        )
    if tma_error:
        raise HTTPException(status_code=401, detail=tma_error)
    raw = _tma_init_data_from_request(request)
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
    return await _finish_miniapp_principal(principal, enforce_access=enforce_access)


async def require_miniapp_user(request: Request) -> _MiniappPrincipal:
    principal = await _resolve_miniapp_principal(request, enforce_access=True)
    try:
        from assistant.stores import note_versions

        # Для истории версий: кто правит и правка ли это ИИ (заголовок ставит клиент).
        note_versions.set_edit_context(
            principal.telegram_user_id, request.headers.get("x-leo-edit-source") or ""
        )
    except Exception:
        pass
    return principal


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
    recurrence: Optional[str] = None  # RRULE («FREQ=DAILY»…); "" — без повтора


class _MiniappReminderCreate(BaseModel):
    task: str = ""
    when_iso: str = ""
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    recurrence: str = ""


_REMINDER_RRULE_RE = re.compile(r"^(?:RRULE:)?FREQ=(?:DAILY|WEEKLY|MONTHLY)(?:;[A-Z]+=[A-Z0-9,+-]+)*$")


def _clean_reminder_recurrence(raw: Optional[str]) -> Optional[str]:
    """Повтор напоминания из окна: простые RRULE (каждый день, по будням, раз в неделю)."""
    if raw is None:
        return None
    rule = str(raw).strip().upper()
    if not rule:
        return ""
    if not _REMINDER_RRULE_RE.match(rule):
        raise HTTPException(status_code=400, detail="Неизвестный повтор напоминания")
    return rule if rule.startswith("RRULE:") else "RRULE:" + rule


class _MiniappCalendarEventUpdate(BaseModel):
    calendar_id: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    attendees: Optional[list[str]] = None
    # None — не трогать; [] — без повтора; ["RRULE:…"] — правило повтора.
    recurrence: Optional[list[str]] = None


def _miniapp_event_recurrence(body: "_MiniappCalendarEventUpdate") -> Optional[list[str]]:
    if body.recurrence is None:
        return None
    from assistant.lib.recurrence import sanitize_client_recurrence

    try:
        return sanitize_client_recurrence(body.recurrence)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


class _MiniappCalendarTaskBody(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    result: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    assignee_user_id: Optional[str] = None
    assignee_email: Optional[str] = None
    assignee_name: Optional[str] = None
    checklist: Optional[list[dict[str, Any]]] = None
    note_id: Optional[str] = None
    done: Optional[bool] = None
    owner_archived: Optional[bool] = None
    all_day: Optional[bool] = None
    text: Optional[str] = None
    # ID пункта задач встречи (data-leo-item) в заметке note_id: задача ставится, только если пункт ничей
    meeting_item: Optional[str] = None


class _MiniappCalendarAvailabilityBody(BaseModel):
    date: str = ""
    attendees: list[Any] = Field(default_factory=list)


class _MiniappCalendarExcludedBody(BaseModel):
    excluded_ids: list[str] = []


class _MiniappCalendarRsvpBody(BaseModel):
    status: str = ""
    calendar_id: Optional[str] = None


class _MiniappBookingLinkCreate(BaseModel):
    title: Optional[str] = None
    attendees: list[Any] = Field(default_factory=list)
    duration_min: Optional[int] = None
    link_mode: str = "reusable"
    expire_days: Optional[int] = None


class _PublicBookBody(BaseModel):
    start: str = ""
    duration_min: Optional[int] = None
    guest_email: Optional[str] = None
    guest_emails: Optional[list[str]] = None


class _MiniappSettingsPatch(BaseModel):
    meeting_reminders_enabled: Optional[bool] = None
    task_reminders_enabled: Optional[bool] = None
    task_assigned_notify: Optional[bool] = None
    discussion_notifications_enabled: Optional[bool] = None
    discussion_notify_telegram: Optional[bool] = None
    discussion_notify_web_push: Optional[bool] = None
    zoom_auto_record_enabled: Optional[bool] = None
    telemost_auto_record_enabled: Optional[bool] = None
    digest_enabled: Optional[bool] = None
    digest_chat_ids: Optional[list[int]] = None


class _MiniappPushSubscribeKeys(BaseModel):
    p256dh: str = ""
    auth: str = ""


class _MiniappPushSubscribeBody(BaseModel):
    endpoint: str = ""
    keys: Optional[_MiniappPushSubscribeKeys] = None
    user_agent: str = ""


class _MiniappPushUnsubscribeBody(BaseModel):
    endpoint: str = ""


class _MiniappEntryColorBody(BaseModel):
    entry_key: Optional[str] = None
    event_id: Optional[str] = None
    calendar_id: Optional[str] = None
    task_id: Optional[str] = None
    color: Optional[str] = None


class _MiniappDigestRefreshBody(BaseModel):
    date: Optional[str] = None


class _MiniappDigestReportPatch(BaseModel):
    title: Optional[str] = None
    body_html: Optional[str] = None


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
    kb_enabled: Optional[bool] = None
    expected_updated_at: Optional[str] = None
    expected_revision: Optional[int] = None
    # HTML из совместного документа Yjs: правки уже слиты, проверка ревизии не нужна.
    yjs: Optional[bool] = None


class _MiniappNoteSheetCreate(BaseModel):
    title: str = ""
    description: Optional[str] = None
    kind: Optional[str] = None


class _MiniappNoteSheetPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    kind: Optional[str] = None
    expected_revision: Optional[int] = None
    yjs: bool = False  # тело холста из совместного документа: без проверки ревизии


class _MiniappNoteAiEdit(BaseModel):
    # Окно ИИ в редакторе: «что сделать с текстом» → правки открытого листа (как у ИИ в обсуждении).
    instruction: str
    doc_view: str
    sheet_id: Optional[str] = None
    selection: Optional[str] = None
    note_title: Optional[str] = None


class _MiniappCanvasImageBody(BaseModel):
    prompt: str = ""
    # Лист презентации — чтобы взять её стиль для арт-дирекшена картинки.
    sheet_id: Optional[str] = None


class _MiniappCanvasOpBody(BaseModel):
    op: str
    slide_id: str = ""
    variant: str = ""
    theme: str = ""
    group: str = ""
    index: int = 0
    after: Optional[int] = None
    delta: int = 0
    path: str = ""
    icon: str = ""
    # Свободный слайд (free.canvas): элементы и фон для detach / free_update / add_free_slide.
    elements: Optional[list[dict[str, Any]]] = None
    surface: str = ""
    # Current editor HTML: inline text edits are synced before the op.
    html: Optional[str] = None


class _MiniappCanvasReviseBody(BaseModel):
    comments: dict[str, str] = Field(default_factory=dict)
    html: Optional[str] = None
    # Новый слайд после insert_after по описанию insert_text (вместо правок комментариями).
    insert_after: Optional[str] = None
    insert_text: Optional[str] = None
    # Макет нового слайда: "auto" | "custom" (свой по описанию) | "saved:<id>" («Мои макеты»).
    insert_layout: Optional[str] = None


class _MiniappCanvasRestyleImages(BaseModel):
    html: Optional[str] = None
    # Только посчитать картинки (для подтверждения), ничего не генерировать.
    dry_run: bool = False


class _MiniappCanvasLayoutSave(BaseModel):
    slide_id: str
    name: Optional[str] = None
    html: Optional[str] = None


class _MiniappCanvasSelection(BaseModel):
    sheet_id: Optional[str] = None
    leo_id: Optional[str] = None
    label: Optional[str] = None
    snippet: Optional[str] = None


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
    sheet_ids: Optional[list[str]] = None


class _MiniappNoteMemberAdd(BaseModel):
    email: Optional[str] = None
    telegram_user_id: Optional[int] = None
    telegram_username: Optional[str] = None


class _MiniappTeamCreate(BaseModel):
    name: str = ""


class _MiniappTeamPatch(BaseModel):
    name: str = ""


class _MiniappContactTeamsBody(BaseModel):
    team_ids: list[int] = Field(default_factory=list)


class _MiniappNoteTeamSharesBody(BaseModel):
    team_ids: list[int] = Field(default_factory=list)


class _MiniappNoteCollabBody(BaseModel):
    cursor: Optional[int] = None


class _MiniappShareCommentCreate(BaseModel):
    body: str = ""
    quote: str = ""
    prefix: str = ""
    suffix: str = ""
    parent_id: Optional[int] = None
    as_role: Optional[str] = None
    model: Optional[str] = None
    file_ids: list[int] = Field(default_factory=list)
    # Чат заметки: id чата с ИИ, -1 — «Комментарии», None — как раньше (до чатов).
    chat_id: Optional[int] = None


class _MiniappNoteChatCreate(BaseModel):
    title: str = ""
    visibility: str = "private"


class _MiniappNoteChatPatch(BaseModel):
    title: Optional[str] = None
    visibility: Optional[str] = None


class _MiniappNoteChatRead(BaseModel):
    last_id: int = Field(..., ge=0)


class _MiniappNoteChatSplit(BaseModel):
    after_id: int


class _MiniappChatCreate(BaseModel):
    title: Optional[str] = None
    origin: Optional[str] = None


class _MiniappChatPatch(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


class _MiniappNotePaeiStart(BaseModel):
    reply: str = ""
    parent_id: Optional[int] = None
    reply_to_id: Optional[int] = None
    quote: str = ""
    use_knowledge: bool = True
    knowledge_note_ids: Optional[list[str]] = None
    sheet_ids: Optional[list[str]] = None
    context_note_ids: Optional[list[str]] = None
    chat_id: Optional[int] = None
    model: Optional[str] = None


class _MiniappNoteResearchStart(_MiniappNotePaeiStart):
    pass


class _MiniappGptChatTurn(BaseModel):
    role: str
    content: str


class _MiniappGptChatBody(BaseModel):
    message: str = ""
    context: str = ""
    note_title: str = ""
    note_text: str = ""
    quote: str = ""
    quotes: list[str] = Field(default_factory=list)
    use_knowledge: bool = False
    knowledge_note_ids: Optional[list[str]] = None
    sheet_ids: Optional[list[str]] = None
    context_note_ids: Optional[list[str]] = None
    history: list[_MiniappGptChatTurn] = Field(default_factory=list)
    model: Optional[str] = None
    file_ids: list[int] = Field(default_factory=list)
    item_kind: Optional[str] = None
    item_id: Optional[str] = None
    agent_id: Optional[str] = None
    canvas_selections: list[_MiniappCanvasSelection] = Field(default_factory=list)
    canvas_sheet_id: Optional[str] = None
    # Открытый текстовый лист: вид для модели (текст + пронумерованные таблицы) и его id —
    # модель может прислать правки (assistant/lib/note_edits.py), клиент их подсветит.
    doc_view: Optional[str] = None
    doc_sheet_id: Optional[str] = None
    # Стиль новых презентаций по умолчанию — последний выбранный пользователем.
    canvas_theme: Optional[str] = None
    # Открытая доска (лист kind=board): модель видит её узлы и может её дорисовать.
    board_sheet_id: Optional[str] = None


class _MiniappAgentCreate(BaseModel):
    title: str
    prompt: str = ""
    rules: str = ""
    model: Optional[str] = None
    web_search: bool = False
    temperature: Optional[float] = None


class _MiniappAgentPatch(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    rules: Optional[str] = None
    model: Optional[str] = None
    web_search: Optional[bool] = None
    temperature: Optional[float] = None
    max_rounds: Optional[int] = None


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
    from assistant.lib.usage_store import (
        journal_json_from_raw,
        journal_meta_from_raw,
        journal_source_links_from_raw,
        title_from_media_filename,
    )

    raw = row.get("raw_usage_json")
    preview = ""
    meta: dict[str, Any] = {}
    raw_s = raw if isinstance(raw, str) else None
    payload = journal_json_from_raw(raw_s)
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
    filename = str(payload.get("filename") or "").strip()
    if filename:
        out["filename"] = filename
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
    if not out.get("main_topic") and not out.get("meeting_topic"):
        derived = title_from_media_filename(filename)
        if derived:
            out["main_topic"] = derived
    links = journal_source_links_from_raw(raw if isinstance(raw, str) else None)
    out.update(links)
    return out


_SHARE_KIND_LABELS = {
    "local": "Заметка",
    "summarize": "Саммари",
    "obuchat_transcribe": "Транскрипция",
    "format_note": "Заметка",
    "answer_with_context": "Ответ",
    "digest": "Дайджест",
    "chat": "Чат",
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
        "sheet_ids": share_links_store.normalize_sheet_ids(link.get("sheet_ids")),
        "views": int(link.get("views") or 0),
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


def _comment_file_api(row: dict[str, Any]) -> dict[str, Any]:
    kind = str(row.get("item_kind") or "")
    item_id = str(row.get("item_id") or "")
    fid = int(row["id"])
    return {
        "id": fid,
        "name": str(row.get("filename") or "file"),
        "mime": str(row.get("mime") or "application/octet-stream"),
        "size": int(row.get("size") or 0),
        "kind": str(row.get("kind") or "file"),
        "url": f"/notes/{kind}/{item_id}/discuss-files/{fid}",
    }


def _materialize_canvas_sheet_images(
    *,
    note: dict[str, Any],
    sheet: dict[str, Any],
    chat_images: list[Any],
    note_sheets_mod: Any,
) -> dict[str, Any]:
    """Embed generated images into pending/abstract media slots on a web sheet."""
    from assistant.integrations.openrouter_client import openrouter_generate_images
    from assistant.lib import web_canvas as web_canvas_lib

    if not sheet or web_canvas_lib.normalize_kind(sheet.get("kind")) != web_canvas_lib.SHEET_KIND_WEB:
        return sheet
    html = str(sheet.get("body") or "")
    if not html.strip():
        return sheet
    images: list[dict[str, str]] = [
        img for img in (chat_images or []) if isinstance(img, dict) and str(img.get("b64") or "").strip()
    ]
    prompts = web_canvas_lib.extract_canvas_img_prompts(html)
    if prompts:
        from assistant.lib import image_style

        theme = image_style.deck_theme_of_html(html)
        prompts = [image_style.styled_prompt(p, theme) for p in prompts]
    if prompts and not images:
        # До 4 картинок за раз (слайд «Изображения с подписями» — до 3), параллельно и по порядку слотов.
        from concurrent.futures import ThreadPoolExecutor

        def _gen(prompt: str) -> list[dict[str, Any]]:
            try:
                return openrouter_generate_images(prompt, n=1, timeout=180) or []
            except Exception as e:
                print(f"[web_canvas] img_prompt_generate failed err={e!r}")
                return []

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_gen, prompts[:4]))
        for generated in results:
            got = [g for g in generated if isinstance(g, dict) and str(g.get("b64") or "").strip()]
            if not got:
                break  # слоты заполняются по порядку — пропуск сдвинул бы картинки
            images.append(got[0])
    data_urls = web_canvas_lib.image_dicts_to_data_urls(images)
    if not data_urls:
        return sheet
    filled = web_canvas_lib.fill_media_slots_with_data_urls(html, data_urls)
    if filled == html:
        return sheet
    try:
        sid = int(sheet["id"])
    except (TypeError, ValueError, KeyError):
        return sheet
    updated = note_sheets_mod.update_sheet(
        int(note["id"]),
        sid,
        body=filled,
        kind=web_canvas_lib.SHEET_KIND_WEB,
    )
    return updated or {**sheet, "body": filled, "description": filled}


def _resolve_post_chat(
    owner: str, kind: str, item_id: str, uid: str, chat_id: Optional[int], parent_id: Optional[int] = None
) -> Optional[int]:
    """Чат для нового сообщения (видимый автору) или 404. None — без чата (старые клиенты)."""
    from assistant.stores import note_chats
    from assistant.stores import share_comments as share_comments_store

    if parent_id not in (None, 0):
        parent = share_comments_store.get_comment(int(parent_id))
        if not parent or not note_chats.comment_visible(parent, uid):
            raise HTTPException(status_code=404, detail="Комментарий для ответа не найден")
    if not note_chats.supports_chats(kind) or chat_id is None:
        return None
    if int(chat_id) == note_chats.COMMENTS_CHAT_ID:
        return note_chats.COMMENTS_CHAT_ID
    chat = note_chats.chat_for_item(chat_id, owner, kind, item_id)
    if not note_chats.can_view(chat, uid):
        raise HTTPException(status_code=404, detail="Чат не найден")
    return int(chat_id)


def _comment_file_visible(item: dict[str, Any], uid: str) -> bool:
    from assistant.stores import note_chats
    from assistant.stores import share_comments as share_comments_store

    cid = item.get("comment_id")
    if cid in (None, ""):
        return True
    return note_chats.comment_visible(share_comments_store.get_comment(int(cid)), uid)


def _comment_api(row: dict[str, Any], *, viewer_uid: str | None = None) -> dict[str, Any]:
    owner = str(row.get("owner_user_id") or "")
    author = str(row.get("author_user_id") or "")
    can_delete = bool(viewer_uid) and viewer_uid in {owner, author}
    from assistant.stores import share_comments as share_comments_store

    parent_id = row.get("parent_id")
    try:
        parent_id = int(parent_id) if parent_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        parent_id = None
    attachments = []
    try:
        from assistant.stores import comment_files

        attachments = [
            _comment_file_api(f) for f in comment_files.list_for_comment(int(row["id"]))
        ]
    except Exception:
        attachments = []
    is_paie = share_comments_store.is_paie_comment(row)
    is_gpt = share_comments_store.is_gpt_comment(row)
    is_research = share_comments_store.is_research_comment(row)
    is_agent = share_comments_store.is_agent_turn(row)
    is_assistant = is_paie or is_gpt or is_research or is_agent
    is_mine = bool(viewer_uid) and viewer_uid == author and not is_assistant
    return {
        "id": int(row["id"]),
        "author_user_id": author,
        "author_name": str(row.get("author_name") or "Пользователь"),
        "author_username": row.get("author_username"),
        "body": str(row.get("body") or ""),
        "created_at": row.get("created_at"),
        "chat_id": row.get("chat_id"),
        "quote": str(row.get("quote") or ""),
        "prefix": str(row.get("prefix") or ""),
        "suffix": str(row.get("suffix") or ""),
        "parent_id": parent_id,
        "is_paie": is_paie,
        "is_gpt": is_gpt,
        "is_research": is_research,
        "is_agent": is_agent,
        "agent_id": share_comments_store.agent_id_of(row),
        "is_mine": is_mine,
        "can_delete": can_delete,
        "attachments": attachments,
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
    raw = _tma_init_data_from_request(request)
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
        from assistant.stores import note_sheets as note_sheets_store

        sheet_ids = share_links_store.normalize_sheet_ids(link.get("sheet_ids"))
        body, _sheets = note_sheets_store.compose_share_body(note, sheet_ids)
        if not _sheets:
            return None  # листы ссылки удалены
        canvases = note_sheets_store.list_share_canvases(note, sheet_ids)
        boards = note_sheets_store.list_share_boards(note, sheet_ids)
        return {
            "kind": "local",
            "operation": None,
            "label": _SHARE_KIND_LABELS["local"],
            "title": title[:500],
            "body": body[:120000],
            "canvases": canvases,
            "boards": boards,
            "updated_at": note.get("updated_at"),
            "access": share_links_store.normalize_access(link.get("access")),
            "item_id": item_id,
            "sheet_ids": sheet_ids,
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
    if kind == "digest":
        try:
            uid_i = int(uid)
        except (TypeError, ValueError):
            return None
        row = _digest_report_for_user(uid_i, item_id)
        if not row:
            return None
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        title = (
            str((summary or {}).get("editor_title") or "").strip()
            or str(row.get("title") or "").strip()
            or "Дайджест"
        )
        body = str((summary or {}).get("body_html") or row.get("preview") or "")
        return {
            "kind": "digest",
            "operation": "chat_digest",
            "label": _SHARE_KIND_LABELS.get("digest", "Дайджест"),
            "title": title[:500],
            "body": body[:120000],
            "updated_at": row.get("generated_at"),
            "access": share_links_store.normalize_access(link.get("access")),
            "item_id": item_id,
        }
    if kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store
        from assistant.stores import share_comments as share_comments_store

        thread = chat_threads_store.get_thread(uid, item_id)
        if not thread:
            return None
        rows = share_comments_store.list_comments(uid, "chat", item_id)[-500:]
        messages: list[dict[str, Any]] = []
        total = 0
        for row in reversed(rows):
            body = str(row.get("body") or "").strip()
            if not body:
                continue
            total += len(body)
            if total > 120000:
                break
            is_assistant = (
                share_comments_store.is_gpt_comment(row)
                or share_comments_store.is_research_comment(row)
                or share_comments_store.is_agent_turn(row)
                or share_comments_store.is_paie_comment(row)
            )
            # Наружу — только имя и текст: без telegram id и username.
            messages.append(
                {
                    "author": "Ассистент" if is_assistant else (str(row.get("author_name") or "").strip() or "Участник"),
                    "is_gpt": is_assistant,
                    "body": body,
                    "created_at": row.get("created_at"),
                }
            )
        messages.reverse()
        return {
            "kind": "chat",
            "operation": None,
            "label": _SHARE_KIND_LABELS["chat"],
            "title": (str(thread.get("title") or "").strip() or "Чат")[:500],
            "body": "",
            "messages": messages,
            "updated_at": thread.get("updated_at"),
            "access": "view",
            "item_id": item_id,
        }
    return None


def _digest_report_for_user(uid: int, item_id: str) -> dict[str, Any] | None:
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    parsed = digest_store.parse_item_id(item_id)
    if not parsed:
        return None
    day, chat_id = parsed
    if not user_prefs.digest_enabled(uid):
        return None
    if int(chat_id) not in set(user_prefs.digest_chat_ids(uid)):
        return None
    return digest_store.get_report(day, chat_id)


def _owner_can_share_item(uid: str, kind: str, item_id: str) -> bool:
    fake = {"user_id": uid, "item_kind": kind, "item_id": item_id}
    return _public_share_payload(fake) is not None


def _resolve_item_owner(uid: str, kind: str, item_id: str) -> str | None:
    """Владелец записи, если зритель — владелец или участник (для local)."""
    kind = (kind or "").strip()
    if kind == "local":
        from assistant.stores import notes as notes_store

        try:
            nid = int(item_id)
        except (TypeError, ValueError):
            return None
        note = notes_store.get_accessible_note(uid, nid, with_sharing=False)
        if not note:
            return None
        return str(note.get("owner_user_id") or "") or None
    if kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store

        thread = chat_threads_store.get_accessible_thread(uid, item_id)
        if not thread:
            return None
        return str(thread.get("owner_user_id") or "") or None
    if kind == "digest":
        try:
            uid_i = int(uid)
        except (TypeError, ValueError):
            return None
        if _digest_report_for_user(uid_i, item_id):
            return str(uid)
        return None
    if kind == "task":
        # Вложения в обсуждении поручения: постановщик и исполнитель.
        from assistant.stores import calendar_tasks as calendar_tasks_store

        task = calendar_tasks_store.get_task_for_user(uid, item_id)
        if not task:
            return None
        return str(task.get("owner_user_id") or "") or None
    if _owner_can_share_item(uid, kind, item_id):
        return str(uid)
    return None


def _remember_principal_profile(principal: _MiniappPrincipal) -> None:
    try:
        from assistant.stores import note_members

        u = principal.user or {}
        note_members.upsert_profile(
            int(principal.telegram_user_id),
            username=u.get("username"),
            first_name=str(u.get("first_name") or "") or None,
            last_name=str(u.get("last_name") or "") or None,
            photo_url=str(u.get("photo_url") or "") or None,
        )
    except Exception as e:
        print(f"[miniapp_auth] remember profile failed uid={principal.telegram_user_id} err={e!r}")


def _principal_display_name(principal: _MiniappPrincipal) -> str:
    _, name, username = _comment_author_from_principal(principal)
    return name or (f"@{username}" if username else "Пользователь")


def _notify_member_added(
    *,
    member_user_id: int,
    title: str,
    note_id: int,
    adder_name: str,
) -> None:
    from assistant.lib.telegram_notify import send_message
    from assistant.stores import note_members

    link = note_members.note_title_link_html(title, note_id)
    who = html_lib.escape(adder_name or "Участник")
    send_message(
        int(member_user_id),
        f"{who} добавил(а) вас в заметку {link}.",
    )


def _notify_chat_member_added(
    *,
    member_user_id: int,
    title: str,
    thread_id: int,
    adder_name: str,
) -> None:
    from assistant.lib.telegram_notify import send_message

    link = _discussion_title_link_html(title or "Чат", "chat", str(thread_id))
    who = html_lib.escape(adder_name or "Участник")
    send_message(int(member_user_id), f"{who} добавил(а) вас в чат {link}.")


def _notify_team_members_added(added: list[dict[str, Any]], adder_name: str) -> None:
    seen: set[tuple[str, int]] = set()
    for row in added or []:
        try:
            mid = int(row.get("user_id") or 0)
            nid = int(row.get("note_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or nid <= 0:
            continue
        key = (str(mid), nid)
        if key in seen:
            continue
        seen.add(key)
        _notify_member_added(
            member_user_id=mid,
            title=str(row.get("title") or "База знаний"),
            note_id=nid,
            adder_name=adder_name,
        )


def _notify_edit_request(req: dict[str, Any], title: str) -> None:
    from assistant.lib.telegram_notify import send_message
    from assistant.stores import note_members

    link = note_members.note_title_link_html(title, int(req["note_id"]))
    who = note_members.requester_label(
        req.get("requester_username"),
        req.get("requester_name"),
        str(req.get("requester_user_id") or ""),
    )
    kb = {
        "inline_keyboard": [
            [
                {"text": "Отклонить", "callback_data": f"ned:no:{req['token']}"},
                {"text": "Разрешить", "callback_data": f"ned:ok:{req['token']}"},
            ]
        ]
    }
    send_message(
        int(req["owner_user_id"]),
        f"{link}\nпользователь {who} просит права редактировать",
        reply_markup=kb,
    )


def _discussion_item_title(owner: str, kind: str, item_id: str) -> str:
    kind = (kind or "").strip()
    if kind == "local":
        from assistant.stores import notes as notes_store

        try:
            note = notes_store.get_note(owner, int(item_id))
        except (TypeError, ValueError):
            note = None
        if note:
            return str(note.get("title") or note.get("content") or "Заметка").strip() or "Заметка"
        return "Заметка"
    if kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store

        thread = chat_threads_store.get_thread(owner, item_id)
        if thread:
            return str(thread.get("title") or "Чат").strip() or "Чат"
        return "Чат"
    return "Обсуждение"


def _discussion_participants(owner: str, kind: str, item_id: str) -> list[dict[str, Any]]:
    kind = (kind or "").strip()
    if kind == "local":
        from assistant.stores import note_members

        try:
            return note_members.list_members_public(owner, int(item_id))
        except (TypeError, ValueError):
            return []
    if kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store

        return chat_threads_store.list_members_public(owner, item_id)
    return []


def _discussion_webapp_url(kind: str, item_id: str) -> str:
    from urllib.parse import quote

    from assistant.lib.webapp_public import webapp_entry_url

    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    kind = (kind or "").strip()
    raw = str(item_id or "").strip()
    if kind == "local":
        return f"{base}{sep}note={quote(raw, safe='')}"
    if kind == "chat":
        return f"{base}{sep}chat={quote(raw, safe='')}"
    return base


def _discussion_title_link_html(title: str, kind: str, item_id: str) -> str:
    from html import escape as html_escape

    label = html_escape((title or "").strip() or "Обсуждение")
    url = html_escape(_discussion_webapp_url(kind, item_id), quote=True)
    return f'<a href="{url}">{label}</a>'


def _discussion_participants_system_hint(owner: str, kind: str, item_id: str) -> str:
    members = _discussion_participants(owner, kind, item_id)
    if len(members) < 2:
        return ""
    lines: list[str] = []
    for m in members:
        name = str(m.get("name") or "Участник").strip() or "Участник"
        uname = str(m.get("username") or "").strip().lstrip("@")
        if uname:
            lines.append(f"- @{uname} — {name}")
        else:
            lines.append(f"- {name} (id {m.get('user_id')})")
    joined = "\n".join(lines)
    return (
        "Это совместное обсуждение нескольких людей. Участники:\n"
        f"{joined}\n"
        "Сообщения пользователей в истории помечены именем/@username автора. "
        "Упоминание @username в тексте относится к этому участнику. "
        "В ответах ссылайся на людей по @username или имени, когда это уместно."
    )


def _notify_discussion_comment(
    *,
    owner: str,
    kind: str,
    item_id: str,
    author_user_id: str,
    author_name: str,
    author_username: str | None,
    body: str,
    is_assistant: bool,
) -> None:
    """Пуш участникам, у которых сейчас не открыто это обсуждение."""
    from assistant.services.discussion_notify import notify_discussion_comment_safe

    notify_discussion_comment_safe(
        owner=owner,
        kind=kind,
        item_id=str(item_id),
        author_user_id=str(author_user_id),
        author_name=author_name,
        author_username=author_username,
        body=body,
        is_assistant=is_assistant,
    )


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


_ATTENDEE_RESPONSE_STATUSES = {
    "accepted": "accepted",
    "tentative": "tentative",
    "declined": "declined",
    "needsaction": "needsAction",
}


def _attendee_payload(
    email: Any, name: Any, *, organizer: bool = False, response_status: Any = None
) -> dict[str, Any] | None:
    em = str(email or "").strip().lower()
    if not em or "@" not in em:
        return None
    item: dict[str, Any] = {"email": em, "name": str(name or "").strip()}
    if organizer:
        item["organizer"] = True
    # Ответ участника на приглашение (Приду / Возможно / Не приду) — видно в карточке встречи.
    st = _ATTENDEE_RESPONSE_STATUSES.get(str(response_status or "").strip().lower())
    if st:
        item["response_status"] = st
    return item


def _merge_serialized_attendee(out: list[dict[str, Any]], seen: set[str], item: dict[str, Any] | None) -> None:
    if not item:
        return
    em = str(item.get("email") or "")
    if em in seen:
        for row in out:
            if row.get("email") != em:
                continue
            if not row.get("name") and item.get("name"):
                row["name"] = str(item.get("name") or "")
            if item.get("organizer"):
                row["organizer"] = True
            if item.get("response_status") and row.get("response_status") in (None, "", "needsAction"):
                row["response_status"] = item["response_status"]
            break
        return
    seen.add(em)
    out.append(item)


def _serialize_event_attendees(
    ev: dict[str, Any],
    *,
    contacts_by_email: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Участники для карточки встречи: гости + организатор, без текущего пользователя (self)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    src = ev or {}
    org = src.get("organizer") if isinstance(src.get("organizer"), dict) else {}
    if org and not org.get("self"):
        _merge_serialized_attendee(
            out,
            seen,
            _attendee_payload(org.get("email"), org.get("displayName"), organizer=True),
        )
    for row in src.get("attendees") or []:
        if not isinstance(row, dict):
            continue
        if row.get("self"):
            continue
        _merge_serialized_attendee(
            out,
            seen,
            _attendee_payload(
                row.get("email"),
                row.get("displayName") or row.get("name"),
                organizer=bool(row.get("organizer")),
                response_status=row.get("responseStatus"),
            ),
        )
    contacts_by_email = contacts_by_email or {}
    for item in out:
        em = str(item.get("email") or "")
        contact = contacts_by_email.get(em) if em else None
        if not isinstance(contact, dict):
            continue
        if not item.get("name") and contact.get("name"):
            item["name"] = str(contact.get("name") or "").strip()
        try:
            tid = int(contact.get("telegram_user_id") or 0)
        except (TypeError, ValueError):
            tid = 0
        if tid > 0:
            item["telegram_user_id"] = tid
        uname = str(contact.get("telegram_username") or "").strip().lstrip("@")
        if uname:
            item["telegram_username"] = uname
    return out


def _normalize_attendee_emails(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, dict):
            em = str(item.get("email") or "").strip().lower()
        else:
            em = str(item or "").strip().lower()
        if not em or "@" not in em or em in seen:
            continue
        seen.add(em)
        out.append(em)
    return out


def _normalize_attendee_refs(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw or []:
        uname = ""
        if isinstance(item, dict):
            em = str(item.get("email") or "").strip().lower()
            uname = str(item.get("telegram_username") or "").strip().lstrip("@")
        else:
            em = str(item or "").strip().lower()
        if not em or "@" not in em or em in seen:
            continue
        seen.add(em)
        out.append({"email": em, "telegram_username": uname})
    return out


def _miniapp_contacts_by_email(telegram_user_id: int) -> dict[str, dict[str, Any]]:
    from assistant.lib.calendar_attendees import enrich_contacts_calendar_flags
    from assistant.stores import contacts_store

    items = enrich_contacts_calendar_flags(
        list(
            contacts_store.load_contacts(telegram_user_id=int(telegram_user_id)) or []
        )
    )
    out: dict[str, dict[str, Any]] = {}
    for row in items:
        em = str((row or {}).get("email") or "").strip().lower()
        if em:
            out[em] = row
    return out


def _raise_calendar_http(exc: BaseException) -> None:
    from assistant.services.calendar import public_calendar_error

    status, detail = public_calendar_error(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _serialize_calendar_event(
    ev: dict[str, Any],
    *,
    contacts_by_email: dict[str, dict[str, Any]] | None = None,
    task_calendar_ids: set[str] | None = None,
) -> dict[str, Any]:
    from assistant.lib.calendar_event_utils import (
        calendar_description_plain,
        calendar_entry_kind_label,
        calendar_event_has_task_marker,
        calendar_event_is_task,
        event_is_invitation,
        event_needs_rsvp,
        event_self_response_status,
    )

    cal_id = str(ev.get("_calendarId") or "primary").strip() or "primary"
    st_raw = ev.get("start") or {}
    en_raw = ev.get("end") or {}
    st = _gcal_json_time_fragment(st_raw if isinstance(st_raw, dict) else {})
    en = _gcal_json_time_fragment(en_raw if isinstance(en_raw, dict) else {})
    invited = event_is_invitation(ev)
    status = event_self_response_status(ev)
    is_task = calendar_event_is_task(ev, task_calendar_ids=task_calendar_ids)
    kind = "Задача" if is_task else calendar_entry_kind_label(ev)
    return {
        "id": str(ev.get("id") or ""),
        "calendar_id": cal_id,
        "summary": str(ev.get("summary") or "").strip(),
        "kind": kind,
        "entry_type": "task" if is_task else "event",
        "leo_task_marker": bool(calendar_event_has_task_marker(ev)),
        "start": st,
        "end": en,
        "html_link": str(ev.get("htmlLink") or "").strip(),
        "recurringEventId": str(ev.get("recurringEventId") or "").strip(),
        "meet_url": _event_meet_url(ev),
        "description": calendar_description_plain(str(ev.get("description") or ""))[:8000],
        "location": str(ev.get("location") or "").strip(),
        "attendees": _serialize_event_attendees(ev, contacts_by_email=contacts_by_email),
        "is_organizer": not invited,
        "self_response_status": status,
        "needs_rsvp": event_needs_rsvp(ev),
    }


def _miniapp_dev_mode_on() -> bool:
    return (os.getenv("MINIAPP_DEV_MODE", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_LOCAL_DEV_RSVP: dict[str, str] = {}
# In-memory overrides for fixture events (start/end/title) when Google is unavailable.
_LOCAL_DEV_EVENT_OVERRIDES: dict[str, dict[str, Any]] = {}


def _is_local_dev_event_id(event_id: str) -> bool:
    return str(event_id or "").startswith("local-dev-ev-")


def _local_dev_event_key(uid: int, event_id: str) -> str:
    return f"{int(uid)}:{str(event_id or '').strip()}"


def _local_dev_iso_to_fragment(raw: str, timezone_name: str) -> dict[str, str]:
    """Normalize client ISO into {dateTime, timeZone} for fixture events."""
    s = str(raw or "").strip()
    if not s:
        return {}
    if "T" not in s and " " not in s:
        return {"date": s[:10]}
    try:
        from zoneinfo import ZoneInfo

        cleaned = s.replace("Z", "+00:00").replace("z", "+00:00")
        dt = datetime.fromisoformat(cleaned.replace(" ", "T", 1))
        tz = ZoneInfo(timezone_name) if timezone_name else None
        if dt.tzinfo is None and tz is not None:
            dt = dt.replace(tzinfo=tz)
        elif dt.tzinfo is not None and tz is not None:
            dt = dt.astimezone(tz)
        offset = dt.strftime("%z") if dt.tzinfo else ""
        if len(offset) == 5:
            offset = offset[:3] + ":" + offset[3:]
        return {
            "dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S") + offset,
            "timeZone": timezone_name or "UTC",
        }
    except Exception:
        return {"dateTime": s, "timeZone": timezone_name or "UTC"}


def _local_dev_event_day_key(ev: dict[str, Any]) -> str:
    st = ev.get("start") if isinstance(ev.get("start"), dict) else {}
    raw = str((st or {}).get("dateTime") or (st or {}).get("date") or "")
    return raw[:10]


def _apply_local_dev_event_state(
    uid: int, events: list[dict[str, Any]], *, day_iso: str | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        key = _local_dev_event_key(uid, str(ev.get("id") or ""))
        st = _LOCAL_DEV_RSVP.get(key)
        if st == "declined":
            continue
        item = dict(ev)
        ov = _LOCAL_DEV_EVENT_OVERRIDES.get(key) or {}
        if ov.get("summary"):
            item["summary"] = str(ov["summary"])
        if ov.get("description") is not None:
            item["description"] = str(ov.get("description") or "")
        if isinstance(ov.get("start"), dict):
            item["start"] = dict(ov["start"])
        if isinstance(ov.get("end"), dict):
            item["end"] = dict(ov["end"])
        if st in ("accepted", "tentative"):
            item["self_response_status"] = st
            item["needs_rsvp"] = False
        if day_iso and _local_dev_event_day_key(item) and _local_dev_event_day_key(item) != day_iso:
            continue
        out.append(item)
    return out


def _apply_local_dev_rsvp(uid: int, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _apply_local_dev_event_state(uid, events)


def _local_dev_events_for(uid: int, day_iso: str, timezone_name: str) -> list[dict[str, Any]]:
    return _apply_local_dev_event_state(
        uid, _local_dev_calendar_events(day_iso, timezone_name), day_iso=day_iso
    )


def _put_local_dev_event(
    uid: int,
    event_id: str,
    parsed: dict[str, Any],
    *,
    calendar_id: str | None,
    timezone_name: str,
) -> dict[str, Any]:
    key = _local_dev_event_key(uid, event_id)
    cur = dict(_LOCAL_DEV_EVENT_OVERRIDES.get(key) or {})
    if parsed.get("title"):
        cur["summary"] = str(parsed["title"]).strip()
    if "description" in parsed:
        cur["description"] = str(parsed.get("description") or "")
    if parsed.get("start"):
        frag = _local_dev_iso_to_fragment(str(parsed["start"]), timezone_name)
        if frag:
            cur["start"] = frag
    if parsed.get("end"):
        frag = _local_dev_iso_to_fragment(str(parsed["end"]), timezone_name)
        if frag:
            cur["end"] = frag
    _LOCAL_DEV_EVENT_OVERRIDES[key] = cur
    base = {
        "id": event_id,
        "calendar_id": str(calendar_id or "primary"),
        "summary": str(cur.get("summary") or event_id),
        "kind": "Встреча",
        "start": cur.get("start") or {},
        "end": cur.get("end") or {},
        "description": str(cur.get("description") or ""),
        "attendees": [],
        "is_organizer": True,
        "needs_rsvp": False,
    }
    return base

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
            f"local-dev-ev-rsvp-{day_iso}",
            "Дизайн-ревью",
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
                "attendees": [],
                "is_organizer": True,
                "self_response_status": None,
                "needs_rsvp": False,
            }
        )
    if out:
        out[0]["attendees"] = [
            {
                "email": "artem.danilin.1999@gmail.com",
                "name": "Артём Данилин",
                "organizer": True,
                "telegram_username": "artyawn",
                "telegram_user_id": 871463833,
            }
        ]
        out[0]["is_organizer"] = False
        out[0]["self_response_status"] = "needsAction"
        out[0]["needs_rsvp"] = True
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
    day_d = datetime.strptime(day_iso, "%Y-%m-%d").date()
    win_start = datetime.combine(day_d, datetime.min.time(), tzinfo=tz)
    win_end = win_start + timedelta(days=1)

    def _finish(payload: dict[str, Any]) -> dict[str, Any]:
        return _attach_meeting_material_kinds(_attach_leo_tasks(payload, uid, win_start, win_end, tz), uid)

    if not has_token:
        if _miniapp_dev_mode_on():
            return _finish(
                {
                    "connected": True,
                    "date": day_iso,
                    "timezone": str(tz),
                    "events": _local_dev_events_for(uid, day_iso, str(tz)),
                    "dev_fixtures": True,
                }
            )
        return _finish(
            {
                "connected": False,
                "date": day_iso,
                "timezone": str(tz),
                "events": [],
                "error": "Подключите Google Calendar в боте: /calendar_auth",
            }
        )
    try:
        payload = calendar_free_slots(
            {"free_slots_date": day_iso},
            telegram_user_id=int(telegram_user_id),
            include_event_details=True,
        )
    except Exception as e:
        print(f"[miniapp_calendar] today_load_failed uid={uid} err={e!r}")
        err = str(e).strip() or "Ошибка загрузки календаря"
        if _miniapp_dev_mode_on():
            return _finish(
                {
                    "connected": True,
                    "date": day_iso,
                    "timezone": str(tz),
                    "events": _local_dev_events_for(uid, day_iso, str(tz)),
                    "dev_fixtures": True,
                    "error_ignored": err,
                }
            )
        return _finish(
            {
                "connected": False,
                "date": day_iso,
                "timezone": str(tz),
                "events": [],
                "error": err,
            }
        )
    try:
        events_raw = [e for e in (payload.get("events") or []) if isinstance(e, dict)]
        ser_sorted = _serialize_gcal_events(uid, events_raw, 80)
        if not ser_sorted and _miniapp_dev_mode_on():
            ser_sorted = _local_dev_events_for(uid, day_iso, str(tz))
        return _finish(
            {
                "connected": True,
                "date": day_iso,
                "timezone": str(tz),
                "events": ser_sorted,
                **({"dev_fixtures": True} if not events_raw and _miniapp_dev_mode_on() else {}),
            }
        )
    except Exception as e:
        print(f"[miniapp_calendar] serialize_failed err={e!r}")
        return _finish(
            {
                "connected": True,
                "date": day_iso,
                "timezone": str(tz),
                "events": [],
                "error": str(e),
            }
        )


def _parse_calendar_day(raw: Optional[str], fallback) -> Any:
    text = str(raw or "").strip()[:10]
    if not text:
        return fallback
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return fallback


def _google_tasks_calendar_ids(uid: int) -> set[str]:
    try:
        from assistant.services import calendar_sources as cal_sources
        from assistant.services.google_tasks import is_tasks_calendar_name

        return {
            str(c.get("id") or "")
            for c in cal_sources.list_readable_calendars(uid)
            if is_tasks_calendar_name(str(c.get("summary") or ""), str(c.get("id") or ""))
        } - {""}
    except Exception as e:
        print(f"[miniapp_calendar] tasks_calendars err={e!r}")
        return set()


def _serialize_gcal_events(
    uid: int, events_raw: list[Any], limit: int
) -> list[dict[str, Any]]:
    ser: list[dict[str, Any]] = []
    try:
        contacts_by_email = _miniapp_contacts_by_email(uid)
    except Exception as ex:
        print(f"[miniapp_calendar] contacts_index err={ex!r}")
        contacts_by_email = {}
    task_cal_ids = _google_tasks_calendar_ids(uid)
    for e in events_raw or []:
        if not isinstance(e, dict):
            continue
        try:
            item = _serialize_calendar_event(
                e,
                contacts_by_email=contacts_by_email,
                task_calendar_ids=task_cal_ids,
            )
            if item.get("self_response_status") == "declined":
                continue
            start_day = str(e.get("_start_day") or "").strip()[:10]
            end_day = str(e.get("_end_day") or "").strip()[:10]
            if start_day:
                item["start_day"] = start_day
                item["end_day"] = end_day or start_day
                item["day_end_exclusive"] = bool(e.get("_day_end_exclusive"))
            elif not item.get("start_day"):
                st = item.get("start") or {}
                en = item.get("end") or {}
                if st.get("date") and not st.get("dateTime"):
                    item["start_day"] = str(st.get("date"))[:10]
                    item["end_day"] = str(en.get("date") or item["start_day"])[:10]
                    item["day_end_exclusive"] = True
                else:
                    dt = str(st.get("dateTime") or "")
                    if len(dt) >= 10:
                        item["start_day"] = dt[:10]
                        edt = str(en.get("dateTime") or dt)
                        item["end_day"] = edt[:10] if len(edt) >= 10 else dt[:10]
                        item["day_end_exclusive"] = False
            ser.append(item)
        except Exception as ex:
            print(f"[miniapp_calendar] skip_event id={e.get('id')!r} err={ex!r}")

    def _sort_key(d: dict[str, Any]) -> str:
        st = d.get("start") or {}
        if not isinstance(st, dict):
            return ""
        return str(st.get("dateTime") or st.get("date") or "")

    return sorted(ser, key=_sort_key)[: max(0, int(limit))]


def _leo_task_events(
    uid: int, start_dt: datetime, end_dt: datetime, tz: Any, *, pull: bool = True
) -> list[dict[str, Any]]:
    if pull:
        try:
            from assistant.services import google_tasks

            # Не блокируем ответ календаря на 2с — импорт задач в фоне.
            google_tasks.schedule_pull(uid, start_dt, end_dt)
        except Exception as e:
            print(f"[miniapp_calendar] tasks_pull err={e!r}")
    try:
        from assistant.stores import calendar_tasks as calendar_tasks_store

        rows = calendar_tasks_store.list_tasks_in_window(uid, start_dt, end_dt)
        out: list[dict[str, Any]] = []
        for r in rows:
            if not calendar_tasks_store.task_visible_in_calendar(r, uid):
                continue
            out.append(
                calendar_tasks_store.as_calendar_event(r, tz=tz, viewer_id=uid)
            )
        return out
    except Exception as e:
        print(f"[miniapp_calendar] tasks_list err={e!r}")
        return []


def _gcal_bounds_for_task(ev: dict[str, Any], tz: Any) -> tuple[datetime | None, datetime | None]:
    st = ev.get("start") if isinstance(ev.get("start"), dict) else {}
    en = ev.get("end") if isinstance(ev.get("end"), dict) else {}
    raw_s = str(st.get("dateTime") or "").strip()
    raw_e = str(en.get("dateTime") or "").strip()
    if raw_s:
        try:
            start = datetime.fromisoformat(raw_s.replace("Z", "+00:00"))
        except ValueError:
            start = None
        end = None
        if raw_e:
            try:
                end = datetime.fromisoformat(raw_e.replace("Z", "+00:00"))
            except ValueError:
                end = None
        if start and start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=tz)
        return start, end
    day_s = str(st.get("date") or "").strip()[:10]
    if not day_s:
        return None, None
    try:
        day = datetime.strptime(day_s, "%Y-%m-%d").date()
    except ValueError:
        return None, None
    start = datetime.combine(day, datetime.min.time().replace(hour=9), tzinfo=tz)
    return start, start + timedelta(minutes=30)


def _norm_task_title(raw: str) -> str:
    title = str(raw or "").strip().lower().replace("ё", "е")
    if title.startswith("задача:"):
        title = title.split(":", 1)[1].strip()
    return title


def _event_local_day(ev: dict[str, Any], tz: Any):
    day = str(ev.get("start_day") or "").strip()[:10]
    if day:
        try:
            return datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            pass
    start, _end = _gcal_bounds_for_task(ev, tz)
    if start is None:
        return None
    if start.tzinfo is not None:
        start = start.astimezone(tz)
    return start.date()


_GCAL_TASK_LINK_RE = re.compile(
    r"изменения в названии.*?tasks\.google\.com/task/\S+"
    r"|changes to the title.*?tasks\.google\.com/task/\S+"
    r"|https?://tasks\.google\.com/task/\S+",
    flags=re.IGNORECASE | re.DOTALL,
)
_GCAL_TASK_ID_RE = re.compile(
    r"tasks\.google\.com/task/([A-Za-z0-9_-]+)",
    flags=re.IGNORECASE,
)


def _gcal_task_description(raw: str) -> str:
    text = _GCAL_TASK_LINK_RE.sub("", str(raw or "")).strip()
    return text


def _google_task_id_from_gcal(ev: dict[str, Any]) -> str:
    desc = str(ev.get("description") or "")
    match = _GCAL_TASK_ID_RE.search(desc)
    return str(match.group(1) or "").strip() if match else ""


def _leo_task_on_local_day(
    uid: int, title: str, day: Any, tz: Any
) -> dict[str, Any] | None:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    want = _norm_task_title(title)
    if not want:
        return None
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    noted: dict[str, Any] | None = None
    first: dict[str, Any] | None = None
    for row in calendar_tasks_store.list_tasks_in_window(
        uid, day_start, day_start + timedelta(days=1)
    ):
        if _norm_task_title(str(row.get("title") or "")) != want:
            continue
        raw_s = str(row.get("start_at") or "")
        try:
            rs = datetime.fromisoformat(raw_s.replace("Z", "+00:00"))
        except ValueError:
            continue
        if rs.tzinfo is not None:
            rs = rs.astimezone(tz)
        if rs.date() != day:
            continue
        if first is None:
            first = row
        if str(row.get("note_id") or "").strip() and noted is None:
            noted = row
    return noted or first


def _link_gcal_ids_to_task(
    uid: int,
    row: dict[str, Any],
    *,
    event_id: str,
    calendar_id: str,
    google_task_id: str = "",
) -> None:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    fields: dict[str, Any] = {
        "google_event_id": event_id,
        "google_calendar_id": calendar_id,
    }
    if google_task_id and not str(row.get("google_task_id") or "").strip():
        fields["google_task_id"] = google_task_id
    calendar_tasks_store.update_task(uid, row["id"], **fields)


def _import_gcal_task_events(uid: int, events: list[dict[str, Any]], tz: Any) -> None:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("entry_type") != "task" and ev.get("kind") != "Задача":
            continue
        eid = str(ev.get("id") or "").strip()
        if not eid or eid.startswith("task-"):
            continue
        if calendar_tasks_store.find_by_google_event(uid, eid):
            continue
        start, end = _gcal_bounds_for_task(ev, tz)
        if start is None:
            continue
        title = str(ev.get("summary") or "").strip()
        if title.lower().startswith("задача:"):
            title = title.split(":", 1)[1].strip()
        local = start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
        day = local.date()
        gid = _google_task_id_from_gcal(ev)
        cal_id = str(ev.get("calendar_id") or "")
        try:
            by_gid = calendar_tasks_store.find_by_google_task(uid, gid) if gid else None
            if by_gid:
                _link_gcal_ids_to_task(
                    uid,
                    by_gid,
                    event_id=eid,
                    calendar_id=cal_id,
                    google_task_id=gid,
                )
                continue
            already = _leo_task_on_local_day(uid, title, day, tz)
            if already:
                _link_gcal_ids_to_task(
                    uid,
                    already,
                    event_id=eid,
                    calendar_id=cal_id,
                    google_task_id=gid,
                )
                continue
            row = calendar_tasks_store.create_task(
                uid,
                title=title or "Задача",
                description=_gcal_task_description(str(ev.get("description") or "")),
                start_at=start,
                end_at=end,
                tz=tz,
            )
            _link_gcal_ids_to_task(
                uid,
                row,
                event_id=eid,
                calendar_id=cal_id,
                google_task_id=gid,
            )
        except Exception as e:
            print(f"[miniapp_calendar] import_gcal_task id={eid!r} err={e!r}")


def _link_events_to_google_tasks(
    uid: int,
    events: list[dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
    tz: Any,
) -> None:
    """Событие с тем же названием и днём, что Google Task, считаем задачей."""
    from assistant.stores import calendar_tasks as calendar_tasks_store

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in calendar_tasks_store.list_tasks_in_window(uid, start_dt, end_dt):
        title = _norm_task_title(str(row.get("title") or ""))
        raw_start = str(row.get("start_at") or "").strip()
        try:
            start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not title:
            continue
        if start.tzinfo is not None:
            start = start.astimezone(tz)
        key = (title, start.date().isoformat())
        prev = by_key.get(key)
        if prev is None or (
            str(row.get("note_id") or "").strip()
            and not str(prev.get("note_id") or "").strip()
        ):
            by_key[key] = row
    for ev in events:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or "").strip()
        if not eid or eid.startswith("task-"):
            continue
        if calendar_tasks_store.find_by_google_event(uid, eid):
            continue
        title = _norm_task_title(str(ev.get("summary") or ""))
        day = _event_local_day(ev, tz)
        if not title or day is None:
            continue
        row = by_key.get((title, day.isoformat()))
        if not row:
            continue
        _link_gcal_ids_to_task(
            uid,
            row,
            event_id=eid,
            calendar_id=str(ev.get("calendar_id") or ""),
            google_task_id=_google_task_id_from_gcal(ev),
        )
        ev["entry_type"] = "task"
        ev["kind"] = "Задача"


def _attach_meeting_material_kinds(payload: dict[str, Any], uid: int) -> dict[str, Any]:
    """Значки «Транскрипция / Заметка» у встреч, к которым пользователь привязал материалы."""
    try:
        from assistant.stores import meeting_links

        evs = [e for e in (payload.get("events") or []) if isinstance(e, dict) and e.get("id")]
        keys = [meeting_links.event_key(e.get("calendar_id"), e.get("id")) for e in evs]
        kinds = meeting_links.kinds_for_events(uid, keys)
        for e, k in zip(evs, keys):
            if k in kinds:
                e["materials"] = kinds[k]
    except Exception as e:
        print(f"[meeting_links] kinds failed uid={uid} err={e!r}")
    return payload


def _attach_leo_tasks(
    payload: dict[str, Any],
    uid: int,
    start_dt: datetime,
    end_dt: datetime,
    tz: Any,
) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    evs = [e for e in (payload.get("events") or []) if isinstance(e, dict)]
    _link_events_to_google_tasks(uid, evs, start_dt, end_dt, tz)
    _import_gcal_task_events(uid, evs, tz)
    _leo_task_events(uid, start_dt, end_dt, tz, pull=True)
    _reconcile_gcal_task_duplicates(uid, start_dt, end_dt)
    hide_ids: set[str] = set()
    for ev in evs:
        eid = str(ev.get("id") or "")
        if not eid or eid.startswith("task-"):
            continue
        if (
            ev.get("entry_type") == "task"
            or ev.get("kind") == "Задача"
            or ev.get("leo_task_marker")
            or calendar_tasks_store.find_by_google_event(uid, eid)
        ):
            hide_ids.add(eid)
    _ensure_task_calendar_events(uid, start_dt, end_dt)
    extra = _leo_task_events(uid, start_dt, end_dt, tz, pull=False)
    evs = [e for e in evs if str(e.get("id") or "") not in hide_ids]
    evs.extend(extra)

    def _sk(d: dict[str, Any]) -> str:
        st = d.get("start") or {}
        if not isinstance(st, dict):
            return ""
        return str(st.get("dateTime") or st.get("date") or "")

    payload["events"] = sorted(evs, key=_sk)
    return _apply_display_colors(uid, payload)


def _apply_display_colors(uid: int, payload: dict[str, Any]) -> dict[str, Any]:
    from assistant.stores import user_prefs

    colors = user_prefs.get_calendar_entry_colors(uid)
    if not colors:
        return payload
    evs = []
    for ev in payload.get("events") or []:
        if not isinstance(ev, dict):
            continue
        item = dict(ev)
        key = user_prefs.calendar_entry_color_key(item)
        color = colors.get(key) if key else None
        if color:
            item["display_color"] = color
            item["color_key"] = key
        else:
            item.pop("display_color", None)
            if key:
                item["color_key"] = key
        evs.append(item)
    payload["events"] = evs
    return payload


def _ensure_task_calendar_events(
    uid: int, start_dt: datetime, end_dt: datetime
) -> None:
    """Leo-задачи уходят в Google Tasks, не в события календаря."""
    from assistant.services import google_tasks
    from assistant.stores import calendar_tasks as calendar_tasks_store

    for row in calendar_tasks_store.list_tasks_in_window(uid, start_dt, end_dt):
        if str(row.get("owner_user_id") or "") != str(uid):
            continue
        if calendar_tasks_store.task_is_delegated(row):
            continue
        if row.get("done") or str(row.get("google_task_id") or "").strip():
            continue
        try:
            google_tasks.push_task(uid, row)
        except Exception as e:
            print(f"[calendar_tasks] ensure_task id={row.get('id')} err={e!r}")


def _reconcile_gcal_task_duplicates(uid: int, start_dt: datetime, end_dt: datetime) -> None:
    """Удалить только слоты, которые Leo сам писал в календарь (leoEntry)."""
    from assistant.lib.calendar_event_utils import calendar_event_has_leo_task_marker
    from assistant.services import calendar as calendar_svc
    from assistant.stores import calendar_tasks as calendar_tasks_store

    for row in calendar_tasks_store.list_tasks_in_window(uid, start_dt, end_dt):
        if str(row.get("owner_user_id") or "") != str(uid):
            continue
        if not str(row.get("google_event_id") or "").strip():
            continue
        try:
            remote = calendar_svc.get_event(
                uid,
                str(row.get("google_event_id") or ""),
                calendar_id=str(row.get("google_calendar_id") or "") or None,
            )
        except Exception:
            continue
        if not calendar_event_has_leo_task_marker(remote):
            continue
        try:
            calendar_svc.delete_task_event(uid, row)
            calendar_tasks_store.update_task(
                uid, row["id"], google_event_id="", google_calendar_id=""
            )
        except Exception as e:
            print(f"[calendar_tasks] reconcile id={row.get('id')} err={e!r}")


def _local_dev_events_range(uid: int, from_d: Any, to_d: Any, timezone_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cur = from_d
    while cur <= to_d:
        out.extend(_local_dev_events_for(uid, cur.isoformat(), timezone_name))
        cur += timedelta(days=1)
    return out


def _calendar_range_payload(
    telegram_user_id: int,
    from_iso: Optional[str] = None,
    to_iso: Optional[str] = None,
) -> dict[str, Any]:
    from assistant.integrations import google_calendar_oauth
    from assistant.services import calendar_sources as cal_sources
    from assistant.services.calendar import _tz_for

    uid = int(telegram_user_id)
    tz = _tz_for(uid)
    today = datetime.now(tz).date()
    start_d = _parse_calendar_day(from_iso, today)
    end_d = _parse_calendar_day(to_iso, start_d)
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    if (end_d - start_d).days > 62:
        end_d = start_d + timedelta(days=62)
    from_s = start_d.isoformat()
    to_s = end_d.isoformat()
    win_start = datetime.combine(start_d, datetime.min.time(), tzinfo=tz)
    win_end = datetime.combine(end_d + timedelta(days=1), datetime.min.time(), tzinfo=tz)

    def _finish(payload: dict[str, Any]) -> dict[str, Any]:
        return _attach_meeting_material_kinds(_attach_leo_tasks(payload, uid, win_start, win_end, tz), uid)

    has_token = google_calendar_oauth.user_token_path(uid).is_file()
    if not has_token:
        if _miniapp_dev_mode_on():
            return _finish(
                {
                    "connected": True,
                    "timezone": str(tz),
                    "from": from_s,
                    "to": to_s,
                    "events": _local_dev_events_range(uid, start_d, end_d, str(tz)),
                    "dev_fixtures": True,
                }
            )
        return _finish(
            {
                "connected": False,
                "timezone": str(tz),
                "from": from_s,
                "to": to_s,
                "events": [],
                "error": "Подключите Google Calendar в боте: /calendar_auth",
            }
        )
    try:
        collected: list[dict[str, Any]] = []
        for norm in cal_sources.list_events_in_window(uid, win_start, win_end):
            raw = dict(norm.get("raw") or {})
            raw["_calendarId"] = str(norm.get("calendar_id") or raw.get("_calendarId") or "primary")
            start = norm.get("start")
            end = norm.get("end")
            start_local = start.astimezone(tz) if isinstance(start, datetime) else None
            if start_local is not None:
                raw["_start_day"] = start_local.date().isoformat()
                raw["start"] = raw.get("start") if isinstance(raw.get("start"), dict) else {}
                if not raw["start"].get("dateTime") and not raw["start"].get("date"):
                    raw["start"] = {
                        "dateTime": start_local.isoformat(),
                        "timeZone": str(tz),
                    }
            if isinstance(end, datetime):
                end_local = end.astimezone(tz)
                raw["_end_day"] = end_local.date().isoformat()
                is_all_day = bool((raw.get("start") or {}).get("date") and not (raw.get("start") or {}).get("dateTime"))
                ends_midnight = end_local.time() == datetime.min.time()
                raw["_day_end_exclusive"] = is_all_day or (
                    ends_midnight
                    and (start_local is None or end_local.date() > start_local.date())
                )
                raw["end"] = raw.get("end") if isinstance(raw.get("end"), dict) else {}
                if not raw["end"].get("dateTime") and not raw["end"].get("date"):
                    raw["end"] = {
                        "dateTime": end_local.isoformat(),
                        "timeZone": str(tz),
                    }
            collected.append(raw)
        ser = _serialize_gcal_events(uid, collected, 1500)
        if not ser and _miniapp_dev_mode_on():
            ser = _local_dev_events_range(uid, start_d, end_d, str(tz))
        return _finish(
            {
                "connected": True,
                "timezone": str(tz),
                "from": from_s,
                "to": to_s,
                "events": ser,
                **({"dev_fixtures": True} if not collected and _miniapp_dev_mode_on() else {}),
            }
        )
    except Exception as e:
        print(f"[miniapp_calendar] range_load_failed uid={uid} err={e!r}")
        err = str(e).strip() or "Ошибка загрузки календаря"
        if _miniapp_dev_mode_on():
            return _finish(
                {
                    "connected": True,
                    "timezone": str(tz),
                    "from": from_s,
                    "to": to_s,
                    "events": _local_dev_events_range(uid, start_d, end_d, str(tz)),
                    "dev_fixtures": True,
                    "error_ignored": err,
                }
            )
        return _finish(
            {
                "connected": False,
                "timezone": str(tz),
                "from": from_s,
                "to": to_s,
                "events": [],
                "error": err,
            }
        )


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


class _LoginCodeBody(BaseModel):
    code: str


@miniapp_router.post("/auth/login-code")
async def miniapp_auth_login_code(body: _LoginCodeBody) -> JSONResponse:
    """Обмен одноразового кода из бота (/web) на browser-сессию — без oauth.telegram.org."""
    from assistant.lib.telegram_login_auth import issue_browser_session
    from assistant.stores import web_login_codes

    user = web_login_codes.consume_code(body.code)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Код неверный или устарел. Напишите боту /web и возьмите новый.",
        )
    uid = int(user["id"])
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


@miniapp_router.post("/auth/login-code/issue")
async def miniapp_auth_login_code_issue(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Выдать код/ссылку для входа в браузер (из миниаппа, когда oauth.telegram.org недоступен)."""
    from assistant.lib.webapp_public import webapp_entry_url
    from assistant.stores import web_login_codes
    from urllib.parse import urlencode

    uid = int(principal.telegram_user_id)
    u = dict(principal.user or {})
    issued = web_login_codes.issue_code(
        uid,
        username=u.get("username"),
        first_name=u.get("first_name"),
        last_name=u.get("last_name"),
        photo_url=u.get("photo_url"),
    )
    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    login_url = f"{base}{sep}{urlencode({'login': issued['code']})}"
    return {
        "code": issued["code"],
        "expires_at": issued["expires_at"],
        "ttl_sec": issued["ttl_sec"],
        "login_url": login_url,
    }


@miniapp_router.post("/auth/logout")
async def miniapp_auth_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    _clear_miniapp_session_cookie(resp)
    return resp


@miniapp_router.get("/me")
async def miniapp_me(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict:
    u = principal.user
    await run_in_threadpool(_remember_principal_profile, principal)
    return {
        "id": u.get("id") if u.get("id") is not None else int(principal.telegram_user_id),
        "telegram_user_id": int(principal.telegram_user_id),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "last_name": u.get("last_name"),
        "language_code": u.get("language_code"),
        "bot_username": _miniapp_resolve_bot_username(),
        "has_custom_avatar": await run_in_threadpool(_user_has_custom_avatar, int(principal.telegram_user_id)),
    }


def _user_has_custom_avatar(user_id: int) -> bool:
    from assistant.stores import user_avatar

    return user_avatar.has_avatar(user_id)


@miniapp_router.put("/profile/avatar")
async def miniapp_profile_avatar_put(
    request: Request,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> Response:
    """Своя аватарка: тело запроса — сама картинка (JPEG/PNG/WebP/HEIC)."""
    from assistant.stores import user_avatar

    uid = int(principal.telegram_user_id)
    raw = await request.body()
    if len(raw) > user_avatar.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше 10 МБ — выберите фото поменьше")
    try:
        data = await run_in_threadpool(user_avatar.save_avatar, uid, raw)
    except user_avatar.AvatarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@miniapp_router.delete("/profile/avatar")
async def miniapp_profile_avatar_delete(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_avatar

    removed = await run_in_threadpool(user_avatar.delete_avatar, int(principal.telegram_user_id))
    return {"ok": True, "removed": bool(removed)}


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
    try:
        trackers = _trackers_public(tid)
    except Exception as e:
        print(f"[integrations] trackers failed uid={tid} err={e!r}")
        trackers = []
    try:
        from assistant.services.mail_connect import accounts as mail_accounts_list

        mail_list = mail_accounts_list(tid)
    except Exception as e:
        print(f"[integrations] mail failed uid={tid} err={e!r}")
        mail_list = []

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
        "mail": bool(mail_list),
        "mail_accounts": mail_list,
        "trackers": trackers,
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
            recurrence=_clean_reminder_recurrence(body.recurrence) or "",
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
            recurrence=_clean_reminder_recurrence(body.recurrence),
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


def _task_api(task: dict[str, Any], *, tz: Any, viewer_id: int | str | None = None) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    item = dict(task)
    item["event"] = calendar_tasks_store.as_calendar_event(
        task, tz=tz, viewer_id=viewer_id
    )
    item["chip_label"] = item["event"].get("chip_label")
    item["schedule_log"] = list(item["event"].get("schedule_log") or [])
    item["schedule_log_lines"] = list(item["event"].get("schedule_log_lines") or [])
    if item.get("id"):
        item["url"] = task_webapp_url(item["id"])
    return item


def task_webapp_url(task_id: int | str) -> str:
    from assistant.lib.task_notify import task_webapp_url as _url

    return _url(task_id)


def task_title_link_html(title: str, task_id: int | str) -> str:
    from assistant.lib.task_notify import task_title_link_html as _link

    return _link(title, task_id)


def _notify_task_assignee(
    *,
    assignee_user_id: int | str,
    task: dict[str, Any],
    assigner_name: str,
) -> None:
    from assistant.lib.task_notify import notify_task_assignee

    notify_task_assignee(
        assignee_user_id=assignee_user_id,
        task=task,
        assigner_name=assigner_name,
    )


def _resolve_calendar_task_assignee(
    principal: "_MiniappPrincipal",
    body: "_MiniappCalendarTaskBody",
) -> str:
    raw = str(body.assignee_user_id or "").strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return str(n)
        except (TypeError, ValueError):
            pass
    email = str(body.assignee_email or "").strip()
    if not email:
        return raw
    try:
        from assistant.stores import note_members

        found, _ = note_members.resolve_contact_telegram_id(
            owner_user_id=int(principal.telegram_user_id),
            email=email,
            owner_username=_miniapp_tg_username(principal),
        )
        return str(int(found))
    except ValueError:
        return raw


@miniapp_router.get("/calendar/tasks/posted")
async def miniapp_calendar_tasks_posted(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    archived: int = Query(0, ge=0, le=1),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    want_archived = bool(archived)
    rows = await run_in_threadpool(
        partial(
            calendar_tasks_store.list_posted_tasks,
            uid,
            include_done=True,
            archived=want_archived,
        )
    )
    items = [_task_api(r, tz=tz, viewer_id=uid) for r in rows]

    def _links() -> None:
        from assistant.stores import trackers as trackers_store

        from assistant.stores import notes as notes_store

        titles: dict[str, str] = {}
        for it in items:
            try:
                it["tracker_links"] = trackers_store.tasks_for_calendar_task(uid, it.get("id"))
            except Exception as e:  # бейдж трекера — не повод не показать список
                print(f"[posted] tracker links failed task={it.get('id')} err={e!r}")
                it["tracker_links"] = []
            nid = str(it.get("note_id") or "")
            if nid.isdigit():  # поручение из встречи — «из «Синк по запуску»» в строке списка
                if nid not in titles:
                    n = notes_store.get_accessible_note(uid, int(nid), with_sharing=False)
                    titles[nid] = str(n.get("title") or "") if n and str(n.get("role") or "") == "transcription" else ""
                if titles[nid]:
                    it["meeting_title"] = titles[nid]

    await run_in_threadpool(_links)
    active_count = await run_in_threadpool(
        calendar_tasks_store.count_active_posted_tasks, uid
    )
    return {"ok": True, "items": items, "count": active_count, "archived": want_archived}


class _MiniappTaskStatusBody(BaseModel):
    status: str = ""
    note: str = ""


# «Напомнить» не чаще раза в несколько часов на задачу — исполнителя не заваливаем пушами.
TASK_NUDGE_COOLDOWN = timedelta(hours=4)


@miniapp_router.post("/calendar/tasks/posted/accept-done")
async def miniapp_calendar_tasks_accept_done(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    n = await run_in_threadpool(calendar_tasks_store.archive_done_posted, uid)
    return {"ok": True, "archived": n}


class _MiniappTaskTriageBody(BaseModel):
    action: str = ""
    start: Optional[str] = None
    end: Optional[str] = None
    all_day: Optional[bool] = None
    title: Optional[str] = None
    description: Optional[str] = None
    result: Optional[str] = None
    note: str = ""
    # undo: вернуть прежнее состояние разбора
    state: Optional[str] = None
    snoozed_until: str = ""


def _inbox_item_api(task: dict[str, Any], *, tz: Any, viewer_id: int) -> dict[str, Any]:
    from assistant.lib.task_notify import owner_display_name
    from assistant.stores import notes as notes_store

    item = _task_api(task, tz=tz, viewer_id=viewer_id)
    item["owner_name"] = owner_display_name(task.get("owner_user_id"))
    nid = str(task.get("note_id") or "").strip()
    if nid.isdigit():
        # Документ, из которого поставлена задача, — только если исполнитель может его открыть.
        try:
            note = notes_store.get_accessible_note(viewer_id, int(nid), with_sharing=False)
            if note:
                item["source"] = {
                    "id": int(nid),
                    "title": str(note.get("title") or "") or "Без названия",
                    "kind": "transcription" if note.get("role") == "transcription" else "note",
                }
        except Exception as e:
            print(f"[inbox] source task={task.get('id')} err={e!r}")
    return item


@miniapp_router.get("/calendar/tasks/inbox")
async def miniapp_calendar_tasks_inbox(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Входящие поручения исполнителя — для разбора карточками (Позже / Беру / Сделано / Изменить / Не моё)."""
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)

    def _run() -> dict[str, Any]:
        inbox = calendar_tasks_store.list_inbox(uid)
        return {
            "items": [_inbox_item_api(t, tz=tz, viewer_id=uid) for t in inbox["items"]],
            "snoozed": inbox["snoozed"],
        }

    res = await run_in_threadpool(_run)
    return {"ok": True, **res}


@miniapp_router.post("/calendar/tasks/{task_id}/triage")
async def miniapp_calendar_tasks_triage(
    task_id: int,
    body: _MiniappTaskTriageBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.lib import task_notify
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    name = _principal_display_name(principal)
    action = str(body.action or "").strip()
    task = await run_in_threadpool(calendar_tasks_store._assignee_task, uid, task_id)
    if not task:
        raise HTTPException(status_code=403, detail="Разобрать поручение может его исполнитель")
    if task.get("done") and action != "undo":
        raise HTTPException(status_code=400, detail="Задача уже выполнена")
    try:
        if action in ("accept", "edit"):
            row = await run_in_threadpool(
                partial(
                    calendar_tasks_store.triage_accept, uid, task_id, actor_name=name,
                    start_at=(body.start or "").strip() or None, end_at=(body.end or "").strip() or None,
                    all_day=body.all_day, title=body.title, description=body.description, tz=tz,
                )
            )
        elif action == "snooze":
            row = await run_in_threadpool(partial(calendar_tasks_store.triage_snooze, uid, task_id, tz=tz))
        elif action == "decline":
            row = await run_in_threadpool(
                partial(calendar_tasks_store.triage_decline, uid, task_id, body.note, actor_name=name)
            )
            if row:
                await run_in_threadpool(
                    partial(task_notify.notify_task_declined, task=row, assignee_name=name, note=body.note)
                )
        elif action == "done":
            await run_in_threadpool(partial(calendar_tasks_store.triage_accept, uid, task_id, actor_name=name))
            row = await run_in_threadpool(
                partial(calendar_tasks_store.update_task, uid, task_id, done=True, result=body.result, tz=tz)
            )
            if row:
                await run_in_threadpool(
                    partial(calendar_tasks_store.add_event, row["id"], "done", actor_user_id=uid,
                            actor_name=name, text=str(row.get("result") or ""))
                )
                await run_in_threadpool(partial(task_notify.notify_task_owner_done, task=row, completer_name=name))
        elif action == "undo":
            # Отмена «Сделано» снимает и отметку о выполнении (постановщик уже получил пуш — это ок).
            if task.get("done"):
                await run_in_threadpool(partial(calendar_tasks_store.update_task, uid, task_id, done=False, tz=tz))
            row = await run_in_threadpool(
                partial(calendar_tasks_store.triage_restore, uid, task_id, str(body.state or "inbox"), body.snoozed_until)
            )
        else:
            raise HTTPException(status_code=400, detail="Неизвестное действие")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if action in ("accept", "edit", "done", "undo"):
        synced = await run_in_threadpool(_sync_task_google, uid, row, "update")
        row = synced or row
    if action in ("done", "decline"):
        _memory_task_changed(row)
    _FOCUS_CACHE.pop(uid, None)
    _FOCUS_CACHE.pop(int(str(row.get("owner_user_id") or 0) or 0), None)
    return {"ok": True, "task": _task_api(row, tz=tz, viewer_id=uid)}


@miniapp_router.post("/calendar/tasks/{task_id}/seen")
async def miniapp_calendar_tasks_seen(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    row = await run_in_threadpool(calendar_tasks_store.mark_seen, uid, task_id)
    # Автор или своя задача — «видел» не про них, это не ошибка.
    return {"ok": True, "assignee_state": calendar_tasks_store.assignee_state(row) if row else None}


@miniapp_router.post("/calendar/tasks/{task_id}/status")
async def miniapp_calendar_tasks_status(
    task_id: int,
    body: _MiniappTaskStatusBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    before = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    try:
        row = await run_in_threadpool(
            partial(calendar_tasks_store.set_assignee_status, uid, task_id, body.status, body.note)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=403, detail="Статус ставит исполнитель поручения")
    prev = calendar_tasks_store.assignee_state(before)
    cur = calendar_tasks_store.assignee_state(row)
    # Пуш автору только про вопрос: «взял в работу» видно в списке, без лишнего шума.
    if cur["status"] == "question" and (prev["status"] != "question" or prev["status_note"] != cur["status_note"]):
        from assistant.lib.task_notify import notify_task_owner_question

        await run_in_threadpool(
            partial(
                notify_task_owner_question,
                task=row,
                asker_name=_principal_display_name(principal),
                question=cur["status_note"],
            )
        )
    if cur["status_note"]:
        _memory_task_changed(row)
    return {"ok": True, "task": _task_api(row, tz=tz, viewer_id=uid)}


@miniapp_router.post("/calendar/tasks/{task_id}/nudge")
async def miniapp_calendar_tasks_nudge(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.lib.task_notify import notify_task_nudge
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    task = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if str(task.get("owner_user_id") or "") != str(uid) or not calendar_tasks_store.task_is_delegated(task):
        raise HTTPException(status_code=403, detail="Напомнить может постановщик поручения")
    if task.get("done"):
        raise HTTPException(status_code=400, detail="Задача уже выполнена")
    last = calendar_tasks_store._parse_dt(task.get("last_nudged_at"))
    if last is not None and datetime.now(timezone.utc) - last < TASK_NUDGE_COOLDOWN:
        when = last.astimezone(tz).strftime("%H:%M")
        raise HTTPException(status_code=429, detail=f"Уже напомнили в {when}")
    sent = await run_in_threadpool(
        partial(notify_task_nudge, task=task, assigner_name=_principal_display_name(principal))
    )
    if not sent:
        raise HTTPException(status_code=502, detail="Не получилось отправить напоминание в Telegram")
    row = await run_in_threadpool(
        partial(calendar_tasks_store.mark_nudged, task_id, actor_user_id=uid, actor_name=_principal_display_name(principal))
    )
    return {"ok": True, "task": _task_api(row or task, tz=tz, viewer_id=uid)}


class _MiniappTaskMessageBody(BaseModel):
    text: str = ""
    file_ids: list[int] = Field(default_factory=list)
    refs: list[dict[str, Any]] = Field(default_factory=list)
    mentions: list[dict[str, Any]] = Field(default_factory=list)


def _task_message_files(uid: int, task: dict[str, Any], file_ids: list[int]) -> list[dict[str, Any]]:
    """Файлы, загруженные в обсуждение этого поручения (чужие id отбрасываем)."""
    from assistant.stores import comment_files

    out: list[dict[str, Any]] = []
    owner = str(task.get("owner_user_id") or "")
    for fid in list(dict.fromkeys(int(x) for x in file_ids if str(x).isdigit()))[:8]:
        row = comment_files.get_file(fid)
        if (
            row
            and str(row.get("owner_user_id") or "") == owner
            and str(row.get("item_kind") or "") == "task"
            and str(row.get("item_id") or "") == str(task["id"])
        ):
            out.append(_comment_file_api(row))
    return out


def _task_message_refs(refs: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in refs[:5]:
        kind = str((r or {}).get("kind") or "")
        rid = str((r or {}).get("id") or "").strip()
        if kind not in ("task", "note", "transcription", "event") or not rid:
            continue
        item = {"kind": kind, "id": rid[:200], "title": str(r.get("title") or "")[:200]}
        if kind == "event":
            item["calendar_id"] = str(r.get("calendar_id") or "")[:200]
            item["start"] = str(r.get("start") or "")[:40]
        out.append(item)
    return out


@miniapp_router.get("/calendar/tasks/{task_id}/events")
async def miniapp_calendar_tasks_events(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    task = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    events = await run_in_threadpool(calendar_tasks_store.list_events, task)
    return {"ok": True, "events": events}


@miniapp_router.post("/calendar/tasks/{task_id}/messages")
async def miniapp_calendar_tasks_message(
    task_id: int,
    body: _MiniappTaskMessageBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.lib.task_notify import notify_task_message
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    name = _principal_display_name(principal)
    task = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not task:
        raise HTTPException(status_code=403, detail="Писать могут постановщик и исполнитель поручения")
    files = await run_in_threadpool(_task_message_files, uid, task, body.file_ids)
    refs = _task_message_refs(body.refs)
    mentions = [
        {"user_id": str(m.get("user_id") or "")[:20], "name": str(m.get("name") or "")[:120]}
        for m in body.mentions[:10]
        if str((m or {}).get("user_id") or "").isdigit()
    ]
    meta = {k: v for k, v in (("files", files), ("refs", refs), ("mentions", mentions)) if v}
    try:
        row = await run_in_threadpool(
            partial(calendar_tasks_store.post_message, uid, task_id, body.text, actor_name=name, meta=meta)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=403, detail="Писать могут постановщик и исполнитель поручения")
    extra = []
    if files:
        extra.append(f"📎 {len(files)} влож.")
    extra += [f"«{r['title']}»" for r in refs if r.get("title")]
    push_text = " ".join([body.text.strip()] + extra).strip()
    await run_in_threadpool(
        partial(notify_task_message, task=row, sender_id=uid, sender_name=name, text=push_text, mentions=mentions)
    )
    _memory_task_changed(row)
    events = await run_in_threadpool(calendar_tasks_store.list_events, row)
    return {"ok": True, "task": _task_api(row, tz=tz, viewer_id=uid), "events": events}


async def _owner_task_action(task_id: int, principal: "_MiniappPrincipal", action: str, comment: str = "") -> dict[str, Any]:
    from assistant.lib import task_notify
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    name = _principal_display_name(principal)
    if action == "accept":
        fn = partial(calendar_tasks_store.accept_task, uid, task_id, actor_name=name)
    elif action == "return":
        fn = partial(calendar_tasks_store.return_task, uid, task_id, comment, actor_name=name)
    elif action == "insist":
        fn = partial(calendar_tasks_store.insist_task, uid, task_id, comment, actor_name=name)
    else:
        fn = partial(calendar_tasks_store.cancel_task, uid, task_id, actor_name=name)
    row = await run_in_threadpool(fn)
    if not row:
        raise HTTPException(status_code=403, detail="Это может сделать постановщик поручения")
    if action == "return":
        await run_in_threadpool(partial(task_notify.notify_task_returned, task=row, owner_name=name, comment=comment))
    elif action == "cancel":
        await run_in_threadpool(partial(task_notify.notify_task_cancelled, task=row, owner_name=name))
    elif action == "insist":
        await run_in_threadpool(partial(task_notify.notify_task_insisted, task=row, owner_name=name, comment=comment))
    synced = await run_in_threadpool(_sync_task_google, uid, row, "update")
    out = synced or row
    _memory_task_changed(out)
    events = await run_in_threadpool(calendar_tasks_store.list_events, out)
    return {"ok": True, "task": _task_api(out, tz=tz, viewer_id=uid), "events": events}


@miniapp_router.post("/calendar/tasks/{task_id}/accept")
async def miniapp_calendar_tasks_accept(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    return await _owner_task_action(task_id, principal, "accept")


@miniapp_router.post("/calendar/tasks/{task_id}/return")
async def miniapp_calendar_tasks_return(
    task_id: int,
    body: _MiniappTaskMessageBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    return await _owner_task_action(task_id, principal, "return", body.text)


@miniapp_router.post("/calendar/tasks/{task_id}/insist")
async def miniapp_calendar_tasks_insist(
    task_id: int,
    body: _MiniappTaskMessageBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """После «Не моё»: постановщик оставляет задачу за тем же исполнителем."""
    return await _owner_task_action(task_id, principal, "insist", body.text)


@miniapp_router.post("/calendar/tasks/{task_id}/cancel")
async def miniapp_calendar_tasks_cancel(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    return await _owner_task_action(task_id, principal, "cancel")


@miniapp_router.get("/calendar/tasks")
async def miniapp_calendar_tasks_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    note_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    if q is not None:
        # «+» в заметке: поиск задачи за всё время, открытые — первыми.
        needle = " ".join(str(q).split()).casefold()[:200]

        def _search() -> list[dict[str, Any]]:
            rows = calendar_tasks_store.list_involved_tasks(uid, limit=1000)
            if needle:
                rows = [
                    r
                    for r in rows
                    if needle in str(r.get("title") or "").casefold()
                    or needle in str(r.get("description") or "").casefold()
                ]
            rows.sort(key=lambda r: bool(r.get("done")))
            return rows[:15]

        rows = await run_in_threadpool(_search)
        return {"ok": True, "tasks": [_task_api(r, tz=tz, viewer_id=uid) for r in rows]}
    nid = str(note_id or "").strip()
    if nid:
        rows = await run_in_threadpool(calendar_tasks_store.list_tasks_for_note, uid, nid)
        return {"ok": True, "tasks": [_task_api(r, tz=tz, viewer_id=uid) for r in rows]}
    today = datetime.now(tz).date()
    start_d = _parse_calendar_day(start, today)
    end_d = _parse_calendar_day(end, start_d)
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    win_start = datetime.combine(start_d, datetime.min.time(), tzinfo=tz)
    win_end = datetime.combine(end_d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    rows = await run_in_threadpool(
        calendar_tasks_store.list_tasks_in_window, uid, win_start, win_end
    )
    return {"ok": True, "tasks": [_task_api(r, tz=tz, viewer_id=uid) for r in rows]}


@miniapp_router.get("/calendar/tasks/{task_id}")
async def miniapp_calendar_tasks_get(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    row = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    # Исполнитель открыл поручение (ссылка из Telegram, календарь) — постановщик видит «просмотрено».
    row = await run_in_threadpool(calendar_tasks_store.mark_seen, uid, task_id) or row
    return {"ok": True, "task": _task_api(row, tz=tz, viewer_id=uid)}


@miniapp_router.post("/calendar/tasks/parse")
async def miniapp_calendar_tasks_parse(
    body: _MiniappCalendarTaskBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    raw = str(body.text or body.title or "").strip()
    try:
        parsed = calendar_tasks_store.parse_slash_when(raw, tz=tz)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": True,
        "title": parsed["title"],
        "start": parsed["start"].isoformat(),
        "end": parsed["end"].isoformat(),
    }


def _find_reusable_note_task(
    uid: int,
    note_id: str,
    title: str,
    start_raw: str,
    tz: Any,
) -> dict[str, Any] | None:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    nid = str(note_id or "").strip()
    if not nid:
        return None
    want = _norm_task_title(title)
    start = calendar_tasks_store._parse_dt(start_raw, tz)
    rows = calendar_tasks_store.list_tasks_for_note(uid, nid)
    if start is not None and start.tzinfo is not None:
        start = start.astimezone(tz)
    if want:
        for row in rows:
            if _norm_task_title(str(row.get("title") or "")) == want:
                return row
    if start is None:
        return None
    for row in rows:
        rs = calendar_tasks_store._parse_dt(row.get("start_at"))
        if rs is None:
            continue
        if rs.tzinfo is not None:
            rs = rs.astimezone(tz)
        if abs((rs - start).total_seconds()) < 60:
            return row
    return None


@miniapp_router.post("/calendar/tasks")
async def miniapp_calendar_tasks_create(
    body: _MiniappCalendarTaskBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    title = str(body.title or "").strip()
    start_raw = str(body.start or "").strip()
    if not start_raw and str(body.text or "").strip():
        try:
            parsed = calendar_tasks_store.parse_slash_when(str(body.text or ""), tz=tz)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        title = title or parsed["title"]
        start_raw = parsed["start"].isoformat()
        if not body.end:
            body.end = parsed["end"].isoformat()
    if not start_raw:
        raise HTTPException(status_code=400, detail="Укажите начало задачи")
    meeting_item = str(body.meeting_item or "").strip()
    if meeting_item and not str(body.note_id or "").isdigit():
        raise HTTPException(status_code=400, detail="Пункт встречи — только вместе с заметкой")
    if not meeting_item:
        reused = await run_in_threadpool(
            _find_reusable_note_task, uid, str(body.note_id or ""), title, start_raw, tz
        )
        if reused:
            return {"ok": True, "task": _task_api(reused, tz=tz, viewer_id=uid)}
    assignee_uid = _resolve_calendar_task_assignee(principal, body)
    create = partial(
        calendar_tasks_store.create_task,
        uid,
        title=title,
        description=body.description or "",
        result=body.result or "",
        start_at=start_raw,
        end_at=body.end,
        assignee_user_id=assignee_uid or uid,
        assignee_email=body.assignee_email or "",
        assignee_name=body.assignee_name or _principal_display_name(principal),
        checklist=body.checklist,
        note_id=body.note_id or "",
        all_day=bool(body.all_day),
        tz=tz,
    )
    from assistant.services import meeting_tasks

    if meeting_item:
        # «Беру» / «Поручить» из таблицы задач встречи: двое одновременно — задача достаётся первому
        create = partial(
            meeting_tasks.claim, uid, int(body.note_id), meeting_item, create,
            by=_principal_display_name(principal),
        )
    try:
        row = await run_in_threadpool(create)
    except meeting_tasks.Taken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    synced = await run_in_threadpool(_sync_task_google, uid, row, "create")
    out = synced or row
    if calendar_tasks_store.task_is_delegated(out):
        await run_in_threadpool(
            partial(
                calendar_tasks_store.add_event,
                out["id"],
                "created",
                actor_user_id=uid,
                actor_name=_principal_display_name(principal),
            )
        )
        await run_in_threadpool(
            partial(
                _notify_task_assignee,
                assignee_user_id=out.get("assignee_user_id") or "",
                task=out,
                assigner_name=_principal_display_name(principal),
            )
        )
    return {"ok": True, "task": _task_api(out, tz=tz, viewer_id=uid)}


@miniapp_router.patch("/calendar/tasks/{task_id}")
async def miniapp_calendar_tasks_patch(
    task_id: int,
    body: _MiniappCalendarTaskBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    existing = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    is_owner = str(existing.get("owner_user_id") or "") == str(uid)
    if body.owner_archived is not None and not is_owner:
        raise HTTPException(
            status_code=403, detail="Архивировать может только постановщик"
        )
    prev_assignee = str(existing.get("assignee_user_id") or "")
    was_declined = str(existing.get("assignee_triage") or "") == "declined"
    was_done = bool(existing.get("done"))
    assignee_uid = None
    # Пустые строки с фронта = «не менять», иначе исполнитель мог
    # затереть назначение при неполном payload.
    patch_assignee = body.assignee_user_id is not None or body.assignee_email is not None
    if patch_assignee:
        assignee_uid = _resolve_calendar_task_assignee(principal, body) or None
    start_raw = (body.start or "").strip() or None
    end_raw = (body.end or "").strip() or None
    try:
        row = await run_in_threadpool(
            partial(
                calendar_tasks_store.update_task,
                uid,
                task_id,
                title=body.title,
                description=body.description,
                result=body.result,
                start_at=start_raw,
                end_at=end_raw,
                assignee_user_id=assignee_uid if patch_assignee else None,
                assignee_email=body.assignee_email if patch_assignee else None,
                assignee_name=body.assignee_name if patch_assignee else None,
                checklist=body.checklist,
                note_id=body.note_id,
                done=body.done,
                owner_archived=body.owner_archived if is_owner else None,
                all_day=body.all_day,
                schedule_actor={
                    "by_user_id": str(uid),
                    "by_name": _principal_display_name(principal),
                },
                tz=tz,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if not is_owner:
        # Исполнитель что-то поменял в поручении — значит, видел его.
        row = await run_in_threadpool(calendar_tasks_store.mark_seen, uid, task_id) or row
    synced = await run_in_threadpool(_sync_task_google, uid, row, "update")
    out = synced or row
    new_assignee = str(out.get("assignee_user_id") or "")
    if (
        calendar_tasks_store.task_is_delegated(out)
        and new_assignee
        and new_assignee != prev_assignee
    ):
        await run_in_threadpool(
            partial(
                _notify_task_assignee,
                assignee_user_id=new_assignee,
                task=out,
                assigner_name=_principal_display_name(principal),
            )
        )
    elif was_declined and new_assignee == prev_assignee and str(out.get("assignee_triage") or "") == "inbox":
        # Постановщик снова выбрал того, кто сказал «не моё», — настаивает.
        name = _principal_display_name(principal)
        await run_in_threadpool(
            partial(calendar_tasks_store.add_event, out["id"], "insisted", actor_user_id=uid, actor_name=name)
        )
        from assistant.lib.task_notify import notify_task_insisted

        await run_in_threadpool(partial(notify_task_insisted, task=out, owner_name=name))
    # Исполнитель отметил задачу выполненной → пуш автору.
    if (
        body.done is True
        and not was_done
        and bool(out.get("done"))
        and calendar_tasks_store.task_is_delegated(out)
        and str(out.get("assignee_user_id") or "") == str(uid)
        and str(out.get("owner_user_id") or "") != str(uid)
    ):
        from assistant.lib.task_notify import notify_task_owner_done

        await run_in_threadpool(
            partial(
                calendar_tasks_store.add_event,
                out["id"],
                "done",
                actor_user_id=uid,
                actor_name=_principal_display_name(principal),
                text=str(out.get("result") or ""),
            )
        )
        await run_in_threadpool(
            partial(
                notify_task_owner_done,
                task=out,
                completer_name=_principal_display_name(principal),
            )
        )
    _memory_task_changed(out)
    return {"ok": True, "task": _task_api(out, tz=tz, viewer_id=uid)}


@miniapp_router.delete("/calendar/tasks/{task_id}")
async def miniapp_calendar_tasks_delete(
    task_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import calendar_tasks as calendar_tasks_store

    uid = int(principal.telegram_user_id)
    existing = await run_in_threadpool(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    await run_in_threadpool(_sync_task_google, uid, existing, "delete")
    ok = await run_in_threadpool(calendar_tasks_store.delete_task, uid, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Задача не найдена")
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
    from assistant.lib.calendar_attendees import enrich_contacts_calendar_flags
    from assistant.stores import contacts_store
    from assistant.stores import teams_store

    def _load() -> list:
        items = contacts_store.load_contacts(
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=_miniapp_tg_username(principal),
        )
        items = enrich_contacts_calendar_flags(list(items or []))
        teams_map = teams_store.teams_by_contact_email(int(principal.telegram_user_id))
        for row in items:
            key = contacts_store.contact_key(row)
            row["key"] = key
            row["teams"] = list(teams_map.get(key) or [])
        return items

    return {"items": await run_in_threadpool(_load)}


_CONTACT_MEETINGS_CACHE: dict[tuple[int, str], tuple[float, list[dict[str, Any]], bool]] = {}


def _contact_calendar_events(uid: int, email: str) -> tuple[list[dict[str, Any]], bool]:
    """Встречи пользователя с участником email за −90…+30 дней (кеш 2 минуты)."""
    from assistant.integrations import google_calendar_oauth
    from assistant.services import calendar_sources as cal_sources
    from assistant.services.calendar import _tz_for

    key = (int(uid), email)
    hit = _CONTACT_MEETINGS_CACHE.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1], hit[2]
    tz = _tz_for(uid)
    today = datetime.now(tz).date()
    start_d, end_d = today - timedelta(days=90), today + timedelta(days=30)
    win_start = datetime.combine(start_d, datetime.min.time(), tzinfo=tz)
    win_end = datetime.combine(end_d + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    connected = google_calendar_oauth.user_token_path(uid).is_file()
    events: list[dict[str, Any]] = []
    if connected:
        collected = []
        for norm in cal_sources.list_events_in_window(uid, win_start, win_end, query=email):
            raw = dict(norm.get("raw") or {})
            raw["_calendarId"] = str(norm.get("calendar_id") or raw.get("_calendarId") or "primary")
            collected.append(raw)
        events = _serialize_gcal_events(uid, collected, 1500)
    elif _miniapp_dev_mode_on():
        connected = True
        events = _local_dev_events_range(uid, start_d, end_d, str(tz))
    want = email.lower()
    mine = [
        e for e in events
        if e.get("entry_type") != "task"
        and any(str(a.get("email") or "").lower() == want for a in (e.get("attendees") or []))
    ]
    _CONTACT_MEETINGS_CACHE[key] = (time.time(), mine, connected)
    return mine, connected


@miniapp_router.get("/contacts/{key:path}/meetings")
async def miniapp_contact_meetings(
    key: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Карточка контакта: встречи с ним (по почте в приглашениях) и их материалы,
    плюс заметки/транскрипции, куда он добавлен вручную."""
    from assistant.stores import contacts_store
    from assistant.stores import meeting_links

    uid = int(principal.telegram_user_id)
    want = contacts_store.normalize_contact_key(key)

    def _run() -> dict[str, Any] | None:
        contact = None
        for row in contacts_store.load_contacts(telegram_user_id=uid, telegram_username=_miniapp_tg_username(principal)):
            if contacts_store.contact_key(row) == want or str(row.get("email") or "").lower() == want:
                contact = dict(row)
                break
        if contact is None:
            return None
        contact["key"] = contacts_store.contact_key(contact)
        email = str(contact.get("email") or "").strip().lower()
        events: list[dict[str, Any]] = []
        connected = True
        error = ""
        if email:
            try:
                events, connected = _contact_calendar_events(uid, email)
            except Exception as e:
                print(f"[contact_meetings] calendar failed uid={uid} err={e!r}")
                error = "Не удалось загрузить календарь"
        keys = [meeting_links.event_key(e.get("calendar_id"), e.get("id")) for e in events]
        mats = meeting_links.materials_for_events(uid, keys)
        now = datetime.now(timezone.utc)
        upcoming: list[dict[str, Any]] = []
        past: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for e, k in zip(events, keys):
            item = dict(e)
            item["materials"] = mats.get(k, [])
            seen_keys.add(k)
            st = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or ""
            try:
                sd = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
                if sd.tzinfo is None:
                    sd = sd.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            (upcoming if sd > now else past).append(item)
        upcoming.sort(key=lambda x: str((x.get("start") or {}).get("dateTime") or (x.get("start") or {}).get("date") or ""))
        past.sort(key=lambda x: str((x.get("start") or {}).get("dateTime") or (x.get("start") or {}).get("date") or ""), reverse=True)
        person_keys = [contact["key"], email, str(contact.get("name") or "").strip().lower()]
        manual = []
        for row in meeting_links.notes_for_person(uid, person_keys):
            if row.get("event_id") and meeting_links.event_key(row.get("calendar_id"), row.get("event_id")) in seen_keys:
                continue  # уже есть в списке встреч
            manual.append(row)
        return {
            "contact": contact,
            "connected": connected,
            "error": error,
            "upcoming": upcoming[:10],
            "past": past[:60],
            "past_total": len(past),
            "manual": manual,
        }

    res = await run_in_threadpool(_run)
    if res is None:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    return {"ok": True, **res}


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
        raise HTTPException(status_code=400, detail="Укажите имя и email или ник в Telegram")
    if item is None:
        raise HTTPException(status_code=400, detail="Не удалось сохранить контакт")
    item = {**item, "key": contacts_store.contact_key(item)}
    return {"item": item, "status": status}


class _MiniappContactsBulk(BaseModel):
    text: str = ""


@miniapp_router.post("/contacts/bulk-telegram")
async def miniapp_contacts_bulk_telegram(
    body: _MiniappContactsBulk,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Добавить контакты списком ников: имя из профиля Leo, почта Google — если человек подключал календарь."""
    from assistant.stores import contacts_store, note_members, telegram_registry

    def _resolve(nick: str) -> tuple[Optional[int], str, str]:
        uid = telegram_registry.lookup_user_id(nick) or note_members._lookup_profile_by_username(nick)
        if not uid:
            return None, "", ""
        profile = note_members.get_profile(uid) or {}
        name = note_members.profile_display_name(profile, fallback="")
        if name.startswith("@") or name == "Участник":
            name = ""
        email = ""
        try:
            from assistant.integrations.google_calendar_oauth import user_token_path
            from assistant.lib.calendar_user_lookup import account_email_for_user

            if user_token_path(int(uid)).is_file():
                email = str(account_email_for_user(int(uid)) or "")
        except Exception as e:
            print(f"[contacts_bulk] email nick={nick!r} err={e!r}")
        return int(uid), name, email

    def _run() -> list[dict[str, Any]]:
        return contacts_store.bulk_add_by_telegram(
            telegram_user_id=int(principal.telegram_user_id),
            telegram_username=_miniapp_tg_username(principal),
            text=body.text,
            resolve=_resolve,
        )

    results = await run_in_threadpool(_run)
    if not results:
        raise HTTPException(status_code=400, detail="Не нашёл ников — пример: @ivan, @maria")
    return {"results": results}


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
    from assistant.stores import teams_store

    old_key = contacts_store.normalize_contact_key(old_email)
    new_key = contacts_store.contact_key(item) or old_key
    if new_key and new_key != old_key:
        await run_in_threadpool(
            teams_store.rename_contact_email,
            int(principal.telegram_user_id),
            old_key,
            new_key,
        )
    await run_in_threadpool(
        teams_store.refresh_contact_telegram,
        int(principal.telegram_user_id),
        new_key,
    )
    return {"item": {**item, "key": new_key}}


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
    from assistant.stores import teams_store

    await run_in_threadpool(
        teams_store.remove_contact_from_all_teams,
        int(principal.telegram_user_id),
        target,
    )
    return {"ok": True}


@miniapp_router.get("/teams")
async def miniapp_teams_list(
    detailed: bool = Query(False),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import teams_store

    uid = int(principal.telegram_user_id)
    if detailed:
        items = await run_in_threadpool(teams_store.list_teams_detailed, uid)
    else:
        items = await run_in_threadpool(teams_store.list_teams, uid)
    return {"ok": True, "teams": items}


@miniapp_router.post("/teams")
async def miniapp_teams_create(
    body: _MiniappTeamCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import teams_store

    try:
        team = await run_in_threadpool(
            teams_store.create_team, int(principal.telegram_user_id), body.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "team": team}


@miniapp_router.patch("/teams/{team_id}")
async def miniapp_teams_rename(
    team_id: int,
    body: _MiniappTeamPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import teams_store

    try:
        team = await run_in_threadpool(
            teams_store.rename_team,
            int(principal.telegram_user_id),
            int(team_id),
            body.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    return {"ok": True, "team": team}


@miniapp_router.delete("/teams/{team_id}")
async def miniapp_teams_delete(
    team_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import teams_store

    ok = await run_in_threadpool(
        teams_store.delete_team, int(principal.telegram_user_id), int(team_id)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    return {"ok": True}


def _contact_exists_for_principal(principal: _MiniappPrincipal, email: str) -> bool:
    from assistant.stores import contacts_store

    key = (email or "").strip().lower()
    if not key:
        return False
    items = contacts_store.load_contacts(
        telegram_user_id=int(principal.telegram_user_id),
        telegram_username=_miniapp_tg_username(principal),
    )
    norm = contacts_store.normalize_contact_key(key)
    return any(contacts_store.contact_key(c) == norm for c in items or [])


@miniapp_router.get("/contacts/{email}/teams")
async def miniapp_contact_teams_get(
    email: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import teams_store

    target = unquote(email or "").strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="Некорректный email")
    exists = await run_in_threadpool(_contact_exists_for_principal, principal, target)
    if not exists:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    team_ids = await run_in_threadpool(
        teams_store.list_contact_team_ids, int(principal.telegram_user_id), target
    )
    teams = await run_in_threadpool(
        teams_store.list_teams, int(principal.telegram_user_id)
    )
    return {"ok": True, "team_ids": team_ids, "teams": teams}


@miniapp_router.put("/contacts/{email}/teams")
async def miniapp_contact_teams_put(
    email: str,
    body: _MiniappContactTeamsBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import teams_store

    target = unquote(email or "").strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="Некорректный email")
    exists = await run_in_threadpool(_contact_exists_for_principal, principal, target)
    if not exists:
        raise HTTPException(status_code=404, detail="Контакт не найден")

    def _run() -> dict[str, Any]:
        return teams_store.set_contact_teams(
            int(principal.telegram_user_id), target, list(body.team_ids or [])
        )

    try:
        result = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await run_in_threadpool(
        partial(
            _notify_team_members_added,
            result.get("added_members") or [],
            _principal_display_name(principal),
        )
    )
    return {
        "ok": True,
        "team_ids": result.get("team_ids") or [],
        "contacts_synced": result.get("contacts_synced") or {},
        "teams": await run_in_threadpool(
            teams_store.list_teams, int(principal.telegram_user_id)
        ),
    }


@miniapp_router.get("/settings")
async def miniapp_settings_get(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import meeting_reminders as mr
    from assistant.services import meeting_record_schedule as mrec
    from assistant.services import task_reminders as tr
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        return {
            "meeting_reminders_enabled": user_prefs.meeting_reminders_enabled(uid),
            "meeting_reminder_minutes_before": mr.reminder_minutes_before(),
            "task_reminders_enabled": user_prefs.task_reminders_enabled(uid),
            "task_reminder_minutes_before": tr.reminder_minutes_before(),
            "task_assigned_notify": user_prefs.task_assigned_notify(uid),
            "discussion_notifications_enabled": user_prefs.discussion_notifications_enabled(
                uid
            ),
            "discussion_notify_telegram": user_prefs.discussion_notify_telegram(uid),
            "discussion_notify_web_push": user_prefs.discussion_notify_web_push(uid),
            "zoom_auto_record_enabled": user_prefs.zoom_auto_record_enabled(uid),
            "telemost_auto_record_enabled": user_prefs.telemost_auto_record_enabled(uid),
            "meeting_bot_available": mrec.service_available(),
            "digest_enabled": user_prefs.digest_enabled(uid),
            "digest_chat_ids": user_prefs.digest_chat_ids(uid),
            "miniapp_dev": _miniapp_dev_mode_on(),
        }

    return await run_in_threadpool(_run)


class _MiniappTgLeoPatch(BaseModel):
    model: str = ""


def _tg_leo_payload(uid: int) -> dict[str, Any]:
    from assistant.integrations.openrouter_client import tg_leo_models
    from assistant.stores import chat_threads as chat_threads_store
    from assistant.stores import user_prefs

    return {
        "model": user_prefs.get_tg_leo_model(uid),
        "models": tg_leo_models(),
        "thread": chat_threads_store.telegram_thread(uid),
    }


@miniapp_router.get("/profile/telegram-leo")
async def miniapp_tg_leo_get(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Лео в Telegram: модель и скрытый тред с перепиской."""
    return await run_in_threadpool(_tg_leo_payload, int(principal.telegram_user_id))


@miniapp_router.patch("/profile/telegram-leo")
async def miniapp_tg_leo_patch(
    body: _MiniappTgLeoPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)
    try:
        await run_in_threadpool(user_prefs.set_tg_leo_model, uid, body.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await run_in_threadpool(_tg_leo_payload, uid)


@miniapp_router.patch("/settings")
async def miniapp_settings_patch(
    body: _MiniappSettingsPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import meeting_reminders as mr
    from assistant.services import meeting_record_schedule as mrec
    from assistant.services import task_reminders as tr
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        if body.meeting_reminders_enabled is not None:
            user_prefs.set_meeting_reminders_enabled(
                uid, bool(body.meeting_reminders_enabled)
            )
        if body.task_reminders_enabled is not None:
            user_prefs.set_task_reminders_enabled(
                uid, bool(body.task_reminders_enabled)
            )
        if body.task_assigned_notify is not None:
            user_prefs.set_task_assigned_notify(uid, bool(body.task_assigned_notify))
        if body.discussion_notifications_enabled is not None:
            user_prefs.set_discussion_notifications_enabled(
                uid, bool(body.discussion_notifications_enabled)
            )
        if body.discussion_notify_telegram is not None:
            user_prefs.set_discussion_notify_telegram(
                uid, bool(body.discussion_notify_telegram)
            )
        if body.discussion_notify_web_push is not None:
            user_prefs.set_discussion_notify_web_push(
                uid, bool(body.discussion_notify_web_push)
            )
        if body.zoom_auto_record_enabled is not None:
            user_prefs.set_zoom_auto_record_enabled(
                uid, bool(body.zoom_auto_record_enabled)
            )
        if body.telemost_auto_record_enabled is not None:
            user_prefs.set_telemost_auto_record_enabled(
                uid, bool(body.telemost_auto_record_enabled)
            )
        if body.digest_enabled is not None:
            user_prefs.set_digest_enabled(uid, bool(body.digest_enabled))
        if body.digest_chat_ids is not None:
            user_prefs.set_digest_chat_ids(uid, list(body.digest_chat_ids))
        return {
            "ok": True,
            "meeting_reminders_enabled": user_prefs.meeting_reminders_enabled(uid),
            "meeting_reminder_minutes_before": mr.reminder_minutes_before(),
            "task_reminders_enabled": user_prefs.task_reminders_enabled(uid),
            "task_reminder_minutes_before": tr.reminder_minutes_before(),
            "task_assigned_notify": user_prefs.task_assigned_notify(uid),
            "discussion_notifications_enabled": user_prefs.discussion_notifications_enabled(
                uid
            ),
            "discussion_notify_telegram": user_prefs.discussion_notify_telegram(uid),
            "discussion_notify_web_push": user_prefs.discussion_notify_web_push(uid),
            "zoom_auto_record_enabled": user_prefs.zoom_auto_record_enabled(uid),
            "telemost_auto_record_enabled": user_prefs.telemost_auto_record_enabled(uid),
            "meeting_bot_available": mrec.service_available(),
            "digest_enabled": user_prefs.digest_enabled(uid),
            "digest_chat_ids": user_prefs.digest_chat_ids(uid),
        }

    return await run_in_threadpool(_run)


@miniapp_router.get("/push/vapid-public-key")
async def miniapp_push_vapid_public_key(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    del principal
    from assistant.services.discussion_notify import vapid_configured, vapid_public_key

    if not vapid_configured():
        raise HTTPException(status_code=503, detail="Web Push не настроен")
    return {"ok": True, "public_key": vapid_public_key()}


@miniapp_router.post("/push/subscribe")
async def miniapp_push_subscribe(
    body: _MiniappPushSubscribeBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import push_subscriptions

    keys = body.keys or _MiniappPushSubscribeKeys()
    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        return push_subscriptions.upsert(
            uid,
            endpoint=body.endpoint,
            p256dh=keys.p256dh,
            auth=keys.auth,
            user_agent=body.user_agent,
        )

    try:
        row = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "endpoint": row.get("endpoint")}


@miniapp_router.delete("/push/subscribe")
async def miniapp_push_unsubscribe(
    body: _MiniappPushUnsubscribeBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import push_subscriptions

    uid = int(principal.telegram_user_id)
    ok = await run_in_threadpool(
        push_subscriptions.delete, body.endpoint, user_id=uid
    )
    return {"ok": True, "deleted": bool(ok)}


@miniapp_router.get("/digest/chats")
async def miniapp_digest_chats(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        selected = set(user_prefs.digest_chat_ids(uid))
        chats = digest_store.list_chats_for_user(uid)
        for c in chats:
            c["selected"] = int(c["chat_id"]) in selected
        return {
            "digest_enabled": user_prefs.digest_enabled(uid),
            "chats": chats,
            "miniapp_dev": _miniapp_dev_mode_on(),
        }

    return await run_in_threadpool(_run)


@miniapp_router.get("/digest/reports")
async def miniapp_digest_reports(
    limit: int = 60,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        if not user_prefs.digest_enabled(uid):
            return {"digest_enabled": False, "reports": []}
        selected = user_prefs.digest_chat_ids(uid)
        reports = digest_store.list_reports_for_chats(selected, limit=limit)
        try:
            from assistant.services import proactive, proactive_ai

            hidden = proactive.dismissed_keys(uid)
            counts: dict[tuple[int, str], int] = {}
            for c in proactive_ai.chat_cards(uid):
                if c["key"] in hidden:
                    continue
                k = (int(c["chat"]["id"]), str(c["chat"]["date"]))
                counts[k] = counts.get(k, 0) + 1
            for r in reports:
                n = counts.get((int(r.get("chat_id") or 0), str(r.get("report_date") or "")), 0)
                if n:
                    r["important_count"] = n
        except Exception as e:
            print(f"[digest] important counts failed uid={uid} err={e!r}")
        return {"digest_enabled": True, "reports": reports}

    return await run_in_threadpool(_run)


@miniapp_router.get("/digest/overview")
async def miniapp_digest_overview(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«Дайджест» → «Чаты»: список по чатам (только отмеченные в профиле; /digest/chats — список для настроек) — последняя главная мысль и метки (ждут ответа, эскалация, вы обещали)."""
    from assistant.services import digest_overview

    return {"ok": True, **await run_in_threadpool(digest_overview.overview, int(principal.telegram_user_id))}


@miniapp_router.get("/digest/chats/{chat_id}")
async def miniapp_digest_chat(chat_id: int, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Отчёт чата лентой по дням + «Ждут вашего ответа» (те же пункты, что в «Фокусе»)."""
    from assistant.services import digest_overview

    try:
        return {"ok": True, **await run_in_threadpool(digest_overview.chat, int(principal.telegram_user_id), chat_id)}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@miniapp_router.post("/digest/item")
async def miniapp_digest_item(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«Ответил» / «Не мне» / «Сделано» из дайджеста — закрывает тот же пункт «Фокуса»."""
    from assistant.services import digest_overview

    uid = int(principal.telegram_user_id)
    try:
        row = await run_in_threadpool(partial(digest_overview.item_op, uid, str(body.get("key") or ""),
                                              str(body.get("op") or ""), by=_principal_display_name(principal)))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, "item": row}


@miniapp_router.post("/digest/step-task")
async def miniapp_digest_step_task(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«В задачу» из «Следующих шагов» отчёта чата: задача поставлена — связь пункта с ней (кнопка → чип)."""
    from assistant.services import digest_overview

    uid = int(principal.telegram_user_id)
    try:
        res = await run_in_threadpool(partial(
            digest_overview.step_converted, uid, str(body.get("key") or ""),
            task_id=body.get("task_id") or None, url=str(body.get("url") or ""), title=str(body.get("title") or ""),
            by=_principal_display_name(principal)))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, **res}


@miniapp_router.post("/digest/market/insight")
async def miniapp_digest_market_insight(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«Рынок»: «Что это значит для вас» — один раз на выпуск (кэш), по запросу пользователя."""
    from assistant.services import market_insight

    uid = int(principal.telegram_user_id)
    issue = str((body or {}).get("issue") or "")
    if body.get("cached_only"):
        return {"ok": True, "text": await run_in_threadpool(market_insight.cached, uid, issue)}
    try:
        text = await run_in_threadpool(partial(market_insight.insight, uid, issue, str(body.get("text") or ""),
                                               force=bool(body.get("force"))))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "text": text}


def _digest_people(uid: int, principal: Any, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Участники чата → контакты пользователя (по Telegram id или нику); себя не показываем."""
    from assistant.stores import contacts_store

    contacts = contacts_store.load_contacts(telegram_user_id=uid, telegram_username=_miniapp_tg_username(principal))
    by_id: dict[int, dict[str, Any]] = {}
    by_nick: dict[str, dict[str, Any]] = {}
    for c in contacts:
        try:
            tid = int(c.get("telegram_user_id") or 0)
        except (TypeError, ValueError):
            tid = 0
        if tid:
            by_id[tid] = c
        nick = contacts_store.normalize_telegram_username(str(c.get("telegram_username") or ""))
        if nick:
            by_nick[nick] = c
    out = []
    for p in people:
        if int(p.get("telegram_user_id") or 0) == int(uid):
            continue
        nick = contacts_store.normalize_telegram_username(str(p.get("username") or ""))
        c = by_id.get(int(p.get("telegram_user_id") or 0)) or (by_nick.get(nick) if nick else None)
        item = dict(p)
        if c:
            item["contact"] = {
                "key": contacts_store.contact_key(c),
                "name": c.get("name") or "",
                "email": c.get("email") or "",
                "telegram_username": c.get("telegram_username") or "",
            }
            item["name"] = str(c.get("name") or item.get("name") or "")
        out.append(item)
    return out


@miniapp_router.get("/digest/reports/{report_date}/{chat_id}")
async def miniapp_digest_report_detail(
    report_date: str,
    chat_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run():
        if not user_prefs.digest_enabled(uid):
            return {"_error": "off"}
        selected = set(user_prefs.digest_chat_ids(uid))
        if int(chat_id) not in selected:
            return {"_error": "not_selected"}
        row = digest_store.get_report(report_date, int(chat_id))
        if not row:
            return {"_error": "missing"}
        try:
            row = dict(row)
            row["people"] = _digest_people(uid, principal, digest_store.participants_for_day(int(chat_id), report_date))
            from assistant.services import proactive, proactive_ai

            hidden = proactive.dismissed_keys(uid)
            row["important"] = [
                c for c in proactive_ai.chat_cards(uid, chat_id=int(chat_id), day=report_date) if c["key"] not in hidden
            ]
        except Exception as e:
            print(f"[digest] people failed uid={uid} chat={chat_id} err={e!r}")
        return row

    result = await run_in_threadpool(_run)
    if not result or result.get("_error"):
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return result


@miniapp_router.patch("/digest/reports/{report_date}/{chat_id}")
async def miniapp_digest_report_patch(
    report_date: str,
    chat_id: int,
    body: _MiniappDigestReportPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        if not user_prefs.digest_enabled(uid):
            return {"_error": "off"}
        selected = set(user_prefs.digest_chat_ids(uid))
        if int(chat_id) not in selected:
            return {"_error": "not_selected"}
        row = digest_store.get_report(report_date, int(chat_id))
        if not row:
            return {"_error": "missing"}
        summary = dict(row.get("summary") or {})
        if body.title is not None:
            summary["editor_title"] = str(body.title or "").strip()[:500]
        if body.body_html is not None:
            summary["body_html"] = str(body.body_html or "")[:120000]
        updated = digest_store.update_report_summary(
            report_date, int(chat_id), summary
        )
        return updated or {"_error": "missing"}

    result = await run_in_threadpool(_run)
    if not result or result.get("_error"):
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return result


@miniapp_router.post("/digest/refresh")
async def miniapp_digest_refresh(
    body: Optional[_MiniappDigestRefreshBody] = None,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import chat_digest as digest_svc
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)
    body = body or _MiniappDigestRefreshBody()

    if not await run_in_threadpool(user_prefs.digest_enabled, uid):
        raise HTTPException(status_code=400, detail="Включите Дайджест в профиле")
    selected = await run_in_threadpool(user_prefs.digest_chat_ids, uid)
    if not selected:
        raise HTTPException(
            status_code=400, detail="Выберите хотя бы один чат в Профиль → Чаты"
        )
    if not digest_svc.try_begin_refresh(uid):
        raise HTTPException(status_code=409, detail="Обновление уже выполняется")

    try:
        uname = _miniapp_tg_username(principal)

        def _run() -> dict[str, Any]:
            return digest_svc.run_digest_for_user(
                uid,
                report_date=(body.date or "").strip() or None,
                telegram_username=uname,
            )

        result = await run_in_threadpool(_run)
    finally:
        digest_svc.end_refresh(uid)

    if not result.get("ok"):
        err = str(result.get("error") or "failed")
        if err == "digest_disabled":
            raise HTTPException(status_code=400, detail="Включите Дайджест в профиле")
        if err == "no_chats_selected":
            raise HTTPException(
                status_code=400, detail="Выберите хотя бы один чат в Профиль → Чаты"
            )
        raise HTTPException(status_code=500, detail=err)
    return result


@miniapp_router.post("/digest/demo-seed")
async def miniapp_digest_demo_seed(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    if not _miniapp_dev_mode_on():
        raise HTTPException(status_code=404, detail="Not found")
    from assistant.services import chat_digest as digest_svc

    uid = int(principal.telegram_user_id)
    return await run_in_threadpool(digest_svc.seed_demo_for_user, uid)


@miniapp_router.get("/agents")
async def miniapp_agents_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_agents

    uid = int(principal.telegram_user_id)
    rows = await run_in_threadpool(user_agents.list_agents, uid)
    return {"agents": rows}


@miniapp_router.post("/agents")
async def miniapp_agents_create(
    body: _MiniappAgentCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_agents

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        return user_agents.create_agent(
            uid,
            title=body.title,
            prompt=body.prompt,
            rules=body.rules,
            model=body.model,
            web_search=bool(body.web_search),
            temperature=body.temperature,
        )

    try:
        item = await run_in_threadpool(_run)
    except user_agents.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "agent": item}


@miniapp_router.patch("/agents/{agent_id}")
async def miniapp_agents_patch(
    agent_id: str,
    body: _MiniappAgentPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_agents

    uid = int(principal.telegram_user_id)
    fields = body.model_dump(exclude_unset=True)

    def _run() -> dict[str, Any]:
        return user_agents.patch_agent(uid, agent_id, fields)

    try:
        item = await run_in_threadpool(_run)
    except user_agents.AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except user_agents.AgentForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except user_agents.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "agent": item}


@miniapp_router.delete("/agents/{agent_id}")
async def miniapp_agents_delete(
    agent_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_agents

    uid = int(principal.telegram_user_id)

    def _run() -> None:
        user_agents.delete_agent(uid, agent_id)

    try:
        await run_in_threadpool(_run)
    except user_agents.AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except user_agents.AgentForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except user_agents.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@miniapp_router.post("/agents/{agent_id}/reset")
async def miniapp_agents_reset(
    agent_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import user_agents

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        return user_agents.reset_agent(uid, agent_id)

    try:
        item = await run_in_threadpool(_run)
    except user_agents.AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except user_agents.AgentForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except user_agents.AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "agent": item}


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


@miniapp_router.put("/calendar/entry-color")
async def miniapp_calendar_entry_color_put(
    body: _MiniappEntryColorBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Personal display color for a meeting/task (viewer-only, not shared)."""
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)
    key = str(body.entry_key or "").strip()
    if not key:
        key = user_prefs.calendar_entry_color_key(
            {
                "id": body.event_id or (f"task-{body.task_id}" if body.task_id else ""),
                "calendar_id": body.calendar_id or "primary",
                "task_id": body.task_id,
                "entry_type": "task" if body.task_id else "event",
            }
        )
    if not key:
        raise HTTPException(status_code=400, detail="Нет id записи")

    def _run() -> dict[str, Any]:
        colors = user_prefs.set_calendar_entry_color(uid, key, body.color)
        return {
            "ok": True,
            "entry_key": key,
            "color": colors.get(key) or None,
            "colors": colors,
        }

    try:
        return await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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


@miniapp_router.get("/calendar/range")
async def miniapp_calendar_range(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    start: Optional[str] = Query(
        None,
        description="Начало диапазона YYYY-MM-DD (включительно)",
    ),
    end: Optional[str] = Query(
        None,
        description="Конец диапазона YYYY-MM-DD (включительно)",
    ),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _calendar_range_payload,
        int(principal.telegram_user_id),
        start or from_date,
        end or to_date,
    )


@miniapp_router.post("/calendar/availability")
async def miniapp_calendar_availability(
    body: _MiniappCalendarAvailabilityBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import calendar_availability

    day = str(body.date or "").strip()[:10]
    if not day:
        raise HTTPException(status_code=400, detail="Укажите дату")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Дата должна быть YYYY-MM-DD") from None
    emails = _normalize_attendee_emails(body.attendees)
    refs = _normalize_attendee_refs(body.attendees)
    uname = _miniapp_tg_username(principal)

    def _run() -> dict[str, Any]:
        return calendar_availability(
            telegram_user_id=int(principal.telegram_user_id),
            day_iso=day,
            attendees=emails,
            telegram_username=uname,
            attendee_refs=refs,
        )

    try:
        return await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@miniapp_router.post("/calendar/booking-links")
async def miniapp_calendar_booking_link_create(
    body: _MiniappBookingLinkCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services.booking_links import BookingLinkError, create_booking_link

    def _run() -> dict[str, Any]:
        return create_booking_link(
            int(principal.telegram_user_id),
            title=body.title,
            attendees=_normalize_attendee_emails(body.attendees),
            duration_min=body.duration_min,
            link_mode=body.link_mode,
            expire_days=body.expire_days,
        )

    try:
        return await run_in_threadpool(_run)
    except BookingLinkError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        _raise_calendar_http(e)


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
    parsed["attendees"] = _normalize_attendee_emails(body.attendees)
    rules = _miniapp_event_recurrence(body)
    if rules:
        parsed["recurrence"] = rules
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
        _raise_calendar_http(e)
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
        "attendees": [{"email": e, "name": ""} for e in parsed.get("attendees") or []],
        "recurrence": list(created.get("recurrence") or []),
    }
    try:
        from assistant.services import meeting_invites as inv

        await inv.notify_invitees_for_miniapp(
            organizer_uid=int(principal.telegram_user_id),
            organizer_user=principal.user if isinstance(principal.user, dict) else {},
            result=created,
        )
    except Exception as e:
        print(f"[miniapp_calendar] invite_notify_create err={e!r}")
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
    if body.attendees is not None:
        parsed["attendees"] = _normalize_attendee_emails(body.attendees)
    rules = _miniapp_event_recurrence(body)
    if rules is not None:
        parsed["recurrence"] = rules
    if not parsed:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    eid = str(event_id or "").strip()
    cal_id = str(body.calendar_id or "").strip() or "primary"
    if _is_local_dev_event_id(eid) and _miniapp_dev_mode_on():
        from assistant.services.calendar import _tz_for

        tz = _tz_for(int(principal.telegram_user_id))
        ser = _put_local_dev_event(
            int(principal.telegram_user_id),
            eid,
            parsed,
            calendar_id=cal_id,
            timezone_name=str(tz),
        )
        return {"event": ser}

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
        _raise_calendar_http(e)
    if not isinstance(ev, dict):
        raise HTTPException(status_code=502, detail="Пустой ответ календаря")
    try:
        contacts_by_email = _miniapp_contacts_by_email(int(principal.telegram_user_id))
    except Exception:
        contacts_by_email = {}
    ser = _serialize_calendar_event(ev, contacts_by_email=contacts_by_email)
    if body.calendar_id and str(body.calendar_id).strip():
        ser["calendar_id"] = str(body.calendar_id).strip()
    emails = [str(e).strip().lower() for e in (ev.get("attendee_emails") or []) if str(e).strip()]
    if emails and not ser.get("attendees"):
        ser["attendees"] = [{"email": e, "name": ""} for e in emails]
    try:
        from assistant.services import meeting_invites as inv

        added = inv.added_invitees(ev)
        if added:
            await inv.notify_invitees_for_miniapp(
                organizer_uid=int(principal.telegram_user_id),
                organizer_user=principal.user if isinstance(principal.user, dict) else {},
                result=ev,
                only_emails=added,
            )
    except Exception as e:
        print(f"[miniapp_calendar] invite_notify_update err={e!r}")
    return {"event": ser}


@miniapp_router.get("/calendar/events/{event_id}/recurrence")
async def miniapp_calendar_event_recurrence(
    event_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    calendar_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Правило повтора встречи (у экземпляра — правило его серии)."""
    from assistant.services import calendar as cal_svc

    uid = int(principal.telegram_user_id)
    eid = str(event_id or "").strip()
    if _is_local_dev_event_id(eid):
        return {"recurrence": [], "series_id": ""}

    def _run() -> dict[str, Any]:
        ev = cal_svc.get_event(uid, eid, calendar_id=calendar_id)
        series_id = str(ev.get("recurringEventId") or "").strip()
        lines = ev.get("recurrence") or []
        if series_id:
            master = cal_svc.get_event(uid, series_id, calendar_id=calendar_id)
            lines = master.get("recurrence") or []
        rules = [str(x) for x in lines if str(x).strip().upper().startswith("RRULE:")]
        return {"recurrence": rules, "series_id": series_id}

    try:
        return await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        _raise_calendar_http(e)


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


@miniapp_router.post("/calendar/events/{event_id}/rsvp")
async def miniapp_calendar_event_rsvp(
    event_id: str,
    body: _MiniappCalendarRsvpBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import meeting_invites as inv

    status_map = {
        "accepted": inv.RSVP_ACCEPTED,
        "tentative": inv.RSVP_TENTATIVE,
        "declined": inv.RSVP_DECLINED,
        "yes": inv.RSVP_ACCEPTED,
        "maybe": inv.RSVP_TENTATIVE,
        "no": inv.RSVP_DECLINED,
        "приду": inv.RSVP_ACCEPTED,
        "возможно": inv.RSVP_TENTATIVE,
        "не приду": inv.RSVP_DECLINED,
    }
    raw = str(body.status or "").strip().lower()
    status = status_map.get(raw)
    if not status:
        raise HTTPException(status_code=400, detail="Укажите ответ: accepted, tentative или declined")
    eid = str(event_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail="Нет события")
    cal_id = str(body.calendar_id or "").strip() or "primary"

    if _is_local_dev_event_id(eid) and _miniapp_dev_mode_on():
        key = _local_dev_event_key(int(principal.telegram_user_id), eid)
        _LOCAL_DEV_RSVP[key] = status
        if status == inv.RSVP_DECLINED:
            return {"ok": True, "declined": True, "self_response_status": status}
        return {
            "ok": True,
            "self_response_status": status,
            "event": {
                "id": eid,
                "calendar_id": cal_id,
                "self_response_status": status,
                "needs_rsvp": False,
            },
        }

    def _run() -> dict[str, Any]:
        return inv.apply_invitee_rsvp(
            int(principal.telegram_user_id),
            event_id=eid,
            calendar_id=cal_id,
            response_status=status,
        )

    try:
        patched = await run_in_threadpool(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if status == inv.RSVP_DECLINED:
        return {"ok": True, "declined": True, "self_response_status": status}
    try:
        contacts_by_email = _miniapp_contacts_by_email(int(principal.telegram_user_id))
    except Exception:
        contacts_by_email = {}
    if isinstance(patched, dict) and not patched.get("_calendarId"):
        patched = dict(patched)
        patched["_calendarId"] = cal_id
    ser = _serialize_calendar_event(
        patched if isinstance(patched, dict) else {},
        contacts_by_email=contacts_by_email,
    )
    if cal_id:
        ser["calendar_id"] = cal_id
    return {"ok": True, "event": ser}


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


@miniapp_router.get("/gpt/models")
async def miniapp_gpt_models(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    del principal
    from assistant.integrations.openrouter_client import list_gpt_picker_models

    def _run() -> dict[str, Any]:
        return list_gpt_picker_models()

    return await run_in_threadpool(_run)


@miniapp_router.get("/paie/models")
async def miniapp_paie_models(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    del principal
    from assistant.integrations.openrouter_client import list_paie_picker_models

    def _run() -> dict[str, Any]:
        return list_paie_picker_models()

    return await run_in_threadpool(_run)


async def _start_chat_url_transcription(
    background_tasks: BackgroundTasks,
    body: _MiniappGptChatBody,
    *,
    uid: int,
    model: str | None,
    username: str | None,
) -> dict[str, Any] | None:
    """«<ссылка> транскрибируй» в треде чата: ответ сразу, транскрипция — в фоне."""
    from assistant.integrations.openrouter_client import gpt_display_label
    from assistant.services import chat_url_transcribe

    if str(body.item_kind or "").strip() != "chat" or not str(body.item_id or "").strip():
        return None
    req = chat_url_transcribe.detect_url_transcribe_request(body.message or "")
    if req is None:
        return None
    thread_id = str(body.item_id).strip()
    owner = await run_in_threadpool(_resolve_item_owner, str(uid), "chat", thread_id)
    if not owner or str(owner) != str(uid):
        return None
    print(f"[chat_url_transcribe] start uid={uid} thread={thread_id} url={req.url!r} summary={req.with_summary}")
    background_tasks.add_task(
        chat_url_transcribe.run_chat_url_transcription,
        uid,
        thread_id,
        req,
        model=model,
        username=username,
        prepare_audio=_prepare_miniapp_voice_audio,
    )
    return {
        "answer": chat_url_transcribe.accepted_text(req),
        "calendar_actions": [],
        "bullets": [],
        "kb_version": "",
        "files": [],
        "canvas": [],
        "canvas_error": None,
        "note_edits": None,
        "model": model or "",
        "model_label": gpt_display_label(model),
        "transcription": {"url": req.url, "with_summary": req.with_summary},
    }


@miniapp_router.post("/gpt/chat")
async def miniapp_gpt_chat(
    body: _MiniappGptChatBody,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.compat.miniapp_shims import gpt_openrouter_answer_with_context
    from assistant.integrations.openrouter_client import sanitize_openrouter_model_id

    q = (body.message or "").strip()
    file_ids = [int(x) for x in (body.file_ids or []) if int(x) > 0]
    if not q and not file_ids:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    hist: list[dict[str, str]] = []
    for it in body.history[-50:]:
        r = (it.role or "").strip().lower()
        if r not in ("user", "assistant"):
            continue
        c = (it.content or "").strip()
        if not c:
            continue
        hist.append({"role": r, "content": c[:24000]})
    model = sanitize_openrouter_model_id(body.model)
    agent_id = str(body.agent_id or "").strip() or "gpt"

    tg_uname: str | None = None
    if isinstance(principal.user, dict):
        ru = principal.user.get("username")
        if ru:
            tg_uname = str(ru).strip() or None

    def _load_prompt_media() -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
        from assistant.stores import comment_files

        images: list[dict[str, str]] = []
        files: list[dict[str, str]] = []
        notes: list[str] = []
        uid = str(int(principal.telegram_user_id))
        for fid in file_ids[:8]:
            item = comment_files.get_file(fid)
            if not item:
                continue
            if item["author_user_id"] != uid and item["owner_user_id"] != uid:
                continue
            if not _comment_file_visible(item, uid):
                continue
            label = str(item.get("filename") or "file")
            if str(item.get("kind") or "") == "image":
                path = comment_files.disk_path(fid)
                if not path.is_file():
                    continue
                blob = path.read_bytes()
                if len(blob) > 5 * 1024 * 1024:
                    continue
                import base64

                images.append(
                    {
                        "mime": str(item.get("mime") or "image/jpeg"),
                        "b64": base64.b64encode(blob).decode("ascii"),
                    }
                )
                notes.append("Изображение: " + label)
                continue
            if comment_files.is_pdf(item):
                path = comment_files.disk_path(fid)
                if path.is_file():
                    blob = path.read_bytes()
                    if 0 < len(blob) <= comment_files.MAX_FILE_BYTES:
                        import base64

                        files.append(
                            {
                                "filename": label if label.lower().endswith(".pdf") else f"{label}.pdf",
                                "mime": "application/pdf",
                                "b64": base64.b64encode(blob).decode("ascii"),
                            }
                        )
                        notes.append(
                            f"PDF «{label}» приложен к запросу целиком. "
                            "Отвечай по его содержимому, не проси прислать файл или скрины."
                        )
                        continue
            preview = comment_files.extract_text_preview(item)
            if preview:
                notes.append(f"Файл «{label}»:\n{preview}")
            else:
                notes.append(f"Прикреплён файл «{label}» ({item.get('mime') or 'file'}).")
        return images, files, "\n\n".join(notes).strip()

    def _save_generated(out: dict[str, Any]) -> list[dict[str, Any]]:
        from assistant.stores import comment_files

        kind = str(body.item_kind or "").strip()
        iid = str(body.item_id or "").strip()
        if kind not in ("local", "journal", "chat", "digest") or not iid:
            return []
        owner = _resolve_item_owner(str(int(principal.telegram_user_id)), kind, iid)
        if not owner:
            return []
        uid = int(principal.telegram_user_id)
        saved: list[dict[str, Any]] = []
        answer = str(out.get("answer") or "")
        cleaned, blocks = comment_files.parse_generated_file_blocks(answer)
        if blocks:
            out["answer"] = cleaned
        for name, text in blocks:
            decoded = comment_files.decode_generated_file_bytes(name, text)
            try:
                if decoded:
                    blob, mime = decoded
                    row = comment_files.save_bytes(
                        owner_user_id=owner,
                        item_kind=kind,
                        item_id=iid,
                        author_user_id=uid,
                        filename=name,
                        mime=mime,
                        data=blob,
                    )
                else:
                    row = comment_files.save_bytes(
                        owner_user_id=owner,
                        item_kind=kind,
                        item_id=iid,
                        author_user_id=uid,
                        filename=name,
                        mime="application/octet-stream",
                        data=text.encode("utf-8"),
                    )
            except ValueError:
                try:
                    stem = str(name or "file").rsplit(".", 1)[0] or "file"
                    row = comment_files.save_bytes(
                        owner_user_id=owner,
                        item_kind=kind,
                        item_id=iid,
                        author_user_id=uid,
                        filename=f"{stem}.txt",
                        mime="text/plain",
                        data=text.encode("utf-8"),
                    )
                except Exception as e:
                    print(f"[gpt_files] save_txt_failed name={name!r} err={e!r}")
                    continue
            except Exception as e:
                print(f"[gpt_files] save_failed name={name!r} err={e!r}")
                continue
            saved.append(_comment_file_api(row))
        import base64

        for i, img in enumerate(out.get("images") or []):
            if not isinstance(img, dict):
                continue
            b64 = str(img.get("b64") or "").strip()
            mime = str(img.get("mime") or "image/png").split(";")[0]
            if not b64:
                continue
            try:
                blob = base64.b64decode(b64)
            except Exception as e:
                print(f"[gpt_files] image_decode_failed i={i} err={e!r}")
                continue
            ext = "png"
            if "jpeg" in mime or "jpg" in mime:
                ext = "jpg"
            elif "webp" in mime:
                ext = "webp"
            elif "gif" in mime:
                ext = "gif"
            filename = str(img.get("filename") or "").strip() or f"gpt-image-{i + 1}.{ext}"
            try:
                row = comment_files.save_bytes(
                    owner_user_id=owner,
                    item_kind=kind,
                    item_id=iid,
                    author_user_id=uid,
                    filename=filename,
                    mime=mime,
                    data=blob,
                )
            except Exception as e:
                print(f"[gpt_files] image_save_failed name={filename!r} err={e!r}")
                continue
            saved.append(_comment_file_api(row))
        return saved

    from assistant.stores import user_agents

    uid = int(principal.telegram_user_id)
    try:
        agent = user_agents.runtime_config(uid, agent_id)
    except user_agents.AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if agent["kind"] not in ("gpt", user_agents.CUSTOM_KIND):
        raise HTTPException(status_code=400, detail="Этот агент запускается из своего режима")
    # Календарь для ИИ: инструменты работают с календарём спрашивающего (не владельца заметки).
    from assistant.services import gpt_calendar_tools as gcal_tools
    from assistant.services import leo_agent

    gcal_ctx = leo_agent.make_calendar_context(
        uid, telegram_username=tg_uname, display_name=_principal_display_name(principal)
    )
    gcal_tools.seed_quoted_tasks(gcal_ctx, [body.quote or "", *(body.quotes or [])])
    run_model = model or sanitize_openrouter_model_id(str(agent.get("model") or ""))
    tg_leo_thread = False
    if str(body.item_kind or "") == "chat" and agent_id == "gpt":
        from assistant.stores import chat_threads as chat_threads_store

        th = chat_threads_store.get_thread(uid, str(body.item_id or ""))
        tg_leo_thread = bool(th and th.get("origin") == chat_threads_store.TELEGRAM_ORIGIN)
    if tg_leo_thread:
        # Тред «Лео · Telegram»: модель из Профиля, агента приложения не трогаем.
        from assistant.stores import user_prefs

        run_model = user_prefs.get_tg_leo_model(uid)
    elif (
        model
        and model != str(agent.get("model") or "")
        and agent["kind"] in ("gpt", user_agents.CUSTOM_KIND)
    ):
        try:
            user_agents.patch_agent(uid, str(agent["id"]), {"model": model})
        except user_agents.AgentError as e:
            print(f"[agents] save model failed uid={uid} agent={agent['id']} err={e!r}")

    url_job = await _start_chat_url_transcription(
        background_tasks, body, uid=uid, model=run_model, username=tg_uname
    )
    if url_job is not None:
        return url_job

    def _run() -> dict[str, Any] | None:
        from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
        from assistant.nlu.ask_context import pack_attached_notes, pack_knowledge_brief

        kb_brief = ""
        kb_version = ""
        images, files, file_notes = _load_prompt_media()
        prompt = q or "Опиши вложение и ответь по нему."
        kind = str(body.item_kind or "").strip()
        iid = str(body.item_id or "").strip()
        if not images:
            from assistant.stores import comment_files as _cf

            if kind in ("local", "journal", "chat", "digest") and iid and (
                _cf.wants_image_delivery(prompt)
                or _cf.wants_resend_attachment(prompt)
            ):
                owner = _resolve_item_owner(str(uid), kind, iid)
                if owner:
                    import base64 as _b64

                    recent = [
                        it
                        for it in _cf.list_recent_images(
                            owner_user_id=owner,
                            item_kind=kind,
                            item_id=iid,
                            limit=8,
                        )
                        if _comment_file_visible(it, str(uid))
                    ][:2]
                    for item in recent:
                        path = _cf.disk_path(int(item["id"]))
                        if not path.is_file():
                            continue
                        blob = path.read_bytes()
                        if not blob or len(blob) > 5 * 1024 * 1024:
                            continue
                        images.append(
                            {
                                "mime": str(item.get("mime") or "image/jpeg"),
                                "b64": _b64.b64encode(blob).decode("ascii"),
                            }
                        )
                    if images and not file_notes:
                        file_notes = "Изображение из предыдущего сообщения в обсуждении."
        kb_ids = body.knowledge_note_ids
        if kb_ids is not None:
            kb_ids = [str(x).strip() for x in kb_ids if str(x).strip()]
            use_kb = bool(kb_ids)
        else:
            use_kb = bool(body.use_knowledge)
        if use_kb:
            query = " ".join(
                p
                for p in (
                    (body.note_title or "").strip(),
                    (body.note_text or "").strip()[:800],
                )
                if p
            ) or prompt
            kb_brief, kb_version = pack_knowledge_brief(
                uid, query, note_ids=kb_ids if body.knowledge_note_ids is not None else None
            )
        attached_notes = pack_attached_notes(uid, body.context_note_ids)
        system_text = leo_agent.gpt_system_text(agent)
        if kind in ("local", "chat") and iid:
            owner_for_hint = _resolve_item_owner(str(uid), kind, iid)
            if owner_for_hint:
                hint = _discussion_participants_system_hint(owner_for_hint, kind, iid)
                if hint:
                    system_text = (system_text.rstrip() + "\n\n" + hint).strip() if system_text else hint
        canvas_enabled = kind == "local" and bool(iid)
        note_text_for_model = (body.note_text or "").strip()
        if kind == "local" and iid:
            try:
                facts = _note_table_facts(_notes_store_mod().get_accessible_note(uid, int(iid)))
            except (TypeError, ValueError):
                facts = ""
            if facts:
                note_text_for_model = (note_text_for_model + "\n\n" + facts).strip()
        doc_view = str(body.doc_view or "").strip()
        note_edit_enabled = kind == "local" and bool(iid) and bool(doc_view)
        if note_edit_enabled:
            from assistant.lib import note_edits as note_edits_lib

            system_text = (system_text.rstrip() + "\n\n" + note_edits_lib.system_addon()).strip()
            prompt = (prompt.rstrip() + "\n\n" + note_edits_lib.prompt_block(doc_view)).strip()
        open_canvas_sheet_id = str(body.canvas_sheet_id or "").strip()
        canvas_sel_dumps = [s.model_dump() for s in (body.canvas_selections or [])]
        if canvas_enabled:
            from assistant.lib import web_canvas as web_canvas_lib

            system_text = (
                (system_text.rstrip() + "\n\n" + web_canvas_lib.canvas_system_addon()).strip()
                if system_text
                else web_canvas_lib.canvas_system_addon()
            )
            ctx_sheet_id = open_canvas_sheet_id or next(
                iter(web_canvas_lib.selection_sheet_ids(canvas_sel_dumps)), ""
            )
            ctx_sheet_html = ""
            if ctx_sheet_id.isdigit():
                try:
                    from assistant.stores import note_sheets as note_sheets_store

                    ctx_sheet = note_sheets_store.get_extra_sheet(int(iid), int(ctx_sheet_id))
                    ctx_sheet_html = str((ctx_sheet or {}).get("body") or "")
                except (TypeError, ValueError):
                    ctx_sheet_html = ""
            open_block = web_canvas_lib.open_canvas_prompt_block(
                ctx_sheet_id, html=ctx_sheet_html
            )
            if open_block:
                prompt = (prompt.rstrip() + "\n\n" + open_block).strip()
            sel_block = web_canvas_lib.selections_prompt_block(canvas_sel_dumps)
            if sel_block:
                prompt = (prompt.rstrip() + "\n\n" + sel_block).strip()
            from assistant.lib import board_ai as board_ai_lib

            system_text = (system_text.rstrip() + "\n\n" + board_ai_lib.system_addon()).strip()
            open_board_id = str(body.board_sheet_id or "").strip()
            if open_board_id.isdigit():
                try:
                    from assistant.stores import note_sheets as note_sheets_store

                    board_sheet = note_sheets_store.get_extra_sheet(int(iid), int(open_board_id))
                except (TypeError, ValueError):
                    board_sheet = None
                if board_sheet and str(board_sheet.get("kind") or "") == "board":
                    prompt = (
                        prompt.rstrip() + "\n\n" + board_ai_lib.open_board_prompt_block(
                            open_board_id, str(board_sheet.get("title") or ""), board_sheet.get("body") or ""
                        )
                    ).strip()
        from assistant.integrations.openrouter_client import model_emits_images as _emits_images

        def board_intent_text(text: str) -> str:
            # Просьба нарисовать доску — не повод генерировать картинку.
            from assistant.lib import board_ai as board_ai_lib

            return "" if board_ai_lib.looks_like_board_request(text) else text

        calendar_tools_on = not _emits_images(run_model or "")
        if calendar_tools_on:
            system_text = leo_agent.append_system(system_text, leo_agent.calendar_addon(uid))
        meeting_line = _note_meeting_prompt_line(uid, kind, iid)
        if meeting_line:
            system_text = (str(system_text or "").rstrip() + "\n\n" + meeting_line).strip()
        tool_kwargs: dict[str, Any] = leo_agent.tool_kwargs(gcal_ctx, q) if calendar_tools_on else {}
        try:
            set_openrouter_usage_telegram_user(
                telegram_user_id=uid,
                telegram_username=tg_uname,
            )
            out = gpt_openrouter_answer_with_context(
                prompt,
                (body.context or "").strip(),
                history=hist or None,
                model=run_model,
                note_title=(body.note_title or "").strip(),
                note_text=note_text_for_model,
                quote=(body.quote or "").strip(),
                quotes=[str(x).strip() for x in (body.quotes or []) if str(x).strip()],
                knowledge_brief=kb_brief,
                attached_notes=attached_notes,
                images=images or None,
                files=files or None,
                file_notes=file_notes,
                system=system_text,
                temperature=agent.get("temperature"),
                web=bool(agent.get("web_search")),
                max_tokens=16384 if (canvas_enabled or note_edit_enabled) else None,
                # Картинку — только если попросил сам пользователь: в prompt приклеены лист заметки и канвас
                # (там base64-фото и любые слова), по ним картинка генерировалась почти к каждому ответу.
                intent_text=board_intent_text(q) if canvas_enabled else q,
                **tool_kwargs,
            )
            if out is None:
                return None
            leo_agent.attach_calendar_actions(gcal_ctx, out, uid)
            board_applied: list[dict[str, Any]] = []
            board_failed = False
            if canvas_enabled:
                # Доска — первой: парсер презентаций принимает и «голый» JSON.
                from assistant.lib import board_ai as board_ai_lib
                from assistant.stores import note_sheets as note_sheets_store
                from assistant.stores import notes as notes_store

                b_clean, b_actions, b_tried = board_ai_lib.parse_blocks(str(out.get("answer") or ""))
                if b_tried:
                    out["answer"] = b_clean
                    try:
                        b_note = notes_store.get_accessible_note(uid, int(iid))
                    except (TypeError, ValueError):
                        b_note = None
                    for act in b_actions[:2] if b_note else []:
                        try:
                            sheet = board_ai_lib.apply_action(
                                b_note, act, note_sheets_store, open_sheet_id=str(body.board_sheet_id or "").strip()
                            )
                        except Exception as e:
                            print(f"[board_ai] apply failed note={iid} err={e!r}")
                            sheet = None
                        if sheet:
                            board_applied.append({
                                "action": str(act.get("action") or "create"),
                                "sheet_id": str(sheet.get("id")),
                                "title": str(sheet.get("title") or ""),
                                "kind": "board",
                                "revision": sheet.get("revision"),
                            })
                    board_failed = not board_applied
                    print(f"[board_ai] note={iid} actions={len(b_actions)} applied={len(board_applied)}")
                    if board_applied and not str(out.get("answer") or "").strip():
                        out["answer"] = "Нарисовал на доске «" + board_applied[-1]["title"] + "»."
                    elif board_failed and not str(out.get("answer") or "").strip():
                        out["answer"] = "Не получилось нарисовать доску — попробуйте описать проще."
            if note_edit_enabled:
                # До разбора канваса: его парсер принимает и «голый» JSON.
                clean_answer, edits = note_edits_lib.parse_blocks(str(out.get("answer") or ""))
                if edits:
                    out["answer"] = clean_answer or "Внёс изменения в заметку — проверьте подсвеченное."
                    out["note_edits"] = {"sheet_id": str(body.doc_sheet_id or ""), "edits": edits}
                    print(f"[note_edits] note={iid} sheet={body.doc_sheet_id} edits={len(edits)}")
                elif "```leo-note" in str(out.get("answer") or "").lower():
                    out["answer"] = clean_answer or "Не получилось подготовить правки — попробуйте сформулировать иначе."
            if canvas_enabled:
                from assistant.lib import web_canvas as web_canvas_lib
                from assistant.stores import note_sheets as note_sheets_store
                from assistant.stores import notes as notes_store

                answer_raw = str(out.get("answer") or "")
                clean_answer, canvas_actions = web_canvas_lib.parse_leo_canvas_blocks(answer_raw)
                applied: list[dict[str, Any]] = []
                try:
                    note_id_int = int(iid)
                except (TypeError, ValueError):
                    note_id_int = 0
                note = (
                    notes_store.get_accessible_note(uid, note_id_int)
                    if note_id_int
                    else None
                )
                if note and canvas_actions:
                    sel_sheet_ids = web_canvas_lib.selection_sheet_ids(canvas_sel_dumps)
                    for act in canvas_actions[:3]:
                        normalized: dict[str, Any] = {}
                        try:
                            normalized = web_canvas_lib.normalize_canvas_action_for_apply(
                                act,
                                selection_sheet_ids=sel_sheet_ids,
                                open_sheet_id=open_canvas_sheet_id,
                            )
                            sheet = web_canvas_lib.apply_canvas_action(
                                note=note,
                                action=normalized,
                                note_sheets_mod=note_sheets_store,
                                selection_sheet_ids=sel_sheet_ids,
                                open_sheet_id=open_canvas_sheet_id,
                                default_deck_theme=str(body.canvas_theme or "").strip()[:20] or None,
                            )
                        except Exception as e:
                            print(f"[web_canvas] apply failed err={e!r}")
                            continue
                        if sheet:
                            # Fill pending media slots from chat images / data-leo-img-prompt
                            try:
                                sheet = _materialize_canvas_sheet_images(
                                    note=note,
                                    sheet=sheet,
                                    chat_images=out.get("images") or [],
                                    note_sheets_mod=note_sheets_store,
                                )
                            except Exception as e:
                                print(f"[web_canvas] media_fill failed err={e!r}")
                            applied.append(
                                {
                                    "action": str(normalized.get("action") or "create"),
                                    "sheet_id": str(sheet.get("id")),
                                    "title": str(sheet.get("title") or ""),
                                    "kind": str(sheet.get("kind") or "web"),
                                    "revision": sheet.get("revision"),
                                }
                            )
                        else:
                            print(
                                "[web_canvas] apply returned None "
                                f"action={normalized.get('action')!r} "
                                f"sheet_id={normalized.get('sheet_id')!r}"
                            )
                if board_applied and not canvas_actions:
                    # Ответ про доску — не «ложное Готово» презентации.
                    out["answer"] = clean_answer or out.get("answer") or ""
                elif board_failed and not canvas_actions:
                    out["answer"] = "Не получилось нарисовать доску — попробуйте описать проще."
                else:
                    out["answer"] = web_canvas_lib.reconcile_canvas_chat_answer(
                        clean_answer,
                        actions=canvas_actions,
                        applied=applied,
                        raw_answer=answer_raw,
                    )
                if applied or board_applied:
                    out["canvas"] = applied + board_applied
                elif canvas_actions or web_canvas_lib.canvas_payload_attempted(answer_raw) or board_failed:
                    out["canvas_error"] = "apply_failed"
            if kb_version:
                out = {**out, "kb_version": kb_version}
            generated = _save_generated(out)
            if generated:
                out = {**out, "files": generated}
            return out
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
    from assistant.integrations.openrouter_client import gpt_display_label

    files = out.get("files") if isinstance(out.get("files"), list) else []
    used_model = str(out.get("model") or run_model or "").strip()
    canvas = out.get("canvas") if isinstance(out.get("canvas"), list) else []
    await leo_agent.notify_invitees(
        gcal_ctx, uid=uid, organizer_user=principal.user if isinstance(principal.user, dict) else None
    )
    return {
        "answer": str(out.get("answer") or ""),
        "calendar_actions": (
            out.get("calendar_actions") if isinstance(out.get("calendar_actions"), list) else []
        ),
        "bullets": out.get("bullets") if isinstance(out.get("bullets"), list) else [],
        "kb_version": str(out.get("kb_version") or ""),
        "files": files,
        "canvas": canvas,
        "canvas_error": str(out.get("canvas_error") or "") or None,
        "note_edits": out.get("note_edits") if isinstance(out.get("note_edits"), dict) else None,
        "model": used_model,
        "model_label": gpt_display_label(used_model),
    }


class _MiniappCalActionIds(BaseModel):
    ids: list[str] = []


@miniapp_router.post("/gpt/calendar-actions/status")
async def miniapp_gpt_calendar_actions_status(
    body: _MiniappCalActionIds,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Карточки действий ИИ с календарём для ответов в обсуждении (видны всем, менять — только автору)."""
    from assistant.services import gpt_calendar_tools as gcal_tools

    ids = [str(i).strip()[:32] for i in (body.ids or []) if str(i).strip()][:50]
    rows = await run_in_threadpool(gcal_tools.actions_by_ids, ids)
    uid = int(principal.telegram_user_id)
    return {"actions": [gcal_tools.public_action(a, viewer_uid=uid) for a in rows]}


@miniapp_router.post("/gpt/calendar-actions/{action_id}/{op}")
async def miniapp_gpt_calendar_action_op(
    action_id: str,
    op: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import gpt_calendar_tools as gcal_tools
    from assistant.services import leo_agent

    uid = int(principal.telegram_user_id)
    try:
        fresh, created = await run_in_threadpool(
            partial(
                leo_agent.apply_action_op,
                uid,
                action_id,
                op,
                telegram_username=_miniapp_tg_username(principal),
            )
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail="Действие не найдено") from e
    except leo_agent.ActionNeedsConfirm as e:
        raise HTTPException(status_code=409, detail={"message": str(e), "code": "new_recipients", "addrs": e.addrs}) from e
    except leo_agent.ActionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        print(f"[gpt_calendar] op={op} id={action_id} err={e!r}")
        raise HTTPException(status_code=502, detail=f"Не получилось: {e}") from e
    if created and created.get("attendee_emails"):
        try:
            from assistant.services import meeting_invites as inv

            await inv.notify_invitees_for_miniapp(
                organizer_uid=uid,
                organizer_user=principal.user if isinstance(principal.user, dict) else {},
                result=created,
            )
        except Exception as e:
            print(f"[gpt_calendar] slot invite err={e!r}")
    return {"action": gcal_tools.public_action(fresh, viewer_uid=uid)}


def _strip_speaker_labels(text: str) -> str:
    """Диктовка — один говорящий: «SPEAKER_1: …» / «Спикер 1:\n…» → просто текст."""
    from assistant.integrations.transcribe import strip_speaker_labels

    return strip_speaker_labels(text)


class _MiniappVoiceSettingsPatch(BaseModel):
    cleanup: Optional[bool] = None
    numbers: Optional[bool] = None
    dictionary: Optional[bool] = None
    snippets: Optional[bool] = None
    history: Optional[bool] = None
    dictionary_entries: Optional[list[dict[str, Any]]] = None
    snippet_entries: Optional[list[dict[str, Any]]] = None


@miniapp_router.get("/voice/settings")
async def miniapp_voice_settings_get(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Профиль → «Голосовой ввод»: переключатели, словарь, сниппеты."""
    from assistant.stores import voice_prefs

    return await run_in_threadpool(
        voice_prefs.get_settings, int(principal.telegram_user_id)
    )


@miniapp_router.patch("/voice/settings")
async def miniapp_voice_settings_patch(
    body: _MiniappVoiceSettingsPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import voice_prefs

    return await run_in_threadpool(
        voice_prefs.update_settings,
        int(principal.telegram_user_id),
        body.model_dump(exclude_none=True),
    )


@miniapp_router.get("/voice/history")
async def miniapp_voice_history_get(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import voice_prefs

    uid = int(principal.telegram_user_id)
    items = await run_in_threadpool(voice_prefs.list_history, uid)
    return {"items": items}


@miniapp_router.delete("/voice/history")
async def miniapp_voice_history_clear(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import voice_prefs

    await run_in_threadpool(voice_prefs.clear_history, int(principal.telegram_user_id))
    return {"ok": True, "items": []}


@miniapp_router.delete("/voice/history/{item_id}")
async def miniapp_voice_history_delete(
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import voice_prefs

    uid = int(principal.telegram_user_id)
    await run_in_threadpool(voice_prefs.delete_history_item, uid, item_id)
    items = await run_in_threadpool(voice_prefs.list_history, uid)
    return {"ok": True, "items": items}


@miniapp_router.post("/voice/transcribe")
async def miniapp_voice_transcribe(
    file: UploadFile = File(...),
    purpose: str = Form(""),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.integrations import transcribe as obu
    from assistant.lib.usage_store import insert_usage_event
    from assistant.stores import voice_prefs

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
        # Диктовка в заметку: один говорящий — без «Спикер 1:»; в журнал транскрипций не попадает.
        dictation = str(purpose or "").strip().lower() == "note"
        if dictation:
            transcript = _strip_speaker_labels(result.plain_text or result.formatted_text or "")
        else:
            transcript = (result.formatted_text or result.plain_text or "").strip()
        if not transcript:
            raise RuntimeError("(пустая транскрипция)")
        # Паразиты, числа, личный словарь, сниппеты — по настройкам «Голосового ввода».
        raw_transcript = transcript
        uid = int(principal.telegram_user_id)
        try:
            transcript = voice_prefs.process_text(uid, transcript, mode="dictation") or transcript
            voice_prefs.add_history(
                uid,
                text=transcript,
                raw_text=raw_transcript,
                source="note" if dictation else "assistant",
            )
        except Exception as e:
            print(f"[miniapp_voice_transcribe] postprocess_failed uid={uid} err={e!r}")
        event_id = insert_usage_event(
            operation="obuchat_dictation" if dictation else "obuchat_transcribe",
            model=None,
            generation_id=result.job_id,
            usage={
                "text": transcript,
                "source": "note_dictation" if dictation else "miniapp_voice",
                "filename": filename,
            },
            telegram_user_id=str(int(principal.telegram_user_id)),
            telegram_username=username,
        )
        return {
            "text": transcript,
            "plain_text": transcript,
            "raw_text": raw_transcript,
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


class _MiniappVoiceCommandBody(BaseModel):
    text: str = ""


_VOICE_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _voice_parse_date(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError:
        return None


def _voice_parse_time(raw: Any) -> dt_time | None:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})", str(raw or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return dt_time(h, mi)


def _voice_when_label(day: date, at: dt_time | None, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        label = "сегодня"
    elif delta == 1:
        label = "завтра"
    elif delta == 2:
        label = "послезавтра"
    else:
        label = f"{day.day} {_VOICE_RU_MONTHS[day.month - 1]}"
        if day.year != today.year:
            label += f" {day.year}"
    if at is not None:
        label += f" в {at.hour}:{at.minute:02d}"
    return label


@miniapp_router.post("/voice/command")
async def miniapp_voice_command(
    body: _MiniappVoiceCommandBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Наговорить» на «Актуальном»: задача / напоминание / встреча — создаём сразу;
    вопрос и всё неясное — клиент отправляет в чат с Ассистентом."""
    from assistant.nlu.llm import parse_voice_command
    from assistant.services.calendar import _tz_for

    text = str(body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст")
    uid = int(principal.telegram_user_id)
    tz = _tz_for(uid)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    today = now.date()
    try:
        parsed = await run_in_threadpool(partial(parse_voice_command, text, now=now))
    except Exception as e:
        print(f"[miniapp_voice_command] parse_failed uid={uid} err={e!r}")
        parsed = None
    parsed = parsed or {}
    kind = str(parsed.get("kind") or "").strip().lower()
    title = str(parsed.get("title") or "").strip()
    question = {"kind": "question", "text": text}
    if kind not in ("task", "reminder", "meeting") or not title:
        return question
    day = _voice_parse_date(parsed.get("date"))
    at = _voice_parse_time(parsed.get("time"))

    def _at(d: date, t: dt_time) -> datetime:
        out = datetime.combine(d, t, tzinfo=tz)
        # «в 10» без дня, а 10:00 уже прошло — значит, завтра.
        if day is None and out <= now:
            out += timedelta(days=1)
        return out

    if kind == "reminder":
        if at is not None:
            when = _at(day or today, at)
        elif day is not None and day > today:
            when = datetime.combine(day, dt_time(9, 0), tzinfo=tz)
        else:
            when = now + timedelta(hours=1)
        res = await miniapp_reminders_create(
            _MiniappReminderCreate(task=title, when_iso=when.isoformat()), principal
        )
        return {
            "kind": "reminder",
            "title": title,
            "when_label": _voice_when_label(when.date(), when.timetz(), today),
            "item": res.get("item"),
        }

    if kind == "task":
        if at is not None:
            start = _at(day or today, at)
            task_body = _MiniappCalendarTaskBody(title=title, start=start.isoformat())
            label = _voice_when_label(start.date(), start.timetz(), today)
        else:
            d = day if day is not None and day >= today else today
            start = datetime.combine(d, dt_time(0, 0), tzinfo=tz)
            task_body = _MiniappCalendarTaskBody(title=title, start=start.isoformat(), all_day=True)
            label = _voice_when_label(d, None, today)
        res = await miniapp_calendar_tasks_create(task_body, principal)
        return {"kind": "task", "title": title, "when_label": label, "task": res.get("task")}

    # Встреча без времени — не угадываем: пусть Ассистент уточнит в чате.
    if at is None:
        return question
    start = _at(day or today, at)
    end_t = _voice_parse_time(parsed.get("end_time"))
    end = datetime.combine(start.date(), end_t, tzinfo=tz) if end_t is not None else None
    if end is None or end <= start:
        try:
            dur = int(parsed.get("duration_min") or 0)
        except (TypeError, ValueError):
            dur = 0
        end = start + timedelta(minutes=dur if 5 <= dur <= 12 * 60 else 60)
    res = await miniapp_calendar_event_create(
        _MiniappCalendarEventUpdate(title=title, start=start.isoformat(), end=end.isoformat()),
        principal,
    )
    return {
        "kind": "meeting",
        "title": title,
        "when_label": _voice_when_label(start.date(), start.timetz(), today),
        "event": res.get("event"),
    }


def _project_from_tags(tags: list[dict[str, Any]]) -> dict[str, Any] | None:
    return tags[0] if tags else None


# Список /notes отдаёт только превью: иначе одна заметка с base64-картинками
# раздувает ответ до мегабайт и миниапп грузится десятки секунд.
_NOTE_LIST_BODY_MAX = 600
_HASHTAG_SYNC_BODY_MAX = 50_000
_DATA_URI_RE = re.compile(
    r"data:(?:image|video|audio|application)/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+",
    re.IGNORECASE,
)


def note_body_list_preview(
    text: str, *, max_len: int = _NOTE_LIST_BODY_MAX
) -> tuple[str, bool]:
    """Сжимает тело заметки для списка: без data-URI и с усечением."""
    raw = str(text or "")
    if not raw:
        return "", False
    cleaned = _DATA_URI_RE.sub("[image]", raw)
    truncated = cleaned != raw or len(cleaned) > max_len
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned, truncated


class _NoteBadgeParser(HTMLParser):
    """Считает пункты чек-листа и таблицы во всём теле заметки (до усечения)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lists: list[bool] = []
        self.total = 0
        self.done = 0
        self.has_table = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in ("ul", "ol"):
            cls = a.get("class", "")
            self.lists.append("note-task-list" in cls.split() or a.get("data-type") == "taskList")
        elif tag == "li" and self.lists and self.lists[-1]:
            self.total += 1
            if "checked" in a or a.get("data-checked") == "true":
                self.done += 1
        elif tag == "table":
            self.has_table = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()


def note_list_badges(body: str) -> dict[str, Any]:
    raw = _DATA_URI_RE.sub("", str(body or ""))
    out: dict[str, Any] = {"checklist_total": 0, "checklist_done": 0, "has_table": False}
    if not raw or ("<li" not in raw and "<table" not in raw):
        return out
    p = _NoteBadgeParser()
    try:
        p.feed(raw)
        p.close()
    except Exception:
        return out
    out.update(checklist_total=p.total, checklist_done=p.done, has_table=p.has_table)
    return out


def _plain_preview_text(h: str) -> str:
    t = _DATA_URI_RE.sub(" ", h or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_lib.unescape(t).replace("\xa0", " ")
    return " ".join(t.split())


def looks_like_list_preview_overwrite(new_body: str | None, old_body: str | None) -> bool:
    """Новый текст — это обрезанное превью из списка (600 симв. + «…»), а в базе полный текст.

    Защита от потери данных: если клиент открыл заметку по превью (не дождался полной
    версии) и сохранил её, не даём затереть полный текст.
    """
    if new_body is None or not old_body:
        return False
    old_raw = str(old_body)
    if len(old_raw) <= _NOTE_LIST_BODY_MAX:
        return False

    def _plain(h: str) -> str:
        t = _DATA_URI_RE.sub(" ", h or "")
        t = re.sub(r"<[^>]+>", " ", t)
        t = html_lib.unescape(t).replace("\xa0", " ")
        return " ".join(t.split())

    new_raw = str(new_body)
    # Превью — не длиннее 600 симв. (+ редактор закрывает теги). Длинный новый текст — обычная правка.
    if len(new_raw) > _NOTE_LIST_BODY_MAX + 160:
        return False
    new_plain = _plain(new_raw)
    head = new_plain.rstrip(" …").rstrip("…").strip()
    old_plain = _plain(old_raw)
    if len(head) < 20 or len(old_plain) < len(head) + 120:
        return False
    # Обрезка могла прийтись на середину слова/тега — сравниваем без последних символов.
    return old_plain.startswith(head[: max(20, len(head) - 12)])


def _attach_note_meetings(uid: int, notes: list[dict[str, Any]]) -> None:
    """Строчка встречи на карточке: тема, время, участники (своя привязка)."""
    try:
        from assistant.stores import meeting_links

        links = meeting_links.for_notes(uid, [n.get("id") for n in notes if isinstance(n, dict)])
    except Exception as e:
        print(f"[meeting_links] list attach failed uid={uid} err={e!r}")
        return
    for n in notes:
        try:
            m = links.get(int(n.get("id")))
        except (TypeError, ValueError):
            m = None
        if m:
            n["meeting"] = {
                "event_id": m["event_id"],
                "calendar_id": m["calendar_id"],
                "summary": m["summary"],
                "start_iso": m["start_iso"],
                "end_iso": m["end_iso"],
                "people": [a.get("name") or a.get("email") for a in m.get("attendees") or []][:6],
            }


def minify_note_for_list(item: dict[str, Any]) -> dict[str, Any]:
    """Оставляет в list-payload только превью body/description."""
    if not isinstance(item, dict):
        return item
    item.update(note_list_badges(str(item.get("body") or item.get("description") or "")))
    touched = False
    for key in ("body", "description"):
        if key not in item or item.get(key) is None:
            continue
        preview, truncated = note_body_list_preview(str(item.get(key) or ""))
        item[key] = preview
        if truncated:
            touched = True
    if touched:
        item["body_preview_only"] = True
    return item


def _apply_project_fields(item: dict[str, Any], tags: list[dict[str, Any]]) -> None:
    project_tags = tags[:1]
    item["tags"] = project_tags
    item["project"] = _project_from_tags(project_tags)


def _sync_local_note_hashtags(uid: int | str, item: dict[str, Any]) -> dict[str, Any]:
    from assistant.stores import hashtags as hashtags_store
    from assistant.stores import tags as tags_store

    nid = item.get("id")
    if nid is None:
        return item
    title = str(item.get("title") or item.get("content") or "")
    body = str(item.get("body") or item.get("description") or "")
    item["hashtags"] = hashtags_store.sync_item_hashtags(uid, "local", nid, title, body)
    tags = tags_store.get_item_tags(uid, "local", nid)
    _apply_project_fields(item, tags)
    return item


def _sync_journal_hashtags(
    uid: int | str, event_id: int | str, item: dict[str, Any]
) -> dict[str, Any]:
    from assistant.stores import hashtags as hashtags_store
    from assistant.stores import tags as tags_store

    title = str(item.get("main_topic") or item.get("title") or "")
    body = str(item.get("body") or item.get("preview") or "")
    item["hashtags"] = hashtags_store.sync_item_hashtags(
        uid, "journal", event_id, title, body
    )
    tags = tags_store.get_item_tags(uid, "journal", event_id)
    _apply_project_fields(item, tags)
    return item


def _enrich_item_labels(
    uid: int | str, item_kind: str, item: dict[str, Any]
) -> dict[str, Any]:
    from assistant.stores import hashtags as hashtags_store
    from assistant.stores import tags as tags_store

    iid = item.get("id")
    if iid is None:
        item.setdefault("tags", [])
        item.setdefault("project", None)
        item.setdefault("hashtags", [])
        return item
    tags = tags_store.get_item_tags(uid, item_kind, iid)
    _apply_project_fields(item, tags)
    item["hashtags"] = hashtags_store.get_item_hashtags(uid, item_kind, iid)
    return item


@miniapp_router.get("/chats")
async def miniapp_chats_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    limit: int = 100,
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store

    uid = int(principal.telegram_user_id)
    lim = min(max(int(limit), 1), 300)
    rows = await run_in_threadpool(partial(chat_threads_store.list_threads, uid, limit=lim))
    return {"ok": True, "chats": rows}


@miniapp_router.post("/chats")
async def miniapp_chats_create(
    body: Optional[_MiniappChatCreate] = Body(default=None),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store

    uid = int(principal.telegram_user_id)
    body = body or _MiniappChatCreate()
    row = await run_in_threadpool(
        partial(chat_threads_store.create_thread, uid, title=body.title, origin=body.origin)
    )
    return {"ok": True, "chat": row}


@miniapp_router.patch("/chats/{thread_id}")
async def miniapp_chats_patch(
    thread_id: int,
    body: _MiniappChatPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store

    uid = int(principal.telegram_user_id)
    title = (body.title or "").strip() if body.title is not None else None
    pinned = body.pinned
    if title is None and pinned is None:
        raise HTTPException(status_code=400, detail="Нечего обновлять")
    row = None
    try:
        if title is not None:
            row = await run_in_threadpool(
                partial(chat_threads_store.rename_thread, uid, thread_id, title, lock=True)
            )
        if pinned is not None:
            row = await run_in_threadpool(
                partial(chat_threads_store.set_pinned, uid, thread_id, bool(pinned))
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"ok": True, "chat": row}


@miniapp_router.delete("/chats/{thread_id}")
async def miniapp_chats_delete(
    thread_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store

    uid = int(principal.telegram_user_id)
    ok = await run_in_threadpool(chat_threads_store.delete_thread, uid, thread_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"ok": True}


@miniapp_router.get("/chats/{thread_id}/members")
async def miniapp_chat_members_list(
    thread_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store

    uid = int(principal.telegram_user_id)
    thread = await run_in_threadpool(
        chat_threads_store.get_accessible_thread, uid, thread_id
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"ok": True, "members": thread.get("members") or [], "chat": thread}


@miniapp_router.post("/chats/{thread_id}/members")
async def miniapp_chat_members_add(
    thread_id: int,
    body: _MiniappNoteMemberAdd,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store
    from assistant.stores import note_members

    uid = int(principal.telegram_user_id)
    await run_in_threadpool(_remember_principal_profile, principal)

    def _run() -> tuple[dict[str, Any], dict[str, Any], bool]:
        thread = chat_threads_store.get_thread(uid, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Чат не найден")
        owner = str(thread.get("owner_user_id") or uid)
        if str(uid) != owner:
            raise HTTPException(
                status_code=403, detail="Добавлять участников может только автор"
            )
        member_id, _contact = note_members.resolve_contact_telegram_id(
            owner_user_id=uid,
            email=body.email,
            telegram_user_id=body.telegram_user_id,
            telegram_username=body.telegram_username,
            owner_username=_miniapp_tg_username(principal),
        )
        if int(member_id) == int(owner):
            raise HTTPException(status_code=400, detail="Это владелец чата")
        was_member = chat_threads_store.is_member(owner, thread_id, member_id)
        member = chat_threads_store.add_member(owner, thread_id, member_id)
        fresh = chat_threads_store.get_accessible_thread(uid, thread_id)
        return member, fresh or thread, was_member

    try:
        member, thread, was_member = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    if not was_member:
        try:
            await run_in_threadpool(
                partial(
                    _notify_chat_member_added,
                    member_user_id=int(member["user_id"]),
                    title=str((thread or {}).get("title") or "Чат"),
                    thread_id=int(thread_id),
                    adder_name=_principal_display_name(principal),
                )
            )
        except Exception as e:
            print(f"[chat_members] notify failed chat={thread_id} err={e!r}")
    return {"ok": True, "member": member, "chat": thread}


@miniapp_router.delete("/chats/{thread_id}/members/{member_id}")
async def miniapp_chat_members_remove(
    thread_id: int,
    member_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import chat_threads as chat_threads_store

    uid = int(principal.telegram_user_id)

    def _run() -> bool:
        thread = chat_threads_store.get_accessible_thread(uid, thread_id)
        if not thread:
            return False
        owner = str(thread.get("owner_user_id") or uid)
        if str(uid) != owner and int(member_id) != uid:
            raise HTTPException(
                status_code=403, detail="Удалять участников может только автор"
            )
        return chat_threads_store.remove_member(owner, thread_id, member_id)

    try:
        ok = await run_in_threadpool(_run)
    except HTTPException:
        raise
    if not ok:
        raise HTTPException(status_code=404, detail="Участник не найден")
    thread = await run_in_threadpool(
        chat_threads_store.get_accessible_thread, uid, thread_id
    )
    return {"ok": True, "chat": thread}


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
    except Exception as e:
        print(f"[miniapp_notes] journal_fail err={e!r}")
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
        local_err = "Не удалось загрузить заметки. Обновите страницу чуть позже."
        print(f"[miniapp_notes] local_fail err={e!r}")
    out_journal: list[dict[str, Any]] = []
    transcriptions: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for row in journal:
        if not isinstance(row, dict):
            continue
        item = _journal_row_api(dict(row))
        out_journal.append(item)

    try:
        from assistant.services.transcription_notes_migrate import migrate_user_if_needed

        await run_in_threadpool(migrate_user_if_needed, uid)
    except Exception as e:
        print(f"[miniapp_notes] transcription_migrate_fail err={e!r}")

    try:
        from assistant.services import transcription_notes as tnotes

        transcriptions = await run_in_threadpool(
            partial(tnotes.list_for_user, uid, limit=lim)
        )
    except Exception as e:
        print(f"[miniapp_notes] transcription_notes_fail err={e!r}")
        transcriptions = []

    def _attach_tags() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        from assistant.stores import hashtags as hashtags_store
        from assistant.stores import notes as notes_store
        from assistant.stores import tags as tags_store

        hashtags_store.migrate_extra_tags_to_hashtags(uid)
        fresh_notes = notes_store.list_notes(uid, limit=lim)
        local_notes[:] = fresh_notes
        tag_items: list[tuple[str, str]] = []
        for n in local_notes:
            tag_items.append(("local", str(n.get("id"))))
        for row in transcriptions:
            tag_items.append(("local", str(row.get("id"))))
        mapping = tags_store.tags_by_items(uid, tag_items)
        for n in local_notes:
            body = str(n.get("body") or n.get("description") or "")
            # Не гоняем мегабайтные base64-тела через extract hashtags на каждый /notes.
            if len(body) <= _HASHTAG_SYNC_BODY_MAX:
                hashtags_store.sync_item_hashtags(
                    uid,
                    "local",
                    n.get("id"),
                    str(n.get("title") or ""),
                    body,
                )
        for row in transcriptions:
            body = str(
                row.get("body") or row.get("description") or row.get("preview") or ""
            )
            if len(body) <= _HASHTAG_SYNC_BODY_MAX:
                hashtags_store.sync_item_hashtags(
                    uid,
                    "local",
                    row.get("id"),
                    str(row.get("title") or ""),
                    body,
                )
        hash_mapping = hashtags_store.hashtags_by_items(uid, tag_items)
        for n in local_notes:
            key = ("local", str(n.get("id")))
            _apply_project_fields(n, mapping.get(key, []))
            n["hashtags"] = hash_mapping.get(key, [])
        for row in transcriptions:
            key = ("local", str(row.get("id")))
            _apply_project_fields(row, mapping.get(key, []))
            row["hashtags"] = hash_mapping.get(key, [])
        return tags_store.list_tags(uid), hashtags_store.list_hashtags(uid)

    all_hashtags: list[dict[str, Any]] = []
    try:
        all_tags, all_hashtags = await run_in_threadpool(_attach_tags)
    except Exception as e:
        tags_err = "Не удалось загрузить проекты."
        print(f"[miniapp_notes] tags_fail err={e!r}")
        for n in local_notes:
            n.setdefault("tags", [])
            n.setdefault("project", None)
            n.setdefault("hashtags", [])
        for row in transcriptions:
            row.setdefault("tags", [])
            row.setdefault("project", None)
            row.setdefault("hashtags", [])

    def _attach_shares() -> None:
        from assistant.stores import share_links as share_links_store

        ids = [
            str(n.get("id") or "")
            for n in (local_notes + transcriptions)
            if n.get("id") is not None
        ]
        shares = share_links_store.list_active_by_item_ids("local", ids)
        for n in local_notes + transcriptions:
            owner = str(n.get("owner_user_id") or n.get("user_id") or "")
            iid = str(n.get("id") or "")
            link = shares.get((owner, iid))
            if link and link.get("token"):
                n["shared"] = True
                n["share_url"] = _public_share_url(str(link.get("token") or ""))
                n["share_access"] = link.get("access") or "view"
            else:
                n["shared"] = False
                n["share_url"] = None
                n["share_access"] = None

    try:
        await run_in_threadpool(_attach_shares)
    except Exception as e:
        print(f"[miniapp_notes] share_meta_fail err={e!r}")
        for n in local_notes + transcriptions:
            n.setdefault("shared", False)

    def _attach_sheet_counts() -> None:
        from assistant.stores import note_sheets as note_sheets_store

        counts = note_sheets_store.extra_sheet_counts(
            [n.get("id") for n in local_notes + transcriptions]
        )
        for n in local_notes + transcriptions:
            try:
                n["sheets_count"] = 1 + counts.get(int(n.get("id")), 0)
            except (TypeError, ValueError):
                n["sheets_count"] = 1

    try:
        await run_in_threadpool(_attach_sheet_counts)
    except Exception as e:
        print(f"[miniapp_notes] sheet_counts_fail err={e!r}")

    def _attach_discuss_latest() -> None:
        from assistant.stores import share_comments as share_comments_store

        share_comments_store.attach_discuss_latest(local_notes, kind="local")
        share_comments_store.attach_discuss_latest(transcriptions, kind="local")

    try:
        await run_in_threadpool(_attach_discuss_latest)
    except Exception as e:
        print(f"[miniapp_notes] discuss_latest_fail err={e!r}")
        for n in local_notes + transcriptions:
            n.setdefault("discuss_latest", None)

    await run_in_threadpool(_attach_note_meetings, uid, local_notes + transcriptions)
    local_notes = [minify_note_for_list(n) for n in local_notes]
    transcriptions = [minify_note_for_list(n) for n in transcriptions]

    return {
        "journal": out_journal,
        "transcriptions": transcriptions,
        "summaries": summaries,
        "journal_error": journal_err,
        "local_notes": local_notes,
        "local_has_more": len(local_notes) >= lim,
        "local_error": local_err,
        "tags": all_tags,
        "tags_error": tags_err,
        "hashtags": all_hashtags,
        "todoist_notes": [],
        "todoist_error": None,
    }


def _decorate_notes_for_list(uid: int, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Поля карточки списка (проект, #теги, ссылки, листы, обсуждение) + усечение тела."""
    from assistant.stores import hashtags as hashtags_store
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import share_comments as share_comments_store
    from assistant.stores import share_links as share_links_store
    from assistant.stores import tags as tags_store

    keys = [("local", str(n.get("id"))) for n in notes if n.get("id") is not None]
    try:
        mapping = tags_store.tags_by_items(uid, keys)
        hash_mapping = hashtags_store.hashtags_by_items(uid, keys)
    except Exception as e:
        print(f"[miniapp_notes_list] tags_fail err={e!r}")
        mapping, hash_mapping = {}, {}
    for n in notes:
        key = ("local", str(n.get("id")))
        _apply_project_fields(n, mapping.get(key, []))
        n["hashtags"] = hash_mapping.get(key, [])
    try:
        shares = share_links_store.list_active_by_item_ids("local", [k[1] for k in keys])
    except Exception as e:
        print(f"[miniapp_notes_list] share_fail err={e!r}")
        shares = {}
    for n in notes:
        owner = str(n.get("owner_user_id") or n.get("user_id") or "")
        link = shares.get((owner, str(n.get("id") or "")))
        if link and link.get("token"):
            n["shared"] = True
            n["share_url"] = _public_share_url(str(link.get("token") or ""))
            n["share_access"] = link.get("access") or "view"
        else:
            n["shared"] = False
            n["share_url"] = None
            n["share_access"] = None
    try:
        counts = note_sheets_store.extra_sheet_counts([n.get("id") for n in notes])
    except Exception:
        counts = {}
    for n in notes:
        try:
            n["sheets_count"] = 1 + counts.get(int(n.get("id")), 0)
        except (TypeError, ValueError):
            n["sheets_count"] = 1
    try:
        share_comments_store.attach_discuss_latest(notes, kind="local")
    except Exception:
        for n in notes:
            n.setdefault("discuss_latest", None)
    _attach_note_meetings(uid, notes)
    return [minify_note_for_list(n) for n in notes]


@miniapp_router.get("/notes/local/{note_id}/versions")
async def miniapp_note_versions(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """История версий заметки и её листов (без текста — только список)."""
    from assistant.stores import note_versions
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        if notes_store.get_accessible_note(uid, note_id, with_sharing=False) is None:
            return None
        rows = note_versions.list_versions(note_id)
        current = note_versions.current_states(note_id)
        ids = {str(r.get("author_user_id") or "") for r in rows}
        ids |= {str(c.get("author_user_id") or "") for c in current.values()}
        return {
            "versions": rows,
            "current": {str(k): v for k, v in current.items()},
            "authors": _note_version_authors(ids),
        }

    res = await run_in_threadpool(_run)
    if res is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, **res}


def _note_version_authors(ids: set[str]) -> dict[str, dict[str, Any]]:
    from assistant.stores import note_members

    out: dict[str, dict[str, Any]] = {}
    for raw in ids:
        if not raw:
            continue
        try:
            prof = note_members.get_profile(raw)
        except Exception:
            prof = None
        name = note_members.profile_display_name(prof, fallback="Участник")
        out[raw] = {"name": name, "photo_url": (prof or {}).get("photo_url") or None}
    return out


@miniapp_router.get("/notes/local/{note_id}/versions/{version_id}")
async def miniapp_note_version_get(
    note_id: int,
    version_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_versions
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        if notes_store.get_accessible_note(uid, note_id, with_sharing=False) is None:
            return None
        return note_versions.get_version(note_id, version_id)

    row = await run_in_threadpool(_run)
    if row is None:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    return {"ok": True, "version": row}


@miniapp_router.post("/notes/local/{note_id}/versions/{version_id}/restore")
async def miniapp_note_version_restore(
    note_id: int,
    version_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Вернуть версию: текущее состояние само уходит в историю, так что это обратимо."""
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import note_versions
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        access = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if access is None:
            return None
        v = note_versions.get_version(note_id, version_id)
        if v is None:
            return None
        owner = str(access.get("owner_user_id") or uid)
        sid = int(v.get("sheet_id") or 0)
        # Текущее состояние — отдельной версией «до восстановления», чтобы восстановление можно было отменить.
        undo_id = None
        if sid == note_versions.PRIMARY_SHEET:
            cur = notes_store.get_note(owner, note_id) or {}
            undo_id = note_versions.record(
                owner_user_id=owner, note_id=note_id, title=str(cur.get("title") or ""),
                body=str(cur.get("body") or ""), revision=cur.get("revision") or 0,
                state_at=str(cur.get("updated_at") or ""),
                sheet_title=str(cur.get("primary_sheet_title") or ""),
                reason="before_restore", force=True,
            )
        else:
            cur_sh = note_sheets_store.get_extra_sheet(note_id, sid)
            if cur_sh:
                undo_id = note_versions.record(
                    owner_user_id=owner, note_id=note_id, title=str(access.get("title") or ""),
                    body=str(cur_sh.get("body") or ""), revision=cur_sh.get("revision") or 0,
                    state_at=str(cur_sh.get("updated_at") or ""), sheet_id=sid,
                    sheet_title=str(cur_sh.get("title") or ""), sheet_kind=str(cur_sh.get("kind") or ""),
                    reason="before_restore", force=True,
                )
        if sid == note_versions.PRIMARY_SHEET:
            item = notes_store.update_note(
                owner, note_id, title=v.get("title") or None, body=v.get("body") or "",
                allow_empty_body=True, actor_user_id=uid,
            )
            return {"kind": "note", "item": item, "undo_version_id": undo_id}
        existing = note_sheets_store.get_extra_sheet(note_id, sid)
        if existing:
            sheet = note_sheets_store.update_sheet(
                note_id, sid, title=v.get("sheet_title") or None, body=v.get("body") or "",
                kind=v.get("sheet_kind") or None,
            )
            return {"kind": "sheet", "sheet": sheet, "recreated": False, "undo_version_id": undo_id}
        note = notes_store.get_note(owner, note_id)
        sheet = note_sheets_store.create_sheet(
            note, title=v.get("sheet_title") or "Лист", body=v.get("body") or "",
            kind=v.get("sheet_kind") or note_sheets_store.SHEET_KIND_DOC,
        )
        return {"kind": "sheet", "sheet": sheet, "recreated": True}

    try:
        res = await run_in_threadpool(_run)
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if res is None:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    return {"ok": True, **res}


def _note_meeting_prompt_line(uid: int, kind: str, iid: str) -> str:
    """Обсуждение заметки встречи: ИИ знает, что это за встреча и кто на ней был."""
    if kind != "local" or not str(iid or "").isdigit():
        return ""
    try:
        from assistant.services.gpt_calendar_tools import human_when, _iso_local
        from assistant.stores import meeting_links

        link = meeting_links.get_for_note(uid, int(iid))
        people = meeting_links.people_for_note(uid, int(iid))
        if not link and not people:
            return ""
        names = [str(a.get("name") or a.get("email") or "") for a in ((link or {}).get("attendees") or [])]
        names += [str(p.get("name") or p.get("email") or "") for p in people]
        names = [n for i, n in enumerate(names) if n and n not in names[:i]]
        if link:
            when = human_when(_iso_local(uid, link.get("start_iso")), _iso_local(uid, link.get("end_iso")), uid)
            line = f"Эта заметка — материалы встречи «{link.get('summary') or 'Встреча'}» ({when})"
            if link.get("event_id"):
                line += f", event_id={link.get('event_id')}, calendar_id={link.get('calendar_id')}"
        else:
            line = "Эта заметка — материалы созвона вне календаря"
        if names:
            line += ". Участники: " + ", ".join(names[:20])
        return line + "."
    except Exception as e:
        print(f"[gpt_chat] meeting line failed note={iid} err={e!r}")
        return ""


class _MiniappMeetingLinkBody(BaseModel):
    event: dict[str, Any]


class _MiniappNotePersonBody(BaseModel):
    name: str = ""
    email: str = ""
    contact_key: str = ""


def _meeting_material_item(note: dict[str, Any]) -> dict[str, Any]:
    from assistant.services import transcription_notes as tnotes

    is_tr = str(note.get("role") or "") == "transcription"
    item = tnotes.enrich(note) if is_tr else dict(note)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    tasks = []
    for t in meta.get("tasks") or []:
        if isinstance(t, dict):
            line = " — ".join(x for x in (str(t.get("assignee") or "").strip(), str(t.get("task") or "").strip()) if x)
            if line:
                tasks.append(line[:200])
    preview = str(item.get("preview") or "") if is_tr else _plain_preview_text(str(note.get("body") or ""))[:280]
    return {
        "id": int(note["id"]),
        "title": str(note.get("title") or ""),
        "kind": "transcription" if is_tr else "note",
        "has_summary": bool(item.get("has_summary")),
        "summary_generating": bool(item.get("summary_generating")),
        "preview": preview,
        "tasks": tasks[:5],
        "decisions": [str(x)[:200] for x in (meta.get("decisions") or []) if str(x).strip()][:4],
        "updated_at": str(note.get("updated_at") or ""),
    }


@miniapp_router.get("/calendar/events/{event_id}/materials")
async def miniapp_calendar_event_materials(
    event_id: str,
    calendar_id: str = "primary",
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Материалы встречи: привязанные транскрипции и заметки (только свои)."""
    from assistant.stores import meeting_links
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> list[dict[str, Any]]:
        out = []
        for row in meeting_links.notes_for_event(uid, meeting_links.event_key(calendar_id, event_id)):
            note = notes_store.get_accessible_note(uid, int(row["note_id"]), with_sharing=False)
            if note:
                out.append(_meeting_material_item(note))
        return out

    return {"ok": True, "items": await run_in_threadpool(_run)}


@miniapp_router.post("/calendar/events/{event_id}/note")
async def miniapp_calendar_event_note(
    event_id: str,
    body: _MiniappMeetingLinkBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Создать заметку» из встречи: название — тема встречи, заметка сразу привязана."""
    from assistant.stores import meeting_links
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    ev = dict(body.event or {})
    ev["id"] = event_id

    def _run() -> dict[str, Any]:
        m = meeting_links.meeting_from_event(ev)
        note = notes_store.create_note(uid, m.get("summary") or "Встреча", "")
        meeting_links.link(uid, note["id"], m, source="meeting")
        return _decorate_notes_for_list(uid, [note])[0] | {"body": note.get("body") or "", "body_preview_only": False}

    item = await run_in_threadpool(_run)
    return {"ok": True, "item": item}


@miniapp_router.get("/notes/local/{note_id}/meeting")
async def miniapp_note_meeting_get(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import meeting_links
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if note is None:
            return None
        owner = str(note.get("owner_user_id") or note.get("user_id") or uid)  # встречей могли поделиться
        return {
            "meeting": meeting_links.get_for_note(owner, note_id),
            "people": meeting_links.people_for_note(owner, note_id),
            "is_owner": owner == str(uid),
        }

    res = await run_in_threadpool(_run)
    if res is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, **res}


@miniapp_router.put("/notes/local/{note_id}/meeting")
async def miniapp_note_meeting_put(
    note_id: int,
    body: _MiniappMeetingLinkBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Добавить ко встрече»: у транскрипции название становится темой встречи."""
    from assistant.stores import meeting_links
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        access = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if access is None:
            return None
        m = meeting_links.meeting_from_event(dict(body.event or {}))
        if not m.get("event_id"):
            raise ValueError("Не выбрана встреча")
        link = meeting_links.link(uid, note_id, m)
        item = None
        if str(access.get("role") or "") == "transcription" and m.get("summary"):
            owner = str(access.get("owner_user_id") or uid)
            item = notes_store.update_note(owner, note_id, title=m["summary"], actor_user_id=uid)
        return {"meeting": link, "people": meeting_links.people_for_note(uid, note_id), "title": (item or {}).get("title")}

    try:
        res = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if res is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, **res}


@miniapp_router.delete("/notes/local/{note_id}/meeting")
async def miniapp_note_meeting_delete(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import meeting_links

    uid = int(principal.telegram_user_id)
    await run_in_threadpool(meeting_links.unlink, uid, note_id)
    return {"ok": True}


@miniapp_router.post("/notes/local/{note_id}/people")
async def miniapp_note_people_add(
    note_id: int,
    body: _MiniappNotePersonBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«+ участник»: человек, которого не было в приглашении (или созвон вне календаря)."""
    from assistant.stores import meeting_links
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> list[dict[str, Any]] | None:
        if notes_store.get_accessible_note(uid, note_id, with_sharing=False) is None:
            return None
        return meeting_links.add_person(uid, note_id, name=body.name, email=body.email, contact_key=body.contact_key)

    try:
        people = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if people is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "people": people}


@miniapp_router.delete("/notes/local/{note_id}/people/{person_key:path}")
async def miniapp_note_people_delete(
    note_id: int,
    person_key: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import meeting_links

    uid = int(principal.telegram_user_id)
    people = await run_in_threadpool(meeting_links.remove_person, uid, note_id, person_key)
    return {"ok": True, "people": people}


@miniapp_router.get("/notes/local/{note_id}/meeting-candidates")
async def miniapp_note_meeting_candidates(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Встречи вокруг времени записи (−3…+1 день); подходящая по времени — первой."""
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if note is None:
            return None
        try:
            at = datetime.fromisoformat(str(note.get("created_at") or ""))
        except ValueError:
            at = datetime.now(timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        payload = _calendar_range_payload(
            uid, (at - timedelta(days=3)).date().isoformat(), (at + timedelta(days=1)).date().isoformat()
        )
        return {"connected": bool(payload.get("connected", True)), **_rank_meeting_candidates(payload.get("events") or [], at)}

    res = await run_in_threadpool(_run)
    if res is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, **res}


def _rank_meeting_candidates(events: list[dict[str, Any]], at: datetime) -> dict[str, Any]:
    """Подходящие встречи: запись сделана во время встречи или вскоре после неё (до 3 ч)."""
    rows = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("entry_type") == "task" or str(ev.get("id") or "").startswith("task-"):
            continue
        st = (ev.get("start") or {}).get("dateTime") if isinstance(ev.get("start"), dict) else None
        en = (ev.get("end") or {}).get("dateTime") if isinstance(ev.get("end"), dict) else None
        if not st:
            continue  # события на весь день — не встречи
        try:
            s_dt = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            e_dt = datetime.fromisoformat(str(en).replace("Z", "+00:00")) if en else s_dt + timedelta(hours=1)
        except ValueError:
            continue
        if s_dt.tzinfo is None:
            s_dt = s_dt.replace(tzinfo=timezone.utc)
        if e_dt.tzinfo is None:
            e_dt = e_dt.replace(tzinfo=timezone.utc)
        if s_dt > at + timedelta(minutes=30):
            continue  # встреча после записи
        match = s_dt - timedelta(minutes=15) <= at <= e_dt + timedelta(hours=3)
        rows.append((not match, abs((at - e_dt).total_seconds()), ev, match))
    rows.sort(key=lambda r: (r[0], r[1]))
    return {
        "suggested": [r[2] for r in rows if r[3]][:2],
        "others": [r[2] for r in rows if not r[3]][:40],
    }


_FOCUS_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}


class _MiniappFocusDismissBody(BaseModel):
    key: str


class _MiniappFocusDoneItem(BaseModel):
    id: str
    text: str = ""


class _MiniappFocusDoneBody(BaseModel):
    key: str
    items: list[_MiniappFocusDoneItem] = []
    whole: bool = False
    op: str = "done"  # done | not_me («Ждут вашего ответа»: вопрос был не мне)


def _focus_note_changed(owner: Any, note_id: Any) -> None:
    """Заметку правили — Фокус разберёт её сам после короткой тишины (не ждёт открытия ленты)."""
    try:
        from assistant.services import proactive_ai

        proactive_ai.note_changed(owner, note_id)
    except Exception as e:
        print(f"[proactive] note_changed failed note={note_id} err={e!r}")
    try:
        from assistant.services import semantic_memory

        semantic_memory.note_changed(owner)  # память Лео — через минуту, а не через 15
    except Exception as e:
        print(f"[semantic] note_changed failed note={note_id} err={e!r}")


def _memory_task_changed(task: dict[str, Any] | None) -> None:
    """Задачу правили / написали в переписке — память Лео (постановщика и исполнителя) через минуту."""
    if not task:
        return
    try:
        from assistant.services import semantic_memory

        for who in {str(task.get("owner_user_id") or ""), str(task.get("assignee_user_id") or "")} - {""}:
            semantic_memory.note_changed(who)
    except Exception as e:
        print(f"[semantic] task_changed failed task={task.get('id')} err={e!r}")


def _memory_sheet_changed(uid: Any, note_id: Any) -> None:
    """Лист заметки создали / правили / удалили — память Лео переиндексирует через минуту
    (у автора и у того, кто правил: оба ищут по этой заметке)."""
    try:
        from assistant.services import semantic_memory
        from assistant.stores import notes as notes_store

        note = notes_store.get_accessible_note(uid, int(note_id), with_sharing=False) or {}
        for who in {str(uid), str(note.get("owner_user_id") or uid)}:
            semantic_memory.note_changed(who)
    except Exception as e:
        print(f"[semantic] sheet_changed failed note={note_id} err={e!r}")


def _focus_enabled(uid: int) -> bool:
    try:
        from assistant.stores import user_prefs

        return bool(user_prefs.proactive_prefs(uid).get("focus"))
    except Exception:
        return True


def _focus_feed_items(uid: int) -> list[dict[str, Any]]:
    from assistant.services import proactive
    from assistant.services.calendar import _tz_for
    from assistant.stores import user_prefs

    if not user_prefs.proactive_prefs(uid).get("focus"):
        # Фокус выключен, но входящие поручения разобрать всё равно надо.
        return proactive.rule_task_inbox(uid, proactive._now())
    today = datetime.now(_tz_for(uid)).date()
    try:
        payload = _calendar_range_payload(uid, (today - timedelta(days=2)).isoformat(), (today + timedelta(days=1)).isoformat())
        events = payload.get("events") or []
    except Exception as e:
        print(f"[proactive] calendar failed uid={uid} err={e!r}")
        events = []
    return proactive.build_feed(uid, events)


@miniapp_router.get("/proactive/feed")
async def miniapp_proactive_feed(
    fresh: int = 0,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Фокус»: подсказки с готовыми действиями (правила по задачам, встречам и саммари)."""
    from assistant.services import proactive_ai

    uid = int(principal.telegram_user_id)
    hit = _FOCUS_CACHE.get(uid)
    # Кэш минуту — но не дольше свежего ИИ-разбора: новые карточки видны сразу после него.
    if hit and not fresh and time.time() - hit[0] < 60 and hit[0] >= proactive_ai.last_analyzed_at(uid):
        return {"ok": True, "items": hit[1], "analyzing": proactive_ai.busy(uid), "enabled": _focus_enabled(uid)}

    items = await run_in_threadpool(_focus_feed_items, uid)
    _FOCUS_CACHE[uid] = (time.time(), items)
    try:
        # ИИ разбирает изменившиеся материалы в фоне; клиент перезапросит ленту, пока analyzing.
        proactive_ai.kick(uid)
    except Exception as e:
        print(f"[proactive] kick failed uid={uid} err={e!r}")
    return {"ok": True, "items": items, "analyzing": proactive_ai.busy(uid), "enabled": _focus_enabled(uid)}


@miniapp_router.post("/proactive/analyze")
async def miniapp_proactive_analyze(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Кнопка «Найти инсайты» в Фокусе: разобрать изменённые материалы сейчас, без ожидания тишины."""
    from assistant.services import proactive_ai

    uid = int(principal.telegram_user_id)
    res = await run_in_threadpool(proactive_ai.analyze_now, uid)
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, **res, "analyzing": proactive_ai.busy(uid)}


@miniapp_router.get("/proactive/prefs")
async def miniapp_proactive_prefs_get(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.stores import user_prefs

    return {"ok": True, "prefs": await run_in_threadpool(user_prefs.proactive_prefs, int(principal.telegram_user_id))}


@miniapp_router.patch("/proactive/prefs")
async def miniapp_proactive_prefs_patch(
    body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """Профиль → «Подсказки ассистента»: Фокус, утренний бриф и его время, после встреч и т.д."""
    from assistant.stores import user_prefs

    uid = int(principal.telegram_user_id)
    prefs = await run_in_threadpool(user_prefs.set_proactive_prefs, uid, body or {})
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, "prefs": prefs}


@miniapp_router.post("/proactive/report/{kind}")
async def miniapp_proactive_report(
    kind: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """«План на день» / «Итоги недели» — заметка в проекте «От ассистента» (одна на день/неделю)."""
    from assistant.services import proactive_reports
    from assistant.services.calendar import _tz_for

    uid = int(principal.telegram_user_id)
    if kind not in ("day", "week"):
        raise HTTPException(status_code=404, detail="Нет такого отчёта")

    def _run() -> dict[str, Any]:
        today = datetime.now(_tz_for(uid)).date()
        try:
            events = _calendar_range_payload(uid, (today - timedelta(days=7)).isoformat(), (today + timedelta(days=1)).isoformat()).get("events") or []
        except Exception:
            events = []
        return proactive_reports.day_plan(uid, events=events) if kind == "day" else proactive_reports.weekly(uid, events=events)

    try:
        note = await run_in_threadpool(_run)
    except Exception as e:
        print(f"[proactive] report {kind} failed uid={uid} err={e!r}")
        raise HTTPException(status_code=502, detail="Не удалось собрать отчёт, попробуйте ещё раз") from e
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, "item": {"id": note["id"], "title": note.get("title") or ""}}


@miniapp_router.post("/proactive/dismiss")
async def miniapp_proactive_dismiss(
    body: _MiniappFocusDismissBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """✕ на карточке Фокуса: больше не показывать эту подсказку."""
    from assistant.services import proactive

    uid = int(principal.telegram_user_id)
    key = str(body.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Нет подсказки")
    await run_in_threadpool(proactive.dismiss, uid, key)

    def _mark_dismissed() -> None:
        # ✕ на пункте (договорённость, важное из чата): похожие формулировки тоже не поднимаем.
        from assistant.services import focus_items

        hit = _FOCUS_CACHE.get(uid)
        card = next((c for c in (hit[1] if hit else []) if c.get("key") == key), None)
        if not card or not card.get("items") or card.get("type") == "summary_tasks":
            return
        for k in _focus_remember(uid, card):
            focus_items.apply(uid, k, "dismiss", by=_principal_display_name(principal), reason="not_relevant")

    try:
        await run_in_threadpool(_mark_dismissed)
    except Exception as e:
        print(f"[proactive] dismiss item failed uid={uid} key={key} err={e!r}")
    hit = _FOCUS_CACHE.get(uid)
    if hit:
        _FOCUS_CACHE[uid] = (hit[0], [i for i in hit[1] if i.get("key") != key])
    return {"ok": True}


class _MiniappTopicBody(BaseModel):
    op: str  # confirm | reject | undo
    key: str = ""
    name: str = ""
    topic_id: Optional[int] = None
    items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    undo: Optional[dict[str, Any]] = None


class _MiniappTopicItemBody(BaseModel):
    kind: str
    value: str
    on: bool
    scope: str = "me"  # me | all


def _topic_payload(uid: int, view: dict[str, Any]) -> dict[str, Any]:
    from assistant.stores import meeting_topics as topics

    out = dict(view)
    out["history"] = topics.history(int(view["id"]), limit=20)
    return out


@miniapp_router.post("/proactive/topic")
async def miniapp_proactive_topic(
    body: _MiniappTopicBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Карточка «связать тему?»: Связать (отмеченное — у всех), Не то (мне больше не предлагать), Вернуть."""
    from assistant.services import proactive
    from assistant.stores import meeting_topics as topics

    uid = int(principal.telegram_user_id)
    who = _principal_display_name(principal)
    op = str(body.op or "")

    def _run() -> dict[str, Any]:
        if body.topic_id and int(body.topic_id) not in topics.visible_topic_ids(uid):
            raise HTTPException(status_code=404, detail="Тема не найдена")
        if op == "confirm":
            if not body.items:
                raise HTTPException(status_code=400, detail="Отметьте, что связать")
            res = topics.confirm(uid, body.items, name=body.name, topic_id=body.topic_id, by_name=who)
            if body.rejected:
                topics.reject(uid, body.rejected)
            return {"undo": res}
        if op == "reject":
            topics.reject(uid, body.items)
            return {"undo": {}}
        if op == "undo":
            tok = body.undo or {}
            if tok.get("topic_id") and int(tok["topic_id"]) not in topics.visible_topic_ids(uid):
                raise HTTPException(status_code=404, detail="Тема не найдена")
            topics.undo(uid, tok, body.rejected or body.items, by_name=who)
            if body.key:
                proactive.undismiss(uid, body.key)
            return {}
        raise HTTPException(status_code=400, detail="Неизвестное действие")

    res = await run_in_threadpool(_run)
    if body.key and op in ("confirm", "reject"):
        await run_in_threadpool(proactive.dismiss, uid, body.key)
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, **res}


@miniapp_router.get("/topics")
async def miniapp_topics_list(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Темы встреч, которые видит пользователь (свои и из чатов, где он состоит)."""
    from assistant.stores import meeting_topics as topics

    uid = int(principal.telegram_user_id)
    items = await run_in_threadpool(topics.topics_for_user, uid)
    return {"ok": True, "topics": items}


@miniapp_router.get("/topics/for-event")
async def miniapp_topic_for_event(
    title: str = "",
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Чип «Тема …» в карточке встречи."""
    from assistant.stores import meeting_topics as topics

    uid = int(principal.telegram_user_id)
    t = await run_in_threadpool(topics.topic_for_title, uid, title)
    return {"ok": True, "topic": t}


@miniapp_router.get("/topics/{topic_id}")
async def miniapp_topic_get(topic_id: int, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.stores import meeting_topics as topics

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        if int(topic_id) not in topics.visible_topic_ids(uid):
            raise HTTPException(status_code=404, detail="Тема не найдена")
        return _topic_payload(uid, topics.topic_view(uid, int(topic_id)))

    return {"ok": True, "topic": await run_in_threadpool(_run)}


@miniapp_router.post("/topics/{topic_id}/item")
async def miniapp_topic_item(
    topic_id: int,
    body: _MiniappTopicItemBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Переключатель пункта темы: у меня / у всех."""
    from assistant.stores import meeting_topics as topics

    uid = int(principal.telegram_user_id)
    who = _principal_display_name(principal)
    if body.kind not in ("title", "chat") or body.scope not in ("me", "all"):
        raise HTTPException(status_code=400, detail="Неверный пункт")

    def _run() -> dict[str, Any]:
        if int(topic_id) not in topics.visible_topic_ids(uid):
            raise HTTPException(status_code=404, detail="Тема не найдена")
        try:
            topics.set_item(uid, int(topic_id), body.kind, body.value, on=bool(body.on), scope=body.scope, by_name=who)
        except KeyError:
            raise HTTPException(status_code=404, detail="Пункт не найден")
        return _topic_payload(uid, topics.topic_view(uid, int(topic_id)))

    res = await run_in_threadpool(_run)
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, "topic": res}


@miniapp_router.post("/proactive/done")
async def miniapp_proactive_done(
    body: _MiniappFocusDoneBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Уже есть» на карточке Фокуса: задачи заведены в другом сервисе. Возвращает обновлённую карточку или null."""
    from assistant.services import proactive

    uid = int(principal.telegram_user_id)
    key = str(body.key or "").strip()
    items = [{"id": i.id, "text": i.text} for i in body.items if str(i.id or "").strip()]
    if not key or not items:
        raise HTTPException(status_code=400, detail="Нечего отмечать")
    by = _principal_display_name(principal)

    def _mark_items() -> None:
        # «✓ Сделано»: статус пункта + значок в исходнике (саммари / заметке).
        from assistant.services import focus_items

        card = _focus_card_for(uid, key)
        if not card:
            return
        for k in _focus_remember(uid, card, [i["id"] for i in items]):
            try:
                if body.op in ("not_me", "newsletter"):  # «Это рассылка» — для писем: глушит отправителя
                    focus_items.apply(uid, k, "dismiss", by=by, reason=body.op)
                else:
                    focus_items.apply(uid, k, "done", by=by)
            except ValueError as e:
                print(f"[proactive] done item={k} err={e!r}")

    try:
        await run_in_threadpool(_mark_items)
    except Exception as e:
        print(f"[proactive] done items failed uid={uid} err={e!r}")
    await run_in_threadpool(proactive.mark_done, uid, key, items, whole=bool(body.whole))
    if body.whole:
        hit = _FOCUS_CACHE.get(uid)
        if hit:
            _FOCUS_CACHE[uid] = (hit[0], [i for i in hit[1] if i.get("key") != key])
        return {"ok": True, "item": None}
    feed = await run_in_threadpool(_focus_feed_items, uid)
    _FOCUS_CACHE[uid] = (time.time(), feed)
    return {"ok": True, "item": next((i for i in feed if i.get("key") == key), None)}


class _MiniappFocusRefineBody(BaseModel):
    key: str


class _MiniappFocusItemBody(BaseModel):
    key: str
    op: str
    until: Optional[str] = None
    reason: Optional[str] = None
    type: Optional[str] = None
    url: Optional[str] = None
    task_id: Optional[int] = None
    title: Optional[str] = None
    fields: Optional[dict[str, Any]] = None


def _focus_card_for(uid: int, key: str) -> Optional[dict[str, Any]]:
    """Карточка Фокуса по её ключу или по ключу пункта внутри (задачи из саммари)."""
    hit = _FOCUS_CACHE.get(uid)
    feeds = [hit[1]] if hit else []
    for attempt in range(2):
        for feed in feeds:
            for card in feed:
                if card.get("key") == key or any(str(i.get("id")) == key for i in card.get("items") or []):
                    return card
        if attempt == 0:
            feed = _focus_feed_items(uid)
            _FOCUS_CACHE[uid] = (time.time(), feed)
            feeds = [feed]
    return None


def _focus_remember(uid: int, card: dict[str, Any], keys: Optional[list[str]] = None) -> list[str]:
    from assistant.services import focus_items

    out = []
    for snap in focus_items.item_from_card(card, with_closed=True):
        if keys and str(snap.get("key")) not in keys:
            continue
        focus_items.remember(uid, snap)
        out.append(str(snap["key"]))
    return out


@miniapp_router.post("/proactive/refine")
async def miniapp_proactive_refine(
    body: _MiniappFocusRefineBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Уточнить» на карточке Фокуса: карточка разбора пункта для чата с ассистентом."""
    from assistant.services import gpt_calendar_tools as gcal_tools

    uid = int(principal.telegram_user_id)
    key = str(body.key or "").strip()

    def _run() -> dict[str, Any]:
        card = _focus_card_for(uid, key)
        if card and not card.get("items") and card.get("tasks"):
            # «Просрочено»: в чат — карточки самих задач (открыть, перенести, отметить)
            ctx = gcal_tools.GptCalendarContext(uid=uid)
            acts = []
            for t in card["tasks"][: gcal_tools.MAX_CARDS]:
                try:
                    made = gcal_tools._card_for(ctx, {"type": "task", "id": str(t.get("id") or "")})
                except Exception as e:
                    print(f"[proactive] refine task card uid={uid} task={t.get('id')} err={e!r}")
                    made = None
                if made:
                    kind, title, payload = made
                    a = gcal_tools.get_action(gcal_tools.record_action(uid, kind, "info", title=title, payload=payload)) or {}
                    acts.append(gcal_tools.public_action(a, viewer_uid=uid))
            if not acts:
                raise HTTPException(status_code=404, detail="Задачи уже не найдены — обновите Фокус")
            return {"actions": acts, "action": acts[0], "title": card.get("title") or "", "kind": "tasks"}
        if card and not card.get("items") and card.get("commitment_keys"):
            # Риски по реестру обязательств: в чат — карточки самих пунктов (как у задач из саммари)
            from assistant.services import focus_items

            keys = [k for k in card["commitment_keys"] if focus_items.get(uid, k)]
            if not keys:
                raise HTTPException(status_code=404, detail="Подсказка уже неактуальна — обновите Фокус")
            aid = gcal_tools.record_action(
                uid, "focus_item", "info", title=str(card.get("title") or "")[:200],
                payload={"keys": keys, "card_key": card.get("key")},
            )
            a = gcal_tools.get_action(aid) or {}
            return {"action": gcal_tools.public_action(a, viewer_uid=uid), "title": card.get("title") or ""}
        if not card or not card.get("items"):
            raise HTTPException(status_code=404, detail="Подсказка уже неактуальна — обновите Фокус")
        keys = _focus_remember(uid, card)
        aid = gcal_tools.record_action(
            uid, "focus_item", "info", title=str(card.get("title") or "")[:200],
            payload={"keys": keys, "card_key": card.get("key")},
        )
        a = gcal_tools.get_action(aid) or {}
        return {"action": gcal_tools.public_action(a, viewer_uid=uid), "title": card.get("title") or ""}

    return {"ok": True, **(await run_in_threadpool(_run))}


@miniapp_router.post("/proactive/items")
async def miniapp_proactive_items(
    body: _MiniappFocusRefineBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Разобрать» на карточке Фокуса: пункты карточки для разбора стопкой (без записи в чат)."""
    from assistant.services import focus_items

    uid = int(principal.telegram_user_id)
    key = str(body.key or "").strip()

    def _run() -> list[dict[str, Any]]:
        card = _focus_card_for(uid, key)
        if card and card.get("items"):
            keys = _focus_remember(uid, card)
        elif card and card.get("commitment_keys"):
            keys = [str(k) for k in card["commitment_keys"]]
        else:
            raise HTTPException(status_code=404, detail="Подсказка уже неактуальна — обновите Фокус")
        rows = [focus_items.get(uid, k) for k in keys]
        out = [focus_items.public(r) for r in rows if r]
        try:  # задачи встречи, которые уже поставил или выполнил другой участник, — не в стопку
            from assistant.services import meeting_share

            out = meeting_share.overlay(uid, out)
        except Exception as e:
            print(f"[focus] shared overlay failed key={key} err={e!r}")
        return out

    items = await run_in_threadpool(_run)
    return {"ok": True, "items": items}


@miniapp_router.post("/proactive/item")
async def miniapp_proactive_item(
    body: _MiniappFocusItemBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Разбор пункта Фокуса: выполнено, связать, отложить, скрыть с причиной, сменить тип, уточнить поля."""
    from assistant.services import focus_items

    uid = int(principal.telegram_user_id)
    key = str(body.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Нет пункта")
    by = _principal_display_name(principal)

    def _run() -> dict[str, Any]:
        if not focus_items.get(uid, key):
            card = _focus_card_for(uid, key)
            if not card:
                raise HTTPException(status_code=404, detail="Пункт не найден — обновите Фокус")
            _focus_remember(uid, card, [key])
        try:
            row = focus_items.apply(
                uid, key, body.op, by=by, until=body.until, reason=body.reason, type=body.type,
                url=body.url, task_id=body.task_id, title=body.title, fields=body.fields or {},
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return focus_items.public(row)

    item = await run_in_threadpool(_run)
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, "item": item}


@miniapp_router.get("/notes/trash")
async def miniapp_notes_trash(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Недавно удалённые заметки (30 дней) — из снимков истории версий."""
    from assistant.stores import note_versions
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> list[dict[str, Any]]:
        out = []
        for r in note_versions.list_deleted_notes(uid):
            if notes_store.get_note(uid, int(r["note_id"])):
                continue  # заметка с этим id жива (на всякий случай)
            head = str(r.pop("head", "") or "")
            r["preview"] = _plain_preview_text(head)[:220]
            out.append(r)
        return out

    items = await run_in_threadpool(_run)
    return {"ok": True, "items": items, "days": note_versions.TRASH_DAYS}


@miniapp_router.post("/notes/trash/{note_id}/restore")
async def miniapp_notes_trash_restore(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Вернуть удалённую заметку: новая заметка с тем же текстом, листами и проектом."""
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import note_versions
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        snap = note_versions.deleted_note_snapshot(uid, note_id)
        if snap is None:
            return None
        extra = snap.get("extra") or {}
        meta = extra.get("meta") if isinstance(extra.get("meta"), dict) else None
        note = notes_store.create_note(
            uid, str(snap.get("title") or ""), str(snap.get("body") or ""),
            role=str(extra.get("role") or ""), meta=meta,
            primary_sheet_title=str(snap.get("sheet_title") or ""),
        )
        failed = 0
        for sh in snap.get("sheets") or []:
            try:
                note_sheets_store.create_sheet(
                    note, title=str(sh.get("sheet_title") or ""), body=str(sh.get("body") or ""),
                    kind=str(sh.get("sheet_kind") or note_sheets_store.SHEET_KIND_DOC),
                )
            except Exception as e:
                failed += 1
                print(f"[notes_trash] sheet restore failed note={note_id} err={e!r}")
        tag_ids = [int(t) for t in (extra.get("tag_ids") or []) if str(t).isdigit()]
        if tag_ids:
            try:
                from assistant.stores import tags as tags_store

                tags_store.set_item_tags(uid, "local", note["id"], tag_ids[:1])
            except Exception as e:
                print(f"[notes_trash] tags restore failed note={note_id} err={e!r}")
        note_versions.mark_note_restored(note_id)
        return {"note_id": int(note["id"]), "sheets_failed": failed}

    res = await run_in_threadpool(_run)
    if res is None:
        raise HTTPException(status_code=404, detail="Удалённая заметка не найдена")
    return {"ok": True, **res}


@miniapp_router.get("/notes/page")
async def miniapp_notes_page(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    offset: int = 0,
    limit: int = 120,
) -> dict[str, Any]:
    """Следующая страница заметок (после первых 120 из /notes)."""
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    off = max(0, int(offset))
    lim = min(max(int(limit), 1), 300)

    def _run() -> tuple[list[dict[str, Any]], bool]:
        rows = notes_store.list_notes(uid, limit=off + lim + 1)
        page = rows[off : off + lim]
        return _decorate_notes_for_list(uid, page), len(rows) > off + lim

    items, has_more = await run_in_threadpool(_run)
    return {"local_notes": items, "has_more": has_more, "offset": off}


@miniapp_router.get("/notes/backlinks")
async def miniapp_notes_backlinks(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    kind: str = "",
    id: str = "",
) -> dict[str, Any]:
    """Где упомянута сущность чипом «+»: для строки «Заметки» в карточке задачи, напоминания, контакта."""
    from assistant.stores import calendar_tasks as calendar_tasks_store
    from assistant.stores import note_sheets as note_sheets_store

    uid = int(principal.telegram_user_id)
    k = str(kind or "").strip().lower()
    rid = str(id or "").strip()[:200]
    if k not in ("task", "reminder", "person") or not rid:
        raise HTTPException(status_code=400, detail="Укажите kind и id")

    def _run() -> list[dict[str, Any]]:
        origin: list[Any] = []
        ref_id = rid
        if k == "task":
            task = calendar_tasks_store.get_task_for_user(uid, rid)
            if not task:
                return []
            origin = [task.get("note_id")]
        elif k == "person":
            from assistant.stores import contacts_store

            ref_id = contacts_store.normalize_contact_key(rid)
        return note_sheets_store.find_ref_backlinks(uid, k, ref_id, origin_note_ids=origin)

    return {"ok": True, "items": await run_in_threadpool(_run)}


@miniapp_router.get("/notes/search")
async def miniapp_notes_search(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
    q: str = "",
    kind: str = "notes",
    limit: int = 60,
) -> dict[str, Any]:
    """Поиск по всему тексту заметок (или транскрипций) со сниппетом вокруг найденного."""
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    query = " ".join(str(q or "").split())[:200]
    if len(query) < 2:
        return {"items": [], "query": query}

    def _run() -> list[dict[str, Any]]:
        found = notes_store.search_notes_for_list(
            uid, query, limit=limit, only_transcription=(kind == "transcriptions")
        )
        return _decorate_notes_for_list(uid, found)

    items = await run_in_threadpool(_run)
    return {"items": items, "query": query}


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
        raise HTTPException(status_code=404, detail="Проект не найден")
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
        raise HTTPException(status_code=404, detail="Проект не найден")
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
            return notes_store.get_accessible_note(uid, nid, with_sharing=False) is not None

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
    return {"ok": True, "tags": tags, "project": tags[0] if tags else None}


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
    _apply_project_fields(item, item["tags"])
    from assistant.stores import hashtags as hashtags_store

    item["hashtags"] = await run_in_threadpool(
        partial(hashtags_store.get_item_hashtags, uid, "journal", str(event_id))
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
    from assistant.stores import hashtags as hashtags_store

    await run_in_threadpool(hashtags_store.delete_item_links, uid, "journal", event_id)
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
        return _sync_journal_hashtags(uid, event_id, item)

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


@miniapp_router.get("/notes/knowledge")
async def miniapp_knowledge_notes(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store
    from assistant.stores import tags as tags_store

    uid = int(principal.telegram_user_id)

    def _load() -> list[dict[str, Any]]:
        from assistant.stores import hashtags as hashtags_store

        notes = notes_store.list_knowledge_notes(uid)
        for n in notes:
            body = str(n.get("body") or n.get("description") or "")
            if len(body) <= _HASHTAG_SYNC_BODY_MAX:
                hashtags_store.sync_item_hashtags(
                    uid,
                    "local",
                    n.get("id"),
                    str(n.get("title") or ""),
                    body,
                )
        tag_items = [("local", str(n.get("id"))) for n in notes]
        mapping = tags_store.tags_by_items(uid, tag_items)
        hash_mapping = hashtags_store.hashtags_by_items(uid, tag_items)
        from assistant.stores import teams_store

        team_map = teams_store.teams_for_notes([int(n["id"]) for n in notes if n.get("id") is not None])
        for n in notes:
            key = ("local", str(n.get("id")))
            _apply_project_fields(n, mapping.get(key, []))
            n["hashtags"] = hash_mapping.get(key, [])
            n["teams"] = team_map.get(int(n["id"]), []) if n.get("id") is not None else []
        return notes

    try:
        notes = await run_in_threadpool(_load)
    except Exception:
        notes = await run_in_threadpool(notes_store.list_knowledge_notes, uid)
        for n in notes:
            n.setdefault("tags", [])
            n.setdefault("project", None)
            n.setdefault("hashtags", [])
    return {"notes": [minify_note_for_list(n) for n in notes]}


@miniapp_router.post("/notes/knowledge")
async def miniapp_knowledge_note_create(
    body: _MiniappLocalNoteCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Укажите заголовок")
    desc = (body.description or "").strip()
    uid = int(principal.telegram_user_id)

    def _local() -> dict[str, Any]:
        created = notes_store.create_note(
            uid, title, desc, role=notes_store.KNOWLEDGE_ROLE
        )
        return _sync_local_note_hashtags(uid, created)

    item = await run_in_threadpool(_local)
    return {"ok": True, "id": item["id"], "item": item}


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
        created = notes_store.create_note(uid, title, desc)
        _focus_note_changed(uid, created["id"])
        return _sync_local_note_hashtags(uid, created)

    item = await run_in_threadpool(_local)
    return {"ok": True, "id": item["id"], "item": item}


@miniapp_router.patch("/notes/local/{note_id}")
async def miniapp_local_note_patch(
    note_id: int,
    body: _MiniappLocalNotePatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    if (
        body.title is None
        and body.description is None
        and body.sync_todoist is None
        and body.kb_enabled is None
    ):
        raise HTTPException(status_code=400, detail="Укажите title и/или description")
    uid = int(principal.telegram_user_id)
    yjs_saved: list[tuple[int, str]] = []  # (ревизия, HTML), если сохранили снимок из Yjs

    def _run() -> dict[str, Any] | None:
        access = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if access is None:
            return None
        owner = str(access.get("owner_user_id") or uid)
        if body.description is not None:
            current = notes_store.get_note(owner, note_id) or {}
            if looks_like_list_preview_overwrite(body.description, current.get("body")):
                print(f"[note_patch] blocked preview overwrite note={note_id} uid={uid}")
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Заметка открылась не полностью — откройте её заново, чтобы не потерять текст",
                        "item": current,
                        "preview_overwrite": True,
                    },
                )
        from_yjs = bool(body.yjs) and _realtime_yjs_enabled()
        try:
            item = notes_store.update_note(
                owner,
                note_id,
                title=body.title,
                body=body.description,
                kb_enabled=body.kb_enabled,
                expected_updated_at=None if from_yjs else body.expected_updated_at,
                expected_revision=None if from_yjs else body.expected_revision,
                actor_user_id=uid,
                realtime_source="yjs" if from_yjs else "",
            )
        except notes_store.NoteConflictError as e:
            raise HTTPException(
                status_code=409,
                detail={"message": "Заметка изменена другим участником", "item": e.note},
            ) from e
        if item is None:
            return None
        if from_yjs:
            from assistant.stores import note_ydoc

            yjs_saved.append((int(item.get("revision") or 1), str(item.get("body") or "")))
            note_ydoc.set_base(note_id, *yjs_saved[-1])
        from assistant.services import transcription_notes as tnotes

        if tnotes.is_transcription_note(item):
            item = tnotes.enrich(item)
        if body.description is not None or body.title is not None:
            item = _sync_local_note_hashtags(owner, item)
            _focus_note_changed(owner, note_id)
        else:
            item = _enrich_item_labels(owner, "local", item)
        if body.sync_todoist and item.get("todoist_id"):
            from assistant.compat.miniapp_shims import set_todoist_task_content

            set_todoist_task_content(
                str(item["todoist_id"]),
                content=body.title,
                description=body.description,
                telegram_user_id=uid,
            )
        elif body.sync_todoist and not item.get("todoist_id") and str(item.get("owner_user_id") or "") == str(uid):
            from assistant.compat.miniapp_shims import add_todoist_note

            tid = add_todoist_note(
                item["title"],
                item.get("body") or "",
                telegram_user_id=uid,
            )
            item = notes_store.update_note(owner, note_id, todoist_id=tid, actor_user_id=uid) or item
        return item

    item = await run_in_threadpool(_run)
    if not item:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    if yjs_saved:
        from assistant.realtime.yrooms import rooms as yjs_rooms

        yjs_rooms().mark_saved(int(note_id), *yjs_saved[-1])
    return {"ok": True, "item": item}


class _MiniappNotePinBody(BaseModel):
    pinned: bool = True


@miniapp_router.post("/notes/local/{note_id}/duplicate")
async def miniapp_local_note_duplicate(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        dup = notes_store.duplicate_note(uid, note_id)
        if not dup:
            return None
        return _sync_local_note_hashtags(uid, dup)

    item = await run_in_threadpool(_run)
    if not item:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "id": item["id"], "item": item}


@miniapp_router.post("/notes/local/{note_id}/copy-to-knowledge")
async def miniapp_local_note_copy_to_knowledge(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Копия заметки в Базу знаний: текст и листы, без участников, команд и ссылок исходной заметки."""
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        dup = notes_store.duplicate_note(uid, note_id, to_knowledge=True)
        if not dup:
            return None
        return _sync_local_note_hashtags(uid, dup)

    item = await run_in_threadpool(_run)
    if not item:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "id": item["id"], "item": item}


@miniapp_router.put("/notes/local/{note_id}/pin")
async def miniapp_local_note_pin(
    note_id: int,
    body: _MiniappNotePinBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> bool:
        access = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if access is None:
            return False
        notes_store.set_note_pinned(uid, note_id, bool(body.pinned))
        return True

    ok = await run_in_threadpool(_run)
    if not ok:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "pinned": bool(body.pinned)}


@miniapp_router.delete("/notes/local/{note_id}")
async def miniapp_local_note_delete(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> str:
        access = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if access is None:
            return "missing"
        owner = str(access.get("owner_user_id") or uid)
        if owner == str(uid):
            return "deleted" if notes_store.delete_note(uid, note_id) else "missing"
        from assistant.stores import note_members

        return "left" if note_members.remove_member(owner, note_id, uid) else "missing"

    action = await run_in_threadpool(_run)
    if action == "missing":
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    if action == "deleted":
        from assistant.stores import share_links as share_links_store

        await run_in_threadpool(share_links_store.revoke_share, str(uid), "local", note_id)
        from assistant.stores import share_comments as share_comments_store

        await run_in_threadpool(share_comments_store.delete_all_for_item, str(uid), "local", note_id)
    return {"ok": True, "left": action == "left"}


@miniapp_router.get("/notes/local/{note_id}")
async def miniapp_local_note_get(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    item = await run_in_threadpool(notes_store.get_accessible_note, uid, note_id)
    if not item:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    from assistant.services import transcription_notes as tnotes

    if tnotes.is_transcription_note(item):
        item = tnotes.enrich(item)
    item = await run_in_threadpool(_enrich_item_labels, uid, "local", item)
    if item.get("is_knowledge"):
        from assistant.stores import teams_store

        item["teams"] = (await run_in_threadpool(teams_store.teams_for_notes, [int(note_id)])).get(int(note_id), [])
    return {"ok": True, "item": item}


def _upgrade_stale_deck(note: dict[str, Any], sheet: dict[str, Any]) -> dict[str, Any]:
    """Презентация, сделанная старой версией оформления (CSS и скрипт зашиты в её HTML), —
    перерисовать при открытии, чтобы исправления шаблонов доходили и до старых презентаций."""
    from assistant.lib import canvas_templates
    from assistant.lib import web_canvas as web_canvas_lib
    from assistant.stores import note_sheets as note_sheets_store

    body = str(sheet.get("body") or "")
    if web_canvas_lib.normalize_kind(sheet.get("kind")) != web_canvas_lib.SHEET_KIND_WEB or not canvas_templates.is_stale(body):
        return sheet
    try:
        sid = int(sheet.get("id"))
    except (TypeError, ValueError):
        return sheet  # основной лист — не презентация
    try:
        fresh = canvas_templates.refinalize(body)
        if not fresh:
            return sheet
        updated = note_sheets_store.update_sheet(int(note["id"]), sid, body=fresh, kind=web_canvas_lib.SHEET_KIND_WEB)
        return updated or sheet
    except Exception as e:  # noqa: BLE001 — не мешаем открыть заметку
        print(f"[web_canvas] upgrade_stale_deck failed note={note.get('id')} sheet={sheet.get('id')} err={e!r}")
        return sheet


@miniapp_router.get("/notes/local/{note_id}/sheets")
async def miniapp_note_sheets_list(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> list[dict[str, Any]] | None:
        note = notes_store.get_accessible_note(uid, note_id)
        if not note:
            return None
        sheets = note_sheets_store.list_sheets(note)
        return [_upgrade_stale_deck(note, sh) for sh in sheets]

    sheets = await run_in_threadpool(_run)
    if sheets is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "sheets": sheets}


@miniapp_router.post("/notes/local/{note_id}/sheets")
async def miniapp_note_sheet_create(
    note_id: int,
    body: _MiniappNoteSheetCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        from assistant.lib import web_canvas as web_canvas_lib

        note = notes_store.get_accessible_note(uid, note_id)
        if not note:
            return None
        kind = web_canvas_lib.normalize_kind(body.kind)
        sheet_body = body.description if body.description is not None else ""
        if kind == web_canvas_lib.SHEET_KIND_WEB:
            # Empty body → default stub deck so the sheet opens as a canvas
            sheet_body = web_canvas_lib.finalize_canvas_html(sheet_body or "")
        return note_sheets_store.create_sheet(
            note, body.title, body=sheet_body or "", kind=kind
        )

    try:
        item = await run_in_threadpool(_run)
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    await run_in_threadpool(_memory_sheet_changed, uid, note_id)
    return {"ok": True, "item": item}


@miniapp_router.post("/notes/local/{note_id}/make-summary")
async def miniapp_note_make_summary(
    note_id: int,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.services import transcription_notes as tnotes

    uid = int(principal.telegram_user_id)

    def _begin() -> dict[str, Any]:
        return tnotes.begin_make_summary(uid, note_id)

    try:
        item = await run_in_threadpool(_begin)
    except tnotes.TranscriptionNoteError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    background_tasks.add_task(tnotes.generate_summary_for_note, uid, note_id)
    return {"ok": True, "item": item}


@miniapp_router.get("/memory/status")
async def miniapp_memory_status(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Сколько материалов пользователя уже в семантической памяти Лео (для строки в чате)."""
    from assistant.services import semantic_memory

    return await run_in_threadpool(semantic_memory.status, int(principal.telegram_user_id))


class _SpeakerConfirmBody(BaseModel):
    label: str
    name: str = ""
    is_me: bool = False


@miniapp_router.post("/notes/local/{note_id}/speakers")
async def miniapp_note_confirm_speaker(
    note_id: int,
    body: _SpeakerConfirmBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Спикер 2 — это Саша»: пересобрать исполнителей саммари без нового STT/LLM."""
    from assistant.services import transcription_notes as tnotes

    uid = int(principal.telegram_user_id)
    try:
        item = await run_in_threadpool(
            tnotes.confirm_speaker, uid, note_id, body.label, body.name, is_me=body.is_me
        )
    except tnotes.TranscriptionNoteError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "item": item}


@miniapp_router.get("/notes/local/{note_id}/speakers")
async def miniapp_note_speakers(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Кто есть кто»: спикеры с репликами, догадками и кандидатами в имена."""
    from assistant.services import transcription_notes as tnotes

    uid = int(principal.telegram_user_id)
    try:
        return await run_in_threadpool(tnotes.speakers_view, uid, note_id)
    except tnotes.TranscriptionNoteError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@miniapp_router.get("/notes/local/{note_id}/transcript/dialog")
async def miniapp_note_transcript_dialog(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Транскрипт как переписка: реплики по спикерам, свои — справа."""
    from assistant.services import transcription_notes as tnotes

    uid = int(principal.telegram_user_id)
    try:
        return await run_in_threadpool(tnotes.transcript_dialog, uid, note_id)
    except tnotes.TranscriptionNoteError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class _SpeakerMergeBody(BaseModel):
    source: str
    target: str


@miniapp_router.post("/notes/local/{note_id}/speakers/merge")
async def miniapp_note_merge_speakers(
    note_id: int,
    body: _SpeakerMergeBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Диаризация разделила одного человека на двоих — склеить."""
    from assistant.services import transcription_notes as tnotes

    uid = int(principal.telegram_user_id)
    try:
        item = await run_in_threadpool(
            tnotes.merge_speakers, uid, note_id, body.source, body.target
        )
    except tnotes.TranscriptionNoteError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "item": item}


def _truthy_form_flag(raw: str | bool | None) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on", "summary")


@miniapp_router.post("/notes/transcriptions/upload")
async def miniapp_transcription_upload(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    url: str = Form(""),
    with_summary: str = Form("0"),
    meeting: str = Form(""),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Загрузка аудио/видео из миниаппа → транскрипция (+ опционально саммари).

    Вместо файла можно передать url — прямую ссылку, Яндекс.Диск или Google Диск.
    meeting — JSON встречи (из окна встречи «Добавить запись»): транскрипция получает
    её тему и сразу привязывается к встрече.
    """
    from assistant.integrations import transcribe as obu
    from assistant.lib.usage_store import insert_usage_event
    from assistant.services import transcription_notes as tnotes

    source_url = (url or "").strip()
    if file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Пустой файл")
        max_bytes = 100 * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 100 МБ)")
        filename = str(file.filename or "audio.mp3").strip() or "audio.mp3"
        content_type = file.content_type
        source_url = ""
    elif source_url:
        from assistant.lib.media_download import download_url_bytes

        try:
            data, filename = await run_in_threadpool(download_url_bytes, source_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            print(f"[miniapp_transcription_upload] url download failed url={source_url!r} err={e!r}")
            raise HTTPException(status_code=502, detail="Не удалось скачать файл по ссылке") from e
        filename = str(filename or "media.mp4").strip() or "media.mp4"
        content_type = None
    else:
        raise HTTPException(status_code=400, detail="Выберите файл или вставьте ссылку")
    source_filename = filename
    source_size = len(data)
    want_summary = _truthy_form_flag(with_summary)
    try:
        data, filename = await run_in_threadpool(
            _prepare_miniapp_voice_audio,
            data,
            filename,
            content_type,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    stem = Path(source_filename).stem.strip() or "Транскрипция"
    title = stem[:120]
    meeting_ev: dict[str, Any] = {}
    if meeting.strip():
        try:
            parsed = json.loads(meeting)
            meeting_ev = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            meeting_ev = {}
    if meeting_ev.get("summary"):
        title = str(meeting_ev.get("summary") or "").strip()[:120] or title
    username = None
    if isinstance(principal.user, dict) and principal.user.get("username"):
        username = str(principal.user.get("username") or "").strip() or None
    uid = int(principal.telegram_user_id)

    def _transcribe_and_create() -> dict[str, Any]:
        from assistant.stores import voice_prefs

        result = voice_prefs.process_transcript_result(
            uid, obu.transcribe_bytes(data, filename)
        )
        transcript = (result.formatted_text or result.plain_text or "").strip()
        if not transcript:
            raise RuntimeError("(пустая транскрипция)")
        insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id=result.job_id,
            usage={
                "text": transcript[:4000],
                "source": "miniapp_transcription_upload",
                "filename": filename,
                "with_summary": want_summary,
            },
            telegram_user_id=str(uid),
            telegram_username=username,
        )
        meta = {
            "source": "miniapp_url" if source_url else "miniapp_upload",
            "filename": source_filename,
            "job_id": result.job_id,
        }
        if source_url:
            meta["url"] = source_url
        note = tnotes.create_transcript_only(
            uid,
            title=title,
            transcript=transcript,
            meta=meta,
            segments=getattr(result, "segments", None),
        )
        if meeting_ev.get("id") or meeting_ev.get("event_id"):
            try:
                from assistant.stores import meeting_links

                meeting_links.link(uid, note["id"], meeting_ev, source="upload")
            except Exception as e:
                print(f"[miniapp_transcription_upload] meeting link failed note={note.get('id')} err={e!r}")
        return note

    try:
        item = await run_in_threadpool(_transcribe_and_create)
    except RuntimeError as e:
        print(
            "[miniapp_transcription_upload] failed "
            f"source_filename={source_filename!r} filename={filename!r} "
            f"content_type={content_type!r} source_bytes={source_size} "
            f"bytes={len(data)} err={e!r}"
        )
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        print(
            "[miniapp_transcription_upload] failed "
            f"source_filename={source_filename!r} filename={filename!r} "
            f"err={e!r}"
        )
        raise HTTPException(status_code=502, detail=f"Транскрибация: {e}") from e

    if want_summary:
        note_id = int(item["id"])

        def _begin() -> dict[str, Any]:
            return tnotes.begin_make_summary(uid, note_id)

        try:
            item = await run_in_threadpool(_begin)
        except tnotes.TranscriptionNoteError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background_tasks.add_task(tnotes.generate_summary_for_note, uid, note_id)

    return {"ok": True, "id": item["id"], "item": item, "with_summary": want_summary}


@miniapp_router.post("/notes/local/{note_id}/pdf")
async def miniapp_local_note_pdf(
    note_id: int,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    note = await run_in_threadpool(notes_store.get_accessible_note, uid, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Заметка не найдена")

    def _send() -> None:
        from assistant.skills.journal_pdf import deliver_note_pdf_sync

        deliver_note_pdf_sync(
            telegram_user_id=uid, note_id=int(note_id), notify_on_fail=True
        )

    background_tasks.add_task(_send)
    return {"ok": True}


# Выгрузка заметки файлом: PDF / Word / TXT — в чат с Лео или на устройство.
# «На устройство» — одноразовая ссылка на 10 минут: её открывает и браузер (fetch → blob),
# и Telegram.WebApp.downloadFile (на iOS скачать blob из мини-аппа нельзя).
_NOTE_EXPORT_TTL_S = 600
_note_export_files: dict[str, tuple[float, bytes, str, str]] = {}


class _MiniappNoteExport(BaseModel):
    format: str = "pdf"
    to: str = "chat"
    # to=device: True — файл прямо в ответе (браузер сохраняет сразу), False — одноразовая ссылка (Telegram).
    inline: bool = False


def _note_export_file_response(data: bytes, fname: str, mime: str) -> Response:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", fname).strip("_") or "file"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname)}",
            "Cache-Control": "no-store",
        },
    )


def _note_export_stash(data: bytes, fname: str, mime: str) -> str:
    now = time.time()
    for k in [k for k, v in _note_export_files.items() if v[0] < now]:
        _note_export_files.pop(k, None)
    token = secrets.token_urlsafe(24)
    _note_export_files[token] = (now + _NOTE_EXPORT_TTL_S, data, fname, mime)
    return token


async def _miniapp_note_export(
    build: Callable[[], tuple[bytes, str, str, str]],
    body: _MiniappNoteExport,
    background_tasks: BackgroundTasks,
    uid: int,
) -> Any:
    from assistant.lib.note_export import FORMATS

    fmt = (body.format or "pdf").strip().lower()
    if fmt not in FORMATS:
        raise HTTPException(status_code=400, detail="Неизвестный формат файла")
    to = (body.to or "chat").strip().lower()
    if to == "device":
        try:
            data, fname, _caption, mime = await run_in_threadpool(build)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if body.inline:
            return _note_export_file_response(data, fname, mime)
        token = _note_export_stash(data, fname, mime)
        return {"ok": True, "url": f"/api/miniapp/notes/export/file/{token}", "filename": fname}

    def _send() -> None:
        from assistant.lib.telegram_notify import send_user_document, send_user_message

        try:
            data, fname, caption, mime = build()
        except Exception as e:
            send_user_message(uid, f"Не удалось сделать файл: {e}")
            return
        if not send_user_document(uid, data, filename=fname, caption=caption, mime=mime.split(";")[0]):
            send_user_message(uid, "Не удалось отправить файл в чат.")

    background_tasks.add_task(_send)
    return {"ok": True}


@miniapp_router.post("/notes/local/{note_id}/export")
async def miniapp_local_note_export(
    note_id: int,
    body: _MiniappNoteExport,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> Any:
    from assistant.lib.note_export import note_export_for_user
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    note = await run_in_threadpool(notes_store.get_accessible_note, uid, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return await _miniapp_note_export(
        lambda: note_export_for_user(str(uid), int(note_id), body.format),
        body,
        background_tasks,
        uid,
    )


@miniapp_router.post("/notes/journal/{event_id}/export")
async def miniapp_journal_export(
    event_id: int,
    body: _MiniappNoteExport,
    background_tasks: BackgroundTasks,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> Any:
    from assistant.lib.note_export import journal_export_for_user

    uid = int(principal.telegram_user_id)
    row = await run_in_threadpool(get_user_usage_event, str(uid), event_id)
    if not row or str(row.get("operation") or "") not in ("obuchat_transcribe", "summarize"):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return await _miniapp_note_export(
        lambda: journal_export_for_user(str(uid), int(event_id), body.format),
        body,
        background_tasks,
        uid,
    )


@miniapp_router.get("/notes/export/file/{token}")
async def miniapp_note_export_file(token: str) -> Response:
    item = _note_export_files.get(token)
    if not item or item[0] < time.time():
        _note_export_files.pop(token, None)
        raise HTTPException(status_code=404, detail="Ссылка устарела — выгрузите файл ещё раз")
    _exp, data, fname, mime = item
    return _note_export_file_response(data, fname, mime)


class _MiniappSheetOrder(BaseModel):
    order: list[str] = Field(default_factory=list)


@miniapp_router.put("/notes/local/{note_id}/sheets/order")
async def miniapp_note_sheets_order(
    note_id: int,
    body: _MiniappSheetOrder,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Порядок вкладок листов (перетаскивание). «Основную» тоже можно двигать."""
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> list[dict[str, Any]] | None:
        if notes_store.get_accessible_note(uid, note_id, with_sharing=False) is None:
            return None
        note = notes_store.get_accessible_note(uid, note_id)
        return note_sheets_store.set_sheet_order(note, body.order)

    try:
        sheets = await run_in_threadpool(_run)
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if sheets is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "sheets": sheets}


@miniapp_router.patch("/notes/local/{note_id}/sheets/{sheet_id}")
async def miniapp_note_sheet_patch(
    note_id: int,
    sheet_id: str,
    body: _MiniappNoteSheetPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    if note_sheets_store.is_primary_id(sheet_id):
        # Основной лист: переименовать можно, как любой; текст — через заметку.
        if body.title is None or body.description is not None or body.kind is not None:
            raise HTTPException(status_code=400, detail="Основную правьте через заметку")

        def _rename() -> dict[str, Any] | None:
            note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
            if note is None:
                return None
            owner = str(note.get("owner_user_id") or uid)
            return note_sheets_store.rename_primary(owner, note, str(body.title or ""), actor_user_id=uid)

        try:
            primary = await run_in_threadpool(_rename)
        except note_sheets_store.SheetError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if primary is None:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        return {"ok": True, "item": primary, "note_updated_at": primary.get("updated_at")}
    if body.title is None and body.description is None and body.kind is None:
        raise HTTPException(status_code=400, detail="Укажите title и/или description")
    try:
        extra_id = int(sheet_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="Некорректный лист") from e

    def _run() -> dict[str, Any] | None:
        from assistant.lib import web_canvas as web_canvas_lib

        if notes_store.get_accessible_note(uid, note_id, with_sharing=False) is None:
            return None
        existing = note_sheets_store.get_extra_sheet(note_id, extra_id)
        kind = (
            web_canvas_lib.normalize_kind(body.kind)
            if body.kind is not None
            else None
        )
        sheet_body = body.description
        effective_kind = kind or (
            web_canvas_lib.normalize_kind((existing or {}).get("kind"))
            if existing
            else web_canvas_lib.SHEET_KIND_DOC
        )
        if sheet_body is not None and effective_kind == web_canvas_lib.SHEET_KIND_WEB:
            sheet_body = web_canvas_lib.finalize_canvas_html(sheet_body)
        from_yjs = (
            bool(body.yjs)
            and _realtime_yjs_enabled()
            and effective_kind == web_canvas_lib.SHEET_KIND_BOARD
            and sheet_body is not None
        )
        item = note_sheets_store.update_sheet(
            note_id,
            extra_id,
            title=body.title,
            body=sheet_body,
            kind=kind,
            expected_revision=None if from_yjs else body.expected_revision,
            realtime_source="yjs" if from_yjs else "",
        )
        if from_yjs and item:
            from assistant.stores import note_ydoc

            yjs_saved.append((int(item.get("revision") or 1), str(item.get("body") or "")))
            note_ydoc.set_base(note_id, *yjs_saved[-1], sheet_id=str(extra_id))
        return item

    yjs_saved: list[tuple[int, str]] = []
    try:
        item = await run_in_threadpool(_run)
    except note_sheets_store.SheetConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={"message": "Лист изменён другим участником", "item": e.sheet},
        ) from e
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    if yjs_saved:
        from assistant.realtime.yrooms import rooms as yjs_rooms

        yjs_rooms().mark_saved(int(note_id), *yjs_saved[-1], sheet=str(extra_id))
    await run_in_threadpool(_memory_sheet_changed, uid, note_id)
    note_updated_at = None
    try:
        fresh = await run_in_threadpool(
            partial(notes_store.get_accessible_note, uid, note_id, with_sharing=False)
        )
        note_updated_at = (fresh or {}).get("updated_at")
    except Exception:
        note_updated_at = None
    return {"ok": True, "item": item, "note_updated_at": note_updated_at}


@miniapp_router.delete("/notes/local/{note_id}/sheets/{sheet_id}")
async def miniapp_note_sheet_delete(
    note_id: int,
    sheet_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    if note_sheets_store.is_primary_id(sheet_id):
        # Основной лист: его место занимает следующий текстовый лист.
        def _del_primary() -> dict[str, Any] | None:
            note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
            if note is None:
                return None
            owner = str(note.get("owner_user_id") or uid)
            return note_sheets_store.delete_primary(owner, note, actor_user_id=uid)

        try:
            res = await run_in_threadpool(_del_primary)
        except note_sheets_store.SheetError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if res is None:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        await run_in_threadpool(_memory_sheet_changed, uid, note_id)
        return {"ok": True, **res}
    try:
        extra_id = int(sheet_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="Некорректный лист") from e

    def _run() -> bool | None:
        if notes_store.get_accessible_note(uid, note_id, with_sharing=False) is None:
            return None
        return note_sheets_store.delete_sheet(note_id, extra_id)

    ok = await run_in_threadpool(_run)
    if ok is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    if not ok:
        raise HTTPException(status_code=404, detail="Лист не найден")
    await run_in_threadpool(_memory_sheet_changed, uid, note_id)
    return {"ok": True}


@miniapp_router.get("/notes/local/{note_id}/members")
async def miniapp_note_members_list(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    note = await run_in_threadpool(notes_store.get_accessible_note, uid, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return {"ok": True, "members": note.get("members") or []}


@miniapp_router.get("/notes/local/{note_id}/team-shares")
async def miniapp_note_team_shares_get(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store
    from assistant.stores import teams_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        note = notes_store.get_note(uid, note_id)
        if not note:
            return None
        if not note.get("is_knowledge"):
            raise HTTPException(
                status_code=400,
                detail="С командой можно делиться только документами базы знаний",
            )
        return {
            "ok": True,
            "teams": teams_store.list_note_teams(uid, note_id),
            "all_teams": teams_store.list_teams(uid),
        }

    try:
        data = await run_in_threadpool(_run)
    except HTTPException:
        raise
    if not data:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return data


@miniapp_router.put("/notes/local/{note_id}/team-shares")
async def miniapp_note_team_shares_put(
    note_id: int,
    body: _MiniappNoteTeamSharesBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store
    from assistant.stores import teams_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        note = notes_store.get_note(uid, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        return teams_store.set_note_teams(uid, note_id, list(body.team_ids or []))

    try:
        result = await run_in_threadpool(_run)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await run_in_threadpool(
        partial(
            _notify_team_members_added,
            result.get("added_members") or [],
            _principal_display_name(principal),
        )
    )
    return {"ok": True, "teams": result.get("teams") or []}


@miniapp_router.post("/notes/local/{note_id}/members")
async def miniapp_note_members_add(
    note_id: int,
    body: _MiniappNoteMemberAdd,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    await run_in_threadpool(_remember_principal_profile, principal)

    def _run() -> tuple[dict[str, Any], dict[str, Any]]:
        note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        owner = str(note.get("owner_user_id") or uid)
        member_id, _contact = note_members.resolve_contact_telegram_id(
            owner_user_id=uid,
            email=body.email,
            telegram_user_id=body.telegram_user_id,
            telegram_username=body.telegram_username,
            owner_username=_miniapp_tg_username(principal),
        )
        if int(member_id) == int(owner):
            raise HTTPException(status_code=400, detail="Это владелец заметки")
        member = note_members.add_member(owner, note_id, member_id)
        fresh = notes_store.get_accessible_note(uid, note_id)
        return member, fresh or note

    try:
        member, note = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    title = str((note or {}).get("title") or "Без названия")
    await run_in_threadpool(
        partial(
            _notify_member_added,
            member_user_id=int(member["user_id"]),
            title=title,
            note_id=int(note_id),
            adder_name=_principal_display_name(principal),
        )
    )
    return {"ok": True, "member": member, "item": note}


class _MeetingTaskOpBody(BaseModel):
    op: str = ""
    reason: Optional[str] = None
    fields: Optional[dict[str, Any]] = None


@miniapp_router.get("/notes/local/{note_id}/meeting-tasks")
async def miniapp_note_meeting_tasks(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Таблица задач встречи: пункты с живым статусом и счётчик «Разобрано N из M»."""
    from assistant.services import meeting_tasks
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        return meeting_tasks.rows(note)

    return {"ok": True, **(await run_in_threadpool(_run))}


@miniapp_router.post("/notes/local/{note_id}/meeting-tasks/{item_id}")
async def miniapp_note_meeting_task_op(
    note_id: int,
    item_id: str,
    body: _MeetingTaskOpBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Действие с пунктом из таблицы: Сделано / Не задача / Дубль / Вернуть / правка ячейки."""
    from assistant.services import meeting_tasks

    uid = int(principal.telegram_user_id)
    try:
        item = await run_in_threadpool(
            partial(
                meeting_tasks.apply, uid, note_id, item_id, body.op,
                by=_principal_display_name(principal), reason=body.reason, fields=body.fields,
            )
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "item": item}


@miniapp_router.post("/notes/local/{note_id}/share-attendees")
async def miniapp_note_share_attendees(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Поделиться с участниками»: участники встречи с Лео получают доступ к заметке."""
    from assistant.services import meeting_share
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    await run_in_threadpool(_remember_principal_profile, principal)

    def _run() -> tuple[dict[str, Any], dict[str, Any]]:
        note = notes_store.get_note(uid, note_id)
        if not note:
            raise HTTPException(status_code=403, detail="Поделиться может только владелец заметки")
        res = meeting_share.share_with_attendees(uid, note_id, owner_username=_miniapp_tg_username(principal))
        return res, notes_store.get_accessible_note(uid, note_id) or note

    try:
        res, note = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    title = str((note or {}).get("title") or "Встреча")
    adder = _principal_display_name(principal)
    for m in res["added"]:
        try:
            await run_in_threadpool(
                partial(_notify_member_added, member_user_id=int(m["user_id"]), title=title, note_id=int(note_id), adder_name=adder)
            )
        except Exception as e:
            print(f"[share_attendees] notify failed note={note_id} uid={m['user_id']} err={e!r}")
    return {"ok": True, **res, "item": note}


@miniapp_router.delete("/notes/local/{note_id}/members/{member_id}")
async def miniapp_note_members_remove(
    note_id: int,
    member_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> bool:
        note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if not note:
            return False
        owner = str(note.get("owner_user_id") or uid)
        if str(uid) != owner and int(member_id) != uid:
            raise HTTPException(
                status_code=403, detail="Удалять участников может только автор"
            )
        return note_members.remove_member(owner, note_id, member_id)

    try:
        ok = await run_in_threadpool(_run)
    except HTTPException:
        raise
    if not ok:
        raise HTTPException(status_code=404, detail="Участник не найден")
    return {"ok": True}


@miniapp_router.post("/notes/local/{note_id}/collab")
async def miniapp_note_collab(
    note_id: int,
    body: Optional[_MiniappNoteCollabBody] = None,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    payload = body or _MiniappNoteCollabBody()

    def _run() -> dict[str, Any] | None:
        note = notes_store.get_accessible_note(uid, note_id)
        if not note:
            return None
        owner = str(note.get("owner_user_id") or uid)
        _, name, username = _comment_author_from_principal(principal)
        peers = note_members.heartbeat(
            owner,
            note_id,
            uid,
            cursor=payload.cursor,
            display_name=name,
            username=username,
        )
        others = [p for p in peers if str(p.get("user_id")) != str(uid)]
        from assistant.realtime.hub import HUB, note_topic

        # Участникам на сокете — сразу, без ожидания их опроса.
        HUB.publish(note_topic(note_id), {"type": "presence", "note_id": int(note_id), "peers": peers})
        from assistant.stores import note_sheets as note_sheets_store

        return {
            "ok": True,
            "item": note,
            "peers": others,
            "members": note.get("members") or [],
            # Версии дополнительных листов: клиент подтягивает изменённые другими участниками.
            "sheets": note_sheets_store.list_sheet_versions(note_id)
            if note_sheets_store.note_allows_sheets(note)
            else [],
            "sheet_order": note_sheets_store.sheet_order_ids(note)
            if note_sheets_store.note_allows_sheets(note)
            else [],
        }

    data = await run_in_threadpool(_run)
    if not data:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    return data


@miniapp_router.delete("/notes/local/{note_id}/collab")
async def miniapp_note_collab_leave(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> None:
        note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
        if not note:
            return
        owner = str(note.get("owner_user_id") or uid)
        note_members.leave_presence(owner, note_id, uid)

    await run_in_threadpool(_run)
    return {"ok": True}


# ---- Живые обновления (WebSocket) ----
# Сокет несёт только сигналы «изменилось» и присутствие; данные — обычными запросами.
_REALTIME_AUTH_TIMEOUT_SEC = 10.0
_REALTIME_IDLE_TIMEOUT_SEC = 70.0
_REALTIME_CURSOR_MIN_INTERVAL_SEC = 0.08
_REALTIME_MAX_NOTE_SUBS = 8
_REALTIME_TOPIC_RE = re.compile(r"^(note|user):\d{1,18}$")


def _realtime_origin_ok(websocket: WebSocket) -> bool:
    origin = (websocket.headers.get("origin") or "").strip()
    if not origin:
        return True
    from urllib.parse import urlparse

    host = (websocket.headers.get("x-forwarded-host") or websocket.headers.get("host") or "").strip().lower()
    try:
        origin_host = (urlparse(origin).netloc or "").lower()
    except Exception:
        return False
    return bool(host) and origin_host == host


async def _realtime_principal(websocket: WebSocket, headers: Any) -> _MiniappPrincipal:
    """Та же проверка, что у HTTP: initData / session / dev — из первого сообщения, cookie — из рукопожатия."""
    raw: list[tuple[bytes, bytes]] = []
    if isinstance(headers, dict):
        for k, v in headers.items():
            key = str(k or "").strip().lower()
            if key in ("authorization", "x-telegram-init-data") and isinstance(v, str) and v.strip():
                raw.append((key.encode("latin-1"), v.strip().encode("utf-8")))
    cookie = websocket.headers.get("cookie")
    if cookie:
        raw.append((b"cookie", cookie.encode("latin-1", "ignore")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/miniapp/ws",
        "headers": raw,
        "query_string": b"",
        "client": websocket.client,
        "scheme": "https",
    }
    return await _resolve_miniapp_principal(Request(scope), enforce_access=True)


def _realtime_yjs_enabled() -> bool:
    return (os.getenv("REALTIME_YJS", "1") or "").strip().lower() not in ("0", "false", "no", "off")


def _realtime_note_access(uid: int, note_id: int) -> tuple[str, bool] | None:
    """(владелец, совместная правка Yjs) или None — нет доступа.
    Yjs — только у обычных общих заметок (есть участники кроме владельца)."""
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    note = notes_store.get_accessible_note(uid, note_id)
    if not note:
        return None
    owner = str(note.get("owner_user_id") or uid)
    yjs = (
        _realtime_yjs_enabled()
        and not str(note.get("role") or "")
        and not tnotes.is_transcription_note(note)
        and len(note.get("members") or []) > 1
    )
    return owner, yjs


def _realtime_b64(raw: Any) -> bytes | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        import base64

        return base64.b64decode(raw, validate=True)
    except Exception:
        return None


@miniapp_router.websocket("/ws")
async def miniapp_realtime_ws(websocket: WebSocket) -> None:
    from assistant.realtime.hub import HUB, Connection, note_topic, user_topic
    from assistant.realtime.yrooms import normalize_sheet as normalize_yjs_sheet
    from assistant.realtime.yrooms import rooms as yjs_rooms
    from assistant.stores import note_members

    if not _realtime_origin_ok(websocket):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    HUB.bind_loop(asyncio.get_running_loop())
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=_REALTIME_AUTH_TIMEOUT_SEC)
        if not isinstance(first, dict) or first.get("type") != "auth":
            raise ValueError("auth expected")
    except Exception:
        await websocket.close(code=4401)
        return
    try:
        principal = await _realtime_principal(websocket, first.get("headers"))
    except HTTPException as e:
        try:
            await websocket.send_json({"type": "error", "status": e.status_code})
        finally:
            await websocket.close(code=4403 if e.status_code == 403 else 4401)
        return

    uid = int(principal.telegram_user_id)
    _, display_name, username = _comment_author_from_principal(principal)
    conn = Connection(str(uid))
    owners: dict[int, str] = {}  # подписанные заметки → владелец (ключ присутствия)
    yjs_notes: set[int] = set()  # подписки, где разрешена совместная правка Yjs
    yjs_joined: set[tuple[int, str]] = set()  # (заметка, лист): "main" — текст, иначе холст
    present: set[int] = set()  # заметки, где мы отметились курсором
    last_cursor_at: dict[int, float] = {}

    async def _sender() -> None:
        while True:
            text = await conn.queue.get()
            if text is None:
                break
            await websocket.send_text(text)

    def _presence_event(owner: str, note_id: int) -> dict[str, Any]:
        return {"type": "presence", "note_id": note_id, "peers": note_members.list_peers(owner, note_id)}

    def _leave(owner: str, note_id: int) -> None:
        present.discard(note_id)
        note_members.leave_presence(owner, note_id, uid)
        HUB.publish(note_topic(note_id), _presence_event(owner, note_id))

    HUB.subscribe(conn, user_topic(uid))
    sender = asyncio.create_task(_sender())
    conn.push(json.dumps({"type": "hello", "user_id": str(uid)}))
    try:
        while True:
            msg = await asyncio.wait_for(websocket.receive_json(), timeout=_REALTIME_IDLE_TIMEOUT_SEC)
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "ping":
                conn.push('{"type":"pong"}')
                continue
            try:
                note_id = int(msg.get("note_id"))
            except (TypeError, ValueError):
                continue
            if kind == "sub":
                if note_id not in owners and len(owners) >= _REALTIME_MAX_NOTE_SUBS:
                    conn.push(json.dumps({"type": "sub_denied", "note_id": note_id}))
                    continue
                access = await run_in_threadpool(_realtime_note_access, uid, note_id)
                if access is None:
                    conn.push(json.dumps({"type": "sub_denied", "note_id": note_id}))
                    continue
                owners[note_id], yjs_ok = access
                if yjs_ok:
                    yjs_notes.add(note_id)
                else:
                    yjs_notes.discard(note_id)
                HUB.subscribe(conn, note_topic(note_id))
                conn.push(json.dumps({"type": "subscribed", "note_id": note_id, "yjs": yjs_ok}))
            elif kind == "unsub":
                HUB.unsubscribe(conn, note_topic(note_id))
                owner = owners.pop(note_id, None)
                yjs_notes.discard(note_id)
                for key in [k for k in yjs_joined if k[0] == note_id]:
                    yjs_joined.discard(key)
                    await yjs_rooms().leave(conn, note_id, key[1])
                if owner is not None and note_id in present:
                    _leave(owner, note_id)
            elif kind is not None and str(kind).startswith("y."):
                ykey = (note_id, normalize_yjs_sheet(msg.get("sheet_id")))
                if kind == "y.join":
                    if note_id not in yjs_notes:
                        err = {"type": "y.error", "note_id": note_id, "reason": "not_allowed"}
                        if ykey[1] != "main":
                            err["sheet_id"] = ykey[1]
                        conn.push(json.dumps(err))
                        continue
                    if ykey not in yjs_joined and len(yjs_joined) >= _REALTIME_MAX_NOTE_SUBS * 2:
                        continue
                    yjs_joined.add(ykey)
                    await yjs_rooms().join(conn, note_id, ykey[1])
                elif kind == "y.update" and ykey in yjs_joined:
                    data = _realtime_b64(msg.get("update"))
                    if data is not None:
                        yjs_rooms().update(conn, note_id, data, ykey[1])
                elif kind == "y.compact" and ykey in yjs_joined:
                    data = _realtime_b64(msg.get("update"))
                    upto = msg.get("upto")
                    if data is not None and isinstance(upto, int):
                        yjs_rooms().compact(conn, note_id, data, upto, ykey[1])
                elif kind == "y.leave" and ykey in yjs_joined:
                    yjs_joined.discard(ykey)
                    await yjs_rooms().leave(conn, note_id, ykey[1])
            elif kind == "board.cursor" and note_id in owners:
                # Курсор на холсте: только рассылка участникам, без записи в присутствие.
                now = time.monotonic()
                ck = -note_id
                if now - last_cursor_at.get(ck, 0.0) < _REALTIME_CURSOR_MIN_INTERVAL_SEC:
                    continue
                last_cursor_at[ck] = now
                bx, by = msg.get("x"), msg.get("y")
                if not isinstance(bx, (int, float)) or not isinstance(by, (int, float)):
                    continue
                HUB.publish(note_topic(note_id), {
                    "type": "board.cursor", "note_id": note_id,
                    "sheet_id": normalize_yjs_sheet(msg.get("sheet_id")),
                    "x": round(float(bx), 1), "y": round(float(by), 1),
                    "user_id": str(uid), "name": display_name or username or "",
                })
            elif kind == "cursor" and note_id in owners:
                now = time.monotonic()
                if now - last_cursor_at.get(note_id, 0.0) < _REALTIME_CURSOR_MIN_INTERVAL_SEC:
                    continue
                last_cursor_at[note_id] = now
                raw_cursor = msg.get("cursor")
                cursor = int(raw_cursor) if isinstance(raw_cursor, (int, float)) else None
                note_members.heartbeat(
                    owners[note_id], note_id, uid,
                    cursor=cursor, display_name=display_name, username=username,
                )
                present.add(note_id)
                HUB.publish(note_topic(note_id), _presence_event(owners[note_id], note_id))
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as exc:
        print(f"[realtime] ws error uid={uid} err={exc!r}")
    finally:
        HUB.drop(conn)
        sender.cancel()
        if yjs_joined:
            # Отдельной задачей: обработчик сокета могут отменить при обрыве,
            # а уход из комнаты Yjs (передача посева, запись журнала) должен дойти до конца.
            async def _yjs_cleanup(keys: list[tuple[int, str]]) -> None:
                for nid, sheet in keys:
                    try:
                        await yjs_rooms().leave(conn, nid, sheet)
                    except Exception as exc:
                        print(f"[yjs] leave failed note={nid} sheet={sheet} err={exc!r}")

            asyncio.get_running_loop().create_task(_yjs_cleanup(list(yjs_joined)))
        for note_id in list(present):
            owner = owners.get(note_id)
            if owner is not None:
                _leave(owner, note_id)
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/internal/realtime/publish", include_in_schema=False)
async def internal_realtime_publish(request: Request) -> dict[str, Any]:
    """События из других процессов (бот). Только локально, мимо nginx, с общим секретом."""
    from assistant.realtime import notify as realtime_notify
    from assistant.realtime.hub import HUB

    client_host = (request.client.host if request.client else "") or ""
    proxied = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for")
    if proxied or client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=404, detail="Not Found")
    if not realtime_notify.secret_ok(request.headers.get(realtime_notify.SECRET_HEADER)):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")
    sent = 0
    for item in (body.get("events") if isinstance(body, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "")
        event = item.get("event")
        if not _REALTIME_TOPIC_RE.match(topic) or not isinstance(event, dict) or not isinstance(event.get("type"), str):
            continue
        if HUB.publish(topic, event):
            sent += 1
    return {"ok": True, "sent": sent}


def _viewer_may_see_user_photo(viewer: int, user_id: int) -> bool:
    if int(viewer) == int(user_id):
        return True
    from assistant.stores import note_members

    if note_members.shares_any_note_with(viewer, user_id):
        return True
    from assistant.stores import calendar_tasks as calendar_tasks_store

    if calendar_tasks_store.users_share_task(viewer, user_id):
        return True  # постановщик и исполнитель поручения видят фото друг друга
    contacts = _miniapp_contacts_by_email(int(viewer))
    for row in contacts.values():
        try:
            if int((row or {}).get("telegram_user_id") or 0) == int(user_id):
                return True
        except (TypeError, ValueError):
            continue
    return False


@miniapp_router.get("/users/{user_id}/photo")
async def miniapp_user_photo(
    user_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> Response:
    from assistant.stores import note_members

    viewer = int(principal.telegram_user_id)
    allowed = await run_in_threadpool(_viewer_may_see_user_photo, viewer, int(user_id))
    if not allowed:
        raise HTTPException(status_code=404, detail="Фото недоступно")
    from assistant.stores import user_avatar

    # Своя аватарка в Orbita важнее фото из Telegram.
    blob = await run_in_threadpool(user_avatar.get_avatar, int(user_id))
    if not blob:
        blob = await run_in_threadpool(note_members.cached_profile_photo, int(user_id))
    if not blob:
        raise HTTPException(status_code=404, detail="Фото недоступно")
    return Response(content=blob, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


def _viewer_may_see_username_photo(viewer: int, username: str) -> bool:
    from assistant.stores.contacts_store import normalize_telegram_username

    uname = normalize_telegram_username(username)
    if not uname:
        return False
    contacts = _miniapp_contacts_by_email(int(viewer))
    for row in contacts.values():
        if normalize_telegram_username(str((row or {}).get("telegram_username") or "")) == uname:
            return True
    return False


@miniapp_router.get("/contacts/telegram-photo/{username}")
async def miniapp_contact_telegram_photo(
    username: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> Response:
    from assistant.stores import note_members
    from assistant.stores.contacts_store import normalize_telegram_username

    uname = normalize_telegram_username(username)
    if not uname or len(uname) < 4:
        raise HTTPException(status_code=404, detail="Фото недоступно")
    viewer = int(principal.telegram_user_id)
    allowed = await run_in_threadpool(_viewer_may_see_username_photo, viewer, uname)
    if not allowed:
        raise HTTPException(status_code=404, detail="Фото недоступно")
    from assistant.stores import user_avatar
    from assistant.stores.telegram_registry import lookup_user_id

    blob = None
    known_uid = await run_in_threadpool(lookup_user_id, uname)
    if known_uid:
        blob = await run_in_threadpool(user_avatar.get_avatar, int(known_uid))
    if not blob:
        blob = await run_in_threadpool(note_members.cached_username_photo, uname)
    if not blob:
        raise HTTPException(status_code=404, detail="Фото недоступно")
    return Response(content=blob, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


@miniapp_router.get("/notes/{kind}/{item_id}/share")
async def miniapp_share_get(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        return {"shared": False, "url": None, "token": None, "access": None, "sheet_ids": []}
    links = await run_in_threadpool(share_links_store.list_active_shares, owner, kind, item_id)
    if not links:
        return {"shared": False, "url": None, "token": None, "access": None, "sheet_ids": [], "links": []}
    # Поля верхнего уровня — последняя ссылка (совместимость), links — все ссылки записи.
    return {**_share_response(links[0]), "links": [_share_response(x) for x in links]}


@miniapp_router.post("/notes/{kind}/{item_id}/share")
async def miniapp_share_create(
    kind: str,
    item_id: str,
    body: Optional[_MiniappShareCreate] = None,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    access = (body.access if body else None)
    sheet_ids = body.sheet_ids if body else None
    try:
        link = await run_in_threadpool(
            share_links_store.create_or_get_share,
            owner,
            kind,
            item_id,
            access,
            sheet_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _share_response(link)


@miniapp_router.delete("/notes/{kind}/{item_id}/share")
async def miniapp_share_revoke(
    kind: str,
    item_id: str,
    request: Request,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        return {"shared": False, "revoked": False, "access": None}
    token = str(request.query_params.get("token") or "").strip() or None
    revoked = await run_in_threadpool(share_links_store.revoke_share, owner, kind, item_id, token)
    left = await run_in_threadpool(share_links_store.list_active_shares, owner, kind, item_id)
    return {
        "shared": bool(left),
        "revoked": bool(revoked),
        "access": None,
        "links": [_share_response(x) for x in left],
    }


@miniapp_router.get("/notes/{kind}/{item_id}/comments")
async def miniapp_share_comments_list(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    from assistant.stores import note_chats

    rows = await run_in_threadpool(note_chats.visible_comments, owner, kind, item_id, uid)
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
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    author_id, author_name, author_username = _comment_author_from_principal(principal)
    prefix = body.prefix
    role = str(body.as_role or "").strip().lower()
    if role == "gpt":
        from assistant.integrations.openrouter_client import gpt_display_label

        author_name = gpt_display_label(body.model) or share_comments_store.GPT_AUTHOR_NAME
        author_username = share_comments_store.GPT_AUTHOR_USERNAME
        prefix = share_comments_store.GPT_PREFIX
    elif role.startswith("agent:"):
        from assistant.stores import user_agents

        aid = role.split(":", 1)[1].strip()
        agent = user_agents.get_agent(int(principal.telegram_user_id), aid)
        if not agent or not agent.get("can_delete"):
            raise HTTPException(status_code=404, detail="Агент не найден")
        author_name = str(agent.get("title") or "Агент")
        author_username = user_agents.AGENT_AUTHOR_USERNAME
        prefix = user_agents.comment_prefix(agent["id"])
    text = (body.body or "").strip()
    file_ids = [int(x) for x in (body.file_ids or []) if int(x) > 0]
    if not text:
        if file_ids:
            text = "📎"
        else:
            raise HTTPException(status_code=400, detail="Введите текст комментария")
    chat_id = await run_in_threadpool(
        _resolve_post_chat, owner, kind, str(item_id), uid, body.chat_id, body.parent_id
    )
    try:
        row = await run_in_threadpool(
            partial(
                share_comments_store.add_comment,
                owner,
                kind,
                item_id,
                author_user_id=author_id,
                author_name=author_name,
                author_username=author_username,
                body=text,
                quote=body.quote,
                prefix=prefix,
                suffix=body.suffix,
                parent_id=body.parent_id,
                chat_id=chat_id,
            )
        )
        if file_ids:
            from assistant.stores import comment_files

            await run_in_threadpool(
                comment_files.link_to_comment,
                file_ids,
                comment_id=int(row["id"]),
                owner_user_id=owner,
                item_kind=kind,
                item_id=item_id,
                author_user_id=author_id,
            )
            row = await run_in_threadpool(share_comments_store.get_comment, int(row["id"])) or row
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    out: dict[str, Any] = {"ok": True, "comment": _comment_api(row, viewer_uid=uid)}
    if kind == "chat":
        from assistant.stores import chat_threads as chat_threads_store

        role_l = str(body.as_role or "").strip().lower()
        is_user = role_l not in ("gpt",) and not role_l.startswith("agent:")
        thread = await run_in_threadpool(
            partial(
                chat_threads_store.after_comment,
                owner,
                item_id,
                text,
                is_user=is_user,
            )
        )
        if thread:
            out["thread"] = thread
    is_assistant = bool(
        role in ("gpt",)
        or role.startswith("agent:")
        or share_comments_store.is_gpt_comment(row)
        or share_comments_store.is_paie_comment(row)
        or share_comments_store.is_research_comment(row)
        or share_comments_store.is_agent_turn(row)
    )
    from assistant.stores import note_chats

    if not note_chats.comment_visible(row, None):
        # Личный чат: участникам заметки ничего не рассылаем.
        return out
    try:
        await run_in_threadpool(
            partial(
                _notify_discussion_comment,
                owner=owner,
                kind=kind,
                item_id=str(item_id),
                author_user_id=author_id,
                author_name=author_name,
                author_username=author_username,
                body=text,
                is_assistant=is_assistant,
            )
        )
    except Exception as e:
        print(f"[discuss_notify] schedule_fail err={e!r}")
    return out


@miniapp_router.post("/notes/{kind}/{item_id}/discuss-presence")
async def miniapp_discuss_presence_heartbeat(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import discussion_presence

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    _, name, username = _comment_author_from_principal(principal)
    present = await run_in_threadpool(
        discussion_presence.heartbeat,
        owner,
        kind,
        str(item_id),
        uid,
        display_name=name,
        username=username,
    )
    return {"ok": True, "present": present}


@miniapp_router.delete("/notes/{kind}/{item_id}/discuss-presence")
async def miniapp_discuss_presence_leave(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import discussion_presence

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        return {"ok": True}
    await run_in_threadpool(
        discussion_presence.leave, owner, kind, str(item_id), uid
    )
    return {"ok": True}


@miniapp_router.delete("/notes/{kind}/{item_id}/comments/{comment_id}")
async def miniapp_share_comments_delete(
    kind: str,
    item_id: str,
    comment_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    row = await run_in_threadpool(share_comments_store.get_comment, comment_id)
    if (
        not row
        or str(row.get("owner_user_id") or "") != owner
        or str(row.get("item_kind") or "") != kind
        or str(row.get("item_id") or "") != str(item_id)
    ):
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    from assistant.stores import note_chats

    if not note_chats.comment_visible(row, uid):
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    deleted = await run_in_threadpool(
        share_comments_store.delete_comment, comment_id, requester_user_id=uid
    )
    if not deleted:
        raise HTTPException(status_code=403, detail="Нельзя удалить этот комментарий")
    return {"ok": True}


def _note_chat_api(chat: dict[str, Any], uid: str) -> dict[str, Any]:
    if chat.get("is_comments"):
        return {**chat, "is_mine": False, "can_edit": False}
    return {
        "id": int(chat["id"]),
        "title": str(chat.get("title") or ""),
        "visibility": chat.get("visibility"),
        "author_user_id": chat.get("author_user_id"),
        "is_mine": chat.get("author_user_id") == uid,
        "can_edit": chat.get("author_user_id") == uid,
        "created_at": chat.get("created_at"),
        "last_at": chat.get("last_at") or chat.get("created_at"),
        "message_count": int(chat.get("message_count") or 0),
        "first_message": str(chat.get("first_message") or ""),
        "legacy": chat.get("origin") == "legacy",
        "last_seen_id": chat.get("last_seen_id"),
    }


def _note_chat_owner_or_404(uid: str, kind: str, item_id: str) -> str:
    from assistant.stores import note_chats

    if not note_chats.supports_chats(kind):
        raise HTTPException(status_code=404, detail="Чаты есть только у заметок")
    owner = _resolve_item_owner(uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return owner


@miniapp_router.get("/notes/{kind}/{item_id}/chats")
async def miniapp_note_chats_list(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Чаты обсуждения заметки, видимые пользователю, + общий чат «Комментарии» (id -1)."""
    from assistant.stores import note_chats

    uid = str(int(principal.telegram_user_id))

    def _run() -> list[dict[str, Any]]:
        owner = _note_chat_owner_or_404(uid, kind, item_id)
        return note_chats.list_chats(owner, kind, str(item_id), uid)

    chats = await run_in_threadpool(_run)
    return {"chats": [_note_chat_api(c, uid) for c in chats]}


@miniapp_router.post("/notes/{kind}/{item_id}/chats")
async def miniapp_note_chats_create(
    kind: str,
    item_id: str,
    body: _MiniappNoteChatCreate,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import note_chats

    uid = str(int(principal.telegram_user_id))

    def _run() -> dict[str, Any]:
        owner = _note_chat_owner_or_404(uid, kind, item_id)
        return note_chats.create_chat(
            owner, kind, str(item_id), author=uid, title=body.title, visibility=body.visibility
        )

    chat = await run_in_threadpool(_run)
    return {"ok": True, "chat": _note_chat_api(chat, uid)}


@miniapp_router.patch("/notes/{kind}/{item_id}/chats/{chat_id}")
async def miniapp_note_chats_patch(
    kind: str,
    item_id: str,
    chat_id: int,
    body: _MiniappNoteChatPatch,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Название и видимость («Только я» / «Все участники») меняет только автор чата."""
    from assistant.stores import note_chats

    uid = str(int(principal.telegram_user_id))

    def _run() -> dict[str, Any]:
        owner = _note_chat_owner_or_404(uid, kind, item_id)
        chat = note_chats.chat_for_item(chat_id, owner, kind, str(item_id))
        if not note_chats.can_view(chat, uid):
            raise HTTPException(status_code=404, detail="Чат не найден")
        assert chat is not None
        if chat["author_user_id"] != uid:
            raise HTTPException(status_code=403, detail="Настройки чата меняет его автор")
        return note_chats.update_chat(chat, title=body.title, visibility=body.visibility)

    chat = await run_in_threadpool(_run)
    return {"ok": True, "chat": _note_chat_api(chat, uid)}


@miniapp_router.delete("/notes/{kind}/{item_id}/chats/{chat_id}")
async def miniapp_note_chats_delete(
    kind: str,
    item_id: str,
    chat_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Удалить чат с ИИ вместе с сообщениями — только автор чата."""
    from assistant.stores import note_chats

    uid = str(int(principal.telegram_user_id))

    def _run() -> int:
        owner = _note_chat_owner_or_404(uid, kind, item_id)
        chat = note_chats.chat_for_item(chat_id, owner, kind, str(item_id))
        if not note_chats.can_view(chat, uid):
            raise HTTPException(status_code=404, detail="Чат не найден")
        assert chat is not None
        if chat["author_user_id"] != uid:
            raise HTTPException(status_code=403, detail="Удалить чат может только его автор")
        try:
            return note_chats.delete_chat(chat)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    removed = await run_in_threadpool(_run)
    return {"ok": True, "removed_messages": removed}


@miniapp_router.post("/notes/{kind}/{item_id}/chats/{chat_id}/read")
async def miniapp_note_chats_read(
    kind: str,
    item_id: str,
    chat_id: int,
    body: _MiniappNoteChatRead,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Отметка «прочитано до сообщения last_id» — общая для всех устройств пользователя."""
    from assistant.stores import note_chats

    uid = str(int(principal.telegram_user_id))

    def _run() -> int:
        owner = _note_chat_owner_or_404(uid, kind, item_id)
        if chat_id != note_chats.COMMENTS_CHAT_ID:
            chat = note_chats.chat_for_item(chat_id, owner, kind, str(item_id))
            if not note_chats.can_view(chat, uid):
                raise HTTPException(status_code=404, detail="Чат не найден")
        return note_chats.mark_read(owner, kind, str(item_id), uid, chat_id, body.last_id)

    seen = await run_in_threadpool(_run)
    return {"ok": True, "last_seen_id": seen}


@miniapp_router.post("/notes/{kind}/{item_id}/chats/{chat_id}/split")
async def miniapp_note_chats_split(
    kind: str,
    item_id: str,
    chat_id: int,
    body: _MiniappNoteChatSplit,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Сброс контекста GPT, сохранённый в браузере до чатов → отдельный чат (один раз)."""
    from assistant.stores import note_chats

    uid = str(int(principal.telegram_user_id))

    def _run() -> Optional[dict[str, Any]]:
        owner = _note_chat_owner_or_404(uid, kind, item_id)
        chat = note_chats.chat_for_item(chat_id, owner, kind, str(item_id))
        if not note_chats.can_view(chat, uid):
            raise HTTPException(status_code=404, detail="Чат не найден")
        assert chat is not None
        return note_chats.split_legacy(chat, int(body.after_id), requester=uid)

    chat = await run_in_threadpool(_run)
    return {"ok": True, "chat": _note_chat_api(chat, uid) if chat else None}


@miniapp_router.post("/notes/{kind}/{item_id}/discuss-files")
async def miniapp_discuss_file_upload(
    kind: str,
    item_id: str,
    file: UploadFile = File(...),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import comment_files

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    data = await file.read()
    filename = str(file.filename or "file").strip() or "file"
    mime = str(file.content_type or "").strip()
    try:
        row = await run_in_threadpool(
            comment_files.save_bytes,
            owner_user_id=owner,
            item_kind=kind,
            item_id=item_id,
            author_user_id=uid,
            filename=filename,
            mime=mime,
            data=data,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "file": _comment_file_api(row)}


@miniapp_router.post("/notes/local/{note_id}/ai-edit")
async def miniapp_note_ai_edit(
    note_id: int,
    body: _MiniappNoteAiEdit,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Окно ИИ: правки открытого листа по просьбе пользователя. В обсуждение не пишется."""
    from assistant.lib import note_edits as note_edits_lib
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    tg_uname: str | None = None
    if isinstance(principal.user, dict) and principal.user.get("username"):
        tg_uname = str(principal.user.get("username")).strip() or None
    instruction = re.sub(r"\s+", " ", str(body.instruction or "")).strip()[:1500]
    view = str(body.doc_view or "").strip()
    if not instruction or not view:
        raise HTTPException(status_code=400, detail="Напишите, что сделать с текстом")
    selection = str(body.selection or "").strip()[:4000]

    def _run() -> dict[str, Any]:
        from assistant.compat.miniapp_shims import gpt_openrouter_answer_with_context
        from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user

        note = notes_store.get_accessible_note(uid, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        set_openrouter_usage_telegram_user(telegram_user_id=uid, telegram_username=tg_uname)
        scope = (
            "Change ONLY the selected fragment below (and nothing else in the note); keep everything around it as is. "
            "Edit the whole sentence/paragraph that contains it, so punctuation right after the fragment is not duplicated. "
            "If the fragment is already correct, return it unchanged.\n"
            f"<selected>\n{selection}\n</selected>"
            if selection
            else "The request applies to the whole open note."
        )
        system = (
            note_edits_lib.system_addon()
            + "\n\nThis request comes from the editor's AI button: the user wants the note CHANGED. "
            "ALWAYS answer with one leo-note block of edits (then one short Russian sentence). "
            "Prefer replace edits of whole sentences/paragraphs; keep the note's language unless asked to translate."
        )
        facts = _note_table_facts(note)
        prompt = (
            f"Просьба пользователя: {instruction}\n{scope}\n\n"
            + note_edits_lib.prompt_block(view)
            + (f"\n\n{facts}" if facts else "")
        )
        out = gpt_openrouter_answer_with_context(
            prompt, "", note_title=str(body.note_title or note.get("title") or ""), note_text="",
            system=system, max_tokens=16384,
        )
        answer = str((out or {}).get("answer") or "")
        clean, edits = note_edits_lib.parse_blocks(answer)
        print(f"[note_ai_edit] note={note_id} sheet={body.sheet_id} sel={bool(selection)} edits={len(edits)}")
        return {"edits": edits, "message": clean.strip()[:500]}

    try:
        res = await run_in_threadpool(_run)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — сеть/модель
        print(f"[note_ai_edit] failed note={note_id} err={e!r}")
        raise HTTPException(status_code=502, detail="ИИ не ответил, попробуйте ещё раз") from e
    if not res["edits"]:
        raise HTTPException(status_code=422, detail=res["message"] or "ИИ не нашёл, что поменять — уточните просьбу")
    return {"ok": True, "sheet_id": str(body.sheet_id or ""), **res}


@miniapp_router.post("/notes/local/{note_id}/suggest-title")
async def miniapp_note_suggest_title(
    note_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Название заметки по содержанию (для «Новый чат» и пустых названий). Сам ничего не переименовывает."""
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)

    def _run() -> str:
        from assistant.compat.miniapp_shims import gpt_openrouter_answer_with_context
        from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user

        note = notes_store.get_accessible_note(uid, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(note.get("body") or ""))).strip()
        if len(text) < 40:
            return ""
        set_openrouter_usage_telegram_user(telegram_user_id=uid, telegram_username=None)
        out = gpt_openrouter_answer_with_context(
            "Придумай короткое название этой заметки: 2–5 слов, по-русски, по сути содержания, "
            "без кавычек, без точки в конце, без слов «заметка» и «встреча», если они не несут смысла. "
            "Ответь только названием.",
            "",
            note_title="",
            note_text=text[:6000],
            system="Ты называешь заметки коротко и по делу.",
            max_tokens=40,
        )
        title = str((out or {}).get("answer") or "").strip().splitlines()[0] if out else ""
        return re.sub(r'^[«"\'\s]+|[»"\'\s.]+$', "", title)[:60]

    try:
        title = await run_in_threadpool(_run)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[note_title] failed note={note_id} err={e!r}")
        title = ""
    return {"ok": True, "title": title}


@miniapp_router.post("/notes/local/{note_id}/canvas-image")
async def miniapp_canvas_image_generate(
    note_id: int,
    body: _MiniappCanvasImageBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Generate one image for a web-canvas media slot (edit mode)."""
    from assistant.integrations.openrouter_client import openrouter_generate_images
    from assistant.stores import notes as notes_store

    uid = int(principal.telegram_user_id)
    prompt = str(body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Нужен prompt")
    if len(prompt) > 2000:
        prompt = prompt[:2000]

    def _check() -> bool:
        return bool(notes_store.get_accessible_note(uid, note_id))

    ok = await run_in_threadpool(_check)
    if not ok:
        raise HTTPException(status_code=404, detail="Заметка не найдена")

    def _gen() -> list[dict[str, str]]:
        from assistant.lib import image_style
        from assistant.stores import note_sheets as note_sheets_store

        theme = None
        sid = str(body.sheet_id or "").strip()
        if sid.isdigit():
            sheet = note_sheets_store.get_extra_sheet(int(note_id), int(sid))
            theme = image_style.deck_theme_of_html(str((sheet or {}).get("body") or ""))
        return openrouter_generate_images(image_style.styled_prompt(prompt, theme), n=1, timeout=180)

    try:
        images = await run_in_threadpool(_gen)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e) or "Не удалось сгенерировать") from e
    if not images:
        raise HTTPException(status_code=502, detail="Модель не вернула изображение")
    img = images[0] if isinstance(images[0], dict) else {}
    b64 = str(img.get("b64") or "").strip()
    mime = str(img.get("mime") or "image/png").split(";")[0].strip() or "image/png"
    if not b64:
        raise HTTPException(status_code=502, detail="Модель не вернула изображение")
    # Cap payload size for iframe data-URL embed
    from assistant.lib import web_canvas as web_canvas_lib

    b64, mime = web_canvas_lib.shrink_image_b64(b64, mime)
    if len(b64) > web_canvas_lib.MAX_IMAGE_B64:
        raise HTTPException(status_code=413, detail="Картинка слишком большая")
    return {"ok": True, "data_url": f"data:{mime};base64,{b64}", "mime": mime}


def _notes_store_mod():
    from assistant.stores import notes as notes_store

    return notes_store


def _note_table_facts(note: Optional[dict[str, Any]]) -> str:
    """Итоги таблиц заметки и её текстовых листов, посчитанные кодом (для промпта модели)."""
    from assistant.lib import table_facts
    from assistant.lib import web_canvas as web_canvas_lib
    from assistant.stores import note_sheets as note_sheets_store

    if not note:
        return ""
    html = str(note.get("body") or "")
    try:
        for sh in note_sheets_store.list_extra_sheets(int(note["id"])):
            if web_canvas_lib.normalize_kind(sh.get("kind")) != web_canvas_lib.SHEET_KIND_WEB:
                html += str(sh.get("body") or "")
    except Exception as e:  # noqa: BLE001 — без итогов ответ всё равно будет
        print(f"[table_facts] sheets failed err={e!r}")
    return table_facts.facts_from_html(html)


def _load_web_sheet_for_edit(uid: int, note_id: int, sheet_id: str) -> tuple[dict[str, Any], int, dict[str, Any]]:
    """Note + extra web sheet the user may edit, or HTTPException."""
    from assistant.lib import web_canvas as web_canvas_lib
    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import notes as notes_store

    try:
        extra_id = int(sheet_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="Некорректный лист") from e
    note = notes_store.get_accessible_note(uid, note_id, with_sharing=False)
    if note is None:
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    sheet = note_sheets_store.get_extra_sheet(note_id, extra_id)
    if not sheet or web_canvas_lib.normalize_kind(sheet.get("kind")) != web_canvas_lib.SHEET_KIND_WEB:
        raise HTTPException(status_code=404, detail="Полотно не найдено")
    return note, extra_id, sheet


@miniapp_router.post("/notes/local/{note_id}/sheets/{sheet_id}/canvas-op")
async def miniapp_canvas_op(
    note_id: int,
    sheet_id: str,
    body: _MiniappCanvasOpBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Layout edits on a template deck: switch variant, add/remove items, move/delete slides."""
    from assistant.lib import canvas_templates
    from assistant.stores import note_sheets as note_sheets_store

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any] | None:
        _note, extra_id, sheet = _load_web_sheet_for_edit(uid, note_id, sheet_id)
        stored = str(sheet.get("body") or "")
        base = body.html if body.html and canvas_templates.is_template_html(body.html) else stored
        deck = canvas_templates.extract_deck(base)
        if deck is None:
            raise HTTPException(status_code=400, detail="Это полотно собрано без шаблонов")
        deck = canvas_templates.sync_text_edits(deck, base)
        try:
            canvas_templates.apply_op(deck, body.model_dump())
        except (canvas_templates.DeckOpError, ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e) or "Не удалось изменить слайд") from e
        media = {**canvas_templates.extract_media(stored), **canvas_templates.extract_media(base)}
        html = canvas_templates.render_deck_html(deck, media)
        return note_sheets_store.update_sheet(note_id, extra_id, body=html, kind="web")

    try:
        item = await run_in_threadpool(_run)
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="Полотно не найдено")
    return {"ok": True, "item": item}


@miniapp_router.get("/canvas-icons")
async def miniapp_canvas_icons(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Иконки пунктов списков для выбора в редакторе презентации: имя, когда ставить, SVG (без обёртки)."""
    from assistant.lib import canvas_icons

    return {"icons": [{"name": k, "desc": v, "svg": canvas_icons.inner(k)} for k, v in canvas_icons.catalog().items()]}


@miniapp_router.get("/canvas-layouts")
async def miniapp_canvas_layouts_list(
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """«Мои макеты» пользователя — раскладки «Свой макет», общие для всех презентаций."""
    from assistant.stores import canvas_layouts

    items = await run_in_threadpool(canvas_layouts.list_layouts, int(principal.telegram_user_id))
    return {"layouts": items}


@miniapp_router.post("/notes/local/{note_id}/sheets/{sheet_id}/canvas-restyle-images")
async def miniapp_canvas_restyle_images(
    note_id: int,
    sheet_id: str,
    body: _MiniappCanvasRestyleImages,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Перерисовать картинки презентации в её текущем стиле (по их описаниям); свои картинки пользователя не трогаем."""
    from concurrent.futures import ThreadPoolExecutor

    from assistant.integrations.openrouter_client import (
        openrouter_generate_images,
        set_openrouter_usage_telegram_user,
    )
    from assistant.lib import canvas_templates, image_style
    from assistant.lib import web_canvas as web_canvas_lib
    from assistant.stores import note_sheets as note_sheets_store

    uid = int(principal.telegram_user_id)
    tg_uname: str | None = None
    if isinstance(principal.user, dict) and principal.user.get("username"):
        tg_uname = str(principal.user.get("username")).strip() or None

    def _run() -> dict[str, Any]:
        _note, extra_id, sheet = _load_web_sheet_for_edit(uid, note_id, sheet_id)
        stored = str(sheet.get("body") or "")
        html = body.html if body.html and canvas_templates.is_template_html(body.html) else stored
        if not canvas_templates.is_template_html(html):
            raise HTTPException(status_code=400, detail="Перерисовать картинки можно только в презентации из шаблонов")
        targets = canvas_templates.restyle_targets(html)
        if targets:
            # Картинки, загруженные до появления отметки «своя», узнаём по совпадению с файлами заметки.
            from assistant.stores import comment_files

            owner = _resolve_item_owner(str(uid), "local", str(note_id)) or str(uid)
            uploaded = comment_files.image_digests_for_item(
                owner_user_id=owner, item_kind="local", item_id=str(note_id)
            )
            if uploaded:
                media_now = canvas_templates.extract_media(html)
                targets = [
                    (mid, p) for mid, p in targets
                    if canvas_templates.data_url_digest(media_now.get(mid, "")) not in uploaded
                ]
        if body.dry_run or not targets:
            return {"count": len(targets), "item": None, "done": 0}
        theme = image_style.deck_theme_of_html(html)
        set_openrouter_usage_telegram_user(telegram_user_id=uid, telegram_username=tg_uname)

        def _gen(prompt: str) -> str:
            try:
                got = openrouter_generate_images(image_style.styled_prompt(prompt, theme), n=1, timeout=180) or []
            except Exception as e:  # noqa: BLE001 — одна неудачная картинка не должна ронять остальные
                print(f"[web_canvas] restyle img failed err={e!r}")
                return ""
            urls = web_canvas_lib.image_dicts_to_data_urls([g for g in got if isinstance(g, dict)])
            return urls[0] if urls else ""

        with ThreadPoolExecutor(max_workers=4) as pool:
            urls = list(pool.map(_gen, [p for _mid, p in targets]))
        deck = canvas_templates.extract_deck(html)
        if not deck:
            raise HTTPException(status_code=400, detail="Не удалось прочитать презентацию")
        deck = canvas_templates.sync_text_edits(deck, html)
        media = canvas_templates.extract_media(html)
        done = 0
        for (mid, prompt), url in zip(targets, urls):
            if url:
                media[mid] = url
                media[mid + canvas_templates.PROMPT_SUFFIX] = prompt
                done += 1
        item = None
        if done:
            item = note_sheets_store.update_sheet(
                note_id, extra_id, body=canvas_templates.render_deck_html(deck, media), kind="web"
            )
        print(f"[web_canvas] restyle note={note_id} sheet={sheet_id} theme={theme} done={done}/{len(targets)}")
        return {"count": len(targets), "item": item, "done": done}

    try:
        res = await run_in_threadpool(_run)
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **res}


@miniapp_router.post("/notes/local/{note_id}/sheets/{sheet_id}/canvas-layouts")
async def miniapp_canvas_layout_save(
    note_id: int,
    sheet_id: str,
    body: _MiniappCanvasLayoutSave,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Сохранить раскладку слайда «Свой макет» в «Мои макеты»."""
    from assistant.lib import canvas_templates
    from assistant.stores import canvas_layouts

    uid = int(principal.telegram_user_id)

    def _run() -> dict[str, Any]:
        _note, _extra_id, sheet = _load_web_sheet_for_edit(uid, note_id, sheet_id)
        html = body.html if body.html and canvas_templates.is_template_html(body.html) else str(sheet.get("body") or "")
        deck = canvas_templates.extract_deck(html)
        slide = next((x for x in (deck or {}).get("slides") or [] if str(x.get("id")) == body.slide_id), None)
        if not slide or slide.get("variant") != "custom.grid":
            raise ValueError("Сохранить можно только слайд «Свой макет»")
        name = body.name or ""  # пусто → имя по раскладке («Цифра + 3 карточки / Текст»)
        return canvas_layouts.create_layout(uid, name, slide.get("slots") or {})

    try:
        item = await run_in_threadpool(_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "layout": item}


@miniapp_router.delete("/canvas-layouts/{layout_id}")
async def miniapp_canvas_layout_delete(
    layout_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import canvas_layouts

    ok = await run_in_threadpool(canvas_layouts.delete_layout, int(principal.telegram_user_id), layout_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Макет не найден")
    return {"ok": True}


@miniapp_router.post("/notes/local/{note_id}/sheets/{sheet_id}/canvas-revise")
async def miniapp_canvas_revise(
    note_id: int,
    sheet_id: str,
    body: _MiniappCanvasReviseBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    """Apply per-slide edit comments (edit mode) with one model call."""
    from assistant.compat.miniapp_shims import gpt_openrouter_answer_with_context
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.lib import canvas_revise, canvas_templates
    from assistant.stores import note_sheets as note_sheets_store

    uid = int(principal.telegram_user_id)
    comments = canvas_revise.clean_comments(body.comments)
    insert_after = str(body.insert_after or "").strip()[:64]
    if not comments and not insert_after:
        raise HTTPException(status_code=400, detail="Нет комментариев к слайдам")
    tg_uname: str | None = None
    if isinstance(principal.user, dict) and principal.user.get("username"):
        tg_uname = str(principal.user.get("username")).strip() or None

    def _run() -> dict[str, Any]:
        note, extra_id, sheet = _load_web_sheet_for_edit(uid, note_id, sheet_id)
        stored = str(sheet.get("body") or "")
        base = stored
        if body.html and canvas_templates.is_template_html(body.html) == canvas_templates.is_template_html(stored):
            base = body.html
        note_text = re.sub(r"<[^>]+>", " ", str(note.get("body") or ""))
        note_text = re.sub(r"\s+", " ", note_text).strip()[:40000]
        facts = _note_table_facts(note)
        if facts:
            note_text = facts + "\n\n" + note_text
        set_openrouter_usage_telegram_user(telegram_user_id=uid, telegram_username=tg_uname)

        def _ask(system: str, user_msg: str) -> str | None:
            out = gpt_openrouter_answer_with_context(
                user_msg,
                "",
                note_title=str(note.get("title") or ""),
                note_text=note_text,
                system=system,
                max_tokens=12000,
            )
            return str((out or {}).get("answer") or "")

        def _fill_images(
            item: Optional[dict[str, Any]], slide_ids: list[str], skip: frozenset = frozenset()
        ) -> Optional[dict[str, Any]]:
            # Фото-блоки новых/переделанных слайдов (галерея, «Свой макет») — сгенерировать по image_prompt.
            # Пустые слоты на остальных слайдах не трогаем: пользователь мог оставить их пустыми.
            html = str((item or {}).get("body") or "")
            todo = [t for t in canvas_templates.pending_media(html, slide_ids) if t[0] not in skip][:4] if item else []
            if not todo:
                return item
            from concurrent.futures import ThreadPoolExecutor

            from assistant.integrations.openrouter_client import openrouter_generate_images
            from assistant.lib import web_canvas as web_canvas_lib

            def _gen(prompt: str) -> str:
                try:
                    got = openrouter_generate_images(prompt, n=1, timeout=180) or []
                except Exception as e:  # noqa: BLE001 — без картинки слайд всё равно сохранён
                    print(f"[web_canvas] insert img failed err={e!r}")
                    return ""
                urls = web_canvas_lib.image_dicts_to_data_urls([g for g in got if isinstance(g, dict)])
                return urls[0] if urls else ""

            from assistant.lib import image_style

            theme = image_style.deck_theme_of_html(html)
            with ThreadPoolExecutor(max_workers=4) as pool:
                urls = list(pool.map(_gen, [image_style.styled_prompt(p, theme) for _mid, p in todo]))
            media = canvas_templates.extract_media(html)
            media.update({mid: u for (mid, _p), u in zip(todo, urls) if u})
            deck = canvas_templates.extract_deck(html)
            if not deck or len(media) == len(canvas_templates.extract_media(html)):
                return item
            return note_sheets_store.update_sheet(
                note_id, extra_id, body=canvas_templates.render_deck_html(deck, media), kind="web"
            ) or item

        if insert_after:
            if not canvas_templates.is_template_html(base):
                raise HTTPException(status_code=400, detail="Добавлять слайды можно только в презентации из шаблонов")
            layout = str(body.insert_layout or "auto").strip()
            structure = None
            if layout.startswith("saved:"):
                from assistant.stores import canvas_layouts as canvas_layouts_store

                try:
                    saved = canvas_layouts_store.get_layout(uid, int(layout.split(":", 1)[1]))
                except (TypeError, ValueError):
                    saved = None
                if not saved:
                    raise HTTPException(status_code=404, detail="Макет не найден — возможно, его удалили")
                structure = saved["structure"]
            try:
                new_html, new_ids = canvas_revise.insert_slides(
                    base, insert_after, body.insert_text or "", ask=_ask, layout=layout, structure=structure
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            results = {insert_after: "inserted" if new_html else "failed"}
            print(f"[web_canvas] insert note={note_id} sheet={sheet_id} after={insert_after} new={new_ids}")
            item = note_sheets_store.update_sheet(note_id, extra_id, body=new_html, kind="web") if new_html else None
            return {"item": _fill_images(item, new_ids), "results": results, "new_ids": new_ids}
        new_html, results = canvas_revise.revise(base, comments, ask=_ask)
        print(f"[web_canvas] revise note={note_id} sheet={sheet_id} results={results}")
        item = None
        if new_html:
            item = _fill_images(
                note_sheets_store.update_sheet(note_id, extra_id, body=new_html, kind="web"),
                [sid for sid, st in (results or {}).items() if st == "applied"],
                skip=frozenset(mid for mid, _p in canvas_templates.pending_media(base, list(results or {}))),
            )
        return {"item": item, "results": results}

    try:
        res = await run_in_threadpool(_run)
    except note_sheets_store.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — model/network errors
        print(f"[web_canvas] revise failed note={note_id} sheet={sheet_id} err={e!r}")
        raise HTTPException(status_code=502, detail="Модель не ответила, попробуйте ещё раз") from e
    return {
        "ok": bool(res.get("item")),
        "item": res.get("item"),
        "results": res.get("results") or {},
        "new_ids": res.get("new_ids") or [],
    }


@miniapp_router.get("/notes/{kind}/{item_id}/discuss-files/{file_id}")
async def miniapp_discuss_file_get(
    kind: str,
    item_id: str,
    file_id: int,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> Response:
    from assistant.stores import comment_files

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    row = await run_in_threadpool(comment_files.get_file, file_id)
    if (
        not row
        or str(row.get("owner_user_id") or "") != owner
        or str(row.get("item_kind") or "") != kind
        or str(row.get("item_id") or "") != str(item_id)
        or not _comment_file_visible(row, uid)
    ):
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = comment_files.disk_path(file_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    filename = str(row.get("filename") or "file")
    mime = str(row.get("mime") or "application/octet-stream")
    return FileResponse(path, media_type=mime, filename=filename)


@miniapp_router.post("/notes/{kind}/{item_id}/paei")
async def miniapp_note_paei_start(
    kind: str,
    item_id: str,
    body: Optional[_MiniappNotePaeiStart] = Body(default=None),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    import asyncio

    from assistant.board.note_paei import begin_job, get_job, run_note_paei_job
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    payload = body or _MiniappNotePaeiStart()
    reply_text = (payload.reply or "").strip()
    parent_id = payload.parent_id
    quote_text = (payload.quote or "").strip()
    kb_ids = payload.knowledge_note_ids
    if kb_ids is not None:
        kb_ids = [str(x).strip() for x in kb_ids if str(x).strip()]
        use_knowledge = bool(kb_ids)
    else:
        use_knowledge = payload.use_knowledge is not False
    sheet_ids = payload.sheet_ids
    if sheet_ids is not None:
        sheet_ids = [str(x).strip() for x in sheet_ids if str(x).strip()]
    ctx_ids = payload.context_note_ids
    if ctx_ids is not None:
        ctx_ids = [str(x).strip() for x in ctx_ids if str(x).strip()]
    from assistant.nlu.ask_context import pack_attached_notes

    attached_notes = pack_attached_notes(int(principal.telegram_user_id), ctx_ids)
    from assistant.stores import note_chats
    from assistant.stores import user_agents as user_agents_store

    requested_model = ""
    raw_model = str(payload.model or "").strip()
    if raw_model:
        try:
            requested_model = user_agents_store.sanitize_model(raw_model, kind="paie")
        except user_agents_store.AgentError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    chat_id = await run_in_threadpool(
        _resolve_post_chat, owner, kind, str(item_id), uid, payload.chat_id, parent_id
    )
    if reply_text:
        comments = await run_in_threadpool(
            note_chats.chat_context_comments, owner, kind, str(item_id), uid, chat_id
        )
        root_id = parent_id or share_comments_store.paie_thread_root_id(comments)
        if not root_id:
            raise HTTPException(
                status_code=400, detail="Сначала дождитесь комментария CHAIR"
            )
        running = get_job(owner, kind, item_id)
        if running and str(running.get("status") or "") == "running":
            raise HTTPException(status_code=409, detail="Дождитесь ответа CHAIR")
        author_id, author_name, author_username = _comment_author_from_principal(
            principal
        )
        reply_to_id = None
        raw_reply_to = payload.reply_to_id
        if raw_reply_to not in (None, "", 0, "0"):
            try:
                tid = int(raw_reply_to)
            except (TypeError, ValueError):
                tid = 0
            if tid > 0 and any(int(c.get("id") or 0) == tid for c in comments):
                reply_to_id = tid
        user_parent = reply_to_id or root_id
        try:
            await run_in_threadpool(
                share_comments_store.add_comment,
                owner,
                kind,
                item_id,
                author_user_id=author_id,
                author_name=author_name,
                author_username=author_username,
                body=reply_text,
                quote=quote_text,
                parent_id=user_parent,
                chat_id=chat_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            await run_in_threadpool(
                partial(
                    _notify_discussion_comment,
                    owner=owner,
                    kind=kind,
                    item_id=str(item_id),
                    author_user_id=author_id,
                    author_name=author_name,
                    author_username=author_username,
                    body=reply_text,
                    is_assistant=False,
                )
            )
        except Exception as e:
            print(f"[discuss_notify] paie_user_fail err={e!r}")
        job, started = begin_job(
            owner,
            kind,
            item_id,
            chat_id=chat_id,
            viewer_uid=uid,
            reply=reply_text,
            parent_id=int(root_id),
            use_knowledge=use_knowledge,
            knowledge_note_ids=kb_ids,
            quote=quote_text,
            reply_to_id=reply_to_id,
            sheet_ids=sheet_ids,
            attached_notes=attached_notes,
            model=requested_model,
        )
        followup_parent = int(root_id)
    else:
        job, started = begin_job(
            owner,
            kind,
            item_id,
            chat_id=chat_id,
            viewer_uid=uid,
            use_knowledge=use_knowledge,
            knowledge_note_ids=kb_ids,
            sheet_ids=sheet_ids,
            attached_notes=attached_notes,
            model=requested_model,
        )
        followup_parent = None
    if started:
        asyncio.create_task(
            run_note_paei_job(
                int(owner),
                kind,
                item_id,
                reply=reply_text,
                parent_id=followup_parent,
                use_knowledge=use_knowledge,
                knowledge_note_ids=kb_ids,
                sheet_ids=sheet_ids,
                attached_notes=attached_notes,
                model=requested_model,
            )
        )
    return {
        "ok": True,
        "status": str(job.get("status") or "running"),
        "comment_id": job.get("comment_id"),
        "error": job.get("error"),
        "progress": job.get("progress") if isinstance(job.get("progress"), dict) else None,
    }


@miniapp_router.get("/notes/{kind}/{item_id}/paei")
async def miniapp_note_paei_status(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.board.note_paei import get_job

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    job = get_job(owner, kind, item_id)
    if not job:
        return {"ok": True, "status": "idle"}
    return {
        "ok": True,
        "status": str(job.get("status") or "idle"),
        "comment_id": job.get("comment_id"),
        "error": job.get("error"),
        "meeting_id": job.get("meeting_id"),
        "progress": job.get("progress") if isinstance(job.get("progress"), dict) else None,
    }


@miniapp_router.post("/notes/{kind}/{item_id}/research")
async def miniapp_note_research_start(
    kind: str,
    item_id: str,
    body: Optional[_MiniappNoteResearchStart] = Body(default=None),
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    import asyncio

    from assistant.board.note_research import begin_job, get_job, run_note_research_job
    from assistant.stores import share_comments as share_comments_store

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    payload = body or _MiniappNoteResearchStart()
    reply_text = (payload.reply or "").strip()
    parent_id = payload.parent_id
    quote_text = (payload.quote or "").strip()
    kb_ids = payload.knowledge_note_ids
    if kb_ids is not None:
        kb_ids = [str(x).strip() for x in kb_ids if str(x).strip()]
        use_knowledge = bool(kb_ids)
    else:
        use_knowledge = payload.use_knowledge is not False
    sheet_ids = payload.sheet_ids
    if sheet_ids is not None:
        sheet_ids = [str(x).strip() for x in sheet_ids if str(x).strip()]
    ctx_ids = payload.context_note_ids
    if ctx_ids is not None:
        ctx_ids = [str(x).strip() for x in ctx_ids if str(x).strip()]
    from assistant.nlu.ask_context import pack_attached_notes

    attached_notes = pack_attached_notes(int(principal.telegram_user_id), ctx_ids)
    from assistant.stores import user_agents as user_agents_store

    requested_model = ""
    raw_model = str(payload.model or "").strip()
    if raw_model:
        try:
            requested_model = user_agents_store.sanitize_model(raw_model, kind="research")
        except user_agents_store.AgentError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    running = get_job(owner, kind, item_id)
    if running and str(running.get("status") or "") == "running":
        raise HTTPException(status_code=409, detail="Дождитесь ответа Research")
    thread_parent = parent_id
    from assistant.stores import note_chats

    chat_id = await run_in_threadpool(
        _resolve_post_chat, owner, kind, str(item_id), uid, payload.chat_id, parent_id
    )
    if reply_text:
        comments = await run_in_threadpool(
            note_chats.chat_context_comments, owner, kind, str(item_id), uid, chat_id
        )
        reply_to_id = None
        raw_reply_to = payload.reply_to_id
        if raw_reply_to not in (None, "", 0, "0"):
            try:
                tid = int(raw_reply_to)
            except (TypeError, ValueError):
                tid = 0
            if tid > 0 and any(int(c.get("id") or 0) == tid for c in comments):
                reply_to_id = tid
        user_parent = reply_to_id or parent_id
        author_id, author_name, author_username = _comment_author_from_principal(
            principal
        )
        try:
            saved = await run_in_threadpool(
                share_comments_store.add_comment,
                owner,
                kind,
                item_id,
                author_user_id=author_id,
                author_name=author_name,
                author_username=author_username,
                body=reply_text,
                quote=quote_text,
                prefix=share_comments_store.RESEARCH_PREFIX,
                parent_id=user_parent,
                chat_id=chat_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            await run_in_threadpool(
                partial(
                    _notify_discussion_comment,
                    owner=owner,
                    kind=kind,
                    item_id=str(item_id),
                    author_user_id=author_id,
                    author_name=author_name,
                    author_username=author_username,
                    body=reply_text,
                    is_assistant=False,
                )
            )
        except Exception as e:
            print(f"[discuss_notify] research_user_fail err={e!r}")
        if thread_parent in (None, "", 0, "0"):
            thread_parent = saved.get("id")
    job, started = begin_job(
        owner,
        kind,
        item_id,
        chat_id=chat_id,
        viewer_uid=uid,
        reply=reply_text,
        parent_id=thread_parent,
        use_knowledge=use_knowledge,
        knowledge_note_ids=kb_ids,
        quote=quote_text,
        sheet_ids=sheet_ids,
        attached_notes=attached_notes,
        model=requested_model,
    )
    if started:
        asyncio.create_task(
            run_note_research_job(
                int(owner),
                kind,
                item_id,
                reply=reply_text,
                parent_id=thread_parent,
                use_knowledge=use_knowledge,
                knowledge_note_ids=kb_ids,
                quote=quote_text,
                sheet_ids=sheet_ids,
                attached_notes=attached_notes,
                model=requested_model,
            )
        )
    return {
        "ok": True,
        "status": str(job.get("status") or "running"),
        "comment_id": job.get("comment_id"),
        "error": job.get("error"),
        "progress": job.get("progress") if isinstance(job.get("progress"), dict) else None,
    }


@miniapp_router.get("/notes/{kind}/{item_id}/research")
async def miniapp_note_research_status(
    kind: str,
    item_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.board.note_research import get_job

    uid = str(int(principal.telegram_user_id))
    owner = await run_in_threadpool(_resolve_item_owner, uid, kind, item_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    job = get_job(owner, kind, item_id)
    if not job:
        return {"ok": True, "status": "idle"}
    return {
        "ok": True,
        "status": str(job.get("status") or "idle"),
        "comment_id": job.get("comment_id"),
        "error": job.get("error"),
        "progress": job.get("progress") if isinstance(job.get("progress"), dict) else None,
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
        from assistant.compat.miniapp_shims import add_todoist_note

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


# ---------------------------------------------------------------- почта (подключение ящиков)


def _mail_error(e: Exception) -> HTTPException:
    from assistant.services.mail_connect import ConnectError

    if isinstance(e, ConnectError):
        return HTTPException(status_code=400, detail={"message": str(e), "step": e.step, "code": e.code})
    print(f"[mail] connect err={e!r}")
    return HTTPException(status_code=502, detail={"message": "Не получилось связаться с почтой. Попробуйте ещё раз."})


@miniapp_router.get("/mail/accounts")
async def miniapp_mail_accounts(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_connect

    uid = int(principal.telegram_user_id)
    return {
        "ok": True,
        "accounts": await run_in_threadpool(mail_connect.accounts, uid),
        "yandex": await run_in_threadpool(mail_connect.yandex_oauth_state, uid),
    }


@miniapp_router.post("/mail/detect")
async def miniapp_mail_detect(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Мастер подключения по адресу: провайдер, сервер, подсказки."""
    from assistant.services import mail_connect

    del principal
    try:
        return {"ok": True, **await run_in_threadpool(mail_connect.detect, str(body.get("email") or ""))}
    except Exception as e:
        raise _mail_error(e) from e


@miniapp_router.post("/mail/connect")
async def miniapp_mail_connect(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_connect

    try:
        return await run_in_threadpool(mail_connect.connect, int(principal.telegram_user_id), body or {})
    except Exception as e:
        raise _mail_error(e) from e


@miniapp_router.post("/mail/yandex/start")
async def miniapp_mail_yandex_start(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Вход через Яндекс для почты — тот же OAuth, что у Телемоста, с правами на почту."""
    from assistant.integrations.telemost_oauth import (
        build_authorization_url,
        register_oauth_state,
        uses_verification_code_flow,
    )

    try:
        state = register_oauth_state(int(principal.telegram_user_id))
        return {"url": build_authorization_url(state, include_mail_scope=True),
                "verification_code_flow": uses_verification_code_flow()}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@miniapp_router.patch("/mail/accounts/{account_id}")
async def miniapp_mail_account_patch(
    account_id: str, body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    from assistant.stores import mail_accounts

    uid = int(principal.telegram_user_id)
    if not mail_accounts.get(uid, account_id):
        raise HTTPException(status_code=404, detail="Ящик не найден")
    if "send_enabled" in (body or {}):
        await run_in_threadpool(mail_accounts.set_send_enabled, uid, account_id, bool(body["send_enabled"]))
    return {"ok": True, "account": mail_accounts.public(mail_accounts.get(uid, account_id) or {})}


@miniapp_router.post("/mail/accounts/{account_id}/sync")
async def miniapp_mail_account_sync(account_id: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_source
    from assistant.stores import mail_accounts

    uid = int(principal.telegram_user_id)
    if not mail_accounts.get(uid, account_id):
        raise HTTPException(status_code=404, detail="Ящик не найден")
    mail_source.kick(uid)
    return {"ok": True}


@miniapp_router.delete("/mail/accounts/{account_id}")
async def miniapp_mail_account_delete(account_id: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Отключить ящик: стереть пароль/доступ, состояние синхронизации и индекс писем."""
    from assistant.services import mail_connect

    return {"ok": await run_in_threadpool(mail_connect.disconnect, int(principal.telegram_user_id), account_id)}


# ---------------------------------------------------------------- почта: разбор и ответ из чата


def _mail_reply_error(e: Exception) -> HTTPException:
    from assistant.services.mail_reply import ReplyError

    if isinstance(e, LookupError):
        return HTTPException(status_code=404, detail="Черновик не найден")
    if isinstance(e, (ReplyError, ValueError)):
        return HTTPException(status_code=400, detail=str(e))
    print(f"[mail] reply err={e!r}")
    return HTTPException(status_code=502, detail="Не получилось связаться с почтой. Попробуйте ещё раз.")


@miniapp_router.post("/mail/thread-card")
async def miniapp_mail_thread_card(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«Разобрать письмо»: карточка письма для чата Лео (кнопки «Ответить», «Подготовить ответ», «Саммари»)."""
    from assistant.services import gpt_calendar_tools as gcal_tools
    from assistant.services import mail_reply

    uid = int(principal.telegram_user_id)
    try:
        aid = await run_in_threadpool(mail_reply.thread_card, uid, str(body.get("mail_key") or ""))
    except Exception as e:
        raise _mail_reply_error(e) from e
    return {"action": gcal_tools.public_action(gcal_tools.get_action(aid) or {}, viewer_uid=uid)}


@miniapp_router.post("/mail/drafts")
async def miniapp_mail_draft_create(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«Ответить»: пустой черновик (адресаты — из исходного письма), его открывает окно письма."""
    from assistant.services import gpt_calendar_tools as gcal_tools
    from assistant.services import mail_reply

    uid = int(principal.telegram_user_id)
    try:
        aid = await run_in_threadpool(
            partial(mail_reply.create_draft, uid, str(body.get("mail_key") or ""), body=str(body.get("body") or ""),
                    reply_all=bool(body.get("reply_all")))
        )
    except Exception as e:
        raise _mail_reply_error(e) from e
    return {"action": gcal_tools.public_action(gcal_tools.get_action(aid) or {}, viewer_uid=uid)}


@miniapp_router.post("/mail/drafts/{action_id}")
async def miniapp_mail_draft_update(
    action_id: str, body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """Правки из окна письма (сохраняются на ходу): кому, копия, тема, текст, вложения, «Ответить всем»."""
    from assistant.services import gpt_calendar_tools as gcal_tools
    from assistant.services import mail_reply

    uid = int(principal.telegram_user_id)
    try:
        a = await run_in_threadpool(mail_reply.update_draft, uid, action_id, body or {})
    except Exception as e:
        raise _mail_reply_error(e) from e
    return {"action": gcal_tools.public_action(a, viewer_uid=uid)}


@miniapp_router.post("/mail/rewrite")
async def miniapp_mail_rewrite(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """ИИ-правка текста в окне письма: short / polite / translate / custom. Ничего не отправляет."""
    from assistant.services import mail_composer

    try:
        text = await run_in_threadpool(
            partial(mail_composer.rewrite, int(principal.telegram_user_id), str(body.get("body") or ""),
                    str(body.get("mode") or ""), instruction=str(body.get("instruction") or ""),
                    lang=str(body.get("lang") or "английский"))
        )
    except Exception as e:
        raise _mail_reply_error(e) from e
    return {"body": text}


@miniapp_router.post("/mail/composer/slots")
async def miniapp_mail_composer_slots(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«+слоты [люди] [день]» в окне письма: свободные (общие) окна."""
    from assistant.services import mail_composer

    try:
        return await run_in_threadpool(mail_composer.slots, int(principal.telegram_user_id), str(body.get("query") or ""))
    except Exception as e:
        print(f"[mail] slots err={e!r}")
        return {"ok": False, "error": "Не получилось посмотреть календарь. Попробуйте ещё раз."}


@miniapp_router.post("/mail/attachments")
async def miniapp_mail_attachment_upload(
    file: UploadFile = File(...), principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    from assistant.stores import mail_attachments

    data = await file.read(mail_attachments.MAX_TOTAL + 1)
    try:
        meta = await run_in_threadpool(
            mail_attachments.save, int(principal.telegram_user_id), file.filename or "файл", file.content_type or "", data
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "attachment": {k: meta[k] for k in ("id", "name", "size")}}


@miniapp_router.get("/mail/threads")
async def miniapp_mail_threads(
    filter: str = "all", account: str = "", principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """Раздел «Почта»: ветки переписки с людьми (как список чатов) с метками Лео."""
    from assistant.services import mail_inbox

    return {"ok": True, **await run_in_threadpool(partial(mail_inbox.threads, int(principal.telegram_user_id),
                                                          filt=filter, account_id=account))}


@miniapp_router.get("/mail/threads/{thread_key}")
async def miniapp_mail_thread(thread_key: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_inbox

    try:
        return {"ok": True, **await run_in_threadpool(mail_inbox.thread, int(principal.telegram_user_id), thread_key)}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        print(f"[mail] thread err={e!r}")
        raise HTTPException(status_code=502, detail="Не получилось загрузить переписку из почты. Попробуйте ещё раз.") from e


@miniapp_router.post("/mail/threads/{thread_key}/read")
async def miniapp_mail_thread_read(
    thread_key: str, body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """Прочитано / не прочитано (в Orbita и, если есть права, в самом ящике)."""
    from assistant.services import mail_inbox
    from assistant.stores import mail_index

    uid = int(principal.telegram_user_id)
    read = bool((body or {}).get("read", True))
    keys = [r["mail_key"] for r in mail_index.thread(uid, thread_key) if r["direction"] == "in"]
    await run_in_threadpool(mail_inbox.set_read, uid, keys, read)
    from assistant.services import mail_sync

    mail_sync.mark_seen_soon(uid, keys, read)
    return {"ok": True, "count": len(keys)}


@miniapp_router.get("/mail/attachment")
async def miniapp_mail_attachment(mail_key: str, part: int, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    import base64

    from assistant.services import mail_inbox

    try:
        att = await run_in_threadpool(mail_inbox.attachment, int(principal.telegram_user_id), mail_key, int(part))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True, "name": att["name"], "type": att["type"], "data": base64.b64encode(att["data"]).decode()}


@miniapp_router.get("/mail/open-url")
async def miniapp_mail_open_url(mail_key: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_inbox

    return {"ok": True, "url": await run_in_threadpool(mail_inbox.open_in_mail_url, int(principal.telegram_user_id), mail_key)}


@miniapp_router.post("/mail/threads/{thread_key}/draft")
async def miniapp_mail_thread_ai_draft(
    thread_key: str, body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """«Подготовить ответ» в ветке: текст в стиле пользователя — в поле ввода (не отправляется)."""
    from assistant.services import mail_composer

    try:
        text = await run_in_threadpool(partial(
            mail_composer.draft_reply, int(principal.telegram_user_id), thread_key,
            reply_to_key=str((body or {}).get("reply_to_key") or ""), instruction=str((body or {}).get("instruction") or "")))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "body": text}


@miniapp_router.post("/mail/threads/{thread_key}/summary")
async def miniapp_mail_thread_summary(thread_key: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_composer

    try:
        text = await run_in_threadpool(mail_composer.summarize_thread, int(principal.telegram_user_id), thread_key)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "summary": text}


@miniapp_router.post("/mail/threads/{thread_key}/note")
async def miniapp_mail_thread_note(thread_key: str, principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_inbox

    try:
        note = await run_in_threadpool(mail_inbox.save_to_note, int(principal.telegram_user_id), thread_key)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True, "note_id": note.get("id")}


@miniapp_router.get("/mail/senders")
async def miniapp_mail_senders(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Правила отправителей («Скрытые», «Всегда пропускать») и «Отсеяно Лео» за 14 дней."""
    from assistant.stores import mail_index, mail_senders

    uid = int(principal.telegram_user_id)

    def _load() -> dict[str, Any]:
        rules = mail_senders.list_rules(uid)
        rmap = {r["pattern"]: r["rule"] for r in rules}
        since = datetime.now(timezone.utc) - timedelta(days=14)
        filtered = [f for f in mail_index.filtered_senders(uid, since=since) if mail_senders.match(rmap, f["addr"]) != "block"]
        return {"ok": True, "rules": [{k: r[k] for k in ("pattern", "rule", "created_at")} | {"moved": len(r["moved"])} for r in rules],
                "filtered": filtered}

    return await run_in_threadpool(_load)


@miniapp_router.post("/mail/senders")
async def miniapp_mail_senders_set(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """«Не показывать» (block) / «Всегда пропускать» (allow) для адреса или домена."""
    from assistant.services import mail_source

    uid = int(principal.telegram_user_id)
    rule = str((body or {}).get("rule") or "")
    fn = {"block": mail_source.block_sender, "allow": mail_source.allow_sender}.get(rule)
    if not fn:
        raise HTTPException(status_code=400, detail="Неизвестное правило")
    try:
        res = await run_in_threadpool(fn, uid, str(body.get("pattern") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, **res}


@miniapp_router.delete("/mail/senders")
async def miniapp_mail_senders_delete(
    pattern: str, restore: int = 0, principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """Снять правило. restore=1 — вернуть письма, перенесённые в спам, во «Входящие»."""
    from assistant.services import mail_sync
    from assistant.stores import mail_senders

    uid = int(principal.telegram_user_id)
    try:
        old = await run_in_threadpool(mail_senders.remove, uid, pattern)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    restored = 0
    if restore and old and old.get("moved"):
        restored = await run_in_threadpool(mail_sync.restore_moved, uid, old["moved"])
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, "removed": bool(old), "restored": restored}


@miniapp_router.post("/mail/threads/{thread_key}/spam")
async def miniapp_mail_thread_spam(
    thread_key: str, body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)
) -> dict[str, Any]:
    """«Спам»: не показывать отправителя + письма ветки — в папку спама ящика (если есть права)."""
    from assistant.services import mail_sync

    uid = int(principal.telegram_user_id)
    try:
        res = await run_in_threadpool(mail_sync.spam_thread, uid, thread_key, str((body or {}).get("pattern") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _FOCUS_CACHE.pop(uid, None)
    return {"ok": True, **res}


@miniapp_router.get("/mail/settings")
async def miniapp_mail_settings(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    from assistant.services import mail_memory
    from assistant.stores import mail_accounts

    uid = int(principal.telegram_user_id)
    cfg = await run_in_threadpool(mail_accounts.settings, uid)
    return {"ok": True, **cfg, "memory": await run_in_threadpool(mail_memory.status, uid)}


@miniapp_router.patch("/mail/settings")
async def miniapp_mail_settings_patch(body: dict[str, Any], principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict[str, Any]:
    """Подпись, «Память переписки» (выключение стирает память), вид раздела «Почта» (chat | letters)."""
    from assistant.services import mail_memory, mail_source
    from assistant.stores import mail_accounts

    uid = int(principal.telegram_user_id)
    before = await run_in_threadpool(mail_accounts.settings, uid)
    cfg = await run_in_threadpool(mail_accounts.update_settings, uid, body or {})
    if before["memory_enabled"] and not cfg["memory_enabled"]:
        await run_in_threadpool(mail_memory.forget_all, uid)
    elif not before["memory_enabled"] and cfg["memory_enabled"]:
        mail_source.kick(uid)
    return {"ok": True, **cfg}


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


# --- Внешние трекеры задач (Битрикс24 и др.): доски, стадии, заведение задачи, описание по WWA ---


def _tracker_or_404(provider: str) -> Any:
    from assistant.trackers.base import TrackerError
    from assistant.trackers.registry import get_provider

    try:
        return get_provider(provider)
    except TrackerError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _tracker_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    from assistant.trackers.base import TrackerError, TrackerNotConnected

    try:
        return fn(*args, **kwargs)
    except TrackerNotConnected as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _trackers_public(uid: int) -> list[dict[str, Any]]:
    from assistant.stores import trackers as trackers_store
    from assistant.trackers.registry import PROVIDERS

    out = []
    for p in PROVIDERS.values():
        try:
            connected = bool(p.is_connected(uid))
            conn = p.connection(uid)
        except Exception as e:
            print(f"[trackers] status failed provider={p.id} uid={uid} err={e!r}")
            connected, conn = False, {}
        out.append({
            "id": p.id,
            "title": p.title,
            "short_title": p.short_title,
            "connected": connected,
            "connection": conn,
            "defaults": trackers_store.get_settings(uid, p.id),
        })
    return out


class _MiniappTrackerTaskBody(BaseModel):
    title: str = ""
    description: str = ""
    board_id: str = ""
    board_name: str = ""
    stage_id: str = ""
    stage_name: str = ""
    responsible: str = ""
    responsible_id: str = ""
    deadline: str = ""
    note_id: str = ""
    calendar_task_id: str = ""
    focus_key: str = ""
    meeting_item: str = ""  # пункт задач встречи в note_id: в трекер — только если пункт ничей


class _MiniappWwaBody(BaseModel):
    title: str = ""
    description: str = ""
    note_id: str = ""
    focus_key: str = ""
    context: str = ""


@miniapp_router.get("/trackers")
async def miniapp_trackers(principal: _MiniappPrincipal = Depends(require_miniapp_user)) -> dict:
    uid = int(principal.telegram_user_id)
    return {"trackers": await run_in_threadpool(_trackers_public, uid)}


@miniapp_router.post("/trackers/{provider}/webhook")
async def miniapp_tracker_webhook_connect(
    provider: str,
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    p = _tracker_or_404(provider)
    if not hasattr(p, "connect_webhook"):
        raise HTTPException(status_code=400, detail="Этот трекер подключается иначе")
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Вставьте адрес вебхука")
    uid = int(principal.telegram_user_id)
    conn = await run_in_threadpool(_tracker_call, p.connect_webhook, uid, url)
    return {"connected": True, "connection": conn}


@miniapp_router.delete("/trackers/{provider}/webhook")
async def miniapp_tracker_webhook_disconnect(
    provider: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    p = _tracker_or_404(provider)
    if not hasattr(p, "disconnect"):
        raise HTTPException(status_code=400, detail="Этот трекер отключается иначе")
    return {"disconnected": await run_in_threadpool(p.disconnect, int(principal.telegram_user_id))}


@miniapp_router.get("/trackers/{provider}/boards")
async def miniapp_tracker_boards(
    provider: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    p = _tracker_or_404(provider)
    boards = await run_in_threadpool(_tracker_call, p.list_boards, int(principal.telegram_user_id))
    return {"boards": [b.public() for b in boards]}


@miniapp_router.get("/trackers/{provider}/boards/{board_id}/stages")
async def miniapp_tracker_stages(
    provider: str,
    board_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    p = _tracker_or_404(provider)
    stages = await run_in_threadpool(_tracker_call, p.list_stages, int(principal.telegram_user_id), board_id)
    return {"stages": [s.public() for s in stages]}


@miniapp_router.get("/trackers/{provider}/users")
async def miniapp_tracker_users(
    provider: str,
    q: str = "",
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    p = _tracker_or_404(provider)
    people = await run_in_threadpool(_tracker_call, p.search_users, int(principal.telegram_user_id), q[:80])
    return {"users": [x.public() for x in people]}


@miniapp_router.put("/trackers/{provider}/settings")
async def miniapp_tracker_settings(
    provider: str,
    body: dict[str, Any],
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.stores import trackers as trackers_store

    p = _tracker_or_404(provider)
    saved = await run_in_threadpool(
        partial(
            trackers_store.save_settings,
            int(principal.telegram_user_id),
            p.id,
            board_id=str(body.get("board_id") or ""),
            board_name=str(body.get("board_name") or ""),
            stage_id=str(body.get("stage_id") or ""),
            stage_name=str(body.get("stage_name") or ""),
        )
    )
    return {"ok": True, "defaults": saved}


@miniapp_router.post("/trackers/{provider}/tasks")
async def miniapp_tracker_create_task(
    provider: str,
    body: _MiniappTrackerTaskBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.stores import trackers as trackers_store
    from assistant.trackers.base import TaskDraft

    p = _tracker_or_404(provider)
    uid = int(principal.telegram_user_id)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Укажите название задачи")
    draft = TaskDraft(
        title=body.title.strip(),
        description=body.description,
        board_id=body.board_id,
        stage_id=body.stage_id,
        responsible=body.responsible,
        responsible_id=body.responsible_id,
        deadline=body.deadline,
    )
    def _create() -> tuple[Any, dict[str, Any]]:
        created = _tracker_call(p.create_task, uid, draft)
        row = trackers_store.add_task(
            uid,
            p.id,
            external_id=created.external_id,
            url=created.url,
            title=created.title,
            board_id=body.board_id,
            board_name=body.board_name,
            stage_id=body.stage_id,
            stage_name=body.stage_name,
            note_id=body.note_id,
            calendar_task_id=body.calendar_task_id,
            focus_key=body.focus_key,
        )
        link = {"url": created.url, "title": created.title, "provider": p.id, "stage": body.stage_name}
        return (created, row), link

    from assistant.services import meeting_tasks

    if body.meeting_item and str(body.note_id or "").isdigit():
        # из таблицы задач встречи: двое одновременно — в трекер уходит одна задача
        run = partial(
            meeting_tasks.claim_tracker, uid, int(body.note_id), body.meeting_item, _create,
            by=_principal_display_name(principal),
        )
    else:
        run = lambda: _create()[0]  # noqa: E731
    try:
        created, row = await run_in_threadpool(run)
    except meeting_tasks.Taken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "ok": True,
        "task": row,
        "external": {"provider": p.id, "id": created.external_id, "url": created.url},
        "chip_label": created.title,
        "responsible_name": created.extra.get("responsible_name") or "",
    }


@miniapp_router.get("/trackers/links")
async def miniapp_tracker_links(
    calendar_task_id: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.stores import trackers as trackers_store

    links = await run_in_threadpool(
        trackers_store.tasks_for_calendar_task, int(principal.telegram_user_id), calendar_task_id
    )
    return {"links": links}


@miniapp_router.post("/trackers/describe-wwa")
async def miniapp_tracker_describe_wwa(
    body: _MiniappWwaBody,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict:
    from assistant.services import task_wwa

    try:
        text = await run_in_threadpool(
            partial(
                task_wwa.describe,
                int(principal.telegram_user_id),
                title=body.title,
                description=body.description,
                note_id=body.note_id or None,
                focus_key=body.focus_key,
                context=body.context,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        print(f"[trackers] wwa failed uid={principal.telegram_user_id} err={e!r}")
        raise HTTPException(status_code=502, detail="Не получилось описать задачу — попробуйте ещё раз") from e
    return {"ok": True, "description": text}


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
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        elif path.endswith((".css", ".js", ".png", ".svg", ".ico", ".jpg", ".jpeg", ".webp")):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response


_SHARE_PAGE_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


@app.get("/api/public/share/{token}")
async def public_share_json(token: str, request: Request) -> dict[str, Any]:
    from assistant.stores import share_links as share_links_store

    link = await run_in_threadpool(share_links_store.resolve_share, token)
    if not link:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    payload = await run_in_threadpool(_public_share_payload, link)
    if not payload:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    viewer = _optional_share_viewer(request)
    payload["viewer"] = _public_share_viewer_flags(link, viewer)
    if not payload["viewer"].get("is_owner"):
        await run_in_threadpool(share_links_store.count_view, str(link.get("token") or ""))
    return payload


def _public_share_viewer_flags(
    link: dict[str, Any], principal: _MiniappPrincipal | None
) -> dict[str, Any]:
    kind = str(link.get("item_kind") or "")
    owner = str(link.get("user_id") or "")
    item_id = str(link.get("item_id") or "")
    if principal is None:
        return {
            "logged_in": False,
            "is_owner": False,
            "is_member": False,
            "can_copy": kind == "local",
            "can_request_edit": kind == "local",
            "user": None,
        }
    uid = str(int(principal.telegram_user_id))
    is_owner = uid == owner
    is_member = is_owner
    if kind == "local" and item_id and not is_owner:
        from assistant.stores import note_members

        try:
            is_member = note_members.is_member(owner, int(item_id), uid)
        except (TypeError, ValueError):
            is_member = False
    return {
        "logged_in": True,
        "is_owner": is_owner,
        "is_member": bool(is_member),
        "can_copy": kind == "local" and not is_owner,
        "can_request_edit": kind == "local" and not is_member,
        "user": _share_viewer_public(principal),
    }


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
    if not share_comments_store.comments_visible_for_link(link):
        raise HTTPException(status_code=403, detail="Комментарии недоступны")
    from assistant.stores import note_chats

    rows = await run_in_threadpool(
        note_chats.visible_comments,
        str(link["user_id"]),
        str(link["item_kind"]),
        str(link["item_id"]),
        None,
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
    from assistant.stores import note_chats

    if body.parent_id not in (None, 0):
        parent = await run_in_threadpool(share_comments_store.get_comment, int(body.parent_id))
        if not note_chats.comment_visible(parent, None):
            raise HTTPException(status_code=404, detail="Комментарий для ответа не найден")
    pub_chat = (
        note_chats.COMMENTS_CHAT_ID if note_chats.supports_chats(str(link["item_kind"])) else None
    )
    try:
        row = await run_in_threadpool(
            partial(
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
                parent_id=body.parent_id,
                chat_id=pub_chat if body.parent_id in (None, 0) else None,
            )
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
    from assistant.stores import note_chats

    if not note_chats.comment_visible(row, None):
        raise HTTPException(status_code=404, detail="Комментарий не найден")
    uid = str(int(principal.telegram_user_id))
    deleted = await run_in_threadpool(
        share_comments_store.delete_comment, comment_id, requester_user_id=uid
    )
    if not deleted:
        raise HTTPException(status_code=403, detail="Нельзя удалить этот комментарий")
    return {"ok": True}


@app.post("/api/public/share/{token}/copy")
async def public_share_copy(
    token: str,
    principal: _MiniappPrincipal = Depends(require_miniapp_user),
) -> dict[str, Any]:
    from assistant.stores import notes as notes_store

    link = await _resolve_public_share_or_404(token)
    if str(link.get("item_kind") or "") != "local":
        raise HTTPException(status_code=400, detail="В свои заметки можно добавить только заметку")
    owner = str(link.get("user_id") or "")
    try:
        nid = int(link.get("item_id") or 0)
    except (TypeError, ValueError):
        nid = 0
    if not owner or nid <= 0:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    uid = int(principal.telegram_user_id)
    if str(uid) == owner:
        note = await run_in_threadpool(notes_store.get_note, owner, nid)
        if not note:
            raise HTTPException(status_code=404, detail="Документ недоступен")
        return {"ok": True, "item": note, "copied": False}

    from assistant.stores import note_sheets as note_sheets_store
    from assistant.stores import share_links as share_links_store

    sheet_ids = share_links_store.normalize_sheet_ids(link.get("sheet_ids"))

    def _copy() -> dict[str, Any] | None:
        src = notes_store.get_note(owner, nid)
        if not src:
            return None
        # Копируем ровно те листы, которые открыты этой ссылкой (как на странице share).
        return note_sheets_store.copy_shared_selection(src, sheet_ids, uid)

    item = await run_in_threadpool(_copy)
    if not item:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    return {"ok": True, "item": item, "copied": True}


@app.post("/api/public/share/{token}/request-edit")
async def public_share_request_edit(
    token: str,
    principal: _MiniappPrincipal = Depends(require_share_commenter),
) -> dict[str, Any]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    link = await _resolve_public_share_or_404(token)
    if str(link.get("item_kind") or "") != "local":
        raise HTTPException(
            status_code=400, detail="Запросить редактирование можно только для заметки"
        )
    owner = str(link.get("user_id") or "")
    try:
        nid = int(link.get("item_id") or 0)
    except (TypeError, ValueError):
        nid = 0
    if not owner or nid <= 0:
        raise HTTPException(status_code=404, detail="Документ недоступен")
    await run_in_threadpool(_remember_principal_profile, principal)
    author_id, author_name, author_username = _comment_author_from_principal(principal)
    try:
        req = await run_in_threadpool(
            partial(
                note_members.create_edit_request,
                owner_user_id=owner,
                note_id=nid,
                requester_user_id=author_id,
                requester_username=author_username,
                requester_name=author_name,
                share_token=token,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    note = await run_in_threadpool(notes_store.get_note, owner, nid)
    title = str((note or {}).get("title") or "Без названия")
    if req.get("new"):
        await run_in_threadpool(_notify_edit_request, req, title)
    return {"ok": True, "status": req.get("status")}


def _booking_http_error(exc: BaseException) -> None:
    from assistant.services.booking_links import BookingLinkError

    if isinstance(exc, BookingLinkError):
        raise HTTPException(
            status_code=exc.status,
            detail={"reason": exc.reason, "message": str(exc)},
        ) from exc
    raise exc


@app.get("/api/public/book/{token}")
async def public_book_json(
    token: str,
    duration_min: Optional[int] = Query(None),
) -> dict[str, Any]:
    from assistant.services.booking_links import BookingLinkError, public_booking_meta

    def _run() -> dict[str, Any]:
        return public_booking_meta(token, duration_min=duration_min)

    try:
        return await run_in_threadpool(_run)
    except BookingLinkError as e:
        _booking_http_error(e)
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        _raise_calendar_http(e)


@app.get("/api/public/book/{token}/slots")
async def public_book_day_slots(
    token: str,
    date: str = Query(""),
    duration_min: Optional[int] = Query(None),
) -> dict[str, Any]:
    from assistant.services.booking_links import BookingLinkError, public_booking_day_slots

    def _run() -> dict[str, Any]:
        return public_booking_day_slots(token, date, duration_min=duration_min)

    try:
        return await run_in_threadpool(_run)
    except BookingLinkError as e:
        _booking_http_error(e)
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        _raise_calendar_http(e)


@app.post("/api/public/book/{token}/book")
async def public_book_slot(token: str, body: _PublicBookBody) -> dict[str, Any]:
    from assistant.services.booking_links import BookingLinkError, book_public_slot

    def _run() -> dict[str, Any]:
        return book_public_slot(
            token,
            start=body.start,
            duration_min=body.duration_min,
            guest_email=body.guest_email,
            guest_emails=body.guest_emails,
        )

    try:
        result = await run_in_threadpool(_run)
    except BookingLinkError as e:
        _booking_http_error(e)
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        _raise_calendar_http(e)
    created = result.pop("_created", None) or {}
    owner_id = int(result.pop("_owner_id", 0) or 0)
    guests = list(result.pop("_guests", None) or [])
    source_chat_id = result.pop("_source_chat_id", None)
    source_message_id = result.pop("_source_message_id", None)
    if created and owner_id:
        try:
            from assistant.services.booking_links import announce_public_booking

            await announce_public_booking(
                owner_id=owner_id,
                created=created,
                guests=guests,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
        except Exception as e:
            print(f"[public_book] announce err={e!r}")
    return result


@app.get("/book/{token}")
async def public_book_page(token: str) -> HTMLResponse:
    from assistant.lib.webapp_public import WEBAPP_BUILD_ID
    from assistant.services.booking_links import (
        BookingLinkError,
        first_bookable_day,
        format_booking_day_heading,
        public_booking_dates_html,
        public_booking_meta,
    )

    book_html_path = Path(__file__).resolve().parent / "webapp" / "book.html"
    if not book_html_path.is_file():
        raise HTTPException(status_code=404, detail="Страница недоступна")
    html = book_html_path.read_text(encoding="utf-8")
    title = "Выберите время"
    lead = "Свободные слоты на ближайшие дни"
    dates_html = public_booking_dates_html([])
    day_heading = ""
    duration_class = ""
    meta: dict[str, Any] = {}
    try:
        meta = await run_in_threadpool(public_booking_meta, token)
        title = str(meta.get("title") or "").strip() or title
        tz = str(meta.get("timezone") or "").strip()
        if tz:
            lead = f"Время указано в часовом поясе {tz}"
        days = list(meta.get("days") or [])
        selected = first_bookable_day(days)
        dates_html = public_booking_dates_html(days, selected=selected)
        today = None
        if days:
            try:
                today = date.fromisoformat(str(days[0].get("date") or "")[:10])
            except ValueError:
                today = None
        if selected:
            day_heading = format_booking_day_heading(selected, today=today)
        if meta.get("duration_min"):
            duration_class = " hidden"
    except BookingLinkError:
        meta = {}
    except Exception:
        meta = {}
    meta_json = json.dumps(meta, ensure_ascii=False).replace("<", "\\u003c")
    html = html.replace("{{BUILD}}", html_lib.escape(WEBAPP_BUILD_ID))
    html = html.replace("{{TITLE}}", html_lib.escape(title))
    html = html.replace("{{LEAD}}", html_lib.escape(lead))
    html = html.replace("{{DATES}}", dates_html)
    html = html.replace("{{DAY_HEADING}}", html_lib.escape(day_heading))
    html = html.replace("{{DURATION_CLASS}}", duration_class)
    html = html.replace("{{META_JSON}}", meta_json)
    return HTMLResponse(content=html, headers=dict(_SHARE_PAGE_NO_CACHE))


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
    async def webapp_index_route(request: Request):
        from urllib.parse import urlencode

        from assistant.lib.webapp_public import WEBAPP_BUILD_ID

        # Telegram/браузер кэшируют миниапп по ?b= — старый b оставлял битую оболочку.
        current = (WEBAPP_BUILD_ID or "").strip()
        got = (request.query_params.get("b") or "").strip()
        if current and got and got != current:
            params: list[tuple[str, str]] = [("b", current)]
            for key, val in request.query_params.multi_items():
                if key == "b":
                    continue
                params.append((key, val))
            return RedirectResponse(
                url="/webapp/?" + urlencode(params),
                status_code=302,
            )
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
