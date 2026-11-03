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
]);

let n = 0;
const res = IMP.fromHtml(html, () => "e" + ++n);
assert.equal(res.source, "Miro");
assert.equal(res.skipped, 2); // линия к чужому блоку и embed
const [a, b, t, ln] = res.elements;
assert.deepEqual([a.type, a.x, a.y, a.w, a.h, a.text], ["rect", -100, -50, 200, 100, "Старт"]);
assert.deepEqual(a.style, { fill: "#305bab", stroke: "ink", strokeWidth: 2, fontSize: 26, textColor: "ink", align: "center", radius: 40 });
assert.equal(b.style.fill, "none");
assert.equal(b.style.stroke, "#4264ff");
assert.equal(b.style.align, "left");
assert.deepEqual([t.type, t.text, t.style.fill], ["text", "Подпись", "#fe9f4d"]);
assert.equal(ln.type, "line");
assert.deepEqual(ln.start, { el: a.id, side: "bottom" });
assert.deepEqual(ln.end, { el: b.id, anchor: [0.25, 0] });
assert.equal(ln.route, "elbow");
assert.equal(ln.arrowEnd, true);
assert.equal(ln.text, "да");
assert.equal(IMP.fromHtml("<p>просто текст</p>", () => "x"), null);
assert.equal(IMP.fromHtml('<span data-meta="<--(miro-data-v1)!!!(/miro-data-v1)-->">', () => "x"), null);
console.log("board import ok");
