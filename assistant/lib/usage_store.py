"""SQLite-хранилище событий расхода OpenRouter (токены, стоимость)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_lock = threading.Lock()


def _usage_project_dir() -> Path:
    """Каталог проекта (рядом с bot.py / usage_server.py): якорь для относительного USAGE_DB_PATH."""
    return Path(__file__).resolve().parent


def _db_path() -> Path:
    """Путь к usage.sqlite.

    Относительный USAGE_DB_PATH в .env резолвится от каталога проекта, а не от cwd процесса,
    чтобы bot и usage_server (systemd с разным WorkingDirectory) смотрели в один файл.
    """
    anchor = _usage_project_dir()
    raw = os.getenv("USAGE_DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (anchor / p).resolve()
        else:
            p = p.resolve()
        return p
    return (anchor / "usage.sqlite").resolve()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        # Создаём таблицу (или убеждаемся, что она существует).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                date_utc TEXT NOT NULL,
                operation TEXT NOT NULL,
                model TEXT,
                generation_id TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cost_usd REAL,
                telegram_user_id TEXT,
                telegram_username TEXT,
                raw_usage_json TEXT
            )
            """
        )
        # Миграции "на месте": если таблица уже существовала без колонок пользователей.
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(usage_events)").fetchall()
        }
        # Добавляем колонки без пересоздания таблицы.
        if "telegram_user_id" not in existing_cols:
            conn.execute("ALTER TABLE usage_events ADD COLUMN telegram_user_id TEXT")
        if "telegram_username" not in existing_cols:
            conn.execute(
                "ALTER TABLE usage_events ADD COLUMN telegram_username TEXT"
            )
        # Таблица могла быть создана старым кодом без raw_usage_json.
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(usage_events)").fetchall()
        }
        if "raw_usage_json" not in existing_cols:
            conn.execute(
                "ALTER TABLE usage_events ADD COLUMN raw_usage_json TEXT"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_date ON usage_events(date_utc)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_user ON usage_events(telegram_user_id)"
        )
        conn.commit()


def insert_usage_event(
    *,
    operation: str,
    model: str | None,
    generation_id: str | None,
    usage: dict[str, Any] | None,
    ts_utc: datetime | None = None,
    telegram_user_id: str | None = None,
    telegram_username: str | None = None,
) -> int:
    """Записывает одно успешное обращение к chat/completions или внешний сервис (Obuchat)."""
    if usage is None:
        usage = {}
    when = ts_utc or datetime.now(timezone.utc)
    date_utc = when.date().isoformat()
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    tt = usage.get("total_tokens")
    try:
        prompt_tokens = int(pt) if pt is not None else None
    except (TypeError, ValueError):
        prompt_tokens = None
    try:
        completion_tokens = int(ct) if ct is not None else None
    except (TypeError, ValueError):
        completion_tokens = None
    try:
        total_tokens = int(tt) if tt is not None else None
    except (TypeError, ValueError):
        total_tokens = None
    # Некоторые провайдеры могут не присылать total_tokens.
    # Если есть prompt и completion — считаем total корректно.
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    cost = usage.get("cost")
    try:
        cost_usd = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost_usd = None

    raw_json = json.dumps(usage, ensure_ascii=False)

    event_id = 0
    with _lock:
        init_db()
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO usage_events (
                    ts_utc, date_utc, operation, model, generation_id,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd,
                    telegram_user_id, telegram_username, raw_usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    when.isoformat(timespec="seconds"),
                    date_utc,
                    operation,
                    (model or "").strip() or None,
                    (generation_id or "").strip() or None,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost_usd,
                    (telegram_user_id or "").strip() or None,
                    (telegram_username or "").strip() or None,
                    raw_json,
                ),
            )
            event_id = int(cur.lastrowid or 0)
            conn.commit()

    if cost_usd is not None and cost_usd > 0:
        uid_s = (telegram_user_id or "").strip()
        if uid_s:
            try:
                from assistant.lib import billing_store

                billing_store.charge_cost_usd(int(uid_s), cost_usd)
            except Exception as e:
                print(f"[billing_store] charge_failed uid={uid_s!r} err={e!r}")

    return event_id


