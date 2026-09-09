from assistant.lib.usage_store import extract_cached_tokens, insert_usage_event, events_for_date


def test_extract_cached_tokens_openai_details():
    assert (
        extract_cached_tokens(
            {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 80}}
        )
        == 80
    )


def test_extract_cached_tokens_anthropic_alias():
    assert extract_cached_tokens({"cache_read_input_tokens": 12}) == 12


def test_extract_cached_tokens_missing():
    assert extract_cached_tokens({}) is None
    assert extract_cached_tokens(None) is None


def test_insert_usage_event_persists_cached_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "usage.sqlite"))
    insert_usage_event(
        operation="ask",
        model="openai/gpt-4o-mini",
        generation_id="gen-1",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 40},
        },
        telegram_user_id="1",
    )
    from datetime import datetime, timezone

    day = datetime.now(timezone.utc).date().isoformat()
    rows = events_for_date(day)
    assert rows
    assert int(rows[-1].get("cached_tokens") or 0) == 40
    assert int(rows[-1].get("prompt_tokens") or 0) == 100

