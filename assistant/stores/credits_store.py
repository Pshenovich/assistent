"""Кредиты ИИ: счета людей и организаций, списания, дневной лимит участника.

Модель (согласовано 30.10.2026, «Финмодель» в заметке про УТП):
- кредиты за ИИ = себестоимость в ₽ × 2 (маржа) / 0,8 ₽;
- расшифровка — 2 кредита за минуту;
- людям показываем кредиты и рубли по цене тарифа Solo — 1,3 ₽ за кредит.

Счёт: ('user', telegram_id) или ('org', org_id). Каждое изменение баланса — строка
в журнале credit_ledger и обновление credit_accounts в одной транзакции BEGIN IMMEDIATE,
поэтому бот и веб не теряют списания друг друга.

Участник организации: до дневного лимита расход идёт со «своих» (учитывается как
расход за день), сверх лимита — со счёта организации. Человек без организации
тратит только личный баланс.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from assistant.lib import sqlite_conn

SOLO_RUB_PER_CREDIT = 1.3
_COST_CREDIT_RUB = 0.8
_MARGIN = 2.0
CREDITS_PER_TRANSCRIBE_MINUTE = 2.0

USER_START_BALANCE = 2500.0
USER_TOPUP = 2500.0
ORG_START_BALANCE = 20000.0
ORG_TOPUP = 20000.0
DEFAULT_DAILY_LIMIT = 500.0

_lock = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None


def usd_to_rub() -> float:
    try:
        return float(os.getenv("USD_TO_RUB", "92").strip() or "92")
    except ValueError:
        return 92.0


def credits_for_cost_usd(cost_usd: float) -> float:
    return max(0.0, float(cost_usd or 0)) * usd_to_rub() * _MARGIN / _COST_CREDIT_RUB


def credits_for_minutes(minutes: float) -> float:
    return max(0.0, float(minutes or 0)) * CREDITS_PER_TRANSCRIBE_MINUTE


def credits_to_rub(credits: float) -> float:
    return float(credits or 0) * SOLO_RUB_PER_CREDIT


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def db_path() -> Path:
    raw = os.getenv("CREDITS_DB_PATH", "").strip()
    from assistant.config import ROOT

    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (ROOT / p).resolve()
    return ROOT / "data" / "credits.sqlite"


def _conn() -> sqlite3.Connection:
    """Соединение текущего пути; вызывать под _lock."""
    global _CONN, _CONN_PATH
    path = db_path()
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    if _CONN is not None:
        _CONN.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_conn.connect(path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS credit_accounts (
            kind TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            balance REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (kind, owner_id)
        );
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            user_id TEXT,
            delta REAL NOT NULL,
            reason TEXT NOT NULL,
            meta TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ledger_account ON credit_ledger(kind, owner_id, day);
        CREATE INDEX IF NOT EXISTS idx_ledger_user_day ON credit_ledger(user_id, day);
        CREATE TABLE IF NOT EXISTS user_day_spend (
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            credits REAL NOT NULL,
            PRIMARY KEY (user_id, day)
        );
        CREATE TABLE IF NOT EXISTS credit_notices (
            user_id TEXT NOT NULL,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            PRIMARY KEY (user_id, day, kind)
        );
        CREATE TABLE IF NOT EXISTS org_member_ai (
            org_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            daily_limit REAL,
            allowed_models TEXT,
            PRIMARY KEY (org_id, user_id)
        );
        """
    )
    _CONN, _CONN_PATH = conn, path
    return conn


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def _ensure_account(conn: sqlite3.Connection, kind: str, owner_id: str) -> float:
    row = conn.execute(
        "SELECT balance FROM credit_accounts WHERE kind = ? AND owner_id = ?", (kind, owner_id)
    ).fetchone()
    if row is not None:
        return float(row["balance"])
    start = USER_START_BALANCE if kind == "user" else ORG_START_BALANCE
    now = time.time()
    conn.execute(
        "INSERT INTO credit_accounts(kind, owner_id, balance, updated_at) VALUES (?, ?, ?, ?)",
        (kind, owner_id, start, now),
    )
    conn.execute(
        "INSERT INTO credit_ledger(ts, day, kind, owner_id, user_id, delta, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now, today_utc(), kind, owner_id, None, start, "start"),
    )
    return start


