"""Нормализация извлечённого саммари v2: цитаты, спикеры, исполнители, сроки.

Ничего не вызывает по сети — чистые функции поверх JSON модели и транскрипта.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Optional

SPEAKER_CONFIRM_MIN = 0.75  # с этой уверенности «я сделаю» отдаём имени спикера
SPEAKER_HINT_MIN = 0.4  # с этой — показываем «Спикер 2 (вероятно Саша)»

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPEAKER_LABEL_RE = re.compile(r"^(?:спикер|speaker)[ _]?(\d+)$", re.IGNORECASE)


def _norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def norm_speaker_label(label: str) -> str:
    raw = (label or "").strip().strip("[]():")
    m = _SPEAKER_LABEL_RE.match(raw)
    return f"Спикер {m.group(1)}" if m else raw


# ---------- цитаты ----------


def locate_quote(quote: str, transcript_norm: str) -> float:
    """Насколько цитата реально есть в транскрипте: 1.0 — дословно, 0 — нет."""
    q = _norm(quote)
    if not q or not transcript_norm:
        return 0.0
    if q in transcript_norm:
        return 1.0
    words = q.split()
    if len(words) < 3:
        return 0.0
    # якорь — самое длинное слово цитаты; сравниваем окна вокруг его вхождений
    anchor = max(words, key=len)
    best = 0.0
    start = 0
    n = len(q)
    hits = 0
    while hits < 40:
        i = transcript_norm.find(anchor, start)
        if i < 0:
            break
        hits += 1
        lo = max(0, i - n)
        window = transcript_norm[lo : i + n + len(anchor)]
        m = SequenceMatcher(None, q, window, autojunk=False)
        blocks = sum(b.size for b in m.get_matching_blocks())
        best = max(best, blocks / max(1, len(q)))
        if best >= 0.95:
            break
        start = i + len(anchor)
    return round(best, 2)


# ---------- speaker_map ----------


def clean_speaker_map(raw: Any, transcript: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    labels = {norm_speaker_label(m.group(0)) for m in re.finditer(r"(?:Спикер|SPEAKER)[ _]?\d+", transcript or "", re.IGNORECASE)}
    for k, v in raw.items():
        label = norm_speaker_label(str(k))
        if not label or (labels and label not in labels):
            continue
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "").strip()
        try:
            conf = float(v.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        if not name:
            conf = 0.0
        out[label] = {
            "name": name,
            "confidence": round(conf, 2),
            "evidence": str(v.get("evidence") or "").strip()[:240],
            "confirmed": bool(v.get("confirmed")),
        }
    return out


def speaker_name(speaker_map: dict[str, dict[str, Any]], label: str, *, min_conf: float) -> str:
    info = speaker_map.get(norm_speaker_label(label)) or {}
    name = str(info.get("name") or "").strip()
    if not name:
        return ""
    if info.get("confirmed") or float(info.get("confidence") or 0) >= min_conf:
        return name
    return ""


# ---------- люди ----------


def _name_forms(name: str) -> set[str]:
    n = _norm(name)
    if not n:
        return set()
    forms = {n}
    parts = n.split()
    if parts:
        forms.add(parts[0])
    return forms


_ENDING_RE = re.compile(r"(?:еем|ием|ию|ия|ий|ею|ея|ей|ой|ем|ом|ю|я|и|е|а|у|ы|о|ь|й)$")

# уменьшительные → полное имя (основы без окончаний, «ё» → «е»)
_NICK = {
    "саш": "александр", "шур": "александр", "пет": "петр", "вов": "владимир", "володь": "владимир",
    "дим": "дмитрий", "миш": "михаил", "кол": "николай", "леш": "алексей", "сереж": "сергей",
    "серег": "сергей", "жен": "евгений", "паш": "павел", "кост": "константин", "ван": "иван",
    "андрюш": "андрей", "толь": "анатолий", "слав": "вячеслав", "гриш": "григорий", "юр": "юрий",
    "стас": "станислав", "макс": "максим", "вит": "виктор", "тем": "артем", "ден": "денис",
    "ром": "роман", "кат": "екатерина", "маш": "мария", "наст": "анастасия", "лен": "елена",
    "тан": "татьяна", "ол": "ольга", "юл": "юлия", "наташ": "наталья", "ир": "ирина",
    "свет": "светлана", "ан": "анна", "даш": "дарья", "ксюш": "ксения", "люд": "людмила",
    "гал": "галина", "вик": "виктория", "лиз": "елизавета", "сон": "софья", "над": "надежда",
}


def _base(word: str) -> str:
    w = (word or "").replace("ё", "е")
    b = _ENDING_RE.sub("", w) if len(w) >= 3 else w
    return b or w


def _canon_first(word: str) -> str:
    b = _base(word)
    full = _NICK.get(b)
    return _base(full) if full else b


def match_person(name: str, people: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Однозначное совпадение имени из транскрипта с человеком из контекста."""
    q = _norm(name)
    if not q or not people:
        return None
    q_first = q.split()[0]
    exact: list[dict[str, Any]] = []
    loose: list[dict[str, Any]] = []
    for p in people:
        forms: set[str] = set()
        forms |= _name_forms(str(p.get("name") or ""))
        for a in p.get("aliases") or []:
            forms |= _name_forms(str(a))
        if q in forms:
            exact.append(p)
            continue
        # падежи и уменьшительные: «Пети»/«Петей» ~ «Петя» ~ «Пётр», «Ивану» ~ «Иван»
        qc = _canon_first(q_first)
        if len(qc) >= 2 and any(f and _canon_first(f.split()[0]) == qc for f in forms):
            loose.append(p)
    pool = exact or loose
    # один человек может быть и контактом, и владельцем — склеиваем по полному имени
    by_name: dict[str, list[dict[str, Any]]] = {}
    for p in pool:
        by_name.setdefault(_norm(str(p.get("name") or "")), []).append(p)
    if len(by_name) != 1:
        return None
    same = next(iter(by_name.values()))
    owner = next((p for p in same if p.get("owner")), None)
    if owner is None:
        return same[0]
    merged = dict(owner)
    merged["key"] = next((str(p.get("key") or "") for p in same if p.get("key")), "")
    return merged


