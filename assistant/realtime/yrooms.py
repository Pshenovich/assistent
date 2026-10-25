"""Комнаты Yjs: совместная правка текста заметки поверх WebSocket.

Сервер не разбирает Yjs — хранит журнал бинарных обновлений, пересылает их участникам
и назначает ровно одного «посевщика» для первого открытия (иначе два клиента
одновременно превратят HTML в документ и текст удвоится). Журнал сжимает клиент:
присылает снимок своего состояния, покрывающий обновления до ``upto``.

Правка мимо Yjs (ИИ, бот, старое приложение) вливается в документ трёхсторонним слиянием
на клиенте: y.reseed несёт old_html (что было) и html (что стало) — затирать документ
целиком нельзя, в нём бывают правки, ещё не попавшие в HTML. Сохраняет HTML автор правки
и «ведущий» комнаты (после чужих правок и слияний), поэтому HTML отстаёт на секунды.

Все методы — только из цикла событий веб-процесса; база — в потоках.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, Callable, Optional

from assistant.realtime.hub import HUB, Connection

SAVE_DELAY_SEC = 2.0
COMPACT_AT = 300  # столько обновлений в журнале — просим клиента прислать снимок
MAX_UPDATE_BYTES = 12 * 1024 * 1024

# (note_id) -> (body_html, revision); подменяется в тестах.
NoteReader = Callable[[int], Optional[tuple[str, int]]]


def _read_note(note_id: int) -> Optional[tuple[str, int]]:
    from assistant.stores import notes as notes_store

    note = notes_store.get_note_by_id(int(note_id))
    if not note:
        return None
    return str(note.get("body") or note.get("description") or ""), int(note.get("revision") or 1)


class YRoom:
    def __init__(
        self, note_id: int, updates: list[bytes], base_revision: int, base_html: Optional[str]
    ) -> None:
        self.note_id = note_id
        self.conns: list[Connection] = []
        self.updates = updates
        self.start_seq = 0  # seq первого элемента журнала
        self.seq = len(updates)  # seq следующего обновления
        self.seeder: Optional[Connection] = None
        self.leader: Optional[Connection] = None
        self.base_revision = base_revision  # ревизия, на которой HTML сохранён из Yjs
        self.base_html = base_html  # тот HTML
        self.merge_old: Optional[str] = base_html  # последний известный HTML — «было» для слияния
        self.pending_merge: Optional[tuple[str, str]] = None  # (было, стало) — влить при входе
        self.save_handle: Optional[asyncio.TimerHandle] = None
        self.compact_pending = False


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _send(conn: Connection, payload: dict[str, Any]) -> None:
    conn.push(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


class YRooms:
    def __init__(self, read_note: NoteReader = _read_note) -> None:
        self.rooms: dict[int, YRoom] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self.read_note = read_note
        HUB.add_listener(self._on_hub_event)

    # ---- загрузка ----
    async def _room(self, note_id: int) -> Optional[YRoom]:
        room = self.rooms.get(note_id)
        if room is not None:
            return room
        lock = self._locks.setdefault(note_id, asyncio.Lock())
        async with lock:
            room = self.rooms.get(note_id)
            if room is not None:
                return room
            from assistant.stores import note_ydoc

            updates, base, base_html = await asyncio.to_thread(note_ydoc.load, note_id)
            note = await asyncio.to_thread(self.read_note, note_id)
            if note is None:
                return None
            html, revision = note
            pending: Optional[tuple[str, str]] = None
            if updates and base != revision:
                # HTML менялся мимо Yjs, пока заметку никто не держал открытой.
                if base_html is not None:
                    print(f"[yjs] outside edit note={note_id} base={base} rev={revision} — merge")
                    pending = (base_html, html)
                else:
                    print(f"[yjs] stale log note={note_id} base={base} rev={revision} — reseed from html")
                    updates = []
            if updates:
                room = YRoom(note_id, updates, base, base_html)
            else:
                room = YRoom(note_id, updates, revision, html)
            room.merge_old = html
            room.pending_merge = pending
            self.rooms[note_id] = room
            return room

    # ---- протокол ----
    async def join(self, conn: Connection, note_id: int) -> None:
        room = await self._room(note_id)
        if room is None:
            _send(conn, {"type": "y.error", "note_id": note_id, "reason": "not_found"})
            return
        if conn not in room.conns:
            room.conns.append(conn)
        await self._send_state(room, conn)
        if room.pending_merge is not None and room.updates:
            old_html, html = room.pending_merge
            room.pending_merge = None
            _send(conn, {"type": "y.reseed", "note_id": note_id, "old_html": old_html, "html": html})
        self._update_leader(room)

    def _update_leader(self, room: YRoom) -> None:
        """Ведущий (первый в комнате) сохраняет HTML и после чужих правок — HTML не отстаёт."""
        leader = room.conns[0] if room.conns else None
        if leader is room.leader:
            return
        room.leader = leader
        if leader is not None:
            _send(leader, {"type": "y.leader", "note_id": room.note_id, "leader": True})

    async def _send_state(self, room: YRoom, conn: Connection) -> None:
        nid = room.note_id
        if room.updates:
            _send(conn, {
                "type": "y.state", "note_id": nid, "role": "sync",
                "updates": [_b64(u) for u in room.updates], "seq": room.seq,
            })
            return
        if room.seeder is None or room.seeder not in room.conns:
            room.seeder = conn
            note = await asyncio.to_thread(self.read_note, nid)
            if note is None:
                _send(conn, {"type": "y.error", "note_id": nid, "reason": "not_found"})
                return
            html, revision = note
            room.base_revision = revision
            room.base_html = html
            room.merge_old = html
            _send(conn, {
                "type": "y.state", "note_id": nid, "role": "seed",
                "html": html, "revision": revision, "seq": room.seq,
            })
            return
        _send(conn, {"type": "y.state", "note_id": nid, "role": "wait", "seq": room.seq})

    def update(self, conn: Connection, note_id: int, data: bytes) -> None:
        room = self.rooms.get(note_id)
        if room is None or conn not in room.conns:
            _send(conn, {"type": "y.error", "note_id": note_id, "reason": "not_joined"})
            return
        if not data or len(data) > MAX_UPDATE_BYTES:
            _send(conn, {"type": "y.error", "note_id": note_id, "reason": "too_large"})
            return
        seq = room.seq
        room.updates.append(data)
        room.seq += 1
        if room.seeder is conn:
            room.seeder = None
        msg = {"type": "y.update", "note_id": note_id, "update": _b64(data), "seq": seq}
        for other in room.conns:
            if other is not conn:
                _send(other, msg)
        _send(conn, {"type": "y.ack", "note_id": note_id, "seq": seq})
        self._schedule_save(room)
        if len(room.updates) > COMPACT_AT and not room.compact_pending:
            room.compact_pending = True
            _send(conn, {"type": "y.compact_request", "note_id": note_id})

    def compact(self, conn: Connection, note_id: int, snapshot: bytes, upto: int) -> None:
        room = self.rooms.get(note_id)
        if room is None or conn not in room.conns:
            return
        room.compact_pending = False
        if not snapshot or len(snapshot) > MAX_UPDATE_BYTES:
            return
        if not (room.start_seq < upto <= room.seq):
            return
        keep = room.updates[upto - room.start_seq:]
        room.updates = [snapshot] + keep
        room.start_seq = upto - 1
        self._schedule_save(room)

    async def leave(self, conn: Connection, note_id: int) -> None:
        room = self.rooms.get(note_id)
        if room is None or conn not in room.conns:
            return
        room.conns.remove(conn)
        if room.conns:
            self._update_leader(room)
            if room.seeder is conn:
                room.seeder = None
                if not room.updates:
                    # Посевщик ушёл, не успев — посев переходит следующему.
                    await self._send_state(room, room.conns[0])
            return
        self.rooms.pop(note_id, None)
        if room.save_handle is not None:
            room.save_handle.cancel()
            room.save_handle = None
        await self._flush(room)

    def mark_saved(self, note_id: int, revision: int, html: str) -> None:
        """HTML сохранён из Yjs на этой ревизии — журнал с ней согласован."""
        room = self.rooms.get(note_id)
        if room is not None:
            room.base_revision = int(revision)
            room.base_html = html
            room.merge_old = html
            self._schedule_save(room)

    # ---- правки мимо Yjs ----
    def _on_hub_event(self, topic: str, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype not in ("note.changed", "note.deleted") or not topic.startswith("note:"):
            return
        try:
            note_id = int(event.get("note_id") or topic.split(":", 1)[1])
        except (TypeError, ValueError):
            return
        if etype == "note.deleted":
            room = self.rooms.pop(note_id, None)
            if room is not None and room.save_handle is not None:
                room.save_handle.cancel()
            asyncio.get_running_loop().create_task(self._delete(note_id))
            return
        if event.get("source") == "yjs":
            return
        room = self.rooms.get(note_id)
        if room is not None and room.conns:
            asyncio.get_running_loop().create_task(self._reseed(room))

    async def _reseed(self, room: YRoom) -> None:
        note = await asyncio.to_thread(self.read_note, room.note_id)
        if note is None or not room.conns:
            return
        html, revision = note
        old_html = room.merge_old
        room.merge_old = html
        if not room.updates or old_html is None or old_html == html:
            return  # документа ещё нет — посевщик возьмёт свежий HTML сам
        # Ведущий вливает правку (было → стало) слиянием; его сохранение обновит base.
        _send(room.conns[0], {
            "type": "y.reseed", "note_id": room.note_id,
            "old_html": old_html, "html": html, "revision": revision,
        })

    # ---- хранение ----
    def _schedule_save(self, room: YRoom) -> None:
        if room.save_handle is not None:
            return
        loop = asyncio.get_running_loop()

        def _fire() -> None:
            room.save_handle = None
            loop.create_task(self._flush(room))

        room.save_handle = loop.call_later(SAVE_DELAY_SEC, _fire)

    async def _flush(self, room: YRoom) -> None:
        from assistant.stores import note_ydoc

        try:
            await asyncio.to_thread(
                note_ydoc.save, room.note_id, list(room.updates), room.base_revision, room.base_html
            )
        except Exception as exc:
            print(f"[yjs] save failed note={room.note_id} err={exc!r}")

    async def _delete(self, note_id: int) -> None:
        from assistant.stores import note_ydoc

        try:
            await asyncio.to_thread(note_ydoc.delete, note_id)
        except Exception as exc:
            print(f"[yjs] delete failed note={note_id} err={exc!r}")


_ROOMS: Optional[YRooms] = None


def rooms() -> YRooms:
    global _ROOMS
    if _ROOMS is None:
        _ROOMS = YRooms()
    return _ROOMS