def _move(conn, kind: str, owner_id: str, delta: float, reason: str, *, user_id: str | None, meta: Any = None) -> float:
    bal = _ensure_account(conn, kind, owner_id) + delta
    now = time.time()
    conn.execute(
        "UPDATE credit_accounts SET balance = ?, updated_at = ? WHERE kind = ? AND owner_id = ?",
        (bal, now, kind, owner_id),
    )
    conn.execute(
        "INSERT INTO credit_ledger(ts, day, kind, owner_id, user_id, delta, reason, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now, today_utc(), kind, owner_id, user_id, delta, reason,
         json.dumps(meta, ensure_ascii=False) if meta is not None else None),
    )
    return bal


def balance(kind: str, owner_id: str | int) -> float:
    with _tx() as conn:
        return _ensure_account(conn, kind, str(owner_id))


def top_up(kind: str, owner_id: str | int, *, by_user: str | int | None = None) -> float:
    """Тестовое пополнение без оплаты: +2 500 человеку, +20 000 организации."""
    amount = USER_TOPUP if kind == "user" else ORG_TOPUP
    with _tx() as conn:
        return _move(conn, kind, str(owner_id), amount, "topup",
                     user_id=None if by_user is None else str(by_user))


def spent_today(user_id: str | int, day: str | None = None) -> float:
    with _lock:
        row = _conn().execute(
            "SELECT credits FROM user_day_spend WHERE user_id = ? AND day = ?",
            (str(user_id), day or today_utc()),
        ).fetchone()
    return float(row["credits"]) if row else 0.0


# ── настройки участника организации ─────────────────────────────────────────

def member_settings(org_id: str | int, user_id: str | int) -> dict[str, Any]:
    with _lock:
        row = _conn().execute(
            "SELECT daily_limit, allowed_models FROM org_member_ai WHERE org_id = ? AND user_id = ?",
            (str(org_id), str(user_id)),
        ).fetchone()
    limit = DEFAULT_DAILY_LIMIT
    allowed: list[str] | None = None  # None — доступны все модели
    if row is not None:
        if row["daily_limit"] is not None:
            limit = float(row["daily_limit"])
        if row["allowed_models"]:
            try:
                val = json.loads(row["allowed_models"])
                allowed = [str(x) for x in val] if isinstance(val, list) else None
            except ValueError:
                allowed = None
    return {"daily_limit": limit, "allowed_models": allowed}


def set_member_settings(
    org_id: str | int,
    user_id: str | int,
    *,
    daily_limit: float | None = None,
    allowed_models: list[str] | None = None,
    clear_models: bool = False,
) -> dict[str, Any]:
    """Права проверяет вызывающий (только владелец или админ организации)."""
    cur = member_settings(org_id, user_id)
    limit = cur["daily_limit"] if daily_limit is None else max(0.0, float(daily_limit))
    models = None if clear_models else (allowed_models if allowed_models is not None else cur["allowed_models"])
    with _tx() as conn:
        conn.execute(
            "INSERT INTO org_member_ai(org_id, user_id, daily_limit, allowed_models) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(org_id, user_id) DO UPDATE SET daily_limit = excluded.daily_limit, "
            "allowed_models = excluded.allowed_models",
            (str(org_id), str(user_id), limit,
             None if models is None else json.dumps(sorted(set(models)), ensure_ascii=False)),
        )
    return member_settings(org_id, user_id)


def model_allowed(org_id: str | int | None, user_id: str | int, model: str) -> bool:
    if org_id is None:
        return True
    allowed = member_settings(org_id, user_id)["allowed_models"]
    return allowed is None or model in allowed


