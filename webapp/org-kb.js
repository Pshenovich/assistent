// База знаний организации: разделы и документы во вкладке «База знаний», окно «Доступ»
// и полоска «только чтение / проверено» в открытом документе. Сервер — /orgs/{id}/kb.
// Личные документы базы знаний («Мои документы») по-прежнему рисует app.js.
(function () {
  "use strict";

  var state = {
    orgs: [], // [{org, kb}] — kb: ответ GET /orgs/{id}/kb
    sectionOrg: null, // открытый раздел: id организации
    sectionId: null,
    query: "",
    loading: null,
  };

  var ICO = {
    folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>',
    doc: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>',
    lock: '<svg class="kb-lock" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>',
    eye: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>',
    x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>',
    key: '<svg viewBox="0 0 24 24"><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M17 6l3 3M15 8l2 2" /></svg>',
  };
  var RANK = { reader: 1, commenter: 2, editor: 3, owner: 4 };
  var ROLE_LABEL = { reader: "Читатель", commenter: "Комментатор", editor: "Редактор", owner: "Владелец" };

  function app() {
    return window.LeoApp || {};
  }
  function esc(s) {
    return app().escapeHtml ? app().escapeHtml(String(s == null ? "" : s)) : String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function toast(msg) {
    if (app().toast) app().toast(msg);
  }
  function errText(e) {
    return (e && (e.detail || e.message)) || "Не получилось — попробуйте ещё раз";
  }
  function api(path, opts) {
    return app().apiFetch("/orgs" + path, opts || { method: "GET" });
  }
  function json(method, body) {
    return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  }
  function atLeast(role, need) {
    return (RANK[role] || 0) >= RANK[need];
  }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  function count(n, one, few, many) {
    return n + " " + plural(n, one, few, many);
  }
  // «сегодня», «3 дня назад», «7 месяцев»
  function ago(iso, bare) {
    var t = Date.parse(iso || "");
    if (!t) return "";
    var days = Math.floor((Date.now() - t) / 86400000);
    var s;
    if (days < 1) return bare ? "меньше дня" : "сегодня";
    if (days < 30) s = count(days, "день", "дня", "дней");
    else if (days < 365) s = count(Math.floor(days / 30), "месяц", "месяца", "месяцев");
    else s = count(Math.floor(days / 365), "год", "года", "лет");
    return bare ? s : s + " назад";
  }
  function initials(name) {
    var w = String(name || "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
    return ((w[0] || "?").charAt(0) + (w[1] || "").charAt(0)).toUpperCase();
  }

  // ---------- данные ----------

  function kbOf(orgId) {
    for (var i = 0; i < state.orgs.length; i++) if (state.orgs[i].org.id === orgId) return state.orgs[i].kb;
    return null;
  }
  function orgOf(orgId) {
    for (var i = 0; i < state.orgs.length; i++) if (state.orgs[i].org.id === orgId) return state.orgs[i].org;
    return null;
  }
  function nodeOf(kb, id) {
    var nodes = (kb && kb.nodes) || [];
    for (var i = 0; i < nodes.length; i++) if (nodes[i].id === id) return nodes[i];
    return null;
  }
  function childrenOf(kb, parentId) {
    return ((kb && kb.nodes) || []).filter(function (n) { return n.parent_id === parentId; });
  }
  function docsUnder(kb, id) {
    var out = [];
    childrenOf(kb, id).forEach(function (n) {
      if (n.kind === "doc") out.push(n);
      else out = out.concat(docsUnder(kb, n.id));
    });
    return out;
  }
  function principalName(kb, p) {
    if (p === "all") return "Все";
    var id = String(p).slice(2);
    if (p.indexOf("g:") === 0) {
      var found = "";
      (kb.groups || []).forEach(function (g) {
        if (String(g.id) === id) found = g.name;
        (g.teams || []).forEach(function (t) { if (String(t.id) === id) found = t.name; });
      });
      return found || "Удалённая группа";
    }
    var m = (kb.members || []).filter(function (x) { return String(x.user_id) === id; })[0];
    return m ? m.name : "Бывший участник";
  }
  function principalSub(kb, p) {
    if (p === "all") return "все участники организации, кроме гостей";
    var id = String(p).slice(2);
    if (p.indexOf("u:") === 0) return "";
    var sub = "";
    (kb.groups || []).forEach(function (g) {
      if (String(g.id) === id) sub = "отдел";
      (g.teams || []).forEach(function (t) { if (String(t.id) === id) sub = "команда"; });
    });
    return sub;
  }
  // «Все», «ОКК, Чат-боты + 2 человека», «только владельцы»
  function accessLine(kb, n) {
    var acc = n.access || [];
    var groups = acc.filter(function (p) { return p.indexOf("u:") !== 0; }).map(function (p) { return principalName(kb, p); });
    var people = acc.length - groups.length;
    var line = groups.length > 2 ? groups.slice(0, 2).join(", ") + " и ещё " + (groups.length - 2) : groups.join(", ");
    if (people) line += (line ? " + " : "") + count(people, "человек", "человека", "человек");
    return line || "только владельцы";
  }

  async function load() {
    if (state.loading) return state.loading;
    state.loading = (async function () {
      try {
        var ov = await api("");
        var orgs = (ov && ov.orgs) || [];
        var out = [];
        for (var i = 0; i < orgs.length; i++) {
          try {
            out.push({ org: orgs[i], kb: await api("/" + orgs[i].id + "/kb") });
          } catch (_) {
            var prev = orgOf(orgs[i].id) && kbOf(orgs[i].id);
            if (prev) out.push({ org: orgs[i], kb: prev });
          }
        }
        state.orgs = out;
        state.loaded = true;
      } catch (_) {
        // нет связи — оставляем то, что уже показано, а не «создайте организацию»
        if (!state.loaded) return;
      } finally {
        state.loading = null;
      }
      render();
    })();
    return state.loading;
  }

  function setKb(orgId, kb) {
    state.orgs.forEach(function (o) { if (o.org.id === orgId) o.kb = kb; });
    render();
  }

  // ---------- главный экран ----------

  function rowHtml(attrs, ico, title, sub, extra, cls) {
    return '<button type="button" class="pf-row ' + (cls || "") + '" ' + attrs + ">" +
      '<span class="pf-ico kb-ico" aria-hidden="true">' + ico + "</span>" +
      '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(title) + "</span>" +
      (sub ? '<span class="pf-row-sub pf-row-sub--wrap">' + sub + "</span>" : "") + "</span>" + (extra || "") +
      '<span class="pf-chev" aria-hidden="true">›</span></span></button>';
  }
  function addRowHtml(attrs, title) {
    return '<button type="button" class="pf-row pf-add-row" ' + attrs + '><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
      '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(title) + "</span></span></span></button>";
  }

  function sectionRow(kb, orgId, n) {
    var docs = docsUnder(kb, n.id);
    if (n.restricted) {
      return rowHtml('data-kb="open" data-org="' + orgId + '" data-id="' + n.id + '"', ICO.folder, n.title,
        esc("Вам " + plural(docs.length, "открыт", "открыто", "открыто") + " " + count(docs.length, "документ", "документа", "документов")),
        ICO.lock, "kb-row-restricted");
    }
    var stale = docs.filter(function (d) { return d.stale; }).length;
    var parts = [esc(count(docs.length, "документ", "документа", "документов")), esc(accessLine(kb, n))];
    if (stale) parts.push('<span class="kb-stale">' + esc(count(stale, "давно не проверялся", "давно не проверялись", "давно не проверялись")) + "</span>");
    return rowHtml('data-kb="open" data-org="' + orgId + '" data-id="' + n.id + '"', ICO.folder, n.title, parts.join(" · "));
  }

  function docSub(kb, d) {
    var owner = principalName(kb, "u:" + d.owner_user_id);
    var check = d.no_expiry
      ? "не устаревает"
      : d.stale
        ? '<span class="kb-stale">' + esc("не проверялся " + ago(d.verified_at || d.updated_at, true)) + "</span>"
        : esc("проверено " + ago(d.verified_at));
    return esc(owner) + " · " + check;
  }

  function docRow(kb, orgId, d) {
    return rowHtml('data-kb="doc" data-org="' + orgId + '" data-note="' + d.note_id + '"', ICO.doc, d.title, docSub(kb, d));
  }

  function orgGroupHtml(o) {
    var kb = o.kb;
    var org = o.org;
    var tops = childrenOf(kb, null);
    var head = '<p class="pf-group-title kb-group-head"><span class="kb-org-ico" aria-hidden="true">' +
      (org.logo_url ? '<img src="' + esc(org.logo_url) + '" alt="" />' : esc(initials(org.name).charAt(0))) + "</span>" + esc(org.name) +
      (kb.can_create_root && tops.length ? '<button type="button" class="pf-link" data-kb="new-root" data-org="' + org.id + '">+ Раздел</button>' : "") + "</p>";
    var body;
    if (!tops.length) {
      body = kb.can_create_root
        ? '<div class="pf-list">' + addRowHtml('data-kb="new-root" data-org="' + org.id + '"', "Создать первый раздел") + "</div>" +
          '<p class="pf-hint">Например, «Общее» или «Инструкции». Новый раздел видите только вы — откройте его отделу или всем в «Доступе».</p>'
        : '<p class="pf-hint kb-empty-hint">Коллеги ещё не открыли вам ни одного раздела.</p>';
    } else {
      body = '<div class="pf-list">' + tops.map(function (n) {
        return n.kind === "section" ? sectionRow(kb, org.id, n) : docRow(kb, org.id, n);
      }).join("") + "</div>";
    }
    return '<div class="pf-group kb-org-group">' + head + body + "</div>";
  }

  function searchHtml() {
    var q = state.query.toLowerCase();
    var rows = "";
    state.orgs.forEach(function (o) {
      (o.kb.nodes || []).forEach(function (n) {
        if (n.restricted || String(n.title || "").toLowerCase().indexOf(q) < 0) return;
        rows += n.kind === "doc" ? docRow(o.kb, o.org.id, n) : sectionRow(o.kb, o.org.id, n);
      });
    });
    if (!rows) return "";
    return '<div class="pf-group kb-org-group"><p class="pf-group-title">Разделы и документы организации</p><div class="pf-list">' + rows + "</div></div>";
  }

  function renderRoot() {
    var root = document.getElementById("org-kb-root");
    if (!root) return;
    var panel = document.getElementById("panel-knowledge");
    var has = state.orgs.length > 0;
    if (panel) panel.classList.toggle("kb-has-org", has);
    var mine = document.getElementById("knowledge-mine-title");
    if (mine) mine.classList.toggle("hidden", !has);
    var aiEntry = document.getElementById("knowledge-ai-entry");
    if (aiEntry) aiEntry.classList.toggle("hidden", !has);
    if (!state.loaded) {
      root.innerHTML = "";
      return;
    }
    if (!has) {
      root.innerHTML = '<p class="pf-hint kb-no-org">Создайте организацию, чтобы вести общую базу знаний с коллегами. ' +
        '<button type="button" class="pf-link" data-kb="org-open">Профиль → Организация</button></p>';
      return;
    }
    root.innerHTML = state.query ? searchHtml() : state.orgs.map(orgGroupHtml).join("");
  }

  // ---------- раздел ----------

  function crumbs(kb, org, id) {
    var out = [];
    var n = nodeOf(kb, id);
    while (n) {
      out.unshift(n);
      n = n.parent_id != null ? nodeOf(kb, n.parent_id) : null;
    }
    return out;
  }

  function renderSection() {
    var el = document.getElementById("org-kb-section");
    var rootEl = document.getElementById("knowledge-root");
    if (!el || !rootEl) return;
    var open = state.sectionId != null;
    el.classList.toggle("hidden", !open);
    rootEl.classList.toggle("hidden", open);
    if (!open) {
      el.innerHTML = "";
      return;
    }
    var kb = kbOf(state.sectionOrg);
    var org = orgOf(state.sectionOrg);
    var sec = kb && nodeOf(kb, state.sectionId);
    if (!sec) {
      state.sectionId = null;
      renderSection();
      return;
    }
    var path = crumbs(kb, org, sec.id);
    var back = path.length > 1 ? path[path.length - 2] : null;
    var role = sec.my_role;
    var html = '<div class="detail-toolbar detail-toolbar--subscreen kb-toolbar">' +
      '<button type="button" class="btn-text" data-kb="back">← ' + esc(back ? back.title : "База знаний") + "</button>" +
      '<span class="detail-toolbar-spacer"></span>' +
      (role === "owner" && !sec.restricted ? '<button type="button" class="pf-link" data-kb="access" data-id="' + sec.id + '">Доступ</button>' : "") +
      "</div>";
    html += '<div class="knowledge-list-head kb-section-head">';
    html += '<p class="kb-crumbs">' + esc(org.name) + path.slice(0, -1).map(function (n) { return " › " + esc(n.title); }).join("") + "</p>";
    html += '<h1 class="screen-title">' + esc(sec.title) + "</h1>";
    html += '<p class="muted lead lead-tight">' + (sec.restricted ? "Вам открыта только часть раздела" : esc("Открыт: " + accessLine(kb, sec))) + "</p></div>";

    var kids = childrenOf(kb, sec.id);
    var rows = kids.map(function (n) {
      return n.kind === "doc" ? docRow(kb, org.id, n) : sectionRow(kb, org.id, n);
    }).join("");
    if (atLeast(role, "editor") && !sec.restricted) {
      rows += addRowHtml('data-kb="new-doc" data-id="' + sec.id + '"', "Новый документ");
      rows += addRowHtml('data-kb="add-doc" data-id="' + sec.id + '"', "Добавить свой документ…");
      rows += addRowHtml('data-kb="new-sub" data-id="' + sec.id + '"', "Подраздел");
    }
    html += '<div class="pf-group">' + (rows ? '<div class="pf-list">' + rows + "</div>" : '<p class="pf-hint">В разделе пока пусто.</p>');
    if (atLeast(role, "editor") && !sec.restricted) {
      html += '<p class="pf-hint">Документы наследуют доступ раздела. Любая правка документа отмечает его проверенным.</p>';
    }
    html += "</div>";
    if (!sec.restricted && (atLeast(role, "editor") || role === "owner")) {
      var manage = "";
      if (atLeast(role, "editor")) {
        manage += '<button type="button" class="pf-row" data-kb="rename" data-id="' + sec.id + '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Переименовать раздел</span></span></span></button>';
      }
      if (role === "owner" && sec.parent_id != null) {
        manage += '<button type="button" class="pf-row" data-kb="move-section" data-id="' + sec.id + '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Переместить раздел…</span></span></span></button>';
      }
      if (role === "owner") {
        manage += '<button type="button" class="pf-row pf-row--danger" data-kb="remove" data-id="' + sec.id + '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Убрать раздел</span></span></span></button>';
      }
      html += '<div class="pf-list">' + manage + "</div>";
    }
    el.innerHTML = html;
  }

  function render() {
    renderRoot();
    renderSection();
    compactMineEmpty();
  }

  // Есть организация — пустые «Мои документы» не должны выглядеть как пустая база знаний.
  function compactMineEmpty() {
    var pane = document.getElementById("knowledge-pane");
    var empty = pane && pane.querySelector(".empty-state");
    if (!empty) return;
    var has = state.orgs.length > 0;
    empty.classList.toggle("kb-mine-empty", has);
    [
      [".empty-state-title", "Личных документов нет"],
      [".empty-state-text", "Их видите только вы, а Лео отвечает и по ним. Общие документы — в разделах выше."],
      ["[data-empty-action]", "Мой документ"],
    ].forEach(function (pair) {
      var el = empty.querySelector(pair[0]);
      if (!el) return;
      if (el._kbOrig == null) el._kbOrig = el.textContent; // исходный текст app.js — вернуть, если организации нет
      el.textContent = has ? pair[1] : el._kbOrig;
    });
  }

  function openSection(orgId, id) {
    state.sectionOrg = orgId;
    state.sectionId = id;
    renderSection();
    var el = document.getElementById("org-kb-section");
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "start" });
  }

  // ---------- шторка ----------

  function sheet(title, bodyHtml) {
    var ov = document.createElement("div");
    ov.className = "modal-overlay kb-sheet-overlay";
    ov.innerHTML = '<div class="modal modal--sheet kb-sheet" role="dialog" aria-modal="true">' +
      '<div class="modal-form"><div class="modal-header">' +
      '<button type="button" class="modal-header-btn modal-header-btn--close" data-kb-close aria-label="Закрыть" title="Закрыть">' +
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button>' +
      '<h2 class="modal-title">' + esc(title) + "</h2>" +
      '<span class="modal-header-btn modal-header-btn--placeholder" aria-hidden="true"></span></div>' +
      '<div class="modal-body"><div class="pf-screen-body kb-sheet-body">' + bodyHtml + "</div></div></div></div>";
    document.body.appendChild(ov);
    function close() {
      ov.remove();
      if (app().syncAppOverlay) app().syncAppOverlay();
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) {
      if (e.key === "Escape") close();
    }
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.closest("[data-kb-close]")) close();
    });
    document.addEventListener("keydown", onKey);
    if (app().syncAppOverlay) app().syncAppOverlay();
    return { el: ov, body: ov.querySelector(".kb-sheet-body"), close: close };
  }

  async function prompt(title, label, value) {
    if (app().promptDialog) return app().promptDialog(label, value || "");
    return window.prompt(label, value || "");
  }

  // ---------- «Доступ» ----------

  function roleSelect(value, attrs) {
    var roles = ["reader", "commenter", "editor", "owner"];
    return '<select class="pf-select kb-role" ' + attrs + ">" + roles.map(function (r) {
      return '<option value="' + r + '"' + (r === value ? " selected" : "") + ">" + ROLE_LABEL[r] + "</option>";
    }).join("") + "</select>";
  }

  function principalRow(kb, p, right, isInherited) {
    var name = principalName(kb, p);
    var sub = principalSub(kb, p);
    var pic = p === "all" ? "В" : initials(name);
    return '<div class="pf-row pf-row--static' + (isInherited ? " kb-inherited" : "") + '"><span class="pf-avatar" aria-hidden="true">' + esc(pic) + "</span>" +
      '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(name) + "</span>" +
      (sub ? '<span class="pf-row-sub">' + esc(sub) + "</span>" : "") + "</span>" + right + "</span></div>";
  }

  function addOptions(kb, taken) {
    var opts = '<option value="">Открыть: всем, отделу, команде, человеку…</option>';
    if (taken.indexOf("all") < 0) opts += '<option value="all">Все участники</option>';
    var groups = "";
    (kb.groups || []).forEach(function (g) {
      if (taken.indexOf("g:" + g.id) < 0) groups += '<option value="g:' + g.id + '">' + esc(g.name) + "</option>";
      (g.teams || []).forEach(function (t) {
        if (taken.indexOf("g:" + t.id) < 0) groups += '<option value="g:' + t.id + '">' + esc(t.name) + "</option>";
      });
    });
    if (groups) opts += '<optgroup label="Отделы и команды">' + groups + "</optgroup>";
    var people = "";
    (kb.members || []).forEach(function (m) {
      if (taken.indexOf("u:" + m.user_id) < 0) people += '<option value="u:' + esc(m.user_id) + '">' + esc(m.name) + (m.guest ? " (гость)" : "") + "</option>";
    });
    if (people) opts += '<optgroup label="Люди">' + people + "</optgroup>";
    return opts;
  }

  async function openAccess(orgId, nodeId) {
    var kb = kbOf(orgId);
    var node = kb && nodeOf(kb, nodeId);
    if (!node) return;
    var data;
    try {
      data = await api("/" + orgId + "/kb/nodes/" + nodeId + "/access");
    } catch (e) {
      toast(errText(e));
      return;
    }
    var parent = node.parent_id != null ? nodeOf(kb, node.parent_id) : null;
    var sh = sheet("Доступ", "");

    function paint() {
      var html = '<p class="kb-crumbs">' + (node.kind === "doc" ? "Документ" : "Раздел") + " <b>«" + esc(node.title) + "»</b></p>";
      if (data.can_inherit) {
        html += '<div class="pf-group"><div class="pf-list"><label class="pf-row pf-row--static"><span class="pf-row-body">' +
          '<span class="pf-row-text"><span class="pf-row-title">Как у раздела «' + esc(parent ? parent.title : "") + "»</span>" +
          '<span class="pf-row-sub">' + (data.inherit ? "Выключите, чтобы оставить только тех, кто ниже" : "Сейчас — только те, кто ниже") + "</span></span>" +
          '<input type="checkbox" class="pf-switch" data-kb-acc="inherit"' + (data.inherit ? " checked" : "") + ' aria-label="Как у раздела" /></span></label></div></div>';
      }
      if (data.inherit && data.inherited.length) {
        html += '<div class="pf-group"><p class="pf-group-title">От раздела</p><div class="pf-list">' +
          data.inherited.map(function (a) {
            return principalRow(kb, a.principal, '<span class="pf-row-val">' + esc(ROLE_LABEL[a.role] || a.role) + "</span>", true);
          }).join("") + "</div></div>";
      }
      var taken = data.own.map(function (a) { return a.principal; });
      html += '<div class="pf-group"><p class="pf-group-title">' + (data.can_inherit ? "Здесь" : "Кому открыт") + '<span class="org-save" data-kb-save></span></p><div class="pf-list">';
      if (node.kind === "doc") {
        html += principalRow(kb, "u:" + node.owner_user_id, '<span class="pf-row-val">Владелец · автор</span>');
      }
      html += data.own.map(function (a) {
        return principalRow(kb, a.principal,
          roleSelect(a.role, 'data-kb-acc="role" data-p="' + esc(a.principal) + '" aria-label="Роль"') +
          '<button type="button" class="pf-del" data-kb-acc="del" data-p="' + esc(a.principal) + '" aria-label="Убрать" title="Убрать">' + ICO.x + "</button>");
      }).join("");
      html += '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true">' + ICO.plus + "</span>" +
        '<span class="pf-row-body org-add-row kb-add-row"><select class="pf-input" data-kb-acc="add-who" aria-label="Кому открыть">' + addOptions(kb, taken) + "</select>" +
        roleSelect("reader", 'data-kb-acc="add-role" aria-label="Роль"') + "</span></div>";
      html += "</div>";
      html += '<p class="pf-hint">Читатель — только читает. Комментатор — ещё комментирует. Редактор — правит и добавляет документы. ' +
        "Владелец — ещё меняет доступ. Гости не входят во «Все». Владелец и админы организации видят всё.</p></div>";
      if (node.kind === "doc" && node.my_role === "owner") {
        html += '<div class="pf-group"><div class="pf-list"><label class="pf-row pf-row--static"><span class="pf-row-body">' +
          '<span class="pf-row-text"><span class="pf-row-title">Не устаревает</span><span class="pf-row-sub">Регламент или история: не напоминать о проверке</span></span>' +
          '<input type="checkbox" class="pf-switch" data-kb-acc="no-expiry"' + (node.no_expiry ? " checked" : "") + ' aria-label="Не устаревает" /></span></label></div></div>';
      }
      sh.body.innerHTML = html;
    }

    function mark(kind, text) {
      var m = sh.body.querySelector("[data-kb-save]");
      if (!m) return;
      m.textContent = text || "";
      m.className = "org-save" + (kind ? " org-save--" + kind : "");
    }

    async function run(req) {
      mark("", "Сохраняю…");
      try {
        data = await req();
        mark("ok", "Сохранено ✓");
        paint();
        mark("ok", "Сохранено ✓");
        setKb(orgId, await api("/" + orgId + "/kb"));
        kb = kbOf(orgId);
        node = nodeOf(kb, nodeId) || node;
      } catch (e) {
        mark("err", errText(e));
        paint();
      }
    }

    function put(principal, role) {
      return run(function () {
        return api("/" + orgId + "/kb/nodes/" + nodeId + "/access", json("PUT", { principal: principal, role: role }));
      });
    }

    sh.body.addEventListener("change", function (e) {
      var t = e.target;
      var kind = t.getAttribute("data-kb-acc");
      if (kind === "inherit") {
        run(function () {
          return api("/" + orgId + "/kb/nodes/" + nodeId + "/inherit", json("PUT", { inherit: !!t.checked }));
        });
      } else if (kind === "role") {
        put(t.getAttribute("data-p"), t.value);
      } else if (kind === "add-who" && t.value) {
        var roleEl = sh.body.querySelector('[data-kb-acc="add-role"]');
        put(t.value, roleEl ? roleEl.value : "reader");
      } else if (kind === "no-expiry") {
        app().apiFetch("/notes/local/" + node.note_id + "/verify", json("POST", { no_expiry: !!t.checked }))
          .then(function () { return api("/" + orgId + "/kb"); })
          .then(function (k) { setKb(orgId, k); kb = k; node = nodeOf(kb, nodeId) || node; })
          .catch(function (err) { toast(errText(err)); t.checked = !t.checked; });
      }
    });
    sh.body.addEventListener("click", function (e) {
      var b = e.target.closest('[data-kb-acc="del"]');
      if (b) put(b.getAttribute("data-p"), null);
    });
    paint();
  }

  // ---------- действия ----------

  async function refreshOrg(orgId, d) {
    setKb(orgId, d && d.nodes ? d : await api("/" + orgId + "/kb"));
  }

  async function newRoot(orgId) {
    var title = await prompt("Новый раздел", "Название раздела", "");
    title = String(title || "").trim();
    if (!title) return;
    try {
      var d = await api("/" + orgId + "/kb/sections", json("POST", { title: title }));
      await refreshOrg(orgId, d);
      if (d.created) openSection(orgId, d.created.id);
      toast("Раздел создан — его видите только вы. Откройте его в «Доступе»");
    } catch (e) {
      toast(errText(e));
    }
  }

  async function newSub(orgId, parentId) {
    var title = await prompt("Подраздел", "Название подраздела", "");
    title = String(title || "").trim();
    if (!title) return;
    try {
      await refreshOrg(orgId, await api("/" + orgId + "/kb/sections", json("POST", { title: title, parent_id: parentId })));
    } catch (e) {
      toast(errText(e));
    }
  }

  async function newDoc(orgId, sectionId) {
    try {
      var d = await api("/" + orgId + "/kb/docs", json("POST", { section_id: sectionId, title: "Новый документ" }));
      await refreshOrg(orgId, d);
      if (d.created && app().openNote) app().openNote(d.created.note_id);
    } catch (e) {
      toast(errText(e));
    }
  }

  async function addMyDoc(orgId, sectionId) {
    var items;
    try {
      items = (await api("/" + orgId + "/kb/my-docs")).items || [];
    } catch (e) {
      toast(errText(e));
      return;
    }
    var sec = nodeOf(kbOf(orgId), sectionId);
    var sh = sheet("Добавить в раздел", "");
    function paint(q) {
      q = String(q || "").toLowerCase();
      var rows = items.filter(function (it) { return !q || String(it.title).toLowerCase().indexOf(q) >= 0; }).slice(0, 80)
        .map(function (it) {
          return '<button type="button" class="pf-row" data-kb-pick="' + it.note_id + '"><span class="pf-ico kb-ico" aria-hidden="true">' + ICO.doc + "</span>" +
            '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(it.title) + "</span>" +
            '<span class="pf-row-sub">' + esc((it.is_knowledge ? "Мои документы базы знаний · " : "") + "изменён " + ago(it.updated_at)) + "</span></span></span></button>";
        }).join("");
      sh.body.querySelector(".kb-pick-list").innerHTML = rows || '<p class="pf-hint">Ничего не нашлось.</p>';
    }
    sh.body.innerHTML = '<p class="kb-crumbs">В раздел <b>«' + esc(sec ? sec.title : "") + "»</b></p>" +
      '<div class="notes-search-wrap kb-pick-search"><input type="search" class="notes-search-input" placeholder="Поиск по своим документам…" aria-label="Поиск" /></div>' +
      '<div class="pf-list kb-pick-list"></div>' +
      '<p class="pf-hint">Документ останется вашим, а доступ к нему получат все, кому открыт раздел.</p>';
    paint("");
    sh.body.querySelector("input").addEventListener("input", function (e) { paint(e.target.value); });
    sh.body.addEventListener("click", async function (e) {
      var b = e.target.closest("[data-kb-pick]");
      if (!b) return;
      try {
        var d = await api("/" + orgId + "/kb/docs", json("POST", { section_id: sectionId, note_id: Number(b.getAttribute("data-kb-pick")) }));
        sh.close();
        await refreshOrg(orgId, d);
        toast("Документ в разделе");
        if (app().setTab && document.getElementById("knowledge-pane")) loadMine();
      } catch (err) {
        toast(errText(err));
      }
    });
  }

  function loadMine() {
    // личный список мог измениться (документ ушёл в раздел) — app.js перерисует его при входе во вкладку
    try {
      if (app().setTab) app().setTab("knowledge");
    } catch (_) {}
  }

  async function renameSection(orgId, id) {
    var n = nodeOf(kbOf(orgId), id);
    var title = await prompt("Раздел", "Название раздела", n ? n.title : "");
    title = String(title || "").trim();
    if (!title || (n && title === n.title)) return;
    try {
      await refreshOrg(orgId, await api("/" + orgId + "/kb/nodes/" + id, json("PATCH", { title: title })));
    } catch (e) {
      toast(errText(e));
    }
  }

  async function removeSection(orgId, id) {
    var ok = app().confirm ? await app().confirm("Убрать раздел? Он должен быть пустым.") : window.confirm("Убрать раздел?");
    if (!ok) return;
    try {
      var n = nodeOf(kbOf(orgId), id);
      await refreshOrg(orgId, await api("/" + orgId + "/kb/nodes/" + id, { method: "DELETE" }));
      state.sectionId = n && n.parent_id != null ? n.parent_id : null;
      renderSection();
    } catch (e) {
      toast(errText(e));
    }
  }

  // Кнопка «+» вкладки: есть разделы, куда можно добавлять, — спросить куда; иначе — как раньше.
  function editableSections() {
    var out = [];
    state.orgs.forEach(function (o) {
      (o.kb.nodes || []).forEach(function (n) {
        if (n.kind === "section" && !n.restricted && atLeast(n.my_role, "editor")) out.push({ org: o.org, kb: o.kb, node: n });
      });
    });
    return out;
  }

  function sectionPath(kb, n) {
    return crumbs(kb, null, n.id).map(function (x) { return x.title; }).join(" › ");
  }

  function openCreateChooser() {
    var secs = editableSections().sort(function (a, b) {
      return (a.org.name + " " + sectionPath(a.kb, a.node)).localeCompare(b.org.name + " " + sectionPath(b.kb, b.node), "ru");
    });
    var sh = sheet("Новый документ", "");
    var rows = '<div class="pf-group"><p class="pf-group-title">В раздел организации</p><div class="pf-list">' +
      secs.map(function (s, i) {
        return '<button type="button" class="pf-row" data-kb-new="' + i + '"><span class="pf-ico kb-ico" aria-hidden="true">' + ICO.folder + "</span>" +
          '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(sectionPath(s.kb, s.node)) + "</span>" +
          '<span class="pf-row-sub">' + esc(s.org.name + " · " + accessLine(s.kb, s.node)) + "</span></span></span></button>";
      }).join("") + "</div></div>" +
      '<div class="pf-group"><div class="pf-list"><button type="button" class="pf-row" data-kb-new="mine"><span class="pf-ico kb-ico" aria-hidden="true">' + ICO.doc + "</span>" +
      '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Мой документ</span><span class="pf-row-sub">Видите только вы</span></span></span></button></div></div>';
    sh.body.innerHTML = rows;
    sh.body.addEventListener("click", function (e) {
      var b = e.target.closest("[data-kb-new]");
      if (!b) return;
      sh.close();
      var v = b.getAttribute("data-kb-new");
      if (v === "mine") {
        if (app().newKnowledgeNote) app().newKnowledgeNote();
        return;
      }
      var s = secs[Number(v)];
      if (s) newDoc(s.org.id, s.node.id);
    });
  }

  // ---------- перенести документ или раздел ----------

  function nodeForNote(noteId) {
    var hit = null;
    state.orgs.forEach(function (o) {
      (o.kb.nodes || []).forEach(function (n) {
        if (n.kind === "doc" && String(n.note_id) === String(noteId)) hit = { org: o.org, kb: o.kb, node: n };
      });
    });
    return hit;
  }

  // Куда можно положить: разделы, где человек редактор; для раздела — не он сам и не его подразделы.
  function targetsFor(moving) {
    var skip = {};
    if (moving && moving.node.kind === "section") {
      (function mark(id) {
        skip[id] = true;
        childrenOf(moving.kb, id).forEach(function (c) { if (c.kind === "section") mark(c.id); });
      })(moving.node.id);
    }
    return editableSections().filter(function (s) {
      if (moving && s.org.id !== moving.org.id) return false; // между организациями не переносим
      if (skip[s.node.id]) return false;
      return !(moving && moving.node.parent_id === s.node.id);
    }).sort(function (a, b) {
      return (a.org.name + " " + sectionPath(a.kb, a.node)).localeCompare(b.org.name + " " + sectionPath(b.kb, b.node), "ru");
    });
  }

  function chooseSection(title, lead, targets, onPick) {
    var sh = sheet(title, "");
    if (!targets.length) {
      sh.body.innerHTML = '<p class="pf-hint">Нет разделов, куда у вас есть права добавлять. Попросите владельца раздела дать вам роль редактора.</p>';
      return;
    }
    sh.body.innerHTML = (lead ? '<p class="kb-crumbs">' + lead + "</p>" : "") + '<div class="pf-list">' +
      targets.map(function (t, i) {
        return '<button type="button" class="pf-row" data-kb-target="' + i + '"><span class="pf-ico kb-ico" aria-hidden="true">' + ICO.folder + "</span>" +
          '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(sectionPath(t.kb, t.node)) + "</span>" +
          '<span class="pf-row-sub">' + esc(t.org.name + " · " + accessLine(t.kb, t.node)) + "</span></span></span></button>";
      }).join("") + "</div>" +
      '<p class="pf-hint">Документ получит доступ нового раздела.</p>';
    sh.body.addEventListener("click", function (e) {
      var b = e.target.closest("[data-kb-target]");
      if (!b) return;
      sh.close();
      onPick(targets[Number(b.getAttribute("data-kb-target"))]);
    });
  }

  async function moveNode(moving, target) {
    try {
      var d = await api("/" + moving.org.id + "/kb/nodes/" + moving.node.id, json("PATCH", { parent_id: target.node.id }));
      await refreshOrg(moving.org.id, d);
      toast("Перенесено в «" + target.node.title + "»");
      return true;
    } catch (e) {
      toast(errText(e));
      return false;
    }
  }

  // onDone(org, section) — документ теперь в этом разделе (обновить полоску открытого документа)
  async function placeNote(noteId, onDone) {
    if (!state.loaded) await load();
    var cur = nodeForNote(noteId);
    if (cur) {
      chooseSection("Переместить документ", "Документ <b>«" + esc(cur.node.title) + "»</b>", targetsFor(cur), function (t) {
        moveNode(cur, t).then(function (ok) {
          if (ok && onDone) onDone(t.org, t.node);
        });
      });
      return;
    }
    chooseSection("В базу знаний", "Документ останется вашим, доступ к нему получат все, кому открыт раздел.", targetsFor(null), async function (t) {
      try {
        var d = await api("/" + t.org.id + "/kb/docs", json("POST", { section_id: t.node.id, note_id: Number(noteId) }));
        await refreshOrg(t.org.id, d);
        toast("Документ в разделе «" + t.node.title + "»");
        if (onDone) onDone(t.org, t.node);
      } catch (e) {
        toast(errText(e));
      }
    });
  }

  function moveSection(orgId, id) {
    var kb = kbOf(orgId);
    var moving = { org: orgOf(orgId), kb: kb, node: nodeOf(kb, id) };
    if (!moving.node) return;
    chooseSection("Переместить раздел", "Раздел <b>«" + esc(moving.node.title) + "»</b> со всем, что внутри", targetsFor(moving), function (t) {
      moveNode(moving, t);
    });
  }

  function onClick(e) {
    var b = e.target.closest("[data-kb]");
    if (!b) return;
    var kind = b.getAttribute("data-kb");
    var orgId = Number(b.getAttribute("data-org") || state.sectionOrg);
    var id = Number(b.getAttribute("data-id"));
    if (kind === "open") return openSection(orgId, id);
    if (kind === "doc") {
      if (app().openNote) app().openNote(b.getAttribute("data-note"));
      return;
    }
    if (kind === "back") {
      var cur = nodeOf(kbOf(state.sectionOrg), state.sectionId);
      state.sectionId = cur && cur.parent_id != null ? cur.parent_id : null;
      renderSection();
      return;
    }
    if (kind === "new-root") return newRoot(orgId);
    if (kind === "new-sub") return newSub(orgId, id);
    if (kind === "new-doc") return newDoc(orgId, id);
    if (kind === "add-doc") return addMyDoc(orgId, id);
    if (kind === "access") return openAccess(orgId, id);
    if (kind === "rename") return renameSection(orgId, id);
    if (kind === "move-section") return moveSection(orgId, id);
    if (kind === "remove") return removeSection(orgId, id);
    if (kind === "org-open") {
      if (window.LeoOrg && window.LeoOrg.open) {
        if (app().setTab) app().setTab("profile");
        window.LeoOrg.open();
      }
    }
  }

  // ---------- полоска в открытом документе ----------

  function bannerHtml(note) {
    var kb = note.org_kb;
    var role = kb.role;
    var orgName = (orgOf(kb.org_id) || {}).name || "";
    var crumb = '<p class="kb-crumbs kb-doc-crumbs">' + esc(orgName) + (kb.section_title ? " › " + esc(kb.section_title) : "") + "</p>";
    var canEdit = atLeast(role, "editor");
    var text;
    var cls = "kb-banner";
    var action = "";
    if (kb.stale && !kb.no_expiry) {
      cls += " kb-banner--stale";
      text = "Не проверялся " + ago(kb.verified_at, true);
      if (canEdit) action = '<button type="button" class="pf-link" data-kb-banner="verify">Всё актуально</button>';
    } else {
      var check = kb.no_expiry ? "не устаревает" : "проверено " + ago(kb.verified_at);
      text = (role === "reader" ? "Только чтение · " : role === "commenter" ? "Можно комментировать · " : "") + check;
    }
    if (!canEdit) {
      action += note._kbRequested
        ? '<span class="kb-banner-done">Запрос отправлен</span>'
        : '<button type="button" class="pf-link" data-kb-banner="request">Попросить править</button>';
    }
    if (role === "owner") {
      action += '<button type="button" class="pf-link" data-kb-banner="move">Переместить</button>' +
        '<button type="button" class="pf-link" data-kb-banner="access">Доступ</button>';
    }
    var ico = kb.stale && !kb.no_expiry ? ICO.clock : canEdit ? ICO.check : ICO.eye;
    return crumb + '<div class="' + cls + '">' + ico + "<span>" + esc(text) + "</span>" + action + "</div>";
  }

  function onNoteOpen(e) {
    var d = (e && e.detail) || {};
    var note = d.note;
    var page = d.page;
    // читатель и комментатор: прячем панель правки (класс снимается на следующем открытом документе)
    var ro = !!(note && note.can_edit === false);
    document.documentElement.classList.toggle("kb-note-readonly", ro);
    document.documentElement.classList.toggle("kb-note-reader", ro && !note.can_comment);
    if (!note || !note.org_kb || !page) return;
    var box = document.createElement("div");
    box.className = "kb-doc-banner";
    box.innerHTML = bannerHtml(note);
    page.insertBefore(box, page.firstChild);
    box.addEventListener("click", async function (ev) {
      var b = ev.target.closest("[data-kb-banner]");
      if (!b) return;
      var kind = b.getAttribute("data-kb-banner");
      if (kind === "verify") {
        try {
          await app().apiFetch("/notes/local/" + note.id + "/verify", json("POST", {}));
          note.org_kb.stale = false;
          note.org_kb.verified_at = new Date().toISOString();
          box.innerHTML = bannerHtml(note);
          toast("Отмечено: всё актуально");
          refreshOrg(note.org_kb.org_id).catch(function () {});
        } catch (err) {
          toast(errText(err));
        }
      } else if (kind === "request") {
        try {
          var r = await app().apiFetch("/notes/local/" + note.id + "/request-edit", json("POST", {}));
          note._kbRequested = true;
          box.innerHTML = bannerHtml(note);
          toast(r && r.already ? "Запрос уже отправлен — ждём ответа автора" : "Запрос отправлен автору документа");
        } catch (err) {
          toast(errText(err));
        }
      } else if (kind === "move") {
        placeNote(note.id, function (org, sec) {
          note.org_kb.section_title = sec.title;
          box.innerHTML = bannerHtml(note);
        });
      } else if (kind === "access") {
        if (!kbOf(note.org_kb.org_id)) await load();
        openAccess(note.org_kb.org_id, note.org_kb.node_id);
      }
    });
    if (!orgOf(note.org_kb.org_id)) {
      load().then(function () { box.innerHTML = bannerHtml(note); }).catch(function () {});
    }
  }

  // ---------- доступ к базе знаний для ИИ (Профиль → Аккаунт) ----------

  var ai = { items: [], created: null, command: "", url: "" };

  function aiApi(path, opts) {
    return app().apiFetch("/ai-tokens" + (path || ""), opts || { method: "GET" });
  }

  function aiApply(r) {
    ai.items = (r && r.items) || [];
    ai.command = (r && r.command) || ai.command;
    ai.url = (r && r.mcp_url) || ai.url;
    renderAi();
  }

  function renderAi() {
    var body = document.getElementById("account-ai-body");
    var group = document.getElementById("account-ai-group");
    if (!body || !group) return;
    group.classList.remove("hidden");
    var rows = ai.items.map(function (t) {
      var used = (t.scope === "kb:write" ? "чтение и запись · " : "только чтение · ") +
        (t.last_used_at ? "использовался " + ago(t.last_used_at) : "ещё не использовался");
      return '<div class="pf-row pf-row--static"><span class="pf-ico" aria-hidden="true">' + ICO.key + "</span>" +
        '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(t.name) + "</span>" +
        '<span class="pf-row-sub">' + esc(t.hint + " · " + used) + "</span></span>" +
        '<button type="button" class="pf-del" data-ai="revoke" data-id="' + t.id + '" aria-label="Отозвать" title="Отозвать">' + ICO.x + "</button></span></div>";
    }).join("");
    rows += addRowHtml('data-ai="create"', "Новый токен");
    var html = '<div class="pf-list">' + rows + "</div>";
    if (ai.created) {
      html += '<div class="kb-ai-new">' +
        '<p class="kb-ai-new-title">Токен «' + esc(ai.created.name) + "» — скопируйте сейчас, второй раз его не покажем</p>" +
        '<pre class="kb-ai-code" data-ai="code">' + esc(ai.command) + "</pre>" +
        '<div class="kb-ai-actions"><button type="button" class="pf-btn" data-ai="copy">Скопировать команду</button>' +
        '<button type="button" class="pf-link" data-ai="copy-token">Только токен</button>' +
        '<button type="button" class="pf-link pf-link--muted" data-ai="hide">Готово</button></div>' +
        '<p class="pf-hint">Выполните команду в терминале — Claude Code подключит базу знаний. Для других агентов: адрес ' +
        esc(ai.url) + ", заголовок Authorization: Bearer и токен.</p></div>";
    }
    html += '<p class="pf-hint">Агент видит то же, что вы в базе знаний. С токеном «чтение и запись» — ещё пишет там, где вы редактор. Обычные документы, почта, задачи и сейфы ему недоступны. Ушёл ноутбук или токен попал не туда — отзовите.</p>';
    body.innerHTML = html;
    if (ai.scrollTo) {
      ai.scrollTo = false;
      setTimeout(function () {
        if (group.scrollIntoView) group.scrollIntoView({ block: "start", behavior: "smooth" });
      }, 50);
    }
  }

  async function aiCopy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Скопировано");
    } catch (_) {
      toast("Выделите и скопируйте вручную");
    }
  }

  // Новый токен: название и доступ — только чтение (по умолчанию) или ещё и запись.
  function openAiCreate() {
    var sh = sheet("Новый токен", "");
    sh.body.innerHTML =
      '<div class="pf-group"><div class="pf-list"><label class="pf-field"><span class="pf-field-label">Где будете использовать</span>' +
      '<input type="text" class="pf-input" data-ai-new="name" maxlength="60" value="Claude Code" placeholder="Например, Claude Code на ноутбуке" /></label></div></div>' +
      '<div class="pf-group"><p class="pf-group-title">Доступ</p><div class="pf-list">' +
      '<label class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Только чтение</span>' +
      '<span class="pf-row-sub pf-row-sub--wrap">Искать и читать документы базы знаний</span></span>' +
      '<input type="radio" class="kb-radio" name="ai-scope" value="read" checked /></span></label>' +
      '<label class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Чтение и запись</span>' +
      '<span class="pf-row-sub pf-row-sub--wrap">Ещё создавать разделы и документы и править документы — там, где вы редактор. Удалять и менять доступ агент не может</span></span>' +
      '<input type="radio" class="kb-radio" name="ai-scope" value="write" /></span></label>' +
      '</div><p class="pf-hint">Правки агента сохраняются в истории версий документа с пометкой «ИИ» — любую можно откатить.</p></div>' +
      '<button type="button" class="pf-btn" data-ai-new="go">Создать токен</button>';
    var input = sh.body.querySelector('[data-ai-new="name"]');
    if (input) input.focus();
    sh.body.querySelector('[data-ai-new="go"]').addEventListener("click", function () {
      var name = String((input && input.value) || "").trim();
      var write = !!sh.body.querySelector('input[name="ai-scope"][value="write"]:checked');
      aiApi("", json("POST", { name: name, write: write })).then(function (r) {
        sh.close();
        ai.created = r.created;
        aiApply(r);
      }).catch(function (err) { toast(errText(err)); });
    });
  }

  function onAiClick(e) {
    var b = e.target.closest("[data-ai]");
    if (!b) return;
    var kind = b.getAttribute("data-ai");
    if (kind === "create") {
      openAiCreate();
    } else if (kind === "revoke") {
      var ok = app().confirm ? app().confirm("Отозвать токен? Агенты с ним сразу потеряют доступ.") : Promise.resolve(window.confirm("Отозвать токен?"));
      Promise.resolve(ok).then(function (yes) {
        if (!yes) return;
        aiApi("/" + b.getAttribute("data-id"), { method: "DELETE" }).then(function (r) {
          if (ai.created && String(ai.created.id) === b.getAttribute("data-id")) ai.created = null;
          aiApply(r);
        }).catch(function (err) { toast(errText(err)); });
      });
    } else if (kind === "copy") {
      aiCopy(ai.command);
    } else if (kind === "copy-token") {
      if (ai.created) aiCopy(ai.created.token);
    } else if (kind === "hide") {
      ai.created = null;
      renderAi();
    }
  }

  function loadAi() {
    aiApi().then(function (r) {
      ai.command = "";
      aiApply(r);
    }).catch(function () {});
  }

  // ---------- подключение ----------

  function init() {
    if (!window.LeoApp) {
      setTimeout(init, 200);
      return;
    }
    var panel = document.getElementById("panel-knowledge");
    if (!panel) return;
    panel.addEventListener("click", onClick);
    var search = document.getElementById("knowledge-search-input");
    if (search) {
      search.addEventListener("input", function () {
        state.query = String(search.value || "").trim();
        renderRoot();
      });
    }
    var fab = document.getElementById("knowledge-create-btn");
    if (fab) {
      // раньше обработчика app.js: есть разделы, куда можно писать, — сначала выбор «куда»
      fab.addEventListener("click", function (e) {
        if (state.sectionId != null) {
          var cur = nodeOf(kbOf(state.sectionOrg), state.sectionId);
          if (cur && atLeast(cur.my_role, "editor") && !cur.restricted) {
            e.stopImmediatePropagation();
            newDoc(state.sectionOrg, state.sectionId);
            return;
          }
        }
        if (!editableSections().length) return;
        e.stopImmediatePropagation();
        openCreateChooser();
      }, true);
    }
    // вкладку показали — обновить разделы
    var wasHidden = panel.hidden;
    new MutationObserver(function () {
      if (wasHidden && !panel.hidden) load();
      wasHidden = panel.hidden;
    }).observe(panel, { attributes: true, attributeFilter: ["hidden", "class"] });
    if (!panel.hidden) load();
    var minePane = document.getElementById("knowledge-pane");
    if (minePane) new MutationObserver(compactMineEmpty).observe(minePane, { childList: true });
    document.addEventListener("leo:note-open", onNoteOpen);
    var aiBody = document.getElementById("account-ai-body");
    if (aiBody) aiBody.addEventListener("click", onAiClick);
    var account = document.getElementById("profile-account");
    if (account) {
      // экран «Аккаунт» показали — подтянуть токены (токен целиком не храним: при уходе с экрана прячем)
      new MutationObserver(function () {
        if (account.classList.contains("hidden")) {
          ai.created = null;
        } else {
          loadAi();
        }
      }).observe(account, { attributes: true, attributeFilter: ["class"] });
    }
    // разделы нужны и вне вкладки — для пункта «В базу знаний…» в меню документа
    setTimeout(function () {
      if (!state.loaded) load();
    }, 3000);
    var aiOpen = document.getElementById("knowledge-ai-open");
    var profileAi = document.getElementById("profile-ai-open");
    if (profileAi) {
      profileAi.addEventListener("click", function () {
        ai.scrollTo = true; // прокрутить к токенам, когда они загрузятся (renderAi)
        if (app().openAccount) app().openAccount();
      });
    }
    if (aiOpen) {
      aiOpen.addEventListener("click", function () {
        if (app().setTab) app().setTab("profile");
        ai.scrollTo = true; // прокрутить к токенам, когда они загрузятся (renderAi)
        if (app().openAccount) app().openAccount();
      });
    }
  }

  window.LeoOrgKb = {
    reload: load,
    // для меню «⋯» документа (app.js)
    canPlace: function () {
      return editableSections().length > 0;
    },
    inKb: function (noteId) {
      return !!nodeForNote(noteId);
    },
    place: placeNote,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
