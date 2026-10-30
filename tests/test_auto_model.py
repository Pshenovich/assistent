"""«Авто»: какая модель отвечает на какой вопрос (assistant/nlu/auto_model.py)."""

from __future__ import annotations

import pytest

from assistant.integrations import openrouter_client as orc
from assistant.nlu import auto_model as am


@pytest.mark.parametrize(
    "question",
    [
        "Что такое Playbook",
        "как записываться к врачам по московскому ОМС?",
        "покажи разметку маркдаун, какие там есть спец символы",
        "какая погода обычно в Москве в ноябре?",
    ],
)
def test_simple_general_question_goes_cheap(question):
    assert am.route(question)[0] == "cheap"


@pytest.mark.parametrize(
    "question",
    [
        "Поставь встречу завтра в 8:30 с Мыздриковым",
        "поставь мне задачу на следующую пятницу",
        "Удали эти встречи из календаря",
        "Какие у меня задачи просрочены?",
        "сделай презентацию по проекту",
        "нарисуй схему процесса на доске",
        "Нужно сделать проектный документ по продукту",
        "проанализируй выручку по месяцам",
        "что в таблице https://docs.google.com/spreadsheets/d/abc/edit",
    ],
)
def test_actions_documents_analysis_go_strong(question):
    assert am.route(question)[0] == "strong"


def test_note_attachment_and_followups_go_strong():
    assert am.route("что такое NPS?", in_note=True)[0] == "strong"
    assert am.route("что на фото?", has_attachments=True)[0] == "strong"
    # «Пятница 14:00» — ответ на уточнение к постановке задачи
    hist = [{"role": "user", "content": "поставь задачу Андрею"}, {"role": "assistant", "content": "На когда?"}]
    assert am.route("Пятница 14:00", history=hist)[0] == "strong"
    done = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "Готово\n[Уже выполнено, не повторять: …]"}]
    assert am.route("а ещё одну такую же", history=done)[0] == "strong"


def test_resolve_picks_models_and_hint(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_AUTO_STRONG", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL_AUTO_CHEAP", raising=False)
    assert am.resolve("leo/auto", "что такое NPS?") == ("anthropic/claude-haiku-5.5", am.CHEAP_HINT)
    assert am.resolve("leo/auto", "поставь задачу на завтра") == ("openai/gpt-5.5", "")
    assert am.resolve("openai/gpt-5.6-sol", "что такое NPS?") == ("openai/gpt-5.6-sol", "")


def test_auto_is_first_in_pickers_and_default():
    picker = orc.build_gpt_picker_models(set())
    assert picker["models"][0]["id"] == am.AUTO_MODEL_ID
    assert "экономить" in picker["models"][0]["note"]
    assert picker["default"] == am.AUTO_MODEL_ID
    assert orc.tg_leo_models()[0]["id"] == am.AUTO_MODEL_ID
    assert orc.tg_leo_model_allowed(am.AUTO_MODEL_ID)
    assert orc.gpt_display_label(am.AUTO_MODEL_ID) == "Авто"
    assert orc.gpt_display_label("anthropic/claude-haiku-5.5") == "Claude Haiku 5.5"


def test_unresolved_auto_never_reaches_openrouter(monkeypatch):
    sent = []
    monkeypatch.setattr(orc, "_chat_completion", lambda payload, **kw: sent.append(payload["model"]) or {})
    orc.openrouter_chat_completion({"model": am.AUTO_MODEL_ID, "messages": []}, operation="ask")
    assert sent == [am.strong_model()]
