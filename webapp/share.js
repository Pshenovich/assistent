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
      if (bodyEl) bodyEl.innerHTML = renderBody(data);
      if (doc) doc.classList.remove("hidden");
    })
    .catch(function (e) {
      showError(e.message || "Документ недоступен");
    });
})();
