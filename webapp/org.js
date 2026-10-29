/* Профиль → «Организация»: создать организацию, участники и роли, домен почты, ссылки-приглашения.
 * Вход по ссылке: /webapp/?join=<token> или startapp=join_<token> в миниаппе. */
(function () {
  "use strict";

  var ROLE_LABEL = { owner: "Владелец", admin: "Админ", member: "Участник" };
  var state = { overview: null, detail: null, busy: false, slugTimer: null };

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

  async function act(fn) {
    if (state.busy) return;
    state.busy = true;
    try {
      await fn();
    } catch (e) {
      toast(errText(e));
    } finally {
      state.busy = false;
    }
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
      state.detail = cur ? await api("/" + cur) : null;
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
          : '<button type="button" class="pf-btn org-btn-sm" data-org="join" data-id="' + s.id + '">Вступить</button>' +
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
      html += '<div class="pf-group"><p class="pf-group-title">Название и адрес</p><div class="pf-list">' +
        '<label class="pf-field"><span class="pf-field-label">Название</span><input type="text" class="pf-input" id="org-name" maxlength="80" value="' + esc(org.name) + '" /></label>' +
        '<label class="pf-field"><span class="pf-field-label">Короткий адрес</span><input type="text" class="pf-input" id="org-slug" maxlength="40" value="' + esc(org.slug) + '" autocapitalize="off" spellcheck="false" /></label>' +
        '</div><p class="pf-hint" id="org-slug-hint">Латиница, цифры и дефис. Пригодится для публичной страницы выпуска и поддомена.</p>' +
        '<button type="button" class="pf-btn org-btn-sm" data-org="save-org">Сохранить</button></div>';
    }

    html += billingHtml(d);
    html += membersHtml(d);

    if (can.invite) {
      html += '<div class="pf-group"><p class="pf-group-title">Как вступают</p><div class="pf-list">';
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
    var html = '<div class="pf-group"><p class="pf-group-title">Тариф</p>';
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
    if (can.billing) {
      html += '<p class="pf-hint">Тариф каждому участнику выдаётся в списке ниже.</p>' +
        '<button type="button" class="pf-btn org-btn-sm" data-org="save-billing">Сохранить тариф</button>';
    }
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
          right = '<button type="button" class="pf-btn org-btn-sm" data-org="approve" data-uid="' + esc(m.user_id) + '">Принять</button>' +
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
      add = '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></span>' +
        '<span class="pf-row-body"><input type="text" class="pf-input" id="org-add-key" placeholder="Почта или @ник в Telegram" autocapitalize="off" spellcheck="false" />' +
        '<button type="button" class="pf-link" data-org="add">Добавить</button></span></div>';
    }
    return '<div class="pf-group"><p class="pf-group-title">Участники</p><div class="pf-list">' + rows + add + "</div>" +
      (can.set_role ? '<p class="pf-hint">Админ приглашает и исключает участников и настраивает выпуск «Рынок». Владелец ещё меняет название и адрес, назначает админов. Назначить другого владельцем — передать владение.</p>' : "") +
      "</div>";
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
    if (kind === "save-billing") {
      return act(async function () {
        var d = await api("/" + id + "/billing", json("PATCH", {
          plan: document.getElementById("org-plan").value,
          seats: Number(document.getElementById("org-seats").value || 0),
        }));
        toast("Тариф сохранён");
        await applyDetail(d);
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
    if (kind === "save-org") {
      return act(async function () {
        var d = await api("/" + id, json("PATCH", {
          name: document.getElementById("org-name").value.trim(),
          slug: document.getElementById("org-slug").value.trim().toLowerCase(),
        }));
        toast("Сохранено");
        await applyDetail(d);
      });
    }
    if (kind === "add") {
      return act(async function () {
        var inp = document.getElementById("org-add-key");
        var r = await api("/" + id + "/members", json("POST", { key: (inp && inp.value) || "" }));
        toast(r.added && r.added.status === "invited" ? "Приглашён — появится после первого входа" : "Добавлен в организацию");
        await applyDetail(r);
      });
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
      return act(async function () {
        try {
          await applyDetail(await api("/" + id + "/members/" + el.getAttribute("data-mid") + "/plan", json("PATCH", { plan: el.value })));
        } catch (err) {
          await reload();
          throw err;
        }
      });
    }
    if (kind === "join-mode") {
      return act(async function () {
        await applyDetail(await api("/" + id, json("PATCH", { join_mode: el.value })));
      });
    }
    if (kind === "role") {
      var uid = el.getAttribute("data-uid");
      return act(async function () {
        var ownerId = String((state.detail.members || []).filter(function (m) { return m.role === "owner"; }).map(function (m) { return m.user_id; })[0] || "");
        if (el.value === "owner" && uid !== ownerId &&
            !(await confirm("Передать владение? Вы станете админом."))) {
          await reload();
          return;
        }
        try {
          await applyDetail(await api("/" + id + "/members/" + uid, json("PATCH", { role: el.value })));
        } catch (err) {
          await reload();
          throw err;
        }
      });
    }
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
    }
    var token = pendingJoinToken();
    if (token) setTimeout(function () { handleJoin(token, 0); }, 800);
    // подпись в профиле: «<организация>» или «Обучат уже в Орбите — вступить»
    setTimeout(function () {
      api("").then(function (ov) {
        state.overview = ov;
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
