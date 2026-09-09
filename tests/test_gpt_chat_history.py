from assistant.nlu import llm
from assistant.nlu.ask_context import assemble_ask_messages, build_context_prefix, clip_text


def test_answer_with_context_forwards_history(monkeypatch):
    seen: dict = {}

    def fake_chat(system, user, **kwargs):
        seen["history"] = kwargs.get("history")
        seen["user"] = user
        seen["system"] = system
        return "ok"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    out = llm.answer_with_context(
        "второй вопрос",
        "заметка",
        history=[{"role": "user", "content": "первый вопрос"}],
    )
    assert out == "ok"
    assert seen["history"][0]["content"] == "первый вопрос"
    assert "второй вопрос" in seen["user"]


def test_answer_with_context_puts_note_in_system(monkeypatch):
    seen: dict = {}

    def fake_chat(system, user, **kwargs):
        seen["system"] = system
        seen["user"] = user
        seen["context_prefix"] = kwargs.get("context_prefix")
        return "ok"

    monkeypatch.setattr(llm, "_chat", fake_chat)
    out = llm.answer_with_context(
        "что решили?",
        "",
        note_title="Встреча",
        note_text="Договорились о пилоте",
        knowledge_brief="Каталог:\n• Боты — 10 ₽",
        quote="пилот",
    )
    assert out == "ok"
    assert "что решили?" in seen["user"]
    prefix = seen["context_prefix"] or ""
    assert "БАЗА ЗНАНИЙ:" in prefix
    assert "ЗАМЕТКА:" in prefix
    assert "ВЫДЕЛЕННЫЙ ТЕКСТ:" not in prefix
    assert "пилот" in seen["user"]
    assert "Договорились о пилоте" in prefix
    assert prefix.index("БАЗА ЗНАНИЙ:") < prefix.index("ЗАМЕТКА:")


def test_clip_and_prefix_budgets():
    long_note = "x" * 9000
    prefix = build_context_prefix(note_title="T", note_text=long_note, quote="q" * 800)
    assert "ЗАМЕТКА:" in prefix
    assert len(clip_text(long_note, 8000)) == 8000
    extra, user = assemble_ask_messages(question="hi", note_text="тело", quote="фрагмент")
    assert extra.startswith("ЗАМЕТКА:")
    assert "фрагмент" in user
    assert "ВЫДЕЛЕННЫЙ ТЕКСТ:" in user
    assert "фрагмент" not in extra


def test_answer_with_context_sends_pdf_as_openrouter_file(monkeypatch):
    seen: dict = {}

    def fake_complete(payload, **kwargs):
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "каникулы 1–10 января"}}]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake_complete)
    out = llm.answer_with_context_result(
        "когда каникулы?",
        files=[{"filename": "holidays.pdf", "mime": "application/pdf", "b64": "JVBERg=="}],
    )
    assert "каникулы" in out["answer"]
    user = seen["payload"]["messages"][-1]["content"]
    assert isinstance(user, list)
    file_part = next(p for p in user if p.get("type") == "file")
    assert file_part["file"]["filename"] == "holidays.pdf"
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert seen["payload"]["plugins"] == [{"id": "file-parser"}]


def test_chat_merges_context_prefix_into_system(monkeypatch):
    seen: dict = {}

    def fake_complete(payload, **kwargs):
        seen["messages"] = payload["messages"]
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake_complete)
    out = llm._chat("SYS", "вопрос", operation="ask", context_prefix="БАЗА ЗНАНИЙ:\nX")
    assert out == "ok"
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][0]["content"].startswith("SYS")
    assert "БАЗА ЗНАНИЙ:\nX" in seen["messages"][0]["content"]
    assert seen["messages"][-1]["content"] == "вопрос"

