"""Обработка текста после распознавания: паразиты, числа, личный словарь, сниппеты.

Всё правилами, без LLM: на одну диктовку — миллисекунды. Что включено, решают
настройки пользователя (``assistant.stores.voice_prefs``); здесь только чистые функции.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# ---------------------------------------------------------------- слова-паразиты

_HESITATION = r"(?:э+(?:-э+)*|эм+|м{2,}|хм+|а-а+)"
# Вводные «пустые» слова: убираем, только когда они отделены запятыми или стоят
# в начале/конце фразы — «как бы не опоздать» и «вот договор» не трогаем.
_FILLER = (
    r"(?:ну|короче|как бы|типа|это самое|так сказать|скажем так|в общем-то|вот|"
    + _HESITATION
    + r")"
)
_SENT_START = r"(^|[.!?…]\s+)"
# После этих слов запятая структурная: «сказал, ну, что…» → «сказал, что…».
_KEEP_COMMA_BEFORE = frozenset(
    "что чтобы который которая которое которые которого которой которым "
    "но а если когда где куда откуда как потому поэтому хотя или либо пока "
    "чем зато однако".split()
)

_RE_START_COMMA = re.compile(_SENT_START + _FILLER + r"\s*,\s*", re.IGNORECASE)
_RE_START_BARE = re.compile(
    _SENT_START + r"(?:ну|короче|" + _HESITATION + r")\s+(?=\w)", re.IGNORECASE
)
_RE_MID = re.compile(r",\s*" + _FILLER + r"\s*,\s*(?=(\w+))", re.IGNORECASE)
_RE_END = re.compile(r",?\s*(?<![\w-])" + _FILLER + r"\s*(?=[.!?…]|$)", re.IGNORECASE)
_RE_HESITATION = re.compile(
    r"(?<![\w-])" + _HESITATION + r"(?![\w-])[,]?", re.IGNORECASE
)


def _capitalize_sentences(text: str) -> str:
    return re.sub(
        r"(^|[.!?…]\s+)([a-zа-яё])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )


def _tidy_punctuation(text: str) -> str:
    t = re.sub(r"[ \t]+([,.!?…:;])", r"\1", text)
    t = re.sub(r",(\s*,)+", ",", t)
    t = re.sub(r",\s*([.!?…])", r"\1", t)
    t = re.sub(r"(^|\n)[ \t]*[,;]+[ \t]*", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def remove_fillers(text: str) -> str:
    """«Ну, короче, я посмотрел договор, и, э-э, там…» → «Я посмотрел договор, и там…»."""
    t = str(text or "")
    if not t.strip():
        return t.strip()
    for _ in range(4):  # «Ну, короче, …» — по одному за проход
        prev = t
        t = _RE_START_COMMA.sub(r"\1", t)
        t = _RE_START_BARE.sub(r"\1", t)
        t = _RE_MID.sub(
            lambda m: ", " if m.group(1).lower() in _KEEP_COMMA_BEFORE else " ", t
        )
        t = _RE_END.sub("", t)
        if t == prev:
            break
    t = _RE_HESITATION.sub("", t)
    return _capitalize_sentences(_tidy_punctuation(t))


# ---------------------------------------------------------------------- числа


def _forms(value: int, *words: str) -> dict[str, int]:
    return {w: value for w in words}


_UNITS: dict[str, int] = {}
_UNITS.update(_forms(0, "ноль", "нуля", "нулю", "нулём", "нулем"))
_UNITS.update(
    _forms(1, "один", "одного", "одному", "одним", "одном", "одна", "одной", "одну", "одно")
)
_UNITS.update(_forms(2, "два", "две", "двух", "двум", "двумя"))
_UNITS.update(_forms(3, "три", "трёх", "трех", "трём", "трем", "тремя"))
_UNITS.update(_forms(4, "четыре", "четырёх", "четырех", "четырём", "четырем", "четырьмя"))
_UNITS.update(_forms(5, "пять", "пяти", "пятью"))
_UNITS.update(_forms(6, "шесть", "шести", "шестью"))
# «семью» без числа рядом — чаще «семья», поэтому только в составе (см. _AMBIGUOUS).
_UNITS.update(_forms(7, "семь", "семи", "семью"))
_UNITS.update(_forms(8, "восемь", "восьми", "восемью", "восьмью"))
_UNITS.update(_forms(9, "девять", "девяти", "девятью"))

_TEENS: dict[str, int] = {}
for _v, _w in (
    (10, "десять"),
    (11, "одиннадцать"),
    (12, "двенадцать"),
    (13, "тринадцать"),
    (14, "четырнадцать"),
    (15, "пятнадцать"),
    (16, "шестнадцать"),
    (17, "семнадцать"),
    (18, "восемнадцать"),
    (19, "девятнадцать"),
):
    _TEENS.update(_forms(_v, _w, _w[:-1] + "и", _w[:-1] + "ью"))

_TENS: dict[str, int] = {}
_TENS.update(_forms(20, "двадцать", "двадцати", "двадцатью"))
_TENS.update(_forms(30, "тридцать", "тридцати", "тридцатью"))
_TENS.update(_forms(40, "сорок", "сорока"))
_TENS.update(_forms(50, "пятьдесят", "пятидесяти", "пятьюдесятью"))
_TENS.update(_forms(60, "шестьдесят", "шестидесяти", "шестьюдесятью"))
_TENS.update(_forms(70, "семьдесят", "семидесяти", "семьюдесятью"))
_TENS.update(_forms(80, "восемьдесят", "восьмидесяти", "восемьюдесятью"))
_TENS.update(_forms(90, "девяносто", "девяноста"))

_HUNDREDS: dict[str, int] = {}
_HUNDREDS.update(_forms(100, "сто", "ста"))
_HUNDREDS.update(_forms(200, "двести", "двухсот", "двумстам"))
_HUNDREDS.update(_forms(300, "триста", "трёхсот", "трехсот", "трёмстам", "тремстам"))
_HUNDREDS.update(
    _forms(400, "четыреста", "четырёхсот", "четырехсот", "четырёмстам", "четыремстам")
)
_HUNDREDS.update(_forms(500, "пятьсот", "пятисот", "пятистам"))
_HUNDREDS.update(_forms(600, "шестьсот", "шестисот", "шестистам"))
_HUNDREDS.update(_forms(700, "семьсот", "семисот", "семистам"))
_HUNDREDS.update(_forms(800, "восемьсот", "восьмисот", "восьмистам"))
_HUNDREDS.update(_forms(900, "девятьсот", "девятисот", "девятистам"))

_MULTS: dict[str, int] = {}
_MULTS.update(
    _forms(1000, "тысяча", "тысячи", "тысяч", "тысячу", "тысячей", "тысячам", "тысячами")
)
_MULTS.update(
    _forms(10**6, "миллион", "миллиона", "миллионов", "миллиону", "миллионом", "миллионами")
)
_MULTS.update(
    _forms(10**9, "миллиард", "миллиарда", "миллиардов", "миллиарду", "миллиардом")
)

# Порядковые — только в датах («двадцать пятого марта» → «25 марта»).
_ORD_STEMS = {
    1: "перв", 2: "втор", 3: "трет", 4: "четвёрт", 5: "пят", 6: "шест", 7: "седьм",
    8: "восьм", 9: "девят", 10: "десят", 11: "одиннадцат", 12: "двенадцат",
    13: "тринадцат", 14: "четырнадцат", 15: "пятнадцат", 16: "шестнадцат",
    17: "семнадцат", 18: "восемнадцат", 19: "девятнадцат", 20: "двадцат",
    30: "тридцат",
}
_ORDINALS: dict[str, int] = {}
for _v, _stem in _ORD_STEMS.items():
    endings = ("ьего", "ье") if _v == 3 else ("ого", "ое")
    for _e in endings:
        _ORDINALS[_stem + _e] = _v
        _ORDINALS[_stem.replace("ё", "е") + _e] = _v

_MONTHS_GEN = frozenset(
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
)
# Одно такое слово само по себе — не число: «один из нас», «семью», «тысячи людей».
_AMBIGUOUS = frozenset(w for w, v in _UNITS.items() if v == 1) | {"семью"}

_WORD_RE = re.compile(r"[А-Яа-яЁё]+")


def _cls(word: str) -> tuple[str, int] | None:
    w = word.lower()
    for name, table in (
        ("unit", _UNITS),
        ("teen", _TEENS),
        ("ten", _TENS),
        ("hundred", _HUNDREDS),
        ("mult", _MULTS),
    ):
        if w in table:
            return name, table[w]
    return None


# Что может идти за чем внутри одного числа («сто двадцать пять тысяч триста»).
_NEXT_OK = {
    None: {"unit", "teen", "ten", "hundred"},
    "hundred": {"unit", "teen", "ten", "mult"},
    "ten": {"unit", "mult"},
    "teen": {"mult"},
    "unit": {"mult"},
    "mult": {"unit", "teen", "ten", "hundred"},
}


def _format_int(n: int) -> str:
    if n < 10000:
        return str(n)
    return f"{n:,}".replace(",", " ")


def _parse_number(words: list[re.Match[str]], i: int, text: str) -> tuple[int, int, str] | None:
    """С позиции i: (сколько слов съели, значение, тип 'card'|'ord'). None — не число."""
    total = 0
    group = 0
    last: str | None = None
    last_mult = 10**12
    j = i
    while j < len(words):
        if j > i and text[words[j - 1].end() : words[j].start()] != " ":
            break
        w = words[j].group(0).lower()
        c = _cls(w)
        if c is None:
            # Порядковое в конце: «двадцать пятого марта».
            ordv = _ORDINALS.get(w)
            if ordv is None:
                break
            nxt = j + 1
            if (
                total == 0
                and group in (0, 20, 30)
                and not (group and ordv >= 10)
                and nxt < len(words)
                and text[words[j].end() : words[nxt].start()] == " "
                and words[nxt].group(0).lower() in _MONTHS_GEN
                and 1 <= group + ordv <= 31
            ):
                return j - i + 1, group + ordv, "ord"
            # «двадцать пятого» не про дату — оставляем словами целиком.
            return None
        name, val = c
        if name not in _NEXT_OK[last]:
            break
        if name == "mult":
            if val >= last_mult or (group == 0 and total == 0):
                break
            total += (group or 1) * val
            group = 0
            last_mult = val
        else:
            group += val
        last = name
        j += 1
    used = j - i
    if used == 0:
        return None
    # «двадцать» без порядкового слова и месяца — обычное число.
    return used, total + group, "card"


def numbers_to_digits(text: str) -> str:
    """«в шесть часов» → «в 6 часов», «двадцать пятого марта» → «25 марта»."""
    t = str(text or "")
    words = list(_WORD_RE.finditer(t))
    if not words:
        return t
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(words):
        parsed = _parse_number(words, i, t)
        if not parsed or (parsed[0] == 1 and words[i].group(0).lower() in _AMBIGUOUS):
            i += 1
            continue
        used, value, _kind = parsed
        start = words[i].start()
        end = words[i + used - 1].end()
        out.append(t[pos:start])
        out.append(_format_int(value))
        pos = end
        i += used
    out.append(t[pos:])
    res = "".join(out)
    # «в 6 30» → «в 6:30»; «к 9 05» тоже.
    res = re.sub(
        r"(?i)(?<![\w])(в|к|до|с|со|после|около)\s+(\d{1,2})\s+(?:0\s+)?(\d{1,2})(?![\d:])",
        _join_time,
        res,
    )
    return res


def _join_time(m: re.Match[str]) -> str:
    h = int(m.group(2))
    mm_raw = m.group(3)
    zero = " 0 " in m.group(0)
    mm = int(mm_raw)
    if h > 23 or mm > 59 or (zero and mm > 9) or (not zero and mm < 10):
        return m.group(0)
    return f"{m.group(1)} {h}:{mm:02d}"


# ---------------------------------------------------------------- личный словарь

_CYR = re.compile(r"[А-Яа-яЁё]")


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _dictionary_pattern(src: str, dst: str) -> re.Pattern[str] | None:
    parts = [
        re.sub("[её]", "[её]", re.escape(p))
        for p in _norm_space(src).split(" ")
        if p
    ]
    if not parts:
        return None
    # «гитхабе» → GitHub: для латинских замен допускаем русское окончание.
    last = parts[-1]
    tail = ""
    if _CYR.search(src) and not _CYR.search(dst) and len(src.split()[-1]) >= 4:
        tail = r"[а-яё]{0,3}"
    body = r"\s+".join(parts[:-1] + [last + tail])
    return re.compile(r"(?<![\w-])" + body + r"(?![\w-])", re.IGNORECASE)


def apply_dictionary(text: str, entries: Iterable[dict[str, Any]]) -> str:
    """Замены «как услышано → как писать», длинные фразы раньше коротких."""
    t = str(text or "")
    pairs = []
    for e in entries or []:
        src = _norm_space((e or {}).get("from", ""))
        dst = str((e or {}).get("to", "") or "").strip()
        if src and dst and e.get("enabled", True) is not False:
            pairs.append((src, dst))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    for src, dst in pairs:
        pat = _dictionary_pattern(src, dst)
        if pat is None:
            continue

        def _repl(m: re.Match[str], dst: str = dst) -> str:
            got = m.group(0)
            if got[:1].isupper() and dst[:1].islower() and _CYR.match(dst[:1]):
                return dst[:1].upper() + dst[1:]
            return dst

        t = pat.sub(_repl, t)
    return t


# ---------------------------------------------------------------------- сниппеты


def _snippet_key(s: str) -> str:
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s]", " ", s)
    return _norm_space(s)


def match_snippet(text: str, snippets: Iterable[dict[str, Any]]) -> str | None:
    """Вся диктовка — фраза-триггер («адрес офиса») → текст сниппета."""
    key = _snippet_key(text)
    if not key:
        return None
    for s in snippets or []:
        trig = _snippet_key((s or {}).get("trigger", ""))
        body = str((s or {}).get("text", "") or "")
        if trig and body.strip() and trig == key:
            return body
    return None


# ----------------------------------------------------------------- общий конвейер


def postprocess(
    text: str,
    *,
    cleanup: bool = True,
    numbers: bool = True,
    dictionary: Iterable[dict[str, Any]] | None = None,
    snippets: Iterable[dict[str, Any]] | None = None,
) -> str:
    """Порядок: паразиты → сниппет (вся фраза) → числа → словарь."""
    t = str(text or "").strip()
    if not t:
        return t
    if snippets:
        hit = match_snippet(t, snippets)
        if hit is None and cleanup:
            hit = match_snippet(remove_fillers(t), snippets)
        if hit is not None:
            return hit
    if cleanup:
        t = remove_fillers(t)
    if numbers:
        t = numbers_to_digits(t)
    if dictionary:
        t = apply_dictionary(t, dictionary)
    return t
