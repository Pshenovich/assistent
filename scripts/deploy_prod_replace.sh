#!/usr/bin/env bash
# Полная замена кода КомАссист на NewAssistant в /opt/assistant (данные и .env сохраняются).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ASSISTANT_ROOT="/opt/assistant"

_env_get() {
  local key="$1"
  local line val
  [[ -f "$ROOT/.env" ]] || return 0
  line="$(grep -E "^${key}=" "$ROOT/.env" 2>/dev/null | tail -1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val%$'\r'}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  printf '%s' "$val"
}

SSH_USER="$(_env_get SSH_USERNAME)"
SSH_PASS="$(_env_get SSH_PASSWORD)"
SSH_HOST="$(_env_get SSH_IP)"
SSH_PORT="$(_env_get DEPLOY_SSH_PORT)"
SSH_IDENTITY="$(_env_get SSH_IDENTITY_FILE)"
SSH_IDENTITY="${SSH_IDENTITY:-$HOME/.ssh/leo_server}"

# Прод миниаппа: DEPLOY_SSH_* (assistent.networ.ru), иначе старый origin SSH_*
if [[ -n "$(_env_get DEPLOY_SSH_HOST)" ]]; then
  SSH_HOST="$(_env_get DEPLOY_SSH_HOST)"
  SSH_USER="$(_env_get DEPLOY_SSH_USER)"
  SSH_USER="${SSH_USER:-root}"
  _edge_pass="$(_env_get DEPLOY_SSH_PASSWORD)"
  [[ -n "$_edge_pass" ]] && SSH_PASS="$_edge_pass"
fi

if [[ "$SSH_USER" == *@* ]]; then
  _host_part="${SSH_USER#*@}"
  SSH_USER="${SSH_USER%%@*}"
  [[ -z "$SSH_HOST" ]] && SSH_HOST="$_host_part"
fi
SSH_HOST="${SSH_HOST#*@}"
SSH_PORT="${SSH_PORT:-22}"

if [[ -z "$SSH_USER" || -z "$SSH_HOST" ]]; then
  echo "Нужны SSH_USERNAME и SSH_IP в .env" >&2
  exit 1
fi

SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ServerAliveInterval=15)
USE_SSH_KEY=0
if [[ -n "$SSH_IDENTITY" && -f "$SSH_IDENTITY" ]]; then
  SSH_OPTS+=(-i "$SSH_IDENTITY" -o IdentitiesOnly=yes -o BatchMode=yes)
  USE_SSH_KEY=1
