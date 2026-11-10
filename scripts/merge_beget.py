#!/usr/bin/env python3
"""Слияние старых данных Beget (код 01.11, id < 10 000 000) с новым продом. План: docs/beget-merge-plan.md.

  merge_beget.py unpack <каталог спасения> <old_root>
        спасённая копия (*.sqlite.gz + state.tar.gz, scripts/rescue_old_server.sh) → дерево как /opt/assistant
  merge_beget.py plan  --old <old_root> --new <new_root> [--report FILE] [--now ISO]
        только отчёт: слияние делается во временный каталог и выбрасывается
  merge_beget.py apply --old <old_root> --new <new_root> --out <пустой каталог> [--report FILE] [--now ISO]
        результат — в --out (базы и файлы состояния в раскладке /opt/assistant); живые базы не трогаются.
        Установка на прод (сервисы остановлены): удалить *-wal/*-shm у подменяемых баз, rsync -a <out>/ /opt/assistant/

Правила (кратко): старое вливается в копию нового; новое побеждает при конфликте; ничего не удаляется.
Люди: Telegram id T, вошедший на новом по почте (accounts.user_id = S, tg_user_id = T) → все его старые строки на S.
Документы: полный дубль на новом — не переносим (ссылки ведут на новый), похожий — переносим с «(до 01.11)».
Старая БЗ (knowledge_bases/kb_*) — не таблицами, а личными документами владельца базы.
Напоминания из прошлого: разовые — закрыты без отправки, повторяющиеся — на ближайшее будущее срабатывание.
Кредиты: старый баланс + движения на новом без стартового подарка. Курсор срочных из чатов — в конец.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
OLD_SUFFIX = " (до 01.11)"
CUTOFF_LABEL = "01.11"

# Базы-кэши и то, что собирается иначе: не переносим как таблицы.
SKIP_DBS = {
    "data/gcal_push.sqlite",
    "data/calendar_mirror.sqlite",
    "data/push_subscriptions.sqlite",
    "data/knowledge_bases.sqlite",  # → личные документы (kb_to_notes)
    "data/analytics.sqlite",
    "usage.sqlite",  # пустой файл в корне (старый путь)
}
SKIP_TABLES = {"_seq_bootstrap", "_litestream_seq", "_litestream_lock", "email_codes", "tg_link_tokens"}
SKIP_TABLE_RE = re.compile(r"^(sqlite_|mail_fts_|mail_memory_fts_)")
# Колонки, где лежит id человека в Орбите (переписываются по owner_map).
OWNER_COLS = {
    "user_id", "owner_user_id", "member_user_id", "author_user_id", "assignee_user_id", "actor_user_id",
    "requester_user_id", "by_user_id", "owner_uid", "telegram_user_id", "owner_telegram_user_id",
    "created_by", "added_by", "verified_by", "granted_by",
}
# Где id — это «сырой» Telegram (участники групп, отправители, учёт вызовов): не переписываем.
NO_OWNER_REMAP_DBS = {"data/chat_digest.sqlite", "assistant/lib/usage.sqlite", "data/site_chat.sqlite"}
NOTE_ITEM_KINDS = {"local", "note", "doc"}
# Содержимое документа: у полного дубля не переносим (есть на новом).
NOTE_CONTENT_TABLES = {
    "note_sheets", "note_sheet_prefs", "note_versions", "item_state", "note_ydoc", "note_sheet_ydoc",
    "transcript_segments",
}
# Новая строка побеждает, пустые поля дополняются старыми.
FILL_GAPS = {"user_profiles", "mail_settings", "focus_items", "tracker_settings", "user_context", "org_settings"}
ROLE_RANK = {"owner": 3, "admin": 2, "member": 1}
SEM_NOTE_KEY = re.compile(r"^(note|doc|tr_sum|tr_txt):(\d+)")
# Файлы на пользователя: копируются, только если на новом их нет.
USER_TOKEN_DIRS = [
    "google_user_tokens", "zoom_user_tokens", "todoist_user_tokens", "yandex_disk_user_tokens",
    "bitrix_mcp_user_tokens", "telemost_user_tokens",
]
USER_FILE_DIRS = ["data/zoom_recent_meetings", "contacts_user", "assistant/stores/contacts_user"]
USER_TREE_DIRS = ["mail_attachments"]  # <uid>/...
UNION_JSON = [
    "data/meeting_invites_notified.json", "data/meeting_reminders_sent.json",
    "data/allowed_telegram_access.json", "data/allowed_telegram_access_donatello.json",
    "data/access_requests.json", "data/access_requests_donatello.json",
    "data/telegram_registry.json", "data/telegram_registry_donatello.json",
    "data/research_jobs.json",
]
UID_PREFIX_JSON = {"data/meeting_invites_notified.json", "data/meeting_reminders_sent.json", "data/research_jobs.json"}


# ---------------------------------------------------------------- утилиты


def db_files(root: Path) -> list[str]:
    out = []
    for p in [root / "notes.sqlite", root / "assistant/lib/usage.sqlite", *sorted((root / "data").glob("*.sqlite"))]:
        if p.is_file() and p.stat().st_size > 0:
            out.append(p.relative_to(root).as_posix())
    return out


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def tables(c: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] or "" for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}


def columns(c: sqlite3.Connection, t: str) -> list[str]:
    return [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def norm_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"[#*_`>|\-\[\]()]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_lines(s: str) -> set[str]:
    s = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", s or "", flags=re.I)
    return {x for x in (norm_text(line) for line in s.splitlines()) if len(x) >= 3}


def norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def now_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- контекст и отчёт


@dataclass
class TableStat:
    old: int = 0
    inserted: int = 0
    skipped: int = 0  # пропущено по правилу (дубль, кэш, отработано)
    conflict: int = 0  # ключ уже есть на новом — новое победило
    filled: int = 0  # строка нового дополнена старыми полями
    new_before: int = 0
    after: int = 0


@dataclass
class Ctx:
    old: Path
    out: Path
    now: datetime
    owner_map: dict[str, str] = field(default_factory=dict)
    org_map: dict[str, str] = field(default_factory=dict)
    note_map: dict[int, int] = field(default_factory=dict)  # старый документ-дубль → новый
    note_skip: set[int] = field(default_factory=set)
    tag_map: dict[str, dict[int, int]] = field(default_factory=lambda: {"tags": {}, "hashtags": {}})
    events_ok: set[int] = field(default_factory=set)
    mail_ok: dict[str, set[tuple[str, str]]] = field(default_factory=lambda: defaultdict(set))
    stats: dict[str, TableStat] = field(default_factory=dict)
    people: dict[str, dict[str, Any]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    dup_exact: list[tuple[str, int, int, str]] = field(default_factory=list)
    dup_similar: list[tuple[str, int, int, str]] = field(default_factory=list)
    kb_returned: list[tuple[str, str, str]] = field(default_factory=list)  # (uid, база, документ)
    reminders_closed: list[tuple[str, str, str]] = field(default_factory=list)
    reminders_moved: list[tuple[str, str, str, str]] = field(default_factory=list)
    credits: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)
    tokens: list[tuple[str, str, str]] = field(default_factory=list)  # (uid, сервис, скопирован/уже есть)
    notes: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)

    def stat(self, db: str, t: str) -> TableStat:
        return self.stats.setdefault(f"{db}:{t}", TableStat())

    def uid(self, v: Any) -> Any:
        """id человека по owner_map, тип сохраняется (int остаётся int)."""
        if v is None or v == "":
            return v
        s = str(v).strip()
        if s.lstrip("-").isdigit():
            s = str(int(s))
        new = self.owner_map.get(s)
        if new is None:
            return v
        return int(new) if isinstance(v, int) else new

    def org(self, v: Any) -> Any:
        if v is None or v == "":
            return v
        new = self.org_map.get(str(v))
        if new is None:
            return v
        return int(new) if isinstance(v, int) else new

    def note(self, v: Any) -> Any:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return v
        if n in self.note_map:
            return self.note_map[n] if isinstance(v, int) else str(self.note_map[n])
        return v


# ---------------------------------------------------------------- карта людей и организаций


def build_owner_map(out_notes: sqlite3.Connection) -> dict[str, str]:
    m: dict[str, str] = {}
    t = tables(out_notes)
    if "accounts" in t:
        for uid, tg in out_notes.execute("SELECT user_id, tg_user_id FROM accounts WHERE tg_user_id IS NOT NULL AND tg_user_id != ''"):
            if str(uid) != str(tg):
                m[str(int(tg))] = str(int(uid))
    aliases: dict[str, str] = {}
    if "account_aliases" in t:
        aliases = {str(a): str(b) for a, b in out_notes.execute("SELECT old_user_id, new_user_id FROM account_aliases")}

    def follow(x: str) -> str:
        seen = set()
        while x in aliases and x not in seen:
            seen.add(x)
            x = aliases[x]
        return x

    for k in list(aliases):
        target = follow(k)
        m.setdefault(k, m.get(target, target))
    for k, v in list(m.items()):
        m[k] = follow(v)
    return {k: v for k, v in m.items() if k != v}


def merge_orgs(ctx: Ctx, old: sqlite3.Connection, out: sqlite3.Connection) -> None:
    st = ctx.stat("notes.sqlite", "orgs")
    ot, nt = tables(old), tables(out)
    if "orgs" not in ot:
        return
    new_orgs = [dict(r) for r in out.execute("SELECT * FROM orgs")] if "orgs" in nt else []
    new_domains = {r[0]: str(r[1]) for r in out.execute("SELECT domain, org_id FROM org_domains")} if "org_domains" in nt else {}
    old_domains = defaultdict(list)
    if "org_domains" in ot:
        for d, oid in old.execute("SELECT domain, org_id FROM org_domains"):
            old_domains[str(oid)].append(d)
    st.new_before = len(new_orgs)
    for r in old.execute("SELECT * FROM orgs"):
        o = dict(r)
        st.old += 1
        oid = str(o["id"])
        match = next((new_domains[d] for d in old_domains[oid] if d in new_domains), None)
        if match is None:
            match = next((str(n["id"]) for n in new_orgs if o.get("slug") and n.get("slug") == o.get("slug")), None)
        if match is None:
            creator = str(ctx.uid(o.get("created_by")))
            match = next((str(n["id"]) for n in new_orgs
                          if norm_title(n.get("name")) == norm_title(o.get("name")) and str(n.get("created_by")) == creator), None)
        if match is not None:
            ctx.org_map[oid] = match
            st.conflict += 1
            ctx.orgs.append(f"«{o.get('name')}» (старый id {oid}) слита с новой организацией {match}: переносим только участников и приглашения")
            continue
        cols = [c for c in columns(old, "orgs") if c in columns(out, "orgs")]
        o["created_by"] = ctx.uid(o.get("created_by"))
        slug = o.get("slug")
        if slug and out.execute("SELECT 1 FROM orgs WHERE slug = ?", (slug,)).fetchone():
            o["slug"] = f"{slug}-old"
        out.execute(f"INSERT INTO orgs ({','.join(map(q, cols))}) VALUES ({','.join('?' * len(cols))})", [o[c] for c in cols])
        st.inserted += 1
        ctx.orgs.append(f"«{o.get('name')}» (id {oid}) перенесена целиком (на новом пары нет)")


def merge_org_members(ctx: Ctx, old: sqlite3.Connection, out: sqlite3.Connection) -> None:
    st = ctx.stat("notes.sqlite", "org_members")
    if "org_members" not in tables(old):
        return
    st.new_before = out.execute("SELECT count(*) FROM org_members").fetchone()[0]
    cols = [c for c in columns(old, "org_members") if c in columns(out, "org_members")]
    for r in old.execute("SELECT * FROM org_members"):
        st.old += 1
        m = dict(r)
        m["org_id"] = ctx.org(m["org_id"])
        m["user_id"] = ctx.uid(m.get("user_id"))
        m["added_by"] = ctx.uid(m.get("added_by"))
        if m.get("user_id") not in (None, ""):
            ex = out.execute("SELECT id, role FROM org_members WHERE org_id = ? AND user_id = ?", (m["org_id"], m["user_id"])).fetchone()
        else:
            ex = out.execute("SELECT id, role FROM org_members WHERE org_id = ? AND invited_key = ?", (m["org_id"], m.get("invited_key"))).fetchone()
        if ex:
            st.conflict += 1
            if ROLE_RANK.get(str(m.get("role")), 0) > ROLE_RANK.get(str(ex[1]), 0):
                out.execute("UPDATE org_members SET role = ? WHERE id = ?", (m["role"], ex[0]))
                st.filled += 1
            continue
        if out.execute("SELECT 1 FROM org_members WHERE id = ?", (m["id"],)).fetchone():
            m["id"] = None
        out.execute(f"INSERT INTO org_members ({','.join(map(q, cols))}) VALUES ({','.join('?' * len(cols))})", [m[c] for c in cols])
        st.inserted += 1


# ---------------------------------------------------------------- документы


def note_fulltext(c: sqlite3.Connection, note_id: int, body: str, has_sheets: bool) -> str:
    parts = [body or ""]
    if has_sheets:
        parts += [r[0] or "" for r in c.execute("SELECT body FROM note_sheets WHERE note_id = ? ORDER BY position, id", (note_id,))]
    return "\n".join(parts)


def _note_index(out: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Документы нового по владельцу + документы БЗ организаций, где он участник."""
    nt = tables(out)
    has_sheets = "note_sheets" in nt
    by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_notes: dict[int, dict[str, Any]] = {}
    for r in out.execute("SELECT id, user_id, title, body FROM local_notes"):
        text = note_fulltext(out, r[0], r[3], has_sheets)
        d = {"id": r[0], "title": r[2], "hash": hashlib.sha1(norm_text(text).encode()).hexdigest(),
             "empty": not norm_text(text), "lines": norm_lines(text)}
        all_notes[r[0]] = d
        by_owner[str(r[1])].append(d)
    if "org_kb_nodes" in nt and "org_members" in nt:
        kb = defaultdict(list)
        for org_id, note_id in out.execute("SELECT org_id, note_id FROM org_kb_nodes WHERE note_id IS NOT NULL"):
            if note_id in all_notes:
                kb[str(org_id)].append(all_notes[note_id])
        for org_id, uid in out.execute("SELECT org_id, user_id FROM org_members WHERE user_id IS NOT NULL"):
            for d in kb.get(str(org_id), []):
                if d not in by_owner[str(uid)]:
                    by_owner[str(uid)].append(d)
    return by_owner


