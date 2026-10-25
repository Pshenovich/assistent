/* Совместная правка текста заметки (Yjs) поверх сокета webapp/realtime.js.
 * Сервер хранит журнал обновлений и назначает одного «посевщика» (первое открытие:
 * HTML → документ). Документ живёт здесь и пока открыт другой лист — редактор
 * к нему подключается и отключается (attach / detach). */
(function () {
  "use strict";

  var ORIGIN_REMOTE = "leo-remote"; // пришло с сервера — обратно не отправляем
  var ORIGIN_SEED = "leo-seed";
  var ORIGIN_RESEED = "leo-reseed";
  var LEADER_SAVE_MS = 1200;

  function b64encode(u8) {
    var s = "";
    for (var i = 0; i < u8.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }

  function b64decode(str) {
    var bin = atob(String(str || ""));
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  /** Документ основного листа — редактор TipTap (y-prosemirror). */
  function editorAdapter(o) {
    function editor() {
      try {
        return o.getEditor ? o.getEditor() : null;
      } catch (_) {
        return null;
      }
    }
    function anyEditor() {
      return (o.getAnyEditor ? o.getAnyEditor() : null) || editor();
    }
    return {
      hasContent: function (doc) {
        return doc.getXmlFragment("prosemirror").length > 0;
      },
      canSave: function () {
        var ed = editor();
        return !!(ed && ed.isYjsAttached());
      },
      attach: function (doc, ready, waiting) {
        var ed = editor();
        if (!ed || typeof ed.attachYjs !== "function") return;
        if (!ready) {
          if (waiting && typeof ed.setEditable === "function") ed.setEditable(false);
          return;
        }
        if (!ed.isYjsAttached()) ed.attachYjs(doc);
        if (typeof ed.setEditable === "function") ed.setEditable(true);
      },
      detach: function () {
        var ed = anyEditor();
        if (ed && typeof ed.detachYjs === "function") ed.detachYjs();
      },
      seed: function (doc, html, origin) {
        var ed = anyEditor();
        if (!ed || typeof ed.applyHtmlToYjs !== "function") return false;
        ed.applyHtmlToYjs(doc, html, origin);
        return true;
      },
      reseed: function (doc, oldHtml, html, origin) {
        var ed = anyEditor();
        if (!ed || typeof ed.applyHtmlDeltaToYjs !== "function") return false;
        ed.applyHtmlDeltaToYjs(doc, oldHtml, html, origin);
        return true;
      },
    };
  }

  /**
   * o.noteId, o.sheetId (нет — основной лист; id холста — его документ), o.adapter (как
   * editorAdapter: документ холста — webapp/board.js), o.Y (yjs из бандла редактора), o.send(obj) → bool,
   * o.getEditor() — редактор основного листа (или null, если открыт другой лист),
   * o.getAnyEditor() — любой редактор (нужна схема для посева),
   * o.onReady(), o.onReseeded() — HTML документа обновлён правкой мимо Yjs (сохранить),
   * o.onRemoteChange() — ведущему: документ изменили другие, сохранить HTML (чтобы не отставал),
   * o.onFallback(reason) — совместная правка недоступна, вернуться к обычному режиму.
   */
  function createSession(o) {
    var Y = o.Y;
    var noteId = Number(o.noteId);
    var sheetKey = o.sheetId != null && String(o.sheetId) !== "" && String(o.sheetId) !== "main" ? String(o.sheetId) : "main";
    var ad = o.adapter || editorAdapter(o);
    var doc = new Y.Doc();
    var ready = false;
    var waiting = false;
    var stopped = false;
    var nextSeq = 0; // все обновления с seq < nextSeq у нас есть
    var offlineDirty = false; // правили, пока сокета не было — после входа отправим всё состояние
    var leader = false; // сервер назначил нас сохранять HTML и после чужих правок
    var leaderTimer = null;

    doc.on("update", function (update, origin) {
      if (stopped) return;
      if (origin === ORIGIN_REMOTE) {
        if (ready && leader) scheduleLeaderSave();
        return;
      }
      if (!o.send(msg("y.update", { update: b64encode(update) }))) offlineDirty = true;
    });

    function msg(type, extra) {
      var m = { type: type, note_id: noteId };
      if (sheetKey !== "main") m.sheet_id = sheetKey;
      if (extra) {
        for (var k in extra) m[k] = extra[k];
      }
      return m;
    }

    function scheduleLeaderSave() {
      if (leaderTimer) clearTimeout(leaderTimer);
      leaderTimer = setTimeout(function () {
        leaderTimer = null;
        // Сохраняем только с подключённым редактором: HTML берётся из него.
        if (stopped || !ad.canSave()) return;
        if (typeof o.onRemoteChange === "function") o.onRemoteChange();
      }, LEADER_SAVE_MS);
    }

    function hasContent() {
      return ad.hasContent(doc);
    }

    function attach() {
      if (stopped) return;
      ad.attach(doc, ready, waiting);
    }

    function detach() {
      ad.detach();
    }

    function becomeReady() {
      var first = !ready;
      ready = true;
      waiting = false;
      attach();
      if (first && typeof o.onReady === "function") o.onReady();
    }

    function sendFullState() {
      o.send(msg("y.update", { update: b64encode(Y.encodeStateAsUpdate(doc)) }));
      offlineDirty = false;
    }

    function seedFrom(html) {
      if (!ad.seed(doc, html, ORIGIN_SEED)) fallback("no_editor");
    }

    function fallback(reason) {
      if (stopped) return;
      stop();
      if (typeof o.onFallback === "function") o.onFallback(reason);
    }

    function handle(m) {
      if (stopped || !m || Number(m.note_id) !== noteId) return;
      if ((m.sheet_id != null && String(m.sheet_id) !== "" ? String(m.sheet_id) : "main") !== sheetKey) return;
      handleOwn(m);
    }

    function handleOwn(msg) {
      var t = msg.type;
      if (t === "y.state") {
        nextSeq = Number(msg.seq) || 0;
        if (msg.role === "sync") {
          var had = hasContent();
          (msg.updates || []).forEach(function (u) {
            Y.applyUpdate(doc, b64decode(u), ORIGIN_REMOTE);
          });
          // Переподключились с правками, которых сервер мог не видеть, — отдать всё (Yjs не задвоит).
          if (had || offlineDirty) sendFullState();
          becomeReady();
        } else if (msg.role === "seed") {
          if (hasContent()) sendFullState(); // журнал на сервере пропал, а документ у нас есть
          else seedFrom(msg.html || "");
          becomeReady();
        } else {
          waiting = true; // посев делает другой участник — ждём его первого обновления
          attach();
        }
        return;
      }
      if (t === "y.update") {
        Y.applyUpdate(doc, b64decode(msg.update), ORIGIN_REMOTE);
        nextSeq = Math.max(nextSeq, Number(msg.seq) + 1);
        if (!ready && hasContent()) becomeReady();
        return;
      }
      if (t === "y.ack") {
        nextSeq = Math.max(nextSeq, Number(msg.seq) + 1);
        return;
      }
      if (t === "y.compact_request") {
        o.send(msg("y.compact", { update: b64encode(Y.encodeStateAsUpdate(doc)), upto: nextSeq }));
        return;
      }
      if (t === "y.leader") {
        leader = !!msg.leader;
        if (leader && ready) scheduleLeaderSave(); // догнать HTML после чужих правок
        return;
      }
      if (t === "y.reseed") {
        // HTML изменили мимо Yjs (ИИ, бот, старое приложение): вливаем только их правку
        // (было → стало) — не затирая того, что ещё не попало в HTML.
        if (!ready || msg.old_html == null) return;
        if (ad.reseed(doc, msg.old_html, msg.html || "", ORIGIN_RESEED) && typeof o.onReseeded === "function") {
          o.onReseeded();
        }
        return;
      }
      if (t === "y.error") {
        fallback(String(msg.reason || "error"));
      }
    }

    function join() {
      if (stopped) return;
      o.send(msg("y.join"));
    }

    function stop() {
      if (stopped) return;
      stopped = true;
      if (leaderTimer) clearTimeout(leaderTimer);
      detach();
      o.send(msg("y.leave"));
      try {
        doc.destroy();
      } catch (_) {}
    }

    return {
      noteId: noteId,
      sheetId: sheetKey,
      join: join,
      handle: handle,
      attach: attach,
      detach: detach,
      stop: stop,
      isReady: function () {
        return ready && !stopped;
      },
    };
  }

  window.LeoNoteYjs = { createSession: createSession };
})();
