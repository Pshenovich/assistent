"""«Заблокировано»: чем стоит задача.

Блокер — другая задача Орбиты, человек из Орбиты, внешний человек, задача Битрикс24 или текст.
У задачи может быть несколько блокеров. Статус задачи — assignee_status='blocked', пока есть хоть
один активный блокер. Хранится в базе задач (calendar_tasks.sqlite), таблица task_blockers.

Кто ставит: исполнитель или постановщик. Человеку-блокеру из Орбиты — карточка в Фокусе
(без пушей), у задачи-блокера — метка «Блокирует …». Когда задача-блокер сдана или отменена,
её блокер снимается сам; не осталось блокеров — задача снова «в работе».
"""

from __future__ import annotations

import re
from typing import Any, Optional

from assistant.stores import calendar_tasks as ct

KINDS = ("task", "user", "external", "bitrix", "text")
MAX_BLOCKERS = 10
MAX_TEXT = 300
MAX_NAME = 120
MAX_URL = 500

_SCHEMA_CONN: object | None = None  # соединение, в котором таблица уже создана (тесты меняют базу)


def _db():
    global _SCHEMA_CONN
    conn = ct._conn()
    if _SCHEMA_CONN is not conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_blockers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                ref_task_id INTEGER,
                ref_user_id TEXT NOT NULL DEFAULT '',
                ref_name TEXT NOT NULL DEFAULT '',
                ref_email TEXT NOT NULL DEFAULT '',
                ref_company TEXT NOT NULL DEFAULT '',
                ref_provider TEXT NOT NULL DEFAULT '',
                ref_external_id TEXT NOT NULL DEFAULT '',
                ref_url TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT '',
                resolved_by TEXT NOT NULL DEFAULT '',
                resolved_how TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_blockers_task ON task_blockers(task_id, resolved_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_blockers_ref_task ON task_blockers(ref_task_id, resolved_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_blockers_ref_user ON task_blockers(ref_user_id, resolved_at)")
        conn.commit()
        _SCHEMA_CONN = conn
    return conn


def _clip(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


_BITRIX_URL_RE = re.compile(r"/tasks/task/view/(\d+)")


def parse_bitrix_ref(raw: str) -> tuple[str, str]:
    """Ссылка на задачу Битрикс24 или её номер → (id, url). Не распознали — ('', '')."""
    s = str(raw or "").strip()
    if not s:
        return "", ""
    if s.isdigit():
        return s, ""
    m = _BITRIX_URL_RE.search(s)
    if m and s.lower().startswith(("http://", "https://")):
        return m.group(1), s[:MAX_URL]
    m = re.fullmatch(r"#?(\d{1,12})", s)
    if m:
        return m.group(1), ""
    return "", ""


def can_set(viewer_id: int | str, task: dict[str, Any] | None) -> bool:
    """Ставить и снимать блокировку: исполнитель или постановщик (и общие задачи выше по цепочке)."""
    if not task:
        return False
    return ct.user_can_access_task(viewer_id, task)


def _row(r) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def _normalize(viewer_id: int | str, raw: dict[str, Any], task_id: int) -> Optional[dict[str, Any]]:
    kind = str((raw or {}).get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError("Неизвестный вид блокера")
    out: dict[str, Any] = {"kind": kind, "ref_task_id": None, "ref_user_id": "", "ref_name": "", "ref_email": "",
                           "ref_company": "", "ref_provider": "", "ref_external_id": "", "ref_url": "", "text": ""}
    if kind == "task":
        ref = ct.get_task_for_user(viewer_id, raw.get("task_id"))
        if not ref:
            raise ValueError("Задача не найдена")
        if int(ref["id"]) == int(task_id):
            raise ValueError("Задача не может блокировать саму себя")
        out["ref_task_id"] = int(ref["id"])
        out["ref_name"] = _clip(ref.get("title"), ct.MAX_TITLE_LEN)
    elif kind == "user":
        uid = str(raw.get("user_id") or "").strip()
        if not uid.isdigit():
            raise ValueError("Выберите человека из Орбиты")
        out["ref_user_id"] = uid
        out["ref_name"] = _clip(raw.get("name"), MAX_NAME)
        out["ref_email"] = _clip(raw.get("email"), MAX_NAME)
    elif kind == "external":
        name = _clip(raw.get("name"), MAX_NAME)
        if not name:
            raise ValueError("Напишите, кто блокирует")
        out["ref_name"] = name
        out["ref_email"] = _clip(raw.get("email"), MAX_NAME)
        out["ref_company"] = _clip(raw.get("company"), MAX_NAME)
    elif kind == "bitrix":
        ext_id = str(raw.get("external_id") or "").strip()
        url = _clip(raw.get("url"), MAX_URL)
        if not ext_id:
            ext_id, parsed_url = parse_bitrix_ref(str(raw.get("ref") or url or ""))
            url = url or parsed_url
        if not ext_id.isdigit():
            raise ValueError("Вставьте ссылку на задачу Битрикс24 или её номер")
        if url and not url.lower().startswith(("http://", "https://")):
            url = ""
        out["ref_provider"] = "bitrix24"
        out["ref_external_id"] = ext_id
        out["ref_url"] = url
        out["ref_name"] = _clip(raw.get("title"), ct.MAX_TITLE_LEN)
    else:
        text = _clip(raw.get("text"), MAX_TEXT)
        if not text:
            raise ValueError("Напишите, чем заблокировано")
        out["text"] = text
    return out


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["kind"] != b["kind"]:
        return False
    k = a["kind"]
    if k == "task":
        return int(a["ref_task_id"] or 0) == int(b["ref_task_id"] or 0)
    if k == "user":
        return str(a["ref_user_id"]) == str(b["ref_user_id"])
    if k == "bitrix":
        return str(a["ref_external_id"]) == str(b["ref_external_id"])
    if k == "external":
        return str(a["ref_name"]).lower() == str(b["ref_name"]).lower()
    return str(a["text"]).lower() == str(b["text"]).lower()


def active_for_task(task_id: int | str) -> list[dict[str, Any]]:
    tid = ct._parse_task_id(task_id)
    if tid is None:
        return []
    with ct._LOCK:
        rows = _db().execute(
            "SELECT * FROM task_blockers WHERE task_id = ? AND resolved_at = '' ORDER BY id", (tid,)
        ).fetchall()
    return [_row(r) for r in rows]


def active_for_tasks(task_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(t) for t in task_ids if str(t).isdigit()})
    out: dict[int, list[dict[str, Any]]] = {}
    if not ids:
        return out
    with ct._LOCK:
        conn = _db()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT * FROM task_blockers WHERE task_id IN ({q}) AND resolved_at = '' ORDER BY id", chunk
            ).fetchall():
                out.setdefault(int(r["task_id"]), []).append(_row(r))
    return out


def blocking_for_tasks(task_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Какие задачи держит каждая из task_ids (активные блокеры вида task)."""
    ids = sorted({int(t) for t in task_ids if str(t).isdigit()})
    out: dict[int, list[dict[str, Any]]] = {}
    if not ids:
        return out
    with ct._LOCK:
        conn = _db()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT * FROM task_blockers WHERE ref_task_id IN ({q}) AND kind = 'task' AND resolved_at = ''"
                " ORDER BY id", chunk
            ).fetchall():
                out.setdefault(int(r["ref_task_id"]), []).append(_row(r))
    return out


def active_for_user(user_id: int | str) -> list[dict[str, Any]]:
    """Активные блокеры «человек из Орбиты» на этого пользователя (по незакрытым задачам)."""
    uid = str(user_id or "").strip()
    if not uid:
        return []
    with ct._LOCK:
        rows = _db().execute(
            "SELECT b.* FROM task_blockers b JOIN calendar_tasks t ON t.id = b.task_id"
            " WHERE b.ref_user_id = ? AND b.kind = 'user' AND b.resolved_at = ''"
            " AND t.done = 0 AND t.outcome = '' ORDER BY b.id",
            (uid,),
        ).fetchall()
    return [_row(r) for r in rows]


def user_ids_blocking_task(task_id: int | str) -> list[str]:
    return [str(b["ref_user_id"]) for b in active_for_task(task_id) if b["kind"] == "user" and b["ref_user_id"]]


def _focus_changed(user_ids: list[str]) -> None:
    ids = [u for u in {str(u) for u in user_ids} if u]
    if not ids:
        return
    try:
        from assistant.realtime import notify as realtime_notify

        for uid in ids:  # без note_id: таблицу встречи не трогаем, только ленту Фокуса
            realtime_notify.publish(realtime_notify.user_topic(uid), {"type": "focus.changed"})
    except Exception as e:
        print(f"[task_blockers] focus notify failed users={ids} err={e!r}")
    try:  # кэш ленты Фокуса живёт в usage_server
        import sys

        srv = sys.modules.get("usage_server")
        cache = getattr(srv, "_FOCUS_CACHE", None) if srv else None
        if isinstance(cache, dict):
            for u in ids:
                cache.pop(int(u) if u.isdigit() else u, None)
    except Exception:
        pass


def _summary(blockers: list[dict[str, Any]]) -> str:
    """Короткая строка «чем заблокировано» — в статус и ленту задачи."""
    parts = []
    for b in blockers:
        k = b["kind"]
        if k == "task":
            parts.append(f"задача «{b['ref_name']}»")
        elif k == "bitrix":
            parts.append(f"Битрикс24 #{b['ref_external_id']}" + (f" «{b['ref_name']}»" if b["ref_name"] else ""))
        elif k == "text":
            parts.append(b["text"])
        else:
            parts.append(b["ref_name"] or b["ref_email"] or "человек")
    return "; ".join(parts)[: ct.MAX_STATUS_NOTE_LEN]


def set_blocked(
    viewer_id: int | str,
    task_id: int | str,
    blockers: list[dict[str, Any]],
    note: str = "",
    *,
    actor_name: str = "",
) -> Optional[dict[str, Any]]:
    """Поставить «Заблокировано» (или добавить блокеры к уже заблокированной). None — нет доступа."""
    task = ct.get_task_for_user(viewer_id, task_id)
    if not can_set(viewer_id, task):
        return None
    if task.get("done") or task.get("outcome"):
        raise ValueError("Задача уже закрыта")
    items = [_normalize(viewer_id, b, int(task["id"])) for b in (blockers or [])]
    items = [b for b in items if b]
    if not items:
        raise ValueError("Укажите, чем заблокировано")
    existing = active_for_task(task["id"])
    prev_auto = _summary(existing)
    fresh: list[dict[str, Any]] = []
    for b in items:
        if any(_same(b, e) for e in existing + fresh):
            continue
        fresh.append(b)
    if len(existing) + len(fresh) > MAX_BLOCKERS:
        raise ValueError(f"Не больше {MAX_BLOCKERS} блокеров")
    now = ct._now_iso()
    viewer = str(viewer_id)
    name = _clip(actor_name, MAX_NAME)
    with ct._LOCK:
        conn = _db()
        for b in fresh:
            conn.execute(
                "INSERT INTO task_blockers (task_id, kind, ref_task_id, ref_user_id, ref_name, ref_email, ref_company,"
                " ref_provider, ref_external_id, ref_url, text, created_by, created_by_name, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(task["id"]), b["kind"], b["ref_task_id"], b["ref_user_id"], b["ref_name"], b["ref_email"],
                 b["ref_company"], b["ref_provider"], b["ref_external_id"], b["ref_url"], b["text"],
                 viewer, name, now),
            )
        conn.commit()
    note = _clip(note, ct.MAX_STATUS_NOTE_LEN)
    active = active_for_task(task["id"])
    summary = _summary(active)
    if fresh or note:
        ct.add_event(task["id"], "blocked", actor_user_id=viewer, actor_name=name,
                     text=note or _summary(fresh) or summary,
                     meta={"blockers": [_public(b) for b in fresh]} if fresh else None)
    values = {
        "assignee_status": "blocked",
        "assignee_status_note": note or _keep_note(task, prev_auto, summary),
        "assignee_status_at": now if task.get("assignee_status") != "blocked" else (task.get("assignee_status_at") or now),
    }
    if viewer == str(task.get("assignee_user_id") or ""):
        values["assignee_seen_at"] = task.get("assignee_seen_at") or now
        ct.mark_triaged(task)
    row = ct._set_columns(int(task["id"]), values)
    _focus_changed([b["ref_user_id"] for b in fresh if b["kind"] == "user"])
    return row


def _keep_note(task: dict[str, Any], prev_auto: str, new_auto: str) -> str:
    """Комментарий человека («чего ждём») не затираем списком блокеров; авто-строку — обновляем."""
    cur = str(task.get("assignee_status_note") or "")
    if task.get("assignee_status") == "blocked" and cur and cur != prev_auto:
        return cur
    return new_auto


def _resolve(blocker_ids: list[int], *, by: str, how: str) -> None:
    if not blocker_ids:
        return
    now = ct._now_iso()
    with ct._LOCK:
        conn = _db()
        conn.executemany(
            "UPDATE task_blockers SET resolved_at = ?, resolved_by = ?, resolved_how = ? WHERE id = ? AND resolved_at = ''",
            [(now, str(by or ""), how, int(i)) for i in blocker_ids],
        )
        conn.commit()


def _after_resolve(task_id: int, *, actor_user_id: str = "", actor_name: str = "", text: str = "",
                   kind: str = "blocker_resolved", prev_auto: str = "") -> Optional[dict[str, Any]]:
    """Блокеров не осталось — задача снова «в работе»; иначе обновить строку статуса."""
    task = ct._get_task_row(int(task_id))
    if not task:
        return None
    left = active_for_task(task_id)
    if text:
        ct.add_event(task_id, kind, actor_user_id=actor_user_id, actor_name=actor_name, text=text)
    if task.get("assignee_status") != "blocked":
        return task
    now = ct._now_iso()
    if left:
        return ct._set_columns(int(task_id), {"assignee_status_note": _keep_note(task, prev_auto, _summary(left))})
    return ct._set_columns(int(task_id), {"assignee_status": "in_progress", "assignee_status_note": "",
                                          "assignee_status_at": now})


def unblock(viewer_id: int | str, task_id: int | str, blocker_id: int | str | None = None,
            *, actor_name: str = "") -> Optional[dict[str, Any]]:
    """Снять блокировку целиком или один блокер. None — нет доступа."""
    task = ct.get_task_for_user(viewer_id, task_id)
    if not can_set(viewer_id, task):
        return None
    active = active_for_task(task["id"])
    if blocker_id not in (None, ""):
        active = [b for b in active if str(b["id"]) == str(blocker_id)]
    users = [b["ref_user_id"] for b in active if b["kind"] == "user"]
    prev_auto = _summary(active_for_task(task["id"]))
    _resolve([int(b["id"]) for b in active], by=str(viewer_id), how="manual")
    left = active_for_task(task["id"])
    if blocker_id in (None, "") or not left:
        if task.get("assignee_status") == "blocked" or active:
            ct.add_event(task["id"], "unblocked", actor_user_id=str(viewer_id), actor_name=_clip(actor_name, MAX_NAME))
        row = _after_resolve(int(task["id"]))
    else:
        row = _after_resolve(int(task["id"]), actor_user_id=str(viewer_id), actor_name=_clip(actor_name, MAX_NAME),
                             text=f"Снят блокер: {_summary(active)}", prev_auto=prev_auto)
    _focus_changed(users)
    return row


def resolve_by_user(user_id: int | str, blocker_id: int | str, how: str, *, actor_name: str = "") -> bool:
    """Человек-блокер из Фокуса: «Готово» (сделал свою часть) или «Не я»."""
    uid = str(user_id or "")
    if how not in ("done", "not_me"):
        raise ValueError("Неизвестное действие")
    b = next((x for x in active_for_user(uid) if str(x["id"]) == str(blocker_id)), None)
    if not b:
        return False
    prev_auto = _summary(active_for_task(b["task_id"]))
    _resolve([int(b["id"])], by=uid, how=how)
    who = _clip(actor_name, MAX_NAME) or b["ref_name"] or "Человек"
    text = f"{who}: своё сделал, больше не блокирую" if how == "done" else f"{who}: я не блокирую эту задачу"
    _after_resolve(int(b["task_id"]), actor_user_id=uid, actor_name=who, text=text, prev_auto=prev_auto)
    _focus_changed([uid])
    return True


def on_task_closed(task_id: int | str, *, how: str = "done") -> None:
    """Задача сдана/принята/отменена: снимаем блокеры, которые она держала, и её собственные."""
    tid = ct._parse_task_id(task_id)
    if tid is None:
        return
    try:
        held = blocking_for_tasks([tid]).get(tid, [])
        own = active_for_task(tid)
        if own:
            _resolve([int(b["id"]) for b in own], by="", how="task_closed")
            _focus_changed([b["ref_user_id"] for b in own if b["kind"] == "user"])
        if not held:
            return
        prev = {int(b["task_id"]): _summary(active_for_task(b["task_id"])) for b in held}
        _resolve([int(b["id"]) for b in held], by="", how=how)
        src = ct._get_task_row(tid) or {}
        label = "сдана" if how == "done" else "отменена"
        for b in held:
            _after_resolve(int(b["task_id"]), text=f"Задача «{src.get('title') or b['ref_name']}» {label} — блокер снят",
                           prev_auto=prev.get(int(b["task_id"]), ""))
            if how == "done":
                _mark_unblocked(int(b["task_id"]))
    except Exception as e:  # блокеры — не повод провалить сдачу задачи
        print(f"[task_blockers] on_task_closed failed task={task_id} err={e!r}")


def on_status_changed(task: dict[str, Any] | None, new_status: str) -> None:
    """Исполнитель сам перевёл задачу из «Заблокировано» в другой статус — блокеры сняты."""
    if not task or new_status == "blocked" or task.get("assignee_status") != "blocked":
        return
    own = active_for_task(task["id"])
    if own:
        _resolve([int(b["id"]) for b in own], by=str(task.get("assignee_user_id") or ""), how="status")
        _focus_changed([b["ref_user_id"] for b in own if b["kind"] == "user"])


# «Можно продолжать»: задача, у которой блокер снялся сам, — карточка исполнителю на 3 дня.
def _mark_unblocked(task_id: int) -> None:
    task = ct._get_task_row(task_id)
    if not task or active_for_task(task_id):
        return
    who = str(task.get("assignee_user_id") or task.get("owner_user_id") or "")
    _focus_changed([who])


def recently_unblocked(user_id: int | str, *, days: int = 3) -> list[dict[str, Any]]:
    """Задачи пользователя (исполнитель), чей блокер-задача сдана за последние дни, без активных блокеров."""
    from datetime import datetime, timedelta, timezone

    uid = str(user_id or "")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with ct._LOCK:
        rows = _db().execute(
            "SELECT b.task_id, MAX(b.resolved_at) AS at, b.ref_name FROM task_blockers b"
            " JOIN calendar_tasks t ON t.id = b.task_id"
            " WHERE b.kind = 'task' AND b.resolved_how = 'done' AND b.resolved_at >= ?"
            " AND t.assignee_user_id = ? AND t.done = 0 AND t.outcome = '' AND t.assignee_status != 'blocked'"
            " GROUP BY b.task_id ORDER BY at DESC LIMIT 10",
            (since, uid),
        ).fetchall()
    out = []
    for r in rows:
        task = ct._get_task_row(int(r["task_id"]))
        if task:
            out.append({"task": task, "at": str(r["at"]), "blocker_title": str(r["ref_name"] or "")})
    return out


def _public(b: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": int(b["id"]) if b.get("id") else None, "kind": b["kind"]}
    if b["kind"] == "task":
        out.update(task_id=b["ref_task_id"], title=b["ref_name"])
    elif b["kind"] == "user":
        out.update(user_id=b["ref_user_id"], name=b["ref_name"], email=b["ref_email"])
    elif b["kind"] == "external":
        out.update(name=b["ref_name"], email=b["ref_email"], company=b["ref_company"])
    elif b["kind"] == "bitrix":
        out.update(provider=b["ref_provider"], external_id=b["ref_external_id"], url=b["ref_url"], title=b["ref_name"])
    else:
        out.update(text=b["text"])
    if b.get("created_by") is not None:
        out.update(created_by=str(b.get("created_by") or ""), created_by_name=str(b.get("created_by_name") or ""),
                   created_at=str(b.get("created_at") or ""))
    return out


def public_blockers(viewer_id: int | str, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Блокеры для клиента. Название задачи-блокера — свежее, если смотрящему она доступна."""
    out = []
    for b in blockers:
        p = _public(b)
        if b["kind"] == "task" and b.get("ref_task_id"):
            ref = ct._get_task_row(int(b["ref_task_id"]))
            p["accessible"] = bool(ref) and ct.user_can_access_task(viewer_id, ref)
            if ref and p["accessible"]:
                p["title"] = ref.get("title") or p.get("title")
                p["done"] = bool(ref.get("done"))
            elif not p["accessible"]:
                p["title"] = p.get("title") or "Задача"
        out.append(p)
    return out


def public_blocking(viewer_id: int | str, held: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Какие задачи держит эта. Название чужой недоступной задачи не показываем — «задача <Имя>»."""
    out = []
    seen: set[int] = set()
    for b in held:
        tid = int(b["task_id"])
        if tid in seen:
            continue
        seen.add(tid)
        t = ct._get_task_row(tid)
        if not t:
            continue
        ok = ct.user_can_access_task(viewer_id, t)
        who = str(t.get("assignee_name") or b.get("created_by_name") or "").strip()
        out.append({
            "task_id": tid if ok else None,
            "title": str(t.get("title") or "") if ok else (f"задача {who}" if who else "чужая задача"),
            "accessible": ok,
            "by_name": str(b.get("created_by_name") or ""),
        })
    return out


def enrich(viewer_id: int | str, tasks: list[dict[str, Any]]) -> None:
    """Дописывает в словари задач (как из _row_to_dict / _task_api) поля blockers и blocking."""
    ids = [int(t["id"]) for t in tasks if str(t.get("id") or "").isdigit()]
    if not ids:
        return
    own = active_for_tasks(ids)
    held = blocking_for_tasks(ids)
    for t in tasks:
        try:
            tid = int(t.get("id"))
        except (TypeError, ValueError):
            continue
        t["blockers"] = public_blockers(viewer_id, own.get(tid, []))
        t["blocking"] = public_blocking(viewer_id, held.get(tid, []))
