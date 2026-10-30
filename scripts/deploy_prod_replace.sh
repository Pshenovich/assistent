#!/usr/bin/env bash
# Полная замена кода КомАссист на NewAssistant в /opt/assistant (данные и .env сохраняются).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Выкатываем только закоммиченное и проверенное: скрипт увозит рабочий каталог
# целиком, и незакоммиченные правки параллельных сессий уезжали на прод без проверки.
# DEPLOY_ALLOW_DIRTY=1 / DEPLOY_SKIP_TESTS=1 — обход только на крайний случай.
if [[ "${DEPLOY_ALLOW_DIRTY:-0}" != "1" ]] && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  _dirty="$(git -C "$ROOT" status --porcelain --untracked-files=normal | grep -v ' \.claude/$' || true)"
  if [[ -n "$_dirty" ]]; then
    echo "Выкатка остановлена: в рабочем каталоге есть незакоммиченные изменения:" >&2
    echo "$_dirty" | head -20 >&2
    echo "Закоммитьте или уберите их (DEPLOY_ALLOW_DIRTY=1 — обход)." >&2
    exit 1
  fi
fi
if [[ "${DEPLOY_SKIP_TESTS:-0}" != "1" ]]; then
  _py="$ROOT/.venv/bin/python"
  [[ -x "$_py" ]] || _py="python3"
  echo "==> Тесты перед выкаткой"
  if ! "$_py" -m pytest "$ROOT/tests" -q -x -p no:cacheprovider >/tmp/leo-deploy-tests.log 2>&1; then
    tail -30 /tmp/leo-deploy-tests.log >&2
    echo "Выкатка остановлена: тесты не прошли (DEPLOY_SKIP_TESTS=1 — обход)." >&2
    exit 1
  fi
  tail -1 /tmp/leo-deploy-tests.log
fi

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

# Прод (assistent.networ.ru) — только DEPLOY_SSH_*; старый сервер больше не используется.
SSH_HOST="$(_env_get DEPLOY_SSH_HOST)"
SSH_USER="$(_env_get DEPLOY_SSH_USER)"
SSH_USER="${SSH_USER:-root}"
SSH_PASS="$(_env_get DEPLOY_SSH_PASSWORD)"
SSH_PORT="$(_env_get DEPLOY_SSH_PORT)"
SSH_IDENTITY="$(_env_get SSH_IDENTITY_FILE)"
SSH_IDENTITY="${SSH_IDENTITY:-$HOME/.ssh/leo_server}"

if [[ "$SSH_USER" == *@* ]]; then
  _host_part="${SSH_USER#*@}"
  SSH_USER="${SSH_USER%%@*}"
  [[ -z "$SSH_HOST" ]] && SSH_HOST="$_host_part"
fi
SSH_HOST="${SSH_HOST#*@}"
SSH_PORT="${SSH_PORT:-22}"

if [[ -z "$SSH_USER" || -z "$SSH_HOST" ]]; then
  echo "Нужен DEPLOY_SSH_HOST в .env" >&2
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
  echo "Нужен DEPLOY_SSH_PASSWORD в .env или SSH-ключ (~/.ssh/leo_server / SSH_IDENTITY_FILE)" >&2
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

STAGE_ROOT="/opt/assistant.stage"
DEPLOY_DIR="/root/leo-deploy"
EXCLUDES_FILE="scripts/deploy_rsync_excludes.txt"

# Записать файл на сервер (с повторами).
_remote_write() {
  local path="$1" content="$2" attempt
  for attempt in 1 2 3 4 5; do
    if "${SSH[@]}" "${SSH_USER}@${SSH_HOST}" "cat > '$path'" <<< "$content"; then
      return 0
    fi
    echo "SSH попытка ${attempt}/5 (запись ${path}) не удалась" >&2
    sleep $((attempt * 3))
  done
  return 1
}

echo "==> Проверка SSH ${SSH_HOST}"
_ssh_retry "echo ssh_ok" >/dev/null

