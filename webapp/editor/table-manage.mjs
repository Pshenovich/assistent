import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import {
  CellSelection,
  TableMap,
  addColumnAfter,
  addColumnBefore,
  addRowAfter,
  addRowBefore,
  cellAround,
  deleteColumn,
  deleteRow,
  deleteTable,
  mergeCells,
  moveTableColumn,
  moveTableRow,
  setCellAttr,
  splitCell,
  toggleHeaderRow,
} from "@tiptap/pm/tables";

var tableManageKey = new PluginKey("tableManage");
var MIN_COL = 48;
var MIN_ROW = 32;
var DRAG_THRESHOLD = 8;
var activeView = null;
var confirmCloser = null;
// Все таблицы редактора: активной становится та, где стоит курсор.
var allViews = new Set();
// Палец / кнопка мыши сейчас прижаты в редакторе — меню таблицы не показываем, пока не отпустят
// (иначе оно выскакивало посреди выделения ячеек и закрывало их).
var pointerHeld = false;
var MENU_DELAY = 400;

function svg(paths, size) {
  var s = size || 22;
  return (
    '<svg viewBox="0 0 24 24" width="' + s + '" height="' + s + '" aria-hidden="true" fill="none" ' +
    'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
    paths +
    "</svg>"
  );
}

// «•••» под столбцом и «⋮» у строки — как в Telegram.
var ICON_MORE =
  '<svg viewBox="0 0 20 8" width="18" height="8" aria-hidden="true">' +
  '<circle cx="4" cy="4" r="1.7" fill="currentColor"/>' +
  '<circle cx="10" cy="4" r="1.7" fill="currentColor"/>' +
  '<circle cx="16" cy="4" r="1.7" fill="currentColor"/>' +
  "</svg>";

var ICON_DOTS =
  '<svg viewBox="0 0 8 20" width="8" height="18" aria-hidden="true">' +
  '<circle cx="4" cy="4" r="1.7" fill="currentColor"/>' +
  '<circle cx="4" cy="10" r="1.7" fill="currentColor"/>' +
  '<circle cx="4" cy="16" r="1.7" fill="currentColor"/>' +
  "</svg>";

var ICONS = {
  "h-left": svg('<path d="M4 4v16"/><path d="M8 9h12M8 15h8"/>'),
  "h-center": svg('<path d="M12 3v18"/><path d="M5 9h14M7 15h10"/>'),
  "h-right": svg('<path d="M20 4v16"/><path d="M4 9h12M8 15h8"/>'),
  "v-top": svg('<path d="M4 4h16"/><path d="M9 8v12M15 8v8"/>'),
  "v-middle": svg('<path d="M3 12h18"/><path d="M9 5v14M15 7v10"/>'),
  "v-bottom": svg('<path d="M4 20h16"/><path d="M9 4v12M15 8v8"/>'),
  highlight: svg('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 14 14 4M9 20 20 9M4 9l5-5M15 20l5-5"/>'),
  merge: svg('<path d="M3 5v14M21 5v14"/><path d="M7 12h10"/><path d="m10 9-3 3 3 3M14 9l3 3-3 3"/>'),
  split: svg('<path d="M12 4v16"/><path d="M3 12h5M16 12h5"/><path d="m6 9 3 3-3 3M18 9l-3 3 3 3"/>'),
  "col-left": svg('<rect x="12" y="4" width="8" height="16" rx="2"/><path d="M4 12h5M6.5 9.5v5"/>'),
  "col-right": svg('<rect x="4" y="4" width="8" height="16" rx="2"/><path d="M15 12h5M17.5 9.5v5"/>'),
  "row-above": svg('<rect x="4" y="12" width="16" height="8" rx="2"/><path d="M12 3v6M9 6h6"/>'),
  "row-below": svg('<rect x="4" y="4" width="16" height="8" rx="2"/><path d="M12 15v6M9 18h6"/>'),
  header: svg('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h16"/><path d="M4 6.5h16" stroke-width="3.5" opacity=".5"/>'),
  "sort-asc": svg('<path d="M7 4v16M3.5 7.5 7 4l3.5 3.5"/><path d="M13 6h8M13 11h6M13 16h4"/>'),
  "sort-desc": svg('<path d="M7 4v16M3.5 16.5 7 20l3.5-3.5"/><path d="M13 6h4M13 11h6M13 16h8"/>'),
  sum: svg('<path d="M18 5H6l6 7-6 7h12"/>'),
  table: svg('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>'),
  ai: svg('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>'),
  delete: svg('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m9 9 6 6M15 9l-6 6"/>'),
  width: svg('<path d="M4 5v14M20 5v14"/><path d="M8 12h8"/><path d="m10 9-3 3 3 3M14 9l3 3-3 3"/>'),
  height: svg('<path d="M5 4h14M5 20h14"/><path d="M12 8v8"/><path d="m9 10 3-3 3 3M9 14l3 3 3-3"/>'),
};

function parseRowHeight(element) {
  var attr = element.getAttribute("data-row-height");
  if (attr) {
    var n = parseInt(attr, 10);
    if (n > 0) return n;
  }
  var h = element.style && element.style.height;
  if (h) {
    var n2 = parseInt(h, 10);
    if (n2 > 0) return n2;
  }
  return null;
}

export const ManagedTableRow = TableRow.extend({
  addAttributes() {
    return {
      height: {
        default: null,
        parseHTML: parseRowHeight,
        renderHTML: function (attributes) {
          if (!attributes.height) return {};
          return {
            "data-row-height": String(attributes.height),
            style: "height: " + attributes.height + "px",
          };
        },
      },
    };
  },
});


// Выравнивание и заливка ячеек: text-align / vertical-align в style, заливка — data-highlight.
var H_ALIGN = ["left", "center", "right"];
var V_ALIGN = ["top", "middle", "bottom"];

function pick(value, allowed) {
  var v = String(value || "").trim().toLowerCase();
  if (v === "start") v = "left";
  if (v === "end") v = "right";
  return allowed.indexOf(v) >= 0 ? v : null;
}

var CELL_ATTRS = {
  align: {
    default: null,
    parseHTML: function (el) {
      var v = pick(el.style.textAlign || el.getAttribute("align"), H_ALIGN);
      return v === "left" ? null : v;
    },
    renderHTML: function (attrs) {
      return attrs.align ? { style: "text-align: " + attrs.align } : {};
    },
  },
  valign: {
    default: null,
    parseHTML: function (el) {
      var v = pick(el.style.verticalAlign || el.getAttribute("valign"), V_ALIGN);
      return v === "top" ? null : v;
    },
    renderHTML: function (attrs) {
      return attrs.valign ? { style: "vertical-align: " + attrs.valign } : {};
    },
  },
  highlight: {
    default: null,
    parseHTML: function (el) {
      return el.getAttribute("data-highlight") ? "1" : null;
    },
    renderHTML: function (attrs) {
      return attrs.highlight ? { "data-highlight": "1" } : {};
    },
  },
};

function withCellAttrs() {
  var base = this.parent ? this.parent() : {};
  return Object.assign({}, base, CELL_ATTRS);
}

export const ManagedTableCell = TableCell.extend({ addAttributes: withCellAttrs });
export const ManagedTableHeader = TableHeader.extend({ addAttributes: withCellAttrs });

function updateColumns(node, colgroup, table, cellMinWidth, overrideCol, overrideValue) {
  var totalWidth = 0;
  var fixedWidth = true;
  var nextDOM = colgroup.firstChild;
  var row = node.firstChild;
  if (row) {
    for (var i = 0, col = 0; i < row.childCount; i += 1) {
      var attrs = row.child(i).attrs;
      var colspan = attrs.colspan || 1;
      var colwidth = attrs.colwidth;
      for (var j = 0; j < colspan; j += 1, col += 1) {
        var hasWidth =
          overrideCol === col ? overrideValue : colwidth && colwidth[j];
        var cssWidth = hasWidth ? Math.max(hasWidth, cellMinWidth) + "px" : "";
        totalWidth += hasWidth || cellMinWidth;
        if (!hasWidth) fixedWidth = false;
        if (!nextDOM) {
          var colEl = document.createElement("col");
          if (cssWidth) colEl.style.width = cssWidth;
          else colEl.style.minWidth = cellMinWidth + "px";
          colgroup.appendChild(colEl);
          nextDOM = null;
        } else {
          if (cssWidth) {
            nextDOM.style.width = cssWidth;
            nextDOM.style.minWidth = "";
          } else {
            nextDOM.style.width = "";
            nextDOM.style.minWidth = cellMinWidth + "px";
          }
          nextDOM = nextDOM.nextSibling;
        }
      }
    }
  }
  while (nextDOM) {
    var after = nextDOM.nextSibling;
    nextDOM.parentNode.removeChild(nextDOM);
    nextDOM = after;
  }
  if (fixedWidth) {
    table.style.width = totalWidth + "px";
    table.style.minWidth = "";
  } else {
    table.style.width = "";
    table.style.minWidth = totalWidth + "px";
  }
}

