# Telegram-бот + Битрикс24

Бот работает в режиме **long polling** (входящий вебхук Telegram не используется). Для вызовов Битрикс24 используется **входящий вебхук REST** (`BITRIX_WEBHOOK` или `BITRIX24_WEBHOOK_URL`).

## Для нового чата в Cursor / другого агента

1. Открой эту папку как workspace в Cursor.
2. Прочитай **[AGENTS.md](AGENTS.md)** — краткая карта проекта, точка входа, секреты, где искать логику.
3. Скопируй **[.env.example](.env.example)** в `.env` и заполни (токены не коммитить).
4. Установи зависимости: `python3 -m pip install -r requirements.txt` (лучше в venv).

## Структура репозитория

| Файл / каталог | Назначение |
|----------------|------------|
| [bot.py](bot.py) | Весь код бота (хендлеры, интеграции, GPT, напоминания) |
| [usage_server.py](usage_server.py) | Дашборд usage, OAuth-колбэки, API и статика мини-приложения |
| [webapp/](webapp/) | Статика Web App «Скиллы / Профиль» (URL `/webapp/`). PWA: `manifest.webmanifest` + `sw.js` (кэш оболочки) |
| [requirements.txt](requirements.txt) | Зависимости Python |
| [.env.example](.env.example) | Шаблон переменных окружения |
| [oauth_setup.py](oauth_setup.py) | Первичная авторизация Google Calendar → `token.json` |
| [AGENTS.md](AGENTS.md) | Ориентир для ИИ/разработчика |
| `reminders.json` | Создаётся при работе напоминаний (в `.gitignore`) |
| `contacts.json` | Локальные контакты для календаря (в `.gitignore`) |

## Требования

- Python **3.9+** (на хостинге Beget обычно доступен `python3`).
- Файлы проекта: `bot.py`, `requirements.txt`, рядом с `bot.py` — файл **`.env`** (см. ниже).

## Установка на сервере (Beget и аналоги)

1. Загрузите каталог с ботом на сервер (SFTP, файловый менеджер панели и т.д.).

2. Подключитесь по SSH и перейдите в каталог с `bot.py`:

   ```bash
   cd /path/to/your/bot
   ```

3. Рекомендуется виртуальное окружение:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Установите зависимости:

   ```bash
   python3 -m pip install --upgrade pip
   python3 -m pip install -r requirements.txt
   ```

   Опционально для встроенного планировщика напоминаний (JobQueue):

   ```bash
   python3 -m pip install "python-telegram-bot[job-queue,socks]>=21.0,<23.0"
   ```

   Без `job-queue` напоминания всё равно работают через fallback в коде.

