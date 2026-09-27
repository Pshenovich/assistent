"""Персональные настройки агентов обсуждения: оверлеи builtin + свои чат-агенты."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

BUILTIN_IDS = ("paie", "research", "gpt")
BUILTIN_TITLES = {"paie": "PAIE", "research": "Research", "gpt": "GPT"}
CUSTOM_KIND = "custom"
MAX_CUSTOM = 20
MAX_TITLE = 60
MAX_PROMPT = 12000
MAX_RULES = 4000
AGENT_PREFIX = "__agent__:"
AGENT_AUTHOR_USERNAME = "agent"
DEFAULT_GPT_TEMP = 0.1
DEFAULT_RESEARCH_TEMP = 0.2


class AgentError(ValueError):
    pass


class AgentForbidden(AgentError):
    pass


class AgentNotFound(AgentError):
    pass


def _db_path() -> Path:
    raw = (os.getenv("USER_AGENTS_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "data" / "user_agents.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_agents (
            id TEXT NOT NULL,
            owner_telegram_user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            rules TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            web_search INTEGER NOT NULL DEFAULT 0,
            temperature REAL,
            extra_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_telegram_user_id, id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_agents_owner
            ON user_agents(owner_telegram_user_id, kind);
        """
    )
    _CONN.commit()
    return _CONN


def reset_connection() -> None:
    global _CONN
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
    _CONN = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clip(text: str, limit: int) -> str:
    return str(text or "")[: int(limit)]


def builtin_prompt(kind: str) -> str:
    kind = str(kind or "").strip().lower()
    if kind == "gpt":
        from assistant.nlu.prompts import ASK_SYSTEM

        return ASK_SYSTEM.strip()
    if kind == "research":
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "board" / "prompts" / "RESEARCH.md"
        return path.read_text(encoding="utf-8").strip()
    if kind == "paie":
        from assistant.board.context import load_prompt

        return load_prompt("CHAIR")
    return ""


def builtin_model(kind: str) -> str:
    kind = str(kind or "").strip().lower()
    if kind == "gpt":
        return (
            os.getenv("OPENROUTER_MODEL_ASK", "").strip()
            or os.getenv("OPENROUTER_MODEL_CHAT", "").strip()
            or os.getenv("OPENROUTER_MODEL", "").strip()
            or "openai/gpt-4.1"
        )
    if kind == "research":
        return os.getenv("OPENROUTER_MODEL", "").strip() or "openai/gpt-4o-mini"
    if kind == "paie":
        from assistant.board.llm import board_model

        return board_model()
    return os.getenv("OPENROUTER_MODEL_ASK", "").strip() or "openai/gpt-4.1"


def builtin_temperature(kind: str) -> float | None:
    kind = str(kind or "").strip().lower()
    if kind == "gpt" or kind == CUSTOM_KIND:
        return DEFAULT_GPT_TEMP
    if kind == "research":
        return DEFAULT_RESEARCH_TEMP
    return None


def builtin_max_rounds() -> int:
    from assistant.board.orchestrator import max_rounds

    return max_rounds()


def allowed_model_ids() -> set[str]:
    from assistant.integrations.openrouter_client import GPT_PICKER_MODELS

    return {mid for mid, _name in GPT_PICKER_MODELS}


def sanitize_model(raw: str | None) -> str:
    from assistant.integrations.openrouter_client import sanitize_openrouter_model_id

    mid = sanitize_openrouter_model_id(raw) or ""
    if not mid:
        return ""
    allowed = allowed_model_ids()
    if allowed and mid not in allowed:
        raise AgentError("Модель недоступна в списке")
    return mid


