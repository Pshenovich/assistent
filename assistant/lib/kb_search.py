"""Поисковые эвристики для базы знаний (русская морфология)."""

from __future__ import annotations

import re

_KB_SEARCH_INTRO_RE = re.compile(
    r"^(?:найди|найти|ищи|покажи|поиск|расскажи|что\s+есть)\s+"
    r"(?:мне\s+)?(?:в\s+)?(?:базе\s+знан\w*|баз[ае]\w*)\s+"
    r"(?:(?:про|об|по)\s+)?(.+)$",
    re.IGNORECASE | re.UNICODE,
)

_KB_NAME_STOPWORDS = frozenset(
    {
        "база",
        "базе",
        "базы",
        "знаний",
        "знания",
        "знанию",
        "корпоративная",
        "корпоративной",
        "компании",
        "компания",
        "наша",
        "нашей",
    }
)

_RU_ENDINGS = (
    "скими",
    "скому",
    "ского",
    "ской",
    "ское",
    "ская",
    "ские",
    "ский",
    "ских",
    "ским",
    "скую",
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ому",
    "ией",
    "ии",
    "ию",
    "ия",
    "ьем",
    "ием",
    "ов",
    "ев",
    "ей",
    "ий",
    "ый",
    "ая",
    "ое",
    "ые",
    "ам",
    "ах",
    "ом",
    "ем",
    "ой",
    "ую",
    "ю",
    "а",
    "е",
    "и",
    "о",
    "ы",
    "ь",
    "й",
    "у",
)


def extract_kb_search_query(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    m = _KB_SEARCH_INTRO_RE.search(s)
    if m:
        body = (m.group(1) or "").strip()
        if body:
            return body
    cleaned = re.sub(
        r"^(?:найди|найти|ищи|покажи|поиск|расскажи|что\s+есть)\s+"
        r"(?:мне\s+)?(?:в\s+)?(?:базе\s+знан\w*|баз[ае]\w*)\s*",
        "",
        s,
        flags=re.IGNORECASE | re.UNICODE,
    ).strip()
    cleaned = re.sub(r"^(?:про|о|об|по)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned or s


def _kb_title_tokens(title: str) -> list[str]:
    tokens = re.findall(r"\w+", (title or "").lower(), flags=re.UNICODE)
    out: list[str] = []
    for token in tokens:
        if len(token) < 2:
            continue
        if token in _KB_NAME_STOPWORDS:
            continue
        out.append(token)
    return out


def resolve_kb_name_and_search_query(
    text: str,
    *,
    knowledge_bases: list[dict[str, str]] | None = None,
    parsed_kb_name: str = "",
    parsed_search_query: str = "",
) -> tuple[str, str]:
    """Отделяет имя базы (например «ОП») от поискового запроса."""
    kb_name = normalize_kb_name_filter(parsed_kb_name)
    search_query = (parsed_search_query or "").strip()
    body = extract_kb_search_query(text)
    if not body:
        body = (text or "").strip()

    if not kb_name and knowledge_bases:
        candidates: list[tuple[int, str, str]] = []
        body_l = body.lower()
        for kb in knowledge_bases:
            title = str(kb.get("title") or "").strip()
            if not title:
                continue
            for token in _kb_title_tokens(title):
                if body_l == token or body_l.startswith(token + " "):
                    candidates.append((len(token), token, title))
        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[2]))
            kb_name = candidates[0][1]
            body = re.sub(
                rf"^{re.escape(candidates[0][1])}\s+",
                "",
                body,
                count=1,
                flags=re.IGNORECASE,
            ).strip()

    if not search_query:
        search_query = body
    return kb_name, search_query


def expand_kb_search_queries(query: str) -> list[str]:
    """Несколько вариантов запроса для лучшего recall."""
    base = (query or "").strip()
    if not base:
        return []
    out: list[str] = [base]
    words = tokenize_query(base)
    if len(words) >= 2:
        joined = " ".join(words)
        if joined not in out:
            out.append(joined)
    focus = [w for w in words if len(w) >= 4][:6]
    for w in focus:
        if w not in out:
            out.append(w)
    return out


def normalize_kb_name_filter(name: str) -> str:
    value = (name or "").strip().lower()
    if not value or value in _KB_NAME_STOPWORDS:
        return ""
    return value


def tokenize_query(text: str) -> list[str]:
    return [
        w
        for w in re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)
        if len(w) >= 2
    ]


def normalize_ru_word(word: str) -> str:
    w = (word or "").lower().strip()
    if len(w) <= 3:
        return w
    for _ in range(4):
        changed = False
        for suf in _RU_ENDINGS:
            # Keep stems of length >= 3 so дети/детей/детские → «дет»
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                changed = True
                break
        if not changed:
            break
    return w


