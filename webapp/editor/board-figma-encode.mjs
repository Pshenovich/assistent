// Макет → Figma: элементы листа «Макет» (webapp/board.js) в родной буфер Figma Design — фреймы с автолэйаутом,
// заливки и градиенты, обводки, скругления, тени и размытия, тексты со шрифтами, векторы.
// Зеркало разбора в board-figma.mjs (designImport): те же поля NodeChange, что мы читаем при вставке из Figma.
//
// Буфер: text/html со <span data-metadata="<!--(figmeta)…(/figmeta)-->"> и <span data-buffer="<!--(figma)…(/figma)-->">,
// внутри — fig-kiwi: заголовок, схема kiwi (как в образце — deflate-raw, хранится готовой) и сообщение (zstd).

import { layoutText } from "./board-figma-font.mjs";

// ---- kiwi: запись сообщения по схеме (формат evanw/kiwi) ----
function Writer() {
  this.b = new Uint8Array(1 << 14);
  this.n = 0;
}
Writer.prototype.room = function (k) {
  if (this.n + k <= this.b.length) return;
  var nb = new Uint8Array(Math.max(this.b.length * 2, this.n + k));
  nb.set(this.b.subarray(0, this.n));
  this.b = nb;
};
Writer.prototype.byte = function (v) {
  this.room(1);
  this.b[this.n++] = v & 255;
};
Writer.prototype.bytes = function (arr) {
  this.room(arr.length);
  this.b.set(arr, this.n);
  this.n += arr.length;
};
Writer.prototype.vu = function (v) {
  v = v >>> 0;
  do {
    var c = v & 127;
    v >>>= 7;
    this.byte(v ? c | 128 : c);
  } while (v);
};
Writer.prototype.vi = function (v) {
  this.vu((v << 1) ^ (v >> 31));
};
Writer.prototype.vu64 = function (v) {
  v = Math.max(0, Math.floor(v));
  do {
    var c = v % 128;
    v = Math.floor(v / 128);
    this.byte(v ? c | 128 : c);
  } while (v);
};
var f32 = new Float32Array(1);
var u32 = new Uint32Array(f32.buffer);
Writer.prototype.f = function (v) {
  f32[0] = v;
  var bits = u32[0];
  bits = (bits >>> 23) | (bits << 9);
  if ((bits & 255) === 0) return this.byte(0);
  this.byte(bits);
  this.byte(bits >>> 8);
  this.byte(bits >>> 16);
  this.byte(bits >>> 24);
};
var utf8 = new TextEncoder();
Writer.prototype.s = function (v) {
  this.bytes(utf8.encode(String(v)));
  this.byte(0);
};

function writeValue(defs, w, type, v, path) {
  switch (type) {
    case -1: return w.byte(v ? 1 : 0);
    case -2: return w.byte(v);
    case -3: return w.vi(v | 0);
    case -4: return w.vu(v);
    case -5: return w.f(+v || 0);
    case -6: return w.s(v == null ? "" : v);
    case -7: return w.vu64(v < 0 ? -2 * v - 1 : 2 * v);
    case -8: return w.vu64(v);
  }
  var def = defs[type];
  if (!def) throw new Error("kiwi: type " + type);
  if (def.kind === 0) {
    var ef = typeof v === "number" ? def.byValue[v] : def.byName[v];
    if (!ef) throw new Error("kiwi: " + def.name + "." + v + " (" + path + ")");
    return w.vu(ef.value);
  }
  v = v || {};
  if (def.kind === 1) {
    def.fields.forEach(function (f) {
      writeField(defs, w, f, v[f.name], path + "." + f.name);
    });
    return;
  }
  Object.keys(v).forEach(function (k) {
    if (v[k] == null) return;
    var f = def.byName[k];
    if (!f) throw new Error("kiwi: нет поля " + def.name + "." + k);
  });
  def.fields.forEach(function (f) {
    if (v[f.name] == null) return;
    w.vu(f.value);
    writeField(defs, w, f, v[f.name], path + "." + f.name);
  });
  w.vu(0);
}

function writeField(defs, w, f, v, path) {
  if (!f.array) return writeValue(defs, w, f.type, v, path);
  var arr = v || [];
  w.vu(arr.length);
  if (f.type === -2) return w.bytes(arr);
  for (var i = 0; i < arr.length; i++) writeValue(defs, w, f.type, arr[i], path + "[" + i + "]");
}

/** Сообщение по схеме → байты kiwi. defs — readSchema (board-figma.mjs). */
export function encodeKiwi(defs, rootName, msg) {
  defs.forEach(function (d) {
    if (d.byName) return;
    d.byName = {};
    d.fields.forEach(function (f) {
      d.byName[f.name] = f;
    });
  });
  var root = defs.findIndex(function (d) {
    return d.name === rootName;
  });
  if (root < 0) throw new Error("kiwi: нет " + rootName);
  var w = new Writer();
  writeValue(defs, w, root, msg, rootName);
  return w.b.slice(0, w.n);
}

/** zstd без сжатия (сырые блоки) — валидный кадр, который читает любой распаковщик, в том числе Figma. */
export function zstdRaw(data) {
  var BLOCK = 1 << 17;
  var nb = Math.max(1, Math.ceil(data.length / BLOCK));
  var out = new Uint8Array(4 + 1 + 4 + nb * 3 + data.length);
  var i = 0;
  [0x28, 0xb5, 0x2f, 0xfd].forEach(function (c) {
    out[i++] = c;
  });
  out[i++] = 0xa0; // один сегмент, размер содержимого — 4 байта, без контрольной суммы
  var n = data.length;
  out[i++] = n & 255;
  out[i++] = (n >>> 8) & 255;
  out[i++] = (n >>> 16) & 255;
  out[i++] = (n >>> 24) & 255;
  for (var b = 0; b < nb; b++) {
    var part = data.subarray(b * BLOCK, Math.min(n, (b + 1) * BLOCK));
    var h = (part.length << 3) | (b === nb - 1 ? 1 : 0); // тип блока 0 — сырой
    out[i++] = h & 255;
    out[i++] = (h >>> 8) & 255;
    out[i++] = (h >>> 16) & 255;
    out.set(part, i);
    i += part.length;
  }
  return out;
}

