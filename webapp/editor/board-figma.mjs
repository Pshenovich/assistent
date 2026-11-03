// Вставка схем из FigJam на доску (webapp/board.js, onPaste) — как в оригинале.
//
// FigJam при копировании кладёт в text/html `data-buffer="<!--(figma)…(/figma)-->"`: base64 файла
// fig-kiwi — заголовок «fig-kiwi», версия, затем куски [длина][данные]: первый — схема kiwi
// (deflate-raw), второй — сообщение (zstd) с nodeChanges — узлами сцены. Формат закрытый: что не
// разобрали — null, и доска вставит текст как раньше.
//
// Сборка: npm run build (→ webapp/board-figma.js, window.LeoBoardFigma).
// LeoBoardFigma.fromHtml(html, uid) → Promise<{source, elements, skipped} | null>.
import { decompress as zstd } from "fzstd";

var FIGMA_RE = /\(figma\)([A-Za-z0-9+/=]+)\(\/figma\)/;

// ---- kiwi: схема и сообщение (формат evanw/kiwi) ----
function Reader(bytes) {
  this.b = bytes;
  this.i = 0;
}
Reader.prototype.byte = function () {
  if (this.i >= this.b.length) throw new Error("kiwi: eof");
  return this.b[this.i++];
};
Reader.prototype.vu = function () {
  var r = 0;
  var sh = 0;
  for (;;) {
    var c = this.byte();
    r |= (c & 127) << sh;
    sh += 7;
    if (!(c & 128) || sh >= 35) return r >>> 0;
  }
};
Reader.prototype.vi = function () {
  var v = this.vu();
  return v & 1 ? ~(v >>> 1) : v >>> 1;
};
Reader.prototype.vu64 = function () {
  var r = 0;
  var mul = 1;
  for (;;) {
    var c = this.byte();
    r += (c & 127) * mul;
    mul *= 128;
    if (!(c & 128) || mul > 2 ** 63) return r;
  }
};
var f32 = new Float32Array(1);
var u32 = new Uint32Array(f32.buffer);
Reader.prototype.f = function () {
  var b0 = this.byte();
  if (b0 === 0) return 0;
  var bits = b0 | (this.byte() << 8) | (this.byte() << 16) | (this.byte() << 24);
  u32[0] = (bits << 23) | (bits >>> 9);
  return f32[0];
};
var utf8 = new TextDecoder("utf-8");
Reader.prototype.s = function () {
  var j = this.b.indexOf(0, this.i);
  if (j < 0) throw new Error("kiwi: string");
  var v = utf8.decode(this.b.subarray(this.i, j));
  this.i = j + 1;
  return v;
};

function readSchema(bytes) {
  var r = new Reader(bytes);
  var n = r.vu();
  var defs = [];
  for (var d = 0; d < n; d++) {
    var name = r.s();
    var kind = r.byte();
    var fc = r.vu();
    var fields = [];
    for (var k = 0; k < fc; k++) fields.push({ name: r.s(), type: r.vi(), array: !!r.byte(), value: r.vu() });
    var byValue = {};
    fields.forEach(function (f) {
      byValue[f.value] = f;
    });
    defs.push({ name: name, kind: kind, fields: fields, byValue: byValue });
  }
  return defs;
}

function readValue(defs, r, type) {
  switch (type) {
    case -1: return !!r.byte();
    case -2: return r.byte();
    case -3: return r.vi();
    case -4: return r.vu();
    case -5: return r.f();
    case -6: return r.s();
    case -7: {
      var v = r.vu64();
      return v % 2 ? -(v + 1) / 2 : v / 2;
    }
    case -8: return r.vu64();
  }
  var def = defs[type];
  if (!def) throw new Error("kiwi: type");
  if (def.kind === 0) {
    var ev = r.vu();
    var ef = def.byValue[ev];
    return ef ? ef.name : ev;
  }
  var out = {};
  if (def.kind === 1) {
    def.fields.forEach(function (f) {
      out[f.name] = readField(defs, r, f);
    });
    return out;
  }
  for (;;) {
    var key = r.vu();
    if (key === 0) return out;
    var f = def.byValue[key];
    if (!f) throw new Error("kiwi: field");
    out[f.name] = readField(defs, r, f);
  }
}

function readField(defs, r, f) {
  if (!f.array) return readValue(defs, r, f.type);
  var n = r.vu();
  if (f.type === -2) {
    r.i += n; // байтовые массивы (геометрия векторов) нам не нужны
    return null;
  }
  var arr = new Array(n);
  for (var i = 0; i < n; i++) arr[i] = readValue(defs, r, f.type);
  return arr;
}