function makeButton(className, label, html) {
  var el = document.createElement("button");
  el.type = "button";
  el.className = className;
  el.setAttribute("aria-label", label);
  el.innerHTML = html;
  el.contentEditable = "false";
  el.tabIndex = 0;
  el.draggable = false;
  return el;
}

function dist(ax, ay, bx, by) {
  var dx = ax - bx;
  var dy = ay - by;
  return Math.sqrt(dx * dx + dy * dy);
}

function closeConfirm(result) {
  var fn = confirmCloser;
  confirmCloser = null;
  if (fn) fn(result);
}

function confirmTableDelete(message) {
  closeConfirm(false);
  return new Promise(function (resolve) {
    var overlay = document.createElement("div");
    overlay.className = "note-table-confirm-overlay";
    overlay.innerHTML =
      '<div class="note-table-confirm-modal" role="dialog" aria-modal="true">' +
      "<h2 class=\"note-table-confirm-title\"></h2>" +
      '<div class="note-table-confirm-actions">' +
      '<button type="button" class="btn primary note-table-confirm-ok">Удалить</button>' +
      '<button type="button" class="btn ghost note-table-confirm-cancel">Отмена</button>' +
      "</div></div>";
    overlay.querySelector(".note-table-confirm-title").textContent = message;
    var host = document.getElementById("note-editor-overlay") || document.body;
    host.appendChild(overlay);

    function finish(ok) {
      if (confirmCloser !== finish) return;
      confirmCloser = null;
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.removeEventListener("keydown", onKey, true);
      resolve(!!ok);
    }

    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        finish(false);
      } else if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      }
    }

    confirmCloser = finish;
    overlay.querySelector(".note-table-confirm-ok").addEventListener("click", function () {
      finish(true);
    });
    overlay.querySelector(".note-table-confirm-cancel").addEventListener("click", function () {
      finish(false);
    });
    overlay.addEventListener("mousedown", function (e) {
      if (e.target === overlay) finish(false);
    });
    document.addEventListener("keydown", onKey, true);
  });
}

function selectCell(editor, tablePos, row, col) {
  var table = editor.state.doc.nodeAt(tablePos);
  if (!table) return false;
  var map = TableMap.get(table);
  var r = Math.max(0, Math.min(row, map.height - 1));
  var c = Math.max(0, Math.min(col, map.width - 1));
  var cellPos = tablePos + 1 + map.positionAt(r, c, table);
  return editor.chain().focus().setCellSelection({ anchorCell: cellPos }).run();
}

function runTableCommand(editor, tablePos, row, col, command) {
  if (!selectCell(editor, tablePos, row, col)) return false;
  return command(editor.state, function (tr) {
    editor.view.dispatch(tr);
  });
}

function setAllColWidths(editor, tablePos, widths) {
  var table = editor.state.doc.nodeAt(tablePos);
  if (!table) return;
  var tr = editor.state.tr;
  table.forEach(function (row, rowOffset) {
    var col = 0;
    row.forEach(function (cell, cellOffset) {
      var span = cell.attrs.colspan || 1;
      var next = [];
      for (var i = 0; i < span; i += 1) {
        next.push(Math.max(MIN_COL, Math.round(widths[col + i] || MIN_COL)));
      }
      var pos = tablePos + 1 + rowOffset + 1 + cellOffset;
      tr.setNodeMarkup(pos, null, Object.assign({}, cell.attrs, { colwidth: next }));
      col += span;
    });
  });
  editor.view.dispatch(tr.setMeta("leoNoScroll", true));
}

function setRowHeight(editor, tablePos, rowIndex, height) {
  var table = editor.state.doc.nodeAt(tablePos);
  if (!table) return;
  var tr = editor.state.tr;
  var h = Math.max(MIN_ROW, Math.round(height));
  table.forEach(function (row, rowOffset, i) {
    if (i === rowIndex) {
      tr.setNodeMarkup(
        tablePos + 1 + rowOffset,
        null,
        Object.assign({}, row.attrs, { height: h })
      );
    }
  });
  editor.view.dispatch(tr.setMeta("leoNoScroll", true));
}

function applyPreviewWidths(colgroup, table, widths, minWidth) {
  var cols = colgroup.children;
  var total = 0;
  for (var i = 0; i < widths.length; i += 1) {
    var w = Math.max(minWidth, widths[i]);
    total += w;
    var col = cols[i];
    if (!col) {
      col = document.createElement("col");
      colgroup.appendChild(col);
    }
    col.style.width = w + "px";
    col.style.minWidth = "";
  }
  while (colgroup.children.length > widths.length) {
    colgroup.removeChild(colgroup.lastChild);
  }
  table.style.width = total + "px";
  table.style.minWidth = "";
}



// ---- Данные таблицы: итоги и сортировка (считает код, без ИИ) ----
// \b в JS не понимает кириллицу — границу слова проверяем сами.
var TOTAL_RE = /^\s*(итого|всего|сумма|total|sum)(?![a-zа-яё0-9])/i;

function parseNum(text) {
  var t = String(text || "").trim().replace(/[−–]/g, "-").replace(/[\s  ]/g, "");
  t = t.replace(/(%|₽|руб\.?|р\.|\$|€)$/i, "");
  if (!/^[+-]?\d+(?:[.,]\d+)?$/.test(t)) return null;
  return { value: parseFloat(t.replace(",", ".")), decimals: (t.split(/[.,]/)[1] || "").length, comma: t.indexOf(",") >= 0 };
}

function fmtNum(n, decimals, comma) {
  var neg = n < 0;
  var fixed = Math.abs(n).toFixed(decimals);
  var parts = fixed.split(".");
  var int = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  var out = int + (parts[1] ? (comma ? "," : ".") + parts[1] : "");
  return (neg ? "−" : "") + out;
}

// Строки таблицы: первая — шапка, если в ней заголовки (th) или нет чисел; строки «Итого» — отдельно.
function tableShape(table) {
  var map = TableMap.get(table);
  var cellText = function (r, c) {
    var node = table.nodeAt(map.positionAt(r, c, table));
    return node ? node.textContent : "";
  };
  var first = table.firstChild;
  var allTh = true;
  first.forEach(function (cell) {
    if (cell.type.spec.tableRole !== "header_cell") allTh = false;
  });
  var firstHasNum = false;
  for (var c = 0; c < map.width; c += 1) if (parseNum(cellText(0, c))) firstHasNum = true;
  var header = allTh || (!firstHasNum && map.height > 1) ? 1 : 0;
  var totalRows = [];
  var dataRows = [];
  for (var r = header; r < map.height; r += 1) {
    if (TOTAL_RE.test(cellText(r, 0))) totalRows.push(r);
    else dataRows.push(r);
  }
  var totalCol = -1;
  if (header) for (var k = 1; k < map.width; k += 1) if (TOTAL_RE.test(cellText(0, k))) totalCol = k;
  return { map: map, text: cellText, header: header, dataRows: dataRows, totalRows: totalRows, totalCol: totalCol };
}

function setCellTexts(editor, tablePos, edits) {
  // edits: [{row, col, text}] — заменяем содержимое ячеек одной транзакцией, с конца документа к началу.
  var state = editor.state;
  var table = state.doc.nodeAt(tablePos);
  if (!table) return;
  var map = TableMap.get(table);
  var schema = state.schema;
  var tr = state.tr;
  edits
    .map(function (e) {
      var rel = map.positionAt(e.row, e.col, table);
      return { pos: tablePos + 1 + rel, cell: table.nodeAt(rel), text: e.text };
    })
    .sort(function (a, b) {
      return b.pos - a.pos;
    })
    .forEach(function (e) {
      if (!e.cell) return;
      var para = schema.nodes.paragraph.create(null, e.text ? schema.text(e.text) : null);
      tr.replaceWith(e.pos + 1, e.pos + e.cell.nodeSize - 1, para);
    });
  if (tr.docChanged) editor.view.dispatch(tr);
}

function sumOf(values) {
  var nums = values.filter(Boolean);
  if (!nums.length) return "";
  var total = 0;
  var dec = 0;
  var comma = false;
  nums.forEach(function (n) {
    total += n.value;
    dec = Math.max(dec, n.decimals);
    comma = comma || n.comma;
  });
  return fmtNum(total, Math.min(dec, 4), comma);
}

function cellStartPos(table, tablePos, row, col) {
  var map = TableMap.get(table);
  var r = Math.max(0, Math.min(row, map.height - 1));
  var c = Math.max(0, Math.min(col, map.width - 1));
  return tablePos + 1 + map.positionAt(r, c, table);
}

function focusCell(editor, tablePos, row, col) {
  var table = editor.state.doc.nodeAt(tablePos);
  if (!table) return;
  var pos = cellStartPos(table, tablePos, row, col);
  var sel = TextSelection.near(editor.state.doc.resolve(pos + 1));
  editor.view.dispatch(editor.state.tr.setSelection(sel));
  editor.view.focus();
}

