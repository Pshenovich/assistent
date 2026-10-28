"""Напоминания в SQLite: перенос из reminders.json, атомарные правки из двух процессов."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sqlite3

import pytest

from assistant.stores import reminders_store as rs

LEGACY = [
    {"id": "a1", "user_id": 7, "chat_id": 7, "task": "Позвонить", "when_iso": "2026-10-29T09:00:00+03:00",
     "done": False, "checklist": [{"id": "c1", "text": "x", "done": False}], "extra_field": {"keep": 1}},
    {"id": "b2", "user_id": 8, "chat_id": 8, "task": "Отчёт", "when_iso": "2026-10-30T10:00:00+03:00",
     "done": True, "recurrence": "RRULE:FREQ=DAILY", "reminder_message_id": 55},
    {"user_id": 7, "task": "без id"},
]


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    path = tmp_path / "reminders.json"
    path.write_text(json.dumps(LEGACY, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("REMINDERS_STORE_PATH", str(path))
    monkeypatch.delenv("REMINDERS_DB_PATH", raising=False)
    return path


def test_import_keeps_every_record_and_field(legacy):
    assert rs.load_reminders() == LEGACY
    assert rs.db_path() == legacy.with_suffix(".sqlite")
    # JSON не тронут
    assert json.loads(legacy.read_text(encoding="utf-8")) == LEGACY
    assert [r.get("id") for r in rs.reminders_for_user(7)] == ["a1", None]
    assert rs.get_reminder_by_id("b2")["reminder_message_id"] == 55
    assert rs.find_reminder_by_message(8, 55)["id"] == "b2"


def test_import_runs_once(legacy):
    rs.load_reminders()
    rs.remove_reminder("a1")
    rs.remove_reminder("b2")
    # Повторное открытие (рестарт) не возвращает удалённое из старого JSON
    rs._CONN = None
    ids = [r.get("id") for r in rs.load_reminders()]
    assert "a1" not in ids and "b2" not in ids


def test_mutations(legacy):
    rid = rs.add_reminder(user_id=7, chat_id=7, task=" Новое ", when_iso="2026-11-01T09:00:00+03:00",
                          recurrence="FREQ=WEEKLY")
    assert rs.load_reminders()[-1]["id"] == rid  # порядок — по добавлению
    assert rs.get_reminder_by_id(rid)["task"] == "Новое"
    rs.set_reminder_message_id(rid, 99)
    assert rs.find_reminder_by_message(7, 99)["id"] == rid
    rs.clear_reminder_message_id(rid)
    assert "reminder_message_id" not in rs.get_reminder_by_id(rid)
    assert rs.update_reminder_when(rid, "2026-11-02T09:00:00+03:00")["when_iso"] == "2026-11-02T09:00:00+03:00"
    row = rs.patch_reminder(rid, done=True, recurrence="")
    assert row["done"] is True and "recurrence" not in row
    assert rs.patch_reminder("nope", task="x") is None
    rs.remove_reminder(rid)
    assert rs.get_reminder_by_id(rid) is None
    # extra-поля старых записей переживают правку
    rs.patch_reminder("a1", task="Позвонить маме")
    assert rs.get_reminder_by_id("a1")["extra_field"] == {"keep": 1}


def test_default_path_is_data_dir(monkeypatch):
    from assistant.config import ROOT

    monkeypatch.setenv("REMINDERS_STORE_PATH", "./reminders.json")
    monkeypatch.delenv("REMINDERS_DB_PATH", raising=False)
    assert rs.db_path() == ROOT / "data" / "reminders.sqlite"
    monkeypatch.delenv("REMINDERS_STORE_PATH")
    assert rs.db_path() == ROOT / "data" / "reminders.sqlite"


def test_broken_legacy_json_does_not_break_store(monkeypatch, tmp_path):
    path = tmp_path / "reminders.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("REMINDERS_STORE_PATH", str(path))
    rid = rs.add_reminder(user_id=1, chat_id=1, task="t", when_iso="2026-11-01T09:00:00+03:00")
    assert rs.get_reminder_by_id(rid)["task"] == "t"


def _patcher(store_path: str, field: str, n: int) -> None:
    os.environ["REMINDERS_STORE_PATH"] = store_path
    from assistant.stores import reminders_store

    for i in range(n):
        def fn(it, i=i):
            it[field] = i
            it["count"] = int(it.get("count") or 0) + 1

        reminders_store._update("a1", fn)


def test_two_processes_do_not_lose_updates(legacy):
    rs.load_reminders()  # перенос до старта процессов
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_patcher, args=(str(legacy), f, 100)) for f in ("bot_field", "web_field")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0, 0]
    row = rs.get_reminder_by_id("a1")
    assert row["count"] == 200
    assert row["bot_field"] == 99 and row["web_field"] == 99


def test_export_script_roundtrip(legacy, tmp_path):
    import subprocess
    import sys

    out = tmp_path / "export.json"
    rs.add_reminder(user_id=9, chat_id=9, task="после переезда", when_iso="2026-11-01T09:00:00+03:00")
    env = dict(os.environ, REMINDERS_STORE_PATH=str(legacy))
    subprocess.run([sys.executable, "scripts/reminders_export_json.py", str(out)], check=True, env=env)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[:3] == LEGACY and data[3]["task"] == "после переезда"


def test_miniapp_api_roundtrip(legacy, monkeypatch):
    """Окно напоминаний Орбиты: создать, увидеть, поправить, удалить — через HTTP."""
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode

    from fastapi.testclient import TestClient

    import usage_server
    from assistant.bot import access_gate

    token = "123456:REMINDERS-SQLITE"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 7, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    h = {"Authorization": "tma " + urlencode(fields)}
    c = TestClient(usage_server.app)

    r = c.get("/api/miniapp/reminders", headers=h)
    assert r.status_code == 200, r.text
    assert "a1" in json.dumps(r.json(), ensure_ascii=False)  # перенесённое из JSON видно

    r = c.post("/api/miniapp/reminders", headers=h,
               json={"task": "Из Орбиты", "when_iso": "2030-11-01T09:00:00+03:00"})
    assert r.status_code == 200, r.text
    rid = next(x["id"] for x in rs.reminders_for_user(7) if x.get("task") == "Из Орбиты")

    r = c.patch(f"/api/miniapp/reminders/{rid}", headers=h, json={"task": "Из Орбиты 2", "recurrence": "FREQ=DAILY"})
    assert r.status_code == 200, r.text
    assert rs.get_reminder_by_id(rid)["recurrence"] == "RRULE:FREQ=DAILY"

    r = c.delete(f"/api/miniapp/reminders/{rid}", headers=h)
    assert r.status_code == 200, r.text
    assert rs.get_reminder_by_id(rid) is None
