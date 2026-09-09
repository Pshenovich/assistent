"""Вызов chat/completions: OpenRouter, при сбое — CometAPI (OpenAI-совместимый). Запись usage в SQLite."""

from __future__ import annotations

import base64
import os
import re
import time
from contextvars import ContextVar
from typing import Any

import requests

from assistant.lib.usage_store import extract_cached_tokens, insert_usage_event


# Telegram user context for usage accounting.
# Заполняется из bot.py на входе в хендлеры.
_current_tg_user_id: ContextVar[str | None] = ContextVar(
    "current_tg_user_id", default=None
)
_current_tg_user_username: ContextVar[str | None] = ContextVar(
    "current_tg_user_username", default=None
)


def set_openrouter_usage_telegram_user(
    *,
    telegram_user_id: int | str | None,
    telegram_username: str | None = None,
) -> None:
    """Устанавливает текущего Telegram-пользователя для записи usage в SQLite."""
    if telegram_user_id is None or str(telegram_user_id).strip() == "":
        _current_tg_user_id.set(None)
        _current_tg_user_username.set(None)
        return
    _current_tg_user_id.set(str(telegram_user_id).strip())
    _current_tg_user_username.set(
        (telegram_username or "").strip() or None  # username может отсутствовать
    )


def get_openrouter_usage_telegram_user() -> tuple[str | None, str | None]:
    """Текущий Telegram-пользователь для записи usage (в т.ч. из worker-thread после copy_context)."""
    return _current_tg_user_id.get(), _current_tg_user_username.get()


def is_llm_configured() -> bool:
    """Есть ли ключ для LLM: OpenRouter и/или Comet (основной или только фолбэк)."""
    return bool(os.getenv("OPENROUTER_KEY", "").strip() or os.getenv("COMET_API_KEY", "").strip())


def _openrouter_chat_url() -> str:
    return os.getenv(
        "OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"
    ).strip()


_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.+:/-]{0,127}$")
_MODELS_SKIP = ("embed", "whisper", "tts-", "-tts", "moderation", "rerank")
_MODELS_CACHE: tuple[float, dict[str, Any]] | None = None
_MODELS_TTL_SEC = 600.0

# Короткий список для свитчера GPT в заметке (не весь каталог OpenRouter).
GPT_PICKER_MODELS: tuple[tuple[str, str], ...] = (
    ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
    ("openai/gpt-5.5", "GPT-5.5"),
    ("openai/gpt-5.4", "GPT-5.4"),
    ("openai/gpt-5", "GPT-5"),
    ("openai/gpt-4.1", "GPT-4.1"),
    ("openai/gpt-4o", "GPT-4o"),
    ("openai/gpt-4o-mini", "GPT-4o mini"),
)


def default_openrouter_model() -> str:
    return (
        os.getenv("OPENROUTER_MODEL_ASK", "").strip()
        or os.getenv("OPENROUTER_MODEL", "").strip()
        or os.getenv("OPENROUTER_MODEL_BOARD", "").strip()
        or "openai/gpt-4o-mini"
    )


