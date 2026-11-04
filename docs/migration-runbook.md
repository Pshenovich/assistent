# Переезд Орбиты на новый сервер

Старый сервер: Beget `91.229.90.4` (общий, 2 ядра / 4 ГБ). С вечера 01.11 лежал; данные были только на нём.
Новый: **Selectel `161.104.35.44`** (`NEW` ниже), проект Orbita, СПб, 4 vCPU / 16 ГБ / 80 ГБ SSD, Ubuntu 26.04
(Python 3.12 ставит `provision_server.sh` через uv — системный 3.14). Вход: `ssh -i ~/.ssh/leo_server root@NEW`.
Бэкапы — S3 Selectel, бакет `orbita-backups` (ru-7, Москва, приватный), ключи `S3_BACKUP_*` в локальном `.env`.
Оба домена переезжают сразу: `assistent.networ.ru` и `getorbita.me` (+ `www`). DNS обоих — в панели Beget.

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

## Сразу, как старый сервер ответит

Снять копию всех баз и файлов состояния и увезти её со старого сервера — **до всего остального**:
```bash
./scripts/rescue_old_server.sh
```
Сервисы не останавливает. Копия ложится на Мак (`~/leo-rescue/<время>`), в S3 (`beget-rescue/…`)
и на новый сервер (`/root/leo-rescue/<время>`).

## За день (T-1)

1. **DNS.** Beget → Домены и поддомены → DNS: у A-записей `assistent.networ.ru`, `getorbita.me`, `www.getorbita.me`
   поставить TTL = 300 (5 минут). Тогда в день переезда новый IP разойдётся за минуты, а не за часы.
2. **Подготовка нового сервера** (сделано 02.11; повторный запуск безопасен):
   ```bash
   ssh-copy-id -i ~/.ssh/leo_server.pub root@NEW
   scp scripts/provision_server.sh root@NEW:/root/
   ssh root@NEW 'bash /root/provision_server.sh'
   ```
   Проверка: `ssh root@NEW 'ufw status; swapon --show; sshd -T | grep -i passwordauth'`.
3. **Репетиция на копии данных** — старый сервер продолжает работать:
   ```bash
   # со старого на новый, без остановки старого (копия может быть неконсистентной — это только репетиция).
   # -A: старый сервер ходит на новый ключом с Мака (сначала ssh-add ~/.ssh/leo_server)
   ssh -A root@91.229.90.4 "rsync -az --exclude .venv --exclude '__pycache__' /opt/assistant/ root@NEW:/opt/assistant/"
   ```
   На новом сервере:
   - в его `.env` поставить `TELEGRAM_BOT_TOKEN=` **пустым**, чтобы бот не стартовал;
   - деплоить на него: `DEPLOY_SSH_HOST_OVERRIDE=NEW ./scripts/deploy_prod_replace.sh`. Общий `.env`
     не трогать: на `DEPLOY_SSH_HOST` в нём смотрят выкатки всех сессий;
   - деплой ставит оба nginx-сайта (`deploy/nginx-assistent.networ.ru.conf`, `deploy/nginx-getorbita.me.conf`)
     и Litestream; certbot на репетиции не выпустит сертификат (DNS ещё на старом) — это нормально;
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
   ssh -A root@91.229.90.4 "rsync -az --delete --exclude .venv --exclude '__pycache__' /opt/assistant/ root@NEW:/opt/assistant/"
   ssh -A root@91.229.90.4 "rsync -az /opt/obuchat-news/ root@NEW:/opt/obuchat-news/ && rsync -az /etc/letsencrypt/ root@NEW:/etc/letsencrypt/ && rsync -az /root/leo-backups/ root@NEW:/root/leo-backups/"
   ssh -A root@91.229.90.4 "scp /etc/systemd/system/assistant-news-sync.* root@NEW:/etc/systemd/system/"
   ```
3. **Сверка** `count_rows` на обоих серверах — должно совпасть один в один.
3а. **Заглушка.** С 04.11 на NEW включён `scripts/maintenance_mode.sh public`: getorbita.me уже смотрит
   на NEW, лендинг и юридические страницы работают, приложение — «на обслуживании». Перед деплоем:
   `bash /root/maintenance_mode.sh off` (вернёт сайты Орбиты; сертификат getorbita.me уже выпущен).
4. **Код.** В `.env` на Маке `DEPLOY_SSH_HOST=NEW`, затем `./scripts/deploy_prod_replace.sh`. Деплой:
   - ставит venv и сервисы, включая `assistant-worker`;
   - поднимает Telegram Bot API;
   - настраивает nginx;
   - запускает всё.
5. **DNS.** A-записи `assistent.networ.ru`, `getorbita.me`, `www.getorbita.me` → `NEW`. Подождать 5 минут.
   `DEPLOY_SSH_HOST=NEW` в локальном `.env` — в этот же момент (и не раньше).
6. **Проверки:**
   - `curl https://assistent.networ.ru/healthz` → 200, бот подаёт сигнал;
   - вход в Орбиту, открыть заметку, создать и удалить напоминание;
   - написать боту в Telegram и получить ответ;
   - в журнале нет ошибок: `journalctl -u assistant-bot -u assistant-usage --since -10min | grep -iE 'error|traceback'`;
   - в Sentry нет новых ошибок.
