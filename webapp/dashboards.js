/* Раздел «Дашборды»: страницы с цифрами, которые наполняются с серверов компании.
 * Данные и расчёт — сервер (assistant/lib/dashboard_data.py); здесь только рисование.
 * Виды виджетов — закрытый каталог (assistant/lib/dashboard_widgets.py), графики — свой SVG. */
(function () {
  "use strict";

  var PERIODS = [["7d", "7 дн"], ["30d", "30 дн"], ["90d", "90 дн"], ["365d", "Год"]];
  var SERIES = ["var(--dsh-s1)", "var(--dsh-s2)", "var(--dsh-s3)", "var(--dsh-s4)", "var(--dsh-s5)", "var(--dsh-s6)"];
  var TZ = (function () {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { return ""; }
  })();
  var state = {
    view: "list", list: null, orgs: [], id: 0, dash: null, period: "30d", editing: false,
    catalog: null, setup: null, loading: false, seq: 0,
  };

  function app() { return window.LeoApp || {}; }
  function api(path, opts) { return app().apiFetch(path, opts || { method: "GET" }); }
  function toast(t) { if (app().toast) app().toast(t); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function root() { return document.getElementById("dashboards-root"); }
  function color(i) { return SERIES[i % SERIES.length]; }
  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }

  // ---------- числа ----------

  function trimNum(x, digits) {
    var s = Number(x).toFixed(digits);
    if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s.replace(".", ",");
  }
  function grouped(n) {
    var neg = n < 0, s = String(Math.round(Math.abs(n)));
    s = s.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return (neg ? "−" : "") + s;
  }
  function fmt(v, format, unit, compact) {
    if (v == null || v === "" || isNaN(Number(v))) return "—";
    var n = Number(v), a = Math.abs(n), s;
    if (format === "percent") {
      s = trimNum(n, a < 10 ? 1 : 0) + "%";
      return unit && unit !== "%" ? s + " " + unit : s;
    }
    if (compact && a >= 1e9) s = (n < 0 ? "−" : "") + trimNum(a / 1e9, 1) + " млрд";
    else if (compact && a >= 1e6) s = (n < 0 ? "−" : "") + trimNum(a / 1e6, a >= 1e8 ? 0 : 1) + " млн";
    else if (compact && a >= 1e4) s = (n < 0 ? "−" : "") + trimNum(a / 1e3, 0) + " тыс";
    else if (a >= 100 || Number.isInteger(n)) s = grouped(n);
    else s = (n < 0 ? "−" : "") + trimNum(a, 2);
    var u = unit || (format === "money" ? "₽" : "");
    return u ? s + " " + u : s;
  }
  // подписи осей — без «₽», чтобы влезали
  function axisFmt(v, format) { return fmt(v, format === "money" ? "number" : format, "", true); }
  function deltaHtml(d, good) {
    if (d == null) return "";
    var up = d > 0, flat = Math.abs(d) < 0.05;
    var ok = flat ? "flat" : ((up && good !== "down") || (!up && good === "down")) ? "good" : "bad";
    var txt = flat ? "= 0%" : (up ? "↑ " : "↓ ") + trimNum(Math.abs(d), Math.abs(d) < 10 ? 1 : 0) + "%";
    return '<span class="dsh-delta dsh-delta--' + ok + '">' + esc(txt) + "</span>";
  }
  function ago(iso) {
    if (!iso) return "нет данных";
    var t = new Date(iso.length <= 19 ? iso + "Z" : iso);
    var m = Math.round((Date.now() - t.getTime()) / 60000);
    if (isNaN(m)) return "";
    if (m < 1) return "только что";
    if (m < 60) return m + " мин назад";
    var h = Math.round(m / 60);
    if (h < 24) return h + " ч назад";
    var d = Math.round(h / 24);
    return d + " " + plural(d, "день", "дня", "дней") + " назад";
  }
  function niceStep(raw) {
    if (!(raw > 0)) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  // Шкала с круглыми делениями: {min, max, step}; 3–5 делений.
  function scale(maxV, minV) {
    var lo = Math.min(0, minV || 0), hi = Math.max(0, maxV || 0);
    if (hi === lo) hi = lo + 1;
    var step = niceStep((hi - lo) / 4);
    return { min: Math.floor(lo / step) * step, max: Math.ceil(hi / step) * step, step: step };
  }

  function mdText(t) {
    var s = esc(t);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
  }
  function svgEl(w, h, inner) {
    return '<svg class="dsh-svg" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" role="img">' + inner + "</svg>";
  }
  function tipAttr(t) { return ' data-tip="' + esc(t) + '"'; }

  // ---------- графики ----------

  function axes(w, h, padL, padB, sc, fmtY) {
    var out = "", H = h - padB - 6;
    for (var v = sc.min; v <= sc.max + sc.step / 2; v += sc.step) {
      var y = h - padB - (v - sc.min) / ((sc.max - sc.min) || 1) * H;
      out += '<line x1="' + padL + '" x2="' + w + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '" stroke="' +
        (Math.abs(v) < sc.step / 2 ? "var(--dsh-axis)" : "var(--dsh-grid)") + '"/>';
      out += '<text x="' + (padL - 6) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">' + esc(fmtY(Math.abs(v) < sc.step / 1e6 ? 0 : v)) + "</text>";
    }
    return out;
  }
  function xLabels(labels, x0, step, h, maxN) {
    var n = labels.length, every = Math.max(1, Math.ceil(n / maxN)), out = "";
    for (var i = 0; i < n; i += every) {
      out += '<text x="' + (x0 + step * i + step / 2) + '" y="' + (h - 3) + '" text-anchor="middle">' + esc(labels[i]) + "</text>";
    }
    return out;
  }
  function timeChart(el, d, kind) {
    var w = Math.max(220, el.clientWidth || 300), h = w < 360 ? 150 : 190, padL = 46, padB = 18;
    var series = d.series || [], labels = d.labels || [], n = labels.length;
    if (!n || !series.length) { el.innerHTML = '<div class="dsh-w-none">Нет данных за период</div>'; return; }
    var stacked = kind === "stacked100" || !!d.stacked;
    var tops = [], bottoms = [], maxV = 0, minV = 0, i, s;
    for (i = 0; i < n; i++) {
      var pos = 0, tot = 0;
      for (s = 0; s < series.length; s++) tot += Math.max(0, Number(series[s].values[i]) || 0);
      tops.push([]); bottoms.push([]);
      for (s = 0; s < series.length; s++) {
        var v = series[s].values[i];
        if (v == null) { tops[i].push(null); bottoms[i].push(null); continue; }
        v = Number(v);
        if (kind === "stacked100") v = tot ? Math.max(0, v) / tot * 100 : 0;
        if (stacked) { bottoms[i].push(pos); pos += v; tops[i].push(pos); }
        else { bottoms[i].push(0); tops[i].push(v); }
        maxV = Math.max(maxV, stacked ? pos : v);
        minV = Math.min(minV, v);
      }
    }
    if (d.target != null) maxV = Math.max(maxV, Number(d.target));
    var sc = kind === "stacked100" ? { min: 0, max: 100, step: 25 } : scale(maxV, minV);
    var max = sc.max, min = sc.min;
    var plotW = w - padL, step = plotW / n, H = h - padB - 6;
    function y(v) { return h - padB - (v - min) / (max - min || 1) * H; }
    var fy = function (v) { return kind === "stacked100" ? Math.round(v) + "%" : axisFmt(v, d.format); };
    var out = axes(w, h, padL, padB, sc, fy);
    if (kind === "bars" || kind === "stacked100") {
      var groups = stacked ? 1 : series.length, bw = Math.max(2, Math.min(24, step * 0.7 / groups));
      for (i = 0; i < n; i++) {
        for (s = 0; s < series.length; s++) {
          if (tops[i][s] == null) continue;
          var x = padL + step * i + (step - bw * groups) / 2 + (stacked ? 0 : bw * s);
          var y1 = y(tops[i][s]), y0 = y(bottoms[i][s]);
          var top = Math.min(y0, y1), hh = Math.max(1, Math.abs(y0 - y1) - (stacked && s ? 1 : 0));
          var last = d.highlight_last && i === n - 1 && series.length === 1;
          out += '<rect x="' + x.toFixed(1) + '" y="' + top.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + hh.toFixed(1) +
            '" rx="' + Math.min(3, bw / 3) + '" fill="' + (last ? "var(--dsh-s3)" : color(s)) + '"/>';
        }
      }
    } else {
      for (s = series.length - 1; s >= 0; s--) {
        var pts = [], seg = [], paths = [], area = "";
        for (i = 0; i < n; i++) {
          if (tops[i][s] == null) { if (seg.length) paths.push(seg); seg = []; continue; }
          seg.push([padL + step * i + step / 2, y(tops[i][s]), y(bottoms[i][s])]);
        }
        if (seg.length) paths.push(seg);
        paths.forEach(function (p) {
          var line = p.map(function (q, k) { return (k ? "L" : "M") + q[0].toFixed(1) + "," + q[1].toFixed(1); }).join("");
          if (kind === "area") {
            var back = p.slice().reverse().map(function (q) { return "L" + q[0].toFixed(1) + "," + q[2].toFixed(1); }).join("");
            area += '<path d="' + line + back + 'Z" fill="' + color(s) + '" fill-opacity="0.18"/>';
          }
          pts.push('<path d="' + line + '" fill="none" stroke="' + color(s) + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"' +
            (s > 0 && kind === "line" ? ' stroke-dasharray="' + (s === 1 ? "5 3" : s === 2 ? "2 3" : "8 3 2 3") + '"' : "") + "/>");
          if (p.length === 1) pts.push('<circle cx="' + p[0][0] + '" cy="' + p[0][1] + '" r="3" fill="' + color(s) + '"/>');
        });
        out += area + pts.join("");
      }
    }
    if (d.target != null) {
      var ty = y(Number(d.target));
      out += '<line x1="' + padL + '" x2="' + w + '" y1="' + ty + '" y2="' + ty + '" stroke="var(--text-tertiary)" stroke-dasharray="4 4"/>' +
        '<text x="' + w + '" y="' + (ty - 4) + '" text-anchor="end">цель ' + esc(fmt(d.target, d.format, "", true)) + "</text>";
    }
    out += xLabels(labels, padL, step, h, Math.max(2, Math.floor(plotW / 70)));
    for (i = 0; i < n; i++) {
      var tip = labels[i] + ": " + series.map(function (sr) {
        var v2 = sr.values[i];
        return (series.length > 1 ? sr.name + " " : "") + (v2 == null ? "—" : fmt(v2, d.format, d.unit));
      }).join(" · ");
      out += '<rect class="dsh-hot" x="' + (padL + step * i) + '" y="0" width="' + step + '" height="' + (h - padB) + '" fill="transparent"' + tipAttr(tip) + "/>";
    }
    var legend = series.length > 1
      ? '<div class="dsh-legend">' + series.map(function (sr, k) { return '<span><i style="background:' + color(k) + '"></i>' + esc(sr.name) + "</span>"; }).join("") + "</div>"
      : "";
    el.innerHTML = legend + svgEl(w, h, out);
  }
  function spark(values, w, h) {
    var v = (values || []).map(function (x) { return x == null ? null : Number(x); });
    var nums = v.filter(function (x) { return x != null; });
    if (nums.length < 2) return "";
    var mx = Math.max.apply(null, nums), mn = Math.min.apply(null, nums), n = v.length, d = "", started = false;
    v.forEach(function (x, i) {
      if (x == null) return;
      var px = (i / (n - 1)) * w, py = h - 2 - ((x - mn) / ((mx - mn) || 1)) * (h - 4);
      d += (started ? "L" : "M") + px.toFixed(1) + "," + py.toFixed(1);
      started = true;
    });
    return svgEl(w, h, '<path d="' + d + '" fill="none" stroke="var(--dsh-s1)" stroke-width="1.6" stroke-linejoin="round"/>');
  }
  function donut(items, fmtV) {
    var total = items.reduce(function (a, b) { return a + b.value; }, 0) || 1, off = 0, segs = "";
    items.forEach(function (it, i) {
      var p = it.value / total * 100;
      segs += '<circle class="dsh-hot" cx="21" cy="21" r="15.9" fill="none" stroke="' + (it.label === "Прочее" ? "var(--dsh-other)" : color(i)) +
        '" stroke-width="6" stroke-dasharray="' + Math.max(0, p - 0.6).toFixed(2) + " " + (100 - p + 0.6).toFixed(2) + '" stroke-dashoffset="' + (-off).toFixed(2) +
        '" transform="rotate(-90 21 21)"' + tipAttr(it.label + ": " + fmtV(it.value) + " (" + Math.round(it.pct) + "%)") + "/>";
      off += p;
    });
    return '<svg viewBox="0 0 42 42" width="96" height="96" style="flex:none">' + segs + "</svg>";
  }
  function scatterChart(el, d) {
    var w = Math.max(220, el.clientWidth || 300), h = 190, padL = 46, padB = 26, pts = d.points || [];
    if (!pts.length) { el.innerHTML = '<div class="dsh-w-none">Нет данных</div>'; return; }
    var xs = pts.map(function (p) { return p.x; }), ys = pts.map(function (p) { return p.y; });
    var sx = scale(Math.max.apply(null, xs), Math.min.apply(null, xs)), sy = scale(Math.max.apply(null, ys), Math.min.apply(null, ys));
    var x0 = sx.min, x1 = sx.max, y0 = sy.min, y1 = sy.max;
    var out = axes(w, h, padL, padB, sy, function (v) { return fmt(v, "number", "", true); });
    for (var xv = sx.min; xv <= sx.max + sx.step / 2; xv += sx.step) {
      out += '<text x="' + (padL + (xv - x0) / ((x1 - x0) || 1) * (w - padL - 6)).toFixed(1) + '" y="' + (h - padB + 12) + '" text-anchor="middle">' + esc(fmt(xv, "number", "", true)) + "</text>";
    }
    pts.forEach(function (p) {
      var cx = padL + (p.x - x0) / ((x1 - x0) || 1) * (w - padL - 6), cy = h - padB - (p.y - y0) / ((y1 - y0) || 1) * (h - padB - 6);
      out += '<circle class="dsh-hot" cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + (p.hl ? 5.5 : 4) + '" fill="' + (p.hl ? "var(--dsh-s2)" : "var(--dsh-s1)") +
        '" fill-opacity="' + (p.hl ? 1 : 0.65) + '"' + tipAttr((p.label ? p.label + ": " : "") + d.x_label + " " + fmt(p.x) + ", " + d.y_label + " " + fmt(p.y)) + "/>";
    });
    out += '<text x="' + w + '" y="' + (h - 1) + '" text-anchor="end">' + esc(d.x_label) + " →</text>";
    out += '<text x="' + (padL + 4) + '" y="10">↑ ' + esc(d.y_label) + "</text>";
    el.innerHTML = svgEl(w, h, out);
  }
  function waterfallChart(el, d) {
    var steps = d.steps || [], w = Math.max(220, el.clientWidth || 300), h = 190, padL = 46, padB = 18;
    var vals = [0];
    steps.forEach(function (s) { vals.push(s.start, s.end); });
    var sc = scale(Math.max.apply(null, vals), Math.min.apply(null, vals)), max = sc.max, min = sc.min;
    var step = (w - padL) / (steps.length || 1), bw = Math.min(40, step * 0.65), H = h - padB - 6;
    function y(v) { return h - padB - (v - min) / ((max - min) || 1) * H; }
    var out = axes(w, h, padL, padB, sc, function (v) { return axisFmt(v, d.format); });
    steps.forEach(function (s, i) {
      var a = y(s.start), b = y(s.end), fill = s.total ? "var(--dsh-s6)" : s.value >= 0 ? "var(--dsh-s3)" : "var(--danger)";
      out += '<rect class="dsh-hot" x="' + (padL + step * i + (step - bw) / 2).toFixed(1) + '" y="' + Math.min(a, b).toFixed(1) + '" width="' + bw.toFixed(1) +
        '" height="' + Math.max(1, Math.abs(a - b)).toFixed(1) + '" rx="3" fill="' + fill + '"' +
        tipAttr(s.label + ": " + (s.value > 0 && !s.total ? "+" : "") + fmt(s.value, d.format, d.unit)) + "/>";
    });
    out += xLabels(steps.map(function (s) { return s.label; }), padL, step, h, Math.max(2, Math.floor((w - padL) / 60)));
    el.innerHTML = svgEl(w, h, out);
  }
  function groupedChart(el, d) {
    var cats = d.categories || [], fields = d.fields || [];
    if (!cats.length) { el.innerHTML = '<div class="dsh-w-none">Нет данных</div>'; return; }
    var legend = '<div class="dsh-legend">' + fields.map(function (f, k) { return '<span><i style="background:' + color(k) + '"></i>' + esc(f.label) + "</span>"; }).join("") + "</div>";
    var mx = 0;
    cats.forEach(function (c) { c.values.forEach(function (v) { mx = Math.max(mx, Number(v) || 0); }); });
    if (d.orientation === "horizontal") {
      el.innerHTML = legend + '<div class="dsh-list">' + cats.map(function (c) {
        return '<div><div class="dsh-row"><span>' + esc(c.label) + "</span></div>" + c.values.map(function (v, k) {
          return '<div class="dsh-bar dsh-hot" style="width:' + Math.max(1, (Number(v) || 0) / (mx || 1) * 100) + "%;background:" + color(k) + '"' +
            tipAttr(c.label + " · " + fields[k].label + ": " + fmt(v, d.format, d.unit)) + "></div>";
        }).join("") + "</div>";
      }).join("") + "</div>";
      return;
    }
    var w = Math.max(220, el.clientWidth || 300), h = 190, padL = 46, padB = 18, sc = scale(mx, 0), max = sc.max;
    var step = (w - padL) / cats.length, bw = Math.max(3, Math.min(22, step * 0.75 / fields.length)), H = h - padB - 6;
    var out = axes(w, h, padL, padB, sc, function (v) { return axisFmt(v, d.format); });
    cats.forEach(function (c, i) {
      c.values.forEach(function (v, k) {
        if (v == null) return;
        var hh = (Number(v) || 0) / max * H, x = padL + step * i + (step - bw * fields.length) / 2 + bw * k;
        out += '<rect class="dsh-hot" x="' + x.toFixed(1) + '" y="' + (h - padB - hh).toFixed(1) + '" width="' + (bw - 1).toFixed(1) + '" height="' + Math.max(1, hh).toFixed(1) +
          '" rx="3" fill="' + color(k) + '"' + tipAttr(c.label + " · " + fields[k].label + ": " + fmt(v, d.format, d.unit)) + "/>";
      });
    });
    out += xLabels(cats.map(function (c) { return c.label; }), padL, step, h, Math.max(2, Math.floor((w - padL) / 60)));
    el.innerHTML = legend + svgEl(w, h, out);
  }

  // ---------- виджеты ----------

  function noData(text) { return '<div class="dsh-w-none">' + esc(text || "Пока нет данных — источник ещё ничего не прислал") + "</div>"; }

  function wNumber(d) {
    if (d.empty) return noData();
    return '<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap"><span class="dsh-big">' + esc(fmt(d.value, d.format, d.unit, true)) +
      "</span>" + deltaHtml(d.delta_pct, d.good) + "</div>" +
      (d.prev != null ? '<div class="dsh-faint">прошлый период: ' + esc(fmt(d.prev, d.format, d.unit, true)) + "</div>" : "") +
      (d.spark && d.spark.length ? '<div data-spark="1"></div>' : "");
  }
  function wGoal(d) {
    var pct = d.pct == null ? 0 : Math.max(0, Math.min(100, d.pct));
    var per = { month: "месяца", quarter: "квартала", year: "года" }[d.goal_period] || "периода";
    return '<div style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap"><span class="dsh-big">' + esc(fmt(d.value, d.format, "", true)) +
      '</span><span class="dsh-muted">из ' + esc(fmt(d.target, d.format, d.unit, true)) + "</span></div>" +
      '<div class="dsh-meter"><i style="width:' + pct + '%"></i></div>' +
      '<div class="dsh-row dsh-row--wrap"><span class="dsh-faint">' + (d.pct == null ? "" : Math.round(d.pct) + "% · ") + "до конца " + per + " " + d.days_left + " " +
      plural(d.days_left, "день", "дня", "дней") + "</span>" +
      '<span class="dsh-delta dsh-delta--' + (d.on_pace ? "good" : "bad") + '">' + (d.on_pace ? "в темпе" : "отстаём") + "</span></div>";
  }
  function wKpis(d) {
    return '<div class="dsh-kpis">' + (d.items || []).map(function (x) {
      if (x.missing) return '<div class="dsh-kpi"><div class="dsh-faint">' + esc(x.label) + '</div><div class="dsh-w-err">источник удалён</div></div>';
      return '<div class="dsh-kpi"><div class="dsh-faint">' + esc(x.label) + '</div><div class="dsh-big">' + esc(fmt(x.value, x.format, x.unit, true)) +
        "</div>" + deltaHtml(x.delta_pct, x.good) + "</div>";
    }).join("") + "</div>";
  }
  function wRanking(d) {
    var items = d.items || [];
    if (!items.length) return noData();
    var mx = Math.max.apply(null, items.map(function (x) { return Math.abs(x.value); })) || 1;
    return '<div class="dsh-list">' + items.map(function (x) {
      return '<div><div class="dsh-row"><span>' + esc(x.label) + "</span><span>" + esc(fmt(x.value, d.format, d.unit, true)) + "</span></div>" +
        '<div class="dsh-bar" style="width:' + Math.max(1, Math.abs(x.value) / mx * 100) + '%"></div></div>';
    }).join("") + "</div>";
  }
  function wShare(d) {
    var items = d.items || [];
    if (!items.length) return noData();
    var fv = function (v) { return fmt(v, d.format, d.unit); };
    return '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">' + donut(items, fv) +
      '<div class="dsh-list" style="flex:1;min-width:120px">' + items.map(function (x, i) {
        return '<div class="dsh-row"><span><i class="dsh-sw" style="background:' + (x.label === "Прочее" ? "var(--dsh-other)" : color(i)) + '"></i>' + esc(x.label) +
          "</span><span>" + Math.round(x.pct) + "%</span></div>";
      }).join("") + "</div></div>";
  }
  function wSparkgrid(d) {
    var items = d.items || [];
    if (!items.length) return noData();
    return '<div class="dsh-list">' + items.map(function (x, i) {
      return '<div class="dsh-row"><span>' + esc(x.label) + '</span><span style="flex:1;max-width:120px" data-spark-i="' + i + '"></span><span>' +
        esc(fmt(x.value, d.format, d.unit, true)) + "</span></div>";
    }).join("") + "</div>";
  }
  function heatTable(rows, cols, matrix, max, fmtV, opts) {
    opts = opts || {};
    var html = '<div class="dsh-table-wrap"><table class="dsh-table dsh-heat"><thead><tr><th></th>' +
      cols.map(function (c) { return '<th class="is-num">' + esc(c) + "</th>"; }).join("") +
      (opts.rowTotals ? '<th class="is-num">Итого</th>' : "") + "</tr></thead><tbody>";
    rows.forEach(function (r, i) {
      html += "<tr><td>" + esc(r) + "</td>" + matrix[i].map(function (v) {
        if (v == null) return "<td></td>";
        var a = Math.round(Math.max(0, Math.min(1, (opts.scale ? v / opts.scale : v / (max || 1)))) * 55);
        return '<td class="is-num" style="background:color-mix(in srgb, ' + (opts.color || "var(--dsh-s1)") + " " + a + '%, transparent)">' + esc(fmtV(v)) + "</td>";
      }).join("") + (opts.rowTotals ? '<td class="is-num"><b>' + esc(fmtV(opts.rowTotals[i])) + "</b></td>" : "") + "</tr>";
    });
    if (opts.colTotals) {
      html += '<tr class="is-total"><td>Итого</td>' + opts.colTotals.map(function (v) { return '<td class="is-num">' + esc(fmtV(v)) + "</td>"; }).join("") +
        (opts.rowTotals ? '<td class="is-num">' + esc(fmtV(opts.rowTotals.reduce(function (a, b) { return a + b; }, 0))) + "</td>" : "") + "</tr>";
    }
    return html + "</tbody></table></div>";
  }
  function wHeatmap(d) {
    if (!d.max) return noData("Нет данных за период");
    var fv = function (v) { return axisFmt(v, d.format); };
    if (d.cols.length === 24) {
      var cell = "";
      d.rows.forEach(function (r, i) {
        cell += '<div style="display:flex;gap:2px;align-items:center"><span class="dsh-faint" style="width:22px">' + esc(r) + "</span>";
        d.matrix[i].forEach(function (v, j) {
          var a = v == null ? 4 : Math.round(8 + v / d.max * 80);
          cell += '<span class="dsh-hot" style="flex:1;height:14px;border-radius:2px;background:color-mix(in srgb, var(--dsh-s1) ' + a + '%, transparent)"' +
            tipAttr(r + " " + d.cols[j] + ":00 — " + (v == null ? "нет" : fmt(v, d.format, d.unit))) + "></span>";
        });
        cell += "</div>";
      });
      return '<div style="display:flex;flex-direction:column;gap:2px">' + cell +
        '<div class="dsh-row dsh-faint" style="padding-left:24px"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div></div>';
    }
    return heatTable(d.rows, d.cols, d.matrix, d.max, fv);
  }
  function wFunnel(d) {
    var items = d.items || [];
    if (!items.length) return noData();
    var first = items[0].value || 1;
    return '<div class="dsh-list">' + items.map(function (x, i) {
      var conv = d.show_conversion && i ? '<span class="dsh-faint"> · ' + Math.round(x.from_prev || 0) + "%</span>" : "";
      return '<div><div class="dsh-row"><span>' + esc(x.label) + "</span><span>" + esc(fmt(x.value, "number", "", true)) + conv + "</span></div>" +
        '<div class="dsh-funnel-bar" style="width:' + Math.max(4, x.value / first * 100) + '%"></div></div>';
    }).join("") + (d.show_conversion && items.length > 1
      ? '<div class="dsh-faint">Итоговая конверсия ' + Math.round(items[items.length - 1].from_first || 0) + "%</div>" : "") + "</div>";
  }
  function wBullet(d) {
    var items = d.items || [];
    if (!items.length) return noData();
    return '<div class="dsh-list">' + items.map(function (x) {
      var mx = Math.max(x.fact, x.plan || 0) || 1;
      var cls = x.level === "bad" ? "is-bad" : x.level === "warn" ? "is-warn" : "";
      return '<div><div class="dsh-row"><span>' + esc(x.label) + '</span><span class="dsh-delta dsh-delta--' + (x.level === "ok" ? "good" : x.level === "bad" ? "bad" : "flat") + '">' +
        (x.pct == null ? "—" : Math.round(x.pct) + "%") + "</span></div>" +
        '<div class="dsh-bullet dsh-hot"' + tipAttr(x.label + ": " + fmt(x.fact, d.format) + " из " + fmt(x.plan, d.format)) + '><i class="' + cls + '" style="width:' + (x.fact / mx * 100) + '%"></i>' +
        (x.plan ? '<b style="left:calc(' + (x.plan / mx * 100) + '% - 2px)"></b>' : "") + "</div></div>";
    }).join("") + "</div>";
  }
  var STATUS_IC = {
    ok: '<svg class="dsh-status-ic dsh-status-ic--ok" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-label="в порядке"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
    warn: '<svg class="dsh-status-ic dsh-status-ic--warn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-label="внимание"><path d="M12 3 2 21h20z"/><path d="M12 10v4M12 17.5v.5"/></svg>',
    fail: '<svg class="dsh-status-ic dsh-status-ic--fail" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-label="сбой"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>',
  };
  function wStatus(d) {
    var items = d.items || [];
    if (!items.length) return noData();
    return '<div class="dsh-list">' + items.map(function (x) {
      return '<div class="dsh-row"><span>' + (STATUS_IC[x.state] || STATUS_IC.warn) + esc(x.label) + '</span><span class="dsh-faint">' + esc(x.note) + "</span></div>";
    }).join("") + "</div>";
  }
  function wFeed(d) {
    var items = d.items || [];
    if (!items.length) return noData("Событий пока нет");
    return '<div class="dsh-list dsh-feed">' + items.map(function (x) {
      var t = new Date(x.ts + "Z"), today = new Date();
      var when = isNaN(t) ? "" : t.toDateString() === today.toDateString()
        ? t.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
        : t.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
      var txt = x.url ? '<a href="' + esc(x.url) + '" target="_blank" rel="noopener noreferrer">' + esc(x.text) + "</a>" : esc(x.text);
      return '<div class="dsh-row"><span>' + txt + '</span><span class="dsh-faint">' + esc(when) + "</span></div>";
    }).join("") + "</div>";
  }
  function wPivot(d) {
    if (!d.rows || !d.rows.length) return noData();
    var fv = function (v) { return axisFmt(v, d.format); };
    return heatTable(d.rows, d.cols, d.matrix, d.max, fv, d.totals_meaningful ? { rowTotals: d.row_totals, colTotals: d.col_totals } : {});
  }
  function wCohort(d) {
    if (!d.cohorts || !d.cohorts.length) return noData();
    return heatTable(d.cohorts, d.steps.map(function (s) { return "М" + s; }), d.matrix, 100, function (v) { return Math.round(v) + "%"; },
      { scale: 100, color: "var(--dsh-s3)" });
  }

  // таблица с сортировкой, поиском, фильтром, итогами и CSV
  var tableUi = {};
  function cellHtml(v, type) {
    if (v == null || v === "") return "";
    if (type === "money") return esc(fmt(v, "money"));
    if (type === "number") return esc(fmt(v));
    if (type === "percent") return esc(fmt(v, "percent"));
    if (type === "progress") {
      var p = Math.max(0, Math.min(100, Number(v) <= 1 ? Number(v) * 100 : Number(v)));
      return '<span class="dsh-cell-progress dsh-hot"' + tipAttr(Math.round(p) + "%") + '><i style="width:' + p + '%"></i></span>';
    }
    if (type === "status") {
      var s = String(v).toLowerCase();
      var cls = /оплач|готов|done|ok|успе|закрыт|won|актив/.test(s) ? "ok" : /просроч|отказ|fail|ошиб|lost|отмен/.test(s) ? "bad" : /счёт|счет|ожид|wait|warn|риск/.test(s) ? "warn" : "";
      return '<span class="dsh-pill' + (cls ? " dsh-pill--" + cls : "") + '">' + esc(v) + "</span>";
    }
    if (type === "link") {
      var u = String(v);
      return /^https?:\/\//.test(u) ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(u.replace(/^https?:\/\//, "").slice(0, 40)) + "</a>" : esc(u);
    }
    if (type === "date") {
      var t = new Date(v);
      return isNaN(t) ? esc(v) : esc(t.toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: t.getFullYear() === new Date().getFullYear() ? undefined : "numeric" }));
    }
    return esc(v);
  }
  function isNumType(t) { return t === "number" || t === "money" || t === "percent" || t === "progress"; }
  function wTable(d, w) {
    var ui = tableUi[w.id] || (tableUi[w.id] = { q: "", st: "", sort: d.sort_field, dir: d.sort_dir, all: false });
    var cols = d.columns || [], rows = (d.rows || []).slice();
    if (!rows.length) return noData();
    var q = ui.q.trim().toLowerCase();
    if (q) rows = rows.filter(function (r) { return cols.some(function (c) { return String(r[c.name] == null ? "" : r[c.name]).toLowerCase().indexOf(q) >= 0; }); });
    if (ui.st && d.status_filter) rows = rows.filter(function (r) { return String(r[d.status_filter]) === ui.st; });
    if (ui.sort) {
      var col = cols.filter(function (c) { return c.name === ui.sort; })[0], num = col ? isNumType(col.type) : false, k = ui.dir === "asc" ? 1 : -1;
      rows.sort(function (a, b) {
        var x = a[ui.sort], y = b[ui.sort];
        if (num) return ((Number(x) || 0) - (Number(y) || 0)) * k;
        return String(x == null ? "" : x).localeCompare(String(y == null ? "" : y), "ru") * k;
      });
    }
    var shown = ui.all ? rows : rows.slice(0, d.limit || 50);
    var statuses = [];
    if (d.status_filter) (d.rows || []).forEach(function (r) { var s = r[d.status_filter]; if (s != null && statuses.indexOf(String(s)) < 0) statuses.push(String(s)); });
    var tools = '<div class="dsh-tools">' +
      (d.search ? '<input type="search" placeholder="Поиск" data-tq="' + w.id + '" value="' + esc(ui.q) + '" aria-label="Поиск по таблице">' : "") +
      (statuses.length ? '<select data-tst="' + w.id + '" aria-label="Фильтр по статусу"><option value="">Все статусы</option>' +
        statuses.map(function (s) { return '<option value="' + esc(s) + '"' + (ui.st === s ? " selected" : "") + ">" + esc(s) + "</option>"; }).join("") + "</select>" : "") +
      '<button type="button" class="dsh-btn dsh-btn--icon" data-tcsv="' + w.id + '" aria-label="Скачать CSV" title="Скачать CSV"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16"/></svg></button></div>';
    var head = "<tr>" + cols.map(function (c) {
      var arrow = ui.sort === c.name ? (ui.dir === "asc" ? " ↑" : " ↓") : "";
      return '<th class="' + (isNumType(c.type) ? "is-num" : "") + '" data-tsort="' + w.id + ":" + esc(c.name) + '">' + esc(c.label) + arrow + "</th>";
    }).join("") + "</tr>";
    var body = shown.map(function (r) {
      return "<tr>" + cols.map(function (c) { return '<td class="' + (isNumType(c.type) ? "is-num" : "") + '">' + cellHtml(r[c.name], c.type) + "</td>"; }).join("") + "</tr>";
    }).join("");
    var tot = d.totals || {};
    if (Object.keys(tot).length) {
      body += '<tr class="is-total">' + cols.map(function (c, i) {
        if (tot[c.name] != null) return '<td class="is-num">' + cellHtml(tot[c.name], c.type === "progress" ? "number" : c.type) + "</td>";
        return i === 0 ? "<td>Итого · " + d.total_rows + "</td>" : "<td></td>";
      }).join("") + "</tr>";
    }
    var more = rows.length > shown.length ? '<button type="button" class="dsh-btn" data-tall="' + w.id + '">Показать все (' + rows.length + ")</button>" : "";
    return tools + '<div class="dsh-table-wrap"><table class="dsh-table"><thead>' + head + "</thead><tbody>" + body + "</tbody></table></div>" + more;
  }
  function tableCsv(w) {
    var d = w.data, cols = d.columns || [];
    var lines = [cols.map(function (c) { return '"' + String(c.label).replace(/"/g, '""') + '"'; }).join(";")];
    (d.rows || []).forEach(function (r) {
      lines.push(cols.map(function (c) { var v = r[c.name]; return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"'; }).join(";"));
    });
    var blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (w.title || "table").replace(/[\\/:*?"<>|]+/g, " ") + ".csv";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  var CHARTS = { line: 1, bars: 1, area: 1, stacked100: 1, scatter: 1, waterfall: 1, grouped: 1 };
  function widgetBody(w) {
    if (w.error) return '<div class="dsh-w-err">' + esc(w.error) + "</div>";
    var d = w.data || {};
    switch (w.kind) {
      case "number": return wNumber(d);
      case "goal": return wGoal(d);
      case "kpi_row": return wKpis(d);
      case "ranking": return wRanking(d);
      case "share": return wShare(d);
      case "sparkgrid": return wSparkgrid(d);
      case "heatmap": return wHeatmap(d);
      case "funnel": return wFunnel(d);
      case "bullet": return wBullet(d);
      case "status": return wStatus(d);
      case "feed": return wFeed(d);
      case "pivot": return wPivot(d);
      case "cohort": return wCohort(d);
      case "table": return wTable(d, w);
      case "text": return '<div class="dsh-text">' + mdText(d.text) + "</div>";
      default: return CHARTS[w.kind] ? '<div data-chart="1"></div>' : "";
    }
  }
  function drawCharts(scope) {
    (scope || root()).querySelectorAll(".dsh-w").forEach(function (el) {
      var w = findWidget(Number(el.getAttribute("data-wid")));
      if (!w || w.error) return;
      var c = el.querySelector("[data-chart]");
      if (c) {
        if (w.kind === "scatter") scatterChart(c, w.data);
        else if (w.kind === "waterfall") waterfallChart(c, w.data);
        else if (w.kind === "grouped") groupedChart(c, w.data);
        else timeChart(c, w.data, w.kind);
      }
      var sp = el.querySelector("[data-spark]");
      if (sp) sp.innerHTML = spark(w.data.spark, Math.max(80, sp.clientWidth || 180), 30);
      el.querySelectorAll("[data-spark-i]").forEach(function (s) {
        var it = (w.data.items || [])[Number(s.getAttribute("data-spark-i"))];
        if (it) s.innerHTML = spark(it.spark, Math.max(50, s.clientWidth || 80), 16);
      });
    });
  }
  function findWidget(id) {
    var ws = (state.dash && state.dash.widgets) || [];
    for (var i = 0; i < ws.length; i++) if (ws[i].id === id) return ws[i];
    return null;
  }

  // ---------- экраны ----------

  var IC = {
    plug: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"/></svg>',
    db: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6M4 11.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>',
    dots: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
  };

  function renderList() {
    var r = root();
    if (!r) return;
    var items = state.list || [];
    var head = '<div class="dsh-head"><div class="dsh-head-left"><h1 class="screen-title">Дашборды</h1></div><div class="dsh-head-actions">' +
      '<button type="button" class="dsh-btn" data-act="connect">' + IC.plug + "Как подключить</button>" +
      '<button type="button" class="dsh-btn dsh-btn--primary" data-act="create">' + IC.plus + "Создать</button></div></div>";
    if (state.list == null) { r.innerHTML = head + '<div class="dsh-w-none">Загружаю…</div>'; return; }
    if (!items.length) {
      r.innerHTML = head + '<div class="dsh-empty"><h2>Соберите цифры компании в одном месте</h2>' +
        "<p>Дашборд обновляется сам: ИИ-агент через MCP Орбиты создаёт его и настраивает отправку данных с вашего сервера — выручка, заявки, загрузка, статусы сервисов.</p>" +
        '<button type="button" class="dsh-btn dsh-btn--primary" data-act="connect">' + IC.plug + "Как подключить</button></div>";
      return;
    }
    r.innerHTML = head + '<div class="dsh-cards">' + items.map(function (d) {
      var h = d.headline;
      var org = d.org_id ? ((state.orgs.filter(function (o) { return o.id === d.org_id; })[0] || {}).name || "") : "Личный";
      var value = h ? '<div><div class="dsh-faint">' + esc(h.label) + '</div><div class="dsh-card-value">' + esc(fmt(h.value, h.format, h.unit, true)) + "</div>" + deltaHtml(h.delta_pct, h.good) + "</div>" +
        (h.spark && h.spark.length ? '<span style="width:72px">' + spark(h.spark, 72, 24) + "</span>" : "")
        : '<span class="dsh-faint">' + d.widgets + " " + plural(d.widgets, "виджет", "виджета", "виджетов") + "</span>";
      return '<button type="button" class="dsh-card' + (d.stale ? " is-stale" : "") + '" data-open="' + d.id + '">' +
        '<span class="dsh-card-title">' + esc(d.title) + "</span>" +
        '<span class="dsh-faint">' + esc(org) + " · " + (d.stale ? '<span class="dsh-pill dsh-pill--warn">давно нет данных</span>' : esc(ago(d.last_push_at))) + "</span>" +
        '<span class="dsh-card-row">' + value + "</span></button>";
    }).join("") + "</div>";
  }

  function renderDash() {
    var r = root(), d = state.dash;
    if (!r || !d) return;
    var can = d.can || {};
    var seg = '<span class="dsh-seg" role="group" aria-label="Период">' + PERIODS.map(function (p) {
      return '<button type="button" data-period="' + p[0] + '" class="' + (state.period === p[0] ? "is-on" : "") + '">' + p[1] + "</button>";
    }).join("") + "</span>";
    var status = d.stale ? '<span class="dsh-pill dsh-pill--warn">давно нет данных</span>' : '<span class="dsh-muted">обновлено ' + esc(ago(d.last_push_at)) + "</span>";
    var html = '<button type="button" class="dsh-back" data-act="back">← Все дашборды</button>' +
      '<div class="dsh-head"><div class="dsh-head-left"><h1 class="screen-title">' + esc(d.title) + "</h1>" + status + "</div>" +
      '<div class="dsh-head-actions">' + seg +
      (can.own && d.org_id ? '<button type="button" class="dsh-btn dsh-btn--icon" data-act="access" aria-label="Доступ" title="Доступ">' + IC.users + "</button>" : "") +
      (can.edit ? '<button type="button" class="dsh-btn dsh-btn--icon" data-act="sources" aria-label="Источники" title="Источники данных">' + IC.db + "</button>" : "") +
      (can.edit ? '<button type="button" class="dsh-btn dsh-btn--icon" data-act="menu" aria-label="Ещё" title="Ещё">' + IC.dots + "</button>" : "") +
      "</div></div>";
    if (d.description) html += '<p class="dsh-muted" style="margin:-0.5rem 0 0.85rem">' + esc(d.description) + "</p>";
    if (state.editing) {
      html += '<div class="dsh-edit-bar"><b>Правка</b><span class="dsh-muted">порядок ← →, ширина − +, ✎ — вид и опции</span><span style="flex:1"></span>' +
        '<button type="button" class="dsh-btn" data-act="add-widget">' + IC.plus + "Виджет</button>" +
        '<button type="button" class="dsh-btn dsh-btn--primary" data-act="edit-done">Готово</button></div>';
    }
    var ws = d.widgets || [];
    if (!ws.length) {
      html += '<div class="dsh-empty"><h2>Виджетов пока нет</h2><p>Попросите агента добавить виджеты через MCP или добавьте сами — из готового каталога.</p>' +
        (can.edit ? '<button type="button" class="dsh-btn dsh-btn--primary" data-act="add-widget">' + IC.plus + "Добавить виджет</button>" : "") + "</div>";
    } else {
      html += '<div class="dsh-board' + (state.editing ? " dsh-editing" : "") + '">' + ws.map(function (w, i) {
        var tools = '<span class="dsh-w-tools">' +
          '<button type="button" data-wmove="' + w.id + ':-1" aria-label="Левее"' + (i ? "" : " disabled") + ">←</button>" +
          '<button type="button" data-wmove="' + w.id + ':1" aria-label="Правее"' + (i < ws.length - 1 ? "" : " disabled") + ">→</button>" +
          '<button type="button" data-wwidth="' + w.id + ':-1" aria-label="Уже">−</button>' +
          '<button type="button" data-wwidth="' + w.id + ':1" aria-label="Шире">+</button>' +
          '<button type="button" data-wedit="' + w.id + '" aria-label="Изменить">✎</button>' +
          '<button type="button" data-wdel="' + w.id + '" aria-label="Убрать">×</button></span>';
        return '<section class="dsh-w dsh-w--' + Math.max(1, Math.min(4, w.width || 1)) + (w.kind === "kpi_row" ? " dsh-w--wide-m" : "") + '" data-wid="' + w.id + '">' +
          '<div class="dsh-w-title"><span>' + esc(w.title) + "</span>" + tools + "</div>" + widgetBody(w) + "</section>";
      }).join("") + "</div>";
    }
    r.innerHTML = html;
    drawCharts(r);
  }

  function render() {
    if (state.view === "dash") renderDash();
    else renderList();
  }

  // ---------- загрузка ----------

  async function loadList(silent) {
    if (!silent && state.list == null) renderList();
    try {
      var res = await api("/dashboards?tz=" + encodeURIComponent(TZ));
      state.list = res.items || [];
      state.orgs = res.orgs || [];
    } catch (e) {
      if (state.list == null) state.list = [];
      if (!silent) toast((e && e.message) || "Не удалось загрузить дашборды");
    }
    if (state.view === "list") renderList();
  }
  async function loadDash(id, silent) {
    var my = ++state.seq;
    try {
      var res = await api("/dashboards/" + id + "?period=" + state.period + "&tz=" + encodeURIComponent(TZ));
      if (my !== state.seq) return;
      state.dash = res.dashboard;
      state.id = id;
      state.view = "dash";
      renderDash();
    } catch (e) {
      if (my !== state.seq) return;
      if (!silent) {
        toast((e && e.message) || "Дашборд не открылся");
        backToList();
      }
    }
  }
  function openDash(id) {
    state.id = Number(id);
    state.view = "dash";
    state.editing = false;
    var cached = (state.list || []).filter(function (x) { return x.id === state.id; })[0];
    var r = root();
    if (r) r.innerHTML = '<button type="button" class="dsh-back" data-act="back">← Все дашборды</button><div class="dsh-head"><h1 class="screen-title">' +
      esc(cached ? cached.title : "Дашборд") + '</h1></div><div class="dsh-w-none">Загружаю…</div>';
    try { history.replaceState(null, "", setParam(location.href, "dashboard", String(state.id))); } catch (_) {}
    return loadDash(state.id);
  }
  function backToList() {
    state.view = "list";
    state.dash = null;
    state.id = 0;
    state.editing = false;
    try { history.replaceState(null, "", setParam(location.href, "dashboard", "")); } catch (_) {}
    renderList();
    loadList(true);
  }
  function setParam(href, key, val) {
    var u = new URL(href);
    if (val) u.searchParams.set(key, val);
    else u.searchParams.delete(key);
    return u.toString();
  }
  async function ensureCatalog() {
    if (state.catalog) return state.catalog;
    var res = await api("/dashboards/catalog");
    state.catalog = res.widgets || [];
    state.setup = res;
    return state.catalog;
  }

  // ---------- окна ----------

  function sheet(title, bodyHtml, onMount) {
    var ov = document.createElement("div");
    ov.className = "dsh-sheet-ov";
    ov.innerHTML = '<div class="dsh-sheet" role="dialog" aria-modal="true"><div class="dsh-sheet-head"><h2>' + esc(title) +
      '</h2><button type="button" class="dsh-btn dsh-btn--icon" data-close="1" aria-label="Закрыть">✕</button></div><div class="dsh-sheet-body">' + bodyHtml + "</div></div>";
    function close() { ov.remove(); document.removeEventListener("keydown", onKey, true); }
    function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }
    ov.addEventListener("click", function (e) { if (e.target === ov || (e.target.closest && e.target.closest("[data-close]"))) close(); });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(ov);
    if (onMount) onMount(ov.querySelector(".dsh-sheet-body"), close);
    return close;
  }
  function copyBtn(text, label) {
    return '<button type="button" class="dsh-btn" data-copy="' + esc(text) + '">' + esc(label || "Скопировать") + "</button>";
  }
  function bindCopy(body) {
    body.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("[data-copy]");
      if (!b) return;
      var t = b.getAttribute("data-copy");
      (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () { toast("Скопировано"); }, function () {
        window.prompt("Скопируйте:", t);
      });
    });
  }

  async function openConnect() {
    try { await ensureCatalog(); } catch (_) {}
    var s = state.setup || {};
    var cmd = s.dash_command || "claude mcp add --transport http orbita-dashboards https://getorbita.me/mcp/dashboards --header \"Authorization: Bearer <токен>\"";
    var ask = "Сделай в Орбите дашборд продаж: выручка по дням, план месяца, топ менеджеров. Данные бери из нашей базы и настрой отправку с сервера по расписанию. Инструкция — инструмент guide в orbita-dashboards.";
    sheet("Как подключить дашборды",
      "<p>Дашборды наполняются автоматически: ИИ-агент (например, Claude Code) создаёт их через MCP Орбиты и ставит на сервер компании скрипт, который присылает цифры.</p>" +
      "<p><b>1. Выпустите токен для агента.</b> Профиль → Аккаунт → «Доступ для ИИ», отметьте «Дашборды: чтение и запись». Токен показывается один раз.</p>" +
      '<button type="button" class="dsh-btn" data-act-account="1">Открыть «Доступ для ИИ»</button>' +
      '<p style="margin-top:1rem"><b>2. Подключите MCP у агента</b> — подставьте токен вместо &lt;токен&gt;:</p><code class="dsh-code">' + esc(cmd) + "</code>" + copyBtn(cmd) +
      '<p style="margin-top:1rem"><b>3. Попросите агента</b>, например:</p><code class="dsh-code">' + esc(ask) + "</code>" + copyBtn(ask) +
      '<p class="dsh-muted" style="margin-top:1rem">Агент может создавать дашборды, источники и виджеты, но удалить дашборд или источник и выдать доступ можете только вы. ' +
      (s.dash_guide_url ? '<a href="' + esc(s.dash_guide_url) + '" target="_blank" rel="noopener">Инструкция для агента</a>.' : "") + "</p>",
      function (body, close) {
        bindCopy(body);
        var acc = body.querySelector("[data-act-account]");
        if (acc) acc.addEventListener("click", function () {
          close();
          if (app().setTab) app().setTab("profile");
          setTimeout(function () { if (app().openAccount) app().openAccount(); }, 60);
        });
      });
  }

  function openCreate() {
    var orgs = state.orgs || [];
    sheet("Новый дашборд",
      '<label class="dsh-field">Название<input type="text" data-f="title" maxlength="80" placeholder="Продажи"></label>' +
      (orgs.length ? '<label class="dsh-field">Где<select data-f="org">' + orgs.map(function (o) { return '<option value="' + esc(o.id) + '">' + esc(o.name) + "</option>"; }).join("") +
        '<option value="">Личный — видите только вы</option></select></label>' : "") +
      '<p class="dsh-muted">Пустой дашборд. Наполнять его удобнее через агента — см. «Как подключить».</p>' +
      '<div class="dsh-head-actions" style="justify-content:flex-end"><button type="button" class="dsh-btn dsh-btn--primary" data-save="1">Создать</button></div>',
      function (body, close) {
        var t = body.querySelector('[data-f="title"]');
        t.focus();
        async function save() {
          var title = t.value.trim();
          if (!title) { t.focus(); return; }
          var org = body.querySelector('[data-f="org"]');
          try {
            var res = await api("/dashboards", { method: "POST", body: JSON.stringify({ title: title, org_id: org ? org.value : "" }) });
            close();
            state.list = null;
            loadList(true);
            state.dash = res.dashboard;
            state.id = res.dashboard.id;
            state.view = "dash";
            renderDash();
          } catch (e) { toast((e && e.message) || "Не удалось создать"); }
        }
        body.querySelector("[data-save]").addEventListener("click", save);
        t.addEventListener("keydown", function (e) { if (e.key === "Enter") save(); });
      });
  }

  function openMenu() {
    var d = state.dash, can = d.can || {};
    var items = [["rename", "Переименовать"], ["describe", "Описание"], ["edit", "Править виджеты"], ["add-widget", "Добавить виджет"], ["sources", "Источники данных"], ["connect", "Как подключить сервер"]];
    if (can.own && d.org_id) items.push(["access", "Доступ"]);
    if (can.own) items.push(["archive", "Убрать в архив"], ["delete", "Удалить дашборд"]);
    sheet(d.title, '<div class="dsh-list">' + items.map(function (it) {
      return '<button type="button" class="dsh-btn' + (it[0] === "delete" ? " dsh-btn--danger" : "") + '" data-m="' + it[0] + '" style="justify-content:flex-start">' + esc(it[1]) + "</button>";
    }).join("") + "</div>", function (body, close) {
      body.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("[data-m]");
        if (!b) return;
        close();
        action(b.getAttribute("data-m"));
      });
    });
  }

  async function patchDash(patch) {
    try {
      await api("/dashboards/" + state.id, { method: "PATCH", body: JSON.stringify(patch) });
      await loadDash(state.id, true);
    } catch (e) { toast((e && e.message) || "Не удалось сохранить"); }
  }

  function openSources() {
    var d = state.dash, can = d.can || {};
    var dss = d.datasets || [];
    var kindName = { metric: "метрика", table: "таблица", events: "лента" };
    var html = dss.length ? dss.map(function (s) {
      var st = s.last_error ? '<span class="dsh-pill dsh-pill--bad">ошибка</span>' : s.stale ? '<span class="dsh-pill dsh-pill--warn">давно нет данных</span>'
        : s.last_push_at ? '<span class="dsh-pill dsh-pill--ok">работает</span>' : '<span class="dsh-pill">ждёт данных</span>';
      return '<div class="dsh-src"><div class="dsh-row"><span><b>' + esc(s.title) + '</b> <span class="dsh-faint">' + esc(s.key) + " · " + esc(kindName[s.kind] || s.kind) +
        (s.unit ? " · " + esc(s.unit) : "") + "</span></span>" + st + "</div>" +
        '<div class="dsh-faint">Последняя отправка: ' + esc(ago(s.last_push_at)) + (s.count ? " · " + grouped(s.count) + " " + (s.kind === "table" ? plural(s.count, "строка", "строки", "строк") : s.kind === "events" ? plural(s.count, "событие", "события", "событий") : plural(s.count, "точка", "точки", "точек")) : "") +
        (s.expected_every_min ? " · ждём каждые " + s.expected_every_min + " мин" : "") + " · ключ " + esc(s.key_hint) + "</div>" +
        (s.last_error ? '<div class="dsh-w-err">' + esc(s.last_error) + "</div>" : "") +
        '<div class="dsh-src-actions"><button type="button" class="dsh-btn" data-srckey="' + esc(s.key) + '">Как подключить</button>' +
        '<button type="button" class="dsh-btn" data-rotate="' + esc(s.key) + '">Сменить ключ</button>' +
        (can.own ? '<button type="button" class="dsh-btn dsh-btn--danger" data-srcdel="' + esc(s.key) + '">Удалить</button>' : "") + "</div></div>";
    }).join("") : '<p class="dsh-muted">Источников пока нет. Их создаёт агент через MCP (create_dataset) — он же получает ключ и ставит скрипт на сервер.</p>';
    sheet("Источники данных", html, function (body, close) {
      body.addEventListener("click", async function (e) {
        var t = e.target.closest && e.target.closest("[data-srckey],[data-rotate],[data-srcdel]");
        if (!t) return;
        var key = t.getAttribute("data-srckey") || t.getAttribute("data-rotate") || t.getAttribute("data-srcdel");
        var ds = dss.filter(function (x) { return x.key === key; })[0];
        if (t.hasAttribute("data-srckey")) { close(); showSourceHelp(ds, null); return; }
        if (t.hasAttribute("data-rotate")) {
          if (!(await app().confirm("Сменить ключ источника «" + ds.title + "»? Старый ключ перестанет работать сразу — его надо будет заменить на сервере."))) return;
          try {
            var res = await api("/dashboards/" + state.id + "/datasets/" + encodeURIComponent(key) + "/rotate", { method: "POST" });
            close();
            showSourceHelp(ds, res);
            loadDash(state.id, true);
          } catch (err) { toast((err && err.message) || "Не удалось сменить ключ"); }
          return;
        }
        if (!(await app().confirm("Удалить источник «" + ds.title + "» со всеми данными и его виджетами? Это нельзя отменить."))) return;
        try {
          await api("/dashboards/" + state.id + "/datasets/" + encodeURIComponent(key), { method: "DELETE" });
          close();
          toast("Источник удалён");
          loadDash(state.id, true);
        } catch (err) { toast((err && err.message) || "Не удалось удалить"); }
      });
    });
  }

  function showSourceHelp(ds, rotated) {
    var url = (rotated && rotated.ingest_url) || ds.ingest_url;
    var body = { metric: '{"points":[{"ts":"2026-11-13","value":123456}]}', table: '{"mode":"replace","rows":[{"client":"Ромашка","sum":840000}]}',
      events: '{"events":[{"ts":"2026-11-13T10:42:00+03:00","text":"Оплата 84 000 ₽","id":"pay-1"}]}' }[ds.kind];
    var curl = rotated ? rotated.examples.curl : "curl -sS -X POST " + url + ' -H "Authorization: Bearer $ORBITA_KEY" -H \'Content-Type: application/json\' -d \'' + body + "'";
    var html = (rotated
      ? '<p><b>Новый ключ</b> — показывается один раз. Замените им старый в env-файле на сервере:</p><code class="dsh-code">' + esc(rotated.ingest_key) + "</code>" + copyBtn(rotated.ingest_key, "Скопировать ключ")
      : '<p class="dsh-muted">Ключ источника виден только при создании (его получил агент) и при смене. Потеряли — «Сменить ключ».</p>') +
      '<p style="margin-top:1rem"><b>Адрес приёма</b></p><code class="dsh-code">' + esc(url) + "</code>" +
      "<p><b>Пример отправки</b> (ключ — в переменной ORBITA_KEY):</p><code class=\"dsh-code\">" + esc(curl) + "</code>" + copyBtn(curl) +
      '<p class="dsh-muted" style="margin-top:1rem">Повторная отправка безопасна: точка за тот же день перезаписывается. Подробно — в инструкции для агента.</p>';
    sheet(ds.title, html, function (b) { bindCopy(b); });
  }

  function openAccess() {
    var d = state.dash;
    var roles = { viewer: "Смотрит", editor: "Редактирует", owner: "Владелец" };
    function membersHtml() {
      return (d.members || []).map(function (m) {
        if (m.creator) return '<div class="dsh-member"><span>' + esc(m.label) + '</span><span class="dsh-faint">Создатель</span></div>';
        return '<div class="dsh-member"><span>' + esc(m.label) + '</span><span style="display:flex;gap:6px">' +
          '<select data-mrole="' + esc(m.principal) + '"><option value="viewer"' + (m.role === "viewer" ? " selected" : "") + ">Смотрит</option>" +
          '<option value="editor"' + (m.role === "editor" ? " selected" : "") + ">Редактирует</option></select>" +
          '<button type="button" class="dsh-btn dsh-btn--icon" data-mdel="' + esc(m.principal) + '" aria-label="Убрать">✕</button></span></div>';
      }).join("");
    }
    var have = {};
    (d.members || []).forEach(function (m) { have[m.principal] = 1; });
    var cand = (d.candidates || []).filter(function (c) { return !have[c.principal]; });
    var html = '<p class="dsh-muted">Владелец и админы организации видят все её дашборды. Остальным — доступ здесь.</p><div data-members>' + membersHtml() + "</div>" +
      (cand.length ? '<div class="dsh-field dsh-field--row" style="margin-top:0.85rem"><select data-addp style="flex:1">' + cand.map(function (c) {
        return '<option value="' + esc(c.principal) + '">' + esc(c.label) + "</option>";
      }).join("") + '</select><select data-addr><option value="viewer">Смотрит</option><option value="editor">Редактирует</option></select>' +
        '<button type="button" class="dsh-btn dsh-btn--primary" data-add="1">Добавить</button></div>' : "");
    sheet("Доступ к дашборду", html, function (body, close) {
      body.addEventListener("change", async function (e) {
        var s = e.target.closest && e.target.closest("[data-mrole]");
        if (!s) return;
        try { await api("/dashboards/" + state.id + "/members", { method: "PUT", body: JSON.stringify({ principal: s.getAttribute("data-mrole"), role: s.value }) }); toast("Сохранено"); }
        catch (err) { toast((err && err.message) || "Не удалось"); }
      });
      body.addEventListener("click", async function (e) {
        var del = e.target.closest && e.target.closest("[data-mdel]");
        var add = e.target.closest && e.target.closest("[data-add]");
        try {
          if (del) await api("/dashboards/" + state.id + "/members?principal=" + encodeURIComponent(del.getAttribute("data-mdel")), { method: "DELETE" });
          else if (add) await api("/dashboards/" + state.id + "/members", { method: "PUT", body: JSON.stringify({ principal: body.querySelector("[data-addp]").value, role: body.querySelector("[data-addr]").value }) });
          else return;
          close();
          await loadDash(state.id, true);
          openAccess();
        } catch (err) { toast((err && err.message) || "Не удалось"); }
      });
    });
  }

  // редактор виджета: вид из каталога, источник, заголовок, ширина, опции по схеме
  async function openWidgetEditor(w) {
    var catalog;
    try { catalog = await ensureCatalog(); } catch (e) { toast("Каталог не загрузился"); return; }
    var d = state.dash, dss = d.datasets || [];
    var cur = { type: w ? w.kind : "number", dataset: w ? (w.dataset || "") : "", title: w ? w.title : "", width: w ? w.width : 0,
      options: w ? JSON.parse(JSON.stringify(w.options || {})) : {} };
    function spec() { return catalog.filter(function (c) { return c.type === cur.type; })[0]; }
    function dsOptions() {
      var sp = spec();
      if (!sp.source) return "";
      var ok = dss.filter(function (s) { return sp.source.indexOf(s.kind) >= 0; });
      if (!ok.length) return '<p class="dsh-w-err">Нет подходящего источника (' + esc(sp.source.join(" или ")) + "). Его создаёт агент через MCP.</p>";
      if (!cur.dataset || !ok.some(function (s) { return s.key === cur.dataset; })) cur.dataset = ok[0].key;
      return '<label class="dsh-field">Источник<select data-f="dataset">' + ok.map(function (s) {
        return '<option value="' + esc(s.key) + '"' + (s.key === cur.dataset ? " selected" : "") + ">" + esc(s.title) + " (" + esc(s.key) + ")</option>";
      }).join("") + "</select></label>";
    }
    function colsOf() {
      var ds = dss.filter(function (s) { return s.key === cur.dataset; })[0];
      return ds && ds.schema && ds.schema.columns ? ds.schema.columns.map(function (c) { return c.name; }) : [];
    }
    function optField(name, o) {
      var v = cur.options[name];
      var label = name;
      if (o.one_of) return '<label class="dsh-field">' + esc(label) + '<select data-o="' + name + '">' + o.one_of.map(function (x) {
        return '<option value="' + esc(x) + '"' + ((v == null ? o.default : v) === x ? " selected" : "") + ">" + esc(x) + "</option>"; }).join("") + "</select></label>";
      if (o.bool) return '<label class="dsh-field dsh-field--row"><input type="checkbox" data-o="' + name + '"' + ((v == null ? o.default : v) ? " checked" : "") + "> " + esc(label) + "</label>";
      if (o.column) {
        var cols = colsOf();
        if (cols.length) return '<label class="dsh-field">' + esc(label) + (o.required ? " *" : "") + '<select data-o="' + name + '"><option value=""></option>' + cols.map(function (c) {
          return '<option value="' + esc(c) + '"' + (v === c ? " selected" : "") + ">" + esc(c) + "</option>"; }).join("") + "</select></label>";
        return '<label class="dsh-field">' + esc(label) + (o.required ? " *" : "") + '<input type="text" data-o="' + name + '" value="' + esc(v || "") + '" placeholder="имя колонки"></label>';
      }
      if (o.columns_max || o.series_max) return '<label class="dsh-field">' + esc(label) + ' (через запятую)<input type="text" data-o="' + name + '" data-list="1" value="' + esc((v || []).join(", ")) + '"></label>';
      if (o.list) return '<label class="dsh-field">' + esc(label) + ' (JSON)<textarea data-o="' + name + '" data-json="1">' + esc(v ? JSON.stringify(v, null, 1) : "") + "</textarea></label>";
      if (o.int || o.number) return '<label class="dsh-field">' + esc(label) + (o.required ? " *" : "") + '<input type="number" step="any" data-o="' + name + '" value="' + esc(v == null ? (o.default == null ? "" : o.default) : v) + '"></label>';
      if (o.text_max > 200) return '<label class="dsh-field">' + esc(label) + '<textarea data-o="' + name + '" style="font-family:inherit;font-size:0.875rem">' + esc(v || "") + "</textarea></label>";
      return '<label class="dsh-field">' + esc(label) + '<input type="text" data-o="' + name + '" maxlength="' + (o.text_max || 80) + '" value="' + esc(v || "") + '"></label>';
    }
    function bodyHtml() {
      var sp = spec();
      return '<div class="dsh-types">' + catalog.map(function (c) {
        return '<button type="button" class="dsh-type' + (c.type === cur.type ? " is-on" : "") + '" data-type="' + c.type + '"><b>' + esc(c.label) + "</b><span>" + esc(c.about) + "</span></button>";
      }).join("") + "</div>" + dsOptions() +
        '<label class="dsh-field">Заголовок<input type="text" data-f="title" maxlength="60" value="' + esc(cur.title) + '" placeholder="по источнику"></label>' +
        '<label class="dsh-field">Ширина (из 4)<select data-f="width">' + [1, 2, 3, 4].filter(function (n) { return n >= sp.min_width; }).map(function (n) {
          return '<option value="' + n + '"' + ((cur.width || sp.min_width) === n ? " selected" : "") + ">" + n + "</option>"; }).join("") + "</select></label>" +
        Object.keys(sp.options).map(function (k) { return optField(k, sp.options[k]); }).join("") +
        '<div class="dsh-head-actions" style="justify-content:flex-end"><button type="button" class="dsh-btn dsh-btn--primary" data-save="1">' + (w ? "Сохранить" : "Добавить") + "</button></div>";
    }
    sheet(w ? "Виджет" : "Новый виджет", "", function (body, close) {
      function paint() { body.innerHTML = bodyHtml(); }
      function collect() {
        var f = body.querySelector('[data-f="dataset"]');
        if (f) cur.dataset = f.value;
        cur.title = body.querySelector('[data-f="title"]').value;
        cur.width = Number(body.querySelector('[data-f="width"]').value);
        var opts = {};
        body.querySelectorAll("[data-o]").forEach(function (el) {
          var k = el.getAttribute("data-o");
          if (el.type === "checkbox") opts[k] = el.checked;
          else if (el.hasAttribute("data-list")) { var l = el.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean); if (l.length) opts[k] = l; }
          else if (el.hasAttribute("data-json")) { if (el.value.trim()) opts[k] = JSON.parse(el.value); }
          else if (el.type === "number") { if (el.value !== "") opts[k] = Number(el.value); }
          else if (el.value !== "") opts[k] = el.value;
        });
        cur.options = opts;
      }
      paint();
      body.addEventListener("click", async function (e) {
        var t = e.target.closest && e.target.closest("[data-type]");
        if (t) { try { collect(); } catch (_) {} cur.type = t.getAttribute("data-type"); cur.options = {}; cur.width = 0; paint(); return; }
        if (!(e.target.closest && e.target.closest("[data-save]"))) return;
        try { collect(); } catch (err) { toast("В поле JSON ошибка"); return; }
        var sp = spec();
        var payload = { kind: cur.type, title: cur.title, options: cur.options, width: cur.width, dataset: sp.source ? cur.dataset : "" };
        try {
          if (w) await api("/dashboards/" + state.id + "/widgets/" + w.id, { method: "PATCH", body: JSON.stringify(Object.assign({ replace_options: true }, payload)) });
          else await api("/dashboards/" + state.id + "/widgets", { method: "POST", body: JSON.stringify(payload) });
          close();
          loadDash(state.id, true);
        } catch (err) { toast((err && err.message) || "Не удалось сохранить"); }
      });
      body.addEventListener("change", function (e) {
        if (e.target.getAttribute && e.target.getAttribute("data-f") === "dataset") { try { collect(); } catch (_) {} paint(); }
      });
    });
  }
  // ---------- действия ----------

  async function action(name) {
    var d = state.dash;
    if (name === "connect") return openConnect();
    if (name === "create") return openCreate();
    if (name === "back") return backToList();
    if (name === "sources") return openSources();
    if (name === "access") return openAccess();
    if (name === "menu") return openMenu();
    if (name === "add-widget") return openWidgetEditor(null);
    if (name === "edit") { state.editing = true; return renderDash(); }
    if (name === "edit-done") { state.editing = false; return renderDash(); }
    if (name === "rename") {
      var t = await app().promptDialog("Название дашборда", d.title);
      if (t != null && t.trim() && t.trim() !== d.title) patchDash({ title: t.trim() });
      return;
    }
    if (name === "describe") {
      var ds = await app().promptDialog("Описание (видно под названием)", d.description || "");
      if (ds != null) patchDash({ description: ds.trim() });
      return;
    }
    if (name === "archive") {
      if (!(await app().confirm("Убрать дашборд «" + d.title + "» в архив? Приём данных остановится."))) return;
      await patchDash({ archived: true });
      return backToList();
    }
    if (name === "delete") {
      if (!(await app().confirm("Удалить дашборд «" + d.title + "» со всеми источниками и данными? Это нельзя отменить."))) return;
      try {
        await api("/dashboards/" + state.id, { method: "DELETE" });
        toast("Дашборд удалён");
        backToList();
      } catch (e) { toast((e && e.message) || "Не удалось удалить"); }
    }
  }

  async function moveWidget(id, delta) {
    var ws = state.dash.widgets.map(function (w) { return w.id; });
    var i = ws.indexOf(id), j = i + delta;
    if (i < 0 || j < 0 || j >= ws.length) return;
    ws.splice(j, 0, ws.splice(i, 1)[0]);
    try {
      await api("/dashboards/" + state.id + "/widgets-order", { method: "PUT", body: JSON.stringify({ ids: ws }) });
      await loadDash(state.id, true);
    } catch (e) { toast((e && e.message) || "Не удалось"); }
  }
  async function widthWidget(id, delta) {
    var w = findWidget(id);
    if (!w) return;
    try {
      await api("/dashboards/" + state.id + "/widgets/" + id, { method: "PATCH", body: JSON.stringify({ width: Math.max(1, Math.min(4, (w.width || 1) + delta)) }) });
      await loadDash(state.id, true);
    } catch (e) { toast((e && e.message) || "Не удалось"); }
  }

  function onClick(e) {
    var t = e.target.closest ? e.target : null;
    if (!t) return;
    var a = t.closest("[data-act]");
    if (a) return action(a.getAttribute("data-act"));
    var o = t.closest("[data-open]");
    if (o) return openDash(o.getAttribute("data-open"));
    var p = t.closest("[data-period]");
    if (p) {
      state.period = p.getAttribute("data-period");
      try { localStorage.setItem("dsh.period", state.period); } catch (_) {}
      return loadDash(state.id, true);
    }
    var m = t.closest("[data-wmove]");
    if (m) { var mv = m.getAttribute("data-wmove").split(":"); return moveWidget(Number(mv[0]), Number(mv[1])); }
    var ww = t.closest("[data-wwidth]");
    if (ww) { var wd = ww.getAttribute("data-wwidth").split(":"); return widthWidget(Number(wd[0]), Number(wd[1])); }
    var we = t.closest("[data-wedit]");
    if (we) return openWidgetEditor(findWidget(Number(we.getAttribute("data-wedit"))));
    var wx = t.closest("[data-wdel]");
    if (wx) {
      var wid = Number(wx.getAttribute("data-wdel"));
      app().confirm("Убрать виджет? Данные источника останутся.").then(function (ok) {
        if (!ok) return;
        api("/dashboards/" + state.id + "/widgets/" + wid, { method: "DELETE" }).then(function () { loadDash(state.id, true); }, function (err) { toast((err && err.message) || "Не удалось"); });
      });
      return;
    }
    var ts = t.closest("[data-tsort]");
    if (ts) {
      var parts = ts.getAttribute("data-tsort").split(":"), ui = tableUi[parts[0]];
      if (ui) { ui.dir = ui.sort === parts[1] && ui.dir === "desc" ? "asc" : "desc"; ui.sort = parts[1]; rerenderWidget(Number(parts[0])); }
      return;
    }
    var ta = t.closest("[data-tall]");
    if (ta) { var u2 = tableUi[ta.getAttribute("data-tall")]; if (u2) { u2.all = true; rerenderWidget(Number(ta.getAttribute("data-tall"))); } return; }
    var tc = t.closest("[data-tcsv]");
    if (tc) { var w3 = findWidget(Number(tc.getAttribute("data-tcsv"))); if (w3) tableCsv(w3); }
  }
  function rerenderWidget(id) {
    var el = root() && root().querySelector('.dsh-w[data-wid="' + id + '"]');
    var w = findWidget(id);
    if (!el || !w) return;
    var focused = document.activeElement && el.contains(document.activeElement) ? document.activeElement.getAttribute("data-tq") : null;
    var title = el.querySelector(".dsh-w-title");
    el.innerHTML = "";
    el.appendChild(title);
    el.insertAdjacentHTML("beforeend", widgetBody(w));
    drawCharts(el.parentNode);
    if (focused) {
      var inp = el.querySelector('[data-tq="' + focused + '"]');
      if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
    }
  }
  function onInput(e) {
    var q = e.target.getAttribute && e.target.getAttribute("data-tq");
    if (q && tableUi[q]) { tableUi[q].q = e.target.value; tableUi[q].all = false; rerenderWidget(Number(q)); }
  }
  function onChange(e) {
    var s = e.target.getAttribute && e.target.getAttribute("data-tst");
    if (s && tableUi[s]) { tableUi[s].st = e.target.value; rerenderWidget(Number(s)); }
  }

  // подсказка со значением: наведение мышью или касание
  var tipEl = null;
  function showTip(target, x, y) {
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "dsh-tip"; document.body.appendChild(tipEl); }
    tipEl.textContent = target.getAttribute("data-tip");
    tipEl.style.display = "block";
    var r = tipEl.getBoundingClientRect();
    var left = Math.min(window.innerWidth - r.width - 8, Math.max(8, x - r.width / 2));
    tipEl.style.left = left + "px";
    tipEl.style.top = Math.max(8, y - r.height - 12) + "px";
  }
  function hideTip() { if (tipEl) tipEl.style.display = "none"; }
  function onPointer(e) {
    var t = e.target.closest && e.target.closest("[data-tip]");
    if (t && root() && root().contains(t)) showTip(t, e.clientX, e.clientY);
    else hideTip();
  }

  // ---------- вход ----------

  var bound = false;
  function bind() {
    if (bound) return;
    var r = root();
    if (!r) return;
    bound = true;
    r.addEventListener("click", onClick);
    r.addEventListener("input", onInput);
    r.addEventListener("change", onChange);
    r.addEventListener("pointermove", onPointer);
    r.addEventListener("pointerdown", onPointer);
    r.addEventListener("pointerleave", hideTip);
    var t = null;
    window.addEventListener("resize", function () {
      clearTimeout(t);
      t = setTimeout(function () { if (state.view === "dash" && isVisible()) drawCharts(); }, 150);
    });
    try { var p = localStorage.getItem("dsh.period"); if (p && /^(7d|30d|90d|365d)$/.test(p)) state.period = p; } catch (_) {}
  }
  function isVisible() {
    var p = document.getElementById("panel-dashboards");
    return !!(p && !p.hidden);
  }

  function show() {
    bind();
    if (state.view === "dash" && state.id) return loadDash(state.id, true);
    var want = 0;
    try { want = Number(new URL(location.href).searchParams.get("dashboard") || 0); } catch (_) {}
    if (want) return openDash(want);
    renderList();
    return loadList(state.list != null);
  }

  function changed(id) {
    if (!isVisible()) return;
    if (state.view === "dash" && Number(id) === state.id) loadDash(state.id, true);
    else if (state.view === "list") loadList(true);
  }

  // глубокая ссылка ?dashboard=N — открыть вкладку после загрузки приложения
  function boot() {
    var want = 0;
    try { want = Number(new URL(location.href).searchParams.get("dashboard") || 0); } catch (_) {}
    if (want && app().setTab) app().setTab("dashboards");
  }
  if (document.readyState === "complete") setTimeout(boot, 300);
  else window.addEventListener("load", function () { setTimeout(boot, 300); });

  window.LeoDashboards = { show: show, refresh: function () { return state.view === "dash" ? loadDash(state.id, true) : loadList(true); }, changed: changed, open: openDash };
})();
