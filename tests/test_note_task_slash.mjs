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

console.log("ok");
