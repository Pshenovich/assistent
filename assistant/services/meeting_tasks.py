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
# Неразобранный пункт, вложенный в другой: решается вместе с основным — в счётчике разобран.
NESTED = "nested"
CLAIMABLE = UNDECIDED + ("doubtful", SKIPPED, NESTED)
# Пункт → подпункт → под-подпункт, как у задач Орбиты.
MAX_DEPTH = 2

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
    "nested": "Подзадача",
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
    par = _parent_map(tasks)
    for i, t in enumerate(tasks):
        s = status_of(t, linked.get(i))
        iid = str(t.get("id") or "")
        parent = par.get(iid, "")
        if parent and s["code"] == "open":
            s.update(code=NESTED, label=LABELS[NESTED])
        elif skip and s["code"] in UNDECIDED:
            s.update(code=SKIPPED, label=LABELS[SKIPPED], note="")
        dec = decider(note, t, parts) if parts else ""
        if s["code"] == "declined":  # исполнитель отказался — пункт снова у владельца записи
            dec = str(note.get("owner_user_id") or note.get("user_id") or dec)
        items.append({"id": iid, "task": str(t.get("task") or "").strip(), "decider": dec,
                      "parent": parent, "depth": len(_chain(par, iid)), **s})
    live = [x for x in items if x["code"] not in EXCLUDED]
    decided = sum(1 for x in live if x["code"] not in UNDECIDED)
    out = {"items": items, "decided": decided, "total": len(live), "groups": suggested_groups(meta, items)}
    if meta.get("task_groups_asked"):
        out["groups_asked"] = True
    if skip:
        out["skipped"] = skip
    return out


# ---------------------------------------------------------------- группы от Лео


