/* Профиль → «Мои показатели»: сроки, качество и реакция по поручениям от других.
 * Те же цифры увидит руководитель отдела. Расчёт — assistant/services/people_metrics.py. */
(function () {
  "use strict";

  var state = { days: 28, data: null, loading: false };
  var REASON = {
    owner_moved: "срок перенёс постановщик",
    question_wait: "ждали ответа на вопрос",
    short_notice: "срок меньше рабочего дня",
    absence: "срок пришёлся на отпуск",
  };

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
  function root() {
    return document.getElementById("metrics-root");
  }
  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }
  function hours(h) {
    if (h == null) return "—";
    if (h < 1) return Math.max(1, Math.round(h * 60)) + " мин";
    return String(Math.round(h * 10) / 10).replace(".", ",") + " раб. ч";
  }
  function fmtDate(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  }
  function fmtDay(s) {
    var p = String(s || "").split("-");
    return p.length === 3 ? Number(p[2]) + "." + p[1] : s;
  }

  // Сдвиг к прошлому периоду. lowerBetter — для времени реакции.
  function trend(v, lowerBetter, unit) {
    if (v == null || v === 0) return "";
    var good = lowerBetter ? v < 0 : v > 0;
    var txt = (v > 0 ? "↑" : "↓") + String(Math.abs(Math.round(v * 10) / 10)).replace(".", ",") + (unit || "");
    return '<span class="metrics-trend metrics-trend--' + (good ? "up" : "down") + '">' + esc(txt) + "</span>";
  }
  function kpi(label, value, extra) {
    var none = value == null || value === "";
    return '<div class="metrics-kpi"><span class="metrics-kpi-label">' + esc(label) + "</span>" +
      '<span class="metrics-kpi-value' + (none ? " metrics-kpi-value--none" : "") + '">' +
      (none ? "мало данных" : esc(value) + (extra || "")) + "</span></div>";
  }
  function row(title, value, sub) {
    return '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
      esc(title) + "</span>" + (sub ? '<span class="pf-row-sub pf-row-sub--wrap">' + esc(sub) + "</span>" : "") +
      '</span><span class="pf-row-val">' + esc(value) + "</span></span></div>";
  }
  function group(title, inner, hint) {
    return '<div class="pf-group"><p class="pf-group-title">' + esc(title) + "</p>" +
      '<div class="pf-list">' + inner + "</div>" + (hint ? '<p class="pf-hint">' + esc(hint) + "</p>" : "") + "</div>";
  }

  // Короткий вывод словами — из уже посчитанных чисел, без оценки. self — про себя («вы»), иначе безлично.
  function leadText(d, self) {
    var a = d.assignee, t = d.trend, s = [];
    if (!a.received && !a.deadlines && !a.accepted) {
      return self
        ? "За этот период вам не ставили поручений — показателям не из чего складываться. Здесь считаются только задачи, которые вам поставили другие."
        : "За этот период поручений от других не было — показателям не из чего складываться.";
    }
    if (a.on_time_pct == null) {
      s.push("Сроков пока мало для процента: " + a.deadlines + " из " + a.received + " " + plural(a.received, "поручения", "поручений", "поручений") + " дошли до срока.");
    } else {
      s.push("В срок — " + a.on_time_pct + "% поручений" + (t.on_time_pct ? (t.on_time_pct > 0 ? ", лучше" : ", хуже") + " прошлого периода" : "") + ".");
    }
    if (a.late) {
      s.push(a.late_warned
        ? "О " + a.late_warned + " из " + a.late + " " + plural(a.late, "срыва", "срывов", "срывов") + (self ? " вы предупредили" : " предупредили") + " заранее."
        : "О срывах сроков заранее не предупреждали — перенос или вопрос до срока засчитывается как «предупредил».");
    }
    if (a.first_time_pct != null) s.push("С первого раза принято " + a.first_time_pct + "%.");
    if (a.stale) s.push(a.stale + " " + plural(a.stale, "поручение висит", "поручения висят", "поручений висят") + " дольше 10 дней.");
    if (!self && d.absent_days) s.push("В периоде " + d.absent_days + " " + plural(d.absent_days, "день", "дня", "дней") + " отпуска — сроки в эти дни не считаются.");
    return s.join(" ");
  }

  function excusedHtml(a, self) {
    var keys = Object.keys(a.excused || {});
    if (!keys.length) return "";
    var total = keys.reduce(function (n, k) { return n + a.excused[k]; }, 0);
    var inner = keys.map(function (k) {
      var label = REASON[k] || k;
      return row(label.charAt(0).toUpperCase() + label.slice(1), String(a.excused[k]));
    }).join("");
    return group("Не засчитано в просрочку · " + total, inner, self ? "Эти сроки не идут вам в минус." : "Эти сроки не идут в минус: причина не на исполнителе.");
  }

  function tagFor(t) {
    if (t.open && t.late_days) return ["bad", "просрочено " + t.late_days + " дн."];
    if (t.status === "late_warned") return ["warn", "предупредили, опоздали"];
    if (t.status === "late") return ["bad", "сдано с опозданием"];
    if (t.accepted) return ["ok", t.returns ? "принято после доработки" : "принято"];
    if (t.status === "on_time" && !t.open) return ["ok", "в срок"];
    if (t.open) return ["", "срок " + fmtDate(t.deadline)];
    if (t.status === "excluded") return ["", REASON[t.reason] || "не учитывается"];
    return ["", ""];
  }
  function tasksHtml(d, self) {
    var hidden = d.hidden_tasks || 0;
    if (!d.tasks.length && !hidden) return "";
    var inner = d.tasks.map(function (t) {
      var tag = tagFor(t);
      var sub = (self && t.owner_name ? "от " + t.owner_name + " · " : "") + "срок " + fmtDate(t.deadline);
      return '<button type="button" class="pf-row" data-task="' + t.id + '"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
        esc(t.title) + '</span><span class="pf-row-sub">' + esc(sub) + "</span></span>" +
        (tag[1] ? '<span class="metrics-tag' + (tag[0] ? " metrics-tag--" + tag[0] : "") + '">' + esc(tag[1]) + "</span>" : "") +
        "</span></button>";
    }).join("");
    if (hidden) {
      inner += row("От других постановщиков", String(hidden), "Названия видят только они — здесь только число");
    }
    return group(self ? "Поручения" : "Ваши поручения", inner);
  }

  function absenceHtml(d, self) {
    var items = d.absences || [];
    if (!self) {
      if (!items.length) return "";
      return group("Отпуск и больничный", items.map(function (it) { return row(fmtDay(it.from) + " — " + fmtDay(it.to), ""); }).join(""));
    }
    var rows = items.map(function (it, i) {
      return '<div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' +
        esc(fmtDay(it.from) + " — " + fmtDay(it.to)) + '</span></span><button type="button" class="btn-text metrics-abs-del" data-absence-del="' + i + '">Убрать</button></span></div>';
    }).join("");
    var form = '<div class="metrics-absence-form"><input type="date" class="pf-input" id="metrics-abs-from" aria-label="С">' +
      '<input type="date" class="pf-input" id="metrics-abs-to" aria-label="По">' +
      '<button type="button" class="pf-btn" id="metrics-abs-add">Добавить</button></div>';
    return group("Отпуск и больничный", rows + form, "Сроки, которые пришлись на эти дни, не засчитываются.");
  }

  function ratingsText(a) {
    var r = a.ratings || {};
    if (!a.rated) return "—";
    return [["good", "хорошо"], ["ok", "нормально"], ["bad", "плохо"]]
      .filter(function (p) { return r[p[0]]; })
      .map(function (p) { return p[1] + " " + r[p[0]]; }).join(" · ");
  }

  function hrs(v) {
    return v == null ? "—" : String(v).replace(".", ",") + " ч";
  }

  // Нагрузка — контекст рядом с поручениями: объясняет сроки, в минус не идёт.
  function calendarHtml(c, self) {
    if (!c) return "";
    var hint = "Контекст, а не оценка: встречи считаются, где есть другие участники; отклонённые и «свободен» — нет.";
    if (!c.has_calendar) {
      return self ? group("Нагрузка календаря", row("Календарь не подключён", ""), "Подключите Google Календарь в профиле или ведите встречи в Орбите — здесь появятся часы встреч и время без встреч.") : "";
    }
    if (!c.workdays) {
      return group("Нагрузка календаря", row("История календаря собирается", ""), "Орбита сохраняет календарь у себя — цифры появятся после первой сверки.");
    }
    if (c.covered_from) hint = "Считается с " + fmtDate(c.covered_from) + ": раньше календарь в Орбиту не сохранялся. " + hint;
    return group("Нагрузка календаря",
      row("Встречи, в среднем за рабочий день", hrs(c.meeting_hours_per_day), c.meetings + " " + plural(c.meetings, "встреча", "встречи", "встреч") + " · " + hrs(c.meeting_hours) + " всего") +
      row("Время без встреч, в среднем за день", hrs(c.focus_hours_per_day), "окна от 2 часов подряд с 9 до 19") +
      row("Дней без 2 свободных часов подряд", c.low_focus_days + " из " + c.workdays) +
      row("Встречи вне рабочих часов", hrs(c.after_hours), "до 9, после 19 и в выходные") +
      (c.task_hours ? row("Задачи в календаре", hrs(c.task_hours)) : ""),
      hint);
  }

  // «Без ответа три рабочих дня» — раскрывается списком: чат, тема, кто и когда спросил, ссылка в Telegram.
  // Текста сообщения нет: его видит по ссылке только тот, кто сам состоит в чате.
  function fmtWhen(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) + ", " +
      d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  function silentHtml(c) {
    var items = c.unanswered_items || [];
    if (!c.unanswered || !items.length) return row("Без ответа три рабочих дня", String(c.unanswered || 0));
    var list = items.map(function (it) {
      var place = it.chat + (it.topic ? " · " + it.topic : "");
      var sub = it.from + " · " + fmtWhen(it.asked_at);
      var body = '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(place) +
        '</span><span class="pf-row-sub">' + esc(sub) + "</span></span>" +
        (it.url ? '<span class="metrics-tag">Открыть</span>' : "") + "</span>";
      return it.url
        ? '<button type="button" class="pf-row metrics-silent-item" data-tg-url="' + esc(it.url) + '" title="Открыть сообщение в Telegram">' + body + "</button>"
        : '<div class="pf-row pf-row--static metrics-silent-item">' + body + "</div>";
    }).join("");
    var more = c.unanswered > items.length ? '<p class="pf-hint">Показаны последние ' + items.length + " из " + c.unanswered + "</p>" : "";
    return '<details class="metrics-silent"><summary class="pf-row"><span class="pf-row-body"><span class="pf-row-text">' +
      '<span class="pf-row-title">Без ответа три рабочих дня</span><span class="pf-row-sub">где именно — нажмите</span></span>' +
      '<span class="pf-row-val">' + esc(String(c.unanswered)) + ' <span class="metrics-silent-chev" aria-hidden="true">›</span></span></span></summary>' +
      '<div class="metrics-silent-list">' + list + more + "</div></details>";
  }
  function openChatLink(b) {
    var url = b && b.getAttribute("data-tg-url");
    if (!url) return false;
    if (app().openLink) app().openLink(url);
    else window.open(url, "_blank");
    return true;
  }

  function chatsHtml(c, self) {
    if (!c || !c.requests) return "";
    return group("Ответы в рабочих чатах",
      row(self ? "Обращались к вам" : "Обращений", String(c.requests), c.chats ? "в " + c.chats + " " + plural(c.chats, "чате", "чатах", "чатах") : "") +
      row("Ответ, обычно", c.reply_hours == null ? "мало данных" : hours(c.reply_hours)) +
      silentHtml(c) +
      (c.late_replies ? row("Ответ позже трёх рабочих дней", String(c.late_replies)) : ""),
      "Считаются только ответ на сообщение и @упоминание в рабочих группах, где есть Лео. Тексты сообщений не показываются: по ссылке их видит только тот, кто сам состоит в чате.");
  }

  function contributionHtml(c, s) {
    c = c || {};
    if (!c.kb_added && !c.kb_updated && !(s && s.accepted)) return "";
    return group("Вклад",
      row("Добавлено в базу знаний", String(c.kb_added || 0)) +
      row("Обновлено или подтверждено", String(c.kb_updated || 0)) +
      (s && s.posted ? row("Своих поручений доведено до «Принято»", String(s.accepted || 0), s.rated ? "оценено: " + s.rated : "") : ""),
      "Только как контекст: объём не равен качеству.");
  }

  // Вывод Лео подгружается после карточки: модель формулирует по уже посчитанным числам.
  async function loadLead(path) {
    var el = document.querySelector("[data-metrics-lead]");
    if (!el || !app().apiFetch) return;
    var key = el.getAttribute("data-metrics-lead-key") || "";
    try {
      var r = await app().apiFetch(path, { method: "GET" });
      var cur = document.querySelector("[data-metrics-lead]");
      if (!r || !r.text || !cur || cur.getAttribute("data-metrics-lead-key") !== key) return;
      cur.textContent = r.text;
      cur.classList.add("is-leo");
    } catch (_) {
      /* остаётся текст по шаблону */
    }
  }

  function segHtml(days) {
    return '<div class="pf-seg" role="tablist" aria-label="Период">' +
      [[28, "4 недели"], [84, "12 недель"]].map(function (p) {
        var on = p[0] === days;
        return '<button type="button" role="tab" data-days="' + p[0] + '" aria-selected="' + on + '"' + (on ? ' class="is-on"' : "") + ">" + p[1] + "</button>";
      }).join("") + "</div>";
  }

  // Карточка показателей: своя (self) или человека для руководителя — те же цифры.
  function cardHtml(d, opts) {
    var self = !!(opts && opts.self);
    var a = d.assignee, t = d.trend, s = d.setter;
    var html = '<p class="metrics-lead"><span class="metrics-lead-text" data-metrics-lead data-metrics-lead-key="' +
      esc((d.user_id || "") + ":" + d.days) + '">' + esc(leadText(d, self)) + "</span>" +
      (self ? "<small>Руководитель отдела видит те же цифры. Тексты переписки и задач ему не показываются.</small>"
        : "<small>Человек видит у себя ровно эти цифры.</small>") + "</p>";
    html += '<div class="metrics-kpis">' +
      kpi("В срок", a.on_time_pct == null ? null : a.on_time_pct + "%", trend(t.on_time_pct, false, " п.п.")) +
      kpi("С первого раза", a.first_time_pct == null ? null : a.first_time_pct + "%", trend(t.first_time_pct, false, " п.п.")) +
      kpi("Реакция на поручение", a.reaction_hours == null ? null : hours(a.reaction_hours), trend(t.reaction_hours, true, " ч")) +
      kpi("Висят дольше 10 дней", String(a.stale)) +
      "</div>";
    html += group("Надёжность",
      row("Поручений получено", String(a.received), a.from_people ? "от " + a.from_people + " " + plural(a.from_people, "человека", "человек", "человек") : "") +
      row("Дошли до срока", String(a.deadlines)) +
      row("Сдано в срок", String(a.on_time)) +
      row("Опоздания", String(a.late), a.late ? "из них предупредили заранее: " + a.late_warned : "") +
      (a.late_open ? row("Просрочено и не сдано", String(a.late_open)) : ""));
    html += group("Качество",
      row("Принято постановщиком", String(a.accepted)) +
      row("С первого раза", String(a.first_time)) +
      row("Возвраты на доработку", String(a.returns)) +
      row("Отказы с причиной", String(a.declines)) +
      row("Оценки постановщиков", ratingsText(a), a.rated ? "" : "Постановщик может оценить работу, когда нажимает «Принять»"),
      "Отказ с причиной — не минус: он честнее, чем взять и не сделать.");
    html += excusedHtml(a, self);
    html += calendarHtml(d.calendar, self);
    html += chatsHtml(d.chats, self);
    html += contributionHtml(d.contribution, s);
    if (s.posted) {
      html += group("Как постановщик",
        row("Поставили поручений", String(s.posted)) +
        row("Со сроком меньше рабочего дня", String(s.short_notice)) +
        row("Вызвали вопросы", String(s.with_questions)) +
        row(self ? "Ваш ответ на вопрос" : "Ответ на вопросы исполнителей", hours(s.answer_hours)) +
        row("Возвраты без пояснения", String(s.silent_returns)) +
        (s.waiting_on_me ? row(self ? "Ждут вашего ответа сейчас" : "Ждут ответа сейчас", String(s.waiting_on_me)) : ""),
        self ? "Это ваша сторона: короткие сроки и непонятные задачи мешают людям успевать." : "Короткие сроки и непонятные задачи мешают людям успевать.");
    }
    html += tasksHtml(d, self);
    html += absenceHtml(d, self);
    return html;
  }

  function render() {
    var el = root();
    if (!el) return;
    var d = state.data;
    var seg = segHtml(state.days);
    if (!d) {
      el.innerHTML = seg + '<p class="pf-hint">' + (state.loading ? "Считаю…" : "") + "</p>";
      return;
    }
    el.innerHTML = seg + '<button type="button" class="pj-hours-entry" data-project-hours="1"><span>Часы по проектам</span><small>встречи и задачи по неделям ›</small></button>' + cardHtml(d, { self: true });
    loadLead("/metrics/me/summary?days=" + state.days);
  }

  async function load() {
    state.loading = true;
    render();
    try {
      state.data = await app().apiFetch("/metrics/me?days=" + state.days, { method: "GET" });
    } catch (e) {
      var el = root();
      state.loading = false;
      if (el) el.innerHTML = '<p class="error">' + esc((e && (e.detail || e.message)) || "Не получилось загрузить") + "</p>";
      return;
    }
    state.loading = false;
    render();
  }

  async function saveAbsences(items) {
    try {
      await app().apiFetch("/me/absences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ absences: items }),
      });
    } catch (e) {
      if (app().toast) app().toast((e && (e.detail || e.message)) || "Не получилось сохранить");
      return;
    }
    await load();
  }

  function onClick(e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.projectHours != null) {
      if (window.LeoProjects && window.LeoProjects.openHours) window.LeoProjects.openHours();
      return;
    }
    if (b.dataset.days) {
      state.days = Number(b.dataset.days);
      state.data = null;
      load();
    } else if (b.dataset.task) {
      if (app().openTaskById) app().openTaskById(Number(b.dataset.task));
    } else if (b.dataset.tgUrl) {
      openChatLink(b);
    } else if (b.dataset.absenceDel != null) {
      var items = (state.data.absences || []).slice();
      items.splice(Number(b.dataset.absenceDel), 1);
      saveAbsences(items);
    } else if (b.id === "metrics-abs-add") {
      var from = document.getElementById("metrics-abs-from").value;
      var to = document.getElementById("metrics-abs-to").value || from;
      if (!from || to < from) {
        if (app().toast) app().toast("Укажите даты: с какого по какое");
        return;
      }
      saveAbsences((state.data.absences || []).concat([{ from: from, to: to, kind: "vacation" }]));
    }
  }

  function open() {
    if (app().profileScreen) app().profileScreen("metrics");
    load();
  }

  function init() {
    if (!window.LeoApp) {
      setTimeout(init, 200);
      return;
    }
    var openBtn = document.getElementById("profile-metrics-open");
    if (openBtn) openBtn.addEventListener("click", open);
    var back = document.getElementById("profile-metrics-back");
    if (back) back.addEventListener("click", function () { app().profileScreen("main"); });
    var el = root();
    if (el) el.addEventListener("click", onClick);
  }

  window.LeoMetrics = { open: open, cardHtml: cardHtml, openChatLink: openChatLink, loadLead: loadLead, segHtml: segHtml, esc: esc, plural: plural, hours: hours, trend: trend };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
