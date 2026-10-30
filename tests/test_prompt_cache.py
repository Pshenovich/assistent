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


def test_gemini_marked_like_claude(monkeypatch):
    msgs = _run(monkeypatch, "google/gemini-2.5-flash", [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[2]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_other_models_unchanged(monkeypatch):
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    msgs = _run(monkeypatch, "openai/gpt-5.5", hist)
    assert msgs[0]["content"] == "SYS\n\nЗАМЕТКА"
    assert msgs[2]["content"] == "a1"


def test_openai_gets_cache_key_others_not(monkeypatch):
    from assistant.integrations import openrouter_client as oc

    seen = {}

    def fake(payload, *, operation, timeout):
        seen[payload["model"]] = payload.get("prompt_cache_key")
        return {"choices": [{"message": {"content": "ок"}}], "model": payload["model"]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake)
    with oc.usage_user(106):
        for m in ("openai/gpt-5.5", "anthropic/claude-opus-5.5", "google/gemini-2.5-flash"):
            llm._chat_result("SYS", "q", operation="ask", model=m)
    assert seen == {"openai/gpt-5.5": "leo:106:ask", "anthropic/claude-opus-5.5": None, "google/gemini-2.5-flash": None}
    assert "prompt_cache_key" not in oc._payload_for_comet({"model": "x", "messages": [], "prompt_cache_key": "k"})
