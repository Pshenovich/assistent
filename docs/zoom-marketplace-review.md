# Проверка приложения Orbita в Zoom Marketplace — пошагово

Зачем: пока приложение не прошло проверку, подключить Zoom к Орбите могут только люди из **вашего аккаунта Zoom**. После проверки — любой пользователь Zoom.

Сколько ждать: обычно 1–4 недели. Проверяющие могут вернуть заявку с вопросами — отвечаете и подаёте снова.

Названия кнопок у Zoom иногда меняются — ищите похожие по смыслу. Непонятно — пришлите Claude скриншот.

---

## Development и Production — что это и что трогаем

Вверху страницы приложения есть переключатель **Development / Production**. Это два отдельных набора настроек одного приложения:

| | Development | Production |
|---|---|---|
| Что это | «Черновик» — то, что работает у вас сейчас | «Чистовик» — то, что увидит проверяющий и все пользователи после проверки |
| Ключи (Client ID / Secret) | свои | **другие**, свои |
| Кто может подключить | только люди из вашего аккаунта Zoom | все — после проверки |

**Правило: Development не трогаем.** На нём сейчас работает Zoom у вас и коллег. Всё, что ниже, — заполняем на **Production**.

После одобрения Claude переключит сервер на ключи Production. Тем, кто уже подключил Zoom, нужно будет один раз переподключить его (кнопка в Профиле) — сейчас таких единицы.

**Новое приложение заводить не нужно:** Production этого же приложения — и есть чистый лист. Новое имеет смысл, только если текущее создано в чужом аккаунте Zoom (например, у бывшего сотрудника). Тогда скажите Claude.

---

## Что готово

- [x] Домен `getorbita.me`, HTTPS, лендинг, `/privacy`, `/terms`.
- [x] Почта `support@getorbita.me`.
- [x] Страница помощи `https://getorbita.me/zoom` — как подключить, пользоваться, отключить.
- [x] Вход для проверяющего: код в поле «Войти по коду» (строка `REVIEW_LOGIN_CODE` в вашем локальном `.env`).
- [x] Сервер принимает Zoom на `https://getorbita.me/oauth/zoom/callback` и `https://getorbita.me/webhook`.
- [ ] Реквизиты компании в политике и условиях (юрист).
- [ ] 2–3 скриншота Орбиты 1200×780 (см. шаг 5).

---

## Шаг 1. Открыть приложение на Production

1. https://marketplace.zoom.us → войдите аккаунтом, где создано приложение.
2. Вверху справа **Develop** → **Build App** (или **Manage** → **Created Apps**) → откройте приложение Орбиты.
3. Вверху переключите на **Production**.

## Шаг 2. Basic Info

| Поле | Что сделать |
|---|---|
| App name | `Orbita` |
| App type / management | User-managed — не менять |
| Client ID / Client Secret (Production) | Ничего не делать, Claude попросит их после одобрения |
| OAuth Redirect URL | `https://getorbita.me/oauth/zoom/callback` |
| OAuth Allow List | Тот же адрес `https://getorbita.me/oauth/zoom/callback`. Другие адреса здесь удалите (только на Production!) |
| Use Strict Mode URL, Subdomain check | Выключено |

Нажмите **Save** (или **Continue**).

## Шаг 3. Features → Access (уведомления)

Нам нужно одно уведомление — когда пользователь удаляет приложение в Zoom, Орбита стирает его токены.

1. **Event Subscription** — включите (или **+ Add Event Subscription**).
2. Subscription name: `Orbita`.
3. Event notification endpoint URL: `https://getorbita.me/webhook`.
4. **Add Events** → найдите **App Deauthorized** (раздел App / «Приложение») → отметьте → Done.
5. Нажмите **Validate** рядом с адресом → должно стать «Validated». Не стало — скриншот Claude (у Production может быть свой Secret Token).
6. Если в подписке есть **Recording Completed**, **Meeting Ended** или другие события — **уберите**: облачные записи Орбита пока не обрабатывает, лишние события = вопросы на проверке.
7. **Save**.

Если поле **Deauthorization Notification Endpoint URL** есть отдельно — впишите `https://getorbita.me/webhook/deauthorize`.

Остальное в Features (Surface, Embed, Connect) — не включать.

## Шаг 4. Scopes

Нажмите **+ Add Scopes** / удалите лишнее, чтобы осталось **ровно 5**. Для каждого Zoom просит описание — вставьте текст справа:

| Scope | Scope description |
|---|---|
| `meeting:write:meeting` | Orbita creates a Zoom meeting when the user schedules a meeting with a Zoom link in Orbita or asks the Leo assistant to do it. |
| `meeting:read:list_meetings` | When the user asks to reschedule or cancel a Zoom meeting, Orbita looks it up among the user's upcoming meetings; it also reuses an existing link instead of creating a duplicate. |
| `meeting:update:meeting` | Orbita updates the Zoom meeting time when the user reschedules the event in Orbita. |
| `meeting:delete:meeting` | Orbita deletes the Zoom meeting when the user cancels the event in Orbita. |
| `user:read:user` | Orbita shows which Zoom account is connected (name and email) in the user's profile. |