function b64bytes(s) {
  var bin = atob(s);
  var out = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function inflateRaw(bytes) {
  var ds = new DecompressionStream("deflate-raw");
  var buf = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(buf);
}

function unpack(bytes) {
  if (bytes[0] === 0x28 && bytes[1] === 0xb5 && bytes[2] === 0x2f && bytes[3] === 0xfd) return Promise.resolve(zstd(bytes));
  return inflateRaw(bytes);
}

async function decodeFigma(html) {
  var m = FIGMA_RE.exec(String(html || ""));
  if (!m) return null;
  var raw = b64bytes(m[1]);
  if (utf8.decode(raw.subarray(0, 8)) !== "fig-kiwi") return null;
  var dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  var off = 12;
  var chunks = [];
  while (off + 4 <= raw.length) {
    var n = dv.getUint32(off, true);
    off += 4;
    chunks.push(raw.subarray(off, off + n));
    off += n;
  }
  if (chunks.length < 2) return null;
  var defs = readSchema(await unpack(chunks[0]));
  var root = defs.findIndex(function (d) {
    return d.name === "Message";
  });
  if (root < 0) return null;
  var msg = readValue(defs, new Reader(await unpack(chunks[1])), root);
  return msg && Array.isArray(msg.nodeChanges) ? msg : null;
}

// ---- цвета ----
function hex2(v) {
  return ("0" + Math.max(0, Math.min(255, Math.round(v * 255))).toString(16)).slice(-2);
}

/** Заливка Figma → #rrggbb; полупрозрачная — смешиваем с белым (фон FigJam). */
function paintHex(paints) {
  var p = (paints || []).filter(function (x) {
    return x && x.type === "SOLID" && x.visible !== false && x.color;
  })[0];
  if (!p) return null;
  var a = (p.color.a == null ? 1 : p.color.a) * (p.opacity == null ? 1 : p.opacity);
  if (a <= 0.01) return null;
  var mix = function (c) {
    return c * a + (1 - a);
  };
  return "#" + hex2(mix(p.color.r)) + hex2(mix(p.color.g)) + hex2(mix(p.color.b));
}

/** Почти чёрный и белый — цвета темы доски (как при вставке из Miro). */
function themed(h) {
  if (!h) return h;
  var r = parseInt(h.slice(1, 3), 16);
  var g = parseInt(h.slice(3, 5), 16);
  var b = parseInt(h.slice(5, 7), 16);
  var lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  var sat = Math.max(r, g, b) - Math.min(r, g, b);
  if (sat < 24 && lum < 70) return "ink";
  if (sat < 12 && lum > 245) return "white";
  return h;
}

function isHex(v) {
  return typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v);
}

// ---- узлы ----
function gid(g) {
  return g ? g.sessionID + ":" + g.localID : "";
}

/** Переопределение части узла: 40000000:0 — фигура (заливка, рамка), :1 — её текст, :2 — фон ячеек. */
function part(node, localID, path) {
  var ov = (node.nodeGenerationData && node.nodeGenerationData.overrides) || [];
  for (var i = 0; i < ov.length; i++) {
    var g = (ov[i].guidPath && ov[i].guidPath.guids) || [];
    if (!g.length || g[0].sessionID !== 40000000 || g[0].localID !== localID) continue;
    if (path ? g.length === 3 && gid(g[1]) === path[0] && gid(g[2]) === path[1] : g.length === 1) return ov[i];
  }
  return null;
}

function textOf(td) {
  return String((td && td.characters) || "").replace(/\u2028|\u2029/g, "\n").replace(/\s+$/, "");
}

var ALIGN = { LEFT: "left", CENTER: "center", RIGHT: "right", JUSTIFIED: "left" };
var SHAPES = { SQUARE: "rect", ROUNDED_RECTANGLE: "round", ELLIPSE: "ellipse", DIAMOND: "diamond" };

function mul(a, b) {
  return {
    m00: a.m00 * b.m00 + a.m01 * b.m10, m01: a.m00 * b.m01 + a.m01 * b.m11, m02: a.m00 * b.m02 + a.m01 * b.m12 + a.m02,
    m10: a.m10 * b.m00 + a.m11 * b.m10, m11: a.m10 * b.m01 + a.m11 * b.m11, m12: a.m10 * b.m02 + a.m11 * b.m12 + a.m12,
  };
}

var ID = { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 };