function selectLine(editor, tablePos, kind, index) {
  var table = editor.state.doc.nodeAt(tablePos);
  if (!table) return;
  var map = TableMap.get(table);
  var a;
  var b;
  if (kind === "col") {
    a = cellStartPos(table, tablePos, 0, index);
    b = cellStartPos(table, tablePos, map.height - 1, index);
  } else {
    a = cellStartPos(table, tablePos, index, 0);
    b = cellStartPos(table, tablePos, index, map.width - 1);
  }
  var doc = editor.state.doc;
  editor.view.dispatch(editor.state.tr.setSelection(new CellSelection(doc.resolve(a), doc.resolve(b))));
  editor.view.focus();
}


// Где курсор: таблица (pos), ячейка (row/col) и режим: «cell» — курсор в ячейке, «cells» — выделены ячейки,
// «col» / «row» — столбец / строка целиком, «table» — вся таблица.
function selectionCell(state) {
  var sel = state.selection;
  var $pos = sel.$headCell || sel.$from;
  for (var d = $pos.depth; d > 0; d -= 1) {
    var node = $pos.node(d);
    if (node.type.spec.tableRole !== "table") continue;
    var tablePos = $pos.before(d);
    var map = TableMap.get(node);
    var cellPos = null;
    if (sel.$headCell) {
      cellPos = sel.$headCell.pos - tablePos - 1;
    } else {
      for (var k = $pos.depth; k > d; k -= 1) {
        var role = $pos.node(k).type.spec.tableRole;
        if (role === "cell" || role === "header_cell") {
          cellPos = $pos.before(k) - tablePos - 1;
          break;
        }
      }
    }
    if (cellPos == null) return null;
    var rect;
    try {
      rect = map.findCell(cellPos);
    } catch (_) {
      return null;
    }
    var mode = "cell";
    var key = "";
    if (sel instanceof CellSelection) {
      var isCol = sel.isColSelection();
      var isRow = sel.isRowSelection();
      // Столбец / строка — только один целиком; несколько — диапазон ячеек (можно объединить).
      var a0 = map.findCell(sel.$anchorCell.pos - tablePos - 1);
      var oneCol = a0.left === rect.left;
      var oneRow = a0.top === rect.top;
      mode = isCol && isRow ? "table" : isCol && oneCol ? "col" : isRow && oneRow ? "row" : "cells";
      key = sel.$anchorCell.pos + ":" + sel.$headCell.pos;
    }
    return {
      tablePos: tablePos,
      cellPos: tablePos + 1 + cellPos,
      row: rect.top,
      col: rect.left,
      mode: mode,
      key: mode + ":" + rect.top + ":" + rect.left + ":" + key,
    };
  }
  return null;
}

function syncActiveTable(view) {
  var info = selectionCell(view.state);
  var focused = view.hasFocus() || allViewsHaveInteraction();
  allViews.forEach(function (v) {
    var mine = info && focused && v.tablePos() === info.tablePos ? info : null;
    v.setActive(mine);
  });
}

function allViewsHaveInteraction() {
  var now = Date.now();
  var busy = false;
  allViews.forEach(function (v) {
    if (v.interactUntil > now || v.drag || v.menuOpen) busy = true;
  });
  return busy;
}

// Ячейки выделения (CellSelection) или ячейка с курсором: [{node, pos}].
function selectedCells(state, info) {
  var sel = state.selection;
  var out = [];
  if (sel instanceof CellSelection) {
    sel.forEachCell(function (node, pos) {
      out.push({ node: node, pos: pos });
    });
  } else if (info) {
    var node = state.doc.nodeAt(info.cellPos);
    if (node) out.push({ node: node, pos: info.cellPos });
  }
  return out;
}

function cellAtPoint(view, x, y) {
  var el = document.elementFromPoint(x, y);
  var td = el && el.closest && el.closest("td, th");
  if (!td || !view.dom.contains(td)) return null;
  var pos;
  try {
    pos = view.posAtDOM(td, 0);
  } catch (_) {
    return null;
  }
  return cellAround(view.state.doc.resolve(pos));
}

// Телефон: выделено больше одной ячейки — клавиатура не нужна и закрывает таблицу. inputmode="none"
// и перефокус её прячут, фокус остаётся в редакторе (таблица и меню живут); вернулись в ячейку — обратно.
function setSoftKeyboard(view, show) {
  var dom = view.dom;
  var hidden = dom.getAttribute("inputmode") === "none";
  if (show ? !hidden : hidden) return;
  if (show) dom.removeAttribute("inputmode");
  else dom.setAttribute("inputmode", "none");
  if (document.activeElement === dom) {
    try {
      dom.blur();
      dom.focus({ preventScroll: true });
    } catch (_) {}
  }
}

function isCoarsePointer() {
  return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
}

function TableManageView(props, cellMinWidth) {
  this.editor = props.editor;
  this.getPos = props.getPos;
  this.node = props.node;
  this.cellMinWidth = cellMinWidth || MIN_COL;
  this.active = null;
  this.drag = null;
  this.menuOpen = false;
  this.menuDismissed = "";
  this.interactUntil = 0;
  this.ro = null;

  this.dom = document.createElement("div");
  this.dom.className = "note-table-manage-wrap";

  this.menu = document.createElement("div");
  this.menu.className = "note-table-menu";
  this.menu.contentEditable = "false";
  this.menu.setAttribute("role", "menu");
  this.menu.hidden = true;

  this.scroll = document.createElement("div");
  this.scroll.className = "note-table-manage-scroll";
  this.overlay = document.createElement("div");
  this.overlay.className = "note-table-manage-overlay";
  this.overlay.contentEditable = "false";
  this.overlay.hidden = true;

  this.table = document.createElement("table");
  var attrs = props.HTMLAttributes || {};
  this.table.className = attrs.class || "note-editor-table";
  Object.keys(attrs).forEach(function (key) {
    if (key === "class") return;
    if (attrs[key] != null && attrs[key] !== "") {
      this.table.setAttribute(key, String(attrs[key]));
    }
  }, this);

  this.colgroup = document.createElement("colgroup");
  this.contentDOM = document.createElement("tbody");
  this.table.appendChild(this.colgroup);
  this.table.appendChild(this.contentDOM);

  this.scroll.appendChild(this.table);
  this.dom.appendChild(this.overlay);
  this.dom.appendChild(this.menu);
  this.dom.appendChild(this.scroll);

  updateColumns(this.node, this.colgroup, this.table, this.cellMinWidth);
  this.bind();
  allViews.add(this);
}

TableManageView.prototype.tablePos = function () {
  try {
    var pos = this.getPos();
    if (typeof pos === "number" && pos >= 0) return pos;
  } catch (_) {}
  return null;
};

