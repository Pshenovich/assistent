#!/usr/bin/env bash
# Nginx + Let's Encrypt для stagassistant.obuchat.me (Zoom Development redirect URL).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

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

if [[ "$SSH_USER" == *@* ]]; then
  _host_part="${SSH_USER#*@}"
  SSH_USER="${SSH_USER%%@*}"
  [[ -z "$SSH_HOST" ]] && SSH_HOST="$_host_part"
fi
SSH_HOST="${SSH_HOST#*@}"

if [[ -z "$SSH_USER" || -z "$SSH_HOST" || -z "$SSH_PASS" ]]; then
  echo "Нужны SSH_USERNAME, SSH_IP, SSH_PASSWORD в .env" >&2
  exit 1
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "Установите sshpass" >&2
  exit 1
fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
SSH=(sshpass -p "$SSH_PASS" ssh "${SSH_OPTS[@]}")

echo "==> Копируем nginx-конфиг на ${SSH_HOST}"
sshpass -p "$SSH_PASS" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/nginx-stagassistant.obuchat.me.conf" \
  "${SSH_USER}@${SSH_HOST}:/etc/nginx/sites-available/stagassistant.obuchat.me"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -euo pipefail
ln -sf /etc/nginx/sites-available/stagassistant.obuchat.me /etc/nginx/sites-enabled/stagassistant.obuchat.me
nginx -t
systemctl reload nginx
if [[ ! -f /etc/letsencrypt/live/stagassistant.obuchat.me/fullchain.pem ]]; then
  certbot --nginx -d stagassistant.obuchat.me --non-interactive --agree-tos -m support@obuchat.me --redirect
else
  certbot renew --nginx --quiet || true
fi
nginx -t && systemctl reload nginx
curl -sS -o /dev/null -w "staging_https=%{http_code}\n" https://stagassistant.obuchat.me/zoom/home || true
REMOTE
)

echo "==> Настройка nginx + certbot"
"${SSH[@]}" "${SSH_USER}@${SSH_HOST}" bash -s <<< "$REMOTE_SCRIPT"

echo "Готово: https://stagassistant.obuchat.me/oauth/zoom/callback"
