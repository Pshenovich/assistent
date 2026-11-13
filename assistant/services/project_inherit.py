"""Наследование проекта кодом (без LLM): что связано со встречей проекта, само попадает в проект.

Цепочка (решение владельца 2026-11-13):
• встреча, вся серия или тема встреч в проекте → саммари этих встреч в проекте (и будущие тоже:
  саммари привязали к встрече серии позже — оно попадёт само);
• саммари (любой документ) в проекте → задачи из него (note_id) в проекте, и новые тоже;
• подзадача → в проектах общей задачи;
• Лео ставит задачу в открытом документе проекта → задача в его проектах.

Элемент добавляется от имени его хозяина (автор документа, постановщик задачи): тогда, по правилу
services/project_access.py, он открывается участникам проекта на чтение. Проекты в архиве не растут.
Убрали элемент из проекта руками — заново он придёт только при новом событии (новое саммари, новая задача).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


def _projects_with(kind: str, item_id: Any) -> list[int]:
    from assistant.stores import projects as projects_store

    try:
        pids = projects_store.ids_with_item(kind, item_id)
    except ValueError:
        return []
    out = []
    for pid in pids:
        p = projects_store.get(pid)
        if p and not p.get("archived_at"):
            out.append(pid)
    return out


def _put(pid: int, kind: str, item_id: Any, added_by: Any, label: str = "") -> bool:
    """Добавить элемент без проверки роли (это делает код, не человек). True — добавился впервые."""
    from assistant.stores import bundles as bundles_store
    from assistant.stores import projects as projects_store

    try:
        k, rid = bundles_store.norm_item(kind, item_id)
        if pid in projects_store.ids_with_item(k, rid):
            return False
        bundles_store.add_items(str(int(added_by)), pid, [(k, rid, label)])
        return True
    except (ValueError, TypeError) as e:
        print(f"[project_inherit] put pid={pid} {kind}:{item_id} err={e!r}")
        return False


def _note_owner(note_id: int) -> Optional[str]:
    from assistant.stores import notes as notes_store

    n = notes_store.get_note_by_id(int(note_id))
    if not n:
        return None
    return str(n.get("owner_user_id") or n.get("user_id") or "") or None


# ---------- задачи ----------


def _tasks_of_note(note_id: int) -> list[tuple[int, str, str]]:
    """(id, постановщик, название) задач, поставленных из документа."""
    from assistant.stores import calendar_tasks

    with calendar_tasks._LOCK:  # type: ignore[attr-defined]
        rows = calendar_tasks._conn().execute(  # type: ignore[attr-defined]
            "SELECT id, owner_user_id, title FROM calendar_tasks WHERE note_id = ?", (str(int(note_id)),)
        ).fetchall()
    return [(int(r[0]), str(r[1]), str(r[2] or "")) for r in rows]


def _note_into(pids: Iterable[int], note_id: int, label: str = "") -> int:
    """Документ — в проекты, и его задачи следом. Возвращает число добавленных элементов."""
    pids = list(pids)
    if not pids:
        return 0
    owner = _note_owner(note_id)
    if not owner:
        return 0
    added = 0
    for pid in pids:
        added += int(_put(pid, "note", note_id, owner, label))
    for tid, t_owner, title in _tasks_of_note(note_id):
        for pid in pids:
            added += int(_put(pid, "task", tid, t_owner, title))
    return added


def on_task_created(task: dict[str, Any]) -> int:
    """Новая задача: из документа проекта или подзадача задачи проекта — в те же проекты."""
    if not task or not task.get("id"):
        return 0
    pids: set[int] = set()
    nid = str(task.get("note_id") or "").strip()
    if nid.isdigit():
        pids.update(_projects_with("note", nid))
    if task.get("parent_id"):
        pids.update(_projects_with("task", task["parent_id"]))
    added = 0
    for pid in sorted(pids):
        added += int(_put(pid, "task", task["id"], task.get("owner_user_id") or 0, str(task.get("title") or "")))
    return added


def from_open_note(user_id: Any, task: dict[str, Any], note_id: Any) -> list[int]:
    """Лео поставил задачу, когда человек был в документе: задача — в проекты документа,
    где человек может править (он сам её положил бы туда)."""
    from assistant.stores import projects as projects_store

    if not str(note_id or "").strip().isdigit() or not task.get("id"):
        return []
    out = []
    for pid in _projects_with("note", note_id):
        p = projects_store.get(pid)
        if p and projects_store.at_least(projects_store.role_of(user_id, p), "editor"):
            if _put(pid, "task", task["id"], task.get("owner_user_id") or user_id, str(task.get("title") or "")):
                out.append(pid)
    return out


# ---------- встречи ----------


def _split_key(key: str) -> tuple[str, str]:
    cal, _, eid = str(key or "").partition(":")
    return cal, eid


def _in_series(event_id: str, series_id: str) -> bool:
    """Встреча серии: id серии или «<id серии>_<время>» (так у Google и у встреч Орбиты orb-N_…)."""
    return bool(series_id) and (event_id == series_id or event_id.startswith(series_id + "_"))


def _topic_titles(topic_id: int) -> set[str]:
    from assistant.stores import meeting_topics

    try:
        with meeting_topics._LOCK:  # type: ignore[attr-defined]
            conn = meeting_topics._conn()  # type: ignore[attr-defined]
            t = conn.execute("SELECT name FROM meeting_topics WHERE id = ?", (int(topic_id),)).fetchone()
            rows = conn.execute(
                "SELECT value FROM meeting_topic_members WHERE topic_id = ? AND kind = 'title' AND status = 'on'",
                (int(topic_id),),
            ).fetchall()
    except Exception as e:
        print(f"[project_inherit] topic {topic_id} err={e!r}")
        return set()
    out = {str(r[0]) for r in rows if str(r[0] or "").strip()}
    if t and str(t[0] or "").strip():
        out.add(meeting_topics.norm_title(str(t[0])))
    return out


def _linked_notes(where: str, args: tuple) -> list[tuple[int, str, str, str]]:
    """(note_id, calendar_id, event_id, summary) из привязок встреч у всех владельцев."""
    from assistant.stores import meeting_links

    with meeting_links._LOCK:  # type: ignore[attr-defined]
        rows = meeting_links._conn().execute(  # type: ignore[attr-defined]
            f"SELECT note_id, calendar_id, event_id, summary FROM meeting_links WHERE {where}", args
        ).fetchall()
    return [(int(r[0]), str(r[1]), str(r[2]), str(r[3] or "")) for r in rows]


def _notes_for_item(kind: str, item_id: str) -> list[tuple[int, str]]:
    """Саммари встреч элемента проекта: встреча, серия или тема → [(note_id, название встречи)]."""
    if kind == "event":
        cal, eid = _split_key(item_id)
        return [(n, s) for n, _c, _e, s in _linked_notes("calendar_id = ? AND event_id = ?", (cal, eid))]
    if kind == "series":
        cal, sid = _split_key(item_id)
        if not sid:
            return []
        rows = _linked_notes("calendar_id = ? AND (event_id = ? OR substr(event_id, 1, ?) = ?)",
                             (cal, sid, len(sid) + 1, sid + "_"))
        return [(n, s) for n, _c, e, s in rows if _in_series(e, sid)]
    if kind == "topic":
        from assistant.stores import meeting_topics

        titles = _topic_titles(int(item_id))
        if not titles:
            return []
        return [(n, s) for n, _c, _e, s in _linked_notes("summary != ''", ())
                if meeting_topics.norm_title(s) in titles]
    return []


def after_add(project_id: Any, items: Iterable[tuple[str, str]]) -> int:
    """В проект добавили элементы: подтянуть саммари встреч и задачи из документов."""
    from assistant.stores import projects as projects_store

    p = projects_store.get(project_id)
    if not p or p.get("archived_at"):
        return 0
    pid = int(p["id"])
    added = 0
    for kind, rid in items:
        try:
            if kind in ("event", "series", "topic"):
                for nid, summary in _notes_for_item(kind, rid):
                    added += _note_into([pid], nid, summary)
            elif kind == "note":
                for tid, t_owner, title in _tasks_of_note(int(rid)):
                    added += int(_put(pid, "task", tid, t_owner, title))
        except Exception as e:
            print(f"[project_inherit] after_add pid={pid} {kind}:{rid} err={e!r}")
    return added


def on_meeting_linked(note_id: Any, meeting: dict[str, Any]) -> int:
    """Саммари привязали к встрече: если встреча, её серия или тема — в проекте, саммари туда же."""
    from assistant.stores import bundles as bundles_store
    from assistant.stores import meeting_topics

    cal = str(meeting.get("calendar_id") or "primary").strip() or "primary"
    eid = str(meeting.get("event_id") or "").strip()
    if not eid:
        return 0
    pids = set(_projects_with("event", f"{cal}:{eid}"))
    with bundles_store._LOCK:  # type: ignore[attr-defined]
        conn = bundles_store._conn()  # type: ignore[attr-defined]
        series = conn.execute(
            "SELECT i.bundle_id, i.item_id FROM bundle_items i JOIN bundles b ON b.id = i.bundle_id "
            "WHERE b.kind = 'project' AND b.archived_at = '' AND i.item_kind = 'series' AND i.item_id LIKE ?",
            (cal + ":%",),
        ).fetchall()
        topics = conn.execute(
            "SELECT i.bundle_id, i.item_id FROM bundle_items i JOIN bundles b ON b.id = i.bundle_id "
            "WHERE b.kind = 'project' AND b.archived_at = '' AND i.item_kind = 'topic'"
        ).fetchall()
    for bid, key in series:
        if _in_series(eid, _split_key(str(key))[1]):
            pids.add(int(bid))
    title = meeting_topics.norm_title(str(meeting.get("summary") or ""))
    if title:
        for bid, tid in topics:
            if title in _topic_titles(int(tid)):
                pids.add(int(bid))
    return _note_into(sorted(pids), int(note_id), str(meeting.get("summary") or ""))
