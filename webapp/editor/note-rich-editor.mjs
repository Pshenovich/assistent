import { Editor, Extension } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Placeholder from "@tiptap/extension-placeholder";
import Underline from "@tiptap/extension-underline";
import Link from "@tiptap/extension-link";
import Image from "@tiptap/extension-image";
import Table from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import { DOMSerializer, Slice } from "@tiptap/pm/model";
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import { CellSelection, cellAround, isInTable } from "@tiptap/pm/tables";

function isIOSDevice() {
  if (typeof navigator === "undefined") return false;
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
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

          var oldSel = oldState.selection;
          var newSel = newState.selection;
          if (newSel.empty || !oldSel.empty) return null;
          if (Date.now() - lastTouchTime > 4000) return null;
          if (lastTouchPos == null || lastTouchPos < 3) return null;
          if (newSel.from > 2) return null;
          if (newSel.to < lastTouchPos - 2) return null;
          if (newSel.to - newSel.from < 8) return null;

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

function prepareHtmlForEditor(raw) {
  let html = stripEditorArtifacts(raw, { keepTaskListClass: true, keepTaskTypes: true });
  const doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
  const root = doc.body.firstChild;
  if (root) {
    repairOrphanTaskText(root);
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
  if (
    hasHtml &&
    (/\bnote-task-list\b/i.test(raw) ||
      /<input[^>]+type=["']checkbox["']/i.test(raw) ||
      /<li\b[^>]*\schecked(?:=["']|>|\s)/i.test(raw))
  ) {
    return prepareHtmlForEditor(raw);
  }
  if (hasHtml) {
    const plain = raw
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|div|li|h[1-6])>/gi, "\n")
      .replace(/<[^>]+>/g, "")
      .replace(/\u00a0/g, " ")
      .trim();
    if (/(^|\n|\s)#{1,6}\s|\*\*[^*]+\*\*|(^|\n)-\s+\[[ xX]\]\s|(^|\n)[-*•]\s/.test(plain)) {
      return window.NoteHtml && window.NoteHtml.markdownToHtml
        ? prepareHtmlForEditor(window.NoteHtml.markdownToHtml(plain))
        : prepareHtmlForEditor(markdownTasksToHtml(plain));
    }
    const splitIdx = raw.split("\n").findIndex((l) => /^-\s+\[[ xX]\]\s/.test(l.trim()));
    if (splitIdx >= 0) {
      const htmlPart = raw.split("\n").slice(0, splitIdx).join("\n").trim();
      const taskPart = raw.split("\n").slice(splitIdx).join("\n").trim();
      return prepareHtmlForEditor(htmlPart) + prepareHtmlForEditor(markdownTasksToHtml(taskPart));
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
      li.removeAttribute("data-type");
      li.removeAttribute("data-checked");
      if (checked) li.setAttribute("checked", "");
      else li.removeAttribute("checked");
      li.innerHTML = "<p>" + escapeHtmlText(text) + "</p>";
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
  };
  return "<svg " + common + ">" + (paths[name] || "") + "</svg>";
}

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
  var onNavigate =
    typeof options.onTocNavigate === "function" ? options.onTocNavigate : function () {};
  var scrollParent = options.scrollParent || null;

  function renderToc() {
    var headings = collectEditorHeadings(editor);
    var selectionPos = editor.state && editor.state.selection ? editor.state.selection.from : 0;
    var activePos = activeHeadingPos(headings, selectionPos);
    tocEl.innerHTML = "";

    if (!headings.length) {
      var empty = document.createElement("p");
      empty.className = "note-editor-toc-empty";
      empty.textContent = "Добавьте заголовки, чтобы увидеть оглавление.";
      tocEl.appendChild(empty);
      return;
    }

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
  }

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

function mountToolbar(toolbarEl, editor) {
  var refreshFn = function () {};
  var fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/*";
  fileInput.className = "note-tg-file-input";
  fileInput.setAttribute("aria-hidden", "true");
  fileInput.tabIndex = -1;

  toolbarEl.className = "note-rich-editor-toolbar note-tg-toolbar";
  toolbarEl.innerHTML =
    '<button type="button" class="note-tg-btn note-tg-btn--undo" data-action="undo" title="Отменить" aria-label="Отменить">' +
    svgIcon("undo") +
    "</button>" +
    '<div class="note-tg-capsule note-tg-capsule--default" data-mode="default">' +
    '<button type="button" class="note-tg-btn" data-menu="plus" title="Блок" aria-label="Блок">' +
    svgIcon("plus") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-menu="heading" title="Заголовок" aria-label="Заголовок">' +
    svgIcon("heading") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-menu="list" title="Список" aria-label="Список">' +
    svgIcon("list") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="table" title="Таблица" aria-label="Таблица">' +
    svgIcon("table") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="attach" title="Вложение" aria-label="Вложение">' +
    svgIcon("paperclip") +
    "</button>" +
    "</div>" +
    '<div class="note-tg-capsule note-tg-capsule--selection hidden" data-mode="selection">' +
    '<button type="button" class="note-tg-btn" data-action="bold" title="Жирный" aria-label="Жирный">' +
    svgIcon("bold") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="italic" title="Курсив" aria-label="Курсив">' +
    svgIcon("italic") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="strike" title="Зачёркнутый" aria-label="Зачёркнутый">' +
    svgIcon("strike") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="underline" title="Подчёркнутый" aria-label="Подчёркнутый">' +
    svgIcon("underline") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="link" title="Ссылка" aria-label="Ссылка">' +
    svgIcon("link") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-menu="heading" title="Заголовок" aria-label="Заголовок">' +
    svgIcon("heading") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-menu="list-sel" title="Список" aria-label="Список">' +
    svgIcon("list") +
    "</button>" +
    '<button type="button" class="note-tg-btn" data-action="quote" title="Цитата" aria-label="Цитата">' +
    svgIcon("quote") +
    "</button>" +
    "</div>" +
    '<div class="note-tg-menu hidden" data-menu-panel="heading" role="menu">' +
    '<button type="button" class="note-tg-menu-item" data-cmd="heading-2" data-check="heading-2">' +
    svgIcon("heading") +
    "<span>Заголовок 2</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="heading-3" data-check="heading-3">' +
    svgIcon("heading") +
    "<span>Заголовок 3</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="heading-4" data-check="heading-4">' +
    svgIcon("heading") +
    "<span>Заголовок 4</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="paragraph" data-check="plain">' +
    svgIcon("text") +
    "<span>Обычный текст</span></button>" +
    "</div>" +
    '<div class="note-tg-menu hidden" data-menu-panel="plus" role="menu">' +
    '<button type="button" class="note-tg-menu-item" data-cmd="paragraph">' +
    svgIcon("text") +
    "<span>Обычный текст</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="quote">' +
    svgIcon("quote") +
    "<span>Цитата</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="task">' +
    svgIcon("check") +
    "<span>Чек-бокс</span></button>" +
    "</div>" +
    '<div class="note-tg-menu hidden" data-menu-panel="list" role="menu">' +
    '<button type="button" class="note-tg-menu-item" data-cmd="paragraph" data-check="plain">' +
    svgIcon("none") +
    "<span>Обычный текст</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="bullet" data-check="bulletList">' +
    svgIcon("list") +
    "<span>Булет лист</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="ordered" data-check="orderedList">' +
    svgIcon("numbered") +
    "<span>Нумерованный список</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="task" data-check="taskList">' +
    svgIcon("checklist") +
    "<span>Чек-лист</span></button>" +
    "</div>" +
    '<div class="note-tg-menu hidden" data-menu-panel="list-sel" role="menu">' +
    '<button type="button" class="note-tg-menu-item" data-cmd="paragraph" data-check="plain">' +
    svgIcon("none") +
    "<span>Ничего</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="bullet" data-check="bulletList">' +
    svgIcon("list") +
    "<span>Булет лист</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="ordered" data-check="orderedList">' +
    svgIcon("numbered") +
    "<span>Нумерованный список</span></button>" +
    '<button type="button" class="note-tg-menu-item" data-cmd="task" data-check="taskList">' +
    svgIcon("checklist") +
    "<span>Чек-лист</span></button>" +
    "</div>";

  toolbarEl.appendChild(fileInput);

  function runCmd(cmd) {
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
    } else if (cmd === "paragraph") {
      clearToParagraph(editor);
    } else if (cmd === "quote") {
      editor.chain().focus().toggleBlockquote().run();
    } else if (cmd === "task") {
      editor.chain().focus().toggleTaskList().run();
    } else if (cmd === "bullet") {
      editor.chain().focus().toggleBulletList().run();
    } else if (cmd === "ordered") {
      editor.chain().focus().toggleOrderedList().run();
    }
    refreshFn();
  }

  var lastMenuCmdAt = 0;

  function onMenuItem(item) {
    if (!item) return;
    var now = Date.now();
    if (now - lastMenuCmdAt < 400) return;
    lastMenuCmdAt = now;
    runCmd(item.getAttribute("data-cmd"));
    closeTgMenus(toolbarEl);
  }

  function openMenu(name, triggerBtn) {
    var panel = toolbarEl.querySelector('[data-menu-panel="' + name + '"]');
    if (!panel) return;
    var wasOpen = !panel.classList.contains("hidden");
    closeTgMenus(toolbarEl);
    closeLinkPopover();
    if (wasOpen) return;
    panel.classList.remove("hidden");
    if (triggerBtn) triggerBtn.classList.add("note-tg-btn--menu-open");
  }

  function pickImage() {
    fileInput.value = "";
    fileInput.click();
  }

  fileInput.addEventListener("change", function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) return;
    if (!/^image\//i.test(file.type || "")) return;
    if (file.size > 8 * 1024 * 1024) {
      window.alert("Фото больше 8 МБ. Выберите файл поменьше.");
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      var src = String(reader.result || "");
      if (!src) return;
      editor.chain().focus().setImage({ src: src, alt: file.name || "Фото" }).run();
      refreshFn();
    };
    reader.readAsDataURL(file);
  });

  // Prevent toolbar buttons from stealing focus from the editor (incl. menu items).
  toolbarEl.addEventListener("mousedown", function (e) {
    var btn = e.target.closest("button");
    if (!btn || !toolbarEl.contains(btn)) return;
    e.preventDefault();
  });
  toolbarEl.addEventListener(
    "pointerdown",
    function (e) {
      var item = e.target.closest(".note-tg-menu-item");
      if (!item || !toolbarEl.contains(item)) return;
      // Run on pointerdown so the command isn't lost if selectionUpdate
      // would hide the menu before click (empty caret → closeTgMenus race).
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      onMenuItem(item);
    },
    true
  );

  toolbarEl.addEventListener("click", function (e) {
    var item = e.target.closest(".note-tg-menu-item");
    if (item && toolbarEl.contains(item)) {
      e.preventDefault();
      e.stopPropagation();
      onMenuItem(item);
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
    if (action === "undo") {
      if (editor.can().chain().focus().undo().run()) editor.chain().focus().undo().run();
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
  }
  document.addEventListener("mousedown", outsideMenus, true);
  document.addEventListener("touchstart", outsideMenus, true);

  var hadTextSelection = false;
  function refreshFnImpl() {
    var hasSelection = !editor.state.selection.empty;
    var defCap = toolbarEl.querySelector(".note-tg-capsule--default");
    var selCap = toolbarEl.querySelector(".note-tg-capsule--selection");
    if (defCap) defCap.classList.toggle("hidden", hasSelection);
    if (selCap) selCap.classList.toggle("hidden", !hasSelection);
    // Only close when leaving selection mode — never while caret is empty
    // (that race hid the «+» menu before the item click fired).
    if (hadTextSelection && !hasSelection) closeTgMenus(toolbarEl);
    hadTextSelection = hasSelection;

    var undoBtn = toolbarEl.querySelector('[data-action="undo"]');
    if (undoBtn) {
      var canUndo = editor.can().chain().focus().undo().run();
      undoBtn.disabled = !canUndo;
      undoBtn.classList.toggle("note-tg-btn--disabled", !canUndo);
    }

    var markMap = {
      bold: "bold",
      italic: "italic",
      strike: "strike",
      underline: "underline",
      link: "link",
      quote: "blockquote",
    };
    Object.keys(markMap).forEach(function (action) {
      toolbarEl.querySelectorAll('[data-action="' + action + '"]').forEach(function (btn) {
        btn.classList.toggle("note-tg-btn--active", editor.isActive(markMap[action]));
      });
    });
    toolbarEl.querySelectorAll('[data-menu="heading"]').forEach(function (btn) {
      btn.classList.toggle("note-tg-btn--active", editor.isActive("heading"));
    });

    toolbarEl.querySelectorAll(".note-tg-menu-item[data-check]").forEach(function (item) {
      var check = item.getAttribute("data-check");
      var headingMatch = String(check || "").match(/^heading-(\d)$/);
      var on =
        check === "plain"
          ? !(
              editor.isActive("bulletList") ||
              editor.isActive("orderedList") ||
              editor.isActive("taskList") ||
              editor.isActive("heading") ||
              editor.isActive("blockquote")
            )
          : headingMatch
          ? editor.isActive("heading", { level: parseInt(headingMatch[1], 10) })
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

  const editor = new Editor({
    element: content,
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4, 5, 6] },
      }),
      Underline,
      Link.configure({ openOnClick: false, autolink: true, linkOnPaste: true }),
      Image.configure({
        inline: false,
        allowBase64: true,
        HTMLAttributes: { class: "note-editor-image" },
      }),
      Table.configure({
        resizable: false,
        HTMLAttributes: { class: "note-editor-table" },
      }),
      TableRow,
      TableHeader,
      TableCell,
      BotTaskList.configure({
        HTMLAttributes: { class: "note-task-list" },
      }),
      BotTaskItem.configure({ nested: false }),
      Placeholder.configure({
        placeholder: options.placeholder || "Начните писать…",
      }),
      IosSelectionGuard,
    ],
    content: importBody(options.body || ""),
    autofocus: options.autofocus === true,
    editorProps: {
      attributes: {
        class: "note-rich-editor-body ProseMirror",
        spellcheck: "true",
      },
      handlePaste: function (view, event) {
        return pastePlainTextInTableCell(view, event, tablePasteClickPos.pos);
      },
      handleTripleClick: function (view, pos) {
        return selectAllTextInTableCell(view, pos);
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
    onUpdate: function () {
      if (typeof options.onUpdate === "function") options.onUpdate();
    },
  });

  editor.view.dom.addEventListener(
    "paste",
    function (event) {
      if (pastePlainTextInTableCell(editor.view, event, tablePasteClickPos.pos)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  var cleanupToolbar = mountToolbar(toolbar, editor);
  var cleanupToc = mountToc(options.tocParent, editor, {
    onTocNavigate: options.onTocNavigate,
    scrollParent: options.scrollParent,
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
            "input, textarea, button, a, label, .note-rich-toolbar-btn, .note-tg-btn, .note-tg-menu"
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

  return {
    getHtml: function () {
      return exportBody(editor);
    },
    prepareForSave: function () {
      editor.view.dom.blur();
    },
    focus: function () {
      editor.commands.focus("end");
    },
    bindTapFocus: bindTapFocus,
    destroy: function () {
      closeLinkPopover();
      closeTgMenus(toolbar);
      if (typeof cleanupToolbar === "function") cleanupToolbar();
      if (typeof cleanupToc === "function") cleanupToc();
      editor.destroy();
    },
  };
}

globalThis.NoteRichEditor = {
  mount: mount,
  importBody: importBody,
  exportBody: exportBody,
  canonicalBody: canonicalBody,
};