def find_dup(index: dict[str, list[dict[str, Any]]], owner: str, title: str, text: str) -> tuple[str, Optional[int]]:
    """('exact', id) — тот же текст; ('similar', id) — тот же заголовок или ≥80% общих строк; ('', None)."""
    h = hashlib.sha1(norm_text(text).encode()).hexdigest()
    empty = not norm_text(text)
    lines = norm_lines(text)
    similar = None
    for d in index.get(owner, []):
        same_title = norm_title(d["title"]) == norm_title(title)
        if d["hash"] == h and (not empty or same_title):
            return "exact", d["id"]
        if similar is None:
            if same_title:
                similar = d["id"]
            elif lines and d["lines"]:
                common = len(lines & d["lines"]) / max(len(lines), len(d["lines"]))
                if common >= 0.8:
                    similar = d["id"]
    return ("similar", similar) if similar is not None else ("", None)


def merge_local_notes(ctx: Ctx, old: sqlite3.Connection, out: sqlite3.Connection, index: dict[str, list[dict[str, Any]]]) -> None:
    st = ctx.stat("notes.sqlite", "local_notes")
    st.new_before = out.execute("SELECT count(*) FROM local_notes").fetchone()[0]
    if "local_notes" not in tables(old):
        return
    has_sheets = "note_sheets" in tables(old)
    cols = [c for c in columns(old, "local_notes") if c in columns(out, "local_notes")]
    for r in old.execute("SELECT * FROM local_notes ORDER BY id"):
        st.old += 1
        n = dict(r)
        n["user_id"] = str(ctx.uid(n["user_id"]))
        owner = n["user_id"]
        ctx.people[owner]["docs_old"] += 1
        kind, new_id = find_dup(index, owner, n.get("title") or "", note_fulltext(old, n["id"], n.get("body") or "", has_sheets))
        if kind == "exact":
            ctx.note_map[int(n["id"])] = int(new_id)  # type: ignore[arg-type]
            ctx.note_skip.add(int(n["id"]))
            ctx.dup_exact.append((owner, int(n["id"]), int(new_id), n.get("title") or ""))  # type: ignore[arg-type]
            ctx.people[owner]["docs_dup"] += 1
            st.skipped += 1
            continue
        if kind == "similar":
            n["title"] = (n.get("title") or "") + OLD_SUFFIX
            ctx.dup_similar.append((owner, int(n["id"]), int(new_id), n["title"]))  # type: ignore[arg-type]
            ctx.people[owner]["docs_similar"] += 1
        out.execute(f"INSERT INTO local_notes ({','.join(map(q, cols))}) VALUES ({','.join('?' * len(cols))})", [n[c] for c in cols])
        st.inserted += 1
        ctx.people[owner]["docs_in"] += 1


