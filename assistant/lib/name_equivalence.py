"""Сопоставление имён: кириллица/латиница, прозвища (Вова ↔ vova ↔ Владимир), падежи (с Вовой)."""

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


# Полное имя → уменьшительные формы. «Вова» в просьбе и «Владимир» в контакте — один человек.
_DIMINUTIVES: dict[str, tuple[str, ...]] = {
    "владимир": ("вова", "володя", "вовка", "вовочка", "вован", "vova", "volodya", "vladimir"),
    "александр": ("саша", "саня", "шура", "санёк", "sasha", "alexander", "aleksandr"),
    "александра": ("саша", "шура", "sasha"),
    "дмитрий": ("дима", "митя", "димон", "dima", "dmitry", "dmitriy"),
    "николай": ("коля", "колян", "kolya", "nikolay", "nikolai"),
    "павел": ("паша", "pasha", "pavel"),
    "евгений": ("женя", "zhenya", "evgeny", "evgeniy"),
    "евгения": ("женя", "zhenya"),
    "иван": ("ваня", "vanya", "ivan"),
    "пётр": ("петя", "petya", "petr", "pyotr"),
    "петр": ("петя", "petya", "petr", "pyotr"),
    "сергей": ("серёжа", "сережа", "серёга", "серега", "seryozha", "sergey", "sergei"),
    "алексей": ("лёша", "леша", "алёша", "алеша", "lesha", "alexey", "aleksey"),
    "андрей": ("андрюша", "andrey", "andrei"),
    "михаил": ("миша", "misha", "mikhail"),
    "мария": ("маша", "masha", "maria", "mariya"),
    "екатерина": ("катя", "katya", "ekaterina"),
    "анна": ("аня", "anya", "anna"),
    "ольга": ("оля", "olya", "olga"),
    "наталья": ("наташа", "natasha", "natalya"),
    "наталия": ("наташа", "natasha"),
    "татьяна": ("таня", "tanya", "tatyana"),
    "елена": ("лена", "lena", "elena"),
    "юлия": ("юля", "yulya", "yulia"),
    "анастасия": ("настя", "nastya", "anastasia"),
    "ульяна": ("уля", "ulya", "ulyana"),
    "ксения": ("ксюша", "ksusha", "ksenia"),
    "виктория": ("вика", "vika", "victoria"),
    "константин": ("костя", "kostya", "konstantin"),
    "григорий": ("гриша", "grisha", "grigory"),
    "анатолий": ("толя", "tolya", "anatoly"),
    "василий": ("вася", "vasya", "vasily"),
    "станислав": ("стас", "stas", "stanislav"),
    "вячеслав": ("слава", "slava", "vyacheslav"),
    "ярослав": ("слава", "slava", "yaroslav"),
    "георгий": ("жора", "гоша", "georgy"),
    "роман": ("рома", "roma", "roman"),
    "антон": ("тоша", "anton"),
    "илья": ("илюша", "ilya"),
    "артём": ("тёма", "тема", "artem", "artyom"),
    "артем": ("тёма", "тема", "artem"),
    "максим": ("макс", "max", "maxim", "maksim"),
    "светлана": ("света", "sveta", "svetlana"),
    "ирина": ("ира", "ira", "irina"),
    "дарья": ("даша", "dasha", "darya"),
    "полина": ("поля", "polina"),
    "валерия": ("лера", "lera", "valeria"),
    "людмила": ("люда", "lyuda", "lyudmila"),
    "галина": ("галя", "galya", "galina"),
}
_NAME_GROUPS: dict[str, set[str]] = {}
for _full, _short in _DIMINUTIVES.items():
    for _k in (_full, *_short):
        _NAME_GROUPS.setdefault(_k, set()).update({_full, *_short})

# Падежные окончания → именительный: «с Вовой», «Володе», «Владимиром».
_CASE_ENDINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ой", ("а",)), ("ей", ("я", "а", "ей", "ий")), ("ою", ("а",)), ("ею", ("я",)),
    ("ом", ("",)), ("ем", ("ь", "й", "")), ("ём", ("ь", "")),
    ("ым", ("",)), ("ем", ("ий",)),
    ("у", ("а", "")), ("ю", ("я", "й", "ь")), ("ы", ("а",)), ("и", ("я", "а", "ь", "ий")),
    ("е", ("а", "я", "", "ий")), ("а", ("", "й", "ь")), ("я", ("й", "ь")),
)


def _nominative_candidates(word: str) -> set[str]:
    out = {word}
    for end, repl in _CASE_ENDINGS:
        if len(word) > len(end) + 2 and word.endswith(end):
            stem = word[: -len(end)]
            out.update(stem + r for r in repl)
    return out


def name_group_keys(name: str) -> set[str]:
    """Все формы имени из словаря прозвищ (Вовой → вова, владимир, володя, vova…); пусто, если имени нет в словаре."""
    n = _norm(name).lstrip("@")
    if not n or " " in n:
        return set()
    out: set[str] = set()
    for cand in _nominative_candidates(n):
        out |= _NAME_GROUPS.get(cand, set())
    return out


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
    ng, cg = name_group_keys(needle), name_group_keys(candidate)
    if ng and cg:
        # Оба имени есть в словаре прозвищ — решает словарь, без нечёткого сравнения
        # (иначе «Саша» совпала бы с «Алексей» по префиксу «але» у «Александр»).
        return bool(ng & cg)
    if ng & ck or cg & nk:
        return True
    for n in nk:
        for c in ck:
            if len(n) >= 3 and len(c) >= 3 and (c.startswith(n[:3]) or n.startswith(c[:3])):
                return True
            if len(n) >= 2 and (n in c or c in n):
                return True
    return False
