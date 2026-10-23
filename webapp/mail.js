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
    signature: null,
    rules: [],
    filtered: [],
    showFiltered: false,
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
      if (st.accounts.length && st.signature == null) {
        var cfg = await api("/mail/settings");
        st.signature = (cfg && cfg.signature) || "";
      }
      if (st.accounts.length) {
        var sd = await api("/mail/senders");
        st.rules = sd.rules || [];
        st.filtered = sd.filtered || [];
      }
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
    h +=
      '<div class="pf-group"><p class="pf-group-title">Подпись в письмах</p><div class="pf-list"><label class="pf-row pf-row--static field"><span class="pf-row-body">' +
      '<textarea class="pf-input mail-signature" rows="3" data-mail-signature placeholder="Например: Владимир Пшенович, руководитель проектов, +7 …">' +
      esc(st.signature || "") +
      '</textarea></span></label></div><p class="pf-hint">Добавится под ответами, которые вы отправляете из Orbita.</p></div>';
    h += sendersHtml();
    h += '<button type="button" class="pf-btn" data-mail-act="add">Подключить ещё ящик</button>';
    if (st.error) h += '<p class="pf-field-error">' + esc(st.error) + "</p>";
    return h;
  }

  function sendersHtml() {
    var allow = st.rules.filter(function (r) {
      return r.rule === "allow";
    });
    var block = st.rules.filter(function (r) {
      return r.rule === "block";
    });
    var h = '<div class="pf-group"><p class="pf-group-title">Всегда пропускать</p><div class="pf-list">';
    allow.forEach(function (r) {
      h +=
        '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
        esc(r.pattern) +
        '</span></span><button type="button" class="pf-link pf-link--muted" data-mail-act="rule-del" data-pattern="' +
        esc(r.pattern) +
        '">Убрать</button></span></div>';
    });
    h +=
      '<label class="pf-row pf-row--static field"><span class="pf-row-body"><input type="text" class="pf-input" data-mail-field="allow" placeholder="Адрес или домен: ivan@client.ru, @client.ru" />' +
      '<button type="button" class="pf-link" data-mail-act="allow-add">Добавить</button></span></label>';
    h += '</div><p class="pf-hint">Письма от них Лео покажет, даже если принял их за рассылку или робота.</p></div>';
    if (block.length) {
      h += '<div class="pf-group"><p class="pf-group-title">Скрытые отправители</p><div class="pf-list">';
      block.forEach(function (r) {
        h +=
          '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
          esc(r.pattern) +
          '</span></span><button type="button" class="pf-link" data-mail-act="rule-del" data-pattern="' +
          esc(r.pattern) +
          '">Показывать снова</button></span></div>';
      });
      h += "</div></div>";
    }
    if (st.filtered.length) {
      h +=
        '<div class="pf-group"><button type="button" class="pf-group-title mail-group-toggle" data-mail-act="filtered-toggle">Отсеяно Лео за 14 дней · ' +
        st.filtered.length +
        " " +
        (st.showFiltered ? "▴" : "▾") +
        "</button>";
      if (st.showFiltered) {
        h += '<div class="pf-list">';
        st.filtered.forEach(function (f) {
          h +=
            '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
            esc(f.name || f.addr) +
            '</span><span class="pf-row-sub">' +
            esc((f.name ? f.addr + " · " : "") + f.count + " " + plural(f.count, "письмо", "письма", "писем") + (f.last_subject ? " · «" + f.last_subject + "»" : "")) +
            '</span></span><button type="button" class="pf-link" data-mail-act="allow-addr" data-pattern="' +
            esc(f.addr) +
            '">Пропускать</button></span></div>';
        });
        h += "</div>";
      }
      h += "</div>";
    }
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
    } else if (act === "allow-add" || act === "allow-addr") {
      var pattern = act === "allow-add" ? st.form.allow : btn.getAttribute("data-pattern");
      if (!pattern) return;
      try {
        var ra = await post("/mail/senders", { pattern: pattern, rule: "allow" });
        st.form.allow = "";
        toast("Будем пропускать " + ra.pattern + (ra.revived ? " · перечитал писем: " + ra.revived : ""));
        await refresh();
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
    } else if (act === "rule-del") {
      try {
        await api("/mail/senders?pattern=" + encodeURIComponent(btn.getAttribute("data-pattern")), { method: "DELETE" });
        await refresh();
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
    } else if (act === "filtered-toggle") {
      st.showFiltered = !st.showFiltered;
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
    document.addEventListener("change", async function (e) {
      var sig = e.target.closest && e.target.closest("#profile-mail-body [data-mail-signature]");
      if (!sig) return;
      try {
        var res = await post("/mail/settings", { signature: sig.value }, "PATCH");
        st.signature = res.signature;
        toast("Подпись сохранена");
      } catch (err) {
        toast((err && err.message) || "Не удалось сохранить подпись");
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      var inp = e.target.closest && e.target.closest("#profile-mail-body [data-mail-field]");
      if (!inp) return;
      var f = inp.getAttribute("data-mail-field");
      var act = f === "email" && st.view === "choose" ? "detect" : f === "code" ? "yandex-code" : f === "allow" ? "allow-add" : "connect";
      var b = body().querySelector('[data-mail-act="' + act + '"]');
      if (b) b.click();
    });
  }

  /* ——————————————————————————————— чат: письмо, черновик, окно письма ——————————————————————————————— */

  var AUTO_KEY = "leo_mail_autopened";

  function cacheAction(a) {
    if (a && app().rememberActions) app().rememberActions([a]);
  }

  async function refreshActions(ids) {
    try {
      var res = await post("/gpt/calendar-actions/status", { ids: ids });
      (res.actions || []).forEach(cacheAction);
      if (app().refreshFeed) app().refreshFeed();
      return res.actions || [];
    } catch (_) {
      return [];
    }
  }

  async function openThread(mailKey) {
    if (!mailKey) return;
    try {
      var res = await post("/mail/thread-card", { mail_key: mailKey });
      cacheAction(res.action);
      var m = res.action.mail || {};
      await app().postLeoCard("Письмо от " + (m.from || "отправителя") + ": «" + (res.action.title || "без темы") + "». Ответить, подготовить ответ или собрать саммари переписки?", [res.action.id]);
    } catch (e) {
      toast((e && e.message) || "Не удалось открыть письмо");
    }
  }

  async function replyTo(a) {
    var m = a.mail || {};
    try {
      var res = await post("/mail/drafts", { mail_key: m.mail_key });
      cacheAction(res.action);
      await app().postLeoCard("Ответ на «" + (a.title || "письмо") + "».", [res.action.id]);
      openComposer(res.action);
    } catch (e) {
      toast((e && e.message) || "Не удалось начать ответ");
    }
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function cardBtn(label, cls, fn) {
    var b = el("button", "note-discuss-edits-btn" + (cls ? " " + cls : ""), label);
    b.type = "button";
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      fn(b);
    });
    return b;
  }

  function renderThreadCard(a) {
    var m = a.mail || {};
    var card = el("div", "mail-card");
    var head = el("div", "mail-card-head");
    head.appendChild(el("span", "mail-card-ico", "✉"));
    var copy = el("div", "mail-card-copy");
    copy.appendChild(el("small", "mail-card-meta", [m.direction === "out" ? "Ваше письмо" : m.from, m.when].filter(Boolean).join(" · ")));
    copy.appendChild(el("b", "mail-card-title", a.title || "Без темы"));
    if (m.gist) copy.appendChild(el("span", "mail-card-gist", m.gist));
    head.appendChild(copy);
    card.appendChild(head);
    if (a.mine) {
      var acts = el("div", "note-discuss-edits-actions mail-card-acts");
      acts.appendChild(cardBtn("Ответить", "is-primary", function () {
        replyTo(a);
      }));
      acts.appendChild(cardBtn("Подготовить ответ", "", function () {
        app().askLeo("Подготовь ответ на письмо «" + (a.title || "") + "» от " + (m.from || "") + " (mail key: " + m.mail_key + ").");
      }));
      acts.appendChild(cardBtn("Саммари переписки", "", function () {
        app().askLeo("Сделай саммари переписки по письму «" + (a.title || "") + "» (mail key: " + m.mail_key + "): кто что писал, что решили, что ждут от меня.");
      }));
      card.appendChild(acts);
    }
    return card;
  }

  function hm(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  function draftTo(d) {
    return (d.to || []).join(", ") + ((d.cc || []).length ? " +" + d.cc.length : "");
  }

  var countdownTimers = {};

  function renderDraftCard(a) {
    var d = a.draft || {};
    if (a.status === "dismissed") return null;
    var card = el("div", "mail-card mail-card--draft is-" + a.status);
    var line = el("div", "mail-card-head");
    line.appendChild(el("span", "mail-card-ico", a.status === "sent" ? "✓" : "✎"));
    var copy = el("div", "mail-card-copy");
    var title;
    if (a.status === "sent") title = "Отправлено · " + hm(d.sent_at);
    else if (a.status === "sending") title = "Отправка…";
    else if (a.status === "failed") title = "Не отправилось";
    else title = "Черновик";
    copy.appendChild(el("b", "mail-card-title", title));
    copy.appendChild(el("span", "mail-card-gist", (a.title || "Ответ") + " → " + draftTo(d)));
    if (a.status === "failed" && d.error) copy.appendChild(el("span", "mail-card-err", d.error));
    line.appendChild(copy);
    card.appendChild(line);
    if (!a.mine) return card;
    var acts = el("div", "note-discuss-edits-actions mail-card-acts");
    if (a.status === "sending") {
      var left = Math.max(0, Math.ceil((new Date(d.send_at) - Date.now()) / 1000));
      var undo = cardBtn("Отменить" + (left ? " · " + left : ""), "is-primary", async function (b) {
        b.disabled = true;
        try {
          var res = await post("/gpt/calendar-actions/" + encodeURIComponent(a.id) + "/cancel_send", {});
          cacheAction(res.action);
          if (app().refreshFeed) app().refreshFeed();
          toast("Отправка отменена — черновик сохранён");
        } catch (e) {
          toast((e && e.message) || "Не успели отменить");
        }
      });
      acts.appendChild(undo);
      if (!countdownTimers[a.id]) {
        countdownTimers[a.id] = setTimeout(function () {
          delete countdownTimers[a.id];
          refreshActions([a.id]);
        }, left > 0 ? 1000 : 2500);
      }
    } else if (a.status === "sent") {
      acts.appendChild(cardBtn("Показать письмо", "", function () {
        openComposer(a, { readOnly: true });
      }));
    } else {
      acts.appendChild(cardBtn(a.status === "failed" ? "Открыть и отправить снова" : "Открыть", "is-primary", function () {
        openComposer(a);
      }));
    }
    card.appendChild(acts);
    maybeAutoOpen(a);
    return card;
  }

  // Черновик, который только что подготовил Лео, сразу открываем в окне письма (один раз).
  function maybeAutoOpen(a) {
    var d = a.draft || {};
    if (a.status !== "proposed" || d.by !== "leo" || composer.action) return;
    var created = Date.parse(String(d.created_at || "").replace(/Z?$/, "Z"));
    if (!created || Date.now() - created > 3 * 60 * 1000) return;
    var seen = [];
    try {
      seen = JSON.parse(localStorage.getItem(AUTO_KEY) || "[]");
    } catch (_) {}
    if (seen.indexOf(a.id) >= 0) return;
    seen.push(a.id);
    try {
      localStorage.setItem(AUTO_KEY, JSON.stringify(seen.slice(-50)));
    } catch (_) {}
    setTimeout(function () {
      openComposer(a);
    }, 0);
  }

  function renderAction(a) {
    if (a.kind === "email_thread") return renderThreadCard(a);
    if (a.kind === "email_draft") return renderDraftCard(a);
    return null;
  }

  /* ——— окно письма на месте поля ввода чата ——— */

  var composer = { action: null, d: null, readOnly: false, saveTimer: null, saving: null, prevBody: null, plus: null, expanded: false };

  function isMobile() {
    return window.matchMedia && window.matchMedia("(max-width: 767px)").matches;
  }

  function chatInput() {
    return document.getElementById("note-paie-thread");
  }

  function composerEl() {
    var n = document.getElementById("mail-composer");
    if (n) return n;
    var host = chatInput();
    if (!host || !host.parentNode) return null;
    n = el("div", "mail-composer hidden");
    n.id = "mail-composer";
    n.setAttribute("role", "dialog");
    n.setAttribute("aria-label", "Письмо");
    host.parentNode.insertBefore(n, host);
    n.addEventListener("click", onComposerClick);
    n.addEventListener("input", onComposerInput);
    n.addEventListener("keydown", onComposerKey);
    n.addEventListener("change", onComposerChange);
    bindSwipe(n);
    return n;
  }

  function openComposer(a, opts) {
    var n = composerEl();
    if (!n) {
      toast("Откройте чат с Лео, чтобы ответить");
      return;
    }
    composer.action = a;
    composer.d = JSON.parse(JSON.stringify(a.draft || {}));
    composer.readOnly = !!(opts && opts.readOnly);
    composer.prevBody = null;
    composer.plus = null;
    composer.expanded = !isMobile();
    renderComposer();
    n.classList.remove("hidden");
    if (chatInput()) chatInput().classList.add("hidden");
    var body = n.querySelector("[data-mf=body]");
    if (body && !composer.readOnly) {
      autoGrow(body);
      body.focus();
      body.setSelectionRange(body.value.length, body.value.length);
    }
  }

  async function closeComposer() {
    await flushSave();
    var n = document.getElementById("mail-composer");
    if (n) {
      n.classList.add("hidden");
      n.innerHTML = "";
    }
    if (chatInput()) chatInput().classList.remove("hidden");
    composer.action = null;
    composer.d = null;
    if (app().refreshFeed) app().refreshFeed();
  }

  function summaryLine(d) {
    var who = d.orig_from && (d.to || [])[0] ? d.orig_from : (d.to || [])[0] || "кому?";
    if ((d.to || []).length > 1 || (d.cc || []).length) who += " и ещё " + ((d.to || []).length - 1 + (d.cc || []).length);
    return who + " · " + (d.subject || "без темы");
  }

  function chipsHtml(field) {
    var d = composer.d;
    var fresh = d.new_recipients || [];
    var h = '<div class="mail-chips" data-chips="' + field + '">';
    (d[field] || []).forEach(function (addr) {
      h +=
        '<span class="mail-chip' +
        (fresh.indexOf(addr) >= 0 ? " is-new" : "") +
        '" title="' +
        (fresh.indexOf(addr) >= 0 ? "Нового адреса не было в переписке" : "") +
        '">' +
        esc(addr) +
        (composer.readOnly ? "" : '<button type="button" class="mail-chip-x" data-mc="chip-x" data-field="' + field + '" data-addr="' + esc(addr) + '" aria-label="Убрать">×</button>') +
        "</span>";
    });
    if (!composer.readOnly) h += '<input type="email" class="mail-chip-input" data-chip-input="' + field + '" placeholder="' + (field === "to" ? "адрес" : "добавить копию") + '" />';
    return h + "</div>";
  }

  function renderComposer() {
    var n = composerEl();
    var d = composer.d;
    if (!n || !d) return;
    var ro = composer.readOnly;
    var h = '<div class="mail-cmp-grab" aria-hidden="true"></div>';
    h += '<div class="mail-cmp-head">';
    h += '<button type="button" class="btn-text mail-cmp-close" data-mc="close">' + (ro ? "Закрыть" : "Отмена") + "</button>";
    h += '<span class="mail-cmp-kind">' + (ro ? "Отправленное письмо" : "Ответ на письмо") + '</span><span class="mail-cmp-saved" data-saved></span>';
    if (!ro) h += '<button type="button" class="mail-cmp-send" data-mc="send">Отправить</button>';
    h += "</div>";
    h += '<button type="button" class="mail-cmp-summary' + (composer.expanded ? " hidden" : "") + '" data-mc="expand">' + esc(summaryLine(d)) + ' <span aria-hidden="true">▾</span></button>';
    h += '<div class="mail-cmp-fields' + (composer.expanded ? "" : " hidden") + '">';
    if ((d.accounts || []).length > 1) {
      h += '<label class="mail-cmp-field"><span>От</span><select data-mf="account_id"' + (ro ? " disabled" : "") + ">";
      d.accounts.forEach(function (acc) {
        h += '<option value="' + esc(acc.account_id) + '"' + (acc.account_id === d.account_id ? " selected" : "") + ">" + esc(acc.email) + "</option>";
      });
      h += "</select></label>";
    }
    h += '<div class="mail-cmp-field"><span>Кому</span>' + chipsHtml("to") + "</div>";
    h += '<div class="mail-cmp-field"><span>Копия</span>' + chipsHtml("cc") + "</div>";
    h += '<label class="mail-cmp-field"><span>Тема</span><input type="text" data-mf="subject" value="' + esc(d.subject) + '"' + (ro ? " readonly" : "") + " /></label>";
    if (!ro)
      h +=
        '<label class="mail-cmp-replyall"><input type="checkbox" data-mf="reply_all"' +
        (d.reply_all ? " checked" : "") +
        " /> Ответить всем</label>";
    if ((d.new_recipients || []).length)
      h += '<p class="mail-cmp-warn">Новый получатель — его не было в переписке: ' + esc(d.new_recipients.join(", ")) + ". Перед отправкой спрошу ещё раз.</p>";
    h += "</div>";
    h += '<div class="mail-cmp-body-wrap"><textarea class="mail-cmp-body" data-mf="body" placeholder="Текст письма. «+» — быстрые действия, например слоты"' + (ro ? " readonly" : "") + ">" + esc(d.body) + "</textarea>";
    h += '<div class="mail-plus hidden" data-plus></div></div>';
    if ((d.attachments || []).length) {
      h += '<div class="mail-cmp-atts">';
      d.attachments.forEach(function (f) {
        h +=
          '<span class="mail-chip mail-chip--file">📎 ' +
          esc(f.name) +
          " · " +
          fmtSize(f.size) +
          (ro ? "" : '<button type="button" class="mail-chip-x" data-mc="att-x" data-id="' + esc(f.id) + '" aria-label="Убрать вложение">×</button>') +
          "</span>";
      });
      h += "</div>";
    }
    if (!ro) {
      h += '<div class="mail-cmp-tools">';
      h += '<button type="button" class="mail-tool" data-mc="attach" aria-label="Вложить файл" title="Вложить файл">📎</button><input type="file" multiple class="hidden" data-file />';
      h += '<button type="button" class="mail-tool" data-mc="ai" data-mode="short">Короче</button>';
      h += '<button type="button" class="mail-tool" data-mc="ai" data-mode="polite">Вежливее</button>';
      h += '<button type="button" class="mail-tool" data-mc="ai" data-mode="translate">Перевести</button>';
      h += '<button type="button" class="mail-tool" data-mc="fix">Поправь…</button>';
      if (composer.prevBody != null) h += '<button type="button" class="mail-tool mail-tool--undo" data-mc="ai-undo">Вернуть как было</button>';
      h += "</div>";
      h += '<div class="mail-cmp-fix hidden" data-fix><input type="text" placeholder="Что поправить? Например: убери про сроки" data-fix-input /><button type="button" class="mail-tool" data-mc="fix-go">Готово</button></div>';
    }
    if (d.error && composer.action && composer.action.status === "failed") h += '<p class="pf-field-error">' + esc(d.error) + "</p>";
    n.innerHTML = h;
    autoGrow(n.querySelector("[data-mf=body]"));
  }

  function fmtSize(b) {
    b = Number(b || 0);
    return b > 1048576 ? (b / 1048576).toFixed(1) + " МБ" : Math.max(1, Math.round(b / 1024)) + " КБ";
  }

  function autoGrow(t) {
    if (!t) return;
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight + 2, Math.round(window.innerHeight * (isMobile() ? 0.5 : 0.45))) + "px";
  }

  function setSaved(text) {
    var s = document.querySelector("#mail-composer [data-saved]");
    if (s) s.textContent = text || "";
  }

  function scheduleSave(patch) {
    if (!composer.action || composer.readOnly) return;
    composer.pending = Object.assign(composer.pending || {}, patch);
    setSaved("…");
    clearTimeout(composer.saveTimer);
    composer.saveTimer = setTimeout(flushSave, 600);
  }

  async function flushSave() {
    clearTimeout(composer.saveTimer);
    var patch = composer.pending;
    if (!patch || !composer.action) return;
    composer.pending = null;
    var id = composer.action.id;
    try {
      var res = await post("/mail/drafts/" + encodeURIComponent(id), patch);
      cacheAction(res.action);
      if (composer.action && composer.action.id === id) {
        composer.action = res.action;
        var fresh = res.action.draft || {};
        var structural = "to" in patch || "cc" in patch || "reply_all" in patch || "attachments" in patch;
        composer.d.new_recipients = fresh.new_recipients || [];
        if (structural) {
          composer.d.to = fresh.to;
          composer.d.cc = fresh.cc;
          composer.d.reply_all = fresh.reply_all;
          composer.d.attachments = fresh.attachments;
          var body = document.querySelector("#mail-composer [data-mf=body]");
          if (body) composer.d.body = body.value;
          renderComposer();
        }
        setSaved("Сохранено");
      }
    } catch (e) {
      setSaved("");
      toast((e && e.message) || "Не удалось сохранить черновик");
    }
  }

  function addChip(field, raw) {
    var list = String(raw || "")
      .split(/[,;\s]+/)
      .map(function (x) {
        return x.trim().toLowerCase();
      })
      .filter(Boolean);
    if (!list.length) return;
    var cur = (composer.d[field] || []).slice();
    list.forEach(function (a) {
      if (cur.indexOf(a) < 0) cur.push(a);
    });
    composer.d[field] = cur;
    var p = {};
    p[field] = cur;
    scheduleSave(p);
    flushSave();
  }

  async function sendDraft(confirmed) {
    var body = document.querySelector("#mail-composer [data-mf=body]");
    if (body) {
      composer.d.body = body.value;
      composer.pending = Object.assign(composer.pending || {}, { body: body.value });
    }
    await flushSave();
    var a = composer.action;
    if (!a) return;
    try {
      var res = await post("/gpt/calendar-actions/" + encodeURIComponent(a.id) + "/" + (confirmed ? "send_confirmed" : "send"), {});
      cacheAction(res.action);
      var n = document.getElementById("mail-composer");
      if (n) {
        n.classList.add("hidden");
        n.innerHTML = "";
      }
      if (chatInput()) chatInput().classList.remove("hidden");
      composer.action = null;
      composer.d = null;
      if (app().refreshFeed) app().refreshFeed();
    } catch (e) {
      var det = (e && e.detail) || {};
      if (e && e.status === 409 && det.code === "new_recipients") {
        if (await app().confirm(det.message || e.message)) return sendDraft(true);
        return;
      }
      toast((e && e.message) || "Не получилось отправить");
    }
  }

  async function onComposerClick(e) {
    var b = e.target.closest("[data-mc]");
    if (!b) {
      var chips = e.target.closest(".mail-chips");
      if (chips) {
        var inp = chips.querySelector("[data-chip-input]");
        if (inp) inp.focus();
      }
      return;
    }
    var act = b.getAttribute("data-mc");
    var n = document.getElementById("mail-composer");
    if (act === "close") return closeComposer();
    if (act === "expand") {
      composer.expanded = true;
      readBody();
      renderComposer();
      return;
    }
    if (act === "send") return sendDraft(false);
    if (act === "chip-x") {
      var f = b.getAttribute("data-field");
      composer.d[f] = (composer.d[f] || []).filter(function (x) {
        return x !== b.getAttribute("data-addr");
      });
      readBody();
      var p = {};
      p[f] = composer.d[f];
      scheduleSave(p);
      renderComposer();
      return flushSave();
    }
    if (act === "attach") {
      var fi = n.querySelector("[data-file]");
      if (fi) fi.click();
      return;
    }
    if (act === "att-x") {
      composer.d.attachments = (composer.d.attachments || []).filter(function (x) {
        return x.id !== b.getAttribute("data-id");
      });
      readBody();
      scheduleSave({ attachments: composer.d.attachments.map(function (x) { return x.id; }) });
      renderComposer();
      return flushSave();
    }
    if (act === "ai") return aiRewrite(b.getAttribute("data-mode"), "", b);
    if (act === "ai-undo") {
      composer.d.body = composer.prevBody;
      composer.prevBody = null;
      renderComposer();
      scheduleSave({ body: composer.d.body });
      return;
    }
    if (act === "fix") {
      var fx = n.querySelector("[data-fix]");
      if (fx) {
        fx.classList.toggle("hidden");
        var fin = fx.querySelector("[data-fix-input]");
        if (fin && !fx.classList.contains("hidden")) fin.focus();
      }
      return;
    }
    if (act === "fix-go") {
      var fin2 = n.querySelector("[data-fix-input]");
      return aiRewrite("custom", fin2 ? fin2.value : "", b);
    }
    if (act === "plus-insert") return plusInsert();
  }

  function readBody() {
    var t = document.querySelector("#mail-composer [data-mf=body]");
    if (t && composer.d) composer.d.body = t.value;
  }

  async function aiRewrite(mode, instruction, btn) {
    readBody();
    var body = composer.d.body;
    if (!String(body || "").trim()) {
      toast("Сначала напишите текст письма");
      return;
    }
    if (btn) {
      btn.disabled = true;
      btn.textContent = "…";
    }
    try {
      var res = await post("/mail/rewrite", { body: body, mode: mode, instruction: instruction });
      composer.prevBody = body;
      composer.d.body = res.body;
      renderComposer();
      scheduleSave({ body: res.body });
    } catch (e) {
      toast((e && e.message) || "Не получилось");
      renderComposer();
    }
  }

  function onComposerInput(e) {
    var t = e.target;
    if (!composer.d) return;
    var f = t.getAttribute("data-mf");
    if (f === "body") {
      composer.d.body = t.value;
      autoGrow(t);
      scheduleSave({ body: t.value });
      plusDetect(t);
    } else if (f === "subject") {
      composer.d.subject = t.value;
      scheduleSave({ subject: t.value });
    } else if (t.hasAttribute("data-chip-input") && /[,;\s]$/.test(t.value)) {
      readBody();
      addChip(t.getAttribute("data-chip-input"), t.value);
      renderComposer();
      var again = document.querySelector('#mail-composer [data-chip-input="' + t.getAttribute("data-chip-input") + '"]');
      if (again) again.focus();
    }
  }

  function onComposerChange(e) {
    var t = e.target;
    if (!composer.d) return;
    var f = t.getAttribute("data-mf");
    if (f === "reply_all") {
      readBody();
      scheduleSave({ reply_all: t.checked });
      flushSave();
    } else if (f === "account_id") {
      scheduleSave({ account_id: t.value });
    } else if (t.hasAttribute("data-file")) {
      uploadFiles(t.files);
    } else if (t.hasAttribute("data-chip-input") && t.value.trim()) {
      readBody();
      addChip(t.getAttribute("data-chip-input"), t.value);
      renderComposer();
    }
  }

  async function uploadFiles(files) {
    readBody();
    var ids = (composer.d.attachments || []).map(function (x) {
      return x.id;
    });
    for (var i = 0; i < (files || []).length; i++) {
      var fd = new FormData();
      fd.append("file", files[i], files[i].name);
      setSaved("Загружаю " + files[i].name + "…");
      try {
        var res = await api("/mail/attachments", { method: "POST", body: fd });
        composer.d.attachments = (composer.d.attachments || []).concat([res.attachment]);
        ids.push(res.attachment.id);
      } catch (e) {
        toast((e && e.message) || "Не удалось загрузить файл");
      }
    }
    renderComposer();
    scheduleSave({ attachments: ids });
    flushSave();
  }

  function onComposerKey(e) {
    if (e.key === "Escape") {
      if (composer.plus) {
        e.preventDefault();
        composer.plus.dismissedAt = composer.plus.start;
        hidePlus();
        return;
      }
      e.preventDefault();
      closeComposer();
      return;
    }
    if (e.key === "Enter" && composer.plus && (composer.plus.selected || []).length) {
      e.preventDefault();
      plusInsert();
      return;
    }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && e.target.getAttribute("data-mf") === "body") {
      e.preventDefault();
      sendDraft(false);
      return;
    }
    if (e.key === "Enter" && e.target.hasAttribute("data-chip-input")) {
      e.preventDefault();
      readBody();
      var field = e.target.getAttribute("data-chip-input");
      addChip(field, e.target.value);
      renderComposer();
      var again = document.querySelector('#mail-composer [data-chip-input="' + field + '"]');
      if (again) again.focus();
    }
  }

  // Свайп вниз за «ручку» на телефоне — как «Отмена» (черновик остаётся).
  function bindSwipe(n) {
    var y0 = null;
    n.addEventListener(
      "touchstart",
      function (e) {
        y0 = e.target.closest(".mail-cmp-grab, .mail-cmp-head") ? e.touches[0].clientY : null;
      },
      { passive: true }
    );
    n.addEventListener(
      "touchend",
      function (e) {
        if (y0 != null && e.changedTouches[0].clientY - y0 > 60) closeComposer();
        y0 = null;
      },
      { passive: true }
    );
  }

  /* ——— «+» — быстрые действия (пока одно: слоты) ——— */

  var QUICK_ACTIONS = [{ id: "slots", word: "слоты", title: "Слоты — свободное время" }];

  function plusDetect(t) {
    var caret = t.selectionStart;
    var before = t.value.slice(0, caret);
    var m = /(^|\s)\+([^\n+]{0,80})$/.exec(before);
    if (!m) return hidePlus();
    var start = caret - m[2].length - 1;
    var q = m[2];
    var first = (q.trim().split(/\s+/)[0] || "").toLowerCase();
    var action = QUICK_ACTIONS.filter(function (x) {
      return !first || x.word.indexOf(first) === 0 || first.indexOf(x.word.slice(0, 4)) === 0;
    })[0];
    if (!action) return hidePlus(); // «+7 999…» — это не команда
    if (composer.plus && composer.plus.dismissedAt === start) return;
    var prev = composer.plus;
    composer.plus = { start: start, query: q, action: action.id, data: prev && prev.start === start ? prev.data : null, selected: prev && prev.start === start ? prev.selected : [] };
    renderPlus(t);
    clearTimeout(composer.plusTimer);
    composer.plusTimer = setTimeout(function () {
      loadSlots(q);
    }, prev && prev.start === start ? 450 : 0);
  }

  function hidePlus() {
    var p = document.querySelector("#mail-composer [data-plus]");
    if (p) p.classList.add("hidden");
    // закрытое по Esc меню помним, чтобы оно не всплыло снова на том же «+»
    if (composer.plus && composer.plus.dismissedAt == null) composer.plus = null;
  }

  async function loadSlots(q) {
    var plus = composer.plus;
    if (!plus || plus.dismissedAt != null) return;
    var query = q.replace(/^\s*слот[а-я]*/i, "").trim();
    plus.loading = true;
    renderPlus();
    try {
      var res = await post("/mail/composer/slots", { query: query });
      if (composer.plus !== plus) return;
      plus.data = res;
      plus.selected = [];
    } catch (e) {
      if (composer.plus !== plus) return;
      plus.data = { ok: false, error: (e && e.message) || "Не получилось" };
    }
    plus.loading = false;
    renderPlus();
  }

  function renderPlus(t) {
    var box = document.querySelector("#mail-composer [data-plus]");
    var plus = composer.plus;
    if (!box || !plus || plus.dismissedAt != null) return;
    var d = plus.data;
    var h = '<div class="mail-plus-head">Слоты · свободное время</div>';
    if (plus.loading && !d) h += '<p class="mail-plus-sub">Смотрю календарь…</p>';
    else if (d && !d.ok) h += '<p class="mail-plus-sub">' + esc(d.error || "Не получилось") + "</p>";
    else if (d) {
      var who = (d.people || []).length ? "вы и " + d.people.join(", ") : "только вы";
      var day = d.slots && d.slots[0] ? d.slots[0].label.split(",")[0] : d.date;
      h += '<p class="mail-plus-sub">' + esc(day) + " · " + esc(who) + " · " + d.duration + " мин" + (plus.loading ? " · обновляю…" : "") + "</p>";
      (d.unavailable || []).forEach(function (n) {
        h += '<p class="mail-plus-warn">Не вижу календарь: ' + esc(n) + " — показываю только ваши окна</p>";
      });
      (d.not_found || []).forEach(function (n) {
        h += '<p class="mail-plus-warn">«' + esc(n) + "» нет в контактах</p>";
      });
      if (!(d.slots || []).length) h += '<p class="mail-plus-sub">Свободных окон нет — попробуйте другой день: «+слоты завтра»</p>';
      (d.slots || []).forEach(function (s, i) {
        h +=
          '<label class="mail-plus-slot"><input type="checkbox" data-slot="' +
          i +
          '"' +
          (plus.selected.indexOf(i) >= 0 ? " checked" : "") +
          " /> " +
          esc(s.label) +
          "</label>";
      });
    }
    h +=
      '<div class="mail-plus-foot"><span>Например: +слоты я и Мыздриков следующий вторник</span><button type="button" class="mail-tool mail-tool--primary" data-mc="plus-insert"' +
      ((plus.selected || []).length ? "" : " disabled") +
      ">Вставить</button></div>";
    box.innerHTML = h;
    box.classList.remove("hidden");
    box.onchange = function (e) {
      var i = e.target.getAttribute("data-slot");
      if (i == null) return;
      i = parseInt(i, 10);
      plus.selected = plus.selected.filter(function (x) {
        return x !== i;
      });
      if (e.target.checked) plus.selected.push(i);
      plus.selected.sort(function (a, b) {
        return a - b;
      });
      var ins = box.querySelector("[data-mc=plus-insert]");
      if (ins) ins.disabled = !plus.selected.length;
    };
    positionPlus(t || document.querySelector("#mail-composer [data-mf=body]"), box);
  }

  // Меню — над строкой с курсором (зеркало textarea, чтобы узнать координаты курсора).
  function positionPlus(t, box) {
    if (!t) return;
    var mirror = document.createElement("div");
    var cs = window.getComputedStyle(t);
    ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingLeft", "paddingRight", "borderTopWidth", "borderLeftWidth", "boxSizing", "whiteSpace", "wordWrap"].forEach(function (k) {
      mirror.style[k] = cs[k];
    });
    mirror.style.position = "absolute";
    mirror.style.visibility = "hidden";
    mirror.style.whiteSpace = "pre-wrap";
    mirror.style.width = t.clientWidth + "px";
    mirror.textContent = t.value.slice(0, t.selectionStart);
    var mark = document.createElement("span");
    mark.textContent = "​";
    mirror.appendChild(mark);
    document.body.appendChild(mirror);
    var top = mark.offsetTop - t.scrollTop;
    var left = Math.min(mark.offsetLeft, Math.max(0, t.clientWidth - box.offsetWidth));
    document.body.removeChild(mirror);
    box.style.left = Math.max(0, left) + "px";
    box.style.bottom = Math.max(0, t.offsetHeight - top + 4) + "px";
  }

  function slotsText(labels, tzLabel) {
    if (!labels.length) return "";
    var day0 = labels[0].split(",")[0];
    var parts = [labels[0]].concat(
      labels.slice(1).map(function (lb) {
        return lb.split(",")[0] === day0 ? lb.split(", ").slice(1).join(", ") : lb;
      })
    );
    var body = parts.length > 1 ? parts.slice(0, -1).join(", ") + " или " + parts[parts.length - 1] : parts[0];
    return "Предлагаю: " + body + (tzLabel ? " (" + tzLabel + ")" : "");
  }

  function plusInsert() {
    var plus = composer.plus;
    var t = document.querySelector("#mail-composer [data-mf=body]");
    if (!plus || !t || !plus.data || !(plus.selected || []).length) return;
    var labels = plus.selected.map(function (i) {
      return plus.data.slots[i].label;
    });
    var text = slotsText(labels, plus.data.tz_label);
    var caret = t.selectionStart;
    t.value = t.value.slice(0, plus.start) + text + t.value.slice(caret);
    var pos = plus.start + text.length;
    composer.plus = null;
    hidePlus();
    t.focus();
    t.setSelectionRange(pos, pos);
    composer.d.body = t.value;
    autoGrow(t);
    scheduleSave({ body: t.value });
  }

  window.LeoMail = { openProfile: openProfile, renderAction: renderAction, openThread: openThread, openComposer: openComposer };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
