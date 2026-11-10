"""scripts/merge_beget.py: слияние старых данных Beget с новым продом (docs/beget-merge-plan.md)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("merge_beget", ROOT / "scripts" / "merge_beget.py")
mb = importlib.util.module_from_spec(_spec)
sys.modules["merge_beget"] = mb
_spec.loader.exec_module(mb)  # type: ignore[union-attr]

S = "5000000000000850"  # вошёл по почте на новом, привязал Telegram 111
T = "111"
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)

NOTES_SCHEMA = """
CREATE TABLE local_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '', todoist_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '', kb_enabled INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
  meta_json TEXT NOT NULL DEFAULT '{}', primary_sheet_title TEXT NOT NULL DEFAULT '');
CREATE TABLE note_sheets (id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'doc');
CREATE TABLE share_links (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, item_kind TEXT NOT NULL, item_id TEXT NOT NULL,
  created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE note_members (owner_user_id TEXT NOT NULL, note_id INTEGER NOT NULL, member_user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'editor', added_at TEXT NOT NULL, PRIMARY KEY (owner_user_id, note_id, member_user_id));
CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
  photo_url TEXT, updated_at TEXT);
CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id, name));
CREATE TABLE tag_links (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, tag_id INTEGER NOT NULL,
  item_kind TEXT NOT NULL, item_id TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id, tag_id, item_kind, item_id));
CREATE TABLE orgs (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, name TEXT NOT NULL, logo_url TEXT,
  join_mode TEXT, plan TEXT, seats INTEGER, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE org_members (id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL, user_id TEXT,
  invited_key TEXT, role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', plan TEXT, added_by TEXT,
  added_at TEXT NOT NULL, UNIQUE(org_id, user_id), UNIQUE(org_id, invited_key));
CREATE TABLE org_domains (domain TEXT PRIMARY KEY, org_id INTEGER NOT NULL, verified_by TEXT, created_at TEXT);
CREATE TABLE org_settings (org_id INTEGER PRIMARY KEY, json TEXT NOT NULL, updated_at TEXT);
CREATE TABLE tracker_credentials (owner_user_id TEXT NOT NULL, provider TEXT NOT NULL, kind TEXT, secret_enc TEXT,
  portal TEXT, account_id TEXT, account_name TEXT, saved_at TEXT, PRIMARY KEY (owner_user_id, provider));
CREATE TABLE orbita_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ical_uid TEXT UNIQUE, owner_user_id TEXT, title TEXT);
CREATE TABLE orbita_event_attendees (event_id INTEGER NOT NULL, email TEXT NOT NULL, user_id TEXT, token TEXT UNIQUE,
  PRIMARY KEY (event_id, email));
"""
NEW_ONLY = """
CREATE TABLE accounts (user_id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, email_verified_at INTEGER,
  tg_user_id TEXT UNIQUE, created_at INTEGER NOT NULL);
CREATE TABLE account_aliases (old_user_id TEXT PRIMARY KEY, new_user_id TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE org_kb_nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL, parent_id INTEGER,
  kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', note_id INTEGER, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
"""
TASKS = """CREATE TABLE calendar_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id TEXT NOT NULL, title TEXT,
  assignee_user_id TEXT, note_id INTEGER, done INTEGER DEFAULT 0);"""
CREDITS = """
CREATE TABLE credit_accounts (kind TEXT NOT NULL, owner_id TEXT NOT NULL, balance REAL NOT NULL, updated_at TEXT,
  PRIMARY KEY (kind, owner_id));
CREATE TABLE credit_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, kind TEXT, owner_id TEXT,
  user_id TEXT, delta REAL, reason TEXT, meta TEXT);
CREATE TABLE user_day_spend (user_id TEXT NOT NULL, day TEXT NOT NULL, credits REAL NOT NULL, PRIMARY KEY (user_id, day));
"""
REMINDERS = "CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id INTEGER, seq INTEGER, data TEXT NOT NULL);"
CHATS = """
CREATE TABLE leo_chat_messages (chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, user_id INTEGER,
  text TEXT NOT NULL DEFAULT '', ts_utc TEXT NOT NULL, date_utc TEXT NOT NULL, PRIMARY KEY (chat_id, message_id));
