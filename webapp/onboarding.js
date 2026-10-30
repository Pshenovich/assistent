/* Онбординг: Лео-комета, интро («Привет, я Лео!»), экран команды, микротур по «Актуальному».
 * Каталог шагов и функций — /onboarding/catalog (общий с Лео), состояние — в /me → onboarding.
 * Каждый Лео — свой встроенный SVG: через <use> CSS не достаёт до рук и глаз, и позы не работают. */
(function () {
  "use strict";

  var state = null;
  var catalogCache = null;
  var running = false;

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
  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_) {
      return false;
    }
  }
  function isDesktop() {
    try {
      return window.matchMedia("(min-width: 960px)").matches;
    } catch (_) {
      return false;
    }
  }
  function json(method, body) {
    return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  }

  // ---------- Лео ----------

  var LEO_SVG =
    '<g class="leo-bd">' +
    '<path class="leo-l leo-body" d="M50 88 C50 132 60 160 77 168 C72 148 70 132 72 118 Z"/>' +
    '<path class="leo-r leo-body" d="M150 88 C150 132 140 160 123 168 C128 148 130 132 128 118 Z"/>' +
    '<path class="leo-c leo-body" d="M72 118 C76 150 88 175 101 190 C113 175 124 150 128 118 Z"/>' +
    '<circle class="leo-rim" cx="100" cy="85" r="52"/>' +
    '<circle class="leo-body" cx="100" cy="76" r="52"/>' +
    '<g class="leo-eyes"><rect class="leo-eye" x="72" y="61" width="21" height="28" rx="9"/>' +
    '<rect class="leo-eye" x="107" y="61" width="21" height="28" rx="9"/></g>' +
    "</g>";

  /** HTML Лео. pose: fly | wave | point-up | point-right | point-left | point-down | stand. */
  function leoAvatar(pose, size, cls) {
    var s = size ? ' style="width:' + size + "px;height:" + size + 'px"' : "";
    return (
      '<span class="leo' + (cls ? " " + cls : "") + '"' + s + ' aria-hidden="true">' +
      '<svg class="leo-svg leo--' + (pose || "fly") + '" viewBox="0 0 200 200">' + LEO_SVG + "</svg></span>"
    );
  }
  function setPose(host, pose) {
    var svg = host && host.querySelector(".leo-svg");
    if (svg) svg.setAttribute("class", "leo-svg leo--" + (pose || "fly"));
  }

  /** Куда показывать рукой: от Лео к ближайшей точке элемента (у широкой панели центр далеко в стороне). */
  function pointDir(leoRect, target) {
    var cx = leoRect.left + leoRect.width / 2;
    var cy = leoRect.top + leoRect.height / 2;
    var dx = Math.max(target.left, Math.min(cx, target.right)) - cx;
    var dy = Math.max(target.top, Math.min(cy, target.bottom)) - cy;
    if (Math.abs(dy) >= Math.abs(dx) * 0.6) return dy < 0 ? "up" : "down";
    return dx < 0 ? "left" : "right";
  }

  // ---------- сервер ----------

  async function loadCatalog() {
    if (catalogCache) return catalogCache;
    catalogCache = await app().apiFetch("/onboarding/catalog", { method: "GET" });
    return catalogCache;
  }
  function send(kind, feature) {
    var p = app().apiFetch("/onboarding/event", json("POST", { kind: kind, feature: feature || "" }));
    return Promise.resolve(p)
      .then(function (r) {
        if (r && r.onboarding) state = r.onboarding;
        return state;
      })
      .catch(function () {
        return state;
      });
  }

  // ---------- слой ----------

  function mount(cls) {
    var el = document.createElement("div");
    el.className = "ob-root " + cls;
    document.body.appendChild(el);
    return el;
  }
  function unmount(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // ---------- 1. интро ----------

  function runIntro(cat) {
    return new Promise(function (resolve) {
      var intro = (cat && cat.intro) || {};
      var msg = intro.greeting || "Привет, я Лео! Теперь я на твоей орбите";
      var root = mount("ob-intro");
      root.innerHTML =
        '<div class="ob-intro-leo">' + leoAvatar("fly", 128) + "</div>" +
        '<div class="ob-intro-text" role="status"></div>' +
        '<div class="ob-intro-cta"><button type="button" class="ob-btn ob-btn--wide">' + esc(intro.button || "Привет, Лео") + "</button></div>";
      var box = root.querySelector(".ob-intro-leo");
      var text = root.querySelector(".ob-intro-text");
      var cta = root.querySelector(".ob-intro-cta");
      var raf = 0;
      var timers = [];
      var finished = false;
      var W, H, CX, CY, A, B, FX, FY;
      var LEO = 128, FLY = 4200, SET = 900, S_END = 0.72;

      function measure() {
        W = window.innerWidth;
        H = window.innerHeight;
        A = Math.min(W * 0.38, 240);
        B = A * 0.48;
        CX = W / 2;
        CY = H * 0.34;
        FX = W / 2;
        FY = Math.max(CY + B + 30, H * 0.42);
        text.style.top = FY + LEO / 2 + 18 + "px";
      }
      function place(x, y, s, rot) {
        box.style.transform = "translate(" + (x - LEO / 2) + "px," + (y - LEO / 2) + "px) rotate(" + rot + "deg) scale(" + s + ")";
      }
      function ease(k) {
        return k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      }
      // полтора круга: старт сверху, конец — в нижней точке орбиты
      function pos(k) {
        var a = -Math.PI / 2 + ease(k) * 3 * Math.PI;
        return [CX + A * Math.cos(a), CY + B * Math.sin(a)];
      }
      function finalFrame() {
        if (finished) return;
        finished = true;
        cancelAnimationFrame(raf);
        place(FX, FY, 1, 0);
        setPose(box, "wave");
        if (reducedMotion()) {
          text.textContent = msg;
          cta.classList.add("is-on");
          return;
        }
        var i = 0;
        (function type() {
          text.textContent = msg.slice(0, ++i);
          if (i < msg.length) timers.push(setTimeout(type, 38));
          else cta.classList.add("is-on");
        })();
      }
      function skip() {
        if (!finished) return finalFrame();
        timers.forEach(clearTimeout);
        text.textContent = msg;
        cta.classList.add("is-on");
      }

      measure();
      var t0 = performance.now();
      var lastRot = 0;
      function step(now) {
        var t = now - t0;
        if (t < FLY) {
          var k = t / FLY;
          var p = pos(k);
          var q = pos(Math.min(1, k + 0.004));
          if (q[0] !== p[0] || q[1] !== p[1]) lastRot = (Math.atan2(q[1] - p[1], q[0] - p[0]) * 180) / Math.PI + 90;
          place(p[0], p[1], 0.05 + (S_END - 0.05) * k * k, lastRot);
          raf = requestAnimationFrame(step);
        } else if (t < FLY + SET) {
          // из нижней точки — в центр, без разворота: нос плавно выпрямляется
          var e = 1 - Math.pow(1 - (t - FLY) / SET, 3);
          var r0 = ((lastRot + 540) % 360) - 180;
          var x0 = CX;
          var y0 = CY + B;
          place(x0 + (FX - x0) * e, y0 + (FY - y0) * e, S_END + (1 - S_END) * e, r0 * (1 - e));
          raf = requestAnimationFrame(step);
        } else {
          finalFrame();
        }
      }
      if (reducedMotion()) finalFrame();
      else raf = requestAnimationFrame(step);

      function onResize() {
        measure();
        if (finished) place(FX, FY, 1, 0);
      }
      window.addEventListener("resize", onResize);
      root.addEventListener("click", function (e) {
        if (e.target.closest(".ob-intro-cta button")) {
          window.removeEventListener("resize", onResize);
          timers.forEach(clearTimeout);
          root.style.opacity = "0";
          setTimeout(function () {
            unmount(root);
          }, 350);
          resolve();
          return;
        }
        skip();
      });
    });
  }

  // ---------- 2. команда ----------

  function suggestName(domains) {
    var d = String((domains || [])[0] || "").split(".")[0];
    return d ? d.charAt(0).toUpperCase() + d.slice(1) : "";
  }

  function runTeam(cat) {
    return new Promise(function (resolve) {
      var t = (cat && cat.team) || {};
      var root = mount("ob-team");
      var keys = [];
      var orgId = null;
      root.innerHTML =
        '<div class="ob-team-inner">' +
        '<span class="ob-team-leo">' + leoAvatar("fly", 72) + "</span>" +
        "<h2>" + esc(t.title || "С кем вы работаете?") + "</h2>" +
        '<p class="ob-team-sub">' + esc(t.text || "") + "</p>" +
        '<div class="ob-team-body"><p class="ob-team-sub">Загружаю…</p></div>' +
        '<div class="ob-team-err" role="alert"></div>' +
        '<div class="ob-team-foot"><button type="button" class="ob-btn ob-btn--wide" data-ob="next">Продолжить</button>' +
        '<button type="button" class="ob-link" data-ob="skip">Пока без команды</button></div>' +
        "</div>";
      var body = root.querySelector(".ob-team-body");
      var err = root.querySelector(".ob-team-err");
      var org = window.LeoOrg;

      function done() {
        unmount(root);
        resolve();
      }
      function renderChips() {
        var box = root.querySelector(".ob-chips");
        if (!box) return;
        box.querySelectorAll(".ob-chip").forEach(function (c) {
          c.remove();
        });
        var input = box.querySelector("input");
        keys.forEach(function (k, i) {
          var c = document.createElement("span");
          c.className = "ob-chip";
          c.innerHTML = esc(k) + '<button type="button" aria-label="Убрать" data-i="' + i + '">×</button>';
          box.insertBefore(c, input);
        });
      }
      // keepTail: последний кусок без разделителя ещё печатают — оставить в поле.
      function takeInput(keepTail) {
        var input = root.querySelector(".ob-chips input");
        if (!input) return;
        var parts = String(input.value || "").split(/[\s,;]+/);
        var tail = keepTail ? parts.pop() : "";
        parts
          .map(function (s) {
            return s.trim();
          })
          .filter(Boolean)
          .forEach(function (k) {
            if (keys.indexOf(k) < 0) keys.push(k);
          });
        input.value = tail || "";
        renderChips();
      }
      async function ensureOrg() {
        if (orgId) return orgId;
        var nameEl = root.querySelector("#ob-org-name");
        var name = String((nameEl && nameEl.value) || "").trim();
        if (!name) throw new Error("Назовите организацию");
        var d = await org.create(name, root.getAttribute("data-domain") || "");
        orgId = d && d.org && d.org.id;
        return orgId;
      }

      function renderForm(ov) {
        var doms = (ov && ov.my_domains) || [];
        if (doms[0]) root.setAttribute("data-domain", doms[0]);
        body.innerHTML =
          '<label class="ob-label" for="ob-org-name">Организация</label>' +
          '<input id="ob-org-name" class="ob-input" type="text" maxlength="80" autocomplete="organization" placeholder="Название компании или команды" value="' + esc(suggestName(doms)) + '" />' +
          '<label class="ob-label" for="ob-members-input">Участники</label>' +
          '<div class="ob-chips"><input id="ob-members-input" type="text" autocomplete="off" placeholder="@ник или почта" /></div>' +
          '<button type="button" class="ob-invite" data-ob="invite">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>' +
          "Скопировать ссылку-приглашение</button>";
        var input = root.querySelector("#ob-members-input");
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            takeInput(false);
          } else if (e.key === "Backspace" && !input.value && keys.length) {
            keys.pop();
            renderChips();
          }
        });
        input.addEventListener("input", function () {
          if (/[\s,;]/.test(input.value)) takeInput(true);
        });
        input.addEventListener("blur", function () {
          takeInput(false);
        });
      }

      function renderMember(detail) {
        var o = (detail && detail.org) || {};
        var names = ((detail && detail.members) || [])
          .filter(function (m) {
            return m.status === "active";
          })
          .slice(0, 6)
          .map(function (m) {
            return "<li>" + esc(m.name || m.username || m.invited_key || "Участник") + "</li>";
          })
          .join("");
        body.innerHTML = '<p class="ob-team-sub">Вы в команде «' + esc(o.name || "") + "»</p>" + (names ? '<ul class="ob-team-members">' + names + "</ul>" : "");
        root.querySelector('[data-ob="skip"]').hidden = true;
        orgId = o.id || null;
        root.setAttribute("data-member", "1");
      }

      root.addEventListener("click", async function (e) {
        var x = e.target.closest(".ob-chip button");
        if (x) {
          keys.splice(Number(x.getAttribute("data-i")), 1);
          renderChips();
          return;
        }
        var b = e.target.closest("[data-ob]");
        if (!b) return;
        var kind = b.getAttribute("data-ob");
        err.textContent = "";
        if (kind === "skip") {
          send("team_done");
          return done();
        }
        if (kind === "invite") {
          try {
            var id = await ensureOrg();
            var url = await org.createInvite(id);
            if (url && navigator.clipboard) await navigator.clipboard.writeText(url);
            if (app().toast) app().toast("Ссылка скопирована");
          } catch (ex) {
            err.textContent = (ex && (ex.detail || ex.message)) || "Не получилось";
          }
          return;
        }
        if (kind === "next") {
          if (root.getAttribute("data-member")) {
            send("team_done");
            return done();
          }
          takeInput(false);
          var nameEl = root.querySelector("#ob-org-name");
          if (!orgId && !String((nameEl && nameEl.value) || "").trim() && !keys.length) {
            send("team_done");
            return done();
          }
          b.disabled = true;
          try {
            var oid = await ensureOrg();
            var failed = [];
            for (var i = 0; i < keys.length; i++) {
              try {
                await org.addMember(oid, keys[i]);
              } catch (ex2) {
                failed.push(keys[i]);
              }
            }
            if (failed.length) {
              keys = failed;
              renderChips();
              err.textContent = "Не получилось добавить: " + failed.join(", ") + ". Проверьте ник или почту.";
              b.disabled = false;
              return;
            }
            if (app().toast) app().toast("Команда создана");
            if (org.reload) org.reload();
            send("used", "org");
            send("team_done");
            done();
          } catch (ex) {
            err.textContent = (ex && (ex.detail || ex.message)) || "Не получилось";
            b.disabled = false;
          }
        }
      });

      if (!org || !org.overview) {
        renderForm({});
        return;
      }
      org
        .overview()
        .then(function (ov) {
          if (ov && ov.current_id) {
            return org.detail(ov.current_id).then(renderMember, function () {
              renderMember({ org: (ov.orgs || [])[0] });
            });
          }
          renderForm(ov);
        })
        .catch(function () {
          renderForm({});
        });
    });
  }

  // ---------- 3. тур с подсветкой ----------

  function visibleTarget(anchor) {
    var list = document.querySelectorAll('[data-tour="' + anchor + '"]');
    for (var i = 0; i < list.length; i++) {
      var r = list[i].getBoundingClientRect();
      // offsetParent у position:fixed (кнопка «+») всегда null — смотрим на размер и видимость.
      if (r.width > 0 && r.height > 0 && getComputedStyle(list[i]).visibility !== "hidden") return list[i];
    }
    return null;
  }
  function waitFor(anchor, ms) {
    return new Promise(function (resolve) {
      var t0 = Date.now();
      (function tick() {
        var el = visibleTarget(anchor);
        if (el || Date.now() - t0 > (ms || 1500)) return resolve(el);
        requestAnimationFrame(tick);
      })();
    });
  }

  /**
   * Подсветка элемента с карточкой Лео. opts: {title, text, step, total, modal, actions:[{label, kind:'primary'|'link', onClick}]}
   * Возвращает {close, reposition}.
   */
  function spotlight(el, opts) {
    var root = mount("ob-tour" + (opts.cls ? " " + opts.cls : ""));
    if (!opts.modal) root.style.pointerEvents = "none";
    var hole = document.createElement("div");
    // подсказка — только рамка, без затемнения: человек продолжает работать
    hole.className = "ob-hole" + (opts.dim === false ? " is-ring" : "");
    var card = document.createElement("div");
    card.className = "ob-card has-leo";
    card.style.pointerEvents = "auto";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", opts.title || "Подсказка");
    var acts = (opts.actions || [])
      .map(function (a, i) {
        return '<button type="button" class="' + (a.kind === "primary" ? "ob-btn ob-btn--small" : "ob-link") + '" data-i="' + i + '">' + esc(a.label) + "</button>";
      })
      .join("");
    card.innerHTML =
      '<span class="ob-card-leo is-left">' + leoAvatar("point-up", 48) + "</span>" +
      '<div class="ob-card-head"><span class="ob-card-title">' + esc(opts.title || "") + "</span>" +
      (opts.total ? '<span class="ob-card-step">' + opts.step + " из " + opts.total + "</span>" : "") +
      "</div><div>" + esc(opts.text || "") + "</div>" +
      '<div class="ob-card-foot">' + acts + "</div>";
    root.appendChild(hole);
    root.appendChild(card);
    card.addEventListener("click", function (e) {
      var b = e.target.closest("[data-i]");
      if (!b) return;
      var a = opts.actions[Number(b.getAttribute("data-i"))];
      if (a && a.onClick) a.onClick();
    });
    var leoHost = card.querySelector(".ob-card-leo");
    var alive = true;

    function reposition() {
      if (!alive) return;
      var r = el.getBoundingClientRect();
      // Элемент пропал (сменили экран) — подсказка уходит вместе с ним.
      if (!opts.modal && (!document.body.contains(el) || !(r.width > 0 && r.height > 0))) {
        if (opts.onLost) opts.onLost();
        return;
      }
      var pad = 6;
      hole.style.left = r.left - pad + "px";
      hole.style.top = r.top - pad + "px";
      hole.style.width = r.width + pad * 2 + "px";
      hole.style.height = r.height + pad * 2 + "px";
      var vw = window.innerWidth;
      var vh = window.innerHeight;
      var cw = card.offsetWidth;
      var ch = card.offsetHeight;
      var gap = 58; // место под Лео между карточкой и элементом
      var below = r.bottom + gap;
      var top;
      if (below + ch <= vh - 12) top = below;
      else if (r.top - gap - ch >= 12) top = r.top - gap - ch;
      else top = Math.max(12 + gap, vh - ch - 12);
      var left = isDesktop() ? r.left + r.width / 2 - cw / 2 : (vw - cw) / 2;
      left = Math.max(10, Math.min(vw - cw - 10, left));
      card.style.left = left + "px";
      card.style.top = top + "px";
      // Лео — на краю карточки со стороны элемента, рукой к нему
      var toRight = r.left + r.width / 2 > left + cw / 2 + 20;
      leoHost.classList.toggle("is-right", toRight);
      leoHost.classList.toggle("is-left", !toRight);
      // элемент под карточкой — Лео свешивается с нижнего края, иначе сидит на верхнем
      leoHost.classList.toggle("is-bottom", r.top >= top + ch);
      setPose(leoHost, "point-" + pointDir(leoHost.getBoundingClientRect(), r));
    }
    var raf = 0;
    function loop() {
      reposition();
      if (alive) raf = requestAnimationFrame(loop);
    }
    try {
      el.scrollIntoView({ block: "nearest", inline: "nearest" });
    } catch (_) {}
    loop();
    return {
      close: function () {
        alive = false;
        cancelAnimationFrame(raf);
        unmount(root);
      },
      reposition: reposition,
    };
  }

  function setMenuOpen(anchor, open) {
    var btn = visibleTarget(anchor);
    if (!btn) return;
    var expanded = btn.getAttribute("aria-expanded") === "true";
    if (expanded !== open) btn.click();
  }

  async function runTour(cat) {
    var steps = (cat && cat.tour) || [];
    if (app().setTab) app().setTab("actual", { noAnim: true });
    await new Promise(function (r) {
      setTimeout(r, 250);
    });
    // Шаги собираем заранее: «1 из N» — только по тем, что реально видны (пункт меню — если видна кнопка меню).
    var shown = [];
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i];
      var el = await waitFor(s.open_menu || s.anchor, 800);
      if (el) shown.push({ s: s, el: el });
    }
    var total = shown.length;
    for (var j = 0; j < total; j++) {
      var cur = shown[j];
      if (cur.s.open_menu) setMenuOpen(cur.s.open_menu, true);
      var el2 = (await waitFor(cur.s.anchor, 1200)) || cur.el;
      var text = cur.s.text;
      if (cur.s.text_empty && !el2.querySelector(".focus-card")) text = cur.s.text_empty;
      var last = j === total - 1;
      var res = await new Promise(function (resolve) {
        var spot = spotlight(el2, {
          modal: true,
          title: cur.s.title,
          text: text,
          step: j + 1,
          total: total,
          actions: [
            { label: "Пропустить", kind: "link", onClick: function () { spot.close(); resolve("skip"); } },
            { label: last ? cur.s.done || "Готово" : "Дальше", kind: "primary", onClick: function () { spot.close(); resolve("next"); } },
          ],
        });
      });
      if (cur.s.open_menu) setMenuOpen(cur.s.open_menu, false);
      if (res === "skip") break;
    }
    await send("tour_done");
  }

  // ---------- «воспользовался» ----------

  function isUsed(id) {
    return !!(state && state.used && state.used[id]);
  }

  /** Функцией воспользовались: подсказка к ней больше не нужна. */
  function used(id) {
    if (!state || !id || isUsed(id)) return;
    state.used = state.used || {};
    state.used[id] = Date.now() / 1000;
    if (hint && hint.id === id) closeHint();
    dropMarker(id);
    send("used", id);
  }

  // Успешный запрос к серверу → какая функция освоена. body — то, что ушло на сервер.
  var API_USES = [
    ["POST", /^\/calendar\/tasks$/, "create_task"],
    ["POST", /^\/calendar\/events$/, "create_meeting"],
    ["POST", /^\/reminders$/, "create_reminder"],
    ["POST", /^\/voice\/command$/, "voice_command"],
    ["POST", /^\/gpt\/chat$/, "leo_chat"],
    ["POST", /^\/notes\/local$/, "notes"],
    ["POST", /\/ai-edit$/, "note_ai"],
    ["POST", /\/comments$/, "discussion"],
    ["POST", /\/(share|members|team-shares)$/, "share"],
    ["POST", /^\/chats$/, "chats"],
    ["POST", /^\/notes\/transcriptions\/upload$/, "transcriptions"],
    ["GET", /^\/notes\/search$/, "search"],
    ["POST", /^\/proactive\/(item|items|dismiss)$/, "focus_card"],
    ["POST", /^\/(notes\/knowledge|knowledge-bases)/, "knowledge"],
    ["POST", /^\/agents$/, "agents"],
    ["POST", /^\/trackers\/bitrix24\/webhook$/, "bitrix"],
  ];

  function onApi(path, opts, res) {
    if (!state) return;
    var method = String((opts && opts.method) || "GET").toUpperCase();
    var p = String(path || "").split("?")[0];
    if (method === "GET" && p === "/integrations" && res) {
      if (res.google) used("google");
      if (res.zoom || res.telemost) used("recordings");
      if (res.bitrix) used("bitrix");
      if (res.mail) used("mail");
      return;
    }
    if (method === "POST" && /\/sheets$/.test(p)) {
      var kind = "";
      try {
        kind = JSON.parse((opts && opts.body) || "{}").kind || "";
      } catch (_) {}
      if (kind === "board") used("board");
      if (kind === "web") used("presentation");
      return;
    }
    for (var i = 0; i < API_USES.length; i++) {
      var u = API_USES[i];
      if (u[0] === method && u[1].test(p)) used(u[2]);
    }
  }

  // Открыли экран/вкладку — этого достаточно, чтобы считать функцию освоенной.
  var CLICK_USES = {
    "my-tasks": "my_tasks",
    "posted-tasks": "posted_tasks",
    "task-inbox": "task_inbox",
    "posted-toggle": "posted_in_calendar",
    "calendar-view": "calendar_view",
    "notes-dictate": "dictate_note",
    "digest-chats": "digest_chats",
    "digest-mail": "digest_mail",
    "digest-market": "digest_market",
    "profile-proactive": "proactive",
    "profile-voice": "voice_settings",
    "profile-agents": "agents",
    "profile-org": "org",
    "chat-model": "chat_model",
    "chat-attach": "chat_attach",
    "chat-mic": "chat_mic",
    "chat-new": "chat_new",
    "knowledge-sources": "knowledge",
  };

  // ---------- подсказки к функциям ----------

  // Не больше одной карточки на заход в раздел (знакомство или подсказка) и не больше SESSION_CARDS за сессию.
  var SESSION_CARDS = 8;
  var MAX_MARKERS = 2; // и тихих меток с Лео на кнопках
  var cardsShown = 0;
  var screenNow = "";
  var screenCards = 0;
  var introWaits = 0;
  var sessionSnoozed = {};
  var hint = null; // {id, spot}
  var markers = {}; // id → {el, target}
  var scanTimer = 0;

  var guiding = false; // идёт «Показать» — обычные подсказки молчат
  var quietUntil = 0; // сразу после тура — дать осмотреться, без новых подсказок

  function hintsEnabled() {
    return !!(state && state.tour_done_at && !running && !guiding);
  }
  // Что-то открыто поверх (окно, меню, лист) — подсказки не показываем. Заметка и чат с Лео — не помеха:
  // это обычные экраны, подсказки показываются прямо в них.
  function busy() {
    if (document.querySelector(".ob-root:not(.ob-hint)")) return true;
    if (document.querySelector(".modal-overlay:not(.hidden):not(#note-editor-overlay), .nl-sheet-overlay, .leo-prompt-overlay")) return true;
    if (document.querySelector('[aria-expanded="true"][aria-haspopup="menu"], [aria-expanded="true"][aria-haspopup="listbox"]')) return true;
    return false;
  }
  function shown(id) {
    var el = document.getElementById(id);
    return !!(el && !el.classList.contains("hidden") && el.getBoundingClientRect().width > 0);
  }
  // Какие разделы сейчас перед глазами. На компьютере рядом могут быть открыты список, заметка и чат —
  // знакомства тогда идут по очереди: раздел → заметка → чат.
  function visibleScreens() {
    var out = [];
    var tab = document.querySelector(".tabbar-btn.active[data-tab]");
    var name = tab ? tab.getAttribute("data-tab") : "";
    var note = shown("note-editor-overlay");
    var chat = shown("note-discussion-overlay");
    var tabUnder = !(note || chat) || isDesktop(); // на телефоне заметка и чат закрывают раздел целиком
    if (tabUnder) {
      if (name === "notes") out.push("saved");
      else if (name === "profile") out.push(shown("profile-main") ? "profile" : "profile-sub");
      else if (name) out.push(name);
    }
    if (note) out.push("note");
    if (chat) out.push("leo_chat");
    return out;
  }
  // Ключ «захода» для лимита подсказок: самый верхний слой.
  function currentScreen() {
    var list = visibleScreens();
    return list[list.length - 1] || "";
  }
  // Элемент не закрыт другим слоем (у кнопки под открытой заметкой размер есть, но её не видно).
  function onTop(el) {
    var r = el.getBoundingClientRect();
    var x = Math.min(window.innerWidth - 1, Math.max(0, r.left + r.width / 2));
    var y = Math.min(window.innerHeight - 1, Math.max(0, r.top + Math.min(r.height / 2, 20)));
    var top = document.elementFromPoint(x, y);
    return !!top && (top === el || el.contains(top) || !!(top.closest && top.closest(".ob-root, .ob-marker")));
  }
  // Подсказки — только к кнопкам текущего раздела: в заметке — её кнопки, в чате — кнопки чата.
  var SCREEN_SCOPE = { note: "note-editor-overlay", leo_chat: "note-discussion-overlay" };
  function inScope(el, scr) {
    var scopeId = SCREEN_SCOPE[scr];
    if (scopeId) return !!el.closest("#" + scopeId);
    return !el.closest("#note-editor-overlay, #note-discussion-overlay");
  }
  function visibleOnTop(anchor, scr) {
    var el = visibleTarget(anchor);
    if (!el || !onTop(el)) return null;
    return scr && !inScope(el, scr) ? null : el;
  }
  function hintCandidates(scr) {
    var cat = catalogCache;
    if (!cat) return [];
    var seenAnchor = {};
    return (cat.features || []).filter(function (f) {
      if (!f.hint || isUsed(f.id) || sessionSnoozed[f.id]) return false;
      if (seenAnchor[f.anchor]) return false; // у «+» три функции — по одной за раз
      if (!visibleOnTop(f.anchor, scr)) return false;
      seenAnchor[f.anchor] = 1;
      return true;
    });
  }
  function shownCount(id) {
    return Number((state && state.hint_shown && state.hint_shown[id]) || 0);
  }

  function scheduleScan(ms) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, ms == null ? 1200 : ms);
  }

  async function scan() {
    if (!hintsEnabled()) return;
    try {
      await loadCatalog();
    } catch (_) {
      return;
    }
    Object.keys(markers).forEach(function (id) {
      if (isUsed(id) || !document.body.contains(markers[id].target) || !visibleTarget(markers[id].anchor)) dropMarker(id);
    });
    if (hint || busy()) return;
    var scr = currentScreen();
    if (scr !== screenNow) {
      screenNow = scr;
      screenCards = 0;
      introWaits = 0;
    }
    if (cardsShown >= SESSION_CARDS) return;
    // Первый заход в раздел — знакомство (без паузы после тура и без учёта освоенного).
    var intro = null;
    var shownScreens = visibleScreens();
    for (var k = 0; k < shownScreens.length && !(intro && intro.el); k++) intro = screenIntro(shownScreens[k]) || intro;
    if (intro && intro.el) return showScreenIntro(intro);
    if (intro) {
      // раздел открыт впервые, но его кнопки ещё грузятся — подождать их, а не подсказывать соседнее
      if (introWaits++ < 15) scheduleScan(700);
      return;
    }
    introWaits = 0;
    if (screenCards > 0 || Date.now() < quietUntil) return;
    var list = hintCandidates(scr);
    var fresh = list.filter(function (f) {
      return shownCount(f.id) < 3;
    });
    if (fresh.length) return showHint(fresh[0]);
    // Подсказку уже видели трижды — вместо карточки маленький Лео на кнопке.
    list
      .filter(function (f) {
        return shownCount(f.id) >= 3 && !markers[f.id];
      })
      .slice(0, Math.max(0, MAX_MARKERS - Object.keys(markers).length))
      .forEach(addMarker);
  }

  function showHint(f, opts) {
    var el = visibleTarget(f.anchor);
    if (!el) return;
    closeHint();
    if (!(opts && opts.fromMarker)) {
      cardsShown++;
      screenCards++;
      state.hint_shown = state.hint_shown || {};
      state.hint_shown[f.id] = shownCount(f.id) + 1;
      send("hint_shown", f.id);
    }
    dropMarker(f.id);
    var spot = spotlight(el, {
      dim: false,
      cls: "ob-hint",
      onLost: closeHint,
      title: f.title,
      text: f.hint,
      actions: [
        {
          label: "Позже",
          kind: "link",
          onClick: function () {
            sessionSnoozed[f.id] = 1;
            send("snooze", f.id);
            closeHint();
          },
        },
        {
          label: "Попробовать",
          kind: "primary",
          onClick: function () {
            closeHint();
            tryFeature(f);
          },
        },
      ],
    });
    hint = { id: f.id, spot: spot };
  }
  function screenIntro(scr) {
    var seen = (state && state.screens_seen) || {};
    var list = (catalogCache && catalogCache.screens) || [];
    for (var i = 0; i < list.length; i++) {
      var sc = list[i];
      if (sc.id !== scr || seen[sc.id]) continue;
      for (var j = 0; j < sc.anchors.length; j++) {
        var el = visibleOnTop(sc.anchors[j], scr);
        if (el) return { sc: sc, el: el };
      }
      return { sc: sc, el: null };
    }
    return null;
  }

  function showScreenIntro(it) {
    closeHint();
    cardsShown++;
    state.screens_seen = state.screens_seen || {};
    state.screens_seen[it.sc.id] = Date.now() / 1000;
    send("screen_seen", it.sc.id);
    var spot = spotlight(it.el, {
      dim: false,
      cls: "ob-hint",
      onLost: closeHint,
      title: it.sc.title,
      text: it.sc.text,
      actions: [{ label: "Понятно", kind: "primary", onClick: closeHint }],
    });
    hint = { id: "screen:" + it.sc.id, spot: spot };
  }

  function closeHint() {
    if (hint) hint.spot.close();
    hint = null;
  }

  // «Попробовать»: нажать подсвеченную кнопку; у пунктов меню «+» — открыть меню и подсветить пункт.
  async function tryFeature(f) {
    var el = visibleTarget(f.anchor);
    if (!el) return;
    el.click();
    if (f.menu_anchor) {
      var item = await waitFor(f.menu_anchor, 1200);
      if (item) item.focus();
    }
  }

  function addMarker(f) {
    var target = visibleTarget(f.anchor);
    if (!target) return;
    var m = document.createElement("button");
    m.type = "button";
    m.className = "ob-marker";
    m.setAttribute("aria-label", "Подсказка: " + f.title);
    m.innerHTML = leoAvatar("stand", 22);
    m.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      showHint(f, { fromMarker: true });
    });
    document.body.appendChild(m);
    markers[f.id] = { el: m, target: target, anchor: f.anchor };
    placeMarkers();
  }
  function dropMarker(id) {
    var m = markers[id];
    if (!m) return;
    unmount(m.el);
    delete markers[id];
  }
  function placeMarkers() {
    Object.keys(markers).forEach(function (id) {
      var m = markers[id];
      var r = m.target.getBoundingClientRect();
      var hidden = !(r.width > 0 && r.height > 0) || busy();
      m.el.style.display = hidden ? "none" : "";
      m.el.style.left = Math.min(window.innerWidth - 26, r.right - 12) + "px";
      m.el.style.top = Math.max(2, r.top - 16) + "px";
    });
    if (Object.keys(markers).length) requestAnimationFrame(placeMarkers);
  }

  // Экран мог смениться и без клика (диплинк, медленная загрузка заметки) — тихая проверка раз в пару секунд.
  setInterval(function () {
    if (!document.hidden && !hint && hintsEnabled()) scan();
  }, 2500);

  document.addEventListener(
    "click",
    function (e) {
      var t = e.target && e.target.closest && e.target.closest("[data-tour]");
      if (t && CLICK_USES[t.getAttribute("data-tour")]) used(CLICK_USES[t.getAttribute("data-tour")]);
      // Сменился экран, открылась заметка, закрылось окно — посмотреть, что подсказать.
      if (!(e.target && e.target.closest && e.target.closest(".ob-root, .ob-marker"))) scheduleScan();
    },
    true
  );

  // ---------- «Показать» (из Лео, из Профиля, по ссылке ?guide=) ----------

  async function openRoute(f) {
    var r = f.route || {};
    if (r.needs === "chat") {
      // чат с Лео: «+» на Актуальном → «Лео»
      if (app().setTab) app().setTab("actual", { noAnim: true });
      setMenuOpen("create", true);
      var item = await waitFor("create-leo", 1200);
      if (item) item.click();
      await new Promise(function (res) {
        setTimeout(res, 700);
      });
      return;
    }
    if (r.tab && app().setTab) app().setTab(r.tab, { noAnim: true });
    if (r.tab === "profile" && app().profileScreen) app().profileScreen("main");
    if (r.subtab) {
      var sub = document.querySelector('#panel-notes .subtab-btn[data-subtab="' + r.subtab + '"]');
      if (sub) sub.click();
    }
    await new Promise(function (res) {
      setTimeout(res, 350);
    });
  }

  async function show(id) {
    var cat = await loadCatalog();
    var f = (cat.features || []).filter(function (x) {
      return x.id === id;
    })[0];
    if (!f) return;
    closeHint();
    guiding = true;
    var el = visibleTarget(f.anchor);
    if (!el) {
      await openRoute(f);
      el = await waitFor(f.anchor, 1500);
    }
    var text = f.hint || f.summary;
    if (!el && f.route && f.route.needs === "note") {
      // Кнопка живёт внутри заметки: показать, где её создать или открыть.
      el = await waitFor("notes-create", 800);
      text = "Откройте любую заметку — " + f.where.split("→").slice(-2).join("→").trim() + ". Или создайте новую здесь.";
    }
    if (!el) {
      guiding = false;
      if (app().toast) app().toast(f.title + ": " + f.where);
      return;
    }
    closeHint();
    if (f.open_menu && f.menu_anchor) {
      setMenuOpen(f.open_menu, true);
      el = (await waitFor(f.menu_anchor, 1200)) || el;
    }
    var spot = spotlight(el, {
      modal: true,
      title: f.title,
      text: text,
      actions: [
        {
          label: "Понятно",
          kind: "primary",
          onClick: function () {
            spot.close();
            guiding = false;
            if (f.open_menu) setMenuOpen(f.open_menu, false);
          },
        },
      ],
    });
  }

  // ---------- Профиль → «Как пользоваться Орбитой» ----------

  async function renderHowto() {
    var root = document.getElementById("howto-root");
    if (!root) return;
    var cat;
    try {
      cat = await loadCatalog();
    } catch (_) {
      root.innerHTML = '<p class="pf-hint">Не получилось загрузить список</p>';
      return;
    }
    var feats = (cat.features || []).filter(function (f) {
      return f.hint;
    });
    var done = feats.filter(function (f) {
      return isUsed(f.id);
    }).length;
    var html =
      '<div class="pf-list"><div class="pf-row pf-row--static"><span class="pf-row-body"><span class="pf-row-text">' +
      '<span class="pf-row-title">Освоено ' + done + " из " + feats.length + "</span>" +
      '<span class="pf-row-sub">Спросите Лео «где…» или «как…» — он объяснит и покажет</span></span>' +
      '<button type="button" class="pf-link" data-howto-tour>Пройти тур</button></span></div></div>';
    var sections = [];
    feats.forEach(function (f) {
      if (sections.indexOf(f.section) < 0) sections.push(f.section);
    });
    sections.forEach(function (sec) {
      html += '<div class="pf-group"><p class="pf-group-title">' + esc(sec) + '</p><div class="pf-list">';
      feats
        .filter(function (f) {
          return f.section === sec;
        })
        .forEach(function (f) {
          var ok = isUsed(f.id);
          html +=
            '<div class="pf-row pf-row--static ob-howto-row' + (ok ? " is-done" : "") + '">' +
            '<span class="ob-howto-mark" aria-label="' + (ok ? "Освоено" : "Ещё не пробовали") + '">' + (ok ? "✓" : "") + "</span>" +
            '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(f.title) + "</span>" +
            '<span class="pf-row-sub">' + esc(f.summary) + "</span></span>" +
            '<button type="button" class="pf-link" data-howto-show="' + esc(f.id) + '">Показать</button></span></div>';
        });
      html += "</div></div>";
    });
    root.innerHTML = html;
    var hintEl = document.getElementById("profile-howto-hint");
    if (hintEl) hintEl.textContent = "Освоено " + done + " из " + feats.length + " · Лео покажет каждую";
  }

  function bindHowto() {
    var open = document.getElementById("profile-howto-open");
    if (!open || open.dataset.bound) return;
    open.dataset.bound = "1";
    open.addEventListener("click", function () {
      if (app().profileScreen) app().profileScreen("howto");
      renderHowto();
    });
    var back = document.getElementById("profile-howto-back");
    if (back)
      back.addEventListener("click", function () {
        if (app().profileScreen) app().profileScreen("main");
      });
    var root = document.getElementById("howto-root");
    if (root)
      root.addEventListener("click", function (e) {
        var b = e.target.closest("[data-howto-show]");
        if (b) return show(b.getAttribute("data-howto-show"));
        if (e.target.closest("[data-howto-tour]")) {
          state.tour_done_at = null;
          start();
        }
      });
  }

  // ---------- запуск ----------

  async function start() {
    if (running || !state) return;
    running = true;
    try {
      var cat = await loadCatalog();
      if (!state.intro_done_at) {
        await runIntro(cat);
        await send("intro_done");
      }
      if (!state.team_step_done_at) await runTeam(cat);
      if (!state.tour_done_at) {
        await runTour(cat);
        quietUntil = Date.now() + 45000;
      }
    } catch (e) {
      console.error("onboarding", e);
    } finally {
      running = false;
    }
    scheduleScan(2000);
  }

  /** Из app.js после /me. Новый пользователь проходит интро → команда → тур; по диплинку — в следующий раз. */
  function boot(me, opts) {
    if (!me || !me.onboarding) return;
    state = me.onboarding;
    bindReset();
    bindHowto();
    // ?guide=<id> — «Показать в приложении» из ответа Лео в Telegram.
    var guide = takeGuideParam();
    if (guide) {
      setTimeout(function () {
        show(guide);
      }, 900);
      return;
    }
    if (opts && opts.deepLink) return;
    if (!state.tour_done_at) start();
    else scheduleScan(2500);
  }

  function takeGuideParam() {
    try {
      var u = new URL(location.href);
      var g = u.searchParams.get("guide");
      if (!g) return "";
      u.searchParams.delete("guide");
      history.replaceState(history.state, "", u.pathname + u.search + u.hash);
      return g;
    } catch (_) {
      return "";
    }
  }

  /** Состояние пришло с другого устройства (prefs.changed). */
  function applyState(st) {
    if (st && typeof st === "object") state = st;
  }

  // Профиль → «Пройти онбординг заново»: только тем, кому разрешён сброс.
  function bindReset() {
    var wrap = document.getElementById("profile-onboarding-reset-wrap");
    var btn = document.getElementById("profile-onboarding-reset");
    if (!wrap || !btn) return;
    wrap.classList.toggle("hidden", !(state && state.can_reset));
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async function () {
      var ok = app().confirm ? await app().confirm("Сбросить онбординг и пройти его с самого начала?") : true;
      if (!ok) return;
      try {
        await reset();
      } catch (e) {
        if (app().toast) app().toast((e && (e.detail || e.message)) || "Не получилось");
      }
    });
  }

  async function reset() {
    var r = await app().apiFetch("/onboarding/reset", { method: "POST" });
    state = (r && r.onboarding) || state;
    start();
  }

  window.LeoOnboarding = {
    boot: boot,
    applyState: applyState,
    reset: reset,
    state: function () {
      return state;
    },
    leoAvatar: leoAvatar,
    pointDir: pointDir,
    spotlight: spotlight,
    onApi: onApi,
    used: used,
    // для отладки в консоли: почему подсказка не показалась
    debug: function () {
      return { running: running, guiding: guiding, hint: hint && hint.id, screen: currentScreen(), screenNow: screenNow,
        screenCards: screenCards, cardsShown: cardsShown, quietLeftMs: Math.max(0, quietUntil - Date.now()), busy: busy() };
    },
    show: show,
    scan: scan,
  };
  var pending = window.__leoOnboardingPending;
  if (pending) {
    window.__leoOnboardingPending = null;
    boot(pending.me, pending.opts);
  }
})();