def kb_to_notes(ctx: Ctx, out: sqlite3.Connection, index: dict[str, list[dict[str, Any]]]) -> None:
    """Старая БЗ → личные документы владельца базы (решение владельца 09.11)."""
    p = ctx.old / "data/knowledge_bases.sqlite"
    st = ctx.stat("data/knowledge_bases.sqlite", "kb_documents→local_notes")
    if not p.is_file():
        return
    kb = ro(p)
    t = tables(kb)
    if not {"knowledge_bases", "kb_documents", "kb_chunks"} <= set(t):
        return
    bases = {r["id"]: dict(r) for r in kb.execute("SELECT * FROM knowledge_bases")}
    for d in kb.execute("SELECT * FROM kb_documents ORDER BY kb_id, rowid"):
        st.old += 1
        base = bases.get(d["kb_id"])
        if not base:
            st.skipped += 1
            continue
        owner = str(ctx.uid(base["owner_telegram_user_id"]))
        title = (d["title"] or "").strip() or (base["title"] or "").strip() or "(без названия)"
        parts, last_heading = [], None
        for ch in kb.execute("SELECT heading, text FROM kb_chunks WHERE kb_id = ? AND doc_id = ? ORDER BY chunk_index, id",
                             (d["kb_id"], d["doc_id"])):
            heading = (ch["heading"] or "").strip()
            if heading and heading != last_heading and heading != title:
                parts.append(f"## {heading}")
            last_heading = heading
            if (ch["text"] or "").strip():
                parts.append(ch["text"].strip())
        body = "\n\n".join(parts)
        kind, new_id = find_dup(index, owner, title, body)
        if kind == "exact":
            ctx.dup_exact.append((owner, -1, int(new_id), f"[БЗ «{base['title']}»] {title}"))  # type: ignore[arg-type]
            ctx.people[owner]["kb_dup"] += 1
            st.skipped += 1
            continue
        if kind == "similar":
            title += OLD_SUFFIX
            ctx.dup_similar.append((owner, -1, int(new_id), f"[БЗ «{base['title']}»] {title}"))  # type: ignore[arg-type]
        meta = {"imported_from": "old_kb", "kb_title": base["title"] or "", "source_url": d["url"] or base["source_url"] or "",
                "source_type": base["source_type"] or ""}
        ts = now_iso(ctx.now)
        created = base.get("created_at") or ts
        updated = d["modified_at"] or base.get("updated_at") or ts
        out.execute(
            "INSERT INTO local_notes (user_id, title, body, created_at, updated_at, role, kb_enabled, revision, meta_json, primary_sheet_title) "
            "VALUES (?, ?, ?, ?, ?, '', 1, 1, ?, '')",
            (owner, title, body, str(created), str(updated), json.dumps(meta, ensure_ascii=False)),
        )
        st.inserted += 1
        ctx.kb_returned.append((owner, base["title"] or "", title))
        ctx.people[owner]["kb_in"] += 1
    if "kb_members" in t:
        n = kb.execute("SELECT count(*) FROM kb_members").fetchone()[0]
        if n:
            ctx.notes.append(f"Доступы старой БЗ (kb_members, {n} строк) не переносятся — документы стали личными у владельцев баз.")
    kb.close()


