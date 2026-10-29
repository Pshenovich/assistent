/* Профиль → «Организация»: создать организацию, участники и роли, домен почты, ссылки-приглашения.
 * Вход по ссылке: /webapp/?join=<token> или startapp=join_<token> в миниаппе. */
(function () {
  "use strict";

  var ROLE_LABEL = { owner: "Владелец", admin: "Админ", member: "Участник" };
  var state = { overview: null, detail: null, market: null, contacts: null, queue: Promise.resolve(), saved: {}, slugTimer: null };

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

  function render() {
    var el = root();
    if (!el) return;
    var ov = state.overview || {};
    var html = "";
    if ((ov.orgs || []).length > 1) html += switcherHtml(ov);
    html += suggestionsHtml(ov);
    html += state.detail ? detailHtml(state.detail) : createHtml(ov);
    el.innerHTML = html;
  }

  function switcherHtml(ov) {
    var opts = ov.orgs
      .map(function (o) {
        return '<option value="' + o.id + '"' + (o.id === ov.current_id ? " selected" : "") + ">" + esc(o.name) + "</option>";
      })
      .join("");
    return (
      '<div class="pf-list"><label class="pf-field"><span class="pf-field-label">Текущая организация</span>' +
      '<select class="pf-input" data-org="switch">' + opts + "</select></label></div>"
    );
  }

  function suggestionsHtml(ov) {
    return (ov.suggestions || [])
      .map(function (s) {
        var body = s.pending
          ? '<span class="pf-row-sub">Заявка отправлена — ждём одобрения админа</span>'
          : '<span class="pf-row-sub">Коллеги с вашего домена уже здесь</span>';
        var btns = s.pending
          ? ""
          : '<button type="button" class="pf-link" data-org="join" data-id="' + s.id + '">Вступить</button>' +
            '<button type="button" class="pf-link" data-org="dismiss" data-id="' + s.id + '">Не сейчас</button>';
        return (
          '<div class="pf-list org-suggest"><div class="pf-row pf-row--static">' + logoHtml(s) +
          '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(s.name) + "</span>" + body +
          '</span><span class="org-actions">' + btns + "</span></span></div></div>"
        );
      })
      .join("");
  }

  function logoHtml(org) {
    if (org.logo_url) return '<span class="org-logo"><img src="' + esc(org.logo_url) + '" alt="" /></span>';
    return '<span class="org-logo" aria-hidden="true">' + esc(String(org.name || "?").trim().charAt(0).toUpperCase()) + "</span>";
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
      '<div class="pf-list">' +
      '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-new-name" maxlength="80" placeholder="Например, Обучат" /></label>' +
      domSel + "</div>" +
      '<p class="pf-hint">' + esc(domHint) + "</p>" +
      '<button type="button" class="pf-btn" data-org="create">Создать организацию</button>'
    );
  }

  function detailHtml(d) {
    var org = d.org;
    var can = d.can || {};
    var html = '<div class="pf-list"><div class="pf-row pf-row--static org-head">' + logoHtml(org) +
      '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(org.name) + "</span>" +
      '<span class="pf-row-sub">' + esc(ROLE_LABEL[org.my_role] || "") + " · " + org.member_count + " " + plural(org.member_count, "участник", "участника", "участников") +
      "</span></span></span></div></div>";

    if (can.edit_org) {
      html += '<div class="pf-group"><p class="pf-group-title">Название и адрес' + saveMark("org") + '</p><div class="pf-list">' +
        '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-name" maxlength="80" value="' + esc(org.name) + '" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Короткий адрес</span><input type="text" class="pf-input" id="org-slug" maxlength="40" value="' + esc(org.slug) + '" autocapitalize="off" spellcheck="false" /></label>' +
        '</div><p class="pf-hint" id="org-slug-hint">Латиница, цифры и дефис. Пригодится для публичной страницы выпуска и поддомена.</p></div>';
    }

    html += marketHtml(state.market);
    html += billingHtml(d);
    html += membersHtml(d);

    if (can.invite) {
      html += '<div class="pf-group"><p class="pf-group-title">Как вступают' + saveMark("join") + '</p><div class="pf-list">';
      (org.domains || []).forEach(function (dom) {
        html += '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">@' + esc(dom) + "</span>" +
          '<span class="pf-row-sub">Коллеги с этим доменом почты видят организацию</span></span>' +
          (can.edit_org ? '<button type="button" class="pf-link" data-org="domain-del" data-domain="' + esc(dom) + '">Убрать</button>' : "") +
          "</span></div>";
      });
      var free = (d.my_domains || []).filter(function (x) { return (org.domains || []).indexOf(x) < 0; });
      if (can.edit_org && free.length) {
        html += '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Добавить @' + esc(free[0]) + "</span></span>" +
          '<button type="button" class="pf-link" data-org="domain-add" data-domain="' + esc(free[0]) + '">Добавить</button></span></div>';
      }
      if ((org.domains || []).length) {
        html += '<label class="pf-field"><span class="pf-field-label">Коллеги с домена</span><select class="pf-input" data-org="join-mode">' +
          '<option value="request"' + (org.join_mode === "request" ? " selected" : "") + ">Вступают по заявке — я одобряю</option>" +
          '<option value="auto"' + (org.join_mode === "auto" ? " selected" : "") + ">Вступают сами</option></select></label>";
      }
      html += "</div>";
      html += invitesHtml(d) + "</div>";
    }

    html += '<div class="pf-list org-danger">';
    if ((state.overview.orgs || []).length) {
      html += '<button type="button" class="pf-row pf-row--danger" data-org="leave"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Выйти из организации</span></span></span></button>';
    }
    if (can.delete_org) {
      html += '<button type="button" class="pf-row pf-row--danger" data-org="delete"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Удалить организацию</span></span></span></button>';
    }
    html += "</div>";
    if ((state.overview.orgs || []).length) {
      html += '<button type="button" class="pf-link org-new-link" data-org="show-create">+ Создать ещё одну организацию</button>';
    }
    return html;
  }

  // Выпуск «Рынок» организации: о компании, конкуренты, темы, свои источники, время + личное «в Telegram».
  function marketHtml(m) {
    if (!m || !m.settings) return "";
    var st = m.settings;
    var html = '<div class="pf-group"><p class="pf-group-title">Выпуск «Рынок»' + saveMark("market") + "</p>";
    html += '<p class="pf-hint">По этим настройкам Лео пишет в «Дайджест → Рынок» блок «Что это значит для вас». Сам выпуск под вашу компанию (свои источники и отбор новостей) — в следующем обновлении, пока он общий.</p>';
    html += '<div class="pf-list">';
    if (m.can_edit) {
      html +=
        '<label class="pf-field"><span class="pf-field-label">О компании</span><textarea class="pf-input org-mkt-area" id="org-mkt-about" rows="3" maxlength="1500" placeholder="Чем занимаетесь, для кого, что важно отслеживать">' + esc(st.about) + "</textarea></label>" +
        '<label class="pf-field"><span class="pf-field-label">Конкуренты</span><input type="text" class="pf-input" id="org-mkt-competitors" value="' + esc((st.competitors || []).join(", ")) + '" placeholder="Через запятую: Gong, Clari, amoCRM" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Темы</span><input type="text" class="pf-input" id="org-mkt-topics" value="' + esc((st.topics || []).join(", ")) + '" placeholder="Через запятую: речевая аналитика, ИИ в продажах" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Свои источники</span><textarea class="pf-input org-mkt-area" id="org-mkt-sources" rows="2" placeholder="Ссылки на RSS, по одной в строке">' + esc((st.sources || []).join("\n")) + "</textarea></label>" +
        '<label class="pf-field"><span class="pf-field-label">Время выпуска</span><input type="time" class="pf-input" id="org-mkt-time" value="' + esc(st.time) + '" /></label>';
    } else {
      var rows = [["Конкуренты", (st.competitors || []).join(", ")], ["Темы", (st.topics || []).join(", ")], ["Время выпуска", st.time]];
      rows.forEach(function (r) {
        if (!r[1]) return;
        html += '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(r[0]) +
          '</span><span class="pf-row-sub">' + esc(r[1]) + "</span></span></span></div>";
      });
    }
    html += '<label class="pf-row"><input type="checkbox" class="pf-check-input" id="org-mkt-telegram"' + (m.telegram ? " checked" : "") + " />" +
      '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Присылать мне выпуск в Telegram</span>' +
      '<span class="pf-row-sub">Коротко главное и ссылка на выпуск</span></span><span class="pf-check" aria-hidden="true"></span></span></label>';
    html += "</div></div>";
    return html;
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
    return '<select class="pf-input org-plan" ' + attrs + ">" +
      (d.plans || []).map(function (p) {
        return '<option value="' + esc(p.id) + '"' + (p.id === value ? " selected" : "") + ">" + esc(p.label) + "</option>";
      }).join("") + "</select>";
  }

  function billingHtml(d) {
    var org = d.org;
    var can = d.can || {};
    var used = org.seats_used || 0;
    var seats = org.seats || 0;
    var html = '<div class="pf-group"><p class="pf-group-title">Тариф' + saveMark("billing") + "</p>";
    if (!d.billing_enabled) {
      html += '<p class="org-demo">Оплата ещё не подключена — тарифы можно менять, но они пока ни на что не влияют.</p>';
    }
    html += '<div class="pf-list">';
    if (can.billing) {
      html += '<label class="pf-field"><span class="pf-field-label">Тариф организации</span>' +
        planSelect(d, org.plan, 'id="org-plan" aria-label="Тариф организации"') + "</label>" +
        '<label class="pf-field"><span class="pf-field-label">Мест с платным тарифом</span>' +
        '<input type="number" class="pf-input" id="org-seats" min="0" max="10000" inputmode="numeric" value="' + seats + '" /></label>';
    } else {
      html += '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
        esc(planLabel(d, org.plan)) + "</span></span></span></div>";
    }
    html += '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Занято мест</span>' +
      '<span class="pf-row-sub">Место занимает участник с платным тарифом</span></span>' +
      '<span class="pf-row-val' + (used > seats ? " org-hint-bad" : "") + '">' + used + " из " + seats + "</span></span></div></div>";
    if (can.billing) html += '<p class="pf-hint">Тариф каждому участнику выдаётся в списке ниже.</p>';
    return html + "</div>";
  }

  function membersHtml(d) {
    var can = d.can || {};
    var me = d.org.my_role;
    var rows = (d.members || [])
      .map(function (m) {
        var sub = m.username ? "@" + m.username : "";
        if (m.status === "pending") sub = "Просится в организацию";
        if (m.status === "invited") sub = "Приглашён — появится после первого входа в Орбиту";
        var right = "";
        if (m.status === "pending" && can.approve) {
          right = '<button type="button" class="pf-link" data-org="approve" data-uid="' + esc(m.user_id) + '">Принять</button>' +
            '<button type="button" class="pf-link" data-org="reject" data-uid="' + esc(m.user_id) + '">Отклонить</button>';
        } else if (m.status === "active" && can.set_role && m.user_id !== d.my_user_id) {
          right = '<select class="pf-input org-role" data-org="role" data-uid="' + esc(m.user_id) + '" aria-label="Роль">' +
            ["owner", "admin", "member"].map(function (r) {
              return '<option value="' + r + '"' + (m.role === r ? " selected" : "") + ">" + ROLE_LABEL[r] + "</option>";
            }).join("") + "</select>";
        } else if (m.status === "active") {
          right = '<span class="pf-row-val">' + esc(ROLE_LABEL[m.role] || "") + "</span>";
        }
        var canRemove = can.remove_member && m.role !== "owner" && (m.role !== "admin" || me === "owner");
        if (canRemove && m.status !== "pending") {
          right += '<button type="button" class="org-x" data-org="remove" data-mid="' + m.id + '" aria-label="Исключить" title="' +
            (m.status === "invited" ? "Отозвать приглашение" : "Исключить") + '">×</button>';
        }
        if (m.status !== "pending") {
          right = (can.billing
            ? planSelect(d, m.plan, 'data-org="member-plan" data-mid="' + m.id + '" aria-label="Тариф участника"')
            : '<span class="org-plan-chip' + (m.plan !== "free" ? " org-plan-chip--paid" : "") + '">' + esc(planLabel(d, m.plan)) + "</span>") + right;
        }
        var name = m.user_id ? m.name : String(m.name || "").replace(/^tg:/, "@");
        if (m.user_id === d.my_user_id) name += " (вы)";
        return '<div class="pf-row pf-row--static org-member"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(name) +
          "</span>" + (sub ? '<span class="pf-row-sub">' + esc(sub) + "</span>" : "") + '</span><span class="org-actions">' + right + "</span></span></div>";
      })
      .join("");
    var add = "";
    if (can.invite) {
      var opts = contactOptions(d);
      if (opts) {
        add += '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></span>' +
          '<span class="pf-row-body"><select class="pf-input" data-org="add-contact" aria-label="Добавить из контактов">' +
          '<option value="">Добавить из контактов…</option>' + opts + "</select></span></div>";
      }
      add += '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></span>' +
        '<span class="pf-row-body"><input type="text" class="pf-input" id="org-add-key" placeholder="Почта или @ник в Telegram" autocapitalize="off" spellcheck="false" />' +
        '<button type="button" class="pf-link" data-org="add">Добавить</button></span></div>';
    }
    return '<div class="pf-group"><p class="pf-group-title">Участники' + saveMark("members") + '</p><div class="pf-list">' + rows + add + "</div>" +
      (can.set_role ? '<p class="pf-hint">Админ приглашает и исключает участников и настраивает выпуск «Рынок». Владелец ещё меняет название и адрес, назначает админов. Назначить другого владельцем — передать владение.</p>' : "") +
      "</div>";
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

  function invitesHtml(d) {
    var tg = inTelegram();
    var list = (d.invites || [])
      .map(function (i) {
        var url = (tg && i.urls.telegram) || i.urls.web;
        return '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title org-link">' + esc(url) + "</span>" +
          '<span class="pf-row-sub">Действует до ' + esc(fmtDate(i.expires_at)) + "</span></span>" +
          '<span class="org-actions"><button type="button" class="pf-link" data-org="copy" data-url="' + esc(url) + '">Копировать</button>' +
          '<button type="button" class="org-x" data-org="revoke" data-token="' + esc(i.token) + '" aria-label="Отозвать" title="Отозвать">×</button></span></span></div>';
      })
      .join("");
    return '<div class="pf-list">' + list +
      '<button type="button" class="pf-row" data-org="invite"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Создать ссылку-приглашение</span>' +
      '<span class="pf-row-sub">Для тех, у кого почта не на домене, — подрядчиков, партнёров</span></span></span></button></div>';
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
    var btn = e.target.closest("[data-org]");
    if (!btn || btn.tagName === "SELECT") return;
    var kind = btn.getAttribute("data-org");
    var id = currentId();
    if (kind === "create") {
      return act(async function () {
        var name = (document.getElementById("org-new-name") || {}).value || "";
        var domEl = document.getElementById("org-new-domain");
        await api("", json("POST", { name: name.trim(), domain: domEl ? domEl.value : "" }));
        toast("Организация создана");
        await reload();
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
        hint.classList.toggle("org-hint-bad", !r.ok);
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

  window.LeoOrg = { open: open, reload: reload };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
