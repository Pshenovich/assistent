import assert from "node:assert/strict";
import { alreadyApplied, mergeBlocks } from "../webapp/editor/yjs-merge.mjs";

const eq = (a, b) => a === b;
const m = (o, n, c) => mergeBlocks(o, n, c, eq);

// Бот дописал строку в отстающий HTML — правка человека (которой нет в HTML) сохраняется.
assert.deepEqual(
  m(["A", "B"], ["A", "B", "bot"], ["A+offline", "B"]),
  ["A+offline", "B", "bot"]
);
// Вставка в середину — после того же соседа.
assert.deepEqual(m(["A", "B", "C"], ["A", "B", "X", "C"], ["A", "B", "C", "D"]), ["A", "B", "X", "C", "D"]);
// Вставка в начало.
assert.deepEqual(m(["A"], ["T", "A"], ["A", "Z"]), ["T", "A", "Z"]);
// Замена строки (отметка «Фокуса»), которую человек не трогал.
assert.deepEqual(m(["A", "B", "C"], ["A", "B✓", "C"], ["A", "B", "C", "new"]), ["A", "B✓", "C", "new"]);
// Человек правит ту же строку — остаётся человеческая версия.
assert.deepEqual(m(["A", "B", "C"], ["A", "B✓", "C"], ["A", "B-edited", "C"]), ["A", "B-edited", "C"]);
// Удаление нетронутого блока.
assert.deepEqual(m(["A", "B", "C"], ["A", "C"], ["A", "B", "C", "D"]), ["A", "C", "D"]);
// Ничего не изменилось — документ как есть.
assert.deepEqual(m(["A", "B"], ["A", "B"], ["A", "x", "B"]), ["A", "x", "B"]);
// Сосед сверху изменён человеком — вставка после ближайшего сохранившегося.
assert.deepEqual(m(["A", "B"], ["A", "B", "bot"], ["A", "B-human"]), ["A", "B-human", "bot"]);
// Человек дописал свою строку в конец, бот тоже — обе на месте.
assert.deepEqual(m(["A"], ["A", "bot"], ["A", "mine"]), ["A", "bot", "mine"]);
// Полная замена (транскрибация переписала саммари), человек ничего не трогал.
assert.deepEqual(m(["A", "B"], ["X", "Y"], ["A", "B"]), ["X", "Y"]);
// Повторная доставка слияния: правка уже в документе — не сливаем второй раз.
const done = (o, n, c) => alreadyApplied(o, n, c, eq);
assert.equal(done(["A"], ["A", "bot"], ["A", "bot", "mine"]), true); // дописанное уже есть
assert.equal(done(["A"], ["A", "bot"], ["A", "mine"]), false);
assert.equal(done(["A", "B"], ["X", "Y"], ["X", "Y"]), true); // полная замена уже прошла
assert.equal(done(["T1", "T2"], ["X", "Y"], ["T1", "T2"]), false); // случай с прода: в журнале старое
assert.equal(done(["A", "B", "C"], ["A", "C"], ["A", "B", "C"]), false); // удаление ещё не прошло
assert.equal(done(["A", "B", "C"], ["A", "C"], ["A", "C"]), true);
console.log("yjs merge ok");
