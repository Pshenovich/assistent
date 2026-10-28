import { Editor, Extension, Mark, Node } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Placeholder from "@tiptap/extension-placeholder";
import Underline from "@tiptap/extension-underline";
import TextStyle from "@tiptap/extension-text-style";
import Link from "@tiptap/extension-link";
import Image from "@tiptap/extension-image";
import { ManagedTable, ManagedTableCell, ManagedTableHeader, ManagedTableRow } from "./table-manage.mjs";
import { DOMSerializer, Slice } from "@tiptap/pm/model";
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { CellSelection, cellAround, isInTable } from "@tiptap/pm/tables";
import { trailingNode } from "@tiptap/pm/trailing-node";
import { canJoin } from "@tiptap/pm/transform";
import { keydownHandler } from "@tiptap/pm/keymap";
import { createDocument } from "@tiptap/core";
import * as Y from "yjs";
import { mergeBlocks } from "./yjs-merge.mjs";
import {
  defaultDeleteFilter,
  defaultProtectedNodes,
  prosemirrorToYXmlFragment,
  yXmlFragmentToProseMirrorRootNode,
  redo as yRedo,
  undo as yUndo,
  ySyncPlugin,
  ySyncPluginKey,
  yUndoPlugin,
  yUndoPluginKey,
} from "y-prosemirror";

/** Транзакция пришла из совместного документа Yjs (чужая правка / пересборка) — не наша. */
function isRemoteYTr(tr) {
  var m = tr && tr.getMeta(ySyncPluginKey);
  return !!(m && m.isChangeOrigin);
}

/** Плагины, дописывающие правки (appendTransaction), не должны отвечать на чужие правки:
 * автор уже дописал их у себя, а ответ каждого участника задвоит правку. */
function skipRemoteY(plugin) {
  var orig = plugin.spec.appendTransaction;
  if (typeof orig === "function") {
    plugin.spec.appendTransaction = function (trs) {
      if (trs.some(isRemoteYTr)) return null;
      return orig.apply(this, arguments);
    };
  }
  return plugin;
}

var Y_FRAGMENT = "prosemirror";

/** ID строки (data-leo-item): по нему задача саммари и пункт Фокуса держат связь со строкой,
 * как бы её ни правили. Enter не копирует ID на новую строку; дубль (строку скопировали) — снимаем. */
var LEO_LINE_TYPES = ["paragraph", "heading", "listItem", "taskItem", "tableCell", "tableHeader"];
var LEO_ITEM_RE = /^i[0-9a-f]{10}$/;
function newLeoItemId() {
  var bytes = new Uint8Array(5);
  crypto.getRandomValues(bytes);
  return "i" + Array.prototype.map.call(bytes, function (x) { return (x < 16 ? "0" : "") + x.toString(16); }).join("");
}
var LeoLineId = Extension.create({
  name: "leoLineId",
  addGlobalAttributes: function () {
    return [
      {
        types: LEO_LINE_TYPES,
        attributes: {
          leoItem: {
            default: null,
            keepOnSplit: false,
            parseHTML: function (el) {
              var v = el.getAttribute("data-leo-item");
              return v && LEO_ITEM_RE.test(v) ? v : null;
            },
            renderHTML: function (attrs) {
              return attrs.leoItem ? { "data-leo-item": attrs.leoItem } : {};
            },
          },
        },
      },
    ];
  },
  addProseMirrorPlugins: function () {
    return [
      new Plugin({
        key: new PluginKey("leoLineIdDedupe"),
        appendTransaction: function (trs, _old, state) {
          if (!trs.some(function (tr) { return tr.docChanged; })) return null;
          if (trs.some(isRemoteYTr)) return null;
          var seen = {};
          var tr = null;
          state.doc.descendants(function (node, pos, parent, index) {
            var id = node.attrs && node.attrs.leoItem;
            if (!id) return;
            if (seen[id]) {
              tr = tr || state.tr;
              tr.setNodeMarkup(pos, undefined, Object.assign({}, node.attrs, { leoItem: null }));
              return;
            }
            seen[id] = true;
            // Enter в начале строки: ID остался на пустой строке сверху, текст ушёл вниз — ID за текстом.
            var next = parent && index + 1 < parent.childCount ? parent.child(index + 1) : null;
            if (!node.textContent && next && next.type === node.type && !next.attrs.leoItem && next.textContent) {
              tr = tr || state.tr;
              tr.setNodeMarkup(pos, undefined, Object.assign({}, node.attrs, { leoItem: null }));
              tr.setNodeMarkup(pos + node.nodeSize, undefined, Object.assign({}, next.attrs, { leoItem: id }));
            }
          });
          return tr;
        },
      }),
    ];
  },
});

/** Always keep a paragraph after non-paragraph blocks (table, image, …) so typing below works. */
var TrailingParagraph = Extension.create({
  name: "trailingParagraph",
  addProseMirrorPlugins: function () {
    return [
      skipRemoteY(
        trailingNode({
          nodeName: "paragraph",
          ignoredNodes: ["paragraph"],
        })
      ),
    ];
  },
  onCreate: function () {
    var editor = this.editor;
    if (!editor || !editor.state) return;
    var last = editor.state.doc.lastChild;
    if (!last || last.type.name === "paragraph") return;
    var paragraph = editor.state.schema.nodes.paragraph;
    if (!paragraph) return;
    editor.view.dispatch(
      editor.state.tr.insert(editor.state.doc.content.size, paragraph.create())
    );
  },
});

var IMAGE_WIDTH_MIN_PCT = 15;
var IMAGE_WIDTH_MAX_PCT = 100;

function clampImageWidthPct(n) {
  var v = Math.round(Number(n));
  if (!isFinite(v)) return IMAGE_WIDTH_MAX_PCT;
  if (v < IMAGE_WIDTH_MIN_PCT) return IMAGE_WIDTH_MIN_PCT;
  if (v > IMAGE_WIDTH_MAX_PCT) return IMAGE_WIDTH_MAX_PCT;
  return v;
}

function parseImageWidthPct(el) {
  if (!el || typeof el.getAttribute !== "function") return null;
  var widthAttr = String(el.getAttribute("width") || "").trim();
  if (/^\d{1,3}%$/.test(widthAttr)) {
    return clampImageWidthPct(parseInt(widthAttr, 10));
  }
  var style = String(el.getAttribute("style") || "");
  var m = style.match(/(?:^|;)\s*width\s*:\s*(\d+(?:\.\d+)?)\s*%/i);
  if (m) return clampImageWidthPct(parseFloat(m[1]));
  return null;
}

function applyImageWrapWidth(wrap, img, widthPct) {
  if (!wrap || !img) return;
  if (widthPct == null || widthPct === "") {
    wrap.style.width = "";
    img.style.width = "";
    return;
  }
  var pct = clampImageWidthPct(widthPct);
  wrap.style.width = pct + "%";
  img.style.width = "100%";
}

var ResizableImage = Image.extend({
  addAttributes: function () {
    var parent = typeof this.parent === "function" ? this.parent() : {};
    return Object.assign({}, parent, {
      width: {
        default: null,
        parseHTML: function (element) {
          return parseImageWidthPct(element);
        },
        renderHTML: function (attributes) {
          if (attributes.width == null || attributes.width === "") return {};
          var pct = clampImageWidthPct(attributes.width);
          return {
            width: String(pct) + "%",
            style: "width: " + String(pct) + "%",
          };
        },
      },
    });
  },
  addNodeView: function () {
    return function (props) {
      var node = props.node;
      var editor = props.editor;
      var getPos = props.getPos;
      var wrap = document.createElement("div");
      wrap.className = "note-editor-image-wrap";
      wrap.setAttribute("data-drag-handle", "");

      var img = document.createElement("img");
      img.className = "note-editor-image";
      img.draggable = false;
      img.src = node.attrs.src || "";
      img.alt = node.attrs.alt || "";
      if (node.attrs.title) img.title = node.attrs.title;
      applyImageWrapWidth(wrap, img, node.attrs.width);

      var handle = document.createElement("span");
      handle.className = "note-editor-image-resize";
      handle.contentEditable = "false";
      handle.setAttribute("aria-hidden", "true");

      wrap.appendChild(img);
      wrap.appendChild(handle);

      var pendingWidth = null;
      var activePointerId = null;

      function commitWidth() {
        if (pendingWidth == null) return;
        if (typeof getPos !== "function") return;
        var pos = getPos();
        if (typeof pos !== "number") return;
        var nextAttrs = Object.assign({}, node.attrs, { width: pendingWidth });
        var tr = editor.view.state.tr.setNodeMarkup(pos, undefined, nextAttrs);
        editor.view.dispatch(tr);
        pendingWidth = null;
      }

      function onPointerMove(e) {
        if (activePointerId != null && e.pointerId !== activePointerId) return;
        var parent = wrap.parentElement;
        if (!parent) return;
        var parentWidth = parent.getBoundingClientRect().width;
        if (!(parentWidth > 0)) return;
        var left = wrap.getBoundingClientRect().left;
        var newPx = e.clientX - left;
        var pct = clampImageWidthPct((newPx / parentWidth) * 100);
        pendingWidth = pct;
        applyImageWrapWidth(wrap, img, pct);
      }

      function onPointerUp(e) {
        if (activePointerId != null && e.pointerId !== activePointerId) return;
        activePointerId = null;
        wrap.classList.remove("is-resizing");
        try {
          handle.releasePointerCapture(e.pointerId);
        } catch (_) {}
        document.removeEventListener("pointermove", onPointerMove);
        document.removeEventListener("pointerup", onPointerUp);
        document.removeEventListener("pointercancel", onPointerUp);
        commitWidth();
      }

      function onPointerDown(e) {
        if (e.button != null && e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        activePointerId = e.pointerId;
        wrap.classList.add("is-resizing");
        try {
          handle.setPointerCapture(e.pointerId);
        } catch (_) {}
        document.addEventListener("pointermove", onPointerMove);
        document.addEventListener("pointerup", onPointerUp);
        document.addEventListener("pointercancel", onPointerUp);
        onPointerMove(e);
      }

      handle.addEventListener("pointerdown", onPointerDown);

      return {
        dom: wrap,
        update: function (updated) {
          if (!updated || updated.type.name !== "image") return false;
          node = updated;
          img.src = updated.attrs.src || "";
          img.alt = updated.attrs.alt || "";
          if (updated.attrs.title) img.title = updated.attrs.title;
          else img.removeAttribute("title");
          if (pendingWidth == null) {
            applyImageWrapWidth(wrap, img, updated.attrs.width);
          }
          return true;
        },
        selectNode: function () {
          wrap.classList.add("is-selected");
        },
        deselectNode: function () {
          wrap.classList.remove("is-selected");
        },
        destroy: function () {
          handle.removeEventListener("pointerdown", onPointerDown);
          document.removeEventListener("pointermove", onPointerMove);
          document.removeEventListener("pointerup", onPointerUp);
          document.removeEventListener("pointercancel", onPointerUp);
        },
      };
    };
  },
});

/* Фото в заметке хранятся в теле (data URL): снимок с телефона — мегабайты, из-за них тормозил редактор
   и не успевало сохранение. Ужимаем: длинная сторона до IMAGE_MAX_SIDE, JPEG; PNG с прозрачностью — PNG. */
var IMAGE_MAX_SIDE = 1600;
var IMAGE_KEEP_CHARS = 480000; // ~350 КБ — такие не трогаем

function compressImageDataUrl(src) {
  return new Promise(function (resolve) {
    var s = String(src || "");
    if (!/^data:image\/(jpeg|jpg|png|webp|heic|heif)/i.test(s) || s.length < IMAGE_KEEP_CHARS) {
      resolve(s);
      return;
    }
    var img = new window.Image();
    img.onload = function () {
      try {
        var w = img.naturalWidth || img.width;
        var h = img.naturalHeight || img.height;
        if (!w || !h) return resolve(s);
        var k = Math.min(1, IMAGE_MAX_SIDE / Math.max(w, h));
        var cw = Math.max(1, Math.round(w * k));
        var ch = Math.max(1, Math.round(h * k));
        var canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        var ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, cw, ch);
        var alpha = false;
        if (/^data:image\/png/i.test(s)) {
          var px = ctx.getImageData(0, 0, cw, ch).data;
          for (var i = 3; i < px.length; i += 4) {
            if (px[i] < 250) {
              alpha = true;
              break;
            }
          }
        }
        var out = alpha ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.82);
        resolve(out && out.length < s.length ? out : s);
      } catch (_) {
        resolve(s);
      }
    };
    img.onerror = function () {
      resolve(s);
    };
    img.src = s;
  });
}

var IMAGE_MAX_FILE = 25 * 1024 * 1024;

function imageFilesFrom(dataTransfer) {
  return Array.prototype.slice.call((dataTransfer && dataTransfer.files) || []).filter(function (f) {
    return /^image\//i.test(String(f.type || "")) || /\.(jpe?g|png|gif|webp|heic|heif)$/i.test(String(f.name || ""));
  });
}

function readFileAsDataUrl(file) {
  return new Promise(function (resolve) {
    var reader = new FileReader();
    reader.onload = function () {
      resolve(String(reader.result || ""));
    };
    reader.onerror = function () {
      resolve("");
    };
    reader.readAsDataURL(file);
  });
}

// Фото, перетащенные в заметку или вставленные из буфера (скриншот): по очереди, в место броска
// (pos = null — к курсору), ужатые как при вставке скрепкой.
function insertImageFiles(editor, files, pos) {
  var tooBig = files.filter(function (f) {
    return f.size > IMAGE_MAX_FILE;
  });
  if (tooBig.length) window.alert("Фото больше 25 МБ не вставляются: " + tooBig.map(function (f) { return f.name || "фото"; }).join(", "));
  var at = pos;
  var chain = Promise.resolve();
  files
    .filter(function (f) {
      return f.size <= IMAGE_MAX_FILE;
    })
    .forEach(function (file) {
      chain = chain
        .then(function () {
          return readFileAsDataUrl(file);
        })
        .then(compressImageDataUrl)
        .then(function (src) {
          if (!src || editor.isDestroyed) return;
          var node = { type: "image", attrs: { src: src, alt: file.name || "Фото" } };
          if (at == null) {
            editor.chain().focus().insertContent([node, { type: "paragraph" }]).run();
          } else {
            editor.chain().focus().insertContentAt(Math.min(at, editor.state.doc.content.size), node).run();
            at = editor.state.selection.to;
          }
        });
    });
  return chain;
}

// Уже сохранённые тяжёлые фото ужимаем после открытия (без записи в историю «Отменить»).
function shrinkHeavyImages(editor) {
  var queue = [];
  editor.state.doc.descendants(function (node) {
    if (node.type.name === "image" && String(node.attrs.src || "").length > IMAGE_KEEP_CHARS * 2) {
      queue.push(String(node.attrs.src));
    }
  });
  (function next() {
    if (!queue.length || editor.isDestroyed) return;
    var src = queue.shift();
    compressImageDataUrl(src).then(function (out) {
      if (editor.isDestroyed) return;
      if (out !== src) {
        var tr = editor.state.tr;
        editor.state.doc.descendants(function (node, pos) {
          if (node.type.name === "image" && node.attrs.src === src) {
            tr.setNodeMarkup(pos, undefined, Object.assign({}, node.attrs, { src: out }));
          }
        });
        if (tr.docChanged) editor.view.dispatch(tr.setMeta("addToHistory", false).setMeta("leoNoScroll", true));
      }
      next();
    });
  })();
}