def usage_journal_meta() -> dict[str, Any]:
    """Путь к журналу usage и число записей (для подписи на дашборде и отладки)."""
    path = _db_path()
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM usage_events").fetchone()
        n = int(row["c"] if row else 0)
    return {"db_path": str(path), "n_events": n}


def daily_summary() -> list[dict[str, Any]]:
    """Агрегаты по дням (UTC), новые дни первыми."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                date_utc,
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM usage_events
            GROUP BY date_utc
            ORDER BY date_utc DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]


# Русские подписи операций (синхронизировать с webapp/app.js operationLabel).
OPERATION_LABELS_RU: dict[str, str] = {
    "intent_route": "Маршрутизация запроса",
    "parse_calendar": "Разбор встречи (календарь)",
    "parse_zoom": "Разбор Zoom-встречи",
    "parse_reminder": "Разбор напоминания",
    "format_note": "Оформление заметки",
    "ask": "Вопрос к GPT",
    "obuchat_transcribe": "Транскрипция",
    "summarize": "Саммари",
    "summarize_llm": "Саммари (LLM)",
    "answer_with_context": "Обсуждение с GPT (архив)",
    "chat/completions": "Запрос к модели",
}


def operation_label_ru(op: str) -> str:
    """Человекочитаемое название операции."""
    key = (op or "").strip()
    if not key:
        return "Прочее"
    return OPERATION_LABELS_RU.get(key, key)


def _operation_label_extra(raw: str | None) -> str:
    if not raw or not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        j = json.loads(raw)
        if isinstance(j, dict):
            prov = str(j.get("provider") or "").strip()
            if prov:
                return f" ({prov})"
    except json.JSONDecodeError:
        pass
    return ""


def operation_display_label(
    op: str, *, model: str | None = None, raw_usage_json: str | None = None
) -> str:
    """Подпись для UI: русское имя + provider + модель."""
    base = operation_label_ru(op) + _operation_label_extra(raw_usage_json)
    m = (model or "").strip()
    return f"{base} · {m}" if m else base


def distinct_operations(from_date_utc: str, to_date_utc: str) -> list[str]:
    """Уникальные operation за период (UTC-дни), отсортировано."""
    init_db()
    from_d = from_date_utc.strip()
    to_d = to_date_utc.strip()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT operation
            FROM usage_events
            WHERE date_utc >= ? AND date_utc <= ?
              AND operation IS NOT NULL AND TRIM(operation) != ''
            ORDER BY operation ASC
            """,
            (from_d, to_d),
        )
        return [str(row[0]) for row in cur.fetchall()]


