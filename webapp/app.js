(function () {
  var WEBAPP_BUILD = "20260908-remdisc";

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
    if (!standalone && looksLikeTelegramWebView()) {
      navigator.serviceWorker.getRegistrations().then(function (regs) {
        regs.forEach(function (reg) {
          try {
            reg.unregister();
          } catch (_) {}
        });
      });
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
  const NOTE_EDITOR_ASSET_V = "20260908-remdisc";
  const MINIAPP_CACHE_SCHEMA = 2;
  let noteEditorScriptsPromise = null;

  function isIOSDevice() {
    if (typeof navigator === "undefined") return false;
    return (
      /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
    );
  }
  function getMiniappCacheUserKey() {
    if (cachedMe && cachedMe.telegram_user_id != null) {
      return String(cachedMe.telegram_user_id);
    }
    var raw = getInitDataRaw();
    if (raw) {
      try {
        var p = new URLSearchParams(raw);
        var user = JSON.parse(p.get("user") || "null");
        if (user && user.id != null) return String(user.id);
      } catch (_) {}
    }
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
    } catch (_) {}
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
  let selectedPaymentMethod = "";
  let miniappDev = false;
  var meetingsPageDate = null;
  var notesDataCache = null;
  var knowledgeDataCache = null;
  var notesSearchQuery = "";
  var knowledgeSearchQuery = "";
  var notesActiveTagIds = [];
  var tagPickerSaveTimer = null;
  var tagPickerActiveClose = null;
  var tagPickerDocClickBound = false;
  var tabInited = { profile: false };
  var TAB_ORDER = ["actual", "notes", "knowledge", "profile"];
  var currentTab = "actual";
  var currentProfileScreen = "main";
  var panelTransitionMs = 280;
  var gptChatState = { context: "", title: "Диалог с GPT", history: [] };
  var voiceChatState = { history: [] };
  var voiceMediaRecorder = null;
  var voiceMediaStream = null;
  var voiceChunks = [];
  var notesTelegramBackHandlerBound = false;

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function shiftMeetingsPage(delta) {
    var base = meetingsPageDate;
    if (!base) {
      var now = new Date();
      base = now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());
    }
    var d = new Date(base + "T12:00:00");
    d.setDate(d.getDate() + delta);
    meetingsPageDate = d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
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

  function paintMeetingsList(ui, calData) {
    var listM = document.getElementById(ui.listId);
    var emptyM = document.getElementById(ui.emptyId);
    var errM = document.getElementById(ui.errId);
    var badgeM = ui.badgeId ? document.getElementById(ui.badgeId) : null;
    var headMeet = ui.headId ? document.getElementById(ui.headId) : null;
    if (!listM) return;
    listM.innerHTML = "";
    if (errM) {
      errM.textContent = "";
      setHidden(errM, true);
    }
    var calDate = (calData && calData.date) || "";
    if (headMeet) headMeet.textContent = formatMeetingsHead(calDate);
    var evs = (calData && calData.events) || [];
    if (!evs.length) {
      setHidden(emptyM, false);
      if (emptyM) emptyM.textContent = meetingsEmptyMessage(calData);
      if (badgeM) setHidden(badgeM, true);
      return;
    }
    setHidden(emptyM, true);
    if (badgeM) {
      setHidden(badgeM, false);
      badgeM.textContent = String(evs.length);
    }
    evs.forEach(function (ev, i) {
      listM.appendChild(renderMeetingCard(ev, i, evs.length));
    });
  }

  async function loadMeetingsInto(ui, dateIso) {
    var errM = document.getElementById(ui.errId);
    var listM = document.getElementById(ui.listId);
    if (listM) listM.innerHTML = '<p class="muted small">Загрузка…</p>';
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
    paintMeetingsList(ui, calData);
    return calData;
  }

  var meetingsUiMain = {
    listId: "meetings-list",
    emptyId: "meetings-empty",
    errId: "meetings-error",
    badgeId: "meetings-badge",
    headId: "meetings-head-title",
    trackDate: true,
  };

  function formatMeetingsHead(iso) {
    if (!iso) return "Встречи";
    try {
      var d = new Date(iso + "T12:00:00");
      if (isNaN(d.getTime())) return "Встречи";
      return (
        "Встречи · " +
        d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })
      );
    } catch (_) {
      return "Встречи";
    }
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
    var ids = ["note-editor-overlay", "tags-manage-overlay", "modal-overlay", "voice-overlay"];
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

  function createJournalPdfButton(journalId) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "note-card-action-btn";
    btn.setAttribute("aria-label", "Скачать PDF");
    btn.title = "Скачать PDF";
    btn.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
      '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
      '<polyline points="7 10 12 15 17 10"/>' +
      '<line x1="12" y1="15" x2="12" y2="3"/>' +
      "</svg>";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      requestJournalPdf(journalId).catch(function (err) {
        alert(err.message || String(err));
      });
    });
    return btn;
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
    var body = stripJournalEmbeddedFooters(String((it && (it.body || it.preview)) || ""));
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
    var nodes = root.querySelectorAll("h2, h3, h4, h5, h6");
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
      var plain = htmlToPlainText(clean);
      if (looksLikeMarkdown(plain)) {
        return plain;
      }
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
        source: "local",
        kb_enabled: item.kb_enabled !== false && item.kb_enabled !== 0 && item.kb_enabled !== "0",
        owner_user_id: item.owner_user_id || item.user_id || "",
        is_owner: item.is_owner !== false,
        revision: item.revision || 1,
        updated_at: item.updated_at || "",
        members: Array.isArray(item.members) ? item.members : [],
      },
      extra
    );
  }

  function isKnowledgeNoteItem(item) {
    return !!(item && (item.is_knowledge || item.role === "knowledge"));
  }

  function prependKnowledgeInCache(item) {
    if (!knowledgeDataCache) knowledgeDataCache = { notes: [] };
    if (!knowledgeDataCache.notes) knowledgeDataCache.notes = [];
    var lid = localNoteIdFrom(item);
    if (!lid) return;
    knowledgeDataCache.notes = knowledgeDataCache.notes.filter(function (x) {
      return String(x.id) !== String(lid);
    });
    var entry = noteCacheEntry(item, { role: "knowledge", is_knowledge: true });
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
    if (!notesDataCache) notesDataCache = { local_notes: [] };
    if (!notesDataCache.local_notes) notesDataCache.local_notes = [];
    var lid = localNoteIdFrom(item);
    if (!lid) return;
    notesDataCache.local_notes = notesDataCache.local_notes.filter(function (x) {
      return String(x.id) !== String(lid);
    });
    notesDataCache.local_notes.unshift(noteCacheEntry(item));
  }

  function removeLocalFromCache(id) {
    if (!notesDataCache || !notesDataCache.local_notes) return;
    notesDataCache.local_notes = notesDataCache.local_notes.filter(function (x) {
      return String(x.id) !== String(id);
    });
  }

  function isJournalPdfOp(op) {
    var k = String(op || "");
    return k === "obuchat_transcribe" || k === "summarize";
  }

  async function requestJournalPdf(eventId) {
    await apiFetch("/notes/journal/" + encodeURIComponent(String(eventId)) + "/pdf", {
      method: "POST",
    });
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
          context: gptChatState.context,
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
    var body = getNoteEditorBodyEl();
    if (body && typeof body._noteEditorFlush === "function") {
      try {
        await body._noteEditorFlush();
      } catch (e) {
        alert(e.message || "Не удалось сохранить заметку");
        return false;
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
    await apiFetch("/notes/local/" + encodeURIComponent(String(id)), {
      method: "DELETE",
    });
    removeLocalFromCache(id);
    removeKnowledgeFromCache(id);
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
    const res = await fetch(API + path, init);
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
        err.conflictItem = body.detail.item || null;
      }
      throw err;
    }
    return body;
  }

  async function apiFetchReal(path, opts) {
    const o = opts || {};
    const method = String(o.method || "GET").toUpperCase();
    const maxTries = method === "GET" || method === "HEAD" ? 12 : 1;
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
    var immersive =
      (detail && !detail.classList.contains("hidden")) ||
      (noteEditor && !noteEditor.classList.contains("hidden")) ||
      (pay && !pay.classList.contains("hidden")) ||
      (exp && !exp.classList.contains("hidden")) ||
      (booking && !booking.classList.contains("hidden")) ||
      (gpt && !gpt.classList.contains("hidden")) ||
      (discussion && !discussion.classList.contains("hidden")) ||
      (modal && !modal.classList.contains("hidden")) ||
      (voice && !voice.classList.contains("hidden")) ||
      (noteUnsaved && !noteUnsaved.classList.contains("hidden")) ||
      (tagsSheet && !tagsSheet.classList.contains("hidden"));
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
    }
    if (name !== "notes" && name !== "knowledge") {
      closeNotesDetail();
      if (isNoteEditorModalOpen()) handleNoteEditorModalClose({ skipPrompt: true });
    }
    if (name !== "profile" && currentProfileScreen !== "main") {
      profileScreen("main");
    }
    syncTelegramNativeBack();
    syncAppOverlay();
    syncNotesCreateFab();
    syncActualCreateFab();
  }

  function setTab(name, options) {
    options = options || {};
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

  function datetimeLocalToIso(val) {
    if (!val) return "";
    const d = new Date(val);
    if (isNaN(d.getTime())) return "";
    return d.toISOString();
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
      answer_with_context: "Обсуждение с GPT (архив)",
      "chat/completions": "Запрос к модели",
    };
    const key = String(op || "").trim();
    if (!key) return "—";
    return labels[key] || key;
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

  function contactsResetForm() {
    contactsEditingEmail = null;
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
    var cancel = document.getElementById("contacts-cancel-edit");
    if (cancel) setHidden(cancel, true);
    contactsShowFormMsg("", "");
  }

  function contactsFillForm(c) {
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
    var cancel = document.getElementById("contacts-cancel-edit");
    if (cancel) setHidden(cancel, false);
    contactsShowFormMsg("", "");
    var card = document.getElementById("contacts-form-card");
    if (card && card.scrollIntoView) card.scrollIntoView({ behavior: "smooth", block: "start" });
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
        if (contactsEditingEmail === c.email) contactsResetForm();
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

  var calendarsCache = [];
  var calendarsSaveTimer = null;
  var calendarsSaveRevision = 0;
  var calendarsFlushInFlight = false;
  var calendarsFlushPromise = null;
  var calendarsTouchedIds = {};
  var calendarsSaveDoneTimer = null;
  var meetingRemindersEnabled = true;
  var meetingRemindersSaveInFlight = false;
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
      applyMeetingRemindersToggleUi(settings.meeting_reminders_enabled !== false);
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
    try {
      if (contactsEditingEmail) {
        await apiFetch("/contacts/" + encodeURIComponent(contactsEditingEmail), {
          method: "PUT",
          body: JSON.stringify({
            name: name,
            email: email,
            telegram_username: tg || "",
            aliases: aliases,
            clear_telegram: !tg,
          }),
        });
        contactsShowFormMsg("Сохранено", "");
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
        var st = (created && created.status) || "added";
        contactsShowFormMsg(
          st === "updated" ? "Контакт обновлён" : st === "existing" ? "Уже в списке" : "Контакт добавлен",
          ""
        );
      }
      contactsResetForm();
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
    var q = String(knowledgeSearchQuery || "")
      .trim()
      .toLowerCase();
    if (!q) return true;
    var title =
      sanitizeNoteTitle(item.title || item.content || "") ||
      String(item.title || item.content || "");
    var preview = notePlainExcerpt(item.description || item.body || "", 500);
    var tagNames = noteTagsOf(item)
      .map(function (t) {
        return String(t.name || "");
      })
      .join(" ");
    var hay = (title + " " + preview + " " + tagNames).toLowerCase();
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
      appendTagChipsRow(inner, noteTagsOf(n), { prepend: true, head: true });
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className =
        "svc-toggle knowledge-card-toggle" +
        (n.kb_enabled === false || n.kb_enabled === 0 || n.kb_enabled === "0" ? "" : " svc-toggle--on");
      toggle.setAttribute("role", "switch");
      toggle.setAttribute(
        "aria-checked",
        n.kb_enabled === false || n.kb_enabled === 0 || n.kb_enabled === "0" ? "false" : "true"
      );
      toggle.innerHTML = '<span class="svc-toggle-knob" aria-hidden="true"></span>';
      function stopToggle(e) {
        e.preventDefault();
        e.stopPropagation();
      }
      toggle.addEventListener("pointerdown", stopToggle);
      toggle.addEventListener("click", function (e) {
        stopToggle(e);
        if (!id) return;
        var next = !(
          n.kb_enabled === false ||
          n.kb_enabled === 0 ||
          n.kb_enabled === "0"
        )
          ? false
          : true;
        n.kb_enabled = next;
        toggle.classList.toggle("svc-toggle--on", next);
        toggle.setAttribute("aria-checked", next ? "true" : "false");
        if (knowledgeDataCache && knowledgeDataCache.notes) {
          knowledgeDataCache.notes.forEach(function (row) {
            if (String(row.id) === String(id)) row.kb_enabled = next;
          });
          writeMiniappCache("knowledge", knowledgeDataCache);
        }
        apiFetch("/notes/local/" + encodeURIComponent(id), {
          method: "PATCH",
          body: JSON.stringify({ kb_enabled: next }),
        }).catch(function () {
          n.kb_enabled = !next;
          toggle.classList.toggle("svc-toggle--on", !next);
          toggle.setAttribute("aria-checked", !next ? "true" : "false");
        });
      });
      card.appendChild(inner);
      card.appendChild(toggle);
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
          { removeStack: true, confirmMessage: "Удалить документ?" }
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
    openLocalNoteDetail(n, opts);
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
    if (!field || isNoteRichEditorField(field)) return;
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
      var visibleH = noteEditorVisibleHeightPx();
      if (visibleH > 0) {
        sheet.style.height = visibleH + "px";
        sheet.style.maxHeight = visibleH + "px";
      } else {
        sheet.style.height = "100%";
        sheet.style.maxHeight = "100%";
      }
      // Keep sheet aligned with visual viewport when iOS shifts offsetTop.
      var offsetTop = vv && typeof vv.offsetTop === "number" ? vv.offsetTop : 0;
      sheet.style.transform = offsetTop > 0 ? "translateY(" + Math.round(offsetTop) + "px)" : "";
      body.style.paddingBottom = "";

      var stable =
        tg && typeof tg.viewportStableHeight === "number" ? tg.viewportStableHeight : 0;
      var tgH = tg && typeof tg.viewportHeight === "number" ? tg.viewportHeight : 0;
      var kbGuess = 0;
      if (stable > 0 && tgH > 0) kbGuess = Math.max(0, Math.round(stable - tgH));
      else if (vv) {
        kbGuess = Math.max(
          0,
          Math.round(window.innerHeight - vv.height - (vv.offsetTop || 0))
        );
      }
      root.classList.toggle("note-editor-kb-open", kbGuess > 40);
      root.style.setProperty("--note-editor-kb-inset", kbGuess + "px");
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
    bindModalSheetMobileOnce();
    updateModalSheetForKeyboard();
    syncNoteFormatToolbarForComposer();
  }

  function onModalSheetClose() {
    modalSheetOpen = false;
    document.documentElement.classList.remove("modal-sheet-open");
    document.querySelectorAll(".modal--sheet").forEach(resetModalSheetViewportStyles);
  }

  function closeModal() {
    const ov = document.getElementById("modal-overlay");
    ov.classList.add("hidden");
    ov.setAttribute("aria-hidden", "true");
    document.getElementById("modal-delete").classList.add("hidden");
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
  var MEETING_EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;

  function meetingEmailKey(email) {
    return String(email || "").trim().toLowerCase();
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
    var a = sEl ? new Date(sEl.value).getTime() : NaN;
    var b = eEl ? new Date(eEl.value).getTime() : NaN;
    if (!isNaN(a) && !isNaN(b) && b > a) return b - a;
    return 60 * 60 * 1000;
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
        syncHiddenFromEventPills(which);
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
        text.textContent = it.name || it.email;
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
        else if (items.length === 1) sum.textContent = items[0].name || items[0].email;
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
        name: String((raw && raw.name) || "").trim(),
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
        items.forEach(function (it) {
          contacts.forEach(function (c) {
            if (meetingEmailKey(c.email) === it.email) {
              if (!it.name) it.name = c.name || "";
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
    ev = ev || {};
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
    document.getElementById("m-ev-sum").value = ev.summary || "";
    document.getElementById("m-ev-start").value = isoToDatetimeLocalValue(
      sRaw || defaultDatetimeLocalValue(60)
    );
    document.getElementById("m-ev-end").value = isoToDatetimeLocalValue(
      eRaw || defaultDatetimeLocalValue(120)
    );
    syncEventPillsFromHidden("start");
    syncEventPillsFromHidden("end");
    bindEventDateTimePills();
    document.getElementById("m-ev-desc").value = ev.description || "";
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
    document.getElementById("modal-delete").classList.toggle("hidden", !ev.id);
    document.getElementById("modal-overlay").classList.remove("hidden");
    document.getElementById("modal-overlay").setAttribute("aria-hidden", "false");
    onModalSheetOpen();
    syncAppOverlay();
  }

  async function modalSubmit(ev) {
    ev.preventDefault();
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
        await apiFetch(id ? "/reminders/" + encodeURIComponent(id) : "/reminders", {
          method: id ? "PATCH" : "POST",
          body: JSON.stringify(body),
        });
        closeModal();
        await loadActual();
      } catch (e) {
        alert(e.message || String(e));
      }
      return;
    }
    if (kind === "event") {
      const id = document.getElementById("modal-event-id").value;
      const calId = document.getElementById("modal-calendar-id").value || "primary";
      const body = {
        calendar_id: calId,
        title: document.getElementById("m-ev-sum").value.trim(),
        start: datetimeLocalToIso(document.getElementById("m-ev-start").value),
        end: datetimeLocalToIso(document.getElementById("m-ev-end").value),
        description: document.getElementById("m-ev-desc").value,
        attendees: meetingAttendeesApi ? meetingAttendeesApi.getEmails() : [],
      };
      try {
        await apiFetch(id ? "/calendar/events/" + encodeURIComponent(id) : "/calendar/events", {
          method: id ? "PUT" : "POST",
          body: JSON.stringify(body),
        });
        closeModal();
        await loadActual();
      } catch (e) {
        alert(e.message || String(e));
      }
    }
  }

  async function modalDelete() {
    const kind = document.getElementById("modal-kind").value;
    if (kind === "reminder") {
      const id = document.getElementById("modal-reminder-id").value;
      if (!confirm("Удалить напоминание?")) return;
      try {
        await apiFetch("/reminders/" + encodeURIComponent(id), { method: "DELETE" });
        closeModal();
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
        await apiFetch("/reminders/" + encodeURIComponent(r.id), { method: "DELETE" });
        await loadActual();
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
      await apiFetch("/reminders/" + encodeURIComponent(r.id), { method: "DELETE" });
      await loadActual();
    });
  }

  function renderMeetingCard(ev, idx, total) {
    const row = document.createElement("div");
    row.className = "meeting-row";

    const startT = formatEventTime(ev);
    const endT = formatEventEndTime(ev);
    const col = document.createElement("div");
    col.className = "meeting-time-col";
    col.innerHTML =
      "<div>" +
      startT +
      "</div>" +
      (endT
        ? '<div class="meeting-time-end">' + endT + "</div>"
        : "") +
      '<div class="meeting-dot"></div>' +
      (idx < total - 1 ? '<div class="meeting-line"></div>' : "");

    const wrap = document.createElement("div");
    wrap.className = "meeting-card-wrap";
    const card = document.createElement("div");
    card.className = "meeting-card";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "meeting-card-edit-btn";
    edit.setAttribute("aria-label", "Изменить");
    edit.title = "Изменить";
    edit.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
      '<path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />' +
      "</svg>";
    edit.addEventListener("click", function (e) {
      e.stopPropagation();
      openEventModal(ev);
    });

    const top = document.createElement("div");
    top.className = "meeting-card-top";
    const topLeft = document.createElement("div");
    topLeft.className = "meeting-card-top-left";
    if (ev.kind) {
      const kind = document.createElement("span");
      kind.className = "meeting-kind-badge";
      kind.textContent = String(ev.kind);
      topLeft.appendChild(kind);
    }
    top.appendChild(topLeft);
    top.appendChild(edit);
    card.appendChild(top);

    const title = document.createElement("div");
    title.className = "item-title";
    const summary = ev.summary || "(без названия)";
    if (ev.html_link) {
      const link = document.createElement("a");
      link.className = "item-title-link";
      link.href = ev.html_link;
      link.textContent = summary;
      link.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        openExternal(ev.html_link);
      });
      title.appendChild(link);
    } else {
      title.textContent = summary;
    }
    card.appendChild(title);

    if (ev.meet_url) {
      const join = document.createElement("button");
      join.type = "button";
      join.className = "btn join";
      join.textContent = "Присоединиться";
      join.addEventListener("click", function (e) {
        e.stopPropagation();
        openExternal(ev.meet_url);
      });
      card.appendChild(join);
    }

    const swipe = wrapWithSwipeDelete(card, async function () {
      const calId = ev.calendar_id || "primary";
      const q = calId && calId !== "primary" ? "?calendar_id=" + encodeURIComponent(calId) : "";
      await apiFetch("/calendar/events/" + encodeURIComponent(ev.id) + q, {
        method: "DELETE",
      });
      await loadActual();
    });
    wrap.appendChild(swipe);
    row.appendChild(col);
    row.appendChild(wrap);
    return row;
  }

  function paintActual(remData, calData) {
    const hero = document.getElementById("actual-hero");
    const listEl = document.getElementById("reminders-list");
    const emptyR = document.getElementById("reminders-empty");
    const errR = document.getElementById("reminders-error");
    const errM = document.getElementById("meetings-error");
    const badgeR = document.getElementById("reminders-badge");

    listEl.innerHTML = "";
    errR.textContent = "";
    if (errM) {
      errM.textContent = "";
      setHidden(errM, true);
    }
    setHidden(errR, true);

    const calDate = (calData && calData.date) || "";

    const items = (remData && remData.items) || [];
    const active = items.filter(function (x) {
      return !x.done;
    });
    const evs = (calData && calData.events) || [];

    hero.innerHTML =
      '<div class="hero-inner">' +
      '<p class="hero-kicker">' +
      heroDateLineFor(calDate) +
      "</p>" +
      '<h1 class="hero-title">Добрый день 👋</h1>' +
      '<div class="hero-stats">' +
      '<div class="hero-stat"><p class="hero-stat-label">Напоминаний</p><p class="hero-stat-value">' +
      active.length +
      "</p></div>" +
      '<div class="hero-stat"><p class="hero-stat-label">Встреч</p><p class="hero-stat-value">' +
      evs.length +
      "</p></div></div></div>";

    if (!active.length) {
      setHidden(emptyR, false);
      setHidden(badgeR, true);
    } else {
      setHidden(emptyR, true);
      setHidden(badgeR, false);
      badgeR.textContent = String(active.length);
      active.forEach(function (r) {
        listEl.appendChild(renderReminderCard(r));
      });
    }

    paintMeetingsList(meetingsUiMain, calData);
  }

  async function loadActual() {
    const errR = document.getElementById("reminders-error");
    const errM = document.getElementById("meetings-error");
    var cacheKind = "actual_" + (meetingsPageDate || "today");
    var cached = readMiniappCache(cacheKind);
    if (cached) {
      paintActual(cached.remData || { items: [] }, cached.calData || { events: [] });
    }

    var remData = { items: [] };
    var calData = { events: [], connected: false };
    var results = await Promise.allSettled([
      apiFetch("/reminders", { method: "GET" }),
      fetchCalendarDay(meetingsPageDate),
    ]);

    if (results[0].status === "fulfilled") {
      remData = results[0].value;
      errR.textContent = "";
      setHidden(errR, true);
    } else if (!cached) {
      var remErr = results[0].reason;
      errR.textContent = (remErr && remErr.message) || String(remErr || "Ошибка");
      setHidden(errR, false);
    }

    if (results[1].status === "fulfilled") {
      calData = results[1].value;
      if (calData && calData.date && !meetingsPageDate) {
        meetingsPageDate = calData.date;
      }
      if (errM) {
        errM.textContent = "";
        setHidden(errM, true);
      }
    } else if (!cached && errM) {
      var calErr = results[1].reason;
      errM.textContent = (calErr && calErr.message) || String(calErr || "Ошибка");
      setHidden(errM, false);
    }

    paintActual(remData, calData);
    writeMiniappCache(cacheKind, { remData: remData, calData: calData });
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
    if (!isTelegramMobilePlatform(tg)) {
      syncTelegramFullscreenClass();
      return;
    }
    try {
      tg.expand();
    } catch (_) {}
    if (typeof tg.requestFullscreen === "function") {
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

  function bindTelegramMiniappBackOnce() {
    if (notesTelegramBackHandlerBound) return;
    notesTelegramBackHandlerBound = true;
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.BackButton || typeof tg.BackButton.onClick !== "function") return;
    tg.BackButton.onClick(function () {
      if (isNoteDiscussionOpen()) {
        closeNoteDiscussion();
        return;
      }
      if (isNoteEditorModalOpen()) {
        handleNoteEditorModalClose();
        return;
      }
      var detail = document.getElementById("notes-detail");
      if (detail && !detail.classList.contains("hidden")) {
        handleNotesDetailBack();
        return;
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
      if (pay && !pay.classList.contains("hidden")) {
        profileScreen("main");
        return;
      }
      if (zoom && !zoom.classList.contains("hidden")) {
        stopZoomStatusPoll();
        profileScreen("main");
        loadIntegrations();
        return;
      }
      if (telemost && !telemost.classList.contains("hidden")) {
        profileScreen("main");
        loadIntegrations();
        return;
      }
      if (yandexDisk && !yandexDisk.classList.contains("hidden")) {
        profileScreen("main");
        loadIntegrations();
        return;
      }
      if (bitrix && !bitrix.classList.contains("hidden")) {
        profileScreen("main");
        loadIntegrations();
        return;
      }
      if (knowledgeBase && !knowledgeBase.classList.contains("hidden")) {
        profileScreen("main");
        refreshKnowledgeBaseHint();
        return;
      }
      if (contacts && !contacts.classList.contains("hidden")) {
        profileScreen("main");
        return;
      }
      if (calendars && !calendars.classList.contains("hidden")) {
        profileScreen("main");
        return;
      }
      if (booking && !booking.classList.contains("hidden")) {
        profileScreen("main");
        loadIntegrations();
        return;
      }
      if (exp && !exp.classList.contains("hidden")) {
        profileScreen("main");
      }
    });
  }

  function applyTelegramSafeAreaInsets() {
    var tg = window.Telegram && window.Telegram.WebApp;
    var root = document.documentElement;
    if (!tg) return;
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
      root.style.setProperty("--tg-viewport-bottom-overlay", overlay + "px");

      // Final fallback: VisualViewport delta (works in many Android WebViews)
      var vv = window.visualViewport;
      var vvOverlay = 0;
      if (vv && typeof vv.height === "number") {
        vvOverlay = Math.max(0, window.innerHeight - vv.height - (vv.offsetTop || 0));
      }
      root.style.setProperty("--vv-bottom-overlay", Math.round(vvOverlay) + "px");

      // Android (3-button navigation) often reports all insets as 0 while system UI
      // still overlays the bottom of the webview. Add a conservative fallback.
      var plat = String(tg.platform || "").toLowerCase();
      var ua = (navigator && navigator.userAgent) || "";
      var isAndroid =
        plat.indexOf("android") === 0 || /android/i.test(ua || "");
      var inferredInset =
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
    var panelNotes = document.getElementById("panel-notes");
    var panelKnowledge = document.getElementById("panel-knowledge");
    var panelProfile = document.getElementById("panel-profile");
    var notesPanelVisible = panelNotes && !panelNotes.classList.contains("hidden");
    var knowledgePanelVisible =
      panelKnowledge && !panelKnowledge.classList.contains("hidden");
    var profilePanelVisible = panelProfile && !panelProfile.classList.contains("hidden");
    var notesOpen = detail && !detail.classList.contains("hidden") && notesPanelVisible;
    var noteEditorOpen =
      isNoteEditorModalOpen() && (notesPanelVisible || knowledgePanelVisible);
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

    var webEditorBack = document.getElementById("note-editor-web-back");
    var webEditorBackBar = document.getElementById("note-editor-web-back-bar");
    var showWebEditorBack = !!(noteEditorOpen && !native);
    if (webEditorBack) setHidden(webEditorBack, !showWebEditorBack);
    if (webEditorBackBar) setHidden(webEditorBackBar, !noteEditorOpen);
    document.documentElement.classList.toggle("note-editor-web-back-visible", !!noteEditorOpen);

    if (!native) {
      if (tg && tg.BackButton && typeof tg.BackButton.hide === "function") {
        try {
          tg.BackButton.hide();
        } catch (_) {}
      }
      return;
    }

    if (noteEditorOpen) {
      applyTelegramSafeAreaInsets();
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }

    if (notesOpen) {
      detail.classList.add("notes-detail--native-back");
      applyTelegramSafeAreaInsets();
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (payOpen) {
      pay.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (expOpen) {
      exp.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (bookingOpen) {
      booking.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (contactsOpen) {
      contacts.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (calendarsOpen) {
      calendars.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (zoomOpen) {
      zoom.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (telemostOpen) {
      telemost.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (yandexDiskOpen) {
      yandexDisk.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (bitrixOpen) {
      bitrix.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (kbaseOpen) {
      knowledgeBase.classList.add("profile-subscreen--native-back");
      try {
        tg.BackButton.show();
      } catch (_) {}
      return;
    }
    if (tg && tg.BackButton && typeof tg.BackButton.hide === "function") {
      try {
        tg.BackButton.hide();
      } catch (_) {}
    }
  }

  function getUserTagsFromCache() {
    return (notesDataCache && notesDataCache.tags) || [];
  }

  function noteTagsOf(item) {
    return Array.isArray(item && item.tags) ? item.tags : [];
  }

  function noteItemMatchesFilter(item, kind) {
    var active = notesActiveTagIds.slice();
    if (active.length) {
      var itemTagIds = noteTagsOf(item).map(function (t) {
        return Number(t.id);
      });
      for (var i = 0; i < active.length; i++) {
        if (itemTagIds.indexOf(active[i]) < 0) return false;
      }
    }
    var q = String(notesSearchQuery || "")
      .trim()
      .toLowerCase();
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
    var tagNames = noteTagsOf(item)
      .map(function (t) {
        return String(t.name || "");
      })
      .join(" ");
    var hay = (title + " " + preview + " " + tagNames).toLowerCase();
    return hay.indexOf(q) >= 0;
  }

  function renderNotesTagFilterBar() {
    var menu = document.getElementById("notes-tag-filter-menu");
    var badge = document.getElementById("notes-tag-filter-badge");
    var btn = document.getElementById("notes-tag-filter-btn");
    if (!menu) return;
    menu.innerHTML = "";
    var tags = getUserTagsFromCache();
    if (!tags.length) {
      var empty = document.createElement("p");
      empty.className = "muted small";
      empty.style.padding = "0.5rem 0.625rem";
      empty.textContent = "Тегов пока нет";
      menu.appendChild(empty);
    } else {
      tags.forEach(function (tag) {
        var tid = Number(tag.id);
        var active = notesActiveTagIds.indexOf(tid) >= 0;
        var item = document.createElement("button");
        item.type = "button";
        item.className = "notes-tag-filter-menu-item" + (active ? " notes-tag-filter-menu-item--active" : "");
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", active ? "true" : "false");
        item.innerHTML =
          "<span>" + escapeHtml(String(tag.name || "")) + "</span>" +
          (active ? '<span class="notes-tag-filter-menu-item-check" aria-hidden="true">✓</span>' : "");
        item.addEventListener("click", function (e) {
          e.stopPropagation();
          var idx = notesActiveTagIds.indexOf(tid);
          if (idx >= 0) notesActiveTagIds.splice(idx, 1);
          else notesActiveTagIds.push(tid);
          renderNotesTagFilterBar();
          if (notesDataCache) renderNotesPanesFromData(notesDataCache);
        });
        menu.appendChild(item);
      });
    }
    var foot = document.createElement("div");
    foot.className = "notes-tag-filter-menu-foot";
    var manage = document.createElement("button");
    manage.type = "button";
    manage.className = "notes-tag-filter-menu-manage";
    manage.textContent = "Управление тегами…";
    manage.addEventListener("click", function (e) {
      e.stopPropagation();
      closeNotesTagFilterMenu();
      openTagsManageSheet();
    });
    foot.appendChild(manage);
    menu.appendChild(foot);
    if (badge) {
      if (notesActiveTagIds.length) {
        badge.textContent = String(notesActiveTagIds.length);
        badge.classList.remove("hidden");
      } else {
        badge.textContent = "";
        badge.classList.add("hidden");
      }
    }
    if (btn) {
      btn.setAttribute("aria-expanded", notesTagFilterMenuOpen ? "true" : "false");
    }
  }

  var notesTagFilterMenuOpen = false;

  function closeNotesTagFilterMenu() {
    notesTagFilterMenuOpen = false;
    var menu = document.getElementById("notes-tag-filter-menu");
    var btn = document.getElementById("notes-tag-filter-btn");
    if (menu) menu.classList.add("hidden");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleNotesTagFilterMenu() {
    notesTagFilterMenuOpen = !notesTagFilterMenuOpen;
    var menu = document.getElementById("notes-tag-filter-menu");
    var btn = document.getElementById("notes-tag-filter-btn");
    if (!menu) return;
    if (notesTagFilterMenuOpen) {
      renderNotesTagFilterBar();
      menu.classList.remove("hidden");
      if (btn) btn.setAttribute("aria-expanded", "true");
    } else {
      closeNotesTagFilterMenu();
    }
  }

  function appendTagChipsRow(parent, tags, opts) {
    opts = opts || {};
    if (!tags || !tags.length) return;
    var row = document.createElement("div");
    row.className = parent.classList && parent.classList.contains("journal-card-main")
      ? "journal-card-tags"
      : "note-card-tags";
    if (opts.head) row.classList.add("note-card-tags--head");
    tags.forEach(function (tag) {
      var chip = document.createElement("span");
      chip.className = "tag-chip tag-chip--card";
      chip.textContent = String(tag.name || "");
      row.appendChild(chip);
    });
    if (opts.prepend) parent.insertBefore(row, parent.firstChild);
    else parent.appendChild(row);
  }

  function updateItemTagsInCache(itemKind, itemId, tags) {
    if (!notesDataCache) return;
    var idStr = String(itemId);
    function patch(list) {
      if (!list) return;
      list.forEach(function (row) {
        if (String(row.id) === idStr) row.tags = tags.slice();
      });
    }
    if (itemKind === "local") patch(notesDataCache.local_notes);
    else {
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

    var stateTags = (currentTags || []).slice();
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
        emptyLabel.textContent = "Теги";
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
        empty.textContent = "Тегов пока нет";
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
          var check = document.createElement("span");
          check.className = "tag-picker-menu-check";
          check.textContent = selected[tid] ? "✓" : "";
          check.setAttribute("aria-hidden", "true");
          var name = document.createElement("span");
          name.className = "tag-picker-menu-label";
          name.textContent = String(t.name || "");
          btn.appendChild(check);
          btn.appendChild(name);
          btn.addEventListener("click", function (e) {
            e.stopPropagation();
            if (selected[tid]) {
              stateTags = stateTags.filter(function (x) {
                return Number(x.id) !== tid;
              });
            } else {
              stateTags.push({ id: t.id, name: t.name });
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
        var name = prompt("Название тега");
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
          stateTags.push({ id: created.id, name: created.name });
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
    }

    var addWrap = document.createElement("div");
    addWrap.className = "tag-picker-add-wrap";
    addBtnEl = document.createElement("button");
    addBtnEl.type = "button";
    addBtnEl.className = "tag-picker-add-btn tag-picker-trigger";
    addBtnEl.setAttribute("aria-label", "Теги");
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
      list.innerHTML = '<p class="muted small">Тегов пока нет. Создайте первый ниже.</p>';
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
        if (!(await confirmDialog('Удалить тег «' + String(tag.name || "") + "»?"))) return;
        try {
          await apiFetch("/tags/" + encodeURIComponent(String(tag.id)), { method: "DELETE" });
          notesActiveTagIds = notesActiveTagIds.filter(function (id) {
            return id !== Number(tag.id);
          });
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
    return (
      '<div class="note-editor-doc-layout">' +
      tocToggle +
      '<div class="note-editor-toc-backdrop" data-note-toc-close aria-hidden="true"></div>' +
      '<aside class="note-editor-toc-panel" id="note-editor-toc-panel" aria-label="Оглавление">' +
      '<div class="note-editor-toc-head">' +
      "<span>Оглавление</span>" +
      '<button type="button" class="note-editor-toc-close" data-note-toc-close aria-label="Закрыть">x</button>' +
      "</div>" +
      '<div class="note-editor-toc-list"></div>' +
      "</aside>" +
      '<div class="note-editor-main-column">' +
      '<div class="note-editor-pad note-editor-pad--title-in-body">' +
      '<textarea id="note-editor-title-input" class="note-editor-title vkui--font_title1--regular" ' +
      'placeholder="Заголовок" aria-label="' +
      titleAria +
      '" maxlength="500" rows="1" autocapitalize="sentences" spellcheck="true"></textarea>' +
      '<div id="note-editor-members" class="note-editor-members hidden"></div>' +
      "</div>" +
      '<div class="note-editor-pad note-editor-pad--body"></div>' +
      "</div>" +
      "</div>"
    );
  }

  function mountNoteRichEditor(container, body, onUpdate, options) {
    options = options || {};
    var toolbarWrap = document.getElementById("note-editor-toolbar-wrap");
    var formatHost = document.getElementById("note-editor-format-toolbar-host");
    if (toolbarWrap) {
      setHidden(toolbarWrap, false);
      syncNoteFormatToolbarForComposer();
    }
    if (formatHost) formatHost.innerHTML = "";
    var mountPoint = document.createElement("div");
    mountPoint.id = "td-desc";
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
            onUpdate: onUpdate,
            onSelection: options.onSelection || null,
            toolbarParent: formatHost || null,
            tocParent: options.tocParent || null,
            onTocNavigate: options.onTocNavigate || null,
            scrollParent: options.scrollParent || null,
            extraTocItems: options.extraTocItems || null,
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
    if (!n || !notesDataCache || !notesDataCache.local_notes) return n;
    var lid = localNoteIdFrom(n);
    if (!lid) return n;
    for (var i = 0; i < notesDataCache.local_notes.length; i++) {
      if (String(notesDataCache.local_notes[i].id) === String(lid)) {
        return notesDataCache.local_notes[i];
      }
    }
    return n;
  }

  function getActiveNoteRichEditor() {
    var body = getNoteEditorBodyEl();
    return body && body._noteRichEditor ? body._noteRichEditor : null;
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
      notesSubTab === "notes" &&
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

  function setNotesSubTab(name) {
    notesSubTab = name;
    document.querySelectorAll(".subtab-btn").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-subtab") === name);
    });
    setHidden(document.getElementById("notes-pane-notes"), name !== "notes");
    setHidden(document.getElementById("notes-pane-transcriptions"), name !== "transcriptions");
    setHidden(document.getElementById("notes-pane-summaries"), name !== "summaries");
    syncNotesCreateFab();
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
      wrap._paeiRunning = false;
      wrap._commentChairId = null;
      wrap._commentDraft = null;
      wrap._noteMembers = [];
      wrap._noteRevision = 1;
      wrap._noteUpdatedAt = "";
      wrap._isNoteOwner = true;
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

  function applyNoteMemberPhoto(img, userId) {
    var uid = String(userId || "");
    if (!img || !uid) return;
    if (noteCollabState.photoBlobs[uid]) {
      img.src = noteCollabState.photoBlobs[uid];
      img.classList.add("has-photo");
      return;
    }
    fetch(API + "/users/" + encodeURIComponent(uid) + "/photo", {
      credentials: "same-origin",
      headers: authHeaders(),
    })
      .then(function (res) {
        return res.ok ? res.blob() : null;
      })
      .then(function (blob) {
        if (!blob || !blob.size) return;
        var url = URL.createObjectURL(blob);
        noteCollabState.photoBlobs[uid] = url;
        img.src = url;
        img.classList.add("has-photo");
      })
      .catch(function () {});
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

  function appendNoteCardAvatars(inner, members) {
    var list = (members || []).filter(function (m) {
      return m && m.user_id;
    });
    if (list.length < 2) return;
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
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) {
      wrap._noteRevision = item.revision || wrap._noteRevision;
      wrap._noteUpdatedAt = item.updated_at || wrap._noteUpdatedAt;
      wrap._noteMembers = item.members || wrap._noteMembers || [];
      wrap._isNoteOwner = item.is_owner !== false;
    }
    prependLocalInCache(item);
    var typing = Date.now() - noteCollabState.lastTypedAt < 1600;
    if (typing && !force) return;
    var titleInput = document.getElementById("note-editor-title-input");
    var editor = getActiveNoteRichEditor();
    noteCollabState.applying = true;
    try {
      if (titleInput && item.title != null && titleInput.value !== String(item.title || "")) {
        titleInput.value = String(item.title || "");
        titleInput.dispatchEvent(new Event("input"));
      }
      var nextBody = String(item.body || item.description || "");
      if (editor && typeof editor.getHtml === "function" && typeof editor.setHtml === "function") {
        if (editor.getHtml() !== nextBody) editor.setHtml(nextBody, { preserveCursor: true });
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
    var editor = getActiveNoteRichEditor();
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
        else {
          wrap._noteRevision = item.revision || wrap._noteRevision;
          wrap._noteUpdatedAt = item.updated_at || wrap._noteUpdatedAt;
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

    addItem(
      wrap._paeiRunning ? "PAIE · идёт разбор…" : "PAIE",
      function () {
        closeNoteMoreMenu();
        startNotePaei();
      },
      !!wrap._paeiRunning
    );

    if (wrap._shareKind === "local") {
      addItem("Участники", function () {
        closeNoteMoreMenu();
        openShareAccessSheet();
      });
    }

    if (!shared) {
      addItem("Поделиться ссылкой", function () {
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
  var gptModelsState = { loaded: false, loading: false, models: [], defaultId: "" };

  function noteComposerAsset(name) {
    return "/webapp/icons/" + name + ".svg?v=" + WEBAPP_BUILD;
  }

  function noteAskModeLabel(mode) {
    if (mode === "gpt") return "GPT";
    if (mode === "comment") return "Текст";
    return "PAIE";
  }

  function noteAskPlaceholder(mode) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var quoted = !!(wrap && wrap._commentDraft && String(wrap._commentDraft.quote || "").trim());
    if (mode === "gpt") {
      return quoted ? "Спросите GPT про выделенный текст" : "Свободный запрос к GPT по заметке";
    }
    if (mode === "comment") {
      return quoted ? "Комментарий к выделенному тексту" : "Напишите комментарий";
    }
    return quoted
      ? "Уточнение по выделенному тексту"
      : "Уточнение или новая информация — CHAIR ответит";
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
      '<form id="note-paie-reply-form" class="note-paie-reply-form">' +
      '<div class="note-paie-reply-target hidden">' +
      '<span class="note-paie-reply-target-bar" aria-hidden="true"></span>' +
      '<div class="note-paie-reply-target-copy">' +
      '<p class="note-paie-reply-target-who">Фрагмент</p>' +
      '<p class="note-paie-reply-target-quote"></p>' +
      "</div>" +
      '<button type="button" class="note-paie-reply-target-clear" aria-label="Отменить ответ">&times;</button>' +
      "</div>" +
      '<textarea id="note-paie-reply-input" class="note-paie-composer-input" rows="2" maxlength="4000" placeholder="' +
      noteAskPlaceholder("paie") +
      '"></textarea>' +
      '<div class="note-paie-composer-bar">' +
      '<div class="note-paie-composer-left">' +
      '<div class="note-paie-mode-switch" role="tablist" aria-label="Режим">' +
      '<button type="button" class="note-paie-mode is-active" data-mode="paie">PAIE</button>' +
      '<button type="button" class="note-paie-mode" data-mode="gpt">GPT</button>' +
      '<button type="button" class="note-paie-mode" data-mode="comment">Текст</button>' +
      "</div>" +
      '<button type="button" class="note-paie-mode-compact" aria-haspopup="listbox" aria-expanded="false">' +
      '<span class="note-paie-mode-compact-label">PAIE</span>' +
      '<img class="note-paie-chevron" src="' +
      chevronFill +
      '" alt="" width="24" height="24">' +
      "</button>" +
      '<div class="note-paie-menu note-paie-mode-menu hidden" role="listbox">' +
      '<button type="button" class="note-paie-menu-item is-active" data-mode="paie">PAIE</button>' +
      '<button type="button" class="note-paie-menu-item" data-mode="gpt">GPT</button>' +
      '<button type="button" class="note-paie-menu-item" data-mode="comment">Текст</button>' +
      "</div>" +
      '<button type="button" class="note-paie-model-btn" aria-haspopup="listbox" aria-expanded="false">' +
      '<span class="note-paie-model-label">Модель</span>' +
      '<img class="note-paie-chevron" src="' +
      chevronMuted +
      '" alt="" width="24" height="24">' +
      "</button>" +
      '<div class="note-paie-menu note-paie-menu--models hidden">' +
      '<input type="search" class="note-paie-model-search" placeholder="Поиск модели" autocomplete="off">' +
      '<div class="note-paie-menu-list"></div>' +
      "</div></div>" +
      '<button type="button" class="note-paie-kb-toggle is-on" aria-pressed="true" title="База знаний">' +
      noteKnowledgeIconHtml() +
      "</button>" +
      '<button type="submit" class="note-paie-send" id="note-paie-reply-submit" disabled aria-label="Отправить">' +
      '<img class="note-paie-send-icon" src="' +
      arrow +
      '" alt="" width="24" height="24">' +
      "</button></div></form>"
    );
  }

  function selectedGptModelId() {
    try {
      var stored = String(localStorage.getItem(GPT_MODEL_LS) || "").trim();
      if (stored) return stored;
    } catch (_) {}
    return gptModelsState.defaultId || "";
  }

  function setSelectedGptModelId(id) {
    var next = String(id || "").trim();
    try {
      if (next) localStorage.setItem(GPT_MODEL_LS, next);
    } catch (_) {}
    syncGptModelButton();
  }

  function gptModelShortName(id) {
    var found = gptModelsState.models.find(function (m) {
      return m.id === id;
    });
    if (found && found.name) return found.name;
    var raw = String(id || "");
    if (!raw) return "Модель";
    return raw.split("/").pop() || raw;
  }

  function closeNoteComposerMenus(except) {
    var form = document.getElementById("note-paie-reply-form");
    if (!form) return;
    form.querySelectorAll(".note-paie-menu").forEach(function (menu) {
      if (except && menu === except) return;
      menu.classList.add("hidden");
    });
    form.querySelectorAll(".note-paie-mode-compact, .note-paie-model-btn").forEach(function (btn) {
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
      gptModelsState.defaultId = String((res && res.default) || "").trim();
      gptModelsState.loaded = true;
      var allowed = {};
      gptModelsState.models.forEach(function (m) {
        if (m && m.id) allowed[m.id] = true;
      });
      var current = selectedGptModelId();
      if (!current || !allowed[current]) {
        setSelectedGptModelId(
          gptModelsState.defaultId ||
            (gptModelsState.models[0] && gptModelsState.models[0].id) ||
            ""
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
      gptModelsState.defaultId = "openai/gpt-5.6-sol";
      gptModelsState.loaded = true;
      setSelectedGptModelId(gptModelsState.defaultId);
    } finally {
      gptModelsState.loading = false;
      syncGptModelButton();
      renderGptModelMenu();
    }
  }

  function autosizeNoteComposer(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 136) + "px";
  }

  function bindNotePaieReplyForm(threadEl) {
    var form = document.getElementById("note-paie-reply-form");
    if (!form || !form.querySelector(".note-paie-composer-bar > .note-paie-kb-toggle")) {
      var html = notePaieReplyFormHtml();
      if (form) form.outerHTML = html;
      else if (threadEl) threadEl.insertAdjacentHTML("beforeend", html);
      form = document.getElementById("note-paie-reply-form");
    }
    if (!form) return;
    bindNoteKnowledgeToggle(form);
    if (form._bound) return;
    form._bound = true;
    var input = document.getElementById("note-paie-reply-input");
    var compact = form.querySelector(".note-paie-mode-compact");
    var modeMenu = form.querySelector(".note-paie-mode-menu");
    var modelBtn = form.querySelector(".note-paie-model-btn");
    var modelMenu = form.querySelector(".note-paie-menu--models");
    var modelSearch = form.querySelector(".note-paie-model-search");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      closeNoteComposerMenus();
      if (noteAskMode() === "gpt") submitNoteGptQuestion(input && input.value);
      else if (noteAskMode() === "comment") submitNoteFooterComment(input && input.value);
      else submitNotePaieReply(input && input.value);
    });
    var replyClear = form.querySelector(".note-paie-reply-target-clear");
    if (replyClear && !replyClear._bound) {
      replyClear._bound = true;
      replyClear.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        clearNoteComposerCommentTarget();
        var ta = document.getElementById("note-paie-reply-input");
        if (ta) {
          ta.placeholder = noteAskPlaceholder(noteAskMode());
          try {
            ta.focus();
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
    form.addEventListener("focusin", function () {
      syncNoteFormatToolbarForComposer();
    });
    form.addEventListener("focusout", function () {
      window.setTimeout(syncNoteFormatToolbarForComposer, 80);
    });
    if (input) {
      input.addEventListener("input", function () {
        autosizeNoteComposer(input);
        syncNotePaieReplyForm();
      });
      input.addEventListener("keydown", function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }
      });
    }
    if (!document._noteComposerMenuBound) {
      document._noteComposerMenuBound = true;
      document.addEventListener("click", function (e) {
        var t = e && e.target;
        if (t && t.closest && t.closest(".note-discuss-actions")) return;
        closeNoteComposerMenus();
        closeNoteAnswerMenu();
      });
    }
    syncGptModelButton();
  }

  function isNoteDiscussionOpen() {
    var ov = document.getElementById("note-discussion-overlay");
    return !!(ov && !ov.classList.contains("hidden"));
  }

  function freezeNoteEditorUnderDiscussion() {
    var sheet = document.querySelector("#note-editor-overlay .modal--sheet");
    var body = sheet && sheet.querySelector(".modal-body");
    if (sheet) {
      sheet.style.transform = "";
      sheet.style.height = "100%";
      sheet.style.maxHeight = "100%";
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
    var vv = window.visualViewport;
    var visibleH = noteEditorVisibleHeightPx();
    var offsetTop = vv && typeof vv.offsetTop === "number" ? vv.offsetTop : 0;
    var offsetLeft = vv && typeof vv.offsetLeft === "number" ? vv.offsetLeft : 0;
    var width = vv && typeof vv.width === "number" ? Math.round(vv.width) : 0;
    ov.style.bottom = "auto";
    ov.style.right = "auto";
    ov.style.top = Math.round(offsetTop) + "px";
    ov.style.left = Math.round(offsetLeft) + "px";
    ov.style.width = width > 0 ? width + "px" : "100%";
    if (visibleH > 0) {
      ov.style.height = visibleH + "px";
      ov.style.maxHeight = visibleH + "px";
    } else {
      ov.style.height = "100%";
      ov.style.maxHeight = "100%";
    }
    sheet.style.height = "100%";
    sheet.style.maxHeight = "100%";
    ov.style.transform = "";
    sheet.style.transform = "";
    if (window.scrollY || window.scrollX) {
      try {
        window.scrollTo(0, 0);
      } catch (_) {}
    }
  }

  function bindNoteDiscussionOverlayOnce() {
    var ov = document.getElementById("note-discussion-overlay");
    if (!ov || ov._bound) return ov;
    ov._bound = true;
    var closeBtn = document.getElementById("note-discussion-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function (e) {
        e.preventDefault();
        closeNoteDiscussion();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isNoteDiscussionOpen()) {
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
        if (e.matches) syncDesktopNoteDiscussion();
        else if (isNoteDiscussionOpen()) closeNoteDiscussion(true);
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
        if (t.closest(".note-discussion-scroll")) return;
        if (t.closest("textarea, input, .note-paie-menu, .note-paie-model-search")) return;
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
    resetDiscussionOverlayViewport();
    syncNoteDiscussionOpenClass();
    syncAppOverlay();
    syncTelegramNativeBack();
    if (isNoteEditorModalOpen()) updateModalSheetForKeyboard();
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
    if (wrap) wrap._discussionPinId = rootC && rootC.id;
    paintEditorCommentOverlay(comments, rootC && rootC.id);
    openNoteDiscussion({
      focus: false,
      scrollToId: rootC && rootC.id,
    });
    if (opts.fromDiscussion) {
      scrollNoteToQuote(rootC || comment);
      if (!isDesktopLayout()) closeNoteDiscussion(true);
    }
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
  }

  function openNoteDiscussion(opts) {
    opts = opts || {};
    bindNoteDiscussionOverlayOnce();
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareKind || !wrap._shareId) return;
    ensureNotePaieThread();
    if (opts.mode) setNoteAskMode(opts.mode);
    if (opts.draft) {
      wrap._commentDraft = {
        quote: String(opts.draft.quote || "").trim(),
        prefix: opts.draft.prefix || "",
        suffix: opts.draft.suffix || "",
      };
      wrap._commentChairId = opts.chairId || null;
      if (!opts.mode) setNoteAskMode("comment");
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
    var panel = document.getElementById("note-editor-comments");
    renderNotePaieThread(
      (panel && panel._allComments) || [],
      wrap._shareKind,
      wrap._shareId
    );
    var input = document.getElementById("note-paie-reply-input");
    var shouldFocus =
      opts.focus === true ||
      (!!opts.draft && !isDesktopLayout() && opts.focus !== false);
    if (input && shouldFocus) {
      try {
        input.focus();
      } catch (_) {}
    }
    var pinId = wrap._discussionPinId || opts.scrollToId;
    if (pinId) {
      scrollDiscussionToComment(pinId);
    } else if (!opts.fromDesktopSync) {
      var scroll = document.querySelector("#note-discussion-overlay .note-discussion-scroll");
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    }
    syncNoteDiscussionOpenClass();
    syncAppOverlay();
    syncTelegramNativeBack();
    syncNoteFormatToolbarForComposer();
    updateModalSheetForKeyboard();
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
    var active = !!quote;
    if (whoEl) whoEl.textContent = chairId ? "CHAIR" : "Фрагмент";
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
    return false;
  }

  function closeNoteAnswerMenu() {
    document.querySelectorAll(".note-discuss-menu").forEach(function (menu) {
      menu.classList.add("hidden");
    });
    document.querySelectorAll(".note-discuss-more").forEach(function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function showNoteToast(text) {
    var el = document.getElementById("note-toast");
    if (!el) return;
    el.textContent = String(text || "");
    el.classList.remove("hidden");
    if (el._timer) window.clearTimeout(el._timer);
    el._timer = window.setTimeout(function () {
      el.classList.add("hidden");
    }, 2400);
  }

  function appendAnswerToCurrentNote(text) {
    var raw = String(text || "").trim();
    if (!raw) return;
    var inst = getActiveNoteRichEditor();
    if (inst && typeof inst.appendText === "function") {
      inst.appendText(raw);
      showNoteToast("Добавлено в заметку");
      shareHaptic();
      return;
    }
    showNoteToast("Не удалось вставить в заметку");
  }

  async function createTaskNoteFromAnswer(text) {
    var raw = String(text || "").trim();
    if (!raw) return;
    var title = raw.split(/\n/)[0].trim();
    if (title.length > 80) title = title.slice(0, 79) + "…";
    if (!title) title = "Задача";
    var html = raw
      .split("\n")
      .map(function (line) {
        return "<p>" + escapeHtml(line || " ") + "</p>";
      })
      .join("");
    try {
      var createRes = await apiFetch("/notes/local", {
        method: "POST",
        body: JSON.stringify({
          title: title,
          description: html,
          sync_todoist: false,
        }),
      });
      var created = (createRes && createRes.item) || { id: createRes && createRes.id, title: title };
      prependLocalInCache(created);
      if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      showNoteToast("Заметка создана");
      shareHaptic();
    } catch (e) {
      alert(e.message || String(e));
    }
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
    var wrap = document.getElementById("note-editor-more-wrap");
    var currentId = wrap && wrap._shareKind === "local" ? String(wrap._shareId || "") : "";
    var q = String(query || "").trim().toLowerCase();
    var notes = ((notesDataCache && notesDataCache.local_notes) || []).filter(function (n) {
      var id = String((n && n.id) || "");
      if (!id || id === currentId) return false;
      if (!q) return true;
      var title = String(n.title || n.content || "").toLowerCase();
      var preview = noteLinkPreview(n).toLowerCase();
      return title.indexOf(q) >= 0 || preview.indexOf(q) >= 0;
    });
    list.innerHTML = "";
    if (!notes.length) {
      var empty = document.createElement("p");
      empty.className = "note-link-empty";
      empty.textContent = q ? "Ничего не найдено" : "Других заметок нет";
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
    var search = document.getElementById("note-link-search");
    if (search) search.value = "";
    function show() {
      renderNoteLinkList("", ov._answerText);
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

  function bindNoteAnswerMenu(btn, menu, text) {
    if (!btn || !menu) return;
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
        else if (action === "task") createTaskNoteFromAnswer(text);
        else if (action === "link") openNoteLinkPicker(text);
        else if (action === "copy") copyAnswerText(text);
      });
    });
  }

  function renderDiscussionMessage(c) {
    var item = document.createElement("article");
    var assistant = isAssistantComment(c);
    item.className = "note-discuss-msg " + (assistant ? "is-assistant" : "is-user");
    item.setAttribute("data-comment-id", String(c.id));
    if (c.quote) {
      var q = document.createElement("p");
      q.className = "note-discuss-quote";
      q.textContent = "«" + String(c.quote) + "»";
      item.appendChild(q);
    }
    var role = document.createElement("p");
    role.className = "note-discuss-role";
    role.textContent = assistant ? shareCommentAuthorLabel(c) : "Вы";
    item.appendChild(role);
    var bubble = document.createElement("div");
    bubble.className = "note-discuss-bubble";
    bubble.textContent = String((c && c.body) || "");
    item.appendChild(bubble);
    if (assistant && String((c && c.body) || "").trim()) {
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
        '<button type="button" class="note-discuss-menu-item" data-answer-action="into-note">📌 В заметку</button>' +
        '<button type="button" class="note-discuss-menu-item" data-answer-action="task">📋 Создать задачу</button>' +
        '<button type="button" class="note-discuss-menu-item" data-answer-action="link">🔗 Связать с заметкой</button>' +
        '<button type="button" class="note-discuss-menu-item" data-answer-action="copy">📄 Копировать</button>';
      actions.appendChild(more);
      actions.appendChild(menu);
      bindNoteAnswerMenu(more, menu, String(c.body || ""));
      item.appendChild(actions);
    }
    item.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".note-discuss-more, .note-discuss-menu")) {
        return;
      }
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
    if (list) {
      list.innerHTML = "";
      if (!rows.length && !statusOn && !running) {
        var empty = document.createElement("p");
        empty.className = "note-discussion-empty";
        empty.textContent = "Задайте вопрос или оставьте комментарий";
        list.appendChild(empty);
      } else {
        rows.forEach(function (c) {
          list.appendChild(renderDiscussionMessage(c));
        });
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
      if (pinId) scrollDiscussionToComment(pinId);
      else {
        var scroll = document.querySelector("#note-discussion-overlay .note-discussion-scroll");
        if (scroll) scroll.scrollTop = scroll.scrollHeight;
      }
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

  function noteUsesKnowledge() {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap && typeof wrap._useKnowledge === "boolean") return wrap._useKnowledge;
    var key = noteKnowledgeStorageKey();
    if (key) {
      try {
        var raw = localStorage.getItem(key);
        if (raw === "0") return false;
        if (raw === "1") return true;
      } catch (_) {}
    }
    return true;
  }

  function setNoteUsesKnowledge(on) {
    var next = !!on;
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) wrap._useKnowledge = next;
    var key = noteKnowledgeStorageKey();
    if (key) {
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch (_) {}
    }
    syncNoteKnowledgeToggle();
  }

  function syncNoteKnowledgeToggle() {
    var form = document.getElementById("note-paie-reply-form");
    var btn = form && form.querySelector(".note-paie-kb-toggle");
    if (!btn) return;
    var on = noteUsesKnowledge();
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on
      ? "База знаний включена. Нажмите, чтобы выключить"
      : "База знаний выключена. Нажмите, чтобы включить";
  }

  function bindNoteKnowledgeToggle(form) {
    var btn = form && form.querySelector(".note-paie-kb-toggle");
    if (!btn || btn._bound) {
      syncNoteKnowledgeToggle();
      return;
    }
    btn._bound = true;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      setNoteUsesKnowledge(!noteUsesKnowledge());
    });
    syncNoteKnowledgeToggle();
  }

  function noteAskMode() {
    var wrap = document.getElementById("note-editor-more-wrap");
    var mode = wrap && wrap._askMode;
    if (mode === "gpt" || mode === "comment") return mode;
    return "paie";
  }

  function setNoteAskMode(mode) {
    var wrap = document.getElementById("note-editor-more-wrap");
    if (wrap) {
      wrap._askMode = mode === "gpt" || mode === "comment" ? mode : "paie";
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
      form.classList.toggle("is-gpt-mode", current === "gpt");
      form.classList.toggle("is-comment-mode", current === "comment");
      var compactLabel = form.querySelector(".note-paie-mode-compact-label");
      if (compactLabel) compactLabel.textContent = noteAskModeLabel(current);
    }
    var input = document.getElementById("note-paie-reply-input");
    if (input) {
      input.placeholder = noteAskPlaceholder(current);
    }
    syncNoteComposerCommentTarget();
    if (current === "gpt") loadGptModels();
    else closeNoteComposerMenus();
    syncNotePaieReplyForm();
  }

  function activeNoteGptContext() {
    var title = "";
    var titleEl = document.getElementById("note-editor-title-input");
    if (titleEl) title = String(titleEl.value || "").trim();
    var root = noteEditorCommentRoot();
    var body = root ? String(root.innerText || root.textContent || "").trim() : "";
    var text = (title ? title + "\n\n" : "") + body;
    if (text.length > 12000) text = text.slice(0, 11999) + "…";
    return text;
  }

  async function knowledgeTextForAsk() {
    if (!noteUsesKnowledge()) return "";
    try {
      var data = await apiFetch("/notes/knowledge", { method: "GET" });
      var notes = (data && data.notes) || [];
      var parts = [];
      notes.forEach(function (note) {
        if (note && (note.kb_enabled === false || note.kb_enabled === 0 || note.kb_enabled === "0")) {
          return;
        }
        var html = note && note.body;
        if (!html) return;
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        var text = String(tmp.innerText || tmp.textContent || "").trim();
        if (!text) return;
        var title = String((note && note.title) || "Документ").trim() || "Документ";
        parts.push("## " + title + "\n" + text);
      });
      var text = parts.join("\n\n");
      if (text.length > 12000) text = text.slice(0, 11999) + "…";
      return text;
    } catch (_) {
      return "";
    }
  }

  async function submitNoteGptQuestion(text) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var body = String(text || "").trim();
    if (!wrap || !wrap._shareKind || !wrap._shareId || wrap._gptBusy || !body) return;
    var kind = wrap._shareKind;
    var itemId = wrap._shareId;
    var panel = document.getElementById("note-editor-comments");
    var api = window.NoteComments;
    var history = [];
    var prior = discussionComments((panel && panel._allComments) || []).slice().sort(function (a, b) {
      var at = Date.parse(a && a.created_at) || 0;
      var bt = Date.parse(b && b.created_at) || 0;
      if (at !== bt) return at - bt;
      return Number(a.id || 0) - Number(b.id || 0);
    });
    prior.forEach(function (c) {
      var content = String((c && c.body) || "").trim();
      if (!content) return;
      var quote = String((c && c.quote) || "").trim();
      if (quote) content = "Про текст: «" + quote + "»\n\n" + content;
      if (api && api.isGptComment && api.isGptComment(c)) {
        history.push({ role: "assistant", content: content });
        return;
      }
      if (api && api.isPaieComment && api.isPaieComment(c)) {
        history.push({ role: "user", content: "CHAIR:\n" + content });
        return;
      }
      history.push({ role: "user", content: content });
    });
    wrap._gptBusy = true;
    syncNotePaieReplyForm(true);
    var input = document.getElementById("note-paie-reply-input");
    if (input) input.value = "";
    var draft = wrap._commentDraft;
    var quote = (draft && draft.quote) || "";
    try {
      var saved = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: body,
            quote: quote,
            prefix: "__gpt__",
            suffix: "",
          }),
        }
      );
      var parentId = saved && saved.comment && saved.comment.id;
      refreshNoteCommentsList();
      var kb = await knowledgeTextForAsk();
      var ctx = "Заметка:\n" + activeNoteGptContext();
      if (quote) ctx = "Выделенный текст:\n" + quote + "\n\n" + ctx;
      if (kb) ctx = "База знаний:\n" + kb + "\n\n" + ctx;
      var res = await apiFetch("/gpt/chat", {
        method: "POST",
        body: JSON.stringify({
          message: body,
          context: ctx,
          history: history,
          model: selectedGptModelId() || undefined,
        }),
      });
      var ans = String((res && res.answer) || "").trim() || "—";
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
      await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/comments",
        {
          method: "POST",
          body: JSON.stringify({
            body: ans,
            quote: quote,
            prefix: "__gpt__",
            suffix: "",
            parent_id: parentId || null,
            as_role: "gpt",
          }),
        }
      );
      shareHaptic();
      clearNoteComposerCommentTarget();
      refreshNoteCommentsList();
    } catch (e) {
      alert(e.message || String(e));
      refreshNoteCommentsList();
    } finally {
      wrap._gptBusy = false;
      syncNotePaieReplyForm();
    }
  }

  async function submitNoteFooterComment(text) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var body = String(text || "").trim();
    if (!wrap || !wrap._shareKind || !wrap._shareId || !body) return;
    var api = window.NoteComments;
    var chairId = wrap._commentChairId;
    var draft =
      wrap._commentDraft ||
      (api && api.selectionAnchor ? api.selectionAnchor(noteEditorCommentRoot()) : null);
    var input = document.getElementById("note-paie-reply-input");
    try {
      await apiFetch(
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
          }),
        }
      );
      if (input) input.value = "";
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
    var busy = paieRunning || gptBusy;
    var input = document.getElementById("note-paie-reply-input");
    var submit = document.getElementById("note-paie-reply-submit");
    var hasChair = !!(
      api &&
      api.paieThreadRootId &&
      api.paieThreadRootId((panel && panel._allComments) || [])
    );
    if (input) input.disabled = busy;
    var modeEl = document.getElementById("note-paie-thread");
    if (modeEl) {
      modeEl
        .querySelectorAll(
          ".note-paie-mode, .note-paie-mode-compact, .note-paie-model-btn, .note-paie-kb-toggle"
        )
        .forEach(function (btn) {
          btn.disabled = busy;
        });
    }
    if (submit) {
      var hasText = !!(input && String(input.value || "").trim());
      var canLaunchPaie = noteAskMode() === "paie" && !hasChair;
      var ready = !busy && (hasText || canLaunchPaie);
      submit.disabled = !ready;
      submit.classList.toggle("is-ready", ready);
      var label = "Отправить";
      if (gptBusy) label = "GPT отвечает…";
      else if (paieRunning) label = "CHAIR отвечает…";
      else if (noteAskMode() === "gpt") label = "Спросить GPT";
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
    if (!wrap || !listEl) return;
    loadNoteComments(wrap._shareKind, wrap._shareId, listEl);
  }

  function pollNotePaei(path) {
    var tries = 0;
    function tick() {
      var live = document.getElementById("note-editor-more-wrap");
      if (!live || notePaeiPath() !== path) return;
      apiFetch(path, { method: "GET" })
        .then(function (data) {
          var status = (data && data.status) || "idle";
          if (status === "running") {
            tries += 1;
            setNotePaeiStatus(
              (data.progress && data.progress.label) || "PAIE разбирает заметку…",
              data.progress
            );
            if (tries > 180) {
              live._paeiRunning = false;
              setNotePaeiStatus("PAIE всё ещё работает. Обновите заметку чуть позже.");
              return;
            }
            setTimeout(tick, 2500);
            return;
          }
          live._paeiRunning = false;
          if (status === "error") {
            setNotePaeiStatus((data && data.error) || "Не удалось завершить PAIE");
            return;
          }
          if (status === "done") {
            setNotePaeiStatus("");
            shareHaptic();
            refreshNoteCommentsList();
            return;
          }
          setNotePaeiStatus("PAIE прервался. Запустите ещё раз.");
        })
        .catch(function (e) {
          tries += 1;
          var code = e && e.status;
          var transient = code === 502 || code === 503 || code === 504 || !code;
          if (transient && tries <= 24) {
            setNotePaeiStatus("Сервер временно недоступен, пробую снова…", {
              label: "Сервер временно недоступен, пробую снова…",
              pct: 16,
            });
            setTimeout(tick, 3000);
            return;
          }
          if (tries > 8) {
            var w = document.getElementById("note-editor-more-wrap");
            if (w) w._paeiRunning = false;
            setNotePaeiStatus(e.message || "Не удалось проверить статус PAIE");
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
    wrap._paeiRunning = true;
    syncNoteCommentsPanel();
    ensureNotePaieThread();
    openNoteDiscussion({ mode: "paie" });
    setNotePaeiStatus("Анализ заметки…", { label: "Анализ заметки…", pct: 10 });
    apiFetch(path, {
      method: "POST",
      body: JSON.stringify({ use_knowledge: noteUsesKnowledge() }),
    })
      .then(function (data) {
        var status = (data && data.status) || "running";
        if (status === "done") {
          wrap._paeiRunning = false;
          setNotePaeiStatus("");
          shareHaptic();
          refreshNoteCommentsList();
          return;
        }
        if (status === "error") {
          wrap._paeiRunning = false;
          setNotePaeiStatus((data && data.error) || "Не удалось запустить PAIE");
          return;
        }
        if (data && data.progress) {
          setNotePaeiStatus(data.progress.label, data.progress);
        }
        pollNotePaei(path);
      })
      .catch(function (e) {
        wrap._paeiRunning = false;
        setNotePaeiStatus(e.message || "Не удалось запустить PAIE");
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
    wrap._paeiRunning = true;
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
    apiFetch(path, {
      method: "POST",
      body: JSON.stringify({
        reply: body,
        parent_id: rootId,
        reply_to_id: replyToId,
        quote: (draft && draft.quote) || "",
        use_knowledge: noteUsesKnowledge(),
      }),
    })
      .then(function (data) {
        if (input) input.value = "";
        clearNoteComposerCommentTarget();
        refreshNoteCommentsList();
        var status = (data && data.status) || "running";
        if (status === "done") {
          wrap._paeiRunning = false;
          setNotePaeiStatus("");
          shareHaptic();
          refreshNoteCommentsList();
          return;
        }
        if (status === "error") {
          wrap._paeiRunning = false;
          setNotePaeiStatus((data && data.error) || "Не удалось отправить уточнение");
          return;
        }
        if (data && data.progress) {
          setNotePaeiStatus(data.progress.label, data.progress);
        }
        pollNotePaei(path);
      })
      .catch(function (e) {
        wrap._paeiRunning = false;
        setNotePaeiStatus(e.message || "Не удалось отправить уточнение");
        renderNotePaieThread(
          (panel && panel._allComments) || (panel && panel._paieComments) || [],
          wrap._shareKind,
          wrap._shareId
        );
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
    syncNoteCommentsPanel();
  }

  function selectedShareAccess() {
    var checked = document.querySelector('input[name="note-share-access"]:checked');
    return checked && checked.value === "comment" ? "comment" : "view";
  }

  function closeShareAccessSheet() {
    var ov = document.getElementById("note-share-sheet-overlay");
    if (ov) ov.classList.add("hidden");
  }

  function openShareAccessSheet() {
    var wrap = document.getElementById("note-editor-more-wrap");
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
    if (membersBlock) {
      membersBlock.classList.toggle("hidden", wrap._shareKind !== "local");
    }
    paintShareMembersList();
    ov.classList.remove("hidden");
    if (wrap._shareKind === "local") loadShareMemberContacts();
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
    var wrap = document.getElementById("note-editor-more-wrap");
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
      role.textContent = m.is_owner ? "Автор" : "Редактирование";
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
    var wrap = document.getElementById("note-editor-more-wrap");
    var suggest = document.getElementById("note-share-member-suggest");
    var input = document.getElementById("note-share-member-input");
    if (!wrap || wrap._shareKind !== "local" || !wrap._shareId) return;
    if (suggest) suggest.classList.add("hidden");
    try {
      var data = await apiFetch(
        "/notes/local/" + encodeURIComponent(wrap._shareId) + "/members",
        {
          method: "POST",
          body: JSON.stringify({
            email: contact && contact.email ? contact.email : undefined,
            telegram_username: contact && contact.telegram_username ? contact.telegram_username : undefined,
            telegram_user_id: contact && contact.telegram_user_id ? contact.telegram_user_id : undefined,
          }),
        }
      );
      if (data && data.item) {
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
    var wrap = document.getElementById("note-editor-more-wrap");
    if (!wrap || !wrap._shareId || !memberId) return;
    try {
      await apiFetch(
        "/notes/local/" +
          encodeURIComponent(wrap._shareId) +
          "/members/" +
          encodeURIComponent(String(memberId)),
        { method: "DELETE" }
      );
      wrap._noteMembers = (wrap._noteMembers || []).filter(function (m) {
        return String(m.user_id) !== String(memberId);
      });
      paintShareMembersList();
      renderOpenNoteMembers(wrap._noteMembers, []);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function createAndCopyShareLink(access) {
    var wrap = document.getElementById("note-editor-more-wrap");
    var kind = wrap && wrap._shareKind;
    var itemId = wrap && wrap._shareId;
    if (!kind || !itemId) return;
    try {
      var data = await apiFetch(
        "/notes/" + encodeURIComponent(kind) + "/" + encodeURIComponent(itemId) + "/share",
        { method: "POST", body: JSON.stringify({ access: access || "view" }) }
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
    var wrap = document.getElementById("note-editor-more-wrap");
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
    var wrap = document.getElementById("note-editor-more-wrap");
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
      closeNoteMoreMenu();
      closeShareAccessSheet();
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
    if (api && api.isGptComment && api.isGptComment(c)) return "GPT";
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
      syncDesktopNoteDiscussion();
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
      api.showBubble(sel.rect, function () {
        openNoteDiscussion({ mode: "comment", draft: sel });
      });
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
    wrap._paeiRunning = false;
    wrap._askMode = "paie";
    wrap._noteMembers = [];
    wrap._noteRevision = 1;
    wrap._noteUpdatedAt = "";
    wrap._isNoteOwner = true;
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
    syncAppOverlay();
    syncTelegramNativeBack();
    syncNotesCreateFab();
  }

  function closeNoteEditorModal() {
    var flushP = Promise.resolve();
    var body = getNoteEditorBodyEl();
    if (body && typeof body._noteEditorFlush === "function") {
      flushP = body._noteEditorFlush().catch(function () {});
      body._noteEditorFlush = null;
    }
    flushP.finally(function () {
      closeNoteDiscussion(true);
      closeShareAccessSheet();
      stopNoteCollab();
      destroyActiveNoteRichEditor();
      clearNoteEditorToolbarWrap();
      clearNoteEditorTagsWrap();
      if (body) {
        body._noteEditor = null;
        body.innerHTML = "";
      }
      var ov = document.getElementById("note-editor-overlay");
      if (ov) {
        ov.classList.remove("note-editor-overlay--fullscreen");
        ov.classList.add("hidden");
        ov.setAttribute("aria-hidden", "true");
      }
      onModalSheetClose();
      applyTelegramSafeAreaInsets();
      syncTelegramNativeBack();
      syncAppOverlay();
      syncNotesCreateFab();
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
      renderNotesPanesFromData(notesDataCache);
    } else {
      var cachedNotes = readMiniappCache("notes");
      if (cachedNotes) {
        notesDataCache = cachedNotes;
        renderNotesPanesFromData(cachedNotes);
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
    notesDataCache = data;
    writeMiniappCache("notes", data);
    if (data.journal_error && err) {
      err.textContent = data.journal_error;
      setHidden(err, false);
    }
    renderNotesTagFilterBar();
    renderNotesPanesFromData(data);
    openPendingSharedNote(data);
  }

  var noteDeepLinkConsumed = false;

  function openPendingSharedNote(data) {
    if (noteDeepLinkConsumed) return;
    var pending = pendingNoteIdFromLocation();
    if (!pending) return;
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

  function renderNotesPanesFromData(data) {
    renderNotesTagFilterBar();
    const pn = document.getElementById("notes-pane-notes");
    const pt = document.getElementById("notes-pane-transcriptions");
    const ps = document.getElementById("notes-pane-summaries");
    if (!pn || !pt || !ps) return;
    pn.innerHTML = "";
    pt.innerHTML = "";
    ps.innerHTML = "";

    const localAll = (data && data.local_notes) || [];
    const local = localAll.filter(function (n) {
      return noteItemMatchesFilter(n, "local");
    });
    var errParts = [];
    if (data.local_error) errParts.push(String(data.local_error));
    if (errParts.length && !local.length) {
      pn.innerHTML = '<p class="error"></p>';
      pn.querySelector(".error").textContent = errParts.join(" · ");
    } else if (!local.length) {
      pn.innerHTML =
        localAll.length && (notesSearchQuery.trim() || notesActiveTagIds.length)
          ? '<p class="muted empty-hint">Ничего не найдено. Измените поиск или фильтр по тегам.</p>'
          : '<p class="muted empty-hint">Заметок пока нет. Нажмите «+» справа от переключателя или создайте заметку в чате с ботом.</p>';
    } else {
      function appendNoteCard(n, opts) {
        opts = opts || {};
        const isLocal = !!opts.isLocal;
        const noteId = localNoteIdFrom(n);
        const id = noteId != null ? String(noteId) : "";
        const card = document.createElement("div");
        card.className = "note-card";
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
        appendTagChipsRow(inner, noteTagsOf(n), { prepend: true, head: true });
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

    function journalCards(listAll, pane, accent) {
      var list = (listAll || []).filter(function (row) {
        return noteItemMatchesFilter(row, "journal");
      });
      var emptyHint =
        accent && accent.emptyHint
          ? accent.emptyHint
          : "Пока нет записей";
      if (!listAll || !listAll.length) {
        pane.innerHTML = '<p class="muted empty-hint">' + escapeHtml(emptyHint) + "</p>";
        return;
      }
      if (!list.length) {
        pane.innerHTML =
          '<p class="muted empty-hint">Ничего не найдено. Измените поиск или фильтр по тегам.</p>';
        return;
      }
      list.forEach(function (row) {
        const jid = row.id;
        const card = document.createElement("div");
        card.className = "note-card";
        const inner = document.createElement("div");
        inner.className = "note-card-inner";
        const titleRaw = journalCardTitle(row);
        const descRaw = notePlainExcerpt(row.preview || "", 280);
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
        if (isJournalPdfOp(row.operation)) {
          inner.appendChild(createJournalPdfButton(jid));
        }
        appendTagChipsRow(inner, noteTagsOf(row), { prepend: true, head: true });
        card.appendChild(inner);
        card.addEventListener("click", function () {
          openJournalEditorDetail(row);
        });
        pane.appendChild(
          wrapWithSwipeDelete(
            card,
            async function () {
              await apiFetch("/notes/journal/" + encodeURIComponent(String(jid)), {
                method: "DELETE",
              });
              removeJournalFromCache(jid);
              if (notesDataCache) renderNotesPanesFromData(notesDataCache);
            },
            {
              removeStack: true,
              confirmMessage: journalDeleteConfirmMessage(row.operation),
            }
          )
        );
      });
    }

    journalCards(data.transcriptions || [], pt, {
      emptyHint: "Транскрипций пока нет.",
    });
    journalCards(data.summaries || [], ps, {
      emptyHint: "Саммари пока нет.",
    });
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
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|div|li|h[1-6])>/gi, "\n");
    var doc = new DOMParser().parseFromString("<div>" + raw + "</div>", "text/html");
    return (doc.body.textContent || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
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
      /(^|\n)\|.+\|/.test(s) ||
      /(^|\n)>\s/.test(s)
    );
  }

  function inlineMarkdown(s) {
    return String(s || "")
      .split(/(\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~|__[^_]+__)/g)
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
    var inList = false;
    var inTaskList = false;
    function closeList() {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
    }
    function closeTaskList() {
      if (inTaskList) {
        html.push("</ul>");
        inTaskList = false;
      }
    }
    function closeAllLists() {
      closeList();
      closeTaskList();
    }
    for (var lineIndex = 0; lineIndex < lines.length; lineIndex++) {
      var line = lines[lineIndex];
      var trimmed = line.trim();
      if (!trimmed) {
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
          html.push("<table>");
          tableRows.forEach(function (cells, rowIdx) {
            html.push("<tr>");
            cells.forEach(function (cell) {
              var tag = rowIdx === 0 ? "th" : "td";
              html.push("<" + tag + ">" + inlineMarkdown(cell) + "</" + tag + ">");
            });
            html.push("</tr>");
          });
          html.push("</table>");
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
      var tm = trimmed.match(/^-\s+\[([ xX])\]\s+(.*)$/);
      if (tm) {
        closeList();
        if (!inTaskList) {
          html.push('<ul class="note-task-list">');
          inTaskList = true;
        }
        var checked = String(tm[1]).toLowerCase() === "x";
        html.push(
          '<li class="note-task' +
            (checked ? " note-task--checked" : "") +
            '" data-line="' +
            String(lineIndex) +
            '"><input type="checkbox"' +
            (checked ? " checked" : "") +
            ' data-line="' +
            String(lineIndex) +
            '" aria-label="Задача"><span class="note-task-text">' +
            inlineMarkdown(tm[2]) +
            "</span></li>"
        );
        continue;
      }
      var bm = trimmed.match(/^[-*•]\s+(.*)$/);
      if (bm) {
        closeTaskList();
        if (!inList) {
          html.push("<ul>");
          inList = true;
        }
        html.push("<li>" + inlineMarkdown(bm[1]) + "</li>");
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
      ul: true,
      ol: true,
      li: true,
      br: true,
      p: true,
      a: true,
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
    var proceed = function () {
      openLocalNoteDetailReady(n, opts, noteIsCreate, noteId);
    };
    if (noteIsCreate || !noteId || opts.knowledge || isKnowledgeNoteItem(n)) {
      proceed();
      return;
    }
    apiFetch("/notes?limit=120", { method: "GET" })
      .then(function (bundle) {
        var fresh = ((bundle && bundle.local_notes) || []).find(function (x) {
          return String(x.id) === String(noteId);
        });
        if (fresh) {
          n = Object.assign({}, n, fresh);
          if (notesDataCache) {
            prependLocalInCache(fresh);
          }
        }
        openLocalNoteDetailReady(n, opts, noteIsCreate, noteId);
      })
      .catch(function () {
        proceed();
      });
  }

  function openLocalNoteDetailReady(n, opts, noteIsCreate, noteId) {
    const cleanTitle =
      sanitizeNoteTitle(n.title || n.content || "") || String(n.title || n.content || "");
    const cleanBody = sanitizeNoteBody(n.description || n.body || "");
    var isKnowledge = !!(opts && opts.knowledge) || isKnowledgeNoteItem(n);

    const wrap = document.createElement("div");
    wrap.className = "note-editor-page";
    clearNoteEditorTagsWrap();
    var tagsWrap = document.getElementById("note-editor-tags-wrap");
    if (!noteIsCreate && noteId && tagsWrap) {
      try {
        mountTagPicker(tagsWrap, "local", noteId, noteTagsOf(n), function (tags) {
          n.tags = tags;
        });
        setHidden(tagsWrap, false);
      } catch (err) {
        console.error("mountTagPicker", err);
        setHidden(tagsWrap, true);
      }
      mountShareControls("local", noteId);
    }
    wrap.innerHTML = noteEditorPageInnerHtml(
      isKnowledge ? "Заголовок документа" : "Заголовок заметки"
    );
    var tocControls = bindNoteEditorTocControls(wrap);
    var editorPad = wrap.querySelector(".note-editor-pad--body");
    var titleInput = wrap.querySelector("#note-editor-title-input");
    if (titleInput) titleInput.value = cleanTitle;

    var saveTimer = null;
    var noteRichEditor = null;
    function readFields() {
      return {
        title: getNoteEditorTitle().trim(),
        description: readActiveNoteEditorHtml(),
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
      var fields = readFields();
      var title = String(fields.title || "").trim();
      if (!title) {
        if (!opts.defaultTitle) return;
        title = String(opts.defaultTitle).trim();
        if (!title) return;
        if (titleInput) titleInput.value = title;
        fields.title = title;
      }
      if (!noteIsCreate && noteId && isTrivialNoteHtml(fields.description) && !opts.keepEmpty) {
        return;
      }
      if (noteIsCreate || !noteId) {
        var createRes = await apiFetch(isKnowledge ? "/notes/knowledge" : "/notes/local", {
          method: "POST",
          body: JSON.stringify({
            title: fields.title,
            description: fields.description,
            sync_todoist: false,
          }),
        });
        var created =
          (createRes && createRes.item) ||
          { id: createRes.id, title: fields.title, description: fields.description };
        if (isKnowledge) {
          created.role = created.role || "knowledge";
          created.is_knowledge = true;
        }
        noteId = String(created.id || "");
        noteIsCreate = false;
        n.id = noteId;
        if (isKnowledge) {
          prependKnowledgeInCache(created);
        } else {
          prependLocalInCache(created);
          if (notesDataCache) renderNotesPanesFromData(notesDataCache);
        }
        if (noteId && tagsWrap && tagsWrap.classList.contains("hidden")) {
          try {
            mountTagPicker(tagsWrap, "local", noteId, noteTagsOf(n), function (tags) {
              n.tags = tags;
            });
            setHidden(tagsWrap, false);
          } catch (err) {
            console.error("mountTagPicker", err);
          }
        }
        if (noteId && !isKnowledge) ensureNoteShareMounted(noteId);
      } else {
        var moreWrap = document.getElementById("note-editor-more-wrap");
        var patchBody = {
          title: fields.title,
          description: fields.description,
        };
        if (moreWrap && moreWrap._noteRevision) {
          patchBody.expected_revision = moreWrap._noteRevision;
        }
        if (moreWrap && moreWrap._noteUpdatedAt) {
          patchBody.expected_updated_at = moreWrap._noteUpdatedAt;
        }
        try {
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
            tags: noteTagsOf(n),
            members: moreWrap && moreWrap._noteMembers,
            revision: moreWrap && moreWrap._noteRevision,
            updated_at: moreWrap && moreWrap._noteUpdatedAt,
            is_owner: moreWrap ? moreWrap._isNoteOwner : n.is_owner,
            owner_user_id: n.owner_user_id,
          };
          if (patchRes && patchRes.item) {
            n = Object.assign(n, patchRes.item);
            if (moreWrap) {
              moreWrap._noteRevision = patchRes.item.revision || moreWrap._noteRevision;
              moreWrap._noteUpdatedAt = patchRes.item.updated_at || moreWrap._noteUpdatedAt;
              moreWrap._noteMembers = patchRes.item.members || moreWrap._noteMembers;
            }
          }
          if (isKnowledge) {
            patchedItem.role = "knowledge";
            patchedItem.is_knowledge = true;
            prependKnowledgeInCache(patchedItem);
          } else {
            prependLocalInCache(patchedItem);
          }
        } catch (e) {
          if (e && e.status === 409 && e.conflictItem) {
            applyRemoteSharedNote(e.conflictItem, true);
            return;
          }
          throw e;
        }
      }
      var detailBodyDraft = getNoteEditorBodyEl();
      if (detailBodyDraft && detailBodyDraft._noteEditor) {
        detailBodyDraft._noteEditor.baseline = noteEditorSnapshot(
          fields.title,
          fields.description
        );
      }
      setNoteEditorSaveHint("");
    }

    async function openNoteGptComposer() {
      if (isKnowledge) return;
      try {
        await persistNoteDraft({ defaultTitle: "Новый чат", keepEmpty: true });
      } catch (e) {
        alert(e.message || String(e));
        return;
      }
      if (!noteId) return;
      ensureNoteShareMounted(noteId);
      openNoteDiscussion({ mode: "gpt", focus: true });
    }

    function schedulePatch() {
      if (noteCollabState.applying) return;
      noteCollabState.lastTypedAt = Date.now();
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
        await persistNoteDraft();
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
      modalBody._noteEditorFlush = async function () {
        if (saveTimer) {
          clearTimeout(saveTimer);
          saveTimer = null;
        }
        await persistNoteDraft();
      };
      modalBody._noteEditor = {
        ready: false,
        baseline: noteEditorSnapshot(cleanTitle, cleanBody),
        getCurrent: function () {
          return noteEditorSnapshot(getNoteEditorTitle(), readActiveNoteEditorHtml());
        },
        runSave: runSave,
      };
      modalBody._openNoteGpt = openNoteGptComposer;
    }
    bindNoteGptToolbarButton();
    syncNoteGptToolbarButton(!isKnowledge);
    openNoteEditorModal(
      noteIsCreate
        ? isKnowledge
          ? "Новый документ"
          : "Новая заметка"
        : isKnowledge
          ? "Документ"
          : "Заметка"
    );
    mountNoteRichEditor(editorPad, cleanBody, schedulePatch, {
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
          readActiveNoteEditorHtml()
        );
      }
      refreshNoteCommentAnchors();
      if (!noteIsCreate && noteId && !isKnowledge) startNoteCollab(noteId, n);
    });
    bindNoteTitleAutoresize(titleInput);
    if (titleInput) titleInput.addEventListener("input", schedulePatch);
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
          row.tags = tags;
        });
        setHidden(tagsWrap, false);
      } catch (err) {
        console.error("mountTagPicker", err);
        setHidden(tagsWrap, true);
      }
      mountShareControls("journal", journalId);
    }
    wrap.innerHTML = noteEditorPageInnerHtml("Заголовок");
    syncNoteGptToolbarButton(false);
    var tocControls = bindNoteEditorTocControls(wrap);
    var editorPad = wrap.querySelector(".note-editor-pad--body");
    var titleInput = wrap.querySelector("#note-editor-title-input");
    if (titleInput) titleInput.value = cleanTitle;
    appendJournalRelatedLinks(wrap, it, op, row);

    var saveTimer = null;
    function readFields() {
      return {
        title: getNoteEditorTitle().trim(),
        description: readActiveNoteEditorHtml(),
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
      };
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
          return noteEditorSnapshot(getNoteEditorTitle(), readActiveNoteEditorHtml());
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
      btn.addEventListener("click", function () {
        setTab(btn.getAttribute("data-tab"));
      });
    });

    document.querySelectorAll(".subtab-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        setNotesSubTab(b.getAttribute("data-subtab"));
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
        openNewTodoistNote();
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
    document.querySelectorAll("#actual-create-menu [data-actual-action]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var action = btn.getAttribute("data-actual-action");
        closeActualCreateMenu();
        if (action === "voice") openVoiceModal();
        if (action === "reminder") openReminderModal({});
        if (action === "meeting") openEventModal({});
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
        if (notesDataCache) renderNotesPanesFromData(notesDataCache);
      });
    }

    var knowledgeSearchInput = document.getElementById("knowledge-search-input");
    if (knowledgeSearchInput) {
      knowledgeSearchInput.addEventListener("input", function () {
        knowledgeSearchQuery = knowledgeSearchInput.value || "";
        if (knowledgeDataCache) renderKnowledgePaneFromData(knowledgeDataCache);
      });
    }

    var notesTagFilterBtn = document.getElementById("notes-tag-filter-btn");
    if (notesTagFilterBtn) {
      notesTagFilterBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        toggleNotesTagFilterMenu();
      });
    }
    document.addEventListener("click", function (e) {
      if (!notesTagFilterMenuOpen) return;
      var menu = document.getElementById("notes-tag-filter-menu");
      var btn = document.getElementById("notes-tag-filter-btn");
      if (menu && (menu.contains(e.target) || (btn && btn.contains(e.target)))) return;
      closeNotesTagFilterMenu();
    });

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
      shiftMeetingsPage(-1);
    });
    onId("meetings-next", "click", function () {
      shiftMeetingsPage(1);
    });

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
        contactsResetForm();
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
    var contactsCancelBtn = document.getElementById("contacts-cancel-edit");
    if (contactsCancelBtn) contactsCancelBtn.addEventListener("click", contactsResetForm);
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
      showTelegramAuthError(
        "Telegram не передал данные для входа. Закройте окно, напишите боту /start и откройте «Ассистент» снова."
      );
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
    var tg = getTelegramWebApp();
    if (tg) {
      try {
        tg.ready();
      } catch (_) {}
    }
    if (hasTelegramWebAppAuth() || miniappDev || !looksLikeTelegramWebView()) {
      initWhenReady();
      return;
    }
    var tries = 0;
    var maxTries = 8;
    function waitForTma() {
      tries += 1;
      if (hasTelegramWebAppAuth() || tries >= maxTries) {
        initWhenReady();
        return;
      }
      setTimeout(waitForTma, 50);
    }
    waitForTma();
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
