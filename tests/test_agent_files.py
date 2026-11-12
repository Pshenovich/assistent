"""PAIE / Research: прикреплённые к запросу файлы идут в контекст текстом; Research не держит сервер."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

import usage_server
from assistant.stores import comment_files
from assistant.stores import notes as notes_store


def _pdf(text: str) -> bytes:
    """Минимальный PDF с одной страницей и текстом."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    yield
    notes_store._CONN = None


def _save(name, mime, data, author=5):
    return comment_files.save_bytes(owner_user_id=5, item_kind="local", item_id=7, author_user_id=author,
                                    filename=name, mime=mime, data=data)


def test_files_text_txt_and_pdf(db):
    txt = _save("brief.txt", "text/plain", "Выручка 120 млн".encode("utf-8"))
    pdf = _save("report.pdf", "application/pdf", _pdf("Quarterly revenue grew 15 percent"))
    other = _save("secret.txt", "text/plain", b"not yours", author=99)
    text = usage_server._discuss_files_text(5, [txt["id"], pdf["id"], "x"])
    assert "Файл «brief.txt»" in text and "Выручка 120 млн" in text
    assert "Файл «report.pdf»" in text and "revenue grew 15" in text
    assert "not yours" not in usage_server._discuss_files_text(6, [other["id"]])
    merged = usage_server._with_files_text("ЗАМЕТКА", text)
    assert merged.startswith("ЗАМЕТКА") and "ПРИКРЕПЛЁННЫЕ ФАЙЛЫ" in merged
    assert usage_server._with_files_text("ЗАМЕТКА", "") == "ЗАМЕТКА"


def test_research_job_runs_off_event_loop(monkeypatch):
    """Долгий синхронный Research не должен останавливать цикл событий сервера."""
    from assistant.board import note_research as nr

    def slow(**kw):
        time.sleep(0.4)
        return {"comment": {"id": 1}}

    monkeypatch.setattr(nr, "run_note_research", slow)
    monkeypatch.setattr(nr, "get_job", lambda *a, **k: {})
    monkeypatch.setattr(nr, "_update_job", lambda *a, **k: None)

    async def main():
        ticks = 0
        done_at = None

        async def ticker():
            nonlocal ticks
            for _ in range(40):
                await asyncio.sleep(0.02)
                ticks += 1

        async def job():
            nonlocal done_at
            await nr.run_note_research_job(5, "local", "7")
            done_at = ticks

        await asyncio.gather(job(), ticker())
        return done_at

    assert asyncio.run(main()) >= 10  # пока шёл Research (0,4 с), цикл событий продолжал работать
