/* Холст — лист заметки kind=board: доска в духе Miro.
 * Блоки текста, прямоугольники, ромбы, эллипсы, стикеры, линии и стрелки (прилипают к блокам
 * и едут за ними), карандаш, картинки; зум, панорама, выделение рамкой, группы, сетка и
 * направляющие, отмена, буфер обмена, мини-карта, экспорт PNG/SVG.
 *
 * Документ — JSON {v, elements: {id: {...}}} (assistant/lib/board_doc.py). Элементы рисуются
 * HTML-узлами в «мире» с CSS-трансформом (текст правится на месте, порядок слоёв — порядок узлов),
 * фигуры и линии внутри узлов — SVG. Геометрия — webapp/board-geom.js.
 *
 * LeoBoard.create({host, json, sheetId, readOnly, onChange(json), onNotify(text)}) →
 *   {getJson, setJson, setJsonKeepView, fitAll, exportPng, exportSvg, attachYjs, detachYjs,
 *    isYjsAttached, setRemoteCursors, destroy}
 */
(function () {
  "use strict";

  var G = window.LeoBoardGeom;
  var INK = ["ink", "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink"];
  var FILLS = ["none", "white", "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink"];
  var STICKY = ["yellow", "orange", "pink", "green", "blue", "purple", "gray"];
  var COLOR_NAMES = {
    none: "Без заливки", white: "Белый", ink: "Чёрный", gray: "Серый", red: "Красный", orange: "Оранжевый",
    yellow: "Жёлтый", green: "Зелёный", blue: "Синий", purple: "Фиолетовый", pink: "Розовый",
  };
  var FONT_SIZES = [{ v: 12, t: "S" }, { v: 16, t: "M" }, { v: 24, t: "L" }, { v: 36, t: "XL" }];
  var WIDTHS = [1, 2, 4, 6];
  var MIN_Z = 0.1;
  var MAX_Z = 4;
  var GRID = 20;
  var SHAPES = { rect: 1, diamond: 1, ellipse: 1 };
  var BOXED = { text: 1, rect: 1, diamond: 1, ellipse: 1, sticky: 1, image: 1 };
  var ALIGN_ICON = { left: "alignLeft", center: "alignCenter", right: "alignRight" };

  // Действия с выделенными блоками — те же иконки и подписи, что у плашки выделения в заметке
  // (webapp/note-comments.js showBubble), чтобы везде было одинаково.
  var NOTE_ACTS = [
    {
      id: "ai", label: "ИИ", cls: " lb-nact--ai",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
        '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>',
    },
    {
      id: "discuss", label: "Обсудить", cls: "",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
    },
    {
      id: "task", label: "Задача", cls: "",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
    },
    {
      id: "link", label: "Ссылка", cls: "",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
        '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/></svg>',
    },
  ];

  /** Выравнивание текста: у текста по умолчанию влево, в фигурах и стикерах — по центру. */
  function alignOf(el) {
    var a = el.style && el.style.align;
    if (a === "left" || a === "center" || a === "right") return a;
    return el.type === "text" ? "left" : "center";
  }

  function isAlignable(el) {
    return el.type === "text" || el.type === "rect" || el.type === "diamond" || el.type === "ellipse" || el.type === "sticky";
  }
  // Палец вместо мыши — ручки крупнее.
  var COARSE = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(pointer: coarse)").matches : false;
  var TEXTUAL = { text: 1, rect: 1, diamond: 1, ellipse: 1, sticky: 1, line: 1 };
  var STROKED = { rect: 1, diamond: 1, ellipse: 1, line: 1, pen: 1 };
  var IMAGE_MAX_SIDE = 1400;
  var CLIP_MIME = "application/x-leo-board";

  var DEFAULTS = {
    text: { w: 240, h: 26, style: { fontSize: 16, textColor: "ink", align: "left" } },
    rect: { w: 180, h: 100, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    diamond: { w: 170, h: 120, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    ellipse: { w: 170, h: 110, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    sticky: { w: 200, h: 200, style: { fill: "yellow", fontSize: 16 } },
    line: { style: { stroke: "ink", strokeWidth: 2 } },
    pen: { style: { stroke: "ink", strokeWidth: 3 } },
  };

  var TOOLS = [
    { id: "select", key: "v", title: "Выделение (V)", icon: "cursor" },
    { id: "hand", key: "h", title: "Рука (H, пробел)", icon: "hand" },
    { id: "text", key: "t", title: "Текст (T)", icon: "text" },
    { id: "sticky", key: "s", title: "Стикер (S)", icon: "sticky" },
    { id: "rect", key: "r", title: "Прямоугольник (R)", icon: "rect" },
    { id: "diamond", key: "d", title: "Ромб (D)", icon: "diamond" },
    { id: "ellipse", key: "o", title: "Эллипс (O)", icon: "ellipse" },
    { id: "line", key: "l", title: "Линия (L)", icon: "line" },
    { id: "arrow", key: "a", title: "Стрелка (A)", icon: "arrow" },
    { id: "pen", key: "p", title: "Карандаш (P)", icon: "pen" },
    { id: "image", key: "", title: "Картинка", icon: "image" },
  ];

  var ICONS = {
    cursor: '<path d="M5 3l12 7-5.2 1.3L9.5 17z"/>',
    hand: '<path d="M7 11V5.5a1.5 1.5 0 013 0V10m0-.5v-5a1.5 1.5 0 013 0V10m0-3.5a1.5 1.5 0 013 0V12c0 3.3-2.2 6-5.5 6H10c-2 0-3.2-1-4.3-2.6L3.5 12a1.5 1.5 0 012.4-1.7L7 11.5"/>',
    text: '<path d="M4 5h12M10 5v11M7.5 16h5"/>',
    sticky: '<path d="M4 4h12v8l-4 4H4z"/><path d="M12 16v-4h4"/>',
    rect: '<rect x="3" y="5" width="14" height="10" rx="1.5"/>',
    diamond: '<path d="M10 2.5L17.5 10 10 17.5 2.5 10z"/>',
    ellipse: '<ellipse cx="10" cy="10" rx="7.5" ry="5.5"/>',
    line: '<path d="M4 16L16 4"/>',
    arrow: '<path d="M4 16L16 4M9 4h7v7"/>',
    pen: '<path d="M13.5 3.5l3 3L7 16H4v-3z"/>',
    image: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><circle cx="7.5" cy="8.5" r="1.5"/><path d="M17 13l-4-4-7 7"/>',
    undo: '<path d="M7 5L3.5 8.5 7 12"/><path d="M3.5 8.5H12a4.5 4.5 0 010 9H9"/>',
    redo: '<path d="M13 5l3.5 3.5L13 12"/><path d="M16.5 8.5H8a4.5 4.5 0 000 9h3"/>',
    plus: '<path d="M10 4v12M4 10h12"/>',
    minus: '<path d="M4 10h12"/>',
    fit: '<path d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4"/>',
    grid: '<path d="M3 7.5h14M3 12.5h14M7.5 3v14M12.5 3v14"/>',
    map: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><rect x="9" y="9" width="5" height="4"/>',
    download: '<path d="M10 3v10M6 9l4 4 4-4M4 16h12"/>',
    trash: '<path d="M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10"/>',
    copy: '<rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V4H4v9h3"/>',
    front: '<rect x="7" y="7" width="9" height="9" rx="1"/><path d="M4 13V4h9"/>',
    back: '<rect x="4" y="4" width="9" height="9" rx="1"/><path d="M16 7v9H7"/>',
    group: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/><path d="M2 2h16v16H2z" stroke-dasharray="2 2"/>',
    ungroup: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>',
    arrowStart: '<path d="M17 10H4M8 6l-4 4 4 4"/>',
    arrowEnd: '<path d="M3 10h13M12 6l4 4-4 4"/>',
    straight: '<path d="M3 16L17 4"/>',
    curve: '<path d="M3 16C3 6 17 14 17 4"/>',
    elbow: '<path d="M3 16h7V4h7"/>',
    unbend: '<path d="M3 15h4l3-7 3 7h4" stroke-dasharray="2 2.2"/><path d="M3 15h14"/>',
    alignLeft: '<path d="M3 5h14M3 10h9M3 15h12"/>',
    alignCenter: '<path d="M3 5h14M5.5 10h9M4 15h12"/>',
    alignRight: '<path d="M3 5h14M8 10h9M5 15h12"/>',
    more: '<circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/>',
  };

  function icon(name) {
    return (
      '<svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || "") + "</svg>"
    );
  }

  function uid() {
    return "e" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function clone(o) {
    return o == null ? null : JSON.parse(JSON.stringify(o));
  }

  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function r2(v) {
    return Math.round((Number(v) || 0) * 100) / 100;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function clamp(v, a, b) {
    return Math.max(a, Math.min(b, v));
  }

  function inkVar(key) {
    return "var(--lb-ink-" + (INK.indexOf(key) >= 0 || key === "white" ? key : "ink") + ")";
  }

  function fillVar(key) {
    if (!key || key === "none") return "none";
    return "var(--lb-fill-" + (FILLS.indexOf(key) >= 0 ? key : "white") + ")";
  }

  function stickyVar(key) {
    return "var(--lb-sticky-" + (STICKY.indexOf(key) >= 0 ? key : "yellow") + ")";
  }

  function dashArray(dash, sw) {
    if (dash === "dashed") return sw * 4 + " " + sw * 3;
    if (dash === "dotted") return "0.1 " + sw * 2.5;
    return "";
  }

  /** Элемент в порядке полей, как его пишет сервер (board_doc._clean_element). */
  function serializeEl(el, getEl, pathOpts) {
    var o = { id: el.id, type: el.type, x: r2(el.x), y: r2(el.y), w: r2(el.w), h: r2(el.h), z: r2(el.z) };
    if (el.rot) o.rot = r2(el.rot);
    if (el.group) o.group = el.group;
    if (el.text) o.text = String(el.text);
    if (el.style) {
      var st = {};
      ["fill", "stroke", "strokeWidth", "dash", "fontSize", "align", "textColor", "bold"].forEach(function (k) {
        if (el.style[k] != null) st[k] = el.style[k];
      });
      if (Object.keys(st).length) o.style = st;
    }
    if (el.type === "line") {
      var res = G.resolveLine(el, getEl);
      o.points = [[r2(res.p0.x), r2(res.p0.y)], [r2(res.p1.x), r2(res.p1.y)]];
      if (el.start) o.start = el.start;
      if (el.end) o.end = el.end;
      if (el.arrowStart) o.arrowStart = true;
      if (el.arrowEnd) o.arrowEnd = true;
      o.route = el.route || "straight";
      if (el.bend) o.bend = r2(el.bend);
      if (el.labelT != null && isFinite(el.labelT)) o.labelT = Math.round(el.labelT * 1000) / 1000;
      if (el.waypoints && el.waypoints.length) {
        o.waypoints = el.waypoints.map(function (q) {
          return [r2(q[0]), r2(q[1])];
        });
      }
      o.d = G.linePath(el, res, pathOpts ? pathOpts(el) : null).d;
    } else if (el.type === "pen") {
      o.points = (el.points || []).map(function (p) {
        return [r2(p[0]), r2(p[1])];
      });
    } else if (el.type === "image") {
      o.src = el.src;
    }
    if (el.type === "sticky" && el.author && (el.author.id || el.author.name)) o.author = el.author;
    return o;
  }

  function parseBoard(json) {
    var data = null;
    if (json && typeof json === "object") data = json;
    else {
      try {
        data = JSON.parse(String(json || "").trim() || "{}");
      } catch (_) {
        data = {};
      }
    }
    var raw = (data && data.elements) || {};
    var out = [];
    var list = Array.isArray(raw) ? raw : Object.keys(raw).map(function (k) {
      var e = raw[k];
      if (e && !e.id) e.id = k;
      return e;
    });
    list.forEach(function (e) {
      if (!e || !e.id || !e.type) return;
      var el = clone(e);
      delete el.d;
      el.x = Number(el.x) || 0;
      el.y = Number(el.y) || 0;
      el.w = Number(el.w) || 0;
      el.h = Number(el.h) || 0;
      el.z = Number(el.z) || 0;
      out.push(el);
    });
    return out;
  }

  function readFileAsDataUrl(file) {
    return new Promise(function (resolve) {
      var reader = new FileReader();
      reader.onload = function () {
        resolve(String(reader.result || ""));
      };
      reader.onerror = function () {
        resolve("");
      };
      reader.readAsDataURL(file);
    });
  }

  function loadImage(src) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = function () {
        resolve(img);
      };
      img.onerror = function () {
        resolve(null);
      };
      img.src = src;
    });
  }

  /** Картинка в документе — data URL: ужимаем длинную сторону и в JPEG (PNG с прозрачностью — PNG). */
  function compressImage(src) {
    return loadImage(src).then(function (img) {
      if (!img) return null;
      var w = img.naturalWidth || img.width;
      var h = img.naturalHeight || img.height;
      if (!w || !h) return null;
      var k = Math.min(1, IMAGE_MAX_SIDE / Math.max(w, h));
      var cw = Math.max(1, Math.round(w * k));
      var ch = Math.max(1, Math.round(h * k));
      var out = src;
      try {
        var canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        var ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, cw, ch);
        var alpha = false;
        if (/^data:image\/(png|gif|webp)/i.test(src)) {
          var px = ctx.getImageData(0, 0, cw, ch).data;
          for (var i = 3; i < px.length; i += 16) {
            if (px[i] < 250) {
              alpha = true;
              break;
            }
          }
        }
        var enc = alpha ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.84);
        if (enc && (enc.length < src.length || !/^data:image\/(png|jpe?g|webp|gif)/i.test(src))) out = enc;
      } catch (_) {}
      return { src: out, w: cw, h: ch };
    });
  }

  function downloadBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 4000);
  }

  function create(opts) {
    var o = opts || {};
    var host = o.host;
    var readOnly = !!o.readOnly;
    var sheetId = String(o.sheetId || "");
    var viewKey = sheetId ? "leo-board-view:" + sheetId : "";

    // ---- состояние ----
    var els = new Map();
    var nodes = new Map();
    var selected = new Set();
    var undoStack = [];
    var redoStack = [];
    var tx = null;
    var dirty = new Set();
    var orderDirty = true;
    var frame = 0;
    var view = { x: 0, y: 0, z: 1 };
    var tool = "select";
    var gesture = null;
    var pointers = new Map();
    var editing = null; // {id, node}
    var spaceDown = false;
    var showGrid = true;
    var showMap = (host.getBoundingClientRect().width || window.innerWidth) > 640;
    var guides = [];
    var snapTarget = null; // {el, side}
    var marquee = null;
    var lastTap = { id: "", t: 0, x: 0, y: 0 };
    var lastWpTap = { h: "", id: "", t: 0 };
    var lastStyle = {};
    var changeTimer = 0;
    var destroyed = false;
    var mapTimer = 0;
    var remoteCursors = [];
    var yjs = null; // {doc, map, observer}
    var autoFit = false;
    var pendingY = new Set();
    var yFlushHandle = 0;

    function getEl(id) {
      return els.get(id) || null;
    }

    function sortedEls() {
      return Array.from(els.values()).sort(function (a, b) {
        return (a.z || 0) - (b.z || 0) || (a.id < b.id ? -1 : 1);
      });
    }

    function topZ() {
      var m = 0;
      els.forEach(function (e) {
        if ((e.z || 0) > m) m = e.z || 0;
      });
      return Math.floor(m) + 1;
    }

    function bottomZ() {
      var m = Infinity;
      els.forEach(function (e) {
        if ((e.z || 0) < m) m = e.z || 0;
      });
      return (m === Infinity ? 0 : Math.floor(m)) - 1;
    }

    // ---- DOM ----
    var root = document.createElement("div");
    root.className = "leo-board" + (readOnly ? " is-readonly" : "");
    root.innerHTML =
      '<div class="lb-stage" tabindex="0" aria-label="Доска">' +
      '<div class="lb-world"></div>' +
      '<svg class="lb-overlay" xmlns="http://www.w3.org/2000/svg"></svg>' +
      "</div>" +
      (readOnly ? "" : '<div class="lb-tools" role="toolbar" aria-label="Инструменты"></div>') +
      (readOnly ? "" : '<div class="lb-history"></div>') +
      '<div class="lb-props hidden"></div>' +
      '<div class="lb-pop hidden"></div>' +
      '<canvas class="lb-minimap" width="180" height="120"></canvas>' +
      '<div class="lb-zoom"></div>' +
      '<div class="lb-empty-hint hidden">' +
      (readOnly ? "Доска пуста" : "Выберите инструмент или дважды нажмите на доску, чтобы добавить текст") +
      "</div>";
    host.appendChild(root);
    var stage = root.querySelector(".lb-stage");
    var world = root.querySelector(".lb-world");
    var overlay = root.querySelector(".lb-overlay");
    var toolsBar = root.querySelector(".lb-tools");
    var historyBar = root.querySelector(".lb-history");
    var propsBar = root.querySelector(".lb-props");
    var pop = root.querySelector(".lb-pop");
    var minimap = root.querySelector(".lb-minimap");
    var zoomBar = root.querySelector(".lb-zoom");
    var emptyHint = root.querySelector(".lb-empty-hint");

    function stageRect() {
      return stage.getBoundingClientRect();
    }

    function toWorld(cx, cy, rect) {
      var r = rect || stageRect();
      return G.pt((cx - r.left - view.x) / view.z, (cy - r.top - view.y) / view.z);
    }

    function toScreen(p) {
      return G.pt(p.x * view.z + view.x, p.y * view.z + view.y);
    }

    // ---- изменения и отмена ----
    function begin() {
      if (!tx) tx = { before: new Map(), depth: 0 };
      tx.depth++;
    }

    function touch(id) {
      if (tx && !tx.before.has(id)) tx.before.set(id, clone(els.get(id) || null));
    }

    function commit() {
      if (!tx) return;
      tx.depth--;
      if (tx.depth > 0) return;
      var entry = [];
      tx.before.forEach(function (before, id) {
        var after = clone(els.get(id) || null);
        if (!same(before, after)) entry.push({ id: id, before: before, after: after });
      });
      tx = null;
      if (entry.length) {
        undoStack.push(entry);
        if (undoStack.length > 200) undoStack.shift();
        redoStack = [];
      }
      renderHistoryBar();
    }

    function markDirty(id) {
      dirty.add(id);
      schedule();
    }

    function localChanged(id) {
      if (yjs) {
        pendingY.add(id);
        scheduleYFlush();
      }
      if (changeTimer) clearTimeout(changeTimer);
      changeTimer = setTimeout(emitChange, 250);
    }

    function emitChange() {
      changeTimer = 0;
      if (destroyed || typeof o.onChange !== "function") return;
      o.onChange(getJson());
    }

    function put(el) {
      if (readOnly) return;
      touch(el.id);
      var prev = els.get(el.id);
      if (prev && (prev.z || 0) !== (el.z || 0)) orderDirty = true;
      if (!prev) orderDirty = true;
      els.set(el.id, el);
      markDirty(el.id);
      localChanged(el.id);
    }

    function patch(id, fields) {
      var el = els.get(id);
      if (!el) return null;
      var next = Object.assign({}, el, fields);
      if (fields.style) next.style = Object.assign({}, el.style || {}, fields.style);
      put(next);
      return next;
    }

    function remove(id) {
      if (!els.has(id) || readOnly) return;
      touch(id);
      els.delete(id);
      selected.delete(id);
      orderDirty = true;
      markDirty(id);
      localChanged(id);
    }

    function applyEntry(entry, useAfter) {
      entry.forEach(function (ch) {
        var val = clone(useAfter ? ch.after : ch.before);
        if (val) els.set(ch.id, val);
        else {
          els.delete(ch.id);
          selected.delete(ch.id);
        }
        orderDirty = true;
        markDirty(ch.id);
        localChanged(ch.id);
      });
    }

    function undo() {
      finishEdit();
      var entry = undoStack.pop();
      if (!entry) return;
      applyEntry(entry, false);
      redoStack.push(entry);
      renderHistoryBar();
    }

    function redo() {
      finishEdit();
      var entry = redoStack.pop();
      if (!entry) return;
      applyEntry(entry, true);
      undoStack.push(entry);
      renderHistoryBar();
    }

    // ---- рендер ----
    function schedule() {
      if (frame || destroyed) return;
      frame = requestAnimationFrame(flush);
    }

    function flush() {
      frame = 0;
      if (destroyed) return;
      // Линии, прилипшие к изменённым блокам, тоже перерисовываем.
      if (dirty.size) {
        var extra = [];
        // Сдвинули или удалили блок — ломаные с обходом могут проложиться иначе.
        var blockDirty = false;
        dirty.forEach(function (id) {
          var d = els.get(id);
          if (!d || BOXED[d.type]) blockDirty = true;
        });
        els.forEach(function (el) {
          if (el.type !== "line") return;
          var a = el.start && el.start.el;
          var b = el.end && el.end.el;
          if ((a && dirty.has(a)) || (b && dirty.has(b)) || (blockDirty && autoRouted(el))) extra.push(el.id);
        });
        extra.forEach(function (id) {
          dirty.add(id);
        });
      }
      dirty.forEach(renderNode);
      dirty.clear();
      if (orderDirty) {
        orderDirty = false;
        sortedEls().forEach(function (el) {
          var n = nodes.get(el.id);
          if (n) world.appendChild(n);
        });
      }
      emptyHint.classList.toggle("hidden", els.size > 0 || !!gesture);
      renderOverlay();
      renderProps();
      scheduleMinimap();
    }

    function applyView() {
      world.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.z + ")";
      var g = GRID * view.z;
      if (showGrid && g >= 6) {
        stage.style.backgroundSize = g + "px " + g + "px";
        stage.style.backgroundPosition = (view.x % g) + "px " + (view.y % g) + "px";
        stage.classList.add("has-grid");
      } else {
        stage.classList.remove("has-grid");
      }
      stage.style.setProperty("--lb-zoom", String(view.z));
      renderZoomBar();
      renderOverlay();
      renderProps();
      scheduleMinimap();
      saveView();
    }

    var saveViewTimer = 0;
    function saveView() {
      if (!viewKey || readOnly) return;
      if (saveViewTimer) clearTimeout(saveViewTimer);
      saveViewTimer = setTimeout(function () {
        try {
          localStorage.setItem(viewKey, JSON.stringify(view));
        } catch (_) {}
      }, 400);
    }

    function loadView() {
      if (!viewKey || readOnly) return false;
      try {
        var v = JSON.parse(localStorage.getItem(viewKey) || "null");
        if (v && isFinite(v.x) && isFinite(v.y) && isFinite(v.z) && v.z > 0) {
          view = { x: v.x, y: v.y, z: clamp(v.z, MIN_Z, MAX_Z) };
          return true;
        }
      } catch (_) {}
      return false;
    }

    function textStyle(el, div) {
      var st = el.style || {};
      div.style.fontSize = (Number(st.fontSize) || 16) + "px";
      div.style.fontWeight = st.bold ? "600" : "";
      var color = st.textColor ? inkVar(st.textColor) : "";
      div.style.color = color;
      div.style.textAlign = alignOf(el);
    }

    function shapeSvg(el) {
      var st = el.style || {};
      var sw = Number(st.strokeWidth) || 2;
      var w = Math.max(1, el.w);
      var h = Math.max(1, el.h);
      var stroke = inkVar(st.stroke || "ink");
      var fill = fillVar(st.fill);
      var dash = dashArray(st.dash, sw);
      var common =
        ' fill="' + fill + '" stroke="' + stroke + '" stroke-width="' + sw + '"' +
        (dash ? ' stroke-dasharray="' + dash + '" stroke-linecap="round"' : "") +
        ' pointer-events="all" class="lb-hit"';
      var inner = "";
      if (el.type === "rect") {
        inner = '<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="' + Math.min(10, w / 4, h / 4) + '"' + common + "/>";
      } else if (el.type === "ellipse") {
        inner = '<ellipse cx="' + w / 2 + '" cy="' + h / 2 + '" rx="' + w / 2 + '" ry="' + h / 2 + '"' + common + "/>";
      } else if (el.type === "diamond") {
        inner = '<polygon points="' + w / 2 + ",0 " + w + "," + h / 2 + " " + w / 2 + "," + h + " 0," + h / 2 + '"' + common + ' stroke-linejoin="round"/>';
      } else if (el.type === "sticky") {
        inner = '<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="3" fill="' + stickyVar(st.fill) + '" pointer-events="all" class="lb-hit lb-sticky-bg"/>';
      } else if (el.type === "image") {
        inner = '<image x="0" y="0" width="' + w + '" height="' + h + '" preserveAspectRatio="none" href="' + esc(el.src || "") + '" pointer-events="all" class="lb-hit"/>';
      } else {
        inner = '<rect x="0" y="0" width="' + w + '" height="' + h + '" fill="transparent" pointer-events="all" class="lb-hit"/>';
      }
      return '<svg class="lb-shape" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" overflow="visible">' + inner + "</svg>";
    }

    /** Ломаная без ручных изломов обходит блоки — им нужны рамки препятствий. */
    function autoRouted(ln) {
      return ln.type === "line" && ln.route === "elbow" && !ln.bend && !(ln.waypoints && ln.waypoints.length);
    }

    function pathOptsOf(ln) {
      if (!autoRouted(ln)) return null;
      var own = {};
      if (ln.start && ln.start.el) own[ln.start.el] = 1;
      if (ln.end && ln.end.el) own[ln.end.el] = 1;
      var obstacles = [];
      els.forEach(function (el) {
        if (!BOXED[el.type]) return;
        var b = G.elBounds(el, getEl);
        if (b) obstacles.push({ x: b.x, y: b.y, w: b.w, h: b.h, own: !!own[el.id] });
      });
      return { obstacles: obstacles };
    }

    function linePathOf(ln, res) {
      return G.linePath(ln, res || G.resolveLine(ln, getEl), pathOptsOf(ln));
    }

    function lineSvg(el) {
      var st = el.style || {};
      var sw = Number(st.strokeWidth) || 2;
      var stroke = inkVar(st.stroke || "ink");
      var res = G.resolveLine(el, getEl);
      var path = linePathOf(el, res);
      var dash = dashArray(st.dash, sw);
      var size = 8 + sw * 2.5;
      var heads = "";
      if (el.arrowEnd) heads += headPoly(G.arrowHead(res.p1, path.endDir, size), stroke);
      if (el.arrowStart) heads += headPoly(G.arrowHead(res.p0, path.startDir, size), stroke);
      return {
        svg:
          '<svg class="lb-shape" width="1" height="1" overflow="visible">' +
          '<path d="' + path.d + '" fill="none" stroke="transparent" stroke-width="' + Math.max(14, sw + 12) + '" pointer-events="stroke" class="lb-hit" stroke-linecap="round" stroke-linejoin="round"/>' +
          '<path d="' + path.d + '" fill="none" stroke="' + stroke + '" stroke-width="' + sw + '"' +
          (dash ? ' stroke-dasharray="' + dash + '"' : "") + ' stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>' +
          heads + "</svg>",
        mid: G.labelPoint(el, path),
      };
    }

    function headPoly(pts, color) {
      return (
        '<polygon points="' + pts.map(function (p) {
          return Math.round(p.x * 10) / 10 + "," + Math.round(p.y * 10) / 10;
        }).join(" ") + '" fill="' + color + '" stroke="' + color + '" stroke-width="1" stroke-linejoin="round" pointer-events="none"/>'
      );
    }

    function penSvg(el) {
      var st = el.style || {};
      var sw = Number(st.strokeWidth) || 3;
      var pts = (el.points || []).map(function (p) {
        return G.pt(el.x + p[0], el.y + p[1]);
      });
      var d = G.smoothPath(pts);
      return (
        '<svg class="lb-shape" width="1" height="1" overflow="visible">' +
        '<path d="' + d + '" fill="none" stroke="transparent" stroke-width="' + Math.max(14, sw + 10) + '" pointer-events="stroke" class="lb-hit" stroke-linecap="round"/>' +
        '<path d="' + d + '" fill="none" stroke="' + inkVar(st.stroke || "ink") + '" stroke-width="' + sw + '" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>' +
        "</svg>"
      );
    }

    function ensureText(node) {
      var t = node.querySelector(":scope > .lb-text");
      if (!t) {
        t = document.createElement("div");
        t.className = "lb-text";
        node.appendChild(t);
      }
      return t;
    }

    function renderNode(id) {
      var el = els.get(id);
      var node = nodes.get(id);
      if (!el) {
        if (node) node.remove();
        nodes.delete(id);
        if (editing && editing.id === id) editing = null;
        return;
      }
      if (!node) {
        node = document.createElement("div");
        node.className = "lb-el";
        node.setAttribute("data-id", id);
        nodes.set(id, node);
        world.appendChild(node);
        orderDirty = true;
      }
      node.className = "lb-el lb-t-" + el.type + (el.group ? " is-grouped" : "");
      var isEditing = editing && editing.id === id;
      var textEl;
      if (el.type === "line") {
        var ls = lineSvg(el);
        node.style.cssText = "left:0;top:0;width:0;height:0";
        replaceShape(node, ls.svg);
        textEl = ensureText(node);
        textEl.classList.add("lb-line-label");
        textEl.style.left = ls.mid.x + "px";
        textEl.style.top = ls.mid.y + "px";
        textEl.classList.toggle("is-empty", !el.text && !isEditing);
      } else if (el.type === "pen") {
        node.style.cssText = "left:0;top:0;width:0;height:0";
        replaceShape(node, penSvg(el));
        return;
      } else {
        node.style.cssText =
          "left:" + el.x + "px;top:" + el.y + "px;width:" + Math.max(1, el.w) + "px;height:" + Math.max(1, el.h) + "px;" +
          (el.rot ? "transform:rotate(" + el.rot + "deg);" : "");
        replaceShape(node, shapeSvg(el));
        if (el.type === "image") return;
        textEl = ensureText(node);
        if (el.type === "sticky") renderAuthor(node, el.author);
      }
      textStyle(el, textEl);
      if (!isEditing && textEl.textContent !== String(el.text || "")) textEl.textContent = String(el.text || "");
      if (el.type === "text" && !isEditing) fitTextHeight(el, textEl);
    }

    /** Подпись автора внизу стикера: аватар (фото или инициалы) и имя; у Лео — ✨. */
    function renderAuthor(node, author) {
      var has = !!(author && (author.name || author.id));
      var key = has ? JSON.stringify(author) : "";
      var box = node.querySelector(":scope > .lb-author");
      node.classList.toggle("has-author", has);
      if (!has) {
        if (box) box.remove();
        return;
      }
      if (box && box.getAttribute("data-key") === key) return;
      if (!box) {
        box = document.createElement("div");
        box.className = "lb-author";
        node.appendChild(box);
      }
      box.setAttribute("data-key", key);
      var name = String(author.name || "Участник");
      box.title = author.ai ? "Добавил Лео" : "Автор: " + name;
      var av;
      if (author.ai) {
        av = '<span class="lb-av is-ai"><svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
          '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg></span>';
      } else {
        av = '<span class="lb-av lb-av--t' + toneOf(author.id || name) + '"><span class="lb-av-txt">' + esc(initialsOf(name)) + "</span></span>";
      }
      box.innerHTML = av + '<span class="lb-author-name">' + esc(name) + "</span>";
      if (!author.ai && author.id && o.decorateAvatar) {
        var img = document.createElement("img");
        img.alt = "";
        img.className = "lb-av-img";
        box.querySelector(".lb-av").appendChild(img);
        try {
          o.decorateAvatar(img, author);
        } catch (_) {}
      }
    }

    function initialsOf(name) {
      var parts = String(name || "").replace(/^@/, "").trim().split(/\s+/).filter(Boolean);
      if (!parts.length) return "?";
      return (parts[0].charAt(0) + (parts[1] ? parts[1].charAt(0) : "")).toUpperCase();
    }

    function toneOf(key) {
      var h = 0;
      var str = String(key || "");
      for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
      return Math.abs(h) % 6;
    }

    function replaceShape(node, html) {
      var old = node.querySelector(":scope > .lb-shape");
      if (old && old.getAttribute("data-src") === html) return;
      var tmp = document.createElement("div");
      tmp.innerHTML = html;
      var svg = tmp.firstChild;
      svg.setAttribute("data-src", html);
      if (old) node.replaceChild(svg, old);
      else node.insertBefore(svg, node.firstChild);
    }

    /** Высота самого текста (в единицах холста); null — узел не показан, мерить нечего. */
    function textHeight(textEl) {
      if (!textEl.isConnected || !root.offsetWidth) return null;
      if (!textEl.textContent) return 0;
      var range = document.createRange();
      range.selectNodeContents(textEl);
      var r = range.getBoundingClientRect();
      if (!r.width && !r.height) return null;
      return r.height / view.z;
    }

    /** Текстовый блок растёт по высоте под текст (без отдельного шага отмены). */
    function fitTextHeight(el, textEl) {
      if (el.rot) return;
      var th = textHeight(textEl);
      if (th == null) return;
      var h = Math.max(20, Math.ceil(th));
      if (Math.abs(h - el.h) > 1) {
        var next = Object.assign({}, el, { h: h });
        els.set(el.id, next);
        var n = nodes.get(el.id);
        if (n) n.style.height = h + "px";
        localChanged(el.id);
        dirty.add(el.id);
        schedule();
      }
    }

    /** Фигура с текстом вырастает, если текст не помещается. */
    function growToText(el, textEl) {
      if ((!SHAPES[el.type] && el.type !== "sticky") || el.rot) return null;
      var th = textHeight(textEl);
      if (!th) return null;
      // Отступы подписи; у ромба и эллипса текст помещается только в средней части.
      var pad = el.type === "sticky" ? 28 : 16;
      var need = Math.ceil(el.type === "diamond" ? th * 2 + pad : el.type === "ellipse" ? th * 1.45 + pad : th + pad);
      if (need > el.h + 1) return need;
      return null;
    }

    // ---- оверлей выделения ----
    var SVGNS = "http://www.w3.org/2000/svg";

    function selList() {
      var out = [];
      selected.forEach(function (id) {
        var el = els.get(id);
        if (el) out.push(el);
      });
      return out;
    }

    function selBounds() {
      return G.unionBounds(selList().map(function (el) {
        return G.elBounds(el, getEl);
      }));
    }

    function renderOverlay() {
      if (destroyed) return;
      var r = stage.getBoundingClientRect();
      overlay.setAttribute("width", r.width);
      overlay.setAttribute("height", r.height);
      var h = [];
      var z = view.z;
      var tr = 'transform="translate(' + view.x + " " + view.y + ") scale(" + z + ')"';
      var list = selList();
      var single = list.length === 1 ? list[0] : null;
      var hs = 8;
      list.forEach(function (el) {
        if (el.type === "line") {
          var lp = linePathOf(el);
          h.push('<path ' + tr + ' d="' + lp.d + '" class="lb-sel-line" vector-effect="non-scaling-stroke"/>');
        } else if (el.type === "pen") {
          var b = G.elBounds(el, getEl);
          h.push(rectPath(b, "lb-sel-box"));
        } else {
          h.push('<polygon class="lb-sel-box" points="' + G.corners(el).map(function (p) {
            var s = toScreen(p);
            return s.x + "," + s.y;
          }).join(" ") + '"/>');
        }
      });
      if (!readOnly && !editing && !(gesture && gesture.kind === "move")) {
        if (single && BOXED[single.type]) {
          var c = G.corners(single);
          var mids = [
            mid(c[0], c[1]), mid(c[1], c[2]), mid(c[2], c[3]), mid(c[3], c[0]),
          ];
          var names = [["nw", c[0]], ["n", mids[0]], ["ne", c[1]], ["e", mids[1]], ["se", c[2]], ["s", mids[2]], ["sw", c[3]], ["w", mids[3]]];
          if (single.type === "text") names = [["w", mids[3]], ["e", mids[1]], ["nw", c[0]], ["se", c[2]]];
          names.forEach(function (n) {
            var s = toScreen(n[1]);
            h.push('<rect class="lb-handle" data-h="' + n[0] + '" x="' + (s.x - hs / 2) + '" y="' + (s.y - hs / 2) + '" width="' + hs + '" height="' + hs + '" rx="2"/>');
          });
          var top = toScreen(mids[0]);
          var up = G.rot(G.pt(0, -1), G.pt(0, 0), single.rot || 0);
          var rp = G.add(top, G.mul(up, 24));
          h.push('<line class="lb-rot-stem" x1="' + top.x + '" y1="' + top.y + '" x2="' + rp.x + '" y2="' + rp.y + '"/>');
          h.push('<circle class="lb-handle lb-rot" data-h="rot" cx="' + rp.x + '" cy="' + rp.y + '" r="6"/>');
          if (single.type !== "image" || true) {
            G.SIDES.forEach(function (side) {
              var p = G.sidePoint(single, side);
              var n = G.sideNormal(single, side);
              var s = G.add(toScreen(p), G.mul(n, side === "top" ? 44 : 22));
              h.push(
                '<g class="lb-connect" data-h="c-' + side + '" transform="translate(' + s.x + " " + s.y + ')">' +
                '<circle r="9"/><path d="M-4 0H4M0 -4V4"/></g>'
              );
            });
          }
        } else if (single && single.type === "line") {
          var res = G.resolveLine(single, getEl);
          var lp2 = linePathOf(single, res);
          var wps = G.waypointsOf(single);
          var hr = COARSE ? 1.6 : 1;
          if ((single.route || "straight") === "elbow") {
            // Ломаная: ручка на каждом отрезке, тянется поперёк.
            (lp2.segs || []).forEach(function (sg) {
              if (sg.len * z < 18) return;
              var s = toScreen(sg.p);
              var lw = (sg.horiz ? 16 : 6) * hr;
              var lh = (sg.horiz ? 6 : 16) * hr;
              h.push('<rect class="lb-handle lb-seg' + (sg.horiz ? " is-h" : " is-v") + '" data-h="seg:' + sg.i + '" x="' + (s.x - lw / 2) + '" y="' + (s.y - lh / 2) + '" width="' + lw + '" height="' + lh + '" rx="3"/>');
            });
          } else {
            wps.forEach(function (q, i) {
              var s = toScreen(q);
              h.push('<circle class="lb-handle lb-wp" data-h="wp:' + i + '" cx="' + s.x + '" cy="' + s.y + '" r="' + 5 * hr + '"/>');
            });
            if (wps.length) {
              (lp2.segs || []).forEach(function (sg) {
                var s = toScreen(sg.p);
                h.push('<circle class="lb-handle lb-ins" data-h="ins:' + sg.i + '" cx="' + s.x + '" cy="' + s.y + '" r="' + 4 * hr + '"/>');
              });
            } else {
              var m = toScreen(lp2.mid);
              h.push('<rect class="lb-handle lb-bend" data-h="ins:0" x="' + (m.x - 5 * hr) + '" y="' + (m.y - 5 * hr) + '" width="' + 10 * hr + '" height="' + 10 * hr + '" rx="' + 5 * hr + '"/>');
            }
          }
          [["p0", res.p0], ["p1", res.p1]].forEach(function (n) {
            var s = toScreen(n[1]);
            h.push('<circle class="lb-handle lb-end" data-h="' + n[0] + '" cx="' + s.x + '" cy="' + s.y + '" r="' + 6 * hr + '"/>');
          });
        } else if (list.length > 1) {
          var sb = selBounds();
          if (sb) {
            h.push(rectPath(sb, "lb-sel-group"));
            var a = toScreen(G.pt(sb.x, sb.y));
            var bb = toScreen(G.pt(sb.x + sb.w, sb.y + sb.h));
            [["nw", a.x, a.y], ["ne", bb.x, a.y], ["se", bb.x, bb.y], ["sw", a.x, bb.y]].forEach(function (n) {
              h.push('<rect class="lb-handle" data-h="g' + n[0] + '" x="' + (n[1] - hs / 2) + '" y="' + (n[2] - hs / 2) + '" width="' + hs + '" height="' + hs + '" rx="2"/>');
            });
          }
        }
      }
      if (snapTarget && snapTarget.el) {
        var t = snapTarget.el;
        h.push('<polygon class="lb-snap-box" points="' + G.corners(t).map(function (p) {
          var s = toScreen(p);
          return s.x + "," + s.y;
        }).join(" ") + '"/>');
        G.SIDES.forEach(function (side) {
          var s = toScreen(G.sidePoint(t, side));
          h.push('<circle class="lb-snap-dot' + (snapTarget.side === side ? " is-on" : "") + '" cx="' + s.x + '" cy="' + s.y + '" r="5"/>');
        });
      }
      guides.forEach(function (g) {
        var p0 = toScreen(g.a);
        var p1 = toScreen(g.b);
        h.push('<line class="lb-guide" x1="' + p0.x + '" y1="' + p0.y + '" x2="' + p1.x + '" y2="' + p1.y + '"/>');
      });
      if (marquee) h.push(rectPath(marquee, "lb-marquee"));
      remoteCursors.forEach(function (c) {
        if (!c || !isFinite(c.x) || !isFinite(c.y)) return;
        var s = toScreen(G.pt(c.x, c.y));
        var color = esc(c.color || "#7b68ee");
        h.push(
          '<g class="lb-cursor" transform="translate(' + s.x + " " + s.y + ')">' +
          '<path d="M0 0L0 16L4.5 12L8 19L10.5 18L7 11L13 11Z" fill="' + color + '" stroke="#fff" stroke-width="1"/>' +
          '<text x="14" y="24" fill="' + color + '">' + esc(c.name || "") + "</text></g>"
        );
      });
      overlay.innerHTML = h.join("");
    }

    function mid(a, b) {
      return G.mul(G.add(a, b), 0.5);
    }

    function rectPath(b, cls) {
      var a = toScreen(G.pt(b.x, b.y));
      return '<rect class="' + cls + '" x="' + a.x + '" y="' + a.y + '" width="' + Math.max(0, b.w * view.z) + '" height="' + Math.max(0, b.h * view.z) + '"/>';
    }

    // ---- выбор ----
    function expandGroups(ids) {
      var groups = {};
      ids.forEach(function (id) {
        var el = els.get(id);
        if (el && el.group) groups[el.group] = 1;
      });
      var out = new Set(ids);
      if (Object.keys(groups).length) {
        els.forEach(function (el) {
          if (el.group && groups[el.group]) out.add(el.id);
        });
      }
      return out;
    }

    function setSelection(ids) {
      selected = expandGroups(Array.from(ids || []));
      schedule();
    }

    function hitId(target) {
      var node = target && target.closest ? target.closest(".lb-el") : null;
      if (!node || !world.contains(node)) return "";
      return node.getAttribute("data-id") || "";
    }

    /** Фигура под точкой (для прилипания концов линий): верхняя по z, с запасом. */
    function shapeAt(p, exclude) {
      var list = sortedEls().reverse();
      var m = 10 / view.z;
      for (var i = 0; i < list.length; i++) {
        var el = list[i];
        if (!BOXED[el.type] || (exclude && exclude[el.id])) continue;
        if (G.contains(el, p, m)) return el;
      }
      return null;
    }

    function snapEnd(p, exclude, free) {
      if (free) return { end: null, el: null };
      var el = shapeAt(p, exclude);
      if (!el) return { end: null, el: null };
      var ns = G.nearestSide(el, p);
      if (ns.dist * view.z <= 18) return { end: { el: el.id, side: ns.side }, el: el, side: ns.side };
      return { end: { el: el.id }, el: el, side: "" };
    }

    // ---- текст ----
    function startEdit(id, opts2) {
      if (readOnly) return;
      var el = els.get(id);
      if (!el || !TEXTUAL[el.type]) return;
      finishEdit();
      setSelection([id]);
      flushNow();
      var node = nodes.get(id);
      if (!node) return;
      var t = ensureText(node);
      t.classList.remove("is-empty");
      t.contentEditable = "plaintext-only";
      if (t.contentEditable !== "plaintext-only") t.contentEditable = "true";
      t.spellcheck = true;
      editing = { id: id, node: node, text: t, before: clone(el), isNew: !!(opts2 && opts2.isNew) };
      node.classList.add("is-editing");
      if (opts2 && typeof opts2.initial === "string") t.textContent = opts2.initial;
      t.focus({ preventScroll: true });
      var range = document.createRange();
      range.selectNodeContents(t);
      if (!(opts2 && opts2.selectAll)) range.collapse(false);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      t.addEventListener("input", onEditInput);
      t.addEventListener("keydown", onEditKey);
      t.addEventListener("blur", onEditBlur);
      renderOverlay();
      renderProps();
    }

    function onEditInput() {
      if (!editing) return;
      var el = els.get(editing.id);
      if (!el) return;
      if (el.type === "text") {
        var th = textHeight(editing.text);
        if (th != null) editing.node.style.height = Math.max(20, Math.ceil(th)) + "px";
      } else {
        var need = growToText(el, editing.text);
        if (need) editing.node.style.height = need + "px";
      }
    }

    function onEditKey(e) {
      if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        e.stopPropagation();
        finishEdit();
        stage.focus({ preventScroll: true });
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        finishEdit();
        stage.focus({ preventScroll: true });
      }
      e.stopPropagation();
    }

    function onEditBlur() {
      setTimeout(function () {
        if (editing && document.activeElement !== editing.text) finishEdit();
      }, 0);
    }

    function finishEdit() {
      if (!editing) return;
      var ed = editing;
      editing = null;
      ed.text.removeEventListener("input", onEditInput);
      ed.text.removeEventListener("keydown", onEditKey);
      ed.text.removeEventListener("blur", onEditBlur);
      ed.text.contentEditable = "false";
      ed.text.removeAttribute("contenteditable");
      ed.node.classList.remove("is-editing");
      var text = String(ed.text.innerText || "").replace(/ /g, " ").replace(/\n+$/, "");
      var el = els.get(ed.id);
      if (!el) return;
      begin();
      if (el.type === "text" && !text.trim()) {
        remove(ed.id);
      } else {
        var fields = (el.text || "") === text ? {} : { text: text };
        if (el.type === "text") {
          var th = textHeight(ed.text);
          if (th != null && !el.rot) fields.h = Math.max(20, Math.ceil(th));
        } else {
          var need = growToText(el, ed.text);
          if (need) fields.h = need;
        }
        patch(ed.id, fields);
      }
      // Новый элемент и его текст — один шаг отмены.
      if (ed.isNew && undoStack.length && tx && tx.depth === 1) {
        var last = undoStack[undoStack.length - 1];
        if (last.length === 1 && last[0].id === ed.id && last[0].before === null) {
          undoStack.pop();
          tx.before.set(ed.id, null);
        }
      }
      commit();
      schedule();
    }

    function flushNow() {
      if (frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
      flush();
    }

    // ---- создание ----
    function styleFor(type) {
      var base = clone((DEFAULTS[type] || {}).style || {});
      return Object.assign(base, lastStyle[type] || {});
    }

    function newEl(type, p, extra) {
      var d = DEFAULTS[type] || {};
      var el = Object.assign(
        { id: uid(), type: type, x: p.x, y: p.y, w: d.w || 0, h: d.h || 0, z: topZ(), style: styleFor(type) },
        extra || {}
      );
      if (type === "sticky" && !el.author && meAuthor()) el.author = meAuthor();
      return el;
    }

    /** Кто я — автор новых стикеров (подпись внизу стикера, как у комментариев). */
    function meAuthor() {
      var me = o.me;
      if (!me || !(me.id || me.name)) return null;
      var a = {};
      if (me.id) a.id = String(me.id);
      if (me.name) a.name = String(me.name);
      return a;
    }

    function createAtCenter(type, extra) {
      var r = stageRect();
      var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
      var d = DEFAULTS[type] || { w: 100, h: 100 };
      var w = (extra && extra.w) || d.w;
      var h = (extra && extra.h) || d.h;
      begin();
      var el = newEl(type, G.pt(c.x - w / 2, c.y - h / 2), extra);
      put(el);
      commit();
      setSelection([el.id]);
      return el;
    }

    function pickImage() {
      var input = document.createElement("input");
      input.type = "file";
      input.accept = "image/*";
      input.style.display = "none";
      document.body.appendChild(input);
      input.addEventListener("change", function () {
        var f = input.files && input.files[0];
        input.remove();
        if (f) insertImageFile(f);
      });
      input.click();
    }

    function insertImageFile(file, at) {
      if (!file || !/^image\//i.test(String(file.type || ""))) return Promise.resolve();
      if (file.size > 25 * 1024 * 1024) {
        notify("Картинка больше 25 МБ");
        return Promise.resolve();
      }
      return readFileAsDataUrl(file).then(compressImage).then(function (img) {
        if (!img) {
          notify("Не удалось открыть картинку");
          return;
        }
        var k = Math.min(1, 480 / Math.max(img.w, img.h));
        var w = Math.round(img.w * k);
        var h = Math.round(img.h * k);
        if (at) {
          begin();
          var el = newEl("image", G.pt(at.x - w / 2, at.y - h / 2), { w: w, h: h, src: img.src, style: undefined });
          delete el.style;
          put(el);
          commit();
          setSelection([el.id]);
        } else {
          var el2 = createAtCenter("image", { w: w, h: h, src: img.src });
          delete el2.style;
        }
        setTool("select");
      });
    }

    function notify(text) {
      if (typeof o.onNotify === "function") o.onNotify(text);
    }

    // ---- инструменты и панели ----
    function setTool(t) {
      tool = t;
      stage.setAttribute("data-tool", t);
      if (toolsBar) {
        toolsBar.querySelectorAll("[data-tool]").forEach(function (b) {
          b.classList.toggle("is-on", b.getAttribute("data-tool") === t);
        });
      }
    }

    function renderTools() {
      if (!toolsBar) return;
      toolsBar.innerHTML = TOOLS.map(function (t) {
        return '<button type="button" class="lb-btn" data-tool="' + t.id + '" title="' + esc(t.title) + '" aria-label="' + esc(t.title) + '">' + icon(t.icon) + "</button>";
      }).join("");
      var syncToolsEnd = function () {
        toolsBar.classList.toggle("is-scrolled-end", toolsBar.scrollLeft + toolsBar.clientWidth >= toolsBar.scrollWidth - 4);
      };
      toolsBar.addEventListener("scroll", syncToolsEnd, { passive: true });
      setTimeout(syncToolsEnd, 0);
      toolsBar.addEventListener("click", function (e) {
        var b = e.target.closest("[data-tool]");
        if (!b) return;
        var t = b.getAttribute("data-tool");
        finishEdit();
        if (t === "image") {
          pickImage();
          return;
        }
        setTool(t);
        stage.focus({ preventScroll: true });
      });
      setTool("select");
    }

    function renderHistoryBar() {
      if (!historyBar) return;
      historyBar.innerHTML =
        '<button type="button" class="lb-btn" data-act="undo" title="Отменить (⌘Z)"' + (undoStack.length ? "" : " disabled") + ">" + icon("undo") + "</button>" +
        '<button type="button" class="lb-btn" data-act="redo" title="Повторить (⇧⌘Z)"' + (redoStack.length ? "" : " disabled") + ">" + icon("redo") + "</button>";
    }

    // Чат с Лео (кнопка «Обсуждение», как в нижней панели заметки): бейдж — число непрочитанных или точка.
    var chatBadge = "";
    var CHAT_SVG = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';

    function renderZoomBar() {
      var pct = Math.round(view.z * 100) + "%";
      var stateKey = pct + showGrid + showMap + "|" + chatBadge;
      if (zoomBar.getAttribute("data-pct") === stateKey) return;
      zoomBar.setAttribute("data-pct", stateKey);
      zoomBar.innerHTML =
        '<button type="button" class="lb-btn" data-act="zoom-out" title="Отдалить (−)">' + icon("minus") + "</button>" +
        '<button type="button" class="lb-btn lb-pct" data-act="zoom-100" title="Масштаб 100%">' + pct + "</button>" +
        '<button type="button" class="lb-btn" data-act="zoom-in" title="Приблизить (+)">' + icon("plus") + "</button>" +
        '<button type="button" class="lb-btn" data-act="fit" title="Показать всё (⇧1)">' + icon("fit") + "</button>" +
        '<button type="button" class="lb-btn lb-map-btn' + (showMap ? " is-on" : "") + '" data-act="map" title="Мини-карта">' + icon("map") + "</button>" +
        (readOnly
          ? ""
          : '<button type="button" class="lb-btn' + (showGrid ? " is-on" : "") + '" data-act="grid" title="Сетка и привязка">' + icon("grid") + "</button>" +
            '<button type="button" class="lb-btn" data-act="export" title="Экспорт">' + icon("download") + "</button>") +
        (o.onOpenChat
          ? '<span class="lb-sep"></span><button type="button" class="lb-btn lb-chat-btn" data-act="chat" title="Обсуждение с Лео" aria-label="Обсуждение">' + CHAT_SVG +
            (chatBadge ? '<span class="lb-chat-badge' + (chatBadge === "•" ? " is-dot" : "") + '">' + (chatBadge === "•" ? "" : esc(chatBadge)) + "</span>" : "") +
            "</button>"
          : "");
      minimap.classList.toggle("hidden", !showMap);
    }

    function onBarClick(e) {
      var b = e.target.closest("[data-act]");
      if (!b || b.disabled) return;
      var act = b.getAttribute("data-act");
      if (/^zoom|^fit$/.test(act)) autoFit = false;
      if (act === "chat") {
        if (editing) finishEdit();
        if (o.onOpenChat) o.onOpenChat();
        return;
      }
      if (act === "undo") undo();
      else if (act === "redo") redo();
      else if (act === "zoom-in") zoomBy(1.25);
      else if (act === "zoom-out") zoomBy(0.8);
      else if (act === "zoom-100") zoomTo(1);
      else if (act === "fit") fitAll();
      else if (act === "grid") {
        showGrid = !showGrid;
        applyView();
      } else if (act === "map") {
        showMap = !showMap;
        renderZoomBar();
        scheduleMinimap();
      } else if (act === "export") {
        openPop(b, function (box) {
          box.innerHTML =
            '<button type="button" class="lb-menu-item" data-x="png">Скачать PNG</button>' +
            '<button type="button" class="lb-menu-item" data-x="svg">Скачать SVG</button>';
          box.addEventListener("click", function (ev) {
            var x = ev.target.closest("[data-x]");
            if (!x) return;
            closePop();
            if (x.getAttribute("data-x") === "png") exportPng();
            else exportSvg();
          });
        });
      }
    }

    // ---- панель свойств выделения ----
    function commonStyle(list, key) {
      var v;
      for (var i = 0; i < list.length; i++) {
        var cur = (list[i].style || {})[key];
        if (cur == null) cur = ((DEFAULTS[list[i].type] || {}).style || {})[key];
        if (i === 0) v = cur;
        else if (v !== cur) return undefined;
      }
      return v;
    }

    /** Какие действия заметки доступны для выделения: ИИ и «Обсудить» — только с текстом, ссылка — на один блок. */
    function blockActsFor(list) {
      var acts = o.blockActions;
      if (!acts || readOnly) return [];
      var hasText = list.some(function (e) {
        return String(e.text || "").trim();
      });
      return NOTE_ACTS.filter(function (a) {
        if (!acts[a.id]) return false;
        if (a.id === "ai" || a.id === "discuss") return hasText;
        if (a.id === "link") return list.length === 1;
        return true;
      });
    }

    /** Выделенные элементы для действий заметки: id, вид и текст (сверху вниз, слева направо). */
    function selectionInfo() {
      return selList().filter(function (el) {
        return el.type !== "pen";
      }).sort(function (a, b) {
        return a.y - b.y || a.x - b.x;
      }).map(function (el) {
        var label = el.type === "line" ? labelOfLine(el) : "";
        return { id: el.id, type: el.type, text: String(el.text || "").trim(), label: label };
      });
    }

    function labelOfLine(ln) {
      var a = ln.start && getEl(ln.start.el);
      var b = ln.end && getEl(ln.end.el);
      if (!a || !b) return "";
      return oneLine(a.text || "…") + " → " + oneLine(b.text || "…");
    }

    /** По ссылке: показать элемент — в центр экрана, выделить и мигнуть. */
    function revealElement(id) {
      var el = getEl(id);
      if (!el) return false;
      autoFit = false;
      var r = stage.getBoundingClientRect();
      var b = G.elBounds(el, getEl);
      if (b && r.width && r.height) {
        var pad = 80;
        var z = clamp(Math.min((r.width - pad * 2) / Math.max(1, b.w), (r.height - pad * 2) / Math.max(1, b.h), Math.max(view.z, 0.8)), MIN_Z, 1.5);
        view.z = z;
        view.x = r.width / 2 - (b.x + b.w / 2) * z;
        view.y = r.height / 2 - (b.y + b.h / 2) * z;
        applyView();
      }
      setSelection([id]);
      flushNow();
      var node = nodes.get(id);
      if (node) {
        node.classList.remove("is-flash");
        void node.offsetWidth;
        node.classList.add("is-flash");
        setTimeout(function () {
          node.classList.remove("is-flash");
        }, 1600);
      }
      return true;
    }

    /** Новые тексты блоков (ИИ) — одним шагом отмены. */
    function setTexts(map) {
      if (readOnly) return 0;
      var n = 0;
      begin();
      Object.keys(map || {}).forEach(function (id) {
        var el = getEl(id);
        var t = String(map[id] == null ? "" : map[id]);
        if (!el || !TEXTUAL[el.type] || (el.text || "") === t) return;
        var f = { text: t };
        if (el.type !== "line" && el.type !== "text") {
          // Блок растёт под длинный текст — как при ручном вводе.
          var node = nodes.get(id);
          var tEl = node && node.querySelector(":scope > .lb-text");
          if (tEl) {
            var old = tEl.textContent;
            tEl.textContent = t;
            var need = growToText(el, tEl);
            tEl.textContent = old;
            if (need) f.h = need;
          }
        }
        patch(id, f);
        n++;
      });
      commit();
      return n;
    }

    function renderProps() {
      var list = selList();
      if (readOnly || !list.length || (gesture && gesture.kind !== "none")) {
        propsBar.classList.add("hidden");
        return;
      }
      var types = {};
      list.forEach(function (el) {
        types[el.type] = 1;
      });
      var anyShape = types.rect || types.diamond || types.ellipse;
      var anyStroke = anyShape || types.line || types.pen;
      var anyText = types.text || anyShape || types.sticky || types.line;
      var h = [];
      // Действия заметки с выделенными блоками: ИИ, обсудить с Лео, задача, ссылка (как у выделенного текста).
      var acts = o.blockActions;
      // Голые стрелки и штрихи без подписи — не блоки: им эти действия не нужны.
      if (acts && list.some(function (e) {
        return (e.type !== "line" && e.type !== "pen") || String(e.text || "").trim();
      })) {
        // Одна кнопка (✨ как «ИИ» в заметке) — по нажатию меню: ИИ · Обсудить · Задача · Ссылка.
        if (blockActsFor(list).length) {
          h.push('<button type="button" class="lb-btn lb-pbtn lb-nact-trigger" data-p="noteacts" title="ИИ, обсудить, задача, ссылка">' + NOTE_ACTS[0].svg + "</button>");
          h.push('<span class="lb-sep"></span>');
        }
      }
      var key = JSON.stringify([Object.keys(types).sort(), list.map(function (e) {
        return [e.style, e.route, e.arrowStart, e.arrowEnd, e.group, e.type, !!(e.bend || (e.waypoints && e.waypoints.length)), !!String(e.text || "").trim(), acts ? e.id : ""];
      }), list.length]);
      if (types.sticky) h.push(swatchBtn("sticky", stickyVar(commonStyle(list.filter(isType("sticky")), "fill") || "yellow"), "Цвет стикера"));
      if (anyStroke) h.push(swatchBtn("stroke", inkVar(commonStyle(list.filter(isStroked), "stroke") || "ink"), "Цвет линии", true));
      if (anyShape) h.push(swatchBtn("fill", fillVar(commonStyle(list.filter(isShape), "fill") || "none"), "Заливка"));
      if (types.text) h.push(swatchBtn("textColor", inkVar(commonStyle(list.filter(isType("text")), "textColor") || "ink"), "Цвет текста", false, true));
      if (anyStroke) h.push('<button type="button" class="lb-btn lb-pbtn" data-p="width" title="Толщина и пунктир">' + widthIcon(commonStyle(list.filter(isStroked), "strokeWidth") || 2, commonStyle(list.filter(isStroked), "dash")) + "</button>");
      if (anyText) {
        var fs = commonStyle(list.filter(isTextual), "fontSize");
        var lbl = (FONT_SIZES.find(function (f) {
          return f.v === fs;
        }) || { t: fs ? String(fs) : "Aa" }).t;
        h.push('<button type="button" class="lb-btn lb-pbtn lb-font" data-p="font" title="Размер текста">' + esc(lbl) + "</button>");
      }
      if (list.every(isAlignable)) {
        var al = alignOf(list[0]);
        if (!list.every(function (e) {
          return alignOf(e) === al;
        })) al = "left";
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="align" title="Выравнивание">' + icon(ALIGN_ICON[al]) + "</button>");
      }
      if (anyShape && !types.line && !types.pen && !types.text && !types.sticky && !types.image) {
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="shape" title="Фигура">' + icon(list[0].type) + "</button>");
      }
      if (types.line) {
        var lines = list.filter(isType("line"));
        var as = lines.every(function (l) {
          return l.arrowStart;
        });
        var ae = lines.every(function (l) {
          return l.arrowEnd;
        });
        var route = lines.every(function (l) {
          return (l.route || "straight") === (lines[0].route || "straight");
        }) ? lines[0].route || "straight" : "straight";
        h.push('<span class="lb-sep"></span>');
        h.push('<button type="button" class="lb-btn lb-pbtn' + (as ? " is-on" : "") + '" data-p="arrowStart" title="Наконечник в начале">' + icon("arrowStart") + "</button>");
        h.push('<button type="button" class="lb-btn lb-pbtn' + (ae ? " is-on" : "") + '" data-p="arrowEnd" title="Наконечник в конце">' + icon("arrowEnd") + "</button>");
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="route" title="Вид линии">' + icon(route) + "</button>");
        if (lines.some(function (l) {
          return l.bend || (l.waypoints && l.waypoints.length);
        })) {
          h.push('<button type="button" class="lb-btn lb-pbtn" data-p="unbend" title="Сбросить изломы">' + icon("unbend") + "</button>");
        }
      }
      h.push('<span class="lb-sep"></span>');
      if (list.length > 1) {
        var grouped = list.every(function (e) {
          return e.group && e.group === list[0].group;
        });
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="' + (grouped ? "ungroup" : "group") + '" title="' + (grouped ? "Разгруппировать (⇧⌘G)" : "Сгруппировать (⌘G)") + '">' + icon(grouped ? "ungroup" : "group") + "</button>");
      }
      h.push('<button type="button" class="lb-btn lb-pbtn" data-p="more" title="Ещё">' + icon("more") + "</button>");
      h.push('<button type="button" class="lb-btn lb-pbtn lb-danger" data-p="delete" title="Удалить (Delete)">' + icon("trash") + "</button>");
      if (propsBar.getAttribute("data-key") !== key) {
        propsBar.setAttribute("data-key", key);
        propsBar.innerHTML = h.join("");
      }
      propsBar.classList.remove("hidden");
      positionProps();
    }

    function positionProps() {
      var b = selBounds();
      if (!b) return;
      var r = stage.getBoundingClientRect();
      var a = toScreen(G.pt(b.x, b.y));
      var c = toScreen(G.pt(b.x + b.w, b.y + b.h));
      var pw = propsBar.offsetWidth || 300;
      var ph = propsBar.offsetHeight || 40;
      var x = clamp((a.x + c.x) / 2 - pw / 2, 8, Math.max(8, r.width - pw - 8));
      var single = selList().length === 1 && BOXED[selList()[0].type];
      var y = a.y - ph - (single ? 56 : 14);
      if (y < 8) y = c.y + 40;
      if (y + ph > r.height - 8) y = Math.max(8, r.height - ph - 64);
      propsBar.style.left = x + "px";
      propsBar.style.top = y + "px";
    }

    function isType(t) {
      return function (e) {
        return e.type === t;
      };
    }

    function isStroked(e) {
      return !!STROKED[e.type];
    }

    function isShape(e) {
      return !!SHAPES[e.type];
    }

    function isTextual(e) {
      return !!TEXTUAL[e.type];
    }

    function swatchBtn(p, color, title, ring, letter) {
      return (
        '<button type="button" class="lb-btn lb-pbtn" data-p="' + p + '" title="' + esc(title) + '">' +
        (letter
          ? '<span class="lb-swatch-a" style="color:' + color + '">A</span>'
          : '<span class="lb-swatch' + (ring ? " is-ring" : "") + (color === "none" ? " is-none" : "") + '" style="--c:' + color + '"></span>') +
        "</button>"
      );
    }

    function widthIcon(w, dash) {
      return (
        '<svg viewBox="0 0 20 20" width="20" height="20"><path d="M3 10h14" stroke="currentColor" stroke-width="' + Math.min(6, w) + '" stroke-linecap="round"' +
        (dash ? ' stroke-dasharray="' + dashArray(dash, Math.min(3, w)) + '"' : "") + "/></svg>"
      );
    }

    function applyStyle(filter, fields) {
      var list = selList().filter(filter);
      if (!list.length) return;
      begin();
      list.forEach(function (el) {
        patch(el.id, { style: fields });
        lastStyle[el.type] = Object.assign({}, lastStyle[el.type] || {}, fields);
      });
      commit();
    }

    function applyFields(filter, fieldsFn) {
      var list = selList().filter(filter);
      if (!list.length) return;
      begin();
      list.forEach(function (el) {
        patch(el.id, fieldsFn(el));
      });
      commit();
    }

    function onPropsClick(e) {
      var b = e.target.closest("[data-p]");
      if (!b) return;
      var p = b.getAttribute("data-p");
      if (p === "stroke" || p === "textColor") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + INK.map(function (k) {
            return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + inkVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s = ev.target.closest("[data-v]");
            if (!s) return;
            var f = {};
            f[p] = s.getAttribute("data-v");
            applyStyle(p === "stroke" ? isStroked : isType("text"), f);
            closePop();
          };
        });
      } else if (p === "fill") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + FILLS.map(function (k) {
            return '<button type="button" class="lb-sw' + (k === "none" ? " is-none" : "") + '" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + fillVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s = ev.target.closest("[data-v]");
            if (!s) return;
            applyStyle(isShape, { fill: s.getAttribute("data-v") });
            closePop();
          };
        });
      } else if (p === "sticky") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + STICKY.map(function (k) {
            return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + stickyVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s = ev.target.closest("[data-v]");
            if (!s) return;
            applyStyle(isType("sticky"), { fill: s.getAttribute("data-v") });
            closePop();
          };
        });
      } else if (p === "width") {
        openPop(b, function (box) {
          box.innerHTML =
            '<div class="lb-row">' + WIDTHS.map(function (w) {
              return '<button type="button" class="lb-btn" data-w="' + w + '" title="' + w + ' px">' + widthIcon(w) + "</button>";
            }).join("") + "</div>" +
            '<div class="lb-row">' + [["solid", "Сплошная"], ["dashed", "Пунктир"], ["dotted", "Точки"]].map(function (d) {
              return '<button type="button" class="lb-btn" data-d="' + d[0] + '" title="' + d[1] + '">' + widthIcon(2, d[0] === "solid" ? "" : d[0]) + "</button>";
            }).join("") + "</div>";
          box.onclick = function (ev) {
            var w = ev.target.closest("[data-w]");
            var d = ev.target.closest("[data-d]");
            if (w) applyStyle(isStroked, { strokeWidth: Number(w.getAttribute("data-w")) });
            if (d) applyStyle(isStroked, { dash: d.getAttribute("data-d") === "solid" ? null : d.getAttribute("data-d") });
          };
        });
      } else if (p === "font") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + FONT_SIZES.map(function (f) {
            return '<button type="button" class="lb-btn lb-font" data-f="' + f.v + '">' + f.t + "</button>";
          }).join("") + '<button type="button" class="lb-btn lb-font" data-b="1" title="Жирный"><b>B</b></button></div>';
          box.onclick = function (ev) {
            var f = ev.target.closest("[data-f]");
            var bb = ev.target.closest("[data-b]");
            if (f) applyStyle(isTextual, { fontSize: Number(f.getAttribute("data-f")) });
            if (bb) {
              var bold = !commonStyle(selList().filter(isTextual), "bold");
              applyStyle(isTextual, { bold: bold || null });
            }
          };
        });
      } else if (p === "align") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + [["left", "По левому краю"], ["center", "По центру"], ["right", "По правому краю"]].map(function (a) {
            return '<button type="button" class="lb-btn" data-a="' + a[0] + '" title="' + a[1] + '">' + icon(ALIGN_ICON[a[0]]) + "</button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var a = ev.target.closest("[data-a]");
            if (!a) return;
            applyStyle(isAlignable, { align: a.getAttribute("data-a") });
            closePop();
          };
        });
      } else if (p === "shape") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + ["rect", "diamond", "ellipse"].map(function (t) {
            return '<button type="button" class="lb-btn" data-t="' + t + '">' + icon(t) + "</button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var t = ev.target.closest("[data-t]");
            if (!t) return;
            var tt = t.getAttribute("data-t");
            applyFields(isShape, function () {
              return { type: tt };
            });
            closePop();
          };
        });
      } else if (p === "arrowStart" || p === "arrowEnd") {
        var on = !b.classList.contains("is-on");
        applyFields(isType("line"), function () {
          var f = {};
          f[p] = on || undefined;
          return f;
        });
      } else if (p === "noteacts") {
        var avail = blockActsFor(selList());
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-nacts">' + avail.map(function (a) {
            return '<button type="button" class="lb-nact' + a.cls + '" data-act="' + a.id + '" title="' + (a.id === "link" ? "Скопировать ссылку на блок" : a.label) + '">' +
              a.svg + "<span>" + a.label + "</span></button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var btn = ev.target.closest("[data-act]");
            if (!btn) return;
            var fn = o.blockActions && o.blockActions[btn.getAttribute("data-act")];
            closePop();
            if (editing) finishEdit();
            if (fn) fn(selectionInfo());
          };
        });
      } else if (p === "unbend") {
        applyFields(isType("line"), function () {
          return { waypoints: undefined, bend: undefined };
        });
      } else if (p === "route") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + [["straight", "Прямая"], ["curve", "Кривая"], ["elbow", "Ломаная"]].map(function (r) {
            return '<button type="button" class="lb-btn" data-r="' + r[0] + '" title="' + r[1] + '">' + icon(r[0]) + "</button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var r = ev.target.closest("[data-r]");
            if (!r) return;
            var route = r.getAttribute("data-r");
            applyFields(isType("line"), function () {
              return { route: route, bend: 0 };
            });
            lastStyle.lineRoute = route;
            closePop();
          };
        });
      } else if (p === "group") groupSel();
      else if (p === "ungroup") ungroupSel();
      else if (p === "delete") deleteSel();
      else if (p === "more") {
        openPop(b, function (box) {
          box.innerHTML =
            '<button type="button" class="lb-menu-item" data-m="dup">' + icon("copy") + "Дублировать<kbd>⌘D</kbd></button>" +
            '<button type="button" class="lb-menu-item" data-m="front">' + icon("front") + "На передний план<kbd>]</kbd></button>" +
            '<button type="button" class="lb-menu-item" data-m="back">' + icon("back") + "На задний план<kbd>[</kbd></button>";
          box.onclick = function (ev) {
            var m = ev.target.closest("[data-m]");
            if (!m) return;
            var a = m.getAttribute("data-m");
            if (a === "dup") duplicateSel();
            else if (a === "front") reorderSel(true);
            else reorderSel(false);
            closePop();
          };
        });
      }
    }

    function openPop(anchor, build) {
      if (!pop.classList.contains("hidden") && pop._anchor === anchor) {
        closePop();
        return;
      }
      pop.innerHTML = "";
      pop.onclick = null;
      var box = pop;
      build(box);
      pop._anchor = anchor;
      pop.classList.remove("hidden");
      var r = root.getBoundingClientRect();
      var a = anchor.getBoundingClientRect();
      var pw = pop.offsetWidth;
      var ph = pop.offsetHeight;
      var x = clamp(a.left - r.left + a.width / 2 - pw / 2, 8, Math.max(8, r.width - pw - 8));
      var y = a.bottom - r.top + 6;
      if (y + ph > r.height - 8) y = a.top - r.top - ph - 6;
      pop.style.left = x + "px";
      pop.style.top = Math.max(8, y) + "px";
    }

    function closePop() {
      pop.classList.add("hidden");
      pop._anchor = null;
      pop.onclick = null;
    }

    // ---- действия с выделением ----
    function deleteSel() {
      var ids = Array.from(selected);
      if (!ids.length) return;
      var gone = {};
      ids.forEach(function (id) {
        gone[id] = 1;
      });
      begin();
      // Стрелки, прилипшие к удаляемым блокам, удаляются вместе с ними.
      els.forEach(function (el) {
        if (el.type === "line" && ((el.start && gone[el.start.el]) || (el.end && gone[el.end.el]))) gone[el.id] = 1;
      });
      Object.keys(gone).forEach(remove);
      commit();
      selected.clear();
      closePop();
      schedule();
    }

    function copyEls(list, dx, dy) {
      var map = {};
      var groups = {};
      list.forEach(function (el) {
        map[el.id] = uid();
      });
      var z = topZ();
      var out = list.slice().sort(function (a, b) {
        return (a.z || 0) - (b.z || 0);
      }).map(function (el, i) {
        var c = clone(el);
        c.id = map[el.id];
        c.z = z + i * 0.001;
        c.x = (c.x || 0) + dx;
        c.y = (c.y || 0) + dy;
        if (c.group) {
          if (!groups[c.group]) groups[c.group] = uid();
          c.group = groups[c.group];
        }
        if (c.type === "line") {
          var res = G.resolveLine(el, function (id) {
            return getEl(id) || null;
          });
          c.points = [[res.p0.x + dx, res.p0.y + dy], [res.p1.x + dx, res.p1.y + dy]];
          if (c.waypoints) c.waypoints = shiftWaypoints(c.waypoints, dx, dy);
          ["start", "end"].forEach(function (k) {
            if (c[k] && !map[c[k].el]) delete c[k];
            else if (c[k]) c[k] = Object.assign({}, c[k], { el: map[c[k].el] });
          });
        }
        return c;
      });
      return out;
    }

    function duplicateSel() {
      var list = selList();
      if (!list.length) return;
      var copies = copyEls(list, 24, 24);
      begin();
      copies.forEach(put);
      commit();
      setSelection(copies.map(function (c) {
        return c.id;
      }));
    }

    function reorderSel(front) {
      var list = selList().sort(function (a, b) {
        return (a.z || 0) - (b.z || 0);
      });
      if (!list.length) return;
      var base = front ? topZ() : bottomZ() - list.length * 0.001;
      begin();
      list.forEach(function (el, i) {
        patch(el.id, { z: base + i * 0.001 });
      });
      commit();
    }

    function groupSel() {
      var list = selList();
      if (list.length < 2) return;
      var g = uid();
      begin();
      list.forEach(function (el) {
        patch(el.id, { group: g });
      });
      commit();
    }

    function ungroupSel() {
      begin();
      selList().forEach(function (el) {
        if (el.group) patch(el.id, { group: undefined });
      });
      commit();
    }

    function nudge(dx, dy) {
      var list = selList();
      if (!list.length) return;
      begin();
      list.forEach(function (el) {
        patch(el.id, moved(el, dx, dy, null));
      });
      commit();
    }

    function shiftWaypoints(wps, dx, dy) {
      return (wps || []).map(function (q) {
        return [Math.round((q[0] + dx) * 10) / 10, Math.round((q[1] + dy) * 10) / 10];
      });
    }

    /** Сдвиг элемента; у линии сдвигаются свободные концы, прилипшие к неподвижным блокам — отлипают. */
    function moved(el0, dx, dy, movingIds) {
      if (el0.type === "line") {
        var res = G.resolveLine(el0, getEl);
        var f = { points: [[res.p0.x + dx, res.p0.y + dy], [res.p1.x + dx, res.p1.y + dy]] };
        ["start", "end"].forEach(function (k) {
          if (el0[k] && !(movingIds && movingIds[el0[k].el])) f[k] = undefined;
        });
        if (el0.waypoints && el0.waypoints.length) f.waypoints = shiftWaypoints(el0.waypoints, dx, dy);
        return f;
      }
      return { x: el0.x + dx, y: el0.y + dy };
    }

    // ---- буфер обмена ----
    function onCopy(e) {
      if (editing || !selected.size) return;
      var list = selList();
      var payload = JSON.stringify({ leoBoard: 1, elements: list.map(function (el) {
        return serializeEl(el, getEl, pathOptsOf);
      }) });
      var text = list.map(function (el) {
        return el.text || "";
      }).filter(Boolean).join("\n");
      try {
        e.clipboardData.setData(CLIP_MIME, payload);
        e.clipboardData.setData("text/plain", text || " ");
        e.clipboardData.setData("text/html", '<span data-leo-board="' + esc(payload) + '">' + esc(text) + "</span>");
        e.preventDefault();
      } catch (_) {}
      return true;
    }

    function onCut(e) {
      if (readOnly || editing || !selected.size) return;
      if (onCopy(e)) deleteSel();
    }

    function onPaste(e) {
      if (readOnly || editing) return;
      var cd = e.clipboardData;
      if (!cd) return;
      var raw = cd.getData(CLIP_MIME);
      if (!raw) {
        var html = cd.getData("text/html") || "";
        var m = html.match(/data-leo-board="([^"]*)"/);
        if (m) {
          var ta = document.createElement("textarea");
          ta.innerHTML = m[1];
          raw = ta.value;
        }
      }
      var files = Array.prototype.slice.call(cd.files || []).filter(function (f) {
        return /^image\//i.test(f.type || "");
      });
      e.preventDefault();
      if (raw) {
        try {
          var data = JSON.parse(raw);
          var list = parseBoard({ elements: data.elements || [] });
          if (list.length) {
            var tmp = new Map();
            list.forEach(function (el) {
              tmp.set(el.id, el);
            });
            var b = G.unionBounds(list.map(function (el) {
              return G.elBounds(el, function (id) {
                return tmp.get(id) || null;
              });
            }));
            var r = stageRect();
            var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
            var dx = b ? c.x - (b.x + b.w / 2) : 0;
            var dy = b ? c.y - (b.y + b.h / 2) : 0;
            // Вставка рядом с исходником — со сдвигом, если исходник на экране.
            var saved = getEl;
            var copies;
            getEl = function (id) {
              return tmp.get(id) || null;
            };
            try {
              copies = copyEls(list, Math.round(dx), Math.round(dy));
            } finally {
              getEl = saved;
            }
            begin();
            copies.forEach(put);
            commit();
            setSelection(copies.map(function (x) {
              return x.id;
            }));
            return;
          }
        } catch (_) {}
      }
      if (files.length) {
        insertImageFile(files[0]);
        return;
      }
      var text = (cd.getData("text/plain") || "").trim();
      if (text) {
        var el = createAtCenter("text", { text: text.slice(0, 20000) });
        setSelection([el.id]);
      }
    }

    // ---- указатель ----
    function startGesture(g) {
      gesture = g;
      closePop();
      propsBar.classList.add("hidden");
    }

    function onPointerDown(e) {
      if (destroyed) return;
      autoFit = false;
      if (editing && editing.text.contains(e.target)) return;
      if (e.button === 2) return;
      if (editing) finishEdit();
      closePop();
      try {
        stage.setPointerCapture(e.pointerId);
      } catch (_) {}
      if (document.activeElement !== stage) stage.focus({ preventScroll: true });
      pointers.set(e.pointerId, G.pt(e.clientX, e.clientY));
      var rect = stageRect();
      if (pointers.size === 2) {
        // Второй палец — щипок: прерываем начатое (создание/перемещение уже применено, фиксируем).
        endGesture(true);
        var ps = Array.from(pointers.values());
        startGesture({
          kind: "pinch", rect: rect, d0: Math.max(1, G.dist(ps[0], ps[1])), z0: view.z,
          w0: toWorld((ps[0].x + ps[1].x) / 2, (ps[0].y + ps[1].y) / 2, rect),
        });
        return;
      }
      if (pointers.size > 2) return;
      var p = toWorld(e.clientX, e.clientY, rect);
      var base = { rect: rect, sx: e.clientX, sy: e.clientY, p0: p, moved: false, pointerId: e.pointerId, pointerType: e.pointerType };
      if (e.button === 1 || spaceDown || tool === "hand" || (readOnly && e.pointerType !== "mouse")) {
        e.preventDefault();
        startGesture(Object.assign(base, { kind: "pan", vx: view.x, vy: view.y }));
        return;
      }
      if (readOnly) {
        startGesture(Object.assign(base, { kind: "pan", vx: view.x, vy: view.y }));
        return;
      }
      var hEl = e.target.closest ? e.target.closest("[data-h]") : null;
      if (hEl && tool === "select") {
        e.preventDefault();
        startHandle(hEl.getAttribute("data-h"), base, e);
        return;
      }
      if (tool === "rect" || tool === "diamond" || tool === "ellipse" || tool === "sticky" || tool === "text") {
        e.preventDefault();
        begin();
        var el = newEl(tool, p, { w: 1, h: 1 });
        if (tool === "text") el.h = DEFAULTS.text.h;
        put(el);
        startGesture(Object.assign(base, { kind: "create", id: el.id, type: tool }));
        return;
      }
      if (tool === "line" || tool === "arrow") {
        e.preventDefault();
        var sn = snapEnd(p, null, e.altKey);
        begin();
        var ln = newEl("line", G.pt(0, 0), {
          points: [[p.x, p.y], [p.x, p.y]],
          route: lastStyle.lineRoute || "straight",
        });
        if (sn.end) ln.start = sn.end;
        if (tool === "arrow") ln.arrowEnd = true;
        put(ln);
        startGesture(Object.assign(base, { kind: "end", id: ln.id, which: "p1", created: true }));
        return;
      }
      if (tool === "pen") {
        e.preventDefault();
        startGesture(Object.assign(base, { kind: "pen", pts: [p], live: null }));
        drawPenPreview();
        return;
      }
      // Подпись стрелки тянется вдоль линии; щелчок — как по самой стрелке (двойной — правка текста).
      var lbl = tool === "select" && e.target.closest ? e.target.closest(".lb-line-label") : null;
      var lblId = lbl ? hitId(lbl) : "";
      if (lblId && !(e.shiftKey || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (!selected.has(lblId) || selected.size !== 1) setSelection([lblId]);
        begin();
        startGesture(Object.assign(base, { kind: "label", id: lblId }));
        return;
      }
      var id = hitId(e.target);
      if (id) {
        e.preventDefault();
        var ids;
        if (e.shiftKey || e.metaKey || e.ctrlKey) {
          var grp = expandGroups([id]);
          var nextSel = new Set(selected);
          var allIn = Array.from(grp).every(function (x) {
            return nextSel.has(x);
          });
          // Уже выделенный: снимем выделение только по щелчку без движения — с Shift его можно тащить.
          if (!allIn) {
            grp.forEach(function (x) {
              nextSel.add(x);
            });
            selected = nextSel;
            schedule();
          } else {
            base.toggleOff = Array.from(grp);
          }
        } else if (!selected.has(id)) {
          setSelection([id]);
        }
        ids = Array.from(selected);
        startMove(base, ids, id);
        return;
      }
      // Пустое место: мышью — рамка выделения, пальцем — панорама.
      if (e.pointerType === "touch") {
        startGesture(Object.assign(base, { kind: "pan", vx: view.x, vy: view.y, tapEmpty: true }));
        return;
      }
      if (!(e.shiftKey || e.metaKey || e.ctrlKey)) selected.clear();
      startGesture(Object.assign(base, { kind: "marquee", keep: new Set(selected) }));
      schedule();
    }

    function startMove(base, ids, hit) {
      var list = ids.map(getEl).filter(Boolean);
      begin();
      var moving = {};
      list.forEach(function (el) {
        moving[el.id] = 1;
      });
      // Стрелки с изломами между двумя переносимыми блоками едут целиком.
      var carry = [];
      els.forEach(function (el) {
        if (el.type !== "line" || moving[el.id] || !(el.waypoints && el.waypoints.length)) return;
        if (el.start && moving[el.start.el] && el.end && moving[el.end.el]) carry.push(clone(el));
      });
      startGesture(Object.assign(base, {
        kind: "move", hit: hit, items: list.map(clone), moving: moving, carry: carry,
        bounds: G.unionBounds(list.map(function (el) {
          return G.elBounds(el, getEl);
        })),
      }));
    }

    function startHandle(h, base, e) {
      var single = selList().length === 1 ? selList()[0] : null;
      if (h.indexOf("c-") === 0 && single) {
        var side = h.slice(2);
        startGesture(Object.assign(base, { kind: "connect", from: single.id, side: side }));
        return;
      }
      if (/^(wp|ins|seg):\d+$/.test(h) && single && single.type === "line") {
        startBendHandle(h, base, single);
        return;
      }
      if ((h === "p0" || h === "p1" || h === "mid") && single && single.type === "line") {
        begin();
        startGesture(Object.assign(base, { kind: h === "mid" ? "bend" : "end", id: single.id, which: h, res: G.resolveLine(single, getEl) }));
        return;
      }
      if (h === "rot" && single) {
        begin();
        startGesture(Object.assign(base, { kind: "rotate", id: single.id, el0: clone(single) }));
        return;
      }
      if (h.charAt(0) === "g") {
        var list = selList();
        begin();
        startGesture(Object.assign(base, {
          kind: "gscale", h: h.slice(1), items: list.map(clone),
          bounds: G.unionBounds(list.map(function (el) {
            return G.elBounds(el, getEl);
          })),
        }));
        return;
      }
      if (single) {
        begin();
        startGesture(Object.assign(base, { kind: "resize", id: single.id, h: h, el0: clone(single), shift: e.shiftKey }));
      }
    }

    function wpArr(q) {
      return [Math.round(q.x * 10) / 10, Math.round(q.y * 10) / 10];
    }

    /** Ручки изломов: точка (wp), вставка новой точки (ins), отрезок ломаной (seg). */
    function startBendHandle(h, base, ln) {
      var kind = h.split(":")[0];
      var n = Number(h.split(":")[1]);
      var res = G.resolveLine(ln, getEl);
      var before = ln.waypoints;
      if (kind === "wp") {
        var now = Date.now();
        if (lastWpTap.h === h && lastWpTap.id === ln.id && now - lastWpTap.t < 380) {
          // Двойное нажатие по точке — убрать её.
          lastWpTap = { h: "", id: "", t: 0 };
          var rest = G.waypointsOf(ln).filter(function (_, i) {
            return i !== n;
          }).map(wpArr);
          begin();
          patch(ln.id, { waypoints: rest.length ? rest : undefined });
          commit();
          return;
        }
        lastWpTap = { h: h, id: ln.id, t: now };
        begin();
        startGesture(Object.assign(base, { kind: "wp", id: ln.id, idx: n, before: before }));
        return;
      }
      if (kind === "ins") {
        var had = G.waypointsOf(ln).length;
        var wps = G.materializeWaypoints(ln, res, pathOptsOf(ln)).map(wpArr);
        // Изгиб старого вида превращается в одну точку — её и тянем.
        var fromBend = !had && wps.length === 1;
        if (!fromBend) wps.splice(n, 0, wpArr(base.p0));
        begin();
        patch(ln.id, { waypoints: wps, bend: undefined });
        startGesture(Object.assign(base, { kind: "wp", id: ln.id, idx: fromBend ? 0 : n, before: before, inserted: true, bend0: ln.bend }));
        return;
      }
      // seg: отрезок ломаной двигается поперёк себя, соседние углы — за ним.
      var Q = linePathOf(ln, res).poly.map(function (q) {
        return G.pt(q.x, q.y);
      });
      var i = n;
      if (i < 0 || i > Q.length - 2) return;
      if (i === 0) {
        // Крайний отрезок прилип к концу: оставляем у конца короткий отросток и тянем остальное.
        var l0 = G.dist(Q[0], Q[1]);
        var st = G.add(Q[0], G.mul(G.norm(G.sub(Q[1], Q[0])), Math.min(G.STUB, l0 / 2)));
        Q.splice(1, 0, st, G.pt(st.x, st.y));
        i = 2;
      }
      if (i === Q.length - 2) {
        var m = Q.length - 1;
        var l1 = G.dist(Q[m - 1], Q[m]);
        var et = G.sub(Q[m], G.mul(G.norm(G.sub(Q[m], Q[m - 1])), Math.min(G.STUB, l1 / 2)));
        Q.splice(m, 0, et, G.pt(et.x, et.y));
      }
      var horiz = Math.abs(Q[i + 1].x - Q[i].x) >= Math.abs(Q[i + 1].y - Q[i].y);
      begin();
      startGesture(Object.assign(base, {
        kind: "seg", id: ln.id, idx: i, poly: Q, horiz: horiz, before: before, bend0: ln.bend,
        c0: horiz ? Q[i].y : Q[i].x,
      }));
    }

    function doWaypoint(g, p, shift) {
      var ln = getEl(g.id);
      if (!ln) return;
      var res = G.resolveLine(ln, getEl);
      var wps = G.waypointsOf(ln);
      var pass = [res.p0].concat(wps, [res.p1]);
      var prev = pass[g.idx];
      var next = pass[g.idx + 2];
      if (shift && prev) {
        // Shift — угол от предыдущей точки с шагом 15°.
        var v = G.sub(p, prev);
        var step = Math.PI / 12;
        var ang = Math.round(Math.atan2(v.y, v.x) / step) * step;
        p = G.add(prev, G.pt(Math.cos(ang) * G.len(v), Math.sin(ang) * G.len(v)));
      } else {
        // Прилипание к соседним точкам по осям — чтобы легко поставить ровно.
        var th = 6 / view.z;
        var x = p.x;
        var y = p.y;
        [prev, next].forEach(function (q) {
          if (!q) return;
          if (Math.abs(p.x - q.x) < th) x = q.x;
          if (Math.abs(p.y - q.y) < th) y = q.y;
        });
        if (showGrid && x === p.x) x = Math.abs(p.x - Math.round(p.x / GRID) * GRID) < th ? Math.round(p.x / GRID) * GRID : x;
        if (showGrid && y === p.y) y = Math.abs(p.y - Math.round(p.y / GRID) * GRID) < th ? Math.round(p.y / GRID) * GRID : y;
        p = G.pt(x, y);
      }
      var arr = wps.map(wpArr);
      arr[g.idx] = wpArr(p);
      patch(g.id, { waypoints: arr });
    }

    function doLabel(g, p) {
      var ln = getEl(g.id);
      if (!ln) return;
      var t = G.polyProjectT(linePathOf(ln).poly, p);
      t = Math.max(0.03, Math.min(0.97, t));
      // Около середины прилипает к ней — вернуть подпись на место легко.
      patch(g.id, { labelT: Math.abs(t - 0.5) < 0.03 ? undefined : Math.round(t * 1000) / 1000 });
    }

    function doSegment(g, p) {
      var ln = getEl(g.id);
      if (!ln) return;
      var c = g.horiz ? p.y : p.x;
      var th = 6 / view.z;
      var res = G.resolveLine(ln, getEl);
      // Прилипание к концам — чтобы вытянуть ломаную в прямую.
      [res.p0, res.p1].forEach(function (q) {
        var qc = g.horiz ? q.y : q.x;
        if (Math.abs(c - qc) < th) c = qc;
      });
      if (showGrid) {
        var gc = Math.round(c / GRID) * GRID;
        if (Math.abs(gc - c) < th) c = gc;
      }
      var Q = g.poly.map(function (q) {
        return G.pt(q.x, q.y);
      });
      [g.idx, g.idx + 1].forEach(function (k) {
        if (g.horiz) Q[k].y = c;
        else Q[k].x = c;
      });
      patch(g.id, { waypoints: Q.slice(1, -1).map(wpArr), bend: undefined });
    }

    /** После правки изломов: лишние точки на прямых участках убираем. */
    function tidyWaypoints(id) {
      var ln = getEl(id);
      if (!ln) return;
      var wps = G.waypointsOf(ln);
      if (!wps.length) return;
      if ((ln.route || "straight") === "curve") return;
      var res = G.resolveLine(ln, getEl);
      var clean = G.cleanPoly([res.p0].concat(wps, [res.p1])).slice(1, -1);
      if (clean.length === wps.length) return;
      patch(id, { waypoints: clean.length ? clean.map(wpArr) : undefined });
    }

    function onPointerMove(e) {
      if (!pointers.has(e.pointerId)) {
        if (o.onCursor && !readOnly) {
          var pc = toWorld(e.clientX, e.clientY);
          o.onCursor(pc.x, pc.y);
        }
        return;
      }
      pointers.set(e.pointerId, G.pt(e.clientX, e.clientY));
      if (o.onCursor && !readOnly) {
        var pw = toWorld(e.clientX, e.clientY);
        o.onCursor(pw.x, pw.y);
      }
      var g = gesture;
      if (!g) return;
      if (g.kind === "pinch") {
        var ps = Array.from(pointers.values());
        if (ps.length < 2) return;
        var d = Math.max(1, G.dist(ps[0], ps[1]));
        var z = clamp(g.z0 * (d / g.d0), MIN_Z, MAX_Z);
        var mx = (ps[0].x + ps[1].x) / 2 - g.rect.left;
        var my = (ps[0].y + ps[1].y) / 2 - g.rect.top;
        view.z = z;
        view.x = mx - g.w0.x * z;
        view.y = my - g.w0.y * z;
        applyView();
        return;
      }
      if (e.pointerId !== g.pointerId) return;
      var dxs = e.clientX - g.sx;
      var dys = e.clientY - g.sy;
      if (!g.moved && Math.abs(dxs) + Math.abs(dys) < (e.pointerType === "touch" ? 6 : 3)) return;
      g.moved = true;
      var p = toWorld(e.clientX, e.clientY, g.rect);
      if (g.kind === "pan") {
        view.x = g.vx + dxs;
        view.y = g.vy + dys;
        applyView();
      } else if (g.kind === "marquee") {
        var a = g.p0;
        marquee = { x: Math.min(a.x, p.x), y: Math.min(a.y, p.y), w: Math.abs(p.x - a.x), h: Math.abs(p.y - a.y) };
        var hit = new Set(g.keep);
        els.forEach(function (el) {
          var b = G.elBounds(el, getEl);
          if (b && G.intersects(b, marquee)) hit.add(el.id);
        });
        selected = expandGroups(Array.from(hit));
        schedule();
      } else if (g.kind === "move") {
        doMove(g, p, e);
      } else if (g.kind === "create") {
        doCreate(g, p, e.shiftKey);
      } else if (g.kind === "end") {
        doEnd(g, p, e.altKey, e.shiftKey);
      } else if (g.kind === "label") {
        doLabel(g, p);
      } else if (g.kind === "wp") {
        doWaypoint(g, p, e.shiftKey);
      } else if (g.kind === "seg") {
        doSegment(g, p);
      } else if (g.kind === "bend") {
        var ln = getEl(g.id);
        if (ln) patch(g.id, { bend: Math.round(G.bendFromPoint(ln, G.resolveLine(ln, getEl), p)) });
      } else if (g.kind === "rotate") {
        var c = G.center(g.el0);
        var ang = (Math.atan2(p.y - c.y, p.x - c.x) * 180) / Math.PI + 90;
        if (e.shiftKey) ang = Math.round(ang / 15) * 15;
        else {
          var near = Math.round(ang / 90) * 90;
          if (Math.abs(ang - near) < 4) ang = near;
        }
        ang = ((ang % 360) + 360) % 360;
        if (ang > 180) ang -= 360;
        patch(g.id, { rot: Math.round(ang * 10) / 10 || undefined });
      } else if (g.kind === "resize") {
        doResize(g, p, e.shiftKey);
      } else if (g.kind === "gscale") {
        doGroupScale(g, p);
      } else if (g.kind === "pen") {
        var last = g.pts[g.pts.length - 1];
        if (G.dist(last, p) * view.z >= 1.5) {
          g.pts.push(p);
          drawPenPreview();
        }
      } else if (g.kind === "connect") {
        // Тянем от «плюса»: появляется стрелка от стороны блока.
        begin();
        var from = getEl(g.from);
        if (!from) return;
        var sp = G.sidePoint(from, g.side);
        var ln2 = newEl("line", G.pt(0, 0), {
          points: [[sp.x, sp.y], [p.x, p.y]], start: { el: from.id, side: g.side }, arrowEnd: true,
          route: lastStyle.lineRoute || "curve",
        });
        put(ln2);
        gesture = Object.assign(g, { kind: "end", id: ln2.id, which: "p1", created: true });
        doEnd(gesture, p, e.altKey, e.shiftKey);
      }
    }

    /** Option/Alt при перетаскивании — тянем копию, оригинал остаётся на месте (как в Miro). */
    function duplicateDragged(g) {
      g.items.forEach(function (el0) {
        put(clone(el0)); // оригиналы — на исходные места
      });
      var copies = copyEls(g.items, 0, 0);
      copies.forEach(put);
      g.items = copies.map(clone);
      g.moving = {};
      copies.forEach(function (c) {
        g.moving[c.id] = 1;
      });
      g.dup = true;
      setSelection(copies.map(function (c) {
        return c.id;
      }));
    }

    function doMove(g, p, e) {
      if (e.altKey && !g.dup) duplicateDragged(g);
      var dx = p.x - g.p0.x;
      var dy = p.y - g.p0.y;
      // Shift — только по горизонтали или только по вертикали (куда сдвинули больше).
      var lock = e.shiftKey ? (Math.abs(dx) >= Math.abs(dy) ? "x" : "y") : "";
      if (lock === "x") dy = 0;
      if (lock === "y") dx = 0;
      // Cmd/Ctrl — без привязки к сетке и направляющим.
      var free = e.metaKey || e.ctrlKey;
      guides = [];
      if (g.bounds && !free) {
        var b = g.bounds;
        // Направляющие важнее сетки: иначе блоки разной ширины не встают центр к центру
        // (сетка двигает край с шагом 20, центры всегда расходятся на несколько пикселей).
        var pre = alignGuides(g, b, dx, dy);
        guides = [];
        if (pre.snapX && lock !== "y") dx = pre.dx;
        else if (showGrid && lock !== "y") dx = Math.round((b.x + dx) / GRID) * GRID - b.x;
        if (pre.snapY && lock !== "x") dy = pre.dy;
        else if (showGrid && lock !== "x") dy = Math.round((b.y + dy) / GRID) * GRID - b.y;
        var adj = alignGuides(g, b, dx, dy);
        if (lock !== "y") dx = adj.dx;
        if (lock !== "x") dy = adj.dy;
        if (lock) {
          guides = guides.filter(function (gd) {
            // Направляющая по зафиксированной оси не действует — не рисуем её.
            return lock === "x" ? gd.a.x === gd.b.x : gd.a.y === gd.b.y;
          });
        }
      }
      g.items.forEach(function (el0) {
        var f = moved(el0, dx, dy, g.moving);
        if (el0.type === "line") {
          ["start", "end"].forEach(function (k) {
            if (el0[k] && g.moving[el0[k].el]) f[k] = el0[k];
          });
        }
        var cur = getEl(el0.id);
        if (cur) put(Object.assign({}, cur, f, el0.type === "line" ? {} : { x: el0.x + dx, y: el0.y + dy }));
      });
      (g.carry || []).forEach(function (ln0) {
        if (getEl(ln0.id)) patch(ln0.id, { waypoints: shiftWaypoints(ln0.waypoints, dx, dy) });
      });
    }

    /** Направляющие: края и центры выделения прилипают к краям и центрам соседних блоков. */
    function alignGuides(g, b, dx, dy) {
      var th = 6 / view.z;
      var xs = [b.x + dx, b.x + dx + b.w / 2, b.x + dx + b.w];
      var ys = [b.y + dy, b.y + dy + b.h / 2, b.y + dy + b.h];
      var bestX = null;
      var bestY = null;
      // Блоки, связанные стрелкой с переносимыми: центр к центру тянет сильнее и в первую очередь —
      // тогда стрелка между ними встаёт ровно.
      var linked = g.linked || (g.linked = linkedTo(g.moving));
      var thL = 14 / view.z;
      function better(cur, d, prio) {
        return !cur || prio > cur.prio || (prio === cur.prio && Math.abs(d) < Math.abs(cur.d));
      }
      els.forEach(function (el) {
        if (g.moving[el.id] || !BOXED[el.type]) return;
        var ob = G.elBounds(el, getEl);
        if (!ob) return;
        var oxs = [ob.x, ob.x + ob.w / 2, ob.x + ob.w];
        var oys = [ob.y, ob.y + ob.h / 2, ob.y + ob.h];
        var isLinked = !!linked[el.id];
        xs.forEach(function (x, i) {
          oxs.forEach(function (ox, j) {
            var d = ox - x;
            var prio = isLinked && i === 1 && j === 1 ? 1 : 0;
            if (Math.abs(d) <= (prio ? thL : th) && better(bestX, d, prio)) bestX = { d: d, x: ox, ob: ob, prio: prio };
          });
        });
        ys.forEach(function (y, i) {
          oys.forEach(function (oy, j) {
            var d = oy - y;
            var prio = isLinked && i === 1 && j === 1 ? 1 : 0;
            if (Math.abs(d) <= (prio ? thL : th) && better(bestY, d, prio)) bestY = { d: d, y: oy, ob: ob, prio: prio };
          });
        });
      });
      if (bestX) {
        dx += bestX.d;
        var y0 = Math.min(bestX.ob.y, b.y + dy);
        var y1 = Math.max(bestX.ob.y + bestX.ob.h, b.y + dy + b.h);
        guides.push({ a: G.pt(bestX.x, y0), b: G.pt(bestX.x, y1) });
      }
      if (bestY) {
        dy += bestY.d;
        var x0 = Math.min(bestY.ob.x, b.x + dx);
        var x1 = Math.max(bestY.ob.x + bestY.ob.w, b.x + dx + b.w);
        guides.push({ a: G.pt(x0, bestY.y), b: G.pt(x1, bestY.y) });
      }
      return { dx: dx, dy: dy, snapX: !!bestX, snapY: !!bestY };
    }

    /** Блоки на другом конце стрелок от переносимых (сами переносимые не в счёт). */
    function linkedTo(moving) {
      var out = {};
      els.forEach(function (el) {
        if (el.type !== "line") return;
        var a = el.start && el.start.el;
        var b = el.end && el.end.el;
        if (a && b && moving[a] && !moving[b]) out[b] = 1;
        if (a && b && moving[b] && !moving[a]) out[a] = 1;
      });
      return out;
    }

    function doCreate(g, p, square) {
      var el = getEl(g.id);
      if (!el) return;
      var a = g.p0;
      var w = Math.abs(p.x - a.x);
      var h = Math.abs(p.y - a.y);
      if (square || g.type === "sticky") {
        var s = Math.max(w, h);
        w = s;
        h = s;
      }
      var x = p.x < a.x ? a.x - w : a.x;
      var y = p.y < a.y ? a.y - h : a.y;
      if (g.type === "text") {
        put(Object.assign({}, el, { x: x, y: a.y, w: Math.max(40, w) }));
        return;
      }
      put(Object.assign({}, el, { x: x, y: y, w: Math.max(4, w), h: Math.max(4, h) }));
    }

    function doEnd(g, p, free, shift) {
      var ln = getEl(g.id);
      if (!ln) return;
      var key = g.which === "p0" ? "start" : "end";
      var other = g.which === "p0" ? "end" : "start";
      if (shift) {
        // Shift — угол линии с шагом 15°, конец свободный (прилипание сбило бы угол).
        var r0 = G.resolveLine(ln, getEl);
        var o = g.which === "p0" ? r0.p1 : r0.p0;
        var v = G.sub(p, o);
        var len = G.len(v);
        var step = Math.PI / 12;
        var ang = Math.round(Math.atan2(v.y, v.x) / step) * step;
        p = G.add(o, G.pt(Math.cos(ang) * len, Math.sin(ang) * len));
        free = true;
      }
      var exclude = {};
      exclude[ln.id] = 1;
      var sn = snapEnd(p, exclude, free);
      // Обе стороны к одной фигуре «к объекту» — бессмысленная стрелка: тогда только к стороне.
      if (sn.end && ln[other] && ln[other].el === sn.end.el && !sn.end.side) sn = { end: null, el: null };
      snapTarget = sn.el ? { el: sn.el, side: sn.side } : null;
      var res = G.resolveLine(ln, getEl);
      var pts = g.which === "p0" ? [[p.x, p.y], [res.p1.x, res.p1.y]] : [[res.p0.x, res.p0.y], [p.x, p.y]];
      var f = { points: pts };
      f[key] = sn.end || undefined;
      put(Object.assign({}, ln, f));
    }

    var RESIZE = {
      nw: [-1, -1], n: [0, -1], ne: [1, -1], e: [1, 0], se: [1, 1], s: [0, 1], sw: [-1, 1], w: [-1, 0],
    };

    function doResize(g, p, keepRatio) {
      var el0 = g.el0;
      var dir = RESIZE[g.h];
      if (!dir) return;
      var lp = G.toLocal(el0, p);
      var x0 = 0;
      var y0 = 0;
      var x1 = el0.w;
      var y1 = el0.h;
      if (dir[0] < 0) x0 = Math.min(lp.x, el0.w - 8);
      if (dir[0] > 0) x1 = Math.max(lp.x, 8);
      if (dir[1] < 0) y0 = Math.min(lp.y, el0.h - 8);
      if (dir[1] > 0) y1 = Math.max(lp.y, 8);
      var w = x1 - x0;
      var h = y1 - y0;
      var ratio = el0.type === "image" || el0.type === "sticky" ? !keepRatio : keepRatio;
      if (ratio && dir[0] && dir[1] && el0.w > 0 && el0.h > 0) {
        var k = Math.max(w / el0.w, h / el0.h);
        w = el0.w * k;
        h = el0.h * k;
        if (dir[0] < 0) x0 = el0.w - w;
        if (dir[1] < 0) y0 = el0.h - h;
      }
      if (el0.type === "text") {
        h = el0.h;
        y0 = 0;
      }
      var cLocal = G.pt(x0 + w / 2, y0 + h / 2);
      var cWorld = G.toWorld(el0, cLocal);
      var f = { x: cWorld.x - w / 2, y: cWorld.y - h / 2, w: w, h: h };
      if (el0.type === "text" && dir[0] && dir[1]) {
        // Угол текстового блока — масштаб шрифта.
        var fs = (el0.style && el0.style.fontSize) || 18;
        f.style = { fontSize: Math.max(8, Math.round(fs * (w / Math.max(1, el0.w)))) };
      }
      patch(g.id, f);
    }

    function doGroupScale(g, p) {
      var b = g.bounds;
      if (!b || b.w <= 0 || b.h <= 0) return;
      var ax = g.h.indexOf("w") >= 0 ? b.x + b.w : b.x;
      var ay = g.h.indexOf("n") >= 0 ? b.y + b.h : b.y;
      var k = Math.max(0.05, Math.max(Math.abs(p.x - ax) / b.w, Math.abs(p.y - ay) / b.h));
      g.items.forEach(function (el0) {
        var cur = getEl(el0.id);
        if (!cur) return;
        var sx = function (x) {
          return ax + (x - ax) * k;
        };
        var sy = function (y) {
          return ay + (y - ay) * k;
        };
        var f;
        if (el0.type === "line") {
          var res = G.resolveLine(el0, getEl);
          f = { points: [[sx(res.p0.x), sy(res.p0.y)], [sx(res.p1.x), sy(res.p1.y)]], bend: (el0.bend || 0) * k || undefined };
          if (el0.waypoints && el0.waypoints.length) {
            f.waypoints = el0.waypoints.map(function (q) {
              return [Math.round(sx(q[0]) * 10) / 10, Math.round(sy(q[1]) * 10) / 10];
            });
          }
        } else if (el0.type === "pen") {
          f = {
            x: sx(el0.x), y: sy(el0.y),
            points: (el0.points || []).map(function (q) {
              return [q[0] * k, q[1] * k];
            }),
          };
        } else {
          f = { x: sx(el0.x), y: sy(el0.y), w: el0.w * k, h: el0.h * k };
          if (TEXTUAL[el0.type] && el0.style && el0.style.fontSize) f.style = Object.assign({}, el0.style, { fontSize: Math.max(6, Math.round(el0.style.fontSize * k)) });
        }
        put(Object.assign({}, cur, f));
      });
    }

    var penPreview = null;
    function drawPenPreview() {
      var g = gesture;
      if (!g || g.kind !== "pen") return;
      if (!penPreview) {
        penPreview = document.createElement("div");
        penPreview.className = "lb-el lb-t-pen lb-pen-live";
        world.appendChild(penPreview);
      }
      var st = styleFor("pen");
      penPreview.innerHTML =
        '<svg class="lb-shape" width="1" height="1" overflow="visible"><path d="' + G.smoothPath(g.pts) +
        '" fill="none" stroke="' + inkVar(st.stroke) + '" stroke-width="' + st.strokeWidth + '" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }

    function onPointerUp(e) {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      var g = gesture;
      if (!g) return;
      if (g.kind === "pinch") {
        if (pointers.size < 2) {
          gesture = null;
          // Оставшийся палец — продолжаем панораму с текущего места.
          if (pointers.size === 1) {
            var rest = Array.from(pointers.entries())[0];
            gesture = { kind: "pan", rect: stageRect(), sx: rest[1].x, sy: rest[1].y, vx: view.x, vy: view.y, moved: true, pointerId: rest[0] };
          }
          schedule();
        }
        return;
      }
      if (e.pointerId !== g.pointerId) return;
      var p = toWorld(e.clientX, e.clientY, g.rect);
      endGesture(false, p, e);
    }

    function endGesture(cancelled, p, e) {
      var g = gesture;
      if (!g) return;
      gesture = null;
      guides = [];
      snapTarget = null;
      marquee = null;
      if (penPreview) {
        penPreview.remove();
        penPreview = null;
      }
      if (g.kind === "create") {
        var el = getEl(g.id);
        if (el && !g.moved) {
          var d = DEFAULTS[g.type];
          put(Object.assign({}, el, { x: g.p0.x - d.w / 2, y: g.p0.y - (g.type === "text" ? d.h / 2 : d.h / 2), w: d.w, h: d.h }));
        } else if (el && (el.w < 12 || el.h < 12) && g.type !== "text") {
          var d2 = DEFAULTS[g.type];
          put(Object.assign({}, el, { w: Math.max(el.w, d2.w / 3), h: Math.max(el.h, d2.h / 3) }));
        }
        commit();
        setTool("select");
        if (el) {
          setSelection([g.id]);
          if (!cancelled && (g.type === "text" || g.type === "sticky" || SHAPES[g.type])) {
            // Новый блок сразу готов к вводу текста — без лишнего нажатия.
            startEdit(g.id, { isNew: true });
          }
        }
      } else if (g.kind === "end") {
        var ln = getEl(g.id);
        if (ln && g.created && !g.moved) {
          // Клик линией — отрезок по умолчанию вправо.
          var a = g.p0;
          put(Object.assign({}, ln, { points: [[a.x, a.y], [a.x + 160, a.y]] }));
        } else if (ln) {
          var res = G.resolveLine(ln, getEl);
          if (G.dist(res.p0, res.p1) < 4 && !ln.start && !ln.end) remove(ln.id);
        }
        commit();
        if (g.created) setTool("select");
        if (getEl(g.id)) setSelection([g.id]);
      } else if (g.kind === "pen") {
        var pts = G.simplify(g.pts, 0.8 / view.z);
        if (pts.length >= 2 || (g.pts.length && !cancelled)) {
          if (pts.length < 2) pts = [g.pts[0], G.add(g.pts[0], G.pt(0.1, 0))];
          var bb = G.boundsOf(pts);
          begin();
          var pen = newEl("pen", G.pt(bb.x, bb.y), {
            w: bb.w, h: bb.h,
            points: pts.map(function (q) {
              return [Math.round((q.x - bb.x) * 10) / 10, Math.round((q.y - bb.y) * 10) / 10];
            }),
          });
          put(pen);
          commit();
        }
      } else if (g.kind === "connect") {
        if (!g.moved && !cancelled) quickAdd(g.from, g.side);
      } else if (g.kind === "label") {
        commit();
        if (!g.moved && e && !cancelled) onTap(g.id, p, e);
      } else if (g.kind === "wp" || g.kind === "seg") {
        // Нажали, но не потянули — изломы как были.
        if (!g.moved && g.inserted) patch(g.id, { waypoints: g.before, bend: g.bend0 });
        else tidyWaypoints(g.id);
        commit();
      } else if (g.kind === "move" || g.kind === "resize" || g.kind === "rotate" || g.kind === "bend" || g.kind === "gscale") {
        commit();
        if (g.kind === "move" && !g.moved && e && !cancelled) {
          if (g.toggleOff) {
            g.toggleOff.forEach(function (x) {
              selected.delete(x);
            });
          } else onTap(g.hit, p, e);
        }
      } else if (g.kind === "pan") {
        if (g.tapEmpty && !g.moved) {
          selected.clear();
          onTap("", p, e);
        }
      } else if (g.kind === "marquee") {
        if (!g.moved && e) onTap("", p, e);
      }
      schedule();
    }

    /** Двойное нажатие: по элементу — правка текста, по пустому месту — новый текст. */
    function onTap(id, p, e) {
      var now = Date.now();
      var dbl = now - lastTap.t < 380 && lastTap.id === id && Math.abs(e.clientX - lastTap.x) < 12 && Math.abs(e.clientY - lastTap.y) < 12;
      lastTap = { id: id, t: dbl ? 0 : now, x: e.clientX, y: e.clientY };
      if (!dbl || readOnly) return;
      if (id) {
        var el = getEl(id);
        if (el && TEXTUAL[el.type]) startEdit(id, { selectAll: false });
        return;
      }
      begin();
      var t = newEl("text", G.pt(p.x - 8, p.y - DEFAULTS.text.h / 2));
      put(t);
      commit();
      startEdit(t.id, { isNew: true });
    }

    /** «Плюс» у блока: копия блока сбоку, связанная стрелкой. */
    function quickAdd(fromId, side) {
      var from = getEl(fromId);
      if (!from) return;
      var gap = 90;
      var n = G.sideNormal(from, side);
      var horiz = side === "left" || side === "right";
      var dist = (horiz ? from.w : from.h) + gap;
      var c = G.add(G.center(from), G.mul(n, dist));
      var opp = { top: "bottom", bottom: "top", left: "right", right: "left" }[side];
      begin();
      var copy = {
        id: uid(), type: from.type === "text" || from.type === "image" ? "rect" : from.type,
        x: c.x - from.w / 2, y: c.y - from.h / 2, w: from.w, h: from.h, z: topZ(),
        style: clone(from.style || styleFor("rect")),
      };
      if (from.rot) copy.rot = from.rot;
      if (copy.type === "sticky" && meAuthor()) copy.author = meAuthor();
      if (from.type === "text" || from.type === "image") {
        copy.w = DEFAULTS.rect.w;
        copy.h = DEFAULTS.rect.h;
        copy.x = c.x - copy.w / 2;
        copy.y = c.y - copy.h / 2;
        copy.style = styleFor("rect");
      }
      put(copy);
      put(newEl("line", G.pt(0, 0), {
        points: [[0, 0], [1, 1]], start: { el: from.id, side: side }, end: { el: copy.id, side: opp },
        arrowEnd: true, route: lastStyle.lineRoute || "straight", z: topZ(),
      }));
      commit();
      setSelection([copy.id]);
      // Новый блок за краем экрана — подвинуть вид.
      var r = stage.getBoundingClientRect();
      var a = toScreen(G.pt(copy.x, copy.y));
      var b = toScreen(G.pt(copy.x + copy.w, copy.y + copy.h));
      if (a.x < 60 || a.y < 60 || b.x > r.width - 60 || b.y > r.height - 60) {
        var s = toScreen(G.center(copy));
        view.x += r.width / 2 - s.x;
        view.y += r.height / 2 - s.y;
        applyView();
      }
      startEdit(copy.id);
    }

    // ---- колесо и клавиши ----
    function onWheel(e) {
      e.preventDefault();
      autoFit = false;
      var r = stageRect();
      var k = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? r.height : 1;
      if (e.ctrlKey || e.metaKey) {
        // Щипок на трекпаде — мелкие шаги, колесо с Ctrl — крупные: ограничиваем шаг.
        var f = Math.exp(-clamp(e.deltaY * k, -40, 40) * 0.008);
        zoomAt(view.z * f, e.clientX - r.left, e.clientY - r.top);
      } else {
        view.x -= e.deltaX * k;
        view.y -= e.deltaY * k;
        applyView();
      }
    }

    function zoomAt(z, sx, sy) {
      z = clamp(z, MIN_Z, MAX_Z);
      var wx = (sx - view.x) / view.z;
      var wy = (sy - view.y) / view.z;
      view.z = z;
      view.x = sx - wx * z;
      view.y = sy - wy * z;
      applyView();
    }

    function zoomBy(f) {
      var r = stage.getBoundingClientRect();
      zoomAt(view.z * f, r.width / 2, r.height / 2);
    }

    function zoomTo(z) {
      var r = stage.getBoundingClientRect();
      zoomAt(z, r.width / 2, r.height / 2);
    }

    function contentBounds() {
      return G.unionBounds(Array.from(els.values()).map(function (el) {
        return G.elBounds(el, getEl);
      }));
    }

    function fitAll() {
      var r = stage.getBoundingClientRect();
      var b = contentBounds();
      if (!b || !r.width || !r.height) {
        view = { x: r.width / 2, y: r.height / 2, z: 1 };
        applyView();
        return;
      }
      var pad = 60;
      var z = clamp(Math.min((r.width - pad * 2) / Math.max(1, b.w), (r.height - pad * 2) / Math.max(1, b.h)), MIN_Z, 1);
      view.z = z;
      view.x = r.width / 2 - (b.x + b.w / 2) * z;
      view.y = r.height / 2 - (b.y + b.h / 2) * z;
      applyView();
    }

    function onKeyDown(e) {
      if (editing || destroyed) return;
      var t = e.target;
      if (t && t !== stage && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      var mod = e.metaKey || e.ctrlKey;
      var k = e.key;
      var handled = true;
      if (k === " " && !mod) {
        spaceDown = true;
        stage.classList.add("is-panning");
      } else if (mod && (k === "z" || k === "Z" || k === "я" || k === "Я")) {
        if (readOnly) return;
        if (e.shiftKey) redo();
        else undo();
      } else if (mod && (k === "y" || k === "н")) {
        if (!readOnly) redo();
      } else if (mod && (k === "a" || k === "ф")) {
        setSelection(Array.from(els.keys()));
      } else if (mod && (k === "d" || k === "в")) {
        if (!readOnly) duplicateSel();
      } else if (mod && (k === "g" || k === "п")) {
        if (readOnly) return;
        if (e.shiftKey) ungroupSel();
        else groupSel();
      } else if ((k === "Delete" || k === "Backspace") && !readOnly) {
        deleteSel();
      } else if (k === "Escape") {
        if (tool !== "select") setTool("select");
        else {
          selected.clear();
          schedule();
        }
        closePop();
      } else if (k === "Enter" && !readOnly && selected.size === 1) {
        startEdit(Array.from(selected)[0], { selectAll: true });
      } else if (/^Arrow/.test(k) && selected.size && !readOnly) {
        var step = e.shiftKey ? 10 : 1;
        nudge(k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0, k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0);
      } else if ((k === "+" || k === "=") && !mod) {
        zoomBy(1.25);
      } else if ((k === "-" || k === "_") && !mod) {
        zoomBy(0.8);
      } else if (k === "!" || (e.shiftKey && (e.code === "Digit1"))) {
        fitAll();
      } else if (e.shiftKey && e.code === "Digit0") {
        zoomTo(1);
      } else if (k === "]" && !readOnly) {
        reorderSel(true);
      } else if (k === "[" && !readOnly) {
        reorderSel(false);
      } else if (!mod && !e.altKey && k.length === 1 && !readOnly) {
        var byKey = TOOLS.find(function (x) {
          return x.key && (x.key === k.toLowerCase() || x.key === latinOf(e.code));
        });
        if (byKey && !(selected.size === 1 && e.shiftKey)) {
          setTool(byKey.id);
        } else if (selected.size === 1 && TEXTUAL[(getEl(Array.from(selected)[0]) || {}).type]) {
          // Набор по выделенному блоку — замена текста, как в Miro.
          startEdit(Array.from(selected)[0], { initial: k });
        } else handled = false;
      } else {
        handled = false;
      }
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    }

    function latinOf(code) {
      var m = /^Key([A-Z])$/.exec(code || "");
      return m ? m[1].toLowerCase() : "";
    }

    function onKeyUp(e) {
      if (e.key === " ") {
        spaceDown = false;
        stage.classList.remove("is-panning");
      }
    }

    function onDrop(e) {
      if (readOnly) return;
      var files = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.files) || []).filter(function (f) {
        return /^image\//i.test(f.type || "");
      });
      if (!files.length) return;
      e.preventDefault();
      var p = toWorld(e.clientX, e.clientY);
      files.slice(0, 6).reduce(function (chain, f, i) {
        return chain.then(function () {
          return insertImageFile(f, G.pt(p.x + i * 30, p.y + i * 30));
        });
      }, Promise.resolve());
    }

    // ---- мини-карта ----
    function scheduleMinimap() {
      if (mapTimer || destroyed) return;
      mapTimer = setTimeout(function () {
        mapTimer = 0;
        drawMinimap();
      }, 120);
    }

    var mapXform = null;
    function drawMinimap() {
      if (!showMap || destroyed) return;
      var r = stage.getBoundingClientRect();
      if (!r.width) return;
      var vw = { x: -view.x / view.z, y: -view.y / view.z, w: r.width / view.z, h: r.height / view.z };
      var cb = contentBounds();
      var b = G.unionBounds([cb, vw]);
      var cw = minimap.width;
      var ch = minimap.height;
      var pad = 6;
      var k = Math.min((cw - pad * 2) / Math.max(1, b.w), (ch - pad * 2) / Math.max(1, b.h));
      var ox = pad + ((cw - pad * 2) - b.w * k) / 2 - b.x * k;
      var oy = pad + ((ch - pad * 2) - b.h * k) / 2 - b.y * k;
      mapXform = { k: k, ox: ox, oy: oy };
      var ctx = minimap.getContext("2d");
      ctx.clearRect(0, 0, cw, ch);
      var cs = getComputedStyle(root);
      var ink = cs.getPropertyValue("--lb-map-ink").trim() || "#888";
      sortedEls().forEach(function (el) {
        var eb = G.elBounds(el, getEl);
        if (!eb) return;
        ctx.fillStyle = el.type === "sticky" ? cs.getPropertyValue("--lb-sticky-" + ((el.style && el.style.fill) || "yellow")).trim() || ink : ink;
        if (el.type === "line" || el.type === "pen") {
          ctx.strokeStyle = ink;
          ctx.lineWidth = 1;
          ctx.strokeRect(ox + eb.x * k, oy + eb.y * k, Math.max(1, eb.w * k), Math.max(1, eb.h * k));
        } else {
          ctx.globalAlpha = el.type === "sticky" ? 0.9 : 0.55;
          ctx.fillRect(ox + eb.x * k, oy + eb.y * k, Math.max(1.5, eb.w * k), Math.max(1.5, eb.h * k));
          ctx.globalAlpha = 1;
        }
      });
      ctx.strokeStyle = cs.getPropertyValue("--lb-accent").trim() || "#529ef4";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(ox + vw.x * k, oy + vw.y * k, vw.w * k, vw.h * k);
    }

    function onMapPointer(e) {
      if (!mapXform) return;
      autoFit = false;
      e.preventDefault();
      e.stopPropagation();
      var move = function (ev) {
        var mr = minimap.getBoundingClientRect();
        var mx = ((ev.clientX - mr.left) * minimap.width) / mr.width;
        var my = ((ev.clientY - mr.top) * minimap.height) / mr.height;
        var wx = (mx - mapXform.ox) / mapXform.k;
        var wy = (my - mapXform.oy) / mapXform.k;
        var r = stage.getBoundingClientRect();
        view.x = r.width / 2 - wx * view.z;
        view.y = r.height / 2 - wy * view.z;
        applyView();
      };
      move(e);
      var up = function () {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }

    // ---- экспорт ----
    /** Экспорт — в светлых цветах (файл открывают где угодно). */
    function exportColors() {
      var wasLight = root.classList.contains("is-light");
      root.classList.add("is-light");
      var cs = getComputedStyle(root);
      var snap = {};
      ["--lb-bg", "--lb-font"].concat(
        INK.concat(["white"]).map(function (k) {
          return "--lb-ink-" + k;
        }),
        FILLS.map(function (k) {
          return "--lb-fill-" + k;
        }),
        STICKY.map(function (k) {
          return "--lb-sticky-" + k;
        })
      ).forEach(function (n) {
        snap[n] = cs.getPropertyValue(n).trim();
      });
      if (!wasLight) root.classList.remove("is-light");
      cs = {
        getPropertyValue: function (n) {
          return snap[n] || "";
        },
      };
      var get = function (name, fb) {
        return cs.getPropertyValue(name).trim() || fb;
      };
      return {
        bg: get("--lb-bg", "#ffffff"),
        ink: function (k) {
          return get("--lb-ink-" + (k || "ink"), "#222");
        },
        fill: function (k) {
          return !k || k === "none" ? null : get("--lb-fill-" + k, "#fff");
        },
        sticky: function (k) {
          return get("--lb-sticky-" + (k || "yellow"), "#fff3a3");
        },
        font: get("--lb-font", "system-ui, sans-serif"),
      };
    }

    function wrapLines(ctx, text, maxW) {
      var out = [];
      String(text || "").split("\n").forEach(function (para) {
        var words = para.split(/(\s+)/);
        var line = "";
        words.forEach(function (w) {
          var test = line + w;
          if (line && ctx.measureText(test).width > maxW && w.trim()) {
            out.push(line.replace(/\s+$/, ""));
            line = w.replace(/^\s+/, "");
            while (ctx.measureText(line).width > maxW && line.length > 1) {
              var cut = line.length - 1;
              while (cut > 1 && ctx.measureText(line.slice(0, cut)).width > maxW) cut--;
              out.push(line.slice(0, cut));
              line = line.slice(cut);
            }
          } else line = test;
        });
        out.push(line);
      });
      return out;
    }

    /** Раскладка подписи: строки и их положение — общая для PNG и SVG. */
    function textLayout(ctx, el, colors) {
      if (!el.text || el.type === "image") return null;
      var st = el.style || {};
      var fs = Number(st.fontSize) || 16;
      ctx.font = (st.bold ? "600 " : "") + fs + "px " + colors.font;
      var lh = fs * 1.3;
      var padX = el.type === "text" ? 0 : el.type === "diamond" ? el.w * 0.22 : 10;
      var maxW;
      var cx;
      var top;
      var align;
      if (el.type === "line") {
        var lp = linePathOf(el);
        var lm = G.labelPoint(el, lp);
        maxW = 220;
        var lines0 = wrapLines(ctx, el.text, maxW);
        return { lines: lines0, x: lm.x, y: lm.y - ((lines0.length - 1) * lh) / 2, lh: lh, align: "center", fs: fs, label: true };
      }
      maxW = Math.max(10, el.w - padX * 2);
      var lines = wrapLines(ctx, el.text, maxW);
      align = alignOf(el);
      cx = align === "center" ? el.x + el.w / 2 : align === "right" ? el.x + el.w - padX : el.x + padX;
      if (el.type === "text") top = el.y + lh / 2;
      else top = el.y + el.h / 2 - ((lines.length - 1) * lh) / 2;
      return { lines: lines, x: cx, y: top, lh: lh, align: align, fs: fs };
    }

    function drawScene(ctx, colors, images) {
      sortedEls().forEach(function (el) {
        var st = el.style || {};
        var sw = Number(st.strokeWidth) || 2;
        ctx.save();
        if (el.rot && BOXED[el.type]) {
          var c = G.center(el);
          ctx.translate(c.x, c.y);
          ctx.rotate((el.rot * Math.PI) / 180);
          ctx.translate(-c.x, -c.y);
        }
        var dash = dashArray(st.dash, sw);
        ctx.setLineDash(dash ? dash.split(" ").map(Number) : []);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        if (el.type === "line" || el.type === "pen") {
          var d;
          var res = null;
          var lp = null;
          if (el.type === "line") {
            res = G.resolveLine(el, getEl);
            lp = linePathOf(el, res);
            d = lp.d;
          } else {
            d = G.smoothPath((el.points || []).map(function (q) {
              return G.pt(el.x + q[0], el.y + q[1]);
            }));
            sw = Number(st.strokeWidth) || 3;
          }
          ctx.strokeStyle = colors.ink(st.stroke);
          ctx.lineWidth = sw;
          ctx.stroke(new Path2D(d));
          if (lp) {
            ctx.setLineDash([]);
            ctx.fillStyle = colors.ink(st.stroke);
            var size = 8 + sw * 2.5;
            [[el.arrowEnd, res.p1, lp.endDir], [el.arrowStart, res.p0, lp.startDir]].forEach(function (a) {
              if (!a[0]) return;
              var pts = G.arrowHead(a[1], a[2], size);
              ctx.beginPath();
              ctx.moveTo(pts[0].x, pts[0].y);
              ctx.lineTo(pts[1].x, pts[1].y);
              ctx.lineTo(pts[2].x, pts[2].y);
              ctx.closePath();
              ctx.fill();
            });
          }
        } else if (el.type === "image") {
          var img = images[el.id];
          if (img) ctx.drawImage(img, el.x, el.y, el.w, el.h);
        } else {
          var path = new Path2D();
          if (el.type === "ellipse") path.ellipse(el.x + el.w / 2, el.y + el.h / 2, el.w / 2, el.h / 2, 0, 0, Math.PI * 2);
          else if (el.type === "diamond") {
            path.moveTo(el.x + el.w / 2, el.y);
            path.lineTo(el.x + el.w, el.y + el.h / 2);
            path.lineTo(el.x + el.w / 2, el.y + el.h);
            path.lineTo(el.x, el.y + el.h / 2);
            path.closePath();
          } else if (el.type === "rect" || el.type === "sticky") {
            var rr = el.type === "sticky" ? 3 : Math.min(10, el.w / 4, el.h / 4);
            if (path.roundRect) path.roundRect(el.x, el.y, el.w, el.h, rr);
            else path.rect(el.x, el.y, el.w, el.h);
          }
          if (el.type === "sticky") {
            ctx.shadowColor = "rgba(0,0,0,0.18)";
            ctx.shadowBlur = 8;
            ctx.shadowOffsetY = 2;
            ctx.fillStyle = colors.sticky(st.fill);
            ctx.fill(path);
            ctx.shadowColor = "transparent";
          } else if (el.type !== "text") {
            var fill = colors.fill(st.fill);
            if (fill) {
              ctx.fillStyle = fill;
              ctx.fill(path);
            }
            ctx.strokeStyle = colors.ink(st.stroke);
            ctx.lineWidth = sw;
            ctx.stroke(path);
          }
        }
        ctx.setLineDash([]);
        var tl = textLayout(ctx, el, colors);
        if (tl) {
          ctx.fillStyle = el.type === "sticky" ? "#1f2328" : colors.ink(st.textColor || "ink");
          ctx.textAlign = tl.align === "center" || tl.align === "right" ? tl.align : "left";
          ctx.textBaseline = "middle";
          if (tl.label) {
            var wmax = 0;
            tl.lines.forEach(function (l) {
              wmax = Math.max(wmax, ctx.measureText(l).width);
            });
            ctx.save();
            ctx.fillStyle = colors.bg;
            ctx.fillRect(tl.x - wmax / 2 - 4, tl.y - tl.lh / 2, wmax + 8, tl.lh * tl.lines.length);
            ctx.restore();
          }
          tl.lines.forEach(function (l, i) {
            ctx.fillText(l, tl.x, tl.y + i * tl.lh);
          });
        }
        ctx.restore();
      });
    }

    function loadImages() {
      var out = {};
      var jobs = [];
      els.forEach(function (el) {
        if (el.type === "image" && el.src) {
          jobs.push(loadImage(el.src).then(function (img) {
            out[el.id] = img;
          }));
        }
      });
      return Promise.all(jobs).then(function () {
        return out;
      });
    }

    function exportName(ext) {
      var t = String(o.title || "Доска").replace(/[\\/:*?"<>|]+/g, " ").trim() || "Доска";
      return t + "." + ext;
    }

    function exportPng() {
      var b = contentBounds();
      if (!b) {
        notify("Доска пуста");
        return Promise.resolve();
      }
      var pad = 40;
      var scale = Math.min(2, 8000 / Math.max(b.w + pad * 2, b.h + pad * 2));
      var colors = exportColors();
      return loadImages().then(function (images) {
        var canvas = document.createElement("canvas");
        canvas.width = Math.ceil((b.w + pad * 2) * scale);
        canvas.height = Math.ceil((b.h + pad * 2) * scale);
        var ctx = canvas.getContext("2d");
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.scale(scale, scale);
        ctx.translate(pad - b.x, pad - b.y);
        drawScene(ctx, colors, images);
        return new Promise(function (resolve) {
          canvas.toBlob(function (blob) {
            if (blob) downloadBlob(blob, exportName("png"));
            else notify("Не удалось сохранить PNG");
            resolve();
          }, "image/png");
        });
      });
    }

    function exportSvg() {
      var b = contentBounds();
      if (!b) {
        notify("Доска пуста");
        return;
      }
      var pad = 40;
      var colors = exportColors();
      var measure = document.createElement("canvas").getContext("2d");
      var out = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + (b.x - pad) + " " + (b.y - pad) + " " + (b.w + pad * 2) + " " + (b.h + pad * 2) +
        '" width="' + Math.ceil(b.w + pad * 2) + '" height="' + Math.ceil(b.h + pad * 2) + '" font-family="' + esc(colors.font) + '">',
        '<rect x="' + (b.x - pad) + '" y="' + (b.y - pad) + '" width="' + (b.w + pad * 2) + '" height="' + (b.h + pad * 2) + '" fill="' + colors.bg + '"/>',
      ];
      sortedEls().forEach(function (el) {
        var st = el.style || {};
        var sw = Number(st.strokeWidth) || 2;
        var stroke = colors.ink(st.stroke);
        var dash = dashArray(st.dash, sw);
        var da = dash ? ' stroke-dasharray="' + dash + '"' : "";
        var g = el.rot && BOXED[el.type] ? '<g transform="rotate(' + el.rot + " " + (el.x + el.w / 2) + " " + (el.y + el.h / 2) + ')">' : "<g>";
        out.push(g);
        if (el.type === "line") {
          var res = G.resolveLine(el, getEl);
          var lp = linePathOf(el, res);
          out.push('<path d="' + lp.d + '" fill="none" stroke="' + stroke + '" stroke-width="' + sw + '"' + da + ' stroke-linecap="round" stroke-linejoin="round"/>');
          var size = 8 + sw * 2.5;
          if (el.arrowEnd) out.push(headPoly(G.arrowHead(res.p1, lp.endDir, size), stroke));
          if (el.arrowStart) out.push(headPoly(G.arrowHead(res.p0, lp.startDir, size), stroke));
        } else if (el.type === "pen") {
          var d = G.smoothPath((el.points || []).map(function (q) {
            return G.pt(el.x + q[0], el.y + q[1]);
          }));
          out.push('<path d="' + d + '" fill="none" stroke="' + stroke + '" stroke-width="' + (Number(st.strokeWidth) || 3) + '" stroke-linecap="round" stroke-linejoin="round"/>');
        } else if (el.type === "image") {
          out.push('<image x="' + el.x + '" y="' + el.y + '" width="' + el.w + '" height="' + el.h + '" preserveAspectRatio="none" href="' + esc(el.src || "") + '"/>');
        } else {
          var fill = el.type === "sticky" ? colors.sticky(st.fill) : colors.fill(st.fill) || "none";
          var strokeAttr = el.type === "sticky" || el.type === "text" ? ' stroke="none"' : ' stroke="' + stroke + '" stroke-width="' + sw + '"' + da;
          if (el.type === "ellipse") out.push('<ellipse cx="' + (el.x + el.w / 2) + '" cy="' + (el.y + el.h / 2) + '" rx="' + el.w / 2 + '" ry="' + el.h / 2 + '" fill="' + fill + '"' + strokeAttr + "/>");
          else if (el.type === "diamond") out.push('<polygon points="' + (el.x + el.w / 2) + "," + el.y + " " + (el.x + el.w) + "," + (el.y + el.h / 2) + " " + (el.x + el.w / 2) + "," + (el.y + el.h) + " " + el.x + "," + (el.y + el.h / 2) + '" fill="' + fill + '"' + strokeAttr + ' stroke-linejoin="round"/>');
          else if (el.type !== "text") out.push('<rect x="' + el.x + '" y="' + el.y + '" width="' + el.w + '" height="' + el.h + '" rx="' + (el.type === "sticky" ? 3 : Math.min(10, el.w / 4, el.h / 4)) + '" fill="' + fill + '"' + strokeAttr + "/>");
        }
        var tl = textLayout(measure, el, colors);
        if (tl) {
          var color = el.type === "sticky" ? "#1f2328" : colors.ink(st.textColor || "ink");
          if (tl.label) {
            var wmax = 0;
            tl.lines.forEach(function (l) {
              wmax = Math.max(wmax, measure.measureText(l).width);
            });
            out.push('<rect x="' + (tl.x - wmax / 2 - 4) + '" y="' + (tl.y - tl.lh / 2) + '" width="' + (wmax + 8) + '" height="' + tl.lh * tl.lines.length + '" fill="' + colors.bg + '"/>');
          }
          out.push(
            '<text font-size="' + tl.fs + '"' + (st.bold ? ' font-weight="600"' : "") + ' fill="' + color + '" text-anchor="' + (tl.align === "center" ? "middle" : tl.align === "right" ? "end" : "start") + '" dominant-baseline="central">' +
            tl.lines.map(function (l, i) {
              return '<tspan x="' + tl.x + '" y="' + (tl.y + i * tl.lh) + '">' + esc(l) + "</tspan>";
            }).join("") + "</text>"
          );
        }
        out.push("</g>");
      });
      out.push("</svg>");
      downloadBlob(new Blob([out.join("")], { type: "image/svg+xml" }), exportName("svg"));
    }

    // ---- документ ----
    function getJson() {
      var elements = {};
      sortedEls().forEach(function (el) {
        elements[el.id] = serializeEl(el, getEl, pathOptsOf);
      });
      return JSON.stringify({ v: 1, elements: elements });
    }

    function loadElements(list) {
      finishEdit();
      var keep = new Set();
      var nextIds = {};
      list.forEach(function (el) {
        nextIds[el.id] = 1;
      });
      els.forEach(function (_, id) {
        if (!nextIds[id]) dirty.add(id);
      });
      els = new Map();
      list.forEach(function (el) {
        els.set(el.id, el);
        dirty.add(el.id);
        if (selected.has(el.id)) keep.add(el.id);
      });
      selected = keep;
      orderDirty = true;
      schedule();
    }

    function setJson(json, keepView) {
      // В совместном режиме источник — документ Yjs: чужой JSON с сервера его не заменяет.
      if (yjs && keepView) return;
      if (!keepView) autoFit = true;
      loadElements(parseBoard(json));
      undoStack = [];
      redoStack = [];
      renderHistoryBar();
      if (!keepView) {
        flushNow();
        fitAll();
      }
    }

    // ---- Yjs: каждый элемент — запись в Y.Map("elements") ----
    function yElements(doc) {
      return doc.getMap("elements");
    }

    function attachYjs(doc, Y) {
      detachYjs();
      var map = yElements(doc);
      var observer = function (events, tr) {
        if (tr.origin === "leo-board-local") return;
        var ids = {};
        events.forEach(function (ev) {
          if (ev.target === map) ev.keysChanged.forEach(function (k) {
            ids[k] = 1;
          });
          else {
            var p = ev.path && ev.path[0];
            if (p) ids[p] = 1;
          }
        });
        Object.keys(ids).forEach(function (id) {
          var ym = map.get(id);
          var val = ym && typeof ym.toJSON === "function" ? ym.toJSON() : null;
          if (val && val.type) {
            val.id = id;
            if (!els.has(id) || (els.get(id).z || 0) !== (val.z || 0)) orderDirty = true;
            els.set(id, val);
          } else {
            els.delete(id);
            selected.delete(id);
            orderDirty = true;
          }
          dirty.add(id);
        });
        schedule();
      };
      map.observeDeep(observer);
      yjs = { doc: doc, map: map, Y: Y, observer: observer };
      root.classList.add("is-live");
      // Документ уже есть — показываем его; пустой — заполняем своим.
      if (map.size) {
        var list = [];
        map.forEach(function (ym, id) {
          var val = ym && typeof ym.toJSON === "function" ? ym.toJSON() : null;
          if (val && val.type) {
            val.id = id;
            list.push(val);
          }
        });
        loadElements(list);
      } else if (els.size) {
        els.forEach(function (_, id) {
          pendingY.add(id);
        });
        flushY();
      }
    }

    function detachYjs() {
      if (!yjs) return;
      try {
        yjs.map.unobserveDeep(yjs.observer);
      } catch (_) {}
      yjs = null;
      pendingY.clear();
      root.classList.remove("is-live");
    }

    function scheduleYFlush() {
      if (yFlushHandle) return;
      yFlushHandle = setTimeout(function () {
        yFlushHandle = 0;
        flushY();
      }, 40);
    }

    function flushY() {
      if (!yjs || !pendingY.size) return;
      var Y = yjs.Y;
      var map = yjs.map;
      var ids = Array.from(pendingY);
      pendingY.clear();
      yjs.doc.transact(function () {
        ids.forEach(function (id) {
          var el = els.get(id);
          if (!el) {
            if (map.has(id)) map.delete(id);
            return;
          }
          var ser = serializeEl(el, getEl, pathOptsOf);
          delete ser.id;
          delete ser.d;
          if (el.type === "line") ser.points = el.points;
          writeEl(Y, map, id, ser);
        });
      }, "leo-board-local");
    }

    function setRemoteCursors(list) {
      remoteCursors = Array.isArray(list) ? list : [];
      renderOverlay();
    }

    // ---- события ----
    function onResize() {
      // Вид ещё не трогали — вписываем заново: при открытии размер панели приходит не сразу.
      if (autoFit) fitAll();
      renderOverlay();
      renderProps();
      scheduleMinimap();
    }

    stage.addEventListener("pointerdown", onPointerDown);
    stage.addEventListener("pointermove", onPointerMove);
    stage.addEventListener("pointerup", onPointerUp);
    stage.addEventListener("pointercancel", onPointerUp);
    stage.addEventListener("wheel", onWheel, { passive: false });
    stage.addEventListener("keydown", onKeyDown);
    stage.addEventListener("keyup", onKeyUp);
    stage.addEventListener("copy", onCopy);
    stage.addEventListener("cut", onCut);
    stage.addEventListener("paste", onPaste);
    stage.addEventListener("dragover", function (e) {
      if (!readOnly) e.preventDefault();
    });
    stage.addEventListener("drop", onDrop);
    stage.addEventListener("contextmenu", function (e) {
      if (!editing) e.preventDefault();
    });
    // Жест «назад» и масштаб страницы в Safari.
    stage.addEventListener("gesturestart", function (e) {
      e.preventDefault();
    });
    function onWinBlur() {
      spaceDown = false;
      stage.classList.remove("is-panning");
    }
    window.addEventListener("blur", onWinBlur);
    propsBar.addEventListener("click", onPropsClick);
    propsBar.addEventListener("pointerdown", function (e) {
      e.stopPropagation();
    });
    pop.addEventListener("pointerdown", function (e) {
      e.stopPropagation();
    });
    zoomBar.addEventListener("click", onBarClick);
    if (historyBar) historyBar.addEventListener("click", onBarClick);
    minimap.addEventListener("pointerdown", onMapPointer);
    var ro = typeof ResizeObserver === "function" ? new ResizeObserver(onResize) : null;
    if (ro) ro.observe(stage);
    document.addEventListener("pointerdown", onDocPointer, true);

    function onDocPointer(e) {
      if (!pop.classList.contains("hidden") && !pop.contains(e.target) && !(pop._anchor && pop._anchor.contains(e.target))) closePop();
    }

    renderTools();
    renderHistoryBar();
    loadElements(parseBoard(o.json));
    var hadView = loadView();
    flushNow();
    if (hadView) applyView();
    else {
      // Размер хоста может появиться позже (вкладка только что показана) — см. onResize.
      autoFit = true;
      fitAll();
    }

    return {
      root: root,
      getJson: getJson,
      getHtml: getJson,
      setJson: function (json) {
        setJson(json, false);
      },
      setJsonKeepView: function (json) {
        setJson(json, true);
      },
      fitAll: fitAll,
      exportPng: exportPng,
      exportSvg: exportSvg,
      attachYjs: attachYjs,
      detachYjs: detachYjs,
      isYjsAttached: function () {
        return !!yjs;
      },
      setRemoteCursors: setRemoteCursors,
      hasElement: function (id) {
        return !!getEl(id);
      },
      revealElement: revealElement,
      setChatBadge: function (v) {
        chatBadge = String(v || "");
        renderZoomBar();
      },
      setTexts: setTexts,
      getSelection: selectionInfo,
      isEditing: function () {
        return !!editing || !!gesture;
      },
      focus: function () {
        stage.focus({ preventScroll: true });
      },
      destroy: function () {
        if (destroyed) return;
        finishEdit();
        if (changeTimer) {
          clearTimeout(changeTimer);
          emitChange();
        }
        flushY();
        destroyed = true;
        detachYjs();
        if (frame) cancelAnimationFrame(frame);
        if (mapTimer) clearTimeout(mapTimer);
        if (ro) ro.disconnect();
        document.removeEventListener("pointerdown", onDocPointer, true);
        window.removeEventListener("blur", onWinBlur);
        if (saveViewTimer) clearTimeout(saveViewTimer);
        root.remove();
      },
    };
  }

  // ---- документ Yjs: Y.Map("elements"), элемент — вложенный Y.Map полей ----
  function docFields(el) {
    var f = clone(el);
    delete f.id;
    delete f.d;
    return f;
  }

  function writeEl(Y, map, id, fields) {
    var ym = map.get(id);
    if (!ym || typeof ym.set !== "function") {
      ym = new Y.Map();
      map.set(id, ym);
    }
    var keys = {};
    Object.keys(fields).forEach(function (k) {
      if (fields[k] === undefined) return;
      keys[k] = 1;
      if (!same(ym.get(k), fields[k])) ym.set(k, fields[k]);
    });
    ym.forEach(function (_, k) {
      if (!keys[k]) ym.delete(k);
    });
  }

  /** Посев: JSON листа → документ (первое открытие совместного холста). */
  function seedDoc(Y, doc, json, origin) {
    var map = doc.getMap("elements");
    doc.transact(function () {
      parseBoard(json).forEach(function (el) {
        writeEl(Y, map, el.id, docFields(el));
      });
    }, origin);
  }

  /** Правка мимо Yjs (было → стало): в документ — только изменённые элементы, чужие правки остаются. */
  function applyDelta(Y, doc, oldJson, newJson, origin) {
    var before = {};
    var after = {};
    parseBoard(oldJson).forEach(function (el) {
      before[el.id] = docFields(el);
    });
    parseBoard(newJson).forEach(function (el) {
      after[el.id] = docFields(el);
    });
    var map = doc.getMap("elements");
    doc.transact(function () {
      Object.keys(before).concat(Object.keys(after)).forEach(function (id) {
        if (same(before[id], after[id])) return;
        if (!after[id]) {
          if (map.has(id)) map.delete(id);
          return;
        }
        // Поле за полем: меняем только то, что поменяла правка.
        var b = before[id] || {};
        var a = after[id];
        var ym = map.get(id);
        if (!ym || typeof ym.set !== "function") {
          writeEl(Y, map, id, a);
          return;
        }
        Object.keys(a).forEach(function (k) {
          if (!same(a[k], b[k])) ym.set(k, a[k]);
        });
        Object.keys(b).forEach(function (k) {
          if (!(k in a)) ym.delete(k);
        });
      });
    }, origin);
  }

  var SHAPE_RU = { rect: "блок", diamond: "ромб/решение", ellipse: "овал", sticky: "стикер", text: "текст", image: "картинка" };
  var COLOR_RU = {
    red: "красный", orange: "оранжевый", yellow: "жёлтый", green: "зелёный", blue: "синий",
    purple: "фиолетовый", pink: "розовый", gray: "серый",
  };

  function oneLine(t) {
    return String(t || "").replace(/\s+/g, " ").trim();
  }

  /** Описание доски для Лео — как board_doc.board_describe на сервере: блоки, связи, группы, рисунки. */
  function describeBoard(json, title) {
    var list = parseBoard(json);
    if (!list.length) return "Доска пустая.";
    var byId = {};
    list.forEach(function (el) {
      byId[el.id] = el;
    });
    var shapes = list.filter(function (el) {
      return BOXED[el.type];
    }).sort(function (a, b) {
      return Math.round(a.y / 40) - Math.round(b.y / 40) || a.x - b.x;
    });
    var num = {};
    shapes.forEach(function (el, i) {
      num[el.id] = i + 1;
    });
    function name(id) {
      var t = oneLine((byId[id] || {}).text);
      return num[id] ? "#" + num[id] + (t ? " «" + t + "»" : "") : "?";
    }
    var out = [title ? "Доска «" + title + "»:" : "Доска:", "Блоки (сверху вниз, слева направо):"];
    shapes.forEach(function (el) {
      var st = el.style || {};
      var color = el.type === "sticky" ? st.fill : st.fill && st.fill !== "none" ? st.fill : st.stroke;
      var kind = SHAPE_RU[el.type] || el.type;
      if (COLOR_RU[color]) kind += ", " + COLOR_RU[color];
      if (el.type === "sticky" && el.author && el.author.name) kind += ", автор " + el.author.name;
      out.push(("- #" + num[el.id] + " [" + kind + "] " + oneLine(el.text)).replace(/\s+$/, ""));
    });
    var links = [];
    var free = 0;
    list.forEach(function (el) {
      if (el.type !== "line") return;
      var a = el.start && el.start.el;
      var b = el.end && el.end.el;
      var label = oneLine(el.text);
      if (num[a] && num[b]) {
        var arrow = el.arrowEnd ? "→" : "—";
        if (el.arrowStart && el.arrowEnd) arrow = "↔";
        else if (el.arrowStart) {
          var t = a;
          a = b;
          b = t;
          arrow = "→";
        }
        var dashed = el.style && el.style.dash ? "пунктир" : "";
        var extra = label ? " («" + label + "»" + (dashed ? ", " + dashed : "") + ")" : dashed ? " (" + dashed + ")" : "";
        links.push("- " + name(a) + " " + arrow + " " + name(b) + extra);
      } else {
        free++;
        if (label) links.push("- линия с подписью «" + label + "»");
      }
    });
    if (links.length) out = out.concat(["Связи:"], links);
    var groups = {};
    shapes.forEach(function (el) {
      if (el.group) (groups[el.group] = groups[el.group] || []).push(name(el.id));
    });
    Object.keys(groups).forEach(function (g) {
      if (groups[g].length > 1) out.push("Группа: " + groups[g].join(", "));
    });
    var pens = list.filter(function (el) {
      return el.type === "pen";
    }).length;
    var extras = [];
    if (pens) extras.push("рисунков карандашом: " + pens);
    if (free) extras.push("линий без привязки: " + free);
    if (extras.length) out.push("Ещё на доске: " + extras.join(", ") + " (их содержимое текстом не описать).");
    return out.join("\n");
  }

  window.LeoBoard = {
    create: create,
    parse: parseBoard,
    describe: describeBoard,
    seedDoc: seedDoc,
    applyDelta: applyDelta,
    emptyJson: function () {
      return JSON.stringify({ v: 1, elements: {} });
    },
    plainText: function (json) {
      return parseBoard(json)
        .filter(function (el) {
          return el.text;
        })
        .sort(function (a, b) {
          return Math.round(a.y / 40) - Math.round(b.y / 40) || a.x - b.x;
        })
        .map(function (el) {
          return String(el.text);
        })
        .join("\n");
    },
  };
})();
