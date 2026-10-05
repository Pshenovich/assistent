import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const file = path.join(path.dirname(fileURLToPath(import.meta.url)), "../webapp/note-drafts.js");
const sandbox = { console: console };
sandbox.globalThis = sandbox;
vm.runInNewContext(fs.readFileSync(file, "utf8"), sandbox);
const NoteDrafts = sandbox.NoteDrafts;
assert.ok(NoteDrafts);

const mem = {};
const store = NoteDrafts.create({
  getItem: function (k) {
    return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null;
  },
  setItem: function (k, v) {
    mem[k] = String(v);
  },
  removeItem: function (k) {
    delete mem[k];
  },
});

store.putDraft("7", "local", "42", {
  title: "Черновик",
  body: "<p>с телефона</p>",
  baseRevision: 3,
  baseUpdatedAt: "2026-10-05T08:00:00Z",
});
const got = store.getDraft("7", "local", "42");
assert.equal(got.title, "Черновик");
assert.equal(got.body, "<p>с телефона</p>");
assert.equal(got.baseRevision, 3);

store.remapItemId("7", "local", "42", "99");
assert.equal(store.getDraft("7", "local", "42"), null);
assert.equal(store.getDraft("7", "local", "99").body, "<p>с телефона</p>");

const sameRev = store.hydrateNoteFromDraft(
  { title: "Старое", body: "<p>сервер</p>", revision: 3 },
  store.getDraft("7", "local", "99")
);
assert.equal(sameRev.use, "draft");
assert.equal(sameRev.body, "<p>с телефона</p>");

const conflict = store.hydrateNoteFromDraft(
  { title: "Десктоп", body: "<p>с компьютера</p>", revision: 4 },
  store.getDraft("7", "local", "99")
);
assert.equal(conflict.use, "conflict");
assert.equal(conflict.body, "<p>с компьютера</p>");

const sameText = store.hydrateNoteFromDraft(
  { title: "Черновик", body: "<p>с телефона</p>", revision: 4 },
  store.getDraft("7", "local", "99")
);
assert.equal(sameText.use, "server");

const merged = store.mergeKeepBoth("<p>сервер</p>", "<p>телефон</p>");
assert.match(merged, /сервер/);
assert.match(merged, /Несохранённый черновик/);
assert.match(merged, /телефон/);

store.putDraft("7", "local", "99", {
  title: "Черновик",
  body: "<p>с телефона</p>",
  baseRevision: 3,
  conflict: { title: "Десктоп", body: "<p>с компьютера</p>", revision: 4 },
});
const flagged = store.hydrateNoteFromDraft(
  { title: "Десктоп", body: "<p>с компьютера</p>", revision: 4 },
  store.getDraft("7", "local", "99")
);
assert.equal(flagged.use, "conflict");

store.clearDraft("7", "local", "99");
assert.equal(store.getDraft("7", "local", "99"), null);
assert.equal(store.listUnsynced("7", "local").length, 0);

console.log("ok");
