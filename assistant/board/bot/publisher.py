"""Публикация реплик совета в Telegram."""

from __future__ import annotations

import asyncio
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application

from assistant.board.renderer import agent_delay_sec
from assistant.lib.telegram_html import split_telegram_html


class TelegramPublisher:
    def __init__(self, application: Application) -> None:
        self.app = application
        self._status: dict[str, tuple[int, str]] = {}

    def _thread_kwargs(self, meeting: dict[str, Any]) -> dict[str, Any]:
        tid = meeting.get("forum_topic_id") or meeting.get("thread_id")
        if tid:
            return {"message_thread_id": int(tid)}
        return {}

    async def before_agent(self, meeting: dict[str, Any], agent: str) -> None:
        del agent
        chat_id = int(meeting.get("chat_id") or 0)
        if not chat_id:
            return
        try:
            await self.app.bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.TYPING,
                **self._thread_kwargs(meeting),
            )
        except Exception:
            pass
        await asyncio.sleep(agent_delay_sec())

    async def update_status(self, meeting: dict[str, Any], text: str) -> None:
        chat_id = int(meeting.get("chat_id") or 0)
        meeting_id = str(meeting.get("id") or "")
        body = (text or "").strip()
        if not chat_id or not meeting_id or not body:
            return
        prev = self._status.get(meeting_id)
        if prev:
            msg_id, old = prev
            if old == body:
                return
            try:
                await self.app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=body,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                self._status[meeting_id] = (msg_id, body)
                return
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    self._status[meeting_id] = (msg_id, body)
                    return
            except Exception:
                pass
        try:
            sent = await self.app.bot.send_message(
                chat_id=chat_id,
                text=body,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                **self._thread_kwargs(meeting),
            )
            self._status[meeting_id] = (int(sent.message_id), body)
        except Exception as e:
            print(f"[board.publish] status_fail err={e!r}")

    async def clear_status(self, meeting: dict[str, Any]) -> None:
        chat_id = int(meeting.get("chat_id") or 0)
        meeting_id = str(meeting.get("id") or "")
        prev = self._status.pop(meeting_id, None)
        if not prev or not chat_id:
            return
        msg_id, _ = prev
        try:
            await self.app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

    async def publish_text(
        self,
        meeting: dict[str, Any],
        text: str,
        *,
        markup: Any = None,
    ) -> int | None:
        chat_id = int(meeting.get("chat_id") or 0)
        if not chat_id:
            return None
        kb = None
        if markup:
            rows = []
            for row in markup:
                buttons = [
                    InlineKeyboardButton(b["text"], callback_data=b["callback_data"])
                    for b in row
                ]
                rows.append(buttons)
            kb = InlineKeyboardMarkup(rows)
        chunks = split_telegram_html(text, 3800) or [text]
        last_id: int | None = None
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            try:
                sent = await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=kb if is_last else None,
                    **self._thread_kwargs(meeting),
                )
                last_id = int(sent.message_id)
            except Exception as e:
                print(f"[board.publish] html_fail err={e!r}")
                sent = await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    disable_web_page_preview=True,
                    reply_markup=kb if is_last else None,
                    **self._thread_kwargs(meeting),
                )
                last_id = int(sent.message_id)
        return last_id
