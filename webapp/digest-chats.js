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

  // rules — окно «Правила» чата: { chat_id, loading, data, error, edit }; edit — форма правила.
  var st = { data: null, filter: "all", q: "", open: null, chat: null, expanded: {}, loading: false, rules: null };
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
      st.data = await api("/digest/overview");
    } catch (e) {
      st.data = { error: (e && e.message) || "Не удалось загрузить дайджест" };
    }
    st.loading = false;
    if (st.pendingRules) {
      var want = st.pendingRules;
      st.pendingRules = null;
      st.open = want;
      render();
      await loadChat(want);
      return openRules(want);
    }
    if (!st.open && isWide() && st.data.chats && st.data.chats.length) st.open = st.data.chats[0].chat_id;
    render();
    if (st.open) loadChat(st.open);
  }

  async function loadChat(id) {
    if (st.rules && st.rules.chat_id !== id) st.rules = null;
    st.open = id;
    var same = !!(st.chat && st.chat.chat_id === id && !st.chat.loading);
    if (!same) st.chat = { chat_id: id, loading: true };
    render();
    try {
      st.chat = await api("/digest/chats/" + encodeURIComponent(id));
    } catch (e) {
      st.chat = { chat_id: id, error: (e && e.message) || "Не удалось открыть отчёт" };
    }
    // Перечитали тот же отчёт (задача поставлена / изменена) — остаёмся на том же месте.
    if (same) renderKeepScroll();
    else render();
  }

  function renderKeepScroll() {
    var r = root();
    var body = r && r.querySelector(".dgx-body");
    var main = document.querySelector(".main-scroll");
    var top = body ? body.scrollTop : 0;
    var mainTop = main ? main.scrollTop : 0;
    var winTop = window.scrollY || 0;
    render();
    var nb = r && r.querySelector(".dgx-body");
    if (nb) nb.scrollTop = top;
    if (main) main.scrollTop = mainTop;
    if (window.scrollY !== winTop) window.scrollTo(0, winTop);
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
      '<div class="dgx-chips"><button type="button" class="dgx-chip' + (st.filter === "all" ? " is-on" : "") + '" data-dgx="filter" data-f="all">Все чаты · ' + ((d.chats || []).length) +
      '</button><button type="button" class="dgx-chip' + (st.filter === "labels" ? " is-on" : "") + '" data-dgx="filter" data-f="labels">Важное' +
      (d.with_labels ? ' <span class="dgx-chip-count">' + d.with_labels + "</span>" : "") + "</button></div></div>";
    if (st.loading) h += '<div class="dgx-empty">Загружаю…</div>';
    else if (d.error) h += '<div class="dgx-empty">' + esc(d.error) + "</div>";
    else if (!(d.chats || []).length) h +=
        '<div class="dgx-empty dgx-empty--start"><b>Отчётов пока нет</b>' +
        "<span>Выберите чаты для дайджеста, например чат отдела или клиента, и Лео будет присылать главное за день.</span>" +
        '<button type="button" class="pf-btn" data-empty-action="digest-pick-chats">Выбрать чаты</button></div>';
    else if (!chats.length)
      h += '<div class="dgx-empty">' + (st.filter === "labels" ? 'В чатах ничего важного. <button type="button" class="pf-link" data-dgx="filter" data-f="all">Показать все</button>' : "Ничего не найдено") + "</div>";
    chats.forEach(function (c) {
      h +=
        '<button type="button" class="dgx-row' + (c.chat_id === st.open && isWide() ? " is-sel" : "") + (c.quiet ? " is-quiet" : "") + '" data-dgx="open" data-id="' + c.chat_id + '">' +
        ava(c.title, c.chat_id) +
        '<span class="dgx-rmain"><span class="dgx-rtop"><b>' + esc(c.title) + '</b><span class="dgx-time">' + esc(shortDay(c.last_date)) + "</span></span>" +
        '<span class="dgx-snip">' + esc(c.brief) + "</span>" +
        ((c.labels || []).length || c.rules_count
          ? '<span class="dgx-tags">' + (c.labels || []).map(function (l) {
              return '<span class="dgx-tag dgx-tag--' + esc(l.kind) + '">' + esc(l.text) + "</span>";
            }).join("") +
            (c.rules_count ? '<span class="dgx-tag dgx-tag--rules" title="Ваши правила в этом чате">правила · ' + c.rules_count + "</span>" : "") +
            "</span>"
          : "") +
        "</span></button>";
    });
    return h + "</div>";
  }

  function li(text, extra) {
    return '<div class="dgx-li"><span class="dgx-dot"></span><span class="dgx-li-text">' + text + "</span>" + (extra || "") + "</div>";
  }

  // «В задачу» → окно задачи (исполнитель и срок из шага); поставленная — чип задачи вместо кнопки.
  function taskBtn(s, day) {
    var t = s.task_link;
    if (t) {
      var label = '<span class="dgx-task-chip-t">' + esc(t.title || s.task) + "</span>";
      return t.url
        ? '<button type="button" class="dgx-task-chip" data-dgx="tg" data-url="' + esc(t.url) + '" title="Открыть задачу">' + label + "</button>"
        : '<button type="button" class="dgx-task-chip' + (t.done ? " is-done" : "") + '" data-dgx="opentask" data-task-id="' + esc(t.id) + '" title="Открыть задачу">' + label + "</button>";
    }
    if (!s.key) return "";
    return '<button type="button" class="dgx-btn" data-dgx="task" data-key="' + esc(s.key) + '" data-day="' + esc(day) + '">В задачу</button>';
  }

  function findStep(key) {
    var hit = null;
    ((st.chat && st.chat.days) || []).forEach(function (d) {
      (d.next_steps || []).forEach(function (s) {
        if (s.key === key) hit = s;
      });
    });
    return hit;
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
          return li(line, taskBtn(s, day.date));
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
    if (st.rules && st.rules.chat_id === c.chat_id) return '<div class="dgx-detail">' + rulesHtml(title) + "</div>";
    var h = '<div class="dgx-detail">';
    h +=
      '<div class="dgx-dhead"><button type="button" class="dgx-back" data-dgx="back" aria-label="Назад">←</button>' + ava(title, c.chat_id) +
      '<div class="dgx-dtitle"><b>' + esc(title) + "</b><span>" + (c.days ? "отчёты за " + c.days.length + " " + (c.days.length === 1 ? "день" : c.days.length < 5 ? "дня" : "дней") : "") + "</span></div>" +
      (c.url ? '<button type="button" class="dgx-btn" data-dgx="tg" data-url="' + esc(c.url) + '">Открыть в Telegram</button>' : "") +
      '<button type="button" class="dgx-btn dgx-btn--rules' + (c.rules_count ? " is-on" : "") + '" data-dgx="rules" title="Что Лео ищет в этом чате для вас">' +
      "Правила" + (c.rules_count ? " · " + c.rules_count : "") + "</button>" +
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
      h += hitsHtml(c);
      (c.days || []).forEach(function (day, i) {
        h += dayHtml(c, day, i === 0 || st.expanded[c.chat_id + ":" + day.date]);
      });
    }
    return h + "</div></div>";
  }

  function hhmm(iso) {
    var t = new Date(iso);
    return isNaN(t) ? "" : ("0" + t.getHours()).slice(-2) + ":" + ("0" + t.getMinutes()).slice(-2);
  }

  function fieldsHtml(f) {
    var keys = Object.keys(f || {});
    if (!keys.length) return "";
    return '<div class="dgx-fields">' + keys.map(function (k) {
      return '<span><span class="dgx-muted">' + esc(k) + ":</span> " + esc(f[k]) + "</span>";
    }).join("") + "</div>";
  }

  // Находки личных правил чата (видит только автор правил).
  function hitsHtml(c) {
    var hits = (c.rule_hits || []).filter(function (x) {
      return x.action === "table_row" ? x.status === "done" || x.status === "failed" : x.status === "new";
    });
    if (!hits.length) return "";
    var h = '<div class="dgx-box dgx-box--rules"><div class="dgx-boxh">Нашёл Лео по вашим правилам<span class="dgx-infocus">видите только вы</span></div>';
    hits.forEach(function (x) {
      var when = hhmm(x.ts_utc);
      h +=
        '<div class="dgx-q"><span class="dgx-who">' + esc(x.rule ? "«" + x.rule + "»" : "Правило") +
        (x.who ? " · " + esc(x.who) : "") + (when ? ", " + when : "") + "</span>" +
        (x.action === "table_row"
          ? (x.status === "failed"
              ? '<div class="dgx-err">Не смог записать: ' + esc(x.error || "ошибка") + "</div><div>" + esc(x.summary) + "</div>"
              : "<div>Добавил строку в «" + esc(x.doc_title || "документ") + "»</div>")
          : "<div>" + esc(x.summary) + "</div>") +
        fieldsHtml(x.fields) +
        '<div class="dgx-acts">' +
        (x.action === "table_row"
          ? (x.status === "done" && x.note_id
              ? '<button type="button" class="dgx-btn" data-dgx="hit-doc" data-note="' + esc(x.note_id) + '">Открыть документ</button>' +
                '<button type="button" class="dgx-btn" data-dgx="hit" data-op="remove" data-id="' + x.id + '">Убрать</button>'
              : "") +
            (x.url ? '<button type="button" class="dgx-btn" data-dgx="tg" data-url="' + esc(x.url) + '">Сообщение</button>' : "") +
            '<button type="button" class="dgx-btn" data-dgx="hit" data-op="hide" data-id="' + x.id + '">Понятно</button>'
          : (x.url ? '<button type="button" class="dgx-btn" data-dgx="tg" data-url="' + esc(x.url) + '">Открыть в Telegram</button>' : "") +
            '<button type="button" class="dgx-btn" data-dgx="hit" data-op="done" data-id="' + x.id + '">Сделано</button>' +
            '<button type="button" class="dgx-btn" data-dgx="hit" data-op="hide" data-id="' + x.id + '">Не то</button>') +
        "</div></div>";
    });
    return h + "</div>";
  }

  function rulesHead(back, title, sub) {
    return (
      '<div class="dgx-dhead"><button type="button" class="dgx-back dgx-back--always" data-dgx="' + back + '" aria-label="Назад">←</button>' +
      '<div class="dgx-dtitle"><b>' + esc(title) + "</b>" + (sub ? "<span>" + esc(sub) + "</span>" : "") + "</div></div>"
    );
  }

  // «Правила» чата: личные «Если → То». Лео находит, что делать — решает код.
  function rulesHtml(title) {
    var R = st.rules;
    if (R.edit) return ruleFormHtml();
    var h = rulesHead("rules-close", "Правила для чата", title + " · видите только вы");
    h += '<div class="dgx-body">';
    if (R.loading) return h + '<div class="dgx-empty">Загружаю…</div></div>';
    if (R.error) return h + '<div class="dgx-empty">' + esc(R.error) + "</div></div>";
    var d = R.data || {};
    if (!(d.rules || []).length)
      h += '<p class="dgx-hint">Опишите своими словами, что Лео искать в этом чате, — он будет показывать находки здесь. Например, сообщения об оплатах или просьбы к вам.</p>';
    (d.rules || []).forEach(function (r) {
      h +=
        '<div class="dgx-rule' + (r.enabled ? "" : " is-off") + '">' +
        '<button type="button" class="dgx-rule-main" data-dgx="rule-edit" data-id="' + r.id + '"><b>' + esc(r.title) + "</b>" +
        '<span class="dgx-muted">' + esc(r.instruction) + " → " + esc(r.action_label) + "</span>" +
        ((r.action_data || {}).error ? '<span class="dgx-err">На паузе: ' + esc(r.action_data.error) + "</span>" : "") + "</button>" +
        '<button type="button" class="dgx-switch' + (r.enabled ? " is-on" : "") + '" role="switch" aria-checked="' + !!r.enabled +
        '" aria-label="Включено" data-dgx="rule-toggle" data-id="' + r.id + '"></button></div>';
    });
    h += '<button type="button" class="dgx-btn dgx-btn--p dgx-btn--wide" data-dgx="rule-new">+ Новое правило</button>';
    if ((d.templates || []).length) {
      h += '<div class="dgx-sub">Или начните с готового</div><div class="dgx-chips">';
      d.templates.forEach(function (t, i) {
        h += '<button type="button" class="dgx-chip" data-dgx="rule-tpl" data-i="' + i + '">' + esc(t.title) + "</button>";
      });
      h += "</div>";
    }
    h += '<p class="dgx-hint dgx-muted">Лео видит сообщения, которые получает бот в группе. Чтобы он видел все, сделайте бота администратором чата.</p>';
    return h + "</div>";
  }

  function ruleFormHtml() {
    var R = st.rules;
    var E = R.edit;
    var d = R.data || {};
    var h = rulesHead("rule-cancel", E.id ? "Правило" : "Новое правило", "");
    h += '<div class="dgx-body">';
    h += '<label class="dgx-field"><span class="dgx-sub">Название</span><input type="text" class="dgx-input" data-rf="title" maxlength="80" placeholder="Например: Оплаты" value="' + esc(E.title) + '" /></label>';
    h +=
      '<div class="dgx-step"><span class="dgx-stepn">Если</span><div class="dgx-stepb">' +
      '<textarea class="dgx-input" data-rf="instruction" rows="3" maxlength="1500" placeholder="Например: бухгалтер пишет, что пришла оплата — кто, сколько и когда">' + esc(E.instruction) + "</textarea>" +
      '<div class="dgx-sub">От кого</div><div class="dgx-chips">' +
      '<button type="button" class="dgx-chip' + (E.from.length ? "" : " is-on") + '" data-dgx="rf-from" data-id="">Любой</button>';
    (d.authors || []).slice(0, 15).forEach(function (a) {
      var on = E.from.indexOf(a.user_id) >= 0;
      h += '<button type="button" class="dgx-chip' + (on ? " is-on" : "") + '" data-dgx="rf-from" data-id="' + a.user_id + '">' + esc(a.name) + (a.bot ? " · бот" : "") + "</button>";
    });
    h += "</div>";
    if ((d.topics || []).length) {
      h += '<div class="dgx-sub">В топике</div><div class="dgx-chips">' +
        '<button type="button" class="dgx-chip' + (E.topics.length ? "" : " is-on") + '" data-dgx="rf-topic" data-id="">Любой</button>';
      d.topics.forEach(function (t) {
        h += '<button type="button" class="dgx-chip' + (E.topics.indexOf(t.id) >= 0 ? " is-on" : "") + '" data-dgx="rf-topic" data-id="' + t.id + '">' + esc(t.name) + "</button>";
      });
      h += "</div>";
    }
    h += prefilterHtml(E) + "</div></div>";
    h += '<div class="dgx-step"><span class="dgx-stepn">То</span><div class="dgx-stepb"><div class="dgx-chips">';
    (d.actions || []).forEach(function (a) {
      h += '<button type="button" class="dgx-chip' + (E.action === a.id ? " is-on" : "") + '" data-dgx="rf-action" data-id="' + esc(a.id) + '">' + esc(a.label) + "</button>";
    });
    h += "</div>" + (E.action === "table_row" ? targetHtml(E) : '<span class="dgx-muted">Скоро: создать задачу</span>') + "</div></div>";
    if (E.checking) h += '<div class="dgx-box"><span class="dgx-muted">Лео читает последние сообщения…</span></div>';
    else if (E.check) {
      var ch = E.check;
      h += '<div class="dgx-box"><div class="dgx-boxh">Проверка на последних сообщениях</div>';
      if (ch.error) h += '<span class="dgx-muted">' + esc(ch.error) + "</span>";
      else if (!(ch.hits || []).length)
        h += '<span class="dgx-muted">' + (ch.checked ? "Из " + ch.checked + " подходящих по условиям сообщений ничего не нашлось. Попробуйте описать иначе."
          : "Среди последних " + ch.total + " сообщений нет подходящих по условиям" + ((ch.words || []).length ? " и словам-фильтру" : "") + ".") + "</span>";
      else {
        h += '<span class="dgx-muted">Нашёл ' + ch.hits.length + " из " + ch.checked + " сообщений" + ((ch.columns || []).length ? " — так Лео заполнит строки:" : ":") + "</span>";
        if ((ch.columns || []).length) {
          h += '<div class="dgx-tablewrap"><table class="dgx-table"><tr>' + ch.columns.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "<th>Источник</th></tr>";
          ch.hits.forEach(function (x) {
            h += "<tr>" + ch.columns.map(function (c) { return "<td>" + esc((x.fields || {})[c] || "") + "</td>"; }).join("") +
              '<td class="dgx-muted">Лео · ' + esc(x.who) + "</td></tr>";
          });
          h += "</table></div>";
        } else ch.hits.forEach(function (x) {
          h += '<div class="dgx-q"><span class="dgx-who">' + esc(x.who) + (hhmm(x.ts_utc) ? ", " + hhmm(x.ts_utc) : "") + "</span><div>" + esc(x.summary) + "</div>" + fieldsHtml(x.fields) + "</div>";
        });
      }
      h += "</div>";
    }
    h +=
      '<div class="dgx-acts dgx-acts--form"><button type="button" class="dgx-btn" data-dgx="rule-check"' + (E.checking ? " disabled" : "") + ">Проверить</button>" +
      '<button type="button" class="dgx-btn dgx-btn--p" data-dgx="rule-save"' + (E.saving ? " disabled" : "") + ">Сохранить</button></div>";
    if (E.id) h += '<button type="button" class="dgx-linkdanger" data-dgx="rule-delete">Удалить правило</button>';
    return h + "</div>";
  }

  // Слова-фильтр: сообщения без них модель не читает (экономия). Подбирает Лео при сохранении / проверке.
  function prefilterHtml(E) {
    var h = '<div class="dgx-prefilter">';
    if (E.prefilter_off) h += '<span class="dgx-muted">Лео читает все сообщения, подходящие по условиям выше.</span>';
    else if ((E.words || []).length)
      h += '<span class="dgx-muted">Чтобы не тратить лишнее, Лео читает только сообщения со словами: ' +
        E.words.map(function (w) { return "«" + esc(w) + "»"; }).join(", ") + ".</span>";
    else if (E.words) h += '<span class="dgx-muted">По словам такие сообщения не отобрать — Лео читает все, подходящие по условиям.</span>';
    else h += '<span class="dgx-muted">При проверке и сохранении Лео подберёт слова-фильтр, чтобы не читать лишнее.</span>';
    h += '<label class="dgx-checkline"><input type="checkbox" data-dgx="rf-prefilter"' + (E.prefilter_off ? " checked" : "") + " /> Читать все сообщения (дороже)</label>";
    return h + "</div>";
  }

  // «Куда добавлять»: документ → лист → таблица → как Лео заполнит столбцы.
  function targetHtml(E) {
    var h = '<div class="dgx-target">';
    if (!E.target) {
      h += '<div class="dgx-sub">Куда добавлять</div>' +
        '<input type="search" class="dgx-input" data-rf-q placeholder="Найти документ или вставить ссылку" value="' + esc(E.q || "") + '" />' +
        '<div class="dgx-docs">' + docsHtml(E) + "</div>";
      return h + "</div>";
    }
    var T = E.target;
    h += '<div class="dgx-doc is-sel"><span class="dgx-doc-t">' + esc(T.title) + '</span><button type="button" class="dgx-linkbtn" data-dgx="rf-doc-clear">Сменить</button></div>';
    var sheets = T.sheets || [];
    var sh = sheets.filter(function (x) { return x.id === E.sheet_id; })[0] || sheets[0];
    if (sheets.length > 1) {
      h += '<div class="dgx-sub">Лист</div><div class="dgx-chips">';
      sheets.forEach(function (x) {
        h += '<button type="button" class="dgx-chip' + (sh && x.id === sh.id ? " is-on" : "") + '" data-dgx="rf-sheet" data-id="' + esc(x.id) + '">' + esc(x.title) + "</button>";
      });
      h += "</div>";
    }
    var tables = (sh && sh.tables) || [];
    if (!tables.length) return h + '<span class="dgx-err">На этом листе нет таблицы. Добавьте в документ таблицу с шапкой — первая строка называет столбцы.</span></div>';
    var tb = tables[E.table_index] || tables[0];
    if (tables.length > 1) {
      h += '<div class="dgx-sub">Таблица</div><div class="dgx-chips">';
      tables.forEach(function (x) {
        h += '<button type="button" class="dgx-chip' + (x.index === tb.index ? " is-on" : "") + '" data-dgx="rf-table" data-i="' + x.index + '">' + esc(x.label) + "</button>";
      });
      h += "</div>";
    }
    h += '<div class="dgx-sub">Как Лео заполнит столбцы — понимает сам, можно подсказать</div><div class="dgx-cols">';
    (tb.columns || []).forEach(function (c) {
      if (!c || /^источник$/i.test(c)) return;
      h += '<label class="dgx-col"><span>' + esc(c) + '</span><input type="text" class="dgx-input dgx-input--sm" maxlength="200" data-rf-hint="' + esc(c) +
        '" placeholder="подсказка, если нужно" value="' + esc((E.hints || {})[c] || "") + '" /></label>';
    });
    h += '<div class="dgx-col"><span>Источник</span><span class="dgx-muted">«Лео · сообщение» со ссылкой — ставит Орбита</span></div>';
    return h + "</div></div>";
  }

  function docsHtml(E) {
    if (E.docsLoading) return '<span class="dgx-muted">Ищу…</span>';
    if (E.docsError) return '<span class="dgx-err">' + esc(E.docsError) + "</span>";
    var docs = E.docs || [];
    if (!docs.length) return '<span class="dgx-muted">' + (E.q ? "Ничего не нашлось" : "Документов пока нет") + "</span>";
    return (E.q ? "" : '<span class="dgx-sub">Недавние</span>') + docs.map(function (d) {
      return '<button type="button" class="dgx-doc" data-dgx="rf-doc" data-id="' + d.id + '"><span class="dgx-doc-t">' + esc(d.title) + "</span></button>";
    }).join("");
  }

  var docsTimer = null;
  function loadDocs(E, q) {
    E.q = q;
    if (docsTimer) clearTimeout(docsTimer);
    docsTimer = setTimeout(async function () {
      if (/note=\d+/.test(q)) return pickDoc(E, q.trim());
      E.docsLoading = true;
      E.docsError = "";
      paintDocs(E);
      try {
        var d = await api("/digest/rules/targets?q=" + encodeURIComponent(q || ""));
        if (E.q !== q) return;
        E.docs = d.items || [];
      } catch (err) {
        E.docsError = (err && err.message) || "Не удалось найти";
      }
      E.docsLoading = false;
      paintDocs(E);
    }, q ? 300 : 0);
  }

  function paintDocs(E) {
    var box = root() && root().querySelector(".dgx-docs");
    if (box && st.rules && st.rules.edit === E) box.innerHTML = docsHtml(E);
  }

  async function pickDoc(E, ref, keep) {
    E.docsLoading = true;
    paintDocs(E);
    try {
      var t = await api("/digest/rules/target?ref=" + encodeURIComponent(ref));
      E.target = t;
      if (!keep) {
        E.sheet_id = t.sheet_id || "main";
        if (!(t.sheets || []).some(function (x) { return x.id === E.sheet_id && (x.tables || []).length; })) {
          var withTable = (t.sheets || []).filter(function (x) { return (x.tables || []).length; })[0];
          if (withTable) E.sheet_id = withTable.id;
        }
        E.table_index = 0;
        E.hints = {};
      }
    } catch (err) {
      E.docsError = (err && err.message) || "Не удалось открыть документ";
    }
    E.docsLoading = false;
    if (st.rules && st.rules.edit === E) render();
  }

  async function openRules(chatId) {
    st.rules = { chat_id: chatId, loading: true, edit: null };
    render();
    try {
      var d = await api("/digest/chats/" + encodeURIComponent(chatId) + "/rules");
      if (!st.rules || st.rules.chat_id !== chatId) return;
      st.rules.data = d;
    } catch (e) {
      if (st.rules) st.rules.error = (e && e.message) || "Не удалось загрузить правила";
    }
    if (st.rules) st.rules.loading = false;
    render();
  }

  function ruleBody(E) {
    var b = { title: E.title, instruction: E.instruction, action: E.action,
              conditions: { from_user_ids: E.from, thread_ids: E.topics || [], prefilter_off: !!E.prefilter_off } };
    if (E.action === "table_row")
      b.action_data = E.target
        ? { note_id: E.target.note_id, sheet_id: E.sheet_id || "main", table_index: E.table_index || 0, column_hints: E.hints || {} }
        : {};
    return b;
  }

  async function onRulesClick(act, b) {
    var R = st.rules;
    if (!R) return;
    var E = R.edit;
    var d = R.data || {};
    if (act === "rules-close") {
      st.rules = null;
      return loadChat(st.chat.chat_id);
    }
    if (act === "rule-new" || act === "rule-tpl") {
      var t = act === "rule-tpl" ? (d.templates || [])[Number(b.getAttribute("data-i"))] || {} : {};
      R.edit = { title: t.title || "", instruction: t.instruction || "", from: [], topics: [], action: "show" };
      return render();
    }
    if (act === "rule-edit") {
      var r = (d.rules || []).filter(function (x) {
        return String(x.id) === b.getAttribute("data-id");
      })[0];
      if (!r) return;
      var ad = r.action_data || {};
      var rc = r.conditions || {};
      R.edit = { id: r.id, title: r.title, instruction: r.instruction, from: (rc.from_user_ids || []).slice(), action: r.action,
                 topics: (rc.thread_ids || []).slice(), prefilter_off: !!rc.prefilter_off, words: rc.words || null,
                 sheet_id: ad.sheet_id || "main", table_index: ad.table_index || 0, hints: Object.assign({}, ad.column_hints || {}) };
      render();
      if (r.action === "table_row" && ad.note_id) pickDoc(R.edit, String(ad.note_id), true);
      return;
    }
    if (act === "rule-cancel") {
      R.edit = null;
      return render();
    }
    if (act === "rule-toggle") {
      var id = b.getAttribute("data-id");
      var on = b.getAttribute("aria-checked") !== "true";
      b.disabled = true;
      try {
        var res = await api("/digest/rules/" + encodeURIComponent(id), { method: "PATCH", body: JSON.stringify({ enabled: on }) });
        (d.rules || []).forEach(function (x) {
          if (String(x.id) === String(id)) x.enabled = res.rule.enabled;
        });
      } catch (err) {
        toast((err && err.message) || "Не получилось");
      }
      return render();
    }
    if (!E) return;
    if (act === "rf-from") {
      var uid = b.getAttribute("data-id");
      if (!uid) E.from = [];
      else {
        uid = Number(uid);
        var i = E.from.indexOf(uid);
        if (i >= 0) E.from.splice(i, 1);
        else E.from.push(uid);
      }
      return render();
    }
    if (act === "rf-topic") {
      var tid = b.getAttribute("data-id");
      if (tid === "") E.topics = [];
      else {
        tid = Number(tid);
        var ti = E.topics.indexOf(tid);
        if (ti >= 0) E.topics.splice(ti, 1);
        else E.topics.push(tid);
      }
      E.check = null;
      return render();
    }
    if (act === "rf-prefilter") {
      E.prefilter_off = !!b.checked;
      E.check = null;
      return render();
    }
    if (act === "rf-action") {
      E.action = b.getAttribute("data-id");
      E.check = null;
      render();
      if (E.action === "table_row" && !E.target && !E.docs) loadDocs(E, "");
      return;
    }
    if (act === "rf-doc") return pickDoc(E, b.getAttribute("data-id"));
    if (act === "rf-doc-clear") {
      E.target = null;
      E.check = null;
      render();
      return loadDocs(E, E.q || "");
    }
    if (act === "rf-sheet") {
      E.sheet_id = b.getAttribute("data-id");
      E.table_index = 0;
      E.check = null;
      return render();
    }
    if (act === "rf-table") {
      E.table_index = Number(b.getAttribute("data-i")) || 0;
      E.check = null;
      return render();
    }
    if (act === "rule-check") {
      if (String(E.instruction || "").trim().length < 5) return toast("Опишите, что Лео искать");
      E.checking = true;
      E.check = null;
      render();
      try {
        E.check = await api("/digest/chats/" + encodeURIComponent(R.chat_id) + "/rules/check", { method: "POST", body: JSON.stringify(ruleBody(E)) });
        if (!E.prefilter_off) E.words = E.check.words || [];
      } catch (err) {
        E.check = { error: (err && err.message) || "Не получилось проверить" };
      }
      E.checking = false;
      return render();
    }
    if (act === "rule-save") {
      if (String(E.instruction || "").trim().length < 5) return toast("Опишите, что Лео искать");
      E.saving = true;
      render();
      try {
        if (E.id) await api("/digest/rules/" + encodeURIComponent(E.id), { method: "PATCH", body: JSON.stringify(ruleBody(E)) });
        else await api("/digest/chats/" + encodeURIComponent(R.chat_id) + "/rules", { method: "POST", body: JSON.stringify(ruleBody(E)) });
        toast(E.id ? "Правило сохранено" : "Правило включено — Лео смотрит новые сообщения");
        return openRules(R.chat_id);
      } catch (err) {
        E.saving = false;
        toast((err && err.message) || "Не получилось сохранить");
        return render();
      }
    }
    if (act === "rule-delete") {
      if (!(await app().confirm("Удалить правило «" + (E.title || "без названия") + "»?"))) return;
      try {
        await api("/digest/rules/" + encodeURIComponent(E.id), { method: "DELETE" });
        return openRules(R.chat_id);
      } catch (err) {
        toast((err && err.message) || "Не получилось удалить");
      }
    }
  }

  // «Мои сценарии» в Профиле (экран «Чаты с Лео»): все правила, вкл/выкл, переход к правилам чата.
  async function renderScenarios() {
    var group = document.getElementById("profile-scenarios-group");
    var list = document.getElementById("profile-scenarios-list");
    if (!group || !list) return;
    var rules = [];
    try {
      rules = (await api("/digest/rules")).rules || [];
    } catch (e) {
      rules = [];
    }
    group.classList.toggle("hidden", !rules.length);
    list.innerHTML = rules.map(function (r) {
      var err = (r.action_data || {}).error;
      return (
        '<div class="pf-row dgx-sc-row' + (r.enabled ? "" : " is-off") + '">' +
        '<button type="button" class="pf-row-body dgx-sc-open" data-sc-chat="' + r.chat_id + '"><span class="pf-row-text">' +
        '<span class="pf-row-title">' + esc(r.title) + "</span>" +
        '<span class="pf-row-sub">' + esc(r.chat_title) + " · " + esc(r.action_label) + "</span>" +
        (err ? '<span class="pf-row-sub dgx-err">На паузе: ' + esc(err) + "</span>" : "") +
        "</span></button>" +
        '<input type="checkbox" class="pf-switch dgx-sc-switch" aria-label="Включено" data-sc-toggle="' + r.id + '"' + (r.enabled ? " checked" : "") + " /></div>"
      );
    }).join("");
  }

  document.addEventListener("change", async function (e) {
    var t = e.target;
    var id = t && t.getAttribute && t.getAttribute("data-sc-toggle");
    if (!id) return;
    t.disabled = true;
    try {
      var res = await api("/digest/rules/" + encodeURIComponent(id), { method: "PATCH", body: JSON.stringify({ enabled: t.checked }) });
      t.checked = !!res.rule.enabled;
      toast(t.checked ? "Правило включено" : "Правило выключено");
    } catch (err) {
      t.checked = !t.checked;
      toast((err && err.message) || "Не получилось");
    }
    t.disabled = false;
    renderScenarios();
  });

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-sc-chat]");
    if (b) openRulesFor(Number(b.getAttribute("data-sc-chat")));
  });

  // Открыть «Правила» чата из другого места (Профиль): вкладка «Дайджест» → «Чаты» → чат → правила.
  function openRulesFor(chatId) {
    st.pendingRules = chatId;
    if (app().setTab) app().setTab("digest");
    var sub = document.querySelector('[data-digest-subtab="chats"]');
    if (sub) sub.click();
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
      st.rules = null;
      return render();
    }
    if (act === "expand") {
      st.expanded[st.chat.chat_id + ":" + b.getAttribute("data-day")] = true;
      return render();
    }
    if (act === "tg") return app().openLink(b.getAttribute("data-url"));
    if (act === "rules") return openRules(st.chat.chat_id);
    if (act === "hit-doc") return app().openNote(b.getAttribute("data-note"));
    if (/^(rules-|rule-|rf-)/.test(act)) return onRulesClick(act, b);
    if (act === "hit") {
      b.disabled = true;
      try {
        await api("/digest/rule-hits/" + encodeURIComponent(b.getAttribute("data-id")), { method: "POST", body: JSON.stringify({ op: b.getAttribute("data-op") }) });
        var op = b.getAttribute("data-op");
        if (op === "hide" && b.textContent !== "Понятно") toast("Скрыто. Если Лео часто ошибается — уточните правило");
        if (op === "remove") toast("Строку убрал из документа");
        return loadChat(st.chat.chat_id);
      } catch (err) {
        b.disabled = false;
        return toast((err && err.message) || "Не получилось");
      }
    }
    if (act === "task") {
      var step = findStep(b.getAttribute("data-key"));
      if (!step) return;
      var stepDay = b.getAttribute("data-day");
      var chatId = st.chat.chat_id;
      return app().openTaskDraft({
        title: step.task,
        assignee: step.who,
        deadline: step.deadline,
        baseDay: stepDay,
        description: "Из чата «" + (st.chat.title || "Чат") + "», " + dayLabel(stepDay).toLowerCase(),
        onSaved: async function (task) {
          if (!task) return;
          // Битрикс24 и др.: id вида «bitrix24:123» — запоминаем ссылку и подпись.
          var ext = task.external || null;
          var body = ext
            ? { key: step.key, url: ext.url || "", title: task.chip_label || step.task }
            : { key: step.key, task_id: task.id };
          step.task_link = ext ? { url: ext.url || "", title: task.chip_label || step.task } : { id: task.id, title: task.title || step.task };
          if (st.chat && st.chat.chat_id === chatId) renderKeepScroll();
          try {
            await api("/digest/step-task", { method: "POST", body: JSON.stringify(body) });
          } catch (err) {
            toast((err && err.message) || "Задача поставлена, но связь с пунктом не сохранилась");
          }
        },
      });
    }
    if (act === "opentask") {
      var openedChat = st.chat && st.chat.chat_id;
      // Переименовали / удалили задачу — перечитываем отчёт: чип с новым названием или снова «В задачу».
      return app().openTaskById(b.getAttribute("data-task-id"), function () {
        if (openedChat && st.chat && st.chat.chat_id === openedChat) loadChat(openedChat);
      });
    }
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
        api("/digest/overview").then(function (d) {
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
    var rf = t && t.getAttribute && t.getAttribute("data-rf");
    if (rf && st.rules && st.rules.edit) {
      st.rules.edit[rf] = t.value; // без перерисовки — курсор остаётся в поле
      if (rf === "instruction") st.rules.edit.words = null; // текст другой — слова подберутся заново
      return;
    }
    if (t && t.hasAttribute && st.rules && st.rules.edit) {
      if (t.hasAttribute("data-rf-q")) return loadDocs(st.rules.edit, t.value);
      var hc = t.getAttribute("data-rf-hint");
      if (hc) {
        st.rules.edit.hints = st.rules.edit.hints || {};
        st.rules.edit.hints[hc] = t.value;
        return;
      }
    }
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
  window.LeoDigestChats = { show: show, renderScenarios: renderScenarios, openRulesFor: openRulesFor };
})();