function isIOSDevice() {
  if (typeof navigator === "undefined") return false;
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

/* Markdown-заголовки при наборе: «# », «## », «### », «#### » в начале строки → заголовок.
   Стандартное правило Tiptap ловит только handleTextInput, а на телефоне (автозамена, Gboard, диктовка)
   текст приходит через композицию/DOM — поэтому проверяем саму строку после каждого ввода. */
var MD_HEADING_RE = /^(#{1,4})[ \u00a0]/;
const MarkdownHeadingShortcut = Extension.create({
  name: "markdownHeadingShortcut",
  addProseMirrorPlugins: function () {
    return [
      new Plugin({
        key: new PluginKey("markdownHeadingShortcut"),
        appendTransaction: function (trs, oldState, state) {
          if (!trs.some(function (tr) { return tr.docChanged; })) return null;
          if (trs.some(function (tr) { return isRemoteYTr(tr) || tr.getMeta("paste") || tr.getMeta("uiEvent") === "paste" || tr.getMeta("history$") || tr.getMeta("addToHistory") === false; })) return null;
          var sel = state.selection;
          if (!sel.empty) return null;
          var $from = sel.$from;
          var para = $from.parent;
          if (para.type.name !== "paragraph") return null;
          // Только обычный абзац верхнего уровня или в цитате — не в списке и не в таблице.
          for (var d = $from.depth - 1; d > 0; d--) {
            var t = $from.node(d).type.name;
            if (t !== "blockquote") return null;
          }
          var m = MD_HEADING_RE.exec(para.textContent);
          if (!m || $from.parentOffset < m[0].length) return null;
          // Маркер должен появиться этим вводом: строку, которая уже начиналась с «# », не трогаем.
          var old$ = oldState.selection.$from;
          if (old$.parent.type.name === "paragraph" && MD_HEADING_RE.test(old$.parent.textContent)) return null;
          var heading = state.schema.nodes.heading;
          if (!heading) return null;
          var start = $from.start();
          var tr = state.tr.delete(start, start + m[0].length);
          tr.setBlockType(start, start, heading, { level: m[1].length });
          return tr;
        },
      }),
    ];
  },
});

/* Два столбца: «[[» в конце абзаца — этот абзац (текст с последнего Enter) уходит в левый столбец, справа — пустой столбец с курсором, между ними линия. Backspace в начале
   правого столбца — столбцы снова обычным текстом. В HTML: div.note-columns > div.note-column ×2. */
const NoteColumn = Node.create({
  name: "noteColumn",
  content: "block+",
  isolating: true,
  defining: true,
  parseHTML: function () {
    return [{ tag: "div.note-column" }];
  },
  renderHTML: function () {
    return ["div", { class: "note-column" }, 0];
  },
});

// Левый столбец — только текущий абзац (текст с последнего Enter); lastIndex — до какого блока (выделение).
function columnsSourceRange(doc, index, lastIndex) {
  var last = lastIndex == null ? index : lastIndex;
  var from = 0;
  for (var k = 0; k < index; k += 1) from += doc.child(k).nodeSize;
  var to = from;
  for (var j = index; j <= last; j += 1) to += doc.child(j).nodeSize;
  return { from: from, to: to };
}

// Обернуть блоки [from, to) верхнего уровня в два столбца, курсор — в правый.
function wrapInColumns(tr, from, to) {
  var schema = tr.doc.type.schema;
  var left = tr.doc.slice(from, to).content;
  if (!left.childCount) left = schema.nodes.paragraph.create();
  var leftCol = schema.nodes.noteColumn.create(null, left);
  var rightCol = schema.nodes.noteColumn.create(null, schema.nodes.paragraph.create());
  tr.replaceWith(from, to, schema.nodes.noteColumns.create(null, [leftCol, rightCol]));
  tr.setSelection(TextSelection.create(tr.doc, from + 1 + leftCol.nodeSize + 2));
  return tr;
}

// Ширина левого столбца в % (20–80): тянуть линию между столбцами мышью, как границу в таблице.
var COLS_MIN = 20;
var COLS_MAX = 80;

function clampSplit(v) {
  if (v == null || v === "") return null; // ширина не задана — поровну
  var n = Math.round(Number(v));
  if (!isFinite(n) || n === 50) return null;
  return Math.max(COLS_MIN, Math.min(COLS_MAX, n));
}

function applyColumnsSplit(dom, split) {
  if (split) {
    dom.style.setProperty("--note-col-l", split + "fr");
    dom.style.setProperty("--note-col-r", 100 - split + "fr");
    dom.setAttribute("data-split", String(split));
  } else {
    dom.style.removeProperty("--note-col-l");
    dom.style.removeProperty("--note-col-r");
    dom.removeAttribute("data-split");
  }
}

// Десктоп: курсор у линии между столбцами — ↔, тянется; отпустили — ширина в заметке.
function createColumnsResizePlugin() {
  return new Plugin({
    key: new PluginKey("noteColumnsResize"),
    view: function (view) {
      var drag = null;
      var hovered = null;
      function divider(colsEl) {
        var second = colsEl && colsEl.children[1];
        if (!second) return null;
        var first = colsEl.children[0];
        // Столбцы друг под другом (узкий экран) — тянуть нечего.
        if (first && second.getBoundingClientRect().top >= first.getBoundingClientRect().bottom - 1) return null;
        return second.getBoundingClientRect().left;
      }
      function hitColumns(e) {
        var el = e.target && e.target.closest && e.target.closest(".note-columns");
        if (!el || !view.dom.contains(el)) return null;
        var x = divider(el);
        return x != null && Math.abs(e.clientX - x) <= 6 ? el : null;
      }
      function setHover(el) {
        if (hovered === el) return;
        if (hovered) hovered.classList.remove("is-col-resize");
        hovered = el;
        if (hovered) hovered.classList.add("is-col-resize");
      }
      function onMove(e) {
        if (drag) {
          var r = drag.el.getBoundingClientRect();
          var pct = Math.max(COLS_MIN, Math.min(COLS_MAX, ((e.clientX - r.left) / r.width) * 100));
          drag.split = Math.round(pct);
          applyColumnsSplit(drag.el, drag.split);
          return;
        }
        if (e.pointerType !== "mouse" || e.buttons) return;
        setHover(hitColumns(e));
      }
      function onDown(e) {
        if (e.pointerType !== "mouse" || e.button !== 0) return;
        var el = hitColumns(e);
        if (!el) return;
        var pos;
        try {
          pos = view.posAtDOM(el, 0) - 1;
        } catch (_) {
          return;
        }
        var node = view.state.doc.nodeAt(pos);
        if (!node || node.type.name !== "noteColumns") return;
        e.preventDefault();
        e.stopPropagation();
        drag = { el: el, pos: pos, split: node.attrs.split || 50 };
        el.classList.add("is-col-resizing");
        document.addEventListener("pointermove", onMove);
        document.addEventListener("pointerup", onUp);
        document.addEventListener("pointercancel", onUp);
      }
      // ProseMirror ловит mousedown — пока тянем линию, до него не доходит (иначе выделял бы текст).
      function onMouseDown(e) {
        if (drag) {
          e.preventDefault();
          e.stopPropagation();
        }
      }
      function onUp() {
        var d = drag;
        drag = null;
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("pointercancel", onUp);
        if (!d) return;
        d.el.classList.remove("is-col-resizing");
        var node = view.state.doc.nodeAt(d.pos);
        if (!node || node.type.name !== "noteColumns") return;
        var split = clampSplit(d.split);
        if ((node.attrs.split || null) === split) return;
        view.dispatch(
          view.state.tr
            .setNodeMarkup(d.pos, null, Object.assign({}, node.attrs, { split: split }))
            .setMeta("leoNoScroll", true)
        );
      }
      function onLeave() {
        if (!drag) setHover(null);
      }
      view.dom.addEventListener("pointermove", onMove);
      view.dom.addEventListener("pointerleave", onLeave);
      view.dom.addEventListener("pointerdown", onDown, true);
      view.dom.addEventListener("mousedown", onMouseDown, true);
      return {
        destroy: function () {
          onUp();
          view.dom.removeEventListener("pointermove", onMove);
          view.dom.removeEventListener("pointerleave", onLeave);
          view.dom.removeEventListener("pointerdown", onDown, true);
          view.dom.removeEventListener("mousedown", onMouseDown, true);
        },
      };
    },
  });
}

const NoteColumns = Node.create({
  name: "noteColumns",
  group: "block",
  content: "noteColumn noteColumn",
  isolating: true,
  addAttributes: function () {
    return {
      split: {
        default: null,
        parseHTML: function (el) {
          return clampSplit(el.getAttribute("data-split"));
        },
        renderHTML: function (attrs) {
          if (!attrs.split) return {};
          return {
            "data-split": String(attrs.split),
            style: "--note-col-l: " + attrs.split + "fr; --note-col-r: " + (100 - attrs.split) + "fr",
          };
        },
      },
    };
  },
  parseHTML: function () {
    return [{ tag: "div.note-columns" }];
  },
  renderHTML: function (props) {
    return ["div", Object.assign({ class: "note-columns" }, props.HTMLAttributes), 0];
  },
  // Свой DOM: ширину во время перетаскивания меняем стилем, ProseMirror эти правки не трогает.
  addNodeView: function () {
    return function (props) {
      var node = props.node;
      var dom = document.createElement("div");
      dom.className = "note-columns";
      applyColumnsSplit(dom, node.attrs.split);
      return {
        dom: dom,
        contentDOM: dom,
        update: function (next) {
          if (next.type !== node.type) return false;
          node = next;
          applyColumnsSplit(dom, next.attrs.split);
          return true;
        },
        ignoreMutation: function (m) {
          return m.type === "attributes" && m.target === dom;
        },
      };
    };
  },
  addCommands: function () {
    return {
      // «+» / «H» → «Столбец»: выделенный текст (все блоки, которые задевает выделение) — в левый столбец,
      // без выделения — текущий абзац, как «[[».
      splitIntoColumns: function () {
        return function (props) {
          var state = props.state;
          var sel = state.selection;
          var $a = sel.$from;
          var $b = sel.$to;
          if ($a.depth < 1 || $b.depth < 1) return false;
          var first = $a.index(0);
          var last = $b.index(0);
          // Выделение кончается в самом начале следующего блока — его не берём.
          if (!sel.empty && last > first && $b.pos === $b.start(1)) last -= 1;
          for (var i = first; i <= last; i += 1) {
            if (state.doc.child(i).type.name === "noteColumns") return false; // в столбцах столбцы не делаем
          }
          if (props.dispatch) {
            var r = columnsSourceRange(state.doc, first, last);
            props.dispatch(wrapInColumns(props.tr, r.from, r.to).scrollIntoView());
          }
          return true;
        };
      },
    };
  },
  addKeyboardShortcuts: function () {
    return {
      // Backspace в самом начале правого столбца — снова обычный текст (левый столбец, затем правый).
      Backspace: function (props) {
        var state = props.editor.state;
        var sel = state.selection;
        if (!sel.empty) return false;
        var $from = sel.$from;
        for (var d = $from.depth; d > 0; d -= 1) {
          if ($from.node(d).type.name !== "noteColumn") continue;
          var cols = $from.node(d - 1);
          if (cols.type.name !== "noteColumns" || $from.index(d - 1) !== 1) return false;
          if ($from.pos !== $from.start(d) + 1 || $from.parentOffset !== 0) return false;
          var colsPos = $from.before(d - 1);
          var content = cols.child(0).content;
          var right = cols.child(1);
          var rightEmpty = right.childCount === 1 && !right.firstChild.content.size;
          if (!rightEmpty) content = content.append(right.content);
          var tr = state.tr.replaceWith(colsPos, colsPos + cols.nodeSize, content);
          var caret = colsPos + cols.child(0).content.size;
          tr.setSelection(TextSelection.near(tr.doc.resolve(Math.max(1, caret - 1)), -1));
          props.editor.view.dispatch(tr.scrollIntoView());
          return true;
        }
        return false;
      },
    };
  },
  addProseMirrorPlugins: function () {
    return [
      createColumnsResizePlugin(),
      new Plugin({
        key: new PluginKey("noteColumnsShortcut"),
        appendTransaction: function (trs, oldState, state) {
          if (!trs.some(function (tr) { return tr.docChanged; })) return null;
          if (trs.some(function (tr) { return tr.getMeta("paste") || tr.getMeta("uiEvent") === "paste" || tr.getMeta("history$") || isRemoteYTr(tr); })) return null;
          var sel = state.selection;
          if (!sel.empty) return null;
          var $from = sel.$from;
          if ($from.depth !== 1 || $from.parent.type.name !== "paragraph") return null;
          if ($from.parentOffset !== $from.parent.content.size) return null;
          if (!/\[\[$/.test($from.parent.textContent)) return null;
          // Только что набрали: раньше абзац на «[[» не заканчивался.
          var old$ = oldState.selection.$from;
          if (old$.parent.type.name === "paragraph" && /\[\[$/.test(old$.parent.textContent)) return null;
          var tr = state.tr.delete($from.pos - 2, $from.pos);
          var r = columnsSourceRange(tr.doc, $from.index(0));
          return wrapInColumns(tr, r.from, r.to);
        },
      }),
    ];
  },
});

/* Сдвиг абзацев («Сдвинуть», Tab / Shift+Tab): как вложенный пункт списка, до 8 уровней.
   В HTML — data-indent и margin-left (видно и в просмотре, и по ссылке). В списках Tab — вложенный
   пункт, в таблице — следующая ячейка (их обработчики), сюда не доходит. */
var INDENT_MAX = 8;
var INDENT_STEP_EM = 1.5;
var LIST_TYPES = ["bulletList", "orderedList", "taskList"];
var INDENT_TYPES = ["paragraph", "heading", "blockquote"].concat(LIST_TYPES);

// Абзацы / заголовки выделения (не внутри списка или таблицы): сдвинуть на delta. true — что-то сдвинули.
function changeIndent(state, dispatch, delta) {
  var sel = state.selection;
  var tr = state.tr;
  var changed = false;
  state.doc.nodesBetween(sel.from, sel.to, function (node, pos, parent) {
    // Цитата и список (верхнего уровня) сдвигаются целиком — с чертой / номерами (внутри не трогаем).
    if (INDENT_TYPES.indexOf(node.type.name) < 0) return true;
    var role = parent && parent.type.name;
    if (/^(listItem|taskItem|tableCell|tableHeader)$/.test(role || "")) return false;
    var cur = node.attrs.indent || 0;
    var next = Math.max(0, Math.min(INDENT_MAX, cur + delta));
    if (next !== cur) {
      tr.setNodeMarkup(pos, null, Object.assign({}, node.attrs, { indent: next || null }));
      changed = true;
    }
    return false;
  });
  if (changed && dispatch) dispatch(tr);
  return changed;
}

// Пункт списка у курсора: «listItem» / «taskItem» (null — не в списке).
function listItemTypeAt(state) {
  var $from = state.selection.$from;
  for (var d = $from.depth; d > 0; d -= 1) {
    var n = $from.node(d).type.name;
    if (n === "listItem" || n === "taskItem") return n;
  }
  return null;
}

// Список верхнего уровня вокруг курсора и глубина пункта: { node, depth, itemDepth } | null.
function topListAt(state) {
  var $from = state.selection.$from;
  var top = null;
  for (var d = 1; d <= $from.depth; d += 1) {
    if (LIST_TYPES.indexOf($from.node(d).type.name) >= 0) {
      top = { node: $from.node(d), depth: d, itemDepth: d + 1 };
      break;
    }
  }
  return top;
}

function inTable(state) {
  var $from = state.selection.$from;
  for (var d = $from.depth; d > 0; d -= 1) {
    if ($from.node(d).type.name === "table") return true;
  }
  return false;
}

function inListOrTable(state) {
  var $from = state.selection.$from;
  for (var d = $from.depth; d > 0; d -= 1) {
    if (/^(listItem|taskItem|table)$/.test($from.node(d).type.name)) return true;
  }
  return false;
}

const NoteIndent = Extension.create({
  name: "noteIndent",
  // Последним: Tab сначала у подсказок (хэштеги, «+», «/»), списков и таблиц.
  priority: 10,
  addGlobalAttributes: function () {
    return [
      {
        types: INDENT_TYPES,
        attributes: {
          indent: {
            default: null,
            parseHTML: function (el) {
              var n = parseInt(el.getAttribute("data-indent") || "", 10);
              return n > 0 ? Math.min(INDENT_MAX, n) : null;
            },
            renderHTML: function (attrs) {
              if (!attrs.indent) return {};
              return { "data-indent": String(attrs.indent), style: "margin-left: " + attrs.indent * INDENT_STEP_EM + "em" };
            },
          },
        },
      },
    ];
  },
  addCommands: function () {
    return {
      // В списке — сначала вложенный пункт; вложить некуда (первый пункт, весь список) — сдвигаем список.
      indentText: function () {
        return function (props) {
          var item = listItemTypeAt(props.state);
          // У чек-листа вложенности нет — сразу сдвиг списка.
          if (item && item !== "taskItem" && props.can().sinkListItem(item) && props.commands.sinkListItem(item)) return true;
          return changeIndent(props.state, props.dispatch, 1);
        };
      },
      outdentText: function () {
        return function (props) {
          return changeIndent(props.state, props.dispatch, -1);
        };
      },
    };
  },
  addKeyboardShortcuts: function () {
    return {
      Tab: function (props) {
        if (inTable(props.editor.state)) return false;
        // Сюда в списке доходит, только если пункт вложить некуда (обработчик списка не смог) — сдвигаем список.
        // Нечего сдвигать (дальше некуда) — всё равно держим Tab в заметке, а не уводим фокус.
        props.editor.commands.indentText();
        return true;
      },
      "Shift-Tab": function (props) {
        if (inListOrTable(props.editor.state)) return false;
        props.editor.commands.outdentText();
        return true;
      },
    };
  },
});

// Backspace в начале сдвинутой строки — сначала назад на уровень (раньше склейки со строкой выше).
const NoteIndentBackspace = Extension.create({
  name: "noteIndentBackspace",
  priority: 1000,
  addKeyboardShortcuts: function () {
    return {
      // Сдвинутый список, курсор в пункте верхнего уровня: Shift+Tab сначала сдвигает список назад
      // (иначе пункт превратился бы в обычный абзац).
      "Shift-Tab": function (props) {
        var state = props.editor.state;
        var top = topListAt(state);
        if (!top || !top.node.attrs.indent) return false;
        var $from = state.selection.$from;
        var $to = state.selection.$to;
        if ($from.depth < top.itemDepth || $to.depth < top.itemDepth) return false;
        // Курсор во вложенном пункте — пусть его поднимет обработчик списка.
        for (var d = top.itemDepth + 1; d <= $from.depth; d += 1) {
          if (LIST_TYPES.indexOf($from.node(d).type.name) >= 0) return false;
        }
        return props.editor.commands.outdentText();
      },
      Backspace: function (props) {
        var sel = props.editor.state.selection;
        if (!sel.empty || sel.$from.parentOffset !== 0) return false;
        var $from = sel.$from;
        var node = $from.parent;
        if (INDENT_TYPES.indexOf(node.type.name) >= 0 && node.attrs.indent) return props.editor.commands.outdentText();
        // В начале сдвинутой цитаты — назад сдвигается цитата.
        var d = $from.depth - 1;
        if (d > 0 && $from.node(d).type.name === "blockquote" && $from.node(d).attrs.indent && $from.index(d) === 0) {
          return props.editor.commands.outdentText();
        }
        return false;
      },
    };
  },
});

/* Сворачиваемый блок: заголовок со шевроном в конце строки (∨ свёрнуто / ⌃ развёрнуто), под ним —
   что угодно (текст, заголовки, списки). В HTML — <details class="note-toggle" open><summary>…</summary>
   <div class="note-toggle-body">…</div></details>: в просмотре и по ссылке сворачивается и без редактора. */
var TOGGLE_CHEVRON =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

const NoteToggleSummary = Node.create({
  name: "noteToggleSummary",
  content: "inline*",
  defining: true,
  parseHTML: function () {
    return [{ tag: "details.note-toggle > summary" }, { tag: "summary.note-toggle-summary" }];
  },
  renderHTML: function () {
    return ["summary", { class: "note-toggle-summary" }, 0];
  },
  addNodeView: function () {
    return function (props) {
      var node = props.node;
      var dom = document.createElement("div");
      dom.className = "note-toggle-summary";
      var text = document.createElement("span");
      text.className = "note-toggle-summary-text";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-toggle-chevron";
      btn.contentEditable = "false";
      btn.setAttribute("aria-label", "Свернуть / развернуть");
      btn.innerHTML = TOGGLE_CHEVRON;
      dom.appendChild(text);
      dom.appendChild(btn);
      dom.classList.toggle("is-empty", !node.content.size);
      btn.addEventListener("mousedown", function (e) {
        e.preventDefault();
      });
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var pos = typeof props.getPos === "function" ? props.getPos() : null;
        if (typeof pos !== "number") return;
        var view = props.editor.view;
        var togglePos = pos - 1;
        var t = view.state.doc.nodeAt(togglePos);
        if (!t || t.type.name !== "noteToggle") return;
        var tr = view.state.tr.setNodeMarkup(togglePos, null, Object.assign({}, t.attrs, { open: !t.attrs.open }));
        // Свернули, а курсор был внутри — ставим его в конец заголовка (иначе он «пропадает» в скрытом).
        var sel = view.state.selection;
        if (t.attrs.open && sel.from > pos + node.nodeSize && sel.from < togglePos + t.nodeSize) {
          tr.setSelection(TextSelection.create(tr.doc, pos + node.nodeSize - 1));
        }
        view.dispatch(tr.setMeta("leoNoScroll", true));
      });
      return {
        dom: dom,
        contentDOM: text,
        update: function (next) {
          if (next.type !== node.type) return false;
          node = next;
          dom.classList.toggle("is-empty", !next.content.size);
          return true;
        },
        stopEvent: function (e) {
          return !!(e.target && e.target.closest && e.target.closest(".note-toggle-chevron"));
        },
        ignoreMutation: function (m) {
          return m.target === dom || btn.contains(m.target);
        },
      };
    };
  },
});

const NoteToggleBody = Node.create({
  name: "noteToggleBody",
  content: "block+",
  defining: true,
  parseHTML: function () {
    return [{ tag: "div.note-toggle-body" }];
  },
  renderHTML: function () {
    return ["div", { class: "note-toggle-body" }, 0];
  },
});

// Блоки [first..last] верхнего уровня → сворачиваемый блок: текст первого — заголовок, остальное — внутрь.
function wrapInToggle(tr, from, to, caretInBody) {
  var schema = tr.doc.type.schema;
  var blocks = [];
  tr.doc.slice(from, to).content.forEach(function (n) {
    blocks.push(n);
  });
  var head = blocks.shift();
  var summary = schema.nodes.noteToggleSummary.create(null, head && head.isTextblock ? head.content : null);
  if (head && !head.isTextblock) blocks.unshift(head);
  if (!blocks.length) blocks.push(schema.nodes.paragraph.create());
  var body = schema.nodes.noteToggleBody.create(null, blocks);
  var toggle = schema.nodes.noteToggle.create({ open: true }, [summary, body]);
  tr.replaceWith(from, to, toggle);
  var caret = caretInBody ? from + 1 + summary.nodeSize + 2 : from + 1 + summary.nodeSize - 1;
  tr.setSelection(TextSelection.near(tr.doc.resolve(caret)));
  return tr;
}

// Снять сворачиваемый блок: заголовок — обычной строкой, содержимое — под ней.
function unwrapToggle(tr, togglePos, toggle) {
  var schema = tr.doc.type.schema;
  var summary = toggle.child(0);
  var body = toggle.child(1);
  var out = [schema.nodes.paragraph.create(null, summary.content)];
  var bodyEmpty = body.childCount === 1 && !body.firstChild.content.size;
  if (!bodyEmpty) body.forEach(function (n) {
    out.push(n);
  });
  tr.replaceWith(togglePos, togglePos + toggle.nodeSize, out);
  tr.setSelection(TextSelection.create(tr.doc, togglePos + 1));
  return tr;
}

function toggleAround($pos) {
  for (var d = $pos.depth; d > 0; d -= 1) {
    if ($pos.node(d).type.name === "noteToggle") return { node: $pos.node(d), pos: $pos.before(d), depth: d };
  }
  return null;
}

const NoteToggle = Node.create({
  name: "noteToggle",
  group: "block",
  content: "noteToggleSummary noteToggleBody",
  defining: true,
  addAttributes: function () {
    return {
      open: {
        default: true,
        parseHTML: function (el) {
          return el.hasAttribute("open");
        },
        renderHTML: function (attrs) {
          return attrs.open ? { open: "" } : {};
        },
      },
    };
  },
  parseHTML: function () {
    return [{ tag: "details.note-toggle" }];
  },
  renderHTML: function (props) {
    return ["details", Object.assign({ class: "note-toggle" }, props.HTMLAttributes), 0];
  },
  addNodeView: function () {
    return function (props) {
      var node = props.node;
      var dom = document.createElement("div");
      dom.className = "note-toggle";
      dom.classList.toggle("is-open", !!node.attrs.open);
      return {
        dom: dom,
        contentDOM: dom,
        update: function (next) {
          if (next.type !== node.type) return false;
          node = next;
          dom.classList.toggle("is-open", !!next.attrs.open);
          return true;
        },
        ignoreMutation: function (m) {
          return m.type === "attributes" && m.target === dom;
        },
      };
    };
  },
  addCommands: function () {
    return {
      // Меню списка → «Сворачиваемый блок»: строка (или выделенные строки) — в блок; уже в блоке — снять.
      toggleNoteToggle: function () {
        return function (props) {
          var state = props.state;
          var sel = state.selection;
          var t = toggleAround(sel.$from);
          if (t) {
            if (props.dispatch) unwrapToggle(props.tr, t.pos, t.node);
            return true;
          }
          var $a = sel.$from;
          var $b = sel.$to;
          if ($a.depth < 1) return false;
          var first = $a.index(0);
          var last = $b.index(0);
          if (!sel.empty && last > first && $b.pos === $b.start(1)) last -= 1;
          if (props.dispatch) {
            var from = 0;
            for (var k = 0; k < first; k += 1) from += state.doc.child(k).nodeSize;
            var to = from;
            for (var j = first; j <= last; j += 1) to += state.doc.child(j).nodeSize;
            wrapInToggle(props.tr, from, to, true);
          }
          return true;
        };
      },
    };
  },
  addKeyboardShortcuts: function () {
    return {
      // Enter в заголовке: развёрнут — внутрь блока; свёрнут — новая строка после блока.
      Enter: function (props) {
        var state = props.editor.state;
        var $from = state.selection.$from;
        if ($from.parent.type.name !== "noteToggleSummary") return false;
        var t = toggleAround($from);
        if (!t) return false;
        var tr = state.tr;
        if (t.node.attrs.open) {
          var bodyStart = t.pos + 1 + t.node.child(0).nodeSize + 1;
          tr.setSelection(TextSelection.near(tr.doc.resolve(bodyStart + 1)));
        } else {
          var after = t.pos + t.node.nodeSize;
          tr.insert(after, state.schema.nodes.paragraph.create());
          tr.setSelection(TextSelection.create(tr.doc, after + 1));
        }
        props.editor.view.dispatch(tr.scrollIntoView());
        return true;
      },
      // Backspace в начале заголовка — снова обычный текст.
      Backspace: function (props) {
        var state = props.editor.state;
        var sel = state.selection;
        if (!sel.empty || sel.$from.parent.type.name !== "noteToggleSummary" || sel.$from.parentOffset !== 0) return false;
        var t = toggleAround(sel.$from);
        if (!t) return false;
        props.editor.view.dispatch(unwrapToggle(state.tr, t.pos, t.node).scrollIntoView());
        return true;
      },
    };
  },
});

var PASTE_URL_RE = /^(?:https?:\/\/|www\.|t\.me\/|mailto:)[^\s<>"]+$/i;

function pasteUrlAsLink(view, event) {
  var sel = view.state.selection;
  // Тип — по JSON: в таблице выделение создаёт своя копия prosemirror-state, instanceof там не срабатывает.
  if (sel.empty || sel.toJSON().type !== "text" || !event || !event.clipboardData) return false;
  var url = String(event.clipboardData.getData("text/plain") || "").trim();
  if (!url || !PASTE_URL_RE.test(url)) return false;
  if (/^www\./i.test(url) || /^t\.me\//i.test(url)) url = "https://" + url;
  var editor = view.dom && view.dom.editor;
  if (!editor) return false;
  event.preventDefault();
  editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
  return true;
}

/* Вставка простого текста с markdown (из чата, ChatGPT, .md) — сразу оформлением. */
var MD_PASTE_HINT_RE = /(^|\n)(#{1,6}\s+\S|[-*•]\s+\S|\d+[.)]\s+\S|-\s+\[[ xX]\]\s|>\s+\S)|\*\*[^*\n]+\*\*/;
function pasteMarkdownText(view, event) {
  if (!event || !event.clipboardData) return false;
  if (event.clipboardData.getData("text/html")) return false;
  var text = String(event.clipboardData.getData("text/plain") || "").replace(/\r\n?/g, "\n");
  if (!text.trim() || !MD_PASTE_HINT_RE.test(text)) return false;
  if (!(window.NoteHtml && window.NoteHtml.markdownToHtml)) return false;
  var editor = view.dom && view.dom.editor;
  if (!editor || isInTable(view.state)) return false;
  var html = prepareHtmlForEditor(window.NoteHtml.markdownToHtml(text));
  if (!html) return false;
  event.preventDefault();
  editor.chain().focus().insertContent(html).run();
  return true;
}

const IosSelectionGuard = Extension.create({
  name: "iosSelectionGuard",
  addProseMirrorPlugins() {
    if (!isIOSDevice()) return [];

    var lastTouchPos = null;
    var lastTouchTime = 0;

    return [
      new Plugin({
        key: new PluginKey("iosSelectionGuard"),
        props: {
          handleDOMEvents: {
            touchstart: function (view, event) {
              if (!event.touches || !event.touches[0]) return false;
              var coords = view.posAtCoords({
                left: event.touches[0].clientX,
                top: event.touches[0].clientY,
              });
              if (coords && coords.pos >= 0) {
                lastTouchPos = coords.pos;
                lastTouchTime = Date.now();
              }
              return false;
            },
          },
        },
        appendTransaction: function (transactions, oldState, newState) {
          if (!transactions.some(function (tr) {
            return tr.selectionSet;
          })) {
            return null;
          }
          if (transactions.some(isRemoteYTr)) return null;

          var oldSel = oldState.selection;
          var newSel = newState.selection;
          if (newSel.empty || !oldSel.empty) return null;
          if (Date.now() - lastTouchTime > 4000) return null;
          if (lastTouchPos == null || lastTouchPos < 3) return null;
          if (newSel.from > 2) return null;
          // Баг iOS — выделение ровно от начала заметки до места касания. Длинное первое слово
          // (долгое нажатие) и «Выбрать все» сюда не попадают: там нет пробела до касания
          // или выделение идёт дальше него.
          if (newSel.to < lastTouchPos - 2 || newSel.to > lastTouchPos + 30) return null;
          if (newSel.to - newSel.from < 8) return null;
          if (!/\s/.test(newState.doc.textBetween(newSel.from, newSel.to, "\n", " "))) return null;

          return newState.tr.setSelection(TextSelection.create(newState.doc, lastTouchPos));
        },
      }),
    ];
  },
});

function clipboardTableGrid(html) {
  var s = String(html || "");
  if (!/<(table|t[rdh])[\s>]/i.test(s)) return null;
  var doc = new DOMParser().parseFromString(s, "text/html");
  var cells = doc.querySelectorAll("td, th");
  if (!cells.length) return null;
  var nonempty = 0;
  for (var i = 0; i < cells.length; i++) {
    if (String(cells[i].textContent || "").trim()) nonempty += 1;
  }
  var rows = doc.querySelectorAll("tr");
  var width = 0;
  if (rows.length) {
    for (var r = 0; r < rows.length; r++) {
      var n = rows[r].querySelectorAll("td, th").length;
      if (n > width) width = n;
    }
  } else {
    width = cells.length;
  }
  return {
    width: width,
    height: rows.length || 1,
    cells: cells.length,
    nonempty: nonempty,
  };
}

function htmlLooksLikeMultiCellTable(html) {
  var grid = clipboardTableGrid(html);
  if (!grid) return false;
  return grid.nonempty > 1;
}

function unwrapSingleCellTableHtml(html) {
  var s = String(html || "");
  if (!/<(table|t[rdh])[\s>]/i.test(s)) return s;
  var doc = new DOMParser().parseFromString("<div>" + s + "</div>", "text/html");
  var cells = doc.querySelectorAll("td, th");
  var nonempty = [];
  for (var i = 0; i < cells.length; i++) {
    if (String(cells[i].textContent || "").trim()) nonempty.push(cells[i]);
  }
  if (nonempty.length === 1) return nonempty[0].innerHTML.trim();
  if (cells.length === 1) return cells[0].innerHTML.trim();
  return s;
}

function sliceForRichClipboard(state) {
  var sel = state.selection;
  if (sel instanceof CellSelection) {
    if (sel.$anchorCell.pos === sel.$headCell.pos) {
      var cell = sel.$anchorCell.nodeAfter;
      if (cell) return new Slice(cell.content, 0, 0);
    }
    return sel.content();
  }
  if (isInTable(state)) {
    return state.doc.slice(sel.from, sel.to, false);
  }
  return sel.content();
}

function cellRangeFromResolved($cell) {
  var cell = $cell.nodeAfter;
  if (!cell) return null;
  var from = $cell.pos + 1;
  var to = $cell.pos + cell.nodeSize - 1;
  return { from: from, to: to };
}

function textRangeInsideCell($cell) {
  var cell = $cell.nodeAfter;
  if (!cell) return null;
  var innerFrom = $cell.pos + 1;
  var innerTo = $cell.pos + cell.nodeSize - 1;
  var from = innerFrom;
  var to = innerTo;
  if (cell.firstChild && cell.firstChild.isTextblock) from = innerFrom + 1;
  if (cell.lastChild && cell.lastChild.isTextblock) to = innerTo - 1;
  if (from > to) return { from: innerFrom, to: innerTo };
  return { from: from, to: to };
}

function selectAllTextInTableCell(view, pos) {
  var doc = view.state.doc;
  var $cell = cellAround(doc.resolve(pos));
  if (!$cell) return false;
  var range = textRangeInsideCell($cell);
  if (!range) return false;
  try {
    view.dispatch(
      view.state.tr.setSelection(TextSelection.create(doc, range.from, range.to)).scrollIntoView()
    );
  } catch (_) {
    return false;
  }
  return true;
}

function clipboardPlainText(event) {
  if (!event || !event.clipboardData) return "";
  var text = event.clipboardData.getData("text/plain") || "";
  if (!text) {
    var html = event.clipboardData.getData("text/html") || "";
    if (html) {
      var tmp = document.createElement("div");
      tmp.innerHTML = html;
      text = tmp.textContent || tmp.innerText || "";
    }
  }
  return String(text).replace(/\r\n?/g, "\n").replace(/\n+/g, " ");
}

function rememberTableClickPos(view, clientX, clientY, slot) {
  var coords = view.posAtCoords({ left: clientX, top: clientY });
  slot.pos = coords && coords.pos >= 0 ? coords.pos : null;
}

function pastePlainTextInTableCell(view, event, clickPos) {
  var state = view.state;
  if (!isInTable(state)) return false;
  var html = event.clipboardData ? event.clipboardData.getData("text/html") : "";
  if (htmlLooksLikeMultiCellTable(html)) return false;

  var sel = state.selection;
  if (sel instanceof CellSelection && sel.$anchorCell.pos !== sel.$headCell.pos) {
    return false;
  }

  var text = clipboardPlainText(event);
  if (!text) return false;

  var insertFrom;
  var insertTo;
  if (sel instanceof CellSelection) {
    var range = cellRangeFromResolved(sel.$headCell);
    if (!range) return false;
    insertFrom = clickPos;
    if (insertFrom == null || insertFrom < range.from || insertFrom > range.to) {
      insertFrom = range.to;
    }
    insertTo = insertFrom;
  } else {
    insertFrom = sel.from;
    insertTo = sel.to;
  }

  try {
    view.dispatch(state.tr.insertText(text, insertFrom, insertTo).scrollIntoView());
  } catch (_) {
    return false;
  }
  return true;
}

const BotTaskItem = TaskItem.extend({
  parseHTML() {
    return [
      {
        tag: "li[data-type='taskItem']",
        priority: 64,
        getAttrs: (el) => ({
          checked:
            el.getAttribute("data-checked") === "true" || el.hasAttribute("checked"),
        }),
      },
      {
        tag: "ul.note-task-list > li",
        priority: 63,
        getAttrs: (el) => ({
          checked: el.hasAttribute("checked"),
        }),
      },
      {
        tag: "li[data-checked]",
        getAttrs: (el) => ({
          checked: el.getAttribute("data-checked") === "true",
        }),
      },
      {
        tag: "li[checked]",
        getAttrs: () => ({ checked: true }),
      },
    ];
  },
});

const BotTaskList = TaskList.extend({
  renderHTML({ HTMLAttributes }) {
    const attrs = { ...HTMLAttributes, class: "note-task-list" };
    delete attrs["data-type"];
    return ["ul", attrs, 0];
  },
  parseHTML() {
    return [
      { tag: "ul[data-type='taskList']", priority: 61 },
      {
        tag: "ul.note-task-list",
        priority: 60,
      },
      {
        tag: "ul",
        priority: 50,
        getAttrs: (el) => {
          const items = el.querySelectorAll(":scope > li");
          if (!items.length) return false;
          for (const li of items) {
            if (
              li.hasAttribute("checked") ||
              li.getAttribute("data-type") === "taskItem" ||
              li.classList.contains("note-task") ||
              li.querySelector('input[type="checkbox"]')
            ) {
              return {};
            }
          }
          return false;
        },
      },
    ];
  },
});

function stripEditorArtifacts(html, opts) {
  opts = opts || {};
  let s = String(html || "");
  if (!opts.keepTaskTypes) {
    s = s.replace(/\sdata-type="[^"]*"/gi, "");
    s = s.replace(/\sdata-checked="[^"]*"/gi, "");
  }
  // TipTap NodeView: <label><input …><span></span></label>
  s = s.replace(/<label[^>]*>[\s\S]*?<\/label>/gi, "");
  // enrichForDisplay: unwrap <span class="note-task-text">…</span>
  s = s.replace(/<input[^>]*type="checkbox"[^>]*>/gi, "");
  s = s.replace(/<span class="note-task-text">([\s\S]*?)<\/span>/gi, "$1");
  s = s.replace(/\sclass="note-task(?:\s+note-task--checked)?"/gi, "");
  if (!opts.keepTaskListClass) {
    s = s.replace(/\sclass="note-task-list"/gi, "");
  }
  return s;
}

function escapeHtmlText(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function extractTaskText(li) {
  if (!li) return "";
  li.querySelectorAll("label, input[type='checkbox']").forEach(function (el) {
    el.remove();
  });
  const div = li.querySelector(":scope > div");
  if (div) {
    const p = div.querySelector("p");
    return (p ? p.textContent : div.textContent).trim();
  }
  const ps = li.querySelectorAll(":scope > p");
  if (ps.length) {
    return Array.from(ps)
      .map(function (p) {
        return p.textContent.trim();
      })
      .filter(Boolean)
      .join(" ");
  }
  return li.textContent.trim();
}

// Inline HTML of a checklist item's text (task chips, bold, links), or null when
// the item has no single text paragraph. Call after extractTaskText stripped labels.
function extractTaskInnerHtml(li) {
  if (!li) return null;
  const div = li.querySelector(":scope > div");
  const p = div ? div.querySelector(":scope > p") : null;
  if (p) return p.innerHTML.trim();
  const ps = li.querySelectorAll(":scope > p");
  if (ps.length === 1) return ps[0].innerHTML.trim();
  return null;
}

function setTaskItemContent(li, text, checked) {
  li.classList.remove("note-task", "note-task--checked");
  li.removeAttribute("checked");
  li.innerHTML = "<p>" + escapeHtmlText(text) + "</p>";
  li.setAttribute("data-type", "taskItem");
  li.setAttribute("data-checked", checked ? "true" : "false");
}

function repairOrphanTaskText(root) {
  root.querySelectorAll("ul.note-task-list, ul[data-type='taskList']").forEach(function (taskUl) {
    taskUl.classList.add("note-task-list");
    taskUl.setAttribute("data-type", "taskList");

    taskUl.querySelectorAll(":scope > li").forEach(function (li) {
      // Чип задачи и значок Фокуса («неактуально», чип поставленной задачи) — не текст:
      // пересборка строки текстом превращала значок в слова и теряла чип.
      if (li.querySelector(".note-task-chip, [data-leo-task-id], .note-focus-mark")) return;
      const text = extractTaskText(li);
      const checked =
        li.hasAttribute("checked") || li.getAttribute("data-checked") === "true";
      if (text) {
        setTaskItemContent(li, text, checked);
        return;
      }

      let next = taskUl.nextElementSibling;
      while (next) {
        if (
          next.tagName === "UL" &&
          !next.classList.contains("note-task-list") &&
          next.getAttribute("data-type") !== "taskList"
        ) {
          const merged = [];
          next.querySelectorAll(":scope > li").forEach(function (bli) {
            const t = extractTaskText(bli);
            if (t) merged.push(t);
          });
          if (merged.length) {
            setTaskItemContent(li, merged.join(" "), checked);
            next.remove();
          }
          break;
        }
        break;
      }
    });
  });
}

const PLAIN_URL_RE = /(https?:\/\/[^\s<>"]+|www\.[^\s<>"]+)/gi;

function safeLinkHref(raw) {
  let s = String(raw || "").trim().replace(/[),.;:!?'"»]+$/g, "");
  if (/^www\./i.test(s)) s = "https://" + s;
  try {
    const u = new URL(s);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : "";
  } catch (_) {
    return "";
  }
}

// Адреса, сохранённые простым текстом (например, ботом), превращаем в ссылки.
function linkifyPlainUrls(root) {
  const doc = root.ownerDocument;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: function (node) {
      const p = node.parentElement;
      if (!p || p.closest("a, code, pre, [data-leo-task-id], [contenteditable='false']")) {
        return NodeFilter.FILTER_REJECT;
      }
      PLAIN_URL_RE.lastIndex = 0;
      return PLAIN_URL_RE.test(node.nodeValue || "") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(function (node) {
    const text = node.nodeValue || "";
    const frag = doc.createDocumentFragment();
    let last = 0;
    text.replace(PLAIN_URL_RE, function (match, _g, offset) {
      const href = safeLinkHref(match);
      if (!href) return match;
      const tail = (match.match(/[),.;:!?'"»]+$/) || [""])[0];
      const shown = tail ? match.slice(0, -tail.length) : match;
      if (offset > last) frag.appendChild(doc.createTextNode(text.slice(last, offset)));
      const a = doc.createElement("a");
      a.setAttribute("href", href);
      a.textContent = shown;
      frag.appendChild(a);
      last = offset + shown.length;
      return match;
    });
    if (last === 0) return;
    if (last < text.length) frag.appendChild(doc.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
  });
}

function prepareHtmlForEditor(raw) {
  let html = stripEditorArtifacts(raw, { keepTaskListClass: true, keepTaskTypes: true });
  const doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
  const root = doc.body.firstChild;
  if (root) {
    repairOrphanTaskText(root);
    linkifyPlainUrls(root);
    html = root.innerHTML;
  }
  return html.trim();
}

function markdownTasksToHtml(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  let taskBuf = [];
  function flushTasks() {
    if (!taskBuf.length) return;
    out.push('<ul class="note-task-list">');
    taskBuf.forEach(function (line) {
      const m = line.trim().match(/^-\s+\[([ xX])\]\s+(.*)$/);
      if (!m) return;
      const checked = String(m[1]).toLowerCase() === "x";
      const body = m[2] || "";
      if (checked) out.push("<li checked><p>" + body + "</p></li>");
      else out.push("<li><p>" + body + "</p></li>");
    });
    out.push("</ul>");
    taskBuf = [];
  }
  lines.forEach(function (line) {
    if (/^-\s+\[[ xX]\]\s/.test(String(line || "").trim())) {
      taskBuf.push(line);
      return;
    }
    flushTasks();
    out.push(line);
  });
  flushTasks();
  return out.join("\n");
}

function normalizeCollapsedMarkdown(raw) {
  let s = String(raw || "").trim();
  if (!s) return s;
  if (
    window.NoteHtml &&
    typeof window.NoteHtml.normalizeCollapsedMarkdown === "function"
  ) {
    return window.NoteHtml.normalizeCollapsedMarkdown(s);
  }
  s = s.replace(/\s+(#{1,6}\s+)/g, "\n\n$1");
  s = s.replace(/(#{1,6}\s+[^\n#]+?)\s+(-\s+\[[ xX]\]\s+)/g, "$1\n$2");
  s = s.replace(/(#{1,6}\s+[^\n#]+?)\s+(-\s+(?!\[[ xX]\]))/g, "$1\n$2");
  return s;
}

function importBody(body) {
  const raw = normalizeCollapsedMarkdown(body);
  if (!raw) return "";
  const hasHtml = /<[a-z][\s\S]*>/i.test(raw);
  if (hasHtml) {
    const splitIdx = raw.split("\n").findIndex((l) => /^-\s+\[[ xX]\]\s/.test(l.trim()));
    if (splitIdx >= 0) {
      const htmlPart = raw.split("\n").slice(0, splitIdx).join("\n").trim();
      const taskPart = raw.split("\n").slice(splitIdx).join("\n").trim();
      if (htmlPart && !/<[a-z][\s\S]*>/i.test(taskPart)) {
        return (
          prepareHtmlForEditor(htmlPart) + prepareHtmlForEditor(markdownTasksToHtml(taskPart))
        );
      }
    }
    return prepareHtmlForEditor(raw);
  }
  if (/(^|\n)-\s+\[[ xX]\]\s/.test(raw)) {
    if (/(^|\n)#{1,6}\s|\*\*[^*]+\*\*|(^|\n)[-*•]\s/.test(raw)) {
      return window.NoteHtml && window.NoteHtml.markdownToHtml
        ? prepareHtmlForEditor(window.NoteHtml.markdownToHtml(raw))
        : prepareHtmlForEditor(markdownTasksToHtml(raw));
    }
    return prepareHtmlForEditor(markdownTasksToHtml(raw));
  }
  if (/(^|\n)#{1,6}\s|\*\*[^*]+\*\*|(^|\n)[-*•]\s|~~[^~]+~~/.test(raw)) {
    return window.NoteHtml && window.NoteHtml.markdownToHtml
      ? prepareHtmlForEditor(window.NoteHtml.markdownToHtml(raw))
      : "<p>" + raw.replace(/\n/g, "<br>") + "</p>";
  }
  return "<p>" + raw.replace(/\n/g, "<br>") + "</p>";
}

function normalizeExportedTaskHtml(html) {
  const doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
  const root = doc.body.firstChild;
  if (!root) return html;
  root.querySelectorAll("ul").forEach(function (ul) {
    const items = ul.querySelectorAll(":scope > li");
    if (!items.length) return;
    let isTask =
      ul.classList.contains("note-task-list") ||
      ul.getAttribute("data-type") === "taskList";
    if (!isTask) {
      for (const li of items) {
        if (
          li.hasAttribute("checked") ||
          li.getAttribute("data-type") === "taskItem" ||
          li.getAttribute("data-checked") === "true" ||
          li.querySelector('input[type="checkbox"]')
        ) {
          isTask = true;
          break;
        }
      }
    }
    if (!isTask) return;
    ul.classList.add("note-task-list");
    ul.removeAttribute("data-type");
    items.forEach(function (li) {
      const checked =
        li.hasAttribute("checked") || li.getAttribute("data-checked") === "true";
      const text = extractTaskText(li);
      // Keep inline markup: flattening to text dropped task chips («/» tasks) and links.
      const inner = extractTaskInnerHtml(li);
      li.removeAttribute("data-type");
      li.removeAttribute("data-checked");
      if (checked) li.setAttribute("checked", "");
      else li.removeAttribute("checked");
      li.innerHTML = "<p>" + (inner != null ? inner : escapeHtmlText(text)) + "</p>";
    });
  });
  return root.innerHTML;
}

function finalizeExportedHtml(html) {
  let out = normalizeExportedTaskHtml(html);
  out = stripEditorArtifacts(out, { keepTaskListClass: true });
  out = out.replace(/<p><br><\/p>/gi, "");
  return out.trim();
}

function unwrapLiBlockWrappers(li) {
  li.querySelectorAll(":scope > p, :scope > div").forEach(function (block) {
    const parent = block.parentNode;
    if (!parent) return;
    while (block.firstChild) parent.insertBefore(block.firstChild, block);
    block.remove();
  });
}

function flattenListsForClipboard(root) {
  root.querySelectorAll("ol, ul").forEach(function (list) {
    list.removeAttribute("data-type");
    if (!list.classList.contains("note-task-list")) {
      list.className = "";
    }
    list.querySelectorAll(":scope > li").forEach(function (li) {
      li.removeAttribute("data-type");
      li.removeAttribute("data-checked");
      unwrapLiBlockWrappers(li);
    });
  });
}

function prepareHtmlForClipboard(html) {
  let s = finalizeExportedHtml(html);
  const doc = new DOMParser().parseFromString("<div>" + s + "</div>", "text/html");
  const root = doc.body.firstChild;
  if (!root) return s;
  root.querySelectorAll("ul.note-task-list, ul[data-type='taskList']").forEach(function (ul) {
    ul.removeAttribute("data-type");
    ul.className = "";
    ul.querySelectorAll(":scope > li").forEach(function (li) {
      const checked = li.hasAttribute("checked") || li.getAttribute("data-checked") === "true";
      const text = extractTaskText(li);
      li.removeAttribute("checked");
      li.removeAttribute("data-checked");
      li.removeAttribute("data-type");
      li.innerHTML = escapeHtmlText((checked ? "☑ " : "☐ ") + text);
    });
  });
  flattenListsForClipboard(root);
  root.querySelectorAll("a[href]").forEach(function (a) {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });
  return root.innerHTML.trim();
}

function selectionPlainText(state, from, to) {
  return state.doc.textBetween(from, to, "\n\n", "\n");
}

function wrapClipboardHtml(html) {
  return (
    "<html><body><!--StartFragment-->" + html + "<!--EndFragment--></body></html>"
  );
}

function fillRichClipboard(view, event) {
  if (!event.clipboardData) return false;
  const { state } = view;
  const { from, to, empty } = state.selection;
  if (empty) return false;

  const slice = sliceForRichClipboard(state);
  const serializer = DOMSerializer.fromSchema(state.schema);
  const wrap = document.createElement("div");
  wrap.appendChild(serializer.serializeFragment(slice.content));
  const html = unwrapSingleCellTableHtml(prepareHtmlForClipboard(wrap.innerHTML));
  const plain = selectionPlainText(state, from, to);

  event.clipboardData.setData("text/plain", plain);
  event.clipboardData.setData("text/html", wrapClipboardHtml(html));
  event.preventDefault();
  return true;
}

function canonicalBody(body) {
  return finalizeExportedHtml(importBody(body));
}

function exportBody(editor) {
  return finalizeExportedHtml(editor.getHTML());
}

function normalizeLinkUrl(url) {
  const s = String(url || "").trim();
  if (!s) return "";
  if (/^(https?:\/\/|mailto:|tel:)/i.test(s)) return s;
  return "https://" + s;
}

var activeLinkPopover = null;

function closeLinkPopover() {
  if (!activeLinkPopover) return;
  if (activeLinkPopover.outsideHandler) {
    document.removeEventListener("mousedown", activeLinkPopover.outsideHandler, true);
    document.removeEventListener("touchstart", activeLinkPopover.outsideHandler, true);
  }
  if (activeLinkPopover.el && activeLinkPopover.el.parentNode) {
    activeLinkPopover.el.parentNode.removeChild(activeLinkPopover.el);
  }
  activeLinkPopover = null;
}

function showLinkPopover(toolbarEl, editor, refresh) {
  closeLinkPopover();
  const selection = editor.state.selection;
  const savedFrom = selection.from;
  const savedTo = selection.to;
  const prev = editor.getAttributes("link").href || "https://";
  const host =
    (toolbarEl && toolbarEl.closest && toolbarEl.closest(".note-editor-toolbar-wrap")) ||
    (toolbarEl && toolbarEl.parentElement) ||
    document.body;

  const pop = document.createElement("div");
  pop.className = "note-link-popover note-link-popover--bottom";
  pop.innerHTML =
    '<p class="note-link-popover-hint">Укажите адрес ссылки</p>' +
    '<input type="url" class="note-link-popover-input" inputmode="url" autocapitalize="off" ' +
    'autocomplete="off" spellcheck="false" placeholder="https://example.com" aria-label="URL ссылки" />' +
    '<div class="note-link-popover-actions">' +
    '<button type="button" class="note-link-popover-btn note-link-popover-btn--apply">Применить</button>' +
    '<button type="button" class="note-link-popover-btn note-link-popover-btn--remove">Убрать ссылку</button>' +
    "</div>";

  host.appendChild(pop);
  const input = pop.querySelector(".note-link-popover-input");
  const applyBtn = pop.querySelector(".note-link-popover-btn--apply");
  const removeBtn = pop.querySelector(".note-link-popover-btn--remove");
  if (input) input.value = prev === "https://" ? "" : prev;

  function applyLink() {
    const url = normalizeLinkUrl(input ? input.value : "");
    if (!url) {
      if (input) input.focus();
      return;
    }
    editor
      .chain()
      .focus()
      .setTextSelection({ from: savedFrom, to: savedTo })
      .setLink({ href: url })
      .run();
    closeLinkPopover();
    if (typeof refresh === "function") refresh();
  }

  function removeLink() {
    editor
      .chain()
      .focus()
      .setTextSelection({ from: savedFrom, to: savedTo })
      .unsetLink()
      .run();
    closeLinkPopover();
    if (typeof refresh === "function") refresh();
  }

  if (applyBtn) applyBtn.addEventListener("click", applyLink);
  if (removeBtn) removeBtn.addEventListener("click", removeLink);
  if (input) {
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        applyLink();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeLinkPopover();
      }
    });
  }

  function outsideHandler(e) {
    if (pop.contains(e.target)) return;
    closeLinkPopover();
  }
  document.addEventListener("mousedown", outsideHandler, true);
  document.addEventListener("touchstart", outsideHandler, true);
  activeLinkPopover = { el: pop, outsideHandler: outsideHandler };

  window.setTimeout(function () {
    if (input) {
      input.focus();
      input.select();
    }
  }, 0);
}

function svgIcon(name) {
  var common =
    'width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
  var paths = {
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H13"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H11"/>',
    highlight: '<path d="m9 15 6.5-6.5a2.1 2.1 0 0 0-3-3L6 12l-1 4z"/><path d="M4 20h8" stroke="#facc15" stroke-width="2.6"/>',
    font: '<path d="M3 19 8.5 5h1L15 19"/><path d="M5.2 14h7.6"/><path d="M15.5 19v-5.2a2.6 2.6 0 0 1 5.2 0V19"/><path d="M15.5 16.2h5.2"/>',
    divider: '<path d="M4 12h16"/><path d="M8 7h8M8 17h8" opacity=".35"/>',
    noteref: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M12 10v6"/>',
    chevronRight: '<path d="m9 6 6 6-6 6"/>',
    chevronLeft: '<path d="m15 6-6 6 6 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="4" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="4" cy="18" r="1" fill="currentColor" stroke="none"/>',
    table:
      '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    paperclip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    bold: '<path d="M6 4h8a4 4 0 0 1 0 8H6z"/><path d="M6 12h9a4 4 0 0 1 0 8H6z"/>',
    italic: '<line x1="19" y1="4" x2="10" y2="4"/><line x1="14" y1="20" x2="5" y2="20"/><line x1="15" y1="4" x2="9" y2="20"/>',
    strike: '<path d="M16 4H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H6"/><line x1="4" y1="12" x2="20" y2="12"/>',
    underline: '<path d="M6 4v6a6 6 0 0 0 12 0V4"/><line x1="4" y1="20" x2="20" y2="20"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    quote: '<path d="M3 21c3 0 7-1 7-8V5"/><path d="M14 21c3 0 7-1 7-8V5"/>',
    heading: '<path d="M6 4v16M18 4v16M8 4h-4M20 4h-4M8 12h8"/>',
    text: '<path d="M4 7V4h16v3"/><path d="M12 4v16"/><path d="M8 20h8"/>',
    check: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    numbered:
      '<path d="M10 6h11M10 12h11M10 18h11"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
    checklist:
      '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6l1 1 2-2"/><path d="M4 12l1 1 2-2"/><path d="M4 18l1 1 2-2"/>',
    none: '<path d="M4 7h16M4 12h16M4 17h10"/>',
    blank: "",
    toggle: '<path d="m4 8 4 4-4 4"/><path d="M12 7h9M12 12h9M12 17h6"/>',
    indent: '<path d="M10 6h10M10 12h10M10 18h10"/><path d="m3 9 3 3-3 3"/>',
    outdent: '<path d="M10 6h10M10 12h10M10 18h10"/><path d="m6 9-3 3 3 3"/>',
    columns: '<path d="M4 7h10M4 12h10M4 17h7"/><path d="M19 4v16"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    discuss: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    fragment: '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>',
  };
  return "<svg " + common + ">" + (paths[name] || "") + "</svg>";
}


// Backspace в начале пункта списка/задачи: сначала убираем маркер и отступ (строка остаётся на месте),
// а не приклеиваем её к предыдущему пункту вторым абзацем — иначе текст «застревает» с отступом.
const ListBackspace = Extension.create({
  name: "leoListBackspace",
  priority: 1000,
  addKeyboardShortcuts() {
    return {
      Backspace: ({ editor }) => {
        const sel = editor.state.selection;
        if (!sel.empty) return false;
        const $from = sel.$from;
        if ($from.parentOffset !== 0 || $from.depth < 2) return false;
        if (!$from.parent.isTextblock) return false;
        const item = $from.node(-1);
        const name = item && item.type && item.type.name;
        if (name !== "listItem" && name !== "taskItem") return false;
        if ($from.index(-1) === 0) {
          // Пункт из середины нумерованного списка: lift режет список надвое, и нижняя часть
          // начиналась заново с 1. Продолжаем нумерацию — как будто пункт просто убрали.
          const list = $from.node(-2);
          const itemIdx = $from.index(-2);
          const nextStart =
            list && list.type.name === "orderedList" && itemIdx < list.childCount - 1
              ? (Number(list.attrs.start) || 1) + itemIdx
              : 0;
          if (!editor.commands.liftListItem(name)) return false;
          if (nextStart > 1) continueOrderedListAfterCursor(editor, nextStart);
          return true;
        }
        return editor.commands.lift(name);
      },
    };
  },
});

function continueOrderedListAfterCursor(editor, start) {
  const state = editor.state;
  const $pos = state.selection.$from;
  for (let d = $pos.depth; d > 0; d--) {
    const parent = $pos.node(d - 1);
    const idx = $pos.index(d - 1);
    if (idx + 1 >= parent.childCount) continue;
    const next = parent.child(idx + 1);
    if (next.type.name !== "orderedList") return;
    if ((Number(next.attrs.start) || 1) !== 1) return;
    const nextPos = $pos.after(d);
    editor.view.dispatch(
      state.tr.setNodeMarkup(nextPos, undefined, Object.assign({}, next.attrs, { start: start }))
    );
    return;
  }
}

// Второй Backspace: строка сразу после списка склеивается с концом последнего пункта
// (а не превращается обратно в пункт списка).
const ListJoinBackspace = Extension.create({
  name: "leoListJoinBackspace",
  priority: 999,
  addKeyboardShortcuts() {
    return {
      Backspace: ({ editor }) => {
        const state = editor.state;
        const sel = state.selection;
        if (!sel.empty) return false;
        const $from = sel.$from;
        if ($from.depth !== 1 || $from.parentOffset !== 0 || !$from.parent.isTextblock) return false;
        if ($from.parent.type.name !== "paragraph") return false;
        const idx = $from.index(0);
        if (idx === 0) return false;
        const prev = state.doc.child(idx - 1);
        const listNames = ["bulletList", "orderedList", "taskList"];
        if (listNames.indexOf(prev.type.name) < 0) return false;
        const paraStart = $from.before(1);
        const prevStart = paraStart - prev.nodeSize;
        let lastPos = -1;
        let lastNode = null;
        prev.descendants(function (node, pos) {
          if (node.isTextblock) {
            lastPos = prevStart + 1 + pos;
            lastNode = node;
          }
        });
        if (lastPos < 0 || !lastNode) return false;
        const end = lastPos + 1 + lastNode.content.size;
        const para = $from.parent;
        const tr = state.tr.delete(paraStart, paraStart + para.nodeSize);
        if (para.content.size) tr.insert(end, para.content);
        // Строка стояла между двумя одинаковыми списками (остаток от удалённого пункта) —
        // склеиваем их обратно, иначе нумерация ниже так и останется отдельной (1, 2, 3…).
        const joinPos = tr.mapping.map(paraStart);
        const $join = tr.doc.resolve(joinPos);
        if (
          $join.nodeBefore &&
          $join.nodeAfter &&
          $join.nodeBefore.type === $join.nodeAfter.type &&
          canJoin(tr.doc, joinPos)
        ) {
          tr.join(joinPos);
        }
        tr.setSelection(state.selection.constructor.near(tr.doc.resolve(end)));
        editor.view.dispatch(tr.scrollIntoView());
        return true;
      },
    };
  },
});

/* Шрифт и кегль выделенного текста: <span style="font-family / font-size"> (марк textStyle).
   При чтении HTML берём только свои варианты — вставка из Google Docs / Word не тащит их Arial 11pt. */
var NOTE_FONTS = [
  { id: "", label: "Как в приложении", css: "" },
  { id: "arial", label: "Arial", css: "Arial, Helvetica, sans-serif" },
  { id: "georgia", label: "Georgia", css: "Georgia, serif" },
  { id: "times", label: "Times New Roman", css: '"Times New Roman", Times, serif' },
  { id: "mono", label: "Моноширинный", css: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace' },
];
var NOTE_FONT_SIZES = ["12px", "14px", "16px", "18px", "20px", "24px", "30px"];
// Заголовки при смене кегля всей заметки — пропорционально (как h1 1.5em / h2 1.3em / h3 1.15em в CSS).
var NOTE_HEADING_SCALE = { 1: 1.5, 2: 1.3, 3: 1.15, 4: 1, 5: 1, 6: 1 };

// Кегль из меню — всегда; посчитанный для заголовка (data-note-fs) — любой 10–60px.
function noteFontSizeFrom(el) {
  var v = String((el && el.style && el.style.fontSize) || "").trim();
  if (NOTE_FONT_SIZES.indexOf(v) >= 0) return v;
  var m = /^(\d{2})px$/.exec(v);
  if (m && el.hasAttribute && el.hasAttribute("data-note-fs") && +m[1] >= 10 && +m[1] <= 60) return v;
  return null;
}

function noteFontByCss(css) {
  var norm = String(css || "").replace(/["'\s]/g, "").toLowerCase();
  if (!norm) return null;
  for (var i = 1; i < NOTE_FONTS.length; i++) {
    if (NOTE_FONTS[i].css.replace(/["'\s]/g, "").toLowerCase() === norm) return NOTE_FONTS[i];
  }
  return null;
}

// Свой textStyle: <span> без нашего шрифта/кегля не превращается в марк (иначе после вставки — голые <span>).
var NoteTextStyle = TextStyle.extend({
  parseHTML: function () {
    return [
      {
        tag: "span",
        getAttrs: function (el) {
          return noteFontByCss(el.style.fontFamily) || noteFontSizeFrom(el) ? {} : false;
        },
      },
    ];
  },
});

var NoteFontStyle = Extension.create({
  name: "noteFontStyle",
  addGlobalAttributes: function () {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontFamily: {
            default: null,
            parseHTML: function (el) {
              var f = noteFontByCss(el.style.fontFamily);
              return f ? f.css : null;
            },
            renderHTML: function (attrs) {
              return attrs.fontFamily ? { style: "font-family: " + attrs.fontFamily } : {};
            },
          },
          fontSize: {
            default: null,
            parseHTML: function (el) {
              return noteFontSizeFrom(el);
            },
            renderHTML: function (attrs) {
              if (!attrs.fontSize) return {};
              var out = { style: "font-size: " + attrs.fontSize };
              if (NOTE_FONT_SIZES.indexOf(attrs.fontSize) < 0) out["data-note-fs"] = "1";
              return out;
            },
          },
        },
      },
    ];
  },
  addCommands: function () {
    return {
      setNoteFontStyle: function (attrs) {
        return function (props) {
          return props.chain().setMark("textStyle", attrs).removeEmptyTextStyle().run();
        };
      },
    };
  },
});

var NoteHighlight = Mark.create({
  name: "highlight",
  parseHTML: function () {
    return [{ tag: "mark" }];
  },
  renderHTML: function () {
    return ["mark", { class: "note-highlight" }, 0];
  },
  addCommands: function () {
    var name = this.name;
    return {
      toggleHighlight: function () {
        return function (props) {
          return props.commands.toggleMark(name);
        };
      },
    };
  },
});

function clearToParagraph(editor) {
  return editor.chain().focus().clearNodes().setParagraph().run();
}

function collectEditorHeadings(editor) {
  var items = [];
  var byPos = {};
  if (!editor || !editor.state || !editor.state.doc) return items;
  function addHeading(level, text, pos) {
    var label = String(text || "").trim().replace(/\s+/g, " ");
    if (!label) return;
    var key = String(pos);
    if (byPos[key]) return;
    byPos[key] = true;
    items.push({ level: Number(level) || 3, text: label, pos: pos });
  }
  editor.state.doc.descendants(function (node, pos) {
    if (!node || !node.type || node.type.name !== "heading") return true;
    addHeading(node.attrs && node.attrs.level, node.textContent, pos);
    return true;
  });
  if (!items.length && editor.view && editor.view.dom) {
    editor.view.dom.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach(function (el) {
      try {
        var pos = editor.view.posAtDOM(el, 0);
        var level = Number(String(el.tagName || "H3").replace(/^H/i, "")) || 3;
        addHeading(level, el.textContent, pos);
      } catch (_) {}
    });
  }
  items.sort(function (a, b) {
    return a.pos - b.pos;
  });
  return items;
}

function activeHeadingPos(headings, selectionPos) {
  var active = null;
  for (var i = 0; i < headings.length; i++) {
    if (headings[i].pos <= selectionPos) active = headings[i].pos;
    else break;
  }
  return active;
}

function findHeadingElementAtPos(editor, pos) {
  if (!editor || !editor.view) return null;
  try {
    var found = editor.view.domAtPos(pos + 1);
    var node = found && found.node;
    var el = node && node.nodeType === 1 ? node : node && node.parentElement;
    if (!el || !el.closest) return null;
    return el.closest("h1, h2, h3, h4, h5, h6");
  } catch (_) {
    return null;
  }
}

function gotoEditorHeading(editor, pos, scrollParent) {
  if (!editor || !editor.state || !editor.state.doc) return;
  var target = Math.max(0, Math.min(pos + 1, editor.state.doc.content.size));
  try {
    editor.view.dispatch(
      editor.state.tr.setSelection(TextSelection.create(editor.state.doc, target))
    );
  } catch (_) {}
  window.requestAnimationFrame(function () {
    var headingEl = findHeadingElementAtPos(editor, pos);
    if (!headingEl || !scrollParent || !scrollParent.getBoundingClientRect) return;
    var headRect = headingEl.getBoundingClientRect();
    var scrollRect = scrollParent.getBoundingClientRect();
    var top = scrollParent.scrollTop + headRect.top - scrollRect.top;
    scrollParent.scrollTo({ top: Math.max(0, top), behavior: "auto" });
    try {
      if (editor.view && editor.view.dom && editor.view.dom.focus) {
        editor.view.dom.focus({ preventScroll: true });
      }
    } catch (_) {}
  });
}

function mountToc(tocEl, editor, options) {
  if (!tocEl || !editor) return function () {};
  options = options || {};
  var extraItems =
    typeof options.extraTocItems === "function" ? options.extraTocItems : null;
  var onNavigate =
    typeof options.onTocNavigate === "function" ? options.onTocNavigate : function () {};
  var scrollParent = options.scrollParent || null;

  function renderToc() {
    var headings = collectEditorHeadings(editor);
    var selectionPos = editor.state && editor.state.selection ? editor.state.selection.from : 0;
    var activePos = activeHeadingPos(headings, selectionPos);
    tocEl.innerHTML = "";

    headings.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "note-editor-toc-item note-editor-toc-item--level-" +
        String(item.level) +
        (item.pos === activePos ? " note-editor-toc-item--active" : "");
      btn.textContent = item.text;
      btn.title = item.text;
      btn.addEventListener("click", function () {
        gotoEditorHeading(editor, item.pos, scrollParent);
        onNavigate(item);
      });
      tocEl.appendChild(btn);
    });
    if (extraItems) extraItems(tocEl);
    if (!tocEl.children.length) {
      var empty = document.createElement("p");
      empty.className = "note-editor-toc-empty";
      empty.textContent = "Добавьте заголовки, чтобы увидеть оглавление.";
      tocEl.appendChild(empty);
    }
  }

  tocEl._refreshToc = renderToc;

  editor.on("transaction", renderToc);
  editor.on("selectionUpdate", renderToc);
  renderToc();

  return function cleanupToc() {
    editor.off("transaction", renderToc);
    editor.off("selectionUpdate", renderToc);
    tocEl.innerHTML = "";
  };
}

function closeTgMenus(toolbarEl) {
  if (!toolbarEl) return;
  toolbarEl.querySelectorAll(".note-tg-menu").forEach(function (el) {
    el.classList.add("hidden");
  });
  toolbarEl.querySelectorAll(".note-tg-btn--menu-open").forEach(function (el) {
    el.classList.remove("note-tg-btn--menu-open");
  });
}

/** «Отменить / Повторить» в совместной заметке — только свои правки (Yjs UndoManager). */
function yUndoCan(editor, act) {
  var st = yUndoPluginKey.getState(editor.state);
  var um = st && st.undoManager;
  if (!um) return false;
  return act === "undo" ? um.undoStack.length > 0 : um.redoStack.length > 0;
}

function mountToolbar(toolbarEl, editor) {
  var refreshFn = function () {};
  // input всегда в toolbar (не внутри .note-tg-menu) — иначе display:none меню убивает iOS picker.
  var fileInput = null;

  function ensureFileInput() {
    if (fileInput && fileInput.isConnected) return fileInput;
    var el = document.createElement("input");
    el.type = "file";
    el.accept =
      "image/*,image/jpeg,image/png,image/heic,image/heif,image/webp,.jpg,.jpeg,.png,.heic,.webp";
    el.className = "note-tg-file-input-host";
    el.tabIndex = -1;
    el.setAttribute("aria-hidden", "true");
    el.addEventListener("change", onFileChosen);
    toolbarEl.appendChild(el);
    fileInput = el;
    return fileInput;
  }

  function onFileChosen() {
    var input = fileInput;
    var file = input && input.files && input.files[0];
    closeTgMenus(toolbarEl);
    if (!file) return;
    var mime = String(file.type || "");
    var name = String(file.name || "");
    var looksImage =
      /^image\//i.test(mime) || /\.(jpe?g|png|gif|webp|heic|heif)$/i.test(name);
    if (!looksImage) {
      window.alert("В заметку можно вставить изображение (JPG, PNG, HEIC…).");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      window.alert("Фото больше 25 МБ. Выберите файл поменьше.");
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      compressImageDataUrl(String(reader.result || "")).then(insertImage);
    };
    function insertImage(src) {
      if (!src || editor.isDestroyed) return;
      editor
        .chain()
        .focus()
        .insertContent([
          {
            type: "image",
            attrs: { src: src, alt: file.name || "Фото" },
          },
          { type: "paragraph" },
        ])
        .run();
      refreshFn();
    }
    reader.readAsDataURL(file);
  }

  toolbarEl.className = "note-rich-editor-toolbar note-tg-toolbar";
  function btn(cls, attr, title, icon) {
    return (
      '<button type="button" class="note-tg-btn' + (cls ? " " + cls : "") + '" ' + attr +
      ' title="' + title + '" aria-label="' + title + '">' + svgIcon(icon) + "</button>"
    );
  }
  function item(cmd, check, icon, label, extra) {
    return (
      '<button type="button" class="note-tg-menu-item' +
      (cmd === "attach" ? " note-tg-menu-item--attach" : "") +
      (extra ? " " + extra : "") +
      '" data-cmd="' +
      cmd +
      '"' +
      (check ? ' data-check="' + check + '"' : "") +
      ">" +
      (icon ? svgIcon(icon) : "") +
      "<span>" +
      label +
      "</span></button>"
    );
  }
  function sep() {
    return '<div class="note-tg-menu-sep" role="separator"></div>';
  }
  // Списки — как в Telegram: «Нет» без иконки, текущий пункт — галочка справа.
  function listItems(plainLabel) {
    return (
      item("paragraph", "plain-list", "blank", plainLabel) +
      item("bullet", "bulletList", "list", "Маркированный список") +
      item("ordered", "orderedList", "numbered", "Нумерованный список") +
      item("task", "taskList", "checklist", "Чек-лист") +
      item("toggle", "noteToggle", "toggle", "Сворачиваемый блок")
    );
  }
  // «+» — вид блока (как в Telegram), под чертой — вставка. «Заголовок ›» и «Шрифт и размер ›» открываются внутри меню.
  var styleMenu =
    '<div class="note-tg-menu note-tg-menu--paged hidden" data-menu-panel="style" role="menu">' +
    '<div class="note-tg-page" data-page="main">' +
    '<button type="button" class="note-tg-menu-item note-tg-menu-page" data-go="heading" data-check="heading">' +
    svgIcon("heading") + "<span>Заголовок</span>" + svgIcon("chevronRight") + "</button>" +
    item("paragraph", "plain", "text", "Текст") +
    item("quote", "blockquote", "quote", "Цитата") +
    '<button type="button" class="note-tg-menu-item note-tg-menu-page" data-go="font">' +
    svgIcon("font") + "<span>Шрифт и размер</span>" + svgIcon("chevronRight") + "</button>" +
    // Телефон: редкое форматирование — страницей, на панели при выделении нет места.
    '<button type="button" class="note-tg-menu-item note-tg-menu-page note-tg-mob-only" data-go="more">' +
    svgIcon("strike") + "<span>Ещё</span>" + svgIcon("chevronRight") + "</button>" +
    sep() +
    item("columns", "", "columns", "Столбец") +
    item("indent", "", "indent", "Сдвинуть вправо") +
    item("outdent", "", "outdent", "Сдвинуть влево") +
    item("divider", "", "divider", "Разделитель") +
    item("noteref", "", "noteref", "Ссылка на заметку") +
    "</div>" +
    '<div class="note-tg-page hidden" data-page="heading">' +
    '<button type="button" class="note-tg-menu-item note-tg-menu-page note-tg-menu-back" data-go="main">' +
    svgIcon("chevronLeft") + "<span>Назад</span></button>" +
    item("heading-1", "heading-1", "", "Заголовок 1", "note-tg-h note-tg-h1") +
    item("heading-2", "heading-2", "", "Заголовок 2", "note-tg-h note-tg-h2") +
    item("heading-3", "heading-3", "", "Заголовок 3", "note-tg-h note-tg-h3") +
    item("heading-4", "heading-4", "", "Заголовок 4", "note-tg-h note-tg-h4") +
    "</div>" +
    '<div class="note-tg-page note-tg-page--font hidden" data-page="font">' +
    '<button type="button" class="note-tg-menu-item note-tg-menu-page note-tg-menu-back" data-go="main">' +
    svgIcon("chevronLeft") + "<span>Назад</span></button>" +
    '<p class="note-tg-menu-hint" data-font-target></p>' +
    '<p class="note-tg-menu-label">Шрифт</p>' +
    '<div class="note-tg-chip-row">' +
    NOTE_FONTS.map(function (f) {
      return (
        '<button type="button" class="note-tg-menu-item note-tg-font-item" data-cmd="font:' + f.id + '" data-check="font:' + f.id + '"' +
        (f.css ? " style='font-family: " + f.css.replace(/'/g, "&#39;") + "'" : "") +
        "><span>" + f.label + "</span></button>"
      );
    }).join("") +
    "</div>" +
    '<p class="note-tg-menu-label">Размер</p>' +
    '<div class="note-tg-size-row note-tg-chip-row">' +
    '<button type="button" class="note-tg-menu-item note-tg-size-item" data-cmd="size:" data-check="size:" title="Как в приложении"><span>Авто</span></button>' +
    NOTE_FONT_SIZES.map(function (v) {
      return '<button type="button" class="note-tg-menu-item note-tg-size-item" data-cmd="size:' + v + '" data-check="size:' + v + '"><span>' + parseInt(v, 10) + "</span></button>";
    }).join("") +
    "</div></div>" +
    '<div class="note-tg-page hidden" data-page="more">' +
    '<button type="button" class="note-tg-menu-item note-tg-menu-page note-tg-menu-back" data-go="main">' +
    svgIcon("chevronLeft") + "<span>Назад</span></button>" +
    item("strike", "strike", "strike", "Зачёркнутый") +
    item("underline", "underline", "underline", "Подчёркнутый") +
    item("highlight", "highlight", "highlight", "Маркер") +
    "</div></div>";

  toolbarEl.className = "note-rich-editor-toolbar note-tg-toolbar";
  toolbarEl.innerHTML =
    '<div class="note-tg-capsule note-tg-capsule--history">' +
    btn("note-tg-btn--undo", 'data-action="undo"', "Отменить", "undo") +
    btn("note-tg-btn--redo", 'data-action="redo"', "Повторить", "redo") +
    "</div>" +
    // Как в Telegram: «+» (вид блока, шрифт и кегль), список, таблица, файл.
    '<div class="note-tg-capsule note-tg-capsule--default" data-mode="default">' +
    btn("", 'data-menu="style"', "Вид текста и вставка", "plus") +
    btn("", 'data-menu="list"', "Список", "list") +
    btn("", 'data-action="table"', "Таблица", "table") +
    btn("note-tg-btn--attach", 'data-action="attach"', "Файл или фото", "paperclip") +
    "</div>" +
    '<div class="note-tg-capsule note-tg-capsule--selection hidden" data-mode="selection">' +
    // Телефон: ИИ / Обсудить / Задача — здесь, а не всплывающей плашкой над выделением
    // (там системное меню «Копировать / Вырезать», убрать его веб-приложение не может).
    btn("note-tg-mob-only note-tg-sel-act hidden note-tg-btn--sel-ai", 'data-action="sel-ai"', "ИИ", "sparkle") +
    btn("note-tg-mob-only note-tg-sel-act hidden", 'data-action="sel-discuss"', "Обсудить", "discuss") +
    btn("note-tg-mob-only note-tg-sel-act hidden", 'data-action="sel-task"', "Задача", "check") +
    btn("note-tg-mob-only note-tg-sel-act hidden", 'data-action="sel-link"', "Ссылка на фрагмент", "fragment") +
    '<span class="note-tg-sep note-tg-mob-only note-tg-sel-act hidden" aria-hidden="true"></span>' +
    btn("", 'data-action="bold"', "Жирный", "bold") +
    btn("", 'data-action="italic"', "Курсив", "italic") +
    btn("note-tg-desk-only", 'data-action="strike"', "Зачёркнутый", "strike") +
    btn("note-tg-desk-only", 'data-action="underline"', "Подчёркнутый", "underline") +
    btn("note-tg-desk-only", 'data-action="highlight"', "Маркер", "highlight") +
    btn("", 'data-action="link"', "Ссылка", "link") +
    btn("note-tg-desk-only", 'data-action="indent"', "Сдвинуть вправо (Tab)", "indent") +
    '<span class="note-tg-sep" aria-hidden="true"></span>' +
    btn("", 'data-menu="style"', "Вид текста, шрифт и размер", "heading") +
    btn("", 'data-menu="list-sel"', "Список", "list") +
    "</div>" +
    styleMenu +
    '<div class="note-tg-menu hidden" data-menu-panel="list" role="menu">' + listItems("Нет") + "</div>" +
    '<div class="note-tg-menu hidden" data-menu-panel="list-sel" role="menu">' + listItems("Нет") + "</div>";

  ensureFileInput();

  function runCmd(cmd) {
    releaseSelection(true); // команда — к тому, что было выделено до открытия меню
    // Keep editor focused — menu taps would otherwise blur first and drop the click.
    if (/^heading-\d$/.test(String(cmd || ""))) {
      var level = parseInt(String(cmd).slice(-1), 10);
      if (editor.isActive("heading", { level: level })) {
        clearToParagraph(editor);
      } else {
        editor.chain().focus().clearNodes().setHeading({ level: level }).run();
      }
    } else if (cmd === "heading") {
      if (editor.isActive("heading", { level: 3 })) {
        clearToParagraph(editor);
      } else {
        editor.chain().focus().clearNodes().setHeading({ level: 3 }).run();
      }
    } else if (/^font:/.test(String(cmd || ""))) {
      var font = NOTE_FONTS.filter(function (f) {
        return f.id === String(cmd).slice(5);
      })[0];
      applyFontStyle({ fontFamily: (font && font.css) || null });
    } else if (/^size:/.test(String(cmd || ""))) {
      applyFontStyle({ fontSize: String(cmd).slice(5) || null });
    } else if (cmd === "paragraph") {
      clearToParagraph(editor);
    } else if (cmd === "quote") {
      editor.chain().focus().toggleBlockquote().run();
    } else if (cmd === "task") {
      editor.chain().focus().toggleTaskList().run();
    } else if (cmd === "toggle") {
      editor.chain().focus().toggleNoteToggle().run();
    } else if (cmd === "bullet") {
      editor.chain().focus().toggleBulletList().run();
    } else if (cmd === "ordered") {
      editor.chain().focus().toggleOrderedList().run();
    } else if (cmd === "table") {
      editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
    } else if (cmd === "attach") {
      pickImage();
    } else if (cmd === "strike") {
      editor.chain().focus().toggleStrike().run();
    } else if (cmd === "underline") {
      editor.chain().focus().toggleUnderline().run();
    } else if (cmd === "highlight") {
      editor.chain().focus().toggleHighlight().run();
    } else if (cmd === "indent") {
      editor.chain().focus().indentText().run();
    } else if (cmd === "outdent") {
      editor.chain().focus().outdentText().run();
    } else if (cmd === "columns") {
      editor.chain().focus().splitIntoColumns().run();
    } else if (cmd === "divider") {
      editor.chain().focus().setHorizontalRule().run();
    } else if (cmd === "noteref") {
      // «+» в начале слова открывает поиск заметок (ссылка-чип).
      var st = editor.state;
      var before = st.selection.$from.parentOffset > 0
        ? st.doc.textBetween(st.selection.from - 1, st.selection.from, "\n", "\uFFFC")
        : "";
      editor.chain().focus().insertContent(before && !/\s/.test(before) ? " +" : "+").run();
    }
    refreshFn();
  }

  var lastMenuCmdAt = 0;

  function showMenuPage(panel, page) {
    panel.querySelectorAll(".note-tg-page").forEach(function (p) {
      p.classList.toggle("hidden", p.getAttribute("data-page") !== page);
    });
  }

  function onMenuItem(item) {
    if (!item) return;
    var go = item.getAttribute("data-go");
    if (go) {
      var pnl = item.closest(".note-tg-menu");
      if (go === "font") syncFontTarget(pnl);
      showMenuPage(pnl, go);
      pnl.scrollTop = 0;
      placeMenu(pnl, toolbarEl.querySelector(".note-tg-btn--menu-open"));
      return;
    }
    var now = Date.now();
    if (now - lastMenuCmdAt < 400) return;
    lastMenuCmdAt = now;
    runCmd(item.getAttribute("data-cmd"));
    closeTgMenus(toolbarEl);
  }

  // Выделение на момент открытия меню «+» / «H» (страница «Шрифт и размер»): на телефоне касание кнопки / пункта меню сбрасывает
  // выделение раньше, чем выбран шрифт, и он применялся к пустому курсору.
  var fontMenuRange = null;
  editor.on("transaction", function (p) {
    var tr = p && p.transaction;
    if (tr && tr.docChanged && !tr.getMeta("noteFontApply")) fontMenuRange = null;
  });
  var toolbarTouchAt = 0;
  toolbarEl.addEventListener(
    "pointerdown",
    function () {
      toolbarTouchAt = Date.now();
    },
    true
  );
  editor.on("selectionUpdate", function () {
    var sel = editor.state.selection;
    if (!fontMenuRange) return;
    // Новое выделение где-то ещё — забываем старое.
    if (!sel.empty && (sel.from !== fontMenuRange.from || sel.to !== fontMenuRange.to)) fontMenuRange = null;
    // Курсор поставили сами (не сброс от касания панели) — тоже.
    else if (sel.empty && Date.now() - toolbarTouchAt > 1000) fontMenuRange = null;
  });
  // Ничего не выделено — шрифт / кегль всей заметки: по каждому текстовому блоку.
  // Кегль заголовков — пропорционально (H1 ×1.5, H2 ×1.3, H3 ×1.15), шрифт — у всех.
  function applyFontStyleToDoc(attrs) {
    var sizeOnly = attrs.fontSize !== undefined && attrs.fontFamily === undefined;
    var state = editor.state;
    var type = state.schema.marks.textStyle;
    var tr = state.tr;
    var touched = false;
    state.doc.descendants(function (node, pos, parent) {
      if (!node.isText) return true;
      var own = attrs;
      if (sizeOnly && parent && parent.type.name === "heading") {
        // Заголовок крупнее текста в той же пропорции, что и по умолчанию.
        var scale = NOTE_HEADING_SCALE[parent.attrs.level] || 1;
        own = { fontSize: attrs.fontSize ? Math.round(parseInt(attrs.fontSize, 10) * scale) + "px" : null };
      }
      var cur = node.marks.filter(function (m) {
        return m.type === type;
      })[0];
      var next = Object.assign({}, cur ? cur.attrs : {}, own);
      var has = Object.keys(next).some(function (k) {
        return next[k] != null && next[k] !== "";
      });
      tr.removeMark(pos, pos + node.nodeSize, type);
      if (has) tr.addMark(pos, pos + node.nodeSize, type.create(next));
      touched = true;
      return false;
    });
    if (!touched) {
      editor.chain().focus().setNoteFontStyle(attrs).run();
      return;
    }
    // Новый текст у курсора — тем же шрифтом.
    var caretMarks = state.storedMarks || state.selection.$from.marks();
    var caretStyle = caretMarks.filter(function (m) {
      return m.type === type;
    })[0];
    var stored = caretMarks.filter(function (m) {
      return m.type !== type;
    });
    var nextStored = Object.assign({}, caretStyle ? caretStyle.attrs : {}, attrs);
    if (Object.keys(nextStored).some(function (k) { return nextStored[k] != null; })) stored = stored.concat(type.create(nextStored));
    tr.setStoredMarks(stored);
    tr.setMeta("noteFontApply", true);
    editor.view.dispatch(tr);
    editor.commands.focus();
  }

  function fontTargetIsDoc() {
    return editor.state.selection.empty && !fontMenuRange && !menuRange;
  }

  function applyFontStyle(attrs) {
    var sel = editor.state.selection;
    var range = !sel.empty ? { from: sel.from, to: sel.to } : fontMenuRange;
    if (!range) {
      applyFontStyleToDoc(attrs);
      return;
    }
    var max = editor.state.doc.content.size;
    var chain = editor.chain().focus().setMeta("noteFontApply", true);
    if (range && range.to <= max && range.from < range.to) chain = chain.setTextSelection(range);
    chain.setNoteFontStyle(attrs).run();
    if (range) fontMenuRange = { from: range.from, to: Math.min(range.to, editor.state.doc.content.size) };
  }

  function syncFontTarget(panel) {
    var hint = panel.querySelector("[data-font-target]");
    if (hint) hint.textContent = fontTargetIsDoc() ? "Ко всей заметке · выделите текст, чтобы изменить часть" : "К выделенному тексту";
  }

  // Телефон: пока открыто меню панели, системное меню выделения («Вырезать / Скопировать»)
  // рисуется поверх него. Родное выделение на это время сворачиваем (меню iOS пропадает),
  // выделенное подсвечиваем сами и возвращаем выделение перед командой.
  var menuRangeKey = new PluginKey("leoMenuRange");
  var menuRange = null;
  try {
    editor.registerPlugin(
      new Plugin({
        key: menuRangeKey,
        state: {
          init: function () {
            return null;
          },
          apply: function (tr, val) {
            var m = tr.getMeta(menuRangeKey);
            if (m !== undefined) return m;
            if (!val || !tr.docChanged) return val;
            return { from: tr.mapping.map(val.from), to: tr.mapping.map(val.to) };
          },
        },
        props: {
          decorations: function (state) {
            var r = menuRangeKey.getState(state);
            if (!r || r.from >= r.to) return null;
            return DecorationSet.create(state.doc, [Decoration.inline(r.from, r.to, { class: "note-menu-range" })]);
          },
        },
      })
    );
  } catch (_) {}

  function holdSelection() {
    var sel = editor.state.selection;
    var coarse = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    if (!coarse || sel.empty || !(sel instanceof TextSelection)) return;
    menuRange = { from: sel.from, to: sel.to };
    toolbarEl.setAttribute("data-menu-range", "1");
    editor.view.dispatch(
      editor.state.tr
        .setSelection(TextSelection.create(editor.state.doc, sel.to))
        .setMeta(menuRangeKey, menuRange)
        .setMeta("addToHistory", false)
    );
  }

  function releaseSelection(restore) {
    if (!menuRange) return;
    var r = menuRangeKey.getState(editor.state) || menuRange;
    menuRange = null;
    toolbarEl.removeAttribute("data-menu-range");
    var tr = editor.state.tr.setMeta(menuRangeKey, null).setMeta("addToHistory", false);
    if (restore && r && r.from < r.to && r.to <= tr.doc.content.size) {
      tr.setSelection(TextSelection.create(tr.doc, r.from, r.to));
    }
    editor.view.dispatch(tr);
  }

  function openMenu(name, triggerBtn) {
    var panel = toolbarEl.querySelector('[data-menu-panel="' + name + '"]');
    if (!panel) return;
    if (name === "style") {
      var fs = editor.state.selection;
      // Выделение пропало (повторное открытие меню на телефоне) — остаётся прошлый диапазон, пока текст не правили.
      if (!fs.empty) fontMenuRange = { from: fs.from, to: fs.to };
    }
    var wasOpen = !panel.classList.contains("hidden");
    closeTgMenus(toolbarEl);
    closeLinkPopover();
    if (wasOpen) {
      releaseSelection(true);
      return;
    }
    if (panel.classList.contains("note-tg-menu--paged")) showMenuPage(panel, "main");
    panel.classList.remove("hidden");
    panel.scrollTop = 0;
    if (triggerBtn) triggerBtn.classList.add("note-tg-btn--menu-open");
    placeMenu(panel, triggerBtn);
    holdSelection();
  }

  // Меню открываем над своей кнопкой (по центру), не вылезая за края тулбара.
  function placeMenu(panel, triggerBtn) {
    var host = panel.offsetParent;
    if (!triggerBtn || !host) return;
    var hr = host.getBoundingClientRect();
    var br = triggerBtn.getBoundingClientRect();
    // В пределах экрана (8 px от краёв), а не панели: широкое меню («Шрифт и размер») уезжало за правый край.
    var vw = document.documentElement.clientWidth || window.innerWidth;
    panel.style.maxWidth = vw - 16 + "px";
    var w = panel.offsetWidth;
    var left = br.left + br.width / 2 - w / 2 - hr.left;
    var minL = 8 - hr.left;
    var maxL = vw - 8 - w - hr.left;
    left = Math.max(minL, Math.min(left, maxL));
    panel.style.left = Math.round(left) + "px";
    panel.style.right = "auto";
    // Не выше шапки заметки («← Назад»): остальное листается внутри меню. Считаем от шапки,
    // а не от visualViewport — в iOS PWA с клавиатурой его offsetTop не в тех же координатах.
    var form = toolbarEl.closest(".note-editor-modal-form, .modal");
    var bar = form && form.querySelector(".note-editor-web-back-bar:not(.hidden), .modal-header:not([aria-hidden='true'])");
    var topLimit = 8;
    if (bar && bar.offsetHeight) topLimit = Math.max(8, bar.getBoundingClientRect().bottom + 6);
    else if (form) topLimit = Math.max(8, form.getBoundingClientRect().top + 8);
    panel.style.maxHeight = Math.max(200, Math.round(toolbarEl.getBoundingClientRect().top - 10 - topLimit)) + "px";
  }

  // Вызывать только из click/keydown: тач-pointerdown на iOS не даёт user activation,
  // и input.click() молча игнорируется.
  function pickImage() {
    var input = ensureFileInput();
    try {
      input.click();
    } catch (_) {}
  }

  function closeInsertMenuSoon() {
    window.setTimeout(function () {
      closeTgMenus(toolbarEl);
    }, 300);
  }

  // Prevent toolbar buttons from stealing focus from the editor (incl. menu items).
  toolbarEl.addEventListener("mousedown", function (e) {
    var attach = e.target.closest && e.target.closest('[data-cmd="attach"], [data-action="attach"]');
    if (attach) return; // нельзя preventDefault — ломает file picker
    var btn = e.target.closest("button");
    if (!btn || !toolbarEl.contains(btn)) return;
    e.preventDefault();
  });
  // Пункт меню срабатывает по отпусканию без сдвига: касание с движением листает меню
  // (раньше команда шла на pointerdown — меню нельзя было прокрутить, любое касание выбирало пункт).
  var menuTap = null;
  var menuTapDoneAt = 0;
  toolbarEl.addEventListener(
    "pointerdown",
    function (e) {
      var item = e.target.closest(".note-tg-menu-item");
      if (!item || !toolbarEl.contains(item)) return;
      if (e.button != null && e.button !== 0) return;
      // Attach открывается в click (нужен user activation для file picker).
      if (item.getAttribute("data-cmd") === "attach") return;
      menuTap = { item: item, x: e.clientX, y: e.clientY, id: e.pointerId };
    },
    true
  );
  toolbarEl.addEventListener(
    "pointermove",
    function (e) {
      if (menuTap && e.pointerId === menuTap.id && Math.abs(e.clientX - menuTap.x) + Math.abs(e.clientY - menuTap.y) > 10) menuTap = null;
    },
    true
  );
  toolbarEl.addEventListener(
    "pointercancel",
    function () {
      menuTap = null; // начали листать
    },
    true
  );
  toolbarEl.addEventListener(
    "pointerup",
    function (e) {
      var tap = menuTap;
      menuTap = null;
      if (!tap || e.pointerId !== tap.id) return;
      var item = e.target.closest && e.target.closest(".note-tg-menu-item");
      if (item !== tap.item) return;
      e.preventDefault();
      e.stopPropagation();
      menuTapDoneAt = Date.now();
      var paging = item.hasAttribute("data-go");
      onMenuItem(item);
      if (!paging) closeTgMenus(toolbarEl);
    },
    true
  );

  toolbarEl.addEventListener("click", function (e) {
    var item = e.target.closest(".note-tg-menu-item");
    if (item && toolbarEl.contains(item)) {
      if (item.getAttribute("data-cmd") === "attach") {
        e.preventDefault();
        e.stopPropagation();
        pickImage();
        closeInsertMenuSoon();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      // Уже сработал по pointerup — click следом не повторяем; без pointer-событий (клавиатура) — здесь.
      if (Date.now() - menuTapDoneAt < 600) return;
      onMenuItem(item);
      if (!item.hasAttribute("data-go")) closeTgMenus(toolbarEl);
      return;
    }
    var btn = e.target.closest(".note-tg-btn");
    if (!btn || !toolbarEl.contains(btn)) return;
    e.preventDefault();
    var menu = btn.getAttribute("data-menu");
    if (menu) {
      openMenu(menu, btn);
      return;
    }
    var action = btn.getAttribute("data-action");
    if (menuRange) {
      // Кнопка панели при открытом меню — к выделенному до меню тексту.
      closeTgMenus(toolbarEl);
      releaseSelection(true);
    }
    if (/^sel-/.test(action || "")) {
      // ИИ / Обсудить / Задача по выделению — их выполняет приложение (как плашка на десктопе).
      closeTgMenus(toolbarEl);
      window.dispatchEvent(new CustomEvent("leo-note-selection-action", { detail: { kind: action.slice(4) } }));
      return;
    }
    if (action === "indent") {
      editor.chain().focus().indentText().run();
    } else if (action === "undo") {
      if (editor.__leoYjs) {
        editor.commands.focus();
        yUndo(editor.state);
      } else if (editor.can().chain().focus().undo().run()) editor.chain().focus().undo().run();
    } else if (action === "redo") {
      if (editor.__leoYjs) {
        editor.commands.focus();
        yRedo(editor.state);
      } else if (editor.can().chain().focus().redo().run()) editor.chain().focus().redo().run();
    } else if (action === "task") {
      editor.chain().focus().toggleTaskList().run();
    } else if (action === "highlight") {
      editor.chain().focus().toggleHighlight().run();
    } else if (action === "table") {
      editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run();
    } else if (action === "attach") {
      pickImage();
    } else if (action === "bold") {
      editor.chain().focus().toggleBold().run();
    } else if (action === "italic") {
      editor.chain().focus().toggleItalic().run();
    } else if (action === "strike") {
      editor.chain().focus().toggleStrike().run();
    } else if (action === "underline") {
      editor.chain().focus().toggleUnderline().run();
    } else if (action === "link") {
      closeTgMenus(toolbarEl);
      showLinkPopover(toolbarEl, editor, refreshFn);
    } else if (action === "quote") {
      editor.chain().focus().toggleBlockquote().run();
    }
    refreshFn();
  });

  function outsideMenus(e) {
    if (toolbarEl.contains(e.target)) return;
    closeTgMenus(toolbarEl);
    releaseSelection(false); // нажали в текст — там и ставится курсор
  }
  document.addEventListener("mousedown", outsideMenus, true);
  document.addEventListener("touchstart", outsideMenus, true);

  var hadTextSelection = false;
  function refreshFnImpl() {
    var hasSelection = !editor.state.selection.empty || !!menuRange;
    var defCap = toolbarEl.querySelector(".note-tg-capsule--default");
    var selCap = toolbarEl.querySelector(".note-tg-capsule--selection");
    if (defCap) defCap.classList.toggle("hidden", hasSelection);
    if (selCap) selCap.classList.toggle("hidden", !hasSelection);
    // При выделении — только форматирование (ИИ, история и обсуждение прячутся, как на макете).
    var histCap = toolbarEl.querySelector(".note-tg-capsule--history");
    if (histCap) histCap.classList.toggle("hidden", hasSelection);
    var row = toolbarEl.closest(".note-editor-toolbar-row");
    if (row) row.classList.toggle("is-selection", hasSelection);
    // Only close when leaving selection mode — never while caret is empty
    // (that race hid the «+» menu before the item click fired).
    var fontMenuOpen = !!toolbarEl.querySelector('[data-menu-panel="style"]:not(.hidden) [data-page="font"]:not(.hidden)');
    if (hadTextSelection !== hasSelection && (hadTextSelection || hasSelection) && !(fontMenuOpen && fontMenuRange)) closeTgMenus(toolbarEl);
    hadTextSelection = hasSelection;

    ["undo", "redo"].forEach(function (act) {
      var hb = toolbarEl.querySelector('[data-action="' + act + '"]');
      if (!hb) return;
      var can = editor.__leoYjs
        ? yUndoCan(editor, act)
        : act === "undo" ? editor.can().chain().focus().undo().run() : editor.can().chain().focus().redo().run();
      hb.disabled = !can;
      hb.classList.toggle("note-tg-btn--disabled", !can);
    });

    var markMap = {
      bold: "bold",
      italic: "italic",
      strike: "strike",
      underline: "underline",
      link: "link",
      quote: "blockquote",
      highlight: "highlight",
      task: "taskList",
    };
    Object.keys(markMap).forEach(function (action) {
      toolbarEl.querySelectorAll('[data-action="' + action + '"]').forEach(function (btn) {
        btn.classList.toggle("note-tg-btn--active", editor.isActive(markMap[action]));
      });
    });
    var ts = editor.getAttributes("textStyle") || {};
    var curFont = noteFontByCss(ts.fontFamily);
    toolbarEl.querySelectorAll('[data-menu="style"]').forEach(function (btn) {
      btn.classList.toggle("note-tg-btn--active", editor.isActive("heading") || editor.isActive("blockquote") || !!(curFont || ts.fontSize));
    });
    toolbarEl.querySelectorAll(".note-tg-menu-item[data-check]").forEach(function (item) {
      var check = item.getAttribute("data-check");
      if (/^font:/.test(check)) {
        item.classList.toggle("note-tg-menu-item--active", check.slice(5) === (curFont ? curFont.id : ""));
        return;
      }
      if (/^size:/.test(check)) {
        item.classList.toggle("note-tg-menu-item--active", check.slice(5) === String(ts.fontSize || ""));
        return;
      }
      var headingMatch = String(check || "").match(/^heading-(\d)$/);
      var on =
        check === "plain-list"
          ? !(editor.isActive("bulletList") || editor.isActive("orderedList") || editor.isActive("taskList") || editor.isActive("noteToggle"))
          : check === "plain"
          ? !(
              editor.isActive("bulletList") ||
              editor.isActive("orderedList") ||
              editor.isActive("taskList") ||
              editor.isActive("heading") ||
              editor.isActive("blockquote")
            )
          : headingMatch
          ? editor.isActive("heading", { level: parseInt(headingMatch[1], 10) })
          : check === "heading"
          ? editor.isActive("heading")
          : editor.isActive(check);
      item.classList.toggle("note-tg-menu-item--active", on);
    });
  }

  refreshFn = refreshFnImpl;
  editor.on("transaction", refreshFnImpl);
  editor.on("selectionUpdate", refreshFnImpl);
  refreshFnImpl();

  var wrap = toolbarEl.closest(".note-editor-toolbar-wrap");
  if (wrap) wrap.classList.add("note-editor-toolbar-wrap--bottom");

  return function cleanup() {
    document.removeEventListener("mousedown", outsideMenus, true);
    document.removeEventListener("touchstart", outsideMenus, true);
  };
}

var HASHTAG_QUERY_RE = /(?:^|\s)(#([A-Za-zА-Яа-яЁё0-9_]{0,40}))$/;
var HASHTAG_INLINE_RE = /#([A-Za-zА-Яа-яЁё0-9_]{1,40})/g;
/** «+» mention: link to a note, sheet, task, reminder or person (chip). After start/whitespace only. */
var NOTE_REF_QUERY_RE = /(?:^|[\s￼])(\+([^\n]*))$/;

function refSvg(paths) {
  return (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    paths +
    "</svg>"
  );
}

var NOTE_REF_CHIP_ICON_SVG = refSvg(
  '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/>'
);

/** Типы сущностей в «+»: порядок — как в меню. */
var NOTE_REF_KINDS = [
  { kind: "task", label: "Задача", icon: refSvg('<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m8 12 3 3 5-6"/>') },
  { kind: "note", label: "Заметка", icon: NOTE_REF_CHIP_ICON_SVG },
  { kind: "person", label: "Человек", icon: refSvg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>') },
  { kind: "reminder", label: "Напоминание", icon: refSvg('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>') },
];
var NOTE_REF_CHAT_ICON_SVG = refSvg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>');
var NOTE_REF_SEARCH_ICON_SVG = refSvg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>');
/** Сущности, которые хранятся в чипе как data-leo-ref="kind:id" (не заметки/листы). */
var NOTE_REF_ENTITY_RE = /^(task|reminder|person|chat):(.{1,160})$/;

function noteRefKindMeta(kind) {
  for (var i = 0; i < NOTE_REF_KINDS.length; i++) {
    if (NOTE_REF_KINDS[i].kind === kind) return NOTE_REF_KINDS[i];
  }
  return null;
}

function noteRefIconFor(refKind) {
  if (refKind === "chat") return NOTE_REF_CHAT_ICON_SVG;
  var meta = refKind ? noteRefKindMeta(refKind) : null;
  return meta ? meta.icon : NOTE_REF_CHIP_ICON_SVG;
}

function normalizeNoteRefLabel(label, refKind) {
  var fallback = refKind && noteRefKindMeta(refKind) ? noteRefKindMeta(refKind).label : "Заметка";
  return String(label || "").replace(/\s+/g, " ").trim() || fallback;
}

function noteRefLabelFromEl(el) {
  if (!el) return "";
  var named = el.querySelector && el.querySelector(".note-ref-chip-label");
  return String((named ? named.textContent : el.textContent) || "").replace(/\s+/g, " ").trim();
}

function parseEntityRef(raw) {
  var m = String(raw || "").match(NOTE_REF_ENTITY_RE);
  return m ? { refKind: m[1], refId: m[2] } : null;
}

function fillNoteRefChipEl(el, attrs) {
  var refKind = String(attrs.refKind || "");
  el.className = "note-ref-chip" + (refKind ? " note-ref-chip--" + refKind : "");
  if (refKind) {
    el.setAttribute("data-leo-ref", refKind + ":" + String(attrs.refId || ""));
    el.removeAttribute("data-leo-note-id");
    el.removeAttribute("data-leo-sheet-id");
  } else {
    el.removeAttribute("data-leo-ref");
    el.setAttribute("data-leo-note-id", attrs.noteId || "");
    if (attrs.sheetId) el.setAttribute("data-leo-sheet-id", attrs.sheetId);
    else el.removeAttribute("data-leo-sheet-id");
  }
  el.setAttribute("contenteditable", "false");
  el.innerHTML =
    '<span class="note-ref-chip-icon" aria-hidden="true">' +
    noteRefIconFor(refKind) +
    '</span><span class="note-ref-chip-label"></span>';
  el.querySelector(".note-ref-chip-label").textContent = normalizeNoteRefLabel(attrs.label, refKind);
}

function createNoteRefExtensions(ctx) {
  var LeoNoteRefChip = Node.create({
    name: "leoNoteRefChip",
    group: "inline",
    inline: true,
    atom: true,
    selectable: true,
    draggable: false,
    addAttributes: function () {
      return {
        noteId: { default: "" },
        sheetId: { default: "" },
        refKind: { default: "" },
        refId: { default: "" },
        label: { default: "" },
      };
    },
    parseHTML: function () {
      return [
        {
          tag: "span[data-leo-ref]",
          getAttrs: function (el) {
            var ref = parseEntityRef(el.getAttribute("data-leo-ref"));
            if (!ref) return false;
            return {
              noteId: "",
              sheetId: "",
              refKind: ref.refKind,
              refId: ref.refId,
              label: noteRefLabelFromEl(el),
            };
          },
        },
        {
          tag: "span[data-leo-note-id]",
          getAttrs: function (el) {
            return {
              noteId: el.getAttribute("data-leo-note-id") || "",
              sheetId: el.getAttribute("data-leo-sheet-id") || "",
              refKind: "",
              refId: "",
              label: noteRefLabelFromEl(el),
            };
          },
        },
      ];
    },
    renderHTML: function (_ref) {
      var HTMLAttributes = _ref.HTMLAttributes;
      var refKind = String(HTMLAttributes.refKind || "");
      var attrs = { contenteditable: "false" };
      if (refKind) {
        attrs["data-leo-ref"] = refKind + ":" + String(HTMLAttributes.refId || "");
        attrs.class = "note-ref-chip note-ref-chip--" + refKind;
      } else {
        attrs["data-leo-note-id"] = HTMLAttributes.noteId;
        attrs.class = "note-ref-chip";
        if (HTMLAttributes.sheetId) attrs["data-leo-sheet-id"] = HTMLAttributes.sheetId;
      }
      return [
        "span",
        attrs,
        ["span", { class: "note-ref-chip-icon", "aria-hidden": "true" }],
        ["span", { class: "note-ref-chip-label" }, normalizeNoteRefLabel(HTMLAttributes.label, refKind)],
      ];
    },
    addNodeView: function () {
      return function (_ref) {
        var node = _ref.node;
        var el = document.createElement("span");
        fillNoteRefChipEl(el, node.attrs);
        el.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          var ref = parseEntityRef(el.getAttribute("data-leo-ref"));
          if (ref) {
            if (ref.refKind === "chat") {
              if (typeof ctx.onNoteRefChipClick === "function") ctx.onNoteRefChipClick("", "", ref.refId);
            } else if (typeof ctx.onEntityRefChipClick === "function") {
              ctx.onEntityRefChipClick(ref.refKind, ref.refId, noteRefLabelFromEl(el));
            }
            return;
          }
          var nid = el.getAttribute("data-leo-note-id") || "";
          var sid = el.getAttribute("data-leo-sheet-id") || "";
          if (nid && typeof ctx.onNoteRefChipClick === "function") {
            ctx.onNoteRefChipClick(nid, sid);
          }
        });
        return {
          dom: el,
          update: function (updated) {
            if (!updated || updated.type.name !== "leoNoteRefChip") return false;
            fillNoteRefChipEl(el, updated.attrs);
            return true;
          },
        };
      };
    },
  });

  var LeoNoteRefPlus = Extension.create({
    name: "leoNoteRefPlus",
    addProseMirrorPlugins: function () {
      return [
        new Plugin({
          key: new PluginKey("leoNoteRefPlus"),
          view: function () {
            var el = document.createElement("div");
            el.className = "note-ref-plus-suggest hidden";
            document.body.appendChild(el);
            ctx.refSuggestEl = el;
            ctx.refKind = "";
            ctx.refQuery = "";
            var searchGen = 0;

            function hideSuggest() {
              searchGen += 1;
              el.classList.add("hidden");
              el.innerHTML = "";
              ctx.refItems = [];
              ctx.refIndex = 0;
              ctx.refFrom = null;
              ctx.refTo = null;
              ctx.refKind = "";
              ctx.refQuery = "";
              ctx.refLoading = false;
              ctx.refItemsKind = null;
            }
            ctx.hideNoteRefSuggest = hideSuggest;

            function insertChip(item) {
              var view = ctx.editor && ctx.editor.view;
              var isEntity = !!(item && item.refKind && item.refId);
              if (!view || !item || (!isEntity && !item.noteId)) {
                hideSuggest();
                return;
              }
              var from = ctx.refFrom;
              var to = view.state.selection.from;
              if (from == null || from < 0 || to < from) {
                hideSuggest();
                return;
              }
              hideSuggest();
              view.dispatch(view.state.tr.delete(from, to));
              var attrs = isEntity
                ? {
                    noteId: "",
                    sheetId: "",
                    refKind: String(item.refKind),
                    refId: String(item.refId),
                    label: normalizeNoteRefLabel(item.label, item.refKind),
                  }
                : {
                    noteId: String(item.noteId || ""),
                    sheetId: String(item.sheetId || ""),
                    refKind: "",
                    refId: "",
                    label: normalizeNoteRefLabel(item.label),
                  };
              ctx.editor
                .chain()
                .focus()
                .insertContent([
                  { type: "leoNoteRefChip", attrs: attrs },
                  { type: "text", text: " " },
                ])
                .run();
            }

            function setKind(kind) {
              ctx.refKind = kind || "";
              ctx.refIndex = 0;
              var view = ctx.editor && ctx.editor.view;
              if (view) runSearch(view);
            }

            function pick(row) {
              if (!row) return;
              if (row.pickKind) setKind(row.pickKind);
              else insertChip(row);
            }
            ctx.pickNoteRef = pick;
            ctx.setNoteRefKind = setKind;

            function rowButton(row, i) {
              var btn = document.createElement("button");
              btn.type = "button";
              btn.className =
                "note-ref-suggest-item" +
                (row.pickKind ? " note-ref-suggest-item--kind" : "") +
                (i === ctx.refIndex ? " is-active" : "");
              var icon = document.createElement("span");
              icon.className = "note-ref-suggest-icon";
              icon.innerHTML = noteRefIconFor(row.pickKind || row.refKind || (row.chatId ? "chat" : ""));
              btn.appendChild(icon);
              var copy = document.createElement("span");
              copy.className = "note-ref-suggest-copy";
              var title = document.createElement("span");
              title.className = "note-ref-suggest-label";
              title.textContent = row.label || "Заметка";
              copy.appendChild(title);
              if (row.hint) {
                var hint = document.createElement("span");
                hint.className = "note-ref-suggest-hint";
                hint.textContent = row.hint;
                copy.appendChild(hint);
              }
              btn.appendChild(copy);
              btn.addEventListener("mousedown", function (e) {
                e.preventDefault();
                pick(row);
              });
              return btn;
            }

            function render() {
              el.innerHTML = "";
              var head = document.createElement("div");
              head.className = "note-ref-suggest-search";
              var sIcon = document.createElement("span");
              sIcon.className = "note-ref-suggest-search-icon";
              sIcon.innerHTML = NOTE_REF_SEARCH_ICON_SVG;
              head.appendChild(sIcon);
              var meta = ctx.refKind ? noteRefKindMeta(ctx.refKind) : null;
              if (meta) {
                var chip = document.createElement("button");
                chip.type = "button";
                chip.className = "note-ref-suggest-kind-chip";
                chip.setAttribute("aria-label", "Убрать фильтр «" + meta.label + "»");
                chip.innerHTML = meta.icon + "<span></span>" + refSvg('<path d="M18 6 6 18M6 6l12 12"/>');
                chip.querySelector("span").textContent = meta.label;
                chip.addEventListener("mousedown", function (e) {
                  e.preventDefault();
                  setKind("");
                });
                head.appendChild(chip);
              }
              var q = document.createElement("span");
              q.className = "note-ref-suggest-query" + (ctx.refQuery ? "" : " is-placeholder");
              q.textContent = ctx.refQuery || (meta ? "Поиск" : "Поиск: задача, заметка, человек…");
              head.appendChild(q);
              el.appendChild(head);

              var rows = ctx.refItems || [];
              if (!rows.length) {
                var empty = document.createElement("div");
                empty.className = "note-ref-suggest-empty";
                empty.textContent = ctx.refLoading ? "Ищу…" : "Ничего не найдено";
                el.appendChild(empty);
                return;
              }
              var list = document.createElement("div");
              list.className = "note-ref-suggest-list";
              rows.forEach(function (row, i) {
                list.appendChild(rowButton(row, i));
              });
              el.appendChild(list);
              var active = list.querySelector(".is-active");
              if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
            }
            ctx.renderNoteRefSuggest = render;

            function place(view, pos) {
              var coords = view.coordsAtPos(pos);
              el.classList.remove("hidden");
              var w = el.offsetWidth || 280;
              var left = Math.min(Math.max(8, coords.left), Math.max(8, window.innerWidth - w - 8));
              el.style.left = left + "px";
              var h = el.offsetHeight || 0;
              var vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
              var top = coords.bottom + 6;
              if (h && top + h > vh - 8 && coords.top - h - 6 > 8) top = coords.top - h - 6;
              el.style.top = top + "px";
            }

            function kindRows(query) {
              var q = String(query || "").trim().toLowerCase();
              return NOTE_REF_KINDS.filter(function (k) {
                return !q || k.label.toLowerCase().indexOf(q) === 0;
              }).map(function (k) {
                return { pickKind: k.kind, label: k.label };
              });
            }

            function runSearch(view) {
              var $from = view.state.selection.$from;
              var text = $from.parent.textBetween(0, $from.parentOffset, null, "￼");
              var match = text.match(NOTE_REF_QUERY_RE);
              if (!match) {
                hideSuggest();
                return;
              }
              var token = String(match[1] || "");
              var query = String(match[2] || "");
              ctx.refFrom = $from.pos - token.length;
              ctx.refTo = $from.pos;
              if (query !== ctx.refQuery) ctx.refIndex = 0;
              ctx.refQuery = query;
              var kind = ctx.refKind;
              // Без типа и без запроса — только пункты-типы, искать нечего.
              var typeRows = kind ? [] : kindRows(query);
              var gen = ++searchGen;
              if ((!kind && !query.trim()) || typeof ctx.onSearchNoteRefs !== "function") {
                ctx.refLoading = false;
                ctx.refItems = typeRows;
                ctx.refItemsKind = null;
                render();
                place(view, ctx.refFrom);
                return;
              }
              ctx.refLoading = true;
              // Пока ищем, показываем прошлые результаты того же типа — без мигания «Ищу…».
              if (ctx.refItemsKind !== kind) ctx.refItems = typeRows;
              render();
              place(view, ctx.refFrom);
              ctx.onSearchNoteRefs(
                query,
                function (items) {
                  if (gen !== searchGen) return;
                  if (!ctx.editor || !ctx.editor.view) return;
                  var sel = ctx.editor.view.state.selection;
                  if (!sel.empty) return;
                  var $now = sel.$from;
                  var nowText = $now.parent.textBetween(0, $now.parentOffset, null, "￼");
                  if (!NOTE_REF_QUERY_RE.test(nowText)) return;
                  items = Array.isArray(items) ? items.slice(0, 20) : [];
                  ctx.refLoading = false;
                  ctx.refItems = typeRows.concat(items);
                  ctx.refItemsKind = kind;
                  if (ctx.refIndex >= ctx.refItems.length) ctx.refIndex = 0;
                  render();
                  place(ctx.editor.view, ctx.refFrom);
                },
                kind
              );
            }

            return {
              update: function (view, prevState) {
                if (!view.state.selection.empty) {
                  hideSuggest();
                  return;
                }
                if (
                  prevState &&
                  prevState.doc.eq(view.state.doc) &&
                  prevState.selection.eq(view.state.selection)
                ) {
                  return;
                }
                runSearch(view);
              },
              destroy: function () {
                searchGen += 1;
                if (el.parentNode) el.parentNode.removeChild(el);
              },
            };
          },
          props: {
            handleKeyDown: function (view, event) {
              var open = ctx.refSuggestEl && !ctx.refSuggestEl.classList.contains("hidden");
              if (!open) return false;
              var items = ctx.refItems || [];
              if (event.key === "Backspace" && ctx.refKind && !ctx.refQuery) {
                // Сначала снимаем фильтр-чип, потом уже стираем «+».
                event.preventDefault();
                if (ctx.setNoteRefKind) ctx.setNoteRefKind("");
                return true;
              }
              if (event.key === "Escape") {
                if (ctx.hideNoteRefSuggest) ctx.hideNoteRefSuggest();
                return true;
              }
              if (!items.length) return false;
              if (event.key === "ArrowDown") {
                event.preventDefault();
                ctx.refIndex = (ctx.refIndex + 1) % items.length;
                if (ctx.renderNoteRefSuggest) ctx.renderNoteRefSuggest();
                return true;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                ctx.refIndex = (ctx.refIndex - 1 + items.length) % items.length;
                if (ctx.renderNoteRefSuggest) ctx.renderNoteRefSuggest();
                return true;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                event.preventDefault();
                if (ctx.pickNoteRef) ctx.pickNoteRef(items[ctx.refIndex]);
                return true;
              }
              return false;
            },
          },
        }),
      ];
    },
  });

  return [LeoNoteRefChip, LeoNoteRefPlus];
}
var TASK_SLASH_DAYS = [
  "сегодня",
  "завтра",
  "послезавтра",
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
  "воскресенье",
];
var TASK_SLASH_DAY =
  "сегодня|завтра|послезавтра|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|пн|вт|ср|чт|пт|сб|вс";
var TASK_SLASH_MONTHS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
var TASK_SLASH_MONTH =
  "янв\\w*|фев\\w*|мар\\w*|апр\\w*|ма[йя]\\w*|июн\\w*|июл\\w*|авг\\w*|сен\\w*|окт\\w*|ноя\\w*|дек\\w*";
var TASK_SLASH_WHEN =
  "(?:" +
  TASK_SLASH_DAY +
  "|\\d{1,2}[./]\\d{1,2}(?:[./]\\d{2,4})?" +
  "|\\d{1,2}\\s+(?:" +
  TASK_SLASH_MONTH +
  "))";
var TASK_SLASH_LEAD = "(?:^|[\\s\\uFFFC]|[^:/\\s])";
var TASK_SLASH_QUERY_RE = new RegExp(TASK_SLASH_LEAD + "(\\/([^\\n]*))$");
var TASK_SLASH_COMPLETE_RE = new RegExp(
  "^/(" + TASK_SLASH_WHEN + ")(?:\\s+\\d{1,2}[:.]\\d{2})?(?:\\s+[^\\n/]+)?$",
  "i"
);
var TASK_SLASH_TRAIL_RE = new RegExp(
  TASK_SLASH_LEAD + "(/(" + TASK_SLASH_WHEN + ")(?:\\s+\\d{1,2}[:.]\\d{2})(?:\\s+[^\\n/]+)?\\s)$",
  "i"
);
var TASK_SLASH_ENTER_RE = new RegExp(
  TASK_SLASH_LEAD + "(/(" + TASK_SLASH_WHEN + ")(?:\\s+\\d{1,2}[:.]\\d{2})?(?:\\s+[^\\n/]+)?)$",
  "i"
);

var TASK_CHIP_ICON_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>' +
  "</svg>";

function normalizeTaskChipLabel(label) {
  var s = String(label || "").trim();
  if (/^Задача\s+/i.test(s)) s = s.replace(/^Задача\s+/i, "").trim();
  return s;
}

function taskChipLabelFromEl(el) {
  if (!el) return "";
  var named = el.querySelector && el.querySelector(".note-task-chip-label");
  return normalizeTaskChipLabel(named ? named.textContent : el.textContent);
}

// Задача во внешнем трекере (Битрикс24…): чип открывает её там, а не задачу Орбиты.
var TASK_CHIP_PROVIDER_SHORT = { bitrix24: "Б24" };
var TASK_CHIP_EXT_ICON_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>' +
  "</svg>";

function taskChipProviderShort(provider) {
  return TASK_CHIP_PROVIDER_SHORT[provider] || "";
}

function fillTaskChipEl(el, taskId, label, provider, url) {
  var ext = !!provider;
  el.className = "note-task-chip" + (ext ? " note-task-chip--ext" : "");
  el.setAttribute("data-leo-task-id", taskId || "");
  if (ext) {
    el.setAttribute("data-leo-task-provider", provider);
    el.setAttribute("data-leo-task-url", url || "");
    el.title = "Открыть в " + (provider === "bitrix24" ? "Битрикс24" : provider);
  } else {
    el.removeAttribute("data-leo-task-provider");
    el.removeAttribute("data-leo-task-url");
    el.removeAttribute("title");
  }
  el.setAttribute("contenteditable", "false");
  el.innerHTML =
    '<span class="note-task-chip-icon" aria-hidden="true">' +
    (ext ? TASK_CHIP_EXT_ICON_SVG : TASK_CHIP_ICON_SVG) +
    "</span>" +
    (ext && taskChipProviderShort(provider) ? '<span class="note-task-chip-provider"></span>' : "") +
    '<span class="note-task-chip-label"></span>';
  var short = el.querySelector(".note-task-chip-provider");
  if (short) short.textContent = taskChipProviderShort(provider) + " ·";
  el.querySelector(".note-task-chip-label").textContent = normalizeTaskChipLabel(label);
}

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

// Текст значка: чип задачи — её название; «не задача», «дубль», смена типа — пометка; иначе ✓ / «Задача».
function focusMarkText(a) {
  if (a.mark === "task" || a.mark === "dismissed" || a.mark === "retyped") return a.label || (a.mark === "task" ? "Задача" : "");
  return a.mark === "linked" ? "Задача" : "✓";
}

function focusMarkTitle(a) {
  var head =
    a.mark === "task"
      ? (a.done ? "Задача выполнена" : "Поставлена задача")
      : a.mark === "dismissed"
        ? "Скрыто: " + (a.label || "")
        : a.mark === "retyped"
          ? "Тип изменён: " + (a.label || "")
          : a.mark === "linked"
            ? "Связано с задачей"
            : "Выполнено";
  var when = "";
  var d = new Date(String(a.at || ""));
  if (!isNaN(d.getTime())) {
    when =
      d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) +
      ", " +
      d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  return [head, a.by, when, "из «Фокуса»"].filter(Boolean).join(" · ");
}

function taskChipAttrs(taskId, label, ext) {
  ext = ext || {};
  return {
    taskId: String(taskId || ""),
    label: normalizeTaskChipLabel(label),
    provider: String(ext.provider || ""),
    url: String(ext.url || ""),
  };
}

function collectLeoTaskIds(doc) {
  var ids = new Set();
  if (!doc) return ids;
  doc.descendants(function (node) {
    // Внешние задачи (Битрикс24…) Орбита не удаляет — их не отслеживаем.
    if (node.type && node.type.name === "leoTaskChip" && node.attrs && node.attrs.taskId && !node.attrs.provider) {
      ids.add(String(node.attrs.taskId));
    }
  });
  return ids;
}

function createLeoTaskExtensions(ctx) {
  var LeoTaskChip = Node.create({
    name: "leoTaskChip",
    group: "inline",
    inline: true,
    atom: true,
    selectable: true,
    draggable: false,
    addAttributes: function () {
      return {
        taskId: { default: "" },
        label: { default: "" },
        provider: { default: "" },
        url: { default: "" },
      };
    },
    parseHTML: function () {
      return [
        {
          tag: "span[data-leo-task-id]",
          getAttrs: function (el) {
            return {
              taskId: el.getAttribute("data-leo-task-id") || "",
              label: taskChipLabelFromEl(el),
              provider: el.getAttribute("data-leo-task-provider") || "",
              url: el.getAttribute("data-leo-task-url") || "",
            };
          },
        },
      ];
    },
    renderHTML: function (_ref) {
      var HTMLAttributes = _ref.HTMLAttributes;
      var attrs = {
        "data-leo-task-id": HTMLAttributes.taskId,
        class: "note-task-chip",
        contenteditable: "false",
      };
      if (HTMLAttributes.provider) {
        attrs["data-leo-task-provider"] = HTMLAttributes.provider;
        attrs["data-leo-task-url"] = HTMLAttributes.url || "";
        attrs.class = "note-task-chip note-task-chip--ext";
      }
      var short = HTMLAttributes.provider ? taskChipProviderShort(HTMLAttributes.provider) : "";
      return [
        "span",
        attrs,
        ["span", { class: "note-task-chip-icon", "aria-hidden": "true" }],
      ]
        .concat(short ? [["span", { class: "note-task-chip-provider" }, short + " ·"]] : [])
        .concat([["span", { class: "note-task-chip-label" }, normalizeTaskChipLabel(HTMLAttributes.label)]]);
    },
    addNodeView: function () {
      return function (_ref) {
        var node = _ref.node;
        var el = document.createElement("span");
        fillTaskChipEl(el, node.attrs.taskId, node.attrs.label, node.attrs.provider, node.attrs.url);
        el.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          var url = el.getAttribute("data-leo-task-url") || "";
          if (el.getAttribute("data-leo-task-provider")) {
            if (url && typeof ctx.onExternalTaskClick === "function") ctx.onExternalTaskClick(url);
            else if (url) window.open(url, "_blank", "noopener");
            return;
          }
          var id = el.getAttribute("data-leo-task-id") || "";
          if (id && typeof ctx.onTaskChipClick === "function") ctx.onTaskChipClick(id);
        });
        return {
          dom: el,
          update: function (updated) {
            if (!updated || updated.type.name !== "leoTaskChip") return false;
            fillTaskChipEl(el, updated.attrs.taskId, updated.attrs.label, updated.attrs.provider, updated.attrs.url);
            return true;
          },
        };
      };
    },
  });

  var LeoTaskSlash = Extension.create({
    name: "leoTaskSlash",
    addProseMirrorPlugins: function () {
      return [
        new Plugin({
          key: new PluginKey("leoTaskSlash"),
          view: function () {
            var el = document.createElement("div");
            el.className = "note-task-slash-suggest note-hashtag-suggest hidden";
            document.body.appendChild(el);
            ctx.slashSuggestEl = el;

            function hideSuggest() {
              el.classList.add("hidden");
              el.innerHTML = "";
              ctx.slashItems = [];
              ctx.slashIndex = 0;
              ctx.slashFrom = null;
            }
            ctx.hideTaskSlashSuggest = hideSuggest;

            function commitToken(view, token, from, to, title) {
              if (!view || ctx.slashBusy || !token) return false;
              if (from < 0 || to < from) return false;
              ctx.slashBusy = true;
              hideSuggest();
              view.dispatch(view.state.tr.delete(from, to));
              if (typeof ctx.onTaskSlash !== "function") {
                ctx.slashBusy = false;
                return true;
              }
              ctx.onTaskSlash(
                token,
                function (taskId, label) {
                  ctx.slashBusy = false;
                  if (!ctx.editor) return;
                  ctx.editor
                    .chain()
                    .focus()
                    .insertContent([
                      {
                        type: "leoTaskChip",
                        attrs: {
                          taskId: String(taskId || ""),
                          label: normalizeTaskChipLabel(label),
                        },
                      },
                      { type: "text", text: " " },
                    ])
                    .run();
                },
                function () {
                  ctx.slashBusy = false;
                },
                title
              );
              return true;
            }

            function commit(view, re) {
              if (!view || ctx.slashBusy) return false;
              var $from = view.state.selection.$from;
              var text = $from.parent.textBetween(0, $from.parentOffset, null, "\ufffc");
              var match = text.match(re);
              if (!match) return false;
              var rawToken = String(match[1] || "");
              var token = rawToken.trim();
              if (!token) return false;
              var trail = rawToken.length - token.length;
              if (trail <= 0 && text.endsWith(" ")) trail = 1;
              var from = $from.pos - token.length - trail;
              var to = $from.pos;
              if (from < $from.start() || to > $from.end() || from >= to) return false;
              return commitToken(view, token, from, to, paragraphTitleBeforeSlash(text));
            }
            ctx.commitTaskSlash = commit;

            function pickDay(name) {
              var view = ctx.editor && ctx.editor.view;
              if (!view || ctx.slashFrom == null) {
                hideSuggest();
                return;
              }
              var to = view.state.selection.from;
              view.dispatch(view.state.tr.insertText("/" + name + " ", ctx.slashFrom, to));
              view.focus();
            }

            function pickCreate() {
              var view = ctx.editor && ctx.editor.view;
              if (!view) return;
              commit(view, TASK_SLASH_ENTER_RE);
            }

            function render() {
              el.innerHTML = "";
              (ctx.slashItems || []).forEach(function (item, i) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className =
                  "note-hashtag-suggest-item" + (i === ctx.slashIndex ? " is-active" : "");
                btn.textContent = item.label;
                btn.addEventListener("mousedown", function (e) {
                  e.preventDefault();
                  if (item.kind === "create") pickCreate();
                  else if (item.kind === "day" && item.name) pickDay(item.name);
                });
                el.appendChild(btn);
              });
            }
            ctx.renderTaskSlashSuggest = render;

            return {
              update: function (view) {
                if (commit(view, TASK_SLASH_TRAIL_RE)) return;
                if (!view.state.selection.empty) {
                  hideSuggest();
                  return;
                }
                var $from = view.state.selection.$from;
                var text = $from.parent.textBetween(0, $from.parentOffset, null, "\ufffc");
                var match = text.match(TASK_SLASH_QUERY_RE);
                if (!match) {
                  hideSuggest();
                  return;
                }
                var token = String(match[1] || "");
                var rest = String(match[2] || "").trim().toLowerCase();
                ctx.slashFrom = $from.pos - token.length;
                var items = [];
                if (TASK_SLASH_COMPLETE_RE.test(token.trim())) {
                  items.push({
                    kind: "create",
                    label: rest ? "Создать задачу · /" + rest : "Создать задачу",
                  });
                }
                var datePrefix = rest.match(/^(\d{1,2})(?:\s+([а-яё]*))?$/i);
                var dottedDate = /^\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?:\s|$)/.test(rest);
                if (datePrefix && !dottedDate) {
                  var dayNum = datePrefix[1];
                  var monQ = String(datePrefix[2] || "").toLowerCase();
                  TASK_SLASH_MONTHS.forEach(function (name) {
                    if (!monQ || name.indexOf(monQ) === 0) {
                      items.push({
                        kind: "day",
                        name: dayNum + " " + name,
                        label: "/" + dayNum + " " + name,
                      });
                    }
                  });
                } else if (!rest || !/^\d/.test(rest)) {
                  TASK_SLASH_DAYS.forEach(function (name) {
                    if (!rest || name.indexOf(rest.split(/\s+/)[0]) === 0) {
                      items.push({ kind: "day", name: name, label: "/" + name });
                    }
                  });
                }
                if (!items.length && /^\d/.test(rest)) {
                  items.push({
                    kind: "hint",
                    label: "Например: /7 окт 15:00 или /7.10 15:00",
                  });
                }
                if (!items.length) {
                  hideSuggest();
                  return;
                }
                ctx.slashItems = items.slice(0, 9);
                ctx.slashIndex = 0;
                render();
                var coords = view.coordsAtPos($from.pos);
                el.style.position = "fixed";
                el.style.zIndex = "520";
                el.style.left = Math.max(8, coords.left) + "px";
                el.style.top = coords.bottom + 6 + "px";
                el.classList.remove("hidden");
              },
              destroy: function () {
                if (el.parentNode) el.parentNode.removeChild(el);
              },
            };
          },
          props: {
            handleKeyDown: function (view, event) {
              var items = ctx.slashItems || [];
              var open =
                items.length &&
                ctx.slashSuggestEl &&
                !ctx.slashSuggestEl.classList.contains("hidden");
              if (open && event.key === "ArrowDown") {
                event.preventDefault();
                ctx.slashIndex = (ctx.slashIndex + 1) % items.length;
                if (ctx.renderTaskSlashSuggest) ctx.renderTaskSlashSuggest();
                return true;
              }
              if (open && event.key === "ArrowUp") {
                event.preventDefault();
                ctx.slashIndex = (ctx.slashIndex - 1 + items.length) % items.length;
                if (ctx.renderTaskSlashSuggest) ctx.renderTaskSlashSuggest();
                return true;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                if (open) {
                  event.preventDefault();
                  var item = items[ctx.slashIndex];
                  if (item && item.kind === "create") {
                    if (ctx.commitTaskSlash) ctx.commitTaskSlash(view, TASK_SLASH_ENTER_RE);
                  } else if (item && item.kind === "day" && item.name) {
                    var to = view.state.selection.from;
                    view.dispatch(view.state.tr.insertText("/" + item.name + " ", ctx.slashFrom, to));
                    view.focus();
                  }
                  return true;
                }
                if (ctx.commitTaskSlash && ctx.commitTaskSlash(view, TASK_SLASH_ENTER_RE)) {
                  event.preventDefault();
                  return true;
                }
              }
              if (event.key === "Escape" && open) {
                if (ctx.hideTaskSlashSuggest) ctx.hideTaskSlashSuggest();
                return true;
              }
              return false;
            },
          },
        }),
        new Plugin({
          key: new PluginKey("leoTaskChipWatch"),
          appendTransaction: function (transactions, oldState, newState) {
            if (ctx.suppressChipDelete) return null;
            if (!transactions.some(function (tr) { return tr.docChanged; })) return null;
            var fromUi = transactions.some(function (tr) {
              var ev = tr.getMeta("uiEvent");
              return ev === "key" || ev === "delete" || ev === "cut";
            });
            if (!fromUi) return null;
            var oldIds = collectLeoTaskIds(oldState.doc);
            var newIds = collectLeoTaskIds(newState.doc);
            oldIds.forEach(function (id) {
              if (!newIds.has(id) && typeof ctx.onTaskChipRemoved === "function") {
                ctx.onTaskChipRemoved(id);
              }
            });
            return null;
          },
        }),
      ];
    },
  });

  // Значок разбора пункта из «Фокуса» (✓ выполнено / «Задача» — ссылка) в конце строки.
  var LeoFocusMark = Node.create({
    name: "leoFocusMark",
    group: "inline",
    inline: true,
    atom: true,
    selectable: true,
    draggable: false,
    addAttributes: function () {
      return {
        mark: { default: "done" },
        item: { default: "" },
        by: { default: "" },
        at: { default: "" },
        url: { default: "" },
        task: { default: "" },
        label: { default: "" },
        done: { default: "" },
      };
    },
    parseHTML: function () {
      return [
        {
          tag: "span.note-focus-mark",
          getAttrs: function (el) {
            return {
              mark: el.getAttribute("data-leo-mark") || "done",
              item: el.getAttribute("data-leo-mark-item") || "",
              by: el.getAttribute("data-leo-mark-by") || "",
              at: el.getAttribute("data-leo-mark-at") || "",
              url: el.getAttribute("data-leo-mark-url") || "",
              task: el.getAttribute("data-leo-mark-task") || "",
              label: el.getAttribute("data-leo-mark-label") || "",
              done: el.getAttribute("data-leo-mark-done") || "",
            };
          },
        },
      ];
    },
    renderHTML: function (_ref) {
      var a = _ref.node.attrs;
      var attrs = { class: "note-focus-mark", "data-leo-mark": a.mark, contenteditable: "false" };
      ["item", "by", "at", "url", "task", "label", "done"].forEach(function (k) {
        if (a[k]) attrs["data-leo-mark-" + k] = a[k];
      });
      return ["span", attrs, focusMarkText(a)];
    },
    addNodeView: function () {
      return function (_ref) {
        var el = document.createElement("span");
        function fill(node) {
          var a = node.attrs;
          el.className = "note-focus-mark";
          el.setAttribute("contenteditable", "false");
          el.setAttribute("data-leo-mark", a.mark);
          if (a.done) el.setAttribute("data-leo-mark-done", a.done);
          else el.removeAttribute("data-leo-mark-done");
          if (a.task) el.setAttribute("data-leo-mark-task", a.task);
          el.textContent = focusMarkText(a);
          el.title = focusMarkTitle(a);
        }
        fill(_ref.node);
        el.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          var a = _ref.node.attrs;
          if (typeof ctx.onFocusMarkClick === "function") ctx.onFocusMarkClick(Object.assign({ title: focusMarkTitle(a) }, a));
        });
        return {
          dom: el,
          update: function (updated) {
            if (!updated || updated.type.name !== "leoFocusMark") return false;
            _ref.node = updated;
            fill(updated);
            return true;
          },
        };
      };
    },
  });

  return [LeoTaskChip, LeoTaskSlash, LeoFocusMark];
}

/** Таблица задач встречи («Задача | Статус | Ответственный | Дедлайн»). Строка — пункт, если ID её первой
 * ячейки есть в состоянии (setMeetingTasks): статус, ответственный и срок рисуются поверх текста ячеек
 * (текст ведёт сервер, ячейки не редактируются руками), над таблицей — «Разобрано N из M»,
 * «Не задачи» и «Дубли» свёрнуты под таблицей. Клик по ячейке — ctx.onMeetingTaskClick. */
var meetingTasksKey = new PluginKey("leoMeetingTasks");
var MT_EXCLUDED = { not_task: 1, duplicate: 1, doubtful: 1 };
var MT_UNDECIDED = { open: 1, declined: 1 };

function createMeetingTasksExtension(ctx) {
  function fire(detail, e) {
    e.preventDefault();
    e.stopPropagation();
    if (typeof ctx.onMeetingTaskClick === "function") ctx.onMeetingTaskClick(Object.assign({ anchor: e.currentTarget }, detail));
  }
  function cellWidget(item, col) {
    return function () {
      var el = document.createElement("span");
      var text = col === 1 ? item.label : col === 2 ? item.who : item.due;
      el.className = "mt-val" + (col === 1 ? " mt-chip mt-chip--" + item.code : "") + (text ? "" : " is-empty");
      el.setAttribute("contenteditable", "false");
      el.setAttribute("role", "button");
      el.textContent = text || (col === 2 && !MT_EXCLUDED[item.code] ? "выбрать…" : "—");
      if (col === 1 && item.note) el.title = item.note;
      el.addEventListener("mousedown", function (e) {
        e.preventDefault();
      });
      el.addEventListener("click", function (e) {
        fire({ item: item.id, col: col, state: item }, e);
      });
      return el;
    };
  }
  function counterWidget(decided, total) {
    return function () {
      var el = document.createElement("div");
      el.className = "mt-summary";
      el.setAttribute("contenteditable", "false");
      var bar = document.createElement("span");
      bar.className = "mt-summary-bar";
      var fill = document.createElement("span");
      fill.style.width = (total ? Math.round((100 * decided) / total) : 100) + "%";
      bar.appendChild(fill);
      var txt = document.createElement("span");
      txt.className = "mt-summary-text";
      txt.textContent = total && decided >= total ? "Всё разобрано · " + total : "Разобрано " + decided + " из " + total;
      el.appendChild(bar);
      el.appendChild(txt);
      return el;
    };
  }
  function tailWidget(n, open) {
    return function (view) {
      var el = document.createElement("button");
      el.type = "button";
      el.className = "mt-excluded-toggle" + (open ? " is-open" : "");
      el.setAttribute("contenteditable", "false");
      el.textContent = (open ? "Скрыть: " : "") + "Не задачи и сомнительные · " + n;
      el.addEventListener("mousedown", function (e) {
        e.preventDefault();
      });
      el.addEventListener("click", function (e) {
        e.preventDefault();
        view.dispatch(view.state.tr.setMeta(meetingTasksKey, { open: !open }).setMeta("addToHistory", false));
      });
      return el;
    };
  }
  var stop = function () {
    return true;
  };
  return Extension.create({
    name: "leoMeetingTasks",
    addProseMirrorPlugins: function () {
      return [
        new Plugin({
          key: meetingTasksKey,
          state: {
            init: function () {
              return { items: null, open: false };
            },
            apply: function (tr, v) {
              var m = tr.getMeta(meetingTasksKey);
              return m ? Object.assign({}, v, m) : v;
            },
          },
          props: {
            decorations: function (state) {
              var st = meetingTasksKey.getState(state);
              if (!st || !st.items) return null;
              var decos = [];
              state.doc.descendants(function (node, pos) {
                if (node.type.name !== "table") return true;
                var rows = [];
                node.forEach(function (row, off) {
                  var first = row.firstChild;
                  var id = first && first.attrs && first.attrs.leoItem;
                  rows.push({ pos: pos + 1 + off, node: row, item: id ? st.items[id] : null, head: !!(first && first.type.name === "tableHeader") });
                });
                if (!rows.some(function (r) { return r.item; })) return false;
                var decided = 0;
                var total = 0;
                var excluded = 0;
                rows.forEach(function (r) {
                  if (!r.item) {
                    if (r.head) decos.push(Decoration.node(r.pos, r.pos + r.node.nodeSize, { class: "mt-head" }));
                    return;
                  }
                  var it = r.item;
                  var ex = !!MT_EXCLUDED[it.code];
                  if (ex) excluded++;
                  else {
                    total++;
                    if (!MT_UNDECIDED[it.code]) decided++;
                  }
                  decos.push(
                    Decoration.node(r.pos, r.pos + r.node.nodeSize, {
                      class: "mt-row mt-row--" + it.code + (ex ? " is-excluded" + (st.open ? "" : " is-collapsed") : ""),
                    })
                  );
                  r.node.forEach(function (cell, coff, i) {
                    if (i < 1 || i > 3) return;
                    var at = r.pos + 1 + coff;
                    decos.push(Decoration.node(at, at + cell.nodeSize, { class: "mt-cell mt-col" + i, contenteditable: "false" }));
                    decos.push(
                      Decoration.widget(at + 1, cellWidget(it, i), {
                        key: ["mt", i, it.id, it.code, it.label, it.who, it.due, it.note].join("|"),
                        side: -1,
                        ignoreSelection: true,
                        stopEvent: stop,
                      })
                    );
                  });
                });
                decos.push(Decoration.widget(pos, counterWidget(decided, total), { key: "mt-sum|" + decided + "/" + total, side: -1, stopEvent: stop }));
                if (excluded) {
                  decos.push(
                    Decoration.widget(pos + node.nodeSize, tailWidget(excluded, !!st.open), {
                      key: "mt-ex|" + excluded + (st.open ? "o" : "c"),
                      side: 1,
                      stopEvent: stop,
                    })
                  );
                }
                return false;
              });
              return DecorationSet.create(state.doc, decos);
            },
          },
        }),
      ];
    },
  });
}

function createHashtagExtensions(ctx) {
  var HashtagHighlight = Extension.create({
    name: "hashtagHighlight",
    addProseMirrorPlugins: function () {
      return [
        new Plugin({
          key: new PluginKey("hashtagHighlight"),
          props: {
            decorations: function (state) {
              var decos = [];
              state.doc.descendants(function (node, pos) {
                if (!node.isText) return;
                var text = node.text || "";
                HASHTAG_INLINE_RE.lastIndex = 0;
                var match;
                while ((match = HASHTAG_INLINE_RE.exec(text))) {
                  decos.push(
                    Decoration.inline(pos + match.index, pos + match.index + match[0].length, {
                      class: "note-hashtag",
                    })
                  );
                }
              });
              return DecorationSet.create(state.doc, decos);
            },
          },
        }),
      ];
    },
  });

  var HashtagSuggest = Extension.create({
    name: "hashtagSuggest",
    addProseMirrorPlugins: function () {
      return [
        new Plugin({
          key: new PluginKey("hashtagSuggest"),
          view: function () {
            var el = document.createElement("div");
            el.className = "note-hashtag-suggest hidden";
            document.body.appendChild(el);
            ctx.suggestEl = el;

            function hide() {
              el.classList.add("hidden");
              el.innerHTML = "";
              ctx.suggestItems = [];
              ctx.suggestIndex = 0;
              ctx.suggestFrom = null;
            }

            function pick(name) {
              var view = ctx.editor && ctx.editor.view;
              if (!view || ctx.suggestFrom == null) {
                hide();
                return;
              }
              var to = view.state.selection.from;
              var tr = view.state.tr.insertText("#" + name + " ", ctx.suggestFrom, to);
              view.dispatch(tr);
              view.focus();
              hide();
            }
            ctx.pickHashtag = pick;
            ctx.hideHashtagSuggest = hide;

            function render() {
              el.innerHTML = "";
              (ctx.suggestItems || []).forEach(function (name, i) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className =
                  "note-hashtag-suggest-item" + (i === ctx.suggestIndex ? " is-active" : "");
                btn.textContent = "#" + name;
                btn.addEventListener("mousedown", function (e) {
                  e.preventDefault();
                  pick(name);
                });
                el.appendChild(btn);
              });
            }
            ctx.renderHashtagSuggest = render;

            return {
              update: function (view) {
                if (!view.state.selection.empty) {
                  hide();
                  return;
                }
                var $from = view.state.selection.$from;
                var text = $from.parent.textBetween(0, $from.parentOffset, null, "\ufffc");
                var match = text.match(HASHTAG_QUERY_RE);
                if (!match) {
                  hide();
                  return;
                }
                var q = String(match[2] || "").toLowerCase();
                ctx.suggestFrom = $from.pos - match[1].length;
                var all = (ctx.hashtags || []).filter(Boolean);
                var items = all.filter(function (name) {
                  var low = String(name).toLowerCase();
                  return low.indexOf(q) === 0 && low !== q;
                }).slice(0, 8);
                if (q && !all.some(function (name) {
                  return String(name).toLowerCase() === q;
                })) {
                  items.push(match[2]);
                }
                if (!items.length) {
                  hide();
                  return;
                }
                ctx.suggestItems = items;
                ctx.suggestIndex = 0;
                render();
                var coords = view.coordsAtPos($from.pos);
                el.style.position = "fixed";
                el.style.zIndex = "520";
                el.style.left = Math.max(8, coords.left) + "px";
                el.style.top = coords.bottom + 6 + "px";
                el.classList.remove("hidden");
              },
              destroy: function () {
                if (el.parentNode) el.parentNode.removeChild(el);
              },
            };
          },
          props: {
            handleKeyDown: function (view, event) {
              var items = ctx.suggestItems || [];
              if (!items.length || (ctx.suggestEl && ctx.suggestEl.classList.contains("hidden"))) {
                return false;
              }
              if (event.key === "ArrowDown") {
                event.preventDefault();
                ctx.suggestIndex = (ctx.suggestIndex + 1) % items.length;
                if (ctx.renderHashtagSuggest) ctx.renderHashtagSuggest();
                return true;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                ctx.suggestIndex = (ctx.suggestIndex - 1 + items.length) % items.length;
                if (ctx.renderHashtagSuggest) ctx.renderHashtagSuggest();
                return true;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                event.preventDefault();
                if (ctx.pickHashtag) ctx.pickHashtag(items[ctx.suggestIndex]);
                return true;
              }
              if (event.key === "Escape") {
                if (ctx.hideHashtagSuggest) ctx.hideHashtagSuggest();
                return true;
              }
              return false;
            },
          },
        }),
      ];
    },
  });

  return [HashtagHighlight, HashtagSuggest];
}

function mount(container, options) {
  options = options || {};
  const shell = document.createElement("div");
  shell.className = "note-rich-editor";
  const toolbar = document.createElement("div");
  toolbar.className = "note-rich-editor-toolbar note-tg-toolbar";
  toolbar.setAttribute("role", "toolbar");
  const content = document.createElement("div");
  content.className = "note-rich-editor-surface";
  if (options.toolbarParent) {
    options.toolbarParent.appendChild(toolbar);
    shell.appendChild(content);
  } else {
    shell.appendChild(toolbar);
    shell.appendChild(content);
  }
  container.innerHTML = "";
  container.appendChild(shell);

  var tablePasteClickPos = { pos: null };
  var hashtagCtx = {
    hashtags: Array.isArray(options.hashtags) ? options.hashtags.slice() : [],
    editor: null,
  };
  var taskCtx = {
    editor: null,
    slashBusy: false,
    suppressChipDelete: false,
    onTaskSlash: options.onTaskSlash,
    onTaskChipClick: options.onTaskChipClick,
    onExternalTaskClick: options.onExternalTaskClick,
    onTaskChipRemoved: options.onTaskChipRemoved,
    onFocusMarkClick: options.onFocusMarkClick,
  };
  var meetingTasksCtx = { onMeetingTaskClick: options.onMeetingTaskClick };
  var noteRefCtx = {
    editor: null,
    onSearchNoteRefs: options.onSearchNoteRefs,
    onNoteRefChipClick: options.onNoteRefChipClick,
    onEntityRefChipClick: options.onEntityRefChipClick,
  };

  const editor = new Editor({
    element: content,
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4, 5, 6] },
      }),
      Underline,
      NoteTextStyle,
      NoteFontStyle,
      NoteHighlight,
      ListBackspace,
      ListJoinBackspace,
      Link.configure({ openOnClick: false, autolink: true, linkOnPaste: true }),
      ResizableImage.configure({
        inline: false,
        allowBase64: true,
        HTMLAttributes: { class: "note-editor-image" },
      }),
      ManagedTable.configure({
        resizable: false,
        cellMinWidth: 48,
        HTMLAttributes: { class: "note-editor-table" },
      }),
      ManagedTableRow,
      ManagedTableHeader,
      ManagedTableCell,
      TrailingParagraph,
      LeoLineId,
      BotTaskList.configure({
        HTMLAttributes: { class: "note-task-list" },
      }),
      BotTaskItem.configure({ nested: false }),
      Placeholder.configure({
        placeholder: options.placeholder || "Начните писать…",
      }),
      IosSelectionGuard,
      MarkdownHeadingShortcut,
      NoteIndent,
      NoteIndentBackspace,
      NoteToggle,
      NoteToggleSummary,
      NoteToggleBody,
      NoteColumns,
      NoteColumn,
    ]
      .concat(createHashtagExtensions(hashtagCtx))
      .concat(createLeoTaskExtensions(taskCtx))
      .concat([createMeetingTasksExtension(meetingTasksCtx)])
      .concat(createNoteRefExtensions(noteRefCtx)),
    content: importBody(options.body || ""),
    autofocus: options.autofocus === true,
    editorProps: {
      attributes: {
        class: "note-rich-editor-body ProseMirror",
        spellcheck: "true",
      },
      handlePaste: function (view, event) {
        // Выделили текст и вставили ссылку — выделенное становится гиперссылкой (и в таблице, где вставку
        // иначе перехватывает обработчик ячеек, и если у ссылки пробел / перенос по краям).
        if (pasteUrlAsLink(view, event)) return true;
        // Скриншот / фото из буфера (без HTML — иначе это копия из веба, её разберёт редактор).
        var pastedImages = imageFilesFrom(event && event.clipboardData);
        if (pastedImages.length && !(event.clipboardData.getData("text/html") || "").trim()) {
          event.preventDefault();
          insertImageFiles(view.dom.editor, pastedImages, null);
          return true;
        }
        return pastePlainTextInTableCell(view, event, tablePasteClickPos.pos) || pasteMarkdownText(view, event);
      },
      // Фото, перетащенные в заметку с компьютера, — в место, куда отпустили.
      handleDrop: function (view, event, slice, moved) {
        if (moved) return false;
        var dropped = imageFilesFrom(event && event.dataTransfer);
        if (!dropped.length) return false;
        event.preventDefault();
        var hit = view.posAtCoords({ left: event.clientX, top: event.clientY });
        insertImageFiles(view.dom.editor, dropped, hit ? hit.pos : null);
        return true;
      },
      handleTripleClick: function (view, pos) {
        return selectAllTextInTableCell(view, pos);
      },
      // Нажатие на ссылку открывает её (через Telegram в мини-приложении).
      handleClick: function (view, pos, event) {
        const a = event && event.target && event.target.closest && event.target.closest("a[href]");
        if (!a || !view.dom.contains(a)) return false;
        if (event.shiftKey || event.altKey) return false;
        const href = safeLinkHref(a.getAttribute("href"));
        if (!href) return false;
        event.preventDefault();
        if (typeof options.onOpenLink === "function") options.onOpenLink(href);
        else window.open(href, "_blank", "noopener");
        return true;
      },
      handleDOMEvents: {
        copy: function (view, event) {
          return fillRichClipboard(view, event);
        },
        cut: function (view, event) {
          if (!fillRichClipboard(view, event)) return false;
          view.dispatch(view.state.tr.deleteSelection());
          return true;
        },
        mousedown: function (view, event) {
          rememberTableClickPos(view, event.clientX, event.clientY, tablePasteClickPos);
          return false;
        },
        touchstart: function (view, event) {
          if (!event.touches || !event.touches[0]) return false;
          rememberTableClickPos(
            view,
            event.touches[0].clientX,
            event.touches[0].clientY,
            tablePasteClickPos
          );
          return false;
        },
      },
    },
    onUpdate: function (props) {
      // remote — правка из совместного документа (чужая / пересборка): автосохранять её не нам.
      var remote = !!(props && isRemoteYTr(props.transaction));
      // noScroll — правка не у курсора (ширина / высота таблицы, ужатие фото, чужая правка): приложение
      // не подтягивает заметку к курсору.
      var noScroll = remote || !!(props && props.transaction && props.transaction.getMeta("leoNoScroll"));
      if (typeof options.onUpdate === "function") options.onUpdate({ remote: remote, noScroll: noScroll });
    },
    onSelectionUpdate: function (props) {
      if (typeof options.onSelection === "function") {
        var ed = props && props.editor;
        if (ed && ed.state && ed.state.selection) {
          options.onSelection(ed.state.selection.from);
        }
      }
    },
  });
  hashtagCtx.editor = editor;
  taskCtx.editor = editor;
  noteRefCtx.editor = editor;

  editor.view.dom.addEventListener(
    "paste",
    function (event) {
      // Ссылка на выделенный текст — раньше вставки в ячейку (та заменила бы выделение текстом ссылки).
      if (pasteUrlAsLink(editor.view, event)) {
        event.stopImmediatePropagation();
        return;
      }
      if (pastePlainTextInTableCell(editor.view, event, tablePasteClickPos.pos)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  var cleanupToolbar = mountToolbar(toolbar, editor);
  // Фото отпустили рядом с текстом (пустое место окна заметки) — браузер открыл бы файл
  // вместо приложения; вставляем в конец заметки.
  var dropZone = function () {
    return shell.closest(".modal, .note-editor-sheet") || shell;
  };
  function onDocDragOver(e) {
    if (!shell.isConnected || !imageDragged(e) || !dropZone().contains(e.target)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  }
  function onDocDrop(e) {
    if (!shell.isConnected || !dropZone().contains(e.target) || editor.view.dom.contains(e.target)) return;
    var files = imageFilesFrom(e.dataTransfer);
    if (!files.length) return;
    e.preventDefault();
    insertImageFiles(editor, files, editor.state.doc.content.size);
  }
  function imageDragged(e) {
    var dt = e.dataTransfer;
    if (!dt) return false;
    var items = Array.prototype.slice.call(dt.items || []);
    if (items.length) return items.some(function (it) { return it.kind === "file" && /^image\//i.test(String(it.type || "")); });
    return Array.prototype.indexOf.call(dt.types || [], "Files") >= 0;
  }
  document.addEventListener("dragover", onDocDragOver);
  document.addEventListener("drop", onDocDrop);
  setTimeout(function () {
    shrinkHeavyImages(editor);
  }, 1500);
  var cleanupToc = mountToc(options.tocParent, editor, {
    onTocNavigate: options.onTocNavigate,
    scrollParent: options.scrollParent,
    extraTocItems: options.extraTocItems,
  });

  function bindTapFocus(container) {
    if (!container || container._noteTapFocusBound) return;
    container._noteTapFocusBound = true;
    var touchStartX = 0;
    var touchStartY = 0;
    var touchMoved = false;

    container.addEventListener(
      "touchstart",
      function (e) {
        touchMoved = false;
        if (e.touches && e.touches[0]) {
          touchStartX = e.touches[0].clientX;
          touchStartY = e.touches[0].clientY;
        }
      },
      { passive: true }
    );

    container.addEventListener(
      "touchmove",
      function (e) {
        if (!e.touches || !e.touches[0]) return;
        var dx = Math.abs(e.touches[0].clientX - touchStartX);
        var dy = Math.abs(e.touches[0].clientY - touchStartY);
        if (dx > 8 || dy > 8) touchMoved = true;
      },
      { passive: true }
    );

    container.addEventListener(
      "touchend",
      function (e) {
        if (touchMoved) return;
        var sel = window.getSelection && window.getSelection();
        if (sel && !sel.isCollapsed && String(sel.toString() || "").length) return;
        if (e.target.closest && e.target.closest(".ProseMirror")) return;
        if (
          e.target.closest &&
          e.target.closest(
            "input, textarea, button, a, label, .note-rich-toolbar-btn, .note-tg-btn, .note-tg-menu, .note-table-manage-toggle, .note-table-handle, .note-table-confirm-overlay"
          )
        ) {
          return;
        }
        if (!container.contains(e.target)) return;
        window.requestAnimationFrame(function () {
          editor.commands.focus("end");
        });
      },
      { passive: true }
    );
  }

  // Диктовка: запоминаем место курсора при старте записи и сдвигаем его вместе с правками,
  // пока идёт расшифровка, — текст встанет туда, где пользователь начал говорить.
  var dictationMarks = {};
  var dictationSeq = 0;
  editor.on("transaction", function (ev) {
    var tr = ev && ev.transaction;
    if (!tr || !tr.docChanged) return;
    Object.keys(dictationMarks).forEach(function (k) {
      dictationMarks[k] = tr.mapping.map(dictationMarks[k]);
    });
  });

  return {
    // Таблица задач встречи: { items: [{ id, code, label, who, due, note, … }] } с сервера; null — снять.
    setMeetingTasks: function (state) {
      var map = null;
      if (state && Array.isArray(state.items)) {
        map = {};
        state.items.forEach(function (x) {
          if (x && x.id) map[x.id] = x;
        });
      }
      editor.view.dispatch(editor.state.tr.setMeta(meetingTasksKey, { items: map }).setMeta("addToHistory", false));
    },
    beginDictation: function (atEnd) {
      var id = "d" + ++dictationSeq;
      dictationMarks[id] = atEnd ? editor.state.doc.content.size : editor.state.selection.to;
      return id;
    },
    cancelDictation: function (id) {
      delete dictationMarks[id];
    },
    insertDictation: function (id, text) {
      var pos = dictationMarks[id];
      delete dictationMarks[id];
      var t = String(text || "").replace(/\r/g, "").trim();
      if (!t) return false;
      var size = editor.state.doc.content.size;
      if (typeof pos !== "number") pos = editor.state.selection.to;
      pos = Math.max(0, Math.min(pos, size));
      var $pos = editor.state.doc.resolve(pos);
      var parts = t.split(/\n{2,}/).map(function (x) { return x.replace(/\n/g, " ").trim(); }).filter(Boolean);
      if (!$pos.parent.isTextblock) {
        // Конец документа / между блоками — отдельными абзацами.
        editor.chain().focus().insertContentAt(pos, parts.map(function (x) {
          return { type: "paragraph", content: [{ type: "text", text: x }] };
        })).run();
        editor.commands.scrollIntoView();
        return true;
      }
      var before = $pos.parent.textBetween(0, $pos.parentOffset, " ", " ");
      var first = parts.shift() || "";
      if (before && !/\s$/.test(before)) first = " " + first;
      var chain = editor.chain().focus().insertContentAt(pos, { type: "text", text: first });
      chain.run();
      if (parts.length) {
        editor.chain().focus().insertContent(parts.map(function (x) {
          return { type: "paragraph", content: [{ type: "text", text: x }] };
        })).run();
      }
      editor.commands.scrollIntoView();
      return true;
    },
    /** Подключить совместный документ: содержимое редактора становится содержимым ydoc. */
    attachYjs: function (ydoc) {
      if (editor.__leoYjs && editor.__leoYjs.ydoc === ydoc) return;
      this.detachYjs();
      var fragment = ydoc.getXmlFragment(Y_FRAGMENT);
      var keysKey = new PluginKey("leoYjsKeys");
      var keys = new Plugin({
        key: keysKey,
        props: {
          handleKeyDown: keydownHandler({
            "Mod-z": function (state) { return yUndo(state); },
            "Mod-y": function (state) { return yRedo(state); },
            "Mod-Shift-z": function (state) { return yRedo(state); },
          }),
        },
      });
      // Свой UndoManager: y-prosemirror уничтожает его вместе с видом плагина, а ProseMirror
      // пересоздаёт виды при любой перенастройке плагинов — отмена бы молча отключилась.
      var undoManager = new Y.UndoManager(fragment, {
        trackedOrigins: new Set([ySyncPluginKey]),
        deleteFilter: function (item) { return defaultDeleteFilter(item, defaultProtectedNodes); },
        captureTransaction: function (tr) { return tr.meta.get("addToHistory") !== false; },
      });
      var destroyUndo = undoManager.destroy.bind(undoManager);
      undoManager.destroy = function () {};
      editor.__leoYjs = { ydoc: ydoc, keysKey: keysKey, destroyUndo: destroyUndo };
      var sync = ySyncPlugin(fragment);
      var undo = yUndoPlugin({ undoManager: undoManager });
      taskCtx.suppressChipDelete = true;
      try {
        // Одной перенастройкой; клавиши — раньше истории StarterKit, иначе Ctrl+Z откатит не то.
        editor.registerPlugin(sync, function (_p, plugins) {
          return [keys].concat(plugins, [sync, undo]);
        });
      } finally {
        taskCtx.suppressChipDelete = false;
      }
    },
    /** Отключить (переход на другой лист): ydoc продолжает жить и принимать правки. */
    detachYjs: function () {
      var st = editor.__leoYjs;
      if (!st) return;
      editor.__leoYjs = null;
      taskCtx.suppressChipDelete = true;
      try {
        editor.unregisterPlugin([st.keysKey, yUndoPluginKey, ySyncPluginKey]);
      } finally {
        taskCtx.suppressChipDelete = false;
      }
      st.destroyUndo();
    },
    isYjsAttached: function () {
      return !!editor.__leoYjs;
    },
    /** Влить HTML в совместный документ разницей (первый посев или правка мимо Yjs — ИИ, бот).
     * origin — метка транзакции Yjs, чтобы приложение отличало свои правки. */
    applyHtmlToYjs: function (ydoc, html, origin) {
      // Те же опции разбора, что у setContent — иначе посев разойдётся с тем, что в редакторе.
      var doc = createDocument(importBody(html || ""), editor.schema, editor.options.parseOptions || {});
      var fragment = ydoc.getXmlFragment(Y_FRAGMENT);
      ydoc.transact(function () {
        prosemirrorToYXmlFragment(doc, fragment);
      }, origin);
    },
    /** Правка мимо Yjs (ИИ, бот, старое приложение): вносим только её разницу old → new,
     * остальной документ (в т.ч. ещё не сохранённые в HTML правки людей) не трогаем. */
    applyHtmlDeltaToYjs: function (ydoc, oldHtml, newHtml, origin) {
      var parse = editor.options.parseOptions || {};
      var oldDoc = createDocument(importBody(oldHtml || ""), editor.schema, parse);
      var newDoc = createDocument(importBody(newHtml || ""), editor.schema, parse);
      var fragment = ydoc.getXmlFragment(Y_FRAGMENT);
      var curDoc = yXmlFragmentToProseMirrorRootNode(fragment, editor.schema);
      function blocks(doc) {
        var out = [];
        doc.forEach(function (node) { out.push(node); });
        return out;
      }
      var merged = mergeBlocks(blocks(oldDoc), blocks(newDoc), blocks(curDoc), function (a, b) {
        return a.eq(b);
      });
      if (!merged.length) merged = [editor.schema.nodes.paragraph.create()];
      var nextDoc = editor.schema.topNodeType.create(null, merged);
      if (nextDoc.eq(curDoc)) return false;
      ydoc.transact(function () {
        prosemirrorToYXmlFragment(nextDoc, fragment);
      }, origin);
      return true;
    },
    setEditable: function (on) {
      editor.setEditable(!!on, false);
    },
    getHtml: function () {
      return exportBody(editor);
    },
    setHtml: function (html, opts) {
      opts = opts || {};
      var parsed = importBody(html || "");
      var keep = !!opts.preserveCursor;
      var from = editor.state.selection.from;
      taskCtx.suppressChipDelete = true;
      try {
        editor.commands.setContent(parsed, false);
      } finally {
        taskCtx.suppressChipDelete = false;
      }
      if (keep) {
        var max = editor.state.doc.content.size;
        var pos = Math.max(1, Math.min(from, max));
        try {
          editor.commands.setTextSelection(pos);
        } catch (_) {}
      }
    },
    getCursor: function () {
      var sel = editor.state.selection;
      return sel.head != null ? sel.head : sel.from;
    },
    hasRangeSelection: function () {
      var sel = editor.state.selection;
      return !!(sel && !sel.empty);
    },
    coordsAtPos: function (pos) {
      try {
        var n = Number(pos);
        if (!isFinite(n) || n < 0) return null;
        var max = editor.state.doc.content.size;
        var safe = Math.max(0, Math.min(n, max));
        return editor.view.coordsAtPos(safe);
      } catch (_) {
        return null;
      }
    },
    scrollCaretIntoView: function () {
      try {
        editor.commands.scrollIntoView();
      } catch (_) {}
    },
    appendText: function (text) {
      var raw = String(text || "").replace(/\s+$/, "");
      if (!raw) return;
      var lines = raw.split("\n");
      var nodes = lines.map(function (line) {
        if (!line) return { type: "paragraph" };
        return { type: "paragraph", content: [{ type: "text", text: line }] };
      });
      editor.chain().focus("end").insertContent(nodes).run();
    },
    appendHtml: function (html) {
      var raw = String(html || "").trim();
      if (!raw) return;
      editor.chain().focus("end").insertContent(raw).run();
    },
    prepareForSave: function () {
      editor.view.dom.blur();
    },
    collapseSelection: function () {
      try {
        var sel = editor.state.selection;
        if (sel && !sel.empty) {
          editor.commands.setTextSelection(sel.to);
        }
      } catch (_) {}
      try {
        var native = window.getSelection && window.getSelection();
        if (native) native.removeAllRanges();
      } catch (_) {}
    },
    /** Сбросить выделение и iOS callout/контекстное меню после выхода из редактирования. */
    dismissSelectionUi: function () {
      try {
        if (typeof this.collapseSelection === "function") this.collapseSelection();
      } catch (_) {}
      var dom = editor.view && editor.view.dom;
      if (!dom) return;
      try {
        dom.blur();
      } catch (_) {}
      // iOS: callout часто остаётся после blur — короткий contenteditable=false его снимает.
      // Возвращаем по состоянию редактора, а не «как было»: второй вызов подряд читал уже «false»
      // и оставлял заметку нередактируемой (не выделялся и не стирался текст).
      try {
        dom.setAttribute("contenteditable", "false");
      } catch (_) {}
      var restoreToken = (dom._leoEditableToken || 0) + 1;
      dom._leoEditableToken = restoreToken;
      var restore = function () {
        if (dom._leoEditableToken !== restoreToken) return;
        try {
          dom.setAttribute("contenteditable", editor.isEditable ? "true" : "false");
        } catch (_) {}
      };
      window.requestAnimationFrame(restore);
      // rAF не идёт, пока мини-приложение свёрнуто, — подстраховка таймером.
      window.setTimeout(restore, 120);
    },
    focus: function () {
      editor.commands.focus("end");
    },
    bindTapFocus: bindTapFocus,
    setHashtags: function (names) {
      hashtagCtx.hashtags = Array.isArray(names) ? names.slice() : [];
    },
    getSelectionRange: function () {
      var sel = editor.state.selection;
      return { from: sel.from, to: sel.to };
    },
    // ID строки начала выделения (data-leo-item) — якорь ссылки на фрагмент. Нет ID — выдаём.
    ensureSelectionLineId: function () {
      var $from = editor.state.selection.$from;
      for (var d = $from.depth; d > 0; d--) {
        var node = $from.node(d);
        if (LEO_LINE_TYPES.indexOf(node.type.name) < 0) continue;
        if (node.attrs.leoItem) return node.attrs.leoItem;
        var id = newLeoItemId();
        editor.view.dispatch(
          editor.state.tr.setNodeMarkup($from.before(d), undefined, Object.assign({}, node.attrs, { leoItem: id }))
        );
        return id;
      }
      return "";
    },
    insertTaskChip: function (taskId, label, ext) {
      editor
        .chain()
        .focus()
        .insertContent([
          {
            type: "leoTaskChip",
            attrs: taskChipAttrs(taskId, label, ext),
          },
          { type: "text", text: " " },
        ])
        .run();
    },
    insertTaskChipAfter: function (taskId, label, pos, ext) {
      var size = editor.state.doc.content.size;
      var at = typeof pos === "number" ? pos : editor.state.selection.to;
      if (at < 0 || at > size) at = editor.state.selection.to;
      editor
        .chain()
        .focus()
        .insertContentAt(at, [
          { type: "text", text: " " },
          {
            type: "leoTaskChip",
            attrs: taskChipAttrs(taskId, label, ext),
          },
          { type: "text", text: " " },
        ])
        .run();
    },
    updateTaskChip: function (taskId, label) {
      var id = String(taskId || "");
      var tr = editor.state.tr;
      var changed = false;
      editor.state.doc.descendants(function (node, pos) {
        if (node.type.name === "leoTaskChip" && String(node.attrs.taskId) === id) {
          tr.setNodeMarkup(pos, undefined, Object.assign({}, node.attrs, {
            taskId: id,
            label: normalizeTaskChipLabel(label || node.attrs.label),
          }));
          changed = true;
        }
      });
      if (changed) editor.view.dispatch(tr);
    },
    getTaskChipMeta: function (taskId) {
      var id = String(taskId || "");
      var found = null;
      editor.state.doc.descendants(function (node, pos) {
        if (!id || found || node.type.name !== "leoTaskChip") return;
        if (String(node.attrs.taskId) !== id) return;
        var parent = editor.state.doc.resolve(pos).parent;
        var around = "";
        try {
          around = parent.textBetween(0, parent.content.size, " ", " ");
        } catch (_) {}
        found = {
          taskId: id,
          label: normalizeTaskChipLabel(node.attrs.label),
          title: String(around || "")
            .replace(String(node.attrs.label || ""), " ")
            .replace(/\s+/g, " ")
            .trim(),
        };
      });
      return found;
    },
    remapTaskChip: function (fromId, toId, label) {
      var src = String(fromId || "");
      var dst = String(toId || "");
      if (!src || !dst) return;
      var tr = editor.state.tr;
      var changed = false;
      editor.state.doc.descendants(function (node, pos) {
        if (node.type.name === "leoTaskChip" && String(node.attrs.taskId) === src) {
          tr.setNodeMarkup(pos, undefined, Object.assign({}, node.attrs, {
            taskId: dst,
            label: normalizeTaskChipLabel(label || node.attrs.label),
          }));
          changed = true;
        }
      });
      if (changed) {
        taskCtx.suppressChipDelete = true;
        editor.view.dispatch(tr);
        taskCtx.suppressChipDelete = false;
      }
    },
    removeTaskChip: function (taskId) {
      var id = String(taskId || "");
      var deletes = [];
      editor.state.doc.descendants(function (node, pos) {
        if (node.type.name === "leoTaskChip" && String(node.attrs.taskId) === id) {
          deletes.push({ from: pos, to: pos + node.nodeSize });
        }
      });
      if (!deletes.length) return;
      var tr = editor.state.tr;
      for (var i = deletes.length - 1; i >= 0; i--) {
        tr.delete(deletes[i].from, deletes[i].to);
      }
      taskCtx.suppressChipDelete = true;
      editor.view.dispatch(tr);
      taskCtx.suppressChipDelete = false;
    },
    destroy: function () {
      closeLinkPopover();
      closeTgMenus(toolbar);
      if (typeof hashtagCtx.hideHashtagSuggest === "function") hashtagCtx.hideHashtagSuggest();
      if (typeof taskCtx.hideTaskSlashSuggest === "function") taskCtx.hideTaskSlashSuggest();
      if (typeof noteRefCtx.hideNoteRefSuggest === "function") noteRefCtx.hideNoteRefSuggest();
      if (typeof cleanupToolbar === "function") cleanupToolbar();
      if (typeof cleanupToc === "function") cleanupToc();
      document.removeEventListener("dragover", onDocDragOver);
      document.removeEventListener("drop", onDocDrop);
      editor.destroy();
    },
  };
}

var noteRichEditorApi = {
  assetVersion: "20261028-align",
  Y: Y,
  mount: mount,
  importBody: importBody,
  exportBody: exportBody,
  canonicalBody: canonicalBody,
  paragraphTitleBeforeSlash: paragraphTitleBeforeSlash,
};
if (typeof window !== "undefined") window.NoteRichEditor = noteRichEditorApi;
else globalThis.NoteRichEditor = noteRichEditorApi;