def users_summary(
    from_date_utc: str, to_date_utc: str, *, operation: str | None = None
) -> list[dict[str, Any]]:
    """Агрегаты по пользователям (telegram_user_id) за период (включительно), по UTC-дням."""
    init_db()
    from_d = from_date_utc.strip()
    to_d = to_date_utc.strip()
    op = (operation or "").strip() or None
    sql = """
            SELECT
                COALESCE(telegram_user_id, '__unknown__') AS telegram_user_id,
                COALESCE(MAX(telegram_username), '') AS telegram_username,
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM usage_events
            WHERE date_utc >= ? AND date_utc <= ?
            """
    params: list[Any] = [from_d, to_d]
    if op:
        sql += " AND operation = ?"
        params.append(op)
    sql += """
            GROUP BY COALESCE(telegram_user_id, '__unknown__')
            ORDER BY cost_usd DESC
            """
    with _connect() as conn:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def summary_by_operation(
    from_date_utc: str, to_date_utc: str
) -> list[dict[str, Any]]:
    """Агрегаты по типу operation за период."""
    init_db()
    from_d = from_date_utc.strip()
    to_d = to_date_utc.strip()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                operation,
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM usage_events
            WHERE date_utc >= ? AND date_utc <= ?
            GROUP BY operation
            ORDER BY cost_usd DESC, operation ASC
            """,
            (from_d, to_d),
        )
        return [dict(row) for row in cur.fetchall()]


def user_summary_by_operation(
    telegram_user_id: str, from_date_utc: str, to_date_utc: str
) -> list[dict[str, Any]]:
    """Агрегаты по operation для одного пользователя за период."""
    init_db()
    uid = (telegram_user_id or "").strip()
    from_d = from_date_utc.strip()
    to_d = to_date_utc.strip()
    if not uid:
        return []
    user_clause = "telegram_user_id IS NULL" if uid == "__unknown__" else "telegram_user_id = ?"
    params: list[Any] = [] if uid == "__unknown__" else [uid]
    params.extend([from_d, to_d])
    with _connect() as conn:
        cur = conn.execute(
            f"""
            SELECT
                operation,
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM usage_events
            WHERE {user_clause}
              AND date_utc >= ? AND date_utc <= ?
            GROUP BY operation
            ORDER BY cost_usd DESC, operation ASC
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]


