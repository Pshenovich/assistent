"""Знакомство в Telegram: человек выбирает, что болит, и за минуту получает результат на своих данных.

Три сценария: «Голова забита делами» (день из одного сообщения → превью → «Добавить всё»),
«Много голосовых и созвонов» (пересланное голосовое или запись → суть и задачи),
«Тону в рабочих чатах» (бота добавляют в группу → дайджест и срочное).
После первого результата — одно сообщение «что дальше».
"""

from __future__ import annotations

import asyncio
import html
import os
import time
from datetime import datetime
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from assistant.stores import user_prefs

_CALLBACK_PREFIX = "ob:"
PICKS = {
    "day": "🗂 Голова забита делами",
    "talk": "🎙 Много голосовых и созвонов",
    "chats": "💬 Тону в рабочих чатах",
}
_CAPTURE_KEY = "ob_capture"
_PLAN_KEY = "ob_day_plan"
_CAPTURE_TTL_SEC = 30 * 60
# «Что дальше» и включение дайджеста — только пока выбор свежий.
_PICK_FRESH_SEC = 7 * 24 * 3600

WELCOME_NEW = (
    "<b>Привет, я Лео</b> 👋\n"
    "Держу встречи, задачи и договорённости под контролем — прямо здесь, в Телеграме.\n\n"
    "С чего начнём? Выберите, что сейчас мешает больше всего:"
)
WELCOME_RETURNING = (
    "С чего продолжим? Или просто напишите, что нужно: "
    "«встреча завтра в 15:00», «что решили по тарифам?». Всё, что умею, — /help"
)

DAY_PROMPT = (
    "Наговорите голосовым или напишите, что у вас сегодня и в ближайшие дни: "
    "встречи, дела, о чём не забыть. Как есть, одним сообщением — я разложу по полочкам.\n\n"
    "<i>Например: «завтра в 11 созвон с Аней, до пятницы отправить отчёт, "
    "напомни вечером позвонить в банк»</i>\n\n"
    "Сам ничего не создам — сначала покажу, что понял."
)
TALK_PROMPT = (
    "Перешлите сюда голосовое от коллеги или пришлите запись встречи — аудио или видео. "
    "Верну краткую суть и задачи по людям.\n\n"
    "Пересланное я только разбираю: в календарь и задачи ничего не попадёт, "
    "пока вы сами не возьмёте."
)
CHATS_PROMPT = (
    "Добавьте меня в рабочую группу — я буду читать молча и собирать главное: "
    "решения, вопросы к вам, срочное.\n\n"
    "• Сводка по чату — в Орбите, раздел «Дайджест»\n"
    "• Срочное, где ждут именно вас, — пришлю сюда сразу\n\n"
    "В группе я отвечаю, только если меня упомянуть или ответить на моё сообщение."
)

HELP_HTML = """<b>Что умеет Лео</b>
Пишите или наговорите обычными фразами — без команд.

📅 <b>Календарь</b> — «встреча с Марией завтра в 15:00», «какие встречи завтра?», «свободные слоты в понедельник»
✅ <b>Задачи и напоминания</b> — «до пятницы отправить отчёт», «напомни в 18:00 позвонить клиенту»
🎙 <b>Записи и голосовые</b> — перешлите голосовое или пришлите запись встречи: верну суть и задачи
🔎 <b>Поиск по архиву</b> — «что решили по тарифам на встрече с Марией?»
📝 <b>Документы</b> — «заметка: договорились о тарифе 600 мин»
💬 <b>Рабочие чаты</b> — добавьте меня в группу: сводка в «Дайджесте», срочное — сюда

<b>Подключения</b>
/calendar_auth — Google Календарь
/zoom_auth — Zoom (запись и саммари созвонов)
/telemost_auth — Телемост (Яндекс 360 для бизнеса)
/yandex_disk_auth — Яндекс Диск (записи и расшифровки)

/app — открыть Орбиту · /cancel — отменить текущий шаг
Не нашли, где что-то, — спросите меня: «где настроить уведомления?»"""


