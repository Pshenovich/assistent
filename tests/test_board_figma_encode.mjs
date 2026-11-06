// Макет → Figma (webapp/board-figma.js, сборка npm run build): node tests/test_board_figma_encode.mjs
// Круговой тест: элементы макета → родной буфер Figma (text/html) → разбор тем же board-figma.js → те же элементы.
import fs from "node:fs";
import assert from "node:assert/strict";

globalThis.window = globalThis;
new Function(fs.readFileSync(new URL("../webapp/board-figma.js", import.meta.url), "utf8"))();
const F = globalThis.LeoBoardFigma;

assert.equal(F.toHtml([], {}), null); // схема ещё не распакована
assert.equal(await F.prepare(), true);
assert.equal(F.isReady(), true);

const screen = [
  // Экран: вертикальный автолэйаут, «обнимает» по высоте, тень, скругление.
  { id: "f1", type: "frame", name: "Экран", x: 100, y: 50, w: 360, h: 236, layout: "v 12 24 16 24 16 start start h",
    style: { fill: "#ffffff", radius: 16, shadow: "0 4 12 #00000040" } },
  { id: "t1", type: "text", frame: "f1", x: 116, y: 74, w: 328, h: 31.2, text: "Заголовок\nвторая строка", grow: "w",
    style: { fontSize: 24, fontFamily: "Inter", fontWeight: 600, textColor: "#1a1a1a", lineHeight: 32, letterSpacing: -2, align: "center", textCase: "upper", deco: "underline" } },
  // Кнопка: горизонтальный автолэйаут по центру, «обнимает» по обеим осям, градиент, обводка внутрь.
  { id: "b1", type: "frame", name: "Кнопка", frame: "f1", x: 116, y: 117, w: 120, h: 44, layout: "h 8 12 20 12 20 center center wh",
    style: { fill: "#ff0000", fillGrad: "linear 90 #ff0000ff@0 #0000ff80@100", radius: 22, stroke: "#000000", strokeWidth: 2, strokeAlign: "inside" } },
  { id: "bt", type: "text", frame: "b1", x: 136, y: 129, w: 80, h: 20.8, text: "Войти", autoW: true, style: { fontSize: 16, textColor: "#ffffff" } },
  // Прямоугольник «заполнить» по ширине, свои скругления углов, мин./макс. ширина, прозрачность заливки.
  { id: "r1", type: "rect", frame: "f1", x: 116, y: 173, w: 328, h: 40, grow: "w", minW: 100, maxW: 600,
    style: { fill: "#00ff00", fillOpacity: 0.5, radii: "8 8 0 0", strokeWidth: 0, opacity: 0.8, blur: 4 } },
  { id: "e1", type: "ellipse", frame: "f1", x: 116, y: 225, w: 40, h: 40, ratio: true, style: { fill: "#0000ff", strokeWidth: 0, bgBlur: 10 } },
  // Фрейм без автолэйаута: привязки к краям и вектор.
  { id: "f2", type: "frame", name: "Свободный", x: 600, y: 50, w: 200, h: 100, noclip: true, style: { fillOff: true, fill: "#ffffff", strokeWidth: 1, stroke: "#cccccc" } },
  { id: "r2", type: "rect", frame: "f2", x: 610, y: 60, w: 50, h: 20, pin: "lr b", style: { fill: "#123456", radius: 4, strokeWidth: 0 } },
  { id: "v1", type: "vector", frame: "f2", name: "Иконка", x: 700, y: 60, w: 20, h: 20, vb: [10, 10],
    paths: [{ d: "M0 0L10 0L10 10Z", fill: "#ff0000" }] },
  { id: "img", type: "image", frame: "f2", x: 610, y: 90, w: 40, h: 40, src: "data:image/png;base64,AAAA" },
];

