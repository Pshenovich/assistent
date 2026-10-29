/* Карточка контакта → «Поделиться»: выбрать, кому из своих контактов отправить.
 * Получателю контакт сразу записывается, бот пишет, от кого он. */
(function () {
  "use strict";

  var state = { key: "", name: "", items: [], picked: {}, sending: false };

  function app() {
    return window.LeoApp || {};
  }
  function $(id) {
    return document.getElementById(id);
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function initials(name) {
    var w = String(name || "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
    return ((w[0] || "?").charAt(0) + (w[1] || "").charAt(0)).toUpperCase();
  }
  function setErr(text) {
    var el = $("contact-share-err");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  function pickedKeys() {
    return Object.keys(state.picked).filter(function (k) { return state.picked[k]; });
  }

  function syncSend() {
    var btn = $("contact-share-send");
    if (btn) btn.disabled = state.sending || !pickedKeys().length;
  }

  function renderList() {
    var list = $("contact-share-list");
    if (!list) return;
    var q = String(($("contact-share-search") || {}).value || "").trim().toLowerCase().replace(/^@/, "");
    var rows = state.items.filter(function (t) {
      return !q || t.name.toLowerCase().indexOf(q) >= 0 || String(t.sub || "").toLowerCase().replace(/^@/, "").indexOf(q) >= 0;
    });
    if (!rows.length) {
      list.innerHTML = '<p class="pf-hint">' + (state.items.length ? "Никого не нашли." : "В контактах пока некому отправить.") + "</p>";
      return;
    }
    list.innerHTML = rows
      .map(function (t) {
        var sub = t.can_receive ? t.sub : "Ещё не в Орбите";
        return (
          '<label class="pf-row' + (t.can_receive ? "" : " contact-share-off") + '">' +
          '<input type="checkbox" class="pf-check-input" data-key="' + esc(t.key) + '"' +
          (state.picked[t.key] ? " checked" : "") + (t.can_receive ? "" : " disabled") + " />" +
          '<span class="pf-avatar" aria-hidden="true">' + esc(initials(t.name)) + "</span>" +
          '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(t.name) + "</span>" +
          (sub ? '<span class="pf-row-sub">' + esc(sub) + "</span>" : "") +
          '</span><span class="pf-check" aria-hidden="true"></span></span></label>'
        );
      })
      .join("");
  }

  async function open(contact) {
    var key = String((contact && (contact.key || contact.email)) || "");
    if (!key) return;
    state = { key: key, name: String((contact && contact.name) || "").trim(), items: [], picked: {}, sending: false };
    var ov = $("contact-share-overlay");
    if (!ov) return;
    document.body.appendChild(ov); // поверх карточки контакта, которая создаётся в конце body
    var who = $("contact-share-who");
    if (who) who.textContent = state.name ? "Кому отправить контакт «" + state.name + "»?" : "";
    var search = $("contact-share-search");
    if (search) search.value = "";
    setErr("");
    $("contact-share-list").innerHTML = '<p class="pf-hint">Загружаю контакты…</p>';
    syncSend();
    ov.classList.remove("hidden");
    ov.setAttribute("aria-hidden", "false");
    if (app().syncAppOverlay) app().syncAppOverlay();
    try {
      var r = await app().apiFetch("/contact-share/targets?key=" + encodeURIComponent(key), { method: "GET" });
      state.items = (r && r.items) || [];
    } catch (e) {
      state.items = [];
      setErr((e && e.message) || "Не удалось загрузить контакты");
    }
    renderList();
  }

  function close() {
    var ov = $("contact-share-overlay");
    if (!ov) return;
    ov.classList.add("hidden");
    ov.setAttribute("aria-hidden", "true");
    if (app().syncAppOverlay) app().syncAppOverlay();
  }

  var STATUS = {
    added: "отправлен",
    existing: "у него уже был",
    not_in_orbita: "ещё не в Орбите",
  };

  async function send() {
    var keys = pickedKeys();
    if (!keys.length || state.sending) return;
    state.sending = true;
    syncSend();
    setErr("");
    try {
      var r = await app().apiFetch("/contact-share", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: state.key, to: keys }),
      });
      var res = (r && r.results) || [];
      var ok = res.filter(function (x) { return x.status === "added" || x.status === "existing"; }).length;
      var bad = res.filter(function (x) { return x.status !== "added" && x.status !== "existing"; });
      if (bad.length && !ok) {
        setErr("Не отправилось: " + bad.map(function (x) { return STATUS[x.status] || x.status; }).join(", "));
        return;
      }
      close();
      if (app().toast) app().toast(ok === 1 ? "Контакт отправлен" : "Контакт отправлен: " + ok + " получателям");
    } catch (e) {
      setErr((e && e.message) || "Не удалось отправить");
    } finally {
      state.sending = false;
      syncSend();
    }
  }

  function init() {
    if (!window.LeoApp) {
      setTimeout(init, 200);
      return;
    }
    var closeBtn = $("contact-share-close");
    if (closeBtn) closeBtn.addEventListener("click", close);
    var sendBtn = $("contact-share-send");
    if (sendBtn) sendBtn.addEventListener("click", send);
    var search = $("contact-share-search");
    if (search) search.addEventListener("input", renderList);
    var list = $("contact-share-list");
    if (list) {
      list.addEventListener("change", function (e) {
        var cb = e.target;
        if (!cb || !cb.getAttribute) return;
        var key = cb.getAttribute("data-key");
        if (!key) return;
        state.picked[key] = cb.checked;
        syncSend();
      });
    }
    var shareOv = $("contact-share-overlay");
    if (shareOv) {
      shareOv.addEventListener("click", function (e) {
        if (e.target === shareOv) close();
      });
    }
  }

  window.LeoContactShare = { open: open };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
