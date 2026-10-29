"""Задачи встречи: одна таблица в общей заметке, у каждого пункта один статус.

Источник правды — meta.tasks заметки встречи (её видят все участники). Статус пункта считается
кодом: из отметок разбора (Фокус, таблица) и из связанной задачи — её разбор у исполнителя
(ждёт, принято, в работе, вопрос, отказ) и выполнение. В тексте саммари задачи — таблица
«Задача | Статус | Ответственный | Дедлайн»: ID пункта на первой ячейке строки, остальные
ячейки сервер переписывает из meta (приложение рисует поверх живой статус).
"""

from __future__ import annotations

import html as _h
import re
import threading
from datetime import datetime
from typing import Any, Optional

# «Не задача» / «Дубль» — про сам пункт: не в счётчике, строка серая внизу таблицы.
# «Возможно, не задача» — проверка по транскрипту усомнилась: тоже внизу, но вернуть — одним нажатием.
EXCLUDED = ("not_task", "duplicate", "doubtful")
# Ещё ждут решения: в счётчике «Разобрано N из M» не считаются разобранными.
UNDECIDED = ("open", "declined")
# «Не разбирать» у всей встречи (задачи ведут в другом трекере): неразобранные пункты — «Не разбираем»,
# в Фокус и в счётчик не идут; взять пункт можно и так.
SKIPPED = "skipped"
CLAIMABLE = UNDECIDED + ("doubtful", SKIPPED)

LABELS = {
    "open": "Не разобрано",
    "taken": "Взял",
    "assigned": "Поручено · ждёт",
    "accepted": "Принято",
    "in_progress": "В работе",
    "question": "Вопрос",
    "declined": "Отказ",
    "tracker": "В трекере",
    "done": "Сделано",
    "not_task": "Не задача",
    "duplicate": "Дубль",
    "doubtful": "Возможно, не задача",
    "skipped": "Не разбираем",
}

_DOUBT_KIND = {"decision": "это решение", "question": "это вопрос", "not_task": "не похоже на задачу", "duplicate": "похоже на дубль"}


def merge_doubtful(meta: dict[str, Any]) -> None:
    """Пункты, которые проверка по транскрипту отбросила, — в meta.tasks с пометкой doubtful (один раз).

    Ничего не теряется: в таблице они свёрнуты внизу «Возможно, не задача», вернуть — одним нажатием."""
    dropped = [d for d in meta.get("tasks_dropped") or [] if isinstance(d, dict) and str(d.get("task") or "").strip()]
    if not dropped:
        return
    tasks = list(meta.get("tasks") or [])
    for d in dropped:
        tasks.append({
            "assignee": str(d.get("assignee") or ""), "task": str(d["task"]).strip(), "deadline": str(d.get("deadline") or ""),
            "doubtful": str(d.get("kind") or "not_task"),
            "doubt_reason": str(d.get("reason") or "") or _DOUBT_KIND.get(str(d.get("kind") or ""), ""),
        })
    meta["tasks"] = tasks
    meta["tasks_dropped"] = []

HEADERS = ("Задача", "Статус", "Ответственный", "Дедлайн")
TABLE_HEADING = "📋 Задачи"

_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

# «Беру» / «Поручить» двоих участников одновременно: пункт достаётся первому.
_CLAIM_LOCK = threading.RLock()


class Taken(Exception):
    """Пункт уже разобран кем-то другим — вторую задачу не ставим."""

    def __init__(self, state: dict[str, Any]):
        self.state = state
        who = str(state.get("by") or "").strip()
        text = state.get("label") or "Уже разобрано"
        if state.get("code") == "taken" and who:
            text = f"Уже взял {who}"
        elif who and state.get("code") not in ("not_task", "duplicate"):
            text = f"{text} · {who}"
        super().__init__(text)


# ---------------------------------------------------------------- статус пункта


def _task_row(task_id: Any) -> Optional[dict[str, Any]]:
    from assistant.stores import calendar_tasks

    try:
        return calendar_tasks._get_task_row(int(task_id))
    except (TypeError, ValueError):
        return None
    except Exception as e:
        print(f"[meeting_tasks] task lookup failed id={task_id} err={e!r}")
        return None


