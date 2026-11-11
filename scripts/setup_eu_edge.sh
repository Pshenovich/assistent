#!/usr/bin/env bash
# eu.getorbita.me — запасной адрес через проходку в КЗ (72.56.10.65 → сюда :8443 с PROXY protocol).
# Запускать НА СЕРВЕРЕ Selectel от root, из папки, где лежат deploy/ и scripts/ (например /opt/assistant):
#   bash scripts/setup_eu_edge.sh base   — порт 80 для eu + ufw 8443 только для 72.56.10.65 (можно сразу)
#   bash scripts/setup_eu_edge.sh tls    — сертификат + сайт на 8443 (только когда КЗ пересылает :80 и DNS готов)
#   bash scripts/setup_eu_edge.sh check  — что сейчас настроено
#   bash scripts/setup_eu_edge.sh off    — снять eu (сайты и правило ufw; сертификат остаётся)
# Повторный запуск безопасен.
set -euo pipefail

EDGE_IP="72.56.10.65"
HOST="eu.getorbita.me"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AVAIL=/etc/nginx/sites-available
ENABLED=/etc/nginx/sites-enabled

reload_or_rollback() {
  local site="$1"
  if nginx -t; then
    systemctl reload nginx
  else
    rm -f "$ENABLED/$site"
    echo "ОШИБКА: nginx -t не прошёл, сайт $site снят, nginx не перезагружался" >&2
    exit 1
  fi
}

case "${1:-}" in
  base)
    install -m 644 "$ROOT/deploy/nginx-eu.getorbita.me.conf" "$AVAIL/$HOST"
    ln -sf "$AVAIL/$HOST" "$ENABLED/$HOST"
    mkdir -p /var/www/certbot
    reload_or_rollback "$HOST"
    ufw allow from "$EDGE_IP" to any port 8443 proto tcp comment "eu.getorbita.me edge KZ" >/dev/null
    echo "OK: порт 80 для $HOST готов, 8443 открыт только для $EDGE_IP"
    ;;
  tls)
    if [[ ! -e "$ENABLED/$HOST" ]]; then
      echo "Сначала: bash scripts/setup_eu_edge.sh base" >&2
      exit 1
    fi
    if [[ ! -f "/etc/letsencrypt/live/$HOST/fullchain.pem" ]]; then
      certbot certonly --webroot -w /var/www/certbot -d "$HOST" --cert-name "$HOST" \
        --non-interactive --keep-until-expiring --deploy-hook "systemctl reload nginx"
    fi
    install -m 644 "$ROOT/deploy/nginx-eu.getorbita.me-tls.conf" "$AVAIL/$HOST-tls"
    ln -sf "$AVAIL/$HOST-tls" "$ENABLED/$HOST-tls"
    reload_or_rollback "$HOST-tls"
    echo "OK: $HOST на 8443 (PROXY protocol от $EDGE_IP)"
    ;;
  check)
    ls -l "$ENABLED" | grep -E "eu\.getorbita" || echo "сайтов eu нет"
    ufw status | grep 8443 || echo "правила ufw для 8443 нет"
    ss -ltnp | grep -E ":8443\b" || echo "8443 никто не слушает"
    ls "/etc/letsencrypt/live/$HOST" 2>/dev/null || echo "сертификата eu нет"
    ;;
  off)
    rm -f "$ENABLED/$HOST" "$ENABLED/$HOST-tls"
    nginx -t && systemctl reload nginx
    ufw delete allow from "$EDGE_IP" to any port 8443 proto tcp >/dev/null || true
    echo "OK: eu снят"
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 2
    ;;
esac
