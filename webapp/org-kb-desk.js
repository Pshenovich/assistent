// База знаний на компьютере (от 960 px): дерево разделов слева, страница справа — как в вики.
// Главная базы, страница раздела, документ — в обычном редакторе справа от дерева, со строкой сведений,
// оглавлением, «Ссылаются сюда» и «Предыдущий / Следующий». На телефоне — прежний вид (org-kb.js).
// Данные и действия — из org-kb.js (window.LeoOrgKb._), сервер — /orgs/{id}/kb, /kb/search, /kb/backlinks.
(function () {
  "use strict";

  var TREE_W_LS = "leo_kb_tree_w";
  var TREE_HIDDEN_LS = "leo_kb_tree_hidden";
  var OPEN_LS = "leo_kb_tree_open";
  var TREE_W_DEFAULT = 272;
  var TREE_W_MIN = 220;
  var TREE_W_MAX = 440;

  var desk = {
    on: false,
    view: "home", // home | section | mine
    orgId: null,
    sectionId: null,
    noteId: "", // открытый документ (подсветка в дереве)
    treeQ: "",
    q: "",
    results: null, // ответ /kb/search для q
    searching: false,
    open: readOpen(),
    hidden: lsGet(TREE_HIDDEN_LS) === "1",
    width: clampW(Number(lsGet(TREE_W_LS)) || TREE_W_DEFAULT),
    drag: null,
    noteCtx: null, // {note, page, node} — для строки сведений и оглавления
  };

  var IC = {
    chev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>',
    side: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>',
    more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.3" /><circle cx="12" cy="12" r="1.3" /><circle cx="19" cy="12" r="1.3" /></svg>',
    home: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 19V5M19 19v2H6" /></svg>',
    mine: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4z" /></svg>',
    link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" /><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></svg>',
    spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /></svg>',
    group: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c1-3.5 3.5-5 6.5-5s5.5 1.5 6.5 5" /><circle cx="17" cy="9" r="2.5" /><path d="M17 14c2.5 0 4 1.5 4.5 4" /></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>',
  };

  function K() {
    return (window.LeoOrgKb && window.LeoOrgKb._) || null;
  }
  function app() {
    return window.LeoApp || {};
  }
  function lsGet(k) {
    try {
      return localStorage.getItem(k);
    } catch (_) {
      return null;
    }
  }
  function lsSet(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch (_) {}
  }
  function readOpen() {
    try {
      return JSON.parse(lsGet(OPEN_LS) || "{}") || {};
    } catch (_) {
      return {};
    }
  }
  function saveOpen() {
    lsSet(OPEN_LS, JSON.stringify(desk.open));
  }
  function clampW(w) {
    return Math.max(TREE_W_MIN, Math.min(TREE_W_MAX, Math.round(w || TREE_W_DEFAULT)));
  }
  function wide() {
    return !!(window.matchMedia && window.matchMedia("(min-width: 960px)").matches);
  }
  function panel() {
    return document.getElementById("panel-knowledge");
  }
  function panelVisible() {
    var p = panel();
    return !!(p && !p.hidden && !p.classList.contains("hidden"));
  }
  function esc(s) {
    return K().esc(s);
  }
  function openKey(orgId, id) {
    return orgId + ":" + id;
  }
  function isOpen(orgId, id) {
    return !!desk.open[openKey(orgId, id)];
  }
  function setOpen(orgId, id, on) {
    if (on) desk.open[openKey(orgId, id)] = 1;
    else delete desk.open[openKey(orgId, id)];
    saveOpen();
  }
  function expandTo(orgId, id) {
    var kb = K().kbOf(orgId);
    var n = kb && K().nodeOf(kb, id);
    var changed = false;
    while (n && n.parent_id != null) {
      if (!isOpen(orgId, n.parent_id)) {
        desk.open[openKey(orgId, n.parent_id)] = 1;
        changed = true;
      }
      n = K().nodeOf(kb, n.parent_id);
    }
    if (changed) saveOpen();
  }
  function orgs() {
    return (K() && K().state.orgs) || [];
  }
  function sectionPathNodes(kb, id) {
    return K().crumbs(kb, null, id);
  }
  function ownerName(kb, d) {
    return d.owner_user_id ? K().principalName(kb, "u:" + d.owner_user_id) : "";
  }
  function docsAll() {
    var out = [];
    orgs().forEach(function (o) {
      (o.kb.nodes || []).forEach(function (n) {
        if (n.kind === "doc" && !n.restricted) out.push({ org: o.org, kb: o.kb, node: n });
      });
    });
    return out;
  }
  function whereOf(kb, org, n) {
    var p = n.parent_id != null ? sectionPathNodes(kb, n.parent_id) : [];
    return (orgs().length > 1 ? [org.name] : []).concat(p.map(function (x) { return x.title; })).join(" › ");
  }
  function hl(text, q) {
    var s = esc(text);
    var words = String(q || "").toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) || [];
    words.forEach(function (w) {
      var stem = w.length > 5 ? w.slice(0, w.length - 2) : w;
      var re = new RegExp("(" + stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[\\p{L}\\p{N}]*)", "giu");
      s = s.replace(re, "<mark>$1</mark>");
    });
    return s;
  }

  // ---------- включение вида ----------

  function isOn() {
    return wide() && panelVisible() && orgs().length > 0;
  }

  function sync() {
    var root = document.documentElement;
    var on = isOn();
    var was = desk.on;
    desk.on = on;
    root.classList.toggle("kb-desk", on);
    root.classList.toggle("kb-tree-hidden", on && desk.hidden);
    root.classList.toggle("kb-view-mine", on && desk.view === "mine");
    root.style.setProperty("--kb-tree-w", desk.width + "px");
    syncMineHead(on && desk.view === "mine");
    if (on) {
      ensureDom();
      render();
    } else if (was) {
      removeDocPath();
    }
  }

  // «Мои документы» на компьютере — прежний экран вкладки без разделов, со своим заголовком.
  function syncMineHead(mine) {
    var root = document.getElementById("knowledge-root");
    var h = root && root.querySelector(".knowledge-list-head .screen-title");
    var lead = root && root.querySelector(".knowledge-list-head .lead");
    if (h) {
      if (h._kbOrig == null) h._kbOrig = h.textContent;
      h.textContent = mine ? "Мои документы" : h._kbOrig;
    }
    if (lead) {
      if (lead._kbOrig == null) lead._kbOrig = lead.textContent;
      lead.textContent = mine ? "Видите только вы, а Лео отвечает и по ним" : lead._kbOrig;
    }
  }

  function ensureDom() {
    if (!document.getElementById("kb-tree")) {
      var t = document.createElement("aside");
      t.id = "kb-tree";
      t.className = "kb-tree";
      t.setAttribute("aria-label", "Разделы базы знаний");
      document.body.appendChild(t);
      t.addEventListener("click", onTreeClick);
      t.addEventListener("keydown", onTreeKey);
      t.addEventListener("input", onTreeInput);
      t.addEventListener("dragstart", onDragStart);
      t.addEventListener("dragover", onDragOver);
      t.addEventListener("dragleave", onDragLeave);
      t.addEventListener("drop", onDrop);
      t.addEventListener("dragend", onDragEnd);
      t.addEventListener("pointerdown", onResizeStart);
    }
    var p = panel();
    if (p && !document.getElementById("kb-desk")) {
      var d = document.createElement("div");
      d.id = "kb-desk";
      d.className = "kb-desk-page";
      p.insertBefore(d, p.firstChild);
      d.addEventListener("click", onPageClick);
      d.addEventListener("input", onPageInput);
      d.addEventListener("keydown", onPageKey);
    }
  }

  function render() {
    if (!desk.on || !K()) return;
    ensureDom();
    renderTree();
    renderPage();
  }

  // ---------- дерево ----------

  function nodeMatches(kb, n, q) {
    if (String(n.title || "").toLowerCase().indexOf(q) >= 0) return true;
    return K().childrenOf(kb, n.id).some(function (c) { return nodeMatches(kb, c, q); });
  }

  function isCurrent(orgId, n) {
    if (n.kind === "doc") return !!desk.noteId && String(n.note_id) === String(desk.noteId);
    return !desk.noteId && desk.view === "section" && desk.orgId === orgId && desk.sectionId === n.id;
  }

  function nodeHtml(o, n, depth) {
    var kb = o.kb;
    var q = desk.treeQ.toLowerCase();
    if (q && !nodeMatches(kb, n, q)) return "";
    var isSec = n.kind === "section";
    var kids = isSec ? K().childrenOf(kb, n.id) : [];
    var open = isSec && (q ? true : isOpen(o.org.id, n.id));
    var cur = isCurrent(o.org.id, n);
    var role = n.my_role || "";
    var canAdd = isSec && !n.restricted && K().atLeast(role, "editor");
    var drag = !n.restricted && role === "owner";
    var cls = "kb-node" + (cur ? " is-current" : "") + (open ? " is-open" : "") + (n.restricted ? " is-restricted" : "") + (isSec ? " is-section" : " is-doc");
    var ico = n.restricted ? K().ICO.lock : isSec ? K().ICO.folder : K().ICO.doc;
    var docs = isSec ? K().docsUnder(kb, n.id).length : 0;
    var html = '<div class="' + cls + '" role="treeitem" tabindex="-1" style="--kb-depth:' + depth + '"' +
      ' data-org="' + o.org.id + '" data-id="' + n.id + '" data-kind="' + n.kind + '"' +
      (isSec ? ' aria-expanded="' + open + '"' : "") + (cur ? ' aria-current="page"' : "") +
      (drag ? ' draggable="true"' : "") + ">" +
      (isSec && kids.length
        ? '<button type="button" class="kb-chev" data-d="toggle" tabindex="-1" aria-label="' + (open ? "Свернуть" : "Развернуть") + '">' + IC.chev + "</button>"
        : '<span class="kb-chev kb-chev--none" aria-hidden="true"></span>') +
      '<span class="kb-n-ico" aria-hidden="true">' + ico + "</span>" +
      '<span class="kb-n-title">' + (q ? hl(n.title, q) : esc(n.title)) + "</span>" +
      (n.stale ? '<span class="kb-n-stale" title="Давно не проверялся"></span>' : "") +
      (isSec ? '<span class="kb-n-count">' + docs + "</span>" : "") +
      (n.restricted ? "" : '<span class="kb-n-act">' +
        (canAdd ? '<button type="button" class="kb-n-btn" data-d="add" tabindex="-1" aria-label="Добавить в раздел" title="Документ или подраздел">' + K().ICO.plus + "</button>" : "") +
        '<button type="button" class="kb-n-btn" data-d="more" tabindex="-1" aria-label="Действия" title="Действия">' + IC.more + "</button></span>") +
      "</div>";
    if (isSec && open && kids.length) {
      html += '<div class="kb-kids" role="group">' + kids.map(function (c) { return nodeHtml(o, c, depth + 1); }).join("") + "</div>";
    }
    return html;
  }

  function orgTreeHtml(o) {
    var kb = o.kb;
    var tops = K().childrenOf(kb, null);
    var logo = o.org.logo_url ? '<img src="' + esc(o.org.logo_url) + '" alt="" />' : esc(K().initials(o.org.name).charAt(0));
    var head = '<div class="kb-org-row"><span class="kb-org-logo" aria-hidden="true">' + logo + "</span>" +
      '<span class="kb-org-name">' + esc(o.org.name) + "</span>" +
      (kb.can_create_root ? '<button type="button" class="kb-n-btn" data-d="new-root" data-org="' + o.org.id + '" aria-label="Новый раздел" title="Новый раздел">' + K().ICO.plus + "</button>" : "") +
      "</div>";
    var body = tops.map(function (n) { return nodeHtml(o, n, 0); }).join("");
    if (!tops.length) {
      body = '<p class="kb-tree-hint">' + (kb.can_create_root ? "Разделов пока нет — нажмите «+»." : "Вам ещё не открыли ни одного раздела.") + "</p>";
    }
    return '<div class="kb-org" role="group" aria-label="' + esc(o.org.name) + '">' + head + body + "</div>";
  }

  function renderTree() {
    var t = document.getElementById("kb-tree");
    if (!t) return;
    var input = t.querySelector("#kb-tree-q");
    var hadFocus = input && document.activeElement === input;
    var caret = hadFocus ? input.selectionStart : null;
    var scroll = t.querySelector(".kb-tree-scroll");
    var top = scroll ? scroll.scrollTop : 0;
    var homeCur = !desk.noteId && desk.view === "home";
    var mineCur = !desk.noteId && desk.view === "mine";
    var srcCount = (document.getElementById("knowledge-sources-count") || {}).textContent || "";
    var orgsHtml = orgs().map(orgTreeHtml).join("");
    t.innerHTML =
      '<div class="kb-tree-top">' +
        '<div class="kb-tree-title"><span>База знаний</span>' +
          '<button type="button" class="kb-n-btn" data-d="hide" aria-label="Спрятать дерево" title="Спрятать дерево">' + IC.side + "</button></div>" +
        '<label class="kb-tree-search">' + IC.search +
          '<input type="search" id="kb-tree-q" placeholder="Найти в базе" autocomplete="off" aria-label="Найти раздел или документ" value="' + esc(desk.treeQ) + '" />' +
          '<kbd class="kb-kbd">' + (/Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘K" : "Ctrl K") + "</kbd></label>" +
      "</div>" +
      '<div class="kb-tree-scroll" role="tree" aria-label="Разделы и документы">' +
        '<div class="kb-node kb-node--home' + (homeCur ? " is-current" : "") + '" role="treeitem" tabindex="-1" data-d="home" style="--kb-depth:0">' +
          '<span class="kb-chev kb-chev--none" aria-hidden="true"></span><span class="kb-n-ico" aria-hidden="true">' + IC.home + '</span><span class="kb-n-title">Главная</span></div>' +
        orgsHtml +
        (desk.treeQ ? '<button type="button" class="kb-tree-fulltext" data-d="fulltext">' + IC.search + "<span>Искать «" + esc(desk.treeQ) + "» в тексте</span></button>" : "") +
      "</div>" +
      '<div class="kb-tree-foot">' +
        '<button type="button" class="kb-foot-row' + (mineCur ? " is-current" : "") + '" data-d="mine">' + IC.mine + "<span>Мои документы</span></button>" +
        '<button type="button" class="kb-foot-row" data-d="sources">' + IC.link + "<span>Источники</span>" + (srcCount ? '<span class="kb-foot-val">' + esc(srcCount) + "</span>" : "") + "</button>" +
        '<button type="button" class="kb-foot-row" data-d="ai">' + IC.spark + "<span>Доступ для ИИ</span></button>" +
      "</div>" +
      '<div class="kb-tree-resizer" role="separator" aria-orientation="vertical" aria-label="Ширина дерева" title="Потяните, чтобы изменить ширину"></div>';
    var sc = t.querySelector(".kb-tree-scroll");
    if (sc) sc.scrollTop = top;
    if (hadFocus) {
      var ni = t.querySelector("#kb-tree-q");
      ni.focus();
      if (caret != null) ni.setSelectionRange(caret, caret);
    }
    var cur = t.querySelector(".kb-node.is-current");
    if (cur && t._scrollToCurrent) {
      t._scrollToCurrent = false;
      if (cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
    }
  }

  function rowNode(row) {
    var orgId = Number(row.getAttribute("data-org"));
    var id = Number(row.getAttribute("data-id"));
    var kb = K().kbOf(orgId);
    return { orgId: orgId, kb: kb, org: K().orgOf(orgId), node: kb && K().nodeOf(kb, id) };
  }

  function onTreeClick(e) {
    var b = e.target.closest("[data-d]");
    var row = e.target.closest(".kb-node[data-id]");
    var act = b && b.getAttribute("data-d");
    if (act === "toggle" && row) {
      var r = rowNode(row);
      setOpen(r.orgId, r.node.id, !isOpen(r.orgId, r.node.id));
      renderTree();
      return;
    }
    if (act === "add" && row) return openMenu(b, addItems(rowNode(row)));
    if (act === "more" && row) return openMenu(b, moreItems(rowNode(row)));
    if (act === "new-root") return K().newRoot(Number(b.getAttribute("data-org")));
    if (act === "hide") return setHidden(true);
    if (act === "home") return goHome();
    if (act === "mine") return goMine();
    if (act === "fulltext") {
      desk.q = desk.treeQ;
      goHome();
      runSearch();
      return;
    }
    if (act === "sources") {
      var s = document.getElementById("knowledge-sources-open");
      if (s) s.click();
      return;
    }
    if (act === "ai") {
      var a = document.getElementById("knowledge-ai-open");
      if (a) a.click();
      return;
    }
    if (row) activateRow(row);
  }

  function activateRow(row) {
    var r = rowNode(row);
    if (!r.node) return;
    if (r.node.kind === "doc") {
      openDoc(r.orgId, r.node);
      return;
    }
    if (!isOpen(r.orgId, r.node.id)) setOpen(r.orgId, r.node.id, true);
    showSection(r.orgId, r.node.id);
  }

  function visibleRows() {
    var t = document.getElementById("kb-tree");
    return t ? Array.prototype.slice.call(t.querySelectorAll(".kb-tree-scroll .kb-node")) : [];
  }

  function onTreeKey(e) {
    var row = e.target.closest && e.target.closest(".kb-node");
    if (e.target.id === "kb-tree-q") {
      if (e.key === "ArrowDown") {
        var first = visibleRows()[desk.treeQ ? 1 : 0];
        if (first) {
          e.preventDefault();
          first.focus();
        }
      } else if (e.key === "Escape" && desk.treeQ) {
        e.preventDefault();
        desk.treeQ = "";
        renderTree();
      } else if (e.key === "Enter") {
        var hit = visibleRows().filter(function (r) { return r.getAttribute("data-kind") === "doc"; })[0];
        if (hit) activateRow(hit);
      }
      return;
    }
    if (!row) return;
    var rows = visibleRows();
    var i = rows.indexOf(row);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      var next = rows[i + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) next.focus();
    } else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && row.hasAttribute("data-id")) {
      var r = rowNode(row);
      if (!r.node || r.node.kind !== "section") return;
      e.preventDefault();
      var want = e.key === "ArrowRight";
      if (isOpen(r.orgId, r.node.id) !== want) {
        setOpen(r.orgId, r.node.id, want);
        renderTree();
        var again = document.querySelector('#kb-tree .kb-node[data-org="' + r.orgId + '"][data-id="' + r.node.id + '"]');
        if (again) again.focus();
      }
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (row.getAttribute("data-d") === "home") goHome();
      else activateRow(row);
    }
  }

  function onTreeInput(e) {
    if (e.target.id !== "kb-tree-q") return;
    desk.treeQ = String(e.target.value || "").trim();
    renderTree();
  }

  function setHidden(h) {
    desk.hidden = !!h;
    lsSet(TREE_HIDDEN_LS, h ? "1" : "0");
    document.documentElement.classList.toggle("kb-tree-hidden", desk.on && desk.hidden);
    renderPage();
    refreshDocPath();
  }

  // ---------- меню «+» и «⋯» ----------

  function addItems(r) {
    return [
      { label: "Документ", run: function () { K().newDoc(r.orgId, r.node.id); } },
      { label: "Подраздел", run: function () { K().newSub(r.orgId, r.node.id); } },
      { label: "Добавить свой документ…", run: function () { K().addMyDoc(r.orgId, r.node.id); } },
    ];
  }

  function moreItems(r) {
    var n = r.node;
    var role = n.my_role || "";
    var items = [];
    if (n.kind === "doc") {
      items.push({ label: "Открыть", run: function () { openDoc(r.orgId, n); } });
      if (role === "owner") {
        items.push({ label: "Доступ", run: function () { K().openAccess(r.orgId, n.id); } });
        items.push({ label: "Переместить…", run: function () { K().placeNote(n.note_id); } });
      }
      return items;
    }
    items.push({ label: "Открыть раздел", run: function () { showSection(r.orgId, n.id); } });
    if (K().atLeast(role, "editor")) items.push({ label: "Переименовать", run: function () { K().renameSection(r.orgId, n.id); } });
    if (role === "owner") {
      items.push({ label: "Доступ", run: function () { K().openAccess(r.orgId, n.id); } });
      if (n.parent_id != null) items.push({ label: "Переместить…", run: function () { K().moveSection(r.orgId, n.id); } });
      items.push({ label: "Убрать раздел", danger: true, run: function () { removeSectionHere(r.orgId, n.id); } });
    }
    return items;
  }

  async function removeSectionHere(orgId, id) {
    await K().removeSection(orgId, id);
    if (desk.view === "section" && desk.orgId === orgId && !K().nodeOf(K().kbOf(orgId), desk.sectionId)) goHome();
  }

  function closeMenu() {
    var m = document.getElementById("kb-menu");
    if (m) m.remove();
    document.removeEventListener("pointerdown", menuOutside, true);
    document.removeEventListener("keydown", menuKey, true);
  }
  function menuOutside(e) {
    if (!e.target.closest || !e.target.closest("#kb-menu")) closeMenu();
  }
  function menuKey(e) {
    if (e.key === "Escape") {
      e.stopPropagation();
      closeMenu();
    }
  }

  function openMenu(anchor, items) {
    closeMenu();
    if (!items.length) return;
    var m = document.createElement("div");
    m.id = "kb-menu";
    m.className = "kb-menu";
    m.setAttribute("role", "menu");
    m.innerHTML = items.map(function (it, i) {
      return '<button type="button" role="menuitem" class="kb-menu-item' + (it.danger ? " is-danger" : "") + '" data-i="' + i + '">' + esc(it.label) + "</button>";
    }).join("");
    document.body.appendChild(m);
    var r = anchor.getBoundingClientRect();
    var left = Math.min(window.innerWidth - m.offsetWidth - 8, r.left);
    var top = r.bottom + 4;
    if (top + m.offsetHeight > window.innerHeight - 8) top = Math.max(8, r.top - m.offsetHeight - 4);
    m.style.left = Math.max(8, left) + "px";
    m.style.top = top + "px";
    m.addEventListener("click", function (e) {
      var b = e.target.closest("[data-i]");
      if (!b) return;
      var it = items[Number(b.getAttribute("data-i"))];
      closeMenu();
      if (it) it.run();
    });
    var firstItem = m.querySelector("button");
    if (firstItem) firstItem.focus();
    setTimeout(function () {
      document.addEventListener("pointerdown", menuOutside, true);
      document.addEventListener("keydown", menuKey, true);
    }, 0);
  }

  // ---------- перетаскивание ----------

  function inSubtree(kb, rootId, id) {
    if (rootId === id) return true;
    return K().childrenOf(kb, rootId).some(function (c) { return c.kind === "section" && inSubtree(kb, c.id, id); });
  }

  function dropZone(e, row) {
    var target = rowNode(row);
    var d = desk.drag;
    if (!d || !target.node || target.orgId !== d.orgId || target.node.id === d.node.id || target.node.restricted) return null;
    if (d.node.kind === "section" && inSubtree(target.kb, d.node.id, target.node.id)) return null;
    var rect = row.getBoundingClientRect();
    var y = (e.clientY - rect.top) / rect.height;
    var canInto = target.node.kind === "section" && K().atLeast(target.node.my_role, "editor");
    if (canInto && y > 0.25 && y < 0.75) return { zone: "into", target: target };
    // между соседями: тот же родитель — просто порядок; другой раздел — перенос с местом
    var parentId = target.node.parent_id;
    if (parentId !== d.node.parent_id) {
      if (parentId == null) return canInto ? { zone: "into", target: target } : null;
      var parent = K().nodeOf(target.kb, parentId);
      if (!parent || !K().atLeast(parent.my_role, "editor")) return canInto ? { zone: "into", target: target } : null;
    }
    return { zone: y < 0.5 ? "before" : "after", target: target };
  }

  function clearDropMarks() {
    document.querySelectorAll("#kb-tree .kb-drop-into, #kb-tree .kb-drop-before, #kb-tree .kb-drop-after").forEach(function (el) {
      el.classList.remove("kb-drop-into", "kb-drop-before", "kb-drop-after");
    });
  }

  function onDragStart(e) {
    var row = e.target.closest && e.target.closest(".kb-node[draggable]");
    if (!row) return;
    var r = rowNode(row);
    if (!r.node) return;
    desk.drag = r;
    row.classList.add("is-dragging");
    try {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", r.node.title);
    } catch (_) {}
  }
  function onDragOver(e) {
    if (!desk.drag) return;
    var row = e.target.closest && e.target.closest(".kb-node[data-id]");
    clearDropMarks();
    if (!row) return;
    var z = dropZone(e, row);
    if (!z) return;
    e.preventDefault();
    try {
      e.dataTransfer.dropEffect = "move";
    } catch (_) {}
    row.classList.add("kb-drop-" + z.zone);
  }
  function onDragLeave(e) {
    if (e.target.classList && e.target.classList.contains("kb-node")) e.target.classList.remove("kb-drop-into", "kb-drop-before", "kb-drop-after");
  }
  function onDragEnd() {
    desk.drag = null;
    clearDropMarks();
    document.querySelectorAll("#kb-tree .is-dragging").forEach(function (el) { el.classList.remove("is-dragging"); });
  }
  async function onDrop(e) {
    var d = desk.drag;
    var row = e.target.closest && e.target.closest(".kb-node[data-id]");
    var z = d && row ? dropZone(e, row) : null;
    onDragEnd();
    if (!z) return;
    e.preventDefault();
    var t = z.target;
    var body = {};
    if (z.zone === "into") {
      if (d.node.parent_id === t.node.id) return;
      body.parent_id = t.node.id;
      setOpen(t.orgId, t.node.id, true);
    } else {
      var sib = K().childrenOf(t.kb, t.node.parent_id).filter(function (s) { return s.id !== d.node.id; });
      var i = sib.indexOf(t.node);
      var other = z.zone === "before" ? sib[i - 1] : sib[i + 1];
      var pos = t.node.position;
      if (pos == null) pos = i;
      body.position = other && other.position != null ? (pos + other.position) / 2 : z.zone === "before" ? pos - 1 : pos + 1;
      if (t.node.parent_id !== d.node.parent_id) body.parent_id = t.node.parent_id;
    }
    try {
      var res = await K().api("/" + d.orgId + "/kb/nodes/" + d.node.id, K().json("PATCH", body));
      await K().refreshOrg(d.orgId, res);
      if (body.parent_id != null) K().toast("Перенесено в «" + (K().nodeOf(K().kbOf(d.orgId), body.parent_id) || {}).title + "»");
    } catch (err) {
      K().toast(K().errText(err));
    }
  }

  // ---------- ширина дерева ----------

  function onResizeStart(e) {
    if (!e.target.classList || !e.target.classList.contains("kb-tree-resizer")) return;
    e.preventDefault();
    var startX = e.clientX;
    var startW = desk.width;
    var root = document.documentElement;
    root.classList.add("kb-tree-resizing");
    function move(ev) {
      desk.width = clampW(startW + ev.clientX - startX);
      root.style.setProperty("--kb-tree-w", desk.width + "px");
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      root.classList.remove("kb-tree-resizing");
      lsSet(TREE_W_LS, String(desk.width));
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // ---------- навигация ----------

  async function closeDocIfOpen() {
    if (app().currentNoteId && app().currentNoteId()) {
      var ok = app().closeNote ? await app().closeNote() : true;
      if (ok === false) return false;
    }
    return true;
  }

  async function goHome() {
    if (!(await closeDocIfOpen())) return;
    desk.view = "home";
    desk.noteId = "";
    sync();
    scrollPageTop();
  }

  async function goMine() {
    if (!(await closeDocIfOpen())) return;
    desk.view = "mine";
    desk.noteId = "";
    sync();
    scrollPageTop();
  }

  async function showSection(orgId, id) {
    if (!(await closeDocIfOpen())) return;
    desk.view = "section";
    desk.orgId = orgId;
    desk.sectionId = id;
    desk.noteId = "";
    expandTo(orgId, id);
    var t = document.getElementById("kb-tree");
    if (t) t._scrollToCurrent = true;
    sync();
    scrollPageTop();
  }

  function openDoc(orgId, node) {
    if (!node || !node.note_id) return;
    desk.noteId = String(node.note_id);
    expandTo(orgId, node.id);
    var t = document.getElementById("kb-tree");
    if (t) t._scrollToCurrent = true;
    renderTree();
    K().openNoteAny(node.note_id);
  }

  function openNoteId(noteId) {
    var hit = K().nodeForNote(noteId);
    if (hit) return openDoc(hit.org.id, hit.node);
    desk.noteId = String(noteId);
    renderTree();
    K().openNoteAny(noteId);
  }

  function scrollPageTop() {
    var ms = document.querySelector(".main-scroll");
    if (ms) ms.scrollTop = 0;
  }

  // ---------- страница: главная и раздел ----------

  function barHtml(pathHtml, actions) {
    return '<div class="kb-bar">' +
      (desk.hidden ? '<button type="button" class="kb-n-btn kb-bar-tree" data-p="show-tree" aria-label="Показать дерево" title="Показать дерево">' + IC.side + "</button>" : "") +
      '<nav class="kb-path" aria-label="Путь">' + pathHtml + "</nav>" +
      '<span class="kb-bar-actions">' + (actions || "") + "</span></div>";
  }

  function pathHtml(orgId, id, opts) {
    opts = opts || {};
    var html = '<button type="button" data-p="home">База знаний</button>';
    if (orgId == null) return html;
    var kb = K().kbOf(orgId);
    var org = K().orgOf(orgId);
    if (orgs().length > 1 && org) html += '<span class="kb-path-sep">›</span><span>' + esc(org.name) + "</span>";
    var nodes = id != null ? sectionPathNodes(kb, id) : [];
    nodes.forEach(function (n, i) {
      var last = i === nodes.length - 1 && !opts.linkLast;
      html += '<span class="kb-path-sep">›</span>' + (last
        ? '<span class="kb-path-cur" aria-current="page">' + esc(n.title) + "</span>"
        : '<button type="button" data-p="section" data-org="' + orgId + '" data-id="' + n.id + '">' + esc(n.title) + "</button>");
    });
    return html;
  }

  function btn(label, attrs, cls, ico) {
    return '<button type="button" class="kb-btn ' + (cls || "") + '" ' + attrs + ">" + (ico || "") + "<span>" + esc(label) + "</span></button>";
  }

  function statusHtml(d) {
    if (d.no_expiry) return '<span class="kb-st">не устаревает</span>';
    if (d.stale) return '<span class="kb-st kb-st--warn"><i></i>не проверялся ' + esc(K().ago(d.verified_at || d.updated_at, true)) + "</span>";
    return '<span class="kb-st"><i></i>проверено ' + esc(K().ago(d.verified_at)) + "</span>";
  }

  function docLine(it, right) {
    var d = it.node;
    return '<button type="button" class="kb-line" data-p="doc" data-org="' + it.org.id + '" data-id="' + d.id + '">' +
      '<span class="kb-line-ico" aria-hidden="true">' + K().ICO.doc + "</span>" +
      '<span class="kb-line-text"><b>' + esc(d.title) + "</b><span>" + esc(whereOf(it.kb, it.org, d)) + "</span></span>" +
      '<span class="kb-line-who">' + esc(ownerName(it.kb, d)) + "</span>" +
      '<span class="kb-line-right">' + right + "</span></button>";
  }

  function homeHtml() {
    var all = orgs();
    var single = all.length === 1 ? all[0].org : null;
    var docs = docsAll();
    var secCount = 0;
    all.forEach(function (o) { secCount += (o.kb.nodes || []).filter(function (n) { return n.kind === "section" && !n.restricted; }).length; });
    var canCreate = K().editableSections().length > 0;
    var html = barHtml(pathHtml(null), canCreate ? btn("Документ", 'data-p="new-doc"', "kb-btn--primary", K().ICO.plus) : "");
    html += '<div class="kb-page">';
    html += '<h1 class="screen-title kb-title">База знаний' + (single ? " " + esc(single.name) : "") + "</h1>";
    html += '<p class="muted lead lead-tight">Документы компании — Лео отвечает по ним. Вам открыто ' +
      esc(K().count(secCount, "раздел", "раздела", "разделов")) + ", " + esc(K().count(docs.length, "документ", "документа", "документов")) + ".</p>";
    html += '<label class="kb-search">' + IC.search +
      '<input type="search" id="kb-desk-q" placeholder="Найти по названию и тексту или спросить Лео" autocomplete="off" enterkeyhint="search" aria-label="Поиск по базе знаний" value="' + esc(desk.q) + '" />' +
      "</label>";
    if (desk.q) {
      html += resultsHtml();
    } else {
      all.forEach(function (o) {
        var tops = K().childrenOf(o.kb, null).filter(function (n) { return n.kind === "section"; });
        html += '<p class="kb-label">' + (all.length > 1 ? esc(o.org.name) : "Разделы") + "</p>";
        var cards = tops.map(function (s) {
          var dd = K().docsUnder(o.kb, s.id);
          var stale = dd.filter(function (x) { return x.stale; }).length;
          var meta = s.restricted
            ? "Вам открыта часть раздела"
            : K().count(dd.length, "документ", "документа", "документов") + " · открыт: " + K().accessLine(o.kb, s);
          return '<button type="button" class="kb-card' + (s.restricted ? " is-restricted" : "") + '" data-p="section" data-org="' + o.org.id + '" data-id="' + s.id + '">' +
            '<span class="kb-card-top"><span class="kb-card-ico" aria-hidden="true">' + (s.restricted ? K().ICO.lock : K().ICO.folder) + "</span><b>" + esc(s.title) + "</b></span>" +
            '<span class="kb-card-meta">' + esc(meta) + (stale ? ' · <span class="kb-st kb-st--warn"><i></i>' + esc(K().count(stale, "устаревший", "устаревших", "устаревших")) + "</span>" : "") + "</span></button>";
        }).join("");
        if (o.kb.can_create_root) {
          cards += '<button type="button" class="kb-card kb-card--add" data-p="new-root" data-org="' + o.org.id + '"><span class="kb-card-top">' + K().ICO.plus + "<b>Новый раздел</b></span></button>";
        }
        html += cards ? '<div class="kb-cards">' + cards + "</div>" : '<p class="pf-hint">Коллеги ещё не открыли вам ни одного раздела.</p>';
      });
      var stale = docs.filter(function (it) { return it.node.stale && !it.node.no_expiry && K().atLeast(it.node.my_role, "editor"); });
      if (stale.length) {
        html += '<p class="kb-label">Нужно проверить<span class="kb-label-note">вы можете править, не проверялись ' + esc(K().count(180, "день", "дня", "дней")) + "</span></p>";
        html += '<div class="kb-lines">' + stale.slice(0, 8).map(function (it) { return docLine(it, statusHtml(it.node)); }).join("") + "</div>";
      }
      var recent = docs.filter(function (it) { return it.node.updated_at; })
        .sort(function (a, b) { return String(b.node.updated_at).localeCompare(String(a.node.updated_at)); }).slice(0, 6);
      if (recent.length) {
        html += '<p class="kb-label">Недавно изменены</p>';
        html += '<div class="kb-lines">' + recent.map(function (it) { return docLine(it, esc(K().ago(it.node.updated_at))); }).join("") + "</div>";
      }
    }
    return html + "</div>";
  }

  function resultsHtml() {
    var html = '<div class="kb-lines kb-results">';
    html += '<button type="button" class="kb-line kb-line--ask" data-p="ask"><span class="kb-line-ico" aria-hidden="true">' + IC.spark + "</span>" +
      '<span class="kb-line-text"><b>Спросить Лео</b><span>«' + esc(desk.q) + "» — ответит по документам базы</span></span></button>";
    var items = desk.results && desk.results.q === desk.q ? desk.results.items : null;
    if (!items) {
      html += '<p class="kb-results-note">Ищу…</p>';
    } else if (!items.length) {
      html += '<p class="kb-results-note">В документах ничего не нашлось — спросите Лео или переформулируйте.</p>';
    } else {
      html += items.map(function (it) {
        return '<button type="button" class="kb-line kb-line--hit" data-p="note" data-note="' + it.note_id + '">' +
          '<span class="kb-line-ico" aria-hidden="true">' + K().ICO.doc + "</span>" +
          '<span class="kb-line-text"><b>' + hl(it.title, desk.q) + (it.stale ? ' <span class="kb-st kb-st--warn"><i></i>давно не проверялся</span>' : "") + "</b>" +
          "<span>" + esc(it.where) + "</span>" +
          (it.snippet ? '<span class="kb-snippet">' + hl(it.snippet, desk.q) + "</span>" : "") + "</span></button>";
      }).join("");
    }
    return html + "</div>";
  }

  var searchTimer = null;
  var searchSeq = 0;
  function runSearch() {
    clearTimeout(searchTimer);
    var q = desk.q;
    if (!q) {
      desk.results = null;
      return;
    }
    var seq = ++searchSeq;
    searchTimer = setTimeout(async function () {
      try {
        var r = await app().apiFetch("/kb/search?q=" + encodeURIComponent(q) + "&limit=12", { method: "GET" });
        if (seq !== searchSeq) return;
        desk.results = { q: q, items: (r && r.items) || [] };
      } catch (_) {
        if (seq !== searchSeq) return;
        desk.results = { q: q, items: [] };
      }
      paintResults();
    }, 250);
  }

  function paintResults() {
    var page = document.querySelector("#kb-desk .kb-page");
    if (!page || desk.view !== "home") return;
    var old = page.querySelector(".kb-results");
    var wrap = document.createElement("div");
    wrap.innerHTML = resultsHtml();
    if (old) old.replaceWith(wrap.firstChild);
  }

  function sectionHtml() {
    var kb = K().kbOf(desk.orgId);
    var org = K().orgOf(desk.orgId);
    var sec = kb && K().nodeOf(kb, desk.sectionId);
    if (!sec || !org) {
      desk.view = "home";
      return homeHtml();
    }
    var role = sec.my_role || "";
    var canEdit = !sec.restricted && K().atLeast(role, "editor");
    var actions = "";
    if (role === "owner" && !sec.restricted) actions += btn("Доступ", 'data-p="access"', "kb-btn--ghost", IC.group);
    if (!sec.restricted && (canEdit || role === "owner")) actions += '<button type="button" class="kb-n-btn kb-bar-more" data-p="more" aria-label="Действия с разделом" title="Действия с разделом">' + IC.more + "</button>";
    if (canEdit) actions += btn("Документ", 'data-p="new-doc-here"', "kb-btn--primary", K().ICO.plus);
    var html = barHtml(pathHtml(desk.orgId, sec.id), actions);
    html += '<div class="kb-page">';
    html += '<h1 class="screen-title kb-title">' + esc(sec.title) + "</h1>";
    html += '<p class="muted lead lead-tight">' + (sec.restricted
      ? "Вам открыта только часть раздела"
      : esc("Открыт: " + K().accessLine(kb, sec))) + "</p>";
    var kids = K().childrenOf(kb, sec.id);
    html += '<p class="kb-label">В разделе' + (kids.length ? " · " + kids.length : "") + "</p>";
    var rows = kids.map(function (k) {
      if (k.kind === "section") {
        var dd = K().docsUnder(kb, k.id);
        var st = dd.some(function (x) { return x.stale; })
          ? '<span class="kb-st kb-st--warn"><i></i>есть устаревшие</span>'
          : dd.length ? '<span class="kb-st"><i></i>всё проверено</span>' : "";
        return '<button type="button" class="kb-row" data-p="section" data-org="' + desk.orgId + '" data-id="' + k.id + '">' +
          '<span class="kb-row-ico" aria-hidden="true">' + (k.restricted ? K().ICO.lock : K().ICO.folder) + "</span>" +
          '<span class="kb-row-title"><b>' + esc(k.title) + "</b><span>Подраздел · " + esc(K().count(dd.length, "документ", "документа", "документов")) + "</span></span>" +
          '<span class="kb-row-c"></span><span class="kb-row-c"></span><span class="kb-row-c">' + st + "</span></button>";
      }
      return '<button type="button" class="kb-row" data-p="doc" data-org="' + desk.orgId + '" data-id="' + k.id + '">' +
        '<span class="kb-row-ico" aria-hidden="true">' + K().ICO.doc + "</span>" +
        '<span class="kb-row-title"><b>' + esc(k.title) + "</b></span>" +
        '<span class="kb-row-c">' + esc(ownerName(kb, k)) + "</span>" +
        '<span class="kb-row-c">' + esc(K().ago(k.updated_at)) + "</span>" +
        '<span class="kb-row-c">' + statusHtml(k) + "</span></button>";
    }).join("");
    html += '<div class="kb-table">';
    if (kids.length) {
      html += '<div class="kb-row kb-row--head" aria-hidden="true"><span></span><span>Название</span><span>Владелец</span><span>Изменён</span><span>Проверка</span></div>' + rows;
    } else {
      html += '<p class="kb-empty">В разделе пока пусто.</p>';
    }
    if (canEdit) {
      html += '<div class="kb-table-add">' +
        btn("Документ", 'data-p="new-doc-here"', "", K().ICO.plus) +
        btn("Подраздел", 'data-p="new-sub"', "", K().ICO.folder) +
        btn("Добавить свой документ…", 'data-p="add-doc"', "kb-btn--ghost") + "</div>";
    }
    html += "</div>";
    if (canEdit) html += '<p class="pf-hint">Документы наследуют доступ раздела. Порядок и место меняются перетаскиванием в дереве слева.</p>';
    return html + "</div>";
  }

  function renderPage() {
    var el = document.getElementById("kb-desk");
    if (!el || !desk.on) return;
    if (desk.view === "mine") {
      el.innerHTML = barHtml('<button type="button" data-p="home">База знаний</button><span class="kb-path-sep">›</span><span class="kb-path-cur">Мои документы</span>', "");
      return;
    }
    var input = el.querySelector("#kb-desk-q");
    var hadFocus = input && document.activeElement === input;
    var caret = hadFocus ? input.selectionStart : null;
    el.innerHTML = desk.view === "section" ? sectionHtml() : homeHtml();
    if (hadFocus) {
      var ni = el.querySelector("#kb-desk-q");
      if (ni) {
        ni.focus();
        if (caret != null) ni.setSelectionRange(caret, caret);
      }
    }
  }

  function onPageClick(e) {
    var b = e.target.closest("[data-p]");
    if (!b) return;
    var p = b.getAttribute("data-p");
    var orgId = Number(b.getAttribute("data-org") || desk.orgId);
    var id = Number(b.getAttribute("data-id"));
    if (p === "home") return goHome();
    if (p === "show-tree") return setHidden(false);
    if (p === "section") return showSection(orgId, id);
    if (p === "doc") {
      var n = K().nodeOf(K().kbOf(orgId), id);
      if (n) openDoc(orgId, n);
      return;
    }
    if (p === "note") return openNoteId(b.getAttribute("data-note"));
    if (p === "ask") {
      if (app().askLeo) app().askLeo("По базе знаний: " + desk.q);
      return;
    }
    if (p === "new-doc") return K().openCreateChooser();
    if (p === "new-root") return K().newRoot(orgId);
    if (p === "new-doc-here") return K().newDoc(desk.orgId, desk.sectionId);
    if (p === "new-sub") return K().newSub(desk.orgId, desk.sectionId);
    if (p === "add-doc") return K().addMyDoc(desk.orgId, desk.sectionId);
    if (p === "access") return K().openAccess(desk.orgId, desk.sectionId);
    if (p === "more") {
      var kb = K().kbOf(desk.orgId);
      var r = { orgId: desk.orgId, kb: kb, org: K().orgOf(desk.orgId), node: K().nodeOf(kb, desk.sectionId) };
      if (r.node) openMenu(b, moreItems(r).filter(function (it) { return it.label !== "Открыть раздел"; }));
    }
  }

  function onPageInput(e) {
    if (e.target.id !== "kb-desk-q") return;
    var q = String(e.target.value || "").trim();
    var had = !!desk.q;
    desk.q = q;
    if (!q) desk.results = null;
    runSearch();
    // первый символ — страница переключается на результаты; дальше обновляются только они
    if (had !== !!q) renderPage();
    else paintResults();
  }

  function onPageKey(e) {
    if (e.target.id !== "kb-desk-q") return;
    if (e.key === "Escape" && desk.q) {
      e.preventDefault();
      desk.q = "";
      desk.results = null;
      renderPage();
    } else if (e.key === "Enter") {
      e.preventDefault();
      var first = document.querySelector("#kb-desk .kb-line--hit");
      if (first) first.click();
      else if (desk.q && app().askLeo) app().askLeo("По базе знаний: " + desk.q);
    }
  }

  // ---------- открытый документ ----------

  function removeDocPath() {
    var old = document.getElementById("kb-doc-path");
    if (old) old.remove();
  }

  // Путь — отдельной строкой над названием документа; кнопки шапки редактора («Проект», «⋯») — справа в той же строке.
  function refreshDocPath() {
    removeDocPath();
    var page = desk.noteCtx && desk.noteCtx.page;
    if (!desk.on || !page) return;
    var ctx = desk.noteCtx;
    var nav = document.createElement("nav");
    nav.id = "kb-doc-path";
    nav.className = "kb-path kb-doc-path";
    nav.setAttribute("aria-label", "Путь к документу");
    var html = desk.hidden ? '<button type="button" class="kb-n-btn" data-p="show-tree" aria-label="Показать дерево" title="Показать дерево">' + IC.side + "</button>" : "";
    if (ctx.hit) {
      html += pathHtml(ctx.hit.org.id, ctx.hit.node.parent_id, { linkLast: true });
    } else {
      html += '<button type="button" data-p="home">База знаний</button><span class="kb-path-sep">›</span><button type="button" data-p="mine">Мои документы</button>';
    }
    nav.innerHTML = html;
    nav.addEventListener("click", function (e) {
      var b = e.target.closest("[data-p]");
      if (!b) return;
      var p = b.getAttribute("data-p");
      if (p === "home") goHome();
      else if (p === "mine") goMine();
      else if (p === "show-tree") setHidden(false);
      else if (p === "section") showSection(Number(b.getAttribute("data-org")), Number(b.getAttribute("data-id")));
    });
    page.insertBefore(nav, page.firstChild);
  }

  function metaHtml(note, hit) {
    var kb = hit.kb;
    var n = hit.node;
    var info = note.org_kb || {};
    var role = info.role || n.my_role || "";
    var canEdit = K().atLeast(role, "editor");
    var stale = !!(info.stale && !info.no_expiry);
    var owner = ownerName(kb, n);
    var parent = n.parent_id != null ? K().nodeOf(kb, n.parent_id) : null;
    var parts = [];
    if (owner) parts.push('<span class="kb-meta-who"><span class="kb-meta-av" aria-hidden="true">' + esc(K().initials(owner)) + "</span>" + esc(owner) + "</span>");
    if (n.updated_at || note.updated_at) parts.push("<span>изменён " + esc(K().ago(note.updated_at || n.updated_at)) + "</span>");
    if (!stale) parts.push(statusHtml({ no_expiry: info.no_expiry, verified_at: info.verified_at }));
    if (parent && !parent.restricted) parts.push("<span>открыт: " + esc(K().accessLine(kb, parent)) + "</span>");
    if (role === "reader") parts.push("<span>только чтение</span>");
    else if (role === "commenter") parts.push("<span>можно комментировать</span>");
    var acts = "";
    if (!canEdit) {
      acts += note._kbRequested ? '<span class="kb-meta-done">Запрос отправлен</span>' : '<button type="button" class="pf-link" data-kb-banner="request">Попросить править</button>';
    }
    if (role === "owner") {
      acts += '<button type="button" class="pf-link" data-kb-banner="move">Переместить</button><button type="button" class="pf-link" data-kb-banner="access">Доступ</button>';
    }
    var html = '<div class="kb-meta-line">' + parts.join('<span class="kb-meta-dot" aria-hidden="true">·</span>') +
      (acts ? '<span class="kb-meta-acts">' + acts + "</span>" : "") + "</div>";
    if (stale) {
      html += '<div class="kb-banner kb-banner--stale">' + K().ICO.clock + "<span>" +
        esc("Не проверялся " + K().ago(info.verified_at, true) + (canEdit ? ". Если всё верно — отметьте, иначе поправьте текст." : ". Сведения могли устареть.")) + "</span>" +
        (canEdit ? '<button type="button" class="pf-link" data-kb-banner="verify">Всё актуально</button>' : "") + "</div>";
    }
    return html;
  }

  // Вызывает org-kb.js из события leo:note-open, когда вид с деревом включён. true — полоску не рисовать.
  function decorateNote(note, page) {
    if (!note) return false;
    var hit = K().nodeForNote(note.id);
    desk.noteId = String(note.id);
    desk.noteCtx = { note: note, page: page, hit: hit };
    if (hit) expandTo(hit.org.id, hit.node.id);
    var t = document.getElementById("kb-tree");
    if (t) t._scrollToCurrent = true;
    renderTree();
    refreshDocPath();
    if (!hit) return !note.org_kb; // документ организации, которой нет в дереве, — прежняя полоска
    var titlePad = page.querySelector(".note-editor-pad--title-in-body");
    var meta = document.createElement("div");
    meta.className = "kb-doc-meta";
    var repaint = function () { meta.innerHTML = metaHtml(note, K().nodeForNote(note.id) || hit); };
    repaint();
    meta.addEventListener("click", function (e) {
      var b = e.target.closest("[data-kb-banner]");
      if (b) K().bannerAction(b.getAttribute("data-kb-banner"), note, repaint);
    });
    if (titlePad && titlePad.parentNode) titlePad.parentNode.insertBefore(meta, titlePad.nextSibling);
    else page.insertBefore(meta, page.firstChild);
    mountDocExtras(note, page, hit);
    return true;
  }

  // Оглавление по заголовкам, «Ссылаются сюда» и «Предыдущий / Следующий» в разделе.
  function mountDocExtras(note, page, hit) {
    var layout = page.querySelector(".note-editor-doc-layout");
    var main = page.querySelector(".note-editor-main-column");
    if (!layout || !main) return;
    var rail = document.createElement("aside");
    rail.className = "kb-rail";
    rail.setAttribute("aria-label", "На странице");
    layout.appendChild(rail);
    var foot = document.createElement("div");
    foot.className = "kb-doc-foot";
    main.appendChild(foot);

    var sib = K().childrenOf(hit.kb, hit.node.parent_id).filter(function (k) { return k.kind === "doc" && !k.restricted; });
    var i = sib.indexOf(hit.node);
    var prev = i > 0 ? sib[i - 1] : null;
    var next = i >= 0 && i < sib.length - 1 ? sib[i + 1] : null;
    var nav = "";
    if (prev || next) {
      nav = '<div class="kb-prevnext">' +
        (prev ? '<button type="button" data-p="doc" data-org="' + hit.org.id + '" data-id="' + prev.id + '"><span>← Предыдущий</span><b>' + esc(prev.title) + "</b></button>" : "<span></span>") +
        (next ? '<button type="button" class="is-next" data-p="doc" data-org="' + hit.org.id + '" data-id="' + next.id + '"><span>Следующий →</span><b>' + esc(next.title) + "</b></button>" : "<span></span>") +
        "</div>";
    }
    var back = { items: null };
    function backHtml() {
      if (!back.items || !back.items.length) return "";
      return '<div class="kb-backlinks"><p class="kb-rail-title">Ссылаются сюда</p>' + back.items.map(function (b) {
        return '<button type="button" data-p="note" data-note="' + b.note_id + '" title="' + esc(b.where) + '">' + esc(b.title) + "</button>";
      }).join("") + "</div>";
    }
    function paintFoot() {
      foot.innerHTML = '<div class="kb-foot-backlinks">' + backHtml() + "</div>" + nav;
    }
    paintFoot();
    var heads = [];
    function paintRail() {
      var toc = heads.length > 1
        ? '<p class="kb-rail-title">На странице</p><div class="kb-toc">' + heads.map(function (h, j) {
          return '<button type="button" class="kb-toc-' + h.level + '" data-toc="' + j + '">' + esc(h.text) + "</button>";
        }).join("") + "</div>"
        : "";
      rail.innerHTML = toc + backHtml();
      rail.classList.toggle("is-empty", !toc && !backHtml());
      markActive();
    }
    function collect() {
      var body = page.querySelector(".note-editor-pad--body .ProseMirror") || page.querySelector(".note-editor-pad--body");
      var list = body ? Array.prototype.slice.call(body.querySelectorAll("h1, h2, h3")) : [];
      heads = list.map(function (el) {
        return { el: el, text: String(el.textContent || "").trim(), level: el.tagName === "H3" ? 3 : el.tagName === "H1" ? 1 : 2 };
      }).filter(function (h) { return h.text; }).slice(0, 60);
      paintRail();
    }
    var scroller = document.querySelector("#note-editor-overlay .note-editor-modal-body");
    function markActive() {
      if (!heads.length || !scroller) return;
      var top = scroller.getBoundingClientRect().top + 80;
      var cur = 0;
      heads.forEach(function (h, j) { if (h.el.getBoundingClientRect().top <= top) cur = j; });
      rail.querySelectorAll("[data-toc]").forEach(function (b) { b.classList.toggle("is-active", Number(b.getAttribute("data-toc")) === cur); });
    }
    function onClick(e) {
      var t = e.target.closest("[data-toc]");
      if (t) {
        var h = heads[Number(t.getAttribute("data-toc"))];
        if (h && h.el.scrollIntoView) h.el.scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      var b = e.target.closest("[data-p]");
      if (!b) return;
      if (b.getAttribute("data-p") === "note") openNoteId(b.getAttribute("data-note"));
      else {
        var orgId = Number(b.getAttribute("data-org"));
        var n = K().nodeOf(K().kbOf(orgId), Number(b.getAttribute("data-id")));
        if (n) openDoc(orgId, n);
      }
    }
    rail.addEventListener("click", onClick);
    foot.addEventListener("click", onClick);
    var timer = null;
    var mo = new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(collect, 400);
    });
    mo.observe(main, { childList: true, subtree: true, characterData: true });
    if (scroller) scroller.addEventListener("scroll", markActive, { passive: true });
    // Узкая страница (открыто обсуждение) — оглавления справа нет, «Ссылаются сюда» — внизу.
    var ro = window.ResizeObserver ? new ResizeObserver(function () {
      var on = layout.getBoundingClientRect().width >= 860;
      layout.classList.toggle("kb-rail-on", on);
    }) : null;
    if (ro) ro.observe(layout);
    else layout.classList.add("kb-rail-on");
    // документ закрыли или открыли другой — наблюдатели больше не нужны
    page._kbCleanup = function () {
      mo.disconnect();
      if (ro) ro.disconnect();
      if (scroller) scroller.removeEventListener("scroll", markActive);
      clearTimeout(timer);
    };
    setTimeout(collect, 300);
    app().apiFetch("/kb/backlinks/" + encodeURIComponent(note.id), { method: "GET" }).then(function (r) {
      back.items = (r && r.items) || [];
      paintFoot();
      paintRail();
    }).catch(function () {});
  }

  function onEditorClosed() {
    var ctx = desk.noteCtx;
    if (ctx && ctx.page && ctx.page._kbCleanup) ctx.page._kbCleanup();
    desk.noteCtx = null;
    desk.noteId = "";
    removeDocPath();
    if (desk.on) renderTree();
  }

  // ---------- подключение ----------

  function init() {
    if (!window.LeoOrgKb || !window.LeoApp || !panel()) {
      setTimeout(init, 200);
      return;
    }
    var p = panel();
    new MutationObserver(sync).observe(p, { attributes: true, attributeFilter: ["hidden", "class"] });
    var mq = window.matchMedia && window.matchMedia("(min-width: 960px)");
    if (mq && mq.addEventListener) mq.addEventListener("change", sync);
    var ov = document.getElementById("note-editor-overlay");
    if (ov) {
      var wasOpen = !ov.classList.contains("hidden");
      new MutationObserver(function () {
        var open = !ov.classList.contains("hidden");
        if (wasOpen && !open) onEditorClosed();
        wasOpen = open;
      }).observe(ov, { attributes: true, attributeFilter: ["class"] });
    }
    document.addEventListener("keydown", function (e) {
      if (!desk.on) return;
      var t = e.target;
      var editable = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && String(e.key).toLowerCase() === "k") {
        e.preventDefault();
        if (desk.hidden) setHidden(false);
        var q = document.getElementById("kb-tree-q");
        if (q) {
          q.focus();
          q.select();
        }
        return;
      }
      if (e.key === "Escape" && !editable && desk.noteId && app().currentNoteId && app().currentNoteId() &&
          !document.querySelector(".modal-overlay.kb-sheet-overlay, #kb-menu, #note-discussion-overlay:not(.hidden)")) {
        // Esc в документе — назад к разделу, где он лежит
        var hit = K().nodeForNote(desk.noteId);
        e.preventDefault();
        if (hit && hit.node.parent_id != null) showSection(hit.org.id, hit.node.parent_id);
        else goHome();
      }
    });
    sync();
  }

  window.LeoOrgKbDesk = {
    isOn: function () {
      return desk.on && isOn();
    },
    render: function () {
      if (!desk.on && isOn()) {
        sync();
        return;
      }
      if (desk.on && !isOn()) {
        sync();
        return;
      }
      render();
    },
    showSection: showSection,
    decorateNote: decorateNote,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
