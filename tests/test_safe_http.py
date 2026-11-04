"""safe_http: запросы по ссылкам пользователя не уходят во внутреннюю сеть сервера (SSRF)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import pytest
import requests

from assistant.lib import safe_http


@pytest.mark.parametrize(
    "ip,public",
    [
        ("8.8.8.8", True),
        ("2a00:1450:4010:c05::64", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("224.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip, public):
    assert safe_http.is_public_ip(ip) is public


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/dashboard",
        "http://localhost:8095/",
        "http://169.254.169.254/openstack/latest/meta_data.json",
        "http://[::1]/",
        "http://0.0.0.0:8080/",
        "http://2130706433/",
        "http://127.1/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_internal_addresses_blocked_before_connect(url):
    with mock.patch.object(safe_http.requests.Session, "request") as sent:
        with pytest.raises(safe_http.BlockedAddress):
            safe_http.get(url, timeout=2)
    sent.assert_not_called()


def test_non_http_scheme_rejected():
    for url in ("file:///etc/passwd", "gopher://x/", "ftp://x/"):
        with pytest.raises(requests.exceptions.InvalidURL):
            safe_http.get(url)


@pytest.fixture
def local_server():
    class H(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            H.hits += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"internal")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, H
    srv.shutdown()


def test_dns_rebinding_blocked_by_socket_check(local_server):
    """Проверка DNS заранее прошла (ответ «публичный»), а сокет открылся на 127.0.0.1 — запрос не отправлен."""
    srv, handler = local_server
    with mock.patch.object(safe_http, "_precheck", lambda url: None):
        with pytest.raises(safe_http.BlockedAddress):
            safe_http.get(f"http://127.0.0.1:{srv.server_port}/", timeout=3)
    assert handler.hits == 0


class _Resp:
    def __init__(self, status, location=""):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.is_redirect = bool(location)

    def close(self):
        pass


def test_redirect_to_internal_address_blocked():
    calls = []

    def fake_request(self, method, url, **kw):
        calls.append((method, url))
        return _Resp(302, "http://127.0.0.1:8080/dashboard")

    with mock.patch.object(safe_http, "_precheck", side_effect=lambda u: _real_precheck(u)):
        with mock.patch.object(safe_http.requests.Session, "request", fake_request):
            with pytest.raises(safe_http.BlockedAddress):
                safe_http.get("http://8.8.8.8/start")
    assert calls == [("GET", "http://8.8.8.8/start")]


_real_precheck = safe_http._precheck


def test_303_switches_post_to_get_and_drops_body():
    calls = []
    seq = [_Resp(303, "/done"), _Resp(200)]

    def fake_request(self, method, url, **kw):
        calls.append((method, url, "json" in kw))
        return seq.pop(0)

    with mock.patch.object(safe_http, "_precheck", lambda u: None):
        with mock.patch.object(safe_http.requests.Session, "request", fake_request):
            resp = safe_http.post("http://8.8.8.8/form", json={"a": 1})
    assert resp.status_code == 200
    assert calls == [("POST", "http://8.8.8.8/form", True), ("GET", "http://8.8.8.8/done", False)]


def test_ffmpeg_voice_conversion_pins_format_and_protocols(monkeypatch):
    """Плейлист HLS под видом .webm: ffmpeg не угадывает формат и не ходит по ссылкам (только file)."""
    import shutil
    import subprocess

    import usage_server

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out = cmd[-1]
        with open(out, "wb") as f:
            f.write(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)
    usage_server._prepare_miniapp_voice_audio(b"#EXTM3U\nhttp://127.0.0.1/x.ts\n", "voice.webm", "audio/webm")
    cmd = seen["cmd"]
    i = cmd.index("-i")
    assert cmd[cmd.index("-f") + 1] == "matroska" and cmd.index("-f") < i
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file" and cmd.index("-protocol_whitelist") < i


def test_mail_custom_server_on_internal_address_refused(monkeypatch):
    """Свой IMAP-сервер на 127.0.0.1 / во внутренней сети: вход даже не пробуем (сканирование портов)."""
    from assistant.services import mail_connect, mail_source

    tried = []
    monkeypatch.setattr(mail_source, "check_login", lambda *a, **k: tried.append(a))
    for host in ("127.0.0.1", "10.0.0.5", "localhost"):
        with pytest.raises(mail_connect.ConnectError) as ei:
            mail_connect.connect(1, {"email": "a@corp.example", "password": "p", "imap_host": host, "imap_port": 8080})
        assert ei.value.step == "server"
    assert tried == []
