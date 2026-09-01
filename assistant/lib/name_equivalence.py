"""Сопоставление имён: кириллица/латиница, прозвища (Вова ↔ vova)."""

from __future__ import annotations


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


# Пары взаимозаменяемых написаний (нормализованные ключи).
_NICKNAME_PAIRS: tuple[tuple[str, str], ...] = (
    ("вова", "vova"),
    ("дима", "dima"),
    ("саша", "sasha"),
    ("маша", "masha"),
    ("коля", "kolya"),
    ("паша", "pasha"),
    ("женя", "zhenya"),
    ("ваня", "vanya"),
    ("петя", "petya"),
    ("серёжа", "seryozha"),
    ("сережа", "seryozha"),
)


def name_lookup_keys(name: str) -> set[str]:
    """Варианты строки для сравнения с именем/алиасом контакта."""
    n = _norm(name)
    if not n:
        return set()
    keys = {n}
    if n.startswith("@"):
        keys.add(n.lstrip("@"))
    for a, b in _NICKNAME_PAIRS:
        if n == a:
            keys.add(b)
        elif n == b:
            keys.add(a)
    return keys


def names_equivalent(needle: str, candidate: str) -> bool:
    nk = name_lookup_keys(needle)
    ck = name_lookup_keys(candidate)
    if not nk or not ck:
        return False
    if nk & ck:
        return True
    for n in nk:
        for c in ck:
            if len(n) >= 3 and len(c) >= 3 and (c.startswith(n[:3]) or n.startswith(c[:3])):
                return True
            if len(n) >= 2 and (n in c or c in n):
                return True
    return False
