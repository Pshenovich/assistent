"""Сигналы «изменилось» из хранилищ — в WebSocket-хаб.

В веб-процессе событие сразу уходит в ``HUB``. В других процессах (Telegram-бот и его
фоновые задачи) — внутренним POST на веб-сервер; включается ``enable_remote()`` при старте
процесса. Ошибки глотаются: уведомление никогда не ломает запись.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import queue
import threading
from typing import Any, Optional

from assistant.realtime.hub import HUB, note_topic, user_topic

_REMOTE_ENABLED = False
_REMOTE_QUEUE: "queue.Queue[tuple[str, dict[str, Any]]]" = queue.Queue(maxsize=2000)
_REMOTE_THREAD: Optional[threading.Thread] = None
_REMOTE_LOCK = threading.Lock()
_REMOTE_TIMEOUT_SEC = 1.5
_REMOTE_BATCH_WAIT_SEC = 0.05

SECRET_HEADER = "X-Leo-Realtime-Secret"


def internal_secret() -> str:
    """Общий секрет веб-сервера и бота: из env или производный от токена бота."""
    explicit = (os.getenv("REALTIME_INTERNAL_SECRET") or "").strip()
    if explicit:
        return explicit
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return ""
    return hashlib.sha256(("leo-realtime:" + token).encode("utf-8")).hexdigest()


def secret_ok(given: str | None) -> bool:
    expected = internal_secret()
    return bool(expected) and hmac.compare_digest(str(given or ""), expected)


def _internal_url() -> str:
    url = (os.getenv("REALTIME_INTERNAL_URL") or "").strip()
    return url or "http://127.0.0.1:8080/internal/realtime/publish"


def enable_remote() -> None:
    """Процесс не веб-сервер: слать события веб-серверу по HTTP."""
    global _REMOTE_ENABLED
    _REMOTE_ENABLED = True


def _remote_worker() -> None:
    import httpx

    with httpx.Client(timeout=_REMOTE_TIMEOUT_SEC) as client:
        while True:
            first = _REMOTE_QUEUE.get()
            batch = [first]
            try:
                while len(batch) < 100:
                    batch.append(_REMOTE_QUEUE.get(timeout=_REMOTE_BATCH_WAIT_SEC))
            except queue.Empty:
                pass
            secret = internal_secret()
            if not secret:
                continue
            body = {"events": [{"topic": t, "event": e} for t, e in batch]}
            try:
                client.post(_internal_url(), json=body, headers={SECRET_HEADER: secret})
            except Exception as exc:  # веб-сервер перезапускается — не страшно
                print(f"[realtime] remote publish failed n={len(batch)} err={exc!r}")


def _ensure_remote_thread() -> None:
    global _REMOTE_THREAD
    if _REMOTE_THREAD is not None and _REMOTE_THREAD.is_alive():
        return
    with _REMOTE_LOCK:
        if _REMOTE_THREAD is not None and _REMOTE_THREAD.is_alive():
            return
        _REMOTE_THREAD = threading.Thread(target=_remote_worker, name="realtime-notify", daemon=True)
        _REMOTE_THREAD.start()


def publish(topic: str, event: dict[str, Any]) -> None:
    try:
        if HUB.publish(topic, event):
            return
        if not _REMOTE_ENABLED:
            return
        _ensure_remote_thread()
        _REMOTE_QUEUE.put_nowait((topic, dict(event)))
    except queue.Full:
        pass
    except Exception as exc:
        print(f"[realtime] publish failed topic={topic} err={exc!r}")


# ---- события хранилищ ----

def note_changed(
    note_id: int | str,
    *,
    revision: Any = None,
    actor_user_id: Any = None,
    source: str = "",
) -> None:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return
    ev: dict[str, Any] = {"type": "note.changed", "note_id": nid}
    if revision is not None:
        ev["revision"] = revision
    if actor_user_id not in (None, ""):
        ev["actor"] = str(actor_user_id)
    if source:
        ev["source"] = str(source)  # "yjs" — HTML, сохранённый из совместного документа
    # Список заметок (тема user:) не дёргаем на каждую правку: его перезагрузка тяжёлая.
    publish(note_topic(nid), ev)


def note_deleted(note_id: int | str, *, owner_user_id: int | str | None = None) -> None:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return
    publish(note_topic(nid), {"type": "note.deleted", "note_id": nid})
    if owner_user_id not in (None, ""):
        notes_list_changed(owner_user_id, note_id=nid)


def notes_list_changed(user_id: int | str, *, note_id: int | None = None) -> None:
    try:
        topic = user_topic(user_id)
    except (TypeError, ValueError):
        return
    ev: dict[str, Any] = {"type": "notes.changed"}
    if note_id is not None:
        ev["note_id"] = int(note_id)
    publish(topic, ev)


def sheets_changed(note_id: int | str, *, source: str = "") -> None:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return
    ev: dict[str, Any] = {"type": "sheets.changed", "note_id": nid}
    if source:
        ev["source"] = str(source)  # "yjs" — тело холста, сохранённое из совместного документа
    publish(note_topic(nid), ev)


def comments_changed(item_kind: str, item_id: Any) -> None:
    """Комментарии/обсуждение: заметке — по её теме, чату из «Чатов» — каждому участнику."""
    kind = str(item_kind or "")
    if kind == "chat":
        try:
            from assistant.stores import chat_threads

            uids = chat_threads.participant_ids(item_id)
        except Exception as exc:
            print(f"[realtime] chat participants failed chat={item_id} err={exc!r}")
            return
        chat_changed(item_id, uids, what="messages")
        return
    if kind != "local":
        return
    try:
        nid = int(item_id)
    except (TypeError, ValueError):
        return
    publish(note_topic(nid), {"type": "comments.changed", "note_id": nid})


def chat_changed(chat_id: int | str, user_ids: list[Any], *, what: str = "meta") -> None:
    """Чат из «Чатов»: what="messages" — новые/удалённые сообщения, "meta" — название, участники, удаление.

    Тема user: — у чата нет своей подписки; в событии только id, данные клиент берёт запросом.
    """
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return
    ev: dict[str, Any] = {"type": "chat.changed", "chat_id": cid, "what": str(what or "meta")}
    for uid in dict.fromkeys(str(u or "").strip() for u in user_ids):
        if not uid:
            continue
        try:
            publish(user_topic(uid), ev)
        except (TypeError, ValueError):
            continue


def task_changed(task_id: int | str, user_ids: list[Any], *, actor_user_id: Any = None) -> None:
    """Новое событие в задаче (сообщение, статус, сдача…) — открытой карточке у постановщика и исполнителя."""
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return
    ev: dict[str, Any] = {"type": "task.changed", "task_id": tid}
    if actor_user_id not in (None, ""):
        ev["actor"] = str(actor_user_id)
    for uid in dict.fromkeys(str(u or "").strip() for u in user_ids):
        try:
            publish(user_topic(uid), ev)
        except (TypeError, ValueError):
            continue