def _extra_of(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def comment_prefix(agent_id: str) -> str:
    aid = str(agent_id or "").strip()
    if aid in BUILTIN_IDS or not aid:
        return ""
    return AGENT_PREFIX + aid


def parse_agent_id_from_prefix(prefix: str) -> str:
    raw = str(prefix or "").strip()
    if raw.startswith(AGENT_PREFIX):
        return raw[len(AGENT_PREFIX) :].strip()
    return ""


def is_agent_prefix(prefix: str) -> bool:
    return bool(parse_agent_id_from_prefix(prefix))


def compose_system(prompt: str, rules: str) -> str:
    base = (prompt or "").strip()
    extra = (rules or "").strip()
    if base and extra:
        return f"{base}\n\nПравила пользователя:\n{extra}"
    return base or extra


def factory_agent(kind: str) -> dict[str, Any]:
    kind = str(kind or "").strip().lower()
    if kind not in BUILTIN_IDS:
        raise AgentError("Неизвестный встроенный агент")
    prompt = builtin_prompt(kind)
    model = builtin_model(kind)
    temp = builtin_temperature(kind)
    max_r = builtin_max_rounds() if kind == "paie" else None
    return {
        "id": kind,
        "kind": kind,
        "title": BUILTIN_TITLES[kind],
        "builtin": True,
        "prompt": prompt,
        "prompt_default": prompt,
        "rules": "",
        "model": model,
        "model_default": model,
        "web_search": kind == "research",
        "temperature": temp,
        "temperature_default": temp,
        "max_rounds": max_r,
        "max_rounds_default": max_r,
        "can_delete": False,
        "can_rename": False,
    }


def _row_overlay(row: sqlite3.Row) -> dict[str, Any]:
    extra = _extra_of(row["extra_json"])
    temp = row["temperature"]
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"] or ""),
        "title": str(row["title"] or ""),
        "prompt": str(row["system_prompt"] or ""),
        "rules": str(row["rules"] or ""),
        "model": str(row["model"] or ""),
        "web_search": bool(row["web_search"]),
        "temperature": float(temp) if temp is not None else None,
        "max_rounds": extra.get("max_rounds"),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _merge_builtin(kind: str, overlay: dict[str, Any] | None) -> dict[str, Any]:
    base = factory_agent(kind)
    if not overlay:
        return base
    prompt = str(overlay.get("prompt") or "").strip()
    model = str(overlay.get("model") or "").strip()
    rules = str(overlay.get("rules") or "").strip()
    if prompt:
        base["prompt"] = prompt
    if model:
        base["model"] = model
    base["rules"] = rules
    if overlay.get("temperature") is not None and kind != "paie":
        base["temperature"] = float(overlay["temperature"])
    if kind in ("gpt", CUSTOM_KIND):
        if "web_search" in overlay:
            base["web_search"] = bool(overlay.get("web_search"))
    if kind == "paie":
        raw_rounds = overlay.get("max_rounds")
        try:
            if raw_rounds is not None:
                base["max_rounds"] = max(1, min(8, int(raw_rounds)))
        except (TypeError, ValueError):
            pass
        base["web_search"] = False
    if kind == "research":
        base["web_search"] = True
    return base


def _custom_public(overlay: dict[str, Any]) -> dict[str, Any]:
    model_default = builtin_model("gpt")
    temp_default = DEFAULT_GPT_TEMP
    model = str(overlay.get("model") or "").strip() or model_default
    temp = overlay.get("temperature")
    if temp is None:
        temp = temp_default
    return {
        "id": str(overlay["id"]),
        "kind": CUSTOM_KIND,
        "title": str(overlay.get("title") or "Агент").strip() or "Агент",
        "builtin": False,
        "prompt": str(overlay.get("prompt") or ""),
        "prompt_default": "",
        "rules": str(overlay.get("rules") or ""),
        "model": model,
        "model_default": model_default,
        "web_search": bool(overlay.get("web_search")),
        "temperature": float(temp),
        "temperature_default": temp_default,
        "max_rounds": None,
        "max_rounds_default": None,
        "can_delete": True,
        "can_rename": True,
        "created_at": overlay.get("created_at") or "",
        "updated_at": overlay.get("updated_at") or "",
    }


def _get_row(user_id: int, agent_id: str) -> sqlite3.Row | None:
    conn = _conn()
    return conn.execute(
        "SELECT * FROM user_agents WHERE owner_telegram_user_id=? AND id=?",
        (int(user_id), str(agent_id)),
    ).fetchone()


def list_agents(user_id: int) -> list[dict[str, Any]]:
    uid = int(user_id)
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM user_agents WHERE owner_telegram_user_id=? ORDER BY created_at ASC",
            (uid,),
        ).fetchall()
    by_id = {str(row["id"]): _row_overlay(row) for row in rows}
    out = [_merge_builtin(kind, by_id.get(kind)) for kind in BUILTIN_IDS]
    for row in rows:
        if str(row["kind"]) == CUSTOM_KIND and str(row["id"]) not in BUILTIN_IDS:
            out.append(_custom_public(_row_overlay(row)))
    return out


