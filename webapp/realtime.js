/* Живые обновления: один WebSocket на вкладку (/api/miniapp/ws).
 * Сокет несёт только сигналы «изменилось» и присутствие — данные приложение
 * берёт своими обычными запросами. Если сокета нет, работает прежний опрос. */
(function () {
  "use strict";

  var PATH = "/api/miniapp/ws";
  var PING_MS = 25000;
  var HIDDEN_CLOSE_MS = 30000;
  var BACKOFF_MS = [1000, 2000, 5000, 10000, 20000, 30000];
  var AUTH_FAIL_RETRY_MS = 60000;

  var opts = null;
  var ws = null;
  var state = "idle"; // idle | connecting | open
  var userId = "";
  var attempt = 0;
  var retryTimer = null;
  var pingTimer = null;
  var hiddenTimer = null;
  var wanted = {}; // note_id → true (подписки, которые держим и после переподключения)
  var live = {}; // note_id → true (сервер подтвердил подписку)

  function emit(ev) {
    if (!opts || typeof opts.onEvent !== "function") return;
    try {
      opts.onEvent(ev);
    } catch (e) {
      if (window.console) console.warn("[realtime] handler", e);
    }
  }

  function send(obj) {
    if (!ws || state !== "open") return false;
    try {
      ws.send(JSON.stringify(obj));
      return true;
    } catch (_) {
      return false;
    }
  }

  function clearTimers() {
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = null;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
  }

  function scheduleReconnect(delay) {
    if (retryTimer || !opts || document.hidden) return;
    var ms = delay != null ? delay : BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    retryTimer = setTimeout(function () {
      retryTimer = null;
      connect();
    }, ms + Math.floor(Math.random() * 400));
  }

  function dropLive() {
    var had = Object.keys(live);
    live = {};
    had.forEach(function (id) {
      emit({ type: "unsubscribed", note_id: Number(id) });
    });
  }

  function connect() {
    if (!opts || ws || document.hidden) return;
    if (typeof WebSocket === "undefined") return;
    // Без заголовков сервер проверит session-cookie из рукопожатия.
    var headers = (opts.getHeaders && opts.getHeaders()) || {};
    var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + PATH;
    var sock;
    try {
      sock = new WebSocket(url);
    } catch (_) {
      scheduleReconnect();
      return;
    }
    ws = sock;
    state = "connecting";
    sock.onopen = function () {
      if (ws !== sock) return;
      sock.send(
        JSON.stringify({
          type: "auth",
          headers: {
            Authorization: headers.Authorization || "",
            "X-Telegram-Init-Data": headers["X-Telegram-Init-Data"] || "",
          },
        })
      );
    };
    sock.onmessage = function (e) {
      if (ws !== sock) return;
      var msg;
      try {
        msg = JSON.parse(e.data);
      } catch (_) {
        return;
      }
      if (!msg || typeof msg.type !== "string") return;
      if (msg.type === "hello") {
        state = "open";
        attempt = 0;
        userId = String(msg.user_id || "");
        pingTimer = setInterval(function () {
          send({ type: "ping" });
        }, PING_MS);
        Object.keys(wanted).forEach(function (id) {
          send({ type: "sub", note_id: Number(id) });
        });
        emit({ type: "open", user_id: userId });
        return;
      }
      if (msg.type === "pong") return;
      if (msg.type === "subscribed") live[String(msg.note_id)] = true;
      if (msg.type === "sub_denied") delete live[String(msg.note_id)];
      emit(msg);
    };
    sock.onclose = function (e) {
      if (ws !== sock) return;
      ws = null;
      state = "idle";
      clearTimers();
      dropLive();
      emit({ type: "close", code: e && e.code });
      var authFail = e && (e.code === 4401 || e.code === 4403);
      scheduleReconnect(authFail ? AUTH_FAIL_RETRY_MS : null);
    };
    sock.onerror = function () {
      /* onclose придёт следом */
    };
  }

  function disconnect() {
    clearTimers();
    var sock = ws;
    ws = null;
    state = "idle";
    dropLive();
    if (sock) {
      try {
        sock.close(1000);
      } catch (_) {}
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (!opts) return;
    if (document.hidden) {
      // Свёрнутое приложение не держит сокет: телефон всё равно его усыпит.
      if (!hiddenTimer) {
        hiddenTimer = setTimeout(function () {
          hiddenTimer = null;
          if (document.hidden) disconnect();
        }, HIDDEN_CLOSE_MS);
      }
      return;
    }
    if (hiddenTimer) {
      clearTimeout(hiddenTimer);
      hiddenTimer = null;
    }
    attempt = 0;
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    connect();
  });
  window.addEventListener("online", function () {
    if (!opts || ws) return;
    attempt = 0;
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    connect();
  });

  window.LeoRealtime = {
    start: function (o) {
      opts = o || {};
      connect();
    },
    /** Переподключиться с новой авторизацией (вход / смена сессии). */
    restart: function () {
      disconnect();
      attempt = 0;
      connect();
    },
    sub: function (noteId) {
      var id = String(noteId || "");
      if (!/^\d+$/.test(id)) return;
      wanted[id] = true;
      send({ type: "sub", note_id: Number(id) });
    },
    unsub: function (noteId) {
      var id = String(noteId || "");
      if (!wanted[id]) return;
      delete wanted[id];
      delete live[id];
      send({ type: "unsub", note_id: Number(id) });
    },
    /** Курсор / присутствие в открытой заметке (сервер рассылает остальным). */
    cursor: function (noteId, pos) {
      var id = String(noteId || "");
      if (!live[id]) return false;
      return send({ type: "cursor", note_id: Number(id), cursor: typeof pos === "number" ? pos : null });
    },
    /** Сообщение совместной правки (y.*) — только по подтверждённой подписке. */
    send: function (obj) {
      if (!obj || !live[String(obj.note_id || "")]) return false;
      return send(obj);
    },
    isLive: function (noteId) {
      return state === "open" && !!live[String(noteId || "")];
    },
    isOpen: function () {
      return state === "open";
    },
    userId: function () {
      return userId;
    },
  };
})();
