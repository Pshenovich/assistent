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
    var root = mount("ob-tour");
    if (!opts.modal) root.style.pointerEvents = "none";
    var hole = document.createElement("div");
    hole.className = "ob-hole";
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
      raf = requestAnimationFrame(loop);
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

  // ---------- запуск ----------

  async function start(opts) {
    if (running || !state) return;
    running = true;
    try {
      var cat = await loadCatalog();
      if (!state.intro_done_at) {
        await runIntro(cat);
        await send("intro_done");
      }
      if (!state.team_step_done_at) await runTeam(cat);
      if (!state.tour_done_at) await runTour(cat);
    } catch (e) {
      console.error("onboarding", e);
    } finally {
      running = false;
    }
  }

  /** Из app.js после /me. Новый пользователь проходит интро → команда → тур; по диплинку — в следующий раз. */
  function boot(me, opts) {
    if (!me || !me.onboarding) return;
    state = me.onboarding;
    bindReset();
    if (opts && opts.deepLink) return;
    if (!state.tour_done_at) start();
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
  };
  var pending = window.__leoOnboardingPending;
  if (pending) {
    window.__leoOnboardingPending = null;
    boot(pending.me, pending.opts);
  }
})();
