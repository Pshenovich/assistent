(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function inlineMarkdown(s) {
    return String(s || "")
      .split(/(\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~)/g)
      .map(function (part) {
        if (/^\*\*[^*]+\*\*$/.test(part)) return "<strong>" + escapeHtml(part.slice(2, -2)) + "</strong>";
        if (/^\*[^*]+\*$/.test(part)) return "<em>" + escapeHtml(part.slice(1, -1)) + "</em>";
        if (/^~~[^~]+~~$/.test(part)) return "<s>" + escapeHtml(part.slice(2, -2)) + "</s>";
        return escapeHtml(part);
      })
      .join("");
  }

  var MEETING_SECTION_HEADERS = [
    "📌 Краткое описание",
    "📝 Краткое содержание",
    "👤 Информация о кандидате",
    "⭐ Самые перспективные идеи",
    "🔧 Практические рекомендации",
    "⚠️ Риски и блокеры",
    "❓ Вопросы без ответа",
    "✅ Принятые решения",
    "✅ Упомянутые действия",
    "🎯 Что обсуждали",
    "🎯 Цели и задачи",
    "❓ Открытые вопросы",
    "🔜 Следующие шаги",
    "📋 Следующие действия",
    "⚠️ Слабые стороны",
    "💪 Сильные стороны",
    "🛠 Навыки и опыт",
    "📝 Итоговая оценка",
    "🧠 Основные идеи",
    "📚 Ключевые тезисы",
    "💡 Предложенные идеи",
    "📌 Цель обсуждения",
    "📌 Важные факты",
    "💡 Основные мысли",
    "📌 О чем запись",
    "📌 Кратко",
    "📋 Задачи",
    "📋 Чек-лист",
    "⚠️ Риски",
    "🎯 Главная идея",
  ].sort(function (a, b) {
    return b.length - a.length;
  });

  function isTaskSectionTitle(title) {
    var t = String(title || "").trim();
    return (
      /📋\s*(Задачи|Чек-лист|Следующие действия)/.test(t) ||
      /✅\s*Выполненные задачи/.test(t)
    );
  }

  function isCompletedTaskSectionTitle(title) {
    return /✅\s*Выполненные задачи/.test(String(title || "").trim());
  }

  function splitInlineBulletLine(line) {
    var t = String(line || "").trim();
    if (!/^-\s/.test(t)) return line;
    if (/\s+-\s+\[[ xX]\]\s/.test(t)) {
      return t.replace(/\s+-\s+(\[[ xX]\]\s+)/g, "\n- $1");
    }
    if (!/\s+-\s+/.test(t)) return line;
    return t
      .replace(/\.\s+-\s+/g, ".\n- ")
      .replace(/\s+-\s+(?=[А-ЯA-ZЁ])/g, "\n- ");
  }

  function splitHeadingContent(hashes, content) {
    var body = String(content || "").trim();
    if (!body) return hashes + " " + body;

    var i;
    for (i = 0; i < MEETING_SECTION_HEADERS.length; i++) {
      var title = MEETING_SECTION_HEADERS[i];
      if (body === title) return hashes + " " + title;
      if (body.indexOf(title) === 0) {
        var rest = body.slice(title.length).trim();
        if (!rest) return hashes + " " + title;
        return hashes + " " + title + "\n" + splitInlineBulletLine(rest);
      }
    }

    var emojiBody = body.match(
      /^((?:[\u2600-\u27BF]|[\uD83C-\uDBFF][\uDC00-\uDFFF])+\s*(?:[^\s#-]+(?:\s+[^\s#-]+){0,5}?))\s*((?:[А-ЯA-ZЁ]|[-•]\s).*)$/
    );
    if (emojiBody && emojiBody[1].trim().length <= 60 && emojiBody[2].trim()) {
      return (
        hashes +
        " " +
        emojiBody[1].trim() +
        "\n" +
        splitInlineBulletLine(emojiBody[2].trim())
      );
    }

    var plainBody = body.match(/^((?:[^\s#-]+(?:\s+[^\s#-]+){0,4}?))\s+([А-ЯA-ZЁ].*)$/);
    if (
      plainBody &&
      plainBody[1].trim().length <= 50 &&
      plainBody[1].trim().length < body.length - 8
    ) {
      return (
        hashes +
        " " +
        plainBody[1].trim() +
        "\n" +
        splitInlineBulletLine(plainBody[2].trim())
      );
    }

    return hashes + " " + body;
  }

  function normalizeCollapsedMarkdown(md) {
    var s = String(md || "").trim();
    if (!s) return s;
    s = s.replace(/\s+(#{1,6}\s+)/g, "\n\n$1");
    s = s.replace(/(#{1,6}\s+[^\n#]+?)\s+(-\s+\[[ xX]\]\s+)/g, "$1\n$2");
    s = s.replace(/(#{1,6}\s+[^\n#]+?)\s+(-\s+(?!\[[ xX]\]))/g, "$1\n$2");
    s = s
      .split("\n")
      .map(function (line) {
        var trimmed = line.trim();
        if (!/^#{1,6}\s+/.test(trimmed)) return splitInlineBulletLine(line);
        var hm = trimmed.match(/^(#{1,6})\s+(.*)$/);
        if (!hm) return line;
        return splitHeadingContent(hm[1], hm[2]);
      })
      .join("\n");
    return s;
  }

  function parseMarkdownTableRow(line) {
    var trimmed = String(line || "").trim();
    if (!/^\|.+\|$/.test(trimmed)) return null;
    if (/^\|[\s\-:|]+\|$/.test(trimmed)) return "sep";
    return trimmed
      .slice(1, -1)
      .split("|")
      .map(function (cell) {
        return cell.trim();
      });
  }

  function markdownTableToHtml(rows) {
    if (!rows || !rows.length) return "";
    var html = ['<table class="note-editor-table"><tbody>'];
    rows.forEach(function (cells, rowIdx) {
      html.push("<tr>");
      cells.forEach(function (cell) {
        var tag = rowIdx === 0 ? "th" : "td";
        html.push("<" + tag + ">" + inlineMarkdown(cell) + "</" + tag + ">");
      });
      html.push("</tr>");
    });
    html.push("</tbody></table>");
    return html.join("");
  }

  function liftMarkdownTables(md) {
    var lines = String(md || "").split("\n");
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var row = parseMarkdownTableRow(lines[i].trim());
      if (!row) {
        out.push(lines[i]);
        continue;
      }
      var rows = [];
      while (i < lines.length) {
        var candidate = parseMarkdownTableRow(lines[i].trim());
        if (!candidate) break;
        if (candidate !== "sep") rows.push(candidate);
        i += 1;
      }
      i -= 1;
      if (rows.length) out.push(markdownTableToHtml(rows));
    }
    return out.join("\n");
  }

  function markdownToHtml(md) {
    var lines = String(liftMarkdownTables(normalizeCollapsedMarkdown(md)) || "").split("\n");
    var html = [];
    var inList = false;
    var inTaskList = false;
    var inTaskSection = false;
    var taskSectionCompleted = false;
    function closeList() {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
    }
    function closeTaskList() {
      if (inTaskList) {
        html.push("</ul>");
        inTaskList = false;
      }
    }
    function closeAll() {
      closeList();
      closeTaskList();
    }
    function pushTaskItem(checked, text) {
      closeList();
      if (!inTaskList) {
        html.push('<ul class="note-task-list">');
        inTaskList = true;
      }
      if (checked) html.push("<li checked><p>" + inlineMarkdown(text) + "</p></li>");
      else html.push("<li><p>" + inlineMarkdown(text) + "</p></li>");
    }
    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed) {
        closeAll();
        return;
      }
      if (/^<table[\s>]/i.test(trimmed)) {
        closeAll();
        html.push(trimmed);
        inTaskSection = false;
        taskSectionCompleted = false;
        return;
      }
      var hm = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (hm) {
        closeAll();
        var tag = "h" + String(Math.min(hm[1].length, 6));
        html.push("<" + tag + ">" + inlineMarkdown(hm[2]) + "</" + tag + ">");
        inTaskSection = isTaskSectionTitle(hm[2]);
        taskSectionCompleted = isCompletedTaskSectionTitle(hm[2]);
        return;
      }
      var tm = trimmed.match(/^-\s+\[([ xX])\]\s+(.*)$/);
      if (tm) {
        pushTaskItem(String(tm[1]).toLowerCase() === "x", tm[2]);
        return;
      }
      var bm = trimmed.match(/^[-*•]\s+(.*)$/);
      if (bm) {
        if (inTaskSection) {
          pushTaskItem(taskSectionCompleted, bm[1]);
          return;
        }
        closeTaskList();
        if (!inList) {
          html.push("<ul>");
          inList = true;
        }
        html.push("<li><p>" + inlineMarkdown(bm[1]) + "</p></li>");
        return;
      }
      closeAll();
      inTaskSection = false;
      taskSectionCompleted = false;
      html.push("<p>" + inlineMarkdown(trimmed) + "</p>");
    });
    closeAll();
    return html.join("");
  }

  function ulHasCheckedItems(ul) {
    var lis = ul.querySelectorAll(":scope > li");
    for (var i = 0; i < lis.length; i++) {
      if (lis[i].hasAttribute("checked")) return true;
    }
    return false;
  }

  function isTaskListUl(ul) {
    if (!ul) return false;
    if (ul.getAttribute("data-type") === "taskList") return true;
    if (ul.classList && ul.classList.contains("note-task-list")) return true;
    if (ul.querySelector(":scope > li[data-type='taskItem'], :scope > li[data-checked]")) {
      return true;
    }
    return ulHasCheckedItems(ul);
  }

  function liIsChecked(li) {
    if (!li) return false;
    if (li.hasAttribute("checked")) return true;
    var checked = String(li.getAttribute("data-checked") || "").toLowerCase();
    return checked === "true" || checked === "1";
  }

  function normalizeTaskItemInner(html) {
    var s = String(html || "").trim();
    var m = s.match(/^<p\b[^>]*>([\s\S]*)<\/p>$/i);
    if (m) return m[1].trim();
    return s;
  }

  function extractTaskTextFromLi(li) {
    li.querySelectorAll("label, input[type='checkbox']").forEach(function (el) {
      el.remove();
    });
    var div = li.querySelector(":scope > div");
    if (div) {
      var p = div.querySelector("p");
      return (p ? p.textContent : div.textContent).trim();
    }
    var ps = li.querySelectorAll(":scope > p");
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

  function repairBrokenTaskLists(root) {
    root.querySelectorAll("ul.note-task-list, ul").forEach(function (ul) {
      if (!isTaskListUl(ul)) return;
      ul.classList.add("note-task-list");
      ul.querySelectorAll(":scope > li").forEach(function (li) {
        if (extractTaskTextFromLi(li)) return;
        var next = ul.nextElementSibling;
        if (next && next.tagName === "UL" && !isTaskListUl(next)) {
          var merged = [];
          next.querySelectorAll(":scope > li").forEach(function (bli) {
            var t = extractTaskTextFromLi(bli);
            if (t) merged.push(t);
          });
          if (merged.length) {
            li.innerHTML = "<p>" + merged.join(" ") + "</p>";
            next.remove();
          }
        }
      });
    });
  }

  /** HTML хранения → вид с чеклистами (интерактивными или только отображение). */
  function enrichForDisplay(html, opts) {
    opts = opts || {};
    var interactive = opts.interactive !== false;
    var raw = String(html || "").trim();
    if (!raw || !/<[a-z]/i.test(raw)) return raw;
    var doc = new DOMParser().parseFromString("<div>" + raw + "</div>", "text/html");
    var root = doc.body.firstChild;
    if (!root) return raw;

    repairBrokenTaskLists(root);

    var taskCounter = 0;
    root.querySelectorAll("ul").forEach(function (ul) {
      if (!isTaskListUl(ul)) return;
      ul.className = "note-task-list";
      var lis = ul.querySelectorAll(":scope > li");
      lis.forEach(function (li) {
        var checked = liIsChecked(li);
        li.className = "note-task" + (checked ? " note-task--checked" : "");
        var inner = normalizeTaskItemInner(li.innerHTML);
        if (interactive) {
          li.innerHTML =
            '<input type="checkbox"' +
            (checked ? " checked" : "") +
            ' data-task-index="' +
            String(taskCounter++) +
            '" aria-label="Задача"><span class="note-task-text">' +
            inner +
            "</span>";
        } else {
          li.innerHTML =
            '<input type="checkbox" disabled' +
            (checked ? " checked" : "") +
            ' tabindex="-1" aria-hidden="true"><span class="note-task-text">' +
            inner +
            "</span>";
        }
        li.removeAttribute("checked");
      });
    });

    return root.innerHTML;
  }

  function toggleTaskInStorageHtml(body, taskIndex) {
    var idx = parseInt(String(taskIndex), 10);
    if (idx < 0 || isNaN(idx)) return String(body || "");
    var raw = String(body || "");
    if (!/<[a-z]/i.test(raw)) {
      var lines = raw.split("\n");
      var taskLine = 0;
      for (var i = 0; i < lines.length; i++) {
        if (/^-\s+\[[ xX]\]\s/.test(String(lines[i] || "").trim())) {
          if (taskLine === idx) {
            var m = lines[i].match(/^(\s*-\s+)\[([ xX])\]\s+(.*)$/);
            if (!m) return raw;
            var checked = String(m[2]).toLowerCase() === "x";
            lines[i] = m[1] + "[" + (checked ? " " : "x") + "] " + m[3];
            return lines.join("\n");
          }
          taskLine++;
        }
      }
      return raw;
    }

    var doc = new DOMParser().parseFromString("<div>" + raw + "</div>", "text/html");
    var root = doc.body.firstChild;
    if (!root) return raw;
    var counter = 0;
    var target = null;
    root.querySelectorAll("ul").forEach(function (ul) {
      if (!isTaskListUl(ul)) return;
      ul.querySelectorAll(":scope > li").forEach(function (li) {
        if (counter === idx) target = li;
        counter++;
      });
    });
    if (!target) return raw;
    if (target.hasAttribute("checked")) target.removeAttribute("checked");
    else target.setAttribute("checked", "");
    return root.innerHTML;
  }

  global.NoteHtml = {
    markdownToHtml: markdownToHtml,
    normalizeCollapsedMarkdown: normalizeCollapsedMarkdown,
    enrichForDisplay: enrichForDisplay,
    toggleTaskInStorageHtml: toggleTaskInStorageHtml,
  };
})(window);
