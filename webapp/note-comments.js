(function (global) {
  var CTX = 40;
  var QUOTE_MAX = 500;

  function collapseWs(s) {
    return String(s || "").replace(/\s+/g, " ");
  }

  function textNodes(root) {
    var out = [];
    if (!root) return out;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node || !node.nodeValue) return NodeFilter.FILTER_REJECT;
        var p = node.parentNode;
        if (p && p.closest && p.closest("script, style")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var n;
    while ((n = walker.nextNode())) out.push(n);
    return out;
  }

  function haystack(root) {
    var nodes = textNodes(root);
    var text = "";
    var spans = [];
    nodes.forEach(function (node) {
      var v = node.nodeValue || "";
      spans.push({ node: node, start: text.length, end: text.length + v.length });
      text += v;
    });
    return { text: text, spans: spans };
  }

  function indexToPoint(spans, index) {
    if (index < 0) return null;
    for (var i = 0; i < spans.length; i++) {
      var sp = spans[i];
      if (index >= sp.start && index <= sp.end) {
        return { node: sp.node, offset: index - sp.start };
      }
    }
    var last = spans[spans.length - 1];
    if (last && index >= last.end) return { node: last.node, offset: last.node.nodeValue.length };
    return null;
  }

  function caretToHayIndex(root, container, offset) {
    var nodes = textNodes(root);
    if (!nodes.length) return 0;
    var probe;
    try {
      probe = document.createRange();
      probe.setStart(container, offset);
      probe.collapse(true);
    } catch (_) {
      return -1;
    }
    var pos = 0;
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var len = node.nodeValue.length;
      var atStart;
      var atEnd;
      try {
        atStart = probe.comparePoint(node, 0);
      } catch (_) {
        pos += len;
        continue;
      }
      if (atStart > 0) return pos;
      try {
        atEnd = probe.comparePoint(node, len);
      } catch (_) {
        atEnd = 1;
      }
      if (atEnd < 0) {
        pos += len;
        continue;
      }
      for (var o = 0; o <= len; o++) {
        try {
          if (probe.comparePoint(node, o) >= 0) return pos + o;
        } catch (_) {}
      }
      return pos + len;
    }
    return pos;
  }

  function normMap(s) {
    var text = "";
    var map = [];
    var space = false;
    for (var i = 0; i < s.length; i++) {
      if (/\s/.test(s.charAt(i))) {
        if (!space) {
          map.push(i);
          text += " ";
          space = true;
        }
      } else {
        map.push(i);
        text += s.charAt(i);
        space = false;
      }
    }
    return { text: text, map: map };
  }

  function searchWithCtx(hay, q, pre, suf) {
    if (!q) return -1;
    var from = 0;
    var idx;
    var found = -1;
    while ((idx = hay.indexOf(q, from)) >= 0) {
      var before = hay.slice(Math.max(0, idx - pre.length), idx);
      var after = hay.slice(idx + q.length, idx + q.length + suf.length);
      var preOk = !pre || before === pre || pre.endsWith(before) || before.endsWith(pre);
      var sufOk = !suf || after === suf || suf.startsWith(after) || after.startsWith(suf);
      if (preOk && sufOk) return idx;
      if (found < 0) found = idx;
      from = idx + 1;
    }
    return found;
  }

  function findQuoteSpan(text, quote, prefix, suffix) {
    var q = String(quote || "");
    if (!q) return null;
    var pre = String(prefix || "");
    var suf = String(suffix || "");
    var idx = searchWithCtx(text, q, pre, suf);
    if (idx >= 0) return { start: idx, end: idx + q.length };
    var nh = normMap(text);
    var nq = collapseWs(q).trim();
    if (!nq) return null;
    var nidx = searchWithCtx(nh.text, nq, collapseWs(pre), collapseWs(suf));
    if (nidx < 0) return null;
    var start = nh.map[nidx];
    var last = nh.map[nidx + nq.length - 1];
    if (start == null || last == null) return null;
    return { start: start, end: last + 1 };
  }

  function rangeForAnchor(root, quote, prefix, suffix) {
    if (!root || !quote) return null;
    var hay = haystack(root);
    var span = findQuoteSpan(hay.text, quote, prefix, suffix);
    if (!span) return null;
    var start = indexToPoint(hay.spans, span.start);
    var end = indexToPoint(hay.spans, span.end);
    if (!start || !end) return null;
    try {
      var range = document.createRange();
      range.setStart(start.node, start.offset);
      range.setEnd(end.node, end.offset);
      return range;
    } catch (_) {
      return null;
    }
  }

  function selectionAnchor(root) {
    var sel = window.getSelection && window.getSelection();
    if (!sel || sel.rangeCount < 1 || sel.isCollapsed) return null;
    var range = sel.getRangeAt(0);
    if (!root || !root.contains(range.commonAncestorContainer)) return null;
    var hay = haystack(root);
    var startIdx = caretToHayIndex(root, range.startContainer, range.startOffset);
    var endIdx = caretToHayIndex(root, range.endContainer, range.endOffset);
    var raw = "";
    if (startIdx >= 0 && endIdx > startIdx) raw = hay.text.slice(startIdx, endIdx);
    if (!raw) raw = range.toString();
    var quote = collapseWs(raw).trim();
    if (!quote) return null;
    if (quote.length > QUOTE_MAX) {
      quote = quote.slice(0, QUOTE_MAX);
      raw = raw.slice(0, QUOTE_MAX);
      if (endIdx > startIdx) endIdx = startIdx + raw.length;
    }
    var idx = startIdx >= 0 ? startIdx : hay.text.indexOf(raw);
    if (idx < 0) idx = hay.text.indexOf(quote);
    var prefix = "";
    var suffix = "";
    if (idx >= 0) {
      var qLen = raw.length || quote.length;
      prefix = hay.text.slice(Math.max(0, idx - CTX), idx);
      suffix = hay.text.slice(idx + qLen, idx + qLen + CTX);
    }
    var rects = range.getClientRects();
    var rect = null;
    for (var i = rects.length - 1; i >= 0; i--) {
      if (rects[i].width >= 1 && rects[i].height >= 1) {
        rect = rects[i];
        break;
      }
    }
    if (!rect) rect = range.getBoundingClientRect();
    return { quote: quote, prefix: prefix, suffix: suffix, rect: rect };
  }

  function wrapRange(range, commentId) {
    if (!range || range.collapsed) return [];
    var mark = document.createElement("mark");
    mark.className = "note-comment-hl";
    mark.setAttribute("data-comment-id", String(commentId));
    try {
      range.surroundContents(mark);
      return [mark];
    } catch (_) {}
    try {
      var frag = range.extractContents();
      mark.appendChild(frag);
      range.insertNode(mark);
      return [mark];
    } catch (_) {
      return [];
    }
  }

  function clearHighlights(root) {
    if (!root) return;
    var marks = Array.prototype.slice.call(root.querySelectorAll("mark.note-comment-hl"));
    marks.forEach(function (mark) {
      var parent = mark.parentNode;
      if (!parent) return;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      parent.normalize();
    });
  }

  function applyHighlights(root, comments) {
    clearHighlights(root);
    var map = {};
    if (!root) return map;
    var planned = [];
    (comments || []).forEach(function (c) {
      if (!c || !c.id || !c.quote || isPaieComment(c) || isGptTurn(c)) return;
      var range = rangeForAnchor(root, c.quote, c.prefix, c.suffix);
      if (range) planned.push({ c: c, range: range });
    });
    planned.sort(function (a, b) {
      try {
        return a.range.compareBoundaryPoints(Range.START_TO_START, b.range);
      } catch (_) {
        return 0;
      }
    });
    for (var i = planned.length - 1; i >= 0; i--) {
      var marks = wrapRange(planned[i].range, planned[i].c.id);
      if (marks[0]) map[String(planned[i].c.id)] = marks[0];
    }
    return map;
  }

  var BUBBLE_SIZE = 32;
  var BUBBLE_GAP = 6;

  function isCommentBubbleEvent(e) {
    var t = e && e.target;
    if (!t) return false;
    if (t.id === "note-comment-bubble") return true;
    return !!(t.closest && t.closest("#note-comment-bubble"));
  }

  function hideBubble() {
    var el = document.getElementById("note-comment-bubble");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function placeBubble(btn, rect) {
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var left = rect.right + BUBBLE_GAP;
    var top = rect.top + (rect.height - BUBBLE_SIZE) / 2;
    if (left + BUBBLE_SIZE > vw - 8) left = rect.left - BUBBLE_SIZE - BUBBLE_GAP;
    if (left < 8) left = 8;
    if (top < 8) top = 8;
    if (top + BUBBLE_SIZE > vh - 8) top = vh - BUBBLE_SIZE - 8;
    btn.style.top = Math.round(top) + "px";
    btn.style.left = Math.round(left) + "px";
  }

  function showBubble(rect, onClick) {
    if (!rect) {
      hideBubble();
      return;
    }
    var btn = document.getElementById("note-comment-bubble");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.id = "note-comment-bubble";
      btn.className = "note-comment-bubble";
      btn.setAttribute("aria-label", "Комментировать");
      btn.setAttribute("title", "Комментировать");
      btn.innerHTML =
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
        '<path d="M21.99 4c0-1.1-.89-2-1.99-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4-.01-18z"/>' +
        "</svg>";
      function stopSel(e) {
        e.preventDefault();
        e.stopPropagation();
      }
      function activate(e) {
        e.preventDefault();
        e.stopPropagation();
        if (btn._fired) return;
        btn._fired = true;
        var fn = btn._onComment;
        hideBubble();
        if (typeof fn === "function") fn();
      }
      btn.addEventListener("pointerdown", stopSel);
      btn.addEventListener("mousedown", stopSel);
      btn.addEventListener("touchstart", stopSel, { passive: false });
      btn.addEventListener("pointerup", activate);
      btn.addEventListener("click", activate);
      document.body.appendChild(btn);
    }
    btn._onComment = onClick;
    btn._fired = false;
    placeBubble(btn, rect);
  }

  function desiredTopFromRect(rect, listEl) {
    if (!rect || !listEl || !listEl.getBoundingClientRect) return null;
    return rect.top - listEl.getBoundingClientRect().top + (listEl.scrollTop || 0);
  }

  function isDesktopCommentsLayout() {
    return !!(window.matchMedia && window.matchMedia("(min-width: 960px)").matches);
  }

  function alignCardTops(listEl, tops) {
    if (!listEl) return;
    var cards = Array.prototype.slice.call(listEl.querySelectorAll("[data-comment-id]"));
    var desktop = isDesktopCommentsLayout();
    if (!desktop) {
      listEl.classList.remove("is-anchored");
      listEl.style.minHeight = "";
      cards.forEach(function (card) {
        card.style.position = "";
        card.style.left = "";
        card.style.right = "";
        card.style.top = "";
        card.style.marginTop = "";
      });
      return;
    }
    listEl.classList.add("is-anchored");
    var cursor = 0;
    var maxBottom = 0;
    cards.forEach(function (card) {
      var id = card.getAttribute("data-comment-id");
      var desired = tops[id];
      if (desired == null || isNaN(desired) || desired < 0) desired = cursor;
      if (desired < cursor) desired = cursor;
      card.style.position = "absolute";
      card.style.left = "0";
      card.style.right = "0";
      card.style.marginTop = "0";
      card.style.top = Math.round(desired) + "px";
      cursor = desired + card.offsetHeight + 8;
      if (cursor > maxBottom) maxBottom = cursor;
    });
    listEl.style.minHeight = Math.max(0, Math.round(maxBottom)) + "px";
  }

  function alignCards(listEl, highlightMap) {
    if (!listEl) return;
    var tops = {};
    var cards = Array.prototype.slice.call(listEl.querySelectorAll("[data-comment-id]"));
    cards.forEach(function (card) {
      var id = card.getAttribute("data-comment-id");
      var mark = highlightMap && highlightMap[id];
      if (mark && mark.getBoundingClientRect) {
        var top = desiredTopFromRect(mark.getBoundingClientRect(), listEl);
        if (top != null) tops[id] = top;
      }
    });
    alignCardTops(listEl, tops);
  }

  function alignCardsByAnchors(listEl, root, comments) {
    if (!listEl) return;
    var tops = {};
    (comments || []).forEach(function (c) {
      if (!c || !c.id || !c.quote || isPaieComment(c) || isGptTurn(c)) return;
      var range = rangeForAnchor(root, c.quote, c.prefix, c.suffix);
      if (!range) return;
      var rect = range.getBoundingClientRect();
      if (!rect || rect.height < 1) return;
      var top = desiredTopFromRect(rect, listEl);
      if (top != null) tops[String(c.id)] = top;
    });
    alignCardTops(listEl, tops);
  }

  function paintOverlay(root, overlay, comments, activeId) {
    if (!overlay) return;
    overlay.innerHTML = "";
    if (!root) return;
    var overlayRect = overlay.getBoundingClientRect ? overlay.getBoundingClientRect() : { left: 0, top: 0 };
    (comments || []).forEach(function (c) {
      if (!c || !c.quote || isPaieComment(c) || isGptTurn(c)) return;
      var range = rangeForAnchor(root, c.quote, c.prefix, c.suffix);
      if (!range) return;
      var rects = range.getClientRects();
      for (var i = 0; i < rects.length; i++) {
        var r = rects[i];
        if (r.width < 1 || r.height < 1) continue;
        var span = document.createElement("span");
        span.className =
          "note-comment-overlay-hl" +
          (String(c.id) === String(activeId || "") ? " is-active" : "");
        span.setAttribute("data-comment-id", String(c.id));
        span.style.left = r.left - overlayRect.left + overlay.scrollLeft + "px";
        span.style.top = r.top - overlayRect.top + overlay.scrollTop + "px";
        span.style.width = r.width + "px";
        span.style.height = r.height + "px";
        overlay.appendChild(span);
      }
    });
  }

  function isPaieComment(c) {
    if (!c) return false;
    if (c.is_paie) return true;
    var uname = String(c.author_username || "").trim().toLowerCase();
    var name = String(c.author_name || "").trim().toUpperCase();
    return uname === "paie" || name === "CHAIR";
  }

  function isGptComment(c) {
    if (!c) return false;
    if (c.is_gpt) return true;
    var uname = String(c.author_username || "").trim().toLowerCase();
    var name = String(c.author_name || "").trim().toUpperCase();
    return uname === "gpt" || name === "GPT";
  }

  function isGptTurn(c) {
    if (!c) return false;
    if (isGptComment(c)) return true;
    if (String(c.prefix || "").trim() !== "__gpt__") return false;
    return !String(c.quote || "").trim();
  }

  function parentIdOf(c) {
    var raw = c && c.parent_id;
    if (raw == null || raw === "" || raw === 0 || raw === "0") return 0;
    var n = Number(raw);
    return n > 0 ? n : 0;
  }

  function paieThread(comments) {
    var rows = comments || [];
    var ids = {};
    rows.forEach(function (c) {
      if (!c || !c.id) return;
      if (isPaieComment(c)) ids[String(c.id)] = true;
    });
    var changed = true;
    while (changed) {
      changed = false;
      rows.forEach(function (c) {
        if (!c || !c.id) return;
        var id = String(c.id);
        if (ids[id] || isGptTurn(c)) return;
        var pid = parentIdOf(c);
        if (pid && ids[String(pid)]) {
          ids[id] = true;
          changed = true;
        }
      });
    }
    return rows.filter(function (c) {
      return c && c.id && ids[String(c.id)] && !isGptTurn(c);
    });
  }

  function paieThreadRootId(comments) {
    var thread = paieThread(comments);
    for (var i = 0; i < thread.length; i += 1) {
      if (isPaieComment(thread[i]) && !parentIdOf(thread[i])) return thread[i].id;
    }
    return thread.length ? thread[0].id : null;
  }

  function gptThread(comments) {
    return (comments || []).filter(function (c) {
      return c && c.id && isGptTurn(c);
    });
  }

  function selectionComments(comments) {
    var skip = {};
    paieThread(comments).forEach(function (c) {
      if (c && c.id) skip[String(c.id)] = true;
    });
    gptThread(comments).forEach(function (c) {
      if (c && c.id) skip[String(c.id)] = true;
    });
    return (comments || []).filter(function (c) {
      return c && c.id && !skip[String(c.id)] && String(c.quote || "").trim();
    });
  }

  function generalComments(comments) {
    var skip = {};
    paieThread(comments).forEach(function (c) {
      if (c && c.id) skip[String(c.id)] = true;
    });
    gptThread(comments).forEach(function (c) {
      if (c && c.id) skip[String(c.id)] = true;
    });
    return (comments || []).filter(function (c) {
      return c && c.id && !skip[String(c.id)] && !String(c.quote || "").trim();
    });
  }

  function discussionCount(comments) {
    return (comments || []).filter(function (c) {
      return c && c.id;
    }).length;
  }

  function hasDiscussion(comments) {
    return discussionCount(comments) > 0;
  }

  function isMobileCommentsLayout() {
    return !isDesktopCommentsLayout();
  }

  function bindOverlayClicks(overlay, onPick) {
    if (!overlay || typeof onPick !== "function") return;
    overlay.querySelectorAll(".note-comment-overlay-hl").forEach(function (span) {
      span.style.pointerEvents = "auto";
      span.style.cursor = "pointer";
      span.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        onPick(span.getAttribute("data-comment-id"));
      });
    });
  }

  function commentSheetRoot() {
    var el = document.getElementById("note-comment-sheet");
    if (el) return el;
    el = document.createElement("div");
    el.id = "note-comment-sheet";
    el.className = "note-comment-sheet hidden";
    el.setAttribute("aria-hidden", "true");
    el.innerHTML =
      '<div class="note-comment-sheet-backdrop" data-sheet-dismiss="1"></div>' +
      '<div class="note-comment-sheet-panel" role="dialog" aria-modal="true" aria-label="Комментарий">' +
      '<div class="note-comment-sheet-handle"></div>' +
      '<button type="button" class="note-comment-sheet-close" data-sheet-dismiss="1">Закрыть</button>' +
      '<div class="note-comment-sheet-body"></div>' +
      "</div>";
    document.body.appendChild(el);
    el.addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.getAttribute && t.getAttribute("data-sheet-dismiss")) hideCommentSheet();
    });
    return el;
  }

  function hideCommentSheet() {
    var el = document.getElementById("note-comment-sheet");
    if (!el) return;
    el.classList.add("hidden");
    el.setAttribute("aria-hidden", "true");
    var body = el.querySelector(".note-comment-sheet-body");
    if (body) body.innerHTML = "";
  }

  function showCommentSheet(comment) {
    if (!comment) return;
    var el = commentSheetRoot();
    var body = el.querySelector(".note-comment-sheet-body");
    if (!body) return;
    body.innerHTML = "";
    var card = document.createElement("article");
    card.className = "note-comment note-comment-sheet-card";
    if (comment.quote) {
      var q = document.createElement("p");
      q.className = "share-comment-quote";
      q.textContent = "«" + String(comment.quote) + "»";
      card.appendChild(q);
    }
    var head = document.createElement("div");
    head.className = "note-comment-head";
    var who = document.createElement("strong");
    who.textContent = String(comment.author_name || "Пользователь");
    var uname = String(comment.author_username || "").trim();
    if (uname && who.textContent.toLowerCase().indexOf("@" + uname.toLowerCase()) < 0) {
      who.textContent += " · @" + uname;
    }
    var when = document.createElement("time");
    var whenText = String(comment.created_at || "");
    try {
      var d = new Date(comment.created_at);
      if (!isNaN(d.getTime())) {
        whenText = d.toLocaleString("ru-RU", {
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        });
      }
    } catch (_) {}
    when.textContent = whenText;
    head.appendChild(who);
    head.appendChild(when);
    var p = document.createElement("p");
    p.className = "note-comment-body";
    p.textContent = String(comment.body || "");
    card.appendChild(head);
    card.appendChild(p);
    body.appendChild(card);
    el.classList.remove("hidden");
    el.setAttribute("aria-hidden", "false");
  }

  function paieThreadGroups(comments) {
    var thread = paieThread(comments);
    var byId = {};
    var chairs = [];
    thread.forEach(function (c) {
      if (!c || !c.id) return;
      byId[String(c.id)] = c;
      if (isPaieComment(c)) chairs.push(c);
    });
    function chairAncestorId(c) {
      var pid = parentIdOf(c);
      var guard = 0;
      while (pid && guard < 24) {
        guard += 1;
        var parent = byId[String(pid)];
        if (!parent) break;
        if (isPaieComment(parent)) return parent.id;
        pid = parentIdOf(parent);
      }
      return chairs.length ? chairs[0].id : 0;
    }
    var groups = chairs.map(function (ch) {
      return { chair: ch, comments: [] };
    });
    var index = {};
    groups.forEach(function (g) {
      index[String(g.chair.id)] = g;
    });
    thread.forEach(function (c) {
      if (!c || isPaieComment(c)) return;
      var cid = chairAncestorId(c);
      if (cid && index[String(cid)]) index[String(cid)].comments.push(c);
    });
    return groups;
  }

  global.NoteComments = {
    selectionAnchor: selectionAnchor,
    rangeForAnchor: rangeForAnchor,
    applyHighlights: applyHighlights,
    clearHighlights: clearHighlights,
    paintOverlay: paintOverlay,
    showBubble: showBubble,
    hideBubble: hideBubble,
    isCommentBubbleEvent: isCommentBubbleEvent,
    alignCards: alignCards,
    alignCardsByAnchors: alignCardsByAnchors,
    collapseWs: collapseWs,
    isPaieComment: isPaieComment,
    isGptComment: isGptComment,
    isGptTurn: isGptTurn,
    parentIdOf: parentIdOf,
    paieThread: paieThread,
    paieThreadGroups: paieThreadGroups,
    paieThreadRootId: paieThreadRootId,
    gptThread: gptThread,
    generalComments: generalComments,
    selectionComments: selectionComments,
    discussionCount: discussionCount,
    hasDiscussion: hasDiscussion,
    isMobileCommentsLayout: isMobileCommentsLayout,
    bindOverlayClicks: bindOverlayClicks,
    showCommentSheet: showCommentSheet,
    hideCommentSheet: hideCommentSheet,
  };
})(window);