def _bot_username(bot: Bot | None) -> str:
    u = ""
    try:
        u = str(getattr(bot, "username", "") or "")
    except Exception:
        u = ""
    return (u or os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")


def pick_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"{_CALLBACK_PREFIX}pick:{key}")] for key, label in PICKS.items()]
    rows.append([InlineKeyboardButton("Открыть Орбиту", web_app=WebAppInfo(url=webapp_url))])
    return InlineKeyboardMarkup(rows)


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data=f"{_CALLBACK_PREFIX}cap:cancel")]])


def chats_keyboard(bot_username: str) -> InlineKeyboardMarkup | None:
    if not bot_username:
        return None
    url = f"https://t.me/{bot_username}?startgroup=orbita"
    return InlineKeyboardMarkup([[InlineKeyboardButton("Добавить в группу", url=url)]])


def _plan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Добавить всё", callback_data=f"{_CALLBACK_PREFIX}day:add")],
            [
                InlineKeyboardButton("Поправить", callback_data=f"{_CALLBACK_PREFIX}day:edit"),
                InlineKeyboardButton("Отмена", callback_data=f"{_CALLBACK_PREFIX}day:cancel"),
            ],
        ]
    )


def _track(user_id: int, event: str, **props: Any) -> None:
    from assistant.stores import analytics_store

    analytics_store.track(int(user_id), event, platform="tg_bot", **props)


def _maybe_mark_fresh(user_id: int) -> None:
    """Первый /start нового человека: в мини-приложении ему покажем интро и тур, даже если он успеет поработать в боте."""
    try:
        from assistant.onboarding import backfill, state

        if user_prefs.onboarding_completed(user_id) or user_prefs.onboarding_fresh(user_id):
            return
        if state.has_state(user_id) or backfill.has_activity(user_id):
            return
        user_prefs.mark_onboarding_fresh(user_id)
    except Exception as e:
        print(f"[onboarding] mark fresh failed uid={user_id} err={e!r}")


async def send_welcome(bot: Bot, chat_id: int, *, user_id: int, webapp_url: str, returning: bool = False) -> None:
    await bot.send_message(
        chat_id=int(chat_id),
        text=WELCOME_RETURNING if returning else WELCOME_NEW,
        parse_mode=ParseMode.HTML,
        reply_markup=pick_keyboard(webapp_url),
    )


async def send_start_onboarding(
    update: Update,
    *,
    user_id: int,
    webapp_url: str,
    force: bool = False,
) -> None:
    msg = update.message
    if not msg:
        return
    returning = not force and user_prefs.onboarding_completed(user_id)
    if not returning:
        _maybe_mark_fresh(user_id)
    await send_welcome(msg.get_bot(), msg.chat_id, user_id=user_id, webapp_url=webapp_url, returning=returning)


async def send_help(update: Update, *, webapp_url: str) -> None:
    if update.message:
        await update.message.reply_text(
            HELP_HTML,
            parse_mode=ParseMode.HTML,
            reply_markup=pick_keyboard(webapp_url),
            disable_web_page_preview=True,
        )


# --- Сценарий «Голова забита делами» ---


def arm_capture(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_CAPTURE_KEY] = {"kind": "day", "at": time.time()}


def clear_capture(context: ContextTypes.DEFAULT_TYPE) -> bool:
    ud = context.user_data
    had = bool(ud.pop(_CAPTURE_KEY, None))
    return bool(ud.pop(_PLAN_KEY, None)) or had


def capture_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    st = context.user_data.get(_CAPTURE_KEY) if context.user_data is not None else None
    if not isinstance(st, dict):
        return False
    if time.time() - float(st.get("at") or 0) > _CAPTURE_TTL_SEC:
        context.user_data.pop(_CAPTURE_KEY, None)
        return False
    return True