/** fig-kiwi: «fig-kiwi», версия, куски [длина][данные]. */
export function figKiwi(version, chunks) {
  var len = 12 + chunks.reduce(function (s, c) {
    return s + 4 + c.length;
  }, 0);
  var out = new Uint8Array(len);
  var dv = new DataView(out.buffer);
  "fig-kiwi".split("").forEach(function (c, k) {
    out[k] = c.charCodeAt(0);
  });
  dv.setUint32(8, version, true);
  var off = 12;
  chunks.forEach(function (c) {
    dv.setUint32(off, c.length, true);
    out.set(c, off + 4);
    off += 4 + c.length;
  });
  return out;
}

export function b64(bytes) {
  var s = "";
  for (var i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

// ---- цвета и стили доски ----
var FILL_HEX = {
  white: "#ffffff", gray: "#f1f3f5", red: "#ffe3e3", orange: "#ffe8cc", yellow: "#fff3bf", lime: "#e9fac8", green: "#d3f9d8",
  teal: "#c3fae8", cyan: "#c5f6fa", blue: "#d0ebff", indigo: "#dbe4ff", purple: "#e5dbff", pink: "#ffdeeb", brown: "#efe3d6",
};
var INK_HEX = {
  ink: "#1f2328", gray: "#868e96", red: "#e03131", orange: "#f08c00", yellow: "#f59f00", green: "#2f9e44", teal: "#0c8599",
  blue: "#1971c2", purple: "#7048e8", pink: "#c2255c", white: "#ffffff",
};

function isHex(v) {
  return typeof v === "string" && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(v);
}

function rgba(hex, a) {
  var h = String(hex).replace("#", "");
  return {
    r: parseInt(h.slice(0, 2), 16) / 255, g: parseInt(h.slice(2, 4), 16) / 255, b: parseInt(h.slice(4, 6), 16) / 255,
    a: a != null ? a : h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1,
  };
}

function solid(hex, opacity) {
  return { type: "SOLID", color: rgba(hex.slice(0, 7), 1), opacity: opacity == null ? 1 : opacity, visible: true, blendMode: "NORMAL" };
}

var GRAD_RE = /^(linear|radial) (-?\d{1,3}) ((?:#[0-9a-f]{8}@\d{1,3} ?){2,8})$/i;

/** «linear|radial угол #rrggbbaa@поз …» → градиент Figma (обратное к gradOf в board-figma.mjs). */
function gradPaint(str) {
  var m = GRAD_RE.exec(String(str || "").trim());
  if (!m) return null;
  var stops = m[3].trim().split(" ").map(function (t) {
    var q = t.split("@");
    return { color: rgba(q[0]), position: Math.max(0, Math.min(100, Number(q[1]))) / 100 };
  }).sort(function (a, b) {
    return a.position - b.position;
  });
  var radial = m[1].toLowerCase() === "radial";
  var th = (Number(m[2]) * Math.PI) / 180;
  var c = Math.cos(th);
  var s = Math.sin(th);
  // На доске линейный — слева направо, повёрнутый вокруг центра; у Figma transform — из рамки фигуры в пространство градиента.
  var T = radial ? { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 }
    : { m00: c, m01: s, m02: 0.5 - 0.5 * c - 0.5 * s, m10: -s, m11: c, m12: 0.5 + 0.5 * s - 0.5 * c };
  return { type: radial ? "GRADIENT_RADIAL" : "GRADIENT_LINEAR", stops: stops, transform: T, opacity: 1, visible: true, blendMode: "NORMAL" };
}

var SHADOW_RE = /^(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (#[0-9a-f]{8})$/i;
var WEIGHTS = { 100: "Thin", 200: "Extra Light", 300: "Light", 400: "Regular", 500: "Medium", 600: "Semi Bold", 700: "Bold", 800: "Extra Bold", 900: "Black" };
var ALIGN_H = { left: "LEFT", center: "CENTER", right: "RIGHT" };
var SIDE = { start: "MIN", center: "CENTER", end: "MAX" };
var PIN_H = { l: "MIN", r: "MAX", lr: "STRETCH", c: "CENTER", s: "SCALE" };
var PIN_V = { t: "MIN", b: "MAX", tb: "STRETCH", c: "CENTER", s: "SCALE" };
var CAP = { round: "ROUND", square: "SQUARE" };
var JOIN = { round: "ROUND", bevel: "BEVEL" };

function r2(v) {
  return Math.round(v * 100) / 100;
}

// ---- матрицы ----
function mul(a, b) {
  return {
    m00: a.m00 * b.m00 + a.m01 * b.m10, m01: a.m00 * b.m01 + a.m01 * b.m11, m02: a.m00 * b.m02 + a.m01 * b.m12 + a.m02,
    m10: a.m10 * b.m00 + a.m11 * b.m10, m11: a.m10 * b.m01 + a.m11 * b.m11, m12: a.m10 * b.m02 + a.m11 * b.m12 + a.m12,
  };
}

function inv(m) {
  var d = m.m00 * m.m11 - m.m01 * m.m10 || 1;
  return { m00: m.m11 / d, m01: -m.m01 / d, m10: -m.m10 / d, m11: m.m00 / d,
    m02: (m.m01 * m.m12 - m.m11 * m.m02) / d, m12: (m.m10 * m.m02 - m.m00 * m.m12) / d };
}

/** Рамка доски (x, y, w, h, поворот вокруг центра) → матрица в мировых координатах. */
function worldOf(b) {
  var th = ((b.rot || 0) * Math.PI) / 180;
  var c = Math.cos(th);
  var s = Math.sin(th);
  var cx = b.x + b.w / 2;
  var cy = b.y + b.h / 2;
  return { m00: c, m01: -s, m02: cx - (c * b.w) / 2 + (s * b.h) / 2, m10: s, m11: c, m12: cy - (s * b.w) / 2 - (c * b.h) / 2 };
}

function clean(m) {
  var o = {};
  Object.keys(m).forEach(function (k) {
    var v = Math.round(m[k] * 10000) / 10000;
    o[k] = Object.is(v, -0) ? 0 : v;
  });
  return o;
}

// ---- контуры векторов → сеть вершин (vectorNetworkBlob; обратное к readNetwork) ----
/** d (M L H V C Q Z, в том числе относительные) → подконтуры [{pts: [[x,y]…], segs: [{a,b,ta,tb}], closed}]. */
function parsePath(d) {
  var toks = String(d || "").match(/[MLHVCQZmlhvcqz]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?/g) || [];
  var subs = [];
  var cur = null;
  var x = 0;
  var y = 0;
  var i = 0;
  var cmd = "";
  var num = function () {
    return Number(toks[i++]);
  };
  var vert = function (px, py) {
    var last = cur.pts[cur.pts.length - 1];
    if (last && Math.abs(last[0] - px) < 1e-6 && Math.abs(last[1] - py) < 1e-6) return cur.pts.length - 1;
    cur.pts.push([px, py]);
    return cur.pts.length - 1;
  };
  while (i < toks.length) {
    if (/[A-Za-z]/.test(toks[i])) cmd = toks[i++];
    var rel = cmd === cmd.toLowerCase();
    var C = cmd.toUpperCase();
    if (C === "Z") {
      if (cur && cur.pts.length > 1) {
        var a0 = cur.pts.length - 1;
        var f0 = cur.pts[0];
        if (Math.abs(f0[0] - cur.pts[a0][0]) > 1e-6 || Math.abs(f0[1] - cur.pts[a0][1]) > 1e-6) cur.segs.push({ a: a0, b: 0, ta: [0, 0], tb: [0, 0] });
        else if (cur.segs.length) {
          // Последняя точка совпала с первой — замыкаем на неё.
          cur.segs[cur.segs.length - 1].b = 0;
          cur.pts.pop();
        }
        cur.closed = true;
        x = f0[0];
        y = f0[1];
      }
      cur = null;
      continue;
    }
    if (!/^-?[\d.]/.test(toks[i] || "")) {
      i++;
      continue;
    }
    if (C === "M") {
      x = (rel ? x : 0) + num();
      y = (rel ? y : 0) + num();
      cur = { pts: [[x, y]], segs: [], closed: false };
      subs.push(cur);
      cmd = rel ? "l" : "L";
      continue;
    }
    if (!cur) {
      cur = { pts: [[x, y]], segs: [], closed: false };
      subs.push(cur);
    }
    var a = cur.pts.length - 1;
    var A = cur.pts[a];
    if (C === "L" || C === "H" || C === "V") {
      var nx = C === "V" ? x : (rel ? x : 0) + num();
      var ny = C === "H" ? y : (rel ? y : 0) + num();
      x = nx;
      y = ny;
      cur.segs.push({ a: a, b: vert(x, y), ta: [0, 0], tb: [0, 0] });
    } else if (C === "C") {
      var c1 = [(rel ? x : 0) + num(), (rel ? y : 0) + num()];
      var c2 = [(rel ? x : 0) + num(), (rel ? y : 0) + num()];
      x = (rel ? x : 0) + num();
      y = (rel ? y : 0) + num();
      cur.segs.push({ a: a, b: vert(x, y), ta: [c1[0] - A[0], c1[1] - A[1]], tb: [c2[0] - x, c2[1] - y] });
    } else if (C === "Q") {
      var q = [(rel ? x : 0) + num(), (rel ? y : 0) + num()];
      x = (rel ? x : 0) + num();
      y = (rel ? y : 0) + num();
      // Квадратичная кривая → кубическая (касательные — 2/3 до контрольной точки).
      cur.segs.push({ a: a, b: vert(x, y), ta: [((q[0] - A[0]) * 2) / 3, ((q[1] - A[1]) * 2) / 3], tb: [((q[0] - x) * 2) / 3, ((q[1] - y) * 2) / 3] });
    } else i++;
  }
  return subs.filter(function (s) {
    return s.segs.length;
  });
}

/** Подконтуры → байты сети вершин в системе координат узла (k — масштаб, ox/oy — сдвиг). */
function networkBlob(subs, k, ox, oy, fillRule) {
  var verts = [];
  var segs = [];
  var loops = [];
  subs.forEach(function (s) {
    var base = verts.length;
    s.pts.forEach(function (p) {
      verts.push([(p[0] - ox) * k[0], (p[1] - oy) * k[1]]);
    });
    var first = segs.length;
    s.segs.forEach(function (g) {
      segs.push({ a: base + g.a, b: base + g.b, ta: [g.ta[0] * k[0], g.ta[1] * k[1]], tb: [g.tb[0] * k[0], g.tb[1] * k[1]] });
    });
    if (s.closed) loops.push(s.segs.map(function (_, j) {
      return first + j;
    }));
  });
  var regions = fillRule ? (loops.length ? [loops] : []) : [];
  var size = 12 + verts.length * 12 + segs.length * 28 + regions.reduce(function (sum, r) {
    return sum + 8 + r.reduce(function (s2, l) {
      return s2 + 4 + l.length * 4;
    }, 0);
  }, 0);
  var out = new Uint8Array(size);
  var dv = new DataView(out.buffer);
  var i = 0;
  var u = function (v) {
    dv.setUint32(i, v >>> 0, true);
    i += 4;
  };
  var f = function (v) {
    dv.setFloat32(i, v, true);
    i += 4;
  };
  u(verts.length);
  u(segs.length);
  u(regions.length);
  verts.forEach(function (v) {
    u(0);
    f(v[0]);
    f(v[1]);
  });
  segs.forEach(function (g) {
    u(0);
    u(g.a);
    f(g.ta[0]);
    f(g.ta[1]);
    u(g.b);
    f(g.tb[0]);
    f(g.tb[1]);
  });
  regions.forEach(function (r) {
    u(fillRule === "evenodd" ? 0 : 1); // младший бит: 1 — NONZERO, 0 — EVENODD (как читает readNetwork)
    u(r.length);
    r.forEach(function (l) {
      u(l.length);
      l.forEach(u);
    });
  });
  return out;
}

function pathBounds(subs) {
  var x0 = Infinity;
  var y0 = Infinity;
  var x1 = -Infinity;
  var y1 = -Infinity;
  subs.forEach(function (s) {
    s.pts.forEach(function (p) {
      x0 = Math.min(x0, p[0]);
      y0 = Math.min(y0, p[1]);
      x1 = Math.max(x1, p[0]);
      y1 = Math.max(y1, p[1]);
    });
  });
  return isFinite(x0) ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
}

// ---- элементы макета → NodeChange[] ----
/**
 * elements — элементы доски (как serializeEl в board.js: x/y/w/h в координатах листа, frame — родитель, порядок — снизу вверх).
 * opts.ink(k) / opts.fill(k) — цвета палитры доски в hex; opts.textColor(el) — цвет текста как на экране (автоконтраст);
 * opts.faces(family, style) — загруженные шрифты для раскладки текста (board-figma-font.mjs), нет — текст без глифов.
 * → { message, topIds } — сообщение для схемы Message.
 */
export function buildMessage(elements, opts) {
  opts = opts || {};
  var ink = function (k) {
    var v = opts.ink ? opts.ink(k) : null;
    return isHex(v) ? v : isHex(k) ? k : INK_HEX[k] || INK_HEX.ink;
  };
  var fill = function (k) {
    if (!k || k === "none") return null;
    var v = opts.fill ? opts.fill(k) : null;
    return isHex(v) ? v : isHex(k) ? k : FILL_HEX[k] || "#ffffff";
  };
  var textColor = function (el) {
    var v = opts.textColor ? opts.textColor(el) : null;
    if (isHex(v)) return v;
    var tc = el.style && el.style.textColor;
    return isHex(tc) ? tc : ink(tc || "ink");
  };
  var byId = {};
  elements.forEach(function (el) {
    byId[el.id] = el;
  });
  // Компоненты: мастера экземпляров, которых нет среди скопированного, — во «внутренний» холст
  // (как делает сама Figma), чтобы экземпляры остались экземплярами.
  var extra = (opts.masters || []).filter(function (el) {
    if (byId[el.id]) return false;
    byId[el.id] = el;
    return true;
  });
  var kids = {};
  var tops = [];
  var hidden = [];
  elements.concat(extra).forEach(function (el, i) {
    if (el.frame && byId[el.frame]) (kids[el.frame] = kids[el.frame] || []).push(el);
    else if (i < elements.length) tops.push(el);
    else hidden.push(el);
  });
  var nodes = [];
  var blobs = [];
  var local = 1;
  var newGuid = function () {
    return { sessionID: 1, localID: ++local };
  };
  // guid каждого элемента заранее: экземпляр ссылается на мастер и его слои, даже если мастер идёт позже.
  var elGuid = {};
  Object.keys(byId).forEach(function (id) {
    elGuid[id] = newGuid();
  });
  var dry = false; // «сухая» сборка узла — только чтобы взять его поля для правок экземпляра
  var pendingText = []; // готовая раскладка текста для правок экземпляров (derivedSymbolData)
  var setDefs = {}; // фрейм набора вариантов → {свойство: id описания}
  var DOC = { sessionID: 0, localID: 0 };
  var PAGE = { sessionID: 0, localID: 1 };
  nodes.push({ guid: DOC, phase: "CREATED", type: "DOCUMENT", name: "Document", visible: true, opacity: 1, transform: { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 } });
  nodes.push({ guid: PAGE, phase: "CREATED", parentIndex: { guid: DOC, position: "!" }, type: "CANVAS", name: "Page 1", visible: true, opacity: 1,
    transform: { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 }, backgroundOpacity: 1, backgroundEnabled: true });
  var topGuids = [];

  var posKey = function (i, n) {
    if (n <= 94) return String.fromCharCode(0x21 + i);
    return String.fromCharCode(0x21 + Math.floor(i / 94)) + String.fromCharCode(0x21 + (i % 94));
  };

  /** Заливка, обводка, скругления, прозрачность, эффекты (обратное к styleOf). */
  function paint(n, el, kind) {
    var st = el.style || {};
    var g = !st.fillOff && gradPaint(st.fillGrad);
    var fl = st.fillOff ? null : kind === "frame" ? fill(st.fill || "#ffffff") : fill(st.fill);
    if (g) n.fillPaints = [g];
    else if (fl) n.fillPaints = [solid(fl, st.fillOpacity != null ? Number(st.fillOpacity) : 1)];
    else n.fillPaints = [];
    var sw = st.strokeOff ? 0 : st.strokeWidth != null && isFinite(Number(st.strokeWidth)) ? Number(st.strokeWidth) : kind === "frame" ? 0 : 2;
    if (sw > 0) {
      var sg = gradPaint(st.strokeGrad);
      n.strokePaints = [sg || solid(ink(st.stroke || "ink"), st.strokeOpacity != null ? Number(st.strokeOpacity) : 1)];
      n.strokeWeight = sw;
      n.strokeAlign = st.strokeAlign === "inside" ? "INSIDE" : st.strokeAlign === "outside" ? "OUTSIDE" : "CENTER";
      n.strokeJoin = "MITER";
      if (st.dash === "dashed") n.dashPattern = [sw * 4, sw * 3];
      else if (st.dash === "dotted") n.dashPattern = [0.1, sw * 2.5];
    }
    if (kind === "rect" || kind === "frame") {
      var m = /^(\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)$/.exec(String(st.radii || ""));
      var lim = Math.max(0, Math.min(el.w, el.h) / 2);
      var one = st.radius != null && isFinite(st.radius) ? Number(st.radius) : kind === "frame" ? 0 : Math.min(10, el.w / 4, el.h / 4);
      var r = (m ? [m[1], m[2], m[3], m[4]].map(Number) : [one, one, one, one]).map(function (v) {
        return r2(Math.max(0, Math.min(lim, v)));
      });
      if (m) n.rectangleCornerRadiiIndependent = true;
      else n.cornerRadius = r[0];
      n.rectangleTopLeftCornerRadius = r[0];
      n.rectangleTopRightCornerRadius = r[1];
      n.rectangleBottomRightCornerRadius = r[2];
      n.rectangleBottomLeftCornerRadius = r[3];
    }
    if (st.opacity != null && Number(st.opacity) < 1) n.opacity = Math.max(0, Number(st.opacity));
    var fx = [];
    var sh = SHADOW_RE.exec(String(st.shadow || ""));
    if (sh) {
      fx.push({ type: "DROP_SHADOW", color: rgba(sh[4]), offset: { x: Number(sh[1]), y: Number(sh[2]) }, radius: Number(sh[3]), spread: 0,
        visible: true, blendMode: "NORMAL", showShadowBehindNode: false });
    }
    if (Number(st.blur) > 0) fx.push({ type: "FOREGROUND_BLUR", radius: Number(st.blur), visible: true });
    if (Number(st.bgBlur) > 0) fx.push({ type: "BACKGROUND_BLUR", radius: Number(st.bgBlur), visible: true });
    if (fx.length) n.effects = fx;
  }

  function textProps(n, el, fs) {
    var st = el.style || {};
    var wt = Number(st.fontWeight) || (st.bold ? 600 : 400);
    wt = Math.max(100, Math.min(900, Math.round(wt / 100) * 100));
    var fam = st.fontFamily && /^[A-Za-z0-9 ]{1,40}$/.test(st.fontFamily) ? st.fontFamily : "Inter";
    var style = WEIGHTS[wt];
    n.fontSize = fs;
    n.fontName = { family: fam, style: style, postscript: fam.replace(/\s+/g, "") + "-" + style.replace(/\s+/g, "") };
    n.lineHeight = { value: r2(Number(st.lineHeight) > 0 ? Number(st.lineHeight) : fs * 1.3), units: "PIXELS" };
    n.letterSpacing = { value: Number(st.letterSpacing) || 0, units: "PERCENT" };
    if (ALIGN_H[st.align]) n.textAlignHorizontal = ALIGN_H[st.align];
    if (st.deco === "underline") n.textDecoration = "UNDERLINE";
    else if (st.deco === "strike") n.textDecoration = "STRIKETHROUGH";
    if (st.textCase === "upper") n.textCase = "UPPER";
    else if (st.textCase === "lower") n.textCase = "LOWER";
    else if (st.textCase === "title") n.textCase = "TITLE";
    var chars = String(el.text || "");
    n.textData = { characters: chars, lines: chars.split("\n").map(function () {
      return { lineType: "PLAIN", styleId: 0, indentationLevel: 0, sourceDirectionality: "AUTO", listStartOffset: 0, isFirstLineOfList: false };
    }) };
    n.fillPaints = [solid(textColor(el))];
    n.fontVariantCommonLigatures = true;
    n.fontVariantContextualLigatures = true;
    n.autoRename = false;
  }

  /** Узел с общими полями: место у родителя, рамка, имя, видимость, поведение в автолэйауте. */
  function base(el, type, parent, b) {
    var own = !dry && elGuid[el.id] && !elGuid[el.id].used ? elGuid[el.id] : null;
    if (own) own.used = true;
    var n = { guid: own ? { sessionID: own.sessionID, localID: own.localID } : newGuid(), phase: "CREATED", parentIndex: { guid: parent.guid, position: "" }, type: type, name: String(el.name || defaultName(el)).slice(0, 120),
      visible: !el.hidden, opacity: 1, size: { x: r2(Math.max(0.01, b.w)), y: r2(Math.max(0.01, b.h)) },
      transform: clean(mul(inv(parent.world), worldOf(b))) };
    if (el.locked) n.locked = true;
    if (el.ratio) n.proportionsConstrained = true;
    var L = parent.layout;
    if (L) {
      var g = String(el.grow || "");
      var main = L.dir === "h" ? "w" : "h";
      var cross = L.dir === "h" ? "h" : "w";
      if (g.indexOf(main) >= 0) n.stackChildPrimaryGrow = 1;
      if (g.indexOf(cross) >= 0) n.stackChildAlignSelf = "STRETCH";
    } else if (parent.el) {
      // Привязки к краям фрейма (pin «h v») — только вне автолэйаута.
      var p = String(el.pin || "").split(" ");
      n.horizontalConstraint = PIN_H[p[0]] || "MIN";
      n.verticalConstraint = PIN_V[p[1]] || "MIN";
    }
    if (el.minW > 0 || el.minH > 0) n.minSize = { value: { x: el.minW || 0, y: el.minH || 0 } };
    if (el.maxW > 0 || el.maxH > 0) n.maxSize = { value: { x: el.maxW || 0, y: el.maxH || 0 } };
    n.overrideKey = { sessionID: n.guid.sessionID, localID: n.guid.localID };
    if (!dry) nodes.push(n);
    return n;
  }

  function defaultName(el) {
    if (el.type === "text") return String(el.text || "Текст").split("\n")[0].slice(0, 60) || "Текст";
    return { frame: "Фрейм", rect: "Прямоугольник", ellipse: "Эллипс", vector: "Вектор", image: "Картинка", line: "Линия", pen: "Линия", sticky: "Стикер" }[el.type] || el.type;
  }

  function parseLayout(str) {
    var p = String(str || "").trim().split(/\s+/);
    if (p[0] !== "h" && p[0] !== "v") return null; // сетку (g) Figma понимает иначе — переносим без автолэйаута
    var n = function (i) {
      var v = Number(p[i]);
      return isFinite(v) && v >= 0 ? v : 0;
    };
    return { dir: p[0], gap: n(1), pad: [n(2), n(3), n(4), n(5)], align: p[6] || "start", justify: p[7] || "start", hug: p[8] || "none", wrap: p[9] === "wrap" };
  }

  /** Названия «Свойство=Значение, …» → {свойство: значение} (как в наборах вариантов Figma). */
  function variantProps(name) {
    var out = {};
    String(name || "").split(",").forEach(function (part) {
      var i = part.indexOf("=");
      if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
    });
    return out;
  }

  function isMaster(el) {
    return el && el.type === "frame" && el.comp;
  }

  function frameNode(el, parent) {
    var isInst = !dry && el.inst && isMaster(byId[el.inst]);
    var type = dry ? "FRAME" : isMaster(el) ? "SYMBOL" : isInst ? "INSTANCE" : "FRAME";
    var n = base(el, type, parent, el);
    paint(n, el, "frame");
    if (type === "SYMBOL" && parent.el && setDefs[parent.el.id]) {
      var pr = variantProps(el.name);
      var defs = setDefs[parent.el.id];
      n.variantPropSpecs = Object.keys(pr).filter(function (k) {
        return defs[k];
      }).map(function (k) {
        return { propDefId: defs[k], value: pr[k] };
      });
    }
    n.frameMaskDisabled = !!el.noclip;
    var L = parseLayout(el.layout);
    var list = kids[el.id] || [];
    if (L && list.some(function (k) {
      return k.type === "line" || k.type === "pen";
    })) L = null;
    if (L) {
      n.stackMode = L.dir === "h" ? "HORIZONTAL" : "VERTICAL";
      n.stackSpacing = L.gap;
      n.stackVerticalPadding = L.pad[0];
      n.stackPaddingRight = L.pad[1];
      n.stackPaddingBottom = L.pad[2];
      n.stackHorizontalPadding = L.pad[3];
      n.stackPrimaryAlignItems = L.justify === "between" ? "SPACE_BETWEEN" : SIDE[L.justify] || "MIN";
      n.stackCounterAlignItems = SIDE[L.align] || "MIN";
      var hw = L.hug.indexOf("w") >= 0;
      var hh = L.hug.indexOf("h") >= 0;
      var fitM = L.dir === "h" ? hw : hh;
      var fitC = L.dir === "h" ? hh : hw;
      n.stackPrimarySizing = fitM ? "RESIZE_TO_FIT_WITH_IMPLICIT_SIZE" : "FIXED";
      n.stackCounterSizing = fitC ? "RESIZE_TO_FIT_WITH_IMPLICIT_SIZE" : "FIXED";
      if (L.wrap && L.dir === "h") n.stackWrap = "WRAP";
      // Порядок в потоке — по положению (как раскладывает доска), а не по слоям.
      list = list.slice().sort(L.wrap ? function (a, b) {
        var d = a.y + a.h / 2 - (b.y + b.h / 2);
        return Math.abs(d) > Math.min(a.h, b.h) / 2 ? d : a.x - b.x;
      } : function (a, b) {
        return L.dir === "h" ? a.x - b.x : a.y - b.y;
      });
    }
    var me = { guid: n.guid, world: worldOf(el), layout: L, el: el };
    if (dry) return n;
    if (type === "INSTANCE") {
      instanceData(n, el, me);
      return n;
    }
    // Набор вариантов: фрейм, в котором только мастера (больше одного).
    if (type === "FRAME" && list.length > 1 && list.every(isMaster)) {
      var defs = {};
      var names = [];
      list.forEach(function (k) {
        Object.keys(variantProps(k.name)).forEach(function (p) {
          if (names.indexOf(p) < 0) names.push(p);
        });
      });
      if (names.length) {
        n.isStateGroup = true;
        n.componentPropDefs = names.map(function (p, i) {
          var id = newGuid();
          defs[p] = id;
          return { id: id, name: p, initialValue: {}, sortPosition: posKey(i, names.length), type: "VARIANT", preferredValues: {},
            varValue: { value: { textValue: "" }, dataType: "STRING", resolvedDataType: "STRING" } };
        });
        setDefs[el.id] = defs;
      }
    }
    children(me, list);
    return n;
  }

  /** Путь слоя мастера для правок экземпляра: guid слоя; слой внутри вложенного экземпляра — [вложенный, слой в его мастере]. */
  function keyPath(id, depth) {
    var e = byId[id];
    if (!e || depth > 6) return null;
    if (!e.msrc) return [elGuid[e.id]];
    var root = e;
    for (var i = 0; root && root.msrc && i < 40; i++) root = byId[root.frame];
    if (!root || !root.inst) return null;
    var inner = keyPath(e.msrc, depth + 1);
    return inner ? [elGuid[root.id]].concat(inner) : null;
  }

  function guidOnly(g) {
    return { sessionID: g.sessionID, localID: g.localID };
  }

  var OV_PAINT = ["fillPaints", "strokePaints", "strokeWeight", "strokeAlign", "dashPattern", "cornerRadius", "rectangleCornerRadiiIndependent",
    "rectangleTopLeftCornerRadius", "rectangleTopRightCornerRadius", "rectangleBottomRightCornerRadius", "rectangleBottomLeftCornerRadius", "opacity", "effects"];
  var OV_TEXT = ["fontSize", "fontName", "lineHeight", "letterSpacing", "textAlignHorizontal", "textDecoration", "textCase", "fillPaints"];

  /** Экземпляр: ссылка на мастер, свои правки (symbolOverrides) и готовые размеры слоёв (derivedSymbolData). */
  function instanceData(n, el, me) {
    var m = byId[el.inst];
    var overrides = [];
    var derived = [];
    var rootOv = { guidPath: { guids: [guidOnly(elGuid[m.id])] } };
    var rov = el.ovr || [];
    // Свой размер — только если его меняли руками; «обнимающий» экземпляр Figma пересчитает сама (иначе он застынет).
    if (rov.indexOf("geo") >= 0) {
      rootOv.size = { x: n.size.x, y: n.size.y };
      if (n.stackMode) n.stackPrimarySizing = rootOv.stackPrimarySizing = "FIXED";
    }
    if (rov.some(function (k) {
      return /^style\./.test(k);
    })) OV_PAINT.forEach(function (k) {
      if (n[k] != null) rootOv[k] = n[k];
    });
    if (Object.keys(rootOv).length > 1) overrides.push(rootOv);
    var parts = [];
    var stack = [el.id];
    while (stack.length) {
      (kids[stack.pop()] || []).forEach(function (k) {
        parts.push(k);
        stack.push(k.id);
      });
    }
    parts.forEach(function (p) {
      if (!p.msrc) return;
      var path = keyPath(p.msrc, 0);
      if (!path) return;
      var fe = byId[p.frame];
      var pinfo = { guid: n.guid, world: worldOf(fe), layout: parseLayout(fe.layout), el: fe };
      dry = true;
      var simple = p.type === "rect" || p.type === "ellipse" || p.type === "sticky";
      var dn = p.type === "text" ? textNode(p, pinfo) : p.type === "frame" ? frameNode(p, pinfo) : simple ? node(p, pinfo) : base(p, "VECTOR", pinfo, p);
      dry = false;
      if (!dn) return;
      var gp = { guids: path.map(guidOnly) };
      var ov = { guidPath: gp };
      var keys = p.ovr || [];
      if (keys.indexOf("text") >= 0 && dn.textData) ov.textData = dn.textData;
      if (keys.some(function (k) {
        return /^style\./.test(k);
      })) (p.type === "text" ? OV_TEXT : OV_PAINT).forEach(function (k) {
        if (dn[k] != null) ov[k] = dn[k];
      });
      if (keys.indexOf("hidden") >= 0 || p.hidden) ov.visible = !p.hidden;
      if (keys.indexOf("geo") >= 0) ov.size = dn.size;
      if (Object.keys(ov).length > 1) overrides.push(ov);
      var d = { guidPath: gp, size: dn.size, transform: dn.transform };
      derived.push(d);
      if (dn.type === "TEXT") pendingText.push({ d: d, n: dn });
    });
    n.symbolData = { symbolID: guidOnly(elGuid[m.id]), symbolOverrides: overrides, uniformScaleFactor: 1 };
    n.derivedSymbolData = derived;
    n.derivedSymbolDataLayoutVersion = 3;
  }

  function textNode(el, parent) {
    var st = el.style || {};
    var n = base(el, "TEXT", parent, el);
    textProps(n, el, Number(st.fontSize) || 16);
    n.textAutoResize = el.autoW ? "WIDTH_AND_HEIGHT" : el.fixH ? "NONE" : "HEIGHT";
    if (st.valign === "bottom") n.textAlignVertical = "BOTTOM";
    else if (st.valign === "middle" || st.valign === "center") n.textAlignVertical = "CENTER";
    if (st.opacity != null && Number(st.opacity) < 1) n.opacity = Math.max(0, Number(st.opacity));
    return n;
  }

  function shapeNode(el, parent, type) {
    var n = base(el, type, parent, el);
    paint(n, el, type === "ELLIPSE" ? "ellipse" : "rect");
    return n;
  }

  /** Фигура с подписью (прямоугольник, эллипс, стикер): фрейм без заливки — фигура и текст по центру. */
  function labelled(el, parent) {
    var st = el.style || {};
    var wrap = base(el, "FRAME", parent, el);
    wrap.fillPaints = [];
    wrap.frameMaskDisabled = true;
    var me = { guid: wrap.guid, world: worldOf(el), layout: null, el: el };
    var shape;
    var sub = { id: el.id + ":s", x: el.x, y: el.y, w: el.w, h: el.h, rot: el.rot, style: st, name: el.type === "sticky" ? "Стикер" : "Фигура", pin: "lr tb" };
    if (el.type === "sticky") {
      shape = base(sub, "ROUNDED_RECTANGLE", me, sub);
      shape.fillPaints = [solid(fill(st.fill || "yellow"))];
      shape.cornerRadius = 3;
    } else shape = shapeNode(sub, me, el.type === "ellipse" ? "ELLIPSE" : "ROUNDED_RECTANGLE");
    shape.parentIndex.position = "!";
    var padX = el.type === "sticky" ? 14 : 20;
    var tb = { id: el.id + ":t", type: "text", text: el.text, x: el.x + padX, y: el.y + 8, w: Math.max(1, el.w - padX * 2), h: Math.max(1, el.h - 16), rot: el.rot,
      style: Object.assign({}, st, { align: st.align || "center", opacity: undefined }), pin: "lr tb" };
    var tx = base(tb, "TEXT", me, tb);
    textProps(tx, Object.assign({}, el, { style: tb.style }), Number(st.fontSize) || 16);
    tx.name = String(el.text).split("\n")[0].slice(0, 60);
    tx.textAutoResize = "NONE";
    tx.textAlignVertical = st.valign === "top" ? "TOP" : st.valign === "bottom" ? "BOTTOM" : "CENTER";
    tx.parentIndex.position = '"';
    if (st.opacity != null && Number(st.opacity) < 1) wrap.opacity = Math.max(0, Number(st.opacity));
    return wrap;
  }

  /** Контур(ы) → VECTOR; несколько контуров со своими цветами — фрейм с векторами. */
  function vectorNodes(el, parent, paths, k, ox, oy, b) {
    var st = el.style || {};
    var one = function (p, holder, box) {
      var subs = parsePath(p.d);
      if (!subs.length) return null;
      var closed = subs.some(function (s) {
        return s.closed;
      });
      var fl = st.fillOff ? null : p.fill ? fill(st.fill && st.fill !== "none" ? st.fill : p.fill) : st.fill && st.fill !== "none" && closed ? fill(st.fill) : null;
      if (p.fill && st.fill === "none") fl = null;
      var swRaw = st.strokeWidth != null ? Number(st.strokeWidth) : p.sw || 1;
      var stc = p.stroke && !st.strokeOff && st.strokeWidth !== 0 ? ink(st.stroke || p.stroke) : null;
      var n = base(Object.assign({}, el, { name: holder === parent ? el.name : "Контур", grow: holder === parent ? el.grow : "", pin: holder === parent ? el.pin : "s s" }), "VECTOR", holder, box);
      blobs.push({ bytes: networkBlob(subs, k, ox, oy, fl ? p.rule || "nonzero" : null) });
      n.vectorData = { vectorNetworkBlob: blobs.length - 1, normalizedSize: { x: n.size.x, y: n.size.y } };
      n.fillPaints = fl ? [solid(fl, st.fillOpacity != null ? Number(st.fillOpacity) : 1)] : [];
      if (stc) {
        n.strokePaints = [solid(stc, st.strokeOpacity != null ? Number(st.strokeOpacity) : 1)];
        n.strokeWeight = r2(swRaw * (k[0] + k[1]) / 2);
        n.strokeAlign = "CENTER";
        n.strokeCap = CAP[p.cap] || "NONE";
        n.strokeJoin = JOIN[p.join] || "MITER";
        if (st.dash === "dashed") n.dashPattern = [n.strokeWeight * 4, n.strokeWeight * 3];
      } else n.strokeWeight = 0;
      return n;
    };
    var list = paths.filter(function (p) {
      return p && p.d;
    });
    if (list.length === 1) {
      var v = one(list[0], parent, b);
      if (v && st.opacity != null && Number(st.opacity) < 1) v.opacity = Math.max(0, Number(st.opacity));
      return v;
    }
    var wrap = base(el, "FRAME", parent, b);
    wrap.fillPaints = [];
    wrap.frameMaskDisabled = true;
    if (st.opacity != null && Number(st.opacity) < 1) wrap.opacity = Math.max(0, Number(st.opacity));
    var me = { guid: wrap.guid, world: worldOf(b), layout: null, el: el };
    list.forEach(function (p, i) {
      var v = one(p, me, b);
      if (v) v.parentIndex.position = posKey(i, list.length);
    });
    return wrap;
  }

  function node(el, parent) {
    var t = el.type;
    if (t === "frame") return frameNode(el, parent);
    if (t === "text") return textNode(el, parent);
    if ((t === "rect" || t === "ellipse" || t === "sticky") && el.text) return labelled(el, parent);
    if (t === "rect") return shapeNode(el, parent, "ROUNDED_RECTANGLE");
    if (t === "ellipse") return shapeNode(el, parent, "ELLIPSE");
    if (t === "sticky") {
      var sn = shapeNode(Object.assign({}, el, { style: { fill: (el.style || {}).fill || "yellow", radius: 3, strokeWidth: 0 } }), parent, "ROUNDED_RECTANGLE");
      return sn;
    }
    if (t === "vector") {
      var vb = el.vb || [el.w || 1, el.h || 1];
      return vectorNodes(el, parent, el.paths || [], [(el.w || 1) / (vb[0] || 1), (el.h || 1) / (vb[1] || 1)], 0, 0, el);
    }
    if (t === "line" || t === "pen") {
      var st = el.style || {};
      var d = t === "line" ? el.d : (el.points || []).map(function (q, i) {
        return (i ? "L" : "M") + (el.x + q[0]) + " " + (el.y + q[1]);
      }).join("");
      var bb = pathBounds(parsePath(d));
      if (!bb) return null;
      var lb = { x: bb.x, y: bb.y, w: Math.max(1, bb.w), h: Math.max(1, bb.h) };
      var sw = Number(st.strokeWidth) || (t === "pen" ? 3 : 2);
      var le = Object.assign({}, el, { style: { stroke: st.stroke || "ink", strokeWidth: sw, dash: st.dash, opacity: st.opacity }, rot: 0 });
      return vectorNodes(le, parent, [{ d: d, stroke: st.stroke || "ink", sw: sw, cap: "round", join: "round" }], [1, 1], bb.x, bb.y, lb);
    }
    // Картинки и прочие блоки — заглушкой с именем (картинки пока не вшиваем).
    var ph = base(Object.assign({}, el, { name: el.name || (t === "image" ? "Картинка" : defaultName(el)) }), "ROUNDED_RECTANGLE", parent, el);
    ph.fillPaints = [solid("#e5e7eb")];
    ph.cornerRadius = 0;
    return ph;
  }

  function children(parent, list) {
    var made = [];
    list.forEach(function (el) {
      var n = node(el, parent);
      if (n) made.push(n);
    });
    made.forEach(function (n, i) {
      n.parentIndex.position = posKey(i, made.length);
    });
    return made;
  }

  var root = { guid: PAGE, world: { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 }, layout: null, el: null };
  children(root, tops).forEach(function (n) {
    topGuids.push(n.guid);
  });
  if (hidden.length) {
    var INTERNAL = { sessionID: 0, localID: 2 };
    nodes.push({ guid: INTERNAL, phase: "CREATED", parentIndex: { guid: DOC, position: '"' }, type: "CANVAS", name: "Internal Only Canvas", visible: false,
      opacity: 1, transform: { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 }, backgroundOpacity: 1, backgroundEnabled: true, internalOnly: true });
    children({ guid: INTERNAL, world: { m00: 1, m01: 0, m02: 0, m10: 0, m11: 1, m12: 0 }, layout: null, el: null }, hidden);
  }

  // Готовая раскладка текстов (глифы) — без неё Figma показывает текстовый слой пустым, пока его не тронешь.
  if (opts.faces) {
    nodes.forEach(function (n) {
      if (n.type !== "TEXT") return;
      var dt = layoutText(n, opts.faces(n.fontName.family, n.fontName.style), function (bytes) {
        blobs.push({ bytes: bytes });
        return blobs.length - 1;
      });
      if (!dt) return;
      n.derivedTextData = dt;
      // Размер слоя — как у раскладки (по содержимому — ширина, «авто-высота» — высота).
      if (n.textAutoResize === "WIDTH_AND_HEIGHT") n.size = { x: dt.layoutSize.x, y: dt.layoutSize.y };
      else if (n.textAutoResize === "HEIGHT") n.size = { x: n.size.x, y: dt.layoutSize.y };
    });
    pendingText.forEach(function (t) {
      var dt = layoutText(t.n, opts.faces(t.n.fontName.family, t.n.fontName.style), function (bytes) {
        blobs.push({ bytes: bytes });
        return blobs.length - 1;
      });
      if (!dt) return;
      t.d.derivedTextData = dt;
      if (t.n.textAutoResize === "WIDTH_AND_HEIGHT") t.d.size = { x: dt.layoutSize.x, y: dt.layoutSize.y };
      else if (t.n.textAutoResize === "HEIGHT") t.d.size = { x: t.d.size.x, y: dt.layoutSize.y };
    });
  }

  var message = {
    type: "NODE_CHANGES", sessionID: 0, ackID: 0, pasteID: Math.floor(Math.random() * 2e9), pasteFileKey: opts.fileKey || "orbita",
    pasteIsPartiallyOutsideEnclosingFrame: false, pastePageId: PAGE, isCut: false, pasteEditorType: "DESIGN", publishedAssetGuids: [],
    clipboardSelectionRegions: [{ parent: PAGE, nodes: topGuids, pasteIsPartiallyOutsideEnclosingFrame: false, focusType: "NONE" }],
    pasteAssetType: "UNKNOWN", nodeChangeOrder: "GUID", nodeChanges: nodes, blobs: blobs,
  };
  return { message: message, topGuids: topGuids, types: topGuids.map(function (g) {
    return nodes.find(function (n) {
      return n.guid === g;
    }).type;
  }) };
}
