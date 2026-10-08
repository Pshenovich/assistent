"""Роутинг-логи идут в stdout (journald), а не в файл в общем /tmp."""

from __future__ import annotations

import builtins
from pathlib import Path

from assistant.skills import bitrix


def test_route_log_prints_without_touching_files(capsys, monkeypatch) -> None:
    def _no_open(*args, **kwargs):
        raise AssertionError(f"unexpected open{args!r}")

    monkeypatch.setattr(builtins, "open", _no_open)
    bitrix._route_log("[bitrix] enter uid=1")
    assert capsys.readouterr().out == "[bitrix] enter uid=1\n"


def test_no_shared_tmp_route_log_in_sources() -> None:
    root = Path(__file__).resolve().parents[1] / "assistant"
    offenders = [
        str(p) for p in root.rglob("*.py") if "/tmp/leo-route.log" in p.read_text("utf-8")
    ]
    assert offenders == []