def get_agent(user_id: int, agent_id: str) -> dict[str, Any] | None:
    aid = str(agent_id or "").strip()
    if not aid:
        return None
    uid = int(user_id)
    with _LOCK:
        row = _get_row(uid, aid)
        overlay = _row_overlay(row) if row else None
    if aid in BUILTIN_IDS:
        return _merge_builtin(aid, overlay)
    if overlay and overlay.get("kind") == CUSTOM_KIND:
        return _custom_public(overlay)
    return None


def _normalize_temp(raw: Any, *, required: bool = False) -> float | None:
    if raw is None or raw == "":
        if required:
            raise AgentError("Укажите температуру")
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise AgentError("Температура должна быть числом") from exc
    if val < 0 or val > 2:
        raise AgentError("Температура от 0 до 2")
    return val


def _normalize_rounds(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError) as exc:
        raise AgentError("Число раундов должно быть целым") from exc
    return max(1, min(8, val))


def _write_row(
    user_id: int,
    agent_id: str,
    *,
    kind: str,
    title: str,
    prompt: str,
    rules: str,
    model: str,
    web_search: bool,
    temperature: float | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    now = _now_iso()
    uid = int(user_id)
    with _LOCK:
        conn = _conn()
        existing = _get_row(uid, agent_id)
        created = str(existing["created_at"]) if existing else now
        conn.execute(
            """
            INSERT INTO user_agents (
                id, owner_telegram_user_id, kind, title, system_prompt, rules,
                model, web_search, temperature, extra_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_telegram_user_id, id) DO UPDATE SET
                kind=excluded.kind,
                title=excluded.title,
                system_prompt=excluded.system_prompt,
                rules=excluded.rules,
                model=excluded.model,
                web_search=excluded.web_search,
                temperature=excluded.temperature,
                extra_json=excluded.extra_json,
                updated_at=excluded.updated_at
            """,
            (
                agent_id,
                uid,
                kind,
                title,
                prompt,
                rules,
                model,
                1 if web_search else 0,
                temperature,
                json.dumps(extra, ensure_ascii=False),
                created,
                now,
            ),
        )
        conn.commit()
    got = get_agent(uid, agent_id)
    if not got:
        raise AgentError("Не удалось сохранить агента")
    return got


def patch_agent(user_id: int, agent_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    aid = str(agent_id or "").strip()
    current = get_agent(user_id, aid)
    if not current:
        raise AgentNotFound("Агент не найден")
    kind = str(current["kind"])
    title = current["title"]
    if current.get("can_rename") and "title" in fields:
        title = _clip(str(fields.get("title") or "").strip(), MAX_TITLE)
        if not title:
            raise AgentError("Название не может быть пустым")
    prompt = current["prompt"]
    if "prompt" in fields:
        prompt = _clip(str(fields.get("prompt") or ""), MAX_PROMPT)
        if kind in BUILTIN_IDS and not prompt.strip():
            prompt = ""
    rules = current["rules"]
    if "rules" in fields:
        rules = _clip(str(fields.get("rules") or ""), MAX_RULES)
    model = current["model"] if current["model"] != current.get("model_default") else ""
    if "model" in fields:
        raw_model = str(fields.get("model") or "").strip()
        model = sanitize_model(raw_model) if raw_model else ""
        if kind == CUSTOM_KIND and not model:
            raise AgentError("Укажите модель")
    web_search = bool(current.get("web_search"))
    if kind in ("gpt", CUSTOM_KIND) and "web_search" in fields:
        web_search = bool(fields.get("web_search"))
    if kind == "research":
        web_search = True
    if kind == "paie":
        web_search = False
    temperature = current.get("temperature")
    if kind == "paie":
        temperature = None
    elif "temperature" in fields:
        temperature = _normalize_temp(fields.get("temperature"))
        if temperature is None and kind == CUSTOM_KIND:
            temperature = DEFAULT_GPT_TEMP
    extra: dict[str, Any] = {}
    if kind == "paie":
        rounds = current.get("max_rounds")
        if "max_rounds" in fields:
            rounds = _normalize_rounds(fields.get("max_rounds"))
        if rounds is not None:
            extra["max_rounds"] = int(rounds)
    stored_prompt = prompt
    if kind in BUILTIN_IDS and prompt.strip() == builtin_prompt(kind):
        stored_prompt = ""
    stored_model = model
    if kind in BUILTIN_IDS and model == builtin_model(kind):
        stored_model = ""
    stored_temp = temperature
    if kind in BUILTIN_IDS and temperature == builtin_temperature(kind):
        stored_temp = None
    return _write_row(
        user_id,
        aid,
        kind=kind,
        title=title if kind == CUSTOM_KIND else BUILTIN_TITLES[kind],
        prompt=stored_prompt,
        rules=rules,
        model=stored_model,
        web_search=web_search,
        temperature=stored_temp,
        extra=extra,
    )


def create_agent(
    user_id: int,
    *,
    title: str,
    prompt: str = "",
    rules: str = "",
    model: str | None = None,
    web_search: bool = False,
    temperature: float | None = None,
) -> dict[str, Any]:
    uid = int(user_id)
    name = _clip(str(title or "").strip(), MAX_TITLE)
    if not name:
        raise AgentError("Укажите название агента")
    with _LOCK:
        conn = _conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM user_agents WHERE owner_telegram_user_id=? AND kind=?",
            (uid, CUSTOM_KIND),
        ).fetchone()[0]
    if int(count) >= MAX_CUSTOM:
        raise AgentError(f"Можно создать не больше {MAX_CUSTOM} своих агентов")
    mid = sanitize_model(model) if model else builtin_model("gpt")
    if not mid:
        mid = builtin_model("gpt")
    temp = _normalize_temp(temperature)
    if temp is None:
        temp = DEFAULT_GPT_TEMP
    agent_id = str(uuid.uuid4())
    return _write_row(
        uid,
        agent_id,
        kind=CUSTOM_KIND,
        title=name,
        prompt=_clip(prompt, MAX_PROMPT),
        rules=_clip(rules, MAX_RULES),
        model=mid,
        web_search=bool(web_search),
        temperature=temp,
        extra={},
    )


def delete_agent(user_id: int, agent_id: str) -> None:
    aid = str(agent_id or "").strip()
    if aid in BUILTIN_IDS:
        raise AgentForbidden("Встроенного агента нельзя удалить")
    uid = int(user_id)
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM user_agents WHERE owner_telegram_user_id=? AND id=? AND kind=?",
            (uid, aid, CUSTOM_KIND),
        )
        conn.commit()
        if cur.rowcount < 1:
            raise AgentNotFound("Агент не найден")


def reset_agent(user_id: int, agent_id: str) -> dict[str, Any]:
    aid = str(agent_id or "").strip()
    if aid not in BUILTIN_IDS:
        raise AgentForbidden("Сбросить можно только встроенного агента")
    uid = int(user_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "DELETE FROM user_agents WHERE owner_telegram_user_id=? AND id=?",
            (uid, aid),
        )
        conn.commit()
    return factory_agent(aid)


def runtime_config(user_id: int, agent_id: str | None) -> dict[str, Any]:
    """Эффективные настройки для запуска. agent_id пустой = gpt."""
    aid = str(agent_id or "").strip() or "gpt"
    agent = get_agent(user_id, aid)
    if not agent:
        raise AgentNotFound("Агент не найден")
    prompt = str(agent.get("prompt") or "").strip() or (
        builtin_prompt(agent["kind"]) if agent["kind"] in BUILTIN_IDS else ""
    )
    return {
        **agent,
        "system": compose_system(prompt, str(agent.get("rules") or "")),
        "prefix": comment_prefix(agent["id"]) if agent["kind"] == CUSTOM_KIND else "",
    }