# Шаг 1. Код — в промежуточный каталог; бот и сайт работают. Обрыв SSH здесь прод не трогает:
# выкатка просто останавливается, на сервере остаётся прежняя версия.
echo "==> Синхронизация кода в ${STAGE_ROOT} (сервисы работают; без .env, .venv, данных)"
_ssh_retry "mkdir -p '${STAGE_ROOT}' '${DEPLOY_DIR}'" >/dev/null
_rsync_ok=0
for _attempt in 1 2 3 4; do
  if "${RSYNC[@]}" --partial --exclude-from="$ROOT/$EXCLUDES_FILE" \
    "$ROOT/" "${SSH_USER}@${SSH_HOST}:${STAGE_ROOT}/"; then
    _rsync_ok=1
    break
  fi
  echo "rsync попытка ${_attempt}/4 не удалась — повтор (прод не тронут)" >&2
  sleep $((_attempt * 10))
done
if [[ "$_rsync_ok" -ne 1 ]]; then
  echo "Код не скопирован: связь с сервером нестабильна. Прод работает на прежней версии — запустите выкатку ещё раз." >&2
  exit 1
fi

echo "==> OAuth JSON (если есть локально)"
if [[ -f "$ROOT/OAuth Client ID AssitentAI.json" ]]; then
  "${SCP[@]}" \
    "$ROOT/OAuth Client ID AssitentAI.json" \
    "${SSH_USER}@${SSH_HOST}:${ASSISTANT_ROOT}/"
fi

echo "==> USAGE_DASHBOARD_TOKEN на сервере"
python3 - <<'PY'
from pathlib import Path
p = Path(".env")
if not p.is_file():
    raise SystemExit(0)
text = p.read_text()
if "USAGE_DASHBOARD_TOKEN=" in text and not any(
    line.startswith("USAGE_DASHBOARD_TOKEN=") for line in text.splitlines()
):
    p.write_text(text.replace("USAGE_DASHBOARD_TOKEN=", "\nUSAGE_DASHBOARD_TOKEN=", 1))
PY
_DASH_SYNC_FILE="$(mktemp)"
printf '%s' "$(_env_get USAGE_DASHBOARD_TOKEN)" > "$_DASH_SYNC_FILE"
"${SCP[@]}" "$_DASH_SYNC_FILE" "${SSH_USER}@${SSH_HOST}:${ASSISTANT_ROOT}/.dash_token_sync"
rm -f "$_DASH_SYNC_FILE"
_ssh_retry "python3 /opt/assistant/scripts/sync_dashboard_token.py"

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
  # Локальный Bot API для файлов >20 МБ. Включаем, если есть api_id/hash или явный флаг.
  has_tg_api_id=0
  grep -qE '^TELEGRAM_API_ID=.+' .env && has_tg_api_id=1
  grep -qE '^TELEGRAM_ASSISTANT_API_ID=.+' .env && has_tg_api_id=1
  if [[ "${ENABLE_LOCAL_TELEGRAM_BOT_API:-}" == "1" || "$has_tg_api_id" -eq 1 ]]; then
    grep -q '^TELEGRAM_BOT_API_BASE_URL=' .env || echo 'TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081' >> .env
  fi
  if grep -qE '^TELEGRAM_BOT_MAX_DOWNLOAD_MB=(20|50)$' .env; then
    sed -i 's/^TELEGRAM_BOT_MAX_DOWNLOAD_MB=.*/TELEGRAM_BOT_MAX_DOWNLOAD_MB=500/' .env
  fi
  grep -q '^TELEGRAM_BOT_MAX_DOWNLOAD_MB=' .env || echo 'TELEGRAM_BOT_MAX_DOWNLOAD_MB=500' >> .env
  grep -q '^TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB=' .env || echo 'TELEGRAM_LOCAL_BOT_API_MAX_DOWNLOAD_MB=500' >> .env
  grep -q '^BITRIX_MCP_URL=' .env || echo 'BITRIX_MCP_URL=https://mcp.bitrix24.tech/mcp/' >> .env
  grep -q '^OPENROUTER_MODEL_BOARD=' .env || echo 'OPENROUTER_MODEL_BOARD=google/gemini-2.5-flash' >> .env
  grep -q '^USAGE_DASHBOARD_URL_PREFIX=' .env || echo 'USAGE_DASHBOARD_URL_PREFIX=/dashboard' >> .env
  # Ошибки бота и веба в Sentry (проект orbita). DSN не секрет — это адрес приёма событий.
  grep -q '^SENTRY_DSN=' .env || echo 'SENTRY_DSN=https://8701c2c70e3ddadcb106150aac9c264e@o4512332678430720.ingest.de.sentry.io/4512332863570000' >> .env
  if grep -q '^USAGE_DASHBOARD_URL_PREFIX=' .env; then
    sed -i 's|^USAGE_DASHBOARD_URL_PREFIX=.*|USAGE_DASHBOARD_URL_PREFIX=/dashboard|' .env
  fi
  grep -q '^USAGE_DASHBOARD_FORCE_AUTH=' .env || echo 'USAGE_DASHBOARD_FORCE_AUTH=1' >> .env
  python3 - <<'PY'
