"""Онбординг новых пользователей Telegram (Rich Markdown + inline-кнопки)."""

from __future__ import annotations

import asyncio
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from assistant.lib.telegram_markdown import prepare_summary_markdown
from assistant.lib.telegram_message import reply_formatted
from assistant.lib.telegram_rich import format_events_day_markdown
from assistant.stores import user_prefs

_CALLBACK_PREFIX = "ob:"

_MOCK_EVENTS = [
    {
        "start": "2026-06-20T11:30:00",
        "end": "2026-06-20T12:00:00",
        "summary": "Синк с командой",
    },
    {
        "start": "2026-06-20T15:00:00",
        "end": "2026-06-20T16:00:00",
        "summary": "Клиентский созвон",
    },
    {
        "start": "2026-06-20T16:30:00",
        "end": "2026-06-20T17:00:00",
        "summary": "1:1 с Анной",
    },
]

_EXAMPLE_PHRASES = (
    "Создай Zoom с командой завтра в 16:00\n"
    "Какие встречи завтра?\n"
    "Напомни в 18:00 позвонить клиенту"
)

_WELCOME_SHORT = (
    "Leo — встречи, календарь, напоминания в одном чате.\n"
    "Пишите в свободной форме или откройте «Ассистент» (≡).\n"
    "Интеграции: /calendar_auth, /zoom_auth, /telemost_auth, /yandex_disk_auth"
)


def onboarding_mode() -> str:
    return (os.getenv("TELEGRAM_ONBOARDING_MODE", "card") or "card").strip().lower()


def onboarding_slide_urls() -> list[str]:
    raw = (os.getenv("TELEGRAM_ONBOARDING_SLIDE_URLS", "") or "").strip()
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def _bot_mention(bot_username: str | None = None) -> str:
    u = (bot_username or os.getenv("TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
    return f"@{u}" if u else "@бот"


def welcome_card_markdown(*, bot_username: str | None = None) -> str:
    mention = _bot_mention(bot_username)
    return f"""## Leo — ассистент для встреч и календаря

Пишите **обычными фразами** — без команд.

| Что нужно | Пример фразы |
|:----------|:-------------|
| Встреча + Zoom | «Создай Zoom с командой завтра в 16:00» |
| Календарь | «Какие встречи завтра?» / «Свободные слоты в понедельник» |
| Контакты | «Встреча с Марией завтра в 15:00» — добавьте людей в мини-app: Профиль → Мои контакты |
| Напоминание | «Напомни в 18:00 позвонить клиенту» |
| Заметка | «Заметка: договорились о тарифе 600 мин» |
| Запись / саммари | Голос, файл или ссылка на запись |
| Поиск по архиву | «Что решили по тарифам на встрече с Марией?» |

- [ ] Google Calendar — `/calendar_auth` *(необязательно сразу)*
- [ ] Zoom — `/zoom_auth`
- [ ] Телемост — `/telemost_auth` *(только корпоративный тариф Яндекс 360 для бизнеса)*
- [ ] Яндекс Диск — `/yandex_disk_auth` *(записи встреч, транскрипции и саммари)*

<details>
<summary>Мини-app «Ассистент» (кнопка ≡ слева внизу)</summary>

Удобно, когда нужен обзор и настройки без переписки с ботом:

- **Актуальное** — встречи на выбранный день и напоминания
- **Сохранённое** — заметки, транскрипции и саммари из чата с Leo
- **Профиль** — подключение Calendar, Zoom и Яндекс Диска; **контакты** для встреч, календари, расходы

</details>

<details>
<summary>Голос и группы</summary>

- **Голосовые** — говорите задачу, Leo распознает
- **В группах** — через {mention} или ответ на сообщение бота
- **Отмена шага:** «отмена», «стоп» или /cancel

</details>"""


def welcome_slideshow_markdown(urls: list[str]) -> str:
    captions = [
        "Встречи — Zoom, запись, транскрипт и саммари",
        "Календарь — «встреча завтра в 15:00 с Иваном»",
        "Напоминания — «напомни завтра в 9:30»",
        "Архив — заметки, транскрипции и саммари",
        "Мини-app — встречи, сохранённое и интеграции",
    ]
    slides: list[str] = []
    for i, url in enumerate(urls[:5]):
        cap = captions[i] if i < len(captions) else f"Leo — слайд {i + 1}"
        slides.append(f'![]({url} "{cap}")')
    slideshow_body = "\n".join(slides)
    return f"""## Знакомство с Leo

<tg-slideshow>

{slideshow_body}

</tg-slideshow>

> Свайпните карусель ← →

1. **Встречи** — Zoom, запись, транскрипт, саммари на Яндекс Диск
2. **Календарь** — создание, слоты, напоминания за 15 мин
3. **Напоминания** — текстом, без отдельного приложения
4. **Архив** — поиск по заметкам, транскрипциям и саммари
5. **Мини-app** — актуальное на день, сохранённое, профиль и интеграции"""


def welcome_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Примеры фраз", callback_data=f"{_CALLBACK_PREFIX}examples")],
            [
                InlineKeyboardButton(
                    "Открыть мини-app",
                    web_app=WebAppInfo(url=webapp_url),
                )
            ],
            [InlineKeyboardButton("Понятно", callback_data=f"{_CALLBACK_PREFIX}done")],
        ]
    )


