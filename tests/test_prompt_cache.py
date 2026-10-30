"""Кэш подсказки для Claude: метки на системной части и последней реплике истории; у других моделей запрос прежний."""

from __future__ import annotations

from assistant.nlu import llm


def _run(monkeypatch, model, history):
    seen = {}

    def fake(payload, *, operation, timeout):
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "ок"}}], "model": model}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake)
    llm._chat_result("SYS", "вопрос", operation="ask", model=model, history=history, context_prefix="ЗАМЕТКА")
    return seen["payload"]["messages"]


def test_claude_marks_system_and_last_history(monkeypatch):
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    msgs = _run(monkeypatch, "anthropic/claude-opus-5.5", hist)
    assert msgs[0]["content"] == [{"type": "text", "text": "SYS\n\nЗАМЕТКА", "cache_control": {"type": "ephemeral"}}]
    assert msgs[1]["content"] == "q1"  # середина истории без метки
    assert msgs[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[2]["content"][0]["text"] == "a1"
    assert msgs[3]["content"] == "вопрос"  # текущий вопрос не кэшируем


def test_claude_without_history(monkeypatch):
    msgs = _run(monkeypatch, "anthropic/claude-opus-5.5", None)
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[1]["content"] == "вопрос"


def test_other_models_unchanged(monkeypatch):
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    msgs = _run(monkeypatch, "openai/gpt-5.5", hist)
    assert msgs[0]["content"] == "SYS\n\nЗАМЕТКА"
    assert msgs[2]["content"] == "a1"
