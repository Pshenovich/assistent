// Макет → Figma: готовая раскладка текста (derivedTextData). Figma при вставке рисует текст только из неё —
// без глифов слой есть, но пуст, пока его не тронешь. Поэтому раскладываем сами: шрифт (woff с Fontsource —
// те же Google Fonts, TrueType в zlib, читается без сторонних распаковщиков) → контуры букв и ширины → строки.

// ---- woff → таблицы TrueType ----
async function inflate(bytes) {
  var ds = new DecompressionStream("deflate");
  var buf = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(buf);
}

async function woffTables(bytes) {
  var dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (dv.getUint32(0) !== 0x774f4646) throw new Error("font: не woff");
  if (dv.getUint32(4) !== 0x00010000) throw new Error("font: не TrueType");
  var n = dv.getUint16(12);
  var out = {};
  for (var i = 0; i < n; i++) {
    var o = 44 + i * 20;
    var tag = String.fromCharCode(bytes[o], bytes[o + 1], bytes[o + 2], bytes[o + 3]);
    var off = dv.getUint32(o + 4);
    var comp = dv.getUint32(o + 8);
    var orig = dv.getUint32(o + 12);
    var raw = bytes.subarray(off, off + comp);
    out[tag] = comp < orig ? await inflate(raw) : raw;
  }
  return out;
}

function view(t) {
  return new DataView(t.buffer, t.byteOffset, t.byteLength);
}

