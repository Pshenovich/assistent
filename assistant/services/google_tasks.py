"""Синхронизация Leo-задач с Google Tasks (best-effort)."""

from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Iterator

from assistant.stores import calendar_tasks as calendar_tasks_store

_TIME_NOTE_RE = re.compile(r"Время:\s*(\S+)")


TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
DEFAULT_TASKLIST = "@default"
PULL_TIMEOUT_SEC = 8.0
PUSH_TIMEOUT_SEC = 8.0
PULL_MIN_INTERVAL_SEC = 45.0

_LAST_PULL: dict[int, float] = {}
_PULL_INFLIGHT: set[int] = set()
_PULL_GUARD = threading.Lock()


def _reset_pull_state_for_tests() -> None:
    with _PULL_GUARD:
        _LAST_PULL.clear()
        _PULL_INFLIGHT.clear()


def _creds(user_id: int):
    from google.oauth2.credentials import Credentials

    from assistant.integrations import google_calendar_oauth

    path = google_calendar_oauth.user_token_path(int(user_id))
    if not path.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(path))
    scopes = {str(s).strip() for s in (creds.scopes or []) if str(s).strip()}
    # Пустой список scopes в JSON не значит, что Tasks нет — пробуем API.
    if scopes and TASKS_SCOPE not in scopes:
        return None
    return creds


def _authorized_http(creds: Any, timeout: float):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    return AuthorizedHttp(creds, http=httplib2.Http(timeout=float(timeout)))


def _service(user_id: int, timeout: float = PUSH_TIMEOUT_SEC):
    creds = _creds(user_id)
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build(
        "tasks",
        "v1",
        http=_authorized_http(creds, timeout),
        cache_discovery=False,
    )


def _default_tasklist_id(svc: Any) -> str:
    """Список, который Google Calendar рисует на сетке — не отдельный Leo."""
    try:
        default = svc.tasklists().get(tasklist=DEFAULT_TASKLIST).execute()
        tid = str((default or {}).get("id") or "").strip()
        if tid:
            return tid
    except Exception as e:
        print(f"[google_tasks] default_list_failed err={e!r}")
    return DEFAULT_TASKLIST


def _tasklist_ids(svc: Any) -> list[str]:
    out: list[str] = [DEFAULT_TASKLIST]
    page: str | None = None
    while True:
        kwargs: dict[str, Any] = {"maxResults": 100}
        if page:
            kwargs["pageToken"] = page
        res = svc.tasklists().list(**kwargs).execute()
        for it in res.get("items") or []:
            tid = str((it or {}).get("id") or "").strip()
            if tid and tid not in out:
                out.append(tid)
        page = str(res.get("nextPageToken") or "").strip() or None
        if not page:
            break
    return out


