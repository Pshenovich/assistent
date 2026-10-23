"""Синхронизация с ящиком: «прочитано» в обе стороны и перенос в «Спам» (с возвратом).

Запись в ящик — отдельным соединением, папка на запись. Права есть не всегда (у Яндекса нужен mail:imap_full):
первая неудачная запись ставит ящику can_write=False — отметки остаются только в Orbita, профиль подскажет,
как дать права. Ничего не удаляем: при переносе без MOVE письмо копируется, в исходной папке — только пометка.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from assistant.integrations import mail_imap
from assistant.stores import mail_accounts, mail_index, mail_senders


def _group(uid: int, keys: list[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """(account_id, folder) → письма индекса."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for k in keys:
        r = mail_index.get(uid, k)
        if r and r.get("uid"):
            out.setdefault((str(r["account_id"]), str(r.get("folder") or "INBOX")), []).append(r)
    return out


def _writable(uid: int, account_id: str) -> Optional[dict[str, Any]]:
    acc = mail_accounts.get(uid, account_id)
    if not acc or acc.get("can_write") is False:
        return None
    return acc


def _with_rw(uid: int, acc: dict[str, Any], fn) -> bool:
    """Выполнить запись; нет прав — запомнить can_write=False. True — получилось."""
    from assistant.services import mail_source

    try:
        mail = mail_source.open_imap(uid, acc)
    except Exception as e:
        print(f"[mail_sync] login uid={uid} acc={acc['account_id']} err={e!r}")
        return False
    try:
        fn(mail)
        mail_accounts.set_can_write(uid, acc["account_id"], True)
        return True
    except PermissionError as e:
        print(f"[mail_sync] no write uid={uid} acc={acc['account_id']} err={e}")
        mail_accounts.set_can_write(uid, acc["account_id"], False)
        return False
    except Exception as e:
        print(f"[mail_sync] write uid={uid} acc={acc['account_id']} err={e!r}")
        return False
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def mark_seen(uid: int, keys: list[str], seen: bool = True) -> int:
    n = 0
    for (aid, folder), rows in _group(uid, keys).items():
        acc = _writable(uid, aid)
        if not acc:
            continue

        def run(mail, folder=folder, rows=rows):
            mail_imap.select_rw(mail, folder)
            mail_imap.set_seen(mail, [int(r["uid"]) for r in rows], seen)

        if _with_rw(uid, acc, run):
            n += len(rows)
    return n


def mark_seen_soon(uid: int, keys: list[str], seen: bool = True) -> None:
    """Из обработчика запроса — в фоне, чтобы не держать пользователя на IMAP."""
    if keys:
        threading.Thread(target=mark_seen, args=(int(uid), list(keys), seen), daemon=True).start()


def pull_seen(uid: int, acc: dict[str, Any], mail, folder: str, rows: list[dict[str, Any]]) -> int:
    """Прочитанное в Gmail/Яндексе → прочитано в Orbita (папка уже открыта синхронизацией)."""
    from assistant.services import mail_inbox

    inbound = [r for r in rows if r.get("direction") == "in" and r.get("uid")]
    if not inbound:
        return 0
    seen = mail_imap.seen_uids(mail, [int(r["uid"]) for r in inbound])
    keys = [r["mail_key"] for r in inbound if int(r["uid"]) in seen]
    if keys:
        mail_inbox.set_read(uid, keys, True)
    return len(keys)


def spam_thread(uid: int, thread_key: str, pattern: str) -> dict[str, Any]:
    """«Спам»: правило «Не показывать» + письма ветки от этого отправителя — в папку спама ящика."""
    from assistant.services import mail_source

    p = mail_senders.normalize(pattern)
    rows = [r for r in mail_index.thread(uid, thread_key) if r.get("direction") == "in"
            and (r.get("from_addr") == p or (p.startswith("@") and str(r.get("from_addr") or "").endswith(p)))]
    res = mail_source.block_sender(uid, p)
    moved: list[dict[str, Any]] = []
    for (aid, folder), items in _group(uid, [r["mail_key"] for r in rows]).items():
        acc = _writable(uid, aid)
        if not acc:
            continue

        def run(mail, folder=folder, items=items):
            junk = mail_imap.junk_folder(mail)
            if not junk:
                raise RuntimeError("нет папки спама")
            mail_imap.select_rw(mail, folder)
            mail_imap.move(mail, [int(r["uid"]) for r in items], junk)
            moved.extend({"account_id": aid, "message_id": r.get("message_id") or "", "from_folder": folder} for r in items)

        _with_rw(uid, acc, run)
    if moved:
        mail_senders.add_moved(uid, p, moved)
    return {**res, "moved": len(moved), "synced": bool(moved) or not rows}


def restore_moved(uid: int, moved: list[dict[str, Any]]) -> int:
    """Блок сняли — вернуть перенесённые в спам письма во «Входящие» (ищем по Message-ID)."""
    n = 0
    by_acc: dict[str, list[dict[str, Any]]] = {}
    for m in moved:
        by_acc.setdefault(str(m.get("account_id")), []).append(m)
    for aid, items in by_acc.items():
        acc = _writable(uid, aid)
        if not acc:
            continue
        done: list[int] = []

        def run(mail, items=items):
            junk = mail_imap.junk_folder(mail)
            if not junk:
                return
            mail_imap.select_rw(mail, junk)
            uids = sorted({u for u in (mail_imap.find_by_message_id(mail, m.get("message_id") or "") for m in items) if u})
            mail_imap.move(mail, uids, '"INBOX"')
            done.extend(uids)

        _with_rw(uid, acc, run)
        n += len(done)
    return n
