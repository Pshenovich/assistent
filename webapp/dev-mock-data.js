/* Локальные мок-данные для ?dev=1 — не подключать в прод без необходимости */
(function (global) {
  "use strict";

  var initialized = false;
  var reminders = [];
  var eventsByDate = {};
  var transcriptions = [];
  var summaries = [];
  var journalItems = {};
  var tags = [];
  var nextMockTagId = -91004;
  var nextMockReminderId = 100;
  var nextMockEventId = 100;
  var mockTasks = [
    {
      id: 9001,
      owner_user_id: "1",
      title: "Сделать презентацию",
      description: "",
      start_at: new Date().toISOString().slice(0, 10) + "T00:00:00+03:00",
      end_at: new Date().toISOString().slice(0, 10) + "T23:59:00+03:00",
      checklist: [],
      assignee_user_id: "22",
      assignee_email: "uliana@example.com",
      assignee_name: "Ульяна",
      note_id: "",
      all_day: true,
      done: false,
      chip_label: "сегодня",
    },
  ];
  var nextMockTaskId = 9100;

  function nowIso() {
    return new Date().toISOString();
  }

  function tagSnapshot(tag) {
    return {
      id: tag.id,
      name: tag.name,
      created_at: tag.created_at,
      updated_at: tag.updated_at,
    };
  }

  function isMockTagId(id) {
    var n = Number(id);
    return !isNaN(n) && n <= -91000 && n > -92000;
  }

  function resolveTagIds(tagIds) {
    return (tagIds || [])
      .map(function (id) {
        return tags.find(function (t) {
          return Number(t.id) === Number(id);
        });
      })
      .filter(Boolean)
      .map(tagSnapshot);
  }

  function setMockJournalTags(jid, resolvedTags) {
    var copy = resolvedTags.map(tagSnapshot);
    transcriptions.forEach(function (row) {
      if (String(row.id) === jid) row.tags = copy.slice();
    });
    summaries.forEach(function (row) {
      if (String(row.id) === jid) row.tags = copy.slice();
    });
    if (journalItems[jid]) journalItems[jid].tags = copy.slice();
  }

  function stripTagFromAllItems(tagId) {
    var tid = Number(tagId);
    function scrub(list) {
      (list || []).forEach(function (row) {
        row.tags = (row.tags || []).filter(function (t) {
          return Number(t.id) !== tid;
        });
      });
    }
    scrub(transcriptions);
    scrub(summaries);
    Object.keys(journalItems).forEach(function (jid) {
      scrub([journalItems[jid]]);
    });
  }

  function renameTagEverywhere(tagId, name) {
    var tid = Number(tagId);
    function patch(list) {
      (list || []).forEach(function (row) {
        (row.tags || []).forEach(function (t) {
          if (Number(t.id) === tid) t.name = name;
        });
      });
    }
    patch(transcriptions);
    patch(summaries);
    Object.keys(journalItems).forEach(function (jid) {
      patch([journalItems[jid]]);
    });
  }

  function mergeTagsList(serverTags) {
    var out = (serverTags || []).slice();
    var seen = {};
    out.forEach(function (t) {
      seen[t.id] = true;
    });
    tags.forEach(function (t) {
      if (!seen[t.id]) out.push(tagSnapshot(t));
    });
    out.sort(function (a, b) {
      return String(a.name || "").localeCompare(String(b.name || ""), "ru");
    });
    return out;
  }

  function pad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function todayYmd() {
    var d = new Date();
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function addYmd(ymd, days) {
    var d = new Date(ymd + "T12:00:00");
    d.setDate(d.getDate() + Number(days || 0));
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function isoLocal(dateYmd, hour, minute) {
    return dateYmd + "T" + pad2(hour) + ":" + pad2(minute) + ":00+03:00";
  }

  function isoUtcDaysAgo(days, hour, minute) {
    var d = new Date();
    d.setDate(d.getDate() - days);
    d.setHours(hour, minute, 0, 0);
    return d.toISOString();
  }

  function initMocks() {
    if (initialized) return;
    initialized = true;

    var today = todayYmd();
    var tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    var tomorrowYmd =
      tomorrow.getFullYear() +
      "-" +
      pad2(tomorrow.getMonth() + 1) +
      "-" +
      pad2(tomorrow.getDate());

    reminders = [
      {
        id: "dev-mock-rem-1",
        task: "Отправить отчёт по спринту",
        when_iso: isoLocal(tomorrowYmd, 18, 0),
        done: false,
      },
      {
        id: "dev-mock-rem-2",
        task: "Позвонить в сервисный центр",
        when_iso: isoLocal(today, 15, 30),
        done: false,
      },
      {
        id: "dev-mock-rem-3",
        task: "Забрать документы",
        when_iso: isoLocal(today, 9, 0),
        done: false,
      },
    ];

    function addMinutes(h, m, extra) {
      var total = h * 60 + m + extra;
      return { h: Math.floor(total / 60) % 24, m: total % 60 };
    }

    function organizerAttendee() {
      return {
        email: "artem.danilin.1999@gmail.com",
        name: "Артём Данилин",
        organizer: true,
        telegram_username: "artyawn",
        telegram_user_id: 871463833,
      };
    }

    function makeEvents(dateYmd) {
      var isToday = dateYmd === today;
      var h = 10;
      var m = 0;
      if (isToday) {
        var now = new Date();
        now.setSeconds(0, 0);
        if (now.getMinutes() < 30) now.setMinutes(30);
        else {
          now.setHours(now.getHours() + 1);
          now.setMinutes(0);
        }
        h = Math.min(22, Math.max(8, now.getHours()));
        m = now.getMinutes();
      }
      var t0 = { h: h, m: m };
      var t1 = addMinutes(h, m, 45);
      var t2 = addMinutes(h, m, 90);
      var prefix = "dev-mock-ev-" + dateYmd + "-";
      return [
        {
          id: prefix + "rsvp",
          calendar_id: "primary",
          summary: "Дизайн-ревью",
          kind: "Google Meet",
          start: { dateTime: isoLocal(dateYmd, t0.h, t0.m) },
          end: { dateTime: isoLocal(dateYmd, addMinutes(t0.h, t0.m, 30).h, addMinutes(t0.h, t0.m, 30).m) },
          meet_url: "https://meet.google.com/abc-defg-hij",
          html_link: "https://calendar.google.com/calendar/event?eid=" + prefix + "rsvp",
          attendees: [organizerAttendee()],
          is_organizer: false,
          self_response_status: "needsAction",
          needs_rsvp: true,
        },
        {
          id: prefix + "maybe",
          calendar_id: "primary",
          summary: "Созвон с клиентом",
          kind: "Zoom",
          start: { dateTime: isoLocal(dateYmd, t1.h, t1.m) },
          end: { dateTime: isoLocal(dateYmd, addMinutes(t1.h, t1.m, 30).h, addMinutes(t1.h, t1.m, 30).m) },
          meet_url: "https://zoom.us/j/1234567890",
          html_link: "https://calendar.google.com/calendar/event?eid=" + prefix + "maybe",
          attendees: [organizerAttendee()],
          is_organizer: false,
          self_response_status: "tentative",
          needs_rsvp: false,
        },
        {
          id: prefix + "own",
          calendar_id: "primary",
          summary: "Демо mini app",
          kind: "Google Meet",
          start: { dateTime: isoLocal(dateYmd, t2.h, t2.m) },
          end: { dateTime: isoLocal(dateYmd, addMinutes(t2.h, t2.m, 45).h, addMinutes(t2.h, t2.m, 45).m) },
          meet_url: "https://meet.google.com/xyz-uvwx-rst",
          html_link: "https://calendar.google.com/calendar/event?eid=" + prefix + "own",
          is_organizer: true,
          needs_rsvp: false,
        },
        {
          id: prefix + "lunch",
          calendar_id: "primary",
          summary: "Обед с партнёром",
          kind: "Встреча",
          start: { dateTime: isoLocal(dateYmd, 12, 0) },
          end: { dateTime: isoLocal(dateYmd, 13, 0) },
          location: "Кафе на Тверской",
          is_organizer: true,
          needs_rsvp: false,
        },
        {
          id: prefix + "overlap",
          calendar_id: "primary",
          summary: "Созвон с дизайнером",
          kind: "Google Meet",
          start: { dateTime: isoLocal(dateYmd, 12, 30) },
          end: { dateTime: isoLocal(dateYmd, 13, 30) },
          meet_url: "https://meet.google.com/des-ign-call",
          is_organizer: true,
          needs_rsvp: false,
        },
        {
          id: prefix + "allday",
          calendar_id: "primary",
          summary: "День рождения Маши",
          kind: "Встреча",
          start: { date: dateYmd },
          end: { date: addYmd(dateYmd, 1) },
          is_organizer: true,
          needs_rsvp: false,
        },
      ];
    }

    eventsByDate[today] = makeEvents(today);
    eventsByDate[tomorrowYmd] = makeEvents(tomorrowYmd);

    var ts = nowIso();
    tags = [
      { id: -91001, name: "Задачи", created_at: ts, updated_at: ts },
      { id: -91002, name: "Срочно", created_at: ts, updated_at: ts },
      { id: -91003, name: "Личное", created_at: ts, updated_at: ts },
    ];

    var transRows = [
      {
        id: -9001,
        title: "Голосовое от коллеги",
        preview:
          "Обсудили дедлайн по проекту Лео и договорились закрыть баги до пятницы.",
        updated_at: isoUtcDaysAgo(0, 9, 12),
        tags: [tagSnapshot(tags[0]), tagSnapshot(tags[1])],
        has_summary: true,
        summary:
          "Ключевые решения: приоритет на mini app, фикс сохранения заметок, деплой в пятницу.",
        transcript:
          "Обсудили дедлайн по проекту Лео и договорились закрыть баги до пятницы.",
      },
      {
        id: -9002,
        title: "Интервью с кандидатом",
        preview:
          "Кандидат рассказал о опыте с Python, FastAPI и интеграциях с Telegram.",
        updated_at: isoUtcDaysAgo(1, 14, 5),
        tags: [tagSnapshot(tags[2])],
        has_summary: true,
        summary: "Клиент согласовал макеты, попросил ускорить интеграцию с календарём.",
        transcript:
          "Кандидат рассказал о опыте с Python, FastAPI и интеграциях с Telegram.",
      },
      {
        id: -9003,
        title: "Заметки с созвона",
        preview: "Нужно обновить документацию API и добавить примеры для мини-приложения.",
        updated_at: isoUtcDaysAgo(2, 11, 40),
        tags: [],
        has_summary: false,
        transcript:
          "Нужно обновить документацию API и добавить примеры для мини-приложения.",
      },
      {
        id: -9004,
        title: "Диктовка идей",
        preview:
          "Идея: добавить мок-данные для локальной разработки, чтобы тестировать UI без продакшена.",
        updated_at: isoUtcDaysAgo(3, 20, 15),
        tags: [],
        has_summary: false,
        transcript:
          "Идея: добавить мок-данные для локальной разработки, чтобы тестировать UI без продакшена.",
      },
    ];
    transcriptions = transRows.map(function (row) {
      return {
        id: row.id,
        title: row.title,
        body: row.has_summary ? row.summary : row.transcript,
        description: row.has_summary ? row.summary : row.transcript,
        preview: row.preview,
        role: "transcription",
        is_transcription: true,
        has_summary: row.has_summary,
        primary_sheet_title: row.has_summary ? "Саммари" : "Транскрипции",
        updated_at: row.updated_at,
        created_at: row.updated_at,
        tags: row.tags || [],
        meta: {},
        _transcript: row.transcript,
        _summary: row.summary || "",
      };
    });
    summaries = [];
    journalItems = {};
    transcriptions.forEach(function (row) {
      journalItems[String(row.id)] = row;
    });
  }

  var rsvpOverrides = {};

  function isMockReminderId(id) {
    return String(id || "").indexOf("dev-mock-rem-") === 0;
  }

  function isMockEventId(id) {
    return String(id || "").indexOf("dev-mock-ev-") === 0;
  }

  function isDevCalendarEventId(id) {
    var s = String(id || "");
    return s.indexOf("dev-mock-ev-") === 0 || s.indexOf("local-dev-ev-") === 0;
  }

  function eventSlotKey(ev) {
    var title = String((ev && ev.summary) || "")
      .replace(/^\s*приглашение:\s*/i, "")
      .trim()
      .toLowerCase();
    var start = (ev && ev.start && (ev.start.dateTime || ev.start.date)) || "";
    return title + "|" + start;
  }

  function applyRsvpOverride(ev) {
    if (!ev) return ev;
    var st = rsvpOverrides[ev.id];
    if (!st) return ev;
    if (st === "declined") return null;
    var copy = Object.assign({}, ev);
    copy.self_response_status = st;
    copy.needs_rsvp = false;
    return copy;
  }

  function isMockJournalId(id) {
    var s = String(id || "");
    return /^-90\d+$/.test(s) && Object.prototype.hasOwnProperty.call(journalItems, s);
  }

  function calendarDateFromPath(path) {
    var q = path.split("?")[1] || "";
    var m = q.match(/(?:^|&)date=([^&]+)/);
    if (m && m[1]) {
      try {
        return decodeURIComponent(m[1]);
      } catch (_) {
        return m[1];
      }
    }
    return todayYmd();
  }

  function mergeReminders(data) {
    initMocks();
    var items = (data && data.items) ? data.items.slice() : [];
    var seen = {};
    items.forEach(function (it) {
      seen[it.id] = true;
    });
    reminders.forEach(function (it) {
      if (!seen[it.id]) items.push(it);
    });
    return { items: items };
  }

  function mergeCalendar(data, dateIso) {
    initMocks();
    var date = dateIso || (data && data.date) || todayYmd();
    if (!eventsByDate[date]) {
      eventsByDate[date] = eventsByDate[todayYmd()] || [];
    }
    var events = ((data && data.events) ? data.events.slice() : [])
      .map(applyRsvpOverride)
      .filter(Boolean);
    var seen = {};
    var slotSeen = {};
    events.forEach(function (ev) {
      seen[ev.id] = true;
      slotSeen[eventSlotKey(ev)] = true;
    });
    eventsByDate[date].forEach(function (ev) {
      if (seen[ev.id]) return;
      var patched = applyRsvpOverride(ev);
      if (!patched) return;
      var slot = eventSlotKey(patched);
      if (slotSeen[slot]) return;
      events.push(patched);
      seen[patched.id] = true;
      slotSeen[slot] = true;
    });
    events.sort(function (a, b) {
      var as = (a.start && (a.start.dateTime || a.start.date)) || "";
      var bs = (b.start && (b.start.dateTime || b.start.date)) || "";
      return as < bs ? -1 : as > bs ? 1 : 0;
    });
    var merged = Object.assign({}, data || {}, {
      connected: true,
      date: date,
      events: events,
    });
    delete merged.error;
    return merged;
  }

  function mergeNotes(data) {
    initMocks();
    var out = Object.assign({}, data || {});
    var trans = (out.transcriptions || []).slice();
    var journal = (out.journal || []).slice();
    var seenT = {};
    trans.forEach(function (r) {
      seenT[r.id] = true;
    });
    transcriptions.forEach(function (r) {
      if (!seenT[r.id]) trans.push(r);
    });
    out.transcriptions = trans;
    out.summaries = [];
    out.journal = journal;
    out.tags = mergeTagsList(out.tags);
    out.hashtags = Array.isArray(out.hashtags) ? out.hashtags : [];
    return out;
  }

  async function handle(path, opts, realFetch) {
    initMocks();
    var method = String((opts && opts.method) || "GET").toUpperCase();
    var basePath = String(path || "").split("?")[0];

    var localMatch = basePath.match(/^\/notes\/local\/([^/]+)(?:\/(sheets|make-summary|pdf))?$/);
    if (localMatch) {
      var lid = decodeURIComponent(localMatch[1]);
      var extra = localMatch[2] || "";
      var mockNote = transcriptions.find(function (r) {
        return String(r.id) === lid;
      });
      if (mockNote) {
        if (extra === "sheets" && method === "GET") {
          var sheets = [
            {
              id: "main",
              title: mockNote.primary_sheet_title || "Транскрипции",
              body: mockNote.body || "",
              description: mockNote.body || "",
              is_primary: true,
              revision: 1,
            },
          ];
          if (mockNote.has_summary) {
            sheets.push({
              id: -91,
              title: "Транскрипции",
              body: mockNote._transcript || "",
              description: mockNote._transcript || "",
              is_primary: false,
              revision: 1,
            });
          }
          return { ok: true, sheets: sheets };
        }
        if (extra === "make-summary" && method === "POST") {
          mockNote.has_summary = true;
          mockNote.summary_generating = true;
          mockNote.primary_sheet_title = "Саммари";
          mockNote.meta = Object.assign({}, mockNote.meta, { summary_generating: true });
          mockNote.body = "";
          mockNote.description = "";
          setTimeout(function () {
            mockNote.summary_generating = false;
            mockNote.meta.summary_generating = false;
            mockNote._summary = mockNote._summary || "Демо-саммари для локальной разработки.";
            mockNote.body = mockNote._summary;
            mockNote.description = mockNote._summary;
            mockNote.preview = mockNote._summary;
          }, 1200);
          return { ok: true, item: Object.assign({}, mockNote) };
        }
        if (extra === "pdf" && method === "POST") {
          return { ok: true };
        }
        if (!extra && method === "GET") {
          return { ok: true, item: Object.assign({}, mockNote) };
        }
        if (!extra && method === "DELETE") {
          transcriptions = transcriptions.filter(function (r) {
            return String(r.id) !== lid;
          });
          return { ok: true };
        }
        if (!extra && method === "PATCH") {
          var localPatch = {};
          try {
            localPatch = opts && opts.body ? JSON.parse(opts.body) : {};
          } catch (_) {}
          if (localPatch.title != null) mockNote.title = String(localPatch.title);
          if (localPatch.description != null) {
            mockNote.body = String(localPatch.description);
            mockNote.description = mockNote.body;
          }
          return { ok: true, item: Object.assign({}, mockNote) };
        }
      }
    }

    var journalMatch = basePath.match(/^\/notes\/journal\/([^/]+)$/);
    if (journalMatch) {
      var jid = decodeURIComponent(journalMatch[1]);
      if (isMockJournalId(jid)) {
        if (method === "GET") {
          return { item: Object.assign({}, journalItems[jid]) };
        }
        if (method === "DELETE") {
          delete journalItems[jid];
          transcriptions = transcriptions.filter(function (r) {
            return String(r.id) !== jid;
          });
          summaries = summaries.filter(function (r) {
            return String(r.id) !== jid;
          });
          return { ok: true };
        }
      }
    }

    var remMatch = basePath.match(/^\/reminders\/([^/]+)$/);
    if (remMatch && isMockReminderId(remMatch[1])) {
      var rid = decodeURIComponent(remMatch[1]);
      if (method === "DELETE") {
        reminders = reminders.filter(function (r) {
          return r.id !== rid;
        });
        return { ok: true };
      }
      if (method === "PATCH") {
        var patch = {};
        try {
          patch = opts && opts.body ? JSON.parse(opts.body) : {};
        } catch (_) {}
        var updated = null;
        reminders = reminders.map(function (r) {
          if (r.id !== rid) return r;
          updated = Object.assign({}, r);
          if (patch.task != null) updated.task = String(patch.task).trim();
          if (patch.when_iso != null) updated.when_iso = String(patch.when_iso).trim();
          if (patch.done != null) updated.done = !!patch.done;
          if (patch.checklist != null) {
            updated.checklist = Array.isArray(patch.checklist)
              ? patch.checklist
                  .map(function (it, idx) {
                    if (!it || typeof it !== "object") return null;
                    var text = String(it.text || "").trim();
                    if (!text) return null;
                    return {
                      id: String(it.id || "c" + idx),
                      text: text,
                      done: !!it.done,
                    };
                  })
                  .filter(Boolean)
              : [];
          }
          return updated;
        });
        if (!updated) throw new Error("Напоминание не найдено");
        return { item: updated };
      }
    }

    if (basePath === "/reminders" && method === "POST") {
      var remBody = {};
      try {
        remBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var task = String(remBody.task || "").trim();
      var whenIso = String(remBody.when_iso || "").trim();
      if (!task) throw new Error("Укажите текст напоминания");
      if (!whenIso) throw new Error("Укажите дату и время");
      var reminder = {
        id: "dev-mock-rem-" + nextMockReminderId++,
        task: task,
        when_iso: whenIso,
        done: false,
        checklist: Array.isArray(remBody.checklist) ? remBody.checklist : [],
      };
      reminders.unshift(reminder);
      return { item: reminder };
    }

    var rsvpMatch = basePath.match(/^\/calendar\/events\/([^/]+)\/rsvp$/);
    if (rsvpMatch && isDevCalendarEventId(rsvpMatch[1]) && method === "POST") {
      var rsvpId = decodeURIComponent(rsvpMatch[1]);
      var rsvpBody = {};
      try {
        rsvpBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var rsvpStatus = String(rsvpBody.status || "").trim().toLowerCase();
      if (rsvpStatus === "yes" || rsvpStatus === "приду") rsvpStatus = "accepted";
      if (rsvpStatus === "maybe" || rsvpStatus === "возможно") rsvpStatus = "tentative";
      if (rsvpStatus === "no" || rsvpStatus === "не приду") rsvpStatus = "declined";
      if (rsvpStatus !== "accepted" && rsvpStatus !== "tentative" && rsvpStatus !== "declined") {
        throw new Error("Укажите ответ: accepted, tentative или declined");
      }
      var foundEv = null;
      rsvpOverrides[rsvpId] = rsvpStatus;
      Object.keys(eventsByDate).forEach(function (d) {
        eventsByDate[d] = eventsByDate[d].filter(function (ev) {
          if (ev.id !== rsvpId) return true;
          if (rsvpStatus === "declined") return false;
          ev.self_response_status = rsvpStatus;
          ev.needs_rsvp = false;
          ev.is_organizer = false;
          foundEv = ev;
          return true;
        });
      });
      if (rsvpStatus === "declined") {
        return { ok: true, declined: true, self_response_status: rsvpStatus };
      }
      if (!foundEv) {
        return { ok: true, self_response_status: rsvpStatus };
      }
      return { ok: true, event: foundEv };
    }

    var evMatch = basePath.match(/^\/calendar\/events\/([^/]+)$/);
    if (evMatch && isMockEventId(evMatch[1]) && method === "DELETE") {
      var eid = decodeURIComponent(evMatch[1]);
      Object.keys(eventsByDate).forEach(function (d) {
        eventsByDate[d] = eventsByDate[d].filter(function (ev) {
          return ev.id !== eid;
        });
      });
      return { ok: true };
    }

    if (basePath === "/calendar/tasks/parse" && method === "POST") {
      var parseBody = {};
      try {
        parseBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var start = String(parseBody.start || new Date(Date.now() + 3600000).toISOString());
      var endDate = new Date(start);
      if (!isNaN(endDate.getTime())) endDate = new Date(endDate.getTime() + 30 * 60000);
      return {
        ok: true,
        title: String(parseBody.title || parseBody.text || "Задача").trim() || "Задача",
        start: start,
        end: endDate.toISOString(),
      };
    }

    function mockTaskEvent(task) {
      var allDay = !!task.all_day;
      var startDay = String(task.start_at || "").slice(0, 10);
      var startPayload = allDay
        ? { date: startDay }
        : { dateTime: task.start_at };
      var endPayload = allDay
        ? { date: startDay }
        : { dateTime: task.end_at };
      if (allDay && startDay) {
        try {
          var d = new Date(startDay + "T12:00:00");
          d.setDate(d.getDate() + 1);
          endPayload = { date: d.toISOString().slice(0, 10) };
        } catch (_) {}
      }
      return {
        id: "task-" + task.id,
        task_id: task.id,
        calendar_id: "leo-tasks",
        summary: task.title,
        kind: "Задача",
        entry_type: "task",
        all_day: allDay,
        start: startPayload,
        end: endPayload,
        start_day: startDay,
        end_day: String((endPayload.date || task.end_at) || "").slice(0, 10),
        description: task.description || "",
        checklist: task.checklist || [],
        assignee: {
          user_id: task.assignee_user_id || "",
          email: task.assignee_email || "",
          name: task.assignee_name || "",
        },
        owner_user_id: task.owner_user_id || "1",
        chip_label: task.chip_label || "Задача",
        done: !!task.done,
        note_id: task.note_id || "",
      };
    }

    function mockTaskApi(task) {
      return {
        ok: true,
        task: Object.assign({}, task, {
          event: mockTaskEvent(task),
          chip_label: task.chip_label || "Задача",
        }),
      };
    }

    function upsertMockTaskEvent(task) {
      var ev = mockTaskEvent(task);
      var day = ev.start_day || todayYmd();
      Object.keys(eventsByDate).forEach(function (d) {
        eventsByDate[d] = (eventsByDate[d] || []).filter(function (item) {
          return item.id !== ev.id;
        });
      });
      if (!eventsByDate[day]) eventsByDate[day] = [];
      eventsByDate[day].push(ev);
      eventsByDate[day].sort(function (a, b) {
        return String((a.start || {}).dateTime || "").localeCompare(
          String((b.start || {}).dateTime || "")
        );
      });
    }

    function removeMockTaskEvent(taskId) {
      var eid = "task-" + taskId;
      Object.keys(eventsByDate).forEach(function (d) {
        eventsByDate[d] = (eventsByDate[d] || []).filter(function (item) {
          return item.id !== eid;
        });
      });
    }

    var taskIdMatch = basePath.match(/^\/calendar\/tasks\/(\d+)$/);
    if (taskIdMatch) {
      var tid = Number(taskIdMatch[1]);
      var existing = mockTasks.filter(function (t) {
        return Number(t.id) === tid;
      })[0];
      if (method === "GET") {
        if (!existing) return realFetch(path, opts);
        return mockTaskApi(existing);
      }
      if (method === "PATCH") {
        if (!existing) return realFetch(path, opts);
        var patch = {};
        try {
          patch = opts && opts.body ? JSON.parse(opts.body) : {};
        } catch (_) {}
        if (patch.title != null) existing.title = String(patch.title || "").trim() || existing.title;
        if (patch.description != null) existing.description = String(patch.description || "");
        if (patch.start) existing.start_at = String(patch.start);
        if (patch.end) existing.end_at = String(patch.end);
        if (patch.checklist) existing.checklist = patch.checklist;
        if (patch.assignee_user_id != null) existing.assignee_user_id = String(patch.assignee_user_id || "");
        if (patch.assignee_email != null) existing.assignee_email = String(patch.assignee_email || "");
        if (patch.assignee_name != null) existing.assignee_name = String(patch.assignee_name || "");
        if (patch.done != null) existing.done = !!patch.done;
        upsertMockTaskEvent(existing);
        return mockTaskApi(existing);
      }
      if (method === "DELETE") {
        if (!existing) return realFetch(path, opts);
        mockTasks = mockTasks.filter(function (t) {
          return Number(t.id) !== tid;
        });
        removeMockTaskEvent(tid);
        return { ok: true };
      }
    }

    if (basePath === "/calendar/tasks/posted" && method === "GET") {
      var posted = mockTasks.filter(function (t) {
        var owner = String(t.owner_user_id || "1");
        var assignee = String(t.assignee_user_id || "");
        return !t.done && assignee && assignee !== owner;
      });
      return {
        ok: true,
        items: posted.map(function (t) {
          return mockTaskApi(t).task;
        }),
        count: posted.length,
      };
    }

    if (basePath === "/calendar/tasks" && method === "GET") {
      var noteFilter = "";
      try {
        var q = String(path || "").split("?")[1] || "";
        q.split("&").forEach(function (part) {
          var kv = part.split("=");
          if (decodeURIComponent(kv[0] || "") === "note_id") {
            noteFilter = decodeURIComponent(kv[1] || "").trim();
          }
        });
      } catch (_) {}
      var list = mockTasks.slice();
      if (noteFilter) {
        list = list.filter(function (t) {
          return String(t.note_id || "") === noteFilter;
        });
      }
      return {
        ok: true,
        tasks: list.map(function (t) {
          return mockTaskApi(t).task;
        }),
      };
    }

    if (basePath === "/calendar/tasks" && method === "POST") {
      var taskBody = {};
      try {
        taskBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var taskStart = String(taskBody.start || "").trim();
      if (!taskStart) {
        taskStart = new Date(Date.now() + 3600000).toISOString();
      }
      var taskEnd = String(taskBody.end || "").trim();
      if (!taskEnd) {
        var te = new Date(taskStart);
        taskEnd = isNaN(te.getTime())
          ? taskStart
          : new Date(te.getTime() + 30 * 60000).toISOString();
      }
      var task = {
        id: nextMockTaskId++,
        owner_user_id: "1",
        title: String(taskBody.title || "").trim() || "Задача",
        description: String(taskBody.description || ""),
        start_at: taskStart,
        end_at: taskEnd,
        checklist: Array.isArray(taskBody.checklist) ? taskBody.checklist : [],
        assignee_user_id: String(taskBody.assignee_user_id || "1"),
        assignee_email: String(taskBody.assignee_email || ""),
        assignee_name: String(taskBody.assignee_name || "Я"),
        note_id: String(taskBody.note_id || ""),
        all_day: !!taskBody.all_day,
        done: false,
        chip_label: "Задача",
      };
      mockTasks.unshift(task);
      upsertMockTaskEvent(task);
      return mockTaskApi(task);
    }

    if (basePath === "/calendar/events" && method === "POST") {
      var evBody = {};
      try {
        evBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var title = String(evBody.title || "").trim();
      var start = String(evBody.start || "").trim();
      var end = String(evBody.end || "").trim();
      if (!title) throw new Error("Укажите название встречи");
      if (!start) throw new Error("Укажите начало встречи");
      var day = start.slice(0, 10) || todayYmd();
      if (!end) {
        var endDate = new Date(start);
        if (!isNaN(endDate.getTime())) {
          endDate.setHours(endDate.getHours() + 1);
          end = endDate.toISOString();
        }
      }
      var event = {
        id: "dev-mock-ev-" + nextMockEventId++,
        calendar_id: "primary",
        summary: title,
        kind: "Встреча",
        start: { dateTime: start },
        end: { dateTime: end },
        description: String(evBody.description || ""),
        attendees: (evBody.attendees || []).map(function (em) {
          return { email: String(em || "").trim().toLowerCase(), name: "" };
        }).filter(function (a) { return a.email; }),
      };
      if (!eventsByDate[day]) eventsByDate[day] = [];
      eventsByDate[day].push(event);
      eventsByDate[day].sort(function (a, b) {
        return String((a.start || {}).dateTime || "").localeCompare(String((b.start || {}).dateTime || ""));
      });
      return { event: event };
    }

    var eventIdMatch = basePath.match(/^\/calendar\/events\/([^/]+)$/);
    if (eventIdMatch && method === "PUT") {
      var eventId = decodeURIComponent(eventIdMatch[1]);
      var putBody = {};
      try {
        putBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var found = null;
      var foundDay = "";
      Object.keys(eventsByDate).forEach(function (d) {
        (eventsByDate[d] || []).forEach(function (item) {
          if (String(item.id) === String(eventId)) {
            found = item;
            foundDay = d;
          }
        });
      });
      if (!found) return realFetch(path, opts);
      if (putBody.title != null && String(putBody.title).trim()) {
        found.summary = String(putBody.title).trim();
      }
      if (putBody.description != null) found.description = String(putBody.description || "");
      if (putBody.start) found.start = { dateTime: String(putBody.start) };
      if (putBody.end) found.end = { dateTime: String(putBody.end) };
      if (putBody.calendar_id) found.calendar_id = String(putBody.calendar_id);
      if (putBody.attendees) {
        found.attendees = (putBody.attendees || []).map(function (em) {
          return { email: String(em || "").trim().toLowerCase(), name: "" };
        }).filter(function (a) { return a.email; });
      }
      var newDay = String((found.start && found.start.dateTime) || foundDay).slice(0, 10) || foundDay;
      if (foundDay && eventsByDate[foundDay]) {
        eventsByDate[foundDay] = eventsByDate[foundDay].filter(function (item) {
          return String(item.id) !== String(eventId);
        });
      }
      if (!eventsByDate[newDay]) eventsByDate[newDay] = [];
      eventsByDate[newDay].push(found);
      eventsByDate[newDay].sort(function (a, b) {
        return String((a.start || {}).dateTime || "").localeCompare(String((b.start || {}).dateTime || ""));
      });
      return { event: found };
    }

    if (basePath === "/calendar/availability" && method === "POST") {
      var avBody = {};
      try {
        avBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var avDay = String(avBody.date || todayYmd()).slice(0, 10);
      function isoAt(h, m) {
        return (
          avDay +
          "T" +
          String(h).padStart(2, "0") +
          ":" +
          String(m).padStart(2, "0") +
          ":00+03:00"
        );
      }
      var people = [
        {
          id: "organizer",
          email: "",
          label: "Вы",
          kind: "organizer",
          calendar: true,
          busy: [{ start: isoAt(11, 0), end: isoAt(12, 0) }],
        },
      ];
      (avBody.attendees || []).forEach(function (em, i) {
        var email = String(em || "").trim().toLowerCase();
        if (!email) return;
        people.push({
          id: email,
          email: email,
          label: email,
          kind: "email",
          calendar: i === 0,
          busy: i === 0 ? [{ start: isoAt(14, 0), end: isoAt(15, 30) }] : [],
        });
      });
      return {
        date: avDay,
        timezone: "Europe/Moscow",
        work_start: isoAt(9, 0),
        work_end: isoAt(18, 0),
        people: people,
      };
    }

    if (basePath === "/voice/transcribe" && method === "POST") {
      return {
        text: "Это тестовая транскрипция голосового сообщения из локального режима.",
        plain_text: "Это тестовая транскрипция голосового сообщения из локального режима.",
        job_id: "dev-mock-transcribe",
        event_id: -99001,
      };
    }

    var itemTagsMatch = basePath.match(/^\/notes\/(journal|local)\/([^/]+)\/tags$/);
    if (itemTagsMatch && method === "PUT") {
      var itemKind = itemTagsMatch[1];
      var itemId = decodeURIComponent(itemTagsMatch[2]);
      if (itemKind === "journal" && isMockJournalId(itemId)) {
        var putBody = {};
        try {
          putBody = opts && opts.body ? JSON.parse(opts.body) : {};
        } catch (_) {}
        var resolved = resolveTagIds(putBody.tag_ids);
        setMockJournalTags(itemId, resolved);
        return { ok: true, tags: resolved };
      }
    }

    var tagMatch = basePath.match(/^\/tags\/([^/]+)$/);
    if (tagMatch && isMockTagId(tagMatch[1])) {
      var tagId = Number(decodeURIComponent(tagMatch[1]));
      if (method === "PATCH") {
        var patchBody = {};
        try {
          patchBody = opts && opts.body ? JSON.parse(opts.body) : {};
        } catch (_) {}
        var newName = String(patchBody.name || "").trim();
        if (!newName) throw new Error("Укажите название проекта");
        var updatedTag = null;
        tags = tags.map(function (t) {
          if (Number(t.id) !== tagId) return t;
          updatedTag = Object.assign({}, t, { name: newName, updated_at: nowIso() });
          return updatedTag;
        });
        if (!updatedTag) throw new Error("Проект не найден");
        renameTagEverywhere(tagId, newName);
        return { ok: true, tag: tagSnapshot(updatedTag) };
      }
      if (method === "DELETE") {
        tags = tags.filter(function (t) {
          return Number(t.id) !== tagId;
        });
        stripTagFromAllItems(tagId);
        return { ok: true };
      }
    }

    if (basePath === "/tags" && method === "POST") {
      var createBody = {};
      try {
        createBody = opts && opts.body ? JSON.parse(opts.body) : {};
      } catch (_) {}
      var createName = String(createBody.name || "").trim();
      if (!createName) throw new Error("Укажите название проекта");
      var duplicate = tags.find(function (t) {
        return String(t.name || "").toLowerCase() === createName.toLowerCase();
      });
      if (duplicate) throw new Error("Проект с таким именем уже существует");
      var created = {
        id: nextMockTagId--,
        name: createName,
        created_at: nowIso(),
        updated_at: nowIso(),
      };
      tags.push(created);
      tags.sort(function (a, b) {
        return String(a.name || "").localeCompare(String(b.name || ""), "ru");
      });
      return { ok: true, tag: tagSnapshot(created) };
    }

    var result = await realFetch(path, opts);

    if (method === "GET" && basePath === "/reminders") {
      return mergeReminders(result);
    }
    if (method === "GET" && basePath === "/calendar/today") {
      return mergeCalendar(result, calendarDateFromPath(path));
    }
    if (method === "GET" && basePath === "/calendar/range") {
      var ranged = result || { events: [] };
      var extra = [];
      Object.keys(eventsByDate).forEach(function (d) {
        (eventsByDate[d] || []).forEach(function (ev) {
          extra.push(ev);
        });
      });
      var seenR = {};
      var evs = (ranged.events || []).slice();
      evs.forEach(function (ev) {
        seenR[ev.id] = true;
      });
      extra.forEach(function (ev) {
        if (seenR[ev.id]) return;
        evs.push(ev);
        seenR[ev.id] = true;
      });
      evs.sort(function (a, b) {
        var as = (a.start && (a.start.dateTime || a.start.date)) || "";
        var bs = (b.start && (b.start.dateTime || b.start.date)) || "";
        return as < bs ? -1 : as > bs ? 1 : 0;
      });
      return Object.assign({}, ranged, { events: evs, connected: true });
    }
    if (method === "GET" && basePath === "/notes") {
      return mergeNotes(result);
    }
    if (method === "GET" && basePath === "/tags") {
      return { tags: mergeTagsList(result && result.tags) };
    }

    return result;
  }

  global.__miniappDevMock = {
    handle: handle,
    mergeReminders: mergeReminders,
    mergeCalendar: mergeCalendar,
    mergeNotes: mergeNotes,
    mergeTagsList: mergeTagsList,
    isMockJournalId: isMockJournalId,
    isMockTagId: isMockTagId,
  };
})(typeof window !== "undefined" ? window : globalThis);
