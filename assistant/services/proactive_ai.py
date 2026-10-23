"""«Фокус», этап 2: ИИ разбирает новые и изменённые материалы — по событию, не по расписанию.

Событие = материал изменился: новая транскрипция (её саммари — сразу), изменённая заметка (через
2 минуты тишины после правок), материал встречи. Разбор запускается сам (note_changed), не ждёт
открытия ленты; лента узнаёт о свежем разборе через last_analyzed_at и busy. Один и тот же текст повторно не
разбирается (хеш). Лимит разборов на пользователя в день. Из ответа ИИ — договорённости
(кто что сделает и к какому сроку), следующие встречи и открытые вопросы; из них — карточки.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

IDLE = timedelta(minutes=2)
LOOKBACK = timedelta(days=14)
DAILY_CAP = 20
PER_RUN = 3
PER_RUN_MANUAL = 6  # кнопка «Найти инсайты»: пользователь ждёт — разбираем больше за раз
MIN_TEXT = 160
MAX_TEXT = 12000

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None
_RUNNING: set[int] = set()
_AGAIN: dict[int, set[int]] = {}  # пока шёл разбор, пришли новые события (id заметок, готовых сразу)
_TIMERS: dict[tuple[int, int], threading.Timer] = {}
_LAST_ANALYZED: dict[int, float] = {}

SYSTEM = """Ты — помощник руководителя. Тебе дают заметку или итоги встречи пользователя.
Выдели только конкретное и проверяемое по тексту, ничего не придумывай:
- agreement — договорённость: кто что сделает и к какому сроку;
- followup — договорились встретиться/созвониться ещё раз (с кем и о чём);
- open_question — вопрос, который остался нерешённым.
Ответь строго JSON без пояснений:
{"items":[{"kind":"agreement|followup|open_question","text":"коротко, до 120 символов","who":"кто делает (имя как в тексте) или 'я', если сам пользователь","due":"YYYY-MM-DD или ''","people":["имена участников, если важно"],"quote":"короткая цитата-основание из текста"}]}
Не больше 6 пунктов. Если ничего конкретного нет — {"items":[]}. Сроки «до пятницы», «завтра» переводи в даты относительно today."""


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_insights (
            owner_user_id TEXT NOT NULL,
            source_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            analyzed_at TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (owner_user_id, source_key)
        )
        """
    )
    _CONN.commit()
    return _CONN


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(raw: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _plain(html_text: str) -> str:
    t = re.sub(r"<(br|/p|/li|/h\d|/tr)[^>]*>", "\n", str(html_text or ""), flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    import html as _h

    t = _h.unescape(t).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def _hash(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------- что разобрать

def _material_text(note: dict[str, Any]) -> str:
    # Значки разбора из Фокуса (✓ выполнено, ссылка) — не повод разбирать материал заново.
    from assistant.services.focus_items import strip_marks

    if str(note.get("role") or "") == "transcription":
        from assistant.services import transcription_notes as tnotes

        return _plain(strip_marks(tnotes.summary_html(note)))  # только саммари: транскрипция без него слишком сырая
    return _plain(strip_marks(str(note.get("body") or "")))


def candidates(
    uid: int, *, now: Optional[datetime] = None, ready_ids: Optional[set[int]] = None, skip_idle: bool = False
) -> list[tuple[dict[str, Any], str, str]]:
    """Изменённые и «остывшие» материалы, которые ещё не разобраны в текущем виде.

    ready_ids — материалы, готовые сразу (саммари только что сделано): без ожидания тишины.
    skip_idle — все изменённые, даже правленные только что (ручной запуск).
    """
    ready_ids = ready_ids or set()
    from assistant.stores import notes as notes_store

    now = now or _now()
    out = []
    with _LOCK:
        done = {
            str(r["source_key"]): str(r["content_hash"])
            for r in _conn().execute(
                "SELECT source_key, content_hash FROM proactive_insights WHERE owner_user_id = ?", (str(uid),)
            ).fetchall()
        }
    for n in notes_store.list_notes(uid, limit=200, include_transcription=True):
        if str(n.get("owner_user_id") or n.get("user_id") or uid) != str(uid):
            continue
        if str(n.get("role") or "") == "knowledge":
            continue
        upd = _parse(n.get("updated_at"))
        if not upd or upd < now - LOOKBACK:
            continue
        if upd > now - IDLE and int(n["id"]) not in ready_ids and not skip_idle:
            continue
        text = _material_text(n)
        if len(text) < MIN_TEXT:
            continue
        h = _hash(str(n.get("title") or ""), text)
        key = f"note:{int(n['id'])}"
        if done.get(key) == h:
            continue
        out.append((n, text, h))
    out.sort(key=lambda x: (int(x[0]["id"]) in ready_ids, str(x[0].get("updated_at") or "")), reverse=True)
    return out


def analyzed_today(uid: int) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _LOCK:
        r = _conn().execute(
            "SELECT COUNT(*) AS n FROM proactive_insights WHERE owner_user_id = ? AND analyzed_at >= ?"
            " AND source_key NOT LIKE 'mail:%'",  # у почты свой лимит (mail_source.DAILY_CAP)
            (str(uid), start),
        ).fetchone()
    return int(r["n"] or 0)


# ---------------------------------------------------------------- разбор

def _llm_items(title: str, text: str, today: date, meeting: str) -> list[dict[str, Any]]:
    from assistant.lib.llm_json import strip_json_from_markdown
    from assistant.nlu import llm

    payload = {"today": today.isoformat(), "title": title, "meeting": meeting, "text": text[:MAX_TEXT]}
    raw = llm._chat(
        SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="proactive_insights",
        model=llm._model_chat_digest(),
        timeout=90,
        temperature=0.1,
        max_tokens=1200,
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    return normalize_items(obj)


def normalize_items(obj: Any) -> list[dict[str, Any]]:
    out = []
    for it in (obj.get("items") if isinstance(obj, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip()
        text = str(it.get("text") or "").strip()
        if kind not in ("agreement", "followup", "open_question") or not text:
            continue
        due = str(it.get("due") or "").strip()[:10]
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            due = ""
        out.append(
            {
                "kind": kind,
                "text": text[:200],
                "who": str(it.get("who") or "").strip()[:80],
                "due": due,
                "people": [str(p).strip()[:80] for p in (it.get("people") or []) if str(p).strip()][:6],
                "quote": str(it.get("quote") or "").strip()[:240],
            }
        )
    return out[:6]


def analyze_note(uid: int, note: dict[str, Any], text: str, content_hash: str) -> list[dict[str, Any]]:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.services.calendar import _tz_for
    from assistant.stores import meeting_links

    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    meeting = ""
    try:
        link = meeting_links.get_for_note(uid, int(note["id"]))
        if link:
            names = ", ".join(a.get("name") or a.get("email") or "" for a in link.get("attendees") or [])
            meeting = f"{link.get('summary')} ({link.get('start_iso')}); участники: {names}"
    except Exception:
        pass
    today = datetime.now(_tz_for(uid)).date()
    items = _llm_items(str(note.get("title") or ""), text, today, meeting)
    save(uid, f"note:{int(note['id'])}", content_hash, str(note.get("title") or ""), items)
    return items


def save(uid: int, source_key: str, content_hash: str, title: str, items: list[dict[str, Any]]) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO proactive_insights (owner_user_id, source_key, content_hash, analyzed_at, title, items_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, source_key) DO UPDATE SET
                content_hash = excluded.content_hash, analyzed_at = excluded.analyzed_at,
                title = excluded.title, items_json = excluded.items_json
            """,
            (str(uid), source_key, content_hash, _now().isoformat(), title, json.dumps(items, ensure_ascii=False)),
        )
        conn.commit()


def run_pending(
    uid: int, ready_ids: Optional[set[int]] = None, *, _claimed: bool = False, limit: int = PER_RUN
) -> int:
    """Разобрать до PER_RUN новых материалов (в фоне, один поток на пользователя)."""
    uid = int(uid)
    with _LOCK:
        if uid in _RUNNING and not _claimed:
            _AGAIN.setdefault(uid, set()).update(ready_ids or set())
            return 0
        _RUNNING.add(uid)
    done = 0
    try:
        budget = DAILY_CAP - analyzed_today(uid)
        for note, text, h in candidates(uid, ready_ids=ready_ids)[: max(0, min(limit, budget))]:
            try:
                analyze_note(uid, note, text, h)
                done += 1
            except Exception as e:
                print(f"[proactive_ai] note={note.get('id')} uid={uid} err={e!r}")
        budget = DAILY_CAP - analyzed_today(uid)
        for cid, day, key, h in digest_candidates(uid)[: max(0, min(limit, budget))]:
            try:
                analyze_digest(uid, cid, day, key, h)
                done += 1
            except Exception as e:
                print(f"[proactive_ai] digest={key} uid={uid} err={e!r}")
    finally:
        with _LOCK:
            again = _AGAIN.pop(uid, None)
            if again is None:
                _RUNNING.discard(uid)
    if done:
        _LAST_ANALYZED[uid] = time.time()
        print(f"[proactive_ai] uid={uid} analyzed={done}")
    if again is not None:
        # События, пришедшие во время разбора, — тем же потоком, не теряя их.
        done += run_pending(uid, again, _claimed=True)
    return done


def kick(uid: int, ready_ids: Optional[set[int]] = None, *, limit: int = PER_RUN) -> None:
    uid = int(uid)
    with _LOCK:
        if uid in _RUNNING:
            _AGAIN.setdefault(uid, set()).update(ready_ids or set())
            return
        _RUNNING.add(uid)  # сразу: лента видит «идёт разбор» (busy) ещё до старта потока
    threading.Thread(
        target=run_pending, args=(uid, ready_ids), kwargs={"_claimed": True, "limit": limit}, daemon=True
    ).start()


def analyze_now(uid: int) -> dict[str, Any]:
    """Кнопка «Найти инсайты»: разобрать все изменённые материалы сейчас, без ожидания тишины.

    Возвращает, сколько материалов ушло в разбор (queued), и упёрлись ли в дневной лимит (capped).
    """
    uid = int(uid)
    budget = max(0, DAILY_CAP - analyzed_today(uid))
    notes = candidates(uid, skip_idle=True)
    chats = digest_candidates(uid)
    pending = len(notes) + len(chats)
    queued = min(pending, budget, PER_RUN_MANUAL * 2)
    if queued:
        kick(uid, {int(n["id"]) for n, _t, _h in notes[:PER_RUN_MANUAL]}, limit=PER_RUN_MANUAL)
    return {"queued": queued, "pending": pending, "capped": bool(pending and not budget)}


def busy(uid: int) -> bool:
    """Идёт разбор или он запланирован в ближайшие секунды (саммари готово) — клиент перезапросит ленту."""
    uid = int(uid)
    with _LOCK:
        if uid in _RUNNING:
            return True
        return any(k[0] == uid and getattr(t, "_leo_ready", False) for k, t in _TIMERS.items())


def last_analyzed_at(uid: int) -> float:
    return _LAST_ANALYZED.get(int(uid), 0.0)


def _autostart() -> bool:
    return (os.getenv("PROACTIVE_AI_AUTOSTART") or "1").strip() != "0"


def note_changed(uid: Any, note_id: Any, *, ready: bool = False) -> None:
    """Материал изменился. ready=True — готов сразу (саммари); иначе — разбор после IDLE тишины.

    Повторная правка той же заметки переносит разбор (debounce), открытие ленты не нужно.
    """
    if not _autostart():
        return
    try:
        uid_i, nid = int(uid), int(note_id)
    except (TypeError, ValueError):
        return
    key = (uid_i, nid)
    delay = 1.0 if ready else IDLE.total_seconds() + 5

    def fire() -> None:
        with _LOCK:
            if _TIMERS.get(key) is timer:
                _TIMERS.pop(key, None)
        try:
            kick(uid_i, {nid} if ready else None)
        except Exception as e:
            print(f"[proactive_ai] note_changed kick failed uid={uid_i} note={nid} err={e!r}")

    timer = threading.Timer(delay, fire)
    timer.daemon = True
    timer._leo_ready = ready  # type: ignore[attr-defined]
    with _LOCK:
        old = _TIMERS.get(key)
        if old is not None:
            if getattr(old, "_leo_ready", False) and not ready:
                return  # уже стоит немедленный разбор — правка его не откладывает
            old.cancel()
        _TIMERS[key] = timer
    timer.start()


# ---------------------------------------------------------------- карточки

def _words(s: str) -> set[str]:
    # Основы слов (первые 4 буквы): «пришлю»/«прислать», «доступы»/«доступ» совпадают.
    return {w[:4] for w in re.findall(r"[a-zа-яё0-9]{4,}", str(s or "").lower().replace("ё", "е"))}


def _covered(text: str, task_titles: list[str]) -> bool:
    a = _words(text)
    if not a:
        return False
    for t in task_titles:
        b = _words(t)
        if b and len(a & b) / max(1, min(len(a), len(b))) >= 0.5:
            return True
    return False


_RU_MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _due_label(due: str, today: date) -> str:
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return ""
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return f"{d.day} {_RU_MON[d.month - 1]}"


def _is_me(who: str) -> bool:
    return str(who or "").strip().lower() in ("я", "мне", "вы", "пользователь", "me", "")


def _handled(uid: int) -> list[str]:
    """Тексты разобранных пунктов («Сделано», ✕, связано, отложено…) — их не поднимаем снова."""
    from assistant.services import proactive

    try:
        return proactive.done_titles(uid)
    except Exception as e:
        print(f"[proactive_ai] done titles failed uid={uid} err={e!r}")
        return []



def insight_cards(uid: int, *, now: Optional[datetime] = None, task_titles: Optional[list[str]] = None) -> list[dict[str, Any]]:
    from assistant.services.calendar import _tz_for
    from assistant.stores import notes as notes_store

    handled = _handled(uid)
    task_titles = list(task_titles or []) + handled
    now = now or _now()
    today = now.astimezone(_tz_for(uid)).date()
    with _LOCK:
        rows = _conn().execute(
            "SELECT source_key, title, items_json, analyzed_at FROM proactive_insights WHERE owner_user_id = ? ORDER BY analyzed_at DESC LIMIT 30",
            (str(uid),),
        ).fetchall()
    cards: list[dict[str, Any]] = []
    for r in rows:
        try:
            items = json.loads(r["items_json"] or "[]")
        except ValueError:
            continue
        src_key = str(r["source_key"])
        nid = int(src_key.split(":", 1)[1]) if src_key.startswith("note:") else 0
        note = notes_store.get_note(uid, nid) if nid else None
        if not note:
            continue  # заметку удалили
        title = str(note.get("title") or r["title"] or "заметка")
        kind_note = "transcription" if str(note.get("role") or "") == "transcription" else "note"
        src = f"из «{title}»"
        meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
        has_summary_tasks = kind_note == "transcription" and bool(meta.get("tasks"))
        for it in items:
            if has_summary_tasks and it.get("kind") == "agreement":
                continue  # задачи из саммари уже предложены отдельной карточкой
            card = card_for(
                it, src=src, note={"id": nid, "kind": kind_note, "title": title}, today=today,
                task_titles=task_titles or [], handled_titles=handled,
            )
            if card:
                cards.append(card)
    return cards


def card_for(
    it: dict[str, Any], *, src: str, note: dict[str, Any], today: date, task_titles: list[str],
    handled_titles: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    kind, text, who, due = it.get("kind"), str(it.get("text") or ""), str(it.get("who") or ""), str(it.get("due") or "")
    if handled_titles and _covered(text, handled_titles):
        return None  # уже разобран (выполнен, скрыт, связан…) — в любой формулировке
    key = "ins:%s:%s" % (note["id"], _hash(kind or "", text)[:10])
    due_l = _due_label(due, today) if due else ""
    if due:
        try:
            if date.fromisoformat(due) < today - timedelta(days=14):
                return None
        except ValueError:
            pass
    base = {"key": key, "source": src + (f" · срок {due_l}" if due_l else ""), "note": note, "ask": f"{text} ({src})"}
    if kind == "agreement":
        if _covered(text, task_titles):
            return None
        me = _is_me(who)
        urgent = bool(due) and due <= (today + timedelta(days=1)).isoformat()
        body = text
        if who and body.lower().startswith(who.lower()):
            body = body[len(who):].lstrip(" ,:—-")
        body = body[:1].lower() + body[1:] if body else text
        return {
            **base,
            "type": "agreement",
            "tag": "🤝 договорённость",
            "urgency": 3 if urgent else 2,
            "title": (f"Вы обещали: {body}" if me else f"{who} — {body}"),
            "actions": [
                {
                    "label": ("Себе" if me else "Поставить задачу") + (f" на {due_l}" if due_l else ""),
                    "act": "assistant",
                    "primary": True,
                    "payload": {
                        "prompt": f"Поставь задачу «{text}» исполнителю {'мне' if me else who}"
                        + (f" на {due}" if due else "")
                        + f" — договорённость {src}. Если исполнителя нет в контактах, поставь мне и скажи."
                    },
                },
                {"label": "Открыть", "act": "open_note"},
                {"label": "✓ Сделано", "act": "mark_done"},
            ],
            "items": [{"id": key, "text": text}],
            "_item": {"text": text, "who": "" if me else who, "due": due},
        }
    if kind == "followup":
        people = ", ".join(it.get("people") or []) or who
        return {
            **base,
            "type": "followup",
            "tag": "📅 встреча",
            "urgency": 2,
            "title": text,
            "source": base["source"] + (f" · с {people}" if people else ""),
            "actions": [
                {
                    "label": "Найти время",
                    "act": "assistant",
                    "primary": True,
                    "payload": {
                        "prompt": f"Найди общее свободное время на ближайшие дни для встречи «{text}»"
                        + (f" с {people}" if people else "")
                        + " и предложи 3 варианта."
                    },
                },
                {"label": "Открыть", "act": "open_note"},
            ],
        }
    if kind == "open_question":
        return {
            **base,
            "type": "open_question",
            "tag": "❓ открытый вопрос",
            "urgency": 1,
            "title": f"Не решено: {text}",
            "actions": [
                {
                    "label": "Обсудить",
                    "act": "assistant",
                    "primary": True,
                    "payload": {"prompt": f"Помоги решить открытый вопрос «{text}» ({src}): какие варианты и что нужно, чтобы решить?"},
                },
                {"label": "Открыть", "act": "open_note"},
            ],
        }
    return None


# ---------------------------------------------------------------- важное из чатов (дайджест)

CHAT_SYSTEM = """Ты — помощник руководителя. Тебе дают переписку рабочего чата за день; сообщения пользователя помечены «Я».
Найди только то, что важно именно пользователю:
- request — просьба или вопрос к пользователю, на который он не ответил;
- promise — пользователь пообещал что-то сделать;
- escalation — недовольство, конфликт, жалоба клиента, эскалация;
- deadline — срок, который касается пользователя или его команды;
- blocker — работа стоит и ждёт решения/действия.
Ничего не придумывай, бери только из текста. Ответ строго JSON:
{"items":[{"kind":"request|promise|escalation|deadline|blocker","text":"коротко, до 120 символов","who":"кто написал/о ком","msg":номер сообщения,"due":"YYYY-MM-DD или ''"}]}
Не больше 5 пунктов. Если ничего важного — {"items":[]}."""

CHAT_KINDS = ("request", "promise", "escalation", "deadline", "blocker")


def _me_identity(uid: int) -> tuple[str, str]:
    try:
        from assistant.stores import note_members

        prof = note_members.get_profile(uid) or {}
        return note_members.profile_display_name(prof, fallback=""), str(prof.get("username") or "")
    except Exception:
        return "", ""


def chat_transcript_for(uid: int, messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        mine = m.get("user_id") is not None and int(m.get("user_id")) == int(uid)
        who = "Я" if mine else (str(m.get("display_name") or "").strip() or (f"@{m['username']}" if m.get("username") else "кто-то"))
        ts = str(m.get("ts_utc") or "")
        lines.append(f"#{m.get('message_id')} [{ts[11:16]}] {who}: {text[:600]}")
    return "\n".join(lines)


def normalize_chat_items(obj: Any) -> list[dict[str, Any]]:
    out = []
    for it in (obj.get("items") if isinstance(obj, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip()
        text = str(it.get("text") or "").strip()
        if kind not in CHAT_KINDS or not text:
            continue
        try:
            msg = int(it.get("msg") or 0)
        except (TypeError, ValueError):
            msg = 0
        due = str(it.get("due") or "").strip()[:10]
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            due = ""
        out.append({"kind": kind, "text": text[:200], "who": str(it.get("who") or "").strip()[:80], "msg": msg, "due": due})
    return out[:5]


def digest_candidates(uid: int) -> list[tuple[int, str, str, str]]:
    """Отчёты дайджеста по выбранным чатам за 3 дня, ещё не разобранные «для меня» в текущем виде."""
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    if not user_prefs.digest_enabled(uid) or not user_prefs.proactive_prefs(uid).get("chats"):
        return []
    chats = [int(c) for c in user_prefs.digest_chat_ids(uid)]
    if not chats:
        return []
    since = (date.today() - timedelta(days=3)).isoformat()
    with _LOCK:
        done = {
            str(r["source_key"]): str(r["content_hash"])
            for r in _conn().execute(
                "SELECT source_key, content_hash FROM proactive_insights WHERE owner_user_id = ? AND source_key LIKE 'digest:%'",
                (str(uid),),
            ).fetchall()
        }
    out = []
    for rep in digest_store.list_reports_for_chats(chats, limit=60):
        day = str(rep.get("report_date") or "")
        if day < since or rep.get("is_demo"):
            continue
        cid = int(rep.get("chat_id"))
        key = f"digest:{cid}:{day}"
        h = _hash(str(rep.get("generated_at") or ""), str(rep.get("message_count") or ""))
        if done.get(key) == h:
            continue
        out.append((cid, day, key, h))
    return out


def analyze_digest(uid: int, chat_id: int, day: str, key: str, content_hash: str) -> list[dict[str, Any]]:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.lib.llm_json import strip_json_from_markdown
    from assistant.nlu import llm
    from assistant.stores import chat_digest as digest_store

    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    messages = digest_store.messages_for_chat_date(chat_id, day)
    chat = digest_store.get_chat(chat_id) or {}
    title = str(chat.get("title") or f"Чат {chat_id}")
    items: list[dict[str, Any]] = []
    if messages:
        name, username = _me_identity(uid)
        payload = {
            "chat": title,
            "date": day,
            "me": {"name": name, "username": username},
            "messages": chat_transcript_for(uid, messages)[:MAX_TEXT],
        }
        raw = llm._chat(
            CHAT_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
            operation="proactive_chat_insights",
            model=llm._model_chat_digest(),
            timeout=90,
            temperature=0.1,
            max_tokens=900,
        )
        try:
            items = normalize_chat_items(json.loads(strip_json_from_markdown(raw)))
        except (json.JSONDecodeError, TypeError):
            items = []
    save(uid, key, content_hash, title, items)
    return items


def chat_message_url(chat_id: int, msg: int) -> str:
    s = str(int(chat_id))
    if s.startswith("-100") and msg:
        return f"https://t.me/c/{s[4:]}/{int(msg)}"
    return ""


_CHAT_TAGS = {
    "request": "💬 ждут ответа",
    "promise": "🤝 вы обещали",
    "escalation": "🔥 эскалация",
    "deadline": "⏰ срок",
    "blocker": "⛔ блокер",
}


def chat_card(
    it: dict[str, Any], *, chat_id: int, day: str, title: str, today: date, task_titles: list[str],
    handled_titles: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    kind, text, who = it.get("kind"), str(it.get("text") or ""), str(it.get("who") or "")
    if kind not in CHAT_KINDS:
        return None
    if handled_titles and _covered(text, handled_titles):
        return None
    if kind in ("promise", "request") and _covered(text, task_titles):
        return None
    key = "chat:%s:%s:%s" % (chat_id, day, _hash(kind or "", text)[:10])
    try:
        d = date.fromisoformat(day)
        day_l = "сегодня" if d == today else ("вчера" if d == today - timedelta(days=1) else f"{d.day} {_RU_MON[d.month - 1]}")
    except ValueError:
        day_l = day
    url = chat_message_url(chat_id, int(it.get("msg") or 0))
    src = f"чат «{title}» · {day_l}" + (f" · {who}" if who and kind != "promise" else "")
    acts: list[dict[str, Any]] = []
    if kind == "promise":
        acts.append({"label": "Напомнить себе", "act": "assistant", "primary": True,
                     "payload": {"prompt": f"Поставь мне задачу «{text}»" + (f" на {it.get('due')}" if it.get("due") else "") + f" — обещал в чате «{title}»."}})
    elif kind == "escalation" and who:
        acts.append({"label": "Встреча", "act": "assistant", "primary": True,
                     "payload": {"prompt": f"Найди ближайшее общее свободное время для встречи с {who} по вопросу «{text}» (чат «{title}») и предложи 3 варианта."}})
    else:
        acts.append({"label": "Задача себе", "act": "assistant", "primary": True,
                     "payload": {"prompt": f"Поставь мне задачу: {text} (из чата «{title}», {day_l})" + (f" на {it.get('due')}" if it.get("due") else "") + "."}})
    if url:
        acts.append({"label": "Открыть чат", "act": "url", "payload": {"url": url}})
    if kind in ("promise", "request"):
        acts.append({"label": "✓ Сделано", "act": "mark_done"})
    return {
        "key": key,
        "type": "chat_" + str(kind),
        "tag": _CHAT_TAGS.get(str(kind), "💬 важное из чатов"),
        "urgency": 3 if kind in ("request", "escalation", "blocker") else 2,
        "title": text,
        "source": src,
        "ask": f"{text} ({src})",
        "chat": {"id": chat_id, "date": day, "title": title},
        "actions": acts,
        "items": [{"id": key, "text": text}],
        "_item": {"text": text, "who": who if kind != "promise" else "", "due": str(it.get("due") or "")},
    }


def chat_cards(uid: int, *, task_titles: Optional[list[str]] = None, chat_id: Optional[int] = None, day: Optional[str] = None) -> list[dict[str, Any]]:
    from assistant.services.calendar import _tz_for

    handled = _handled(uid)
    task_titles = list(task_titles or []) + handled
    today = datetime.now(_tz_for(uid)).date()
    pattern = f"digest:{chat_id}:{day}" if chat_id is not None and day else "digest:%"
    with _LOCK:
        rows = _conn().execute(
            "SELECT source_key, title, items_json FROM proactive_insights WHERE owner_user_id = ? AND source_key LIKE ? ORDER BY analyzed_at DESC LIMIT 40",
            (str(uid), pattern),
        ).fetchall()
    out = []
    for r in rows:
        try:
            _, cid, d = str(r["source_key"]).split(":", 2)
            items = json.loads(r["items_json"] or "[]")
        except ValueError:
            continue
        for it in items:
            c = chat_card(
                it, chat_id=int(cid), day=d, title=str(r["title"] or ""), today=today,
                task_titles=task_titles or [], handled_titles=handled,
            )
            if c:
                out.append(c)
    return out