5. Создайте файл **`.env`** в том же каталоге, что и `bot.py` (можно скопировать из `.env.example` и заполнить):

   - `TELEGRAM_BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
   - `BITRIX_WEBHOOK` или `BITRIX24_WEBHOOK_URL` — URL входящего вебхука Битрикс24 (без пробела в конце, без лишних кавычек)
   - при необходимости: `BITRIX24_USER_ID`, `BITRIX24_PROJECT_ID`

## Запуск бота

Из каталога с `bot.py`, с активированным venv (если используете):

```bash
python3 bot.py
```

В консоли должны появиться сообщения вида `[startup] …`. Остановка: `Ctrl+C`.

Чтобы процесс не завершался после выхода из SSH, можно запустить в фоне (минимальный вариант без Docker):

```bash
nohup python3 bot.py >> bot.log 2>&1 &
```

Проверяйте вывод в `bot.log` или в панели хостинга.

## Если бот «застрял» в диалоге

Незавершённые шаги (календарь, напоминание, заметка, бронь и т.д.) хранятся в памяти процесса.

1. Напишите **`/cancel`** или **`отмена`** / **`стоп`** — сброс всех активных диалогов в этом чате.
2. Если шла **бронь стола** и нужно отменить заявку: **`/booking_cancel`** (сброс черновика брони — тоже через `/cancel`).
3. **Не нажимайте** старые inline-кнопки под прошлыми сообщениями — напишите запрос **новым** сообщением (не reply).
4. Состояние календаря само протухает через `CALENDAR_STATE_TTL_SEC` (по умолчанию 1800 с), если бот не перезапускали.

Администратор: полный сброс памяти у всех пользователей — `sudo systemctl restart assistant-bot` (см. [docs/DEPLOY_BEGET_TERMIUS.md](docs/DEPLOY_BEGET_TERMIUS.md)).

## LLM-роутер скиллов (GPT-first)

По умолчанию бот классифицирует запрос через OpenRouter (`INTENT_ROUTER_ENABLED=1` в коде; отключить: `INTENT_ROUTER_ENABLED=0` в `.env`).

- Модель: `OPENROUTER_MODEL_ROUTER` (по умолчанию `google/gemini-2.5-flash`).
- На каждое сообщение добавляется **~200–500 ms** и один дешёвый вызов LLM; для календаря по-прежнему второй вызов — парсер полей встречи.
- В логах процесса ищите строки `[intent_route]` — там видно, что выбрал regex, что LLM, и итог.
- Если бот «не понял» или ответил не тем скиллом: **`/cancel`**, затем переформулируйте; при отключённом роутере смысл режут только regex-шаблоны.

## Прокси для Telegram (РКН-блокировка)

Если из вашей сети `api.telegram.org` недоступен, есть два простых варианта обхода — никаких Docker/nginx не требуется.

### Вариант A. Системный VPN на Mac

Включите свой VPN-клиент (он туннелирует **весь** трафик системы). В `.env` оставьте `TELEGRAM_PROXY_URL` пустым / закомментированным. На старте в логах увидите:

```
[startup] telegram_proxy=none telegram_get_updates_proxy=none
```

Бот будет ходить в Telegram через VPN. Запросы к Битрикс24, Obuchat и OpenRouter тоже пойдут через VPN — это нормально для локального теста.

### Вариант B. Локальный прокси-порт (без системного VPN)

Подходит для клиентов **V2RayN / V2Box / Shadowsocks / Outline / Clash / sing-box** и подобных — у них в настройках обычно есть пункт «Local SOCKS5 / HTTP proxy» с адресом вида `127.0.0.1:1080` (SOCKS5) или `127.0.0.1:1087` (HTTP).

1. В клиенте включите/посмотрите локальный порт (например, `1080`).
2. В `.env` добавьте строку (одну из):

   ```
   TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080
   ```

   ```
   TELEGRAM_PROXY_URL=http://127.0.0.1:1087
   ```

   Если прокси с авторизацией:

   ```
   TELEGRAM_PROXY_URL=socks5://login:pass@proxy.example.com:1080
   ```

3. Опционально — отдельный прокси для long polling (если хочется разнести): переменная `TELEGRAM_GET_UPDATES_PROXY_URL`. По умолчанию используется тот же URL, что и `TELEGRAM_PROXY_URL`.

4. Запустите бота: `python3 bot.py`. В логах должно появиться:

   ```
   [startup] telegram_proxy=configured telegram_get_updates_proxy=configured
   ```

Запросы к Битрикс24, Obuchat и OpenRouter идут **напрямую** (через `requests`) — прокси на них не влияет.

### Быстрый тест прокси из терминала

Без запуска бота, через `curl`:

```bash
curl --proxy socks5://127.0.0.1:1080 \
  https://api.telegram.org/bot<ВАШ_ТОКЕН>/getMe
```

Ответ должен содержать `"ok": true`. Если получили ошибку соединения — проблема в самом прокси/VPN, не в боте.

### Зависимости для SOCKS5

Поддержка `socks5://` в `python-telegram-bot` включается экстра-пакетом `[socks]` (он уже прописан в [requirements.txt](requirements.txt) как `python-telegram-bot[socks]`). Если ставили зависимости раньше — обновите:

```bash
python3 -m pip install -r requirements.txt --upgrade
```

## Мини-приложение «Скиллы и профиль»

Если на том же HTTPS-хосте, что и [usage_server.py](usage_server.py) (uvicorn), уже проксируются маршруты приложения:

1. В `.env` у бота и у сервера usage задайте **`WEBAPP_PUBLIC_URL`** — базовый URL **без** завершающего слэша, только **HTTPS** (например `https://assistant.obuchat.me`).
2. Перезапустите **usage_server** и **бот**: статика открывается по `{WEBAPP_PUBLIC_URL}/webapp/`, API — `/api/miniapp/…` (авторизация по заголовку `Authorization: tma <initData>` из Telegram WebApp).
3. В [@BotFather](https://t.me/BotFather) для бота укажите домен Web App, совпадающий с этим хостом.
4. В чате: кнопка меню «Скиллы и профиль» (если задан URL) или команда **`/app`** с inline-кнопкой Web App.

Подключение Google Calendar, Todoist и Zoom из мини-приложения открывает OAuth в **внешнем браузере** (`openLink`); после успеха закройте вкладку и обновите мини-приложение.

**Обычный браузер:** откройте `{WEBAPP_PUBLIC_URL}/webapp/` — на экране входа кнопка **Log in with Telegram** (официальный Login Widget). В [@BotFather](https://t.me/BotFather) для бота выполните **`/setdomain`** и укажите домен мини-приложения (тот же, что в `WEBAPP_PUBLIC_URL`, без пути). Сессия хранится в `localStorage` браузера. Внутри Telegram по-прежнему используется `initData` Web App.

**PWA (добавить на домашний экран):** после первого открытия браузер может сохранить приложение. Service Worker кэширует HTML/CSS/JS и иконки, а последний снимок заметок/напоминаний показывается сразу при открытии, пока данные обновляются с сервера в фоне.

### Zoom OAuth

1. В [Zoom Marketplace](https://marketplace.zoom.us/develop/apps) создайте **General app** с **User-managed OAuth** (не Server-to-Server).
2. **Scopes** (в приложении и в `.env` как `ZOOM_OAUTH_SCOPE`): встречи + `user:read:user` + `cloud_recording:read:recording` (если нужны записи по webhook).
3. **Redirect URL** (Development и Production): `https://<ваш-домен>/oauth/zoom/callback` — тот же URL, что `ZOOM_OAUTH_REDIRECT_URI`.
4. **Home URL** (если Zoom требует): `https://<ваш-домен>/zoom/home` (`ZOOM_HOME_URL`).
5. **Для всех пользователей бота**, а не только владельца приложения Zoom:
   - В Marketplace нажмите **Activate** / опубликуйте приложение в **Production** (можно **Unlisted**, без публичного листинга).
   - В `.env` на сервере укажите **Production** Client ID и Client Secret (вкладка **Production** → App Credentials; они **отличаются** от Development).
   - Redirect URL продублируйте во вкладке **Production** → OAuth Information.
6. Пока приложение только в **Development**: OAuth доступен лишь аккаунту, на котором создано приложение, и email’ам из списка тестовых пользователей. Иначе Zoom показывает **«Application not found»** — это не ошибка бота.
7. Event Subscriptions: `ZOOM_WEBHOOK_URL` → `POST /webhook`, Secret Token → `ZOOM_WEBHOOK_SECRET`. В Zoom Marketplace включите:
   - **`recording.completed`** — облачные записи (Zoom Pro+);
   - **`meeting.ended`** — напоминание отправить локальный файл (бесплатный Zoom).
8. **Zoom Pro+ / cloud recording:** на встрече включите Cloud Recording. После `recording.completed` бот скачает файл, сделает транскрипцию в Telegram и задачу в Todoist (`OBUCHAT_TRANSCRIBE`, `ZOOM_RECORDING_TRANSCRIBE=1` по умолчанию).
9. **Zoom Free / локальная запись:** после окончания встречи придёт сообщение в Telegram. Отправьте MP4/M4A боту **файлом** (можно без подписи в течение `ZOOM_LOCAL_PENDING_TTL_SEC`, по умолчанию 48 ч). Файл обычно в «Документы/Zoom» на Mac. Запасной вариант — файл с подписью «транскрипция». Запись появится в мини-приложении (Профиль → Zoom) с меткой «локальная».

После смены Client ID/Secret на сервере: `sudo systemctl restart assistant-usage assistant-bot`.

## Google Calendar (личный аккаунт через OAuth)

Бот умеет создавать/переносить встречи и подсказывать свободные слоты в вашем личном Google Calendar. Доступ настраивается одноразово через OAuth.

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/), создайте проект и включите **Google Calendar API** (APIs & Services → Library → Google Calendar API → Enable).
2. На вкладке **OAuth consent screen** выберите **External**, заполните минимальные поля (название, email) и в разделе **Test users** добавьте ваш Google-аккаунт. Достаточно режима **Testing**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**.
   - Скачайте JSON и сохраните рядом с `bot.py` под именем `client_secret.json`.
4. В корне проекта (рядом с `bot.py`) выполните:

   ```bash
   python3 oauth_setup.py
   ```

   Скрипт откроет браузер; авторизуйтесь под нужным Google-аккаунтом. После успеха появится файл `token.json` (его можно переносить вместе с проектом — `refresh_token` уже внутри).
5. В `.env` (или `.env.example`) при необходимости настройте:
   - `GOOGLE_CALENDAR_ID` — обычно `primary` (по умолчанию).
   - `CALENDAR_TZ` — например, `Europe/Moscow`.
   - `CALENDAR_WORK_START` / `CALENDAR_WORK_END` — окно для расчёта свободных слотов (по умолчанию `09:00`–`20:00`).
   - `GOOGLE_CREDENTIALS_PATH` / `GOOGLE_TOKEN_PATH` — если файлы лежат не рядом с `bot.py`.
6. Перезапустите `python3 bot.py`.

Файлы `client_secret.json` и `token.json` уже включены в `.gitignore` — не коммитьте их.

### Что умеет бот в календаре

- «Поставь встречу с Артёмом завтра в 15:00 на час, обсудить ОКР, artem@example.com» — создаёт событие и присылает ссылку.
- «Поставь встречу с Димой на ближайший свободный слот» / «… на ближайший слот завтра» — подбирает первое свободное окно в рабочем дне (`CALENDAR_WORK_START`–`CALENDAR_WORK_END`); если на указанный день окон нет — предложит ближайший день.
- Гости по **имени**, **email** или **@нику Telegram** из адресной книги (`contacts_user/{ваш_telegram_id}.json`, см. также `contacts.json` для legacy-владельца).
- «Перенеси встречу с Артёмом завтра на 17:00» — находит подходящее событие и обновляет время. Если встреч несколько — кнопки `14:00-15:00 • Название` под сообщением (можно также ответить «1» / «первая»).
- «Какие у меня встречи завтра» / «свободные слоты в пятницу с 11 до 18» — присылает список событий или окон.
- В личке: можно сделать reply на сообщение/файл/голосовое с командой «поставь встречу по этому контексту» — бот возьмёт описание из reply (для голосового — сначала транскрибирует).
- В группе: reply + `@PshAI_bot поставь встречу` (или другая фраза-триггер) с reply на сообщение с описанием.

## Логи в консоль

Скрипт пишет в stdout:

- при старте;
- при каждом входящем текстовом сообщении (краткий превью текста);
- при успешном создании задачи в Битрикс24 (ID и название).

Пути в коде **относительные к каталогу `bot.py`** (через `__file__`), без привязки к Windows или macOS.
