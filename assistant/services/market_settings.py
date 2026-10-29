"""Настройки выпуска «Рынок» организации: о компании, конкуренты, темы, свои источники, время.

Хранятся в orgs_store (раздел ``market``). Присылать ли выпуск в Telegram — личная настройка
участника (user_prefs ``market_telegram``), не организации.
"""

from __future__ import annotations

import re
from typing import Any

SECTION = "market"
MAX_ITEMS = 30
MAX_SOURCES = 20
DEFAULTS: dict[str, Any] = {
    "about": "",
    "competitors": [],
    "topics": [],
    "sources": [],
    "time": "07:20",
}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]{2,500}$", re.I)


def _list(raw: Any, *, limit: int = MAX_ITEMS, size: int = 80) -> list[str]:
    """Список из массива или строки «через запятую / с новой строки», без повторов."""
    if isinstance(raw, str):
        raw = re.split(r"[,\n;]", raw)
    out: list[str] = []
    for x in raw or []:
        t = re.sub(r"\s+", " ", str(x or "")).strip()[:size]
        if t and t.lower() not in {o.lower() for o in out}:
            out.append(t)
    return out[:limit]


def normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Проверить и привести настройки; неверное — ValueError с понятным текстом."""
    src = dict(raw or {})
    out = dict(DEFAULTS)
    out["about"] = str(src.get("about") or "").strip()[:1500]
    out["competitors"] = _list(src.get("competitors"))
    out["topics"] = _list(src.get("topics"))
    sources = _list(src.get("sources"), limit=MAX_SOURCES, size=500)
    bad = [s for s in sources if not _URL_RE.match(s)]
    if bad:
        raise ValueError("Источник должен быть ссылкой http(s)://…: " + bad[0])
    out["sources"] = sources
    t = str(src.get("time") or DEFAULTS["time"]).strip()
    if not _TIME_RE.match(t):
        raise ValueError("Время выпуска — в формате ЧЧ:ММ, например 07:30")
    out["time"] = t
    return out


def get(org_id: int) -> dict[str, Any]:
    from assistant.stores import orgs_store

    saved = orgs_store.get_settings(org_id).get(SECTION) or {}
    try:
        return normalize(saved)
    except ValueError:
        return dict(DEFAULTS)


def save(actor_id: int, org_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    """Сохранить изменённые поля (остальные — как были). Права — у админа и владельца."""
    from assistant.stores import orgs_store

    merged = {**get(org_id), **{k: v for k, v in (patch or {}).items() if k in DEFAULTS}}
    value = normalize(merged)
    orgs_store.set_settings(actor_id, org_id, SECTION, value)
    return value


def telegram_enabled(user_id: int) -> bool:
    from assistant.stores import user_prefs

    return bool(user_prefs.load_prefs(int(user_id)).get("market_telegram"))


def set_telegram(user_id: int, enabled: bool) -> bool:
    from assistant.stores import user_prefs

    prefs = user_prefs.load_prefs(int(user_id))
    prefs["market_telegram"] = bool(enabled)
    user_prefs.save_prefs(int(user_id), prefs)
    return bool(enabled)


def telegram_user_ids() -> list[int]:
    """Кто включил «Присылать мне выпуск в Telegram»."""
    from assistant.stores import user_prefs

    out = []
    for p in user_prefs._dir().glob("*.json"):  # type: ignore[attr-defined]
        try:
            uid = int(p.stem)
        except ValueError:
            continue
        if telegram_enabled(uid):
            out.append(uid)
    return out


# ---------------------------------------------------------------- выпуск в Telegram

def issue_digest(html_text: str) -> tuple[str, list[str]]:
    """Дата выпуска (ГГГГ-ММ-ДД) и пункты «Главное для стратегии» из HTML выпуска."""
    import html as html_lib

    m = re.search(r"<title>[^<]*?(\d{2})\.(\d{2})\.(\d{4})", html_text or "")
    day = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""
    items = []
    for raw in re.findall(r'class="strategy-trigger"[^>]*>\s*<span>(.*?)</span>', html_text or "", flags=re.S):
        t = html_lib.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        if t:
            items.append(re.sub(r"\s+", " ", t))
    return day, items[:5]


def due_message(user_id: int, *, now=None, issue_html: str = "") -> str:
    """Текст сообщения, если пора слать выпуск пользователю; иначе ''."""
    import html as html_lib
    from datetime import datetime

    from assistant.stores import orgs_store, user_prefs

    org = orgs_store.current_org(int(user_id))
    if not org:
        return ""
    st = get(org["id"])
    tz = user_prefs.get_user_tz(int(user_id))
    local = (now or datetime.now(tz)).astimezone(tz)
    today = local.date().isoformat()
    if local.strftime("%H:%M") < st["time"]:
        return ""
    prefs = user_prefs.load_prefs(int(user_id))
    if prefs.get("market_telegram_sent") == today:
        return ""
    day, items = issue_digest(issue_html)
    if day != today or not items:
        return ""  # свежего выпуска ещё нет — подождём
    lines = [f"<b>Рынок · {local.strftime('%d.%m')}</b>", ""]
    lines += ["• " + html_lib.escape(t) for t in items[:3]]
    lines += ["", "Подробнее и «что это значит для вас» — в Орбите → Дайджест → Рынок."]
    return "\n".join(lines)


def mark_sent(user_id: int, day: str) -> None:
    from assistant.stores import user_prefs

    prefs = user_prefs.load_prefs(int(user_id))
    prefs["market_telegram_sent"] = day
    user_prefs.save_prefs(int(user_id), prefs)