function fromFigma(msg, uid) {
  var nodes = msg.nodeChanges;
  var byId = {};
  var kids = {};
  nodes.forEach(function (n) {
    var id = gid(n.guid);
    byId[id] = n;
    var p = n.parentIndex && n.parentIndex.guid ? gid(n.parentIndex.guid) : "";
    if (p) (kids[p] = kids[p] || []).push(n);
  });
  // Порядок детей — по position (дробный индекс строкой).
  Object.keys(kids).forEach(function (k) {
    kids[k].sort(function (a, b) {
      var pa = (a.parentIndex && a.parentIndex.position) || "";
      var pb = (b.parentIndex && b.parentIndex.position) || "";
      return pa < pb ? -1 : pa > pb ? 1 : 0;
    });
  });
  var page = nodes.filter(function (n) {
    return n.type === "CANVAS" && !/internal/i.test(n.name || "");
  })[0];
  if (!page) return null;
  var els = [];
  var map = {};
  var skipped = 0;
  var connectors = [];

  function box(n, world) {
    var t = n.transform ? mul(world, n.transform) : world;
    var w = (n.size && n.size.x) || 0;
    var h = (n.size && n.size.y) || 0;
    var cx = t.m00 * (w / 2) + t.m01 * (h / 2) + t.m02;
    var cy = t.m10 * (w / 2) + t.m11 * (h / 2) + t.m12;
    var rot = (Math.atan2(t.m10, t.m00) * 180) / Math.PI;
    var b = { x: cx - w / 2, y: cy - h / 2, w: w, h: h };
    if (Math.abs(rot) > 0.05) b.rot = Math.round(rot * 10) / 10;
    return { b: b, t: t };
  }

  function textStyle(tp, onColor) {
    var st = {};
    if (!tp) return st;
    if (tp.fontSize) st.fontSize = Math.round(tp.fontSize);
    if (ALIGN[tp.textAlignHorizontal]) st.align = ALIGN[tp.textAlignHorizontal];
    var c = paintHex(tp.fillPaints);
    if (c) st.textColor = onColor ? c : themed(c);
    var style = tp.fontName && tp.fontName.style;
    if (/bold|semi|black|heavy/i.test(style || "")) st.bold = true;
    return st;
  }

  function add(el, figId) {
    if (figId) map[figId] = el.id;
    els.push(el);
  }

  function walk(n, world) {
    if (n.visible === false) return;
    var id = gid(n.guid);
    var type = n.type;
    if (type === "CONNECTOR") {
      connectors.push(n);
      return;
    }
    var pos = box(n, world);
    var b = pos.b;
    if (type === "SHAPE_WITH_TEXT" || type === "STICKY") {
      var shape = part(n, 0) || n;
      var tp = part(n, 1) || n;
      var fill = paintHex(shape.fillPaints || n.fillPaints);
      var text = textOf(tp.textData || n.textData);
      if (type === "STICKY") {
        add(Object.assign({ id: uid(), type: "sticky", text: text }, b, {
          style: Object.assign({ fill: fill || "yellow" }, textStyle(tp, true)),
        }), id);
        return;
      }
      var kind = SHAPES[n.shapeWithTextType] || "rect";
      var fillT = themed(fill) || "none";
      var stroke = paintHex(shape.strokePaints || n.strokePaints);
      var sw = shape.strokeWeight != null ? shape.strokeWeight : n.strokeWeight;
      var st = Object.assign({
        fill: fillT,
        stroke: themed(stroke) || "ink",
        strokeWidth: stroke ? Math.max(0, sw == null ? 2 : sw) : 0,
      }, textStyle(tp, isHex(fillT)));
      if ((shape.dashPattern || []).length) st.dash = "dashed";
      if (kind === "round") st.radius = Math.round(shape.cornerRadius != null ? shape.cornerRadius : 16);
      else if (kind === "rect") st.radius = 0;
      add(Object.assign({ id: uid(), type: kind === "round" ? "rect" : kind, text: text }, b, { style: st }), id);
      return;
    }
    if (type === "TEXT") {
      var tst = textStyle(n, false);
      add(Object.assign({ id: uid(), type: "text", text: textOf(n.textData) }, b, { style: tst }), id);
      return;
    }
    if (type === "TABLE") {
      add(tableEl(n, b), id);
      return;
    }
    if (type === "FRAME" || type === "SECTION" || type === "RECTANGLE" || type === "ROUNDED_RECTANGLE" || type === "ELLIPSE") {
      var ffill = themed(paintHex(n.fillPaints));
      var fstroke = paintHex(n.strokePaints);
      if (ffill || fstroke || type === "SECTION") {
        var fst = {
          fill: ffill || "none",
          stroke: themed(fstroke) || "gray",
          strokeWidth: fstroke ? Math.max(0, n.strokeWeight == null ? 1 : n.strokeWeight) : 0,
        };
        if (type !== "ELLIPSE") fst.radius = Math.round(n.cornerRadius || 0);
        add(Object.assign({ id: uid(), type: type === "ELLIPSE" ? "ellipse" : "rect", text: "" }, b, { style: fst }), id);
      }
      if (type === "SECTION" && n.name) {
        add({ id: uid(), type: "text", x: b.x, y: b.y - 30, w: Math.max(160, b.w), h: 24, text: n.name, style: { fontSize: 16, textColor: "gray" } });
      }
      (kids[id] || []).forEach(function (k) {
        walk(k, pos.t);
      });
      return;
    }
    if (type === "GROUP") {
      (kids[id] || []).forEach(function (k) {
        walk(k, pos.t);
      });
      return;
    }
    // Компоненты, векторы, иконки — нашими блоками не собрать.
    skipped++;
  }

  function tableEl(n, b) {
    var order = function (list) {
      return ((list && list.entries) || []).slice().sort(function (a, c) {
        return a.position < c.position ? -1 : a.position > c.position ? 1 : 0;
      }).map(function (e) {
        return gid(e.id);
      });
    };
    var rows = order(n.tableRowPositions);
    var cols = order(n.tableColumnPositions);
    var widths = {};
    ((n.tableColumnWidths && n.tableColumnWidths.entries) || []).forEach(function (e) {
      widths[gid(e.id)] = Math.round(e.size);
    });
    var esc = function (s) {
      return String(s).replace(/[&<>]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
      });
    };
    var html = '<table class="note-editor-table"><tbody>' + rows.map(function (r) {
      return "<tr>" + cols.map(function (c) {
        var cell = part(n, 1, [r, c]);
        var txt = textOf(cell && cell.textData);
        var w = widths[c] ? ' colwidth="' + widths[c] + '"' : "";
        return '<td colspan="1" rowspan="1"' + w + "><p>" + esc(txt).replace(/\n/g, "<br>") + "</p></td>";
      }).join("") + "</tr>";
    }).join("") + "</tbody></table>";
    var tw = cols.reduce(function (s, c) {
      return s + (widths[c] || 120);
    }, 0);
    return Object.assign({ id: uid(), type: "table" }, b, { w: tw || b.w, html: html });
  }

  (kids[gid(page.guid)] || []).forEach(function (n) {
    walk(n, ID);
  });

  // Стрелки — после блоков: концы прилипают к перенесённым блокам.
  var MAG = { TOP: "top", BOTTOM: "bottom", LEFT: "left", RIGHT: "right" };
  connectors.forEach(function (n) {
    var end = function (c) {
      if (!c || !c.endpointNodeID || !map[gid(c.endpointNodeID)]) return null;
      var e = { el: map[gid(c.endpointNodeID)] };
      if (MAG[c.magnet]) e.side = MAG[c.magnet];
      else if (c.relativePosition) e.anchor = [Math.max(0, Math.min(1, c.relativePosition.x)), Math.max(0, Math.min(1, c.relativePosition.y))];
      return e;
    };
    var a = end(n.connectorStart);
    var z = end(n.connectorEnd);
    // Второй конец — у блока вне выделения: связь не переносим (это не ошибка).
    if (!a || !z || a.el === z.el) return;
    var line = part(n, 0) || n;
    var tp = part(n, 1);
    var arrow = function (cap) {
      return /ARROW|TRIANGLE/i.test(String(cap || ""));
    };
    var ln = {
      id: uid(), type: "line", x: 0, y: 0, w: 0, h: 0, points: [[0, 0], [1, 1]], start: a, end: z,
      route: n.connectorLineStyle === "ELBOWED" ? "elbow" : n.connectorLineStyle === "CURVED" ? "curve" : "straight",
      style: { stroke: themed(paintHex(line.strokePaints)) || "ink", strokeWidth: Math.max(1, Math.round(line.strokeWeight || 2)) },
    };
    if ((line.dashPattern || []).length) ln.style.dash = "dashed";
    if (arrow(n.connectorStartCap)) ln.arrowStart = true;
    if (arrow(n.connectorEndCap)) ln.arrowEnd = true;
    var label = textOf(tp && tp.textData);
    if (label) {
      ln.text = label;
      ln.style.fontSize = Math.round((tp && tp.fontSize) || 14);
    }
    els.push(ln);
  });
  return els.length ? { source: "FigJam", elements: els, skipped: skipped } : null;
}

async function fromHtml(html, uid) {
  try {
    var msg = await decodeFigma(html);
    return msg ? fromFigma(msg, uid) : null;
  } catch (e) {
    if (typeof console !== "undefined") console.warn("[board] FigJam paste:", e && e.message);
    return null;
  }
}

var api = { fromHtml: fromHtml, isFigma: function (html) {
  return FIGMA_RE.test(String(html || ""));
}, _decode: decodeFigma, _fromFigma: fromFigma };
if (typeof window !== "undefined") window.LeoBoardFigma = api;
else globalThis.LeoBoardFigma = api;
