// Вставка из FigJam (webapp/board-figma.js, сборка npm run build): node tests/test_board_figma.mjs
import fs from "node:fs";
import assert from "node:assert/strict";

globalThis.window = globalThis;
new Function(fs.readFileSync(new URL("../webapp/board-figma.js", import.meta.url), "utf8"))();
const F = globalThis.LeoBoardFigma;

const g = (s, l) => ({ sessionID: s, localID: l });
const paint = (r, gg, b, opacity = 1) => [{ type: "SOLID", visible: true, opacity, color: { r, g: gg, b, a: 1 } }];
const tr = (x, y) => ({ m00: 1, m01: 0, m02: x, m10: 0, m11: 1, m12: y });
const page = { guid: g(0, 1), type: "CANVAS", name: "Page 1" };
const under = (n, pos) => Object.assign({ parentIndex: { guid: g(0, 1), position: pos } }, n);
const shape = (id, x, y, text, fill, pos) => under({
  guid: g(10, id), type: "SHAPE_WITH_TEXT", shapeWithTextType: "ROUNDED_RECTANGLE", size: { x: 200, y: 80 }, transform: tr(x, y),
  nodeGenerationData: { overrides: [
    { guidPath: { guids: [g(40000000, 0)] }, fillPaints: fill, strokePaints: paint(0.24, 0.68, 1), strokeWeight: 4, cornerRadius: 40 },
    { guidPath: { guids: [g(40000000, 1)] }, fontSize: 20, textAlignHorizontal: "LEFT", textData: { characters: text }, fillPaints: paint(0, 0, 0, 0.8) },
  ] },
}, pos);
const msg = { nodeChanges: [
  { guid: g(0, 0), type: "DOCUMENT" }, page,
  shape(1, 0, 0, "Старт", paint(0.76, 0.9, 1), "!"),
  shape(2, 0, 200, "Шаг два", paint(1, 1, 1), "\""),
  under({ guid: g(10, 3), type: "CONNECTOR", connectorLineStyle: "CURVED", connectorEndCap: "ARROW_LINES",
    connectorStart: { endpointNodeID: g(10, 1), magnet: "BOTTOM" }, connectorEnd: { endpointNodeID: g(10, 2), magnet: "TOP" },
    nodeGenerationData: { overrides: [
      { guidPath: { guids: [g(40000000, 0)] }, strokeWeight: 4, strokePaints: paint(0.46, 0.46, 0.46) },
      { guidPath: { guids: [g(40000000, 1)] }, textData: { characters: "да" } },
    ] } }, "#"),
  under({ guid: g(10, 4), type: "CONNECTOR", connectorStart: { endpointNodeID: g(10, 1), magnet: "TOP" }, connectorEnd: { endpointNodeID: g(99, 9) } }, "$"),
  under({ guid: g(11, 1), type: "TABLE", size: { x: 300, y: 100 }, transform: tr(400, 0),
    tableRowPositions: { entries: [{ id: g(11, 2), position: "\"" }, { id: g(11, 3), position: "!" }] },
    tableColumnPositions: { entries: [{ id: g(11, 4), position: "!" }, { id: g(11, 5), position: "\"" }] },
    tableColumnWidths: { entries: [{ id: g(11, 4), size: 100 }, { id: g(11, 5), size: 200 }] },
    nodeGenerationData: { overrides: [
      { guidPath: { guids: [g(40000000, 1), g(11, 3), g(11, 4)] }, textData: { characters: "Первая <строка>" } },
      { guidPath: { guids: [g(40000000, 1), g(11, 2), g(11, 5)] }, textData: { characters: "Вторая" } },
    ] } }, "%"),
  under({ guid: g(12, 1), type: "FRAME", size: { x: 300, y: 60 }, transform: tr(0, 400), fillPaints: paint(1, 1, 1), cornerRadius: 12 }, "&"),
  { guid: g(12, 2), parentIndex: { guid: g(12, 1), position: "!" }, type: "TEXT", size: { x: 100, y: 20 }, transform: tr(10, 20),
    fontSize: 14, textData: { characters: "Внутри фрейма" }, fillPaints: paint(0.5, 0.5, 0.5) },
  // Экземпляр иконки 24×24 в размере 48×48: часть компонента со смещением (2, 2), готовая геометрия
  // (уже в размере экземпляра) и подмена цвета; скрытая часть — пропускается.
  under({ guid: g(13, 1), type: "INSTANCE", size: { x: 48, y: 48 }, transform: tr(800, 0),
    symbolData: { symbolID: g(20, 1), symbolOverrides: [{ guidPath: { guids: [g(5, 2)] }, fillPaints: paint(1, 0, 0) }] },
    derivedSymbolData: [
      { guidPath: { guids: [g(5, 2)] }, fillGeometry: [{ windingRule: "NONZERO", commandsBlob: 0 }] },
      { guidPath: { guids: [g(5, 3)] }, fillGeometry: [{ windingRule: "NONZERO", commandsBlob: 0 }] },
    ] }, "'"),
  { guid: g(20, 1), parentIndex: { guid: g(30, 1), position: "!" }, type: "SYMBOL", size: { x: 24, y: 24 } },
  { guid: g(20, 2), parentIndex: { guid: g(20, 1), position: "!" }, type: "VECTOR", overrideKey: g(5, 2), size: { x: 10, y: 10 }, transform: tr(2, 2), fillPaints: paint(0, 0, 0) },
  { guid: g(20, 3), parentIndex: { guid: g(20, 1), position: "\"" }, type: "VECTOR", overrideKey: g(5, 3), visible: false, size: { x: 10, y: 10 }, transform: tr(0, 0), fillPaints: paint(0, 0, 0) },
  // Отдельный вектор: сеть вершин — 2 вершины, 1 сегмент без касательных, обводка.
  under({ guid: g(14, 1), type: "VECTOR", size: { x: 20, y: 10 }, transform: tr(900, 0), strokeWeight: 2, strokeCap: "ROUND",
    strokePaints: paint(0.1, 0.15, 0.35), vectorData: { vectorNetworkBlob: 1, normalizedSize: { x: 10, y: 5 } } }, "("),
], blobs: [{ bytes: cmdBlob() }, { bytes: netBlob() }] };

