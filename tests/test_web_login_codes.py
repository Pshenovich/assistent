"""Одноразовые коды входа в браузер без oauth.telegram.org."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistant.stores import web_login_codes as store


@pytest.fixture()
def codes_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "web_login_codes.json"
    monkeypatch.setattr(store, "_PATH", path)
    return path


def test_issue_and_consume_once(codes_path: Path) -> None:
    issued = store.issue_code(871463833, username="artyawn", first_name="Arty")
    assert len(issued["code"]) == 8
    assert issued["ttl_sec"] == 600
    user = store.consume_code(issued["code"].lower())
    assert user == {"id": 871463833, "username": "artyawn", "first_name": "Arty"}
    assert store.consume_code(issued["code"]) is None
    data = json.loads(codes_path.read_text(encoding="utf-8"))
    assert issued["code"] not in data.get("codes", {})


def test_one_active_code_per_user(codes_path: Path) -> None:
    first = store.issue_code(42, username="a")
    second = store.issue_code(42, username="a")
    assert first["code"] != second["code"]
    assert store.consume_code(first["code"]) is None
    assert store.consume_code(second["code"])["id"] == 42
