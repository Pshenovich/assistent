"""/healthz: база и пульс бота."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

import usage_server
from assistant.lib import heartbeat
from assistant.stores import notes as notes_store


def _client() -> TestClient:
    notes_store._conn()  # база заметок существует, как на проде
    return TestClient(usage_server.app)


def test_healthz_ok_without_bot_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setenv("HEARTBEAT_DIR", str(tmp_path))
    r = _client().get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db"] is True
    assert body["bot_heartbeat_age_sec"] is None
    assert r.headers["cache-control"] == "no-store"


def test_healthz_fresh_bot_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setenv("HEARTBEAT_DIR", str(tmp_path))
    heartbeat.beat("bot")
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json()["bot_heartbeat_age_sec"] < 5


def test_healthz_stale_bot_is_503(monkeypatch, tmp_path):
    monkeypatch.setenv("HEARTBEAT_DIR", str(tmp_path))
    (tmp_path / "bot_heartbeat.json").write_text(json.dumps({"ts": time.time() - 3600}))
    r = _client().get("/healthz")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_healthz_missing_db_does_not_create_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HEARTBEAT_DIR", str(tmp_path))
    missing = tmp_path / "nope.sqlite"
    monkeypatch.setattr(notes_store, "_db_path", lambda: missing)
    r = TestClient(usage_server.app).get("/healthz")
    assert r.status_code == 503
    assert r.json()["db"] is False
    assert not missing.exists()


def test_healthz_requires_worker_when_jobs_moved(monkeypatch, tmp_path):
    monkeypatch.setenv("HEARTBEAT_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_IN_WORKER", "1")
    heartbeat.beat("bot")
    r = _client().get("/healthz")
    assert r.status_code == 503 and r.json()["worker_heartbeat_age_sec"] is None
    heartbeat.beat("worker")
    r = _client().get("/healthz")
    assert r.status_code == 200 and r.json()["worker_heartbeat_age_sec"] < 5