from pathlib import Path
p = Path(".env")
if not p.is_file():
    raise SystemExit(0)
text = p.read_text()
# USAGE_DASHBOARD_TOKEN мог приклеиться к предыдущей строке без перевода.
if "USAGE_DASHBOARD_TOKEN=" in text and not any(
    line.startswith("USAGE_DASHBOARD_TOKEN=") for line in text.splitlines()
):
    text = text.replace("USAGE_DASHBOARD_TOKEN=", "\nUSAGE_DASHBOARD_TOKEN=", 1)
    p.write_text(text)
    print("fixed glued USAGE_DASHBOARD_TOKEN line")
if not any(line.startswith("USAGE_DASHBOARD_TOKEN=") for line in p.read_text().splitlines()):
    import secrets
    p.write_text(p.read_text().rstrip() + "\nUSAGE_DASHBOARD_TOKEN=" + secrets.token_urlsafe(32) + "\n")
    print("generated USAGE_DASHBOARD_TOKEN")
PY
  if grep -qE '^OPENROUTER_MODEL_BOARD_AUX=' .env; then
    true
  else
    echo 'OPENROUTER_MODEL_BOARD_AUX=google/gemini-2.5-flash' >> .env
  fi
  grep -q '^BOARD_MAX_ROUNDS_LOW=' .env || echo 'BOARD_MAX_ROUNDS_LOW=2' >> .env
  grep -q '^BOARD_AGENT_MAX_TOKENS=' .env || echo 'BOARD_AGENT_MAX_TOKENS=1200' >> .env
  grep -q '^BOARD_CHAIR_MAX_TOKENS=' .env || echo 'BOARD_CHAIR_MAX_TOKENS=2500' >> .env
  grep -q '^BOARD_AUX_MAX_TOKENS=' .env || echo 'BOARD_AUX_MAX_TOKENS=800' >> .env
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
# B6: эмбеддинги — сервис assistant-embed работает, только если включён в .env
cp -f deploy/assistant-embed.service /etc/systemd/system/assistant-embed.service
systemctl daemon-reload
if grep -qE '^SEMANTIC_SEARCH_ENABLED=(1|true|yes|on)' .env 2>/dev/null; then
  ./.venv/bin/pip install -q -r requirements-embed.txt || echo "WARN: requirements-embed.txt не установились" >&2
  systemctl enable assistant-embed >/dev/null 2>&1 || true
  systemctl restart assistant-embed || echo "WARN: assistant-embed не стартовал" >&2
else
  systemctl disable --now assistant-embed >/dev/null 2>&1 || true
fi

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

python3 - <<'PY'
from pathlib import Path
p = Path("/etc/nginx/sites-available/assistent.networ.ru")
if not p.is_file():
    raise SystemExit(0)
t = p.read_text()
if "gzip on;" in t:
    print("nginx gzip: already on")
    raise SystemExit(0)
needle = "client_max_body_size 500m;"
gzip = (
    "client_max_body_size 500m;\n\n"
    "    gzip on;\n"
    "    gzip_vary on;\n"
    "    gzip_min_length 256;\n"
    "    gzip_comp_level 5;\n"
    "    gzip_types text/plain text/css application/javascript application/json application/xml image/svg+xml;"
)
if needle in t:
    p.write_text(t.replace(needle, gzip))
    print("nginx gzip: inserted")
else:
    print("nginx gzip: skip (no client_max_body_size)")
PY

