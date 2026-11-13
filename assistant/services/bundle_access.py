"""Связки с учётом доступа: каждый видит в связке только то, к чему у него и так есть доступ.

Правила:
• документ (в т.ч. саммари встречи, база знаний) — если человек может его читать;
• задача — если видит задачу (постановщик, исполнитель, участник);
• чат — владелец или участник;
• напоминание — только хозяин напоминания;
• человек и встреча из Google — личное: видит только тот, кто добавил (снимок подписи).
«Связанное» (безымянную связку) видит тот, кому доступен хотя бы один её элемент.
Проект (kind='project') видят только его участники (stores/projects.py), даже пустой. Недоступные элементы не раскрываются
даже названием. Сейфы связать нельзя — у них нет вида в KINDS.
"""

from __future__ import annotations

from typing import Any, Optional

from assistant.stores import bundles as bundles_store

KIND_LABEL = {
    "note": "Документ",
    "task": "Задача",
    "chat": "Чат",
    "reminder": "Напоминание",
    "person": "Человек",
    "event": "Встреча",
    "journal": "Запись",
    "series": "Серия встреч",
    "topic": "Тема встреч",
    "tgchat": "Чат Telegram",
    "mail": "Переписка",
    "mailaddr": "Почтовый адрес",
}
# Подпись-снимок: видна тому, кто добавил; в проекте — всем участникам (кроме адреса почты).
_LABEL_KINDS = ("person", "event", "series")
_PROJECT_SHARED_LABELS = ("person", "event", "series", "topic")


def _uid(user_id: Any) -> str:
    return str(int(user_id))


