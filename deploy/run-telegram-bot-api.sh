#!/usr/bin/env bash
# Запуск локального telegram-bot-api (Docker). Читает API id/hash из /opt/assistant/.env
set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/assistant/.env}"

# Не делаем `source .env`: значения вроде cloud_api:disk.write ломают bash
# (interpreтируются как команды) и валят сервис с exit 127.
_env_get() {
  local key="$1"
  local line val
  line="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val%"${val##*[![:space:]]}"}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  printf '%s' "$val"
}

if [[ -f "$ENV_FILE" ]]; then
  TELEGRAM_API_ID="$(_env_get TELEGRAM_API_ID)"
  TELEGRAM_API_HASH="$(_env_get TELEGRAM_API_HASH)"
  TELEGRAM_ASSISTANT_API_ID="$(_env_get TELEGRAM_ASSISTANT_API_ID)"
  TELEGRAM_ASSISTANT_API_HASH="$(_env_get TELEGRAM_ASSISTANT_API_HASH)"
  TELEGRAM_BOT_API_IMAGE="$(_env_get TELEGRAM_BOT_API_IMAGE)"
  TELEGRAM_BOT_API_PORT="$(_env_get TELEGRAM_BOT_API_PORT)"
  TELEGRAM_BOT_API_DATA_DIR="$(_env_get TELEGRAM_BOT_API_DATA_DIR)"
fi

API_ID="${TELEGRAM_API_ID:-${TELEGRAM_ASSISTANT_API_ID:-}}"
API_HASH="${TELEGRAM_API_HASH:-${TELEGRAM_ASSISTANT_API_HASH:-}}"

if [[ -z "$API_ID" || -z "$API_HASH" ]]; then
  echo "Нужны TELEGRAM_API_ID и TELEGRAM_API_HASH (или TELEGRAM_ASSISTANT_API_*) в $ENV_FILE" >&2
  exit 1
fi

IMAGE="${TELEGRAM_BOT_API_IMAGE:-aiogram/telegram-bot-api:latest}"
PORT="${TELEGRAM_BOT_API_PORT:-8081}"
DATA_DIR="${TELEGRAM_BOT_API_DATA_DIR:-/opt/assistant/data/telegram-bot-api}"
mkdir -p "$DATA_DIR" "${DATA_DIR}/api-tmp"
# Контейнер пишет в /tmp/telegram-bot-api от пользователя telegram-bot-api
chmod 1777 "${DATA_DIR}/api-tmp"

exec docker run --rm --name telegram-bot-api \
  -p "127.0.0.1:${PORT}:8081" \
  -v "${DATA_DIR}:/var/lib/telegram-bot-api" \
  -v "${DATA_DIR}/api-tmp:/tmp/telegram-bot-api" \
  -e TELEGRAM_API_ID="$API_ID" \
  -e TELEGRAM_API_HASH="$API_HASH" \
  -e TELEGRAM_LOCAL=1 \
  "$IMAGE"
