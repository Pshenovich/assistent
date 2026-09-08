from assistant.nlu import llm


def test_answer_with_context_forwards_history(monkeypatch):
    seen: dict = {}

    def fake_chat(system, user, **kwargs):
        seen["history"] = kwargs.get("history")
        seen["user"] = user
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