# nginx: WebSocket живых обновлений (/api/miniapp/ws) и закрытый снаружи /internal/.
python3 - <<'PY'
import re
import shutil
import subprocess
from pathlib import Path
p = Path("/etc/nginx/sites-available/assistent.networ.ru")
if not p.is_file():
    raise SystemExit(0)
t = p.read_text()
if "/api/miniapp/ws" in t:
    print("nginx realtime: already on")
    raise SystemExit(0)
block = (
    "    location = /api/miniapp/ws {\n"
    "        proxy_pass http://127.0.0.1:8080;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header Upgrade $http_upgrade;\n"
    "        proxy_set_header Connection \"upgrade\";\n"
    "        proxy_set_header Host $host;\n"
    "        proxy_set_header X-Real-IP $remote_addr;\n"
    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
    "        proxy_set_header X-Forwarded-Host $host;\n"
    "        proxy_read_timeout 3600s;\n"
    "        proxy_send_timeout 3600s;\n"
    "        proxy_buffering off;\n"
    "    }\n\n"
    "    location /internal/ {\n"
    "        return 404;\n"
    "    }\n\n"
)
pat = re.compile(r"^(\s*)location / \{\s*\n\s*proxy_pass http://127\.0\.0\.1:8080;", re.M)
new, n = pat.subn(lambda m: block + m.group(0).lstrip("\n"), t)
if not n:
    print("nginx realtime: skip (no location / → :8080)")
    raise SystemExit(0)
backup = p.with_name(p.name + ".bak-realtime")
shutil.copyfile(p, backup)
p.write_text(new)
if subprocess.run(["nginx", "-t"]).returncode != 0:
    shutil.copyfile(backup, p)
    print("nginx realtime: nginx -t failed, restored")
else:
    print(f"nginx realtime: inserted x{n}")
PY

# nginx: ограничение частоты запросов с одного IP. Пока «пробный» режим
# (limit_req_dry_run): nginx никого не режет, только пишет в error.log
# «limiting requests, dry run» — по нему подбираем пороги, потом включаем.
python3 - <<'PY'
import re
import shutil
import subprocess
from pathlib import Path
p = Path("/etc/nginx/sites-available/assistent.networ.ru")
if not p.is_file():
    raise SystemExit(0)
t = p.read_text()
if "zone=leo_rl_all" in t:
    print("nginx rate limit: already on")
    raise SystemExit(0)
zones = (
    "# Ограничение частоты с одного IP (leo_rl_*): общее щедрое — в офисе много людей за одним IP.\n"
    "limit_req_zone $binary_remote_addr zone=leo_rl_all:10m rate=30r/s;\n"
    "limit_req_zone $binary_remote_addr zone=leo_rl_login:10m rate=10r/m;\n\n"
)
login = (
    "    # Вход по коду: перебор кодов.\n"
    "    location ^~ /api/miniapp/auth/login-code {\n"
    "        limit_req zone=leo_rl_login burst=5 nodelay;\n"
    "        limit_req_dry_run on;\n"
    "        limit_req_status 429;\n"
    "        proxy_pass http://127.0.0.1:8080;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header Host $host;\n"
    "        proxy_set_header X-Real-IP $remote_addr;\n"
    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
    "        proxy_set_header X-Forwarded-Host $host;\n"
    "    }\n\n"
)
pat = re.compile(r"^(\s*)location / \{\s*\n(\s*)proxy_pass http://127\.0\.0\.1:8080;", re.M)


def add_limits(m):
    ind = m.group(2)
    return (
        login + m.group(0).lstrip("\n") + "\n"
        f"{ind}limit_req zone=leo_rl_all burst=100 nodelay;\n"
        f"{ind}limit_req_dry_run on;\n"
        f"{ind}limit_req_status 429;"
    )


new, n = pat.subn(add_limits, t)
if not n or "server {" not in new:
    print("nginx rate limit: skip (no location / → :8080)")
    raise SystemExit(0)
new = new.replace("server {", zones + "server {", 1)
backup = p.with_name(p.name + ".bak-ratelimit")
shutil.copyfile(p, backup)
p.write_text(new)
if subprocess.run(["nginx", "-t"]).returncode != 0:
    shutil.copyfile(backup, p)
    print("nginx rate limit: nginx -t failed, restored")
