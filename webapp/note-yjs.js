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

  /**
   * o.noteId, o.Y (yjs из бандла редактора), o.send(obj) → bool,
   * o.getEditor() — редактор основного листа (или null, если открыт другой лист),
   * o.getAnyEditor() — любой редактор (нужна схема для посева),
   * o.onReady(), o.onReseeded() — HTML документа обновлён правкой мимо Yjs (сохранить),
   * o.onRemoteChange() — ведущему: документ изменили другие, сохранить HTML (чтобы не отставал),
   * o.onFallback(reason) — совместная правка недоступна, вернуться к обычному режиму.
   */
  function createSession(o) {
    var Y = o.Y;
    var noteId = Number(o.noteId);
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
      if (!o.send({ type: "y.update", note_id: noteId, update: b64encode(update) })) offlineDirty = true;
    });

    function scheduleLeaderSave() {
      if (leaderTimer) clearTimeout(leaderTimer);
      leaderTimer = setTimeout(function () {
        leaderTimer = null;
        // Сохраняем только с подключённым редактором: HTML берётся из него.
        var ed = editor();
        if (stopped || !ed || !ed.isYjsAttached()) return;
        if (typeof o.onRemoteChange === "function") o.onRemoteChange();
      }, LEADER_SAVE_MS);
    }

    function editor() {
      try {
        return o.getEditor ? o.getEditor() : null;
      } catch (_) {
        return null;
      }
    }

    function hasContent() {
      return doc.getXmlFragment("prosemirror").length > 0;
    }

    function attach() {
      var ed = editor();
      if (!ed || stopped || typeof ed.attachYjs !== "function") return;
      if (!ready) {
        if (waiting && typeof ed.setEditable === "function") ed.setEditable(false);
        return;
      }
      if (!ed.isYjsAttached()) ed.attachYjs(doc);
      if (typeof ed.setEditable === "function") ed.setEditable(true);
    }

    function detach() {
      var ed = editor() || (o.getAnyEditor ? o.getAnyEditor() : null);
      if (ed && typeof ed.detachYjs === "function") ed.detachYjs();
    }

    function becomeReady() {
      var first = !ready;
      ready = true;
      waiting = false;
      attach();
      if (first && typeof o.onReady === "function") o.onReady();
    }

    function sendFullState() {
      o.send({ type: "y.update", note_id: noteId, update: b64encode(Y.encodeStateAsUpdate(doc)) });
      offlineDirty = false;
    }

    function seedFrom(html) {
      var ed = o.getAnyEditor ? o.getAnyEditor() : editor();
      if (!ed || typeof ed.applyHtmlToYjs !== "function") {
        fallback("no_editor");
        return;
      }
      ed.applyHtmlToYjs(doc, html, ORIGIN_SEED);
    }

    function fallback(reason) {
      if (stopped) return;
      stop();
      if (typeof o.onFallback === "function") o.onFallback(reason);
    }

    function handle(msg) {
      if (stopped || !msg || Number(msg.note_id) !== noteId) return;
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
        o.send({
          type: "y.compact",
          note_id: noteId,
          update: b64encode(Y.encodeStateAsUpdate(doc)),
          upto: nextSeq,
        });
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
        var ed = o.getAnyEditor ? o.getAnyEditor() : editor();
        if (ed && typeof ed.applyHtmlDeltaToYjs === "function") {
          ed.applyHtmlDeltaToYjs(doc, msg.old_html, msg.html || "", ORIGIN_RESEED);
          if (typeof o.onReseeded === "function") o.onReseeded();
        }
        return;
      }
      if (t === "y.error") {
        fallback(String(msg.reason || "error"));
      }
    }

    function join() {
      if (stopped) return;
      o.send({ type: "y.join", note_id: noteId });
    }

    function stop() {
      if (stopped) return;
      stopped = true;
      if (leaderTimer) clearTimeout(leaderTimer);
      detach();
      o.send({ type: "y.leave", note_id: noteId });
      try {
        doc.destroy();
      } catch (_) {}
    }

    return {
      noteId: noteId,
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
