"""Саммари v2: лимит ответа с запасом под рассуждения; обрезанный JSON не проглатывается молча."""

from assistant.nlu import summary_v2


def test_call_json_adds_reasoning_headroom(capsys):
    seen = {}

    def chat(system, user, **kw):
        seen.update(kw)
        return '{"tasks": [{"task": "Сделать'  # обрезано лимитом

    assert summary_v2._call_json(chat, "s", {}, operation="summarize_task_candidates", model="m", max_tokens=4096) is None
    assert seen["max_tokens"] == 4096 + summary_v2.REASONING_TOKENS
    assert "summarize_task_candidates unparsable" in capsys.readouterr().out
