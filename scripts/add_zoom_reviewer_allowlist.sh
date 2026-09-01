#!/usr/bin/env bash
# Add Zoom Marketplace reviewer test Telegram user_id to production allowlist.
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

USER_ID="${1:-$(_env_get ZOOM_REVIEWER_TELEGRAM_USER_ID)}"
USERNAME="${2:-$(_env_get ZOOM_REVIEWER_TELEGRAM_USERNAME)}"

if [[ -z "$USER_ID" ]]; then
  echo "Usage: $0 TELEGRAM_USER_ID [username]" >&2
  echo "Or set ZOOM_REVIEWER_TELEGRAM_USER_ID in .env" >&2
  exit 1
fi

if ! [[ "$USER_ID" =~ ^[0-9]+$ ]]; then
  echo "TELEGRAM_USER_ID must be numeric, got: $USER_ID" >&2
  exit 1
fi

SSH_USER="$(_env_get SSH_USERNAME)"
SSH_PASS="$(_env_get SSH_PASSWORD)"
SSH_HOST="$(_env_get SSH_IP)"
SSH_PORT="$(_env_get DEPLOY_SSH_PORT)"
SSH_PORT="${SSH_PORT:-22}"

if [[ "$SSH_USER" == *@* ]]; then
  _host_part="${SSH_USER#*@}"
  SSH_USER="${SSH_USER%%@*}"
  [[ -z "$SSH_HOST" ]] && SSH_HOST="$_host_part"
fi
SSH_HOST="${SSH_HOST#*@}"

if [[ -z "$SSH_USER" || -z "$SSH_HOST" || -z "$SSH_PASS" ]]; then
  echo "Need SSH_USERNAME, SSH_IP, SSH_PASSWORD in .env" >&2
  exit 1
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "Install sshpass" >&2
  exit 1
fi

SSH=(sshpass -p "$SSH_PASS" ssh -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)

_remote_script=$(cat <<EOF
set -euo pipefail
PATH_FILE="/opt/assistant/data/allowed_telegram_access.json"
mkdir -p /opt/assistant/data
python3 - <<'PY'
import json
import time
from pathlib import Path

path = Path("/opt/assistant/data/allowed_telegram_access.json")
user_id = int("${USER_ID}")
username = "${USERNAME}".strip().lstrip("@").lower()

if path.is_file():
    data = json.loads(path.read_text(encoding="utf-8"))
else:
    data = {"usernames": [], "user_ids": []}

ids = {int(x) for x in (data.get("user_ids") or [])}
names = {str(x).strip().lstrip("@").lower() for x in (data.get("usernames") or []) if str(x).strip()}

ids.add(user_id)
if username:
    names.add(username)

data["user_ids"] = sorted(ids)
data["usernames"] = sorted(names)
data["_updated_unix"] = int(time.time())

path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(data, ensure_ascii=False, indent=2))
PY
EOF
)

echo "Adding Telegram user_id=${USER_ID} to production allowlist on ${SSH_HOST}..."
"${SSH[@]}" "${SSH_USER}@${SSH_HOST}" bash -s <<< "$_remote_script"
echo "Done."
