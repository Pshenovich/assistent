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
    if (!headings.length) {
      toc.classList.add("hidden");
      if (wrap) wrap.classList.remove("has-toc");
      return;
    }
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
    var marks = document.querySelectorAll("mark.note-comment-hl");
    marks.forEach(function (m) {
      m.classList.toggle("is-active", String(m.getAttribute("data-comment-id")) === String(id || ""));
    });
    var cards = document.querySelectorAll("#share-comments-list .share-comment");
    cards.forEach(function (c) {
      c.classList.toggle("is-active", String(c.getAttribute("data-comment-id")) === String(id || ""));
    });
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
      setActiveComment(c.id);
      var mark = commentsState.highlights[String(c.id)];
      if (mark && mark.scrollIntoView) mark.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    return item;
  }

  function relayoutCommentCards() {
    var api = commentsApi();
    var list = document.getElementById("share-comments-list");
    if (!api || !api.alignCards || !list) return;
    api.alignCards(list, commentsState.highlights);
  }

  function paintComments() {
    var api = commentsApi();
    var root = bodyRoot();
    var list = document.getElementById("share-comments-list");
    if (!list || !root) return;
    list.innerHTML = "";
    commentsState.highlights = api ? api.applyHighlights(root, commentsState.list) : {};
    commentsState.list.forEach(function (c) {
      list.appendChild(renderCommentCard(c));
    });
    window.requestAnimationFrame(relayoutCommentCards);
    root.querySelectorAll("mark.note-comment-hl").forEach(function (mark) {
      mark.addEventListener("click", function (e) {
        e.preventDefault();
        var id = mark.getAttribute("data-comment-id");
        setActiveComment(id);
        var card = list.querySelector('[data-comment-id="' + id + '"]');
        if (card && card.scrollIntoView) card.scrollIntoView({ block: "nearest" });
      });
    });
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
  }

  function showComposer(draft) {
    commentsState.draft = draft;
    var form = document.getElementById("share-comments-form");
    var quoteEl = document.getElementById("share-comments-quote");
    var input = document.getElementById("share-comments-input");
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
    box.classList.remove("hidden");
    if (wrap) wrap.classList.add("has-comments");
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
    window.addEventListener("scroll", relayoutCommentCards, { passive: true });
    window.addEventListener("resize", relayoutCommentCards);
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
