/* Почта в Orbita: подключение ящиков (мастера по провайдерам), разбор писем и окно письма в чате.
 * Общие помощники приложения — через window.LeoApp (см. конец app.js). */
(function () {
  "use strict";

  function app() {
    return window.LeoApp || {};
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function api(path, opts) {
    return app().apiFetch(path, opts || { method: "GET" });
  }
  function post(path, body, method) {
    return api(path, { method: method || "POST", body: JSON.stringify(body || {}) });
  }
  function toast(t) {
    if (app().toast) app().toast(t);
  }

  /* ——————————————————————————————— профиль: подключение ——————————————————————————————— */

  var PROVIDER_LABEL = { yandex: "Яндекс", gmail: "Gmail", mailru: "Mail.ru", imap: "Почта" };

  // Схемы экранов провайдеров: не скриншоты, а упрощённый рисунок с подсвеченной кнопкой (она пульсирует).
  var ILLO = {
    yandex_allow:
      '<div class="mail-illo-win"><div class="mail-illo-bar">oauth.yandex.ru</div><div class="mail-illo-page">' +
      '<b>Orbita</b> запрашивает доступ<div class="mail-illo-lines"><i></i><i></i></div>' +
      '<div class="mail-illo-acts"><span class="mail-illo-btn">Отмена</span><span class="mail-illo-btn mail-illo-hl">Разрешить</span></div></div></div>',
    yandex_imap:
      '<div class="mail-illo-win"><div class="mail-illo-bar">mail.yandex.ru · Почтовые программы</div><div class="mail-illo-page">' +
      '<div class="mail-illo-check mail-illo-hl">☑ С сервера imap.yandex.ru по протоколу IMAP</div>' +
      '<div class="mail-illo-check mail-illo-hl">☑ Пароли приложений и OAuth-токены</div>' +
      '<div class="mail-illo-acts"><span class="mail-illo-btn mail-illo-btn--fill">Сохранить изменения</span></div></div></div>',
    yandex_apppass:
      '<div class="mail-illo-win"><div class="mail-illo-bar">id.yandex.ru · Пароли приложений</div><div class="mail-illo-page">' +
      '<div class="mail-illo-row">Тип: <span class="mail-illo-btn mail-illo-hl">Почта</span></div>' +
      '<div class="mail-illo-row">Название: <span class="mail-illo-input">Orbita</span></div>' +
      '<div class="mail-illo-acts"><span class="mail-illo-btn mail-illo-btn--fill mail-illo-hl">Создать</span></div></div></div>',
    google_2fa:
      '<div class="mail-illo-win"><div class="mail-illo-bar">myaccount.google.com · Безопасность</div><div class="mail-illo-page">' +
      '<div class="mail-illo-row">Двухэтапная аутентификация <span class="mail-illo-btn mail-illo-hl">Включить</span></div>' +
      '<div class="mail-illo-lines"><i></i><i></i></div></div></div>',
    google_apppass:
      '<div class="mail-illo-win"><div class="mail-illo-bar">myaccount.google.com/apppasswords</div><div class="mail-illo-page">' +
      "<b>Пароли приложений</b>" +
      '<div class="mail-illo-row">Название приложения: <span class="mail-illo-input">Orbita</span></div>' +
      '<div class="mail-illo-acts"><span class="mail-illo-btn mail-illo-btn--fill mail-illo-hl">Создать</span></div></div></div>',
    google_copy:
      '<div class="mail-illo-win"><div class="mail-illo-bar">Пароль приложения создан</div><div class="mail-illo-page">' +
      '<div class="mail-illo-pass mail-illo-hl">abcd efgh ijkl mnop</div><div class="mail-illo-lines"><i></i></div></div></div>',
    mailru_add:
      '<div class="mail-illo-win"><div class="mail-illo-bar">id.mail.ru · Безопасность</div><div class="mail-illo-page">' +
      "<b>Пароли для внешних приложений</b>" +
      '<div class="mail-illo-acts"><span class="mail-illo-btn mail-illo-btn--fill mail-illo-hl">Добавить</span></div></div></div>',
    mailru_access:
      '<div class="mail-illo-win"><div class="mail-illo-bar">Новый пароль</div><div class="mail-illo-page">' +
      '<div class="mail-illo-row">Название: <span class="mail-illo-input">Orbita</span></div>' +
      '<div class="mail-illo-check mail-illo-hl">◉ Полный доступ к Почте</div>' +
      '<div class="mail-illo-acts"><span class="mail-illo-btn mail-illo-btn--fill">Продолжить</span></div></div></div>',
  };

  // Мастера: по одному действию на шаг. kind: info (ссылка + «Готово, дальше»), oauth, code, form.
  var WIZARDS = {
    yandex: {
      label: "Яндекс",
      steps: [
        {
          id: "oauth",
          kind: "oauth",
          title: "Войдите через Яндекс",
          text: "Откроется страница Яндекса. Проверьте, что выбран нужный аккаунт, и нажмите «Разрешить».",
          illo: "yandex_allow",
        },
        {
          id: "imap",
          kind: "imap",
          title: "Включите доступ для почтовых программ",
          text:
            "Яндекс пока не пускает Лео к письмам. Откройте настройки почты → «Почтовые программы», отметьте обе галочки " +
            "и нажмите «Сохранить изменения». Потом вернитесь сюда и нажмите «Проверить».",
          link: "https://mail.yandex.ru/#setup/client",
          linkLabel: "Открыть настройки почты",
          illo: "yandex_imap",
          hiddenUnlessError: true,
        },
      ],
      problems: [
        ["Яндекс не открылся", "Разрешите приложению открывать ссылки или скопируйте адрес из браузера вручную. Потом нажмите «Проверить»."],
        ["Нажал «Разрешить», но ничего не изменилось", "Нажмите «Я разрешил доступ — проверить». Если не помогло — выйдите из Яндекса в браузере и войдите тем аккаунтом, чью почту подключаете."],
        ["«Не удалось войти по IMAP»", "В настройках Яндекс-почты включите «Почтовые программы» → обе галочки и сохраните — это шаг 2."],
        ["Хочу без входа через Яндекс", "Можно по паролю приложения — ссылка «Подключить по паролю приложения» внизу."],
      ],
    },
    yandex_pw: {
      label: "Яндекс",
      provider: "yandex",
      steps: [
        {
          id: "apppass",
          kind: "info",
          title: "Создайте пароль приложения",
          text: "Откройте «Пароли приложений» в Яндекс ID, выберите тип «Почта», в названии введите Orbita и нажмите «Создать».",
          link: "https://id.yandex.ru/security/app-passwords",
          linkLabel: "Открыть пароли приложений",
          illo: "yandex_apppass",
        },
        { id: "password", kind: "form", title: "Вставьте адрес и пароль", text: "Скопируйте пароль, который показал Яндекс, и вставьте его сюда." },
      ],
      problems: [
        ["Нет раздела «Пароли приложений»", "Он появляется в Яндекс ID → «Безопасность». Если вы на корпоративной почте, его мог отключить администратор."],
        ["«Почта не приняла пароль»", "Нужен именно пароль приложения (длинный, его показывают один раз), а не пароль от Яндекса. Создайте новый и вставьте его."],
      ],
    },
    gmail: {
      label: "Gmail",
      provider: "gmail",
      steps: [
        {
          id: "twosv",
          kind: "info",
          title: "Включите двухэтапную аутентификацию",
          text: "Без неё Google не даёт создать пароль приложения. Займёт минуту, понадобится телефон. Если уже включена — просто нажмите «Готово, дальше».",
          link: "https://myaccount.google.com/signinoptions/twosv",
          linkLabel: "Открыть настройки Google",
          illo: "google_2fa",
        },
        {
          id: "apppass",
          kind: "info",
          title: "Создайте пароль приложения",
          text: "На странице «Пароли приложений» в поле «Название приложения» введите Orbita и нажмите «Создать».",
          link: "https://myaccount.google.com/apppasswords",
          linkLabel: "Открыть пароли приложений",
          illo: "google_apppass",
        },
        {
          id: "copy",
          kind: "info",
          title: "Скопируйте пароль",
          text: "Google покажет 16 букв в жёлтой рамке. Скопируйте их — пробелы не важны. Пароль показывают один раз.",
          illo: "google_copy",
        },
        { id: "password", kind: "form", title: "Вставьте адрес и пароль", text: "Адрес Gmail и 16 букв из жёлтой рамки." },
      ],
      problems: [
        ["Страница «Пароли приложений» пишет, что настройка недоступна", "Сначала включите двухэтапную аутентификацию (шаг 1). Если почта рабочая (Google Workspace) — пароли приложений мог запретить администратор, попросите его включить."],
        ["«Почта не приняла пароль»", "Вставлен обычный пароль от Google, а нужен пароль приложения из 16 букв. Вернитесь к шагу 2 и создайте новый."],
        ["Потерял пароль", "Ничего страшного: удалите старый на странице паролей приложений и создайте новый."],
      ],
    },
    mailru: {
      label: "Mail.ru",
      provider: "mailru",
      steps: [
        {
          id: "open",
          kind: "info",
          title: "Откройте «Пароли для внешних приложений»",
          text: "В Mail ID откройте «Безопасность» → «Пароли для внешних приложений» и нажмите «Добавить».",
          link: "https://id.mail.ru/security",
          linkLabel: "Открыть Mail ID",
          illo: "mailru_add",
        },
        {
          id: "access",
          kind: "info",
          title: "Назовите пароль и дайте доступ",
          text: "Название — Orbita, доступ — «Полный доступ к Почте». Нажмите «Продолжить» и скопируйте пароль.",
          illo: "mailru_access",
        },
        { id: "password", kind: "form", title: "Вставьте адрес и пароль", text: "Ваш адрес и пароль, который показал Mail.ru." },
      ],
      problems: [
        ["Нет раздела паролей", "Он в id.mail.ru → «Безопасность». Иногда сначала нужно включить двухфакторную аутентификацию."],
        ["«Почта не приняла пароль»", "Нужен пароль для внешнего приложения, а не пароль от почты. Создайте новый на шаге 1."],
      ],
    },
    imap: {
      label: "Другая почта",
      provider: "imap",
      steps: [
        {
          id: "password",
          kind: "form",
          title: "Адрес и пароль приложения",
          text: "Если ваша почта поддерживает пароли приложений — создайте такой в её настройках безопасности. Если нет — подойдёт обычный пароль.",
        },
      ],
      problems: [
        ["Не знаю сервер", "Найдите в настройках вашей почты раздел IMAP или «Почтовые программы» — там указан сервер (например, imap.company.ru) и порт (обычно 993)."],
        ["Рабочая почта Outlook / Exchange", "Пока не поддерживаем: там нужен вход через Microsoft. Скоро добавим."],
        ["«Почта не приняла пароль»", "Проверьте адрес. Если в почте включена двухфакторная защита, нужен пароль приложения."],
      ],
    },
  };

  var st = {
    accounts: [],
    yandex: {},
    view: "list",
    wiz: "",
    step: 0,
    form: { email: "", password: "", imap_host: "", imap_port: "993", smtp_host: "", smtp_port: "465" },
    send: true,
    error: "",
    hint: "",
    busy: false,
    problems: false,
    codeFlow: false,
    waitingOauth: false,
    showServer: false,
    justConnected: null,
  };
  var pollTimer = null;

  function body() {
    return document.getElementById("profile-mail-body");
  }

  async function openProfile() {
    if (app().profileScreen) app().profileScreen("mail");
    st.view = "list";
    st.error = "";
    st.justConnected = null;
    render();
    await refresh();
  }

  async function refresh() {
    try {
      var d = await api("/mail/accounts");
      st.accounts = d.accounts || [];
      st.yandex = d.yandex || {};
    } catch (e) {
      st.error = (e && e.message) || "Не удалось загрузить почту";
    }
    if (!st.accounts.length && st.view === "list") st.view = "choose";
    render();
  }

  function promiseText() {
    return (
      "Читаю «Входящие» за последние 14 дней. " +
      (st.send ? "Отправляю только письма, которые вы сами подтвердили кнопкой «Отправить». " : "Ничего не отправляю. ") +
      "Ничего не удаляю."
    );
  }

  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var today = new Date();
    var hm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    return d.toDateString() === today.toDateString() ? "сегодня в " + hm : d.toLocaleDateString("ru-RU") + " " + hm;
  }

  function accountSub(a) {
    if (a.status === "auth_error" || a.status === "error") return '<span class="pf-status--err">' + esc(a.last_error || "Ошибка входа") + "</span>";
    var fs = (a.stats || {}).first_sync;
    var parts = [PROVIDER_LABEL[a.provider] || "Почта"];
    if (a.last_sync_at) parts.push("обновлено " + fmtTime(a.last_sync_at));
    else parts.push("читаю письма…");
    var line = parts.join(" · ");
    if (fs) line += "<br>Просмотрел " + fs.seen + " " + plural(fs.seen, "письмо", "письма", "писем") + ": " + fs.bulk + " " + plural(fs.bulk, "рассылку", "рассылки", "рассылок") + " отсеял";
    return line;
  }

  function plural(n, one, few, many) {
    n = Math.abs(n) % 100;
    var n1 = n % 10;
    if (n > 10 && n < 20) return many;
    if (n1 > 1 && n1 < 5) return few;
    if (n1 === 1) return one;
    return many;
  }

  function heroHtml() {
    return (
      '<div class="pf-hero"><span class="pf-hero-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg></span>' +
      '<p class="pf-hero-title">Почта</p><p class="pf-hero-text">Лео разбирает письма: кто ждёт вашего ответа, что вы пообещали, о чём переписывались с участниками встречи. Ответить можно прямо из чата.</p></div>'
    );
  }

  function promiseHtml(withToggle) {
    return (
      '<div class="pf-group"><div class="pf-list">' +
      '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Что делает Лео</span>' +
      '<span class="pf-row-sub pf-row-sub--wrap" data-mail-promise>' +
      esc(promiseText()) +
      "</span></span></span></div>" +
      (withToggle
        ? '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Разрешить отвечать</span>' +
          '<span class="pf-row-sub pf-row-sub--wrap">Лео отправляет ответы, которые вы подтвердили</span></span>' +
          '<button type="button" class="svc-toggle' +
          (st.send ? " svc-toggle--on" : "") +
          '" role="switch" aria-checked="' +
          st.send +
          '" data-mail-act="send-toggle" aria-label="Разрешить отправку ответов"><span class="svc-toggle-knob"></span></button></span></div>'
        : "") +
      "</div></div>"
    );
  }

  function render() {
    var el = body();
    if (!el) return;
    var html = st.view === "list" || st.view === "choose" ? heroHtml() : "";
    if (st.view === "list") html += listHtml();
    else if (st.view === "choose") html += chooseHtml();
    else if (st.view === "wizard") html += wizardHtml();
    else if (st.view === "done") html += doneHtml();
    el.innerHTML = html;
  }

  function listHtml() {
    var h = promiseHtml(false);
    h += '<div class="pf-group"><p class="pf-group-title">Подключённые ящики</p><div class="pf-list">';
    st.accounts.forEach(function (a) {
      var broken = a.status === "auth_error" || a.status === "error";
      h +=
        '<div class="pf-row pf-row--static mail-acc" data-acc="' +
        esc(a.account_id) +
        '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
        esc(a.email) +
        '</span><span class="pf-row-sub pf-row-sub--wrap">' +
        accountSub(a) +
        "</span></span>" +
        (broken
          ? '<button type="button" class="pf-link" data-mail-act="reconnect">Переподключить</button>'
          : '<button type="button" class="pf-link pf-link--muted" data-mail-act="sync">Обновить</button>') +
        "</span></div>" +
        '<div class="pf-row pf-row--static mail-acc-sub" data-acc="' +
        esc(a.account_id) +
        '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Отвечать из Orbita</span></span>' +
        '<button type="button" class="svc-toggle' +
        (a.send_enabled ? " svc-toggle--on" : "") +
        '" role="switch" aria-checked="' +
        !!a.send_enabled +
        '" data-mail-act="acc-send" aria-label="Отвечать из Orbita"><span class="svc-toggle-knob"></span></button>' +
        '<button type="button" class="pf-link pf-link--danger" data-mail-act="disconnect">Отключить</button></span></div>';
    });
    h += "</div></div>";
    h += '<button type="button" class="pf-btn" data-mail-act="add">Подключить ещё ящик</button>';
    if (st.error) h += '<p class="pf-field-error">' + esc(st.error) + "</p>";
    return h;
  }

  function chooseHtml() {
    var tiles = [
      ["yandex", "Яндекс", "Вход через Яндекс — 1–2 нажатия"],
      ["gmail", "Gmail", "Пароль приложения — около 2 минут"],
      ["mailru", "Mail.ru", "Пароль приложения — около 2 минут"],
      ["imap", "Другая почта", "Рабочая почта на своём домене и др."],
    ];
    var h = '<div class="pf-group"><p class="pf-group-title">Какая у вас почта?</p><div class="mail-tiles">';
    tiles.forEach(function (t) {
      h +=
        '<button type="button" class="mail-tile" data-mail-act="pick" data-wiz="' +
        t[0] +
        '"><span class="mail-tile-title">' +
        t[1] +
        '</span><span class="mail-tile-sub">' +
        t[2] +
        "</span></button>";
    });
    h += "</div></div>";
    h +=
      '<div class="pf-group"><p class="pf-group-title">Или введите адрес — подберём шаги сами</p><div class="pf-list"><label class="pf-row pf-row--static field"><span class="pf-row-body">' +
      '<input type="email" class="pf-input" data-mail-field="email" placeholder="you@company.ru" autocomplete="email" value="' +
      esc(st.form.email) +
      '" /><button type="button" class="pf-link" data-mail-act="detect">Дальше</button></span></label></div></div>';
    if (st.error) h += '<p class="pf-field-error">' + esc(st.error) + "</p>";
    h += promiseHtml(true);
    if (st.accounts.length) h += '<button type="button" class="pf-btn pf-btn--ghost" data-mail-act="to-list">Отмена</button>';
    return h;
  }

  function visibleSteps() {
    var w = WIZARDS[st.wiz];
    return w.steps.filter(function (s) {
      return !s.hiddenUnlessError || s.id === (w.steps[st.step] || {}).id;
    });
  }

  function wizardHtml() {
    var w = WIZARDS[st.wiz];
    var step = w.steps[st.step];
    var vis = visibleSteps();
    var idx = vis.indexOf(step) + 1;
    var h = '<div class="mail-wiz">';
    h += '<p class="mail-wiz-progress">' + esc(w.label) + (vis.length > 1 ? " · шаг " + idx + " из " + vis.length : "") + "</p>";
    h += '<p class="mail-wiz-title">' + esc(step.title) + "</p>";
    h += '<p class="mail-wiz-text">' + esc(step.text) + "</p>";
    if (st.hint) h += '<p class="pf-note pf-note--warn">' + esc(st.hint) + "</p>";
    if (step.illo && ILLO[step.illo]) h += '<div class="mail-illo" aria-hidden="true">' + ILLO[step.illo] + "</div>";
    if (step.link)
      h += '<button type="button" class="pf-btn pf-btn--ghost mail-wiz-link" data-mail-act="open-link" data-url="' + esc(step.link) + '">' + esc(step.linkLabel || "Открыть") + " ↗</button>";
    if (step.kind === "oauth") h += oauthStepHtml();
    if (step.kind === "imap") h += '<button type="button" class="pf-btn" data-mail-act="yandex-check"' + (st.busy ? " disabled" : "") + ">" + (st.busy ? "Проверяю…" : "Проверить") + "</button>";
    if (step.kind === "info") h += '<button type="button" class="pf-btn" data-mail-act="next">Готово, дальше</button>';
    if (step.kind === "form") h += formHtml();
    if (st.error) h += '<p class="pf-field-error mail-wiz-error">' + esc(st.error) + "</p>";
    if (st.error && st.backTo && step.id === "password")
      h += '<button type="button" class="pf-btn pf-btn--ghost" data-mail-act="goto" data-step="' + esc(st.backTo) + '">← К созданию пароля приложения</button>';
    if (step.kind === "form") h += privacyHtml();
    h += '<div class="mail-wiz-foot">';
    h += '<button type="button" class="pf-link pf-link--muted" data-mail-act="back">← Назад</button>';
    h += '<button type="button" class="pf-link" data-mail-act="problems">Не получается</button>';
    h += "</div>";
    if (st.problems) h += problemsHtml(w, step);
    if (st.wiz === "yandex")
      h += '<button type="button" class="pf-link pf-link--muted mail-wiz-alt" data-mail-act="pick" data-wiz="yandex_pw">Подключить по паролю приложения</button>';
    h += "</div>";
    return h;
  }

  function oauthStepHtml() {
    var h = "";
    if (st.codeFlow) {
      h +=
        '<div class="pf-list"><label class="pf-row pf-row--static field"><span class="pf-row-body"><input type="text" class="pf-input" data-mail-field="code" placeholder="Вставьте код со страницы Яндекса" autocomplete="one-time-code" /></span></label></div>' +
        '<button type="button" class="pf-btn" data-mail-act="yandex-code"' +
        (st.busy ? " disabled" : "") +
        ">" +
        (st.busy ? "Проверяю…" : "Подключить") +
        "</button>";
      return h;
    }
    h += '<button type="button" class="pf-btn" data-mail-act="yandex-oauth"' + (st.busy ? " disabled" : "") + ">Войти через Яндекс</button>";
    if (st.waitingOauth)
      h +=
        '<p class="pf-hint">Ждём подтверждения в Яндексе… Как только нажмёте «Разрешить», подключим сами.</p>' +
        '<button type="button" class="pf-btn pf-btn--ghost" data-mail-act="yandex-check">Я разрешил доступ — проверить</button>';
    return h;
  }

  function formHtml() {
    var w = WIZARDS[st.wiz];
    var isImap = w.provider === "imap";
    var h = '<div class="pf-list">';
    h +=
      '<label class="pf-row pf-row--static field"><span class="pf-row-body"><input type="email" class="pf-input" data-mail-field="email" placeholder="Адрес почты" autocomplete="email" value="' +
      esc(st.form.email) +
      '" /></span></label>';
    h +=
      '<label class="pf-row pf-row--static field"><span class="pf-row-body"><input type="password" class="pf-input" data-mail-field="password" placeholder="' +
      (w.provider === "gmail" ? "16 букв из жёлтой рамки" : "Пароль приложения") +
      '" autocomplete="off" value="' +
      esc(st.form.password) +
      '" /></span></label>';
    h += "</div>";
    if (isImap || st.showServer) {
      if (!st.showServer) {
        h += '<button type="button" class="pf-link pf-link--muted" data-mail-act="server">Дополнительно: сервер вручную</button>';
      } else {
        h += '<p class="pf-group-title">Сервер</p><div class="pf-list mail-server">';
        [
          ["imap_host", "IMAP-сервер, например imap.company.ru"],
          ["imap_port", "Порт IMAP (обычно 993)"],
          ["smtp_host", "SMTP-сервер для ответов"],
          ["smtp_port", "Порт SMTP (обычно 465)"],
        ].forEach(function (f) {
          h +=
            '<label class="pf-row pf-row--static field"><span class="pf-row-body"><input type="text" class="pf-input" data-mail-field="' +
            f[0] +
            '" placeholder="' +
            f[1] +
            '" value="' +
            esc(st.form[f[0]]) +
            '" /></span></label>';
        });
        h += "</div>";
      }
    }
    h +=
      '<button type="button" class="pf-btn" data-mail-act="connect"' +
      (st.busy ? " disabled" : "") +
      ">" +
      (st.busy ? "Проверяю вход…" : "Подключить") +
      "</button>";
    return h;
  }

  function privacyHtml() {
    var w = WIZARDS[st.wiz];
    var h = "";
    if (w.provider === "gmail" || w.provider === "mailru" || w.provider === "yandex")
      h +=
        '<p class="pf-hint">Пароль приложения даёт доступ к почте целиком. Лео им только читает и отправляет подтверждённые вами ответы; пароль хранится зашифрованным, удаляется кнопкой «Отключить», а отозвать его можно в настройках ' +
        esc(w.label) +
        ".</p>";
    return h;
  }

  function problemsHtml(w, step) {
    var h = '<div class="pf-group mail-problems"><p class="pf-group-title">Частые проблемы</p><div class="pf-list">';
    (w.problems || []).forEach(function (p) {
      h +=
        '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title pf-row-title--wrap">' +
        esc(p[0]) +
        '</span><span class="pf-row-sub pf-row-sub--wrap">' +
        esc(p[1]) +
        "</span></span></span></div>";
    });
    h += "</div></div>";
    h += '<button type="button" class="pf-btn pf-btn--ghost" data-mail-act="ask-leo">Спросить Лео</button>';
    return h;
  }

  function doneHtml() {
    var a = st.justConnected || {};
    var fresh = (st.accounts || []).filter(function (x) {
      return x.account_id === a.account_id;
    })[0] || a;
    var fs = (fresh.stats || {}).first_sync;
    var h = '<div class="mail-done"><p class="mail-wiz-title">Подключено · ' + esc(fresh.email || "") + "</p>";
    h += fs
      ? '<p class="mail-wiz-text">Просмотрел ' + fs.seen + " " + plural(fs.seen, "письмо", "письма", "писем") + ": " + fs.bulk + " " + plural(fs.bulk, "рассылку", "рассылки", "рассылок") + " отсеял. Кто ждёт вашего ответа — Лео покажет вечером в «Фокусе».</p>"
      : '<p class="mail-wiz-text">Читаю письма за 14 дней — это займёт минуту. Рассылки и уведомления отсеиваю сразу.</p>';
    if (st.sendScopeMissing)
      h += '<p class="pf-note pf-note--warn">Чтобы отвечать на письма из Orbita, Яндексу нужно право на отправку: войдите через Яндекс ещё раз.</p>';
    h += '<button type="button" class="pf-btn" data-mail-act="to-list">Готово</button></div>';
    return h;
  }

  function stopPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function pollUntil(check, ms, every) {
    stopPoll();
    var t0 = Date.now();
    pollTimer = setInterval(async function () {
      if (!document.getElementById("profile-mail") || document.getElementById("profile-mail").classList.contains("hidden") || Date.now() - t0 > ms) {
        stopPoll();
        return;
      }
      try {
        if (await check()) stopPoll();
      } catch (_) {}
    }, every);
  }

  function readFields() {
    var el = body();
    if (!el) return;
    el.querySelectorAll("[data-mail-field]").forEach(function (inp) {
      st.form[inp.getAttribute("data-mail-field")] = inp.value.trim();
    });
  }

  function startWizard(wiz) {
    st.view = "wizard";
    st.wiz = wiz;
    st.step = 0;
    st.error = "";
    st.problems = false;
    st.codeFlow = false;
    st.waitingOauth = false;
    st.showServer = false;
    render();
  }

  function goStep(id) {
    var steps = WIZARDS[st.wiz].steps;
    for (var i = 0; i < steps.length; i++) if (steps[i].id === id) st.step = i;
  }

  function connected(res) {
    stopPoll();
    st.busy = false;
    st.error = "";
    st.view = "done";
    st.justConnected = res.account;
    st.sendScopeMissing = st.send && res.send_scope === false;
    st.form.password = "";
    render();
    if (app().loadIntegrations) app().loadIntegrations();
    pollUntil(
      async function () {
        await refresh();
        var acc = st.accounts.filter(function (x) {
          return x.account_id === res.account.account_id;
        })[0];
        return acc && (acc.stats || {}).first_sync;
      },
      120000,
      4000
    );
  }

  function fail(e) {
    st.busy = false;
    var d = (e && e.detail) || {};
    st.error = (e && e.message) || "Не получилось подключить";
    if (d.step === "imap" && st.wiz === "yandex") goStep("imap");
    else if (d.step === "server") {
      st.showServer = true;
      goStep("password");
    } else if (d.step === "password" || d.step === "email") {
      st.backTo =
        d.code === "auth" &&
        WIZARDS[st.wiz].steps.some(function (s) {
          return s.id === "apppass";
        })
          ? "apppass"
          : "";
      goStep("password");
    }
    render();
  }

  async function yandexConnect(silent) {
    try {
      var res = await post("/mail/connect", { provider: "yandex", send_enabled: st.send });
      if (res && res.need_oauth) {
        if (!silent) {
          st.busy = false;
          st.error = "Яндекс пока не подтвердил доступ к почте. Нажмите «Войти через Яндекс» и «Разрешить».";
          render();
        }
        return false;
      }
      connected(res);
      return true;
    } catch (e) {
      stopPoll();
      fail(e);
      return true;
    }
  }

  async function onClick(e) {
    var btn = e.target.closest("[data-mail-act]");
    if (!btn || !body() || !body().contains(btn)) return;
    var act = btn.getAttribute("data-mail-act");
    readFields();
    var accEl = btn.closest("[data-acc]");
    var accId = accEl && accEl.getAttribute("data-acc");
    var acc = st.accounts.filter(function (a) {
      return a.account_id === accId;
    })[0];
    if (act === "send-toggle") {
      st.send = !st.send;
      render();
    } else if (act === "add") {
      st.view = "choose";
      st.error = "";
      render();
    } else if (act === "to-list") {
      stopPoll();
      st.view = st.accounts.length ? "list" : "choose";
      st.error = "";
      render();
      refresh();
    } else if (act === "pick") {
      st.hint = "";
      startWizard(btn.getAttribute("data-wiz"));
    } else if (act === "detect") {
      if (!st.form.email) return;
      st.busy = true;
      try {
        var d = await post("/mail/detect", { email: st.form.email });
        st.busy = false;
        var wiz = d.provider === "yandex" ? "yandex" : d.provider in WIZARDS ? d.provider : "imap";
        if (wiz === "yandex" && d.corporate) wiz = "yandex_pw";
        startWizard(wiz);
        st.hint = d.hint || "";
        if (d.needs_server) st.showServer = true;
        render();
      } catch (err) {
        st.busy = false;
        st.error = (err && err.message) || "Не получилось";
        render();
      }
    } else if (act === "next") {
      var steps = WIZARDS[st.wiz].steps;
      st.step = Math.min(steps.length - 1, st.step + 1);
      while (steps[st.step].hiddenUnlessError && st.step < steps.length - 1) st.step++;
      st.error = "";
      st.problems = false;
      render();
    } else if (act === "back") {
      stopPoll();
      st.problems = false;
      st.error = "";
      if (st.step > 0) {
        st.step--;
        while (st.step > 0 && WIZARDS[st.wiz].steps[st.step].hiddenUnlessError) st.step--;
      } else st.view = "choose";
      render();
    } else if (act === "goto") {
      goStep(btn.getAttribute("data-step"));
      st.error = "";
      st.backTo = "";
      render();
    } else if (act === "problems") {
      st.problems = !st.problems;
      render();
    } else if (act === "ask-leo") {
      var w = WIZARDS[st.wiz];
      var s = w.steps[st.step];
      app().askLeo(
        "Помоги подключить почту " +
          w.label +
          " в Orbita. Я на шаге «" +
          s.title +
          "» (" +
          s.text +
          ")" +
          (st.error ? " Ошибка: «" + st.error + "»." : "") +
          " Не получается — что делать?"
      );
    } else if (act === "open-link") {
      app().openLink(btn.getAttribute("data-url"));
    } else if (act === "server") {
      st.showServer = true;
      render();
    } else if (act === "connect") {
      st.busy = true;
      st.error = "";
      render();
      var w2 = WIZARDS[st.wiz];
      var payload = { provider: w2.provider, email: st.form.email, password: st.form.password, send_enabled: st.send };
      if (st.showServer && st.form.imap_host) {
        payload.imap_host = st.form.imap_host;
        payload.imap_port = parseInt(st.form.imap_port, 10) || 993;
        payload.smtp_host = st.form.smtp_host;
        payload.smtp_port = parseInt(st.form.smtp_port, 10) || 465;
      }
      try {
        connected(await post("/mail/connect", payload));
      } catch (err) {
        fail(err);
      }
    } else if (act === "yandex-oauth") {
      st.error = "";
      try {
        var r = await post("/mail/yandex/start", {});
        if (r.url) app().openLink(r.url);
        if (r.verification_code_flow) {
          st.codeFlow = true;
        } else {
          st.waitingOauth = true;
          pollUntil(function () {
            return yandexConnect(true);
          }, 180000, 3000);
        }
      } catch (err) {
        st.error = (err && err.message) || "Не получилось открыть Яндекс";
      }
      render();
    } else if (act === "yandex-code") {
      var code = st.form.code;
      if (!code) return;
      st.busy = true;
      render();
      try {
        await post("/oauth/telemost/code", { code: code });
        await yandexConnect(false);
      } catch (err) {
        st.busy = false;
        st.error = (err && err.message) || "Код не подошёл — получите новый";
        render();
      }
    } else if (act === "yandex-check") {
      st.busy = true;
      render();
      await yandexConnect(false);
    } else if (act === "sync" && acc) {
      await post("/mail/accounts/" + encodeURIComponent(acc.account_id) + "/sync", {});
      toast("Обновляю почту…");
      setTimeout(refresh, 5000);
    } else if (act === "acc-send" && acc) {
      try {
        await post("/mail/accounts/" + encodeURIComponent(acc.account_id), { send_enabled: !acc.send_enabled }, "PATCH");
        await refresh();
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
    } else if (act === "disconnect" && acc) {
      var ok = await app().confirm("Отключить " + acc.email + "? Лео сотрёт доступ к ящику и всё, что сохранил из писем.");
      if (!ok) return;
      try {
        await api("/mail/accounts/" + encodeURIComponent(acc.account_id), { method: "DELETE" });
        toast("Почта отключена");
      } catch (err) {
        toast((err && err.message) || "Не получилось отключить");
      }
      await refresh();
      if (app().loadIntegrations) app().loadIntegrations();
    } else if (act === "reconnect" && acc) {
      st.form.email = acc.email;
      st.form.password = "";
      startWizard(acc.provider === "yandex" && acc.auth === "oauth_yandex" ? "yandex" : acc.provider === "yandex" ? "yandex_pw" : acc.provider in WIZARDS ? acc.provider : "imap");
      goStep(acc.provider === "yandex" && acc.auth === "oauth_yandex" ? "oauth" : "password");
      render();
    }
  }

  function bind() {
    document.addEventListener("click", function (e) {
      if (e.target.closest("#profile-mail-back")) {
        stopPoll();
        if (app().profileScreen) app().profileScreen("main");
        if (app().loadIntegrations) app().loadIntegrations();
        return;
      }
      onClick(e);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      var inp = e.target.closest && e.target.closest("#profile-mail-body [data-mail-field]");
      if (!inp) return;
      var f = inp.getAttribute("data-mail-field");
      var act = f === "email" && st.view === "choose" ? "detect" : f === "code" ? "yandex-code" : "connect";
      var b = body().querySelector('[data-mail-act="' + act + '"]');
      if (b) b.click();
    });
  }

  window.LeoMail = { openProfile: openProfile };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
