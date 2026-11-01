# Видео для проверки Google — сценарий по шагам

Длина: 3–4 минуты. Одним дублем, без монтажа. Голос не нужен: английские подписи (в кавычках у каждого шага) вставите субтитрами на YouTube — «Субтитры» → «Добавить» → «Ввести вручную».

## Подготовка (один раз, ~10 минут)

1. **Тестовый Google-аккаунт.** Отдельный Gmail, не личный — в кадре будут его почта и календарь. Создаёте вы.
2. **Браузер — Chrome**, интерфейс Google в этом аккаунте — **английский** (myaccount.google.com → Personal info → Language → English). Проверяющие ждут экран разрешений на английском.
3. **Вход в Орбиту без Telegram:** откройте `https://getorbita.me/webapp/` → «Войти по коду» → код проверяющего (его даёт Claude, он же в заявке Zoom). Это пустой тестовый аккаунт — в кадре не будет ваших данных.
4. **Две встречи в календаре тестового Gmail**, чтобы в кадре было не пусто: на calendar.google.com создайте «Team sync» сегодня через пару часов и «Client call» завтра в 11:00.
5. Откройте **три вкладки** заранее:
   - `https://getorbita.me` (лендинг);
   - `https://getorbita.me/webapp/` (Орбита, уже вошли по коду);
   - `https://calendar.google.com` под тестовым аккаунтом.
6. Закройте лишние вкладки и уведомления (macOS: «Не беспокоить»).
7. Запись: `Cmd + Shift + 5` → «Записать весь экран» → «Записать».

## Сценарий

| # | Что делаете | Подпись (субтитр) |
|---|---|---|
| 1 | Вкладка `getorbita.me`. Медленно прокрутите лендинг, внизу покажите ссылки Privacy и Terms | "This is Orbita — an AI assistant for meetings, tasks and notes. Home page: getorbita.me, with links to our Privacy Policy and Terms." |
| 2 | Вкладка Орбиты. Нижняя панель → **Профиль** → **Google Календарь** | "The user is signed in to Orbita and opens Profile → Google Calendar to connect their Google account." |
| 3 | Нажмите **«Подключить Google Календарь»** | "The user clicks Connect Google Calendar." |
| 4 | Откроется Google. Выберите тестовый аккаунт. **Кликните в адресную строку**, чтобы был виден весь адрес с `client_id=…`, **задержитесь на 3 секунды** | "Google's consent screen. The OAuth client ID is visible in the address bar." |
| 5 | На экране разрешений: видно название **Orbita** и две строки — Calendar и Tasks. Задержитесь на 3 секунды. Нажмите **Continue / Allow** | "Orbita requests two scopes: Google Calendar and Google Tasks. The user grants access." |
| 6 | Откроется страница «Google Calendar подключён». Нажмите **«Вернуться в Orbita»** | "Access granted. The user returns to Orbita." |
| 7 | В Орбите — вкладка с расписанием на сегодня: видны встречи из Google Календаря | "calendar scope: Orbita shows the user's schedule from Google Calendar." |
| 8 | Откройте чат с Лео и вставьте: `Create a meeting tomorrow at 3 pm "Demo call" with demo@example.com` — дождитесь ответа и подтвердите | "calendar scope: at the user's request, Leo creates an event and invites the attendee." |
| 9 | Вкладка Google Calendar → обновите → покажите встречу «Demo call» завтра в 15:00 | "The event appears in the user's Google Calendar." |
| 10 | Обратно в Лео: `Move "Demo call" to 4 pm` → подтвердите → в Google Calendar встреча на 16:00 | "calendar scope: rescheduling at the user's request." |
| 11 | Лео: `When am I free tomorrow?` | "calendar scope: finding free time across the user's calendars." |
| 12 | Лео: `Add a task "Send the proposal" for tomorrow` → откройте `https://tasks.google.com` (или боковую панель Tasks в Google Calendar) → задача там | "tasks scope: tasks created in Orbita appear in the user's Google Tasks." |
| 13 | В Google Tasks отметьте задачу выполненной → в Орбите обновите список задач → она закрыта | "Completing the task in Google Tasks marks it done in Orbita." |
| 14 | Лео: `Cancel "Demo call"` → подтвердите → в Google Calendar встречи нет | "calendar scope: cancelling an event at the user's request." |
| 15 | Профиль → Google Календарь → **«Отключить Google Календарь»** | "The user can disconnect Google at any time; Orbita deletes the access tokens." |
| 16 | Откройте `https://myaccount.google.com/permissions` — Orbita в списке нет (или нажмите Remove access) | "Access can also be revoked in the Google Account settings." |

Остановите запись: значок ■ в строке меню.

## Загрузка

YouTube → «Создать» → «Добавить видео» → видимость **«Доступ по ссылке» (Unlisted)**. Добавьте субтитры из таблицы. Ссылку вставьте в заявку (Verification Center).

## Если что-то пошло не так

- Лео ответил по-русски — нормально, подпись всё объясняет. Можно писать ему и по-русски.
- Ошибка или зависание — остановите запись, пришлите Claude скриншот, переснимите после исправления.
- Перед повторной съёмкой: Профиль → Отключить Google, затем myaccount.google.com/permissions → Orbita → Remove access, чтобы Google снова показал экран разрешений.
