import asyncio
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest

from assistant.lib.telegram_status import delivery_kwargs, reply_has_media, set_status


def test_delivery_kwargs_includes_topic_thread():
    msg = MagicMock()
    msg.message_thread_id = 77
    assert delivery_kwargs(msg) == {"message_thread_id": 77}


def test_delivery_kwargs_empty_without_topic():
    msg = MagicMock()
    msg.message_thread_id = None
    assert delivery_kwargs(msg) == {}


def test_reply_has_media_audio():
    msg = MagicMock()
    msg.voice = None
    msg.audio = object()
    msg.video = None
    msg.document = None
    msg.video_note = None
    assert reply_has_media(msg) is True


def test_set_status_unchanged_text_does_not_duplicate():
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock(
        side_effect=BadRequest("Message is not modified")
    )
    anchor = MagicMock()
    anchor.reply_text = AsyncMock()

    out = asyncio.run(
        set_status(status_msg, "Скачиваю файл…", anchor=anchor)
    )

    assert out is status_msg
    anchor.reply_text.assert_not_awaited()
