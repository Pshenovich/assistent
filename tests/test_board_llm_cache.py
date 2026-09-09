from assistant.board.llm import _messages_payload


def test_anthropic_splits_stable_prefix_for_cache():
    stable = "ORIGINAL QUESTION\nhello"
    user = stable + "\n\nMODE\nDISCUSSION"
    msgs = _messages_payload(
        model="anthropic/claude-sonnet-4",
        system="role",
        user=user,
        cache_prefix=stable,
    )
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    user_blocks = msgs[1]["content"]
    assert user_blocks[0]["text"] == stable
    assert user_blocks[0]["cache_control"]["type"] == "ephemeral"
    assert "DISCUSSION" in user_blocks[1]["text"]
    assert "cache_control" not in user_blocks[1]


def test_non_anthropic_keeps_plain_strings():
    msgs = _messages_payload(
        model="openai/gpt-4o",
        system="s",
        user="u",
        cache_prefix="s",
    )
    assert msgs[0]["content"] == "s"
    assert msgs[1]["content"] == "u"


def test_anthropic_without_prefix_does_not_cache_volatile_user():
    msgs = _messages_payload(
        model="anthropic/claude-sonnet-4",
        system="orch",
        user="transcript changes every turn",
        cache_prefix="",
    )
    assert isinstance(msgs[1]["content"], str)
    assert msgs[1]["content"] == "transcript changes every turn"
