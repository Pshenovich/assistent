// Сейфы на основном сайте — только вход на отдельный адрес (vault.getorbita.me, аудит 2026-11-10).
// Сами сейфы, шифрование и Touch ID живут там: XSS основного сайта до них не дотянется.
// Где: строка «Сейфы» в Профиле, под «Базой знаний» (решение владельца 2026-11-10; в базе знаний сейфов нет).
// Открываются в той же вкладке; на странице сейфов «← Орбита» возвращает в Профиль (?open=profile).
// Вход туда — одноразовым кодом: POST /vault-handoff → https://vault…/#c=<код>.
// Ссылки ?open=vaults и старые ?vault=ID (из уведомлений) открывают сейфы так же.
(function () {
  "use strict";

  function app() {
    return window.LeoApp || {};
  }
  function toast(m) {
    if (app().toast) app().toast(m);
  }
  function whenApp(fn) {
    if (app().apiFetch) return setTimeout(fn, 300);
    window.addEventListener("load", function () { setTimeout(fn, 300); });
  }

  var busy = false;
  async function openVaults(vaultId) {
    if (busy) return;
    busy = true;
    try {
      var r = await app().apiFetch("/vault-handoff", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      var u = new URL(r.url);
      if (vaultId) u.searchParams.set("vault", String(vaultId));
      location.assign(u.toString()); // та же вкладка: «назад» в браузере вернёт в Орбиту
    } catch (e) {
      busy = false;
      toast((e && (e.detail || e.message)) || "Не получилось открыть сейфы");
    }
  }

  // ?open=vaults[&vault=ID] (страница сейфов отправила войти), старые ссылки ?vault=ID, ?open=profile («← Орбита»)
  (function fromLink() {
    var p;
    try {
      p = new URL(location.href).searchParams;
    } catch (_) {
      return;
    }
    var open = p.get("open");
    var vid = p.get("vault");
    if (open !== "vaults" && open !== "profile" && !vid) return;
    try {
      var u = new URL(location.href);
      u.searchParams.delete("open");
      u.searchParams.delete("vault");
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    } catch (_) {}
    whenApp(function () {
      if (open === "profile") {
        if (app().setTab) app().setTab("profile");
      } else {
        openVaults(vid && vid !== "link" ? vid : "");
      }
    });
  })();

  // Строка в Профиле: видна, если сейфы включены на сервере
  function bindProfileRow() {
    var row = document.getElementById("profile-vaults-open");
    if (!row || row._bound) return;
    row._bound = true;
    row.addEventListener("click", function () { openVaults(""); });
    whenApp(async function () {
      try {
        var r = await app().apiFetch("/vault-link");
        row.classList.toggle("hidden", !(r && r.enabled));
      } catch (_) {}
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindProfileRow);
  else bindProfileRow();

  // База знаний (org-kb*.js) спрашивает у LeoVault блоки — сейфов там больше нет
  window.LeoVault = {
    load: function () { return Promise.resolve(); },
    enabled: function () { return false; },
    isUnlocked: function () { return false; },
    lock: function () {},
    show: function (id) { openVaults(id || ""); },
    render: function () {},
    treeHtml: function () { return ""; },
    footHtml: function () { return ""; },
    homeHtml: function () { return ""; },
    listBlockHtml: function () { return ""; },
    onClick: function () { return false; },
    open: openVaults,
  };
})();
