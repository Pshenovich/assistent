"""LLMProvider: JSON-ответы через существующий OpenRouter-клиент."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from assistant.integrations.openrouter_client import (
    is_llm_configured,
    openrouter_chat_completion,
)
from assistant.lib.llm_json import strip_json_from_markdown


_FREE_FALLBACKS = (
    "minimax/minimax-m2.7:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
)


def board_model() -> str:
    return (
        os.getenv("OPENROUTER_MODEL_BOARD", "").strip()
        or os.getenv("OPENROUTER_MODEL", "").strip()
        or os.getenv("OPENROUTER_MODEL_ROUTER", "").strip()
        or _FREE_FALLBACKS[0]
    )


def _fallback_models(primary: str) -> list[str]:
    extra = os.getenv("OPENROUTER_MODEL_BOARD_FALLBACK", "").strip()
    out: list[str] = []
    seen = {primary}
    for m in (extra, *_FREE_FALLBACKS):
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def is_credit_error(exc: BaseException) -> bool:
    text = str(exc)
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code == 402:
        return True
    return "402" in text or "Insufficient credits" in text or "Payment Required" in text


def public_llm_error(exc: BaseException) -> str:
    if is_credit_error(exc):
        return (
            "OpenRouter: закончились кредиты, платные модели недоступны.\n"
            "Пополните баланс на openrouter.ai/settings/credits "
            "или задайте OPENROUTER_MODEL_BOARD на бесплатную модель (:free)."
        )
    return f"LLM недоступна: {str(exc)[:280]}"


def _timeout() -> float:
    try:
        return max(30.0, float(os.getenv("BOARD_LLM_TIMEOUT_SEC", "90") or "90"))
    except ValueError:
        return 90.0


def board_aux_model() -> str:
    return os.getenv("OPENROUTER_MODEL_BOARD_AUX", "").strip() or board_model()


def _env_int(name: str, default: int, *, lo: int = 1, hi: int = 8000) -> int:
    try:
        return max(lo, min(hi, int(os.getenv(name, str(default)) or default)))
    except ValueError:
        return default


def agent_max_tokens() -> int:
    return _env_int("BOARD_AGENT_MAX_TOKENS", 1200, lo=256, hi=8000)


def chair_max_tokens() -> int:
    return _env_int("BOARD_CHAIR_MAX_TOKENS", 2500, lo=512, hi=8000)


def aux_max_tokens() -> int:
    return _env_int("BOARD_AUX_MAX_TOKENS", 800, lo=128, hi=4000)


def _is_anthropic(model: str) -> bool:
    return (model or "").startswith("anthropic/")


def _cached_text_block(text: str, *, cache: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _messages_payload(
    *,
    model: str,
    system: str,
    user: str,
    cache_prefix: str = "",
) -> list[dict[str, Any]]:
    if not _is_anthropic(model):
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    system_msg: dict[str, Any] = {
        "role": "system",
        "content": [_cached_text_block(system, cache=True)],
    }
    prefix = (cache_prefix or "").strip()
    if prefix and user.startswith(prefix):
        rest = user[len(prefix) :].lstrip("\n")
        user_content: list[dict[str, Any]] = [_cached_text_block(prefix, cache=True)]
        if rest:
            user_content.append(_cached_text_block(rest, cache=False))
        return [system_msg, {"role": "user", "content": user_content}]
    return [system_msg, {"role": "user", "content": user}]


class LLMProvider(Protocol):
    def generate_json(
        self,
        *,
        system: str,
        user: str,
        operation: str,
        temperature: float = 0.4,
        max_tokens: int | None = None,
        model: str | None = None,
        cache_prefix: str = "",
    ) -> dict[str, Any]: ...


class OpenRouterProvider:
    """Единый интерфейс агентов. Смена модели — через env, без смены логики."""

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        operation: str,
        temperature: float = 0.4,
        max_tokens: int | None = None,
        model: str | None = None,
        cache_prefix: str = "",
    ) -> dict[str, Any]:
        if not is_llm_configured():
            raise RuntimeError("Не заданы OPENROUTER_KEY или COMET_API_KEY")
        primary = (model or "").strip() or board_model()
        models = [primary, *_fallback_models(primary)]
        last_err: BaseException | None = None
        data: dict[str, Any] | None = None
        for model_id in models:
            payload: dict[str, Any] = {
                "model": model_id,
                "messages": _messages_payload(
                    model=model_id,
                    system=system,
                    user=user,
                    cache_prefix=cache_prefix,
                ),
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            if max_tokens is not None:
                payload["max_tokens"] = int(max_tokens)
            try:
                data = openrouter_chat_completion(
                    payload, operation=operation, timeout=_timeout()
                )
                if model_id != models[0]:
                    print(f"[board.llm] fallback_model={model_id} op={operation}")
                break
            except Exception as e:
                last_err = e
                if is_credit_error(e) or getattr(getattr(e, "response", None), "status_code", None) in {
                    402,
                    404,
                    429,
                }:
                    print(f"[board.llm] model={model_id} fail={e!r} — пробую следующую")
                    continue
                payload.pop("response_format", None)
                try:
                    data = openrouter_chat_completion(
                        payload, operation=operation, timeout=_timeout()
                    )
                    break
                except Exception as e2:
                    last_err = e2
                    continue
        if data is None:
            assert last_err is not None
            raise last_err
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get(
            "content"
        ) or ""
        raw = strip_json_from_markdown(str(content))
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise RuntimeError(f"{operation}: LLM вернул не объект JSON")
        return obj


_default_provider: OpenRouterProvider | None = None


def get_provider() -> OpenRouterProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = OpenRouterProvider()
    return _default_provider


def set_provider(provider: OpenRouterProvider | None) -> None:
    global _default_provider
    _default_provider = provider


def generate_json(
    *,
    system: str,
    user: str,
    operation: str,
    temperature: float = 0.4,
    max_tokens: int | None = None,
    model: str | None = None,
    cache_prefix: str = "",
    provider: OpenRouterProvider | None = None,
) -> dict[str, Any]:
    impl = provider or get_provider()
    return impl.generate_json(
        system=system,
        user=user,
        operation=operation,
        temperature=temperature,
        max_tokens=max_tokens,
        model=model,
        cache_prefix=cache_prefix,
    )
