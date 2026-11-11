/* Профиль → «Организация»: создать организацию, участники и роли, домен почты, ссылки-приглашения.
 * Вход по ссылке: /webapp/?join=<token> или startapp=join_<token> в миниаппе. */
(function () {
  "use strict";

  var ROLE_LABEL = { owner: "Владелец", admin: "Админ", member: "Участник" };
  var state = { overview: null, detail: null, market: null, contacts: null, queue: Promise.resolve(), saved: {}, slugTimer: null, view: null, memberFilter: "all", memberQuery: "" };

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
    state.view = null;
    state.returnTo = null;
    var el = root();
    if (el && !el.innerHTML) el.innerHTML = '<p class="pf-hint">Загружаю…</p>';
    await reload();
  }

  // Профиль → «Дайджест» → «Обзор рынка»: сразу экран выпуска; «Назад» — туда, откуда пришли.
  // Организации нет — экран создания (render сам покажет его вместо выпуска).
  async function openMarket(returnTo) {
    if (app().profileScreen) app().profileScreen("org");
    state.view = { name: "market" };
    state.returnTo = returnTo || null;
    var el = root();
    if (el) el.innerHTML = '<p class="pf-hint">Загружаю…</p>';
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
    syncPeopleRow();
  }

  // Профиль → «Показатели сотрудников»: только владельцу, админам и руководителям отделов текущей организации.
  function syncPeopleRow() {
    var row = document.getElementById("profile-people-open");
    var pm = state.overview && state.overview.people_metrics;
    if (!row) return;
    row.classList.toggle("hidden", !(pm && pm.show));
    var hint = document.getElementById("profile-people-hint");
    if (hint && pm) {
      hint.textContent = pm.enabled ? "Сроки, качество, нагрузка — по отделу" : pm.can_toggle ? "Выключены · включить" : "Выключены в организации";
    }
  }

  async function openPeopleFromProfile() {
    try {
      state.overview = await api("");
    } catch (e) {
      toast(errText(e));
      return;
    }
    syncPeopleRow();
    var pm = state.overview.people_metrics;
    if (!pm || !pm.show || !window.LeoPeople) return;
    var cur = (state.overview.orgs || []).filter(function (o) { return o.id === state.overview.current_id; })[0];
    window.LeoPeople.openEntry(Object.assign({ org_name: cur ? cur.name : "" }, pm));
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
    users: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5" /><path d="M2.5 19a6.5 6.5 0 0 1 13 0" /><circle cx="17" cy="9" r="2.5" /><path d="M17 14.5a4.5 4.5 0 0 1 4.5 4.5" /></svg>',
    userPlus: '<svg viewBox="0 0 24 24"><circle cx="10" cy="8" r="3.5" /><path d="M3.5 19a6.5 6.5 0 0 1 13 0" /><path d="M19 8v6M16 11h6" /></svg>',
    tree: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="5" rx="1.5" /><rect x="3" y="16" width="6" height="5" rx="1.5" /><rect x="15" y="16" width="6" height="5" rx="1.5" /><path d="M12 8v4M6 16v-2h12v2" /></svg>',
    spark: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg>',
    card: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="13" rx="2.5" /><path d="M3 10h18M7 15h3" /></svg>',
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

  // ---------- экраны ----------
  // Главная организации — оглавление; каждый раздел открывается своим экраном в той же колонке.
  // «Назад» в шапке (и системная кнопка Telegram) поднимает на уровень выше, с главной — в Профиль.

  var VIEW_PARENT = {
    members: "home", member: "members", invite: "members", join: "home", structure: "home", dept: "structure",
    billing: "home", about: "home", people: "home", market: "home", create: "home",
  };
  var VIEW_TITLE = {
    home: "Организация", members: "Участники", member: "Участник", invite: "Пригласить", join: "Как вступают",
    structure: "Отделы и команды", dept: "Отдел", billing: "ИИ и оплата", about: "Название и адрес",
    people: "Показатели сотрудников", market: "Выпуск «Рынок»", create: "Новая организация",
  };

  function view() {
    return state.view || { name: "home" };
  }

  function go(name, id) {
    if (name !== "market") state.returnTo = null;
    state.view = { name: name, id: id == null ? null : String(id) };
    render();
    var body = root() && root().closest(".subscreen-body");
    if (body) body.scrollTop = 0;
  }

  function goBack() {
    var v = view();
    if (state.returnTo && (v.name === "market" || !state.detail)) {
      var to = state.returnTo;
      state.returnTo = null;
      state.view = null;
      app().profileScreen(to);
      return;
    }
    if (v.name === "home" || !state.detail && v.name !== "create") {
      state.view = null;
      app().profileScreen("main");
      return;
    }
    // Карточку участника открывают и из отдела — туда и возвращаемся.
    var parent = v.from || VIEW_PARENT[v.name] || "home";
    go(parent.name || parent, parent.id);
  }

  function findMember(d, id) {
    return (d.members || []).filter(function (m) { return String(m.id) === String(id); })[0] || null;
  }
  function findGroup(d, id) {
    return (d.groups || []).filter(function (g) { return String(g.id) === String(id); })[0] || null;
  }

  function render() {
    var el = root();
    if (!el) return;
    var ov = state.overview || {};
    var d = state.detail;
    var v = view();
    // Участника исключили, отдел удалили, организацию сменили — показываем экран уровнем выше.
    if (v.name === "member" && !(d && findMember(d, v.id))) v = state.view = { name: "members" };
    if (v.name === "dept" && !(d && findGroup(d, v.id))) v = state.view = { name: "structure" };
    if (!d && v.name !== "create") v = state.view = { name: "home" };
    var html;
    if (v.name === "create" || !d) html = (d ? "" : suggestionsHtml(ov)) + createHtml(ov);
    else if (v.name === "members") html = membersHtml(d);
    else if (v.name === "member") html = memberCardHtml(d, findMember(d, v.id));
    else if (v.name === "invite") html = inviteHtml(d);
    else if (v.name === "join") html = (joinHtml(d) + invitesHtml(d)) || '<p class="pf-hint">Здесь пока нечего настроить.</p>';
    else if (v.name === "structure") html = groupsHtml(d);
    else if (v.name === "dept") html = deptHtml(d, findGroup(d, v.id));
    else if (v.name === "billing") html = creditsHtml(d) + billingHtml(d);
    else if (v.name === "about") html = aboutHtml(d);
    else if (v.name === "people") html = peopleMetricsHtml(d);
    else if (v.name === "market") html = marketHtml(state.market);
    else html = homeHtml(ov, d);
    el.className = "pf-screen-body org-screen org-view-" + (d ? v.name : "create");
    el.innerHTML = html;
    var title = document.querySelector("#profile-org .detail-title");
    if (title) {
      var g = v.name === "dept" && d ? findGroup(d, v.id) : null;
      title.textContent = g ? g.name : d ? VIEW_TITLE[v.name] || "Организация" : "Организация";
    }
  }

  // Строка-переход в раздел: иконка, название, подпись, значение справа и «›».
  function navRow(name, id, title, sub, val, icon) {
    return '<button type="button" class="pf-row" data-org="nav" data-view="' + name + '"' + (id != null ? ' data-id="' + esc(id) + '"' : "") + ">" +
      (icon ? '<span class="pf-ico" aria-hidden="true">' + icon + "</span>" : "") +
      '<span class="pf-row-body">' + textHtml(title, sub) + (val ? '<span class="pf-row-val">' + esc(val) + "</span>" : "") +
      '<span class="pf-chev" aria-hidden="true">›</span></span></button>';
  }

  function orgPic(org) {
    return org.logo_url ? '<img src="' + esc(org.logo_url) + '" alt="" />' : esc(initials(org.name));
  }

  // Шапка: логотип, название, роль и число участников; при нескольких организациях — переключатель.
  function headHtml(ov, org) {
    var sw = "";
    if ((ov.orgs || []).length > 1) {
      // Видна подпись «Сменить организацию», а сам выбор — прозрачный select поверх неё.
      sw = '<label class="org-switch">Сменить организацию<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>' +
        selectHtml(ov.orgs.map(function (o) { return [o.id, o.name]; }), ov.current_id, 'data-org="switch" aria-label="Сменить организацию"') + "</label>";
    }
    return '<div class="org-head"><span class="pf-hero-ico org-hero-ico" aria-hidden="true">' + orgPic(org) + "</span>" +
      '<div class="org-head-text"><p class="org-head-title">' + esc(org.name) + "</p>" +
      '<p class="org-head-sub">' + esc(ROLE_LABEL[org.my_role] || "") + " · " + org.member_count + " " +
      plural(org.member_count, "участник", "участника", "участников") + "</p></div>" + sw + "</div>";
  }

  function pendingMembers(d) {
    return (d.members || []).filter(function (m) { return m.status === "pending"; });
  }

  function homeHtml(ov, d) {
    var org = d.org;
    var can = d.can || {};
    var members = (d.members || []).filter(function (m) { return m.status !== "pending"; });
    var invited = members.filter(function (m) { return m.status === "invited"; }).length;
    var top = headHtml(ov, org);
    var pend = can.approve ? pendingMembers(d) : [];
    if (pend.length) {
      var names = pend.slice(0, 2).map(function (m) { return memberName(d, m); }).join(", ") + (pend.length > 2 ? " и ещё " + (pend.length - 2) : "");
      top += '<button type="button" class="org-banner" data-org="nav" data-view="members">' +
        '<span class="pf-ico" aria-hidden="true">' + ICO.userPlus + "</span>" +
        '<span class="pf-row-text"><span class="pf-row-title">' + pend.length + " " + plural(pend.length, "заявка", "заявки", "заявок") + " на вступление</span>" +
        '<span class="pf-row-sub">' + esc(names) + '</span></span><span class="org-banner-go">Разобрать</span></button>';
    }
    var html = '<div class="org-home-top">' + top + "</div>" + suggestionsHtml(ov);

    var team = navRow("members", null, "Участники",
      members.length + " " + plural(members.length, "человек", "человека", "человек") + (invited ? " · " + invited + " " + plural(invited, "приглашён", "приглашены", "приглашены") : ""),
      "", ICO.users);
    var groups = d.groups || [];
    if (groups.length || can.invite) {
      var teams = groups.reduce(function (n, g) { return n + g.teams.length; }, 0);
      team += navRow("structure", null, "Отделы и команды",
        groups.length ? groups.length + " " + plural(groups.length, "отдел", "отдела", "отделов") + (teams ? " · " + teams + " " + plural(teams, "команда", "команды", "команд") : "") : "Пока нет — кому открывать базу знаний и сейфы",
        "", ICO.tree);
    }
    if (can.invite) {
      var doms = org.domains || [];
      var links = (d.invites || []).length;
      var joinSub = [doms.length ? "@" + doms[0] + (org.join_mode === "auto" ? " сразу" : " по заявке") : "",
        links ? links + " " + plural(links, "ссылка", "ссылки", "ссылок") : ""].filter(Boolean).join(" · ") || "Домен почты и ссылки-приглашения";
      team += navRow("join", null, "Как вступают", joinSub, "", ICO.link);
    }
    var cols = group("Команда", "", '<div class="pf-list">' + team + "</div>");

    var money = "";
    var c = d.credits;
    if (c) {
      var u = c.can_manage && c.usage;
      var msub = u ? "В " + (MONTHS_IN[Number(String(u.month_from).slice(5, 7)) - 1] || "этом месяце") + " потрачено " + fmtNum(u.spent_month) + " кр" : "Счёт организации";
      money += navRow("billing", null, "Кредиты ИИ", msub, fmtNum(c.balance) + " кр", ICO.spark);
    }
    money += navRow("billing", null, "Тариф", "Занято " + (org.seats_used || 0) + " из " + (org.seats || 0) + " мест", planLabel(d, org.plan), ICO.card);
    cols += group("ИИ и оплата", "", '<div class="pf-list">' + money + "</div>");

    var set = "";
    if (can.edit_org) set += navRow("about", null, "Название и адрес", "", org.slug || "");
    var pm = d.people_metrics;
    if (pm && (pm.can_toggle || pm.can_view)) set += navRow("people", null, "Показатели сотрудников", "", pm.enabled ? "Включены" : "Выключены");
    var m = state.market;
    if (m && m.settings) set += navRow("market", null, "Выпуск «Рынок»", "", m.settings.time ? "в " + m.settings.time : "");
    if (set) cols += group("Настройки", "", '<div class="pf-list">' + set + "</div>");
    html += '<div class="org-home-grid">' + cols + "</div>";

    var danger = '<button type="button" class="pf-row pf-row--danger" data-org="leave"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Выйти из организации</span></span></span></button>';
    if (can.delete_org) {
      danger += '<button type="button" class="pf-row pf-row--danger" data-org="delete"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Удалить организацию</span></span></span></button>';
    }
    html += '<div class="pf-list org-home-wide">' + danger + "</div>";
    html += '<button type="button" class="pf-btn pf-btn--ghost org-home-wide" data-org="nav" data-view="create">Создать ещё одну организацию</button>';
    return html;
  }

  function aboutHtml(d) {
    var org = d.org;
    return group("Название и адрес", "org", '<div><div class="pf-list">' +
      '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-name" maxlength="80" value="' + esc(org.name) + '" /></label>' +
      '<label class="pf-field"><span class="pf-field-label">Короткий адрес</span><input type="text" class="pf-input" id="org-slug" maxlength="40" value="' + esc(org.slug) + '" autocapitalize="off" spellcheck="false" /></label>' +
      '</div><p class="pf-hint" id="org-slug-hint">Латиница, цифры и дефис. Пригодится для публичной страницы выпуска и поддомена.</p></div>');
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
      '<p class="pf-empty-text">Общее пространство команды: участники, роли и общие настройки.</p></div>' +
      "<div>" +
      '<div class="pf-list">' +
      '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-new-name" maxlength="80" placeholder="Например, Обучат" /></label>' +
      domSel + "</div>" +
      '<p class="pf-hint">' + esc(domHint) + "</p></div>" +
      '<button type="button" class="pf-btn" data-org="create">Создать организацию</button>'
    );
  }

  // «Показатели сотрудников»: включает владелец или админ; раздел «Люди» — руководителям отделов и админам.
  function peopleMetricsHtml(d) {
    var pm = d.people_metrics;
    if (!pm || (!pm.can_toggle && !pm.can_view)) return "";
    var list = "";
    if (pm.can_view) {
      list += '<button type="button" class="pf-row" data-org="people-open"><span class="pf-row-body">' +
        textHtml("Люди", "Сроки и качество поручений — по отделу") + '<span class="pf-chev" aria-hidden="true">›</span></span></button>';
    }
    if (pm.can_toggle) {
      list += staticRow("Показатели сотрудников", pm.enabled ? "Включены" : "Выключены",
        '<input type="checkbox" class="pf-switch" id="org-people-metrics"' + (pm.enabled ? " checked" : "") + ' aria-label="Показатели сотрудников" />', "label");
    }
    var hint = pm.enabled
      ? "Руководители отделов видят показатели своих людей, владелец и админы — всех. Каждый видит свои: Профиль → «Мои показатели»."
      : "Сроки и качество поручений, реакция на них. Руководитель отдела увидит своих людей, каждый — себя в тех же цифрах. При включении всем участникам придёт сообщение, что и кому видно.";
    return group("Показатели сотрудников", "people", '<div><div class="pf-list">' + list + '</div><p class="pf-hint">' + esc(hint) + "</p></div>");
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
      (can.billing ? '<p class="pf-hint">Тариф каждого человека — в его карточке: Участники → человек.</p>' : "") + "</div>";
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
        " кр). Сверх лимита Лео продолжает работать, кредиты списываются со счёта организации. Лимит и модели — в карточке участника.</p>"
      : "";
    return group("Кредиты ИИ", "credits", "<div>" + wallet + usageHtml(d) + hint + "</div>");
  }

  var MONTHS_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];

  // Расход сотрудников: итог за месяц и список «кто сколько потратил» полосками (видит руководитель).
  function usageHtml(d) {
    var c = d.credits;
    var u = c && c.can_manage && c.usage;
    if (!u) return "";
    var month = MONTHS_IN[Number(String(u.month_from).slice(5, 7)) - 1] || "этом месяце";
    var names = {};
    (d.members || []).forEach(function (m) { if (m.user_id) names[String(m.user_id)] = memberName(d, m); });
    var rows = Object.keys(c.members || {}).map(function (uid) {
      var a = c.members[uid];
      return { uid: uid, name: names[uid] || uid, month: Number(a.spent_month) || 0, today: Number(a.spent_today) || 0, over: Number(a.over_month) || 0 };
    }).filter(function (r) { return r.month > 0 || r.today > 0; });
    rows.sort(function (a, b) { return b.month - a.month; });
    var max = rows.length ? Math.max(rows[0].month, 1) : 1;
    var head = staticRow("Потрачено в " + month, "≈ " + fmtNum(u.spent_month_rub) + " ₽ · сегодня " + fmtNum(u.spent_today) + " кр" +
        (u.over_month > 0 ? " · со счёта организации " + fmtNum(u.over_month) + " кр" : ""),
      '<span class="pf-row-val">' + fmtNum(u.spent_month) + " кр</span>");
    var list = rows.length
      ? rows.map(function (r) {
          var sub = "сегодня " + fmtNum(r.today) + " кр" + (r.over > 0 ? " · сверх лимита за месяц " + fmtNum(r.over) : "");
          return '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text">' +
            '<span class="pf-row-title">' + esc(r.name) + '</span><span class="pf-row-sub">' + esc(sub) + "</span>" +
            '<span class="org-bar" aria-hidden="true"><span style="width:' + Math.max(2, Math.round(r.month / max * 100)) + '%"></span></span>' +
            '</span><span class="pf-row-val">' + fmtNum(r.month) + " кр</span></span></div>";
        }).join("")
      : '<p class="pf-hint">В этом месяце сотрудники ещё не тратили кредиты.</p>';
    return '<div class="pf-list org-usage">' + head + list + "</div>";
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
    var chips = '<button type="button" class="pf-chip' + (all ? " is-on" : "") + '" data-org-ai="all" data-uid="' + uid + '" aria-pressed="' + all + '">Все модели</button>' +
      models.map(function (x) {
        var on = all || allowed.indexOf(x.id) >= 0;
        return '<button type="button" class="pf-chip' + (on ? " is-on" : "") + '" data-org-ai="model" data-uid="' + uid + '" data-model="' + esc(x.id) +
          '" aria-pressed="' + on + '">' + esc(x.name) + "</button>";
      }).join("");
    return staticRow("Дневной лимит, кр", "",
        '<input type="number" class="pf-num-input org-num" data-org-ai="limit" data-uid="' + uid + '" min="0" max="1000000" step="50" inputmode="numeric" value="' +
        Math.round(ai.daily_limit) + '" aria-label="Дневной лимит, кредитов" />', "label") +
      '<div class="pf-field"><span class="pf-field-label">Доступные модели</span><div class="pf-chips org-chips">' + chips + "</div></div>" +
      memberEffortHtml(d, uid, ai);
  }

  // Потолок уровня усилия (сколько модель думает): выше — дороже. Пусто — без ограничения.
  function memberEffortHtml(d, uid, ai) {
    var levels = d.credits.efforts || [];
    if (!levels.length) return "";
    var opts = [["", "Без ограничения"]].concat(levels.map(function (e) { return [e.id, "не выше «" + e.label + "»"]; }));
    return staticRow("Уровень усилия ИИ", "Выше — точнее на трудных задачах, но дольше и дороже",
      selectHtml(opts, ai.max_effort || "", 'data-org-ai="effort" data-uid="' + uid + '" aria-label="Потолок уровня усилия"'), "label");
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
    var eff = e.target.closest('[data-org-ai="effort"]');
    if (eff) {
      saveMemberAi(eff.getAttribute("data-uid"), eff.value ? { max_effort: eff.value } : { any_effort: true });
      return true;
    }
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

  // В каких отделах и командах человек: [{g, team, lead}] — команда внутри отдела или сам отдел.
  function memberGroups(d, uid) {
    var out = [];
    (d.groups || []).forEach(function (g) {
      if (g.member_ids.indexOf(uid) >= 0 || g.lead_user_id === uid) out.push({ g: g, team: null, lead: g.lead_user_id === uid });
      g.teams.forEach(function (t) {
        if (t.member_ids.indexOf(uid) >= 0) out.push({ g: g, team: t, lead: false });
      });
    });
    return out;
  }

  function groupLabel(x) {
    return x.team ? x.g.name + " · " + x.team.name : x.g.name;
  }

  // Короткая подпись в списке: роль (кроме обычного участника) и отдел; почта — если отделов нет.
  function memberLine(d, m) {
    if (m.status === "pending") return m.email || "Просится в организацию";
    if (m.status === "invited") return "Приглашён" + (m.tg ? " · @" + m.tg : "") + " — ждём входа";
    var parts = [];
    if (m.role !== "member") parts.push(ROLE_LABEL[m.role]);
    var gs = m.user_id ? memberGroups(d, m.user_id) : [];
    if (gs.length) parts.push(groupLabel(gs[0]) + (gs.length > 1 ? " +" + (gs.length - 1) : ""));
    else parts.push(m.email && m.email !== m.name ? m.email : m.username || m.tg ? "@" + (m.username || m.tg) : ROLE_LABEL[m.role]);
    return parts.filter(Boolean).join(" · ");
  }

  function avatarHtml(d, m, big) {
    var name = memberName(d, m).replace(/ \(вы\)$/, "");
    var cls = "pf-avatar" + (big ? " org-avatar-big" : "") + (m.status === "invited" ? " org-avatar--invited" : "") + (m.user_id === d.my_user_id ? " org-avatar--me" : "");
    return '<span class="' + cls + '" aria-hidden="true">' + esc(initials(name)) + "</span>";
  }

  function tag(text, kind) {
    return '<span class="org-tag org-tag--' + kind + '">' + esc(text) + "</span>";
  }

  // Участник открывается своей карточкой, если руководителю там есть что менять.
  function memberCanEdit(d, m) {
    var can = d.can || {};
    var me = d.org.my_role;
    var ai = memberAi(d, m);
    return {
      role: m.status === "active" && can.set_role && m.user_id !== d.my_user_id,
      plan: !!can.billing,
      remove: can.remove_member && m.role !== "owner" && (m.role !== "admin" || me === "owner"),
      ai: !!(ai && d.credits.can_manage),
      guest: can.invite && m.status === "active" && m.role === "member",
      groups: !!(m.user_id && m.status === "active" && (d.groups || []).some(function (g) { return g.can_manage; })),
    };
  }

  function memberRowHtml(d, m) {
    var ai = memberAi(d, m);
    var right = "";
    if (m.guest) right += tag("гость", "guest");
    if (ai && ai.over_today > 0) right += tag("лимит", "warn");
    if (m.status === "invited") right += tag("ждём", "muted");
    var body = '<span class="pf-row-body">' + textHtml(memberName(d, m), memberLine(d, m)) + right;
    return '<button type="button" class="pf-row" data-org="nav" data-view="member" data-id="' + m.id + '">' + avatarHtml(d, m) + body +
      '<span class="pf-chev" aria-hidden="true">›</span></span></button>';
  }

  var MEMBER_FILTERS = [
    ["all", "Все", function () { return true; }],
    ["admins", "Админы", function (m) { return m.status === "active" && m.role !== "member"; }],
    ["invited", "Приглашены", function (m) { return m.status === "invited"; }],
    ["guests", "Гости", function (m) { return !!m.guest; }],
  ];

  function memberMatches(d, m, q) {
    if (!q) return true;
    var hay = [memberName(d, m), m.email, m.username, m.tg, m.invited_key].join(" ").toLowerCase();
    return hay.indexOf(q) >= 0;
  }

  function membersListHtml(d) {
    var f = MEMBER_FILTERS.filter(function (x) { return x[0] === state.memberFilter; })[0] || MEMBER_FILTERS[0];
    var q = String(state.memberQuery || "").trim().toLowerCase();
    var list = (d.members || []).filter(function (m) { return m.status !== "pending" && f[2](m) && memberMatches(d, m, q); });
    var rows = list.map(function (m) { return memberRowHtml(d, m); }).join("");
    return group((f[0] === "all" ? "В организации" : f[1]) + " · " + list.length, "members",
      rows ? '<div class="pf-list">' + rows + "</div>" : '<p class="pf-hint">Никого не нашлось.</p>');
  }

  function membersHtml(d) {
    var can = d.can || {};
    var active = (d.members || []).filter(function (m) { return m.status !== "pending"; });
    var html = '<label class="pf-search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4-4" /></svg>' +
      '<input type="search" id="org-member-q" placeholder="Имя, почта или ник" aria-label="Найти участника" value="' + esc(state.memberQuery || "") + '" /></label>';
    var chips = MEMBER_FILTERS.map(function (x) {
      var n = active.filter(x[2]).length;
      if (x[0] !== "all" && !n) return "";
      var on = (state.memberFilter || "all") === x[0];
      return '<button type="button" class="pf-chip org-filter' + (on ? " is-on" : "") + '" data-org="member-filter" data-filter="' + x[0] + '" aria-pressed="' + on + '">' + esc(x[1]) + " " + n + "</button>";
    }).join("");
    html += '<div class="pf-chips org-filters">' + chips + "</div>";
    var pend = pendingMembers(d);
    if (pend.length) {
      var prow = pend.map(function (m) {
        var acts = can.approve
          ? '<span class="org-actions"><button type="button" class="pf-link pf-link--muted" data-org="reject" data-uid="' + esc(m.user_id) + '">Отклонить</button>' +
            '<button type="button" class="pf-link" data-org="approve" data-uid="' + esc(m.user_id) + '">Принять</button></span>'
          : "";
        return '<div class="pf-row pf-row--static">' + avatarHtml(d, m) + '<span class="pf-row-body">' + textHtml(memberName(d, m), memberLine(d, m)) + acts + "</span></div>";
      }).join("");
      html += group("Просятся в организацию · " + pend.length, "", '<div class="pf-list">' + prow + "</div>");
    }
    html += '<div id="org-members-list">' + membersListHtml(d) + "</div>";
    if (can.invite) html += '<button type="button" class="pf-btn" data-org="nav" data-view="invite">Пригласить</button>';
    if (can.set_role) {
      html += '<p class="pf-hint">Админ приглашает и исключает участников и настраивает выпуск «Рынок». Владелец ещё меняет название и адрес, назначает админов. Назначить другого владельцем — передать владение.</p>';
    }
    return html;
  }

  // Карточка участника: доступ, тариф, ИИ, отделы, исключить.
  function memberCardHtml(d, m) {
    var ai = memberAi(d, m);
    var e = memberCanEdit(d, m);
    var contacts = [];
    if (m.email && m.email !== m.name) contacts.push(m.email);
    if (m.username || m.tg) contacts.push("@" + (m.username || m.tg));
    var tags = tag(m.status === "invited" ? "Приглашён" : m.status === "pending" ? "Заявка" : ROLE_LABEL[m.role], "role");
    if (m.guest) tags += tag("Гость", "guest");
    var gs = m.user_id ? memberGroups(d, m.user_id) : [];
    gs.forEach(function (x) { tags += tag(groupLabel(x) + (x.lead ? " · руководитель" : ""), "group"); });
    var html = '<div class="org-person">' + avatarHtml(d, m, true) + '<div class="org-person-text">' +
      '<p class="org-head-title">' + esc(memberName(d, m)) + "</p>" +
      (contacts.length ? '<p class="org-head-sub">' + esc(contacts.join(" · ")) + "</p>" : "") +
      '<p class="org-tags">' + tags + "</p></div></div>";
    if (m.status === "pending") return html + '<p class="pf-hint">Человек просится в организацию — принять или отклонить можно в списке участников.</p>';

    var acc = "";
    if (e.role) {
      acc += staticRow("Роль", "", selectHtml(["owner", "admin", "member"].map(function (r) { return [r, ROLE_LABEL[r]]; }), m.role,
        'data-org="role" data-uid="' + esc(m.user_id) + '" aria-label="Роль"'), "label");
    } else {
      acc += staticRow("Роль", "", '<span class="pf-row-val">' + esc(ROLE_LABEL[m.role] || "") + "</span>");
    }
    if (e.guest) {
      acc += '<label class="pf-row pf-row--static"><span class="pf-row-body">' +
        textHtml("Гость", "Подрядчик или клиент: не видит открытое «Всем», только своё и своих отделов") +
        '<input type="checkbox" class="pf-switch" data-org="guest" data-mid="' + m.id + '"' + (m.guest ? " checked" : "") + ' aria-label="Гость" /></span></label>';
    }
    if (e.plan) {
      acc += staticRow("Тариф", "", planSelect(d, m.plan, 'data-org="member-plan" data-mid="' + m.id + '" aria-label="Тариф участника"'), "label");
    } else {
      acc += staticRow("Тариф", "", '<span class="pf-row-val">' + esc(planLabel(d, m.plan)) + "</span>");
    }
    html += group("Доступ", "members", '<div class="pf-list">' + acc + "</div>");

    if (ai) {
      var limit = Math.max(Number(ai.daily_limit) || 0, 1);
      var spent = Number(ai.spent_today) || 0;
      var within = Math.min(spent, limit);
      var bar = '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text">' +
        '<span class="pf-row-title">Сегодня ' + fmtNum(spent) + " из " + fmtNum(ai.daily_limit) + " кр</span>" +
        '<span class="org-bar org-bar--wide" aria-hidden="true"><span style="width:' + Math.round(within / Math.max(spent, limit) * 100) + '%"></span>' +
        (spent > limit ? '<span class="org-bar-over" style="width:' + Math.round((spent - limit) / spent * 100) + '%"></span>' : "") + "</span>" +
        (ai.over_today > 0 ? '<span class="pf-row-sub org-warn">' + fmtNum(ai.over_today) + " кр сверх лимита — со счёта организации</span>" : "") +
        "</span></span></div>";
      var aiList = bar;
      if (e.ai) aiList += memberAiHtml(d, m, ai);
      else aiList += staticRow("Дневной лимит", "", '<span class="pf-row-val">' + fmtNum(ai.daily_limit) + " кр</span>");
      if (ai.spent_month != null) aiList += staticRow("За месяц", "", '<span class="pf-row-val">' + fmtNum(ai.spent_month) + " кр</span>");
      html += group("ИИ", "", '<div class="pf-list org-ai">' + aiList + "</div>");
    }

    if (m.user_id && m.status === "active" && ((d.groups || []).length)) {
      var rows = gs.map(function (x) {
        return navRow("dept", x.g.id, groupLabel(x), x.lead ? "Руководитель отдела" : x.team ? "Участник команды" : "В отделе напрямую", "");
      }).join("");
      if (e.groups) {
        var opts = [];
        (d.groups || []).forEach(function (g) {
          if (!g.can_manage) return;
          if (g.member_ids.indexOf(m.user_id) < 0) opts.push([g.id, g.name]);
          g.teams.forEach(function (t) { if (t.member_ids.indexOf(m.user_id) < 0) opts.push([t.id, g.name + " · " + t.name]); });
        });
        if (opts.length) {
          rows += '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
            '<span class="pf-row-body"><select class="pf-input" data-org="member-group-add" data-uid="' + esc(m.user_id) + '" aria-label="Добавить в отдел или команду">' +
            '<option value="">Добавить в отдел или команду…</option>' +
            opts.map(function (o) { return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + "</option>"; }).join("") + "</select></span></div>";
        }
      }
      if (rows) html += group("Отделы и команды", "groups", '<div class="pf-list">' + rows + "</div>");
    }

    if (e.remove) {
      html += '<div class="pf-list"><button type="button" class="pf-row pf-row--danger" data-org="remove" data-mid="' + m.id + '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
        (m.status === "invited" ? "Отозвать приглашение" : "Исключить из организации") + "</span></span></span></button></div>";
    }
    return html;
  }

  // Пригласить: почта и ник, из контактов; ссылки и домен — отдельным разделом «Как вступают».
  function inviteHtml(d) {
    var can = d.can || {};
    if (!can.invite) return '<p class="pf-hint">Приглашать участников могут владелец и админы.</p>';
    var form = '<label class="pf-field"><span class="pf-field-label">Рабочая почта</span><input type="email" class="pf-input" id="org-add-key" placeholder="name@company.ru" autocapitalize="off" spellcheck="false" /></label>' +
      '<label class="pf-field"><span class="pf-field-label">Ник в Telegram — по желанию</span><input type="text" class="pf-input" id="org-add-tg" placeholder="@ник" autocapitalize="off" spellcheck="false" /></label>';
    var html = group("По почте", "members", "<div>" + '<div class="pf-list">' + form + "</div>" +
      '<p class="pf-hint">Пришлём письмо со входом. С ником бот узнает человека, как только тот нажмёт Start.</p></div>');
    html += '<button type="button" class="pf-btn" data-org="add">Отправить приглашение</button>';
    var opts = contactOptions(d);
    if (opts) {
      html += group("Из контактов", "", '<div class="pf-list"><div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
        '<span class="pf-row-body"><select class="pf-input" data-org="add-contact" aria-label="Добавить из контактов">' +
        '<option value="">Выбрать контакт…</option>' + opts + "</select></span></div></div>");
    }
    html += group("Другие способы", "", '<div class="pf-list">' + navRow("join", null, "Ссылка-приглашение и домен почты", "Для подрядчиков и коллег с рабочей почтой", "", ICO.link) + "</div>");
    return html;
  }


  // ---------- отделы и команды ----------

  function personName(d, uid) {
    var m = (d.members || []).filter(function (x) { return x.user_id === uid; })[0];
    return m ? memberName(d, m) : "Бывший участник";
  }

  // Выбор участника организации, которого ещё нет в списке ids.
  function personSelect(d, ids, attrs, placeholder) {
    var opts = (d.members || []).filter(function (m) {
      return m.status === "active" && ids.indexOf(m.user_id) < 0;
    });
    if (!opts.length) return "";
    return '<select class="pf-input" ' + attrs + '><option value="">' + esc(placeholder) + "</option>" +
      opts.map(function (m) { return '<option value="' + esc(m.user_id) + '">' + esc(memberName(d, m)) + "</option>"; }).join("") +
      "</select>";
  }

  function addPersonRow(d, g, label) {
    var sel = personSelect(d, g.member_ids, 'data-org="group-member-add" data-gid="' + g.id + '" aria-label="' + esc(label) + '"', label + "…");
    return sel ? '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
      '<span class="pf-row-body">' + sel + "</span></div>" : "";
  }

  function deptPeople(g) {
    var people = g.member_ids.slice();
    g.teams.forEach(function (t) { t.member_ids.forEach(function (u) { if (people.indexOf(u) < 0) people.push(u); }); });
    return people;
  }

  function leadName(d, g) {
    return g.lead_user_id ? personName(d, g.lead_user_id).replace(/ \(вы\)$/, "") : "";
  }

  // Отдел — карточкой: шапка ведёт в отдел, под ней команды веточкой.
  function deptCardHtml(d, g) {
    var n = deptPeople(g).length;
    var sub = [leadName(d, g) || "Руководитель не назначен", n + " " + plural(n, "человек", "человека", "человек")].join(" · ");
    var html = '<button type="button" class="pf-row org-dept-head" data-org="nav" data-view="dept" data-id="' + g.id + '">' +
      '<span class="org-tile" aria-hidden="true">' + esc(initials(g.name).charAt(0)) + "</span>" +
      '<span class="pf-row-body">' + textHtml(g.name, sub, "org-dept-title") + '<span class="pf-chev" aria-hidden="true">›</span></span></button>';
    if (g.teams.length) {
      html += '<div class="org-tree">' + g.teams.map(function (t) {
        return '<button type="button" class="org-tree-row" data-org="nav" data-view="dept" data-id="' + g.id + '">' +
          '<span class="org-tree-name">' + esc(t.name) + '</span><span class="org-tree-count">' + t.member_ids.length + "</span></button>";
      }).join("") + "</div>";
    }
    return '<div class="pf-list org-dept">' + html + "</div>";
  }

  function groupsHtml(d) {
    var can = d.can || {};
    var groups = d.groups || [];
    var html = '<p class="pf-hint org-lead-hint">Отдел и его команды — кому открывать разделы базы знаний и сейфы. Открытое отделу видят все его команды, открытое команде — только она. «Все» — участники организации, кроме гостей.</p>';
    if (groups.length) html += '<div class="org-depts">' + groups.map(function (g) { return deptCardHtml(d, g); }).join("") + "</div>";
    else if (!can.invite) html += '<p class="pf-hint">Отделов пока нет.</p>';
    if (can.invite) {
      html += group("Новый отдел", "groups", '<div class="pf-list"><div class="pf-row pf-row--static"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
        '<span class="pf-row-body org-add-row"><input type="text" class="pf-input" id="org-dept-new" maxlength="60" placeholder="Например, «ОКК»" aria-label="Название отдела" />' +
        '<button type="button" class="pf-link" data-org="dept-add">Добавить</button></span></div></div>');
    }
    return html;
  }

  // Человек в отделе или команде; нажатие ведёт в его карточку.
  function personRows(d, g, manage, leadId) {
    return g.member_ids.map(function (uid) {
      var m = (d.members || []).filter(function (x) { return x.user_id === uid; })[0];
      var name = personName(d, uid);
      var sub = leadId === uid ? "руководитель" : "";
      var del = manage ? delBtn('data-org="group-member-del" data-gid="' + g.id + '" data-uid="' + esc(uid) + '"', "Убрать " + name) : "";
      var av = '<span class="pf-avatar" aria-hidden="true">' + esc(initials(name.replace(/ \(вы\)$/, ""))) + "</span>";
      if (!m) return '<div class="pf-row pf-row--static">' + av + '<span class="pf-row-body">' + textHtml(name, sub) + del + "</span></div>";
      return '<div class="pf-row pf-row--static org-person-row">' + av + '<span class="pf-row-body">' +
        '<button type="button" class="org-person-link" data-org="nav" data-view="member" data-id="' + m.id + '">' + textHtml(name, sub) + "</button>" + del + "</span></div>";
    }).join("");
  }

  function deptHtml(d, g) {
    var can = d.can || {};
    var html = "";
    var head = "";
    if (g.can_manage) {
      head += '<label class="pf-field"><span class="pf-field-label">Название отдела</span><input type="text" class="pf-input" data-org="group-name" data-gid="' + g.id + '" maxlength="60" value="' + esc(g.name) + '" /></label>';
    }
    if (can.edit_org || can.invite) {
      var leadOpts = [["", "Не назначен"]].concat((d.members || []).filter(function (m) { return m.status === "active"; })
        .map(function (m) { return [m.user_id, memberName(d, m)]; }));
      head += staticRow("Руководитель", "Управляет составом отдела и его командами", selectHtml(leadOpts, g.lead_user_id || "",
        'data-org="group-lead" data-gid="' + g.id + '" aria-label="Руководитель отдела"'), "label");
    } else {
      head += staticRow("Руководитель", "", '<span class="pf-row-val">' + esc(leadName(d, g) || "Не назначен") + "</span>");
    }
    html += group("Отдел", "groups", '<div class="pf-list">' + head + "</div>");

    g.teams.forEach(function (t) {
      var del = g.can_manage ? '<button type="button" class="pf-link pf-link--muted org-team-del" data-org="group-del" data-gid="' + t.id + '">Удалить команду</button>' : "";
      var body = personRows(d, t, g.can_manage, null) + (g.can_manage ? addPersonRow(d, t, "Добавить в «" + t.name + "»") : "");
      html += '<div class="pf-group"><p class="pf-group-title org-team-title"><span>Команда · ' + esc(t.name) + " · " + t.member_ids.length + "</span>" + del + "</p>" +
        (body ? '<div class="pf-list">' + body + "</div>" : '<p class="pf-hint">В команде пока никого.</p>') + "</div>";
    });

    var direct = personRows(d, g, g.can_manage, g.lead_user_id) + (g.can_manage ? addPersonRow(d, g, "Добавить в отдел") : "");
    html += group((g.teams.length ? "Без команды" : "Люди") + " · " + g.member_ids.length, "",
      "<div>" + (direct ? '<div class="pf-list">' + direct + "</div>" : '<p class="pf-hint">Пока никого.</p>') +
      '<p class="pf-hint">Открытое отделу видят все его команды, открытое команде — только она.</p></div>');

    if (g.can_manage) {
      html += group("Новая команда", "", '<div class="pf-list"><div class="pf-row pf-row--static"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
        '<span class="pf-row-body org-add-row"><input type="text" class="pf-input" id="org-team-new-' + g.id + '" maxlength="60" placeholder="Например, «' + esc(g.name) + ' · бэкенд»" aria-label="Название команды" />' +
        '<button type="button" class="pf-link" data-org="team-add" data-gid="' + g.id + '">Добавить</button></span></div></div>');
    }
    if (can.edit_org || can.invite) {
      html += '<div class="pf-list"><button type="button" class="pf-row pf-row--danger" data-org="group-del" data-gid="' + g.id + '" data-dept="1"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Удалить отдел</span></span></span></button></div>';
    }
    return html;
  }


  function addGroup(name, parentId) {
    var id = currentId();
    name = String(name || "").trim();
    if (!name) return;
    return autosave("groups", async function () {
      var d = await api("/" + id + "/groups", json("POST", { name: name, parent_id: parentId || null }));
      if (!parentId && d.created) state.view = { name: "dept", id: String(d.created.id) };
      return d;
    });
  }

  function contactKey(c) {
    var email = String(c.email || "").trim().toLowerCase();
    return email && email.indexOf("@") > 0 ? email : "";
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
        return '<option value="' + esc(key + "|" + nick) + '">' + esc(label + extra) + "</option>";
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

  function addMember(key, tg) {
    key = String(key || "").trim();
    tg = String(tg || "").trim();
    if (!key) {
      if (tg) setSaved("members", "err", "Укажите почту — по ней участник входит в Орбиту");
      return Promise.resolve();
    }
    var id = currentId();
    setSaved("members", "busy", "Добавляю…");
    return act(async function () {
      try {
        var r = await api("/" + id + "/members", json("POST", { key: key, tg: tg }));
        state.detail = r;
        var invited = r.added && r.added.status === "invited";
        setSaved("members", "ok", invited ? (r.added.mailed ? "Приглашён — отправили письмо" : "Приглашён — войдёт по почте") : "Добавлен ✓");
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
    if (kind === "people-open") {
      if (window.LeoPeople) window.LeoPeople.open(id);
      return;
    }
    if (kind === "nav") {
      var cur = view();
      var target = btn.getAttribute("data-view");
      go(target, btn.getAttribute("data-id"));
      // Карточка человека, открытая из отдела, возвращает в отдел.
      if (target === "member" && cur.name === "dept") state.view.from = { name: "dept", id: cur.id };
      return;
    }
    if (kind === "member-filter") {
      state.memberFilter = btn.getAttribute("data-filter") || "all";
      render();
      return;
    }
    if (kind === "dept-add") return addGroup((document.getElementById("org-dept-new") || {}).value, null);
    if (kind === "team-add") {
      var pid = btn.getAttribute("data-gid");
      return addGroup((document.getElementById("org-team-new-" + pid) || {}).value, Number(pid));
    }
    if (kind === "group-del") {
      return act(async function () {
        var dept = btn.getAttribute("data-dept");
        if (!(await confirm(dept ? "Удалить отдел вместе с его командами? Доступы, открытые им, пропадут." : "Удалить команду? Доступы, открытые ей, пропадут."))) return;
        await applyDetail(await api("/" + id + "/groups/" + btn.getAttribute("data-gid"), { method: "DELETE" }));
      });
    }
    if (kind === "group-member-del") {
      return autosave("groups", function () {
        return api("/" + id + "/groups/" + btn.getAttribute("data-gid") + "/members/" + btn.getAttribute("data-uid"), { method: "DELETE" });
      });
    }
    if (kind === "create") {
      return act(async function () {
        var name = (document.getElementById("org-new-name") || {}).value || "";
        var domEl = document.getElementById("org-new-domain");
        await api("", json("POST", { name: name.trim(), domain: domEl ? domEl.value : "" }));
        state.view = null;
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
      var tgInp = document.getElementById("org-add-tg");
      return addMember(inp && inp.value, tgInp && tgInp.value);
    }
    if (kind === "approve" || kind === "reject") {
      return act(async function () {
        var d = await api("/" + id + "/approve", json("POST", { user_id: Number(btn.getAttribute("data-uid")), allow: kind === "approve" }));
        await applyDetail(d);
      });
    }
    if (kind === "remove") {
      return act(async function () {
        if (!(await confirm("Исключить из организации? Его документы в базе знаний перейдут владельцу организации."))) return;
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
        if (!(await confirm("Выйти из организации «" + state.detail.org.name + "»? Ваши документы в её базе знаний останутся у организации — у вас их больше не будет."))) return;
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
        state.view = null;
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
    if (kind === "group-member-add" && el.value) {
      var gUid = Number(el.value);
      var gId = el.getAttribute("data-gid");
      return autosave("groups", function () {
        return api("/" + id + "/groups/" + gId + "/members", json("POST", { user_id: gUid }));
      });
    }
    if (kind === "member-group-add" && el.value) {
      var mgUid = Number(el.getAttribute("data-uid"));
      var mgGid = el.value;
      return autosave("groups", function () {
        return api("/" + id + "/groups/" + mgGid + "/members", json("POST", { user_id: mgUid }));
      });
    }
    if (kind === "group-lead") {
      var lead = el.value ? Number(el.value) : null;
      var leadGid = el.getAttribute("data-gid");
      return autosave("groups", function () {
        return api("/" + id + "/groups/" + leadGid, json("PATCH", { lead_user_id: lead }));
      });
    }
    if (kind === "group-name") {
      var gname = el.value.trim();
      var nameGid = el.getAttribute("data-gid");
      if (!gname) return;
      return autosave("groups", function () {
        return api("/" + id + "/groups/" + nameGid, json("PATCH", { name: gname }));
      });
    }
    if (kind === "guest") {
      var guestMid = el.getAttribute("data-mid");
      var isGuest = !!el.checked;
      return autosave("members", function () {
        return api("/" + id + "/members/" + guestMid + "/guest", json("PATCH", { guest: isGuest }));
      });
    }
    if (kind === "add-contact") {
      var parts = String(el.value || "").split("|");
      return addMember(parts[0], parts[1] || "");
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
    if (t.id === "org-people-metrics") {
      var on = !!t.checked;
      act(async function () {
        if (on && !(await confirm("Включить показатели сотрудников? Всем участникам организации придёт сообщение: что считается и кто это видит."))) {
          t.checked = false;
          return;
        }
        setSaved("people", "busy", "Сохраняю…");
        try {
          await api("/" + id + "/people-metrics", json("PATCH", { enabled: on }));
          setSaved("people", "ok", "Сохранено ✓");
        } catch (e) {
          setSaved("people", "err", errText(e));
        }
        await reload();
      });
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
    if (e.target.id === "org-member-q") {
      state.memberQuery = e.target.value;
      var box = document.getElementById("org-members-list");
      if (box && state.detail) box.innerHTML = membersListHtml(state.detail);
      return;
    }
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
    var peopleBtn = document.getElementById("profile-people-open");
    if (peopleBtn) peopleBtn.addEventListener("click", openPeopleFromProfile);
    var back = document.getElementById("profile-org-back");
    if (back) back.addEventListener("click", goBack);
    var el = root();
    if (el) {
      el.addEventListener("click", onClick);
      el.addEventListener("change", onChange);
      el.addEventListener("input", onInput);
      el.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        if (e.target.id === "org-dept-new") {
          e.preventDefault();
          addGroup(e.target.value, null);
        } else if (e.target.id.indexOf("org-team-new-") === 0) {
          e.preventDefault();
          addGroup(e.target.value, Number(e.target.id.slice("org-team-new-".length)));
        } else if (e.target.id === "org-add-key" || e.target.id === "org-add-tg") {
          e.preventDefault();
          var tgEl = document.getElementById("org-add-tg");
          var keyEl = document.getElementById("org-add-key");
          addMember(keyEl && keyEl.value, tgEl && tgEl.value);
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
    openMarket: openMarket,
    syncPeopleRow: function () {
      return api("").then(function (ov) {
        state.overview = ov;
        syncPeopleRow();
      }).catch(function () {});
    },
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
