/* Геометрия холста (webapp/board.js): точки прилипания стрелок к фигурам, маршруты линий
 * (прямая с изгибом, кривая, ломаная), наконечники, рамки элементов. Без DOM — проверяется
 * тестом в node (tests/test_board_geometry.mjs). */
(function (root) {
  "use strict";

  var SIDES = ["top", "right", "bottom", "left"];
  var SIDE_N = { top: [0, -1], right: [1, 0], bottom: [0, 1], left: [-1, 0] };

  function pt(x, y) {
    return { x: x, y: y };
  }

  function add(a, b) {
    return pt(a.x + b.x, a.y + b.y);
  }

  function sub(a, b) {
    return pt(a.x - b.x, a.y - b.y);
  }

  function mul(a, k) {
    return pt(a.x * k, a.y * k);
  }

  function len(a) {
    return Math.sqrt(a.x * a.x + a.y * a.y);
  }

  function dist(a, b) {
    return len(sub(a, b));
  }

  function norm(a) {
    var l = len(a);
    return l > 1e-9 ? pt(a.x / l, a.y / l) : pt(1, 0);
  }

  function perp(a) {
    return pt(-a.y, a.x);
  }

  function dot(a, b) {
    return a.x * b.x + a.y * b.y;
  }

  function rot(p, c, deg) {
    if (!deg) return pt(p.x, p.y);
    var r = (deg * Math.PI) / 180;
    var cs = Math.cos(r);
    var sn = Math.sin(r);
    var dx = p.x - c.x;
    var dy = p.y - c.y;
    return pt(c.x + dx * cs - dy * sn, c.y + dx * sn + dy * cs);
  }

  function rotVec(v, deg) {
    return rot(v, pt(0, 0), deg);
  }

  function center(el) {
    return pt(el.x + el.w / 2, el.y + el.h / 2);
  }

  /** Мировая точка → локальная (0..w, 0..h) с учётом поворота. */
  function toLocal(el, p) {
    var q = rot(p, center(el), -(el.rot || 0));
    return pt(q.x - el.x, q.y - el.y);
  }

  function toWorld(el, lp) {
    return rot(pt(el.x + lp.x, el.y + lp.y), center(el), el.rot || 0);
  }

  function sidePoint(el, side) {
    var lp = {
      top: pt(el.w / 2, 0),
      right: pt(el.w, el.h / 2),
      bottom: pt(el.w / 2, el.h),
      left: pt(0, el.h / 2),
    }[side] || pt(el.w, el.h / 2);
    return toWorld(el, lp);
  }

  function sideNormal(el, side) {
    var n = SIDE_N[side] || SIDE_N.right;
    return rotVec(pt(n[0], n[1]), el.rot || 0);
  }

  function anchorPoint(el, a) {
    return toWorld(el, pt((a[0] || 0) * el.w, (a[1] || 0) * el.h));
  }

  /** Сторона, к которой ближе локальная точка (по направлению от центра, с учётом пропорций). */
  function sideToward(el, p) {
    var lp = toLocal(el, p);
    var dx = (lp.x - el.w / 2) / Math.max(1, el.w / 2);
    var dy = (lp.y - el.h / 2) / Math.max(1, el.h / 2);
    if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? "right" : "left";
    return dy >= 0 ? "bottom" : "top";
  }

  /** Точка на контуре фигуры по лучу из центра к toward (стрелка «к объекту»). */
  function outlinePoint(el, toward) {
    var c = pt(el.w / 2, el.h / 2);
    var lt = toLocal(el, toward);
    var d = sub(lt, c);
    var hw = Math.max(0.5, el.w / 2);
    var hh = Math.max(0.5, el.h / 2);
    if (Math.abs(d.x) < 1e-9 && Math.abs(d.y) < 1e-9) d = pt(1, 0);
    var t;
    if (el.type === "ellipse") {
      t = 1 / Math.sqrt((d.x / hw) * (d.x / hw) + (d.y / hh) * (d.y / hh));
    } else if (el.type === "diamond") {
      t = 1 / (Math.abs(d.x) / hw + Math.abs(d.y) / hh);
    } else {
      var tx = Math.abs(d.x) > 1e-9 ? hw / Math.abs(d.x) : Infinity;
      var ty = Math.abs(d.y) > 1e-9 ? hh / Math.abs(d.y) : Infinity;
      t = Math.min(tx, ty);
    }
    return toWorld(el, add(c, mul(d, t)));
  }

  /** Локальная точка внутри фигуры (с запасом margin) — для прилипания и попадания. */
  function contains(el, p, margin) {
    var m = margin || 0;
    var lp = toLocal(el, p);
    if (lp.x < -m || lp.y < -m || lp.x > el.w + m || lp.y > el.h + m) return false;
    if (el.type === "ellipse" || el.type === "diamond") {
      var hw = el.w / 2 + m;
      var hh = el.h / 2 + m;
      var dx = (lp.x - el.w / 2) / hw;
      var dy = (lp.y - el.h / 2) / hh;
      if (el.type === "ellipse") return dx * dx + dy * dy <= 1;
      return Math.abs(dx) + Math.abs(dy) <= 1;
    }
    return true;
  }

  function nearestSide(el, p) {
    var best = null;
    var bestD = Infinity;
    SIDES.forEach(function (s) {
      var d = dist(sidePoint(el, s), p);
      if (d < bestD) {
        bestD = d;
        best = s;
      }
    });
    return { side: best, dist: bestD };
  }

  /** Точки излома линии (мировые координаты), без мусора. */
  function waypointsOf(line) {
    var raw = line && line.waypoints;
    if (!Array.isArray(raw)) return [];
    var out = [];
    raw.forEach(function (p) {
      if (Array.isArray(p) && isFinite(p[0]) && isFinite(p[1])) out.push(pt(Number(p[0]), Number(p[1])));
    });
    return out;
  }

  /** Концы линии: {p0, p1, n0, n1}; n — наружная нормаль, если конец прилип к фигуре. */
  function resolveLine(line, getEl) {
    var pts = line.points || [];
    var raw0 = pts[0] ? pt(pts[0][0], pts[0][1]) : pt(line.x || 0, line.y || 0);
    var raw1 = pts[1] ? pt(pts[1][0], pts[1][1]) : add(raw0, pt(100, 0));

    function fixed(end) {
      var el = end && end.el ? getEl(end.el) : null;
      if (!el || el.type === "line" || el.type === "pen") return null;
      if (end.side) return { el: el, pt: sidePoint(el, end.side), n: sideNormal(el, end.side) };
      if (end.anchor) {
        var ap = anchorPoint(el, end.anchor);
        return { el: el, pt: ap, n: sideNormal(el, sideToward(el, ap)) };
      }
      return { el: el, auto: true };
    }

    var a = fixed(line.start);
    var b = fixed(line.end);
    var ref0 = a ? (a.auto ? center(a.el) : a.pt) : raw0;
    var ref1 = b ? (b.auto ? center(b.el) : b.pt) : raw1;
    // С изломами конец «к объекту» смотрит на ближайшую точку излома, а не на другой конец.
    var wps = waypointsOf(line);
    if (wps.length) {
      var w0 = wps[0];
      var w1 = wps[wps.length - 1];
      var p0w = a ? (a.auto ? outlinePoint(a.el, w0) : a.pt) : raw0;
      var p1w = b ? (b.auto ? outlinePoint(b.el, w1) : b.pt) : raw1;
      return {
        p0: p0w,
        p1: p1w,
        n0: a ? (a.auto ? sideNormal(a.el, sideToward(a.el, w0)) : a.n) : null,
        n1: b ? (b.auto ? sideNormal(b.el, sideToward(b.el, w1)) : b.n) : null,
      };
    }
    var p0 = a ? (a.auto ? outlinePoint(a.el, ref1) : a.pt) : raw0;
    var p1 = b ? (b.auto ? outlinePoint(b.el, ref0) : b.pt) : raw1;
    var n0 = a ? (a.auto ? sideNormal(a.el, sideToward(a.el, ref1)) : a.n) : null;
    var n1 = b ? (b.auto ? sideNormal(b.el, sideToward(b.el, ref0)) : b.n) : null;
    return { p0: p0, p1: p1, n0: n0, n1: n1 };
  }

  function fmt(v) {
    return String(Math.round(v * 10) / 10);
  }

  function P(p) {
    return fmt(p.x) + "," + fmt(p.y);
  }

  function quadAt(p0, c, p1, t) {
    var u = 1 - t;
    return pt(u * u * p0.x + 2 * u * t * c.x + t * t * p1.x, u * u * p0.y + 2 * u * t * c.y + t * t * p1.y);
  }

  function cubicAt(p0, c1, c2, p1, t) {
    var u = 1 - t;
    var a = u * u * u;
    var b = 3 * u * u * t;
    var c = 3 * u * t * t;
    var d = t * t * t;
    return pt(a * p0.x + b * c1.x + c * c2.x + d * p1.x, a * p0.y + b * c1.y + c * c2.y + d * p1.y);
  }

  function elbowHorizontal(res) {
    if (res.n0) return Math.abs(res.n0.x) >= Math.abs(res.n0.y);
    if (res.n1) return Math.abs(res.n1.x) >= Math.abs(res.n1.y);
    return Math.abs(res.p1.x - res.p0.x) >= Math.abs(res.p1.y - res.p0.y);
  }

  /** Путь без точек излома и обхода — как было до них (прямая с изгибом, кривая, ломаная в 1–2 поворота). */
  function basePath(line, res) {
    var p0 = res.p0;
    var p1 = res.p1;
    var bend = Number(line.bend) || 0;
    var route = line.route || "straight";
    var chord = sub(p1, p0);
    var dir = norm(chord);
    var nrm = perp(dir);
    var midChord = mul(add(p0, p1), 0.5);
    var poly = [];
    var i;
    if (route === "elbow") {
      var horiz = elbowHorizontal(res);
      var endHoriz = res.n1 ? Math.abs(res.n1.x) >= Math.abs(res.n1.y) : horiz;
      var pts;
      if (horiz && endHoriz) {
        var mx = midChord.x + bend;
        pts = [p0, pt(mx, p0.y), pt(mx, p1.y), p1];
      } else if (!horiz && !endHoriz) {
        var my = midChord.y + bend;
        pts = [p0, pt(p0.x, my), pt(p1.x, my), p1];
      } else if (horiz) {
        pts = [p0, pt(p1.x, p0.y), p1];
      } else {
        pts = [p0, pt(p0.x, p1.y), p1];
      }
      var mid = pts.length === 4 ? mul(add(pts[1], pts[2]), 0.5) : pts[1];
      return {
        d: "M" + pts.map(P).join("L"),
        mid: mid,
        poly: pts,
        endDir: norm(sub(pts[pts.length - 1], pts[pts.length - 2])),
        startDir: norm(sub(pts[0], pts[1])),
        horizontal: horiz,
      };
    }
    if (route === "curve") {
      var k = Math.max(30, Math.min(220, len(chord) * 0.4));
      var n0 = res.n0 || dir;
      var n1 = res.n1 || mul(dir, -1);
      var off = mul(nrm, bend * 1.333);
      var c1 = add(add(p0, mul(n0, k)), off);
      var c2 = add(add(p1, mul(n1, k)), off);
      for (i = 0; i <= 16; i++) poly.push(cubicAt(p0, c1, c2, p1, i / 16));
      return {
        d: "M" + P(p0) + "C" + P(c1) + " " + P(c2) + " " + P(p1),
        mid: cubicAt(p0, c1, c2, p1, 0.5),
        poly: poly,
        endDir: norm(sub(p1, c2)),
        startDir: norm(sub(p0, c1)),
      };
    }
    if (Math.abs(bend) < 0.5) {
      return { d: "M" + P(p0) + "L" + P(p1), mid: midChord, poly: [p0, p1], endDir: dir, startDir: mul(dir, -1) };
    }
    // Изгиб: квадратичная кривая через точку mid (середина хорды, сдвинутая на bend).
    var m = add(midChord, mul(nrm, bend));
    var c = sub(mul(m, 2), midChord);
    for (i = 0; i <= 16; i++) poly.push(quadAt(p0, c, p1, i / 16));
    return {
      d: "M" + P(p0) + "Q" + P(c) + " " + P(p1),
      mid: m,
      poly: poly,
      endDir: norm(sub(p1, c)),
      startDir: norm(sub(p0, c)),
    };
  }

  var STUB = 16; // отросток ломаной от стороны блока
  var GAP = 12; // зазор обхода вокруг блоков

  function isH(v) {
    return Math.abs(v.x) >= Math.abs(v.y);
  }

  /** Убрать повторы и точки посреди прямого участка (разворот назад оставляем). */
  function cleanPoly(pts) {
    var out = [];
    pts.forEach(function (p) {
      if (out.length && dist(out[out.length - 1], p) < 0.5) return;
      out.push(p);
    });
    var i = 1;
    while (i < out.length - 1) {
      var a = sub(out[i], out[i - 1]);
      var b = sub(out[i + 1], out[i]);
      var cross = a.x * b.y - a.y * b.x;
      if (Math.abs(cross) < 0.5 * Math.max(1, len(a) + len(b)) && dot(a, b) >= 0) out.splice(i, 1);
      else i++;
    }
    return out;
  }

  /** Длина ломаной и точка на её середине. */
  function polyMid(poly) {
    var total = 0;
    var i;
    for (i = 1; i < poly.length; i++) total += dist(poly[i - 1], poly[i]);
    var half = total / 2;
    for (i = 1; i < poly.length; i++) {
      var l = dist(poly[i - 1], poly[i]);
      if (half <= l && l > 0) return add(poly[i - 1], mul(sub(poly[i], poly[i - 1]), half / l));
      half -= l;
    }
    return poly[poly.length - 1];
  }

  /** Результат для ломаной из отрезков; segs — середины отрезков (ручки). */
  function polyResult(pts) {
    var segs = [];
    for (var i = 1; i < pts.length; i++) {
      var v = sub(pts[i], pts[i - 1]);
      segs.push({ i: i - 1, p: mul(add(pts[i - 1], pts[i]), 0.5), horiz: isH(v), len: len(v) });
    }
    var n = pts.length;
    return {
      d: "M" + pts.map(P).join("L"),
      mid: polyMid(pts),
      poly: pts,
      segs: segs,
      endDir: norm(sub(pts[n - 1], pts[n - 2])),
      startDir: norm(sub(pts[0], pts[1])),
    };
  }

  /**
   * Ломаная через точки излома только из горизонталей и вертикалей; первый и последний
   * отрезки выходят по нормали стороны (n0/n1), при нужде — через отросток STUB.
   */
  function orthoNormalize(p0, n0, wps, p1, n1) {
    var out = [p0];
    var pd = n0 ? (isH(n0) ? pt(n0.x >= 0 ? 1 : -1, 0) : pt(0, n0.y >= 0 ? 1 : -1)) : null;
    var first = wps.length ? wps[0] : p1;
    if (n0 && dot(sub(first, p0), pd) < 1) out.push(add(p0, mul(pd, STUB)));
    var seq = wps.slice();
    var last = wps.length ? wps[wps.length - 1] : out[out.length - 1];
    var pd1 = n1 ? (isH(n1) ? pt(n1.x >= 0 ? 1 : -1, 0) : pt(0, n1.y >= 0 ? 1 : -1)) : null;
    if (n1 && dot(sub(last, p1), pd1) < 1) seq.push(add(p1, mul(pd1, STUB)));
    seq.push(p1);
    seq.forEach(function (b, k) {
      var a = out[out.length - 1];
      var dx = b.x - a.x;
      var dy = b.y - a.y;
      if (Math.abs(dx) < 0.5 || Math.abs(dy) < 0.5) {
        if (Math.abs(dx) >= 0.5 || Math.abs(dy) >= 0.5) {
          pd = Math.abs(dx) >= Math.abs(dy) ? pt(dx > 0 ? 1 : -1, 0) : pt(0, dy > 0 ? 1 : -1);
        }
        out.push(b);
        return;
      }
      var hFirst;
      if (k === seq.length - 1 && pd1) hFirst = !isH(pd1); // последний отрезок — по нормали конца
      else if (!pd) hFirst = Math.abs(dx) >= Math.abs(dy);
      else if (pd.x) hFirst = dx * pd.x > 0; // продолжаем прямо, если не назад
      else hFirst = !(dy * pd.y > 0);
      out.push(hFirst ? pt(b.x, a.y) : pt(a.x, b.y), b);
      pd = hFirst ? pt(0, dy > 0 ? 1 : -1) : pt(dx > 0 ? 1 : -1, 0);
    });
    return cleanPoly(out);
  }

  /** Гладкая кривая через точки (Катмулл — Ром), на концах — по нормалям сторон. */
  function curveThrough(res, wps) {
    var Q = [res.p0].concat(wps, [res.p1]);
    var n = Q.length;
    var T = Q.map(function (q, i) {
      if (i === 0) {
        var l0 = dist(Q[0], Q[1]);
        return res.n0 ? mul(res.n0, 3 * Math.max(20, Math.min(220, l0 * 0.4))) : sub(Q[1], Q[0]);
      }
      if (i === n - 1) {
        var l1 = dist(Q[n - 2], Q[n - 1]);
        return res.n1 ? mul(res.n1, -3 * Math.max(20, Math.min(220, l1 * 0.4))) : sub(Q[n - 1], Q[n - 2]);
      }
      return mul(sub(Q[i + 1], Q[i - 1]), 0.5);
    });
    var d = "M" + P(Q[0]);
    var poly = [];
    var segs = [];
    var c1;
    var c2;
    var firstC1 = null;
    for (var i = 0; i < n - 1; i++) {
      c1 = add(Q[i], mul(T[i], 1 / 3));
      c2 = sub(Q[i + 1], mul(T[i + 1], 1 / 3));
      if (!firstC1) firstC1 = c1;
      d += "C" + P(c1) + " " + P(c2) + " " + P(Q[i + 1]);
      for (var t = i ? 1 : 0; t <= 12; t++) poly.push(cubicAt(Q[i], c1, c2, Q[i + 1], t / 12));
      segs.push({ i: i, p: cubicAt(Q[i], c1, c2, Q[i + 1], 0.5) });
    }
    var endDir = dist(res.p1, c2) > 1e-6 ? norm(sub(res.p1, c2)) : norm(sub(Q[n - 1], Q[n - 2]));
    var startDir = dist(res.p0, firstC1) > 1e-6 ? norm(sub(res.p0, firstC1)) : norm(sub(Q[0], Q[1]));
    return { d: d, mid: polyMid(poly), poly: poly, segs: segs, endDir: endDir, startDir: startDir };
  }

  /** Прямой отрезок a→b (по оси) проходит внутри прямоугольника r? */
  function axisSegHitsRect(a, b, r) {
    var eps = 0.01;
    if (Math.abs(a.y - b.y) < eps) {
      if (!(a.y > r.y + eps && a.y < r.y + r.h - eps)) return false;
      return Math.max(Math.min(a.x, b.x), r.x) < Math.min(Math.max(a.x, b.x), r.x + r.w) - eps;
    }
    if (Math.abs(a.x - b.x) < eps) {
      if (!(a.x > r.x + eps && a.x < r.x + r.w - eps)) return false;
      return Math.max(Math.min(a.y, b.y), r.y) < Math.min(Math.max(a.y, b.y), r.y + r.h) - eps;
    }
    // Косой отрезок — грубо по рамке.
    return intersects({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) }, r);
  }

  function polyHitsRects(poly, rects) {
    for (var i = 1; i < poly.length; i++) {
      for (var j = 0; j < rects.length; j++) if (axisSegHitsRect(poly[i - 1], poly[i], rects[j])) return true;
    }
    return false;
  }

  function insideRect(p, r) {
    return p.x > r.x + 0.01 && p.x < r.x + r.w - 0.01 && p.y > r.y + 0.01 && p.y < r.y + r.h - 0.01;
  }

  var routeCache = new Map();
  var MAX_OBST = 60;
  var BEND_COST = 40;

  /**
   * Ломаная в обход блоков: A* по сетке из линий вдоль краёв препятствий (с зазором GAP),
   * штраф за каждый поворот. obstacles — [{x,y,w,h,own?}], own — свои блоки (без зазора).
   * null — пути нет или задача слишком большая.
   */
  function routeOrthogonal(p0, n0, p1, n1, obstacles) {
    var s0 = n0 ? add(p0, mul(norm(n0), STUB)) : p0;
    var s1 = n1 ? add(p1, mul(norm(n1), STUB)) : p1;
    var box = {
      x: Math.min(s0.x, s1.x) - 300, y: Math.min(s0.y, s1.y) - 300,
      w: Math.abs(s0.x - s1.x) + 600, h: Math.abs(s0.y - s1.y) + 600,
    };
    var c = mul(add(s0, s1), 0.5);
    var rects = [];
    (obstacles || []).forEach(function (o) {
      var g = o.own ? 0 : GAP;
      var r = { x: o.x - g, y: o.y - g, w: o.w + 2 * g, h: o.h + 2 * g };
      if (!intersects(r, box)) return;
      if (insideRect(s0, r) || insideRect(s1, r)) {
        if (o.own) return;
        // Отросток упирается в соседа — обходим с меньшим зазором.
        r = { x: o.x, y: o.y, w: o.w, h: o.h };
        if (insideRect(s0, r) || insideRect(s1, r)) return;
      }
      rects.push(r);
    });
    if (rects.length > MAX_OBST) {
      rects.sort(function (a, b) {
        return dist(center(a), c) - dist(center(b), c);
      });
      rects = rects.slice(0, MAX_OBST);
    }
    var key = [p0.x, p0.y, s0.x, s0.y, p1.x, p1.y, s1.x, s1.y].map(fmt).join(",") + "|" +
      rects.map(function (r) {
        return [r.x, r.y, r.w, r.h].map(fmt).join(",");
      }).join(";");
    if (routeCache.has(key)) return routeCache.get(key);
    var result = astar(s0, n0, s1, n1, rects);
    if (result) result = cleanPoly([p0].concat(result, [p1]));
    if (routeCache.size > 400) routeCache.clear();
    routeCache.set(key, result);
    return result;
  }

  function uniqSorted(arr) {
    arr.sort(function (a, b) {
      return a - b;
    });
    var out = [];
    arr.forEach(function (v) {
      if (!out.length || Math.abs(out[out.length - 1] - v) > 0.5) out.push(v);
    });
    return out;
  }

  function astar(s0, n0, s1, n1, rects) {
    var xs = [s0.x, s1.x, (s0.x + s1.x) / 2];
    var ys = [s0.y, s1.y, (s0.y + s1.y) / 2];
    rects.forEach(function (r) {
      xs.push(r.x, r.x + r.w);
      ys.push(r.y, r.y + r.h);
    });
    xs = uniqSorted(xs);
    ys = uniqSorted(ys);
    var nx = xs.length;
    var ny = ys.length;
    if (nx * ny > 40000) return null;
    function idx(v, list) {
      var best = 0;
      for (var i = 1; i < list.length; i++) if (Math.abs(list[i] - v) < Math.abs(list[best] - v)) best = i;
      return best;
    }
    var si = idx(s0.x, xs);
    var sj = idx(s0.y, ys);
    var gi = idx(s1.x, xs);
    var gj = idx(s1.y, ys);
    var blocked = new Uint8Array(nx * ny);
    for (var i = 0; i < nx; i++) {
      for (var j = 0; j < ny; j++) {
        var q = pt(xs[i], ys[j]);
        for (var k = 0; k < rects.length; k++) {
          if (insideRect(q, rects[k])) {
            blocked[j * nx + i] = 1;
            break;
          }
        }
      }
    }
    blocked[sj * nx + si] = 0;
    blocked[gj * nx + gi] = 0;
    var edgeMemo = new Map();
    function edgeOk(i0, j0, i1, j1) {
      var key = i0 < i1 || j0 < j1 ? i0 + "," + j0 + ">" + i1 + "," + j1 : i1 + "," + j1 + ">" + i0 + "," + j0;
      if (edgeMemo.has(key)) return edgeMemo.get(key);
      var a = pt(xs[i0], ys[j0]);
      var b = pt(xs[i1], ys[j1]);
      var ok = true;
      for (var k = 0; k < rects.length; k++) {
        if (axisSegHitsRect(a, b, rects[k])) {
          ok = false;
          break;
        }
      }
      edgeMemo.set(key, ok);
      return ok;
    }
    // Состояние: узел × направление прихода (0 — по горизонтали, 1 — по вертикали).
    var N = nx * ny * 2;
    var gCost = new Float64Array(N).fill(Infinity);
    var prev = new Int32Array(N).fill(-1);
    var heap = [];
    function push(s, f) {
      heap.push([f, s]);
      var c = heap.length - 1;
      while (c > 0) {
        var pIdx = (c - 1) >> 1;
        if (heap[pIdx][0] <= heap[c][0]) break;
        var tmp = heap[pIdx];
        heap[pIdx] = heap[c];
        heap[c] = tmp;
        c = pIdx;
      }
    }
    function pop() {
      var top = heap[0];
      var last = heap.pop();
      if (heap.length) {
        heap[0] = last;
        var c = 0;
        for (;;) {
          var l = 2 * c + 1;
          var r = l + 1;
          var m = c;
          if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
          if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
          if (m === c) break;
          var tmp = heap[m];
          heap[m] = heap[c];
          heap[c] = tmp;
          c = m;
        }
      }
      return top;
    }
    function h(i, j) {
      return Math.abs(xs[i] - xs[gi]) + Math.abs(ys[j] - ys[gj]);
    }
    var startDirs = n0 ? [isH(n0) ? 0 : 1] : [0, 1];
    startDirs.forEach(function (d) {
      var s = (sj * nx + si) * 2 + d;
      gCost[s] = 0;
      push(s, h(si, sj));
    });
    var endDir = n1 ? (isH(n1) ? 0 : 1) : -1;
    var best = -1;
    var bestCost = Infinity;
    var steps = 0;
    while (heap.length) {
      var it = pop();
      var s = it[1];
      if (it[0] >= bestCost) break;
      if (++steps > 200000) return null;
      var node = s >> 1;
      var dir = s & 1;
      var ci = node % nx;
      var cj = (node - ci) / nx;
      var gc = gCost[s];
      if (it[0] > gc + h(ci, cj) + 1e-6) continue;
      if (ci === gi && cj === gj) {
        var fin = gc + (endDir >= 0 && endDir !== dir ? BEND_COST : 0);
        if (fin < bestCost) {
          bestCost = fin;
          best = s;
        }
        continue;
      }
      var nb = [[ci + 1, cj, 0], [ci - 1, cj, 0], [ci, cj + 1, 1], [ci, cj - 1, 1]];
      for (var t = 0; t < 4; t++) {
        var ni = nb[t][0];
        var nj = nb[t][1];
        var nd = nb[t][2];
        if (ni < 0 || nj < 0 || ni >= nx || nj >= ny) continue;
        if (blocked[nj * nx + ni]) continue;
        if (!edgeOk(ci, cj, ni, nj)) continue;
        var step = Math.abs(xs[ni] - xs[ci]) + Math.abs(ys[nj] - ys[cj]);
        var cost = gc + step + (nd !== dir ? BEND_COST : 0);
        var ns = (nj * nx + ni) * 2 + nd;
        if (cost < gCost[ns] - 1e-6) {
          gCost[ns] = cost;
          prev[ns] = s;
          push(ns, cost + h(ni, nj));
        }
      }
    }
    if (best < 0) return null;
    var path = [];
    for (var cur = best; cur >= 0; cur = prev[cur]) {
      var nd2 = cur >> 1;
      var ii = nd2 % nx;
      path.push(pt(xs[ii], ys[(nd2 - ii) / nx]));
    }
    path.reverse();
    // Узлы сетки рядом с отростками сняты с точностью до 0.5 — ставим точные точки.
    path[0] = s0;
    path[path.length - 1] = s1;
    return path;
  }

  /**
   * Путь линии: {d, mid, poly, endDir, startDir, segs?}. mid — середина (подпись, ручка изгиба),
   * endDir/startDir — направление прихода в конец (для наконечника), segs — середины отрезков.
   * opts.obstacles — рамки блоков [{x,y,w,h,own?}]: ломаная без ручных изломов их обходит.
   */
  function linePath(line, res, opts) {
    var route = line.route || "straight";
    var wps = waypointsOf(line);
    if (wps.length) {
      if (route === "elbow") return polyResult(orthoNormalize(res.p0, res.n0, wps, res.p1, res.n1));
      if (route === "curve") return curveThrough(res, wps);
      return polyResult([res.p0].concat(wps, [res.p1]));
    }
    var base = basePath(line, res);
    if (route === "elbow") {
      var obst = (opts && opts.obstacles) || [];
      if (!line.bend && obst.length) {
        var others = obst.filter(function (o) {
          return !o.own;
        });
        if (polyHitsRects(base.poly, others)) {
          var routed = routeOrthogonal(res.p0, res.n0, res.p1, res.n1, obst);
          if (routed && routed.length >= 2) {
            var r = polyResult(routed);
            r.routed = true;
            return r;
          }
        }
      }
      var pr = polyResult(base.poly);
      pr.mid = base.mid;
      pr.horizontal = base.horizontal;
      return pr;
    }
    return base;
  }

  /** Текущий путь как явные точки излома (для первой ручной правки). */
  function materializeWaypoints(line, res, opts) {
    var route = line.route || "straight";
    var wps = waypointsOf(line);
    if (wps.length) return wps;
    if (route === "elbow") return linePath(line, res, opts).poly.slice(1, -1);
    if (line.bend) return [linePath(line, res, opts).mid];
    return [];
  }

  /** Изгиб из положения ручки p. */
  function bendFromPoint(line, res, p) {
    var route = line.route || "straight";
    var midChord = mul(add(res.p0, res.p1), 0.5);
    if (route === "elbow") {
      return elbowHorizontal(res) ? p.x - midChord.x : p.y - midChord.y;
    }
    var nrm = perp(norm(sub(res.p1, res.p0)));
    var b = dot(sub(p, midChord), nrm);
    // У кривой Безье середина смещается на 3/4 от сдвига контрольных точек.
    return route === "curve" ? b : b;
  }

  function arrowHead(tip, dirIn, size) {
    var d = norm(dirIn);
    var n = perp(d);
    var base = sub(tip, mul(d, size));
    return [tip, add(base, mul(n, size * 0.45)), sub(base, mul(n, size * 0.45))];
  }

  function boundsOf(points) {
    if (!points.length) return null;
    var x0 = Infinity;
    var y0 = Infinity;
    var x1 = -Infinity;
    var y1 = -Infinity;
    points.forEach(function (p) {
      if (p.x < x0) x0 = p.x;
      if (p.y < y0) y0 = p.y;
      if (p.x > x1) x1 = p.x;
      if (p.y > y1) y1 = p.y;
    });
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  function corners(el) {
    return [pt(0, 0), pt(el.w, 0), pt(el.w, el.h), pt(0, el.h)].map(function (lp) {
      return toWorld(el, lp);
    });
  }

  /** Рамка элемента в мире (без поворота — описанная). */
  function elBounds(el, getEl) {
    if (el.type === "line") {
      return boundsOf(linePath(el, resolveLine(el, getEl)).poly);
    }
    if (el.type === "pen") {
      return boundsOf(
        (el.points || []).map(function (p) {
          return pt(el.x + p[0], el.y + p[1]);
        })
      ) || { x: el.x, y: el.y, w: 0, h: 0 };
    }
    return boundsOf(corners(el));
  }

  function unionBounds(list) {
    var pts = [];
    list.forEach(function (b) {
      if (!b) return;
      pts.push(pt(b.x, b.y), pt(b.x + b.w, b.y + b.h));
    });
    return boundsOf(pts);
  }

  function intersects(a, b) {
    return a.x <= b.x + b.w && b.x <= a.x + a.w && a.y <= b.y + b.h && b.y <= a.y + a.h;
  }

  function segDist(p, a, b) {
    var ab = sub(b, a);
    var l2 = dot(ab, ab);
    var t = l2 > 0 ? Math.max(0, Math.min(1, dot(sub(p, a), ab) / l2)) : 0;
    return dist(p, add(a, mul(ab, t)));
  }

  function polyDist(p, poly) {
    var best = Infinity;
    for (var i = 1; i < poly.length; i++) best = Math.min(best, segDist(p, poly[i - 1], poly[i]));
    return best;
  }

  /** Упрощение штриха карандаша (Рамер — Дуглас — Пекер). */
  function simplify(points, eps) {
    if (points.length < 3) return points.slice();
    var first = points[0];
    var last = points[points.length - 1];
    var idx = -1;
    var maxD = 0;
    for (var i = 1; i < points.length - 1; i++) {
      var d = segDist(points[i], first, last);
      if (d > maxD) {
        maxD = d;
        idx = i;
      }
    }
    if (maxD <= eps) return [first, last];
    var left = simplify(points.slice(0, idx + 1), eps);
    var right = simplify(points.slice(idx), eps);
    return left.slice(0, -1).concat(right);
  }

  /** Сглаженный путь по точкам: квадратичные кривые через середины отрезков. */
  function smoothPath(points) {
    if (!points.length) return "";
    if (points.length === 1) return "M" + P(points[0]) + "l0.1,0";
    if (points.length === 2) return "M" + P(points[0]) + "L" + P(points[1]);
    var d = "M" + P(points[0]);
    for (var i = 1; i < points.length - 1; i++) {
      var m = mul(add(points[i], points[i + 1]), 0.5);
      d += "Q" + P(points[i]) + " " + P(m);
    }
    d += "L" + P(points[points.length - 1]);
    return d;
  }

  var api = {
    SIDES: SIDES,
    pt: pt,
    add: add,
    sub: sub,
    mul: mul,
    len: len,
    dist: dist,
    norm: norm,
    perp: perp,
    rot: rot,
    center: center,
    toLocal: toLocal,
    toWorld: toWorld,
    sidePoint: sidePoint,
    sideNormal: sideNormal,
    anchorPoint: anchorPoint,
    sideToward: sideToward,
    outlinePoint: outlinePoint,
    contains: contains,
    nearestSide: nearestSide,
    resolveLine: resolveLine,
    linePath: linePath,
    waypointsOf: waypointsOf,
    orthoNormalize: orthoNormalize,
    routeOrthogonal: routeOrthogonal,
    materializeWaypoints: materializeWaypoints,
    cleanPoly: cleanPoly,
    STUB: STUB,
    bendFromPoint: bendFromPoint,
    arrowHead: arrowHead,
    boundsOf: boundsOf,
    corners: corners,
    elBounds: elBounds,
    unionBounds: unionBounds,
    intersects: intersects,
    polyDist: polyDist,
    simplify: simplify,
    smoothPath: smoothPath,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LeoBoardGeom = api;
})(typeof window !== "undefined" ? window : this);
