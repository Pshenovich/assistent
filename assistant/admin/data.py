"""Выборки для админки: каталог пользователей с доступом и расходом, обзор, карточка пользователя."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from assistant.lib import telegram_access_allowlist as access
from assistant.lib import usage_store

SCOPE = "leo"

STATUS_LABEL = {
    "operator": "Оператор",
    "open": "Доступ открыт",
    "pending": "Ждёт одобрения",
    "denied": "Отклонён",
    "none": "Без доступа",
}


def usd_to_rub() -> float:
    try:
        return float(os.getenv("USD_TO_RUB", "92").strip() or "92")
    except ValueError:
        return 92.0


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def period(days: int) -> tuple[str, str]:
    to_d = today_utc()
    return (to_d - timedelta(days=max(1, days) - 1)).isoformat(), to_d.isoformat()


def _profiles() -> dict[str, dict[str, Any]]:
    from assistant.stores import note_members

    with note_members._LOCK:  # type: ignore[attr-defined]
        rows = note_members._conn().execute("SELECT * FROM user_profiles").fetchall()  # type: ignore[attr-defined]
    return {str(r["user_id"]): dict(r) for r in rows}


def remember_requester(uid: str, req: dict[str, Any]) -> None:
    """Имя из заявки на доступ — в user_profiles, если профиля ещё нет (время — когда просил доступ)."""
    from assistant.stores import note_members

    ts = datetime.fromtimestamp(int(req.get("requested_at") or 0) or 0, tz=timezone.utc).replace(microsecond=0).isoformat()
    with note_members._LOCK:  # type: ignore[attr-defined]
        conn = note_members._conn()  # type: ignore[attr-defined]
        conn.execute(
            "INSERT OR IGNORE INTO user_profiles (user_id, username, first_name, last_name, photo_url, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (str(int(uid)), (req.get("username") or "").lstrip("@") or None, req.get("first_name") or None,
             req.get("last_name") or None, ts),
        )
        conn.commit()


def display_name(prof: dict[str, Any] | None, fallback: str = "") -> str:
    from assistant.stores import note_members

    return note_members.profile_display_name(prof, fallback=fallback)


def access_status(uid: int, username: str | None = None) -> str:
    if uid in access.approver_user_ids():
        return "operator"
    if not access.gate_enabled():
        return "open"
    if access.is_extra_allowed(username, uid, scope=SCOPE):
        return "open"
    if uid in access.list_pending(SCOPE):
        return "pending"
    if uid in access.denied_ids(SCOPE):
        return "denied"
    return "none"


def _ts(s: str | None) -> datetime | None:
    raw = str(s or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.fromisoformat(raw[:10])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def users_directory(from_d: str, to_d: str) -> list[dict[str, Any]]:
    """Все, кого знает Орбита: профили, одобренные, заявки и те, кто тратил токены."""
    from assistant.stores import orgs_store

    profiles = _profiles()
    pending = access.list_pending(SCOPE)
    _, allowed_ids = access.load_allowlist(SCOPE)
    spend = {str(r["telegram_user_id"]): r for r in usage_store.users_summary(from_d, to_d)}
    last_use = usage_store.last_usage_by_user()
    orgs = orgs_store.org_names_by_user()
    ids: set[str] = set(profiles) | {str(x) for x in pending} | {str(x) for x in allowed_ids}
    ids |= {k for k in spend if k.isdigit()} | {k for k in last_use if k.isdigit()}
    ids |= {str(x) for x in access.approver_user_ids()}
    rate = usd_to_rub()
    out: list[dict[str, Any]] = []
    for uid in ids:
        if not uid.isdigit():
            continue
        prof = profiles.get(uid)
        req = pending.get(int(uid)) or {}
        username = (prof or {}).get("username") or req.get("username") or (spend.get(uid) or {}).get("telegram_username") or ""
        name = display_name(prof, fallback="")
        if not name or name == "Участник":
            fn = " ".join(x for x in (req.get("first_name"), req.get("last_name")) if x).strip()
            name = fn or ("@" + username if username else "ID " + uid)
        seen = [t for t in (_ts((prof or {}).get("updated_at")), _ts(last_use.get(uid))) if t]
        if req.get("requested_at"):
            seen.append(datetime.fromtimestamp(int(req["requested_at"]), tz=timezone.utc))
        sp = spend.get(uid) or {}
        out.append({
            "user_id": uid,
            "name": name,
            "username": str(username or "").lstrip("@"),
            "status": access_status(int(uid), username),
            "orgs": orgs.get(uid, []),
            "last_seen": max(seen) if seen else None,
            "requested_at": req.get("requested_at"),
            "n_calls": int(sp.get("n_calls") or 0),
            "cost_rub": float(sp.get("cost_usd") or 0) * rate,
        })
    order = {"pending": 0}
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    out.sort(key=lambda u: (order.get(u["status"], 1), -(u["last_seen"] or epoch).timestamp()))
    return out


def unknown_spend(from_d: str, to_d: str) -> float:
    """Расход без пользователя (фоновые задачи и т. п.), ₽."""
    for r in usage_store.users_summary(from_d, to_d):
        if str(r["telegram_user_id"]) == "__unknown__":
            return float(r.get("cost_usd") or 0) * usd_to_rub()
    return 0.0


def overview() -> dict[str, Any]:
    from assistant.stores import orgs_store

    rate = usd_to_rub()
    f30, t30 = period(30)
    prev_to = (date.fromisoformat(f30) - timedelta(days=1)).isoformat()
    prev_from = (date.fromisoformat(f30) - timedelta(days=30)).isoformat()
    users = users_directory(f30, t30)
    now = datetime.now(timezone.utc)
    active7 = sum(1 for u in users if u["last_seen"] and now - u["last_seen"] <= timedelta(days=7))
    active30 = sum(1 for u in users if u["last_seen"] and now - u["last_seen"] <= timedelta(days=30))
    cur = usage_store.period_totals(f30, t30)
    prev = usage_store.period_totals(prev_from, prev_to)
    days = {r["date_utc"]: r for r in usage_store.daily_totals(f30, t30)}
    series = []
    d = date.fromisoformat(f30)
    while d.isoformat() <= t30:
        r = days.get(d.isoformat()) or {}
        series.append({"date": d.isoformat(), "rub": float(r.get("cost_usd") or 0) * rate,
                       "calls": int(r.get("n_calls") or 0), "users": int(r.get("n_users") or 0)})
        d += timedelta(days=1)
    ops = usage_store.summary_by_operation(f30, t30)[:6]
    top_users = sorted((u for u in users if u["cost_rub"] > 0), key=lambda u: -u["cost_rub"])[:6]
    return {
        "users_total": len(users),
        "active7": active7,
        "active30": active30,
        "pending": [u for u in users if u["status"] == "pending"],
        "orgs": len(orgs_store.all_org_ids()),
        "spend_rub": float(cur.get("cost_usd") or 0) * rate,
        "spend_prev_rub": float(prev.get("cost_usd") or 0) * rate,
        "calls": int(cur.get("n_calls") or 0),
        "series": series,
        "ops": [{"op": o["operation"], "label": usage_store.operation_label_ru(o["operation"]),
                 "rub": float(o.get("cost_usd") or 0) * rate, "calls": int(o.get("n_calls") or 0)} for o in ops],
        "top_users": top_users,
        "unknown_rub": unknown_spend(f30, t30),
    }


def spend(from_d: str, to_d: str, operation: str | None = None) -> dict[str, Any]:
    rate = usd_to_rub()
    days_rows = {r["date_utc"]: r for r in usage_store.daily_totals(from_d, to_d)}
    series = []
    d = date.fromisoformat(from_d)
    while d.isoformat() <= to_d:
        r = days_rows.get(d.isoformat()) or {}
        series.append({"date": d.isoformat(), "rub": float(r.get("cost_usd") or 0) * rate,
                       "calls": int(r.get("n_calls") or 0), "users": int(r.get("n_users") or 0),
                       "tokens": int(r.get("total_tokens") or 0)})
        d += timedelta(days=1)
    totals = usage_store.period_totals(from_d, to_d, operation=operation)
    ops = [{"op": o["operation"], "label": usage_store.operation_label_ru(o["operation"]),
            "rub": float(o.get("cost_usd") or 0) * rate, "calls": int(o.get("n_calls") or 0),
            "tokens": int(o.get("total_tokens") or 0), "cached": int(o.get("cached_tokens") or 0)}
           for o in usage_store.summary_by_operation(from_d, to_d)]
    return {
        "series": series,
        "rub": float(totals.get("cost_usd") or 0) * rate,
        "usd": float(totals.get("cost_usd") or 0),
        "calls": int(totals.get("n_calls") or 0),
        "tokens": int(totals.get("total_tokens") or 0),
        "cached": int(totals.get("cached_tokens") or 0),
        "ops": ops,
        "rate": rate,
    }


def user_card(uid: str, from_d: str, to_d: str) -> dict[str, Any] | None:
    from assistant.stores import orgs_store

    row = next((u for u in users_directory(from_d, to_d) if u["user_id"] == uid), None)
    if not row:
        return None
    rate = usd_to_rub()
    ops = [{"op": o["operation"], "label": usage_store.operation_label_ru(o["operation"]),
            "rub": float(o.get("cost_usd") or 0) * rate, "calls": int(o.get("n_calls") or 0)}
           for o in usage_store.user_summary_by_operation(uid, from_d, to_d)]
    days = [{"date": r["date_utc"], "rub": float(r.get("cost_usd") or 0) * rate, "calls": int(r.get("n_calls") or 0)}
            for r in usage_store.user_daily_summary(uid, from_d, to_d)]
    life = next((r for r in usage_store.users_summary("2000-01-01", today_utc().isoformat())
                 if str(r["telegram_user_id"]) == uid), {})
    return {**row, "ops": ops, "days": days, "orgs_full": orgs_store.orgs_of_user(uid),
            "lifetime_calls": int(life.get("n_calls") or 0),
            "lifetime_rub": float(life.get("cost_usd") or 0) * rate}


def label_targets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """«user:123» / «org:5» в журнале → подпись и ссылка."""
    from assistant.stores import orgs_store

    profiles = _profiles()
    org_names: dict[int, str] = {}
    for it in items:
        kind, _, ref = str(it.get("target") or "").partition(":")
        if kind == "user" and ref.isdigit():
            prof = profiles.get(ref)
            name = display_name(prof, fallback="") if prof else ""
            it["target_label"] = name if name and name != "Участник" else "ID " + ref
            it["target_path"] = "/user/" + ref
        elif kind == "org" and ref.isdigit():
            oid = int(ref)
            if oid not in org_names:
                org_names[oid] = (orgs_store.get_org(oid) or {}).get("name") or f"Организация {oid} (удалена)"
            it["target_label"] = org_names[oid]
            it["target_path"] = "/org/" + ref
    return items
