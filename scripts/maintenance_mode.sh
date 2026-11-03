#!/usr/bin/env bash
# Страница «Орбита на обслуживании» вместо сайта (на сервере, от root). Данные не трогает.
#
#   bash maintenance_mode.sh on    — остановить сервисы Орбиты, все домены → страница-заглушка (503)
#   bash maintenance_mode.sh cert  — после того как DNS смотрит сюда: https-сертификаты (certbot)
#   bash maintenance_mode.sh off   — вернуть сайты Орбиты (сервисы запускает деплой или systemctl start)
#
# Нужна, когда домен уже направлен на этот сервер, а данных на нём ещё нет: люди видят понятную
# страницу, а не пустую Орбиту, и ничего не записывают мимо настоящих баз.
set -euo pipefail

WWW=/var/www/leo-maintenance
SITE=/etc/nginx/sites-available/leo-maintenance
SAVED=/etc/nginx/leo-maintenance.saved
DOMAINS=(assistent.networ.ru getorbita.me www.getorbita.me)

page() {
  mkdir -p "$WWW" /var/www/certbot
  cat > "$WWW/index.html" <<'HTML'
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Орбита — обслуживание</title>
<style>
  :root { --bg: #f6f7fb; --card: #fff; --text: #1b1f2a; --muted: #5d6475; --accent: #4b5cff; }
  @media (prefers-color-scheme: dark) { :root { --bg: #0f1117; --card: #181b24; --text: #eef0f6; --muted: #a1a8b8; --accent: #8d99ff; } }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         background: var(--bg); color: var(--text); font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  .card { max-width: 440px; width: 100%; background: var(--card); border-radius: 20px; padding: 32px 28px;
          box-shadow: 0 8px 30px rgba(0,0,0,.08); text-align: center; }
  .dot { width: 14px; height: 14px; border-radius: 50%; background: var(--accent); margin: 0 auto 20px;
         box-shadow: 0 0 0 8px color-mix(in srgb, var(--accent) 18%, transparent); }
  h1 { font-size: 22px; margin: 0 0 12px; }
  p { margin: 0 0 10px; color: var(--muted); }
</style>
</head>
<body>
  <div class="card">
    <div class="dot"></div>
    <h1>Орбита на обслуживании</h1>
    <p>Переезжаем на новый, более надёжный сервер.</p>
    <p>Ваши заметки, задачи и встречи в сохранности — скоро всё заработает.</p>
  </div>
</body>
</html>
HTML
}

case "${1:-}" in
  on)
    page
    systemctl stop assistant-bot assistant-worker assistant-usage executive-board-bot 2>/dev/null || true
    # Сайты Орбиты снимаем (запоминаем, какие были), ставим заглушку на все домены.
    if [[ ! -f "$SAVED" ]]; then
      : > "$SAVED"
      for s in /etc/nginx/sites-enabled/*; do
        [[ -e "$s" && "$(basename "$s")" != leo-maintenance ]] || continue
        basename "$s" >> "$SAVED"
        rm -f "$s"
      done
    fi
    cat > "$SITE" <<NGINX
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${DOMAINS[*]};
    root $WWW;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location = /index.html { internal; add_header Retry-After 3600 always; add_header Cache-Control "no-store" always; }
    error_page 503 /index.html;
    location / { return 503; }
}
NGINX
    ln -sf "$SITE" /etc/nginx/sites-enabled/leo-maintenance
    nginx -t && systemctl reload nginx
    echo "maintenance: on (сняты сайты: $(tr '\n' ' ' < "$SAVED"))"
    ;;
  cert)
    args=()
    for d in "${DOMAINS[@]}"; do
      ip="$(getent ahostsv4 "$d" | awk 'NR==1{print $1}')"
      if [[ "$ip" == "$(curl -4 -s -m 5 https://ifconfig.me || true)" ]]; then
        args+=(-d "$d")
      else
        echo "пропуск $d: DNS ещё смотрит на ${ip:-?}"
      fi
    done
    [[ ${#args[@]} -gt 0 ]] || { echo "ни один домен ещё не смотрит сюда"; exit 1; }
    certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email --redirect \
      --cert-name leo-maintenance "${args[@]}"
    nginx -t && systemctl reload nginx
    echo "maintenance: https для ${args[*]}"
    ;;
  off)
    rm -f /etc/nginx/sites-enabled/leo-maintenance
    if [[ -f "$SAVED" ]]; then
      while read -r s; do
        [[ -n "$s" && -e "/etc/nginx/sites-available/$s" ]] && ln -sf "/etc/nginx/sites-available/$s" "/etc/nginx/sites-enabled/$s"
      done < "$SAVED"
      rm -f "$SAVED"
    fi
    nginx -t && systemctl reload nginx
    echo "maintenance: off (сервисы Орбиты не запущены — деплой или systemctl start)"
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 2
    ;;
esac
