(function (g) {
  var PREFIX = "leo_note_drafts_v1_";

  function memoryStorage() {
    var mem = {};
    return {
      getItem: function (k) {
        return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null;
      },
      setItem: function (k, v) {
        mem[k] = String(v);
      },
      removeItem: function (k) {
        delete mem[k];
      },
    };
  }

  function defaultStorage() {
    try {
      if (typeof localStorage !== "undefined" && localStorage) return localStorage;
    } catch (_) {}
    return memoryStorage();
  }

  function nowTs() {
    return Date.now();
  }

  function revNum(v) {
    var n = parseInt(v, 10);
    return isFinite(n) && n > 0 ? n : 0;
  }

  function normText(s) {
    return String(s || "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function bodiesEqual(a, b) {
    return normText(a) === normText(b);
  }

  function bucketKey(userId) {
    return PREFIX + String(userId || "anon");
  }

  function itemKey(kind, itemId) {
    return String(kind || "local") + ":" + String(itemId || "");
  }

  function createNoteDrafts(storage) {
    var store = storage || defaultStorage();

    function readBucket(userId) {
      try {
        var raw = store.getItem(bucketKey(userId));
        var obj = raw ? JSON.parse(raw) : {};
        return obj && typeof obj === "object" ? obj : {};
      } catch (_) {
        return {};
      }
    }

    function writeBucket(userId, obj) {
      store.setItem(bucketKey(userId), JSON.stringify(obj || {}));
    }

    function putDraft(userId, kind, itemId, fields) {
      var id = String(itemId || "").trim();
      if (!id) return null;
      var bucket = readBucket(userId);
      var key = itemKey(kind, id);
      var prev = bucket[key] || {};
      var next = {
        kind: String(kind || "local"),
        itemId: id,
        title: fields && fields.title != null ? String(fields.title) : String(prev.title || ""),
        body: fields && fields.body != null ? String(fields.body) : String(prev.body || ""),
        clientTs: (fields && fields.clientTs) || nowTs(),
        baseRevision: revNum(fields && fields.baseRevision != null ? fields.baseRevision : prev.baseRevision),
        baseUpdatedAt: String(
          (fields && fields.baseUpdatedAt != null ? fields.baseUpdatedAt : prev.baseUpdatedAt) || ""
        ),
        conflict: fields && Object.prototype.hasOwnProperty.call(fields, "conflict") ? fields.conflict : prev.conflict || null,
      };
      bucket[key] = next;
      writeBucket(userId, bucket);
      return next;
    }

    function getDraft(userId, kind, itemId) {
      var id = String(itemId || "").trim();
      if (!id) return null;
      var row = readBucket(userId)[itemKey(kind, id)];
      return row && typeof row === "object" ? row : null;
    }

    function clearDraft(userId, kind, itemId) {
      var id = String(itemId || "").trim();
      if (!id) return;
      var bucket = readBucket(userId);
      var key = itemKey(kind, id);
      if (!bucket[key]) return;
      delete bucket[key];
      writeBucket(userId, bucket);
    }

    function listUnsynced(userId, kind) {
      var bucket = readBucket(userId);
      var want = kind ? String(kind) : "";
      var out = [];
      Object.keys(bucket).forEach(function (k) {
        var row = bucket[k];
        if (!row || typeof row !== "object") return;
        if (want && String(row.kind || "") !== want) return;
        out.push(row);
      });
      return out;
    }

    function remapItemId(userId, kind, fromId, toId) {
      var src = getDraft(userId, kind, fromId);
      if (!src) return null;
      var next = Object.assign({}, src, { itemId: String(toId || "") });
      putDraft(userId, kind, toId, next);
      clearDraft(userId, kind, fromId);
      return next;
    }

    function hydrateNoteFromDraft(server, draft) {
      var title = String((server && (server.title || server.content)) || "");
      var body = String((server && (server.body || server.description)) || "");
      var revision = revNum(server && server.revision);
      if (!draft) {
        return { use: "server", title: title, body: body, draft: null };
      }
      var dTitle = String(draft.title || "");
      var dBody = String(draft.body || "");
      if (bodiesEqual(dTitle, title) && bodiesEqual(dBody, body)) {
        return { use: "server", title: title, body: body, draft: draft };
      }
      if (draft.conflict && draft.conflict.body != null) {
        return { use: "conflict", title: title, body: body, draft: draft };
      }
      var base = revNum(draft.baseRevision);
      if (!revision || base === revision) {
        return { use: "draft", title: dTitle, body: dBody, draft: draft };
      }
      if (revision > base) {
        return { use: "conflict", title: title, body: body, draft: draft };
      }
      return { use: "draft", title: dTitle, body: dBody, draft: draft };
    }

    function mergeKeepBoth(serverBody, draftBody) {
      var a = String(serverBody || "").trim();
      var b = String(draftBody || "").trim();
      if (!b) return a;
      if (!a) return b;
      if (bodiesEqual(a, b)) return a;
      return (
        a +
        '<p><br></p><hr><p><strong>Несохранённый черновик</strong></p>' +
        b
      );
    }

    return {
      putDraft: putDraft,
      getDraft: getDraft,
      clearDraft: clearDraft,
      listUnsynced: listUnsynced,
      remapItemId: remapItemId,
      hydrateNoteFromDraft: hydrateNoteFromDraft,
      mergeKeepBoth: mergeKeepBoth,
      bodiesEqual: bodiesEqual,
      revNum: revNum,
    };
  }

  var api = createNoteDrafts();
  api.create = createNoteDrafts;
  g.NoteDrafts = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
