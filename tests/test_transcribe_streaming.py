"""Запись встречи уходит в транскрибацию с диска потоком — сервер получает то же, что и раньше из памяти."""

from __future__ import annotations

import json
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from assistant.integrations import transcribe as obu


@pytest.fixture
def fake_obuchat(monkeypatch):
    got: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            raw = self.rfile.read(n)
            msg = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw
            )
            fields, files = {}, {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                fname = part.get_filename()
                if fname is not None:
                    files[name] = (fname, part.get_payload(decode=True))
                else:
                    fields[name] = part.get_content().strip()
            got.append({"fields": fields, "files": files, "length": n, "chunked": "Transfer-Encoding" in self.headers})
            body = json.dumps({"status": "done", "text": "привет"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(obu, "OBUCHAT_TRANSCRIBE_BASE", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(obu, "_api_key", lambda: "k")
    monkeypatch.setattr(obu.os, "environ", {**obu.os.environ, "OBUCHAT_TRANSCRIBE_SPEAKERS": "auto"})
    yield got
    srv.shutdown()


def test_file_upload_matches_bytes_upload(fake_obuchat, tmp_path):
    payload = bytes(range(256)) * 9000 + b"\r\n--tail"  # больше одного куска, с CRLF внутри
    path = tmp_path / "rec.bin"
    path.write_bytes(payload)
    a = obu.transcribe_bytes(payload, 'Созвон "итоги".oga')
    b = obu.transcribe_file(path, 'Созвон "итоги".oga')
    assert a.plain_text == b.plain_text == "привет"
    from_mem, from_disk = fake_obuchat
    assert from_disk["files"]["file"][1] == payload
    assert from_disk["files"]["file"][1] == from_mem["files"]["file"][1]
    assert from_disk["fields"] == from_mem["fields"]
    assert from_disk["files"]["file"][0].endswith(".ogg")
    assert not from_disk["chunked"] and from_disk["length"] > len(payload)
