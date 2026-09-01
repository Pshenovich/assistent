#!/usr/bin/env bash
# Self-hosted Vexa Lite для Leo meeting bot. Запускается на VPS (Ubuntu + Docker).
set -euo pipefail

ASSISTANT_ROOT="${ASSISTANT_ROOT:-/opt/assistant}"
VEXA_ROOT="${VEXA_ROOT:-/opt/vexa}"
VEXA_PORT="${VEXA_PORT:-8056}"
WEBHOOK_PUBLIC_URL="${WEBHOOK_PUBLIC_URL:-https://assistant.obuchat.me/webhook/vexa}"

mkdir -p "$VEXA_ROOT"
cd "$VEXA_ROOT"

if [[ ! -f "$VEXA_ROOT/.vexa_secrets" ]]; then
  PG_PASS="$(openssl rand -hex 16)"
  ADMIN_TOKEN="$(openssl rand -hex 24)"
  WEBHOOK_SECRET="$(openssl rand -hex 16)"
  cat >"$VEXA_ROOT/.vexa_secrets" <<EOF
VEXA_PG_PASSWORD=${PG_PASS}
VEXA_ADMIN_TOKEN=${ADMIN_TOKEN}
VEXA_WEBHOOK_SECRET=${WEBHOOK_SECRET}
EOF
  chmod 600 "$VEXA_ROOT/.vexa_secrets"
fi
# shellcheck disable=SC1091
source "$VEXA_ROOT/.vexa_secrets"

docker network inspect vexa-network >/dev/null 2>&1 || docker network create vexa-network

if ! docker ps -a --format '{{.Names}}' | grep -qx vexa-postgres; then
  docker run -d \
    --name vexa-postgres \
    --network vexa-network \
    --restart unless-stopped \
    -e POSTGRES_USER=vexa \
    -e POSTGRES_PASSWORD="$VEXA_PG_PASSWORD" \
    -e POSTGRES_DB=vexa \
    -v vexa-pg-data:/var/lib/postgresql/data \
    postgres:16-alpine
else
  docker start vexa-postgres 2>/dev/null || true
fi

echo "==> Ожидание PostgreSQL…"
for _ in $(seq 1 40); do
  if docker exec vexa-postgres pg_isready -U vexa -d vexa >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

