/* Шаблоны доски (webapp/board.js): схема, Гант, оргструктура — на мок-данных, дальше человек правит сам.
 * LeoBoardTemplates.build(id, {uid, today}) → элементы с координатами от (0, 0); board.js сдвигает их на место.
 */
(function () {
  "use strict";

  var B = window.LeoBoardBlocks;

  var LIST = [
    { id: "scheme", title: "Схема", hint: "Блоки и стрелки", ask: "Схема по заметке" },
    { id: "gantt", title: "Гант", hint: "Задачи по датам", ask: "Гант по заметке" },
    { id: "org", title: "Оргструктура", hint: "Карточки людей", ask: "Оргструктура по заметке" },
  ];

  // Превью карточки шаблона на пустой доске и в меню «Шаблоны».
  var PREVIEW = {
    scheme:
      '<svg viewBox="0 0 120 56" aria-hidden="true"><rect x="4" y="18" width="28" height="18" rx="9"/><rect x="46" y="18" width="28" height="18" rx="3"/>' +
      '<path d="M101 13l13 14-13 14-13-14z"/><path d="M32 27h14M74 27h14" class="is-line"/></svg>',
    gantt:
      '<svg viewBox="0 0 120 56" aria-hidden="true"><rect x="6" y="8" width="48" height="8" rx="3" class="is-bar b1"/>' +
      '<rect x="34" y="24" width="56" height="8" rx="3" class="is-bar b2"/><rect x="74" y="40" width="40" height="8" rx="3" class="is-bar b3"/></svg>',
    org:
      '<svg viewBox="0 0 120 56" aria-hidden="true"><rect x="46" y="4" width="28" height="16" rx="3"/><rect x="8" y="36" width="28" height="16" rx="3"/>' +
      '<rect x="46" y="36" width="28" height="16" rx="3"/><rect x="84" y="36" width="28" height="16" rx="3"/>' +
      '<path d="M60 20v8M22 28h76M22 28v8M60 28v8M98 28v8" class="is-line"/></svg>',
  };

  function line(uid, a, sa, b, sb, extra) {
    return Object.assign(
      { id: uid(), type: "line", x: 0, y: 0, w: 0, h: 0, points: [[0, 0], [1, 1]], start: { el: a, side: sa }, end: { el: b, side: sb }, route: "straight", style: { stroke: "ink", strokeWidth: 2 } },
      extra || {}
    );
  }

  function scheme(uid) {
    var shapes = [
      { type: "ellipse", x: 0, y: 30, w: 170, h: 80, text: "Старт", style: { stroke: "green", fill: "green", strokeWidth: 2, fontSize: 16 } },
      { type: "rect", x: 260, y: 30, w: 180, h: 80, text: "Шаг 1", style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
      { type: "diamond", x: 530, y: 10, w: 180, h: 120, text: "Условие?", style: { stroke: "orange", fill: "orange", strokeWidth: 2, fontSize: 16 } },
      { type: "rect", x: 800, y: -100, w: 180, h: 80, text: "Да — шаг 2", style: { stroke: "blue", fill: "blue", strokeWidth: 2, fontSize: 16 } },
      { type: "rect", x: 800, y: 160, w: 180, h: 80, text: "Нет — шаг 3", style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    ].map(function (s) {
      return Object.assign({ id: uid() }, s);
    });
    var ids = shapes.map(function (s) {
      return s.id;
    });
    return shapes.concat([
      line(uid, ids[0], "right", ids[1], "left", { arrowEnd: true }),
      line(uid, ids[1], "right", ids[2], "left", { arrowEnd: true }),
      line(uid, ids[2], "top", ids[3], "left", { arrowEnd: true, route: "elbow", text: "да" }),
      line(uid, ids[2], "bottom", ids[4], "left", { arrowEnd: true, route: "elbow", text: "нет" }),
    ]);
  }

  function gantt(uid, today) {
    var t0 = today || B.today();
    var add = B.addDays;
    var el = {
      id: uid(), type: "gantt", x: 0, y: 0, start: t0, unit: "day", span: 14,
      tasks: [
        { id: uid(), name: "Исследование", start: t0, end: add(t0, 3), owner: "Аня", color: "blue" },
        { id: uid(), name: "Дизайн", start: add(t0, 3), end: add(t0, 7), owner: "Олег", color: "green" },
        { id: uid(), name: "Разработка", start: add(t0, 6), end: add(t0, 11), owner: "Мария", color: "purple" },
        { id: uid(), name: "Запуск", start: add(t0, 12), end: add(t0, 13), owner: "Иван", color: "orange" },
      ],
    };
    return [B.ganttFit(el)];
  }

  function card(uid, x, y, title, name, text, fill) {
    return { id: uid(), type: "card", x: x, y: y, w: 220, h: 230, title: title, name: name, text: text, style: { fill: fill } };
  }

  function org(uid) {
    var boss = card(uid, 280, 0, "Генеральный директор", "", "Стратегия, финансы", "purple");
    var kids = [
      card(uid, 0, 330, "Директор по продукту", "", "Роадмап, исследования", "green"),
      card(uid, 280, 330, "Технический директор", "", "Разработка, качество", "green"),
      card(uid, 560, 330, "Директор по продажам", "", "Клиенты, выручка", "green"),
    ];
    var lines = kids.map(function (k) {
      return line(uid, boss.id, "bottom", k.id, "top", { route: "elbow" });
    });
    return [boss].concat(kids, lines);
  }

  function build(id, ctx) {
    var uid = ctx.uid;
    if (id === "gantt") return gantt(uid, ctx.today);
    if (id === "org") return org(uid);
    return scheme(uid);
  }

  window.LeoBoardTemplates = { LIST: LIST, PREVIEW: PREVIEW, build: build };
})();
