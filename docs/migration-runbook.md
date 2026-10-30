# Переезд Орбиты на новый сервер

Старый сервер: `91.229.90.4` (общий, 2 ядра / 4 ГБ).
Новый: Beget, 8 ядер / 16 ГБ / 220 ГБ, Ubuntu 24.04 — `NEW` ниже.
Адрес сайта на время переезда не меняется: `assistent.networ.ru`. Смена домена на `orbita.me` — отдельным шагом, через 1–2 дня.

**Главное правило:** бот Telegram в каждый момент работает только в одном месте. Если запустить два бота с одним токеном, Telegram отвечает ошибкой 409, и сообщения теряются. Поэтому порядок такой: остановить старый → скопировать данные → запустить новый.

## Что переносим

| Что | Где на старом | Как |
|---|---|---|
| Код | `/opt/assistant` | `scripts/deploy_prod_replace.sh` с Мака, после смены `DEPLOY_SSH_HOST` |
| Базы SQLite | `/opt/assistant/notes.sqlite`, `/opt/assistant/data/*.sqlite`, `/opt/assistant/assistant/lib/usage.sqlite` | rsync сервер → сервер при остановленных сервисах |
| Состояние в файлах | всё, что перечислено в `scripts/deploy_rsync_excludes.txt`: токены OAuth, `contacts*`, `data/users/`, `reminders*`, `.env`, `comment_files/`, `mail_attachments/` | тот же rsync всего `/opt/assistant` без `.venv` |
| Telegram Bot API | `/opt/assistant/data/telegram-bot-api/` | тем же rsync, контейнер поднимает деплой |
| Выпуск «Рынок» | `/opt/obuchat-news` + `assistant-news-sync.timer` | rsync + копия unit-файлов |
| Сертификаты | `/etc/letsencrypt` | rsync, потом обычный certbot renew |
| Бэкапы | `/root/leo-backups` | последние 3 ночных + все predeploy |

**Не переносим:**
- Vexa — `/opt/vexa*` и контейнеры, она выключена.
- Мелкие проекты: `insurance-app`, `netw-*`, `tchk-postgres`, `next-server`, `pm2` — они остаются на старом сервере.
- Старые архивы `/opt/assistant-*-backup*`.

## За день (T-1)

1. **DNS.** В панели DNS у записи `assistent.networ.ru` поставить TTL = 300 (5 минут). Тогда в день переезда новый IP разойдётся за минуты, а не за часы.
2. **Подготовка нового сервера.** На Маке добавить ключ, затем запустить настройку:
   ```bash
   ssh-copy-id -i ~/.ssh/leo_server.pub root@NEW
   scp scripts/provision_server.sh root@NEW:/root/
   ssh root@NEW 'bash /root/provision_server.sh'
   ```
   Проверка: `ssh root@NEW 'ufw status; swapon --show; sshd -T | grep -i passwordauth'`.
3. **Репетиция на копии данных** — старый сервер продолжает работать:
   ```bash
   # со старого на новый, без остановки старого (копия может быть неконсистентной — это только репетиция)
   ssh root@91.229.90.4 "rsync -az --exclude .venv --exclude '__pycache__' /opt/assistant/ root@NEW:/opt/assistant/"
   ```
   На новом сервере:
   - в его `.env` поставить `TELEGRAM_BOT_TOKEN=` **пустым**, чтобы бот не стартовал;
   - деплоить на него, указав `DEPLOY_SSH_HOST=NEW` во временной копии `.env` на Маке;
   - проверить `curl -H 'Host: assistent.networ.ru' http://NEW/healthz` и открыть `/webapp/` через `/etc/hosts` на Маке.
4. **Сверка числа записей** — скрипт `count_rows` ниже, на старом и новом сервере; цифры должны совпасть с моментом копии.

## День переезда (вечер, ~30 минут)

0. **Объявить «час тишины»:** другим сессиям не выкатывать.
1. **Старый сервер — остановить и выключить автозапуск:**
   ```bash
   systemctl stop assistant-bot assistant-worker assistant-usage executive-board-bot
   systemctl disable assistant-bot assistant-worker assistant-usage executive-board-bot
   python3 /opt/assistant/scripts/backup_dbs.py predeploy
   ```
   Если на старом сервере нужна страница «Идёт обновление», поставить её в nginx на эти минуты.