7. **Сертификаты.** Перенесены вместе с `/etc/letsencrypt` → `certbot renew --dry-run`.
   Если не перенеслись — после DNS: `certbot --nginx -d assistent.networ.ru` и `-d getorbita.me -d www.getorbita.me`.
8. **Бэкапы.** `systemctl is-active litestream`; `python3 /opt/assistant/scripts/backup_s3.py list` — утром
   появится ночная копия; `journalctl -u litestream --since -10min` без ошибок.
9. **Таймер «Рынка».** `systemctl daemon-reload && systemctl enable --now assistant-news-sync.timer`.

## Если что-то пошло не так — откат (5 минут)

1. На новом сервере: `systemctl stop assistant-bot assistant-worker assistant-usage executive-board-bot`.
2. DNS обоих доменов → `91.229.90.4`; `DEPLOY_SSH_HOST` в `.env` вернуть.
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
- Мониторинг Selectel (CPU/RAM/диск) с оповещением: включить в панели.
- Внешний пинг `/healthz` раз в 5 минут.

## Бэкапы: что и где

| Что | Где | Сколько хранится |
|---|---|---|
| Litestream — каждая запись в базы, задержка ~1 с | `orbita-backups/litestream/<сервер>/<база>` | 72 часа, снимок раз в 6 ч |
| Ночная копия 03:30: все базы + `state.tar.gz` (токены OAuth, `.env`, `data/users`, json-файлы) | `orbita-backups/<сервер>/daily/<время>/` и `/root/leo-backups/daily` | 30 ночей в S3, 14 на диске |
| Перед каждой выкаткой | `/root/leo-backups/predeploy` (только диск) | 10 |

`<сервер>` — имя машины (`hostname`), на новом — `orbita`. Список копий: `python3 scripts/backup_s3.py list`.

## Восстановление из S3 (если умер сервер) — ~1 час

1. Новый сервер: `provision_server.sh`, затем деплой с Мака (ставит код, сервисы, Litestream).
2. Сразу остановить сервисы **и litestream** (чтобы пустые базы не ушли в S3 поверх живых копий):
   `systemctl stop assistant-bot assistant-worker assistant-usage executive-board-bot litestream`.
3. Восстановить в пустой каталог и проверить (ключи S3 уже в `.env` после деплоя):
   ```bash
   cd /opt/assistant && python3 scripts/restore_from_s3.py /root/restore --prefix orbita
   ```
   Скрипт берёт последнюю ночную копию с файлами состояния, поверх — свежие базы из Litestream,
   делает `quick_check` и печатает число строк.
4. Скопировать: `rsync -a /root/restore/ /opt/assistant/` и удалить старые `*-wal`/`*-shm` рядом с базами.
5. Если имя нового сервера другое — в `/opt/assistant/.env` `S3_BACKUP_PREFIX=orbita` не ставить
   (иначе новые копии лягут поверх старых); лучше оставить новое имя.
6. `systemctl start litestream assistant-bot assistant-worker assistant-usage executive-board-bot`, затем DNS.

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
