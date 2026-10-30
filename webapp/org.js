/* Профиль → «Организация»: создать организацию, участники и роли, домен почты, ссылки-приглашения.
 * Вход по ссылке: /webapp/?join=<token> или startapp=join_<token> в миниаппе. */
(function () {
  "use strict";

  var ROLE_LABEL = { owner: "Владелец", admin: "Админ", member: "Участник" };
  var state = { overview: null, detail: null, market: null, contacts: null, queue: Promise.resolve(), saved: {}, slugTimer: null, openMember: null };

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
  function root() {
    return document.getElementById("org-root");
  }
  function toast(msg) {
    if (app().toast) app().toast(msg);
  }
  function errText(e) {
    return (e && (e.detail || e.message)) || "Не получилось";
  }
  function inTelegram() {
    var tg = window.Telegram && window.Telegram.WebApp;
    return !!(tg && tg.initData);
  }
  async function api(path, opts) {
    return app().apiFetch("/orgs" + path, opts || { method: "GET" });
  }
  function json(method, body) {
    return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  }

  // Действия идут по очереди: раньше второе изменение, пока шло первое, молча терялось.
  function act(fn) {
    state.queue = state.queue.then(fn).catch(function (e) {
      toast(errText(e));
    });
    return state.queue;
  }

  // Автосохранение: рядом с заголовком раздела — «Сохраняю…», «Сохранено ✓» или ошибка.
  function saveMark(key) {
    var s = state.saved[key];
    return '<span class="org-save' + (s ? " org-save--" + s.kind : "") + '" data-save="' + key + '">' + esc(s ? s.text : "") + "</span>";
  }
  function setSaved(key, kind, text) {
    state.saved[key] = kind ? { kind: kind, text: text } : null;
    var el = root() && root().querySelector('[data-save="' + key + '"]');
    if (el) {
      el.className = "org-save" + (kind ? " org-save--" + kind : "");
      el.textContent = text || "";
    }
    if (kind === "ok") {
      setTimeout(function () {
        if (state.saved[key] && state.saved[key].kind === "ok") setSaved(key, null, "");
      }, 2500);
    }
  }
  // Сохранить одно поле: метка «Сохраняю…» → ответ сервера → «Сохранено ✓»; при ошибке — текст ошибки и откат.
  function autosave(key, request) {
    setSaved(key, "busy", "Сохраняю…");
    return act(async function () {
      try {
        state.detail = await request();
        setSaved(key, "ok", "Сохранено ✓");
      } catch (e) {
        setSaved(key, "err", errText(e));
      }
      await reload();
    });
  }

  // ---------- загрузка ----------

  async function open() {
    if (app().profileScreen) app().profileScreen("org");
    var el = root();
    if (el && !el.innerHTML) el.innerHTML = '<p class="pf-hint">Загружаю…</p>';
    await reload();
  }

  async function reload() {
    try {
      state.overview = await api("");
      var cur = state.overview.current_id;
      if (app().setMarketAvailable) app().setMarketAvailable(!!cur);
      state.detail = cur ? await api("/" + cur) : null;
      if (state.detail && state.detail.can && state.detail.can.invite) await loadContacts();
      state.market = cur ? await api("/" + cur + "/market").catch(function () { return null; }) : null;
    } catch (e) {
      var el = root();
      if (el) el.innerHTML = '<p class="error">' + esc(errText(e)) + "</p>";
      return;
    }
    render();
    syncHint();
  }

  function syncHint() {
    var hint = document.getElementById("profile-org-hint");
    if (!hint || !state.overview) return;
    var cur = state.detail && state.detail.org;
    var sug = (state.overview.suggestions || []).filter(function (s) {
      return !s.pending;
    })[0];
    hint.textContent = cur ? cur.name : sug ? sug.name + " уже в Орбите — вступить" : "Команда, участники и права";
  }

  // ---------- отрисовка ----------
  // Только компоненты Профиля (pf-*): шапка как у интеграций, строки списков, pf-select справа,
  // pf-switch, pf-chips, pf-del, pf-wallet. Своих классов — минимум, только раскладка.

  var ICO = {
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" /><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></svg>',
    mail: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v6h-6" /></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" /></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12" /></svg>',
  };

  function textHtml(title, sub, titleCls) {
    return '<span class="pf-row-text"><span class="pf-row-title' + (titleCls ? " " + titleCls : "") + '">' + esc(title) + "</span>" +
      (sub ? '<span class="pf-row-sub pf-row-sub--wrap">' + esc(sub) + "</span>" : "") + "</span>";
  }
  // Строка «подпись слева — значение/поле справа».
  function staticRow(title, sub, right, tag) {
    tag = tag || "div";
    return "<" + tag + ' class="pf-row pf-row--static"><span class="pf-row-body">' + textHtml(title, sub) + (right || "") + "</span></" + tag + ">";
  }
  function selectHtml(options, value, attrs) {
    return '<select class="pf-select" ' + attrs + ">" +
      options.map(function (o) {
        return '<option value="' + esc(o[0]) + '"' + (o[0] === value ? " selected" : "") + ">" + esc(o[1]) + "</option>";
      }).join("") + "</select>";
  }
  function delBtn(attrs, label, accent, icon) {
    return '<button type="button" class="pf-del' + (accent ? " pf-del--accent" : "") + '" ' + attrs + ' aria-label="' + esc(label) + '" title="' + esc(label) + '">' +
      (icon || ICO.x).replace("<svg ", '<svg aria-hidden="true" ') + "</button>";
  }
  function addRow(attrs, title, icon) {
    return '<button type="button" class="pf-row pf-add-row" ' + attrs + '><span class="pf-ico" aria-hidden="true">' + (icon || ICO.plus) + "</span>" +
      '<span class="pf-row-body">' + textHtml(title) + "</span></button>";
  }
  function initials(name) {
    var words = String(name || "").replace(/^@/, "").replace(/@.*$/, "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
    return ((words[0] || "?").charAt(0) + (words[1] || "").charAt(0)).toUpperCase();
  }
  function group(title, saveKey, inner) {
    return '<div class="pf-group"><p class="pf-group-title">' + esc(title) + (saveKey ? saveMark(saveKey) : "") + "</p>" + inner + "</div>";
  }

  function render() {
    var el = root();
    if (!el) return;
    var ov = state.overview || {};
    var html = state.detail ? heroHtml(state.detail.org) : "";
    if ((ov.orgs || []).length > 1) html += switcherHtml(ov);
    html += suggestionsHtml(ov);
    html += state.detail ? detailHtml(state.detail) : createHtml(ov);
    el.innerHTML = html;
  }

  function heroHtml(org) {
    var pic = org.logo_url ? '<img src="' + esc(org.logo_url) + '" alt="" />' : esc(initials(org.name));
    return '<div class="pf-hero"><span class="pf-hero-ico org-hero-ico" aria-hidden="true">' + pic + "</span>" +
      '<p class="pf-hero-title">' + esc(org.name) + "</p>" +
      '<p class="pf-hero-text">' + esc(ROLE_LABEL[org.my_role] || "") + " · " + org.member_count + " " +
      plural(org.member_count, "участник", "участника", "участников") + "</p></div>";
  }

  function switcherHtml(ov) {
    var opts = ov.orgs.map(function (o) { return [o.id, o.name]; });
    return '<div class="pf-list">' +
      staticRow("Текущая организация", "", selectHtml(opts, ov.current_id, 'data-org="switch" aria-label="Текущая организация"'), "label") + "</div>";
  }

  function suggestionsHtml(ov) {
    var rows = (ov.suggestions || [])
      .map(function (s) {
        var sub = s.pending ? "Заявка отправлена — ждём одобрения админа" : "Коллеги с вашего домена уже здесь";
        var btns = s.pending
          ? ""
          : '<span class="org-actions"><button type="button" class="pf-link pf-link--muted" data-org="dismiss" data-id="' + s.id + '">Не сейчас</button>' +
            '<button type="button" class="pf-link" data-org="join" data-id="' + s.id + '">Вступить</button></span>';
        var pic = s.logo_url ? '<img src="' + esc(s.logo_url) + '" alt="" />' : esc(initials(s.name));
        return '<div class="pf-row pf-row--static"><span class="pf-avatar" aria-hidden="true">' + pic + "</span>" +
          '<span class="pf-row-body">' + textHtml(s.name, sub) + btns + "</span></div>";
      })
      .join("");
    return rows ? group("Можно вступить", "", '<div class="pf-list">' + rows + "</div>") : "";
  }

  function createHtml(ov) {
    var doms = ov.my_domains || [];
    var domSel = doms.length
      ? '<label class="pf-field"><span class="pf-field-label">Домен почты</span><select class="pf-input" id="org-new-domain">' +
        doms.map(function (d) { return '<option value="' + esc(d) + '">@' + esc(d) + "</option>"; }).join("") +
        '<option value="">Без домена</option></select></label>'
      : "";
    var domHint = doms.length
      ? "Коллеги с почтой на этом домене смогут вступить сами — по заявке или сразу, как решите."
      : "Подключите рабочую почту или Google-календарь — тогда коллеги с вашего домена смогут вступать сами. Без этого приглашайте ссылкой.";
    return (
      '<div class="pf-empty org-empty"><span class="pf-empty-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 21h18" /><path d="M5 21V7l7-4 7 4v14" /><path d="M10 21v-4h4v4" /></svg></span>' +
      '<p class="pf-empty-title">Организации пока нет</p>' +
      '<p class="pf-empty-text">Организация — общее пространство команды: участники, роли и общие настройки, например выпуск «Рынок».</p></div>' +
      "<div>" +
      '<div class="pf-list">' +
      '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-new-name" maxlength="80" placeholder="Например, Обучат" /></label>' +
      domSel + "</div>" +
      '<p class="pf-hint">' + esc(domHint) + "</p></div>" +
      '<button type="button" class="pf-btn" data-org="create">Создать организацию</button>'
    );
  }

  function detailHtml(d) {
    var org = d.org;
    var can = d.can || {};
    var html = "";

    if (can.edit_org) {
      html += group("Название и адрес", "org", '<div class="pf-list">' +
        '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-name" maxlength="80" value="' + esc(org.name) + '" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Короткий адрес</span><input type="text" class="pf-input" id="org-slug" maxlength="40" value="' + esc(org.slug) + '" autocapitalize="off" spellcheck="false" /></label>' +
        '</div><p class="pf-hint" id="org-slug-hint">Латиница, цифры и дефис. Пригодится для публичной страницы выпуска и поддомена.</p>');
    }

    html += membersHtml(d);
    if (can.invite) html += joinHtml(d) + invitesHtml(d);
    html += creditsHtml(d);
    html += billingHtml(d);
    html += marketHtml(state.market);

    var danger = "";
    if ((state.overview.orgs || []).length) {
      danger += '<button type="button" class="pf-row pf-row--danger" data-org="leave"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Выйти из организации</span></span></span></button>';
    }
    if (can.delete_org) {
      danger += '<button type="button" class="pf-row pf-row--danger" data-org="delete"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Удалить организацию</span></span></span></button>';
    }
    if (danger) html += '<div class="pf-list">' + danger + "</div>";
    if ((state.overview.orgs || []).length) {
      html += '<button type="button" class="pf-btn pf-btn--ghost" data-org="show-create">Создать ещё одну организацию</button>';
    }
    return html;
  }

  // Выпуск «Рынок» организации: о компании, конкуренты, темы, свои источники, время + личное «в Telegram».
  function marketHtml(m) {
    if (!m || !m.settings) return "";
    var st = m.settings;
    var list = "";
    if (m.can_edit) {
      list +=
        '<label class="pf-field"><span class="pf-field-label">О компании</span><textarea class="pf-input org-mkt-area" id="org-mkt-about" rows="3" maxlength="1500" placeholder="Чем занимаетесь, для кого, что важно отслеживать">' + esc(st.about) + "</textarea></label>" +
        '<label class="pf-field"><span class="pf-field-label">Конкуренты</span><input type="text" class="pf-input" id="org-mkt-competitors" value="' + esc((st.competitors || []).join(", ")) + '" placeholder="Через запятую: Gong, Clari, amoCRM" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Темы</span><input type="text" class="pf-input" id="org-mkt-topics" value="' + esc((st.topics || []).join(", ")) + '" placeholder="Через запятую: речевая аналитика, ИИ в продажах" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Свои источники</span><textarea class="pf-input org-mkt-area" id="org-mkt-sources" rows="2" placeholder="Ссылки на RSS, по одной в строке">' + esc((st.sources || []).join("\n")) + "</textarea></label>" +
        staticRow("Время выпуска", "", '<input type="time" class="pf-num-input org-time" id="org-mkt-time" value="' + esc(st.time) + '" aria-label="Время выпуска" />', "label");
    } else {
      [["Конкуренты", (st.competitors || []).join(", ")], ["Темы", (st.topics || []).join(", ")], ["Время выпуска", st.time]].forEach(function (r) {
        if (r[1]) list += staticRow(r[0], r[1]);
      });
    }
    list += staticRow("Присылать мне выпуск в Telegram", "Коротко главное и ссылка на выпуск",
      '<input type="checkbox" class="pf-switch" id="org-mkt-telegram"' + (m.telegram ? " checked" : "") + " />", "label");
    if (m.can_edit && ((st.competitors || []).length || (st.topics || []).length)) {
      list += addRow('data-org="mkt-build"', "Собрать выпуск сейчас", ICO.refresh);
    }
    return group("Выпуск «Рынок»", "market",
      '<div><div class="pf-list">' + list + "</div>" +
      '<p class="pf-hint">Укажите конкурентов или темы — и каждое утро Лео будет собирать в «Дайджест → Рынок» выпуск для вашей компании: свежие новости по ним, выводы и идеи. Без них выпуск общий. Сборка вручную занимает пару минут.</p></div>');
  }

  function saveMarket(patch) {
    var id = currentId();
    setSaved("market", "busy", "Сохраняю…");
    return act(async function () {
      try {
        state.market = await api("/" + id + "/market", json("PATCH", patch));
        setSaved("market", "ok", "Сохранено ✓");
      } catch (e) {
        setSaved("market", "err", errText(e));
      }
      await reload();
    });
  }

  function planLabel(d, id) {
    var p = (d.plans || []).filter(function (x) { return x.id === id; })[0];
    return p ? p.label : id;
  }

  function planSelect(d, value, attrs) {
    return selectHtml((d.plans || []).map(function (p) { return [p.id, p.label]; }), value, attrs);
  }

  function billingHtml(d) {
    var org = d.org;
    var can = d.can || {};
    var used = org.seats_used || 0;
    var seats = org.seats || 0;
    var inner = d.billing_enabled ? "" : '<p class="pf-note pf-note--warn">Оплата ещё не подключена — тарифы можно менять, но они пока ни на что не влияют.</p>';
    var list = "";
    if (can.billing) {
      list += staticRow("Тариф организации", "", planSelect(d, org.plan, 'id="org-plan" aria-label="Тариф организации"'), "label") +
        staticRow("Мест с платным тарифом", "",
          '<input type="number" class="pf-num-input org-num" id="org-seats" min="0" max="10000" inputmode="numeric" value="' + seats + '" aria-label="Мест с платным тарифом" />', "label");
    } else {
      list += staticRow("Тариф организации", "", '<span class="pf-row-val">' + esc(planLabel(d, org.plan)) + "</span>");
    }
    list += staticRow("Занято мест", "Место занимает участник с платным тарифом",
      '<span class="pf-row-val' + (used > seats ? " pf-status--err" : "") + '">' + used + " из " + seats + "</span>");
    inner += "<div>" + '<div class="pf-list">' + list + "</div>" +
      (can.billing ? '<p class="pf-hint">Тариф каждому участнику — в его строке в списке участников.</p>' : "") + "</div>";
    return group("Тариф", "billing", inner);
  }

  // ---------- кредиты ИИ: счёт организации, лимиты и модели участников ----------

  function fmtNum(n) {
    return Math.round(Number(n) || 0).toLocaleString("ru-RU");
  }

  // Счёт — тем же блоком, что баланс в Профиле (pf-wallet).
  function creditsHtml(d) {
    var c = d.credits;
    if (!c) return "";
    var wallet = '<section class="pf-wallet" aria-label="Счёт организации"><div class="pf-wallet-top"><div>' +
      '<p class="pf-wallet-label">Счёт организации</p><p class="pf-wallet-balance">' + fmtNum(c.balance) + " кр</p></div></div>" +
      '<div class="pf-wallet-foot"><span class="org-wallet-sub">≈ ' + fmtNum(c.balance_rub) + " ₽ по тарифу Solo · " +
      String(c.rub_per_credit).replace(".", ",") + " ₽ за кредит</span>" +
      (c.can_manage ? '<button type="button" class="pf-pay" data-org="credits-topup">Пополнить</button>' : "") + "</div></section>";
    var hint = c.can_manage
      ? '<p class="pf-hint">У каждого участника дневной лимит (по умолчанию ' + fmtNum(c.default_daily_limit) +
        " кр). Сверх лимита Лео продолжает работать, кредиты списываются со счёта организации. Лимит и модели — в строке участника.</p>"
      : "";
    return group("Кредиты ИИ", "credits", "<div>" + wallet + hint + "</div>");
  }

  function memberAi(d, m) {
    var c = d.credits;
    var ai = c && c.members && c.members[String(m.user_id)];
    return ai && m.status === "active" ? ai : null;
  }

  // Настройки ИИ участника внутри раскрытой строки: дневной лимит и модели чипами.
  function memberAiHtml(d, m, ai) {
    var models = d.credits.models || [];
    var allowed = ai.allowed_models;  // null — доступны все
    var all = !allowed;
    var uid = esc(m.user_id);
    var spent = "Сегодня " + fmtNum(ai.spent_today) + " кр" + (ai.over_today > 0 ? ", сверх лимита " + fmtNum(ai.over_today) : "");
    var chips = '<button type="button" class="pf-chip' + (all ? " is-on" : "") + '" data-org-ai="all" data-uid="' + uid + '" aria-pressed="' + all + '">Все модели</button>' +
      models.map(function (x) {
        var on = all || allowed.indexOf(x.id) >= 0;
        return '<button type="button" class="pf-chip' + (on ? " is-on" : "") + '" data-org-ai="model" data-uid="' + uid + '" data-model="' + esc(x.id) +
          '" aria-pressed="' + on + '">' + esc(x.name) + "</button>";
      }).join("");
    return staticRow("Дневной лимит, кр", spent,
        '<input type="number" class="pf-num-input org-num" data-org-ai="limit" data-uid="' + uid + '" min="0" max="1000000" step="50" inputmode="numeric" value="' +
        Math.round(ai.daily_limit) + '" aria-label="Дневной лимит, кредитов" />', "label") +
      '<div class="pf-field"><span class="pf-field-label">Доступные модели</span><div class="pf-chips org-chips">' + chips + "</div></div>";
  }

  function saveMemberAi(uid, body) {
    var id = currentId();
    return autosave("members", function () {
      return api("/" + id + "/members/" + uid + "/ai", json("PATCH", body));
    });
  }

  function aiAllowed(uid) {
    var ai = memberAi(state.detail, { user_id: uid, status: "active" });
    var all = ((state.detail.credits || {}).models || []).map(function (x) { return x.id; });
    return { all: all, allowed: ai && ai.allowed_models ? ai.allowed_models.slice() : null };
  }

  // Чипы моделей: «Все модели» снимает ограничения; снять галочку с модели при «Все» — разрешить все, кроме неё.
  function onAiClick(el) {
    var uid = el.getAttribute("data-uid");
    var kind = el.getAttribute("data-org-ai");
    var cur = aiAllowed(uid);
    if (kind === "all") {
      if (cur.allowed) saveMemberAi(uid, { all_models: true });
      return;
    }
    var model = el.getAttribute("data-model");
    var picked = cur.allowed || cur.all.slice();
    var i = picked.indexOf(model);
    if (i >= 0) picked.splice(i, 1);
    else picked.push(model);
    if (!picked.length) {
      toast("Оставьте участнику хотя бы одну модель");
      return;
    }
    if (picked.length === cur.all.length) saveMemberAi(uid, { all_models: true });
    else saveMemberAi(uid, { allowed_models: picked });
  }

  function onAiChange(e) {
    var el = e.target.closest('[data-org-ai="limit"]');
    if (!el) return false;
    var v = Number(el.value);
    if (!isFinite(v) || v < 0) {
      toast("Лимит — число от 0");
      return true;
    }
    saveMemberAi(el.getAttribute("data-uid"), { daily_limit: v });
    return true;
  }

  function memberName(d, m) {
    var name = m.user_id ? m.name : String(m.name || "").replace(/^tg:/, "@");
    return m.user_id === d.my_user_id ? name + " (вы)" : name;
  }

  function memberSub(d, m, ai) {
    if (m.status === "pending") return "Просится в организацию";
    if (m.status === "invited") return "Приглашён — появится после первого входа в Орбиту · " + planLabel(d, m.plan);
    var parts = [];
    if (m.username) parts.push("@" + m.username);
    parts.push(ROLE_LABEL[m.role] || "");
    parts.push(planLabel(d, m.plan));
    if (ai) parts.push("лимит " + fmtNum(ai.daily_limit) + " кр");
    return parts.filter(Boolean).join(" · ");
  }

  // Участник — строка с аватаром; у руководителя строка раскрывается: роль, тариф, лимит, модели, исключить.
  function memberHtml(d, m) {
    var can = d.can || {};
    var me = d.org.my_role;
    var ai = memberAi(d, m);
    var name = memberName(d, m);
    var avatar = '<span class="pf-avatar" aria-hidden="true">' + esc(initials(name.replace(/ \(вы\)$/, ""))) + "</span>";
    var sub = memberSub(d, m, ai);
    if (m.status === "pending") {
      var acts = can.approve
        ? '<span class="org-actions"><button type="button" class="pf-link pf-link--muted" data-org="reject" data-uid="' + esc(m.user_id) + '">Отклонить</button>' +
          '<button type="button" class="pf-link" data-org="approve" data-uid="' + esc(m.user_id) + '">Принять</button></span>'
        : "";
      return '<div class="pf-row pf-row--static">' + avatar + '<span class="pf-row-body">' + textHtml(name, sub) + acts + "</span></div>";
    }
    var canRole = m.status === "active" && can.set_role && m.user_id !== d.my_user_id;
    var canRemove = can.remove_member && m.role !== "owner" && (m.role !== "admin" || me === "owner");
    var aiEdit = ai && d.credits.can_manage;
    if (!(canRole || can.billing || canRemove || aiEdit)) {
      return '<div class="pf-row pf-row--static">' + avatar + '<span class="pf-row-body">' + textHtml(name, sub) + "</span></div>";
    }
    var open = state.openMember === String(m.id);
    var html = '<button type="button" class="pf-row" data-org="member-toggle" data-mid="' + m.id + '" aria-expanded="' + open + '">' + avatar +
      '<span class="pf-row-body">' + textHtml(name, sub) + '<span class="pf-chev' + (open ? " pf-chev--open" : "") + '" aria-hidden="true">›</span></span></button>';
    if (!open) return html;
    var edit = "";
    if (canRole) {
      edit += staticRow("Роль", "", selectHtml(["owner", "admin", "member"].map(function (r) { return [r, ROLE_LABEL[r]]; }), m.role,
        'data-org="role" data-uid="' + esc(m.user_id) + '" aria-label="Роль"'), "label");
    }
    if (can.billing) {
      edit += staticRow("Тариф", "", planSelect(d, m.plan, 'data-org="member-plan" data-mid="' + m.id + '" aria-label="Тариф участника"'), "label");
    }
    if (aiEdit) edit += memberAiHtml(d, m, ai);
    if (canRemove) {
      edit += '<button type="button" class="pf-row" data-org="remove" data-mid="' + m.id + '"><span class="pf-row-body">' +
        textHtml(m.status === "invited" ? "Отозвать приглашение" : "Исключить из организации", "", "pf-link--danger") + "</span></button>";
    }
    return html + '<div class="org-member-edit">' + edit + "</div>";
  }

  function membersHtml(d) {
    var can = d.can || {};
    var rows = (d.members || []).map(function (m) { return memberHtml(d, m); }).join("");
    var add = "";
    if (can.invite) {
      var opts = contactOptions(d);
      if (opts) {
        add += '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
          '<span class="pf-row-body"><select class="pf-input" data-org="add-contact" aria-label="Добавить из контактов">' +
          '<option value="">Добавить из контактов…</option>' + opts + "</select></span></div>";
      }
      add += '<div class="pf-row pf-row--static"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.mail + "</span>" +
        '<span class="pf-row-body"><input type="text" class="pf-input" id="org-add-key" placeholder="Почта или @ник в Telegram" autocapitalize="off" spellcheck="false" aria-label="Почта или @ник в Telegram" />' +
        '<button type="button" class="pf-link" data-org="add">Добавить</button></span></div>';
    }
    return group("Участники", "members", "<div>" + '<div class="pf-list">' + rows + add + "</div>" +
      (can.set_role ? '<p class="pf-hint">Админ приглашает и исключает участников и настраивает выпуск «Рынок». Владелец ещё меняет название и адрес, назначает админов. Назначить другого владельцем — передать владение.</p>' : "") +
      "</div>");
  }

  // Ключ контакта для добавления: почта (по ней находим Telegram через контакты) или @ник.
  function contactKey(c) {
    var email = String(c.email || "").trim().toLowerCase();
    if (email && email.indexOf("@") > 0) return email;
    var nick = String(c.telegram_username || "").trim().replace(/^@/, "").toLowerCase();
    if (nick) return "@" + nick;
    var key = String(c.key || "");
    return key.indexOf("tg:") === 0 ? "@" + key.slice(3) : "";
  }

  function contactOptions(d) {
    var taken = {};
    (d.members || []).forEach(function (m) {
      if (m.invited_key) taken[String(m.invited_key).replace(/^tg:/, "@").toLowerCase()] = 1;
      if (m.username) taken["@" + String(m.username).toLowerCase()] = 1;
    });
    return (state.contacts || [])
      .map(function (c) {
        var key = contactKey(c);
        var nick = String(c.telegram_username || "").replace(/^@/, "").toLowerCase();
        if (!key || taken[key] || (nick && taken["@" + nick])) return "";
        var label = String(c.name || "").trim() || key;
        var extra = key !== label ? " · " + key : "";
        return '<option value="' + esc(key) + '">' + esc(label + extra) + "</option>";
      })
      .filter(Boolean)
      .join("");
  }

  async function loadContacts() {
    if (state.contacts) return;
    try {
      var r = await app().apiFetch("/contacts", { method: "GET" });
      state.contacts = (r && r.items) || [];
    } catch (_) {
      state.contacts = [];
    }
  }

  function addMember(key) {
    key = String(key || "").trim();
    if (!key) return Promise.resolve();
    var id = currentId();
    setSaved("members", "busy", "Добавляю…");
    return act(async function () {
      try {
        var r = await api("/" + id + "/members", json("POST", { key: key }));
        state.detail = r;
        setSaved("members", "ok", r.added && r.added.status === "invited" ? "Приглашён — появится после первого входа" : "Добавлен ✓");
      } catch (e) {
        setSaved("members", "err", errText(e));
      }
      await reload();
    });
  }


  function joinHtml(d) {
    var org = d.org;
    var can = d.can || {};
    var list = "";
    (org.domains || []).forEach(function (dom) {
      list += '<div class="pf-row pf-row--static"><span class="pf-ico" aria-hidden="true">' + ICO.mail + '</span><span class="pf-row-body">' +
        textHtml("@" + dom, "Коллеги с этим доменом почты видят организацию") +
        (can.edit_org ? '<button type="button" class="pf-link pf-link--muted" data-org="domain-del" data-domain="' + esc(dom) + '">Убрать</button>' : "") +
        "</span></div>";
    });
    if ((org.domains || []).length) {
      list += staticRow("Коллеги с домена", org.join_mode === "auto" ? "Вступают без одобрения" : "Вы одобряете каждую заявку",
        selectHtml([["request", "По заявке"], ["auto", "Сразу"]], org.join_mode, 'data-org="join-mode" aria-label="Как вступают коллеги с домена"'), "label");
    }
    var free = (d.my_domains || []).filter(function (x) { return (org.domains || []).indexOf(x) < 0; });
    if (can.edit_org && free.length) list += addRow('data-org="domain-add" data-domain="' + esc(free[0]) + '"', "Добавить @" + free[0]);
    if (!list) return "";
    return group("Вход по домену почты", "join", '<div class="pf-list">' + list + "</div>");
  }

  function invitesHtml(d) {
    var tg = inTelegram();
    var list = (d.invites || [])
      .map(function (i) {
        var url = (tg && i.urls.telegram) || i.urls.web;
        return '<div class="pf-row pf-row--static"><span class="pf-ico" aria-hidden="true">' + ICO.link + '</span><span class="pf-row-body">' +
          '<span class="pf-row-text"><span class="pf-row-title">До ' + esc(fmtDate(i.expires_at)) + "</span>" +
          '<span class="pf-row-sub">' + esc(url.replace(/^https?:\/\//, "")) + "</span></span>" +
          '<span class="org-actions">' + delBtn('data-org="copy" data-url="' + esc(url) + '"', "Копировать", true, ICO.copy) +
          delBtn('data-org="revoke" data-token="' + esc(i.token) + '"', "Отозвать") + "</span></span></div>";
      })
      .join("");
    return group("Ссылки-приглашения", "", "<div>" + '<div class="pf-list">' + list + addRow('data-org="invite"', "Создать ссылку-приглашение") + "</div>" +
      '<p class="pf-hint">Для тех, у кого почта не на домене, — подрядчиков, партнёров. Новая ссылка сразу копируется.</p></div>');
  }

  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    } catch (_) {
      return String(iso || "").slice(0, 10);
    }
  }

  // ---------- действия ----------

  function currentId() {
    return state.detail && state.detail.org && state.detail.org.id;
  }

  function applyDetail(d) {
    state.detail = d;
    return reload();
  }

  async function copy(url) {
    try {
      await navigator.clipboard.writeText(url);
    } catch (_) {
      var ta = document.createElement("textarea");
      ta.value = url;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (_) {}
      ta.remove();
      if (!ok) {
        toast("Скопируйте ссылку из списка ниже");
        return;
      }
    }
    toast("Ссылка скопирована");
  }

  async function confirm(msg) {
    return app().confirm ? app().confirm(msg) : window.confirm(msg);
  }

  function onClick(e) {
    var chip = e.target.closest("button[data-org-ai]");
    if (chip) return onAiClick(chip);
    var btn = e.target.closest("[data-org]");
    if (!btn || btn.tagName === "SELECT") return;
    var kind = btn.getAttribute("data-org");
    var id = currentId();
    if (kind === "member-toggle") {
      var mid = btn.getAttribute("data-mid");
      state.openMember = state.openMember === mid ? null : mid;
      render();
      return;
    }
    if (kind === "create") {
      return act(async function () {
        var name = (document.getElementById("org-new-name") || {}).value || "";
        var domEl = document.getElementById("org-new-domain");
        await api("", json("POST", { name: name.trim(), domain: domEl ? domEl.value : "" }));
        toast("Организация создана");
        await reload();
      });
    }
    if (kind === "mkt-build") {
      return act(async function () {
        setSaved("market", "busy", "Собираю выпуск…");
        try {
          await api("/" + id + "/market/build", { method: "POST" });
          setSaved("market", "ok", "Собираю — через пару минут выпуск будет в «Рынке»");
          setTimeout(function () {
            if (app().reloadMarket) app().reloadMarket();
          }, 120000);
        } catch (e) {
          setSaved("market", "err", errText(e));
        }
      });
    }
    if (kind === "show-create") {
      state.detail = null;
      render();
      return;
    }
    if (kind === "join") {
      return act(async function () {
        var r = await api("/" + btn.getAttribute("data-id") + "/join", { method: "POST" });
        toast(r.status === "active" ? "Вы в организации" : "Заявка отправлена админам");
        await reload();
      });
    }
    if (kind === "dismiss") {
      return act(async function () {
        await api("/" + btn.getAttribute("data-id") + "/dismiss", { method: "POST" });
        await reload();
      });
    }
    if (kind === "credits-topup") {
      return autosave("credits", function () {
        return api("/" + id + "/credits/topup", { method: "POST" });
      });
    }
    if (kind === "add") {
      var inp = document.getElementById("org-add-key");
      return addMember(inp && inp.value);
    }
    if (kind === "approve" || kind === "reject") {
      return act(async function () {
        var d = await api("/" + id + "/approve", json("POST", { user_id: Number(btn.getAttribute("data-uid")), allow: kind === "approve" }));
        await applyDetail(d);
      });
    }
    if (kind === "remove") {
      return act(async function () {
        if (!(await confirm("Исключить из организации?"))) return;
        await applyDetail(await api("/" + id + "/members/" + btn.getAttribute("data-mid"), { method: "DELETE" }));
      });
    }
    if (kind === "invite") {
      return act(async function () {
        var r = await api("/" + id + "/invites", { method: "POST" });
        var urls = r.invite.urls || {};
        await copy((inTelegram() && urls.telegram) || urls.web);
        await reload();
      });
    }
    if (kind === "copy") return copy(btn.getAttribute("data-url"));
    if (kind === "revoke") {
      return act(async function () {
        await applyDetail(await api("/" + id + "/invites/" + encodeURIComponent(btn.getAttribute("data-token")), { method: "DELETE" }));
      });
    }
    if (kind === "domain-add") {
      return act(async function () {
        await applyDetail(await api("/" + id + "/domains", json("POST", { domain: btn.getAttribute("data-domain") })));
      });
    }
    if (kind === "domain-del") {
      return act(async function () {
        if (!(await confirm("Коллеги с этого домена перестанут видеть организацию. Участники останутся."))) return;
        await applyDetail(await api("/" + id + "/domains/" + encodeURIComponent(btn.getAttribute("data-domain")), { method: "DELETE" }));
      });
    }
    if (kind === "leave") {
      return act(async function () {
        if (!(await confirm("Выйти из организации «" + state.detail.org.name + "»?"))) return;
        await api("/" + id + "/leave", { method: "POST" });
        await reload();
      });
    }
    if (kind === "delete") {
      return act(async function () {
        if (!(await confirm("Удалить организацию «" + state.detail.org.name + "» для всех участников? Это нельзя отменить."))) return;
        await api("/" + id, { method: "DELETE" });
        toast("Организация удалена");
        await reload();
      });
    }
  }

  function onChange(e) {
    if (onAiChange(e)) return;
    if (onFieldChange(e)) return;
    var el = e.target.closest("[data-org]");
    if (!el) return;
    var kind = el.getAttribute("data-org");
    var id = currentId();
    if (kind === "switch") {
      return act(async function () {
        await api("/" + el.value + "/current", { method: "POST" });
        await reload();
      });
    }
    if (kind === "member-plan") {
      var mid = el.getAttribute("data-mid");
      var plan = el.value;
      return autosave("members", function () {
        return api("/" + id + "/members/" + mid + "/plan", json("PATCH", { plan: plan }));
      });
    }
    if (kind === "add-contact") {
      return addMember(el.value);
    }
    if (kind === "join-mode") {
      var mode = el.value;
      return autosave("join", function () {
        return api("/" + id, json("PATCH", { join_mode: mode }));
      });
    }
    if (kind === "role") {
      var uid = el.getAttribute("data-uid");
      var role = el.value;
      if (role === "owner") {
        return confirm("Передать владение? Вы станете админом.").then(function (ok) {
          if (!ok) return reload();
          return autosave("members", function () {
            return api("/" + id + "/members/" + uid, json("PATCH", { role: role }));
          });
        });
      }
      return autosave("members", function () {
        return api("/" + id + "/members/" + uid, json("PATCH", { role: role }));
      });
    }
  }

  // Поля текста и чисел сохраняются, когда уходишь из поля или жмёшь Enter (событие change).
  function onFieldChange(e) {
    var t = e.target;
    var id = currentId();
    if (!id || !t.id) return false;
    if (t.id === "org-name") {
      var name = t.value.trim();
      if (name && name !== state.detail.org.name) {
        autosave("org", function () { return api("/" + id, json("PATCH", { name: name })); });
      }
      return true;
    }
    if (t.id === "org-slug") {
      var slug = t.value.trim().toLowerCase();
      if (slug && slug !== state.detail.org.slug) {
        autosave("org", function () { return api("/" + id, json("PATCH", { slug: slug })); });
      }
      return true;
    }
    if (t.id === "org-plan") {
      var plan = t.value;
      autosave("billing", function () { return api("/" + id + "/billing", json("PATCH", { plan: plan })); });
      return true;
    }
    var mk = { "org-mkt-about": "about", "org-mkt-competitors": "competitors", "org-mkt-topics": "topics",
      "org-mkt-sources": "sources", "org-mkt-time": "time" }[t.id];
    if (mk) {
      var patch = {};
      patch[mk] = mk === "sources" ? t.value.split(/\n+/).map(function (x) { return x.trim(); }).filter(Boolean) : t.value;
      saveMarket(patch);
      return true;
    }
    if (t.id === "org-mkt-telegram") {
      saveMarket({ telegram: !!t.checked });
      return true;
    }
    if (t.id === "org-seats") {
      var seats = Number(t.value || 0);
      if (seats !== Number(state.detail.org.seats || 0)) {
        autosave("billing", function () { return api("/" + id + "/billing", json("PATCH", { seats: seats })); });
      }
      return true;
    }
    return false;
  }

  function onInput(e) {
    if (e.target.id !== "org-slug") return;
    var val = e.target.value.trim().toLowerCase();
    clearTimeout(state.slugTimer);
    state.slugTimer = setTimeout(async function () {
      var hint = document.getElementById("org-slug-hint");
      if (!hint) return;
      try {
        var r = await api("/slug-check?slug=" + encodeURIComponent(val) + "&org_id=" + (currentId() || 0));
        hint.textContent = r.ok ? "Адрес свободен" : r.error;
        hint.classList.toggle("pf-status--err", !r.ok);
      } catch (_) {}
    }, 350);
  }

  // ---------- вход по ссылке-приглашению ----------

  function pendingJoinToken() {
    try {
      var q = new URLSearchParams(location.search || "");
      var t = (q.get("join") || "").trim();
      if (t) return t;
    } catch (_) {}
    var tg = window.Telegram && window.Telegram.WebApp;
    var start = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    var m = start && String(start).match(/^join_(.+)$/);
    return m ? m[1] : "";
  }

  function clearJoinParam() {
    try {
      var u = new URL(location.href);
      if (!u.searchParams.has("join")) return;
      u.searchParams.delete("join");
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    } catch (_) {}
  }

  async function handleJoin(token, attempt) {
    var info;
    try {
      info = await api("/invite/" + encodeURIComponent(token));
    } catch (e) {
      // приложение может ещё авторизоваться — пробуем несколько раз
      if (attempt < 5 && (!e || e.status === 401 || e.status === 0 || !e.status)) {
        setTimeout(function () { handleJoin(token, attempt + 1); }, 1500);
        return;
      }
      clearJoinParam();
      toast(errText(e));
      return;
    }
    clearJoinParam();
    if (info.already_member) {
      toast("Вы уже в «" + info.org.name + "»");
      return;
    }
    if (!(await confirm("Вас приглашают в организацию «" + info.org.name + "». Вступить?"))) return;
    try {
      await api("/invite/" + encodeURIComponent(token) + "/accept", { method: "POST" });
      toast("Вы в организации «" + info.org.name + "»");
      await open();
    } catch (e) {
      toast(errText(e));
    }
  }

  // ---------- подключение ----------

  function init() {
    if (!window.LeoApp) {
      setTimeout(init, 200);
      return;
    }
    var openBtn = document.getElementById("profile-org-open");
    if (openBtn) openBtn.addEventListener("click", open);
    var back = document.getElementById("profile-org-back");
    if (back) back.addEventListener("click", function () { app().profileScreen("main"); });
    var el = root();
    if (el) {
      el.addEventListener("click", onClick);
      el.addEventListener("change", onChange);
      el.addEventListener("input", onInput);
      el.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        if (e.target.id === "org-add-key") {
          e.preventDefault();
          addMember(e.target.value);
        } else if (e.target.tagName === "INPUT") {
          e.preventDefault();
          e.target.blur(); // blur → change → сохранение
        }
      });
    }
    var token = pendingJoinToken();
    if (token) setTimeout(function () { handleJoin(token, 0); }, 800);
    // подпись в профиле: «<организация>» или «Обучат уже в Орбите — вступить»
    setTimeout(function () {
      api("").then(function (ov) {
        state.overview = ov;
        if (app().setMarketAvailable) app().setMarketAvailable(!!ov.current_id);
        var cur = (ov.orgs || []).filter(function (o) { return o.id === ov.current_id; })[0];
        state.detail = cur ? { org: cur } : null;
        syncHint();
        state.detail = null;
      }).catch(function () {});
    }, 2500);
  }

  // Для онбординга (экран «С кем вы работаете?»): те же вызовы, что и в Профиле → «Организация».
  window.LeoOrg = {
    open: open,
    reload: reload,
    overview: function () {
      return api("");
    },
    detail: function (id) {
      return api("/" + id);
    },
    create: function (name, domain) {
      return api("", json("POST", { name: String(name || "").trim(), domain: domain || "" }));
    },
    addMember: function (id, key) {
      return api("/" + id + "/members", json("POST", { key: String(key || "").trim() }));
    },
    createInvite: async function (id) {
      var r = await api("/" + id + "/invites", { method: "POST" });
      var urls = (r && r.invite && r.invite.urls) || {};
      return (inTelegram() && urls.telegram) || urls.web || "";
    },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
