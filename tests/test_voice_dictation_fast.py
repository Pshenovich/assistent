"""Голос из чата с Лео — без LLM у Obuchat; встречи и файлы — с LLM, как раньше."""

from __future__ import annotations

from assistant.integrations import transcribe as obu


def test_form_fields_default_keeps_llm():
    assert obu._form_fields(None)["llm_enabled"] == "true"
    assert obu._form_fields("2", llm=False) == {"speakers": "2", "llm_enabled": "false"}


def test_dictation_llm_off_by_default(monkeypatch):
    monkeypatch.delenv("VOICE_DICTATION_LLM", raising=False)
    assert obu.dictation_llm_enabled() is False
    monkeypatch.setenv("VOICE_DICTATION_LLM", "1")
    assert obu.dictation_llm_enabled() is True


def test_dictation_speakers(monkeypatch):
    monkeypatch.delenv("OBUCHAT_DICTATION_SPEAKERS", raising=False)
    assert obu.dictation_speakers() == "none"
    monkeypatch.setenv("OBUCHAT_DICTATION_SPEAKERS", "auto")
    assert obu.dictation_speakers() == "auto"


def test_transcribe_bytes_sends_llm_false(monkeypatch):
    seen = {}

    def fake(**send):
        seen.update(send)
        return "ok"

    monkeypatch.setattr(obu, "_transcribe", fake)
    assert obu.transcribe_bytes(b"x", "voice.oga", llm=False) == "ok"
    assert seen["data"]["llm_enabled"] == "false"
    assert seen["files"]["file"][0] == "voice.ogg"


def test_file_transcription_keeps_llm(monkeypatch, tmp_path):
    p = tmp_path / "a.ogg"
    p.write_bytes(b"abc")
    seen = {}

    def fake(**send):
        seen.update(send)
        return "ok"

    monkeypatch.setattr(obu, "_transcribe", fake)
    obu.transcribe_file(p, "a.ogg")
    body = b"".join(seen["data"])
    assert b'name="llm_enabled"\r\n\r\ntrue' in body


def test_miniapp_webm_falls_back_to_wav(monkeypatch):
    import usage_server as us

    monkeypatch.setattr(us, "_remux_webm_to_ogg", lambda src, dst: False)

    def fake_wav(src, dst):
        dst.write_bytes(b"WAV")

    monkeypatch.setattr(us, "_convert_webm_to_wav", fake_wav)
    assert us._prepare_miniapp_voice_audio(b"webm", "d.webm", "audio/webm") == (b"WAV", "voice.wav")


def test_miniapp_webm_remuxed_to_ogg(monkeypatch):
    import usage_server as us

    def fake_ogg(src, dst):
        dst.write_bytes(b"OGG")
        return True

    monkeypatch.setattr(us, "_remux_webm_to_ogg", fake_ogg)
    assert us._prepare_miniapp_voice_audio(b"webm", "d.webm", "audio/webm") == (b"OGG", "voice.ogg")
    assert us._prepare_miniapp_voice_audio(b"m4a", "d.m4a", "audio/mp4") == (b"m4a", "d.m4a")


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_quick_mode_posts_without_wait_and_survives_503(monkeypatch):
    monkeypatch.setenv("OBUCHAT_TRANSCRIBE", "k")
    monkeypatch.setattr(obu.time, "sleep", lambda s: None)
    posted = {}

    def fake_post(url, **kw):
        posted["url"] = url
        return _Resp(202, {"job_id": "j1", "status": "queued"})

    polls = iter([
        _Resp(503, ValueError("html")),
        _Resp(200, {"status": "processing"}),
        _Resp(200, {"status": "done", "transcript": [{"text": "Привет"}]}),
    ])
    monkeypatch.setattr(obu.requests, "post", fake_post)
    monkeypatch.setattr(obu.requests, "get", lambda url, **kw: next(polls))
    res = obu.transcribe_bytes(b"x", "voice.ogg", speakers="none", llm=False, quick=True)
    assert "wait=true" not in posted["url"]
    assert res.plain_text == "Привет"


def test_default_mode_keeps_wait(monkeypatch):
    monkeypatch.setenv("OBUCHAT_TRANSCRIBE", "k")
    posted = {}

    def fake_post(url, **kw):
        posted["url"] = url
        return _Resp(200, {"status": "done", "transcript": [{"text": "ok"}]})

    monkeypatch.setattr(obu.requests, "post", fake_post)
    assert obu.transcribe_bytes(b"x", "a.ogg").plain_text == "ok"
    assert posted["url"].endswith("?wait=true")


def test_quick_mode_logs_queue_and_work(monkeypatch, capsys):
    monkeypatch.setenv("OBUCHAT_TRANSCRIBE", "k")
    monkeypatch.setattr(obu.time, "sleep", lambda s: None)
    monkeypatch.setattr(obu.requests, "post", lambda url, **kw: _Resp(202, {"job_id": "j9", "status": "queued"}))
    done = {
        "status": "done",
        "transcript": [{"text": "Ок"}],
        "queued_at": "2026-11-11T07:55:00+00:00",
        "started_at": "2026-11-11T07:55:30+00:00",
        "finished_at": "2026-11-11T07:55:31.500000+00:00",
        "meta": {"duration_seconds": 6.3, "speakers_mode": "none", "llm_enabled": False},
    }
    monkeypatch.setattr(obu.requests, "get", lambda url, **kw: _Resp(200, done))
    obu.transcribe_bytes(b"x", "voice.ogg", quick=True)
    out = capsys.readouterr().out
    assert "queue=30.00s" in out and "work=1.50s" in out
