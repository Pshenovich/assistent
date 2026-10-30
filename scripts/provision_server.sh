#!/usr/bin/env bash
# Подготовка чистого сервера под Орбиту (Ubuntu 24.04), запуск от root. Повторный запуск безопасен.
#
#   scp scripts/provision_server.sh root@NEW:/root/ && ssh root@NEW 'bash /root/provision_server.sh'
#
# Что делает:
#   пакеты (python, nginx, certbot, docker, sqlite3, ffmpeg, fail2ban, ufw, unattended-upgrades);
#   swap 2 ГБ; файрвол (только 22/80/443); SSH только по ключу (если ключ уже лежит у root);
#   автоустановка обновлений безопасности; каталоги /opt/assistant, /root/leo-backups, /root/leo-deploy;
#   ночной бэкап баз (cron 03:30); nginx-сайт из deploy/nginx-*.conf (если код уже на месте).
# Код и данные переносит docs/migration-runbook.md; сервисы ставит scripts/deploy_prod_replace.sh.
#
# LEO_NONROOT=1 — сервисы бота/воркера/веба от пользователя leo, а не от root (включаем после репетиции).
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запускать от root" >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
APP=/opt/assistant

echo "==> Пакеты"
apt-get update -q
apt-get install -y -q \
  python3 python3-venv python3-pip python3-dev build-essential \
  nginx certbot python3-certbot-nginx \
  docker.io sqlite3 ffmpeg rsync git curl jq \
  fail2ban ufw unattended-upgrades
systemctl enable --now docker nginx fail2ban

echo "==> Swap 2 ГБ"
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/90-leo-swap.conf
sysctl -q -p /etc/sysctl.d/90-leo-swap.conf

echo "==> Файрвол: только SSH, HTTP, HTTPS"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | head -8

echo "==> SSH только по ключу"
if [[ -s /root/.ssh/authorized_keys ]]; then
  cat > /etc/ssh/sshd_config.d/00-leo-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  # cloud-init часто кладёт «PasswordAuthentication yes» в свой файл — выключаем и там.
  for f in /etc/ssh/sshd_config.d/*.conf; do
    [[ "$f" == */00-leo-hardening.conf ]] && continue
    sed -i 's/^\s*PasswordAuthentication\s\+yes/PasswordAuthentication no/' "$f"
  done
  if sshd -t; then
    systemctl reload ssh || systemctl reload sshd
    echo "SSH: пароли выключены, root — только по ключу"
  else
    rm -f /etc/ssh/sshd_config.d/00-leo-hardening.conf
    echo "WARN: sshd -t не прошёл — настройки SSH не меняю" >&2
  fi
else
  echo "WARN: у root нет authorized_keys — пароли НЕ выключаю, чтобы не потерять доступ" >&2
fi

echo "==> Обновления безопасности автоматически"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "==> Каталоги"
mkdir -p "$APP" /root/leo-backups /root/leo-deploy /var/www/certbot

echo "==> Ночной бэкап баз (03:30)"
cat > /etc/cron.d/leo-backup <<'EOF'
30 3 * * * root /usr/bin/python3 /opt/assistant/scripts/backup_dbs.py daily >> /var/log/leo-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/leo-backup

echo "==> nginx-сайт"
SITE=/etc/nginx/sites-available/assistent.networ.ru
if [[ -f "$APP/deploy/nginx-assistent.networ.ru.conf" && ! -f "$SITE" ]]; then
  cp "$APP/deploy/nginx-assistent.networ.ru.conf" "$SITE"
  ln -sf "$SITE" /etc/nginx/sites-enabled/assistent.networ.ru
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  echo "nginx: сайт поставлен (сертификат — см. runbook)"
else
  echo "nginx: пропуск (кода ещё нет или сайт уже есть)"
fi

if [[ "${LEO_NONROOT:-0}" == "1" ]]; then
  echo "==> Сервисы от пользователя leo"
  id leo >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin leo
  for unit in assistant-bot assistant-worker assistant-usage assistant-embed executive-board-bot; do
    mkdir -p "/etc/systemd/system/${unit}.service.d"
    cat > "/etc/systemd/system/${unit}.service.d/10-user.conf" <<'EOF'
[Service]
User=leo
Group=leo
EOF
  done
  chown -R leo:leo "$APP"
  systemctl daemon-reload
fi

echo "==> Готово. Дальше — docs/migration-runbook.md"