def _short_date(iso: str, owner: Any = None) -> str:
    """«31 окт» в поясе постановщика: задача «на весь день» хранится полуночью его дня в UTC."""
    try:
        d = datetime.fromisoformat(str(iso or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if d.tzinfo is not None and owner:
        try:
            from assistant.stores import user_prefs

            d = d.astimezone(user_prefs.get_user_tz(int(owner)))
        except Exception:
            pass
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _linked_task_id(t: dict[str, Any]) -> Optional[int]:
    link = t.get("link") if isinstance(t.get("link"), dict) else {}
    try:
        return int(link["task_id"]) if link.get("task_id") else None
    except (TypeError, ValueError):
        return None


def status_of(t: dict[str, Any], task: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Статус пункта: code, label, by (кто разобрал), who (ответственный), due, task_id, note.

    task — связанная задача (строка calendar_tasks), если она есть: её состояние важнее отметки."""
    st = str(t.get("focus_status") or "")
    reason = str(t.get("focus_reason") or "")
    by = str(t.get("completed_by") or "").strip()
    link = t.get("link") if isinstance(t.get("link"), dict) else {}
    out: dict[str, Any] = {
        "code": "open",
        "by": by,
        "who": str(t.get("assignee") or "").strip(),
        "due": str(t.get("deadline") or "").strip(),
        "task_id": None,
        "url": str(link.get("url") or ""),
        "note": "",
        "at": str(t.get("completed_at") or ""),
    }
    if st == "dismissed" and reason in ("not_a_task", "duplicate"):
        out["code"] = "duplicate" if reason == "duplicate" else "not_task"
    elif t.get("doubtful") and not st and not t.get("completed") and not link:
        out["code"] = "doubtful"  # проверка усомнилась, а человек ещё не решал
        out["note"] = str(t.get("doubt_reason") or "")
    elif st == "retyped":
        out["code"] = "not_task"  # решение / мысль / напоминание — в счётчик задач не идёт
    elif task:
        out["task_id"] = int(task["id"])
        out["due"] = _short_date(str(task.get("start_at") or ""), task.get("owner_user_id")) or out["due"]
        delegated = str(task.get("owner_user_id") or "") != str(task.get("assignee_user_id") or "")
        triage = str(task.get("assignee_triage") or "")
        astat = str(task.get("assignee_status") or "")
        if delegated:
            out["who"] = str(task.get("assignee_name") or "").strip() or out["who"]
        else:
            out["who"] = by or out["who"]
        if task.get("done"):
            out["code"] = "done"
        elif delegated and triage == "declined":
            out["code"] = "declined"
            out["note"] = str(task.get("assignee_status_note") or "").strip()
        elif astat == "in_progress":
            out["code"] = "in_progress"
        elif astat == "question":
            out["code"] = "question"
            out["note"] = str(task.get("assignee_status_note") or "").strip()
        elif delegated and triage in ("inbox", "snoozed"):
            out["code"] = "assigned"
        elif delegated:
            out["code"] = "accepted"
        else:
            out["code"] = "taken"
    elif out["url"] and st in ("linked", "converted"):
        out["code"] = "tracker"
        out["stage"] = str(link.get("stage") or "")
    elif st in ("completed",) or (t.get("completed") and st not in ("converted", "linked")):
        out["code"] = "done"
    elif st in ("converted", "linked"):
        out["code"] = "taken"  # задача есть, но её не видно (удалили / нет доступа)
    out["label"] = label_for(out)
    return out


def label_for(s: dict[str, Any]) -> str:
    code = s.get("code") or "open"
    if code == "taken":
        return f"Взял {s['by']}" if s.get("by") else "Взято"
    if code == "declined" and s.get("note"):
        return f"Отказ: {s['note']}"
    if code == "tracker" and s.get("stage"):
        return f"В трекере · {s['stage']}"
    return LABELS.get(code, LABELS["open"])


def _task_rows_for(tasks: list[dict[str, Any]], posted: Optional[list[dict[str, Any]]] = None) -> dict[int, dict[str, Any]]:
    """Связанные задачи пунктов: по ссылке в пункте, а у старых — по названию среди поставленных."""
    out: dict[int, dict[str, Any]] = {}
    for i, t in enumerate(tasks):
        tid = _linked_task_id(t)
        if tid is None and posted is not None and str(t.get("focus_status") or "") != "dismissed":
            from assistant.services.meeting_share import task_state

            st = task_state(t, posted) or {}
            link = st.get("link") or {}
            if link.get("task_id"):
                tid = int(link["task_id"])
                t.setdefault("completed_by", st.get("by") or "")
        if tid is not None:
            row = _task_row(tid)
            if row:
                out[i] = row
    return out


# ---------------------------------------------------------------- кому решать


def participants(note: dict[str, Any]) -> list[dict[str, Any]]:
    """Владелец заметки встречи и участники, с кем она открыта: uid и имена для сопоставления."""
    from assistant.stores import note_members

    owner = str(note.get("owner_user_id") or note.get("user_id") or "")
    if not owner:
        return []
    uids = [owner]
    try:
        uids += [str(m.get("member_user_id") or "") for m in note_members.list_member_rows(owner, int(note["id"]))]
    except Exception as e:
        print(f"[meeting_tasks] members failed note={note.get('id')} err={e!r}")
    out = []
    for uid in dict.fromkeys(u for u in uids if u):
        prof = note_members.get_profile(uid) or {}
        names = [str(prof.get(k) or "").strip() for k in ("first_name", "last_name", "username")]
        full = " ".join(x for x in names[:2] if x)
        out.append({"uid": uid, "names": [n for n in names + [full] if n], "name": full,
                    "first": names[0], "last": names[1]})
    return out


def _who_is(name: str, parts: list[dict[str, Any]]) -> str:
    """Участник по имени исполнителя из саммари (Аня ↔ Анна, «Вова П.»): ровно один — его uid."""
    from assistant.lib.name_equivalence import names_equivalent

    words = [w.strip(".") for w in re.split(r"[\s,;]+", str(name or "")) if w.strip(".")]
    if not words:
        return ""
    hits = []
    for p in parts:
        first, last = p.get("first") or "", p.get("last") or ""
        if not (first and names_equivalent(words[0], first)) and not any(
            n.lower() == str(name).strip().lower() for n in p["names"]
        ):
            continue
        if len(words) > 1 and last and not last.lower().startswith(words[1].lower()):
            continue  # «Аня К.» — не «Анна Смирнова»
        hits.append(p["uid"])
    return hits[0] if len(hits) == 1 else ""


def decider(note: dict[str, Any], t: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    """Кто решает судьбу пункта: исполнитель из участников с Лео, иначе — владелец записи встречи.

    Исполнитель ответил «Не моя» — пункт возвращается владельцу."""
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    owner = str(note.get("owner_user_id") or note.get("user_id") or "")
    refused = {str(x) for x in t.get("not_me") or []}
    uid = ""
    who = str(t.get("assignee") or "").strip()
    smap = meta.get("speaker_map") if isinstance(meta.get("speaker_map"), dict) else {}
    for info in smap.values():  # «Это я» в «Кто есть кто»: имя спикера → тот, кто себя отметил
        if isinstance(info, dict) and info.get("user_id") and who and str(info.get("name") or "").strip() == who:
            uid = str(info["user_id"])
    if not uid and who:
        uid = _who_is(who, parts)
    member = {p["uid"] for p in parts}
    if not uid or uid not in member or uid in refused:
        return owner
    return uid


def rows(note: dict[str, Any], posted: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Таблица задач встречи: пункты со статусами, кто решает, и счётчик «Разобрано N из M».

    Пункты — в порядке meta.tasks (без пустых). posted — задачи, уже поставленные из встречи
    (если их уже собрали); иначе собираем сами."""
    from assistant.services.meeting_share import posted_for_note

    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    tasks = [dict(t) for t in meta.get("tasks") or [] if isinstance(t, dict) and str(t.get("task") or "").strip()]
    if posted is None:
        try:
            posted = posted_for_note(note) if tasks else []
        except Exception as e:
            print(f"[meeting_tasks] posted lookup failed note={note.get('id')} err={e!r}")
            posted = []
    linked = _task_rows_for(tasks, posted)
    parts = participants(note) if tasks and note.get("id") is not None else []
    items = []
    skip = skipped_info(meta)
    for i, t in enumerate(tasks):
        s = status_of(t, linked.get(i))
        if skip and s["code"] in UNDECIDED:
            s.update(code=SKIPPED, label=LABELS[SKIPPED], note="")
        dec = decider(note, t, parts) if parts else ""
        if s["code"] == "declined":  # исполнитель отказался — пункт снова у владельца записи
            dec = str(note.get("owner_user_id") or note.get("user_id") or dec)
        items.append({"id": str(t.get("id") or ""), "task": str(t.get("task") or "").strip(), "decider": dec, **s})
    live = [x for x in items if x["code"] not in EXCLUDED]
    decided = sum(1 for x in live if x["code"] not in UNDECIDED)
    out = {"items": items, "decided": decided, "total": len(live)}
    if skip:
        out["skipped"] = skip
    return out


def skipped_info(meta: Any) -> Optional[dict[str, Any]]:
    """{by, at}, если задачи встречи решили не разбирать."""
    v = meta.get("tasks_skip") if isinstance(meta, dict) else None
    return dict(v) if isinstance(v, dict) else None


# ---------------------------------------------------------------- таблица в тексте саммари


def _e(s: Any) -> str:
    return _h.escape(str(s or "").strip())


def _cell(text: str, tag: str = "td", attrs: str = "") -> str:
    return f"<{tag}{attrs}><p>{_e(text)}</p></{tag}>"


def row_html(item_id: str, task_text: str, s: dict[str, Any], *, first_cell: Optional[str] = None) -> str:
    first = first_cell or _cell(task_text, attrs=f' data-leo-item="{_e(item_id)}"' if item_id else "")
    return "<tr>" + first + _cell(s.get("label") or "") + _cell(s.get("who") or "—") + _cell(s.get("due") or "—") + "</tr>"


def table_html(tasks: list[dict[str, Any]]) -> str:
    """Раздел «Задачи» — таблица. Статусы по meta (без запросов к задачам: при создании их ещё нет)."""
    from assistant.lib import note_items

    body = []
    tail = []
    for t in tasks or []:
        if not isinstance(t, dict) or not str(t.get("task") or "").strip():
            continue
        if not note_items.ITEM_ID_RE.match(str(t.get("id") or "")):
            t["id"] = note_items.new_item_id()
        s = status_of(t)
        (tail if s["code"] in EXCLUDED else body).append(row_html(str(t["id"]), str(t.get("task") or ""), s))
    if not body and not tail:
        return ""
    head = "<tr>" + "".join(_cell(h, "th") for h in HEADERS) + "</tr>"
    return f"<h2>{_e(TABLE_HEADING)}</h2><table><tbody>{head}{''.join(body + tail)}</tbody></table>"


_TASK_HEAD_RE = re.compile(r"<h([1-4])\b[^>]*>((?:(?!</h\1>).)*?задач(?:(?!</h\1>).)*?)</h\1>\s*", re.I | re.S)
_LIST_RE = re.compile(r"<(ul|ol)\b[^>]*>.*?</\1>\s*", re.S)


def replace_tasks_section(html_text: str, tasks: list[dict[str, Any]]) -> str:
    """Списки задач саммари («📋 Задачи», «✅ Выполненные задачи») → одна таблица на месте первого.

    Нет такого раздела — таблица в конце. Нет задач — тело как есть."""
    table = table_html(tasks)
    if not table:
        return html_text
    src = str(html_text or "")
    spans: list[tuple[int, int]] = []
    for m in _TASK_HEAD_RE.finditer(src):
        lst = _LIST_RE.match(src, m.end())
        if lst:
            spans.append((m.start(), lst.end()))
    if not spans:
        return src.rstrip() + table
    out = src[: spans[0][0]] + table
    for (_, end), (start, _) in zip(spans, spans[1:]):
        out += src[end:start]
    return out + src[spans[-1][1]:]


_ROW_RE = re.compile(r"<tr\b[^>]*>(?:(?!</tr>).)*?</tr>", re.S)
_FIRST_CELL_RE = re.compile(r"<(td|th)\b[^>]*>.*?</\1>", re.S)
_ITEM_IN_RE = re.compile(r'data-leo-item="(i[0-9a-f]{10})"')


def has_table(html_text: str) -> bool:
    return bool(re.search(r"<td\b[^>]*data-leo-item=", str(html_text or "")))


def refresh_body(html_text: str, state: dict[str, Any]) -> str:
    """Переписать ячейки «Статус / Ответственный / Дедлайн» строк таблицы задач по состоянию.

    Первую ячейку (формулировку) не трогаем — её могли поправить руками. «Не задачи» и
    «Дубли» уходят в конец таблицы."""
    src = str(html_text or "")
    if not has_table(src):
        return src
    by_id = {x["id"]: x for x in state.get("items") or [] if x.get("id")}
    rows_found = [(m, _ITEM_IN_RE.search(m.group(0))) for m in _ROW_RE.finditer(src)]
    rows_found = [(m, im.group(1)) for m, im in rows_found if im and im.group(1) in by_id]
    if not rows_found:
        return src
    out: list[str] = []
    run: list[tuple[str, bool]] = []  # строки одной таблицы подряд: «Не задачи» — в конец

    def flush() -> None:
        out.append("".join([r for r, ex in run if not ex] + [r for r, ex in run if ex]))
        run.clear()

    pos = 0
    for m, iid in rows_found:
        gap = src[pos:m.start()]
        if gap.strip() and run:
            flush()
        if gap.strip() or not run:
            out.append(gap)
        first = _FIRST_CELL_RE.search(m.group(0))
        run.append((row_html(iid, "", by_id[iid], first_cell=first.group(0) if first else None), by_id[iid]["code"] in EXCLUDED))
        pos = m.end()
    if run:
        flush()
    out.append(src[pos:])
    return "".join(out)


# ---------------------------------------------------------------- действия


def _owner_note(viewer_uid: Any, note_id: int) -> tuple[str, dict[str, Any]]:
    from assistant.stores import notes as notes_store

    note = notes_store.get_accessible_note(viewer_uid, int(note_id), with_sharing=False)
    if not note:
        raise LookupError("Заметка не найдена")
    owner = str(note.get("owner_user_id") or note.get("user_id") or viewer_uid)
    full = notes_store.get_note(owner, int(note_id))
    if not full:
        raise LookupError("Заметка не найдена")
    return owner, full


def _find(meta: dict[str, Any], item_id: str) -> int:
    for i, t in enumerate(meta.get("tasks") or []):
        if isinstance(t, dict) and str(t.get("id") or "") == str(item_id):
            return i
    return -1


def state_for(viewer_uid: Any, note_id: int, item_id: str) -> dict[str, Any]:
    _, note = _owner_note(viewer_uid, note_id)
    for x in rows(note)["items"]:
        if x["id"] == item_id:
            return x
    raise LookupError("Пункт не найден — обновите заметку")


def claim(viewer_uid: Any, note_id: int, item_id: str, create: Any, *, by: str) -> dict[str, Any]:
    """«Беру» / «Поручить»: create() ставит задачу, только если пункт ещё ничей. Иначе — Taken.

    Проверка и запись связи — под одним замком: второй одновременный «Беру» получит «Уже взял»."""
    with _CLAIM_LOCK:
        s = state_for(viewer_uid, note_id, item_id)
        if s["code"] not in CLAIMABLE:
            raise Taken(s)
        task = create()
        if not task or task.get("id") is None:
            return task
        attach_task(viewer_uid, note_id, item_id, task, by=by)
        return task


def claim_tracker(viewer_uid: Any, note_id: int, item_id: str, create: Any, *, by: str) -> Any:
    """Задача в трекере (Битрикс24) из пункта: create() → (результат, {url, title, stage}).

    Как claim: пункт должен быть ничей; после — у пункта ссылка, статус «В трекере»."""
    with _CLAIM_LOCK:
        s = state_for(viewer_uid, note_id, item_id)
        if s["code"] not in CLAIMABLE:
            raise Taken(s)
        result, link = create()
        if link and link.get("url"):
            _attach_link(viewer_uid, note_id, item_id, link, by=by)
        return result


def _attach_link(viewer_uid: Any, note_id: int, item_id: str, link: dict[str, Any], *, by: str) -> None:
    owner, note = _owner_note(viewer_uid, note_id)
    meta = dict(note.get("meta") or {})
    tasks = list(meta.get("tasks") or [])
    i = _find(meta, item_id)
    if i < 0:
        return
    t = dict(tasks[i])
    t["link"] = {k: str(v)[:300] for k, v in link.items() if v}
    t.update(focus_status="linked", completed=False, completed_by=by,
             completed_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    t.pop("focus_reason", None)
    tasks[i] = t
    meta["tasks"] = tasks
    _save(owner, note, meta)


def attach_task(viewer_uid: Any, note_id: int, item_id: str, task: dict[str, Any], *, by: str) -> None:
    """Пункт встречи стал задачей: ссылка в meta.tasks, ячейки таблицы, Фокус участников."""
    from assistant.stores import notes as notes_store

    owner, note = _owner_note(viewer_uid, note_id)
    meta = dict(note.get("meta") or {})
    tasks = list(meta.get("tasks") or [])
    i = _find(meta, item_id)
    if i < 0:
        return
    t = dict(tasks[i])
    t["link"] = {"task_id": int(task["id"]), "title": str(task.get("title") or "")[:200]}
    t["focus_status"] = "converted"
    t["completed"] = True  # как у «В задачу» из Фокуса: пункт закрыт у всех
    t["completed_by"] = by
    t["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    t.pop("focus_reason", None)
    tasks[i] = t
    meta["tasks"] = tasks
    _save(owner, note, meta)


def apply(viewer_uid: Any, note_id: int, item_id: str, op: str, *, by: str, **kw: Any) -> dict[str, Any]:
    """Действие из таблицы: done / dismiss (reason not_a_task|duplicate) / undo / update (fields)."""
    from assistant.services.focus_items import FIELDS

    owner, note = _owner_note(viewer_uid, note_id)
    meta = dict(note.get("meta") or {})
    tasks = list(meta.get("tasks") or [])
    i = _find(meta, item_id)
    if i < 0:
        raise LookupError("Пункт не найден — обновите заметку")
    t = dict(tasks[i])
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if op == "done":
        t.update(completed=True, focus_status="completed", completed_by=by, completed_at=now)
        t.pop("focus_reason", None)
    elif op == "dismiss":
        reason = str(kw.get("reason") or "")
        if reason not in ("not_a_task", "duplicate"):
            raise ValueError("Неизвестная причина")
        t.update(completed=False, focus_status="dismissed", focus_reason=reason, completed_by=by, completed_at=now)
    elif op == "undo":
        if _linked_task_id(t):
            raise ValueError("По пункту поставлена задача — измените её саму")
        for k in ("completed_by", "completed_at", "focus_reason", "link", "doubtful", "doubt_reason"):
            t.pop(k, None)  # и «Возможно, не задача» → обычный пункт
        t.update(completed=False, focus_status="new")
    elif op == "update":
        for k, v in (kw.get("fields") or {}).items():
            if k in FIELDS and k in ("assignee", "task", "deadline"):
                t[k] = str(v or "").strip()[:400]
                if k == "assignee" and "assignee_basis" in t:
                    t.update(assignee_basis="named", assignee_name=t[k], assignee_unresolved=False)
    else:
        raise ValueError("Неизвестное действие")
    tasks[i] = t
    meta["tasks"] = tasks
    _save(owner, note, meta)
    return state_for(viewer_uid, note_id, item_id)


def set_skip(viewer_uid: Any, note_id: int, skip: bool, *, by: str) -> dict[str, Any]:
    """«Не разбирать» / «Разбирать» для всех задач встречи — для всех участников."""
    owner, note = _owner_note(viewer_uid, note_id)
    meta = dict(note.get("meta") or {})
    if skip:
        meta["tasks_skip"] = {"by": by, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    else:
        meta.pop("tasks_skip", None)
    _save(owner, note, meta)
    return rows({**note, "meta": meta})


def not_me(viewer_uid: Any, note_id: int, item_id: str) -> None:
    """«Не моя» от исполнителя: пункт у всех открыт, но решать его теперь владельцу записи."""
    owner, note = _owner_note(viewer_uid, note_id)
    meta = dict(note.get("meta") or {})
    tasks = list(meta.get("tasks") or [])
    i = _find(meta, item_id)
    if i < 0:
        return
    t = dict(tasks[i])
    refused = [str(x) for x in t.get("not_me") or []]
    if str(viewer_uid) in refused:
        return
    t["not_me"] = refused + [str(viewer_uid)]
    tasks[i] = t
    meta["tasks"] = tasks
    _save(owner, note, meta)


def _save(owner: str, note: dict[str, Any], meta: dict[str, Any]) -> None:
    from assistant.stores import notes as notes_store

    body = str(note.get("body") or "")
    new_body = refresh_body(body, rows({**note, "meta": meta}))
    notes_store.update_note(owner, int(note["id"]), meta=meta, body=new_body if new_body != body else None)
    notify(owner, int(note["id"]))


def notify(owner: str, note_id: int) -> None:
    """Владельцу и участникам — перечитать таблицу и Фокус."""
    from assistant.services.focus_items import _notify_participants

    _notify_participants(owner, note_id)


def refresh_note(owner: Any, note_id: int) -> bool:
    """Связанная задача поменялась (взяли, в работе, отказ, выполнена) — ячейки таблицы и Фокус."""
    from assistant.stores import notes as notes_store

    note = notes_store.get_note(owner, int(note_id))
    if not note:
        return False
    body = str(note.get("body") or "")
    if not has_table(body):
        notify(str(owner), int(note_id))
        return False
    new_body = refresh_body(body, rows(note))
    if new_body != body:
        notes_store.update_note(owner, int(note_id), body=new_body)
    notify(str(owner), int(note_id))
    return new_body != body
