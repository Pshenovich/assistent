"""«Календарь подчинённого»: руководитель смотрит календарь человека из своего отдела (только просмотр).

Кто кого видит — как в «Показателях работы» (org_groups.metrics_visible_ids): руководитель отдела — участников
отдела и его команд, владелец и админы — всех, кроме гостей. Работает всегда, без включения в организации.
Решение владельца (2026-11-09): видно всё с названиями, кроме личных напоминаний и событий, закрытых в Google
(visibility private / confidential) — они приходят как «Занят» без подробностей.

Встречи Google берём из зеркала (calendar_mirror), а не живым запросом с токеном сотрудника.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

PRIVATE_VISIBILITY = ("private", "confidential")
BUSY_TITLE = "Занят"


def _uid(v: Any) -> str:
    return str(v or "").strip()


def visible_ids(viewer_id: int | str) -> dict[str, int]:
    """user_id → org_id для всех, чей календарь человек может открыть (без него самого)."""
    from assistant.stores import org_groups, orgs_store

    me = _uid(viewer_id)
    out: dict[str, int] = {}
    for org in orgs_store.orgs_for_user(me):
        try:
            ids = org_groups.metrics_visible_ids(int(org["id"]), me)
        except Exception as e:
            print(f"[subordinate_calendar] visible org={org.get('id')} err={e!r}")
            continue
        for u in ids:
            if u != me and u not in out:
                out[u] = int(org["id"])
    return out


def can_view(viewer_id: int | str, target_id: int | str) -> bool:
    target = _uid(target_id)
    return bool(target) and target != _uid(viewer_id) and target in visible_ids(viewer_id)


def is_private(row: dict[str, Any]) -> bool:
    return str(row.get("visibility") or "").strip().lower() in PRIVATE_VISIBILITY


def gcal_from_mirror(row: dict[str, Any], tz: Any) -> dict[str, Any]:
    """Строка зеркала → событие в форме Google для общего сериализатора календаря.
    Закрытое событие — только время и «Занят»."""
    start: datetime = row["start"]
    end: datetime = row["end"]
    if row.get("all_day"):
        st: dict[str, Any] = {"date": start.astimezone(tz).date().isoformat()}
        en: dict[str, Any] = {"date": end.astimezone(tz).date().isoformat()}
    else:
        st = {"dateTime": start.astimezone(tz).isoformat(), "timeZone": str(tz)}
        en = {"dateTime": end.astimezone(tz).isoformat(), "timeZone": str(tz)}
    raw: dict[str, Any] = {
        "id": str(row.get("event_id") or ""),
        "status": "confirmed",
        "start": st,
        "end": en,
        "_calendarId": str(row.get("calendar_id") or "primary"),
    }
    if row.get("transparent"):
        raw["transparency"] = "transparent"
    if is_private(row):
        raw["summary"] = BUSY_TITLE
        raw["visibility"] = "private"
        raw["_private_busy"] = True
        return raw
    raw["summary"] = str(row.get("title") or "") or "(без названия)"
    for src, dst in (("description", "description"), ("location", "location"), ("html_link", "htmlLink")):
        if row.get(src):
            raw[dst] = row[src]
    if row.get("meeting_url"):
        raw["hangoutLink"] = row["meeting_url"]
    if row.get("organizer_email"):
        raw["organizer"] = {"email": row["organizer_email"]}
    people = [a for a in (row.get("attendees") or []) if isinstance(a, dict)]
    if people:
        raw["attendees"] = [{k: v for k, v in a.items() if k != "self"} for a in people]
    if row.get("series_id"):
        raw["recurringEventId"] = row["series_id"]
    return raw


def mirror_rows(target_id: int | str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Встречи Google сотрудника из зеркала: без отклонённых, задач-меток и служебных (дни рождения и т. п.).
    Задачи Google, которые связаны с задачей Орбиты, показываем один раз — задачей."""
    from assistant.stores import calendar_mirror
    from assistant.stores import calendar_tasks as calendar_tasks_store

    out = []
    for r in calendar_mirror.events(target_id, start, end):
        if str(r.get("kind") or "") in ("task", "other"):
            continue
        if str(r.get("my_response") or "") == "declined":
            continue
        try:
            if calendar_tasks_store.find_by_google_event(int(target_id), str(r.get("event_id") or "")):
                continue
        except Exception:
            pass
        out.append(r)
    return out


