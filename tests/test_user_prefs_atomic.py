"""Настройки пользователя: запись атомарная (читатель не видит пустой файл), кэш чтения — копия."""

from __future__ import annotations

import multiprocessing as mp
import os


def _writer(d: str, n: int) -> None:
    os.environ["USER_PREFS_DIR"] = d
    from assistant.stores import user_prefs

    for i in range(n):
        user_prefs.save_prefs(7, {"timezone": "Europe/Moscow", "digest_enabled": True, "i": i, "pad": "x" * 5000})


def test_reader_never_sees_empty_prefs_during_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    from assistant.stores import user_prefs

    user_prefs.save_prefs(7, {"timezone": "Europe/Moscow", "digest_enabled": True})
    p = mp.get_context("spawn").Process(target=_writer, args=(str(tmp_path), 400))
    p.start()
    empty = 0
    while p.is_alive():
        if user_prefs.load_prefs(7).get("timezone") != "Europe/Moscow":
            empty += 1
    p.join()
    assert p.exitcode == 0
    assert empty == 0
    assert user_prefs.load_prefs(7)["i"] == 399


def test_cached_prefs_are_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    from assistant.stores import user_prefs

    user_prefs.save_prefs(8, {"a": {"b": 1}})
    got = user_prefs.load_prefs(8)
    got["a"]["b"] = 2
    assert user_prefs.load_prefs(8) == {"a": {"b": 1}}
