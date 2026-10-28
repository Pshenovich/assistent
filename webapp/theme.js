/* Тема Орбиты: светлая / тёмная / системная.
   Подключается синхронно в <head> до стилей — тема ставится до первой отрисовки, без мигания.
   «Системная»: в миниаппе — тема Telegram, в браузере и PWA — prefers-color-scheme.
   Выбор хранится на сервере (GET /me → ui_theme, PUT /profile/theme); localStorage — кэш для старта. */
(function () {
  "use strict";

  var STORE_KEY = "orbitaTheme";
  var PREFS = ["system", "light", "dark"];
  // Пока выбор темы в пробном режиме, без сохранённого выбора — тёмная, как раньше.
  var DEFAULT_PREF = "dark";
  // Цвета фона до загрузки стилей (совпадают с --bg каждой темы).
  var BG = { dark: "#0a0a0a", light: "#ebedf0" };

  var root = document.documentElement;
  var listeners = [];
  var media = null;
  try {
    media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  } catch (_) {}

  function readStored() {
    try {
      var v = localStorage.getItem(STORE_KEY);
      return PREFS.indexOf(v) >= 0 ? v : null;
    } catch (_) {
      return null;
    }
  }

  function writeStored(v) {
    try {
      if (v) localStorage.setItem(STORE_KEY, v);
      else localStorage.removeItem(STORE_KEY);
    } catch (_) {}
  }

  var pref = readStored() || DEFAULT_PREF;
  var resolved = null;

  function tgApp() {
    var tg = window.Telegram && window.Telegram.WebApp;
    return tg && tg.initData ? tg : null;
  }

  function isColorDark(hex) {
    var h = String(hex || "").replace(/[\s#]/g, "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length < 6) return null;
    var r = parseInt(h.substr(0, 2), 16);
    var g = parseInt(h.substr(2, 2), 16);
    var b = parseInt(h.substr(4, 2), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return null;
    return Math.sqrt(0.299 * r * r + 0.587 * g * g + 0.114 * b * b) < 120;
  }

  // Тема Telegram до загрузки telegram-web-app.js: из адреса при запуске или из sessionStorage.
  function earlyTelegramScheme() {
    var bg = null;
    try {
      var m = /[#&]tgWebAppThemeParams=([^&]*)/.exec(location.hash || "");
      if (m) bg = (JSON.parse(decodeURIComponent(m[1])) || {}).bg_color;
    } catch (_) {}
    if (!bg) {
      try {
        bg = (JSON.parse(sessionStorage.getItem("__telegram__themeParams")) || {}).bg_color;
      } catch (_) {}
    }
    var dark = bg ? isColorDark(bg) : null;
    return dark === null ? null : dark ? "dark" : "light";
  }

  function systemScheme() {
    var tg = tgApp();
    if (tg && (tg.colorScheme === "light" || tg.colorScheme === "dark")) return tg.colorScheme;
    if (!window.Telegram) {
      var early = earlyTelegramScheme();
      if (early) return early;
    }
    return media && media.matches ? "light" : "dark";
  }

  function resolve(p) {
    return p === "light" || p === "dark" ? p : systemScheme();
  }

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(root).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function setMetaThemeColor(color) {
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    if (!metas.length && document.head) {
      var m = document.createElement("meta");
      m.name = "theme-color";
      document.head.appendChild(m);
      metas = [m];
    }
    for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", color);
  }

  // Шапка, фон и нижняя панель Telegram — в цвет текущей темы.
  function syncTelegram() {
    var tg = tgApp();
    var pageBg = cssVar("--bg", BG[resolved]);
    var surfaceBg = cssVar("--surface", pageBg);
    setMetaThemeColor(pageBg);
    if (!tg) return;
    [
      ["setBackgroundColor", pageBg],
      ["setHeaderColor", pageBg],
      ["setBottomBarColor", surfaceBg],
    ].forEach(function (pair) {
      try {
        if (typeof tg[pair[0]] === "function") tg[pair[0]](pair[1]);
      } catch (_) {}
    });
  }

  function apply(animate) {
    var next = resolve(pref);
    var changed = next !== resolved;
    resolved = next;
    if (changed && animate && document.body) {
      root.classList.add("theme-switching");
      setTimeout(function () {
        root.classList.remove("theme-switching");
      }, 260);
    }
    root.setAttribute("data-theme", next);
    root.classList.toggle("tg-dark", next === "dark");
    root.classList.toggle("tg-light", next === "light");
    root.classList.toggle("vkui--vkIOS--dark", next === "dark");
    root.classList.toggle("vkui--vkIOS--light", next === "light");
    if (document.body) syncTelegram();
    else setMetaThemeColor(BG[next]);
    if (changed) {
      listeners.slice().forEach(function (fn) {
        try {
          fn(next, pref);
        } catch (_) {}
      });
    }
  }

  function onSystemChange() {
    if (pref === "system") apply(true);
  }

  if (media) {
    if (media.addEventListener) media.addEventListener("change", onSystemChange);
    else if (media.addListener) media.addListener(onSystemChange);
  }

  function bindTelegram() {
    var tg = tgApp();
    if (tg && typeof tg.onEvent === "function") {
      try {
        tg.onEvent("themeChanged", onSystemChange);
      } catch (_) {}
    }
    apply(false);
  }

  apply(false);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindTelegram);
  else bindTelegram();

  window.OrbitaTheme = {
    PREFS: PREFS.slice(),
    pref: function () {
      return pref;
    },
    resolved: function () {
      return resolved;
    },
    /** Выбрать тему. opts.remember=false — не трогать кэш (выбор недоступен пользователю). */
    set: function (next, opts) {
      if (PREFS.indexOf(next) < 0) return;
      var remember = !opts || opts.remember !== false;
      writeStored(remember ? next : null);
      if (next === pref) return;
      pref = next;
      apply(!opts || opts.animate !== false);
    },
    syncTelegram: syncTelegram,
    onChange: function (fn) {
      if (typeof fn === "function") listeners.push(fn);
    },
  };
})();
