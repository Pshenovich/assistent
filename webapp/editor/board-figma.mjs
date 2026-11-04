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
    var bytes = r.b.slice(r.i, r.i + n); // геометрия векторов (контуры, сеть вершин)
    r.i += n;
    return bytes;
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

// ---- векторы: контуры (commandsBlob) и сеть вершин (vectorNetworkBlob) ----
function num(v) {
  return String(Math.round(v * 100) / 100);
}

function apply(M, x, y) {
  return [M.m00 * x + M.m01 * y + M.m02, M.m10 * x + M.m11 * y + M.m12];
}

function pt(M, x, y) {
  var q = apply(M, x, y);
  return num(q[0]) + " " + num(q[1]);
}

/** Контур Figma: [команда][float32…]: 0 — Z, 1 — M x y, 2 — L x y, 3 — Q, 4 — C. */
function commandsPath(bytes, M) {
  if (!bytes || !bytes.length) return "";
  var dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  var i = 0;
  var out = [];
  var f = function () {
    var v = dv.getFloat32(i, true);
    i += 4;
    return v;
  };
  while (i < bytes.length) {
    var c = bytes[i++];
    if (c === 0) out.push("Z");
    else if (c === 1 || c === 2) {
      var x = f();
      out.push((c === 1 ? "M" : "L") + pt(M, x, f()));
    } else if (c === 3) {
      var qx = f();
      var qy = f();
      var ex = f();
      out.push("Q" + pt(M, qx, qy) + " " + pt(M, ex, f()));
    } else if (c === 4) {
      var ax = f();
      var ay = f();
      var bx = f();
      var by = f();
      var cx = f();
      out.push("C" + pt(M, ax, ay) + " " + pt(M, bx, by) + " " + pt(M, cx, f()));
    } else return "";
  }
  return out.join("");
}

/** Сеть вершин вектора: вершины, сегменты (с касательными), области (петли сегментов). */
function readNetwork(bytes) {
  if (!bytes || bytes.length < 12) return null;
  var dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  var i = 0;
  var u = function () {
    var v = dv.getUint32(i, true);
    i += 4;
    return v;
  };
  var f = function () {
    var v = dv.getFloat32(i, true);
    i += 4;
    return v;
  };
  var nv = u();
  var ns = u();
  var nr = u();
  if (12 + nv * 12 + ns * 28 > bytes.length) return null;
  var verts = [];
  for (var a = 0; a < nv; a++) {
    u();
    verts.push([f(), f()]);
  }
  var segs = [];
  for (var b = 0; b < ns; b++) {
    u();
    var s0 = u();
    var t0 = [f(), f()];
    var s1 = u();
    segs.push({ a: s0, ta: t0, b: s1, tb: [f(), f()] });
  }
  var regions = [];
  for (var c = 0; c < nr && i + 8 <= bytes.length; c++) {
    var sid = u();
    var loops = [];
    var nl = u();
    for (var l = 0; l < nl; l++) {
      var cnt = u();
      var idx = [];
      for (var k = 0; k < cnt; k++) idx.push(u());
      loops.push(idx);
    }
    regions.push({ evenodd: !(sid & 1), loops: loops });
  }
  return { verts: verts, segs: segs, regions: regions };
}

function segTo(net, s, rev, M) {
  var A = net.verts[rev ? s.b : s.a];
  var B = net.verts[rev ? s.a : s.b];
  var ta = rev ? s.tb : s.ta;
  var tb = rev ? s.ta : s.tb;
  if (!A || !B) return "";
  if (!ta[0] && !ta[1] && !tb[0] && !tb[1]) return "L" + pt(M, B[0], B[1]);
  return "C" + pt(M, A[0] + ta[0], A[1] + ta[1]) + " " + pt(M, B[0] + tb[0], B[1] + tb[1]) + " " + pt(M, B[0], B[1]);
}

