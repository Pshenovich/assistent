(function () {
  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function tokenFromPath() {
    var parts = String(location.pathname || "").split("/").filter(Boolean);
    var i = parts.indexOf("share");
    if (i < 0 || !parts[i + 1]) return "";
    return decodeURIComponent(parts[i + 1]);
  }

  function looksLikeHtml(s) {
    return /<\/?[a-z][\s\S]*>/i.test(String(s || ""));
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

  function plainToHtml(raw) {
    var blocks = String(raw || "")
      .replace(/\r\n/g, "\n")
      .trim()
      .split(/\n{2,}/);
    if (!blocks.length || (blocks.length === 1 && !blocks[0])) return "<p>—</p>";
    return blocks
      .map(function (block) {
        return "<p>" + escapeHtml(block).replace(/\n/g, "<br>") + "</p>";
      })
      .join("");
  }

  function renderBody(data) {
    var raw = String((data && data.body) || "").trim();
    var op = String((data && data.operation) || "");
    var kind = String((data && data.kind) || "");
    var api = window.NoteHtml;
    var rich =
      kind === "local" ||
      op === "summarize" ||
      op === "format_note" ||
      op === "answer_with_context";
    if (rich && api) {
      var html = raw;
      if (!looksLikeHtml(html) && looksLikeMarkdown(html) && typeof api.markdownToHtml === "function") {
        html = api.markdownToHtml(html);
      } else if (!looksLikeHtml(html)) {
        html = plainToHtml(html);
      }
      if (typeof api.enrichForDisplay === "function") {
        html = api.enrichForDisplay(html, { interactive: false });
      }
      return html || "<p>—</p>";
    }
    return '<pre class="transcript">' + escapeHtml(raw || "—") + "</pre>";
  }

  function headingLevel(el) {
    return Number(String((el && el.tagName) || "H2").replace(/^H/i, "")) || 2;
  }

  function headingIdBase(text, index) {
    var base = String(text || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9а-яё]+/gi, "-")
      .replace(/^-+|-+$/g, "");
    return "share-heading-" + (base || String(index + 1));
  }

  function ensureHeadingId(el, index) {
    if (el.id) return el.id;
    var base = headingIdBase(el.textContent, index);
    var id = base;
    var n = 2;
    while (document.getElementById(id) && document.getElementById(id) !== el) {
      id = base + "-" + n;
      n += 1;
    }
    el.id = id;
    return id;
  }

  function appendShareDiscussionToc() {
    var toc = document.getElementById("share-toc");
    var list = document.getElementById("share-toc-list");
    var wrap = document.querySelector(".wrap");
    var thread = document.getElementById("share-paie-thread");
    if (!toc || !list) return;
    var prev = list.querySelector("[data-toc-discussion]");
    if (prev && prev.parentNode) prev.parentNode.removeChild(prev);
    var hasThread = !!(thread && !thread.classList.contains("hidden"));
    if (hasThread) {
      var link = document.createElement("a");
      link.className = "share-toc-link share-toc-link--level-1";
      link.setAttribute("data-toc-discussion", "1");
      link.href = "#share-paie-thread";
      link.textContent = "Обсуждение";
      link.addEventListener("click", function (e) {
        e.preventDefault();
        if (thread && thread.scrollIntoView) {
          thread.scrollIntoView({ block: "start", behavior: "smooth" });
        }
      });
      list.appendChild(link);
    }
    if (!list.children.length) {
      toc.classList.add("hidden");
      if (wrap) wrap.classList.remove("has-toc");
      return;
    }
    toc.classList.remove("hidden");
    if (wrap) wrap.classList.add("has-toc");
  }

  function buildShareToc(bodyEl) {
    var toc = document.getElementById("share-toc");
    var list = document.getElementById("share-toc-list");
    var wrap = document.querySelector(".wrap");
    if (!toc || !list || !bodyEl) return;
    list.innerHTML = "";
    var headings = Array.prototype.slice.call(
      bodyEl.querySelectorAll("h1, h2, h3, h4, h5, h6")
    ).filter(function (el) {
      return String(el.textContent || "").trim();
    });
    headings.forEach(function (heading, index) {
      var id = ensureHeadingId(heading, index);
      var link = document.createElement("a");
      link.className = "share-toc-link share-toc-link--level-" + headingLevel(heading);
      link.href = "#" + encodeURIComponent(id);
      link.textContent = String(heading.textContent || "").trim();
      link.addEventListener("click", function (e) {
        e.preventDefault();
        heading.scrollIntoView({ block: "start", behavior: "smooth" });
        try {
          history.replaceState(null, "", "#" + encodeURIComponent(id));
        } catch (_) {}
      });
      list.appendChild(link);
    });
    appendShareDiscussionToc();
    if (!list.children.length) {
      toc.classList.add("hidden");
      if (wrap) wrap.classList.remove("has-toc");
      return;
    }
    toc.classList.remove("hidden");
    if (wrap) wrap.classList.add("has-toc");

    if (location.hash) {
      window.requestAnimationFrame(function () {
        var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        if (target && target.scrollIntoView) {
          target.scrollIntoView({ block: "start" });
        }
      });
    }
  }

  function showError(msg) {
    var status = document.getElementById("share-status");
    var err = document.getElementById("share-error");
    var doc = document.getElementById("share-doc");
    if (status) status.classList.add("hidden");
    if (doc) doc.classList.add("hidden");
    if (err) {
      err.textContent = msg || "Документ недоступен";
      err.classList.remove("hidden");
    }
  }

  var token = tokenFromPath();
  if (!token) {
    showError("Ссылка неполная");
    return;
  }

  fetch("/api/public/share/" + encodeURIComponent(token), {
    headers: { Accept: "application/json" },
  })
    .then(function (res) {
      return res.text().then(function (text) {
        var body = null;
        try {
          body = text ? JSON.parse(text) : null;
        } catch (_) {
          body = null;
        }
        if (!res.ok) {
          var detail = body && (body.detail || body.message);
          throw new Error(typeof detail === "string" ? detail : "Документ недоступен");
        }
        return body;
      });
    })
    .then(function (data) {
      var title = String((data && data.title) || "Без названия");
      var label = String((data && data.label) || "Документ");
      document.title = title;
      var status = document.getElementById("share-status");
      var doc = document.getElementById("share-doc");
      var labelEl = document.getElementById("share-label");
      var titleEl = document.getElementById("share-title");
      var bodyEl = document.getElementById("share-body");
      if (status) status.classList.add("hidden");
      if (labelEl) labelEl.textContent = label;
      if (titleEl) titleEl.textContent = title;
      if (bodyEl) {
        bodyEl.innerHTML = renderBody(data);
        buildShareToc(bodyEl);
      }
      if (doc) doc.classList.remove("hidden");
      setupShareComments(data && data.access === "comment");
    })
    .catch(function (e) {
      showError(e.message || "Документ недоступен");
    });

  function jsonFetch(url, opts) {
    var o = opts || {};
    return fetch(url, Object.assign({ credentials: "same-origin" }, o)).then(function (res) {
      return res.text().then(function (text) {
        var body = null;
        try {
          body = text ? JSON.parse(text) : null;
        } catch (_) {
          body = null;
        }
        if (!res.ok) {
          var detail = body && (body.detail || body.message);
          var err = new Error(typeof detail === "string" ? detail : "Ошибка запроса");
          err.status = res.status;
          throw err;
        }
        return body;
      });
    });
  }

  function formatWhen(iso) {
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

  function authorLabel(c) {
    var api = commentsApi();
    if (api && api.isPaieComment && api.isPaieComment(c)) return "CHAIR";
    if (api && api.isGptComment && api.isGptComment(c)) return "GPT";
    var name = String((c && c.author_name) || "Пользователь").trim();
    var uname = String((c && c.author_username) || "").trim();
    if (uname && name.toLowerCase() !== "@" + uname.toLowerCase()) {
      return name + " · @" + uname;
    }
    return name;
  }

  function setCommentsError(msg) {
    var el = document.getElementById("share-comments-error");
    if (!el) return;
    if (msg) {
      el.textContent = msg;
      el.classList.remove("hidden");
    } else {
      el.textContent = "";
      el.classList.add("hidden");
    }
  }

  var commentsState = {
    me: null,
    list: [],
    highlights: {},
    draft: null,
    canComment: false,
    activeId: null,
  };
  var PENDING_KEY = "leo_share_pending_comment";

  function commentsApi() {
    return window.NoteComments;
  }

  function bodyRoot() {
    return document.getElementById("share-body");
  }

  function savePending(draft) {
    try {
      sessionStorage.setItem(PENDING_KEY, JSON.stringify(draft || {}));
    } catch (_) {}
  }

  function takePending() {
    try {
      var raw = sessionStorage.getItem(PENDING_KEY);
      sessionStorage.removeItem(PENDING_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function setActiveComment(id) {
    var marks = document.querySelectorAll(
      "mark.note-comment-hl, .note-comment-overlay-hl, .note-comment-overlay-bar"
    );
    marks.forEach(function (m) {
      m.classList.toggle("is-active", String(m.getAttribute("data-comment-id")) === String(id || ""));
    });
    var cards = document.querySelectorAll("#share-comments-list .share-comment");
    cards.forEach(function (c) {
      c.classList.toggle("is-active", String(c.getAttribute("data-comment-id")) === String(id || ""));
    });
    commentsState.activeId = id || null;
  }

  function renderCommentCard(c) {
    var item = document.createElement("article");
    item.className = "share-comment";
    item.setAttribute("data-comment-id", String(c.id));
    if (c.quote) {
      var q = document.createElement("p");
      q.className = "share-comment-quote";
      q.textContent = "«" + String(c.quote) + "»";
      item.appendChild(q);
    }
    var head = document.createElement("div");
    head.className = "share-comment-head";
    var who = document.createElement("strong");
    who.textContent = authorLabel(c);
    var when = document.createElement("time");
    when.textContent = formatWhen(c.created_at);
    head.appendChild(who);
    head.appendChild(when);
    var body = document.createElement("p");
    body.className = "share-comment-body";
    body.textContent = String((c && c.body) || "");
    item.appendChild(head);
    item.appendChild(body);
    if (c && c.can_delete) {
      var del = document.createElement("button");
      del.type = "button";
      del.className = "share-comment-delete";
      del.textContent = "Удалить";
      del.addEventListener("click", function (e) {
        e.stopPropagation();
        if (!confirm("Удалить комментарий?")) return;
        jsonFetch("/api/public/share/" + encodeURIComponent(token) + "/comments/" + encodeURIComponent(String(c.id)), {
          method: "DELETE",
        })
          .then(loadComments)
          .catch(function (err) {
            setCommentsError(err.message || "Не удалось удалить");
          });
      });
      item.appendChild(del);
    }
    item.addEventListener("click", function () {
      commentsState.activeId = c.id;
      setActiveComment(c.id);
      paintShareHighlights(c.id);
      var api = commentsApi();
      var root = bodyRoot();
      var range =
        api && api.rangeForAnchor && c.quote
          ? api.rangeForAnchor(root, c.quote, c.prefix, c.suffix)
          : null;
      if (range) {
        var node = range.startContainer;
        var el = node && node.nodeType === 1 ? node : node && node.parentElement;
        if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      window.requestAnimationFrame(function () {
        paintShareHighlights(c.id);
        setActiveComment(c.id);
        relayoutCommentCards();
      });
    });
    return item;
  }

  function relayoutCommentCards() {
    var api = commentsApi();
    var list = document.getElementById("share-comments-list");
    var root = bodyRoot();
    if (!api || !api.alignCardsByAnchors || !list) return;
    var anchored =
      api.selectionComments && commentsState.list
        ? api.selectionComments(commentsState.list)
        : commentsState.list;
    api.alignCardsByAnchors(list, root, anchored || []);
  }

  function paintShareHighlights(activeId) {
    var api = commentsApi();
    var root = bodyRoot();
    var overlay = document.getElementById("share-comment-overlay");
    var list = document.getElementById("share-comments-list");
    if (!api || !api.paintOverlay || !overlay || !root) return;
    var anchored =
      api.selectionComments && commentsState.list
        ? api.selectionComments(commentsState.list)
        : commentsState.list;
    api.paintOverlay(root, overlay, anchored || [], activeId || commentsState.activeId);
    overlay.querySelectorAll(".note-comment-overlay-hl").forEach(function (span) {
      span.style.pointerEvents = "auto";
      span.style.cursor = "pointer";
      span.addEventListener("click", function (e) {
        e.preventDefault();
        var id = span.getAttribute("data-comment-id");
        var found = null;
        (commentsState.list || []).forEach(function (c) {
          if (c && String(c.id) === String(id)) found = c;
        });
        if (api.isMobileCommentsLayout && api.isMobileCommentsLayout()) {
          if (found && api.showCommentSheet) api.showCommentSheet(found);
          return;
        }
        setActiveComment(id);
        paintShareHighlights(id);
        var card = list && list.querySelector('[data-comment-id="' + id + '"]');
        if (card && card.scrollIntoView) card.scrollIntoView({ block: "nearest" });
      });
    });
  }

  function renderPaieCard(c, opts) {
    opts = opts || {};
    var item = renderCommentCard(c);
    var api = commentsApi();
    var chair = !!(api && api.isPaieComment && api.isPaieComment(c));
    item.classList.add("share-paie-msg");
    item.classList.add(chair ? "is-chair" : "is-reply");
    if (chair || opts.stripQuote) {
      var quote = item.querySelector(".share-comment-quote");
      if (quote && quote.parentNode) quote.parentNode.removeChild(quote);
    }
    return item;
  }

  function openShareChairCommentForm(groupEl, draft) {
    if (!groupEl) return;
    if (!commentsState.canComment) return;
    if (!commentsState.me) {
      showLogin();
      setCommentsError("Войдите через Telegram, чтобы комментировать ответ CHAIR");
      return;
    }
    var form = groupEl.querySelector(".share-paie-comment-form");
    var quoteEl = groupEl.querySelector(".share-paie-comment-quote");
    var input = groupEl.querySelector(".share-paie-comment-input");
    if (!form) return;
    form.classList.remove("hidden");
    form._draft = draft || null;
    if (quoteEl) {
      if (draft && draft.quote) {
        quoteEl.textContent = "«" + String(draft.quote) + "»";
        quoteEl.classList.remove("hidden");
      } else {
        quoteEl.textContent = "";
        quoteEl.classList.add("hidden");
      }
    }
    if (input) {
      try {
        input.focus();
      } catch (_) {}
    }
  }

  function postShareChairComment(chairId, text, draft, input) {
    var body = String(text || "").trim();
    if (!body) return;
    jsonFetch("/api/public/share/" + encodeURIComponent(token) + "/comments", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        body: body,
        quote: (draft && draft.quote) || "",
        prefix: (draft && draft.prefix) || "",
        suffix: (draft && draft.suffix) || "",
        parent_id: chairId,
      }),
    })
      .then(function () {
        if (input) input.value = "";
        setCommentsError("");
        return loadComments();
      })
      .catch(function (err) {
        if (err.status === 401) {
          showLogin();
          setCommentsError("Войдите через Telegram, чтобы комментировать");
          return;
        }
        setCommentsError(err.message || "Не удалось отправить");
      });
  }

  function renderShareChairGroup(group) {
    var wrap = document.createElement("div");
    wrap.className = "share-paie-group";
    wrap.setAttribute("data-chair-id", String(group.chair.id));
    wrap.appendChild(renderPaieCard(group.chair, { stripQuote: true }));
    if (commentsState.canComment) {
      var actions = document.createElement("div");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "share-paie-comment-btn";
      btn.textContent = "Комментировать";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        openShareChairCommentForm(wrap, null);
      });
      actions.appendChild(btn);
      wrap.appendChild(actions);
    }
    var nested = document.createElement("div");
    nested.className = "share-paie-nested";
    (group.comments || []).forEach(function (c) {
      nested.appendChild(renderPaieCard(c));
    });
    wrap.appendChild(nested);
    if (commentsState.canComment) {
      var form = document.createElement("form");
      form.className = "share-comments-form share-paie-comment-form hidden";
      form.innerHTML =
        '<p class="share-comment-quote share-paie-comment-quote hidden"></p>' +
        '<textarea class="share-comments-input share-paie-comment-input" rows="3" maxlength="4000" placeholder="Комментарий к ответу CHAIR"></textarea>' +
        '<div class="share-comments-form-actions">' +
        '<button type="submit" class="share-comments-btn">Отправить</button>' +
        '<button type="button" class="share-comments-btn share-comments-btn--ghost">Отмена</button>' +
        "</div>";
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var input = form.querySelector(".share-paie-comment-input");
        postShareChairComment(group.chair.id, input && input.value, form._draft, input);
      });
      var cancel = form.querySelector(".share-comments-btn--ghost");
      if (cancel) {
        cancel.addEventListener("click", function () {
          form.classList.add("hidden");
          form._draft = null;
        });
      }
      wrap.appendChild(form);
    }
    return wrap;
  }

  function renderSharePaieThread(thread) {
    var box = document.getElementById("share-paie-thread");
    var list = document.getElementById("share-paie-list");
    var form = document.getElementById("share-paie-reply-form");
    var api = commentsApi();
    if (!box || !list) return;
    var groups = api && api.paieThreadGroups ? api.paieThreadGroups(thread) : [];
    list.innerHTML = "";
    groups.forEach(function (group) {
      list.appendChild(renderShareChairGroup(group));
    });
    box.classList.toggle("hidden", !thread || !thread.length);
    if (form) form.classList.add("hidden");
  }

  function paintComments() {
    var api = commentsApi();
    var root = bodyRoot();
    var list = document.getElementById("share-comments-list");
    var rail = document.getElementById("share-comments");
    var wrap = document.querySelector(".wrap");
    if (!list || !root) return;
    var all = commentsState.list || [];
    var thread = api && api.paieThread ? api.paieThread(all) : [];
    var anchored = api && api.selectionComments ? api.selectionComments(all) : all;
    list.innerHTML = "";
    paintShareHighlights(commentsState.activeId);
    anchored.forEach(function (c) {
      list.appendChild(renderCommentCard(c));
    });
    renderSharePaieThread(thread);
    appendShareDiscussionToc();
    var title = document.querySelector("#share-comments .share-comments-title");
    if (title) title.classList.add("hidden");
    if (rail) rail.classList.toggle("hidden", !anchored.length);
    if (wrap) wrap.classList.toggle("has-comments", !!anchored.length);
    window.requestAnimationFrame(relayoutCommentCards);
  }

  function loadComments() {
    return jsonFetch("/api/public/share/" + encodeURIComponent(token) + "/comments")
      .then(function (data) {
        setCommentsError("");
        commentsState.list = (data && data.comments) || [];
        paintComments();
      })
      .catch(function (e) {
        setCommentsError(e.message || "Не удалось загрузить комментарии");
      });
  }

  function hideComposer() {
    commentsState.draft = null;
    var form = document.getElementById("share-comments-form");
    if (form) form.classList.add("hidden");
    var api = commentsApi();
    var anchored =
      api && api.selectionComments ? api.selectionComments(commentsState.list) : commentsState.list;
    var rail = document.getElementById("share-comments");
    var wrap = document.querySelector(".wrap");
    if (rail) rail.classList.toggle("hidden", !(anchored && anchored.length));
    if (wrap) wrap.classList.toggle("has-comments", !!(anchored && anchored.length));
  }

  function showComposer(draft) {
    commentsState.draft = draft;
    var form = document.getElementById("share-comments-form");
    var quoteEl = document.getElementById("share-comments-quote");
    var input = document.getElementById("share-comments-input");
    var rail = document.getElementById("share-comments");
    var wrap = document.querySelector(".wrap");
    if (rail) rail.classList.remove("hidden");
    if (wrap) wrap.classList.add("has-comments");
    if (quoteEl) quoteEl.textContent = draft && draft.quote ? "«" + draft.quote + "»" : "";
    if (form) form.classList.remove("hidden");
    if (input) {
      input.value = "";
      input.focus();
    }
  }

  function startCommentFromSelection() {
    var api = commentsApi();
    var root = bodyRoot();
    var draft = api && api.selectionAnchor(root);
    if (!draft || !draft.quote) return;
    if (!commentsState.me) {
      savePending(draft);
      showLogin();
      setCommentsError("Войдите через Telegram, чтобы комментировать выделенный текст");
      return;
    }
    showComposer(draft);
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

  function setLoginReturnCookie() {
    document.cookie = "leo_login_return=/share/" + encodeURIComponent(token) + "; Path=/; Max-Age=600; SameSite=Lax";
  }

  function completeTelegramLogin(user) {
    return jsonFetch("/api/miniapp/auth/telegram", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(user),
    });
  }

  function startTelegramOAuth(botId) {
    setLoginReturnCookie();
    var returnTo = window.location.origin + "/share/" + encodeURIComponent(token);
    window.location.href =
      "https://oauth.telegram.org/auth?bot_id=" +
      encodeURIComponent(String(botId)) +
      "&origin=" +
      encodeURIComponent(window.location.origin) +
      "&return_to=" +
      encodeURIComponent(returnTo);
  }

  function showLoggedIn(me) {
    commentsState.me = me || null;
    var login = document.getElementById("share-comments-login");
    var meEl = document.getElementById("share-comments-me");
    if (login) login.classList.add("hidden");
    if (meEl) {
      var name = String((me && me.first_name) || "").trim() || (me && me.username ? "@" + me.username : "Вы");
      meEl.textContent = "Комментируете как " + name;
    }
  }

  function showLogin() {
    commentsState.me = null;
    var login = document.getElementById("share-comments-login");
    var form = document.getElementById("share-comments-form");
    if (login) login.classList.remove("hidden");
    if (form) form.classList.add("hidden");
  }

  function setupShareComments(canComment) {
    var box = document.getElementById("share-comments");
    var wrap = document.querySelector(".wrap");
    var root = bodyRoot();
    var api = commentsApi();
    if (!box || !root) return;
    commentsState.canComment = !!canComment;
    box.classList.add("hidden");
    var hint = document.getElementById("share-comments-hint");
    var login = document.getElementById("share-comments-login");
    if (!canComment) {
      if (hint) hint.classList.add("hidden");
      if (login) login.classList.add("hidden");
    }
    var form = document.getElementById("share-comments-form");
    var input = document.getElementById("share-comments-input");
    var loginBtn = document.getElementById("share-comments-login-btn");
    var cancelBtn = document.getElementById("share-comments-cancel");
    if (canComment && form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = String((input && input.value) || "").trim();
        var draft = commentsState.draft;
        if (!text) return;
        if (!draft || !draft.quote) {
          setCommentsError("Выделите текст в документе");
          return;
        }
        jsonFetch("/api/public/share/" + encodeURIComponent(token) + "/comments", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            body: text,
            quote: draft.quote,
            prefix: draft.prefix || "",
            suffix: draft.suffix || "",
          }),
        })
          .then(function () {
            if (input) input.value = "";
            hideComposer();
            setCommentsError("");
            return loadComments();
          })
          .catch(function (e) {
            if (e.status === 401) {
              savePending(draft);
              showLogin();
              setCommentsError("Войдите через Telegram, чтобы комментировать");
              return;
            }
            setCommentsError(e.message || "Не удалось отправить");
          });
      });
    }
    if (canComment && cancelBtn) cancelBtn.addEventListener("click", hideComposer);
    var paieForm = document.getElementById("share-paie-reply-form");
    var paieInput = document.getElementById("share-paie-reply-input");
    if (canComment && paieForm && !paieForm._bound) {
      paieForm._bound = true;
      paieForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = String((paieInput && paieInput.value) || "").trim();
        if (!text) return;
        if (!commentsState.me) {
          showLogin();
          setCommentsError("Войдите через Telegram, чтобы ответить");
          return;
        }
        var api = commentsApi();
        var rootId = api && api.paieThreadRootId ? api.paieThreadRootId(commentsState.list) : null;
        jsonFetch("/api/public/share/" + encodeURIComponent(token) + "/comments", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            body: text,
            quote: "",
            prefix: "",
            suffix: "",
            parent_id: rootId,
          }),
        })
          .then(function () {
            if (paieInput) paieInput.value = "";
            setCommentsError("");
            return loadComments();
          })
          .catch(function (err) {
            if (err.status === 401) {
              showLogin();
              setCommentsError("Войдите через Telegram, чтобы ответить");
              return;
            }
            setCommentsError(err.message || "Не удалось отправить");
          });
      });
    }
    if (canComment && loginBtn) {
      loginBtn.addEventListener("click", function () {
        if (commentsState.draft) savePending(commentsState.draft);
        jsonFetch("/api/miniapp/auth/config")
          .then(function (cfg) {
            if (!cfg || !cfg.telegram_login_enabled || !cfg.bot_id) {
              throw new Error("Вход через Telegram сейчас недоступен");
            }
            startTelegramOAuth(cfg.bot_id);
          })
          .catch(function (e) {
            setCommentsError(e.message || "Вход недоступен");
          });
      });
    }
    function onSelect(e) {
      if (!canComment || !api) return;
      if (e && api.isCommentBubbleEvent && api.isCommentBubbleEvent(e)) return;
      var draft = api.selectionAnchor(root);
      if (!draft || !draft.quote) {
        api.hideBubble();
        return;
      }
      api.showBubble(draft.rect, startCommentFromSelection);
    }
    root.addEventListener("mouseup", onSelect);
    root.addEventListener("pointerup", onSelect);
    root.addEventListener("keyup", onSelect);
    document.addEventListener("mousedown", function (e) {
      if (api && api.isCommentBubbleEvent && api.isCommentBubbleEvent(e)) return;
      if (api) api.hideBubble();
    });
    function onShareLayout() {
      relayoutCommentCards();
      paintShareHighlights(commentsState.activeId);
    }
    window.addEventListener("scroll", onShareLayout, { passive: true });
    window.addEventListener("resize", onShareLayout);
    var paieBox = document.getElementById("share-paie-thread");
    if (paieBox && !paieBox._chairSelectBound) {
      paieBox._chairSelectBound = true;
      function onChairSelect() {
        if (!canComment || !api) return;
        var sel = window.getSelection && window.getSelection();
        if (!sel || sel.rangeCount < 1 || sel.isCollapsed) return;
        var node = sel.anchorNode && sel.anchorNode.parentElement;
        var body =
          node && node.closest
            ? node.closest(".share-paie-msg.is-chair .share-comment-body")
            : null;
        if (!body) return;
        var draft = api.selectionAnchor(body);
        if (!draft || !draft.quote) return;
        var group = body.closest(".share-paie-group");
        if (group) openShareChairCommentForm(group, draft);
      }
      paieBox.addEventListener("mouseup", onChairSelect);
      paieBox.addEventListener("pointerup", onChairSelect);
    }
    if (!canComment) {
      loadComments();
      return;
    }
    var pendingAuth = parseTgAuthResultFromHash();
    var boot = Promise.resolve();
    if (pendingAuth && pendingAuth.hash) {
      clearTgAuthHash();
      boot = completeTelegramLogin(pendingAuth).catch(function (e) {
        setCommentsError(e.message || "Не удалось войти");
      });
    }
    boot
      .then(function () {
        return jsonFetch("/api/public/share/" + encodeURIComponent(token) + "/me");
      })
      .then(function (data) {
        if (data && data.user) {
          showLoggedIn(data.user);
          var pending = takePending();
          if (pending && pending.quote) showComposer(pending);
        } else {
          showLogin();
        }
      })
      .catch(function () {
        showLogin();
      })
      .then(loadComments);
  }
})();
