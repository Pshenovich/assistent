"""Лео знает Орбиту: указатель функций в промпте, инструмент app_help и карточка «Показать»."""

from __future__ import annotations

import itertools
import json

from assistant.onboarding import leo_help
from assistant.onboarding import state as ob
from assistant.services import gpt_calendar_tools as g
from assistant.services import leo_agent

_ids = itertools.count(980_000, 10)


def test_prompt_addon_lists_features_and_reaches_leo():
    addon = leo_help.system_addon()
    assert "app_help" in addon and "board · Доска" in addon
    assert "app_help" in leo_agent.calendar_addon(next(_ids))


def test_tool_registered_once():
    names = [t["function"]["name"] for t in g.TOOLS]
    assert names.count("app_help") == 1
    assert g.EXECUTORS["app_help"] is leo_help.tool_app_help
    assert any(t["function"]["name"] == "app_help" for t in g.active_tools(next(_ids)))


def test_app_help_returns_steps_and_guide_card():
    uid = next(_ids)
    ctx = g.GptCalendarContext(uid=uid)
    out = json.loads(leo_help.tool_app_help(ctx, {"feature": "board"}))
    assert out["found"] and out["id"] == "board" and out["how"]
    assert out["user_has_tried"] is False
    assert len(ctx.actions) == 1
    card = g.public_action(g.get_action(ctx.actions[0]), viewer_uid=uid)
    assert card["kind"] == "guide" and card["open"] == {"type": "guide", "id": "board"}
    assert "guide=board" in card["link"]


def test_app_help_matches_free_text_and_knows_used():
    uid = next(_ids)
    ob.apply_event(uid, "used", "board")
    ctx = g.GptCalendarContext(uid=uid)
    out = json.loads(leo_help.tool_app_help(ctx, {"query": "где тут доска?"}))
    assert out["id"] == "board" and out["user_has_tried"] is True


def test_app_help_unknown_is_honest():
    ctx = g.GptCalendarContext(uid=next(_ids))
    out = json.loads(leo_help.tool_app_help(ctx, {"query": "zzz qqq"}))
    assert out["found"] is False and out["features"]
    assert ctx.actions == []


def test_telegram_keyboard_has_show_button():
    from assistant.skills import leo_chat

    kb = leo_chat.action_keyboard([{"id": "a1", "kind": "guide", "status": "done", "title": "Доска", "link": "https://x/webapp/?b=1&guide=board"}])
    btn = kb.inline_keyboard[0][0]
    assert "Показать" in btn.text and btn.url.endswith("guide=board")
