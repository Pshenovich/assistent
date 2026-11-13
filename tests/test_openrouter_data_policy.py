"""Каждый запрос к OpenRouter — с provider.data_collection=deny (Google Limited Use); Comet это поле не получает."""

from __future__ import annotations

from assistant.integrations import openrouter_client as oc


def test_deny_added_and_existing_provider_kept(monkeypatch):
    monkeypatch.delenv("OPENROUTER_DATA_COLLECTION", raising=False)
    out = oc._with_data_policy({"model": "m", "provider": {"order": ["openai"]}})
    assert out["provider"] == {"order": ["openai"], "data_collection": "deny"}
    assert oc._with_data_policy({"model": "m"})["provider"] == {"data_collection": "deny"}


def test_allow_switch(monkeypatch):
    monkeypatch.setenv("OPENROUTER_DATA_COLLECTION", "allow")
    assert "provider" not in oc._with_data_policy({"model": "m"})


def test_comet_payload_drops_provider():
    out = oc._payload_for_comet({"model": "m", "messages": [], "provider": {"data_collection": "deny"}})
    assert "provider" not in out