else:
    print(f"nginx rate limit: inserted x{n} (dry run)")
PY

# nginx: пробный режим отработал (28–30.10 — ни одного срабатывания), ограничение включено.
python3 - <<'PY'
import shutil
import subprocess
from pathlib import Path
p = Path("/etc/nginx/sites-available/assistent.networ.ru")
if not p.is_file():
    raise SystemExit(0)
t = p.read_text()
lines = t.splitlines(keepends=True)
kept = [ln for ln in lines if ln.strip() != "limit_req_dry_run on;"]
if len(kept) == len(lines):
    print("nginx rate limit: enforced already")
    raise SystemExit(0)
backup = p.with_name(p.name + ".bak-ratelimit-enforce")
shutil.copyfile(p, backup)
p.write_text("".join(kept))
if subprocess.run(["nginx", "-t"]).returncode != 0:
    shutil.copyfile(backup, p)
    print("nginx rate limit: nginx -t failed, restored dry run")
else:
    print(f"nginx rate limit: enforced (dry run off x{len(lines) - len(kept)})")
PY
nginx -t && systemctl reload nginx

# Дайджест генерирует assistant-news-sync; rsync репозитория его затирал.
if [[ -d /opt/obuchat-news/public/news ]]; then
  mkdir -p /opt/assistant/news
  rsync -az --delete /opt/obuchat-news/public/news/ /opt/assistant/news/
  echo "republished /news from /opt/obuchat-news/public/news"
fi

systemctl enable assistant-bot assistant-usage executive-board-bot
systemctl restart assistant-bot assistant-usage executive-board-bot
sleep 2
systemctl is-active assistant-bot assistant-usage executive-board-bot

python3 - <<'PY'
from pathlib import Path
from urllib.parse import quote
import urllib.error
import urllib.request

tok = ""
env = Path("/opt/assistant/.env")
if env.is_file():
    for line in env.read_text().splitlines():
        if line.startswith("USAGE_DASHBOARD_TOKEN="):
            tok = line.split("=", 1)[1].strip().strip("'\"")
            break
url = "https://assistent.networ.ru/dashboard/"
try:
    urllib.request.urlopen(urllib.request.Request(url), timeout=10)
    print("dashboard_no_token:200")
except urllib.error.HTTPError as e:
    print(f"dashboard_no_token:{e.code}")
except Exception as e:
    print(f"dashboard_no_token:{type(e).__name__}")
if tok:
    req = urllib.request.Request(url + "?access_token=" + quote(tok, safe=""))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"dashboard_auth:{r.status}")
            print("dashboard_admin:" + ("yes" if "Админка Орбиты" in body else "no"))
    except urllib.error.HTTPError as e:
        print(f"dashboard_auth:{e.code}")
    except Exception as e:
        print(f"dashboard_auth:{type(e).__name__}")
PY

# Vexa (бот записи Zoom) не используется: с 2026-10-22 поднимаем её, только если VEXA_ENABLED=1.
if ! grep -qE '^VEXA_ENABLED=(1|true|yes|on)' .env 2>/dev/null; then
  echo "==> Vexa: выключена (VEXA_ENABLED не задан) — пропуск"
elif [[ -x scripts/setup_vexa_meeting_bot.sh ]]; then
  echo "==> Vexa meeting bot"
  bash scripts/setup_vexa_meeting_bot.sh || {
    echo "WARN: setup_vexa_meeting_bot.sh не завершился — проверьте docker logs vexa" >&2
  }
  systemctl restart assistant-bot assistant-usage executive-board-bot 2>/dev/null || true
fi
REMOTE_SETUP
)
# Шаг 2. Переключение — одним скриптом на самом сервере (systemd-run): обрыв SSH его не прерывает.
# Остановка → бэкап баз → копия stage → /opt/assistant (локально, без сети) → настройка и запуск.
# Упал на любом шаге — сам поднимает сервисы. Mac только читает его лог.
DEPLOY_TS="$(date +%Y%m%d-%H%M%S)-$$"
REMOTE_LOG="${DEPLOY_DIR}/${DEPLOY_TS}.log"
REMOTE_SWITCH="${DEPLOY_DIR}/switch-${DEPLOY_TS}.sh"
SWITCH_SCRIPT="$(cat <<SWITCH_HEAD
#!/usr/bin/env bash
exec >>'${REMOTE_LOG}' 2>&1
exec 9>'${DEPLOY_DIR}/deploy.lock'
if ! flock -n 9; then
  echo "Другая выкатка ещё идёт — эта отменена"
  echo "DEPLOY_EXIT=75"
  exit 75
