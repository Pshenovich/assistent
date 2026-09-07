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
    var rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
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
      if (!c || !c.id || !c.quote) return;
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

  function hideBubble() {
    var el = document.getElementById("note-comment-bubble");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function showBubble(rect, onClick) {
    hideBubble();
    if (!rect) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "note-comment-bubble";
    btn.className = "note-comment-bubble";
    btn.textContent = "Комментировать";
    var top = Math.max(8, rect.bottom + 8);
    var left = Math.max(8, Math.min(window.innerWidth - 180, rect.left));
    btn.style.top = top + "px";
    btn.style.left = left + "px";
    btn.addEventListener("mousedown", function (e) {
      e.preventDefault();
      e.stopPropagation();
    });
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      hideBubble();
      if (typeof onClick === "function") onClick();
    });
    document.body.appendChild(btn);
  }

  function desiredTopFromRect(rect, listEl) {
    if (!rect || !listEl || !listEl.getBoundingClientRect) return null;
    return rect.top - listEl.getBoundingClientRect().top + listEl.scrollTop;
  }

  function alignCardTops(listEl, tops) {
    if (!listEl) return;
    listEl.classList.add("is-anchored");
    var cards = Array.prototype.slice.call(listEl.querySelectorAll("[data-comment-id]"));
    var cursor = 0;
    cards.forEach(function (card) {
      var id = card.getAttribute("data-comment-id");
      var desired = tops[id];
      if (desired == null || isNaN(desired)) desired = cursor;
      if (desired < cursor) desired = cursor;
      var margin = Math.max(0, Math.round(desired - cursor));
      card.style.marginTop = margin + "px";
      cursor = cursor + margin + card.offsetHeight + 8;
    });
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
      if (!c || !c.id || !c.quote) return;
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
    var host = overlay.offsetParent || overlay.parentNode;
    var hostRect = host && host.getBoundingClientRect ? host.getBoundingClientRect() : { left: 0, top: 0 };
    (comments || []).forEach(function (c) {
      if (!c || !c.quote) return;
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
        span.style.left = r.left - hostRect.left + overlay.scrollLeft + "px";
        span.style.top = r.top - hostRect.top + overlay.scrollTop + "px";
        span.style.width = r.width + "px";
        span.style.height = r.height + "px";
        overlay.appendChild(span);
      }
    });
  }

  global.NoteComments = {
    selectionAnchor: selectionAnchor,
    rangeForAnchor: rangeForAnchor,
    applyHighlights: applyHighlights,
    clearHighlights: clearHighlights,
    paintOverlay: paintOverlay,
    showBubble: showBubble,
    hideBubble: hideBubble,
    alignCards: alignCards,
    alignCardsByAnchors: alignCardsByAnchors,
    collapseWs: collapseWs,
  };
})(window);
