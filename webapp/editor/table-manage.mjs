import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import {
  CellSelection,
  TableMap,
  addColumnAfter,
  addColumnBefore,
  addRowAfter,
  addRowBefore,
  deleteColumn,
  deleteRow,
  deleteTable,
  moveTableColumn,
  moveTableRow,
  toggleHeaderRow,
} from "@tiptap/pm/tables";

var tableManageKey = new PluginKey("tableManage");
var MIN_COL = 48;
var MIN_ROW = 32;
var DRAG_THRESHOLD = 8;
var HANDLE = 22;
var activeView = null;
var confirmCloser = null;
// Все таблицы редактора: активной становится та, где стоит курсор.
var allViews = new Set();

var ICON_MORE =
  '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
  '<circle cx="4" cy="10" r="1.8" fill="currentColor"/>' +
  '<circle cx="10" cy="10" r="1.8" fill="currentColor"/>' +
  '<circle cx="16" cy="10" r="1.8" fill="currentColor"/>' +
  "</svg>";

function arrowIcon(dir) {
  var d = { left: "M12 4 6 10l6 6", right: "M8 4l6 6-6 6", up: "M4 12l6-6 6 6", down: "M4 8l6 6 6-6" }[dir];
  return (
    '<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="' +
    d +
    '" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  );
}

var ICON_DOTS =
  '<svg viewBox="0 0 12 20" width="10" height="16" aria-hidden="true">' +
  '<circle cx="6" cy="3" r="1.7" fill="currentColor"/>' +
  '<circle cx="6" cy="10" r="1.7" fill="currentColor"/>' +
  '<circle cx="6" cy="17" r="1.7" fill="currentColor"/>' +
  "</svg>";

var ICON_GRIP =
  '<svg viewBox="0 0 10 16" width="8" height="14" aria-hidden="true">' +
  '<circle cx="3" cy="3" r="1.35" fill="currentColor"/>' +
  '<circle cx="7" cy="3" r="1.35" fill="currentColor"/>' +
  '<circle cx="3" cy="8" r="1.35" fill="currentColor"/>' +
  '<circle cx="7" cy="8" r="1.35" fill="currentColor"/>' +
  '<circle cx="3" cy="13" r="1.35" fill="currentColor"/>' +
  '<circle cx="7" cy="13" r="1.35" fill="currentColor"/>' +
  "</svg>";

var ICON_RESIZE =
  '<svg viewBox="0 0 16 10" width="14" height="10" aria-hidden="true">' +
  '<path d="M5 1 1 5l4 4M11 1l4 4-4 4" fill="none" stroke="currentColor" ' +
  'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
  "</svg>";

var ICON_PLUS =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
  '<path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' +
  "</svg>";

var ICON_TRASH =
  '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>' +
  '<path d="M10 11v6M14 11v6"/>' +
  "</svg>";

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
  editor.view.dispatch(tr);
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
  editor.view.dispatch(tr);
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


// ---- Управление таблицей без отдельного режима ----
// Курсор в ячейке → панель над таблицей («+ строка», «+ столбец», «⋯»), «+» по правому и нижнему краю,
// ручки только у текущего столбца и строки. Нажатие на ручку выделяет столбец/строку целиком —
// панель переключается на «← →» / «↑ ↓», «+», «Удалить». На десктопе ручку можно перетаскивать.

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

function closeAllMenus() {
  allViews.forEach(function (v) {
    v.closeMenu();
  });
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

// Где курсор: таблица (pos) и ячейка (row/col); «col»/«row», если выделен столбец/строка целиком.
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
    if (sel instanceof CellSelection) {
      if (sel.isColSelection() && !sel.isRowSelection()) mode = "col";
      else if (sel.isRowSelection() && !sel.isColSelection()) mode = "row";
    }
    return { tablePos: tablePos, row: rect.top, col: rect.left, mode: mode };
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

function TableManageView(props, cellMinWidth) {
  this.editor = props.editor;
  this.getPos = props.getPos;
  this.node = props.node;
  this.cellMinWidth = cellMinWidth || MIN_COL;
  this.active = null;
  this.drag = null;
  this.menuOpen = false;
  this.interactUntil = 0;
  this.ro = null;

  this.dom = document.createElement("div");
  this.dom.className = "note-table-manage-wrap";

  this.bar = document.createElement("div");
  this.bar.className = "note-table-bar";
  this.bar.contentEditable = "false";
  this.bar.hidden = true;
  this.menu = document.createElement("div");
  this.menu.className = "note-table-menu";
  this.menu.contentEditable = "false";
  this.menu.setAttribute("role", "menu");
  this.menu.hidden = true;

  this.scroll = document.createElement("div");
  this.scroll.className = "note-table-manage-scroll";
  this.overlay = document.createElement("div");
  this.overlay.className = "note-table-manage-overlay";
  this.overlay.hidden = true;

  this.addColBtn = makeButton("note-table-edge note-table-edge--col", "Добавить столбец в конец", ICON_PLUS);
  this.addRowBtn = makeButton("note-table-edge note-table-edge--row", "Добавить строку в конец", ICON_PLUS);
  this.addColBtn.dataset.act = "edge-col";
  this.addRowBtn.dataset.act = "edge-row";

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
  this.dom.appendChild(this.bar);
  this.dom.appendChild(this.menu);
  this.dom.appendChild(this.overlay);
  this.dom.appendChild(this.scroll);
  this.dom.appendChild(this.addColBtn);
  this.dom.appendChild(this.addRowBtn);

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
  // Нажатия по панели/меню/«+» не должны уводить фокус из редактора (иначе пропадёт курсор).
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
  [this.bar, this.menu, this.addColBtn, this.addRowBtn].forEach(function (el) {
    el.addEventListener("mousedown", self.onUiMouseDown);
    el.addEventListener("click", self.onUiClick);
  });

  this.onOverlayPointerDown = function (e) {
    self.handleOverlayPointerDown(e);
  };
  this.overlay.addEventListener("pointerdown", this.onOverlayPointerDown);
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

  if (typeof ResizeObserver !== "undefined") {
    this.ro = new ResizeObserver(function () {
      self.layoutHandles();
    });
    this.ro.observe(this.dom);
    this.ro.observe(this.scroll);
    this.ro.observe(this.table);
  }
};

TableManageView.prototype.unbind = function () {
  var self = this;
  [this.bar, this.menu, this.addColBtn, this.addRowBtn].forEach(function (el) {
    el.removeEventListener("mousedown", self.onUiMouseDown);
    el.removeEventListener("click", self.onUiClick);
  });
  this.overlay.removeEventListener("pointerdown", this.onOverlayPointerDown);
  this.scroll.removeEventListener("scroll", this.onScroll);
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
  var row = this.table.rows[0];
  return row ? row.cells.length : 0;
};

TableManageView.prototype.rowCount = function () {
  return this.table.rows.length;
};

TableManageView.prototype.isFinePointer = function () {
  return !!(window.matchMedia && window.matchMedia("(pointer: fine)").matches);
};

TableManageView.prototype.setActive = function (info) {
  var prev = this.active;
  this.active = info;
  var on = !!info;
  this.dom.classList.toggle("is-active", on);
  if (!on) {
    this.bar.hidden = true;
    this.closeMenu();
    this.overlay.hidden = true;
    this.overlay.innerHTML = "";
    return;
  }
  activeView = this;
  var same =
    prev && prev.mode === info.mode && prev.row === info.row && prev.col === info.col && !this.bar.hidden;
  if (!same) {
    this.renderBar();
    this.renderOverlay();
  } else {
    this.layoutHandles();
  }
};

function barButton(act, label, html, extraClass) {
  var b = makeButton("note-table-bar-btn" + (extraClass ? " " + extraClass : ""), label, html);
  b.dataset.act = act;
  b.title = label;
  return b;
}

TableManageView.prototype.renderBar = function () {
  var a = this.active;
  this.bar.innerHTML = "";
  this.closeMenu();
  if (!a) {
    this.bar.hidden = true;
    return;
  }
  var nCol = this.colCount();
  var nRow = this.rowCount();
  var add = function (el) {
    this.bar.appendChild(el);
    return el;
  }.bind(this);
  if (a.mode === "col") {
    add(barButton("col-left", "Переместить столбец влево", arrowIcon("left"))).disabled = a.col <= 0;
    add(barButton("col-right", "Переместить столбец вправо", arrowIcon("right"))).disabled = a.col >= nCol - 1;
    add(barButton("col-add-right", "Добавить столбец справа", ICON_PLUS + "<span>столбец</span>"));
    add(barButton("col-del", "Удалить столбец", ICON_TRASH + "<span>Удалить</span>", "is-danger")).disabled = nCol <= 1;
  } else if (a.mode === "row") {
    add(barButton("row-up", "Переместить строку выше", arrowIcon("up"))).disabled = a.row <= 0;
    add(barButton("row-down", "Переместить строку ниже", arrowIcon("down"))).disabled = a.row >= nRow - 1;
    add(barButton("row-add-below", "Добавить строку ниже", ICON_PLUS + "<span>строка</span>"));
    add(barButton("row-del", "Удалить строку", ICON_TRASH + "<span>Удалить</span>", "is-danger")).disabled = nRow <= 1;
  } else {
    add(barButton("row-add-below", "Добавить строку ниже", ICON_PLUS + "<span>строка</span>"));
    add(barButton("col-add-right", "Добавить столбец справа", ICON_PLUS + "<span>столбец</span>"));
    var more = add(barButton("more", "Ещё действия с таблицей", ICON_MORE));
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
  }
  this.bar.hidden = false;
};

TableManageView.prototype.openMenu = function () {
  var a = this.active;
  if (!a) return;
  var nCol = this.colCount();
  var nRow = this.rowCount();
  var items = [
    ["row-add-above", "Строка выше"],
    ["row-add-below", "Строка ниже"],
    ["col-add-left", "Столбец слева"],
    ["col-add-right", "Столбец справа"],
    null,
    ["toggle-header", "Первая строка — заголовок"],
    null,
    ["sum-rows", "Σ Итого по строкам (столбец «Итого»)"],
    ["sum-cols", "Σ Итого по столбцам (строка «Итого»)"],
    ["sort-asc", "Сортировать по этому столбцу ↑"],
    ["sort-desc", "Сортировать по этому столбцу ↓"],
    ["ask-ai", "Попросить ИИ…"],
    null,
    ["row-del", "Удалить строку", nRow <= 1],
    ["col-del", "Удалить столбец", nCol <= 1],
    ["table-del", "Удалить таблицу"],
  ];
  this.menu.innerHTML = "";
  items.forEach(function (it) {
    if (!it) {
      var sep = document.createElement("div");
      sep.className = "note-table-menu-sep";
      this.menu.appendChild(sep);
      return;
    }
    var b = document.createElement("button");
    b.type = "button";
    b.className = "note-table-menu-item" + (/del/.test(it[0]) ? " is-danger" : "");
    b.setAttribute("role", "menuitem");
    b.dataset.act = it[0];
    b.textContent = it[1];
    b.disabled = !!it[2];
    this.menu.appendChild(b);
  }, this);
  this.menu.hidden = false;
  this.menuOpen = true;
  var more = this.bar.querySelector('[data-act="more"]');
  if (more) more.setAttribute("aria-expanded", "true");
};

TableManageView.prototype.closeMenu = function () {
  if (!this.menuOpen && this.menu.hidden) return;
  this.menu.hidden = true;
  this.menu.innerHTML = "";
  this.menuOpen = false;
  var more = this.bar.querySelector('[data-act="more"]');
  if (more) more.setAttribute("aria-expanded", "false");
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

TableManageView.prototype.moveLine = function (kind, from, to) {
  var pos = this.tablePos();
  if (pos == null) return;
  var cmd = kind === "col" ? moveTableColumn : moveTableRow;
  var editor = this.editor;
  cmd({ from: from, to: to, select: true, pos: pos + 1 })(editor.state, function (tr) {
    editor.view.dispatch(tr);
  });
  editor.view.focus();
};

TableManageView.prototype.runAction = function (act) {
  var a = this.active;
  var pos = this.tablePos();
  var editor = this.editor;
  var self = this;
  if (act === "more") {
    if (this.menuOpen) this.closeMenu();
    else this.openMenu();
    return;
  }
  this.closeMenu();
  if (pos == null) return;
  if (act === "edge-col") {
    this.addColumn(this.colCount() - 1, false);
    return;
  }
  if (act === "edge-row") {
    this.active = this.active || { row: 0, col: 0, mode: "cell" };
    this.addRow(this.rowCount() - 1, false);
    return;
  }
  if (!a) return;
  if (act === "row-add-below") return this.addRow(a.row, false);
  if (act === "row-add-above") return this.addRow(a.row, true);
  if (act === "col-add-right") return this.addColumn(a.col, false);
  if (act === "col-add-left") return this.addColumn(a.col, true);
  if (act === "col-left") return this.moveLine("col", a.col, a.col - 1);
  if (act === "col-right") return this.moveLine("col", a.col, a.col + 1);
  if (act === "row-up") return this.moveLine("row", a.row, a.row - 1);
  if (act === "row-down") return this.moveLine("row", a.row, a.row + 1);
  if (act === "sum-rows") return this.sumRows();
  if (act === "sum-cols") return this.sumCols();
  if (act === "sort-asc" || act === "sort-desc") return this.sortBy(a.col, act === "sort-desc");
  if (act === "ask-ai") return this.askAi();
  if (act === "toggle-header") {
    runTableCommand(editor, pos, a.row, a.col, toggleHeaderRow);
    focusCell(editor, this.tablePos(), a.row, a.col);
    return;
  }
  if (act === "col-del" || act === "row-del" || act === "table-del") {
    var msg = act === "col-del" ? "Удалить столбец?" : act === "row-del" ? "Удалить строку?" : "Удалить всю таблицу?";
    confirmTableDelete(msg).then(function (ok) {
      if (!ok) return;
      var p = self.tablePos();
      if (p == null) return;
      var cmd = act === "col-del" ? deleteColumn : act === "row-del" ? deleteRow : deleteTable;
      runTableCommand(editor, p, a.row, a.col, cmd);
      if (act !== "table-del") {
        var after = self.tablePos();
        if (after != null) {
          focusCell(
            editor,
            after,
            act === "row-del" ? Math.max(0, a.row - 1) : a.row,
            act === "col-del" ? Math.max(0, a.col - 1) : a.col
          );
        }
      }
    });
  }
};

// Столбец «Итого»: заполняем существующий (заголовок «Итого»/«Всего») или добавляем справа.
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

TableManageView.prototype.wrapPoint = function (rect, edge) {
  var box = this.dom.getBoundingClientRect();
  return {
    x: rect[edge || "left"] - box.left,
    y: rect.top - box.top,
    w: rect.width,
    h: rect.height,
  };
};

// Ручки только у текущего столбца и строки; ширина/высота — только мышью (на телефоне — нет).
TableManageView.prototype.renderOverlay = function () {
  var a = this.active;
  this.overlay.innerHTML = "";
  if (!a) {
    this.overlay.hidden = true;
    return;
  }
  this.overlay.hidden = false;
  // Выделен столбец — ручка строки не нужна (и наоборот).
  var showCol = a.mode !== "row";
  var showRow = a.mode !== "col";
  var colGrip = makeButton(
    "note-table-handle note-table-handle--grip note-table-handle--col" + (a.mode === "col" ? " is-selected" : ""),
    "Выделить столбец (перетащите, чтобы переместить)",
    ICON_GRIP
  );
  colGrip.dataset.role = "move-col";
  colGrip.dataset.index = String(a.col);
  if (showCol) this.overlay.appendChild(colGrip);
  var rowGrip = makeButton(
    "note-table-handle note-table-handle--grip note-table-handle--row" + (a.mode === "row" ? " is-selected" : ""),
    "Выделить строку (перетащите, чтобы переместить)",
    ICON_GRIP
  );
  rowGrip.dataset.role = "move-row";
  rowGrip.dataset.index = String(a.row);
  if (showRow) this.overlay.appendChild(rowGrip);
  if (this.isFinePointer() && a.mode === "cell") {
    var rz = makeButton("note-table-handle note-table-handle--resize", "Ширина столбца", ICON_RESIZE);
    rz.dataset.role = "resize-col";
    rz.dataset.index = String(a.col);
    this.overlay.appendChild(rz);
  }
  this.layoutHandles();
};

TableManageView.prototype.layoutHandles = function () {
  var a = this.active;
  var rows = this.table.rows;
  var box = this.dom.getBoundingClientRect();
  var sb = this.scroll.getBoundingClientRect();
  var tb = this.table.getBoundingClientRect();
  // «+» справа — у правого края видимой части таблицы, «+» снизу — под таблицей.
  // Таблица во всю ширину (или шире, с прокруткой) — «+» внутри у правого края,
  // чтобы под кнопку не резервировать место и таблица шла до края текста.
  var right = Math.min(tb.right, sb.right) - box.left;
  var fills = tb.right >= sb.right - 28;
  this.addColBtn.style.left = (fills ? right - 24 : right + 4) + "px";
  this.addColBtn.classList.toggle("is-inside", fills);
  this.addColBtn.style.top = tb.top - box.top + "px";
  this.addColBtn.style.height = tb.height + "px";
  this.addRowBtn.style.left = Math.max(tb.left, sb.left) - box.left + "px";
  this.addRowBtn.style.width = Math.min(tb.right, sb.right) - Math.max(tb.left, sb.left) + "px";
  this.addRowBtn.style.top = tb.bottom - box.top + 4 + "px";
  if (!a || this.overlay.hidden || !rows.length) return;
  var colIdx = this.drag && this.drag.kind === "move-col" ? this.drag.index : a.col;
  var rowIdx = this.drag && this.drag.kind === "move-row" ? this.drag.index : a.row;
  var first = rows[0];
  var cell = first.cells[colIdx];
  var btn;
  if (cell) {
    var cr = cell.getBoundingClientRect();
    var p = this.wrapPoint(cr);
    var visible = cr.right > sb.left && cr.left < sb.right;
    btn = this.overlay.querySelector('[data-role="move-col"]');
    if (btn) {
      btn.style.left = p.x + p.w / 2 + "px";
      btn.style.top = p.y - 4 + "px";
      btn.style.transform = "translate(-50%, -100%)";
      btn.style.visibility = visible ? "visible" : "hidden";
      btn.dataset.index = String(colIdx);
    }
    btn = this.overlay.querySelector('[data-role="resize-col"]');
    if (btn) {
      btn.style.left = p.x + p.w + "px";
      btn.style.top = p.y - 4 + "px";
      btn.style.transform = "translate(-50%, -100%)";
      btn.style.visibility = visible ? "visible" : "hidden";
      btn.dataset.index = String(colIdx);
    }
  }
  var rowEl = rows[rowIdx];
  if (rowEl && rowEl.cells[0]) {
    var rp = this.wrapPoint(rowEl.getBoundingClientRect());
    var lp = this.wrapPoint(rowEl.cells[0].getBoundingClientRect());
    btn = this.overlay.querySelector('[data-role="move-row"]');
    if (btn) {
      btn.style.left = Math.max(lp.x, sb.left - box.left) + "px";
      btn.style.top = rp.y + rp.h / 2 + "px";
      btn.style.transform = "translate(-50%, -50%)";
      btn.dataset.index = String(rowIdx);
    }
  }
};

TableManageView.prototype.handleOverlayPointerDown = function (e) {
  if (e.button != null && e.button !== 0) return;
  var btn = e.target.closest && e.target.closest(".note-table-handle");
  if (!btn || !this.overlay.contains(btn)) return;
  e.preventDefault();
  e.stopPropagation();
  this.interactUntil = Date.now() + 800;
  var role = btn.dataset.role;
  var index = parseInt(btn.dataset.index, 10);
  if (!role || !isFinite(index)) return;
  this.drag = {
    kind: role,
    index: index,
    startX: e.clientX,
    startY: e.clientY,
    startWidths: role === "resize-col" ? this.measureColWidths() : null,
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
    // Перетаскивание строк/столбцов — только мышью; на телефоне ручка лишь выделяет.
    if (d < DRAG_THRESHOLD || (this.drag.kind !== "resize-col" && e.pointerType === "touch")) return;
    this.drag.moved = true;
    this.closeMenu();
    this.drag.target.classList.add("is-dragging");
    if (this.drag.kind === "resize-col" && this.drag.startWidths) {
      applyPreviewWidths(this.colgroup, this.table, this.drag.startWidths.slice(), this.cellMinWidth);
    }
  }
  this.interactUntil = Date.now() + 800;
  var kind = this.drag.kind;
  if (kind === "resize-col") this.dragResizeCol(e);
  else if (kind === "move-col") this.dragMoveCol(e);
  else if (kind === "move-row") this.dragMoveRow(e);
};

TableManageView.prototype.dragResizeCol = function (e) {
  var widths = this.drag.startWidths.slice();
  var dx = e.clientX - this.drag.startX;
  widths[this.drag.index] = Math.max(this.cellMinWidth, this.drag.startWidths[this.drag.index] + dx);
  this.drag.currentWidths = widths;
  applyPreviewWidths(this.colgroup, this.table, widths, this.cellMinWidth);
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
  if (drag.moved) {
    if (drag.kind === "resize-col" && drag.currentWidths) {
      setAllColWidths(this.editor, pos, drag.currentWidths);
      this.editor.view.focus();
    } else if (drag.kind === "move-col") {
      selectLine(this.editor, pos, "col", drag.index);
    } else if (drag.kind === "move-row") {
      selectLine(this.editor, pos, "row", drag.index);
    }
    return;
  }
  // Нажатие без перетаскивания: выделить столбец/строку (повторное — снять выделение).
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
  if (
    this.overlay.contains(t) ||
    this.bar.contains(t) ||
    this.menu.contains(t) ||
    this.addColBtn.contains(t) ||
    this.addRowBtn.contains(t)
  ) {
    return true;
  }
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
  return !!t.closest(".note-table-bar, .note-table-menu, .note-table-manage-overlay, .note-table-handle, .note-table-edge");
};

TableManageView.prototype.destroy = function () {
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
          activeView.closeMenu();
        }
      }
      function onPointerDown(e) {
        if (confirmCloser) return;
        var t = e.target;
        if (t && t.closest && t.closest(".note-table-menu, [data-act='more']")) return;
        closeAllMenus();
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
      document.addEventListener("pointerdown", onPointerDown, true);
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
          document.removeEventListener("pointerdown", onPointerDown, true);
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