/** Обводка — все сегменты, подряд идущие склеены в одну линию; заливка — по областям. */
function networkPaths(net, M) {
  var stroke = [];
  var cur = -1;
  net.segs.forEach(function (s) {
    var A = net.verts[s.a];
    if (!A) return;
    if (s.a !== cur) stroke.push("M" + pt(M, A[0], A[1]));
    stroke.push(segTo(net, s, false, M));
    cur = s.b;
  });
  var fills = net.regions.map(function (r) {
    var d = [];
    r.loops.forEach(function (loop) {
      var at = -1;
      loop.forEach(function (si, j) {
        var s = net.segs[si];
        if (!s) return;
        var rev = j === 0 ? false : s.a !== at;
        if (j === 0) {
          var nxt = net.segs[loop[1]];
          if (nxt && (s.a === nxt.a || s.a === nxt.b)) rev = true;
          var S = net.verts[rev ? s.b : s.a];
          if (S) d.push("M" + pt(M, S[0], S[1]));
        }
        d.push(segTo(net, s, rev, M));
        at = rev ? s.a : s.b;
      });
      d.push("Z");
    });
    return { d: d.join(""), evenodd: r.evenodd };
  });
  return { stroke: stroke.join(""), fills: fills };
}

var CAPS = { ROUND: "round", SQUARE: "square" };
var JOINS = { ROUND: "round", BEVEL: "bevel" };

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
  var blobs = (msg.blobs || []).map(function (b) {
    return b && b.bytes;
  });
  var blob = function (i) {
    return typeof i === "number" ? blobs[i] : null;
  };

  // ---- компоненты и векторы → один элемент «вектор» на узел ----
  function vecPaint(paints) {
    return themed(paintHex(paints));
  }

  /** Части компонента по ключу подмены (overrideKey) — так на них ссылаются экземпляры. */
  var keyMaps = {};
  function keyMap(symId) {
    if (keyMaps[symId]) return keyMaps[symId];
    var m = {};
    (function walkKeys(id) {
      (kids[id] || []).forEach(function (k) {
        if (k.overrideKey) m[gid(k.overrideKey)] = k;
        walkKeys(gid(k.guid));
      });
    })(symId);
    return (keyMaps[symId] = m);
  }

  function chain(symId, node) {
    var list = [];
    for (var n = node; n && gid(n.guid) !== symId; n = byId[n.parentIndex && n.parentIndex.guid ? gid(n.parentIndex.guid) : ""]) list.unshift(n);
    return list;
  }

  function overridesOf(inst) {
    var o = {};
    ((inst.symbolData && inst.symbolData.symbolOverrides) || []).forEach(function (ov) {
      var g = (ov.guidPath && ov.guidPath.guids) || [];
      if (g.length) o[gid(g[g.length - 1])] = ov;
    });
    return o;
  }

  function pushNetwork(paths, n, M, ov, scaleW) {
    var vd = n.vectorData;
    var net = vd ? readNetwork(blob(vd.vectorNetworkBlob)) : null;
    if (!net) return;
    var ns = vd.normalizedSize || n.size || { x: 1, y: 1 };
    var sx = (n.size && n.size.x) / (ns.x || 1) || 1;
    var sy = (n.size && n.size.y) / (ns.y || 1) || 1;
    var Mn = mul(M, { m00: sx, m01: 0, m02: 0, m10: 0, m11: sy, m12: 0 });
    var o = ov || {};
    var fill = vecPaint(o.fillPaints || n.fillPaints);
    var stroke = vecPaint(o.strokePaints || n.strokePaints);
    var np = networkPaths(net, Mn);
    if (fill) np.fills.forEach(function (fp) {
      if (fp.d) paths.push({ d: fp.d, fill: fill, rule: fp.evenodd ? "evenodd" : undefined });
    });
    var sw = o.strokeWeight != null ? o.strokeWeight : n.strokeWeight;
    if (stroke && np.stroke && sw > 0) {
      var cap = CAPS[n.strokeCap] || ((vd.styleOverrideTable || []).some(function (t) {
        return t.strokeCap === "ROUND";
      }) ? "round" : undefined);
      paths.push({ d: np.stroke, stroke: stroke, sw: Math.round(sw * scaleW * 100) / 100, cap: cap, join: JOINS[n.strokeJoin] });
    }
  }

  /** Простая фигура внутри компонента (прямоугольник / эллипс) — контуром. */
  function pushPrimitive(paths, n, M, ov, scaleW) {
    var w = (n.size && n.size.x) || 0;
    var h = (n.size && n.size.y) || 0;
    var d;
    if (n.type === "ELLIPSE") {
      var k = 0.5523;
      var rx = w / 2;
      var ry = h / 2;
      d = "M" + pt(M, w, ry) + "C" + pt(M, w, ry + ry * k) + " " + pt(M, rx + rx * k, h) + " " + pt(M, rx, h) +
        "C" + pt(M, rx - rx * k, h) + " " + pt(M, 0, ry + ry * k) + " " + pt(M, 0, ry) +
        "C" + pt(M, 0, ry - ry * k) + " " + pt(M, rx - rx * k, 0) + " " + pt(M, rx, 0) +
        "C" + pt(M, rx + rx * k, 0) + " " + pt(M, w, ry - ry * k) + " " + pt(M, w, ry) + "Z";
    } else {
      var r = Math.min(n.cornerRadius || 0, w / 2, h / 2);
      var c = r * 0.4477;
      d = r > 0
        ? "M" + pt(M, r, 0) + "L" + pt(M, w - r, 0) + "C" + pt(M, w - c, 0) + " " + pt(M, w, c) + " " + pt(M, w, r) +
          "L" + pt(M, w, h - r) + "C" + pt(M, w, h - c) + " " + pt(M, w - c, h) + " " + pt(M, w - r, h) +
          "L" + pt(M, r, h) + "C" + pt(M, c, h) + " " + pt(M, 0, h - c) + " " + pt(M, 0, h - r) +
          "L" + pt(M, 0, r) + "C" + pt(M, 0, c) + " " + pt(M, c, 0) + " " + pt(M, r, 0) + "Z"
        : "M" + pt(M, 0, 0) + "L" + pt(M, w, 0) + "L" + pt(M, w, h) + "L" + pt(M, 0, h) + "Z";
    }
    var o = ov || {};
    var fill = vecPaint(o.fillPaints || n.fillPaints);
    var stroke = vecPaint(o.strokePaints || n.strokePaints);
    var sw = o.strokeWeight != null ? o.strokeWeight : n.strokeWeight;
    if (fill) paths.push({ d: d, fill: fill });
    if (stroke && sw > 0) paths.push({ d: d, stroke: stroke, sw: Math.round(sw * scaleW * 100) / 100, join: JOINS[n.strokeJoin] });
  }

  /** Содержимое компонента / группы векторов: обходим детей, скрытые ветки пропускаем. */
  function pushTree(paths, parentId, M, ovs, scaleW, depth) {
    if (depth > 6) return;
    (kids[parentId] || []).forEach(function (n) {
      if (n.visible === false) return;
      var Mn = n.transform ? mul(M, n.transform) : M;
      var ov = n.overrideKey ? ovs[gid(n.overrideKey)] : null;
      if (ov && ov.visible === false) return;
      if (n.type === "VECTOR" || n.type === "STAR" || n.type === "REGULAR_POLYGON" || n.type === "LINE") pushNetwork(paths, n, Mn, ov, scaleW);
      else if (n.type === "RECTANGLE" || n.type === "ROUNDED_RECTANGLE" || n.type === "ELLIPSE") pushPrimitive(paths, n, Mn, ov, scaleW);
      else if (n.type === "INSTANCE" && n.symbolData) {
        var sid = gid(n.symbolData.symbolID);
        var sym = byId[sid];
        if (!sym) return;
        var s2 = { m00: (n.size.x || 1) / ((sym.size && sym.size.x) || 1), m01: 0, m02: 0, m10: 0, m11: (n.size.y || 1) / ((sym.size && sym.size.y) || 1), m12: 0 };
        pushTree(paths, sid, mul(Mn, s2), Object.assign({}, ovs, overridesOf(n)), scaleW * s2.m00, depth + 1);
      } else pushTree(paths, gid(n.guid), Mn, ovs, scaleW, depth + 1);
    });
  }

  /** Есть ли в поддереве узлы этих типов (с заходом во вложенные компоненты). */
  function hasKind(parentId, types, depth) {
    if (depth > 6) return false;
    return (kids[parentId] || []).some(function (n) {
      if (n.visible === false) return false;
      if (types[n.type]) return true;
      if (n.type === "INSTANCE" && n.symbolData) return hasKind(gid(n.symbolData.symbolID), types, depth + 1);
      return hasKind(gid(n.guid), types, depth + 1);
    });
  }

  /** Экземпляр компонента: готовая геометрия (derivedSymbolData) — или обход самого компонента. */
  function instancePaths(n) {
    var paths = [];
    var sid = n.symbolData ? gid(n.symbolData.symbolID) : "";
    var sym = byId[sid];
    if (!sym) return paths;
    var sx = ((n.size && n.size.x) || 1) / ((sym.size && sym.size.x) || 1);
    var sy = ((n.size && n.size.y) || 1) / ((sym.size && sym.size.y) || 1);
    var ovs = overridesOf(n);
    // Сначала — сами линии компонента (сеть вершин): их можно править по точкам, обводка остаётся обводкой.
    // Булевы операции так не собрать (вычитание) — тогда готовая геометрия.
    if (!hasKind(sid, { BOOLEAN_OPERATION: 1, TEXT: 1 }, 0)) {
      var tree = [];
      pushTree(tree, sid, { m00: sx, m01: 0, m02: 0, m10: 0, m11: sy, m12: 0 }, ovs, (sx + sy) / 2, 0);
      if (tree.length) return tree;
    }
    var keys = keyMap(sid);
    (n.derivedSymbolData || []).forEach(function (dd) {
      var g = (dd.guidPath && dd.guidPath.guids) || [];
      var part = g.length ? keys[gid(g[g.length - 1])] : null;
      if (!part || !(dd.fillGeometry || dd.strokeGeometry)) return;
      var links = chain(sid, part);
      if (links.some(function (x) {
        return x.visible === false || (x.overrideKey && ovs[gid(x.overrideKey)] && ovs[gid(x.overrideKey)].visible === false);
      })) return;
      // Готовая геометрия уже в размере экземпляра: масштабируем только смещения частей.
      var M = ID;
      links.forEach(function (x) {
        if (!x.transform) return;
        var t = Object.assign({}, x.transform, { m02: x.transform.m02 * sx, m12: x.transform.m12 * sy });
        M = mul(M, t);
      });
      var ov = part.overrideKey ? ovs[gid(part.overrideKey)] : null;
      var fill = vecPaint((ov && ov.fillPaints) || part.fillPaints);
      var stroke = vecPaint((ov && ov.strokePaints) || part.strokePaints);
      (dd.fillGeometry || []).forEach(function (gm) {
        var d = fill && commandsPath(blob(gm.commandsBlob), M);
        if (d) paths.push({ d: d, fill: fill, rule: gm.windingRule === "ODD" ? "evenodd" : undefined });
      });
      // Обводка в готовой геометрии — уже контур: заливаем его цветом обводки.
      (dd.strokeGeometry || []).forEach(function (gm) {
        var d = stroke && commandsPath(blob(gm.commandsBlob), M);
        if (d) paths.push({ d: d, fill: stroke, rule: gm.windingRule === "ODD" ? "evenodd" : undefined });
      });
    });
    if (!paths.length) pushTree(paths, sid, { m00: sx, m01: 0, m02: 0, m10: 0, m11: sy, m12: 0 }, ovs, (sx + sy) / 2, 0);
    return paths;
  }

  function vectorEl(n, b, paths) {
    if (!paths.length) return null;
    var d = paths.reduce(function (s, p) {
      return s + p.d.length;
    }, 0);
    if (d > 400000) return null;
    return Object.assign({ id: uid(), type: "vector" }, b, { vb: [Math.max(0.01, b.w), Math.max(0.01, b.h)], paths: paths.slice(0, 200) });
  }

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
    var style = (tp.fontName && tp.fontName.style) || "";
    // Шрифт и начертание как в Figma: «Inter» + «Semi Bold» → fontFamily, fontWeight 600.
    var fam = tp.fontName && tp.fontName.family;
    if (fam && /^[A-Za-z0-9 ]{1,40}$/.test(fam)) st.fontFamily = fam;
    var s0 = style.toLowerCase().replace(/[\s_-]/g, "");
    var wt = /thin|hairline/.test(s0) ? 100 : /extralight|ultralight/.test(s0) ? 200 : /light/.test(s0) ? 300 : /medium/.test(s0) ? 500 :
      /semibold|demibold/.test(s0) ? 600 : /extrabold|ultrabold/.test(s0) ? 800 : /black|heavy/.test(s0) ? 900 : /bold/.test(s0) ? 700 : 400;
    if (wt !== 400) st.fontWeight = wt;
    var lh = tp.lineHeight;
    if (lh && lh.value && tp.fontSize) {
      var px = lh.units === "PIXELS" ? lh.value : lh.units === "PERCENT" ? (lh.value / 100) * tp.fontSize : lh.units === "RAW" ? lh.value * tp.fontSize : 0;
      if (px > 0 && Math.abs(px - tp.fontSize * 1.3) > 0.5) st.lineHeight = Math.round(px * 10) / 10;
    }
    var ls = tp.letterSpacing;
    if (ls && ls.value) {
      var pct = ls.units === "PIXELS" && tp.fontSize ? (ls.value / tp.fontSize) * 100 : ls.units === "PERCENT" ? ls.value : 0;
      if (pct) st.letterSpacing = Math.round(pct * 10) / 10;
    }
    if (tp.textDecoration === "UNDERLINE") st.deco = "underline";
    if (tp.textDecoration === "STRIKETHROUGH") st.deco = "strike";
    if (tp.textCase === "UPPER") st.textCase = "upper";
    if (tp.textCase === "LOWER") st.textCase = "lower";
    if (tp.textCase === "TITLE") st.textCase = "title";
    if (tp.textAlignVertical === "TOP") st.valign = "top";
    if (tp.textAlignVertical === "BOTTOM") st.valign = "bottom";
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
    // Компоненты, иконки, векторы — элементом «вектор» (контуры с цветами, как в FigJam).
    var paths = [];
    if (type === "INSTANCE") paths = instancePaths(n);
    else if (type === "VECTOR" || type === "STAR" || type === "REGULAR_POLYGON" || type === "LINE") pushNetwork(paths, n, ID, null, 1);
    else if (type === "BOOLEAN_OPERATION") {
      (n.fillGeometry || []).forEach(function (gm) {
        var fill = vecPaint(n.fillPaints);
        var d = fill && commandsPath(blob(gm.commandsBlob), ID);
        if (d) paths.push({ d: d, fill: fill, rule: gm.windingRule === "ODD" ? "evenodd" : undefined });
      });
      if (!paths.length) pushTree(paths, id, ID, {}, 1, 0);
    }
    var vel = vectorEl(n, b, paths);
    if (vel) add(vel, id);
    else skipped++;
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
