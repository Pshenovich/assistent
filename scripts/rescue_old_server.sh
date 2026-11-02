#!/usr/bin/env bash
# Спасение данных со старого сервера (Beget), пока он жив: свежая копия всех баз и файлов состояния
# → Мак → S3 (orbita-backups/beget-rescue/…) → новый сервер (/root/leo-rescue/…).
# Сервисы на старом сервере НЕ останавливает и ничего там не меняет, кроме файлов в /root/leo-rescue.
#
#   ./scripts/rescue_old_server.sh [СТАРЫЙ_IP] [НОВЫЙ_IP]
#
# По умолчанию 91.229.90.4 → 161.104.35.44. Ключ ~/.ssh/leo_server, ключи S3 — из .env.
set -euo pipefail

OLD="${1:-91.229.90.4}"
NEW="${2:-161.104.35.44}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${SSH_IDENTITY_FILE:-$HOME/.ssh/leo_server}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$HOME/leo-rescue/$TS"
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new)

_retry() {  # старый сервер иногда рвёт SSH на «banner exchange» — пробуем до 5 раз
  local i
  for i in 1 2 3 4 5; do
    "$@" && return 0
    echo "(не получилось, попытка $i из 5 — жду 10 с)" >&2
    sleep 10
  done
  return 1
}

echo "==> 1/4 Копия на старом сервере $OLD (сервисы работают дальше)"
_retry ssh "${SSH_OPTS[@]}" "root@$OLD" "bash -s" <<REMOTE
set -euo pipefail
rm -rf /root/leo-rescue && mkdir -p /root/leo-rescue/$TS
cd /opt/assistant
python3 - <<'PY'
import sys
sys.path.insert(0, "scripts")
from pathlib import Path
import backup_dbs as b
out = Path("/root/leo-rescue/$TS")
ok = 0
for db in b.databases():
    try:
        b.backup_one(db, out)
        ok += 1
    except Exception as e:
        print(f"WARN {db}: {e!r}")
print(f"баз скопировано: {ok}")
PY
tar czf /root/leo-rescue/$TS/state.tar.gz -C /opt/assistant \
  --exclude=./.venv --exclude=./.git --exclude=./.claude --exclude='*/node_modules' --exclude='*/.npm-cache' \
  --exclude='*/__pycache__' --exclude='./data/telegram-bot-api' --exclude=./news \
  --exclude='*.sqlite' --exclude='*.sqlite-wal' --exclude='*.sqlite-shm' --exclude='*.sqlite-journal' --exclude='*.log' \
  . || [[ \$? -eq 1 ]]   # 1 = «файл менялся во время чтения» — не ошибка для живого сервера
tar czf /root/leo-rescue/$TS/letsencrypt.tar.gz -C /etc letsencrypt 2>/dev/null || true
[[ -d /opt/obuchat-news ]] && tar czf /root/leo-rescue/$TS/obuchat-news.tar.gz -C /opt obuchat-news --exclude='*/node_modules' || true
cp /etc/systemd/system/assistant-news-sync.* /root/leo-rescue/$TS/ 2>/dev/null || true
ls -la /root/leo-rescue/$TS
du -sh /root/leo-rescue/$TS
REMOTE

echo "==> 2/4 Забираю копию на Мак: $OUT"
mkdir -p "$OUT"
_retry scp "${SSH_OPTS[@]}" -r "root@$OLD:/root/leo-rescue/$TS/." "$OUT/"
ls -la "$OUT"
for f in "$OUT"/*.sqlite.gz; do
  gunzip -t "$f" || { echo "ОШИБКА: битый архив $f" >&2; exit 1; }
done
gunzip -t "$OUT/state.tar.gz"
echo "Копия на Маке цела: $(ls "$OUT"/*.sqlite.gz | wc -l | tr -d ' ') баз + файлы состояния"

echo "==> 3/4 В S3 (orbita-backups/beget-rescue/daily/$TS)"
if S3_BACKUP_PREFIX=beget-rescue python3 "$ROOT/scripts/backup_s3.py" upload "$OUT" daily; then
  for extra in letsencrypt.tar.gz obuchat-news.tar.gz; do
    [[ -f "$OUT/$extra" ]] || continue
    S3_BACKUP_PREFIX=beget-rescue python3 - "$ROOT" "$OUT/$extra" "beget-rescue/extra/$TS/$extra" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import backup_s3
backup_s3.S3(backup_s3._env()).put(Path(sys.argv[2]), sys.argv[3])
print("[s3]", sys.argv[3])
PY
  done
else
  echo "WARN: в S3 не ушло — копия есть на Маке ($OUT), продолжаю" >&2
fi

echo "==> 4/4 На новый сервер $NEW (/root/leo-rescue/$TS)"
if ssh "${SSH_OPTS[@]}" "root@$NEW" "mkdir -p /root/leo-rescue/$TS" && \
   scp "${SSH_OPTS[@]}" -r "$OUT/." "root@$NEW:/root/leo-rescue/$TS/"; then
  echo "На новом сервере: /root/leo-rescue/$TS"
else
  echo "WARN: на новый сервер не скопировалось — копия есть на Маке и в S3" >&2
fi

echo
echo "ГОТОВО: данные Орбиты сохранены вне старого сервера ($TS)."
