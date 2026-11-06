"""Комнаты Yjs: совместная правка заметки поверх WebSocket.

Комната — документ одного листа: основного (``sheet="main"``, текст заметки) или холста
(``sheet`` — id листа kind=board, документ — элементы доски). Ключ комнаты — (note_id, sheet).

Сервер не разбирает Yjs — хранит журнал бинарных обновлений, пересылает их участникам
и назначает ровно одного «посевщика» для первого открытия (иначе два клиента
одновременно превратят HTML в документ и текст удвоится). Журнал сжимает клиент:
присылает снимок своего состояния, покрывающий обновления до ``upto``.

Правка мимо Yjs (ИИ, бот, старое приложение) вливается в документ трёхсторонним слиянием
на клиенте: y.reseed несёт old_html (что было) и html (что стало) — затирать документ
целиком нельзя, в нём бывают правки, ещё не попавшие в HTML. Сохраняет HTML автор правки
и «ведущий» комнаты (после чужих правок и слияний), поэтому HTML отстаёт на секунды.
У холста вместо HTML — JSON доски, слияние — по элементам.

Все методы — только из цикла событий веб-процесса; база — в потоках.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Callable, Optional

from assistant.realtime.hub import HUB, Connection

SAVE_DELAY_SEC = 2.0
COMPACT_AT = 300  # столько обновлений в журнале — просим клиента прислать снимок
MAX_UPDATE_BYTES = 12 * 1024 * 1024
# Слияние правки мимо Yjs считаем сделанным только по y.reseeded от клиента. Не подтвердил
# за это время (фоновая вкладка, полумёртвый сокет) — отдаём слияние следующему участнику.
RESEED_ACK_SEC = 8.0
MAIN = "main"

# (note_id) -> (body_html, revision); подменяется в тестах.
NoteReader = Callable[[int], Optional[tuple[str, int]]]
# (note_id, sheet_id) -> (body, revision) холста; None — листа нет или он не холст.
SheetReader = Callable[[int, str], Optional[tuple[str, int]]]
RoomKey = tuple[int, str]


def _read_note(note_id: int) -> Optional[tuple[str, int]]:
    from assistant.stores import notes as notes_store

    note = notes_store.get_note_by_id(int(note_id))
    if not note:
        return None
    return str(note.get("body") or note.get("description") or ""), int(note.get("revision") or 1)


def _read_sheet(note_id: int, sheet_id: str) -> Optional[tuple[str, int]]:
    from assistant.stores import note_sheets

    try:
        sid = int(sheet_id)
    except (TypeError, ValueError):
        return None
    sheet = note_sheets.get_extra_sheet(int(note_id), sid)
    if not sheet or str(sheet.get("kind") or "") != note_sheets.SHEET_KIND_BOARD:
        return None
    return str(sheet.get("body") or ""), int(sheet.get("revision") or 1)


def normalize_sheet(raw: Any) -> str:
    s = str(raw if raw is not None else "").strip()
    return s if s and s != MAIN else MAIN


class YRoom:
    def __init__(
        self, note_id: int, updates: list[bytes], base_revision: int, base_html: Optional[str], sheet: str = MAIN
    ) -> None:
        self.note_id = note_id
        self.sheet = sheet
        self.conns: list[Connection] = []
        self.updates = updates
        self.start_seq = 0  # seq первого элемента журнала
        self.seq = len(updates)  # seq следующего обновления
        self.seeder: Optional[Connection] = None
        self.leader: Optional[Connection] = None
        self.base_revision = base_revision  # ревизия, на которой HTML сохранён из Yjs
        self.base_html = base_html  # тот HTML
        self.merge_old: Optional[str] = base_html  # последний известный HTML — «было» для слияния
        # Неподтверждённое слияние правки мимо Yjs: {"old", "new", "conn", "at"}; conn=None — ещё не отдано.
        self.reseed: Optional[dict[str, Any]] = None
        self.reseed_timer: Optional[asyncio.TimerHandle] = None
        self.save_handle: Optional[asyncio.TimerHandle] = None
        self.compact_pending = False

    @property
    def key(self) -> RoomKey:
        return (self.note_id, self.sheet)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _send(conn: Connection, payload: dict[str, Any]) -> None:
    conn.push(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _msg(room_or_note: Any, sheet: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Сообщение комнаты: note_id и (для листа) sheet_id — клиент разводит документы по ним."""
    if isinstance(room_or_note, YRoom):
        note_id, sheet = room_or_note.note_id, room_or_note.sheet
    else:
        note_id = int(room_or_note)
    out = {"type": payload.pop("type"), "note_id": note_id}
    if sheet != MAIN:
        out["sheet_id"] = sheet
    out.update(payload)
    return out


