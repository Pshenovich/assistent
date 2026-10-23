"""«Поделиться с участниками»: участники встречи с аккаунтом в Лео получают доступ к её заметке
(транскрипция + саммари), задачи из неё — в их Фокус (только свои)."""

from __future__ import annotations

from typing import Any, Optional


def _candidates(owner_uid: int, note_id: int) -> list[dict[str, str]]:
    """Участники встречи из календаря + добавленные вручную, без повторов."""
    from assistant.stores import meeting_links

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    m = meeting_links.get_for_note(owner_uid, note_id) or {}
    for p in list(m.get("attendees") or []) + meeting_links.people_for_note(owner_uid, note_id):
        email = str(p.get("email") or "").strip().lower()
        name = str(p.get("name") or "").strip()
        k = email or name.lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append({"email": email, "name": name or email})
    return out


def share_with_attendees(owner_uid: int, note_id: int, *, owner_username: Optional[str] = None) -> dict[str, Any]:
    """added — кому открыли доступ сейчас; already — у кого уже был; missing — нет в Лео."""
    from assistant.bot.access_gate import is_user_allowed
    from assistant.services.meeting_invites import telegram_id_for_attendee_email
    from assistant.stores import note_members

    added: list[dict[str, Any]] = []
    already: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    seen: set[int] = set()
    for p in _candidates(owner_uid, note_id):
        uid = None
        if p["email"]:
            try:
                uid = telegram_id_for_attendee_email(owner_uid, p["email"], telegram_username=owner_username)
            except Exception as e:
                print(f"[meeting_share] lookup failed note={note_id} err={e!r}")
        if uid and int(uid) == int(owner_uid):
            continue  # это сам владелец
        if not uid or not is_user_allowed(int(uid), None):
            missing.append(p)
            continue
        if int(uid) in seen:
            continue
        seen.add(int(uid))
        row = {"user_id": int(uid), "name": p["name"]}
        if note_members.is_member(owner_uid, note_id, uid):
            already.append(row)
            continue
        note_members.add_member(owner_uid, note_id, uid)
        added.append(row)
    return {"added": added, "already": already, "missing": missing}


def posted_for_note(note: dict[str, Any]) -> list[dict[str, Any]]:
    """Задачи, поставленные из встречи, — у владельца и у всех, с кем ей поделились: название, кто, id."""
    from assistant.stores import calendar_tasks, note_members

    users = [str(note.get("owner_user_id") or note.get("user_id") or "")]
    users += [str(m.get("user_id") or "") for m in note.get("members") or [] if isinstance(m, dict)]
    out: list[dict[str, Any]] = []
    for u in dict.fromkeys(x for x in users if x.isdigit()):
        tasks = calendar_tasks.list_tasks_for_note(u, note["id"])
        if not tasks:
            continue
        who = note_members.profile_display_name(note_members.get_profile(u), fallback="")
        out += [{"title": str(t.get("title") or ""), "by": who, "task_id": t.get("id")} for t in tasks]
    return out


def task_state(t: dict[str, Any], posted: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Задача из саммари уже разобрана кем-то из участников встречи: статус, кто, ссылка. None — открыта."""
    from assistant.services.proactive_ai import _covered

    st = str(t.get("focus_status") or "")
    if t.get("completed") or t.get("link") or st in ("completed", "converted", "linked"):
        link = t.get("link") if isinstance(t.get("link"), dict) else {}
        if st not in ("completed", "converted", "linked"):
            st = "converted" if link.get("task_id") else "completed"
        return {"status": st, "by": str(t.get("completed_by") or ""), "link": link}
    title = str(t.get("task") or "")
    for p in posted:
        if p["title"] and _covered(title, [p["title"]]):
            return {"status": "converted", "by": p["by"], "link": {"task_id": p["task_id"]} if p.get("task_id") else {}}
    return None


def overlay(viewer_uid: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Пункты чата из встречи: что уже поставил или выполнил кто-то другой — с его именем."""
    from assistant.services import proactive
    from assistant.services.focus_items import _score
    from assistant.stores import notes as notes_store

    cache: dict[int, Any] = {}
    out = []
    for it in items:
        src = it.get("source") or {}
        if it.get("status") != "new" or src.get("kind") != "note" or not src.get("id"):
            out.append(it)
            continue
        nid = int(src["id"])
        if nid not in cache:
            note = notes_store.get_accessible_note(viewer_uid, nid)
            tasks = [t for t in ((note or {}).get("meta") or {}).get("tasks") or [] if isinstance(t, dict)]
            cache[nid] = (tasks, posted_for_note(note) if note and tasks else [])
        tasks, posted = cache[nid]
        best, best_s = None, 0.0
        want = f"{it.get('assignee') or ''} {it.get('text') or ''}"
        for t in tasks:
            line = " — ".join(x for x in (str(t.get(k) or "").strip() for k in ("assignee", "task", "deadline")) if x)
            s = 1.0 if proactive.summary_task_id(nid, line, t.get("id")) == it.get("key") else _score(want, f"{t.get('assignee') or ''} {t.get('task') or ''}")
            if s > best_s:
                best, best_s = t, s
        if best is None or best_s < 0.5:
            out.append(it)
            continue
        # правки любого участника (исполнитель, срок, формулировка) пишутся в саммари — показываем их
        it = {**it, "text": str(best.get("task") or it.get("text") or ""),
              "assignee": str(best.get("assignee") or ""), "deadline": str(best.get("deadline") or "")}
        st = task_state(best, posted)
        out.append({**it, **st, "shared": True} if st else it)
    return out
