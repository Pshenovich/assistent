/* Составные блоки доски (webapp/board.js): оргкарточка и диаграмма Ганта — разметка и геометрия.
 * Таблица доски — таблица заметки (NoteRichEditor.mountTable), здесь её нет.
 *
 * Гант: {start: "YYYY-MM-DD", unit: "day"|"week", span, tasks: [{id, name, start, end, owner, color}]}.
 * Размеры — те же, что у сервера (assistant/lib/board_doc.py _gantt_geom).
 */
(function () {
  "use strict";

  var GANTT = {
    cols: [
      { key: "name", title: "Задача", w: 150 },
      { key: "start", title: "Начало", w: 64 },
      { key: "end", title: "Конец", w: 64 },
      { key: "owner", title: "Ответственный", w: 110 },
    ],
    labelW: 388,
    unitW: { day: 34, week: 64 },
    head: 44,
    row: 34,
    minRows: 1,
  };
  var MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  var BAR_COLORS = ["blue", "green", "purple", "orange", "pink", "red", "yellow", "gray"];
  var DAY = 86400000;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---- даты: ISO-строки, считаем в UTC (без сдвигов часовых поясов) ----
  function toMs(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    if (!m) return NaN;
    return Date.UTC(+m[1], +m[2] - 1, +m[3]);
  }

  function fromMs(ms) {
    var d = new Date(ms);
    return d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function today() {
    var d = new Date();
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function addDays(iso, n) {
    return fromMs(toMs(iso) + n * DAY);
  }

  function diffDays(a, b) {
    return Math.round((toMs(b) - toMs(a)) / DAY);
  }

  /** Понедельник недели с этой датой. */
  function weekStart(iso) {
    var d = new Date(toMs(iso));
    var wd = (d.getUTCDay() + 6) % 7;
    return addDays(iso, -wd);
  }

  function fmtDay(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    return m ? m[3] + "." + m[2] : "";
  }

  /** «5.11», «05.11.2026», «2026-11-05» → ISO; год по умолчанию — из ref. */
  function parseDay(text, ref) {
    var s = String(text || "").trim();
    var iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
    var y;
    var mo;
    var d;
    if (iso) {
      y = +iso[1];
      mo = +iso[2];
      d = +iso[3];
    } else {
      var m = /^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?$/.exec(s);
      if (!m) return null;
      d = +m[1];
      mo = +m[2];
      y = m[3] ? +m[3] : +(String(ref || today()).slice(0, 4));
      if (y < 100) y += 2000;
    }
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
    var out = fromMs(Date.UTC(y, mo - 1, d));
    return +out.slice(8, 10) === d ? out : null;
  }

  // ---- Гант ----
  function stepOf(el) {
    return el.unit === "week" ? 7 : 1;
  }

  function unitW(el) {
    return GANTT.unitW[el.unit === "week" ? "week" : "day"];
  }

  function ganttSize(el) {
    var rows = Math.max(GANTT.minRows, (el.tasks || []).length);
    return { w: GANTT.labelW + (el.span || 14) * unitW(el), h: GANTT.head + rows * GANTT.row + GANTT.row };
  }

  /** Шкала накрывает все задачи (+ одно деление запаса); размеры — из данных. */
  function ganttFit(el) {
    var tasks = el.tasks || [];
    var step = stepOf(el);
    var start = el.start || today();
    var minS = null;
    var maxE = null;
    tasks.forEach(function (t) {
      if (t.start && (!minS || t.start < minS)) minS = t.start;
      if (t.end && (!maxE || t.end > maxE)) maxE = t.end;
    });
    if (minS && minS < start) start = minS;
    if (step === 7) start = weekStart(start);
    var need = maxE ? Math.ceil((diffDays(start, maxE) + 1) / step) + 1 : 0;
    var span = Math.max(el.unit === "week" ? 8 : 14, need, 1);
    var next = Object.assign({}, el, { start: start, span: span });
    var size = ganttSize(next);
    next.w = size.w;
    next.h = size.h;
    return next;
  }

  /** Полоса задачи в координатах блока: x — от начала шкалы. */
  function barOf(el, t) {
    var step = stepOf(el);
    var a = diffDays(el.start, t.start) / step;
    var b = (diffDays(el.start, t.end) + 1) / step;
    return { x: GANTT.labelW + a * unitW(el), w: Math.max(6, (b - a) * unitW(el)) };
  }

  function ganttHtml(el, opts) {
    opts = opts || {};
    var tasks = el.tasks || [];
    var uw = unitW(el);
    var step = stepOf(el);
    var span = el.span || 14;
    var h = [];
    var gridW = span * uw;
    var size = ganttSize(el);
    h.push('<div class="lb-g" style="width:' + (GANTT.labelW + gridW) + "px;height:" + size.h + 'px">');
    // Шапка: подписи колонок слева, справа — месяц/неделя сверху и деления снизу.
    h.push('<div class="lb-g-head" style="height:' + GANTT.head + 'px">');
    var x = 0;
    GANTT.cols.forEach(function (c) {
      h.push('<div class="lb-g-hcell" style="left:' + x + "px;width:" + c.w + 'px">' + esc(c.title) + "</div>");
      x += c.w;
    });
    var lastMonth = -1;
    for (var i = 0; i < span; i++) {
      var iso = addDays(el.start, i * step);
      var mo = +iso.slice(5, 7) - 1;
      var day = +iso.slice(8, 10);
      var left = GANTT.labelW + i * uw;
      if (mo !== lastMonth) {
        h.push('<div class="lb-g-month" style="left:' + left + 'px">' + MONTHS[mo] + (mo === 0 || lastMonth === -1 ? " " + iso.slice(0, 4) : "") + "</div>");
        lastMonth = mo;
      }
      var wd = (new Date(toMs(iso)).getUTCDay() + 6) % 7;
      var label = step === 7 ? day + "–" + +addDays(iso, 6).slice(8, 10) : String(day);
      h.push('<div class="lb-g-tick' + (step === 1 && wd >= 5 ? " is-off" : "") + '" style="left:' + left + "px;width:" + uw + 'px">' + label + "</div>");
    }
    h.push("</div>");
    // Сетка дней — фон под строками; выходные чуть темнее.
    h.push('<div class="lb-g-body" style="top:' + GANTT.head + "px;height:" + (Math.max(GANTT.minRows, tasks.length) * GANTT.row + GANTT.row) + 'px">');
    for (var j = 0; j < span; j++) {
      var iso2 = addDays(el.start, j * step);
      var wd2 = (new Date(toMs(iso2)).getUTCDay() + 6) % 7;
      h.push('<div class="lb-g-col' + (step === 1 && wd2 >= 5 ? " is-off" : "") + '" style="left:' + (GANTT.labelW + j * uw) + "px;width:" + uw + 'px"></div>');
    }
    var now = opts.today || today();
    var nowD = diffDays(el.start, now);
    if (nowD >= 0 && nowD < span * step) {
      h.push('<div class="lb-g-now" style="left:' + (GANTT.labelW + ((nowD + 0.5) / step) * uw) + 'px"></div>');
    }
    tasks.forEach(function (t, r) {
      var top = r * GANTT.row;
      var sel = opts.selTask && opts.selTask === t.id;
      h.push('<div class="lb-g-row' + (sel ? " is-sel" : "") + '" style="top:' + top + "px;height:" + GANTT.row + 'px">');
      var cx = 0;
      GANTT.cols.forEach(function (c) {
        var v = c.key === "start" || c.key === "end" ? fmtDay(t[c.key]) : t[c.key] || "";
        h.push(
          '<div class="lb-g-cell lb-g-c-' + c.key + (v ? "" : " is-empty") + '" data-task="' + esc(t.id) + '" data-col="' + c.key + '" style="left:' + cx + "px;width:" + c.w + 'px">' +
          esc(v) + "</div>"
        );
        cx += c.w;
      });
      var b = barOf(el, t);
      h.push(
        '<div class="lb-g-bar' + (sel ? " is-sel" : "") + '" data-task="' + esc(t.id) + '" style="left:' + b.x + "px;width:" + b.w + "px;--c:var(--lb-ink-" + esc(t.color || "blue") + ')">' +
        '<span class="lb-g-edge is-start" data-edge="start"></span><span class="lb-g-edge is-end" data-edge="end"></span></div>'
      );
      h.push("</div>");
    });
    if (opts.editable) {
      h.push('<div class="lb-g-add" data-gadd="1" style="top:' + tasks.length * GANTT.row + "px;height:" + GANTT.row + 'px">+ Задача</div>');
    }
    h.push("</div></div>");
    return h.join("");
  }

  /** Новая задача — после последней (или с начала шкалы), на 3 дня / 1 неделю. */
  function newTask(el, id) {
    var tasks = el.tasks || [];
    var last = tasks[tasks.length - 1];
    var start = last ? addDays(last.end, 1) : el.start || today();
    var len = el.unit === "week" ? 6 : 2;
    return { id: id, name: "", start: start, end: addDays(start, len), color: BAR_COLORS[tasks.length % BAR_COLORS.length] };
  }

  // ---- оргкарточка ----
  var CARD_FIELDS = [
    { key: "title", ph: "Должность" },
    { key: "name", ph: "Имя Фамилия" },
    { key: "text", ph: "Обязанности" },
  ];

  function cardHtml(el, opts) {
    opts = opts || {};
    var st = el.style || {};
    var fill = st.fill && st.fill !== "none" ? st.fill : "purple";
    var photo = el.photo
      ? '<div class="lb-card-photo"><img alt="" src="' + esc(el.photo) + '"></div>'
      : '<div class="lb-card-photo is-empty" title="Добавить фото">' + (opts.editable ? "+ фото" : "") + "</div>";
    function field(key, cls) {
      var f = CARD_FIELDS.filter(function (x) {
        return x.key === key;
      })[0];
      var v = String(el[key] || "");
      return '<div class="' + cls + (v ? "" : " is-empty") + '" data-f="' + key + '" data-ph="' + esc(f.ph) + '">' + esc(v) + "</div>";
    }
    return (
      '<div class="lb-card" style="--head:var(--lb-fill-' + esc(fill) + ')">' +
      field("title", "lb-card-head") +
      '<div class="lb-card-body">' + photo + field("name", "lb-card-name") + field("text", "lb-card-about") + "</div></div>"
    );
  }

  window.LeoBoardBlocks = {
    GANTT: GANTT,
    BAR_COLORS: BAR_COLORS,
    CARD_FIELDS: CARD_FIELDS,
    today: today,
    addDays: addDays,
    diffDays: diffDays,
    weekStart: weekStart,
    fmtDay: fmtDay,
    parseDay: parseDay,
    stepOf: stepOf,
    unitW: unitW,
    ganttSize: ganttSize,
    ganttFit: ganttFit,
    barOf: barOf,
    ganttHtml: ganttHtml,
    newTask: newTask,
    cardHtml: cardHtml,
  };
})();
