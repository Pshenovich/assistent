/* Локальные мок-данные для ?dev=1 — не подключать в прод без необходимости */
(function (global) {
  "use strict";

  var initialized = false;
  var mockVoiceSettings = {
    cleanup: true,
    numbers: true,
    dictionary: true,
    snippets: true,
    history: true,
    dictionary_entries: [
      { from: "гитхаб", to: "GitHub" },
      { from: "кубер", to: "Kubernetes" },
    ],
    snippet_entries: [{ trigger: "адрес офиса", text: "Москва, ул. Примерная, д. 1, офис 12.\nВход со двора." }],
  };
  var mockVoiceHistory = [
    { id: "h1", ts: Date.now() / 1000 - 600, text: "Составь короткое письмо клиенту: встречу переносим на четверг.", source: "note" },
    { id: "h2", ts: Date.now() / 1000 - 90000, text: "Обновить конфиг Kubernetes и перезапустить деплой в 6:30.", source: "telegram" },
  ];
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
      owner_archived: false,
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

  // ---- Профиль: моки для локальной вёрстки экранов ----
  var mockProfile = {
    me: { id: 123456789, first_name: "Вова", username: "pshenovich", bot_username: "orbita_dev_bot" },
    billing: { show_balance: true, balance_rub: 1240, tariff_label: "Pro" },
    integrations: {
      google: true,
      zoom: true,
      zoom_display_name: "v.savashkevich@obuchat.me",
      telemost: false,
      "yandex-disk": false,
      bitrix: false,
    },
    prefs: { focus: true, brief: true, brief_time: "08:30", after_meetings: true, before_meetings: true, chats: true, weekly: false },
    settings: {
      digest_enabled: true,
      meeting_reminders_enabled: true,
      meeting_reminder_minutes_before: 10,
      task_reminders_enabled: true,
      task_reminder_minutes_before: 15,
      discussion_notify_telegram: true,
      discussion_notify_web_push: false,
    },
    teams: [
      { id: 901, name: "Продукт", members: [{ email: "anna@obuchat.me", name: "Анна Котова" }, { email: "d.morozov@obuchat.me", name: "Дмитрий Морозов" }], shares: [{ note_id: 5, title: "Регламент релизов" }] },
      { id: 902, name: "Продажи", members: [{ email: "d.morozov@obuchat.me", name: "Дмитрий Морозов" }], shares: [] },
    ],
    tgLeo: {
      model: "claude-sonnet",
      models: [
        { id: "claude-sonnet", label: "Claude Sonnet" },
        { id: "claude-opus", label: "Claude Opus" },
        { id: "gpt-5", label: "GPT-5" },
      ],
      thread: null,
    },
  };

  function mockExpenses(days) {
    var out = [];
    var total = 0;
    var labels = ["Саммари встречи", "Ассистент", "Транскрипция", "Поиск по базе знаний"];
    for (var k = 0; k < days; k++) {
      var dt = new Date(Date.now() - k * 86400000).toISOString().slice(0, 10);
      var reqs = [];
      var dayTotal = 0;
      var n = (k * 7 + 3) % 4;
      for (var j = 0; j <= n; j++) {
        var cost = Math.round(((k + 2) * (j + 3) * 1.7) % 23 + 2);
        reqs.push({ label: labels[(k + j) % labels.length], cost_rub: cost });
        dayTotal += cost;
      }
      if (k === 2) {
        reqs = [];
        dayTotal = 0;
      }
      total += dayTotal;
      out.push({ date_utc: dt, total_rub: dayTotal, requests: reqs });
    }
    return { total_rub: total, days: out };
  }

  // ---- Почта: моки мастеров подключения ----
  var mockMail = { accounts: [], yandexAuthorized: false };
  function mockMailErr(message, step, code) {
    var e = new Error(message);
    e.status = 400;
    e.detail = { message: message, step: step || "", code: code || "" };
    return e;
  }
  function mockMailAdd(provider, email, auth) {
    var acc = {
      account_id: "m_" + email.replace(/[^a-z0-9]/gi, ""),
      provider: provider,
      email: email,
      auth: auth,
      send_enabled: true,
      status: "ok",
      last_error: "",
      last_sync_at: "",
      stats: {},
    };
    mockMail.accounts = mockMail.accounts.filter(function (a) {
      return a.email !== email;
    });
    mockMail.accounts.push(acc);
    mockProfile.integrations.mail = true;
    mockProfile.integrations.mail_accounts = mockMail.accounts;
    setTimeout(function () {
      acc.last_sync_at = new Date().toISOString();
      acc.stats = { first_sync: { seen: 230, bulk: 190 } };
    }, 5000);
    return { ok: true, account: acc, send_scope: true };
  }
  mockMail.actions = {};
  function mockMailThreadAction(key) {
    var a = {
      id: "t" + Date.now().toString(16).slice(-8), kind: "email_thread", status: "info", title: "Договор поставки", mine: true,
      mail: { mail_key: key || "k1", from: "Иван Петров", from_addr: "ivan@firm.ru", when: "22.10 14:30",
        gist: "Вова, привет! Посмотри, пожалуйста, договор — сможем подписать до пятницы? Если есть правки, давай созвонимся.", direction: "in" },
    };
    mockMail.actions[a.id] = a;
    return a;
  }
  function mockMailDraft(key, body, by) {
    var a = {
      id: "d" + Date.now().toString(16).slice(-8), kind: "email_draft", status: "proposed", title: "Re: Договор поставки", mine: true,
      draft: { mail_key: key || "k1", account_id: "m_1", from: "vova@yandex.ru", to: ["ivan@firm.ru"], cc: [], reply_all: false,
        subject: "Re: Договор поставки", body: body, orig_from: "Иван Петров", by: by, attachments: [], new_recipients: [],
        accounts: [{ account_id: "m_1", email: "vova@yandex.ru", send_enabled: true }], created_at: new Date().toISOString().replace("Z", "") },
    };
    mockMail.actions[a.id] = a;
    return a;
  }
  function mockMailActionOp(id, op) {
    var a = mockMail.actions[id];
    if (!a) return null;
    if (op === "send" && a.draft.new_recipients.length) {
      var e = new Error("Новый получатель: " + a.draft.new_recipients.join(", ") + " — отправить?");
      e.status = 409;
      e.detail = { message: e.message, code: "new_recipients", addrs: a.draft.new_recipients };
      throw e;
    }
    if (op === "send" || op === "send_confirmed") {
      a.status = "sending";
      a.draft.send_at = new Date(Date.now() + 10000).toISOString();
      setTimeout(function () {
        if (a.status === "sending") {
          a.status = "sent";
          a.draft.sent_at = new Date().toISOString();
        }
      }, 10000);
    } else if (op === "cancel_send") a.status = "proposed";
    return { action: a };
  }
  global.__mockMail = mockMail;
  global.__mockMailDraft = mockMailDraft;
  global.__mockMailThreadAction = mockMailThreadAction;

  function mailMockResponse(basePath, method, opts) {
    var b = opts && typeof opts.body === "string" ? JSON.parse(opts.body || "{}") : {};
    if (basePath === "/mail/accounts" && method === "GET") return { ok: true, accounts: mockMail.accounts, yandex: { connected: mockMail.yandexAuthorized } };
    if (basePath === "/mail/detect") {
      var email = String(b.email || "").toLowerCase();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw mockMailErr("Похоже, в адресе опечатка — проверьте, пожалуйста.", "email");
      var dom = email.split("@")[1];
      var prov = /gmail/.test(dom) ? "gmail" : /yandex|ya\.ru/.test(dom) ? "yandex" : /mail\.ru|inbox|list\.ru|bk\.ru/.test(dom) ? "mailru" : dom === "obuchat.me" ? "gmail" : "imap";
      return {
        ok: true, email: email, provider: prov, corporate: dom === "obuchat.me",
        hint: dom === "obuchat.me" ? "Это корпоративная почта Google Workspace. Если в настройках Google не будет раздела «Пароли приложений», их отключил администратор — попросите его включить или подключите другую почту." : "",
        needs_server: prov === "imap", imap_host: prov === "imap" ? "" : "imap." + dom, imap_port: 993, smtp_host: "", smtp_port: 465,
      };
    }
    if (basePath === "/mail/yandex/start") {
      setTimeout(function () {
        mockMail.yandexAuthorized = true;
      }, 4000);
      return { url: "https://oauth.yandex.ru/authorize?mock=1", verification_code_flow: false };
    }
    if (basePath === "/mail/connect") {
      if (b.provider === "yandex" && !b.password) {
        if (!mockMail.yandexAuthorized) return { ok: false, need_oauth: true };
        if (!mockMail.imapWarned) {
          mockMail.imapWarned = true;
          throw mockMailErr("Яндекс не пустил по IMAP: скорее всего, в настройках почты выключен доступ почтовым программам.", "imap", "imap_disabled");
        }
        return mockMailAdd("yandex", "vova@yandex.ru", "oauth_yandex");
      }
      if (!b.password) throw mockMailErr("Вставьте пароль приложения.", "password");
      if (b.password === "bad") throw mockMailErr("Почта не приняла пароль. Скорее всего, вставлен обычный пароль от почты, а нужен пароль приложения.", "password", "auth");
      return mockMailAdd(b.provider || "imap", String(b.email || "me@example.com").toLowerCase(), "password");
    }
    if (basePath === "/mail/settings") {
      if (method === "PATCH") mockMail.signature = String(b.signature || "");
      return { ok: true, signature: mockMail.signature || "" };
    }
    if (basePath === "/mail/thread-card") return { action: mockMailThreadAction(b.mail_key) };
    if (basePath === "/mail/drafts") return { action: mockMailDraft(b.mail_key, b.body || "", "user") };
    var dm = /^\/mail\/drafts\/([^/]+)$/.exec(basePath);
    if (dm) {
      var dr = mockMail.actions[dm[1]];
      if (!dr) throw mockMailErr("Черновик не найден");
      ["to", "cc", "subject", "body", "reply_all"].forEach(function (k) {
        if (k in b) dr.draft[k] = b[k];
      });
      if ("reply_all" in b) dr.draft.cc = b.reply_all ? ["olga@firm.ru", "boss@firm.ru"] : [];
      if ("attachments" in b) dr.draft.attachments = (mockMail.files || []).filter(function (f) { return b.attachments.indexOf(f.id) >= 0; });
      var known = ["ivan@firm.ru", "olga@firm.ru", "boss@firm.ru"];
      dr.draft.new_recipients = dr.draft.to.concat(dr.draft.cc).filter(function (x) { return known.indexOf(x) < 0; });
      dr.title = dr.draft.subject;
      return { action: dr };
    }
    if (basePath === "/mail/rewrite") {
      var txt = String(b.body || "");
      if (b.mode === "short") txt = txt.split(/\n/)[0];
      else if (b.mode === "polite") txt = "Здравствуйте!\n\n" + txt + "\n\nСпасибо!";
      else if (b.mode === "translate") txt = "Hello!\n\n" + txt;
      else txt = txt + "\n(поправлено: " + b.instruction + ")";
      return { body: txt };
    }
    if (basePath === "/mail/composer/slots") {
      var q = String(b.query || "");
      var people = /мыздрик/i.test(q) ? ["Олег Мыздриков"] : [];
      var nextTue = /вторник/i.test(q);
      var dl = nextTue ? "вт 03.11" : "чт 22.10";
      return {
        ok: true, date: nextTue ? "2026-11-03" : "2026-10-22", duration: 60, people: people, not_found: [],
        unavailable: people.length ? ["Олег Мыздриков"] : [], tz_label: "МСК",
        slots: [{ label: dl + ", 11:00–12:00" }, { label: dl + ", 14:00–15:00" }, { label: dl + ", 16:30–17:30" }],
      };
    }
    if (basePath === "/mail/attachments") {
      var f = { id: "f" + Date.now().toString(16).slice(-8), name: "файл.pdf", size: 182000 };
      try {
        var file = opts.body && opts.body.get && opts.body.get("file");
        if (file) f = { id: f.id, name: file.name, size: file.size };
      } catch (_) {}
      mockMail.files = (mockMail.files || []).concat([f]);
      return { ok: true, attachment: f };
    }
    var m = /^\/mail\/accounts\/([^/]+)(\/sync)?$/.exec(basePath);
    if (m) {
      var acc = mockMail.accounts.filter(function (a) { return a.account_id === m[1]; })[0];
      if (method === "DELETE") {
        mockMail.accounts = mockMail.accounts.filter(function (a) { return a.account_id !== m[1]; });
        mockProfile.integrations.mail = mockMail.accounts.length > 0;
        mockProfile.integrations.mail_accounts = mockMail.accounts;
        return { ok: true };
      }
      if (method === "PATCH" && acc && "send_enabled" in b) acc.send_enabled = !!b.send_enabled;
      return { ok: true, account: acc };
    }
    return null;
  }

  function profileMockResponse(basePath, method, opts) {
    var gm = /^\/gpt\/calendar-actions\/([^/]+)\/([a-z_]+)$/.exec(basePath);
    if (gm && mockMail.actions[gm[1]]) return mockMailActionOp(gm[1], gm[2]);
    if (basePath === "/gpt/calendar-actions/status") {
      var ids = JSON.parse((opts && opts.body) || "{}").ids || [];
      return { actions: ids.map(function (i) { return mockMail.actions[i]; }).filter(Boolean) };
    }
    if (basePath.indexOf("/mail/") === 0) {
      var mailRes = mailMockResponse(basePath, method, opts);
      if (mailRes) return mailRes;
    }
    if (basePath === "/me" && method === "GET") return mockProfile.me;
    if (basePath === "/billing" && method === "GET") return mockProfile.billing;
    if (basePath === "/usage/expenses" && method === "GET") {
      var m = /days=(\d+)/.exec(String((opts && opts._path) || ""));
      return mockExpenses(m ? parseInt(m[1], 10) : 7);
    }
    if (basePath === "/integrations" && method === "GET") return mockProfile.integrations;
    if (basePath === "/profile/telegram-leo") {
      if (method === "PATCH") {
        var b = JSON.parse((opts && opts.body) || "{}");
        if (b.model) mockProfile.tgLeo.model = b.model;
      }
      return mockProfile.tgLeo;
    }
    if (basePath === "/digest/chats" && method === "GET") {
      return {
        digest_enabled: !!mockProfile.settings.digest_enabled,
        chats: [
          { chat_id: -1001, title: "Продукт · ядро", selected: true },
          { chat_id: -1002, title: "Продажи · Москва", selected: true },
          { chat_id: -1003, title: "Офис", selected: false },
          { chat_id: -1004, title: "Найм", selected: true },
        ],
      };
    }
    if (basePath === "/settings") {
      if (method === "PATCH") Object.assign(mockProfile.settings, JSON.parse((opts && opts.body) || "{}"));
      return mockProfile.settings;
    }
    if (basePath === "/calendar/sources" && method === "GET") {
      return {
        calendars: [
          { id: "primary", summary: "Работа", primary: true, backgroundColor: "#3b82f6", excluded: false },
          { id: "team", summary: "Команда продукта", accessRole: "writer", backgroundColor: "#34b36b", excluded: false },
          { id: "personal", summary: "Личное", accessRole: "owner", backgroundColor: "#e5568a", excluded: false },
          { id: "holidays", summary: "Праздники РФ", accessRole: "reader", backgroundColor: "#8e8e93", excluded: true },
        ],
      };
    }
    if (basePath === "/zoom/status" && method === "GET") {
      return { connected: !!mockProfile.integrations.zoom, zoom_display_name: mockProfile.integrations.zoom_display_name };
    }
    if (basePath === "/zoom/recordings" && method === "GET") {
      var t0 = Date.now();
      return {
        items: [
          { topic: "Планёрка продукта", start_at_utc: new Date(t0 - 2 * 86400000).toISOString(), source: "cloud", play_url: "https://zoom.us/rec/1" },
          { topic: "1:1 Анна / Вова", start_at_utc: new Date(t0 - 6 * 86400000).toISOString(), source: "local" },
        ],
      };
    }
    if (basePath === "/telemost/status" && method === "GET") return { connected: false };
    if (basePath === "/proactive/prefs") {
      if (method === "PATCH") Object.assign(mockProfile.prefs, JSON.parse((opts && opts.body) || "{}"));
      return { prefs: mockProfile.prefs };
    }
    if (basePath === "/knowledge-bases" && method === "GET") {
      return {
        items: [
          { id: 1, title: "Регламенты компании", source_type: "google_docs", role: "owner", sync_status: "ok", chunk_count: 184, last_sync_at: "2026-10-21T09:12:00" },
          { id: 2, title: "Вики продукта", source_type: "notion", role: "member", sync_status: "ok", chunk_count: 62, last_sync_at: "2026-10-20T18:40:00" },
        ],
      };
    }
    if (basePath === "/profile/avatar" && method === "DELETE") {
      mockProfile.me.has_custom_avatar = false;
      return { ok: true, removed: true };
    }
    if (basePath === "/agents" && method === "GET") {
      return {
        agents: [
          { id: "paie", kind: "paie", title: "PAIE", builtin: true, model: "anthropic/claude-sonnet", prompt: "Отвечай по базе знаний компании.", rules: "", max_rounds: 4 },
          { id: "research", kind: "research", title: "Research", builtin: true, model: "anthropic/claude-opus", prompt: "Ищи в интернете и давай ссылки.", temperature: 0.2 },
          { id: "gpt", kind: "gpt", title: "GPT", builtin: true, model: "openai/gpt-5", prompt: "", temperature: 0.7 },
          { id: "u1", kind: "custom", title: "Редактор постов", builtin: false, can_rename: true, can_delete: true, model: "anthropic/claude-haiku", prompt: "Ты редактор корпоративного блога.", temperature: 0.7, web_search: false },
        ],
      };
    }
    if (basePath === "/gpt/models" && method === "GET") {
      return {
        models: [
          { id: "anthropic/claude-sonnet", name: "Claude Sonnet" },
          { id: "anthropic/claude-opus", name: "Claude Opus", reasoning: true },
          { id: "anthropic/claude-haiku", name: "Claude Haiku" },
          { id: "openai/gpt-5", name: "GPT-5" },
        ],
      };
    }
    if (basePath === "/teams" && method === "GET") return { teams: mockProfile.teams };
    if (basePath === "/teams" && method === "POST") {
      var tb = JSON.parse((opts && opts.body) || "{}");
      var team = { id: 900 + mockProfile.teams.length, name: tb.name || "Команда", members: [], shares: [] };
      mockProfile.teams.push(team);
      return team;
    }
    if (/^\/contacts\/[^/]+\/meetings$/.test(basePath)) {
      var d0 = new Date();
      function iso(days, h) {
        var d = new Date(d0.getTime() + days * 86400000);
        d.setHours(h, 0, 0, 0);
        return d.toISOString();
      }
      return {
        connected: true,
        upcoming: [{ id: "m1", summary: "Ревью роадмапа Q4", start: { dateTime: iso(2, 11) }, end: { dateTime: iso(2, 12) }, attendees: [{}, {}, {}] }],
        past: [
          { id: "m2", summary: "Планёрка продукта", start: { dateTime: iso(-2, 15) }, end: { dateTime: iso(-2, 16) }, attendees: [{}, {}, {}, {}] },
          { id: "m3", summary: "1:1 Анна / Вова", start: { dateTime: iso(-6, 12) }, end: { dateTime: iso(-6, 13) }, attendees: [{}, {}] },
        ],
        past_total: 6,
        manual: [],
      };
    }
    return undefined;
  }

  async function handle(path, opts, realFetch) {
    initMocks();
    var method = String((opts && opts.method) || "GET").toUpperCase();
    var basePath = String(path || "").split("?")[0];
    opts = Object.assign({}, opts || {}, { _path: path });

    if (basePath === "/voice/settings") {
      if (method === "PATCH") {
        var vp = JSON.parse((opts && opts.body) || "{}");
        Object.keys(vp).forEach(function (k) {
          mockVoiceSettings[k] = vp[k];
        });
      }
      return JSON.parse(JSON.stringify(mockVoiceSettings));
    }
    if (basePath === "/voice/history" && method === "DELETE") {
      mockVoiceHistory = [];
      return { ok: true, items: [] };
    }
    var voiceHistDel = basePath.match(/^\/voice\/history\/([^/]+)$/);
    if (voiceHistDel && method === "DELETE") {
      mockVoiceHistory = mockVoiceHistory.filter(function (x) {
        return x.id !== decodeURIComponent(voiceHistDel[1]);
      });
      return { ok: true, items: mockVoiceHistory };
    }
    if (basePath === "/voice/history") return { items: mockVoiceHistory };

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
        owner_archived: !!task.owner_archived,
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
        if (patch.result != null) existing.result = String(patch.result || "");
        if (patch.start) existing.start_at = String(patch.start);
        if (patch.end) existing.end_at = String(patch.end);
        if (patch.checklist) existing.checklist = patch.checklist;
        if (patch.assignee_user_id != null) existing.assignee_user_id = String(patch.assignee_user_id || "");
        if (patch.assignee_email != null) existing.assignee_email = String(patch.assignee_email || "");
        if (patch.assignee_name != null) existing.assignee_name = String(patch.assignee_name || "");
        if (patch.done != null) existing.done = !!patch.done;
        if (patch.owner_archived != null) existing.owner_archived = !!patch.owner_archived;
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
      var wantArchived = false;
      try {
        var postedQ = String(path || "").split("?")[1] || "";
        postedQ.split("&").forEach(function (part) {
          var kv = part.split("=");
          if (decodeURIComponent(kv[0] || "") === "archived") {
            wantArchived = decodeURIComponent(kv[1] || "") === "1";
          }
        });
      } catch (_) {}
      var postedAll = mockTasks.filter(function (t) {
        var owner = String(t.owner_user_id || "1");
        var assignee = String(t.assignee_user_id || "");
        return assignee && assignee !== owner;
      });
      var posted = postedAll.filter(function (t) {
        return !!t.owner_archived === wantArchived;
      });
      var activeCount = postedAll.filter(function (t) {
        return !t.done && !t.owner_archived;
      }).length;
      return {
        ok: true,
        items: posted.map(function (t) {
          return mockTaskApi(t).task;
        }),
        count: activeCount,
        archived: wantArchived,
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
      var taskQuery = /[?&]q=/.test(String(path || ""))
        ? decodeURIComponent((String(path).match(/[?&]q=([^&]*)/) || [])[1] || "").toLowerCase()
        : null;
      if (taskQuery !== null) {
        list = list.filter(function (t) {
          return !taskQuery || String(t.title || "").toLowerCase().indexOf(taskQuery) >= 0;
        });
      }
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
        owner_archived: false,
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

    if (basePath === "/notes/transcriptions/upload" && method === "POST") {
      var withSummary = false;
      try {
        if (opts && opts.body && typeof opts.body.get === "function") {
          withSummary = String(opts.body.get("with_summary") || "") === "1";
        }
      } catch (_) {}
      var newId = -(93000 + transcriptions.length + 1);
      var transcriptBody =
        "SPEAKER1: Тестовая транскрипция загруженного файла.\nSPEAKER2: Проверка режима миниаппа.";
      var item = {
        id: newId,
        title: "Загруженный файл",
        body: withSummary ? "" : transcriptBody,
        description: withSummary ? "" : transcriptBody,
        preview: withSummary ? "Готовим саммари…" : transcriptBody.slice(0, 120),
        role: "transcription",
        is_transcription: true,
        has_summary: !!withSummary,
        summary_generating: !!withSummary,
        primary_sheet_title: withSummary ? "Саммари" : "Транскрипции",
        created_at: nowIso(),
        updated_at: nowIso(),
        meta: {
          source: "miniapp_upload",
          summary_generating: !!withSummary,
        },
        _transcript: transcriptBody,
        _summary: "Демо-саммари загруженного файла.",
        tags: [],
      };
      transcriptions.unshift(item);
      if (withSummary) {
        setTimeout(function () {
          item.summary_generating = false;
          item.meta.summary_generating = false;
          item.body = item._summary;
          item.description = item._summary;
          item.preview = item._summary;
        }, 1200);
      }
      return { ok: true, id: newId, item: Object.assign({}, item), with_summary: withSummary };
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

    var profileMock = profileMockResponse(basePath, method, opts);
    if (profileMock !== undefined) return profileMock;

    if (basePath === "/contacts" && method === "GET") {
      return {
        items: [
          { key: "anna@obuchat.me", email: "anna@obuchat.me", telegram_username: "annak", name: "Анна Котова", aliases: ["Аня"], teams: [{ id: 901, name: "Продукт" }] },
          { key: "tg:artleb", email: "", telegram_username: "artleb", name: "Артём Лебедев", teams: [] },
          { key: "d.morozov@obuchat.me", email: "d.morozov@obuchat.me", name: "Дмитрий Морозов", teams: [{ id: 902, name: "Продажи" }] },
          { key: "uliana@example.com", email: "uliana@example.com", name: "Ульяна", teams: [] },
          { key: "tg:petrov", email: "", telegram_username: "petrov", name: "Пётр Петров", teams: [] },
        ],
      };
    }
    if (basePath === "/notes/backlinks" && method === "GET") {
      return {
        ok: true,
        items: [{ note_id: -1, sheet_id: "", title: "Планёрка", sheet_title: "", is_transcription: false }],
      };
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
