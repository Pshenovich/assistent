# Проверка приложения Orbita в Zoom Marketplace — пошагово

Зачем: пока приложение Zoom не прошло проверку, подключить Zoom к Орбите могут только люди из **вашего же аккаунта Zoom**. Остальные увидят ошибку. После проверки — любой пользователь Zoom.

Сколько ждать: обычно 1–4 недели, проверяющие могут вернуть заявку с вопросами — отвечаете и подаёте снова.

Названия кнопок у Zoom иногда меняются — ищите похожие по смыслу.

---

## Что должно быть готово до подачи

- [x] Домен `getorbita.me` с HTTPS, лендинг, `/privacy`, `/terms`.
- [x] Почта `support@getorbita.me`.
- [ ] **Страница помощи по Zoom** — `https://getorbita.me/zoom`: как подключить, как пользоваться, как отключить. Zoom требует её обязательно. *Делает Claude.*
- [ ] **Тестовый вход для проверяющего** — проверяющий из Zoom должен зайти в Орбиту и сам подключить Zoom. Нужен вход по почте и паролю/коду без Telegram. *Делает Claude, логин и пароль вы впишете в заявку.*
- [ ] **Адреса Zoom переведены на getorbita.me** (вход и уведомления). Поменять адрес после проверки = новая проверка, поэтому сразу новый домен. *Делает Claude вместе с вами, см. шаг 2.*
- [ ] Реквизиты в политике и условиях (юрист) — та же задача, что для Google.

---

## Шаг 1. Найти приложение

1. Откройте https://marketplace.zoom.us и войдите тем аккаунтом Zoom, в котором создавали приложение.
2. Вверху справа: **Develop** → **Build App** (или **Manage** → **Created Apps**).
3. Откройте приложение, через которое Орбита подключает Zoom (тип — *General App*, *User-managed*).

## Шаг 2. Basic Information (основное)

| Поле | Что вписать |
|---|---|
| App name | `Orbita` |
| OAuth Redirect URL | `https://getorbita.me/oauth/zoom/callback` — **меняем только вместе с Claude**: в тот же момент он переключит адрес на сервере, иначе подключение Zoom сломается |
| OAuth Allow List | `https://getorbita.me/oauth/zoom/callback` и адрес, который там стоит сейчас, — **не удаляйте его** до перехода |
| Deauthorization Notification Endpoint URL | `https://getorbita.me/webhook/deauthorize` |

Когда пользователь удаляет приложение в Zoom, Zoom шлёт уведомление на этот адрес, и Орбита удаляет его токены. Это уже сделано в коде.

## Шаг 3. Features → Event Subscriptions (уведомления)

1. Подписка должна быть **включена**. Endpoint URL: `https://getorbita.me/webhook`.
2. Нажмите **Validate** — должно появиться «Validated». Если нет — напишите Claude.
3. События оставить только:
   - **Recording Completed** — запись встречи в облаке Zoom готова, Орбита делает расшифровку и саммари;
   - **Meeting Ended** — встреча закончилась.

## Шаг 4. Scopes (разрешения)

Должно быть ровно это. Для каждого Zoom просит объяснение — готовые тексты справа.

| Scope | Объяснение для Zoom |
|---|---|
| `meeting:write:meeting` | Orbita creates Zoom meetings when the user schedules a meeting in Orbita or asks the Leo assistant to do it. |
| `meeting:read:list_meetings` | When the user asks to reschedule or cancel a Zoom meeting, Orbita looks it up among the user's upcoming meetings; it also reuses an existing link instead of creating a duplicate meeting. |
| `meeting:update:meeting` | Orbita reschedules a Zoom meeting when the user moves the event in Orbita. |
| `meeting:delete:meeting` | Orbita cancels the Zoom meeting when the user cancels the event in Orbita. |
| `user:read:user` | Orbita shows which Zoom account is connected (name and email) on the integrations screen. |
| `cloud_recording:read:...` — **только если есть в списке** | When a cloud recording is completed, Orbita downloads it to create a transcript and meeting summary for the user. |

**`user:read:token` — удалить.** Он был нужен боту-записывателю Vexa, который мы не используем. Лишнее разрешение = лишние вопросы на проверке. Если когда-нибудь вернём Vexa — добавим снова.

Если в списке есть что-то ещё — скажите Claude, прежде чем удалять.

## Шаг 5. App Listing (карточка в магазине)

| Поле | Что вписать |
|---|---|
| Short description | `AI assistant for meetings: schedule Zoom calls, get transcripts, summaries and tasks.` |
| Long description | Текст ниже |
| Category | Productivity (и, если можно второй, — Collaboration) |
| Icon / logo | `docs/google-verification/orbita-logo-120.png` (если попросят крупнее — скажите Claude) |
| Screenshots | 3–4 скриншота Орбиты: расписание с Zoom-встречей, саммари встречи, экран интеграций с подключённым Zoom. Можно сделать с телефона или компьютера |
| Privacy Policy URL | `https://getorbita.me/privacy` |
| Terms of Use URL | `https://getorbita.me/terms` |
| Support URL | `https://getorbita.me/zoom` |
| Documentation URL | `https://getorbita.me/zoom` |
| Support email | `support@getorbita.me` |

**Long description:**
> Orbita is an AI workspace for meetings, tasks and notes, with the Leo assistant.
> With the Zoom integration you can:
> - create, reschedule and cancel Zoom meetings right from Orbita or by asking Leo;
> - get a transcript, summary and action items after a cloud-recorded meeting.
> You can disconnect Zoom at any time in Orbita (Profile → Integrations) or in the Zoom App Marketplace; your Zoom tokens are deleted immediately.

## Шаг 6. Technical Design и Security (вопросы о безопасности)

Zoom задаёт вопросы анкетой. Типичные ответы:

| Вопрос | Ответ |
|---|---|
| Где хранятся данные / хостинг | Servers in Russia (Beget), single region |
| Шифрование при передаче | Yes, TLS 1.2+ (HTTPS only) |
| Шифрование хранения токенов | Tokens are stored on the server with access restricted to the service account; disk-level encryption — **уточните у Claude перед ответом** |
| Удаление данных при отключении | Yes, tokens are deleted on app_deauthorized event and on disconnect in the app |
| Сколько храним записи | Recordings are processed for transcription and not stored after processing; transcripts and summaries are kept until the user deletes them — **уточните у Claude перед ответом** |
| Доступ сотрудников к данным | Only on user request (support) or for security |
| Используете ли ИИ | Yes: transcription (Obuchat) and summaries via LLM providers through OpenRouter; data is not used to train models |

Если вопрос непонятен — пришлите Claude скриншот вопроса.

## Шаг 7. Тестовый доступ для проверяющего

В поле для проверяющих (Test credentials / Review notes) — то, что подготовит Claude:
- адрес входа: `https://getorbita.me/webapp/`;
- логин и пароль тестового аккаунта;
- короткая инструкция: «Profile → Integrations → Connect Zoom, then ask Leo: "Create a Zoom meeting tomorrow at 3 pm"».

Иногда Zoom просит видео (как у Google): вход → подключение Zoom → создание встречи → саммари после записи → отключение. 2–3 минуты, запись экрана.

## Шаг 8. Отправить

**Submit** (или **Publish** → **Submit for review**). Ответы придут на почту аккаунта Zoom — проверяйте и её, и `support@getorbita.me`.

---

## Важно

- Zoom с 2022 года не продаёт в России. Сама проверка бесплатная, но если Zoom спросит страну и реквизиты компании — пришлите Claude вопрос до ответа.
- Пока проверка идёт, у вас и коллег из вашего аккаунта Zoom всё продолжает работать.