_run_vexa_container() {
  local zoom_env=()
  if [[ -f "$ASSISTANT_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source <(grep -E '^(ZOOM_CLIENT_ID|ZOOM_CLIENT_SECRET)=' "$ASSISTANT_ROOT/.env" || true)
    set +a
  fi
  if [[ -n "${ZOOM_CLIENT_ID:-}" && -n "${ZOOM_CLIENT_SECRET:-}" ]]; then
    zoom_env+=(
      -e "ZOOM_SDK=true"
      -e "ZOOM_CLIENT_ID=${ZOOM_CLIENT_ID}"
      -e "ZOOM_CLIENT_SECRET=${ZOOM_CLIENT_SECRET}"
    )
  fi
  local sdk_mount=()
  if [[ -d /opt/vexa-zoom-sdk/libs ]]; then
    sdk_mount+=(
      -v /opt/vexa-zoom-sdk/libs:/opt/zoom-sdk/libs:ro
      -v /opt/vexa-zoom-sdk/zoom_sdk_wrapper.node:/app/build/Release/zoom_sdk_wrapper.node:ro
    )
  fi
  docker pull vexaai/vexa-lite:latest
  docker run -d \
    --name vexa \
    --network vexa-network \
    --restart unless-stopped \
    -p "127.0.0.1:${VEXA_PORT}:8056" \
    -v vexa-recordings:/var/lib/vexa/recordings \
    "${sdk_mount[@]}" \
    -e DATABASE_URL="postgresql://vexa:${VEXA_PG_PASSWORD}@vexa-postgres:5432/vexa" \
    -e ADMIN_API_TOKEN="$VEXA_ADMIN_TOKEN" \
    -e STORAGE_BACKEND=local \
    -e LOCAL_RECORDING_DIR=/var/lib/vexa/recordings \
    -e TRANSCRIPTION_SERVICE_URL="https://transcription.vexa.ai/v1/audio/transcriptions" \
    -e TRANSCRIPTION_SERVICE_TOKEN="not-used-by-leo" \
    -e SKIP_TRANSCRIPTION_CHECK=true \
    -e WHISPER_BACKEND=remote \
    -e LOG_LEVEL=warning \
    "${zoom_env[@]}" \
    vexaai/vexa-lite:latest
}

if ! docker ps -a --format '{{.Names}}' | grep -qx vexa; then
  _run_vexa_container
else
  docker start vexa 2>/dev/null || true
fi

echo "==> Ожидание Vexa API…"
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${VEXA_PORT}/docs" >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
if ! curl -sf "http://127.0.0.1:${VEXA_PORT}/docs" >/dev/null 2>&1; then
  echo "==> Пересоздание контейнера vexa (SKIP_TRANSCRIPTION_CHECK)…" >&2
  docker rm -f vexa 2>/dev/null || true
  _run_vexa_container
  for _ in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:${VEXA_PORT}/docs" >/dev/null 2>&1; then
      break
    fi
    sleep 3
  done
fi
if ! curl -sf "http://127.0.0.1:${VEXA_PORT}/docs" >/dev/null 2>&1; then
  echo "Vexa API не отвечает на :${VEXA_PORT}" >&2
  docker logs vexa --tail 80 >&2 || true
  exit 1
fi

PATCH_SCRIPT="${ASSISTANT_ROOT}/scripts/patch_vexa_zoom_join.sh"
if [[ -f "$PATCH_SCRIPT" ]]; then
  bash "$PATCH_SCRIPT" || echo "==> patch_vexa_zoom_join: пропуск (не критично)" >&2
fi

SDK_SCRIPT="${ASSISTANT_ROOT}/scripts/setup_vexa_zoom_sdk.sh"
if [[ -f "$SDK_SCRIPT" && -x "$SDK_SCRIPT" ]]; then
  bash "$SDK_SCRIPT" || echo "==> setup_vexa_zoom_sdk: пропуск (см. лог)" >&2
fi

USER_JSON="$(curl -sf -X POST "http://127.0.0.1:${VEXA_PORT}/admin/users" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: ${VEXA_ADMIN_TOKEN}" \
  -d '{"email":"leo@obuchat.me","name":"Leo Assistant","max_concurrent_bots":3}' || true)"
USER_ID="$(printf '%s' "$USER_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("id",""))' 2>/dev/null || true)"
if [[ -z "$USER_ID" ]]; then
  USER_JSON="$(curl -sf "http://127.0.0.1:${VEXA_PORT}/admin/users/email/leo@obuchat.me" \
    -H "X-Admin-API-Key: ${VEXA_ADMIN_TOKEN}")"
  USER_ID="$(printf '%s' "$USER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')"
fi

if [[ -f "$VEXA_ROOT/.api_token" ]]; then
  VEXA_API_KEY="$(cat "$VEXA_ROOT/.api_token")"
else
  TOKEN_JSON="$(curl -sf -X POST "http://127.0.0.1:${VEXA_PORT}/admin/users/${USER_ID}/tokens" \
    -H "X-Admin-API-Key: ${VEXA_ADMIN_TOKEN}")"
  VEXA_API_KEY="$(printf '%s' "$TOKEN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))')"
  printf '%s' "$VEXA_API_KEY" >"$VEXA_ROOT/.api_token"
  chmod 600 "$VEXA_ROOT/.api_token"
fi

curl -sf -X PUT "http://127.0.0.1:${VEXA_PORT}/user/webhook" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${VEXA_API_KEY}" \
  -d "{\"webhook_url\":\"${WEBHOOK_PUBLIC_URL}\",\"webhook_secret\":\"${VEXA_WEBHOOK_SECRET}\"}" \
  >/dev/null

touch "$ASSISTANT_ROOT/.env"
_set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ASSISTANT_ROOT/.env" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ASSISTANT_ROOT/.env"
  else
    echo "${key}=${val}" >>"$ASSISTANT_ROOT/.env"
  fi
}

_set_env MEETING_BOT_ENABLED 1
_set_env VEXA_API_BASE "http://127.0.0.1:${VEXA_PORT}"
_set_env VEXA_API_KEY "$VEXA_API_KEY"
_set_env VEXA_WEBHOOK_SECRET "$VEXA_WEBHOOK_SECRET"
_set_env MEETING_BOT_NAME Leo
_set_env MEETING_BOT_JOIN_EARLY_MIN 2
_set_env MEETING_BOT_SCHEDULER_ENABLED 1
_set_env MEETING_BOT_POLL_SEC 20
_set_env MEETING_RECORDINGS_DB_PATH ./data/meeting_recordings.sqlite

echo "==> Vexa готов: http://127.0.0.1:${VEXA_PORT} webhook=${WEBHOOK_PUBLIC_URL}"