/** Шрифт: единицы на em, вертикальные метрики, ширины, кодовая таблица и контуры (лениво). */
export async function parseFont(bytes) {
  var T = await woffTables(bytes);
  ["head", "hhea", "hmtx", "maxp", "cmap", "loca", "glyf"].forEach(function (k) {
    if (!T[k]) throw new Error("font: нет " + k);
  });
  var head = view(T.head);
  var hhea = view(T.hhea);
  var upm = head.getUint16(18);
  var longLoca = head.getInt16(50) === 1;
  var numGlyphs = view(T.maxp).getUint16(4);
  var nh = hhea.getUint16(34);
  var hm = view(T.hmtx);
  var adv = function (g) {
    return hm.getUint16(4 * Math.min(g, nh - 1));
  };
  var loca = view(T.loca);
  var gOff = function (g) {
    return longLoca ? loca.getUint32(g * 4) : loca.getUint16(g * 2) * 2;
  };
  var cmap = readCmap(view(T.cmap));
  var glyf = view(T.glyf);
  var cache = {};

  /** Контуры глифа в единицах шрифта: [[{x, y, on}]…]; составные — с подстановкой частей. */
  function contours(g, depth) {
    if (g >= numGlyphs || depth > 6) return [];
    var a = gOff(g);
    var b = gOff(g + 1);
    if (b <= a) return [];
    var nc = glyf.getInt16(a);
    var p = a + 10;
    if (nc >= 0) {
      var ends = [];
      for (var i = 0; i < nc; i++) ends.push(glyf.getUint16(p + i * 2));
      p += nc * 2;
      p += 2 + glyf.getUint16(p); // инструкции
      var np = nc ? ends[nc - 1] + 1 : 0;
      var flags = [];
      while (flags.length < np) {
        var f = glyf.getUint8(p++);
        flags.push(f);
        if (f & 8) {
          var r = glyf.getUint8(p++);
          for (var k = 0; k < r; k++) flags.push(f);
        }
      }
      var xs = [];
      var x = 0;
      flags.forEach(function (f2) {
        if (f2 & 2) {
          var dx = glyf.getUint8(p++);
          x += f2 & 16 ? dx : -dx;
        } else if (!(f2 & 16)) {
          x += glyf.getInt16(p);
          p += 2;
        }
        xs.push(x);
      });
      var y = 0;
      var pts = flags.map(function (f2, j) {
        if (f2 & 4) {
          var dy = glyf.getUint8(p++);
          y += f2 & 32 ? dy : -dy;
        } else if (!(f2 & 32)) {
          y += glyf.getInt16(p);
          p += 2;
        }
        return { x: xs[j], y: y, on: !!(f2 & 1) };
      });
      var out = [];
      var s = 0;
      ends.forEach(function (e) {
        out.push(pts.slice(s, e + 1));
        s = e + 1;
      });
      return out;
    }
    // Составной глиф: части со сдвигом (и масштабом).
    var res = [];
    for (;;) {
      var fl = glyf.getUint16(p);
      var gi = glyf.getUint16(p + 2);
      p += 4;
      var dx2;
      var dy2;
      if (fl & 1) {
        dx2 = glyf.getInt16(p);
        dy2 = glyf.getInt16(p + 2);
        p += 4;
      } else {
        dx2 = glyf.getInt8(p);
        dy2 = glyf.getInt8(p + 1);
        p += 2;
      }
      var m = [1, 0, 0, 1];
      var f2d = function (q) {
        return glyf.getInt16(q) / 16384;
      };
      if (fl & 8) {
        m[0] = m[3] = f2d(p);
        p += 2;
      } else if (fl & 64) {
        m[0] = f2d(p);
        m[3] = f2d(p + 2);
        p += 4;
      } else if (fl & 128) {
        m = [f2d(p), f2d(p + 2), f2d(p + 4), f2d(p + 6)];
        p += 8;
      }
      if (!(fl & 2)) dx2 = dy2 = 0; // привязка по точкам — редкость, пропускаем сдвиг
      contours(gi, depth + 1).forEach(function (c) {
        res.push(c.map(function (q) {
          return { x: m[0] * q.x + m[2] * q.y + dx2, y: m[1] * q.x + m[3] * q.y + dy2, on: q.on };
        }));
      });
      if (!(fl & 32)) break;
    }
    return res;
  }

  /** Контур глифа → блоб команд Figma (1 M, 2 L, 3 Q, 0 Z; float32, в долях em, ось Y вверх). */
  function blob(g) {
    if (cache[g] !== undefined) return cache[g];
    var cs = contours(g, 0);
    var parts = [];
    var cmd = function (c) {
      var args = Array.prototype.slice.call(arguments, 1);
      parts.push([c].concat(args.map(function (v) {
        return v / upm;
      })));
    };
    cs.forEach(function (c) {
      if (c.length < 2) return;
      var start = c.findIndex(function (q) {
        return q.on;
      });
      var pts = start < 0 ? null : c.slice(start).concat(c.slice(0, start));
      if (!pts) {
        // Все точки — контрольные: начинаем с середины первых двух.
        var m0 = { x: (c[0].x + c[1].x) / 2, y: (c[0].y + c[1].y) / 2, on: true };
        pts = [m0].concat(c.slice(1), [c[0]]);
      }
      cmd(1, pts[0].x, pts[0].y);
      var ctrl = null;
      for (var i = 1; i <= pts.length; i++) {
        var q = pts[i % pts.length];
        if (q.on) {
          if (ctrl) cmd(3, ctrl.x, ctrl.y, q.x, q.y);
          else cmd(2, q.x, q.y);
          ctrl = null;
        } else if (ctrl) {
          var mid = { x: (ctrl.x + q.x) / 2, y: (ctrl.y + q.y) / 2 };
          cmd(3, ctrl.x, ctrl.y, mid.x, mid.y);
          ctrl = q;
        } else ctrl = q;
      }
      parts.push([0]);
    });
    if (!parts.length) return (cache[g] = null);
    var size = parts.reduce(function (s2, c2) {
      return s2 + 1 + (c2.length - 1) * 4;
    }, 0);
    var out = new Uint8Array(size);
    var dv = new DataView(out.buffer);
    var o = 0;
    parts.forEach(function (c2) {
      out[o++] = c2[0];
      for (var k = 1; k < c2.length; k++) {
        dv.setFloat32(o, c2[k], true);
        o += 4;
      }
    });
    return (cache[g] = out);
  }

  return {
    upm: upm,
    ascent: hhea.getInt16(4) / upm,
    descent: -hhea.getInt16(6) / upm,
    glyph: function (cp) {
      return cmap[cp] || 0;
    },
    has: function (cp) {
      return !!cmap[cp];
    },
    advance: function (g) {
      return adv(g) / upm;
    },
    blob: blob,
  };
}

