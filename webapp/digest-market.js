/* «Дайджест» → «Рынок»: выпуск как журнал — оглавление и архив слева, главное, блок Лео
 * «Что это значит для вас» (один раз на выпуск, кэш на сервере) и материалы карточками по темам. */
(function () {
  "use strict";

  function app() {
    return window.LeoApp || {};
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function norm(s) {
    return String(s || "").toLowerCase().replace(/[^a-zа-яё0-9]+/gi, " ").trim();
  }

  var CLASS_MAP = {
    "strategy-list": "digest-market-strategy",
    "strategy-item": "digest-market-strategy-item",
    "strategy-trigger": "digest-market-strategy-trigger",
    "strategy-hint": "digest-market-strategy-hint",
    "strategy-tooltip": "digest-market-strategy-tip",
  };

  function remap(node) {
    [node].concat(Array.prototype.slice.call(node.querySelectorAll("*"))).forEach(function (el) {
      if (!el.classList) return;
      Object.keys(CLASS_MAP).forEach(function (k) {
        if (el.classList.contains(k)) {
          el.classList.remove(k);
          el.classList.add(CLASS_MAP[k]);
        }
      });
      if (el.tagName === "A") {
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
      }
    });
    return node;
  }

  function cardHtml(art) {
    var a = art.querySelector("h3 a") || art.querySelector("a");
    var title = (a ? a.textContent : (art.querySelector("h3") || {}).textContent || "").trim();
    var src = "";
    var m = /\s-\s([^-]+)$/.exec(title);
    if (m) {
      src = m[1].trim();
      title = title.slice(0, m.index).trim();
    }
    var meta = ((art.querySelector(".meta") || {}).textContent || "").split("·");
    var when = (meta.filter(function (x) { return /\d{2}\.\d{2}\.\d{4}/.test(x); })[0] || "").trim().slice(0, 10);
    var why = ((art.querySelector(".why") || {}).textContent || "").replace(/^Почему важно:\s*/i, "").trim();
    var desc = "";
    Array.prototype.forEach.call(art.querySelectorAll("p:not(.why)"), function (p) {
      var t = p.textContent.trim();
      if (t && norm(t).indexOf(norm(title).slice(0, 40)) < 0 && !desc) desc = t;
    });
    return (
      '<a class="mkt-card" href="' + esc(a ? a.getAttribute("href") : "#") + '" target="_blank" rel="noopener noreferrer">' +
      (src || when ? '<span class="mkt-card-src">' + esc([src, when].filter(Boolean).join(" · ")) + "</span>" : "") +
      '<b class="mkt-card-title">' + esc(title) + "</b>" +
      (desc ? '<span class="mkt-card-desc">' + esc(desc) + "</span>" : "") +
      (why ? '<span class="mkt-card-why">' + esc(why) + "</span>" : "") +
      "</a>"
    );
  }

  function parse(main) {
    var header = main.querySelector("header") || main;
    var h1 = (header.querySelector("h1") || {}).textContent || "Выпуск";
    var dm = /(\d{2})\.(\d{2})\.(\d{4})/.exec(h1) || /(\d{2})\.(\d{2})\.(\d{4})/.exec((header.querySelector(".meta") || {}).textContent || "");
    var issue = dm ? dm[3] + "-" + dm[2] + "-" + dm[1] : "";
    var sections = [];
    Array.prototype.forEach.call(main.querySelectorAll(".section"), function (sec, i) {
      var h2 = sec.querySelector("h2");
      var title = h2 ? h2.textContent.trim() : "Раздел";
      var arts = sec.querySelectorAll("article");
      var strat = sec.querySelector(".strategy-list");
      var html = "";
      if (strat) html = remap(strat.cloneNode(true)).outerHTML;
      else if (arts.length) html = '<div class="mkt-grid">' + Array.prototype.map.call(arts, cardHtml).join("") + "</div>";
      else {
        var c = sec.cloneNode(true);
        if (c.querySelector("h2")) c.querySelector("h2").remove();
        if (/watchlist/i.test(title)) {
          var names = c.textContent.split(/[,\n]/).map(function (x) { return x.trim(); }).filter(Boolean);
          html = '<div class="mkt-chips">' + names.map(function (n) { return '<span class="mkt-chip">' + esc(n) + "</span>"; }).join("") + "</div>";
        } else html = '<div class="mkt-prose">' + remap(c).innerHTML + "</div>";
      }
      sections.push({ id: "mkt-sec-" + i, title: title, html: html, text: sec.textContent.replace(/\s+/g, " ").trim() });
    });
    return {
      title: h1.trim(),
      issue: issue,
      eyebrow: ((header.querySelector(".eyebrow") || {}).textContent || "").trim(),
      lead: ((header.querySelector(".lead") || {}).textContent || "").trim(),
      meta: ((header.querySelector(".meta") || {}).textContent || "").trim(),
      sections: sections,
    };
  }

  function archiveDates(issue) {
    var base = issue ? new Date(issue + "T12:00:00") : new Date();
    var out = [];
    for (var i = 1; i <= 7; i++) {
      var d = new Date(base);
      d.setDate(base.getDate() - i);
      out.push(d.toISOString().slice(0, 10));
    }
    return out;
  }

  function fmt(iso) {
    var d = new Date(iso + "T12:00:00");
    return isNaN(d) ? iso : d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  }

  var current = { body: null, issue: "", archive: "" };

  function render(main, body, opts) {
    opts = opts || {};
    var p = parse(main);
    current.body = body;
    current.issue = p.issue;
    var toc = '<nav class="mkt-toc" aria-label="Оглавление выпуска"><small>Выпуск · ' + esc(fmt(p.issue)) + "</small>";
    toc += '<button type="button" class="mkt-toc-item is-on" data-mkt="go" data-id="mkt-top">Главное</button>';
    p.sections.forEach(function (s) {
      toc += '<button type="button" class="mkt-toc-item" data-mkt="go" data-id="' + s.id + '">' + esc(s.title) + "</button>";
    });
    toc += "<small>Прошлые выпуски</small>";
    if (opts.archive) toc += '<button type="button" class="mkt-toc-item" data-mkt="today">← Сегодняшний</button>';
    archiveDates(p.issue).forEach(function (d) {
      toc += '<button type="button" class="mkt-toc-item mkt-toc-item--arch" data-mkt="arch" data-date="' + d + '">' + esc(fmt(d)) + "</button>";
    });
    toc += "</nav>";
    var main2 = '<div class="mkt-body"><div class="mkt-hero" id="mkt-top">';
    if (p.eyebrow) main2 += '<div class="mkt-eyebrow">' + esc(p.eyebrow) + "</div>";
    main2 += '<h2 class="mkt-title">' + esc(p.title) + "</h2>";
    if (p.meta) main2 += '<div class="mkt-meta">' + esc(p.meta) + "</div>";
    if (p.lead) main2 += '<p class="mkt-lead">' + esc(p.lead) + "</p>";
    main2 += "</div>";
    main2 += '<div class="mkt-leo" data-mkt-leo><b>✨ Что это значит для вас</b><div class="mkt-leo-body">Лео читает выпуск и сверяет с вашими встречами…</div></div>';
    p.sections.forEach(function (s) {
      main2 += '<section class="mkt-sec" id="' + s.id + '"><h3 class="mkt-h">' + esc(s.title) + "</h3>" + s.html + "</section>";
    });
    main2 += "</div>";
    body.innerHTML = '<div class="mkt">' + toc + main2 + "</div>";
    watchSections(body);
    loadInsight(p, body);
  }

  // Меню следит за прокруткой: подсвечен раздел, который сейчас на экране.
  var observer = null;
  function watchSections(body) {
    if (observer) observer.disconnect();
    if (!("IntersectionObserver" in window)) return;
    var items = body.querySelectorAll('.mkt-toc-item[data-mkt="go"]');
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        items.forEach(function (x) {
          x.classList.toggle("is-on", x.getAttribute("data-id") === en.target.id);
        });
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    body.querySelectorAll("#mkt-top, .mkt-sec").forEach(function (sec) {
      observer.observe(sec);
    });
  }

  async function loadInsight(p, body, force) {
    var box = body.querySelector("[data-mkt-leo]");
    if (!box || !app().apiFetch) return;
    var text = p.sections.map(function (s) { return s.title + ": " + s.text; }).join("\n").slice(0, 8000);
    try {
      var r = await app().apiFetch("/digest/market/insight", {
        method: "POST",
        body: JSON.stringify({ issue: p.issue, text: p.title + "\n" + p.lead + "\n" + text, force: !!force }),
      });
      var lines = String(r.text || "").split(/\n+/).map(function (l) { return l.replace(/^[•\-\*]\s*/, "").trim(); }).filter(Boolean);
      box.querySelector(".mkt-leo-body").innerHTML =
        "<ul>" + lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul>" +
        '<div class="mkt-leo-acts"><button type="button" class="dgx-btn dgx-btn--leo" data-mkt="discuss">Обсудить с Лео</button>' +
        '<button type="button" class="dgx-btn" data-mkt="regen">Пересобрать</button></div>';
      box._insight = r.text;
      box._issue = p;
    } catch (e) {
      box.querySelector(".mkt-leo-body").innerHTML =
        '<span class="dgx-muted">' + esc((e && e.message) || "Не получилось") + '</span> <button type="button" class="dgx-btn" data-mkt="regen">Повторить</button>';
      box._issue = p;
    }
  }

  async function openArchive(date) {
    var body = current.body;
    if (!body) return;
    try {
      var res = await fetch("/news/archive/" + date + ".html", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) throw new Error("Выпуска за " + fmt(date) + " нет");
      var doc = new DOMParser().parseFromString(await res.text(), "text/html");
      var main = doc.querySelector("main");
      if (!main) throw new Error("Выпуск пустой");
      render(main, body, { archive: true });
      body.scrollTop = 0;
    } catch (e) {
      if (app().toast) app().toast((e && e.message) || "Не удалось открыть выпуск");
    }
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-mkt]");
    if (!b || !current.body || !current.body.contains(b)) return;
    var act = b.getAttribute("data-mkt");
    if (act === "go") {
      var el = document.getElementById(b.getAttribute("data-id"));
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      current.body.querySelectorAll(".mkt-toc-item").forEach(function (x) { x.classList.toggle("is-on", x === b); });
    } else if (act === "arch") openArchive(b.getAttribute("data-date"));
    else if (act === "today") {
      if (window.LeoApp && window.LeoApp.reloadMarket) window.LeoApp.reloadMarket();
    } else if (act === "discuss") {
      var box = current.body.querySelector("[data-mkt-leo]");
      app().askLeo("Обсудим отраслевой дайджест «" + ((box && box._issue && box._issue.title) || "выпуск") + "». Ты отметил для меня: " + ((box && box._insight) || "") + "\nЧто из этого стоит сделать на этой неделе?");
    } else if (act === "regen") {
      var bx = current.body.querySelector("[data-mkt-leo]");
      if (bx && bx._issue) {
        bx.querySelector(".mkt-leo-body").textContent = "Лео перечитывает выпуск…";
        loadInsight(bx._issue, current.body, true);
      }
    }
  });

  window.LeoMarket = { render: render };
})();
