"""loadNotes не должен рендерить синхронно до того, как запомнен notesLoadPromise.

Иначе setNotesSubTab → renderSidebarTrees → loadNotes() уходит в рекурсию,
и десктопный клиент шлёт тысячи GET /notes за одно открытие.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "webapp" / "app.js"


def _load_notes_iife_head() -> str:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("async function loadNotes() {")
    iife = src.index("notesLoadPromise = (async function () {", start)
    return src[iife : iife + 600]


def test_load_notes_yields_before_rendering() -> None:
    head = _load_notes_iife_head()
    first_await = re.search(r"\bawait\b", head)
    first_render = re.search(r"\b(setNotesSubTab|renderNotesPanesFromData|renderSidebarTrees)\(", head)
    assert first_await and first_render
    assert first_await.start() < first_render.start()