async def try_consume_capture(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Сообщение после «Голова забита делами» — разбор дня, а не команда. True — обработано."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat or chat.type != "private" or not text.strip():
        return False
    if not capture_active(context):
        return False
    context.user_data.pop(_CAPTURE_KEY, None)

    from assistant.bot import onboarding_day
    from assistant.nlu.llm import parse_day_plan
    from assistant.services.calendar import _tz_for

    uid = int(user.id)
    tz = _tz_for(uid)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    status = await msg.reply_text("Раскладываю по полочкам…")
    try:
        raw = await asyncio.to_thread(parse_day_plan, text, now=now)
    except Exception as e:
        print(f"[onboarding] parse_day_plan failed uid={uid} err={e!r}")
        raw = []
    plan = onboarding_day.build_plan(raw, now=now, tz=tz)
    if not plan:
        arm_capture(context)
        await status.edit_text(
            "Не нашёл здесь встреч и дел. Попробуйте ещё раз — например: "
            "«завтра в 11 созвон с Аней, до пятницы отправить отчёт».",
            reply_markup=_cancel_keyboard(),
        )
        return True
    context.user_data[_PLAN_KEY] = plan
    body = onboarding_day.plan_html(plan, today=now.date())
    await status.edit_text(
        f"<b>Вот что я понял</b>\n\n{body}\n\nДобавить в календарь?",
        parse_mode=ParseMode.HTML,
        reply_markup=_plan_keyboard(),
    )
    return True


async def _commit_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not query.message:
        return
    plan = context.user_data.pop(_PLAN_KEY, None)
    if not plan:
        await query.edit_message_text("Этот список уже неактуален — пришлите дела ещё раз: /start")
        return
    from assistant.bot import onboarding_day
    from assistant.services.calendar import _tz_for
    from assistant.skills import reminders as reminders_skill

    uid = int(user.id)
    chat_id = int(query.message.chat_id)
    tz = _tz_for(uid)
    now = datetime.now(tz)
    done, failed = await asyncio.to_thread(
        onboarding_day.commit_plan, uid, chat_id, plan, tz=tz, telegram_username=user.username
    )
    for it in done:
        if it.get("reminder_id"):
            reminders_skill.schedule_reminder_job(
                context,
                reminder_id=it["reminder_id"],
                chat_id=chat_id,
                user_id=uid,
                task=it["title"],
                when=datetime.fromisoformat(it["start"]),
                now=now,
            )
    if not done:
        await query.edit_message_text("Не получилось добавить — попробуйте чуть позже или напишите дела по одному.")
        return
    text = f"<b>Готово: {onboarding_day.counts_label(done)}</b>\n\n{onboarding_day.plan_html(done, today=now.date())}"
    if failed:
        names = ", ".join(html.escape(f["title"]) for f in failed)
        text += f"\n\nНе получилось добавить: {names}"
    await query.edit_message_text(text, parse_mode=ParseMode.HTML)
    await after_first_value(context.bot, chat_id, uid, "day")


def wants_summary(user_id: int) -> bool:
    """Выбрал «Много голосовых и созвонов» и ещё не получил первый результат — запись сразу в саммари."""
    try:
        if user_prefs.onboarding_pick(int(user_id), max_age_sec=_PICK_FRESH_SEC) != "talk":
            return False
        return not user_prefs.load_prefs(int(user_id)).get("onboarding_followup_at")
    except Exception:
        return False


# --- «Что дальше» после первого результата ---


def _followup(choice: str, user_id: int) -> str:
    if choice == "day":
        from assistant.services import calendar as cal_svc

        lines = ["<b>Что дальше</b>"]
        if not cal_svc.has_google(int(user_id)):
            lines.append("• Подключите Google Календарь — увижу весь ваш день и найду свободное время: /calendar_auth")
        lines.append("• Каждое утро соберу в «Фокусе» то, что нельзя упустить: просрочки, обещания, важное")
        lines.append("• Новые дела просто пишите или наговаривайте сюда")
        return "\n".join(lines)
    if choice == "talk":
        return (
            "<b>Что дальше</b>\n"
            "• Хотите саммари после каждого созвона без пересылок? Подключите Zoom — /zoom_auth "
            "(или Телемост — /telemost_auth), я сам запишу встречу\n"
            "• Все саммари и расшифровки — в Орбите, раздел «Сохранённое»; поиск находит по смыслу\n"
            "• Спросите меня: «что решили по тарифам?» — найду в записях"
        )
    return (
        "<b>Что дальше</b>\n"
        "• Добавьте меня и в другие рабочие группы — сводка соберётся по всем\n"
        "• Загляните в Орбиту: там ваш день, задачи и «Фокус»\n"
        "• Отключить чат можно в Профиль → Чаты"
    )


async def after_first_value(bot: Bot, chat_id: int, user_id: int, choice: str) -> None:
    """Первый результат по выбранному сценарию → событие и одно сообщение «что дальше»."""
    try:
        if user_prefs.onboarding_pick(int(user_id), max_age_sec=_PICK_FRESH_SEC) != choice:
            return
        if not user_prefs.claim_onboarding_followup(int(user_id)):
            return
        prefs = user_prefs.load_prefs(int(user_id))
        sec = int(time.time() - float(prefs.get("onboarding_pick_at") or time.time()))
        _track(int(user_id), "onboarding.first_value", choice=choice, sec_since_pick=sec)
        from assistant.lib.webapp_public import webapp_entry_url

        await asyncio.sleep(1.0)
        await bot.send_message(
            chat_id=int(chat_id),
            text=_followup(choice, int(user_id)),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Открыть Орбиту", web_app=WebAppInfo(url=webapp_entry_url()))]]
            ),
            disable_web_page_preview=True,
        )
    except Exception as e:
        print(f"[onboarding] followup failed uid={user_id} choice={choice} err={e!r}")


