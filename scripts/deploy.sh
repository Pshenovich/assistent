#!/usr/bin/env bash
# Деплой только репозитория ассистента в /opt/assistant — не трогает другие проекты на VPS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ASSISTANT_ROOT="/opt/assistant"
ALLOWED_SERVICES=(assistant-bot assistant-usage assistant-whatsapp-bridge)

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

# Допустимо SSH_USERNAME=user@host — тогда SSH_IP можно не дублировать.
if [[ "$SSH_USER" == *@* ]]; then
  _host_part="${SSH_USER#*@}"
  SSH_USER="${SSH_USER%%@*}"
  if [[ -z "$SSH_HOST" ]]; then
    SSH_HOST="$_host_part"
  fi
fi
SSH_HOST="${SSH_HOST#*@}"
DEPLOY_BRANCH="$(_env_get DEPLOY_BRANCH)"
DEPLOY_SERVICES_RAW="$(_env_get DEPLOY_SERVICES)"
DEPLOY_PIP="$(_env_get DEPLOY_PIP)"
DEPLOY_USE_SUDO="$(_env_get DEPLOY_USE_SUDO)"
GITLAB_TOKEN="$(_env_get GITLAB_DEPLOY_TOKEN)"

SSH_PORT="${SSH_PORT:-22}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_PIP="${DEPLOY_PIP:-0}"
DEPLOY_USE_SUDO="${DEPLOY_USE_SUDO:-1}"

if [[ -z "$SSH_USER" || -z "$SSH_HOST" ]]; then
  echo "В .env нужны SSH_USERNAME и SSH_IP (и SSH_PASSWORD, если вход по паролю)." >&2
  exit 1
fi

if [[ -n "$(_env_get DEPLOY_PATH)" && "$(_env_get DEPLOY_PATH)" != "$ASSISTANT_ROOT" ]]; then
  echo "DEPLOY_PATH задан, но для безопасности разрешён только ${ASSISTANT_ROOT} (на сервере есть другие проекты)." >&2
  exit 1
fi

if [[ -z "$DEPLOY_SERVICES_RAW" ]]; then
  DEPLOY_SERVICES_RAW="assistant-bot,assistant-usage"
fi

IFS=',' read -r -a REQUESTED_SERVICES <<< "${DEPLOY_SERVICES_RAW// /}"
SERVICES=()
for s in "${REQUESTED_SERVICES[@]}"; do
  s="${s// /}"
  [[ -n "$s" ]] || continue
  ok=0
  for a in "${ALLOWED_SERVICES[@]}"; do
    if [[ "$s" == "$a" ]]; then ok=1; break; fi
  done
  if [[ "$ok" -ne 1 ]]; then
    echo "Сервис «${s}» не в белом списке: ${ALLOWED_SERVICES[*]}" >&2
    exit 1
  fi
  SERVICES+=("$s")
done

if [[ ${#SERVICES[@]} -eq 0 ]]; then
  echo "Укажите DEPLOY_SERVICES (например assistant-bot,assistant-usage)." >&2
  exit 1
fi

if [[ -n "$SSH_PASS" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "Для SSH_PASSWORD установите sshpass: brew install hudochenkov/sshpass/sshpass" >&2
    exit 1
  fi
  SSH_BASE=(sshpass -p "$SSH_PASS" ssh -p "$SSH_PORT" -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new)
else
  SSH_BASE=(ssh -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new)
fi

remote() {
  "${SSH_BASE[@]}" "${SSH_USER}@${SSH_HOST}" "$@"
}

SUDO=""
if [[ "$DEPLOY_USE_SUDO" == "1" ]]; then
  SUDO="sudo "
fi

preflight_remote() {
  remote bash -s <<EOF
set -euo pipefail
ASSISTANT_ROOT="${ASSISTANT_ROOT}"
if [[ ! -d "\$ASSISTANT_ROOT" ]]; then
  echo "Каталог \$ASSISTANT_ROOT не найден." >&2
  exit 1
fi
cd "\$ASSISTANT_ROOT"
if [[ "\$(pwd -P)" != "\$ASSISTANT_ROOT" ]]; then
  echo "pwd не совпадает с \$ASSISTANT_ROOT." >&2
  exit 1
fi
EOF
}

sync_code_remote() {
  if [[ -n "$GITLAB_TOKEN" ]]; then
    echo "==> git fetch на сервере (GITLAB_DEPLOY_TOKEN)"
    local tok_escaped="${GITLAB_TOKEN//\'/\'\\\'\'}"
    remote bash -s <<EOF
set -euo pipefail
cd "${ASSISTANT_ROOT}"
git fetch "https://oauth2:${tok_escaped}@gitlab.com/Pshenovich/assistant.git" "${DEPLOY_BRANCH}"
git reset --hard FETCH_HEAD
EOF
    return
  fi
  echo "==> sync: git archive с Mac (на сервере нет GitLab HTTPS-токена)"
  if ! git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1; then
    echo "Локальный git HEAD недоступен." >&2
    exit 1
  fi
  git -C "$ROOT" archive HEAD | remote "cd ${ASSISTANT_ROOT} && tar xf -"
}

restart_remote() {
  local script="set -euo pipefail
cd ${ASSISTANT_ROOT}
echo '==> перезапуск сервисов ассистента'"
  for svc in "${SERVICES[@]}"; do
    script+=$(printf '\n%s systemctl restart %q' "$SUDO" "$svc")
    script+=$(printf '\n%s systemctl is-active %q || true' "$SUDO" "$svc")
  done
  if [[ "$DEPLOY_PIP" == "1" ]]; then
    script="
set -euo pipefail
cd ${ASSISTANT_ROOT}
echo '==> pip install'
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -c \"import aiohttp; import ddgs; print('deps ok')\"
${script}"
  fi
  remote bash -s <<< "$script"
}

echo "Деплой ${ASSISTANT_ROOT} на ${SSH_USER}@${SSH_HOST} (сервисы: ${SERVICES[*]})"
preflight_remote
sync_code_remote
restart_remote
echo "Готово. HEAD на Mac: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
