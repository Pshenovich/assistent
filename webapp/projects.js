/* Проекты: окно «Проекты» (список), страница проекта, «Поделиться», «+ Добавить».
   Сервер: /projects*, элементы проекта — те же, что у связок (kind/id), доступ проверяет сервер.
   Общие помощники берём из window.LeoApp (app.js). */
(function () {
  "use strict";

  function app() {
    return window.LeoApp || {};
  }
  function api(path, opts) {
    return app().apiFetch(path, opts || { method: "GET" });
  }
  function esc(s) {
    var f = app().escapeHtml;
    if (f) return f(String(s == null ? "" : s));
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toast(msg) {
    if (app().toast) app().toast(msg);
  }
  function errText(e) {
    return (e && e.message) || "Не получилось";
  }
  function isDesktop() {
    return window.matchMedia && window.matchMedia("(min-width: 960px)").matches;
  }

  var SVG = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    more: '<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
    share: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M17 8v6M14 11h6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    note: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    meeting: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    task: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>',
    chat: '<path d="M5 18l-1 3 4-1.5A8 8 0 1 0 5 18z"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7l8.5 6 8.5-6"/>',
    person: '<circle cx="12" cy="8" r="4"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    remind: '<path d="M18 8A6 6 0 1 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>',
    back: '<path d="M15 6l-6 6 6 6"/>',
    leo: '<circle cx="12" cy="12" r="4"/><ellipse cx="12" cy="12" rx="10" ry="4.5" transform="rotate(-25 12 12)"/>',
  };
  function icon(name, size) {
    var s = size || 18;
    return (
      '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (SVG[name] || "") + "</svg>"
    );
  }

  var STATUS = { active: "Идёт", paused: "На паузе", done: "Завершён" };
  var COLOR_NAMES = { "": "Без цвета", blue: "Синий", green: "Зелёный", orange: "Оранжевый", red: "Красный", purple: "Фиолетовый", teal: "Бирюзовый", gray: "Серый" };
  var COLOR_KEYS = ["blue", "green", "purple", "orange", "teal", "red", "gray"];
  function colorOf(p) {
    if (p && p.color) return p.color;
    var t = String((p && p.title) || "");
    var h = 0;
    for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0;
    return COLOR_KEYS[h % COLOR_KEYS.length];
  }
  function avatarHtml(p, cls) {
    var letter = String((p && p.title) || "?").trim().charAt(0).toUpperCase() || "?";
    return '<span class="pj-ava pj-c-' + esc(colorOf(p)) + (cls ? " " + cls : "") + '" aria-hidden="true">' + esc(letter) + "</span>";
  }
  function initials(label) {
    return String(label || "?").trim().charAt(0).toUpperCase() || "?";
  }

  // Группы элементов проекта: что в какой вкладке.
  var GROUPS = [
    { id: "meeting", label: "Встречи", icon: "meeting", test: function (it) { return it.kind === "event" || it.kind === "series" || it.kind === "topic" || (it.kind === "note" && it.is_transcription); } },
    { id: "task", label: "Задачи", icon: "task", test: function (it) { return it.kind === "task" || it.kind === "reminder"; } },
    { id: "note", label: "Документы", icon: "note", test: function (it) { return (it.kind === "note" && !it.is_transcription) || it.kind === "journal"; } },
    { id: "chat", label: "Чаты", icon: "chat", test: function (it) { return it.kind === "chat" || it.kind === "tgchat"; } },
    { id: "mail", label: "Почта", icon: "mail", test: function (it) { return it.kind === "mail" || it.kind === "mailaddr"; } },
    { id: "person", label: "Люди", icon: "person", test: function (it) { return it.kind === "person"; } },
  ];
  function groupOf(it) {
    for (var i = 0; i < GROUPS.length; i++) if (GROUPS[i].test(it)) return GROUPS[i];
    return GROUPS[2];
  }
  var KIND_ICON = { event: "meeting", series: "meeting", topic: "meeting", task: "task", reminder: "remind", note: "note", journal: "note", chat: "chat", tgchat: "chat", mail: "mail", mailaddr: "mail", person: "person" };

  // ---------- общий каркас окна ----------

  function makeOverlay(id, cls) {
    var old = document.getElementById(id);
    if (old) old.remove();
    var ov = document.createElement("div");
    ov.id = id;
    ov.className = "modal-overlay pj-overlay " + (cls || "");
    ov.innerHTML = '<div class="modal modal--sheet pj-sheet" role="dialog" aria-modal="true"></div>';
    document.body.appendChild(ov);
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeOverlay(ov);
    });
    ov._onKey = function (e) {
      if (e.key !== "Escape" || document.getElementById("nl-sheet-overlay")) return;
      var top = topOverlay();
      if (top !== ov) return;
      e.preventDefault();
      closeOverlay(ov);
    };
    document.addEventListener("keydown", ov._onKey);
    if (app().syncAppOverlay) app().syncAppOverlay();
    setTimeout(markNav, 0);
    return ov;
  }
  function topOverlay() {
    var all = document.querySelectorAll(".pj-overlay");
    return all.length ? all[all.length - 1] : null;
  }
  function closeOverlay(ov) {
    if (!ov) return;
    document.removeEventListener("keydown", ov._onKey);
    ov.remove();
    if (typeof ov._onClose === "function") ov._onClose();
    if (app().syncAppOverlay) app().syncAppOverlay();
    setTimeout(markNav, 0);
  }

  // ---------- кэш списка ----------

  var listCache = null;
  var listLoading = null;
  var currentOrgId = null;
  function loadList(force) {
    if (listLoading && !force) return listLoading;
    if (listCache && !force) return Promise.resolve(listCache);
    listLoading = api("/projects?archived=1")
      .then(function (r) {
        listCache = (r && r.projects) || [];
        listLoading = null;
        notifyChanged();
        return listCache;
      })
      .catch(function (e) {
        listLoading = null;
        throw e;
      });
    return listLoading;
  }
  function activeProjects() {
    return (listCache || []).filter(function (p) {
      return !p.archived && p.status !== "done";
    });
  }
  var listeners = [];
  function onChange(fn) {
    listeners.push(fn);
  }
  function notifyChanged() {
    listeners.forEach(function (fn) {
      try {
        fn(listCache || []);
      } catch (_) {}
    });
  }
  function loadOrgId() {
    if (currentOrgId !== null) return Promise.resolve(currentOrgId);
    return api("/orgs")
      .then(function (r) {
        currentOrgId = (r && r.current_id) || "";
        return currentOrgId;
      })
      .catch(function () {
        currentOrgId = "";
        return "";
      });
  }

  // ---------- окно «Проекты» ----------

  var listState = { tab: "active", q: "" };

  function openList() {
    var ov = makeOverlay("pj-list-overlay", "pj-list-overlay");
    var sheet = ov.querySelector(".pj-sheet");
    sheet.classList.add("pj-sheet--list");
    sheet.setAttribute("aria-label", "Проекты");
    sheet.innerHTML =
      '<div class="pj-head">' +
      '<h2 class="pj-title">Проекты</h2>' +
      '<button type="button" class="pj-btn pj-btn--primary" data-pj="new">' + icon("plus", 16) + "<span>Новый проект</span></button>" +
      '<button type="button" class="pj-icon-btn" data-pj="close" aria-label="Закрыть">' + icon("close") + "</button>" +
      "</div>" +
      '<div class="pj-tools">' +
      '<label class="pj-search">' + icon("search", 16) + '<input type="search" placeholder="Найти проект" aria-label="Найти проект" data-pj="q" /></label>' +
      '<div class="pj-seg" role="tablist" aria-label="Какие проекты"></div>' +
      "</div>" +
      '<div class="pj-list" data-pj="list"><div class="pj-empty">Загружаю…</div></div>' +
      '<p class="pj-foot">Бывшие связки с названием и старые папки — здесь. Личные проекты видите только вы.</p>';
    sheet.addEventListener("click", function (e) {
      var b = e.target.closest("[data-pj]");
      if (!b) return;
      var act = b.getAttribute("data-pj");
      if (act === "close") closeOverlay(ov);
      else if (act === "new") createProjectFlow(ov);
      else if (act === "tab") {
        listState.tab = b.getAttribute("data-tab");
        paintList(ov);
      } else if (act === "open") {
        openPage(b.getAttribute("data-id"));
      }
    });
    var q = sheet.querySelector('[data-pj="q"]');
    q.value = listState.q;
    q.addEventListener("input", function () {
      listState.q = q.value;
      paintList(ov);
    });
    loadList(true)
      .then(function () {
        paintList(ov);
      })
      .catch(function (e) {
        sheet.querySelector('[data-pj="list"]').innerHTML = '<div class="pj-empty">' + esc(errText(e)) + "</div>";
      });
    if (isDesktop()) setTimeout(function () { q.focus(); }, 50);
    return ov;
  }

  function listBucket(p) {
    if (p.archived || p.status === "done") return "archive";
    return p.status === "paused" ? "paused" : "active";
  }

  function paintList(ov) {
    var sheet = ov.querySelector(".pj-sheet");
    var all = listCache || [];
    var counts = { active: 0, paused: 0, archive: 0 };
    all.forEach(function (p) {
      counts[listBucket(p)]++;
    });
    var seg = sheet.querySelector(".pj-seg");
    seg.innerHTML = [
      ["active", "Идут"],
      ["paused", "Пауза"],
      ["archive", "Архив"],
    ]
      .map(function (t) {
        var on = listState.tab === t[0];
        return (
          '<button type="button" role="tab" aria-selected="' + on + '" class="pj-seg-btn' + (on ? " is-on" : "") + '" data-pj="tab" data-tab="' + t[0] + '">' +
          esc(t[1]) + (counts[t[0]] ? " · " + counts[t[0]] : "") + "</button>"
        );
      })
      .join("");
    var words = String(listState.q || "").toLowerCase().split(/\s+/).filter(Boolean);
    var xs = all.filter(function (p) {
      if (words.length) {
        var t = String(p.title || "").toLowerCase();
        return words.every(function (w) { return t.indexOf(w) >= 0; });
      }
      return listBucket(p) === listState.tab;
    });
    var host = sheet.querySelector('[data-pj="list"]');
    if (!xs.length) {
      host.innerHTML =
        '<div class="pj-empty">' +
        (words.length ? "Ничего не нашлось" : listState.tab === "active" ? "Проектов пока нет. Соберите в проект встречи, задачи, документы и чаты по клиенту или направлению." : "Здесь пусто") +
        "</div>";
      return;
    }
    host.innerHTML = xs
      .map(function (p) {
        var shared = (p.members || []).length > 1 || p.role !== "owner";
        var n = p.items_total || 0;
        return (
          '<button type="button" class="pj-row" data-pj="open" data-id="' + esc(p.id) + '">' +
          avatarHtml(p) +
          '<span class="pj-row-txt"><span class="pj-row-title">' + esc(p.title) + (shared ? ' <span class="pj-muted">· общий</span>' : "") + "</span>" +
          '<span class="pj-row-sub">' + (n ? esc(n + " " + plural(n, "элемент", "элемента", "элементов")) : "пусто") + (p.description ? " · " + esc(p.description.slice(0, 80)) : "") + "</span></span>" +
          '<span class="pj-status pj-status--' + esc(p.status) + '">' + esc(p.archived ? "В архиве" : STATUS[p.status] || "") + "</span>" +
          "</button>"
        );
      })
      .join("");
  }

  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }

  async function createProjectFlow(fromOv) {
    var name = await app().promptDialog("Название проекта", "");
    if (!name || !String(name).trim()) return;
    try {
      var oid = await loadOrgId();
      var r = await api("/projects", { method: "POST", body: JSON.stringify({ title: String(name).trim(), org_id: oid ? String(oid) : "" }) });
      await loadList(true);
      if (fromOv) closeOverlay(fromOv);
      openPage(r.project.id);
    } catch (e) {
      toast(errText(e));
    }
  }

  // ---------- страница проекта ----------

  var pageState = { id: null, tab: "all", data: null };

  function openPage(id) {
    pageState.id = String(id);
    pageState.tab = "all";
    var ov = makeOverlay("pj-page-overlay", "pj-page-overlay");
    var sheet = ov.querySelector(".pj-sheet");
    sheet.classList.add("pj-sheet--page");
    sheet.innerHTML = '<div class="pj-empty">Загружаю…</div>';
    sheet.addEventListener("click", function (e) {
      onPageClick(e, ov);
    });
    reloadPage(ov);
    return ov;
  }

  function reloadPage(ov) {
    ov = ov || document.getElementById("pj-page-overlay");
    if (!ov) return Promise.resolve();
    var id = pageState.id;
    return api("/projects/" + encodeURIComponent(id))
      .then(function (r) {
        if (pageState.id !== id) return;
        if (pageState.data && String(pageState.data.id) === id && (pageState.data.items || []).length !== (r.project.items || []).length) {
          delete passportCache[id]; // состав изменился — паспорт пересчитаем
        }
        pageState.data = r.project;
        paintPage(ov);
      })
      .catch(function (e) {
        var sheet = ov.querySelector(".pj-sheet");
        sheet.innerHTML =
          '<div class="pj-head"><span class="pj-title">Проект</span><button type="button" class="pj-icon-btn" data-pj="close" aria-label="Закрыть">' + icon("close") + "</button></div>" +
          '<div class="pj-empty">' + esc(errText(e)) + "</div>";
      });
  }

  function paintPage(ov) {
    var p = pageState.data;
    var sheet = ov.querySelector(".pj-sheet");
    sheet.setAttribute("aria-label", "Проект " + p.title);
    var members = p.members || [];
    var people = members.slice(0, 4).map(function (m) {
      return '<span class="pj-mini-ava" title="' + esc(m.label) + '">' + esc(initials(m.label)) + "</span>";
    }).join("");
    var others = members.filter(function (m) { return m.principal.indexOf("u:") !== 0; }).map(function (m) { return m.label; });
    var head =
      '<div class="pj-page-top">' +
      '<button type="button" class="pj-icon-btn pj-back" data-pj="close" aria-label="Закрыть">' + icon(isDesktop() ? "close" : "back") + "</button>" +
      '<div class="pj-page-actions">' +
      (p.can_share ? '<button type="button" class="pj-btn" data-pj="share">' + icon("share", 16) + '<span class="pj-hide-sm">Поделиться</span></button>' : "") +
      (p.can_edit ? '<button type="button" class="pj-btn pj-btn--primary" data-pj="add" aria-haspopup="menu">' + icon("plus", 16) + "<span>Добавить</span></button>" : "") +
      '<button type="button" class="pj-icon-btn" data-pj="more" aria-label="Ещё">' + icon("more") + "</button>" +
      "</div></div>" +
      '<div class="pj-page-head">' +
      avatarHtml(p, "pj-ava--lg") +
      '<div class="pj-page-head-txt">' +
      '<div class="pj-title-row"><h1 class="pj-page-title"' + (p.can_edit ? ' data-pj="rename" title="Переименовать"' : "") + ">" + esc(p.title) + "</h1>" +
      '<button type="button" class="pj-status pj-status--' + esc(p.status) + '"' + (p.can_edit ? ' data-pj="status"' : " disabled") + ">" +
      esc(p.archived ? "В архиве" : STATUS[p.status] || "") + (p.can_edit ? " ▾" : "") + "</button></div>" +
      (p.description || p.can_edit
        ? '<p class="pj-desc' + (p.description ? "" : " pj-desc--empty") + '"' + (p.can_edit ? ' data-pj="desc"' : "") + ">" +
          esc(p.description || "Добавить описание") + "</p>"
        : "") +
      '<div class="pj-meta">' +
      '<span class="pj-meta-item">Руководитель ' +
      (p.can_edit ? '<button type="button" class="pj-link" data-pj="lead">' + esc(p.lead_label || "не назначен") + "</button>" : "<b>" + esc(p.lead_label || "не назначен") + "</b>") +
      "</span>" +
      '<span class="pj-meta-item">Участники <span class="pj-ava-stack">' + people + "</span>" +
      (others.length ? '<span class="pj-muted">и ' + esc(others.join(", ")) + "</span>" : "") +
      "</span></div></div></div>";

    var items = p.items || [];
    var counts = {};
    items.forEach(function (it) {
      var g = groupOf(it).id;
      counts[g] = (counts[g] || 0) + 1;
    });
    var tabs =
      '<div class="pj-tabs" role="tablist" aria-label="Разделы проекта">' +
      [["all", "Всё", items.length]]
        .concat(GROUPS.map(function (g) { return [g.id, g.label, counts[g.id] || 0]; }))
        .filter(function (t) { return t[0] === "all" || t[2] || pageState.tab === t[0]; })
        .map(function (t) {
          var on = pageState.tab === t[0];
          return '<button type="button" role="tab" aria-selected="' + on + '" class="pj-tab' + (on ? " is-on" : "") + '" data-pj="tab" data-tab="' + t[0] + '">' +
            esc(t[1]) + (t[2] ? ' <span class="pj-muted">' + t[2] + "</span>" : "") + "</button>";
        })
        .join("") +
      '<button type="button" class="pj-tab" data-pj="hours">Часы</button>' +
      "</div>";

    var body;
    if (!items.length) {
      body =
        '<div class="pj-empty pj-empty--big">' +
        "<b>В проекте пока пусто</b>" +
        "<span>Добавьте встречи, задачи, документы и чаты. Саммари встреч и задачи из них подтянутся сами.</span>" +
        (p.can_edit ? '<button type="button" class="pj-btn pj-btn--primary" data-pj="add">' + icon("plus", 16) + "<span>Добавить</span></button>" : "") +
        "</div>";
    } else {
      var groups = pageState.tab === "all" ? GROUPS : GROUPS.filter(function (g) { return g.id === pageState.tab; });
      body = '<div class="pj-grid' + (pageState.tab === "all" ? "" : " pj-grid--one") + '">' +
        groups
          .map(function (g) {
            var xs = items.filter(function (it) { return groupOf(it) === g; });
            if (!xs.length) return "";
            var limit = pageState.tab === "all" ? 6 : 1000;
            return (
              '<section class="pj-card"><h2 class="pj-card-title">' + esc(g.label) + ' <span class="pj-muted">' + xs.length + "</span></h2>" +
              xs.slice(0, limit).map(function (it) { return itemRowHtml(it); }).join("") +
              (xs.length > limit ? '<button type="button" class="pj-link pj-card-more" data-pj="tab" data-tab="' + g.id + '">Все ' + xs.length + " ›</button>" : "") +
              "</section>"
            );
          })
          .join("") +
        "</div>";
    }
    var hidden = p.hidden
      ? '<p class="pj-hidden-note">Ещё ' + p.hidden + " " + plural(p.hidden, "элемент скрыт", "элемента скрыто", "элементов скрыто") +
        " — например, чужая почта или чат, где вас нет. Их видят только те, у кого они есть.</p>"
      : "";
    var pp = passportCache[String(p.id)];
    var askText = pp
      ? pp.text || passportNumbers(pp.facts)
      : "Лео смотрит, что в проекте главное и что буксует…";
    var ask = items.length
      ? '<div class="pj-ask' + (pp && pp.facts && pp.facts.stuck ? " pj-ask--warn" : "") + '"><span>' + icon("leo") + '</span><span data-pj="passport">' + esc(askText) + "</span>" +
        '<button type="button" class="pj-btn pj-btn--soft" data-pj="ask">Спросить Лео про проект</button></div>'
      : "";
    sheet.innerHTML = head + ask + tabs + body + hidden;
    if (items.length && !pp) loadPassport(String(p.id));
  }

  // Паспорт проекта: строки Лео по фактам (сервер кэширует, пока факты не изменятся).
  var passportCache = {};
  var passportLoading = {};
  function passportNumbers(f) {
    if (!f) return "";
    var bits = ["открыто задач " + (f.tasks_open || 0)];
    if (f.overdue_total) bits.push("просрочено " + f.overdue_total);
    if (f.tasks_done_week) bits.push("сдано за неделю " + f.tasks_done_week);
    if (f.upcoming) bits.push("встреч впереди " + f.upcoming);
    return bits.join(" · ");
  }
  function loadPassport(id) {
    if (passportLoading[id]) return;
    passportLoading[id] = true;
    // Лео пишет паспорт моделью — дольше общего лимита запроса (8 с)
    api("/projects/" + encodeURIComponent(id) + "/passport", { method: "GET", timeout: 60000 })
      .then(function (r) {
        passportCache[id] = { text: (r && r.text) || "", facts: (r && r.facts) || null };
      })
      .catch(function () {
        passportCache[id] = { text: "", facts: null, failed: true };
      })
      .then(function () {
        passportLoading[id] = false;
        var ov = document.getElementById("pj-page-overlay");
        if (ov && pageState.data && String(pageState.data.id) === id) {
          var el = ov.querySelector('[data-pj="passport"]');
          var pp = passportCache[id];
          if (el) el.textContent = pp.text || passportNumbers(pp.facts) || "Спросите Лео: что важного, что буксует, кто кому что должен.";
          if (pp.failed) delete passportCache[id]; // не получилось — попробуем при следующем открытии
          var box = el && el.closest(".pj-ask");
          if (box && pp.facts && pp.facts.stuck) box.classList.add("pj-ask--warn");
        }
      });
  }

  function itemRowHtml(it) {
    var sub = it.subtitle || "";
    if (it.kind === "task") sub += it.done ? " · выполнена" : "";
    return (
      '<div class="pj-item' + (it.kind === "task" && it.done ? " is-done" : "") + '">' +
      '<button type="button" class="pj-item-open" data-pj="item" data-kind="' + esc(it.kind) + '" data-id="' + esc(it.id) + '">' +
      '<span class="pj-ico pj-ico--' + esc(groupOf(it).id) + '">' + icon(KIND_ICON[it.kind] || "note", 16) + "</span>" +
      '<span class="pj-item-txt"><span class="pj-item-title">' + esc(it.title) + "</span>" +
      (sub ? '<span class="pj-item-sub">' + esc(sub) + "</span>" : "") + "</span></button>" +
      (it.can_remove
        ? '<button type="button" class="pj-icon-btn pj-item-x" data-pj="remove" data-kind="' + esc(it.kind) + '" data-id="' + esc(it.id) + '" aria-label="Убрать из проекта">' + icon("close", 14) + "</button>"
        : "") +
      "</div>"
    );
  }

  function findItem(kind, id) {
    var items = (pageState.data && pageState.data.items) || [];
    for (var i = 0; i < items.length; i++) if (items[i].kind === kind && String(items[i].id) === String(id)) return items[i];
    return null;
  }

  function onPageClick(e, ov) {
    var b = e.target.closest("[data-pj]");
    if (!b) return;
    var act = b.getAttribute("data-pj");
    var p = pageState.data;
    if (act === "close") return closeOverlay(ov);
    if (!p) return;
    if (act === "tab") {
      pageState.tab = b.getAttribute("data-tab");
      paintPage(ov);
    } else if (act === "item") {
      var it = findItem(b.getAttribute("data-kind"), b.getAttribute("data-id"));
      if (!it) return;
      // документ и чат открываются под окнами — проект закрываем
      if (it.kind === "note" || it.kind === "chat" || it.kind === "journal") closeOverlay(ov);
      if (app().openLinkedItem) app().openLinkedItem(it);
    } else if (act === "remove") {
      removeItem(b.getAttribute("data-kind"), b.getAttribute("data-id"));
    } else if (act === "hours") {
      openHours({ projectId: p.id, title: p.title });
    } else if (act === "share") {
      openShare();
    } else if (act === "add") {
      openAddMenu(b);
    } else if (act === "more") {
      openMoreMenu(b, ov);
    } else if (act === "rename") {
      renameProject();
    } else if (act === "desc") {
      editDescription();
    } else if (act === "status") {
      openStatusMenu(b);
    } else if (act === "lead") {
      openLeadMenu(b);
    } else if (act === "ask") {
      if (app().askLeoAboutProject) app().askLeoAboutProject(p);
      closeOverlay(ov);
    }
  }

  function patchProject(body) {
    return api("/projects/" + encodeURIComponent(pageState.id), { method: "PATCH", body: JSON.stringify(body) })
      .then(function (r) {
        pageState.data = r.project;
        var ov = document.getElementById("pj-page-overlay");
        if (ov) paintPage(ov);
        loadList(true).catch(function () {});
        return r.project;
      })
      .catch(function (e) {
        toast(errText(e));
      });
  }

  async function renameProject() {
    var name = await app().promptDialog("Название проекта", pageState.data.title);
    if (name && String(name).trim() && String(name).trim() !== pageState.data.title) patchProject({ title: String(name).trim() });
  }
  async function editDescription() {
    var d = await app().promptDialog("Описание проекта", pageState.data.description || "");
    if (d !== null && d !== undefined) patchProject({ description: String(d) });
  }
  function sheetMenu(title, items, anchor) {
    if (app().openActionSheet) app().openActionSheet(title, items, anchor);
  }
  function openStatusMenu(anchor) {
    var p = pageState.data;
    sheetMenu(
      "Статус",
      ["active", "paused", "done"].map(function (s) {
        return { label: STATUS[s], icon: p.status === s ? "check" : "", active: p.status === s, onClick: function () { patchProject({ status: s }); } };
      }),
      anchor
    );
  }
  function openLeadMenu(anchor) {
    var p = pageState.data;
    var items = (p.members || [])
      .filter(function (m) { return m.principal.indexOf("u:") === 0; })
      .map(function (m) {
        var uid = m.principal.slice(2);
        return { label: m.label, icon: p.lead_user_id === uid ? "check" : "person", active: p.lead_user_id === uid, onClick: function () { patchProject({ lead_user_id: uid }); } };
      });
    if (p.lead_user_id) items.push("-", { label: "Без руководителя", onClick: function () { patchProject({ lead_user_id: "" }); } });
    if (!items.length) items.push({ label: "Сначала поделитесь проектом", onClick: openShare });
    sheetMenu("Руководитель — из участников", items, anchor);
  }
  function openMoreMenu(anchor, ov) {
    var p = pageState.data;
    var items = [];
    if (p.can_edit) {
      items.push({ label: "Переименовать", onClick: renameProject });
      items.push({ label: "Описание", onClick: editDescription });
      items.push({ label: "Цвет…", onClick: function () { openColorMenu(anchor); } });
    }
    if (p.can_delete) {
      items.push({ label: p.archived ? "Вернуть из архива" : "В архив", onClick: function () { patchProject({ archived: !p.archived }); } });
    }
    if (p.role !== "owner" && p.lead_user_id !== p.my_user_id) {
      items.push("-", { label: "Выйти из проекта", danger: true, onClick: leaveProject });
    }
    if (p.can_delete) {
      items.push("-", { label: "Удалить проект", icon: "trash", danger: true, onClick: function () { deleteProject(ov); } });
    }
    sheetMenu("", items, anchor);
  }
  function openColorMenu(anchor) {
    var p = pageState.data;
    sheetMenu(
      "Цвет проекта",
      [""].concat(COLOR_KEYS).map(function (c) {
        return { label: COLOR_NAMES[c], icon: p.color === c ? "check" : "", active: p.color === c, onClick: function () { patchProject({ color: c }); } };
      }),
      anchor
    );
  }
  async function deleteProject(ov) {
    var ok = await app().confirm("Удалить проект «" + pageState.data.title + "»? Документы, задачи, встречи и чаты останутся — пропадёт только проект.");
    if (!ok) return;
    try {
      await api("/projects/" + encodeURIComponent(pageState.id), { method: "DELETE" });
      closeOverlay(ov);
      loadList(true).catch(function () {});
      toast("Проект удалён");
    } catch (e) {
      toast(errText(e));
    }
  }
  async function leaveProject() {
    var ok = await app().confirm("Выйти из проекта «" + pageState.data.title + "»? Вернуть сможет его владелец.");
    if (!ok) return;
    try {
      var me = pageState.data.my_principal;
      await api("/projects/" + encodeURIComponent(pageState.id) + "/members/" + encodeURIComponent(me), { method: "DELETE" });
      closeOverlay(document.getElementById("pj-page-overlay"));
      loadList(true).catch(function () {});
    } catch (e) {
      toast(errText(e));
    }
  }
  async function removeItem(kind, id) {
    try {
      var r = await api("/projects/" + encodeURIComponent(pageState.id) + "/items/" + encodeURIComponent(kind) + "/" + encodeURIComponent(id), { method: "DELETE" });
      pageState.data = r.project;
      var ov = document.getElementById("pj-page-overlay");
      if (ov) paintPage(ov);
    } catch (e) {
      toast(errText(e));
    }
  }

  // ---------- «+ Добавить»: создать в проекте или добавить существующее ----------

  function openAddMenu(anchor) {
    var p = pageState.data;
    var ref = { id: Number(p.id), name: p.title };
    var A = app();
    function closePage() {
      closeOverlay(document.getElementById("pj-page-overlay"));
    }
    var items = [
      { section: "Создать в проекте" },
      { label: "Документ", icon: "file", onClick: function () { closePage(); A.createInProject("doc", ref); } },
      { label: "Доска", icon: "stack", onClick: function () { closePage(); A.createInProject("board", ref); } },
      { label: "Макет", icon: "stack", onClick: function () { closePage(); A.createInProject("design", ref); } },
      { label: "Презентация", icon: "stack", onClick: function () { closePage(); A.createInProject("web", ref); } },
      { label: "Надиктовать", icon: "file", onClick: function () { closePage(); A.createInProject("dictate", ref); } },
      { label: "Задача", icon: "check", onClick: function () { A.createInProject("task", ref, function () { reloadPage(); }); } },
      { label: "Встреча", icon: "folder", onClick: function () { A.createInProject("meeting", ref, function () { reloadPage(); }); } },
      { label: "Чат с Лео", icon: "chat", onClick: function () { closePage(); A.createInProject("chat", ref); } },
      "-",
      { label: "Добавить существующее…", icon: "link", onClick: openAddExisting },
    ];
    sheetMenu("", items, anchor);
  }

  // ---------- «Добавить существующее»: встречи и серии, задачи, документы, чаты, почта, люди ----------

  var ADD_TABS = [
    ["all", "Всё"],
    ["meetings", "Встречи"],
    ["task", "Задачи"],
    ["note", "Документы"],
    ["chats", "Чаты"],
    ["mail", "Почта"],
    ["person", "Люди"],
  ];
  var addState = { tab: "all", q: "", picked: {}, rows: [], seq: 0, timer: 0, series: null, occMap: {} };

  function addKey(it) {
    return it.kind + ":" + it.id;
  }

  function fetchAddRows(tab, q) {
    var A = app();
    function link(t, limit) {
      if (!A.searchLinkItems) return Promise.resolve([]);
      return A.searchLinkItems(q, t, false)
        .then(function (xs) { return (xs || []).slice(0, limit || 40); })
        .catch(function () { return []; });
    }
    function src(t, limit) {
      return api("/project-sources?tab=" + t + "&q=" + encodeURIComponent(q || ""))
        .then(function (r) { return ((r && r.items) || []).slice(0, limit || 40); })
        .catch(function () { return []; });
    }
    var jobs;
    if (tab === "meetings") jobs = [["Встречи", src("meetings")], ["Саммари встреч", link("meeting")]];
    else if (tab === "task") jobs = [["Задачи", link("task")]];
    else if (tab === "note") jobs = [["Документы", link("note")]];
    else if (tab === "chats") jobs = [["Чаты Telegram", src("tgchats")], ["Чаты с Лео", link("chat")]];
    else if (tab === "mail") jobs = [["Почта", src("mail")]];
    else if (tab === "person") jobs = [["Люди", link("person")]];
    else
      jobs = [
        ["Встречи", src("meetings", 4)],
        ["Задачи", link("task", 4)],
        ["Документы", link("note", 4)],
        ["Чаты", src("tgchats", 3)],
        ["Почта", q ? src("mail", 3) : Promise.resolve([])],
      ];
    return Promise.all(jobs.map(function (j) { return j[1]; })).then(function (res) {
      var have = {};
      ((pageState.data && pageState.data.items) || []).forEach(function (it) { have[addKey(it)] = 1; });
      var out = [];
      res.forEach(function (xs, i) {
        xs = xs.filter(function (x) { return x && x.kind !== "bundle" && !have[addKey(x)]; });
        if (!xs.length) return;
        out.push({ head: jobs[i][0] });
        xs.forEach(function (x) { out.push(x); });
      });
      return out;
    });
  }

  function openAddExisting() {
    var p = pageState.data;
    addState = { tab: "all", q: "", picked: {}, rows: [], seq: 0, timer: 0, series: null, occMap: {} };
    var ov = makeOverlay("pj-add-overlay", "pj-add-overlay");
    var sheet = ov.querySelector(".pj-sheet");
    sheet.classList.add("pj-sheet--add");
    sheet.setAttribute("aria-label", "Добавить в проект");
    sheet.innerHTML =
      '<div class="pj-head"><h2 class="pj-title pj-title--sm">Добавить в «' + esc(p.title) + "»</h2>" +
      '<button type="button" class="pj-icon-btn" data-pj="close" aria-label="Закрыть">' + icon("close") + "</button></div>" +
      '<div data-pj="body" class="pj-add-body"></div>' +
      '<div class="pj-add-foot"><span data-pj="count" class="pj-muted"></span>' +
      '<button type="button" class="pj-btn" data-pj="close">Отмена</button>' +
      '<button type="button" class="pj-btn pj-btn--primary" data-pj="submit" disabled>Добавить</button></div>';
    sheet.addEventListener("click", function (e) {
      var b = e.target.closest("[data-pj]");
      if (!b) return;
      var act = b.getAttribute("data-pj");
      if (act === "close") closeOverlay(ov);
      else if (act === "tab") {
        addState.tab = b.getAttribute("data-tab");
        paintAddList(sheet, true);
      } else if (act === "series") openSeries(sheet, b.getAttribute("data-key"), b.getAttribute("data-title"));
      else if (act === "series-back") {
        addState.series = null;
        paintAddList(sheet, false);
      } else if (act === "submit") submitAdd(ov);
    });
    sheet.addEventListener("change", function (e) {
      var cb = e.target.closest('input[type="checkbox"][data-key]');
      if (cb) {
        var key = cb.getAttribute("data-key");
        if (cb.checked) {
          var row = addState.rows.filter(function (r) { return r.kind && addKey(r) === key; })[0] || addState.occMap[key];
          if (row) addState.picked[key] = { kind: row.kind, id: row.id, label: row.title || "", series: row.series || "" };
        } else delete addState.picked[key];
        paintAddCount(sheet);
        return;
      }
      var radio = e.target.closest('input[type="radio"][name="pj-series-scope"]');
      if (radio) seriesScope(sheet, radio.value);
    });
    paintAddList(sheet, true);
  }

  function paintAddCount(sheet) {
    var keys = Object.keys(addState.picked);
    var n = keys.length;
    var meet = keys.some(function (k) { return /^(event|series|topic):/.test(k); });
    sheet.querySelector('[data-pj="count"]').textContent = n
      ? "Выбрано " + n + (meet ? " · саммари встреч и их задачи добавятся сами" : "")
      : "";
    var btn = sheet.querySelector('[data-pj="submit"]');
    btn.disabled = !n;
    btn.textContent = n ? "Добавить " + n : "Добавить";
  }

  function paintAddList(sheet, reload) {
    var body = sheet.querySelector('[data-pj="body"]');
    body.innerHTML =
      '<label class="pj-search">' + icon("search", 16) +
      '<input type="search" placeholder="Поиск по встречам, задачам, документам, чатам, почте" aria-label="Что добавить" data-pj="q" /></label>' +
      '<div class="pj-chips" role="tablist" aria-label="Что добавить">' +
      ADD_TABS.map(function (t) {
        var on = addState.tab === t[0];
        return '<button type="button" role="tab" aria-selected="' + on + '" class="pj-chip' + (on ? " is-on" : "") + '" data-pj="tab" data-tab="' + t[0] + '">' + esc(t[1]) + "</button>";
      }).join("") +
      "</div>" +
      '<div class="pj-add-list" data-pj="rows"><div class="pj-empty">Ищу…</div></div>';
    var q = body.querySelector('[data-pj="q"]');
    q.value = addState.q;
    q.addEventListener("input", function () {
      addState.q = q.value;
      clearTimeout(addState.timer);
      addState.timer = setTimeout(function () { loadAddRows(sheet); }, 250);
    });
    if (isDesktop()) setTimeout(function () { q.focus(); }, 30);
    if (reload) loadAddRows(sheet);
    else paintAddRows(sheet);
    paintAddCount(sheet);
  }

  function loadAddRows(sheet) {
    var seq = ++addState.seq;
    fetchAddRows(addState.tab, addState.q).then(function (rows) {
      if (seq !== addState.seq) return;
      addState.rows = rows;
      paintAddRows(sheet);
    });
  }

  function paintAddRows(sheet) {
    var host = sheet.querySelector('[data-pj="rows"]');
    if (!host) return;
    var rows = addState.rows || [];
    if (!rows.length) {
      host.innerHTML = '<div class="pj-empty">' + (addState.q ? "Ничего не нашлось" : "Здесь пока нечего добавить") + "</div>";
      return;
    }
    host.innerHTML = rows
      .map(function (r) {
        if (r.head) return '<div class="pj-add-head">' + esc(r.head) + "</div>";
        var key = addKey(r);
        var picked = !!addState.picked[key] || (r.kind === "series" && Object.keys(addState.picked).some(function (k) { return addState.picked[k].series === r.id; }));
        var grp = groupOf({ kind: r.kind, is_transcription: r.is_transcription || r.kind === "meeting" });
        return (
          '<div class="pj-add-row' + (picked ? " is-on" : "") + '">' +
          '<label class="pj-add-label"><input type="checkbox" data-key="' + esc(key) + '"' + (addState.picked[key] ? " checked" : "") + " />" +
          '<span class="pj-ico pj-ico--' + esc(grp.id) + '">' + icon(KIND_ICON[r.kind] || (r.kind === "meeting" ? "meeting" : "note"), 16) + "</span>" +
          '<span class="pj-item-txt"><span class="pj-item-title">' + esc(r.title || "") + "</span>" +
          (r.subtitle ? '<span class="pj-item-sub">' + esc(r.subtitle) + "</span>" : "") + "</span></label>" +
          (r.kind === "series" ? '<button type="button" class="pj-link pj-add-series" data-pj="series" data-key="' + esc(r.id) + '" data-title="' + esc(r.title || "") + '">Какие встречи ›</button>' : "") +
          "</div>"
        );
      })
      .join("");
  }

  // Серия: вся (и будущие) или только выбранные встречи.
  function openSeries(sheet, key, title) {
    var cur = addState.picked["series:" + key] ? "all" : Object.keys(addState.picked).some(function (k) { return addState.picked[k].series === key; }) ? "some" : "all";
    addState.series = { key: key, title: title, mode: cur, occ: null };
    var body = sheet.querySelector('[data-pj="body"]');
    body.innerHTML =
      '<div class="pj-series-head"><button type="button" class="pj-icon-btn" data-pj="series-back" aria-label="Назад">' + icon("back") + "</button>" +
      '<b class="pj-item-title">' + esc(title) + "</b></div>" +
      '<fieldset class="pj-scope"><legend class="pj-muted">Что положить в проект</legend>' +
      '<label class="pj-scope-opt"><input type="radio" name="pj-series-scope" value="all"' + (cur === "all" ? " checked" : "") + " />" +
      "<span><b>Всю серию</b><small>прошедшие и будущие встречи — их саммари и задачи попадут в проект сами</small></span></label>" +
      '<label class="pj-scope-opt"><input type="radio" name="pj-series-scope" value="some"' + (cur === "some" ? " checked" : "") + " />" +
      "<span><b>Только выбранные встречи</b><small>например, когда в серии обсуждают разных клиентов</small></span></label></fieldset>" +
      '<div class="pj-add-list" data-pj="occ"><div class="pj-empty">Загружаю встречи…</div></div>';
    api("/project-sources/series?key=" + encodeURIComponent(key))
      .then(function (r) {
        if (!addState.series || addState.series.key !== key) return;
        addState.series.occ = (r && r.items) || [];
        paintOcc(sheet);
      })
      .catch(function () {
        sheet.querySelector('[data-pj="occ"]').innerHTML = '<div class="pj-empty">Не получилось загрузить встречи</div>';
      });
  }

  function paintOcc(sheet) {
    var s = addState.series;
    var host = sheet.querySelector('[data-pj="occ"]');
    if (!s || !host) return;
    var xs = s.occ || [];
    if (!xs.length) {
      host.innerHTML = '<div class="pj-empty">Встреч серии рядом по времени нет</div>';
      return;
    }
    host.classList.toggle("is-dim", s.mode !== "some");
    host.innerHTML = xs
      .map(function (o) {
        var key = addKey(o);
        addState.occMap[key] = { kind: o.kind, id: o.id, title: s.title + " · " + o.date, series: s.key };
        var on = !!addState.picked[key];
        return '<label class="pj-add-row pj-add-label' + (on ? " is-on" : "") + '"><input type="checkbox" data-key="' + esc(key) + '"' + (on ? " checked" : "") + (s.mode !== "some" ? " disabled" : "") + " />" +
          '<span class="pj-occ-date">' + esc(o.date) + "</span>" +
          '<span class="pj-item-sub">' + esc(o.past ? (o.has_summary ? "саммари есть" : "прошла") : "будет") + "</span></label>";
      })
      .join("");
  }

  function seriesScope(sheet, mode) {
    var s = addState.series;
    if (!s) return;
    s.mode = mode;
    var skey = "series:" + s.key;
    if (mode === "all") {
      Object.keys(addState.picked).forEach(function (k) { if (addState.picked[k].series === s.key) delete addState.picked[k]; });
      addState.picked[skey] = { kind: "series", id: s.key, label: s.title };
    } else {
      delete addState.picked[skey];
    }
    paintOcc(sheet);
    paintAddCount(sheet);
  }

  async function submitAdd(ov) {
    var items = Object.keys(addState.picked).map(function (k) {
      var x = addState.picked[k];
      return { kind: x.kind === "meeting" ? "note" : x.kind, id: String(x.id), label: x.label || "" };
    });
    if (!items.length) return;
    var btn = ov.querySelector('[data-pj="submit"]');
    btn.disabled = true;
    try {
      var r = await api("/projects/" + encodeURIComponent(pageState.id) + "/items", { method: "POST", body: JSON.stringify({ items: items }) });
      pageState.data = r.project;
      closeOverlay(ov);
      var page = document.getElementById("pj-page-overlay");
      if (page) paintPage(page);
      loadList(true).catch(function () {});
      toast(items.length === 1 ? "Добавлено в проект" : "Добавлено: " + items.length);
    } catch (e) {
      btn.disabled = false;
      toast(errText(e));
    }
  }

  // ---------- «Поделиться» ----------

  var shareState = { role: "editor", q: "", timer: 0, cands: null };

  function openShare() {
    var p = pageState.data;
    var ov = makeOverlay("pj-share-overlay", "pj-share-overlay");
    var sheet = ov.querySelector(".pj-sheet");
    sheet.classList.add("pj-sheet--share");
    sheet.setAttribute("aria-label", "Поделиться проектом");
    sheet.innerHTML =
      '<div class="pj-head"><h2 class="pj-title pj-title--sm">Поделиться «' + esc(p.title) + "»</h2>" +
      '<button type="button" class="pj-icon-btn" data-pj="close" aria-label="Закрыть">' + icon("close") + "</button></div>" +
      '<div class="pj-share-add">' +
      '<label class="pj-search pj-search--grow">' + icon("search", 16) +
      '<input type="search" placeholder="Человек, отдел, команда или вся организация" aria-label="Кому открыть" data-pj="q" /></label>' +
      '<select class="pj-select" data-pj="role" aria-label="Роль"><option value="editor">Редактирует</option><option value="viewer">Смотрит</option></select>' +
      "</div>" +
      '<div class="pj-cands" data-pj="cands"></div>' +
      '<div class="pj-share-sub">Доступ есть у</div>' +
      '<div class="pj-members" data-pj="members"></div>' +
      '<p class="pj-note">Участники видят документы, задачи и встречи проекта — только читать. Почту и внешние чаты видит лишь тот, у кого они и так есть.</p>';
    var q = sheet.querySelector('[data-pj="q"]');
    var roleSel = sheet.querySelector('[data-pj="role"]');
    roleSel.value = shareState.role;
    roleSel.addEventListener("change", function () {
      shareState.role = roleSel.value;
    });
    q.addEventListener("input", function () {
      clearTimeout(shareState.timer);
      shareState.timer = setTimeout(function () { loadCands(sheet, q.value); }, 200);
    });
    q.addEventListener("focus", function () {
      if (!shareState.cands) loadCands(sheet, q.value);
    });
    sheet.addEventListener("click", function (e) {
      var b = e.target.closest("[data-pj]");
      if (!b) return;
      var act = b.getAttribute("data-pj");
      if (act === "close") closeOverlay(ov);
      else if (act === "pick") addMember(sheet, b.getAttribute("data-principal"), b.getAttribute("data-org"));
      else if (act === "unshare") removeMember(sheet, b.getAttribute("data-principal"));
    });
    sheet.addEventListener("change", function (e) {
      var s = e.target.closest('[data-pj="member-role"]');
      if (s) setRole(sheet, s.getAttribute("data-principal"), s.value);
    });
    ov._onClose = function () {
      shareState.cands = null;
      reloadPage();
    };
    paintMembers(sheet);
    if (isDesktop()) setTimeout(function () { q.focus(); }, 50);
  }

  function loadCands(sheet, q) {
    return api("/projects/" + encodeURIComponent(pageState.id) + "/share-candidates?q=" + encodeURIComponent(q || ""))
      .then(function (r) {
        shareState.cands = r;
        var rows = []
          .concat(r.groups || [], r.orgs || [], r.people || [])
          .filter(function (c) { return !c.member; })
          .slice(0, 8);
        var host = sheet.querySelector('[data-pj="cands"]');
        if (!rows.length) {
          host.innerHTML = q ? '<div class="pj-cand-empty">Никого не нашлось в ваших организациях</div>' : "";
          return;
        }
        host.innerHTML = rows
          .map(function (c) {
            var grp = c.principal.indexOf("u:") !== 0;
            return '<button type="button" class="pj-cand" data-pj="pick" data-principal="' + esc(c.principal) + '" data-org="' + esc(c.org_id || "") + '">' +
              '<span class="pj-mini-ava' + (grp ? " pj-mini-ava--grp" : "") + '">' + esc(initials(c.label)) + "</span>" +
              '<span class="pj-item-txt"><span class="pj-item-title">' + esc(c.label) + '</span><span class="pj-item-sub">' + esc(c.sub || "") + "</span></span></button>";
          })
          .join("");
      })
      .catch(function (e) {
        sheet.querySelector('[data-pj="cands"]').innerHTML = '<div class="pj-cand-empty">' + esc(errText(e)) + "</div>";
      });
  }

  function paintMembers(sheet) {
    var p = pageState.data;
    var me = p.my_principal;
    sheet.querySelector('[data-pj="members"]').innerHTML = (p.members || [])
      .map(function (m) {
        var grp = m.principal.indexOf("u:") !== 0;
        var mine = m.principal === me;
        var lead = !grp && p.lead_user_id === m.principal.slice(2);
        var sub = (mine ? "вы" : "") + (lead ? (mine ? " · " : "") + "руководитель проекта" : "");
        var canChange = p.can_share && !mine && !(m.role === "owner" && p.role !== "owner");
        var roleCtl = canChange
          ? '<select class="pj-select pj-select--sm" data-pj="member-role" data-principal="' + esc(m.principal) + '" aria-label="Роль: ' + esc(m.label) + '">' +
            ["viewer", "editor", "owner"]
              .filter(function (r) { return r !== "owner" || p.role === "owner"; })
              .map(function (r) {
                return '<option value="' + r + '"' + (m.role === r ? " selected" : "") + ">" + { viewer: "Смотрит", editor: "Редактирует", owner: "Владелец" }[r] + "</option>";
              })
              .join("") +
            "</select>" +
            '<button type="button" class="pj-icon-btn" data-pj="unshare" data-principal="' + esc(m.principal) + '" aria-label="Убрать доступ: ' + esc(m.label) + '">' + icon("close", 14) + "</button>"
          : '<span class="pj-muted">' + esc(m.role_label) + "</span>";
        return '<div class="pj-member"><span class="pj-mini-ava' + (grp ? " pj-mini-ava--grp" : "") + '">' + esc(initials(m.label)) + "</span>" +
          '<span class="pj-item-txt"><span class="pj-item-title">' + esc(m.label) + "</span>" + (sub ? '<span class="pj-item-sub">' + esc(sub) + "</span>" : "") + "</span>" +
          roleCtl + "</div>";
      })
      .join("");
  }

  function afterMembers(sheet, r) {
    if (r && r.project) pageState.data = r.project;
    paintMembers(sheet);
    var q = sheet.querySelector('[data-pj="q"]');
    if (shareState.cands) loadCands(sheet, q ? q.value : "");
  }
  function addMember(sheet, principal, orgId) {
    return api("/projects/" + encodeURIComponent(pageState.id) + "/members", {
      method: "POST",
      body: JSON.stringify({ principal: principal, role: shareState.role, org_id: orgId || "" }),
    })
      .then(function (r) {
        var q = sheet.querySelector('[data-pj="q"]');
        if (q) q.value = "";
        afterMembers(sheet, r);
      })
      .catch(function (e) { toast(errText(e)); });
  }
  function setRole(sheet, principal, role) {
    return api("/projects/" + encodeURIComponent(pageState.id) + "/members", { method: "POST", body: JSON.stringify({ principal: principal, role: role }) })
      .then(function (r) { afterMembers(sheet, r); })
      .catch(function (e) {
        toast(errText(e));
        paintMembers(sheet);
      });
  }
  function removeMember(sheet, principal) {
    return api("/projects/" + encodeURIComponent(pageState.id) + "/members/" + encodeURIComponent(principal), { method: "DELETE" })
      .then(function (r) {
        if (!r.project) {
          closeOverlay(document.getElementById("pj-share-overlay"));
          closeOverlay(document.getElementById("pj-page-overlay"));
          loadList(true).catch(function () {});
          return;
        }
        afterMembers(sheet, r);
      })
      .catch(function (e) { toast(errText(e)); });
  }

  // ---------- «Часы по проектам»: встречи + задачи по неделям ----------

  // Цвета графика: проверены на различимость (в т.ч. при дальтонизме) на светлом и тёмном фоне.
  var HOURS_COLORS = ["#3b82e0", "#c2861f", "#8b5cf6", "#2f9a4a"];
  var HOURS_GRAY = "#7a7a80";
  var HOURS_BY_KEY = { blue: 0, orange: 1, purple: 2, green: 3 };
  var hoursState = { week: "", group: "project", people: null, projectId: null, data: null, seq: 0 };

  function fmtH(v) {
    v = Math.round(Number(v || 0) * 10) / 10;
    return String(v).replace(".", ",");
  }
  function diffHtml(total, prev) {
    var d = Math.round((Number(total || 0) - Number(prev || 0)) * 10) / 10;
    var cls = d >= 5 ? "pj-up" : d <= -5 ? "pj-down" : "";
    var txt = d > 0 ? "+" + fmtH(d) + " ▲" : d < 0 ? "−" + fmtH(-d) + " ▼" : "0";
    return '<span class="' + cls + '">' + txt + "</span>";
  }

  function openHours(opts) {
    opts = opts || {};
    hoursState = { week: "", group: "project", people: opts.people || null, projectId: opts.projectId || null, data: null, seq: 0, title: opts.title || "" };
    var ov = makeOverlay("pj-hours-overlay", "pj-hours-overlay");
    var sheet = ov.querySelector(".pj-sheet");
    sheet.classList.add("pj-sheet--page", "pj-sheet--hours");
    sheet.setAttribute("aria-label", "Часы по проектам");
    sheet.innerHTML = '<div class="pj-empty">Считаю часы…</div>';
    sheet.addEventListener("click", function (e) {
      var b = e.target.closest("[data-pj]");
      if (!b) return;
      var act = b.getAttribute("data-pj");
      if (act === "close") closeOverlay(ov);
      else if (act === "group") {
        hoursState.group = b.getAttribute("data-group");
        paintHours(ov);
      } else if (act === "week-prev" || act === "week-next") {
        var d = new Date((hoursState.data && hoursState.data.week) + "T12:00:00");
        d.setDate(d.getDate() + (act === "week-prev" ? -7 : 7));
        hoursState.week = d.toISOString().slice(0, 10);
        loadHours(ov);
      } else if (act === "week-now") {
        hoursState.week = "";
        loadHours(ov);
      } else if (act === "people") openPeopleMenu(ov, b);
      else if (act === "no-hours") {
        closeOverlay(ov);
        if (app().openMyTasks) app().openMyTasks();
      } else if (act === "open-project") {
        var id = b.getAttribute("data-id");
        if (id && /^\d+$/.test(id)) openPage(id);
      }
    });
    loadHours(ov);
    return ov;
  }

  function loadHours(ov) {
    var seq = ++hoursState.seq;
    var q = "?weeks=8" + (hoursState.week ? "&week=" + hoursState.week : "") +
      (hoursState.people ? "&people=" + encodeURIComponent(hoursState.people.join(",")) : "") +
      (hoursState.projectId ? "&project_id=" + encodeURIComponent(hoursState.projectId) : "");
    api("/metrics/project-hours" + q, { method: "GET", timeout: 30000 })
      .then(function (r) {
        if (seq !== hoursState.seq) return;
        hoursState.data = r;
        paintHours(ov);
      })
      .catch(function (e) {
        ov.querySelector(".pj-sheet").innerHTML =
          '<div class="pj-head"><h2 class="pj-title">Часы по проектам</h2><button type="button" class="pj-icon-btn" data-pj="close" aria-label="Закрыть">' + icon("close") + "</button></div>" +
          '<div class="pj-empty">' + esc(errText(e)) + "</div>";
      });
  }

  function openPeopleMenu(ov, anchor) {
    var d = hoursState.data;
    if (!d) return;
    var sel = hoursState.people;
    var items = [{ label: "Все", icon: !sel ? "check" : "", active: !sel, onClick: function () { hoursState.people = null; loadHours(ov); } }];
    (d.people || []).forEach(function (p) {
      var on = !!sel && sel.length === 1 && sel[0] === p.user_id;
      items.push({ label: p.name, icon: on ? "check" : "person", active: on, onClick: function () { hoursState.people = [p.user_id]; loadHours(ov); } });
    });
    sheetMenu("Чьи часы", items, anchor);
  }

  function hoursColors(d) {
    // цвет — за проектом (его цвет или порядок по id), не по месту в рейтинге; до 4 проектов, остальное серым
    var buckets = (d.projects || []).filter(function (p) { return /^\d+$/.test(p.bucket); });
    var totals = {};
    (d.weeks || []).forEach(function (w) { Object.keys(w.by || {}).forEach(function (b) { totals[b] = (totals[b] || 0) + w.by[b]; }); });
    var top = buckets.slice().sort(function (a, b) { return (totals[b.bucket] || 0) - (totals[a.bucket] || 0); }).slice(0, 4);
    top.sort(function (a, b) { return Number(a.bucket) - Number(b.bucket); });
    var used = {};
    var map = {};
    top.forEach(function (p) {
      var i = HOURS_BY_KEY[p.color];
      if (i != null && !used[i]) { map[p.bucket] = i; used[i] = 1; }
    });
    top.forEach(function (p) {
      if (map[p.bucket] != null) return;
      for (var i = 0; i < HOURS_COLORS.length; i++) if (!used[i]) { map[p.bucket] = i; used[i] = 1; return; }
    });
    var out = {};
    (d.projects || []).forEach(function (p) {
      out[p.bucket] = map[p.bucket] != null ? HOURS_COLORS[map[p.bucket]] : HOURS_GRAY;
    });
    return { color: out, top: top.map(function (p) { return p.bucket; }) };
  }

  function paintHours(ov) {
    var d = hoursState.data;
    var sheet = ov.querySelector(".pj-sheet");
    if (!d) return;
    var multi = (d.people || []).length > 1;
    var selName = !hoursState.people ? "Все" : ((d.people || []).filter(function (p) { return p.user_id === hoursState.people[0]; })[0] || {}).name || "Выбранные";
    var pal = hoursColors(d);
    var head =
      '<div class="pj-head"><div class="pj-head-txt"><span class="pj-muted pj-crumb">' + esc(hoursState.title ? "Проект «" + hoursState.title + "»" : "Показатели") + " › Часы по проектам</span>" +
      '<h2 class="pj-title">Часы по проектам</h2></div>' +
      '<button type="button" class="pj-icon-btn" data-pj="close" aria-label="Закрыть">' + icon("close") + "</button></div>" +
      '<p class="pj-muted pj-small">' + (multi ? "Видят сам человек и его руководитель. " : "") + "Это учёт, не рейтинг.</p>";
    var controls =
      '<div class="pj-tools">' +
      (hoursState.projectId ? "" :
        '<div class="pj-seg" role="tablist" aria-label="Как сгруппировать">' +
        ["project", "person"].map(function (g) {
          var on = hoursState.group === g;
          return '<button type="button" role="tab" aria-selected="' + on + '" class="pj-seg-btn' + (on ? " is-on" : "") + '" data-pj="group" data-group="' + g + '">' + (g === "project" ? "По проектам" : "По людям") + "</button>";
        }).join("") + "</div>") +
      '<div class="pj-week">' +
      '<button type="button" class="pj-icon-btn" data-pj="week-prev" aria-label="Предыдущая неделя">‹</button>' +
      '<button type="button" class="pj-btn" data-pj="week-now" title="К текущей неделе">Неделя ' + esc(d.week_label) + "</button>" +
      '<button type="button" class="pj-icon-btn" data-pj="week-next" aria-label="Следующая неделя">›</button></div>' +
      '<span class="pj-muted pj-small">сравнение с ' + esc(d.prev_label) + "</span>" +
      (multi ? '<button type="button" class="pj-btn" data-pj="people"><span class="pj-muted">Люди</span> ' + esc(selName) + " ▾</button>" : "") +
      '<span class="pj-grow"></span>' +
      (d.no_hours ? '<button type="button" class="pj-btn pj-btn--warn" data-pj="no-hours">' + d.no_hours + " " + plural(d.no_hours, "задача", "задачи", "задач") + " без часов ›</button>" : "") +
      "</div>";
    sheet.innerHTML = head + controls + hoursChartHtml(d, pal) + (hoursState.group === "person" && !hoursState.projectId ? personTableHtml(d) : projectTableHtml(d, pal)) +
      '<p class="pj-muted pj-small">Встречи — из календаря: только с другими участниками; к проекту — через встречу, серию, тему или саммари. ' +
      "Задачи — «сколько ушло» при сдаче, иначе время задачи в календаре. Если вещь в нескольких проектах, часы делятся поровну. «Без проекта» — то, что ни в один проект не попало.</p>";
  }

  function hoursChartHtml(d, pal) {
    var weeks = d.weeks || [];
    if (!weeks.length) return "";
    var order = pal.top.concat(["__rest"]);
    var max = 0;
    weeks.forEach(function (w) {
      var t = 0;
      Object.keys(w.by || {}).forEach(function (b) { t += w.by[b]; });
      w._total = t;
      if (t > max) max = t;
    });
    var step = max > 80 ? 40 : max > 40 ? 20 : max > 16 ? 10 : 4;
    var top = Math.max(step, Math.ceil(max / step) * step);
    var ticks = [];
    for (var v = top; v >= 0; v -= step) ticks.push(v);
    var legend = pal.top.map(function (b) {
      var p = (d.projects || []).filter(function (x) { return x.bucket === b; })[0] || {};
      return '<span class="pj-leg"><i style="background:' + pal.color[b] + '"></i>' + esc(p.title || "") + "</span>";
    }).join("") + '<span class="pj-leg"><i style="background:' + HOURS_GRAY + '"></i>' + (pal.top.length < (d.projects || []).length ? "Остальное и без проекта" : "Без проекта") + "</span>";
    var bars = weeks.map(function (w, i) {
      var segs = order.map(function (b) {
        var h = 0;
        if (b === "__rest") Object.keys(w.by || {}).forEach(function (k) { if (pal.top.indexOf(k) < 0) h += w.by[k]; });
        else h = (w.by || {})[b] || 0;
        if (!h) return "";
        return '<div class="pj-seg-bar" style="height:' + (h / top * 100).toFixed(2) + "%;background:" + (b === "__rest" ? HOURS_GRAY : pal.color[b]) + '"></div>';
      }).reverse().join("");
      var tip = w.label + ": " + fmtH(w._total) + " ч" + Object.keys(w.by || {}).filter(function (k) { return w.by[k]; }).map(function (k) {
        var p = (d.projects || []).filter(function (x) { return x.bucket === k; })[0] || {};
        return "\n" + (p.title || "") + " — " + fmtH(w.by[k]) + " ч";
      }).join("");
      var last = i === weeks.length - 1;
      return '<div class="pj-bar' + (last ? " is-cur" : "") + '" title="' + esc(tip) + '" role="img" aria-label="' + esc(tip.replace(/\n/g, "; ")) + '">' +
        '<span class="pj-bar-total" style="bottom:calc(' + (w._total / top * 100).toFixed(2) + '% + 4px)">' + (w._total ? fmtH(w._total) : "") + "</span>" + segs + "</div>";
    }).join("");
    var labels = weeks.map(function (w, i) {
      return '<span class="' + (i === weeks.length - 1 ? "is-cur" : "") + '">' + esc(w.label) + "</span>";
    }).join("");
    return (
      '<section class="pj-card pj-chart"><div class="pj-chart-head"><h3 class="pj-card-title">Часы по неделям</h3>' +
      '<span class="pj-muted pj-small">встречи + задачи, последние ' + weeks.length + " недель</span><span class=\"pj-grow\"></span>" +
      '<div class="pj-legend">' + legend + "</div></div>" +
      '<div class="pj-chart-body"><div class="pj-axis">' + ticks.map(function (t) { return "<span>" + t + (t ? " ч" : "") + "</span>"; }).join("") + "</div>" +
      '<div class="pj-bars" style="--pj-rows:' + (ticks.length - 1) + '">' + bars + "</div>" +
      "<span></span><div class=\"pj-bar-labels\">" + labels + "</div></div></section>"
    );
  }

  function projectTableHtml(d, pal) {
    if (!(d.rows || []).length) return '<div class="pj-empty">За эту неделю часов нет: ни встреч с другими участниками, ни задач со временем.</div>';
    var body = d.rows.map(function (r) {
      var openable = /^\d+$/.test(r.bucket) && !hoursState.projectId;
      var name = openable ? '<button type="button" class="pj-link pj-strong" data-pj="open-project" data-id="' + esc(r.bucket) + '">' + esc(r.title) + "</button>" : '<span class="pj-strong">' + esc(r.title) + "</span>";
      var head = '<tr class="pj-tr-group"><th scope="row"><i class="pj-dot" style="background:' + (pal.color[r.bucket] || HOURS_GRAY) + '"></i>' + name + "</th>" +
        "<td>" + fmtH(r.meet) + "</td><td>" + fmtH(r.task) + '</td><td class="pj-strong">' + fmtH(r.total) + '</td><td class="pj-muted">' + fmtH(r.prev) + "</td><td>" + diffHtml(r.total, r.prev) + "</td></tr>";
      var people = (r.people || []).length > 1 || (d.people || []).length > 1
        ? r.people.map(function (p) {
            return '<tr class="pj-tr-person"><th scope="row">' + esc(p.name) + "</th><td>" + fmtH(p.meet) + "</td><td>" + fmtH(p.task) + "</td><td>" + fmtH(p.total) +
              '</td><td class="pj-muted">' + fmtH(p.prev) + "</td><td>" + diffHtml(p.total, p.prev) + "</td></tr>";
          }).join("")
        : "";
      return head + people;
    }).join("");
    return hoursTable("Проект · человек", body, d);
  }

  function personTableHtml(d) {
    var xs = d.by_person || [];
    if (!xs.length) return '<div class="pj-empty">За эту неделю часов нет.</div>';
    var body = xs.map(function (p) {
      var head = '<tr class="pj-tr-group"><th scope="row"><span class="pj-strong">' + esc(p.name) + "</span></th><td>" + fmtH(p.meet) + "</td><td>" + fmtH(p.task) +
        '</td><td class="pj-strong">' + fmtH(p.total) + '</td><td class="pj-muted">' + fmtH(p.prev) + "</td><td>" + diffHtml(p.total, p.prev) + "</td></tr>";
      return head + p.projects.map(function (r) {
        return '<tr class="pj-tr-person"><th scope="row">' + esc(r.title) + "</th><td>" + fmtH(r.meet) + "</td><td>" + fmtH(r.task) + "</td><td>" + fmtH(r.total) +
          '</td><td class="pj-muted">' + fmtH(r.prev) + "</td><td>" + diffHtml(r.total, r.prev) + "</td></tr>";
      }).join("");
    }).join("");
    return hoursTable("Человек · проект", body, d);
  }

  function hoursTable(first, body, d) {
    return (
      '<section class="pj-card pj-table-card"><div class="pj-table-scroll"><table class="pj-table">' +
      "<caption>Неделя " + esc(d.week_label) + ' <span class="pj-muted">· рядом — ' + esc(d.prev_label) + "</span></caption>" +
      '<thead><tr><th scope="col">' + first + '</th><th scope="col">Встречи, ч</th><th scope="col">Задачи, ч</th><th scope="col">Итого, ч</th>' +
      '<th scope="col">Прошлая неделя</th><th scope="col">Разница</th></tr></thead><tbody>' + body + "</tbody></table></div></section>"
    );
  }

  // ---------- меню: «Задачи», «Проекты» (+ список в левой колонке), «Ещё» на телефоне ----------

  var TREE_KEY = "pj-tree-open";
  function treeOpen() {
    try {
      return localStorage.getItem(TREE_KEY) !== "0";
    } catch (_) {
      return true;
    }
  }
  function setTreeOpen(v) {
    try {
      localStorage.setItem(TREE_KEY, v ? "1" : "0");
    } catch (_) {}
  }
  function treeAllowed() {
    return isDesktop() && !document.documentElement.classList.contains("sidebar-collapsed");
  }

  function treeRow(label, opts) {
    opts = opts || {};
    var row = document.createElement("div");
    row.className = "sidebar-tree-row sidebar-tree-row--leaf" + (opts.action ? " sidebar-tree-row--action" : "") + (opts.active ? " is-active" : "");
    row.style.setProperty("--sidebar-depth", "1");
    var b = document.createElement("button");
    b.type = "button";
    b.className = "sidebar-tree-label" + (opts.action ? " sidebar-tree-label--action" : "");
    b.title = label;
    var sp = document.createElement("span");
    sp.className = "sidebar-tree-chevron-spacer";
    sp.setAttribute("aria-hidden", "true");
    b.appendChild(sp);
    var t = document.createElement("span");
    t.className = "sidebar-tree-label-text";
    t.textContent = label;
    b.appendChild(t);
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      opts.onClick();
    });
    row.appendChild(b);
    return row;
  }

  function renderTree() {
    var tree = document.getElementById("sidebar-tree-projects");
    var toggle = document.querySelector("[data-pj-toggle]");
    if (!tree) return;
    var open = treeAllowed() && treeOpen();
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    var btn = document.querySelector('.tabbar-btn[data-nav="projects"]');
    if (btn) btn.classList.toggle("is-tree-open", open);
    tree.classList.toggle("hidden", !open);
    tree.innerHTML = "";
    if (!open) return;
    var xs = activeProjects().slice(0, 12);
    var openId = document.getElementById("pj-page-overlay") ? pageState.id : null;
    xs.forEach(function (p) {
      tree.appendChild(treeRow(p.title, { active: String(p.id) === String(openId), onClick: function () { openPage(p.id); } }));
    });
    if (!xs.length && listCache) {
      tree.appendChild(treeRow("+ Новый проект", { action: true, onClick: function () { createProjectFlow(null); } }));
    }
    tree.appendChild(treeRow("Все проекты…", { action: true, onClick: openList }));
  }

  function setCls(el, cls, on) {
    // только если меняется — иначе наблюдатель за классами меню зациклится
    if (el && el.classList.contains(cls) !== !!on) el.classList.toggle(cls, !!on);
  }

  function markNav() {
    var actual = document.querySelector('.tabbar-btn[data-tab="actual"]');
    var tasksBtn = document.querySelector('.tabbar-btn[data-nav="tasks"]');
    var projBtn = document.querySelector('.tabbar-btn[data-nav="projects"]');
    var moreBtn = document.querySelector('.tabbar-btn[data-nav="more"]');
    var activeTab = document.querySelector(".tabbar-btn.active[data-tab]");
    var cur = activeTab ? activeTab.getAttribute("data-tab") : "";
    var mobile = !isDesktop();
    var pjOpen = mobile && !!document.querySelector(".pj-overlay");
    var tasksOn = !pjOpen && cur === "actual" && !!(app().isMyTasksOpen && app().isMyTasksOpen());
    var moreOn = !pjOpen && mobile && ["digest", "knowledge", "dashboards", "profile"].indexOf(cur) >= 0;
    document.querySelectorAll(".tabbar .tabbar-btn").forEach(function (x) {
      var dim = x === activeTab && (pjOpen || (tasksOn && x === actual));
      setCls(x, "nav-dim", dim);
    });
    setCls(tasksBtn, "nav-on", tasksOn);
    setCls(projBtn, "nav-on", pjOpen);
    setCls(moreBtn, "nav-on", moreOn);
    var notesLabel = document.querySelector('.tabbar-btn[data-tab="notes"] .tabbar-label');
    var want = mobile ? "Документы" : "Сохранённое";
    if (notesLabel && notesLabel.textContent !== want) notesLabel.textContent = want;
  }

  function openMoreSheet(anchor) {
    var items = [];
    var digestGroup = document.querySelector('[data-sidebar-group="digest"]');
    var digestOn = digestGroup && !digestGroup.hidden && !digestGroup.classList.contains("hidden");
    function go(tab) {
      return function () {
        var b = document.querySelector('.tabbar-btn[data-tab="' + tab + '"]');
        if (b) b.click();
        setTimeout(markNav, 30);
      };
    }
    if (digestOn) items.push({ label: "Дайджест", icon: "chat", onClick: go("digest") });
    items.push({ label: "База знаний", icon: "stack", onClick: go("knowledge") });
    items.push({ label: "Дашборды", icon: "chart", onClick: go("dashboards") });
    items.push({ label: "Профиль", icon: "person", onClick: go("profile") });
    sheetMenu("", items, anchor);
  }

  function bindNav() {
    var tasksBtn = document.querySelector('.tabbar-btn[data-nav="tasks"]');
    var projBtn = document.querySelector('.tabbar-btn[data-nav="projects"]');
    var moreBtn = document.querySelector('.tabbar-btn[data-nav="more"]');
    if (tasksBtn)
      tasksBtn.addEventListener("click", function () {
        if (app().openMyTasks) app().openMyTasks();
        setTimeout(markNav, 30);
      });
    if (projBtn)
      projBtn.addEventListener("click", function (e) {
        if (e.target.closest("[data-pj-toggle]")) {
          setTreeOpen(!treeOpen());
          renderTree();
          return;
        }
        openList();
      });
    if (moreBtn)
      moreBtn.addEventListener("click", function () {
        openMoreSheet(moreBtn);
      });
    document.querySelectorAll(".tabbar .tabbar-btn").forEach(function (b) {
      if (b.getAttribute("data-nav") === "projects") return;
      b.addEventListener("click", function () {
        if (isDesktop()) return;
        document.querySelectorAll(".pj-overlay").forEach(function (o) { closeOverlay(o); });
      });
    });
    var nav = document.querySelector(".tabbar");
    if (nav && window.MutationObserver) {
      new MutationObserver(function () { markNav(); }).observe(nav, { subtree: true, attributes: true, attributeFilter: ["class"] });
    }
    new MutationObserver(function () { renderTree(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    // «Мои задачи» открываются и закрываются внутри «Актуального» — подсветку пересчитываем после кликов
    document.addEventListener("click", function () { setTimeout(markNav, 60); }, true);
    window.addEventListener("resize", function () {
      markNav();
      renderTree();
    });
    window.addEventListener("focus", function () {
      loadList(true).catch(function () {});
    });
    onChange(renderTree);
    markNav();
    renderTree();
    loadList().catch(function () {});
  }

  // Ссылка на проект: …/webapp/?project=N (ответы Лео, брифы), #project=N, Telegram startapp=project_N.
  function projectIdFromHref(href) {
    try {
      var u = new URL(String(href || ""), location.href);
      if (u.host !== location.host || u.pathname.indexOf("/webapp") !== 0) return "";
      var n = u.searchParams.get("project") || "";
      return /^\d+$/.test(n) ? n : "";
    } catch (_) {
      return "";
    }
  }
  function projectIdFromLocation() {
    var n = projectIdFromHref(location.href);
    if (n) return n;
    var hm = String(location.hash || "").match(/(?:^#|&)project=(\d+)/);
    if (hm) return hm[1];
    var tg = window.Telegram && window.Telegram.WebApp;
    var start = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    var sm = start && String(start).match(/^project[_-]?(\d+)$/i);
    return sm ? sm[1] : "";
  }
  function bindLinks() {
    document.addEventListener(
      "click",
      function (e) {
        var a = e.target && e.target.closest && e.target.closest("a[href]");
        if (!a) return;
        var id = projectIdFromHref(a.getAttribute("href"));
        if (!id) return;
        e.preventDefault();
        e.stopPropagation();
        openPage(id);
      },
      true
    );
    var start = projectIdFromLocation();
    if (start) {
      setTimeout(function () { openPage(start); }, 600);
      try {
        var u = new URL(location.href);
        u.searchParams.delete("project");
        history.replaceState(history.state, "", u.pathname + (u.search ? u.search : "") + u.hash.replace(/(^#|&)project=\d+/, ""));
      } catch (_) {}
    }
  }

  function init() {
    if (!window.LeoApp || !window.LeoApp.apiFetch) {
      setTimeout(init, 200);
      return;
    }
    bindNav();
    bindLinks();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.LeoProjects = {
    openList: openList,
    openPage: openPage,
    openHours: openHours,
    loadList: loadList,
    list: function () { return listCache || []; },
    active: activeProjects,
    onChange: onChange,
    create: createProjectFlow,
    colorOf: colorOf,
  };
})();
