"""Форматирование исключений для пользовательских сообщений."""

from __future__ import annotations

try:
    BaseExceptionGroup
except NameError:  # pragma: no cover - Python < 3.11
    from exceptiongroup import BaseExceptionGroup


def _is_exception_group(exc: BaseException) -> bool:
    """Проверка группы исключений без привязки к конкретному классу (3.9/3.11/anyio)."""
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions)
    group = getattr(exc, "exceptions", None)
    return isinstance(group, tuple) and bool(group)


def unwrap_exception(exc: BaseException) -> BaseException:
    """Возвращает корневую причину из ExceptionGroup / TaskGroup anyio."""
    current: BaseException = exc
    seen: set[int] = set()
    while _is_exception_group(current):
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        subs = current.exceptions  # type: ignore[attr-defined]
        if len(subs) == 1:
            sub = subs[0]
            if sub is None:
                break
            current = sub
            continue
        for sub in subs:
            if sub is not None:
                current = sub
                break
        else:
            break
    return current


def _flatten_exceptions(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []

    def walk(item: BaseException) -> None:
        if _is_exception_group(item):
            for sub in item.exceptions:  # type: ignore[attr-defined]
                if sub is not None:
                    walk(sub)
            return
        out.append(item)

    walk(exc)
    return out


def format_exception_message(exc: BaseException) -> str:
    """Человекочитаемое сообщение об ошибке без обёртки TaskGroup."""
    root = unwrap_exception(exc)
    message = str(root).strip()
    if message and "unhandled errors in a TaskGroup" not in message:
        return message

    for sub in _flatten_exceptions(exc):
        text = str(sub).strip()
        if text and "unhandled errors in a TaskGroup" not in text:
            return text

    if message:
        return message
    return root.__class__.__name__