fi
SWITCH_HEAD
)
$(cat <<'SWITCH_BODY'
_on_exit() {
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    echo "==> Сбой выкатки (код $rc) — поднимаю сервисы"
    systemctl start assistant-bot assistant-usage executive-board-bot 2>/dev/null || true
  fi
  echo "DEPLOY_EXIT=$rc"
}
trap _on_exit EXIT
set -euo pipefail

echo "==> Остановка сервисов"
systemctl stop assistant-bot assistant-usage executive-board-bot assistant-whatsapp-bridge 2>/dev/null || true
systemctl disable assistant-whatsapp-bridge 2>/dev/null || true

echo "==> Резервная копия баз перед выкаткой (/root/leo-backups/predeploy)"
if [[ -f /opt/assistant/scripts/backup_dbs.py ]]; then
  python3 /opt/assistant/scripts/backup_dbs.py predeploy || echo "WARN: backup_dbs predeploy failed"
fi

echo "==> Код: /opt/assistant.stage → /opt/assistant"
rsync -a --delete --exclude-from=/opt/assistant.stage/scripts/deploy_rsync_excludes.txt \
  /opt/assistant.stage/ /opt/assistant/
SWITCH_BODY
)
${REMOTE_SETUP_SCRIPT}
"

echo "==> Переключение на сервере (лог ${REMOTE_LOG})"
_remote_write "$REMOTE_SWITCH" "$SWITCH_SCRIPT"
_ssh_retry "systemd-run --unit='leo-deploy-${DEPLOY_TS}' --collect --quiet /bin/bash '${REMOTE_SWITCH}'" >/dev/null

# Шаг 3. Читаем лог, пока не появится DEPLOY_EXIT. Связь пропала — переподключаемся;
# выкатка на сервере идёт своим ходом.
_log_off=0
_lost=0
_deploy_rc=""
while :; do
  if _chunk="$("${SSH[@]}" "${SSH_USER}@${SSH_HOST}" \
    "f='${REMOTE_LOG}'; s=\$(stat -c %s \"\$f\" 2>/dev/null || echo 0); echo \"@@SIZE=\$s\"; tail -c +$((_log_off + 1)) \"\$f\" 2>/dev/null | head -c \$((s - ${_log_off}))" 2>/dev/null)"; then
    _lost=0
    _size="$(printf '%s\n' "$_chunk" | head -1 | sed -n 's/^@@SIZE=//p')"
    _body="$(printf '%s\n' "$_chunk" | tail -n +2)"
    [[ -n "$_body" ]] && printf '%s\n' "$_body"
    [[ "$_size" =~ ^[0-9]+$ ]] && _log_off="$_size"
    _deploy_rc="$(printf '%s\n' "$_body" | sed -n 's/^DEPLOY_EXIT=//p' | tail -1)"
    [[ -n "$_deploy_rc" ]] && break
  else
    _lost=$((_lost + 1))
    if [[ "$_lost" -ge 60 ]]; then
      echo "Нет связи с сервером ~5 минут. Выкатка продолжается на сервере сама; лог: ${REMOTE_LOG}" >&2
      exit 2
    fi
    echo "(нет связи с сервером — жду, выкатка идёт на сервере)" >&2
  fi
  sleep 5
done
if [[ "$_deploy_rc" != "0" ]]; then
  echo "Выкатка на сервере завершилась с ошибкой (код ${_deploy_rc}); сервисы подняты. Лог: ${REMOTE_LOG}" >&2
  exit 1
fi

echo "Готово: https://assistent.networ.ru/webapp/"
echo "Дашборд: https://assistent.networ.ru/dashboard/?access_token=<USAGE_DASHBOARD_TOKEN>"
