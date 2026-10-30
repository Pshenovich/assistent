"""Вызов chat/completions: OpenRouter, при сбое — CometAPI (OpenAI-совместимый). Запись usage в SQLite."""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from contextlib import contextmanager
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


@contextmanager
def usage_user(telegram_user_id: int | str | None):
    """Расход ИИ внутри блока записывается на этого пользователя (фон, саммари, задания)."""
    tok_id = _current_tg_user_id.set(
        None if telegram_user_id in (None, "") else str(telegram_user_id).strip()
    )
    tok_name = _current_tg_user_username.set(None)
    try:
        yield
    finally:
        _current_tg_user_id.reset(tok_id)
        _current_tg_user_username.reset(tok_name)


def get_openrouter_usage_telegram_user() -> tuple[str | None, str | None]:
    """Текущий Telegram-пользователь для записи usage (в т.ч. из worker-thread после copy_context)."""
    return _current_tg_user_id.get(), _current_tg_user_username.get()


def comet_api_key() -> str:
    """Ключ CometAPI (фолбэк). В .env встречается под разными именами."""
    for name in ("COMET_API_KEY", "COMETA_APIKEY", "COMET_APIKEY", "COMETA_API_KEY"):
        v = os.getenv(name, "").strip()
        if v:
            return v
    return ""


def is_llm_configured() -> bool:
    """Есть ли ключ для LLM: OpenRouter и/или Comet (основной или только фолбэк)."""
    return bool(os.getenv("OPENROUTER_KEY", "").strip() or comet_api_key())


# Если прокси OpenRouter не отвечает по сети — какое-то время сразу идём к
# следующему (запасному прокси или Comet), а не ждём таймаут на каждом запросе.
# Ключ — адрес прокси, значение — time.monotonic(), до которого он пропускается.
_OR_DOWN: dict[str, float] = {}


def _or_connect_timeout() -> float:
    try:
        return max(2.0, float(os.getenv("OPENROUTER_CONNECT_TIMEOUT_SEC", "8") or "8"))
    except ValueError:
        return 8.0


def _or_down_sec() -> float:
    try:
        return max(30.0, float(os.getenv("OPENROUTER_DOWN_SEC", "300") or "300"))
    except ValueError:
        return 300.0


def _openrouter_unreachable(e: BaseException) -> bool:
    """Сетевой сбой или 5xx/403 от шлюза — OpenRouter недоступен, а не плохой запрос."""
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code in (403, 502, 503, 504)
    return False


def _openrouter_chat_urls() -> list[str]:
    """Адреса chat/completions по порядку: основной прокси, затем запасные.

    OPENROUTER_API_URLS — список через запятую; если не задан — один OPENROUTER_API_URL.
    """
    raw = os.getenv("OPENROUTER_API_URLS", "").strip()
    urls = [u.strip() for u in raw.split(",") if u.strip()] if raw else []
    if not urls:
        urls = [
            os.getenv(
                "OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"
            ).strip()
        ]
    return list(dict.fromkeys(urls))


def _openrouter_chat_url() -> str:
    return _openrouter_chat_urls()[0]


# Фоновые разборы (алерты, Фокус, почта, брифы) не должны занимать прокси
# целиком, пока человек ждёт ответа Лео: одновременно их не больше N на процесс.
# chat_digest и market_insight сюда не входят — их запускают и кнопкой в Орбите.
_BACKGROUND_OPERATIONS = frozenset(
    {
        "chat_alerts",
        "proactive_insights",
        "proactive_chat_insights",
        "mail_insights",
        "meeting_brief",
        "meeting_brief_related",
        "market_digest",
    }
)
_BG_SEM: threading.BoundedSemaphore | None = None
_BG_SEM_LOCK = threading.Lock()
# Дольше не ждём: лучше лишний параллельный вызов, чем вставший фон.
_BG_WAIT_MAX_SEC = 300.0


def _background_semaphore() -> threading.BoundedSemaphore:
    global _BG_SEM
    with _BG_SEM_LOCK:
        if _BG_SEM is None:
            try:
                n = max(1, int(os.getenv("LLM_BACKGROUND_MAX_CONCURRENCY", "3") or "3"))
            except ValueError:
                n = 3
            _BG_SEM = threading.BoundedSemaphore(n)
        return _BG_SEM