function readCmap(dv) {
  var n = dv.getUint16(2);
  var best = -1;
  var bestFmt = 0;
  for (var i = 0; i < n; i++) {
    var pid = dv.getUint16(4 + i * 8);
    var eid = dv.getUint16(6 + i * 8);
    var off = dv.getUint32(8 + i * 8);
    var fmt = dv.getUint16(off);
    if ((pid === 3 && (eid === 1 || eid === 10)) || pid === 0) {
      if (fmt === 12 && bestFmt !== 12) {
        best = off;
        bestFmt = 12;
      } else if (fmt === 4 && !bestFmt) {
        best = off;
        bestFmt = 4;
      }
    }
  }
  var map = {};
  if (best < 0) return map;
  if (bestFmt === 12) {
    var ng = dv.getUint32(best + 12);
    for (var k = 0; k < ng; k++) {
      var o = best + 16 + k * 12;
      var a = dv.getUint32(o);
      var b = dv.getUint32(o + 4);
      var g0 = dv.getUint32(o + 8);
      for (var c = a; c <= b && c - a < 70000; c++) map[c] = g0 + (c - a);
    }
    return map;
  }
  var segX2 = dv.getUint16(best + 6);
  var ends = best + 14;
  var starts = ends + segX2 + 2;
  var deltas = starts + segX2;
  var ranges = deltas + segX2;
  for (var s = 0; s < segX2 / 2; s++) {
    var end = dv.getUint16(ends + s * 2);
    var st = dv.getUint16(starts + s * 2);
    var delta = dv.getInt16(deltas + s * 2);
    var ro = dv.getUint16(ranges + s * 2);
    for (var cp = st; cp <= end && cp !== 0xffff; cp++) {
      var g;
      if (!ro) g = (cp + delta) & 0xffff;
      else {
        g = dv.getUint16(ranges + s * 2 + ro + (cp - st) * 2);
        if (g) g = (g + delta) & 0xffff;
      }
      if (g) map[cp] = g;
    }
  }
  return map;
}