# ---------- сроки ----------

_MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def clean_due_iso(raw: Any, meeting_day: Optional[date]) -> str:
    s = str(raw or "").strip()[:10]
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return ""
    if meeting_day and not (meeting_day - timedelta(days=1) <= d <= meeting_day + timedelta(days=400)):
        return ""
    return d.isoformat()


def due_label(deadline: str, due_iso: str) -> str:
    text = (deadline or "").strip()
    if not due_iso:
        return text
    d = date.fromisoformat(due_iso)
    short = f"{d.day} {_MONTHS_SHORT[d.month - 1]}"
    if not text:
        return f"до {short}"
    if re.search(r"\d", text):
        return text
    return f"{text} ({short})"


# ---------- задачи ----------

_TASK_KEYS = ("task", "assignee", "assignee_basis", "speaker", "deadline", "due_iso", "completed", "quote")


def raw_task(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    task = str(item.get("task") or "").strip()
    if not task:
        return None
    basis = str(item.get("assignee_basis") or "").strip().lower()
    if basis not in ("named", "self", "none"):
        basis = "named" if str(item.get("assignee") or "").strip() else "none"
    return {
        "task": task,
        "assignee": str(item.get("assignee") or "").strip(),
        "assignee_basis": basis,
        "speaker": norm_speaker_label(str(item.get("speaker") or "")),
        "deadline": str(item.get("deadline") or "").strip(),
        "due_iso": str(item.get("due_iso") or "").strip(),
        "completed": bool(item.get("completed")),
        "quote": str(item.get("quote") or "").strip()[:300],
    }


def resolve_task(
    t: dict[str, Any],
    *,
    speaker_map: dict[str, dict[str, Any]],
    people: list[dict[str, Any]],
    meeting_day: Optional[date],
    transcript_norm: str,
) -> dict[str, Any]:
    """Финальная задача: assignee — для показа, остальное — в поля."""
    out = dict(t)
    who = str(t.get("assignee") or "").strip()
    speaker = str(t.get("speaker") or "").strip()
    basis = t.get("assignee_basis") or "none"
    hint = ""
    if basis == "self" and speaker:
        # самоназначение: имя только при уверенном сопоставлении спикера
        sure = speaker_name(speaker_map, speaker, min_conf=SPEAKER_CONFIRM_MIN)
        if sure:
            who = sure
        elif speaker.startswith("Спикер"):
            who = ""
            guess = speaker_name(speaker_map, speaker, min_conf=SPEAKER_HINT_MIN)
            hint = f"{speaker} (вероятно {guess})" if guess else speaker
        else:
            who = speaker  # в транскрипте реальные имена
    elif basis == "self" and not speaker:
        who = ""
    out["assignee_name"] = who
    person = match_person(who, people) if who else None
    if person:
        out["assignee_name"] = str(person.get("name") or who)
        out["assignee_contact_key"] = str(person.get("key") or "")
    out["owner_flag"] = "mine" if person and person.get("owner") else ("others" if who else "unknown")
    out["assignee"] = out["assignee_name"] or hint
    out["assignee_unresolved"] = not out["assignee_name"]
    out["due_iso"] = clean_due_iso(t.get("due_iso"), meeting_day)
    out["deadline_text"] = str(t.get("deadline") or "").strip()
    out["deadline"] = due_label(out["deadline_text"], out["due_iso"])
    q = str(t.get("quote") or "")
    out["quote_score"] = locate_quote(q, transcript_norm) if q else 0.0
    return out


def merge_raw_tasks(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Склейка задач двух вызовов: дубликат — по тексту задачи или по одной цитате."""
    out: list[dict[str, Any]] = []

    def _dup(a: dict[str, Any], b: dict[str, Any]) -> bool:
        ta, tb = _norm(a["task"]), _norm(b["task"])
        if ta == tb:
            return True
        qa, qb = _norm(a.get("quote") or ""), _norm(b.get("quote") or "")
        if qa and qb and (qa in qb or qb in qa) and SequenceMatcher(None, ta, tb).ratio() >= 0.5:
            return True
        return SequenceMatcher(None, ta, tb).ratio() >= 0.85

    for src in (primary, secondary):
        for t in src:
            cur = next((x for x in out if _dup(x, t)), None)
            if cur is None:
                out.append(dict(t))
                continue
            for k in ("assignee", "speaker", "deadline", "due_iso", "quote"):
                if not cur.get(k) and t.get(k):
                    cur[k] = t[k]
            if cur.get("assignee_basis") == "none" and t.get("assignee_basis") != "none":
                cur["assignee_basis"] = t["assignee_basis"]
                if t.get("assignee"):
                    cur["assignee"] = t["assignee"]
            if t.get("completed"):
                cur["completed"] = True
            if len(t["task"]) > len(cur["task"]):
                cur["task"] = t["task"]
    return out


def resolve_tasks(
    tasks: list[dict[str, Any]],
    *,
    speaker_map: dict[str, dict[str, Any]],
    people: list[dict[str, Any]],
    meeting_at: str,
    transcript: str,
) -> list[dict[str, Any]]:
    try:
        day = datetime.fromisoformat(meeting_at).date() if meeting_at else None
    except ValueError:
        day = None
    tn = _norm(transcript)
    return [
        resolve_task(t, speaker_map=speaker_map, people=people, meeting_day=day, transcript_norm=tn)
        for t in tasks
    ]


def text_items(raw: Any, *, limit: int = 30) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if isinstance(x, dict):
            s = str(x.get("text") or x.get("name") or x.get("task") or "").strip()
        else:
            s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out[:limit]


def decisions(raw: Any, transcript: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tn = _norm(transcript)
    if not isinstance(raw, list):
        return out
    for x in raw:
        if isinstance(x, dict):
            text = str(x.get("text") or x.get("decision") or "").strip()
            quote = str(x.get("quote") or "").strip()[:300]
        else:
            text, quote = str(x or "").strip(), ""
        if not text:
            continue
        out.append({"text": text, "quote": quote, "quote_score": locate_quote(quote, tn) if quote else 0.0})
    return out


_V2_META_KEYS = ("speaker_map", "summary_data", "summary_version", "summary_model", "meeting_at", "decisions_detail")


def v2_meta(result: dict[str, Any] | None) -> dict[str, Any]:
    """Поля саммари v2 для note.meta (пусто для v1)."""
    if not isinstance(result, dict) or result.get("summary_version") != 2:
        return {}
    out = {k: result[k] for k in _V2_META_KEYS if k in result}
    data = out.get("summary_data")
    if isinstance(data, dict):
        # задачи, спикеры и решения уже лежат в meta отдельно — не дублируем
        out["summary_data"] = {k: v for k, v in data.items() if k not in ("tasks", "speaker_map", "decisions")}
    return out


def summary_data_from_meta(meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    data = meta.get("summary_data")
    if not isinstance(data, dict):
        return None
    full = dict(data)
    full["tasks"] = [t for t in meta.get("tasks") or [] if isinstance(t, dict)]
    full["speaker_map"] = dict(meta.get("speaker_map") or {})
    full["decisions"] = [d for d in meta.get("decisions_detail") or [] if isinstance(d, dict)]
    return full


def tasks_for_inject(result: dict[str, Any] | None) -> Optional[list[dict[str, Any]]]:
    """Задачи для prepare_summary_markdown: v2 уже отрисовал их на своём месте — не трогаем."""
    if not isinstance(result, dict) or result.get("summary_version") == 2:
        return None
    return result.get("tasks")
