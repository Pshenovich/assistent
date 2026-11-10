// Сейфы на основном сайте — только ссылка на отдельный адрес (vault.getorbita.me, аудит 2026-11-10).
// Сами сейфы, шифрование и Touch ID живут там: XSS основного сайта до них не дотянется.
// Вход туда — одноразовым кодом: POST /vault-handoff → https://vault…/#c=<код>.
// Ссылки ?open=vaults и старые ?vault=ID (из уведомлений) открывают сейфы так же.
(function () {
  "use strict";

  var IC_LOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';
  var IC_OUT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>';
  var enabled = true; // выключенные сейфы: handoff вернёт 404 — скажем об этом при нажатии

  function app() {
    return window.LeoApp || {};
  }
  function toast(m) {
    if (app().toast) app().toast(m);
  }

  // Окно открываем сразу по нажатию (иначе Safari его заблокирует), адрес — когда придёт код.
  async function openVaults(vaultId, sameTab) {
    var w = sameTab ? null : window.open("", "_blank");
    try {
      var r = await app().apiFetch("/vault-handoff", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      var u = new URL(r.url);
      if (vaultId) u.searchParams.set("vault", String(vaultId));
      if (w) {
        try { w.opener = null; } catch (_) {}
        w.location.href = u.toString();
      } else {
        location.replace(u.toString());
      }
    } catch (e) {
      if (w) w.close();
      if (e && (e.status === 404 || /недоступны/.test(String(e.message || e.detail || "")))) enabled = false;
      toast((e && (e.detail || e.message)) || "Не получилось открыть сейфы");
    }
  }

  // ?open=vaults[&vault=ID] (страница сейфов отправила войти) и старые ссылки ?vault=ID
  (function fromLink() {
    var p;
    try {
      p = new URL(location.href).searchParams;
    } catch (_) {
      return;
    }
    var vid = p.get("vault");
    if (p.get("open") !== "vaults" && !vid) return;
    try {
      var u = new URL(location.href);
      u.searchParams.delete("open");
      u.searchParams.delete("vault");
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    } catch (_) {}
    var go = function () { openVaults(vid && vid !== "link" ? vid : "", true); };
    if (app().apiFetch) setTimeout(go, 300);
    else window.addEventListener("load", function () { setTimeout(go, 300); });
  })();

  function rowHtml() {
    return '<div class="vlt-tree"><div class="kb-node vlt-node vlt-open-row" role="treeitem" tabindex="-1" data-v="vault-open">' +
      '<span class="kb-chev kb-chev--none" aria-hidden="true"></span><span class="kb-n-ico" aria-hidden="true">' + IC_LOCK + "</span>" +
      '<span class="kb-n-title">Сейфы</span><span class="kb-n-count" aria-hidden="true">' + IC_OUT + "</span></div></div>";
  }

  window.LeoVault = {
    load: function () { return Promise.resolve(); },
    enabled: function () { return enabled; },
    isUnlocked: function () { return false; },
    lock: function () {},
    show: function (id) { openVaults(id || ""); },
    render: function () {},
    treeHtml: function (orgId, cur) { return enabled && orgId === firstOrgId() ? rowHtml() : ""; },
    footHtml: function () { return ""; },
    homeHtml: function (orgId) {
      if (!enabled || orgId !== firstOrgId()) return "";
      return '<p class="kb-label">Сейфы<span class="kb-label-note">пароли и ключи — шифруются на ваших устройствах, Лео их не видит</span></p>' +
        '<div class="kb-cards"><button type="button" class="kb-card" data-v="vault-open"><span class="kb-card-top"><span class="kb-card-ico" aria-hidden="true">' + IC_LOCK +
        '</span><b>Открыть сейфы</b></span><span class="kb-card-meta">на отдельном защищённом адресе — откроется в новой вкладке</span></button></div>';
    },
    listBlockHtml: function () {
      if (!enabled) return "";
      return '<div class="pf-group kb-org-group vlt-list-block"><div class="pf-list"><button type="button" class="pf-row" data-v="vault-open">' +
        '<span class="pf-ico kb-ico" aria-hidden="true">' + IC_LOCK + '</span><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Сейфы</span>' +
        '<span class="pf-row-sub">пароли и ключи — на отдельном защищённом адресе</span></span><span class="pf-chev" aria-hidden="true">›</span></span></button></div></div>';
    },
    onClick: function (e) {
      var b = e.target.closest("[data-v]");
      if (!b) return false;
      if (b.getAttribute("data-v") !== "vault-open") return false;
      e.preventDefault();
      openVaults("");
      return true;
    },
  };

  // Строка «Сейфы» — один раз, под первой организацией дерева
  function firstOrgId() {
    var k = window.LeoOrgKb && window.LeoOrgKb._;
    var orgs = (k && k.state && k.state.orgs) || [];
    return orgs.length ? orgs[0].org.id : null;
  }
})();
