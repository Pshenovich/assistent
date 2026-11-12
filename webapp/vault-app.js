// Каркас страницы сейфов на отдельном адресе (vault.getorbita.me): вход по одноразовому коду из Орбиты,
// список сейфов слева, сам сейф справа (vault.js). Заменяет vault.js то, что на основном сайте давали
// app.js и база знаний: запросы к API, сообщения, окно-«шторку», меню.
//
// Строгий CSP: Trusted Types — разметку вставляем только через политику "vault-html". Строки собирают
// vault.js и этот файл, всё пользовательское экранируется (esc).
(function () {
  "use strict";

  var policy = null;
  try {
    if (window.trustedTypes && window.trustedTypes.createPolicy) {
      policy = window.trustedTypes.createPolicy("vault-html", { createHTML: function (s) { return s; } });
    }
  } catch (_) {}
  function html(s) {
    return policy ? policy.createHTML(s) : s;
  }
  function setHTML(el, s) {
    el.innerHTML = html(s);
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  var mainMeta = document.querySelector('meta[name="orbita-main"]');
  var MAIN = (mainMeta && mainMeta.getAttribute("content")) || "https://getorbita.me";

  // ---------- вход: #c=<код> из Орбиты → своя сессия ----------

  var ready = (async function () {
    var m = /(?:^#|&)c=([^&]+)/.exec(location.hash || "");
    if (!m) return;
    var code = decodeURIComponent(m[1]);
    try {
      history.replaceState(null, "", location.pathname + location.search); // код — один раз, из адресной строки убираем
    } catch (_) {}
    try {
      var r = await fetch("/api/miniapp/vault-session", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code }),
      });
      if (!r.ok) {
        var d = await r.json().catch(function () { return {}; });
        loginText(d.detail || "Ссылка входа устарела — войдите ещё раз.");
      }
    } catch (_) {}
  })();

  function loginUrl() {
    var vid = new URLSearchParams(location.search).get("vault");
    return MAIN + "/webapp/?open=vaults" + (vid ? "&vault=" + encodeURIComponent(vid) : "");
  }
  function loginText(t) {
    var el = document.getElementById("vh-login-text");
    if (el && t) el.textContent = t;
  }
  function showLogin(on) {
    var box = document.getElementById("vh-login");
    var btn = document.getElementById("vh-login-btn");
    if (btn) btn.setAttribute("href", loginUrl());
    if (box) box.classList.toggle("hidden", !on);
    document.querySelector(".vh-app").classList.toggle("hidden", !!on);
  }

  async function apiFetch(path, opts) {
    await ready;
    opts = opts || {};
    var r = await fetch("/api/miniapp" + path, {
      method: opts.method || "GET",
      headers: opts.headers || {},
      body: opts.body,
      credentials: "same-origin",
      cache: "no-store",
    });
    var data = await r.json().catch(function () { return {}; });
    if (r.status === 401) {
      showLogin(true);
    }
    if (!r.ok) {
      var e = new Error((data && data.detail) || "Не получилось — попробуйте ещё раз");
      e.detail = data && data.detail;
      e.status = r.status;
      throw e;
    }
    return data;
  }

  // Шифр файла из сейфа — байтами (apiFetch разбирает JSON)
  async function apiBytes(path) {
    await ready;
    var r = await fetch("/api/miniapp" + path, { credentials: "same-origin", cache: "no-store" });
    if (r.status === 401) showLogin(true);
    if (!r.ok) {
      var data = await r.json().catch(function () { return {}; });
      var e = new Error((data && data.detail) || "Не получилось — попробуйте ещё раз");
      e.detail = data && data.detail;
      e.status = r.status;
      throw e;
    }
    return new Uint8Array(await r.arrayBuffer());
  }

  // ---------- сообщения, вопросы ----------

  var toastTimer = null;
  function toast(msg) {
    var el = document.getElementById("vh-toast");
    if (!el) return;
    el.textContent = String(msg || "");
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.add("hidden"); }, 4000);
  }

  window.LeoApp = {
    apiFetch: apiFetch,
    apiBytes: apiBytes,
    toast: toast,
    confirm: function (msg) { return Promise.resolve(window.confirm(msg)); },
    promptDialog: function (title, value) { return Promise.resolve(window.prompt(title, value || "")); },
    openLink: function (url) {
      if (/^https?:\/\//i.test(String(url || ""))) window.open(url, "_blank", "noopener,noreferrer");
    },
    setTab: function () {},
    syncAppOverlay: function () {},
  };
  window.LeoVaultHost = { html: html, ready: ready };

  // ---------- организации: участники и группы — для окна «Доступ» ----------

  var orgs = [];
  function kbOf(orgId) {
    for (var i = 0; i < orgs.length; i++) if (orgs[i].org.id === orgId) return orgs[i].kb;
    return null;
  }
  function initials(name) {
    var w = String(name || "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
    return ((w[0] || "?").charAt(0) + (w[1] || "").charAt(0)).toUpperCase();
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
    if (p.indexOf("u:") === 0) return "";
    var id = String(p).slice(2), sub = "";
    (kb.groups || []).forEach(function (g) {
      if (String(g.id) === id) sub = "отдел";
      (g.teams || []).forEach(function (t) { if (String(t.id) === id) sub = "команда"; });
    });
    return sub;
  }
  function sheet(title, bodyHtml) {
    var ov = document.createElement("div");
    ov.className = "modal-overlay kb-sheet-overlay";
    setHTML(ov, '<div class="modal modal--sheet kb-sheet" role="dialog" aria-modal="true">' +
      '<div class="modal-form"><div class="modal-header">' +
      '<button type="button" class="modal-header-btn modal-header-btn--close" data-kb-close aria-label="Закрыть" title="Закрыть">' +
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button>' +
      '<h2 class="modal-title">' + esc(title) + "</h2>" +
      '<span class="modal-header-btn modal-header-btn--placeholder" aria-hidden="true"></span></div>' +
      '<div class="modal-body"><div class="pf-screen-body kb-sheet-body">' + (bodyHtml || "") + "</div></div></div></div>");
    document.body.appendChild(ov);
    function close() {
      ov.remove();
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) {
      if (e.key === "Escape") close();
    }
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.closest("[data-kb-close]")) close();
    });
    document.addEventListener("keydown", onKey);
    return { el: ov, body: ov.querySelector(".kb-sheet-body"), close: close };
  }
  window.LeoOrgKb = {
    renderVaults: function () { renderSide(); },
    _: { state: { orgs: orgs }, kbOf: kbOf, initials: initials, principalName: principalName, principalSub: principalSub, sheet: sheet },
  };

  // ---------- раскладка: слева список, справа сейф ----------

  var current = null;
  function V() {
    return window.LeoVault;
  }
  function renderSide() {
    var side = document.getElementById("vh-side");
    if (!side || !V()) return;
    var h = '<a class="vh-back" href="' + esc(MAIN + "/webapp/?open=profile") + '"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg><span>Орбита</span></a>' +
      '<div class="vh-brand"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>' +
      "<span>Сейфы</span></div>";
    orgs.forEach(function (o) {
      h += '<div class="vh-org"><span>' + esc(o.org.name) + '</span><button type="button" class="kb-n-btn" data-v="new-vault" data-org="' + o.org.id +
        '" aria-label="Новый сейф" title="Новый сейф"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button></div>' +
        V().treeHtml(o.org.id, current);
    });
    h += '<div class="vh-foot">' + V().footHtml(current) +
      '<button type="button" class="kb-foot-row" data-vh="logout"><span>Выйти из сейфов</span></button></div>';
    setHTML(side, h);
  }
  function renderMain() {
    var el = document.getElementById("kb-desk");
    if (el && V()) V().render(el);
  }
  function renderAll() {
    renderSide();
    renderMain();
  }

  var menuEl = null;
  function closeMenu() {
    if (menuEl) menuEl.remove();
    menuEl = null;
  }
  window.LeoOrgKbDesk = {
    isOn: function () { return true; },
    inVault: function () { return true; },
    treeHidden: function () { return false; },
    showVault: function (id) {
      current = id == null ? null : Number(id);
      renderAll();
    },
    render: renderAll,
    goHome: function () {
      current = null;
      if (V()) V().show(null);
    },
    openMenu: function (anchor, items) {
      closeMenu();
      menuEl = document.createElement("div");
      menuEl.className = "kb-menu vh-menu";
      setHTML(menuEl, items.map(function (it, i) {
        return '<button type="button" class="kb-menu-item' + (it.danger ? " is-danger" : "") + '" data-vh-menu="' + i + '">' + esc(it.label) + "</button>";
      }).join(""));
      var r = anchor.getBoundingClientRect();
      menuEl.style.top = Math.round(r.bottom + 4) + "px";
      menuEl.style.right = Math.round(window.innerWidth - r.right) + "px";
      menuEl.addEventListener("click", function (e) {
        var b = e.target.closest("[data-vh-menu]");
        if (!b) return;
        var it = items[Number(b.getAttribute("data-vh-menu"))];
        closeMenu();
        if (it && it.run) it.run();
      });
      document.body.appendChild(menuEl);
    },
  };

  document.addEventListener("click", function (e) {
    if (menuEl && !menuEl.contains(e.target)) closeMenu();
    var own = e.target.closest("[data-vh]");
    if (own) {
      var a = own.getAttribute("data-vh");
      if (a === "main") location.href = MAIN + "/webapp/";
      if (a === "logout") {
        if (V()) V().lock(true);
        fetch("/api/miniapp/vault-session/logout", { method: "POST", credentials: "same-origin" }).finally(function () {
          loginText("Вы вышли из сейфов.");
          showLogin(true);
        });
      }
      return;
    }
    if (e.target.closest("[data-v]") && V() && !e.target.closest(".kb-sheet")) V().onClick(e);
  });

  async function loadOrgs() {
    try {
      var r = await apiFetch("/vaults/orgs");
      orgs.length = 0;
      (r.orgs || []).forEach(function (o) { orgs.push(o); });
    } catch (_) {}
  }

  async function start() {
    await ready;
    await loadOrgs();
    if (!V()) return;
    await V().load(true);
    if (document.getElementById("vh-login").classList.contains("hidden")) {
      if (current == null) V().show(null);
      renderAll();
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
