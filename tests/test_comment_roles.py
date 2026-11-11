"""Реплику «Лео / ИИ» ставит только сервер: служебный prefix и «ИИ-имя» от человека не работают."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import usage_server
from assistant.stores import share_comments as sc


@pytest.mark.parametrize("prefix", ["__gpt__", "__research__", "__agent__:x", "  __gpt__"])
def test_service_prefix_from_client_dropped(prefix):
    assert sc.user_prefix(prefix) == ""


def test_plain_anchor_prefix_kept():
    assert sc.user_prefix("текст перед цитатой ") == "текст перед цитатой "


@pytest.mark.parametrize(
    "first,username",
    [("GPT", None), ("gpt-5", None), ("GPT Bot", None), ("Research", None), ("CHAIR", None), ("Вова", "research")],
)
def test_human_with_ai_like_name_is_not_ai(first, username):
    p = SimpleNamespace(user={"first_name": first, "username": username}, telegram_user_id=1)
    _, name, uname = usage_server._comment_author_from_principal(p)
    row = {"author_name": name, "author_username": uname or "", "prefix": ""}
    assert not (sc.is_gpt_turn(row) or sc.is_research_turn(row) or sc.is_paie_comment(row) or sc.is_agent_turn(row))


def test_regular_human_name_unchanged():
    p = SimpleNamespace(user={"first_name": "Вова", "username": "pshenovich"}, telegram_user_id=1)
    assert usage_server._comment_author_from_principal(p) == ("1", "Вова", "pshenovich")


def test_name_from_orbita_profile_beats_empty_session():
    """Вошёл по почте без имени, имя указал позже — в документе и обсуждении имя из профиля."""
    from assistant.stores import note_members

    uid = 5_000_000_000_000_123
    note_members.set_custom_name(uid, "Андрей", "Мыздриков")
    p = SimpleNamespace(user={"id": uid, "email": "a@example.com"}, telegram_user_id=uid)
    assert usage_server._comment_author_from_principal(p) == (str(uid), "Андрей Мыздриков", None)


def test_custom_orbita_name_beats_telegram_name():
    from assistant.stores import note_members

    uid = 777001
    note_members.upsert_profile(uid, username="andrey", first_name="Andy")
    note_members.set_custom_name(uid, "Андрей")
    p = SimpleNamespace(user={"first_name": "Andy", "username": "andrey"}, telegram_user_id=uid)
    assert usage_server._comment_author_from_principal(p) == (str(uid), "Андрей", "andrey")