class YRooms:
    def __init__(self, read_note: NoteReader = _read_note, read_sheet: SheetReader = _read_sheet) -> None:
        self.rooms: dict[RoomKey, YRoom] = {}
        self._locks: dict[RoomKey, asyncio.Lock] = {}
        self.read_note = read_note
        self.read_sheet = read_sheet
        HUB.add_listener(self._on_hub_event)

    def _read(self, note_id: int, sheet: str) -> Optional[tuple[str, int]]:
        if sheet == MAIN:
            return self.read_note(note_id)
        return self.read_sheet(note_id, sheet)

    def get(self, note_id: int, sheet: str = MAIN) -> Optional[YRoom]:
        return self.rooms.get((int(note_id), normalize_sheet(sheet)))

    # ---- загрузка ----
    async def _room(self, note_id: int, sheet: str = MAIN) -> Optional[YRoom]:
        key = (note_id, sheet)
        room = self.rooms.get(key)
        if room is not None:
            return room
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            room = self.rooms.get(key)
            if room is not None:
                return room
            from assistant.stores import note_ydoc

            updates, base, base_html = await asyncio.to_thread(note_ydoc.load, note_id, sheet)
            note = await asyncio.to_thread(self._read, note_id, sheet)
            if note is None:
                return None
            html, revision = note
            pending: Optional[tuple[str, str]] = None
            if updates and base != revision:
                # HTML менялся мимо Yjs, пока заметку никто не держал открытой
                # (или прошлое слияние так и не подтвердили — база тогда не сдвигалась).
                if base_html is not None:
                    print(f"[yjs] outside edit note={note_id} sheet={sheet} base={base} rev={revision} — merge")
                    pending = (base_html, html)
                else:
                    print(f"[yjs] stale log note={note_id} sheet={sheet} base={base} rev={revision} — reseed from html")
                    updates = []
            if updates:
                room = YRoom(note_id, updates, base, base_html, sheet)
            else:
                room = YRoom(note_id, updates, revision, html, sheet)
            room.merge_old = html
            if pending is not None:
                room.reseed = {"old": pending[0], "new": pending[1], "revision": revision, "conn": None, "at": 0.0}
            self.rooms[key] = room
            return room

    # ---- протокол ----
    async def join(self, conn: Connection, note_id: int, sheet: str = MAIN) -> None:
        sheet = normalize_sheet(sheet)
        room = await self._room(note_id, sheet)
        if room is None:
            _send(conn, _msg(note_id, sheet, {"type": "y.error", "reason": "not_found"}))
            return
        if conn not in room.conns:
            room.conns.append(conn)
        await self._send_state(room, conn)
        rs = room.reseed
        if rs is not None and room.updates and (
            rs["conn"] is None or rs["conn"] not in room.conns or time.monotonic() - rs["at"] >= RESEED_ACK_SEC
        ):
            self._deliver_reseed(room, conn)
        self._update_leader(room)

    # ---- слияние правки мимо Yjs: отдать участнику и дождаться подтверждения ----
    def _deliver_reseed(self, room: YRoom, conn: Connection) -> None:
        rs = room.reseed
        if rs is None:
            return
        rs["conn"] = conn
        rs["at"] = time.monotonic()
        payload: dict[str, Any] = {"type": "y.reseed", "old_html": rs["old"], "html": rs["new"]}
        if rs.get("revision") is not None:
            payload["revision"] = rs["revision"]
        _send(conn, _msg(room, room.sheet, payload))
        self._arm_reseed_timer(room)

    def _arm_reseed_timer(self, room: YRoom) -> None:
        if room.reseed_timer is not None:
            room.reseed_timer.cancel()
        loop = asyncio.get_running_loop()

        def _fire() -> None:
            room.reseed_timer = None
            rs = room.reseed
            if rs is None or self.rooms.get(room.key) is not room:
                return
            others = [c for c in room.conns if c is not rs["conn"]]
            if others:
                print(f"[yjs] reseed not confirmed note={room.note_id} sheet={room.sheet} — handing to next")
                self._deliver_reseed(room, others[0])

        room.reseed_timer = loop.call_later(RESEED_ACK_SEC, _fire)

    def reseeded(self, conn: Connection, note_id: int, sheet: str = MAIN) -> None:
        """Клиент влил правку мимо Yjs (y.reseeded) — её обновление уже ушло перед этим сообщением."""
        room = self.rooms.get((int(note_id), normalize_sheet(sheet)))
        if room is None or room.reseed is None or room.reseed["conn"] is not conn:
            return
        rs, room.reseed = room.reseed, None
        if room.reseed_timer is not None:
            room.reseed_timer.cancel()
            room.reseed_timer = None
        if rs.get("revision") is not None and int(rs["revision"]) >= int(room.base_revision or 0):
            # Журнал теперь содержит правку — база догоняет, иначе при следующей загрузке слили бы её снова.
            room.base_revision = int(rs["revision"])
            room.base_html = rs["new"]
            self._schedule_save(room)

    def _update_leader(self, room: YRoom) -> None:
        """Ведущий (первый в комнате) сохраняет HTML и после чужих правок — HTML не отстаёт."""
        leader = room.conns[0] if room.conns else None
        if leader is room.leader:
            return
        room.leader = leader
        if leader is not None:
            _send(leader, _msg(room, room.sheet, {"type": "y.leader", "leader": True}))

    async def _send_state(self, room: YRoom, conn: Connection) -> None:
        if room.updates:
            _send(conn, _msg(room, room.sheet, {
                "type": "y.state", "role": "sync",
                "updates": [_b64(u) for u in room.updates], "seq": room.seq,
            }))
            return
        if room.seeder is None or room.seeder not in room.conns:
            room.seeder = conn
            note = await asyncio.to_thread(self._read, room.note_id, room.sheet)
            if note is None:
                _send(conn, _msg(room, room.sheet, {"type": "y.error", "reason": "not_found"}))
                return
            html, revision = note
            room.base_revision = revision
            room.base_html = html
            room.merge_old = html
            _send(conn, _msg(room, room.sheet, {
                "type": "y.state", "role": "seed", "html": html, "revision": revision, "seq": room.seq,
            }))
            return
        _send(conn, _msg(room, room.sheet, {"type": "y.state", "role": "wait", "seq": room.seq}))

    def update(self, conn: Connection, note_id: int, data: bytes, sheet: str = MAIN) -> None:
        sheet = normalize_sheet(sheet)
        room = self.rooms.get((note_id, sheet))
        if room is None or conn not in room.conns:
            _send(conn, _msg(note_id, sheet, {"type": "y.error", "reason": "not_joined"}))
            return
        if not data or len(data) > MAX_UPDATE_BYTES:
            _send(conn, _msg(room, sheet, {"type": "y.error", "reason": "too_large"}))
            return
        seq = room.seq
        room.updates.append(data)
        room.seq += 1
        if room.seeder is conn:
            room.seeder = None
        msg = _msg(room, sheet, {"type": "y.update", "update": _b64(data), "seq": seq})
        for other in room.conns:
            if other is not conn:
                _send(other, dict(msg))
        _send(conn, _msg(room, sheet, {"type": "y.ack", "seq": seq}))
        self._schedule_save(room)
        if len(room.updates) > COMPACT_AT and not room.compact_pending:
            room.compact_pending = True
            _send(conn, _msg(room, sheet, {"type": "y.compact_request"}))

    def compact(self, conn: Connection, note_id: int, snapshot: bytes, upto: int, sheet: str = MAIN) -> None:
        room = self.rooms.get((note_id, normalize_sheet(sheet)))
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

    async def leave(self, conn: Connection, note_id: int, sheet: str = MAIN) -> None:
        sheet = normalize_sheet(sheet)
        room = self.rooms.get((note_id, sheet))
        if room is None or conn not in room.conns:
            return
        room.conns.remove(conn)
        if room.conns:
            self._update_leader(room)
            if room.reseed is not None and room.reseed["conn"] is conn:
                self._deliver_reseed(room, room.conns[0])  # ушёл, не подтвердив слияние
            if room.seeder is conn:
                room.seeder = None
                if not room.updates:
                    # Посевщик ушёл, не успев — посев переходит следующему.
                    await self._send_state(room, room.conns[0])
            return
        self.rooms.pop(room.key, None)
        if room.reseed_timer is not None:
            room.reseed_timer.cancel()
            room.reseed_timer = None
        if room.save_handle is not None:
            room.save_handle.cancel()
            room.save_handle = None
        await self._flush(room)

    def reseed_pending(self, note_id: int, sheet: str = MAIN) -> bool:
        """Есть неподтверждённое слияние правки мимо Yjs — базу журнала сдвигать рано."""
        room = self.rooms.get((int(note_id), normalize_sheet(sheet)))
        return bool(room is not None and room.reseed is not None)

    def mark_saved(self, note_id: int, revision: int, html: str, sheet: str = MAIN) -> None:
        """HTML сохранён из Yjs на этой ревизии — журнал с ней согласован."""
        room = self.rooms.get((int(note_id), normalize_sheet(sheet)))
        if room is not None:
            room.merge_old = html
            if room.reseed is None:
                room.base_revision = int(revision)
                room.base_html = html
            # Иначе база остаётся прежней: журнал ещё без правки мимо Yjs, и после выхода всех
            # (или перезапуска) расхождение ревизий снова вызовет слияние при входе.
            self._schedule_save(room)

    # ---- правки мимо Yjs ----
    def _on_hub_event(self, topic: str, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype not in ("note.changed", "note.deleted", "sheets.changed") or not topic.startswith("note:"):
            return
        try:
            note_id = int(event.get("note_id") or topic.split(":", 1)[1])
        except (TypeError, ValueError):
            return
        loop = asyncio.get_running_loop()
        if etype == "note.deleted":
            for key in [k for k in self.rooms if k[0] == note_id]:
                room = self.rooms.pop(key)
                if room.save_handle is not None:
                    room.save_handle.cancel()
            loop.create_task(self._delete(note_id))
            return
        if event.get("source") == "yjs":
            return
        if etype == "note.changed":
            room = self.rooms.get((note_id, MAIN))
            if room is not None and room.conns:
                loop.create_task(self._reseed(room))
            return
        # Листы: правка тела холста мимо Yjs, удаление листа.
        for key, room in list(self.rooms.items()):
            if key[0] == note_id and key[1] != MAIN and room.conns:
                loop.create_task(self._reseed(room))

    async def _reseed(self, room: YRoom) -> None:
        note = await asyncio.to_thread(self._read, room.note_id, room.sheet)
        if note is None:
            if room.sheet != MAIN and self.rooms.get(room.key) is room:
                # Лист удалён (или больше не холст) — комнату закрываем.
                self.rooms.pop(room.key, None)
                if room.save_handle is not None:
                    room.save_handle.cancel()
                for conn in room.conns:
                    _send(conn, _msg(room, room.sheet, {"type": "y.error", "reason": "not_found"}))
                room.conns = []
                await self._delete(room.note_id, room.sheet)
            return
        if not room.conns:
            return
        html, revision = note
        old_html = room.merge_old
        room.merge_old = html
        if not room.updates or old_html is None or old_html == html:
            return  # документа ещё нет — посевщик возьмёт свежий HTML сам
        # Ведущий вливает правку (было → стало) слиянием и подтверждает y.reseeded.
        # Прошлое слияние не подтверждено — «было» берём от него: так вольются обе правки.
        if room.reseed is not None:
            old_html = room.reseed["old"]
        room.reseed = {"old": old_html, "new": html, "revision": revision, "conn": None, "at": 0.0}
        self._deliver_reseed(room, room.conns[0])

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
                note_ydoc.save, room.note_id, list(room.updates), room.base_revision, room.base_html, room.sheet
            )
        except Exception as exc:
            print(f"[yjs] save failed note={room.note_id} sheet={room.sheet} err={exc!r}")

    async def _delete(self, note_id: int, sheet: Optional[str] = None) -> None:
        from assistant.stores import note_ydoc

        try:
            await asyncio.to_thread(note_ydoc.delete, note_id, sheet)
        except Exception as exc:
            print(f"[yjs] delete failed note={note_id} err={exc!r}")


_ROOMS: Optional[YRooms] = None


def rooms() -> YRooms:
    global _ROOMS
    if _ROOMS is None:
        _ROOMS = YRooms()
    return _ROOMS
