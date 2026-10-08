(function () {
  var MONTHS = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
  ];
  var WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  var WEEKDAYS_FULL = [
    "воскресенье",
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
  ];
  var DURATIONS = [15, 30, 45, 60];

  var token = "";
  var meta = null;
  var days = [];
  var slotsByDay = {};
  var selectedDay = "";
  var selectedStart = "";
  var selectedEnd = "";
  var selectedDuration = 30;
  var loadingBook = false;
  var prefetchGen = 0;

  function $(id) {
    return document.getElementById(id);
  }

  function tokenFromPath() {
    var parts = String(location.pathname || "").split("/").filter(Boolean);
    var i = parts.indexOf("book");
    if (i < 0 || !parts[i + 1]) return "";
    return decodeURIComponent(parts[i + 1]);
  }

  function wallTime(iso) {
    var m = String(iso || "").match(/T(\d{2}:\d{2})/);
    return m ? m[1] : "";
  }

  function parseDay(dateIso) {
    var parts = String(dateIso || "").split("-");
    if (parts.length !== 3) return null;
    var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return isNaN(d.getTime()) ? null : d;
  }

  function todayIso() {
    var n = new Date();
    var m = String(n.getMonth() + 1).padStart(2, "0");
    var d = String(n.getDate()).padStart(2, "0");
    return n.getFullYear() + "-" + m + "-" + d;
  }

  function addDaysIso(iso, daysCount) {
    var d = parseDay(iso);
    if (!d) return iso;
    d.setDate(d.getDate() + daysCount);
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }

  function isWeekendIso(dateIso) {
    var d = parseDay(dateIso);
    return d ? d.getDay() === 0 || d.getDay() === 6 : false;
  }

  function relativeDay(dateIso) {
    var today = todayIso();
    if (dateIso === today) return "сегодня";
    if (dateIso === addDaysIso(today, 1)) return "завтра";
    return "";
  }

  function formatDayHeading(dateIso) {
    var d = parseDay(dateIso);
    if (!d) return dateIso;
    var rel = relativeDay(dateIso);
    var core = WEEKDAYS_FULL[d.getDay()] + ", " + d.getDate() + " " + MONTHS[d.getMonth()];
    return rel ? rel + " · " + core : core;
  }

  function parseEmails(raw) {
    return String(raw || "")
      .split(/[,;]+/)
      .map(function (s) {
        return s.trim().toLowerCase();
      })
      .filter(Boolean);
  }

  function emailsValid(list) {
    if (!list.length) return false;
    return list.every(function (em) {
      return em.indexOf("@") > 0 && em.indexOf(" ") < 0;
    });
  }

  function detailMessage(body, fallback) {
    var d = body && (body.detail != null ? body.detail : body.message);
    if (typeof d === "string" && d.trim()) return d.trim();
    if (d && typeof d === "object") {
      if (typeof d.message === "string" && d.message.trim()) return d.message.trim();
      if (typeof d.reason === "string") {
        if (d.reason === "expired") return "Срок действия ссылки истёк";
        if (d.reason === "used") return "Ссылка уже использована";
        if (d.reason === "slot_taken") return "Этот слот уже занят";
      }
    }
    return fallback || "Не удалось загрузить слоты";
  }

  function setStatus(text, isError) {
    var status = $("book-status");
    var err = $("book-error");
    if (status) {
      status.textContent = text || "";
      status.classList.toggle("hidden", !text || !!isError);
    }
    if (err) {
      err.textContent = isError ? text || "" : "";
      err.classList.toggle("hidden", !isError);
    }
  }

  function showForm(on) {
    var form = $("book-form");
    var dock = $("book-dock");
    if (form) form.classList.toggle("hidden", !on);
    if (dock) dock.classList.toggle("hidden", !on);
  }

  async function api(path, opts) {
    var res = await fetch(path, Object.assign({ headers: { Accept: "application/json" } }, opts || {}));
    var text = await res.text();
    var body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch (_) {
      body = { detail: text };
    }
    if (!res.ok) {
      var err = new Error(detailMessage(body, res.statusText));
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  function readEmbeddedMeta() {
    var el = $("book-meta");
    if (!el) return null;
    try {
      var data = JSON.parse(el.textContent || "{}");
      return data && data.ok ? data : null;
    } catch (_) {
      return null;
    }
  }

  function paintMeta() {
    var title = String((meta && meta.title) || "").trim();
    var titleEl = $("book-title");
    if (titleEl) titleEl.textContent = title || "Выберите время";
    document.title = title || "Leo — свободные слоты";
    var lead = $("book-lead");
    if (lead) {
      var tz = String((meta && meta.timezone) || "").trim();
      lead.textContent = tz ? "Время указано в часовом поясе " + tz : "Свободные слоты на ближайшие дни";
    }
    if (meta && meta.effective_duration_min) {
      selectedDuration = Number(meta.effective_duration_min) || selectedDuration;
    } else if (meta && meta.duration_min) {
      selectedDuration = Number(meta.duration_min) || selectedDuration;
    }
    days = (meta && meta.days) || [];
    renderDuration();
    if (!selectedDay) {
      var first = days.find(function (row) {
        return row && !row.weekend;
      });
      selectedDay = first ? String(first.date) : "";
    }
    renderDates();
  }

  function renderDuration() {
    var host = $("book-duration");
    var label = $("book-duration-label");
    if (!host) return;
    var fixed = meta && meta.duration_min;
    if (fixed) {
      host.classList.add("hidden");
      if (label) label.classList.add("hidden");
      selectedDuration = Number(fixed);
      return;
    }
    host.classList.remove("hidden");
    if (label) label.classList.remove("hidden");
    host.querySelectorAll("[data-dur]").forEach(function (btn) {
      var n = Number(btn.getAttribute("data-dur"));
      btn.classList.toggle("is-active", n === selectedDuration);
    });
  }

  function bindDuration() {
    var host = $("book-duration");
    if (!host) return;
    host.querySelectorAll("[data-dur]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var n = Number(btn.getAttribute("data-dur"));
        if (!DURATIONS.includes(n) || selectedDuration === n) return;
        selectedDuration = n;
        selectedStart = "";
        selectedEnd = "";
        slotsByDay = {};
        prefetchGen += 1;
        renderDuration();
        loadDaySlots(selectedDay, { prefer: true }).then(prefetchRest);
        syncDock();
      });
    });
  }

  function renderDates() {
    var host = $("book-dates");
    if (!host) return;
    host.innerHTML = "";
    if (!days.length) {
      for (var i = 0; i < 6; i++) {
        var sk = document.createElement("div");
        sk.className = "date-chip sk";
        host.appendChild(sk);
      }
      return;
    }
    days.forEach(function (row) {
      var day = String(row.date || "");
      var d = parseDay(day);
      var weekend = !!row.weekend;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "date-chip";
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", day === selectedDay ? "true" : "false");
      if (day === selectedDay) btn.classList.add("is-active");
      if (weekend) btn.classList.add("is-weekend");
      var rec = slotsByDay[day];
      if (rec && rec.status === "ready" && !rec.slots.length && !weekend) {
        btn.classList.add("is-empty");
      }
      var wd = document.createElement("span");
      wd.className = "date-chip-wd";
      wd.textContent = d ? WEEKDAYS[d.getDay()] : "";
      var num = document.createElement("span");
      num.className = "date-chip-num";
      num.textContent = d ? String(d.getDate()) : day;
      var rel = document.createElement("span");
      rel.className = "date-chip-rel";
      rel.textContent = relativeDay(day) || (d ? MONTHS[d.getMonth()].slice(0, 3) : "");
      btn.appendChild(wd);
      btn.appendChild(num);
      btn.appendChild(rel);
      btn.addEventListener("click", function () {
        if (weekend || selectedDay === day) return;
        selectedDay = day;
        selectedStart = "";
        selectedEnd = "";
        renderDates();
        renderDaySlots();
        loadDaySlots(day, { prefer: true });
        syncDock();
      });
      host.appendChild(btn);
    });
  }

  function renderSlotSkeleton() {
    var host = $("book-slots");
    var empty = $("book-empty");
    if (empty) empty.classList.add("hidden");
    if (!host) return;
    host.innerHTML = "";
    for (var i = 0; i < 9; i++) {
      var sk = document.createElement("div");
      sk.className = "slot sk";
      host.appendChild(sk);
    }
  }

  function renderDaySlots() {
    var host = $("book-slots");
    var empty = $("book-empty");
    var heading = $("book-day-heading");
    if (!host) return;
    if (heading) heading.textContent = selectedDay ? formatDayHeading(selectedDay) : "";
    var weekend = isWeekendIso(selectedDay);
    if (weekend) {
      host.innerHTML = "";
      if (empty) {
        empty.textContent = "Выходной — слоты не предлагаются";
        empty.classList.remove("hidden");
      }
      return;
    }
    var rec = slotsByDay[selectedDay];
    if (!rec || rec.status === "loading") {
      renderSlotSkeleton();
      return;
    }
    var slots = rec.slots || [];
    host.innerHTML = "";
    if (empty) {
      empty.textContent = "В этот день свободных слотов нет";
      empty.classList.toggle("hidden", !!slots.length);
    }
    slots.forEach(function (slot) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slot";
      btn.textContent = wallTime(slot.start);
      if (slot.start === selectedStart) btn.classList.add("is-active");
      btn.addEventListener("click", function () {
        selectedStart = slot.start;
        selectedEnd = slot.end || "";
        host.querySelectorAll(".slot").forEach(function (el) {
          el.classList.toggle("is-active", el === btn);
        });
        syncDock();
      });
      host.appendChild(btn);
    });
  }

  function dayQuery(day) {
    return (
      "/api/public/book/" +
      encodeURIComponent(token) +
      "/slots?date=" +
      encodeURIComponent(day) +
      "&duration_min=" +
      encodeURIComponent(String(selectedDuration))
    );
  }

  async function loadDaySlots(day, opts) {
    opts = opts || {};
    if (!token || !day || isWeekendIso(day)) {
      renderDaySlots();
      return;
    }
    var rec = slotsByDay[day];
    if (rec && rec.status === "ready" && rec.duration === selectedDuration) {
      if (opts.prefer || day === selectedDay) renderDaySlots();
      return rec;
    }
    if (rec && rec.status === "loading" && rec.promise) {
      if (day === selectedDay) renderDaySlots();
      return rec.promise;
    }
    var pending = { status: "loading", slots: [], duration: selectedDuration, promise: null };
    slotsByDay[day] = pending;
    if (day === selectedDay) renderDaySlots();
    pending.promise = api(dayQuery(day))
      .then(function (data) {
        if (slotsByDay[day] !== pending) return pending;
        pending.status = "ready";
        pending.slots = (data && data.slots) || [];
        pending.promise = null;
        if (day === selectedDay) {
          renderDates();
          renderDaySlots();
        } else {
          renderDates();
        }
        return pending;
      })
      .catch(function () {
        if (slotsByDay[day] !== pending) return pending;
        pending.status = "ready";
        pending.slots = [];
        pending.promise = null;
        if (day === selectedDay) {
          renderDates();
          renderDaySlots();
        }
        return pending;
      });
    return pending.promise;
  }

  async function prefetchRest() {
    var gen = prefetchGen;
    var rest = days
      .map(function (row) {
        return String(row.date || "");
      })
      .filter(function (day) {
        return day && day !== selectedDay && !isWeekendIso(day);
      });
    for (var i = 0; i < rest.length; i++) {
      if (gen !== prefetchGen) return;
      await loadDaySlots(rest[i]);
    }
  }

  function syncDock() {
    var btn = $("book-submit");
    var summary = $("book-summary");
    var emailEl = $("book-email");
    var emails = parseEmails(emailEl ? emailEl.value : "");
    var okEmail = emailsValid(emails);
    if (emailEl) emailEl.classList.toggle("is-invalid", !!String(emailEl.value || "").trim() && !okEmail);
    if (btn) btn.disabled = loadingBook || !selectedStart || !okEmail;
    if (!summary) return;
    if (!selectedStart) {
      summary.textContent = "Выберите день, время и email";
      return;
    }
    var start = wallTime(selectedStart);
    var end = wallTime(selectedEnd);
    var range = end ? start + "–" + end : start;
    summary.innerHTML = "Выбрано: <strong>" + formatDayHeading(selectedDay) + ", " + range + "</strong>";
  }

  async function boot() {
    token = tokenFromPath();
    if (!token) {
      showForm(false);
      setStatus("Ссылка недействительна", true);
      return;
    }
    meta = readEmbeddedMeta();
    try {
      if (!meta) {
        renderDates();
        renderSlotSkeleton();
        meta = await api("/api/public/book/" + encodeURIComponent(token));
      }
      paintMeta();
      setStatus("");
      showForm(true);
      await loadDaySlots(selectedDay, { prefer: true });
      prefetchRest();
    } catch (e) {
      showForm(false);
      setStatus((e && e.message) || "Ссылка недоступна", true);
    }
    syncDock();
  }

  async function submit() {
    var emailEl = $("book-email");
    var emails = parseEmails(emailEl ? emailEl.value : "");
    if (!selectedStart || loadingBook) return;
    if (!emailsValid(emails)) {
      if (emailEl) {
        emailEl.classList.add("is-invalid");
        emailEl.focus();
      }
      setStatus("Укажите хотя бы один корректный email", true);
      return;
    }
    loadingBook = true;
    syncDock();
    setStatus("");
    try {
      var result = await api("/api/public/book/" + encodeURIComponent(token) + "/book", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          start: selectedStart,
          duration_min: selectedDuration,
          guest_email: emails.join(", "),
          guest_emails: emails,
        }),
      });
      showForm(false);
      var dock = $("book-dock");
      if (dock) dock.classList.add("hidden");
      var success = $("book-success");
      var text = $("book-success-text");
      if (success) success.classList.remove("hidden");
      if (text) {
        var when = wallTime(result && result.start);
        var end = wallTime(result && result.end);
        var range = when ? (end ? when + "–" + end : when) : "";
        var day = selectedDay ? formatDayHeading(selectedDay) : "";
        text.innerHTML = range
          ? "Ждём вас <strong>" + day + ", " + range + "</strong>."
          : "Встреча добавлена в календарь.";
      }
    } catch (e) {
      var msg = (e && e.message) || "Не удалось забронировать";
      setStatus(msg, true);
      if (e && e.status === 409) {
        selectedStart = "";
        selectedEnd = "";
        slotsByDay = {};
        loadDaySlots(selectedDay, { prefer: true }).then(prefetchRest);
      }
    } finally {
      loadingBook = false;
      syncDock();
    }
  }

  var emailEl = $("book-email");
  if (emailEl) emailEl.addEventListener("input", syncDock);
  var submitBtn = $("book-submit");
  if (submitBtn) submitBtn.addEventListener("click", submit);
  bindDuration();
  boot();
})();