def due_calendar_date(raw: str) -> date | None:
    """Google Tasks due — только дата; берём YYYY-MM-DD, не UTC-instant."""
    text = str(raw or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def is_tasks_calendar_name(summary: str | None, calendar_id: str | None = None) -> bool:
    name = str(summary or "").strip().lower().replace("ё", "е")
    if name in {"tasks", "задачи", "my tasks", "мои задачи", "google tasks"}:
        return True
    cid = str(calendar_id or "").strip().lower()
    return "#tasks" in cid or cid.startswith("tasks#") or "tasks@" in cid


def start_from_google_task(
    item: dict[str, Any], *, due_day: date, tz: Any
) -> datetime:
    notes = str(item.get("notes") or "")
    match = _TIME_NOTE_RE.search(notes)
    if match:
        raw = match.group(1).strip().rstrip("—-")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(tz)
    return datetime.combine(due_day, dt_time(9, 0), tzinfo=tz)


def notes_without_leo_meta(notes: str) -> str:
    lines = []
    for line in str(notes or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Время:") or stripped.startswith("Исполнитель:"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _due_rfc3339(task: dict[str, Any]) -> str | None:
    """Google берёт только дату; полдень UTC, чтобы не съехать на вчера из‑за TZ."""
    raw = str(task.get("start_at") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{dt.date().isoformat()}T12:00:00.000Z"


def _notes_from_task(task: dict[str, Any]) -> str:
    lines: list[str] = []
    desc = str(task.get("description") or "").strip()
    if desc:
        lines.append(desc)
    start = str(task.get("start_at") or "")
    end = str(task.get("end_at") or "")
    if start:
        lines.append(f"Время: {start} — {end}".strip(" —"))
    who = str(task.get("assignee_name") or task.get("assignee_email") or "").strip()
    if who:
        lines.append(f"Исполнитель: {who}")
    return "\n".join(lines)[:8000]


def _subtask_status(item: dict[str, Any]) -> str:
    return "completed" if item.get("done") else "needsAction"


def _update_task_without_due(
    svc: Any, list_id: str, gid: str, body: dict[str, Any]
) -> dict[str, Any]:
    """PATCH не умеет обнулить due; update без поля снимает дату с сетки."""
    prev = svc.tasks().get(tasklist=list_id, task=gid).execute() or {}
    prev.pop("due", None)
    prev.update(body)
    return svc.tasks().update(tasklist=list_id, task=gid, body=prev).execute()


def push_task(user_id: int, task: dict[str, Any]) -> dict[str, Any] | None:
    svc = _service(user_id)
    if svc is None or not task:
        if svc is None:
            print(f"[google_tasks] push_skipped uid={user_id} no_service")
        return task
    list_id = DEFAULT_TASKLIST
    body = {
        "title": str(task.get("title") or "Задача"),
        "notes": _notes_from_task(task),
        "status": "completed" if task.get("done") else "needsAction",
    }
    # due только с датой — задача висит сверху дня. Время рисуем Calendar event.
    gid = str(task.get("google_task_id") or "").strip()
    remote = None
    if gid:
        try:
            remote = _update_task_without_due(svc, list_id, gid, body)
        except Exception as e:
            print(f"[google_tasks] patch_default_failed id={gid} err={e!r}")
            remote = None
            gid = ""
    if remote is None:
        remote = svc.tasks().insert(tasklist=list_id, body=body).execute()
        gid = str((remote or {}).get("id") or "")
        print(f"[google_tasks] inserted uid={user_id} title={body['title']!r} gid={gid}")
    _sync_subtasks(svc, list_id, gid, task.get("checklist") or [])
    if gid and (
        gid != str(task.get("google_task_id") or "")
        or list_id != str(task.get("google_tasklist_id") or "")
    ):
        updated = calendar_tasks_store.update_task(
            user_id,
            task["id"],
            google_task_id=gid,
            google_tasklist_id=list_id,
        )
        return updated or task
    return task


def _sync_subtasks(svc: Any, list_id: str, parent_id: str, checklist: list[dict[str, Any]]) -> None:
    if not parent_id:
        return
    existing = (
        svc.tasks()
        .list(tasklist=list_id, showCompleted=True, showHidden=True, maxResults=100)
        .execute()
        .get("items")
        or []
    )
    children = [it for it in existing if str(it.get("parent") or "") == parent_id]
    by_title = {str(it.get("title") or "").strip(): it for it in children}
    keep_ids: set[str] = set()
    for item in checklist:
        title = str(item.get("text") or "").strip()
        if not title:
            continue
        prev = by_title.get(title)
        payload = {"title": title, "status": _subtask_status(item)}
        if prev:
            svc.tasks().patch(
                tasklist=list_id, task=str(prev.get("id")), body=payload
            ).execute()
            keep_ids.add(str(prev.get("id") or ""))
        else:
            created = svc.tasks().insert(
                tasklist=list_id, body=payload, parent=parent_id
            ).execute()
            keep_ids.add(str(created.get("id") or ""))
    for child in children:
        cid = str(child.get("id") or "")
        if cid and cid not in keep_ids:
            try:
                svc.tasks().delete(tasklist=list_id, task=cid).execute()
            except Exception:
                pass


def remote_task_notes(user_id: int, task: dict[str, Any] | None) -> str | None:
    svc = _service(user_id)
    gid = str((task or {}).get("google_task_id") or "").strip()
    list_id = str((task or {}).get("google_tasklist_id") or "").strip() or DEFAULT_TASKLIST
    if svc is None or not gid:
        return None
    try:
        remote = svc.tasks().get(tasklist=list_id, task=gid).execute() or {}
    except Exception as e:
        print(f"[google_tasks] get_notes_failed id={gid} err={e!r}")
        return None
    return str(remote.get("notes") or "")


def delete_remote(user_id: int, task: dict[str, Any]) -> None:
    svc = _service(user_id)
    gid = str((task or {}).get("google_task_id") or "").strip()
    list_id = str((task or {}).get("google_tasklist_id") or "").strip()
    if svc is None or not gid:
        return
    if not list_id:
        list_id = DEFAULT_TASKLIST
    try:
        svc.tasks().delete(tasklist=list_id, task=gid).execute()
    except Exception as e:
        print(f"[google_tasks] delete_failed id={gid} err={e!r}")


def schedule_pull(user_id: int, start: datetime, end: datetime) -> None:
    """Импорт Google Tasks в фоне, если краткое ожидание не успело."""
    uid = int(user_id)
    now = time.monotonic()
    with _PULL_GUARD:
        if uid in _PULL_INFLIGHT:
            return
        last = _LAST_PULL.get(uid)
        if last is not None and now - last < PULL_MIN_INTERVAL_SEC:
            return
        _PULL_INFLIGHT.add(uid)

    def _run() -> None:
        ok = False
        try:
            pull_into_leo(uid, start, end, force=True)
            ok = True
        except Exception as e:
            print(f"[google_tasks] pull_bg uid={uid} err={e!r}")
        finally:
            with _PULL_GUARD:
                _PULL_INFLIGHT.discard(uid)
                if ok:
                    _LAST_PULL[uid] = time.monotonic()

    threading.Thread(target=_run, name=f"leo-gtasks-pull-{uid}", daemon=True).start()


def pull_into_leo_brief(
    user_id: int,
    start: datetime,
    end: datetime,
    *,
    wait_sec: float = 2.0,
) -> list[dict[str, Any]]:
    """Дождаться короткого импорта, чтобы Актуальное сразу увидело задачи из GCal."""
    uid = int(user_id)
    now = time.monotonic()
    with _PULL_GUARD:
        last = _LAST_PULL.get(uid)
        if last is not None and now - last < PULL_MIN_INTERVAL_SEC:
            return []
        if uid in _PULL_INFLIGHT:
            return []
        _PULL_INFLIGHT.add(uid)

    imported: list[dict[str, Any]] = []
    ok = {"v": False}

    def _run() -> None:
        try:
            imported.extend(pull_into_leo(uid, start, end, force=True))
            ok["v"] = True
        except Exception as e:
            print(f"[google_tasks] pull_brief uid={uid} err={e!r}")
        finally:
            with _PULL_GUARD:
                _PULL_INFLIGHT.discard(uid)
                if ok["v"]:
                    _LAST_PULL[uid] = time.monotonic()

    t = threading.Thread(target=_run, name=f"leo-gtasks-pull-{uid}", daemon=True)
    t.start()
    t.join(max(0.2, float(wait_sec)))
    return list(imported)


def _due_in_window(due_day: date, start: datetime, end: datetime) -> bool:
    start_d = start.date()
    end_d = end.date()
    if end_d <= start_d:
        end_d = start_d + timedelta(days=1)
    return start_d <= due_day < end_d


def _iter_task_items(svc: Any, list_id: str, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    del start, end
    page: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "tasklist": list_id,
            "showCompleted": False,
            "showHidden": False,
            "maxResults": 100,
        }
        if page:
            kwargs["pageToken"] = page
        try:
            res = svc.tasks().list(**kwargs).execute()
        except Exception as e:
            print(f"[google_tasks] list_failed list={list_id} err={e!r}")
            return
        for it in res.get("items") or []:
            if isinstance(it, dict):
                yield it
        page = str(res.get("nextPageToken") or "").strip() or None
        if not page:
            break


def pull_into_leo(
    user_id: int,
    start: datetime,
    end: datetime,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Подтянуть задачи Google с due в окне; не затирать локальные интервалы."""
    uid = int(user_id)
    if not force:
        now = time.monotonic()
        with _PULL_GUARD:
            last = _LAST_PULL.get(uid)
            if last is not None and now - last < PULL_MIN_INTERVAL_SEC:
                return []
    svc = _service(uid, timeout=PULL_TIMEOUT_SEC)
    if svc is None:
        print(f"[google_tasks] pull_skipped uid={uid} no_service")
        return []
    if not force:
        with _PULL_GUARD:
            _LAST_PULL[uid] = time.monotonic()
    list_ids = _tasklist_ids(svc)
    if not list_ids:
        return []
    tz = start.tzinfo or timezone.utc
    imported: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for list_id in list_ids:
        for it in _iter_task_items(svc, list_id, start, end):
            if it.get("parent"):
                continue
            gid = str(it.get("id") or "").strip()
            if not gid or gid in seen_ids:
                continue
            seen_ids.add(gid)
            existing = calendar_tasks_store.find_by_google_task(user_id, gid)
            if existing:
                continue
            due_day = due_calendar_date(str(it.get("due") or ""))
            if due_day is None or not _due_in_window(due_day, start, end):
                continue
            notes = notes_without_leo_meta(str(it.get("notes") or ""))
            start_at = start_from_google_task(it, due_day=due_day, tz=tz)
            row = calendar_tasks_store.create_task(
                user_id,
                title=str(it.get("title") or "Задача"),
                description=notes,
                start_at=start_at,
            )
            updated = calendar_tasks_store.update_task(
                user_id, row["id"], google_task_id=gid, google_tasklist_id=list_id
            )
            imported.append(updated or row)
    pushed = push_unsynced_in_window(uid, start, end)
    print(
        f"[google_tasks] pull uid={uid} imported={len(imported)} backfilled={len(pushed)}"
    )
    return imported


def push_unsynced_in_window(
    user_id: int, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Дописать timed-слот в календарь для Leo-задач без Google Task."""
    from assistant.services import calendar as calendar_svc

    out: list[dict[str, Any]] = []
    for row in calendar_tasks_store.list_tasks_in_window(user_id, start, end):
        if row.get("done"):
            continue
        if str(row.get("google_task_id") or "").strip():
            continue
        if str(row.get("google_event_id") or "").strip():
            continue
        try:
            updated = calendar_svc.upsert_task_event(user_id, row)
        except Exception as e:
            print(f"[google_tasks] backfill_event id={row.get('id')} err={e!r}")
            continue
        if updated and str(updated.get("google_event_id") or "").strip():
            out.append(updated)
    return out
