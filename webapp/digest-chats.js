/* «Дайджест» → «Чаты»: список по чатам (как «Сохранённое») и отчёт чата лентой по дням.
 * Дела здесь не дублируются: «Ждут вашего ответа» — те же пункты, что в «Фокусе». */
(function () {
  "use strict";

  function app() {
    return window.LeoApp || {};
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function api(path, opts) {
    return app().apiFetch(path, opts || { method: "GET" });
  }
  function toast(t) {
    if (app().toast) app().toast(t);
  }

  var st = { data: null, filter: "labels", q: "", open: null, chat: null, expanded: {}, loading: false };
  var COLORS = ["#7a6ff0", "#d6455b", "#e8833a", "#2fa4b8", "#3fb27f", "#c05bd6", "#3d8ee8", "#b8962f"];
  var MON = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var WD = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];

  function root() {
    return document.getElementById("digest-pane-chats");
  }

  function isWide() {
    return window.matchMedia && window.matchMedia("(min-width: 900px)").matches;
  }

  function ava(title, id) {
    var w = String(title || "?").replace(/[·|]/g, " ").split(/\s+/).filter(Boolean);
    var ini = ((w[0] || "?")[0] + ((w[1] || "")[0] || "")).toUpperCase();
    var h = Math.abs(Number(id) || 0);
    return '<span class="dgx-ava" style="background:' + COLORS[h % COLORS.length] + '">' + esc(ini) + "</span>";
  }

  function dayLabel(iso) {
    var d = new Date(iso + "T12:00:00");
    if (isNaN(d)) return iso;
    var t = new Date();
    var y = new Date();
    y.setDate(t.getDate() - 1);
    var base = d.getDate() + " " + MON[d.getMonth()];
    if (d.toDateString() === t.toDateString()) return "Сегодня, " + base;
    if (d.toDateString() === y.toDateString()) return "Вчера, " + base;
    return WD[d.getDay()] + ", " + base;
  }

  function shortDay(iso) {
    var d = new Date(iso + "T12:00:00");
    if (isNaN(d)) return "";
    var t = new Date();
    if (d.toDateString() === t.toDateString()) return "сегодня";
    var y = new Date();
    y.setDate(t.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return "вчера";
    if ((t - d) / 86400000 < 6) return ["вс", "пн", "вт", "ср", "чт", "пт", "сб"][d.getDay()];
    return d.getDate() + " " + MON[d.getMonth()].slice(0, 3);
  }

  async function show() {
    var r = root();
    if (!r) return;
    ["digest-list", "digest-empty"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.classList.add("hidden");
    });
    var host = r.querySelector(".dgx");
    if (!host) {
      host = document.createElement("div");
      host.className = "dgx";
      r.insertBefore(host, r.firstChild);
    }
    st.loading = !st.data;
    render();
    try {
      st.data = await api("/digest/chats");
    } catch (e) {
      st.data = { error: (e && e.message) || "Не удалось загрузить дайджест" };
    }
    st.loading = false;
    if (!st.open && isWide() && st.data.chats && st.data.chats.length) st.open = st.data.chats[0].chat_id;
    render();
    if (st.open) loadChat(st.open);
  }

  async function loadChat(id) {
    st.open = id;
    if (!st.chat || st.chat.chat_id !== id) st.chat = { chat_id: id, loading: true };
    render();
    try {
      st.chat = await api("/digest/chats/" + encodeURIComponent(id));
    } catch (e) {
      st.chat = { chat_id: id, error: (e && e.message) || "Не удалось открыть отчёт" };
    }
    render();
  }

  function listHtml() {
    var d = st.data || {};
    var chats = (d.chats || []).filter(function (c) {
      if (st.filter === "labels" && !(c.labels || []).length) return false;
      if (st.q && String(c.title).toLowerCase().indexOf(st.q.toLowerCase()) < 0) return false;
      return true;
    });
    var h = '<div class="dgx-list">';
    h +=
      '<div class="dgx-tools"><input type="search" class="dgx-search" data-dgx-q placeholder="Поиск по чатам" value="' + esc(st.q) + '" />' +
      '<div class="dgx-chips"><button type="button" class="dgx-chip' + (st.filter === "labels" ? " is-on" : "") + '" data-dgx="filter" data-f="labels">С важным · ' + (d.with_labels || 0) +
      '</button><button type="button" class="dgx-chip' + (st.filter === "all" ? " is-on" : "") + '" data-dgx="filter" data-f="all">Все чаты · ' + ((d.chats || []).length) + "</button></div></div>";
    if (st.loading) h += '<div class="dgx-empty">Загружаю…</div>';
    else if (d.error) h += '<div class="dgx-empty">' + esc(d.error) + "</div>";
    else if (!(d.chats || []).length) h += '<div class="dgx-empty">Пока нет отчётов. Чаты для дайджеста выбираются в профиле → «Чаты».</div>';
    else if (!chats.length)
      h += '<div class="dgx-empty">' + (st.filter === "labels" ? 'В чатах ничего важного. <button type="button" class="pf-link" data-dgx="filter" data-f="all">Показать все</button>' : "Ничего не найдено") + "</div>";
    chats.forEach(function (c) {
      h +=
        '<button type="button" class="dgx-row' + (c.chat_id === st.open && isWide() ? " is-sel" : "") + (c.quiet ? " is-quiet" : "") + '" data-dgx="open" data-id="' + c.chat_id + '">' +
        ava(c.title, c.chat_id) +
        '<span class="dgx-rmain"><span class="dgx-rtop"><b>' + esc(c.title) + '</b><span class="dgx-time">' + esc(shortDay(c.last_date)) + "</span></span>" +
        '<span class="dgx-snip">' + esc(c.brief) + "</span>" +
        ((c.labels || []).length
          ? '<span class="dgx-tags">' + c.labels.map(function (l) {
              return '<span class="dgx-tag dgx-tag--' + esc(l.kind) + '">' + esc(l.text) + "</span>";
            }).join("") + "</span>"
          : "") +
        "</span></button>";
    });
    return h + "</div>";
  }

  function li(text, extra) {
    return '<div class="dgx-li"><span class="dgx-dot"></span><span class="dgx-li-text">' + text + "</span>" + (extra || "") + "</div>";
  }

  function taskBtn(text, chatTitle, day) {
    return (
      '<button type="button" class="dgx-btn" data-dgx="task" data-text="' + esc(text) + '" data-chat="' + esc(chatTitle) + '" data-day="' + esc(day) + '">В задачу</button>'
    );
  }

  function dayHtml(c, day, open) {
    var h = '<div class="dgx-day">' + esc(dayLabel(day.date)) + (day.message_count ? " · " + day.message_count + " сообщ." : "") + "</div>";
    var rich = (day.decisions || []).length + (day.next_steps || []).length + (day.open_questions || []).length + (day.deadlines || []).length;
    if (!open) {
      return h +
        '<button type="button" class="dgx-box dgx-collapsed" data-dgx="expand" data-day="' + esc(day.date) + '"><span>' +
        esc(day.brief || "Ничего важного") + (rich ? " · " + rich + " " + (rich === 1 ? "пункт" : rich < 5 ? "пункта" : "пунктов") : "") +
        "</span><span class=\"dgx-more\">Развернуть</span></button>";
    }
    h += '<div class="dgx-box">';
    if (day.brief) h += '<div class="dgx-sec"><h4>Коротко</h4><p>' + esc(day.brief) + "</p></div>";
    if ((day.decisions || []).length)
      h += '<div class="dgx-sec"><h4>Договорились</h4>' + day.decisions.map(function (t) { return li(esc(t)); }).join("") + "</div>";
    if ((day.next_steps || []).length)
      h +=
        '<div class="dgx-sec"><h4>Следующие шаги</h4>' +
        day.next_steps.map(function (s) {
          var line = (s.who ? "<b>" + esc(s.who) + "</b> — " : "") + esc(s.task) + (s.deadline ? ' <span class="dgx-muted">до ' + esc(s.deadline) + "</span>" : "");
          return li(line, taskBtn((s.who ? s.who + " — " : "") + s.task + (s.deadline ? " (до " + s.deadline + ")" : ""), c.title, day.date));
        }).join("") +
        "</div>";
    if ((day.deadlines || []).length)
      h += '<div class="dgx-sec"><h4>Сроки</h4>' + day.deadlines.map(function (x) { return li(esc(x.item) + (x.when ? ' <span class="dgx-muted">— ' + esc(x.when) + "</span>" : "")); }).join("") + "</div>";
    if ((day.open_questions || []).length)
      h += '<div class="dgx-sec"><h4>Открытые вопросы</h4>' + day.open_questions.map(function (t) { return li(esc(t)); }).join("") + "</div>";
    if (!day.brief && !rich) h += '<p class="dgx-muted">Ничего важного</p>';
    return h + "</div>";
  }

  function detailHtml() {
    var c = st.chat;
    if (!c) return '<div class="dgx-detail"><div class="dgx-empty dgx-empty--big">Выберите чат слева</div></div>';
    var title = c.title || ((st.data && (st.data.chats || []).filter(function (x) { return x.chat_id === c.chat_id; })[0]) || {}).title || "Чат";
    var h = '<div class="dgx-detail">';
    h +=
      '<div class="dgx-dhead"><button type="button" class="dgx-back" data-dgx="back" aria-label="Назад">←</button>' + ava(title, c.chat_id) +
      '<div class="dgx-dtitle"><b>' + esc(title) + "</b><span>" + (c.days ? "отчёты за " + c.days.length + " " + (c.days.length === 1 ? "день" : c.days.length < 5 ? "дня" : "дней") : "") + "</span></div>" +
      (c.url ? '<button type="button" class="dgx-btn" data-dgx="tg" data-url="' + esc(c.url) + '">Открыть в Telegram</button>' : "") +
      '<button type="button" class="dgx-btn dgx-btn--leo" data-dgx="leo">Обсудить с Лео</button></div>';
    h += '<div class="dgx-body">';
    if (c.loading) h += '<div class="dgx-empty">Загружаю отчёт…</div>';
    else if (c.error) h += '<div class="dgx-empty">' + esc(c.error) + "</div>";
    else {
      if ((c.awaiting || []).length) {
        h += '<div class="dgx-box dgx-box--wait"><div class="dgx-boxh">Ждут вашего ответа<span class="dgx-infocus">в Фокусе</span></div>';
        c.awaiting.forEach(function (q) {
          var t = new Date(q.asked_at);
          h +=
            '<div class="dgx-q"><span class="dgx-who">' + esc(q.who) + (isNaN(t) ? "" : " · " + ("0" + t.getHours()).slice(-2) + ":" + ("0" + t.getMinutes()).slice(-2)) + "</span>" +
            "<div>«" + esc(q.text) + "»</div><div class=\"dgx-acts\">" +
            (q.url ? '<button type="button" class="dgx-btn dgx-btn--p" data-dgx="tg" data-url="' + esc(q.url) + '">Ответить</button>' : "") +
            '<button type="button" class="dgx-btn" data-dgx="op" data-op="done" data-key="' + esc(q.key) + '">Ответил</button>' +
            '<button type="button" class="dgx-btn" data-dgx="op" data-op="not_me" data-key="' + esc(q.key) + '">Не мне</button></div></div>';
        });
        h += "</div>";
      }
      if ((c.cards || []).length) {
        h += '<div class="dgx-box"><div class="dgx-boxh">В Фокусе из этого чата</div>';
        c.cards.forEach(function (x) {
          h +=
            li('<span class="dgx-tag dgx-tag--' + (x.type === "chat_promise" ? "promise" : x.type === "chat_deadline" ? "deadline" : "risk") + '">' + esc(x.label) + "</span> " + esc(x.text),
              '<button type="button" class="dgx-btn" data-dgx="op" data-op="done" data-key="' + esc(x.key) + '">Сделано</button>');
        });
        h += "</div>";
      }
      (c.days || []).forEach(function (day, i) {
        h += dayHtml(c, day, i === 0 || st.expanded[c.chat_id + ":" + day.date]);
      });
    }
    return h + "</div></div>";
  }

  function render() {
    var r = root();
    var host = r && r.querySelector(".dgx");
    if (!host) return;
    var wide = isWide();
    var showDetail = !!st.open && !wide;
    host.className = "dgx" + (wide ? " is-wide" : "") + (showDetail ? " is-detail" : "");
    host.innerHTML = '<div class="dgx-split">' + (showDetail ? "" : listHtml()) + (wide || showDetail ? detailHtml() : "") + "</div>";
    document.documentElement.classList.toggle("dgx-detail-open", showDetail);
  }

  async function onClick(e) {
    var link = e.target.closest && e.target.closest("#digest-focus-link");
    if (link) {
      if (app().openFocus) app().openFocus();
      return;
    }
    var b = e.target.closest && e.target.closest("[data-dgx]");
    var r = root();
    if (!b || !r || !r.contains(b)) return;
    var act = b.getAttribute("data-dgx");
    if (act === "filter") {
      st.filter = b.getAttribute("data-f");
      return render();
    }
    if (act === "open") return loadChat(Number(b.getAttribute("data-id")));
    if (act === "back") {
      st.open = null;
      st.chat = null;
      return render();
    }
    if (act === "expand") {
      st.expanded[st.chat.chat_id + ":" + b.getAttribute("data-day")] = true;
      return render();
    }
    if (act === "tg") return app().openLink(b.getAttribute("data-url"));
    if (act === "task")
      return app().askLeo(
        "Поставь мне задачу: " + b.getAttribute("data-text") + " (из чата «" + b.getAttribute("data-chat") + "», " + dayLabel(b.getAttribute("data-day")).toLowerCase() + ")."
      );
    if (act === "leo") {
      var day = (st.chat.days || [])[0] || {};
      return app().askLeo("Разберём чат «" + st.chat.title + "». Коротко за последний день: " + (day.brief || "без сводки") + ". Что мне важно знать и что сделать?");
    }
    if (act === "op") {
      b.disabled = true;
      try {
        await api("/digest/item", { method: "POST", body: JSON.stringify({ key: b.getAttribute("data-key"), op: b.getAttribute("data-op") }) });
        toast(b.getAttribute("data-op") === "not_me" ? "Скрыто — и в Фокусе тоже" : "Отмечено — и в Фокусе тоже");
        await loadChat(st.chat.chat_id);
        api("/digest/chats").then(function (d) {
          st.data = d;
          render();
        }).catch(function () {});
      } catch (err) {
        b.disabled = false;
        toast((err && err.message) || "Не получилось");
      }
    }
  }

  function onInput(e) {
    var t = e.target;
    if (t && t.hasAttribute && t.hasAttribute("data-dgx-q")) {
      st.q = t.value;
      var pos = t.selectionStart;
      render();
      var again = root().querySelector("[data-dgx-q]");
      if (again) {
        again.focus();
        again.setSelectionRange(pos, pos);
      }
    }
  }

  var lastWide = null;
  window.addEventListener("resize", function () {
    var w = isWide();
    if (w !== lastWide && st.data) {
      lastWide = w;
      render();
    }
  });

  document.addEventListener("click", onClick);
  document.addEventListener("input", onInput);
  window.LeoDigestChats = { show: show };
})();