function cmdBlob() {
  // M 0 0, L 20 0, L 20 20, Z — float32 LE
  const buf = new ArrayBuffer(1 + 8 + 1 + 8 + 1 + 8 + 1);
  const dv = new DataView(buf);
  let i = 0;
  const pt = (c, x, y) => { dv.setUint8(i++, c); dv.setFloat32(i, x, true); i += 4; dv.setFloat32(i, y, true); i += 4; };
  pt(1, 0, 0); pt(2, 20, 0); pt(2, 20, 20); dv.setUint8(i, 0);
  return new Uint8Array(buf);
}

function netBlob() {
  const buf = new ArrayBuffer(12 + 2 * 12 + 28);
  const dv = new DataView(buf);
  const u = [2, 1, 0];
  u.forEach((v, k) => dv.setUint32(k * 4, v, true));
  let i = 12;
  [[0, 0], [10, 5]].forEach(([x, y]) => { dv.setUint32(i, 0, true); dv.setFloat32(i + 4, x, true); dv.setFloat32(i + 8, y, true); i += 12; });
  dv.setUint32(i, 0, true); dv.setUint32(i + 4, 0, true); dv.setUint32(i + 16, 1, true);
  return new Uint8Array(buf);
}

let n = 0;
const res = F._fromFigma(msg, () => "e" + ++n);
assert.equal(res.source, "FigJam");
assert.equal(res.skipped, 0);
const [a, b] = res.elements;
assert.deepEqual([a.type, a.x, a.y, a.w, a.h, a.text], ["rect", 0, 0, 200, 80, "Старт"]);
assert.equal(a.style.fill, "#c2e6ff");
assert.equal(a.style.stroke, "#3dadff");
assert.equal(a.style.radius, 40);
assert.equal(a.style.textColor, "#333333"); // на точной заливке — точный цвет
assert.equal(b.text, "Шаг\nдва");
assert.equal(b.style.fill, "white");
assert.equal(b.style.textColor, "ink"); // на «белой» — цвет темы
const lines = res.elements.filter((e) => e.type === "line");
assert.equal(lines.length, 1); // вторая связь — к блоку вне выделения
assert.deepEqual([lines[0].start, lines[0].end, lines[0].route, lines[0].arrowEnd, lines[0].text], [{ el: a.id, side: "bottom" }, { el: b.id, side: "top" }, "curve", true, "да"]);
const table = res.elements.find((e) => e.type === "table");
assert.equal(table.w, 300);
assert.match(table.html, /<tr><td[^>]*colwidth="100"><p>Первая &lt;строка&gt;<\/p><\/td><td[^>]*colwidth="200"><p><\/p><\/td><\/tr><tr>/);
const inner = res.elements.find((e) => e.text === "Внутри фрейма");
assert.deepEqual([inner.x, inner.y], [10, 420]);
const [icon, net] = res.elements.filter((e) => e.type === "vector");
assert.deepEqual([icon.x, icon.y, icon.w, icon.h, icon.vb], [800, 0, 48, 48, [48, 48]]);
// Смещение части 2×2 → 4×4 (масштаб 2), геометрия как есть; цвет — из подмены; скрытая часть пропущена.
assert.deepEqual(icon.paths, [{ d: "M4 4L24 4L24 24Z", fill: "#ff0000", rule: undefined }]);
assert.deepEqual(net.paths, [{ d: "M0 0L20 10", stroke: "#1a2659", sw: 2, cap: "round", join: undefined }]);
// Figma Design: фрейм с автолэйаутом, текст и прямоугольник — точные стили, членство во фрейме.
{
  const G = (l) => ({ sessionID: 1, localID: l });
  const P = (l, pos) => ({ guid: G(l), position: pos });
  const T = (x, y) => ({ m00: 1, m01: 0, m02: x, m10: 0, m11: 1, m12: y });
  const dmsg = {
    pasteEditorType: "DESIGN",
    nodeChanges: [
      { guid: G(0), type: "DOCUMENT" },
      { guid: G(1), type: "CANVAS", parentIndex: P(0, "a"), name: "Page" },
      { guid: G(2), type: "FRAME", parentIndex: P(1, "a"), name: "Карточка", size: { x: 200, y: 120 }, transform: T(100, 50),
        fillPaints: [{ type: "SOLID", color: { r: 1, g: 1, b: 1, a: 1 } }], cornerRadius: 12,
        stackMode: "VERTICAL", stackSpacing: 8, stackHorizontalPadding: 16, stackVerticalPadding: 12, stackPrimarySizing: "RESIZE_TO_FIT_WITH_IMPLICIT_SIZE",
        stackCounterAlignItems: "CENTER", effects: [{ type: "DROP_SHADOW", color: { r: 0, g: 0, b: 0, a: 0.25 }, offset: { x: 0, y: 4 }, radius: 12, visible: true }] },
      { guid: G(3), type: "TEXT", parentIndex: P(2, "a"), size: { x: 168, y: 20 }, transform: T(16, 12), textData: { characters: "Заголовок" },
        fontSize: 16, textAlignVertical: "CENTER", lineHeight: { value: 100, units: "PERCENT" }, derivedTextData: { baselines: [{ lineHeight: 19.36 }] }, fontName: { family: "Inter", style: "Semi Bold" }, fillPaints: [{ type: "SOLID", color: { r: 0.1, g: 0.1, b: 0.1, a: 1 } }] },
      { guid: G(4), type: "RECTANGLE", parentIndex: P(2, "b"), size: { x: 168, y: 40 }, transform: T(16, 40), stackChildAlignSelf: "STRETCH",
        rectangleCornerRadiiIndependent: true, rectangleTopLeftCornerRadius: 8, rectangleTopRightCornerRadius: 8,
        strokePaints: [{ type: "SOLID", color: { r: 0, g: 0, b: 1, a: 1 } }], strokeWeight: 2, strokeAlign: "INSIDE",
        fillPaints: [{ type: "GRADIENT_LINEAR", stops: [{ color: { r: 1, g: 0, b: 0, a: 1 }, position: 0 }, { color: { r: 0, g: 0, b: 1, a: 1 }, position: 1 }] }] },
      { guid: G(5), type: "RECTANGLE", parentIndex: P(2, "c"), size: { x: 40, y: 40 }, transform: T(16, 88), fillPaints: [{ type: "IMAGE" }] },
    ],
  };
  let k = 0;
  const d = F._fromFigma(dmsg, () => "d" + ++k);
  assert.equal(d.source, "Figma");
  assert.equal(d.imageSlots, 1);
  const [fr, tx, rc, im] = d.elements;
  assert.deepEqual([fr.type, fr.name, fr.x, fr.y, fr.w, fr.h], ["frame", "Карточка", 100, 50, 200, 120]);
  assert.equal(fr.layout, "v 8 12 16 12 16 center start h");
  assert.deepEqual([fr.style.fill, fr.style.radius, fr.style.shadow], ["#ffffff", 12, "0 4 12 #00000040"]);
  assert.deepEqual([tx.type, tx.frame, tx.x, tx.y, tx.text, tx.style.textColor, tx.style.fontFamily, tx.style.fontWeight], ["text", fr.id, 116, 62, "Заголовок", "#1a1a1a", "Inter", 600]);
  assert.equal(tx.style.valign, "middle"); // центр из Figma сохраняется явно: у текста по умолчанию верх
  assert.equal(tx.style.lineHeight, 19.4); // 100% во Figma — естественная строка шрифта (из раскладки), не кегль
  assert.equal(tx.fixH, true); // фиксированный блок Figma (без «авто-высоты») держит свою высоту
  assert.equal(rc.grow, "w"); // STRETCH поперёк вертикального — «заполнить» по ширине
  assert.deepEqual([rc.style.radii, rc.style.stroke, rc.style.strokeWidth, rc.style.strokeAlign, rc.style.fillGrad], ["8 8 0 0", "#0000ff", 2, "inside", "linear 0 #ff0000ff@0 #0000ffff@100"]);
  assert.equal(im.text || im.name, "Картинка из Figma"); // маленькая заглушка — без подписи, но узнаётся по имени
}
assert.equal(F.isFigma("<p>нет</p>"), false);
assert.equal(await F.fromHtml('<span data-buffer="<!--(figma)AAAA(/figma)-->">', () => "x"), null);
console.log("board figma ok");
