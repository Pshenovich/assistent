"""classify_intent / _parse_llm_route / resolve_route: разбор ответа LLM и выбор маршрута."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import requests

from assistant.nlu import intent_router as ir
from assistant.nlu.intent_router import IntentContext, Route


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.delenv("INTENT_ROUTER_ENABLED", raising=False)
    monkeypatch.setattr(ir, "is_llm_configured", lambda: True)
    with patch.object(ir, "openrouter_chat_completion") as call:
        yield call


def test_classify_returns_llm_route(llm):
    llm.return_value = _completion(
        '```json\n{"skill":"reminder","sub_intent":"","confidence":0.9,"body":"купить хлеб","reason":"напомни"}\n```'
    )

    route = ir.classify_intent(IntentContext(text="напомни купить хлеб", regex_hint={"skill": "reminder"}))

    assert route == Route(skill="reminder", body="купить хлеб", confidence=0.9, source="llm", reason="напомни")
    payload = llm.call_args.args[0]
    user_obj = json.loads(payload["messages"][1]["content"])
    assert user_obj["regex_hint"] == {"skill": "reminder"}
    assert payload["temperature"] == 0
    assert llm.call_args.kwargs["operation"] == "intent_route"


def test_classify_skipped_without_llm(monkeypatch, llm):
    monkeypatch.setattr(ir, "is_llm_configured", lambda: False)
    assert ir.classify_intent(IntentContext(text="напомни"), force=True) is None
    llm.assert_not_called()


def test_classify_respects_disabled_router_unless_forced(monkeypatch, llm):
    monkeypatch.setenv("INTENT_ROUTER_ENABLED", "0")
    llm.return_value = _completion('{"skill":"calendar","sub_intent":"create","confidence":0.8}')

    assert ir.classify_intent(IntentContext(text="встреча завтра")) is None
    llm.assert_not_called()
    assert ir.classify_intent(IntentContext(text="встреча завтра"), force=True).calendar_kind == "create"


def test_classify_skips_too_short_text(llm):
    assert ir.classify_intent(IntentContext(text=" a ")) is None
    llm.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        requests.Timeout("slow"),
        requests.ConnectionError("down"),
        {"choices": []},
        {"error": "quota"},
        _completion("not json at all"),
        _completion('["calendar"]'),
        _completion('{"skill":"launch_rockets","confidence":1}'),
        _completion('{"skill":"none","confidence":1}'),
    ],
    ids=["timeout", "network", "no-choices", "error-body", "not-json", "json-list", "unknown-skill", "none-skill"],
)
def test_classify_failures_fall_back_to_none(llm, outcome):
    if isinstance(outcome, Exception):
        llm.side_effect = outcome
    else:
        llm.return_value = outcome
    assert ir.classify_intent(IntentContext(text="что у меня завтра")) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.7", 0.7),
        (5, 1.0),
        (-1, 0.0),
        ("high", 0.0),
        (None, 0.0),
    ],
)
def test_parse_confidence_is_clamped(raw, expected):
    route = ir._parse_llm_route({"skill": "ask", "confidence": raw}, "что это")
    assert route.confidence == expected


def test_parse_body_falls_back_to_text_then_user_text():
    assert ir._parse_llm_route({"skill": "ask", "text": "t"}, "orig").body == "t"
    assert ir._parse_llm_route({"skill": "ask", "body": "  "}, "orig").body == "orig"


@pytest.mark.parametrize(
    ("sub", "text", "kind", "sub_out"),
    [
        ("create", "встреча завтра", "create", "create"),
        ("free_slots", "когда я свободен", "free", "free_slots"),
        ("free", "что у меня завтра", "free", "free"),
        ("zoom", "дай зум", "zoom", "zoom"),
        ("instant", "ссылку", "zoom", "instant"),
        ("telemost_delete", "удали телемост", "telemost", "telemost_delete"),
        ("update", "перенеси зум на 15", "zoom", "zoom_update"),
        ("delete", "удали Zoom", "zoom", "zoom_delete"),
        ("update", "перенеси телемост", "telemost", "telemost_update"),
        ("update", "перенеси встречу про бюджет", "update", "update"),
        ("contacts", "контакты", "contacts", "contacts"),
        ("weird", "что-то", None, "weird"),
    ],
)
def test_parse_calendar_sub_intents(sub, text, kind, sub_out):
    route = ir._parse_llm_route({"skill": "calendar", "sub_intent": sub, "confidence": 0.9}, text)
    assert (route.calendar_kind, route.sub_intent) == (kind, sub_out)


def test_non_calendar_skill_has_no_calendar_kind():
    assert ir._parse_llm_route({"skill": "reminder", "sub_intent": "create"}, "напомни").calendar_kind is None


def _rx(skill: str, **kw) -> Route:
    return Route(skill=skill, source="regex", **kw)


def _llm(skill: str, conf: float, **kw) -> Route:
    return Route(skill=skill, confidence=conf, source="llm", **kw)


@pytest.mark.parametrize("skill", ["zoom_record", "assign_task"])
def test_resolve_regex_wins_for_protected_skills(skill):
    regex = _rx(skill)
    assert ir.resolve_route(regex, _llm("bitrix", 0.99)) is regex


def test_resolve_empty():
    assert ir.resolve_route(None, None) is None


def test_resolve_regex_only():
    regex = _rx("reminder")
    assert ir.resolve_route(regex, None) is regex


def test_resolve_same_skill_other_sub_intent(monkeypatch):
    monkeypatch.delenv("INTENT_ROUTER_MIN_CONFIDENCE", raising=False)
    regex = _rx("calendar", sub_intent="create", calendar_kind="create")
    sure = _llm("calendar", 0.7, sub_intent="update", calendar_kind="update")
    unsure = _llm("calendar", 0.5, sub_intent="update", calendar_kind="update")
    assert ir.resolve_route(regex, sure) is sure
    assert ir.resolve_route(regex, unsure) is regex


@pytest.mark.parametrize("bad", ["abc", ""])
def test_bad_threshold_env_uses_defaults(monkeypatch, bad):
    monkeypatch.setenv("INTENT_ROUTER_MIN_CONFIDENCE", bad)
    monkeypatch.setenv("INTENT_ROUTER_OVERRIDE_CONFIDENCE", bad)
    assert ir._min_confidence() == 0.65
    assert ir._override_confidence() == 0.82


def test_router_model_override(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_ROUTER", raising=False)
    assert ir._router_model() == "google/gemini-2.5-flash"
    monkeypatch.setenv("OPENROUTER_MODEL_ROUTER", "x/y")
    assert ir._router_model() == "x/y"
