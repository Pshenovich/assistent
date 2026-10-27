"""Голосовой ввод: переключатели, личный словарь, сниппеты, история диктовок.

Настройки лежат в общем файле пользователя (``user_prefs``, ключ ``voice``),
история — рядом, отдельным файлом ``<uid>.voice_history.json``.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from assistant.stores import user_prefs

FEATURES = ("cleanup", "numbers", "dictionary", "snippets", "history")
# Всё включено по умолчанию: пустые словарь и сниппеты ничего не меняют.
_DEFAULTS = {name: True for name in FEATURES}

MAX_DICTIONARY = 300
MAX_SNIPPETS = 100
MAX_SNIPPET_TEXT = 4000
MAX_PHRASE = 120
MAX_HISTORY = 100

_lock = threading.RLock()


def _voice(prefs: dict[str, Any]) -> dict[str, Any]:
    v = prefs.get("voice")
    return v if isinstance(v, dict) else {}


def _clean_dictionary(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        src = " ".join(str(e.get("from") or "").split())[:MAX_PHRASE]
        dst = str(e.get("to") or "").strip()[:MAX_PHRASE]
        key = src.lower().replace("ё", "е")
        if not src or not dst or key in seen:
            continue
        seen.add(key)
        out.append({"from": src, "to": dst})
        if len(out) >= MAX_DICTIONARY:
            break
    return out


def _clean_snippets(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        trig = " ".join(str(s.get("trigger") or "").split())[:MAX_PHRASE]
        text = str(s.get("text") or "").strip()[:MAX_SNIPPET_TEXT]
        key = trig.lower().replace("ё", "е")
        if not trig or not text or key in seen:
            continue
        seen.add(key)
        out.append({"trigger": trig, "text": text})
        if len(out) >= MAX_SNIPPETS:
            break
    return out


def get_settings(user_id: int) -> dict[str, Any]:
    v = _voice(user_prefs.load_prefs(user_id))
    res: dict[str, Any] = {}
    for name in FEATURES:
        res[name] = bool(v.get(name, _DEFAULTS[name]))
    res["dictionary_entries"] = _clean_dictionary(v.get("dictionary_entries"))
    res["snippet_entries"] = _clean_snippets(v.get("snippet_entries"))
    return res


def update_settings(user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        prefs = user_prefs.load_prefs(user_id)
        v = dict(_voice(prefs))
        for name in FEATURES:
            if patch.get(name) is not None:
                v[name] = bool(patch[name])
        if patch.get("dictionary_entries") is not None:
            v["dictionary_entries"] = _clean_dictionary(patch["dictionary_entries"])
        if patch.get("snippet_entries") is not None:
            v["snippet_entries"] = _clean_snippets(patch["snippet_entries"])
        prefs["voice"] = v
        user_prefs.save_prefs(user_id, prefs)
    return get_settings(user_id)


# Куда ушёл текст — от этого зависит, что применять.
# dictation: диктовка в мини-приложении — всё; command: голосовое в бот — без сниппетов;
# transcript: файлы и встречи — только словарь (запись должна остаться дословной).
def process_text(user_id: int, text: str, *, mode: str = "dictation") -> str:
    from assistant.lib.voice_postprocess import postprocess

    if not user_id:
        return str(text or "").strip()
    try:
        s = get_settings(int(user_id))
    except Exception as e:
        print(f"[voice_prefs] settings_failed uid={user_id} err={e!r}")
        return str(text or "").strip()
    verbatim = mode == "transcript"
    return postprocess(
        text,
        cleanup=s["cleanup"] and not verbatim,
        numbers=s["numbers"] and not verbatim,
        dictionary=s["dictionary_entries"] if s["dictionary"] else None,
        snippets=s["snippet_entries"] if s["snippets"] and mode == "dictation" else None,
    )


def process_transcript_result(user_id: int, result: Any) -> Any:
    """TranscribeResult встречи/файла с заменами из личного словаря."""
    from dataclasses import replace

    if not user_id or result is None:
        return result
    plain = process_text(user_id, result.plain_text or "", mode="transcript")
    formatted = process_text(user_id, result.formatted_text or "", mode="transcript")
    segments = tuple(
        {**s, "text": process_text(user_id, str(s.get("text") or ""), mode="transcript")}
        for s in (getattr(result, "segments", None) or ())
    )
    return replace(result, plain_text=plain, formatted_text=formatted, segments=segments)


# ------------------------------------------------------------------ история


def _history_path(user_id: int) -> Path:
    return user_prefs._dir() / f"{int(user_id)}.voice_history.json"


def list_history(user_id: int) -> list[dict[str, Any]]:
    path = _history_path(user_id)
    with _lock:
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def add_history(user_id: int, *, text: str, raw_text: str = "", source: str = "") -> None:
    if not user_id or not str(text or "").strip():
        return
    if not get_settings(int(user_id))["history"]:
        return
    item = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "text": str(text).strip(),
        "raw_text": str(raw_text or "").strip(),
        "source": source,
    }
    with _lock:
        items = [item] + list_history(user_id)
        _history_path(user_id).write_text(
            json.dumps(items[:MAX_HISTORY], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


def delete_history_item(user_id: int, item_id: str) -> None:
    with _lock:
        items = [x for x in list_history(user_id) if x.get("id") != item_id]
        _history_path(user_id).write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8"
        )


def clear_history(user_id: int) -> None:
    with _lock:
        path = _history_path(user_id)
        if path.is_file():
            path.unlink()