def overage_by_member(org_id: str | int, day_from: str, day_to: str) -> dict[str, float]:
    """Сколько каждый участник потратил со счёта организации (сверх своего лимита)."""
    with _lock:
        rows = _conn().execute(
            "SELECT user_id, -SUM(delta) AS c FROM credit_ledger WHERE kind = 'org' AND owner_id = ? "
            "AND reason = 'spend' AND day >= ? AND day <= ? GROUP BY user_id",
            (str(org_id), day_from, day_to),
        ).fetchall()
    return {str(r["user_id"]): float(r["c"] or 0) for r in rows}


def take_notice(user_id: str | int, kind: str) -> bool:
    """True — первый раз за день для этого предупреждения (дальше молчим до завтра)."""
    with _tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO credit_notices(user_id, day, kind) VALUES (?, ?, ?)",
            (str(user_id), today_utc(), kind),
        )
        return bool(cur.rowcount)


# ── списание ────────────────────────────────────────────────────────────────

def charge(
    user_id: str | int,
    credits: float,
    *,
    org_id: str | int | None,
    reason_meta: Any = None,
) -> dict[str, Any]:
    """Списывает кредиты за действие пользователя.

    Участник организации: часть до дневного лимита — «своя» (только учёт за день),
    остаток — со счёта организации. Без организации — с личного баланса.
    Ничего не блокирует: решение «отвечать ли» принимает status() до вызова ИИ.
    """
    uid = str(user_id)
    amount = max(0.0, float(credits or 0))
    day = today_utc()
    out: dict[str, Any] = {"credits": amount, "from_org": 0.0, "from_user": 0.0}
    if amount <= 0:
        return out
    with _tx() as conn:
        row = conn.execute("SELECT credits FROM user_day_spend WHERE user_id = ? AND day = ?", (uid, day)).fetchone()
        before = float(row["credits"]) if row else 0.0
        conn.execute(
            "INSERT INTO user_day_spend(user_id, day, credits) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET credits = credits + excluded.credits",
            (uid, day, amount),
        )
        if org_id is None:
            _move(conn, "user", uid, -amount, "spend", user_id=uid, meta=reason_meta)
            out["from_user"] = amount
            return out
        limit = DEFAULT_DAILY_LIMIT
        mrow = conn.execute(
            "SELECT daily_limit FROM org_member_ai WHERE org_id = ? AND user_id = ?", (str(org_id), uid)
        ).fetchone()
        if mrow is not None and mrow["daily_limit"] is not None:
            limit = float(mrow["daily_limit"])
        over = max(0.0, before + amount - max(limit, before))
        if over > 0:
            _move(conn, "org", str(org_id), -over, "spend", user_id=uid, meta=reason_meta)
            out["from_org"] = over
    return out


def status(user_id: str | int, *, org_id: str | int | None) -> dict[str, Any]:
    """Что показать в полоске диалога и можно ли отвечать.

    state: ok — в пределах; over_limit — лимит исчерпан, платит организация;
    org_empty — лимит исчерпан и счёт организации пуст; empty — личный баланс кончился.
    """
    uid = str(user_id)
    spent = spent_today(uid)
    if org_id is None:
        bal = balance("user", uid)
        return {
            "mode": "personal",
            "balance": bal,
            "balance_rub": credits_to_rub(bal),
            "spent_today": spent,
            "state": "ok" if bal > 0 else "empty",
            "can_answer": bal > 0,
        }
    limit = member_settings(org_id, uid)["daily_limit"]
    org_bal = balance("org", str(org_id))
    over = spent >= limit
    state = "ok" if not over else ("over_limit" if org_bal > 0 else "org_empty")
    return {
        "mode": "org",
        "org_id": str(org_id),
        "daily_limit": limit,
        "spent_today": spent,
        "spent_today_rub": credits_to_rub(spent),
        "daily_limit_rub": credits_to_rub(limit),
        "share": 0.0 if limit <= 0 else min(1.0, spent / limit),
        "org_balance": org_bal,
        "state": state,
        "can_answer": state != "org_empty",
    }
