#!/usr/bin/env bash
# Установка локального telegram-bot-api на VPS (вариант A). Запускать на сервере или через deploy.
set -euo pipefail

ASSISTANT_ROOT="${ASSISTANT_ROOT:-/opt/assistant}"
ENV_FILE="${ASSISTANT_ROOT}/.env"

_ensure_env_var() {
  local key="$1"
  local val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Нет $ENV_FILE" >&2
  exit 1
fi

# Не source-им весь .env — значения со scope (cloud_api:disk.write) ломают bash.
_env_get() {
  local key="$1"
  local line val
  line="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  printf '%s' "$val"
}

TELEGRAM_API_ID="$(_env_get TELEGRAM_API_ID)"
TELEGRAM_API_HASH="$(_env_get TELEGRAM_API_HASH)"
TELEGRAM_ASSISTANT_API_ID="$(_env_get TELEGRAM_ASSISTANT_API_ID)"
TELEGRAM_ASSISTANT_API_HASH="$(_env_get TELEGRAM_ASSISTANT_API_HASH)"
TELEGRAM_BOT_API_IMAGE="$(_env_get TELEGRAM_BOT_API_IMAGE)"

if [[ -z "${TELEGRAM_API_ID:-}" ]]; then
  if [[ -n "${TELEGRAM_ASSISTANT_API_ID:-}" ]]; then
    _ensure_env_var "TELEGRAM_API_ID" "$TELEGRAM_ASSISTANT_API_ID"
    TELEGRAM_API_ID="$TELEGRAM_ASSISTANT_API_ID"
  fi
fi
if [[ -z "${TELEGRAM_API_HASH:-}" ]]; then
  if [[ -n "${TELEGRAM_ASSISTANT_API_HASH:-}" ]]; then
    _ensure_env_var "TELEGRAM_API_HASH" "$TELEGRAM_ASSISTANT_API_HASH"
    TELEGRAM_API_HASH="$TELEGRAM_ASSISTANT_API_HASH"
  fi
fi

if [[ -z "${TELEGRAM_API_ID:-}" || -z "${TELEGRAM_API_HASH:-}" ]]; then
  echo "Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH в $ENV_FILE (my.telegram.org)" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Установка Docker…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq docker.io
  systemctl enable docker
  systemctl start docker
fi

chmod +x "${ASSISTANT_ROOT}/deploy/run-telegram-bot-api.sh"

echo "==> Образ telegram-bot-api…"
docker pull "${TELEGRAM_BOT_API_IMAGE:-aiogram/telegram-bot-api:latest}"

_ensure_env_var "TELEGRAM_BOT_API_BASE_URL" "http://127.0.0.1:8081"
_ensure_env_var "TELEGRAM_BOT_API_DATA_DIR" "/opt/assistant/data/telegram-bot-api"
_ensure_env_var "TELEGRAM_BOT_MAX_DOWNLOAD_MB" "500"
_ensure_env_var "TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB" "500"
mkdir -p /opt/assistant/data/telegram-bot-api /opt/assistant/data/telegram-bot-api/api-tmp
chmod 1777 /opt/assistant/data/telegram-bot-api/api-tmp

cp -f "${ASSISTANT_ROOT}/deploy/telegram-bot-api.service" /etc/systemd/system/telegram-bot-api.service

# Бот стартует после локального API
if [[ -f /etc/systemd/system/assistant-bot.service ]]; then
  if ! grep -q 'telegram-bot-api.service' /etc/systemd/system/assistant-bot.service; then
    sed -i '/^\[Unit\]/a After=telegram-bot-api.service\nWants=telegram-bot-api.service' \
      /etc/systemd/system/assistant-bot.service
  fi
fi

systemctl daemon-reload
systemctl enable telegram-bot-api

docker stop telegram-bot-api 2>/dev/null || true
systemctl restart telegram-bot-api

echo "==> Ожидание порта 8081…"
for _ in $(seq 1 60); do
  if curl -sf --max-time 2 "http://127.0.0.1:8081/" >/dev/null 2>&1; then
    break
  fi
  if ss -ltn 2>/dev/null | grep -q ':8081 '; then
    break
  fi
  sleep 2
done

if ! ss -ltn 2>/dev/null | grep -q ':8081 '; then
  echo "telegram-bot-api не слушает 8081. Лог:" >&2
  journalctl -u telegram-bot-api -n 40 --no-pager >&2 || true
  exit 1
fi

cp -f "${ASSISTANT_ROOT}/deploy/assistant-bot.service" /etc/systemd/system/assistant-bot.service
systemctl daemon-reload
systemctl restart assistant-bot 2>/dev/null || true
sleep 2

echo "OK: локальный Bot API на http://127.0.0.1:8081"
systemctl is-active telegram-bot-api
systemctl is-active assistant-bot 2>/dev/null || {
  journalctl -u assistant-bot -n 20 --no-pager >&2 || true
  exit 1
}
