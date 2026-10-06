(function () {
  var WEBAPP_BUILD = "20261006-cal-colorrow";

  function getTelegramWebApp() {
    return window.Telegram && window.Telegram.WebApp;
  }

  function isInsideTelegramClient() {
    if (hasTelegramWebAppAuth()) return true;
    var tg = getTelegramWebApp();
    if (!tg) return false;
    var p = String(tg.platform || "").toLowerCase();
    // telegram-web-app.js в обычном браузере тоже создаёт WebApp (platform=unknown, version задан).
    return !!(p && p !== "unknown");
  }

  function isTelegramMobilePlatform(tg) {
    tg = tg || getTelegramWebApp();
    if (!tg) return false;
    var p = String(tg.platform || "").toLowerCase();
    return p === "ios" || p === "android";
  }

  function readInitDataFromUrl() {
    try {
      var u = new URL(window.location.href);
      var raw = u.searchParams.get("tgWebAppData") || "";
      if (raw) return raw;
      if (u.hash && u.hash.indexOf("tgWebAppData=") >= 0) {
        var hp = new URLSearchParams(u.hash.replace(/^#/, ""));
        raw = hp.get("tgWebAppData") || "";
        if (raw) return raw;
      }
    } catch (_) {}
    return "";
  }

  function getInitDataRaw() {
    var tg = getTelegramWebApp();
    if (tg) {
      var raw = String(tg.initData || "").trim();
      if (raw) return raw;
    }
    return readInitDataFromUrl();
  }

  (function ensureFreshWebappBuild() {
    var htmlBuild = document.documentElement.getAttribute("data-build") || "";
    if (!htmlBuild || htmlBuild === WEBAPP_BUILD) return;
    var key = "leo_webapp_reload_" + WEBAPP_BUILD;
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, "1");
    try {
      var u = new URL(window.location.href);
      u.searchParams.set("b", WEBAPP_BUILD);
      window.location.replace(u.toString());
    } catch (_) {}
  })();

  function looksLikeTelegramWebView() {
    try {
      if (window.TelegramWebviewProxy) return true;
      if (
        window.webkit &&
        window.webkit.messageHandlers &&
        window.webkit.messageHandlers.TelegramWebView
      ) {
        return true;
      }
      var ua = String(navigator.userAgent || "");
      if (/Telegram/i.test(ua)) return true;
      if (/tgWebApp/i.test(String(location.href || "") + String(location.hash || ""))) {
        return true;
      }
    } catch (_) {}
    return isInsideTelegramClient();
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    var standalone = false;
    try {
      standalone =
        (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
        window.navigator.standalone === true;
    } catch (_) {}
    if (!standalone) {
      navigator.serviceWorker.getRegistrations().then(function (regs) {
        regs.forEach(function (reg) {
          try {
            reg.unregister();
          } catch (_) {}
        });
      });
      if (typeof caches !== "undefined" && caches.keys) {
        caches.keys().then(function (keys) {
          keys.forEach(function (key) {
            if (String(key).indexOf("miniapp-") === 0) caches.delete(key);
          });
        }).catch(function () {});
      }
      return;
    }
    var run = function () {
      navigator.serviceWorker.register("./sw.js?v=" + WEBAPP_BUILD, { scope: "./" }).catch(function () {});
    };
    if (document.readyState === "complete") run();
    else window.addEventListener("load", run);
  }

  const API = "/api/miniapp";
  /** Должен совпадать с MINIAPP_DEV_BEARER в .env (по умолчанию miniapp-local-dev). */
  const MINIAPP_DEV_BEARER = "miniapp-local-dev";
  const MINIAPP_SESSION_KEY = "miniapp_session";
  const MINIAPP_SESSION_HINT_KEY = "miniapp_session_hint";
  const NOTE_EDITOR_ASSET_V = "20261005-heading-pdf";
  const MINIAPP_CACHE_SCHEMA = 2;
  let noteEditorScriptsPromise = null;

  function isIOSDevice() {
    if (typeof navigator === "undefined") return false;
    return (
      /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
    );
  }
  function currentTelegramUserId() {
    if (cachedMe) {
      if (cachedMe.telegram_user_id != null) return String(cachedMe.telegram_user_id);
      if (cachedMe.id != null) return String(cachedMe.id);
    }
    var raw = getInitDataRaw();
    if (raw) {
      try {
        var p = new URLSearchParams(raw);
        var user = JSON.parse(p.get("user") || "null");
        if (user && user.id != null) return String(user.id);
      } catch (_) {}
    }
    return "";
  }

  function getMiniappCacheUserKey() {
    var uid = currentTelegramUserId();
    if (uid) return uid;
    var sess = getStoredSession();
    if (sess) return "s_" + sess.slice(0, 16);
    return "anon";
  }

  function miniappCacheStorageKey(kind) {
    return (
      "leo_miniapp_c" +
      MINIAPP_CACHE_SCHEMA +
      "_" +
      getMiniappCacheUserKey() +
      "_" +
      kind
    );
  }

  function readMiniappCache(kind) {
    try {
      var raw = localStorage.getItem(miniappCacheStorageKey(kind));
      if (!raw) return null;
      var box = JSON.parse(raw);
      return box && box.data != null ? box.data : null;
    } catch (_) {
      return null;
    }
  }

  function writeMiniappCache(kind, data) {
    try {
      localStorage.setItem(
        miniappCacheStorageKey(kind),
        JSON.stringify({ ts: Date.now(), data: data })
      );
      return true;
    } catch (e) {
      try {
        // Drop old day caches to free quota, then retry once.
        var prefix =
          "leo_miniapp_c" + MINIAPP_CACHE_SCHEMA + "_" + getMiniappCacheUserKey() + "_actual_";
        var keys = [];
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.indexOf(prefix) === 0) keys.push(k);
        }
        keys.sort();
        keys.slice(0, Math.max(0, keys.length - 3)).forEach(function (k) {
          try {
            localStorage.removeItem(k);
          } catch (_) {}
        });
        localStorage.setItem(
          miniappCacheStorageKey(kind),
          JSON.stringify({ ts: Date.now(), data: data })
        );
        return true;
      } catch (e2) {
        console.warn("writeMiniappCache failed", e2);
        return false;
      }
    }
  }

  var lastNetworkFailAt = 0;

  function markNetworkFailure() {
    lastNetworkFailAt = Date.now();
  }

  function markNetworkSuccess() {
    lastNetworkFailAt = 0;
  }

  function isAppOffline() {
    try {
      if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
    } catch (_) {}
    return false;
  }

  function isNetworkUnreliable() {
    // Soft signal after a recent timeout/5xx — used only to reduce retries,
    // never to block requests. Blocking caused "ждут сеть" + empty discussions
    // while the device was actually online.
    return !!(lastNetworkFailAt && Date.now() - lastNetworkFailAt < 12000);
  }

  function persistNotesCacheToDisk() {
    if (notesDataCache) writeMiniappCache("notes", notesDataCache);
  }

  function noteDraftsApi() {
    return (typeof window !== "undefined" && window.NoteDrafts) || null;
  }

  function noteDraftUserKey() {
    return getMiniappCacheUserKey();
  }

  function writeLocalNoteDraft(itemId, fields) {
    var api = noteDraftsApi();
    if (!api || !itemId) return null;
    try {
      return api.putDraft(noteDraftUserKey(), "local", itemId, fields || {});
    } catch (e) {
      console.warn("writeLocalNoteDraft", e);
      return null;
    }
  }

  function readLocalNoteDraft(itemId) {
    var api = noteDraftsApi();
    if (!api || !itemId) return null;
    try {
      return api.getDraft(noteDraftUserKey(), "local", itemId);
    } catch (_) {
      return null;
    }
  }

  function clearLocalNoteDraft(itemId) {
    var api = noteDraftsApi();
    if (!api || !itemId) return;
    try {
      api.clearDraft(noteDraftUserKey(), "local", itemId);
    } catch (_) {}
  }

  function remapLocalNoteDraft(fromId, toId) {
    var api = noteDraftsApi();
    if (!api || !fromId || !toId) return;
    try {
      api.remapItemId(noteDraftUserKey(), "local", fromId, toId);
    } catch (_) {}
  }

  function hydrateLocalNoteWithDraft(note) {
    var api = noteDraftsApi();
    var id = localNoteIdFrom(note);
    if (!api || !id) return { use: "server", title: note && note.title, body: (note && (note.body || note.description)) || "", draft: null };
    var draft = readLocalNoteDraft(id);
    return api.hydrateNoteFromDraft(
      {
        title: note && (note.title || note.content),
        body: note && (note.body || note.description),
        revision: note && note.revision,
      },
      draft
    );
  }

  function applyDraftsToNotesList(data) {
    var api = noteDraftsApi();
    if (!api || !data || !Array.isArray(data.local_notes)) return data;
    var drafts = [];
    try {
      drafts = api.listUnsynced(noteDraftUserKey(), "local") || [];
    } catch (_) {
      return data;
    }
    drafts.forEach(function (draft) {
      var id = String((draft && draft.itemId) || "");
      if (!id) return;
      function hydrateList(list) {
        (list || []).forEach(function (note) {
          if (String(note.id) !== id) return;
          var h = api.hydrateNoteFromDraft(note, draft);
          if (h.use === "draft") {
            note.title = h.title;
            note.body = h.body;
            note.description = h.body;
          }
        });
      }
      hydrateList(data.local_notes);
      hydrateList(data.transcriptions);
    });
    return data;
  }

  function snapshotOpenLocalNoteDraft() {
    if (!isNoteEditorModalOpen()) return null;
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || wrap._shareKind !== "local" || !wrap._shareId) return null;
    var title = "";
    try {
      title = getNoteEditorTitle().trim();
    } catch (_) {}
    var body = "";
    try {
      body = isPrimarySheetId(noteSheetState.leftId)
        ? readActiveNoteEditorHtml(getLeftNoteRichEditor())
        : noteSheetState.primaryBody || "";
    } catch (_) {
      body = noteSheetState.primaryBody || "";
    }
    return writeLocalNoteDraft(wrap._shareId, {
      title: title,
      body: body,
      baseRevision: wrap._noteRevision,
      baseUpdatedAt: wrap._noteUpdatedAt,
    });
  }

  function keepaliveOpenLocalNotePatch() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || wrap._shareKind !== "local" || !wrap._shareId) return;
    var id = String(wrap._shareId);
    if (!id || id.indexOf("tmp_") === 0) return;
    if (isAppOffline()) return;
    var title = "";
    try {
      title = getNoteEditorTitle().trim();
    } catch (_) {}
    var body = "";
    try {
      body = isPrimarySheetId(noteSheetState.leftId)
        ? readActiveNoteEditorHtml(getLeftNoteRichEditor())
        : noteSheetState.primaryBody || "";
    } catch (_) {}
    try {
      fetch(API + "/notes/local/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({
          title: title,
          description: body,
          expected_revision: wrap._noteRevision || undefined,
        }),
        credentials: "same-origin",
        keepalive: true,
      });
    } catch (_) {}
  }

  function applyNoteDraftToOpenEditor(title, body) {
    var titleInput = document.getElementById("note-editor-title-input");
    if (titleInput && title != null) {
      titleInput.value = String(title || "");
      titleInput.dispatchEvent(new Event("input"));
    }
    noteSheetState.primaryBody = String(body || "");
    var left = getLeftNoteRichEditor();
    if (left && typeof left.setHtml === "function" && isPrimarySheetId(noteSheetState.leftId)) {
      left.setHtml(String(body || ""));
    }
  }

  var noteDraftConflictBusy = false;

  function persistNoteDraftConflict(itemId, server, choice) {
    var api = noteDraftsApi();
    var draft = readLocalNoteDraft(itemId);
    if (!draft) return Promise.resolve();
    var serverTitle = String((server && server.title) || "");
    var serverBody = String((server && (server.body || server.description)) || "");
    if (choice === "server") {
      clearLocalNoteDraft(itemId);
      if (isNoteEditorModalOpen() && currentOpenNoteId() === String(itemId)) {
        applyRemoteSharedNote(server, true);
      } else if (server) {
        prependLocalInCache(server);
      }
      return Promise.resolve();
    }
    var title = choice === "both" ? serverTitle || draft.title : draft.title;
    var body =
      choice === "both" && api
        ? api.mergeKeepBoth(serverBody, draft.body)
        : draft.body;
    if (isNoteEditorModalOpen() && currentOpenNoteId() === String(itemId)) {
      var wrap = document.getElementById("note-editor-more-wrap");
      if (wrap) {
        wrap._noteRevision = (server && server.revision) || wrap._noteRevision;
        wrap._noteUpdatedAt = (server && server.updated_at) || wrap._noteUpdatedAt;
      }
      applyNoteDraftToOpenEditor(title, body);
      writeLocalNoteDraft(itemId, {
        title: title,
        body: body,
        baseRevision: server && server.revision,
        baseUpdatedAt: server && server.updated_at,
        conflict: null,
      });
      var flush = getNoteEditorBodyEl();
      if (flush && typeof flush._noteEditorFlush === "function") {
        return flush._noteEditorFlush();
      }
    }
    writeLocalNoteDraft(itemId, {
      title: title,
      body: body,
      baseRevision: server && server.revision,
      baseUpdatedAt: server && server.updated_at,
      conflict: null,
    });
    return apiFetch("/notes/local/" + encodeURIComponent(itemId), {
      method: "PATCH",
      body: JSON.stringify({
        title: title,
        description: body,
        expected_revision: server && server.revision,
      }),
    }).then(function (res) {
      var item = res && res.item;
      if (item) {
        prependLocalInCache(item);
        clearLocalNoteDraft(itemId);
      }
    });
  }

  function showNoteDraftConflictDialog(itemId, server, draft) {
    if (noteDraftConflictBusy) return;
    var existing = document.getElementById("note-draft-conflict-overlay");
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    noteDraftConflictBusy = true;
    var ov = document.createElement("div");
    ov.id = "note-draft-conflict-overlay";
    ov.className = "note-draft-conflict-overlay";
    ov.innerHTML =
      '<div class="note-draft-conflict-sheet" role="dialog" aria-modal="true">' +
      "<p>Заметка уже изменилась на другом устройстве. Что оставить?</p>" +
      '<div class="note-draft-conflict-actions">' +
      '<button type="button" class="btn" data-choice="server">Оставить с сервера</button>' +
      '<button type="button" class="btn" data-choice="mine">Заменить своим текстом</button>' +
      '<button type="button" class="btn-text" data-choice="both">Оставить оба</button>' +
      "</div></div>";
    function finish(choice) {
      if (ov.parentNode) ov.parentNode.removeChild(ov);
      noteDraftConflictBusy = false;
      persistNoteDraftConflict(itemId, server, choice).catch(function (e) {
        alert((e && e.message) || "Не удалось сохранить выбор");
      });
    }
    ov.addEventListener("click", function (e) {
      var btn = e.target && e.target.closest && e.target.closest("[data-choice]");
      if (!btn || !ov.contains(btn)) return;
      finish(btn.getAttribute("data-choice"));
    });
    document.body.appendChild(ov);
  }

  var noteDraftConflictNotified = {};

  function handleNotePatchConflict(itemId, conflictItem, localTitle, localBody) {
    var id = String(itemId || "");
    if (!id) return;
    var server = conflictItem || {};
    writeLocalNoteDraft(id, {
      title: localTitle,
      body: localBody,
      conflict: {
        title: server.title,
        body: server.body || server.description,
        revision: server.revision,
        updated_at: server.updated_at,
      },
    });
    if (isNoteEditorModalOpen() && currentOpenNoteId() === id) {
      showNoteDraftConflictDialog(id, server, readLocalNoteDraft(id));
      return;
    }
    var notifyKey = id + ":" + String(server.revision || "");
    if (noteDraftConflictNotified[notifyKey]) return;
    noteDraftConflictNotified[notifyKey] = true;
    showNoteToast("Заметка изменилась на другом устройстве. Откройте её, чтобы выбрать версию.", {
      duration: 7000,
    });
  }

  function clearLocalNoteDraftIfUnchanged(itemId, title, body) {
    var api = noteDraftsApi();
    var d = readLocalNoteDraft(itemId);
    if (!d) return;
    if (!api || (api.bodiesEqual(d.title, title) && api.bodiesEqual(d.body, body))) {
      clearLocalNoteDraft(itemId);
    }
  }

  function notePatchQueueBody(fields, wrap) {
    var body = {
      title: fields.title,
      description: fields.description,
    };
    if (wrap && wrap._noteRevision) body.expected_revision = wrap._noteRevision;
    return body;
  }

  function notePayloadEquals(titleA, bodyA, titleB, bodyB) {
    var api = noteDraftsApi();
    if (api) return api.bodiesEqual(titleA, titleB) && api.bodiesEqual(bodyA, bodyB);
    return String(titleA || "") === String(titleB || "") && String(bodyA || "") === String(bodyB || "");
  }

  function adoptNoteRevision(item, itemId) {
    if (!item) return;
    var wrap = document.getElementById("note-editor-more-wrap");
    var api = noteDraftsApi();
    var next = api ? api.revNum(item.revision) : Number(item.revision || 0);
    if (wrap) {
      var cur = api ? api.revNum(wrap._noteRevision) : Number(wrap._noteRevision || 0);
      if (next >= cur) {
        wrap._noteRevision = item.revision || wrap._noteRevision;
        wrap._noteUpdatedAt = item.updated_at || wrap._noteUpdatedAt;
      }
    }
    var id = String(itemId || (item && item.id) || currentOpenNoteId() || "");
    var draft = id ? readLocalNoteDraft(id) : null;
    if (draft && next) {
      writeLocalNoteDraft(id, {
        title: draft.title,
        body: draft.body,
        baseRevision: item.revision,
        baseUpdatedAt: item.updated_at,
        conflict: null,
      });
    }
  }

  function noteEditorBaseline() {
    var el = getNoteEditorBodyEl();
    return el && el._noteEditor && el._noteEditor.baseline ? el._noteEditor.baseline : null;
  }

  function offlineQueueStorageKey() {
    return "leo_offline_q_v1_" + getMiniappCacheUserKey();
  }

  function readOfflineQueue() {
    try {
      var raw = localStorage.getItem(offlineQueueStorageKey());
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) {
      return [];
    }
  }

  function writeOfflineQueue(arr) {
    try {
      localStorage.setItem(offlineQueueStorageKey(), JSON.stringify(arr || []));
    } catch (e) {
      console.warn("writeOfflineQueue failed", e);
      try {
        showNoteToast("Мало места для офлайн-сохранения");
      } catch (_) {}
      throw e;
    }
    syncOfflineQueueBadge();
  }

  function enqueueOfflineOp(op) {
    var q = readOfflineQueue();
    op = op || {};
    op.id = op.id || "oq_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
    op.createdAt = op.createdAt || Date.now();
    // Coalesce note patches for the same note into the latest body.
    if (op.type === "note_patch" && op.clientId) {
      for (var i = q.length - 1; i >= 0; i--) {
        if (q[i].type === "note_create" && String(q[i].clientId) === String(op.clientId)) {
          q[i].body = Object.assign({}, q[i].body || {}, op.body || {});
          writeOfflineQueue(q);
          return q[i];
        }
        if (q[i].type === "note_patch" && String(q[i].clientId) === String(op.clientId)) {
          q[i].body = Object.assign({}, q[i].body || {}, op.body || {});
          writeOfflineQueue(q);
          return q[i];
        }
      }
    }
    if (op.type === "note_create" && op.clientId) {
      for (var j = q.length - 1; j >= 0; j--) {
        if (q[j].type === "note_create" && String(q[j].clientId) === String(op.clientId)) {
          q[j].body = Object.assign({}, q[j].body || {}, op.body || {});
          writeOfflineQueue(q);
          return q[j];
        }
      }
    }
    if (op.type === "reminder_patch" && op.clientId) {
      for (var k = q.length - 1; k >= 0; k--) {
        if (
          (q[k].type === "reminder_create" || q[k].type === "reminder_patch") &&
          String(q[k].clientId) === String(op.clientId)
        ) {
          q[k].body = Object.assign({}, q[k].body || {}, op.body || {});
          if (q[k].type === "reminder_create") {
            writeOfflineQueue(q);
            return q[k];
          }
          writeOfflineQueue(q);
          return q[k];
        }
      }
    }
    if (op.type === "reminder_delete" && op.clientId) {
      var filtered = [];
      var droppedCreate = false;
      for (var d = 0; d < q.length; d++) {
        if (
          String(q[d].clientId) === String(op.clientId) &&
          (q[d].type === "reminder_create" || q[d].type === "reminder_patch")
        ) {
          if (q[d].type === "reminder_create") droppedCreate = true;
          continue;
        }
        filtered.push(q[d]);
      }
      q = filtered;
      if (droppedCreate && String(op.clientId).indexOf("tmp_") === 0) {
        writeOfflineQueue(q);
        return null;
      }
    }
    if (op.type === "note_delete" && op.clientId) {
      var nq = [];
      var droppedNoteCreate = false;
      for (var n = 0; n < q.length; n++) {
        if (
          String(q[n].clientId) === String(op.clientId) &&
          (q[n].type === "note_create" || q[n].type === "note_patch")
        ) {
          if (q[n].type === "note_create") droppedNoteCreate = true;
          continue;
        }
        nq.push(q[n]);
      }
      q = nq;
      if (droppedNoteCreate && String(op.clientId).indexOf("tmp_") === 0) {
        writeOfflineQueue(q);
        return null;
      }
    }
    if (op.type === "sheet_patch" && op.clientId) {
      for (var si = q.length - 1; si >= 0; si--) {
        if (q[si].type === "sheet_create" && String(q[si].clientId) === String(op.clientId)) {
          q[si].body = Object.assign({}, q[si].body || {}, op.body || {});
          writeOfflineQueue(q);
          return q[si];
        }
        if (q[si].type === "sheet_patch" && String(q[si].clientId) === String(op.clientId)) {
          q[si].body = Object.assign({}, q[si].body || {}, op.body || {});
          writeOfflineQueue(q);
          return q[si];
        }
      }
    }
    if (op.type === "sheet_create" && op.clientId) {
      for (var sj = q.length - 1; sj >= 0; sj--) {
        if (q[sj].type === "sheet_create" && String(q[sj].clientId) === String(op.clientId)) {
          q[sj].body = Object.assign({}, q[sj].body || {}, op.body || {});
          writeOfflineQueue(q);
          return q[sj];
        }
      }
    }
    if (op.type === "sheet_delete" && op.clientId) {
      var sq = [];
      var droppedSheetCreate = false;
      for (var sn = 0; sn < q.length; sn++) {
        if (
          String(q[sn].clientId) === String(op.clientId) &&
          (q[sn].type === "sheet_create" || q[sn].type === "sheet_patch")
        ) {
          if (q[sn].type === "sheet_create") droppedSheetCreate = true;
          continue;
        }
        sq.push(q[sn]);
      }
      q = sq;
      if (droppedSheetCreate && String(op.clientId).indexOf("tmp_") === 0) {
        writeOfflineQueue(q);
        return null;
      }
    }
    q.push(op);
    writeOfflineQueue(q);
    return op;
  }

  function newTempId(prefix) {
    return (
      (prefix || "tmp_") +
      Date.now().toString(36) +
      "_" +
      Math.random().toString(36).slice(2, 8)
    );
  }

  function showOfflineSavedToast(msg) {
    try {
      showNoteToast(msg || "Сохранено офлайн");
    } catch (_) {}
  }

  function syncOfflineQueueBadge() {
    var q = readOfflineQueue();
    var n = q.length;
    document.documentElement.classList.toggle("has-offline-queue", n > 0);
    var el = document.getElementById("offline-sync-banner");
    if (!el) {
      if (!n) return;
      el = document.createElement("div");
      el.id = "offline-sync-banner";
      el.className = "offline-sync-banner";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    if (!n) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    el.classList.remove("hidden");
    el.textContent =
      n === 1
        ? "1 изменение ждёт сеть"
        : n + " изменений ждут сеть";
  }

  function remapOfflineNoteId(tmpId, realId) {
    if (!tmpId || !realId || String(tmpId) === String(realId)) return;
    if (notesDataCache && notesDataCache.local_notes) {
      notesDataCache.local_notes.forEach(function (n) {
        if (String(n.id) === String(tmpId)) n.id = realId;
      });
      persistNotesCacheToDisk();
    }
    var q = readOfflineQueue();
    q.forEach(function (op) {
      if (String(op.clientId) === String(tmpId)) {
        op.clientId = String(realId);
        if (op.path && op.path.indexOf("/notes/local/") === 0) {
          var sheetTail = String(op.path).replace(/^\/notes\/local\/[^/]+/, "");
          if (op.type === "note_pin") {
            op.path = "/notes/local/" + encodeURIComponent(String(realId)) + "/pin";
          } else if (
            op.type === "sheet_create" ||
            op.type === "sheet_patch" ||
            op.type === "sheet_delete"
          ) {
            op.path = "/notes/local/" + encodeURIComponent(String(realId)) + sheetTail;
            op.noteId = String(realId);
          } else {
            op.path = "/notes/local/" + encodeURIComponent(String(realId));
          }
        }
      }
    });
    writeOfflineQueue(q);
    remapLocalNoteDraft(tmpId, realId);
  }

  function remapOfflineSheetId(tmpId, realId, noteId) {
    if (!tmpId || !realId || String(tmpId) === String(realId)) return;
    var q = readOfflineQueue();
    q.forEach(function (op) {
      if (String(op.clientId) !== String(tmpId)) return;
      if (
        op.type !== "sheet_create" &&
        op.type !== "sheet_patch" &&
        op.type !== "sheet_delete"
      ) {
        return;
      }
      op.clientId = String(realId);
      var nid = String(op.noteId || noteId || "");
      if (nid) {
        op.noteId = nid;
        if (op.type === "sheet_create") {
          op.path = "/notes/local/" + encodeURIComponent(nid) + "/sheets";
        } else {
          op.path =
            "/notes/local/" +
            encodeURIComponent(nid) +
            "/sheets/" +
            encodeURIComponent(String(realId));
        }
      }
    });
    writeOfflineQueue(q);
  }

  function remapOfflineReminderId(tmpId, realId) {
    if (!tmpId || !realId || String(tmpId) === String(realId)) return;
    lastRemindersItems = (lastRemindersItems || []).map(function (r) {
      if (String(r.id) === String(tmpId)) {
        return Object.assign({}, r, { id: realId });
      }
      return r;
    });
    persistActualRemindersToDisk();
    var q = readOfflineQueue();
    q.forEach(function (op) {
      if (String(op.clientId) === String(tmpId)) {
        op.clientId = String(realId);
        if (op.path && op.path.indexOf("/reminders/") === 0) {
          op.path = "/reminders/" + encodeURIComponent(String(realId));
        }
      }
    });
    writeOfflineQueue(q);
  }

  function actualCacheKind() {
    var leftIso = meetingsPageDate || todayIsoLocal();
    if (meetingsViewMode === "week") return "actual_week_" + startOfWeekMonday(leftIso);
    if (meetingsViewMode === "month") return "actual_month_" + startOfMonthIso(leftIso);
    return "actual_" + leftIso + (isDesktopLayout() ? "_2" : "");
  }

  function persistActualRemindersToDisk() {
    try {
      var cacheKind = actualCacheKind();
      var cached = readMiniappCache(cacheKind) || {};
      cached.remData = { items: lastRemindersItems || [] };
      if (lastMeetingsLeft) cached.calData = lastMeetingsLeft;
      if (lastMeetingsRight) cached.calDataNext = lastMeetingsRight;
      if (lastMeetingsRange) cached.rangeData = lastMeetingsRange;
      writeMiniappCache(cacheKind, cached);
    } catch (_) {}
  }

  function applyReminderLocal(itemsMutator) {
    lastRemindersItems = itemsMutator(lastRemindersItems.slice());
    persistActualRemindersToDisk();
    paintActual(
      { items: lastRemindersItems },
      lastMeetingsLeft || { events: [], connected: false },
      lastMeetingsRight
    );
  }

  var offlineFlushInFlight = false;

  async function flushOfflineQueue() {
    if (offlineFlushInFlight) return;
    if (isAppOffline()) {
      syncOfflineQueueBadge();
      return;
    }
    // Previous sticky-offline logic left the queue stuck even when online.
    // Clear soft failure flag so pending ops can actually sync.
    markNetworkSuccess();
    var q = readOfflineQueue();
    if (!q.length) {
      syncOfflineQueueBadge();
      return;
    }
    offlineFlushInFlight = true;
    try {
      while (q.length) {
        var op = q[0];
        try {
          var bodyStr =
            op.body != null
              ? typeof op.body === "string"
                ? op.body
                : JSON.stringify(op.body)
              : undefined;
          var res = await apiFetchReal(op.path, {
            method: op.method || "POST",
            body: bodyStr,
          });
          if (op.type === "note_create") {
            var createdId =
              (res && res.item && res.item.id) || (res && res.id) || null;
            if (createdId) remapOfflineNoteId(op.clientId, createdId);
          }
          if (op.type === "sheet_create") {
            var createdSheetId =
              (res && res.item && res.item.id) || (res && res.id) || null;
            if (createdSheetId) remapOfflineSheetId(op.clientId, createdSheetId, op.noteId);
          }
          if (op.type === "reminder_create") {
            var rid =
              (res && res.item && res.item.id) ||
              (res && res.id) ||
              (res && res.reminder && res.reminder.id) ||
              null;
            if (rid) remapOfflineReminderId(op.clientId, rid);
          }
          q.shift();
          writeOfflineQueue(q);
        } catch (e) {
          if (op.type === "note_patch" && e && e.status === 409) {
            var conflictBody = op.body || {};
            var remoteQ = e.conflictItem || {};
            if (
              notePayloadEquals(
                conflictBody.title,
                conflictBody.description,
                remoteQ.title,
                remoteQ.body || remoteQ.description
              )
            ) {
              q.shift();
              writeOfflineQueue(q);
              adoptNoteRevision(remoteQ, op.clientId);
              clearLocalNoteDraftIfUnchanged(op.clientId, conflictBody.title, conflictBody.description);
              continue;
            }
            q.shift();
            writeOfflineQueue(q);
            handleNotePatchConflict(
              op.clientId,
              e.conflictItem,
              conflictBody.title,
              conflictBody.description
            );
            continue;
          }
          if (op.type === "note_patch" && e && e.status === 404) {
            q.shift();
            writeOfflineQueue(q);
            clearLocalNoteDraft(op.clientId);
            continue;
          }
          if (op.type === "note_patch" && !isTransientApiError(e)) {
            console.warn("offline note_patch kept", op, e);
            break;
          }
          if (isTransientApiError(e)) break;
          // Permanent failure: drop op so the queue does not stick forever.
          q.shift();
          writeOfflineQueue(q);
          console.warn("offline op dropped", op, e);
        }
      }
      if (!readOfflineQueue().length) {
        try {
          await loadNotes();
        } catch (_) {}
        try {
          await loadActual();
        } catch (_) {}
      }
    } finally {
      offlineFlushInFlight = false;
      syncOfflineQueueBadge();
    }
  }

  function bindOfflineQueueListenersOnce() {
    if (window._leoOfflineQueueBound) return;
    window._leoOfflineQueueBound = true;
    function flushOpenNoteOnHide() {
      snapshotOpenLocalNoteDraft();
      keepaliveOpenLocalNotePatch();
      var body = getNoteEditorBodyEl();
      if (body && typeof body._noteEditorFlush === "function") {
        try {
          var p = body._noteEditorFlush();
          if (p && typeof p.catch === "function") p.catch(function () {});
        } catch (_) {}
      }
    }
    window.addEventListener("online", function () {
      markNetworkSuccess();
      flushOfflineQueue();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        flushOpenNoteOnHide();
        return;
      }
      if (document.visibilityState === "visible") {
        if (!isAppOffline()) markNetworkSuccess();
        flushOfflineQueue();
      }
    });
    window.addEventListener("pagehide", flushOpenNoteOnHide);
    window.addEventListener("freeze", flushOpenNoteOnHide);
    syncOfflineQueueBadge();
    setTimeout(function () {
      flushOfflineQueue();
    }, 1200);
  }

  function loadNoteEditorScripts() {
    if (getNoteRichEditorApi()) return Promise.resolve();
    if (noteEditorScriptsPromise) return noteEditorScriptsPromise;
    function injectScript(src) {
      return new Promise(function (res, rej) {
        var existing = document.querySelector('script[src="' + src + '"]');
        if (existing) {
          var waited = 0;
          var timer = setInterval(function () {
            waited += 50;
            if (src.indexOf("note-rich-editor.js") !== -1) {
              if (getNoteRichEditorApi()) {
                clearInterval(timer);
                res();
                return;
              }
            } else if (waited >= 80) {
              clearInterval(timer);
              res();
              return;
            }
            if (waited >= 25000) {
              clearInterval(timer);
              rej(new Error("Таймаут " + src));
            }
          }, 50);
          return;
        }
        var s = document.createElement("script");
        s.src = src;
        s.async = false;
        s.onload = function () {
          res();
        };
        s.onerror = function () {
          rej(new Error("Не удалось загрузить " + src));
        };
        document.head.appendChild(s);
      });
    }
    noteEditorScriptsPromise = injectScript("/webapp/note-html.js?v=" + NOTE_EDITOR_ASSET_V)
      .then(function () {
        return injectScript("/webapp/note-comments.js?v=" + NOTE_EDITOR_ASSET_V);
      })
      .then(function () {
        return injectScript("/webapp/note-rich-editor.js?v=" + NOTE_EDITOR_ASSET_V);
      })
      .then(function () {
        if (getNoteRichEditorApi()) return;
        throw new Error("Редактор не инициализировался");
      })
      .catch(function (err) {
        noteEditorScriptsPromise = null;
        throw err;
      });
    return noteEditorScriptsPromise;
  }

  let cachedBotUsername = "";
  let gateLoginBootstrapped = false;
  let cachedMe = null;
  let notesSubTab = "notes";
  var chatThreadsCache = [];
  var activeChatThreadId = "";
  var chatThreadsLoading = false;
  var leavingChatsTab = false;
  var sessionChatDraftIds = Object.create(null);
  let selectedPaymentMethod = "";
  let miniappDev = false;
  var meetingsPageDate = null;
  var timelineScrolledKey = "";
  var notesDataCache = null;
  var knowledgeDataCache = null;
  var notesSearchQuery = "";
  var knowledgeSearchQuery = "";
  var notesActiveProjectId = 0;
  var plusPickerActiveProjectId = 0;
  var plusPickerKind = "note";
  var panePickerActiveProjectId = 0;
  var linkPickerActiveProjectId = 0;
  var noteAgentSessions = {};
  var noteAgentPollers = {};
  var pendingDiscussOpen = null;
  var tagPickerSaveTimer = null;
  var tagPickerActiveClose = null;
  var tagPickerDocClickBound = false;
  var tabInited = { profile: false };
  var TAB_ORDER = ["actual", "notes", "digest", "knowledge", "profile"];
  var digestTabEnabled = false;
  var digestRefreshing = false;
  var digestSubTab = "chats";
  var sidebarTreeState = {
    notesOpen: true,
    digestOpen: true,
    sections: { notes: true, chats: false, transcriptions: false },
    projects: {},
  };
  var digestMarketLoaded = false;
  var digestMarketLoading = false;
  var currentTab = "actual";
  var currentProfileScreen = "main";
  var panelTransitionMs = 280;
  var gptChatState = { context: "", title: "Диалог с GPT", history: [] };
  var voiceChatState = { history: [] };
  var voiceMediaRecorder = null;
  var voiceMediaStream = null;
  var voiceChunks = [];
  var notesTelegramBackHandlerBound = false;
  var edgeSwipeBackBound = false;

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function todayIsoLocal() {
    var now = new Date();
    return now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());
  }

  function addDaysIso(iso, days) {
    var base = iso || todayIsoLocal();
    var d = new Date(base + "T12:00:00");
    d.setDate(d.getDate() + Number(days || 0));
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  var MEETINGS_VIEW_LS = "leo_meetings_view";
  var meetingsViewMode = "day";
  var lastMeetingsRange = null;
  var meetingsViewMenuBound = false;

  function readMeetingsViewMode() {
    try {
      var raw = String(localStorage.getItem(MEETINGS_VIEW_LS) || "").trim();
      if (raw === "week" || raw === "month" || raw === "day") return raw;
    } catch (_) {}
    return "day";
  }

  function persistMeetingsViewMode(mode) {
    try {
      localStorage.setItem(MEETINGS_VIEW_LS, mode);
    } catch (_) {}
  }

  function isoFromDate(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function dateFromIso(iso) {
    return new Date((iso || todayIsoLocal()) + "T12:00:00");
  }

  function startOfWeekMonday(iso) {
    var d = dateFromIso(iso);
    if (isNaN(d.getTime())) d = dateFromIso(todayIsoLocal());
    var wd = d.getDay();
    var delta = wd === 0 ? -6 : 1 - wd;
    d.setDate(d.getDate() + delta);
    return isoFromDate(d);
  }

  function startOfMonthIso(iso) {
    var d = dateFromIso(iso);
    if (isNaN(d.getTime())) d = dateFromIso(todayIsoLocal());
    d.setDate(1);
    return isoFromDate(d);
  }

  function addMonthsIso(iso, months) {
    var d = dateFromIso(iso);
    if (isNaN(d.getTime())) d = dateFromIso(todayIsoLocal());
    var day = d.getDate();
    d.setDate(1);
    d.setMonth(d.getMonth() + Number(months || 0));
    var last = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(day, last));
    return isoFromDate(d);
  }

  function monthGridRange(iso) {
    var monthStart = startOfMonthIso(iso);
    var nextMonth = addMonthsIso(monthStart, 1);
    var monthEnd = addDaysIso(nextMonth, -1);
    return {
      from: startOfWeekMonday(monthStart),
      to: addDaysIso(startOfWeekMonday(monthEnd), 6),
      monthStart: monthStart,
    };
  }

  function meetingsVisibleRange(anchorIso) {
    var iso = anchorIso || meetingsPageDate || todayIsoLocal();
    if (meetingsViewMode === "week") {
      var mon = startOfWeekMonday(iso);
      return { from: mon, to: addDaysIso(mon, 6) };
    }
    if (meetingsViewMode === "month") {
      var grid = monthGridRange(iso);
      return { from: grid.from, to: grid.to };
    }
    var dual = isDesktopLayout();
    return { from: iso, to: dual ? addDaysIso(iso, 1) : iso };
  }

  function eachIsoDay(fromIso, toIso) {
    var out = [];
    var cur = fromIso;
    var guard = 0;
    while (cur && cur <= toIso && guard < 50) {
      out.push(cur);
      cur = addDaysIso(cur, 1);
      guard += 1;
    }
    return out;
  }

  function meetingsPageStep() {
    if (meetingsViewMode === "week") return 7;
    if (meetingsViewMode === "month") return 1;
    return isDesktopLayout() ? 2 : 1;
  }

  var meetingsLoadGen = 0;

  function meetingsPageShowsToday() {
    var today = todayIsoLocal();
    var range = meetingsVisibleRange();
    if (meetingsViewMode === "month") {
      var a = dateFromIso(meetingsPageDate || today);
      var t = dateFromIso(today);
      return a.getFullYear() === t.getFullYear() && a.getMonth() === t.getMonth();
    }
    return today >= range.from && today <= range.to;
  }

  function syncMeetingsTodayBtn() {
    var btn = document.getElementById("meetings-today");
    if (!btn) return;
    setHidden(btn, meetingsPageShowsToday());
  }

  function syncMeetingsViewSurfaces() {
    var board = document.getElementById("meetings-board");
    var week = document.getElementById("meetings-week");
    var month = document.getElementById("meetings-month");
    var mode = meetingsViewMode;
    if (board) {
      board.classList.toggle("hidden", mode !== "day");
      if (mode !== "day") board.setAttribute("hidden", "hidden");
      else board.removeAttribute("hidden");
    }
    if (week) {
      week.classList.toggle("hidden", mode !== "week");
      if (mode !== "week") week.setAttribute("hidden", "hidden");
      else week.removeAttribute("hidden");
    }
    if (month) {
      month.classList.toggle("hidden", mode !== "month");
      if (mode !== "month") month.setAttribute("hidden", "hidden");
      else month.removeAttribute("hidden");
    }
    document.documentElement.setAttribute("data-meetings-view", mode);
  }

  function syncMeetingsViewMenu() {
    var menu = document.getElementById("meetings-view-menu");
    if (!menu) return;
    menu.querySelectorAll("[data-meetings-view]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-meetings-view") === meetingsViewMode);
    });
  }

  function closeMeetingsViewMenu() {
    var menu = document.getElementById("meetings-view-menu");
    var btn = document.getElementById("meetings-view-btn");
    if (menu) menu.classList.add("hidden");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleMeetingsViewMenu() {
    var menu = document.getElementById("meetings-view-menu");
    var btn = document.getElementById("meetings-view-btn");
    if (!menu || !btn) return;
    var open = menu.classList.contains("hidden");
    if (open) {
      syncMeetingsViewMenu();
      menu.classList.remove("hidden");
      btn.setAttribute("aria-expanded", "true");
    } else {
      closeMeetingsViewMenu();
    }
  }

  function bindMeetingsViewMenuOnce() {
    if (meetingsViewMenuBound) return;
    meetingsViewMenuBound = true;
    document.addEventListener("click", function (e) {
      var wrap = document.querySelector(".meetings-view-wrap");
      if (!wrap || (e.target && wrap.contains(e.target))) return;
      closeMeetingsViewMenu();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMeetingsViewMenu();
    });
  }

  function setMeetingsViewMode(mode, opts) {
    opts = opts || {};
    var next = mode === "week" || mode === "month" ? mode : "day";
    if (meetingsViewMode === next && !opts.force) {
      closeMeetingsViewMenu();
      return;
    }
    meetingsViewMode = next;
    persistMeetingsViewMode(next);
    syncMeetingsViewMenu();
    closeMeetingsViewMenu();
    syncMeetingsViewSurfaces();
    timelineScrolledKey = "";
    showMeetingsDaySkeleton();
    if (!opts.skipLoad) loadActual();
  }

  function paintMeetingsListSkeleton(ui, dateIso) {
    var listM = document.getElementById(ui.listId);
    var emptyM = document.getElementById(ui.emptyId);
    var titleEl = ui.titleId ? document.getElementById(ui.titleId) : null;
    if (titleEl) titleEl.textContent = formatMeetingsDayTitle(dateIso || "");
    var dayCol = ui.dayId ? document.getElementById(ui.dayId) : null;
    if (dayCol) dayCol.classList.toggle("meetings-day--today", dateIso === todayIsoLocal());
    if (emptyM) setHidden(emptyM, true);
    if (!listM) return;
    listM.classList.add("meetings-stack--timeline");
    listM.innerHTML =
      '<div class="meetings-skeleton" aria-hidden="true" aria-busy="true">' +
      '<div class="meetings-skeleton-row" style="width:74%"></div>' +
      '<div class="meetings-skeleton-row meetings-skeleton-row--tall" style="width:88%"></div>' +
      '<div class="meetings-skeleton-row" style="width:62%"></div>' +
      '<div class="meetings-skeleton-row meetings-skeleton-row--tall" style="width:80%"></div>' +
      '<div class="meetings-skeleton-row" style="width:70%"></div>' +
      "</div>";
  }

  function paintMeetingsRangeSkeleton(host) {
    if (!host) return;
    host.innerHTML =
      '<div class="meetings-skeleton" aria-hidden="true" aria-busy="true">' +
      '<div class="meetings-skeleton-row" style="width:74%"></div>' +
      '<div class="meetings-skeleton-row meetings-skeleton-row--tall" style="width:92%"></div>' +
      '<div class="meetings-skeleton-row" style="width:68%"></div>' +
      '<div class="meetings-skeleton-row meetings-skeleton-row--tall" style="width:86%"></div>' +
      '<div class="meetings-skeleton-row" style="width:70%"></div>' +
      "</div>";
  }

  function showMeetingsDaySkeleton() {
    syncMeetingsViewSurfaces();
    var leftIso = meetingsPageDate || todayIsoLocal();
    var range = meetingsVisibleRange(leftIso);
    if (meetingsViewMode === "week") {
      syncMeetingsHead(range.from, range.to);
      paintMeetingsRangeSkeleton(document.getElementById("meetings-week"));
      return;
    }
    if (meetingsViewMode === "month") {
      syncMeetingsHead(leftIso, null);
      paintMeetingsRangeSkeleton(document.getElementById("meetings-month"));
      return;
    }
    var dual = isDesktopLayout();
    var rightIso = dual ? addDaysIso(leftIso, 1) : null;
    syncMeetingsHead(leftIso, rightIso);
    paintMeetingsListSkeleton(meetingsUiMain, leftIso);
    if (dual) paintMeetingsListSkeleton(meetingsUiNext, rightIso);
    else {
      var nextList = document.getElementById(meetingsUiNext.listId);
      if (nextList) nextList.innerHTML = "";
    }
  }

  function shiftMeetingsPage(delta) {
    var step = Number(delta || 0);
    if (meetingsViewMode === "month") {
      meetingsPageDate = addMonthsIso(meetingsPageDate || todayIsoLocal(), step);
    } else {
      meetingsPageDate = addDaysIso(meetingsPageDate || todayIsoLocal(), step);
    }
    timelineScrolledKey = "";
    showMeetingsDaySkeleton();
    loadActual();
  }

  function goMeetingsToday() {
    var today = todayIsoLocal();
    if (meetingsPageShowsToday() && (meetingsPageDate || today) === today) {
      syncMeetingsTodayBtn();
      return;
    }
    meetingsPageDate = today;
    timelineScrolledKey = "";
    showMeetingsDaySkeleton();
    loadActual();
  }

  function meetingsEmptyMessage(calData) {
    if (calData && calData.connected === false) {
      var err = (calData.error || "").trim();
      if (err) return err;
      return "Подключите Google Calendar в боте: /calendar_auth";
    }
    return "Нет встреч на этот день";
  }

  async function fetchCalendarDay(dateIso) {
    var q =
      dateIso && dateIso.length ? "?date=" + encodeURIComponent(dateIso) : "";
    return apiFetch("/calendar/today" + q, { method: "GET" });
  }

  async function fetchCalendarRange(fromIso, toIso) {
    return apiFetch(
      "/calendar/range?start=" +
        encodeURIComponent(fromIso) +
        "&end=" +
        encodeURIComponent(toIso),
      { method: "GET" }
    );
  }

  function eventCoversDate(ev, dateIso) {
    if (!ev || !dateIso) return false;
    if (eventIsAllDay(ev)) {
      var start = String((ev.start && ev.start.date) || ev.start_day || "").slice(0, 10);
      var end = String((ev.end && ev.end.date) || ev.end_day || "").slice(0, 10);
      if (!start) return false;
      if (!end || end <= start) end = addDaysIso(start, 1);
      return start <= dateIso && dateIso < end;
    }
    var day0 = dayStartMs(dateIso);
    var day1 = dayStartMs(addDaysIso(dateIso, 1));
    if (!isFinite(day0) || !isFinite(day1)) return false;
    var startMs = eventStartMs(ev);
    var endMs = eventEndMs(ev);
    if (startMs != null) {
      if (endMs == null || endMs < startMs) endMs = startMs;
      if (endMs === startMs) return startMs >= day0 && startMs < day1;
      return startMs < day1 && endMs > day0;
    }
    var taggedStart = String(ev.start_day || "").slice(0, 10);
    if (!taggedStart) return false;
    var taggedEnd = String(ev.end_day || taggedStart).slice(0, 10);
    if (!taggedEnd || taggedEnd < taggedStart) taggedEnd = taggedStart;
    if (ev.day_end_exclusive && taggedEnd > taggedStart) {
      return taggedStart <= dateIso && dateIso < taggedEnd;
    }
    return taggedStart <= dateIso && dateIso <= taggedEnd;
  }

  function eventsForDate(events, dateIso) {
    return (events || []).filter(function (ev) {
      return !meetingIsDeclined(ev) && eventCoversDate(ev, dateIso);
    });
  }

  function weekHourPx() {
    return isDesktopLayout() ? 52 : 48;
  }

  function weekdayShort(iso) {
    try {
      return new Date(iso + "T12:00:00").toLocaleDateString("ru-RU", { weekday: "short" }).replace(".", "");
    } catch (_) {
      return "";
    }
  }

  function formatMeetingsWeekHead(fromIso, toIso) {
    try {
      var a = dateFromIso(fromIso);
      var b = dateFromIso(toIso || fromIso);
      if (isNaN(a.getTime()) || isNaN(b.getTime())) return "";
      var sameMonth = a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear();
      var left = a.toLocaleDateString("ru-RU", {
        day: "numeric",
        month: sameMonth ? undefined : "short",
      });
      var right = b.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
      return left + "–" + right;
    } catch (_) {
      return "";
    }
  }

  function formatMeetingsMonthHead(iso) {
    try {
      var d = dateFromIso(iso);
      if (isNaN(d.getTime())) return "";
      var label = d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
      return label.charAt(0).toUpperCase() + label.slice(1);
    } catch (_) {
      return "";
    }
  }

  function eventStartMs(ev) {
    var raw = ev && ev.start && ev.start.dateTime;
    if (!raw) return null;
    var t = Date.parse(raw);
    return isFinite(t) ? t : null;
  }

  function eventEndMs(ev) {
    var raw = ev && ev.end && ev.end.dateTime;
    if (raw) {
      var t = Date.parse(raw);
      if (isFinite(t)) return t;
    }
    return eventStartMs(ev);
  }

  function formatNowClock() {
    return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  var TIMELINE_START_MIN = 7 * 60;
  var TIMELINE_END_MIN = 24 * 60;
  var TIMELINE_HOUR_PX_MOBILE = 56;
  var TIMELINE_HOUR_PX_DESKTOP = 64;
  var TIMELINE_REMINDER_SPAN_MIN = 30;

  function timelineHourPx() {
    return isDesktopLayout() ? TIMELINE_HOUR_PX_DESKTOP : TIMELINE_HOUR_PX_MOBILE;
  }

  function localDateIsoFromMs(ms) {
    var d = new Date(ms);
    if (isNaN(d.getTime())) return "";
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function dayStartMs(dateIso) {
    return new Date(dateIso + "T00:00:00").getTime();
  }

  function eventIsAllDay(ev) {
    return !!(ev && ev.start && ev.start.date && !ev.start.dateTime);
  }

  function remindersForDate(reminders, dateIso) {
    return (reminders || []).filter(function (r) {
      if (!r || r.done) return false;
      var t = Date.parse(r.when_iso || "");
      if (!isFinite(t)) return false;
      return localDateIsoFromMs(t) === dateIso;
    });
  }

  function timelineSpanForRange(dateIso, startMs, endMs, opts) {
    opts = opts || {};
    var day0 = dayStartMs(dateIso);
    if (!isFinite(day0) || startMs == null || !isFinite(startMs)) return null;
    var minSpan = opts.minSpanMin || 15;
    var end = endMs != null && isFinite(endMs) ? endMs : startMs + minSpan * 60000;
    if (end <= startMs) end = startMs + minSpan * 60000;
    var startMin = (startMs - day0) / 60000;
    var endMin = (end - day0) / 60000;
    if (endMin <= 0 || startMin >= TIMELINE_END_MIN) return null;
    if (endMin <= TIMELINE_START_MIN) {
      return { zone: "early", startMin: startMin, endMin: endMin };
    }
    var clipStart = Math.max(startMin, TIMELINE_START_MIN);
    var clipEnd = Math.min(endMin, TIMELINE_END_MIN);
    if (clipEnd <= clipStart) return null;
    var visStart = clipStart - TIMELINE_START_MIN;
    var visEnd = clipEnd - TIMELINE_START_MIN;
    if (visEnd - visStart < 15) {
      visEnd = Math.min(TIMELINE_END_MIN - TIMELINE_START_MIN, visStart + 15);
    }
    return { zone: "grid", startMin: visStart, endMin: visEnd };
  }

  function assignTimelineColumns(items) {
    var sorted = items.slice().sort(function (a, b) {
      return a.startMin - b.startMin || b.endMin - a.endMin;
    });
    var cluster = [];
    var clusterEnd = -1;
    function flush() {
      if (!cluster.length) return;
      var colEnds = [];
      cluster.forEach(function (it) {
        var col = 0;
        while (col < colEnds.length && colEnds[col] > it.startMin + 0.01) col += 1;
        it.col = col;
        colEnds[col] = it.endMin;
      });
      var n = Math.max(1, colEnds.length);
      cluster.forEach(function (it) {
        it.cols = n;
      });
      cluster = [];
      clusterEnd = -1;
    }
    sorted.forEach(function (it) {
      if (cluster.length && it.startMin >= clusterEnd) flush();
      cluster.push(it);
      clusterEnd = Math.max(clusterEnd, it.endMin);
    });
    flush();
  }

  function formatTimelineHourLabel(hour) {
    var h = hour === 24 ? 0 : hour;
    return pad2(h) + ":00";
  }

  function renderDayTimelineNow(topPx) {
    var el = document.createElement("div");
    el.className = "day-timeline-now";
    el.style.top = topPx + "px";
    var clock = formatNowClock();
    el.setAttribute("aria-label", "Сейчас " + clock);
    var time = document.createElement("span");
    time.className = "day-timeline-now-time";
    time.textContent = clock;
    var line = document.createElement("span");
    line.className = "day-timeline-now-line";
    el.appendChild(time);
    el.appendChild(line);
    return el;
  }

  function renderTimelineReminder(r) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "day-timeline-reminder";
    var taskText = r.task || "Напоминание";
    var clock = formatEventClock(r.when_iso);
    btn.setAttribute("aria-label", clock ? taskText + ", " + clock : taskText);
    var task = document.createElement("span");
    task.className = "day-timeline-reminder-task";
    task.textContent = taskText;
    btn.appendChild(task);
    btn.addEventListener("click", function () {
      openReminderModal(r);
    });
    return btn;
  }

  function calendarEntryIsTask(ev) {
    if (!ev) return false;
    if (ev.entry_type === "task") return true;
    if (ev.kind === "Задача") return true;
    return String(ev.id || "").indexOf("task-") === 0;
  }

  function calendarTaskId(ev) {
    if (!ev) return "";
    if (ev.task_id != null && ev.task_id !== "") return String(ev.task_id);
    var id = String(ev.id || "");
    if (id.indexOf("task-") === 0) return id.slice(5);
    return /^\d+$/.test(id) ? id : "";
  }

  function withTaskClass(base, ev) {
    return calendarEntryIsTask(ev) ? base + " " + base + "--task" : base;
  }

  function openCalendarEntry(ev) {
    if (calendarEntryIsTask(ev)) openTaskModal(ev);
    else openEventModal(ev);
  }

  var CAL_ENTRY_COLOR_PALETTE = [
    "",
    "#039be5",
    "#3f51b5",
    "#7986cb",
    "#8e24aa",
    "#e67c73",
    "#f6bf26",
    "#33b679",
    "#0b8043",
    "#f4511e",
    "#d50000",
    "#616161",
  ];

  function calendarEntryColorKey(ev) {
    if (!ev) return "";
    var tid = calendarTaskId(ev);
    if (tid) return "task:" + tid;
    var id = String(ev.id || "").trim();
    if (!id) return "";
    if (id.indexOf("task-") === 0) return "task:" + id.slice(5);
    var cal = String(ev.calendar_id || "primary").trim() || "primary";
    return "event:" + cal + ":" + id;
  }

  function applyEntryDisplayColor(el, ev) {
    if (!el || !ev) return;
    var color = String(ev.display_color || "").trim();
    if (!/^#[0-9A-Fa-f]{6}$/.test(color)) {
      el.classList.remove("has-entry-color");
      el.style.removeProperty("--entry-color");
      return;
    }
    el.classList.add("has-entry-color");
    el.style.setProperty("--entry-color", color);
  }

  async function persistEntryDisplayColor(ev, color) {
    var key = (ev && (ev.color_key || calendarEntryColorKey(ev))) || "";
    if (!key) return;
    var cleaned = String(color || "").trim();
    await apiFetch("/calendar/entry-color", {
      method: "PUT",
      body: JSON.stringify({ entry_key: key, color: cleaned || null }),
    });
    if (ev) {
      if (cleaned) ev.display_color = cleaned;
      else delete ev.display_color;
      ev.color_key = key;
    }
  }

  function mountEntryColorPicker(host, initialColor) {
    if (!host) {
      return {
        getColor: function () {
          return "";
        },
      };
    }
    var selected = String(initialColor || "").trim().toLowerCase();
    if (selected && !/^#[0-9a-f]{6}$/.test(selected)) selected = "";
    host.innerHTML = "";
    host.className = "event-sheet-group event-sheet-color-group";

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "event-sheet-row event-sheet-row--nav event-sheet-color-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", "m-ev-color-menu");

    var lab = document.createElement("span");
    lab.className = "event-sheet-row-label";
    lab.textContent = "Цвет";

    var value = document.createElement("span");
    value.className = "event-sheet-row-value";
    var preview = document.createElement("span");
    preview.className = "event-sheet-color-preview";
    preview.setAttribute("aria-hidden", "true");
    value.appendChild(preview);
    value.insertAdjacentHTML("beforeend", eventSheetChevronHtml());

    toggle.appendChild(lab);
    toggle.appendChild(value);

    var menu = document.createElement("div");
    menu.id = "m-ev-color-menu";
    menu.className = "event-sheet-color-menu hidden";
    menu.setAttribute("role", "radiogroup");
    menu.setAttribute("aria-label", "Цвет в календаре");

    var menuHint = document.createElement("p");
    menuHint.className = "event-sheet-color-menu-hint muted small";
    menuHint.textContent = "Только у вас";
    menu.appendChild(menuHint);

    var swatches = document.createElement("div");
    swatches.className = "event-sheet-color-swatches";

    function syncPreview() {
      preview.className =
        "event-sheet-color-preview" +
        (selected ? "" : " event-sheet-color-preview--default");
      if (selected) {
        preview.style.setProperty("--swatch-color", selected);
        preview.style.background = selected;
      } else {
        preview.style.removeProperty("--swatch-color");
        preview.style.background = "";
      }
    }

    function paintSwatches() {
      swatches.querySelectorAll(".event-sheet-color-swatch").forEach(function (btn) {
        var val = btn.getAttribute("data-color") || "";
        btn.classList.toggle("is-selected", val === selected);
        btn.setAttribute("aria-checked", val === selected ? "true" : "false");
      });
      syncPreview();
    }

    function setOpen(open) {
      menu.classList.toggle("hidden", !open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      host.classList.toggle("is-open", !!open);
    }

    CAL_ENTRY_COLOR_PALETTE.forEach(function (color) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "event-sheet-color-swatch" + (color ? "" : " event-sheet-color-swatch--default");
      btn.setAttribute("role", "radio");
      btn.setAttribute("data-color", color);
      btn.setAttribute("aria-label", color ? "Цвет " + color : "По умолчанию");
      btn.title = color ? color : "По умолчанию";
      if (color) btn.style.setProperty("--swatch-color", color);
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        selected = color;
        paintSwatches();
        setOpen(false);
      });
      swatches.appendChild(btn);
    });
    menu.appendChild(swatches);

    toggle.addEventListener("click", function () {
      setOpen(menu.classList.contains("hidden"));
    });

    paintSwatches();
    host.appendChild(toggle);
    host.appendChild(menu);
    return {
      getColor: function () {
        return selected;
      },
    };
  }

  var entryColorPickerApi = null;

  var CAL_DRAG_THRESHOLD_PX = 6;
  var CAL_RESIZE_MIN_MS = 15 * 60 * 1000;
  var calendarSuppressClickUntil = 0;
  var calendarGestureBusy = false;

  function calendarSuppressClick(ms) {
    calendarSuppressClickUntil = Date.now() + (ms || 400);
  }

  function calendarConsumeSuppressClick() {
    if (Date.now() < calendarSuppressClickUntil) {
      calendarSuppressClickUntil = 0;
      return true;
    }
    return false;
  }

  function openCreateEventAtMs(startMs) {
    var snapped = snapMsTo15(startMs);
    var day0 = dayStartMs(localDateIsoFromMs(snapped));
    var dayEnd = day0 + TIMELINE_END_MIN * 60000;
    var endMs = Math.min(snapped + 60 * 60 * 1000, dayEnd);
    if (endMs <= snapped) endMs = snapped + CAL_RESIZE_MIN_MS;
    openEventModal({
      start: { dateTime: new Date(snapped).toISOString() },
      end: { dateTime: new Date(endMs).toISOString() },
    });
  }

  function timelineClientYToStartMs(dateIso, hostEl, clientY, hourPx) {
    if (!hostEl || !dateIso || !hourPx) return null;
    var rect = hostEl.getBoundingClientRect();
    var y = clientY - rect.top;
    var minutesFromStart = (y / hourPx) * 60;
    var absMin = TIMELINE_START_MIN + minutesFromStart;
    absMin = Math.max(TIMELINE_START_MIN, Math.min(TIMELINE_END_MIN - 15, absMin));
    return snapMsTo15(dayStartMs(dateIso) + absMin * 60000);
  }

  function timelineClientYToEndMs(dateIso, hostEl, clientY, hourPx, startMs) {
    if (!hostEl || !dateIso || !hourPx || startMs == null) return null;
    var rect = hostEl.getBoundingClientRect();
    var y = clientY - rect.top;
    var minutesFromStart = (y / hourPx) * 60;
    var absMin = TIMELINE_START_MIN + minutesFromStart;
    absMin = Math.max(TIMELINE_START_MIN + 15, Math.min(TIMELINE_END_MIN, absMin));
    var endMs = snapMsTo15(dayStartMs(dateIso) + absMin * 60000);
    if (endMs < startMs + CAL_RESIZE_MIN_MS) endMs = startMs + CAL_RESIZE_MIN_MS;
    var dayEnd = dayStartMs(dateIso) + TIMELINE_END_MIN * 60000;
    if (endMs > dayEnd) endMs = dayEnd;
    if (endMs < startMs + CAL_RESIZE_MIN_MS) endMs = startMs + CAL_RESIZE_MIN_MS;
    return endMs;
  }

  function applyTimedEntryLayout(el, dateIso, startMs, endMs, hourPx) {
    if (!el || !dateIso || startMs == null || !hourPx) return;
    var span = timelineSpanForRange(dateIso, startMs, endMs || startMs + CAL_RESIZE_MIN_MS);
    if (!span || span.zone !== "grid") return;
    el.style.top = (span.startMin / 60) * hourPx + "px";
    el.style.height = Math.max(18, ((span.endMin - span.startMin) / 60) * hourPx - 2) + "px";
  }

  function showCalendarToast(msg) {
    var text = String(msg || "").trim() || "Ошибка календаря";
    try {
      var tg = getTelegramWebApp();
      if (tg && typeof tg.showAlert === "function") {
        tg.showAlert(text);
        return;
      }
    } catch (_) {}
    window.alert(text);
  }

  function patchLocalCalendarEntryTimes(ev, startMs, endMs) {
    if (!ev || startMs == null || endMs == null) return;
    var startIso = new Date(startMs).toISOString();
    var endIso = new Date(endMs).toISOString();
    if (!ev.start) ev.start = {};
    if (!ev.end) ev.end = {};
    ev.start.dateTime = startIso;
    delete ev.start.date;
    ev.end.dateTime = endIso;
    delete ev.end.date;
    function patchList(list) {
      (list || []).forEach(function (item) {
        if (!item || String(item.id) !== String(ev.id)) return;
        if (!item.start) item.start = {};
        if (!item.end) item.end = {};
        item.start.dateTime = startIso;
        delete item.start.date;
        item.end.dateTime = endIso;
        delete item.end.date;
      });
    }
    if (lastMeetingsLeft) patchList(lastMeetingsLeft.events);
    if (lastMeetingsRight) patchList(lastMeetingsRight.events);
    if (lastMeetingsRange) patchList(lastMeetingsRange.events);
  }

  async function persistCalendarEntryTimes(ev, startMs, endMs) {
    if (!ev || startMs == null || endMs == null || endMs <= startMs) return;
    var startIso = new Date(startMs).toISOString();
    var endIso = new Date(endMs).toISOString();
    patchLocalCalendarEntryTimes(ev, startMs, endMs);
    try {
      var isTask = calendarEntryIsTask(ev);
      var tid = isTask ? calendarTaskId(ev) : "";
      if (isTask && tid) {
        await apiFetch("/calendar/tasks/" + encodeURIComponent(tid), {
          method: "PATCH",
          body: JSON.stringify({ start: startIso, end: endIso }),
        });
      } else {
        if (!ev.id) throw new Error("Нет id встречи");
        await apiFetch("/calendar/events/" + encodeURIComponent(ev.id), {
          method: "PUT",
          body: JSON.stringify({
            calendar_id: ev.calendar_id || "primary",
            start: startIso,
            end: endIso,
          }),
        });
      }
      await loadActual();
    } catch (err) {
      showCalendarToast((err && err.message) || "Не удалось сохранить время");
      try {
        await loadActual();
      } catch (_) {}
    }
  }

  function clearCalendarGestureState() {
    calendarGestureBusy = false;
    calendarSuppressClickUntil = 0;
    document.documentElement.classList.remove("cal-gesture-active");
    document.querySelectorAll(".cal-entry--interactive.is-dragging, .cal-entry--interactive.is-resizing").forEach(function (n) {
      n.classList.remove("is-dragging", "is-resizing");
    });
    document.querySelectorAll(".meetings-month-cell.is-drop-target").forEach(function (c) {
      c.classList.remove("is-drop-target");
    });
  }

  function appendCalResizeHandle(el) {
    var handle = document.createElement("div");
    handle.className = "cal-resize-handle";
    handle.setAttribute("aria-hidden", "true");
    el.appendChild(handle);
    return handle;
  }

  function bindTimedEntryInteractions(el, ev, ctx) {
    if (!el || !ev || eventIsAllDay(ev)) return;
    var startMs0 = eventStartMs(ev);
    var endMs0 = eventEndMs(ev);
    if (startMs0 == null) return;
    if (endMs0 == null || endMs0 <= startMs0) endMs0 = startMs0 + CAL_RESIZE_MIN_MS;
    var duration0 = endMs0 - startMs0;
    var handle = appendCalResizeHandle(el);
    el.classList.add("cal-entry--interactive");

    function dayHosts() {
      return ctx.dayHosts || [];
    }

    function resolveHostAt(clientX) {
      var hosts = dayHosts();
      if (!hosts.length) {
        return { el: ctx.axisHost, dateIso: ctx.dateIso };
      }
      for (var i = 0; i < hosts.length; i += 1) {
        var r = hosts[i].el.getBoundingClientRect();
        if (clientX >= r.left && clientX < r.right) return hosts[i];
      }
      var best = hosts[0];
      var bestDist = Infinity;
      hosts.forEach(function (h) {
        var r = h.el.getBoundingClientRect();
        var mid = (r.left + r.right) / 2;
        var d = Math.abs(clientX - mid);
        if (d < bestDist) {
          bestDist = d;
          best = h;
        }
      });
      return best;
    }

    function canCrossDays() {
      return dayHosts().length > 1;
    }

    function onPointerDown(e, mode) {
      if (e.button != null && e.button !== 0) return;
      if (calendarGestureBusy) return;
      if (e.target && e.target.closest && e.target.closest(".meeting-card-join-btn")) return;
      e.preventDefault();
      e.stopPropagation();
      calendarGestureBusy = true;
      var pointerId = e.pointerId;
      var originX = e.clientX;
      var originY = e.clientY;
      var dragging = false;
      var finished = false;
      var curStart = startMs0;
      var curEnd = endMs0;
      var curDate = ctx.dateIso;
      var axisHost = ctx.axisHost;
      var originParent = el.parentNode;
      var originLeft = el.style.left;
      var originWidth = el.style.width;
      el.classList.add(mode === "resize" ? "is-resizing" : "is-dragging");
      document.documentElement.classList.add("cal-gesture-active");

      function onMove(evMove) {
        if (finished || evMove.pointerId !== pointerId) return;
        var dx = evMove.clientX - originX;
        var dy = evMove.clientY - originY;
        if (!dragging) {
          if (Math.abs(dx) < CAL_DRAG_THRESHOLD_PX && Math.abs(dy) < CAL_DRAG_THRESHOLD_PX) return;
          dragging = true;
        }
        evMove.preventDefault();
        if (mode === "resize") {
          var endMs = timelineClientYToEndMs(
            curDate,
            axisHost,
            evMove.clientY,
            ctx.hourPx,
            curStart
          );
          if (endMs == null) return;
          curEnd = endMs;
          applyTimedEntryLayout(el, curDate, curStart, curEnd, ctx.hourPx);
          return;
        }
        if (canCrossDays()) {
          var hostInfo = resolveHostAt(evMove.clientX);
          axisHost = hostInfo.el;
          curDate = hostInfo.dateIso;
          if (el.parentNode !== axisHost) {
            axisHost.appendChild(el);
            el.style.left = ctx.view === "week" ? "1px" : "0";
            el.style.width = ctx.view === "week" ? "calc(100% - 3px)" : "100%";
          }
        }
        var newStart = timelineClientYToStartMs(curDate, axisHost, evMove.clientY, ctx.hourPx);
        if (newStart == null) return;
        var dayEnd = dayStartMs(curDate) + TIMELINE_END_MIN * 60000;
        if (newStart + duration0 > dayEnd) newStart = dayEnd - duration0;
        var dayStartTimed = dayStartMs(curDate) + TIMELINE_START_MIN * 60000;
        if (newStart < dayStartTimed) newStart = dayStartTimed;
        newStart = snapMsTo15(newStart);
        curStart = newStart;
        curEnd = newStart + duration0;
        applyTimedEntryLayout(el, curDate, curStart, curEnd, ctx.hourPx);
      }

      function finish(evUp) {
        if (finished) return;
        if (evUp && evUp.pointerId != null && evUp.pointerId !== pointerId) return;
        finished = true;
        document.removeEventListener("pointermove", onMove, true);
        document.removeEventListener("pointerup", finish, true);
        document.removeEventListener("pointercancel", finish, true);
        el.classList.remove("is-dragging", "is-resizing");
        document.documentElement.classList.remove("cal-gesture-active");
        calendarGestureBusy = false;
        if (!dragging) {
          calendarSuppressClick(450);
          if (mode === "move") openCalendarEntry(ev);
          return;
        }
        calendarSuppressClick(450);
        var changed =
          curStart !== startMs0 ||
          curEnd !== endMs0 ||
          curDate !== ctx.dateIso ||
          el.parentNode !== originParent;
        if (!changed) {
          if (originParent && el.parentNode !== originParent) {
            originParent.appendChild(el);
            el.style.left = originLeft;
            el.style.width = originWidth;
          }
          applyTimedEntryLayout(el, ctx.dateIso, startMs0, endMs0, ctx.hourPx);
          return;
        }
        persistCalendarEntryTimes(ev, curStart, curEnd);
      }

      document.addEventListener("pointermove", onMove, true);
      document.addEventListener("pointerup", finish, true);
      document.addEventListener("pointercancel", finish, true);
    }

    handle.addEventListener("pointerdown", function (e) {
      onPointerDown(e, "resize");
    });
    el.addEventListener("pointerdown", function (e) {
      if (e.target === handle || (e.target && e.target.closest && e.target.closest(".cal-resize-handle"))) {
        return;
      }
      onPointerDown(e, "move");
    });
  }

  function bindMonthChipDrag(chip, ev, getCellAtPoint) {
    if (!chip || !ev || !getCellAtPoint) return;
    var startMs0 = eventStartMs(ev);
    var endMs0 = eventEndMs(ev);
    if (startMs0 == null) return;
    if (endMs0 == null || endMs0 <= startMs0) endMs0 = startMs0 + CAL_RESIZE_MIN_MS;
    chip.classList.add("cal-entry--interactive");

    chip.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      if (calendarGestureBusy) return;
      e.preventDefault();
      e.stopPropagation();
      calendarGestureBusy = true;
      var pointerId = e.pointerId;
      var originX = e.clientX;
      var originY = e.clientY;
      var dragging = false;
      var finished = false;
      var targetIso = null;
      chip.classList.add("is-dragging");
      document.documentElement.classList.add("cal-gesture-active");

      function onMove(evMove) {
        if (finished || evMove.pointerId !== pointerId) return;
        var dx = evMove.clientX - originX;
        var dy = evMove.clientY - originY;
        if (!dragging) {
          if (Math.abs(dx) < CAL_DRAG_THRESHOLD_PX && Math.abs(dy) < CAL_DRAG_THRESHOLD_PX) return;
          dragging = true;
        }
        evMove.preventDefault();
        var cell = getCellAtPoint(evMove.clientX, evMove.clientY);
        targetIso = cell ? cell.getAttribute("data-date-iso") : null;
        document.querySelectorAll(".meetings-month-cell.is-drop-target").forEach(function (c) {
          c.classList.remove("is-drop-target");
        });
        if (cell) cell.classList.add("is-drop-target");
      }

      function finish(evUp) {
        if (finished) return;
        if (evUp && evUp.pointerId != null && evUp.pointerId !== pointerId) return;
        finished = true;
        document.removeEventListener("pointermove", onMove, true);
        document.removeEventListener("pointerup", finish, true);
        document.removeEventListener("pointercancel", finish, true);
        chip.classList.remove("is-dragging");
        document.documentElement.classList.remove("cal-gesture-active");
        document.querySelectorAll(".meetings-month-cell.is-drop-target").forEach(function (c) {
          c.classList.remove("is-drop-target");
        });
        calendarGestureBusy = false;
        calendarSuppressClick(450);
        if (!dragging) {
          openCalendarEntry(ev);
          return;
        }
        if (!targetIso) return;
        var oldDate = localDateIsoFromMs(startMs0);
        if (oldDate === targetIso) return;
        var dayDelta = dayStartMs(targetIso) - dayStartMs(oldDate);
        persistCalendarEntryTimes(ev, startMs0 + dayDelta, endMs0 + dayDelta);
      }

      document.addEventListener("pointermove", onMove, true);
      document.addEventListener("pointerup", finish, true);
      document.addEventListener("pointercancel", finish, true);
    });
  }

  function currentOpenNoteId() {
    var wrap = document.getElementById("note-editor-more-wrap");
    return wrap && wrap._shareId ? String(wrap._shareId) : "";
  }

  function defaultTaskAssignee() {
    var me = cachedMe || {};
    var name = [me.first_name, me.last_name].filter(Boolean).join(" ").trim();
    if (!name) name = me.username || me.name || "Я";
    return {
      user_id: String(me.telegram_user_id || me.id || ""),
      email: String(me.email || ""),
      name: name,
    };
  }

  function renderTimelineBandItem(opts) {
    opts = opts || {};
    var el = document.createElement("button");
    el.type = "button";
    el.className = "day-timeline-band-item" + (opts.mod ? " " + opts.mod : "");
    el.textContent = opts.title || "";
    if (opts.ev) applyEntryDisplayColor(el, opts.ev);
    if (opts.onClick) el.addEventListener("click", opts.onClick);
    return el;
  }

  function paintMeetingsList(ui, calData, reminders, paintOpts) {
    paintOpts = paintOpts || {};
    var listM = document.getElementById(ui.listId);
    var emptyM = document.getElementById(ui.emptyId);
    var errM = document.getElementById(ui.errId);
    var titleEl = ui.titleId ? document.getElementById(ui.titleId) : null;
    if (!listM) return;
    var scroller = document.querySelector(".main-scroll");
    var savedScroll = paintOpts.preserveScroll && scroller ? scroller.scrollTop : null;
    listM.innerHTML = "";
    listM.classList.add("meetings-stack--timeline");
    if (errM) {
      errM.textContent = "";
      setHidden(errM, true);
    }
    var calDate = (calData && calData.date) || "";
    if (titleEl) titleEl.textContent = formatMeetingsDayTitle(calDate);
    var dayCol = ui.dayId ? document.getElementById(ui.dayId) : null;
    if (dayCol) dayCol.classList.toggle("meetings-day--today", calDate === todayIsoLocal());
    var evs = ((calData && calData.events) || []).filter(function (ev) {
      return !meetingIsDeclined(ev);
    });
    var dayReminders = calDate ? remindersForDate(reminders, calDate) : [];
    var disconnected = calData && calData.connected === false;
    setHidden(emptyM, true);
    if (disconnected) {
      var banner = document.createElement("p");
      banner.className = "muted empty-hint day-timeline-connect";
      banner.textContent = meetingsEmptyMessage(calData);
      listM.appendChild(banner);
    }

    var nowMs = Date.now();
    var hourPx = timelineHourPx();
    var hours = (TIMELINE_END_MIN - TIMELINE_START_MIN) / 60;
    var band = document.createElement("div");
    band.className = "day-timeline-band";
    var earlyItems = [];
    var gridItems = [];

    evs.forEach(function (ev) {
      if (eventIsAllDay(ev)) {
        band.appendChild(
          renderTimelineBandItem({
            title: meetingDisplayTitle(ev),
            ev: ev,
            mod:
              (meetingNeedsRsvp(ev) ? "day-timeline-band-item--pending" : "") +
              (calendarEntryIsTask(ev) ? " day-timeline-band-item--task" : ""),
            onClick: function () {
              openCalendarEntry(ev);
            },
          })
        );
        return;
      }
      var startMs = eventStartMs(ev);
      var endMs = eventEndMs(ev);
      var span = timelineSpanForRange(calDate, startMs, endMs);
      if (!span) return;
      if (span.zone === "early") {
        var earlyTitle =
          (formatEventTime(ev) || "") +
          (ev.summary ? " · " + meetingDisplayTitle(ev) : "");
        earlyItems.push(
          renderTimelineBandItem({
            title: earlyTitle || meetingDisplayTitle(ev),
            ev: ev,
            mod:
              (meetingNeedsRsvp(ev) ? "day-timeline-band-item--pending" : "") +
              (calendarEntryIsTask(ev) ? " day-timeline-band-item--task" : ""),
            onClick: function () {
              openCalendarEntry(ev);
            },
          })
        );
        return;
      }
      gridItems.push({
        kind: "event",
        ev: ev,
        startMin: span.startMin,
        endMin: span.endMin,
        endMs: endMs,
      });
    });

    dayReminders.forEach(function (r) {
      var startMs = Date.parse(r.when_iso || "");
      if (!isFinite(startMs)) return;
      var span = timelineSpanForRange(calDate, startMs, startMs + TIMELINE_REMINDER_SPAN_MIN * 60000, {
        minSpanMin: TIMELINE_REMINDER_SPAN_MIN,
      });
      if (!span) return;
      if (span.zone === "early") {
        earlyItems.push(
          renderTimelineBandItem({
            title: (formatEventClock(r.when_iso) || "") + " · " + (r.task || "Напоминание"),
            mod: "day-timeline-band-item--reminder",
            onClick: function () {
              openReminderModal(r);
            },
          })
        );
        return;
      }
      gridItems.push({
        kind: "reminder",
        reminder: r,
        startMin: span.startMin,
        endMin: span.endMin,
        endMs: startMs,
      });
    });

    if (earlyItems.length) {
      var earlyLabel = document.createElement("p");
      earlyLabel.className = "day-timeline-band-label";
      earlyLabel.textContent = "До 7:00";
      band.appendChild(earlyLabel);
      earlyItems.forEach(function (el) {
        band.appendChild(el);
      });
    }
    if (band.childNodes.length) listM.appendChild(band);

    var timeline = document.createElement("div");
    timeline.className = "day-timeline";
    timeline.style.setProperty("--day-hour-h", hourPx + "px");
    var gutter = document.createElement("div");
    gutter.className = "day-timeline-gutter";
    var canvas = document.createElement("div");
    canvas.className = "day-timeline-canvas";
    canvas.style.height = hours * hourPx + "px";

    for (var h = 0; h <= hours; h += 1) {
      var label = document.createElement("div");
      label.className = "day-timeline-label";
      label.style.top = h * hourPx + "px";
      label.textContent = formatTimelineHourLabel(7 + h);
      gutter.appendChild(label);

      var line = document.createElement("div");
      line.className = "day-timeline-hour-line";
      line.style.top = h * hourPx + "px";
      canvas.appendChild(line);
      if (h < hours) {
        var half = document.createElement("div");
        half.className = "day-timeline-hour-line day-timeline-hour-line--half";
        half.style.top = (h + 0.5) * hourPx + "px";
        canvas.appendChild(half);
      }
    }

    assignTimelineColumns(gridItems);
    gridItems.forEach(function (it) {
      var slot = document.createElement("div");
      slot.className = "day-timeline-item";
      if (it.kind === "reminder") slot.classList.add("day-timeline-item--reminder");
      if (it.kind === "event" && calendarEntryIsTask(it.ev)) {
        slot.classList.add("day-timeline-item--task");
      }
      var cols = Math.max(1, it.cols || 1);
      var col = it.col || 0;
      var top = (it.startMin / 60) * hourPx;
      var height = Math.max(22, ((it.endMin - it.startMin) / 60) * hourPx);
      slot.style.top = top + "px";
      slot.style.height = height + "px";
      slot.style.left = (col / cols) * 100 + "%";
      slot.style.width = 100 / cols + "%";
      if (it.endMs != null && it.endMs < nowMs) slot.classList.add("is-past");
      if (it.kind === "reminder") {
        slot.appendChild(renderTimelineReminder(it.reminder));
      } else {
        slot.appendChild(renderMeetingCard(it.ev, { heightPx: height, skipClick: true }));
        bindTimedEntryInteractions(slot, it.ev, {
          view: "day",
          dateIso: calDate,
          hourPx: hourPx,
          axisHost: canvas,
          dayHosts: paintOpts.dayHosts || null,
        });
      }
      canvas.appendChild(slot);
    });

    if (paintOpts.dayHosts && calDate) {
      paintOpts.dayHosts.push({ el: canvas, dateIso: calDate });
    }
    canvas.addEventListener("click", function (e) {
      if (calendarConsumeSuppressClick()) return;
      if (e.target !== canvas) return;
      var startMs = timelineClientYToStartMs(calDate, canvas, e.clientY, hourPx);
      if (startMs == null) return;
      openCreateEventAtMs(startMs);
    });

    var showNow = !!(ui.showNow && calDate && calDate === todayIsoLocal());
    if (showNow) {
      var now = new Date();
      var nowMin = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
      if (nowMin >= TIMELINE_START_MIN && nowMin <= TIMELINE_END_MIN) {
        var nowTop = ((nowMin - TIMELINE_START_MIN) / 60) * hourPx;
        canvas.appendChild(renderDayTimelineNow(nowTop));
      }
    }

    timeline.appendChild(gutter);
    timeline.appendChild(canvas);
    listM.appendChild(timeline);

    var scrollKey = ui.listId + ":" + calDate;
    if (savedScroll != null && scroller) {
      requestAnimationFrame(function () {
        scroller.scrollTop = savedScroll;
      });
    } else if (showNow && scrollKey !== timelineScrolledKey) {
      timelineScrolledKey = scrollKey;
      requestAnimationFrame(function () {
        var nowEl = listM.querySelector(".day-timeline-now");
        if (nowEl && nowEl.scrollIntoView) {
          nowEl.scrollIntoView({ block: "center", inline: "nearest" });
        }
      });
    } else {
      timelineScrolledKey = scrollKey;
    }
  }

  async function loadMeetingsInto(ui, dateIso) {
    var errM = document.getElementById(ui.errId);
    paintMeetingsListSkeleton(ui, dateIso);
    var calData;
    try {
      calData = await fetchCalendarDay(dateIso);
    } catch (e) {
      if (errM) {
        errM.textContent = e.message || String(e);
        setHidden(errM, false);
      }
      calData = { events: [], connected: false };
    }
    if (calData && calData.date && ui.trackDate) {
      meetingsPageDate = calData.date;
    }
    paintMeetingsList(ui, calData, lastRemindersItems);
    syncMeetingsTodayBtn();
    return calData;
  }

  var lastMeetingsLeft = null;
  var lastMeetingsRight = null;
  var lastRemindersItems = [];
  var meetingsNowTimer = null;

  var meetingsUiMain = {
    listId: "meetings-list",
    emptyId: "meetings-empty",
    errId: "meetings-error",
    titleId: "meetings-day-left-title",
    dayId: "meetings-day-left",
    trackDate: true,
    showNow: true,
  };

  var meetingsUiNext = {
    listId: "meetings-list-next",
    emptyId: "meetings-empty-next",
    errId: null,
    titleId: "meetings-day-right-title",
    dayId: "meetings-day-right",
    trackDate: false,
    showNow: true,
  };

  function formatMeetingsHead(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso + "T12:00:00");
      if (isNaN(d.getTime())) return "";
      return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
    } catch (_) {
      return "";
    }
  }

  function formatMeetingsHeadRange(isoA, isoB) {
    if (!isoA) return "";
    if (!isoB || isoB === isoA) return formatMeetingsHead(isoA);
    try {
      var a = new Date(isoA + "T12:00:00");
      var b = new Date(isoB + "T12:00:00");
      if (isNaN(a.getTime()) || isNaN(b.getTime())) return formatMeetingsHead(isoA);
      var sameMonth = a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear();
      var left = a.toLocaleDateString("ru-RU", {
        day: "numeric",
        month: sameMonth ? undefined : "short",
      });
      var right = b.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
      return left + "–" + right;
    } catch (_) {
      return formatMeetingsHead(isoA);
    }
  }

  function formatMeetingsDayTitle(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso + "T12:00:00");
      if (isNaN(d.getTime())) return "";
      var dayMonth = d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
      var today = todayIsoLocal();
      if (iso === today) return "Сегодня · " + dayMonth;
      if (iso === addDaysIso(today, 1)) return "Завтра · " + dayMonth;
      if (iso === addDaysIso(today, -1)) return "Вчера · " + dayMonth;
      var wd = d.toLocaleDateString("ru-RU", { weekday: "short" });
      return wd + " · " + dayMonth;
    } catch (_) {
      return "";
    }
  }

  function syncMeetingsHead(leftIso, rightIso) {
    var dual = isDesktopLayout();
    var head = document.getElementById("meetings-head-title");
    var prev = document.getElementById("meetings-prev");
    var next = document.getElementById("meetings-next");
    if (head) {
      if (meetingsViewMode === "week") {
        head.textContent = formatMeetingsWeekHead(leftIso, rightIso || addDaysIso(leftIso, 6));
      } else if (meetingsViewMode === "month") {
        head.textContent = formatMeetingsMonthHead(leftIso);
      } else {
        head.textContent = dual && rightIso ? formatMeetingsHeadRange(leftIso, rightIso) : formatMeetingsHead(leftIso);
      }
    }
    if (prev) {
      prev.setAttribute(
        "aria-label",
        meetingsViewMode === "week"
          ? "Предыдущая неделя"
          : meetingsViewMode === "month"
            ? "Предыдущий месяц"
            : dual
              ? "Предыдущие два дня"
              : "Предыдущий день"
      );
    }
    if (next) {
      next.setAttribute(
        "aria-label",
        meetingsViewMode === "week"
          ? "Следующая неделя"
          : meetingsViewMode === "month"
            ? "Следующий месяц"
            : dual
              ? "Следующие два дня"
              : "Следующий день"
      );
    }
    var board = document.getElementById("meetings-board");
    if (board) board.classList.toggle("meetings-board--dual", dual && meetingsViewMode === "day");
    syncMeetingsViewSurfaces();
    syncMeetingsTodayBtn();
  }

  function updateMeetingsBadge(leftData, rightData) {
    var badgeM = document.getElementById("meetings-badge");
    if (!badgeM) return;
    var n =
      (((leftData && leftData.events) || []).length) +
      (((rightData && rightData.events) || []).length);
    setHidden(badgeM, n <= 0);
    badgeM.textContent = String(n);
  }

  function ensureMeetingsNowTimer() {
    if (meetingsNowTimer) return;
    meetingsNowTimer = window.setInterval(function () {
      if (meetingsViewMode === "week" && lastMeetingsRange) {
        paintMeetingsWeek(lastMeetingsRange);
        return;
      }
      if (lastMeetingsLeft && lastMeetingsLeft.date === todayIsoLocal()) {
        var hostsA = [];
        paintMeetingsList(meetingsUiMain, lastMeetingsLeft, lastRemindersItems, {
          preserveScroll: true,
          dayHosts: hostsA,
        });
        if (lastMeetingsRight) {
          paintMeetingsList(meetingsUiNext, lastMeetingsRight, lastRemindersItems, {
            preserveScroll: true,
            dayHosts: hostsA,
          });
        }
      } else if (lastMeetingsRight && lastMeetingsRight.date === todayIsoLocal()) {
        var hostsB = [];
        if (lastMeetingsLeft) {
          paintMeetingsList(meetingsUiMain, lastMeetingsLeft, lastRemindersItems, {
            preserveScroll: true,
            dayHosts: hostsB,
          });
        }
        paintMeetingsList(meetingsUiNext, lastMeetingsRight, lastRemindersItems, {
          preserveScroll: true,
          dayHosts: hostsB,
        });
      }
    }, 60000);
  }

  function paintMeetingsBoard(leftData, rightData, reminders) {
    var dual = isDesktopLayout();
    if (reminders !== undefined) lastRemindersItems = reminders || [];
    lastMeetingsLeft = leftData || { events: [] };
    lastMeetingsRight = dual ? rightData || { events: [], date: addDaysIso((leftData && leftData.date) || meetingsPageDate, 1) } : null;
    var leftIso = (leftData && leftData.date) || "";
    var rightIso = lastMeetingsRight && lastMeetingsRight.date;
    syncMeetingsHead(leftIso, rightIso);
    var dayHosts = [];
    paintMeetingsList(meetingsUiMain, lastMeetingsLeft, lastRemindersItems, { dayHosts: dayHosts });
    if (dual) paintMeetingsList(meetingsUiNext, lastMeetingsRight, lastRemindersItems, { dayHosts: dayHosts });
    updateMeetingsBadge(lastMeetingsLeft, dual ? lastMeetingsRight : null);
    ensureMeetingsNowTimer();
  }

  function updateMeetingsRangeBadge(events) {
    var badgeM = document.getElementById("meetings-badge");
    if (!badgeM) return;
    var n = (events || []).filter(function (ev) {
      return !meetingIsDeclined(ev);
    }).length;
    setHidden(badgeM, n <= 0);
    badgeM.textContent = String(n);
  }

  function paintMeetingsWeek(rangeData) {
    var host = document.getElementById("meetings-week");
    if (!host) return;
    lastMeetingsRange = rangeData || lastMeetingsRange || { events: [] };
    var range = meetingsVisibleRange();
    var days = eachIsoDay(range.from, range.to);
    var events = (lastMeetingsRange.events || []).filter(function (ev) {
      return !meetingIsDeclined(ev);
    });
    syncMeetingsHead(range.from, range.to);
    updateMeetingsRangeBadge(events);
    if (lastMeetingsRange.connected === false && !events.length) {
      host.innerHTML = "";
      var banner = document.createElement("p");
      banner.className = "muted empty-hint day-timeline-connect";
      banner.textContent = meetingsEmptyMessage(lastMeetingsRange);
      host.appendChild(banner);
      return;
    }
    var hourPx = weekHourPx();
    var hours = (TIMELINE_END_MIN - TIMELINE_START_MIN) / 60;
    var gridH = hours * hourPx;
    var today = todayIsoLocal();
    var wrap = document.createElement("div");
    wrap.className = "meetings-week-scroll";
    var grid = document.createElement("div");
    grid.className = "meetings-week-grid";
    grid.style.setProperty("--week-hour-h", hourPx + "px");

    var head = document.createElement("div");
    head.className = "meetings-week-head";
    head.appendChild(document.createElement("div"));
    days.forEach(function (iso) {
      var d = dateFromIso(iso);
      var cell = document.createElement("div");
      cell.className = "meetings-week-dow" + (iso === today ? " is-today" : "");
      var name = document.createElement("span");
      name.className = "meetings-week-dow-name";
      name.textContent = weekdayShort(iso);
      var num = document.createElement("span");
      num.className = "meetings-week-dow-num";
      num.textContent = String(d.getDate());
      cell.appendChild(name);
      cell.appendChild(num);
      head.appendChild(cell);
    });

    var allday = document.createElement("div");
    allday.className = "meetings-week-allday";
    allday.appendChild(document.createElement("div"));
    days.forEach(function (iso) {
      var cell = document.createElement("div");
      cell.className = "meetings-week-allday-cell";
      eventsForDate(events, iso).forEach(function (ev) {
        if (!eventIsAllDay(ev)) return;
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className =
          withTaskClass("meetings-week-chip", ev) +
          (meetingNeedsRsvp(ev) ? " meetings-week-chip--pending" : "");
        chip.textContent = meetingDisplayTitle(ev);
        chip.addEventListener("click", function (e) {
          e.stopPropagation();
          openCalendarEntry(ev);
        });
        applyEntryDisplayColor(chip, ev);
        cell.appendChild(chip);
      });
      allday.appendChild(cell);
    });

    var body = document.createElement("div");
    body.className = "meetings-week-body";
    var gutter = document.createElement("div");
    gutter.className = "meetings-week-gutter";
    gutter.style.height = gridH + "px";
    for (var h = TIMELINE_START_MIN / 60; h < TIMELINE_END_MIN / 60; h += 1) {
      var lab = document.createElement("div");
      lab.className = "meetings-week-hour";
      lab.textContent = formatTimelineHourLabel(h);
      gutter.appendChild(lab);
    }
    body.appendChild(gutter);

    var weekDayHosts = [];
    days.forEach(function (iso) {
      var col = document.createElement("div");
      col.className = "meetings-week-col";
      col.style.height = gridH + "px";
      col.setAttribute("data-date-iso", iso);
      weekDayHosts.push({ el: col, dateIso: iso });
      var timed = [];
      eventsForDate(events, iso).forEach(function (ev) {
        if (eventIsAllDay(ev)) return;
        var span = timelineSpanForRange(iso, eventStartMs(ev), eventEndMs(ev));
        if (!span || span.zone !== "grid") return;
        timed.push({ ev: ev, startMin: span.startMin, endMin: span.endMin });
      });
      assignTimelineColumns(timed);
      timed.forEach(function (it) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          withTaskClass("meetings-week-event", it.ev) +
          (meetingNeedsRsvp(it.ev) ? " meetings-week-event--pending" : "");
        var top = (it.startMin / 60) * hourPx;
        var height = Math.max(18, ((it.endMin - it.startMin) / 60) * hourPx - 2);
        var width = 100 / (it.cols || 1);
        btn.style.top = top + "px";
        btn.style.height = height + "px";
        btn.style.left = "calc(" + (it.col || 0) * width + "% + 1px)";
        btn.style.width = "calc(" + width + "% - 3px)";
        btn.textContent = meetingDisplayTitle(it.ev);
        applyEntryDisplayColor(btn, it.ev);
        bindTimedEntryInteractions(btn, it.ev, {
          view: "week",
          dateIso: iso,
          hourPx: hourPx,
          axisHost: col,
          dayHosts: weekDayHosts,
        });
        col.appendChild(btn);
      });
      col.addEventListener("click", function (e) {
        if (calendarConsumeSuppressClick()) return;
        if (e.target !== col) return;
        var startMs = timelineClientYToStartMs(iso, col, e.clientY, hourPx);
        if (startMs == null) return;
        openCreateEventAtMs(startMs);
      });
      if (iso === today) {
        var nowMs = Date.now();
        var nowMin = (nowMs - dayStartMs(iso)) / 60000 - TIMELINE_START_MIN;
        if (nowMin >= 0 && nowMin <= TIMELINE_END_MIN - TIMELINE_START_MIN) {
          var nowEl = document.createElement("div");
          nowEl.className = "meetings-week-now";
          nowEl.style.top = (nowMin / 60) * hourPx + "px";
          col.appendChild(nowEl);
        }
      }
      body.appendChild(col);
    });

    grid.appendChild(head);
    grid.appendChild(allday);
    grid.appendChild(body);
    wrap.appendChild(grid);
    host.innerHTML = "";
    host.appendChild(wrap);
  }

  function paintMeetingsMonth(rangeData) {
    var host = document.getElementById("meetings-month");
    if (!host) return;
    lastMeetingsRange = rangeData || lastMeetingsRange || { events: [] };
    var anchor = meetingsPageDate || todayIsoLocal();
    var gridRange = monthGridRange(anchor);
    var days = eachIsoDay(gridRange.from, gridRange.to);
    var events = (lastMeetingsRange.events || []).filter(function (ev) {
      return !meetingIsDeclined(ev);
    });
    syncMeetingsHead(anchor, null);
    updateMeetingsRangeBadge(events);
    host.innerHTML = "";
    if (lastMeetingsRange.connected === false && !events.length) {
      var banner = document.createElement("p");
      banner.className = "muted empty-hint day-timeline-connect";
      banner.textContent = meetingsEmptyMessage(lastMeetingsRange);
      host.appendChild(banner);
      return;
    }
    var wd = document.createElement("div");
    wd.className = "meetings-month-wd";
    ["пн", "вт", "ср", "чт", "пт", "сб", "вс"].forEach(function (label) {
      var s = document.createElement("span");
      s.textContent = label;
      wd.appendChild(s);
    });
    var grid = document.createElement("div");
    grid.className = "meetings-month-grid";
    var today = todayIsoLocal();
    var monthPrefix = startOfMonthIso(anchor).slice(0, 7);
    days.forEach(function (iso) {
      var cell = document.createElement("div");
      cell.className = "meetings-month-cell";
      cell.setAttribute("role", "button");
      cell.tabIndex = 0;
      cell.setAttribute("data-date-iso", iso);
      if (iso.slice(0, 7) !== monthPrefix) cell.classList.add("meetings-month-cell--out");
      if (iso === today) cell.classList.add("is-today");
      var num = document.createElement("span");
      num.className = "meetings-month-num";
      num.textContent = String(dateFromIso(iso).getDate());
      cell.appendChild(num);
      var dayEvs = eventsForDate(events, iso);
      dayEvs.slice(0, 3).forEach(function (ev) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className =
          withTaskClass("meetings-month-chip", ev) +
          (meetingNeedsRsvp(ev) ? " meetings-month-chip--pending" : "");
        chip.textContent = meetingDisplayTitle(ev);
        applyEntryDisplayColor(chip, ev);
        if (!eventIsAllDay(ev) && eventStartMs(ev) != null) {
          bindMonthChipDrag(chip, ev, function (x, y) {
            var nodes = host.querySelectorAll(".meetings-month-cell[data-date-iso]");
            for (var i = 0; i < nodes.length; i += 1) {
              var r = nodes[i].getBoundingClientRect();
              if (x >= r.left && x < r.right && y >= r.top && y < r.bottom) return nodes[i];
            }
            return null;
          });
        } else {
          chip.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (calendarConsumeSuppressClick()) return;
            openCalendarEntry(ev);
          });
        }
        cell.appendChild(chip);
      });
      if (dayEvs.length > 3) {
        var more = document.createElement("span");
        more.className = "meetings-month-more";
        more.textContent = "+" + (dayEvs.length - 3);
        cell.appendChild(more);
      }
      cell.addEventListener("click", function () {
        if (calendarConsumeSuppressClick()) return;
        openCreateEventAtMs(dayStartMs(iso) + 9 * 60 * 60000);
      });
      cell.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        openCreateEventAtMs(dayStartMs(iso) + 9 * 60 * 60000);
      });
      grid.appendChild(cell);
    });
    host.appendChild(wd);
    host.appendChild(grid);
  }

  function heroDateLineFor(iso) {
    if (!iso) return heroDateLine();
    try {
      return new Date(iso + "T12:00:00").toLocaleDateString("ru-RU", {
        weekday: "long",
        day: "numeric",
        month: "long",
      });
    } catch (_) {
      return heroDateLine();
    }
  }

  function linkifyEscaped(html) {
    return String(html || "").replace(
      /(https?:\/\/[^\s<]+[^<.,:;"')\]\s])/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
    );
  }

  function matchQuotedNoteValue(s, key) {
    var re = new RegExp(
      "['\"]" + key + "['\"]\\s*:\\s*(['\"])((?:\\\\.|(?!\\1)[^\\\\])*)\\1",
      "i"
    );
    var m = String(s || "").match(re);
    if (!m) return "";
    return m[2]
      .replace(/\\n/g, "\n")
      .replace(/\\t/g, "\t")
      .replace(/\\'/g, "'")
      .replace(/\\"/g, '"')
      .trim();
  }

  function pickBodyFromNoteObject(o) {
    if (!o || typeof o !== "object") return "";
    var keys = ["description", "body", "content", "text", "note", "message"];
    for (var i = 0; i < keys.length; i++) {
      var v = o[keys[i]];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    if (o.response && typeof o.response === "object") {
      var nested = pickBodyFromNoteObject(o.response);
      if (nested) return nested;
    }
    if (typeof o.response === "string" && o.response.trim()) return o.response.trim();
    return "";
  }

  function tryExtractNoteContent(s) {
    var t = String(s || "").trim();
    if (!t) return null;
    if (t.charAt(0) === "{") {
      try {
        var body = pickBodyFromNoteObject(JSON.parse(t));
        if (body) return body;
      } catch (_) {}
    }
    var fenced = t.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fenced) {
      try {
        var body2 = pickBodyFromNoteObject(JSON.parse(fenced[1].trim()));
        if (body2) return body2;
      } catch (_) {}
    }
    var desc =
      matchQuotedNoteValue(t, "description") ||
      matchQuotedNoteValue(t, "body") ||
      matchQuotedNoteValue(t, "content");
    if (desc && (desc.length > 12 || t.length < 600)) return desc;
    return null;
  }

  function stripNoteMetadataArtifacts(s) {
    var lines = String(s || "").split("\n");
    var out = [];
    var metaRe =
      /^\s*['"]?(response|status|headers|data|error|detail|request_id|ok|success|result)['"]?\s*[:=]\s*/i;
    var dictOnlyRe = /^\s*[\{\}\[\],]+\s*$/;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (metaRe.test(line) && line.length < 240) continue;
      if (dictOnlyRe.test(line)) continue;
      out.push(line);
    }
    return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  function sanitizeNoteBody(raw) {
    if (raw == null) return "";
    var s = String(raw).replace(/\r\n/g, "\n").trim();
    if (!s) return "";
    var extracted = tryExtractNoteContent(s);
    if (extracted) s = extracted;
    return stripNoteMetadataArtifacts(s);
  }

  function sanitizeNoteTitle(raw) {
    if (raw == null) return "";
    var s = String(raw).replace(/\r\n/g, "\n").trim();
    if (!s) return "";
    if (s.charAt(0) === "{") {
      try {
        var o = JSON.parse(s);
        var title = String(o.title || o.name || "").trim();
        if (title) return title;
      } catch (_) {}
    }
    var titleQ = matchQuotedNoteValue(s, "title");
    if (titleQ) return titleQ;
    var first = s.split("\n")[0].trim();
    if (/^['"]?(response|status|data)['"]?\s*:/i.test(first)) {
      return "";
    }
    return first.slice(0, 500);
  }

  function notePlainExcerpt(text, maxLen) {
    var s = sanitizeNoteBody(text);
    if (noteBodyHasHtml(s)) s = htmlToPlainText(s);
    s = s
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^[-*•]\s+/gm, "")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1")
      .replace(/_([^_]+)_/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
    maxLen = maxLen || 280;
    if (s.length <= maxLen) return s;
    return s.slice(0, maxLen);
  }

  function getNoteEditorBodyEl() {
    return document.getElementById("note-editor-modal-body");
  }

  function isNoteEditorModalOpen() {
    var ov = document.getElementById("note-editor-overlay");
    return !!(ov && !ov.classList.contains("hidden"));
  }

  function getActiveModalSheet() {
    var ids = ["note-editor-overlay", "tags-manage-overlay", "contacts-form-overlay", "teams-manage-overlay", "modal-overlay", "voice-overlay"];
    for (var i = 0; i < ids.length; i++) {
      var ov = document.getElementById(ids[i]);
      if (ov && !ov.classList.contains("hidden")) {
        return ov.querySelector(".modal--sheet");
      }
    }
    return null;
  }

  function getActiveModalBody() {
    var sheet = getActiveModalSheet();
    return sheet ? sheet.querySelector(".modal-body") : null;
  }

  function getNoteEditorTitle() {
    var inp = document.getElementById("note-editor-title-input");
    return inp ? String(inp.value || "") : "";
  }

  function bindNoteTitleAutoresize(el) {
    if (!el) return;
    function resize() {
      el.style.height = "auto";
      el.style.height = Math.max(el.scrollHeight, 28) + "px";
    }
    el.addEventListener("input", resize);
    resize();
  }

  function updateJournalInCache(jid, op, item) {
    if (!notesDataCache) return;
    var id = String(jid);
    function patchArr(arr) {
      if (!arr) return arr;
      return arr.map(function (x) {
        if (String(x.id) !== id) return x;
        return Object.assign({}, x, {
          preview: item.preview != null ? item.preview : x.preview,
          main_topic: item.main_topic != null ? item.main_topic : x.main_topic,
          tags: Array.isArray(item.tags) ? item.tags : x.tags,
          project: item.project !== undefined ? item.project : x.project,
          hashtags: Array.isArray(item.hashtags) ? item.hashtags : x.hashtags,
        });
      });
    }
    notesDataCache.transcriptions = patchArr(notesDataCache.transcriptions);
    notesDataCache.summaries = patchArr(notesDataCache.summaries);
    notesDataCache.journal = patchArr(notesDataCache.journal);
  }

  function journalEditorModalTitle(op) {
    var k = String(op || "");
    if (k === "obuchat_transcribe") return "Транскрипция";
    if (k === "summarize") return "Саммари";
    return operationLabel(op) || "Запись";
  }

  function journalDeleteConfirmMessage(op) {
    var k = String(op || "");
    if (k === "obuchat_transcribe") return "Удалить транскрипцию?";
    if (k === "summarize") return "Удалить саммари?";
    return "Удалить запись?";
  }

  function findJournalRowInCache(journalId) {
    var id = String(journalId || "");
    if (!id || !notesDataCache) return null;
    var pools = [
      notesDataCache.transcriptions,
      notesDataCache.summaries,
      notesDataCache.journal,
    ];
    for (var p = 0; p < pools.length; p++) {
      var arr = pools[p] || [];
      for (var i = 0; i < arr.length; i++) {
        if (String(arr[i].id) === id) return arr[i];
      }
    }
    return null;
  }

  function findSummaryForTranscript(transcriptId) {
    var id = String(transcriptId || "");
    if (!id || !notesDataCache) return null;
    var arr = notesDataCache.summaries || [];
    for (var i = 0; i < arr.length; i++) {
      if (String(arr[i].transcript_event_id) === id) return arr[i];
    }
    return null;
  }

  function openJournalById(journalId) {
    var row = findJournalRowInCache(journalId);
    if (row) {
      openJournalEditorDetail(row);
      return;
    }
    openJournalEditorDetail({ id: journalId });
  }

  function isGenericMeetingTopic(topic) {
    var t = String(topic || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, " ");
    if (!t) return true;
    return (
      t === "встреча" ||
      t === "встреча zoom" ||
      t === "zoom meeting" ||
      t === "meeting" ||
      t === "telemost" ||
      t === "telemost meeting"
    );
  }

  function appendJournalRelatedLinks(wrap, it, op, row) {
    var relatedId = null;
    var relatedLabel = "";
    if (String(op) === "summarize" && it && it.transcript_event_id) {
      relatedId = String(it.transcript_event_id);
      relatedLabel = "Транскрипция встречи";
    } else if (
      String(op) === "obuchat_transcribe" &&
      (it || row) &&
      ((it && it.related_summary_id) || (row && row.related_summary_id))
    ) {
      relatedId = String(
        (it && it.related_summary_id) || (row && row.related_summary_id) || ""
      );
      relatedLabel = "Саммари встречи";
    }
    if (!relatedId) return;
    var pad = document.createElement("div");
    pad.className = "note-editor-pad note-editor-pad--related-links";
    var link = document.createElement("button");
    link.type = "button";
    link.className = "journal-related-link-btn";
    link.textContent = relatedLabel;
    link.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openJournalById(relatedId);
    });
    pad.appendChild(link);
    var titlePad = wrap.querySelector(".note-editor-pad--title-in-body");
    var bodyPad = wrap.querySelector(".note-editor-pad--body");
    if (bodyPad && bodyPad.parentNode) bodyPad.parentNode.insertBefore(pad, bodyPad);
    else if (titlePad && titlePad.parentNode && titlePad.nextSibling) {
      titlePad.parentNode.insertBefore(pad, titlePad.nextSibling);
    }
    else wrap.appendChild(pad);
  }

  function relatedSummaryTitle(row) {
    var fromApi = String((row && row.related_summary_title) || "").trim();
    if (fromApi) return fromApi.slice(0, 120);
    var linkedSummary = findSummaryForTranscript(row && row.id);
    if (!linkedSummary) return "";
    var summaryTitle = String((linkedSummary.main_topic) || "").trim();
    if (summaryTitle) return summaryTitle.slice(0, 120);
    var summaryPreview = String((linkedSummary.preview) || "").trim();
    if (summaryPreview) {
      return summaryPreview.replace(/^#{1,6}\s+/, "").slice(0, 120);
    }
    return "";
  }

  function normalizeSummarySectionHtml(html) {
    var s = String(html || "");
    if (!s.trim()) return s;
    if (/<h[1-6]\b/i.test(s)) return s;
    return s.replace(/<b\b[^>]*>([\s\S]*?)<\/b>/gi, function (_match, inner) {
      var t = String(inner || "")
        .replace(/<[^>]+>/g, "")
        .trim();
      if (!t || t.length > 100) return "<b>" + inner + "</b>";
      if (/^[\u2600-\u27BF\uD83C-\uDBFF\uDC00-\uDFFF]/.test(t)) {
        return "<h2>" + inner + "</h2>";
      }
      return "<b>" + inner + "</b>";
    });
  }

  function journalBodyForEditor(it) {
    var body = stripJournalEmbeddedFooters(
      String((it && (it.body || it.description || it.preview)) || "")
    );
    if (
      window.NoteHtml &&
      typeof window.NoteHtml.normalizeCollapsedMarkdown === "function"
    ) {
      body = window.NoteHtml.normalizeCollapsedMarkdown(body);
    }
    return body;
  }

  function isTaskSectionHeadingText(title) {
    var t = String(title || "").trim();
    return (
      /📋\s*(Задачи|Чек-лист|Следующие действия)/.test(t) ||
      /✅\s*Выполненные задачи/.test(t)
    );
  }

  function promoteSummaryTaskSections(html) {
    var raw = String(html || "").trim();
    if (!raw || typeof DOMParser === "undefined") return raw;
    var doc = new DOMParser().parseFromString("<div>" + raw + "</div>", "text/html");
    var root = doc.body.firstChild;
    if (!root) return raw;
    var nodes = root.querySelectorAll("h1, h2, h3, h4, h5, h6");
    for (var i = 0; i < nodes.length; i++) {
      var heading = nodes[i];
      if (!isTaskSectionHeadingText(heading.textContent || "")) continue;
      var ul = heading.nextElementSibling;
      while (ul && ul.tagName !== "UL") {
        if (/^H[1-6]$/.test(ul.tagName)) break;
        ul = ul.nextElementSibling;
      }
      if (!ul || ul.tagName !== "UL") continue;
      ul.classList.add("note-task-list");
      var completed = /✅\s*Выполненные задачи/.test(String(heading.textContent || ""));
      ul.querySelectorAll(":scope > li").forEach(function (li) {
        if (li.querySelector('input[type="checkbox"]')) return;
        if (completed || li.hasAttribute("checked")) li.setAttribute("checked", "");
        else li.removeAttribute("checked");
      });
    }
    return root.innerHTML;
  }

  function prepareJournalBodyForEditor(it) {
    var clean = sanitizeNoteBody(journalBodyForEditor(it));
    if (!clean) return "";
    if (noteBodyHasHtml(clean)) {
      return promoteSummaryTaskSections(
        normalizeSummarySectionHtml(sanitizeSummaryHtml(clean))
      );
    }
    if (looksLikeMarkdown(clean)) {
      return clean;
    }
    return "<p>" + escapeHtml(clean).replace(/\n/g, "<br>") + "</p>";
  }

  function journalTitleForEditor(it, row) {
    var op = String((row && row.operation) || (it && it.operation) || "");
    if (op === "obuchat_transcribe") {
      var meeting = String((it && it.meeting_topic) || (row && row.meeting_topic) || "").trim();
      var summaryTitle = relatedSummaryTitle(row || it) || relatedSummaryTitle(it);
      if (meeting && !isGenericMeetingTopic(meeting)) {
        return sanitizeNoteTitle(meeting);
      }
      if (summaryTitle) return sanitizeNoteTitle(summaryTitle);
      if (meeting) return sanitizeNoteTitle(meeting);
    }
    var topic = String((it && it.main_topic) || (row && row.main_topic) || "").trim();
    if (topic) return sanitizeNoteTitle(topic);
    return sanitizeNoteTitle(journalCardTitle(row || it));
  }

  function resolveJournalFromCache(row) {
    if (!row || !notesDataCache) return row;
    var id = String(row.id || "");
    if (!id) return row;
    var pools = [notesDataCache.transcriptions, notesDataCache.summaries, notesDataCache.journal];
    for (var i = 0; i < pools.length; i++) {
      var arr = pools[i] || [];
      for (var j = 0; j < arr.length; j++) {
        if (String(arr[j].id) === id) return arr[j];
      }
    }
    return row;
  }

  function removeJournalFromCache(jid) {
    if (!notesDataCache) return;
    var id = String(jid);
    function filt(arr) {
      return (arr || []).filter(function (x) {
        return String(x.id) !== id;
      });
    }
    notesDataCache.transcriptions = filt(notesDataCache.transcriptions);
    notesDataCache.summaries = filt(notesDataCache.summaries);
    notesDataCache.journal = filt(notesDataCache.journal);
  }

  function removeTodoFromCache(tid) {
    if (!notesDataCache || !notesDataCache.todoist_notes) return;
    var id = String(tid);
    notesDataCache.todoist_notes = notesDataCache.todoist_notes.filter(function (x) {
      return String(x.id) !== id;
    });
  }

  function mergeTodoInCache(tid, title, description) {
    if (!notesDataCache || !notesDataCache.todoist_notes) return;
    var id = String(tid);
    (notesDataCache.todoist_notes || []).forEach(function (x) {
      if (String(x.id) === id) {
        x.title = title;
        x.content = title;
        x.description = description;
      }
    });
  }

  function prependTodoInCache(item) {
    if (!notesDataCache) notesDataCache = { todoist_notes: [] };
    if (!notesDataCache.todoist_notes) notesDataCache.todoist_notes = [];
    var id = String((item && item.id) || "").trim();
    if (!id) return;
    notesDataCache.todoist_notes = notesDataCache.todoist_notes.filter(function (x) {
      return String(x.id) !== id;
    });
    notesDataCache.todoist_notes.unshift({
      id: id,
      title: item.title || item.content || "",
      content: item.content || item.title || "",
      description: item.description || "",
    });
  }

  function noteCacheEntry(item, extra) {
    var lid = localNoteIdFrom(item);
    extra = extra || {};
    return Object.assign(
      {
        id: lid,
        title: item.title || item.content || "",
        content: item.content || item.title || "",
        description: item.description || item.body || "",
        body: item.body || item.description || "",
        todoist_id: item.todoist_id || null,
        tags: Array.isArray(item.tags) ? item.tags : [],
        project: item.project || (Array.isArray(item.tags) && item.tags[0]) || null,
        hashtags: Array.isArray(item.hashtags) ? item.hashtags : [],
        source: "local",
        kb_enabled: item.kb_enabled !== false && item.kb_enabled !== 0 && item.kb_enabled !== "0",
        owner_user_id: item.owner_user_id || item.user_id || "",
        is_owner: item.is_owner !== false,
        revision: item.revision || 1,
        updated_at: item.updated_at || "",
        members: Array.isArray(item.members) ? item.members : [],
        pinned: !!item.pinned,
        shared: !!item.shared,
        share_url: item.share_url || null,
        share_access: item.share_access || null,
        role: item.role || extra.role || "",
        is_knowledge: !!(item.is_knowledge || extra.is_knowledge),
        is_transcription: !!(item.is_transcription || extra.is_transcription),
        has_summary: !!item.has_summary,
        primary_sheet_title: item.primary_sheet_title || "",
        preview: item.preview || "",
        meta: item.meta && typeof item.meta === "object" ? item.meta : {},
        summary_generating: !!item.summary_generating,
      },
      extra
    );
  }

  function isKnowledgeNoteItem(item) {
    return !!(item && (item.is_knowledge || item.role === "knowledge"));
  }

  function isTranscriptionNoteItem(item) {
    return !!(item && (item.is_transcription || item.role === "transcription"));
  }

  function looksLikeTranscriptionNote(item) {
    if (!item) return false;
    if (isTranscriptionNoteItem(item)) return true;
    var sheet = String(item.primary_sheet_title || "");
    return sheet === "Саммари" || sheet === "Транскрипции";
  }

  function stampTranscriptionFields(dst, src) {
    dst = dst || {};
    src = src || {};
    if (!looksLikeTranscriptionNote(src) && !looksLikeTranscriptionNote(dst)) return dst;
    dst.role = "transcription";
    dst.is_transcription = true;
    dst.primary_sheet_title =
      dst.primary_sheet_title || src.primary_sheet_title || "";
    if (dst.has_summary == null) dst.has_summary = !!src.has_summary;
    if (dst.summary_generating == null) dst.summary_generating = !!src.summary_generating;
    if (!dst.meta && src.meta) dst.meta = src.meta;
    if (!dst.preview && src.preview) dst.preview = src.preview;
    return dst;
  }

  function splitTranscriptionsOutOfLocalNotes(data) {
    if (!data || !Array.isArray(data.local_notes)) return data;
    var trans = Array.isArray(data.transcriptions) ? data.transcriptions.slice() : [];
    var transIds = {};
    trans.forEach(function (n) {
      var id = localNoteIdFrom(n);
      if (id != null) transIds[String(id)] = true;
    });
    var keep = [];
    data.local_notes.forEach(function (n) {
      var id = localNoteIdFrom(n);
      var leak = looksLikeTranscriptionNote(n) || (id != null && transIds[String(id)]);
      if (!leak) {
        keep.push(n);
        return;
      }
      if (id != null && !transIds[String(id)]) {
        trans.unshift(
          Object.assign({}, n, { role: "transcription", is_transcription: true })
        );
        transIds[String(id)] = true;
      }
    });
    data.local_notes = keep;
    data.transcriptions = trans;
    return data;
  }

  function transcriptionHasSummary(item) {
    if (!item) return false;
    if (item.has_summary) return true;
    return String(item.primary_sheet_title || "") === "Саммари";
  }

  function transcriptionIsGenerating(item) {
    if (!item) return false;
    if (item.summary_generating) return true;
    var meta = item.meta;
    return !!(meta && meta.summary_generating);
  }

  function prependKnowledgeInCache(item) {
    if (!knowledgeDataCache) knowledgeDataCache = { notes: [] };
    if (!knowledgeDataCache.notes) knowledgeDataCache.notes = [];
    var lid = localNoteIdFrom(item);
    if (!lid) return;
    var prev = null;
    knowledgeDataCache.notes.forEach(function (x) {
      if (String(x.id) === String(lid)) prev = x;
    });
    knowledgeDataCache.notes = knowledgeDataCache.notes.filter(function (x) {
      return String(x.id) !== String(lid);
    });
    var merged = Object.assign({}, item);
    // PATCH/create ответы иногда приходят без tags — не затираем уже выбранный проект.
    var incomingTags = Array.isArray(merged.tags) ? merged.tags : null;
    var incomingProject = merged.project && merged.project.id ? merged.project : null;
    if ((!incomingTags || !incomingTags.length) && !incomingProject && prev) {
      var keep = noteTagsOf(prev);
      if (keep.length) {
        merged.tags = keep.slice();
        merged.project = keep[0];
      } else if (prev.project) {
        merged.project = prev.project;
        merged.tags = noteTagsOf(prev);
      }
    }
    if ((!Array.isArray(merged.hashtags) || !merged.hashtags.length) && prev && Array.isArray(prev.hashtags)) {
      merged.hashtags = prev.hashtags.slice();
    }
    if (merged.pinned == null && prev) merged.pinned = !!prev.pinned;
    var entry = noteCacheEntry(merged, { role: "knowledge", is_knowledge: true });
    knowledgeDataCache.notes.unshift(entry);
    writeMiniappCache("knowledge", knowledgeDataCache);
    renderKnowledgePaneFromData(knowledgeDataCache);
  }

  function removeKnowledgeFromCache(id) {
    if (!knowledgeDataCache || !knowledgeDataCache.notes) return;
    knowledgeDataCache.notes = knowledgeDataCache.notes.filter(function (x) {
      return String(x.id) !== String(id);
    });
    writeMiniappCache("knowledge", knowledgeDataCache);
  }

  function prependLocalInCache(item) {
    if (isKnowledgeNoteItem(item)) {
      prependKnowledgeInCache(item);
      return;
    }
    var lid = localNoteIdFrom(item);
    var alreadyTrans = false;
    if (lid && notesDataCache && Array.isArray(notesDataCache.transcriptions)) {
      alreadyTrans = notesDataCache.transcriptions.some(function (x) {
        return String(x.id) === String(lid);
      });
    }
    if (looksLikeTranscriptionNote(item) || alreadyTrans) {
      stampTranscriptionFields(item, item);
      if (lid) removeLocalFromCache(lid);
      prependTranscriptionInCache(item);
      return;
    }
    if (!notesDataCache) notesDataCache = { local_notes: [] };
    if (!notesDataCache.local_notes) notesDataCache.local_notes = [];
    if (!lid) return;
    notesDataCache.local_notes = notesDataCache.local_notes.filter(function (x) {
      return String(x.id) !== String(lid);
    });
    notesDataCache.local_notes.unshift(noteCacheEntry(item));
    persistNotesCacheToDisk();
  }

  function prependTranscriptionInCache(item) {
    if (!notesDataCache) notesDataCache = { transcriptions: [] };
    if (!notesDataCache.transcriptions) notesDataCache.transcriptions = [];
    var lid = localNoteIdFrom(item);
    if (!lid) return;
    notesDataCache.transcriptions = notesDataCache.transcriptions.filter(function (x) {
      return String(x.id) !== String(lid);
    });
    var merged = Object.assign({}, item, {
      role: "transcription",
      is_transcription: true,
    });
    notesDataCache.transcriptions.unshift(merged);
    persistNotesCacheToDisk();
  }

  function removeTranscriptionFromCache(id) {
    if (!notesDataCache || !notesDataCache.transcriptions) return;
    notesDataCache.transcriptions = notesDataCache.transcriptions.filter(function (x) {
      return String(x.id) !== String(id);
    });
    persistNotesCacheToDisk();
  }

  function paintLocalNoteCard(item) {
    var id = String(localNoteIdFrom(item) || "");
    if (!id) return false;
    var card = document.querySelector(
      '#notes-pane-notes .note-card[data-note-id="' + id.replace(/"/g, "") + '"]'
    );
    if (!card) return false;
    var inner = card.querySelector(".note-card-inner");
    if (!inner) return false;
    var titleRaw =
      sanitizeNoteTitle(item.title || item.content || "") ||
      (item.title || item.content || "(без названия)");
    var descRaw = notePlainExcerpt(item.description || item.body || "", 280);
    var title =
      inner.querySelector(".note-card-title-row > .note-card-excerpt") ||
      inner.querySelector(".note-card-excerpt:not(.note-card-excerpt--secondary)");
    if (title) title.textContent = titleRaw;
    var sec = inner.querySelector(".note-card-excerpt--secondary");
    if (descRaw) {
      if (!sec) {
        sec = document.createElement("p");
        sec.className = "note-card-excerpt note-card-excerpt--secondary muted";
        var row = title && title.closest(".note-card-title-row");
        if (row && row.parentNode) row.parentNode.insertBefore(sec, row.nextSibling);
        else if (title && title.parentNode) title.parentNode.insertBefore(sec, title.nextSibling);
        else inner.appendChild(sec);
      }
      sec.innerHTML = linkifyEscaped(escapeHtml(descRaw)) + (descRaw.length >= 280 ? "…" : "");
    } else if (sec && sec.parentNode) {
      sec.parentNode.removeChild(sec);
    }
    return true;
  }

  function syncLocalNoteInList(item) {
    prependLocalInCache(item);
    if (!paintLocalNoteCard(item) && notesDataCache) {
      renderNotesPanesFromData(notesDataCache);
    }
  }

  function removeLocalFromCache(id) {
    if (!notesDataCache || !notesDataCache.local_notes) return;
    notesDataCache.local_notes = notesDataCache.local_notes.filter(function (x) {
      return String(x.id) !== String(id);
    });
    persistNotesCacheToDisk();
  }

  async function requestNotePdf(kind, noteId) {
    var path =
      kind === "journal"
        ? "/notes/journal/" + encodeURIComponent(String(noteId)) + "/pdf"
        : "/notes/local/" + encodeURIComponent(String(noteId)) + "/pdf";
    await apiFetch(path, { method: "POST" });
    alert("Файл генерируется, по готовности будет отправлен в чат");
  }

  function openGptChat(title, context) {
    gptChatState = {
      context: (context || "").trim(),
      title: title || "Диалог с GPT",
      history: [],
    };
    var lineEl = document.getElementById("gpt-context-line");
    var firstLine = (gptChatState.context || "").split(/\n/)[0].trim() || (title || "").trim() || "…";
    if (firstLine.length > 42) firstLine = firstLine.slice(0, 42) + "…";
    if (lineEl) lineEl.textContent = "Заметка: " + firstLine;

    document.getElementById("gpt-messages").innerHTML = "";
    var mat = (gptChatState.context || "").split(/\n/)[0].trim().slice(0, 40);
    if ((gptChatState.context || "").length > 40) mat += "…";
    var intro =
      "Я загрузил материал «" +
      (mat || title || "материал") +
      "». Готов обсудить — задайте вопрос или выберите один из вариантов ниже.";
    appendGptBubble("assistant", intro);

    document.getElementById("gpt-input").value = "";
    var hints = document.getElementById("gpt-hints-block");
    if (hints) hints.classList.remove("hidden");
    document.getElementById("gpt-overlay").classList.remove("hidden");
    document.getElementById("gpt-overlay").setAttribute("aria-hidden", "false");
    syncAppOverlay();
  }

  function closeGptChat() {
    document.getElementById("gpt-overlay").classList.add("hidden");
    document.getElementById("gpt-overlay").setAttribute("aria-hidden", "true");
    syncAppOverlay();
  }

  function appendGptBubble(role, text) {
    var wrap = document.getElementById("gpt-messages");
    var b = document.createElement("div");
    b.className =
      "gpt-bubble " + (role === "user" ? "gpt-bubble--user" : "gpt-bubble--assistant");
    b.textContent = text;
    wrap.appendChild(b);
    wrap.scrollTop = wrap.scrollHeight;
  }

  async function gptSendMessage() {
    var inp = document.getElementById("gpt-input");
    var text = (inp && inp.value || "").trim();
    if (!text) return;
    var hints = document.getElementById("gpt-hints-block");
    if (hints) hints.classList.add("hidden");
    inp.value = "";
    appendGptBubble("user", text);
    try {
      var res = await apiFetch("/gpt/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          note_title: gptChatState.title || "",
          note_text: gptChatState.context || "",
          history: gptChatState.history,
        }),
      });
      var ans = (res && res.answer) || "";
      var bullets = (res && res.bullets) || [];
      var full = ans;
      if (bullets.length) {
        full += "\n\n" + bullets.map(function (x) {
          return "• " + x;
        }).join("\n");
      }
      appendGptBubble("assistant", full || "—");
      gptChatState.history.push({ role: "user", content: text });
      gptChatState.history.push({ role: "assistant", content: full || "—" });
    } catch (e) {
      appendGptBubble("assistant", "Ошибка: " + (e.message || String(e)));
    }
  }

  function closeActualCreateMenu() {
    var menu = document.getElementById("actual-create-menu");
    var btn = document.getElementById("actual-create-btn");
    if (menu) menu.classList.add("hidden");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleActualCreateMenu() {
    var menu = document.getElementById("actual-create-menu");
    var btn = document.getElementById("actual-create-btn");
    if (!menu || !btn) return;
    var open = menu.classList.contains("hidden");
    menu.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function stopVoiceStream() {
    if (voiceMediaStream && voiceMediaStream.getTracks) {
      voiceMediaStream.getTracks().forEach(function (track) {
        try {
          track.stop();
        } catch (_) {}
      });
    }
    voiceMediaStream = null;
  }

  function resetVoiceModal() {
    stopVoiceStream();
    voiceMediaRecorder = null;
    voiceChunks = [];
    voiceChatState = { history: [] };
    var status = document.getElementById("voice-status");
    var err = document.getElementById("voice-error");
    var start = document.getElementById("voice-record-start");
    var stop = document.getElementById("voice-record-stop");
    var wrap = document.getElementById("voice-transcript-wrap");
    var input = document.getElementById("voice-transcript-input");
    var chat = document.getElementById("voice-chat");
    if (status) status.textContent = "Нажмите «Начать запись» и продиктуйте сообщение.";
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    if (start) {
      start.disabled = false;
      start.classList.remove("hidden");
      start.textContent = "Начать запись";
    }
    if (stop) {
      stop.disabled = false;
      stop.classList.add("hidden");
    }
    if (wrap) wrap.classList.add("hidden");
    if (input) input.value = "";
    if (chat) {
      chat.innerHTML = "";
      chat.classList.add("hidden");
    }
  }

  function openVoiceModal() {
    resetVoiceModal();
    var ov = document.getElementById("voice-overlay");
    if (!ov) return;
    ov.classList.remove("hidden");
    ov.setAttribute("aria-hidden", "false");
    onModalSheetOpen();
    syncAppOverlay();
  }

  function closeVoiceModal() {
    resetVoiceModal();
    var ov = document.getElementById("voice-overlay");
    if (!ov) return;
    ov.classList.add("hidden");
    ov.setAttribute("aria-hidden", "true");
    onModalSheetClose();
    syncAppOverlay();
  }

  function setVoiceError(message) {
    var err = document.getElementById("voice-error");
    if (!err) return;
    err.textContent = message || "";
    err.classList.toggle("hidden", !message);
  }

  function appendVoiceBubble(role, text) {
    var chat = document.getElementById("voice-chat");
    if (!chat) return;
    chat.classList.remove("hidden");
    var b = document.createElement("div");
    b.className =
      "gpt-bubble voice-bubble " + (role === "user" ? "gpt-bubble--user" : "gpt-bubble--assistant");
    b.textContent = text;
    chat.appendChild(b);
    chat.scrollTop = chat.scrollHeight;
  }

  function preferredVoiceMimeType() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    var candidates = [
      "audio/ogg;codecs=opus",
      "audio/ogg",
      "audio/mp4",
      "audio/aac",
      "audio/webm;codecs=opus",
      "audio/webm",
    ];
    for (var i = 0; i < candidates.length; i++) {
      if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
    }
    return "";
  }

  function voiceExtFromMime(type) {
    var t = String(type || "").toLowerCase();
    if (t.indexOf("ogg") >= 0) return "ogg";
    if (t.indexOf("mp4") >= 0) return "m4a";
    if (t.indexOf("aac") >= 0) return "aac";
    if (t.indexOf("mpeg") >= 0 || t.indexOf("mp3") >= 0) return "mp3";
    return "webm";
  }

  async function uploadVoiceBlob(blob) {
    var status = document.getElementById("voice-status");
    var start = document.getElementById("voice-record-start");
    var stop = document.getElementById("voice-record-stop");
    if (status) status.textContent = "Отправляю аудио в транскрайбер Obuchat…";
    if (start) start.classList.add("hidden");
    if (stop) stop.classList.add("hidden");
    setVoiceError("");
    if (!blob || blob.size < 512) {
      if (status) status.textContent = "Запись получилась пустой.";
      setVoiceError("Не удалось записать звук. Проверьте доступ к микрофону и попробуйте ещё раз.");
      if (start) {
        start.classList.remove("hidden");
        start.disabled = false;
        start.textContent = "Записать заново";
      }
      return;
    }
    var fd = new FormData();
    var ext = voiceExtFromMime(blob.type);
    fd.append("file", blob, "voice." + ext);
    try {
      var res = await apiFetch("/voice/transcribe", { method: "POST", body: fd });
      var text = String((res && res.text) || "").trim();
      if (!text) throw new Error("Пустая транскрипция");
      var input = document.getElementById("voice-transcript-input");
      var wrap = document.getElementById("voice-transcript-wrap");
      if (input) input.value = text;
      if (wrap) wrap.classList.remove("hidden");
      if (status) status.textContent = "Проверьте текст и отправьте его в чат.";
      window.setTimeout(function () {
        if (input) input.focus();
      }, 0);
    } catch (e) {
      if (status) status.textContent = "Не удалось распознать аудио.";
      setVoiceError(e.message || String(e));
      if (start) {
        start.classList.remove("hidden");
        start.disabled = false;
        start.textContent = "Записать заново";
      }
    }
  }

  async function startVoiceRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      setVoiceError("Запись аудио не поддерживается в этом браузере.");
      return;
    }
    var start = document.getElementById("voice-record-start");
    var stop = document.getElementById("voice-record-stop");
    var status = document.getElementById("voice-status");
    setVoiceError("");
    try {
      voiceMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceChunks = [];
      var opts = {};
      var preferred = preferredVoiceMimeType();
      if (preferred) {
        opts.mimeType = preferred;
      }
      var recorder = new MediaRecorder(voiceMediaStream, opts);
      voiceMediaRecorder = recorder;
      recorder.addEventListener("dataavailable", function (e) {
        if (e.data && e.data.size) voiceChunks.push(e.data);
      });
      recorder.addEventListener("stop", function () {
        stopVoiceStream();
        var type = recorder.mimeType || "audio/webm";
        var blob = new Blob(voiceChunks, { type: type });
        uploadVoiceBlob(blob);
      });
      recorder.start();
      if (status) status.textContent = "Идет запись…";
      if (start) start.classList.add("hidden");
      if (stop) stop.classList.remove("hidden");
    } catch (e) {
      stopVoiceStream();
      setVoiceError(e.message || "Не удалось получить доступ к микрофону.");
    }
  }

  function stopVoiceRecording() {
    if (voiceMediaRecorder && voiceMediaRecorder.state === "recording") {
      var status = document.getElementById("voice-status");
      if (status) status.textContent = "Готовлю аудио…";
      voiceMediaRecorder.stop();
    }
  }

  async function sendVoiceTranscriptMessage() {
    var input = document.getElementById("voice-transcript-input");
    var text = String((input && input.value) || "").trim();
    if (!text) {
      if (input) input.focus();
      return;
    }
    var wrap = document.getElementById("voice-transcript-wrap");
    if (wrap) wrap.classList.add("hidden");
    appendVoiceBubble("user", text);
    try {
      var res = await apiFetch("/gpt/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          context: "",
          history: voiceChatState.history,
        }),
      });
      var ans = (res && res.answer) || "";
      var bullets = (res && res.bullets) || [];
      var full = ans;
      if (bullets.length) {
        full += "\n\n" + bullets.map(function (x) {
          return "• " + x;
        }).join("\n");
      }
      appendVoiceBubble("assistant", full || "—");
      voiceChatState.history.push({ role: "user", content: text });
      voiceChatState.history.push({ role: "assistant", content: full || "—" });
    } catch (e) {
      appendVoiceBubble("assistant", "Ошибка: " + (e.message || String(e)));
    }
  }

  function isLocalDevHost() {
    try {
      var h = String(window.location.hostname || "").toLowerCase();
      return h === "127.0.0.1" || h === "localhost" || h === "[::1]";
    } catch (_) {
      return false;
    }
  }

  function refreshMiniappDevFromUrl() {
    try {
      var q = window.location.search || "";
      if (/[?&]dev=0(?:&|$)/.test(q)) {
        localStorage.removeItem("miniapp_dev");
        miniappDev = false;
        return;
      }
      if (hasTelegramWebAppAuth()) {
        miniappDev = false;
        return;
      }
      if (/[?&]dev=1(?:&|$)/.test(q) || isLocalDevHost()) {
        localStorage.setItem("miniapp_dev", "1");
        miniappDev = true;
        return;
      }
      miniappDev = localStorage.getItem("miniapp_dev") === "1";
    } catch (_) {
      miniappDev = false;
    }
  }

  function getStoredSession() {
    try {
      return (localStorage.getItem(MINIAPP_SESSION_KEY) || "").trim();
    } catch (_) {
      return "";
    }
  }

  function setStoredSession(token) {
    try {
      if (token) {
        localStorage.setItem(MINIAPP_SESSION_KEY, token);
        localStorage.setItem(MINIAPP_SESSION_HINT_KEY, "1");
      } else {
        localStorage.removeItem(MINIAPP_SESSION_KEY);
        localStorage.removeItem(MINIAPP_SESSION_HINT_KEY);
      }
    } catch (_) {}
  }

  function hasSessionHint() {
    try {
      return localStorage.getItem(MINIAPP_SESSION_HINT_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function setSessionHint(ok) {
    try {
      if (ok) localStorage.setItem(MINIAPP_SESSION_HINT_KEY, "1");
      else localStorage.removeItem(MINIAPP_SESSION_HINT_KEY);
    } catch (_) {}
  }

  function isStandalonePwa() {
    try {
      return (
        window.matchMedia("(display-mode: standalone)").matches ||
        window.navigator.standalone === true
      );
    } catch (_) {
      return false;
    }
  }

  function hasTelegramWebAppAuth() {
    return getInitDataRaw().length > 0;
  }

  function authHeaders() {
    var initData = getInitDataRaw();
    if (initData) {
      return {
        Authorization: "tma " + initData,
        Accept: "application/json",
      };
    }
    if (miniappDev) {
      return {
        Authorization: "Bearer " + MINIAPP_DEV_BEARER,
        Accept: "application/json",
      };
    }
    const sess = getStoredSession();
    if (sess) {
      return {
        Authorization: "session " + sess,
        Accept: "application/json",
      };
    }
    return {
      Authorization: "tma ",
      Accept: "application/json",
    };
  }

  function looksLikeHtmlError(text) {
    var s = String(text || "")
      .replace(/^\uFEFF/, "")
      .trim();
    if (!s) return false;
    var head = s.slice(0, 320).toLowerCase();
    if (head.charAt(0) !== "<") return /502 bad gateway/i.test(s);
    return (
      head.indexOf("<html") >= 0 ||
      head.indexOf("<!doctype") >= 0 ||
      head.indexOf("<head") >= 0 ||
      head.indexOf("<body") >= 0 ||
      head.indexOf("<center") >= 0 ||
      head.indexOf("bad gateway") >= 0
    );
  }

  function httpStatusFallback(status, fallback) {
    var n = Number(status) || 0;
    if (n === 502 || n === 503 || n === 504) {
      return "Сервер временно недоступен. Попробуйте ещё раз через несколько секунд.";
    }
    return fallback || "Ошибка";
  }

  function sleepMs(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function isTransientApiError(err) {
    if (!err) return true;
    if (err.name === "AbortError") return false;
    var code = Number(err.status) || 0;
    if (
      code === 400 ||
      code === 401 ||
      code === 403 ||
      code === 404 ||
      code === 409 ||
      code === 422
    ) {
      return false;
    }
    return code === 0 || code === 500 || code === 502 || code === 503 || code === 504;
  }

  function onId(id, event, handler) {
    var el = document.getElementById(id);
    if (!el) return null;
    el.addEventListener(event, handler);
    return el;
  }

  function apiErrorMessage(body, fallback) {
    if (!body) return fallback || "Ошибка";
    var d = body.detail != null ? body.detail : body.message;
    if (typeof d === "string" && d.trim()) {
      if (looksLikeHtmlError(d)) return fallback || "Ошибка";
      return d.trim();
    }
    if (Array.isArray(d)) {
      return d
        .map(function (x) {
          if (typeof x === "string") return x;
          if (x && x.msg) return String(x.msg);
          return "";
        })
        .filter(Boolean)
        .join(". ") || fallback || "Ошибка";
    }
    if (d && typeof d === "object") {
      if (typeof d.message === "string" && d.message.trim()) return d.message.trim();
      try {
        return JSON.stringify(d);
      } catch (_) {
        return fallback || "Ошибка";
      }
    }
    return fallback || "Ошибка";
  }

  function localNoteIdFrom(item) {
    if (!item) return null;
    var raw = item.id;
    if (typeof raw === "number" && Number.isInteger(raw) && raw > 0) return raw;
    var s = String(raw == null ? "" : raw).trim();
    if (/^\d+$/.test(s)) {
      var n = parseInt(s, 10);
      return n > 0 ? n : null;
    }
    return null;
  }

  function confirmDialog(message) {
    return new Promise(function (resolve) {
      var tg = window.Telegram && window.Telegram.WebApp;
      // Вне клиента Telegram showConfirm есть, но диалог не показывается → промис висит.
      if (
        isInsideTelegramClient() &&
        tg &&
        typeof tg.showConfirm === "function"
      ) {
        try {
          tg.showConfirm(message, function (ok) {
            resolve(!!ok);
          });
          return;
        } catch (_) {}
      }
      resolve(window.confirm(message));
    });
  }

  function promptDialog(message, defaultValue) {
    return new Promise(function (resolve) {
      var existing = document.getElementById("leo-prompt-overlay");
      if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
      var ov = document.createElement("div");
      ov.id = "leo-prompt-overlay";
      ov.className = "leo-prompt-overlay";
      ov.innerHTML =
        '<div class="leo-prompt-sheet" role="dialog" aria-modal="true">' +
        '<p class="leo-prompt-message"></p>' +
        '<input type="text" class="leo-prompt-input field-input" autocomplete="off" />' +
        '<div class="leo-prompt-actions">' +
        '<button type="button" class="btn-text leo-prompt-cancel">Отмена</button>' +
        '<button type="button" class="btn leo-prompt-ok">Сохранить</button>' +
        "</div></div>";
      var msg = ov.querySelector(".leo-prompt-message");
      var input = ov.querySelector(".leo-prompt-input");
      var cancel = ov.querySelector(".leo-prompt-cancel");
      var ok = ov.querySelector(".leo-prompt-ok");
      if (msg) msg.textContent = String(message || "");
      if (input) input.value = String(defaultValue || "");
      function finish(value) {
        if (ov.parentNode) ov.parentNode.removeChild(ov);
        document.removeEventListener("keydown", onKey, true);
        resolve(value);
      }
      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          finish(null);
        } else if (e.key === "Enter") {
          e.preventDefault();
          finish(input ? input.value : "");
        }
      }
      cancel.addEventListener("click", function () {
        finish(null);
      });
      ok.addEventListener("click", function () {
        finish(input ? input.value : "");
      });
      ov.addEventListener("click", function (e) {
        if (e.target === ov) finish(null);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(ov);
      window.setTimeout(function () {
        try {
          if (input) {
            input.focus();
            input.select();
          }
        } catch (_) {}
      }, 30);
    });
  }

  function noteEditorSnapshot(title, description) {
    return {
      title: String(title || "").trim(),
      description: String(description || "").trim(),
    };
  }

  function noteDescriptionForCompare(description) {
    var api = getNoteRichEditorApi();
    if (api && typeof api.canonicalBody === "function") {
      return api.canonicalBody(description);
    }
    return String(description || "").trim();
  }

  function isTrivialNoteHtml(html) {
    var s = String(html || "")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/gi, " ")
      .replace(/\u00a0/g, " ")
      .trim();
    return !s;
  }

  function noteEditorSnapshotsEqual(a, b) {
    if (String(a.title || "").trim() !== String(b.title || "").trim()) return false;
    return (
      noteDescriptionForCompare(a.description) === noteDescriptionForCompare(b.description)
    );
  }

  function getActiveNoteEditorState() {
    var body = getNoteEditorBodyEl();
    return body && body._noteEditor ? body._noteEditor : null;
  }

  function isNoteEditorDirty() {
    var st = getActiveNoteEditorState();
    if (!st || typeof st.getCurrent !== "function") return false;
    if (st.ready === false) return false;
    return !noteEditorSnapshotsEqual(st.baseline, st.getCurrent());
  }

  function showNoteUnsavedDialog() {
    return new Promise(function (resolve) {
      var ov = document.getElementById("note-unsaved-overlay");
      var saveBtn = document.getElementById("note-unsaved-save");
      var discardBtn = document.getElementById("note-unsaved-discard");
      if (!ov || !saveBtn || !discardBtn) {
        resolve("save");
        return;
      }
      function cleanup() {
        ov.classList.add("hidden");
        ov.setAttribute("aria-hidden", "true");
        saveBtn.removeEventListener("click", onSave);
        discardBtn.removeEventListener("click", onDiscard);
        ov.removeEventListener("click", onBackdrop);
        syncAppOverlay();
      }
      function onSave() {
        cleanup();
        resolve("save");
      }
      function onDiscard() {
        cleanup();
        resolve("discard");
      }
      function onBackdrop(e) {
        if (e.target === ov) {
          cleanup();
          resolve("cancel");
        }
      }
      saveBtn.addEventListener("click", onSave);
      discardBtn.addEventListener("click", onDiscard);
      ov.addEventListener("click", onBackdrop);
      ov.classList.remove("hidden");
      ov.setAttribute("aria-hidden", "false");
      syncAppOverlay();
    });
  }

  function setNoteEditorSaveHint(msg) {
    var el = document.getElementById("note-editor-save-hint");
    var title = document.getElementById("note-editor-title-input");
    if (!el && title && title.parentNode) {
      el = document.createElement("p");
      el.id = "note-editor-save-hint";
      el.className = "error small hidden";
      title.parentNode.appendChild(el);
    }
    if (!el) return;
    if (!msg) {
      el.textContent = "";
      el.classList.add("hidden");
      return;
    }
    el.textContent = String(msg);
    el.classList.remove("hidden");
  }

  async function handleNoteEditorModalClose(opts) {
    opts = opts || {};
    if (!isNoteEditorModalOpen()) return true;
    if (!opts.skipPrompt && isNoteEditorDirty()) {
      var choice = await showNoteUnsavedDialog();
      if (choice === "cancel") return false;
      if (choice === "discard") {
        var discardBody = getNoteEditorBodyEl();
        if (discardBody) discardBody._noteEditorFlush = null;
        closeNoteEditorModal();
        return true;
      }
    }
    closeNoteEditorModal();
    return true;
  }

  async function handleNotesDetailBack() {
    if (isNoteEditorModalOpen()) {
      await handleNoteEditorModalClose();
      return;
    }
    closeNotesDetail();
  }

  async function deleteLocalNoteById(noteId) {
    var id = localNoteIdFrom({ id: noteId });
    if (!id) {
      throw new Error("Некорректный идентификатор заметки");
    }
    if (isAppOffline() || String(id).indexOf("tmp_") === 0) {
      removeLocalFromCache(id);
      removeKnowledgeFromCache(id);
      removeTranscriptionFromCache(id);
      enqueueOfflineOp({
        type: "note_delete",
        clientId: id,
        path: "/notes/local/" + encodeURIComponent(String(id)),
        method: "DELETE",
      });
      showOfflineSavedToast("Удалено офлайн");
      return;
    }
    try {
      await apiFetch("/notes/local/" + encodeURIComponent(String(id)), {
        method: "DELETE",
      });
      removeLocalFromCache(id);
      removeKnowledgeFromCache(id);
      removeTranscriptionFromCache(id);
    } catch (e) {
      if (!isTransientApiError(e)) throw e;
      removeLocalFromCache(id);
      removeKnowledgeFromCache(id);
      removeTranscriptionFromCache(id);
      enqueueOfflineOp({
        type: "note_delete",
        clientId: id,
        path: "/notes/local/" + encodeURIComponent(String(id)),
        method: "DELETE",
      });
      showOfflineSavedToast("Удалено офлайн");
    }
  }

  function apiRequestTimeoutMs(path, o) {
    if (o && Number(o.timeout) > 0) return Number(o.timeout);
    var p = String(path || "");
    if (p.indexOf("/gpt/chat") >= 0) return 180000;
    if (p.indexOf("/calendar/today") >= 0) return 20000;
    if (p.indexOf("/calendar/range") >= 0) return 30000;
    if (/\/paei(\/|\?|$)/.test(p) || p.slice(-5) === "/paei") return 180000;
    if (/\/research(\/|\?|$)/.test(p) || p.slice(-9) === "/research") return 180000;
    return 8000;
  }

  async function apiFetchOnce(path, o) {
    const headers = Object.assign({}, authHeaders(), o.headers || {});
    if (
      o.body &&
      typeof o.body === "string" &&
      !(headers["Content-Type"] || headers["content-type"])
    ) {
      headers["Content-Type"] = "application/json";
    }
    const init = Object.assign({ credentials: "same-origin" }, o, { headers });
    delete init.timeout;
    var timeoutMs = apiRequestTimeoutMs(path, o);
    var timedOut = false;
    var abortTimer = null;
    var controller = null;
    try {
      if (typeof AbortController !== "undefined" && !init.signal) {
        controller = new AbortController();
        init.signal = controller.signal;
        abortTimer = setTimeout(function () {
          timedOut = true;
          try {
            controller.abort();
          } catch (_) {}
        }, timeoutMs);
      }
      const res = await fetch(API + path, init);
      if (abortTimer) clearTimeout(abortTimer);
      const text = await res.text();
      let body = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch (_) {
        body = {
          detail: looksLikeHtmlError(text)
            ? httpStatusFallback(res.status, res.statusText)
            : text || res.statusText,
        };
      }
      if (!res.ok) {
        if (res.status === 403 && hasTelegramWebAppAuth() && !miniappDev) {
          showAccessBlocked(apiErrorMessage(body, res.statusText || "Доступ ограничен."));
        }
        if (res.status === 401 && hasTelegramWebAppAuth() && !miniappDev) {
          showTelegramAuthError(
            apiErrorMessage(body, "Ошибка авторизации Telegram. Напишите боту /start и откройте «Ассистент» снова.")
          );
        }
        if (res.status === 401 && !hasTelegramWebAppAuth() && !miniappDev) {
          setStoredSession("");
          gateLoginBootstrapped = false;
          fetch(API + "/auth/logout", {
            method: "POST",
            credentials: "same-origin",
          }).catch(function () {});
          if (isInsideTelegramClient()) {
            showTelegramAuthError(
              apiErrorMessage(body, "Telegram не передал данные для входа. Закройте окно и откройте мини-приложение снова.")
            );
          } else {
            showGate();
            bootstrapGateLogin();
          }
        }
        const msg = apiErrorMessage(
          body,
          httpStatusFallback(res.status, res.statusText || "Ошибка")
        );
        const err = new Error(msg);
        err.status = res.status;
        if (res.status === 409 && body && body.detail && typeof body.detail === "object") {
          err.detail = body.detail;
          err.conflictItem = body.detail.item || null;
        }
        if (res.status >= 500) markNetworkFailure();
        throw err;
      }
      markNetworkSuccess();
      return body;
    } catch (e) {
      if (abortTimer) clearTimeout(abortTimer);
      if (timedOut || (e && e.name === "AbortError")) {
        if (timeoutMs <= 15000) markNetworkFailure();
        var tErr = new Error(timedOut ? "Нет ответа сети" : "Запрос отменён");
        tErr.status = 0;
        throw tErr;
      }
      if (e && e.status != null) throw e;
      markNetworkFailure();
      var nErr = new Error((e && e.message) || "Нет сети");
      nErr.status = 0;
      throw nErr;
    }
  }

  async function apiFetchReal(path, opts) {
    const o = opts || {};
    const method = String(o.method || "GET").toUpperCase();
    if (isAppOffline()) {
      var offlineErr = new Error("Нет сети");
      offlineErr.status = 0;
      throw offlineErr;
    }
    var calendarGet =
      method === "GET" &&
      (String(path || "").indexOf("/calendar/today") >= 0 ||
        String(path || "").indexOf("/calendar/range") >= 0);
    const maxTries =
      method === "GET" || method === "HEAD"
        ? calendarGet
          ? 2
          : isNetworkUnreliable()
            ? 2
            : 12
        : 1;
    var lastErr = null;
    for (var i = 0; i < maxTries; i++) {
      try {
        return await apiFetchOnce(path, o);
      } catch (e) {
        lastErr = e;
        if (i >= maxTries - 1 || !isTransientApiError(e)) throw e;
        await sleepMs(Math.min(3000, 400 * Math.pow(1.6, i)));
      }
    }
    throw lastErr;
  }

  async function apiFetch(path, opts) {
    if (
      miniappDev &&
      window.__miniappDevMock &&
      typeof window.__miniappDevMock.handle === "function"
    ) {
      return window.__miniappDevMock.handle(path, opts, apiFetchReal);
    }
    return apiFetchReal(path, opts);
  }

  function showGate() {
    document.documentElement.classList.remove("boot-open-app");
    var gate = document.getElementById("gate");
    var app = document.getElementById("app");
    if (gate) gate.classList.remove("hidden");
    if (app) app.classList.add("hidden");
  }

  function showAccessBlocked(msg) {
    var status = document.getElementById("gate-login-status");
    var login = document.getElementById("gate-telegram-login");
    var err = document.getElementById("gate-login-err");
    var alt = document.querySelector("#gate .gate-alt");
    if (status) {
      status.textContent =
        msg || "Доступ к ассистенту ограничен. Дождитесь одобрения администратора.";
      status.classList.remove("hidden");
    }
    if (login) login.classList.add("hidden");
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    if (alt) alt.classList.add("hidden");
    showGate();
  }

  function showTelegramAuthError(msg) {
    var status = document.getElementById("gate-login-status");
    var login = document.getElementById("gate-telegram-login");
    var err = document.getElementById("gate-login-err");
    var alt = document.querySelector("#gate .gate-alt");
    var devHint = document.getElementById("gate-dev-hint");
    if (status) {
      status.textContent =
        msg ||
        "Не удалось войти через Telegram. Закройте мини-приложение и откройте снова из бота.";
      status.classList.remove("hidden");
    }
    if (login) login.classList.add("hidden");
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    if (alt) {
      alt.innerHTML =
        "В чате с ботом нажмите <b>/start</b>, затем кнопку меню <b>«Ассистент»</b> (≡ внизу слева).";
      alt.classList.remove("hidden");
    }
    if (devHint) devHint.classList.add("hidden");
    showGate();
  }

  function showApp() {
    var gate = document.getElementById("gate");
    var app = document.getElementById("app");
    if (gate) gate.classList.add("hidden");
    if (app) app.classList.remove("hidden");
  }

  function setHidden(el, hidden) {
    if (!el) return;
    el.classList.toggle("hidden", !!hidden);
  }

  /** Скрывает таббар и лишние отступы, когда открыт полноэкранный слой. */
  function syncAppOverlay() {
    var app = document.getElementById("app");
    if (!app) return;
    var detail = document.getElementById("notes-detail");
    var digestDetail = document.getElementById("digest-detail");
    var pay = document.getElementById("profile-payment");
    var exp = document.getElementById("profile-expenses");
    var booking = document.getElementById("profile-booking");
    var gpt = document.getElementById("gpt-overlay");
    var discussion = document.getElementById("note-discussion-overlay");
    var noteEditor = document.getElementById("note-editor-overlay");
    var modal = document.getElementById("modal-overlay");
    var voice = document.getElementById("voice-overlay");
    var noteUnsaved = document.getElementById("note-unsaved-overlay");
    var tagsSheet = document.getElementById("tags-manage-overlay");
    var contactsForm = document.getElementById("contacts-form-overlay");
    var teamsManage = document.getElementById("teams-manage-overlay");
    var chatsSplit =
      document.documentElement.classList.contains("chats-split-open") &&
      isDesktopLayout();
    var immersive =
      (detail && !detail.classList.contains("hidden")) ||
      (digestDetail && !digestDetail.classList.contains("hidden")) ||
      (noteEditor && !noteEditor.classList.contains("hidden")) ||
      (pay && !pay.classList.contains("hidden")) ||
      (exp && !exp.classList.contains("hidden")) ||
      (booking && !booking.classList.contains("hidden")) ||
      (gpt && !gpt.classList.contains("hidden")) ||
      (discussion && !discussion.classList.contains("hidden") && !chatsSplit) ||
      (modal && !modal.classList.contains("hidden")) ||
      (voice && !voice.classList.contains("hidden")) ||
      (noteUnsaved && !noteUnsaved.classList.contains("hidden")) ||
      (tagsSheet && !tagsSheet.classList.contains("hidden")) ||
      (contactsForm && !contactsForm.classList.contains("hidden")) ||
      (teamsManage && !teamsManage.classList.contains("hidden"));
    app.classList.toggle("app--immersive", !!immersive);
  }

  var SWIPE_REVEAL = 84;
  var SWIPE_THRESHOLD = 40;

  /**
   * @param {HTMLElement} innerRoot
   * @param {() => Promise<void>} onDeleteAsync
   * @param {{ removeStack?: boolean, confirmMessage?: string }} [opts]
   */
  function wrapWithSwipeDelete(innerRoot, onDeleteAsync, opts) {
    opts = opts || {};
    var removeStack = !!opts.removeStack;
    var confirmMessage = opts.confirmMessage || "";
    var stack = document.createElement("div");
    stack.className = "swipe-row";
    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "swipe-row__delete";
    delBtn.textContent = "Удалить";
    var panel = document.createElement("div");
    panel.className = "swipe-row__panel";
    panel.appendChild(innerRoot);

    stack.appendChild(delBtn);
    stack.appendChild(panel);

    var x = 0;
    var startX = 0;
    var startY = 0;
    var startOffset = 0;
    var dragging = false;
    var locked = null;
    var moved = false;

    function applyX(nx) {
      x = Math.min(20, Math.max(-SWIPE_REVEAL - 30, nx));
      panel.style.transform = "translateX(" + x + "px)";
      stack.classList.toggle("swipe-row--revealed", x < -8);
    }

    function finish() {
      if (!dragging) return;
      dragging = false;
      if (locked === "x") {
        applyX(x < -SWIPE_THRESHOLD ? -SWIPE_REVEAL : 0);
      }
      locked = null;
    }

    panel.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      startX = e.clientX;
      startY = e.clientY;
      startOffset = x;
      dragging = true;
      locked = null;
      moved = false;
    });

    panel.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      if (!locked) {
        if (Math.abs(dx) > 6 || Math.abs(dy) > 6) {
          locked = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
        }
      }
      if (locked === "x") {
        moved = true;
        e.preventDefault();
        try {
          panel.setPointerCapture(e.pointerId);
        } catch (_) {}
        applyX(startOffset + dx);
      }
    });

    panel.addEventListener("pointerup", finish);
    panel.addEventListener("pointercancel", finish);

    panel.addEventListener("click", function (e) {
      if (moved) {
        moved = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (x < -8) {
        e.preventDefault();
        e.stopPropagation();
        applyX(0);
      }
    });

    delBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      (async function () {
        if (confirmMessage && !(await confirmDialog(confirmMessage))) return;
        await onDeleteAsync();
        applyX(0);
        if (removeStack) stack.remove();
        var tw = window.Telegram && window.Telegram.WebApp;
        if (tw && tw.HapticFeedback) tw.HapticFeedback.notificationOccurred("success");
      })().catch(function (err) {
        alert(err.message || String(err));
      });
    });

    return stack;
  }

  function prefersReducedMotion() {
    return (
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function isDesktopLayout() {
    return window.matchMedia && window.matchMedia("(min-width: 960px)").matches;
  }

  var NOTE_PANE_W_LS = "leo_note_pane_w";
  var DISCUSS_PANE_W_LS = "leo_discuss_pane_w";
  var MIN_NOTE_PANE_W = 360;
  var MIN_DISCUSS_PANE_W = 280;
  var NOTE_PANE_SNAP_FULL = 28;

  function cssLenToPx(value, fallback) {
    var s = String(value || "").trim();
    var n = parseFloat(s);
    if (!isFinite(n) || n <= 0) return fallback;
    if (/rem$/i.test(s)) {
      var fs = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      return Math.round(n * fs);
    }
    return Math.round(n);
  }

  function sidebarWidthPx() {
    if (!isDesktopLayout()) return 0;
    var raw = getComputedStyle(document.documentElement).getPropertyValue("--sidebar-w");
    return cssLenToPx(raw, 244);
  }

  var SIDEBAR_COLLAPSED_LS = "leo_sidebar_collapsed";

  function isSidebarCollapsed() {
    return document.documentElement.classList.contains("sidebar-collapsed");
  }

  function syncSidebarToggleUi() {
    var collapsed = isSidebarCollapsed();
    var btn = document.getElementById("sidebar-toggle");
    var label = collapsed ? "Развернуть меню" : "Свернуть меню";
    if (btn) {
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", label);
    }
    document.querySelectorAll(".tabbar-btn").forEach(function (tab) {
      var text = tab.querySelector(".tabbar-label");
      var name = text ? String(text.textContent || "").trim() : "";
      if (collapsed && name) tab.setAttribute("title", name);
      else tab.removeAttribute("title");
    });
  }

  function applySidebarCollapsed(collapsed) {
    var root = document.documentElement;
    if (collapsed) root.classList.add("sidebar-collapsed");
    else root.classList.remove("sidebar-collapsed");
    try {
      if (collapsed) localStorage.setItem(SIDEBAR_COLLAPSED_LS, "1");
      else localStorage.removeItem(SIDEBAR_COLLAPSED_LS);
    } catch (_) {}
    syncSidebarToggleUi();
    if (isDesktopLayout()) applyNotePaneWidths();
    if (!collapsed) {
      sidebarTreeState.notesOpen = true;
      if (digestTabEnabled) sidebarTreeState.digestOpen = true;
      if (!notesDataCache) loadNotes().catch(function () {});
      if (!chatThreadsCache.length) loadChatThreads().catch(function () {});
    }
    renderSidebarTrees();
  }

  function toggleSidebarCollapsed() {
    if (!isDesktopLayout()) return;
    applySidebarCollapsed(!isSidebarCollapsed());
  }

  function noteStageWidthPx() {
    return Math.max(0, window.innerWidth - sidebarWidthPx());
  }

  function defaultDiscussWidthPx() {
    var raw = getComputedStyle(document.documentElement).getPropertyValue("--max-w");
    return cssLenToPx(raw, 416);
  }

  function readStoredPx(key) {
    try {
      var n = parseInt(localStorage.getItem(key) || "", 10);
      if (isFinite(n) && n > 0) return n;
    } catch (_) {}
    return 0;
  }

  function writeStoredPx(key, px) {
    try {
      if (px > 0) localStorage.setItem(key, String(Math.round(px)));
      else localStorage.removeItem(key);
    } catch (_) {}
  }

  function clampDiscussWidthPx(px) {
    var stage = noteStageWidthPx();
    var maxW = Math.max(MIN_DISCUSS_PANE_W, stage - MIN_NOTE_PANE_W);
    var n = Math.round(px);
    if (!isFinite(n) || n <= 0) n = defaultDiscussWidthPx();
    return Math.min(Math.max(n, MIN_DISCUSS_PANE_W), maxW);
  }

  function clampNotePaneWidthPx(px, allowFull) {
    var stage = noteStageWidthPx();
    var n = Math.round(px);
    if (!isFinite(n) || n <= 0) return 0;
    if (allowFull && n >= stage - NOTE_PANE_SNAP_FULL) return 0;
    var maxW = Math.max(MIN_NOTE_PANE_W, stage);
    return Math.min(Math.max(n, MIN_NOTE_PANE_W), maxW);
  }

  function applyNotePaneWidths() {
    bindNotePaneResizersOnce();
    var root = document.documentElement;
    var ov = document.getElementById("note-editor-overlay");
    var discussEl = document.getElementById("note-discussion-resizer");
    var noteEl = document.getElementById("note-editor-resizer");
    if (!isDesktopLayout()) {
      root.style.removeProperty("--discuss-w");
      root.style.removeProperty("--note-pane-w");
      if (ov) ov.classList.remove("note-editor-overlay--sized");
      return;
    }
    var stage = noteStageWidthPx();
    var discussW = clampDiscussWidthPx(readStoredPx(DISCUSS_PANE_W_LS) || defaultDiscussWidthPx());
    root.style.setProperty("--discuss-w", discussW + "px");
    if (discussEl) {
      discussEl.setAttribute("aria-valuemin", String(MIN_DISCUSS_PANE_W));
      discussEl.setAttribute("aria-valuemax", String(Math.max(MIN_DISCUSS_PANE_W, stage - MIN_NOTE_PANE_W)));
      discussEl.setAttribute("aria-valuenow", String(discussW));
    }
    var noteW = 0;
    if (!isNoteDiscussionOpen()) {
      noteW = clampNotePaneWidthPx(readStoredPx(NOTE_PANE_W_LS), false);
      if (noteW && ov) {
        root.style.setProperty("--note-pane-w", noteW + "px");
        ov.classList.add("note-editor-overlay--sized");
      } else {
        root.style.removeProperty("--note-pane-w");
        if (ov) ov.classList.remove("note-editor-overlay--sized");
      }
    } else {
      root.style.removeProperty("--note-pane-w");
      if (ov) ov.classList.remove("note-editor-overlay--sized");
    }
    if (noteEl) {
      noteEl.setAttribute("aria-valuemin", String(MIN_NOTE_PANE_W));
      noteEl.setAttribute("aria-valuemax", String(stage));
      noteEl.setAttribute("aria-valuenow", String(noteW || stage));
    }
  }

  function bindNotePaneResizersOnce() {
    if (document.documentElement._notePaneResizersBound) return;
    document.documentElement._notePaneResizersBound = true;
    var noteHandle = document.getElementById("note-editor-resizer");
    var discussHandle = document.getElementById("note-discussion-resizer");

    function startDrag(kind, e) {
      if (!isDesktopLayout()) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      var handle = e.currentTarget;
      var root = document.documentElement;
      var live =
        kind === "discuss"
          ? clampDiscussWidthPx(readStoredPx(DISCUSS_PANE_W_LS) || defaultDiscussWidthPx())
          : clampNotePaneWidthPx(readStoredPx(NOTE_PANE_W_LS) || noteStageWidthPx(), false);
      root.classList.add("note-pane-resizing");
      try {
        handle.setPointerCapture(e.pointerId);
      } catch (_) {}
      function onMove(ev) {
        if (kind === "discuss") {
          live = clampDiscussWidthPx(window.innerWidth - ev.clientX);
          root.style.setProperty("--discuss-w", live + "px");
          if (handle.setAttribute) handle.setAttribute("aria-valuenow", String(live));
        } else {
          live = clampNotePaneWidthPx(ev.clientX - sidebarWidthPx(), true);
          var ov = document.getElementById("note-editor-overlay");
          if (live) {
            root.style.setProperty("--note-pane-w", live + "px");
            if (ov) ov.classList.add("note-editor-overlay--sized");
          } else {
            root.style.removeProperty("--note-pane-w");
            if (ov) ov.classList.remove("note-editor-overlay--sized");
          }
          if (handle.setAttribute) {
            handle.setAttribute("aria-valuenow", String(live || noteStageWidthPx()));
          }
        }
      }
      function onUp() {
        root.classList.remove("note-pane-resizing");
        if (kind === "discuss") writeStoredPx(DISCUSS_PANE_W_LS, live);
        else writeStoredPx(NOTE_PANE_W_LS, live);
        applyNotePaneWidths();
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
        handle.removeEventListener("pointercancel", onUp);
      }
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
      handle.addEventListener("pointercancel", onUp);
    }

    if (noteHandle) {
      noteHandle.addEventListener("pointerdown", function (e) {
        startDrag("note", e);
      });
      noteHandle.addEventListener("keydown", function (e) {
        if (!isDesktopLayout() || isNoteDiscussionOpen()) return;
        var stage = noteStageWidthPx();
        var cur = readStoredPx(NOTE_PANE_W_LS) || stage;
        var step = e.shiftKey ? 48 : 16;
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          writeStoredPx(NOTE_PANE_W_LS, clampNotePaneWidthPx(cur - step, true));
          applyNotePaneWidths();
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          writeStoredPx(NOTE_PANE_W_LS, clampNotePaneWidthPx(cur + step, true));
          applyNotePaneWidths();
        } else if (e.key === "Home") {
          e.preventDefault();
          writeStoredPx(NOTE_PANE_W_LS, 0);
          applyNotePaneWidths();
        }
      });
    }
    if (discussHandle) {
      discussHandle.addEventListener("pointerdown", function (e) {
        startDrag("discuss", e);
      });
      discussHandle.addEventListener("keydown", function (e) {
        if (!isDesktopLayout() || !isNoteDiscussionOpen()) return;
        var cur = clampDiscussWidthPx(readStoredPx(DISCUSS_PANE_W_LS) || defaultDiscussWidthPx());
        var step = e.shiftKey ? 48 : 16;
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          writeStoredPx(DISCUSS_PANE_W_LS, clampDiscussWidthPx(cur + step));
          applyNotePaneWidths();
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          writeStoredPx(DISCUSS_PANE_W_LS, clampDiscussWidthPx(cur - step));
          applyNotePaneWidths();
        } else if (e.key === "Home") {
          e.preventDefault();
          writeStoredPx(DISCUSS_PANE_W_LS, defaultDiscussWidthPx());
          applyNotePaneWidths();
        }
      });
    }
    window.addEventListener("resize", function () {
      syncSidebarToggleUi();
      if (isNoteEditorModalOpen() || isNoteDiscussionOpen()) applyNotePaneWidths();
    });
  }

  function pulseMotionEnter(el) {
    if (!el || prefersReducedMotion()) return;
    el.classList.remove("motion-enter");
    void el.offsetWidth;
    el.classList.add("motion-enter");
  }

  function clearPanelTransitionClasses(panel) {
    if (!panel) return;
    panel.classList.remove(
      "panel--out",
      "panel--out-left",
      "panel--out-right",
      "panel--out-active",
      "panel--in",
      "panel--in-from-right",
      "panel--in-from-left",
      "panel--in-active"
    );
  }

  function applyTabPanelsVisible(activeName) {
    document.querySelectorAll(".panel").forEach(function (p) {
      var on = p.id === "panel-" + activeName;
      p.classList.toggle("hidden", !on);
      if (on) p.removeAttribute("hidden");
      else p.setAttribute("hidden", "hidden");
    });
  }

  function postTabSwitch(name, prev) {
    if (name === "profile") {
      if (!tabInited.profile) {
        tabInited.profile = true;
        loadProfile();
      }
    }
    if (name === "actual") loadActual();
    if (name === "notes") {
      loadNoteEditorScripts().catch(function () {});
      loadNotes();
      if (prev === "knowledge") {
        closeNotesDetail();
        if (isNoteEditorModalOpen()) handleNoteEditorModalClose({ skipPrompt: true });
      }
    }
    if (name === "knowledge") {
      closeNotesDetail();
      if (isNoteEditorModalOpen()) handleNoteEditorModalClose({ skipPrompt: true });
      loadNoteEditorScripts().catch(function () {});
      loadKnowledgeNotes();
      ensureUserTagsLoaded();
    }
    if (name === "digest") {
      closeDigestDetail();
      setDigestSubTab(digestSubTab || "chats");
      if (digestSubTab === "market") loadMarketDigest();
      else loadDigestReports();
    }
    if (name !== "notes" && name !== "knowledge") {
      closeNotesDetail();
      if (isNoteEditorModalOpen()) handleNoteEditorModalClose({ skipPrompt: true });
    }
    if (name !== "digest") {
      closeDigestDetail();
    }
    if (name !== "profile" && currentProfileScreen !== "main") {
      profileScreen("main");
    }
    syncTelegramNativeBack();
    syncAppOverlay();
    syncNotesCreateFab();
    syncActualCreateFab();
    syncContactsCreateFab();
    renderSidebarTrees();
  }

  function setTab(name, options) {
    options = options || {};
    if (name === "digest" && !digestTabEnabled) name = "actual";
    if (TAB_ORDER.indexOf(name) < 0) name = "actual";
    var prev = currentTab;
    document.querySelectorAll(".tabbar-btn").forEach(function (btn) {
      var on = btn.getAttribute("data-tab") === name;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (prev === name) {
      if (name === "profile" && currentProfileScreen !== "main") {
        profileScreen("main");
      }
      if (name === "notes" || name === "knowledge") {
        closeNotesDetail();
        if (isNoteEditorModalOpen()) handleNoteEditorModalClose({ skipPrompt: true });
      }
      if (name === "digest") closeDigestDetail();
      postTabSwitch(name, prev);
      return;
    }
    var stage = document.querySelector("main.main-scroll.panels-stage");
    var outEl = document.getElementById("panel-" + prev);
    var inEl = document.getElementById("panel-" + name);
    var forward = TAB_ORDER.indexOf(name) > TAB_ORDER.indexOf(prev);
    var animate =
      !options.noAnim &&
      !isDesktopLayout() &&
      stage &&
      outEl &&
      inEl &&
      !prefersReducedMotion() &&
      !stage.classList.contains("panels-stage--animating");

    if (!animate) {
      applyTabPanelsVisible(name);
      clearPanelTransitionClasses(outEl);
      clearPanelTransitionClasses(inEl);
      currentTab = name;
      postTabSwitch(name, prev);
      return;
    }

    stage.classList.add("panels-stage--animating");
    inEl.classList.remove("hidden");
    inEl.removeAttribute("hidden");
    clearPanelTransitionClasses(outEl);
    clearPanelTransitionClasses(inEl);
    outEl.classList.add("panel--out");
    inEl.classList.add(
      "panel--in",
      forward ? "panel--in-from-right" : "panel--in-from-left"
    );
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        outEl.classList.add(
          "panel--out-active",
          forward ? "panel--out-left" : "panel--out-right"
        );
        inEl.classList.add("panel--in-active");
      });
    });
    window.setTimeout(function () {
      outEl.classList.add("hidden");
      outEl.setAttribute("hidden", "hidden");
      clearPanelTransitionClasses(outEl);
      clearPanelTransitionClasses(inEl);
      stage.classList.remove("panels-stage--animating");
      currentTab = name;
      postTabSwitch(name, prev);
    }, panelTransitionMs);
  }

  function isoToDatetimeLocalValue(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const p = function (n) {
      return String(n).padStart(2, "0");
    };
    return (
      d.getFullYear() +
      "-" +
      p(d.getMonth() + 1) +
      "-" +
      p(d.getDate()) +
      "T" +
      p(d.getHours()) +
      ":" +
      p(d.getMinutes())
    );
  }

  function defaultDatetimeLocalValue(addMinutes) {
    var d = new Date();
    d.setMinutes(d.getMinutes() + (addMinutes || 60));
    d.setSeconds(0, 0);
    return isoToDatetimeLocalValue(d.toISOString());
  }

  function parseDatetimeLocal(val) {
    var s = String(val || "").trim();
    if (!s) return null;
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s)) s += ":00";
    var d = new Date(s);
    if (isNaN(d.getTime())) return null;
    return d;
  }

  function datetimeLocalToIso(val) {
    var d = parseDatetimeLocal(val);
    return d ? d.toISOString() : "";
  }

  function openExternal(url) {
    if (!url) return;
    if (
      window.Telegram &&
      window.Telegram.WebApp &&
      window.Telegram.WebApp.openLink
    ) {
      window.Telegram.WebApp.openLink(url);
    } else {
      window.open(url, "_blank");
    }
  }

  function openTelegramDeepLink(startParam) {
    const bot = (cachedBotUsername || "").replace(/^@/, "");
    if (!bot) {
      alert("Не задан TELEGRAM_BOT_USERNAME на сервере.");
      return;
    }
    const url = "https://t.me/" + encodeURIComponent(bot) + "?start=" + encodeURIComponent(startParam);
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.openTelegramLink) {
      window.Telegram.WebApp.openTelegramLink(url);
    } else {
      window.location.href = url;
    }
  }

  function heroDateLine() {
    try {
      return new Date().toLocaleDateString("ru-RU", {
        weekday: "long",
        day: "numeric",
        month: "long",
      });
    } catch (_) {
      return "";
    }
  }

  function formatEventClock(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return d.toLocaleString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_) {
      return "";
    }
  }

  function formatEventTime(ev) {
    const st = ev.start || {};
    const raw = st.dateTime || st.date || "";
    if (!raw) return "—";
    const t = formatEventClock(raw);
    return t || raw;
  }

  function formatEventEndTime(ev) {
    const en = ev.end || {};
    const raw = en.dateTime || en.date || "";
    if (!raw) return "";
    return formatEventClock(raw);
  }

  // Держать в sync с assistant/lib/usage_store.py OPERATION_LABELS_RU
  function operationLabel(op) {
    const labels = {
      intent_route: "Маршрутизация запроса",
      parse_calendar: "Разбор встречи (календарь)",
      parse_zoom: "Разбор Zoom-встречи",
      parse_reminder: "Разбор напоминания",
      format_note: "Оформление заметки",
      ask: "Вопрос к GPT",
      obuchat_transcribe: "Транскрипция",
      summarize: "Саммари",
      chat_digest: "Дайджест чатов",
      answer_with_context: "Обсуждение с GPT (архив)",
      "chat/completions": "Запрос к модели",
    };
    const key = String(op || "").trim();
    if (!key) return "—";
    return labels[key] || key;
  }

  function sidebarTreesAllowed() {
    return isDesktopLayout() && !isSidebarCollapsed();
  }

  function sidebarProjectKey(section, projectId) {
    return String(section) + ":" + String(projectId || "none");
  }

  function isSidebarProjectOpen(section, projectId) {
    var key = sidebarProjectKey(section, projectId);
    if (Object.prototype.hasOwnProperty.call(sidebarTreeState.projects, key)) {
      return !!sidebarTreeState.projects[key];
    }
    return false;
  }

  function setSidebarProjectOpen(section, projectId, open) {
    sidebarTreeState.projects[sidebarProjectKey(section, projectId)] = !!open;
  }

  function groupItemsByProject(items) {
    var tags = getUserTagsFromCache();
    var byId = {};
    var none = [];
    (items || []).forEach(function (item) {
      var project = noteProjectOf(item);
      if (project && project.id != null) {
        var pid = String(project.id);
        if (!byId[pid]) byId[pid] = { project: project, items: [] };
        byId[pid].items.push(item);
      } else {
        none.push(item);
      }
    });
    var groups = [];
    tags.forEach(function (tag) {
      var pid = String(tag.id);
      if (byId[pid] && byId[pid].items.length) {
        groups.push({
          project: { id: tag.id, name: tag.name || byId[pid].project.name || "Проект" },
          items: byId[pid].items,
        });
        delete byId[pid];
      }
    });
    Object.keys(byId).forEach(function (pid) {
      groups.push(byId[pid]);
    });
    if (none.length) {
      groups.push({ project: { id: "none", name: "Без проекта" }, items: none });
    }
    return groups;
  }

  function sidebarItemTitle(item, kind) {
    if (kind === "local") {
      return (
        sanitizeNoteTitle((item && (item.title || item.content)) || "") ||
        String((item && (item.title || item.content)) || "Без названия")
      );
    }
    if (kind === "chat") {
      return String((item && item.title) || "Новый чат");
    }
    return journalCardTitle(item) || "Запись";
  }

  function makeSidebarTreeToggle(expanded) {
    var chev = document.createElement("span");
    chev.className = "sidebar-tree-chevron" + (expanded ? " is-open" : "");
    chev.setAttribute("aria-hidden", "true");
    chev.innerHTML =
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
    return chev;
  }

  function makeSidebarTreeRow(opts) {
    opts = opts || {};
    var depth = opts.depth || 0;
    var row = document.createElement("div");
    row.className =
      "sidebar-tree-row" +
      (opts.leaf ? " sidebar-tree-row--leaf" : "") +
      (opts.active ? " is-active" : "") +
      (opts.expandable ? " is-expandable" : "") +
      (opts.action ? " sidebar-tree-row--action" : "") +
      (depth === 0 ? " sidebar-tree-row--section" : "");
    row.style.setProperty("--sidebar-depth", String(depth));
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "sidebar-tree-label" +
      (opts.expandable ? " has-chevron" : "") +
      (opts.action ? " sidebar-tree-label--action" : "");
    btn.title = opts.label || "";
    if (opts.expandable) {
      btn.appendChild(makeSidebarTreeToggle(!!opts.expanded));
    } else {
      var spacer = document.createElement("span");
      spacer.className = "sidebar-tree-chevron-spacer";
      spacer.setAttribute("aria-hidden", "true");
      btn.appendChild(spacer);
    }
    var text = document.createElement("span");
    text.className = "sidebar-tree-label-text";
    text.textContent = opts.label || "";
    btn.appendChild(text);
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var hitChevron =
        e.target && e.target.closest && e.target.closest(".sidebar-tree-chevron");
      if (hitChevron && typeof opts.onToggle === "function") {
        opts.onToggle();
        return;
      }
      if (typeof opts.onClick === "function") opts.onClick();
    });
    row.appendChild(btn);
    return row;
  }

  function sidebarTreeGuideLeft(depth) {
    return String(Number(depth || 0));
  }

  function appendSidebarProjectBranch(host, section, groups, kind, openItem) {
    groups.forEach(function (group) {
      var pid = group.project && group.project.id != null ? group.project.id : "none";
      var open = isSidebarProjectOpen(section, pid);
      var branch = document.createElement("div");
      branch.className = "sidebar-tree-branch";
      branch.appendChild(
        makeSidebarTreeRow({
          depth: 1,
          expandable: true,
          expanded: open,
          label: String((group.project && group.project.name) || "Проект"),
          active: false,
          onToggle: function () {
            setSidebarProjectOpen(section, pid, !open);
            renderSidebarTrees();
          },
          onClick: function () {
            setSidebarProjectOpen(section, pid, true);
            setTab("notes", { noAnim: true });
            setNotesSubTab(section);
            if (pid !== "none" && kind !== "chat") {
              notesActiveProjectId = Number(pid) || 0;
              renderNotesTagFilterBar();
              if (notesDataCache) renderNotesPanesFromData(notesDataCache);
            }
            renderSidebarTrees();
          },
        })
      );
      if (open) {
        var kids = document.createElement("div");
        kids.className = "sidebar-tree-children";
        kids.style.setProperty("--parent-depth", sidebarTreeGuideLeft(1));
        (group.items || []).forEach(function (item) {
          kids.appendChild(
            makeSidebarTreeRow({
              depth: 2,
              leaf: true,
              label: sidebarItemTitle(item, kind),
              active: false,
              onClick: function () {
                openItem(item);
              },
            })
          );
        });
        branch.appendChild(kids);
      }
      host.appendChild(branch);
    });
  }

  function renderSidebarNotesTree() {
    var tree = document.getElementById("sidebar-tree-notes");
    if (!tree) return;
    var open = sidebarTreesAllowed() && !!sidebarTreeState.notesOpen;
    tree.classList.toggle("hidden", !open);
    if (!open) {
      tree.innerHTML = "";
      return;
    }
    if (!chatThreadsCache.length && !chatThreadsLoading) {
      loadChatThreads().catch(function () {});
    }
    tree.innerHTML = "";
    var data = notesDataCache || {};
    var sections = [
      {
        id: "notes",
        label: "Заметки",
        kind: "local",
        items: (function () {
          splitTranscriptionsOutOfLocalNotes(data);
          return (data.local_notes || []).filter(function (n) {
            return !looksLikeTranscriptionNote(n);
          });
        })(),
        openItem: function (n) {
          setTab("notes", { noAnim: true });
          setNotesSubTab("notes");
          openNoteDetail(n, { isLocal: true });
        },
      },
      {
        id: "chats",
        label: "Чаты",
        kind: "chat",
        items: visibleChatThreads(),
        openItem: function (t) {
          setTab("notes", { noAnim: true });
          setNotesSubTab("chats");
          openChatThread(t);
        },
      },
      {
        id: "transcriptions",
        label: "Транскрипции",
        kind: "local",
        items: data.transcriptions || [],
        openItem: function (row) {
          setTab("notes", { noAnim: true });
          setNotesSubTab("transcriptions");
          openNoteDetail(row, { isLocal: true });
        },
      },
    ];
    sections.forEach(function (sec) {
      var expanded = !!sidebarTreeState.sections[sec.id];
      var branch = document.createElement("div");
      branch.className = "sidebar-tree-branch";
      branch.appendChild(
        makeSidebarTreeRow({
          depth: 0,
          expandable: true,
          expanded: expanded,
          label: sec.label,
          active: currentTab === "notes" && notesSubTab === sec.id,
          onToggle: function () {
            sidebarTreeState.sections[sec.id] = !expanded;
            renderSidebarTrees();
          },
          onClick: function () {
            setTab("notes", { noAnim: true });
            setNotesSubTab(sec.id);
            sidebarTreeState.sections[sec.id] = true;
            renderSidebarTrees();
          },
        })
      );
      if (expanded) {
        var kids = document.createElement("div");
        kids.className = "sidebar-tree-children";
        kids.style.setProperty("--parent-depth", sidebarTreeGuideLeft(0));
        if (sec.id === "chats") {
          var chats = sec.items || [];
          if (!chats.length) {
            var empty = document.createElement("p");
            empty.className = "sidebar-tree-empty";
            empty.textContent = "Чатов пока нет";
            kids.appendChild(empty);
          } else {
            chats.forEach(function (t) {
              kids.appendChild(
                makeSidebarTreeRow({
                  depth: 1,
                  leaf: true,
                  label: sidebarItemTitle(t, "chat"),
                  active: String(activeChatThreadId) === String(t.id),
                  onClick: function () {
                    sec.openItem(t);
                  },
                })
              );
            });
          }
        } else {
          var groups = groupItemsByProject(sec.items || []);
          if (!groups.length) {
            var empty2 = document.createElement("p");
            empty2.className = "sidebar-tree-empty";
            empty2.textContent = "Пусто";
            kids.appendChild(empty2);
          } else {
            appendSidebarProjectBranch(kids, sec.id, groups, sec.kind, sec.openItem);
          }
          if (sec.id === "notes") {
            kids.appendChild(
              makeSidebarTreeRow({
                depth: 1,
                leaf: true,
                action: true,
                label: "+ Создать проект",
                onClick: function () {
                  setTab("notes", { noAnim: true });
                  setNotesSubTab("notes");
                  openTagsManageSheet();
                },
              })
            );
          }
        }
        branch.appendChild(kids);
      }
      tree.appendChild(branch);
    });
  }

  function renderSidebarDigestTree() {
    var tree = document.getElementById("sidebar-tree-digest");
    if (!tree) return;
    var open = sidebarTreesAllowed() && digestTabEnabled && !!sidebarTreeState.digestOpen;
    tree.classList.toggle("hidden", !open);
    if (!open) {
      tree.innerHTML = "";
      return;
    }
    tree.innerHTML = "";
    [
      { id: "chats", label: "Чаты" },
      { id: "market", label: "Рынок" },
    ].forEach(function (sec) {
      tree.appendChild(
        makeSidebarTreeRow({
          depth: 0,
          leaf: true,
          label: sec.label,
          active: currentTab === "digest" && digestSubTab === sec.id,
          onClick: function () {
            setTab("digest", { noAnim: true });
            setDigestSubTab(sec.id);
            renderSidebarTrees();
          },
        })
      );
    });
  }

  function syncSidebarTreeToggles() {
    document.querySelectorAll("[data-sidebar-toggle]").forEach(function (el) {
      var key = el.getAttribute("data-sidebar-toggle");
      var open = false;
      if (key === "notes") open = sidebarTreesAllowed() && !!sidebarTreeState.notesOpen;
      else if (key === "digest") {
        open = sidebarTreesAllowed() && digestTabEnabled && !!sidebarTreeState.digestOpen;
      }
      el.classList.toggle("is-open", open);
      el.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function renderSidebarTrees() {
    if (!sidebarTreesAllowed()) {
      var notesTree = document.getElementById("sidebar-tree-notes");
      var digestTree = document.getElementById("sidebar-tree-digest");
      if (notesTree) {
        notesTree.classList.add("hidden");
        notesTree.innerHTML = "";
      }
      if (digestTree) {
        digestTree.classList.add("hidden");
        digestTree.innerHTML = "";
      }
      syncSidebarTreeToggles();
      return;
    }
    if (!notesDataCache) {
      loadNotes().catch(function () {});
    }
    renderSidebarNotesTree();
    renderSidebarDigestTree();
    syncSidebarTreeToggles();
  }

  var sidebarTreeBound = false;
  function bindSidebarTreesOnce() {
    if (sidebarTreeBound) return;
    sidebarTreeBound = true;
    document.addEventListener(
      "click",
      function (e) {
        var toggle = e.target && e.target.closest && e.target.closest("[data-sidebar-toggle]");
        if (!toggle) return;
        e.preventDefault();
        e.stopPropagation();
        if (!sidebarTreesAllowed()) return;
        var key = toggle.getAttribute("data-sidebar-toggle");
        if (key === "notes") {
          sidebarTreeState.notesOpen = !sidebarTreeState.notesOpen;
          if (sidebarTreeState.notesOpen && !notesDataCache) {
            loadNotes().catch(function () {});
          }
        } else if (key === "digest") {
          sidebarTreeState.digestOpen = !sidebarTreeState.digestOpen;
        }
        renderSidebarTrees();
      },
      true
    );
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var toggle = e.target && e.target.closest && e.target.closest("[data-sidebar-toggle]");
      if (!toggle) return;
      e.preventDefault();
      e.stopPropagation();
      toggle.click();
    });
    if (window.matchMedia) {
      var mq = window.matchMedia("(min-width: 960px)");
      var onChange = function () {
        renderSidebarTrees();
      };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }

  function setDigestTabVisible(enabled) {

    digestTabEnabled = !!enabled;
    var btn = document.querySelector('.tabbar-btn[data-tab="digest"]');
    var group = document.querySelector('.sidebar-nav-group[data-sidebar-group="digest"]');
    if (btn) {
      setHidden(btn, !digestTabEnabled);
      if (digestTabEnabled) btn.removeAttribute("hidden");
      else btn.setAttribute("hidden", "hidden");
    }
    if (group) {
      setHidden(group, !digestTabEnabled);
      if (digestTabEnabled) group.removeAttribute("hidden");
      else group.setAttribute("hidden", "hidden");
    }
    var hint = document.getElementById("profile-chats-hint");
    if (hint) {
      hint.textContent = digestTabEnabled
        ? "Включён · отчёты во вкладке Дайджест"
        : "Дайджест выбранных чатов";
    }
    if (!digestTabEnabled && currentTab === "digest") {
      setTab("actual", { noAnim: true });
    }
    renderSidebarTrees();
  }

  async function syncDigestSettingsFromServer() {
    try {
      var data = await apiFetch("/settings", { method: "GET" });
      setDigestTabVisible(!!(data && data.digest_enabled));
      return data;
    } catch (e) {
      setDigestTabVisible(false);
      return null;
    }
  }

  function applyDigestEnabledToggleUi(on) {
    var btn = document.getElementById("digest-enabled-toggle");
    if (!btn) return;
    btn.classList.toggle("svc-toggle--on", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function closeDigestDetail() {
    var root = document.getElementById("digest-root");
    var detail = document.getElementById("digest-detail");
    setHidden(detail, true);
    setHidden(root, false);
    syncAppOverlay();
    syncTelegramNativeBack();
  }

  function setDigestSubTab(name) {
    digestSubTab = name === "market" ? "market" : "chats";
    document.querySelectorAll("[data-digest-subtab]").forEach(function (b) {
      var on = b.getAttribute("data-digest-subtab") === digestSubTab;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    setHidden(document.getElementById("digest-pane-chats"), digestSubTab !== "chats");
    setHidden(document.getElementById("digest-pane-market"), digestSubTab !== "market");
    var refreshBtn = document.getElementById("digest-refresh-btn");
    if (refreshBtn) {
      refreshBtn.title = digestSubTab === "market" ? "Обновить рынок" : "Обновить";
      refreshBtn.setAttribute(
        "aria-label",
        digestSubTab === "market" ? "Обновить рынок" : "Обновить"
      );
    }
    if (digestSubTab === "market") {
      closeDigestDetail();
      loadMarketDigest();
    } else {
      loadDigestReports();
    }
    renderSidebarTrees();
  }

  function digestItemId(report) {
    if (report && report.item_id) return String(report.item_id);
    var day = String((report && report.report_date) || "").trim();
    var chatId = report && report.chat_id;
    if (!day || chatId == null || chatId === "") return "";
    return day + ":" + String(chatId);
  }

  function digestTitleForEditor(report) {
    var summary = (report && report.summary) || {};
    var custom = String(summary.editor_title || "").trim();
    if (custom) return custom;
    var title = String((report && report.title) || "Чат").trim() || "Чат";
    var day = report && report.report_date ? formatDigestDate(report.report_date) : "";
    return day ? title + " · " + day : title;
  }

  function digestBodyForEditor(report) {
    var summary = (report && report.summary) || {};
    var saved = sanitizeNoteBody(summary.body_html || "");
    if (saved) return saved;
    return renderDigestDetailHtml(report);
  }

  function openDigestDetail(report) {
    if (!report) return;
    closeDigestDetail();
    var date = String(report.report_date || "").trim();
    var chatId = report.chat_id;
    if (!date || chatId == null || chatId === "") {
      openDigestEditorDetail(report);
      return;
    }
    apiFetch(
      "/digest/reports/" + encodeURIComponent(date) + "/" + encodeURIComponent(String(chatId)),
      { method: "GET" }
    )
      .then(function (fresh) {
        openDigestEditorDetail(fresh || report);
      })
      .catch(function () {
        openDigestEditorDetail(report);
      });
  }

  function openDigestEditorDetail(report) {
    if (!report) return;
    resetNoteSheetState("");
    var itemId = digestItemId(report);
    if (!itemId) return;
    var cleanTitle = digestTitleForEditor(report);
    var cleanBody = digestBodyForEditor(report);
    var wrap = document.createElement("div");
    wrap.className = "note-editor-page";
    clearNoteEditorTagsWrap();
    var tagsWrap = document.getElementById("note-editor-tags-wrap");
    if (tagsWrap) setHidden(tagsWrap, false);
    mountShareControls("digest", itemId);
    wrap.innerHTML = noteEditorPageInnerHtml("Заголовок");
    syncNoteGptToolbarButton(true);
    bindNoteGptToolbarButton();
    var tocControls = bindNoteEditorTocControls(wrap);
    var editorPad = wrap.querySelector(".note-editor-pad--body");
    var titleInput = wrap.querySelector("#note-editor-title-input");
    if (titleInput) titleInput.value = cleanTitle;

    var saveTimer = null;
    function readFields() {
      return {
        title: getNoteEditorTitle().trim(),
        description: readActiveNoteEditorHtml(getLeftNoteRichEditor()),
      };
    }
    async function persistDigestDraft() {
      var fields = readFields();
      var date = String(report.report_date || "").trim();
      var chatId = report.chat_id;
      var patchRes = await apiFetch(
        "/digest/reports/" +
          encodeURIComponent(date) +
          "/" +
          encodeURIComponent(String(chatId)),
        {
          method: "PATCH",
          body: JSON.stringify({
            title: fields.title,
            body_html: fields.description,
          }),
        }
      );
      if (patchRes && patchRes.summary) report.summary = patchRes.summary;
      if (patchRes && patchRes.title) report.title = patchRes.title;
      var detailBodyDraft = getNoteEditorBodyEl();
      if (detailBodyDraft && detailBodyDraft._noteEditor) {
        detailBodyDraft._noteEditor.baseline = noteEditorSnapshot(fields.title, fields.description);
      }
    }
    function schedulePatch() {
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        persistDigestDraft()
          .then(function () {
            setNoteEditorSaveHint("");
          })
          .catch(function (e) {
            setNoteEditorSaveHint(e.message || "Не удалось сохранить отчёт");
          });
      }, 1500);
    }

    var modalBody = getNoteEditorBodyEl();
    if (modalBody) {
      modalBody.innerHTML = "";
      modalBody.appendChild(wrap);
      syncNoteCommentsPanel();
      modalBody._openNoteGpt = async function () {
        try {
          await persistDigestDraft();
        } catch (e) {
          alert(e.message || String(e));
          return;
        }
        openNoteDiscussion({ mode: "gpt", focus: true });
      };
      modalBody._noteEditorFlush = async function () {
        if (saveTimer) {
          clearTimeout(saveTimer);
          saveTimer = null;
        }
        await persistDigestDraft();
      };
      modalBody._noteEditor = {
        ready: false,
        baseline: noteEditorSnapshot(cleanTitle, cleanBody),
        getCurrent: function () {
          return noteEditorSnapshot(
            getNoteEditorTitle(),
            readActiveNoteEditorHtml(getLeftNoteRichEditor())
          );
        },
        runSave: async function () {
          if (saveTimer) {
            clearTimeout(saveTimer);
            saveTimer = null;
          }
          try {
            await persistDigestDraft();
          } catch (e) {
            alert(e.message || String(e));
            return false;
          }
          modalBody._noteEditorFlush = null;
          closeNoteEditorModal();
          return true;
        },
      };
    }
    openNoteEditorModal("Дайджест");
    mountNoteRichEditor(editorPad, cleanBody, schedulePatch, {
      tocParent: tocControls.tocParent,
      onTocNavigate: tocControls.close,
      scrollParent: document.querySelector("#note-editor-overlay .note-editor-modal-body"),
      extraTocItems: function (tocEl) {
        appendNoteDiscussionToc(tocEl, tocControls.close);
      },
    }).then(function (editor) {
      var detailBodyMount = getNoteEditorBodyEl();
      if (detailBodyMount) detailBodyMount._noteRichEditor = editor;
      if (detailBodyMount && detailBodyMount._noteEditor) {
        detailBodyMount._noteEditor.ready = true;
        detailBodyMount._noteEditor.baseline = noteEditorSnapshot(
          getNoteEditorTitle(),
          readActiveNoteEditorHtml()
        );
      }
      refreshNoteCommentAnchors();
    });
    bindNoteTitleAutoresize(titleInput);
    if (titleInput) titleInput.addEventListener("input", schedulePatch);
    consumePendingDiscussOpen("digest", itemId);
  }

  function formatDigestDate(ymd) {
    var s = String(ymd || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    var p = s.split("-");
    return p[2] + "." + p[1] + "." + p[0];
  }

  function escapeHtmlDigest(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderDigestSection(title, itemsHtml) {
    if (!itemsHtml) return "";
    return (
      '<section class="digest-section"><h3 class="digest-section-title">' +
      escapeHtmlDigest(title) +
      "</h3>" +
      itemsHtml +
      "</section>"
    );
  }

  function renderDigestDetailHtml(report) {
    var summary = (report && report.summary) || {};
    var parts = [];
    if (report.is_demo) {
      parts.push('<p class="digest-demo-badge">Демо-отчёт</p>');
    }
    var meta = [];
    if (report.message_count) meta.push(report.message_count + " сообщ.");
    if (report.generated_at) {
      meta.push(String(report.generated_at).replace("T", " ").slice(0, 16));
    }
    if (meta.length) {
      parts.push('<p class="muted small">' + escapeHtmlDigest(meta.join(" · ")) + "</p>");
    }
    if (summary.brief) {
      parts.push(renderDigestSection("Коротко", "<p>" + escapeHtmlDigest(summary.brief) + "</p>"));
    }
    var decisions = summary.decisions || [];
    if (decisions.length) {
      var dHtml =
        "<ul>" +
        decisions
          .map(function (d) {
            var t = typeof d === "string" ? d : d.text || d.decision || "";
            return "<li>" + escapeHtmlDigest(t) + "</li>";
          })
          .join("") +
        "</ul>";
      parts.push(renderDigestSection("Договорились", dHtml));
    }
    var steps = summary.next_steps || [];
    if (steps.length) {
      var sHtml =
        "<ul>" +
        steps
          .map(function (st) {
            var who = (st && st.assignee) || "не назначен";
            var task = (st && st.task) || "";
            var dl = (st && st.deadline) || "";
            var line = who + " — " + task + (dl ? " (" + dl + ")" : "");
            return "<li>" + escapeHtmlDigest(line) + "</li>";
          })
          .join("") +
        "</ul>";
      parts.push(renderDigestSection("Следующие шаги", sHtml));
    }
    var deadlines = summary.deadlines || [];
    if (deadlines.length) {
      var dlHtml =
        "<ul>" +
        deadlines
          .map(function (d) {
            var line =
              ((d && d.item) || "") +
              (d && d.when ? " — " + d.when : "") +
              (d && d.assignee ? " (" + d.assignee + ")" : "");
            return "<li>" + escapeHtmlDigest(line) + "</li>";
          })
          .join("") +
        "</ul>";
      parts.push(renderDigestSection("Дедлайны", dlHtml));
    }
    var questions = summary.open_questions || [];
    if (questions.length) {
      var qHtml =
        "<ul>" +
        questions
          .map(function (q) {
            var t = typeof q === "string" ? q : q.text || "";
            return "<li>" + escapeHtmlDigest(t) + "</li>";
          })
          .join("") +
        "</ul>";
      parts.push(renderDigestSection("Открытые вопросы", qHtml));
    }
    var topics = summary.context_topics || [];
    if (topics.length) {
      var tHtml =
        "<ul>" +
        topics.map(function (t) {
          return "<li>" + escapeHtmlDigest(t) + "</li>";
        }).join("") +
        "</ul>";
      parts.push(renderDigestSection("Ещё обсуждали", tHtml));
    }
    if (!parts.length) {
      return '<p class="muted">Пустой отчёт</p>';
    }
    return parts.join("");
  }

  function renderDigestList(reports) {
    var list = document.getElementById("digest-list");
    var empty = document.getElementById("digest-empty");
    if (!list) return;
    list.innerHTML = "";
    var rows = Array.isArray(reports) ? reports : [];
    setHidden(empty, rows.length > 0);
    var lastDate = "";
    rows.forEach(function (rep) {
      var day = String(rep.report_date || "");
      if (day && day !== lastDate) {
        lastDate = day;
        var head = document.createElement("p");
        head.className = "digest-day-head";
        head.textContent = formatDigestDate(day);
        list.appendChild(head);
      }
      var card = document.createElement("button");
      card.type = "button";
      card.className = "note-card digest-card";
      var title = document.createElement("p");
      title.className = "note-card-title";
      title.textContent = rep.title || "Чат";
      var preview = document.createElement("p");
      preview.className = "note-card-preview muted";
      preview.textContent = rep.preview || (rep.summary && rep.summary.brief) || "Открыть отчёт";
      card.appendChild(title);
      if (rep.is_demo) {
        var badge = document.createElement("span");
        badge.className = "digest-demo-pill";
        badge.textContent = "демо";
        card.appendChild(badge);
      }
      card.appendChild(preview);
      card.addEventListener("click", function () {
        openDigestDetail(rep);
      });
      list.appendChild(card);
    });
  }

  async function loadDigestReports() {
    var err = document.getElementById("digest-error");
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    try {
      var data = await apiFetch("/digest/reports?limit=60", { method: "GET" });
      if (data && data.digest_enabled === false) {
        setDigestTabVisible(false);
        return;
      }
      renderDigestList((data && data.reports) || []);
    } catch (e) {
      if (err) {
        err.textContent = e.message || String(e);
        err.classList.remove("hidden");
      }
      renderDigestList([]);
    }
  }

  async function refreshDigestReports() {
    if (digestSubTab === "market") {
      digestMarketLoaded = false;
      await loadMarketDigest({ force: true });
      return;
    }
    if (digestRefreshing) return;
    var btn = document.getElementById("digest-refresh-btn");
    var err = document.getElementById("digest-error");
    digestRefreshing = true;
    if (btn) {
      btn.disabled = true;
      btn.classList.add("is-spinning");
    }
    if (err) {
      err.className = "error hidden";
      err.textContent = "";
    }
    try {
      var res = await apiFetch("/digest/refresh", {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadDigestReports();
      var updated = (res && res.reports_updated) || 0;
      var skipped = (res && res.skipped_empty) || 0;
      if (err && updated === 0 && skipped > 0) {
        err.className = "error";
        err.textContent =
          "Нет новых сообщений с последнего анализа в выбранных чатах. Дождитесь переписки или проверьте Профиль → Чаты.";
        err.classList.remove("hidden");
      }
    } catch (e) {
      if (err) {
        err.className = "error";
        err.textContent = e.message || String(e);
        err.classList.remove("hidden");
      }
    } finally {
      digestRefreshing = false;
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("is-spinning");
      }
    }
  }

  function remapMarketClass(el) {
    if (!el || !el.classList) return;
    var map = {
      eyebrow: "digest-market-eyebrow",
      lead: "digest-market-lead",
      meta: "digest-market-meta-line",
      why: "digest-market-why",
      footer: "digest-market-footer",
      section: "digest-market-section",
      card: "digest-market-card",
      grid: "digest-market-grid",
      pill: "digest-market-pill",
      "strategy-list": "digest-market-strategy",
      "strategy-item": "digest-market-strategy-item",
      "strategy-trigger": "digest-market-strategy-trigger",
      "strategy-hint": "digest-market-strategy-hint",
      "strategy-tooltip": "digest-market-strategy-tip",
    };
    Object.keys(map).forEach(function (from) {
      if (el.classList.contains(from)) {
        el.classList.remove(from);
        el.classList.add(map[from]);
      }
    });
  }

  function bindMarketStrategyTips(root) {
    if (!root) return;
    root.querySelectorAll(".digest-market-strategy-trigger").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tipId = btn.getAttribute("aria-controls");
        var tip = tipId ? document.getElementById(tipId) : null;
        if (!tip) {
          tip = btn.parentElement && btn.parentElement.querySelector(".digest-market-strategy-tip");
        }
        if (!tip) return;
        var open = btn.getAttribute("aria-expanded") === "true";
        root.querySelectorAll(".digest-market-strategy-trigger").forEach(function (other) {
          other.setAttribute("aria-expanded", "false");
        });
        root.querySelectorAll(".digest-market-strategy-tip").forEach(function (t) {
          t.hidden = true;
        });
        if (!open) {
          btn.setAttribute("aria-expanded", "true");
          tip.hidden = false;
        }
      });
    });
  }

  function cleanMarketCard(card) {
    if (!card) return;
    var titleEl = card.querySelector("h3");
    var titleNorm = normalizeMarketText(titleEl ? titleEl.textContent : "");
    Array.prototype.slice.call(card.querySelectorAll("p")).forEach(function (p) {
      if (
        p.classList.contains("digest-market-why") ||
        p.classList.contains("why") ||
        p.classList.contains("digest-market-meta-line") ||
        p.classList.contains("meta") ||
        p.classList.contains("digest-market-footer") ||
        p.classList.contains("footer")
      ) {
        return;
      }
      var t = normalizeMarketText(p.textContent);
      if (!t) {
        p.remove();
        return;
      }
      if (!titleNorm) return;
      var titleCore = titleNorm.split(" - ")[0];
      if (
        titleNorm.indexOf(t) >= 0 ||
        t.indexOf(titleCore) >= 0 ||
        titleCore.indexOf(t) >= 0
      ) {
        p.remove();
      }
    });
  }

  function normalizeMarketText(s) {
    return String(s || "")
      .toLowerCase()
      .replace(/[—–|,:;]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function adaptMarketNewsDom(main) {
    if (!main) return null;
    var wrap = document.createElement("div");
    wrap.className = "digest-market";

    var header = main.querySelector("header");
    if (header) {
      var head = document.createElement("div");
      head.className = "digest-market-header";
      Array.prototype.forEach.call(header.children, function (child) {
        var clone = child.cloneNode(true);
        if (clone.tagName === "H1") clone.className = "digest-market-title";
        remapMarketClass(clone);
        head.appendChild(clone);
      });
      wrap.appendChild(head);
    }

    var grid = main.querySelector(".grid");
    if (grid) {
      var g = grid.cloneNode(true);
      g.className = "digest-market-grid";
      remapMarketClass(g);
      g.querySelectorAll("*").forEach(remapMarketClass);
      g.querySelectorAll(".digest-market-card, article").forEach(cleanMarketCard);
      wrap.appendChild(g);
    } else {
      var sections = main.querySelectorAll(".section");
      Array.prototype.forEach.call(sections, function (sec) {
        var s = sec.cloneNode(true);
        remapMarketClass(s);
        s.querySelectorAll("*").forEach(remapMarketClass);
        s.querySelectorAll(".digest-market-card, article").forEach(cleanMarketCard);
        wrap.appendChild(s);
      });
    }

    if (!wrap.querySelector(".digest-market-section") && !wrap.querySelector(".digest-market-grid")) {
      var fallback = document.createElement("div");
      fallback.className = "digest-market-section";
      fallback.innerHTML = main.innerHTML;
      fallback.querySelectorAll("*").forEach(remapMarketClass);
      fallback.querySelectorAll(".digest-market-card, article").forEach(cleanMarketCard);
      wrap.appendChild(fallback);
    }
    return wrap;
  }

  async function loadMarketDigest(opts) {
    opts = opts || {};
    var body = document.getElementById("digest-market-body");
    var empty = document.getElementById("digest-market-empty");
    var err = document.getElementById("digest-market-error");
    var btn = document.getElementById("digest-refresh-btn");
    if (!body) return;
    if (digestMarketLoaded && !opts.force) return;
    if (digestMarketLoading) return;
    digestMarketLoading = true;
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    setHidden(empty, true);
    if (btn && digestSubTab === "market") {
      btn.disabled = true;
      btn.classList.add("is-spinning");
    }
    try {
      var res = await fetch("/news/", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
      });
      if (!res.ok) throw new Error("Не удалось загрузить дайджест рынка (" + res.status + ")");
      var html = await res.text();
      var doc = new DOMParser().parseFromString(html, "text/html");
      var main = doc.querySelector("main");
      var adapted = adaptMarketNewsDom(main);
      body.innerHTML = "";
      if (!adapted || !adapted.children.length) {
        setHidden(empty, false);
        digestMarketLoaded = true;
        return;
      }
      body.appendChild(adapted);
      bindMarketStrategyTips(body);
      digestMarketLoaded = true;
    } catch (e) {
      body.innerHTML = "";
      if (err) {
        err.textContent = (e && e.message) || String(e);
        err.classList.remove("hidden");
      }
      setHidden(empty, false);
      digestMarketLoaded = false;
    } finally {
      digestMarketLoading = false;
      if (btn && digestSubTab === "market") {
        btn.disabled = false;
        btn.classList.remove("is-spinning");
      }
    }
  }

  async function loadProfileChatsScreen() {
    var list = document.getElementById("profile-chats-list");
    var empty = document.getElementById("profile-chats-empty");
    var err = document.getElementById("profile-chats-err");
    var msg = document.getElementById("profile-chats-msg");
    var demoBtn = document.getElementById("digest-demo-seed-btn");
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    if (msg) msg.classList.add("hidden");
    try {
      var data = await apiFetch("/digest/chats", { method: "GET" });
      var enabled = !!(data && data.digest_enabled);
      applyDigestEnabledToggleUi(enabled);
      setDigestTabVisible(enabled);
      var chats = (data && data.chats) || [];
      if (list) list.innerHTML = "";
      setHidden(empty, chats.length > 0);
      chats.forEach(function (chat) {
        var row = document.createElement("label");
        row.className = "digest-chat-row";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!chat.selected;
        cb.addEventListener("change", function () {
          saveDigestChatSelection();
        });
        cb.setAttribute("data-chat-id", String(chat.chat_id));
        var text = document.createElement("span");
        text.className = "digest-chat-title";
        text.textContent = chat.title || "Чат " + chat.chat_id;
        row.appendChild(cb);
        row.appendChild(text);
        if (list) list.appendChild(row);
      });
      if (demoBtn) {
        setHidden(demoBtn, !(data && data.miniapp_dev));
      }
    } catch (e) {
      if (err) {
        err.textContent = e.message || String(e);
        err.classList.remove("hidden");
      }
    }
  }

  async function saveDigestChatSelection() {
    var list = document.getElementById("profile-chats-list");
    var err = document.getElementById("profile-chats-err");
    var ids = [];
    if (list) {
      list.querySelectorAll('input[type="checkbox"][data-chat-id]').forEach(function (cb) {
        if (cb.checked) ids.push(parseInt(cb.getAttribute("data-chat-id"), 10));
      });
    }
    try {
      await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify({ digest_chat_ids: ids }),
      });
    } catch (e) {
      if (err) {
        err.textContent = e.message || String(e);
        err.classList.remove("hidden");
      }
    }
  }

  async function toggleDigestEnabled() {
    var btn = document.getElementById("digest-enabled-toggle");
    var err = document.getElementById("profile-chats-err");
    var next = !(btn && btn.classList.contains("svc-toggle--on"));
    applyDigestEnabledToggleUi(next);
    try {
      await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify({ digest_enabled: next }),
      });
      setDigestTabVisible(next);
    } catch (e) {
      applyDigestEnabledToggleUi(!next);
      if (err) {
        err.textContent = e.message || String(e);
        err.classList.remove("hidden");
      }
    }
  }

  async function runDigestDemoSeed() {
    var err = document.getElementById("profile-chats-err");
    var msg = document.getElementById("profile-chats-msg");
    if (err) err.classList.add("hidden");
    try {
      var res = await apiFetch("/digest/demo-seed", { method: "POST", body: "{}" });
      await loadProfileChatsScreen();
      setDigestTabVisible(true);
      if (msg) {
        msg.textContent =
          "Демо-отчёт за " +
          ((res && res.report_date) || "вчера") +
          " создан (" +
          ((res && res.reports_updated) || 0) +
          "). Откройте вкладку Дайджест.";
        msg.classList.remove("hidden");
      }
    } catch (e) {
      if (err) {
        err.textContent = e.message || String(e);
        err.classList.remove("hidden");
      }
    }
  }

  function initials(me) {
    const fn = (me && me.first_name) || "";
    const parts = String(fn).trim().split(/\s+/);
    let s = "";
    if (parts[0]) s += parts[0][0];
    if (parts[1]) s += parts[1][0];
    if (!s && me && me.username) s = String(me.username).slice(0, 2);
    return (s || "?").toUpperCase();
  }

  /* ——— Profile ——— */
  function showProfileError(msg) {
    const el = document.querySelector("#panel-profile [data-error]");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
  }

  function clearProfileError() {
    const el = document.querySelector("#panel-profile [data-error]");
    if (!el) return;
    el.textContent = "";
    el.classList.add("hidden");
  }

  async function loadIntegrations() {
    let data;
    try {
      data = await apiFetch("/integrations", { method: "GET" });
    } catch (e) {
      showProfileError(e.message || String(e));
      return;
    }
    document.querySelectorAll("#panel-profile [data-svc]").forEach(function (card) {
      const key = card.getAttribute("data-svc");
      const on =
        key === "booking" ? !!data.booking_assistant : !!data[key];
      const connectBtn = card.querySelector('[data-action="connect"]');
      const disconnectBtn = card.querySelector('[data-action="disconnect"]');
      const zoomLine = card.querySelector("[data-zoom-line]");
      const yandexDiskLine = card.querySelector("[data-yandex-disk-line]");
      const bookingLine = card.querySelector("[data-booking-line]");
      if (key === "booking") {
        if (bookingLine) {
          var waOn = !!data.booking_whatsapp;
          if (on || waOn) {
            var parts = [];
            if (on) parts.push("Telegram");
            if (waOn) parts.push("WhatsApp");
            bookingLine.textContent = "● " + parts.join(" · ");
          } else {
            bookingLine.textContent =
              "Telegram и/или WhatsApp для переписки с ресторанами";
          }
          bookingLine.classList.toggle("svc-desc--connected", on || waOn);
        }
        if (connectBtn) {
          connectBtn.textContent = "Настроить";
          setHidden(connectBtn, false);
        }
        if (disconnectBtn) {
          setHidden(
            disconnectBtn,
            !on || !data.booking_assistant_can_disconnect
          );
        }
      } else if (key === "zoom") {
        if (zoomLine) {
          if (on) {
            var zwho =
              data.zoom_display_name || data.zoom_email || "аккаунт подключён";
            zoomLine.textContent = "● " + zwho;
          } else {
            zoomLine.textContent = "Создание ссылок на встречи";
          }
          zoomLine.classList.toggle("svc-desc--connected", on);
        }
        if (connectBtn) setHidden(connectBtn, on);
        if (disconnectBtn) setHidden(disconnectBtn, !on);
      } else if (key === "telemost") {
        var telemostLine = card.querySelector("[data-telemost-line]");
        if (telemostLine) {
          if (on) {
            var twho =
              data.telemost_display_name || data.telemost_email || "аккаунт подключён";
            telemostLine.textContent = "● " + twho;
            if (data.telemost_org_likely === false) {
              telemostLine.textContent += " (@yandex.ru — API недоступен)";
            }
          } else {
            telemostLine.textContent = "Создание ссылок на встречи";
          }
          telemostLine.classList.toggle("svc-desc--connected", on);
        }
        if (connectBtn) setHidden(connectBtn, on);
        if (disconnectBtn) setHidden(disconnectBtn, !on);
      } else if (key === "yandex-disk") {
        if (yandexDiskLine) {
          if (on) {
            if (data.yandex_disk_needs_scope_refresh) {
              yandexDiskLine.textContent = "● нужно обновить права (отключить → подключить)";
            } else {
              var ywho =
                data.yandex_disk_display_name || data.yandex_disk_login || "аккаунт подключён";
              yandexDiskLine.textContent = "● " + ywho;
            }
          } else {
            yandexDiskLine.textContent = "Записи Zoom-встреч";
          }
          yandexDiskLine.classList.toggle("svc-desc--connected", on);
        }
        if (connectBtn) setHidden(connectBtn, on);
        if (disconnectBtn) setHidden(disconnectBtn, !on);
      } else if (key === "bitrix") {
        var bitrixLine = card.querySelector("[data-bitrix-line]");
        if (bitrixLine) {
          if (on) {
            bitrixLine.textContent = "● Битрикс24 подключён";
          } else {
            bitrixLine.textContent = "Задачи и CRM через MCP";
          }
          bitrixLine.classList.toggle("svc-desc--connected", on);
        }
        if (connectBtn) setHidden(connectBtn, on);
        if (disconnectBtn) setHidden(disconnectBtn, !on);
      } else {
        var statusLine = card.querySelector("[data-status-line]");
        if (statusLine) {
          if (key === "google") {
            statusLine.textContent = on
              ? "● Google Calendar подключён"
              : "Синхронизация встреч";
          } else if (key === "todoist") {
            statusLine.textContent = on
              ? "● Todoist подключён"
              : "Задачи из заметок";
          }
          statusLine.classList.toggle("svc-desc--connected", on);
        }
        if (connectBtn) setHidden(connectBtn, on);
        if (disconnectBtn) setHidden(disconnectBtn, !on);
      }
    });
    clearProfileError();
  }

  function profileScreen(name) {
    name = name || "main";
    var prev = currentProfileScreen;
    currentProfileScreen = name;
    const main = document.getElementById("profile-main");
    const pay = document.getElementById("profile-payment");
    const exp = document.getElementById("profile-expenses");
    const booking = document.getElementById("profile-booking");
    const contacts = document.getElementById("profile-contacts");
    const calendars = document.getElementById("profile-calendars");
    const zoom = document.getElementById("profile-zoom");
    const telemost = document.getElementById("profile-telemost");
    const yandexDisk = document.getElementById("profile-yandex-disk");
    const bitrix = document.getElementById("profile-bitrix");
    const knowledgeBase = document.getElementById("profile-knowledge-base");
    const agents = document.getElementById("profile-agents");
    const chats = document.getElementById("profile-chats");
    setHidden(main, name !== "main");
    setHidden(pay, name !== "payment");
    setHidden(exp, name !== "expenses");
    if (booking) setHidden(booking, name !== "booking");
    if (contacts) setHidden(contacts, name !== "contacts");
    if (calendars) setHidden(calendars, name !== "calendars");
    if (zoom) setHidden(zoom, name !== "zoom");
    if (telemost) setHidden(telemost, name !== "telemost");
    if (yandexDisk) setHidden(yandexDisk, name !== "yandex-disk");
    if (bitrix) setHidden(bitrix, name !== "bitrix");
    if (knowledgeBase) setHidden(knowledgeBase, name !== "knowledge-base");
    if (agents) setHidden(agents, name !== "agents");
    if (chats) setHidden(chats, name !== "chats");
    if (name !== "booking") stopBookingWaQrPoll();
    if (
      name !== "zoom" &&
      name !== "telemost" &&
      name !== "yandex-disk" &&
      name !== "bitrix"
    ) {
      stopZoomStatusPoll();
    }
    if (name !== "main" && prev !== name) {
      var sub = document.getElementById("profile-" + name);
      pulseMotionEnter(sub);
    }
    syncTelegramNativeBack();
    syncAppOverlay();
    syncContactsCreateFab();
  }

  function openLegalLink(path) {
    var url = path;
    if (url.indexOf("http") !== 0) {
      url = window.location.origin + path;
    }
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.openLink) {
      tg.openLink(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  var oauthStatusPollTimer = null;

  function stopOAuthStatusPoll() {
    if (oauthStatusPollTimer) {
      clearInterval(oauthStatusPollTimer);
      oauthStatusPollTimer = null;
    }
  }

  function startOAuthStatusPoll() {
    stopOAuthStatusPoll();
    var n = 0;
    oauthStatusPollTimer = setInterval(async function () {
      n += 1;
      await loadIntegrations();
      var zoom = document.getElementById("profile-zoom");
      if (zoom && !zoom.classList.contains("hidden")) {
        await loadZoomScreen(false);
      }
      var telemost = document.getElementById("profile-telemost");
      if (telemost && !telemost.classList.contains("hidden")) {
        await loadTelemostScreen(false);
      }
      if (n >= 15) stopOAuthStatusPoll();
    }, 2000);
  }

  function stopZoomStatusPoll() {
    stopOAuthStatusPoll();
  }

  function startZoomStatusPoll() {
    startOAuthStatusPoll();
  }

  async function loadZoomRecordings() {
    var list = document.getElementById("profile-zoom-recordings");
    var empty = document.getElementById("profile-zoom-recordings-empty");
    if (!list) return;
    list.innerHTML = "";
    try {
      var data = await apiFetch("/zoom/recordings", { method: "GET" });
      var items = data.items || [];
      if (!items.length) {
        setHidden(empty, false);
        return;
      }
      setHidden(empty, true);
      items.forEach(function (rec) {
        var card = document.createElement("div");
        card.className = "zoom-recording-card";
        var title = document.createElement("p");
        title.className = "zoom-recording-topic";
        title.textContent = rec.topic || "Встреча";
        var meta = document.createElement("p");
        meta.className = "zoom-recording-meta";
        var when = rec.start_at_utc || rec.created_at_utc || rec.start_time || rec.created_at || "";
        var status = rec.status || "";
        var src = status || (rec.source === "local" ? "локальная" : rec.source === "cloud" ? "облако" : "");
        meta.textContent = [when ? when.replace("T", " ").slice(0, 16) : "", src].filter(Boolean).join(" · ");
        card.appendChild(title);
        card.appendChild(meta);
        var link = rec.play_url || rec.download_url;
        if (link) {
          var a = document.createElement("a");
          a.className = "zoom-recording-link";
          a.href = link;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = "Открыть запись";
          a.addEventListener("click", function (e) {
            e.preventDefault();
            openLegalLink(link);
          });
          card.appendChild(a);
        }
        list.appendChild(card);
      });
    } catch (_) {
      setHidden(empty, false);
    }
  }

  async function loadZoomScreen(showLoading) {
    var stEl = document.getElementById("profile-zoom-status");
    var connectBtn = document.getElementById("profile-zoom-connect");
    var discBtn = document.getElementById("profile-zoom-disconnect");
    var msg = document.getElementById("profile-zoom-msg");
    var err = document.getElementById("profile-zoom-err");
    initZoomAutoRecordToggle();
    if (msg) setHidden(msg, true);
    if (err) setHidden(err, true);
    if (showLoading !== false && stEl) stEl.textContent = "Проверка…";
    try {
      var results = await Promise.all([
        apiFetch("/zoom/status", { method: "GET" }),
        apiFetch("/settings", { method: "GET" }),
      ]);
      var st = results[0] || {};
      var settings = results[1] || {};
      applyZoomAutoRecordToggleUi(
        !!settings.zoom_auto_record_enabled,
        !!settings.meeting_bot_available
      );
      var on = !!(st && st.connected);
      if (stEl) {
        if (on) {
          var who = st.zoom_display_name || st.zoom_email || "аккаунт подключён";
          stEl.textContent = "● " + who;
        } else {
          stEl.textContent = "Не подключён";
        }
      }
      if (connectBtn) setHidden(connectBtn, on);
      if (discBtn) setHidden(discBtn, !on);
      if (on) await loadZoomRecordings();
    } catch (e) {
      if (stEl) stEl.textContent = "Ошибка загрузки";
      if (err) {
        err.textContent = e.message || String(e);
        setHidden(err, false);
      }
    }
  }

  async function openZoomSetup() {
    profileScreen("zoom");
    await loadZoomScreen(true);
  }

  var telemostAutoRecordEnabled = false;
  var telemostAutoRecordSaveInFlight = false;

  function applyTelemostAutoRecordToggleUi(enabled) {
    var row = document.getElementById("telemost-auto-record-setting");
    var toggle = document.getElementById("telemost-auto-record-toggle");
    if (!row || !toggle) return;
    telemostAutoRecordEnabled = !!enabled;
    setHidden(row, false);
    toggle.classList.toggle("svc-toggle--on", telemostAutoRecordEnabled);
    toggle.classList.toggle("svc-toggle--pending", telemostAutoRecordSaveInFlight);
    toggle.setAttribute("aria-pressed", telemostAutoRecordEnabled ? "true" : "false");
    toggle.setAttribute("aria-checked", telemostAutoRecordEnabled ? "true" : "false");
  }

  async function saveTelemostAutoRecordEnabled(enabled) {
    var errEl = document.getElementById("telemost-auto-record-err");
    var toggle = document.getElementById("telemost-auto-record-toggle");
    telemostAutoRecordSaveInFlight = true;
    if (toggle) toggle.classList.add("svc-toggle--pending");
    try {
      var data = await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify({ telemost_auto_record_enabled: !!enabled }),
      });
      applyTelemostAutoRecordToggleUi(!!(data && data.telemost_auto_record_enabled));
      if (errEl) setHidden(errEl, true);
    } catch (e) {
      applyTelemostAutoRecordToggleUi(!enabled);
      if (errEl) {
        errEl.textContent = e.message || String(e);
        setHidden(errEl, false);
      }
    } finally {
      telemostAutoRecordSaveInFlight = false;
      if (toggle) toggle.classList.remove("svc-toggle--pending");
    }
  }

  function initTelemostAutoRecordToggle() {
    var toggle = document.getElementById("telemost-auto-record-toggle");
    if (!toggle || toggle.getAttribute("data-bound") === "1") return;
    toggle.setAttribute("data-bound", "1");
    toggle.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (telemostAutoRecordSaveInFlight) return;
      var next = !telemostAutoRecordEnabled;
      applyTelemostAutoRecordToggleUi(next);
      saveTelemostAutoRecordEnabled(next);
    });
  }

  async function loadTelemostScreen(showLoading) {
    if (showLoading !== false) {
      await loadIntegrations();
    }
    var stEl = document.getElementById("profile-telemost-status");
    var authBtn = document.getElementById("profile-telemost-auth");
    var submitBtn = document.getElementById("profile-telemost-submit");
    var discBtn = document.getElementById("profile-telemost-disconnect");
    var codeInp = document.getElementById("profile-telemost-code");
    var msg = document.getElementById("profile-telemost-msg");
    var err = document.getElementById("profile-telemost-err");
    var orgWarn = document.getElementById("profile-telemost-org-warn");
    initTelemostAutoRecordToggle();
    if (msg) setHidden(msg, true);
    if (err) setHidden(err, true);
    if (showLoading !== false && stEl) stEl.textContent = "Проверка…";
    try {
      var results = await Promise.all([
        apiFetch("/telemost/status", { method: "GET" }),
        apiFetch("/settings", { method: "GET" }),
      ]);
      var st = results[0] || {};
      var settings = results[1] || {};
      applyTelemostAutoRecordToggleUi(!!settings.telemost_auto_record_enabled);
      var on = !!(st && st.connected);
      if (orgWarn) setHidden(orgWarn, st.telemost_org_likely !== false);
      if (stEl) {
        if (on) {
          var who = st.telemost_display_name || st.telemost_email || "аккаунт подключён";
          stEl.textContent = "● " + who;
          if (st.telemost_org_likely === false) {
            stEl.textContent += " (@yandex.ru — только вручную)";
          }
        } else {
          stEl.textContent = "Не подключён";
        }
      }
      if (authBtn) setHidden(authBtn, on);
      if (submitBtn) setHidden(submitBtn, on);
      if (codeInp) {
        var codeField = codeInp.closest(".field");
        if (codeField) setHidden(codeField, on);
      }
      if (discBtn) setHidden(discBtn, !on);
    } catch (e) {
      if (stEl) stEl.textContent = "Ошибка загрузки";
      if (err) {
        err.textContent = e.message || String(e);
        setHidden(err, false);
      }
    }
  }

  async function submitTelemostCode(code) {
    var errEl = document.getElementById("profile-telemost-err");
    var msgEl = document.getElementById("profile-telemost-msg");
    if (errEl) setHidden(errEl, true);
    if (msgEl) setHidden(msgEl, true);
    clearProfileError();
    try {
      await apiFetch("/oauth/telemost/code", {
        method: "POST",
        body: JSON.stringify({ code: code }),
      });
      var codeInp = document.getElementById("profile-telemost-code");
      if (codeInp) codeInp.value = "";
      if (msgEl) {
        msgEl.textContent = "Телемост подключён.";
        setHidden(msgEl, false);
      }
      await loadIntegrations();
      await loadTelemostScreen(false);
      stopOAuthStatusPoll();
      var tgApp = window.Telegram && window.Telegram.WebApp;
      if (tgApp && tgApp.HapticFeedback) tgApp.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      var errText = "Телемост: " + (e.message || e);
      if (errEl) {
        errEl.textContent = errText;
        setHidden(errEl, false);
      } else {
        showProfileError(errText);
      }
    }
  }

  async function openTelemostAuthLink() {
    var errEl = document.getElementById("profile-telemost-err");
    if (errEl) setHidden(errEl, true);
    try {
      const data = await apiFetch("/oauth/telemost/start", { method: "POST" });
      const url = data && data.url;
      if (!url) throw new Error("нет URL авторизации");
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.openLink) {
        tg.openLink(url);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
      var codeInp = document.getElementById("profile-telemost-code");
      if (codeInp) codeInp.focus();
      startOAuthStatusPoll();
    } catch (e) {
      if (errEl) {
        errEl.textContent = e.message || String(e);
        setHidden(errEl, false);
      }
    }
  }

  async function openTelemostSetup() {
    profileScreen("telemost");
    await loadTelemostScreen(true);
  }

  function bookingShowMsg(okId, errId, okText, errText) {
    const ok = document.getElementById(okId);
    const er = document.getElementById(errId);
    if (ok) {
      ok.textContent = okText || "";
      setHidden(ok, !okText);
    }
    if (er) {
      er.textContent = errText || "";
      setHidden(er, !errText);
    }
  }

  var bookingWaPollTimer = null;

  function stopBookingWaQrPoll() {
    if (bookingWaPollTimer) {
      clearInterval(bookingWaPollTimer);
      bookingWaPollTimer = null;
    }
  }

  function setBookingWaUi(st) {
    const connected = !!(st && st.connected);
    const qrUrl = st && st.qr_data_url ? st.qr_data_url : null;
    const wrap = document.getElementById("booking-wa-qr-wrap");
    const img = document.getElementById("booking-wa-qr-img");
    const startBtn = document.getElementById("booking-wa-start");
    const discBtn = document.getElementById("booking-wa-disconnect");
    if (wrap) setHidden(wrap, !qrUrl || connected);
    if (img && qrUrl) img.src = qrUrl;
    if (startBtn) setHidden(startBtn, connected);
    if (discBtn) setHidden(discBtn, !connected);
    if (connected) stopBookingWaQrPoll();
  }

  async function loadBookingWhatsAppStatus() {
    bookingShowMsg("booking-wa-msg", "booking-wa-err", "", "");
    let st;
    try {
      st = await apiFetch("/booking-assistant/whatsapp/status", { method: "GET" });
    } catch (e) {
      setBookingWaUi({ connected: false });
      return;
    }
    setBookingWaUi(st);
    if (st && st.connected) {
      bookingShowMsg(
        "booking-wa-msg",
        "booking-wa-err",
        "WhatsApp подключён. Заявки с телефоном заведения пойдут в WhatsApp.",
        ""
      );
    } else if (st && st.has_qr && st.qr_data_url) {
      bookingShowMsg(
        "booking-wa-msg",
        "booking-wa-err",
        "Отсканируйте QR в приложении WhatsApp (Связанные устройства).",
        ""
      );
    }
  }

  function startBookingWaQrPoll() {
    stopBookingWaQrPoll();
    bookingWaPollTimer = setInterval(async function () {
      try {
        const st = await apiFetch("/booking-assistant/whatsapp/qr", { method: "GET" });
        setBookingWaUi(st);
        if (st && st.connected) {
          bookingShowMsg(
            "booking-wa-msg",
            "booking-wa-err",
            "WhatsApp подключён.",
            ""
          );
          await loadIntegrations();
          stopBookingWaQrPoll();
        }
      } catch (_) {
        /* ignore transient poll errors */
      }
    }, 3000);
  }

  async function bookingWaStart() {
    bookingShowMsg(
      "booking-wa-msg",
      "booking-wa-err",
      "Подключаю WhatsApp, ждите QR (до ~30 с)…",
      ""
    );
    stopBookingWaQrPoll();
    startBookingWaQrPoll();
    try {
      const st = await apiFetch("/booking-assistant/whatsapp/start", {
        method: "POST",
        body: JSON.stringify({}),
      });
      setBookingWaUi(st);
      if (st && st.connected) {
        bookingShowMsg(
          "booking-wa-msg",
          "booking-wa-err",
          "WhatsApp уже подключён.",
          ""
        );
        stopBookingWaQrPoll();
        await loadIntegrations();
        return;
      }
      if (st && st.qr_data_url) {
        bookingShowMsg(
          "booking-wa-msg",
          "booking-wa-err",
          "Отсканируйте QR в WhatsApp → Связанные устройства.",
          ""
        );
        return;
      }
      bookingShowMsg(
        "booking-wa-msg",
        "booking-wa-err",
        "Жду QR от сервера… Если не появится — обновите страницу и проверьте bridge.",
        ""
      );
    } catch (e) {
      bookingShowMsg("booking-wa-msg", "booking-wa-err", "", e.message || String(e));
    }
  }

  async function bookingWaDisconnect() {
    stopBookingWaQrPoll();
    bookingShowMsg("booking-wa-msg", "booking-wa-err", "", "");
    try {
      await apiFetch("/booking-assistant/whatsapp/disconnect", { method: "POST" });
      setBookingWaUi({ connected: false });
      bookingShowMsg("booking-wa-msg", "booking-wa-err", "WhatsApp отключён.", "");
      await loadIntegrations();
    } catch (e) {
      bookingShowMsg("booking-wa-msg", "booking-wa-err", "", e.message || String(e));
    }
  }

  async function loadBookingConfigForm() {
    bookingShowMsg("booking-config-msg", "booking-config-err", "", "");
    bookingShowMsg("booking-login-msg", "booking-login-err", "", "");
    let cfg;
    try {
      cfg = await apiFetch("/booking-assistant/config", { method: "GET" });
    } catch (e) {
      bookingShowMsg("booking-config-msg", "booking-config-err", "", e.message || String(e));
      return;
    }
    const apiId = document.getElementById("booking-api-id");
    const apiHash = document.getElementById("booking-api-hash");
    const dn = document.getElementById("booking-display-name");
    const ph = document.getElementById("booking-owner-phone");
    const g = document.getElementById("booking-gender");
    if (apiId && cfg.api_id != null) apiId.value = String(cfg.api_id);
    if (dn) dn.value = cfg.owner_display_name || "";
    if (ph) ph.value = cfg.owner_phone || "";
    if (g) g.value = cfg.assistant_gender === "female" ? "female" : "male";
    if (apiHash) apiHash.placeholder = cfg.api_hash_set ? "•••••••• (уже сохранён)" : "";
    if (cfg.connected && cfg.uses_server_session) {
      bookingShowMsg(
        "booking-config-msg",
        "booking-config-err",
        "Используется серверный ассистент владельца (настроен администратором).",
        ""
      );
    } else if (cfg.has_own_session) {
      bookingShowMsg(
        "booking-login-msg",
        "booking-login-err",
        "Ваш ассистент подключён и готов к бронированию.",
        ""
      );
    }
  }

  async function openBookingSetup() {
    clearProfileError();
    profileScreen("booking");
    await loadBookingConfigForm();
    await loadBookingWhatsAppStatus();
  }

  async function bookingSaveConfig() {
    const apiIdEl = document.getElementById("booking-api-id");
    const apiHashEl = document.getElementById("booking-api-hash");
    const body = {
      owner_display_name: (document.getElementById("booking-display-name") || {}).value || "",
      owner_phone: (document.getElementById("booking-owner-phone") || {}).value || "",
      assistant_gender: (document.getElementById("booking-gender") || {}).value || "male",
    };
    const rawId = apiIdEl && apiIdEl.value ? apiIdEl.value.trim() : "";
    const rawHash = apiHashEl && apiHashEl.value ? apiHashEl.value.trim() : "";
    if (rawId) body.api_id = parseInt(rawId, 10);
    if (rawHash) body.api_hash = rawHash;
    bookingShowMsg("booking-config-msg", "booking-config-err", "", "");
    try {
      await apiFetch("/booking-assistant/config", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      bookingShowMsg(
        "booking-config-msg",
        "booking-config-err",
        "Настройки сохранены. Отправьте код входа для аккаунта ассистента.",
        ""
      );
      if (apiHashEl) apiHashEl.value = "";
      await loadBookingConfigForm();
    } catch (e) {
      bookingShowMsg("booking-config-msg", "booking-config-err", "", e.message || String(e));
    }
  }

  async function bookingSendCode() {
    const phone = (document.getElementById("booking-assistant-phone") || {}).value || "";
    bookingShowMsg("booking-login-msg", "booking-login-err", "", "");
    try {
      const r = await apiFetch("/booking-assistant/login/start", {
        method: "POST",
        body: JSON.stringify({ phone: phone }),
      });
      if (r && r.already_authorized) {
        bookingShowMsg(
          "booking-login-msg",
          "booking-login-err",
          "Ассистент уже авторизован.",
          ""
        );
        await loadIntegrations();
        return;
      }
      bookingShowMsg(
        "booking-login-msg",
        "booking-login-err",
        "Код отправлен в Telegram на номер " + (r.phone || phone) + ".",
        ""
      );
    } catch (e) {
      bookingShowMsg("booking-login-msg", "booking-login-err", "", e.message || String(e));
    }
  }

  async function bookingConfirmLogin() {
    const code = (document.getElementById("booking-login-code") || {}).value || "";
    const password = (document.getElementById("booking-login-password") || {}).value || "";
    bookingShowMsg("booking-login-msg", "booking-login-err", "", "");
    try {
      const r = await apiFetch("/booking-assistant/login/confirm", {
        method: "POST",
        body: JSON.stringify({ code: code, password: password || null }),
      });
      const un = r.username ? "@" + r.username : r.first_name || "аккаунт";
      bookingShowMsg(
        "booking-login-msg",
        "booking-login-err",
        "Готово: " + un + ". Можно бронировать в боте.",
        ""
      );
      await loadIntegrations();
    } catch (e) {
      bookingShowMsg("booking-login-msg", "booking-login-err", "", e.message || String(e));
    }
  }

  async function bookingDisconnect() {
    clearProfileError();
    try {
      await apiFetch("/booking-assistant/disconnect", { method: "POST" });
    } catch (e) {
      showProfileError("Бронирование: " + (e.message || e));
      return;
    }
    await loadIntegrations();
  }

  function renderPaymentMethods() {
    const wrap = document.getElementById("payment-methods");
    if (!wrap) return;
    const methods = [
      { id: "card", label: "Банковская карта", icon: "💳" },
      { id: "tinkoff", label: "Tinkoff Pay", icon: "🟡" },
      { id: "sbp", label: "СБП (Система быстрых платежей)", icon: "⚡" },
      { id: "crypto", label: "Криптовалюта (USDT)", icon: "₿" },
    ];
    wrap.innerHTML = "";
    methods.forEach(function (m) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "payment-row";
      b.setAttribute("data-mid", m.id);
      b.innerHTML =
        '<span class="payment-row-icon" aria-hidden="true">' +
        m.icon +
        "</span>" +
        '<span class="payment-row-label">' +
        m.label +
        "</span>" +
        '<span class="pay-radio"></span>';
      b.addEventListener("click", function () {
        selectedPaymentMethod = m.id;
        wrap.querySelectorAll(".payment-row").forEach(function (r) {
          r.classList.toggle("selected", r.getAttribute("data-mid") === m.id);
        });
      });
      wrap.appendChild(b);
    });
  }

  function syncPaymentSummary() {
    const custom = document.getElementById("payment-custom-amount");
    const raw = String((custom && custom.value) || "")
      .trim()
      .replace(/\s/g, "")
      .replace(",", ".");
    let v = parseFloat(raw);
    if (!isFinite(v) || v <= 0) {
      const active = document.querySelector("#payment-amount-chips .amount-chip--active");
      const dr = active && active.getAttribute("data-rub");
      v = dr ? parseInt(dr, 10) : 500;
    } else {
      v = Math.round(v);
    }
    const r = "₽ " + v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
    const sumLine = document.getElementById("payment-sum-line");
    const totalLine = document.getElementById("payment-total-line");
    const sub = document.getElementById("payment-submit");
    if (sumLine) sumLine.textContent = r;
    if (totalLine) totalLine.textContent = r;
    if (sub) sub.textContent = "Оплатить " + r;
  }

  async function loadProfile() {
    profileScreen("main");
    try {
      const me = await apiFetch("/me", { method: "GET" });
      cachedMe = me;
      if (me.bot_username) cachedBotUsername = me.bot_username;

      const head = document.getElementById("profile-head");
      head.innerHTML =
        '<div class="avatar">' +
        initials(me) +
        "</div>" +
        '<div class="profile-names">' +
        "<h2>" +
        (me.first_name || "Пользователь") +
        "</h2>" +
        "<p>@" +
        (me.username || "—") +
        " · id " +
        (me.id != null ? me.id : "—") +
        "</p></div>";

      const b = await apiFetch("/billing", { method: "GET" });
      const tariffCard = document.getElementById("profile-tariff-card");
      const bal =
        b.show_balance && typeof b.balance_rub === "number"
          ? b.balance_rub.toLocaleString("ru-RU", {
              minimumFractionDigits: 0,
              maximumFractionDigits: 2,
            }) + " ₽"
          : "—";
      tariffCard.innerHTML =
        '<div class="tariff-row-top">' +
        '<span class="tariff-name">' +
        (b.tariff_label || "Тариф") +
        "</span>" +
        '<span class="tariff-pill">Активен</span></div>' +
        '<p class="tariff-balance-label">Текущий баланс</p>' +
        '<p class="tariff-balance">' +
        (b.show_balance ? bal : "Безлимит") +
        "</p>" +
        '<button type="button" class="tariff-pay" id="profile-open-pay">Пополнить баланс</button>';

      var payBtn = document.getElementById("profile-open-pay");
      if (payBtn) {
        payBtn.addEventListener("click", function () {
          profileScreen("payment");
          renderPaymentMethods();
          syncPaymentSummary();
        });
      }

      try {
        const ex = await apiFetch("/usage/expenses?days=14", { method: "GET" });
        const total = typeof ex.total_rub === "number" ? ex.total_rub : 0;
        var expTotal = document.getElementById("profile-expenses-total");
        if (expTotal) {
          expTotal.textContent =
            "₽ " + total.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
        }
      } catch (_) {
        var expTotalFail = document.getElementById("profile-expenses-total");
        if (expTotalFail) expTotalFail.textContent = "—";
      }
      await refreshProfileContactsCount();
      await refreshKnowledgeBaseHint();
      await syncDigestSettingsFromServer();
    } catch (e) {
      showProfileError(e.message || String(e));
    }
    await loadIntegrations();
  }

  async function loadExpensesDetail() {
    const body = document.getElementById("profile-expenses-body");
    if (!body) return;
    body.innerHTML = "<p class=\"muted small\">Загрузка…</p>";
    try {
      const ex = await apiFetch("/usage/expenses?days=14", { method: "GET" });
      const days = ex.days || [];
      const total = ex.total_rub || 0;
      let html =
        '<div class="stat-banner">' +
        '<p class="stat-banner-label">Всего за период</p>' +
        '<p class="stat-banner-value">₽ ' +
        Number(total).toLocaleString("ru-RU", { maximumFractionDigits: 2 }) +
        "</p></div>";

      days.forEach(function (d) {
        const dayId = "expd-" + d.date_utc;
        const reqs = d.requests || [];
        let inner = "";
        reqs.forEach(function (r) {
          inner +=
            '<div class="exp-day-req"><span class="muted exp-day-req-label">' +
            (r.label || "—") +
            "</span><span>₽ " +
            Number(r.cost_rub || 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 }) +
            "</span></div>";
        });
        html +=
          '<div class="exp-day">' +
          '<button type="button" class="exp-day-head" data-toggle="' +
          dayId +
          '">' +
          "<span><strong>" +
          d.date_utc +
          "</strong></span>" +
          "<span>₽ " +
          Number(d.total_rub || 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 }) +
          " ›</span></button>" +
          '<div id="' +
          dayId +
          '" class="hidden">' +
          inner +
          "</div></div>";
      });
      body.innerHTML = html || "<p class=\"muted\">Нет расходов с учётом cost_usd за период.</p>";

      body.querySelectorAll(".exp-day-head").forEach(function (btn) {
        btn.addEventListener("click", function () {
          const id = btn.getAttribute("data-toggle");
          const el = document.getElementById(id);
          if (el) el.classList.toggle("hidden");
        });
      });
    } catch (e) {
      body.innerHTML = "<p class=\"error\">" + (e.message || e) + "</p>";
    }
  }

  var contactsEditingEmail = null;

  function contactsShowFormMsg(okText, errText) {
    var ok = document.getElementById("contacts-form-msg");
    var er = document.getElementById("contacts-form-err");
    if (ok) {
      ok.textContent = okText || "";
      setHidden(ok, !okText);
    }
    if (er) {
      er.textContent = errText || "";
      setHidden(er, !errText);
    }
  }

  var contactsFormTeamSelected = {};

  function contactsResetForm() {
    contactsEditingEmail = null;
    contactsFormTeamSelected = {};
    var hid = document.getElementById("contacts-edit-email");
    if (hid) hid.value = "";
    var t = document.getElementById("contacts-form-title");
    if (t) t.textContent = "Новый контакт";
    var n = document.getElementById("contacts-f-name");
    var e = document.getElementById("contacts-f-email");
    var tg = document.getElementById("contacts-f-tg");
    var al = document.getElementById("contacts-f-aliases");
    if (n) n.value = "";
    if (e) {
      e.value = "";
      e.disabled = false;
    }
    if (tg) tg.value = "";
    if (al) al.value = "";
    contactsShowFormMsg("", "");
    paintContactsFormTeams([]);
  }

  function selectedContactFormTeamIds() {
    return Object.keys(contactsFormTeamSelected)
      .filter(function (id) {
        return contactsFormTeamSelected[id];
      })
      .map(function (id) {
        return parseInt(id, 10);
      })
      .filter(function (id) {
        return id > 0;
      });
  }

  function paintContactsFormTeams(allTeams) {
    var list = document.getElementById("contacts-form-teams-list");
    var empty = document.getElementById("contacts-form-teams-empty");
    if (!list) return;
    list.innerHTML = "";
    var teams = allTeams || [];
    if (empty) empty.classList.toggle("hidden", !!teams.length);
    teams.forEach(function (team) {
      var id = String(team.id);
      var label = document.createElement("label");
      label.className = "note-share-option contacts-form-team-option";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!contactsFormTeamSelected[id];
      input.addEventListener("change", function () {
        contactsFormTeamSelected[id] = !!input.checked;
      });
      var copy = document.createElement("span");
      var strong = document.createElement("strong");
      strong.textContent = team.name || "Команда";
      copy.appendChild(strong);
      label.appendChild(input);
      label.appendChild(copy);
      list.appendChild(label);
    });
  }

  function updateContactsFormTelegramHint() {
    var hint = document.getElementById("contacts-form-teams-hint");
    if (!hint) return;
    var tg = (document.getElementById("contacts-f-tg") && document.getElementById("contacts-f-tg").value) || "";
    var hasTg = !!String(tg).replace(/^@/, "").trim();
    hint.classList.toggle("hidden", hasTg);
  }

  async function loadContactsFormTeams(preselectedIds) {
    contactsFormTeamSelected = {};
    (preselectedIds || []).forEach(function (id) {
      contactsFormTeamSelected[String(id)] = true;
    });
    try {
      var data = await apiFetch("/teams", { method: "GET" });
      paintContactsFormTeams((data && data.teams) || []);
    } catch (_) {
      paintContactsFormTeams([]);
    }
    updateContactsFormTelegramHint();
  }

  function openContactsFormModal() {
    var ov = document.getElementById("contacts-form-overlay");
    if (!ov) return;
    ov.classList.remove("hidden");
    ov.setAttribute("aria-hidden", "false");
    syncAppOverlay();
    var nameInput = document.getElementById("contacts-f-name");
    if (nameInput) {
      setTimeout(function () {
        try {
          nameInput.focus();
        } catch (_) {}
      }, 50);
    }
  }

  function closeContactsFormModal() {
    var ov = document.getElementById("contacts-form-overlay");
    if (ov) {
      ov.classList.add("hidden");
      ov.setAttribute("aria-hidden", "true");
    }
    contactsResetForm();
    syncAppOverlay();
  }

  async function openNewContactModal() {
    contactsResetForm();
    await loadContactsFormTeams([]);
    openContactsFormModal();
  }

  async function contactsFillForm(c) {
    contactsEditingEmail = (c && c.email) || null;
    var hid = document.getElementById("contacts-edit-email");
    if (hid) hid.value = contactsEditingEmail || "";
    var t = document.getElementById("contacts-form-title");
    if (t) t.textContent = "Редактирование";
    var n = document.getElementById("contacts-f-name");
    var e = document.getElementById("contacts-f-email");
    var tg = document.getElementById("contacts-f-tg");
    var al = document.getElementById("contacts-f-aliases");
    if (n) n.value = (c && c.name) || "";
    if (e) {
      e.value = (c && c.email) || "";
      e.disabled = false;
    }
    if (tg) tg.value = (c && c.telegram_username) || "";
    if (al) {
      var aliases = (c && c.aliases) || [];
      al.value = Array.isArray(aliases) ? aliases.join(", ") : "";
    }
    contactsShowFormMsg("", "");
    var preselected = ((c && c.teams) || []).map(function (team) {
      return team && team.id;
    }).filter(Boolean);
    openContactsFormModal();
    await loadContactsFormTeams(preselected);
  }

  function contactsParseAliases(raw) {
    if (!raw || !String(raw).trim()) return [];
    return String(raw)
      .split(/[,;]+/)
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
  }

  function renderContactCard(c) {
    var card = document.createElement("div");
    card.className = "contact-card";
    var name = document.createElement("p");
    name.className = "contact-card-name";
    name.textContent = c.name || "(без имени)";
    var meta = document.createElement("div");
    meta.className = "contact-card-meta";
    var lines = [c.email || ""];
    if (c.telegram_username) lines.push("@" + c.telegram_username);
    if (c.aliases && c.aliases.length) {
      lines.push("Также: " + c.aliases.join(", "));
    }
    meta.textContent = lines.filter(Boolean).join("\n");
    card.appendChild(name);
    card.appendChild(meta);
    var teams = Array.isArray(c.teams) ? c.teams : [];
    if (teams.length) {
      var teamsEl = document.createElement("p");
      teamsEl.className = "contact-card-teams muted small";
      teamsEl.textContent =
        "Команды: " +
        teams
          .map(function (t) {
            return t.name || "Команда";
          })
          .join(", ");
      card.appendChild(teamsEl);
    }
    var actions = document.createElement("div");
    actions.className = "contact-card-actions";
    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn ghost btn-sm";
    editBtn.textContent = "Изменить";
    editBtn.addEventListener("click", function () {
      contactsFillForm(c);
    });
    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn ghost btn-sm";
    delBtn.textContent = "Удалить";
    delBtn.addEventListener("click", async function () {
      if (!confirm("Удалить контакт «" + (c.name || c.email) + "»?")) return;
      try {
        await apiFetch("/contacts/" + encodeURIComponent(c.email), { method: "DELETE" });
        if (contactsEditingEmail === c.email) closeContactsFormModal();
        await loadContacts();
        await refreshProfileContactsCount();
      } catch (err) {
        alert(err.message || String(err));
      }
    });
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);
    card.appendChild(actions);
    return card;
  }

  var teamPickerState = {
    mode: "",
    email: "",
    noteId: "",
    hasTelegram: true,
    selected: {},
    teams: [],
    loadGen: 0,
  };

  function contactHasTelegram(contact) {
    if (!contact) return false;
    if (contact.telegram_user_id) return true;
    return !!(String(contact.telegram_username || "").replace(/^@/, "").trim());
  }

  function closeTeamPicker() {
    var ov = document.getElementById("team-picker-overlay");
    if (ov) ov.classList.add("hidden");
    teamPickerState.mode = "";
    teamPickerState.email = "";
    teamPickerState.noteId = "";
  }

  function bindTeamPickerControls(ov) {
    if (!ov || ov._teamPickerBound) return;
    ov._teamPickerBound = true;
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeTeamPicker();
    });
    var createBtn = ov.querySelector("#team-picker-create");
    var saveBtn = ov.querySelector("#team-picker-save");
    var cancelBtn = ov.querySelector("#team-picker-cancel");
    var nameInput = ov.querySelector("#team-picker-new-name");
    if (createBtn) {
      createBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        createTeamFromPicker();
      });
    }
    if (nameInput) {
      nameInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          e.stopPropagation();
          createTeamFromPicker();
        }
      });
    }
    if (saveBtn) {
      saveBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        saveTeamPicker();
      });
    }
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        closeTeamPicker();
      });
    }
  }

  function ensureTeamPickerOverlay() {
    var ov = document.getElementById("team-picker-overlay");
    if (ov) {
      if (!ov.querySelector("#team-picker-error")) {
        var createRow = ov.querySelector(".team-picker-create");
        if (createRow && createRow.parentNode) {
          var err = document.createElement("p");
          err.id = "team-picker-error";
          err.className = "error hidden";
          createRow.parentNode.insertBefore(err, createRow.nextSibling);
        }
      }
      bindTeamPickerControls(ov);
      return ov;
    }
    ov = document.createElement("div");
    ov.id = "team-picker-overlay";
    ov.className = "note-share-sheet-overlay hidden";
    ov.innerHTML =
      '<div class="note-share-sheet" role="dialog" aria-labelledby="team-picker-title">' +
      '<h3 id="team-picker-title" class="note-share-sheet-title">Команды</h3>' +
      '<p id="team-picker-hint" class="note-share-members-hint muted small"></p>' +
      '<div class="team-picker-create">' +
      '<input id="team-picker-new-name" type="text" class="field-input" maxlength="80" placeholder="Название команды" autocomplete="off" />' +
      '<button type="button" class="btn ghost btn-sm" id="team-picker-create">Создать</button>' +
      "</div>" +
      '<p id="team-picker-error" class="error hidden"></p>' +
      '<div id="team-picker-list" class="team-picker-list"></div>' +
      '<p id="team-picker-empty" class="team-picker-empty muted small">Пока нет команд — создайте первую.</p>' +
      '<div class="note-share-sheet-actions">' +
      '<button type="button" class="btn" id="team-picker-save">Сохранить</button>' +
      '<button type="button" class="btn-text" id="team-picker-cancel">Закрыть</button>' +
      "</div></div>";
    document.body.appendChild(ov);
    bindTeamPickerControls(ov);
    return ov;
  }

  function selectedTeamIdsFromPicker() {
    return Object.keys(teamPickerState.selected)
      .filter(function (id) {
        return teamPickerState.selected[id];
      })
      .map(function (id) {
        return parseInt(id, 10);
      })
      .filter(function (id) {
        return id > 0;
      });
  }

  function paintTeamPickerList() {
    var list = document.getElementById("team-picker-list");
    var empty = document.getElementById("team-picker-empty");
    if (!list) return;
    list.innerHTML = "";
    var teams = teamPickerState.teams || [];
    if (empty) empty.classList.toggle("hidden", !!teams.length);
    teams.forEach(function (team) {
      var id = String(team.id);
      var label = document.createElement("label");
      label.className = "note-share-option";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!teamPickerState.selected[id];
      input.addEventListener("change", function () {
        teamPickerState.selected[id] = !!input.checked;
      });
      var copy = document.createElement("span");
      var strong = document.createElement("strong");
      strong.textContent = team.name || "Команда";
      copy.appendChild(strong);
      var count = Number(team.member_count || 0);
      if (count) {
        var small = document.createElement("small");
        small.textContent =
          count +
          " " +
          (count === 1 ? "участник" : count < 5 ? "участника" : "участников");
        copy.appendChild(small);
      }
      label.appendChild(input);
      label.appendChild(copy);
      list.appendChild(label);
    });
  }

  function applyTeamPickerHint() {
    var hint = document.getElementById("team-picker-hint");
    if (!hint) return;
    if (teamPickerState.mode === "contact" && !teamPickerState.hasTelegram) {
      hint.textContent =
        "У контакта нет Telegram — файл появится в его базе знаний после того, как он откроет Leo.";
      hint.classList.remove("hidden");
      return;
    }
    if (teamPickerState.mode === "note") {
      hint.textContent =
        "Выберите команды — документ появится в базе знаний у всех участников.";
      hint.classList.remove("hidden");
      return;
    }
    hint.textContent = "Можно выбрать несколько команд.";
    hint.classList.remove("hidden");
  }

  function setTeamPickerError(text) {
    var el = document.getElementById("team-picker-error");
    if (el) {
      el.textContent = text || "";
      el.classList.toggle("hidden", !text);
    }
    if (text && typeof showNoteToast === "function") {
      try {
        showNoteToast(text);
      } catch (_) {}
    }
  }

  function mergeTeamsIntoPicker(serverTeams, selectedIds) {
    var byId = {};
    (teamPickerState.teams || []).forEach(function (team) {
      if (team && team.id != null) byId[String(team.id)] = team;
    });
    (serverTeams || []).forEach(function (team) {
      if (team && team.id != null) byId[String(team.id)] = team;
    });
    teamPickerState.teams = Object.keys(byId)
      .map(function (id) {
        return byId[id];
      })
      .sort(function (a, b) {
        return Number(a.id) - Number(b.id);
      });
    (selectedIds || []).forEach(function (id) {
      teamPickerState.selected[String(id)] = true;
    });
  }

  async function createTeamFromPicker() {
    var ov = document.getElementById("team-picker-overlay");
    var input = ov
      ? ov.querySelector("#team-picker-new-name")
      : document.getElementById("team-picker-new-name");
    var createBtn = ov
      ? ov.querySelector("#team-picker-create")
      : document.getElementById("team-picker-create");
    var name = String((input && input.value) || "").trim();
    if (!name) {
      setTeamPickerError("Введите название команды");
      if (input) input.focus();
      return;
    }
    if (createBtn && createBtn.disabled) return;
    setTeamPickerError("");
    var prevLabel = createBtn ? createBtn.textContent : "";
    if (createBtn) {
      createBtn.disabled = true;
      createBtn.textContent = "…";
    }
    // Invalidate in-flight list loads so they cannot wipe a just-created team.
    teamPickerState.loadGen += 1;
    try {
      var data = await apiFetch("/teams", {
        method: "POST",
        body: JSON.stringify({ name: name }),
      });
      var team = data && data.team;
      if (!team || team.id == null) {
        setTeamPickerError("Не удалось создать команду");
        return;
      }
      mergeTeamsIntoPicker([team], [team.id]);
      if (input) input.value = "";
      paintTeamPickerList();
    } catch (err) {
      setTeamPickerError(err.message || String(err));
    } finally {
      if (createBtn) {
        createBtn.disabled = false;
        createBtn.textContent = prevLabel || "Создать";
      }
    }
  }

  async function saveTeamPicker() {
    var ids = selectedTeamIdsFromPicker();
    try {
      if (teamPickerState.mode === "contact" && teamPickerState.email) {
        await apiFetch(
          "/contacts/" + encodeURIComponent(teamPickerState.email) + "/teams",
          { method: "PUT", body: JSON.stringify({ team_ids: ids }) }
        );
      } else if (teamPickerState.mode === "note" && teamPickerState.noteId) {
        await apiFetch(
          "/notes/local/" +
            encodeURIComponent(teamPickerState.noteId) +
            "/team-shares",
          { method: "PUT", body: JSON.stringify({ team_ids: ids }) }
        );
      }
      closeTeamPicker();
      if (typeof shareHaptic === "function") shareHaptic();
    } catch (err) {
      setTeamPickerError(err.message || String(err));
    }
  }

  async function openContactTeamsSheet(contact) {
    if (!contact || !contact.email) return;
    var ov = ensureTeamPickerOverlay();
    var title = ov.querySelector("#team-picker-title");
    if (title) title.textContent = "Участник команды";
    teamPickerState.mode = "contact";
    teamPickerState.email = String(contact.email);
    teamPickerState.noteId = "";
    teamPickerState.hasTelegram = contactHasTelegram(contact);
    teamPickerState.selected = {};
    teamPickerState.teams = [];
    teamPickerState.loadGen += 1;
    var loadGen = teamPickerState.loadGen;
    applyTeamPickerHint();
    setTeamPickerError("");
    ov.classList.remove("hidden");
    paintTeamPickerList();
    var nameInput = ov.querySelector("#team-picker-new-name");
    if (nameInput) {
      nameInput.value = "";
      setTimeout(function () {
        try {
          nameInput.focus();
        } catch (_) {}
      }, 50);
    }
    try {
      var data = await apiFetch(
        "/contacts/" + encodeURIComponent(contact.email) + "/teams",
        { method: "GET" }
      );
      if (
        loadGen !== teamPickerState.loadGen ||
        teamPickerState.mode !== "contact" ||
        teamPickerState.email !== String(contact.email)
      ) {
        return;
      }
      mergeTeamsIntoPicker(data && data.teams, data && data.team_ids);
      paintTeamPickerList();
    } catch (err) {
      if (loadGen !== teamPickerState.loadGen) return;
      setTeamPickerError(err.message || String(err));
    }
  }

  async function openNoteTeamShareSheet(note) {
    var wrap = getShareCtx();
    var noteId =
      (note && String(localNoteIdFrom(note) || note.id || "")) ||
      (wrap && wrap._shareId ? String(wrap._shareId) : "");
    if (!noteId) return;
    var ov = ensureTeamPickerOverlay();
    var title = ov.querySelector("#team-picker-title");
    if (title) title.textContent = "Поделиться с командой";
    teamPickerState.mode = "note";
    teamPickerState.email = "";
    teamPickerState.noteId = noteId;
    teamPickerState.hasTelegram = true;
    teamPickerState.selected = {};
    teamPickerState.teams = [];
    teamPickerState.loadGen += 1;
    var loadGen = teamPickerState.loadGen;
    applyTeamPickerHint();
    setTeamPickerError("");
    ov.classList.remove("hidden");
    paintTeamPickerList();
    var nameInput = ov.querySelector("#team-picker-new-name");
    if (nameInput) nameInput.value = "";
    try {
      var data = await apiFetch(
        "/notes/local/" + encodeURIComponent(noteId) + "/team-shares",
        { method: "GET" }
      );
      if (
        loadGen !== teamPickerState.loadGen ||
        teamPickerState.mode !== "note" ||
        teamPickerState.noteId !== noteId
      ) {
        return;
      }
      var selectedIds = ((data && data.teams) || [])
        .map(function (team) {
          return team && team.id;
        })
        .filter(Boolean);
      mergeTeamsIntoPicker(data && data.all_teams, selectedIds);
      paintTeamPickerList();
    } catch (err) {
      if (loadGen !== teamPickerState.loadGen) return;
      setTeamPickerError(err.message || String(err));
    }
  }

  function closeContactsCreateMenu() {
    var menu = document.getElementById("contacts-create-menu");
    var btn = document.getElementById("contacts-create-btn");
    if (menu) menu.classList.add("hidden");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleContactsCreateMenu() {
    var menu = document.getElementById("contacts-create-menu");
    var btn = document.getElementById("contacts-create-btn");
    if (!menu || !btn) return;
    var open = menu.classList.contains("hidden");
    menu.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function syncContactsCreateFab() {
    var createBtn = document.getElementById("contacts-create-btn");
    if (!createBtn) return;
    var contacts = document.getElementById("profile-contacts");
    var profilePanel = document.getElementById("panel-profile");
    var show =
      currentTab === "profile" &&
      currentProfileScreen === "contacts" &&
      contacts &&
      !contacts.classList.contains("hidden") &&
      profilePanel &&
      !profilePanel.classList.contains("hidden");
    setHidden(createBtn, !show);
    if (!show) closeContactsCreateMenu();
  }

  function setTeamsManageError(text) {
    var el = document.getElementById("teams-manage-err");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  var teamsManageState = {
    editingId: null,
    team: null,
    contacts: [],
    kbNotes: [],
  };

  var TEAMS_ICON_CLOSE =
    '<path d="M18 6 6 18M6 6l12 12" />';
  var TEAMS_ICON_BACK =
    '<path d="M15 18l-6-6 6-6" />';
  var TEAMS_ICON_EDIT =
    '<path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />';
  var TEAMS_ICON_TRASH =
    '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14zM10 11v6M14 11v6" />';

  function teamsManageIconBtn(opts) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "btn-icon-tile" + (opts.neutral ? " btn-icon-tile--neutral" : "");
    btn.setAttribute("aria-label", opts.label || "");
    btn.title = opts.label || "";
    btn.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
      (opts.paths || "") +
      "</svg>";
    if (opts.onClick) btn.addEventListener("click", opts.onClick);
    return btn;
  }

  function syncTeamsManageHeader() {
    var title = document.getElementById("teams-manage-title");
    var closeBtn = document.getElementById("teams-manage-close");
    var closeIcon = document.getElementById("teams-manage-close-icon");
    var saveBtn = document.getElementById("teams-manage-save");
    var spacer = document.getElementById("teams-manage-header-spacer");
    var editing = !!teamsManageState.editingId;
    if (title) {
      title.textContent = editing
        ? (teamsManageState.team && teamsManageState.team.name) || "Команда"
        : "Команды";
    }
    if (closeBtn) {
      closeBtn.setAttribute("aria-label", editing ? "Назад" : "Закрыть");
      closeBtn.title = editing ? "Назад" : "Закрыть";
    }
    if (closeIcon) {
      closeIcon.innerHTML = editing ? TEAMS_ICON_BACK : TEAMS_ICON_CLOSE;
    }
    if (saveBtn) saveBtn.classList.toggle("hidden", !editing);
    if (spacer) spacer.classList.toggle("hidden", editing);
  }

  function showTeamsManageListView() {
    teamsManageState.editingId = null;
    teamsManageState.team = null;
    var listView = document.getElementById("teams-manage-list-view");
    var editView = document.getElementById("teams-manage-edit-view");
    if (listView) listView.classList.remove("hidden");
    if (editView) editView.classList.add("hidden");
    syncTeamsManageHeader();
    setTeamsManageError("");
  }

  function memberLabel(m) {
    var label = (m && (m.name || m.email)) || "Участник";
    if (m && m.telegram_username) label += " · @" + m.telegram_username;
    else if (m && m.email && m.name) label += " · " + m.email;
    return label;
  }

  function paintTeamsManageList(teams) {
    var list = document.getElementById("teams-manage-list");
    var empty = document.getElementById("teams-manage-empty");
    if (!list) return;
    list.innerHTML = "";
    var rows = teams || [];
    if (empty) empty.classList.toggle("hidden", !!rows.length);
    rows.forEach(function (team) {
      var card = document.createElement("div");
      card.className = "teams-manage-card";
      var head = document.createElement("div");
      head.className = "teams-manage-card-head";
      var title = document.createElement("p");
      title.className = "teams-manage-card-title";
      title.textContent = team.name || "Команда";
      head.appendChild(title);
      var actions = document.createElement("div");
      actions.className = "teams-manage-card-actions";
      actions.appendChild(
        teamsManageIconBtn({
          label: "Редактировать",
          neutral: true,
          paths: TEAMS_ICON_EDIT,
          onClick: function () {
            openTeamsManageEdit(team);
          },
        })
      );
      actions.appendChild(
        teamsManageIconBtn({
          label: "Удалить",
          paths: TEAMS_ICON_TRASH,
          onClick: async function () {
            if (!confirm("Удалить команду «" + (team.name || "") + "»?")) return;
            try {
              await apiFetch("/teams/" + encodeURIComponent(String(team.id)), {
                method: "DELETE",
              });
              await loadTeamsManageList();
              await loadContacts();
            } catch (err) {
              setTeamsManageError(err.message || String(err));
            }
          },
        })
      );
      head.appendChild(actions);
      card.appendChild(head);

      var membersLabel = document.createElement("p");
      membersLabel.className = "field-label";
      membersLabel.textContent = "Участники";
      card.appendChild(membersLabel);
      var members = team.members || [];
      if (!members.length) {
        var noMembers = document.createElement("p");
        noMembers.className = "muted small";
        noMembers.textContent = "Пока никого нет";
        card.appendChild(noMembers);
      } else {
        var membersList = document.createElement("ul");
        membersList.className = "teams-manage-meta-list";
        members.forEach(function (m) {
          var li = document.createElement("li");
          li.textContent = memberLabel(m);
          membersList.appendChild(li);
        });
        card.appendChild(membersList);
      }

      var sharesLabel = document.createElement("p");
      sharesLabel.className = "field-label";
      sharesLabel.textContent = "Документы базы знаний";
      card.appendChild(sharesLabel);
      var shares = team.shares || [];
      if (!shares.length) {
        var noShares = document.createElement("p");
        noShares.className = "muted small";
        noShares.textContent = "Нет расшаренных документов.";
        card.appendChild(noShares);
      } else {
        var sharesList = document.createElement("ul");
        sharesList.className = "teams-manage-meta-list";
        shares.forEach(function (s) {
          var li = document.createElement("li");
          li.textContent = s.title || "Документ";
          sharesList.appendChild(li);
        });
        card.appendChild(sharesList);
      }
      list.appendChild(card);
    });
  }

  function fillTeamsManageAddMemberSelect() {
    var sel = document.getElementById("teams-manage-add-member");
    if (!sel) return;
    var team = teamsManageState.team || {};
    var memberEmails = {};
    (team.members || []).forEach(function (m) {
      if (m && m.email) memberEmails[String(m.email).toLowerCase()] = true;
    });
    sel.innerHTML = '<option value="">Добавить контакт…</option>';
    (teamsManageState.contacts || []).forEach(function (c) {
      var email = String((c && c.email) || "").toLowerCase();
      if (!email || memberEmails[email]) return;
      var opt = document.createElement("option");
      opt.value = email;
      opt.textContent = (c.name || email) + (c.name ? " · " + email : "");
      sel.appendChild(opt);
    });
  }

  function fillTeamsManageAddShareSelect() {
    var sel = document.getElementById("teams-manage-add-share");
    if (!sel) return;
    var team = teamsManageState.team || {};
    var sharedIds = {};
    (team.shares || []).forEach(function (s) {
      if (s && s.note_id != null) sharedIds[String(s.note_id)] = true;
    });
    sel.innerHTML = '<option value="">Добавить документ…</option>';
    (teamsManageState.kbNotes || []).forEach(function (n) {
      var id = n && (n.id != null ? n.id : n.note_id);
      if (id == null || sharedIds[String(id)]) return;
      // Only own knowledge docs (not shared-into) for sharing out
      if (n.is_owner === false) return;
      var opt = document.createElement("option");
      opt.value = String(id);
      opt.textContent = n.title || "Документ";
      sel.appendChild(opt);
    });
  }

  function paintTeamsManageEdit() {
    var team = teamsManageState.team;
    if (!team) return;
    var nameInput = document.getElementById("teams-manage-edit-name");
    if (nameInput && document.activeElement !== nameInput) {
      nameInput.value = team.name || "";
    }
    var membersEl = document.getElementById("teams-manage-edit-members");
    if (membersEl) {
      membersEl.innerHTML = "";
      var members = team.members || [];
      if (!members.length) {
        var emptyM = document.createElement("p");
        emptyM.className = "muted small";
        emptyM.textContent = "Пока никого нет";
        membersEl.appendChild(emptyM);
      } else {
        members.forEach(function (m) {
          var row = document.createElement("div");
          row.className = "teams-manage-edit-row";
          var label = document.createElement("span");
          label.className = "teams-manage-edit-row-label";
          label.textContent = memberLabel(m);
          row.appendChild(label);
          row.appendChild(
            teamsManageIconBtn({
              label: "Удалить участника",
              paths: TEAMS_ICON_TRASH,
              onClick: function () {
                removeTeamMemberFromEdit(m.email);
              },
            })
          );
          membersEl.appendChild(row);
        });
      }
    }
    var sharesEl = document.getElementById("teams-manage-edit-shares");
    if (sharesEl) {
      sharesEl.innerHTML = "";
      var shares = team.shares || [];
      if (!shares.length) {
        var emptyS = document.createElement("p");
        emptyS.className = "muted small";
        emptyS.textContent = "Нет расшаренных документов.";
        sharesEl.appendChild(emptyS);
      } else {
        shares.forEach(function (s) {
          var row = document.createElement("div");
          row.className = "teams-manage-edit-row";
          var label = document.createElement("span");
          label.className = "teams-manage-edit-row-label";
          label.textContent = s.title || "Документ";
          row.appendChild(label);
          row.appendChild(
            teamsManageIconBtn({
              label: "Убрать документ",
              paths: TEAMS_ICON_TRASH,
              onClick: function () {
                removeTeamShareFromEdit(s.note_id);
              },
            })
          );
          sharesEl.appendChild(row);
        });
      }
    }
    fillTeamsManageAddMemberSelect();
    fillTeamsManageAddShareSelect();
    syncTeamsManageHeader();
  }

  async function refreshTeamsManageEditTeam() {
    var tid = teamsManageState.editingId;
    if (!tid) return null;
    var data = await apiFetch("/teams?detailed=1", { method: "GET" });
    var teams = (data && data.teams) || [];
    var found = null;
    for (var i = 0; i < teams.length; i++) {
      if (String(teams[i].id) === String(tid)) {
        found = teams[i];
        break;
      }
    }
    teamsManageState.team = found;
    return found;
  }

  async function openTeamsManageEdit(team) {
    if (!team || team.id == null) return;
    setTeamsManageError("");
    teamsManageState.editingId = team.id;
    teamsManageState.team = team;
    var listView = document.getElementById("teams-manage-list-view");
    var editView = document.getElementById("teams-manage-edit-view");
    if (listView) listView.classList.add("hidden");
    if (editView) editView.classList.remove("hidden");
    paintTeamsManageEdit();
    try {
      var contactsData = await apiFetch("/contacts", { method: "GET" });
      teamsManageState.contacts = (contactsData && contactsData.items) || [];
    } catch (_) {
      teamsManageState.contacts = [];
    }
    try {
      if (!knowledgeDataCache) {
        var kbData = await apiFetch("/notes/knowledge", { method: "GET" });
        knowledgeDataCache = { notes: (kbData && kbData.notes) || [] };
      }
      teamsManageState.kbNotes = knowledgeNotesList().filter(function (n) {
        return n && n.is_owner !== false;
      });
    } catch (_) {
      teamsManageState.kbNotes = knowledgeNotesList().slice();
    }
    try {
      var fresh = await refreshTeamsManageEditTeam();
      if (!fresh) {
        setTeamsManageError("Команда не найдена");
        showTeamsManageListView();
        await loadTeamsManageList();
        return;
      }
      paintTeamsManageEdit();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  async function saveTeamsManageEditName() {
    var tid = teamsManageState.editingId;
    var input = document.getElementById("teams-manage-edit-name");
    if (!tid || !input) return;
    var name = String(input.value || "").trim();
    if (!name) {
      setTeamsManageError("Введите название команды");
      input.focus();
      return;
    }
    setTeamsManageError("");
    try {
      var data = await apiFetch("/teams/" + encodeURIComponent(String(tid)), {
        method: "PATCH",
        body: JSON.stringify({ name: name }),
      });
      if (data && data.team) {
        teamsManageState.team = Object.assign({}, teamsManageState.team || {}, data.team);
      } else if (teamsManageState.team) {
        teamsManageState.team.name = name;
      }
      syncTeamsManageHeader();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  async function setContactTeamsForEmail(email, teamIds) {
    await apiFetch("/contacts/" + encodeURIComponent(email) + "/teams", {
      method: "PUT",
      body: JSON.stringify({ team_ids: teamIds }),
    });
  }

  async function setNoteTeamShares(noteId, teamIds) {
    await apiFetch(
      "/notes/local/" + encodeURIComponent(String(noteId)) + "/team-shares",
      {
        method: "PUT",
        body: JSON.stringify({ team_ids: teamIds }),
      }
    );
  }

  async function removeTeamMemberFromEdit(email) {
    var tid = teamsManageState.editingId;
    var key = String(email || "").trim().toLowerCase();
    if (!tid || !key) return;
    setTeamsManageError("");
    try {
      var data = await apiFetch("/contacts/" + encodeURIComponent(key) + "/teams", {
        method: "GET",
      });
      var ids = ((data && data.team_ids) || [])
        .map(function (id) {
          return Number(id);
        })
        .filter(function (id) {
          return id && id !== Number(tid);
        });
      await setContactTeamsForEmail(key, ids);
      await refreshTeamsManageEditTeam();
      paintTeamsManageEdit();
      await loadContacts();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  async function addTeamMemberFromEdit() {
    var tid = teamsManageState.editingId;
    var sel = document.getElementById("teams-manage-add-member");
    var email = sel ? String(sel.value || "").trim().toLowerCase() : "";
    if (!tid || !email) {
      setTeamsManageError("Выберите контакт");
      return;
    }
    setTeamsManageError("");
    try {
      var data = await apiFetch("/contacts/" + encodeURIComponent(email) + "/teams", {
        method: "GET",
      });
      var ids = ((data && data.team_ids) || [])
        .map(function (id) {
          return Number(id);
        })
        .filter(Boolean);
      if (ids.indexOf(Number(tid)) < 0) ids.push(Number(tid));
      await setContactTeamsForEmail(email, ids);
      if (sel) sel.value = "";
      await refreshTeamsManageEditTeam();
      paintTeamsManageEdit();
      await loadContacts();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  async function removeTeamShareFromEdit(noteId) {
    var tid = teamsManageState.editingId;
    if (!tid || noteId == null) return;
    setTeamsManageError("");
    try {
      var data = await apiFetch(
        "/notes/local/" + encodeURIComponent(String(noteId)) + "/team-shares",
        { method: "GET" }
      );
      var ids = ((data && data.teams) || [])
        .map(function (t) {
          return t && t.id != null ? Number(t.id) : 0;
        })
        .filter(function (id) {
          return id && id !== Number(tid);
        });
      await setNoteTeamShares(noteId, ids);
      await refreshTeamsManageEditTeam();
      paintTeamsManageEdit();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  async function addTeamShareFromEdit() {
    var tid = teamsManageState.editingId;
    var sel = document.getElementById("teams-manage-add-share");
    var noteId = sel ? String(sel.value || "").trim() : "";
    if (!tid || !noteId) {
      setTeamsManageError("Выберите документ");
      return;
    }
    setTeamsManageError("");
    try {
      var data = await apiFetch(
        "/notes/local/" + encodeURIComponent(noteId) + "/team-shares",
        { method: "GET" }
      );
      var ids = ((data && data.teams) || [])
        .map(function (t) {
          return t && t.id != null ? Number(t.id) : 0;
        })
        .filter(Boolean);
      if (ids.indexOf(Number(tid)) < 0) ids.push(Number(tid));
      await setNoteTeamShares(noteId, ids);
      if (sel) sel.value = "";
      await refreshTeamsManageEditTeam();
      paintTeamsManageEdit();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  async function loadTeamsManageList() {
    setTeamsManageError("");
    try {
      var data = await apiFetch("/teams?detailed=1", { method: "GET" });
      paintTeamsManageList((data && data.teams) || []);
    } catch (err) {
      paintTeamsManageList([]);
      setTeamsManageError(err.message || String(err));
    }
  }

  function openTeamsManageModal() {
    var ov = document.getElementById("teams-manage-overlay");
    if (!ov) return;
    showTeamsManageListView();
    ov.classList.remove("hidden");
    ov.setAttribute("aria-hidden", "false");
    syncAppOverlay();
    loadTeamsManageList();
  }

  function closeTeamsManageModal() {
    var ov = document.getElementById("teams-manage-overlay");
    if (ov) {
      ov.classList.add("hidden");
      ov.setAttribute("aria-hidden", "true");
    }
    showTeamsManageListView();
    setTeamsManageError("");
    syncAppOverlay();
  }

  function teamsManageCloseOrBack() {
    if (teamsManageState.editingId) {
      showTeamsManageListView();
      loadTeamsManageList();
      return;
    }
    closeTeamsManageModal();
  }

  async function createTeamFromManage() {
    var input = document.getElementById("teams-manage-new-input");
    var name = String((input && input.value) || "").trim();
    if (!name) {
      setTeamsManageError("Введите название команды");
      if (input) input.focus();
      return;
    }
    setTeamsManageError("");
    try {
      await apiFetch("/teams", {
        method: "POST",
        body: JSON.stringify({ name: name }),
      });
      if (input) input.value = "";
      await loadTeamsManageList();
    } catch (err) {
      setTeamsManageError(err.message || String(err));
    }
  }

  var calendarsCache = [];
  var calendarsSaveTimer = null;
  var calendarsSaveRevision = 0;
  var calendarsFlushInFlight = false;
  var calendarsFlushPromise = null;
  var calendarsTouchedIds = {};
  var calendarsSaveDoneTimer = null;
  var meetingRemindersEnabled = true;
  var meetingRemindersSaveInFlight = false;
  var taskRemindersEnabled = true;
  var taskRemindersSaveInFlight = false;
  var zoomAutoRecordEnabled = false;
  var zoomAutoRecordSaveInFlight = false;
  var zoomAutoRecordAvailable = false;

  function applyZoomAutoRecordToggleUi(enabled, available) {
    var row = document.getElementById("zoom-auto-record-setting");
    var toggle = document.getElementById("zoom-auto-record-toggle");
    if (!row || !toggle) return;
    zoomAutoRecordAvailable = available !== false;
    zoomAutoRecordEnabled = !!enabled;
    setHidden(row, !zoomAutoRecordAvailable);
    toggle.classList.toggle("svc-toggle--on", zoomAutoRecordEnabled);
    toggle.classList.toggle("svc-toggle--pending", zoomAutoRecordSaveInFlight);
    toggle.setAttribute("aria-pressed", zoomAutoRecordEnabled ? "true" : "false");
    toggle.setAttribute("aria-checked", zoomAutoRecordEnabled ? "true" : "false");
  }

  async function saveZoomAutoRecordEnabled(enabled) {
    var errEl = document.getElementById("zoom-auto-record-err");
    var toggle = document.getElementById("zoom-auto-record-toggle");
    zoomAutoRecordSaveInFlight = true;
    if (toggle) toggle.classList.add("svc-toggle--pending");
    try {
      var data = await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify({ zoom_auto_record_enabled: !!enabled }),
      });
      applyZoomAutoRecordToggleUi(
        !!(data && data.zoom_auto_record_enabled),
        !!(data && data.meeting_bot_available)
      );
      if (errEl) setHidden(errEl, true);
    } catch (e) {
      applyZoomAutoRecordToggleUi(!enabled, zoomAutoRecordAvailable);
      if (errEl) {
        errEl.textContent = e.message || String(e);
        setHidden(errEl, false);
      }
    } finally {
      zoomAutoRecordSaveInFlight = false;
      if (toggle) toggle.classList.remove("svc-toggle--pending");
    }
  }

  function initZoomAutoRecordToggle() {
    var toggle = document.getElementById("zoom-auto-record-toggle");
    if (!toggle || toggle.getAttribute("data-bound") === "1") return;
    toggle.setAttribute("data-bound", "1");
    toggle.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (zoomAutoRecordSaveInFlight) return;
      var next = !zoomAutoRecordEnabled;
      applyZoomAutoRecordToggleUi(next, zoomAutoRecordAvailable);
      saveZoomAutoRecordEnabled(next);
    });
  }

  function applyMeetingRemindersToggleUi(enabled) {
    var row = document.getElementById("meeting-reminders-setting");
    var toggle = document.getElementById("meeting-reminders-toggle");
    if (!row || !toggle) return;
    meetingRemindersEnabled = !!enabled;
    setHidden(row, false);
    toggle.classList.toggle("svc-toggle--on", meetingRemindersEnabled);
    toggle.classList.toggle("svc-toggle--pending", meetingRemindersSaveInFlight);
    toggle.setAttribute("aria-pressed", meetingRemindersEnabled ? "true" : "false");
  }

  async function saveMeetingRemindersEnabled(enabled) {
    var errEl = document.getElementById("meeting-reminders-setting-err");
    var toggle = document.getElementById("meeting-reminders-toggle");
    meetingRemindersSaveInFlight = true;
    if (toggle) toggle.classList.add("svc-toggle--pending");
    try {
      var data = await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify({ meeting_reminders_enabled: !!enabled }),
      });
      applyMeetingRemindersToggleUi(!!(data && data.meeting_reminders_enabled));
      if (errEl) setHidden(errEl, true);
    } catch (e) {
      applyMeetingRemindersToggleUi(!enabled);
      if (errEl) {
        errEl.textContent = e.message || String(e);
        setHidden(errEl, false);
      }
    } finally {
      meetingRemindersSaveInFlight = false;
      if (toggle) toggle.classList.remove("svc-toggle--pending");
    }
  }

  function initMeetingRemindersToggle() {
    var toggle = document.getElementById("meeting-reminders-toggle");
    if (!toggle || toggle.getAttribute("data-bound") === "1") return;
    toggle.setAttribute("data-bound", "1");
    toggle.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (meetingRemindersSaveInFlight) return;
      var next = !meetingRemindersEnabled;
      applyMeetingRemindersToggleUi(next);
      saveMeetingRemindersEnabled(next);
    });
  }

  function applyTaskRemindersToggleUi(enabled) {
    var row = document.getElementById("task-reminders-setting");
    var toggle = document.getElementById("task-reminders-toggle");
    if (!row || !toggle) return;
    taskRemindersEnabled = !!enabled;
    setHidden(row, false);
    toggle.classList.toggle("svc-toggle--on", taskRemindersEnabled);
    toggle.classList.toggle("svc-toggle--pending", taskRemindersSaveInFlight);
    toggle.setAttribute("aria-pressed", taskRemindersEnabled ? "true" : "false");
  }

  async function saveTaskRemindersEnabled(enabled) {
    var errEl = document.getElementById("task-reminders-setting-err");
    var toggle = document.getElementById("task-reminders-toggle");
    taskRemindersSaveInFlight = true;
    if (toggle) toggle.classList.add("svc-toggle--pending");
    try {
      var data = await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify({ task_reminders_enabled: !!enabled }),
      });
      applyTaskRemindersToggleUi(!!(data && data.task_reminders_enabled));
      if (errEl) setHidden(errEl, true);
    } catch (e) {
      applyTaskRemindersToggleUi(!enabled);
      if (errEl) {
        errEl.textContent = e.message || String(e);
        setHidden(errEl, false);
      }
    } finally {
      taskRemindersSaveInFlight = false;
      if (toggle) toggle.classList.remove("svc-toggle--pending");
    }
  }

  function initTaskRemindersToggle() {
    var toggle = document.getElementById("task-reminders-toggle");
    if (!toggle || toggle.getAttribute("data-bound") === "1") return;
    toggle.setAttribute("data-bound", "1");
    toggle.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (taskRemindersSaveInFlight) return;
      var next = !taskRemindersEnabled;
      applyTaskRemindersToggleUi(next);
      saveTaskRemindersEnabled(next);
    });
  }

  function findCalendarInCache(calId) {
    var id = (calId || "").trim();
    if (!id) return null;
    for (var i = 0; i < calendarsCache.length; i += 1) {
      if (calendarsCache[i].id === id) return calendarsCache[i];
    }
    return null;
  }

  function setCalendarsSaveStatus(phase, message) {
    var el = document.getElementById("calendars-save-status");
    if (!el) return;
    el.classList.remove("calendars-save-status--done");
    if (phase === "idle") {
      setHidden(el, true);
      el.textContent = "";
      return;
    }
    setHidden(el, false);
    el.textContent = message || "";
    if (phase === "done") el.classList.add("calendars-save-status--done");
  }

  function updateCalendarsPendingUi() {
    var saving = calendarsFlushInFlight;
    var waiting = !!calendarsSaveTimer;
    document.querySelectorAll(".calendars-row").forEach(function (row) {
      var id = row.getAttribute("data-cal-id") || "";
      var pending = !!calendarsTouchedIds[id] && (waiting || saving);
      row.classList.toggle("calendars-row--pending", pending);
      var toggle = row.querySelector(".svc-toggle");
      if (toggle) toggle.classList.toggle("svc-toggle--pending", pending);
    });
    if (saving) {
      setCalendarsSaveStatus("saving", "Сохранение…");
    } else if (waiting && Object.keys(calendarsTouchedIds).length) {
      setCalendarsSaveStatus("waiting", "Применяем изменения…");
    }
  }

  function scheduleCalendarsSave() {
    if (calendarsSaveTimer) clearTimeout(calendarsSaveTimer);
    if (calendarsSaveDoneTimer) {
      clearTimeout(calendarsSaveDoneTimer);
      calendarsSaveDoneTimer = null;
    }
    updateCalendarsPendingUi();
    calendarsSaveTimer = setTimeout(function () {
      calendarsSaveTimer = null;
      flushCalendarsExcluded();
    }, 350);
  }

  function paintCalendarsList() {
    var list = document.getElementById("calendars-list");
    var empty = document.getElementById("calendars-empty");
    if (!list) return;
    list.innerHTML = "";
    if (!calendarsCache.length) {
      setHidden(empty, false);
      return;
    }
    setHidden(empty, true);
    calendarsCache.forEach(function (cal) {
      list.appendChild(renderCalendarRow(cal));
    });
  }

  function renderCalendarRow(cal) {
    var calId = cal.id || "";
    var card = document.createElement("div");
    card.className = "svc-row calendars-row";
    card.setAttribute("data-cal-id", calId);
    var dot = document.createElement("span");
    dot.className = "calendars-dot";
    dot.style.background = cal.backgroundColor || "var(--accent)";
    var text = document.createElement("div");
    text.className = "svc-text";
    var name = document.createElement("p");
    name.className = "svc-name";
    name.textContent = cal.summary || calId || "Календарь";
    var sub = document.createElement("p");
    sub.className = "svc-desc muted small";
    sub.textContent = cal.primary
      ? "Основной"
      : cal.accessRole === "reader"
        ? "Только просмотр"
        : "Подключённый";
    text.appendChild(name);
    text.appendChild(sub);
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "svc-toggle";
    toggle.setAttribute("aria-pressed", cal.excluded ? "false" : "true");
    toggle.setAttribute("aria-label", "Учитывать календарь");
    if (!cal.excluded) toggle.classList.add("svc-toggle--on");
    var knob = document.createElement("span");
    knob.className = "svc-toggle-knob";
    toggle.appendChild(knob);
    toggle.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (toggle.classList.contains("svc-toggle--pending")) return;
      var item = findCalendarInCache(calId);
      if (!item) return;
      var nextExcluded = !item.excluded;
      item.excluded = nextExcluded;
      toggle.classList.toggle("svc-toggle--on", !nextExcluded);
      toggle.setAttribute("aria-pressed", !nextExcluded ? "true" : "false");
      calendarsTouchedIds[calId] = true;
      calendarsSaveRevision += 1;
      scheduleCalendarsSave();
    });
    card.appendChild(dot);
    card.appendChild(text);
    card.appendChild(toggle);
    return card;
  }

  function calendarsExcludedIdsFromCache() {
    return calendarsCache
      .filter(function (c) {
        return c.excluded;
      })
      .map(function (c) {
        return c.id;
      });
  }

  function applyCalendarsExcludedToCache(excludedIds) {
    var excludedSet = {};
    (excludedIds || []).forEach(function (id) {
      if (id) excludedSet[id] = true;
    });
    calendarsCache.forEach(function (cal) {
      cal.excluded = !!excludedSet[cal.id];
    });
  }

  function syncCalendarsToggleUi() {
    document.querySelectorAll(".calendars-row").forEach(function (row) {
      var calId = row.getAttribute("data-cal-id") || "";
      var item = findCalendarInCache(calId);
      var toggle = row.querySelector(".svc-toggle");
      if (!item || !toggle) return;
      var on = !item.excluded;
      toggle.classList.toggle("svc-toggle--on", on);
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  async function flushCalendarsExcluded() {
    if (calendarsFlushPromise) {
      return calendarsFlushPromise;
    }
    var errEl = document.getElementById("calendars-err");
    var revisionAtStart = calendarsSaveRevision;
    var excluded = calendarsExcludedIdsFromCache();
    calendarsFlushInFlight = true;
    updateCalendarsPendingUi();
    calendarsFlushPromise = (async function () {
      try {
        var data = await apiFetch("/calendar/sources/excluded", {
          method: "PUT",
          body: JSON.stringify({ excluded_ids: excluded }),
        });
        if (revisionAtStart !== calendarsSaveRevision) {
          scheduleCalendarsSave();
          return;
        }
        calendarsTouchedIds = {};
        applyCalendarsExcludedToCache(excluded);
        if (data && data.calendars && data.calendars.length) {
          var serverById = {};
          data.calendars.forEach(function (c) {
            if (c && c.id) serverById[c.id] = c;
          });
          calendarsCache.forEach(function (cal) {
            if (serverById[cal.id]) {
              cal.summary = serverById[cal.id].summary || cal.summary;
              cal.backgroundColor =
                serverById[cal.id].backgroundColor || cal.backgroundColor;
            }
          });
        }
        syncCalendarsToggleUi();
        if (errEl) setHidden(errEl, true);
        setCalendarsSaveStatus("done", "Сохранено");
        calendarsSaveDoneTimer = setTimeout(function () {
          calendarsSaveDoneTimer = null;
          if (!calendarsSaveTimer && !calendarsFlushInFlight) {
            setCalendarsSaveStatus("idle");
          }
        }, 1200);
      } catch (e) {
        if (revisionAtStart !== calendarsSaveRevision) {
          scheduleCalendarsSave();
          return;
        }
        if (errEl) {
          errEl.textContent = e.message || String(e);
          setHidden(errEl, false);
        }
        setCalendarsSaveStatus("idle");
      } finally {
        calendarsFlushInFlight = false;
        calendarsFlushPromise = null;
        updateCalendarsPendingUi();
        if (
          !calendarsSaveTimer &&
          !calendarsFlushInFlight &&
          !calendarsSaveDoneTimer
        ) {
          setCalendarsSaveStatus("idle");
        }
      }
    })();
    return calendarsFlushPromise;
  }

  async function ensureCalendarsSaved() {
    if (calendarsSaveTimer) {
      clearTimeout(calendarsSaveTimer);
      calendarsSaveTimer = null;
    }
    if (calendarsTouchedIds && Object.keys(calendarsTouchedIds).length) {
      await flushCalendarsExcluded();
    } else if (calendarsFlushPromise) {
      await calendarsFlushPromise;
    }
  }

  async function loadCalendarsSources() {
    var list = document.getElementById("calendars-list");
    var err = document.getElementById("calendars-err");
    if (!list) return;
    initMeetingRemindersToggle();
    initTaskRemindersToggle();
    await ensureCalendarsSaved();
    setHidden(err, true);
    list.innerHTML = "<p class=\"muted small\">Загрузка…</p>";
    try {
      var results = await Promise.all([
        apiFetch("/settings", { method: "GET" }),
        apiFetch("/calendar/sources", { method: "GET" }),
      ]);
      var settings = results[0] || {};
      var data = results[1] || {};
      var mins = parseInt(settings.meeting_reminder_minutes_before, 10);
      var hint = document.getElementById("meeting-reminders-setting-hint");
      if (hint && mins > 0) {
        hint.textContent = "За " + mins + " мин. до начала";
      }
      var taskMins = parseInt(settings.task_reminder_minutes_before, 10);
      var taskHint = document.getElementById("task-reminders-setting-hint");
      if (taskHint && taskMins > 0) {
        taskHint.textContent = "За " + taskMins + " мин. до начала";
      }
      applyMeetingRemindersToggleUi(settings.meeting_reminders_enabled !== false);
      applyTaskRemindersToggleUi(settings.task_reminders_enabled !== false);
      calendarsCache = data.calendars || [];
      paintCalendarsList();
    } catch (e) {
      list.innerHTML = "";
      if (err) {
        err.textContent = e.message || String(e);
        setHidden(err, false);
      }
    }
  }

  async function refreshProfileContactsCount() {
    var el = document.getElementById("profile-contacts-count");
    if (!el) return;
    try {
      var data = await apiFetch("/contacts", { method: "GET" });
      var n = (data.items || []).length;
      el.textContent = n ? String(n) + " контакт(ов)" : "Пусто";
    } catch (_) {
      el.textContent = "—";
    }
  }

  async function loadContacts() {
    var list = document.getElementById("contacts-list");
    var empty = document.getElementById("contacts-empty");
    var err = document.getElementById("contacts-err");
    if (!list) return;
    setHidden(err, true);
    list.innerHTML = "<p class=\"muted small\">Загрузка…</p>";
    try {
      var data = await apiFetch("/contacts", { method: "GET" });
      var items = data.items || [];
      list.innerHTML = "";
      if (!items.length) {
        setHidden(empty, false);
        return;
      }
      setHidden(empty, true);
      items.sort(function (a, b) {
        return (a.name || "").localeCompare(b.name || "", "ru");
      });
      items.forEach(function (c) {
        list.appendChild(renderContactCard(c));
      });
    } catch (e) {
      list.innerHTML = "";
      if (err) {
        err.textContent = e.message || String(e);
        setHidden(err, false);
      }
    }
  }

  async function contactsSave() {
    var name = (document.getElementById("contacts-f-name").value || "").trim();
    var email = (document.getElementById("contacts-f-email").value || "").trim();
    var tg = (document.getElementById("contacts-f-tg").value || "").trim().replace(/^@/, "");
    var aliases = contactsParseAliases(
      document.getElementById("contacts-f-aliases").value || ""
    );
    if (!name || !email) {
      contactsShowFormMsg("", "Укажите имя и email");
      return;
    }
    contactsShowFormMsg("", "");
    var teamIds = selectedContactFormTeamIds();
    try {
      var savedEmail = email.toLowerCase();
      if (contactsEditingEmail) {
        var updated = await apiFetch(
          "/contacts/" + encodeURIComponent(contactsEditingEmail),
          {
            method: "PUT",
            body: JSON.stringify({
              name: name,
              email: email,
              telegram_username: tg || "",
              aliases: aliases,
              clear_telegram: !tg,
            }),
          }
        );
        if (updated && updated.item && updated.item.email) {
          savedEmail = String(updated.item.email).toLowerCase();
        }
      } else {
        var created = await apiFetch("/contacts", {
          method: "POST",
          body: JSON.stringify({
            name: name,
            email: email,
            telegram_username: tg || null,
            aliases: aliases.length ? aliases : null,
          }),
        });
        if (created && created.item && created.item.email) {
          savedEmail = String(created.item.email).toLowerCase();
        }
      }
      await apiFetch("/contacts/" + encodeURIComponent(savedEmail) + "/teams", {
        method: "PUT",
        body: JSON.stringify({ team_ids: teamIds }),
      });
      closeContactsFormModal();
      await loadContacts();
      await refreshProfileContactsCount();
      var tgApp = window.Telegram && window.Telegram.WebApp;
      if (tgApp && tgApp.HapticFeedback) tgApp.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      contactsShowFormMsg("", e.message || String(e));
    }
  }

  async function submitYandexDiskCode(code) {
    var errEl = document.getElementById("profile-yandex-disk-err");
    var msgEl = document.getElementById("profile-yandex-disk-msg");
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    if (msgEl) {
      msgEl.textContent = "";
      msgEl.classList.add("hidden");
    }
    clearProfileError();
    try {
      await apiFetch("/oauth/yandex-disk/code", {
        method: "POST",
        body: JSON.stringify({ code: code }),
      });
      var codeInp = document.getElementById("profile-yandex-disk-code");
      if (codeInp) codeInp.value = "";
      if (msgEl) {
        msgEl.textContent = "Яндекс Диск подключён.";
        msgEl.classList.remove("hidden");
      }
      await loadIntegrations();
      await loadYandexDiskScreen(false);
      stopOAuthStatusPoll();
      var tgApp = window.Telegram && window.Telegram.WebApp;
      if (tgApp && tgApp.HapticFeedback) tgApp.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      var errText = "Яндекс Диск: " + (e.message || e);
      if (errEl) {
        errEl.textContent = errText;
        errEl.classList.remove("hidden");
      } else {
        showProfileError(errText);
      }
    }
  }

  async function openYandexDiskAuthLink() {
    var errEl = document.getElementById("profile-yandex-disk-err");
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    try {
      const data = await apiFetch("/oauth/yandex-disk/start", { method: "POST" });
      const url = data && data.url;
      if (!url) throw new Error("нет URL авторизации");
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.openLink) {
        tg.openLink(url);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
      var codeInp = document.getElementById("profile-yandex-disk-code");
      if (codeInp) codeInp.focus();
    } catch (e) {
      if (errEl) {
        errEl.textContent = e.message || String(e);
        errEl.classList.remove("hidden");
      }
    }
  }

  async function loadYandexDiskScreen(refreshIntegrations) {
    if (refreshIntegrations !== false) {
      await loadIntegrations();
    }
    var stEl = document.getElementById("profile-yandex-disk-status");
    var authBtn = document.getElementById("profile-yandex-disk-auth");
    var submitBtn = document.getElementById("profile-yandex-disk-submit");
    var discBtn = document.getElementById("profile-yandex-disk-disconnect");
    var codeWrap = document.getElementById("profile-yandex-disk-code");
    var msgEl = document.getElementById("profile-yandex-disk-msg");
    var errEl = document.getElementById("profile-yandex-disk-err");
    if (msgEl) {
      msgEl.textContent = "";
      msgEl.classList.add("hidden");
    }
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    var data;
    try {
      data = await apiFetch("/integrations", { method: "GET" });
    } catch (e) {
      if (stEl) stEl.textContent = "Не удалось проверить статус.";
      return;
    }
    var on = !!(data && (data["yandex-disk"] || data.yandex_disk_connected));
    var needsRefresh = !!(on && data && data.yandex_disk_needs_scope_refresh);
    if (stEl) {
      if (on) {
        if (needsRefresh) {
          stEl.textContent =
            "● подключён, но нужны права на чтение — нажмите «Отключить», затем снова «Подключить»";
        } else {
          var who =
            data.yandex_disk_display_name || data.yandex_disk_login || "аккаунт подключён";
          stEl.textContent = "● " + who;
        }
      } else {
        stEl.textContent = "Не подключён";
      }
    }
    if (authBtn) setHidden(authBtn, on && !needsRefresh);
    if (submitBtn) setHidden(submitBtn, on && !needsRefresh);
    if (codeWrap) {
      var codeField = codeWrap.closest(".field");
      if (codeField) setHidden(codeField, on && !needsRefresh);
    }
    if (discBtn) setHidden(discBtn, !on);
    if (msgEl && needsRefresh) {
      msgEl.textContent =
        "Диск подключён со старыми правами: Leo может сохранять файлы, но не получает ссылку на папку. Отключите и подключите снова.";
      msgEl.classList.remove("hidden");
    }
  }

  function openYandexDiskSetup() {
    profileScreen("yandex-disk");
    loadYandexDiskScreen(true);
  }

  async function loadBitrixScreen(refreshIntegrations) {
    if (refreshIntegrations !== false) {
      await loadIntegrations();
    }
    var stEl = document.getElementById("profile-bitrix-status");
    var submitBtn = document.getElementById("profile-bitrix-submit");
    var discBtn = document.getElementById("profile-bitrix-disconnect");
    var tokenInp = document.getElementById("profile-bitrix-token");
    var msgEl = document.getElementById("profile-bitrix-msg");
    var errEl = document.getElementById("profile-bitrix-err");
    if (msgEl) {
      msgEl.textContent = "";
      msgEl.classList.add("hidden");
    }
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    var data;
    try {
      data = await apiFetch("/integrations", { method: "GET" });
    } catch (e) {
      if (stEl) stEl.textContent = "Не удалось проверить статус.";
      return;
    }
    var on = !!(data && (data.bitrix || data.bitrix_connected));
    if (stEl) {
      stEl.textContent = on ? "● Битрикс24 подключён" : "Не подключён";
    }
    if (submitBtn) setHidden(submitBtn, on);
    if (tokenInp) {
      var tokenField = tokenInp.closest(".field");
      if (tokenField) setHidden(tokenField, on);
    }
    if (discBtn) setHidden(discBtn, !on);
  }

  function openBitrixSetup() {
    profileScreen("bitrix");
    loadBitrixScreen(true);
  }

  async function connectBitrix(token) {
    var errEl = document.getElementById("profile-bitrix-err");
    var msgEl = document.getElementById("profile-bitrix-msg");
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    if (msgEl) {
      msgEl.textContent = "";
      msgEl.classList.add("hidden");
    }
    clearProfileError();
    try {
      await apiFetch("/bitrix-mcp/connect", {
        method: "POST",
        body: JSON.stringify({ token: token }),
      });
      var tokenInp = document.getElementById("profile-bitrix-token");
      if (tokenInp) tokenInp.value = "";
      if (msgEl) {
        msgEl.textContent = "Битрикс24 подключён.";
        msgEl.classList.remove("hidden");
      }
      await loadIntegrations();
      await loadBitrixScreen(false);
      var tgApp = window.Telegram && window.Telegram.WebApp;
      if (tgApp && tgApp.HapticFeedback) tgApp.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      var errText = "Битрикс24: " + (e.message || e);
      if (errEl) {
        errEl.textContent = errText;
        errEl.classList.remove("hidden");
      } else {
        showProfileError(errText);
      }
    }
  }

  async function disconnectBitrix() {
    clearProfileError();
    try {
      await apiFetch("/bitrix-mcp/disconnect", { method: "POST" });
    } catch (e) {
      showProfileError("bitrix: " + (e.message || e));
      return;
    }
    await loadIntegrations();
    await loadBitrixScreen(false);
  }

  var selectedKbId = null;

  function kbSyncStatusLabel(item) {
    if (!item) return "—";
    if (item.sync_status === "syncing") return "Синхронизация…";
    if (item.sync_status === "error") return "Ошибка синхронизации";
    if (item.sync_status === "ok") {
      var chunks = item.chunk_count != null ? item.chunk_count : 0;
      var when = item.last_sync_at ? String(item.last_sync_at).slice(0, 16).replace("T", " ") : "";
      return "● " + chunks + " фрагм." + (when ? " · " + when : "");
    }
    return "Ожидает синхронизации";
  }

  function kbSyncStatusTitle(item) {
    if (!item || item.sync_status !== "error") return "";
    return String(item.sync_error || "неизвестно").trim();
  }

  function kbSourceTypeLabel(t) {
    if (t === "google_docs") return "Google Docs";
    if (t === "notion") return "Notion";
    if (t === "yandex_disk") return "Яндекс Диск";
    if (t === "bitrix24_knowledge") return "Битрикс24";
    return t || "—";
  }

  function syncKbNotionFieldVisibility() {
    var urlInp = document.getElementById("profile-kb-url");
    var wrap = document.getElementById("profile-kb-notion-wrap");
    if (!wrap || !urlInp) return;
    var u = String(urlInp.value || "").toLowerCase();
    setHidden(wrap, u.indexOf("notion") < 0);
  }

  async function refreshKnowledgeBaseHint() {
    var hint = document.getElementById("profile-knowledge-base-hint");
    if (hint) hint.textContent = "Документы компании для PAIE";
  }

  async function loadKnowledgeBaseMembers(kbId) {
    var section = document.getElementById("profile-kb-members-section");
    var listEl = document.getElementById("profile-kb-members-list");
    var titleEl = document.getElementById("profile-kb-members-kb-title");
    if (!section || !listEl || !kbId) return;
    selectedKbId = kbId;
    setHidden(section, false);
    try {
      var data = await apiFetch("/knowledge-bases/" + encodeURIComponent(kbId) + "/members", {
        method: "GET",
      });
      var members = (data && data.members) || [];
      if (titleEl) {
        titleEl.textContent = "Участники с доступом к чтению и поиску в боте.";
      }
      if (!members.length) {
        listEl.innerHTML = "<p class=\"muted small\">Пока только владелец.</p>";
        return;
      }
      listEl.innerHTML = members
        .map(function (m) {
          var label = m.username ? "@" + m.username : "id " + m.telegram_user_id;
          var role = m.role === "owner" ? "владелец" : "участник";
          var revoke =
            m.role === "member"
              ? '<button type="button" class="btn ghost small" data-kb-revoke="' +
                m.telegram_user_id +
                '">Отключить</button>'
              : "";
          return (
            '<div class="svc-row">' +
            '<div class="svc-text"><p class="svc-name">' +
            label +
            '</p><p class="svc-desc">' +
            role +
            "</p></div>" +
            '<div class="svc-actions">' +
            revoke +
            "</div></div>"
          );
        })
        .join("");
      listEl.querySelectorAll("[data-kb-revoke]").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          var uid = btn.getAttribute("data-kb-revoke");
          if (!uid || !selectedKbId) return;
          try {
            await apiFetch(
              "/knowledge-bases/" +
                encodeURIComponent(selectedKbId) +
                "/members/" +
                encodeURIComponent(uid),
              { method: "DELETE" }
            );
            await loadKnowledgeBaseMembers(selectedKbId);
          } catch (e) {
            showKbError(e.message || String(e));
          }
        });
      });
    } catch (e) {
      listEl.innerHTML = "<p class=\"muted small\">" + (e.message || e) + "</p>";
    }
  }

  function showKbError(text) {
    var errEl = document.getElementById("profile-kb-err");
    if (errEl) {
      errEl.textContent = text;
      errEl.classList.remove("hidden");
    }
  }

  function clearKbMessages() {
    var msgEl = document.getElementById("profile-kb-msg");
    var errEl = document.getElementById("profile-kb-err");
    if (msgEl) {
      msgEl.textContent = "";
      msgEl.classList.add("hidden");
    }
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
  }

  async function loadKnowledgeBaseScreen() {
    clearKbMessages();
    syncKbNotionFieldVisibility();
    var listEl = document.getElementById("profile-kb-list");
    var emptyEl = document.getElementById("profile-kb-empty");
    var membersSection = document.getElementById("profile-kb-members-section");
    if (!listEl) return;
    listEl.innerHTML = "<p class=\"muted small\">Загрузка…</p>";
    if (emptyEl) emptyEl.classList.add("hidden");
    if (membersSection) setHidden(membersSection, true);
    selectedKbId = null;
    try {
      var data = await apiFetch("/knowledge-bases", { method: "GET" });
      var items = (data && data.items) || [];
      if (!items.length) {
        listEl.innerHTML = "";
        if (emptyEl) emptyEl.classList.remove("hidden");
        await refreshKnowledgeBaseHint();
        return;
      }
      listEl.innerHTML = items
        .map(function (it) {
          var isOwner = it.role === "owner";
          var statusTitle = kbSyncStatusTitle(it);
          var statusLabel = kbSyncStatusLabel(it);
          var actions =
            (isOwner
              ? '<button type="button" class="btn ghost small" data-kb-sync="' +
                it.id +
                '">Синхронизировать</button>' +
                '<button type="button" class="btn ghost small" data-kb-members="' +
                it.id +
                '">Доступ</button>' +
                '<button type="button" class="btn ghost small" data-kb-delete="' +
                it.id +
                '">Удалить</button>'
              : "") +
            "";
          return (
            '<div class="svc-row svc-row--kb" data-kb-item="' +
            it.id +
            '">' +
            '<div class="svc-text"><p class="svc-name">' +
            escapeHtml(it.title || "База знаний") +
            '</p><p class="svc-desc"' +
            (statusTitle
              ? ' title="' + escapeHtml(statusTitle).replace(/"/g, "&quot;") + '"'
              : "") +
            ">" +
            escapeHtml(kbSourceTypeLabel(it.source_type) +
            " · " +
            statusLabel +
            (isOwner ? "" : " · общая")) +
            "</p></div>" +
            '<div class="svc-actions">' +
            actions +
            "</div></div>"
          );
        })
        .join("");
      listEl.querySelectorAll("[data-kb-sync]").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          var id = btn.getAttribute("data-kb-sync");
          if (!id) return;
          clearKbMessages();
          btn.disabled = true;
          try {
            await apiFetch("/knowledge-bases/" + encodeURIComponent(id) + "/sync", {
              method: "POST",
            });
            var msgEl = document.getElementById("profile-kb-msg");
            if (msgEl) {
              msgEl.textContent = "Синхронизация завершена.";
              msgEl.classList.remove("hidden");
            }
            await loadKnowledgeBaseScreen();
          } catch (e) {
            showKbError(e.message || String(e));
          } finally {
            btn.disabled = false;
          }
        });
      });
      listEl.querySelectorAll("[data-kb-delete]").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          var id = btn.getAttribute("data-kb-delete");
          if (!id || !window.confirm("Удалить базу знаний и индекс?")) return;
          try {
            await apiFetch("/knowledge-bases/" + encodeURIComponent(id), {
              method: "DELETE",
            });
            await loadKnowledgeBaseScreen();
          } catch (e) {
            showKbError(e.message || String(e));
          }
        });
      });
      listEl.querySelectorAll("[data-kb-members]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-kb-members");
          if (id) loadKnowledgeBaseMembers(id);
        });
      });
      await refreshKnowledgeBaseHint();
    } catch (e) {
      listEl.innerHTML = "<p class=\"muted small\">" + (e.message || e) + "</p>";
    }
  }

  function knowledgeItemMatchesFilter(item) {
    var parsed = parseNotesSearchQuery(knowledgeSearchQuery);
    if (parsed.hashtags.length) {
      var itemHash = noteHashtagsOf(item).map(function (h) {
        return String(h.name || "").toLowerCase();
      });
      for (var i = 0; i < parsed.hashtags.length; i++) {
        if (itemHash.indexOf(parsed.hashtags[i]) < 0) return false;
      }
    }
    var q = parsed.text;
    if (!q) return true;
    var title =
      sanitizeNoteTitle(item.title || item.content || "") ||
      String(item.title || item.content || "");
    var preview = notePlainExcerpt(item.description || item.body || "", 500);
    var project = noteProjectOf(item);
    var projectName = project ? String(project.name || "") : "";
    var hashNames = noteHashtagsOf(item)
      .map(function (t) {
        return "#" + String(t.name || "");
      })
      .join(" ");
    var hay = (title + " " + preview + " " + projectName + " " + hashNames).toLowerCase();
    return hay.indexOf(q) >= 0;
  }

  function renderKnowledgePaneFromData(data) {
    var pane = document.getElementById("knowledge-pane");
    if (!pane) return;
    pane.innerHTML = "";
    var all = (data && data.notes) || [];
    var list = all.filter(knowledgeItemMatchesFilter);
    if (!all.length) {
      pane.innerHTML =
        '<p class="muted empty-hint">Документов пока нет. Нажмите «+», чтобы создать.</p>';
      return;
    }
    if (!list.length) {
      pane.innerHTML =
        '<p class="muted empty-hint">Ничего не найдено. Измените поиск.</p>';
      return;
    }
    list.forEach(function (n) {
      var noteId = localNoteIdFrom(n);
      var id = noteId != null ? String(noteId) : "";
      var card = document.createElement("div");
      card.className = "note-card";
      if (id) card.setAttribute("data-note-id", id);
      var inner = document.createElement("div");
      inner.className = "note-card-inner";
      var titleRaw =
        sanitizeNoteTitle(n.title || n.content || "") ||
        (n.title || n.content || "(без названия)");
      var descRaw = notePlainExcerpt(n.description || n.body || "", 280);
      var descHtml = linkifyEscaped(escapeHtml(descRaw));
      inner.innerHTML =
        '<p class="note-card-excerpt">' +
        escapeHtml(titleRaw) +
        "</p>" +
        (descRaw
          ? '<p class="note-card-excerpt note-card-excerpt--secondary muted">' +
            descHtml +
            (descRaw.length >= 280 ? "…" : "") +
            "</p>"
          : "");
      appendItemLabelChips(inner, n);
      mountNoteCardActions(card, inner, n);
      appendNoteCardAvatars(inner, n.members);
      card.appendChild(inner);
      card.addEventListener("click", function () {
        openKnowledgeNoteDetail(n);
      });
      pane.appendChild(
        wrapWithSwipeDelete(
          card,
          async function () {
            await deleteLocalNoteById(id);
            if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
          },
          {
            removeStack: true,
            confirmMessage:
              n.is_owner === false ? "Убрать документ из списка?" : "Удалить документ?",
          }
        )
      );
    });
  }

  async function loadKnowledgeNotes() {
    var err = document.getElementById("knowledge-global-error");
    if (err) {
      err.textContent = "";
      setHidden(err, true);
    }
    if (knowledgeDataCache) {
      renderKnowledgePaneFromData(knowledgeDataCache);
    } else {
      var cached = readMiniappCache("knowledge");
      if (cached) {
        knowledgeDataCache = cached;
        renderKnowledgePaneFromData(cached);
      }
    }
    try {
      var data = await apiFetch("/notes/knowledge", { method: "GET" });
      knowledgeDataCache = { notes: (data && data.notes) || [] };
      writeMiniappCache("knowledge", knowledgeDataCache);
      renderKnowledgePaneFromData(knowledgeDataCache);
    } catch (e) {
      if (!knowledgeDataCache && err) {
        err.textContent = e.message || String(e);
        setHidden(err, false);
      }
    }
    syncKnowledgeCreateFab();
    syncNoteKnowledgeToggle();
  }

  function openNewKnowledgeNote() {
    openLocalNoteDetail(
      {
        id: "",
        title: "",
        content: "",
        description: "",
        role: "knowledge",
        is_knowledge: true,
      },
      { create: true, isLocal: true, knowledge: true }
    );
  }

  function openKnowledgeNoteDetail(n, opts) {
    opts = Object.assign({ isLocal: true, knowledge: true }, opts || {});
    openLocalNoteDetail(resolveKnowledgeNoteFromCache(n), opts);
  }

  function openKnowledgeNote() {
    setTab("knowledge");
  }

  async function addKnowledgeBase() {
    clearKbMessages();
    var titleInp = document.getElementById("profile-kb-title");
    var urlInp = document.getElementById("profile-kb-url");
    var notionInp = document.getElementById("profile-kb-notion-token");
    var addBtn = document.getElementById("profile-kb-add");
    var title = titleInp ? String(titleInp.value || "").trim() : "";
    var url = urlInp ? String(urlInp.value || "").trim() : "";
    var notionToken = notionInp ? String(notionInp.value || "").trim() : "";
    if (!url) {
      showKbError("Укажите ссылку на документ или папку.");
      return;
    }
    if (addBtn) addBtn.disabled = true;
    try {
      var body = { title: title || "База знаний", source_url: url };
      if (notionToken) body.notion_token = notionToken;
      await apiFetch("/knowledge-bases", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (titleInp) titleInp.value = "";
      if (urlInp) urlInp.value = "";
      if (notionInp) notionInp.value = "";
      var msgEl = document.getElementById("profile-kb-msg");
      if (msgEl) {
        msgEl.textContent = "База подключена и проиндексирована.";
        msgEl.classList.remove("hidden");
      }
      await loadKnowledgeBaseScreen();
    } catch (e) {
      showKbError(e.message || String(e));
    } finally {
      if (addBtn) addBtn.disabled = false;
    }
  }

  async function oauthStart(svc) {
    if (svc === "bitrix") {
      openBitrixSetup();
      return;
    }
    if (svc === "yandex-disk") {
      openYandexDiskSetup();
      return;
    }
    if (svc === "telemost") {
      openTelemostSetup();
      return;
    }
    clearProfileError();
    var label =
      svc === "google"
        ? "Google Calendar"
        : svc === "zoom"
          ? "Zoom"
          : svc === "telemost"
            ? "Телемост"
          : svc === "yandex-disk"
            ? "Яндекс Диск"
            : svc === "bitrix"
              ? "Битрикс24"
            : "Todoist";
    try {
      const data = await apiFetch("/oauth/" + svc + "/start", { method: "POST" });
      const url = data && data.url;
      if (!url) {
        showProfileError(label + ": нет URL авторизации");
        return;
      }
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.openLink) {
        tg.openLink(url);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
      startOAuthStatusPoll();
    } catch (e) {
      showProfileError(label + ": " + (e.message || e));
    }
  }

  async function oauthDisconnect(svc) {
    clearProfileError();
    if (svc === "bitrix") {
      await disconnectBitrix();
      return;
    }
    try {
      await apiFetch("/oauth/" + svc + "/disconnect", { method: "POST" });
    } catch (e) {
      showProfileError(svc + ": " + (e.message || e));
      return;
    }
    await loadIntegrations();
    if (svc === "zoom") await loadZoomScreen(false);
    if (svc === "telemost") await loadTelemostScreen(false);
    if (svc === "yandex-disk") await loadYandexDiskScreen(false);
    if (svc === "bitrix") await loadBitrixScreen(false);
  }

  /* ——— Modal (reminder / event) ——— */
  var modalSheetOpen = false;
  var modalSheetViewportBound = false;

  function getNoteEditorScrollEl() {
    var ov = document.getElementById("note-editor-overlay");
    if (!ov) return null;
    return ov.querySelector(".note-editor-modal-body");
  }

  var noteEditorBgLock = null;
  var noteCaretScrollTimer = null;
  var noteEditorTouchGuardBound = false;
  var noteEditorKbWasOpen = false;

  function isNoteEditorScrollTarget(target) {
    if (!target || !target.closest) return false;
    return !!target.closest(
      ".note-editor-modal-body, .note-editor-toolbar-wrap, .note-more-menu, .note-share-sheet-overlay, .note-link-popover, .tag-picker-menu, .note-discussion-overlay, .note-link-overlay, textarea, input"
    );
  }

  var noteEditorTouchStartY = 0;

  function onNoteEditorTouchStartGuard(e) {
    if (e.touches && e.touches[0]) noteEditorTouchStartY = e.touches[0].clientY;
  }

  function onNoteEditorTouchMoveGuard(e) {
    if (!isNoteEditorModalOpen()) return;
    if (isNoteEditorScrollTarget(e.target)) {
      var scrollEl = getNoteEditorScrollEl();
      if (!scrollEl || !e.target || !scrollEl.contains(e.target)) return;
      var y = e.touches && e.touches[0] ? e.touches[0].clientY : noteEditorTouchStartY;
      var dy = y - noteEditorTouchStartY;
      var atTop = scrollEl.scrollTop <= 0;
      var atBottom =
        scrollEl.scrollTop + scrollEl.clientHeight >= scrollEl.scrollHeight - 1;
      if (scrollEl.scrollHeight <= scrollEl.clientHeight + 2) {
        e.preventDefault();
        return;
      }
      if ((atTop && dy > 0) || (atBottom && dy < 0)) {
        e.preventDefault();
      }
      return;
    }
    e.preventDefault();
  }

  function resetNoteEditorOverlayPin() {
    var ov = document.getElementById("note-editor-overlay");
    if (ov) {
      ov.style.top = "";
      ov.style.left = "";
      ov.style.right = "";
      ov.style.bottom = "";
      ov.style.width = "";
      ov.style.height = "";
    }
    var sheet = ov && ov.querySelector(".note-editor-sheet");
    if (sheet) {
      sheet.style.height = "";
      sheet.style.maxHeight = "";
      sheet.style.transform = "";
    }
    var content = document.getElementById("note-editor-modal-body");
    if (content) content.style.paddingBottom = "";
    var scrollEl = document.querySelector("#note-editor-overlay .note-editor-modal-body");
    if (scrollEl) scrollEl.style.paddingBottom = "";
  }

  function lockNoteEditorBackground() {
    if (noteEditorBgLock) return;
    var main = document.querySelector(".main-scroll");
    noteEditorBgLock = {
      y: window.scrollY || window.pageYOffset || 0,
      main: main ? main.scrollTop : 0,
    };
    document.documentElement.classList.add("note-editor-open");
    document.body.style.position = "fixed";
    document.body.style.width = "100%";
    document.body.style.left = "0";
    document.body.style.right = "0";
    document.body.style.top = "-" + noteEditorBgLock.y + "px";
    if (!noteEditorTouchGuardBound) {
      noteEditorTouchGuardBound = true;
      document.addEventListener("touchstart", onNoteEditorTouchStartGuard, {
        passive: true,
        capture: true,
      });
      document.addEventListener("touchmove", onNoteEditorTouchMoveGuard, {
        passive: false,
        capture: true,
      });
    }
  }

  function unlockNoteEditorBackground() {
    document.documentElement.classList.remove("note-editor-open");
    document.documentElement.classList.remove("note-editor-kb-open");
    document.documentElement.style.removeProperty("--note-editor-kb-inset");
    document.body.style.position = "";
    document.body.style.width = "";
    document.body.style.left = "";
    document.body.style.right = "";
    document.body.style.top = "";
    resetNoteEditorOverlayPin();
    noteEditorKbWasOpen = false;
    if (noteEditorBgLock) {
      var y = noteEditorBgLock.y;
      var mainTop = noteEditorBgLock.main;
      noteEditorBgLock = null;
      var main = document.querySelector(".main-scroll");
      if (main) main.scrollTop = mainTop;
      window.scrollTo(0, y);
    }
  }

  function noteEditorToolbarHeightPx() {
    var toolbar = document.getElementById("note-editor-toolbar-wrap");
    if (
      !toolbar ||
      toolbar.classList.contains("hidden") ||
      toolbar.classList.contains("is-composer-hidden")
    ) {
      return 0;
    }
    var h = Math.ceil(toolbar.getBoundingClientRect().height);
    return h > 0 ? h : 64;
  }

  function noteEditorLineRoomPx() {
    var el =
      document.querySelector("#note-editor-overlay .ProseMirror") ||
      document.querySelector("#note-editor-overlay .note-rich-editor-body") ||
      getNoteEditorScrollEl();
    var lh = 28;
    if (el) {
      var parsed = parseFloat(window.getComputedStyle(el).lineHeight);
      if (parsed > 8 && isFinite(parsed)) lh = parsed;
    }
    return Math.round(lh * 3);
  }

  function syncNoteEditorEndPad() {
    var scrollEl = getNoteEditorScrollEl();
    var content = document.getElementById("note-editor-modal-body");
    if (content) content.style.paddingBottom = "";
    if (!scrollEl) return false;
    if (!isNoteEditorModalOpen() || !isMobileNoteLayout()) {
      if (scrollEl.style.paddingBottom) scrollEl.style.paddingBottom = "";
      return false;
    }
    var toolbarH = noteEditorToolbarHeightPx();
    var lineRoom = noteEditorLineRoomPx();
    var prevPad = scrollEl.style.paddingBottom;
    scrollEl.style.paddingBottom = "0px";
    var contentH = scrollEl.scrollHeight;
    var viewH = scrollEl.clientHeight;
    var pad = toolbarH + lineRoom + Math.max(0, viewH - contentH);
    var nextPad = Math.round(pad) + "px";
    scrollEl.style.paddingBottom = nextPad;
    var changed = prevPad !== nextPad;
    if (changed && document.documentElement.classList.contains("note-editor-kb-open")) {
      window.setTimeout(function () {
        scrollNoteCaretIntoView(true);
      }, 40);
    }
    return changed;
  }

  function pinNoteEditorOverlayToVisualViewport() {
    var ov = document.getElementById("note-editor-overlay");
    var sheet = ov && ov.querySelector(".note-editor-sheet");
    if (!ov || ov.classList.contains("hidden") || !isMobileNoteLayout()) {
      resetNoteEditorOverlayPin();
      return 0;
    }
    var vv = window.visualViewport;
    var visH = noteEditorVisibleHeightPx();
    var layoutH = Math.max(
      window.innerHeight || 0,
      document.documentElement.clientHeight || 0,
      visH
    );
    var offsetTop = vv && typeof vv.offsetTop === "number" ? Math.round(vv.offsetTop) : 0;
    var offsetLeft = vv && typeof vv.offsetLeft === "number" ? Math.round(vv.offsetLeft) : 0;
    var visW =
      vv && typeof vv.width === "number" && vv.width > 0
        ? Math.round(vv.width)
        : window.innerWidth;
    ov.style.top = "0px";
    ov.style.left = "0px";
    ov.style.right = "auto";
    ov.style.bottom = "auto";
    ov.style.width = Math.max(window.innerWidth || 0, visW + offsetLeft) + "px";
    ov.style.height = Math.max(layoutH, visH + offsetTop) + "px";
    var ovTop = Math.round(ov.getBoundingClientRect().top);
    var sheetShift = ovTop < -8 ? -ovTop : 0;
    var sheetH = visH > 0 ? visH : layoutH;
    if (sheet) {
      sheet.style.height = sheetH + "px";
      sheet.style.maxHeight = sheetH + "px";
      sheet.style.transform = sheetShift ? "translateY(" + sheetShift + "px)" : "";
    }
    var kbGuess = Math.max(0, Math.round(layoutH - sheetH - offsetTop));
    if (kbGuess < 40) kbGuess = 0;
    var kbOpen = kbGuess > 40;
    document.documentElement.classList.toggle("note-editor-kb-open", kbOpen);
    document.documentElement.style.setProperty("--note-editor-kb-inset", kbGuess + "px");
    var padChanged = syncNoteEditorEndPad();
    if (kbOpen && (!noteEditorKbWasOpen || padChanged)) {
      window.setTimeout(function () {
        scrollNoteCaretIntoView(true);
      }, noteEditorKbWasOpen ? 40 : 280);
    }
    noteEditorKbWasOpen = kbOpen;
    return kbGuess;
  }

  function noteEditorHasRangeSelection() {
    var editor = getActiveNoteRichEditor();
    if (editor && typeof editor.hasRangeSelection === "function") {
      try {
        if (editor.hasRangeSelection()) return true;
      } catch (_) {}
    }
    var sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
    var scrollEl = getNoteEditorScrollEl();
    if (!scrollEl) return false;
    var range = sel.getRangeAt(0);
    var root = range && range.commonAncestorContainer;
    return !!(root && scrollEl.contains(root.nodeType === 1 ? root : root.parentNode));
  }

  function scrollNoteCaretIntoView(immediate) {
    if (!isNoteEditorModalOpen()) return;
    if (noteSheetState && noteSheetState.skipCaretScroll) return;
    if (noteSheetState && noteSheetState.focus === "right") return;
    if (noteEditorHasRangeSelection()) return;
    var run = function () {
      if (noteEditorHasRangeSelection()) return;
      var scrollEl = getNoteEditorScrollEl();
      if (!scrollEl) return;
      var active = document.activeElement;
      var caretRect = null;
      if (active && active.id === "note-editor-title-input") {
        caretRect = active.getBoundingClientRect();
      } else {
        var editor = getActiveNoteRichEditor();
        if (editor && typeof editor.coordsAtPos === "function" && typeof editor.getCursor === "function") {
          try {
            caretRect = editor.coordsAtPos(editor.getCursor());
          } catch (_) {
            caretRect = null;
          }
        }
        if (!caretRect && active && active.getBoundingClientRect && isNoteRichEditorField(active)) {
          caretRect = active.getBoundingClientRect();
        }
        if (!caretRect) {
          var sel = window.getSelection && window.getSelection();
          if (sel && sel.rangeCount) {
            var range = sel.getRangeAt(0);
            if (range && scrollEl.contains(range.startContainer)) {
              var rects = range.getClientRects();
              caretRect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
            }
          }
        }
      }
      if (!caretRect || (caretRect.width === 0 && caretRect.height === 0 && !caretRect.top)) return;
      var bodyRect = scrollEl.getBoundingClientRect();
      var toolbar = document.getElementById("note-editor-toolbar-wrap");
      var toolbarH = 0;
      if (toolbar && !toolbar.classList.contains("hidden")) {
        var tb = toolbar.getBoundingClientRect();
        if (tb.top < bodyRect.bottom && tb.bottom > bodyRect.top) {
          toolbarH = Math.max(0, bodyRect.bottom - tb.top);
        }
      }
      var margin = isMobileNoteLayout() ? 72 : 20;
      var bottomLimit = bodyRect.bottom - toolbarH - margin;
      var topLimit = bodyRect.top + margin;
      var bottom = caretRect.bottom != null ? caretRect.bottom : caretRect.top;
      var top = caretRect.top;
      if (bottom > bottomLimit) {
        scrollEl.scrollTop += bottom - bottomLimit;
      } else if (top < topLimit) {
        scrollEl.scrollTop -= topLimit - top;
      }
    };
    if (immediate) {
      run();
      return;
    }
    if (noteCaretScrollTimer) clearTimeout(noteCaretScrollTimer);
    noteCaretScrollTimer = window.setTimeout(function () {
      noteCaretScrollTimer = null;
      window.requestAnimationFrame(run);
    }, 60);
  }

  function isMobileNoteLayout() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 959px)").matches);
  }

  function composerUiActive() {
    var form = document.getElementById("note-paie-reply-form");
    if (!form) return false;
    var active = document.activeElement;
    if (active && form.contains(active)) return true;
    return !!form.querySelector(".note-paie-menu:not(.hidden)");
  }

  function noteComposerMenuOpen() {
    var form = document.getElementById("note-paie-reply-form");
    return !!(form && form.querySelector(".note-paie-menu:not(.hidden)"));
  }

  function noteFormatUiActive() {
    if (composerUiActive()) return false;
    var active = document.activeElement;
    if (!active || !active.closest) return false;
    if (active.closest(".note-editor-toolbar-wrap, .note-link-popover, .note-rich-editor-toolbar")) {
      return true;
    }
    return isNoteRichEditorField(active) || !!active.closest(".note-rich-editor-mount");
  }

  function syncNoteFormatToolbarForComposer() {
    var wrap = document.getElementById("note-editor-toolbar-wrap");
    if (!wrap) return;
    wrap.classList.remove("is-composer-hidden");
    if (!isNoteEditorModalOpen() || !isMobileNoteLayout()) {
      wrap.classList.remove("is-format-active");
      return;
    }
    wrap.classList.toggle("is-format-active", noteFormatUiActive());
    syncNoteEditorEndPad();
  }

  function noteGptIconHtml() {
    return (
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
      '<path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A6.066 6.066 0 0 0 19.019 19.82a5.988 5.988 0 0 0 3.997-2.9 6.052 6.052 0 0 0-.739-7.098ZM13.254 21.3a4.47 4.47 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494ZM3.6 17.49a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.49 19.86a4.504 4.504 0 0 1-5.89-2.37Zm-.963-8.02a4.482 4.482 0 0 1 2.352-1.972V12.1a.77.77 0 0 0 .388.676l5.815 3.358-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.637 9.47Zm16.08-.352-4.784-2.77a.766.766 0 0 0-.78 0L7.311 9.718V7.386a.066.066 0 0 1 .028-.061l4.83-2.787a4.504 4.504 0 0 1 6.68 4.66l-.14-.08Zm1.34 3.043-2.021-1.167a.077.077 0 0 1-.037-.053V5.357a.08.08 0 0 1 .033-.061l4.83 2.787a4.504 4.504 0 0 1-.49 8.129l-.141-.085-4.773-2.758a.794.794 0 0 0-.393-.68Zm-9.75 1.17L7.59 10.16l2.02-1.164a.08.08 0 0 1 .072 0l4.83 2.786a.077.077 0 0 1 .037.052v2.332l-2.02 1.168a.074.074 0 0 1-.072 0Z"/>' +
      "</svg>"
    );
  }

  function syncNoteGptToolbarButton(show) {
    var wrap = document.getElementById("note-editor-toolbar-wrap");
    var btn = document.getElementById("note-editor-gpt-btn");
    if (btn) {
      if (!btn.innerHTML.trim()) btn.innerHTML = noteGptIconHtml();
      setHidden(btn, !show);
    }
    if (wrap) wrap.classList.toggle("has-gpt-btn", !!show);
    syncNoteEditorEndPad();
  }

  function bindNoteGptToolbarButton() {
    var btn = document.getElementById("note-editor-gpt-btn");
    if (!btn || btn._bound) return;
    btn._bound = true;
    function openGpt(e) {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      var body = getNoteEditorBodyEl();
      if (body && typeof body._openNoteGpt === "function") {
        body._openNoteGpt();
      }
    }
    btn.addEventListener("click", openGpt);
    btn.addEventListener("touchend", function (e) {
      e.preventDefault();
      openGpt(e);
    });
  }

  function noteEditorVisibleHeightPx() {
    var tg = window.Telegram && window.Telegram.WebApp;
    var vv = window.visualViewport;
    var heights = [];
    if (tg && typeof tg.viewportHeight === "number" && tg.viewportHeight > 0) {
      heights.push(tg.viewportHeight);
    }
    if (vv && typeof vv.height === "number" && vv.height > 0) {
      heights.push(vv.height);
    }
    if (window.innerHeight > 0) heights.push(window.innerHeight);
    if (!heights.length) return 0;
    return Math.round(Math.min.apply(null, heights));
  }

  var modalSheetScrollTimer = null;

  function isNoteRichEditorField(field) {
    if (!field || !field.matches) return false;
    return !!field.matches(
      ".ProseMirror, .note-rich-editor-body, .note-rich-editor-mount, .note-rich-editor"
    );
  }

  function scrollModalFieldIntoView(field) {
    if (!field) return;
    if (isNoteRichEditorField(field) || (field.id === "note-editor-title-input")) {
      scrollNoteCaretIntoView();
      window.setTimeout(function () {
        scrollNoteCaretIntoView(true);
      }, 380);
      return;
    }
    var scrollEl = isNoteEditorModalOpen() ? getNoteEditorScrollEl() : getActiveModalBody();
    if (!scrollEl) return;
    if (modalSheetScrollTimer) clearTimeout(modalSheetScrollTimer);
    modalSheetScrollTimer = window.setTimeout(function () {
      modalSheetScrollTimer = null;
      var bodyRect = scrollEl.getBoundingClientRect();
      var fieldRect = field.getBoundingClientRect();
      var margin = 16;
      if (fieldRect.bottom > bodyRect.bottom - margin) {
        scrollEl.scrollTop += fieldRect.bottom - bodyRect.bottom + margin;
      } else if (fieldRect.top < bodyRect.top + margin) {
        scrollEl.scrollTop -= bodyRect.top + margin - fieldRect.top;
      }
    }, 280);
  }

  function resetModalSheetViewportStyles(sheet) {
    if (!sheet) return;
    sheet.style.maxHeight = "";
    sheet.style.height = "";
    sheet.style.transform = "";
    var body = sheet.querySelector(".modal-body");
    if (body) body.style.paddingBottom = "";
    var noteScroll = document.querySelector(
      "#note-editor-overlay .note-editor-modal-body"
    );
    if (noteScroll) noteScroll.style.paddingBottom = "";
    document.documentElement.style.removeProperty("--note-editor-kb-inset");
    document.documentElement.classList.remove("note-editor-kb-open");
    syncNoteFormatToolbarForComposer();
  }

  function updateModalSheetForKeyboard() {
    if (isNoteDiscussionOpen() && !isDesktopLayout()) {
      freezeNoteEditorUnderDiscussion();
      updateDiscussionOverlayForKeyboard();
      return;
    }
    var sheet = getActiveModalSheet();
    var body = sheet && sheet.querySelector(".modal-body");
    if (!sheet || !body) return;
    var noteEditorOpen = isNoteEditorModalOpen();
    var vv = window.visualViewport;
    var root = document.documentElement;
    var tg = window.Telegram && window.Telegram.WebApp;

    if (noteEditorOpen) {
      if (isMobileNoteLayout() && noteComposerMenuOpen()) return;
      if (isMobileNoteLayout()) lockNoteEditorBackground();
      pinNoteEditorOverlayToVisualViewport();
      if (!isMobileNoteLayout()) {
        var visibleH = noteEditorVisibleHeightPx();
        if (visibleH > 0) {
          sheet.style.height = visibleH + "px";
          sheet.style.maxHeight = visibleH + "px";
        } else {
          sheet.style.height = "100%";
          sheet.style.maxHeight = "100%";
        }
        sheet.style.transform = "";
      }
      body.style.paddingBottom = "";
      return;
    }

    var eventModal = !!(sheet.closest && sheet.closest("#modal-overlay"));
    if (eventModal && !isDesktopLayout()) {
      var visibleH = noteEditorVisibleHeightPx();
      if (visibleH > 0) {
        sheet.style.height = visibleH + "px";
        sheet.style.maxHeight = visibleH + "px";
      } else {
        sheet.style.height = "100%";
        sheet.style.maxHeight = "100%";
      }
      var offsetTop = vv && typeof vv.offsetTop === "number" ? vv.offsetTop : 0;
      sheet.style.transform =
        offsetTop > 0 ? "translateY(" + Math.round(offsetTop) + "px)" : "";
      body.style.paddingBottom = "";
      root.style.removeProperty("--note-editor-kb-inset");
      root.classList.remove("note-editor-kb-open");
      return;
    }
    if (!vv) return;
    var kb = Math.max(0, Math.round(window.innerHeight - vv.height - (vv.offsetTop || 0)));
    var visibleH = Math.round(vv.height);
    sheet.style.height = "";
    sheet.style.maxHeight = visibleH + "px";
    sheet.style.transform = "";
    root.style.removeProperty("--note-editor-kb-inset");
    root.classList.remove("note-editor-kb-open");
    body.style.paddingBottom =
      kb > 0
        ? "calc(var(--space-4) + env(safe-area-inset-bottom, 0px) + " +
          Math.round(kb) +
          "px)"
        : "";
  }

  function bindModalSheetMobileOnce() {
    if (modalSheetViewportBound) return;
    modalSheetViewportBound = true;
    document.addEventListener("focusin", function (e) {
      if (!modalSheetOpen) return;
      var body = getActiveModalBody();
      if (!body) return;
      var t = e.target;
      if (!t || !t.closest) return;
      var field = t.closest(
        "input, textarea, select, .ProseMirror, .note-rich-editor-mount"
      );
      if (!field || !body.contains(field)) return;
      if (field.closest && field.closest(".note-paie-model-search, .note-paie-menu")) {
        syncNoteFormatToolbarForComposer();
        return;
      }
      updateModalSheetForKeyboard();
      scrollModalFieldIntoView(field);
      syncNoteFormatToolbarForComposer();
    });
    document.addEventListener("input", function (e) {
      if (!modalSheetOpen || !isNoteEditorModalOpen()) return;
      var t = e.target;
      if (!t) return;
      if (t.id === "note-editor-title-input" || isNoteRichEditorField(t)) {
        scrollNoteCaretIntoView();
      }
    });
    document.addEventListener(
      "pointerdown",
      function (e) {
        if (!modalSheetOpen || !isNoteEditorModalOpen() || !isMobileNoteLayout()) return;
        var t = e.target;
        if (!t || !t.closest || !t.closest("#note-editor-overlay")) return;
        if (t.closest("#note-editor-gpt-btn, .note-discussion-card")) return;
        var wrap = document.getElementById("note-editor-toolbar-wrap");
        if (!wrap || wrap.classList.contains("hidden")) return;
        wrap.classList.remove("is-composer-hidden");
        if (
          t.closest(
            ".note-rich-editor-mount, .ProseMirror, .note-editor-format-toolbar-host, .note-link-popover"
          )
        ) {
          wrap.classList.add("is-format-active");
          return;
        }
        wrap.classList.remove("is-format-active");
      },
      true
    );
    document.addEventListener("focusout", function () {
      if (!modalSheetOpen) return;
      window.setTimeout(function () {
        if (!modalSheetOpen) return;
        updateModalSheetForKeyboard();
        syncNoteFormatToolbarForComposer();
      }, 80);
    });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", function () {
        if (modalSheetOpen || isNoteDiscussionOpen()) updateModalSheetForKeyboard();
      });
      window.visualViewport.addEventListener("scroll", function () {
        if (modalSheetOpen || isNoteDiscussionOpen()) updateModalSheetForKeyboard();
      });
    }
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && typeof tg.onEvent === "function" && !tg._leoNoteEditorViewportBound) {
      tg._leoNoteEditorViewportBound = true;
      try {
        tg.onEvent("viewportChanged", function () {
          if (modalSheetOpen) updateModalSheetForKeyboard();
        });
      } catch (_) {}
    }
  }

  function onModalSheetOpen() {
    modalSheetOpen = true;
    document.documentElement.classList.add("modal-sheet-open");
    if (isNoteEditorModalOpen() && isMobileNoteLayout()) lockNoteEditorBackground();
    bindModalSheetMobileOnce();
    updateModalSheetForKeyboard();
    syncNoteFormatToolbarForComposer();
  }

  function onModalSheetClose() {
    modalSheetOpen = false;
    document.documentElement.classList.remove("modal-sheet-open");
    unlockNoteEditorBackground();
    document.querySelectorAll(".modal--sheet").forEach(resetModalSheetViewportStyles);
  }

  function closeModal() {
    const ov = document.getElementById("modal-overlay");
    ov.classList.add("hidden");
    ov.setAttribute("aria-hidden", "true");
    document.getElementById("modal-delete").classList.add("hidden");
    taskModalOnSaved = null;
    taskModalOnDeleted = null;
    onModalSheetClose();
    syncAppOverlay();
  }

  function normalizeReminderChecklist(raw) {
    if (!Array.isArray(raw)) return [];
    return raw
      .map(function (it, idx) {
        if (!it || typeof it !== "object") return null;
        var text = String(it.text || "").trim();
        if (!text) return null;
        return {
          id: String(it.id || "c" + idx + "-" + Date.now()).trim(),
          text: text,
          done: !!it.done,
        };
      })
      .filter(Boolean);
  }

  function mountReminderChecklist(host, initial) {
    var items = normalizeReminderChecklist(initial);
    var listEl = document.createElement("div");
    listEl.className = "rem-checklist";
    listEl.setAttribute("role", "list");
    var addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "rem-checklist-add";
    addBtn.innerHTML =
      '<span class="rem-checklist-add-icon" aria-hidden="true">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round">' +
      '<path d="M12 5v14M5 12h14"/>' +
      "</svg></span>" +
      '<span class="rem-checklist-add-label">Добавить задачу</span>';

    function paintList() {
      listEl.innerHTML = "";
      items.forEach(function (it, idx) {
        var row = document.createElement("div");
        row.className = "rem-checklist-item" + (it.done ? " rem-checklist-item--done" : "");
        row.setAttribute("role", "listitem");
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "rem-checklist-cb";
        cb.checked = !!it.done;
        cb.setAttribute("aria-label", "Выполнено");
        cb.addEventListener("change", function () {
          items[idx].done = !!cb.checked;
          row.classList.toggle("rem-checklist-item--done", !!cb.checked);
        });
        var text = document.createElement("span");
        text.className = "rem-checklist-text";
        text.textContent = it.text;
        var del = document.createElement("button");
        del.type = "button";
        del.className = "rem-checklist-del";
        del.setAttribute("aria-label", "Удалить задачу");
        del.title = "Удалить";
        del.innerHTML =
          '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
          '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>' +
          '<path d="M10 11v6M14 11v6"/>' +
          "</svg>";
        del.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          confirmDialog("Удалить задачу «" + it.text + "»?").then(function (ok) {
            if (!ok) return;
            items.splice(idx, 1);
            paintList();
          });
        });
        row.appendChild(cb);
        row.appendChild(text);
        row.appendChild(del);
        listEl.appendChild(row);
      });
    }

    function startDraft() {
      if (host.querySelector(".rem-checklist-draft")) return;
      addBtn.classList.add("hidden");
      var draft = document.createElement("div");
      draft.className = "rem-checklist-draft";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "rem-checklist-cb";
      cb.setAttribute("aria-label", "Выполнено");
      var input = document.createElement("input");
      input.type = "text";
      input.className = "field-input rem-checklist-draft-input";
      input.placeholder = "Текст задачи";
      input.autocomplete = "off";
      var committed = false;

      function commit() {
        if (committed) return;
        committed = true;
        var text = String(input.value || "").trim();
        if (draft.parentNode) draft.parentNode.removeChild(draft);
        if (text) {
          items.push({
            id: "c" + Date.now().toString(36) + Math.floor(Math.random() * 1e4),
            text: text,
            done: !!cb.checked,
          });
          paintList();
        }
        addBtn.classList.remove("hidden");
      }

      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          e.stopPropagation();
          commit();
        }
        if (e.key === "Escape") {
          e.preventDefault();
          input.value = "";
          commit();
        }
      });
      input.addEventListener("blur", function () {
        window.setTimeout(commit, 0);
      });
      // Keep focus when tapping the draft checkbox.
      cb.addEventListener("mousedown", function (e) {
        e.preventDefault();
      });
      cb.addEventListener("touchstart", function (e) {
        e.preventDefault();
      }, { passive: false });

      draft.appendChild(cb);
      draft.appendChild(input);
      host.insertBefore(draft, addBtn);
      window.setTimeout(function () {
        input.focus();
      }, 0);
    }

    addBtn.addEventListener("click", function (e) {
      e.preventDefault();
      startDraft();
    });

    paintList();
    host.appendChild(listEl);
    host.appendChild(addBtn);

    return {
      getItems: function () {
        return items.map(function (it) {
          return { id: it.id, text: it.text, done: !!it.done };
        });
      },
    };
  }

  var reminderChecklistApi = null;
  var meetingAttendeesApi = null;
  var taskAssigneeApi = null;
  var taskModalOnSaved = null;
  var taskModalOnDeleted = null;
  var MEETING_EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;

  function meetingEmailKey(email) {
    return String(email || "").trim().toLowerCase();
  }

  var calendarContactsByEmail = {};

  function setCalendarContacts(items) {
    calendarContactsByEmail = {};
    (items || []).forEach(function (c) {
      var k = meetingEmailKey(c && c.email);
      if (k) calendarContactsByEmail[k] = c;
    });
  }

  function refreshCalendarContacts() {
    return apiFetch("/contacts", { method: "GET" })
      .then(function (data) {
        setCalendarContacts((data && data.items) || []);
        return calendarContactsByEmail;
      })
      .catch(function () {
        return calendarContactsByEmail;
      });
  }

  function contactByAttendeeEmail(email) {
    return calendarContactsByEmail[meetingEmailKey(email)] || null;
  }

  function attendeeChipLabel(att) {
    var c = contactByAttendeeEmail(att && att.email);
    var name = String((c && c.name) || "").trim();
    if (name) return name;
    return meetingEmailKey(att && att.email) || "";
  }

  function attendeeInitialsFrom(att) {
    var c = contactByAttendeeEmail(att && att.email);
    var name = String((c && c.name) || "").trim();
    if (name) {
      var parts = name.replace(/^@/, "").split(/\s+/).filter(Boolean);
      if (parts.length >= 2) {
        return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
      }
      if (parts[0] && parts[0].length >= 2) return parts[0].slice(0, 2).toUpperCase();
      if (parts[0]) return (parts[0].charAt(0) + parts[0].charAt(0)).toUpperCase();
    }
    var local = String((att && att.email) || "").split("@")[0] || "";
    var tokens = local.split(/[._+\-]+/).filter(function (t) {
      return t && /[a-zA-Zа-яА-ЯёЁ]/.test(t);
    });
    if (tokens.length >= 2) {
      return (tokens[0].charAt(0) + tokens[1].charAt(0)).toUpperCase();
    }
    if (tokens[0] && tokens[0].length >= 2) return tokens[0].slice(0, 2).toUpperCase();
    var letters = local.replace(/[^a-zA-Zа-яА-ЯёЁ]/g, "");
    if (letters.length >= 2) return letters.slice(0, 2).toUpperCase();
    if (letters) return (letters.charAt(0) + letters.charAt(0)).toUpperCase();
    return "?";
  }

  function meetingAttendeeAvatarNode(att) {
    var el = document.createElement("span");
    el.className = "note-member-avatar";
    el.title = attendeeChipLabel(att);
    var img = document.createElement("img");
    img.alt = "";
    el.appendChild(img);
    var fallback = document.createElement("span");
    fallback.className = "note-member-avatar-fallback";
    fallback.textContent = attendeeInitialsFrom(att);
    el.appendChild(fallback);
    var c = contactByAttendeeEmail(att && att.email);
    var tid = (c && c.telegram_user_id) || (att && att.telegram_user_id);
    var uname = String(
      (c && c.telegram_username) || (att && att.telegram_username) || ""
    )
      .replace(/^@/, "")
      .trim();
    applyTelegramAvatar(img, { userId: tid, username: uname });
    return el;
  }

  function appendMeetingAttendeeAvatars(card, ev) {
    var list = ((ev && ev.attendees) || []).filter(function (a) {
      return a && meetingEmailKey(a.email);
    });
    if (!list.length) return;
    var wrap = document.createElement("div");
    wrap.className = "meeting-card-avatars";
    list.slice(0, 4).forEach(function (att) {
      wrap.appendChild(meetingAttendeeAvatarNode(att));
    });
    if (list.length > 4) {
      var more = document.createElement("span");
      more.className = "note-member-avatar note-member-avatar-more";
      more.textContent = "+" + (list.length - 4);
      wrap.appendChild(more);
    }
    card.appendChild(wrap);
  }

  function mergeBusyMs(intervals) {
    var items = (intervals || [])
      .map(function (it) {
        var a = new Date(it.start).getTime();
        var b = new Date(it.end).getTime();
        if (isNaN(a) || isNaN(b) || b <= a) return null;
        return { start: a, end: b };
      })
      .filter(Boolean)
      .sort(function (x, y) {
        return x.start - y.start;
      });
    var out = [];
    items.forEach(function (it) {
      if (out.length && it.start <= out[out.length - 1].end) {
        out[out.length - 1].end = Math.max(out[out.length - 1].end, it.end);
      } else {
        out.push({ start: it.start, end: it.end });
      }
    });
    return out;
  }

  function invertBusyToFree(workStart, workEnd, busy) {
    var free = [];
    var cursor = workStart;
    (busy || []).forEach(function (b) {
      var s = Math.max(b.start, workStart);
      var e = Math.min(b.end, workEnd);
      if (s > cursor) free.push({ start: cursor, end: s });
      if (e > cursor) cursor = Math.max(cursor, e);
    });
    if (cursor < workEnd) free.push({ start: cursor, end: workEnd });
    return free;
  }

  function snapMsTo15(ms) {
    var d = new Date(ms);
    d.setSeconds(0, 0);
    var m = d.getMinutes();
    d.setMinutes(Math.round(m / 15) * 15);
    return d.getTime();
  }

  function meetingDurationMs() {
    var sEl = document.getElementById("m-ev-start");
    var eEl = document.getElementById("m-ev-end");
    var a = sEl ? parseDatetimeLocal(sEl.value) : null;
    var b = eEl ? parseDatetimeLocal(eEl.value) : null;
    if (a && b && b.getTime() > a.getTime()) return b.getTime() - a.getTime();
    return 60 * 60 * 1000;
  }

  function ensureMeetingEndAfterStart(durMs) {
    var startEl = document.getElementById("m-ev-start");
    var endEl = document.getElementById("m-ev-end");
    if (!startEl || !endEl || !startEl.value) return;
    var start = parseDatetimeLocal(startEl.value);
    if (!start) return;
    if (durMs != null && isFinite(durMs) && durMs > 0) {
      endEl.value = isoToDatetimeLocalValue(new Date(start.getTime() + durMs).toISOString());
      syncEventPillsFromHidden("end");
      return;
    }
    var end = parseDatetimeLocal(endEl.value);
    if (end && end.getTime() > start.getTime()) return;
    endEl.value = isoToDatetimeLocalValue(
      new Date(start.getTime() + meetingDurationMs()).toISOString()
    );
    syncEventPillsFromHidden("end");
  }

  function datetimeLocalParts(value) {
    var s = String(value || "");
    var m = s.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return { date: m ? m[1] : "", time: m ? m[2] : "" };
  }

  function joinDatetimeLocal(date, time) {
    if (!date || !time) return "";
    return date + "T" + time;
  }

  function syncHiddenFromEventPills(which) {
    var hidden = document.getElementById("m-ev-" + which);
    var dateEl = document.getElementById("m-ev-" + which + "-date");
    var timeEl = document.getElementById("m-ev-" + which + "-time");
    if (!hidden || !dateEl || !timeEl) return;
    hidden.value = joinDatetimeLocal(dateEl.value, timeEl.value);
    try {
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (_) {}
  }

  function syncEventPillsFromHidden(which) {
    var hidden = document.getElementById("m-ev-" + which);
    var dateEl = document.getElementById("m-ev-" + which + "-date");
    var timeEl = document.getElementById("m-ev-" + which + "-time");
    if (!hidden || !dateEl || !timeEl) return;
    var p = datetimeLocalParts(hidden.value);
    if (p.date) dateEl.value = p.date;
    if (p.time) timeEl.value = p.time;
  }

  function bindEventDateTimePills() {
    ["start", "end"].forEach(function (which) {
      var dateEl = document.getElementById("m-ev-" + which + "-date");
      var timeEl = document.getElementById("m-ev-" + which + "-time");
      function onChange() {
        var dur = which === "start" ? meetingDurationMs() : null;
        syncHiddenFromEventPills(which);
        if (which === "start") ensureMeetingEndAfterStart(dur);
      }
      if (dateEl) {
        dateEl.addEventListener("change", onChange);
        dateEl.addEventListener("input", onChange);
      }
      if (timeEl) {
        timeEl.addEventListener("change", onChange);
        timeEl.addEventListener("input", onChange);
      }
    });
  }

  function applyMeetingSlot(startMs) {
    var dur = meetingDurationMs();
    var startEl = document.getElementById("m-ev-start");
    var endEl = document.getElementById("m-ev-end");
    if (!startEl || !endEl) return;
    startEl.value = isoToDatetimeLocalValue(new Date(startMs).toISOString());
    endEl.value = isoToDatetimeLocalValue(new Date(startMs + dur).toISOString());
    syncEventPillsFromHidden("start");
    syncEventPillsFromHidden("end");
  }

  function paintMeetingAvailability(host, data) {
    if (!host) return;
    host.innerHTML = "";
    var people = (data && data.people) || [];
    if (!people.length) {
      host.classList.add("hidden");
      return;
    }
    host.classList.remove("hidden");
    var workStart = new Date(data.work_start).getTime();
    var workEnd = new Date(data.work_end).getTime();
    if (isNaN(workStart) || isNaN(workEnd) || workEnd <= workStart) {
      host.classList.add("hidden");
      return;
    }
    var span = workEnd - workStart;
    var head = document.createElement("div");
    head.className = "meeting-avail-head";
    head.textContent = "Занятость";
    host.appendChild(head);
    var scroll = document.createElement("div");
    scroll.className = "meeting-avail-scroll";
    var grid = document.createElement("div");
    grid.className = "meeting-avail-grid";
    var hours = document.createElement("div");
    hours.className = "meeting-avail-hours";
    var tick = new Date(workStart);
    tick.setMinutes(0, 0, 0);
    if (tick.getTime() < workStart) tick.setHours(tick.getHours() + 1);
    while (tick.getTime() < workEnd) {
      var cell = document.createElement("span");
      cell.className = "meeting-avail-hour";
      cell.textContent = String(tick.getHours()).padStart(2, "0");
      hours.appendChild(cell);
      tick.setHours(tick.getHours() + 1);
    }
    grid.appendChild(hours);

    function addRow(label, busy, opts) {
      opts = opts || {};
      var row = document.createElement("div");
      row.className = "meeting-avail-row";
      var lab = document.createElement("div");
      lab.className = "meeting-avail-label" + (opts.muted ? " is-muted" : "");
      lab.textContent = label;
      lab.title = label;
      var track = document.createElement("div");
      track.className = "meeting-avail-track" + (opts.joint ? " meeting-avail-track--joint" : "");
      (busy || []).forEach(function (b) {
        var left = ((b.start - workStart) / span) * 100;
        var width = ((b.end - b.start) / span) * 100;
        if (width <= 0) return;
        var block = document.createElement("span");
        block.className = opts.joint ? "meeting-avail-free" : "meeting-avail-busy";
        block.style.left = Math.max(0, left) + "%";
        block.style.width = Math.min(100 - Math.max(0, left), width) + "%";
        track.appendChild(block);
      });
      if (opts.joint) {
        track.addEventListener("click", function (e) {
          var rect = track.getBoundingClientRect();
          if (!rect.width) return;
          var ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
          var t = snapMsTo15(workStart + ratio * span);
          var hit = null;
          (busy || []).forEach(function (b) {
            if (t >= b.start && t < b.end) hit = b;
          });
          if (!hit) return;
          var start = Math.max(hit.start, Math.min(t, hit.end - meetingDurationMs()));
          if (start < hit.start) start = hit.start;
          applyMeetingSlot(start);
        });
      }
      row.appendChild(lab);
      row.appendChild(track);
      grid.appendChild(row);
    }

    var union = [];
    var calendarPeople = 0;
    people.forEach(function (p) {
      var hasCal = !!(p && (p.calendar || ((p.busy || []).length > 0)));
      if (hasCal) {
        calendarPeople += 1;
        union = union.concat(p.busy || []);
      }
    });
    var jointFree = invertBusyToFree(workStart, workEnd, mergeBusyMs(union));
    if (calendarPeople) {
      addRow("Все свободны", jointFree, { joint: true });
    }
    people.forEach(function (p) {
      var label = String((p && p.label) || p.email || "Участник");
      if (p && p.calendar) {
        addRow(label, mergeBusyMs(p.busy || []));
      } else {
        addRow(label + " — нет календаря", [], { muted: true });
      }
    });
    scroll.appendChild(grid);
    host.appendChild(scroll);
    var hint = document.createElement("p");
    hint.className = "meeting-avail-hint muted small";
    hint.textContent = calendarPeople
      ? "Нажмите на зелёный слот, чтобы поставить время"
      : "Слоты появятся, когда у участников будет календарь в Leo";
    host.appendChild(hint);
  }

  function mountMeetingAttendees(host, initial) {
    var items = [];
    var contacts = [];
    if (!host) {
      return {
        getEmails: function () {
          return [];
        },
      };
    }
    var suggestEl = host.querySelector("#m-ev-attendee-suggest");
    var inputEl = host.querySelector("#m-ev-attendee-input");
    var chipsEl = host.querySelector("#m-ev-attendees-chips");
    var availEl = document.getElementById("m-ev-avail");
    var availTimer = null;
    var suggestIndex = 0;
    if (!host || !suggestEl || !inputEl || !chipsEl) {
      return {
        getEmails: function () {
          return [];
        },
      };
    }

    function emails() {
      return items.map(function (it) {
        return it.email;
      });
    }

    function hasEmail(em) {
      var key = meetingEmailKey(em);
      return items.some(function (it) {
        return it.email === key;
      });
    }

    function paintChips() {
      chipsEl.innerHTML = "";
      items.forEach(function (it, idx) {
        var chip = document.createElement("span");
        chip.className = "meeting-attendee-chip";
        if (it.calendar) {
          var dot = document.createElement("span");
          dot.className = "meeting-attendee-chip-cal";
          dot.title = "Календарь подключён";
          chip.appendChild(dot);
        }
        var text = document.createElement("span");
        text.className = "meeting-attendee-chip-text";
        text.textContent = attendeeChipLabel(it);
        chip.appendChild(text);
        var del = document.createElement("button");
        del.type = "button";
        del.className = "meeting-attendee-chip-remove";
        del.setAttribute("aria-label", "Убрать");
        del.textContent = "×";
        del.addEventListener("click", function () {
          items.splice(idx, 1);
          paintChips();
          scheduleAvail();
        });
        chip.appendChild(del);
        chipsEl.appendChild(chip);
      });
      var sum = host.querySelector("#m-ev-attendees-summary");
      if (sum) {
        if (!items.length) sum.textContent = "Нет";
        else if (items.length === 1) sum.textContent = attendeeChipLabel(items[0]);
        else sum.textContent = String(items.length);
      }
    }

    function hideSuggest() {
      suggestEl.classList.add("hidden");
      suggestEl.innerHTML = "";
    }

    function addPerson(person) {
      var em = meetingEmailKey(person && person.email);
      if (!em || !MEETING_EMAIL_RE.test(em) || hasEmail(em)) {
        hideSuggest();
        return false;
      }
      items.push({
        email: em,
        name: String((person && person.name) || "").trim(),
        calendar: !!(person && person.calendar_connected),
        telegram_username: String((person && person.telegram_username) || "")
          .trim()
          .replace(/^@/, ""),
      });
      if (person && person.name && String(person.name).indexOf("@") < 0) {
        calendarContactsByEmail[em] = person;
      }
      paintChips();
      if (inputEl) inputEl.value = "";
      hideSuggest();
      scheduleAvail();
      return true;
    }

    function visibleSuggestions() {
      var q = String((inputEl && inputEl.value) || "").trim().toLowerCase();
      var out = [];
      contacts.forEach(function (c) {
        if (!c || hasEmail(c.email)) return;
        if (q) {
          var hay = [c.name, c.email, c.telegram_username, (c.aliases || []).join(" ")]
            .join(" ")
            .toLowerCase();
          if (hay.indexOf(q) < 0) return;
        }
        out.push(c);
      });
      return out.slice(0, 8);
    }

    function paintSuggest() {
      var q = String((inputEl && inputEl.value) || "").trim();
      var rows = visibleSuggestions();
      suggestEl.innerHTML = "";
      if (!rows.length && !(q && MEETING_EMAIL_RE.test(q) && !hasEmail(q))) {
        hideSuggest();
        return;
      }
      suggestEl.classList.remove("hidden");
      rows.forEach(function (c, i) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          "meeting-attendee-suggest-item" + (i === suggestIndex ? " is-active" : "");
        var name = document.createElement("span");
        name.className = "meeting-attendee-suggest-name";
        name.textContent = c.name || c.email;
        var meta = document.createElement("span");
        meta.className = "meeting-attendee-suggest-meta";
        meta.textContent =
          (c.email || "") + (c.calendar_connected ? " · календарь" : "");
        btn.appendChild(name);
        btn.appendChild(meta);
        btn.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        btn.addEventListener("click", function () {
          addPerson(c);
        });
        suggestEl.appendChild(btn);
      });
      if (q && MEETING_EMAIL_RE.test(q) && !hasEmail(q)) {
        var extra = document.createElement("button");
        extra.type = "button";
        extra.className = "meeting-attendee-suggest-item";
        extra.textContent = "Добавить " + q;
        extra.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        extra.addEventListener("click", function () {
          addPerson({ email: q, name: q, calendar_connected: false });
        });
        suggestEl.appendChild(extra);
      }
    }

    function commitInput() {
      var q = String((inputEl && inputEl.value) || "").trim();
      var rows = visibleSuggestions();
      if (rows[suggestIndex]) {
        addPerson(rows[suggestIndex]);
        return;
      }
      if (q && MEETING_EMAIL_RE.test(q)) {
        addPerson({ email: q, name: q, calendar_connected: false });
      }
    }

    function scheduleAvail() {
      if (availTimer) window.clearTimeout(availTimer);
      availTimer = window.setTimeout(refreshAvail, 250);
    }

    function refreshAvail() {
      var startEl = document.getElementById("m-ev-start");
      var day = String((startEl && startEl.value) || "").slice(0, 10);
      if (!availEl || !day) return;
      apiFetch("/calendar/availability", {
        method: "POST",
        body: JSON.stringify({
          date: day,
          attendees: items.map(function (it) {
            return {
              email: it.email,
              telegram_username: it.telegram_username || "",
            };
          }),
        }),
      })
        .then(function (data) {
          paintMeetingAvailability(availEl, data);
        })
        .catch(function () {
          if (availEl) availEl.classList.add("hidden");
        });
    }

    (initial || []).forEach(function (raw) {
      var em = meetingEmailKey(raw && (raw.email || raw));
      if (!em || !MEETING_EMAIL_RE.test(em) || hasEmail(em)) return;
      items.push({
        email: em,
        name: "",
        calendar: !!(raw && raw.calendar_connected),
        telegram_username: String((raw && raw.telegram_username) || "")
          .trim()
          .replace(/^@/, ""),
      });
    });
    paintChips();

    apiFetch("/contacts", { method: "GET" })
      .then(function (data) {
        contacts = data.items || [];
        setCalendarContacts(contacts);
        items.forEach(function (it) {
          contacts.forEach(function (c) {
            if (meetingEmailKey(c.email) === it.email) {
              it.name = c.name || "";
              it.calendar = !!c.calendar_connected;
              if (!it.telegram_username) {
                it.telegram_username = String(c.telegram_username || "").replace(/^@/, "");
              }
            }
          });
        });
        paintChips();
      })
      .catch(function () {});

    if (inputEl) {
      inputEl.addEventListener("input", function () {
        suggestIndex = 0;
        paintSuggest();
      });
      inputEl.addEventListener("focus", function () {
        suggestIndex = 0;
        paintSuggest();
      });
      inputEl.addEventListener("blur", function () {
        window.setTimeout(hideSuggest, 120);
      });
      inputEl.addEventListener("keydown", function (e) {
        var rows = visibleSuggestions();
        if (e.key === "ArrowDown" && rows.length) {
          e.preventDefault();
          suggestIndex = Math.min(rows.length - 1, suggestIndex + 1);
          paintSuggest();
        } else if (e.key === "ArrowUp" && rows.length) {
          e.preventDefault();
          suggestIndex = Math.max(0, suggestIndex - 1);
          paintSuggest();
        } else if (e.key === "Enter") {
          e.preventDefault();
          e.stopPropagation();
          commitInput();
        } else if (e.key === "Escape") {
          hideSuggest();
        }
      });
    }
    var startEl = document.getElementById("m-ev-start");
    if (startEl) {
      startEl.addEventListener("change", scheduleAvail);
      startEl.addEventListener("input", scheduleAvail);
    }
    refreshAvail();
    return {
      getEmails: emails,
      refreshAvail: refreshAvail,
    };
  }

  function bindReminderWhenPills() {
    var hidden = document.getElementById("m-rem-when");
    var dateEl = document.getElementById("m-rem-when-date");
    var timeEl = document.getElementById("m-rem-when-time");
    function syncHidden() {
      if (!hidden || !dateEl || !timeEl) return;
      hidden.value = joinDatetimeLocal(dateEl.value, timeEl.value);
    }
    function syncPills() {
      if (!hidden || !dateEl || !timeEl) return;
      var p = datetimeLocalParts(hidden.value);
      if (p.date) dateEl.value = p.date;
      if (p.time) timeEl.value = p.time;
    }
    if (dateEl) {
      dateEl.addEventListener("change", syncHidden);
      dateEl.addEventListener("input", syncHidden);
    }
    if (timeEl) {
      timeEl.addEventListener("change", syncHidden);
      timeEl.addEventListener("input", syncHidden);
    }
    syncPills();
  }

  function autosizeReminderTitle(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.max(el.scrollHeight, 52) + "px";
  }

  function mountTaskAssignee(host, initial) {
    var fallback = defaultTaskAssignee();
    var selected = {
      user_id: String((initial && (initial.user_id || initial.assignee_user_id)) || "") || fallback.user_id,
      email: String((initial && (initial.email || initial.assignee_email)) || "") || fallback.email,
      name: String((initial && (initial.name || initial.assignee_name)) || "") || fallback.name,
    };
    if (!host) {
      return {
        getAssignee: function () {
          return selected;
        },
      };
    }
    host.innerHTML = "";
    var chips = document.createElement("div");
    chips.className = "meeting-attendee-chips";
    var add = document.createElement("div");
    add.className = "meeting-attendee-add";
    var input = document.createElement("input");
    input.type = "text";
    input.className = "field-input";
    input.placeholder = "Контакт или имя";
    input.autocomplete = "off";
    var suggest = document.createElement("div");
    suggest.className = "meeting-attendee-suggest hidden";
    suggest.setAttribute("role", "listbox");
    add.appendChild(input);
    add.appendChild(suggest);
    host.appendChild(chips);
    host.appendChild(add);

    function paint() {
      chips.innerHTML = "";
      var chip = document.createElement("span");
      chip.className = "meeting-attendee-chip";
      var text = document.createElement("span");
      text.className = "meeting-attendee-chip-text";
      text.textContent = selected.name || selected.email || "Я";
      chip.appendChild(text);
      chips.appendChild(chip);
      var sum = document.getElementById("m-task-assignee-summary");
      if (sum) sum.textContent = selected.name || selected.email || "Я";
    }

    function pick(person) {
      selected = {
        user_id: String((person && (person.telegram_user_id || person.user_id)) || ""),
        email: String((person && person.email) || ""),
        name: String((person && person.name) || selected.name || ""),
      };
      if (!selected.name) selected.name = selected.email || "Я";
      input.value = "";
      suggest.classList.add("hidden");
      paint();
    }

    function paintSuggest() {
      var q = String(input.value || "").trim().toLowerCase();
      suggest.innerHTML = "";
      var rows = [];
      var me = defaultTaskAssignee();
      if (!q || String(me.name || "").toLowerCase().indexOf(q) >= 0) {
        rows.push({ name: me.name, email: me.email, user_id: me.user_id });
      }
      Object.keys(calendarContactsByEmail).forEach(function (k) {
        var c = calendarContactsByEmail[k];
        if (!c) return;
        var hay = [c.name, c.email, c.telegram_username, (c.aliases || []).join(" ")]
          .join(" ")
          .toLowerCase();
        if (q && hay.indexOf(q) < 0) return;
        rows.push(c);
      });
      rows.slice(0, 8).forEach(function (c) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "meeting-attendee-suggest-item";
        btn.textContent = c.name || c.email || "Я";
        btn.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        btn.addEventListener("click", function () {
          pick(c);
        });
        suggest.appendChild(btn);
      });
      if (q && !rows.length) {
        var custom = document.createElement("button");
        custom.type = "button";
        custom.className = "meeting-attendee-suggest-item";
        custom.textContent = q;
        custom.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        custom.addEventListener("click", function () {
          pick({ name: q, email: "", user_id: "" });
        });
        suggest.appendChild(custom);
      }
      suggest.classList.toggle("hidden", !suggest.childNodes.length);
    }

    input.addEventListener("input", paintSuggest);
    input.addEventListener("focus", paintSuggest);
    refreshCalendarContacts().then(paint);
    paint();
    return {
      getAssignee: function () {
        return selected;
      },
    };
  }

  function taskPayloadFromModal() {
    syncHiddenFromEventPills("start");
    syncHiddenFromEventPills("end");
    var assignee = taskAssigneeApi ? taskAssigneeApi.getAssignee() : defaultTaskAssignee();
    var noteEl = document.getElementById("modal-task-note-id");
    return {
      title: (document.getElementById("m-ev-sum") || {}).value
        ? document.getElementById("m-ev-sum").value.trim()
        : "",
      description: (document.getElementById("m-task-desc") || {}).value || "",
      start: datetimeLocalToIso((document.getElementById("m-ev-start") || {}).value || ""),
      end: datetimeLocalToIso((document.getElementById("m-ev-end") || {}).value || ""),
      checklist: reminderChecklistApi ? reminderChecklistApi.getItems() : [],
      assignee_user_id: assignee.user_id || "",
      assignee_email: assignee.email || "",
      assignee_name: assignee.name || "",
      note_id: noteEl ? String(noteEl.value || "") : "",
    };
  }

  function openTaskModal(ev, opts) {
    clearCalendarGestureState();
    ev = ev || {};
    opts = opts || {};
    var taskId = calendarTaskId(ev);
    var existing = !!taskId;
    document.getElementById("modal-title").textContent = existing ? "Задача" : "Новая задача";
    document.getElementById("modal-kind").value = "task";
    document.getElementById("modal-reminder-id").value = "";
    document.getElementById("modal-event-id").value = taskId;
    document.getElementById("modal-calendar-id").value = ev.calendar_id || "leo-tasks";
    taskModalOnSaved = typeof opts.onSaved === "function" ? opts.onSaved : null;
    taskModalOnDeleted = typeof opts.onDeleted === "function" ? opts.onDeleted : null;
    var st = ev.start || {};
    var en = ev.end || {};
    var sRaw = st.dateTime || ev.start_at || (st.date ? st.date + "T09:00:00" : "");
    var eRaw = en.dateTime || ev.end_at || (en.date ? en.date + "T09:30:00" : "");
    var startLocal = isoToDatetimeLocalValue(sRaw || defaultDatetimeLocalValue(60));
    var endLocal = isoToDatetimeLocalValue(eRaw);
    if (!endLocal) {
      var startDt = parseDatetimeLocal(startLocal);
      endLocal = startDt
        ? isoToDatetimeLocalValue(new Date(startDt.getTime() + 30 * 60000).toISOString())
        : defaultDatetimeLocalValue(90);
    }
    var fields = document.getElementById("modal-fields");
    fields.innerHTML =
      '<div class="event-sheet">' +
      '<input id="m-ev-sum" type="text" class="event-sheet-title" placeholder="Название" autocomplete="off" />' +
      '<textarea id="m-task-desc" class="field-textarea event-sheet-desc" rows="3" placeholder="Описание"></textarea>' +
      '<input type="hidden" id="m-ev-start" />' +
      '<input type="hidden" id="m-ev-end" />' +
      '<input type="hidden" id="modal-task-note-id" />' +
      '<div class="event-sheet-group event-sheet-checklist-group">' +
      '<div id="m-task-checklist-host" class="rem-checklist-host"></div>' +
      "</div>" +
      '<div class="event-sheet-group">' +
      '<div class="event-sheet-row">' +
      '<span class="event-sheet-row-label">Начало</span>' +
      '<span class="event-sheet-pills">' +
      '<input id="m-ev-start-date" type="date" class="event-sheet-pill" />' +
      '<input id="m-ev-start-time" type="time" class="event-sheet-pill" />' +
      "</span></div>" +
      '<div class="event-sheet-row">' +
      '<span class="event-sheet-row-label">Конец</span>' +
      '<span class="event-sheet-pills">' +
      '<input id="m-ev-end-date" type="date" class="event-sheet-pill" />' +
      '<input id="m-ev-end-time" type="time" class="event-sheet-pill" />' +
      "</span></div></div>" +
      '<div id="m-ev-color-host"></div>' +
      '<div class="event-sheet-group meeting-attendees-field">' +
      '<button type="button" class="event-sheet-row event-sheet-row--nav" id="m-task-assignee-toggle" aria-expanded="false">' +
      '<span class="event-sheet-row-label">Исполнитель</span>' +
      '<span class="event-sheet-row-value"><span id="m-task-assignee-summary">Я</span>' +
      eventSheetChevronHtml() +
      "</span></button>" +
      '<div id="m-task-assignee-editor" class="event-sheet-attendees hidden">' +
      '<div id="m-task-assignee-host"></div>' +
      "</div></div></div>";
    document.getElementById("m-ev-sum").value =
      ev.summary || ev.title || (opts.quote ? String(opts.quote) : "") || "";
    document.getElementById("m-task-desc").value = ev.description || "";
    document.getElementById("m-ev-start").value = startLocal;
    document.getElementById("m-ev-end").value = endLocal;
    document.getElementById("modal-task-note-id").value = ev.note_id || currentOpenNoteId() || "";
    syncEventPillsFromHidden("start");
    syncEventPillsFromHidden("end");
    bindEventDateTimePills();
    entryColorPickerApi = mountEntryColorPicker(
      document.getElementById("m-ev-color-host"),
      ev.display_color || ""
    );
    reminderChecklistApi = mountReminderChecklist(
      document.getElementById("m-task-checklist-host"),
      ev.checklist
    );
    taskAssigneeApi = mountTaskAssignee(
      document.getElementById("m-task-assignee-host"),
      ev.assignee || {
        user_id: ev.assignee_user_id,
        email: ev.assignee_email,
        name: ev.assignee_name,
      }
    );
    var assEditor = document.getElementById("m-task-assignee-editor");
    var assToggle = document.getElementById("m-task-assignee-toggle");
    if (assToggle && assEditor) {
      assToggle.addEventListener("click", function () {
        var open = assEditor.classList.contains("hidden");
        assEditor.classList.toggle("hidden", !open);
        assToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    document.getElementById("modal-delete").classList.toggle("hidden", !existing);
    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("modal-overlay").setAttribute("aria-hidden", "false");
    onModalSheetOpen();
    syncAppOverlay();
    var titleEl = document.getElementById("m-ev-sum");
    if (titleEl && !existing) {
      window.setTimeout(function () {
        titleEl.focus();
      }, 0);
    }
  }

  function openTaskFromChip(taskId) {
    var id = String(taskId || "").trim();
    if (!id) return;
    function show(task) {
      if (!task) return;
      var ev = Object.assign({}, task.event || {}, task, {
        id: "task-" + task.id,
        task_id: task.id,
      });
      openTaskModal(ev, {
        onSaved: function (saved) {
          var ed = getActiveNoteRichEditor && getActiveNoteRichEditor();
          if (ed && ed.updateTaskChip && saved) {
            ed.updateTaskChip(
              String(saved.id || task.id),
              saved.chip_label || (saved.event && saved.event.chip_label)
            );
          }
        },
        onDeleted: function () {
          var ed = getActiveNoteRichEditor && getActiveNoteRichEditor();
          if (ed && ed.removeTaskChip) ed.removeTaskChip(String(task.id || id));
        },
      });
    }
    function recreateFromChip() {
      var ed = getActiveNoteRichEditor && getActiveNoteRichEditor();
      var meta = ed && ed.getTaskChipMeta ? ed.getTaskChipMeta(id) : null;
      var label = (meta && meta.label) || "";
      var title = (meta && meta.title) || "";
      var noteId = currentOpenNoteId();
      function bind(task) {
        if (!task) throw new Error("Не удалось восстановить задачу");
        if (ed && ed.remapTaskChip) {
          ed.remapTaskChip(id, task.id, task.chip_label || label);
        }
        loadActual();
        show(task);
      }
      function createFresh() {
        if (!label && !title) {
          alert("Задача не найдена");
          return;
        }
        apiFetch("/calendar/tasks", {
          method: "POST",
          body: JSON.stringify({
            text: label,
            title: title,
            note_id: noteId,
          }),
        })
          .then(function (r) {
            bind(r && r.task);
          })
          .catch(function (err) {
            alert((err && err.message) || "Задача не найдена");
          });
      }
      if (!noteId) {
        createFresh();
        return;
      }
      apiFetch("/calendar/tasks?note_id=" + encodeURIComponent(noteId), { method: "GET" })
        .then(function (r) {
          var tasks = (r && r.tasks) || [];
          var wantTitle = String(title || "")
            .trim()
            .toLowerCase();
          var match = null;
          for (var i = 0; i < tasks.length; i++) {
            var t = tasks[i];
            var tTitle = String((t && t.title) || "")
              .trim()
              .toLowerCase();
            if (wantTitle && tTitle === wantTitle) {
              match = t;
              break;
            }
            var chip = String((t && t.chip_label) || "").trim();
            if (label && chip && chip === label) {
              match = t;
              break;
            }
          }
          if (match) {
            bind(match);
            return;
          }
          createFresh();
        })
        .catch(function () {
          createFresh();
        });
    }
    apiFetch("/calendar/tasks/" + encodeURIComponent(id), { method: "GET" })
      .then(function (r) {
        var task = r && r.task;
        if (!task) {
          recreateFromChip();
          return;
        }
        show(task);
      })
      .catch(function (e) {
        if (e && e.status === 404) {
          recreateFromChip();
          return;
        }
        alert((e && e.message) || "Не удалось открыть задачу");
      });
  }

  function createTaskFromSlash(text, done, fail, title) {
    apiFetch("/calendar/tasks", {
      method: "POST",
      body: JSON.stringify({
        text: String(text || ""),
        title: String(title || "").trim(),
        note_id: currentOpenNoteId(),
      }),
    })
      .then(function (r) {
        var task = r && r.task;
        if (!task) throw new Error("Не удалось создать задачу");
        if (typeof done === "function") done(task.id, task.chip_label || "");
        loadActual();
      })
      .catch(function (e) {
        if (typeof fail === "function") fail(e);
        alert((e && e.message) || "Не удалось создать задачу");
      });
  }

  function deleteTaskFromChip(taskId) {
    var id = String(taskId || "").trim();
    if (!id) return;
    apiFetch("/calendar/tasks/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () {
        loadActual();
      })
      .catch(function () {});
  }

  function openReminderModal(r) {
    r = r || {};
    document.getElementById("modal-title").textContent = "Напоминание";
    document.getElementById("modal-kind").value = "reminder";
    document.getElementById("modal-reminder-id").value = r.id || "";
    document.getElementById("modal-event-id").value = "";
    document.getElementById("modal-calendar-id").value = "";
    const fields = document.getElementById("modal-fields");
    fields.innerHTML =
      '<div class="event-sheet">' +
      '<textarea id="m-rem-task" class="event-sheet-title event-sheet-title--area" rows="1" placeholder="Текст" autocomplete="off"></textarea>' +
      '<input type="hidden" id="m-rem-when" />' +
      '<div class="event-sheet-group">' +
      '<div class="event-sheet-row">' +
      '<span class="event-sheet-row-label">Когда</span>' +
      '<span class="event-sheet-pills">' +
      '<input id="m-rem-when-date" type="date" class="event-sheet-pill" />' +
      '<input id="m-rem-when-time" type="time" class="event-sheet-pill" />' +
      "</span></div></div>" +
      '<div class="event-sheet-group event-sheet-checklist-group">' +
      '<div id="m-rem-checklist-host" class="rem-checklist-host"></div>' +
      "</div></div>";
    var taskEl = document.getElementById("m-rem-task");
    taskEl.value = r.task || "";
    autosizeReminderTitle(taskEl);
    taskEl.addEventListener("input", function () {
      autosizeReminderTitle(taskEl);
    });
    document.getElementById("m-rem-when").value = isoToDatetimeLocalValue(
      r.when_iso || defaultDatetimeLocalValue(60)
    );
    bindReminderWhenPills();
    reminderChecklistApi = mountReminderChecklist(
      document.getElementById("m-rem-checklist-host"),
      r.checklist
    );
    document.getElementById("modal-delete").classList.toggle("hidden", !r.id);
    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("modal-overlay").setAttribute("aria-hidden", "false");
    onModalSheetOpen();
    syncAppOverlay();
  }

  function eventSheetChevronHtml() {
    return (
      '<svg class="event-sheet-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">' +
      '<path d="M6 9l6 6 6-6"/></svg>'
    );
  }

  function openEventModal(ev) {
    clearCalendarGestureState();
    ev = ev || {};
    if (calendarEntryIsTask(ev)) {
      openTaskModal(ev);
      return;
    }
    document.getElementById("modal-title").textContent = ev.id ? "Встреча" : "Новое";
    document.getElementById("modal-kind").value = "event";
    document.getElementById("modal-reminder-id").value = "";
    document.getElementById("modal-event-id").value = ev.id || "";
    document.getElementById("modal-calendar-id").value = ev.calendar_id || "primary";
    const st = ev.start || {};
    const en = ev.end || {};
    const sRaw = st.dateTime || (st.date ? st.date + "T09:00:00" : "");
    const eRaw = en.dateTime || (en.date ? en.date + "T10:00:00" : "");
    const fields = document.getElementById("modal-fields");
    fields.innerHTML =
      '<div class="event-sheet">' +
      '<input id="m-ev-sum" type="text" class="event-sheet-title" placeholder="Название" autocomplete="off" />' +
      '<input type="hidden" id="m-ev-start" />' +
      '<input type="hidden" id="m-ev-end" />' +
      '<div class="event-sheet-group">' +
      '<div class="event-sheet-row">' +
      '<span class="event-sheet-row-label">Начало</span>' +
      '<span class="event-sheet-pills">' +
      '<input id="m-ev-start-date" type="date" class="event-sheet-pill" />' +
      '<input id="m-ev-start-time" type="time" class="event-sheet-pill" />' +
      "</span></div>" +
      '<div class="event-sheet-row">' +
      '<span class="event-sheet-row-label">Конец</span>' +
      '<span class="event-sheet-pills">' +
      '<input id="m-ev-end-date" type="date" class="event-sheet-pill" />' +
      '<input id="m-ev-end-time" type="time" class="event-sheet-pill" />' +
      "</span></div></div>" +
      '<div id="m-ev-color-host"></div>' +
      '<div class="event-sheet-group meeting-attendees-field">' +
      '<button type="button" class="event-sheet-row event-sheet-row--nav" id="m-ev-attendees-toggle" aria-expanded="false">' +
      '<span class="event-sheet-row-label">Участники</span>' +
      '<span class="event-sheet-row-value"><span id="m-ev-attendees-summary">Нет</span>' +
      eventSheetChevronHtml() +
      "</span></button>" +
      '<div id="m-ev-attendees-editor" class="event-sheet-attendees hidden">' +
      '<div id="m-ev-attendees-chips" class="meeting-attendee-chips"></div>' +
      '<div class="meeting-attendee-add">' +
      '<input id="m-ev-attendee-input" type="text" class="field-input" placeholder="Контакт или email" autocomplete="off" />' +
      '<div id="m-ev-attendee-suggest" class="meeting-attendee-suggest hidden" role="listbox"></div>' +
      "</div></div></div>" +
      '<div id="m-ev-avail" class="meeting-avail hidden"></div>' +
      '<div class="event-sheet-group">' +
      '<button type="button" class="event-sheet-row event-sheet-row--nav" id="m-ev-desc-toggle" aria-expanded="false">' +
      '<span class="field-label">Описание</span>' +
      eventSheetChevronHtml() +
      "</button>" +
      '<textarea id="m-ev-desc" class="field-textarea event-sheet-desc hidden" rows="4"></textarea>' +
      "</div>" +
      '<div class="event-sheet-link-row"></div>' +
      "</div>";
    document.getElementById("m-ev-sum").value = meetingDisplayTitle(ev, { blank: true });
    document.getElementById("m-ev-start").value = isoToDatetimeLocalValue(
      sRaw || defaultDatetimeLocalValue(60)
    );
    document.getElementById("m-ev-end").value = isoToDatetimeLocalValue(
      eRaw || defaultDatetimeLocalValue(120)
    );
    syncEventPillsFromHidden("start");
    syncEventPillsFromHidden("end");
    bindEventDateTimePills();
    entryColorPickerApi = mountEntryColorPicker(
      document.getElementById("m-ev-color-host"),
      ev.display_color || ""
    );
    document.getElementById("m-ev-desc").value = calendarDescriptionPlain(
      ev.description || ""
    );
    var attEditor = document.getElementById("m-ev-attendees-editor");
    var attToggle = document.getElementById("m-ev-attendees-toggle");
    if ((ev.attendees || []).length && attEditor && attToggle) {
      attEditor.classList.remove("hidden");
      attToggle.setAttribute("aria-expanded", "true");
    }
    if (attToggle && attEditor) {
      attToggle.addEventListener("click", function () {
        var open = attEditor.classList.contains("hidden");
        attEditor.classList.toggle("hidden", !open);
        attToggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
          var inp = document.getElementById("m-ev-attendee-input");
          if (inp) inp.focus();
        }
      });
    }
    function setEventDescOpen(open, opts) {
      var ta = document.getElementById("m-ev-desc");
      var toggle = document.getElementById("m-ev-desc-toggle");
      if (!ta) return;
      ta.classList.toggle("hidden", !open);
      if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && opts && opts.focus) ta.focus();
    }
    var descToggle = document.getElementById("m-ev-desc-toggle");
    var hasDesc = !!(ev.description && String(ev.description).trim());
    setEventDescOpen(hasDesc);
    if (descToggle) {
      descToggle.addEventListener("click", function () {
        var ta = document.getElementById("m-ev-desc");
        setEventDescOpen(!!(ta && ta.classList.contains("hidden")), { focus: true });
      });
    }
    meetingAttendeesApi = mountMeetingAttendees(
      document.querySelector(".meeting-attendees-field"),
      ev.attendees || []
    );
    function makeLinkActionBtn(label) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rem-checklist-add modal-link-action";
      btn.textContent = label;
      return btn;
    }

    const zoomBtn = makeLinkActionBtn("Zoom");
    zoomBtn.addEventListener("click", async function () {
      const id = document.getElementById("modal-event-id").value;
      if (!id) {
        alert("Сначала сохраните встречу.");
        return;
      }
      const calId = document.getElementById("modal-calendar-id").value || "primary";
      const q =
        calId && calId !== "primary"
          ? "?calendar_id=" + encodeURIComponent(calId)
          : "";
      try {
        const r = await apiFetch(
          "/calendar/events/" + encodeURIComponent(id) + "/zoom-link" + q,
          { method: "POST", body: "{}" }
        );
        const join = (r && r.join_url) || "";
        if (join) {
          const ta = document.getElementById("m-ev-desc");
          var cur = (ta && ta.value) || "";
          var line = "Zoom: " + join;
          ta.value = cur.trim() ? cur.trim() + "\n\n" + line : line;
          setEventDescOpen(true);
        }
        var msg = (r && r.already) ? "Ссылка уже была в календаре." : "Ссылка Zoom добавлена.";
        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
        if (tg && tg.showAlert) {
          tg.showAlert(msg);
        } else {
          alert(msg);
        }
        await loadActual();
      } catch (e) {
        alert(e.message || String(e));
      }
    });
    var linkRow = fields.querySelector(".event-sheet-link-row");
    if (linkRow) linkRow.appendChild(zoomBtn);
    else fields.appendChild(zoomBtn);
    const telemostBtn = makeLinkActionBtn("Телемост");
    telemostBtn.addEventListener("click", async function () {
      const id = document.getElementById("modal-event-id").value;
      if (!id) {
        alert("Сначала сохраните встречу.");
        return;
      }
      const calId = document.getElementById("modal-calendar-id").value || "primary";
      const q =
        calId && calId !== "primary"
          ? "?calendar_id=" + encodeURIComponent(calId)
          : "";
      try {
        const r = await apiFetch(
          "/calendar/events/" + encodeURIComponent(id) + "/telemost-link" + q,
          { method: "POST", body: "{}" }
        );
        const join = (r && r.join_url) || "";
        if (join) {
          const ta = document.getElementById("m-ev-desc");
          var cur = (ta && ta.value) || "";
          var line = "Телемост: " + join;
          ta.value = cur.trim() ? cur.trim() + "\n\n" + line : line;
          setEventDescOpen(true);
        }
        var msg = (r && r.already) ? "Ссылка уже была в календаре." : "Ссылка Телемост добавлена.";
        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
        if (tg && tg.showAlert) {
          tg.showAlert(msg);
        } else {
          alert(msg);
        }
        await loadActual();
      } catch (e) {
        alert(e.message || String(e));
      }
    });
    if (linkRow) linkRow.appendChild(telemostBtn);
    else fields.appendChild(telemostBtn);
    if (meetingNeedsRsvp(ev)) {
      var rsvpHost = document.createElement("div");
      rsvpHost.className = "event-sheet-group event-sheet-rsvp-group";
      var rsvpLab = document.createElement("div");
      rsvpLab.className = "event-sheet-row-label";
      rsvpLab.textContent = "Вы пойдёте?";
      rsvpHost.appendChild(rsvpLab);
      rsvpHost.appendChild(renderMeetingRsvpRow(ev));
      var titleEl = document.getElementById("m-ev-sum");
      if (titleEl && titleEl.parentNode) {
        titleEl.parentNode.insertBefore(rsvpHost, titleEl.nextSibling);
      } else if (fields.firstChild) {
        fields.firstChild.insertBefore(rsvpHost, fields.firstChild.children[1] || null);
      }
    }
    if (ev.meet_url) {
      var joinBtn = document.createElement("button");
      joinBtn.type = "button";
      joinBtn.className = "btn join event-sheet-join";
      joinBtn.textContent = "Присоединиться";
      joinBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        openExternal(ev.meet_url);
      });
      if (linkRow && linkRow.parentNode) {
        linkRow.parentNode.insertBefore(joinBtn, linkRow);
      } else {
        fields.appendChild(joinBtn);
      }
    }
    document.getElementById("modal-delete").classList.toggle("hidden", !ev.id);
    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("modal-overlay").setAttribute("aria-hidden", "false");
    onModalSheetOpen();
    syncAppOverlay();
  }

  var modalSubmitBusy = false;

  async function modalSubmit(ev) {
    if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
    if (modalSubmitBusy) return;
    modalSubmitBusy = true;
    try {
      await modalSubmitInner();
    } catch (e) {
      showCalendarToast((e && e.message) || "Не удалось сохранить");
    } finally {
      modalSubmitBusy = false;
    }
  }

  async function modalSubmitInner() {
    const kind = document.getElementById("modal-kind").value;
    if (kind === "reminder") {
      const id = document.getElementById("modal-reminder-id").value;
      const body = {
        task: document.getElementById("m-rem-task").value.trim(),
        when_iso: datetimeLocalToIso(document.getElementById("m-rem-when").value),
        checklist: reminderChecklistApi
          ? reminderChecklistApi.getItems()
          : [],
      };
      try {
        if (isAppOffline() || (id && String(id).indexOf("tmp_") === 0)) {
          var clientId = id || newTempId("tmp_rem_");
          if (id) {
            applyReminderLocal(function (items) {
              return items.map(function (r) {
                if (String(r.id) !== String(clientId)) return r;
                return Object.assign({}, r, body, { id: clientId });
              });
            });
            enqueueOfflineOp({
              type: "reminder_patch",
              clientId: clientId,
              path: "/reminders/" + encodeURIComponent(clientId),
              method: "PATCH",
              body: body,
            });
          } else {
            var created = Object.assign({ id: clientId, done: false }, body);
            applyReminderLocal(function (items) {
              return [created].concat(items);
            });
            enqueueOfflineOp({
              type: "reminder_create",
              clientId: clientId,
              path: "/reminders",
              method: "POST",
              body: body,
            });
          }
          closeModal();
          showOfflineSavedToast();
          return;
        }
        await apiFetch(id ? "/reminders/" + encodeURIComponent(id) : "/reminders", {
          method: id ? "PATCH" : "POST",
          body: JSON.stringify(body),
        });
        closeModal();
        await loadActual();
      } catch (e) {
        if (isTransientApiError(e)) {
          var cid = id || newTempId("tmp_rem_");
          if (id) {
            applyReminderLocal(function (items) {
              return items.map(function (r) {
                if (String(r.id) !== String(cid)) return r;
                return Object.assign({}, r, body, { id: cid });
              });
            });
            enqueueOfflineOp({
              type: "reminder_patch",
              clientId: cid,
              path: "/reminders/" + encodeURIComponent(cid),
              method: "PATCH",
              body: body,
            });
          } else {
            var createdOff = Object.assign({ id: cid, done: false }, body);
            applyReminderLocal(function (items) {
              return [createdOff].concat(items);
            });
            enqueueOfflineOp({
              type: "reminder_create",
              clientId: cid,
              path: "/reminders",
              method: "POST",
              body: body,
            });
          }
          closeModal();
          showOfflineSavedToast();
          return;
        }
        showCalendarToast(e.message || String(e));
      }
      return;
    }
    if (kind === "task") {
      const id = document.getElementById("modal-event-id").value;
      const body = taskPayloadFromModal();
      var taskColor = entryColorPickerApi ? entryColorPickerApi.getColor() : "";
      try {
        const r = await apiFetch(id ? "/calendar/tasks/" + encodeURIComponent(id) : "/calendar/tasks", {
          method: id ? "PATCH" : "POST",
          body: JSON.stringify(body),
        });
        var saved = r && r.task;
        var colorId = id || (saved && (saved.id || (saved.task_id != null ? saved.task_id : "")));
        if (colorId || taskColor) {
          try {
            await persistEntryDisplayColor(
              {
                id: String(colorId).indexOf("task-") === 0 ? colorId : "task-" + colorId,
                task_id: colorId,
                entry_type: "task",
              },
              taskColor
            );
          } catch (colorErr) {
            showCalendarToast((colorErr && colorErr.message) || "Цвет не сохранён");
          }
        }
        var savedCb = taskModalOnSaved;
        closeModal();
        if (savedCb) savedCb(saved);
        await loadActual();
      } catch (e) {
        showCalendarToast(e.message || String(e));
      }
      return;
    }
    if (kind === "event") {
      const id = document.getElementById("modal-event-id").value;
      const calId = document.getElementById("modal-calendar-id").value || "primary";
      syncHiddenFromEventPills("start");
      syncHiddenFromEventPills("end");
      ensureMeetingEndAfterStart();
      var sumEl = document.getElementById("m-ev-sum");
      var startEl = document.getElementById("m-ev-start");
      var endEl = document.getElementById("m-ev-end");
      var descEl = document.getElementById("m-ev-desc");
      if (!sumEl || !startEl || !endEl) {
        showCalendarToast("Форма встречи не готова — откройте снова");
        return;
      }
      var title = String(sumEl.value || "").trim();
      var startIso = datetimeLocalToIso(startEl.value);
      var endIso = datetimeLocalToIso(endEl.value);
      if (!id && !title) {
        showCalendarToast("Укажите название встречи");
        try {
          sumEl.focus();
        } catch (_) {}
        return;
      }
      if (!startIso) {
        showCalendarToast("Укажите начало встречи");
        return;
      }
      var eventColor = entryColorPickerApi ? entryColorPickerApi.getColor() : "";
      const body = {
        calendar_id: calId,
        title: title,
        start: startIso,
        end: endIso,
        description: descEl ? descEl.value : "",
        attendees: meetingAttendeesApi ? meetingAttendeesApi.getEmails() : [],
      };
      try {
        var savedEv = await apiFetch(id ? "/calendar/events/" + encodeURIComponent(id) : "/calendar/events", {
          method: id ? "PUT" : "POST",
          body: JSON.stringify(body),
        });
        var savedEvent = savedEv && savedEv.event;
        var colorTarget = {
          id: (savedEvent && savedEvent.id) || id,
          calendar_id: (savedEvent && savedEvent.calendar_id) || calId,
        };
        if (colorTarget.id) {
          try {
            await persistEntryDisplayColor(colorTarget, eventColor);
          } catch (colorErr) {
            showCalendarToast((colorErr && colorErr.message) || "Цвет не сохранён");
          }
        }
        closeModal();
        await loadActual();
      } catch (e) {
        showCalendarToast(e.message || String(e));
      }
      return;
    }
    if (kind === "agent") {
      await saveAgentForm();
      return;
    }
    showCalendarToast("Неизвестный тип формы");
  }

  async function modalDelete() {
    const kind = document.getElementById("modal-kind").value;
    if (kind === "reminder") {
      const id = document.getElementById("modal-reminder-id").value;
      if (!confirm("Удалить напоминание?")) return;
      try {
        if (isAppOffline() || String(id).indexOf("tmp_") === 0) {
          applyReminderLocal(function (items) {
            return items.filter(function (r) {
              return String(r.id) !== String(id);
            });
          });
          enqueueOfflineOp({
            type: "reminder_delete",
            clientId: id,
            path: "/reminders/" + encodeURIComponent(id),
            method: "DELETE",
          });
          closeModal();
          showOfflineSavedToast("Удалено офлайн");
          return;
        }
        await apiFetch("/reminders/" + encodeURIComponent(id), { method: "DELETE" });
        closeModal();
        await loadActual();
      } catch (e) {
        if (isTransientApiError(e)) {
          applyReminderLocal(function (items) {
            return items.filter(function (r) {
              return String(r.id) !== String(id);
            });
          });
          enqueueOfflineOp({
            type: "reminder_delete",
            clientId: id,
            path: "/reminders/" + encodeURIComponent(id),
            method: "DELETE",
          });
          closeModal();
          showOfflineSavedToast("Удалено офлайн");
          return;
        }
        alert(e.message || String(e));
      }
      return;
    }
    if (kind === "task") {
      const id = document.getElementById("modal-event-id").value;
      if (!id || !confirm("Удалить задачу?")) return;
      try {
        await apiFetch("/calendar/tasks/" + encodeURIComponent(id), { method: "DELETE" });
        var deletedCb = taskModalOnDeleted;
        closeModal();
        if (deletedCb) deletedCb(id);
        await loadActual();
      } catch (e) {
        alert(e.message || String(e));
      }
      return;
    }
    if (kind === "event") {
      const id = document.getElementById("modal-event-id").value;
      const calId = document.getElementById("modal-calendar-id").value || "primary";
      if (!confirm("Удалить встречу из календаря?")) return;
      try {
        const q = calId && calId !== "primary" ? "?calendar_id=" + encodeURIComponent(calId) : "";
        await apiFetch("/calendar/events/" + encodeURIComponent(id) + q, {
          method: "DELETE",
        });
        closeModal();
        await loadActual();
      } catch (e) {
        alert(e.message || String(e));
      }
      return;
    }
    if (kind === "agent") {
      await deleteAgentForm();
    }
  }

  async function deleteReminderById(id, opts) {
    opts = opts || {};
    if (!id) return;
    if (isAppOffline() || String(id).indexOf("tmp_") === 0) {
      applyReminderLocal(function (items) {
        return items.filter(function (r) {
          return String(r.id) !== String(id);
        });
      });
      enqueueOfflineOp({
        type: "reminder_delete",
        clientId: id,
        path: "/reminders/" + encodeURIComponent(id),
        method: "DELETE",
      });
      if (!opts.silent) showOfflineSavedToast(opts.toast || "Удалено офлайн");
      return { queued: true };
    }
    try {
      await apiFetch("/reminders/" + encodeURIComponent(id), { method: "DELETE" });
      await loadActual();
      return { queued: false };
    } catch (e) {
      if (isTransientApiError(e)) {
        applyReminderLocal(function (items) {
          return items.filter(function (r) {
            return String(r.id) !== String(id);
          });
        });
        enqueueOfflineOp({
          type: "reminder_delete",
          clientId: id,
          path: "/reminders/" + encodeURIComponent(id),
          method: "DELETE",
        });
        if (!opts.silent) showOfflineSavedToast(opts.toast || "Удалено офлайн");
        return { queued: true };
      }
      throw e;
    }
  }

  /* ——— Actual ——— */
  function renderReminderCard(r) {
    const wrap = document.createElement("div");
    wrap.className = "reminder-card";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "meeting-card-edit-btn reminder-card-edit-btn";
    edit.setAttribute("aria-label", "Изменить");
    edit.title = "Изменить";
    edit.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
      '<path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />' +
      "</svg>";
    edit.addEventListener("click", function (e) {
      e.stopPropagation();
      openReminderModal(r);
    });
    wrap.appendChild(edit);

    const row = document.createElement("div");
    row.className = "reminder-row";

    const check = document.createElement("button");
    check.type = "button";
    check.className = "reminder-check";
    check.innerHTML = "";
    check.addEventListener("click", async function (e) {
      e.stopPropagation();
      try {
        await deleteReminderById(r.id, { toast: "Готово" });
        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      } catch (err) {
        alert(err.message || String(err));
      }
    });

    const textCol = document.createElement("div");
    textCol.style.flex = "1";
    textCol.style.minWidth = "0";
    const p = document.createElement("p");
    p.className = "reminder-text";
    p.textContent = r.task || "(без текста)";
    const meta = document.createElement("div");
    meta.className = "reminder-meta";
    const timePill = document.createElement("span");
    timePill.className = "time-pill";
    try {
      const d = new Date(r.when_iso);
      timePill.textContent = isNaN(d.getTime())
        ? (r.when_iso || "").slice(11, 16)
        : d.toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      timePill.textContent = "—";
    }
    const dateMuted = document.createElement("span");
    dateMuted.className = "muted small";
    dateMuted.textContent = new Date(r.when_iso).toLocaleDateString("ru-RU");
    meta.appendChild(timePill);
    meta.appendChild(dateMuted);
    textCol.appendChild(p);
    var checklist = normalizeReminderChecklist(r.checklist);
    if (checklist.length) {
      var cl = document.createElement("ul");
      cl.className = "reminder-card-checklist";
      checklist.forEach(function (it) {
        var li = document.createElement("li");
        li.className =
          "reminder-card-checklist-item" +
          (it.done ? " reminder-card-checklist-item--done" : "");
        li.textContent = (it.done ? "☑ " : "☐ ") + it.text;
        cl.appendChild(li);
      });
      textCol.appendChild(cl);
    }
    textCol.appendChild(meta);

    row.appendChild(check);
    row.appendChild(textCol);
    wrap.appendChild(row);

    return wrapWithSwipeDelete(wrap, async function () {
      await deleteReminderById(r.id, { toast: "Удалено офлайн" });
    });
  }

  function meetingIsDeclined(ev) {
    return String((ev && ev.self_response_status) || "").toLowerCase() === "declined";
  }

  function meetingNeedsRsvp(ev) {
    if (!ev) return false;
    if (ev.needs_rsvp === true) return true;
    if (ev.needs_rsvp === false) return false;
    if (ev.is_organizer === true) return false;
    return String(ev.self_response_status || "").toLowerCase() === "needsaction";
  }

  function meetingDisplayTitle(ev, opts) {
    var s = String((ev && ev.summary) || "")
      .replace(/^\s*приглашение:\s*/i, "")
      .trim();
    if (s) return s;
    return opts && opts.blank ? "" : "(без названия)";
  }

  function renderMeetingRsvpRow(ev) {
    var row = document.createElement("div");
    row.className = "meeting-rsvp";
    var actions = [
      { status: "declined", label: "Не приду", cls: "rsvp-no" },
      { status: "tentative", label: "Возможно", cls: "rsvp-maybe" },
      { status: "accepted", label: "Приду", cls: "rsvp-yes" },
    ];
    actions.forEach(function (act) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "meeting-rsvp-btn " + act.cls;
      btn.textContent = act.label;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        submitMeetingRsvp(ev, act.status, row);
      });
      row.appendChild(btn);
    });
    return row;
  }

  async function submitMeetingRsvp(ev, status, rowEl) {
    if (!ev || !ev.id) {
      window.alert("Не удалось сохранить ответ");
      return;
    }
    if (ev._rsvpBusy) return;
    ev._rsvpBusy = true;
    if (rowEl) {
      rowEl.querySelectorAll("button").forEach(function (b) {
        b.disabled = true;
      });
    }
    try {
      var calId = ev.calendar_id || "primary";
      await apiFetch(
        "/calendar/events/" + encodeURIComponent(ev.id) + "/rsvp",
        {
          method: "POST",
          body: JSON.stringify({ status: status, calendar_id: calId }),
        }
      );
      closeModal();
      ev._rsvpBusy = false;
      await loadActual();
    } catch (err) {
      ev._rsvpBusy = false;
      if (rowEl) {
        rowEl.querySelectorAll("button").forEach(function (b) {
          b.disabled = false;
        });
      }
      window.alert((err && err.message) || "Не удалось сохранить ответ");
    }
  }

  function renderMeetingCard(ev, opts) {
    opts = opts || {};
    var heightPx = Number(opts.heightPx) || 0;
    var short = heightPx > 0 && heightPx < 52;
    var tiny = heightPx > 0 && heightPx < 36;

    const wrap = document.createElement("div");
    wrap.className = "meeting-card-wrap meeting-card-wrap--timeline";
    const card = document.createElement("div");
    card.className = "meeting-card meeting-card--timeline meeting-card--compact";
    if (short) card.classList.add("meeting-card--timeline-short");
    if (tiny) card.classList.add("meeting-card--timeline-tiny");
    if (meetingNeedsRsvp(ev)) {
      card.classList.add("meeting-card--timeline-pending");
      card.title = "Не подтверждена — откройте, чтобы ответить";
    }
    if (calendarEntryIsTask(ev)) card.classList.add("meeting-card--task");
    applyEntryDisplayColor(card, ev);
    if (!opts.skipClick) {
      card.addEventListener("click", function () {
        if (calendarConsumeSuppressClick()) return;
        openCalendarEntry(ev);
      });
    }

    const top = document.createElement("div");
    top.className = "meeting-card-top";
    const topLeft = document.createElement("div");
    topLeft.className = "meeting-card-top-left";
    if (!short) appendMeetingAttendeeAvatars(topLeft, ev);

    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = meetingDisplayTitle(ev);
    topLeft.appendChild(title);
    top.appendChild(topLeft);

    if (ev.meet_url) {
      const join = document.createElement("button");
      join.type = "button";
      join.className = "meeting-card-edit-btn meeting-card-join-btn";
      join.setAttribute("aria-label", "Присоединиться");
      join.title = "Присоединиться";
      join.innerHTML =
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<rect x="2" y="6" width="14" height="12" rx="2"/>' +
        '<path d="M16 10l6-3v10l-6-3z"/>' +
        "</svg>";
      join.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        openExternal(ev.meet_url);
      });
      top.appendChild(join);
    }

    card.appendChild(top);

    if (!tiny) {
      const meta = document.createElement("div");
      meta.className = "day-timeline-event-meta";
      const startT = formatEventTime(ev);
      const endT = formatEventEndTime(ev);
      meta.textContent = startT + (endT ? "–" + endT : "");
      card.appendChild(meta);
    }

    wrap.appendChild(card);
    return wrap;
  }

  function paintActual(remData, calData, calDataNext, rangeData, postedData) {
    const hero = document.getElementById("actual-hero");
    const errP = document.getElementById("posted-tasks-error");
    const errM = document.getElementById("meetings-error");
    const countEl = document.getElementById("posted-tasks-count");

    if (errP) {
      errP.textContent = "";
      setHidden(errP, true);
    }
    if (errM) {
      errM.textContent = "";
      setHidden(errM, true);
    }

    const calDate = (calData && calData.date) || meetingsPageDate || todayIsoLocal();

    const items = (remData && remData.items) || [];
    const active = items.filter(function (x) {
      return !x.done;
    });
    var postedItems = (postedData && postedData.items) || lastPostedTasksItems || [];
    var postedCount =
      postedData && typeof postedData.count === "number"
        ? postedData.count
        : postedItems.length;
    lastPostedTasksItems = postedItems;
    if (countEl) {
      countEl.textContent =
        "Поставлено " +
        postedCount +
        " " +
        pluralRu(postedCount, "задача", "задачи", "задач");
    }
    var meetingsCount = 0;
    if (meetingsViewMode === "week" || meetingsViewMode === "month") {
      meetingsCount = ((rangeData && rangeData.events) || []).filter(function (ev) {
        return !meetingIsDeclined(ev);
      }).length;
    } else {
      const evs = ((calData && calData.events) || []).filter(function (ev) {
        return !meetingIsDeclined(ev);
      });
      const nextEvs = ((calDataNext && calDataNext.events) || []).filter(function (ev) {
        return !meetingIsDeclined(ev);
      });
      meetingsCount = isDesktopLayout() ? evs.length + nextEvs.length : evs.length;
    }

    hero.innerHTML =
      '<div class="hero-inner">' +
      '<p class="hero-kicker">' +
      heroDateLineFor(calDate) +
      "</p>" +
      '<h1 class="hero-title">Добрый день 👋</h1>' +
      '<div class="hero-stats">' +
      '<div class="hero-stat"><p class="hero-stat-label">Поставлено</p><p class="hero-stat-value">' +
      postedCount +
      "</p></div>" +
      '<div class="hero-stat"><p class="hero-stat-label">Встреч</p><p class="hero-stat-value">' +
      meetingsCount +
      "</p></div></div></div>";

    lastRemindersItems = active;
    if (meetingsViewMode === "week") {
      paintMeetingsWeek(rangeData || lastMeetingsRange);
      ensureMeetingsNowTimer();
    } else if (meetingsViewMode === "month") {
      paintMeetingsMonth(rangeData || lastMeetingsRange);
    } else {
      paintMeetingsBoard(calData, calDataNext, active);
    }
  }

  function pluralRu(n, one, few, many) {
    var abs = Math.abs(Number(n) || 0) % 100;
    var n1 = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (n1 === 1) return one;
    if (n1 >= 2 && n1 <= 4) return few;
    return many;
  }

  var lastPostedTasksItems = [];

  function renderPostedTaskCard(task) {
    var wrap = document.createElement("div");
    wrap.className = "reminder-card";
    var ev = (task && task.event) || {};
    var title = String((task && task.title) || ev.summary || "Задача");
    var assignee =
      (task && task.assignee_name) ||
      (ev.assignee && ev.assignee.name) ||
      "";
    var checklist = normalizeReminderChecklist(
      (task && task.checklist) || ev.checklist || []
    );
    var whenLabel =
      (task && task.chip_label) ||
      (ev && ev.chip_label) ||
      "";
    if (!whenLabel && task && task.all_day) whenLabel = "на день";

    function openThisTask() {
      var payload = Object.assign({}, ev, {
        task_id: task.id,
        id: "task-" + task.id,
        summary: title,
        checklist: checklist,
        assignee: ev.assignee || {
          name: assignee,
          user_id: task.assignee_user_id,
          email: task.assignee_email,
        },
        all_day: !!(task.all_day || ev.all_day),
      });
      openTaskModal(payload);
    }

    var edit = document.createElement("button");
    edit.type = "button";
    edit.className = "meeting-card-edit-btn reminder-card-edit-btn";
    edit.setAttribute("aria-label", "Изменить");
    edit.title = "Изменить";
    edit.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
      '<path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />' +
      "</svg>";
    edit.addEventListener("click", function (e) {
      e.stopPropagation();
      openThisTask();
    });
    wrap.appendChild(edit);

    var row = document.createElement("div");
    row.className = "reminder-row";

    var check = document.createElement("button");
    check.type = "button";
    check.className = "reminder-check";
    check.innerHTML = "";
    check.addEventListener("click", async function (e) {
      e.stopPropagation();
      try {
        await apiFetch("/calendar/tasks/" + encodeURIComponent(String(task.id)), {
          method: "PATCH",
          body: JSON.stringify({ done: true }),
        });
        await loadPostedTasksList();
        loadActual();
        var tg = window.Telegram && window.Telegram.WebApp;
        if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      } catch (err) {
        alert(err.message || String(err));
      }
    });

    var textCol = document.createElement("div");
    textCol.style.flex = "1";
    textCol.style.minWidth = "0";
    var p = document.createElement("p");
    p.className = "reminder-text";
    p.textContent = title;
    textCol.appendChild(p);
    if (checklist.length) {
      var cl = document.createElement("ul");
      cl.className = "reminder-card-checklist";
      checklist.forEach(function (it) {
        var li = document.createElement("li");
        li.className =
          "reminder-card-checklist-item" +
          (it.done ? " reminder-card-checklist-item--done" : "");
        li.textContent = (it.done ? "☑ " : "☐ ") + it.text;
        cl.appendChild(li);
      });
      textCol.appendChild(cl);
    }
    var meta = document.createElement("div");
    meta.className = "reminder-meta";
    if (assignee) {
      var who = document.createElement("span");
      who.className = "time-pill";
      who.textContent = assignee;
      meta.appendChild(who);
    }
    if (whenLabel) {
      var dateMuted = document.createElement("span");
      dateMuted.className = "muted small";
      dateMuted.textContent = whenLabel;
      meta.appendChild(dateMuted);
    }
    textCol.appendChild(meta);
    row.appendChild(check);
    row.appendChild(textCol);
    wrap.appendChild(row);

    wrap.addEventListener("click", openThisTask);

    return wrapWithSwipeDelete(wrap, async function () {
      await apiFetch("/calendar/tasks/" + encodeURIComponent(String(task.id)), {
        method: "DELETE",
      });
      await loadPostedTasksList();
      loadActual();
    });
  }

  async function loadPostedTasksList() {
    var listEl = document.getElementById("posted-tasks-list");
    var emptyEl = document.getElementById("posted-tasks-empty");
    if (!listEl) return;
    try {
      var data = await apiFetch("/calendar/tasks/posted", { method: "GET" });
      var items = (data && data.items) || [];
      lastPostedTasksItems = items;
      listEl.innerHTML = "";
      items.forEach(function (t) {
        listEl.appendChild(renderPostedTaskCard(t));
      });
      setHidden(emptyEl, items.length > 0);
      var countEl = document.getElementById("posted-tasks-count");
      if (countEl) {
        countEl.textContent =
          "Поставлено " +
          items.length +
          " " +
          pluralRu(items.length, "задача", "задачи", "задач");
      }
    } catch (err) {
      if (emptyEl) {
        emptyEl.textContent = (err && err.message) || String(err);
        setHidden(emptyEl, false);
      }
    }
  }

  function setPostedTasksScreen(open) {
    var panel = document.getElementById("panel-actual");
    var sub = document.getElementById("actual-posted-tasks");
    if (!panel || !sub) return;
    panel.classList.toggle("panel-actual--posted", !!open);
    setHidden(sub, !open);
    if (open) loadPostedTasksList();
  }

  function isPostedTasksOpen() {
    var sub = document.getElementById("actual-posted-tasks");
    return !!(sub && !sub.classList.contains("hidden"));
  }

  async function loadActual() {
    const errP = document.getElementById("posted-tasks-error");
    const errM = document.getElementById("meetings-error");
    var dual = isDesktopLayout();
    var leftIso = meetingsPageDate || todayIsoLocal();
    var loadGen = ++meetingsLoadGen;
    var range = meetingsVisibleRange(leftIso);
    var cacheKind = actualCacheKind();
    var cached = readMiniappCache(cacheKind);
    if (cached) {
      paintActual(
        cached.remData || { items: [] },
        cached.calData || { events: [] },
        cached.calDataNext || null,
        cached.rangeData || null,
        cached.postedData || null
      );
    }

    var remData = { items: [] };
    var postedData = { items: [], count: 0 };
    var calData = { events: [], connected: false };
    var calDataNext = null;
    var rangeData = null;
    var fetches = [
      apiFetch("/reminders", { method: "GET" }),
      apiFetch("/calendar/tasks/posted", { method: "GET" }),
    ];
    var calFetchIndex = 2;
    if (meetingsViewMode === "week" || meetingsViewMode === "month") {
      fetches.push(fetchCalendarRange(range.from, range.to));
    } else {
      fetches.push(fetchCalendarDay(meetingsPageDate || leftIso));
      if (dual) fetches.push(fetchCalendarDay(addDaysIso(leftIso, 1)));
    }
    var contactP = refreshCalendarContacts();
    var results = await Promise.allSettled(fetches);
    await contactP;
    if (loadGen !== meetingsLoadGen) return;

    var remOk = results[0].status === "fulfilled";
    var postedOk = results[1].status === "fulfilled";
    var calOk = results[calFetchIndex].status === "fulfilled";
    var nextOk = !!(results[calFetchIndex + 1] && results[calFetchIndex + 1].status === "fulfilled");

    if (remOk) {
      remData = results[0].value;
    } else if (cached && cached.remData) {
      remData = cached.remData;
    }

    if (postedOk) {
      postedData = results[1].value || postedData;
      if (errP) {
        errP.textContent = "";
        setHidden(errP, true);
      }
    } else if (cached && cached.postedData) {
      postedData = cached.postedData;
    } else if (errP) {
      var pErr = results[1].reason;
      errP.textContent = (pErr && pErr.message) || String(pErr || "Ошибка");
      setHidden(errP, false);
    }

    if (calOk) {
      if (meetingsViewMode === "week" || meetingsViewMode === "month") {
        rangeData = results[calFetchIndex].value;
        lastMeetingsRange = rangeData;
        if (errM) {
          errM.textContent = "";
          setHidden(errM, true);
        }
      } else {
        calData = results[calFetchIndex].value;
        if (calData && calData.date) {
          meetingsPageDate = calData.date;
        }
        if (errM) {
          errM.textContent = "";
          setHidden(errM, true);
        }
      }
    } else if (cached) {
      if (meetingsViewMode === "week" || meetingsViewMode === "month") {
        rangeData = cached.rangeData || lastMeetingsRange;
      } else {
        calData = cached.calData || lastMeetingsLeft || calData;
        if (dual) {
          calDataNext = cached.calDataNext || lastMeetingsRight || null;
        }
      }
    } else if (errM) {
      var calErr = results[calFetchIndex].reason;
      errM.textContent = (calErr && calErr.message) || String(calErr || "Ошибка");
      setHidden(errM, false);
    }

    if (meetingsViewMode === "day" && dual && results[calFetchIndex + 1]) {
      if (nextOk) {
        calDataNext = results[calFetchIndex + 1].value;
      } else if (cached && cached.calDataNext) {
        calDataNext = cached.calDataNext;
      } else if (!calDataNext) {
        calDataNext = {
          events: [],
          date: addDaysIso((calData && calData.date) || leftIso, 1),
        };
      }
    }

    if (loadGen !== meetingsLoadGen) return;
    paintActual(remData, calData, calDataNext, rangeData, postedData);
    writeMiniappCache(cacheKind, {
      remData: remOk ? remData : (cached && cached.remData) || remData,
      postedData: postedOk ? postedData : (cached && cached.postedData) || postedData,
      calData:
        meetingsViewMode === "day" && calOk
          ? calData
          : (cached && cached.calData) || calData,
      calDataNext:
        meetingsViewMode === "day" && (nextOk || !dual)
          ? calDataNext
          : (cached && cached.calDataNext) || calDataNext,
      rangeData:
        (meetingsViewMode === "week" || meetingsViewMode === "month") && calOk
          ? rangeData
          : (cached && cached.rangeData) || rangeData,
    });
  }

  /* ——— Заметки и нативная кнопка «Назад» (Telegram) ——— */
  function useTelegramNativeBack() {
    // telegram-web-app.js в обычном браузере тоже создаёт BackButton stub —
    // нативный back только внутри клиента Telegram.
    var tg = window.Telegram && window.Telegram.WebApp;
    return !!(
      isInsideTelegramClient() &&
      tg &&
      tg.BackButton &&
      typeof tg.BackButton.show === "function" &&
      typeof tg.BackButton.hide === "function"
    );
  }

  function isTelegramFullscreenActive() {
    var tg = window.Telegram && window.Telegram.WebApp;
    return !!(tg && tg.isFullscreen === true);
  }

  function syncTelegramFullscreenClass() {
    document.documentElement.classList.toggle(
      "tg-miniapp-fullscreen",
      isTelegramFullscreenActive()
    );
  }

  function ensureTelegramFullscreen() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;
    try {
      tg.expand();
    } catch (_) {}
    // requestFullscreen на iOS Telegram WebView часто даёт белый/пустой экран.
    var platform = String(tg.platform || "").toLowerCase();
    var skipFullscreen = platform === "ios" || isIOSDevice();
    if (!skipFullscreen && typeof tg.requestFullscreen === "function") {
      try {
        if (!tg.isFullscreen) tg.requestFullscreen();
      } catch (_) {}
    }
    syncTelegramFullscreenClass();
  }

  function bindTelegramViewportOnce(tg) {
    if (!tg || !tg.onEvent || tg._leoViewportBound) return;
    tg._leoViewportBound = true;
    try {
      tg.onEvent("viewportChanged", applyTelegramSafeAreaInsets);
      tg.onEvent("contentSafeAreaInsetChanged", applyTelegramSafeAreaInsets);
      tg.onEvent("safeAreaChanged", applyTelegramSafeAreaInsets);
      tg.onEvent("fullscreenChanged", function () {
        applyTelegramSafeAreaInsets();
        syncTelegramNativeBack();
      });
    } catch (_) {}

    // Extra fallback: on some Android devices, safe-area values can be 0 while
    // the visible viewport is still reduced by system navigation.
    if (window.visualViewport && !tg._leoVvBound) {
      tg._leoVvBound = true;
      try {
        window.visualViewport.addEventListener("resize", applyTelegramSafeAreaInsets);
        window.visualViewport.addEventListener("scroll", applyTelegramSafeAreaInsets);
      } catch (_) {}
    }
  }

  function isAppSheetModalOpen() {
    var ov = document.getElementById("modal-overlay");
    return !!(ov && !ov.classList.contains("hidden"));
  }

  function canPerformAppBack() {
    if (isAppSheetModalOpen()) return true;
    if (isPostedTasksOpen()) return true;
    if (isNoteDiscussionOpen()) return true;
    if (isNoteEditorModalOpen()) return true;
    var detail = document.getElementById("notes-detail");
    if (detail && !detail.classList.contains("hidden")) return true;
    var digestDetail = document.getElementById("digest-detail");
    if (digestDetail && !digestDetail.classList.contains("hidden")) return true;
    var profileIds = [
      "profile-payment",
      "profile-expenses",
      "profile-booking",
      "profile-contacts",
      "profile-calendars",
      "profile-zoom",
      "profile-telemost",
      "profile-yandex-disk",
      "profile-bitrix",
      "profile-knowledge-base",
      "profile-agents",
      "profile-chats",
    ];
    for (var i = 0; i < profileIds.length; i++) {
      var el = document.getElementById(profileIds[i]);
      if (el && !el.classList.contains("hidden")) return true;
    }
    return false;
  }

  /** Same stack as Telegram BackButton / in-app «← Назад». Never switches main tabs. */
  function performAppBack() {
    if (isAppSheetModalOpen()) {
      closeModal();
      return true;
    }
    if (isPostedTasksOpen()) {
      setPostedTasksScreen(false);
      return true;
    }
    if (isNoteDiscussionOpen() && !isDesktopLayout()) {
      closeNoteDiscussion();
      return true;
    }
    if (isNoteEditorModalOpen()) {
      handleNoteEditorModalClose();
      return true;
    }
    var detail = document.getElementById("notes-detail");
    if (detail && !detail.classList.contains("hidden")) {
      handleNotesDetailBack();
      return true;
    }
    var digestDetail = document.getElementById("digest-detail");
    if (digestDetail && !digestDetail.classList.contains("hidden")) {
      closeDigestDetail();
      return true;
    }
    var pay = document.getElementById("profile-payment");
    var exp = document.getElementById("profile-expenses");
    var booking = document.getElementById("profile-booking");
    var contacts = document.getElementById("profile-contacts");
    var calendars = document.getElementById("profile-calendars");
    var zoom = document.getElementById("profile-zoom");
    var telemost = document.getElementById("profile-telemost");
    var yandexDisk = document.getElementById("profile-yandex-disk");
    var bitrix = document.getElementById("profile-bitrix");
    var knowledgeBase = document.getElementById("profile-knowledge-base");
    var agents = document.getElementById("profile-agents");
    if (pay && !pay.classList.contains("hidden")) {
      profileScreen("main");
      return true;
    }
    if (zoom && !zoom.classList.contains("hidden")) {
      stopZoomStatusPoll();
      profileScreen("main");
      loadIntegrations();
      return true;
    }
    if (telemost && !telemost.classList.contains("hidden")) {
      profileScreen("main");
      loadIntegrations();
      return true;
    }
    if (yandexDisk && !yandexDisk.classList.contains("hidden")) {
      profileScreen("main");
      loadIntegrations();
      return true;
    }
    if (bitrix && !bitrix.classList.contains("hidden")) {
      profileScreen("main");
      loadIntegrations();
      return true;
    }
    if (knowledgeBase && !knowledgeBase.classList.contains("hidden")) {
      profileScreen("main");
      refreshKnowledgeBaseHint();
      return true;
    }
    if (agents && !agents.classList.contains("hidden")) {
      profileScreen("main");
      return true;
    }
    var chats = document.getElementById("profile-chats");
    if (chats && !chats.classList.contains("hidden")) {
      profileScreen("main");
      return true;
    }
    if (contacts && !contacts.classList.contains("hidden")) {
      profileScreen("main");
      return true;
    }
    if (calendars && !calendars.classList.contains("hidden")) {
      profileScreen("main");
      return true;
    }
    if (booking && !booking.classList.contains("hidden")) {
      profileScreen("main");
      loadIntegrations();
      return true;
    }
    if (exp && !exp.classList.contains("hidden")) {
      profileScreen("main");
      return true;
    }
    return false;
  }

  function bindTelegramMiniappBackOnce() {
    return ensureTelegramMiniappBackBound();
  }

  /** Stable handler so offClick/onClick re-bind does not stack duplicates. */
  var telegramAppBackHandler = null;
  var telegramBackBindRetries = 0;

  function getTelegramAppBackHandler() {
    if (!telegramAppBackHandler) {
      telegramAppBackHandler = function () {
        performAppBack();
      };
    }
    return telegramAppBackHandler;
  }

  /**
   * Bind Telegram BackButton → performAppBack.
   * Must NOT set the bound flag before Telegram.WebApp exists (async script race).
   */
  function ensureTelegramMiniappBackBound() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.BackButton || typeof tg.BackButton.onClick !== "function") {
      return false;
    }
    var handler = getTelegramAppBackHandler();
    try {
      if (typeof tg.BackButton.offClick === "function") {
        tg.BackButton.offClick(handler);
      }
    } catch (_) {}
    try {
      tg.BackButton.onClick(handler);
      notesTelegramBackHandlerBound = true;
      return true;
    } catch (_) {
      return false;
    }
  }

  function scheduleTelegramMiniappBackBindRetries() {
    if (notesTelegramBackHandlerBound) return;
    if (!looksLikeTelegramWebView() && !isInsideTelegramClient()) return;
    if (telegramBackBindRetries >= 40) return;
    telegramBackBindRetries += 1;
    if (ensureTelegramMiniappBackBound()) return;
    setTimeout(scheduleTelegramMiniappBackBindRetries, 100);
  }

  /*
   * PWA/iOS system edge-swipe uses History. We keep at most one leoAppBack entry while a
   * nested screen is open. Buttons/Telegram/custom swipe call performAppBack() and then
   * sync clears/re-arms via replaceState (no history.back races, no quiet-popstate bugs).
   */
  var appBackHistoryArmed = false;

  function shouldUseHistoryAppBack() {
    return !useTelegramNativeBack();
  }

  function ensureAppBackHistory() {
    if (!shouldUseHistoryAppBack()) {
      appBackHistoryArmed = false;
      return;
    }
    if (!canPerformAppBack()) {
      if (history.state && history.state.leoAppBack) {
        try {
          history.replaceState(null, "");
        } catch (_) {}
      }
      appBackHistoryArmed = false;
      return;
    }
    if (history.state && history.state.leoAppBack) {
      appBackHistoryArmed = true;
      return;
    }
    try {
      history.pushState({ leoAppBack: true }, "");
      appBackHistoryArmed = true;
    } catch (_) {}
  }

  function bindAppBackPopstateOnce() {
    if (window._leoAppBackPopBound) return;
    window._leoAppBackPopBound = true;
    window.addEventListener("popstate", function () {
      if (!shouldUseHistoryAppBack()) return;
      appBackHistoryArmed = !!(history.state && history.state.leoAppBack);
      if (!canPerformAppBack()) return;
      performAppBack();
      setTimeout(function () {
        ensureAppBackHistory();
      }, 0);
    });
  }

  var EDGE_SWIPE_ZONE_PX = 28;
  var EDGE_SWIPE_LOCK_PX = 10;
  var EDGE_SWIPE_THRESHOLD_PX = 56;
  var EDGE_SWIPE_VELOCITY_PX_MS = 0.35;

  function getEdgeSwipeLeftInset() {
    try {
      var raw = getComputedStyle(document.documentElement)
        .getPropertyValue("--tg-safe-left")
        .trim();
      var n = parseFloat(raw);
      return isFinite(n) && n > 0 ? n : 0;
    } catch (_) {
      return 0;
    }
  }

  function isPanelsStageAnimating() {
    var stage = document.querySelector("main.main-scroll.panels-stage");
    return !!(stage && stage.classList.contains("panels-stage--animating"));
  }

  function bindEdgeSwipeBackOnce() {
    if (edgeSwipeBackBound) return;
    edgeSwipeBackBound = true;
    bindAppBackPopstateOnce();

    var tracking = false;
    var startX = 0;
    var startY = 0;
    var startT = 0;
    var locked = null;
    var lastX = 0;
    var pointerId = null;

    function reset() {
      tracking = false;
      locked = null;
      pointerId = null;
    }

    function pointX(e) {
      if (e.touches && e.touches[0]) return e.touches[0].clientX;
      if (e.changedTouches && e.changedTouches[0]) return e.changedTouches[0].clientX;
      return e.clientX;
    }

    function pointY(e) {
      if (e.touches && e.touches[0]) return e.touches[0].clientY;
      if (e.changedTouches && e.changedTouches[0]) return e.changedTouches[0].clientY;
      return e.clientY;
    }

    function edgeLimit() {
      return EDGE_SWIPE_ZONE_PX + getEdgeSwipeLeftInset();
    }

    function onStart(e) {
      if (tracking) return;
      if (e.pointerType === "mouse" && e.button != null && e.button !== 0) return;
      if (!canPerformAppBack() || isPanelsStageAnimating()) return;
      var target = e.target;
      if (
        target &&
        target.closest &&
        target.closest(
          "button, a, input, textarea, select, [role='button'], #note-editor-web-back, #notes-detail-back"
        )
      ) {
        return;
      }
      var x = pointX(e);
      if (x > edgeLimit()) return;
      tracking = true;
      locked = null;
      pointerId = e.pointerId != null ? e.pointerId : null;
      startX = x;
      startY = pointY(e);
      lastX = x;
      startT = Date.now();
    }

    function onMove(e) {
      if (!tracking) return;
      if (pointerId != null && e.pointerId != null && e.pointerId !== pointerId) return;
      lastX = pointX(e);
      var dx = lastX - startX;
      var dy = pointY(e) - startY;
      if (!locked) {
        if (Math.abs(dx) > EDGE_SWIPE_LOCK_PX || Math.abs(dy) > EDGE_SWIPE_LOCK_PX) {
          locked = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
          if (locked === "y" || dx < 0) {
            reset();
            return;
          }
        } else {
          return;
        }
      }
      if (locked !== "x") {
        reset();
        return;
      }
      if (e.cancelable) e.preventDefault();
    }

    function onEnd(e) {
      if (!tracking) return;
      if (pointerId != null && e.pointerId != null && e.pointerId !== pointerId) return;
      var x =
        e.type.indexOf("touch") === 0
          ? pointX(e)
          : e.clientX != null
            ? e.clientX
            : lastX;
      var dx = x - startX;
      var dt = Math.max(1, Date.now() - startT);
      var velocity = dx / dt;
      var shouldBack =
        locked === "x" &&
        dx > 0 &&
        (dx >= EDGE_SWIPE_THRESHOLD_PX || velocity >= EDGE_SWIPE_VELOCITY_PX_MS);
      var ok = shouldBack && canPerformAppBack() && !isPanelsStageAnimating();
      reset();
      if (ok) performAppBack();
    }

    // Document listeners — no overlay, so in-app «← Назад» stays clickable.
    document.addEventListener("touchstart", onStart, { passive: true, capture: true });
    document.addEventListener("touchmove", onMove, { passive: false, capture: true });
    document.addEventListener("touchend", onEnd, { passive: true, capture: true });
    document.addEventListener("touchcancel", onEnd, { passive: true, capture: true });
    document.addEventListener("pointerdown", onStart, { capture: true });
    document.addEventListener("pointermove", onMove, { capture: true });
    document.addEventListener("pointerup", onEnd, { capture: true });
    document.addEventListener("pointercancel", onEnd, { capture: true });

    ensureAppBackHistory();
  }

  function isStandaloneDisplay() {
    try {
      return (
        (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
        (window.matchMedia && window.matchMedia("(display-mode: fullscreen)").matches) ||
        window.navigator.standalone === true
      );
    } catch (_) {
      return false;
    }
  }

  function applyTelegramSafeAreaInsets() {
    var tg = window.Telegram && window.Telegram.WebApp;
    var root = document.documentElement;
    var standalone = isStandaloneDisplay();
    root.classList.toggle("app-standalone", standalone);
    if (standalone) {
      root.style.setProperty("--tg-android-nav-inset", "0px");
      root.classList.remove("android-legacy-nav");
    }
    if (!tg) {
      syncTelegramFullscreenClass();
      return;
    }
    try {
      var sa = tg.safeAreaInset || {};
      var csa = tg.contentSafeAreaInset || {};
      var top = sa.top || 0;
      var bottom = sa.bottom || 0;
      var left = sa.left || 0;
      var right = sa.right || 0;
      var cTop = csa.top || 0;
      var cBottom = csa.bottom || 0;
      var cLeft = csa.left || 0;
      var cRight = csa.right || 0;
      root.style.setProperty("--tg-safe-top", top + "px");
      root.style.setProperty("--tg-safe-bottom", bottom + "px");
      root.style.setProperty("--tg-safe-left", left + "px");
      root.style.setProperty("--tg-safe-right", right + "px");
      root.style.setProperty("--tg-content-safe-top", cTop + "px");
      root.style.setProperty("--tg-content-safe-bottom", cBottom + "px");
      root.style.setProperty("--tg-content-safe-left", cLeft + "px");
      root.style.setProperty("--tg-content-safe-right", cRight + "px");
      root.style.setProperty("--tg-safe-area-inset-top", top + "px");
      root.style.setProperty("--tg-safe-area-inset-bottom", bottom + "px");
      root.style.setProperty("--tg-safe-area-inset-left", left + "px");
      root.style.setProperty("--tg-safe-area-inset-right", right + "px");
      root.style.setProperty("--tg-content-safe-area-inset-top", cTop + "px");
      root.style.setProperty("--tg-content-safe-area-inset-bottom", cBottom + "px");
      root.style.setProperty("--tg-content-safe-area-inset-left", cLeft + "px");
      root.style.setProperty("--tg-content-safe-area-inset-right", cRight + "px");

      // Android can overlay system navigation without safe-area insets.
      // Telegram exposes viewportStableHeight/viewportHeight in such cases.
      var vh = typeof tg.viewportHeight === "number" ? tg.viewportHeight : 0;
      var vsh = typeof tg.viewportStableHeight === "number" ? tg.viewportStableHeight : 0;
      var overlay = 0;
      if (vh > 0 && vsh > 0) overlay = Math.max(0, vh - vsh);
      // Standalone PWA already has env(safe-area); skip webview overlay heuristics.
      if (standalone) overlay = 0;
      root.style.setProperty("--tg-viewport-bottom-overlay", overlay + "px");

      // Final fallback: VisualViewport delta (works in many Android WebViews)
      var vv = window.visualViewport;
      var vvOverlay = 0;
      if (!standalone && vv && typeof vv.height === "number") {
        vvOverlay = Math.max(0, window.innerHeight - vv.height - (vv.offsetTop || 0));
      }
      root.style.setProperty("--vv-bottom-overlay", Math.round(vvOverlay) + "px");

      // Android (3-button navigation) often reports all insets as 0 while system UI
      // still overlays the bottom of the webview. Add a conservative fallback —
      // but never in installed PWA, where env(safe-area-inset-bottom) is reliable.
      var plat = String(tg.platform || "").toLowerCase();
      var ua = (navigator && navigator.userAgent) || "";
      var isAndroid =
        plat.indexOf("android") === 0 || /android/i.test(ua || "");
      var inferredInset =
        !standalone &&
        isAndroid &&
        bottom <= 0 &&
        cBottom <= 0 &&
        overlay <= 0 &&
        vvOverlay <= 0
          ? 88
          : 0;
      root.style.setProperty("--tg-android-nav-inset", inferredInset + "px");
      root.classList.toggle("android-legacy-nav", inferredInset > 0);
      root.classList.toggle("tg-android", isAndroid);
    } catch (_) {}
    syncTelegramFullscreenClass();
  }

  function syncTelegramNativeBack() {
    var tg = window.Telegram && window.Telegram.WebApp;
    var native = useTelegramNativeBack();
    var detail = document.getElementById("notes-detail");
    var pay = document.getElementById("profile-payment");
    var exp = document.getElementById("profile-expenses");
    var booking = document.getElementById("profile-booking");
    var contacts = document.getElementById("profile-contacts");
    var calendars = document.getElementById("profile-calendars");
    var zoom = document.getElementById("profile-zoom");
    var telemost = document.getElementById("profile-telemost");
    var yandexDisk = document.getElementById("profile-yandex-disk");
    var bitrix = document.getElementById("profile-bitrix");
    var knowledgeBase = document.getElementById("profile-knowledge-base");
    var agents = document.getElementById("profile-agents");
    var panelNotes = document.getElementById("panel-notes");
    var panelKnowledge = document.getElementById("panel-knowledge");
    var panelProfile = document.getElementById("panel-profile");
    var notesPanelVisible = panelNotes && !panelNotes.classList.contains("hidden");
    var knowledgePanelVisible =
      panelKnowledge && !panelKnowledge.classList.contains("hidden");
    var profilePanelVisible = panelProfile && !panelProfile.classList.contains("hidden");
    var notesOpen = detail && !detail.classList.contains("hidden") && notesPanelVisible;
    var panelDigest = document.getElementById("panel-digest");
    var digestPanelVisible = panelDigest && !panelDigest.classList.contains("hidden");
    var noteEditorOpen =
      isNoteEditorModalOpen() &&
      (notesPanelVisible || knowledgePanelVisible || digestPanelVisible);
    var payOpen = pay && !pay.classList.contains("hidden") && profilePanelVisible;
    var expOpen = exp && !exp.classList.contains("hidden") && profilePanelVisible;
    var bookingOpen =
      booking && !booking.classList.contains("hidden") && profilePanelVisible;
    var contactsOpen =
      contacts && !contacts.classList.contains("hidden") && profilePanelVisible;
    var calendarsOpen =
      calendars && !calendars.classList.contains("hidden") && profilePanelVisible;
    var zoomOpen =
      zoom && !zoom.classList.contains("hidden") && profilePanelVisible;
    var telemostOpen =
      telemost && !telemost.classList.contains("hidden") && profilePanelVisible;
    var yandexDiskOpen =
      yandexDisk && !yandexDisk.classList.contains("hidden") && profilePanelVisible;
    var bitrixOpen =
      bitrix && !bitrix.classList.contains("hidden") && profilePanelVisible;
    var kbaseOpen =
      knowledgeBase && !knowledgeBase.classList.contains("hidden") && profilePanelVisible;
    var agentsOpen =
      agents && !agents.classList.contains("hidden") && profilePanelVisible;

    if (detail) detail.classList.remove("notes-detail--native-back");
    if (pay) pay.classList.remove("profile-subscreen--native-back");
    if (exp) exp.classList.remove("profile-subscreen--native-back");
    if (booking) booking.classList.remove("profile-subscreen--native-back");
    if (contacts) contacts.classList.remove("profile-subscreen--native-back");
    if (calendars) calendars.classList.remove("profile-subscreen--native-back");
    if (zoom) zoom.classList.remove("profile-subscreen--native-back");
    if (telemost) telemost.classList.remove("profile-subscreen--native-back");
    if (yandexDisk) yandexDisk.classList.remove("profile-subscreen--native-back");
    if (bitrix) bitrix.classList.remove("profile-subscreen--native-back");
    if (knowledgeBase) knowledgeBase.classList.remove("profile-subscreen--native-back");
    if (agents) agents.classList.remove("profile-subscreen--native-back");

    var nb = document.getElementById("notes-detail-back");
    if (nb) setHidden(nb, false);
    var pb = document.getElementById("profile-payment-back");
    if (pb) setHidden(pb, false);
    var eb = document.getElementById("profile-expenses-back");
    if (eb) setHidden(eb, false);
    var bb = document.getElementById("profile-booking-back");
    if (bb) setHidden(bb, false);
    var cb = document.getElementById("profile-contacts-back");
    if (cb) setHidden(cb, false);
    var calb = document.getElementById("profile-calendars-back");
    if (calb) setHidden(calb, false);
    var zb = document.getElementById("profile-zoom-back");
    if (zb) setHidden(zb, false);
    var tb = document.getElementById("profile-telemost-back");
    if (tb) setHidden(tb, false);
    var ydb = document.getElementById("profile-yandex-disk-back");
    if (ydb) setHidden(ydb, false);
    var bdb = document.getElementById("profile-bitrix-back");
    if (bdb) setHidden(bdb, false);
    var kbb = document.getElementById("profile-knowledge-base-back");
    if (kbb) setHidden(kbb, false);
    var agb = document.getElementById("profile-agents-back");
    if (agb) setHidden(agb, false);

    var webEditorBack = document.getElementById("note-editor-web-back");
    var webEditorBackBar = document.getElementById("note-editor-web-back-bar");
    var showWebEditorBack = !!(noteEditorOpen && !native);
    if (webEditorBack) setHidden(webEditorBack, !showWebEditorBack);
    if (webEditorBackBar) setHidden(webEditorBackBar, !noteEditorOpen);
    document.documentElement.classList.toggle("note-editor-web-back-visible", !!noteEditorOpen);

    function finishAppBackChrome() {
      // Re-bind whenever chrome syncs — recovers from async telegram-web-app.js race.
      if (useTelegramNativeBack() || looksLikeTelegramWebView()) {
        ensureTelegramMiniappBackBound();
      }
      ensureAppBackHistory();
    }

    if (!native) {
      if (tg && tg.BackButton && typeof tg.BackButton.hide === "function") {
        try {
          tg.BackButton.hide();
        } catch (_) {}
      }
      finishAppBackChrome();
      return;
    }

    if (noteEditorOpen) {
      applyTelegramSafeAreaInsets();
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }

    if (notesOpen) {
      detail.classList.add("notes-detail--native-back");
      applyTelegramSafeAreaInsets();
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (payOpen) {
      pay.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (expOpen) {
      exp.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (bookingOpen) {
      booking.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (contactsOpen) {
      contacts.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (calendarsOpen) {
      calendars.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (zoomOpen) {
      zoom.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (telemostOpen) {
      telemost.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (yandexDiskOpen) {
      yandexDisk.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (bitrixOpen) {
      bitrix.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (kbaseOpen) {
      knowledgeBase.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (agentsOpen) {
      agents.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      finishAppBackChrome();
      return;
    }
    if (tg && tg.BackButton && typeof tg.BackButton.hide === "function") {
      try {
        tg.BackButton.hide();
      } catch (_) {}
    }
    finishAppBackChrome();
  }

  var HASHTAG_TOKEN_RE = /#([A-Za-zА-Яа-яЁё0-9_]{1,40})/g;

  function getUserTagsFromCache() {
    return (notesDataCache && notesDataCache.tags) || [];
  }

  var userTagsLoadPromise = null;

  function ensureUserTagsLoaded(force) {
    if (
      !force &&
      notesDataCache &&
      notesDataCache._tagsLoaded &&
      Array.isArray(notesDataCache.tags)
    ) {
      return Promise.resolve(notesDataCache.tags.slice());
    }
    if (
      !force &&
      notesDataCache &&
      Array.isArray(notesDataCache.tags) &&
      notesDataCache.tags.length
    ) {
      notesDataCache._tagsLoaded = true;
      return Promise.resolve(notesDataCache.tags.slice());
    }
    if (userTagsLoadPromise) return userTagsLoadPromise;
    userTagsLoadPromise = apiFetch("/tags", { method: "GET" })
      .then(function (data) {
        if (!notesDataCache) notesDataCache = {};
        notesDataCache.tags = (data && data.tags) || [];
        notesDataCache._tagsLoaded = true;
        return notesDataCache.tags.slice();
      })
      .catch(function () {
        return getUserTagsFromCache().slice();
      })
      .finally(function () {
        userTagsLoadPromise = null;
      });
    return userTagsLoadPromise;
  }

  function getUserHashtagsFromCache() {
    var fromBundle = (notesDataCache && notesDataCache.hashtags) || [];
    if (fromBundle.length) return fromBundle;
    var seen = {};
    var out = [];
    function collect(list) {
      (list || []).forEach(function (item) {
        noteHashtagsOf(item).forEach(function (h) {
          var key = String(h.name || "").toLowerCase();
          if (!key || seen[key]) return;
          seen[key] = true;
          out.push(h);
        });
      });
    }
    if (notesDataCache) {
      collect(notesDataCache.local_notes);
      collect(notesDataCache.transcriptions);
      collect(notesDataCache.summaries);
    }
    return out;
  }

  function noteTagsOf(item) {
    var project = noteProjectOf(item);
    if (project) return [project];
    return Array.isArray(item && item.tags) ? item.tags.slice(0, 1) : [];
  }

  function noteProjectOf(item) {
    if (item && item.project && item.project.id) return item.project;
    var tags = Array.isArray(item && item.tags) ? item.tags : [];
    return tags[0] || null;
  }

  function noteHashtagsOf(item) {
    return Array.isArray(item && item.hashtags) ? item.hashtags : [];
  }

  function parseNotesSearchQuery(raw) {
    var src = String(raw || "").trim();
    var hashtags = [];
    var seen = {};
    var text = src.replace(HASHTAG_TOKEN_RE, function (_, name) {
      var key = String(name || "").toLowerCase();
      if (key && !seen[key]) {
        seen[key] = true;
        hashtags.push(key);
      }
      return " ";
    });
    return { text: text.replace(/\s+/g, " ").trim().toLowerCase(), hashtags: hashtags };
  }

  function noteItemMatchesFilter(item, kind) {
    var project = noteProjectOf(item);
    if (notesActiveProjectId) {
      if (!project || Number(project.id) !== Number(notesActiveProjectId)) return false;
    }
    var parsed = parseNotesSearchQuery(notesSearchQuery);
    if (parsed.hashtags.length) {
      var itemHash = noteHashtagsOf(item).map(function (h) {
        return String(h.name || "").toLowerCase();
      });
      for (var hi = 0; hi < parsed.hashtags.length; hi++) {
        if (itemHash.indexOf(parsed.hashtags[hi]) < 0) return false;
      }
    }
    var q = parsed.text;
    if (!q) return true;
    var title = "";
    var preview = "";
    if (kind === "local") {
      title = sanitizeNoteTitle(item.title || item.content || "") || String(item.title || item.content || "");
      preview = notePlainExcerpt(item.description || item.body || "", 500);
    } else {
      title = journalCardTitle(item);
      preview = String(item.preview || "").trim();
    }
    var projectName = project ? String(project.name || "") : "";
    var hashNames = noteHashtagsOf(item)
      .map(function (t) {
        return "#" + String(t.name || "");
      })
      .join(" ");
    var hay = (title + " " + preview + " " + projectName + " " + hashNames).toLowerCase();
    return hay.indexOf(q) >= 0;
  }

  function setNotesSearchExpanded(open) {
    var wrap = document.getElementById("notes-search-wrap");
    var toggle = document.getElementById("notes-search-toggle");
    var input = document.getElementById("notes-search-input");
    if (!wrap) return;
    var shouldOpen = !!open || !!(input && String(input.value || "").trim());
    wrap.classList.toggle("notes-search-wrap--open", shouldOpen);
    if (toggle) toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    if (!shouldOpen) closeNotesTagFilterMenu();
    if (shouldOpen && input && open) {
      window.requestAnimationFrame(function () {
        try {
          input.focus();
        } catch (_) {}
      });
    }
  }

  function applyNotesHashtagSearch(name) {
    var token = "#" + String(name || "").replace(/^#/, "");
    notesSearchQuery = token;
    var input = document.getElementById("notes-search-input");
    if (input) input.value = token;
    setNotesSearchExpanded(true);
    if (notesDataCache) renderNotesPanesFromData(notesDataCache);
  }

  function clearNotesHashtagSearch() {
    notesSearchQuery = "";
    var input = document.getElementById("notes-search-input");
    if (input) input.value = "";
    setNotesSearchExpanded(true);
    if (notesDataCache) renderNotesPanesFromData(notesDataCache);
  }

  function notesTagFilterMenuIsOpen() {
    var menu = document.getElementById("notes-tag-filter-menu");
    return !!(menu && !menu.classList.contains("hidden"));
  }

  function closeNotesTagFilterMenu() {
    var menu = document.getElementById("notes-tag-filter-menu");
    var btn = document.getElementById("notes-search-tag-btn");
    if (menu) menu.classList.add("hidden");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function fillNotesTagFilterMenu() {
    var menu = document.getElementById("notes-tag-filter-menu");
    if (!menu) return;
    menu.innerHTML = "";
    var tags = getUserHashtagsFromCache()
      .slice()
      .sort(function (a, b) {
        return String(a.name || "").localeCompare(String(b.name || ""), "ru", { sensitivity: "base" });
      });
    var active = {};
    parseNotesSearchQuery(notesSearchQuery).hashtags.forEach(function (h) {
      active[h] = true;
    });
    if (!tags.length) {
      var empty = document.createElement("p");
      empty.className = "notes-tag-filter-menu-empty";
      empty.textContent = "Хэштегов пока нет";
      menu.appendChild(empty);
      return;
    }
    tags.forEach(function (tag) {
      var name = String(tag.name || "").trim();
      if (!name) return;
      var key = name.toLowerCase();
      var isActive = !!active[key];
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "notes-tag-filter-menu-item" + (isActive ? " notes-tag-filter-menu-item--active" : "");
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
      var label = document.createElement("span");
      label.className = "notes-tag-filter-menu-item-label";
      label.textContent = "#" + name;
      btn.appendChild(label);
      if (isActive) {
        var check = document.createElement("span");
        check.className = "notes-tag-filter-menu-item-check";
        check.textContent = "✓";
        check.setAttribute("aria-hidden", "true");
        btn.appendChild(check);
      }
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (isActive) {
          clearNotesHashtagSearch();
        } else {
          applyNotesHashtagSearch(name);
        }
        closeNotesTagFilterMenu();
      });
      menu.appendChild(btn);
    });
  }

  function openNotesTagFilterMenu() {
    var wrap = document.getElementById("notes-search-wrap");
    var menu = document.getElementById("notes-tag-filter-menu");
    var btn = document.getElementById("notes-search-tag-btn");
    if (!menu || !btn) return;
    setNotesSearchExpanded(true);
    fillNotesTagFilterMenu();
    menu.classList.remove("hidden");
    btn.setAttribute("aria-expanded", "true");
    if (wrap) wrap.classList.add("notes-search-wrap--open");
  }

  function toggleNotesTagFilterMenu(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (notesTagFilterMenuIsOpen()) {
      closeNotesTagFilterMenu();
      return;
    }
    openNotesTagFilterMenu();
  }

  var notesTagFilterDocBound = false;
  function bindNotesTagFilterDocClickOnce() {
    if (notesTagFilterDocBound) return;
    notesTagFilterDocBound = true;
    document.addEventListener(
      "mousedown",
      function (e) {
        if (!notesTagFilterMenuIsOpen()) return;
        var field = document.getElementById("notes-search-field");
        if (field && field.contains(e.target)) return;
        closeNotesTagFilterMenu();
      },
      true
    );
  }

  function renderNotesTagFilterBar() {
    var row = document.getElementById("notes-project-chips");
    if (!row) return;
    row.innerHTML = "";
    var tags = getUserTagsFromCache();
    tags.forEach(function (tag) {
      var tid = Number(tag.id);
      var active = Number(notesActiveProjectId) === tid;
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className =
        "tag-chip tag-chip--filter notes-project-chip" + (active ? " tag-chip--active" : "");
      chip.setAttribute("role", "option");
      chip.setAttribute("aria-selected", active ? "true" : "false");
      chip.textContent = String(tag.name || "");
      chip.addEventListener("click", function (e) {
        e.stopPropagation();
        notesActiveProjectId = active ? 0 : tid;
        renderNotesTagFilterBar();
        if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      });
      row.appendChild(chip);
    });
    var manage = document.createElement("button");
    manage.type = "button";
    manage.className = "notes-project-manage";
    manage.setAttribute("aria-label", "Управление проектами");
    manage.title = "Проекты";
    manage.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke-linecap="round"/></svg>';
    manage.addEventListener("click", function (e) {
      e.stopPropagation();
      openTagsManageSheet();
    });
    row.appendChild(manage);
  }

  function noteMatchesPickerProjectFilter(item, activeProjectId) {
    if (!activeProjectId) return true;
    var project = noteProjectOf(item);
    return !!(project && Number(project.id) === Number(activeProjectId));
  }

  function renderNotePickerProjectChips(container, getActiveId, setActiveId, onChange) {
    if (!container) return;
    container.innerHTML = "";
    container.className = "notes-project-chips note-picker-project-chips";
    container.setAttribute("role", "listbox");
    container.setAttribute("aria-label", "Проекты");
    var tags = getUserTagsFromCache();
    if (!tags.length) {
      setHidden(container, true);
      return;
    }
    setHidden(container, false);
    var activeProjectId = getActiveId();
    tags.forEach(function (tag) {
      var tid = Number(tag.id);
      var active = Number(activeProjectId) === tid;
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className =
        "tag-chip tag-chip--filter notes-project-chip" + (active ? " tag-chip--active" : "");
      chip.setAttribute("role", "option");
      chip.setAttribute("aria-selected", active ? "true" : "false");
      chip.textContent = String(tag.name || "");
      chip.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        setActiveId(active ? 0 : tid);
        renderNotePickerProjectChips(container, getActiveId, setActiveId, onChange);
        if (typeof onChange === "function") onChange();
      });
      container.appendChild(chip);
    });
  }

  function appendTagChipsRow(parent, tags, opts) {
    opts = opts || {};
    var project = opts.project || null;
    var hashtags = opts.hashtags || [];
    if (!project && (!tags || !tags.length) && !hashtags.length) return;
    var row = document.createElement("div");
    row.className = parent.classList && parent.classList.contains("journal-card-main")
      ? "journal-card-tags"
      : "note-card-tags";
    if (opts.head) row.classList.add("note-card-tags--head");
    if (project) {
      var pchip = document.createElement("span");
      pchip.className = "tag-chip tag-chip--card tag-chip--project";
      pchip.textContent = String(project.name || "");
      row.appendChild(pchip);
    } else if (tags && tags.length) {
      tags.forEach(function (tag) {
        var chip = document.createElement("span");
        chip.className = "tag-chip tag-chip--card tag-chip--project";
        chip.textContent = String(tag.name || "");
        row.appendChild(chip);
      });
    }
    hashtags.forEach(function (tag) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "tag-chip tag-chip--card tag-chip--hashtag";
      chip.textContent = String(tag.name || "");
      chip.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        applyNotesHashtagSearch(tag.name);
      });
      row.appendChild(chip);
    });
    if (!row.childNodes.length) return;
    if (opts.prepend) parent.insertBefore(row, parent.firstChild);
    else parent.appendChild(row);
  }

  function appendItemLabelChips(parent, item, opts) {
    appendTagChipsRow(parent, noteTagsOf(item), Object.assign({
      project: noteProjectOf(item),
      hashtags: noteHashtagsOf(item),
    }, opts || {}));
  }

  function updateItemTagsInCache(itemKind, itemId, tags) {
    var idStr = String(itemId);
    function patch(list) {
      if (!list) return;
      list.forEach(function (row) {
        if (String(row.id) === idStr) {
          row.tags = tags.slice();
          row.project = tags[0] || null;
        }
      });
    }
    if (itemKind === "local") {
      if (notesDataCache) {
        patch(notesDataCache.local_notes);
        patch(notesDataCache.transcriptions);
      }
      if (knowledgeDataCache) patch(knowledgeDataCache.notes);
    } else if (notesDataCache) {
      patch(notesDataCache.transcriptions);
      patch(notesDataCache.summaries);
      patch(notesDataCache.journal);
    }
  }

  async function saveItemTags(itemKind, itemId, tagIds) {
    var res = await apiFetch(
      "/notes/" + encodeURIComponent(itemKind) + "/" + encodeURIComponent(String(itemId)) + "/tags",
      {
        method: "PUT",
        body: JSON.stringify({ tag_ids: tagIds }),
      }
    );
    var tags = (res && res.tags) || [];
    if (res && res.project) tags = [res.project];
    updateItemTagsInCache(itemKind, itemId, tags);
    renderNotesTagFilterBar();
    if (notesDataCache) renderNotesPanesFromData(notesDataCache);
    return tags;
  }

  function bindTagPickerDocClickOnce() {
    if (tagPickerDocClickBound) return;
    tagPickerDocClickBound = true;
    document.addEventListener("click", function (e) {
      if (tagPickerActiveClose) tagPickerActiveClose();
      var more = document.getElementById("note-editor-more-wrap");
      if (more && e.target && more.contains(e.target)) return;
      closeNoteMoreMenu();
      if (
        e.target &&
        e.target.closest &&
        (e.target.closest(".note-card-actions") || e.target.closest(".note-card-menu"))
      ) {
        return;
      }
      if (
        e.target &&
        e.target.closest &&
        (e.target.closest(".chat-thread-more-wrap") || e.target.closest(".chat-thread-menu"))
      ) {
        return;
      }
      closeNoteCardMenus();
      closeChatThreadMenus();
    });
  }

  var TAG_PICKER_ICON_SVG =
    '<svg class="tag-picker-add-btn-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>' +
    '<circle cx="7" cy="7" r="1.5" fill="currentColor" stroke="none"/>' +
    "</svg>";
  var TAG_PICKER_CHEVRON_SVG =
    '<svg class="tag-picker-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">' +
    '<path d="M6 9l6 6 6-6"/>' +
    "</svg>";

  function mountTagPicker(container, itemKind, itemId, currentTags, onChange) {
    container.innerHTML = "";
    container.classList.add("tag-picker", "tag-picker--dropdown");
    bindTagPickerDocClickOnce();

    var stateTags = (currentTags || []).slice(0, 1);
    var menuOpen = false;
    var menuEl = null;
    var addBtnEl = null;
    var menuPositionCleanup = null;
    var useFixedMenu = !!(container.closest && container.closest("#note-editor-overlay"));

    function resetMenuPosition() {
      if (!menuEl) return;
      menuEl.style.position = "";
      menuEl.style.left = "";
      menuEl.style.top = "";
      menuEl.style.transform = "";
      menuEl.style.zIndex = "";
    }

    function positionFixedMenu() {
      if (!useFixedMenu || !menuEl || menuEl.classList.contains("hidden") || !addBtnEl) return;
      var rect = addBtnEl.getBoundingClientRect();
      menuEl.style.position = "fixed";
      menuEl.style.left = rect.left + rect.width / 2 + "px";
      menuEl.style.top = rect.bottom + 6 + "px";
      menuEl.style.transform = "translateX(-50%)";
      menuEl.style.zIndex = "120";
    }

    function unbindMenuPositionListeners() {
      if (!menuPositionCleanup) return;
      menuPositionCleanup();
      menuPositionCleanup = null;
    }

    function bindMenuPositionListeners() {
      unbindMenuPositionListeners();
      if (!useFixedMenu) return;
      var onReposition = function () {
        positionFixedMenu();
      };
      window.addEventListener("resize", onReposition);
      window.addEventListener("scroll", onReposition, true);
      menuPositionCleanup = function () {
        window.removeEventListener("resize", onReposition);
        window.removeEventListener("scroll", onReposition, true);
      };
    }

    function tagIds() {
      return stateTags.map(function (t) {
        return Number(t.id);
      });
    }

    function selectedIdMap() {
      var map = {};
      stateTags.forEach(function (t) {
        map[Number(t.id)] = true;
      });
      return map;
    }

    function updateTrigger() {
      if (!addBtnEl) return;
      addBtnEl.classList.remove("tag-picker-trigger--selected");
      addBtnEl.textContent = "";
      var inner = document.createElement("span");
      inner.className = "tag-picker-add-btn-inner";
      var count = stateTags.length;
      if (!count) {
        inner.insertAdjacentHTML("afterbegin", TAG_PICKER_ICON_SVG);
        var emptyLabel = document.createElement("span");
        emptyLabel.textContent = "Проект";
        inner.appendChild(emptyLabel);
      } else {
        addBtnEl.classList.add("tag-picker-trigger--selected");
        var label = document.createElement("span");
        label.className = "tag-picker-trigger-label";
        label.textContent = String(stateTags[0].name || "");
        inner.appendChild(label);
        if (count > 1) {
          var more = document.createElement("span");
          more.className = "tag-picker-more";
          more.textContent = "+" + (count - 1);
          inner.appendChild(more);
        }
        inner.insertAdjacentHTML("beforeend", TAG_PICKER_CHEVRON_SVG);
      }
      addBtnEl.appendChild(inner);
      addBtnEl.setAttribute(
        "aria-expanded",
        menuOpen && menuEl && !menuEl.classList.contains("hidden") ? "true" : "false"
      );
    }

    function scheduleSave() {
      if (tagPickerSaveTimer) clearTimeout(tagPickerSaveTimer);
      tagPickerSaveTimer = setTimeout(async function () {
        try {
          var saved = await saveItemTags(itemKind, itemId, tagIds());
          stateTags = saved.slice();
          if (typeof onChange === "function") onChange(saved);
          updateTrigger();
          if (menuOpen) fillMenu();
        } catch (e) {
          alert(e.message || String(e));
        }
      }, 400);
    }

    function closeMenu() {
      menuOpen = false;
      if (menuEl) menuEl.classList.add("hidden");
      resetMenuPosition();
      unbindMenuPositionListeners();
      if (tagPickerActiveClose === closeMenu) tagPickerActiveClose = null;
      updateTrigger();
    }

    function fillMenu() {
      if (!menuEl) return;
      menuEl.innerHTML = "";
      var selected = selectedIdMap();
      var allTags = getUserTagsFromCache().slice();
      if (!allTags.length) {
        var empty = document.createElement("p");
        empty.className = "tag-picker-menu-empty";
        empty.textContent = "Проектов пока нет";
        menuEl.appendChild(empty);
      } else {
        allTags.forEach(function (t) {
          var tid = Number(t.id);
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className =
            "tag-picker-menu-item" + (selected[tid] ? " tag-picker-menu-item--selected" : "");
          btn.setAttribute("role", "option");
          btn.setAttribute("aria-selected", selected[tid] ? "true" : "false");
          var name = document.createElement("span");
          name.className = "tag-picker-menu-label";
          name.textContent = String(t.name || "");
          var check = document.createElement("span");
          check.className = "tag-picker-menu-check";
          check.textContent = selected[tid] ? "✓" : "";
          check.setAttribute("aria-hidden", "true");
          btn.appendChild(name);
          btn.appendChild(check);
          btn.addEventListener("click", function (e) {
            e.stopPropagation();
            if (selected[tid]) {
              stateTags = [];
            } else {
              stateTags = [{ id: t.id, name: t.name }];
            }
            updateTrigger();
            fillMenu();
            scheduleSave();
          });
          menuEl.appendChild(btn);
        });
      }
      var createBtn = document.createElement("button");
      createBtn.type = "button";
      createBtn.className = "tag-picker-menu-item tag-picker-menu-item--create";
      createBtn.textContent = "Создать новый…";
      createBtn.addEventListener("click", async function (e) {
        e.stopPropagation();
        closeMenu();
        var name = prompt("Название проекта");
        if (!name || !String(name).trim()) return;
        try {
          var res = await apiFetch("/tags", {
            method: "POST",
            body: JSON.stringify({ name: String(name).trim() }),
          });
          var created = (res && res.tag) || null;
          if (!created) return;
          if (!notesDataCache) notesDataCache = {};
          if (!notesDataCache.tags) notesDataCache.tags = [];
          notesDataCache.tags.push(created);
          notesDataCache.tags.sort(function (a, b) {
            return String(a.name || "").localeCompare(String(b.name || ""), "ru");
          });
          stateTags = [{ id: created.id, name: created.name }];
          renderNotesTagFilterBar();
          scheduleSave();
          updateTrigger();
        } catch (err) {
          alert(err.message || String(err));
        }
      });
      menuEl.appendChild(createBtn);
    }

    function openMenu() {
      if (tagPickerActiveClose && tagPickerActiveClose !== closeMenu) tagPickerActiveClose();
      menuOpen = true;
      tagPickerActiveClose = closeMenu;
      fillMenu();
      menuEl.classList.remove("hidden");
      updateTrigger();
      if (useFixedMenu) {
        positionFixedMenu();
        bindMenuPositionListeners();
      }
      ensureUserTagsLoaded().then(function () {
        if (!menuOpen) return;
        fillMenu();
        if (useFixedMenu) positionFixedMenu();
      });
    }

    var addWrap = document.createElement("div");
    addWrap.className = "tag-picker-add-wrap";
    addBtnEl = document.createElement("button");
    addBtnEl.type = "button";
    addBtnEl.className = "tag-picker-add-btn tag-picker-trigger";
    addBtnEl.setAttribute("aria-label", "Проект");
    addBtnEl.setAttribute("aria-haspopup", "listbox");
    addBtnEl.setAttribute("aria-expanded", "false");
    menuEl = document.createElement("div");
    menuEl.className = "tag-picker-menu hidden";
    menuEl.setAttribute("role", "listbox");
    if (useFixedMenu) menuEl.classList.add("tag-picker-menu--fixed");

    addBtnEl.addEventListener("click", function (e) {
      e.stopPropagation();
      if (menuOpen) closeMenu();
      else openMenu();
    });

    addWrap.appendChild(addBtnEl);
    addWrap.appendChild(menuEl);
    container.appendChild(addWrap);

    updateTrigger();

    container._tagPickerUnmount = closeMenu;
  }

  function openTagsManageSheet() {
    var ov = document.getElementById("tags-manage-overlay");
    if (!ov) return;
    ov.classList.remove("hidden");
    ov.setAttribute("aria-hidden", "false");
    renderTagsManageList();
    onModalSheetOpen();
    updateModalSheetForKeyboard();
    syncAppOverlay();
  }

  function closeTagsManageSheet() {
    var ov = document.getElementById("tags-manage-overlay");
    if (!ov) return;
    ov.classList.add("hidden");
    ov.setAttribute("aria-hidden", "true");
    onModalSheetClose();
    syncAppOverlay();
  }

  function renderTagsManageList() {
    var list = document.getElementById("tags-manage-list");
    if (!list) return;
    list.innerHTML = "";
    var tags = getUserTagsFromCache().slice();
    if (!tags.length) {
      list.innerHTML = '<p class="muted small">Проектов пока нет. Создайте первый ниже.</p>';
      return;
    }
    tags.forEach(function (tag) {
      var row = document.createElement("div");
      row.className = "tags-manage-row";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "field-input tags-manage-row-input";
      input.value = String(tag.name || "");
      input.maxLength = 40;
      var del = document.createElement("button");
      del.type = "button";
      del.className = "tags-manage-row-del";
      del.textContent = "Удалить";
      input.addEventListener("change", async function () {
        var name = String(input.value || "").trim();
        if (!name || name === String(tag.name || "")) {
          input.value = String(tag.name || "");
          return;
        }
        try {
          var res = await apiFetch("/tags/" + encodeURIComponent(String(tag.id)), {
            method: "PATCH",
            body: JSON.stringify({ name: name }),
          });
          var updated = (res && res.tag) || { id: tag.id, name: name };
          if (notesDataCache && notesDataCache.tags) {
            notesDataCache.tags = notesDataCache.tags.map(function (t) {
              return Number(t.id) === Number(tag.id) ? updated : t;
            });
          }
          tag.name = updated.name;
          await loadNotes();
          renderTagsManageList();
        } catch (e) {
          alert(e.message || String(e));
          input.value = String(tag.name || "");
        }
      });
      del.addEventListener("click", async function () {
        if (!(await confirmDialog('Удалить проект «' + String(tag.name || "") + "»?"))) return;
        try {
          await apiFetch("/tags/" + encodeURIComponent(String(tag.id)), { method: "DELETE" });
          if (Number(notesActiveProjectId) === Number(tag.id)) notesActiveProjectId = 0;
          await loadNotes();
          renderTagsManageList();
        } catch (e) {
          alert(e.message || String(e));
        }
      });
      row.appendChild(input);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function destroyActiveNoteRichEditor() {
    var body = getNoteEditorBodyEl();
    if (!body) return;
    var inst = body._noteRichEditor;
    if (inst && typeof inst.destroy === "function") {
      try {
        inst.destroy();
      } catch (_) {}
    }
    body._noteRichEditor = null;
  }

  function getNoteRichEditorApi() {
    var api = window.NoteRichEditor;
    if (!api && typeof globalThis !== "undefined") api = globalThis.NoteRichEditor;
    if (api && api.default && typeof api.default.mount === "function") return api.default;
    return api;
  }

  function mountFallbackNoteEditor(container, body, onUpdate) {
    if (!container) return null;
    container.innerHTML = "";
    var warn = document.createElement("p");
    warn.className = "muted small";
    warn.textContent = "Упрощённый редактор: полный не загрузился.";
    var ed = document.createElement("div");
    ed.className = "note-rich-editor-body note-editor-fallback";
    ed.contentEditable = "true";
    ed.setAttribute("role", "textbox");
    ed.innerHTML = body || "";
    ed.addEventListener("input", function () {
      if (typeof onUpdate === "function") onUpdate();
    });
    container.appendChild(warn);
    container.appendChild(ed);
    return {
      getHtml: function () {
        return ed.innerHTML || "";
      },
      setHtml: function (html) {
        ed.innerHTML = html || "";
      },
      getCursor: function () {
        return 0;
      },
      hasRangeSelection: function () {
        var sel = window.getSelection && window.getSelection();
        return !!(sel && !sel.isCollapsed && ed.contains(sel.anchorNode));
      },
      coordsAtPos: function () {
        return null;
      },
      appendText: function (text) {
        var raw = String(text || "").replace(/\s+$/, "");
        if (!raw) return;
        raw.split("\n").forEach(function (line) {
          var p = document.createElement("p");
          p.textContent = line;
          ed.appendChild(p);
        });
        if (typeof onUpdate === "function") onUpdate();
      },
      appendHtml: function (html) {
        var raw = String(html || "").trim();
        if (!raw) return;
        ed.insertAdjacentHTML("beforeend", raw);
        if (typeof onUpdate === "function") onUpdate();
      },
      prepareForSave: function () {},
      focus: function () {
        ed.focus();
      },
      bindTapFocus: function () {},
      destroy: function () {
        container.innerHTML = "";
      },
    };
  }

  function setNoteEditorTocOpen(page, open) {
    if (!page) return;
    page.classList.toggle("note-editor-page--toc-open", !!open);
    var btn = page.querySelector(".note-editor-toc-toggle");
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function bindNoteEditorTocControls(page) {
    var controls = {
      tocParent: page ? page.querySelector(".note-editor-toc-list") : null,
      close: function () {
        setNoteEditorTocOpen(page, false);
      },
    };
    if (!page) return controls;
    var toggle = page.querySelector(".note-editor-toc-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        setNoteEditorTocOpen(page, !page.classList.contains("note-editor-page--toc-open"));
      });
    }
    page.querySelectorAll("[data-note-toc-close]").forEach(function (el) {
      el.addEventListener("click", controls.close);
    });
    return controls;
  }

  function getActiveNoteEditorPage() {
    var body = getNoteEditorBodyEl();
    return body ? body.querySelector(".note-editor-page") : null;
  }

  function openActiveNoteEditorToc() {
    var page = getActiveNoteEditorPage();
    if (!page) return;
    setNoteEditorTocOpen(page, true);
  }

  function noteEditorPageInnerHtml(titleLabel) {
    var titleAria = escapeHtml(titleLabel || "Заголовок");
    var tocToggle = isMobileNoteLayout()
      ? ""
      : '<button type="button" class="note-editor-toc-toggle" aria-controls="note-editor-toc-panel" aria-expanded="false" title="Оглавление" aria-label="Оглавление">' +
        '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="4" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="4" cy="18" r="1" fill="currentColor" stroke="none"/>' +
        "</svg>" +
        "</button>";
    var tocPanel =
      '<aside class="note-editor-toc-panel" id="note-editor-toc-panel" aria-label="Оглавление">' +
      '<div class="note-editor-toc-head">' +
      "<span>Оглавление</span>" +
      '<button type="button" class="note-editor-toc-close" data-note-toc-close aria-label="Закрыть">x</button>' +
      "</div>" +
      '<div class="note-editor-toc-list"></div>' +
      "</aside>";
    var tocChrome = isMobileNoteLayout()
      ? tocPanel
      : '<div class="note-editor-toc-anchor">' + tocToggle + tocPanel + "</div>";
    return (
      '<div class="note-editor-doc-layout">' +
      tocChrome +
      '<div class="note-editor-toc-backdrop" data-note-toc-close aria-hidden="true"></div>' +
      '<div class="note-editor-main-column">' +
      '<div class="note-editor-pad note-editor-pad--title-in-body">' +
      '<textarea id="note-editor-title-input" class="note-editor-title vkui--font_title1--regular" ' +
      'placeholder="Заголовок" aria-label="' +
      titleAria +
      '" maxlength="500" rows="1" autocapitalize="sentences" spellcheck="true"></textarea>' +
      '<div id="note-editor-members" class="note-editor-members hidden"></div>' +
      '<div id="note-editor-sheets" class="note-editor-sheets hidden"></div>' +
      "</div>" +
      '<div class="note-editor-pad note-editor-pad--body"></div>' +
      "</div>" +
      "</div>"
    );
  }

  function mountNoteRichEditor(container, body, onUpdate, options) {
    options = options || {};
    var toolbarWrap = document.getElementById("note-editor-toolbar-wrap");
    var formatHost =
      options.toolbarParent !== undefined
        ? options.toolbarParent
        : document.getElementById("note-editor-format-toolbar-host");
    if (toolbarWrap && options.showSharedToolbar !== false) {
      setHidden(toolbarWrap, false);
      syncNoteFormatToolbarForComposer();
    }
    if (formatHost && options.clearToolbar !== false) formatHost.innerHTML = "";
    var mountPoint = document.createElement("div");
    mountPoint.id = options.mountId || "td-desc";
    mountPoint.className = "note-rich-editor-mount";
    container.appendChild(mountPoint);
    var loading = document.createElement("p");
    loading.className = "muted small";
    loading.textContent = "Загрузка редактора…";
    mountPoint.appendChild(loading);

    return loadNoteEditorScripts()
      .then(function () {
        loading.remove();
        var NoteRichEditor = getNoteRichEditorApi();
        if (!NoteRichEditor || typeof NoteRichEditor.mount !== "function") {
          return mountFallbackNoteEditor(mountPoint, body, onUpdate);
        }
        try {
          var editor = NoteRichEditor.mount(mountPoint, {
            body: body,
            placeholder: "Начните писать…",
            onUpdate: function () {
              if (typeof onUpdate === "function") onUpdate.apply(null, arguments);
              scrollNoteCaretIntoView();
            },
            onSelection: function () {
              if (typeof options.onSelection === "function") options.onSelection.apply(null, arguments);
              if (!noteEditorHasRangeSelection()) scrollNoteCaretIntoView();
            },
            toolbarParent: formatHost || null,
            tocParent: options.tocParent || null,
            onTocNavigate: options.onTocNavigate || null,
            scrollParent: options.scrollParent || null,
            extraTocItems: options.extraTocItems || null,
            hashtags: getUserHashtagsFromCache().map(function (h) {
              return String(h.name || "");
            }),
            onTaskSlash: createTaskFromSlash,
            onTaskChipClick: openTaskFromChip,
            onTaskChipRemoved: deleteTaskFromChip,
          });
          if (editor && typeof editor.bindTapFocus === "function" && !isIOSDevice()) {
            editor.bindTapFocus(container);
          }
          return editor;
        } catch (err) {
          console.error("NoteRichEditor.mount", err);
          return mountFallbackNoteEditor(mountPoint, body, onUpdate);
        }
      })
      .catch(function (err) {
        console.error("loadNoteEditorScripts", err);
        return mountFallbackNoteEditor(mountPoint, body, onUpdate);
      });
  }

  function resolveLocalNoteFromCache(n) {
    if (!n) return n;
    var lid = localNoteIdFrom(n);
    if (!lid) return n;
    if (notesDataCache && notesDataCache.local_notes) {
      for (var i = 0; i < notesDataCache.local_notes.length; i++) {
        if (String(notesDataCache.local_notes[i].id) === String(lid)) {
          return notesDataCache.local_notes[i];
        }
      }
    }
    if (notesDataCache && notesDataCache.transcriptions) {
      for (var t = 0; t < notesDataCache.transcriptions.length; t++) {
        if (String(notesDataCache.transcriptions[t].id) === String(lid)) {
          return notesDataCache.transcriptions[t];
        }
      }
    }
    if (knowledgeDataCache && knowledgeDataCache.notes) {
      for (var k = 0; k < knowledgeDataCache.notes.length; k++) {
        if (String(knowledgeDataCache.notes[k].id) === String(lid)) {
          return knowledgeDataCache.notes[k];
        }
      }
    }
    return n;
  }

  function resolveKnowledgeNoteFromCache(n) {
    if (!n || !knowledgeDataCache || !knowledgeDataCache.notes) return n;
    var lid = localNoteIdFrom(n);
    if (!lid) return n;
    for (var i = 0; i < knowledgeDataCache.notes.length; i++) {
      if (String(knowledgeDataCache.notes[i].id) === String(lid)) {
        return knowledgeDataCache.notes[i];
      }
    }
    return n;
  }

  var NOTE_SHEET_MAIN = "main";
  var noteSheetState = {
    sheets: [],
    leftId: NOTE_SHEET_MAIN,
    rightMode: "discussion",
    rightId: "",
    rightNote: null,
    rightNotePersistTimer: null,
    focus: "left",
    contextIds: null,
    contextExplicit: false,
    rightEditor: null,
    primaryBody: "",
    sheetTimers: {},
    sheetPersists: {},
    leftScroll: {},
    skipCaretScroll: false,
    enabled: false,
  };

  function getLeftNoteRichEditor() {
    var body = getNoteEditorBodyEl();
    return body && body._noteRichEditor ? body._noteRichEditor : null;
  }

  function getActiveNoteRichEditor() {
    // Чужая заметка в правом слое не должна подменять dirty/save основной.
    if (
      noteSheetState.focus === "right" &&
      noteSheetState.rightEditor &&
      noteSheetState.rightMode === "sheet"
    ) {
      return noteSheetState.rightEditor;
    }
    if (
      noteSheetState.focus === "right" &&
      noteSheetState.rightEditor &&
      noteSheetState.rightMode === "note" &&
      isNoteDiscussionOpen()
    ) {
      return noteSheetState.rightEditor;
    }
    return getLeftNoteRichEditor();
  }

  function readActiveNoteEditorHtml(editorInst) {
    var inst = editorInst || getActiveNoteRichEditor();
    if (!inst || typeof inst.getHtml !== "function") return "";
    return inst.getHtml();
  }

  function bindNoteTaskHandlers(root, noteId, getBody, onSaved) {
    if (!root || !noteId) return;
    root.querySelectorAll(".note-task input[type=checkbox]").forEach(function (cb) {
      cb.addEventListener("change", async function () {
        var taskIndex = parseInt(cb.getAttribute("data-task-index") || "-1", 10);
        var lineIndex = parseInt(cb.getAttribute("data-line") || "-1", 10);
        var body = typeof getBody === "function" ? getBody() : "";
        var next = body;
        if (taskIndex >= 0 && window.NoteHtml && window.NoteHtml.toggleTaskInStorageHtml) {
          next = window.NoteHtml.toggleTaskInStorageHtml(body, taskIndex);
        } else if (lineIndex >= 0) {
          next = toggleNoteTaskAtLine(body, lineIndex);
        }
        if (next === body) return;
        try {
          await apiFetch("/notes/local/" + encodeURIComponent(String(noteId)), {
            method: "PATCH",
            body: JSON.stringify({ description: next }),
          });
          if (typeof onSaved === "function") onSaved(next);
          var contentRoot = root.querySelector(".note-body-html, .note-body-plain") || root;
          contentRoot.innerHTML = wrapTablesForScroll(
            renderRichTextInner(next, { interactive: true })
          );
          bindNoteTaskHandlers(root, noteId, function () {
            return next;
          }, onSaved);
        } catch (e) {
          alert(e.message || String(e));
          cb.checked = !cb.checked;
          var li = cb.closest(".note-task");
          if (li) li.classList.toggle("note-task--checked", cb.checked);
        }
      });
    });
  }

  function toggleNoteTaskAtLine(body, lineIndex) {
    var lines = String(body || "").split("\n");
    if (lineIndex < 0 || lineIndex >= lines.length) return body;
    var line = lines[lineIndex];
    var m = line.match(/^(\s*-\s+)\[([ xX])\]\s+(.*)$/);
    if (!m) return body;
    var checked = String(m[2]).toLowerCase() === "x";
    lines[lineIndex] = m[1] + "[" + (checked ? " " : "x") + "] " + m[3];
    return lines.join("\n");
  }

  function syncNotesCreateFab() {
    var createBtn = document.getElementById("notes-create-btn");
    if (!createBtn) return;
    var notesPanel = document.getElementById("panel-notes");
    var notesDetail = document.getElementById("notes-detail");
    var notesRoot = document.getElementById("notes-root");
    var show =
      currentTab === "notes" &&
      (notesSubTab === "notes" || notesSubTab === "chats") &&
      notesPanel &&
      !notesPanel.classList.contains("hidden") &&
      (!notesDetail || notesDetail.classList.contains("hidden")) &&
      (!notesRoot || !notesRoot.classList.contains("hidden")) &&
      !isNoteEditorModalOpen();
    setHidden(createBtn, !show);
    syncKnowledgeCreateFab();
  }

  function syncKnowledgeCreateFab() {
    var createBtn = document.getElementById("knowledge-create-btn");
    if (!createBtn) return;
    var knowledgePanel = document.getElementById("panel-knowledge");
    var show =
      currentTab === "knowledge" &&
      knowledgePanel &&
      !knowledgePanel.classList.contains("hidden") &&
      !isNoteEditorModalOpen();
    setHidden(createBtn, !show);
  }

  function syncActualCreateFab() {
    var createBtn = document.getElementById("actual-create-btn");
    if (!createBtn) return;
    var actualPanel = document.getElementById("panel-actual");
    var show =
      currentTab === "actual" &&
      actualPanel &&
      !actualPanel.classList.contains("hidden") &&
      !isNoteEditorModalOpen();
    setHidden(createBtn, !show);
    if (!show) closeActualCreateMenu();
  }

  function leaveChatThreadsMode() {
    activeChatThreadId = "";
    leavingChatsTab = true;
    try {
      if (isNoteDiscussionOpen()) closeNoteDiscussion(true);
      restoreDiscussionOverlayHost();
      clearChatShareWrap();
      document.documentElement.classList.remove("chats-split-open");
      var empty = document.getElementById("chat-thread-empty");
      if (empty) setHidden(empty, false);
      pruneEmptyChatDrafts("");
    } finally {
      leavingChatsTab = false;
      syncChatDiscussionChrome();
      renderNoteAskModes();
    }
  }

  function ensureChatShareHost() {
    var host = document.getElementById("chat-share-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "chat-share-host";
      host.className = "hidden";
      host.setAttribute("aria-hidden", "true");
      document.body.appendChild(host);
    }
    return host;
  }

  function clearChatShareWrap() {
    var host = document.getElementById("chat-share-host");
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && host && wrap.parentNode === host) {
      wrap._shareKind = "";
      wrap._shareId = "";
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    }
    var panel = document.getElementById("note-editor-comments");
    if (panel && host && panel.parentNode === host) {
      panel._allComments = [];
      panel._commentsCache = [];
      if (panel.parentNode) panel.parentNode.removeChild(panel);
    }
  }

  function ensureChatShareWrap(threadId) {
    var host = ensureChatShareHost();
    if (isNoteEditorModalOpen()) {
      try {
        handleNoteEditorModalClose();
      } catch (_) {}
    }
    var existing = document.getElementById("note-editor-more-wrap");
    while (existing) {
      if (existing.parentNode) existing.parentNode.removeChild(existing);
      existing = document.getElementById("note-editor-more-wrap");
    }
    var stalePanel = document.getElementById("note-editor-comments");
    if (stalePanel && stalePanel.parentNode && stalePanel.parentNode !== host) {
      stalePanel.parentNode.removeChild(stalePanel);
      stalePanel = null;
    } else if (stalePanel && host && stalePanel.parentNode === host) {
      stalePanel._allComments = [];
      stalePanel._commentsCache = [];
    }
    var wrap = document.createElement("div");
    wrap.id = "note-editor-more-wrap";
    wrap.className = "note-more-wrap hidden";
    wrap._shareKind = "chat";
    wrap._shareId = String(threadId);
    wrap._shareShared = false;
    wrap._shareUrl = "";
    wrap._shareAccess = "view";
    wrap._shareSheetIds = [];
    wrap._paeiRunning = false;
    wrap._askMode = "gpt";
    wrap._noteMembers = [];
    wrap._noteRevision = 1;
    wrap._noteUpdatedAt = "";
    wrap._isNoteOwner = true;
    wrap._discussionDismissed = false;
    host.appendChild(wrap);
    var panel = document.getElementById("note-editor-comments");
    if (!panel || panel.parentNode !== host) {
      if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
      panel = document.createElement("aside");
      panel.id = "note-editor-comments";
      panel.className = "note-comments hidden";
      panel.innerHTML = '<div class="note-comments-list"></div>';
      host.appendChild(panel);
    }
    return wrap;
  }

  function restoreDiscussionOverlayHost() {
    var ov = document.getElementById("note-discussion-overlay");
    if (!ov) return;
    ov.classList.remove("chat-hosted-discussion");
    if (ov._chatHostStyles) {
      ov.style.cssText = ov._chatHostStylesPrev || "";
      ov._chatHostStyles = false;
      ov._chatHostStylesPrev = "";
    }
    if (!ov._chatHomeParent) return;
    if (ov.parentNode !== ov._chatHomeParent) {
      ov._chatHomeParent.appendChild(ov);
    }
  }

  function chatSplitPaneReady() {
    if (notesSubTab !== "chats") return false;
    var pane = document.getElementById("chat-thread-pane");
    var root = document.getElementById("notes-pane-chats");
    if (!pane || !root || root.classList.contains("hidden")) return false;
    document.documentElement.classList.add("notes-chats-tab");
    var cs = window.getComputedStyle(pane);
    if (cs.display === "none") {
      // принудительно включаем split, если CSS ещё не применился
      pane.style.display = "flex";
    }
    var rect = pane.getBoundingClientRect();
    return rect.width >= 80 && rect.height >= 80;
  }

  function applyChatHostedOverlayStyles(ov) {
    if (!ov) return;
    if (!ov._chatHostStyles) {
      ov._chatHostStylesPrev = ov.getAttribute("style") || "";
      ov._chatHostStyles = true;
    }
    ov.classList.add("chat-hosted-discussion");
    ov.style.setProperty("position", "absolute", "important");
    ov.style.setProperty("inset", "0px", "important");
    ov.style.setProperty("top", "0px", "important");
    ov.style.setProperty("right", "0px", "important");
    ov.style.setProperty("bottom", "0px", "important");
    ov.style.setProperty("left", "0px", "important");
    ov.style.setProperty("width", "100%", "important");
    ov.style.setProperty("height", "100%", "important");
    ov.style.setProperty("max-width", "none", "important");
    ov.style.setProperty("display", "flex", "important");
    ov.style.setProperty("visibility", "visible", "important");
    ov.style.setProperty("opacity", "1", "important");
    ov.style.setProperty("z-index", "5", "important");
    ov.style.setProperty("border", "none", "important");
    ov.style.setProperty("box-shadow", "none", "important");
    ov.style.setProperty("background", "var(--bg, #0a0a0a)", "important");
    ov.style.setProperty("pointer-events", "auto", "important");
  }

  function hostDiscussionInChatPane() {
    var ov = document.getElementById("note-discussion-overlay");
    var pane = document.getElementById("chat-thread-pane");
    if (!ov) return;
    if (!ov._chatHomeParent) ov._chatHomeParent = ov.parentNode;
    if (chatSplitPaneReady() && pane) {
      if (ov.parentNode !== pane) pane.appendChild(ov);
      document.documentElement.classList.add("notes-chats-tab");
      document.documentElement.classList.add("chats-split-open");
      document.documentElement.classList.add("note-discussion-open");
      var empty = document.getElementById("chat-thread-empty");
      if (empty) setHidden(empty, true);
      ov.classList.remove("hidden");
      ov.setAttribute("aria-hidden", "false");
      applyChatHostedOverlayStyles(ov);
      var sheet = ov.querySelector(".note-discussion-sheet");
      if (sheet) {
        sheet.style.maxWidth = "none";
        sheet.style.height = "100%";
        sheet.style.maxHeight = "100%";
        sheet.style.width = "100%";
      }
    } else {
      restoreDiscussionOverlayHost();
      document.documentElement.classList.remove("chats-split-open");
    }
  }

  function syncChatDiscussionTitle(title) {
    var label = document.getElementById("note-discussion-title-label");
    var t = String(title || "Чат").trim() || "Чат";
    if (label) label.textContent = t;
    var btn = document.getElementById("note-discussion-title");
    if (btn) {
      btn.disabled = true;
      btn.classList.add("note-pane-switch--plain");
      btn.title = t;
      btn.setAttribute("aria-expanded", "false");
    }
  }

  function sortChatThreadsCache() {
    chatThreadsCache.sort(function (a, b) {
      var pin = (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0);
      if (pin) return pin;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
  }

  function chatThreadHasMessages(thread) {
    if (!thread) return false;
    if (thread.has_messages === true) return true;
    if (Number(thread.message_count) > 0) return true;
    if (String(thread.last_preview || "").trim()) return true;
    return false;
  }

  function isEmptyChatDraft(thread) {
    if (!thread || chatThreadHasMessages(thread)) return false;
    if (thread.pinned || thread.title_locked) return false;
    var title = String(thread.title || "").trim() || "Новый чат";
    return title === "Новый чат";
  }

  function visibleChatThreads() {
    return (chatThreadsCache || []).filter(function (t) {
      return chatThreadHasMessages(t) || !isEmptyChatDraft(t);
    });
  }

  function findEmptyChatDraft(exceptId) {
    for (var i = 0; i < (chatThreadsCache || []).length; i++) {
      var t = chatThreadsCache[i];
      if (isEmptyChatDraft(t) && String(t.id) !== String(exceptId || "")) {
        return t;
      }
    }
    return null;
  }

  async function pruneEmptyChatDrafts(keepId) {
    var empties = (chatThreadsCache || []).filter(function (t) {
      var id = String(t.id);
      if (String(id) === String(keepId || "")) return false;
      if (!isEmptyChatDraft(t)) return false;
      return !!sessionChatDraftIds[id];
    });
    if (!empties.length) return;
    for (var i = 0; i < empties.length; i++) {
      var id = String(empties[i].id);
      try {
        await apiFetch("/chats/" + encodeURIComponent(id), { method: "DELETE" });
        delete sessionChatDraftIds[id];
      } catch (_) {}
    }
    chatThreadsCache = (chatThreadsCache || []).filter(function (t) {
      return !empties.some(function (e) {
        return String(e.id) === String(t.id);
      });
    });
    renderChatThreadsList();
    renderSidebarTrees();
  }

  function closeChatThreadMenus() {
    document.querySelectorAll(".chat-thread-menu:not(.hidden)").forEach(function (el) {
      el.classList.add("hidden");
      el.style.position = "";
      el.style.top = "";
      el.style.left = "";
      el.style.right = "";
      el.style.zIndex = "";
      if (el._homeParent && el.parentNode !== el._homeParent) {
        el._homeParent.appendChild(el);
      }
    });
    document.querySelectorAll(".chat-thread-more-btn[aria-expanded='true']").forEach(function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  var chatThreadMenuDocClickBound = false;
  function bindChatThreadMenuDocClickOnce() {
    if (chatThreadMenuDocClickBound) return;
    chatThreadMenuDocClickBound = true;
    document.addEventListener("click", function (e) {
      if (
        e.target &&
        e.target.closest &&
        (e.target.closest(".chat-thread-more-wrap") || e.target.closest(".chat-thread-menu"))
      ) {
        return;
      }
      closeChatThreadMenus();
    });
  }

  function syncChatDiscussionChrome() {
    var closeBtn = document.getElementById("note-discussion-close");
    if (closeBtn) {
      setHidden(closeBtn, notesSubTab === "chats" && isDesktopLayout());
    }
    document.documentElement.classList.toggle(
      "notes-chats-tab",
      currentTab === "notes" && notesSubTab === "chats"
    );
  }

  function upsertChatThreadCache(thread) {
    if (!thread || thread.id == null) return;
    var id = String(thread.id);
    var found = false;
    chatThreadsCache = (chatThreadsCache || []).map(function (t) {
      if (String(t.id) === id) {
        found = true;
        return Object.assign({}, t, thread);
      }
      return t;
    });
    if (!found) chatThreadsCache.unshift(thread);
    if (chatThreadHasMessages(thread)) delete sessionChatDraftIds[id];
    sortChatThreadsCache();
    renderChatThreadsList();
    if (String(activeChatThreadId) === id) {
      syncChatDiscussionTitle(thread.title);
    }
  }

  function renderChatThreadsList() {
    var list = document.getElementById("chat-threads-list");
    if (!list) return;
    bindChatThreadMenuDocClickOnce();
    closeChatThreadMenus();
    list.innerHTML = "";
    var rows = visibleChatThreads();
    if (!rows.length) {
      list.innerHTML =
        '<p class="muted empty-hint">Чатов пока нет. Нажмите «+», чтобы начать диалог с GPT.</p>';
      return;
    }
    rows.forEach(function (thread) {
      var id = String(thread.id);
      var row = document.createElement("div");
      row.className = "chat-thread-row";
      row.setAttribute("role", "listitem");
      row.setAttribute("data-chat-id", id);
      if (String(activeChatThreadId) === id) row.classList.add("is-active");
      var main = document.createElement("div");
      main.className = "chat-thread-row-main";
      var title = document.createElement("p");
      title.className = "chat-thread-row-title";
      if (thread.pinned) {
        var pin = document.createElement("span");
        pin.className = "chat-thread-pin";
        pin.setAttribute("aria-hidden", "true");
        pin.textContent = "📌";
        title.appendChild(pin);
      }
      title.appendChild(document.createTextNode(String(thread.title || "Новый чат")));
      var preview = document.createElement("p");
      preview.className = "chat-thread-row-preview";
      preview.textContent = String(thread.last_preview || "");
      main.appendChild(title);
      main.appendChild(preview);
      row.appendChild(main);

      var moreWrap = document.createElement("div");
      moreWrap.className = "chat-thread-more-wrap";
      var moreBtn = document.createElement("button");
      moreBtn.type = "button";
      moreBtn.className = "chat-thread-more-btn";
      moreBtn.setAttribute("aria-label", "Действия");
      moreBtn.setAttribute("aria-haspopup", "menu");
      moreBtn.setAttribute("aria-expanded", "false");
      moreBtn.innerHTML =
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
        '<circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/>' +
        "</svg>";
      var menu = document.createElement("div");
      menu.className = "chat-thread-menu note-card-menu hidden";
      menu.setAttribute("role", "menu");
      function addMenuItem(label, fn, danger) {
        var item = document.createElement("button");
        item.type = "button";
        item.className =
          "chat-thread-menu-item" + (danger ? " chat-thread-menu-item--danger" : "");
        item.setAttribute("role", "menuitem");
        item.textContent = label;
        item.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          closeChatThreadMenus();
          fn();
        });
        menu.appendChild(item);
      }
      addMenuItem("Переименовать", function () {
        renameChatThread(thread);
      });
      addMenuItem(thread.pinned ? "Открепить" : "Закрепить чат", function () {
        toggleChatThreadPin(thread, !thread.pinned);
      });
      addMenuItem("Поделиться", function () {
        shareChatThread(thread);
      });
      addMenuItem("Добавить к заметке", function () {
        addChatThreadToNote(thread);
      });
      addMenuItem(
        "Удалить",
        function () {
          deleteChatThread(id);
        },
        true
      );
      moreBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var wasOpen = !menu.classList.contains("hidden");
        closeChatThreadMenus();
        closeNoteCardMenus();
        if (wasOpen) return;
        if (!menu._homeParent) menu._homeParent = menu.parentNode;
        document.body.appendChild(menu);
        menu.classList.remove("hidden");
        moreBtn.setAttribute("aria-expanded", "true");
        positionNoteCardMenu(moreBtn, menu);
      });
      moreWrap.appendChild(moreBtn);
      moreWrap.appendChild(menu);
      row.appendChild(moreWrap);

      row.addEventListener("click", function () {
        openChatThread(thread);
      });
      list.appendChild(row);
    });
  }

  async function renameChatThread(thread) {
    if (!thread || thread.id == null) return;
    var next = await promptDialog("Название чата", String(thread.title || "Новый чат"));
    if (next == null) return;
    next = String(next).trim();
    if (!next) {
      alert("Введите название");
      return;
    }
    try {
      var data = await apiFetch("/chats/" + encodeURIComponent(String(thread.id)), {
        method: "PATCH",
        body: JSON.stringify({ title: next }),
      });
      if (data && data.chat) upsertChatThreadCache(data.chat);
      else {
        upsertChatThreadCache(Object.assign({}, thread, { title: next }));
      }
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function toggleChatThreadPin(thread, pinned) {
    if (!thread || thread.id == null) return;
    try {
      var data = await apiFetch("/chats/" + encodeURIComponent(String(thread.id)), {
        method: "PATCH",
        body: JSON.stringify({ pinned: !!pinned }),
      });
      if (data && data.chat) upsertChatThreadCache(data.chat);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function formatChatThreadTranscript(thread) {
    var id = String((thread && thread.id) || "");
    if (!id) return "";
    var data = await apiFetch(
      "/notes/chat/" + encodeURIComponent(id) + "/comments",
      { method: "GET" }
    );
    var rows = (data && data.comments) || [];
    var lines = ["# " + String((thread && thread.title) || "Чат"), ""];
    rows.forEach(function (c) {
      if (!c) return;
      var who = discussionAuthorLabel(c);
      var body = String(c.body || "").trim();
      if (!body) return;
      lines.push("**" + who + ":** " + body);
      lines.push("");
    });
    return lines.join("\n").trim();
  }

  async function shareChatThread(thread) {
    if (!thread || thread.id == null) return;
    try {
      var id = String(thread.id);
      ensureChatShareWrap(id);
      var wrap = document.getElementById("note-editor-more-wrap");
      if (!wrap) return;
      try {
        var data = await apiFetch(
          "/notes/chat/" + encodeURIComponent(id) + "/share",
          { method: "GET" }
        );
        applyShareState(wrap, data);
      } catch (_) {}
      openShareAccessSheet();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function addChatThreadToNote(thread) {
    try {
      var text = await formatChatThreadTranscript(thread);
      if (!text) {
        showNoteToast("В чате пока нет сообщений");
        return;
      }
      openNoteLinkPicker(text);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function loadChatThreads() {
    if (chatThreadsLoading) return;
    chatThreadsLoading = true;
    try {
      var data = await apiFetch("/chats?limit=100", { method: "GET" });
      chatThreadsCache = (data && data.chats) || [];
      sortChatThreadsCache();
      renderChatThreadsList();
      renderSidebarTrees();
    } catch (e) {
      var list = document.getElementById("chat-threads-list");
      if (list && !visibleChatThreads().length) {
        list.innerHTML = '<p class="error"></p>';
        list.querySelector(".error").textContent = e.message || String(e);
      }
    } finally {
      chatThreadsLoading = false;
    }
  }

  async function deleteChatThread(id) {
    await apiFetch("/chats/" + encodeURIComponent(String(id)), { method: "DELETE" });
    chatThreadsCache = (chatThreadsCache || []).filter(function (t) {
      return String(t.id) !== String(id);
    });
    if (String(activeChatThreadId) === String(id)) {
      activeChatThreadId = "";
      if (isNoteDiscussionOpen()) closeNoteDiscussion(true);
      else if (isDesktopLayout() && notesSubTab === "chats") {
        restoreDiscussionOverlayHost();
        document.documentElement.classList.remove("chats-split-open");
        clearChatShareWrap();
        ensureDesktopChatComposer();
      } else {
        restoreDiscussionOverlayHost();
        document.documentElement.classList.remove("chats-split-open");
        var empty = document.getElementById("chat-thread-empty");
        if (empty) setHidden(empty, false);
        clearChatShareWrap();
      }
    }
    renderChatThreadsList();
  }

  async function openChatThread(thread) {
    if (!thread || thread.id == null) return;
    var id = String(thread.id);
    var prevId = activeChatThreadId;
    activeChatThreadId = id;
    ensureChatShareWrap(id);
    var wrapEarly = document.getElementById("note-editor-more-wrap");
    if (wrapEarly && Array.isArray(thread.members)) {
      wrapEarly._noteMembers = thread.members;
    }
    renderChatThreadsList();
    await pruneEmptyChatDrafts(id);
    if (prevId && String(prevId) !== id) {
      /* previous empty draft pruned above */
    }
    if (!document.getElementById("note-editor-more-wrap")) {
      ensureChatShareWrap(id);
    }
    hostDiscussionInChatPane();
    var panel = document.getElementById("note-editor-comments");
    var listEl = panel && panel.querySelector(".note-comments-list");
    try {
      await loadNoteComments("chat", id, listEl);
    } catch (e) {
      alert(e.message || String(e));
      return;
    }
    try {
      var mem = await apiFetch("/chats/" + encodeURIComponent(id) + "/members", {
        method: "GET",
      });
      var wrapMem = document.getElementById("note-editor-more-wrap");
      if (wrapMem && mem) {
        wrapMem._noteMembers = (mem.chat && mem.chat.members) || mem.members || [];
        if (mem.chat) upsertChatThreadCache(mem.chat);
      }
    } catch (_) {}
    if (!document.getElementById("note-editor-more-wrap")) {
      ensureChatShareWrap(id);
    }
    hostDiscussionInChatPane();
    openNoteDiscussion({
      mode: "gpt",
      focus: !chatSplitPaneReady(),
      keepSheet: true,
    });
    hostDiscussionInChatPane();
    syncChatDiscussionTitle(thread.title);
    renderNoteAskModes();
    syncChatDiscussionChrome();
    syncNoteDiscussionOpenClass();
    syncAppOverlay();
    window.requestAnimationFrame(function () {
      hostDiscussionInChatPane();
    });
  }

  async function createAndOpenChatThread() {
    try {
      var blank = findEmptyChatDraft(activeChatThreadId);
      if (blank) {
        await openChatThread(blank);
        return;
      }
      if (activeChatThreadId) {
        var current = null;
        for (var i = 0; i < (chatThreadsCache || []).length; i++) {
          if (String(chatThreadsCache[i].id) === String(activeChatThreadId)) {
            current = chatThreadsCache[i];
            break;
          }
        }
        if (current && !chatThreadHasMessages(current)) {
          await openChatThread(current);
          return;
        }
      }
      await pruneEmptyChatDrafts("");
      var data = await apiFetch("/chats", {
        method: "POST",
        body: JSON.stringify({}),
      });
      var thread = data && data.chat;
      if (!thread) throw new Error("Не удалось создать чат");
      upsertChatThreadCache(thread);
      sessionChatDraftIds[String(thread.id)] = 1;
      await openChatThread(thread);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function ensureDesktopChatComposer() {
    if (leavingChatsTab || notesSubTab !== "chats") return;
    document.documentElement.classList.add("notes-chats-tab");
    if (!chatSplitPaneReady() && !isDesktopLayout()) return;
    if (activeChatThreadId && isNoteDiscussionOpen()) {
      hostDiscussionInChatPane();
      return;
    }
    var blank = findEmptyChatDraft("");
    if (blank) await openChatThread(blank);
    else await createAndOpenChatThread();
  }

  function setNotesSubTab(name) {
    var next = name || "notes";
    if (next === "summaries") next = "transcriptions";
    if (notesSubTab === "chats" && next !== "chats") {
      leaveChatThreadsMode();
    }
    notesSubTab = next;
    document.querySelectorAll("#panel-notes .subtab-btn").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-subtab") === next);
    });
    setHidden(document.getElementById("notes-pane-notes"), next !== "notes");
    setHidden(document.getElementById("notes-pane-chats"), next !== "chats");
    setHidden(document.getElementById("notes-pane-transcriptions"), next !== "transcriptions");
    var filterBar = document.querySelector("#panel-notes .notes-filter-bar");
    setHidden(filterBar, next === "chats");
    syncChatDiscussionChrome();
    if (next === "chats") {
      var openWrap = document.getElementById("note-editor-more-wrap");
      if (isNoteDiscussionOpen() && (!openWrap || openWrap._shareKind !== "chat")) {
        closeNoteDiscussion(true);
      }
      document.documentElement.classList.add("notes-chats-tab");
      loadChatThreads().then(function () {
        window.requestAnimationFrame(function () {
          ensureDesktopChatComposer();
        });
      });
    }
    syncNotesCreateFab();
    renderSidebarTrees();
  }
  function clearNoteEditorToolbarWrap() {
    var toolbarWrap = document.getElementById("note-editor-toolbar-wrap");
    var formatHost = document.getElementById("note-editor-format-toolbar-host");
    if (formatHost) formatHost.innerHTML = "";
    if (toolbarWrap) setHidden(toolbarWrap, true);
    syncNoteGptToolbarButton(false);
  }

  function clearNoteEditorTagsWrap() {
    var tagsWrap = document.getElementById("note-editor-tags-wrap");
    if (!tagsWrap) return;
    if (typeof tagsWrap._tagPickerUnmount === "function") {
      try {
        tagsWrap._tagPickerUnmount();
      } catch (_) {}
      tagsWrap._tagPickerUnmount = null;
    }
    tagsWrap.innerHTML = "";
    setHidden(tagsWrap, true);
    hideShareControls();
  }

  function closeNoteMoreMenu() {
    var menu = document.getElementById("note-editor-more-menu");
    var btn = document.getElementById("note-editor-more-btn");
    if (menu) {
      menu.classList.add("hidden");
      menu.style.position = "";
      menu.style.top = "";
      menu.style.right = "";
      menu.style.left = "";
      menu.style.zIndex = "";
    }
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function hideShareControls() {
    closeNoteMoreMenu();
    closeShareAccessSheet();
    hideNoteCommentsPanel();
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) {
      wrap._shareKind = "";
      wrap._shareId = "";
      wrap._shareShared = false;
      wrap._shareUrl = "";
      wrap._shareAccess = "view";
      wrap._shareSheetIds = [];
      wrap._paeiRunning = false;
      wrap._commentChairId = null;
      wrap._commentDraft = null;
      wrap._noteMembers = [];
      wrap._noteRevision = 1;
      wrap._noteUpdatedAt = "";
      wrap._isNoteOwner = true;
      wrap._isKnowledge = false;
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    }
  }

  var noteCollabState = {
    timer: null,
    noteId: "",
    applying: false,
    lastTypedAt: 0,
    photoBlobs: {},
  };

  function noteMemberInitials(member) {
    var name = String((member && (member.name || member.username)) || "").trim();
    if (!name) return "?";
    var parts = name.replace(/^@/, "").split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
    return name.replace(/^@/, "").slice(0, 2).toUpperCase();
  }

  function applyTelegramAvatar(img, opts) {
    opts = opts || {};
    var uid = String(opts.userId || "");
    var uname = String(opts.username || "")
      .replace(/^@/, "")
      .trim()
      .toLowerCase();
    if (!img || (!uid && !uname)) return;
    if (uid && noteCollabState.photoBlobs[uid]) {
      img.src = noteCollabState.photoBlobs[uid];
      img.classList.add("has-photo");
      return;
    }
    var unameKey = uname ? "u:" + uname : "";
    if (unameKey && noteCollabState.photoBlobs[unameKey]) {
      img.src = noteCollabState.photoBlobs[unameKey];
      img.classList.add("has-photo");
      return;
    }
    function setBlob(key, blob) {
      if (!key || !blob || !blob.size) return false;
      var url = URL.createObjectURL(blob);
      noteCollabState.photoBlobs[key] = url;
      img.src = url;
      img.classList.add("has-photo");
      return true;
    }
    function fetchPhoto(path, key) {
      return fetch(API + path, {
        credentials: "same-origin",
        headers: authHeaders(),
      })
        .then(function (res) {
          return res.ok ? res.blob() : null;
        })
        .then(function (blob) {
          return setBlob(key, blob);
        })
        .catch(function () {
          return false;
        });
    }
    if (uid) {
      fetchPhoto("/users/" + encodeURIComponent(uid) + "/photo", uid).then(function (ok) {
        if (!ok && uname) {
          fetchPhoto("/contacts/telegram-photo/" + encodeURIComponent(uname), unameKey);
        }
      });
      return;
    }
    fetchPhoto("/contacts/telegram-photo/" + encodeURIComponent(uname), unameKey);
  }

  function applyNoteMemberPhoto(img, userId) {
    applyTelegramAvatar(img, { userId: userId });
  }

  function noteMemberAvatarNode(member, opts) {
    opts = opts || {};
    var el = document.createElement("span");
    el.className = "note-member-avatar" + (opts.online ? " is-online" : "");
    if (member && member.color) el.style.setProperty("--note-member-color", member.color);
    el.title = String((member && (member.name || member.username)) || "Участник");
    var img = document.createElement("img");
    img.alt = "";
    el.appendChild(img);
    var fallback = document.createElement("span");
    fallback.className = "note-member-avatar-fallback";
    fallback.textContent = noteMemberInitials(member);
    el.appendChild(fallback);
    if (member && member.user_id) applyNoteMemberPhoto(img, member.user_id);
    return el;
  }

  function buildNoteCardAvatars(members) {
    var list = (members || []).filter(function (m) {
      return m && m.user_id;
    });
    if (list.length < 2) return null;
    var wrap = document.createElement("div");
    wrap.className = "note-card-avatars";
    list.slice(0, 4).forEach(function (m) {
      wrap.appendChild(noteMemberAvatarNode(m));
    });
    if (list.length > 4) {
      var more = document.createElement("span");
      more.className = "note-member-avatar note-member-avatar-more";
      more.textContent = "+" + (list.length - 4);
      wrap.appendChild(more);
    }
    return wrap;
  }

  function syncNoteCardActionsPad(inner, actions) {
    if (!inner || !actions) return;
    var w = Math.ceil(actions.getBoundingClientRect().width || 0);
    inner.style.paddingRight = Math.max(44, w + 10) + "px";
  }

  function appendNoteCardAvatars(inner, members) {
    var wrap = buildNoteCardAvatars(members);
    if (!wrap || !inner) return;
    var actions = inner.querySelector(".note-card-actions");
    if (actions) {
      var menuWraps = actions.querySelectorAll(".note-card-menu-wrap");
      var moreWrap = menuWraps.length ? menuWraps[menuWraps.length - 1] : null;
      if (moreWrap) actions.insertBefore(wrap, moreWrap);
      else actions.appendChild(wrap);
      syncNoteCardActionsPad(inner, actions);
      return;
    }
    var title = inner.querySelector(".note-card-excerpt");
    if (title) {
      var row = document.createElement("div");
      row.className = "note-card-title-row";
      title.parentNode.insertBefore(row, title);
      row.appendChild(title);
      row.appendChild(wrap);
    } else {
      inner.appendChild(wrap);
    }
  }

  function renderOpenNoteMembers(members, peers) {
    var row = document.getElementById("note-editor-members");
    if (!row) return;
    var list = members || [];
    var online = peers || [];
    var onlineIds = {};
    online.forEach(function (p) {
      onlineIds[String(p.user_id)] = p;
    });
    row.innerHTML = "";
    if (list.length <= 1 && !online.length) {
      row.classList.add("hidden");
      return;
    }
    row.classList.remove("hidden");
    var people = document.createElement("div");
    people.className = "note-editor-members-people";
    list.forEach(function (m) {
      var chip = document.createElement("span");
      chip.className = "note-editor-member-chip";
      var live = onlineIds[String(m.user_id)];
      chip.appendChild(noteMemberAvatarNode(m, { online: !!live }));
      var label = document.createElement("span");
      label.textContent = m.is_owner
        ? (m.name || "Автор") + " · автор"
        : m.name || m.username || "Участник";
      chip.appendChild(label);
      people.appendChild(chip);
    });
    row.appendChild(people);
    if (online.length) {
      var live = document.createElement("p");
      live.className = "note-editor-members-live";
      live.textContent =
        online.length === 1
          ? online[0].name + " сейчас в заметке"
          : online
              .map(function (p) {
                return p.name;
              })
              .join(", ") + " сейчас в заметке";
      row.appendChild(live);
    }
  }

  function ensureNoteCollabOverlay() {
    var pad = document.querySelector("#note-editor-overlay .note-editor-pad--body");
    if (!pad) return null;
    var overlay = document.getElementById("note-collab-cursors");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "note-collab-cursors";
      overlay.className = "note-collab-cursors";
      pad.style.position = "relative";
      pad.appendChild(overlay);
    }
    return overlay;
  }

  function paintRemoteCursors(peers) {
    var overlay = ensureNoteCollabOverlay();
    var editor = getActiveNoteRichEditor();
    if (!overlay) return;
    overlay.innerHTML = "";
    if (!editor || typeof editor.coordsAtPos !== "function") return;
    var pad = overlay.parentNode;
    if (!pad || !pad.getBoundingClientRect) return;
    var padRect = pad.getBoundingClientRect();
    (peers || []).forEach(function (p) {
      if (p.cursor == null) return;
      var coords = null;
      try {
        coords = editor.coordsAtPos(p.cursor);
      } catch (_) {
        return;
      }
      if (!coords) return;
      var caret = document.createElement("div");
      caret.className = "note-collab-caret";
      caret.style.left = coords.left - padRect.left + pad.scrollLeft + "px";
      caret.style.top = coords.top - padRect.top + pad.scrollTop + "px";
      caret.style.height = Math.max(16, coords.bottom - coords.top) + "px";
      caret.style.background = p.color || "#529ef4";
      var flag = document.createElement("span");
      flag.className = "note-collab-caret-label";
      flag.style.background = p.color || "#529ef4";
      flag.textContent = String(p.name || "Участник").split(" ")[0];
      caret.appendChild(flag);
      overlay.appendChild(caret);
    });
  }

  function stopNoteCollab() {
    if (noteCollabState.timer) {
      clearInterval(noteCollabState.timer);
      noteCollabState.timer = null;
    }
    var id = noteCollabState.noteId;
    noteCollabState.noteId = "";
    if (id) {
      apiFetch("/notes/local/" + encodeURIComponent(id) + "/collab", {
        method: "DELETE",
      }).catch(function () {});
    }
    var overlay = document.getElementById("note-collab-cursors");
    if (overlay) overlay.innerHTML = "";
  }

  function applyRemoteSharedNote(item, force) {
    if (!item) return;
    if (!force) {
      var draftId = currentOpenNoteId() || (item && item.id);
      var draft = readLocalNoteDraft(draftId);
      var api = noteDraftsApi();
      var remoteBody = item.body || item.description;
      if (
        draft &&
        api &&
        !(api.bodiesEqual(draft.title, item.title) && api.bodiesEqual(draft.body, remoteBody))
      ) {
        var base = noteEditorBaseline();
        if (base && notePayloadEquals(base.title, base.description, item.title, remoteBody)) {
          adoptNoteRevision(item, draftId);
        }
        return;
      }
    }
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) {
      var apiRev = noteDraftsApi();
      var nextRev = apiRev ? apiRev.revNum(item.revision) : Number(item.revision || 0);
      var curRev = apiRev ? apiRev.revNum(wrap._noteRevision) : Number(wrap._noteRevision || 0);
      if (nextRev >= curRev) {
        wrap._noteRevision = item.revision || wrap._noteRevision;
        wrap._noteUpdatedAt = item.updated_at || wrap._noteUpdatedAt;
      }
      wrap._noteMembers = item.members || wrap._noteMembers || [];
      wrap._isNoteOwner = item.is_owner !== false;
    }
    prependLocalInCache(item);
    var typing = Date.now() - noteCollabState.lastTypedAt < 1600;
    if (typing && !force) return;
    var titleInput = document.getElementById("note-editor-title-input");
    noteCollabState.applying = true;
    try {
      if (titleInput && item.title != null && titleInput.value !== String(item.title || "")) {
        titleInput.value = String(item.title || "");
        titleInput.dispatchEvent(new Event("input"));
      }
      var nextBody = String(item.body || item.description || "");
      noteSheetState.primaryBody = nextBody;
      var main = findNoteSheet(NOTE_SHEET_MAIN);
      if (main) {
        main.body = nextBody;
        main.description = nextBody;
        main.revision = item.revision || main.revision;
      }
      var left = getLeftNoteRichEditor();
      if (
        isPrimarySheetId(noteSheetState.leftId) &&
        left &&
        typeof left.getHtml === "function" &&
        typeof left.setHtml === "function"
      ) {
        if (left.getHtml() !== nextBody) left.setHtml(nextBody, { preserveCursor: true });
      }
    } finally {
      noteCollabState.applying = false;
    }
  }

  function noteCollabTick() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var id = noteCollabState.noteId;
    if (!wrap || !id || String(wrap._shareId) !== String(id)) {
      stopNoteCollab();
      return;
    }
    var editor = isPrimarySheetId(noteSheetState.leftId)
      ? getLeftNoteRichEditor()
      : null;
    var cursor = editor && typeof editor.getCursor === "function" ? editor.getCursor() : null;
    apiFetch("/notes/local/" + encodeURIComponent(id) + "/collab", {
      method: "POST",
      body: JSON.stringify({ cursor: cursor }),
    })
      .then(function (data) {
        if (noteCollabState.noteId !== String(id)) return;
        var item = data && data.item;
        var peers = (data && data.peers) || [];
        var members = (data && data.members) || (wrap._noteMembers || []);
        wrap._noteMembers = members;
        renderOpenNoteMembers(members, peers);
        paintRemoteCursors(peers);
        if (!item) return;
        var remoteRev = Number(item.revision || 0);
        var localRev = Number(wrap._noteRevision || 0);
        if (remoteRev > localRev) applyRemoteSharedNote(item, false);
        else if (remoteRev === localRev && item.updated_at) {
          wrap._noteUpdatedAt = item.updated_at;
        }
      })
      .catch(function () {});
  }

  function startNoteCollab(noteId, note) {
    stopNoteCollab();
    if (!noteId) return;
    noteCollabState.noteId = String(noteId);
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && note) {
      wrap._noteMembers = note.members || [];
      wrap._noteRevision = note.revision || 1;
      wrap._noteUpdatedAt = note.updated_at || "";
      wrap._isNoteOwner = note.is_owner !== false;
      renderOpenNoteMembers(wrap._noteMembers, []);
    }
    noteCollabTick();
    noteCollabState.timer = setInterval(noteCollabTick, 1400);
  }

  function pendingNoteIdFromLocation() {
    try {
      var q = new URLSearchParams(location.search || "");
      var n = q.get("note") || q.get("n");
      if (n && /^\d+$/.test(n)) return n;
    } catch (_) {}
    var hash = String(location.hash || "").replace(/^#/, "");
    var hm = hash.match(/(?:^|&)note=(\d+)/) || hash.match(/^note=(\d+)/);
    if (hm) return hm[1];
    var tg = window.Telegram && window.Telegram.WebApp;
    var start = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    if (start) {
      var sm = String(start).match(/^note[_-]?(\d+)$/i);
      if (sm) return sm[1];
    }
    return "";
  }

  function pendingTaskIdFromLocation() {
    try {
      var q = new URLSearchParams(location.search || "");
      var t = q.get("task") || q.get("t");
      if (t && /^\d+$/.test(t)) return t;
    } catch (_) {}
    var hash = String(location.hash || "").replace(/^#/, "");
    var hm = hash.match(/(?:^|&)task=(\d+)/) || hash.match(/^task=(\d+)/);
    if (hm) return hm[1];
    var tg = window.Telegram && window.Telegram.WebApp;
    var start = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    if (start) {
      var sm = String(start).match(/^task[_-]?(\d+)$/i);
      if (sm) return sm[1];
    }
    return "";
  }

  function pendingChatIdFromLocation() {
    try {
      var q = new URLSearchParams(location.search || "");
      var c = q.get("chat") || q.get("c");
      if (c && String(c).trim()) return String(c).trim();
    } catch (_) {}
    var hash = String(location.hash || "").replace(/^#/, "");
    var hm = hash.match(/(?:^|&)chat=([^&]+)/) || hash.match(/^chat=([^&]+)/);
    if (hm) return decodeURIComponent(hm[1]);
    var tg = window.Telegram && window.Telegram.WebApp;
    var start = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    if (start) {
      var sm = String(start).match(/^chat[_-](.+)$/i);
      if (sm) return sm[1];
    }
    return "";
  }

  function copyShareUrl(url) {
    if (!url) return Promise.resolve();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(url);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = url;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        resolve();
      } catch (e) {
        reject(e);
      }
      document.body.removeChild(ta);
    });
  }

  function shareHaptic() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  }

  function fillNoteMoreMenu() {
    var menu = document.getElementById("note-editor-more-menu");
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!menu || !wrap) return;
    menu.innerHTML = "";
    var shared = !!wrap._shareShared;

    function addItem(label, onClick, disabled) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-more-menu-item";
      btn.textContent = label;
      if (disabled) {
        btn.disabled = true;
        btn.classList.add("is-busy");
      } else {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          onClick();
        });
      }
      menu.appendChild(btn);
    }

    var mobileTocMenu =
      !window.matchMedia || window.matchMedia("(max-width: 959px)").matches;
    if (mobileTocMenu && getActiveNoteEditorPage()) {
      addItem("Оглавление", function () {
        closeNoteMoreMenu();
        openActiveNoteEditorToc();
      });
    }

    if (noteSheetsEnabled()) {
      addItem("Новый лист", function () {
        closeNoteMoreMenu();
        createNoteSheet({ openOn: "left" });
      });
    }

    var shareKind = String(wrap._shareKind || "");
    var shareId = wrap._shareId ? String(wrap._shareId) : "";
    if (shareId && (shareKind === "local" || shareKind === "journal")) {
      addItem("Скачать PDF", function () {
        closeNoteMoreMenu();
        requestNotePdf(shareKind, shareId).catch(function (err) {
          alert(err.message || String(err));
        });
      });
    }

    if (wrap._isKnowledge && wrap._isNoteOwner !== false) {
      addItem("Поделиться с командой", function () {
        closeNoteMoreMenu();
        openNoteTeamShareSheet();
      });
    }

    if (!shared) {
      addItem("Поделиться", function () {
        closeNoteMoreMenu();
        openShareAccessSheet();
      });
      return;
    }
    addItem(
      wrap._shareAccess === "comment" ? "Сменить доступ · комментарии" : "Сменить доступ · просмотр",
      function () {
        closeNoteMoreMenu();
        openShareAccessSheet();
      }
    );
    addItem("Скопировать ссылку", function () {
      copyExistingShareLink();
    });
    addItem("Закрыть доступ", function () {
      revokeShareLink();
    });
  }

  var GPT_MODEL_LS = "leo_gpt_model";
  var GPT_MODEL_DEFAULT = "openai/gpt-4.1";
  var gptModelsState = { loaded: false, loading: false, models: [], defaultId: GPT_MODEL_DEFAULT };
  var userAgentsState = { loaded: false, loading: false, agents: [] };
  var composerAgentModel = "";

  function noteComposerAsset(name) {
    return "/webapp/icons/" + name + ".svg?v=" + WEBAPP_BUILD;
  }

  function noteAskModeLabel(mode) {
    if (mode === "gpt") return "GPT";
    if (mode === "research") return "Research";
    if (mode === "comment") return "Текст";
    if (mode === "paie") return "PAIE";
    var agent = findUserAgent(mode);
    return (agent && agent.title) || "Агент";
  }

  function noteAskUsesChips(mode) {
    return mode === "gpt" || mode === "research" || isCustomAskMode(mode);
  }

  function noteAskUsesComposerEditor(mode) {
    return noteAskUsesChips(mode) || mode === "comment";
  }

  function noteAskAllowsMentions(mode) {
    return isChatAskMode(mode) || mode === "comment";
  }

  function noteAskPlaceholder(mode) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var quoted = !!(wrap && wrap._commentDraft && String(wrap._commentDraft.quote || "").trim());
    var isChatThread = !!(wrap && wrap._shareKind === "chat");
    if (isChatAskMode(mode)) {
      var name = noteAskModeLabel(mode);
      if (gptComposerChipCount(noteGptEditor())) {
        return "Спросите " + name + " про выделенный текст";
      }
      return isChatThread
        ? "Задайте вопрос " + name
        : "Свободный запрос к " + name + " по заметке";
    }
    if (mode === "research") {
      return gptComposerChipCount(noteGptEditor())
        ? "Спросите Research про выделенный текст"
        : "Исследуйте тему заметки в вебе";
    }
    if (mode === "comment") {
      return quoted ? "Комментарий к выделенному тексту" : "Напишите комментарий";
    }
    return quoted
      ? "Уточнение по выделенному тексту"
      : "Уточнение или новая информация — CHAIR ответит";
  }

  var GPT_CHIP_MAX = 5;
  var GPT_CHIP_PREVIEW = 36;
  var GPT_QUOTE_MAX = 2000;

  function noteGptEditor() {
    return document.getElementById("note-paie-reply-editor");
  }

  function clipChipPreview(text) {
    var raw = String(text || "").replace(/\s+/g, " ").trim();
    if (raw.length <= GPT_CHIP_PREVIEW) return raw;
    return raw.slice(0, GPT_CHIP_PREVIEW - 1) + "…";
  }

  function clipGptQuote(text) {
    var raw = String(text || "").replace(/\s+/g, " ").trim();
    if (raw.length > GPT_QUOTE_MAX) return raw.slice(0, GPT_QUOTE_MAX - 1) + "…";
    return raw;
  }

  function gptComposerChipCount(ed) {
    return ed ? ed.querySelectorAll(".note-gpt-quote-chip").length : 0;
  }

  function gptComposerIsEmpty(ed) {
    if (!ed) return true;
    if (ed.querySelector(".note-gpt-quote-chip")) return false;
    if (ed.querySelector(".note-discuss-mention")) return false;
    return !String(ed.innerText || "").replace(/\u00a0/g, " ").trim();
  }

  function normalizeEmptyGptComposer(ed) {
    if (!ed || !gptComposerIsEmpty(ed)) return false;
    var html = String(ed.innerHTML || "")
      .replace(/<br\s*\/?>/gi, "")
      .replace(/&nbsp;/gi, "")
      .replace(/\u00a0/g, "")
      .replace(/<div><\/div>/gi, "")
      .replace(/<p><\/p>/gi, "")
      .trim();
    if (!html) {
      if (ed.innerHTML !== "") {
        ed.innerHTML = "";
        return true;
      }
      return false;
    }
    return false;
  }

  function placeComposerCaretAtStart(ed) {
    if (!ed || document.activeElement !== ed) return;
    try {
      var sel = window.getSelection();
      if (!sel) return;
      var range = document.createRange();
      range.selectNodeContents(ed);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (_) {}
  }

  function syncGptEditorEmpty(ed) {
    ed = ed || noteGptEditor();
    if (!ed) return;
    var cleared = normalizeEmptyGptComposer(ed);
    var empty = gptComposerIsEmpty(ed);
    ed.classList.toggle("is-empty", empty);
    if (cleared && empty) placeComposerCaretAtStart(ed);
  }

  function clearGptComposer() {
    var ed = noteGptEditor();
    if (!ed) return;
    ed.innerHTML = "";
    ed._savedRange = null;
    syncGptEditorEmpty(ed);
    autosizeNoteComposer(ed);
  }

  function composerEditorValue(el) {
    if (!el) return "";
    if (typeof el.value === "string" && !el.isContentEditable) return el.value || "";
    return String(el.innerText || "").replace(/\u00a0/g, " ");
  }

  function serializeGptComposer(ed) {
    ed = ed || noteGptEditor();
    if (!ed) return { text: "", quotes: [] };
    var quotes = [];
    var parts = [];
    function walk(node) {
      if (node.nodeType === 3) {
        parts.push(node.nodeValue);
        return;
      }
      if (node.nodeType !== 1) return;
      if (node.classList && node.classList.contains("note-gpt-quote-chip")) {
        var q = String(node.getAttribute("data-quote") || "").trim();
        if (q) {
          quotes.push(q);
          parts.push("«" + q + "»");
        }
        return;
      }
      if (node.classList && node.classList.contains("note-discuss-mention")) {
        var mention = String(node.getAttribute("data-mention") || node.textContent || "").trim();
        if (mention) parts.push(mention.indexOf("@") === 0 ? mention : "@" + mention);
        return;
      }
      if (node.nodeName === "BR") {
        parts.push("\n");
        return;
      }
      var block = node.nodeName === "DIV" || node.nodeName === "P";
      if (block && parts.length && parts[parts.length - 1] !== "\n") parts.push("\n");
      var kids = node.childNodes;
      for (var i = 0; i < kids.length; i++) walk(kids[i]);
    }
    var rootKids = ed.childNodes;
    for (var r = 0; r < rootKids.length; r++) walk(rootKids[r]);
    var text = parts
      .join("")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    return { text: text, quotes: quotes };
  }

  function cloneGptEditorRange(range, ed) {
    if (!range || !ed) return null;
    try {
      var root = range.commonAncestorContainer;
      if (root !== ed && !ed.contains(root)) return null;
      return range.cloneRange();
    } catch (_) {
      return null;
    }
  }

  function rememberGptEditorRange() {
    var ed = noteGptEditor();
    if (!ed || ed._ignoreRange) return;
    if (document.activeElement !== ed) return;
    var sel = window.getSelection && window.getSelection();
    if (!sel || !sel.rangeCount) return;
    var saved = cloneGptEditorRange(sel.getRangeAt(0), ed);
    if (saved) ed._savedRange = saved;
  }

  function restoreGptEditorRange(ed, preferred) {
    var sel = window.getSelection && window.getSelection();
    if (!ed || !sel) return;
    sel.removeAllRanges();
    var range = cloneGptEditorRange(preferred || ed._savedRange, ed);
    if (range) {
      try {
        sel.addRange(range);
        return;
      } catch (_) {}
    }
    range = document.createRange();
    range.selectNodeContents(ed);
    range.collapse(false);
    sel.addRange(range);
  }

  function insertGptQuoteChip(quote) {
    var ed = noteGptEditor();
    var q = clipGptQuote(quote);
    if (!ed || !q) return false;
    if (gptComposerChipCount(ed) >= GPT_CHIP_MAX) {
      showNoteToast("Можно добавить не больше 5 отрывков");
      return false;
    }
    var saved = cloneGptEditorRange(ed._savedRange, ed);
    var chip = document.createElement("span");
    chip.className = "note-gpt-quote-chip";
    chip.setAttribute("data-quote", q);
    chip.setAttribute("contenteditable", "false");
    chip.textContent = clipChipPreview(q);
    ed._ignoreRange = true;
    try {
      try {
        ed.focus();
      } catch (_) {}
      if (saved) ed._savedRange = saved;
      restoreGptEditorRange(ed, saved);
      var sel = window.getSelection();
      var range = sel && sel.rangeCount ? cloneGptEditorRange(sel.getRangeAt(0), ed) : null;
      if (!range) {
        range = document.createRange();
        range.selectNodeContents(ed);
        range.collapse(false);
      }
      range.deleteContents();
      var spaceAfter = document.createTextNode("\u00a0");
      range.insertNode(spaceAfter);
      range.insertNode(chip);
      var caret = document.createRange();
      caret.setStartAfter(spaceAfter);
      caret.collapse(true);
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(caret);
      }
      ed._savedRange = caret.cloneRange();
    } finally {
      ed._ignoreRange = false;
    }
    syncGptEditorEmpty(ed);
    autosizeNoteComposer(ed);
    ed.setAttribute("data-placeholder", noteAskPlaceholder("gpt"));
    syncNotePaieReplyForm();
    return true;
  }

  function adjacentGptChip(ed, dir) {
    var sel = window.getSelection();
    if (!ed || !sel || !sel.isCollapsed || !sel.rangeCount) return null;
    var range = sel.getRangeAt(0);
    var node = range.startContainer;
    var offset = range.startOffset;
    function isChip(n) {
      return !!(
        n &&
        n.nodeType === 1 &&
        n.classList &&
        (n.classList.contains("note-gpt-quote-chip") || n.classList.contains("note-discuss-mention"))
      );
    }
    function isBlankText(n) {
      return !!(n && n.nodeType === 3 && !String(n.nodeValue || "").replace(/[\s\u00a0]/g, ""));
    }
    function walk(start) {
      var n = start;
      while (n) {
        if (isChip(n)) return n;
        if (n.nodeType === 3 && !isBlankText(n)) return null;
        n = dir < 0 ? n.previousSibling : n.nextSibling;
      }
      return null;
    }
    if (node === ed) {
      return dir < 0 ? walk(ed.childNodes[offset - 1]) : walk(ed.childNodes[offset]);
    }
    if (node.nodeType === 3) {
      var raw = node.nodeValue || "";
      if (
        dir < 0 &&
        (offset === 0 || !String(raw.slice(0, offset) || "").replace(/[\s\u00a0]/g, ""))
      ) {
        return walk(node.previousSibling);
      }
      if (
        dir > 0 &&
        (offset === raw.length || !String(raw.slice(offset) || "").replace(/[\s\u00a0]/g, ""))
      ) {
        return walk(node.nextSibling);
      }
      return null;
    }
    if (isChip(node)) return node;
    if (node.nodeType === 1 && node !== ed && offset === 0 && dir < 0) {
      return walk(node.previousSibling);
    }
    return null;
  }

  function selectedGptChip(ed) {
    var sel = window.getSelection();
    if (!ed || !sel || !sel.rangeCount) return null;
    var node = sel.anchorNode;
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    var chip =
      el && el.closest
        ? el.closest(".note-gpt-quote-chip, .note-discuss-mention")
        : null;
    return chip && ed.contains(chip) ? chip : null;
  }

  function removeGptChip(ed, chip) {
    if (!ed || !chip || !chip.parentNode) return;
    var next = document.createRange();
    next.setStartBefore(chip);
    next.collapse(true);
    chip.parentNode.removeChild(chip);
    var sel = window.getSelection();
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(next);
    }
    ed._savedRange = next.cloneRange();
    syncGptEditorEmpty(ed);
    autosizeNoteComposer(ed);
    if (ed) ed.setAttribute("data-placeholder", noteAskPlaceholder("gpt"));
    syncNotePaieReplyForm();
  }

  function discussionParticipants() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var rows = (wrap && wrap._noteMembers) || [];
    return rows
      .map(function (m) {
        if (!m) return null;
        var uname = String(m.username || "").trim().replace(/^@/, "");
        var name = String(m.name || "").trim();
        if (!name) {
          name = uname
            ? "@" + uname
            : [m.first_name, m.last_name].filter(Boolean).join(" ").trim();
        }
        if (!name && !uname) name = "Участник";
        return {
          user_id: String(m.user_id || ""),
          username: uname,
          name: name,
          mention: uname
            ? "@" + uname
            : "@" + String(name || "user").replace(/\s+/g, ""),
        };
      })
      .filter(function (m) {
        return m && m.user_id;
      });
  }

  function closeDiscussMentionMenu() {
    var menu = document.getElementById("note-discuss-mention-menu");
    if (menu) menu.classList.add("hidden");
  }

  function ensureDiscussMentionMenu() {
    var form = document.getElementById("note-paie-reply-form");
    if (!form) return null;
    var menu = document.getElementById("note-discuss-mention-menu");
    if (menu) return menu;
    menu = document.createElement("div");
    menu.id = "note-discuss-mention-menu";
    menu.className = "note-discuss-mention-menu hidden";
    menu.setAttribute("role", "listbox");
    form.appendChild(menu);
    return menu;
  }

  function getMentionQueryAtCaret(ed) {
    var sel = window.getSelection && window.getSelection();
    if (!ed || !sel || !sel.rangeCount || !sel.isCollapsed) return null;
    var range = sel.getRangeAt(0);
    var node = range.startContainer;
    if (!node || node.nodeType !== 3 || !ed.contains(node)) return null;
    var before = String(node.nodeValue || "").slice(0, range.startOffset);
    var m = before.match(/(^|[\s\u00a0([{\"'«])@([a-zA-Z0-9_]{0,32})$/);
    if (!m) return null;
    return {
      query: String(m[2] || ""),
      node: node,
      start: range.startOffset - (String(m[2] || "").length + 1),
      end: range.startOffset,
    };
  }

  function renderDiscussMentionMenu(query) {
    var menu = ensureDiscussMentionMenu();
    if (!menu) return;
    var q = String(query || "").toLowerCase();
    var rows = discussionParticipants().filter(function (m) {
      if (!q) return true;
      return (
        String(m.username || "").toLowerCase().indexOf(q) >= 0 ||
        String(m.name || "").toLowerCase().indexOf(q) >= 0
      );
    });
    menu.innerHTML = "";
    if (!rows.length) {
      menu.classList.add("hidden");
      return;
    }
    rows.slice(0, 8).forEach(function (m) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-discuss-mention-item";
      btn.setAttribute("role", "option");
      var title = document.createElement("span");
      title.className = "note-discuss-mention-item-name";
      title.textContent = m.name;
      btn.appendChild(title);
      if (m.username) {
        var handle = document.createElement("span");
        handle.className = "note-discuss-mention-item-handle";
        handle.textContent = "@" + m.username;
        btn.appendChild(handle);
      }
      btn.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        insertDiscussMention(m);
      });
      menu.appendChild(btn);
    });
    menu.classList.remove("hidden");
  }

  function syncDiscussMentionMenu() {
    var ed = noteGptEditor();
    if (!ed || !noteAskAllowsMentions(noteAskMode())) {
      closeDiscussMentionMenu();
      return;
    }
    if (!discussionParticipants().length) {
      closeDiscussMentionMenu();
      return;
    }
    var at = getMentionQueryAtCaret(ed);
    if (!at) {
      closeDiscussMentionMenu();
      return;
    }
    renderDiscussMentionMenu(at.query);
  }

  function insertDiscussMention(member) {
    var ed = noteGptEditor();
    if (!ed || !member) return;
    var at = getMentionQueryAtCaret(ed);
    var chip = document.createElement("span");
    chip.className = "note-discuss-mention";
    chip.setAttribute("contenteditable", "false");
    chip.setAttribute("data-user-id", String(member.user_id || ""));
    chip.setAttribute("data-mention", member.mention || ("@" + (member.username || member.name)));
    if (member.username) chip.setAttribute("data-username", member.username);
    chip.textContent = member.mention || ("@" + (member.username || member.name));
    ed._ignoreRange = true;
    try {
      try {
        ed.focus();
      } catch (_) {}
      var sel = window.getSelection();
      if (at && at.node && sel) {
        var del = document.createRange();
        del.setStart(at.node, at.start);
        del.setEnd(at.node, at.end);
        del.deleteContents();
        del.insertNode(chip);
        var after = document.createTextNode("\u00a0");
        if (chip.parentNode) chip.parentNode.insertBefore(after, chip.nextSibling);
        var caret = document.createRange();
        caret.setStart(after, after.nodeValue.length);
        caret.collapse(true);
        sel.removeAllRanges();
        sel.addRange(caret);
        ed._savedRange = caret.cloneRange();
      } else {
        restoreGptEditorRange(ed);
        sel = window.getSelection();
        if (sel && sel.rangeCount) {
          var r = sel.getRangeAt(0);
          r.deleteContents();
          r.insertNode(chip);
          r.setStartAfter(chip);
          r.collapse(true);
          sel.removeAllRanges();
          sel.addRange(r);
          ed._savedRange = r.cloneRange();
        } else {
          ed.appendChild(chip);
        }
      }
    } finally {
      ed._ignoreRange = false;
    }
    closeDiscussMentionMenu();
    syncGptEditorEmpty(ed);
    autosizeNoteComposer(ed);
    syncNotePaieReplyForm();
  }

  function fillDiscussBodyWithMentions(el, text) {
    if (!el) return;
    var raw = String(text || "");
    el.textContent = "";
    if (!raw) return;
    var re = /(^|[\s([{«"'])(@[a-zA-Z0-9_]{2,32})/g;
    var last = 0;
    var m;
    while ((m = re.exec(raw))) {
      var start = m.index + m[1].length;
      if (start > last) el.appendChild(document.createTextNode(raw.slice(last, start)));
      var span = document.createElement("span");
      span.className = "note-discuss-mention-text";
      span.textContent = m[2];
      el.appendChild(span);
      last = start + m[2].length;
    }
    if (last < raw.length) el.appendChild(document.createTextNode(raw.slice(last)));
  }

  function discussionHistoryAuthorLabel(c) {
    if (isAssistantComment(c)) return "";
    var uname = String((c && c.author_username) || "").trim().replace(/^@/, "");
    var name = String((c && c.author_name) || "").trim();
    if (uname && name) return "@" + uname + " (" + name + ")";
    if (uname) return "@" + uname;
    if (name) return name;
    return "Участник";
  }

  function noteKnowledgeIconHtml() {
    return (
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>' +
      '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>' +
      "</svg>"
    );
  }

  function notePaieReplyFormHtml() {
    var arrow = noteComposerAsset("arrow-up-right");
    var chevronFill = noteComposerAsset("chevron-up-on-fill");
    var chevronMuted = noteComposerAsset("chevron-up-muted");
    return (
      '<form id="note-paie-reply-form" class="note-paie-reply-form is-gpt-mode">' +
      '<div class="note-paie-reply-target hidden">' +
      '<span class="note-paie-reply-target-bar" aria-hidden="true"></span>' +
      '<div class="note-paie-reply-target-copy">' +
      '<p class="note-paie-reply-target-who">Фрагмент</p>' +
      '<p class="note-paie-reply-target-quote"></p>' +
      "</div>" +
      '<button type="button" class="note-paie-reply-target-clear" aria-label="Отменить ответ">&times;</button>' +
      "</div>" +
      '<div id="note-paie-plus-menu" class="note-paie-plus-menu hidden">' +
      '<div class="note-paie-plus-page" data-plus-page="root">' +
      '<button type="button" class="note-paie-menu-item" data-plus-action="file">Добавить файл</button>' +
      '<button type="button" class="note-paie-menu-item" data-plus-action="note">Добавить заметку</button>' +
      '<button type="button" class="note-paie-menu-item" data-plus-action="transcription">Добавить транскрипцию</button>' +
      '<button type="button" class="note-paie-menu-item" data-plus-action="summary">Добавить саммари</button>' +
      "</div>" +
      '<div class="note-paie-plus-page hidden" data-plus-page="notes">' +
      '<button type="button" class="note-paie-plus-back" id="note-paie-plus-back">← Назад</button>' +
      '<input type="search" class="note-paie-plus-search" id="note-paie-plus-search" placeholder="Поиск" autocomplete="off" />' +
      '<div class="note-paie-plus-project-chips notes-project-chips hidden" id="note-paie-plus-project-chips" role="listbox" aria-label="Проекты"></div>' +
      '<div class="note-paie-plus-notes" id="note-paie-plus-notes"></div>' +
      "</div></div>" +
      '<div id="note-paie-attach-preview" class="note-paie-attach-preview hidden"></div>' +
      '<div class="note-paie-composer-row">' +
      '<button type="button" class="note-paie-attach" id="note-paie-attach" aria-label="Добавить" title="Добавить" aria-haspopup="true" aria-expanded="false">' +
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
      '<path d="M12 5v14M5 12h14"/>' +
      "</svg></button>" +
      '<input type="file" id="note-paie-attach-input" class="hidden" multiple accept="image/*,.pdf,.txt,.md,.csv,.json,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip">' +
      '<textarea id="note-paie-reply-input" class="note-paie-composer-input" rows="1" maxlength="4000" placeholder="' +
      noteAskPlaceholder("paie") +
      '"></textarea>' +
      '<div id="note-paie-reply-editor" class="note-paie-composer-input note-paie-composer-editor is-empty" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="' +
      noteAskPlaceholder("gpt") +
      '"></div></div>' +
      '<div class="note-paie-composer-bar">' +
      '<div class="note-paie-composer-left">' +
      '<div class="note-paie-mode-switch" role="tablist" aria-label="Режим">' +
      '<button type="button" class="note-paie-mode" data-mode="paie">PAIE</button>' +
      '<button type="button" class="note-paie-mode is-active" data-mode="gpt">GPT</button>' +
      '<button type="button" class="note-paie-mode" data-mode="research">Research</button>' +
      '<button type="button" class="note-paie-mode" data-mode="comment">Текст</button>' +
      "</div>" +
      '<button type="button" class="note-paie-mode-compact" aria-haspopup="listbox" aria-expanded="false">' +
      '<span class="note-paie-mode-compact-label">GPT</span>' +
      '<img class="note-paie-chevron" src="' +
      chevronFill +
      '" alt="" width="24" height="24">' +
      "</button>" +
      '<div class="note-paie-menu note-paie-mode-menu hidden" role="listbox">' +
      '<button type="button" class="note-paie-menu-item" data-mode="paie">PAIE</button>' +
      '<button type="button" class="note-paie-menu-item is-active" data-mode="gpt">GPT</button>' +
      '<button type="button" class="note-paie-menu-item" data-mode="research">Research</button>' +
      '<button type="button" class="note-paie-menu-item" data-mode="comment">Текст</button>' +
      "</div>" +
      '<button type="button" class="note-paie-model-btn" aria-haspopup="listbox" aria-expanded="false">' +
      '<span class="note-paie-model-label">GPT-4.1</span>' +
      '<img class="note-paie-chevron" src="' +
      chevronMuted +
      '" alt="" width="24" height="24">' +
      "</button>" +
      '<div class="note-paie-menu note-paie-menu--models hidden">' +
      '<input type="search" class="note-paie-model-search" placeholder="Поиск модели" autocomplete="off">' +
      '<div class="note-paie-menu-list"></div>' +
      "</div></div>" +
      '<div class="note-paie-kb-wrap">' +
      '<button type="button" class="note-paie-kb-toggle" aria-haspopup="true" aria-expanded="false" aria-pressed="false" title="База знаний">' +
      noteKnowledgeIconHtml() +
      "</button>" +
      '<div class="note-paie-menu note-paie-menu--kb hidden" role="menu">' +
      '<div class="note-paie-menu-list"></div>' +
      "</div></div>" +
      '<button type="submit" class="note-paie-send" id="note-paie-reply-submit" disabled aria-label="Отправить">' +
      '<img class="note-paie-send-icon" src="' +
      arrow +
      '" alt="" width="24" height="24">' +
      "</button></div></form>"
    );
  }

  function selectedGptModelId() {
    if (composerAgentModel) return composerAgentModel;
    try {
      var stored = String(localStorage.getItem(GPT_MODEL_LS) || "").trim();
      if (stored) return stored;
    } catch (_) {}
    return gptModelsState.defaultId || GPT_MODEL_DEFAULT;
  }

  function setSelectedGptModelId(id) {
    var next = String(id || "").trim();
    if (!next) return;
    composerAgentModel = next;
    syncGptModelButton();
    if (isCustomAskMode(noteAskMode())) {
      var agentId = noteAskMode();
      var agent = findUserAgent(agentId);
      if (agent) agent.model = next;
      apiFetch("/agents/" + encodeURIComponent(agentId), {
        method: "PATCH",
        body: JSON.stringify({ model: next }),
      })
        .then(function () {
          return loadUserAgents();
        })
        .catch(function () {});
      return;
    }
    try {
      localStorage.setItem(GPT_MODEL_LS, next);
    } catch (_) {}
  }

  function gptModelShortName(id) {
    var found = gptModelsState.models.find(function (m) {
      return m.id === id;
    });
    if (found && found.name) return found.name;
    var raw = String(id || "");
    if (!raw) return "GPT";
    var short = raw.split("/").pop() || raw;
    if (/^gpt-/i.test(short)) return "GPT-" + short.slice(4);
    if (/^gpt/i.test(short)) return "GPT-" + short.slice(3).replace(/^[-_\s]+/, "");
    return "GPT-" + short;
  }

  function closeNoteComposerMenus(except) {
    var form = document.getElementById("note-paie-reply-form");
    if (!form) return;
    form.querySelectorAll(".note-paie-menu").forEach(function (menu) {
      if (except && menu === except) return;
      menu.classList.add("hidden");
    });
    var plusMenu = document.getElementById("note-paie-plus-menu");
    if (plusMenu && plusMenu !== except) {
      plusMenu.classList.add("hidden");
      showPlusMenuPage("root");
    }
    form.querySelectorAll(".note-paie-mode-compact, .note-paie-model-btn, .note-paie-kb-toggle, .note-paie-attach").forEach(function (btn) {
      btn.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    });
    syncNoteFormatToolbarForComposer();
  }

  function renderGptModelMenu(query) {
    var form = document.getElementById("note-paie-reply-form");
    var list = form && form.querySelector(".note-paie-menu--models .note-paie-menu-list");
    if (!list) return;
    var q = String(query || "").trim().toLowerCase();
    var selected = selectedGptModelId();
    var rows = gptModelsState.models.filter(function (m) {
      if (!q) return true;
      return (
        String(m.name || "").toLowerCase().indexOf(q) >= 0 ||
        String(m.id || "").toLowerCase().indexOf(q) >= 0
      );
    });
    list.innerHTML = "";
    if (!rows.length) {
      var empty = document.createElement("p");
      empty.className = "note-paie-menu-empty";
      empty.textContent = gptModelsState.loading ? "Загрузка…" : "Нет моделей";
      list.appendChild(empty);
      return;
    }
    rows.forEach(function (m) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-paie-menu-item" + (m.id === selected ? " is-active" : "");
      btn.setAttribute("data-model-id", m.id);
      btn.textContent = m.name || m.id;
      btn.title = m.id;
      btn.addEventListener("click", function () {
        setSelectedGptModelId(m.id);
        closeNoteComposerMenus();
      });
      list.appendChild(btn);
    });
  }

  function syncGptModelButton() {
    var form = document.getElementById("note-paie-reply-form");
    var label = form && form.querySelector(".note-paie-model-label");
    if (label) label.textContent = gptModelShortName(selectedGptModelId());
  }

  async function loadGptModels() {
    if (gptModelsState.loaded || gptModelsState.loading) {
      syncGptModelButton();
      renderGptModelMenu();
      return;
    }
    gptModelsState.loading = true;
    renderGptModelMenu();
    try {
      var res = await apiFetch("/gpt/models");
      gptModelsState.models = Array.isArray(res && res.models) ? res.models : [];
      gptModelsState.defaultId = String((res && res.default) || "").trim() || GPT_MODEL_DEFAULT;
      gptModelsState.loaded = true;
      var allowed = {};
      gptModelsState.models.forEach(function (m) {
        if (m && m.id) allowed[m.id] = true;
      });
      var current = selectedGptModelId();
      if (!current || !allowed[current]) {
        setSelectedGptModelId(
          (allowed[GPT_MODEL_DEFAULT] && GPT_MODEL_DEFAULT) ||
            gptModelsState.defaultId ||
            (gptModelsState.models[0] && gptModelsState.models[0].id) ||
            GPT_MODEL_DEFAULT
        );
      }
    } catch (_) {
      gptModelsState.models = [
        { id: "openai/gpt-5.6-sol", name: "GPT-5.6 Sol" },
        { id: "openai/gpt-5.5", name: "GPT-5.5" },
        { id: "openai/gpt-5.4", name: "GPT-5.4" },
        { id: "openai/gpt-5", name: "GPT-5" },
        { id: "openai/gpt-4.1", name: "GPT-4.1" },
        { id: "openai/gpt-4o", name: "GPT-4o" },
        { id: "openai/gpt-4o-mini", name: "GPT-4o mini" },
      ];
      gptModelsState.defaultId = GPT_MODEL_DEFAULT;
      gptModelsState.loaded = true;
      if (!selectedGptModelId()) setSelectedGptModelId(GPT_MODEL_DEFAULT);
    } finally {
      gptModelsState.loading = false;
      syncGptModelButton();
      renderGptModelMenu();
    }
  }

  function findUserAgent(id) {
    var want = String(id || "");
    var rows = userAgentsState.agents || [];
    for (var i = 0; i < rows.length; i++) {
      if (String(rows[i].id) === want) return rows[i];
    }
    return null;
  }

  function isCustomAskMode(mode) {
    var agent = findUserAgent(mode);
    return !!(agent && agent.kind === "custom");
  }

  function isChatAskMode(mode) {
    return mode === "gpt" || isCustomAskMode(mode);
  }

  async function loadUserAgents() {
    if (userAgentsState.loading) return userAgentsState.agents;
    userAgentsState.loading = true;
    try {
      var res = await apiFetch("/agents");
      userAgentsState.agents = (res && res.agents) || [];
      userAgentsState.loaded = true;
    } catch (_) {
      if (!userAgentsState.loaded) {
        userAgentsState.agents = [
          { id: "paie", kind: "paie", title: "PAIE", builtin: true },
          { id: "research", kind: "research", title: "Research", builtin: true },
          { id: "gpt", kind: "gpt", title: "GPT", builtin: true },
        ];
      }
    } finally {
      userAgentsState.loading = false;
    }
    renderNoteAskModes();
    return userAgentsState.agents;
  }

  function renderNoteAskModes() {
    var form = document.getElementById("note-paie-reply-form");
    if (!form) return;
    var switchEl = form.querySelector(".note-paie-mode-switch");
    var menu = form.querySelector(".note-paie-mode-menu");
    if (!switchEl || !menu) return;
    var wrap = document.getElementById("note-editor-more-wrap");
    var isChatThread =
      !!(wrap && wrap._shareKind === "chat") &&
      (notesSubTab === "chats" || !!activeChatThreadId);
    var current = noteAskMode();
    if (isChatThread && current === "comment") {
      if (wrap) wrap._askMode = "gpt";
      current = "gpt";
    }
    var items = (userAgentsState.agents || []).slice();
    if (!isChatThread) {
      items.push({ id: "comment", title: "Текст", kind: "comment" });
    }
    function makeBtn(agent, cls) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = cls + (String(agent.id) === current ? " is-active" : "");
      btn.setAttribute("data-mode", agent.id);
      btn.setAttribute("role", cls.indexOf("menu-item") >= 0 ? "option" : "tab");
      btn.textContent = agent.title || agent.id;
      btn.addEventListener("click", function () {
        setNoteAskMode(agent.id);
        closeNoteComposerMenus();
      });
      return btn;
    }
    switchEl.innerHTML = "";
    menu.innerHTML = "";
    items.forEach(function (agent) {
      switchEl.appendChild(makeBtn(agent, "note-paie-mode"));
      menu.appendChild(makeBtn(agent, "note-paie-menu-item"));
    });
    var compactLabel = form.querySelector(".note-paie-mode-compact-label");
    if (compactLabel) compactLabel.textContent = noteAskModeLabel(current);
  }

  function profileAgentsMsg(ok, err) {
    var msg = document.getElementById("agents-form-msg");
    var error = document.getElementById("agents-form-err");
    if (msg) {
      msg.textContent = ok || "";
      msg.classList.toggle("hidden", !ok);
    }
    if (error) {
      error.textContent = err || "";
      error.classList.toggle("hidden", !err);
    }
  }

  function fillAgentModelSelect(selected) {
    var sel = document.getElementById("agents-f-model");
    if (!sel) return;
    var models = (gptModelsState.models || []).slice();
    if (!models.length) {
      models = [
        { id: "openai/gpt-4.1", name: "GPT-4.1" },
        { id: "openai/gpt-4o", name: "GPT-4o" },
        { id: "openai/gpt-4o-mini", name: "GPT-4o mini" },
      ];
    }
    if (selected && !models.some(function (m) { return m.id === selected; })) {
      models.unshift({ id: selected, name: selected.split("/").pop() });
    }
    sel.innerHTML = "";
    models.forEach(function (m) {
      var opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.name || m.id;
      if (m.id === selected) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function renderProfileAgentsList() {
    var host = document.getElementById("profile-agents-list");
    if (!host) return;
    host.innerHTML = "";
    (userAgentsState.agents || []).forEach(function (agent) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "agent-card";
      var title = document.createElement("p");
      title.className = "agent-card-title";
      title.textContent = agent.title || agent.id;
      var meta = document.createElement("p");
      meta.className = "agent-card-meta";
      meta.textContent = agent.builtin
        ? (agent.model || "модель по умолчанию")
        : "свой агент · " + (agent.model || "");
      var left = document.createElement("div");
      left.appendChild(title);
      left.appendChild(meta);
      btn.appendChild(left);
      btn.addEventListener("click", function () {
        openAgentForm(agent);
      });
      host.appendChild(btn);
    });
  }

  function syncAgentFormFields(kind) {
    var titleField = document.getElementById("agents-title-field");
    var webField = document.getElementById("agents-web-field");
    var tempField = document.getElementById("agents-temp-field");
    var roundsField = document.getElementById("agents-rounds-field");
    if (titleField) titleField.classList.toggle("hidden", kind !== "custom" && kind !== "new");
    if (webField) webField.classList.toggle("hidden", kind !== "gpt" && kind !== "custom" && kind !== "new");
    if (tempField) tempField.classList.toggle("hidden", kind === "paie");
    if (roundsField) roundsField.classList.toggle("hidden", kind !== "paie");
  }

  function openAgentForm(agent) {
    var fields = document.getElementById("modal-fields");
    var overlay = document.getElementById("modal-overlay");
    if (!fields || !overlay) return;
    var isNew = !agent;
    var kind = isNew ? "new" : agent.kind;
    document.getElementById("modal-title").textContent = isNew
      ? "Новый агент"
      : agent.title || "Агент";
    document.getElementById("modal-kind").value = "agent";
    document.getElementById("modal-reminder-id").value = "";
    document.getElementById("modal-event-id").value = "";
    document.getElementById("modal-calendar-id").value = "";
    fields.innerHTML =
      '<div class="event-sheet">' +
      '<input type="hidden" id="agents-edit-id" value="" />' +
      '<div id="agents-title-field">' +
      '<input id="agents-f-title" type="text" class="event-sheet-title" maxlength="60" placeholder="Название" autocomplete="off" />' +
      "</div>" +
      '<label class="field">' +
      '<span class="field-label">Промпт</span>' +
      '<textarea id="agents-f-prompt" class="field-textarea agents-textarea" rows="8" maxlength="12000"></textarea>' +
      "</label>" +
      '<label class="field">' +
      '<span class="field-label">Правила</span>' +
      '<textarea id="agents-f-rules" class="field-textarea agents-textarea agents-textarea--rules" rows="4" maxlength="4000" placeholder="Дополнительные ограничения и тон"></textarea>' +
      "</label>" +
      '<label class="field">' +
      '<span class="field-label">Модель</span>' +
      '<select id="agents-f-model" class="field-input"></select>' +
      "</label>" +
      '<label class="field" id="agents-temp-field">' +
      '<span class="field-label">Температура</span>' +
      '<input type="number" id="agents-f-temp" class="field-input" min="0" max="2" step="0.1" />' +
      "</label>" +
      '<label class="field field--check hidden" id="agents-web-field">' +
      '<input type="checkbox" id="agents-f-web" />' +
      "<span>Поиск в вебе</span>" +
      "</label>" +
      '<label class="field hidden" id="agents-rounds-field">' +
      '<span class="field-label">Макс. раундов PAIE</span>' +
      '<input type="number" id="agents-f-rounds" class="field-input" min="1" max="8" step="1" />' +
      "</label>" +
      '<button type="button" class="btn ghost hidden" id="agents-reset">Сбросить к заводским</button>' +
      '<p id="agents-form-msg" class="billing-msg muted small hidden"></p>' +
      '<p id="agents-form-err" class="error hidden"></p>' +
      "</div>";
    var idEl = document.getElementById("agents-edit-id");
    var titleEl = document.getElementById("agents-f-title");
    var promptEl = document.getElementById("agents-f-prompt");
    var rulesEl = document.getElementById("agents-f-rules");
    var tempEl = document.getElementById("agents-f-temp");
    var webEl = document.getElementById("agents-f-web");
    var roundsEl = document.getElementById("agents-f-rounds");
    var resetBtn = document.getElementById("agents-reset");
    if (idEl) idEl.value = isNew ? "" : String(agent.id);
    if (titleEl) {
      titleEl.value = isNew ? "" : agent.title || "";
      titleEl.disabled = !isNew && !agent.can_rename;
    }
    if (promptEl) promptEl.value = isNew ? "" : agent.prompt || "";
    if (rulesEl) rulesEl.value = isNew ? "" : agent.rules || "";
    if (tempEl) {
      tempEl.value =
        agent && agent.temperature != null
          ? String(agent.temperature)
          : kind === "research"
            ? "0.2"
            : "0.1";
    }
    if (webEl) webEl.checked = !!(agent && agent.web_search);
    if (roundsEl) {
      roundsEl.value = agent && agent.max_rounds != null ? String(agent.max_rounds) : "4";
    }
    fillAgentModelSelect(agent && agent.model ? agent.model : GPT_MODEL_DEFAULT);
    if (resetBtn) {
      resetBtn.classList.toggle("hidden", isNew || !agent.builtin);
      resetBtn.addEventListener("click", function () {
        resetAgentForm();
      });
    }
    document.getElementById("modal-delete").classList.toggle("hidden", isNew || !agent.can_delete);
    syncAgentFormFields(kind);
    profileAgentsMsg("", "");
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    onModalSheetOpen();
    syncAppOverlay();
  }

  function closeAgentForm() {
    var kindEl = document.getElementById("modal-kind");
    if (kindEl && kindEl.value === "agent") closeModal();
    profileAgentsMsg("", "");
  }

  async function saveAgentForm() {
    var idEl = document.getElementById("agents-edit-id");
    var titleEl = document.getElementById("agents-f-title");
    var promptEl = document.getElementById("agents-f-prompt");
    var rulesEl = document.getElementById("agents-f-rules");
    var modelEl = document.getElementById("agents-f-model");
    var tempEl = document.getElementById("agents-f-temp");
    var webEl = document.getElementById("agents-f-web");
    var roundsEl = document.getElementById("agents-f-rounds");
    var id = idEl && idEl.value;
    var body = {
      prompt: promptEl ? promptEl.value : "",
      rules: rulesEl ? rulesEl.value : "",
      model: modelEl ? modelEl.value : "",
      temperature: tempEl && tempEl.value !== "" ? Number(tempEl.value) : null,
      web_search: !!(webEl && webEl.checked),
    };
    if (roundsEl && !roundsEl.closest(".hidden")) {
      body.max_rounds = Number(roundsEl.value || 4);
    }
    try {
      profileAgentsMsg("", "");
      if (!id) {
        body.title = titleEl ? titleEl.value : "";
        await apiFetch("/agents", { method: "POST", body: JSON.stringify(body) });
      } else {
        var current = findUserAgent(id);
        if (current && current.can_rename) body.title = titleEl ? titleEl.value : current.title;
        await apiFetch("/agents/" + encodeURIComponent(id), {
          method: "PATCH",
          body: JSON.stringify(body),
        });
      }
      await loadUserAgents();
      renderProfileAgentsList();
      closeAgentForm();
      profileAgentsMsg("Сохранено", "");
    } catch (e) {
      profileAgentsMsg("", (e && e.message) || String(e));
    }
  }

  async function resetAgentForm() {
    var idEl = document.getElementById("agents-edit-id");
    var id = idEl && idEl.value;
    if (!id) return;
    try {
      await apiFetch("/agents/" + encodeURIComponent(id) + "/reset", { method: "POST" });
      await loadUserAgents();
      renderProfileAgentsList();
      var next = findUserAgent(id);
      if (next) openAgentForm(next);
      else closeAgentForm();
      profileAgentsMsg("Сброшено к заводским", "");
    } catch (e) {
      profileAgentsMsg("", (e && e.message) || String(e));
    }
  }

  async function deleteAgentForm() {
    var idEl = document.getElementById("agents-edit-id");
    var id = idEl && idEl.value;
    if (!id) return;
    var ok = await confirmDialog("Удалить этого агента?");
    if (!ok) return;
    try {
      await apiFetch("/agents/" + encodeURIComponent(id), { method: "DELETE" });
      await loadUserAgents();
      renderProfileAgentsList();
      closeAgentForm();
    } catch (e) {
      profileAgentsMsg("", (e && e.message) || String(e));
    }
  }

  async function openProfileAgents() {
    profileScreen("agents");
    closeAgentForm();
    await loadGptModels();
    await loadUserAgents();
    renderProfileAgentsList();
  }

  function composerLineHeight(el) {
    var lh = parseFloat(window.getComputedStyle(el).lineHeight);
    if (isFinite(lh) && lh > 8) return lh;
    var fs = parseFloat(window.getComputedStyle(el).fontSize) || 16;
    return fs * 1.35;
  }

  function composerPlaceholderWraps(el) {
    var value = composerEditorValue(el);
    if (!el || String(value || "").replace(/\s+/g, " ").trim()) return false;
    if (el.querySelector && el.querySelector(".note-gpt-quote-chip")) return false;
    var ph = el.getAttribute("placeholder") || el.getAttribute("data-placeholder") || "";
    if (!ph || el.clientWidth < 8) return false;
    var cs = window.getComputedStyle(el);
    var probe = document.createElement("div");
    var padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    probe.style.cssText =
      "position:absolute;left:-9999px;top:0;visibility:hidden;pointer-events:none;" +
      "white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;padding:0;border:0;" +
      "box-sizing:border-box;font:" +
      cs.font +
      ";font-size:" +
      cs.fontSize +
      ";font-weight:" +
      cs.fontWeight +
      ";letter-spacing:" +
      cs.letterSpacing +
      ";line-height:" +
      cs.lineHeight +
      ";width:" +
      Math.max(8, el.clientWidth - padX) +
      "px;";
    probe.textContent = ph;
    document.body.appendChild(probe);
    var wraps = probe.offsetHeight > composerLineHeight(el) * 1.35;
    document.body.removeChild(probe);
    return wraps;
  }

  function autosizeNoteComposer(el) {
    if (!el) return;
    var line = composerLineHeight(el);
    var cs = window.getComputedStyle(el);
    var padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    var minSingle = Math.round(line + Math.max(padY, 8));
    var minMulti = Math.round(line * 2 + padY);
    var maxH = 136;
    el.classList.remove("is-multiline");
    el.classList.remove("is-overflowing");
    el.style.height = "auto";
    var value = composerEditorValue(el);
    if (el.querySelector && el.querySelector(".note-gpt-quote-chip")) {
      value = value || " ";
    }
    var valueHasBreak = value.indexOf("\n") >= 0;
    var valueWraps = valueHasBreak || (!!value && el.scrollHeight > minSingle + 1);
    var placeholderWraps = composerPlaceholderWraps(el);
    if (!valueWraps) {
      var singleH = Math.max(el.scrollHeight, minSingle);
      if (!value && placeholderWraps) {
        el.classList.add("is-multiline");
        singleH = Math.max(singleH, minMulti);
      }
      el.style.height = Math.min(singleH, maxH) + "px";
      return;
    }
    el.classList.add("is-multiline");
    el.style.height = "auto";
    var h = Math.max(el.scrollHeight, minMulti);
    if (h > maxH) {
      h = maxH;
      el.classList.add("is-overflowing");
    }
    el.style.height = h + "px";
  }

  var DISCUSS_ATTACH_MAX = 8;
  var DISCUSS_ATTACH_MAX_BYTES = 12 * 1024 * 1024;
  var DISCUSS_CONTEXT_NOTES_MAX = 5;
  var discussFileBlobUrls = {};

  function discussPendingFiles() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap) return [];
    if (!wrap._discussPending) wrap._discussPending = [];
    return wrap._discussPending;
  }

  function discussPendingContextNotes() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap) return [];
    if (!wrap._discussContextNotes) wrap._discussContextNotes = [];
    return wrap._discussContextNotes;
  }

  function discussContextNoteIds() {
    return discussPendingContextNotes()
      .map(function (n) {
        if (!n || n.id == null) return "";
        var id = String(n.id);
        if (!id) return "";
        if (n.kind === "journal") return "journal:" + id;
        return id;
      })
      .filter(Boolean);
  }

  function clearDiscussPendingContextNotes() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) wrap._discussContextNotes = [];
    renderDiscussAttachPreview();
    syncNotePaieReplyForm();
  }

  function addDiscussContextNote(note, kind) {
    var id = note && note.id != null ? String(note.id) : "";
    if (!id) return;
    var itemKind = kind === "journal" ? "journal" : "local";
    var list = discussPendingContextNotes();
    if (
      list.some(function (n) {
        return String(n.id) === id && (n.kind || "local") === itemKind;
      })
    ) {
      return;
    }
    if (list.length >= DISCUSS_CONTEXT_NOTES_MAX) {
      alert("Можно прикрепить не больше " + DISCUSS_CONTEXT_NOTES_MAX + " материалов");
      return;
    }
    var title = "";
    if (itemKind === "journal") {
      title = journalCardTitle(note) || "Запись";
    } else {
      title =
        sanitizeNoteTitle((note && (note.title || note.content)) || "") ||
        String((note && (note.title || note.content)) || "Заметка");
    }
    list.push({
      id: id,
      kind: itemKind,
      title: title,
    });
    renderDiscussAttachPreview();
    syncNotePaieReplyForm();
  }

  function removeDiscussContextNote(idx) {
    discussPendingContextNotes().splice(idx, 1);
    renderDiscussAttachPreview();
    syncNotePaieReplyForm();
  }

  function clearDiscussPendingFiles() {
    var list = discussPendingFiles();
    list.forEach(function (item) {
      if (item && item.previewUrl) {
        try {
          URL.revokeObjectURL(item.previewUrl);
        } catch (_) {}
      }
    });
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) wrap._discussPending = [];
    renderDiscussAttachPreview();
  }

  function discussFileKind(file) {
    var type = String((file && file.type) || "").toLowerCase();
    if (type.indexOf("image/") === 0) return "image";
    var name = String((file && file.name) || "").toLowerCase();
    if (/\.(png|jpe?g|gif|webp|heic|heif)$/.test(name)) return "image";
    return "file";
  }

  function addDiscussPendingFiles(fileList) {
    var pending = discussPendingFiles();
    var files = Array.prototype.slice.call(fileList || []);
    files.forEach(function (file) {
      if (!file || pending.length >= DISCUSS_ATTACH_MAX) return;
      if (file.size > DISCUSS_ATTACH_MAX_BYTES) {
        alert("Файл «" + (file.name || "файл") + "» слишком большой (максимум 12 МБ)");
        return;
      }
      pending.push({
        file: file,
        name: file.name || "file",
        mime: file.type || "",
        size: file.size || 0,
        kind: discussFileKind(file),
        previewUrl: file.type && file.type.indexOf("image/") === 0 ? URL.createObjectURL(file) : "",
      });
    });
    renderDiscussAttachPreview();
    syncNotePaieReplyForm();
  }

  function removeDiscussPendingFile(idx) {
    var pending = discussPendingFiles();
    var item = pending[idx];
    if (item && item.previewUrl) {
      try {
        URL.revokeObjectURL(item.previewUrl);
      } catch (_) {}
    }
    pending.splice(idx, 1);
    renderDiscussAttachPreview();
    syncNotePaieReplyForm();
  }

  function renderDiscussAttachPreview() {
    var host = document.getElementById("note-paie-attach-preview");
    if (!host) return;
    var pending = discussPendingFiles();
    var notes = discussPendingContextNotes();
    host.innerHTML = "";
    if (!pending.length && !notes.length) {
      host.classList.add("hidden");
      return;
    }
    host.classList.remove("hidden");
    notes.forEach(function (item, idx) {
      var chip = document.createElement("div");
      chip.className = "note-paie-attach-chip is-note";
      var name = document.createElement("span");
      name.className = "note-paie-attach-chip-name";
      name.textContent = item.title || "Материал";
      chip.appendChild(name);
      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "note-paie-attach-chip-remove";
      rm.setAttribute("aria-label", "Убрать вложение");
      rm.textContent = "×";
      rm.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        removeDiscussContextNote(idx);
      });
      chip.appendChild(rm);
      host.appendChild(chip);
    });
    pending.forEach(function (item, idx) {
      var chip = document.createElement("div");
      chip.className = "note-paie-attach-chip" + (item.kind === "image" ? " is-image" : "");
      if (item.kind === "image" && item.previewUrl) {
        var img = document.createElement("img");
        img.src = item.previewUrl;
        img.alt = item.name || "Фото";
        chip.appendChild(img);
      }
      var name = document.createElement("span");
      name.className = "note-paie-attach-chip-name";
      name.textContent = item.name || "Файл";
      chip.appendChild(name);
      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "note-paie-attach-chip-remove";
      rm.setAttribute("aria-label", "Убрать файл");
      rm.textContent = "×";
      rm.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        removeDiscussPendingFile(idx);
      });
      chip.appendChild(rm);
      host.appendChild(chip);
    });
  }

  function showPlusMenuPage(page) {
    var menu = document.getElementById("note-paie-plus-menu");
    if (!menu) return;
    menu.querySelectorAll(".note-paie-plus-page").forEach(function (el) {
      el.classList.toggle("hidden", el.getAttribute("data-plus-page") !== page);
    });
    if (page === "notes") {
      plusPickerActiveProjectId = 0;
      var search = document.getElementById("note-paie-plus-search");
      if (search) {
        search.value = "";
        search.placeholder =
          plusPickerKind === "transcription"
            ? "Поиск транскрипций"
            : plusPickerKind === "summary"
              ? "Поиск саммари"
              : "Поиск заметок";
      }
      var chips = document.getElementById("note-paie-plus-project-chips");
      if (chips) setHidden(chips, false);
      ensureUserTagsLoaded().finally(function () {
        renderPlusNotePicker("");
      });
      window.setTimeout(function () {
        if (search) {
          try {
            search.focus();
          } catch (_) {}
        }
      }, 20);
    }
  }

  function plusPickerItems(query) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var currentKind = wrap ? String(wrap._shareKind || "") : "";
    var currentId = wrap && wrap._shareId ? String(wrap._shareId) : "";
    var selected = {};
    discussPendingContextNotes().forEach(function (n) {
      if (!n || n.id == null) return;
      var key = (n.kind || "local") + ":" + String(n.id);
      selected[key] = true;
    });
    var q = String(query || "").trim().toLowerCase();
    var kind = plusPickerKind || "note";
    var source = [];
    if (kind === "transcription") {
      source = (notesDataCache && notesDataCache.transcriptions) || [];
    } else if (kind === "summary") {
      source = ((notesDataCache && notesDataCache.transcriptions) || []).filter(
        function (n) {
          return transcriptionHasSummary(n);
        }
      );
    } else {
      source = (notesDataCache && notesDataCache.local_notes) || [];
    }
    return source.filter(function (n) {
      var id = String((n && n.id) || "");
      if (!id) return false;
      var itemKind = "local";
      if (selected[itemKind + ":" + id]) return false;
      if (currentKind === "local" && id === currentId) return false;
      if (n.is_knowledge || n.role === "knowledge") return false;
      if (!noteMatchesPickerProjectFilter(n, plusPickerActiveProjectId)) return false;
      if (!q) return true;
      if (itemKind === "journal") {
        var jTitle = String(journalCardTitle(n) || "").toLowerCase();
        var jPrev = String(n.preview || "").toLowerCase();
        return jTitle.indexOf(q) >= 0 || jPrev.indexOf(q) >= 0;
      }
      var title = String(n.title || n.content || "").toLowerCase();
      var preview = notePlainExcerpt(n.description || n.body || "", 80).toLowerCase();
      return title.indexOf(q) >= 0 || preview.indexOf(q) >= 0;
    });
  }

  function renderPlusNotePicker(query) {
    var list = document.getElementById("note-paie-plus-notes");
    if (!list) return;
    var chipsHost = document.getElementById("note-paie-plus-project-chips");
    renderNotePickerProjectChips(
      chipsHost,
      function () {
        return plusPickerActiveProjectId;
      },
      function (v) {
        plusPickerActiveProjectId = v;
      },
      function () {
        var search = document.getElementById("note-paie-plus-search");
        renderPlusNotePicker(search ? search.value : "");
      }
    );
    var notes = plusPickerItems(query);
    list.innerHTML = "";
    if (!notes.length) {
      var empty = document.createElement("p");
      empty.className = "note-paie-menu-empty";
      var emptyDefault =
        plusPickerKind === "transcription"
          ? "Транскрипций нет"
          : plusPickerKind === "summary"
            ? "Саммари нет"
            : "Других заметок нет";
      empty.textContent = !notesDataCache
        ? "Загрузка…"
        : String(query || "").trim() || plusPickerActiveProjectId
          ? "Ничего не найдено"
          : emptyDefault;
      list.appendChild(empty);
      return;
    }
    notes.forEach(function (n) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-paie-menu-item note-paie-plus-note";
      var title = document.createElement("span");
      title.className = "note-paie-kb-item-title";
      var titleText =
        sanitizeNoteTitle(n.title || n.content || n.main_topic || "") ||
        String(n.title || n.content || n.main_topic || "Без названия");
      title.textContent = titleText;
      btn.appendChild(title);
      var project = noteProjectOf(n);
      var projectName = (project && (project.name || project.title)) || "";
      if (projectName) {
        var meta = document.createElement("span");
        meta.className = "note-paie-plus-note-project";
        meta.textContent = String(projectName);
        btn.appendChild(meta);
      }
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        addDiscussContextNote(n, "local");
        closeNoteComposerMenus();
      });
      list.appendChild(btn);
    });
  }

  function ensurePlusPickerNotes() {
    if (notesDataCache) {
      if (plusPickerKind === "transcription") {
        return Promise.resolve(notesDataCache.transcriptions || []);
      }
      if (plusPickerKind === "summary") {
        return Promise.resolve(
          ((notesDataCache && notesDataCache.transcriptions) || []).filter(transcriptionHasSummary)
        );
      }
      if (Array.isArray(notesDataCache.local_notes) && notesDataCache.local_notes.length) {
        return Promise.resolve(notesDataCache.local_notes);
      }
    }
    return Promise.resolve(loadNotes())
      .then(function () {
        if (plusPickerKind === "transcription") {
          return (notesDataCache && notesDataCache.transcriptions) || [];
        }
        if (plusPickerKind === "summary") {
          return ((notesDataCache && notesDataCache.transcriptions) || []).filter(
            transcriptionHasSummary
          );
        }
        return (notesDataCache && notesDataCache.local_notes) || [];
      })
      .catch(function () {
        return [];
      });
  }

  function openPlusPicker(kind) {
    plusPickerKind = kind || "note";
    showPlusMenuPage("notes");
    ensurePlusPickerNotes().then(function () {
      var search = document.getElementById("note-paie-plus-search");
      renderPlusNotePicker(search ? search.value : "");
    });
  }
  function bindDiscussAttachOnce(form) {
    var btn = document.getElementById("note-paie-attach");
    var input = document.getElementById("note-paie-attach-input");
    var menu = document.getElementById("note-paie-plus-menu");
    if (!btn || !input || !menu || btn._bound) return;
    btn._bound = true;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (btn.disabled) return;
      var open = menu && menu.classList.contains("hidden");
      closeNoteComposerMenus(open ? menu : null);
      if (menu && open) {
        showPlusMenuPage("root");
        menu.classList.remove("hidden");
        btn.classList.add("is-open");
        btn.setAttribute("aria-expanded", "true");
      }
      syncNoteFormatToolbarForComposer();
    });
    if (menu && !menu._bound) {
      menu._bound = true;
      menu.addEventListener("click", function (e) {
        e.stopPropagation();
      });
      var fileBtn = menu.querySelector('[data-plus-action="file"]');
      if (fileBtn) {
        fileBtn.addEventListener("click", function (e) {
          e.preventDefault();
          closeNoteComposerMenus();
          input.click();
        });
      }
      var noteBtn = menu.querySelector('[data-plus-action="note"]');
      if (noteBtn) {
        noteBtn.addEventListener("click", function (e) {
          e.preventDefault();
          openPlusPicker("note");
        });
      }
      var transcriptionBtn = menu.querySelector('[data-plus-action="transcription"]');
      if (transcriptionBtn) {
        transcriptionBtn.addEventListener("click", function (e) {
          e.preventDefault();
          openPlusPicker("transcription");
        });
      }
      var summaryBtn = menu.querySelector('[data-plus-action="summary"]');
      if (summaryBtn) {
        summaryBtn.addEventListener("click", function (e) {
          e.preventDefault();
          openPlusPicker("summary");
        });
      }
      var notesList = document.getElementById("note-paie-plus-notes");
      if (notesList && !notesList._scrollBound) {
        notesList._scrollBound = true;
        notesList.addEventListener(
          "touchstart",
          function (e) {
            e.stopPropagation();
          },
          { passive: true }
        );
        notesList.addEventListener(
          "touchmove",
          function (e) {
            e.stopPropagation();
          },
          { passive: true }
        );
        notesList.addEventListener(
          "wheel",
          function (e) {
            e.stopPropagation();
          },
          { passive: true }
        );
      }
      var back = document.getElementById("note-paie-plus-back");
      if (back) {
        back.addEventListener("click", function (e) {
          e.preventDefault();
          showPlusMenuPage("root");
        });
      }
      var search = document.getElementById("note-paie-plus-search");
      if (search) {
        search.addEventListener("input", function () {
          renderPlusNotePicker(search.value);
        });
        search.addEventListener("click", function (e) {
          e.stopPropagation();
        });
      }
    }
    input.addEventListener("change", function () {
      if (input.files && input.files.length) addDiscussPendingFiles(input.files);
      input.value = "";
    });
    if (form && !form._attachDropBound) {
      form._attachDropBound = true;
      form.addEventListener("dragover", function (e) {
        if (!e.dataTransfer) return;
        e.preventDefault();
        form.classList.add("is-drop-attach");
      });
      form.addEventListener("dragleave", function () {
        form.classList.remove("is-drop-attach");
      });
      form.addEventListener("drop", function (e) {
        form.classList.remove("is-drop-attach");
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault();
        addDiscussPendingFiles(e.dataTransfer.files);
      });
    }
  }

  async function uploadDiscussPendingFiles(kind, itemId) {
    var pending = discussPendingFiles().slice();
    var ids = [];
    var i;
    for (i = 0; i < pending.length; i++) {
      var item = pending[i];
      var fd = new FormData();
      fd.append("file", item.file, item.name || "file");
      var res = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/discuss-files",
        { method: "POST", body: fd }
      );
      var row = res && res.file;
      if (!row || !row.id) throw new Error("Не удалось загрузить файл");
      ids.push(row.id);
    }
    return ids;
  }

  function discussAuthFileUrl(att) {
    return API + String((att && att.url) || "");
  }

  async function discussFileObjectUrl(att) {
    var key = String((att && att.id) || "");
    if (!key) return "";
    if (discussFileBlobUrls[key]) return discussFileBlobUrls[key];
    var res = await fetch(discussAuthFileUrl(att), {
      credentials: "same-origin",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("Не удалось открыть файл");
    var blob = await res.blob();
    var url = URL.createObjectURL(blob);
    discussFileBlobUrls[key] = url;
    return url;
  }

  function closeDiscussMediaViewer() {
    var ov = document.getElementById("discuss-media-viewer");
    if (!ov) return;
    if (ov._onKey) {
      document.removeEventListener("keydown", ov._onKey, true);
      ov._onKey = null;
    }
    if (ov.parentNode) ov.parentNode.removeChild(ov);
  }

  function downloadDiscussBlobUrl(src, filename) {
    var a = document.createElement("a");
    a.href = src;
    a.download = filename || "file";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function openDiscussMediaViewer(att, src) {
    closeDiscussMediaViewer();
    var name = (att && att.name) || "Файл";
    var mime = String((att && att.mime) || "").toLowerCase();
    var isImage = String((att && att.kind) || "") === "image" || mime.indexOf("image/") === 0;
    var isPdf = mime.indexOf("pdf") >= 0 || /\.pdf$/i.test(name);
    var ov = document.createElement("div");
    ov.id = "discuss-media-viewer";
    ov.className = "discuss-media-viewer";
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "true");
    ov.setAttribute("aria-label", name);
    ov.innerHTML =
      '<div class="discuss-media-viewer-backdrop" data-close="1"></div>' +
      '<div class="discuss-media-viewer-sheet">' +
      '<div class="discuss-media-viewer-bar">' +
      '<p class="discuss-media-viewer-title"></p>' +
      '<div class="discuss-media-viewer-actions">' +
      '<button type="button" class="discuss-media-viewer-btn" data-download="1" aria-label="Скачать" title="Скачать">' +
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>' +
      "</button>" +
      '<button type="button" class="discuss-media-viewer-btn" data-close="1" aria-label="Закрыть" title="Закрыть">' +
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
      "</button>" +
      "</div></div>" +
      '<div class="discuss-media-viewer-body"></div>' +
      "</div>";
    var title = ov.querySelector(".discuss-media-viewer-title");
    if (title) title.textContent = name;
    var body = ov.querySelector(".discuss-media-viewer-body");
    if (isImage) {
      var img = document.createElement("img");
      img.className = "discuss-media-viewer-img";
      img.src = src;
      img.alt = name;
      body.appendChild(img);
    } else if (isPdf) {
      var frame = document.createElement("iframe");
      frame.className = "discuss-media-viewer-frame";
      frame.src = src;
      frame.title = name;
      body.appendChild(frame);
    } else {
      var hint = document.createElement("p");
      hint.className = "discuss-media-viewer-hint";
      hint.textContent = "Нажмите «Скачать», чтобы сохранить файл.";
      body.appendChild(hint);
    }
    ov.addEventListener("click", function (e) {
      var t = e.target;
      if (!t) return;
      if (t.closest && t.closest("[data-download]")) {
        e.preventDefault();
        e.stopPropagation();
        downloadDiscussBlobUrl(src, name);
        return;
      }
      if (t.closest && t.closest("[data-close]")) {
        e.preventDefault();
        e.stopPropagation();
        closeDiscussMediaViewer();
      }
    });
    ov._onKey = function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeDiscussMediaViewer();
      }
    };
    document.addEventListener("keydown", ov._onKey, true);
    document.body.appendChild(ov);
  }

  async function openDiscussAttachment(att) {
    var src = await discussFileObjectUrl(att);
    openDiscussMediaViewer(att, src);
  }

  function renderDiscussAttachments(host, attachments) {
    var list = (attachments || []).filter(Boolean);
    if (!host || !list.length) return;
    var wrap = document.createElement("div");
    wrap.className = "note-discuss-attach-list";
    list.forEach(function (att) {
      if (String(att.kind || "") === "image") {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "note-discuss-attach-img-btn";
        btn.setAttribute("aria-label", "Открыть " + (att.name || "фото"));
        var img = document.createElement("img");
        img.className = "note-discuss-attach-img";
        img.alt = att.name || "Фото";
        btn.appendChild(img);
        btn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          openDiscussAttachment(att).catch(function (err) {
            alert(err.message || String(err));
          });
        });
        wrap.appendChild(btn);
        discussFileObjectUrl(att)
          .then(function (src) {
            img.src = src;
          })
          .catch(function () {
            btn.replaceWith(renderDiscussFileChip(att));
          });
        return;
      }
      wrap.appendChild(renderDiscussFileChip(att));
    });
    host.appendChild(wrap);
  }

  function renderDiscussFileChip(att) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "note-discuss-attach-file";
    btn.textContent = att.name || "Файл";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openDiscussAttachment(att).catch(function (err) {
        alert(err.message || String(err));
      });
    });
    return btn;
  }

  function renderPendingAttachThumbs(host) {
    var pending = discussPendingFiles();
    if (!host || !pending.length) return;
    var wrap = document.createElement("div");
    wrap.className = "note-discuss-attach-list";
    pending.forEach(function (item) {
      if (item.kind === "image" && item.previewUrl) {
        var img = document.createElement("img");
        img.className = "note-discuss-attach-img";
        img.src = item.previewUrl;
        img.alt = item.name || "Фото";
        wrap.appendChild(img);
      } else {
        var chip = document.createElement("span");
        chip.className = "note-discuss-attach-file";
        chip.textContent = item.name || "Файл";
        wrap.appendChild(chip);
      }
    });
    host.appendChild(wrap);
  }

  function ensureAttachPreviewAboveComposer(form) {
    if (!form) return;
    var preview = form.querySelector("#note-paie-attach-preview");
    var row = form.querySelector(".note-paie-composer-row");
    if (!preview || !row || !row.parentNode) return;
    if (preview.nextElementSibling === row) return;
    row.parentNode.insertBefore(preview, row);
  }

  function bindNotePaieReplyForm(threadEl) {
    var form = document.getElementById("note-paie-reply-form");
    if (
      !form ||
      !form.querySelector("#note-paie-attach") ||
      !form.querySelector("#note-paie-plus-menu") ||
      !form.querySelector('[data-plus-action="transcription"]') ||
      !form.querySelector('[data-plus-action="summary"]') ||
      !form.querySelector(".note-paie-kb-wrap") ||
      !form.querySelector("#note-paie-reply-editor") ||
      !form.querySelector('[data-mode="research"]')
    ) {
      var html = notePaieReplyFormHtml();
      if (form) form.outerHTML = html;
      else if (threadEl) threadEl.insertAdjacentHTML("beforeend", html);
      form = document.getElementById("note-paie-reply-form");
    }
    if (!form) return;
    ensureAttachPreviewAboveComposer(form);
    bindNoteKnowledgeToggle(form);
    bindDiscussAttachOnce(form);
    if (form._bound) return;
    form._bound = true;
    var input = document.getElementById("note-paie-reply-input");
    var editor = noteGptEditor();
    var compact = form.querySelector(".note-paie-mode-compact");
    var modeMenu = form.querySelector(".note-paie-mode-menu");
    var modelBtn = form.querySelector(".note-paie-model-btn");
    var modelMenu = form.querySelector(".note-paie-menu--models");
    var modelSearch = form.querySelector(".note-paie-model-search");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      closeNoteComposerMenus();
      if (isChatAskMode(noteAskMode())) {
        var packed = serializeGptComposer(editor);
        submitNoteGptQuestion(packed.text);
      } else if (noteAskMode() === "research") {
        var researchPacked = serializeGptComposer(editor);
        submitNoteResearch(researchPacked.text);
      } else if (noteAskMode() === "comment") {
        var commentPacked = serializeGptComposer(editor);
        submitNoteFooterComment(commentPacked.text);
      } else submitNotePaieReply(input && input.value);
    });
    var replyClear = form.querySelector(".note-paie-reply-target-clear");
    if (replyClear && !replyClear._bound) {
      replyClear._bound = true;
      replyClear.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        clearNoteComposerCommentTarget();
        var focusEl = noteAskUsesComposerEditor(noteAskMode())
          ? noteGptEditor()
          : document.getElementById("note-paie-reply-input");
        if (focusEl) {
          if (focusEl.id === "note-paie-reply-input") {
            focusEl.placeholder = noteAskPlaceholder(noteAskMode());
          } else {
            focusEl.setAttribute("data-placeholder", noteAskPlaceholder(noteAskMode()));
          }
          try {
            focusEl.focus();
          } catch (_) {}
        }
      });
    }
    form.querySelectorAll(".note-paie-mode, .note-paie-mode-menu [data-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setNoteAskMode(btn.getAttribute("data-mode"));
        closeNoteComposerMenus();
      });
    });
    loadUserAgents();
    if (compact) {
      compact.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = modeMenu && modeMenu.classList.contains("hidden");
        closeNoteComposerMenus(open ? modeMenu : null);
        if (modeMenu && open) {
          modeMenu.classList.remove("hidden");
          compact.classList.add("is-open");
          compact.setAttribute("aria-expanded", "true");
        }
        syncNoteFormatToolbarForComposer();
      });
    }
    if (modelBtn) {
      modelBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = modelMenu && modelMenu.classList.contains("hidden");
        closeNoteComposerMenus(open ? modelMenu : null);
        if (modelMenu && open) {
          loadGptModels();
          modelMenu.classList.remove("hidden");
          modelBtn.classList.add("is-open");
          modelBtn.setAttribute("aria-expanded", "true");
          if (modelSearch && !isMobileNoteLayout()) {
            try {
              modelSearch.focus();
            } catch (_) {}
          }
        }
        syncNoteFormatToolbarForComposer();
      });
    }
    if (modelSearch) {
      modelSearch.addEventListener("input", function () {
        renderGptModelMenu(modelSearch.value);
      });
      modelSearch.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }
    if (modeMenu) {
      modeMenu.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }
    if (modelMenu) {
      modelMenu.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }
    var kbMenu = form.querySelector(".note-paie-menu--kb");
    if (kbMenu) {
      kbMenu.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }
    form.addEventListener("focusin", function () {
      syncNoteFormatToolbarForComposer();
    });
    form.addEventListener("focusout", function () {
      window.setTimeout(syncNoteFormatToolbarForComposer, 80);
    });
    if (input) {
      function sizeComposer() {
        autosizeNoteComposer(input);
      }
      sizeComposer();
      window.requestAnimationFrame(sizeComposer);
      input.addEventListener("input", function () {
        autosizeNoteComposer(input);
        syncNotePaieReplyForm();
      });
      if (!input._composerRo && typeof ResizeObserver === "function") {
        input._composerRo = new ResizeObserver(function () {
          autosizeNoteComposer(input);
        });
        input._composerRo.observe(input);
      }
      input.addEventListener("keydown", function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }
      });
    }
    if (editor) {
      function sizeGptEditor() {
        autosizeNoteComposer(editor);
      }
      syncGptEditorEmpty(editor);
      sizeGptEditor();
      window.requestAnimationFrame(sizeGptEditor);
      editor.addEventListener("input", function () {
        syncGptEditorEmpty(editor);
        editor.setAttribute("data-placeholder", noteAskPlaceholder("gpt"));
        autosizeNoteComposer(editor);
        rememberGptEditorRange();
        syncNotePaieReplyForm();
        syncDiscussMentionMenu();
      });
      editor.addEventListener("keyup", function () {
        rememberGptEditorRange();
        syncDiscussMentionMenu();
      });
      editor.addEventListener("pointerup", function () {
        window.setTimeout(function () {
          rememberGptEditorRange();
          syncDiscussMentionMenu();
        }, 0);
      });
      editor.addEventListener("paste", function (e) {
        e.preventDefault();
        closeDiscussMentionMenu();
        var clip = e.clipboardData || window.clipboardData;
        var text = clip ? clip.getData("text/plain") : "";
        if (document.queryCommandSupported && document.queryCommandSupported("insertText")) {
          document.execCommand("insertText", false, text);
        } else if (text) {
          var sel = window.getSelection();
          if (sel && sel.rangeCount) {
            var r = sel.getRangeAt(0);
            r.deleteContents();
            r.insertNode(document.createTextNode(text));
            r.collapse(false);
          }
        }
        syncGptEditorEmpty(editor);
        autosizeNoteComposer(editor);
        rememberGptEditorRange();
        syncNotePaieReplyForm();
        syncDiscussMentionMenu();
      });
      editor.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          closeDiscussMentionMenu();
        }
        if (e.key === "Backspace" || e.key === "Delete") {
          var chip = selectedGptChip(editor) || adjacentGptChip(editor, e.key === "Backspace" ? -1 : 1);
          if (chip) {
            e.preventDefault();
            removeGptChip(editor, chip);
            closeDiscussMentionMenu();
            return;
          }
        }
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          closeDiscussMentionMenu();
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }
      });
      if (!editor._composerRo && typeof ResizeObserver === "function") {
        editor._composerRo = new ResizeObserver(function () {
          autosizeNoteComposer(editor);
        });
        editor._composerRo.observe(editor);
      }
    }
    if (!document._noteComposerMenuBound) {
      document._noteComposerMenuBound = true;
      document.addEventListener("click", function (e) {
        var t = e && e.target;
        if (t && t.closest && t.closest(".note-discuss-actions")) return;
        if (t && t.closest && t.closest("#note-discuss-mention-menu")) return;
        closeNoteComposerMenus();
        closeNoteAnswerMenu();
        closeDiscussMentionMenu();
      });
    }
    syncGptModelButton();
  }

  function isNoteDiscussionOpen() {
    var ov = document.getElementById("note-discussion-overlay");
    return !!(ov && !ov.classList.contains("hidden"));
  }

  function freezeNoteEditorUnderDiscussion() {
    resetNoteEditorOverlayPin();
    var sheet = document.querySelector("#note-editor-overlay .modal--sheet");
    var body = sheet && sheet.querySelector(".modal-body");
    if (sheet) {
      sheet.style.transform = "";
      sheet.style.height = "";
      sheet.style.maxHeight = "";
    }
    if (body) body.style.paddingBottom = "";
    document.documentElement.classList.remove("note-editor-kb-open");
    document.documentElement.style.removeProperty("--note-editor-kb-inset");
  }

  function resetDiscussionOverlayViewport() {
    var ov = document.getElementById("note-discussion-overlay");
    var sheet = ov && ov.querySelector(".note-discussion-sheet");
    if (ov) {
      ov.style.top = "";
      ov.style.left = "";
      ov.style.right = "";
      ov.style.bottom = "";
      ov.style.width = "";
      ov.style.height = "";
      ov.style.maxHeight = "";
      ov.style.transform = "";
    }
    if (sheet) {
      sheet.style.height = "";
      sheet.style.maxHeight = "";
      sheet.style.transform = "";
    }
  }

  function updateDiscussionOverlayForKeyboard() {
    var ov = document.getElementById("note-discussion-overlay");
    var sheet = ov && ov.querySelector(".note-discussion-sheet");
    if (!ov || !sheet || ov.classList.contains("hidden")) return;
    if (isDesktopLayout()) {
      resetDiscussionOverlayViewport();
      return;
    }
    freezeNoteEditorUnderDiscussion();
    var vv = window.visualViewport;
    var visH = noteEditorVisibleHeightPx();
    var layoutH = Math.max(
      window.innerHeight || 0,
      document.documentElement.clientHeight || 0,
      visH
    );
    var offsetTop = vv && typeof vv.offsetTop === "number" ? Math.round(vv.offsetTop) : 0;
    var offsetLeft = vv && typeof vv.offsetLeft === "number" ? Math.round(vv.offsetLeft) : 0;
    var visW =
      vv && typeof vv.width === "number" && vv.width > 0
        ? Math.round(vv.width)
        : window.innerWidth;
    ov.style.top = "0px";
    ov.style.left = "0px";
    ov.style.right = "auto";
    ov.style.bottom = "auto";
    ov.style.width = Math.max(window.innerWidth || 0, visW + offsetLeft) + "px";
    ov.style.height = Math.max(layoutH, visH + offsetTop) + "px";
    ov.style.maxHeight = "";
    ov.style.transform = "";
    var ovTop = Math.round(ov.getBoundingClientRect().top);
    var sheetShift = ovTop < -8 ? -ovTop : 0;
    var sheetH = visH > 0 ? visH : layoutH;
    sheet.style.height = sheetH + "px";
    sheet.style.maxHeight = sheetH + "px";
    sheet.style.transform = sheetShift ? "translateY(" + sheetShift + "px)" : "";
    if (window.scrollY || window.scrollX) {
      try {
        window.scrollTo(0, 0);
      } catch (_) {}
    }
  }

  function resetNoteSheetState(primaryBody) {
    destroyRightSheetEditor();
    if (noteSheetState.rightNotePersistTimer) {
      clearTimeout(noteSheetState.rightNotePersistTimer);
      noteSheetState.rightNotePersistTimer = null;
    }
    Object.keys(noteSheetState.sheetTimers || {}).forEach(function (id) {
      clearTimeout(noteSheetState.sheetTimers[id]);
    });
    noteSheetState.sheets = [];
    noteSheetState.leftId = NOTE_SHEET_MAIN;
    noteSheetState.rightMode = "discussion";
    noteSheetState.rightId = "";
    noteSheetState.rightNote = null;
    noteSheetState.focus = "left";
    noteSheetState.contextIds = null;
    noteSheetState.contextExplicit = false;
    noteSheetState.rightEditor = null;
    noteSheetState.primaryBody = primaryBody || "";
    noteSheetState.sheetTimers = {};
    noteSheetState.sheetPersists = {};
    noteSheetState.leftScroll = {};
    noteSheetState.skipCaretScroll = false;
    noteSheetState.enabled = false;
    syncNotePaneModeClass();
    renderNoteSheetChips();
    syncNotePaneHeader();
  }

  function noteSheetsShareWrap() {
    return document.getElementById("note-editor-more-wrap");
  }

  function noteSheetsNoteId() {
    var wrap = noteSheetsShareWrap();
    if (!wrap || wrap._shareKind !== "local") return "";
    return String(wrap._shareId || "");
  }

  function noteSheetsEnabled() {
    var wrap = noteSheetsShareWrap();
    return !!(noteSheetState.enabled && wrap && wrap._shareKind === "local" && wrap._shareId);
  }

  function notePaneSwitchEnabled() {
    var wrap = noteSheetsShareWrap();
    return !!(wrap && wrap._shareKind && wrap._shareId);
  }

  function currentDiscussSourceKey() {
    var wrap = noteSheetsShareWrap();
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    return String(wrap._shareKind) + ":" + String(wrap._shareId);
  }

  function findNoteSheet(id) {
    var want = String(id || NOTE_SHEET_MAIN);
    for (var i = 0; i < noteSheetState.sheets.length; i++) {
      if (String(noteSheetState.sheets[i].id) === want) return noteSheetState.sheets[i];
    }
    return null;
  }

  function upsertNoteSheet(item) {
    if (!item) return;
    var id = String(item.id);
    for (var i = 0; i < noteSheetState.sheets.length; i++) {
      if (String(noteSheetState.sheets[i].id) === id) {
        noteSheetState.sheets[i] = item;
        return;
      }
    }
    noteSheetState.sheets.push(item);
  }

  function extraNoteSheets() {
    return noteSheetState.sheets.filter(function (s) {
      return s && !s.is_primary && String(s.id) !== NOTE_SHEET_MAIN;
    });
  }

  function isPrimarySheetId(id) {
    return String(id || NOTE_SHEET_MAIN) === NOTE_SHEET_MAIN;
  }

  function focusedNoteSheetId() {
    if (
      noteSheetState.focus === "right" &&
      noteSheetState.rightMode === "sheet" &&
      noteSheetState.rightId
    ) {
      return String(noteSheetState.rightId);
    }
    return String(noteSheetState.leftId || NOTE_SHEET_MAIN);
  }

  function defaultContextSheetIds() {
    var ids = [NOTE_SHEET_MAIN];
    var focus = focusedNoteSheetId();
    if (focus && ids.indexOf(focus) < 0) ids.push(focus);
    return ids;
  }

  function selectedContextSheetIds() {
    if (noteSheetState.contextExplicit && Array.isArray(noteSheetState.contextIds)) {
      return noteSheetState.contextIds.slice();
    }
    return defaultContextSheetIds();
  }

  function setNoteSheetFocus(pane) {
    noteSheetState.focus = pane === "right" ? "right" : "left";
    if (!noteSheetState.contextExplicit) {
      noteSheetState.contextIds = defaultContextSheetIds();
      renderNoteKnowledgeMenu();
      syncNoteKnowledgeToggle();
    }
  }

  function syncNotePaneModeClass() {
    var sheetOpen =
      (noteSheetState.rightMode === "sheet" || noteSheetState.rightMode === "note") &&
      isNoteDiscussionOpen();
    document.documentElement.classList.toggle("note-discussion-sheet-open", sheetOpen);
    var pane = document.getElementById("note-discussion-sheet-pane");
    var msgs = document.getElementById("note-discussion-messages");
    if (pane) pane.classList.toggle("hidden", !sheetOpen);
    if (msgs) msgs.classList.toggle("hidden", !!sheetOpen);
  }

  function closeNotePaneSwitchMenu() {
    var menu = document.getElementById("note-pane-switch-menu");
    var btn = document.getElementById("note-discussion-title");
    if (menu) {
      menu.classList.add("hidden");
      menu.classList.remove("note-pane-switch-menu--picker");
    }
    if (btn) {
      btn.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    }
  }

  function syncNotePaneHeader() {
    var label = document.getElementById("note-discussion-title-label");
    var btn = document.getElementById("note-discussion-title");
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && wrap._shareKind === "chat") {
      var chatTitle = "Чат";
      for (var i = 0; i < (chatThreadsCache || []).length; i++) {
        if (String(chatThreadsCache[i].id) === String(wrap._shareId)) {
          chatTitle = String(chatThreadsCache[i].title || "Чат");
          break;
        }
      }
      syncChatDiscussionTitle(chatTitle);
      return;
    }
    var title = "Обсуждение";
    if (noteSheetState.rightMode === "sheet") {
      var sheet = findNoteSheet(noteSheetState.rightId);
      title = (sheet && sheet.title) || "Лист";
    } else if (noteSheetState.rightMode === "note" && noteSheetState.rightNote) {
      title =
        sanitizeNoteTitle(noteSheetState.rightNote.title || "") ||
        String(noteSheetState.rightNote.title || "Заметка");
    }
    if (label) label.textContent = title;
    if (btn) btn.title = title;
    var canSwitch = notePaneSwitchEnabled();
    if (btn) {
      btn.disabled = !canSwitch;
      btn.classList.toggle("note-pane-switch--plain", !canSwitch);
    }
    syncNotePaneModeClass();
  }

  function paneOpenNoteCandidates(query) {
    var wrap = noteSheetsShareWrap();
    var currentId = wrap && wrap._shareId ? String(wrap._shareId) : "";
    var currentKind = wrap && wrap._shareKind ? String(wrap._shareKind) : "";
    var rightId =
      noteSheetState.rightMode === "note" && noteSheetState.rightNote
        ? String(noteSheetState.rightNote.id || "")
        : "";
    var q = String(query || "").trim().toLowerCase();
    var out = [];
    var seen = {};
    function pushItem(n, kind) {
      if (!n) return;
      var id = String(n.id || "");
      if (!id) return;
      if (seen[id]) return;
      if (rightId && id === rightId) return;
      if (currentId && id === currentId) {
        if (currentKind === "journal") {
          if (kind === "journal") return;
        } else if (kind === "local" || kind === "knowledge") {
          return;
        }
      }
      var title =
        sanitizeNoteTitle(n.title || n.content || n.main_topic || "") ||
        String(n.title || n.content || n.main_topic || "Без названия");
      if (q) {
        var hay =
          (title + " " + notePlainExcerpt(n.description || n.body || n.preview || "", 80)).toLowerCase();
        if (hay.indexOf(q) < 0) return;
      }
      if (!noteMatchesPickerProjectFilter(n, panePickerActiveProjectId)) return;
      seen[id] = true;
      out.push({
        kind: kind,
        id: id,
        title: title,
        project: noteProjectOf(n),
        is_knowledge: !!(n.is_knowledge || n.role === "knowledge" || kind === "knowledge"),
        raw: n,
      });
    }
    ((notesDataCache && notesDataCache.local_notes) || []).forEach(function (n) {
      pushItem(n, "local");
    });
    ((knowledgeDataCache && knowledgeDataCache.notes) || []).forEach(function (n) {
      pushItem(n, "knowledge");
    });
    return out;
  }

  function ensurePaneOpenNoteCandidates() {
    var tasks = [];
    if (!notesDataCache || !Array.isArray(notesDataCache.local_notes)) {
      tasks.push(Promise.resolve(loadNotes()).catch(function () {}));
    }
    if (!knowledgeDataCache || !Array.isArray(knowledgeDataCache.notes)) {
      tasks.push(Promise.resolve(loadKnowledgeNotes()).catch(function () {}));
    }
    return Promise.all(tasks).then(function () {
      return paneOpenNoteCandidates("");
    });
  }

  function renderNotePaneOpenNotePicker(query, opts) {
    opts = opts || {};
    var menu = document.getElementById("note-pane-switch-menu");
    if (!menu) return;
    if (opts.resetProjectFilter) panePickerActiveProjectId = 0;
    menu.classList.add("note-pane-switch-menu--picker");
    menu.innerHTML = "";
    var back = document.createElement("button");
    back.type = "button";
    back.className = "note-pane-switch-item note-pane-switch-back";
    back.textContent = "← Назад";
    back.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      renderNotePaneSwitchMenu();
    });
    menu.appendChild(back);
    var searchRow = document.createElement("div");
    searchRow.className = "note-pane-switch-search-row";
    var search = document.createElement("input");
    search.type = "search";
    search.className = "note-pane-switch-search";
    search.placeholder = "Поиск заметок";
    search.autocomplete = "off";
    search.value = String(query || "");
    var createBtn = document.createElement("button");
    createBtn.type = "button";
    createBtn.className = "note-pane-switch-create";
    createBtn.setAttribute("aria-label", "Новая заметка");
    createBtn.title = "Новая заметка";
    createBtn.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
    createBtn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      createAndOpenNoteInRightPane();
    });
    searchRow.appendChild(search);
    searchRow.appendChild(createBtn);
    menu.appendChild(searchRow);
    var chips = document.createElement("div");
    chips.className = "note-pane-switch-project-chips notes-project-chips hidden";
    chips.setAttribute("role", "listbox");
    chips.setAttribute("aria-label", "Проекты");
    menu.appendChild(chips);
    var list = document.createElement("div");
    list.className = "note-pane-switch-note-list";
    menu.appendChild(list);
    function fill() {
      renderNotePickerProjectChips(
        chips,
        function () {
          return panePickerActiveProjectId;
        },
        function (v) {
          panePickerActiveProjectId = v;
        },
        fill
      );
      var q = search.value || "";
      var notes = paneOpenNoteCandidates(q);
      list.innerHTML = "";
      if (!notes.length) {
        var empty = document.createElement("p");
        empty.className = "note-pane-switch-empty";
        empty.textContent =
          notesDataCache || knowledgeDataCache
            ? String(q).trim() || panePickerActiveProjectId
              ? "Ничего не найдено"
              : "Других заметок нет"
            : "Загрузка…";
        list.appendChild(empty);
        return;
      }
      notes.forEach(function (n) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "note-pane-switch-item note-pane-switch-note";
        var title = document.createElement("span");
        title.className = "note-pane-switch-note-title";
        title.textContent = n.title;
        btn.appendChild(title);
        var projectName = (n.project && (n.project.name || n.project.title)) || "";
        if (projectName) {
          var meta = document.createElement("span");
          meta.className = "note-pane-switch-note-project";
          meta.textContent = String(projectName);
          btn.appendChild(meta);
        }
        btn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          openNoteInRightPane(n);
        });
        list.appendChild(btn);
      });
    }
    search.addEventListener("input", fill);
    search.addEventListener("click", function (e) {
      e.stopPropagation();
    });
    fill();
    window.setTimeout(function () {
      try {
        search.focus();
      } catch (_) {}
    }, 20);
  }

  function renderNotePaneSwitchMenu() {
    var menu = document.getElementById("note-pane-switch-menu");
    if (!menu) return;
    menu.classList.remove("note-pane-switch-menu--picker");
    menu.innerHTML = "";
    function addItem(label, active, onClick) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "note-pane-switch-item" + (active ? " is-active" : "");
      row.setAttribute("role", "menuitem");
      row.textContent = label;
      row.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        onClick();
      });
      menu.appendChild(row);
    }
    addItem("Обсуждение", noteSheetState.rightMode === "discussion", function () {
      closeNotePaneSwitchMenu();
      setRightPaneMode("discussion");
    });
    var discussSep = document.createElement("div");
    discussSep.className = "note-pane-switch-sep";
    menu.appendChild(discussSep);
    var leftOpenId = String(noteSheetState.leftId || NOTE_SHEET_MAIN);
    var otherSheets = (noteSheetState.sheets || []).filter(function (s) {
      return String(s.id) !== leftOpenId;
    });
    if (noteSheetsEnabled() || otherSheets.length) {
      var sheetsLabel = document.createElement("div");
      sheetsLabel.className = "note-pane-switch-label";
      sheetsLabel.textContent = "Листы заметки";
      menu.appendChild(sheetsLabel);
    }
    otherSheets.forEach(function (s) {
      addItem(
        s.title || "Лист",
        noteSheetState.rightMode === "sheet" && String(s.id) === String(noteSheetState.rightId),
        function () {
          closeNotePaneSwitchMenu();
          setRightPaneMode("sheet", s.id);
        }
      );
    });
    if (noteSheetsEnabled()) {
      addItem("+ Новый лист", false, function () {
        closeNotePaneSwitchMenu();
        createNoteSheet({ openOn: "right" });
      });
    }
    if (noteSheetState.rightMode === "note" && noteSheetState.rightNote) {
      var openTitle =
        sanitizeNoteTitle(noteSheetState.rightNote.title || "") ||
        String(noteSheetState.rightNote.title || "Заметка");
      addItem(openTitle, true, function () {
        closeNotePaneSwitchMenu();
      });
    }
    var sep = document.createElement("div");
    sep.className = "note-pane-switch-sep";
    menu.appendChild(sep);
    addItem("Открыть заметку", false, function () {
      renderNotePaneOpenNotePicker("", { resetProjectFilter: true });
      ensurePaneOpenNoteCandidates().then(function () {
        ensureUserTagsLoaded().finally(function () {
          var menuEl = document.getElementById("note-pane-switch-menu");
          if (menuEl && menuEl.classList.contains("note-pane-switch-menu--picker")) {
            renderNotePaneOpenNotePicker(
              (menuEl.querySelector(".note-pane-switch-search") || {}).value || ""
            );
          }
        });
      });
    });
  }

  function toggleNotePaneSwitchMenu() {
    if (!notePaneSwitchEnabled()) return;
    var menu = document.getElementById("note-pane-switch-menu");
    var btn = document.getElementById("note-discussion-title");
    if (!menu || !btn) return;
    var open = menu.classList.contains("hidden");
    if (open) {
      renderNotePaneSwitchMenu();
      menu.classList.remove("hidden");
      btn.classList.add("is-open");
      btn.setAttribute("aria-expanded", "true");
    } else {
      closeNotePaneSwitchMenu();
    }
  }

  function destroyRightSheetEditor() {
    if (noteSheetState.rightNotePersistTimer) {
      clearTimeout(noteSheetState.rightNotePersistTimer);
      noteSheetState.rightNotePersistTimer = null;
    }
    var inst = noteSheetState.rightEditor;
    if (inst && typeof inst.destroy === "function") {
      try {
        inst.destroy();
      } catch (_) {}
    }
    noteSheetState.rightEditor = null;
    var host = document.getElementById("note-discussion-sheet-host");
    var bar = document.getElementById("note-discussion-sheet-toolbar");
    if (host) host.innerHTML = "";
    if (bar) bar.innerHTML = "";
    if (noteSheetState.focus === "right") noteSheetState.focus = "left";
  }

  function scheduleRightForeignNotePersist() {
    if (!noteSheetState.rightNote || noteSheetState.rightMode !== "note") return;
    var rightNote = noteSheetState.rightNote;
    if (rightNote && rightNote.id && (rightNote.kind || "local") !== "journal") {
      writeLocalNoteDraft(rightNote.id, {
        title: String(rightNote.title || "").trim(),
        body: readSheetEditorHtml("right") || rightNote.body || rightNote.description || "",
        baseRevision: rightNote.revision,
        baseUpdatedAt: rightNote.updated_at,
      });
    }
    if (noteSheetState.rightNotePersistTimer) clearTimeout(noteSheetState.rightNotePersistTimer);
    noteSheetState.rightNotePersistTimer = setTimeout(function () {
      persistRightForeignNote().catch(function (e) {
        setNoteEditorSaveHint(e.message || "Не удалось сохранить заметку");
      });
    }, 1500);
  }

  async function persistRightForeignNote(snap) {
    var note = snap || noteSheetState.rightNote;
    if (!note || !note.id) return;
    var html =
      snap && snap.body != null
        ? snap.body
        : readSheetEditorHtml("right") || note.body || note.description || "";
    note.body = html;
    note.description = html;
    var title = String(note.title || "").trim() || "Без названия";
    var kind = note.kind === "knowledge" ? "local" : note.kind || "local";
    if (kind === "journal") {
      await apiFetch("/notes/journal/" + encodeURIComponent(String(note.id)), {
        method: "PATCH",
        body: JSON.stringify({ title: title, description: html }),
      });
      updateJournalInCache(note.id, note.operation, {
        id: note.id,
        main_topic: title,
        preview: notePlainExcerpt(html, 400),
      });
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      return;
    }
    writeLocalNoteDraft(note.id, {
      title: title,
      body: html,
      baseRevision: note.revision,
      baseUpdatedAt: note.updated_at,
    });
    var patchBody = { title: title, description: html };
    if (note.revision) patchBody.expected_revision = note.revision;
    var patchRes;
    try {
      patchRes = await apiFetch("/notes/local/" + encodeURIComponent(String(note.id)), {
        method: "PATCH",
        body: JSON.stringify(patchBody),
      });
    } catch (e) {
      if (e && e.status === 409 && e.conflictItem) {
        var remoteRight = e.conflictItem;
        if (notePayloadEquals(title, html, remoteRight.title, remoteRight.body || remoteRight.description)) {
          adoptNoteRevision(remoteRight, note.id);
          note.revision = remoteRight.revision || note.revision;
          note.updated_at = remoteRight.updated_at || note.updated_at;
          clearLocalNoteDraftIfUnchanged(note.id, title, html);
          return;
        }
        handleNotePatchConflict(note.id, remoteRight, title, html);
        return;
      }
      throw e;
    }
    clearLocalNoteDraftIfUnchanged(note.id, title, html);
    var item = (patchRes && patchRes.item) || {
      id: note.id,
      title: title,
      description: html,
      body: html,
      role: note.kind === "knowledge" ? "knowledge" : note.role || "",
      is_knowledge: note.kind === "knowledge",
      is_transcription: !!note.is_transcription,
      primary_sheet_title: note.primary_sheet_title || "",
    };
    stampTranscriptionFields(item, note);
    if (patchRes && patchRes.item) {
      note.revision = patchRes.item.revision || note.revision;
      note.updated_at = patchRes.item.updated_at || note.updated_at;
    }
    if (note.kind === "knowledge" || isKnowledgeNoteItem(item)) {
      item.role = "knowledge";
      item.is_knowledge = true;
      prependKnowledgeInCache(Object.assign({}, item, { tags: note.tags, project: note.project }));
    } else {
      syncLocalNoteInList(Object.assign({}, item, { tags: note.tags, project: note.project }));
    }
    if (noteSheetState.rightNote && String(noteSheetState.rightNote.id) === String(note.id)) {
      noteSheetState.rightNote.title = item.title || title;
      syncNotePaneHeader();
    }
  }

  function flushRightForeignNoteBeforeLeave() {
    if (noteSheetState.rightMode !== "note" || !noteSheetState.rightNote) return;
    if (noteSheetState.rightNotePersistTimer) {
      clearTimeout(noteSheetState.rightNotePersistTimer);
      noteSheetState.rightNotePersistTimer = null;
    }
    var note = noteSheetState.rightNote;
    var html = readSheetEditorHtml("right") || note.body || note.description || "";
    var snap = Object.assign({}, note, {
      body: html,
      description: html,
      title: String(note.title || "").trim() || "Без названия",
    });
    persistRightForeignNote(snap).catch(function () {});
  }

  async function openNoteInRightPane(item) {
    closeNotePaneSwitchMenu();
    try {
      flushRightForeignNoteBeforeLeave();
      var loaded = await fetchNoteForRightPane(item);
      if (!isNoteDiscussionOpen()) {
        openNoteDiscussion({ fromDesktopSync: true, focus: false });
      }
      await flushSheetEditor("right").catch(function () {});
      noteSheetState.rightMode = "note";
      noteSheetState.rightId = "";
      noteSheetState.rightNote = loaded;
      setNoteSheetFocus("right");
      syncNotePaneHeader();
      mountRightForeignNote(loaded);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function mountRightForeignNote(note) {
    var host = document.getElementById("note-discussion-sheet-host");
    var bar = document.getElementById("note-discussion-sheet-toolbar");
    if (!host || !note) return;
    destroyRightSheetEditor();
    host.innerHTML = "";
    if (bar) bar.innerHTML = "";
    var titleWrap = document.createElement("div");
    titleWrap.className = "note-discussion-right-note-title-wrap";
    var titleInput = document.createElement("textarea");
    titleInput.className = "note-discussion-right-note-title";
    titleInput.rows = 1;
    titleInput.spellcheck = true;
    titleInput.placeholder = "Заголовок";
    titleInput.value = String(note.title || "");
    titleInput.addEventListener("input", function () {
      note.title = titleInput.value;
      syncNotePaneHeader();
      scheduleRightForeignNotePersist();
      bindNoteTitleAutoresize(titleInput);
    });
    titleWrap.appendChild(titleInput);
    host.appendChild(titleWrap);
    var bodyHost = document.createElement("div");
    bodyHost.className = "note-discussion-right-note-body";
    host.appendChild(bodyHost);
    bindNoteTitleAutoresize(titleInput);
    mountNoteRichEditor(bodyHost, note.body || note.description || "", function () {
      scheduleRightForeignNotePersist();
    }, {
      toolbarParent: bar,
      showSharedToolbar: false,
      mountId: "td-desc-right-note",
      scrollParent: host,
    }).then(function (editor) {
      noteSheetState.rightEditor = editor;
      host.addEventListener(
        "focusin",
        function () {
          setNoteSheetFocus("right");
        },
        true
      );
    });
  }

  async function fetchNoteForRightPane(item) {
    var kind = item.kind === "knowledge" ? "local" : item.kind || "local";
    var id = String(item.id || "");
    if (!id) throw new Error("Нет id заметки");
    if (kind === "journal") {
      var jres = await apiFetch("/notes/journal/" + encodeURIComponent(id), { method: "GET" });
      var jit = (jres && jres.item) || item.raw || {};
      return {
        kind: "journal",
        id: id,
        title: journalTitleForEditor(jit, item.raw || jit),
        body: prepareJournalBodyForEditor(jit),
        description: prepareJournalBodyForEditor(jit),
        operation: jit.operation || (item.raw && item.raw.operation) || "",
        tags: noteTagsOf(jit),
        project: noteProjectOf(jit),
      };
    }
    var res = await apiFetch("/notes/local/" + encodeURIComponent(id), { method: "GET" });
    var it = (res && res.item) || item.raw || {};
    var isKb = item.kind === "knowledge" || isKnowledgeNoteItem(it) || item.is_knowledge;
    var loaded = {
      kind: isKb ? "knowledge" : "local",
      id: String(it.id || id),
      title: sanitizeNoteTitle(it.title || it.content || "") || String(it.title || "Без названия"),
      body: it.body || it.description || "",
      description: it.body || it.description || "",
      tags: noteTagsOf(it),
      project: noteProjectOf(it),
      revision: it.revision,
      updated_at: it.updated_at,
    };
    var hydrated = hydrateLocalNoteWithDraft(loaded);
    if (hydrated.use === "draft") {
      loaded.title = hydrated.title;
      loaded.body = hydrated.body;
      loaded.description = hydrated.body;
    }
    return loaded;
  }

  async function createAndOpenNoteInRightPane() {
    closeNotePaneSwitchMenu();
    try {
      var res = await apiFetch("/notes/local", {
        method: "POST",
        body: JSON.stringify({ title: "Новая заметка", description: "" }),
      });
      var item = (res && res.item) || null;
      if (!item || item.id == null) throw new Error("Не удалось создать заметку");
      prependLocalInCache(item);
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      await openNoteInRightPane({
        kind: "local",
        id: item.id,
        title: item.title || "Новая заметка",
        raw: item,
      });
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function previewOpenNoteFromSheets() {
    var body = getNoteEditorBodyEl();
    if (body && typeof body._notePreviewList === "function") body._notePreviewList();
  }

  function scheduleExtraSheetPersist(sheetId) {
    var id = String(sheetId || "");
    if (!id || isPrimarySheetId(id)) return;
    previewOpenNoteFromSheets();
    if (noteSheetState.sheetTimers[id]) clearTimeout(noteSheetState.sheetTimers[id]);
    noteSheetState.sheetTimers[id] = setTimeout(function () {
      persistExtraSheet(id).catch(function (e) {
        setNoteEditorSaveHint(e.message || "Не удалось сохранить лист");
      });
    }, 1500);
  }

  function readSheetEditorHtml(which) {
    var inst = which === "right" ? noteSheetState.rightEditor : getLeftNoteRichEditor();
    if (inst && typeof inst.getHtml === "function") return inst.getHtml();
    return "";
  }

  function flushSheetEditor(which) {
    var id = which === "right" ? noteSheetState.rightId : noteSheetState.leftId;
    if (!id) return Promise.resolve();
    if (noteSheetState.sheetTimers[id]) {
      clearTimeout(noteSheetState.sheetTimers[id]);
      delete noteSheetState.sheetTimers[id];
    }
    if (isPrimarySheetId(id)) {
      var html = readSheetEditorHtml(which);
      if (html) noteSheetState.primaryBody = html;
      return Promise.resolve();
    }
    return persistExtraSheet(id);
  }

  async function persistExtraSheet(sheetId) {
    var id = String(sheetId || "");
    if (!id || isPrimarySheetId(id)) return;
    var prev = noteSheetState.sheetPersists[id];
    var run = function () {
      return persistExtraSheetNow(id);
    };
    var next = prev ? prev.then(run, run) : run();
    noteSheetState.sheetPersists[id] = next;
    try {
      return await next;
    } finally {
      if (noteSheetState.sheetPersists[id] === next) delete noteSheetState.sheetPersists[id];
    }
  }

  async function persistExtraSheetNow(sheetId, retried) {
    var id = String(sheetId || "");
    var noteId = noteSheetsNoteId();
    var sheet = findNoteSheet(id);
    if (!id || !noteId || !sheet || isPrimarySheetId(id)) return;
    var html = "";
    if (String(noteSheetState.leftId) === id) html = readSheetEditorHtml("left");
    else if (String(noteSheetState.rightId) === id) html = readSheetEditorHtml("right");
    else html = sheet.body || sheet.description || "";
    var title = sheet.title || "";
    var body = {
      title: title,
      description: html,
      expected_revision: sheet.revision,
    };
    if (isAppOffline() || String(noteId).indexOf("tmp_") === 0) {
      sheet.body = html;
      sheet.description = html;
      enqueueOfflineOp({
        type: "sheet_patch",
        clientId: id,
        noteId: String(noteId),
        path:
          "/notes/local/" +
          encodeURIComponent(String(noteId)) +
          "/sheets/" +
          encodeURIComponent(id),
        method: "PATCH",
        body: { title: title, description: html },
      });
      showOfflineSavedToast();
      return;
    }
    try {
      var res = await apiFetch(
        "/notes/local/" +
          encodeURIComponent(String(noteId)) +
          "/sheets/" +
          encodeURIComponent(id),
        { method: "PATCH", body: JSON.stringify(body) }
      );
      if (res && res.item) {
        var saved = res.item;
        saved.body = html;
        saved.description = html;
        saved.title = title;
        upsertNoteSheet(saved);
        previewOpenNoteFromSheets();
      }
    } catch (e) {
      var remote = e && e.conflictItem;
      if (e && e.status === 409 && remote) {
        if (sheet) sheet.revision = remote.revision || sheet.revision;
        if (!retried) return persistExtraSheetNow(id, true);
        var err = new Error("Лист изменён в другой вкладке. Сохраните ещё раз.");
        err.status = 409;
        throw err;
      }
      throw e;
    }
  }

  function mountRightSheetEditor(sheet) {
    var host = document.getElementById("note-discussion-sheet-host");
    var bar = document.getElementById("note-discussion-sheet-toolbar");
    if (!host || !sheet) return;
    destroyRightSheetEditor();
    host.innerHTML = "";
    if (bar) bar.innerHTML = "";
    mountNoteRichEditor(host, sheet.body || sheet.description || "", function () {
      if (String(noteSheetState.rightId) === String(sheet.id) && !isPrimarySheetId(sheet.id)) {
        scheduleExtraSheetPersist(sheet.id);
      } else if (isPrimarySheetId(sheet.id)) {
        noteSheetState.primaryBody = readSheetEditorHtml("right");
        var leftFlush = getNoteEditorBodyEl();
        if (leftFlush && typeof leftFlush._noteEditorSchedule === "function") {
          leftFlush._noteEditorSchedule();
        }
      }
    }, {
      toolbarParent: bar,
      showSharedToolbar: false,
      mountId: "td-desc-right",
      scrollParent: host,
    }).then(function (editor) {
      noteSheetState.rightEditor = editor;
      host.addEventListener(
        "focusin",
        function () {
          setNoteSheetFocus("right");
        },
        true
      );
    });
  }

  function setRightPaneMode(mode, sheetId) {
    if (mode === "sheet") {
      var sheet = findNoteSheet(sheetId || noteSheetState.rightId || NOTE_SHEET_MAIN);
      if (!sheet) sheet = findNoteSheet(NOTE_SHEET_MAIN);
      if (!sheet) return;
      if (!isNoteDiscussionOpen()) openNoteDiscussion({ fromDesktopSync: true, focus: false });
      flushRightForeignNoteBeforeLeave();
      noteSheetState.rightMode = "sheet";
      noteSheetState.rightId = String(sheet.id);
      noteSheetState.rightNote = null;
      setNoteSheetFocus("right");
      syncNotePaneHeader();
      mountRightSheetEditor(sheet);
      return;
    }
    if (mode === "note") {
      return;
    }
    flushRightForeignNoteBeforeLeave();
    flushSheetEditor("right").catch(function () {});
    noteSheetState.rightMode = "discussion";
    noteSheetState.rightId = "";
    noteSheetState.rightNote = null;
    destroyRightSheetEditor();
    setNoteSheetFocus("left");
    syncNotePaneHeader();
  }

  function applyLeftSheetHtml(sheet) {
    var editor = getLeftNoteRichEditor();
    var html = (sheet && (sheet.body || sheet.description)) || "";
    if (editor && typeof editor.setHtml === "function") editor.setHtml(html);
  }

  function saveLeftSheetScroll() {
    var el = getNoteEditorScrollEl();
    if (!el) return;
    if (!noteSheetState.leftScroll) noteSheetState.leftScroll = {};
    noteSheetState.leftScroll[String(noteSheetState.leftId || NOTE_SHEET_MAIN)] = el.scrollTop;
  }

  function restoreLeftSheetScroll(sheetId) {
    var el = getNoteEditorScrollEl();
    if (!el) return;
    var saved = noteSheetState.leftScroll && noteSheetState.leftScroll[String(sheetId || NOTE_SHEET_MAIN)];
    el.scrollTop = typeof saved === "number" ? saved : 0;
  }

  async function switchLeftSheet(sheetId) {
    var next = findNoteSheet(sheetId);
    if (!next) return;
    var prevId = String(noteSheetState.leftId || NOTE_SHEET_MAIN);
    if (String(next.id) === prevId) {
      setNoteSheetFocus("left");
      renderNoteSheetChips();
      return;
    }
    saveLeftSheetScroll();
    noteSheetState.skipCaretScroll = true;
    await flushSheetEditor("left");
    if (isPrimarySheetId(prevId)) {
      noteSheetState.primaryBody = readSheetEditorHtml("left") || noteSheetState.primaryBody;
      var main = findNoteSheet(NOTE_SHEET_MAIN);
      if (main) {
        main.body = noteSheetState.primaryBody;
        main.description = noteSheetState.primaryBody;
      }
    }
    noteSheetState.leftId = String(next.id);
    var transNote = resolveLocalNoteFromCache({ id: noteSheetsNoteId() });
    var pad = document.querySelector("#note-editor-overlay .note-editor-pad--body");
    if (
      isTranscriptionNoteItem(transNote) &&
      transcriptionIsGenerating(transNote) &&
      isPrimarySheetId(next.id) &&
      pad
    ) {
      pad.innerHTML = noteSheetSkeletonHtml();
    } else {
      applyLeftSheetHtml(next);
    }
    setNoteSheetFocus("left");
    renderNoteSheetChips();
    var restore = function () {
      restoreLeftSheetScroll(next.id);
    };
    restore();
    requestAnimationFrame(function () {
      restore();
      requestAnimationFrame(function () {
        restore();
        window.setTimeout(function () {
          restore();
          noteSheetState.skipCaretScroll = false;
        }, 80);
      });
    });
  }

  function renderNoteSheetChips() {
    var host = document.getElementById("note-editor-sheets");
    if (!host) return;
    host.innerHTML = "";
    if (!noteSheetsEnabled()) {
      host.classList.add("hidden");
      return;
    }
    var transNote = resolveLocalNoteFromCache({ id: noteSheetsNoteId() });
    var isTrans = isTranscriptionNoteItem(transNote);
    if (isTrans) {
      host.classList.remove("hidden");
      noteSheetState.sheets.forEach(function (s) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className =
          "note-sheet-chip" +
          (String(s.id) === String(noteSheetState.leftId) ? " is-active" : "");
        var label = document.createElement("span");
        label.className = "note-sheet-chip-label";
        label.textContent = s.title || "Лист";
        chip.appendChild(label);
        chip.addEventListener("click", function (e) {
          e.preventDefault();
          switchLeftSheet(s.id);
        });
        host.appendChild(chip);
      });
      if (!transcriptionHasSummary(transNote) && !transcriptionIsGenerating(transNote)) {
        var makeBtn = document.createElement("button");
        makeBtn.type = "button";
        makeBtn.className = "note-sheet-chip note-sheet-chip--make-summary";
        makeBtn.textContent = "Сделать саммари";
        makeBtn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          requestTranscriptionSummary();
        });
        host.appendChild(makeBtn);
      }
      return;
    }
    var extras = extraNoteSheets();
    var showBar = extras.length > 0;
    host.classList.toggle("hidden", !showBar && extras.length === 0);
    if (!showBar) {
      var quiet = document.createElement("button");
      quiet.type = "button";
      quiet.className = "note-sheet-chip note-sheet-chip--add";
      quiet.textContent = "＋ лист";
      quiet.addEventListener("click", function (e) {
        e.preventDefault();
        createNoteSheet({ openOn: "left" });
      });
      host.appendChild(quiet);
      host.classList.remove("hidden");
      return;
    }
    noteSheetState.sheets.forEach(function (s) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className =
        "note-sheet-chip" +
        (String(s.id) === String(noteSheetState.leftId) ? " is-active" : "");
      var label = document.createElement("span");
      label.className = "note-sheet-chip-label";
      label.textContent = s.title || "Лист";
      chip.appendChild(label);
      chip.addEventListener("click", function (e) {
        e.preventDefault();
        if (String(s.id) === String(noteSheetState.leftId) && !s.is_primary) {
          startNoteSheetRename(chip, s);
          return;
        }
        switchLeftSheet(s.id);
      });
      host.appendChild(chip);
    });
    var add = document.createElement("button");
    add.type = "button";
    add.className = "note-sheet-chip note-sheet-chip--add";
    add.textContent = "+";
    add.setAttribute("aria-label", "Новый лист");
    add.addEventListener("click", function (e) {
      e.preventDefault();
      createNoteSheet({ openOn: "left" });
    });
      host.appendChild(add);
  }

  var transcriptionSummaryPollTimer = null;

  function confirmYesNoDialog(message) {
    return new Promise(function (resolve) {
      var existing = document.getElementById("leo-prompt-overlay");
      if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
      var ov = document.createElement("div");
      ov.id = "leo-prompt-overlay";
      ov.className = "leo-prompt-overlay";
      ov.innerHTML =
        '<div class="leo-prompt-sheet" role="dialog" aria-modal="true">' +
        '<p class="leo-prompt-message"></p>' +
        '<div class="leo-prompt-actions">' +
        '<button type="button" class="btn-text leo-prompt-cancel">Нет</button>' +
        '<button type="button" class="btn leo-prompt-ok">Да</button>' +
        "</div></div>";
      var msg = ov.querySelector(".leo-prompt-message");
      if (msg) msg.textContent = String(message || "");
      function finish(ok) {
        if (ov.parentNode) ov.parentNode.removeChild(ov);
        document.removeEventListener("keydown", onKey, true);
        resolve(!!ok);
      }
      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          finish(false);
        }
        if (e.key === "Enter") {
          e.preventDefault();
          finish(true);
        }
      }
      ov.querySelector(".leo-prompt-cancel").addEventListener("click", function () {
        finish(false);
      });
      ov.querySelector(".leo-prompt-ok").addEventListener("click", function () {
        finish(true);
      });
      ov.addEventListener("click", function (e) {
        if (e.target === ov) finish(false);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(ov);
    });
  }

  function noteSheetSkeletonHtml() {
    return (
      '<div class="note-sheet-skeleton" aria-hidden="true" aria-busy="true">' +
      '<div class="note-sheet-skeleton-row" style="width:74%"></div>' +
      '<div class="note-sheet-skeleton-row note-sheet-skeleton-row--tall" style="width:88%"></div>' +
      '<div class="note-sheet-skeleton-row" style="width:62%"></div>' +
      '<div class="note-sheet-skeleton-row note-sheet-skeleton-row--tall" style="width:80%"></div>' +
      '<div class="note-sheet-skeleton-row" style="width:70%"></div>' +
      "</div>"
    );
  }

  function stopTranscriptionSummaryPoll() {
    if (transcriptionSummaryPollTimer) {
      clearTimeout(transcriptionSummaryPollTimer);
      transcriptionSummaryPollTimer = null;
    }
  }

  function pollTranscriptionSummary(noteId) {
    stopTranscriptionSummaryPoll();
    var id = String(noteId || "");
    if (!id) return;
    transcriptionSummaryPollTimer = setTimeout(function () {
      apiFetch("/notes/local/" + encodeURIComponent(id), { method: "GET" })
        .then(function (res) {
          var item = res && res.item;
          if (!item) return;
          prependTranscriptionInCache(item);
          if (transcriptionIsGenerating(item)) {
            pollTranscriptionSummary(id);
            return;
          }
          if (noteSheetsNoteId() === id) {
            openNoteDetail(item, { isLocal: true });
          } else if (notesDataCache) {
            renderNotesPanesFromData(notesDataCache);
          }
        })
        .catch(function () {
          pollTranscriptionSummary(id);
        });
    }, 1600);
  }

  async function requestTranscriptionSummary() {
    var ok = await confirmYesNoDialog(
      "У этого файла еще нет саммари. Хотите его сделать?"
    );
    if (!ok) return;
    var noteId = noteSheetsNoteId();
    if (!noteId) return;
    try {
      var res = await apiFetch(
        "/notes/local/" + encodeURIComponent(String(noteId)) + "/make-summary",
        { method: "POST" }
      );
      var item = res && res.item;
      if (item) {
        prependTranscriptionInCache(item);
        openNoteDetail(item, { isLocal: true });
        pollTranscriptionSummary(noteId);
      }
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function startNoteSheetRename(chip, sheet) {
    if (!chip || !sheet || sheet.is_primary) return;
    var input = document.createElement("input");
    input.type = "text";
    input.className = "note-sheet-chip-input";
    input.value = sheet.title || "";
    chip.innerHTML = "";
    chip.appendChild(input);
    input.focus();
    input.select();
    var done = false;
    function finish(ok) {
      if (done) return;
      done = true;
      var next = String(input.value || "").trim() || sheet.title;
      if (ok && next !== sheet.title) {
        sheet.title = next;
        persistExtraSheet(sheet.id).catch(function () {});
        if (String(noteSheetState.rightId) === String(sheet.id)) syncNotePaneHeader();
      }
      renderNoteSheetChips();
      renderNoteKnowledgeMenu();
    }
    input.addEventListener("blur", function () {
      finish(true);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      }
      if (e.key === "Escape") {
        e.preventDefault();
        finish(false);
      }
    });
  }

  async function ensureNoteIdForSheets() {
    var id = noteSheetsNoteId();
    if (id && String(id).indexOf("tmp_") !== 0) return id;
    var body = getNoteEditorBodyEl();
    if (body && typeof body._noteEditorFlush === "function") {
      await body._noteEditorFlush();
    }
    return noteSheetsNoteId();
  }

  async function createNoteSheet(opts) {
    opts = opts || {};
    var noteId = await ensureNoteIdForSheets();
    if (!noteId) {
      showNoteToast("Сначала сохраните заметку");
      return;
    }
    var title = opts.title || "";
    if (isAppOffline() || String(noteId).indexOf("tmp_") === 0) {
      var tmpId = newTempId("tmp_sheet_");
      var local = {
        id: tmpId,
        note_id: noteId,
        title: title || "Лист " + (extraNoteSheets().length + 2),
        body: "",
        description: "",
        revision: 1,
        is_primary: false,
      };
      upsertNoteSheet(local);
      enqueueOfflineOp({
        type: "sheet_create",
        clientId: tmpId,
        noteId: String(noteId),
        path: "/notes/local/" + encodeURIComponent(String(noteId)) + "/sheets",
        method: "POST",
        body: { title: local.title },
      });
      renderNoteSheetChips();
      if (opts.openOn === "right") setRightPaneMode("sheet", tmpId);
      else switchLeftSheet(tmpId);
      showOfflineSavedToast();
      return;
    }
    try {
      var res = await apiFetch(
        "/notes/local/" + encodeURIComponent(String(noteId)) + "/sheets",
        { method: "POST", body: JSON.stringify({ title: title }) }
      );
      var item = res && res.item;
      if (!item) return;
      upsertNoteSheet(item);
      renderNoteSheetChips();
      renderNoteKnowledgeMenu();
      if (opts.openOn === "right") setRightPaneMode("sheet", item.id);
      else await switchLeftSheet(item.id);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function hydrateNoteSheets(note, primaryBody) {
    noteSheetState.enabled = !!(note && !isKnowledgeNoteItem(note));
    noteSheetState.primaryBody = primaryBody || (note && (note.body || note.description)) || "";
    noteSheetState.leftId = NOTE_SHEET_MAIN;
    noteSheetState.rightMode = "discussion";
    noteSheetState.rightId = "";
    noteSheetState.rightNote = null;
    noteSheetState.focus = "left";
    noteSheetState.contextExplicit = false;
    noteSheetState.contextIds = null;
    noteSheetState.sheets = [
      {
        id: NOTE_SHEET_MAIN,
        title: (note && note.primary_sheet_title) || "Основная",
        body: noteSheetState.primaryBody,
        description: noteSheetState.primaryBody,
        revision: (note && note.revision) || 1,
        is_primary: true,
      },
    ];
    renderNoteSheetChips();
    syncNotePaneHeader();
    var noteId = note && note.id;
    if (!noteSheetsEnabled() || !noteId || String(noteId).indexOf("tmp_") === 0) return;
    apiFetch("/notes/local/" + encodeURIComponent(String(noteId)) + "/sheets", {
      method: "GET",
    })
      .then(function (data) {
        if (noteSheetsNoteId() !== String(noteId)) return;
        var rows = (data && data.sheets) || [];
        if (rows.length) noteSheetState.sheets = rows;
        var main = findNoteSheet(NOTE_SHEET_MAIN);
        if (main) noteSheetState.primaryBody = main.body || main.description || noteSheetState.primaryBody;
        renderNoteSheetChips();
        syncNotePaneHeader();
        renderNoteKnowledgeMenu();
      })
      .catch(function () {});
  }

  function bindNotePaneSwitchOnce() {
    var btn = document.getElementById("note-discussion-title");
    if (!btn || btn._sheetBound) return;
    btn._sheetBound = true;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      toggleNotePaneSwitchMenu();
    });
    document.addEventListener("click", function (e) {
      var wrap = document.querySelector(".note-pane-switch-wrap");
      if (wrap && e.target && wrap.contains(e.target)) return;
      closeNotePaneSwitchMenu();
    });
  }

  function bindNoteDiscussionOverlayOnce() {
    var ov = document.getElementById("note-discussion-overlay");
    if (!ov || ov._bound) return ov;
    ov._bound = true;
    bindNotePaneSwitchOnce();
    bindDiscussClarifyOnce();
    var closeBtn = document.getElementById("note-discussion-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function (e) {
        e.preventDefault();
        closeNoteDiscussion();
      });
    }
    var newCtxBtn = document.getElementById("note-discussion-new-ctx");
    if (newCtxBtn && !newCtxBtn._bound) {
      newCtxBtn._bound = true;
      newCtxBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        clearGptDiscussionContext();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isNoteDiscussionOpen()) {
        if (document.getElementById("discuss-media-viewer")) {
          closeDiscussMediaViewer();
          e.preventDefault();
          return;
        }
        if (document.getElementById("note-link-overlay") &&
            !document.getElementById("note-link-overlay").classList.contains("hidden")) {
          return;
        }
        closeNoteDiscussion();
      }
    });
    if (window.matchMedia) {
      var desk = window.matchMedia("(min-width: 960px)");
      var onDesk = function (e) {
        applyNotePaneWidths();
        if (e.matches) syncDesktopNoteDiscussion();
        else if (isNoteDiscussionOpen()) closeNoteDiscussion(true);
        loadActual();
      };
      if (desk.addEventListener) desk.addEventListener("change", onDesk);
      else if (desk.addListener) desk.addListener(onDesk);
    }
    document.addEventListener(
      "touchmove",
      function (e) {
        if (!isNoteDiscussionOpen() || isDesktopLayout()) return;
        var t = e.target;
        if (!t || !t.closest) {
          e.preventDefault();
          return;
        }
        if (!t.closest("#note-discussion-overlay")) {
          e.preventDefault();
          return;
        }
        if (
          t.closest(
            ".note-discussion-scroll, .notes-project-chips, .note-paie-plus-notes, .note-paie-plus-menu, .note-pane-switch-menu, .note-paie-menu, textarea, input, .note-paie-composer-editor, [contenteditable='true']"
          )
        ) {
          return;
        }
        e.preventDefault();
      },
      { passive: false }
    );
    ov.addEventListener("focusin", function () {
      if (isNoteDiscussionOpen()) updateDiscussionOverlayForKeyboard();
    });
    return ov;
  }

  function syncNoteDiscussionOpenClass() {
    var open = isNoteDiscussionOpen();
    document.documentElement.classList.toggle("note-discussion-open", open);
    var sheet = document.querySelector("#note-discussion-overlay .note-discussion-sheet");
    if (sheet) {
      sheet.setAttribute("aria-modal", open && isDesktopLayout() ? "false" : "true");
    }
    applyNotePaneWidths();
  }

  function shouldKeepNoteDiscussionOpen() {
    var wrap = document.getElementById("note-editor-more-wrap");
    return (
      isDesktopLayout() &&
      isNoteEditorModalOpen() &&
      wrap &&
      wrap._shareKind &&
      wrap._shareId &&
      !wrap._discussionDismissed
    );
  }

  function syncDesktopNoteDiscussion() {
    if (!shouldKeepNoteDiscussionOpen()) return;
    if (isNoteDiscussionOpen()) {
      syncNoteDiscussionOpenClass();
      return;
    }
    openNoteDiscussion({ fromDesktopSync: true, focus: false });
  }

  function closeNoteDiscussion(force) {
    stopDiscussionLiveUpdates();
    leaveDiscussPresence();
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!force && isDesktopLayout() && isNoteEditorModalOpen() && wrap) {
      wrap._discussionDismissed = true;
    }
    var ov = document.getElementById("note-discussion-overlay");
    if (ov) {
      ov.classList.add("hidden");
      ov.setAttribute("aria-hidden", "true");
    }
    closeNoteAnswerMenu();
    closeNoteComposerMenus();
    hideDiscussClarify();
    resetDiscussionOverlayViewport();
    if (noteSheetState.rightMode === "note") {
      flushRightForeignNoteBeforeLeave();
      noteSheetState.rightMode = "discussion";
      noteSheetState.rightId = "";
      noteSheetState.rightNote = null;
      destroyRightSheetEditor();
      setNoteSheetFocus("left");
    } else {
      flushSheetEditor("right").catch(function () {});
      if (noteSheetState.rightMode === "sheet") {
        noteSheetState.rightMode = "discussion";
        noteSheetState.rightId = "";
        destroyRightSheetEditor();
        setNoteSheetFocus("left");
      }
    }
    closeNotePaneSwitchMenu();
    syncNotePaneHeader();
    syncNoteDiscussionOpenClass();
    syncAppOverlay();
    syncTelegramNativeBack();
    updateNoteDiscussionCardUnread();
    if (isNoteEditorModalOpen()) updateModalSheetForKeyboard();
    if (notesSubTab === "chats") {
      activeChatThreadId = "";
      restoreDiscussionOverlayHost();
      document.documentElement.classList.remove("chats-split-open");
      clearChatShareWrap();
      renderChatThreadsList();
      if (!leavingChatsTab && isDesktopLayout()) {
        ensureDesktopChatComposer();
      } else {
        var empty = document.getElementById("chat-thread-empty");
        if (empty) setHidden(empty, false);
      }
    }
    syncChatDiscussionChrome();
  }

  function highlightDiscussionMessage(id) {
    document.querySelectorAll("#note-discussion-messages .note-discuss-msg.is-topic").forEach(function (n) {
      n.classList.remove("is-topic");
    });
    if (!id) return;
    var el = document.querySelector(
      '#note-discussion-messages [data-comment-id="' + String(id) + '"]'
    );
    if (el) el.classList.add("is-topic");
  }

  function scrollDiscussionToComment(id) {
    if (!id) return;
    var el = document.querySelector(
      '#note-discussion-messages [data-comment-id="' + String(id) + '"]'
    );
    var scroll = document.querySelector("#note-discussion-overlay .note-discussion-scroll");
    if (!el || !scroll) return;
    var er = el.getBoundingClientRect();
    var sr = scroll.getBoundingClientRect();
    scroll.scrollTop += er.top - sr.top - 16;
    highlightDiscussionMessage(id);
  }

  function scrollNoteToQuote(comment) {
    if (!comment || !String(comment.quote || "").trim()) return;
    var api = window.NoteComments;
    var root = noteEditorCommentRoot();
    if (!api || !api.rangeForAnchor || !root) return;
    var range = api.rangeForAnchor(root, comment.quote, comment.prefix, comment.suffix);
    if (!range) return;
    var node = range.startContainer;
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    if (el && el.scrollIntoView) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function revealNoteDiscussionTopic(comment, opts) {
    opts = opts || {};
    if (!comment) return;
    var panel = document.getElementById("note-editor-comments");
    var comments = (panel && panel._allComments) || [];
    var api = window.NoteComments;
    var rootC = comment;
    if (api && api.topicRoot) rootC = api.topicRoot(comments, comment.id) || comment;
    if (panel && rootC && rootC.id) panel._activeCommentId = rootC.id;
    var wrap = document.getElementById("note-editor-more-wrap");
    paintEditorCommentOverlay(comments, rootC && rootC.id);
    if (opts.fromDiscussion) {
      if (wrap) wrap._discussionPinId = null;
      highlightDiscussionMessage(comment && comment.id);
      scrollNoteToQuote(rootC || comment);
      if (!isDesktopLayout()) closeNoteDiscussion(true);
      return;
    }
    if (wrap) wrap._discussionPinId = rootC && rootC.id;
    openNoteDiscussion({
      focus: false,
      scrollToId: rootC && rootC.id,
    });
  }

  function ruMessageCount(n) {
    var num = Number(n) || 0;
    var n10 = num % 10;
    var n100 = num % 100;
    if (n10 === 1 && n100 !== 11) return num + " сообщение";
    if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return num + " сообщения";
    return num + " сообщений";
  }

  function discussionComments(comments) {
    return (comments || []).filter(function (c) {
      return c && c.id;
    });
  }

  function renderNoteDiscussionCard(comments) {
    var col = document.querySelector("#note-editor-modal-body .note-editor-main-column");
    var card = document.getElementById("note-discussion-card");
    var rows = discussionComments(comments);
    if (!col || !rows.length) {
      if (card && card.parentNode) card.parentNode.removeChild(card);
      return;
    }
    if (!card) {
      card = document.createElement("button");
      card.type = "button";
      card.id = "note-discussion-card";
      card.className = "note-discussion-card";
      card.innerHTML =
        '<div><p class="note-discussion-card-title">Обсуждение</p>' +
        '<p class="note-discussion-card-meta"></p></div>' +
        '<span class="note-discussion-card-chevron" aria-hidden="true">›</span>';
      card.addEventListener("click", function (e) {
        e.preventDefault();
        openNoteDiscussion();
      });
      card.addEventListener("touchend", function (e) {
        e.preventDefault();
        openNoteDiscussion();
      });
      col.appendChild(card);
    }
    var meta = card.querySelector(".note-discussion-card-meta");
    if (meta) meta.textContent = ruMessageCount(rows.length);
    updateNoteDiscussionCardUnread();
  }

  function updateNoteDiscussionCardUnread() {
    var card = document.getElementById("note-discussion-card");
    if (!card) return;
    var wrap = document.getElementById("note-editor-more-wrap");
    var on = !!(
      wrap &&
      noteHasDiscussUnread(wrap._shareKind, wrap._shareId) &&
      !isNoteDiscussionOpen()
    );
    card.classList.toggle("has-discuss-unread", on);
  }

  function openNoteDiscussion(opts) {
    opts = opts || {};
    bindNoteDiscussionOverlayOnce();
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return;
    var panel = document.getElementById("note-editor-comments");
    var maxId = maxCommentId((panel && panel._allComments) || []);
    if (maxId) markDiscussSeenUpTo(wrap._shareKind, wrap._shareId, maxId);
    else clearDiscussUnread(wrap._shareKind, wrap._shareId);
    ensureNotePaieThread();
    var draftQuote = opts.draft ? String(opts.draft.quote || "").trim() : "";
    var mode = opts.mode;
    if (draftQuote && !mode) {
      mode = noteAskUsesChips(noteAskMode()) ? noteAskMode() : "gpt";
    }
    if (mode) setNoteAskMode(mode);
    else if (!opts.fromDesktopSync && !opts.draft) setNoteAskMode(noteAskMode() || "gpt");

    var useChips = !!(draftQuote && noteAskUsesChips(noteAskMode()));
    if (draftQuote && !useChips) {
      wrap._commentDraft = {
        quote: draftQuote,
        prefix: opts.draft.prefix || "",
        suffix: opts.draft.suffix || "",
      };
      wrap._commentChairId = opts.chairId || null;
    } else if (useChips || opts.draft) {
      wrap._commentDraft = null;
      wrap._commentChairId = null;
    }
    if (opts.scrollToId) wrap._discussionPinId = opts.scrollToId;
    else if (!opts.fromDesktopSync) wrap._discussionPinId = null;
    if (!opts.fromDesktopSync) wrap._discussionDismissed = false;
    syncNoteComposerCommentTarget();
    var ov = document.getElementById("note-discussion-overlay");
    if (ov) {
      ov.classList.remove("hidden");
      ov.setAttribute("aria-hidden", "false");
    }
    renderNotePaieThread(
      (panel && panel._allComments) || [],
      wrap._shareKind,
      wrap._shareId
    );
    if (useChips) insertGptQuoteChip(draftQuote);
    var input = document.getElementById("note-paie-reply-input");
    var editor = noteGptEditor();
    var focusEl = noteAskUsesComposerEditor(noteAskMode()) ? editor : input;
    var shouldFocus =
      opts.focus === true ||
      (!!opts.draft && !isDesktopLayout() && opts.focus !== false);
    if (focusEl && shouldFocus) {
      try {
        focusEl.focus();
      } catch (_) {}
    }
    var pinId = wrap._discussionPinId || opts.scrollToId;
    if (pinId) {
      scrollDiscussionToComment(pinId);
    } else if (!opts.fromDesktopSync) {
      var scroll = document.querySelector("#note-discussion-overlay .note-discussion-scroll");
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    }
    if (!opts.keepSheet && (opts.mode || opts.draft || !opts.fromDesktopSync)) {
      if (noteSheetState.rightMode === "sheet") setRightPaneMode("discussion");
    }
    syncNotePaneHeader();
    syncNoteDiscussionOpenClass();
    syncAppOverlay();
    syncTelegramNativeBack();
    syncNoteFormatToolbarForComposer();
    updateModalSheetForKeyboard();
    syncChatDiscussionChrome();
    renderNoteAskModes();
    startDiscussionLiveUpdates();
    startDiscussPresence();
  }

  var discussionLiveTimer = null;
  var discussionLiveInflight = false;
  var DISCUSSION_LIVE_MS = 2500;
  var discussPresenceTimer = null;

  function discussPresencePath() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    if (wrap._shareKind !== "local" && wrap._shareKind !== "chat") return "";
    return (
      "/notes/" +
      encodeURIComponent(wrap._shareKind) +
      "/" +
      encodeURIComponent(wrap._shareId) +
      "/discuss-presence"
    );
  }

  function tickDiscussPresence() {
    if (!isNoteDiscussionOpen()) {
      leaveDiscussPresence();
      return;
    }
    var path = discussPresencePath();
    if (!path) return;
    apiFetch(path, { method: "POST", body: "{}" }).catch(function () {});
  }

  function startDiscussPresence() {
    if (!isNoteDiscussionOpen() || !discussPresencePath()) return;
    tickDiscussPresence();
    if (discussPresenceTimer) return;
    discussPresenceTimer = setInterval(tickDiscussPresence, 8000);
  }

  function leaveDiscussPresence() {
    if (discussPresenceTimer) {
      clearInterval(discussPresenceTimer);
      discussPresenceTimer = null;
    }
    var path = discussPresencePath();
    if (!path) return;
    apiFetch(path, { method: "DELETE" }).catch(function () {});
  }

  function stopDiscussionLiveUpdates() {
    if (discussionLiveTimer) {
      clearTimeout(discussionLiveTimer);
      discussionLiveTimer = null;
    }
  }

  function startDiscussionLiveUpdates() {
    stopDiscussionLiveUpdates();
    if (!isNoteDiscussionOpen()) return;
    discussionLiveTimer = setTimeout(tickDiscussionLiveUpdates, DISCUSSION_LIVE_MS);
  }

  function tickDiscussionLiveUpdates() {
    discussionLiveTimer = null;
    if (!isNoteDiscussionOpen()) return;
    if (document.hidden) {
      startDiscussionLiveUpdates();
      return;
    }
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return;
    // Не перетирать оптимистичное сообщение и локальный pending во время своей отправки.
    if (document.getElementById("note-discuss-optimistic-user")) {
      startDiscussionLiveUpdates();
      return;
    }
    if (discussionLiveInflight) {
      startDiscussionLiveUpdates();
      return;
    }
    discussionLiveInflight = true;
    refreshNoteCommentsList()
      .catch(function () {})
      .finally(function () {
        discussionLiveInflight = false;
        if (isNoteDiscussionOpen()) startDiscussionLiveUpdates();
      });
  }

  function ensureNotePaieThread() {
    bindNoteDiscussionOverlayOnce();
    var el = document.getElementById("note-paie-thread");
    if (!el) return null;
    bindNotePaieReplyForm(el);
    setNoteAskMode(noteAskMode());
    return el;
  }

  function clipReplyQuote(text) {
    var raw = String(text || "").replace(/\s+/g, " ").trim();
    if (!raw) return "";
    if (raw.length > 160) return raw.slice(0, 159) + "…";
    return raw;
  }

  function chairReplyDraft(groupEl, draft) {
    if (draft && String(draft.quote || "").trim()) {
      return {
        quote: clipReplyQuote(draft.quote),
        prefix: draft.prefix || "",
        suffix: draft.suffix || "",
      };
    }
    var bodyEl =
      groupEl && groupEl.querySelector
        ? groupEl.querySelector(".note-paie-msg.is-chair .note-comment-body")
        : null;
    var quote = clipReplyQuote(bodyEl && bodyEl.textContent);
    if (!quote) quote = "Ответ CHAIR";
    return { quote: quote, prefix: "", suffix: "" };
  }

  function openChairReply(groupEl, draft) {
    if (!groupEl) return;
    var wrap = document.getElementById("note-editor-more-wrap");
    var chairId = parseInt(groupEl.getAttribute("data-chair-id") || "", 10);
    openNoteDiscussion({
      mode: "paie",
      chairId: chairId || null,
      draft: chairReplyDraft(groupEl, draft),
    });
  }

  function ensureNoteComposerReplyTarget(form) {
    if (!form) return null;
    var el = form.querySelector(".note-paie-reply-target");
    if (el) return el;
    var old = form.querySelector(".note-paie-reply-quote");
    el = document.createElement("div");
    el.className = "note-paie-reply-target hidden";
    el.innerHTML =
      '<span class="note-paie-reply-target-bar" aria-hidden="true"></span>' +
      '<div class="note-paie-reply-target-copy">' +
      '<p class="note-paie-reply-target-who">CHAIR</p>' +
      '<p class="note-paie-reply-target-quote"></p>' +
      "</div>" +
      '<button type="button" class="note-paie-reply-target-clear" aria-label="Отменить ответ">&times;</button>';
    var ta = form.querySelector("#note-paie-reply-input");
    if (old && old.parentNode) old.parentNode.replaceChild(el, old);
    else if (ta) form.insertBefore(el, ta);
    else form.insertBefore(el, form.firstChild);
    var clear = el.querySelector(".note-paie-reply-target-clear");
    if (clear && !clear._bound) {
      clear._bound = true;
      clear.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        clearNoteComposerCommentTarget();
        var input = document.getElementById("note-paie-reply-input");
        if (input) {
          input.placeholder = noteAskPlaceholder(noteAskMode());
          try {
            input.focus();
          } catch (_) {}
        }
      });
    }
    return el;
  }

  function syncNoteComposerCommentTarget() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var form = document.getElementById("note-paie-reply-form");
    var targetEl = ensureNoteComposerReplyTarget(form);
    var quoteEl = targetEl && targetEl.querySelector(".note-paie-reply-target-quote");
    var draft = wrap && wrap._commentDraft;
    var chairId = wrap && wrap._commentChairId;
    var whoEl = targetEl && targetEl.querySelector(".note-paie-reply-target-who");
    var quote = draft && String(draft.quote || "").trim();
    var active = !!quote && !noteAskUsesChips(noteAskMode());
    if (whoEl) {
      whoEl.textContent = chairId ? "CHAIR" : "Фрагмент";
    }
    if (form) form.classList.toggle("is-replying", active);
    if (targetEl) targetEl.classList.toggle("hidden", !active);
    if (quoteEl) quoteEl.textContent = clipReplyQuote(quote) || "";
    document.querySelectorAll(".note-paie-group").forEach(function (group) {
      var id = parseInt(group.getAttribute("data-chair-id") || "", 10);
      group.classList.toggle("is-reply-target", active && id === chairId);
    });
    var input = document.getElementById("note-paie-reply-input");
    if (input && active) {
      if (noteAskMode() === "comment") {
        input.placeholder = "Комментарий к ответу CHAIR";
      } else if (noteAskMode() === "paie") {
        input.placeholder = "Уточнение к этому ответу CHAIR";
      }
    } else if (input) {
      input.placeholder = noteAskPlaceholder(noteAskMode());
    }
  }

  function clearNoteComposerCommentTarget() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) {
      wrap._commentChairId = null;
      wrap._commentDraft = null;
    }
    syncNoteComposerCommentTarget();
  }

  function isAssistantComment(c) {
    var api = window.NoteComments;
    if (!c) return false;
    if (api && api.isPaieComment && api.isPaieComment(c)) return true;
    if (api && api.isGptComment && api.isGptComment(c)) return true;
    if (api && api.isResearchComment && api.isResearchComment(c)) return true;
    if (api && api.isAgentTurn && api.isAgentTurn(c)) return true;
    if (c.is_paie || c.is_gpt || c.is_research || c.is_agent) return true;
    return false;
  }

  function isMineDiscussionComment(c) {
    if (!c || isAssistantComment(c)) return false;
    if (c.is_mine === true) return true;
    if (c.is_mine === false) return false;
    var me = currentTelegramUserId();
    var author = String(c.author_user_id || "").trim();
    return !!(me && author && me === author);
  }

  function discussionAuthorLabel(c) {
    if (isAssistantComment(c)) return shareCommentAuthorLabel(c);
    if (isMineDiscussionComment(c)) return "Вы";
    var name = String((c && c.author_name) || "").trim();
    if (name) return name;
    var uname = String((c && c.author_username) || "").trim();
    if (uname) return "@" + uname;
    return "Пользователь";
  }

  function closeNoteAnswerMenu() {
    document.querySelectorAll(".note-discuss-menu").forEach(function (menu) {
      menu.classList.add("hidden");
    });
    document.querySelectorAll(".note-discuss-more").forEach(function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function showNoteToast(text, opts) {
    opts = opts || {};
    var el = document.getElementById("note-toast");
    if (!el) return;
    el.innerHTML = "";
    if (opts.node) {
      el.appendChild(opts.node);
    } else {
      el.textContent = String(text || "");
    }
    el.classList.toggle("note-toast--action", !!opts.actionable);
    el.classList.remove("hidden");
    if (el._timer) window.clearTimeout(el._timer);
    el._timer = window.setTimeout(function () {
      el.classList.add("hidden");
      el.classList.remove("note-toast--action");
    }, opts.duration || (opts.actionable ? 5600 : 2400));
  }

  function noteAgentSessionKey(kind, itemId) {
    return String(kind || "") + ":" + String(itemId || "");
  }

  function discussUnreadStorageKey(kind, itemId) {
    return "leo_discuss_unread:" + String(kind || "") + ":" + String(itemId || "");
  }

  function discussSeenStorageKey(kind, itemId) {
    return "leo_discuss_seen:" + String(kind || "") + ":" + String(itemId || "");
  }

  function noteHasDiscussUnread(kind, itemId) {
    if (!kind || itemId == null || itemId === "") return false;
    try {
      return localStorage.getItem(discussUnreadStorageKey(kind, itemId)) === "1";
    } catch (_) {
      return false;
    }
  }

  function setDiscussUnread(kind, itemId, on) {
    if (!kind || itemId == null || itemId === "") return;
    try {
      var key = discussUnreadStorageKey(kind, itemId);
      if (on) localStorage.setItem(key, "1");
      else localStorage.removeItem(key);
    } catch (_) {}
    updateNoteDiscussionCardUnread();
  }

  function clearDiscussUnread(kind, itemId) {
    if (!noteHasDiscussUnread(kind, itemId)) return;
    setDiscussUnread(kind, itemId, false);
    refreshDiscussUnreadUi();
  }

  function getDiscussSeenId(kind, itemId) {
    try {
      return parseInt(localStorage.getItem(discussSeenStorageKey(kind, itemId)) || "0", 10) || 0;
    } catch (_) {
      return 0;
    }
  }

  function hasDiscussSeenBaseline(kind, itemId) {
    try {
      return localStorage.getItem(discussSeenStorageKey(kind, itemId)) != null;
    } catch (_) {
      return false;
    }
  }

  function markDiscussSeenUpTo(kind, itemId, commentId) {
    if (!kind || itemId == null || itemId === "") return;
    var id = parseInt(commentId, 10) || 0;
    if (!id) return;
    var prev = getDiscussSeenId(kind, itemId);
    if (id > prev) {
      try {
        localStorage.setItem(discussSeenStorageKey(kind, itemId), String(id));
      } catch (_) {}
    } else if (!hasDiscussSeenBaseline(kind, itemId)) {
      try {
        localStorage.setItem(discussSeenStorageKey(kind, itemId), String(id));
      } catch (_) {}
    }
    if (noteHasDiscussUnread(kind, itemId)) {
      setDiscussUnread(kind, itemId, false);
      refreshDiscussUnreadUi();
    } else {
      updateNoteDiscussionCardUnread();
    }
  }

  function maxCommentId(comments) {
    var max = 0;
    (comments || []).forEach(function (c) {
      var id = parseInt(c && c.id, 10) || 0;
      if (id > max) max = id;
    });
    return max;
  }

  function syncDiscussUnreadFromLatest(kind, itemId, latest) {
    if (!kind || itemId == null || itemId === "") return;
    if (!latest || latest.id == null) return;
    var latestId = parseInt(latest.id, 10) || 0;
    if (!latestId) return;
    if (isViewingNoteDiscussion(kind, itemId)) {
      markDiscussSeenUpTo(kind, itemId, latestId);
      return;
    }
    if (!hasDiscussSeenBaseline(kind, itemId)) {
      try {
        localStorage.setItem(discussSeenStorageKey(kind, itemId), String(latestId));
      } catch (_) {}
      return;
    }
    var seen = getDiscussSeenId(kind, itemId);
    if (latestId <= seen) {
      if (noteHasDiscussUnread(kind, itemId)) setDiscussUnread(kind, itemId, false);
      return;
    }
    var me = currentTelegramUserId();
    var mine =
      !!me &&
      String(latest.author_user_id || "") === String(me) &&
      !latest.is_assistant;
    if (mine) {
      try {
        localStorage.setItem(discussSeenStorageKey(kind, itemId), String(latestId));
      } catch (_) {}
      if (noteHasDiscussUnread(kind, itemId)) setDiscussUnread(kind, itemId, false);
      return;
    }
    setDiscussUnread(kind, itemId, true);
  }

  function syncDiscussUnreadFromNoteRow(kind, row) {
    if (!row) return;
    var id = row.id != null ? row.id : localNoteIdFrom(row);
    syncDiscussUnreadFromLatest(kind, id, row.discuss_latest);
  }

  function isViewingNoteDiscussion(kind, itemId) {
    if (!isNoteDiscussionOpen()) return false;
    var wrap = document.getElementById("note-editor-more-wrap");
    return !!(
      wrap &&
      String(wrap._shareKind) === String(kind) &&
      String(wrap._shareId) === String(itemId)
    );
  }

  function noteTitleForDiscussToast(kind, itemId, fallback) {
    var id = String(itemId || "");
    if (kind === "chat") {
      for (var c = 0; c < (chatThreadsCache || []).length; c++) {
        if (String(chatThreadsCache[c].id) === id) {
          return String(chatThreadsCache[c].title || "чате");
        }
      }
      return fallback || "чате";
    }
    function fromList(list) {
      for (var i = 0; i < (list || []).length; i++) {
        if (String(list[i].id) === id) {
          return (
            sanitizeNoteTitle(list[i].title || list[i].content || list[i].main_topic || "") ||
            String(list[i].title || list[i].content || list[i].main_topic || "")
          );
        }
      }
      return "";
    }
    var title = "";
    if (kind === "digest") {
      try {
        title = getNoteEditorTitle().trim();
      } catch (_) {}
      return title || fallback || "дайджесте";
    }
    if (kind === "journal") {
      title =
        fromList(notesDataCache && notesDataCache.transcriptions) ||
        fromList(notesDataCache && notesDataCache.summaries) ||
        fromList(notesDataCache && notesDataCache.journal);
    } else {
      title =
        fromList(notesDataCache && notesDataCache.local_notes) ||
        fromList(knowledgeDataCache && knowledgeDataCache.notes);
    }
    if (!title) {
      try {
        title = getNoteEditorTitle().trim();
      } catch (_) {}
    }
    return title || fallback || "заметке";
  }

  function beginNoteAgentSession(kind, itemId, mode, title) {
    var key = noteAgentSessionKey(kind, itemId);
    noteAgentSessions[key] = {
      kind: String(kind || ""),
      itemId: String(itemId || ""),
      mode: mode || "gpt",
      title: String(title || ""),
      running: true,
    };
  }

  function endNoteAgentSession(kind, itemId) {
    delete noteAgentSessions[noteAgentSessionKey(kind, itemId)];
  }

  function parseNoteAgentPath(path) {
    var m = String(path || "").match(/^\/notes\/([^/]+)\/([^/]+)\/(paei|research)(?:\?|$)/);
    if (!m) return null;
    return {
      kind: decodeURIComponent(m[1]),
      itemId: decodeURIComponent(m[2]),
      mode: m[3] === "paei" ? "paie" : "research",
    };
  }

  function refreshDiscussUnreadUi() {
    if (notesDataCache) renderNotesPanesFromData(notesDataCache);
    if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
  }

  function mountDiscussUnreadBadge(parent, kind, itemId, solo) {
    if (!parent || !noteHasDiscussUnread(kind, itemId)) return;
    var dot = document.createElement("span");
    dot.className =
      "note-card-discuss-unread" + (solo ? " note-card-discuss-unread--solo" : "");
    dot.setAttribute("aria-label", "Новый ответ в обсуждении");
    dot.title = "Новый ответ в обсуждении";
    parent.appendChild(dot);
  }

  function openNoteDiscussionFromNotify(kind, itemId, mode) {
    clearDiscussUnread(kind, itemId);
    pendingDiscussOpen = {
      kind: String(kind || ""),
      itemId: String(itemId || ""),
      mode: mode || "gpt",
    };
    if (String(kind) === "journal") {
      openJournalEditorDetail({ id: itemId });
      return;
    }
    var kbNote = null;
    if (knowledgeDataCache && knowledgeDataCache.notes) {
      kbNote = knowledgeDataCache.notes.find(function (n) {
        return String(n.id) === String(itemId);
      });
    }
    if (kbNote) {
      openKnowledgeNoteDetail(kbNote);
      return;
    }
    openNoteDetail({ id: itemId }, { isLocal: true });
  }

  function consumePendingDiscussOpen(kind, itemId) {
    if (!pendingDiscussOpen) return;
    if (
      String(pendingDiscussOpen.kind) !== String(kind) ||
      String(pendingDiscussOpen.itemId) !== String(itemId)
    ) {
      return;
    }
    var mode = pendingDiscussOpen.mode || "gpt";
    pendingDiscussOpen = null;
    window.setTimeout(function () {
      openNoteDiscussion({ mode: mode, focus: true });
    }, 120);
  }

  function showDiscussReplyToast(kind, itemId, mode, title) {
    var node = document.createElement("span");
    node.className = "note-toast-copy";
    node.appendChild(document.createTextNode("Новый ответ в "));
    var link = document.createElement("button");
    link.type = "button";
    link.className = "note-toast-link";
    link.textContent = "обсуждении";
    if (title) link.title = String(title);
    link.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var el = document.getElementById("note-toast");
      if (el) {
        el.classList.add("hidden");
        if (el._timer) window.clearTimeout(el._timer);
      }
      openNoteDiscussionFromNotify(kind, itemId, mode);
    });
    node.appendChild(link);
    showNoteToast("", { node: node, actionable: true, duration: 6500 });
  }

  function notifyAgentReplyArrived(kind, itemId, mode, title) {
    endNoteAgentSession(kind, itemId);
    shareHaptic();
    if (isViewingNoteDiscussion(kind, itemId)) {
      refreshNoteCommentsList();
      return;
    }
    var label = title || noteTitleForDiscussToast(kind, itemId, "");
    setDiscussUnread(kind, itemId, true);
    refreshDiscussUnreadUi();
    showDiscussReplyToast(kind, itemId, mode || "gpt", label);
  }

  function titleFromAnswerMarkdown(md) {
    var lines = String(md || "").split("\n");
    for (var i = 0; i < lines.length; i++) {
      var t = lines[i].trim();
      if (!t) continue;
      if (parseMarkdownTableRow(t)) continue;
      if (/^```/.test(t)) continue;
      var hm = t.match(/^#{1,6}\s+(.*)$/);
      if (hm) t = hm[1].trim();
      t = t
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/\*([^*]+)\*/g, "$1")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/^[-*•]\s+/, "")
        .replace(/^\d+[.)]\s+/, "")
        .trim();
      if (!t) continue;
      if (t.length > 80) t = t.slice(0, 79) + "…";
      return t;
    }
    return "Заметка";
  }

  function titleFromAnswerHtml(html) {
    var tmp = document.createElement("div");
    tmp.innerHTML = String(html || "");
    var text = String(tmp.textContent || "")
      .replace(/\s+/g, " ")
      .trim();
    if (!text) return "Заметка";
    if (text.length > 80) text = text.slice(0, 79) + "…";
    return text;
  }

  /**
   * HTML of the current selection inside a rendered GPT bubble.
   * Preserves block structure (lists, headings, paragraphs) that plain quote collapse loses.
   */
  function htmlFromDiscussionSelectionRange(range) {
    if (!range || range.collapsed) return "";
    var div = document.createElement("div");
    try {
      div.appendChild(range.cloneContents());
    } catch (_) {
      return "";
    }
    // Drop discussion-only wrappers; keep table/scroll content.
    div.querySelectorAll(".note-table-scroll").forEach(function (wrap) {
      var parent = wrap.parentNode;
      if (!parent) return;
      while (wrap.firstChild) parent.insertBefore(wrap.firstChild, wrap);
      parent.removeChild(wrap);
    });
    var raw = String(div.innerHTML || "").trim();
    if (!raw) return "";
    var clean = sanitizeSummaryHtml(raw);
    if (!clean || !String(clean).trim()) return "";
    if (!/<(p|h[1-6]|ul|ol|li|blockquote|table|pre|div)\b/i.test(clean)) {
      clean = "<p>" + clean + "</p>";
    }
    return clean;
  }

  function htmlFromAnswerMarkdown(md) {
    var raw = String(md || "").trim();
    if (!raw) return "";
    var html = simpleMarkdownToHtml(raw);
    return html || "<p>" + escapeHtml(raw) + "</p>";
  }

  function appendAnswerHtmlToCurrentNote(html) {
    var clean = String(html || "").trim();
    if (!clean) return false;
    var inst = getActiveNoteRichEditor();
    if (inst && typeof inst.appendHtml === "function") {
      inst.appendHtml(clean);
      showNoteToast("Добавлено в заметку");
      shareHaptic();
      return true;
    }
    return false;
  }

  function appendAnswerToCurrentNote(text) {
    var raw = String(text || "").trim();
    if (!raw) return;
    var html = htmlFromAnswerMarkdown(raw);
    if (appendAnswerHtmlToCurrentNote(html)) return;
    var inst = getActiveNoteRichEditor();
    if (inst && typeof inst.appendText === "function") {
      inst.appendText(raw);
      showNoteToast("Добавлено в заметку");
      shareHaptic();
      return;
    }
    showNoteToast("Не удалось вставить в заметку");
  }

  async function createNoteFromAnswerHtml(html) {
    var clean = String(html || "").trim();
    if (!clean) return;
    var title = titleFromAnswerHtml(clean);
    var body = { title: title, description: clean, sync_todoist: false };
    try {
      if (isAppOffline()) {
        var tmpId = newTempId("tmp_note_");
        prependLocalInCache({ id: tmpId, title: title, description: clean, body: clean });
        if (notesDataCache) renderNotesPanesFromData(notesDataCache);
        enqueueOfflineOp({
          type: "note_create",
          clientId: tmpId,
          path: "/notes/local",
          method: "POST",
          body: body,
        });
        showOfflineSavedToast("Заметка создана офлайн");
        shareHaptic();
        return;
      }
      var createRes = await apiFetch("/notes/local", {
        method: "POST",
        body: JSON.stringify(body),
      });
      var created = (createRes && createRes.item) || { id: createRes && createRes.id, title: title };
      prependLocalInCache(created);
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      showNoteToast("Заметка создана");
      shareHaptic();
    } catch (e) {
      if (isTransientApiError(e)) {
        var tmpId2 = newTempId("tmp_note_");
        prependLocalInCache({ id: tmpId2, title: title, description: clean, body: clean });
        if (notesDataCache) renderNotesPanesFromData(notesDataCache);
        enqueueOfflineOp({
          type: "note_create",
          clientId: tmpId2,
          path: "/notes/local",
          method: "POST",
          body: body,
        });
        showOfflineSavedToast("Заметка создана офлайн");
        shareHaptic();
        return;
      }
      alert(e.message || String(e));
    }
  }

  async function createNoteFromAnswer(text) {
    var raw = String(text || "").trim();
    if (!raw) return;
    var title = titleFromAnswerMarkdown(raw);
    var html = htmlFromAnswerMarkdown(raw);
    await createNoteFromAnswerHtml(html || ("<p>" + escapeHtml(raw) + "</p>"));
  }

  function closeNoteLinkPicker() {
    var ov = document.getElementById("note-link-overlay");
    if (ov) {
      ov.classList.add("hidden");
      ov.setAttribute("aria-hidden", "true");
    }
  }

  function bindNoteLinkPickerOnce() {
    var ov = document.getElementById("note-link-overlay");
    if (!ov || ov._bound) return ov;
    ov._bound = true;
    var backdrop = document.getElementById("note-link-backdrop");
    var cancel = document.getElementById("note-link-cancel");
    var search = document.getElementById("note-link-search");
    if (backdrop) backdrop.addEventListener("click", closeNoteLinkPicker);
    if (cancel) cancel.addEventListener("click", closeNoteLinkPicker);
    if (search) {
      search.addEventListener("input", function () {
        renderNoteLinkList(search.value, ov._answerText || "");
      });
    }
    return ov;
  }

  function noteLinkPreview(item) {
    return notePlainExcerpt(item.description || item.body || "", 80);
  }

  function renderNoteLinkList(query, answerText) {
    var list = document.getElementById("note-link-list");
    if (!list) return;
    var chips = document.getElementById("note-link-project-chips");
    if (chips) {
      renderNotePickerProjectChips(
        chips,
        function () {
          return linkPickerActiveProjectId;
        },
        function (v) {
          linkPickerActiveProjectId = v;
        },
        function () {
          renderNoteLinkList(query, answerText);
        }
      );
    }
    var wrap = document.getElementById("note-editor-more-wrap");
    var currentId = wrap && wrap._shareKind === "local" ? String(wrap._shareId || "") : "";
    var q = String(query || "").trim().toLowerCase();
    var notes = ((notesDataCache && notesDataCache.local_notes) || []).filter(function (n) {
      var id = String((n && n.id) || "");
      if (!id || id === currentId) return false;
      if (!noteMatchesPickerProjectFilter(n, linkPickerActiveProjectId)) return false;
      if (!q) return true;
      var title = String(n.title || n.content || "").toLowerCase();
      var preview = noteLinkPreview(n).toLowerCase();
      return title.indexOf(q) >= 0 || preview.indexOf(q) >= 0;
    });
    list.innerHTML = "";
    if (!notes.length) {
      var empty = document.createElement("p");
      empty.className = "note-link-empty";
      empty.textContent =
        q || linkPickerActiveProjectId ? "Ничего не найдено" : "Других заметок нет";
      list.appendChild(empty);
      return;
    }
    notes.forEach(function (n) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-link-item";
      var title = document.createElement("p");
      title.className = "note-link-item-title";
      title.textContent = String(n.title || n.content || "Без названия");
      var preview = document.createElement("p");
      preview.className = "note-link-item-preview";
      preview.textContent = noteLinkPreview(n) || " ";
      btn.appendChild(title);
      btn.appendChild(preview);
      btn.addEventListener("click", function () {
        linkAnswerToNote(n.id, answerText);
      });
      list.appendChild(btn);
    });
  }

  async function linkAnswerToNote(noteId, text) {
    var raw = String(text || "").trim();
    var id = localNoteIdFrom({ id: noteId });
    if (!raw || !id) return;
    try {
      await apiFetch("/notes/local/" + encodeURIComponent(String(id)) + "/comments", {
        method: "POST",
        body: JSON.stringify({
          body: raw,
          quote: "",
          prefix: "",
          suffix: "",
          as_role: "gpt",
        }),
      });
      closeNoteLinkPicker();
      showNoteToast("Ответ добавлен в комментарии заметки");
      shareHaptic();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function openNoteLinkPicker(answerText) {
    bindNoteLinkPickerOnce();
    var ov = document.getElementById("note-link-overlay");
    if (!ov) return;
    ov._answerText = String(answerText || "");
    linkPickerActiveProjectId = 0;
    var search = document.getElementById("note-link-search");
    if (search) search.value = "";
    function show() {
      ensureUserTagsLoaded().finally(function () {
        renderNoteLinkList("", ov._answerText);
      });
      ov.classList.remove("hidden");
      ov.setAttribute("aria-hidden", "false");
      if (search) {
        try {
          search.focus();
        } catch (_) {}
      }
    }
    if (notesDataCache && notesDataCache.local_notes) {
      show();
      return;
    }
    apiFetch("/notes?limit=120", { method: "GET" })
      .then(function (data) {
        notesDataCache = data || notesDataCache;
        if (data) writeMiniappCache("notes", data);
        show();
      })
      .catch(function () {
        show();
      });
  }

  function copyAnswerText(text) {
    copyShareUrl(String(text || "")).then(
      function () {
        showNoteToast("Скопировано");
        shareHaptic();
      },
      function () {
        showNoteToast("Не удалось скопировать");
      }
    );
  }

  function discussMenuIcon(name) {
    var common =
      'width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    var paths = {
      note:
        '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M15 3v6h6"/>',
      task: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
      link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
      copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
      trash:
        '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
      clarify:
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 9h8M8 13h5"/>',
      "into-note":
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M12 11v7M9 15l3 3 3-3"/>',
      "create-note":
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M12 11v6M9 14h6"/>',
    };
    return (
      '<span class="note-discuss-menu-icon"><svg ' +
      common +
      ">" +
      (paths[name] || "") +
      "</svg></span>"
    );
  }

  function discussMenuItem(action, icon, label, extraClass) {
    return (
      '<button type="button" class="note-discuss-menu-item' +
      (extraClass ? " " + extraClass : "") +
      '" data-answer-action="' +
      action +
      '">' +
      discussMenuIcon(icon) +
      "<span>" +
      label +
      "</span></button>"
    );
  }

  function bindNoteAnswerMenu(btn, menu, comment) {
    if (!btn || !menu) return;
    var text = String((comment && comment.body) || "");
    var commentId = comment && comment.id;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var open = menu.classList.contains("hidden");
      closeNoteAnswerMenu();
      if (open) {
        menu.classList.remove("hidden");
        btn.setAttribute("aria-expanded", "true");
      }
    });
    menu.querySelectorAll("[data-answer-action]").forEach(function (item) {
      item.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var action = item.getAttribute("data-answer-action");
        closeNoteAnswerMenu();
        if (action === "into-note") appendAnswerToCurrentNote(text);
        else if (action === "create-note") createNoteFromAnswer(text);
        else if (action === "link") openNoteLinkPicker(text);
        else if (action === "copy") copyAnswerText(text);
        else if (action === "delete") {
          var wrap = document.getElementById("note-editor-more-wrap");
          if (wrap && wrap._shareKind && wrap._shareId && commentId) {
            deleteNoteComment(wrap._shareKind, wrap._shareId, commentId);
          }
        }
      });
    });
  }

  function copyClientRect(r) {
    if (!r) return null;
    return {
      top: r.top,
      left: r.left,
      width: r.width,
      height: r.height,
      bottom: r.bottom,
      right: r.right,
    };
  }

  function snapshotDiscussSelectionRects() {
    var sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount < 1) return [];
    var range = sel.getRangeAt(0);
    var out = [];
    var rects = range.getClientRects ? range.getClientRects() : [];
    for (var i = 0; i < rects.length; i++) {
      var copied = copyClientRect(rects[i]);
      if (copied && copied.width >= 1 && copied.height >= 1) out.push(copied);
    }
    return out;
  }

  function hideDiscussSelMarks() {
    var el = document.getElementById("note-discuss-sel-marks");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function showDiscussSelMarks(rects) {
    hideDiscussSelMarks();
    if (!rects || !rects.length) return;
    var wrap = document.createElement("div");
    wrap.id = "note-discuss-sel-marks";
    wrap.className = "note-discuss-sel-marks";
    wrap.setAttribute("aria-hidden", "true");
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      var mark = document.createElement("div");
      mark.className = "note-discuss-sel-mark";
      mark.style.top = Math.round(r.top) + "px";
      mark.style.left = Math.round(r.left) + "px";
      mark.style.width = Math.round(r.width) + "px";
      mark.style.height = Math.round(r.height) + "px";
      wrap.appendChild(mark);
    }
    if (!wrap.firstChild) return;
    document.body.appendChild(wrap);
  }

  function hideDiscussClarify() {
    hideDiscussSelMarks();
    var el = document.getElementById("note-discuss-selection");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function isDiscussSelectionUiTarget(el) {
    return !!(el && el.closest && el.closest("#note-discuss-selection, #note-discuss-sel-marks"));
  }

  function discussSelectableBubble(el) {
    if (!el || !el.closest) return null;
    var msg = el.closest(".note-discuss-msg.is-assistant");
    if (!msg || msg.classList.contains("is-pending")) return null;
    var bubble = el.closest(".note-discuss-bubble");
    if (!bubble || !msg.contains(bubble)) return null;
    return { msg: msg, bubble: bubble };
  }

  function isDiscussAssistantBubbleTarget(el) {
    return !!discussSelectableBubble(el);
  }

  function collapseNativeDiscussionSelection() {
    var sel = window.getSelection && window.getSelection();
    if (sel && sel.removeAllRanges) sel.removeAllRanges();
  }

  function discussSelectionBtn(action, icon, label) {
    return (
      '<button type="button" class="note-discuss-selection-btn" data-selection-action="' +
      action +
      '" aria-label="' +
      label +
      '" title="' +
      label +
      '">' +
      discussMenuIcon(icon) +
      "</button>"
    );
  }

  function clearDiscussionSelection() {
    var sel = window.getSelection && window.getSelection();
    if (sel && sel.removeAllRanges) sel.removeAllRanges();
    hideDiscussClarify();
  }

  function runDiscussSelectionAction(action, draft) {
    if (!draft || !String(draft.quote || "").trim()) {
      hideDiscussClarify();
      return;
    }
    if (action === "clarify") {
      applyDiscussClarify(draft);
      return;
    }
    var quote = String(draft.quote || "").trim();
    var html = String(draft.html || "").trim();
    if (action === "copy") copyAnswerText(quote);
    else if (action === "into-note") {
      if (html) {
        if (!appendAnswerHtmlToCurrentNote(html)) appendAnswerToCurrentNote(quote);
      } else {
        appendAnswerToCurrentNote(quote);
      }
    } else if (action === "create-note") {
      if (html) createNoteFromAnswerHtml(html);
      else createNoteFromAnswer(quote);
    } else if (action === "task") {
      openTaskModal({ summary: quote, note_id: currentOpenNoteId() });
    }
    clearDiscussionSelection();
  }

  function showDiscussSelectionToolbar(draft) {
    var rect = draft && draft.rect;
    if (!rect) {
      hideDiscussClarify();
      return;
    }
    var bar = document.getElementById("note-discuss-selection");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "note-discuss-selection";
      bar.className = "note-discuss-selection";
      bar.innerHTML =
        discussSelectionBtn("clarify", "clarify", "Уточнить") +
        discussSelectionBtn("task", "task", "Задача") +
        discussSelectionBtn("copy", "copy", "Копировать") +
        discussSelectionBtn("into-note", "into-note", "В заметку") +
        discussSelectionBtn("create-note", "create-note", "Создать заметку");
      function stopSel(e) {
        e.preventDefault();
        e.stopPropagation();
      }
      function activate(e) {
        e.preventDefault();
        e.stopPropagation();
        if (bar._fired) return;
        var btn = e.target && e.target.closest && e.target.closest("[data-selection-action]");
        if (!btn || !bar.contains(btn)) return;
        bar._fired = true;
        runDiscussSelectionAction(btn.getAttribute("data-selection-action"), bar._draft);
      }
      bar.addEventListener("pointerdown", stopSel);
      bar.addEventListener("mousedown", stopSel);
      bar.addEventListener("touchstart", stopSel, { passive: false });
      bar.addEventListener("pointerup", activate);
      bar.addEventListener("click", activate);
      document.body.appendChild(bar);
    }
    bar._draft = draft;
    bar._fired = false;
    var vw = window.innerWidth;
    var width = bar.offsetWidth || 128;
    var height = bar.offsetHeight || 32;
    var left = rect.left + rect.width / 2 - width / 2;
    var top = rect.top - height - 8;
    if (left < 8) left = 8;
    if (left + width > vw - 8) left = vw - width - 8;
    if (top < 8) top = rect.bottom + 8;
    bar.style.top = Math.round(top) + "px";
    bar.style.left = Math.round(left) + "px";
  }

  function applyDiscussClarify(draft) {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !draft || !String(draft.quote || "").trim()) return;
    if (draft.source === "paie") {
      wrap._commentDraft = {
        quote: String(draft.quote || "").trim(),
        prefix: draft.prefix || "",
        suffix: draft.suffix || "",
      };
      wrap._commentChairId = draft.commentId || null;
      setNoteAskMode("paie");
      syncNoteComposerCommentTarget();
      var input = document.getElementById("note-paie-reply-input");
      if (input) {
        try {
          input.focus();
        } catch (_) {}
      }
      clearDiscussionSelection();
      return;
    }
    wrap._commentDraft = null;
    wrap._commentChairId = null;
    setNoteAskMode(draft.source === "research" ? "research" : "gpt");
    syncNoteComposerCommentTarget();
    insertGptQuoteChip(draft.quote);
    var ed = noteGptEditor();
    if (ed) {
      try {
        ed.focus();
      } catch (_) {}
    }
    clearDiscussionSelection();
  }

  function discussionSelectionDraft() {
    var sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount < 1) return null;
    var node = sel.anchorNode;
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    var found = discussSelectableBubble(el);
    if (!found) return null;
    var bubble = found.bubble;
    var msg = found.msg;
    if (sel.focusNode && !bubble.contains(sel.focusNode)) return null;
    var range = sel.getRangeAt(0);
    var html = htmlFromDiscussionSelectionRange(range);
    var api = window.NoteComments;
    var draft = api && api.selectionAnchor ? api.selectionAnchor(bubble) : null;
    var source = "paie";
    if (msg.classList.contains("is-research")) source = "research";
    else if (msg.classList.contains("is-gpt")) source = "gpt";
    var commentId = parseInt(msg.getAttribute("data-comment-id") || "", 10) || 0;
    if (draft && draft.quote) {
      draft.rect = copyClientRect(draft.rect) || draft.rect;
      draft.html = html;
      draft.source = source;
      draft.commentId = commentId;
      return draft;
    }
    var text = String(sel.toString() || "").replace(/\s+/g, " ").trim();
    if (!text) return null;
    var rect = copyClientRect(range.getBoundingClientRect ? range.getBoundingClientRect() : null);
    return {
      quote: text,
      prefix: "",
      suffix: "",
      rect: rect,
      html: html,
      source: source,
      commentId: commentId,
    };
  }

  function bindDiscussClarifyOnce() {
    if (document._discussClarifyBound) return;
    document._discussClarifyBound = true;
    function onContextMenu(e) {
      if (!isNoteDiscussionOpen()) return;
      var t = e.target;
      if (isDiscussSelectionUiTarget(t) || isDiscussAssistantBubbleTarget(t)) {
        e.preventDefault();
      }
    }
    function onSelect(e) {
      if (isDiscussSelectionUiTarget(e && e.target)) return;
      if (!isNoteDiscussionOpen()) {
        hideDiscussClarify();
        return;
      }
      var draft = discussionSelectionDraft();
      if (!draft || !draft.rect) return;
      var rects = snapshotDiscussSelectionRects();
      showDiscussSelectionToolbar(draft);
      showDiscussSelMarks(rects);
      collapseNativeDiscussionSelection();
    }
    function onDismiss(e) {
      if (!document.getElementById("note-discuss-selection")) return;
      if (isDiscussSelectionUiTarget(e && e.target)) return;
      hideDiscussClarify();
    }
    document.addEventListener("contextmenu", onContextMenu, true);
    document.addEventListener("mouseup", onSelect);
    document.addEventListener("pointerup", onSelect);
    document.addEventListener("keyup", onSelect);
    document.addEventListener("scroll", hideDiscussClarify, true);
    document.addEventListener("pointerdown", onDismiss, true);
  }

  function renderDiscussionMessage(c) {
    var item = document.createElement("article");
    var assistant = isAssistantComment(c);
    var mine = !assistant && isMineDiscussionComment(c);
    var api = window.NoteComments;
    var isGpt = !!(api && api.isGptComment && api.isGptComment(c));
    var isResearch = !!(api && api.isResearchComment && api.isResearchComment(c));
    item.className =
      "note-discuss-msg " +
      (assistant ? "is-assistant" : mine ? "is-user" : "is-other") +
      (isGpt ? " is-gpt" : "") +
      (isResearch ? " is-research" : "");
    item.setAttribute("data-comment-id", String(c.id));
    var quoteText = String((c && c.quote) || "").trim();
    var bodyHasQuote = String((c && c.body) || "").indexOf("«") >= 0;
    if (quoteText && !bodyHasQuote) {
      var q = document.createElement("p");
      q.className = "note-discuss-quote";
      q.textContent = "«" + quoteText + "»";
      item.appendChild(q);
    }
    var role = document.createElement("p");
    role.className = "note-discuss-role";
    role.textContent = discussionAuthorLabel(c);
    item.appendChild(role);
    var bodyText = String((c && c.body) || "");
    var attachments = (c && c.attachments) || [];
    var hideClipBody = bodyText.trim() === "📎" && attachments.length;
    var bubble = document.createElement("div");
    bubble.className = "note-discuss-bubble";
    if (assistant && bodyText.trim() && !hideClipBody) {
      bubble.className = "note-discuss-bubble note-discuss-bubble--md journal-summary-html";
      bubble.innerHTML = wrapTablesForScroll(simpleMarkdownToHtml(bodyText));
    } else if (!hideClipBody && bodyText.trim()) {
      fillDiscussBodyWithMentions(bubble, bodyText);
    }
    renderDiscussAttachments(bubble, attachments);
    if (bubble.childNodes.length) item.appendChild(bubble);
    if (String((c && c.body) || "").trim() || attachments.length) {
      var actions = document.createElement("div");
      actions.className = "note-discuss-actions";
      var more = document.createElement("button");
      more.type = "button";
      more.className = "note-discuss-more";
      more.setAttribute("aria-label", "Действия");
      more.setAttribute("aria-expanded", "false");
      more.textContent = "···";
      var menu = document.createElement("div");
      menu.className = "note-discuss-menu hidden";
      menu.innerHTML =
        discussMenuItem("into-note", "note", "В заметку") +
        discussMenuItem("create-note", "note", "Создать заметку") +
        discussMenuItem("link", "link", "Связать с заметкой") +
        discussMenuItem("copy", "copy", "Копировать") +
        discussMenuItem("delete", "trash", "Удалить", "note-discuss-menu-item--danger");
      actions.appendChild(more);
      actions.appendChild(menu);
      bindNoteAnswerMenu(more, menu, c);
      item.appendChild(actions);
    }
    item.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".note-discuss-more, .note-discuss-menu, #note-discuss-selection")) {
        return;
      }
      var sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed) return;
      revealNoteDiscussionTopic(c, { fromDiscussion: true });
    });
    return item;
  }

  function renderNotePaieThread(comments, kind, itemId) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var running = !!(wrap && wrap._paeiRunning);
    ensureNotePaieThread();
    var list = document.getElementById("note-discussion-messages");
    var form = document.getElementById("note-paie-reply-form");
    var status = document.getElementById("note-editor-paei-status");
    var statusOn = !!(status && !status.classList.contains("hidden"));
    var rows = discussionComments(comments).slice().sort(function (a, b) {
      var at = Date.parse(a && a.created_at) || 0;
      var bt = Date.parse(b && b.created_at) || 0;
      if (at !== bt) return at - bt;
      return Number(a.id || 0) - Number(b.id || 0);
    });
    var gptBusy = !!(wrap && wrap._gptBusy);
    var researchRunning = !!(wrap && wrap._researchRunning);
    var fp =
      rows
        .map(function (c) {
          return String(c && c.id) + ":" + String((c && c.body) || "").length;
        })
        .join("|") +
      "|g:" +
      (gptBusy ? String(wrap._gptPhase || "1") : "0") +
      "|p:" +
      (running ? "1" : "0") +
      "|r:" +
      (researchRunning ? "1" : "0") +
      "|s:" +
      (statusOn ? "1" : "0");
    if (list && list._renderFp === fp && list.childNodes.length) {
      if (form) {
        form.classList.remove("hidden");
        syncNotePaieReplyForm(running);
        syncNoteComposerCommentTarget();
      }
      void kind;
      void itemId;
      return;
    }
    var scroll = document.querySelector("#note-discussion-overlay .note-discussion-scroll");
    var stickBottom = true;
    var prevTop = 0;
    if (scroll) {
      prevTop = scroll.scrollTop;
      stickBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 96;
    }
    if (list) {
      list._renderFp = fp;
      list.innerHTML = "";
      if (
        !rows.length &&
        !statusOn &&
        !running &&
        !gptBusy &&
        !researchRunning
      ) {
        var empty = document.createElement("p");
        empty.className = "note-discussion-empty";
        empty.textContent = "Задайте вопрос или оставьте комментарий";
        list.appendChild(empty);
      } else {
        var cut = gptContextCutId();
        rows.forEach(function (c, idx) {
          var msg = renderDiscussionMessage(c);
          if (cut && Number(c.id) <= cut) msg.classList.add("is-ctx-stale");
          list.appendChild(msg);
          var next = rows[idx + 1];
          if (cut && Number(c.id) <= cut && (!next || Number(next.id) > cut)) {
            list.appendChild(renderGptContextDivider());
          }
        });
        if (gptBusy) {
          list.appendChild(renderGptPendingStatus(wrap._gptPhase));
        }
      }
    }
    if (form) {
      form.classList.remove("hidden");
      syncNotePaieReplyForm(running);
      syncNoteComposerCommentTarget();
    }
    renderNoteDiscussionCard(comments);
    refreshNoteEditorTocExtras();
    if (isNoteDiscussionOpen()) {
      var pinId = wrap && wrap._discussionPinId;
      if (pinId && !gptBusy) scrollDiscussionToComment(pinId);
      else if (stickBottom) scrollDiscussionToEnd();
      else if (scroll) scroll.scrollTop = prevTop;
    }
    void kind;
    void itemId;
  }

  function noteHasDiscussion(comments) {
    var api = window.NoteComments;
    if (api && api.hasDiscussion) return api.hasDiscussion(comments || []);
    return false;
  }

  function appendNoteDiscussionToc(tocEl, onNavigate) {
    if (!tocEl) return;
    var panel = document.getElementById("note-editor-comments");
    var comments = (panel && panel._allComments) || [];
    if (!noteHasDiscussion(comments)) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "note-editor-toc-item note-editor-toc-item--level-1";
    btn.setAttribute("data-toc-discussion", "1");
    btn.textContent = "Обсуждение";
    btn.addEventListener("click", function () {
      openNoteDiscussion();
      if (typeof onNavigate === "function") onNavigate();
    });
    tocEl.appendChild(btn);
  }

  function refreshNoteEditorTocExtras() {
    var tocEl = document.querySelector("#note-editor-overlay .note-editor-toc-list");
    if (tocEl && typeof tocEl._refreshToc === "function") tocEl._refreshToc();
  }

  function noteKnowledgeStorageKey() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    return "leo_note_kb:" + wrap._shareKind + ":" + wrap._shareId;
  }

  function noteKnowledgeIdsStorageKey() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    return "leo_note_kb_ids:" + wrap._shareKind + ":" + wrap._shareId;
  }

  function knowledgeNotesList() {
    return (knowledgeDataCache && knowledgeDataCache.notes) || [];
  }

  function allKnowledgeNoteIds() {
    return knowledgeNotesList()
      .map(function (n) {
        return n && n.id != null ? String(n.id) : "";
      })
      .filter(Boolean);
  }

  function readStoredKnowledgeNoteIds() {
    var idsKey = noteKnowledgeIdsStorageKey();
    if (idsKey) {
      try {
        var raw = localStorage.getItem(idsKey);
        if (raw != null && raw !== "") {
          var parsed = JSON.parse(raw);
          if (Array.isArray(parsed)) {
            return {
              explicit: true,
              ids: parsed
                .map(function (id) {
                  return String(id || "").trim();
                })
                .filter(Boolean),
            };
          }
        }
      } catch (_) {}
    }
    var oldKey = noteKnowledgeStorageKey();
    if (oldKey) {
      try {
        var legacy = localStorage.getItem(oldKey);
        if (legacy === "0") return { explicit: true, ids: [] };
        if (legacy === "1") return { explicit: false, ids: [] };
      } catch (_) {}
    }
    return { explicit: false, ids: [] };
  }

  function noteKnowledgeNoteIds() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && Array.isArray(wrap._knowledgeNoteIds)) return wrap._knowledgeNoteIds.slice();
    var stored = readStoredKnowledgeNoteIds();
    if (stored.explicit) return stored.ids.slice();
    // По умолчанию БЗ выключена, пока пользователь сам ничего не выбрал.
    return [];
  }

  function setNoteKnowledgeNoteIds(ids) {
    var next = (ids || [])
      .map(function (id) {
        return String(id || "").trim();
      })
      .filter(Boolean);
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) wrap._knowledgeNoteIds = next;
    var idsKey = noteKnowledgeIdsStorageKey();
    if (idsKey) {
      try {
        localStorage.setItem(idsKey, JSON.stringify(next));
      } catch (_) {}
    }
    var oldKey = noteKnowledgeStorageKey();
    if (oldKey) {
      try {
        localStorage.setItem(oldKey, next.length ? "1" : "0");
      } catch (_) {}
    }
    syncNoteKnowledgeToggle();
    renderNoteKnowledgeMenu();
  }

  function toggleNoteKnowledgeNoteId(id) {
    id = String(id || "").trim();
    if (!id) return;
    var current = noteKnowledgeNoteIds();
    var next =
      current.indexOf(id) >= 0
        ? current.filter(function (x) {
            return x !== id;
          })
        : current.concat([id]);
    setNoteKnowledgeNoteIds(next);
  }

  function noteUsesKnowledge() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && Array.isArray(wrap._knowledgeNoteIds)) return wrap._knowledgeNoteIds.length > 0;
    var stored = readStoredKnowledgeNoteIds();
    if (stored.explicit) return stored.ids.length > 0;
    return false;
  }

  function discussionKnowledgeFields() {
    var ids = noteKnowledgeNoteIds();
    var ctx = noteAskMode() === "comment" ? [] : discussContextNoteIds();
    return {
      use_knowledge: ids.length > 0,
      knowledge_note_ids: ids,
      sheet_ids: selectedContextSheetIds(),
      context_note_ids: ctx.length ? ctx : undefined,
    };
  }

  function ensureDiscussionKnowledgeNotes() {
    if (knowledgeDataCache && Array.isArray(knowledgeDataCache.notes)) {
      return Promise.resolve(knowledgeDataCache.notes);
    }
    return Promise.resolve(loadKnowledgeNotes())
      .then(function () {
        return (knowledgeDataCache && knowledgeDataCache.notes) || [];
      })
      .catch(function () {
        return [];
      });
  }

  function knowledgeNoteMenuTitle(n) {
    return (
      sanitizeNoteTitle((n && (n.title || n.content)) || "") ||
      ((n && (n.title || n.content)) || "(без названия)")
    );
  }

  function knowledgeNotesByProject() {
    var groups = [];
    var index = {};
    knowledgeNotesList().forEach(function (n) {
      var project = noteProjectOf(n);
      var key = project && project.id != null ? String(project.id) : "";
      var title = project && (project.name || project.title)
        ? String(project.name || project.title)
        : "Без проекта";
      if (!index[key]) {
        index[key] = { key: key, title: title, notes: [] };
        groups.push(index[key]);
      }
      index[key].notes.push(n);
    });
    groups.sort(function (a, b) {
      if (!a.key) return 1;
      if (!b.key) return -1;
      return String(a.title).localeCompare(String(b.title), "ru");
    });
    return groups;
  }

  function kbGroupIsOpen(key, hasSelected) {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && wrap._kbOpenGroups && Object.prototype.hasOwnProperty.call(wrap._kbOpenGroups, key)) {
      return !!wrap._kbOpenGroups[key];
    }
    return !!hasSelected;
  }

  function setKbGroupOpen(key, open) {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap) return;
    if (!wrap._kbOpenGroups) wrap._kbOpenGroups = {};
    wrap._kbOpenGroups[key] = !!open;
  }

  function setNoteKnowledgeProjectSelection(ids, selected) {
    var want = {};
    (ids || []).forEach(function (id) {
      var sid = String(id || "").trim();
      if (sid) want[sid] = true;
    });
    var next = noteKnowledgeNoteIds().filter(function (id) {
      return !want[id];
    });
    if (selected) {
      Object.keys(want).forEach(function (id) {
        next.push(id);
      });
    }
    setNoteKnowledgeNoteIds(next);
  }

  function appendContextMenuSection(list, label) {
    var head = document.createElement("p");
    head.className = "note-paie-menu-section";
    head.textContent = label;
    list.appendChild(head);
  }

  function makeContextCheckbox(checked, indeterminate) {
    var box = document.createElement("span");
    box.className =
      "note-paie-kb-check" +
      (checked ? " is-on" : "") +
      (indeterminate ? " is-mixed" : "");
    box.setAttribute("aria-hidden", "true");
    return box;
  }

  function appendContextCheckRow(list, title, on, onClick) {
    var row = document.createElement("button");
    row.type = "button";
    row.className = "note-paie-menu-item note-paie-kb-item";
    row.setAttribute("role", "menuitemcheckbox");
    row.setAttribute("aria-checked", on ? "true" : "false");
    row.appendChild(makeContextCheckbox(on, false));
    var name = document.createElement("span");
    name.className = "note-paie-kb-item-title";
    name.textContent = title;
    row.appendChild(name);
    row.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      onClick();
    });
    list.appendChild(row);
  }

  function appendKnowledgeProjectGroup(list, group, selected) {
    var ids = group.notes
      .map(function (n) {
        return n && n.id != null ? String(n.id) : "";
      })
      .filter(Boolean);
    var selectedCount = ids.filter(function (id) {
      return !!selected[id];
    }).length;
    var allOn = ids.length > 0 && selectedCount === ids.length;
    var someOn = selectedCount > 0 && selectedCount < ids.length;
    var open = kbGroupIsOpen(group.key, selectedCount > 0);
    var wrap = document.createElement("div");
    wrap.className = "note-paie-kb-group" + (open ? " is-open" : "");
    var head = document.createElement("div");
    head.className = "note-paie-kb-group-head";
    var checkBtn = document.createElement("button");
    checkBtn.type = "button";
    checkBtn.className = "note-paie-kb-group-check";
    checkBtn.setAttribute("aria-label", allOn ? "Снять выбор проекта" : "Выбрать весь проект");
    var box = makeContextCheckbox(allOn, someOn);
    checkBtn.appendChild(box);
    checkBtn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      setNoteKnowledgeProjectSelection(ids, !allOn);
      if (!allOn) setKbGroupOpen(group.key, true);
    });
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "note-paie-kb-group-toggle";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    var name = document.createElement("span");
    name.className = "note-paie-kb-item-title";
    name.textContent = group.title;
    var chevron = document.createElement("span");
    chevron.className = "note-paie-kb-group-chevron";
    chevron.setAttribute("aria-hidden", "true");
    chevron.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
    toggle.appendChild(name);
    toggle.appendChild(chevron);
    toggle.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var next = !kbGroupIsOpen(group.key, selectedCount > 0);
      setKbGroupOpen(group.key, next);
      renderNoteKnowledgeMenu();
    });
    head.appendChild(checkBtn);
    head.appendChild(toggle);
    wrap.appendChild(head);
    var body = document.createElement("div");
    body.className = "note-paie-kb-group-body";
    if (!open) body.classList.add("hidden");
    group.notes.forEach(function (n) {
      var id = n && n.id != null ? String(n.id) : "";
      if (!id) return;
      appendContextCheckRow(body, knowledgeNoteMenuTitle(n), !!selected[id], function () {
        toggleNoteKnowledgeNoteId(id);
      });
    });
    wrap.appendChild(body);
    list.appendChild(wrap);
  }

  function toggleNoteSheetContextId(id) {
    id = String(id || "").trim();
    if (!id) return;
    var current = selectedContextSheetIds();
    var next =
      current.indexOf(id) >= 0
        ? current.filter(function (x) {
            return x !== id;
          })
        : current.concat([id]);
    noteSheetState.contextExplicit = true;
    noteSheetState.contextIds = next;
    renderNoteKnowledgeMenu();
    syncNoteKnowledgeToggle();
  }

  function renderNoteKnowledgeMenu() {
    var form = document.getElementById("note-paie-reply-form");
    var list = form && form.querySelector(".note-paie-menu--kb .note-paie-menu-list");
    if (!list) return;
    list.innerHTML = "";
    if (noteSheetsEnabled() && noteSheetState.sheets.length) {
      appendContextMenuSection(list, "Листы заметки");
      var selectedSheets = {};
      selectedContextSheetIds().forEach(function (id) {
        selectedSheets[id] = true;
      });
      noteSheetState.sheets.forEach(function (s) {
        var sid = String(s.id);
        appendContextCheckRow(list, s.title || "Лист", !!selectedSheets[sid], function () {
          toggleNoteSheetContextId(sid);
        });
      });
    }
    appendContextMenuSection(list, "База знаний");
    var notes = knowledgeNotesList();
    if (!notes.length) {
      var empty = document.createElement("p");
      empty.className = "note-paie-menu-empty";
      empty.textContent = knowledgeDataCache ? "Нет документов в базе знаний" : "Загрузка…";
      list.appendChild(empty);
      return;
    }
    var selected = {};
    noteKnowledgeNoteIds().forEach(function (id) {
      selected[id] = true;
    });
    knowledgeNotesByProject().forEach(function (group) {
      appendKnowledgeProjectGroup(list, group, selected);
    });
  }

  function syncNoteKnowledgeToggle() {
    var form = document.getElementById("note-paie-reply-form");
    var btn = form && form.querySelector(".note-paie-kb-toggle");
    if (!btn) return;
    var on = noteUsesKnowledge() || selectedContextSheetIds().length > 0;
    var menu = form.querySelector(".note-paie-menu--kb");
    var open = !!(menu && !menu.classList.contains("hidden"));
    btn.classList.toggle("is-on", on);
    btn.classList.toggle("is-open", open);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.title = on
      ? "Контекст: листы и база знаний"
      : "Контекст: ничего не выбрано";
  }

  function bindNoteKnowledgeToggle(form) {
    var btn = form && form.querySelector(".note-paie-kb-toggle");
    var menu = form && form.querySelector(".note-paie-menu--kb");
    if (!btn || btn._bound) {
      syncNoteKnowledgeToggle();
      return;
    }
    btn._bound = true;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var open = menu && menu.classList.contains("hidden");
      closeNoteComposerMenus(open ? menu : null);
      if (menu && open) {
        menu.classList.remove("hidden");
        btn.classList.add("is-open");
        btn.setAttribute("aria-expanded", "true");
        renderNoteKnowledgeMenu();
        ensureDiscussionKnowledgeNotes().then(function () {
          renderNoteKnowledgeMenu();
          syncNoteKnowledgeToggle();
        });
      }
      syncNoteKnowledgeToggle();
      syncNoteFormatToolbarForComposer();
    });
    syncNoteKnowledgeToggle();
  }

  function noteAskMode() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var mode = wrap && wrap._askMode;
    if (mode === "paie" || mode === "comment" || mode === "research" || mode === "gpt") {
      return mode;
    }
    if (isCustomAskMode(mode)) return mode;
    return "gpt";
  }

  function setNoteAskMode(mode) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var prev = wrap && wrap._askMode;
    var next =
      mode === "paie" || mode === "comment" || mode === "research" || mode === "gpt"
        ? mode
        : isCustomAskMode(mode)
          ? mode
          : "gpt";
    var modeChanged = next !== prev;
    if (wrap) wrap._askMode = next;
    // Не сбрасывать выбранную модель при каждом ensureNotePaieThread —
    // только при реальной смене режима (gpt ↔ свой агент ↔ comment…).
    if (modeChanged) {
      if (isCustomAskMode(next)) {
        var custom = findUserAgent(next);
        composerAgentModel = (custom && custom.model) || "";
      } else if (next === "gpt") {
        try {
          composerAgentModel = String(localStorage.getItem(GPT_MODEL_LS) || "").trim();
        } catch (_) {
          composerAgentModel = composerAgentModel || "";
        }
      } else {
        composerAgentModel = "";
      }
    }
    var current = noteAskMode();
    var form = document.getElementById("note-paie-reply-form");
    var el = document.getElementById("note-paie-thread");
    if (el) {
      el.querySelectorAll(".note-paie-mode, .note-paie-mode-menu [data-mode]").forEach(function (btn) {
        btn.classList.toggle("is-active", btn.getAttribute("data-mode") === current);
      });
    }
    if (form) {
      form.classList.toggle("is-gpt-mode", isChatAskMode(current));
      form.classList.toggle("is-research-mode", current === "research");
      form.classList.toggle("is-comment-mode", current === "comment");
      var compactLabel = form.querySelector(".note-paie-mode-compact-label");
      if (compactLabel) compactLabel.textContent = noteAskModeLabel(current);
      if (current === "comment") showPlusMenuPage("root");
    }
    var input = document.getElementById("note-paie-reply-input");
    if (input) {
      input.placeholder = noteAskPlaceholder(current);
      autosizeNoteComposer(input);
    }
    var editor = noteGptEditor();
    if (editor) {
      editor.setAttribute("data-placeholder", noteAskPlaceholder(current));
      syncGptEditorEmpty(editor);
      autosizeNoteComposer(editor);
    }
    syncNoteComposerCommentTarget();
    if (isChatAskMode(current)) loadGptModels();
    else closeNoteComposerMenus();
    syncNotePaieReplyForm();
  }

  function sheetPlainText(sheet) {
    if (!sheet) return "";
    if (isPrimarySheetId(sheet.id) && isPrimarySheetId(noteSheetState.leftId)) {
      var root = noteEditorCommentRoot();
      if (root) return String(root.innerText || root.textContent || "").trim();
    }
    if (String(noteSheetState.leftId) === String(sheet.id)) {
      var left = getLeftNoteRichEditor();
      if (left && left.getHtml) {
        var node = document.createElement("div");
        node.innerHTML = left.getHtml();
        return String(node.innerText || node.textContent || "").trim();
      }
    }
    if (String(noteSheetState.rightId) === String(sheet.id) && noteSheetState.rightEditor) {
      var nodeR = document.createElement("div");
      nodeR.innerHTML = noteSheetState.rightEditor.getHtml
        ? noteSheetState.rightEditor.getHtml()
        : "";
      return String(nodeR.innerText || nodeR.textContent || "").trim();
    }
    var raw = String(sheet.body || sheet.description || "");
    var tmp = document.createElement("div");
    tmp.innerHTML = raw;
    return String(tmp.innerText || tmp.textContent || raw.replace(/<[^>]+>/g, "")).trim();
  }

  function packSelectedSheetsText(budget) {
    var lim = budget || 8000;
    var ids = selectedContextSheetIds();
    var focus = focusedNoteSheetId();
    var rows = [];
    ids.forEach(function (id) {
      var sheet = findNoteSheet(id);
      if (!sheet) return;
      rows.push({ id: String(sheet.id), title: sheet.title || "Лист", body: sheetPlainText(sheet) });
    });
    if (!rows.length) return "";
    var focusRow = null;
    var rest = [];
    rows.forEach(function (row) {
      if (row.id === focus && !focusRow) focusRow = row;
      else rest.push(row);
    });
    if (!focusRow) {
      focusRow = rows[0];
      rest = rows.slice(1);
    }
    var focusBudget = rest.length ? Math.floor(lim * 0.7) : lim;
    var restBudget = Math.max(120, lim - focusBudget);
    function block(row, size) {
      var body = String(row.body || "");
      if (body.length > size) body = body.slice(0, size - 1) + "…";
      return "[" + (row.title || "Лист") + "]\n" + body;
    }
    var parts = [block(focusRow, focusBudget)];
    if (rest.length) {
      var each = Math.max(80, Math.floor(restBudget / rest.length));
      rest.forEach(function (row) {
        parts.push(block(row, each));
      });
    }
    var text = parts.join("\n\n");
    if (text.length > lim) text = text.slice(0, lim - 1) + "…";
    return text;
  }

  function activeNoteGptContext() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && wrap._shareKind === "chat") {
      return { title: "", text: "", sheet_ids: [] };
    }
    var title = "";
    var titleEl = document.getElementById("note-editor-title-input");
    if (titleEl) title = String(titleEl.value || "").trim();
    var body = packSelectedSheetsText(8000);
    if (!body) {
      var root = noteEditorCommentRoot();
      body = root ? String(root.innerText || root.textContent || "").trim() : "";
      if (body.length > 8000) body = body.slice(0, 7999) + "…";
    }
    return { title: title, text: body, sheet_ids: selectedContextSheetIds() };
  }

  var GPT_COMMENT_BODY_MAX = 24000;
  var GPT_HISTORY_MAX_TURNS = 16;
  var GPT_HISTORY_MAX_CHARS = 18000;

  function gptContextCutKey() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    return "leo_gpt_ctx_cut:" + wrap._shareKind + ":" + wrap._shareId;
  }

  function gptContextCutId() {
    try {
      var n = parseInt(localStorage.getItem(gptContextCutKey()) || "", 10);
      if (isFinite(n) && n > 0) return n;
    } catch (_) {}
    return 0;
  }

  function setGptContextCutId(id) {
    var key = gptContextCutKey();
    if (!key) return;
    try {
      if (id > 0) localStorage.setItem(key, String(id));
      else localStorage.removeItem(key);
    } catch (_) {}
  }

  function clipGptCommentBody(text) {
    var s = String(text || "");
    if (s.length <= GPT_COMMENT_BODY_MAX) return s;
    return s.slice(0, GPT_COMMENT_BODY_MAX - 1) + "…";
  }

  function gptPhaseLabel(phase) {
    if (phase === "sending") return "Отправляю…";
    if (phase === "saving") return "Сохраняю ответ…";
    return "Думает…";
  }

  function renderGptPendingStatus(phase) {
    var item = document.createElement("article");
    item.id = "note-discuss-pending";
    item.className = "note-discuss-msg is-assistant is-pending";
    item.setAttribute("aria-live", "polite");
    var role = document.createElement("p");
    role.className = "note-discuss-role";
    var mode = noteAskMode();
    if (mode === "gpt" || !mode) {
      role.textContent = gptModelShortName(selectedGptModelId()) || "GPT";
    } else {
      var agent = findUserAgent(mode);
      role.textContent = (agent && agent.title) || "Агент";
    }
    var bubble = document.createElement("div");
    bubble.className = "note-discuss-bubble note-discuss-bubble--pending";
    bubble.innerHTML =
      '<span class="note-discuss-pending-dots" aria-hidden="true"><i></i><i></i><i></i></span>';
    var label = document.createElement("span");
    label.className = "note-discuss-pending-label";
    label.textContent = gptPhaseLabel(phase);
    bubble.appendChild(label);
    item.appendChild(role);
    item.appendChild(bubble);
    return item;
  }

  function renderGptContextDivider() {
    var el = document.createElement("div");
    el.className = "note-discuss-ctx-cut";
    el.innerHTML = "<span>Новый контекст</span>";
    return el;
  }

  function scrollDiscussionToEnd() {
    var scroll = document.querySelector("#note-discussion-overlay .note-discussion-scroll");
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
  }

  function setGptDiscussPhase(phase) {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) wrap._gptPhase = phase || "thinking";
    var label = document.querySelector("#note-discuss-pending .note-discuss-pending-label");
    if (label) {
      label.textContent = gptPhaseLabel(wrap && wrap._gptPhase);
      return;
    }
    var list = document.getElementById("note-discussion-messages");
    if (!list || !wrap || !wrap._gptBusy) return;
    var empty = list.querySelector(".note-discussion-empty");
    if (empty) empty.remove();
    list.appendChild(renderGptPendingStatus(wrap._gptPhase));
    scrollDiscussionToEnd();
  }

  function appendOptimisticUserMessage(text, quote) {
    var list = document.getElementById("note-discussion-messages");
    if (!list) return;
    var empty = list.querySelector(".note-discussion-empty");
    if (empty) empty.remove();
    var old = document.getElementById("note-discuss-optimistic-user");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var item = document.createElement("article");
    item.id = "note-discuss-optimistic-user";
    item.className = "note-discuss-msg is-user is-optimistic";
    if (quote) {
      var q = document.createElement("p");
      q.className = "note-discuss-quote";
      q.textContent = "«" + String(quote) + "»";
      item.appendChild(q);
    }
    var role = document.createElement("p");
    role.className = "note-discuss-role";
    role.textContent = "Вы";
    item.appendChild(role);
    if (text) {
      var bubble = document.createElement("div");
      bubble.className = "note-discuss-bubble";
      bubble.textContent = String(text || "");
      item.appendChild(bubble);
    }
    renderPendingAttachThumbs(item);
    var meta = document.createElement("p");
    meta.className = "note-discuss-send-state";
    meta.textContent = "Отправляю…";
    item.appendChild(meta);
    list.appendChild(item);
    scrollDiscussionToEnd();
  }

  function markOptimisticUserSent() {
    var meta = document.querySelector("#note-discuss-optimistic-user .note-discuss-send-state");
    if (meta) meta.textContent = "Отправлено";
  }

  function gptHistoryFromComments(comments, agentId) {
    var api = window.NoteComments;
    var cut = gptContextCutId();
    var customId = agentId && agentId !== "gpt" ? String(agentId) : "";
    var prior = discussionComments(comments || [])
      .filter(function (c) {
        if (!c || Number(c.id) <= cut) return false;
        if (customId) {
          return api && api.agentIdOf ? api.agentIdOf(c) === customId : String(c.prefix || "") === "__agent__:" + customId;
        }
        if (api && api.isAgentTurn && api.isAgentTurn(c)) return false;
        return true;
      })
      .slice()
      .sort(function (a, b) {
        var at = Date.parse(a && a.created_at) || 0;
        var bt = Date.parse(b && b.created_at) || 0;
        if (at !== bt) return at - bt;
        return Number(a.id || 0) - Number(b.id || 0);
      });
    var history = [];
    prior.forEach(function (c) {
      var content = String((c && c.body) || "").trim();
      var names = ((c && c.attachments) || [])
        .map(function (a) {
          return a && a.name;
        })
        .filter(Boolean);
      if (names.length) {
        content = (content && content !== "📎" ? content + "\n" : "") + "[вложения: " + names.join(", ") + "]";
      }
      if (!content) return;
      var quote = String((c && c.quote) || "").trim();
      if (quote && content.indexOf("«") === -1) content = "Про текст: «" + quote + "»\n\n" + content;
      if (content.length > 6000) content = content.slice(0, 5999) + "…";
      if (customId) {
        var agentUname = String((c && c.author_username) || "").trim().toLowerCase();
        history.push({
          role: agentUname === "agent" ? "assistant" : "user",
          content: content,
        });
        return;
      }
      if (api && api.isGptComment && api.isGptComment(c)) {
        history.push({ role: "assistant", content: content });
        return;
      }
      if (api && api.isResearchTurn && api.isResearchTurn(c)) {
        return;
      }
      if (api && api.isPaieComment && api.isPaieComment(c)) {
        history.push({ role: "user", content: "CHAIR:\n" + content });
        return;
      }
      var who = discussionHistoryAuthorLabel(c);
      history.push({
        role: "user",
        content: who ? who + ":\n" + content : content,
      });
    });
    if (history.length > GPT_HISTORY_MAX_TURNS) {
      history = history.slice(-GPT_HISTORY_MAX_TURNS);
    }
    var total = 0;
    var i;
    for (i = history.length - 1; i >= 0; i--) {
      total += String((history[i] && history[i].content) || "").length;
      if (total > GPT_HISTORY_MAX_CHARS) {
        history = history.slice(i + 1);
        break;
      }
    }
    return history;
  }

  async function clearGptDiscussionContext() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || wrap._gptBusy) return;
    var panel = document.getElementById("note-editor-comments");
    var rows = discussionComments((panel && panel._allComments) || []);
    if (!rows.length) {
      showNoteToast("Пока нет сообщений");
      return;
    }
    var ok = await confirmDialog(
      "Начать новый контекст для GPT? Старые сообщения останутся в ленте, но модель их больше не увидит."
    );
    if (!ok) return;
    var maxId = 0;
    rows.forEach(function (c) {
      var n = Number(c && c.id) || 0;
      if (n > maxId) maxId = n;
    });
    setGptContextCutId(maxId);
    renderNotePaieThread(
      (panel && panel._allComments) || [],
      wrap._shareKind,
      wrap._shareId
    );
    showNoteToast("Контекст очищен");
  }

  async function submitNoteGptQuestion(text) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var packed = serializeGptComposer();
    var quotes = packed.quotes || [];
    var body = packed.text || String(text || "").trim();
    var quote = quotes.join("\n");
    var pending = discussPendingFiles();
    var contextIds = discussContextNoteIds();
    if (!wrap || !wrap._shareKind || !wrap._shareId || wrap._gptBusy || (!body && !pending.length && !contextIds.length)) return;
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    var sessionTitle = noteTitleForDiscussToast(kind, itemId, getNoteEditorTitle().trim() || "");
    var panel = document.getElementById("note-editor-comments");
    var agentId = isCustomAskMode(noteAskMode()) ? noteAskMode() : "gpt";
    var agentPrefix = agentId === "gpt" ? "__gpt__" : "__agent__:" + agentId;
    var history = gptHistoryFromComments((panel && panel._allComments) || [], agentId);
    var noteCtxEarly = activeNoteGptContext();
    var kbEarly = discussionKnowledgeFields();
    wrap._gptBusy = true;
    wrap._gptPhase = "sending";
    wrap._discussionPinId = null;
    beginNoteAgentSession(kind, itemId, agentId === "gpt" ? "gpt" : agentId, sessionTitle);
    syncNotePaieReplyForm(true);
    wrap._commentDraft = null;
    appendOptimisticUserMessage(body, "");
    setGptDiscussPhase("sending");
    try {
      var fileIds = await uploadDiscussPendingFiles(kind, itemId);
      clearDiscussPendingFiles();
      clearDiscussPendingContextNotes();
      var saved = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: body,
            quote: quote,
            prefix: agentPrefix,
            suffix: "",
            file_ids: fileIds,
          }),
        }
      );
      if (saved && saved.thread) upsertChatThreadCache(saved.thread);
      var input = document.getElementById("note-paie-reply-input");
      if (input) input.value = "";
      clearGptComposer();
      var parentId = saved && saved.comment && saved.comment.id;
      markOptimisticUserSent();
      setGptDiscussPhase("thinking");
      await refreshNoteCommentsList();
      setGptDiscussPhase("thinking");
      await ensureDiscussionKnowledgeNotes();
      var kb = discussionKnowledgeFields();
      if (!kb.knowledge_note_ids || !kb.knowledge_note_ids.length) kb = kbEarly;
      var noteCtx = noteCtxEarly;
      try {
        var liveCtx = activeNoteGptContext();
        if (liveCtx && (liveCtx.text || liveCtx.title)) noteCtx = liveCtx;
      } catch (_) {}
      var res = await apiFetch("/gpt/chat", {
        method: "POST",
        body: JSON.stringify({
          message: body,
          note_title: noteCtx.title,
          note_text: noteCtx.text,
          quote: quote || undefined,
          quotes: quotes.length ? quotes : undefined,
          use_knowledge: kb.use_knowledge,
          knowledge_note_ids: kb.knowledge_note_ids,
          sheet_ids: noteCtx.sheet_ids,
          history: history,
          model: selectedGptModelId() || undefined,
          agent_id: agentId,
          file_ids: fileIds,
          item_kind: kind,
          item_id: itemId,
          context_note_ids: contextIds.length ? contextIds : undefined,
        }),
      });
      var ans = String((res && res.answer) || "").trim();
      var bullets = (res && res.bullets) || [];
      if (bullets.length) {
        ans +=
          "\n\n" +
          bullets
            .map(function (x) {
              return "• " + x;
            })
            .join("\n");
      }
      var gptFileIds = ((res && res.files) || [])
        .map(function (f) {
          return f && f.id;
        })
        .filter(Boolean);
      if (!ans) ans = gptFileIds.length ? "📎" : "—";
      setGptDiscussPhase("saving");
      var gptSaved = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: clipGptCommentBody(ans),
            quote: quote,
            prefix: agentPrefix,
            suffix: "",
            parent_id: parentId || null,
            as_role: agentId === "gpt" ? "gpt" : "agent:" + agentId,
            model: agentId === "gpt" ? String((res && res.model) || selectedGptModelId() || "") : undefined,
            file_ids: gptFileIds,
          }),
        }
      );
      if (gptSaved && gptSaved.thread) upsertChatThreadCache(gptSaved.thread);
      var liveWrap = document.getElementById("note-editor-more-wrap");
      if (liveWrap && String(liveWrap._shareId) === String(itemId)) {
        liveWrap._gptBusy = false;
        liveWrap._gptPhase = "";
      }
      notifyAgentReplyArrived(kind, itemId, agentId === "gpt" ? "gpt" : agentId, sessionTitle);
      clearNoteComposerCommentTarget();
      if (isViewingNoteDiscussion(kind, itemId)) await refreshNoteCommentsList();
    } catch (e) {
      endNoteAgentSession(kind, itemId);
      var liveErr = document.getElementById("note-editor-more-wrap");
      if (liveErr && String(liveErr._shareId) === String(itemId)) {
        liveErr._gptBusy = false;
        liveErr._gptPhase = "";
      }
      if (isViewingNoteDiscussion(kind, itemId) || (liveErr && String(liveErr._shareId) === String(itemId))) {
        alert(e.message || String(e));
        await refreshNoteCommentsList();
      } else {
        showNoteToast(e.message || "Не удалось получить ответ агента");
      }
    } finally {
      var liveFinally = document.getElementById("note-editor-more-wrap");
      if (liveFinally && String(liveFinally._shareId) === String(itemId)) {
        liveFinally._gptBusy = false;
        liveFinally._gptPhase = "";
      }
      var leftover = document.getElementById("note-discuss-pending");
      if (leftover && leftover.parentNode) leftover.parentNode.removeChild(leftover);
      syncNotePaieReplyForm();
    }
  }

  async function submitNoteFooterComment(text) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var body = String(text || "").trim();
    var pending = discussPendingFiles();
    if (!wrap || !wrap._shareKind || !wrap._shareId || (!body && !pending.length)) return;
    var api = window.NoteComments;
    var chairId = wrap._commentChairId;
    var draft =
      wrap._commentDraft ||
      (api && api.selectionAnchor ? api.selectionAnchor(noteEditorCommentRoot()) : null);
    wrap._discussionPinId = null;
    var input = document.getElementById("note-paie-reply-input");
    try {
      var fileIds = await uploadDiscussPendingFiles(wrap._shareKind, wrap._shareId);
      clearDiscussPendingContextNotes();
      var footSaved = await apiFetch(
        "/notes/" +
          encodeURIComponent(wrap._shareKind) +
          "/" +
          encodeURIComponent(wrap._shareId) +
          "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: body,
            quote: (draft && draft.quote) || "",
            prefix: (draft && draft.prefix) || "",
            suffix: (draft && draft.suffix) || "",
            parent_id: chairId || null,
            file_ids: fileIds,
          }),
        }
      );
      if (footSaved && footSaved.thread) upsertChatThreadCache(footSaved.thread);
      if (input) input.value = "";
      clearGptComposer();
      clearDiscussPendingFiles();
      clearNoteComposerCommentTarget();
      shareHaptic();
      refreshNoteCommentsList();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function postNoteChairAnswerComment(kind, itemId, chairId, text, draft) {
    var body = String(text || "").trim();
    if (!body) return;
    try {
      await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: body,
            quote: (draft && draft.quote) || "",
            prefix: (draft && draft.prefix) || "",
            suffix: (draft && draft.suffix) || "",
            parent_id: chairId,
          }),
        }
      );
      var listEl = document.querySelector("#note-editor-comments .note-comments-list");
      await loadNoteComments(kind, itemId, listEl);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function syncNotePaieReplyForm(running) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var panel = document.getElementById("note-editor-comments");
    var api = window.NoteComments;
    var paieRunning = running != null ? !!running : !!(wrap && wrap._paeiRunning);
    var gptBusy = !!(wrap && wrap._gptBusy);
    var researchRunning = !!(wrap && wrap._researchRunning);
    var busy = paieRunning || gptBusy || researchRunning;
    var input = document.getElementById("note-paie-reply-input");
    var submit = document.getElementById("note-paie-reply-submit");
    var newCtxBtn = document.getElementById("note-discussion-new-ctx");
    if (newCtxBtn) newCtxBtn.disabled = busy;
    var hasChair = !!(
      api &&
      api.paieThreadRootId &&
      api.paieThreadRootId((panel && panel._allComments) || [])
    );
    if (input) input.disabled = busy;
    var editor = noteGptEditor();
    if (editor) {
      editor.setAttribute("contenteditable", busy ? "false" : "true");
      editor.classList.toggle("is-disabled", busy);
      editor.setAttribute("aria-disabled", busy ? "true" : "false");
    }
    var attachBtn = document.getElementById("note-paie-attach");
    if (attachBtn) attachBtn.disabled = busy;
    var modeEl = document.getElementById("note-paie-thread");
    if (modeEl) {
      modeEl
        .querySelectorAll(
          ".note-paie-mode, .note-paie-mode-compact, .note-paie-model-btn, .note-paie-kb-toggle, .note-paie-attach"
        )
        .forEach(function (btn) {
          btn.disabled = busy;
        });
    }
    if (submit) {
      var hasText = noteAskUsesComposerEditor(noteAskMode())
        ? !gptComposerIsEmpty(editor)
        : !!(input && String(input.value || "").trim());
      var hasFiles =
        discussPendingFiles().length > 0 ||
        (noteAskMode() !== "comment" && discussContextNoteIds().length > 0);
      var canLaunchPaie = noteAskMode() === "paie" && !hasChair;
      var canLaunchResearch = noteAskMode() === "research";
      var ready = !busy && (hasText || hasFiles || canLaunchPaie || canLaunchResearch);
      submit.disabled = !ready;
      submit.classList.toggle("is-ready", ready);
      var label = "Отправить";
      if (gptBusy) label = "GPT отвечает…";
      else if (paieRunning) label = "CHAIR отвечает…";
      else if (researchRunning) label = "Research ищет…";
      else if (isChatAskMode(noteAskMode())) label = "Спросить " + noteAskModeLabel(noteAskMode());
      else if (noteAskMode() === "research") label = hasText ? "Спросить Research" : "Запустить Research";
      else if (noteAskMode() === "comment") label = "Отправить";
      else label = hasChair ? "Спросить CHAIR" : "Запустить PAIE";
      submit.setAttribute("aria-label", label);
    }
  }

  function setNotePaeiStatus(text, progress) {
    var el = document.getElementById("note-editor-paei-status");
    if (!el) {
      var thread = ensureNotePaieThread();
      el = thread ? document.getElementById("note-editor-paei-status") : null;
    }
    if (!el) return;
    var wrap = document.getElementById("note-editor-more-wrap");
    var panel = document.getElementById("note-editor-comments");
    var label = (progress && progress.label) || text || "";
    if (looksLikeHtmlError(label)) {
      label = "Сервер временно недоступен. Попробуйте запустить PAIE ещё раз.";
      progress = null;
    }
    if (!label) {
      el.classList.add("hidden");
      el.innerHTML = "";
      renderNotePaieThread(
        (panel && panel._allComments) || (panel && panel._paieComments) || [],
        wrap && wrap._shareKind,
        wrap && wrap._shareId
      );
      return;
    }
    var pct = progress && progress.pct != null ? Number(progress.pct) : 12;
    if (isNaN(pct)) pct = 12;
    pct = Math.max(4, Math.min(100, pct));
    el.classList.remove("hidden");
    el.innerHTML =
      '<span class="note-paei-progress-label"></span>' +
      '<span class="note-paei-progress-track"><span class="note-paei-progress-fill"></span></span>';
    el.querySelector(".note-paei-progress-label").textContent = label;
    el.querySelector(".note-paei-progress-fill").style.width = pct + "%";
    var threadEl = document.getElementById("note-paie-thread");
    if (threadEl) threadEl.classList.remove("hidden");
    syncNotePaieReplyForm();
  }

  function notePaeiPath() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    return (
      "/notes/" +
      encodeURIComponent(wrap._shareKind) +
      "/" +
      encodeURIComponent(wrap._shareId) +
      "/paei"
    );
  }

  function refreshNoteCommentsList() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var listEl = document.querySelector("#note-editor-comments .note-comments-list");
    if (!wrap || !listEl) return Promise.resolve();
    return loadNoteComments(wrap._shareKind, wrap._shareId, listEl);
  }

  function pollNotePaei(path) {
    var meta = parseNoteAgentPath(path);
    var pollKey = meta ? noteAgentSessionKey(meta.kind, meta.itemId) + ":paei" : path;
    if (noteAgentPollers[pollKey]) return;
    noteAgentPollers[pollKey] = true;
    var tries = 0;
    function finishPoll() {
      delete noteAgentPollers[pollKey];
    }
    function tick() {
      var live = document.getElementById("note-editor-more-wrap");
      var sameNote = !!(live && notePaeiPath() === path);
      apiFetch(path, { method: "GET" })
        .then(function (data) {
          var status = (data && data.status) || "idle";
          if (status === "running") {
            tries += 1;
            if (sameNote) {
              setNotePaeiStatus(
                (data.progress && data.progress.label) || "PAIE разбирает заметку…",
                data.progress
              );
            }
            if (tries > 180) {
              if (sameNote) {
                live._paeiRunning = false;
                setNotePaeiStatus("PAIE всё ещё работает. Обновите заметку чуть позже.");
              }
              if (meta) endNoteAgentSession(meta.kind, meta.itemId);
              finishPoll();
              return;
            }
            setTimeout(tick, 2500);
            return;
          }
          if (sameNote) live._paeiRunning = false;
          if (status === "error") {
            if (sameNote) {
              setNotePaeiStatus((data && data.error) || "Не удалось завершить PAIE");
            } else if (meta) {
              showNoteToast((data && data.error) || "Не удалось завершить PAIE");
            }
            if (meta) endNoteAgentSession(meta.kind, meta.itemId);
            finishPoll();
            return;
          }
          if (status === "done") {
            if (sameNote) setNotePaeiStatus("");
            if (meta) {
              var title =
                (noteAgentSessions[noteAgentSessionKey(meta.kind, meta.itemId)] || {}).title ||
                "";
              notifyAgentReplyArrived(meta.kind, meta.itemId, "paie", title);
            } else if (sameNote) {
              shareHaptic();
              refreshNoteCommentsList();
            }
            finishPoll();
            return;
          }
          if (sameNote) setNotePaeiStatus("PAIE прервался. Запустите ещё раз.");
          if (meta) endNoteAgentSession(meta.kind, meta.itemId);
          finishPoll();
        })
        .catch(function (e) {
          tries += 1;
          var code = e && e.status;
          var transient = code === 502 || code === 503 || code === 504 || !code;
          if (transient && tries <= 24) {
            if (sameNote) {
              setNotePaeiStatus("Сервер временно недоступен, пробую снова…", {
                label: "Сервер временно недоступен, пробую снова…",
                pct: 16,
              });
            }
            setTimeout(tick, 3000);
            return;
          }
          if (tries > 8) {
            if (sameNote) {
              live._paeiRunning = false;
              setNotePaeiStatus(e.message || "Не удалось проверить статус PAIE");
            } else {
              showNoteToast(e.message || "Не удалось проверить статус PAIE");
            }
            if (meta) endNoteAgentSession(meta.kind, meta.itemId);
            finishPoll();
            return;
          }
          setTimeout(tick, 2500);
        });
    }
    setTimeout(tick, 2500);
  }

  function startNotePaei() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var path = notePaeiPath();
    if (!wrap || !path || wrap._paeiRunning) return;
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    var sessionTitle = noteTitleForDiscussToast(kind, itemId, getNoteEditorTitle().trim() || "");
    wrap._paeiRunning = true;
    beginNoteAgentSession(kind, itemId, "paie", sessionTitle);
    syncNoteCommentsPanel();
    ensureNotePaieThread();
    openNoteDiscussion({ mode: "paie" });
    setNotePaeiStatus("Анализ заметки…", { label: "Анализ заметки…", pct: 10 });
    ensureDiscussionKnowledgeNotes()
      .then(function () {
        return apiFetch(path, {
          method: "POST",
          body: JSON.stringify(discussionKnowledgeFields()),
        });
      })
      .then(function (data) {
        clearDiscussPendingContextNotes();
        var status = (data && data.status) || "running";
        var live = document.getElementById("note-editor-more-wrap");
        var sameNote = !!(live && String(live._shareId) === String(itemId));
        if (status === "done") {
          if (sameNote) {
            live._paeiRunning = false;
            setNotePaeiStatus("");
          }
          notifyAgentReplyArrived(kind, itemId, "paie", sessionTitle);
          return;
        }
        if (status === "error") {
          if (sameNote) {
            live._paeiRunning = false;
            setNotePaeiStatus((data && data.error) || "Не удалось запустить PAIE");
          } else {
            showNoteToast((data && data.error) || "Не удалось запустить PAIE");
          }
          endNoteAgentSession(kind, itemId);
          return;
        }
        if (sameNote && data && data.progress) {
          setNotePaeiStatus(data.progress.label, data.progress);
        }
        pollNotePaei(path);
      })
      .catch(function (e) {
        var live = document.getElementById("note-editor-more-wrap");
        if (live && String(live._shareId) === String(itemId)) {
          live._paeiRunning = false;
          setNotePaeiStatus(e.message || "Не удалось запустить PAIE");
        } else {
          showNoteToast(e.message || "Не удалось запустить PAIE");
        }
        endNoteAgentSession(kind, itemId);
      })
      .finally(function () {
        syncNotePaieReplyForm();
      });
  }

  function submitNotePaieReply(text) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var path = notePaeiPath();
    var body = String(text || "").trim();
    if (!wrap || !path || wrap._paeiRunning) return;
    var panel = document.getElementById("note-editor-comments");
    var api = window.NoteComments;
    var rootId =
      api && api.paieThreadRootId
        ? api.paieThreadRootId((panel && panel._allComments) || [])
        : null;
    if (!rootId) {
      if (body) {
        submitNoteFooterComment(body).then(function () {
          startNotePaei();
        });
        return;
      }
      startNotePaei();
      return;
    }
    if (!body) return;
    var draft = wrap._commentDraft;
    var replyToId = wrap._commentChairId || null;
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    var sessionTitle = noteTitleForDiscussToast(kind, itemId, getNoteEditorTitle().trim() || "");
    wrap._paeiRunning = true;
    beginNoteAgentSession(kind, itemId, "paie", sessionTitle);
    ensureNotePaieThread();
    setNotePaeiStatus("CHAIR читает уточнение…", {
      label: "CHAIR читает уточнение…",
      pct: 12,
    });
    var input = document.getElementById("note-paie-reply-input");
    var submit = document.getElementById("note-paie-reply-submit");
    if (input) input.disabled = true;
    if (submit) {
      submit.disabled = true;
      submit.classList.remove("is-ready");
      submit.setAttribute("aria-label", "CHAIR отвечает…");
    }
    ensureDiscussionKnowledgeNotes()
      .then(function () {
        return apiFetch(path, {
          method: "POST",
          body: JSON.stringify(
            Object.assign(
              {
                reply: body,
                parent_id: rootId,
                reply_to_id: replyToId,
                quote: (draft && draft.quote) || "",
              },
              discussionKnowledgeFields()
            )
          ),
        });
      })
      .then(function (data) {
        if (input) input.value = "";
        clearNoteComposerCommentTarget();
        clearDiscussPendingContextNotes();
        refreshNoteCommentsList();
        var status = (data && data.status) || "running";
        var live = document.getElementById("note-editor-more-wrap");
        var sameNote = !!(live && String(live._shareId) === String(itemId));
        if (status === "done") {
          if (sameNote) {
            live._paeiRunning = false;
            setNotePaeiStatus("");
          }
          notifyAgentReplyArrived(kind, itemId, "paie", sessionTitle);
          return;
        }
        if (status === "error") {
          if (sameNote) {
            live._paeiRunning = false;
            setNotePaeiStatus((data && data.error) || "Не удалось отправить уточнение");
          } else {
            showNoteToast((data && data.error) || "Не удалось отправить уточнение");
          }
          endNoteAgentSession(kind, itemId);
          return;
        }
        if (sameNote && data && data.progress) {
          setNotePaeiStatus(data.progress.label, data.progress);
        }
        pollNotePaei(path);
      })
      .catch(function (e) {
        var live = document.getElementById("note-editor-more-wrap");
        if (live && String(live._shareId) === String(itemId)) {
          live._paeiRunning = false;
          setNotePaeiStatus(e.message || "Не удалось отправить уточнение");
          renderNotePaieThread(
            (panel && panel._allComments) || (panel && panel._paieComments) || [],
            kind,
            itemId
          );
        } else {
          showNoteToast(e.message || "Не удалось отправить уточнение");
        }
        endNoteAgentSession(kind, itemId);
      })
      .finally(function () {
        syncNotePaieReplyForm();
      });
  }

  function resumeNotePaeiIfRunning(kind, itemId) {
    apiFetch(
      "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/paei",
      { method: "GET" }
    )
      .then(function (data) {
        var wrap = document.getElementById("note-editor-more-wrap");
        if (!wrap || wrap._shareId !== String(itemId)) return;
        if (data && data.status === "running") {
          wrap._paeiRunning = true;
          beginNoteAgentSession(
            kind,
            itemId,
            "paie",
            noteTitleForDiscussToast(kind, itemId, getNoteEditorTitle().trim() || "")
          );
          ensureNotePaieThread();
          setNotePaeiStatus(
            (data.progress && data.progress.label) || "PAIE разбирает заметку…",
            data.progress
          );
          pollNotePaei(notePaeiPath());
          return;
        }
        if (data && data.status === "error") {
          ensureNotePaieThread();
          setNotePaeiStatus((data && data.error) || "Не удалось завершить PAIE");
        }
      })
      .catch(function () {});
  }

  function noteResearchPath() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return "";
    return (
      "/notes/" +
      encodeURIComponent(wrap._shareKind) +
      "/" +
      encodeURIComponent(wrap._shareId) +
      "/research"
    );
  }

  function pollNoteResearch(path) {
    var meta = parseNoteAgentPath(path);
    var pollKey = meta ? noteAgentSessionKey(meta.kind, meta.itemId) + ":research" : path;
    if (noteAgentPollers[pollKey]) return;
    noteAgentPollers[pollKey] = true;
    var tries = 0;
    function finishPoll() {
      delete noteAgentPollers[pollKey];
    }
    function tick() {
      var live = document.getElementById("note-editor-more-wrap");
      var sameNote = !!(live && noteResearchPath() === path);
      apiFetch(path, { method: "GET" })
        .then(function (data) {
          var status = (data && data.status) || "idle";
          if (status === "running") {
            tries += 1;
            if (sameNote) {
              setNotePaeiStatus(
                (data.progress && data.progress.label) || "Ищу в вебе…",
                data.progress
              );
            }
            if (tries > 180) {
              if (sameNote) {
                live._researchRunning = false;
                setNotePaeiStatus("Research всё ещё работает. Обновите заметку чуть позже.");
              }
              if (meta) endNoteAgentSession(meta.kind, meta.itemId);
              finishPoll();
              return;
            }
            setTimeout(tick, 2500);
            return;
          }
          if (sameNote) live._researchRunning = false;
          if (status === "error") {
            if (sameNote) {
              setNotePaeiStatus((data && data.error) || "Не удалось завершить Research");
            } else if (meta) {
              showNoteToast((data && data.error) || "Не удалось завершить Research");
            }
            if (meta) endNoteAgentSession(meta.kind, meta.itemId);
            finishPoll();
            return;
          }
          if (status === "done") {
            if (sameNote) setNotePaeiStatus("");
            if (meta) {
              var title =
                (noteAgentSessions[noteAgentSessionKey(meta.kind, meta.itemId)] || {}).title ||
                "";
              notifyAgentReplyArrived(meta.kind, meta.itemId, "research", title);
            } else if (sameNote) {
              shareHaptic();
              refreshNoteCommentsList();
            }
            finishPoll();
            return;
          }
          if (sameNote) setNotePaeiStatus("Research прервался. Запустите ещё раз.");
          if (meta) endNoteAgentSession(meta.kind, meta.itemId);
          finishPoll();
        })
        .catch(function (e) {
          tries += 1;
          var code = e && e.status;
          var transient = code === 502 || code === 503 || code === 504 || !code;
          if (transient && tries <= 24) {
            if (sameNote) {
              setNotePaeiStatus("Сервер временно недоступен, пробую снова…", {
                label: "Сервер временно недоступен, пробую снова…",
                pct: 16,
              });
            }
            setTimeout(tick, 3000);
            return;
          }
          if (tries > 8) {
            if (sameNote) {
              live._researchRunning = false;
              setNotePaeiStatus(e.message || "Не удалось проверить статус Research");
            } else {
              showNoteToast(e.message || "Не удалось проверить статус Research");
            }
            if (meta) endNoteAgentSession(meta.kind, meta.itemId);
            finishPoll();
            return;
          }
          setTimeout(tick, 2500);
        });
    }
    setTimeout(tick, 2500);
  }

  function submitNoteResearch(text) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var path = noteResearchPath();
    var packed = serializeGptComposer();
    var quotes = packed.quotes || [];
    var body = packed.text || String(text || "").trim();
    var quote = quotes.join("\n");
    if (!wrap || !path || wrap._researchRunning) return;
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    var sessionTitle = noteTitleForDiscussToast(kind, itemId, getNoteEditorTitle().trim() || "");
    wrap._researchRunning = true;
    wrap._discussionPinId = null;
    beginNoteAgentSession(kind, itemId, "research", sessionTitle);
    ensureNotePaieThread();
    openNoteDiscussion({ mode: "research" });
    setNotePaeiStatus(body ? "Ищу в вебе…" : "Исследую заметку…", {
      label: body ? "Ищу в вебе…" : "Исследую заметку…",
      pct: 12,
    });
    appendOptimisticUserMessage(body, quote);
    var editor = noteGptEditor();
    var submit = document.getElementById("note-paie-reply-submit");
    if (submit) {
      submit.disabled = true;
      submit.classList.remove("is-ready");
      submit.setAttribute("aria-label", "Research ищет…");
    }
    ensureDiscussionKnowledgeNotes()
      .then(function () {
        return apiFetch(path, {
          method: "POST",
          body: JSON.stringify(
            Object.assign(
              {
                reply: body,
                quote: quote,
              },
              discussionKnowledgeFields()
            )
          ),
        });
      })
      .then(function (data) {
        clearGptComposer();
        if (editor) syncGptEditorEmpty(editor);
        clearDiscussPendingContextNotes();
        markOptimisticUserSent();
        refreshNoteCommentsList();
        var status = (data && data.status) || "running";
        var live = document.getElementById("note-editor-more-wrap");
        var sameNote = !!(live && String(live._shareId) === String(itemId));
        if (status === "done") {
          if (sameNote) {
            live._researchRunning = false;
            setNotePaeiStatus("");
          }
          notifyAgentReplyArrived(kind, itemId, "research", sessionTitle);
          return;
        }
        if (status === "error") {
          if (sameNote) {
            live._researchRunning = false;
            setNotePaeiStatus((data && data.error) || "Не удалось запустить Research");
          } else {
            showNoteToast((data && data.error) || "Не удалось запустить Research");
          }
          endNoteAgentSession(kind, itemId);
          return;
        }
        if (sameNote && data && data.progress) {
          setNotePaeiStatus(data.progress.label, data.progress);
        }
        pollNoteResearch(path);
      })
      .catch(function (e) {
        var live = document.getElementById("note-editor-more-wrap");
        if (live && String(live._shareId) === String(itemId)) {
          live._researchRunning = false;
          setNotePaeiStatus(e.message || "Не удалось запустить Research");
        } else {
          showNoteToast(e.message || "Не удалось запустить Research");
        }
        endNoteAgentSession(kind, itemId);
      })
      .finally(function () {
        syncNotePaieReplyForm();
      });
  }

  function resumeNoteResearchIfRunning(kind, itemId) {
    apiFetch(
      "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/research",
      { method: "GET" }
    )
      .then(function (data) {
        var wrap = document.getElementById("note-editor-more-wrap");
        if (!wrap || wrap._shareId !== String(itemId)) return;
        if (data && data.status === "running") {
          wrap._researchRunning = true;
          beginNoteAgentSession(
            kind,
            itemId,
            "research",
            noteTitleForDiscussToast(kind, itemId, getNoteEditorTitle().trim() || "")
          );
          ensureNotePaieThread();
          setNotePaeiStatus(
            (data.progress && data.progress.label) || "Ищу в вебе…",
            data.progress
          );
          pollNoteResearch(noteResearchPath());
          return;
        }
        if (data && data.status === "error") {
          ensureNotePaieThread();
          setNotePaeiStatus((data && data.error) || "Не удалось завершить Research");
        }
      })
      .catch(function () {});
  }

  function positionNoteMoreMenu() {
    var menu = document.getElementById("note-editor-more-menu");
    var btn = document.getElementById("note-editor-more-btn");
    if (!menu || !btn || menu.classList.contains("hidden")) return;
    var rect = btn.getBoundingClientRect();
    var gap = 6;
    var margin = 8;
    menu.style.position = "fixed";
    menu.style.right = "auto";
    menu.style.left = "0px";
    menu.style.top = "0px";
    menu.style.zIndex = "130";
    var mw = menu.offsetWidth || 200;
    var mh = menu.offsetHeight || 0;
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var left = rect.right - mw;
    if (left < margin) left = margin;
    if (left + mw > vw - margin) left = Math.max(margin, vw - mw - margin);
    var top = rect.bottom + gap;
    if (top + mh > vh - margin && rect.top - gap - mh >= margin) {
      top = rect.top - gap - mh;
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    var placed = menu.getBoundingClientRect();
    var dx = left - placed.left;
    var dy = top - placed.top;
    if (dx) menu.style.left = left + dx + "px";
    if (dy) menu.style.top = top + dy + "px";
  }

  function openNoteMoreMenu() {
    var menu = document.getElementById("note-editor-more-menu");
    var btn = document.getElementById("note-editor-more-btn");
    if (!menu || !btn) return;
    if (tagPickerActiveClose) tagPickerActiveClose();
    fillNoteMoreMenu();
    menu.classList.remove("hidden");
    btn.setAttribute("aria-expanded", "true");
    positionNoteMoreMenu();
  }

  function toggleNoteMoreMenu(e) {
    if (e) e.stopPropagation();
    var menu = document.getElementById("note-editor-more-menu");
    if (!menu) return;
    if (menu.classList.contains("hidden")) openNoteMoreMenu();
    else closeNoteMoreMenu();
  }

  function applyShareState(wrap, data) {
    if (!wrap) return;
    wrap._shareShared = !!(data && data.shared);
    wrap._shareUrl = (data && data.url) || "";
    wrap._shareAccess = data && data.access === "comment" ? "comment" : "view";
    wrap._shareSheetIds = Array.isArray(data && data.sheet_ids)
      ? data.sheet_ids.map(function (id) { return String(id); })
      : [];
    syncNoteCommentsPanel();
    patchLocalShareInCache(wrap._shareId, {
      shared: wrap._shareShared,
      share_url: wrap._shareUrl,
      share_access: wrap._shareAccess,
      share_sheet_ids: wrap._shareSheetIds,
    });
    if (notesDataCache) renderNotesPanesFromData(notesDataCache);
  }

  var listShareCtx = null;

  function getShareCtx() {
    if (isNoteEditorModalOpen()) {
      var more = document.getElementById("note-editor-more-wrap");
      if (more && more._shareKind) return more;
    }
    if (listShareCtx && listShareCtx._shareKind) return listShareCtx;
    return document.getElementById("note-editor-more-wrap");
  }

  function patchLocalShareInCache(noteId, fields) {
    if (!notesDataCache || !notesDataCache.local_notes || !noteId) return;
    var id = String(noteId);
    notesDataCache.local_notes.forEach(function (n) {
      if (String(n.id) !== id) return;
      Object.assign(n, fields || {});
    });
  }

  function selectedShareAccess() {
    var checked = document.querySelector('input[name="note-share-access"]:checked');
    return checked && checked.value === "comment" ? "comment" : "view";
  }

  function closeShareAccessSheet() {
    var ov = document.getElementById("note-share-sheet-overlay");
    if (ov) ov.classList.add("hidden");
    listShareCtx = null;
  }

  function openShareAccessSheet() {
    var wrap = getShareCtx();
    if (!wrap || !wrap._shareKind || !wrap._shareId) return;
    var ov = document.getElementById("note-share-sheet-overlay");
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "note-share-sheet-overlay";
      ov.className = "note-share-sheet-overlay hidden";
      ov.innerHTML =
        '<div class="note-share-sheet" role="dialog" aria-labelledby="note-share-sheet-title">' +
        '<h3 id="note-share-sheet-title" class="note-share-sheet-title">Совместный доступ</h3>' +
        '<div class="note-share-members-block">' +
        '<p class="note-share-section-label">Участники</p>' +
        '<div id="note-share-members-list" class="note-share-members-list"></div>' +
        '<div class="note-share-member-add">' +
        '<input id="note-share-member-input" type="text" class="field-input" placeholder="Контакт из записной книжки" autocomplete="off" />' +
        '<div id="note-share-member-suggest" class="meeting-attendee-suggest hidden" role="listbox"></div>' +
        "</div>" +
        '<p id="note-share-members-hint" class="note-share-members-hint muted small">Добавьте человека из контактов — заметка появится у него в списке с правом редактировать.</p>' +
        "</div>" +
        '<div id="note-share-sheets-block" class="note-share-sheets-block hidden">' +
        '<p class="note-share-section-label">Листы</p>' +
        '<div id="note-share-sheets-list" class="note-share-sheets-list"></div>' +
        "</div>" +
        '<p class="note-share-section-label">Ссылка</p>' +
        '<label class="note-share-option">' +
        '<input type="radio" name="note-share-access" value="view" />' +
        "<span><strong>Просмотр</strong><small>Любой со ссылкой сможет читать</small></span>" +
        "</label>" +
        '<label class="note-share-option">' +
        '<input type="radio" name="note-share-access" value="comment" />' +
        "<span><strong>Комментарии</strong><small>Можно читать и оставлять комментарии после входа</small></span>" +
        "</label>" +
        '<div class="note-share-sheet-actions">' +
        '<button type="button" class="btn" id="note-share-sheet-copy">Скопировать ссылку</button>' +
        '<button type="button" class="btn-text" id="note-share-sheet-cancel">Закрыть</button>' +
        "</div>" +
        "</div>";
      ov.addEventListener("click", function (e) {
        if (e.target === ov) closeShareAccessSheet();
      });
      document.body.appendChild(ov);
      var copyBtn = document.getElementById("note-share-sheet-copy");
      var cancelBtn = document.getElementById("note-share-sheet-cancel");
      if (copyBtn) {
        copyBtn.addEventListener("click", function () {
          createAndCopyShareLink(selectedShareAccess());
        });
      }
      if (cancelBtn) cancelBtn.addEventListener("click", closeShareAccessSheet);
      bindShareMemberPicker();
    }
    var access = wrap._shareShared && wrap._shareAccess === "comment" ? "comment" : "view";
    var radios = ov.querySelectorAll('input[name="note-share-access"]');
    radios.forEach(function (radio) {
      radio.checked = radio.value === access;
    });
    var copy = document.getElementById("note-share-sheet-copy");
    if (copy) copy.textContent = wrap._shareShared ? "Сохранить и скопировать" : "Скопировать ссылку";
    var membersBlock = ov.querySelector(".note-share-members-block");
    var membersHint = document.getElementById("note-share-members-hint");
    var supportsMembers =
      wrap._shareKind === "local" || wrap._shareKind === "chat";
    if (membersBlock) {
      membersBlock.classList.toggle("hidden", !supportsMembers);
    }
    if (membersHint) {
      membersHint.textContent =
        wrap._shareKind === "chat"
          ? "Добавьте человека из контактов — чат появится у него во вкладке «Чаты»."
          : "Добавьте человека из контактов — заметка появится у него в списке с правом редактировать.";
    }
    paintShareMembersList();
    paintShareSheetPicker();
    ov.classList.remove("hidden");
    if (supportsMembers) {
      loadShareMemberContacts();
    }
    if (wrap._shareKind === "local") {
      refreshShareSheetsState();
    }
  }

  function defaultShareSheetIds() {
    var ids = [];
    if (noteSheetsEnabled()) {
      var left = String(noteSheetState.leftId || NOTE_SHEET_MAIN);
      if (left) ids.push(left);
      if (noteSheetState.rightMode === "sheet" && noteSheetState.rightId) {
        var right = String(noteSheetState.rightId);
        if (right && ids.indexOf(right) < 0) ids.push(right);
      }
    }
    if (!ids.length) ids.push(NOTE_SHEET_MAIN);
    return ids;
  }

  function shareSheetOptions() {
    var wrap = getShareCtx();
    if (noteSheetsEnabled() && noteSheetsNoteId() && wrap && String(noteSheetsNoteId()) === String(wrap._shareId) && noteSheetState.sheets.length) {
      return noteSheetState.sheets.slice();
    }
    return (wrap && Array.isArray(wrap._shareSheets) ? wrap._shareSheets : []).slice();
  }

  function selectedShareSheetIds() {
    var boxes = document.querySelectorAll('#note-share-sheets-list input[name="note-share-sheet"]:checked');
    var ids = [];
    boxes.forEach(function (box) {
      var val = String(box.value || "").trim();
      if (val && ids.indexOf(val) < 0) ids.push(val);
    });
    if (ids.length) return ids;
    var wrap = getShareCtx();
    if (wrap && wrap._shareShared && Array.isArray(wrap._shareSheetIds) && wrap._shareSheetIds.length) {
      return wrap._shareSheetIds.slice();
    }
    return defaultShareSheetIds();
  }

  function paintShareSheetPicker() {
    var ov = document.getElementById("note-share-sheet-overlay");
    var block = document.getElementById("note-share-sheets-block");
    var list = document.getElementById("note-share-sheets-list");
    var wrap = getShareCtx();
    if (!block || !list || !wrap) return;
    if (!block.parentNode && ov) {
      var labels = ov.querySelectorAll(".note-share-section-label");
      var link = null;
      labels.forEach(function (el) {
        if (el.textContent === "Ссылка") link = el;
      });
      if (link && link.parentNode) link.parentNode.insertBefore(block, link);
    }
    var sheets = shareSheetOptions();
    var show = wrap._shareKind === "local" && sheets.length > 1;
    block.classList.toggle("hidden", !show);
    list.innerHTML = "";
    if (!show) return;
    var selected = wrap._shareShared && Array.isArray(wrap._shareSheetIds) && wrap._shareSheetIds.length
      ? wrap._shareSheetIds.slice()
      : defaultShareSheetIds();
    var known = {};
    selected.forEach(function (id) {
      known[String(id)] = true;
    });
    var hasChecked = sheets.some(function (s) {
      return known[String(s.id)];
    });
    sheets.forEach(function (s) {
      var id = String(s.id);
      var label = document.createElement("label");
      label.className = "note-share-option";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.name = "note-share-sheet";
      input.value = id;
      input.checked = hasChecked ? !!known[id] : isPrimarySheetId(id);
      var span = document.createElement("span");
      var strong = document.createElement("strong");
      strong.textContent = s.title || (isPrimarySheetId(id) ? "Основная" : "Лист");
      span.appendChild(strong);
      label.appendChild(input);
      label.appendChild(span);
      list.appendChild(label);
    });
  }

  function refreshShareSheetsState() {
    var wrap = getShareCtx();
    if (!wrap || wrap._shareKind !== "local" || !wrap._shareId) {
      paintShareSheetPicker();
      return;
    }
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    apiFetch("/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/share", {
      method: "GET",
    })
      .then(function (data) {
        if (wrap._shareId !== String(itemId)) return;
        applyShareState(wrap, data);
        var ov = document.getElementById("note-share-sheet-overlay");
        if (ov) {
          var access = wrap._shareShared && wrap._shareAccess === "comment" ? "comment" : "view";
          ov.querySelectorAll('input[name="note-share-access"]').forEach(function (radio) {
            radio.checked = radio.value === access;
          });
          var copy = document.getElementById("note-share-sheet-copy");
          if (copy) copy.textContent = wrap._shareShared ? "Сохранить и скопировать" : "Скопировать ссылку";
        }
        paintShareSheetPicker();
      })
      .catch(function () {});
    if (
      noteSheetsEnabled() &&
      noteSheetsNoteId() &&
      String(noteSheetsNoteId()) === String(wrap._shareId) &&
      noteSheetState.sheets.length > 1
    ) {
      paintShareSheetPicker();
      return;
    }
    apiFetch("/notes/local/" + encodeURIComponent(wrap._shareId) + "/sheets", { method: "GET" })
      .then(function (data) {
        if (!wrap._shareId) return;
        wrap._shareSheets = (data && data.sheets) || [];
        paintShareSheetPicker();
      })
      .catch(function () {
        wrap._shareSheets = [];
        paintShareSheetPicker();
      });
  }

  async function flushOpenNoteForShare() {
    var jobs = [];
    var body = typeof getNoteEditorBodyEl === "function" ? getNoteEditorBodyEl() : null;
    if (body && typeof body._noteEditorFlush === "function") {
      jobs.push(body._noteEditorFlush().catch(function () {}));
    }
    jobs.push(flushSheetEditor("left").catch(function () {}));
    jobs.push(flushSheetEditor("right").catch(function () {}));
    await Promise.all(jobs);
  }

  var shareMemberContacts = [];
  var shareMemberSuggestIndex = 0;

  function bindShareMemberPicker() {
    var input = document.getElementById("note-share-member-input");
    var suggest = document.getElementById("note-share-member-suggest");
    if (!input || !suggest || input._bound) return;
    input._bound = true;
    input.addEventListener("input", function () {
      shareMemberSuggestIndex = 0;
      paintShareMemberSuggest();
    });
    input.addEventListener("focus", paintShareMemberSuggest);
    input.addEventListener("keydown", function (e) {
      var rows = suggest.querySelectorAll(".meeting-attendee-suggest-item");
      if (e.key === "ArrowDown" && rows.length) {
        e.preventDefault();
        shareMemberSuggestIndex = Math.min(rows.length - 1, shareMemberSuggestIndex + 1);
        paintShareMemberSuggest();
      } else if (e.key === "ArrowUp" && rows.length) {
        e.preventDefault();
        shareMemberSuggestIndex = Math.max(0, shareMemberSuggestIndex - 1);
        paintShareMemberSuggest();
      } else if (e.key === "Enter") {
        var active = rows[shareMemberSuggestIndex];
        if (active && active._contact) {
          e.preventDefault();
          addShareMemberFromContact(active._contact);
        }
      } else if (e.key === "Escape") {
        suggest.classList.add("hidden");
      }
    });
    document.addEventListener("mousedown", function (e) {
      if (suggest.classList.contains("hidden")) return;
      if (suggest.contains(e.target) || input.contains(e.target)) return;
      suggest.classList.add("hidden");
    });
  }

  function loadShareMemberContacts() {
    apiFetch("/contacts", { method: "GET" })
      .then(function (data) {
        shareMemberContacts = (data && data.items) || [];
      })
      .catch(function () {
        shareMemberContacts = [];
      });
  }

  function shareMemberAlreadyAdded(contact) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var members = (wrap && wrap._noteMembers) || [];
    var email = String((contact && contact.email) || "").trim().toLowerCase();
    var uname = String((contact && contact.telegram_username) || "")
      .trim()
      .replace(/^@/, "")
      .toLowerCase();
    var tid = String((contact && contact.telegram_user_id) || "");
    return members.some(function (m) {
      if (tid && String(m.user_id) === tid) return true;
      var mu = String(m.username || "")
        .replace(/^@/, "")
        .toLowerCase();
      return !!(uname && mu && mu === uname);
    });
  }

  function paintShareMemberSuggest() {
    var input = document.getElementById("note-share-member-input");
    var suggest = document.getElementById("note-share-member-suggest");
    if (!input || !suggest) return;
    var q = String(input.value || "").trim().toLowerCase();
    var rows = shareMemberContacts.filter(function (c) {
      if (!c) return false;
      if (shareMemberAlreadyAdded(c)) return false;
      var hay = [c.name, c.email, c.telegram_username, (c.aliases || []).join(" ")]
        .join(" ")
        .toLowerCase();
      return !q || hay.indexOf(q) >= 0;
    }).slice(0, 8);
    suggest.innerHTML = "";
    if (!rows.length) {
      suggest.classList.add("hidden");
      return;
    }
    if (shareMemberSuggestIndex >= rows.length) shareMemberSuggestIndex = 0;
    rows.forEach(function (c, i) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "meeting-attendee-suggest-item" + (i === shareMemberSuggestIndex ? " is-active" : "");
      btn._contact = c;
      var name = document.createElement("span");
      name.className = "meeting-attendee-suggest-name";
      name.textContent = c.name || c.email || "Контакт";
      btn.appendChild(name);
      var meta = document.createElement("span");
      meta.className = "meeting-attendee-suggest-meta";
      var tg = String(c.telegram_username || "").trim();
      meta.textContent = tg
        ? "@" + tg.replace(/^@/, "")
        : "Нужен Telegram в контакте";
      btn.appendChild(meta);
      btn.addEventListener("click", function () {
        addShareMemberFromContact(c);
      });
      suggest.appendChild(btn);
    });
    suggest.classList.remove("hidden");
  }

  function paintShareMembersList() {
    var list = document.getElementById("note-share-members-list");
    var wrap = getShareCtx();
    if (!list || !wrap) return;
    var members = wrap._noteMembers || [];
    list.innerHTML = "";
    if (!members.length) {
      var empty = document.createElement("p");
      empty.className = "muted small";
      empty.textContent = "Пока только вы";
      list.appendChild(empty);
      return;
    }
    members.forEach(function (m) {
      var row = document.createElement("div");
      row.className = "note-share-member-row";
      row.appendChild(noteMemberAvatarNode(m));
      var copy = document.createElement("div");
      copy.className = "note-share-member-copy";
      var name = document.createElement("strong");
      name.textContent = m.name || m.username || "Участник";
      copy.appendChild(name);
      var role = document.createElement("small");
      var wrapKind = (getShareCtx() || {})._shareKind;
      role.textContent = m.is_owner
        ? "Автор"
        : wrapKind === "chat"
          ? "Участник"
          : "Редактирование";
      copy.appendChild(role);
      row.appendChild(copy);
      if (!m.is_owner) {
        var del = document.createElement("button");
        del.type = "button";
        del.className = "note-share-member-remove";
        del.setAttribute("aria-label", "Убрать");
        del.textContent = "×";
        del.addEventListener("click", function () {
          removeShareMember(m.user_id);
        });
        row.appendChild(del);
      }
      list.appendChild(row);
    });
  }

  async function addShareMemberFromContact(contact) {
    var wrap = getShareCtx();
    var suggest = document.getElementById("note-share-member-suggest");
    var input = document.getElementById("note-share-member-input");
    if (
      !wrap ||
      !wrap._shareId ||
      (wrap._shareKind !== "local" && wrap._shareKind !== "chat")
    ) {
      return;
    }
    if (suggest) suggest.classList.add("hidden");
    try {
      var membersUrl =
        wrap._shareKind === "chat"
          ? "/chats/" + encodeURIComponent(wrap._shareId) + "/members"
          : "/notes/local/" + encodeURIComponent(wrap._shareId) + "/members";
      var data = await apiFetch(membersUrl, {
        method: "POST",
        body: JSON.stringify({
          email: contact && contact.email ? contact.email : undefined,
          telegram_username:
            contact && contact.telegram_username
              ? contact.telegram_username
              : undefined,
          telegram_user_id:
            contact && contact.telegram_user_id
              ? contact.telegram_user_id
              : undefined,
        }),
      });
      if (wrap._shareKind === "chat" && data && data.chat) {
        wrap._noteMembers = data.chat.members || wrap._noteMembers;
        upsertChatThreadCache(data.chat);
      } else if (data && data.item) {
        wrap._noteMembers = data.item.members || wrap._noteMembers;
        prependLocalInCache(data.item);
        renderOpenNoteMembers(wrap._noteMembers, []);
      }
      if (input) input.value = "";
      paintShareMembersList();
      shareHaptic();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function removeShareMember(memberId) {
    var wrap = getShareCtx();
    if (!wrap || !wrap._shareId || !memberId) return;
    try {
      var url =
        wrap._shareKind === "chat"
          ? "/chats/" +
            encodeURIComponent(wrap._shareId) +
            "/members/" +
            encodeURIComponent(String(memberId))
          : "/notes/local/" +
            encodeURIComponent(wrap._shareId) +
            "/members/" +
            encodeURIComponent(String(memberId));
      var data = await apiFetch(url, { method: "DELETE" });
      if (wrap._shareKind === "chat" && data && data.chat) {
        wrap._noteMembers = data.chat.members || [];
        upsertChatThreadCache(data.chat);
      } else {
        wrap._noteMembers = (wrap._noteMembers || []).filter(function (m) {
          return String(m.user_id) !== String(memberId);
        });
        renderOpenNoteMembers(wrap._noteMembers, []);
      }
      paintShareMembersList();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function createAndCopyShareLink(access) {
    var wrap = getShareCtx();
    var kind = wrap && wrap._shareKind;
    var itemId = wrap && wrap._shareId;
    if (!kind || !itemId) return;
    try {
      await flushOpenNoteForShare();
      var payload = { access: access || "view" };
      if (kind === "local") payload.sheet_ids = selectedShareSheetIds();
      var data = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/share",
        { method: "POST", body: JSON.stringify(payload) }
      );
      applyShareState(wrap, data);
      await copyShareUrl(wrap._shareUrl);
      closeNoteMoreMenu();
      closeShareAccessSheet();
      shareHaptic();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function copyExistingShareLink() {
    var wrap = getShareCtx();
    var url = wrap && wrap._shareUrl;
    var kind = wrap && wrap._shareKind;
    var itemId = wrap && wrap._shareId;
    try {
      if (!url && kind && itemId) {
        var data = await apiFetch(
          "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/share",
          { method: "POST", body: JSON.stringify({ access: wrap._shareAccess || "view" }) }
        );
        applyShareState(wrap, data);
        url = wrap._shareUrl;
      }
      await copyShareUrl(url);
      closeNoteMoreMenu();
      shareHaptic();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function revokeShareLink() {
    var wrap = getShareCtx();
    var kind = wrap && wrap._shareKind;
    var itemId = wrap && wrap._shareId;
    if (!kind || !itemId) return;
    if (!(await confirmDialog("Закрыть публичный доступ по ссылке?"))) return;
    try {
      await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/share",
        { method: "DELETE" }
      );
      wrap._shareShared = false;
      wrap._shareUrl = "";
      wrap._shareAccess = "view";
      wrap._shareSheetIds = [];
      patchLocalShareInCache(itemId, {
        shared: false,
        share_url: null,
        share_access: null,
        share_sheet_ids: [],
      });
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      closeNoteMoreMenu();
      closeShareAccessSheet();
      closeNoteCardMenus();
      hideNoteCommentsPanel();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function hideNoteCommentsPanel() {
    if (window.NoteComments) {
      window.NoteComments.hideBubble();
      if (window.NoteComments.hideCommentSheet) window.NoteComments.hideCommentSheet();
    }
    closeNoteDiscussion(true);
    closeNoteLinkPicker();
    closeNoteAnswerMenu();
    var panel = document.getElementById("note-editor-comments");
    if (panel && typeof panel._commentSelectUnbind === "function") {
      try {
        panel._commentSelectUnbind();
      } catch (_) {}
    }
    if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    var card = document.getElementById("note-discussion-card");
    if (card && card.parentNode) card.parentNode.removeChild(card);
    var messages = document.getElementById("note-discussion-messages");
    if (messages) messages.innerHTML = "";
    var overlay = document.getElementById("note-editor-comment-overlay");
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    var layout = document.querySelector("#note-editor-modal-body .note-editor-doc-layout");
    if (layout) layout.classList.remove("has-comments");
  }

  function noteEditorCommentRoot() {
    return document.querySelector("#note-editor-overlay .note-rich-editor-body");
  }

  function paintEditorCommentOverlay(comments, activeId) {
    var api = window.NoteComments;
    var root = noteEditorCommentRoot();
    var overlay = document.getElementById("note-editor-comment-overlay");
    if (!api || !api.paintOverlay || !overlay) return;
    var panel = document.getElementById("note-editor-comments");
    var all = comments || (panel && panel._allComments) || [];
    api.paintOverlay(root, overlay, all, activeId);
  }

  function onEditorHighlightClick(id, comments) {
    var rows = comments || [];
    var found = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && String(rows[i].id) === String(id)) {
        found = rows[i];
        break;
      }
    }
    revealNoteDiscussionTopic(found || { id: id });
  }

  function refreshNoteCommentAnchors() {
    var panel = document.getElementById("note-editor-comments");
    if (!panel) return;
    var comments = panel._allComments || panel._commentsCache || [];
    paintEditorCommentOverlay(comments, panel._activeCommentId);
    var api = window.NoteComments;
    var listEl = panel.querySelector(".note-comments-list");
    if (api && api.alignCardsByAnchors && listEl) {
      api.alignCardsByAnchors(listEl, noteEditorCommentRoot(), comments);
    }
  }

  function syncSelectionCommentsRail() {
    var panel = document.getElementById("note-editor-comments");
    var layout = document.querySelector("#note-editor-modal-body .note-editor-doc-layout");
    if (panel) panel.classList.add("hidden");
    if (layout) layout.classList.remove("has-comments");
  }

  function renderNoteCommentsList(listEl, comments) {
    if (listEl) listEl.innerHTML = "";
    var panel = document.getElementById("note-editor-comments");
    paintEditorCommentOverlay((panel && panel._allComments) || comments || []);
    syncSelectionCommentsRail();
  }

  function formatShareCommentWhen(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleString("ru-RU", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_) {
      return String(iso);
    }
  }

  function shareCommentAuthorLabel(c) {
    var api = window.NoteComments;
    if (api && api.isPaieComment && api.isPaieComment(c)) return "CHAIR";
    if (api && api.isGptComment && api.isGptComment(c)) {
      var gptName = String((c && c.author_name) || "").trim();
      return gptName || "GPT";
    }
    if (api && api.isResearchComment && api.isResearchComment(c)) return "Research";
    var name = String((c && c.author_name) || "Пользователь").trim();
    var uname = String((c && c.author_username) || "").trim();
    if (uname && name.toLowerCase() !== "@" + uname.toLowerCase()) {
      return name + " · @" + uname;
    }
    return name;
  }

  async function loadNoteComments(kind, itemId, listEl) {
    if (!listEl) return;
    try {
      var data = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        { method: "GET" }
      );
      var comments = (data && data.comments) || [];
      var api = window.NoteComments;
      var thread = api && api.paieThread ? api.paieThread(comments) : [];
      var anchored = api && api.selectionComments ? api.selectionComments(comments) : comments;
      var panel = document.getElementById("note-editor-comments");
      if (panel) {
        panel._commentsCache = anchored;
        panel._allComments = comments;
        panel._paieComments = thread;
      }
      renderNoteCommentsList(listEl, anchored, kind, itemId);
      renderNotePaieThread(comments, kind, itemId);
      if (isViewingNoteDiscussion(kind, itemId) || isNoteDiscussionOpen()) {
        var seenMax = maxCommentId(comments);
        if (seenMax) markDiscussSeenUpTo(kind, itemId, seenMax);
      } else {
        var latestRow = null;
        var mid = maxCommentId(comments);
        if (mid) {
          for (var ci = 0; ci < comments.length; ci++) {
            if (Number(comments[ci].id) === mid) {
              latestRow = comments[ci];
              break;
            }
          }
        }
        if (latestRow) {
          syncDiscussUnreadFromLatest(kind, itemId, {
            id: latestRow.id,
            author_user_id: latestRow.author_user_id,
            is_assistant: isAssistantComment(latestRow),
          });
        }
      }
      syncDesktopNoteDiscussion();
      updateNoteDiscussionCardUnread();
    } catch (e) {
      listEl.innerHTML = "";
      var err = document.createElement("p");
      err.className = "note-comments-empty";
      err.textContent = e.message || "Не удалось загрузить комментарии";
      listEl.appendChild(err);
      ensureNotePaieThread();
      syncDesktopNoteDiscussion();
    }
  }

  async function postNoteComment(kind, itemId, text, listEl, input, draft) {
    var body = String(text || "").trim();
    if (!body) return;
    if (!draft || !draft.quote) {
      alert("Выделите текст в заметке, чтобы оставить комментарий");
      return;
    }
    try {
      await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: body,
            quote: draft.quote || "",
            prefix: draft.prefix || "",
            suffix: draft.suffix || "",
          }),
        }
      );
      if (input) input.value = "";
      var form = document.getElementById("note-editor-comment-form");
      if (form) form.classList.add("hidden");
      await loadNoteComments(kind, itemId, listEl);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function deleteNoteComment(kind, itemId, commentId) {
    if (!(await confirmDialog("Удалить комментарий?"))) return;
    try {
      await apiFetch(
        "/notes/" +
          encodeURIComponent(kind) +
          "/" +
          encodeURIComponent(itemId) +
          "/comments/" +
          encodeURIComponent(String(commentId)),
        { method: "DELETE" }
      );
      var listEl = document.querySelector("#note-editor-comments .note-comments-list");
      await loadNoteComments(kind, itemId, listEl);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function bindEditorCommentSelection(kind, itemId, panel) {
    var api = window.NoteComments;
    if (!api || panel._commentSelectBound) return;
    panel._commentSelectBound = true;
    var draft = null;
    function onSelect(e) {
      if (e && api.isCommentBubbleEvent && api.isCommentBubbleEvent(e)) return;
      var root = noteEditorCommentRoot();
      var sel = api.selectionAnchor(root);
      if (!sel || !sel.quote) {
        api.hideBubble();
        refreshNoteCommentAnchors();
        return;
      }
      draft = sel;
      api.showBubble(
        sel.rect,
        function () {
          var current = noteAskMode();
          var mode = noteAskUsesChips(current)
            ? current
            : current === "comment"
              ? "comment"
              : "gpt";
          openNoteDiscussion({ mode: mode, draft: sel, focus: true });
        },
        {
          onTask: function () {
            var ed = getActiveNoteRichEditor && getActiveNoteRichEditor();
            var range = ed && ed.getSelectionRange ? ed.getSelectionRange() : null;
            openTaskModal(
              {
                summary: sel.quote,
                note_id: currentOpenNoteId(),
              },
              {
                onSaved: function (saved) {
                  var inst = getActiveNoteRichEditor && getActiveNoteRichEditor();
                  if (!inst || !saved || !saved.id) return;
                  var label =
                    saved.chip_label || (saved.event && saved.event.chip_label) || "";
                  if (inst.insertTaskChipAfter && range) {
                    inst.insertTaskChipAfter(saved.id, label, range.to);
                  } else if (inst.insertTaskChip) {
                    inst.insertTaskChip(saved.id, label);
                  }
                },
              }
            );
          },
        }
      );
    }
    function onDocMouseDown(e) {
      if (api.isCommentBubbleEvent && api.isCommentBubbleEvent(e)) return;
      api.hideBubble();
    }
    function onQuoteClick(e) {
      if (!isNoteEditorModalOpen()) return;
      if (
        e.target &&
        e.target.closest &&
        e.target.closest(
          "#note-editor-gpt-btn, .note-discussion-card, .note-comment-bubble, .note-editor-toolbar-wrap"
        )
      ) {
        return;
      }
      var root = noteEditorCommentRoot();
      if (!root || !root.contains(e.target)) return;
      var sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed) return;
      var live = document.getElementById("note-editor-comments");
      var comments = (live && live._allComments) || [];
      if (!api.commentAtPoint) return;
      var hit = api.commentAtPoint(root, comments, e.clientX, e.clientY);
      if (!hit) return;
      revealNoteDiscussionTopic(hit, { fromNote: true });
    }
    var scrollParent = document.querySelector("#note-editor-overlay .note-editor-modal-body");
    document.addEventListener("mouseup", onSelect);
    document.addEventListener("pointerup", onSelect);
    document.addEventListener("keyup", onSelect);
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("click", onQuoteClick);
    if (scrollParent) scrollParent.addEventListener("scroll", refreshNoteCommentAnchors, { passive: true });
    window.addEventListener("resize", refreshNoteCommentAnchors);
    panel._commentSelectUnbind = function () {
      document.removeEventListener("mouseup", onSelect);
      document.removeEventListener("pointerup", onSelect);
      document.removeEventListener("keyup", onSelect);
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("click", onQuoteClick);
      if (scrollParent) scrollParent.removeEventListener("scroll", refreshNoteCommentAnchors);
      window.removeEventListener("resize", refreshNoteCommentAnchors);
    };
    var form = document.getElementById("note-editor-comment-form");
    var input = panel.querySelector(".note-comments-input");
    var cancel = document.getElementById("note-editor-comment-cancel");
    if (form && !form._bound) {
      form._bound = true;
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        postNoteComment(
          kind,
          itemId,
          input && input.value,
          panel.querySelector(".note-comments-list"),
          input,
          form._draft
        );
      });
    }
    if (cancel) {
      cancel.addEventListener("click", function () {
        if (form) form.classList.add("hidden");
        var live = document.getElementById("note-editor-comments");
        syncSelectionCommentsRail(!!(live && live._commentsCache && live._commentsCache.length));
      });
    }
  }

  function syncNoteCommentsPanel() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var layout = document.querySelector("#note-editor-modal-body .note-editor-doc-layout");
    var pad = document.querySelector("#note-editor-modal-body .note-editor-pad--body");
    if (!wrap || !layout || !wrap._shareKind || !wrap._shareId) {
      hideNoteCommentsPanel();
      return;
    }
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    var panel = document.getElementById("note-editor-comments");
    if (!panel) {
      panel = document.createElement("aside");
      panel.id = "note-editor-comments";
      panel.className = "note-comments note-comments-rail hidden";
      panel.innerHTML =
        '<form id="note-editor-comment-form" class="note-comments-form hidden">' +
        '<p id="note-editor-comment-quote" class="share-comment-quote"></p>' +
        '<textarea class="note-comments-input" rows="3" maxlength="4000" placeholder="Комментарий к выделенному тексту"></textarea>' +
        '<div class="share-comments-form-actions">' +
        '<button type="submit" class="btn">Отправить</button>' +
        '<button type="button" class="btn-text" id="note-editor-comment-cancel">Отмена</button>' +
        "</div></form>" +
        '<div class="note-comments-list"></div>';
      layout.appendChild(panel);
      bindEditorCommentSelection(kind, itemId, panel);
    }
    ensureNotePaieThread();
    if (pad && !document.getElementById("note-editor-comment-overlay")) {
      pad.style.position = "relative";
      var overlay = document.createElement("div");
      overlay.id = "note-editor-comment-overlay";
      overlay.className = "note-comment-overlay";
      pad.appendChild(overlay);
    }
    loadNoteComments(kind, itemId, panel.querySelector(".note-comments-list"));
    syncDesktopNoteDiscussion();
  }

  function mountShareControls(kind, itemId) {
    var tagsWrap = document.getElementById("note-editor-tags-wrap");
    if (!tagsWrap || !kind || !itemId) return;
    hideShareControls();
    var wrap = document.createElement("div");
    wrap.id = "note-editor-more-wrap";
    wrap.className = "note-more-wrap";
    wrap._shareKind = kind;
    wrap._shareId = String(itemId);
    wrap._shareShared = false;
    wrap._shareUrl = "";
    wrap._shareAccess = "view";
    wrap._shareSheetIds = [];
    wrap._paeiRunning = false;
    wrap._askMode = "gpt";
    wrap._noteMembers = [];
    wrap._noteRevision = 1;
    wrap._noteUpdatedAt = "";
    wrap._isNoteOwner = true;
    wrap._isKnowledge = false;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "note-editor-more-btn";
    btn.className = "note-more-btn";
    btn.setAttribute("aria-label", "Ещё");
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML =
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
      '<circle cx="12" cy="5" r="1.7"/>' +
      '<circle cx="12" cy="12" r="1.7"/>' +
      '<circle cx="12" cy="19" r="1.7"/>' +
      "</svg>";
    btn.addEventListener("click", toggleNoteMoreMenu);
    var menu = document.createElement("div");
    menu.id = "note-editor-more-menu";
    menu.className = "note-more-menu hidden";
    menu.setAttribute("role", "menu");
    menu.addEventListener("click", function (e) {
      e.stopPropagation();
    });
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    tagsWrap.appendChild(wrap);
    syncNoteCommentsPanel();
    resumeNotePaeiIfRunning(kind, itemId);
    resumeNoteResearchIfRunning(kind, itemId);
    apiFetch("/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/share", {
      method: "GET",
    })
      .then(function (data) {
        if (wrap._shareId !== String(itemId)) return;
        applyShareState(wrap, data);
      })
      .catch(function () {});
  }

  function openNoteEditorModal(title) {
    var ov = document.getElementById("note-editor-overlay");
    var titleEl = document.getElementById("note-editor-modal-title");
    if (titleEl) titleEl.textContent = title || "Заметка";
    if (ov) {
      ov.classList.add("note-editor-overlay--fullscreen");
      ov.classList.remove("hidden");
      ov.setAttribute("aria-hidden", "false");
    }
    applyTelegramSafeAreaInsets();
    onModalSheetOpen();
    updateModalSheetForKeyboard();
    applyNotePaneWidths();
    syncAppOverlay();
    syncTelegramNativeBack();
    syncNotesCreateFab();
  }

  var noteEditorCloseSeq = 0;

  function closeNoteEditorModal() {
    stopTranscriptionSummaryPoll();
    var ov = document.getElementById("note-editor-overlay");
    var body = getNoteEditorBodyEl();
    var flushFn =
      body && typeof body._noteEditorFlush === "function" ? body._noteEditorFlush : null;
    if (ov && ov.classList.contains("hidden") && !flushFn) return;
    var closeSeq = ++noteEditorCloseSeq;
    if (body) body._noteEditorFlush = null;
    if (ov) {
      ov.classList.remove("note-editor-overlay--fullscreen");
      ov.classList.add("hidden");
      ov.setAttribute("aria-hidden", "true");
    }
    onModalSheetClose();
    listShareCtx = null;
    applyTelegramSafeAreaInsets();
    syncTelegramNativeBack();
    syncAppOverlay();
    syncNotesCreateFab();
    if (notesDataCache) renderNotesPanesFromData(notesDataCache);
    if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);

    function teardown() {
      closeNoteDiscussion(true);
      closeShareAccessSheet();
      stopNoteCollab();
      destroyRightSheetEditor();
      resetNoteSheetState("");
      destroyActiveNoteRichEditor();
      clearNoteEditorToolbarWrap();
      clearNoteEditorTagsWrap();
      if (body) {
        body._noteEditor = null;
        body.innerHTML = "";
      }
    }

    if (!flushFn) {
      teardown();
      return;
    }
    Promise.race([
      Promise.resolve().then(flushFn),
      sleepMs(4000),
    ])
      .catch(function () {})
      .then(function () {
        if (closeSeq !== noteEditorCloseSeq || isNoteEditorModalOpen()) return;
        teardown();
        if (notesDataCache) renderNotesPanesFromData(notesDataCache);
        if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
      });
  }

  function closeNotesDetail() {
    var d = document.getElementById("notes-detail");
    var body = document.getElementById("notes-detail-body");
    if (body) body.innerHTML = "";
    var titleSpan = document.getElementById("notes-detail-title");
    if (titleSpan) {
      setHidden(titleSpan, false);
      titleSpan.classList.remove("detail-title--wrap");
    }
    setHidden(d, true);
    setHidden(document.getElementById("notes-root"), false);
    var del = document.getElementById("notes-detail-del");
    if (del) {
      del.onclick = null;
      setHidden(del, true);
    }
    var foot = document.getElementById("notes-detail-footer");
    if (foot) {
      foot.innerHTML = "";
      setHidden(foot, true);
      foot.setAttribute("aria-hidden", "true");
    }
    var backBtn = document.getElementById("notes-detail-back");
    if (backBtn) backBtn.textContent = "← Назад";
    syncTelegramNativeBack();
    syncAppOverlay();
    syncNotesCreateFab();
  }

  function openNotesDetail(title, innerNode, footerNode) {
    setHidden(document.getElementById("notes-root"), true);
    const d = document.getElementById("notes-detail");
    setHidden(d, false);
    pulseMotionEnter(d);
    var titleSpan = document.getElementById("notes-detail-title");
    if (titleSpan) {
      setHidden(titleSpan, false);
      titleSpan.classList.remove("detail-title--wrap");
      titleSpan.textContent = title;
    }
    const body = document.getElementById("notes-detail-body");
    body.innerHTML = "";
    body.appendChild(innerNode);
    const foot = document.getElementById("notes-detail-footer");
    if (foot) {
      foot.innerHTML = "";
      if (footerNode) {
        foot.appendChild(footerNode);
        setHidden(foot, false);
        foot.setAttribute("aria-hidden", "false");
      } else {
        setHidden(foot, true);
        foot.setAttribute("aria-hidden", "true");
      }
    }
    var backBtn = document.getElementById("notes-detail-back");
    if (backBtn) {
      setHidden(backBtn, false);
      backBtn.textContent = "← Назад";
    }
    syncTelegramNativeBack();
    syncAppOverlay();
    syncNotesCreateFab();
  }

  async function loadNotes() {
    setNotesSubTab(notesSubTab);
    const err = document.getElementById("notes-global-error");
    if (err) {
      err.textContent = "";
      setHidden(err, true);
    }
    if (notesDataCache) {
      applyDraftsToNotesList(notesDataCache);
      renderNotesPanesFromData(notesDataCache);
    } else {
      var cachedNotes = readMiniappCache("notes");
      if (cachedNotes) {
        notesDataCache = splitTranscriptionsOutOfLocalNotes(cachedNotes);
        applyDraftsToNotesList(notesDataCache);
        renderNotesPanesFromData(notesDataCache);
      }
    }
    let data;
    try {
      data = await apiFetch("/notes?limit=120", { method: "GET" });
    } catch (e) {
      if (!notesDataCache) {
        if (err) {
          err.textContent = e.message || String(e);
          setHidden(err, false);
        }
      }
      return;
    }
    notesDataCache = splitTranscriptionsOutOfLocalNotes(data);
    if (notesDataCache && Array.isArray(notesDataCache.tags)) {
      notesDataCache._tagsLoaded = true;
    }
    writeMiniappCache("notes", data);
    applyDraftsToNotesList(notesDataCache);
    if (data.journal_error && err) {
      err.textContent = data.journal_error;
      setHidden(err, false);
    }
    renderNotesTagFilterBar();
    renderNotesPanesFromData(notesDataCache);
    openPendingSharedNote(data);
  }

  var noteDeepLinkConsumed = false;
  var chatDeepLinkConsumed = false;
  var taskDeepLinkConsumed = false;

  async function openPendingTaskFromLocation() {
    if (taskDeepLinkConsumed) return;
    var pending = pendingTaskIdFromLocation();
    if (!pending) return;
    taskDeepLinkConsumed = true;
    try {
      var data = await apiFetch(
        "/calendar/tasks/" + encodeURIComponent(pending),
        { method: "GET" }
      );
      var task = data && data.task;
      var ev = (task && task.event) || {};
      if (task) {
        setTab("actual");
        openTaskModal(
          Object.assign({}, ev, {
            task_id: task.id,
            id: "task-" + task.id,
            summary: task.title || ev.summary,
            checklist: task.checklist || ev.checklist,
            assignee: {
              user_id: task.assignee_user_id,
              email: task.assignee_email,
              name: task.assignee_name,
            },
            all_day: !!task.all_day,
          })
        );
      }
    } catch (_) {}
    try {
      var u = new URL(location.href);
      u.searchParams.delete("task");
      u.searchParams.delete("t");
      if (/task=/.test(u.hash)) u.hash = "";
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    } catch (_) {}
  }

  function openPendingSharedNote(data) {
    if (noteDeepLinkConsumed) return;
    var pending = pendingNoteIdFromLocation();
    if (!pending) {
      openPendingChatThread();
      return;
    }
    noteDeepLinkConsumed = true;
    function go(note) {
      if (!note) return;
      setTab("notes");
      openNoteDetail(note, { isLocal: true });
      try {
        var u = new URL(location.href);
        u.searchParams.delete("note");
        u.searchParams.delete("n");
        if (/note=/.test(u.hash)) u.hash = "";
        history.replaceState(null, "", u.pathname + u.search + u.hash);
      } catch (_) {}
    }
    var found = ((data && data.local_notes) || []).find(function (x) {
      return String(x.id) === String(pending);
    });
    if (found) {
      go(found);
      return;
    }
    apiFetch("/notes/local/" + encodeURIComponent(pending), { method: "GET" })
      .then(function (res) {
        if (res && res.item) {
          prependLocalInCache(res.item);
          if (notesDataCache) renderNotesPanesFromData(notesDataCache);
          go(res.item);
        }
      })
      .catch(function () {});
  }

  function openPendingChatThread() {
    if (chatDeepLinkConsumed) return;
    var pending = pendingChatIdFromLocation();
    if (!pending) return;
    chatDeepLinkConsumed = true;
    function go(thread) {
      if (!thread) return;
      setTab("notes");
      setNotesSubTab("chats");
      openChatThread(thread);
      try {
        var u = new URL(location.href);
        u.searchParams.delete("chat");
        u.searchParams.delete("c");
        if (/chat=/.test(u.hash)) u.hash = "";
        history.replaceState(null, "", u.pathname + u.search + u.hash);
      } catch (_) {}
    }
    var found = (chatThreadsCache || []).find(function (t) {
      return String(t.id) === String(pending);
    });
    if (found) {
      go(found);
      return;
    }
    loadChatThreads()
      .then(function () {
        var t = (chatThreadsCache || []).find(function (x) {
          return String(x.id) === String(pending);
        });
        if (t) go(t);
      })
      .catch(function () {});
  }

  function closeNoteCardMenus() {
    document.querySelectorAll(".note-card-menu:not(.hidden)").forEach(function (el) {
      if (el.classList.contains("chat-thread-menu")) return;
      el.classList.add("hidden");
      el.style.position = "";
      el.style.top = "";
      el.style.left = "";
      el.style.right = "";
      el.style.zIndex = "";
      if (el._homeParent && el.parentNode !== el._homeParent) {
        el._homeParent.appendChild(el);
      }
    });
    document.querySelectorAll(".note-card-menu-btn[aria-expanded='true']").forEach(function (btn) {
      if (btn.classList.contains("chat-thread-more-btn")) return;
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function positionNoteCardMenu(btn, menu) {
    var r = btn.getBoundingClientRect();
    var mw = menu.offsetWidth || 184;
    var mh = menu.offsetHeight || 0;
    var left = r.right - mw;
    if (left < 8) left = 8;
    if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - mw - 8);
    var top = r.bottom + 4;
    if (mh && top + mh > window.innerHeight - 8) {
      top = Math.max(8, r.top - mh - 4);
    }
    menu.style.position = "fixed";
    menu.style.top = Math.round(top) + "px";
    menu.style.left = Math.round(left) + "px";
    menu.style.right = "auto";
    menu.style.zIndex = "240";
  }

  function noteCardShareCtx(n) {
    return {
      _shareKind: "local",
      _shareId: String(localNoteIdFrom(n) || n.id || ""),
      _shareShared: !!n.shared,
      _shareUrl: n.share_url || "",
      _shareAccess: n.share_access === "comment" ? "comment" : "view",
      _shareSheetIds: Array.isArray(n.share_sheet_ids)
        ? n.share_sheet_ids.map(function (id) { return String(id); })
        : [],
      _noteMembers: Array.isArray(n.members) ? n.members : [],
    };
  }

  function openNoteCardMenu(btn, menu) {
    var wasOpen = !menu.classList.contains("hidden");
    closeNoteCardMenus();
    if (wasOpen) return;
    if (!menu._homeParent) menu._homeParent = menu.parentNode;
    document.body.appendChild(menu);
    menu.classList.remove("hidden");
    btn.setAttribute("aria-expanded", "true");
    positionNoteCardMenu(btn, menu);
  }

  function mountNoteCardActions(card, inner, n) {
    var id = String(localNoteIdFrom(n) || "");
    if (!id) return;
    syncDiscussUnreadFromNoteRow("local", n);
    var isOwner = n.is_owner !== false;
    var actions = document.createElement("div");
    actions.className = "note-card-actions";
    actions.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
    });
    actions.addEventListener("pointerdown", function (e) {
      e.stopPropagation();
    });

    mountDiscussUnreadBadge(actions, "local", id, false);

    if (n.pinned) {
      var pinIcon = document.createElement("span");
      pinIcon.className = "note-card-pin";
      pinIcon.setAttribute("aria-label", "Закреплена");
      pinIcon.title = "Закреплена";
      pinIcon.innerHTML =
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
        '<path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.2v6h1.6v-6H18v-2c-1.66 0-3-1.34-3-3z"/>' +
        "</svg>";
      actions.appendChild(pinIcon);
    }

    if (isOwner && n.shared && n.share_url) {
      var shareWrap = document.createElement("div");
      shareWrap.className = "note-card-menu-wrap";
      var shareBtn = document.createElement("button");
      shareBtn.type = "button";
      shareBtn.className = "note-card-menu-btn note-card-share-btn";
      shareBtn.setAttribute("aria-label", "Ссылка доступа");
      shareBtn.setAttribute("aria-haspopup", "menu");
      shareBtn.setAttribute("aria-expanded", "false");
      shareBtn.innerHTML =
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>' +
        '<path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>' +
        "</svg>";
      var shareMenu = document.createElement("div");
      shareMenu.className = "note-card-menu hidden";
      shareMenu.setAttribute("role", "menu");
      function addShareItem(label, fn) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "note-card-menu-item";
        item.textContent = label;
        item.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          closeNoteCardMenus();
          listShareCtx = noteCardShareCtx(n);
          fn();
        });
        shareMenu.appendChild(item);
      }
      addShareItem("Копировать ссылку", function () {
        copyExistingShareLink();
      });
      addShareItem("Сменить доступ", function () {
        openShareAccessSheet();
      });
      addShareItem("Закрыть доступ", function () {
        revokeShareLink();
      });
      shareBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        openNoteCardMenu(shareBtn, shareMenu);
      });
      shareWrap.appendChild(shareBtn);
      shareWrap.appendChild(shareMenu);
      actions.appendChild(shareWrap);
    }

    var moreWrap = document.createElement("div");
    moreWrap.className = "note-card-menu-wrap";
    var moreBtn = document.createElement("button");
    moreBtn.type = "button";
    moreBtn.className = "note-card-menu-btn";
    moreBtn.setAttribute("aria-label", "Ещё");
    moreBtn.setAttribute("aria-haspopup", "menu");
    moreBtn.setAttribute("aria-expanded", "false");
    moreBtn.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
      '<circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/>' +
      "</svg>";
    var moreMenu = document.createElement("div");
    moreMenu.className = "note-card-menu hidden";
    moreMenu.setAttribute("role", "menu");
    function addMoreItem(label, fn, danger) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "note-card-menu-item" + (danger ? " note-card-menu-item--danger" : "");
      item.textContent = label;
      item.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        closeNoteCardMenus();
        fn();
      });
      moreMenu.appendChild(item);
    }
    addMoreItem("Поделиться", function () {
      listShareCtx = noteCardShareCtx(n);
      openShareAccessSheet();
    });
    if (isKnowledgeNoteItem(n) && isOwner) {
      addMoreItem("Поделиться с командой", function () {
        listShareCtx = noteCardShareCtx(n);
        openNoteTeamShareSheet(n);
      });
    }
    addMoreItem("Скачать PDF", function () {
      requestNotePdf("local", id).catch(function (err) {
        alert(err.message || String(err));
      });
    });
    addMoreItem(n.pinned ? "Открепить" : "Закрепить", function () {
      toggleNotePin(id, !n.pinned);
    });
    addMoreItem("Копировать", function () {
      duplicateLocalNote(id);
    });
    addMoreItem("Удалить", function () {
      deleteLocalNoteFromCard(n, id);
    }, true);
    moreBtn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openNoteCardMenu(moreBtn, moreMenu);
    });
    moreWrap.appendChild(moreBtn);
    moreWrap.appendChild(moreMenu);
    actions.appendChild(moreWrap);
    inner.appendChild(actions);
    if (n.pinned) card.classList.add("note-card--pinned");
    syncNoteCardActionsPad(inner, actions);
  }

  async function toggleNotePin(noteId, pinned) {
    function applyLocalPin() {
      if (notesDataCache && notesDataCache.local_notes) {
        notesDataCache.local_notes.forEach(function (row) {
          if (String(row.id) === String(noteId)) row.pinned = !!pinned;
        });
        notesDataCache.local_notes.sort(function (a, b) {
          return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
        });
        notesDataCache.local_notes.sort(function (a, b) {
          return (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1);
        });
        persistNotesCacheToDisk();
        renderNotesPanesFromData(notesDataCache);
      }
      if (notesDataCache && notesDataCache.transcriptions) {
        notesDataCache.transcriptions.forEach(function (row) {
          if (String(row.id) === String(noteId)) row.pinned = !!pinned;
        });
        persistNotesCacheToDisk();
        renderNotesPanesFromData(notesDataCache);
      }
      if (knowledgeDataCache && knowledgeDataCache.notes) {
        knowledgeDataCache.notes.forEach(function (row) {
          if (String(row.id) === String(noteId)) row.pinned = !!pinned;
        });
        knowledgeDataCache.notes.sort(function (a, b) {
          return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
        });
        knowledgeDataCache.notes.sort(function (a, b) {
          return (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1);
        });
        writeMiniappCache("knowledge", knowledgeDataCache);
        renderKnowledgePaneFromData(knowledgeDataCache);
      }
    }
    try {
      if (isAppOffline() || String(noteId).indexOf("tmp_") === 0) {
        applyLocalPin();
        enqueueOfflineOp({
          type: "note_pin",
          clientId: noteId,
          path: "/notes/local/" + encodeURIComponent(String(noteId)) + "/pin",
          method: "PUT",
          body: { pinned: !!pinned },
        });
        showOfflineSavedToast();
        return;
      }
      await apiFetch("/notes/local/" + encodeURIComponent(String(noteId)) + "/pin", {
        method: "PUT",
        body: JSON.stringify({ pinned: !!pinned }),
      });
      applyLocalPin();
    } catch (e) {
      if (isTransientApiError(e)) {
        applyLocalPin();
        enqueueOfflineOp({
          type: "note_pin",
          clientId: noteId,
          path: "/notes/local/" + encodeURIComponent(String(noteId)) + "/pin",
          method: "PUT",
          body: { pinned: !!pinned },
        });
        showOfflineSavedToast();
        return;
      }
      alert(e.message || String(e));
    }
  }

  async function duplicateLocalNote(noteId) {
    try {
      var res = await apiFetch(
        "/notes/local/" + encodeURIComponent(String(noteId)) + "/duplicate",
        { method: "POST" }
      );
      var item = res && res.item;
      if (item) {
        if (isKnowledgeNoteItem(item)) {
          prependKnowledgeInCache(item);
        } else {
          prependLocalInCache(item);
          if (notesDataCache) renderNotesPanesFromData(notesDataCache);
        }
      }
      showNoteToast(isKnowledgeNoteItem(item) ? "Документ скопирован" : "Заметка скопирована");
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function deleteLocalNoteFromCard(n, id) {
    var isKb = isKnowledgeNoteItem(n);
    var msg =
      n && n.is_owner === false
        ? isKb
          ? "Убрать документ из списка?"
          : "Убрать заметку из списка?"
        : isKb
          ? "Удалить документ?"
          : isTranscriptionNoteItem(n)
            ? "Удалить транскрипцию?"
            : "Удалить заметку?";
    if (!(await confirmDialog(msg))) return;
    try {
      await deleteLocalNoteById(id);
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function renderNotesPanesFromData(data) {
    renderNotesTagFilterBar();
    renderSidebarTrees();
    const pn = document.getElementById("notes-pane-notes");
    const pt = document.getElementById("notes-pane-transcriptions");
    if (!pn || !pt) return;
    pn.innerHTML = "";
    pt.innerHTML = "";

    splitTranscriptionsOutOfLocalNotes(data);
    const localAll = (data && data.local_notes) || [];
    const transIds = {};
    ((data && data.transcriptions) || []).forEach(function (n) {
      var tid = localNoteIdFrom(n);
      if (tid != null) transIds[String(tid)] = true;
    });
    const local = localAll.filter(function (n) {
      var nid = localNoteIdFrom(n);
      if (nid != null && transIds[String(nid)]) return false;
      if (looksLikeTranscriptionNote(n)) return false;
      return noteItemMatchesFilter(n, "local");
    });
    local.sort(function (a, b) {
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
    local.sort(function (a, b) {
      return (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1);
    });
    var errParts = [];
    if (data.local_error) errParts.push(String(data.local_error));
    if (errParts.length && !local.length) {
      pn.innerHTML = '<p class="error"></p>';
      pn.querySelector(".error").textContent = errParts.join(" · ");
    } else if (!local.length) {
      pn.innerHTML =
        localAll.length && (notesSearchQuery.trim() || notesActiveProjectId)
          ? '<p class="muted empty-hint">Ничего не найдено. Измените поиск, проект или #хэштег.</p>'
          : '<p class="muted empty-hint">Заметок пока нет. Нажмите «+» справа от переключателя или создайте заметку в чате с ботом.</p>';
    } else {
      function appendNoteCard(n, opts) {
        opts = opts || {};
        const isLocal = !!opts.isLocal;
        const noteId = localNoteIdFrom(n);
        const id = noteId != null ? String(noteId) : "";
        const card = document.createElement("div");
        card.className = "note-card";
        if (id) card.setAttribute("data-note-id", id);
        const inner = document.createElement("div");
        inner.className = "note-card-inner";
        const titleRaw =
          sanitizeNoteTitle(n.title || n.content || "") ||
          (n.title || n.content || "(без названия)");
        const descRaw = notePlainExcerpt(n.description || n.body || "", 280);
        const descHtml = linkifyEscaped(escapeHtml(descRaw));
        inner.innerHTML =
          '<p class="note-card-excerpt">' +
          escapeHtml(titleRaw) +
          "</p>" +
          (descRaw
            ? '<p class="note-card-excerpt note-card-excerpt--secondary muted">' +
              descHtml +
              (descRaw.length >= 280 ? "…" : "") +
              "</p>"
            : "");
        appendItemLabelChips(inner, n);
        mountNoteCardActions(card, inner, n);
        appendNoteCardAvatars(inner, n.members);
        card.appendChild(inner);
        card.addEventListener("click", function () {
          openNoteDetail(n, { isLocal: isLocal });
        });
        pn.appendChild(
          wrapWithSwipeDelete(
            card,
            async function () {
              await deleteLocalNoteById(id);
            },
            { removeStack: true, confirmMessage: n.is_owner === false ? "Убрать заметку из списка?" : "Удалить заметку?" }
          )
        );
      }
      local.forEach(function (n) {
        appendNoteCard(n, { isLocal: true });
      });
    }

    function appendTranscriptionCard(n) {
      const noteId = localNoteIdFrom(n);
      const id = noteId != null ? String(noteId) : "";
      const card = document.createElement("div");
      card.className = "note-card";
      if (id) card.setAttribute("data-note-id", id);
      const inner = document.createElement("div");
      inner.className = "note-card-inner";
      const titleRaw =
        sanitizeNoteTitle(n.title || n.content || "") ||
        (n.title || n.content || "(без названия)");
      const descRaw = notePlainExcerpt(n.preview || n.description || n.body || "", 280);
      const descHtml = linkifyEscaped(escapeHtml(descRaw));
      inner.innerHTML =
        '<p class="note-card-excerpt">' +
        escapeHtml(titleRaw) +
        "</p>" +
        (descRaw
          ? '<p class="note-card-excerpt note-card-excerpt--secondary muted">' +
            descHtml +
            (descRaw.length >= 280 ? "…" : "") +
            "</p>"
          : "");
      appendItemLabelChips(inner, n);
      mountNoteCardActions(card, inner, n);
      appendNoteCardAvatars(inner, n.members);
      card.appendChild(inner);
      card.addEventListener("click", function () {
        openNoteDetail(n, { isLocal: true });
      });
      pt.appendChild(
        wrapWithSwipeDelete(
          card,
          async function () {
            await deleteLocalNoteById(id);
            removeTranscriptionFromCache(id);
            if (notesDataCache) renderNotesPanesFromData(notesDataCache);
          },
          { removeStack: true, confirmMessage: "Удалить транскрипцию?" }
        )
      );
    }

    var transAll = (data && data.transcriptions) || [];
    var trans = transAll.filter(function (n) {
      return noteItemMatchesFilter(n, "local");
    });
    if (!transAll.length) {
      pt.innerHTML = '<p class="muted empty-hint">Транскрипций пока нет.</p>';
    } else if (!trans.length) {
      pt.innerHTML =
        '<p class="muted empty-hint">Ничего не найдено. Измените поиск, проект или #хэштег.</p>';
    } else {
      trans.forEach(appendTranscriptionCard);
    }
  }

  function formatJournalListTime(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso).slice(0, 16);
      var now = new Date();
      var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      var startThat = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      var diffDays = Math.round((startThat - startToday) / 86400000);
      var t = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      if (diffDays === 0) return "Сегодня, " + t;
      if (diffDays === -1) return "Вчера, " + t;
      return (
        d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) +
        ", " +
        t
      );
    } catch (_) {
      return "";
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function htmlToPlainText(htmlStr) {
    var raw = String(htmlStr || "")
      .replace(/<\/li>\s*<li[^>]*>/gi, "\n• ")
      .replace(/<li[^>]*>/gi, "• ")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|div|li|h[1-6]|blockquote|ul|ol)>/gi, "\n");
    var doc = new DOMParser().parseFromString("<div>" + raw + "</div>", "text/html");
    return (doc.body.textContent || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function calendarDescriptionPlain(raw) {
    var s = String(raw || "");
    if (!s) return "";
    if (!/<\/?[a-zA-Z][^>]*>/.test(s)) return s;
    return htmlToPlainText(s);
  }

  function journalHeadline(it, body, op, fallback) {
    var topic = String((it && it.main_topic) || "").trim();
    if (topic) return topic.slice(0, 160);
    var plain = htmlToPlainText(body);
    if (!plain) {
      return (fallback || operationLabel(op) || "Запись").slice(0, 160);
    }
    if (String(op || "") === "summarize") {
      var afterBrief = plain
        .replace(/^📌\s*Кратко\s*/i, "")
        .replace(/^📌\s*Краткое описание\s*/i, "")
        .replace(/^📝\s*Краткое содержание\s*/i, "")
        .trim();
      var firstPara = (afterBrief.split(/\n\n+/)[0] || afterBrief.split("\n")[0] || afterBrief).trim();
      if (firstPara) return firstPara.slice(0, 160);
    }
    return (plain.split("\n")[0] || plain).trim().slice(0, 160);
  }

  function journalCardTitle(row) {
    var op = String((row && row.operation) || "");
    if (op === "obuchat_transcribe") {
      var meeting = String((row && row.meeting_topic) || "").trim();
      var summaryTitle = relatedSummaryTitle(row);
      if (meeting && !isGenericMeetingTopic(meeting)) return meeting.slice(0, 120);
      if (summaryTitle) return summaryTitle;
      if (meeting) return meeting.slice(0, 120);
    }
    var topic = String((row && row.main_topic) || "").trim();
    if (topic) return topic.slice(0, 120);
    var prev = String((row && row.preview) || "").trim();
    if (prev) return prev.slice(0, 120);
    return operationLabel((row && row.operation) || "");
  }

  function stripJournalEmbeddedFooters(text) {
    return String(text || "")
      .replace(/<footer\b[^>]*>[\s\S]*?<\/footer>/gi, "")
      .trim();
  }

  function noteBodyHasHtml(text) {
    return /<[a-z][\s\S]*>/i.test(String(text || ""));
  }

  function looksLikeMarkdown(text) {
    var s = String(text || "");
    return (
      /(^|\n|\s)#{1,6}\s/.test(s) ||
      /\*\*[^*]+\*\*/.test(s) ||
      /(^|\n)[-*•]\s/.test(s) ||
      /~~[^~]+~~/.test(s) ||
      /(^|\n)-\s+\[[ xX]\]\s/.test(s) ||
      /(^|\n)\d+[.)]\s/.test(s) ||
      /`[^`]+`/.test(s) ||
      /(^|\n)```/.test(s) ||
      /(^|\n)\|.+\|/.test(s) ||
      /(^|\n)>\s/.test(s)
    );
  }

  function inlineMarkdown(s) {
    return String(s || "")
        .split(/(\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~|__[^_]+__|`[^`]+`)/g)
        .map(function (part) {
        if (/^\*\*[^*]+\*\*$/.test(part)) {
          return "<strong>" + escapeHtml(part.slice(2, -2)) + "</strong>";
        }
        if (/^\*[^*]+\*$/.test(part)) {
          return "<em>" + escapeHtml(part.slice(1, -1)) + "</em>";
        }
        if (/^~~[^~]+~~$/.test(part)) {
          return "<s>" + escapeHtml(part.slice(2, -2)) + "</s>";
        }
        if (/^__[^_]+__$/.test(part)) {
          return "<u>" + escapeHtml(part.slice(2, -2)) + "</u>";
        }
        if (/^`[^`]+`$/.test(part)) {
          return "<code>" + escapeHtml(part.slice(1, -1)) + "</code>";
        }
        return escapeHtml(part);
      })
      .join("");
  }

  function parseMarkdownTableRow(line) {
    var trimmed = String(line || "").trim();
    if (!/^\|.+\|$/.test(trimmed)) return null;
    if (/^\|[\s\-:|]+\|$/.test(trimmed)) return "sep";
    return trimmed
      .slice(1, -1)
      .split("|")
      .map(function (cell) {
        return cell.trim();
      });
  }

  function markdownListIndent(line) {
    var m = String(line || "").match(/^(\s*)/);
    return m ? m[1].replace(/\t/g, "    ").length : 0;
  }

  function parseMarkdownListItem(line) {
    var raw = String(line || "");
    var task = raw.match(/^(\s*)-\s+\[([ xX])\]\s+(.*)$/);
    if (task) {
      return {
        indent: task[1].replace(/\t/g, "    ").length,
        ordered: false,
        task: String(task[2]).toLowerCase() === "x",
        start: 0,
        text: task[3],
      };
    }
    var item = raw.match(/^(\s*)(?:([-*•])|(\d+)[.)])\s+(.*)$/);
    if (!item) return null;
    return {
      indent: item[1].replace(/\t/g, "    ").length,
      ordered: !item[2],
      task: null,
      start: item[3] ? parseInt(item[3], 10) : 0,
      text: item[4],
    };
  }

  function simpleMarkdownToHtml(md) {
    var source = md;
    if (
      window.NoteHtml &&
      typeof window.NoteHtml.normalizeCollapsedMarkdown === "function"
    ) {
      source = window.NoteHtml.normalizeCollapsedMarkdown(md);
    }
    var lines = String(source || "").split("\n");
    var html = [];
    var stack = [];

    function openListTag(item) {
      if (item.task != null) return '<ul class="note-task-list">';
      if (item.ordered) {
        return item.start > 1 ? '<ol start="' + item.start + '">' : "<ol>";
      }
      return "<ul>";
    }

    function closeListLevel() {
      var level = stack.pop();
      if (!level) return;
      if (level.openLi) html.push("</li>");
      html.push(level.task ? "</ul>" : level.ordered ? "</ol>" : "</ul>");
    }

    function closeAllLists() {
      while (stack.length) closeListLevel();
    }

    function shouldNestListItem(item, top) {
      if (!top || !top.openLi) return false;
      if (item.indent > top.indent) return true;
      // Модели часто пишут подпункты без отступа: 1. пункт / - подпункт / 2. пункт
      return item.indent === top.indent && top.ordered && !item.ordered;
    }

    function sameListKind(item, top) {
      return item.ordered === top.ordered && (item.task != null) === top.task;
    }

    function pushListItem(item, lineIndex) {
      while (stack.length && stack[stack.length - 1].indent > item.indent) {
        closeListLevel();
      }
      var top = stack.length ? stack[stack.length - 1] : null;
      if (shouldNestListItem(item, top)) {
        html.push(openListTag(item));
        stack.push({
          indent: item.indent,
          ordered: item.ordered,
          task: item.task != null,
          openLi: false,
        });
        top = stack[stack.length - 1];
      } else if (top && sameListKind(item, top) && item.indent === top.indent) {
        if (top.openLi) {
          html.push("</li>");
          top.openLi = false;
        }
      } else if (top) {
        closeListLevel();
        pushListItem(item, lineIndex);
        return;
      } else {
        html.push(openListTag(item));
        stack.push({
          indent: item.indent,
          ordered: item.ordered,
          task: item.task != null,
          openLi: false,
        });
        top = stack[stack.length - 1];
      }
      if (item.task != null) {
        html.push(
          '<li class="note-task' +
            (item.task ? " note-task--checked" : "") +
            '" data-line="' +
            String(lineIndex) +
            '"><input type="checkbox"' +
            (item.task ? " checked" : "") +
            ' data-line="' +
            String(lineIndex) +
            '" aria-label="Задача"><span class="note-task-text">' +
            inlineMarkdown(item.text) +
            "</span>"
        );
      } else {
        html.push("<li>" + inlineMarkdown(item.text));
      }
      top.openLi = true;
    }

    function nextNonEmptyLine(from) {
      for (var i = from; i < lines.length; i++) {
        if (String(lines[i] || "").trim()) return lines[i];
      }
      return "";
    }

    for (var lineIndex = 0; lineIndex < lines.length; lineIndex++) {
      var line = lines[lineIndex];
      var trimmed = line.trim();
      if (/^```/.test(trimmed)) {
        closeAllLists();
        var codeLines = [];
        lineIndex++;
        while (lineIndex < lines.length && !/^```/.test(lines[lineIndex].trim())) {
          codeLines.push(lines[lineIndex]);
          lineIndex++;
        }
        html.push("<pre><code>" + escapeHtml(codeLines.join("\n")) + "</code></pre>");
        continue;
      }
      if (!trimmed) {
        if (stack.length && parseMarkdownListItem(nextNonEmptyLine(lineIndex + 1))) {
          continue;
        }
        closeAllLists();
        continue;
      }
      var tableRow = parseMarkdownTableRow(trimmed);
      if (tableRow) {
        closeAllLists();
        var tableRows = [];
        while (lineIndex < lines.length) {
          var candidate = parseMarkdownTableRow(lines[lineIndex].trim());
          if (!candidate) break;
          if (candidate !== "sep") tableRows.push(candidate);
          lineIndex++;
        }
        lineIndex--;
        if (tableRows.length) {
          html.push('<table class="note-editor-table"><tbody>');
          tableRows.forEach(function (cells, rowIdx) {
            html.push("<tr>");
            cells.forEach(function (cell) {
              var tag = rowIdx === 0 ? "th" : "td";
              html.push("<" + tag + ">" + inlineMarkdown(cell) + "</" + tag + ">");
            });
            html.push("</tr>");
          });
          html.push("</tbody></table>");
        }
        continue;
      }
      if (/^>\s?/.test(trimmed)) {
        closeAllLists();
        var quoteLines = [];
        while (lineIndex < lines.length && /^>\s?/.test(lines[lineIndex].trim())) {
          quoteLines.push(lines[lineIndex].trim().replace(/^>\s?/, ""));
          lineIndex++;
        }
        lineIndex--;
        html.push("<blockquote><p>" + inlineMarkdown(quoteLines.join("\n")) + "</p></blockquote>");
        continue;
      }
      var hm = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (hm) {
        closeAllLists();
        var tag = "h" + String(Math.min(hm[1].length, 6));
        html.push("<" + tag + ">" + inlineMarkdown(hm[2]) + "</" + tag + ">");
        continue;
      }
      var listItem = parseMarkdownListItem(line);
      if (listItem) {
        pushListItem(listItem, lineIndex);
        continue;
      }
      if (stack.length && markdownListIndent(line) > 0) {
        html.push("<br>" + inlineMarkdown(trimmed));
        continue;
      }
      closeAllLists();
      html.push("<p>" + inlineMarkdown(trimmed) + "</p>");
    }
    closeAllLists();
    return html.join("");
  }

  function splitHtmlAndMarkdownTasks(body) {
    var lines = String(body || "").split("\n");
    var taskStart = -1;
    for (var i = 0; i < lines.length; i++) {
      if (/^-\s+\[[ xX]\]\s/.test(String(lines[i] || "").trim())) {
        taskStart = i;
        break;
      }
    }
    if (taskStart < 0) return { html: String(body || ""), tasks: "" };
    return {
      html: lines.slice(0, taskStart).join("\n").trim(),
      tasks: lines.slice(taskStart).join("\n").trim(),
    };
  }

  function renderRichTextInner(body, opts) {
    opts = opts || {};
    var clean = stripJournalEmbeddedFooters(sanitizeNoteBody(body));
    if (!clean) {
      return '<p class="muted small">Пустая заметка</p>';
    }
    if (looksLikeMarkdown(clean)) {
      var mdHtml =
        window.NoteHtml && window.NoteHtml.markdownToHtml
          ? window.NoteHtml.markdownToHtml(clean)
          : simpleMarkdownToHtml(clean);
      if (window.NoteHtml && window.NoteHtml.enrichForDisplay) {
        return window.NoteHtml.enrichForDisplay(mdHtml, opts);
      }
      return mdHtml;
    }
    if (noteBodyHasHtml(clean)) {
      var split = splitHtmlAndMarkdownTasks(clean);
      var inner = sanitizeSummaryHtml(split.html);
      if (split.tasks) inner += simpleMarkdownToHtml(split.tasks);
      if (window.NoteHtml && window.NoteHtml.enrichForDisplay) {
        inner = window.NoteHtml.enrichForDisplay(inner, opts);
      }
      return inner;
    }
    return escapeHtml(clean);
  }

  function wrapTablesForScroll(inner) {
    var html = String(inner || "");
    if (!/<table[\s>]/i.test(html)) return html;
    try {
      var doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
      var root = doc.body.firstChild;
      if (!root) return html;
      root.querySelectorAll("table").forEach(function (table) {
        if (table.parentElement && table.parentElement.classList.contains("note-table-scroll")) {
          return;
        }
        var wrap = doc.createElement("div");
        wrap.className = "note-table-scroll";
        table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
      });
      return root.innerHTML;
    } catch (_) {
      return html;
    }
  }

  function renderRichTextContent(body, opts) {
    var inner = wrapTablesForScroll(renderRichTextInner(body, opts));
    if (inner.indexOf("<") >= 0) {
      return '<div class="journal-summary-html note-body-html">' + inner + "</div>";
    }
    return '<div class="note-body-plain journal-transcript-pre">' + inner + "</div>";
  }

  function sanitizeSummaryHtml(htmlStr) {
    var raw = String(htmlStr || "");
    if (!raw.trim()) return "";
    var allowed = {
      b: true,
      strong: true,
      i: true,
      em: true,
      s: true,
      del: true,
      strike: true,
      u: true,
      ul: true,
      ol: true,
      li: true,
      br: true,
      p: true,
      a: true,
      h1: true,
      h2: true,
      h3: true,
      h4: true,
      h5: true,
      h6: true,
      blockquote: true,
      footer: true,
      table: true,
      thead: true,
      tbody: true,
      tr: true,
      th: true,
      td: true,
      label: true,
      input: true,
      span: true,
      code: true,
      pre: true,
    };
    function allowedClass(tag, cls) {
      if (tag === "ul" && cls === "note-task-list") return true;
      if (tag === "li" && cls) {
        return String(cls)
          .split(/\s+/)
          .every(function (part) {
            return part === "note-task" || part === "note-task--checked";
          });
      }
      if (tag === "span" && cls === "note-task-text") return true;
      return false;
    }
    function allowedAttr(tag, name, val) {
      if (tag === "input" && name === "type" && val === "checkbox") return true;
      if (tag === "input" && name === "checked") return true;
      if (tag === "input" && name === "aria-label") return true;
      if (tag === "li" && name === "checked") return true;
      if (name === "class" && allowedClass(tag, val)) return true;
      return false;
    }
    var doc = new DOMParser().parseFromString("<div>" + raw + "</div>", "text/html");
    function walk(node) {
      var out = "";
      node.childNodes.forEach(function (ch) {
        if (ch.nodeType === 3) {
          out += escapeHtml(ch.textContent || "");
          return;
        }
        if (ch.nodeType !== 1) return;
        var tag = String(ch.tagName || "").toLowerCase();
        if (!allowed[tag]) {
          out += walk(ch);
          return;
        }
        if (tag === "a") {
          var href = String(ch.getAttribute("href") || "").trim();
          if (!/^https?:\/\//i.test(href) && !/^tg:\/\//i.test(href)) {
            out += walk(ch);
            return;
          }
          out += '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">' + walk(ch) + "</a>";
          return;
        }
        if (tag === "br") {
          out += "<br>";
          return;
        }
        if (tag === "li") {
          var liAttrs = "";
          if (ch.hasAttribute("checked")) liAttrs += " checked";
          out += "<li" + liAttrs + ">" + walk(ch) + "</li>";
          return;
        }
        var attrs = "";
        if (ch.attributes) {
          for (var ai = 0; ai < ch.attributes.length; ai++) {
            var attr = ch.attributes[ai];
            if (allowedAttr(tag, attr.name, attr.value)) {
              if (attr.name === "checked") {
                if (ch.hasAttribute("checked")) attrs += " checked";
              } else {
                attrs +=
                  " " +
                  attr.name +
                  '="' +
                  escapeHtml(String(attr.value || "")) +
                  '"';
              }
            }
          }
        }
        if (tag === "input") {
          out += "<" + tag + attrs + ">";
          return;
        }
        out += "<" + tag + attrs + ">" + walk(ch) + "</" + tag + ">";
      });
      return out;
    }
    return walk(doc.body.firstChild || doc.body);
  }

  function renderJournalSourceFooter(it) {
    var parts = [];
    var fileUrl = String(it.source_url || "").trim();
    var tgUrl = String(it.telegram_link || "").trim();
    if (fileUrl) {
      parts.push(
        '<a class="journal-source-link" href="' +
          escapeHtml(fileUrl) +
          '" target="_blank" rel="noopener noreferrer">Исходный файл</a>'
      );
    }
    if (tgUrl) {
      parts.push(
        '<a class="journal-source-link" href="' +
          escapeHtml(tgUrl) +
          '" target="_blank" rel="noopener noreferrer">Сообщение в Telegram</a>'
      );
    }
    if (!parts.length) return "";
    return '<div class="journal-source-footer">' + parts.join(" · ") + "</div>";
  }

  function renderJournalDetailBody(it, op) {
    var body = stripJournalEmbeddedFooters(String(it.body || it.preview || ""));
    var isSummary = String(op || "") === "summarize";
    var inner = "";
    if (isSummary && (looksLikeMarkdown(body) || noteBodyHasHtml(body))) {
      inner = renderRichTextContent(body, { interactive: false });
    } else {
      inner = '<pre class="journal-transcript-pre">' + escapeHtml(body) + "</pre>";
    }
    inner += renderJournalSourceFooter(it);
    return '<div class="journal-transcript-card">' + inner + "</div>";
  }

  function openNewTodoistNote() {
    openNoteDetail(
      {
        id: "",
        title: "",
        content: "",
        description: "",
      },
      { create: true, isLocal: true }
    );
  }

  function openNoteDetail(n, opts) {
    return openLocalNoteDetail(resolveLocalNoteFromCache(n), opts || { isLocal: true });
  }

  function openLocalNoteDetail(n, opts) {
    opts = opts || {};
    var noteIsCreate = !!opts.create;
    var noteId = localNoteIdFrom(n);
    noteId = noteId != null ? String(noteId) : "";
    var isKnowledge = !!(opts && opts.knowledge) || isKnowledgeNoteItem(n);
    var proceed = function () {
      openLocalNoteDetailReady(n, opts, noteIsCreate, noteId);
    };
    if (noteIsCreate || !noteId) {
      proceed();
      return;
    }
    // Всегда подтягиваем свежие labels (проект/теги), иначе после PATCH без tags
    // шапка и карточка БЗ теряют проект до полной перезагрузки списка.
    apiFetch("/notes/local/" + encodeURIComponent(noteId), { method: "GET" })
      .then(function (res) {
        var fresh = res && res.item;
        if (fresh) {
          var keptTags = noteTagsOf(n);
          var keptProject = noteProjectOf(n);
          n = Object.assign({}, n, fresh);
          if (!noteTagsOf(n).length && keptTags.length) {
            n.tags = keptTags.slice();
            n.project = keptProject || keptTags[0] || null;
          }
          if (isKnowledge || isKnowledgeNoteItem(n) || isKnowledgeNoteItem(fresh)) {
            n.role = n.role || "knowledge";
            n.is_knowledge = true;
            prependKnowledgeInCache(n);
            n = resolveKnowledgeNoteFromCache(n) || n;
          } else if (notesDataCache) {
            stampTranscriptionFields(n, fresh);
            prependLocalInCache(n);
          }
        }
        openLocalNoteDetailReady(n, opts, noteIsCreate, noteId);
      })
      .catch(function () {
        proceed();
      });
  }

  function openLocalNoteDetailReady(n, opts, noteIsCreate, noteId) {
    var hydrated = noteIsCreate ? { use: "server" } : hydrateLocalNoteWithDraft(n);
    var sourceTitle = hydrated.use === "draft" ? hydrated.title : n.title || n.content || "";
    var sourceBody = hydrated.use === "draft" ? hydrated.body : n.description || n.body || "";
    const cleanTitle = sanitizeNoteTitle(sourceTitle) || String(sourceTitle || "");
    const cleanBody = sanitizeNoteBody(sourceBody);
    var isKnowledge = !!(opts && opts.knowledge) || isKnowledgeNoteItem(n);
    var editorBody = isTranscriptionNoteItem(n)
      ? prepareJournalBodyForEditor({
          body: sourceBody,
          description: sourceBody,
          preview: n && n.preview,
        })
      : cleanBody;

    const wrap = document.createElement("div");
    wrap.className = "note-editor-page";
    clearNoteEditorTagsWrap();
    var tagsWrap = document.getElementById("note-editor-tags-wrap");
    if (!noteIsCreate && noteId && tagsWrap) {
      try {
        mountTagPicker(tagsWrap, "local", noteId, noteTagsOf(n), function (tags) {
          n.tags = tags;
          n.project = tags[0] || null;
          if (isKnowledge) {
            updateItemTagsInCache("local", noteId, tags);
            if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
          }
        });
        setHidden(tagsWrap, false);
        ensureUserTagsLoaded();
      } catch (err) {
        console.error("mountTagPicker", err);
        setHidden(tagsWrap, true);
      }
      mountShareControls("local", noteId);
      var shareWrap = document.getElementById("note-editor-more-wrap");
      if (shareWrap && n) {
        shareWrap._isKnowledge = !!isKnowledge;
        if (n.revision) shareWrap._noteRevision = n.revision;
        if (n.updated_at) shareWrap._noteUpdatedAt = n.updated_at;
        if (n.members) shareWrap._noteMembers = n.members;
        if (n.is_owner != null) shareWrap._isNoteOwner = n.is_owner !== false;
      }
    }
    wrap.innerHTML = noteEditorPageInnerHtml(
      isKnowledge
        ? "Заголовок документа"
        : isTranscriptionNoteItem(n)
          ? "Заголовок"
          : "Заголовок заметки"
    );
    var tocControls = bindNoteEditorTocControls(wrap);
    var editorPad = wrap.querySelector(".note-editor-pad--body");
    var titleInput = wrap.querySelector("#note-editor-title-input");
    if (titleInput) titleInput.value = cleanTitle;

    var saveTimer = null;
    var noteRichEditor = null;
    function readFields() {
      var html = isPrimarySheetId(noteSheetState.leftId)
        ? readActiveNoteEditorHtml(getLeftNoteRichEditor())
        : noteSheetState.primaryBody;
      return {
        title: getNoteEditorTitle().trim(),
        description: html,
      };
    }
    function ensureNoteShareMounted(id) {
      if (!id) return;
      var wrap = document.getElementById("note-editor-more-wrap");
      if (wrap && wrap._shareKind === "local" && String(wrap._shareId) === String(id)) {
        ensureNotePaieThread();
        return wrap;
      }
      mountShareControls("local", id);
      return document.getElementById("note-editor-more-wrap");
    }

    async function persistNoteDraft(opts) {
      opts = opts || {};
      if (noteCollabState.applying && !opts.force) return;
      var fields = opts.snapshot || readFields();
      var title = String(fields.title || "").trim();
      if (!title) {
        if (!opts.defaultTitle) return;
        title = String(opts.defaultTitle).trim();
        if (!title) return;
        if (titleInput) titleInput.value = title;
        fields.title = title;
      }
      if (
        !noteIsCreate &&
        noteId &&
        isTrivialNoteHtml(fields.description) &&
        !opts.keepEmpty &&
        !title
      ) {
        return;
      }
      var persistWrap = document.getElementById("note-editor-more-wrap");
      if (noteId) {
        writeLocalNoteDraft(noteId, {
          title: fields.title,
          body: fields.description,
          baseRevision: persistWrap && persistWrap._noteRevision,
          baseUpdatedAt: persistWrap && persistWrap._noteUpdatedAt,
        });
      }
      if (noteIsCreate || !noteId) {
        var createBody = {
          title: fields.title,
          description: fields.description,
          sync_todoist: false,
        };
        var createPath = isKnowledge ? "/notes/knowledge" : "/notes/local";
        function applyCreatedLocal(createdLocal) {
          if (isKnowledge) {
            createdLocal.role = createdLocal.role || "knowledge";
            createdLocal.is_knowledge = true;
          }
          noteId = String(createdLocal.id || "");
          noteIsCreate = false;
          n.id = noteId;
          n.tags = createdLocal.tags || n.tags;
          n.project = createdLocal.project || n.project;
          n.hashtags = createdLocal.hashtags || n.hashtags;
          if (isKnowledge) {
            prependKnowledgeInCache(createdLocal);
          } else {
            prependLocalInCache(createdLocal);
            if (notesDataCache) renderNotesPanesFromData(notesDataCache);
          }
          if (noteId && tagsWrap && tagsWrap.classList.contains("hidden")) {
            try {
              mountTagPicker(tagsWrap, "local", noteId, noteTagsOf(n), function (tags) {
                n.tags = tags;
                n.project = tags[0] || null;
                if (isKnowledge) {
                  updateItemTagsInCache("local", noteId, tags);
                  if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
                }
              });
              setHidden(tagsWrap, false);
              ensureUserTagsLoaded();
            } catch (err) {
              console.error("mountTagPicker", err);
            }
          }
          if (noteId) {
            var createdShareWrap = ensureNoteShareMounted(noteId);
            if (createdShareWrap) createdShareWrap._isKnowledge = !!isKnowledge;
            if (!isKnowledge) {
              hydrateNoteSheets(
                Object.assign({}, n, createdLocal),
                noteSheetState.primaryBody || fields.description
              );
            }
          }
          if (noteId) {
            writeLocalNoteDraft(noteId, {
              title: fields.title,
              body: fields.description,
              baseRevision: createdLocal.revision,
              baseUpdatedAt: createdLocal.updated_at,
            });
          }
        }
        if (isAppOffline()) {
          var tmpNoteId = newTempId("tmp_note_");
          applyCreatedLocal({
            id: tmpNoteId,
            title: fields.title,
            description: fields.description,
            body: fields.description,
          });
          enqueueOfflineOp({
            type: "note_create",
            clientId: tmpNoteId,
            path: createPath,
            method: "POST",
            body: createBody,
          });
          showOfflineSavedToast();
          return;
        }
        try {
          var createRes = await apiFetch(createPath, {
            method: "POST",
            body: JSON.stringify(createBody),
          });
          var created =
            (createRes && createRes.item) ||
            { id: createRes.id, title: fields.title, description: fields.description };
          applyCreatedLocal(created);
        } catch (createErr) {
          if (!isTransientApiError(createErr)) throw createErr;
          var tmpNoteId2 = newTempId("tmp_note_");
          applyCreatedLocal({
            id: tmpNoteId2,
            title: fields.title,
            description: fields.description,
            body: fields.description,
          });
          enqueueOfflineOp({
            type: "note_create",
            clientId: tmpNoteId2,
            path: createPath,
            method: "POST",
            body: createBody,
          });
          showOfflineSavedToast();
          return;
        }
      } else {
        var moreWrap = document.getElementById("note-editor-more-wrap");
        var patchBody = {
          title: fields.title,
          description: fields.description,
        };
        if (moreWrap && moreWrap._noteRevision) {
          patchBody.expected_revision = moreWrap._noteRevision;
        }
        function applyPatchedLocal(patchedItem) {
          if (isKnowledge) {
            patchedItem.role = "knowledge";
            patchedItem.is_knowledge = true;
            var localTags = noteTagsOf(n);
            if (!noteTagsOf(patchedItem).length && localTags.length) {
              patchedItem.tags = localTags.slice();
              patchedItem.project = localTags[0];
            }
            prependKnowledgeInCache(patchedItem);
          } else {
            stampTranscriptionFields(patchedItem, n);
            var localTags2 = noteTagsOf(n);
            if (!noteTagsOf(patchedItem).length && localTags2.length) {
              patchedItem.tags = localTags2.slice();
              patchedItem.project = localTags2[0];
            }
            syncLocalNoteInList(patchedItem);
          }
        }
        var offlinePatch =
          isAppOffline() || String(noteId).indexOf("tmp_") === 0;
        if (offlinePatch) {
          var localPatch = {
            id: noteId,
            title: fields.title,
            description: fields.description,
            body: fields.description,
            todoist_id: n.todoist_id,
            tags: noteTagsOf(n),
            project: noteProjectOf(n),
            hashtags: noteHashtagsOf(n),
            members: moreWrap && moreWrap._noteMembers,
            revision: moreWrap && moreWrap._noteRevision,
            updated_at: moreWrap && moreWrap._noteUpdatedAt,
            is_owner: moreWrap ? moreWrap._isNoteOwner : n.is_owner,
            owner_user_id: n.owner_user_id,
            role: n.role,
            is_transcription: n.is_transcription,
            is_knowledge: n.is_knowledge,
            has_summary: n.has_summary,
            primary_sheet_title: n.primary_sheet_title,
            meta: n.meta,
          };
          applyPatchedLocal(localPatch);
          enqueueOfflineOp({
            type: "note_patch",
            clientId: noteId,
            path: "/notes/local/" + encodeURIComponent(noteId),
            method: "PATCH",
            body: notePatchQueueBody(fields, moreWrap),
          });
          showOfflineSavedToast();
          return;
        }
        try {
          var keptTagsBeforePatch = noteTagsOf(n);
          var keptProjectBeforePatch = noteProjectOf(n);
          var patchRes = await apiFetch("/notes/local/" + encodeURIComponent(noteId), {
            method: "PATCH",
            body: JSON.stringify(patchBody),
          });
          var patchedItem = (patchRes && patchRes.item) || {
            id: noteId,
            title: fields.title,
            description: fields.description,
            body: fields.description,
            todoist_id: n.todoist_id,
            tags: keptTagsBeforePatch,
            project: keptProjectBeforePatch,
            hashtags: noteHashtagsOf(n),
            members: moreWrap && moreWrap._noteMembers,
            revision: moreWrap && moreWrap._noteRevision,
            updated_at: moreWrap && moreWrap._noteUpdatedAt,
            is_owner: moreWrap ? moreWrap._isNoteOwner : n.is_owner,
            owner_user_id: n.owner_user_id,
          };
          if (patchRes && patchRes.item) {
            n = Object.assign(n, patchRes.item);
            if (!noteTagsOf(n).length && keptTagsBeforePatch.length) {
              n.tags = keptTagsBeforePatch.slice();
              n.project = keptProjectBeforePatch || keptTagsBeforePatch[0] || null;
            }
            if (moreWrap) {
              moreWrap._noteRevision = patchRes.item.revision || moreWrap._noteRevision;
              moreWrap._noteUpdatedAt = patchRes.item.updated_at || moreWrap._noteUpdatedAt;
              moreWrap._noteMembers = patchRes.item.members || moreWrap._noteMembers;
            }
          }
          if (!noteTagsOf(patchedItem).length && keptTagsBeforePatch.length) {
            patchedItem.tags = keptTagsBeforePatch.slice();
            patchedItem.project = keptProjectBeforePatch || keptTagsBeforePatch[0] || null;
          }
          applyPatchedLocal(patchedItem);
        } catch (patchErr) {
          if (patchErr && patchErr.status === 409 && patchErr.conflictItem) {
            var remote = patchErr.conflictItem;
            var remoteBody = remote.body || remote.description;
            if (notePayloadEquals(fields.title, fields.description, remote.title, remoteBody)) {
              adoptNoteRevision(remote, noteId);
              applyPatchedLocal(remote);
              clearLocalNoteDraftIfUnchanged(noteId, fields.title, fields.description);
              return;
            }
            var baseline = noteEditorBaseline();
            if (
              !opts._conflictRetry &&
              baseline &&
              notePayloadEquals(baseline.title, baseline.description, remote.title, remoteBody)
            ) {
              adoptNoteRevision(remote, noteId);
              return persistNoteDraft(
                Object.assign({}, opts, { _conflictRetry: true, snapshot: fields, force: true })
              );
            }
            handleNotePatchConflict(noteId, remote, fields.title, fields.description);
            return;
          }
          if (!isTransientApiError(patchErr)) throw patchErr;
          applyPatchedLocal({
            id: noteId,
            title: fields.title,
            description: fields.description,
            body: fields.description,
            todoist_id: n.todoist_id,
            tags: noteTagsOf(n),
            project: noteProjectOf(n),
            hashtags: noteHashtagsOf(n),
          });
          enqueueOfflineOp({
            type: "note_patch",
            clientId: noteId,
            path: "/notes/local/" + encodeURIComponent(noteId),
            method: "PATCH",
            body: notePatchQueueBody(fields, moreWrap),
          });
          showOfflineSavedToast();
        }
      }
      if (noteId) {
        var savedWrap = document.getElementById("note-editor-more-wrap");
        var keepDraft = readLocalNoteDraft(noteId);
        if (keepDraft && savedWrap) {
          writeLocalNoteDraft(noteId, {
            title: keepDraft.title,
            body: keepDraft.body,
            baseRevision: savedWrap._noteRevision,
            baseUpdatedAt: savedWrap._noteUpdatedAt,
            conflict: null,
          });
        }
        clearLocalNoteDraftIfUnchanged(noteId, fields.title, fields.description);
      }
      var detailBodyDraft = getNoteEditorBodyEl();
      if (detailBodyDraft && detailBodyDraft._noteEditor) {
        detailBodyDraft._noteEditor.baseline = noteEditorSnapshot(
          fields.title,
          fields.description
        );
      }
      setNoteEditorSaveHint("");
      if (!opts.snapshot) previewOpenNoteInList();
    }

    async function openNoteGptComposer() {
      try {
        await persistNoteDraft({ defaultTitle: isKnowledge ? "Новый документ" : "Новый чат", keepEmpty: true });
      } catch (e) {
        alert(e.message || String(e));
        return;
      }
      if (!noteId) return;
      ensureNoteShareMounted(noteId);
      openNoteDiscussion({ mode: "gpt", focus: true });
    }

    function previewOpenNoteInList() {
      if (isKnowledge || !noteId) return;
      var fields = readFields();
      var excerpt = fields.description;
      if (isTrivialNoteHtml(excerpt)) {
        extraNoteSheets().some(function (s) {
          var html = s.body || s.description || "";
          if (String(noteSheetState.leftId) === String(s.id)) {
            html = readSheetEditorHtml("left") || html;
          } else if (String(noteSheetState.rightId) === String(s.id)) {
            html = readSheetEditorHtml("right") || html;
          }
          if (!isTrivialNoteHtml(html)) {
            excerpt = html;
            return true;
          }
          return false;
        });
      }
      var moreWrap = document.getElementById("note-editor-more-wrap");
      syncLocalNoteInList({
        id: noteId,
        title: fields.title || n.title,
        description: excerpt,
        body: excerpt,
        todoist_id: n.todoist_id,
        tags: noteTagsOf(n),
        project: noteProjectOf(n),
        hashtags: noteHashtagsOf(n),
        members: (moreWrap && moreWrap._noteMembers) || n.members,
        revision: (moreWrap && moreWrap._noteRevision) || n.revision,
        updated_at: new Date().toISOString(),
        is_owner: moreWrap ? moreWrap._isNoteOwner : n.is_owner,
        owner_user_id: n.owner_user_id,
        pinned: n.pinned,
        shared: n.shared,
        share_url: n.share_url,
        share_access: n.share_access,
      });
    }

    function schedulePatch() {
      if (noteCollabState.applying) return;
      noteCollabState.lastTypedAt = Date.now();
      previewOpenNoteInList();
      if (noteId) {
        var fieldsNow = readFields();
        var wrapNow = document.getElementById("note-editor-more-wrap");
        writeLocalNoteDraft(noteId, {
          title: fieldsNow.title,
          body: fieldsNow.description,
          baseRevision: wrapNow && wrapNow._noteRevision,
          baseUpdatedAt: wrapNow && wrapNow._noteUpdatedAt,
        });
      }
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        persistNoteDraft()
          .then(function () {
            setNoteEditorSaveHint("");
          })
          .catch(function (e) {
            setNoteEditorSaveHint(e.message || "Не удалось сохранить заметку");
          });
      }, 1500);
    }

    async function runSave() {
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      try {
        await persistNoteDraft({ defaultTitle: "Без названия", keepEmpty: true });
      } catch (e) {
        alert(e.message || String(e));
        return false;
      }
      var fields = readFields();
      if (!fields.title) {
        alert(isKnowledge ? "Введите заголовок документа" : "Введите заголовок заметки");
        var titleEl = document.getElementById("note-editor-title-input");
        if (titleEl) titleEl.focus();
        return false;
      }
      var detailBodySave = getNoteEditorBodyEl();
      if (detailBodySave) detailBodySave._noteEditorFlush = null;
      closeNoteEditorModal();
      return true;
    }

    var modalBody = getNoteEditorBodyEl();
    if (modalBody) {
      modalBody.innerHTML = "";
      modalBody.appendChild(wrap);
      syncNoteCommentsPanel();
      modalBody._notePreviewList = previewOpenNoteInList;
      modalBody._noteEditorFlush = async function () {
        if (saveTimer) {
          clearTimeout(saveTimer);
          saveTimer = null;
        }
        var snapshot = {
          title: getNoteEditorTitle().trim(),
          description: isPrimarySheetId(noteSheetState.leftId)
            ? readActiveNoteEditorHtml(getLeftNoteRichEditor())
            : noteSheetState.primaryBody,
        };
        if (snapshot.description) noteSheetState.primaryBody = snapshot.description;
        await flushSheetEditor("left");
        await flushSheetEditor("right");
        await persistNoteDraft({
          defaultTitle: "Без названия",
          keepEmpty: true,
          snapshot: snapshot,
        });
      };
      modalBody._noteEditorSchedule = schedulePatch;
      modalBody._noteEditor = {
        ready: false,
        baseline: noteEditorSnapshot(cleanTitle, editorBody),
        getCurrent: function () {
          return noteEditorSnapshot(
            getNoteEditorTitle(),
            isPrimarySheetId(noteSheetState.leftId)
              ? readActiveNoteEditorHtml(getLeftNoteRichEditor())
              : noteSheetState.primaryBody
          );
        },
        runSave: runSave,
      };
      modalBody._openNoteGpt = openNoteGptComposer;
    }
    bindNoteGptToolbarButton();
    syncNoteGptToolbarButton(true);
    openNoteEditorModal(
      noteIsCreate
        ? isKnowledge
          ? "Новый документ"
          : "Новая заметка"
        : isKnowledge
          ? "Документ"
          : isTranscriptionNoteItem(n)
            ? "Транскрипция"
            : "Заметка"
    );
    var generatingSummary =
      isTranscriptionNoteItem(n) &&
      transcriptionIsGenerating(n) &&
      isPrimarySheetId(noteSheetState.leftId);
    if (generatingSummary && editorPad) {
      editorPad.innerHTML = noteSheetSkeletonHtml();
      pollTranscriptionSummary(noteId);
    } else {
    mountNoteRichEditor(
      editorPad,
      editorBody,
      function () {
        if (isPrimarySheetId(noteSheetState.leftId)) schedulePatch();
        else scheduleExtraSheetPersist(noteSheetState.leftId);
      },
      {
      tocParent: tocControls.tocParent,
      onTocNavigate: tocControls.close,
      scrollParent: document.querySelector("#note-editor-overlay .note-editor-modal-body"),
      extraTocItems: function (tocEl) {
        appendNoteDiscussionToc(tocEl, tocControls.close);
      },
      onSelection: function () {
        noteCollabState.lastTypedAt = Date.now();
      },
    }).then(function (editor) {
      noteRichEditor = editor;
      var detailBodyMount = getNoteEditorBodyEl();
      if (detailBodyMount) detailBodyMount._noteRichEditor = editor;
      if (detailBodyMount && detailBodyMount._noteEditor) {
        detailBodyMount._noteEditor.ready = true;
        detailBodyMount._noteEditor.baseline = noteEditorSnapshot(
          getNoteEditorTitle(),
          readActiveNoteEditorHtml(getLeftNoteRichEditor())
        );
      }
      if (editorPad) {
        editorPad.addEventListener(
          "focusin",
          function () {
            setNoteSheetFocus("left");
          },
          true
        );
      }
      refreshNoteCommentAnchors();
      if (
        !noteIsCreate &&
        noteId &&
        (!isKnowledge || (n.members && n.members.length > 1))
      ) {
        startNoteCollab(noteId, n);
      }
    });
    }
    hydrateNoteSheets(isKnowledge ? null : n, editorBody);
    bindNoteTitleAutoresize(titleInput);
    if (titleInput) titleInput.addEventListener("input", schedulePatch);
    if (!noteIsCreate && noteId) consumePendingDiscussOpen("local", noteId);
  }

  async function openJournalEditorDetail(row) {
    row = resolveJournalFromCache(row);
    var journalId = String((row && row.id) || "").trim();
    if (!journalId) return;
    var op = row.operation;
    try {
      var data = await apiFetch("/notes/journal/" + encodeURIComponent(journalId), {
        method: "GET",
      });
      openJournalEditorDetailWithItem(row, data.item || {}, op || (data.item && data.item.operation));
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  function openJournalEditorDetailWithItem(row, it, op) {
    resetNoteSheetState("");
    var journalId = String((row && row.id) || (it && it.id) || "").trim();
    if (!journalId) return;
    const cleanTitle = journalTitleForEditor(it, row);
    const cleanBody = prepareJournalBodyForEditor(it);

    const wrap = document.createElement("div");
    wrap.className = "note-editor-page";
    clearNoteEditorTagsWrap();
    var tagsWrap = document.getElementById("note-editor-tags-wrap");
    if (journalId && tagsWrap) {
      try {
        mountTagPicker(tagsWrap, "journal", journalId, noteTagsOf(it), function (tags) {
          it.tags = tags;
          it.project = tags[0] || null;
          row.tags = tags;
          row.project = tags[0] || null;
        });
        setHidden(tagsWrap, false);
      } catch (err) {
        console.error("mountTagPicker", err);
        setHidden(tagsWrap, true);
      }
      mountShareControls("journal", journalId);
    }
    wrap.innerHTML = noteEditorPageInnerHtml("Заголовок");
    syncNoteGptToolbarButton(true);
    bindNoteGptToolbarButton();
    var tocControls = bindNoteEditorTocControls(wrap);
    var editorPad = wrap.querySelector(".note-editor-pad--body");
    var titleInput = wrap.querySelector("#note-editor-title-input");
    if (titleInput) titleInput.value = cleanTitle;
    appendJournalRelatedLinks(wrap, it, op, row);

    var saveTimer = null;
    function readFields() {
      return {
        title: getNoteEditorTitle().trim(),
        description: readActiveNoteEditorHtml(getLeftNoteRichEditor()),
      };
    }
    async function persistJournalDraft() {
      var fields = readFields();
      var title = fields.title || journalCardTitle({ main_topic: fields.title, preview: fields.description, operation: op });
      var patchRes = await apiFetch("/notes/journal/" + encodeURIComponent(journalId), {
        method: "PATCH",
        body: JSON.stringify({
          title: title,
          description: fields.description,
        }),
      });
      var updated = (patchRes && patchRes.item) || {
        id: journalId,
        main_topic: title,
        preview: notePlainExcerpt(fields.description, 400),
        tags: noteTagsOf(it),
        project: noteProjectOf(it),
        hashtags: noteHashtagsOf(it),
      };
      it.tags = updated.tags || it.tags;
      it.project = updated.project || it.project;
      it.hashtags = updated.hashtags || it.hashtags;
      updateJournalInCache(journalId, op, updated);
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      var detailBodyDraft = getNoteEditorBodyEl();
      if (detailBodyDraft && detailBodyDraft._noteEditor) {
        detailBodyDraft._noteEditor.baseline = noteEditorSnapshot(title, fields.description);
      }
    }
    function schedulePatch() {
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        persistJournalDraft()
          .then(function () {
            setNoteEditorSaveHint("");
          })
          .catch(function (e) {
            setNoteEditorSaveHint(e.message || "Не удалось сохранить запись");
          });
      }, 1500);
    }

    var modalBody = getNoteEditorBodyEl();
    if (modalBody) {
      modalBody.innerHTML = "";
      modalBody.appendChild(wrap);
      syncNoteCommentsPanel();
      modalBody._openNoteGpt = async function () {
        try {
          await persistJournalDraft();
        } catch (e) {
          alert(e.message || String(e));
          return;
        }
        openNoteDiscussion({ mode: "gpt", focus: true });
      };
      modalBody._noteEditorFlush = async function () {
        if (saveTimer) {
          clearTimeout(saveTimer);
          saveTimer = null;
        }
        await persistJournalDraft();
      };
      modalBody._noteEditor = {
        ready: false,
        baseline: noteEditorSnapshot(cleanTitle, cleanBody),
        getCurrent: function () {
          return noteEditorSnapshot(
            getNoteEditorTitle(),
            readActiveNoteEditorHtml(getLeftNoteRichEditor())
          );
        },
        runSave: async function () {
          if (saveTimer) {
            clearTimeout(saveTimer);
            saveTimer = null;
          }
          try {
            await persistJournalDraft();
          } catch (e) {
            alert(e.message || String(e));
            return false;
          }
          modalBody._noteEditorFlush = null;
          closeNoteEditorModal();
          return true;
        },
      };
    }
    openNoteEditorModal(journalEditorModalTitle(op));
    mountNoteRichEditor(editorPad, cleanBody, schedulePatch, {
      tocParent: tocControls.tocParent,
      onTocNavigate: tocControls.close,
      scrollParent: document.querySelector("#note-editor-overlay .note-editor-modal-body"),
      extraTocItems: function (tocEl) {
        appendNoteDiscussionToc(tocEl, tocControls.close);
      },
    }).then(function (editor) {
      var detailBodyMount = getNoteEditorBodyEl();
      if (detailBodyMount) detailBodyMount._noteRichEditor = editor;
      if (detailBodyMount && detailBodyMount._noteEditor) {
        detailBodyMount._noteEditor.ready = true;
        detailBodyMount._noteEditor.baseline = noteEditorSnapshot(
          getNoteEditorTitle(),
          readActiveNoteEditorHtml()
        );
      }
      refreshNoteCommentAnchors();
    });
    bindNoteTitleAutoresize(titleInput);
    if (titleInput) titleInput.addEventListener("input", schedulePatch);
    if (journalId) consumePendingDiscussOpen("journal", journalId);
  }

  function openTodoistDetail(n, opts) {
    opts = opts || {};
    var isCreate = !!opts.create;
    const id = String(n.id || "").trim();
    const wrap = document.createElement("div");
    wrap.className = "note-detail-stack";
    var nowLine =
      "Сегодня, " +
      new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    wrap.innerHTML =
      '<p class="note-detail-time muted small">' +
      escapeHtml(nowLine) +
      "</p>" +
      '<div class="note-fabric-card">' +
      '<textarea id="td-title" class="note-fabric-input note-fabric-input--title" rows="2" spellcheck="true" placeholder="Заголовок"></textarea>' +
      '<textarea id="td-desc" class="note-fabric-input note-fabric-input--body" rows="8" spellcheck="true" placeholder="Текст заметки…"></textarea>' +
      "</div>" +
      '<button type="button" class="btn-todoist-sync" id="td-push">' +
      '<span class="btn-todoist-sync-ic" aria-hidden="true">✈</span>' +
      (isCreate ? "Создать в Todoist" : "Отправить в Todoist") +
      "</button>";

    wrap.querySelector("#td-title").value = n.title || n.content || "";
    wrap.querySelector("#td-desc").value = n.description || "";

    var saveTimer = null;
    function schedulePatch() {
      if (isCreate || !id) return;
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(async function () {
        try {
          var title = wrap.querySelector("#td-title").value;
          var description = wrap.querySelector("#td-desc").value;
          await apiFetch("/notes/todoist/" + encodeURIComponent(id), {
            method: "PATCH",
            body: JSON.stringify({ title: title, description: description }),
          });
          mergeTodoInCache(id, title, description);
          setNoteEditorSaveHint("");
        } catch (e) {
          setNoteEditorSaveHint(e.message || "Не удалось сохранить заметку");
        }
      }, 1500);
    }
    if (!isCreate) {
      wrap.querySelector("#td-title").addEventListener("input", schedulePatch);
      wrap.querySelector("#td-desc").addEventListener("input", schedulePatch);
    }

    wrap.querySelector("#td-push").addEventListener("click", async function () {
      if (saveTimer) clearTimeout(saveTimer);
      var title = (wrap.querySelector("#td-title").value || "").trim();
      var description = (wrap.querySelector("#td-desc").value || "").trim();
      if (!title) {
        alert("Введите заголовок заметки");
        wrap.querySelector("#td-title").focus();
        return;
      }
      try {
        if (isCreate || !id) {
          var res = await apiFetch("/notes/todoist", {
            method: "POST",
            body: JSON.stringify({ title: title, description: description }),
          });
          var newId = String((res && res.id) || "").trim();
          var item = (res && res.item) || {
            id: newId,
            title: title,
            content: title,
            description: description,
          };
          prependTodoInCache(item);
          renderNotesPanesFromData(notesDataCache);
          var tg = window.Telegram && window.Telegram.WebApp;
          if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
          closeNotesDetail();
          openTodoistDetail(item);
          return;
        }
        await apiFetch("/notes/todoist/" + encodeURIComponent(id), {
          method: "PATCH",
          body: JSON.stringify({ title: title, description: description }),
        });
        mergeTodoInCache(id, title, description);
        var tg2 = window.Telegram && window.Telegram.WebApp;
        if (tg2 && tg2.HapticFeedback) tg2.HapticFeedback.notificationOccurred("success");
        if (tg2 && tg2.showAlert) tg2.showAlert("Сохранено в Todoist.");
        else alert("Сохранено в Todoist.");
      } catch (e) {
        alert(e.message || String(e));
      }
    });

    var foot = document.createElement("button");
    foot.type = "button";
    foot.className = "btn join note-detail-gpt-foot";
    foot.innerHTML =
      '<span class="gpt-foot-icon" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="8" width="16" height="10" rx="2"/><circle cx="9" cy="13" r="1.2" fill="currentColor" stroke="none"/></svg></span>Обсудить с GPT';
    foot.addEventListener("click", function () {
      var t = (wrap.querySelector("#td-title").value || "").trim();
      var d = (wrap.querySelector("#td-desc").value || "").trim();
      openGptChat("Заметка", (t + (d ? "\n\n" + d : "")).trim());
    });

    async function runDelete() {
      if (!confirm("Удалить заметку в Todoist?")) return;
      try {
        await apiFetch("/notes/todoist/" + encodeURIComponent(id), { method: "DELETE" });
        notesDataCache = null;
        closeNotesDetail();
        await loadNotes();
      } catch (e) {
        alert(e.message || String(e));
      }
    }

    openNotesDetail(isCreate ? "Новая заметка" : "Заметка", wrap, foot);
    var del = document.getElementById("notes-detail-del");
    if (del) {
      if (isCreate || !id) {
        del.onclick = null;
        setHidden(del, true);
      } else {
        setHidden(del, false);
        del.onclick = function () {
          runDelete();
        };
      }
    }
    setTimeout(function () {
      wrap.querySelector("#td-title").focus();
    }, 80);
  }

  function setGateError(msg) {
    var el = document.getElementById("gate-login-err");
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.classList.remove("hidden");
    } else {
      el.textContent = "";
      el.classList.add("hidden");
    }
  }

  function setGateStatus(msg) {
    var el = document.getElementById("gate-login-status");
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.classList.remove("hidden");
    } else {
      el.textContent = "";
      el.classList.add("hidden");
    }
  }

  function ensureTelegramLoginJs() {
    return new Promise(function (resolve, reject) {
      if (window.Telegram && window.Telegram.Login && window.Telegram.Login.auth) {
        resolve();
        return;
      }
      var existing = document.getElementById("tg-login-widget-js");
      if (existing) {
        existing.addEventListener("load", function () {
          resolve();
        });
        existing.addEventListener("error", function () {
          reject(new Error("Не удалось загрузить скрипт Telegram Login"));
        });
        return;
      }
      var s = document.createElement("script");
      s.id = "tg-login-widget-js";
      s.async = true;
      s.src = "/webapp/telegram-widget.js?v=" + WEBAPP_BUILD;
      s.onload = function () {
        resolve();
      };
      s.onerror = function () {
        reject(new Error("Не удалось загрузить скрипт Telegram Login"));
      };
      document.head.appendChild(s);
    });
  }

  function parseTgAuthResultFromHash() {
    var hash = location.hash || "";
    var m = hash.match(/tgAuthResult=([A-Za-z0-9\-_=]+)/);
    if (!m) return null;
    try {
      var data = m[1].replace(/-/g, "+").replace(/_/g, "/");
      var pad = data.length % 4;
      if (pad > 1) data += "====".slice(pad);
      return JSON.parse(atob(data));
    } catch (_) {
      return null;
    }
  }

  function clearTgAuthHash() {
    if (!location.hash) return;
    var h = location.hash.replace(/[#&?]tgAuthResult=[^&]*/g, "").replace(/^#&/, "#");
    if (h === "#") h = "";
    history.replaceState(null, "", location.pathname + location.search + h);
  }

  function startTelegramOAuth(botId) {
    // Telegram возвращает данные в #tgAuthResult на return_to (не в query API callback).
    var returnTo = window.location.origin + "/webapp/";
    window.location.href =
      "https://oauth.telegram.org/auth?bot_id=" +
      encodeURIComponent(String(botId)) +
      "&origin=" +
      encodeURIComponent(window.location.origin) +
      "&return_to=" +
      encodeURIComponent(returnTo);
  }

  function renderTelegramLoginButton(botId) {
    var box = document.getElementById("gate-telegram-login");
    if (!box || !botId) return;
    box.innerHTML = "";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn gate-login-btn";
    btn.textContent = "Войти через Telegram";
    btn.addEventListener("click", function () {
      setGateError("");
      startTelegramOAuth(botId);
    });
    box.appendChild(btn);
  }

  function completeTelegramLogin(user) {
    setGateError("");
    setGateStatus("Завершение входа…");
    return fetch(API + "/auth/telegram", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(user),
    }).then(function (res) {
      return res.text().then(function (text) {
        var body = null;
        try {
          body = text ? JSON.parse(text) : null;
        } catch (_) {
          body = {
            detail: looksLikeHtmlError(text)
              ? httpStatusFallback(res.status, "Ошибка входа")
              : text || res.statusText,
          };
        }
        if (!res.ok) {
          var msg = apiErrorMessage(
            body,
            httpStatusFallback(res.status, "Ошибка входа")
          );
          throw new Error(msg);
        }
        return body;
      });
    }).then(function (data) {
      if (!data || !data.session_token) {
        throw new Error("Нет токена сессии");
      }
      setStoredSession(data.session_token);
      setGateStatus("");
      if (!bootAppStarted) {
        bootAppStarted = true;
      }
      var tg = window.Telegram && window.Telegram.WebApp;
      bootApp(tg);
    });
  }

  window.onTelegramAuth = function (user) {
    if (!user || !user.hash) {
      setGateError(
        "Вход не завершён. Проверьте /setdomain в @BotFather: " + location.hostname
      );
      return;
    }
    completeTelegramLogin(user).catch(function (e) {
      setGateStatus("");
      setGateError(e.message || String(e));
    });
  };

  function tryExistingBrowserSession() {
    return apiFetch("/me", { method: "GET" })
      .then(function (me) {
        cachedMe = me;
        if (me.bot_username) cachedBotUsername = me.bot_username;
        setSessionHint(true);
        persistBrowserSessionFromCurrentAuth();
        return true;
      })
      .catch(function (e) {
        if (isTransientApiError(e)) return "offline";
        return false;
      });
  }

  function persistBrowserSessionFromCurrentAuth() {
    if (getStoredSession() || miniappDev) return;
    apiFetch("/auth/session", { method: "POST" })
      .then(function (data) {
        if (data && data.session_token) {
          setStoredSession(data.session_token);
        }
      })
      .catch(function () {});
  }

  function bootstrapGateLogin() {
    if (gateLoginBootstrapped) return;
    gateLoginBootstrapped = true;
    setGateError("");
    setGateStatus("Загрузка…");
    var box = document.getElementById("gate-telegram-login");
    if (box) {
      box.innerHTML =
        '<p class="muted small" style="margin:0">Подключение кнопки входа…</p>';
    }
    var devHint = document.getElementById("gate-dev-hint");
    if (devHint) {
      var showDev =
        !hasTelegramWebAppAuth() &&
        (miniappDev || /[?&]dev=1(?:&|$)/.test(window.location.search || ""));
      devHint.classList.toggle("hidden", !showDev);
    }
    apiFetch("/auth/config", { method: "GET" })
      .then(function (cfg) {
        setGateStatus("");
        var u = (cfg && cfg.bot_username) || "";
        if (u) {
          cachedBotUsername = u.replace(/^@/, "");
          var link = document.getElementById("gate-bot-link");
          if (link) {
            link.innerHTML =
              '<a href="https://t.me/' +
              encodeURIComponent(cachedBotUsername) +
              '" target="_blank" rel="noopener noreferrer">@' +
              escapeHtml(cachedBotUsername) +
              "</a>";
          }
        }
        var botId = cfg && cfg.bot_id != null ? parseInt(cfg.bot_id, 10) : 0;
        if (cfg && cfg.telegram_login_enabled && botId > 0) {
          renderTelegramLoginButton(botId);
          return;
        }
        setGateError(
          "Вход в браузере недоступен. Откройте мини-приложение из Telegram или проверьте TELEGRAM_BOT_TOKEN на сервере."
        );
      })
      .catch(function (e) {
        setGateStatus("");
        setGateError(e.message || "Не удалось загрузить настройки входа");
      });
  }

  var listenersBound = false;

  function bootApp(tg) {
    showApp();
    syncDigestSettingsFromServer().catch(function () {});
    applyTelegramSafeAreaInsets();
    if (tg) {
      try {
        tg.ready();
      } catch (_) {}
      ensureTelegramFullscreen();
      try {
        if (tg.SettingsButton && typeof tg.SettingsButton.hide === "function") {
          tg.SettingsButton.hide();
        }
      } catch (_) {}
      applyTelegramSafeAreaInsets();
      bindTelegramViewportOnce(tg);
    }

    syncAppOverlay();
    meetingsViewMode = readMeetingsViewMode();
    syncMeetingsViewMenu();
    syncMeetingsViewSurfaces();

    if (listenersBound) {
      apiFetch("/me", { method: "GET" })
        .then(function (me) {
          cachedMe = me;
          if (me.bot_username) cachedBotUsername = me.bot_username;
          setSessionHint(true);
          persistBrowserSessionFromCurrentAuth();
        })
        .catch(function () {});
      setTab("actual", { noAnim: true });
      return;
    }
    listenersBound = true;

    bindTelegramMiniappBackOnce();
    scheduleTelegramMiniappBackBindRetries();
    bindEdgeSwipeBackOnce();
    bindOfflineQueueListenersOnce();

    const root = document.documentElement;
    root.classList.remove("tg-light");
    root.classList.add("tg-dark");
    function vkuiCssVar(name, fallback) {
      var v = getComputedStyle(root).getPropertyValue(name).trim();
      return v || fallback;
    }
    var pageBg = vkuiCssVar("--vkui--color_background", "#0a0a0a");
    var headerBg = vkuiCssVar("--vkui--color_background", "#0a0a0a");
    var surfaceBg = vkuiCssVar("--vkui--color_background_content", "#19191a");
    root.style.setProperty("--page-bg", pageBg);
    root.style.setProperty("--app-bg", pageBg);
    root.style.setProperty("--app-surface", surfaceBg);
    root.style.setProperty("--app-section", surfaceBg);
    root.style.setProperty("--tabbar-surface", surfaceBg);
    if (tg) {
      try {
        if (typeof tg.setBackgroundColor === "function") {
          tg.setBackgroundColor(pageBg);
        }
      } catch (_) {}
      try {
        if (typeof tg.setHeaderColor === "function") {
          tg.setHeaderColor(headerBg);
        }
      } catch (_) {}
      try {
        if (typeof tg.setBottomBarColor === "function") {
          tg.setBottomBarColor(surfaceBg);
        }
      } catch (_) {}
      try {
        if (typeof tg.onEvent === "function") {
          tg.onEvent("themeChanged", function () {
            try {
              if (typeof tg.setBackgroundColor === "function") {
                tg.setBackgroundColor(pageBg);
              }
            } catch (_) {}
            try {
              if (typeof tg.setHeaderColor === "function") {
                tg.setHeaderColor(headerBg);
              }
            } catch (_) {}
            try {
              if (typeof tg.setBottomBarColor === "function") {
                tg.setBottomBarColor(surfaceBg);
              }
            } catch (_) {}
            root.style.setProperty("--page-bg", pageBg);
            root.style.setProperty("--app-bg", pageBg);
            root.style.setProperty("--app-surface", surfaceBg);
            root.style.setProperty("--app-section", surfaceBg);
            root.style.setProperty("--tabbar-surface", surfaceBg);
          });
        }
      } catch (_) {}
    }

    document.querySelectorAll(".tabbar-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        if (e.target && e.target.closest && e.target.closest("[data-sidebar-toggle]")) return;
        setTab(btn.getAttribute("data-tab"));
      });
    });

    var sidebarToggle = document.getElementById("sidebar-toggle");
    if (sidebarToggle) {
      sidebarToggle.addEventListener("click", function (e) {
        e.preventDefault();
        toggleSidebarCollapsed();
      });
    }
    syncSidebarToggleUi();
    bindSidebarTreesOnce();
    renderSidebarTrees();

    document.querySelectorAll("#panel-notes .subtab-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        setNotesSubTab(b.getAttribute("data-subtab"));
      });
    });

    document.querySelectorAll("[data-digest-subtab]").forEach(function (b) {
      b.addEventListener("click", function () {
        setDigestSubTab(b.getAttribute("data-digest-subtab"));
      });
    });

    onId("notes-detail-back", "click", handleNotesDetailBack);

    var noteEditorWebBack = document.getElementById("note-editor-web-back");
    if (noteEditorWebBack) {
      noteEditorWebBack.addEventListener("click", function () {
        handleNoteEditorModalClose();
      });
    }

    var notesCreateBtn = document.getElementById("notes-create-btn");
    if (notesCreateBtn) {
      notesCreateBtn.addEventListener("click", function () {
        if (notesSubTab === "chats") createAndOpenChatThread();
        else openNewTodoistNote();
      });
    }

    var knowledgeCreateBtn = document.getElementById("knowledge-create-btn");
    if (knowledgeCreateBtn) {
      knowledgeCreateBtn.addEventListener("click", function () {
        openNewKnowledgeNote();
      });
    }

    var actualCreateBtn = document.getElementById("actual-create-btn");
    if (actualCreateBtn) {
      actualCreateBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleActualCreateMenu();
      });
    }
    var postedOpen = document.getElementById("posted-tasks-open");
    if (postedOpen) {
      postedOpen.addEventListener("click", function () {
        setPostedTasksScreen(true);
      });
    }
    var postedBack = document.getElementById("posted-tasks-back");
    if (postedBack) {
      postedBack.addEventListener("click", function () {
        setPostedTasksScreen(false);
      });
    }
    document.querySelectorAll("#actual-create-menu [data-actual-action]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var action = btn.getAttribute("data-actual-action");
        closeActualCreateMenu();
        if (action === "voice") openVoiceModal();
        if (action === "reminder") openReminderModal({});
        if (action === "meeting") openEventModal({});
        if (action === "task") openTaskModal({});
      });
    });
    document.addEventListener("click", function (e) {
      var menu = document.getElementById("actual-create-menu");
      var btn = document.getElementById("actual-create-btn");
      if (!menu || menu.classList.contains("hidden")) return;
      if (menu.contains(e.target) || (btn && btn.contains(e.target))) return;
      closeActualCreateMenu();
    });

    var voiceClose = document.getElementById("voice-close");
    if (voiceClose) voiceClose.addEventListener("click", closeVoiceModal);
    var voiceOverlay = document.getElementById("voice-overlay");
    if (voiceOverlay) {
      voiceOverlay.addEventListener("click", function (e) {
        if (e.target.id === "voice-overlay") closeVoiceModal();
      });
    }
    var voiceStart = document.getElementById("voice-record-start");
    if (voiceStart) voiceStart.addEventListener("click", startVoiceRecording);
    var voiceStop = document.getElementById("voice-record-stop");
    if (voiceStop) voiceStop.addEventListener("click", stopVoiceRecording);
    var voiceSend = document.getElementById("voice-send");
    if (voiceSend) voiceSend.addEventListener("click", sendVoiceTranscriptMessage);

    var notesSearchInput = document.getElementById("notes-search-input");
    if (notesSearchInput) {
      notesSearchInput.addEventListener("input", function () {
        notesSearchQuery = notesSearchInput.value || "";
        setNotesSearchExpanded(true);
        if (notesTagFilterMenuIsOpen()) fillNotesTagFilterMenu();
        if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      });
      notesSearchInput.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          if (notesTagFilterMenuIsOpen()) {
            e.preventDefault();
            closeNotesTagFilterMenu();
            return;
          }
          if (!String(notesSearchInput.value || "").trim()) {
            notesSearchInput.blur();
            setNotesSearchExpanded(false);
          }
        }
      });
      notesSearchInput.addEventListener("blur", function () {
        window.setTimeout(function () {
          if (notesTagFilterMenuIsOpen()) return;
          var field = document.getElementById("notes-search-field");
          var active = document.activeElement;
          if (field && active && field.contains(active)) return;
          if (!String(notesSearchInput.value || "").trim()) setNotesSearchExpanded(false);
        }, 120);
      });
    }

    var notesSearchToggle = document.getElementById("notes-search-toggle");
    if (notesSearchToggle) {
      notesSearchToggle.addEventListener("click", function (e) {
        e.stopPropagation();
        var wrap = document.getElementById("notes-search-wrap");
        var open = !(wrap && wrap.classList.contains("notes-search-wrap--open"));
        if (!open) closeNotesTagFilterMenu();
        setNotesSearchExpanded(open);
      });
    }

    var notesSearchTagBtn = document.getElementById("notes-search-tag-btn");
    if (notesSearchTagBtn) {
      bindNotesTagFilterDocClickOnce();
      notesSearchTagBtn.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
      notesSearchTagBtn.addEventListener("click", toggleNotesTagFilterMenu);
    }

    var knowledgeSearchInput = document.getElementById("knowledge-search-input");
    if (knowledgeSearchInput) {
      knowledgeSearchInput.addEventListener("input", function () {
        knowledgeSearchQuery = knowledgeSearchInput.value || "";
        if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
      });
    }

    var tagsManageClose = document.getElementById("tags-manage-close");
    if (tagsManageClose) tagsManageClose.addEventListener("click", closeTagsManageSheet);
    var tagsManageOverlay = document.getElementById("tags-manage-overlay");
    if (tagsManageOverlay) {
      tagsManageOverlay.addEventListener("click", function (e) {
        if (e.target.id === "tags-manage-overlay") closeTagsManageSheet();
      });
    }

    var tagsManageCreateBtn = document.getElementById("tags-manage-create-btn");
    var tagsManageNewInput = document.getElementById("tags-manage-new-input");
    if (tagsManageCreateBtn && tagsManageNewInput) {
      tagsManageCreateBtn.addEventListener("click", async function () {
        var name = String(tagsManageNewInput.value || "").trim();
        if (!name) {
          tagsManageNewInput.focus();
          return;
        }
        try {
          await apiFetch("/tags", {
            method: "POST",
            body: JSON.stringify({ name: name }),
          });
          tagsManageNewInput.value = "";
          await loadNotes();
          renderTagsManageList();
        } catch (e) {
          alert(e.message || String(e));
        }
      });
      tagsManageNewInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          tagsManageCreateBtn.click();
        }
      });
    }

    onId("meetings-prev", "click", function () {
      shiftMeetingsPage(-meetingsPageStep());
    });
    onId("meetings-next", "click", function () {
      shiftMeetingsPage(meetingsPageStep());
    });
    onId("meetings-today", "click", function () {
      goMeetingsToday();
    });
    onId("meetings-view-btn", "click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      toggleMeetingsViewMenu();
    });
    var viewMenu = document.getElementById("meetings-view-menu");
    if (viewMenu) {
      viewMenu.addEventListener("click", function (e) {
        var item = e.target && e.target.closest && e.target.closest("[data-meetings-view]");
        if (!item) return;
        e.preventDefault();
        e.stopPropagation();
        setMeetingsViewMode(item.getAttribute("data-meetings-view"));
      });
    }
    bindMeetingsViewMenuOnce();

    onId("gpt-close", "click", closeGptChat);
    onId("gpt-overlay-backdrop", "click", closeGptChat);
    onId("gpt-send", "click", function () {
      gptSendMessage();
    });
    onId("gpt-input", "keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        gptSendMessage();
      }
    });

    document.querySelectorAll("#payment-amount-chips .amount-chip").forEach(function (ch) {
      ch.addEventListener("click", function () {
        document.querySelectorAll("#payment-amount-chips .amount-chip").forEach(function (x) {
          x.classList.remove("amount-chip--active");
        });
        ch.classList.add("amount-chip--active");
        const rub = ch.getAttribute("data-rub");
        const inp = document.getElementById("payment-custom-amount");
        if (inp && rub != null) inp.value = rub;
        syncPaymentSummary();
      });
    });
    var customAmt = document.getElementById("payment-custom-amount");
    if (customAmt) {
      customAmt.addEventListener("input", function () {
        const raw = String(customAmt.value || "")
          .trim()
          .replace(/\s/g, "")
          .replace(",", ".");
        const num = parseFloat(raw);
        document.querySelectorAll("#payment-amount-chips .amount-chip").forEach(function (x) {
          const dr = x.getAttribute("data-rub");
          x.classList.toggle(
            "amount-chip--active",
            isFinite(num) && num > 0 && String(Math.round(num)) === dr
          );
        });
        syncPaymentSummary();
      });
    }

    document.querySelectorAll(".gpt-hint-chip").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const hint = btn.getAttribute("data-hint") || (btn.textContent || "").trim();
        const gptInp = document.getElementById("gpt-input");
        if (gptInp) gptInp.value = hint;
        gptSendMessage();
      });
    });

    onId("profile-expenses-open", "click", async function () {
      profileScreen("expenses");
      await loadExpensesDetail();
    });
    var contactsOpenBtn = document.getElementById("profile-contacts-open");
    if (contactsOpenBtn) {
      contactsOpenBtn.addEventListener("click", async function () {
        profileScreen("contacts");
        await loadContacts();
      });
    }
    var calendarsOpenBtn = document.getElementById("profile-calendars-open");
    if (calendarsOpenBtn) {
      calendarsOpenBtn.addEventListener("click", async function () {
        profileScreen("calendars");
        await loadCalendarsSources();
      });
    }
    var kbOpenBtn = document.getElementById("profile-knowledge-base-open");
    if (kbOpenBtn) {
      kbOpenBtn.addEventListener("click", function () {
        setTab("knowledge");
      });
    }
    var agentsOpenBtn = document.getElementById("profile-agents-open");
    if (agentsOpenBtn) {
      agentsOpenBtn.addEventListener("click", function () {
        openProfileAgents();
      });
    }
    var chatsOpenBtn = document.getElementById("profile-chats-open");
    if (chatsOpenBtn) {
      chatsOpenBtn.addEventListener("click", function () {
        profileScreen("chats");
        loadProfileChatsScreen();
      });
    }
    var chatsBack = document.getElementById("profile-chats-back");
    if (chatsBack) {
      chatsBack.addEventListener("click", function () {
        profileScreen("main");
      });
    }
    var digestToggle = document.getElementById("digest-enabled-toggle");
    if (digestToggle) {
      digestToggle.addEventListener("click", function () {
        toggleDigestEnabled();
      });
    }
    var digestDemoBtn = document.getElementById("digest-demo-seed-btn");
    if (digestDemoBtn) {
      digestDemoBtn.addEventListener("click", function () {
        runDigestDemoSeed();
      });
    }
    var digestRefreshBtn = document.getElementById("digest-refresh-btn");
    if (digestRefreshBtn) {
      digestRefreshBtn.addEventListener("click", function () {
        refreshDigestReports();
      });
    }
    var digestDetailBack = document.getElementById("digest-detail-back");
    if (digestDetailBack) {
      digestDetailBack.addEventListener("click", function () {
        closeDigestDetail();
      });
    }
    var agentsBack = document.getElementById("profile-agents-back");
    if (agentsBack) {
      agentsBack.addEventListener("click", function () {
        profileScreen("main");
      });
    }
    var agentsNew = document.getElementById("agents-new");
    if (agentsNew) {
      agentsNew.addEventListener("click", function () {
        openAgentForm(null);
      });
    }
    var kbBack = document.getElementById("profile-knowledge-base-back");
    if (kbBack) {
      kbBack.addEventListener("click", function () {
        profileScreen("main");
        refreshKnowledgeBaseHint();
      });
    }
    var kbAdd = document.getElementById("profile-kb-add");
    if (kbAdd) {
      kbAdd.addEventListener("click", function () {
        addKnowledgeBase();
      });
    }
    var kbUrl = document.getElementById("profile-kb-url");
    if (kbUrl) {
      kbUrl.addEventListener("input", syncKbNotionFieldVisibility);
    }
    var kbMemberAdd = document.getElementById("profile-kb-member-add");
    if (kbMemberAdd) {
      kbMemberAdd.addEventListener("click", async function () {
        var inp = document.getElementById("profile-kb-member-username");
        var username = inp ? String(inp.value || "").trim() : "";
        if (!selectedKbId) {
          showKbError("Сначала выберите базу: кнопка «Доступ».");
          return;
        }
        if (!username) {
          showKbError("Укажите @username.");
          return;
        }
        clearKbMessages();
        try {
          await apiFetch(
            "/knowledge-bases/" + encodeURIComponent(selectedKbId) + "/members",
            {
              method: "POST",
              body: JSON.stringify({ username: username }),
            }
          );
          if (inp) inp.value = "";
          await loadKnowledgeBaseMembers(selectedKbId);
          var msgEl = document.getElementById("profile-kb-msg");
          if (msgEl) {
            msgEl.textContent = "Доступ выдан.";
            msgEl.classList.remove("hidden");
          }
        } catch (e) {
          showKbError(e.message || String(e));
        }
      });
    }
    var calendarsBack = document.getElementById("profile-calendars-back");
    if (calendarsBack) {
      calendarsBack.addEventListener("click", function () {
        ensureCalendarsSaved().finally(function () {
          profileScreen("main");
        });
      });
    }
    window.addEventListener("pagehide", function () {
      if (!Object.keys(calendarsTouchedIds).length) return;
      var excluded = calendarsExcludedIdsFromCache();
      try {
        fetch(API + "/calendar/sources/excluded", {
          method: "PUT",
          headers: Object.assign(
            { "Content-Type": "application/json" },
            authHeaders()
          ),
          body: JSON.stringify({ excluded_ids: excluded }),
          credentials: "same-origin",
          keepalive: true,
        });
      } catch (_) {}
    });
    var contactsBack = document.getElementById("profile-contacts-back");
    if (contactsBack) {
      contactsBack.addEventListener("click", function () {
        profileScreen("main");
      });
    }
    var contactsSaveBtn = document.getElementById("contacts-save");
    if (contactsSaveBtn) contactsSaveBtn.addEventListener("click", contactsSave);
    var contactsFormClose = document.getElementById("contacts-form-close");
    if (contactsFormClose) {
      contactsFormClose.addEventListener("click", closeContactsFormModal);
    }
    var contactsFormOverlay = document.getElementById("contacts-form-overlay");
    if (contactsFormOverlay) {
      contactsFormOverlay.addEventListener("click", function (e) {
        if (e.target.id === "contacts-form-overlay") closeContactsFormModal();
      });
    }
    var contactsTgInput = document.getElementById("contacts-f-tg");
    if (contactsTgInput) {
      contactsTgInput.addEventListener("input", updateContactsFormTelegramHint);
    }
    var contactsCreateBtn = document.getElementById("contacts-create-btn");
    if (contactsCreateBtn) {
      contactsCreateBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleContactsCreateMenu();
      });
    }
    document.querySelectorAll("#contacts-create-menu [data-contacts-action]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var action = btn.getAttribute("data-contacts-action");
        closeContactsCreateMenu();
        if (action === "contact") openNewContactModal();
        if (action === "teams") openTeamsManageModal();
      });
    });
    document.addEventListener("click", function (e) {
      var menu = document.getElementById("contacts-create-menu");
      var btn = document.getElementById("contacts-create-btn");
      if (!menu || menu.classList.contains("hidden")) return;
      if (menu.contains(e.target) || (btn && btn.contains(e.target))) return;
      closeContactsCreateMenu();
    });
    var teamsManageClose = document.getElementById("teams-manage-close");
    if (teamsManageClose) {
      teamsManageClose.addEventListener("click", teamsManageCloseOrBack);
    }
    var teamsManageSave = document.getElementById("teams-manage-save");
    if (teamsManageSave) {
      teamsManageSave.addEventListener("click", saveTeamsManageEditName);
    }
    var teamsManageOverlay = document.getElementById("teams-manage-overlay");
    if (teamsManageOverlay) {
      teamsManageOverlay.addEventListener("click", function (e) {
        if (e.target.id === "teams-manage-overlay") closeTeamsManageModal();
      });
    }
    var teamsManageCreateBtn = document.getElementById("teams-manage-create-btn");
    if (teamsManageCreateBtn) {
      teamsManageCreateBtn.addEventListener("click", createTeamFromManage);
    }
    var teamsManageNewInput = document.getElementById("teams-manage-new-input");
    if (teamsManageNewInput) {
      teamsManageNewInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          createTeamFromManage();
        }
      });
    }
    var teamsManageEditName = document.getElementById("teams-manage-edit-name");
    if (teamsManageEditName) {
      teamsManageEditName.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          saveTeamsManageEditName();
        }
      });
    }
    var teamsManageAddMemberBtn = document.getElementById("teams-manage-add-member-btn");
    if (teamsManageAddMemberBtn) {
      teamsManageAddMemberBtn.addEventListener("click", addTeamMemberFromEdit);
    }
    var teamsManageAddShareBtn = document.getElementById("teams-manage-add-share-btn");
    if (teamsManageAddShareBtn) {
      teamsManageAddShareBtn.addEventListener("click", addTeamShareFromEdit);
    }
    var zoomBack = document.getElementById("profile-zoom-back");
    if (zoomBack) {
      zoomBack.addEventListener("click", function () {
        stopZoomStatusPoll();
        profileScreen("main");
        loadIntegrations();
      });
    }
    var zoomConnect = document.getElementById("profile-zoom-connect");
    if (zoomConnect) {
      zoomConnect.addEventListener("click", function () {
        oauthStart("zoom");
      });
    }
    var zoomDisc = document.getElementById("profile-zoom-disconnect");
    if (zoomDisc) {
      zoomDisc.addEventListener("click", async function () {
        await oauthDisconnect("zoom");
        await loadZoomScreen(false);
      });
    }
    var telemostBack = document.getElementById("profile-telemost-back");
    if (telemostBack) {
      telemostBack.addEventListener("click", function () {
        profileScreen("main");
        loadIntegrations();
      });
    }
    var telemostConnect = document.getElementById("profile-telemost-auth");
    if (telemostConnect) {
      telemostConnect.addEventListener("click", function () {
        openTelemostAuthLink();
      });
    }
    var telemostSubmit = document.getElementById("profile-telemost-submit");
    if (telemostSubmit) {
      telemostSubmit.addEventListener("click", function () {
        var inp = document.getElementById("profile-telemost-code");
        var code = (inp && inp.value) || "";
        code = String(code).trim();
        if (!code) {
          var errEl = document.getElementById("profile-telemost-err");
          if (errEl) {
            errEl.textContent = "Вставьте код со страницы Яндекса.";
            setHidden(errEl, false);
          }
          return;
        }
        submitTelemostCode(code);
      });
    }
    var telemostDisc = document.getElementById("profile-telemost-disconnect");
    if (telemostDisc) {
      telemostDisc.addEventListener("click", async function () {
        await oauthDisconnect("telemost");
        await loadTelemostScreen(false);
      });
    }
    var yandexBack = document.getElementById("profile-yandex-disk-back");
    if (yandexBack) {
      yandexBack.addEventListener("click", function () {
        profileScreen("main");
        loadIntegrations();
      });
    }
    var yandexAuth = document.getElementById("profile-yandex-disk-auth");
    if (yandexAuth) yandexAuth.addEventListener("click", openYandexDiskAuthLink);
    var yandexSubmit = document.getElementById("profile-yandex-disk-submit");
    if (yandexSubmit) {
      yandexSubmit.addEventListener("click", function () {
        var inp = document.getElementById("profile-yandex-disk-code");
        var code = (inp && inp.value) || "";
        code = String(code).trim();
        if (!code) {
          var errEl = document.getElementById("profile-yandex-disk-err");
          if (errEl) {
            errEl.textContent = "Вставьте код со страницы Яндекса.";
            errEl.classList.remove("hidden");
          }
          return;
        }
        submitYandexDiskCode(code);
      });
    }
    var yandexDisc = document.getElementById("profile-yandex-disk-disconnect");
    if (yandexDisc) {
      yandexDisc.addEventListener("click", async function () {
        await oauthDisconnect("yandex-disk");
        await loadYandexDiskScreen(false);
      });
    }
    var yandexRow = document.querySelector('#panel-profile [data-svc="yandex-disk"]');
    if (yandexRow) {
      yandexRow.addEventListener("click", function (e) {
        if (e.target.closest("[data-action]")) return;
        openYandexDiskSetup();
      });
    }
    var bitrixBack = document.getElementById("profile-bitrix-back");
    if (bitrixBack) {
      bitrixBack.addEventListener("click", function () {
        profileScreen("main");
        loadIntegrations();
      });
    }
    var bitrixSubmit = document.getElementById("profile-bitrix-submit");
    if (bitrixSubmit) {
      bitrixSubmit.addEventListener("click", function () {
        var inp = document.getElementById("profile-bitrix-token");
        var token = (inp && inp.value) || "";
        token = String(token).trim();
        if (!token) {
          var errEl = document.getElementById("profile-bitrix-err");
          if (errEl) {
            errEl.textContent = "Вставьте токен из Битрикс24.";
            errEl.classList.remove("hidden");
          }
          return;
        }
        connectBitrix(token);
      });
    }
    var bitrixDisc = document.getElementById("profile-bitrix-disconnect");
    if (bitrixDisc) {
      bitrixDisc.addEventListener("click", async function () {
        await disconnectBitrix();
      });
    }
    var bitrixRow = document.querySelector('#panel-profile [data-svc="bitrix"]');
    if (bitrixRow) {
      bitrixRow.addEventListener("click", function (e) {
        if (e.target.closest("[data-action]")) return;
        openBitrixSetup();
      });
    }
    document.querySelectorAll(".profile-legal-link").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        openLegalLink(a.getAttribute("href") || "");
      });
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "visible") return;
      var panelProfile = document.getElementById("panel-profile");
      if (panelProfile && !panelProfile.classList.contains("hidden")) {
        loadIntegrations();
        var zoomPanel = document.getElementById("profile-zoom");
        if (zoomPanel && !zoomPanel.classList.contains("hidden")) {
          loadZoomScreen(false);
        }
        var telemostPanel = document.getElementById("profile-telemost");
        if (telemostPanel && !telemostPanel.classList.contains("hidden")) {
          loadTelemostScreen(false);
        }
        var yandexPanel = document.getElementById("profile-yandex-disk");
        if (yandexPanel && !yandexPanel.classList.contains("hidden")) {
          loadYandexDiskScreen(false);
        }
        var bitrixPanel = document.getElementById("profile-bitrix");
        if (bitrixPanel && !bitrixPanel.classList.contains("hidden")) {
          loadBitrixScreen(false);
        }
        var kbPanel = document.getElementById("profile-knowledge-base");
        if (kbPanel && !kbPanel.classList.contains("hidden")) {
          loadKnowledgeBaseScreen();
        }
      }
    });
    onId("profile-expenses-back", "click", function () {
      profileScreen("main");
    });
    onId("profile-payment-back", "click", function () {
      profileScreen("main");
    });
    var bookingBack = document.getElementById("profile-booking-back");
    if (bookingBack) {
      bookingBack.addEventListener("click", function () {
        stopBookingWaQrPoll();
        profileScreen("main");
        loadIntegrations();
      });
    }
    var bookingSave = document.getElementById("booking-save-config");
    if (bookingSave) bookingSave.addEventListener("click", bookingSaveConfig);
    var bookingCode = document.getElementById("booking-send-code");
    if (bookingCode) bookingCode.addEventListener("click", bookingSendCode);
    var bookingConfirm = document.getElementById("booking-confirm-login");
    if (bookingConfirm) bookingConfirm.addEventListener("click", bookingConfirmLogin);
    var bookingWaStartBtn = document.getElementById("booking-wa-start");
    if (bookingWaStartBtn) bookingWaStartBtn.addEventListener("click", bookingWaStart);
    var bookingWaDiscBtn = document.getElementById("booking-wa-disconnect");
    if (bookingWaDiscBtn) bookingWaDiscBtn.addEventListener("click", bookingWaDisconnect);

    onId("payment-submit", "click", function () {
      alert("Оплата пока недоступна. Выберите способ и промокод можно сохранить позже.");
    });

    onId("payment-promo-apply", "click", async function () {
      const inp = document.getElementById("payment-promo-input");
      const code = (inp && inp.value) || "";
      const ok = document.getElementById("payment-promo-msg");
      const er = document.getElementById("payment-promo-err");
      ok.textContent = "";
      er.textContent = "";
      setHidden(ok, true);
      setHidden(er, true);
      try {
        const r = await apiFetch("/billing/redeem-promo", {
          method: "POST",
          body: JSON.stringify({ code: code }),
        });
        ok.textContent = r.message || "Готово.";
        ok.classList.remove("hidden");
        if (inp) inp.value = "";
        await loadProfile();
      } catch (e) {
        er.textContent = e.message || String(e);
        er.classList.remove("hidden");
      }
    });

    document.querySelectorAll("#panel-profile [data-svc] [data-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const card = btn.closest("[data-svc]");
        const svc = card && card.getAttribute("data-svc");
        const act = btn.getAttribute("data-action");
        if (!svc || !act) return;
        if (act === "connect") oauthStart(svc);
        if (act === "disconnect") oauthDisconnect(svc);
      });
    });
    var zoomRow = document.querySelector('#panel-profile [data-svc="zoom"]');
    if (zoomRow) {
      zoomRow.addEventListener("click", function (e) {
        if (e.target.closest("[data-action]")) return;
        openZoomSetup();
      });
    }
    var telemostRow = document.querySelector('#panel-profile [data-svc="telemost"]');
    if (telemostRow) {
      telemostRow.addEventListener("click", function (e) {
        if (e.target.closest("[data-action]")) return;
        openTelemostSetup();
      });
    }

    onId("modal-cancel", "click", closeModal);
    onId("modal-delete", "click", modalDelete);
    onId("modal-overlay", "click", function (e) {
      if (e.target.id === "modal-overlay") closeModal();
    });
    onId("modal-form", "submit", modalSubmit);
    onId("modal-save", "click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      modalSubmit(e);
    });

    apiFetch("/me", { method: "GET" })
      .then(function (me) {
        cachedMe = me;
        if (me.bot_username) cachedBotUsername = me.bot_username;
        setSessionHint(true);
        persistBrowserSessionFromCurrentAuth();
      })
      .catch(function () {});

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && bootAppStarted) {
        loadIntegrations();
      }
    });
    var tgBoot = window.Telegram && window.Telegram.WebApp;
    if (tgBoot && tgBoot.onEvent) {
      try {
        tgBoot.onEvent("visibility_changed", function () {
          if (tgBoot.isVisible !== false && bootAppStarted) loadIntegrations();
        });
      } catch (_) {}
    }

    setTab("actual", { noAnim: true });
    openPendingTaskFromLocation();
  }

  var bootAppStarted = false;

  function initWhenReady() {
    refreshMiniappDevFromUrl();
    const tg = window.Telegram && window.Telegram.WebApp;
    var hasTma = hasTelegramWebAppAuth();

    function startApp() {
      if (bootAppStarted) return;
      bootAppStarted = true;
      try {
        bootApp(tg);
      } catch (e) {
        bootAppStarted = false;
        console.error("bootApp", e);
      }
    }

    if (isInsideTelegramClient() && !hasTma && !miniappDev) {
      var authTries = 0;
      var maxAuthTries = 60;
      function waitTelegramAuth() {
        authTries += 1;
        if (hasTelegramWebAppAuth()) {
          startApp();
          apiFetch("/me", { method: "GET" })
            .then(function (me) {
              cachedMe = me;
              if (me.bot_username) cachedBotUsername = me.bot_username;
              setSessionHint(true);
              persistBrowserSessionFromCurrentAuth();
            })
            .catch(function () {});
          return;
        }
        if (authTries >= maxAuthTries) {
          showTelegramAuthError(
            "Telegram не передал данные для входа. Закройте окно, напишите боту /start и откройте «Ассистент» снова."
          );
          return;
        }
        setTimeout(waitTelegramAuth, 50);
      }
      waitTelegramAuth();
      return;
    }

    if (hasTma) {
      startApp();
      apiFetch("/me", { method: "GET" })
        .then(function (me) {
          cachedMe = me;
          if (me.bot_username) cachedBotUsername = me.bot_username;
          setSessionHint(true);
          persistBrowserSessionFromCurrentAuth();
        })
        .catch(function (e) {
          if (e && e.status === 401 && isInsideTelegramClient()) {
            showTelegramAuthError(e.message || String(e));
          }
        });
      return;
    }

    if (miniappDev) {
      startApp();
      return;
    }

    var hashUser = parseTgAuthResultFromHash();
    if (hashUser && hashUser.hash) {
      clearTgAuthHash();
      showGate();
      completeTelegramLogin(hashUser).catch(function (e) {
        setGateStatus("");
        setGateError(e.message || String(e));
        bootstrapGateLogin();
      });
      return;
    }

    if (getStoredSession() || hasSessionHint() || isStandalonePwa()) {
      startApp();
      tryExistingBrowserSession().then(function (ok) {
        if (ok === true || ok === "offline" || getStoredSession()) return;
        setSessionHint(false);
        gateLoginBootstrapped = false;
        showGate();
        bootstrapGateLogin();
      });
      return;
    }

    startApp();
    tryExistingBrowserSession().then(function (ok) {
      if (ok === true || ok === "offline") {
        return;
      }
      gateLoginBootstrapped = false;
      showGate();
      bootstrapGateLogin();
    });
  }

  function init() {
    refreshMiniappDevFromUrl();
    function kickoff() {
      var tg = getTelegramWebApp();
      if (tg) {
        try {
          tg.ready();
        } catch (_) {}
        try {
          tg.expand();
        } catch (_) {}
      }
      if (hasTelegramWebAppAuth() || miniappDev || !looksLikeTelegramWebView()) {
        initWhenReady();
        return;
      }
      var tries = 0;
      var maxTries = isIOSDevice() ? 80 : 40;
      function waitForTma() {
        tries += 1;
        var readyTg = getTelegramWebApp();
        if (readyTg) {
          try {
            readyTg.ready();
          } catch (_) {}
          try {
            readyTg.expand();
          } catch (_) {}
        }
        if (hasTelegramWebAppAuth() || (readyTg && tries >= 20) || tries >= maxTries) {
          initWhenReady();
          return;
        }
        setTimeout(waitForTma, 50);
      }
      waitForTma();
    }
    // telegram-web-app.js тоже defer — дождаться обоих перед стартом.
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", kickoff);
    } else {
      kickoff();
    }
  }

  registerServiceWorker();

  window.addEventListener("unhandledrejection", function (e) {
    try {
      if (e && typeof e.preventDefault === "function") e.preventDefault();
    } catch (_) {}
    console.error("unhandledrejection", e && e.reason);
  });
  window.addEventListener("error", function (e) {
    console.error("window.error", (e && e.error) || (e && e.message) || e);
  });

  init();
})();