CREATE TABLE leo_chat_alert_cursor (user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, last_rowid INTEGER NOT NULL,
  PRIMARY KEY (user_id, chat_id));
"""
KB = """
CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY, owner_telegram_user_id INTEGER NOT NULL, title TEXT NOT NULL,
  source_type TEXT, source_url TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE kb_documents (doc_id TEXT NOT NULL, kb_id TEXT NOT NULL, title TEXT, content_hash TEXT, modified_at TEXT,
  url TEXT, PRIMARY KEY (kb_id, doc_id));
CREATE TABLE kb_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, kb_id TEXT, doc_id TEXT, heading TEXT, text TEXT,
  chunk_index INTEGER);
CREATE TABLE kb_members (kb_id TEXT, telegram_user_id INTEGER, role TEXT, PRIMARY KEY (kb_id, telegram_user_id));
"""
TS = "2026-10-20T10:00:00+00:00"


def mkdb(path: Path, schema: str, rows: dict[str, list[tuple]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(schema)
    for t, rs in (rows or {}).items():
        for r in rs:
            c.execute(f"INSERT INTO {t} VALUES ({','.join('?' * len(r))})", r)
    c.commit()
    c.close()


def note(i, uid, title, body):
    return (i, uid, title, body, None, TS, TS, "", 1, 1, "{}", "")


@pytest.fixture()
def trees(tmp_path: Path):
    from cryptography.fernet import Fernet

    old, new = tmp_path / "old", tmp_path / "new"
    k_old, k_new = Fernet.generate_key(), Fernet.generate_key()
    mkdb(old / "notes.sqlite", NOTES_SCHEMA, {
        "local_notes": [
            note(1, T, "Планы", "<p>альфа строка</p><p>бета строка</p>"),  # полный дубль нового 10000001
            note(2, T, "Отчёт старый", "q1 строка\nw2 строка"),
            note(3, T, "Планы", "совсем другой текст"),  # тот же заголовок → «(до 01.11)»
            note(4, "333", "Личное", "своё"),
        ],
        "note_sheets": [(1, 1, "Лист", "лист дубля", 1, 1, TS, TS, "doc"), (2, 2, "Лист", "лист отчёта", 1, 1, TS, TS, "doc")],
        "share_links": [("t1", T, "local", "1", TS, None), ("t2", T, "local", "2", TS, None)],
        "note_members": [(T, 2, "333", "editor", TS)],
        "user_profiles": [(111, "vova", "Вова", "П", "http://photo", TS), (333, "kate", "Катя", "", None, TS)],
        "tags": [(7, T, "work", TS, TS), (8, T, "old-only", TS, TS)],
        "tag_links": [(1, T, 7, "local", "2", TS), (2, T, 8, "local", "2", TS)],
        "orgs": [(3, "acme", "Acme", None, None, None, None, T, TS), (4, "beta", "Beta", None, None, None, None, "333", TS)],
        "org_members": [(1, 3, T, None, "admin", "active", None, None, TS), (2, 3, "333", None, "member", "active", None, T, TS),
                        (3, 4, "333", None, "owner", "active", None, None, TS)],
        "org_domains": [("acme.ru", 3, T, TS)],
        "org_settings": [(3, '{"old": true}', TS)],
        "tracker_credentials": [(T, "bitrix", "webhook", Fernet(k_old).encrypt(b"secret-hook").decode(), "p", "a", "n", TS)],
        "orbita_events": [(5, "uid-same", T, "Старая встреча"), (6, "uid-old", T, "Ещё")],
        "orbita_event_attendees": [(5, "a@x.ru", None, "tok5"), (6, "b@x.ru", None, "tok6")],
    })
    (old / "mail_secret.key").write_text(k_old.decode())
    mkdb(old / "data/calendar_tasks.sqlite", TASKS, {"calendar_tasks": [(5, T, "Задача по планам", "333", 1, 0)]})
    mkdb(old / "data/credits.sqlite", CREDITS, {
        "credit_accounts": [("user", T, 300.0, TS), ("user", "333", 50.0, TS)],
        "credit_ledger": [(1, TS, "2026-10-20", "user", T, T, -20.0, "spend", "{}")],
        "user_day_spend": [(T, "2026-11-06", 5.0)],
    })
    mkdb(old / "data/reminders.sqlite", REMINDERS, {"reminders": [
        ("r1", 111, 1, json.dumps({"id": "r1", "user_id": 111, "chat_id": 111, "text": "разовое", "when_iso": "2026-11-03T10:00:00+03:00"})),
        ("r2", 111, 2, json.dumps({"id": "r2", "user_id": 111, "chat_id": 111, "text": "каждый день",
                                   "when_iso": "2026-11-02T09:00:00+03:00", "recurrence": "RRULE:FREQ=DAILY", "message_id": 77})),
        ("r3", 111, 3, json.dumps({"id": "r3", "user_id": 111, "chat_id": 111, "text": "будущее", "when_iso": "2026-12-01T09:00:00+03:00"})),
    ]})
    mkdb(old / "data/chat_digest.sqlite", CHATS, {
        "leo_chat_messages": [(-100, 1, 111, "старое", TS, "2026-10-20"), (-100, 2, 222, "ещё", TS, "2026-10-20")],
        "leo_chat_alert_cursor": [(111, -100, 2)],
    })
    mkdb(old / "data/knowledge_bases.sqlite", KB, {
        "knowledge_bases": [("kb1", 111, "Регламенты", "google_docs", "https://docs/x", TS, TS)],
        "kb_documents": [("d1", "kb1", "Отпуск", "h", TS, "https://docs/d1"), ("d2", "kb1", "", "h", TS, None)],
        "kb_chunks": [(1, "kb1", "d1", "Как взять", "Пишете заявление.", 0), (2, "kb1", "d1", "Как взять", "Ждёте ответа.", 1),
                      (3, "kb1", "d1", "Сроки", "За две недели.", 2), (4, "kb1", "d2", "", "Без названия текст", 0)],
        "kb_members": [("kb1", 333, "reader")],
    })
    (old / "data/users").mkdir(parents=True)
    (old / "data/users/111.json").write_text(json.dumps({"timezone": "Asia/Almaty", "onboarding": {"done": True}, "current_org_id": 3}))
    (old / "google_user_tokens").mkdir()
    (old / "google_user_tokens/111.json").write_text('{"refresh_token": "x"}')
    (old / "data/meeting_invites_notified.json").write_text(json.dumps({"sent": {"111:b": 2.0}}))

    mkdb(new / "notes.sqlite", NOTES_SCHEMA + NEW_ONLY, {
        "accounts": [(S, "v@x.ru", 1, T, 1), ("222", "k@x.ru", 1, "222", 1)],
        "local_notes": [note(10000001, S, "Планы", "<p>альфа строка</p>\n<p>бета строка</p>"), note(10000002, "222", "Отчёт", "x")],
        "note_sheets": [(10000001, 10000001, "Лист", "лист дубля", 1, 1, TS, TS, "doc")],
        "user_profiles": [(int(S), "vova", "", None, None, TS)],
        "tags": [(10000001, S, "work", TS, TS)],
        "orgs": [(10000001, "acme", "Acme", None, None, None, None, S, TS)],
        "org_members": [(10000001, 10000001, S, None, "owner", "active", None, None, TS)],
        "org_domains": [("acme.ru", 10000001, S, TS)],
        "org_settings": [(10000001, '{"new": true}', TS)],
        "orbita_events": [(10000001, "uid-same", S, "Новая")],
    })
    (new / "mail_secret.key").write_text(k_new.decode())
    mkdb(new / "data/calendar_tasks.sqlite", TASKS)
    mkdb(new / "data/credits.sqlite", CREDITS, {
        "credit_accounts": [("user", S, 950.0, TS)],
        "credit_ledger": [(10000001, TS, "2026-11-05", "user", S, None, 1000.0, "start", "{}"),
                          (10000002, TS, "2026-11-06", "user", S, S, -50.0, "spend", "{}")],
        "user_day_spend": [(S, "2026-11-06", 2.0)],
    })
    mkdb(new / "data/reminders.sqlite", REMINDERS)
    mkdb(new / "data/chat_digest.sqlite", CHATS, {"leo_chat_messages": [(-100, 50, 111, "новое", TS, "2026-11-06")]})
    (new / "data/users").mkdir(parents=True)
    (new / f"data/users/{S}.json").write_text(json.dumps({"timezone": "Europe/Moscow"}))
    (new / "data/meeting_invites_notified.json").write_text(json.dumps({"sent": {f"{S}:a": 1.0}}))
    return old, new, k_new


@pytest.fixture()
def merged(trees, tmp_path):
    old, new, k_new = trees
    out = tmp_path / "out"
    ctx, text, problems = mb.run(old, new, out, NOW)
    return ctx, text, problems, out, new, k_new


def rows(db: Path, sql: str, *a):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def test_report_clean_and_new_untouched(merged):
    ctx, text, problems, out, new, _ = merged
    assert problems == []
    assert ctx.owner_map == {T: S}
    # Живые базы нового не тронуты.
    assert rows(new / "notes.sqlite", "SELECT count(*) FROM local_notes") == [(2,)]
    assert "Старая база знаний" in text and "Регламенты" in text


def test_documents_duplicates_and_owner_map(merged):
    ctx, _, _, out, _, _ = merged
    db = out / "notes.sqlite"
    got = {r[0]: (r[1], r[2]) for r in rows(db, "SELECT id, user_id, title FROM local_notes")}
    assert 1 not in got  # полный дубль не перенесён
    assert got[2] == (S, "Отчёт старый")
    assert got[3] == (S, "Планы (до 01.11)")
    assert got[4] == ("333", "Личное")
    assert ctx.note_map == {1: 10000001}
    # Лист дубля не задвоился, лист перенесённого — на месте.
    assert rows(db, "SELECT note_id FROM note_sheets ORDER BY id") == [(2,), (10000001,)]
    # Ссылка на дубль ведёт на новый документ.
    assert dict(rows(db, "SELECT token, item_id FROM share_links")) == {"t1": "10000001", "t2": "2"}
    assert rows(db, "SELECT owner_user_id, member_user_id FROM note_members") == [(S, "333")]
    task = rows(out / "data/calendar_tasks.sqlite", "SELECT owner_user_id, note_id, assignee_user_id FROM calendar_tasks")
    assert task == [(S, 10000001, "333")]


def test_old_kb_becomes_personal_docs(merged):
    ctx, text, _, out, _, _ = merged
    r = rows(out / "notes.sqlite", "SELECT user_id, title, body, meta_json FROM local_notes WHERE meta_json LIKE '%old_kb%' ORDER BY title")
    assert [(x[0], x[1]) for x in r] == [(S, "Отпуск"), (S, "Регламенты")]
    body = r[0][2]
    assert body.count("## Как взять") == 1 and "## Сроки" in body and body.index("Пишете") < body.index("Ждёте")
    meta = json.loads(r[0][3])
    assert meta == {"imported_from": "old_kb", "kb_title": "Регламенты", "source_url": "https://docs/d1", "source_type": "google_docs"}
    assert not (out / "data/knowledge_bases.sqlite").exists()  # таблицами старая БЗ не переносится
    assert "Всего вернулось документов: **2**" in text


def test_tags_orgs_settings(merged):
    ctx, _, _, out, _, _ = merged
    db = out / "notes.sqlite"
    assert rows(db, "SELECT tag_id FROM tag_links ORDER BY id") == [(10000001,), (8,)]
    members = {(r[0], r[1]): r[2] for r in rows(db, "SELECT org_id, user_id, role FROM org_members")}
    assert members[(10000001, S)] == "owner"  # admin старого не понижает owner
    assert members[(10000001, "333")] == "member"  # участник старой слитой организации
    assert members[(4, "333")] == "owner"  # организация без пары перенесена целиком
    assert rows(db, "SELECT json FROM org_settings WHERE org_id = 10000001") == [('{"new": true}',)]
    assert rows(db, "SELECT count(*) FROM orgs") == [(2,)]
    # Профиль: новое побеждает, пустое дополнено.
    assert rows(db, "SELECT username, first_name, last_name FROM user_profiles WHERE user_id = ?", int(S)) == [("vova", "Вова", "П")]


def test_events_by_ical_uid(merged):
    _, _, _, out, _, _ = merged
    db = out / "notes.sqlite"
    assert rows(db, "SELECT id FROM orbita_events ORDER BY id") == [(6,), (10000001,)]
    assert rows(db, "SELECT event_id FROM orbita_event_attendees") == [(6,)]


def test_secret_reencrypted(merged):
    from cryptography.fernet import Fernet

    _, _, _, out, _, k_new = merged
    (enc,) = rows(out / "notes.sqlite", "SELECT secret_enc FROM tracker_credentials WHERE owner_user_id = ?", S)[0]
    assert Fernet(k_new).decrypt(enc.encode()) == b"secret-hook"


def test_credits(merged):
    _, _, _, out, _, _ = merged
    db = out / "data/credits.sqlite"
    bal = dict(rows(db, "SELECT owner_id, balance FROM credit_accounts"))
    assert bal[S] == 250.0  # старые 300 − 50 потрачено на новом, подарок не повторяется
    assert bal["333"] == 50.0
    assert rows(db, "SELECT credits FROM user_day_spend WHERE user_id = ? AND day = '2026-11-06'", S) == [(7.0,)]
    assert rows(db, "SELECT owner_id, user_id FROM credit_ledger WHERE id = 1") == [(S, S)]


def test_reminders_no_spam(merged):
    ctx, _, _, out, _, _ = merged
    data = {r[0]: json.loads(r[1]) for r in rows(out / "data/reminders.sqlite", "SELECT id, data FROM reminders")}
    assert data["r1"]["done"] is True and data["r1"]["user_id"] == int(S)
    nxt = datetime.fromisoformat(data["r2"]["when_iso"])
    assert nxt > NOW and (nxt - NOW).total_seconds() < 86400 and "message_id" not in data["r2"]
    assert not data["r3"].get("done") and data["r3"]["when_iso"].startswith("2026-12-01")
    assert rows(out / "data/reminders.sqlite", "SELECT DISTINCT user_id FROM reminders") == [(int(S),)]


def test_chat_cursor_moved_to_end(merged):
    _, _, _, out, _, _ = merged
    db = out / "data/chat_digest.sqlite"
    mx = rows(db, "SELECT max(rowid) FROM leo_chat_messages")[0][0]
    assert rows(db, "SELECT last_rowid FROM leo_chat_alert_cursor") == [(mx,)]
    assert rows(db, "SELECT user_id FROM leo_chat_messages WHERE message_id = 1") == [(111,)]  # Telegram-данные как есть


def test_files(merged):
    ctx, _, _, out, _, _ = merged
    u = json.loads((out / f"data/users/{S}.json").read_text())
    assert u == {"timezone": "Europe/Moscow", "onboarding": {"done": True}, "current_org_id": 10000001}
    assert (out / f"google_user_tokens/{S}.json").is_file()
    assert json.loads((out / "data/meeting_invites_notified.json").read_text()) == {"sent": {f"{S}:a": 1.0, f"{S}:b": 2.0}}


def test_plan_cli_writes_report_only(trees, tmp_path):
    old, new, _ = trees
    rep = tmp_path / "r.md"
    assert mb.main(["plan", "--old", str(old), "--new", str(new), "--report", str(rep), "--now", NOW.isoformat()]) == 0
    assert "## По людям" in rep.read_text()
    assert rows(new / "notes.sqlite", "SELECT count(*) FROM local_notes") == [(2,)]


def test_apply_refuses_non_empty_out(trees, tmp_path):
    old, new, _ = trees
    out = tmp_path / "busy"
    out.mkdir()
    (out / "x").write_text("1")
    with pytest.raises(SystemExit):
        mb.run(old, new, out, NOW)