# ---------------------------------------------------------------- общий перенос строк


def map_row(ctx: Ctx, db: str, t: str, row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Переписать id людей, организаций, документов. None — строку не переносим."""
    remap_owner = db not in NO_OWNER_REMAP_DBS
    for c in list(row):
        if remap_owner and c in OWNER_COLS:
            row[c] = ctx.uid(row[c])
        elif c == "org_id":
            row[c] = ctx.org(row[c])
    kind_col = "item_kind" if "item_kind" in row else ("kind" if t == "discussion_unread" else None)
    if kind_col and str(row.get(kind_col)) in NOTE_ITEM_KINDS and "item_id" in row:
        if t in NOTE_CONTENT_TABLES and _is_skipped(ctx, row["item_id"]):
            return None
        row["item_id"] = ctx.note(row["item_id"])
    if "note_id" in row:
        if t in NOTE_CONTENT_TABLES and _is_skipped(ctx, row["note_id"]):
            return None
        row["note_id"] = ctx.note(row["note_id"])
    if t in {"sem_sources", "sem_chunks"}:
        m = SEM_NOTE_KEY.match(str(row.get("source_key") or ""))
        if m and int(m.group(2)) in ctx.note_skip:
            return None
        if str(row.get("source_key") or "").startswith("kb"):
            return None
    if t in {"tag_links", "hashtag_links"}:
        tmap = ctx.tag_map["tags" if t == "tag_links" else "hashtags"]
        key = "tag_id" if t == "tag_links" else "hashtag_id"
        row[key] = tmap.get(int(row[key]), row[key])
    if t in {"orbita_event_attendees", "orbita_event_overrides"} and int(row["event_id"]) not in ctx.events_ok:
        return None
    if t in {"mail_fts", "mail_memory_fts"}:
        key = (str(row.get("owner_user_id")), str(row.get("mail_key") or row.get("message_id")))
        if key not in ctx.mail_ok[t]:
            return None
    return row


def _is_skipped(ctx: Ctx, v: Any) -> bool:
    try:
        return int(v) in ctx.note_skip
    except (TypeError, ValueError):
        return False


def copy_table(ctx: Ctx, db: str, old: sqlite3.Connection, out: sqlite3.Connection, t: str,
               after_insert: Optional[Callable[[dict[str, Any], bool], None]] = None,
               transform: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None) -> None:
    st = ctx.stat(db, t)
    ot, nt = tables(old), tables(out)
    if t not in ot:
        return
    virtual = "VIRTUAL TABLE" in ot[t].upper()
    if t not in nt:
        out.execute(ot[t].replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
                    if not virtual else ot[t].replace("CREATE VIRTUAL TABLE ", "CREATE VIRTUAL TABLE IF NOT EXISTS ", 1))
    st.new_before = out.execute(f"SELECT count(*) FROM {q(t)}").fetchone()[0]
    ocols = columns(old, t)
    cols = [c for c in ocols if c in columns(out, t)]
    pk = [r[1] for r in sorted(out.execute(f'PRAGMA table_info("{t}")'), key=lambda r: r[5]) if r[5]]
    verb = "INSERT" if virtual else "INSERT OR IGNORE"
    sql = f"{verb} INTO {q(t)} ({','.join(map(q, cols))}) VALUES ({','.join('?' * len(cols))})"
    for r in old.execute(f"SELECT {','.join(map(q, ocols))} FROM {q(t)}"):
        st.old += 1
        row = map_row(ctx, db, t, dict(r))
        if row is not None and transform:
            row = transform(row)
        if row is None:
            st.skipped += 1
            continue
        cur = out.execute(sql, [row[c] for c in cols])
        ok = cur.rowcount > 0
        if ok:
            st.inserted += 1
        else:
            st.conflict += 1
            if t in FILL_GAPS and pk:
                st.filled += fill_gaps(out, t, pk, cols, row)
        if after_insert:
            after_insert(row, ok)


def fill_gaps(out: sqlite3.Connection, t: str, pk: list[str], cols: list[str], row: dict[str, Any]) -> int:
    where = " AND ".join(f"{q(c)} = ?" for c in pk)
    cur = out.execute(f"SELECT * FROM {q(t)} WHERE {where}", [row[c] for c in pk])
    cur_cols = [d[0] for d in cur.description]
    ex = cur.fetchone()
    if not ex:
        return 0
    ex = dict(zip(cur_cols, ex))
    upd = {c: row[c] for c in cols if c not in pk and ex.get(c) in (None, "", "{}", "[]") and row.get(c) not in (None, "", "{}", "[]")}
    if not upd:
        return 0
    out.execute(f"UPDATE {q(t)} SET {', '.join(f'{q(c)} = ?' for c in upd)} WHERE {where}", [*upd.values(), *[row[c] for c in pk]])
    return 1


# ---------------------------------------------------------------- notes.sqlite


def map_tags(ctx: Ctx, old: sqlite3.Connection, out: sqlite3.Connection, t: str) -> None:
    st = ctx.stat("notes.sqlite", t)
    if t not in tables(old):
        return
    if t not in tables(out):
        out.execute(tables(old)[t])
    st.new_before = out.execute(f"SELECT count(*) FROM {q(t)}").fetchone()[0]
    cols = [c for c in columns(old, t) if c in columns(out, t)]
    for r in old.execute(f"SELECT * FROM {q(t)}"):
        st.old += 1
        row = dict(r)
        row["user_id"] = ctx.uid(row["user_id"])
        ex = out.execute(f"SELECT id FROM {q(t)} WHERE user_id = ? AND name = ?", (row["user_id"], row["name"])).fetchone()
        if ex:
            ctx.tag_map[t][int(row["id"])] = int(ex[0])
            st.conflict += 1
            continue
        out.execute(f"INSERT INTO {q(t)} ({','.join(map(q, cols))}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
        st.inserted += 1


class Recrypt:
    def __init__(self, ctx: Ctx, new_root: Path):
        self.old_f = self._fernet(ctx.old)
        self.new_f = self._fernet(new_root)
        self.ctx = ctx

    @staticmethod
    def _fernet(root: Path) -> Any:
        key = ""
        env = root / ".env"
        if env.is_file():
            for line in env.read_text(errors="replace").splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "MAIL_SECRET_KEY":
                    key = v.strip().strip('"').strip("'")
        if not key and (root / "mail_secret.key").is_file():
            key = (root / "mail_secret.key").read_text().strip()
        if not key:
            return None
        from cryptography.fernet import Fernet

        return Fernet(key.encode())

    def __call__(self, row: dict[str, Any]) -> Optional[dict[str, Any]]:
        enc = row.get("secret_enc")
        if not enc:
            return row
        who = f"{row.get('owner_user_id')} {row.get('provider') or row.get('email') or ''}".strip()
        if self.old_f is None or self.new_f is None:
            self.ctx.secrets.append(f"{who}: нет ключа {'старого' if self.old_f is None else 'нового'} mail_secret.key — не перенесено, переподключить")
            return None
        try:
            row["secret_enc"] = self.new_f.encrypt(self.old_f.decrypt(enc.encode())).decode()
        except Exception as e:  # noqa: BLE001
            self.ctx.secrets.append(f"{who}: не расшифровалось ({type(e).__name__}) — не перенесено, переподключить")
            return None
        return row


def merge_notes_db(ctx: Ctx, new_root: Path) -> None:
    db = "notes.sqlite"
    if not (ctx.old / db).is_file():
        return
    old, out = ro(ctx.old / db), sqlite3.connect(ctx.out / db)
    out.row_factory = sqlite3.Row
    try:
        merge_orgs(ctx, old, out)
        merge_org_members(ctx, old, out)
        index = _note_index(out)
        merge_local_notes(ctx, old, out, index)
        kb_to_notes(ctx, out, index)
        for t in ("tags", "hashtags"):
            map_tags(ctx, old, out, t)
        recrypt = Recrypt(ctx, new_root)
        done = {"orgs", "org_members", "local_notes", "tags", "hashtags"}

        def ev_ok(row: dict[str, Any], ok: bool) -> None:
            if ok:
                ctx.events_ok.add(int(row["id"]))

        def mail_ok(t: str) -> Callable[[dict[str, Any], bool], None]:
            def f(row: dict[str, Any], ok: bool) -> None:
                if ok:
                    ctx.mail_ok[t].add((str(row["owner_user_id"]), str(row.get("mail_key") or row.get("message_id"))))
            return f

        merged_orgs = {v for k, v in ctx.org_map.items() if k != v}

        def org_as_new(row: dict[str, Any]) -> Optional[dict[str, Any]]:
            # Слитая организация: настройки и домены — как на новом.
            return None if str(row["org_id"]) in merged_orgs else row

        order = ["orbita_events", "mail_messages", "mail_memory"]
        rest = [t for t in tables(old) if t not in done and t not in order and t not in SKIP_TABLES and not SKIP_TABLE_RE.match(t)]
        rest.sort(key=lambda t: t in {"mail_fts", "mail_memory_fts", "orbita_event_attendees", "orbita_event_overrides"})
        for t in order + rest:
            after = None
            transform = None
            if t == "orbita_events":
                after = ev_ok
            elif t == "mail_messages":
                after = mail_ok("mail_fts")
            elif t == "mail_memory":
                after = mail_ok("mail_memory_fts")
            elif t in {"mail_accounts", "tracker_credentials"}:
                transform = recrypt
            elif t in {"org_settings", "org_domains"}:
                transform = org_as_new
            copy_table(ctx, db, old, out, t, after_insert=after, transform=transform)
        if "org_domains" in tables(old):
            for d, oid in old.execute("SELECT domain, org_id FROM org_domains"):
                ex = out.execute("SELECT org_id FROM org_domains WHERE domain = ?", (d,)).fetchone()
                if ex and str(ex[0]) != str(ctx.org(oid)):
                    ctx.orgs.append(f"домен {d}: на новом у организации {ex[0]}, старая запись не перенесена")
        out.commit()
    finally:
        old.close()
        out.close()


# ---------------------------------------------------------------- прочие базы


def merge_credits(ctx: Ctx, db: str, old: sqlite3.Connection, out: sqlite3.Connection) -> None:
    def owner(kind: str, oid: Any) -> str:
        return str(ctx.uid(str(oid))) if kind == "user" else str(ctx.org(str(oid)))

    def ledger(row: dict[str, Any]) -> dict[str, Any]:
        row["owner_id"] = owner(row["kind"], row["owner_id"])
        return row

    # Сначала балансы (нужны движения нового до вставки старого журнала).
    st = ctx.stat(db, "credit_accounts")
    if "credit_accounts" in tables(old):
        st.new_before = out.execute("SELECT count(*) FROM credit_accounts").fetchone()[0]
        for r in old.execute("SELECT * FROM credit_accounts"):
            st.old += 1
            kind, oid, bal = r["kind"], owner(r["kind"], r["owner_id"]), float(r["balance"] or 0)
            ex = out.execute("SELECT balance FROM credit_accounts WHERE kind = ? AND owner_id = ?", (kind, oid)).fetchone()
            if not ex:
                out.execute("INSERT INTO credit_accounts (kind, owner_id, balance, updated_at) VALUES (?, ?, ?, ?)",
                            (kind, oid, bal, r["updated_at"]))
                st.inserted += 1
                ctx.credits.append(f"{kind} {oid}: на новом счёта не было — старый баланс {bal:g}")
                continue
            moved = out.execute(
                "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger WHERE kind = ? AND owner_id = ? AND reason != 'start'",
                (kind, oid)).fetchone()[0]
            final = round(bal + float(moved), 4)
            out.execute("UPDATE credit_accounts SET balance = ? WHERE kind = ? AND owner_id = ?", (final, kind, oid))
            st.conflict += 1
            st.filled += 1
            ctx.credits.append(f"{kind} {oid}: старый {bal:g}, на новом {float(ex[0]):g} (движения без подарка {float(moved):+g}) → {final:g}")
    copy_table(ctx, db, old, out, "credit_ledger", transform=ledger)
    st = ctx.stat(db, "user_day_spend")
    if "user_day_spend" in tables(old):
        st.new_before = out.execute("SELECT count(*) FROM user_day_spend").fetchone()[0]
        for r in old.execute("SELECT * FROM user_day_spend"):
            st.old += 1
            uid = str(ctx.uid(str(r["user_id"])))
            uid_v: Any = int(uid) if isinstance(r["user_id"], int) else uid
            cur = out.execute("UPDATE user_day_spend SET credits = credits + ? WHERE user_id = ? AND day = ?", (r["credits"], uid_v, r["day"]))
            if cur.rowcount:
                st.conflict += 1
                st.filled += 1
            else:
                out.execute("INSERT INTO user_day_spend (user_id, day, credits) VALUES (?, ?, ?)", (uid_v, r["day"], r["credits"]))
                st.inserted += 1
    for t in tables(old):
        if t not in {"credit_accounts", "credit_ledger", "user_day_spend"} and t not in SKIP_TABLES and not SKIP_TABLE_RE.match(t):
            copy_table(ctx, db, old, out, t)


def merge_reminders(ctx: Ctx, db: str, old: sqlite3.Connection, out: sqlite3.Connection) -> None:
    from dateutil.rrule import rrulestr

    tz = ZoneInfo(os.getenv("CALENDAR_TZ", "Europe/Moscow") or "Europe/Moscow")

    def next_after(when: datetime, rrule: str, after: datetime) -> Optional[datetime]:
        line = rrule if rrule.upper().startswith("RRULE:") else "RRULE:" + rrule
        try:
            return rrulestr(line, dtstart=when).after(after, inc=False)
        except Exception:  # noqa: BLE001
            return None

    def fix(row: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(row.get("data") or "{}")
        except ValueError:
            return row
        if "user_id" in data:
            data["user_id"] = ctx.uid(data["user_id"])
        uid = str(data.get("user_id") or row.get("user_id"))
        text = str(data.get("text") or "")[:80]
        if not data.get("done"):
            raw = str(data.get("when_iso") or "").strip()
            when = None
            if raw:
                try:
                    when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=tz)
                if when <= ctx.now:
                    rrule = str(data.get("recurrence") or "").strip()
                    nxt = next_after(when, rrule, ctx.now) if rrule else None
                    if nxt is None:
                        data["done"] = True
                        ctx.reminders_closed.append((uid, raw, text))
                        ctx.people[uid]["rem_closed"] += 1
                    else:
                        data["when_iso"] = nxt.isoformat()
                        data.pop("message_id", None)
                        ctx.reminders_moved.append((uid, raw, nxt.isoformat(), text))
                        ctx.people[uid]["rem_moved"] += 1
        ctx.people[uid]["rem_in"] += 1
        row["data"] = json.dumps(data, ensure_ascii=False)
        return row

    copy_table(ctx, db, old, out, "reminders", transform=fix)


def merge_chat_digest(ctx: Ctx, db: str, old: sqlite3.Connection, out: sqlite3.Connection) -> None:
    for t in tables(old):
        if t not in SKIP_TABLES and not SKIP_TABLE_RE.match(t):
            copy_table(ctx, db, old, out, t)
    # Иначе старые сообщения (вставлены с большими rowid) ушли бы как «срочные».
    if "leo_chat_alert_cursor" in tables(out) and "leo_chat_messages" in tables(out):
        mx = out.execute("SELECT COALESCE(MAX(rowid), 0) FROM leo_chat_messages").fetchone()[0]
        n = out.execute("UPDATE leo_chat_alert_cursor SET last_rowid = ? WHERE last_rowid < ?", (mx, mx)).rowcount
        ctx.notes.append(f"Срочные из чатов: курсор {n} подписок сдвинут на конец (rowid {mx}) — старые сообщения не уйдут уведомлениями.")


def merge_db(ctx: Ctx, db: str) -> None:
    old_p, out_p = ctx.old / db, ctx.out / db
    if not old_p.is_file() or db in SKIP_DBS:
        return
    out_p.parent.mkdir(parents=True, exist_ok=True)
    old, out = ro(old_p), sqlite3.connect(out_p)
    out.row_factory = sqlite3.Row
    try:
        if db == "data/credits.sqlite":
            merge_credits(ctx, db, old, out)
        elif db == "data/reminders.sqlite":
            merge_reminders(ctx, db, old, out)
        elif db == "data/chat_digest.sqlite":
            merge_chat_digest(ctx, db, old, out)
        else:
            for t in tables(old):
                if t not in SKIP_TABLES and not SKIP_TABLE_RE.match(t):
                    copy_table(ctx, db, old, out, t)
        out.commit()
    finally:
        old.close()
        out.close()


def person_counts(ctx: Ctx) -> None:
    """Задачи по людям (для отчёта)."""
    p = ctx.old / "data/calendar_tasks.sqlite"
    if p.is_file():
        c = ro(p)
        if "calendar_tasks" in tables(c):
            for (uid,) in c.execute("SELECT owner_user_id FROM calendar_tasks"):
                ctx.people[str(ctx.uid(uid))]["tasks"] += 1
        c.close()


# ---------------------------------------------------------------- файлы


def _uid_of_name(name: str) -> Optional[str]:
    m = re.match(r"^(-?\d+)(\..*)?$", name)
    return m.group(1) if m else None


def _renamed(ctx: Ctx, name: str) -> str:
    uid = _uid_of_name(name)
    if uid is None:
        return name
    return str(ctx.uid(uid)) + name[len(uid):]


def deep_union(new: Any, old: Any) -> Any:
    if isinstance(new, dict) and isinstance(old, dict):
        out = dict(new)
        for k, v in old.items():
            out[k] = deep_union(new[k], v) if k in new else v
        return out
    if isinstance(new, list) and isinstance(old, list):
        return new + [x for x in old if x not in new]
    return new


def remap_json(ctx: Ctx, obj: Any) -> Any:
    """Ключи вида '<uid>:…' и '<uid>' — по owner_map."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            m = re.match(r"^(\d+)(:.*)?$", str(k))
            nk = str(ctx.uid(m.group(1))) + (m.group(2) or "") if m else k
            out[nk] = remap_json(ctx, v)
        return out
    return obj


def merge_files(ctx: Ctx, new_root: Path) -> None:
    old, out = ctx.old, ctx.out
    # data/users/<uid>.json: новые значения побеждают, недостающее — из старого.
    ou = old / "data/users"
    if ou.is_dir():
        (out / "data/users").mkdir(parents=True, exist_ok=True)
        for f in sorted(ou.iterdir()):
            if not f.is_file():
                continue
            target = out / "data/users" / _renamed(ctx, f.name)
            src_new = new_root / "data/users" / target.name
            if f.suffix == ".json" and src_new.is_file():
                try:
                    o, n = json.loads(f.read_text()), json.loads(src_new.read_text())
                except ValueError:
                    continue
                if isinstance(o, dict) and "current_org_id" in o:
                    o["current_org_id"] = ctx.org(o["current_org_id"])
                target.write_text(json.dumps(deep_union(n, o), ensure_ascii=False, indent=2))
            elif not src_new.exists():
                if f.suffix == ".json":
                    try:
                        o = json.loads(f.read_text())
                        if isinstance(o, dict) and "current_org_id" in o:
                            o["current_org_id"] = ctx.org(o["current_org_id"])
                        target.write_text(json.dumps(o, ensure_ascii=False, indent=2))
                        continue
                    except ValueError:
                        pass
                shutil.copy2(f, target)
    # Токены и файлы на пользователя: только если на новом нет.
    for d in USER_TOKEN_DIRS + USER_FILE_DIRS:
        src = old / d
        if not src.is_dir():
            continue
        for f in sorted(src.iterdir()):
            if not f.is_file():
                continue
            name = _renamed(ctx, f.name)
            uid = _uid_of_name(name) or name
            if (new_root / d / name).exists():
                if d in USER_TOKEN_DIRS:
                    ctx.tokens.append((uid, d, "на новом уже подключено"))
                continue
            (out / d).mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out / d / name)
            if d in USER_TOKEN_DIRS:
                ctx.tokens.append((uid, d, "перенесён старый токен — лучше переподключить (сервер атаковали)"))
    for d in USER_TREE_DIRS:
        src = old / d
        if not src.is_dir():
            continue
        for sub in sorted(src.iterdir()):
            name = _renamed(ctx, sub.name)
            if sub.is_dir() and not (new_root / d / name).exists():
                shutil.copytree(sub, out / d / name)
    # Файлы вложений комментариев: id < 10M, не пересекаются.
    for d in ("comment_files", "data/comment_files"):
        src = old / d
        if src.is_dir():
            for f in sorted(src.rglob("*")):
                rel = f.relative_to(src)
                if f.is_file() and not (new_root / d / rel).exists():
                    (out / d / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out / d / rel)
    # JSON-состояние: объединение, новое побеждает.
    for rel in UNION_JSON:
        o_p, n_p = old / rel, new_root / rel
        if not o_p.is_file():
            continue
        try:
            o = json.loads(o_p.read_text())
            n = json.loads(n_p.read_text()) if n_p.is_file() else {}
        except ValueError as e:
            ctx.notes.append(f"{rel}: не JSON ({e}) — не объединён")
            continue
        if rel in UID_PREFIX_JSON:
            o = remap_json(ctx, o)
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(json.dumps(deep_union(n, o), ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- запуск


def snapshot_new(new_root: Path, out: Path) -> None:
    """Копия баз нового (sqlite backup API — годится и для живых баз)."""
    for rel in db_files(new_root):
        if rel in SKIP_DBS:
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(f"file:{new_root / rel}?mode=ro", uri=True)
        d = sqlite3.connect(dst)
        src.backup(d)
        d.close()
        src.close()


def verify(ctx: Ctx) -> list[str]:
    bad = []
    for key, st in ctx.stats.items():
        db, t = key.split(":", 1)
        p = ctx.out / db
        if not p.is_file() or "→" in t:
            continue
        c = sqlite3.connect(p)
        try:
            st.after = c.execute(f"SELECT count(*) FROM {q(t)}").fetchone()[0]
        except sqlite3.Error:
            continue
        finally:
            c.close()
        if st.after != st.new_before + st.inserted and t != "local_notes":
            bad.append(f"{key}: было {st.new_before} + вставлено {st.inserted} ≠ стало {st.after}")
    kb = ctx.stats.get("data/knowledge_bases.sqlite:kb_documents→local_notes", TableStat())
    ln = ctx.stats.get("notes.sqlite:local_notes")
    if ln and ln.after != ln.new_before + ln.inserted + kb.inserted:
        bad.append(f"local_notes: {ln.new_before} + {ln.inserted} + БЗ {kb.inserted} ≠ {ln.after}")
    for rel in db_files(ctx.out):
        c = sqlite3.connect(ctx.out / rel)
        ok = c.execute("PRAGMA quick_check").fetchone()[0]
        c.close()
        if ok != "ok":
            bad.append(f"{rel}: quick_check {ok}")
    return bad


def names(ctx: Ctx) -> dict[str, str]:
    out: dict[str, str] = {}
    for root in (ctx.out, ctx.old):
        p = root / "notes.sqlite"
        if not p.is_file():
            continue
        c = ro(p)
        if "user_profiles" in tables(c):
            for r in c.execute("SELECT user_id, username, first_name, last_name FROM user_profiles"):
                uid = str(ctx.uid(str(r["user_id"])))
                nm = " ".join(x for x in (r["first_name"], r["last_name"]) if x) or ""
                if r["username"]:
                    nm = f"{nm} @{r['username']}".strip()
                if nm and uid not in out:
                    out[uid] = nm
        c.close()
    return out


def report(ctx: Ctx, problems: list[str]) -> str:
    nm = names(ctx)

    def who(uid: str) -> str:
        return f"{nm.get(uid, 'без имени')} ({uid})"

    L = [f"# Слияние Beget → новый прод: отчёт ({now_iso(ctx.now)})", ""]
    L.append("## Проверка")
    L += [f"- ❌ {p}" for p in problems] or ["- ✅ quick_check всех баз ok; по каждой таблице «было + вставлено = стало»"]
    L += ["", "## Сопоставление людей (Telegram → аккаунт по почте)"]
    # Псевдонимы почтовых id (S → T после склейки) в старых данных не встречаются — показываем только T → S.
    L += [f"- {who(k)} → {who(v)}" for k, v in sorted(ctx.owner_map.items()) if int(k) < 5 * 10**15] or ["- нет"]
    L += ["", "## По людям", "", "| Человек | Документы: было / легло / дубль (не перенесён) / похожий «(до 01.11)» | БЗ → «Документы» | Задачи | Напоминания: легло / закрыто / перенесено |", "|---|---|---|---|---|"]
    for uid, p in sorted(ctx.people.items(), key=lambda kv: -kv[1].get("docs_old", 0)):
        L.append(f"| {who(uid)} | {p['docs_old']} / {p['docs_in']} / {p['docs_dup']} / {p['docs_similar']} | {p['kb_in']} | {p['tasks']} | {p['rem_in']} / {p['rem_closed']} / {p['rem_moved']} |")
    L += ["", "## Старая база знаний → личные «Документы» авторов"]
    if ctx.kb_returned:
        by = defaultdict(list)
        for uid, base, title in ctx.kb_returned:
            by[uid].append((base, title))
        L.append(f"Всего вернулось документов: **{len(ctx.kb_returned)}**, людей: {len(by)}.")
        for uid, items in sorted(by.items()):
            bases = defaultdict(int)
            for b, _ in items:
                bases[b] += 1
            L.append(f"- {who(uid)}: {len(items)} — " + "; ".join(f"«{b}» ({n})" for b, n in bases.items()))
    else:
        L.append("- в старых данных документов БЗ нет")
    L += ["", "## Дубли"]
    L += [f"- уже есть на новом: {who(u)} старый {o if o > 0 else 'из БЗ'} → новый {n} «{t}»" for u, o, n, t in ctx.dup_exact] or ["- полных дублей нет"]
    L += [f"- возможный дубль: {who(u)} старый {o if o > 0 else 'из БЗ'} ↔ новый {n} «{t}» — решить, удалять ли" for u, o, n, t in ctx.dup_similar]
    L += ["", "## Организации"] + ([f"- {x}" for x in ctx.orgs] or ["- в старых данных организаций нет"])
    L += ["", "## Кредиты"] + ([f"- {x}" for x in ctx.credits] or ["- нет счетов"])
    L += ["", f"## Напоминания за простой ({CUTOFF_LABEL} → сейчас): не отправлялись"]
    L += [f"- закрыто разовое: {who(u)} {w} «{t}»" for u, w, t in ctx.reminders_closed]
    L += [f"- повторяющееся перенесено: {who(u)} {w} → {n} «{t}»" for u, w, n, t in ctx.reminders_moved]
    if not ctx.reminders_closed and not ctx.reminders_moved:
        L.append("- пропущенных нет")
    L += ["", "## Подключения (токены)"] + ([f"- {who(u)}: {d} — {s}" for u, d, s in ctx.tokens] or ["- нет"])
    if ctx.secrets:
        L += ["", "## Пароли почты / трекеров"] + [f"- {x}" for x in ctx.secrets]
    if ctx.notes:
        L += ["", "## Заметки"] + [f"- {x}" for x in ctx.notes]
    L += ["", "## Таблицы", "", "| База:таблица | на новом | старых | вставлено | пропущено | конфликт (новое победило) | дополнено | стало |", "|---|---|---|---|---|---|---|---|"]
    for k, s in sorted(ctx.stats.items()):
        if s.old or s.inserted:
            L.append(f"| {k} | {s.new_before} | {s.old} | {s.inserted} | {s.skipped} | {s.conflict} | {s.filled} | {s.after} |")
    return "\n".join(L) + "\n"


def run(old: Path, new_root: Path, out: Path, now: datetime) -> tuple[Ctx, str, list[str]]:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} не пустой")
    out.mkdir(parents=True, exist_ok=True)
    snapshot_new(new_root, out)
    ctx = Ctx(old=old, out=out, now=now)
    if (out / "notes.sqlite").is_file():
        c = sqlite3.connect(out / "notes.sqlite")
        ctx.owner_map = build_owner_map(c)
        c.close()
    merge_notes_db(ctx, new_root)
    for db in db_files(old):
        if db != "notes.sqlite":
            if not (out / db).exists() and db not in SKIP_DBS:
                (out / db).parent.mkdir(parents=True, exist_ok=True)
            merge_db(ctx, db)
    person_counts(ctx)
    merge_files(ctx, new_root)
    problems = verify(ctx)
    return ctx, report(ctx, problems), problems


def unpack(rescue: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    state = rescue / "state.tar.gz"
    if state.is_file():
        with tarfile.open(state) as tar:
            try:
                tar.extractall(target, filter="data")
            except TypeError:
                tar.extractall(target)
    n = 0
    for gz in sorted(rescue.glob("*.sqlite.gz")):
        rel = gz.name[: -len(".gz")].replace("__", "/")
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(gz, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        n += 1
    print(f"распаковано баз: {n}, state: {'да' if state.is_file() else 'НЕТ'} → {target}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unpack")
    u.add_argument("rescue")
    u.add_argument("target")
    for name in ("plan", "apply"):
        s = sub.add_parser(name)
        s.add_argument("--old", required=True)
        s.add_argument("--new", required=True)
        if name == "apply":
            s.add_argument("--out", required=True)
        s.add_argument("--report")
        s.add_argument("--now", help="ISO-время «сейчас» (для проверки); по умолчанию текущее")
    a = ap.parse_args(argv)
    if a.cmd == "unpack":
        unpack(Path(a.rescue), Path(a.target))
        return 0
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    old, new_root = Path(a.old).resolve(), Path(a.new).resolve()
    if a.cmd == "plan":
        with tempfile.TemporaryDirectory(prefix="merge-plan-") as tmp:
            ctx, text, problems = run(old, new_root, Path(tmp) / "out", now)
    else:
        ctx, text, problems = run(old, new_root, Path(a.out).resolve(), now)
    if a.report:
        Path(a.report).write_text(text, encoding="utf-8")
        print(f"отчёт: {a.report}")
    else:
        print(text)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