# --- Сценарий «Тону в рабочих чатах» ---


async def on_bot_added_to_group(bot: Bot, *, chat_id: int, chat_title: str, actor_id: int) -> None:
    """Бота добавили в группу человеком, выбравшим «чаты»: включаем ему дайджест этой группы и пишем в личку."""
    uid = int(actor_id)
    if user_prefs.onboarding_pick(uid, max_age_sec=_PICK_FRESH_SEC) != "chats":
        return
    try:
        ids = user_prefs.digest_chat_ids(uid)
        raw = user_prefs.load_prefs(uid).get("digest_chat_ids") or []
        if int(chat_id) not in ids:
            user_prefs.set_digest_chat_ids(uid, [*raw, int(chat_id)])
        user_prefs.set_digest_enabled(uid, True)
    except Exception as e:
        print(f"[onboarding] enable digest failed uid={uid} chat={chat_id} err={e!r}")
        return
    mention = _bot_username(bot)
    try:
        await bot.send_message(
            chat_id=int(chat_id),
            text=(
                "Привет, я Лео — ИИ-помощник. Читаю молча, а отвечаю, только если меня упомянуть"
                + (f" (@{mention})" if mention else "")
                + " или ответить на моё сообщение."
            ),
        )
    except Exception as e:
        print(f"[onboarding] group hello failed chat={chat_id} err={e!r}")
    title = html.escape(chat_title or "группа")
    try:
        await bot.send_message(
            chat_id=uid,
            text=(
                f"Подключил «{title}». Сводка по чату появится в Орбите → «Дайджест», "
                "как только в чате наберутся новые сообщения; срочное, где ждут вас, пришлю сюда."
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"[onboarding] group dm failed uid={uid} err={e!r}")
        return
    await after_first_value(bot, uid, uid, "chats")


# --- Кнопки ---


async def handle_onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(_CALLBACK_PREFIX):
        return
    await query.answer()
    user = update.effective_user
    if not user or not query.message:
        return
    uid = int(user.id)
    chat_id = int(query.message.chat_id)
    action = query.data[len(_CALLBACK_PREFIX) :]

    if action.startswith("pick:"):
        choice = action[len("pick:") :]
        if choice not in PICKS:
            return
        user_prefs.set_onboarding_pick(uid, choice)
        _track(uid, "onboarding.pick", choice=choice)
        clear_capture(context)
        if choice == "day":
            arm_capture(context)
            await context.bot.send_message(
                chat_id=chat_id, text=DAY_PROMPT, parse_mode=ParseMode.HTML, reply_markup=_cancel_keyboard()
            )
        elif choice == "talk":
            await context.bot.send_message(chat_id=chat_id, text=TALK_PROMPT)
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=CHATS_PROMPT, reply_markup=chats_keyboard(_bot_username(context.bot))
            )
        return

    if action == "cap:cancel":
        clear_capture(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=chat_id, text="Хорошо. Пишите, что нужно, — или /start, чтобы выбрать заново.")
        return

    if action == "day:add":
        await _commit_day(update, context)
        return
    if action == "day:edit":
        context.user_data.pop(_PLAN_KEY, None)
        arm_capture(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Пришлите список заново — целиком, с поправками.",
            reply_markup=_cancel_keyboard(),
        )
        return
    if action == "day:cancel":
        clear_capture(context)
        await query.edit_message_text("Ничего не добавил. Пишите, что нужно, — или /start, чтобы выбрать заново.")
        return