def period_totals(
    from_date_utc: str, to_date_utc: str, *, operation: str | None = None
) -> dict[str, Any]:
    """Итоги по всем usage_events за период (UTC-дни), агрегирует по пользователям."""
    init_db()
    from_d = from_date_utc.strip()
    to_d = to_date_utc.strip()
    op = (operation or "").strip() or None
    sql = """
            SELECT
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM usage_events
            WHERE date_utc >= ? AND date_utc <= ?
            """
    params: list[Any] = [from_d, to_d]
    if op:
        sql += " AND operation = ?"
        params.append(op)
    with _connect() as conn:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else {
            "n_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }


def events_for_user_date(telegram_user_id: str, date_utc: str) -> list[dict[str, Any]]:
    """Все операции конкретного пользователя за день YYYY-MM-DD (UTC)."""
    init_db()
    uid = (telegram_user_id or "").strip()
    d = (date_utc or "").strip()
    if not uid or not d:
        return []
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                id,
                ts_utc,
                operation,
                model,
                generation_id,
                prompt_tokens,
                completion_tokens,
                COALESCE(
                    total_tokens,
                    CASE
                        WHEN prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL
                        THEN prompt_tokens + completion_tokens
                        ELSE NULL
                    END
                ) AS total_tokens,
                cost_usd
            FROM usage_events
            WHERE telegram_user_id = ?
              AND date_utc = ?
            ORDER BY ts_utc ASC, id ASC
            """,
            (None if uid == "__unknown__" else uid, d),
        )
        # Если uid = "__unknown__", выбираем события с NULL telegram_user_id.
        if uid == "__unknown__":
            cur = conn.execute(
                """
                SELECT
                    id,
                    ts_utc,
                    operation,
                    model,
                    generation_id,
                    prompt_tokens,
                    completion_tokens,
                    COALESCE(
                        total_tokens,
                        CASE
                            WHEN prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL
                            THEN prompt_tokens + completion_tokens
                            ELSE NULL
                        END
                    ) AS total_tokens,
                    cost_usd
                FROM usage_events
                WHERE telegram_user_id IS NULL
                  AND date_utc = ?
                ORDER BY ts_utc ASC, id ASC
                """,
                (d,),
            )
        return [dict(row) for row in cur.fetchall()]


def user_daily_summary(
    telegram_user_id: str, from_date_utc: str, to_date_utc: str
) -> list[dict[str, Any]]:
    """Агрегаты по дням для конкретного пользователя за период (включительно), по UTC-дням."""
    init_db()
    uid = (telegram_user_id or "").strip()
    from_d = from_date_utc.strip()
    to_d = to_date_utc.strip()
    if not uid:
        return []
    with _connect() as conn:
        if uid == "__unknown__":
            cur = conn.execute(
                """
                SELECT
                    date_utc,
                    COUNT(*) AS n_calls,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(
                        SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                        0
                    ) AS total_tokens,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd
                FROM usage_events
                WHERE telegram_user_id IS NULL
                  AND date_utc >= ? AND date_utc <= ?
                GROUP BY date_utc
                ORDER BY date_utc DESC
                """,
                (from_d, to_d),
            )
        else:
            cur = conn.execute(
                """
                SELECT
                    date_utc,
                    COUNT(*) AS n_calls,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(
                        SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                        0
                    ) AS total_tokens,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd
                FROM usage_events
                WHERE telegram_user_id = ?
                  AND date_utc >= ? AND date_utc <= ?
                GROUP BY date_utc
                ORDER BY date_utc DESC
                """,
                (uid, from_d, to_d),
            )
        return [dict(row) for row in cur.fetchall()]


_JOURNAL_OPERATIONS = (
    "obuchat_transcribe",
    "summarize",
    "format_note",
    "answer_with_context",
)


def _unescape_json_string_fragment(fragment: str) -> str:
    try:
        return json.loads('"' + fragment + '"')
    except json.JSONDecodeError:
        return (
            fragment.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def journal_json_from_raw(raw: str | None) -> dict[str, Any]:
    """Распарсенный raw_usage_json или пустой dict."""
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        j = json.loads(raw.strip())
        if isinstance(j, dict):
            return j
    except json.JSONDecodeError:
        pass
    return {}


def journal_meta_from_raw(raw: str | None) -> dict[str, Any]:
    """Структурированные поля саммари из raw_usage_json."""
    j = journal_json_from_raw(raw)
    meta = j.get("meta")
    return meta if isinstance(meta, dict) else {}


def journal_has_content_text(raw: str | None) -> bool:
    """Есть ли в записи журнала пользовательский текст (не только токены LLM)."""
    j = journal_json_from_raw(raw)
    return bool(str(j.get("text") or "").strip())


def journal_source_links_from_raw(raw: str | None) -> dict[str, str]:
    """Ссылки на исходник саммари/транскрипции."""
    j = journal_json_from_raw(raw)
    out: dict[str, str] = {}
    source_url = str(j.get("source_url") or j.get("url") or "").strip()
    if source_url:
        out["source_url"] = source_url
    tg = str(j.get("telegram_link") or "").strip()
    if tg:
        out["telegram_link"] = tg
    return out


def journal_text_from_raw(raw: str | None) -> str:
    """Текст транскрипции/заметки из raw_usage_json (без обёртки JSON)."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    s = raw.strip()
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            t = j.get("text")
            if isinstance(t, str):
                return t
    except json.JSONDecodeError:
        pass
    m = re.search(r'"text"\s*:\s*"(.*)', s, re.DOTALL)
    if m:
        chunk = m.group(1)
        chunk = re.sub(
            r'"\s*,\s*"(?:source|filename|url|provider)"\s*:.*$',
            "",
            chunk,
            flags=re.DOTALL,
        )
        chunk = chunk.rstrip('"}')
        return _unescape_json_string_fragment(chunk)
    if s.startswith('{"text":'):
        inner = s.split('"text"', 1)[-1]
        inner = re.sub(r'^\s*:\s*"', "", inner, count=1)
        return _unescape_json_string_fragment(inner.rstrip('"}'))
    return s


def get_user_usage_event(telegram_user_id: str, event_id: int) -> dict[str, Any] | None:
    """Одна запись usage_events по id, только если она принадлежит пользователю."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return None
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        return None
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id, ts_utc, date_utc, operation, model, raw_usage_json, cost_usd
            FROM usage_events
            WHERE id = ? AND telegram_user_id = ?
            """,
            (eid, uid),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def user_expenses_by_day(
    telegram_user_id: str, *, last_n_days: int = 7
) -> list[dict[str, Any]]:
    """Расходы по UTC-дням: события с cost_usd, суммы в «отображаемых» ₽ = USD * курс * 5."""
    init_db()
    uid = (telegram_user_id or "").strip()
    n = max(1, min(int(last_n_days or 7), 366))
    if not uid:
        return []
    from assistant.lib import billing_store

    rub_per_usd = billing_store.miniapp_rub_per_usd()

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=n - 1)
    from_d = start.isoformat()
    to_d = end.isoformat()

    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                id, ts_utc, date_utc, operation, model, raw_usage_json,
                cost_usd, prompt_tokens, completion_tokens, total_tokens
            FROM usage_events
            WHERE telegram_user_id = ?
              AND date_utc >= ? AND date_utc <= ?
              AND (
                (cost_usd IS NOT NULL AND cost_usd > 0)
                OR COALESCE(prompt_tokens, 0) > 0
                OR COALESCE(completion_tokens, 0) > 0
                OR COALESCE(total_tokens, 0) > 0
              )
            ORDER BY date_utc ASC, ts_utc ASC, id ASC
            """,
            (uid, from_d, to_d),
        )
        rows = [dict(r) for r in cur.fetchall()]

    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = str(r.get("date_utc") or "").strip()
        if not d:
            continue
        try:
            cusd = float(r.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            cusd = 0.0
        rub = round(cusd * rub_per_usd, 4) if cusd > 0 else 0.0
        try:
            pt = int(r.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            pt = 0
        try:
            ctok = int(r.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            ctok = 0
        tt_raw = r.get("total_tokens")
        if tt_raw is not None:
            try:
                tk = int(tt_raw)
            except (TypeError, ValueError):
                tk = pt + ctok
        else:
            tk = pt + ctok
        base_label = operation_display_label(
            str(r.get("operation") or ""),
            model=str(r.get("model") or "") if r.get("model") else None,
            raw_usage_json=str(r.get("raw_usage_json") or "")
            if r.get("raw_usage_json")
            else None,
        )
        if cusd <= 0 and tk > 0:
            suf = " (стоимость не в ответе API — только токены)"
        else:
            suf = ""
        req = {
            "id": r.get("id"),
            "ts_utc": r.get("ts_utc"),
            "operation": r.get("operation"),
            "label": base_label + suf,
            "cost_usd": round(cusd, 6) if cusd > 0 else None,
            "cost_rub": rub,
            "approx_tokens": tk,
        }
        by_day.setdefault(d, []).append(req)

    out: list[dict[str, Any]] = []
    for d in sorted(by_day.keys(), reverse=True):
        reqs = by_day[d]
        total = round(sum(float(x.get("cost_rub") or 0) for x in reqs), 2)
        out.append({"date_utc": d, "total_rub": total, "requests": reqs})
    return out


def user_journal_entries(telegram_user_id: str, *, limit: int = 120) -> list[dict[str, Any]]:
    """События журнала для вкладки «Заметки»: транскрипции, саммари, форматирование заметок, Q&A."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return []
    lim = max(1, min(int(limit or 120), 500))
    placeholders = ",".join("?" * len(_JOURNAL_OPERATIONS))
    with _connect() as conn:
        cur = conn.execute(
            f"""
            SELECT id, ts_utc, date_utc, operation, model, raw_usage_json
            FROM usage_events
            WHERE telegram_user_id = ?
              AND operation IN ({placeholders})
            ORDER BY ts_utc DESC, id DESC
            LIMIT ?
            """,
            (uid, *_JOURNAL_OPERATIONS, lim),
        )
        rows = [dict(row) for row in cur.fetchall()]
    out: list[dict[str, Any]] = []
    for row in rows:
        op = str(row.get("operation") or "")
        raw = row.get("raw_usage_json")
        if op == "summarize" and not journal_has_content_text(
            raw if isinstance(raw, str) else None
        ):
            continue
        out.append(row)
    return out


