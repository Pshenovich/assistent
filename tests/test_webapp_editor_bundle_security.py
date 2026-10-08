"""Собранный редактор должен содержать защиту mergeAttributes от ключа __proto__.

GHSA-cp6q-959q-f8rh: в @tiptap/core <2.27.3 атрибуты из {"__proto__": {...}}
становились унаследованными и попадали в DOM (например, onerror).
npm audit продолжает помечать 2.x, но исправление есть с 2.27.3.
"""

from __future__ import annotations

import json
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"


def test_bundle_has_merge_attributes_proto_guard() -> None:
    bundle = (WEBAPP / "note-rich-editor.js").read_text(encoding="utf-8")
    assert '==="__proto__"){Object.defineProperty(' in bundle


def test_locked_tiptap_core_is_patched() -> None:
    lock = json.loads((WEBAPP / "package-lock.json").read_text(encoding="utf-8"))
    version = lock["packages"]["node_modules/@tiptap/core"]["version"]
    major, minor, patch = (int(x) for x in version.split(".")[:3])
    assert (major, minor, patch) >= (2, 27, 3)
