# Дайджест чатов

Ежедневные отчёты по Telegram-группам, куда добавлен Leo.

## Включение у пользователя

1. **Профиль → Чаты** — включить тумблер «Дайджест» и отметить нужные чаты.
2. В tabbar появится раздел **Дайджест**.
3. Кнопка **Обновить** (иконка справа вверху) запускает разбор **вчерашнего** дня по выбранным чатам. Автозапуска в v1 нет.

## Privacy mode (BotFather)

Чтобы Leo видел всю переписку группы (не только mention):

1. `@BotFather` → Bot Settings → Group Privacy → **Turn off**.
2. Поведение ответов **не меняется**: отвечает только на @mention / reply / pending-диалог ([`group_gate`](../assistant/bot/group_gate.py)).
3. Пассивный ingest пишет текст в SQLite (`CHAT_DIGEST_DB_PATH`, по умолчанию `data/chat_digest.sqlite`).

Без privacy off ingest в группах будет неполным.

## Env

| Переменная | Смысл |
|---|---|
| `CHAT_DIGEST_INGEST_ENABLED` | `1` по умолчанию — лог сообщений |
| `CHAT_DIGEST_TZ` | TZ для «вчера» (default `Europe/Moscow`) |
| `OPENROUTER_MODEL_CHAT_DIGEST` | модель (default `google/gemini-2.5-flash` или `OPENROUTER_MODEL_BOARD`) |
| `CHAT_DIGEST_DEMO_CHAT_IDS` | fallback chat id для демо-сида |
| `CHAT_DIGEST_DB_PATH` | путь к sqlite |

## Расходы

Операция `chat_digest` («Дайджест чатов») пишется в `usage_events` и видна в дашборде расходов и **Профиль → Расходы**.

## Dev

При `MINIAPP_DEV_MODE=1`: в Профиль → Чаты кнопка «Создать демо-отчёт за вчера» (`POST /api/miniapp/digest/demo-seed`).
