import assert from "node:assert/strict";

function paragraphTitleBeforeSlash(text) {
  var raw = String(text || "");
  var idx = raw.lastIndexOf("/");
  if (idx < 0) return "";
  return raw
    .slice(0, idx)
    .replace(/\uFFFC/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

assert.equal(paragraphTitleBeforeSlash("Купить молоко /сегодня 15:00"), "Купить молоко");
assert.equal(paragraphTitleBeforeSlash("Купить молоко/сегодня 15:00"), "Купить молоко");
assert.equal(paragraphTitleBeforeSlash("/сегодня 15:00"), "");
assert.equal(paragraphTitleBeforeSlash("Согласовать договор \uFFFC /завтра 10:00"), "Согласовать договор");
assert.equal(paragraphTitleBeforeSlash(""), "");

function normalizeTaskChipLabel(label) {
  var s = String(label || "").trim();
  if (/^Задача\s+/i.test(s)) s = s.replace(/^Задача\s+/i, "").trim();
  return s;
}

assert.equal(normalizeTaskChipLabel("Задача 5 окт, 10:40"), "5 окт, 10:40");
assert.equal(normalizeTaskChipLabel("5 окт, 10:40"), "5 окт, 10:40");
assert.equal(normalizeTaskChipLabel(""), "");

console.log("ok");
