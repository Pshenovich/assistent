/* «Дайджест» → «Почта»: переписка с людьми как мессенджер (по умолчанию) или классическим видом «Письма».
 * Данные — /mail/threads; ответ — поле ввода внизу ветки или полное окно письма (LeoMail.openComposer). */
(function () {
  "use strict";

  function app() {
    return window.LeoApp || {};
  }
  function M() {
    return window.LeoMail || {};
  }
  function esc(s) {
    return M().esc ? M().esc(s) : String(s == null ? "" : s);
  }
  function api(path, opts) {
    return app().apiFetch(path, opts || { method: "GET" });
  }
  function post(path, body, method) {
    return api(path, { method: method || "POST", body: JSON.stringify(body || {}) });
  }
  function toast(t) {
    if (app().toast) app().toast(t);
  }

  var st = {
    filter: "all",
    account: "",
    data: null,
    view: "chat",
    open: null, // открытая ветка
    loading: false,
    replyTo: "",
    shown: {}, // mail_key → {full, images, sig, more}
    summary: null,
    pending: null, // отправляемый ответ {action, text}
    menu: false,
  };

  function root() {
    return document.getElementById("mail-inbox");
  }

  // На компьютере — как «Сохранённое»: список слева, переписка справа; на телефоне — по одному экрану.
  function isWide() {
    return window.matchMedia && window.matchMedia("(min-width: 900px)").matches;
  }

  function slots() {
    var r = root();
    if (!r) return {};
    var sp = r.querySelector(".mail-split");
    if (!isWide()) {
      if (sp) r.innerHTML = "";
      return { list: r, detail: r };
    }
    if (!sp) {
      r.innerHTML = '<div class="mail-split"><div class="mail-split-list"></div><div class="mail-split-detail"></div></div>';
      sp = r.querySelector(".mail-split");
    }
    return { list: sp.children[0], detail: sp.children[1] };
  }

  function renderPlaceholder() {
    var d = slots().detail;
    if (d && isWide() && !st.open) d.innerHTML = '<div class="mail-empty mail-empty--big">Выберите переписку слева</div>';
  }

  async function show() {
    await loadSettings();
    if (st.open && !isWide()) return renderThread();
    await refresh();
  }

  async function loadSettings() {
    try {
      var s = await api("/mail/settings");
      st.view = (s && s.view) || "chat";
    } catch (_) {}
  }

  async function refresh() {
    if (st.open && !isWide()) return openThread(st.open.thread_key, true);
    st.loading = !st.data;
    renderList();
    try {
      st.data = await api("/mail/threads?filter=" + encodeURIComponent(st.filter) + (st.account ? "&account=" + encodeURIComponent(st.account) : ""));
    } catch (e) {
      st.data = { error: (e && e.message) || "Не удалось загрузить почту" };
    }
    st.loading = false;
    renderList();
    if (isWide()) {
      if (!st.open && st.data && (st.data.threads || []).length) return openThread(st.data.threads[0].thread_key);
      if (st.open) return openThread(st.open.thread_key, true);
      renderPlaceholder();
    }
  }

  /* ——— список веток ——— */

  var COLORS = ["#5b8def", "#e8833a", "#3fb27f", "#c05bd6", "#d6455b", "#2fa4b8", "#b8962f", "#7a6ff0"];

  function avatar(name, addr) {
    var base = String(name || addr || "?").trim();
    var parts = base.replace(/[<>"@].*$/, "").split(/\s+/).filter(Boolean);
    var ini = ((parts[0] || "?")[0] + ((parts[1] || "")[0] || "")).toUpperCase();
    var h = 0;
    for (var i = 0; i < (addr || base).length; i++) h = (h * 31 + (addr || base).charCodeAt(i)) >>> 0;
    return '<span class="mail-ava" style="background:' + COLORS[h % COLORS.length] + '">' + esc(ini) + "</span>";
  }

  function peopleLine(people) {
    var names = (people || []).map(function (p) {
      return (p.name || p.addr || "").split(" ")[0] || p.addr;
    });
    if (!names.length) return "Без адресата";
    if ((people || []).length === 1) return people[0].name || people[0].addr;
    return names.slice(0, 2).join(", ") + (names.length > 2 ? " и ещё " + (names.length - 2) : "");
  }

  function when(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var now = new Date();
    if (d.toDateString() === now.toDateString()) return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    var y = new Date(now);
    y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return "вчера";
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
  }

  function viewSwitchHtml() {
    return (
      '<div class="mail-seg" role="tablist" aria-label="Вид">' +
      '<button type="button" class="mail-seg-btn' + (st.view === "chat" ? " is-on" : "") + '" data-mi="view" data-view="chat">Чат</button>' +
      '<button type="button" class="mail-seg-btn' + (st.view === "letters" ? " is-on" : "") + '" data-mi="view" data-view="letters">Письма</button></div>'
    );
  }

  function renderList() {
    var r = slots().list;
    if (!r) return;
    var d = st.data || {};
    var h = '<div class="mail-inbox-bar">';
    var counts = d.counts || {};
    [
      ["all", "Все", counts.all],
      ["awaiting", "Ждут ответа", counts.awaiting],
      ["unread", "Непрочитанные", counts.unread],
    ].forEach(function (f) {
      h +=
        '<button type="button" class="mail-chip-filter' + (st.filter === f[0] ? " is-on" : "") + '" data-mi="filter" data-f="' + f[0] + '">' +
        f[1] + (f[2] ? " · " + f[2] : "") + "</button>";
    });
    if ((d.accounts || []).length > 1) {
      h += '<select class="mail-acc-select" data-mi="account"><option value="">Все ящики</option>';
      d.accounts.forEach(function (a) {
        h += '<option value="' + esc(a.account_id) + '"' + (st.account === a.account_id ? " selected" : "") + ">" + esc(a.email) + "</option>";
      });
      h += "</select>";
    }
    h += '<span class="mail-bar-spacer"></span>' + viewSwitchHtml() + "</div>";
    if (st.loading) h += '<div class="mail-empty">Загружаю почту…</div>';
    else if (d.error) h += '<div class="mail-empty">' + esc(d.error) + ' <button type="button" class="pf-link" data-mi="retry">Повторить</button></div>';
    else if (!(d.accounts || []).length)
      h += '<div class="mail-empty">Почта не подключена. <button type="button" class="pf-link" data-mi="connect">Подключить</button></div>';
    else if (!(d.threads || []).length)
      h +=
        '<div class="mail-empty">' +
        (st.filter === "awaiting" ? "Никто не ждёт вашего ответа 🎉" : st.filter === "unread" ? "Всё прочитано" : "Писем от людей пока нет — рассылки Лео отсеивает") +
        "</div>";
    else {
      h += '<div class="mail-threads">';
      d.threads.forEach(function (t) {
        var p0 = (t.people || [])[0] || {};
        var last = t.last || {};
        var sel = isWide() && st.open && st.open.thread_key === t.thread_key;
        h +=
          '<button type="button" class="mail-thread-row' + (t.unread ? " is-unread" : "") + (sel ? " is-sel" : "") + '" data-mi="open" data-tk="' + esc(t.thread_key) + '">' +
          avatar(p0.name, p0.addr) +
          '<span class="mail-thread-main"><span class="mail-thread-top"><span class="mail-thread-who">' + esc(peopleLine(t.people)) + "</span>" +
          '<span class="mail-thread-time">' + esc(when(last.date)) + "</span></span>" +
          '<span class="mail-thread-subj">' + esc(t.subject) + (t.count > 1 ? ' <span class="mail-thread-count">' + t.count + "</span>" : "") + "</span>" +
          '<span class="mail-thread-snip">' + (last.direction === "out" ? "<b>Вы:</b> " : "") + esc(last.snippet || "") + "</span>";
        if ((t.labels || []).length) {
          h += '<span class="mail-labels">';
          t.labels.forEach(function (l) {
            h += '<span class="mail-label mail-label--' + esc(l.kind) + '">' + esc(l.text) + "</span>";
          });
          h += "</span>";
        }
        h += "</span>" + (t.unread ? '<span class="mail-unread">' + t.unread + "</span>" : "") + "</button>";
      });
      h += "</div>";
    }
    r.innerHTML = h;
  }

  /* ——— ветка ——— */

  async function openThread(tk, keepScroll) {
    var r = root();
    var scroller = r && r.querySelector(".mail-msgs");
    var prevScroll = keepScroll && scroller ? scroller.scrollTop : null;
    if (!st.open || st.open.thread_key !== tk) {
      st.open = { thread_key: tk, loading: true, messages: [] };
      st.replyTo = "";
      st.summary = null;
      st.menu = false;
      renderThread();
    }
    try {
      var t = await api("/mail/threads/" + encodeURIComponent(tk));
      st.open = t;
      renderThread();
      if (isWide()) renderList();
      var list = r && r.querySelector(".mail-msgs");
      if (list) list.scrollTop = prevScroll != null ? prevScroll : list.scrollHeight;
      if ((t.messages || []).some(function (m) { return m.unread; })) {
        post("/mail/threads/" + encodeURIComponent(tk) + "/read", { read: true }).catch(function () {});
        if (st.data) (st.data.threads || []).forEach(function (x) { if (x.thread_key === tk) x.unread = 0; });
      }
    } catch (e) {
      if (st.open && st.open.thread_key === tk && (st.open.messages || []).length && !st.open.loading) {
        // повторная загрузка не удалась — показанное письмо не убираем
        toast("Не удалось обновить переписку — показана последняя загруженная версия");
        return;
      }
      st.open = { thread_key: tk, error: (e && e.message) || "Не удалось загрузить переписку", messages: [] };
      renderThread();
    }
  }

  function closeThread() {
    if (window.LeoMail && document.getElementById("mail-composer") && !document.getElementById("mail-composer").classList.contains("hidden")) {
      var x = document.querySelector("#mail-composer [data-mc=close]");
      if (x) x.click();
    }
    st.open = null;
    st.pending = null;
    document.documentElement.classList.remove("mail-thread-open");
    if (isWide()) {
      renderList();
      return renderPlaceholder();
    }
    refresh();
  }

  function msgByKey(k) {
    return ((st.open && st.open.messages) || []).filter(function (m) {
      return m.mail_key === k;
    })[0];
  }

  function plainSnippet(m, n) {
    return String((m && m.text) || "").replace(/\s+/g, " ").slice(0, n || 90);
  }

  function bodyHtml(m) {
    var s = st.shown[m.mail_key] || {};
    var html = s.full && m.html_full ? m.html_full : m.html;
    if (s.images) html = html.replace(/\sdata-src=/g, " src=");
    return html;
  }

  function attachmentsHtml(m) {
    if (!(m.attachments || []).length) return "";
    var h = '<div class="mail-files">';
    m.attachments.forEach(function (f) {
      h +=
        '<button type="button" class="mail-file" data-mi="file" data-key="' + esc(m.mail_key) + '" data-part="' + f.part + '">' +
        '<span class="mail-file-ico">' + (/^image\//.test(f.type) ? "🖼" : /pdf/.test(f.type) ? "📄" : "📎") + "</span>" +
        '<span class="mail-file-name">' + esc(f.name) + '</span><span class="mail-file-size">' + fmtSize(f.size) + "</span></button>";
    });
    return h + "</div>";
  }

  function fmtSize(b) {
    b = Number(b || 0);
    return b > 1048576 ? (b / 1048576).toFixed(1) + " МБ" : Math.max(1, Math.round(b / 1024)) + " КБ";
  }

  function bubbleTools(m) {
    var s = st.shown[m.mail_key] || {};
    var t = "";
    if (m.remote_images && !s.images) t += '<button type="button" class="mail-mini" data-mi="images" data-key="' + esc(m.mail_key) + '">Показать картинки</button>';
    if (m.html_full && m.html_full !== m.html)
      t += '<button type="button" class="mail-mini" data-mi="full" data-key="' + esc(m.mail_key) + '">' + (s.full ? "Только новое" : "Показать целиком") + "</button>";
    if (m.html_full) t += '<button type="button" class="mail-mini" data-mi="orig" data-key="' + esc(m.mail_key) + '">Оригинал</button>';
    return t ? '<div class="mail-bubble-tools">' + t + "</div>" : "";
  }

  function chatHtml(t) {
    var group = (t.people || []).length > 1;
    var h = "";
    var prevSubj = "";
    var prevKey = "";
    (t.messages || []).forEach(function (m) {
      var subj = String(m.subject || "").replace(/^(\s*(re|fwd?|ответ)\s*:\s*)+/i, "").trim();
      if (prevSubj && subj && subj !== prevSubj) h += '<div class="mail-sys">Тема: ' + esc(subj) + "</div>";
      if (subj) prevSubj = subj;
      var out = m.direction === "out";
      var s = st.shown[m.mail_key] || {};
      h += '<div class="mail-msg' + (out ? " is-out" : "") + (m.rich ? " is-rich" : "") + '" data-key="' + esc(m.mail_key) + '">';
      if (!out && group) h += '<div class="mail-msg-who">' + esc(m.from) + "</div>";
      h += '<div class="mail-bubble' + (s.more ? " is-expanded" : "") + '" data-mi="bubble" data-key="' + esc(m.mail_key) + '">';
      if (m.reply_to_key && m.reply_to_key !== prevKey) {
        var rm = msgByKey(m.reply_to_key);
        if (rm)
          h +=
            '<button type="button" class="mail-reply-quote" data-mi="jump" data-key="' + esc(rm.mail_key) + '"><b>' +
            esc(rm.direction === "out" ? "Вы" : rm.from) + "</b><span>" + esc(plainSnippet(rm)) + "</span></button>";
      }
      h += '<div class="mail-bubble-body mail-html' + (m.rich ? " mail-paper" : "") + '">' + (m.loaded === false && !m.html ? "<p>…</p>" : bodyHtml(m)) + "</div>";
      h += '<button type="button" class="mail-more hidden" data-mi="more" data-key="' + esc(m.mail_key) + '">Показать полностью</button>';
      h += bubbleTools(m);
      h += '<div class="mail-bubble-meta">' + esc(when(m.date)) + (out ? " ✓" : "") + "</div>";
      h += '<button type="button" class="mail-reply-btn" data-mi="reply" data-key="' + esc(m.mail_key) + '" aria-label="Ответить на это письмо" title="Ответить на это письмо">↩</button>';
      h += "</div>" + attachmentsHtml(m) + "</div>";
      prevKey = m.mail_key;
    });
    if (st.pending) {
      var p = st.pending;
      h +=
        '<div class="mail-msg is-out is-pending"><div class="mail-bubble"><div class="mail-bubble-body mail-html">' +
        "<p>" + esc(p.text).replace(/\n/g, "<br>") + "</p>" +
        '</div><div class="mail-bubble-meta">' + (p.status === "failed" ? "Не отправилось: " + esc(p.error || "") : "Отправка…") +
        (p.status === "sending" ? ' <button type="button" class="mail-mini" data-mi="cancel-send">Отменить</button>' : "") +
        (p.status === "failed" ? ' <button type="button" class="mail-mini" data-mi="retry-send">Ещё раз</button>' : "") +
        "</div></div></div>";
    }
    return h;
  }

  function lettersHtml(t) {
    var h = "";
    (t.messages || []).forEach(function (m) {
      var s = st.shown[m.mail_key] || {};
      var html = m.html_full || m.html;
      if (s.images) html = html.replace(/\sdata-src=/g, " src=");
      h +=
        '<article class="mail-letter" data-key="' + esc(m.mail_key) + '"><header class="mail-letter-head">' +
        avatar(m.direction === "out" ? "Я" : m.from, m.from_addr) +
        '<div class="mail-letter-meta"><b>' + esc(m.direction === "out" ? "Вы" : m.from) + "</b>" +
        '<span>кому: ' + esc((m.to || []).concat(m.cc || []).join(", ")) + "</span></div>" +
        '<span class="mail-letter-date">' + esc(new Date(m.date).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })) + "</span></header>" +
        '<div class="mail-letter-body mail-html mail-html--letter' + (m.rich ? " mail-paper" : "") + '">' + html + "</div>" +
        (m.remote_images && !s.images ? '<button type="button" class="mail-mini" data-mi="images" data-key="' + esc(m.mail_key) + '">Показать картинки</button>' : "") +
        attachmentsHtml(m) +
        '<div class="mail-letter-acts"><button type="button" class="mail-mini" data-mi="reply-full" data-key="' + esc(m.mail_key) + '">Ответить</button>' +
        '<button type="button" class="mail-mini" data-mi="orig" data-key="' + esc(m.mail_key) + '">Оригинал</button></div></article>';
    });
    return h;
  }

  function menuHtml(t) {
    var p0 = (t.people || [])[0] || {};
    var dom = String(p0.addr || "").split("@")[1] || "";
    return (
      '<div class="mail-menu">' +
      '<button type="button" data-mi="unread">Отметить непрочитанным</button>' +
      '<button type="button" data-mi="note">Сохранить в заметки</button>' +
      '<button type="button" data-mi="discuss">Обсудить с Лео</button>' +
      '<button type="button" data-mi="open-mail">Открыть в почте</button>' +
      (p0.addr ? '<button type="button" data-mi="block" data-pattern="' + esc(p0.addr) + '">Не показывать ' + esc(p0.addr) + "</button>" : "") +
      (dom ? '<button type="button" data-mi="block" data-pattern="@' + esc(dom) + '">Не показывать весь домен ' + esc(dom) + "</button>" : "") +
      (p0.addr ? '<button type="button" class="is-danger" data-mi="spam" data-pattern="' + esc(p0.addr) + '">Спам</button>' : "") +
      "</div>"
    );
  }

  function renderThread() {
    var r = slots().detail;
    var t = st.open;
    if (!r || !t) return;
    document.documentElement.classList.toggle("mail-thread-open", !isWide());
    var keepInput = r.querySelector("[data-mi-input]");
    var draftText = keepInput ? keepInput.value : st.inputText || "";
    var h = '<div class="mail-thread">';
    h +=
      '<div class="mail-thread-head"><button type="button" class="btn-text" data-mi="back">←</button>' +
      '<div class="mail-thread-title"><b>' + esc(t.subject || "Переписка") + "</b><span>" +
      esc((t.people || []).map(function (p) { return p.name || p.addr; }).join(", ")) + "</span></div>" +
      '<button type="button" class="mail-icon-btn" data-mi="menu" aria-label="Ещё">⋯</button></div>';
    if (st.menu) h += menuHtml(t);
    h +=
      '<div class="mail-leo-row"><button type="button" class="mail-mini mail-mini--leo" data-mi="ai-draft">✨ Подготовить ответ</button>' +
      '<button type="button" class="mail-mini mail-mini--leo" data-mi="summary">Саммари</button>' +
      '<span class="mail-bar-spacer"></span>' + viewSwitchHtml() + "</div>";
    if ((t.labels || []).length) {
      h += '<div class="mail-labels mail-labels--thread">';
      t.labels.forEach(function (l) {
        h += '<span class="mail-label mail-label--' + esc(l.kind) + '">' + esc(l.text) + "</span>";
      });
      h += "</div>";
    }
    if (st.summary)
      h +=
        '<div class="mail-summary"><div class="mail-summary-head">Саммари от Лео <button type="button" class="mail-mini" data-mi="summary-x">✕</button></div>' +
        '<div class="mail-summary-body">' + esc(st.summary).replace(/\n/g, "<br>") + "</div></div>";
    h += '<div class="mail-msgs' + (st.view === "letters" ? " is-letters" : "") + '">';
    if (t.loading) h += '<div class="mail-empty">Загружаю переписку…</div>';
    else if (t.error) h += '<div class="mail-empty">' + esc(t.error) + ' <button type="button" class="pf-link" data-mi="reload">Повторить</button></div>';
    else h += st.view === "letters" ? lettersHtml(t) : chatHtml(t);
    h += "</div>";
    if (st.view === "chat" && !t.loading && !t.error) {
      var rt = st.replyTo && msgByKey(st.replyTo);
      h += '<div class="mail-input-wrap">';
      if (rt)
        h +=
          '<div class="mail-replybar"><span><b>Ответ на письмо ' + esc(rt.direction === "out" ? "вам" : rt.from) + ":</b> " +
          esc(plainSnippet(rt, 70)) + '</span><button type="button" class="mail-mini" data-mi="reply-x">✕</button></div>';
      h +=
        '<div class="mail-input-row"><button type="button" class="mail-icon-btn" data-mi="expand" title="Полное окно письма: кому, копия, тема, вложения" aria-label="Полное окно письма">⤢</button>' +
        '<textarea class="mail-input" rows="1" data-mi-input placeholder="Ответить…">' + esc(draftText) + "</textarea>" +
        '<button type="button" class="mail-send" data-mi="send" aria-label="Отправить">➤</button></div></div>';
    }
    h += "</div>";
    r.innerHTML = h;
    var inp = r.querySelector("[data-mi-input]");
    if (inp) grow(inp);
    clampBubbles();
  }

  // Длинные письма — до 12 строк с «Показать полностью».
  function clampBubbles() {
    var r = root();
    if (!r) return;
    r.querySelectorAll(".mail-bubble").forEach(function (b) {
      var body = b.querySelector(".mail-bubble-body");
      var more = b.querySelector(".mail-more");
      if (!body || !more || b.classList.contains("is-expanded")) return;
      if (body.scrollHeight > body.clientHeight + 4) more.classList.remove("hidden");
    });
  }

  function grow(t) {
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 160) + "px";
  }

  function lastReplyTarget() {
    var ms = (st.open && st.open.messages) || [];
    var inbound = ms.filter(function (m) {
      return m.direction === "in";
    });
    return st.replyTo || (inbound.length ? inbound[inbound.length - 1].mail_key : ms.length ? ms[ms.length - 1].mail_key : "");
  }

  async function sendFromInput() {
    var r = root();
    var inp = r && r.querySelector("[data-mi-input]");
    var text = inp ? inp.value.trim() : "";
    if (!text) return;
    var key = lastReplyTarget();
    try {
      var d = await post("/mail/drafts", { mail_key: key, body: text });
      var res = await post("/gpt/calendar-actions/" + encodeURIComponent(d.action.id) + "/send", {});
      st.pending = { action: res.action, text: text, status: "sending" };
      st.inputText = "";
      st.replyTo = "";
      if (inp) inp.value = "";
      renderThread();
      scrollBottom();
      watchSend(res.action.id);
    } catch (e) {
      var det = (e && e.detail) || {};
      toast(det.message || (e && e.message) || "Не получилось отправить");
    }
  }

  function scrollBottom() {
    var l = root() && root().querySelector(".mail-msgs");
    if (l) l.scrollTop = l.scrollHeight;
  }

  function watchSend(id) {
    var tries = 0;
    var timer = setInterval(async function () {
      tries++;
      if (!st.pending || st.pending.action.id !== id || tries > 40) {
        clearInterval(timer);
        return;
      }
      var acts = M().refreshActions ? await M().refreshActions([id]) : [];
      var a = acts[0];
      if (!a) return;
      if (a.status === "sent") {
        clearInterval(timer);
        st.pending = null;
        openThread(st.open.thread_key, false);
      } else if (a.status === "failed") {
        clearInterval(timer);
        st.pending.status = "failed";
        st.pending.error = (a.draft || {}).error || "";
        st.pending.action = a;
        renderThread();
      } else if (a.status === "proposed") {
        clearInterval(timer);
        st.pending = null;
        renderThread();
      }
    }, 1500);
  }

  async function openFull(replyKey, text) {
    try {
      var d = await post("/mail/drafts", { mail_key: replyKey, body: text || "" });
      var host = root() && root().querySelector(".mail-input-wrap");
      M().openComposer(d.action, {
        host: host || undefined,
        onDone: function (sent) {
          if (sent && sent.status === "sending") {
            st.pending = { action: sent, text: (sent.draft || {}).body || "", status: "sending" };
            renderThread();
            scrollBottom();
            watchSend(sent.id);
          }
        },
      });
    } catch (e) {
      toast((e && e.message) || "Не удалось открыть окно письма");
    }
  }

  async function download(key, part) {
    try {
      var f = await api("/mail/attachment?mail_key=" + encodeURIComponent(key) + "&part=" + part);
      var bin = atob(f.data);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var url = URL.createObjectURL(new Blob([bytes], { type: f.type }));
      var a = document.createElement("a");
      a.href = url;
      a.download = f.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () {
        URL.revokeObjectURL(url);
      }, 10000);
    } catch (e) {
      toast((e && e.message) || "Не удалось скачать вложение");
    }
  }

  // «Оригинал» — изолированная страница: без скриптов, внешние картинки — только если разрешили.
  function openOriginal(m) {
    var s = st.shown[m.mail_key] || {};
    var html = m.html_full || m.html;
    if (s.images) html = html.replace(/\sdata-src=/g, " src=");
    var ov = document.createElement("div");
    ov.className = "mail-orig";
    ov.innerHTML =
      '<div class="mail-orig-head"><b>' + esc(m.subject || "Письмо") + '</b><button type="button" class="btn-text">Закрыть</button></div>';
    var fr = document.createElement("iframe");
    fr.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    fr.srcdoc =
      '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: ' +
      (s.images ? "https: http:" : "") +
      "; style-src 'unsafe-inline'\"><style>body{font:15px/1.5 -apple-system,system-ui,sans-serif;margin:16px;color:#111;background:#fff}img{max-width:100%;height:auto}</style>" +
      html;
    ov.appendChild(fr);
    ov.querySelector("button").addEventListener("click", function () {
      ov.remove();
    });
    document.body.appendChild(ov);
  }

  async function onClick(e) {
    var q = e.target.closest && e.target.closest(".mail-html--letter blockquote, .mail-html--letter .mail-quote");
    if (q && !q.classList.contains("is-open") && root() && root().contains(q) && !e.target.closest("a")) {
      var outer = q;
      while (outer.parentElement && outer.parentElement.closest(".mail-html--letter blockquote, .mail-html--letter .mail-quote")) {
        outer = outer.parentElement.closest(".mail-html--letter blockquote, .mail-html--letter .mail-quote");
      }
      outer.classList.add("is-open");
      outer.querySelectorAll("blockquote, .mail-quote").forEach(function (x) { x.classList.add("is-open"); });
      return;
    }
    var b = e.target.closest("[data-mi]");
    var r = root();
    if (!b || !r || !r.contains(b)) {
      if (st.menu && r && r.contains(e.target) && !e.target.closest(".mail-menu")) {
        st.menu = false;
        renderThread();
      }
      return;
    }
    var act = b.getAttribute("data-mi");
    var key = b.getAttribute("data-key");
    var m = key ? msgByKey(key) : null;
    if (act === "filter") {
      st.filter = b.getAttribute("data-f");
      return refresh();
    }
    if (act === "retry") return refresh();
    if (act === "connect") return openProfileTab();
    if (act === "open") return openThread(b.getAttribute("data-tk"));
    if (act === "back") return closeThread();
    if (act === "reload") return openThread(st.open.thread_key);
    if (act === "view") {
      st.view = b.getAttribute("data-view");
      post("/mail/settings", { view: st.view }, "PATCH").catch(function () {});
      return st.open ? renderThread() : renderList();
    }
    if (act === "menu") {
      st.menu = !st.menu;
      return renderThread();
    }
    if (act === "bubble") return;
    if (act === "more") {
      st.shown[key] = Object.assign({}, st.shown[key], { more: true });
      return renderThread();
    }
    if (act === "images" || act === "full") {
      var patch = {};
      patch[act] = !(st.shown[key] || {})[act];
      st.shown[key] = Object.assign({}, st.shown[key], patch);
      return renderThread();
    }
    if (act === "orig" && m) return openOriginal(m);
    if (act === "jump") {
      var el = r.querySelector('.mail-msg[data-key="' + key + '"]');
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("is-flash");
        setTimeout(function () {
          el.classList.remove("is-flash");
        }, 1200);
      }
      return;
    }
    if (act === "reply") {
      st.replyTo = key;
      renderThread();
      var inp = r.querySelector("[data-mi-input]");
      if (inp) inp.focus();
      return;
    }
    if (act === "reply-x") {
      st.replyTo = "";
      return renderThread();
    }
    if (act === "reply-full") return openFull(key, "");
    if (act === "expand") {
      var t = r.querySelector("[data-mi-input]");
      return openFull(lastReplyTarget(), t ? t.value : "");
    }
    if (act === "send") return sendFromInput();
    if (act === "cancel-send" && st.pending) {
      try {
        await post("/gpt/calendar-actions/" + encodeURIComponent(st.pending.action.id) + "/cancel_send", {});
        var txt = st.pending.text;
        st.pending = null;
        st.inputText = txt;
        renderThread();
        toast("Отправка отменена");
      } catch (err) {
        toast((err && err.message) || "Не успели отменить");
      }
      return;
    }
    if (act === "retry-send" && st.pending) {
      try {
        var res = await post("/gpt/calendar-actions/" + encodeURIComponent(st.pending.action.id) + "/send", {});
        st.pending = { action: res.action, text: st.pending.text, status: "sending" };
        renderThread();
        watchSend(res.action.id);
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
      return;
    }
    if (act === "file") return download(key, b.getAttribute("data-part"));
    if (act === "ai-draft") {
      b.disabled = true;
      b.textContent = "✨ Пишу…";
      try {
        var dr = await post("/mail/threads/" + encodeURIComponent(st.open.thread_key) + "/draft", { reply_to_key: st.replyTo });
        if (st.view === "letters") return openFull(lastReplyTarget(), dr.body);
        st.inputText = dr.body;
        var ip = r.querySelector("[data-mi-input]");
        if (ip) ip.value = dr.body;
        renderThread();
        var ip2 = root().querySelector("[data-mi-input]");
        if (ip2) ip2.focus();
      } catch (err) {
        toast((err && err.message) || "Не получилось подготовить ответ");
        renderThread();
      }
      return;
    }
    if (act === "summary") {
      b.disabled = true;
      b.textContent = "Читаю…";
      try {
        var sm = await post("/mail/threads/" + encodeURIComponent(st.open.thread_key) + "/summary", {});
        st.summary = sm.summary;
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
      return renderThread();
    }
    if (act === "summary-x") {
      st.summary = null;
      return renderThread();
    }
    st.menu = false;
    var tk = st.open && st.open.thread_key;
    var lastKey = st.open && st.open.messages && st.open.messages.length ? st.open.messages[st.open.messages.length - 1].mail_key : "";
    if (act === "unread") {
      await post("/mail/threads/" + encodeURIComponent(tk) + "/read", { read: false });
      toast("Отмечено непрочитанным");
      return closeThread();
    }
    if (act === "note") {
      try {
        await post("/mail/threads/" + encodeURIComponent(tk) + "/note", {});
        toast("Переписка сохранена в заметки");
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
      return renderThread();
    }
    if (act === "discuss") return M().openThread && M().openThread(lastKey);
    if (act === "open-mail") {
      var u = await api("/mail/open-url?mail_key=" + encodeURIComponent(lastKey));
      if (u && u.url) app().openLink(u.url);
      else toast("Для этой почты ссылки нет — откройте её в своём почтовом приложении");
      return renderThread();
    }
    if (act === "block" || act === "spam") {
      var pattern = b.getAttribute("data-pattern");
      var q = act === "spam"
        ? "Отправить " + pattern + " в спам? Письма уйдут в папку «Спам» в ящике и пропадут из Orbita и памяти Лео."
        : "Не показывать " + pattern + "? Письма пропадут из Orbita, «Фокуса» и памяти Лео. Вернуть можно в профиле → Почта.";
      if (!(await app().confirm(q))) return renderThread();
      try {
        if (act === "spam") await post("/mail/threads/" + encodeURIComponent(tk) + "/spam", { pattern: pattern });
        else await post("/mail/senders", { pattern: pattern, rule: "block" });
        toast(act === "spam" ? "Отправлено в спам" : "Больше не покажем");
      } catch (err) {
        toast((err && err.message) || "Не получилось");
        return renderThread();
      }
      return closeThread();
    }
  }

  function openProfileTab() {
    var tab = document.querySelector('[data-tab="profile"]');
    if (tab) tab.click();
    setTimeout(function () {
      if (M().openProfile) M().openProfile();
    }, 200);
  }

  function onKey(e) {
    var t = e.target;
    if (!t || !t.hasAttribute || !t.hasAttribute("data-mi-input")) return;
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendFromInput();
    }
  }

  function onInput(e) {
    var t = e.target;
    if (t && t.hasAttribute && t.hasAttribute("data-mi-input")) {
      st.inputText = t.value;
      grow(t);
    }
  }

  // Долгий тап по пузырю (телефон) — «ответить на это письмо».
  var pressTimer = null;
  function onTouchStart(e) {
    var bub = e.target.closest && e.target.closest(".mail-bubble[data-key]");
    if (!bub || !root() || !root().contains(bub)) return;
    pressTimer = setTimeout(function () {
      st.replyTo = bub.getAttribute("data-key");
      if (navigator.vibrate) navigator.vibrate(15);
      renderThread();
    }, 500);
  }
  function onTouchEnd() {
    clearTimeout(pressTimer);
  }

  function bind() {
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    document.addEventListener("input", onInput);
    document.addEventListener("touchstart", onTouchStart, { passive: true });
    document.addEventListener("touchend", onTouchEnd, { passive: true });
    document.addEventListener("touchmove", onTouchEnd, { passive: true });
  }

  window.LeoMailInbox = { show: show, refresh: refresh, openThread: openThread };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