def search_transcriptions(
    telegram_user_id: str, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Поиск по тексту сохранённых транскрипций пользователя."""
    init_db()
    uid = (telegram_user_id or "").strip()
    q = (query or "").strip()
    if not uid or not q:
        return []
    words = [w for w in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(w) >= 2]
    if not words:
        words = [q.lower()]
    lim = max(1, min(int(limit), 20))
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id, ts_utc, date_utc, raw_usage_json
            FROM usage_events
            WHERE telegram_user_id = ?
              AND operation = 'obuchat_transcribe'
            ORDER BY ts_utc DESC, id DESC
            LIMIT 500
            """,
            (uid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for r in rows:
        text = journal_text_from_raw(
            str(r.get("raw_usage_json") or "") if r.get("raw_usage_json") else None
        ).strip()
        if not text:
            continue
        hay = text.lower()
        score = sum(1 for w in words if w in hay)
        if score <= 0:
            continue
        ts = str(r.get("ts_utc") or "")
        headline = text.split("\n", 1)[0].strip()[:120] or "Транскрипция"
        scored.append(
            (
                score,
                ts,
                {
                    "id": r.get("id"),
                    "ts_utc": ts,
                    "date_utc": r.get("date_utc"),
                    "headline": headline,
                    "text": text,
                },
            )
        )
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _, _, item in scored[:lim]]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _summary_search_words(query: str) -> list[str]:
    q = (query or "").strip()
    words = [w for w in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(w) >= 2]
    if words:
        return words
    return [q.lower()] if q else []


def _text_matches_words(text: str, words: list[str]) -> int:
    hay = (text or "").lower()
    if not hay:
        return 0
    return sum(1 for w in words if w in hay)


def _summary_field_text(meta: dict[str, Any], field: str | None) -> str:
    f = (field or "").strip().lower()
    if f == "decisions":
        items = meta.get("decisions") or []
        return "\n".join(str(x) for x in items if str(x).strip())
    if f == "topics":
        items = meta.get("topics") or []
        return "\n".join(str(x) for x in items if str(x).strip())
    if f == "participants":
        items = meta.get("participants") or []
        return "\n".join(str(x) for x in items if str(x).strip())
    if f == "tasks":
        tasks = meta.get("tasks") or []
        lines: list[str] = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            assignee = str(t.get("assignee") or "").strip()
            task = str(t.get("task") or "").strip()
            deadline = str(t.get("deadline") or "").strip()
            parts = [p for p in (assignee, task, deadline) if p]
            if parts:
                lines.append(" ".join(parts))
        return "\n".join(lines)
    if f == "main_topic":
        return str(meta.get("main_topic") or "").strip()
    return ""


def get_latest_summary(telegram_user_id: str) -> dict[str, Any] | None:
    """Последнее сохранённое саммари пользователя."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return None
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id, ts_utc, date_utc, raw_usage_json
            FROM usage_events
            WHERE telegram_user_id = ? AND operation = 'summarize'
            ORDER BY ts_utc DESC, id DESC
            LIMIT 1
            """,
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    r = dict(row)
    raw = str(r.get("raw_usage_json") or "")
    j = journal_json_from_raw(raw)
    meta = journal_meta_from_raw(raw)
    text = str(j.get("text") or "").strip()
    if not text:
        return None
    headline = (
        str(meta.get("main_topic") or "").strip()
        or text.split("\n", 1)[0].strip()[:120]
        or "Саммари"
    )
    return {
        "id": r.get("id"),
        "ts_utc": str(r.get("ts_utc") or ""),
        "date_utc": r.get("date_utc"),
        "headline": headline,
        "text": text,
        "meta": meta,
        "transcript": str(j.get("transcript") or "").strip() or None,
        "source_url": str(j.get("source_url") or "").strip() or None,
        "telegram_link": str(j.get("telegram_link") or "").strip() or None,
    }


def search_summaries(
    telegram_user_id: str,
    query: str,
    *,
    field: str | None = None,
    assignee: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Поиск по сохранённым саммари: структурированные поля meta или текст summary."""
    init_db()
    uid = (telegram_user_id or "").strip()
    q = (query or "").strip()
    if not uid or not q:
        return []
    words = _summary_search_words(q)
    assignee_q = (assignee or "").strip().lower()
    lim = max(1, min(int(limit), 20))
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id, ts_utc, date_utc, raw_usage_json
            FROM usage_events
            WHERE telegram_user_id = ?
              AND operation = 'summarize'
            ORDER BY ts_utc DESC, id DESC
            LIMIT 500
            """,
            (uid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for r in rows:
        raw = str(r.get("raw_usage_json") or "") if r.get("raw_usage_json") else None
        j = journal_json_from_raw(raw)
        meta = journal_meta_from_raw(raw)
        raw_summary_text = str(j.get("text") or "").strip()
        if not raw_summary_text:
            raw_summary_text = journal_text_from_raw(raw).strip()
        summary_text = _strip_html(raw_summary_text)
        if not summary_text:
            continue
        field_name = (field or "").strip().lower() or None
        hay = ""
        if field_name == "tasks":
            tasks = meta.get("tasks") or []
            task_lines: list[str] = []
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                ta = str(t.get("assignee") or "").strip()
                if assignee_q and assignee_q not in ta.lower():
                    continue
                task = str(t.get("task") or "").strip()
                deadline = str(t.get("deadline") or "").strip()
                parts = [p for p in (ta, task, deadline) if p]
                if parts:
                    task_lines.append(" ".join(parts))
            hay = "\n".join(task_lines)
            if assignee_q and not hay:
                continue
        elif field_name:
            hay = _summary_field_text(meta, field_name)
            if not hay and field_name != "summary":
                continue
        if not hay:
            hay = summary_text
            if meta.get("main_topic"):
                hay = f"{meta.get('main_topic')}\n{hay}"
        transcript_text = str(j.get("transcript") or "").strip()
        score = _text_matches_words(hay, words)
        if transcript_text:
            score = max(score, _text_matches_words(transcript_text, words))
        if score <= 0:
            continue
        ts = str(r.get("ts_utc") or "")
        headline = (
            str(meta.get("main_topic") or "").strip()
            or summary_text.split("\n", 1)[0].strip()[:120]
            or "Саммари"
        )
        scored.append(
            (
                score,
                ts,
                {
                    "id": r.get("id"),
                    "ts_utc": ts,
                    "date_utc": r.get("date_utc"),
                    "headline": headline,
                    "text": raw_summary_text,
                    "meta": meta,
                    "field": field_name,
                    "transcript": transcript_text or None,
                    "source_url": str(j.get("source_url") or "").strip() or None,
                    "telegram_link": str(j.get("telegram_link") or "").strip() or None,
                },
            )
        )
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _, _, item in scored[:lim]]


def update_user_journal_event(
    telegram_user_id: str,
    event_id: int,
    *,
    text: str | None = None,
    main_topic: str | None = None,
) -> dict[str, Any] | None:
    """Обновляет текст и/или заголовок (main_topic) записи журнала пользователя."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return None
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        return None
    if text is None and main_topic is None:
        return None
    row = get_user_usage_event(uid, eid)
    if not row:
        return None
    op = str(row.get("operation") or "")
    if op not in _JOURNAL_OPERATIONS:
        return None
    j = journal_json_from_raw(row.get("raw_usage_json"))
    if text is not None:
        j["text"] = text
    if main_topic is not None:
        meta = j.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        mt = str(main_topic).strip()
        if mt:
            meta["main_topic"] = mt
        elif "main_topic" in meta:
            del meta["main_topic"]
        j["meta"] = meta
    new_raw = json.dumps(j, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE usage_events
            SET raw_usage_json = ?
            WHERE id = ? AND telegram_user_id = ?
              AND operation IN ({ops})
            """.format(
                ops=",".join("?" * len(_JOURNAL_OPERATIONS))
            ),
            (new_raw, eid, uid, *_JOURNAL_OPERATIONS),
        )
        if (cur.rowcount or 0) == 0:
            return None
    return get_user_usage_event(uid, eid)


def find_summary_event_for_transcript(
    telegram_user_id: str, transcript_event_id: int
) -> int | None:
    """ID саммари, связанного с транскрипцией (meta.transcript_event_id)."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return None
    try:
        tid = int(transcript_event_id)
    except (TypeError, ValueError):
        return None
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT id, raw_usage_json
            FROM usage_events
            WHERE telegram_user_id = ? AND operation = 'summarize'
            ORDER BY ts_utc DESC, id DESC
            LIMIT 500
            """,
            (uid,),
        )
        for row in cur.fetchall():
            meta = journal_meta_from_raw(row["raw_usage_json"])
            try:
                linked = int(meta.get("transcript_event_id"))
            except (TypeError, ValueError):
                continue
            if linked == tid:
                return int(row["id"])
    return None


