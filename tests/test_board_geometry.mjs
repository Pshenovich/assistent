// node tests/test_board_geometry.mjs — геометрия холста (webapp/board-geom.js).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// webapp/ — "type": "module", а модуль классический (для <script>): грузим как скрипт.
const src = readFileSync(new URL("../webapp/board-geom.js", import.meta.url), "utf8");
const mod = { exports: {} };
new Function("module", src)(mod);
const G = mod.exports;

const near = (a, b, eps = 0.01) => assert.ok(Math.abs(a - b) <= eps, `${a} ≈ ${b}`);
const nearPt = (p, x, y) => {
  near(p.x, x);
  near(p.y, y);
};

const A = { id: "a", type: "rect", x: 0, y: 0, w: 100, h: 50 };
const B = { id: "b", type: "diamond", x: 300, y: 0, w: 100, h: 50 };
const C = { id: "c", type: "ellipse", x: 0, y: 200, w: 100, h: 50 };
const els = { a: A, b: B, c: C };
const get = (id) => els[id];

// Стороны и контур.
nearPt(G.sidePoint(A, "right"), 100, 25);
nearPt(G.sidePoint(A, "top"), 50, 0);
nearPt(G.outlinePoint(A, { x: 500, y: 25 }), 100, 25);
nearPt(G.outlinePoint(B, { x: 0, y: 25 }), 300, 25);
nearPt(G.outlinePoint(C, { x: 50, y: 0 }), 50, 200);
// Ромб: луч по диагонали попадает на ребро, не в угол рамки.
const dp = G.outlinePoint(B, { x: 450, y: 75 });
near(Math.abs(dp.x - 350) / 50 + Math.abs(dp.y - 25) / 25, 1);

// Поворот: правая сторона повёрнутого на 90° прямоугольника смотрит вниз.
const R = { ...A, rot: 90 };
nearPt(G.sidePoint(R, "right"), 50, 75);
nearPt(G.sideNormal(R, "right"), 0, 1);
assert.ok(G.contains(R, { x: 50, y: 70 }));
assert.ok(!G.contains(R, { x: 95, y: 25 }));

// Стрелка «к объекту» с обеих сторон: концы на контурах, по линии центров.
const line = { type: "line", points: [[0, 0], [1, 1]], start: { el: "a" }, end: { el: "b" } };
let res = G.resolveLine(line, get);
nearPt(res.p0, 100, 25);
nearPt(res.p1, 300, 25);
nearPt(res.n0, 1, 0);

// Прилипание к стороне и перемещение блока: конец едет за блоком.
const sideLine = { type: "line", points: [[0, 0], [500, 500]], start: { el: "a", side: "bottom" } };
nearPt(G.resolveLine(sideLine, get).p0, 50, 50);
els.a = { ...A, x: 40, y: 10 };
nearPt(G.resolveLine(sideLine, get).p0, 90, 60);
els.a = A;

// Свободный конец остаётся в points; удалённый блок — тоже как свободный.
const dangling = { type: "line", points: [[5, 6], [7, 8]], start: { el: "zzz" } };
nearPt(G.resolveLine(dangling, get).p0, 5, 6);

// Изгиб: середина пути смещается на bend по нормали, и ручка возвращает тот же bend.
res = G.resolveLine(line, get);
const bent = { ...line, bend: 40 };
const path = G.linePath(bent, res);
nearPt(path.mid, 200, 65);
near(G.bendFromPoint(bent, res, path.mid), 40);
const curved = { ...line, route: "curve", bend: 30 };
const cp = G.linePath(curved, res);
near(G.bendFromPoint(curved, res, cp.mid), 30, 0.5);

// Ломаная: горизонтальные выходы — средний отрезок вертикальный, bend двигает его.
const el2 = { type: "line", route: "elbow", bend: 10, points: [[0, 0], [200, 100]] };
const ep = G.linePath(el2, G.resolveLine(el2, get));
assert.equal(ep.poly.length, 4);
nearPt(ep.poly[1], 110, 0);
nearPt(ep.poly[2], 110, 100);
nearPt(ep.endDir, 1, 0);

// Наконечник: вершина в конце, основание позади.
const head = G.arrowHead({ x: 10, y: 0 }, { x: 1, y: 0 }, 10);
nearPt(head[0], 10, 0);
near(head[1].x, 0);

// Рамка линии и упрощение штриха.
const b = G.elBounds(line, get);
nearPt({ x: b.x, y: b.w }, 100, 200);
const simp = G.simplify([{ x: 0, y: 0 }, { x: 1, y: 0.01 }, { x: 2, y: 0 }, { x: 2, y: 5 }], 0.5);
assert.equal(simp.length, 3);

console.log("board geometry ok");