elif [[ -n "$SSH_PASS" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "Установите sshpass или положите ключ в SSH_IDENTITY_FILE / ~/.ssh/leo_server" >&2
    exit 1
  fi
else
  echo "Нужен SSH_PASSWORD в .env или SSH-ключ (~/.ssh/leo_server / SSH_IDENTITY_FILE)" >&2
  exit 1
fi

if [[ "$USE_SSH_KEY" -eq 1 ]]; then
  echo "==> SSH: ключ ${SSH_IDENTITY}"
  SSH=(ssh "${SSH_OPTS[@]}")
  RSYNC=(rsync -az --delete -e "ssh ${SSH_OPTS[*]}")
  SCP=(scp -P "$SSH_PORT" -i "$SSH_IDENTITY" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes)
else
  echo "==> SSH: пароль (sshpass)"
  SSH=(sshpass -p "$SSH_PASS" ssh "${SSH_OPTS[@]}")
  RSYNC=(sshpass -p "$SSH_PASS" rsync -az --delete -e "ssh ${SSH_OPTS[*]}")
  SCP=(sshpass -p "$SSH_PASS" scp -P "$SSH_PORT" -o StrictHostKeyChecking=accept-new)
fi

_ssh_retry() {
  local attempt out
  for attempt in 1 2 3 4 5; do
    if out="$("${SSH[@]}" "${SSH_USER}@${SSH_HOST}" "$@" 2>&1)"; then
      [[ -n "$out" ]] && printf '%s\n' "$out"
      return 0
    fi
    echo "SSH попытка ${attempt}/5 не удалась: ${out}" >&2
    sleep $((attempt * 3))
  done
  return 1
}

_remote_bash() {
  local script="$1"
  local attempt out
  for attempt in 1 2 3 4 5; do
    if out="$("${SSH[@]}" "${SSH_USER}@${SSH_HOST}" bash -s 2>&1 <<< "$script")"; then
      [[ -n "$out" ]] && printf '%s\n' "$out"
      return 0
    fi
    echo "SSH попытка ${attempt}/5 не удалась: ${out}" >&2
    sleep $((attempt * 3))
  done
  return 1
}

SERVICES_STOPPED=0
_restore_services_on_fail() {
  if [[ "$SERVICES_STOPPED" -eq 1 ]]; then
    echo "==> Восстановление сервисов после сбоя деплоя" >&2
    "${SSH[@]}" "${SSH_USER}@${SSH_HOST}" \
      "systemctl start assistant-bot assistant-usage executive-board-bot 2>/dev/null || true" \
      2>/dev/null || true
  fi
}
trap _restore_services_on_fail EXIT

echo "==> Проверка SSH ${SSH_HOST}"
_ssh_retry "echo ssh_ok" >/dev/null

echo "==> Остановка сервисов на ${SSH_HOST}"
_remote_bash 'set -euo pipefail
systemctl stop assistant-bot assistant-usage executive-board-bot assistant-whatsapp-bridge 2>/dev/null || true
systemctl disable assistant-whatsapp-bridge 2>/dev/null || true'
SERVICES_STOPPED=1

echo "==> Синхронизация кода (без .env, .venv, пользовательских данных)"
"${RSYNC[@]}" \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.venv/' \
  --exclude '.git/' \
  --exclude 'google_user_tokens/' \
  --exclude 'google_oauth_pending/' \
  --exclude 'zoom_user_tokens/' \
  --exclude 'zoom_oauth_pending/' \
  --exclude 'todoist_user_tokens/' \
  --exclude 'todoist_oauth_pending/' \
  --exclude 'yandex_disk_user_tokens/' \
  --exclude 'yandex_disk_oauth_pending/' \
  --exclude 'bitrix_mcp_user_tokens/' \
  --exclude 'telemost_user_tokens/' \
  --exclude 'telemost_oauth_pending/' \
  --exclude 'contacts_user/' \
  --exclude 'contacts.json' \
  --exclude 'assistant/stores/contacts_user/' \
  --exclude 'assistant/stores/contacts.json' \
  --exclude 'reminders.json' \
  --exclude 'reminder_preferences.json' \
  --exclude 'calendar_pending.json' \
  --exclude 'usage.sqlite' \
  --exclude 'notes.sqlite' \
  --exclude 'allowed_telegram_access.json' \
  --exclude 'allowed_telegram_access_donatello.json' \
  --exclude 'access_requests.json' \
  --exclude 'access_requests_donatello.json' \
  --exclude 'data/allowed_telegram_access.json' \
  --exclude 'data/allowed_telegram_access_donatello.json' \
  --exclude 'data/access_requests.json' \
  --exclude 'data/access_requests_donatello.json' \
  --exclude 'data/telegram_registry.json' \
  --exclude 'telegram_registry.json' \
  --exclude 'data/meeting_reminders_sent.json' \
  --exclude 'data/meeting_recordings.sqlite' \
  --exclude 'data/board.sqlite' \
  --exclude 'data/paei_jobs.json' \
  --exclude 'data/knowledge_bases.sqlite' \
  --exclude 'data/users/' \
  --exclude 'data/telegram-bot-api/' \
  --exclude 'miniapp_billing_state.json' \
  --exclude 'booking_store.json' \
  --exclude 'booking_whatsapp_sessions/' \
  --exclude 'booking_whatsapp_user/' \
  --exclude 'booking_assistant_user/' \
  --exclude 'venues.json' \
  --exclude 'venues_user/' \
  --exclude 'telegram_assistant.session' \
  --exclude 'telegram_assistant.session-journal' \
  --exclude 'OAuth Client ID AssitentAI.json' \
  --exclude 'client_secret.json' \
  --exclude 'token.json' \
  --exclude '*.log' \
  --exclude 'ai_docs/' \
  --exclude '.cursor/' \
  --exclude 'webapp/node_modules/' \
  --exclude 'webapp/.npm-cache/' \
  --exclude 'webapp/dev-mock-data.js' \
  "$ROOT/" "${SSH_USER}@${SSH_HOST}:${ASSISTANT_ROOT}/"

echo "==> OAuth JSON (если есть локально)"
if [[ -f "$ROOT/OAuth Client ID AssitentAI.json" ]]; then
  "${SCP[@]}" \
    "$ROOT/OAuth Client ID AssitentAI.json" \
    "${SSH_USER}@${SSH_HOST}:${ASSISTANT_ROOT}/"
fi

echo "==> systemd + prod .env tweaks + pip"
REMOTE_SETUP_SCRIPT=$(cat <<'REMOTE_SETUP'
set -euo pipefail
cd /opt/assistant

# Удалить остатки старого КомАссист (rsync --delete не трогает excluded data)
rm -f booking_*.py assistant_whatsapp.py assistant_telegram.py AGENTS.md 2>/dev/null || true
rm -rf whatsapp_bridge booking_whatsapp_sessions booking_assistant_user ai_docs 2>/dev/null || true

# Prod: миниапп без dev-режима; прокси с Mac на VPS не нужен
if [[ -f .env ]]; then
  grep -q '^WEBAPP_PUBLIC_URL=' .env || echo 'WEBAPP_PUBLIC_URL=https://assistent.networ.ru' >> .env
  grep -q '^NOTES_DB_PATH=' .env || echo 'NOTES_DB_PATH=./notes.sqlite' >> .env
  grep -q '^TELEGRAM_ACCESS_GATE_ENABLED=' .env || echo 'TELEGRAM_ACCESS_GATE_ENABLED=1' >> .env
  grep -q '^MEETING_REMINDERS_ENABLED=' .env || echo 'MEETING_REMINDERS_ENABLED=1' >> .env
  grep -q '^MEETING_REMINDER_MINUTES_BEFORE=' .env || echo 'MEETING_REMINDER_MINUTES_BEFORE=15' >> .env
  grep -q '^MEETING_REMINDER_POLL_SEC=' .env || echo 'MEETING_REMINDER_POLL_SEC=60' >> .env
  grep -q '^MEETING_RECORDINGS_DB_PATH=' .env || echo 'MEETING_RECORDINGS_DB_PATH=./data/meeting_recordings.sqlite' >> .env
  if ! grep -q '^TELEGRAM_ACCESS_APPROVER_IDS=' .env; then
    dev_uid="$(grep '^MINIAPP_DEV_TELEGRAM_USER_ID=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
    if [[ -n "$dev_uid" ]]; then
      echo "TELEGRAM_ACCESS_APPROVER_IDS=${dev_uid}" >> .env
    fi
  fi
  sed -i 's/^MINIAPP_DEV_MODE=1/MINIAPP_DEV_MODE=0/' .env 2>/dev/null || true
  sed -i 's/^TELEGRAM_PROXY_URL=socks5:\\/\\/127\\.0\\.0\\.1/#TELEGRAM_PROXY_URL=/' .env 2>/dev/null || true
  # Локальный Bot API включаем только явно: если сервис не поднялся, бот должен
  # продолжать работать через api.telegram.org.
  if [[ "${ENABLE_LOCAL_TELEGRAM_BOT_API:-0}" == "1" ]]; then
    grep -q '^TELEGRAM_BOT_API_BASE_URL=' .env || echo 'TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081' >> .env
  fi
  grep -q '^TELEGRAM_BOT_MAX_DOWNLOAD_MB=' .env || echo 'TELEGRAM_BOT_MAX_DOWNLOAD_MB=500' >> .env
  grep -q '^TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB=' .env || echo 'TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB=500' >> .env
  grep -q '^BITRIX_MCP_URL=' .env || echo 'BITRIX_MCP_URL=https://mcp.bitrix24.tech/mcp/' >> .env
  grep -q '^OPENROUTER_MODEL_BOARD=' .env || echo 'OPENROUTER_MODEL_BOARD=google/gemini-2.5-flash' >> .env
  grep -q '^BOARD_COMPANY_SHARE_URL=' .env || echo 'BOARD_COMPANY_SHARE_URL=https://assistent.networ.ru/share/lYS_LYaj9MI3aIGHJib2Tyn1nNLAZ-eO' >> .env
fi

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%%.*}"
    minor="${ver#*.}"
    if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
      PYTHON_BIN="$candidate"
      break
    fi
    if [[ -z "$PYTHON_BIN" ]]; then
      PYTHON_BIN="$candidate"
    fi
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN=python3
fi
echo "Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1 || true))"

chmod +x deploy/run-telegram-bot-api.sh 2>/dev/null || true
chmod +x scripts/setup_vexa_meeting_bot.sh 2>/dev/null || true
if [[ -x scripts/setup_telegram_bot_api.sh ]]; then
  bash scripts/setup_telegram_bot_api.sh || {
    echo "WARN: setup_telegram_bot_api.sh не завершился — проверьте TELEGRAM_API_ID на сервере" >&2
  }
fi

cp -f deploy/assistant-bot.service /etc/systemd/system/assistant-bot.service
cp -f deploy/assistant-usage.service /etc/systemd/system/assistant-usage.service
cp -f deploy/executive-board-bot.service /etc/systemd/system/executive-board-bot.service
systemctl daemon-reload

if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
./.venv/bin/pip install -q -U pip
./.venv/bin/pip install -q -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update -qq 2>/dev/null || true
  apt-get install -y -qq ffmpeg 2>/dev/null || {
    echo "WARN: ffmpeg не установлен — запись webm-аудио из miniapp не будет транскрибироваться" >&2
  }
fi

# Шрифт для PDF (кириллица)
mkdir -p assistant/assets/fonts
if [[ ! -f assistant/assets/fonts/DejaVuSans.ttf ]]; then
  apt-get install -y -qq fonts-dejavu-core 2>/dev/null || true
  for f in \
    /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
    /usr/share/fonts/dejavu/DejaVuSans.ttf \
    /usr/share/fonts/TTF/DejaVuSans.ttf; do
    if [[ -f "$f" ]]; then
      cp -f "$f" assistant/assets/fonts/DejaVuSans.ttf
      break
    fi
  done
fi
if [[ -f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf ]] \
  && [[ ! -f assistant/assets/fonts/DejaVuSans-Bold.ttf ]]; then
  cp -f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf assistant/assets/fonts/ 2>/dev/null || true
fi

./.venv/bin/python -c "from assistant.bot.main import build_application; import usage_server; print('import ok')"

echo "==> Восстановление контактов из legacy-путей"
./.venv/bin/python scripts/restore_contacts.py || true

# nginx: раньше смотрел на 8766, uvicorn — 8080
if [[ -f /etc/nginx/sites-available/assistant.obuchat.me ]]; then
  sed -i 's|127.0.0.1:8766|127.0.0.1:8080|g' /etc/nginx/sites-available/assistant.obuchat.me
  nginx -t && systemctl reload nginx
fi

systemctl enable assistant-bot assistant-usage executive-board-bot
systemctl restart assistant-bot assistant-usage executive-board-bot
sleep 2
systemctl is-active assistant-bot assistant-usage executive-board-bot

if [[ -x scripts/setup_vexa_meeting_bot.sh ]]; then
  echo "==> Vexa meeting bot"
  bash scripts/setup_vexa_meeting_bot.sh || {
    echo "WARN: setup_vexa_meeting_bot.sh не завершился — проверьте docker logs vexa" >&2
  }
  systemctl restart assistant-bot assistant-usage executive-board-bot 2>/dev/null || true
fi
REMOTE_SETUP
)
_remote_bash "$REMOTE_SETUP_SCRIPT"

SERVICES_STOPPED=0
trap - EXIT

echo "Готово: https://assistent.networ.ru/webapp/"
