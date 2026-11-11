/* Запасной адрес eu.getorbita.me для пользователей с VPN (2026-11-10).
 *
 * С частью VPN-выходов наш сервер в РФ не отвечает (граница режет ответы VPN-хостингам), а через
 * проходку в Казахстане (eu.getorbita.me → тот же сервер, TLS не расшифровывается) — отвечает.
 *
 * На getorbita.me: если подряд FAIL_LIMIT запросов к /api/ (или подключений WebSocket) падают по сети,
 * проверяем /healthz основного адреса; если он молчит, а eu отвечает — переходим на eu, сохраняя путь.
 * Не чаще одного раза за вкладку (sessionStorage), так что петель нет.
 * На eu: маленькая плашка «Запасной адрес для VPN». На остальных адресах (localhost и т.п.) — ничего.
 * Подключается первым скриптом в index.html, ничего не меняет в app.js.
 * Зеркало этой логики для «зависшей» первой загрузки страницы — в sw.js (vpnFallbackPage).
 */
(function () {
  "use strict";
  var MAIN_HOST = "getorbita.me";
  var EU_HOST = "eu.getorbita.me";
  var EU_ORIGIN = "https://" + EU_HOST;
  var FAIL_LIMIT = 2;
  var PROBE_MS = 5000;
  var API_TIMEOUT_MS = 20000;
  var SWITCHED_KEY = "leo-failover-switched";

  var host = (location.hostname || "").toLowerCase();
  var testMode = /[?&]failover_test=1\b/.test(location.search || "");
  var onMain = host === MAIN_HOST || (testMode && host !== EU_HOST);
  var onEu = host === EU_HOST;
  if (!onMain && !onEu) return;

  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;

  function probe(origin, ms) {
    if (!nativeFetch) return Promise.resolve(false);
    if (testMode && origin === location.origin) return Promise.resolve(false);
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var timer = setTimeout(function () {
      if (ctl) ctl.abort();
    }, ms || PROBE_MS);
    var opts = { cache: "no-store", credentials: "omit" };
    if (origin !== location.origin) opts.mode = "no-cors";
    if (ctl) opts.signal = ctl.signal;
    return nativeFetch(origin + "/healthz?t=" + Date.now(), opts)
      .then(function () {
        return true;
      })
      .catch(function () {
        return false;
      })
      .then(function (ok) {
        clearTimeout(timer);
        return ok;
      });
  }

  function alreadySwitched() {
    try {
      return sessionStorage.getItem(SWITCHED_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  // Миниапп: Telegram кладёт initData в адрес только при первом открытии, на новом адресе
  // его нет — передаём через hash в том же формате, который читает telegram-web-app.js.
  function telegramHash() {
    try {
      var wa = window.Telegram && window.Telegram.WebApp;
      if (!wa || !wa.initData) return "";
      var p = [
        "tgWebAppData=" + encodeURIComponent(wa.initData),
        "tgWebAppVersion=" + encodeURIComponent(wa.version || "6.0"),
        "tgWebAppPlatform=" + encodeURIComponent(wa.platform || "unknown"),
      ];
      if (wa.themeParams) p.push("tgWebAppThemeParams=" + encodeURIComponent(JSON.stringify(wa.themeParams)));
      return "#" + p.join("&");
    } catch (_) {
      return "";
    }
  }

  function goEu() {
    try {
      sessionStorage.setItem(SWITCHED_KEY, "1");
    } catch (_) {}
    var hash = telegramHash() || location.hash || "";
    var target = EU_ORIGIN + location.pathname + (location.search || "") + hash;
    // ?failover_test=1 (локальная проверка): основной адрес «молчит», но никуда не уходим.
    if (testMode) {
      window.__leoFailoverTarget = target;
      console.info("[failover] switch →", target);
      return;
    }
    location.replace(target);
  }

  var fails = 0;
  var checking = false;

  function noteFail() {
    if (!onMain || checking || alreadySwitched()) return;
    fails += 1;
    if (fails < FAIL_LIMIT) return;
    checking = true;
    probe(location.origin).then(function (mainOk) {
      if (mainOk) {
        fails = 0;
        checking = false;
        return;
      }
      return probe(EU_ORIGIN).then(function (euOk) {
        checking = false;
        if (euOk) goEu();
        else fails = 0; // нет сети совсем — ждём, не прыгаем
      });
    });
  }

  function noteOk() {
    fails = 0;
  }

  function isApi(input) {
    try {
      var u = new URL(typeof input === "string" ? input : input && input.url, location.href);
      return u.origin === location.origin && u.pathname.indexOf("/api/") === 0;
    } catch (_) {
      return false;
    }
  }

  if (onMain && nativeFetch) {
    window.fetch = function (input, init) {
      if (!isApi(input)) return nativeFetch(input, init);
      // Зависший запрос (TLS без ответа) — тоже сбой, но сам запрос не обрываем.
      var settled = false;
      var hang = setTimeout(function () {
        if (!settled) noteFail();
      }, API_TIMEOUT_MS);
      return nativeFetch(input, init).then(
        function (res) {
          settled = true;
          clearTimeout(hang);
          noteOk();
          return res;
        },
        function (err) {
          settled = true;
          clearTimeout(hang);
          if (!(err && err.name === "AbortError")) noteFail();
          throw err;
        }
      );
    };
  }

  if (onMain && typeof window.WebSocket === "function") {
    var NativeWS = window.WebSocket;
    var WrappedWS = function (url, protocols) {
      var sock = protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
      var opened = false;
      sock.addEventListener("open", function () {
        opened = true;
        noteOk();
      });
      sock.addEventListener("close", function () {
        if (!opened) noteFail();
      });
      return sock;
    };
    WrappedWS.prototype = NativeWS.prototype;
    ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) {
      WrappedWS[k] = NativeWS[k];
    });
    window.WebSocket = WrappedWS;
  }

  if (onEu) {
    var showBadge = function () {
      if (!document.body || document.getElementById("leo-failover-badge")) return;
      var b = document.createElement("div");
      b.id = "leo-failover-badge";
      b.textContent = "Запасной адрес для VPN";
      b.title = "Основной адрес getorbita.me не отвечает через ваш VPN — Орбита открыта через запасной.";
      b.style.cssText =
        "position:fixed;left:8px;bottom:calc(8px + env(safe-area-inset-bottom,0px));z-index:2147483000;" +
        "font:500 11px/1.2 -apple-system,system-ui,sans-serif;padding:4px 8px;border-radius:8px;" +
        "background:var(--surface-elevated);color:var(--text-secondary);border:1px solid var(--border);" +
        "opacity:.85;pointer-events:none;";
      document.body.appendChild(b);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showBadge);
    else showBadge();
  }

  window.LeoFailover = { probe: probe, onEu: onEu, onMain: onMain };
})();