const opts = {};
const html = F.toHtml(screen, opts);
assert.match(html, /^<span data-metadata="<!--\(figmeta\)[A-Za-z0-9+/=]+\(\/figmeta\)-->"><\/span><span data-buffer="<!--\(figma\)[A-Za-z0-9+/=]+\(\/figma\)-->"><\/span>$/);
assert.equal(F.isFigma(html), true);
const meta = JSON.parse(Buffer.from(/\(figmeta\)([^(]+)\(/.exec(html)[1], "base64").toString());
assert.equal(meta.editorType, "design");
assert.match(meta.selectedNodeData, /^1:\d+\|4\|0\|0,1:\d+\|4\|0\|0$/); // два верхних фрейма (FRAME = 4)

// Буфер как у Figma: fig-kiwi, схема (deflate-raw), сообщение (zstd).
const raw = Buffer.from(/\(figma\)([^(]+)\(/.exec(html)[1], "base64");
assert.equal(raw.subarray(0, 8).toString(), "fig-kiwi");
const msg = await F._decode(html);
assert.equal(msg.pasteEditorType, "DESIGN");
const byName = (nm) => msg.nodeChanges.find((n) => n.name === nm);
const fr = byName("Экран");
assert.deepEqual([fr.type, fr.stackMode, fr.stackPrimarySizing, fr.stackCounterSizing], ["FRAME", "VERTICAL", "RESIZE_TO_FIT_WITH_IMPLICIT_SIZE", "FIXED"]);
const title = msg.nodeChanges.find((n) => n.type === "TEXT" && /Заголовок/.test(n.textData.characters));
assert.deepEqual(title.fontName, { family: "Inter", style: "Semi Bold", postscript: "Inter-SemiBold" });
assert.equal(title.textData.lines.length, 2);
assert.equal(title.stackChildAlignSelf, "STRETCH");
assert.equal(byName("Иконка").vectorData.normalizedSize.x, 20);

let k = 0;
const back = F._fromFigma(msg, () => "n" + ++k);
assert.equal(back.source, "Figma");
assert.equal(back.skipped, 0);
assert.equal(back.imageSlots, 0);
const els = back.elements;
assert.equal(els.length, screen.length);
const by = (name) => els.find((e) => e.name === name || e.text === name);
const r1 = (v) => Math.round(v * 10) / 10 + 0;
const geo = (e) => [e.type, r1(e.x), r1(e.y), r1(e.w), r1(e.h)];

const f1 = by("Экран");
assert.deepEqual(geo(f1), ["frame", 100, 50, 360, 236]);
assert.equal(f1.layout, "v 12 24 16 24 16 start start h");
assert.deepEqual([f1.style.fill, f1.style.radius, f1.style.shadow], ["#ffffff", 16, "0 4 12 #00000040"]);
assert.equal(f1.frame, undefined);

const t1 = by("Заголовок\nвторая строка");
assert.deepEqual(geo(t1), ["text", 116, 74, 328, 31.2]);
assert.equal(t1.frame, f1.id);
assert.equal(t1.grow, "w");
assert.deepEqual(
  [t1.style.fontSize, t1.style.fontFamily, t1.style.fontWeight, t1.style.textColor, t1.style.lineHeight, t1.style.letterSpacing, t1.style.align, t1.style.textCase, t1.style.deco],
  [24, "Inter", 600, "#1a1a1a", 32, -2, "center", "upper", "underline"]);

const b1 = by("Кнопка");
assert.deepEqual(geo(b1), ["frame", 116, 117, 120, 44]);
assert.equal(b1.frame, f1.id);
assert.equal(b1.layout, "h 8 12 20 12 20 center center wh");
assert.deepEqual([b1.style.fillGrad, b1.style.radius, b1.style.stroke, b1.style.strokeWidth, b1.style.strokeAlign],
  ["linear 90 #ff0000ff@0 #0000ff80@100", 22, "#000000", 2, "inside"]);

const bt = by("Войти");
assert.equal(bt.frame, b1.id);
assert.equal(bt.autoW, true);
assert.equal(bt.style.textColor, "#ffffff");
assert.equal(bt.style.lineHeight, undefined); // 1.3 от кегля — по умолчанию

const rr = els.find((e) => e.type === "rect" && e.style.fill === "#00ff00");
assert.deepEqual(geo(rr), ["rect", 116, 173, 328, 40]);
assert.deepEqual([rr.grow, rr.minW, rr.maxW], ["w", 100, 600]);
assert.deepEqual([rr.style.fillOpacity, rr.style.radii, rr.style.opacity, rr.style.blur, rr.style.strokeWidth], [0.5, "8 8 0 0", 0.8, 4, 0]);

const el1 = els.find((e) => e.type === "ellipse");
assert.deepEqual(geo(el1), ["ellipse", 116, 225, 40, 40]);
assert.deepEqual([el1.ratio, el1.style.fill, el1.style.bgBlur], [true, "#0000ff", 10]);

const f2 = by("Свободный");
assert.deepEqual(geo(f2), ["frame", 600, 50, 200, 100]);
assert.deepEqual([f2.layout, f2.noclip, f2.style.fillOff, f2.style.stroke, f2.style.strokeWidth], [undefined, true, true, "#cccccc", 1]);

const r2 = els.find((e) => e.type === "rect" && e.style.fill === "#123456");
assert.deepEqual(geo(r2), ["rect", 610, 60, 50, 20]);
assert.deepEqual([r2.frame, r2.pin, r2.style.radius], [f2.id, "lr b", 4]);

const v1 = by("Иконка") || els.find((e) => e.type === "vector");
assert.deepEqual(geo(v1), ["vector", 700, 60, 20, 20]);
assert.equal(v1.frame, f2.id);
assert.deepEqual(v1.paths, [{ d: "M0 0L20 0L20 20L0 0Z", fill: "#ff0000", rule: undefined }]);

const ph = els.find((e) => e.type === "rect" && e.style.fill === "#e5e7eb"); // заглушка; имя «Картинка» видно в слоях Figma
assert.deepEqual(geo(ph), ["rect", 610, 90, 40, 40]);
assert.equal(ph.frame, f2.id);

// Поворот и подпись в фигуре; линия и перо — векторами; цвета палитры доски — через opts.
{
  const html2 = F.toHtml([
    { id: "a", type: "rect", x: 0, y: 0, w: 100, h: 50, rot: 30, style: { fill: "blue", strokeWidth: 0, radius: 0 } },
    { id: "b", type: "rect", x: 200, y: 0, w: 160, h: 60, text: "Подпись", style: { fill: "yellow" } },
    { id: "c", type: "line", x: 0, y: 0, w: 0, h: 0, d: "M0 200L100 250", style: { stroke: "red", strokeWidth: 3 } },
    { id: "d", type: "pen", x: 300, y: 300, w: 50, h: 50, points: [[0, 0], [50, 50]], style: {} },
  ], { fill: (k) => ({ blue: "#d0ebff" })[k], ink: (k) => ({ red: "#e03131", ink: "#1f2328" })[k] });
  const m2 = await F._decode(html2);
  let q = 0;
  const e2 = F._fromFigma(m2, () => "m" + ++q).elements;
  const rot = e2.find((e) => e.type === "rect" && e.style.fill === "#d0ebff");
  assert.deepEqual([r1(rot.x), r1(rot.y), rot.w, rot.h, rot.rot], [0, 0, 100, 50, 30]);
  const wrap = e2.find((e) => e.type === "frame");
  assert.deepEqual([wrap.x, wrap.y, wrap.w, wrap.h, wrap.noclip], [200, 0, 160, 60, true]);
  const lbl = e2.find((e) => e.type === "text");
  assert.deepEqual([lbl.text, lbl.frame, lbl.x, lbl.y, lbl.w, lbl.h, lbl.style.align], ["Подпись", wrap.id, 220, 8, 120, 44, "center"]);
  const [ln, pen] = e2.filter((e) => e.type === "vector");
  assert.deepEqual([ln.x, ln.y, ln.w, ln.h], [0, 200, 100, 50]);
  assert.deepEqual(ln.paths, [{ d: "M0 0L100 50", stroke: "#e03131", sw: 3, cap: "round", join: "round" }]);
  assert.deepEqual([pen.x, pen.y, pen.paths[0].sw, pen.paths[0].stroke], [300, 300, 3, "#1f2328"]);
}
// Готовая раскладка текста (глифы): без неё Figma показывает текстовый слой пустым до первого щелчка.
{
  await F._addFont("Inter", 400, new Uint8Array(fs.readFileSync(new URL("./fixtures/inter-cyrillic-400.woff", import.meta.url))));
  const els3 = [
    { id: "w", type: "frame", name: "Ф", x: 0, y: 0, w: 300, h: 200, style: {} },
    { id: "p", type: "text", frame: "w", x: 10, y: 10, w: 100, h: 20, text: "Привет мир\nещё", style: { fontSize: 20, lineHeight: 24, align: "center" } },
    { id: "q", type: "text", frame: "w", x: 10, y: 120, w: 10, h: 20, autoW: true, text: "Да", style: { fontSize: 20 } },
  ];
  assert.equal(F.fontsReady(els3), true);
  const m3 = await F._decode(F.toHtml(els3, {}));
  const [p, q] = m3.nodeChanges.filter((n) => n.type === "TEXT");
  const dt = p.derivedTextData;
  // «Привет мир» не влезает в 100 — перенос по пробелу; «ещё» — с новой строки.
  assert.deepEqual(dt.baselines.map((b) => [b.firstCharacter, b.endCharacter, b.lineY, b.lineHeight]), [[0, 7, 0, 24], [7, 10, 24, 24], [11, 14, 48, 24]]);
  assert.equal(dt.glyphs.length, 12); // пробел и перевод строки — без глифа
  assert.deepEqual([p.size.x, p.size.y], [100, 72]);
  const b0 = dt.baselines[0];
  assert.ok(b0.position.x > 0 && Math.abs(b0.position.x * 2 + b0.width - 100) < 0.01); // по центру
  assert.ok(b0.position.y > 12 && b0.position.y < 24); // базовая линия внутри первой строки
  const blob = m3.blobs[dt.glyphs[0].commandsBlob].bytes;
  assert.equal(blob[0], 1); // контур «П»: M … Z, в долях em
  assert.equal(blob[blob.length - 1], 0);
  assert.ok(dt.fontMetaData[0].fontLineHeight > 1 && dt.fontMetaData[0].fontLineHeight < 1.5);
  // «По содержимому» — ширина по тексту, а не по старой рамке.
  assert.ok(q.size.x > 20 && q.size.x < 40, String(q.size.x));
  assert.equal(q.derivedTextData.layoutSize.x, q.size.x);
}
console.log("board figma encode ok");