TableManageView.prototype.bind = function () {
  var self = this;
  // Нажатия по меню не должны уводить фокус из редактора (иначе пропадёт выделение).
  this.onUiMouseDown = function (e) {
    self.interactUntil = Date.now() + 800;
    e.preventDefault();
  };
  this.onUiClick = function (e) {
    var b = e.target.closest && e.target.closest("button[data-act]");
    if (!b || b.disabled) return;
    e.preventDefault();
    e.stopPropagation();
    self.interactUntil = Date.now() + 800;
    self.runAction(b.dataset.act);
  };
  this.menu.addEventListener("mousedown", this.onUiMouseDown);
  this.menu.addEventListener("click", this.onUiClick);

  this.onOverlayPointerDown = function (e) {
    self.handleOverlayPointerDown(e);
  };
  this.overlay.addEventListener("pointerdown", this.onOverlayPointerDown);
  this.overlay.addEventListener("mousedown", this.onUiMouseDown);
  this.onPointerMove = function (e) {
    self.handlePointerMove(e);
  };
  this.onPointerUp = function (e) {
    self.handlePointerUp(e);
  };
  this.onScroll = function () {
    self.layoutHandles();
  };
  this.scroll.addEventListener("scroll", this.onScroll, { passive: true });
  // Десктоп: граница столбца / строки под мышью — курсор ↔ / ↕, тянется (в любой момент, без выделения).
  this.onBorderHover = function (e) {
    if (e.pointerType !== "mouse" || self.drag || e.buttons) return;
    var b = self.borderAt(e.clientX, e.clientY);
    var k = b ? b.kind : "";
    if ((self.dom.dataset.resize || "") === k) return;
    if (k) self.dom.dataset.resize = k;
    else delete self.dom.dataset.resize;
  };
  this.onBorderLeave = function () {
    if (!self.drag) delete self.dom.dataset.resize;
  };
  this.onBorderDown = function (e) {
    if (e.pointerType !== "mouse" || e.button !== 0) return;
    var b = self.borderAt(e.clientX, e.clientY);
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    self.startResize(b.kind, b.index, e, self.scroll);
  };
  // ProseMirror ловит mousedown — пока тянем границу, до него не доходит (иначе выделял бы текст).
  this.onBorderMouseDown = function (e) {
    if (self.drag && /^resize-/.test(self.drag.kind)) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  this.scroll.addEventListener("pointermove", this.onBorderHover);
  this.scroll.addEventListener("pointerleave", this.onBorderLeave);
  this.scroll.addEventListener("pointerdown", this.onBorderDown, true);
  this.scroll.addEventListener("mousedown", this.onBorderMouseDown, true);
  // Меню держится у выделения в видимой области — при прокрутке заметки и клавиатуре двигаем его.
  this.onPageScroll = function () {
    if (self.active) self.layoutHandles();
  };

  if (typeof ResizeObserver !== "undefined") {
    var roRaf = 0;
    this.ro = new ResizeObserver(function () {
      // Раз за кадр: ручки таблицы двигают разметку, без этого наблюдатель зацикливается.
      if (roRaf) return;
      roRaf = requestAnimationFrame(function () {
        roRaf = 0;
        self.layoutHandles();
      });
    });
    this.ro.observe(this.dom);
    this.ro.observe(this.scroll);
    this.ro.observe(this.table);
  }
};

TableManageView.prototype.unbind = function () {
  this.menu.removeEventListener("mousedown", this.onUiMouseDown);
  this.menu.removeEventListener("click", this.onUiClick);
  this.overlay.removeEventListener("pointerdown", this.onOverlayPointerDown);
  this.overlay.removeEventListener("mousedown", this.onUiMouseDown);
  this.scroll.removeEventListener("scroll", this.onScroll);
  this.scroll.removeEventListener("pointermove", this.onBorderHover);
  this.scroll.removeEventListener("pointerleave", this.onBorderLeave);
  this.scroll.removeEventListener("pointerdown", this.onBorderDown, true);
  this.scroll.removeEventListener("mousedown", this.onBorderMouseDown, true);
  this.watchPageScroll(false);
  this.releaseDragListeners();
  if (this.ro) {
    this.ro.disconnect();
    this.ro = null;
  }
};

TableManageView.prototype.releaseDragListeners = function () {
  document.removeEventListener("pointermove", this.onPointerMove);
  document.removeEventListener("pointerup", this.onPointerUp);
  document.removeEventListener("pointercancel", this.onPointerUp);
};

TableManageView.prototype.colCount = function () {
  var pos = this.tablePos();
  var table = pos == null ? null : this.editor.state.doc.nodeAt(pos);
  return table ? TableMap.get(table).width : 0;
};

TableManageView.prototype.rowCount = function () {
  var pos = this.tablePos();
  var table = pos == null ? null : this.editor.state.doc.nodeAt(pos);
  return table ? TableMap.get(table).height : 0;
};

TableManageView.prototype.isFinePointer = function () {
  return !!(window.matchMedia && window.matchMedia("(pointer: fine)").matches);
};

TableManageView.prototype.setActive = function (info) {
  var prev = this.active;
  this.active = info;
  var on = !!info;
  this.dom.classList.toggle("is-active", on);
  this.watchPageScroll(on);
  if (!on) {
    this.cancelMenuTimer();
    this.closeMenu();
    this.menuDismissed = "";
    this.resizeMode = null;
    this.overlay.hidden = true;
    this.overlay.innerHTML = "";
    if (this.kbHidden) {
      this.kbHidden = false;
      this.editor.view.dom.removeAttribute("inputmode");
    }
    return;
  }
  activeView = this;
  if (isCoarsePointer()) {
    var multi = info.mode !== "cell";
    if (multi !== !!this.kbHidden) {
      this.kbHidden = multi;
      setSoftKeyboard(this.editor.view, !multi);
    }
  }
  if (this.resizeMode && prev && prev.key !== info.key) this.resizeMode = null;
  if (!prev || prev.key !== info.key || this.overlay.hidden) {
    this.renderOverlay();
    if (this.menuDismissed !== info.key) this.menuDismissed = "";
  }
  // Меню — когда выделены ячейки, столбец, строка или таблица (курсор в ячейке — только рамка),
  // и только когда выделение улеглось: палец отпущен и MENU_DELAY без изменений.
  if (info.mode !== "cell" && this.menuDismissed !== info.key && !this.resizeMode) {
    if (this.menuOpen && prev && prev.key === info.key) this.renderMenu();
    else this.scheduleMenu(info.key);
  } else {
    this.cancelMenuTimer();
    this.closeMenu();
  }
  this.layoutHandles();
};

TableManageView.prototype.cancelMenuTimer = function () {
  if (this.menuTimer) clearTimeout(this.menuTimer);
  this.menuTimer = 0;
};

TableManageView.prototype.scheduleMenu = function (key) {
  var self = this;
  this.cancelMenuTimer();
  this.closeMenu();
  this.menuTimer = setTimeout(function () {
    self.menuTimer = 0;
    var a = self.active;
    if (!a || a.key !== key || a.mode === "cell" || self.drag || pointerHeld || self.menuDismissed === key) return;
    self.openMenu();
    self.layoutHandles();
  }, MENU_DELAY);
};

TableManageView.prototype.watchPageScroll = function (on) {
  if (!!on === !!this.pageScrollOn) return;
  this.pageScrollOn = !!on;
  var fn = on ? "addEventListener" : "removeEventListener";
  document[fn]("scroll", this.onPageScroll, { capture: true, passive: true });
  if (window.visualViewport) window.visualViewport[fn]("resize", this.onPageScroll);
};

function menuItem(act, icon, label, opts) {
  opts = opts || {};
  return (
    '<button type="button" role="menuitem" class="note-table-menu-item' +
    (opts.danger ? " is-danger" : "") +
    '" data-act="' + act + '"' +
    (opts.disabled ? " disabled" : "") +
    ">" +
    (ICONS[icon] || "") +
    "<span>" + label + "</span></button>"
  );
}

function alignButton(act, label, on) {
  return (
    '<button type="button" class="note-table-align-btn' + (on ? " is-on" : "") + '" data-act="' + act + '"' +
    ' aria-label="' + label + '" title="' + label + '" aria-pressed="' + (on ? "true" : "false") + '">' +
    ICONS[act.replace(/^align-/, "")] +
    "</button>"
  );
}

// Меню как в Telegram: «Выравнивание» (по горизонтали и вертикали), «Заливка», затем действия режима.
TableManageView.prototype.renderMenu = function () {
  var a = this.active;
  if (!a) return;
  var state = this.editor.state;
  var cells = selectedCells(state, a);
  var same = function (name) {
    var first = cells.length ? cells[0].node.attrs[name] || null : null;
    return cells.every(function (c) {
      return (c.node.attrs[name] || null) === first;
    })
      ? first
      : "mixed";
  };
  var h = same("align");
  var v = same("valign");
  var lit = cells.length > 0 && same("highlight") === "1";
  var nCol = this.colCount();
  var nRow = this.rowCount();
  var html =
    '<p class="note-table-menu-label">Выравнивание</p>' +
    '<div class="note-table-align-row">' +
    alignButton("align-h-left", "По левому краю", h === null) +
    alignButton("align-h-center", "По центру", h === "center") +
    alignButton("align-h-right", "По правому краю", h === "right") +
    '<span class="note-table-align-gap"></span>' +
    alignButton("align-v-top", "По верху", v === null) +
    alignButton("align-v-middle", "По середине", v === "middle") +
    alignButton("align-v-bottom", "По низу", v === "bottom") +
    "</div>" +
    '<div class="note-table-menu-sep"></div>' +
    menuItem("highlight", "highlight", lit ? "Убрать заливку" : "Заливка");
  if (a.mode === "col") {
    html +=
      menuItem("col-add-left", "col-left", "Столбец слева") +
      menuItem("col-add-right", "col-right", "Столбец справа") +
      menuItem("resize-col", "width", "Ширина столбца") +
      menuItem("sort-asc", "sort-asc", "Сортировать А → Я") +
      menuItem("sort-desc", "sort-desc", "Сортировать Я → А") +
      menuItem("col-del", "delete", "Удалить столбец", { danger: true, disabled: nCol <= 1 });
  } else if (a.mode === "row") {
    html +=
      menuItem("row-add-above", "row-above", "Строка выше") +
      menuItem("row-add-below", "row-below", "Строка ниже") +
      menuItem("resize-row", "height", "Высота строки") +
      (a.row === 0 ? menuItem("toggle-header", "header", "Строка-заголовок") : "") +
      menuItem("row-del", "delete", "Удалить строку", { danger: true, disabled: nRow <= 1 });
  } else if (a.mode === "table") {
    html +=
      menuItem("toggle-header", "header", "Строка-заголовок") +
      menuItem("sum-rows", "sum", "Итого по строкам") +
      menuItem("sum-cols", "sum", "Итого по столбцам") +
      menuItem("ask-ai", "ai", "Попросить ИИ…") +
      menuItem("table-del", "delete", "Удалить таблицу", { danger: true });
  } else {
    var merged =
      cells.length === 1 && ((cells[0].node.attrs.colspan || 1) > 1 || (cells[0].node.attrs.rowspan || 1) > 1);
    if (merged) html += menuItem("split", "split", "Разделить ячейку");
    else html += menuItem("merge", "merge", "Объединить ячейки", { disabled: cells.length < 2 });
    html += menuItem("select-table", "table", "Вся таблица") + menuItem("ask-ai", "ai", "Попросить ИИ…");
  }
  this.menu.innerHTML = html;
};

TableManageView.prototype.openMenu = function () {
  if (!this.active) return;
  this.renderMenu();
  this.menu.hidden = false;
  this.menuOpen = true;
};

TableManageView.prototype.closeMenu = function () {
  if (!this.menuOpen && this.menu.hidden) return;
  this.menu.hidden = true;
  this.menu.innerHTML = "";
  this.menuOpen = false;
};

// Esc / нажатие мимо: меню прячется, пока выделение не сменится.
TableManageView.prototype.dismissMenu = function () {
  if (!this.menuOpen) return;
  this.menuDismissed = this.active ? this.active.key : "";
  this.closeMenu();
};

TableManageView.prototype.addColumn = function (index, before) {
  var pos = this.tablePos();
  if (pos == null) return;
  var widths = this.measureColWidths();
  var hasFixed = this.node.firstChild && this.node.firstChild.child(0).attrs.colwidth;
  runTableCommand(this.editor, pos, 0, index, before ? addColumnBefore : addColumnAfter);
  var after = this.tablePos();
  if (after != null && hasFixed) {
    widths.splice(before ? index : index + 1, 0, widths[index] || this.cellMinWidth);
    setAllColWidths(this.editor, after, widths);
  }
  var row = this.active ? this.active.row : 0;
  if (after != null) focusCell(this.editor, after, row, before ? index : index + 1);
};

TableManageView.prototype.addRow = function (index, before) {
  var pos = this.tablePos();
  if (pos == null) return;
  runTableCommand(this.editor, pos, index, 0, before ? addRowBefore : addRowAfter);
  var after = this.tablePos();
  var col = this.active ? this.active.col : 0;
  if (after != null) focusCell(this.editor, after, before ? index : index + 1, col);
};



// Атрибут всем ячейкам выделения (setCellAttr из prosemirror-tables смотрит только на первую).
TableManageView.prototype.setCellsAttr = function (name, value) {
  var state = this.editor.state;
  var tr = state.tr;
  selectedCells(state, this.active).forEach(function (c) {
    if ((c.node.attrs[name] || null) === value) return;
    var attrs = Object.assign({}, c.node.attrs);
    attrs[name] = value;
    tr.setNodeMarkup(c.pos, null, attrs);
  });
  if (tr.docChanged) this.editor.view.dispatch(tr);
  this.editor.view.focus();
};

TableManageView.prototype.selectTable = function () {
  var pos = this.tablePos();
  var table = pos == null ? null : this.editor.state.doc.nodeAt(pos);
  if (!table) return;
  var map = TableMap.get(table);
  var doc = this.editor.state.doc;
  var a = cellStartPos(table, pos, 0, 0);
  var b = cellStartPos(table, pos, map.height - 1, map.width - 1);
  this.editor.view.dispatch(this.editor.state.tr.setSelection(new CellSelection(doc.resolve(a), doc.resolve(b))));
  this.editor.view.focus();
};

TableManageView.prototype.runAction = function (act) {
  var a = this.active;
  var pos = this.tablePos();
  var editor = this.editor;
  var self = this;
  if (pos == null || !a) return;
  var dispatch = function (tr) {
    editor.view.dispatch(tr);
  };
  var al = /^align-(h|v)-(\w+)$/.exec(act);
  if (al) {
    var val = al[2] === "left" || al[2] === "top" ? null : al[2];
    this.setCellsAttr(al[1] === "h" ? "align" : "valign", val);
    return;
  }
  if (act === "highlight") {
    var cells = selectedCells(editor.state, a);
    var lit = cells.length > 0 && cells.every(function (c) {
      return !!c.node.attrs.highlight;
    });
    this.setCellsAttr("highlight", lit ? null : "1");
    return;
  }
  if (act === "merge" || act === "split") {
    (act === "merge" ? mergeCells : splitCell)(editor.state, dispatch);
    editor.view.focus();
    return;
  }
  if (act === "select-table") return this.selectTable();
  if (act === "resize-col" || act === "resize-row") {
    // Меню сворачивается, на границах появляются ручки — тянуть пальцем; «Готово» или нажатие мимо — выход.
    this.dismissMenu();
    this.resizeMode = act === "resize-col" ? "col" : "row";
    this.renderOverlay();
    this.layoutHandles();
    return;
  }
  if (act === "row-add-below") return this.addRow(a.row, false);
  if (act === "row-add-above") return this.addRow(a.row, true);
  if (act === "col-add-right") return this.addColumn(a.col, false);
  if (act === "col-add-left") return this.addColumn(a.col, true);
  if (act === "sum-rows") return this.sumRows();
  if (act === "sum-cols") return this.sumCols();
  if (act === "sort-asc" || act === "sort-desc") return this.sortBy(a.col, act === "sort-desc");
  if (act === "ask-ai") {
    this.dismissMenu();
    return this.askAi();
  }
  if (act === "toggle-header") {
    runTableCommand(editor, pos, 0, a.col, toggleHeaderRow);
    focusCell(editor, this.tablePos(), a.row, a.col);
    return;
  }
  if (act === "col-del" || act === "row-del") {
    // Строка и столбец удаляются сразу (есть «Отменить»), как в Telegram.
    (act === "col-del" ? deleteColumn : deleteRow)(editor.state, dispatch);
    var after = this.tablePos();
    if (after != null) {
      focusCell(
        editor,
        after,
        act === "row-del" ? Math.max(0, a.row - 1) : a.row,
        act === "col-del" ? Math.max(0, a.col - 1) : a.col
      );
    }
    return;
  }
  if (act === "table-del") {
    this.dismissMenu();
    confirmTableDelete("Удалить всю таблицу?").then(function (ok) {
      if (!ok) return;
      var p = self.tablePos();
      if (p == null) return;
      runTableCommand(editor, p, 0, 0, deleteTable);
    });
  }
};

TableManageView.prototype.sumRows = function () {
  var pos = this.tablePos();
  if (pos == null) return;
  var back = this.active ? { row: this.active.row, col: this.active.col } : { row: 0, col: 0 };
  var table = this.editor.state.doc.nodeAt(pos);
  var shape = tableShape(table);
  var col = shape.totalCol;
  if (col < 0) {
    runTableCommand(this.editor, pos, 0, shape.map.width - 1, addColumnAfter);
    pos = this.tablePos();
    table = this.editor.state.doc.nodeAt(pos);
    shape = tableShape(table);
    col = shape.map.width - 1;
  }
  var edits = [];
  if (shape.header) edits.push({ row: 0, col: col, text: "Итого" });
  shape.dataRows.concat(shape.totalRows).forEach(function (r) {
    var vals = [];
    for (var c = 1; c < shape.map.width; c += 1) if (c !== col) vals.push(parseNum(shape.text(r, c)));
    edits.push({ row: r, col: col, text: sumOf(vals) });
  });
  setCellTexts(this.editor, pos, edits);
  focusCell(this.editor, pos, back.row, back.col);
};

// Строка «Итого»: заполняем существующую (первая ячейка «Итого»/«Всего») или добавляем снизу.
TableManageView.prototype.sumCols = function () {
  var pos = this.tablePos();
  if (pos == null) return;
  var back = this.active ? { row: this.active.row, col: this.active.col } : { row: 0, col: 0 };
  var table = this.editor.state.doc.nodeAt(pos);
  var shape = tableShape(table);
  var row = shape.totalRows.length ? shape.totalRows[shape.totalRows.length - 1] : -1;
  if (row < 0) {
    runTableCommand(this.editor, pos, shape.map.height - 1, 0, addRowAfter);
    pos = this.tablePos();
    table = this.editor.state.doc.nodeAt(pos);
    row = TableMap.get(table).height - 1;
    shape = tableShape(table);
    shape.dataRows = shape.dataRows.filter(function (r) {
      return r !== row;
    });
  }
  var edits = [{ row: row, col: 0, text: "Итого" }];
  for (var c = 1; c < shape.map.width; c += 1) {
    var vals = shape.dataRows.map(function (r) {
      return parseNum(shape.text(r, c));
    });
    edits.push({ row: row, col: c, text: sumOf(vals) });
  }
  setCellTexts(this.editor, pos, edits);
  focusCell(this.editor, pos, back.row, back.col);
};

// Сортировка строк данных по столбцу; шапка остаётся сверху, строки «Итого» — снизу.
TableManageView.prototype.sortBy = function (col, desc) {
  var pos = this.tablePos();
  if (pos == null) return;
  var state = this.editor.state;
  var table = state.doc.nodeAt(pos);
  var shape = tableShape(table);
  var rows = [];
  table.forEach(function (row) {
    rows.push(row);
  });
  var numeric = shape.dataRows.every(function (r) {
    var t = shape.text(r, col).trim();
    return !t || parseNum(t);
  });
  var keyed = shape.dataRows.map(function (r) {
    var t = shape.text(r, col).trim();
    return { row: rows[r], t: t, n: parseNum(t) };
  });
  keyed.sort(function (a, b) {
    // Пустые — всегда в конце.
    if (!a.t && !b.t) return 0;
    if (!a.t) return 1;
    if (!b.t) return -1;
    var d = numeric ? a.n.value - b.n.value : a.t.localeCompare(b.t, "ru", { numeric: true, sensitivity: "base" });
    return desc ? -d : d;
  });
  var next = rows.slice(0, shape.header).concat(
    keyed.map(function (k) {
      return k.row;
    }),
    shape.totalRows.map(function (r) {
      return rows[r];
    })
  );
  var newTable = table.type.create(table.attrs, next, table.marks);
  this.editor.view.dispatch(state.tr.replaceWith(pos, pos + table.nodeSize, newTable));
  var a = this.active;
  focusCell(this.editor, pos, shape.header, a ? a.col : col);
};

// «Попросить ИИ»: приложение откроет обсуждение с контекстом таблицы (событие ловит app.js).
TableManageView.prototype.askAi = function () {
  var pos = this.tablePos();
  if (pos == null) return;
  var table = this.editor.state.doc.nodeAt(pos);
  var shape = tableShape(table);
  var heads = [];
  for (var c = 0; c < shape.map.width; c += 1) heads.push(shape.text(0, c).trim());
  var n = 0;
  var idx = 0;
  this.editor.state.doc.descendants(function (node, p) {
    if (node.type.spec.tableRole === "table") {
      n += 1;
      if (p === pos) idx = n;
    }
  });
  window.dispatchEvent(
    new CustomEvent("leo-table-ask-ai", {
      detail: { index: idx, headers: heads.filter(Boolean).slice(0, 8), column: this.active ? heads[this.active.col] || "" : "" },
    })
  );
};

TableManageView.prototype.measureColWidths = function () {
  var row = this.table.rows[0];
  var out = [];
  if (!row) return out;
  for (var i = 0; i < row.cells.length; i += 1) {
    out.push(row.cells[i].getBoundingClientRect().width);
  }
  return out;
};

TableManageView.prototype.measureRowHeight = function (index) {
  var row = this.table.rows[index];
  return row ? row.getBoundingClientRect().height : MIN_ROW;
};


// Рамка выделения с точками по краям, «⋮» у строки слева, «•••» под выделением —
// как в Telegram. Точку можно тянуть (выделить несколько ячеек), нажатие на точку выделяет ячейку.
// На десктопе ширину столбца меняет полоса по правой границе столбца.
TableManageView.prototype.renderOverlay = function () {
  var a = this.active;
  this.overlay.innerHTML = "";
  if (!a) {
    this.overlay.hidden = true;
    return;
  }
  this.overlay.hidden = false;
  var frame = document.createElement("div");
  frame.className = "note-table-frame note-table-frame--" + a.mode;
  frame.dataset.part = "frame";
  this.overlay.appendChild(frame);
  if (a.mode === "cell" || a.mode === "cells") {
    ["t", "r", "b", "l"].forEach(function (side) {
      var dot = makeButton("note-table-dot note-table-dot--" + side, "Выделить ячейки", "");
      dot.dataset.role = "extend";
      dot.dataset.side = side;
      this.overlay.appendChild(dot);
    }, this);
  }
  var rowGrip = makeButton(
    "note-table-handle note-table-handle--row" + (a.mode === "row" || a.mode === "table" ? " is-selected" : ""),
    "Строка: действия (перетащите, чтобы переместить)",
    ICON_DOTS
  );
  rowGrip.dataset.role = "move-row";
  rowGrip.dataset.index = String(a.row);
  this.overlay.appendChild(rowGrip);
  var colGrip = makeButton(
    "note-table-handle note-table-handle--col" + (a.mode === "col" ? " is-selected" : ""),
    "Столбец: действия (перетащите, чтобы переместить)",
    ICON_MORE
  );
  colGrip.dataset.role = "move-col";
  colGrip.dataset.index = String(a.col);
  this.overlay.appendChild(colGrip);
  if (this.resizeMode) this.renderResizeGrips();
};

// Режим «Ширина столбца» / «Высота строки» (телефон): ручка на каждой границе и «Готово».
TableManageView.prototype.renderResizeGrips = function () {
  var kind = this.resizeMode;
  var g = this.gridLines();
  if (!g) return;
  var lines = kind === "col" ? g.cols : g.rows;
  for (var i = 0; i < lines.length; i += 1) {
    if (lines[i] == null) continue;
    var grip = document.createElement("div");
    grip.className = "note-table-grip note-table-grip--" + kind;
    grip.dataset.role = kind === "col" ? "resize-col" : "resize-row";
    grip.dataset.index = String(i);
    grip.innerHTML = '<span class="note-table-grip-knob"></span>';
    this.overlay.appendChild(grip);
  }
  var done = makeButton("note-table-resize-done", "Готово", "Готово");
  done.dataset.role = "resize-done";
  done.dataset.index = "0";
  this.overlay.appendChild(done);
};

// Границы по TableMap (с объединёнными ячейками): x правого края столбца c, y нижнего края строки r — в координатах окна.
TableManageView.prototype.gridLines = function () {
  var pos = this.tablePos();
  var table = pos == null ? null : this.editor.state.doc.nodeAt(pos);
  if (!table) return null;
  var map = TableMap.get(table);
  var view = this.editor.view;
  var cols = new Array(map.width);
  var rows = new Array(map.height);
  var seen = {};
  for (var i = 0; i < map.map.length; i += 1) {
    var rel = map.map[i];
    if (seen[rel]) continue;
    seen[rel] = true;
    var el = view.nodeDOM(pos + 1 + rel);
    if (!el || !el.getBoundingClientRect) continue;
    var rect = map.findCell(rel);
    var b = el.getBoundingClientRect();
    cols[rect.right - 1] = b.right;
    rows[rect.bottom - 1] = b.bottom;
  }
  var tb = this.table.getBoundingClientRect();
  return { cols: cols, rows: rows, left: tb.left, top: tb.top };
};

TableManageView.prototype.colWidthsFromGrid = function () {
  var g = this.gridLines();
  if (!g) return this.measureColWidths();
  var out = [];
  var prev = g.left;
  for (var c = 0; c < g.cols.length; c += 1) {
    var x = g.cols[c] != null ? g.cols[c] : prev + this.cellMinWidth;
    out.push(Math.max(this.cellMinWidth, x - prev));
    prev = x;
  }
  return out;
};

// Граница под мышью (±4 px): столбцы важнее строк; внешние левая и верхняя — нет.
TableManageView.prototype.borderAt = function (x, y) {
  var tb = this.table.getBoundingClientRect();
  var sb = this.scroll.getBoundingClientRect();
  if (y < tb.top - 3 || y > tb.bottom + 4 || x < Math.max(tb.left, sb.left) - 3 || x > Math.min(tb.right, sb.right) + 4) return null;
  var g = this.gridLines();
  if (!g) return null;
  var best = null;
  g.cols.forEach(function (cx, c) {
    if (cx == null) return;
    var d = Math.abs(x - cx);
    if (d <= 4 && (!best || d < best.d)) best = { kind: "col", index: c, d: d };
  });
  if (best) return best;
  g.rows.forEach(function (ry, r) {
    if (ry == null) return;
    var d = Math.abs(y - ry);
    if (d <= 4 && (!best || d < best.d)) best = { kind: "row", index: r, d: d };
  });
  return best;
};

TableManageView.prototype.startResize = function (kind, index, e, target) {
  var row = this.table.rows[index];
  this.drag = {
    kind: kind === "col" ? "resize-col" : "resize-row",
    index: index,
    startX: e.clientX,
    startY: e.clientY,
    startWidths: kind === "col" ? this.colWidthsFromGrid() : null,
    startH: kind === "row" && row ? row.getBoundingClientRect().height : 0,
    moved: false,
    pointerId: e.pointerId,
    target: target,
  };
  this.interactUntil = Date.now() + 800;
  try {
    target.setPointerCapture(e.pointerId);
  } catch (_) {}
  document.addEventListener("pointermove", this.onPointerMove);
  document.addEventListener("pointerup", this.onPointerUp);
  document.addEventListener("pointercancel", this.onPointerUp);
};

// Прямоугольник выделения в координатах окна: объединение ячеек (или ячейка с курсором).
TableManageView.prototype.selectionRect = function () {
  var view = this.editor.view;
  var cells = selectedCells(view.state, this.active);
  var r = null;
  cells.forEach(function (c) {
    var el = view.nodeDOM(c.pos);
    if (!el || !el.getBoundingClientRect) return;
    var b = el.getBoundingClientRect();
    if (!r) r = { left: b.left, top: b.top, right: b.right, bottom: b.bottom };
    else {
      r.left = Math.min(r.left, b.left);
      r.top = Math.min(r.top, b.top);
      r.right = Math.max(r.right, b.right);
      r.bottom = Math.max(r.bottom, b.bottom);
    }
  });
  return r;
};

function place(el, left, top, width, height) {
  if (!el) return;
  el.style.left = Math.round(left) + "px";
  el.style.top = Math.round(top) + "px";
  if (width != null) el.style.width = Math.round(width) + "px";
  if (height != null) el.style.height = Math.round(height) + "px";
}

TableManageView.prototype.layoutHandles = function () {
  var a = this.active;
  if (!a || this.overlay.hidden) return;
  var box = this.dom.getBoundingClientRect();
  var sb = this.scroll.getBoundingClientRect();
  var tb = this.table.getBoundingClientRect();
  var r = this.selectionRect();
  if (!r) return;
  // Видимая по горизонтали часть (таблица шире экрана прокручивается вбок).
  var visL = Math.max(sb.left, tb.left);
  var visR = Math.min(sb.right, tb.right);
  var L = Math.max(r.left, visL) - box.left;
  var R = Math.min(r.right, visR) - box.left;
  var T = r.top - box.top;
  var B = r.bottom - box.top;
  var shown = R > L + 4;
  var q = function (sel) {
    return this.overlay.querySelector(sel);
  }.bind(this);
  var frame = q('[data-part="frame"]');
  if (frame) {
    place(frame, L, T, Math.max(0, R - L), B - T);
    frame.style.visibility = shown ? "visible" : "hidden";
    // Край у прокрутки — без скругления (выделение уходит за край).
    frame.classList.toggle("is-cut-left", r.left < visL - 1);
    frame.classList.toggle("is-cut-right", r.right > visR + 1);
  }
  var midX = (L + R) / 2;
  var midY = (T + B) / 2;
  var dots = { t: [midX, T], b: [midX, B], l: [L, midY], r: [R, midY] };
  this.overlay.querySelectorAll(".note-table-dot").forEach(function (d) {
    var p = dots[d.dataset.side];
    place(d, p[0], p[1]);
    var cut = (d.dataset.side === "l" && r.left < visL - 1) || (d.dataset.side === "r" && r.right > visR + 1);
    d.style.visibility = shown && !cut ? "visible" : "hidden";
  });
  // «⋮» — у левого края таблицы на уровне выделения; у выделенной строки/таблицы — синяя вкладка во всю высоту.
  var rowGrip = q('[data-role="move-row"]');
  if (rowGrip) {
    var tabLeft = visL - box.left;
    if (a.mode === "row" || a.mode === "table") place(rowGrip, tabLeft, T, null, B - T);
    else {
      place(rowGrip, tabLeft, midY, null, null);
      rowGrip.style.height = "";
    }
  }
  // «•••» — под выделением; у выделенного столбца — синяя вкладка во всю ширину.
  var colGrip = q('[data-role="move-col"]');
  if (colGrip) {
    if (a.mode === "col") place(colGrip, L, B, Math.max(0, R - L), null);
    else {
      place(colGrip, midX, B, null, null);
      colGrip.style.width = "";
    }
    colGrip.style.visibility = shown ? "visible" : "hidden";
  }
  if (this.resizeMode) {
    var g = this.gridLines();
    var vis = { l: visL - box.left, r: visR - box.left };
    this.overlay.querySelectorAll(".note-table-grip").forEach(function (grip) {
      var i = parseInt(grip.dataset.index, 10);
      if (!g) return;
      if (grip.classList.contains("note-table-grip--col")) {
        var x = g.cols[i] - box.left;
        place(grip, x, tb.top - box.top, null, tb.height);
        grip.style.visibility = x >= vis.l - 1 && x <= vis.r + 1 ? "visible" : "hidden";
      } else {
        place(grip, vis.l, g.rows[i] - box.top, vis.r - vis.l, null);
      }
    });
    var done = q('[data-role="resize-done"]');
    if (done) place(done, Math.max(vis.l, vis.r - (done.offsetWidth || 80)), tb.top - box.top - (done.offsetHeight || 32) - 8);
  }
  this.layoutMenu(r);
};

// Видимая по вертикали полоса экрана (с учётом прокручиваемых предков и клавиатуры) — в координатах окна.
function visibleBand(el) {
  var vv = window.visualViewport;
  var top = vv ? vv.offsetTop : 0;
  var bottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
  for (var n = el.parentElement; n && n !== document.body; n = n.parentElement) {
    var oy = getComputedStyle(n).overflowY;
    if (oy === "auto" || oy === "scroll" || oy === "hidden") {
      var r = n.getBoundingClientRect();
      top = Math.max(top, r.top);
      bottom = Math.min(bottom, r.bottom);
    }
  }
  return { top: top, bottom: bottom };
}

// Меню — под выделением (ниже ручки «•••»), если внизу тесно — над ним; по левому краю выделения.
TableManageView.prototype.layoutMenu = function (r) {
  if (this.menu.hidden || !r) return;
  var box = this.dom.getBoundingClientRect();
  var band = visibleBand(this.dom);
  var gap = 10;
  var menuH = this.menu.offsetHeight;
  var menuW = this.menu.offsetWidth;
  var below = r.bottom + 22;
  var above = r.top - gap - menuH;
  var top;
  if (below + menuH <= band.bottom - gap) top = below;
  else if (above >= band.top + gap) top = above;
  else top = Math.max(band.top + gap, Math.min(below, band.bottom - gap - menuH));
  var vw = document.documentElement.clientWidth || window.innerWidth;
  var left = Math.max(r.left, 8);
  left = Math.min(left, vw - menuW - 8);
  this.menu.style.top = Math.round(top - box.top) + "px";
  this.menu.style.left = Math.round(left - box.left) + "px";
};

TableManageView.prototype.handleOverlayPointerDown = function (e) {
  if (e.button != null && e.button !== 0) return;
  var btn = e.target.closest && e.target.closest("[data-role]");
  if (!btn || !this.overlay.contains(btn)) return;
  e.preventDefault();
  e.stopPropagation();
  this.interactUntil = Date.now() + 800;
  var role = btn.dataset.role;
  var index = parseInt(btn.dataset.index, 10);
  if (role === "resize-done") {
    this.resizeMode = null;
    this.renderOverlay();
    this.layoutHandles();
    return;
  }
  if (role === "resize-col" || role === "resize-row") {
    this.startResize(role === "resize-col" ? "col" : "row", index, e, btn);
    return;
  }
  if (role !== "extend" && !isFinite(index)) return;
  var sel = this.editor.state.selection;
  this.drag = {
    kind: role,
    index: index,
    startX: e.clientX,
    startY: e.clientY,
    startWidths: null,
    anchor: sel instanceof CellSelection ? sel.$anchorCell.pos : this.active ? this.active.cellPos : null,
    moved: false,
    pointerId: e.pointerId,
    target: btn,
  };
  try {
    btn.setPointerCapture(e.pointerId);
  } catch (_) {}
  document.addEventListener("pointermove", this.onPointerMove);
  document.addEventListener("pointerup", this.onPointerUp);
  document.addEventListener("pointercancel", this.onPointerUp);
};

TableManageView.prototype.handlePointerMove = function (e) {
  if (!this.drag) return;
  if (this.drag.pointerId != null && e.pointerId !== this.drag.pointerId) return;
  var d = dist(e.clientX, e.clientY, this.drag.startX, this.drag.startY);
  if (!this.drag.moved) {
    if (d < (/^resize-/.test(this.drag.kind) ? 2 : DRAG_THRESHOLD)) return;
    this.drag.moved = true;
    this.closeMenu();
    this.drag.target.classList.add("is-dragging");
    if (this.drag.kind === "resize-col" && this.drag.startWidths) {
      applyPreviewWidths(this.colgroup, this.table, this.drag.startWidths.slice(), this.cellMinWidth);
    }
  }
  e.preventDefault();
  this.interactUntil = Date.now() + 800;
  var kind = this.drag.kind;
  if (kind === "resize-col") this.dragResizeCol(e);
  else if (kind === "resize-row") this.dragResizeRow(e);
  else if (kind === "move-col") this.dragMoveCol(e);
  else if (kind === "move-row") this.dragMoveRow(e);
  else if (kind === "extend") this.dragExtend(e);
};

// Тянем точку рамки: выделение от исходной ячейки до ячейки под пальцем.
TableManageView.prototype.dragExtend = function (e) {
  if (this.drag.anchor == null) return;
  var view = this.editor.view;
  var $head = cellAtPoint(view, e.clientX, e.clientY);
  if (!$head) return;
  var doc = view.state.doc;
  var sel = view.state.selection;
  if (sel instanceof CellSelection && sel.$anchorCell.pos === this.drag.anchor && sel.$headCell.pos === $head.pos) return;
  try {
    view.dispatch(view.state.tr.setSelection(new CellSelection(doc.resolve(this.drag.anchor), $head)));
  } catch (_) {}
};

TableManageView.prototype.dragResizeCol = function (e) {
  var widths = this.drag.startWidths.slice();
  var dx = e.clientX - this.drag.startX;
  widths[this.drag.index] = Math.max(this.cellMinWidth, this.drag.startWidths[this.drag.index] + dx);
  this.drag.currentWidths = widths;
  applyPreviewWidths(this.colgroup, this.table, widths, this.cellMinWidth);
  this.layoutHandles();
};

TableManageView.prototype.dragResizeRow = function (e) {
  var h = Math.max(MIN_ROW, Math.round(this.drag.startH + (e.clientY - this.drag.startY)));
  this.drag.currentH = h;
  var row = this.table.rows[this.drag.index];
  if (row) row.style.height = h + "px";
  this.layoutHandles();
};

TableManageView.prototype.colMidX = function (index) {
  var row = this.table.rows[0];
  if (!row || !row.cells[index]) return 0;
  var r = row.cells[index].getBoundingClientRect();
  return r.left + r.width / 2;
};

TableManageView.prototype.rowMidY = function (index) {
  var row = this.table.rows[index];
  if (!row) return 0;
  var r = row.getBoundingClientRect();
  return r.top + r.height / 2;
};

TableManageView.prototype.dragMoveCol = function (e) {
  var pos = this.tablePos();
  if (pos == null) return;
  var i = this.drag.index;
  var n = this.colCount();
  var to = null;
  if (i < n - 1 && e.clientX > this.colMidX(i + 1)) to = i + 1;
  else if (i > 0 && e.clientX < this.colMidX(i - 1)) to = i - 1;
  if (to == null) return;
  var editor = this.editor;
  var ok = moveTableColumn({ from: i, to: to, select: false, pos: pos + 1 })(editor.state, function (tr) {
    editor.view.dispatch(tr);
  });
  if (ok) {
    this.drag.index = to;
    this.layoutHandles();
  }
};

TableManageView.prototype.dragMoveRow = function (e) {
  var pos = this.tablePos();
  if (pos == null) return;
  var i = this.drag.index;
  var n = this.rowCount();
  var to = null;
  if (i < n - 1 && e.clientY > this.rowMidY(i + 1)) to = i + 1;
  else if (i > 0 && e.clientY < this.rowMidY(i - 1)) to = i - 1;
  if (to == null) return;
  var editor = this.editor;
  var ok = moveTableRow({ from: i, to: to, select: false, pos: pos + 1 })(editor.state, function (tr) {
    editor.view.dispatch(tr);
  });
  if (ok) {
    this.drag.index = to;
    this.layoutHandles();
  }
};


TableManageView.prototype.handlePointerUp = function (e) {
  if (!this.drag) return;
  if (this.drag.pointerId != null && e.pointerId !== this.drag.pointerId) return;
  var drag = this.drag;
  this.drag = null;
  this.releaseDragListeners();
  if (drag.target) {
    drag.target.classList.remove("is-dragging");
    try {
      drag.target.releasePointerCapture(drag.pointerId);
    } catch (_) {}
  }
  var pos = this.tablePos();
  if (pos == null) return;
  var view = this.editor.view;
  if (!/^resize-/.test(drag.kind)) this.menuDismissed = "";
  if (drag.moved) {
    // Без фокуса в редактор: фокус прокручивал заметку к курсору, а тянули границу совсем в другом месте.
    if (drag.kind === "resize-col" && drag.currentWidths) {
      setAllColWidths(this.editor, pos, drag.currentWidths);
    } else if (drag.kind === "resize-row" && drag.currentH) {
      setRowHeight(this.editor, pos, drag.index, drag.currentH);
    } else if (drag.kind === "move-col") {
      selectLine(this.editor, pos, "col", drag.index);
    } else if (drag.kind === "move-row") {
      selectLine(this.editor, pos, "row", drag.index);
    } else {
      syncActiveTable(view);
    }
    return;
  }
  // Нажатие на точку — выделить ячейку (появится меню); выделение уже есть — снова показать меню.
  if (drag.kind === "extend") {
    var sel = view.state.selection;
    if (!(sel instanceof CellSelection) && drag.anchor != null) {
      var $cell = view.state.doc.resolve(drag.anchor);
      view.dispatch(view.state.tr.setSelection(new CellSelection($cell, $cell)));
    }
    view.focus();
    syncActiveTable(view);
    return;
  }
  // Нажатие на «⋮» / «•••»: выделить строку / столбец (повторное — снять выделение).
  if (drag.kind === "move-col" || drag.kind === "move-row") {
    var kind = drag.kind === "move-col" ? "col" : "row";
    if (this.active && this.active.mode === kind) focusCell(this.editor, pos, this.active.row, this.active.col);
    else selectLine(this.editor, pos, kind, drag.index);
  }
};

TableManageView.prototype.update = function (node) {
  if (node.type !== this.node.type) return false;
  this.node = node;
  if (!this.drag || this.drag.kind !== "resize-col") {
    updateColumns(node, this.colgroup, this.table, this.cellMinWidth);
  }
  var self = this;
  requestAnimationFrame(function () {
    self.layoutHandles();
  });
  return true;
};

TableManageView.prototype.ignoreMutation = function (mutation) {
  if (!mutation || !mutation.target) return false;
  var t = mutation.target;
  if (t === this.dom || t === this.scroll) return true;
  if (this.overlay.contains(t) || this.menu.contains(t)) return true;
  if (mutation.type === "attributes") {
    if (t === this.table) return true;
    if (this.colgroup.contains(t)) return true;
    if (t.nodeName === "TR" || t.nodeName === "COL") return true;
    if ((t.nodeName === "TD" || t.nodeName === "TH") && mutation.attributeName === "style") return true;
  }
  return false;
};

TableManageView.prototype.stopEvent = function (event) {
  var t = event.target;
  if (!t || !t.closest) return false;
  return !!t.closest(".note-table-menu, .note-table-manage-overlay");
};

TableManageView.prototype.destroy = function () {
  this.cancelMenuTimer();
  allViews.delete(this);
  if (activeView === this) {
    activeView = null;
    closeConfirm(false);
  }
  this.unbind();
};

function createTableManagePlugin() {
  return new Plugin({
    key: tableManageKey,
    view: function (editorView) {
      var blurTimer = null;
      function onKey(e) {
        if (e.key !== "Escape") return;
        if (confirmCloser) {
          e.preventDefault();
          closeConfirm(false);
          return;
        }
        if (activeView && activeView.menuOpen) {
          e.preventDefault();
          activeView.dismissMenu();
        }
      }
      function onPress(e) {
        var t = e.target;
        if (!t || !editorView.dom.contains(t) || (t.closest && t.closest(".note-table-menu"))) return;
        pointerHeld = true;
      }
      function onRelease() {
        if (!pointerHeld) return;
        pointerHeld = false;
        // Отпустили — меню появится, когда выделение перестанет меняться.
        setTimeout(function () {
          syncActiveTable(editorView);
        }, 0);
      }
      function onFocus() {
        clearTimeout(blurTimer);
        syncActiveTable(editorView);
      }
      function onBlur() {
        clearTimeout(blurTimer);
        blurTimer = setTimeout(function () {
          syncActiveTable(editorView);
        }, 200);
      }
      document.addEventListener("keydown", onKey, true);
      document.addEventListener("pointerdown", onPress, true);
      document.addEventListener("pointerup", onRelease, true);
      document.addEventListener("pointercancel", onRelease, true);
      editorView.dom.addEventListener("focus", onFocus);
      editorView.dom.addEventListener("blur", onBlur);
      setTimeout(function () {
        syncActiveTable(editorView);
      }, 0);
      return {
        update: function (view) {
          syncActiveTable(view);
        },
        destroy: function () {
          clearTimeout(blurTimer);
          document.removeEventListener("keydown", onKey, true);
          document.removeEventListener("pointerdown", onPress, true);
          document.removeEventListener("pointerup", onRelease, true);
          document.removeEventListener("pointercancel", onRelease, true);
          pointerHeld = false;
          editorView.dom.removeEventListener("focus", onFocus);
          editorView.dom.removeEventListener("blur", onBlur);
          closeConfirm(false);
          activeView = null;
        },
      };
    },
  });
}

export const ManagedTable = Table.extend({
  addNodeView() {
    var minWidth = this.options.cellMinWidth || MIN_COL;
    return function (props) {
      return new TableManageView(props, minWidth);
    };
  },
  addProseMirrorPlugins() {
    var plugins = this.parent ? this.parent() : [];
    return plugins.concat([createTableManagePlugin()]);
  },
});