2. **Данные — финальная копия, сервисы стоят:**
   ```bash
   ssh root@91.229.90.4 "rsync -az --delete --exclude .venv --exclude '__pycache__' /opt/assistant/ root@NEW:/opt/assistant/"
   ssh root@91.229.90.4 "rsync -az /opt/obuchat-news/ root@NEW:/opt/obuchat-news/ && rsync -az /etc/letsencrypt/ root@NEW:/etc/letsencrypt/ && rsync -az /root/leo-backups/ root@NEW:/root/leo-backups/"
   ssh root@91.229.90.4 "scp /etc/systemd/system/assistant-news-sync.* root@NEW:/etc/systemd/system/"
   ```
3. **Сверка** `count_rows` на обоих серверах — должно совпасть один в один.
4. **Код.** В `.env` на Маке `DEPLOY_SSH_HOST=NEW`, затем `./scripts/deploy_prod_replace.sh`. Деплой:
   - ставит venv и сервисы, включая `assistant-worker`;
   - поднимает Telegram Bot API;
   - настраивает nginx;
   - запускает всё.
5. **DNS.** Запись `assistent.networ.ru` → `NEW`. Подождать 5 минут.
6. **Проверки:**
   - `curl https://assistent.networ.ru/healthz` → 200, бот подаёт сигнал;
   - вход в Орбиту, открыть заметку, создать и удалить напоминание;
   - написать боту в Telegram и получить ответ;
   - в журнале нет ошибок: `journalctl -u assistant-bot -u assistant-usage --since -10min | grep -iE 'error|traceback'`;
   - в Sentry нет новых ошибок.
7. **Сертификаты.** `certbot renew --dry-run`.
8. **Таймер «Рынка».** `systemctl daemon-reload && systemctl enable --now assistant-news-sync.timer`.

## Если что-то пошло не так — откат (5 минут)

1. На новом сервере: `systemctl stop assistant-bot assistant-worker assistant-usage executive-board-bot`.
2. DNS `assistent.networ.ru` → `91.229.90.4`.
3. На старом сервере: `systemctl enable --now assistant-bot assistant-worker assistant-usage executive-board-bot`.
4. Изменения, сделанные на новом сервере за эти минуты, будут потеряны. Поэтому решение об откате принимаем в первые 15 минут.

## После переезда

- Старый сервер с остановленными сервисами Орбиты держим неделю: это запасной путь.
- **Включить фоновые задачи в отдельном процессе:**
  1. в `.env` поставить `JOBS_IN_WORKER=1`;
  2. `systemctl restart assistant-bot assistant-worker`;
  3. проверить `/healthz`: появилось поле `worker_heartbeat_age_sec`.
- **Сервисы не от root** (после пары спокойных дней):
  1. `LEO_NONROOT=1 bash /root/provision_server.sh`;
  2. `systemctl restart assistant-bot assistant-worker assistant-usage assistant-embed executive-board-bot`;
  3. проверить, что бот читает файлы локального Bot API.
- Мониторинг Beget, CPU/RAM/диск → Telegram: включить в панели.
- Внешний пинг `/healthz` раз в 5 минут.

## Восстановление из бэкапа (если умер сервер)

1. Новый сервер: `provision_server.sh`.
2. Код: деплой с Мака.
3. Базы: последний бэкап из `/root/leo-backups/daily/<дата>/` (или из S3, когда подключим):
   ```bash
   for f in /root/leo-backups/daily/<дата>/*.gz; do
     name=$(basename "$f" .gz | sed 's#__#/#g')     # notes.sqlite, data/credits.sqlite, ...
     gunzip -c "$f" > "/opt/assistant/$name"
   done
   ```
4. Файлы состояния — токены, `data/users/` — в бэкап баз не входят. Именно поэтому бэкап всего `/opt/assistant` в S3 — следующий шаг после переезда.

## count_rows — сверка данных

```bash
cd /opt/assistant && .venv/bin/python - <<'EOF'
import sqlite3, pathlib
dbs = [pathlib.Path("notes.sqlite"), pathlib.Path("assistant/lib/usage.sqlite"), *sorted(pathlib.Path("data").glob("*.sqlite"))]
for db in dbs:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
        print(f"{db}:{t}", c.execute(f'select count(*) from "{t}"').fetchone()[0])
EOF
```