def word_matches_haystack(word: str, hay: str, *, hay_words: list[str] | None = None) -> bool:
    w = (word or "").lower().strip()
    if len(w) < 2:
        return False
    if w in hay:
        return True
    stem = normalize_ru_word(w)
    if len(stem) >= 3 and stem in hay:
        return True
    words = hay_words if hay_words is not None else re.findall(r"\w+", hay)
    for hw in words:
        hw = hw.lower()
        if w == hw or stem == hw:
            return True
        hstem = normalize_ru_word(hw)
        if stem and hstem and stem == hstem:
            return True
        prefix = min(5, len(stem), len(hstem))
        if prefix >= 4 and stem[:prefix] == hstem[:prefix]:
            return True
    return False


def score_query_against_haystack(query: str, hay: str) -> float:
    words = tokenize_query(query)
    if not words:
        return 0.0
    hay_l = (hay or "").lower()
    hay_words = re.findall(r"\w+", hay_l, flags=re.UNICODE)
    return float(sum(1 for w in words if word_matches_haystack(w, hay_l, hay_words=hay_words)))


_NOTE_SEARCH_INTRO_RE = re.compile(
    r"^(?:найди|найти|ищи|покажи|поиск|открой|дай)\s+"
    r"(?:мне\s+)?(?:в\s+)?заметк\w*\s*"
    r"(?:(?:про|о|об|по)\s+)?(.+)$",
    re.IGNORECASE | re.UNICODE,
)

_NOTE_STOPWORDS = frozenset(
    {
        "найди",
        "найти",
        "ищи",
        "покажи",
        "поиск",
        "открой",
        "дай",
        "заметка",
        "заметку",
        "заметки",
        "заметке",
        "заметках",
        "заметкой",
        "про",
        "об",
        "по",
        "о",
        "в",
        "во",
        "на",
        "и",
        "или",
        "а",
        "но",
        "же",
        "ли",
        "мне",
        "меня",
        "мой",
        "моя",
        "мое",
        "моё",
        "мои",
        "есть",
        "это",
        "эта",
        "этот",
        "эти",
        "что",
        "чтобы",
        "как",
        "где",
        "когда",
        "какой",
        "какая",
        "какие",
        "какое",
        "который",
        "которая",
        "которые",
        "нужно",
        "надо",
        "хочу",
        "пожалуйста",
        "можно",
        "сейчас",
        "там",
        "тут",
        "здесь",
        "всё",
        "все",
        "еще",
        "ещё",
        "уже",
        "был",
        "была",
        "было",
        "были",
        "быть",
        "мы",
        "вы",
        "они",
        "он",
        "она",
        "оно",
        "их",
        "нам",
        "нас",
        "для",
        "из",
        "от",
        "до",
        "при",
        "со",
        "к",
        "ко",
        "у",
        "с",
    }
)


def extract_note_search_query(text: str) -> str:
    """Убирает вводные «найди в заметках …» и лишний шум из запроса."""
    s = " ".join((text or "").strip().split())
    if not s:
        return ""
    m = _NOTE_SEARCH_INTRO_RE.match(s)
    if m:
        body = (m.group(1) or "").strip()
        if body:
            s = body
    cleaned = re.sub(
        r"^(?:найди|найти|ищи|покажи|поиск|открой|дай)\s+(?:мне\s+)?",
        "",
        s,
        flags=re.IGNORECASE | re.UNICODE,
    ).strip()
    cleaned = re.sub(
        r"^(?:в\s+)?заметк\w*\s+(?:(?:про|о|об|по)\s+)?",
        "",
        cleaned,
        flags=re.IGNORECASE | re.UNICODE,
    ).strip()
    cleaned = re.sub(r"^(?:про|о|об|по)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned or s


def note_query_content_words(query: str) -> list[str]:
    """Содержательные токены запроса без стоп-слов."""
    words = tokenize_query(query)
    content = [w for w in words if w not in _NOTE_STOPWORDS]
    return content or words


def score_note_against_query(query: str, title: str, body: str) -> float:
    """Релевантность заметки: морфология + буст заголовка + порог покрытия."""
    words = note_query_content_words(query)
    if not words:
        return 0.0

    title_l = (title or "").lower()
    body_l = (body or "").lower()
    title_words = re.findall(r"\w+", title_l, flags=re.UNICODE)
    body_words = re.findall(r"\w+", body_l, flags=re.UNICODE)

    title_hits = 0
    body_hits = 0
    matched = 0
    for w in words:
        in_title = word_matches_haystack(w, title_l, hay_words=title_words)
        in_body = word_matches_haystack(w, body_l, hay_words=body_words)
        if in_title:
            title_hits += 1
            matched += 1
        elif in_body:
            body_hits += 1
            matched += 1

    n = len(words)
    if n <= 3:
        min_matched = n
    else:
        min_matched = max(2, (n * 2 + 2) // 3)  # ceil(2n/3)
    if matched < min_matched:
        return 0.0

    score = float(title_hits * 4 + body_hits + matched)

    q_joined = " ".join(words)
    if q_joined and q_joined in title_l:
        score += 6.0
    if title_hits == n and n >= 1:
        score += 5.0
    # Полное покрытие заголовка заметки коротким запросом
    if title_words and title_hits >= min(len(title_words), n) and title_hits >= 1:
        score += 3.0
    return score
