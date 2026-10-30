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
  var MOCK_ME = "123456789"; // id пользователя из мока /me
  var mockTasks = [
    {
      id: 9001,
      owner_user_id: MOCK_ME,
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
  // «Поручено другим» в ?dev=1: просроченные, с чек-листом и вопросом, разные исполнители.
  (function () {
    function day(off) {
      var x = new Date();
      x.setDate(x.getDate() + off);
      return x.toISOString().slice(0, 10);
    }
    var hAgo = function (h) {
      return new Date(Date.now() - h * 3600000).toISOString();
    };
    [
      [9002, "Согласовать договор с подрядчиком", -3, "14:00", "33", "Артём", false, [], { assignee_seen_at: hAgo(70) }],
      [9003, "Подготовить отчёт по продажам за Q3", -1, null, "22", "Ульяна", false,
        [{ text: "Выгрузка из CRM", done: true }, { text: "Графики", done: false }, { text: "Выводы", done: false }],
        { assignee_seen_at: hAgo(30), assignee_status: "question", assignee_status_note: "За какой период брать данные — квартал или 9 месяцев?", assignee_status_at: hAgo(5), description: "К совету директоров в пятницу\nИз «Планёрка»", note_id: "12" }],
      [9004, "Созвониться с клиентом Альфа по интеграции и зафиксировать сроки запуска пилота", 0, "23:30", "44", "Максим Петров", false, [], { assignee_seen_at: hAgo(3), assignee_status: "in_progress", assignee_status_at: hAgo(2) }],
      [9005, "Обновить лендинг", 1, null, "33", "Артём", false, [], {}],
      [9006, "Проверить счета", 4, "11:00", "55", "Ирина", false, [], { last_nudged_at: hAgo(1) }],
      [9007, "Собрать фидбек от команды", -2, null, "22", "Ульяна", true, [], { result: "Таблица в заметке «Фидбек Q3», выводы — на втором листе", done_at: hAgo(26), assignee_seen_at: hAgo(60) }],
      [9011, "Подготовить КП для Бета", -5, null, "44", "Максим Петров", true, [], { owner_archived: true, outcome: "accepted", done_at: hAgo(72), accepted_at: hAgo(70) }],
      [9012, "Перенести сайт на новый хостинг", -14, null, "33", "Артём", true, [], { owner_archived: true, outcome: "accepted", done_at: hAgo(15 * 24 + 5), accepted_at: hAgo(14 * 24) }],
      [9013, "Сверка с бухгалтерией", -26, null, "55", "Ирина", false, [], { owner_archived: true, outcome: "cancelled", accepted_at: hAgo(25 * 24) }],
      [9008, "Нанять дизайнера", 12, null, "44", "Максим Петров", false, [], {}],
      // Мне поручил коллега — проверить «В работе / Есть вопрос» у исполнителя (?task=9010).
      [9010, "Прислать комментарии к смете", 1, "12:00", MOCK_ME, "Вова", false, [], { owner_user_id: "22" }],
    ].forEach(function (a) {
      var d = day(a[2]);
      mockTasks.push(Object.assign({
        id: a[0], owner_user_id: MOCK_ME, title: a[1], description: "",
        start_at: d + "T" + (a[3] || "00:00") + ":00+03:00",
        end_at: a[3] ? d + "T" + a[3] + ":00+03:00" : d + "T23:59:00+03:00",
        checklist: a[7], assignee_user_id: a[4], assignee_email: "", assignee_name: a[5], note_id: "",
        all_day: !a[3], done: a[6], owner_archived: false, chip_label: "",
      }, a[8]));
    });
  })();
  var nextMockTaskId = 9100;
  var mockTaskLogs = {};
  function mockTaskLog(task, kind, uid, name, text, meta) {
    var k = String(task.id);
    (mockTaskLogs[k] = mockTaskLogs[k] || []).push({ at: new Date().toISOString(), kind: kind, actor_user_id: String(uid || ""), actor_name: name || "", text: text || "", meta: meta || {} });
  }
  // Как на сервере: события ленты плюс достроенные из полей задачи (поставлено, открыто, вопрос).
  function mockTaskEvents(task) {
    var out = (mockTaskLogs[String(task.id)] || []).slice();
    var kinds = out.map(function (e) { return e.kind; });
    var legacy = [];
    var created = new Date(new Date(task.start_at).getTime() - 2 * 86400000).toISOString();
    if (kinds.indexOf("created") < 0) legacy.push({ at: created, kind: "created", actor_user_id: String(task.owner_user_id), actor_name: String(task.owner_user_id) === MOCK_ME ? "Вова" : "Олег Смирнов", text: "" });
    if (kinds.indexOf("seen") < 0 && task.assignee_seen_at) legacy.push({ at: task.assignee_seen_at, kind: "seen", actor_user_id: task.assignee_user_id, actor_name: task.assignee_name, text: "" });
    if (kinds.indexOf("message") < 0 && task.assignee_status === "question" && task.assignee_status_note) legacy.push({ at: task.assignee_status_at, kind: "message", actor_user_id: task.assignee_user_id, actor_name: task.assignee_name, text: task.assignee_status_note });
    if (kinds.indexOf("nudge") < 0 && task.last_nudged_at) legacy.push({ at: task.last_nudged_at, kind: "nudge", actor_user_id: MOCK_ME, actor_name: "Вова", text: "" });
    if (kinds.indexOf("done") < 0 && task.done && task.done_at) legacy.push({ at: task.done_at, kind: "done", actor_user_id: task.assignee_user_id, actor_name: task.assignee_name, text: task.result || "" });
    return legacy.concat(out).sort(function (a, b) { return String(a.at).localeCompare(String(b.at)); });
  }

  // Внешний трекер (Битрикс24) для ?dev=1: доски, стадии, создание, WWA.
  var mockTracker = {
    connected: true,
    defaults: { board_id: "153", board_name: "Obuchat · Клиентские задачи", stage_id: "1053", stage_name: "Оценка" },
    boards: [
      { id: "personal", name: "Мои задачи", kind: "personal" },
      { id: "153", name: "Obuchat · Клиентские задачи", kind: "group" },
      { id: "12", name: "Продукт и платформа: длинное название проекта для проверки обрезки", kind: "group" },
    ],
    stages: {
      personal: [{ id: "5", name: "Не спланированы" }, { id: "6", name: "На этой неделе" }],
      "153": [{ id: "1053", name: "Оценка" }, { id: "1054", name: "Новые" }, { id: "1089", name: "В работе" }],
      "12": [{ id: "2001", name: "Бэклог продукта с очень длинным названием стадии" }, { id: "2002", name: "Готово" }],
    },
    links: {
      "9001": [{
        provider: "bitrix24", external_id: "4790", title: "Сделать презентацию", stage_name: "Оценка",
        url: "https://acme.bitrix24.ru/workgroups/group/153/tasks/task/view/4790/",
      }],
    },
    nextId: 4812,
  };

  function trackerPublic() {
    return [{
      id: "bitrix24", title: "Битрикс24", short_title: "Б24", connected: mockTracker.connected,
      connection: mockTracker.connected ? { transport: "webhook", portal: "https://acme.bitrix24.ru", account_name: "Вова П" } : {},
      defaults: mockTracker.defaults,
    }];
  }

  function trackerMockResponse(basePath, method, opts) {
    if (basePath.indexOf("/trackers") !== 0) return null;
    var body = {};
    try { body = opts && opts.body ? JSON.parse(opts.body) : {}; } catch (_) {}
    var path = String((opts && opts._path) || basePath);
    if (basePath === "/trackers") return { trackers: trackerPublic() };
    if (basePath === "/trackers/bitrix24/boards") return { boards: mockTracker.boards };
    var sm = /^\/trackers\/bitrix24\/boards\/([^/]+)\/stages$/.exec(basePath);
    if (sm) return { stages: mockTracker.stages[decodeURIComponent(sm[1])] || [] };
    if (basePath === "/trackers/bitrix24/users") {
      var um = /[?&]q=([^&]*)/.exec(path);
      var uq = um ? decodeURIComponent(um[1]).toLowerCase() : "";
      var people = [
        { id: "42", name: "Иван Петров", position: "Ведущий разработчик бэкенда с очень длинной должностью" },
        { id: "43", name: "Ульяна Смирнова", position: "Менеджер проектов" },
        { id: "44", name: "Константин Константинопольский", position: "" },
        { id: "45", name: "Анна Иванова", position: "QA" },
      ];
      return { users: people.filter(function (p) { return !uq || p.name.toLowerCase().indexOf(uq) >= 0; }) };
    }
    if (basePath === "/trackers/bitrix24/settings") {
      mockTracker.defaults = body;
      return { ok: true, defaults: body };
    }
    if (basePath === "/trackers/bitrix24/webhook") {
      mockTracker.connected = method !== "DELETE";
      return method === "DELETE" ? { disconnected: true } : { connected: true, connection: trackerPublic()[0].connection };
    }
    if (basePath === "/trackers/describe-wwa") {
      var t = String(body.title || "задача");
      return {
        ok: true,
        description:
          "Зачем\nКлиенту нужно сверять оплаты со своей бухгалтерией без ручного копирования («" + t + "»).\n\n" +
          "Что сделать\n- Кнопка «Выгрузить в CSV» на экране оплат\n- Поля: дата, сумма, плательщик, статус, ID транзакции\n- Учитывать текущие фильтры экрана\n\n" +
          "Критерии приёмки\n- Файл открывается в Excel без проблем с кодировкой\n- Выгрузка за месяц — меньше 10 секунд\n- Уточнить: нужен ли отдельный столбец с НДС",
      };
    }
    if (basePath === "/trackers/bitrix24/tasks") {
      var id = String(mockTracker.nextId++);
      var url = "https://acme.bitrix24.ru/workgroups/group/" + (body.board_id || "0") + "/tasks/task/view/" + id + "/";
      if (body.calendar_task_id) {
        (mockTracker.links[body.calendar_task_id] = mockTracker.links[body.calendar_task_id] || []).push({
          provider: "bitrix24", external_id: id, url: url, title: body.title, stage_name: body.stage_name || "",
        });
      }
      return { ok: true, external: { provider: "bitrix24", id: id, url: url }, chip_label: body.title };
    }
    if (basePath === "/trackers/links") {
      var cm = /calendar_task_id=([^&]+)/.exec(path);
      return { links: (cm && mockTracker.links[decodeURIComponent(cm[1])]) || [] };
    }
    return null;
  }

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
    prefs: { focus: true, brief: true, brief_time: "08:30", after_meetings: true, before_meetings: true, chats: true, chat_alerts: true, weekly: false },
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
    if (basePath === "/mail/threads" && method === "GET") {
      mockMail.accounts.length || mockMailAdd("gmail", "vova@gmail.com", "password");
      var ago = function (h) { return new Date(Date.now() - h * 3600000).toISOString(); };
      var all = [
        { thread_key: "t1", subject: "Договор поставки", count: 4, unread: 1, account_id: "m_1",
          people: [{ addr: "ivan@firm.ru", name: "Иван Петров" }],
          labels: [{ kind: "awaiting", text: "ждёт ответа" }, { kind: "deadline", text: "срок 25.10" }],
          last: { mail_key: "k4", direction: "in", from: "Иван Петров", snippet: "Вова, так что по срокам оплаты? Нам важно закрыть до пятницы.", date: ago(2) } },
        { thread_key: "t2", subject: "Новые условия сотрудничества", count: 6, unread: 0, account_id: "m_1",
          people: [{ addr: "olga@partner.ru", name: "Ольга Смирнова" }, { addr: "boss@partner.ru", name: "Андрей Ким" }, { addr: "fin@partner.ru", name: "" }],
          labels: [{ kind: "promise", text: "вы обещали: прислать расчёт скидки" }],
          last: { mail_key: "k9", direction: "out", from: "", snippet: "Пришлю расчёт до четверга.", date: ago(27) } },
        { thread_key: "t3", subject: "Интервью для подкаста", count: 1, unread: 1, account_id: "m_1",
          people: [{ addr: "anna@media.ru", name: "Анна Котова" }], labels: [],
          last: { mail_key: "k10", direction: "in", from: "Анна Котова", snippet: "Добрый день! Хотим пригласить вас в подкаст…", date: ago(80) } },
      ];
      var f = (/filter=([a-z]+)/.exec((opts && opts._path) || "") || [])[1] || "all";
      var list = all.filter(function (t) {
        return f === "awaiting" ? t.labels.some(function (l) { return l.kind === "awaiting"; }) : f === "unread" ? t.unread : true;
      });
      return { ok: true, threads: list, counts: { all: 3, awaiting: 1, unread: 2 }, accounts: [{ account_id: "m_1", email: "vova@gmail.com" }] };
    }
    var tm = /^\/mail\/threads\/([^/]+)(\/[a-z]+)?$/.exec(basePath);
    if (tm) {
      if (tm[2] === "/draft") return { ok: true, body: "Иван, привет!\n\nПо оплате готовы на 30 дней с даты поставки — так подойдёт? Если да, подпишем в четверг.\n\n" };
      if (tm[2] === "/summary") return { ok: true, summary: "• Обсуждаем договор поставки на 2026 год.\n• Решили: цена фиксируется, поставка ежемесячно.\n• Ждут от вас: ответ по срокам оплаты до пятницы.\n• Открыто: кто подписывает со стороны Firm." };
      if (tm[2]) return { ok: true };
      var ago2 = function (h) { return new Date(Date.now() - h * 3600000).toISOString(); };
      return { ok: true, thread_key: tm[1], subject: "Договор поставки", account_id: "m_1",
        people: [{ addr: "ivan@firm.ru", name: "Иван Петров" }],
        labels: [{ kind: "awaiting", text: "ждёт ответа" }],
        messages: [
          { mail_key: "k1", direction: "in", from: "Иван Петров", from_addr: "ivan@firm.ru", to: ["vova@gmail.com"], cc: [], subject: "Договор поставки", date: ago2(50),
            html: "<p>Вова, привет!</p><p>Высылаю проект договора поставки на 2026 год. Основное:</p><ul><li>цена фиксируется на год;</li><li>поставка — ежемесячно;</li><li>оплата — <b>обсуждаем</b>.</li></ul><p>Посмотри, пожалуйста, до пятницы.</p><div class=\"mail-sig\">Иван Петров, коммерческий директор Firm, +7 900 000-00-00</div><img data-src=\"https://example.com/logo.png\">",
            html_full: "<p>Вова, привет!</p><p>Высылаю проект договора…</p>", remote_images: true, reply_to_key: "", unread: false, loaded: true,
            attachments: [{ part: 0, name: "Договор_поставки_2026.pdf", size: 482000, type: "application/pdf" }], text: "Вова, привет! Высылаю проект договора поставки на 2026 год." },
          { mail_key: "k2", direction: "out", from: "", from_addr: "vova@gmail.com", to: ["ivan@firm.ru"], cc: [], subject: "Re: Договор поставки", date: ago2(30),
            html: "<p>Иван, спасибо! Посмотрю и вернусь с правками до четверга.</p>", html_full: "", remote_images: false, reply_to_key: "k1", unread: false, loaded: true, attachments: [], text: "Иван, спасибо! Посмотрю и вернусь с правками до четверга." },
          { mail_key: "k3", direction: "in", from: "Иван Петров", from_addr: "ivan@firm.ru", to: ["vova@gmail.com"], cc: [], subject: "Re: Договор поставки", date: ago2(20),
            html: "<p>Ок, жду. И ещё момент — по логистике: склад на Каширке подойдёт?</p>", html_full: "", remote_images: false, reply_to_key: "k2", unread: false, loaded: true, attachments: [], text: "Ок, жду. И ещё момент — по логистике: склад на Каширке подойдёт?" },
          { mail_key: "k4", direction: "in", from: "Иван Петров", from_addr: "ivan@firm.ru", to: ["vova@gmail.com"], cc: [], subject: "Re: Договор поставки", date: ago2(2),
            html: "<p>Вова, так что по срокам оплаты? Нам важно закрыть до пятницы.</p>", html_full: "", remote_images: false, reply_to_key: "k1", unread: true, loaded: true, attachments: [], text: "Вова, так что по срокам оплаты? Нам важно закрыть до пятницы." },
        ] };
    }
    if (basePath.indexOf("/mail/open-url") === 0) return { ok: true, url: "https://mail.google.com/" };
    if (basePath.indexOf("/mail/senders") === 0) {
      mockMail.rules = mockMail.rules || [];
      mockMail.filtered = mockMail.filtered || [
        { addr: "noreply@bank.ru", name: "Банк", count: 6, last_subject: "Выписка за октябрь" },
        { addr: "crm@client.ru", name: "CRM Клиента", count: 3, last_subject: "Новая заявка №1042" },
      ];
      if (method === "POST") {
        var pat = String(b.pattern || "").toLowerCase();
        if (pat.indexOf("@") < 0) pat = "@" + pat;
        mockMail.rules = mockMail.rules.filter(function (r) { return r.pattern !== pat; }).concat([{ pattern: pat, rule: b.rule, moved: 0 }]);
        mockMail.filtered = mockMail.filtered.filter(function (f) { return f.addr !== pat; });
        return { ok: true, pattern: pat, rule: b.rule, revived: b.rule === "allow" ? 3 : 0 };
      }
      if (method === "DELETE") {
        var dp = decodeURIComponent((/pattern=([^&]*)/.exec((opts && opts._path) || basePath) || [])[1] || "");
        mockMail.rules = mockMail.rules.filter(function (r) { return r.pattern !== dp; });
        return { ok: true, removed: true, moved: 0 };
      }
      return { ok: true, rules: mockMail.rules, filtered: mockMail.filtered };
    }
    if (basePath === "/mail/settings") {
      mockMail.settings = mockMail.settings || { signature: "", memory_enabled: true, view: "chat" };
      if (method === "PATCH") {
        ["signature", "memory_enabled", "view"].forEach(function (k) {
          if (k in b) mockMail.settings[k] = b[k];
        });
        mockMail.signature = mockMail.settings.signature;
      }
      return Object.assign({ ok: true, memory: mockMail.settings.memory_enabled ? { messages: 412, threads: 96, since: "2025-11-03" } : {} }, mockMail.settings);
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

  // ---- «Дайджест» → «Чаты»: моки списка по чатам ----
  function mockDigestDay(n) {
    return new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
  }
  var mockDigest = {
    chats: [
      { chat_id: -1001234, title: "Продукт · ядро", last_date: mockDigestDay(0), brief: "Релиз 2.4 сдвигаем на вторник — ждём миграцию биллинга", quiet: false, awaiting: 2, days: 3,
        labels: [{ kind: "wait", text: "2 ждут ответа" }, { kind: "promise", text: "вы обещали" }] },
      { chat_id: -1005678, title: "Продажи · Москва", last_date: mockDigestDay(1), brief: "Firm недоволен сроками поставки, просят созвон", quiet: false, awaiting: 0, days: 2,
        labels: [{ kind: "risk", text: "эскалация" }] },
      { chat_id: -1009012, title: "Найм", last_date: mockDigestDay(3), brief: "Три кандидата на финал, решение до пятницы", quiet: false, awaiting: 0, days: 1,
        labels: [{ kind: "deadline", text: "срок" }] },
      { chat_id: -1003456, title: "Маркетинг", last_date: mockDigestDay(3), brief: "Утвердили бюджет на ноябрь", quiet: false, awaiting: 0, days: 2, labels: [] },
      { chat_id: -1007890, title: "Офис", last_date: mockDigestDay(4), brief: "Ничего важного", quiet: true, awaiting: 0, days: 1, labels: [] },
    ],
  };
  var mockStepLinks = {};
  function mockDigestChat(id) {
    var c = mockDigest.chats.filter(function (x) { return x.chat_id === id; })[0] || mockDigest.chats[0];
    return {
      ok: true, chat_id: c.chat_id, title: c.title, url: "https://t.me/c/1234",
      awaiting: c.awaiting ? [
        { key: "chat:a1", who: "Анна Котова", text: "Вова, апрув на релиз во вторник? Нужно до вечера", asked_at: new Date(Date.now() - 3 * 3600000).toISOString(), url: "https://t.me/c/1234/55" },
        { key: "chat:a2", who: "Дмитрий Морозов", text: "Кто ревьюит миграцию биллинга?", asked_at: new Date(Date.now() - 5 * 3600000).toISOString(), url: "https://t.me/c/1234/41" },
      ] : [],
      cards: c.labels.some(function (l) { return l.kind === "promise"; }) ? [{ key: "chat:p1", type: "chat_promise", label: "вы обещали", text: "Согласовать стенд с DevOps до среды" }] : [],
      days: [
        { date: c.last_date, message_count: 48, brief: c.brief, decisions: ["Стенд под нагрузочные тесты — отдельный"],
          next_steps: [{ who: "Анна", task: "Отчёт по багам", deadline: "18:00", key: "digest_step:k1", task_link: mockStepLinks["digest_step:k1"] || null }, { who: "Вы", task: "Согласовать стенд с DevOps", deadline: "среда", key: "digest_step:k2", task_link: mockStepLinks["digest_step:k2"] || null }],
          deadlines: [], open_questions: ["Кто сообщит клиентам о переносе?"] },
        { date: mockDigestDay(1), message_count: 12, brief: "Обсуждали метрики онбординга, решений нет", decisions: [], next_steps: [], deadlines: [], open_questions: [] },
        { date: mockDigestDay(3), message_count: 30, brief: "Планирование спринта", decisions: ["Спринт — 2 недели", "Демо по пятницам"], next_steps: [{ who: "Олег", task: "Бэклог", deadline: "до пятницы", key: "digest_step:k3", task_link: mockStepLinks["digest_step:k3"] || null }], deadlines: [], open_questions: [] },
      ],
    };
  }

  function profileMockResponse(basePath, method, opts) {
    if (basePath === "/digest/reports" && method === "GET") return { digest_enabled: true, reports: [{ chat_id: -1001234, report_date: mockDigestDay(0), title: "Продукт · ядро", summary: {} }] };
    if (basePath === "/digest/overview" && method === "GET") return Object.assign({ ok: true, with_labels: 3 }, mockDigest);
    var dgm = /^\/digest\/chats\/(-?\d+)$/.exec(basePath);
    if (dgm) return mockDigestChat(Number(dgm[1]));
    if (basePath === "/digest/item") return { ok: true, item: {} };
    if (basePath === "/digest/step-task" && method === "POST") {
      var stb = JSON.parse((opts && opts.body) || "{}");
      var stl = stb.task_id ? { id: stb.task_id, title: "Задача #" + stb.task_id, done: false } : { url: stb.url, title: stb.title };
      mockStepLinks[stb.key] = stl;
      return { ok: true, key: stb.key, task_link: stl };
    }
    if (basePath === "/digest/market/insight") {
      return { ok: true, text: "• Конкуренты (Gong, Cresta) продвигают «действие в CRM», а не только аналитику — это прямо про ваш продукт: стоит обсудить на встрече «Продукт · стратегия» в чт 11:00.\n• Новость про подешевевшие модели снижает вашу стоимость саммари встреч — проверьте экономику тарифа.\n• Вы обещали прислать расчёт скидки Ольге — новость о раунде в нише можно использовать как аргумент." };
    }

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
    if (basePath === "/me" && method === "GET") return Object.assign({}, mockProfile.me, { onboarding: JSON.parse(JSON.stringify(mockOnboarding)) });
    if (basePath === "/billing" && method === "GET") return mockProfile.billing;
    if (basePath === "/usage/expenses" && method === "GET") {
      var m = /days=(\d+)/.exec(String((opts && opts._path) || ""));
      return mockExpenses(m ? parseInt(m[1], 10) : 7);
    }
    if (basePath === "/integrations" && method === "GET") {
      return Object.assign({}, mockProfile.integrations, { trackers: trackerPublic() });
    }
    var trackerRes = trackerMockResponse(basePath, method, opts);
    if (trackerRes) return trackerRes;
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

  // Онбординг: состояние в памяти (новичок — интро, команда, тур), каталог — тот же файл, что у сервера.
  var mockOnboarding = { used: {}, hint_shown: {}, snoozed: {}, screens_seen: {}, can_reset: true };
  var mockOrg = null;
  function onboardingMock(basePath, method, opts) {
    if (basePath === "/onboarding/catalog") {
      return fetch("/assistant/onboarding/catalog.json", { cache: "no-store" }).then(function (r) {
        return r.json();
      });
    }
    if (basePath === "/onboarding/reset" && method === "POST") {
      mockOnboarding = { used: {}, hint_shown: {}, snoozed: {}, screens_seen: {}, can_reset: true, reset_at: Date.now() / 1000 };
      return { ok: true, onboarding: mockOnboarding };
    }
    if (basePath === "/onboarding/event" && method === "POST") {
      var b = JSON.parse((opts && opts.body) || "{}");
      var now = Date.now() / 1000;
      var flags = { intro_done: "intro_done_at", team_done: "team_step_done_at", tour_done: "tour_done_at" };
      if (flags[b.kind]) mockOnboarding[flags[b.kind]] = mockOnboarding[flags[b.kind]] || now;
      if (b.kind === "used") mockOnboarding.used[b.feature] = mockOnboarding.used[b.feature] || now;
      if (b.kind === "hint_shown") mockOnboarding.hint_shown[b.feature] = (mockOnboarding.hint_shown[b.feature] || 0) + 1;
      if (b.kind === "snooze") mockOnboarding.snoozed[b.feature] = now;
      if (b.kind === "screen_seen") mockOnboarding.screens_seen[b.feature] = now;
      return { ok: true, onboarding: JSON.parse(JSON.stringify(mockOnboarding)) };
    }
    // Лео-гид: «где доска?» → ответ и карточка «Показать» (как app_help на сервере).
    if (basePath === "/gpt/chat" && method === "POST") {
      var q = String(JSON.parse((opts && opts.body) || "{}").message || "");
      if (/где|как /i.test(q)) {
        var ga = { id: "g" + Date.now().toString(16).slice(-8), kind: "guide", status: "done", title: "Доска", mine: true,
          detail: "Сохранённое → заметка → «+» рядом с листами → «Доска»", open: { type: "guide", id: "board" }, link: "" };
        mockMail.actions[ga.id] = ga;
        return { answer: "Доска живёт внутри заметки: откройте заметку, нажмите «+» рядом с листами и выберите «Доска». Нажмите «Показать» — подсвечу, где это.", calendar_actions: [ga], model: "mock", model_label: "Мок" };
      }
    }
    if (basePath === "/orgs" && method === "GET") {
      return { ok: true, orgs: mockOrg ? [mockOrg] : [], current_id: mockOrg ? mockOrg.id : null, suggestions: [], my_domains: ["obuchat.me"] };
    }
    if (basePath === "/orgs" && method === "POST") {
      var ob = JSON.parse((opts && opts.body) || "{}");
      mockOrg = { id: 1, name: ob.name || "Команда", members: [{ name: "Вова", status: "active" }] };
      return { ok: true, org: mockOrg, members: mockOrg.members, can: { invite: true } };
    }
    var om = /^\/orgs\/(\d+)(\/members|\/invites)?$/.exec(basePath);
    if (om && mockOrg) {
      if (om[2] === "/members") {
        var key = JSON.parse((opts && opts.body) || "{}").key;
        mockOrg.members.push({ name: key, status: "invited" });
        return { ok: true, org: mockOrg, members: mockOrg.members, added: { status: "invited" } };
      }
      if (om[2] === "/invites") return { ok: true, invite: { token: "t", urls: { web: location.origin + "/webapp/?join=t" } } };
      return { ok: true, org: mockOrg, members: mockOrg.members, can: { invite: true } };
    }
    return undefined;
  }

  async function handle(path, opts, realFetch) {
    initMocks();
    var method = String((opts && opts.method) || "GET").toUpperCase();
    var basePath = String(path || "").split("?")[0];
    opts = Object.assign({}, opts || {}, { _path: path });
    // app.localhost — живой локальный сервер: онбординг, /me и организации берём с него.
    var liveHost = !/^(localhost|127\.0\.0\.1)$/.test(location.hostname);
    if (liveHost && (basePath === "/me" || basePath.indexOf("/onboarding/") === 0 || /^\/orgs/.test(basePath))) {
      return realFetch(path, opts);
    }
    var obRes = onboardingMock(basePath, method, opts);
    if (obRes !== undefined) return obRes;

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

    // Основной лист мок-документа (переименование / удаление с подъёмом «Бюджета»).
    if (!global.__mockKbPrimary) global.__mockKbPrimary = { title: "Основная", body: "<p>Как ведём сделки</p>", revision: 1, extra: true };
    var mockKbPrimary = global.__mockKbPrimary;
    // База знаний: два документа, один расшарен командам (лейбл команды на карточке и в заметке).
    var mockKb = [
      { id: 9001, title: "Регламент продаж", body: mockKbPrimary.body, primary_sheet_title: mockKbPrimary.title, role: "knowledge", is_knowledge: true, is_owner: true, members: [], teams: [{ id: 1, name: "Продажи" }, { id: 2, name: "Руководство" }], updated_at: new Date().toISOString() },
      { id: 9002, title: "Онбординг", body: "<p>Первый день</p>", role: "knowledge", is_knowledge: true, is_owner: true, members: [], teams: [], updated_at: new Date().toISOString() },
    ];
    if (basePath === "/notes/knowledge" && method === "GET") return { notes: JSON.parse(JSON.stringify(mockKb)) };
    var kbGet = basePath.match(/^\/notes\/local\/(900[12])$/);
    if (kbGet && method === "GET") {
      return { ok: true, item: JSON.parse(JSON.stringify(mockKb.filter(function (k) { return String(k.id) === kbGet[1]; })[0])) };
    }
    if (/^\/notes\/local\/900[12]\/sheets$/.test(basePath) && method === "GET") {
      return { sheets: [
        { id: "main", title: mockKbPrimary.title, body: mockKbPrimary.body, is_primary: true, revision: mockKbPrimary.revision, kind: "doc" },
      ].concat(mockKbPrimary.extra ? [{ id: 501, title: "Бюджет", body: "<p>Цифры пилота</p>", is_primary: false, revision: 1, kind: "doc" }] : []) };
    }
    if (/^\/notes\/local\/900[12]\/sheets\/main$/.test(basePath) && method === "PATCH") {
      mockKbPrimary.title = JSON.parse((opts && opts.body) || "{}").title || mockKbPrimary.title;
      mockKbPrimary.revision += 1;
      return { ok: true, item: { id: "main", title: mockKbPrimary.title, is_primary: true, revision: mockKbPrimary.revision }, note_updated_at: new Date().toISOString() };
    }
    if (/^\/notes\/local\/900[12]\/sheets\/main$/.test(basePath) && method === "DELETE") {
      if (!mockKbPrimary.extra) throw new Error("Это единственный текстовый лист — его нельзя удалить, можно удалить заметку");
      mockKbPrimary = global.__mockKbPrimary = { title: "Бюджет", body: "<p>Цифры пилота</p>", revision: mockKbPrimary.revision + 1, extra: false };
      return { ok: true, promoted_sheet_id: 501 };
    }
    var exportMatch = basePath.match(/^\/notes\/(local|journal)\/([^/]+)\/export$/);
    if (exportMatch && method === "POST") {
      var exp = {};
      try {
        exp = JSON.parse((opts && opts.body) || "{}");
      } catch (_) {}
      if (exp.to !== "device") return { ok: true };
      var expName = "zametka-" + exportMatch[2] + "." + (exp.format || "pdf");
      var expUrl = URL.createObjectURL(new Blob(["Мок-выгрузка " + expName + "\n"], { type: "text/plain" }));
      return { ok: true, url: expUrl, filename: expName };
    }
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
        owner_user_id: task.owner_user_id || MOCK_ME,
        chip_label: task.chip_label || "Задача",
        done: !!task.done,
        owner_archived: !!task.owner_archived,
        done_at: task.done_at || "",
        accepted_at: task.accepted_at || "",
        outcome: task.outcome || "",
        is_owner: String(task.owner_user_id || MOCK_ME) === MOCK_ME,
        assignee_state: {
          seen_at: task.assignee_seen_at || "",
          status: task.assignee_status || "",
          status_note: task.assignee_status_note || "",
          status_at: task.assignee_status_at || "",
          nudged_at: task.last_nudged_at || "",
        },
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
        if (patch.done != null) {
          if (patch.done && !existing.done) {
            existing.done_at = new Date().toISOString();
            mockTaskLog(existing, "done", existing.assignee_user_id, existing.assignee_name, existing.result || "");
          }
          existing.done = !!patch.done;
        }
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

    if (basePath === "/calendar/tasks/posted/accept-done" && method === "POST") {
      var acc = 0;
      mockTasks.forEach(function (t) {
        if (t.done && !t.owner_archived && String(t.assignee_user_id || "") !== String(t.owner_user_id || MOCK_ME)) {
          t.owner_archived = true;
          acc++;
        }
      });
      return { ok: true, archived: acc };
    }
    var taskActMatch = basePath.match(/^\/calendar\/tasks\/(\d+)\/(nudge|seen|status|events|messages|accept|return|cancel)$/);
    if (taskActMatch && (method === "POST" || taskActMatch[2] === "events")) {
      var at = mockTasks.filter(function (t) {
        return Number(t.id) === Number(taskActMatch[1]);
      })[0];
      if (!at) throw new Error("Задача не найдена");
      var act = taskActMatch[2];
      var ab = {};
      try {
        ab = JSON.parse((opts && opts.body) || "{}");
      } catch (_) {}
      var nowIso = new Date().toISOString();
      if (act === "events" && method === "GET") return { ok: true, events: mockTaskEvents(at) };
      if (act === "messages") {
        var txt = String(ab.text || "").trim();
        if (!txt && !(ab.refs || []).length && !(ab.file_ids || []).length) throw new Error("Напишите сообщение");
        var fromMe = String(at.owner_user_id) === MOCK_ME;
        var logged = (mockTaskLogs[String(at.id)] || []).some(function (e) { return e.kind === "message"; });
        if (!logged && at.assignee_status === "question" && at.assignee_status_note) {
          (mockTaskLogs[String(at.id)] = mockTaskLogs[String(at.id)] || []).push({ at: at.assignee_status_at, kind: "message", actor_user_id: at.assignee_user_id, actor_name: at.assignee_name, text: at.assignee_status_note });
        }
        mockTaskLog(at, "message", MOCK_ME, "Вова", txt, { refs: ab.refs || [], mentions: ab.mentions || [] });
        if (!fromMe) {
          at.assignee_status = "question";
          at.assignee_status_note = txt;
          at.assignee_status_at = nowIso;
        } else if (at.assignee_status === "question") {
          at.assignee_status = "in_progress";
          at.assignee_status_note = "";
        }
        return Object.assign(mockTaskApi(at), { events: mockTaskEvents(at) });
      }
      if (act === "accept") {
        at.done = true;
        at.owner_archived = true;
        at.outcome = "accepted";
        at.accepted_at = nowIso;
        at.done_at = at.done_at || nowIso;
        mockTaskLog(at, "accepted", MOCK_ME, "Вова", "");
        return Object.assign(mockTaskApi(at), { events: mockTaskEvents(at) });
      }
      if (act === "return") {
        at.done = false;
        at.owner_archived = false;
        at.outcome = "";
        at.done_at = "";
        at.assignee_status = "in_progress";
        at.assignee_status_note = "";
        mockTaskLog(at, "returned", MOCK_ME, "Вова", String(ab.text || ""));
        return Object.assign(mockTaskApi(at), { events: mockTaskEvents(at) });
      }
      if (act === "cancel") {
        at.owner_archived = true;
        at.outcome = "cancelled";
        at.accepted_at = nowIso;
        mockTaskLog(at, "cancelled", MOCK_ME, "Вова", "");
        return Object.assign(mockTaskApi(at), { events: mockTaskEvents(at) });
      }
      if (act === "nudge") {
        if (at.last_nudged_at && Date.now() - new Date(at.last_nudged_at).getTime() < 4 * 3600000) {
          throw new Error("Уже напомнили в " + new Date(at.last_nudged_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }));
        }
        at.last_nudged_at = new Date().toISOString();
        mockTaskLog(at, "nudge", MOCK_ME, "Вова", "");
      } else if (taskActMatch[2] === "status") {
        var sb = {};
        try {
          sb = JSON.parse((opts && opts.body) || "{}");
        } catch (_) {}
        at.assignee_status = sb.status || "";
        at.assignee_status_note = sb.status === "question" ? sb.note || "" : "";
        at.assignee_status_at = sb.status ? new Date().toISOString() : "";
      }
      return mockTaskApi(at);
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
        var owner = String(t.owner_user_id || MOCK_ME);
        var assignee = String(t.assignee_user_id || "");
        return owner === MOCK_ME && assignee && assignee !== owner;
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
          var links = (mockTracker.links[String(t.id)] || []);
          return Object.assign(mockTaskApi(t).task, { tracker_links: links });
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
        owner_user_id: MOCK_ME,
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
        var email = String((em && typeof em === "object" ? em.email : em) || "").trim().toLowerCase();
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