def _note(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import notes as notes_store

    try:
        n = notes_store.get_readable_note(uid, int(rid), with_sharing=False)
    except Exception as e:
        print(f"[bundles] note access id={rid} err={e!r}")
        return None
    if not n:
        return None
    is_tr = str(n.get("role") or "") == notes_store.TRANSCRIPTION_ROLE
    return {
        "title": str(n.get("title") or "").strip() or "Без названия",
        "subtitle": "Саммари встречи" if is_tr else "Документ",
        "is_transcription": is_tr,
        "updated_at": str(n.get("updated_at") or ""),
    }


def _task(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.services import project_access

    t = project_access.viewable_task(uid, rid)
    if not t:
        return None
    sub = ["Задача"]
    if t.get("assignee_name"):
        sub.append(str(t["assignee_name"]))
    return {
        "title": str(t.get("title") or "").strip() or "Задача",
        "subtitle": " · ".join(sub),
        "done": bool(t.get("done")),
        "start_at": str(t.get("start_at") or ""),
    }


def _chat(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import chat_threads

    c = chat_threads.get_accessible_thread(uid, rid)
    if not c:
        return None
    return {"title": str(c.get("title") or "").strip() or "Чат", "subtitle": "Чат"}


def _reminder(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.stores import reminders_store

    r = reminders_store.get_reminder_by_id(rid)
    if not r or str(int(r.get("user_id") or 0)) != uid:
        return None
    return {"title": str(r.get("task") or r.get("text") or "").strip()[:200] or "Напоминание", "subtitle": "Напоминание"}


def _journal(uid: str, rid: str) -> Optional[dict[str, Any]]:
    from assistant.lib import usage_store

    try:
        row = usage_store.get_user_usage_event(uid, int(rid))
    except Exception as e:
        print(f"[bundles] journal access id={rid} err={e!r}")
        return None
    if not row:
        return None
    title = str(row.get("title") or row.get("query") or row.get("operation") or "").strip()[:200]
    return {"title": title or "Запись", "subtitle": "Запись"}


def _tgchat(uid: str, rid: str) -> Optional[dict[str, Any]]:
    """Внешний чат — только его участникам (как Дайджест); топик — с названием топика."""
    from assistant.stores import chat_digest

    parts = rid.split(":")
    chat_id = int(parts[0])
    try:
        if not chat_digest.is_human_member(chat_id, int(uid)):
            return None
        c = chat_digest.get_chat(chat_id)
    except Exception as e:
        print(f"[bundles] tgchat access id={rid} err={e!r}")
        return None
    title = str((c or {}).get("title") or "").strip() or "Чат"
    if len(parts) == 2:
        try:
            topic = chat_digest.topic_label(chat_digest.topic_names(chat_id), int(parts[1]))
        except Exception:
            topic = ""
        return {"title": f"{title} · {topic or 'топик'}", "subtitle": "Топик чата", "chat_id": chat_id}
    return {"title": title, "subtitle": "Чат Telegram", "chat_id": chat_id}


def _mail(uid: str, rid: str) -> Optional[dict[str, Any]]:
    """Переписка — только хозяину ящика (личное, другим участникам проекта не открывается)."""
    from assistant.stores import mail_index

    try:
        msgs = mail_index.thread(uid, rid)
    except Exception as e:
        print(f"[bundles] mail access err={e!r}")
        return None
    if not msgs:
        return None
    last = msgs[-1]
    subj = str(msgs[0].get("subject") or "").strip() or "Без темы"
    who = str(last.get("from_name") or last.get("from_addr") or "").strip()
    return {"title": subj[:200], "subtitle": "Переписка" + (f" · {who}" if who else ""), "date": str(last.get("date_utc") or "")}


def _topic(uid: str, rid: str, *, in_project: bool) -> Optional[dict[str, Any]]:
    from assistant.stores import meeting_topics

    try:
        t = meeting_topics.get_topic(int(rid))
        if not t:
            return None
        if not in_project and int(rid) not in set(meeting_topics.visible_topic_ids(int(uid))):
            return None
    except Exception as e:
        print(f"[bundles] topic access id={rid} err={e!r}")
        return None
    return {"title": str(t.get("name") or "").strip() or "Тема встреч", "subtitle": "Тема встреч"}


def resolve(
    user_id: Any, kind: str, item_id: str, *, label: str = "", added_by: str = "", in_project: bool = False
) -> Optional[dict[str, Any]]:
    """Как элемент выглядит для этого человека, или None — если ему недоступен.
    in_project — элемент смотрят из проекта, где человек участник: встречи, серии, люди и темы
    видны всем участникам (подписью); почта и внешние чаты — по-прежнему только своим."""
    uid = _uid(user_id)
    try:
        k, rid = bundles_store.norm_item(kind, item_id)
    except ValueError:
        return None
    info: Optional[dict[str, Any]]
    if k == "note":
        info = _note(uid, rid)
    elif k == "task":
        info = _task(uid, rid)
    elif k == "chat":
        info = _chat(uid, rid)
    elif k == "reminder":
        info = _reminder(uid, rid)
    elif k == "journal":
        info = _journal(uid, rid)
        if info and str(label or "").strip():
            info["title"] = str(label).strip()[:200]
    elif k == "tgchat":
        info = _tgchat(uid, rid)
    elif k == "mail":
        info = _mail(uid, rid)
    elif k == "topic":
        info = _topic(uid, rid, in_project=in_project)
    else:  # person, event, series, mailaddr — подпись-снимок
        mine = bool(added_by) and str(added_by) == uid
        if not mine and not (in_project and k in _PROJECT_SHARED_LABELS):
            return None
        text = str(label or "").strip() or (rid if k == "mailaddr" else KIND_LABEL[k])
        info = {"title": text, "subtitle": KIND_LABEL[k]}
    if not info:
        return None
    return {"kind": k, "id": rid, **info}


def can_add(user_id: Any, kind: str, item_id: str, label: str = "") -> dict[str, Any]:
    """Проверка перед добавлением: элемент доступен человеку. ValueError — если нет."""
    uid = _uid(user_id)
    k, rid = bundles_store.norm_item(kind, item_id)
    if k in _LABEL_KINDS:
        if not str(label or "").strip():
            raise ValueError("Нет названия элемента")
        return {"kind": k, "id": rid, "title": str(label).strip()[:200]}
    if k == "mailaddr":
        return {"kind": k, "id": rid, "title": str(label or "").strip()[:200] or rid}
    info = resolve(uid, k, rid)
    if not info:
        raise ValueError("Нет доступа к элементу")
    return info


def view_bundle(user_id: Any, bundle: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Связка глазами человека: только доступные элементы; None — если не доступен ни один.
    Проект — только участникам (пустой тоже), с ролью и настройками."""
    uid = _uid(user_id)
    if bundles_store.is_project(bundle):
        return _view_project(uid, bundle)
    items = []
    for it in bundle.get("items") or []:
        info = resolve(
            uid, it["item_kind"], it["item_id"], label=it.get("label") or "", added_by=it.get("added_by") or ""
        )
        if info:
            info["can_remove"] = str(it.get("added_by") or "") == uid or str(bundle.get("owner_user_id")) == uid
            items.append(info)
    if not items:
        return None
    is_owner = str(bundle.get("owner_user_id") or "") == uid
    return {
        "id": int(bundle["id"]),
        "kind": bundles_store.BUNDLE_RELATED,
        "is_project": False,
        "title": str(bundle.get("title") or ""),
        "is_owner": is_owner,
        "can_rename": True,
        "can_delete": is_owner,
        "items": items,
        "hidden": len(bundle.get("items") or []) - len(items),
        "updated_at": str(bundle.get("updated_at") or ""),
    }


def _view_project(uid: str, bundle: dict[str, Any], *, with_items: bool = True) -> Optional[dict[str, Any]]:
    from assistant.stores import projects as projects_store

    if bundle.get("members") is None:
        bundle = {**bundle, "members": projects_store.members(bundle["id"])}
    role = projects_store.role_of(uid, bundle)
    if not role:
        return None
    can_edit = projects_store.at_least(role, "editor")
    items = []
    raw_items = bundle.get("items") or []
    if with_items:
        for it in raw_items:
            info = resolve(
                uid, it["item_kind"], it["item_id"], label=it.get("label") or "", added_by=it.get("added_by") or "",
                in_project=True,
            )
            if info:
                info["can_remove"] = can_edit or str(it.get("added_by") or "") == uid
                items.append(info)
    status = str(bundle.get("status") or "active")
    return {
        "id": int(bundle["id"]),
        "kind": bundles_store.BUNDLE_PROJECT,
        "is_project": True,
        "title": str(bundle.get("title") or ""),
        "description": str(bundle.get("description") or ""),
        "color": str(bundle.get("color") or ""),
        "status": status,
        "status_label": projects_store.STATUS_LABEL.get(status, ""),
        "org_id": str(bundle.get("org_id") or ""),
        "lead_user_id": str(bundle.get("lead_user_id") or ""),
        "archived": bool(bundle.get("archived_at")),
        "role": role,
        "is_owner": role == "owner",
        "can_rename": can_edit,
        "can_edit": can_edit,
        "can_share": can_edit,
        "can_delete": role == "owner",
        "members": [
            {"principal": str(m["principal"]), "role": str(m["role"])} for m in bundle.get("members") or []
        ],
        "items": items,
        "items_total": len(raw_items),
        "hidden": (len(raw_items) - len(items)) if with_items else 0,
        "updated_at": str(bundle.get("updated_at") or ""),
    }


def bundles_for_item(user_id: Any, kind: str, item_id: str) -> list[dict[str, Any]]:
    """Связки элемента для человека, которому доступен сам элемент (проверяет вызывающий)."""
    out = []
    for b in bundles_store.bundles_for_item(kind, item_id):
        v = view_bundle(user_id, b)
        if v:
            out.append(v)
    return out


def visible_bundle(user_id: Any, bundle_id: Any) -> Optional[dict[str, Any]]:
    b = bundles_store.get(bundle_id)
    return view_bundle(user_id, b) if b else None


def visible_ids_for_item(user_id: Any, kind: str, item_id: str) -> set[int]:
    return {int(b["id"]) for b in bundles_for_item(user_id, kind, item_id)}


def member_user_ids(bundle: dict[str, Any]) -> list[str]:
    """Кому слать «связки изменились»: автор, все, кто добавлял элементы, и участники проекта
    (лично добавленные; отделы и вся организация узнают при следующем открытии)."""
    ids = {str(bundle.get("owner_user_id") or ""), str(bundle.get("lead_user_id") or "")}
    for it in bundle.get("items") or []:
        ids.add(str(it.get("added_by") or ""))
    if bundles_store.is_project(bundle):
        from assistant.stores import projects as projects_store

        rows = bundle.get("members")
        if rows is None:
            rows = projects_store.members(bundle["id"])
        for m in rows:
            pr = str(m.get("principal") or "")
            if pr.startswith("u:"):
                ids.add(pr[2:])
    return sorted(i for i in ids if i)


def context_items(user_id: Any, kind: str, item_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Связанные элементы для контекста Лео (без самого элемента, без повторов)."""
    try:
        self_key = bundles_store.norm_item(kind, item_id)
    except ValueError:
        return []
    seen = {self_key}
    out = []
    for b in bundles_for_item(user_id, *self_key):
        for it in b["items"]:
            key = (it["kind"], it["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append({**it, "bundle_title": b["title"]})
            if len(out) >= limit:
                return out
    return out


def leo_context(user_id: Any, kind: str, item_id: str, *, max_notes: int = 3) -> tuple[list[str], str]:
    """Контекст для Лео: id связанных документов (тексты подложит pack_attached_notes) и
    короткий список всего связанного — названия, тип, статус задачи. Только доступное человеку."""
    items = context_items(user_id, kind, item_id, limit=20)
    if not items:
        return [], ""
    note_ids = [it["id"] for it in items if it["kind"] == "note"][:max_notes]
    lines = []
    for it in items:
        bits = [str(it.get("subtitle") or KIND_LABEL.get(it["kind"], "")), f"«{it['title']}»"]
        if it["kind"] == "task":
            bits.append("выполнена" if it.get("done") else "открыта")
        if it.get("bundle_title"):
            bits.append(f"проект «{it['bundle_title']}»")
        lines.append("• " + " · ".join(b for b in bits if b))
    text = (
        "Связанные материалы (пользователь сам связал их с этим документом; "
        "учитывай их, ссылайся по названию):\n" + "\n".join(lines)
    )
    return note_ids, text


def _accessible_note_ids(uid: str) -> list[int]:
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    ids = set(notes_store.own_note_ids(uid))
    try:
        ids.update(int(nid) for _owner, nid in note_members.list_note_ids_for_member(uid))
    except Exception as e:
        print(f"[bundles] shared notes uid={uid} err={e!r}")
    return sorted(ids)


def list_for_user(
    user_id: Any, *, query: str = "", limit: int = 50, include_archived: bool = False
) -> list[dict[str, Any]]:
    """Проекты человека (для «Проектов», поиска и Лео): только где он участник, элементы — видимые ему.
    query — по названию проекта и названиям его элементов."""
    from assistant.stores import projects as projects_store

    uid = _uid(user_id)
    q = " ".join(str(query or "").lower().split())
    out = []
    for bid in projects_store.ids_for_user(uid, include_archived=include_archived):
        v = visible_bundle(uid, bid)
        if not v or not v["title"]:
            continue
        if q:
            hay = " ".join([v["title"]] + [it["title"] for it in v["items"]]).lower()
            if not all(w in hay for w in q.split()):
                continue
        out.append(v)
        if len(out) >= limit:
            break
    return out


def project_list(user_id: Any, *, query: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
    """Проекты человека для списка: без разбора элементов (только сколько их), по названию."""
    from assistant.stores import projects as projects_store

    uid = _uid(user_id)
    q = " ".join(str(query or "").lower().split())
    out = []
    for pid in projects_store.ids_for_user(uid, include_archived=include_archived):
        p = projects_store.get(pid)
        if not p:
            continue
        v = _view_project(uid, p, with_items=False)
        if not v:
            continue
        if q and not all(w in v["title"].lower() for w in q.split()):
            continue
        out.append(v)
    return out


def project_full(user_id: Any, project_id: Any) -> Optional[dict[str, Any]]:
    """Проект целиком для окна проекта: видимые элементы, участники с подписями, руководитель."""
    from assistant.stores import projects as projects_store

    uid = _uid(user_id)
    p = projects_store.get(project_id)
    if not p:
        return None
    v = _view_project(uid, p)
    if not v:
        return None
    prs = [m["principal"] for m in v["members"]]
    if v["lead_user_id"]:
        prs.append(f"u:{v['lead_user_id']}")
    labels = projects_store.principal_labels(v["org_id"], prs)
    for m in v["members"]:
        m["label"] = labels.get(m["principal"], "")
        m["role_label"] = projects_store.ROLE_LABEL.get(m["role"], "")
    v["lead_label"] = labels.get(f"u:{v['lead_user_id']}", "") if v["lead_user_id"] else ""
    v["my_principal"] = f"u:{uid}"
    v["my_user_id"] = uid
    counts: dict[str, int] = {}
    for it in v["items"]:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    v["counts"] = counts
    return v