// ---- раскладка ----
function cased(text, c) {
  if (c === "UPPER") return text.toUpperCase();
  if (c === "LOWER") return text.toLowerCase();
  if (c === "TITLE") return text.replace(/(^|[\s\-«"(])([^\s])/g, function (_, a, b) {
    return a + b.toUpperCase();
  });
  return text;
}

/**
 * Раскладка текстового узла как у Figma: строки (перенос по словам, если ширина фиксирована), базовые линии, глифы.
 * faces — шрифты одного начертания по наборам знаков (латиница, кириллица…): берётся первый, где буква есть.
 * addBlob(bytes) → индекс блоба в сообщении. null — шрифта нет.
 */
export function layoutText(n, faces, addBlob) {
  if (!faces || !faces.length) return null;
  var fs = n.fontSize;
  var lh = n.lineHeight && n.lineHeight.value > 0 ? n.lineHeight.value : fs * 1.2;
  var ls = n.letterSpacing && n.letterSpacing.units === "PERCENT" ? (n.letterSpacing.value / 100) * fs : (n.letterSpacing && n.letterSpacing.value) || 0;
  var src = String(n.textData.characters || "");
  var text = cased(src, n.textCase);
  if (text.length !== src.length) text = src;
  var main = faces[0];
  var faceOf = function (cp) {
    for (var i = 0; i < faces.length; i++) if (faces[i].has(cp)) return faces[i];
    return main;
  };
  // Ширина каждого символа (в пикселях, с межбуквенным).
  var chars = [];
  for (var i = 0; i < text.length; i++) {
    var cp = text.charCodeAt(i);
    var f = faceOf(cp);
    var g = f.glyph(cp);
    chars.push({ i: i, ch: text[i], face: f, g: g, w: (g || cp === 32 ? f.advance(g) * fs : fs * 0.5) + ls });
  }
  var W = n.size.x;
  var wrap = n.textAutoResize !== "WIDTH_AND_HEIGHT";
  var lines = [];
  var cur = { a: 0, b: 0 };
  var width = function (a, b) {
    var s = 0;
    for (var k = a; k < b; k++) s += chars[k].w;
    return s;
  };
  var trimEnd = function (a, b) {
    while (b > a && /\s/.test(text[b - 1])) b--;
    return b;
  };
  for (var p = 0; p <= text.length; p++) {
    if (p === text.length || text[p] === "\n") {
      cur.b = p;
      lines.push(cur);
      cur = { a: p + 1, b: p + 1 };
      continue;
    }
    if (!wrap || /\s/.test(text[p])) continue;
    if (width(cur.a, trimEnd(cur.a, p + 1)) > W + 0.5 && p > cur.a) {
      // Переносим с последнего пробела; длинное слово без пробелов — режем по букве.
      var sp = -1;
      for (var q = p; q > cur.a; q--) {
        if (/\s/.test(text[q - 1])) {
          sp = q;
          break;
        }
      }
      var cut = sp > cur.a ? sp : p;
      lines.push({ a: cur.a, b: cut });
      cur = { a: cut, b: cut };
    }
  }
  var asc = main.ascent;
  var desc = main.descent;
  var total = lines.length * lh;
  var H = n.size.y;
  var oy = n.textAlignVertical === "CENTER" ? (H - total) / 2 : n.textAlignVertical === "BOTTOM" ? H - total : 0;
  var align = n.textAlignHorizontal || "LEFT";
  var baselines = [];
  var glyphs = [];
  var offsets = [];
  var blobIdx = {};
  var lineW = lines.map(function (L) {
    var end = trimEnd(L.a, L.b);
    return Math.max(0, width(L.a, end) - (end > L.a ? ls : 0));
  });
  var maxW = Math.max.apply(null, lineW.concat([0]));
  if (!wrap) W = Math.ceil(maxW * 100) / 100; // «по содержимому» — ширина по самой длинной строке
  lines.forEach(function (L, li) {
    var lw = lineW[li];
    var x0 = align === "CENTER" ? (W - lw) / 2 : align === "RIGHT" ? W - lw : 0;
    var lineY = oy + li * lh;
    var base = lineY + (lh - (asc + desc) * fs) / 2 + asc * fs;
    baselines.push({ position: { x: x0, y: base }, width: lw, lineY: lineY, lineHeight: lh, lineAscent: asc * fs, firstCharacter: L.a, endCharacter: Math.max(L.a, L.b) });
    var x = x0;
    for (var k = L.a; k < L.b; k++) {
      var c = chars[k];
      offsets[k] = x - x0;
      if (c.g && !/\s/.test(c.ch)) {
        var key = c.face.upm + ":" + faces.indexOf(c.face) + ":" + c.g;
        if (blobIdx[key] === undefined) {
          var bytes = c.face.blob(c.g);
          blobIdx[key] = bytes ? addBlob(bytes) : -1;
        }
        if (blobIdx[key] >= 0) {
          glyphs.push({ commandsBlob: blobIdx[key], position: { x: x, y: base }, fontSize: fs, firstCharacter: k, advance: c.face.advance(c.g), rotation: 0 });
        }
      }
      x += c.w;
    }
    if (L.b < text.length) offsets[L.b] = x - x0;
  });
  for (var z = 0; z < text.length; z++) if (offsets[z] == null) offsets[z] = 0;
  return {
    layoutSize: { x: W, y: total },
    baselines: baselines,
    glyphs: glyphs,
    fontMetaData: [{ key: { family: n.fontName.family, style: n.fontName.style, postscript: "" }, fontLineHeight: asc + desc, fontDigest: new Uint8Array(20),
      fontStyle: "NORMAL", fontWeight: WEIGHT_OF[n.fontName.style] || 400 }],
    truncationStartIndex: -1,
    truncatedHeight: -1,
    logicalIndexToCharacterOffsetMap: offsets,
    derivedLines: lines.map(function () {
      return { directionality: "LTR" };
    }),
  };
}

var WEIGHT_OF = { Thin: 100, "Extra Light": 200, Light: 300, Regular: 400, Medium: 500, "Semi Bold": 600, Bold: 700, "Extra Bold": 800, Black: 900 };

// ---- загрузка шрифтов (Fontsource на jsDelivr: те же Google Fonts, woff по наборам знаков) ----
var SUBSETS = ["latin", "cyrillic"];
var faces = {}; // "Inter|600" → [face…] (по наборам знаков)
var loading = {};

function slug(family) {
  return String(family).toLowerCase().replace(/\s+/g, "-");
}

export function facesFor(family, weight) {
  return faces[family + "|" + weight] || null;
}

/** Добавить шрифт из байтов (для тестов и своих источников). */
export async function addFont(family, weight, bytes) {
  var f = await parseFont(bytes);
  var k = family + "|" + weight;
  (faces[k] = faces[k] || []).push(f);
  return f;
}

/** Загрузить начертание: сначала нужный вес, нет такого файла — обычный (400). */
export function loadFace(family, weight) {
  var k = family + "|" + weight;
  if (faces[k]) return Promise.resolve(faces[k]);
  if (loading[k]) return loading[k];
  var get = function (w) {
    return Promise.all(SUBSETS.map(function (s) {
      return fetch("https://cdn.jsdelivr.net/fontsource/fonts/" + slug(family) + "@latest/" + s + "-" + w + "-normal.woff").then(function (r) {
        return r.ok ? r.arrayBuffer() : null;
      }).then(function (buf) {
        return buf ? parseFont(new Uint8Array(buf)) : null;
      }).catch(function () {
        return null;
      });
    })).then(function (list) {
      return list.filter(Boolean);
    });
  };
  loading[k] = get(weight).then(function (list) {
    return list.length || weight === 400 ? list : get(400);
  }).then(function (list) {
    if (list.length) faces[k] = list;
    else delete loading[k];
    return list.length ? list : null;
  });
  return loading[k];
}