def suggested_groups(meta: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Предложения Лео «сложить пункты в одну задачу», ещё актуальные: пункты не вложены и не «Не задачи»."""
    by_id = {x["id"]: x for x in items}
    tasks = [t for t in meta.get("tasks") or [] if isinstance(t, dict) and t.get("id")]
    out = []
    for g in meta.get("task_groups") or []:
        if not isinstance(g, dict) or g.get("state") != "suggested":
            continue
        key = str(g.get("key") or "")
        members = [str(t["id"]) for t in tasks if str(t.get("group_key") or "") == key]
        parent = next((str(t["id"]) for t in tasks if str(t.get("group_key") or "") == key and t.get("group_parent")), "")
        if parent and (parent not in by_id or by_id[parent]["code"] in EXCLUDED):
            parent = ""
        kids = [
            m for m in members
            if m != parent and m in by_id and by_id[m]["code"] not in EXCLUDED and not by_id[m].get("parent")
        ]
        if len(kids) < (1 if parent else 2):
            continue
        out.append({"id": key, "title": str(g.get("title") or ""), "parent": parent, "items": kids})
    return out


def group_op(viewer_uid: Any, note_id: int, key: str, op: str, *, by: str) -> dict[str, Any]:
    """«Объединить» (accept) / «Не надо» (reject) по предложению Лео. Объединить = вложить пункты
    в основной; основного пункта нет — он появляется строкой с названием группы."""
    from assistant.lib import note_items

    with _CLAIM_LOCK:
        owner, note = _owner_note(viewer_uid, note_id)
        meta = dict(note.get("meta") or {})
        groups = [dict(g) for g in meta.get("task_groups") or [] if isinstance(g, dict)]
        gi = next((i for i, g in enumerate(groups) if str(g.get("key") or "") == key), -1)
        if gi < 0:
            raise LookupError("Предложение не найдено — обновите заметку")
        live = next((g for g in suggested_groups(meta, rows(note)["items"]) if g["id"] == key), None)
        if op == "reject" or not live:
            groups[gi]["state"] = "rejected" if op == "reject" else "stale"
            meta["task_groups"] = groups
            _save(owner, note, meta)
            if op != "reject":
                raise ValueError("Эти задачи уже разобрали")
            return {**rows({**note, "meta": meta}), "parent": ""}
        if op != "accept":
            raise ValueError("Неизвестное действие")
        parent = live["parent"]
        body = str(note.get("body") or "")
        if not parent:
            tasks = list(meta.get("tasks") or [])
            first = _find(meta, live["items"][0])
            who = {str(tasks[_find(meta, k)].get("assignee") or "").strip() for k in live["items"]}
            new = {"id": note_items.new_item_id(), "task": live["title"] or "Общая задача",
                   "assignee": who.pop() if len(who) == 1 else "", "deadline": "", "added_by": by}
            tasks.insert(max(first, 0), new)
            meta["tasks"] = tasks
            parent = new["id"]
            m = next((m for m in _ROW_RE.finditer(body) if f'data-leo-item="{live["items"][0]}"' in m.group(0)), None)
            row = row_html(parent, new["task"], status_of(new))
            body = body[:m.start()] + row + body[m.start():] if m else body
        groups[gi]["state"] = "merged"
        meta["task_groups"] = groups
        from assistant.stores import notes as notes_store

        notes_store.update_note(owner, int(note_id), meta=meta, body=body if body != str(note.get("body") or "") else None)
        state = nest(viewer_uid, note_id, live["items"], parent, by=by)
        return {**state, "parent": parent}


def suggest_groups(viewer_uid: Any, note_id: int, *, by: str) -> dict[str, Any]:
    """«Лео, сгруппируй» в старом саммари: предложить группы по ещё не вложенным пунктам."""
    from assistant.nlu import llm

    owner, note = _owner_note(viewer_uid, note_id)
    state = rows(note)
    cand = [x for x in state["items"] if x["code"] not in EXCLUDED and not x.get("parent")]
    found = llm.group_tasks([{"task": x["task"], "assignee": x.get("who") or "", "deadline": x.get("due") or ""} for x in cand])
    with _CLAIM_LOCK:
        owner, note = _owner_note(viewer_uid, note_id)
        meta = dict(note.get("meta") or {})
        tasks = [dict(t) if isinstance(t, dict) else t for t in meta.get("tasks") or []]
        groups = [dict(g) for g in meta.get("task_groups") or [] if isinstance(g, dict)]
        n0 = len(groups)
        for n, g in enumerate(found, start=1):
            key = f"g{n0 + n}"
            for k in g["items"]:
                i = _find({"tasks": tasks}, cand[k]["id"])
                if i < 0:
                    continue
                tasks[i]["group_key"] = key
                tasks[i].pop("group_parent", None)
                if k == g["parent"]:
                    tasks[i]["group_parent"] = True
            groups.append({"key": key, "title": g["title"] or cand[g["parent"]]["task"][:120], "state": "suggested", "by": by})
        meta.update(tasks=tasks, task_groups=groups, task_groups_asked=True)
        _save(owner, note, meta)
        return rows({**note, "meta": meta})


def _parent_map(tasks: list[dict[str, Any]]) -> dict[str, str]:
    """id пункта → id основного пункта. Ссылки на исчезнувшие и «Не задачи» не считаются."""
    alive = {
        str(t.get("id")): t for t in tasks
        if isinstance(t, dict) and t.get("id") and status_of(t)["code"] not in EXCLUDED
    }
    out = {}
    for iid, t in alive.items():
        p = str(t.get("parent_item") or "")
        if p and p != iid and p in alive:
            out[iid] = p
    for iid in list(out):  # петля в данных — разрываем
        if len(_chain(out, iid)) > MAX_DEPTH + 1:
            out.pop(iid, None)
    return out


def _chain(par: dict[str, str], iid: str) -> list[str]:
    """Основные пункты над iid — от ближнего к верхнему."""
    ups: list[str] = []
    cur = par.get(iid, "")
    while cur and cur not in ups and len(ups) <= MAX_DEPTH + 1:
        ups.append(cur)
        cur = par.get(cur, "")
    return ups


def tree_order(ids: list[str], par: dict[str, str]) -> list[str]:
    """Порядок строк: каждый пункт сразу за своим основным, внутри — как в таблице."""
    known = set(ids)
    out: list[str] = []

    def walk(pid: str) -> None:
        for iid in ids:
            if (par.get(iid, "") if par.get(iid, "") in known else "") == pid and iid not in out:
                out.append(iid)
                walk(iid)

    walk("")
    return out + [i for i in ids if i not in out]


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


def refresh_body(html_text: str, state: dict[str, Any], order: Optional[list[str]] = None) -> str:
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
    run: list[tuple[str, bool, str]] = []  # строки одной таблицы подряд: «Не задачи» — в конец
    par = {iid: x["parent"] for iid, x in by_id.items() if x.get("parent")}

    rank = {iid: n for n, iid in enumerate(order or [])}

    def flush() -> None:
        live = {iid: r for r, ex, iid in run if not ex}
        ids = sorted(live, key=lambda i: rank.get(i, len(rank))) if rank else list(live)  # перетащили строку
        out.append("".join([live[i] for i in tree_order(ids, par)] + [r for r, ex, _ in run if ex]))
        run.clear()

    pos = 0
    for m, iid in rows_found:
        gap = src[pos:m.start()]
        if gap.strip() and run:
            flush()
        if gap.strip() or not run:
            out.append(gap)
        first = _FIRST_CELL_RE.search(m.group(0))
        run.append((row_html(iid, "", by_id[iid], first_cell=first.group(0) if first else None), by_id[iid]["code"] in EXCLUDED, iid))
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


def tracker_step(viewer_uid: Any, note_id: int, provider: str) -> Optional[dict[str, Any]]:
    """Следующий шаг, чтобы вложенность пунктов совпала с вложенностью задач в трекере:
    {"op": "create", item, title, who, parent} — неразобранный пункт под пунктом в трекере;
    {"op": "reparent", item, ext, parent} — пункт уже в трекере, но основной у него другой.
    None — всё совпадает. parent — номер задачи в трекере ("" — самостоятельная)."""
    _, note = _owner_note(viewer_uid, note_id)
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    tasks = [t for t in meta.get("tasks") or [] if isinstance(t, dict) and t.get("id")]
    by_id = {str(t["id"]): t for t in tasks}
    par = _parent_map(tasks)
    state = {x["id"]: x for x in rows(note)["items"]}

    def ext(t: dict[str, Any]) -> str:
        link = t.get("link") if isinstance(t.get("link"), dict) else {}
        return str(link.get("external_id") or "") if str(link.get("provider") or "") == provider else ""

    for iid in tree_order(list(by_id), par):
        t = by_id[iid]
        up = next((ext(by_id[a]) for a in _chain(par, iid) if ext(by_id[a])), "")
        mine = ext(t)
        link = t.get("link") if isinstance(t.get("link"), dict) else {}
        if mine:
            had = str(link.get("parent_external") or "")
            if had != up and (up or had):
                return {"op": "reparent", "item": iid, "ext": mine, "parent": up}
            continue
        if up and state.get(iid, {}).get("code") in UNDECIDED + (NESTED,):
            return {"op": "create", "item": iid, "title": str(t.get("task") or "").strip(),
                    "who": str(t.get("assignee") or "").strip(), "parent": up}
    return None


def tracker_providers(viewer_uid: Any, note_id: int) -> set[str]:
    """Трекеры, в которых уже есть задачи из этой встречи."""
    _, note = _owner_note(viewer_uid, note_id)
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    out = set()
    for t in meta.get("tasks") or []:
        link = t.get("link") if isinstance(t, dict) and isinstance(t.get("link"), dict) else {}
        if link.get("provider") and link.get("external_id"):
            out.add(str(link["provider"]))
    return out


def state_rows(viewer_uid: Any, note_id: int) -> dict[str, Any]:
    return rows(_owner_note(viewer_uid, note_id)[1])


def set_tracker_parent(viewer_uid: Any, note_id: int, item_id: str, parent_ext: str) -> None:
    with _CLAIM_LOCK:
        owner, note = _owner_note(viewer_uid, note_id)
        meta = dict(note.get("meta") or {})
        tasks = list(meta.get("tasks") or [])
        i = _find(meta, item_id)
        if i < 0:
            return
        link = dict(tasks[i].get("link") or {})
        if parent_ext:
            link["parent_external"] = parent_ext
        else:
            link.pop("parent_external", None)
        tasks[i] = {**tasks[i], "link": link}
        meta["tasks"] = tasks
        _save(owner, note, meta)


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
    tasks[i] = _linked(tasks[i], task, by)
    meta["tasks"] = tasks
    _sync_links(viewer_uid, note, meta, by=by)  # вложенные пункты — подзадачами новой задачи
    _save(owner, note, meta)


def _linked(t: dict[str, Any], task: dict[str, Any], by: str) -> dict[str, Any]:
    t = dict(t)
    t["link"] = {"task_id": int(task["id"]), "title": str(task.get("title") or "")[:200]}
    t["focus_status"] = "converted"
    t["completed"] = True  # как у «В задачу» из Фокуса: пункт закрыт у всех
    t["completed_by"] = by
    t["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    t.pop("focus_reason", None)
    return t


def nest(
    viewer_uid: Any, note_id: int, item_ids: list[str], parent_item: Optional[str], *, by: str,
    anchor: Optional[str] = None, pos: str = "after",
) -> dict[str, Any]:
    """Вложить пункты в другой (parent_item) или сделать самостоятельными (None).

    Глубже двух уровней не вкладываем: в под-подпункт — значит, рядом с ним. Пункт переезжает
    со своими подпунктами. Если у основного пункта уже есть задача — вложенный сразу становится
    её подзадачей (исполнитель из строки, иначе ответственный основной задачи).

    anchor/pos — куда поставить строки: до или после строки anchor (с её подпунктами).
    Без anchor вложенные встают последними под основным."""
    with _CLAIM_LOCK:
        owner, note = _owner_note(viewer_uid, note_id)
        meta = dict(note.get("meta") or {})
        tasks = [dict(t) if isinstance(t, dict) else t for t in meta.get("tasks") or []]
        idx = {str(t.get("id")): i for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("id")}
        par = _parent_map(tasks)
        target = str(parent_item or "")
        if target:
            if target not in idx:
                raise LookupError("Пункт не найден — обновите заметку")
            if len(_chain(par, target)) >= MAX_DEPTH:
                target = par.get(target, "")
        for iid in item_ids:
            if iid not in idx:
                raise LookupError("Пункт не найден — обновите заметку")
            if target and (iid == target or iid in _chain(par, target)):
                raise ValueError("Задачу нельзя вложить в её же подзадачу")
        for iid in item_ids:
            if target:
                par[iid] = target
            else:
                par.pop(iid, None)
        changed = True
        while changed:  # что оказалось глубже двух уровней — поднимаем
            changed = False
            for iid in list(par):
                if len(_chain(par, iid)) > MAX_DEPTH:
                    par[iid] = par.get(par[iid], "")
                    if not par[iid]:
                        par.pop(iid)
                    changed = True
        for iid, i in idx.items():
            if par.get(iid):
                tasks[i]["parent_item"] = par[iid]
            else:
                tasks[i].pop("parent_item", None)
        meta["tasks"] = tasks
        _sync_links(viewer_uid, note, meta, by=by)
        where = anchor if anchor in idx and anchor not in item_ids else (target or None)
        order = _moved(_ITEM_IN_RE.findall(str(note.get("body") or "")), item_ids, par, where, pos if anchor else "after")
        _save(owner, note, meta, order=order)
        return rows({**note, "meta": meta})


def _moved(order: list[str], moving: list[str], par: dict[str, str], anchor: Optional[str], pos: str) -> list[str]:
    """Порядок строк после переноса: moving со своими подпунктами — до/после anchor с его подпунктами."""
    def subtree(iid: str) -> set[str]:
        return {iid} | {x for x in order if iid in _chain(par, x)}

    block_ids = set().union(*(subtree(m) for m in moving)) if moving else set()
    block = [x for x in order if x in block_ids]
    rest = [x for x in order if x not in block_ids]
    if not anchor or anchor not in rest:
        return rest + block
    if pos == "before":
        at = rest.index(anchor)
    else:
        inside = subtree(anchor)
        at = max(i for i, x in enumerate(rest) if x in inside) + 1
    return rest[:at] + block + rest[at:]


def _sync_links(viewer_uid: Any, note: dict[str, Any], meta: dict[str, Any], *, by: str) -> None:
    """Вложенность пунктов → вложенность задач: задача пункта — подзадача задачи ближайшего
    основного пункта, у которого задача есть. Неразобранный пункт под взятым — сразу подзадача."""
    from assistant.stores import calendar_tasks

    tasks = meta.get("tasks") or []
    par = _parent_map(tasks)
    ids = [str(t.get("id")) for t in tasks if isinstance(t, dict) and t.get("id")]
    pos = {str(t.get("id")): i for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("id")}
    meeting_task_ids = {tid for tid in (_linked_task_id(t) for t in tasks if isinstance(t, dict)) if tid}
    parts = None
    for iid in tree_order(ids, par):  # сверху вниз: у основного задача появляется раньше
        t = tasks[pos[iid]]
        want = next((_linked_task_id(tasks[pos[a]]) for a in _chain(par, iid) if _linked_task_id(tasks[pos[a]])), None)
        mine = _linked_task_id(t)
        if mine:
            row = _task_row(mine)
            if not row:
                continue
            cur = row.get("parent_id")
            # Чужую вложенность (сделанную в самой задаче, не из этой встречи) не трогаем.
            if cur != want and (want or cur in meeting_task_ids):
                calendar_tasks.link_parent(mine, want, actor_user_id=viewer_uid)
            continue
        if not want or status_of(t)["code"] not in UNDECIDED:
            continue
        parent = _task_row(want)
        if not parent:
            continue
        if parts is None:
            parts = participants(note)
        uid = _who_is(str(t.get("assignee") or ""), parts) if str(t.get("assignee") or "").strip() else ""
        name = str(t.get("assignee") or "").strip() if uid else ""
        if not uid:
            uid, name = str(parent.get("assignee_user_id") or ""), str(parent.get("assignee_name") or "")
        try:
            task = calendar_tasks.create_task(
                # Создаёт тот, кто вложил; нет доступа к основной задаче — от имени её постановщика.
                viewer_uid if calendar_tasks.user_can_access_task(viewer_uid, parent) else parent["owner_user_id"],
                title=str(t.get("task") or "").strip()[:200],
                description=f"Из встречи «{str(note.get('title') or 'встреча').strip()}»",
                start_at=str(parent.get("start_at") or ""),
                all_day=bool(parent.get("all_day")),
                assignee_user_id=uid or None,
                assignee_name=name,
                note_id=str(note.get("id") or ""),
                parent_id=None,
            )
        except ValueError as e:
            print(f"[meeting_tasks] subtask create failed note={note.get('id')} item={iid} err={e!r}")
            continue
        calendar_tasks.link_parent(task["id"], want, actor_user_id=viewer_uid)
        tasks[pos[iid]] = _linked(t, task, by)
        meeting_task_ids.add(int(task["id"]))


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
        up = str(t.pop("parent_item", "") or "")
        for j, o in enumerate(tasks):  # подпункты «Не задачи» встают на её место
            if isinstance(o, dict) and str(o.get("parent_item") or "") == str(item_id):
                tasks[j] = {**o, "parent_item": up} if up else {k: v for k, v in o.items() if k != "parent_item"}
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


def _save(owner: str, note: dict[str, Any], meta: dict[str, Any], order: Optional[list[str]] = None) -> None:
    from assistant.stores import notes as notes_store

    body = str(note.get("body") or "")
    new_body = refresh_body(body, rows({**note, "meta": meta}), order)
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
