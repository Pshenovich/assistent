"""Задача Орбиты ← её дубль в трекере (Битрикс24): статус, срок и исполнитель.

Сверка: сразу после «Дубля в …», при открытии задачи (не чаще раза в пару минут) и фоном
(assistant/bot/tracker_sync_job.py). Что меняем в задаче Орбиты:
- срок трекера — дата задачи (время дня прежнее);
- задача в трекере завершена — «выполнено»; снова открыли — снимаем отметку, если ставили её мы;
- исполнитель в трекере не владелец вебхука — исполнитель «не из команды» с его именем, задача уходит
  в «Поручил я»; вернули на владельца — снова своя задача.
Поручения человеку из Орбиты (исполнитель — аккаунт) не трогаем: у них свой процесс.
Стадия/статус трекера хранится у связки и показывается чипом «Битрикс24 #id · стадия».
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

STALE_SEC = 10 * 60  # фоновая сверка: связка старше 10 минут
OPEN_STALE_SEC = 2 * 60  # при открытии задачи
ACTOR = "Битрикс24"


def _iso_ago(sec: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sec)).isoformat()


def _apply_to_task(uid: int, link: dict[str, Any], ref: Any, my_account_id: str) -> Optional[dict[str, Any]]:
    """Перенести состояние трекера в задачу Орбиты. Возвращает обновлённую задачу или None (ничего не меняли)."""
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks as ct

    tid = str(link.get("calendar_task_id") or "").strip()
    if not tid.isdigit():
        return None
    task = ct.get_task_for_user(uid, int(tid))
    if not task or str(task.get("owner_user_id") or "") != str(uid):
        return None
    assignee = str(task.get("assignee_user_id") or "")
    if assignee not in ("", str(uid)):
        return None  # поручение человеку из Орбиты — не наше дело
    kw: dict[str, Any] = {}
    tz = _tz_for(uid)

    # Срок: дата из трекера, время дня прежнее.
    deadline = str(getattr(ref, "deadline", "") or "")[:10]
    start = ct._parse_dt(task.get("start_at"))
    if deadline and start:
        local = start.astimezone(tz)
        try:
            y, m, d = (int(x) for x in deadline.split("-"))
            if (local.year, local.month, local.day) != (y, m, d):
                new_start = local.replace(year=y, month=m, day=d)
                end = ct._parse_dt(task.get("end_at"))
                kw["start_at"] = new_start
                kw["end_at"] = new_start + ((end - start) if end and end > start else timedelta(minutes=30))
        except ValueError:
            pass

    # Завершена в трекере → выполнено; переоткрыли → снимаем, только если отметку ставила сверка.
    closed = bool(getattr(ref, "closed", False))
    if closed and not task.get("done"):
        kw["done"] = True
    elif not closed and task.get("done") and int(link.get("closed") or 0):
        kw["done"] = False

    # Исполнитель: не владелец вебхука — «не из команды», задача в «Поручил я».
    rid = str(getattr(ref, "responsible_id", "") or "")
    rname = str(getattr(ref, "responsible", "") or "").strip()
    if rid and my_account_id and rid != str(my_account_id) and rname:
        if not task.get("assignee_external") or str(task.get("assignee_name") or "") != rname:
            kw.update(assignee_user_id=str(uid), assignee_name=rname, assignee_external=True)
    elif rid and my_account_id and rid == str(my_account_id) and task.get("assignee_external"):
        prev = str(link.get("responsible_name") or "")
        if prev and str(task.get("assignee_name") or "") == prev:  # внешним делали мы — возвращаем
            kw.update(assignee_user_id=str(uid), assignee_name="", assignee_external=False)

    if not kw:
        return None
    return ct.update_task(uid, int(tid), tz=tz, actor_name=ACTOR, **kw)


def sync_link(uid: int, link: dict[str, Any]) -> dict[str, Any]:
    """Сверить одну связку. {"ok", "changed", "link"} — link с актуальными стадией/сроком/исполнителем."""
    from assistant.stores import trackers as trackers_store
    from assistant.trackers.base import TrackerError
    from assistant.trackers.registry import get_provider

    try:
        p = get_provider(str(link.get("provider") or ""))
        if not hasattr(p, "get_task") or not p.is_connected(uid):
            return {"ok": False, "changed": False, "link": link}
        ref = p.get_task(uid, str(link.get("external_id") or ""))
    except TrackerError as e:
        trackers_store.save_sync(link["id"], title="", url="", stage_name="", status="", deadline="",
                                 responsible_id="", responsible_name="", closed=False, error=str(e) or "error")
        return {"ok": False, "changed": False, "link": link}
    creds = trackers_store.get_credentials(uid, p.id) or {}
    changed = None
    try:
        changed = _apply_to_task(uid, link, ref, str(creds.get("account_id") or ""))
    except Exception as e:  # noqa: BLE001 — задачу не поменяли, но стадию всё равно запомним
        print(f"[tracker_sync] apply uid={uid} link={link.get('id')} err={e!r}")
    trackers_store.save_sync(
        link["id"], title=ref.title, url=ref.url, stage_name=ref.stage or "", status=ref.status or "",
        deadline=ref.deadline or "", responsible_id=ref.responsible_id or "", responsible_name=ref.responsible or "",
        closed=ref.closed,
    )
    if changed:
        try:
            from assistant.services.task_google_sync import sync_task_google

            sync_task_google(int(uid), changed, "update")
        except Exception as e:  # noqa: BLE001
            print(f"[tracker_sync] google uid={uid} task={changed.get('id')} err={e!r}")
        try:
            from assistant.realtime import notify as rt

            rt.task_changed(int(changed["id"]), [str(uid)], actor_user_id="")
        except Exception:
            pass
    return {"ok": True, "changed": bool(changed), "link": trackers_store.get_task_link(link["id"]) or link}


def sync_for_task(uid: int, calendar_task_id: Any, *, max_age_sec: int = OPEN_STALE_SEC) -> list[dict[str, Any]]:
    """Связки задачи (для окна задачи): давно не сверенные — сверяем сейчас."""
    from assistant.stores import trackers as trackers_store

    links = trackers_store.tasks_for_calendar_task(uid, calendar_task_id)
    edge = _iso_ago(max_age_sec)
    out = []
    for link in links:
        if str(link.get("synced_at") or "") < edge:
            try:
                link = sync_link(int(uid), link)["link"]
            except Exception as e:  # noqa: BLE001
                print(f"[tracker_sync] task={calendar_task_id} link={link.get('id')} err={e!r}")
        out.append(link)
    # Исполнитель в трекере — не я: чип покажет его имя.
    accounts: dict[str, str] = {}
    for link in out:
        prov = str(link.get("provider") or "")
        if prov not in accounts:
            accounts[prov] = str((trackers_store.get_credentials(uid, prov) or {}).get("account_id") or "")
        rid = str(link.get("responsible_id") or "")
        link["responsible_other"] = bool(rid and accounts[prov] and rid != accounts[prov])
    return out


def sync_due(limit: int = 60) -> int:
    """Фоновая сверка: связки, которые давно не сверяли. Возвращает число изменённых задач."""
    from assistant.stores import trackers as trackers_store

    changed = 0
    for link in trackers_store.links_to_sync(older_than=_iso_ago(STALE_SEC), limit=limit):
        try:
            if sync_link(int(link["owner_user_id"]), link)["changed"]:
                changed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[tracker_sync] link={link.get('id')} err={e!r}")
    return changed