def demo_calendar_markdown() -> str:
    table = format_events_day_markdown(_MOCK_EVENTS, "Завтра")
    return f"### Пример ответа\n\nТак Leo отвечает на «какие встречи завтра»:\n\n{table}"


def demo_summary_markdown() -> str:
    body = prepare_summary_markdown(
        "## 📌 Кратко\n\nОбсуждали тарифы и лимиты минут для команды.",
        tasks=[
            {
                "assignee": "Мария",
                "task": "Подготовить слайды по тарифам",
                "deadline": "пятница",
                "completed": False,
            },
            {
                "assignee": "Иван",
                "task": "Согласовать лимиты с юристами",
                "deadline": "",
                "completed": True,
            },
        ],
        headline="Встреча по тарифам (пример)",
    )
    return f"### Пример саммари\n\n{body}"


def _welcome_body(*, bot_username: str | None = None) -> str:
    mode = onboarding_mode()
    urls = onboarding_slide_urls()
    if mode == "slideshow" and urls:
        return welcome_slideshow_markdown(urls)
    return welcome_card_markdown(bot_username=bot_username)


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
    if not force and user_prefs.onboarding_completed(user_id):
        await msg.reply_text(_WELCOME_SHORT)
        return

    bot_username: str | None = None
    try:
        bot_username = msg.get_bot().username
    except Exception:
        pass

    keyboard = welcome_keyboard(webapp_url)
    await reply_formatted(
        msg,
        _welcome_body(bot_username=bot_username),
        rich_markdown=True,
        reply_markup=keyboard,
    )

    await asyncio.sleep(0.5)
    await reply_formatted(msg, demo_calendar_markdown(), rich_markdown=True)
    await asyncio.sleep(0.3)
    await reply_formatted(msg, demo_summary_markdown(), rich_markdown=True)


async def handle_onboarding_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(_CALLBACK_PREFIX):
        return
    await query.answer()
    user = update.effective_user
    if not user:
        return

    action = query.data[len(_CALLBACK_PREFIX) :]
    chat_id = query.message.chat_id if query.message else None

    if action == "examples":
        if chat_id is not None:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    "Скопируйте и отправьте любую фразу:\n\n"
                    f"{_EXAMPLE_PHRASES}"
                ),
            )
        return

    if action == "done":
        user_prefs.mark_onboarding_completed(int(user.id))
        if chat_id is not None:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text="Готово! Пишите в свободной форме — Leo поймёт.",
            )