def delete_user_journal_event(telegram_user_id: str, event_id: int) -> bool:
    """Удаляет запись журнала (usage_events) пользователя, если это одна из операций мини-приложения."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return False
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        return False
    placeholders = ",".join("?" * len(_JOURNAL_OPERATIONS))
    with _connect() as conn:
        cur = conn.execute(
            f"""
            DELETE FROM usage_events
            WHERE id = ? AND telegram_user_id = ?
              AND operation IN ({placeholders})
            """,
            (eid, uid, *_JOURNAL_OPERATIONS),
        )
        return (cur.rowcount or 0) > 0


def user_lifetime_stats(telegram_user_id: str) -> dict[str, Any]:
    """Агрегат по пользователю за всё время: вызовы и токены, без cost_usd."""
    init_db()
    uid = (telegram_user_id or "").strip()
    if not uid:
        return {
            "n_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens
            FROM usage_events
            WHERE telegram_user_id = ?
            """,
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            return {
                "n_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        return {
            "n_calls": int(row["n_calls"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
        }


def user_daily_usage_no_cost(
    telegram_user_id: str, *, last_n_days: int = 30
) -> list[dict[str, Any]]:
    """Разбивка по UTC-дням за последние N дней, только счётчики (без денег)."""
    init_db()
    uid = (telegram_user_id or "").strip()
    n = max(1, min(int(last_n_days or 30), 366))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=n - 1)
    from_d = start.isoformat()
    to_d = end.isoformat()
    if not uid:
        return []
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                date_utc,
                COUNT(*) AS n_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(
                    SUM(COALESCE(total_tokens, prompt_tokens + completion_tokens)),
                    0
                ) AS total_tokens
            FROM usage_events
            WHERE telegram_user_id = ?
              AND date_utc >= ? AND date_utc <= ?
            GROUP BY date_utc
            ORDER BY date_utc DESC
            """,
            (uid, from_d, to_d),
        )
        rows = [dict(row) for row in cur.fetchall()]
    for r in rows:
        r["n_calls"] = int(r.get("n_calls") or 0)
        r["prompt_tokens"] = int(r.get("prompt_tokens") or 0)
        r["completion_tokens"] = int(r.get("completion_tokens") or 0)
        r["total_tokens"] = int(r.get("total_tokens") or 0)
    return rows


def events_for_date(date_utc: str) -> list[dict[str, Any]]:
    """Все события за день YYYY-MM-DD (UTC), по времени."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT
                id, ts_utc, date_utc, operation, model, generation_id,
                prompt_tokens, completion_tokens,
                COALESCE(
                    total_tokens,
                    CASE
                        WHEN prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL
                        THEN prompt_tokens + completion_tokens
                        ELSE NULL
                    END
                ) AS total_tokens,
                cost_usd
            FROM usage_events
            WHERE date_utc = ?
            ORDER BY ts_utc ASC, id ASC
            """,
            (date_utc.strip(),),
        )
        return [dict(row) for row in cur.fetchall()]
