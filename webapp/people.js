/* Организация → «Люди»: показатели сотрудников для руководителя отдела и админов.
 * Строка на человека, по алфавиту, без рейтинга; по нажатию — та же карточка, что человек видит у себя.
 * Карточку рисует LeoMetrics.cardHtml (metrics.js), расчёт — assistant/services/people_metrics.py. */
(function () {
  "use strict";

  var state = { orgId: null, days: 28, group: 0, list: null, person: null, personData: null, loading: false, error: "" };

  function app() {
    return window.LeoApp || {};
  }
  function M() {
    return window.LeoMetrics || {};
  }
  function esc(s) {
    return M().esc ? M().esc(s) : String(s == null ? "" : s);
  }
  function root() {
    return document.getElementById("people-root");
  }
  function title(text) {
    var el = document.getElementById("profile-people-title");
    if (el) el.textContent = text;
  }

  function pct(v) {
    return v == null ? '<span class="people-none">мало данных</span>' : esc(v + "%");
  }
  function kpi(label, value) {
    return '<div class="metrics-kpi"><span class="metrics-kpi-label">' + esc(label) + '</span><span class="metrics-kpi-value' +
      (value == null ? " metrics-kpi-value--none" : "") + '">' + (value == null ? "мало данных" : esc(value)) + "</span></div>";
  }
  function signalHtml(sig) {
    var cls = sig.kind === "bad" ? " metrics-tag--bad" : sig.kind === "warn" ? " metrics-tag--warn" : "";
    return '<span class="metrics-tag' + cls + '">' + esc(sig.text) + "</span>";
  }

  function filtersHtml(d) {
    var html = M().segHtml(state.days);
    if ((d.departments || []).length > 1) {
      html += '<select class="pf-select people-group" id="people-group" aria-label="Отдел"><option value="0">Все отделы</option>' +
        d.departments.map(function (g) {
          return '<option value="' + g.id + '"' + (g.id === state.group ? " selected" : "") + ">" + esc(g.name) + "</option>";
        }).join("") + "</select>";
    }
    return '<div class="people-filters">' + html + "</div>";
  }

  function listHtml(d) {
    var s = d.summary;
    var html = filtersHtml(d);
    html += '<p class="metrics-lead">Каждый видит свою карточку с теми же цифрами. Без рейтинга: порядок — по алфавиту.' +
      "<small>В срок — с поправками: переносы постановщика, ожидание ответа на вопрос, короткие сроки и отпуск не засчитываются.</small></p>";
    html += '<div class="metrics-kpis">' +
      kpi("В срок, все", s.on_time_pct == null ? null : s.on_time_pct + "%") +
      kpi("С первого раза", s.first_time_pct == null ? null : s.first_time_pct + "%") +
      kpi("Просрочено сейчас", String(s.late_open)) +
      kpi("Висят дольше 10 дней", String(s.stale)) +
      "</div>";
    if (!d.people.length) {
      return html + '<p class="pf-hint">В отделе пока никого, кроме вас. Людей добавляют в «Организация → Отделы и команды».</p>';
    }
    var rows = d.people.map(function (p) {
      var react = p.reaction_hours == null ? "—" : M().hours(p.reaction_hours);
      var sub = (p.groups || []).join(", ");
      return '<button type="button" class="people-row" data-person="' + esc(p.user_id) + '">' +
        '<span class="people-who"><span class="people-name">' + esc(p.name) + "</span>" +
        '<span class="people-sub">' + esc((sub ? sub + " · " : "") + p.received + " " + M().plural(p.received, "поручение", "поручения", "поручений")) + "</span></span>" +
        '<span class="people-num" data-label="В срок">' + pct(p.on_time_pct) + (M().trend ? M().trend(p.trend.on_time_pct, false, "") : "") + "</span>" +
        '<span class="people-num" data-label="С 1-го раза">' + pct(p.first_time_pct) + "</span>" +
        '<span class="people-num" data-label="Реакция">' + esc(react) + "</span>" +
        '<span class="people-signals">' + (p.signals.length ? p.signals.map(signalHtml).join("") : '<span class="people-none">—</span>') + "</span>" +
        "</button>";
    }).join("");
    var head = '<div class="people-head" aria-hidden="true"><span>Сотрудник</span><span>В срок</span><span>С 1-го раза</span><span>Реакция</span><span>На что посмотреть</span></div>';
    return html + '<div class="people-table">' + head + rows + "</div>";
  }

  function personHtml(d) {
    return M().segHtml(state.days) + M().cardHtml(d, { self: false });
  }

  function render() {
    var el = root();
    if (!el) return;
    if (state.error) {
      el.innerHTML = '<p class="error">' + esc(state.error) + "</p>";
      return;
    }
    if (state.person) {
      el.innerHTML = state.personData ? personHtml(state.personData) : '<p class="pf-hint">Считаю…</p>';
      return;
    }
    el.innerHTML = state.list ? listHtml(state.list) : '<p class="pf-hint">Считаю…</p>';
  }

  async function load() {
    state.error = "";
    render();
    try {
      if (state.person) {
        state.personData = await app().apiFetch("/orgs/" + state.orgId + "/people/" + encodeURIComponent(state.person) + "?days=" + state.days, { method: "GET" });
        title(state.personData.name || "Человек");
      } else {
        state.list = await app().apiFetch("/orgs/" + state.orgId + "/people?days=" + state.days + "&group=" + state.group, { method: "GET" });
        title("Люди");
      }
    } catch (e) {
      state.error = (e && (e.detail || e.message)) || "Не получилось загрузить";
    }
    render();
  }

  function onClick(e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.days) {
      state.days = Number(b.dataset.days);
      if (state.person) state.personData = null;
      else state.list = null;
      load();
    } else if (b.dataset.person) {
      state.person = b.dataset.person;
      state.personData = null;
      load();
      var sc = root() && root().closest(".subscreen-body");
      if (sc) sc.scrollTop = 0;
    } else if (b.dataset.task) {
      if (app().openTaskById) app().openTaskById(Number(b.dataset.task));
    }
  }
  function onChange(e) {
    if (e.target.id !== "people-group") return;
    state.group = Number(e.target.value || 0);
    state.list = null;
    load();
  }

  function open(orgId) {
    state.orgId = orgId;
    state.person = null;
    state.personData = null;
    state.list = null;
    if (app().profileScreen) app().profileScreen("people");
    title("Люди");
    load();
  }

  function init() {
    if (!window.LeoApp) {
      setTimeout(init, 200);
      return;
    }
    var back = document.getElementById("profile-people-back");
    if (back) {
      back.addEventListener("click", function () {
        if (state.person) {
          state.person = null;
          state.personData = null;
          title("Люди");
          render();
          if (!state.list) load();
          return;
        }
        if (window.LeoOrg && window.LeoOrg.open) window.LeoOrg.open();
        else app().profileScreen("main");
      });
    }
    var el = root();
    if (el) {
      el.addEventListener("click", onClick);
      el.addEventListener("change", onChange);
    }
  }

  window.LeoPeople = { open: open };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
