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

  /**
   * Путь линии: {d, mid, poly, endDir, startDir}. mid — где ручка изгиба,
   * endDir/startDir — направление прихода в конец (для наконечника).
   */
  function linePath(line, res) {
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
