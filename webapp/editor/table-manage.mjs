import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import {
  TableMap,
  addColumnAfter,
  addRowAfter,
  deleteColumn,
  deleteRow,
  moveTableColumn,
  moveTableRow,
} from "@tiptap/pm/tables";

var tableManageKey = new PluginKey("tableManage");
var MIN_COL = 48;
var MIN_ROW = 32;
var DRAG_THRESHOLD = 8;
var HANDLE = 22;
var activeView = null;
var confirmCloser = null;

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

function exitManageMode() {
  closeConfirm(false);
  if (activeView) {
    activeView.setManaging(false);
    activeView = null;
  }
}

function TableManageView(props, cellMinWidth) {
  this.editor = props.editor;
  this.getPos = props.getPos;
  this.node = props.node;
  this.cellMinWidth = cellMinWidth || MIN_COL;
  this.managing = false;
  this.drag = null;
  this.flyout = null;
  this.ro = null;

  this.dom = document.createElement("div");
  this.dom.className = "note-table-manage-wrap";

  this.toggle = makeButton(
    "note-table-manage-toggle",
    "Управление таблицей",
    ICON_DOTS
  );
  this.scroll = document.createElement("div");
  this.scroll.className = "note-table-manage-scroll";
  this.overlay = document.createElement("div");
  this.overlay.className = "note-table-manage-overlay";
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

  this.scroll.appendChild(this.overlay);
  this.scroll.appendChild(this.table);
  this.dom.appendChild(this.toggle);
  this.dom.appendChild(this.scroll);

  updateColumns(this.node, this.colgroup, this.table, this.cellMinWidth);
  this.bind();
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
  this.onTogglePointer = function (e) {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    self.toggleManaging();
  };
  this.toggle.addEventListener("pointerdown", this.onTogglePointer);

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
  this.toggle.removeEventListener("pointerdown", this.onTogglePointer);
  this.overlay.removeEventListener("pointerdown", this.onOverlayPointerDown);
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

TableManageView.prototype.toggleManaging = function () {
  if (this.managing) {
    this.setManaging(false);
    if (activeView === this) activeView = null;
    return;
  }
  if (activeView && activeView !== this) activeView.setManaging(false);
  this.setManaging(true);
  activeView = this;
};

TableManageView.prototype.setManaging = function (on) {
  this.managing = !!on;
  this.flyout = null;
  this.dom.classList.toggle("is-managing", this.managing);
  this.toggle.classList.toggle("is-active", this.managing);
  this.toggle.setAttribute("aria-pressed", this.managing ? "true" : "false");
  if (this.managing) this.renderOverlay();
  else {
    this.overlay.innerHTML = "";
    this.overlay.hidden = true;
  }
};

TableManageView.prototype.clearFlyout = function () {
  if (!this.flyout) return;
  this.flyout = null;
  this.renderOverlay();
};

TableManageView.prototype.colCount = function () {
  var row = this.table.rows[0];
  return row ? row.cells.length : 0;
};

TableManageView.prototype.rowCount = function () {
  return this.table.rows.length;
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
  var box = this.scroll.getBoundingClientRect();
  var sl = this.scroll.scrollLeft || 0;
  var st = this.scroll.scrollTop || 0;
  return {
    x: rect[edge || "left"] - box.left + sl,
    y: rect.top - box.top + st,
    w: rect.width,
    h: rect.height,
  };
};

TableManageView.prototype.renderOverlay = function () {
  if (!this.managing) {
    this.overlay.hidden = true;
    this.overlay.innerHTML = "";
    return;
  }
  this.overlay.hidden = false;
  this.overlay.innerHTML = "";
  var nCol = this.colCount();
  var nRow = this.rowCount();
  var c;
  var r;
  for (c = 0; c < nCol; c += 1) {
    var grip = makeButton(
      "note-table-handle note-table-handle--grip",
      "Переместить колонку",
      ICON_GRIP
    );
    grip.dataset.role = "move-col";
    grip.dataset.index = String(c);
    this.overlay.appendChild(grip);

    var rz = makeButton(
      "note-table-handle note-table-handle--resize",
      "Ширина колонки",
      ICON_RESIZE
    );
    rz.dataset.role = "resize-col";
    rz.dataset.index = String(c);
    this.overlay.appendChild(rz);
  }
  for (r = 0; r < nRow; r += 1) {
    var rowGrip = makeButton(
      "note-table-handle note-table-handle--grip",
      "Переместить строку",
      ICON_GRIP
    );
    rowGrip.dataset.role = "move-row";
    rowGrip.dataset.index = String(r);
    this.overlay.appendChild(rowGrip);

    var rowRz = makeButton(
      "note-table-handle note-table-handle--resize note-table-handle--resize-row",
      "Высота строки",
      ICON_RESIZE
    );
    rowRz.dataset.role = "resize-row";
    rowRz.dataset.index = String(r);
    this.overlay.appendChild(rowRz);
  }

  if (this.flyout) {
    var extra;
    if (this.flyout.kind === "add-col") {
      extra = makeButton(
        "note-table-handle note-table-handle--add",
        "Добавить колонку справа",
        ICON_PLUS
      );
    } else if (this.flyout.kind === "add-row") {
      extra = makeButton(
        "note-table-handle note-table-handle--add",
        "Добавить строку ниже",
        ICON_PLUS
      );
    } else if (this.flyout.kind === "del-col" && nCol > 1) {
      extra = makeButton(
        "note-table-handle note-table-handle--trash",
        "Удалить колонку",
        ICON_TRASH
      );
    } else if (this.flyout.kind === "del-row" && nRow > 1) {
      extra = makeButton(
        "note-table-handle note-table-handle--trash",
        "Удалить строку",
        ICON_TRASH
      );
    }
    if (extra) {
      extra.dataset.role = this.flyout.kind;
      extra.dataset.index = String(this.flyout.index);
      this.overlay.appendChild(extra);
    } else {
      this.flyout = null;
    }
  }
  this.layoutHandles();
};

TableManageView.prototype.layoutHandles = function () {
  if (!this.managing || this.overlay.hidden) return;
  var rows = this.table.rows;
  if (!rows.length) return;
  var first = rows[0];
  var nCol = first.cells.length;
  var nRow = rows.length;
  var c;
  var r;
  var btn;

  for (c = 0; c < nCol; c += 1) {
    var cell = first.cells[c];
    if (!cell) continue;
    var p = this.wrapPoint(cell.getBoundingClientRect());
    btn = this.overlay.querySelector('[data-role="move-col"][data-index="' + c + '"]');
    if (btn) {
      btn.style.left = p.x + p.w / 2 + "px";
      btn.style.top = p.y - 6 + "px";
      btn.style.transform = "translate(-50%, -100%)";
    }
    btn = this.overlay.querySelector('[data-role="resize-col"][data-index="' + c + '"]');
    if (btn) {
      btn.style.left = p.x + p.w + "px";
      btn.style.top = p.y - 6 + "px";
      btn.style.transform = "translate(-50%, -100%)";
    }
  }

  for (r = 0; r < nRow; r += 1) {
    var rowEl = rows[r];
    var leftCell = rowEl.cells[0];
    if (!leftCell) continue;
    var rp = this.wrapPoint(rowEl.getBoundingClientRect());
    var lp = this.wrapPoint(leftCell.getBoundingClientRect());
    btn = this.overlay.querySelector('[data-role="move-row"][data-index="' + r + '"]');
    if (btn) {
      btn.style.left = lp.x - 6 + "px";
      btn.style.top = rp.y + rp.h / 2 + "px";
      btn.style.transform = "translate(-100%, -50%)";
    }
    btn = this.overlay.querySelector('[data-role="resize-row"][data-index="' + r + '"]');
    if (btn) {
      btn.style.left = lp.x - 6 + "px";
      btn.style.top = rp.y + rp.h + "px";
      btn.style.transform = "translate(-100%, -50%)";
    }
  }

  if (this.flyout) {
    var fly = this.overlay.querySelector('[data-role="' + this.flyout.kind + '"]');
    if (!fly) return;
    var kind = this.flyout.kind;
    var idx = this.flyout.index;
    var anchor;
    if (kind === "add-col") {
      anchor = this.overlay.querySelector(
        '[data-role="resize-col"][data-index="' + idx + '"]'
      );
      if (anchor) {
        fly.style.left = parseFloat(anchor.style.left) + HANDLE + 4 + "px";
        fly.style.top = anchor.style.top;
        fly.style.transform = anchor.style.transform;
      }
    } else if (kind === "add-row") {
      anchor = this.overlay.querySelector(
        '[data-role="resize-row"][data-index="' + idx + '"]'
      );
      if (anchor) {
        fly.style.left = anchor.style.left;
        fly.style.top = parseFloat(anchor.style.top) + HANDLE + 4 + "px";
        fly.style.transform = anchor.style.transform;
      }
    } else if (kind === "del-col") {
      anchor = this.overlay.querySelector(
        '[data-role="move-col"][data-index="' + idx + '"]'
      );
      if (anchor) {
        fly.style.left = parseFloat(anchor.style.left) + HANDLE + 4 + "px";
        fly.style.top = anchor.style.top;
        fly.style.transform = anchor.style.transform;
      }
    } else if (kind === "del-row") {
      anchor = this.overlay.querySelector(
        '[data-role="move-row"][data-index="' + idx + '"]'
      );
      if (anchor) {
        fly.style.left = parseFloat(anchor.style.left) - HANDLE - 4 + "px";
        fly.style.top = anchor.style.top;
        fly.style.transform = "translate(-100%, -50%)";
      }
    }
  }
};

TableManageView.prototype.swapHandleIndex = function (role, from, to) {
  var a = this.overlay.querySelector(
    '[data-role="' + role + '"][data-index="' + from + '"]'
  );
  var b = this.overlay.querySelector(
    '[data-role="' + role + '"][data-index="' + to + '"]'
  );
  if (a) a.dataset.index = String(to);
  if (b) b.dataset.index = String(from);
};

TableManageView.prototype.handleOverlayPointerDown = function (e) {
  if (e.button != null && e.button !== 0) return;
  var btn = e.target.closest && e.target.closest(".note-table-handle");
  if (!btn || !this.overlay.contains(btn)) return;
  e.preventDefault();
  e.stopPropagation();
  var role = btn.dataset.role;
  var index = parseInt(btn.dataset.index, 10);
  if (!role || !isFinite(index)) return;

  if (role === "add-col" || role === "add-row" || role === "del-col" || role === "del-row") {
    this.onFlyoutAction(role, index);
    return;
  }

  this.drag = {
    kind: role,
    index: index,
    startX: e.clientX,
    startY: e.clientY,
    startWidths: role === "resize-col" ? this.measureColWidths() : null,
    startHeight: role === "resize-row" ? this.measureRowHeight(index) : 0,
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
    if (d < DRAG_THRESHOLD) return;
    this.drag.moved = true;
    this.flyout = null;
    var extra = this.overlay.querySelector(
      '[data-role="add-col"], [data-role="add-row"], [data-role="del-col"], [data-role="del-row"]'
    );
    if (extra) extra.remove();
    this.drag.target.classList.add("is-dragging");
    if (this.drag.kind === "resize-col" && this.drag.startWidths) {
      applyPreviewWidths(
        this.colgroup,
        this.table,
        this.drag.startWidths.slice(),
        this.cellMinWidth
      );
    }
  }
  var kind = this.drag.kind;
  if (kind === "resize-col") this.dragResizeCol(e);
  else if (kind === "resize-row") this.dragResizeRow(e);
  else if (kind === "move-col") this.dragMoveCol(e);
  else if (kind === "move-row") this.dragMoveRow(e);
};

TableManageView.prototype.dragResizeCol = function (e) {
  var widths = this.drag.startWidths.slice();
  var dx = e.clientX - this.drag.startX;
  widths[this.drag.index] = Math.max(
    this.cellMinWidth,
    this.drag.startWidths[this.drag.index] + dx
  );
  this.drag.currentWidths = widths;
  applyPreviewWidths(this.colgroup, this.table, widths, this.cellMinWidth);
  this.layoutHandles();
};

TableManageView.prototype.dragResizeRow = function (e) {
  var dy = e.clientY - this.drag.startY;
  var h = Math.max(MIN_ROW, this.drag.startHeight + dy);
  this.drag.currentHeight = h;
  var row = this.table.rows[this.drag.index];
  if (row) {
    row.style.height = h + "px";
    for (var i = 0; i < row.cells.length; i += 1) {
      row.cells[i].style.height = h + "px";
    }
  }
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
  if (e.clientX > this.colMidX(i) && i < n - 1 && e.clientX > this.colMidX(i + 1)) {
    var ok = moveTableColumn({
      from: i,
      to: i + 1,
      select: false,
      pos: pos + 1,
    })(this.editor.state, function (tr) {
      this.editor.view.dispatch(tr);
    }.bind(this));
    if (ok) {
      this.swapHandleIndex("move-col", i, i + 1);
      this.swapHandleIndex("resize-col", i, i + 1);
      this.drag.index = i + 1;
      this.layoutHandles();
    }
  } else if (e.clientX < this.colMidX(i) && i > 0 && e.clientX < this.colMidX(i - 1)) {
    var okL = moveTableColumn({
      from: i,
      to: i - 1,
      select: false,
      pos: pos + 1,
    })(this.editor.state, function (tr) {
      this.editor.view.dispatch(tr);
    }.bind(this));
    if (okL) {
      this.swapHandleIndex("move-col", i, i - 1);
      this.swapHandleIndex("resize-col", i, i - 1);
      this.drag.index = i - 1;
      this.layoutHandles();
    }
  }
};

TableManageView.prototype.dragMoveRow = function (e) {
  var pos = this.tablePos();
  if (pos == null) return;
  var i = this.drag.index;
  var n = this.rowCount();
  if (e.clientY > this.rowMidY(i) && i < n - 1 && e.clientY > this.rowMidY(i + 1)) {
    var ok = moveTableRow({
      from: i,
      to: i + 1,
      select: false,
      pos: pos + 1,
    })(this.editor.state, function (tr) {
      this.editor.view.dispatch(tr);
    }.bind(this));
    if (ok) {
      this.swapHandleIndex("move-row", i, i + 1);
      this.swapHandleIndex("resize-row", i, i + 1);
      this.drag.index = i + 1;
      this.layoutHandles();
    }
  } else if (e.clientY < this.rowMidY(i) && i > 0 && e.clientY < this.rowMidY(i - 1)) {
    var okU = moveTableRow({
      from: i,
      to: i - 1,
      select: false,
      pos: pos + 1,
    })(this.editor.state, function (tr) {
      this.editor.view.dispatch(tr);
    }.bind(this));
    if (okU) {
      this.swapHandleIndex("move-row", i, i - 1);
      this.swapHandleIndex("resize-row", i, i - 1);
      this.drag.index = i - 1;
      this.layoutHandles();
    }
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
  if (drag.moved) {
    if (drag.kind === "resize-col" && drag.currentWidths && pos != null) {
      setAllColWidths(this.editor, pos, drag.currentWidths);
    } else if (drag.kind === "resize-row" && drag.currentHeight && pos != null) {
      setRowHeight(this.editor, pos, drag.index, drag.currentHeight);
    }
    this.flyout = null;
    this.renderOverlay();
    return;
  }

  if (drag.kind === "resize-col") this.flyout = { kind: "add-col", index: drag.index };
  else if (drag.kind === "resize-row") this.flyout = { kind: "add-row", index: drag.index };
  else if (drag.kind === "move-col") this.flyout = { kind: "del-col", index: drag.index };
  else if (drag.kind === "move-row") this.flyout = { kind: "del-row", index: drag.index };
  this.renderOverlay();
};

TableManageView.prototype.onFlyoutAction = function (role, index) {
  var pos = this.tablePos();
  var self = this;
  if (pos == null) return;
  if (role === "add-col") {
    runTableCommand(this.editor, pos, 0, index, addColumnAfter);
    this.flyout = null;
    return;
  }
  if (role === "add-row") {
    runTableCommand(this.editor, pos, index, 0, addRowAfter);
    this.flyout = null;
    return;
  }
  if (role === "del-col") {
    if (this.colCount() <= 1) return;
    confirmTableDelete("Удалить колонку?").then(function (ok) {
      if (!ok) return;
      var p = self.tablePos();
      if (p == null) return;
      runTableCommand(self.editor, p, 0, index, deleteColumn);
      self.flyout = null;
    });
    return;
  }
  if (role === "del-row") {
    if (this.rowCount() <= 1) return;
    confirmTableDelete("Удалить строку?").then(function (ok) {
      if (!ok) return;
      var p = self.tablePos();
      if (p == null) return;
      runTableCommand(self.editor, p, index, 0, deleteRow);
      self.flyout = null;
    });
  }
};

TableManageView.prototype.update = function (node) {
  if (node.type !== this.node.type) return false;
  var oldRows = this.node.childCount;
  var oldCols = this.node.firstChild ? this.node.firstChild.childCount : 0;
  var newRows = node.childCount;
  var newCols = node.firstChild ? node.firstChild.childCount : 0;
  var structureChanged = oldRows !== newRows || oldCols !== newCols;
  this.node = node;
  if (!this.drag || this.drag.kind !== "resize-col") {
    updateColumns(node, this.colgroup, this.table, this.cellMinWidth);
  }
  if (this.drag && (this.drag.kind === "move-col" || this.drag.kind === "move-row")) {
    this.layoutHandles();
  } else if (this.managing && !this.drag) {
    var self = this;
    requestAnimationFrame(function () {
      if (!self.managing || self.drag) return;
      if (structureChanged) self.renderOverlay();
      else self.layoutHandles();
    });
  }
  return true;
};

TableManageView.prototype.ignoreMutation = function (mutation) {
  if (!mutation || !mutation.target) return false;
  if (this.overlay.contains(mutation.target) || this.toggle.contains(mutation.target)) {
    return true;
  }
  if (mutation.type === "attributes") {
    if (mutation.target === this.table || mutation.target === this.dom || mutation.target === this.scroll) return true;
    if (this.colgroup.contains(mutation.target)) return true;
    if (mutation.target.nodeName === "TR" || mutation.target.nodeName === "COL") return true;
    if (
      (mutation.target.nodeName === "TD" || mutation.target.nodeName === "TH") &&
      mutation.attributeName === "style"
    ) {
      return true;
    }
  }
  return false;
};

TableManageView.prototype.stopEvent = function (event) {
  var t = event.target;
  if (!t || !t.closest) return false;
  return !!t.closest(".note-table-manage-toggle, .note-table-manage-overlay, .note-table-handle");
};

TableManageView.prototype.destroy = function () {
  if (activeView === this) {
    activeView = null;
    closeConfirm(false);
  }
  this.unbind();
};

function isInsideManageUi(target) {
  if (!target || !target.closest) return false;
  return !!target.closest(
    ".note-table-manage-wrap, .note-table-confirm-overlay, .note-table-handle"
  );
}

function createTableManagePlugin() {
  return new Plugin({
    key: tableManageKey,
    view: function () {
      function onKey(e) {
        if (e.key !== "Escape") return;
        if (confirmCloser) {
          e.preventDefault();
          closeConfirm(false);
          return;
        }
        if (activeView && activeView.flyout) {
          e.preventDefault();
          activeView.clearFlyout();
          return;
        }
        if (activeView) {
          e.preventDefault();
          exitManageMode();
        }
      }
      function onPointerDown(e) {
        if (confirmCloser) return;
        if (!activeView) return;
        if (activeView.drag) return;
        if (isInsideManageUi(e.target)) {
          if (
            activeView.dom.contains(e.target) &&
            !e.target.closest(".note-table-handle, .note-table-manage-toggle")
          ) {
            activeView.clearFlyout();
          }
          return;
        }
        exitManageMode();
      }
      document.addEventListener("keydown", onKey, true);
      document.addEventListener("pointerdown", onPointerDown, true);
      return {
        destroy: function () {
          document.removeEventListener("keydown", onKey, true);
          document.removeEventListener("pointerdown", onPointerDown, true);
          exitManageMode();
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