def sanitize_openrouter_model_id(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s or not _MODEL_ID_RE.fullmatch(s):
        return None
    return s


def _openrouter_models_url() -> str:
    chat = _openrouter_chat_url().rstrip("/")
    if chat.endswith("/chat/completions"):
        return chat[: -len("/chat/completions")] + "/models"
    return "https://openrouter.ai/api/v1/models"


def _model_short_name(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    if ": " in name:
        name = name.split(": ", 1)[1].strip()
    if name:
        return name
    mid = str(item.get("id") or "").strip()
    return mid.split("/")[-1] if mid else ""


def _is_text_chat_model(item: dict[str, Any]) -> bool:
    mid = str(item.get("id") or "").lower()
    if not mid or any(token in mid for token in _MODELS_SKIP):
        return False
    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    outputs = arch.get("output_modalities") or arch.get("modality")
    if isinstance(outputs, list):
        return "text" in [str(x).lower() for x in outputs]
    if isinstance(outputs, str):
        return "text" in outputs.lower()
    return True


def normalize_openrouter_models(
    raw_items: list[Any],
    *,
    default: str,
) -> dict[str, Any]:
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if not _is_text_chat_model(item):
            continue
        mid = sanitize_openrouter_model_id(str(item.get("id") or ""))
        if not mid or mid in seen:
            continue
        seen.add(mid)
        models.append({"id": mid, "name": _model_short_name(item) or mid})
    default_id = sanitize_openrouter_model_id(default) or "openai/gpt-4o-mini"
    if default_id not in seen:
        models.insert(0, {"id": default_id, "name": default_id.split("/")[-1]})
        seen.add(default_id)

    popular = ("openai/", "anthropic/", "google/", "x-ai/", "meta-llama/")

    def _sort_key(row: dict[str, str]) -> tuple[int, int, str]:
        mid = row["id"]
        if mid == default_id:
            return (0, 0, row["name"].lower())
        rank = 9
        for i, prefix in enumerate(popular):
            if mid.startswith(prefix):
                rank = i + 1
                break
        return (1, rank, row["name"].lower())

    models.sort(key=_sort_key)
    return {"models": models, "default": default_id}


def _comet_chat_url() -> str:
    """Полный URL chat/completions. Если в .env указали только .../v1 — дописываем путь."""
    default = "https://api.cometapi.com/v1/chat/completions"
    raw = (os.getenv("COMET_API_URL") or "").strip().rstrip("/")
    if not raw:
        return default
    if raw.endswith("/v1"):
        return raw + "/chat/completions"
    if "/chat/completions" in raw:
        return raw
    return default


def _comet_model_for_request(openrouter_model: str | None) -> str:
    """Имя модели для Comet: явный COMET_FALLBACK_MODEL или эвристика по префиксу vendor/."""
    explicit = os.getenv("COMET_FALLBACK_MODEL", "").strip()
    if explicit:
        return explicit
    m = (openrouter_model or "").strip()
    if m.startswith("openai/"):
        return m[7:]
    if "/" in m:
        return m.split("/", 1)[1]
    return m or "gpt-4o-mini"


def _post_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    extra_headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    headers.update(extra_headers)
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Ответ не JSON-объект: {type(data).__name__}")
    return data


def _format_api_error_body(data: dict[str, Any]) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err.get("type") or err)
    if err is not None:
        return str(err)
    if data.get("message"):
        return str(data.get("message"))
    return str(data)[:500]


def _raise_if_bad_chat_completion(provider: str, data: dict[str, Any]) -> None:
    """OpenRouter/Comet иногда отвечают HTTP 200 с error в теле или без choices — тогда нужен фолбэк."""
    if data.get("error") is not None:
        raise RuntimeError(f"{provider}: {_format_api_error_body(data)}")
    if str(data.get("object") or "").strip() == "error":
        raise RuntimeError(f"{provider}: {_format_api_error_body(data)}")
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        raise RuntimeError(f"{provider}: в ответе нет choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise RuntimeError(f"{provider}: некорректный choices[0]")
    msg = first.get("message")
    if not isinstance(msg, dict):
        raise RuntimeError(f"{provider}: нет message в choices[0]")
    if msg.get("tool_calls"):
        return
    content = msg.get("content")
    images = msg.get("images")
    has_images = isinstance(images, list) and bool(images)
    if isinstance(content, list) and content:
        return
    if content is None and not has_images:
        raise RuntimeError(f"{provider}: пустой message.content")
    if isinstance(content, str) and not content.strip() and not has_images:
        raise RuntimeError(f"{provider}: пустой текст ответа (content)")


def _data_url_to_image(url: str) -> dict[str, str] | None:
    raw = (url or "").strip()
    if not raw.startswith("data:"):
        return None
    try:
        header, b64 = raw.split(",", 1)
    except ValueError:
        return None
    mime = "image/png"
    if header.startswith("data:") and ";" in header:
        mime = header[5:].split(";", 1)[0].strip() or mime
    try:
        blob = base64.b64decode(b64, validate=False)
    except Exception:
        return None
    if not blob:
        return None
    return {"mime": mime, "b64": base64.b64encode(blob).decode("ascii")}


def extract_chat_message_media(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Текст и картинки (base64) из chat/completions message."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        return "", []
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        return "", []
    texts: list[str] = []
    images: list[dict[str, str]] = []

    def _take_url(url: str) -> None:
        parsed = _data_url_to_image(url)
        if parsed:
            images.append(parsed)

    content = msg.get("content")
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                texts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "").strip().lower()
            if kind in ("text", "output_text"):
                texts.append(str(part.get("text") or part.get("output_text") or ""))
            elif kind in ("image_url", "image"):
                url = ""
                img = part.get("image_url") or part.get("image") or {}
                if isinstance(img, str):
                    url = img
                elif isinstance(img, dict):
                    url = str(img.get("url") or img.get("data") or "")
                if not url:
                    url = str(part.get("url") or "")
                _take_url(url)
    extra = msg.get("images")
    if isinstance(extra, list):
        for part in extra:
            if isinstance(part, str):
                _take_url(part)
            elif isinstance(part, dict):
                img = part.get("image_url") or part.get("image") or part
                if isinstance(img, str):
                    _take_url(img)
                elif isinstance(img, dict):
                    _take_url(str(img.get("url") or img.get("data") or ""))
    text = "\n".join(t.strip() for t in texts if str(t).strip()).strip()
    return text, images


def _log_usage(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    operation: str,
    provider: str,
) -> None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    # OpenRouter часто кладёт cost внутри usage; у части клиентов/прокси cost бывает только на верхнем уровне ответа.
    if usage.get("cost") is None:
        top = data.get("cost")
        if isinstance(top, (int, float)):
            usage = {**usage, "cost": float(top)}
        elif isinstance(top, str):
            try:
                usage = {**usage, "cost": float(top.strip())}
            except ValueError:
                pass
    if provider == "comet":
        usage = {**usage, "provider": "comet"}
    cached = extract_cached_tokens(usage)
    if cached is not None and usage.get("cached_tokens") is None:
        usage = {**usage, "cached_tokens": cached}
    gid = data.get("id")
    uid = _current_tg_user_id.get()
    uname = _current_tg_user_username.get()
    insert_usage_event(
        operation=operation,
        model=(data.get("model") or payload.get("model")),
        generation_id=str(gid).strip() if gid else None,
        usage=usage,
        telegram_user_id=uid,
        telegram_username=uname,
    )


def openrouter_chat_completion(
    payload: dict[str, Any],
    *,
    operation: str,
    timeout: float = 120,
) -> dict[str, Any]:
    """POST chat/completions; при успехе логирует usage. Сначала OpenRouter, при ошибке — CometAPI."""
    or_key = os.getenv("OPENROUTER_KEY", "").strip()
    comet_key = os.getenv("COMET_API_KEY", "").strip()
    if not or_key and not comet_key:
        raise RuntimeError("Не заданы OPENROUTER_KEY или COMET_API_KEY в .env")

    primary_err: BaseException | None = None

    if or_key:
        extra: dict[str, str] = {}
        ref = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
        if ref:
            extra["HTTP-Referer"] = ref
        title_hdr = os.getenv("OPENROUTER_X_TITLE", "").strip()
        if title_hdr:
            extra["X-Title"] = title_hdr
        try:
            data = _post_json(
                _openrouter_chat_url(),
                or_key,
                payload,
                extra_headers=extra,
                timeout=timeout,
            )
            _raise_if_bad_chat_completion("openrouter", data)
            _log_usage(data, payload, operation=operation, provider="openrouter")
            return data
        except BaseException as e:
            primary_err = e
            if comet_key:
                print(f"[openrouter_client] openrouter_failed op={operation!r} err={e!r} -> comet")
            else:
                raise

    if not comet_key:
        assert primary_err is not None
        raise primary_err

    payload_c = dict(payload)
    payload_c["model"] = _comet_model_for_request(payload.get("model"))
    try:
        data_c = _post_json(
            _comet_chat_url(),
            comet_key,
            payload_c,
            extra_headers={},
            timeout=timeout,
        )
        _raise_if_bad_chat_completion("comet", data_c)
        _log_usage(data_c, payload_c, operation=operation, provider="comet")
        return data_c
    except BaseException as e:
        if primary_err is not None:
            raise RuntimeError(
                f"Comet: {e!r}; до этого OpenRouter: {primary_err!r}"
            ) from e
        raise


def list_openrouter_models(*, force: bool = False) -> dict[str, Any]:
    """Каталог текстовых моделей OpenRouter для выбора в миниаппе."""
    global _MODELS_CACHE
    default = default_openrouter_model()
    now = time.monotonic()
    if (
        not force
        and _MODELS_CACHE is not None
        and now - _MODELS_CACHE[0] < _MODELS_TTL_SEC
    ):
        return _MODELS_CACHE[1]

    or_key = os.getenv("OPENROUTER_KEY", "").strip()
    if not or_key:
        out = normalize_openrouter_models([], default=default)
        _MODELS_CACHE = (now, out)
        return out

    extra: dict[str, str] = {}
    ref = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    if ref:
        extra["HTTP-Referer"] = ref
    title_hdr = os.getenv("OPENROUTER_X_TITLE", "").strip()
    if title_hdr:
        extra["X-Title"] = title_hdr
    headers = {
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    headers.update(extra)
    try:
        r = requests.get(_openrouter_models_url(), headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []
        out = normalize_openrouter_models(items, default=default)
    except BaseException:
        out = normalize_openrouter_models([], default=default)
    _MODELS_CACHE = (now, out)
    return out


def build_gpt_picker_models(
    catalog_ids: set[str],
    *,
    ask: str | None = None,
) -> dict[str, Any]:
    catalog_has_openai = any(mid.startswith("openai/") for mid in catalog_ids)
    models: list[dict[str, str]] = []
    for mid, name in GPT_PICKER_MODELS:
        if catalog_has_openai and mid not in catalog_ids:
            continue
        models.append({"id": mid, "name": name})
    if not models:
        models = [{"id": mid, "name": name} for mid, name in GPT_PICKER_MODELS]
    ask_id = sanitize_openrouter_model_id(ask)
    preferred = ask_id or "openai/gpt-4.1"
    default = models[0]["id"]
    if any(row["id"] == preferred for row in models):
        default = preferred
    models.sort(key=lambda row: (0 if row["id"] == default else 1, row["name"].lower()))
    return {"models": models, "default": default}


def list_gpt_picker_models(*, force: bool = False) -> dict[str, Any]:
    """Модели для выбора в режиме GPT: несколько актуальных GPT, не Gemini PAIE."""
    catalog = list_openrouter_models(force=force)
    available = {m["id"] for m in catalog.get("models") or []}
    return build_gpt_picker_models(
        available,
        ask=os.getenv("OPENROUTER_MODEL_ASK", ""),
    )
