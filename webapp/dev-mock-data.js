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
          summary: "Приглашение: дизайн-ревью",
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
          summary: "Приглашение: созвон с клиентом",
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

    transcriptions = [
      {
        id: -9001,
        operation: "obuchat_transcribe",
        main_topic: "Голосовое от коллеги",
        preview:
          "Обсудили дедлайн по проекту Лео и договорились закрыть баги до пятницы.",
        ts_utc: isoUtcDaysAgo(0, 9, 12),
        tags: [tagSnapshot(tags[0]), tagSnapshot(tags[1])],
      },
      {
        id: -9002,
        operation: "obuchat_transcribe",
        main_topic: "Интервью с кандидатом",
        preview:
          "Кандидат рассказал о опыте с Python, FastAPI и интеграциях с Telegram.",
        ts_utc: isoUtcDaysAgo(1, 14, 5),
        tags: [tagSnapshot(tags[2])],
      },
      {
        id: -9003,
        operation: "obuchat_transcribe",
        main_topic: "Заметки с созвона",
        preview: "Нужно обновить документацию API и добавить примеры для мини-приложения.",
        ts_utc: isoUtcDaysAgo(2, 11, 40),
        tags: [],
      },
      {
        id: -9004,
        operation: "obuchat_transcribe",
        main_topic: "Диктовка идей",
        preview:
          "Идея: добавить мок-данные для локальной разработки, чтобы тестировать UI без продакшена.",
        ts_utc: isoUtcDaysAgo(3, 20, 15),
        tags: [],
      },
    ];

    summaries = [
      {
        id: -9005,
        operation: "summarize",
        main_topic: "Итоги weekly",
        preview:
          "Ключевые решения: приоритет на mini app, фикс сохранения заметок, деплой в пятницу.",
        ts_utc: isoUtcDaysAgo(0, 16, 0),
        tags: [tagSnapshot(tags[0])],
      },
      {
        id: -9006,
        operation: "summarize",
        main_topic: "Саммари встречи с клиентом",
        preview: "Клиент согласовал макеты, попросил ускорить интеграцию с календарём.",
        ts_utc: isoUtcDaysAgo(1, 17, 30),
        tags: [tagSnapshot(tags[1])],
      },
      {
        id: -9007,
        operation: "summarize",
        main_topic: "Кратко: подкаст про AI",
        preview:
          "Основные тезисы: автоматизация рутины, агенты в продакшене, важность проверки ответов.",
        ts_utc: isoUtcDaysAgo(4, 8, 50),
        tags: [],
      },
    ];

    function journalBody(row) {
      return (
        (row.main_topic ? row.main_topic + "\n\n" : "") +
        (row.preview || "") +
        "\n\n—\n[Демо-запись для локальной разработки]"
      );
    }

    transcriptions.concat(summaries).forEach(function (row) {
      journalItems[String(row.id)] = {
        id: row.id,
        operation: row.operation,
        main_topic: row.main_topic,
        preview: row.preview,
        ts_utc: row.ts_utc,
        body: journalBody(row),
        tags: row.tags || [],
      };
    });
  }

  function isMockReminderId(id) {
    return String(id || "").indexOf("dev-mock-rem-") === 0;
  }

  function isMockEventId(id) {
    return String(id || "").indexOf("dev-mock-ev-") === 0;
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
    var events = (data && data.events) ? data.events.slice() : [];
    var seen = {};
    events.forEach(function (ev) {
      seen[ev.id] = true;
    });
    eventsByDate[date].forEach(function (ev) {
      if (!seen[ev.id]) events.push(ev);
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
    var sums = (out.summaries || []).slice();
    var journal = (out.journal || []).slice();
    var seenT = {};
    var seenS = {};
    var seenJ = {};
    trans.forEach(function (r) {
      seenT[r.id] = true;
    });
    sums.forEach(function (r) {
      seenS[r.id] = true;
    });
    journal.forEach(function (r) {
      seenJ[r.id] = true;
    });
    transcriptions.forEach(function (r) {
      if (!seenT[r.id]) trans.push(r);
      if (!seenJ[r.id]) journal.push(r);
    });
    summaries.forEach(function (r) {
      if (!seenS[r.id]) sums.push(r);
      if (!seenJ[r.id]) journal.push(r);
    });
    out.transcriptions = trans;
    out.summaries = sums;
    out.journal = journal;
    out.tags = mergeTagsList(out.tags);
    return out;
  }

  async function handle(path, opts, realFetch) {
    initMocks();
    var method = String((opts && opts.method) || "GET").toUpperCase();
    var basePath = String(path || "").split("?")[0];

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
    if (rsvpMatch && isMockEventId(rsvpMatch[1]) && method === "POST") {
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
      if (!foundEv) throw new Error("Встреча не найдена");
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
        if (!newName) throw new Error("Укажите название тега");
        var updatedTag = null;
        tags = tags.map(function (t) {
          if (Number(t.id) !== tagId) return t;
          updatedTag = Object.assign({}, t, { name: newName, updated_at: nowIso() });
          return updatedTag;
        });
        if (!updatedTag) throw new Error("Тег не найден");
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
      if (!createName) throw new Error("Укажите название тега");
      var duplicate = tags.find(function (t) {
        return String(t.name || "").toLowerCase() === createName.toLowerCase();
      });
      if (duplicate) throw new Error("Тег с таким именем уже существует");
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