def task_events(viewer_id: int | str, target_id: int | str, start: datetime, end: datetime, tz: Any) -> list[dict[str, Any]]:
    """Задачи сотрудника в календаре (как видит он сам: без поручений во «Входящих»).
    Поставленные этим руководителем — с флагом posted_by_me (в сетке фиолетовые, «от вас»)."""
    from assistant.stores import calendar_tasks as calendar_tasks_store

    viewer, target = _uid(viewer_id), _uid(target_id)
    out = []
    for r in calendar_tasks_store.list_tasks_in_window(int(target), start, end):
        if not calendar_tasks_store.task_visible_in_calendar(r, target):
            continue
        ev = calendar_tasks_store.as_calendar_event(r, tz=tz, viewer_id=int(target))
        ev = dict(ev)
        ev["posted_by_me"] = str(r.get("owner_user_id") or "") == viewer and viewer != target
        out.append(ev)
    return out


def _fmt(dt: datetime, tz: Any) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def now_status(target_id: int | str, tz: Any, *, now: Optional[datetime] = None) -> str:
    """Строка под именем в списке: «Сейчас: … до 13:00», «Свободен до 15:00» или «Свободен»."""
    now = now or datetime.now(timezone.utc)
    local_end = datetime.combine(now.astimezone(tz).date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    busy = []
    for r in mirror_rows(target_id, now, local_end):
        if r.get("all_day") or r.get("transparent"):
            continue
        busy.append((r["start"], r["end"], BUSY_TITLE if is_private(r) else str(r.get("title") or BUSY_TITLE)))
    try:
        from assistant.services import orbita_calendar

        for ev in orbita_calendar.events_for(int(target_id), now, local_end):
            st = (ev.get("start") or {}).get("dateTime")
            en = (ev.get("end") or {}).get("dateTime")
            if st and en:
                busy.append((datetime.fromisoformat(st), datetime.fromisoformat(en), str(ev.get("summary") or BUSY_TITLE)))
    except Exception:
        pass
    busy.sort(key=lambda b: b[0])
    for s, e, title in busy:
        if s <= now < e:
            return f"Сейчас: {title} до {_fmt(e, tz)}"
    nxt = [s for s, _e, _t in busy if s > now]
    return f"Свободен до {_fmt(nxt[0], tz)}" if nxt else "Свободен"


def people(viewer_id: int | str, *, tz: Any = timezone.utc) -> list[dict[str, Any]]:
    """Кого можно открыть: имя, отдел/команда (для группировки), подключён ли Google, чем занят сейчас.
    Пусто — у человека нет подчинённых (меню показывает только «Мой» и «Поставленные»)."""
    from assistant.services import people_metrics
    from assistant.stores import calendar_mirror, orgs_store

    ids = visible_ids(viewer_id)
    labels_by_org: dict[int, dict[str, list[str]]] = {}
    members_by_org: dict[int, list[dict[str, Any]]] = {}
    out = []
    for uid, org_id in ids.items():
        if org_id not in labels_by_org:
            try:
                labels_by_org[org_id] = people_metrics._labels(org_id, viewer_id)[1]
            except Exception:
                labels_by_org[org_id] = {}
            members_by_org[org_id] = orgs_store.list_members(org_id)
        google = calendar_mirror.synced_from(uid) is not None
        try:
            status = now_status(uid, tz)
        except Exception as e:
            print(f"[subordinate_calendar] status uid={uid} err={e!r}")
            status = ""
        if not google and status in ("", "Свободен"):
            status = "Google-календарь не подключён"
        groups = labels_by_org[org_id].get(uid, [])
        out.append({
            "user_id": uid,
            "org_id": org_id,
            "name": people_metrics.member_name(org_id, uid, members_by_org[org_id]),
            "group": groups[0] if groups else "",
            "google": google,
            "status": status,
        })
    out.sort(key=lambda p: (p["group"] == "", p["group"].casefold(), p["name"].casefold()))
    return out
