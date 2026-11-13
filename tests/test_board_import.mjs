// Вставка из Miro (webapp/board-import.js): node tests/test_board_import.mjs
import fs from "node:fs";
import assert from "node:assert/strict";

globalThis.window = globalThis;
new Function(fs.readFileSync(new URL("../webapp/board-import.js", import.meta.url), "utf8"))();
const IMP = globalThis.LeoBoardImport;

function miroHtml(objects) {
  const bytes = Buffer.from(JSON.stringify({ isProtected: false, data: { objects }, version: 2 }), "utf8");
  const shifted = Buffer.from(bytes.map((b) => (b + 59) & 255));
  return `<meta charset='utf-8'><span data-meta="<--(miro-data-v1)${shifted.toString("base64")}(/miro-data-v1)-->"></span>`;
}

const shape = (id, x, y, text, style) => ({
  id, type: 14, widgetData: { type: "shape", json: {
    _position: { offsetPx: { x, y } }, size: { width: 200, height: 100 }, scale: { scale: 1 }, rotation: { rotation: 0 },
    text: `<p><span>${text}</span></p>`, shape: "7", style: JSON.stringify(style),
  } },
});

const html = miroHtml([
  shape(0, 0, 0, "Старт", { bc: 3169195, brc: 1710618, brw: 2, tc: 0, fs: 26, ta: "c", brr: 40 }),
  shape(1, 0, 300, "Шаг", { bc: -1, brc: 4351231, brw: 2, tc: 1710618, fs: 18, ta: "l", brr: 40 }),
  { id: 2, widgetData: { type: "text", json: {
    _position: { offsetPx: { x: 0, y: 150 } }, size: { width: 270, height: 20 }, scale: { scale: 1 },
    text: '<p><span style="background-color:rgb(254,159,77)">Подпись</span></p>', style: JSON.stringify({ tc: 1710618, ta: "l" }),
  } } },
  { id: 3, widgetData: { type: "line", json: {
    primary: { point: { x: 0.5, y: 1 }, positionType: 0, widgetIndex: 0 },
    secondary: { point: { x: 0.25, y: 0 }, positionType: 0, widgetIndex: 1 },
    style: JSON.stringify({ lc: 3355443, ls: 2, t: 2, a_end: 1 }), line: { captions: [{ text: "<p>да</p>" }] },
  } } },
  { id: 4, widgetData: { type: "line", json: { primary: { widgetIndex: 0 }, secondary: { widgetIndex: 99 }, style: "{}" } } },
  { id: 5, widgetData: { type: "embed", json: { _position: { offsetPx: { x: 0, y: 0 } } } } },
  { id: 6, widgetData: { type: "image", json: {
    _position: { offsetPx: { x: 500, y: 0 } }, scale: { scale: 2 }, crop: { width: 100, height: 50 }, resource: { id: "1", width: 100, height: 50 },
  } } },
]);

let n = 0;
const res = IMP.fromHtml(html, () => "e" + ++n);
assert.equal(res.source, "Miro");
assert.equal(res.skipped, 1); // embed; связь к блоку вне выделения — не ошибка
const [a, b, t] = res.elements;
const ln = res.elements.find((e) => e.type === "line");
assert.deepEqual([a.type, a.x, a.y, a.w, a.h, a.text], ["rect", -100, -50, 200, 100, "Старт"]);
// Текст на синей заливке — точный чёрный (в тёмной теме не должен стать белым).
assert.deepEqual(a.style, { fill: "#305bab", stroke: "ink", strokeWidth: 2, fontSize: 26, textColor: "#000000", align: "center", radius: 40 });
assert.equal(b.style.fill, "none");
assert.equal(b.style.stroke, "#4264ff");
assert.equal(b.style.align, "left");
assert.equal(b.style.textColor, "ink"); // на пустом фоне — цвет темы
assert.deepEqual([t.type, t.text, t.style.highlight, t.style.fill, t.style.textColor], ["text", "Подпись", "#fe9f4d", undefined, "#1a1a1a"]);
assert.equal(ln.type, "line");
assert.deepEqual(ln.start, { el: a.id, side: "bottom" });
assert.deepEqual(ln.end, { el: b.id, anchor: [0.25, 0] });
assert.equal(ln.route, "elbow");
assert.equal(ln.arrowEnd, true);
assert.equal(ln.text, "да");
const slot = res.elements.find((e) => String(e.text || "").startsWith("Картинка из Miro"));
assert.deepEqual([slot.type, slot.x, slot.y, slot.w, slot.h], ["rect", 400, -50, 200, 100]);
assert.equal(res.imageSlots, 1);
assert.equal(IMP.fromHtml("<p>просто текст</p>", () => "x"), null);

