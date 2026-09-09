# Локальный Telegram Bot API (большие файлы)

## Проблема

Через облачный `api.telegram.org` бот **не может скачать файлы больше 20 МБ** — это ограничение Telegram Bot API, не Leo.

Пользователи могут отправлять в Telegram файлы до ~2 ГБ, но бот их не получит целиком без локального сервера API.

## Что уже работает без настройки

- **Прямая ссылка** на аудио/видео (`URL_MAX_DOWNLOAD_MB`, по умолчанию 500 МБ) — скачивание с интернета на VPS, затем транскрипция.
- Сообщение об ошибке с подсказкой прислать ссылку, если файл из Telegram слишком большой.

## Решение на VPS: локальный `telegram-bot-api`

На том же сервере, где крутится бот (`/opt/assistant`), поднимается [официальный Bot API server](https://github.com/tdlib/telegram-bot-api). Он кэширует файлы на диск и отдаёт боту путь к файлу — **лимит 20 МБ снимается** (по умолчанию до ~2000 МБ, настраивается).

### 1. Сборка или Docker

Пример Docker (нужны `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` с [my.telegram.org](https://my.telegram.org)):

На prod это делает `scripts/setup_telegram_bot_api.sh` (systemd-сервис `telegram-bot-api`).

Вручную (образ `aiogram/telegram-bot-api` принимает **переменные окружения**):

```bash
docker run -d --name telegram-bot-api --restart unless-stopped \
  -p 127.0.0.1:8081:8081 \
  -v telegram-bot-api-data:/var/lib/telegram-bot-api \
  -e TELEGRAM_API_ID=YOUR_API_ID \
  -e TELEGRAM_API_HASH=YOUR_API_HASH \
  -e TELEGRAM_LOCAL=1 \
  aiogram/telegram-bot-api:latest
```

`YOUR_API_ID` / `YOUR_API_HASH` — с [my.telegram.org](https://my.telegram.org); на сервере уже могут быть как `TELEGRAM_ASSISTANT_API_ID` / `TELEGRAM_ASSISTANT_API_HASH`.

Порт `8081` только на localhost — наружу не публикуйте.

### 2. Переменные в `/opt/assistant/.env`

В `/opt/assistant/.env` нужны `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` с [my.telegram.org](https://my.telegram.org) (или `TELEGRAM_ASSISTANT_API_ID` / `TELEGRAM_ASSISTANT_API_HASH`). Без них локальный сервер не стартует, и бот остаётся на облачном лимите 20 МБ.

```env
TELEGRAM_API_ID=YOUR_API_ID
TELEGRAM_API_HASH=YOUR_API_HASH
TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081
TELEGRAM_BOT_MAX_DOWNLOAD_MB=500
TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB=500
```

Leo подставит `base_url` вида `http://127.0.0.1:8081/bot` и включит `local_mode`.

### 3. Перезапуск бота

```bash
systemctl restart assistant-bot
```

В логах должно появиться: `[bot] local Bot API: http://127.0.0.1:8081`.

## Лимиты после включения

| Источник | Лимит (по умолчанию) |
|----------|----------------------|
| Telegram через локальный API | min(`TELEGRAM_BOT_MAX_DOWNLOAD_MB`, `TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB`, `URL_MAX_DOWNLOAD_MB`) |
| Прямая HTTP-ссылка | `URL_MAX_DOWNLOAD_MB` (500) |
| Облачный Bot API без локального сервера | **20 МБ** (жёстко) |

## Альтернатива без локального API

Попросить пользователя загрузить файл в облако (S3, Google Drive с прямой ссылкой и т.д.) и отправить боту **прямую ссылку** + «транскрипция».