**Удалить:** `user:read:token` (был для бота Vexa), любые `cloud_recording:...`, `recording:...` и всё остальное. Сомневаетесь — скриншот Claude.

## Шаг 5. App Listing

**Basic / Description:**

| Поле | Что вписать |
|---|---|
| Short description | `Schedule, reschedule and cancel Zoom meetings from your AI workspace.` |
| Long description | текст ниже |
| Category | Productivity |
| Icon | `docs/google-verification/orbita-logo-120.png` (если попросит крупнее — скажите Claude, сделает) |
| Gallery (скриншоты) | 2–3 картинки **1200×780**: экран новой встречи с выбором Zoom в «Видеосвязь»; встреча со ссылкой Zoom; Профиль → Zoom «подключён». Сделайте скриншоты, Claude подгонит размер |

**Long description:**
> Orbita is an AI workspace for meetings, tasks and notes, with the Leo assistant.
>
> With the Zoom integration you can:
> - add a Zoom link when you create a meeting in Orbita, or just ask Leo: "Create a Zoom meeting tomorrow at 3 pm with Anna";
> - reschedule or cancel the meeting in Orbita — the Zoom meeting is updated or deleted too.
>
> Disconnect at any time in Orbita (Profile → Zoom → Disconnect) or in the Zoom App Marketplace; your Zoom tokens are deleted immediately.

**Links & Support:**

| Поле | Адрес |
|---|---|
| Support URL | `https://getorbita.me/zoom` |
| Documentation URL | `https://getorbita.me/zoom` |
| Privacy Policy URL | `https://getorbita.me/privacy` |
| Terms of Use URL | `https://getorbita.me/terms` |
| Support email (если есть поле) | `support@getorbita.me` |
| Configure URL | оставить пустым |

**EU & Discoverability:**
- **Set my app as "Unlisted"** — включите. Приложение не будет в поиске Marketplace, подключение работает по кнопке из Орбиты. Сделать публичным можно потом в любой момент без новой проверки.
- Вопросы про EU / trader (DSA) — пришлите Claude скриншот до ответа.

**Save**.

## Шаг 6. Technical Design

Анкета о безопасности. Отвечайте так; где вопрос другой или требует файлов (SSDLC, SAST/DAST) — скриншот Claude.

| Вопрос | Ответ |
|---|---|
| Where is data hosted | Dedicated server in Russia (Beget), single region |
| Encryption in transit | Yes, TLS 1.2+, HTTPS only |
| What Zoom data do you store | OAuth tokens and the Zoom account name/email; IDs and links of meetings created by Orbita. We do not access recordings, chats or participants |
| Token storage | **уточните у Claude перед ответом** |
| Data deletion | Tokens are deleted when the user disconnects in Orbita or removes the app in Zoom (app_deauthorized event) |
| Employee access | Only on the user's request (support) or for security |
| Third parties | None for Zoom data |

## Шаг 7. Publish / Submit

1. Слева **Publish** (или **Submit**). Zoom покажет список незаполненных полей — дозаполните.
2. **Domain verification** — домен `getorbita.me`. Выберите **TXT record**: Zoom покажет строку, добавьте её в DNS Beget так же, как для Google (вторая TXT-запись, старые не трогать). Скажите Claude — он проверит, что запись видна. Затем **Verify**.
3. **Release notes / Test plan** — вставьте (код возьмите из `.env`, строка `REVIEW_LOGIN_CODE`):

```
Test account: open https://getorbita.me/webapp/ and enter the code <КОД> in the "Войти по коду" (Sign in with code) field. No Telegram needed.

1. Profile tab (bottom right) → Zoom → "Подключить Zoom" (Connect Zoom) → authorize with your Zoom account.
2. On the "Zoom is connected" page click "Back to Orbita".
3. Create a meeting: first tab → "+" (Add) → "Встреча" (Meeting) → set a time → "Видеосвязь" (Video call) → Zoom → save. A Zoom link appears in the event.
   Or "+" → "Лео" (Leo) and type: "Create a Zoom meeting tomorrow at 3 pm".
4. Move the meeting to another time — the Zoom meeting time changes too.
5. Delete the meeting — the Zoom meeting is deleted.
6. Profile → Zoom → "Отключить Zoom" (Disconnect) — tokens are deleted.

The interface is in Russian; English labels are given in parentheses.
Help page: https://getorbita.me/zoom
```

4. **Test account credentials** — если есть отдельные поля: login — `https://getorbita.me/webapp/`, password/code — ваш код.
5. Отметьте согласие с **Marketplace Developer Agreement** → **Submit**.

Ответы придут на почту аккаунта Zoom — проверяйте её и `support@getorbita.me`, папку «Спам» тоже.

---

## Важно

- Пока идёт проверка, у вас и коллег Zoom работает как раньше (на ключах Development).
- Zoom с 2022 года не продаёт в России. Сама проверка бесплатная; если спросят страну и реквизиты компании — пришлите Claude вопрос до ответа.
