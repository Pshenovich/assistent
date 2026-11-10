"""Модели ИИ, которые видят пользователи, и уровни усилия (reasoning effort) у каждой.

Список ведёт оператор в админке (/dashboard/models): выбирает модели из каталога OpenRouter,
даёт название, отмечает доступные уровни усилия и уровень по умолчанию. Пользователь в переключателе
выбирает уровень из разрешённых; руководитель организации может ограничить сотруднику потолок.

Пока оператор ничего не менял, список заполняется прежним набором из кода (seed_rows).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable

# Уровни OpenRouter reasoning.effort — от слабого к сильному.
EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_LABELS: dict[str, str] = {
    "none": "Без рассуждения",
    "minimal": "Минимально",
    "low": "Быстро",
    "medium": "Обычно",
    "high": "Тщательно",
    "xhigh": "Очень тщательно",
    "max": "Максимум",
}
# Доля max_tokens, которую OpenRouter отдаёт на рассуждение (для Claude — budget_tokens).
EFFORT_RATIO: dict[str, float] = {
    "none": 0.0, "minimal": 0.1, "low": 0.2, "medium": 0.5, "high": 0.8, "xhigh": 0.95, "max": 0.95,
}
# Уровни прежних «думающих» моделей: до админки все шли с medium.
SEED_EFFORTS: tuple[str, ...] = ("low", "medium", "high")
SEED_DEFAULT_EFFORT = "medium"

_LOCK = threading.Lock()
_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CACHE_TTL = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _conn():
    from assistant.stores import notes as notes_store

    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_models (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            efforts TEXT NOT NULL DEFAULT '[]',
            default_effort TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS llm_models_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def _notes_lock():
    from assistant.stores import notes as notes_store

    return notes_store._LOCK  # type: ignore[attr-defined]


def reset_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def normalize_efforts(raw: Iterable[Any] | None) -> list[str]:
    got = {str(x or "").strip().lower() for x in (raw or [])}
    return [e for e in EFFORTS if e in got]


def seed_rows() -> list[dict[str, Any]]:
    """Прежний набор из кода: GPT без уровней, «думающие» — быстро/обычно/тщательно."""
    from assistant.integrations.openrouter_client import GPT_PICKER_MODELS, PAIE_REASONING_MODELS

    rows: list[dict[str, Any]] = []
    for mid, label in GPT_PICKER_MODELS:
        rows.append({"id": mid, "label": label, "enabled": True, "efforts": [], "default_effort": ""})
    for mid, label in PAIE_REASONING_MODELS:
        rows.append({"id": mid, "label": label, "enabled": True, "efforts": list(SEED_EFFORTS),
                     "default_effort": SEED_DEFAULT_EFFORT})
    for i, r in enumerate(rows):
        r["position"] = i
    return rows


def _ensure_seeded(conn) -> None:
    if conn.execute("SELECT 1 FROM llm_models_meta WHERE key = 'seeded'").fetchone():
        return
    ts = _now()
    for r in seed_rows():
        conn.execute(
            "INSERT OR IGNORE INTO llm_models (id, label, enabled, efforts, default_effort, position, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'seed')",
            (r["id"], r["label"], 1, json.dumps(r["efforts"]), r["default_effort"], r["position"], ts),
        )
    conn.execute("INSERT OR REPLACE INTO llm_models_meta (key, value) VALUES ('seeded', ?)", (ts,))
    conn.commit()


def _row(r: Any) -> dict[str, Any]:
    try:
        efforts = normalize_efforts(json.loads(r["efforts"] or "[]"))
    except ValueError:
        efforts = []
    default = str(r["default_effort"] or "")
    if efforts and default not in efforts:
        default = efforts[min(len(efforts) - 1, len(efforts) // 2)]
    return {
        "id": str(r["id"]),
        "label": str(r["label"]),
        "enabled": bool(r["enabled"]),
        "efforts": efforts,
        "default_effort": default if efforts else "",
        "position": int(r["position"] or 0),
        "updated_at": str(r["updated_at"] or ""),
        "updated_by": str(r["updated_by"] or ""),
    }


def list_models(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    """Модели в порядке показа. Кэш на несколько секунд: вызывается на каждый запрос к ИИ."""
    global _CACHE
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE
    if cached is None or now - cached[0] > _CACHE_TTL:
        with _notes_lock():
            conn = _conn()
            _ensure_seeded(conn)
            rows = [_row(r) for r in conn.execute("SELECT * FROM llm_models ORDER BY position, label")]
        with _LOCK:
            _CACHE = (now, rows)
    else:
        rows = cached[1]
    out = [dict(r, efforts=list(r["efforts"])) for r in rows]
    return [r for r in out if r["enabled"]] if enabled_only else out


def get(model_id: str | None) -> dict[str, Any] | None:
    mid = str(model_id or "").strip()
    if not mid:
        return None
    return next((r for r in list_models() if r["id"] == mid), None)


def enabled_ids() -> set[str]:
    return {r["id"] for r in list_models(enabled_only=True)}


def labels() -> dict[str, str]:
    return {r["id"]: r["label"] for r in list_models()}


class ModelError(ValueError):
    pass


def save(
    model_id: str,
    *,
    label: str,
    enabled: bool,
    efforts: Iterable[Any],
    default_effort: str = "",
    by: str = "",
) -> dict[str, Any]:
    """Добавить или изменить модель (оператор админки)."""
    from assistant.integrations.openrouter_client import sanitize_openrouter_model_id
    from assistant.nlu.auto_model import is_auto

    mid = sanitize_openrouter_model_id(model_id)
    if not mid or is_auto(mid):
        raise ModelError("Неверный id модели")
    name = " ".join(str(label or "").split())[:60] or mid.split("/")[-1]
    levels = normalize_efforts(efforts)
    default = str(default_effort or "").strip().lower()
    if levels and default not in levels:
        default = SEED_DEFAULT_EFFORT if SEED_DEFAULT_EFFORT in levels else levels[0]
    if not levels:
        default = ""
    with _notes_lock():
        conn = _conn()
        _ensure_seeded(conn)
        cur = conn.execute("SELECT position FROM llm_models WHERE id = ?", (mid,)).fetchone()
        if cur is None:
            pos = int(conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM llm_models").fetchone()[0])
        else:
            pos = int(cur["position"])
        conn.execute(
            "INSERT INTO llm_models (id, label, enabled, efforts, default_effort, position, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET label = excluded.label, "
            "enabled = excluded.enabled, efforts = excluded.efforts, default_effort = excluded.default_effort, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (mid, name, 1 if enabled else 0, json.dumps(levels), default, pos, _now(), str(by or "")),
        )
        conn.commit()
    reset_cache()
    out = get(mid)
    assert out is not None
    return out


def remove(model_id: str) -> bool:
    with _notes_lock():
        conn = _conn()
        _ensure_seeded(conn)
        n = conn.execute("DELETE FROM llm_models WHERE id = ?", (str(model_id or ""),)).rowcount
        conn.commit()
    reset_cache()
    return bool(n)


def move(model_id: str, delta: int) -> None:
    """Сдвинуть модель в списке на delta позиций (−1 — выше)."""
    rows = list_models()
    ids = [r["id"] for r in rows]
    if model_id not in ids:
        raise ModelError("Модели нет в списке")
    i = ids.index(model_id)
    j = max(0, min(len(ids) - 1, i + int(delta)))
    ids.insert(j, ids.pop(i))
    with _notes_lock():
        conn = _conn()
        for pos, mid in enumerate(ids):
            conn.execute("UPDATE llm_models SET position = ? WHERE id = ?", (pos, mid))
        conn.commit()
    reset_cache()


# ── уровень усилия ───────────────────────────────────────────────────────────

def _rank(level: str) -> int:
    return EFFORTS.index(level) if level in EFFORTS else -1


def clamp(level: str | None, allowed: list[str], *, cap: str | None = None) -> str | None:
    """Уровень из allowed: выбранный, иначе ближайший ниже; потолок cap понижает до разрешённого."""
    if not allowed:
        return None
    pool = allowed
    if cap and cap in EFFORTS:
        under = [e for e in allowed if _rank(e) <= _rank(cap)]
        pool = under or [allowed[0]]  # даже самый низкий выше потолка — даём самый низкий
    lv = str(level or "").strip().lower()
    if lv in pool:
        return lv
    if lv in EFFORTS:
        lower = [e for e in pool if _rank(e) <= _rank(lv)]
        if lower:
            return lower[-1]
        return pool[0]
    return None


def effort_for(model_id: str | None, user_id: int | str | None = None, *, requested: str | None = None) -> str | None:
    """Уровень для запроса: выбор пользователя (или requested) → по умолчанию модели → потолок организации.

    None — модели нет в списке или у неё нет уровней (reasoning в запрос не кладём).
    """
    entry = get(model_id)
    if not entry or not entry["efforts"]:
        return None
    allowed = entry["efforts"]
    want = requested
    if want is None and user_id not in (None, ""):
        want = user_effort(user_id, entry["id"])
    cap = org_effort_cap(user_id) if user_id not in (None, "") else None
    return clamp(want or entry["default_effort"], allowed, cap=cap) or clamp(entry["default_effort"], allowed, cap=cap)


def efforts_for_user(model_id: str | None, user_id: int | str | None) -> list[str]:
    """Уровни, которые пользователь может выбрать у модели (с учётом потолка организации)."""
    entry = get(model_id)
    if not entry or not entry["efforts"]:
        return []
    cap = org_effort_cap(user_id) if user_id not in (None, "") else None
    if not cap:
        return list(entry["efforts"])
    under = [e for e in entry["efforts"] if _rank(e) <= _rank(cap)]
    return under or entry["efforts"][:1]


USER_EFFORT_KEY = "llm_effort_by_model"


def user_efforts(user_id: int | str) -> dict[str, str]:
    from assistant.stores import user_prefs

    try:
        raw = user_prefs.load_prefs(int(user_id)).get(USER_EFFORT_KEY)
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(v) in EFFORTS}


def user_effort(user_id: int | str, model_id: str) -> str | None:
    return user_efforts(user_id).get(str(model_id or ""))


def set_user_effort(user_id: int | str, model_id: str, level: str | None) -> str | None:
    """Запомнить выбор пользователя для модели. Возвращает уровень, который реально будет работать."""
    from assistant.stores import user_prefs

    entry = get(model_id)
    if not entry or not entry["efforts"]:
        raise ModelError("У этой модели уровень не выбирается")
    lv = str(level or "").strip().lower()
    if lv not in efforts_for_user(entry["id"], user_id):
        raise ModelError("Этот уровень недоступен")
    uid = int(user_id)
    prefs = user_prefs.load_prefs(uid)
    cur = prefs.get(USER_EFFORT_KEY)
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur[entry["id"]] = lv
    prefs[USER_EFFORT_KEY] = cur
    user_prefs.save_prefs(uid, prefs)
    return effort_for(entry["id"], uid)


def org_effort_cap(user_id: int | str | None) -> str | None:
    """Потолок уровня усилия, который руководитель организации задал сотруднику (None — без ограничения)."""
    if user_id in (None, ""):
        return None
    try:
        from assistant.lib import billing_store
        from assistant.stores import credits_store

        org_id = billing_store.org_id_for_user(user_id)
        if org_id is None:
            return None
        cap = credits_store.member_settings(org_id, user_id).get("max_effort")
    except Exception as e:  # ограничение не должно ронять ответ
        print(f"[llm_models] org cap failed uid={user_id} err={e!r}")
        return None
    return cap if cap in EFFORTS else None


def picker_fields(model_id: str, user_id: int | str | None) -> dict[str, Any]:
    """Поля строки переключателя: доступные уровни, подписи и текущий уровень пользователя."""
    levels = efforts_for_user(model_id, user_id)
    if not levels:
        return {"efforts": [], "effort": None}
    return {
        "efforts": [{"id": e, "label": EFFORT_LABELS[e]} for e in levels],
        "effort": effort_for(model_id, user_id),
    }


def reasoning_max_tokens(level: str, answer_tokens: int | None) -> int:
    """Потолок max_tokens так, чтобы после рассуждения осталось место на ответ.

    OpenRouter отдаёт на рассуждение долю max_tokens (EFFORT_RATIO); ответу нужно answer_tokens.
    """
    ratio = EFFORT_RATIO.get(level, 0.5)
    answer = int(answer_tokens or 0) or 4000
    floor = 8000 if ratio <= 0.5 else 16000
    need = int(answer / max(0.05, 1.0 - ratio)) if ratio < 1 else answer * 20
    return min(64000, max(floor, need, answer * 2 if ratio > 0 else answer))