@contextmanager
def _llm_slot(operation: str):
    if operation not in _BACKGROUND_OPERATIONS:
        yield
        return
    sem = _background_semaphore()
    got = sem.acquire(timeout=_BG_WAIT_MAX_SEC)
    if not got:
        print(f"[openrouter_client] background slot wait timeout op={operation!r} -> run anyway")
    try:
        yield
    finally:
        if got:
            sem.release()


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

# Модель совета по умолчанию остаётся в списке, даже если env указывает другую.
PAIE_BASE_MODELS: tuple[tuple[str, str], ...] = (
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
)

# Модели, которые думают до ответа. Доступны ассистенту, исследованию и PAIE.
PAIE_REASONING_MODELS: tuple[tuple[str, str], ...] = (
    ("openai/o3", "o3"),
    ("openai/o4-mini", "o4-mini"),
    ("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
    ("deepseek/deepseek-r1", "DeepSeek R1"),
    ("anthropic/claude-opus-5.5", "Claude Opus 5.5"),
    ("anthropic/claude-fable-5.1", "Claude Fable 5.1"),
)


# Модели, которые «Авто» выбирает сам, — только для подписи ответа, в списке выбора их нет.
AUTO_INTERNAL_LABELS: dict[str, str] = {"anthropic/claude-haiku-5.5": "Claude Haiku 5.5"}


def _auto_row(label_key: str) -> dict[str, Any]:
    from assistant.nlu.auto_model import AUTO_LABEL, AUTO_MODEL_ID, AUTO_NOTE

    return {"id": AUTO_MODEL_ID, label_key: AUTO_LABEL, "note": AUTO_NOTE}


def tg_leo_models() -> list[dict[str, str]]:
    """Модели, которые можно выбрать для Лео в Telegram (Профиль → «Лео в Telegram»). «Авто» — первым."""
    return [_auto_row("label"), *({"id": mid, "label": label} for mid, label in (*GPT_PICKER_MODELS, *PAIE_REASONING_MODELS))]


def tg_leo_model_allowed(model_id: str | None) -> bool:
    mid = str(model_id or "").strip()
    return bool(mid) and any(m["id"] == mid for m in tg_leo_models())


def gpt_display_label(model_id: str | None) -> str:
    """Короткий ярлык для пузыря ответа: GPT-4.1, GPT-4o mini, …"""
    mid = sanitize_openrouter_model_id(model_id) or ""
    if not mid:
        return "GPT"
    from assistant.nlu.auto_model import AUTO_LABEL, is_auto

    if is_auto(mid):
        return AUTO_LABEL
    for pid, pname in (*GPT_PICKER_MODELS, *PAIE_REASONING_MODELS, *AUTO_INTERNAL_LABELS.items()):
        if pid == mid:
            return pname
    short = mid.split("/")[-1].strip()
    if not short:
        return "GPT"
    lower = short.lower()
    if lower.startswith("gpt-"):
        rest = short[4:].strip()
        return f"GPT-{rest}" if rest else "GPT"
    if lower.startswith("gpt"):
        rest = short[3:].lstrip("-_ ").strip()
        return f"GPT-{rest}" if rest else "GPT"
    return f"GPT-{short}"


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
    timeout: float | tuple[float, float],
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


_MD_DATA_IMG_RE = re.compile(
    r"!\[[^\]]*\]\((data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)\)",
    re.IGNORECASE,
)
_MAX_FETCH_IMAGE_BYTES = 5 * 1024 * 1024


def default_image_model() -> str:
    return os.getenv("OPENROUTER_MODEL_IMAGE", "").strip() or "google/gemini-3.1-flash-image"


def model_emits_images(model: str | None) -> bool:
    m = (model or "").lower()
    return any(tok in m for tok in ("image", "flux", "dall-e", "dalle", "gpt-image"))


def _openrouter_images_url() -> str | None:
    """URL Image API. None — если кастомный gateway без /images (тогда только chat)."""
    chat = _openrouter_chat_url().rstrip("/")
    if chat.endswith("/chat/completions"):
        base = chat[: -len("/chat/completions")]
        # Прокси вида /v1/<uuid>/chat/completions часто без endpoint /images.
        if re.search(r"/[0-9a-f]{8}-[0-9a-f-]{27,}/?$", base, re.I):
            return None
        if "openrouter.ai" not in base and re.search(r"/v1/[^/]+$", base):
            return None
        return base + "/images"
    if "openrouter.ai" in chat:
        return "https://openrouter.ai/api/v1/images"
    return None


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


def _http_url_to_image(url: str) -> dict[str, str] | None:
    raw = (url or "").strip()
    if not raw.startswith("https://"):
        return None
    try:
        r = requests.get(raw, timeout=20)
        r.raise_for_status()
        blob = r.content
        if not blob or len(blob) > _MAX_FETCH_IMAGE_BYTES:
            return None
        mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not mime.startswith("image/"):
            mime = "image/png"
        return {"mime": mime, "b64": base64.b64encode(blob).decode("ascii")}
    except Exception:
        return None


def _url_to_image(url: str) -> dict[str, str] | None:
    parsed = _data_url_to_image(url)
    if parsed:
        return parsed
    return _http_url_to_image(url)


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
        parsed = _url_to_image(url)
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
                b64 = str(part.get("b64_json") or part.get("b64") or "").strip()
                if b64:
                    mime = str(part.get("media_type") or part.get("mime") or "image/png")
                    images.append({"mime": mime, "b64": b64})
                    continue
                img = part.get("image_url") or part.get("image") or part
                if isinstance(img, str):
                    _take_url(img)
                elif isinstance(img, dict):
                    _take_url(str(img.get("url") or img.get("data") or ""))
    text = "\n".join(t.strip() for t in texts if str(t).strip()).strip()
    if text:
        def _md_repl(match: re.Match[str]) -> str:
            parsed = _data_url_to_image(match.group(1))
            if parsed:
                images.append(parsed)
            return ""

        text = _MD_DATA_IMG_RE.sub(_md_repl, text).strip()
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


def _payload_for_comet(payload: dict[str, Any]) -> dict[str, Any]:
    """Comet не принимает OpenRouter plugins и type=file — выкидываем PDF-части."""
    out = dict(payload)
    out.pop("plugins", None)
    out.pop("modalities", None)
    out.pop("prompt_cache_key", None)  # параметр OpenAI через OpenRouter; Comet его не знает
    messages: list[Any] = []
    for msg in out.get("messages") or []:
        if not isinstance(msg, dict):
            messages.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            messages.append(msg)
            continue
        names: list[str] = []
        parts: list[Any] = []
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") == "file":
                file_obj = part.get("file") if isinstance(part.get("file"), dict) else {}
                names.append(str(file_obj.get("filename") or "file"))
                continue
            parts.append(part)
        copied = dict(msg)
        if names:
            note = "Приложены файлы (фолбэк без PDF): " + ", ".join(names)
            if parts and isinstance(parts[0], dict) and parts[0].get("type") == "text":
                parts[0] = {
                    **parts[0],
                    "text": (str(parts[0].get("text") or "").rstrip() + "\n\n" + note).strip(),
                }
            else:
                parts.insert(0, {"type": "text", "text": note})
        if len(parts) == 1 and isinstance(parts[0], dict) and parts[0].get("type") == "text":
            copied["content"] = str(parts[0].get("text") or "")
        else:
            copied["content"] = parts
        messages.append(copied)
    out["messages"] = messages
    return out


def _images_from_api_data(data: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        rows = []
    for item in rows:
        if isinstance(item, str):
            parsed = _url_to_image(item)
            if parsed:
                out.append(parsed)
            continue
        if not isinstance(item, dict):
            continue
        b64 = str(item.get("b64_json") or item.get("b64") or "").strip()
        mime = str(item.get("media_type") or item.get("mime") or "image/png").split(";")[0]
        if b64:
            out.append({"mime": mime or "image/png", "b64": b64})
            continue
        url = str(item.get("url") or "")
        parsed = _url_to_image(url)
        if parsed:
            out.append(parsed)
    if out:
        return out
    _, chat_images = extract_chat_message_media(data)
    return chat_images


def openrouter_generate_images(
    prompt: str,
    *,
    model: str | None = None,
    n: int = 1,
    timeout: float = 120,
    source_images: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Картинки через Image API OpenRouter; при сбое — chat/completions с modalities."""
    text = (prompt or "").strip()
    if not text:
        return []
    or_key = os.getenv("OPENROUTER_KEY", "").strip()
    if not or_key:
        raise RuntimeError("Не задан OPENROUTER_KEY в .env")
    image_model = sanitize_openrouter_model_id(model) or default_image_model()
    extra: dict[str, str] = {}
    ref = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    if ref:
        extra["HTTP-Referer"] = ref
    title_hdr = os.getenv("OPENROUTER_X_TITLE", "").strip()
    if title_hdr:
        extra["X-Title"] = title_hdr
    refs: list[dict[str, Any]] = []
    for img in (source_images or [])[:2]:
        if not isinstance(img, dict):
            continue
        b64 = str(img.get("b64") or "").strip()
        mime = str(img.get("mime") or "image/png").split(";")[0].strip() or "image/png"
        if not b64:
            continue
        refs.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    images_url = _openrouter_images_url()
    if images_url:
        payload: dict[str, Any] = {
            "model": image_model,
            "prompt": text[:4000],
            "n": max(1, min(int(n), 4)),
        }
        if refs:
            payload["input_references"] = refs
        try:
            data = _post_json(
                images_url,
                or_key,
                payload,
                extra_headers=extra,
                timeout=timeout,
            )
            if data.get("error") is not None:
                raise RuntimeError(_format_api_error_body(data))
            images = _images_from_api_data(data)
            if images:
                _log_usage(data, payload, operation="ask_image", provider="openrouter")
                return images
        except BaseException as e:
            print(f"[openrouter_client] images_api_failed model={image_model!r} err={e!r} -> chat")
    guide = (
        "Generate an image now. Return the image in the response modalities, "
        "not a text-only refusal.\n\n"
        + text[:4000]
    )
    chat_content: Any = guide
    if refs:
        chat_content = [{"type": "text", "text": guide}, *refs]
    chat_payload: dict[str, Any] = {
        "model": image_model,
        "messages": [{"role": "user", "content": chat_content}],
        "modalities": ["image", "text"],
    }
    data_c = openrouter_chat_completion(
        chat_payload,
        operation="ask_image",
        timeout=timeout,
    )
    _, images = extract_chat_message_media(data_c)
    return images


def openrouter_chat_completion(
    payload: dict[str, Any],
    *,
    operation: str,
    timeout: float = 120,
) -> dict[str, Any]:
    """POST chat/completions; при успехе логирует usage.

    Прокси OpenRouter по очереди (основной, запасные), при сбое — CometAPI.
    """
    from assistant.nlu.auto_model import is_auto, strong_model

    if is_auto(payload.get("model")):
        # «Авто» выбирается до запроса (auto_model.resolve); сюда дошёл без выбора — сильная модель.
        payload = {**payload, "model": strong_model()}
    with _llm_slot(operation):
        return _chat_completion(payload, operation=operation, timeout=timeout)


def _chat_completion(
    payload: dict[str, Any],
    *,
    operation: str,
    timeout: float,
) -> dict[str, Any]:
    or_key = os.getenv("OPENROUTER_KEY", "").strip()
    comet_key = comet_api_key()
    if not or_key and not comet_key:
        raise RuntimeError("Не заданы OPENROUTER_KEY или COMET_API_KEY в .env")

    primary_err: BaseException | None = None
    urls = _openrouter_chat_urls() if or_key else []
    now = time.monotonic()
    live = [u for u in urls if now >= _OR_DOWN.get(u, 0.0)]
    # Все прокси помечены упавшими и идти больше некуда — пробуем их всё равно.
    if not live and not comet_key:
        live = urls

    extra: dict[str, str] = {}
    ref = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    if ref:
        extra["HTTP-Referer"] = ref
    title_hdr = os.getenv("OPENROUTER_X_TITLE", "").strip()
    if title_hdr:
        extra["X-Title"] = title_hdr
    for i, url in enumerate(live):
        try:
            data = _post_json(
                url,
                or_key,
                payload,
                extra_headers=extra,
                timeout=(_or_connect_timeout(), timeout),
            )
            _raise_if_bad_chat_completion("openrouter", data)
            _log_usage(data, payload, operation=operation, provider="openrouter")
            return data
        except BaseException as e:
            primary_err = e
            unreachable = _openrouter_unreachable(e)
            has_next = i + 1 < len(live)
            if unreachable and (has_next or comet_key):
                _OR_DOWN[url] = time.monotonic() + _or_down_sec()
            # Плохой запрос — не вина прокси, другой прокси ответит так же.
            if unreachable and has_next:
                print(f"[openrouter_client] openrouter_failed op={operation!r} url#{i} err={e!r} -> next proxy")
                continue
            if comet_key:
                print(f"[openrouter_client] openrouter_failed op={operation!r} err={e!r} -> comet")
                break
            raise

    if not comet_key:
        assert primary_err is not None
        raise primary_err

    payload_c = _payload_for_comet(payload)
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
    models: list[dict[str, Any]] = []
    for mid, name in GPT_PICKER_MODELS:
        if catalog_has_openai and mid not in catalog_ids:
            continue
        models.append({"id": mid, "name": name, "reasoning": False})
    if not any(not row.get("reasoning") for row in models):
        models = [
            {"id": mid, "name": name, "reasoning": False} for mid, name in GPT_PICKER_MODELS
        ]
    for mid, name in PAIE_REASONING_MODELS:
        if any(row["id"] == mid for row in models):
            continue
        models.append({"id": mid, "name": name, "reasoning": True})
    from assistant.nlu.auto_model import AUTO_MODEL_ID

    ask_id = sanitize_openrouter_model_id(ask)
    preferred = ask_id or AUTO_MODEL_ID
    default = preferred if any(row["id"] == preferred for row in models) or preferred == AUTO_MODEL_ID else models[0]["id"]
    models.sort(
        key=lambda row: (
            0 if row["id"] == default else 1,
            1 if row.get("reasoning") else 0,
            str(row["name"]).lower(),
        )
    )
    # «Авто» — всегда первым, с подписью про экономию.
    return {"models": [{**_auto_row("name"), "reasoning": False}, *models], "default": default}


def paie_static_model_ids() -> set[str]:
    return (
        {mid for mid, _name in PAIE_BASE_MODELS}
        | {mid for mid, _name in GPT_PICKER_MODELS}
        | {mid for mid, _name in PAIE_REASONING_MODELS}
    )


def is_paie_reasoning_model(model_id: str | None) -> bool:
    mid = sanitize_openrouter_model_id(model_id) or ""
    return mid in {row[0] for row in PAIE_REASONING_MODELS}


def build_paie_picker_models(
    catalog_ids: set[str],
    *,
    default: str,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Модели PAIE: текущая модель совета, те же GPT, что у ассистента, и reasoning."""
    catalog_ids = set(catalog_ids or [])
    labels = dict(names or {})
    catalog_has_openai = any(mid.startswith("openai/") for mid in catalog_ids)
    catalog_has_google = any(mid.startswith("google/") for mid in catalog_ids)
    catalog_has_deepseek = any(mid.startswith("deepseek/") for mid in catalog_ids)
    default_id = sanitize_openrouter_model_id(default) or "google/gemini-2.5-flash"

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(mid: str, name: str, *, reasoning: bool) -> None:
        clean = sanitize_openrouter_model_id(mid)
        if not clean or clean in seen:
            return
        vendor = clean.split("/", 1)[0]
        in_catalog = clean in catalog_ids
        # Курируемые reasoning-модели показываем всегда: каталог часто содержит
        # только текущую модель совета и иначе прячет Gemini 2.5 Pro.
        if catalog_ids and not in_catalog and not reasoning:
            if vendor == "openai" and catalog_has_openai:
                return
            if vendor == "google" and catalog_has_google and clean != default_id:
                return
            if vendor == "deepseek" and catalog_has_deepseek:
                return
        seen.add(clean)
        rows.append(
            {
                "id": clean,
                "name": labels.get(clean) or name,
                "reasoning": bool(reasoning),
            }
        )

    add(
        default_id,
        labels.get(default_id) or default_id.split("/")[-1],
        reasoning=is_paie_reasoning_model(default_id),
    )
    for mid, name in PAIE_BASE_MODELS:
        add(mid, name, reasoning=False)
    for mid, name in GPT_PICKER_MODELS:
        add(mid, name, reasoning=False)
    for mid, name in PAIE_REASONING_MODELS:
        add(mid, name, reasoning=True)
    rows.sort(
        key=lambda row: (
            0 if row["id"] == default_id else 1,
            1 if row["reasoning"] else 0,
            str(row["name"]).lower(),
        )
    )
    return {"models": rows, "default": default_id}


def list_paie_picker_models(*, force: bool = False) -> dict[str, Any]:
    from assistant.board.llm import board_model

    catalog = list_openrouter_models(force=force)
    models = catalog.get("models") or []
    available = {m["id"] for m in models}
    names = {m["id"]: m["name"] for m in models}
    return build_paie_picker_models(available, default=board_model(), names=names)


def list_gpt_picker_models(*, force: bool = False) -> dict[str, Any]:
    """Модели для выбора в режиме GPT: несколько актуальных GPT, не Gemini PAIE."""
    catalog = list_openrouter_models(force=force)
    available = {m["id"] for m in catalog.get("models") or []}
    return build_gpt_picker_models(
        available,
        ask=os.getenv("OPENROUTER_MODEL_ASK", ""),
    )