// Содержимое рамки: offsetPx — от левого верхнего угла рамки (parentOffsetPx), не от центра доски.
{
  const frame = { id: 0, widgetData: { type: "frame", json: {
    _parent: null, _position: { offsetPx: { x: -10000, y: -5000 }, schema: "canvasOffsetPx" },
    size: { width: 2000, height: 1000 }, scale: { scale: 1 }, title: "Рамка", style: "{}",
  } } };
  const inFrame = (id, type, x, y, extra) => ({ id, widgetData: { type, json: Object.assign({
    _parent: { index: 0 }, _position: { offsetPx: { x, y }, schema: "parentOffsetPx", containerType: "FRAME" },
    size: { width: 200, height: 100 }, scale: { scale: 1 },
  }, extra) } });
  const objs = [
    frame,
    inFrame(1, "sticker", 300, 200, { text: "<p>Стикер</p>", style: "{}" }),
    inFrame(2, "code", 100, 100, { code: "event: &#39;etap&#39;\n", _useTopLeftPositioning: true }),
    inFrame(3, "emoji", 50, 50, { code: "✅", size: { width: 80, height: 80 } }),
    inFrame(4, "stencil", 1000, 500, {
      "ns:diagrammingNotation": { collection: "flowchart", element: "flowchart-decision" },
      schema: { data: { texts: { name: { text: "<p>Да?</p>" } } } }, style: JSON.stringify({ bc: 11399367, fs: 24 }),
    }),
    { id: 5, widgetData: { type: "line", json: {
      _parent: { index: 0 }, primary: { widgetIndex: 1, point: { x: 1, y: 0.5 } }, secondary: { widgetIndex: 4, point: { x: 0, y: 0.5 } },
      points: [{ x: -10500, y: -5300 }], style: "{}",
    } } },
    // Рамки нет в буфере — место не восстановить, пропускаем.
    { id: 6, widgetData: { type: "sticker", json: { _parent: { index: 77 }, _position: { offsetPx: { x: 1, y: 1 }, schema: "parentOffsetPx" }, style: "{}" } } },
  ];
  let m = 0;
  const r = IMP.fromHtml(miroHtml(objs), () => "f" + ++m);
  const fr = r.elements.find((e) => e.type === "rect" && e.w === 2000);
  assert.deepEqual([fr.x, fr.y], [-11000, -5500]);
  const sticky = r.elements.find((e) => e.type === "sticky");
  assert.deepEqual([sticky.x, sticky.y], [-11000 + 300 - 100, -5500 + 200 - 50]);
  const code = r.elements.find((e) => e.style && e.style.fontFamily === "JetBrains Mono");
  assert.deepEqual([code.x, code.y, code.text], [-11000 + 100, -5500 + 100, "event: 'etap'"]);
  const emoji = r.elements.find((e) => e.text === "✅");
  assert.deepEqual([emoji.x, emoji.y], [-11000 + 50 - 40, -5500 + 50 - 40]);
  const dia = r.elements.find((e) => e.type === "diamond");
  assert.deepEqual([dia.x, dia.y, dia.text, dia.style.fill], [-10000 - 100, -5000 - 50, "Да?", "#adf0c7"]);
  const line = r.elements.find((e) => e.type === "line");
  assert.deepEqual([line.start.el, line.end.el, line.waypoints], [sticky.id, dia.id, [[-10500, -5300]]]);
  assert.equal(r.skipped, 1);
}
// Стикеры и вид связей — как в буфере настоящей доски Miro (2026-11-13): цвет в sbc, виды связей 4/5/8/24.
{
  const stick = (id, x, sbc) => ({ id, widgetData: { type: "sticker", json: {
    _position: { offsetPx: { x, y: 0 } }, size: { width: 200, height: 200 }, scale: { scale: 1 },
    text: "<p>s</p>", style: JSON.stringify({ fs: 0, ta: "c", sbc }),
  } } });
  const link = (id, a, b, t, points) => ({ id, widgetData: { type: "line", json: {
    primary: { point: { x: 1, y: 0.5 }, widgetIndex: a }, secondary: { point: { x: 0, y: 0.5 }, widgetIndex: b },
    points: points || [], style: JSON.stringify({ lc: 3355443, ls: 2, t, a_end: 9 }),
  } } });
  let m = 0;
  const r = IMP.fromHtml(miroHtml([
    stick(0, 0, 7004301), stick(1, 400, 1381653), stick(2, 800, 15988215),
    link(3, 0, 1, 4), link(4, 1, 2, 8, [{ x: 600, y: 0 }, { x: 600, y: 50 }]), link(5, 0, 2, 5), link(6, 0, 1, 24),
    link(7, 1, 0, 77, [{ x: 10, y: 10 }, { x: 10, y: 90 }]), link(8, 2, 0, 77, [{ x: 10, y: 10 }, { x: 90, y: 90 }]),
  ]), () => "s" + ++m);
  const st = r.elements.filter((e) => e.type === "sticky");
  assert.deepEqual(st.map((e) => e.style.fill), ["#6ae08d", "#151515", "#f3f5f7"]);
  assert.equal(st[0].style.textColor, undefined);
  assert.equal(st[1].style.textColor, "#ffffff"); // тёмный стикер — белый текст
  const routes = r.elements.filter((e) => e.type === "line").map((e) => e.route);
  assert.deepEqual(routes, ["curve", "elbow", "elbow", "elbow", "elbow", "straight"]);
  r.elements.filter((e) => e.type === "line").forEach((e) => assert.equal(e.arrowEnd, true));
}
// Заглушка картинки помнит, где картинка в Miro (доска подтянет её по ключу), и обрезку — долями исходника.
{
  const img = (id, crop) => ({ id, widgetData: { type: "image", json: {
    _position: { offsetPx: { x: 0, y: 0 } }, scale: { scale: 1 }, crop,
    resource: { id: "3458764517723531304", boardId: "uXjVIjX7OTM=", width: 722, height: 310 },
  } } });
  let m = 0;
  const r = IMP.fromHtml(miroHtml([img(0, { x: 0, y: 0, width: 722, height: 310 }), img(1, { x: 72.2, y: 0, width: 361, height: 155 })]), () => "i" + ++m);
  assert.equal(r.imageSlots, 2);
  assert.deepEqual(r.elements[0].miro, { board: "uXjVIjX7OTM=", res: "3458764517723531304" });
  assert.deepEqual(r.elements[1].miro.crop, [0.1, 0, 0.5, 0.5]);
}
assert.equal(IMP.fromHtml('<span data-meta="<--(miro-data-v1)!!!(/miro-data-v1)-->">', () => "x"), null);
console.log("board import ok");
