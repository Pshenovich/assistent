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
    })
    .catch(function (e) {
      showError(e.message || "Документ недоступен");
    });
})();
