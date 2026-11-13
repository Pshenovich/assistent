/* Вставка схем из других досок (webapp/board.js, onPaste): Miro — как в оригинале.
 *
 * Miro при копировании кладёт в text/html `data-meta="<--(miro-data-v1)…(/miro-data-v1)-->"`:
 * base64, каждый байт сдвинут на 59, внутри JSON {data: {objects: [{id, widgetData: {type, json}}]}}.
 * Координаты виджета — его центр (offsetPx), размер — size × scale; у содержимого рамки offsetPx —
 * от левого верхнего угла рамки (_position.schema "parentOffsetPx", рамка — _parent.index), изломы
 * линий — всегда точки на доске. Линия цепляется к виджету по
 * id (widgetIndex) в точке point (доли ширины и высоты). Формат закрытый: что не разобрали —
 * null, и доска вставит текст как раньше.
 *
 * LeoBoardImport.fromHtml(html, uid) → {source, elements, skipped} | null — координаты от (0, 0).
 */
(function () {
  "use strict";

  var MIRO_RE = /\(miro-data-v1\)([A-Za-z0-9+/=]+)/;

  function hex(n) {
    var h = exact(n);
    return h ? themed(h) : null;
  }

  /** Точный цвет без подмены на цвет темы — для текста на заливке или подсветке. */
  function exact(n) {
    if (typeof n !== "number" || n < 0 || !isFinite(n)) return null;
    return "#" + ("000000" + Math.round(n).toString(16)).slice(-6);
  }

  /** Цвет текста: на пустом фоне — с подменой на цвет темы (виден и в тёмной), на цветном — как есть. */
  function textColor(n, onColor) {
    if (onColor) return exact(n) || "#1a1a1a";
    return hex(n) || "ink";
  }

  /** Почти чёрный и белый — цвета темы доски («ink» / «white»): в светлой теме как в оригинале,
   * в тёмной не пропадают на фоне. Остальные цвета — точные. */
  function lum(h) {
    return 0.2126 * parseInt(h.slice(1, 3), 16) + 0.7152 * parseInt(h.slice(3, 5), 16) + 0.0722 * parseInt(h.slice(5, 7), 16);
  }

  function themed(h) {
    var r = parseInt(h.slice(1, 3), 16);
    var g = parseInt(h.slice(3, 5), 16);
    var b = parseInt(h.slice(5, 7), 16);
    var lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    var sat = Math.max(r, g, b) - Math.min(r, g, b);
    if (sat < 24 && lum < 70) return "ink";
    if (sat < 12 && lum > 245) return "white";
    return h;
  }

  function rgbHex(css) {
    var m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)/i.exec(String(css || ""));
    if (!m || (m[4] != null && Number(m[4]) === 0)) return null;
    return "#" + [m[1], m[2], m[3]].map(function (v) {
      return ("0" + Math.min(255, Number(v)).toString(16)).slice(-2);
    }).join("");
  }

  function json(v) {
    if (v && typeof v === "object") return v;
    try {
      return JSON.parse(String(v || "{}"));
    } catch (_) {
      return {};
    }
  }

  /** HTML подписи Miro → текст (абзацы — строки) и цвет подсветки фона, если он есть. */
  function htmlText(html) {
    var out = { text: "", bg: null, bold: false };
    if (!html) return out;
    try {
      var doc = new DOMParser().parseFromString("<div>" + String(html) + "</div>", "text/html");
      var root = doc.body.firstChild;
      var lines = [];
      var blocks = root.querySelectorAll("p, li, h1, h2, h3, h4, div > div");
      if (!blocks.length) lines.push(root.textContent);
      else blocks.forEach(function (b) {
        lines.push(b.textContent);
      });
      out.text = lines.join("\n").replace(/ /g, " ").replace(/\n+$/, "").trim();
      root.querySelectorAll("[style]").forEach(function (n) {
        if (!out.bg) out.bg = rgbHex(n.style.backgroundColor);
      });
      out.bold = !!root.querySelector("strong, b");
    } catch (_) {
      out.text = String(html).replace(/<\/p>\s*<p[^>]*>/gi, "\n").replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").trim();
      var bg = /background-color:\s*(rgba?\([^)]*\))/i.exec(String(html));
      out.bg = bg ? rgbHex(bg[1]) : null;
      out.bold = /<(strong|b)[\s>]/i.test(String(html));
    }
    return out;
  }

  function decodeMiro(html) {
    var m = MIRO_RE.exec(String(html || ""));
    if (!m) return null;
    try {
      var bin = atob(m[1]);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = (bin.charCodeAt(i) - 59) & 255;
      var data = JSON.parse(new TextDecoder("utf-8").decode(bytes));
      return data && data.data && Array.isArray(data.data.objects) ? data.data.objects : null;
    } catch (_) {
      return null;
    }
  }

  // Формы Miro: старые числовые коды и новые имена.
  var MIRO_SHAPES = {
    3: "rect", 7: "round", 4: "ellipse", 8: "diamond", 6: "diamond",
    rectangle: "rect", square: "rect", round_rectangle: "round", circle: "ellipse", ellipse: "ellipse",
    rhombus: "diamond", diamond: "diamond",
    flow_chart_terminator: "pill", triangle: "triangle", parallelogram: "parallelogram", flow_chart_input_output: "parallelogram",
    can: "cylinder", flow_chart_magnetic_disk: "cylinder",
  };

  function side(point) {
    var x = Number(point && point.x);
    var y = Number(point && point.y);
    if (!isFinite(x) || !isFinite(y)) return null;
    var near = function (a, b) {
      return Math.abs(a - b) < 0.02;
    };
    if (near(x, 0.5) && near(y, 0)) return { side: "top" };
    if (near(x, 0.5) && near(y, 1)) return { side: "bottom" };
    if (near(y, 0.5) && near(x, 0)) return { side: "left" };
    if (near(y, 0.5) && near(x, 1)) return { side: "right" };
    return { anchor: [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))] };
  }

  /** Вид связи Miro (style.t) → наш. Сверено с буфером настоящей доски (2026-11-13): у 2, 5, 8 и 24
   * изломы строго по горизонтали и вертикали — ломаные (варианты наконечников и обхода);
   * 4 — без изломов и наискосок — изогнутая (связь Miro по умолчанию), 1 — изогнутая, 0 — прямая.
   * Незнакомый вид: изломы под прямым углом — ломаная, иначе прямая. */
  var MIRO_ROUTES = { 0: "straight", 1: "curve", 4: "curve", 2: "elbow", 5: "elbow", 8: "elbow", 24: "elbow" };

  function miroRoute(t, wps) {
    var known = MIRO_ROUTES[Number(t)];
    if (known) return known;
    if (wps.length < 2) return "straight";
    for (var i = 1; i < wps.length; i++) {
      if (Math.abs(wps[i][0] - wps[i - 1][0]) > 0.5 && Math.abs(wps[i][1] - wps[i - 1][1]) > 0.5) return "straight";
    }
    return "elbow";
  }

  /** Блок-схема Miro (stencil, ns:diagrammingNotation) → наша фигура. */
  var MIRO_STENCILS = {
    "flowchart-decision": "diamond", "flowchart-terminator": "pill", "flowchart-data": "parallelogram",
    "flowchart-database": "cylinder", "flowchart-connector": "ellipse",
  };

  function decodeEntities(s) {
    s = String(s || "");
    try {
      var ta = new DOMParser().parseFromString("<textarea>" + s.replace(/</g, "&lt;") + "</textarea>", "text/html");
      return ta.querySelector("textarea").value;
    } catch (_) {
      return s.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    }
  }

  /** Центр виджета на доске Miro. У содержимого рамки offsetPx — от левого верхнего угла рамки
   * (schema "parentOffsetPx", рамка — _parent.index), у остальных — точка на доске (canvasOffsetPx).
   * _useTopLeftPositioning (блоки кода) — offsetPx указывает левый верхний угол, а не центр. */
  function miroCenter(j, raw, bw, bh, depth) {
    var p = j._position;
    var off = p && p.offsetPx;
    if (!off || !isFinite(off.x) || !isFinite(off.y)) return null;
    var x = Number(off.x);
    var y = Number(off.y);
    if (p.schema === "parentOffsetPx" && j._parent) {
      var par = raw[j._parent.index];
      var pj = par && par.widgetData && par.widgetData.json;
      // Рамки нет в буфере — места не восстановить: такой виджет пропускаем. Рамка в рамке — так же вверх.
      var pc = pj && (depth || 0) < 8 ? miroCenter(pj, raw, 0, 0, (depth || 0) + 1) : null;
      if (!pc) return null;
      var pk = (pj.scale && pj.scale.scale) || 1;
      var ps = pj.size || {};
      x += pc.x - ((Number(ps.width) || Number(pj.width) || 0) * pk) / 2;
      y += pc.y - ((Number(ps.height) || Number(pj.height) || 0) * pk) / 2;
    }
    if (j._useTopLeftPositioning) {
      x += bw / 2;
      y += bh / 2;
    }
    return { x: x, y: y };
  }

  function fromMiro(objects, uid) {
    var els = [];
    var byId = {};
    var skipped = 0;
    var imageSlots = 0;
    var boxes = {};
    var raw = {};
    objects.forEach(function (o) {
      if (o && o.id != null) raw[o.id] = o;
    });
    // Сначала блоки (у линий ссылки на них), потом линии.
    objects.forEach(function (o) {
      var w = o && o.widgetData;
      if (!w || w.type === "line") return;
      var j = w.json || {};
      var size = j.size || {};
      var k = (j.scale && j.scale.scale) || 1;
      var bw = (Number(size.width) || 100) * k;
      var bh = (Number(size.height) || 40) * k;
      var pos = miroCenter(j, raw, bw, bh);
      if (!pos) {
        skipped++;
        return;
      }
      var st = json(j.style);
      var base = { id: uid(), x: pos.x - bw / 2, y: pos.y - bh / 2, w: bw, h: bh };
      var rot = j.rotation && Number(j.rotation.rotation);
      if (rot) base.rot = Math.round(rot * 10) / 10;
      var t = htmlText(j.text);
      var el = null;
      if (w.type === "shape") {
        var kind = MIRO_SHAPES[j.shape] || MIRO_SHAPES[String(j.shape).toLowerCase()] || "rect";
        var fill = hex(st.bc) || "none";
        var style = {
          fill: fill,
          stroke: hex(st.brc) || "ink",
          strokeWidth: st.brc != null && st.brc < 0 ? 0 : Number(st.brw) >= 0 ? Number(st.brw) : 2,
          fontSize: Math.round((Number(st.fs) || 14) * k),
          // Точный цвет — на точной заливке или подсветке; на «белой» (цвет темы) текст тоже цвет темы.
          textColor: textColor(st.tc, /^#/.test(fill) || !!t.bg),
          align: st.ta === "l" ? "left" : st.ta === "r" ? "right" : "center",
        };
        if (t.bg) style.highlight = t.bg;
        if (st.brs === 1) style.dash = "dashed";
        else if (st.brs === 0) style.dash = "dotted";
        if (Number(st.b) || t.bold) style.bold = true;
        if (kind === "round") style.radius = Math.round((Number(st.brr) || 20) * k);
        else if (kind === "rect") style.radius = 0;
        el = Object.assign(base, { type: kind === "round" ? "rect" : kind, text: t.text, style: style });
      } else if (w.type === "text") {
        var box = hex(st.bc);
        el = Object.assign(base, {
          type: "text", text: t.text,
          style: {
            fontSize: Math.round((Number(st.fs) || 14) * k),
            textColor: textColor(st.tc, !!(t.bg || /^#/.test(box || ""))),
            align: st.ta === "c" ? "center" : st.ta === "r" ? "right" : "left",
          },
        });
        // Фон виджета — заливка блока, фон в разметке текста — подсветка под словами (как в Miro).
        if (box) el.style.fill = box;
        if (t.bg) el.style.highlight = t.bg;
        if (Number(st.b) || t.bold) el.style.bold = true;
      } else if (w.type === "sticker" || w.type === "sticky_note") {
        // Цвет стикера: сейчас Miro кладёт его в sbc, раньше — sc / bc. Точный, без подмены на цвет
        // темы: «почти чёрный» стикер иначе стал бы ключом ink, которого нет у стикеров (→ жёлтый).
        var sfillHex = exact(st.sbc) || exact(st.sc) || exact(st.bc);
        var sstyle = { fill: sfillHex || "yellow", fontSize: Math.round((Number(st.fs) || 14) * k) };
        // На тёмном стикере — белый текст, как в Miro.
        if (sfillHex && lum(sfillHex) < 110) sstyle.textColor = "#ffffff";
        el = Object.assign(base, { type: "sticky", text: t.text, style: sstyle });
      } else if (w.type === "image") {
        // Сам файл в буфер не попадает (только id в Miro) — заглушка того же размера: выделить
        // её и вставить картинку («Копировать как изображение» в Miro), и та встанет на место.
        var res = j.resource || {};
        var crop = j.crop || {};
        var iw = (Number(crop.width) || Number(res.width) || 200) * k;
        var ih = (Number(crop.height) || Number(res.height) || 150) * k;
        el = Object.assign(base, {
          x: pos.x - iw / 2, y: pos.y - ih / 2, w: iw, h: ih, type: "rect",
          text: "Картинка из Miro\nвыделите и вставьте её сюда (в Miro: «Копировать как изображение»)",
          style: { fill: "gray", stroke: "gray", strokeWidth: 2, dash: "dashed", radius: 8, fontSize: Math.max(14, Math.round(iw / 22)), textColor: "gray" },
        });
        // Где картинка в Miro: по этому доска сама скачает её с ключом Miro (board.js, miroFill).
        // crop — видимая часть исходного файла в его пикселях (в Miro картинку можно обрезать).
        if (res.id != null && res.boardId) {
          el.miro = { board: String(res.boardId), res: String(res.id) };
          var rw = Number(res.width) || 0;
          var rh = Number(res.height) || 0;
          var cx = Number(crop.x) || 0;
          var cy = Number(crop.y) || 0;
          var cw = Number(crop.width) || 0;
          var ch = Number(crop.height) || 0;
          if (cw > 0 && ch > 0 && rw > 0 && rh > 0 && (cx > 0.5 || cy > 0.5 || Math.abs(cw - rw) > 0.5 || Math.abs(ch - rh) > 0.5)) {
            el.miro.crop = [cx / rw, cy / rh, cw / rw, ch / rh].map(function (v) {
              return Math.round(Math.max(0, Math.min(1, v)) * 10000) / 10000;
            });
          }
        }
        imageSlots++;
      } else if (w.type === "code") {
        // Блок кода — текст моноширинным шрифтом на светлой подложке.
        el = Object.assign(base, {
          type: "text", text: decodeEntities(j.code).replace(/\s+$/, ""),
          style: { fontSize: Math.round(14 * k), fontFamily: "JetBrains Mono", textColor: "#1a1a1a", align: "left", fill: "#f2f4f7" },
        });
      } else if (w.type === "emoji") {
        el = Object.assign(base, {
          type: "text", text: String(j.code || ""),
          style: { fontSize: Math.max(12, Math.round(bh * 0.75)), align: "center" },
        });
      } else if (w.type === "stencil") {
        // Фигура блок-схемы Miro: вид — по ns:diagrammingNotation, подпись — schema.data.texts.name.
        var notation = (j["ns:diagrammingNotation"] && j["ns:diagrammingNotation"].element) || "";
        var sd = (j.schema && j.schema.data) || {};
        var nm = (sd.texts && sd.texts.name) || {};
        var sfill = hex(st.bc) || "none";
        var skind = MIRO_STENCILS[notation] || "rect";
        el = Object.assign(base, {
          type: skind, text: htmlText(nm.text).text,
          style: {
            fill: sfill, stroke: hex(st.brc) || "ink",
            strokeWidth: st.brc != null && st.brc < 0 ? 0 : Number(st.brw) >= 0 ? Number(st.brw) : 2,
            fontSize: Math.round((Number(st.fs) || 14) * k),
            textColor: textColor(st.tc, /^#/.test(sfill)),
            align: st.ta === "l" ? "left" : st.ta === "r" ? "right" : "center",
          },
        });
        if (skind === "rect") el.style.radius = Math.round(((sd.formulaValues && Number(sd.formulaValues.borderRadius)) || 0) * k);
      } else if (w.type === "frame") {
        el = Object.assign(base, {
          type: "rect", text: "",
          style: { fill: hex(st.bc) || "white", stroke: "#d0d5dd", strokeWidth: 1, radius: 0 },
          _back: true,
        });
        var title = String(j.title || j.name || "").trim();
        if (title) {
          els.push({
            id: uid(), type: "text", x: base.x, y: base.y - 28, w: Math.max(120, bw), h: 24, text: title,
            style: { fontSize: 16, textColor: "#555555", align: "left" },
          });
        }
      }
      if (!el) {
        skipped++;
        return;
      }
      byId[o.id] = el.id;
      boxes[o.id] = el;
      els.push(el);
    });
    objects.forEach(function (o) {
      var w = o && o.widgetData;
      if (!w || w.type !== "line") return;
      var j = w.json || {};
      var st = json(j.style);
      var ends = [j.primary, j.secondary].map(function (e) {
        if (!e || byId[e.widgetIndex] == null) return null;
        return Object.assign({ el: byId[e.widgetIndex] }, side(e.point) || {});
      });
      // Связь с блоком вне выделения (второй конец — точка на доске Miro) или петля — не переносим,
      // и это не ошибка: в выделение попал только один её конец.
      if (!ends[0] || !ends[1] || ends[0].el === ends[1].el) return;
      var ln = {
        id: uid(), type: "line", x: 0, y: 0, w: 0, h: 0, points: [[0, 0], [1, 1]],
        start: ends[0], end: ends[1],
        route: "straight",
        style: { stroke: hex(st.lc) || "ink", strokeWidth: Math.max(1, Number(st.ls) || 2) },
      };
      // Изломы ломаной — точки на доске Miro (та же система координат, что у блоков).
      var wps = (Array.isArray(j.points) ? j.points : []).filter(function (q) {
        return q && isFinite(q.x) && isFinite(q.y);
      }).slice(0, 32).map(function (q) {
        return [Math.round(q.x * 10) / 10, Math.round(q.y * 10) / 10];
      });
      ln.route = miroRoute(st.t, wps);
      if (wps.length) ln.waypoints = wps;
      if (Number(st.a_start)) ln.arrowStart = true;
      if (Number(st.a_end)) ln.arrowEnd = true;
      if (Number(st.lt) === 2) ln.style.dash = "dashed";
      else if (Number(st.lt) === 3) ln.style.dash = "dotted";
      var cap = j.line && j.line.captions && j.line.captions[0];
      if (cap && cap.text) {
        ln.text = htmlText(cap.text).text;
        ln.style.fontSize = 14;
      }
      els.push(ln);
    });
    // Рамки — под всем остальным.
    els.sort(function (a, b) {
      return (b._back ? 1 : 0) - (a._back ? 1 : 0);
    });
    els.forEach(function (e) {
      delete e._back;
    });
    return els.length ? { source: "Miro", elements: els, skipped: skipped, imageSlots: imageSlots } : null;
  }

  function fromHtml(html, uid) {
    var miro = decodeMiro(html);
    if (miro) return fromMiro(miro, uid);
    return null;
  }

  window.LeoBoardImport = { fromHtml: fromHtml, _decodeMiro: decodeMiro, _fromMiro: fromMiro };
})();
