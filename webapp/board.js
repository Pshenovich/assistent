/* Холст — лист заметки kind=board: доска в духе Miro.
 * Блоки текста, прямоугольники, ромбы, эллипсы, стикеры, линии и стрелки (прилипают к блокам
 * и едут за ними), карандаш, картинки; зум, панорама, выделение рамкой, группы, сетка и
 * направляющие, отмена, буфер обмена, мини-карта, экспорт PNG/SVG.
 *
 * Документ — JSON {v, elements: {id: {...}}} (assistant/lib/board_doc.py). Элементы рисуются
 * HTML-узлами в «мире» с CSS-трансформом (текст правится на месте, порядок слоёв — порядок узлов),
 * фигуры и линии внутри узлов — SVG. Геометрия — webapp/board-geom.js.
 *
 * LeoBoard.create({host, json, sheetId, readOnly, onChange(json), onNotify(text)}) →
 *   {getJson, setJson, setJsonKeepView, fitAll, exportPng, exportSvg, attachYjs, detachYjs,
 *    isYjsAttached, setRemoteCursors, destroy}
 */
(function () {
  "use strict";

  var G = window.LeoBoardGeom;
  var BL = window.LeoBoardBlocks;
  var TPL = window.LeoBoardTemplates;
  var INK = ["ink", "gray", "red", "orange", "yellow", "lime", "green", "teal", "cyan", "blue", "indigo", "purple", "pink", "brown"];
  var FILLS = ["none", "white", "gray", "red", "orange", "yellow", "lime", "green", "teal", "cyan", "blue", "indigo", "purple", "pink", "brown"];
  var STICKY = ["yellow", "orange", "pink", "green", "blue", "purple", "gray", "lime", "teal", "cyan", "indigo", "brown"];
  var COLOR_NAMES = {
    none: "Без заливки", white: "Белый", ink: "Чёрный", gray: "Серый", red: "Красный", orange: "Оранжевый",
    yellow: "Жёлтый", green: "Зелёный", blue: "Синий", purple: "Фиолетовый", pink: "Розовый",
    lime: "Салатовый", teal: "Бирюзовый", cyan: "Голубой", indigo: "Индиго", brown: "Коричневый",
  };
  var FONT_SIZES = [{ v: 12, t: "S" }, { v: 16, t: "M" }, { v: 24, t: "L" }, { v: 36, t: "XL" }];
  var WIDTHS = [1, 2, 4, 6];
  var MIN_Z = 0.1;
  var MAX_Z = 4;
  var GRID = 20;
  // Фигуры блок-схем (доска): капсула, цилиндр, треугольники, параллелограммы — как ромб, свой контур.
  var FLOW = ["pill", "cylinder", "triangle", "triangleDown", "parallelogram", "parallelogramL"];
  var SHAPES = { rect: 1, diamond: 1, ellipse: 1, pill: 1, cylinder: 1, triangle: 1, triangleDown: 1, parallelogram: 1, parallelogramL: 1 };
  // Набор фигур в панели инструментов (одна кнопка с выпадающей сеткой) — по порядку.
  var SHAPE_MENU = ["rect", "rectSharp", "ellipse", "diamond", "pill", "cylinder", "triangle", "triangleDown", "parallelogram", "parallelogramL"];
  var SHAPE_RU = { rect: "Скруглённый прямоугольник", rectSharp: "Прямоугольник", ellipse: "Эллипс", diamond: "Ромб", pill: "Скруглённый блок", cylinder: "Цилиндр (база данных)",
    triangle: "Треугольник", triangleDown: "Треугольник вниз", parallelogram: "Параллелограмм (данные)", parallelogramL: "Параллелограмм влево" };
  // Где подпись внутри фигуры: доли рамки [слева, сверху, справа, снизу] (у цилиндра сверху — ещё крышка).
  var TEXT_BOX = { diamond: [0.22, 0, 0.22, 0], triangle: [0.24, 0.42, 0.24, 0.02], triangleDown: [0.24, 0.02, 0.24, 0.42],
    parallelogram: [0.18, 0, 0.18, 0], parallelogramL: [0.18, 0, 0.18, 0], pill: [0.1, 0, 0.1, 0] };
  var BOXED = { text: 1, rect: 1, diamond: 1, ellipse: 1, pill: 1, cylinder: 1, triangle: 1, triangleDown: 1, parallelogram: 1, parallelogramL: 1, sticky: 1, image: 1, table: 1, card: 1, gantt: 1, vector: 1, frame: 1 };
  // Составные блоки: свой HTML внутри узла и своя правка (таблица — редактор таблиц заметки).
  var BLOCKS = { table: 1, card: 1, gantt: 1 };
  var ALIGN_ICON = { left: "alignLeft", center: "alignCenter", right: "alignRight" };

  // Действия с выделенными блоками — те же иконки и подписи, что у плашки выделения в заметке
  // (webapp/note-comments.js showBubble), чтобы везде было одинаково.
  var NOTE_ACTS = [
    {
      id: "ai", label: "ИИ", cls: " lb-nact--ai",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
        '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>',
    },
    {
      id: "discuss", label: "Обсудить", cls: "",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
    },
    {
      id: "task", label: "Задача", cls: "",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
    },
    {
      id: "link", label: "Ссылка", cls: "",
      svg: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
        '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/></svg>',
    },
  ];

  /** Выравнивание текста: у текста по умолчанию влево, в фигурах и стикерах — по центру. */
  function alignOf(el) {
    var a = el.style && el.style.align;
    if (a === "left" || a === "center" || a === "right") return a;
    return el.type === "text" ? "left" : "center";
  }

  function isAlignable(el) {
    return el.type === "text" || !!SHAPES[el.type] || el.type === "sticky";
  }
  // Палец вместо мыши — ручки крупнее.
  var COARSE = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(pointer: coarse)").matches : false;
  var TEXTUAL = Object.assign({ text: 1, sticky: 1, line: 1 }, SHAPES);
  var STROKED = Object.assign({ line: 1, pen: 1, vector: 1 }, SHAPES);
  var IMAGE_MAX_SIDE = 1400;
  var CLIP_MIME = "application/x-leo-board";
  var CARD_PHOTO_SIDE = 400;
  // Заглушка картинки при вставке из Miro (файл картинки в буфер не попадает) — webapp/board-import.js.
  var IMAGE_SLOT_TEXT = "Картинка из Miro";
  // Новая таблица — как «Таблица» в заметке: 3×3 с шапкой.
  var TABLE_HTML = '<table class="note-editor-table"><tbody>' +
    "<tr>" + new Array(4).join('<th colspan="1" rowspan="1"><p></p></th>') + "</tr>" +
    new Array(3).join("<tr>" + new Array(4).join('<td colspan="1" rowspan="1"><p></p></td>') + "</tr>") +
    "</tbody></table>";

  var DEFAULTS = {
    text: { w: 240, h: 26, style: { fontSize: 16, textColor: "ink", align: "left" } },
    rect: { w: 180, h: 100, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    diamond: { w: 170, h: 120, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    pill: { w: 200, h: 90, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    cylinder: { w: 150, h: 130, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    triangle: { w: 170, h: 150, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    triangleDown: { w: 170, h: 150, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    parallelogram: { w: 200, h: 110, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    parallelogramL: { w: 200, h: 110, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    ellipse: { w: 170, h: 110, style: { stroke: "ink", fill: "none", strokeWidth: 2, fontSize: 16 } },
    sticky: { w: 200, h: 200, style: { fill: "yellow", fontSize: 16 } },
    line: { style: { stroke: "ink", strokeWidth: 2 } },
    pen: { style: { stroke: "ink", strokeWidth: 3 } },
    table: { w: 420, h: 130 },
    frame: { w: 393, h: 852, style: { fill: "#ffffff", radius: 0 } },
    card: { w: 220, h: 230, style: { fill: "purple" } },
  };

  var TOOLS = [
    { id: "select", key: "v", title: "Выделение (V)", icon: "cursor" },
    { id: "hand", key: "h", title: "Рука (H, пробел)", icon: "hand" },
    { id: "text", key: "t", title: "Текст (T)", icon: "text" },
    { id: "sticky", key: "s", title: "Стикер (S)", icon: "sticky" },
    // Фигуры — одной кнопкой с набором (group); клавиши R / D / O по-прежнему выбирают свою.
    { id: "rect", key: "r", title: "Скруглённый прямоугольник (R)", icon: "rect", group: "shape" },
    { id: "rectSharp", key: "", title: "Прямоугольник", icon: "rectSharp", group: "shape" },
    { id: "diamond", key: "d", title: "Ромб (D)", icon: "diamond", group: "shape" },
    { id: "ellipse", key: "o", title: "Эллипс (O)", icon: "ellipse", group: "shape" },
    { id: "pill", key: "", title: "Скруглённый блок", icon: "pill", group: "shape" },
    { id: "cylinder", key: "", title: "Цилиндр", icon: "cylinder", group: "shape" },
    { id: "triangle", key: "", title: "Треугольник", icon: "triangle", group: "shape" },
    { id: "triangleDown", key: "", title: "Треугольник вниз", icon: "triangleDown", group: "shape" },
    { id: "parallelogram", key: "", title: "Параллелограмм", icon: "parallelogram", group: "shape" },
    { id: "parallelogramL", key: "", title: "Параллелограмм влево", icon: "parallelogramL", group: "shape" },
    { id: "line", key: "l", title: "Линия (L)", icon: "line" },
    { id: "arrow", key: "a", title: "Стрелка (A)", icon: "arrow" },
    { id: "pen", key: "p", title: "Карандаш (P)", icon: "pen" },
    { id: "image", key: "", title: "Картинка", icon: "image" },
    { id: "table", key: "", title: "Таблица", icon: "table" },
  ];

  // Режим «Макет»: экраны приложения для разработки — свои инструменты, те же элементы.
  var TOOLS_DESIGN = [
    { id: "select", key: "v", title: "Выделение (V)", icon: "cursor" },
    { id: "hand", key: "h", title: "Рука (H, пробел)", icon: "hand" },
    { id: "frame", key: "f", title: "Фрейм (F)", icon: "frame" },
    { id: "text", key: "t", title: "Текст (T)", icon: "text" },
    { id: "rect", key: "r", title: "Прямоугольник (R)", icon: "rect" },
    { id: "ellipse", key: "o", title: "Эллипс (O)", icon: "ellipse" },
    { id: "line", key: "l", title: "Линия (L)", icon: "line" },
    { id: "arrow", key: "a", title: "Стрелка — переход между фреймами (A)", icon: "arrow" },
    { id: "path", key: "p", title: "Перо (P): клик — точка, протянуть — кривая, клик по первой — замкнуть, Enter — готово", icon: "penTool" },
    { id: "pen", key: "b", title: "Карандаш (B)", icon: "pen" },
    { id: "image", key: "", title: "Картинка", icon: "image" },
    { id: "table", key: "", title: "Таблица", icon: "table" },
  ];

  // Размеры экранов (логические точки, как в Figma).
  var DEVICES = [
    { id: "iphone", title: "iPhone 16", w: 393, h: 852 },
    { id: "iphone-pro-max", title: "iPhone 16 Pro Max", w: 440, h: 956 },
    { id: "android", title: "Android", w: 412, h: 915 },
    { id: "ipad", title: "iPad", w: 820, h: 1180 },
    { id: "laptop", title: "Ноутбук", w: 1280, h: 800 },
    { id: "desktop", title: "Десктоп", w: 1440, h: 1024 },
  ];

  function modeOf(json) {
    var data = json;
    if (!data || typeof data !== "object") {
      try {
        data = JSON.parse(String(json || "").trim() || "{}");
      } catch (_) {
        data = {};
      }
    }
    return data && data.mode === "design" ? "design" : "board";
  }

  var ICONS = {
    cursor: '<path d="M5 3l12 7-5.2 1.3L9.5 17z"/>',
    hand: '<path d="M7 11V5.5a1.5 1.5 0 013 0V10m0-.5v-5a1.5 1.5 0 013 0V10m0-3.5a1.5 1.5 0 013 0V12c0 3.3-2.2 6-5.5 6H10c-2 0-3.2-1-4.3-2.6L3.5 12a1.5 1.5 0 012.4-1.7L7 11.5"/>',
    text: '<path d="M4 5h12M10 5v11M7.5 16h5"/>',
    sticky: '<path d="M4 4h12v8l-4 4H4z"/><path d="M12 16v-4h4"/>',
    rect: '<rect x="3" y="5" width="14" height="10" rx="1.5"/>',
    diamond: '<path d="M10 2.5L17.5 10 10 17.5 2.5 10z"/>',
    ellipse: '<ellipse cx="10" cy="10" rx="7.5" ry="5.5"/>',
    pill: '<rect x="2.5" y="5.5" width="15" height="9" rx="4.5"/>',
    rectSharp: '<rect x="3" y="5" width="14" height="10"/>',
    cylinder: '<ellipse cx="10" cy="5" rx="6" ry="2"/><path d="M4 5v10c0 1.1 2.7 2 6 2s6-.9 6-2V5"/>',
    triangle: '<path d="M10 3.5l7 12.5H3z"/>',
    triangleDown: '<path d="M3 4h14l-7 12.5z"/>',
    parallelogram: '<path d="M6 5h11.5L14 15H2.5z"/>',
    parallelogramL: '<path d="M2.5 5H14l3.5 10H6z"/>',
    shapes: '<rect x="2.5" y="10" width="7" height="7" rx="1"/><circle cx="14" cy="6" r="3.5"/><path d="M14 11l3.5 6h-7z"/>',
    line: '<path d="M4 16L16 4"/>',
    arrow: '<path d="M4 16L16 4M9 4h7v7"/>',
    pen: '<path d="M13.5 3.5l3 3L7 16H4v-3z"/>',
    image: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><circle cx="7.5" cy="8.5" r="1.5"/><path d="M17 13l-4-4-7 7"/>',
    undo: '<path d="M7 5L3.5 8.5 7 12"/><path d="M3.5 8.5H12a4.5 4.5 0 010 9H9"/>',
    redo: '<path d="M13 5l3.5 3.5L13 12"/><path d="M16.5 8.5H8a4.5 4.5 0 000 9h3"/>',
    plus: '<path d="M10 4v12M4 10h12"/>',
    minus: '<path d="M4 10h12"/>',
    fit: '<path d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4"/>',
    grid: '<path d="M3 7.5h14M3 12.5h14M7.5 3v14M12.5 3v14"/>',
    map: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><rect x="9" y="9" width="5" height="4"/>',
    download: '<path d="M10 3v10M6 9l4 4 4-4M4 16h12"/>',
    trash: '<path d="M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10"/>',
    copy: '<rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V4H4v9h3"/>',
    front: '<rect x="7" y="7" width="9" height="9" rx="1"/><path d="M4 13V4h9"/>',
    back: '<rect x="4" y="4" width="9" height="9" rx="1"/><path d="M16 7v9H7"/>',
    group: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/><path d="M2 2h16v16H2z" stroke-dasharray="2 2"/>',
    ungroup: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>',
    arrowStart: '<path d="M17 10H4M8 6l-4 4 4 4"/>',
    arrowEnd: '<path d="M3 10h13M12 6l4 4-4 4"/>',
    straight: '<path d="M3 16L17 4"/>',
    curve: '<path d="M3 16C3 6 17 14 17 4"/>',
    elbow: '<path d="M3 16h7V4h7"/>',
    unbend: '<path d="M3 15h4l3-7 3 7h4" stroke-dasharray="2 2.2"/><path d="M3 15h14"/>',
    alignLeft: '<path d="M3 5h14M3 10h9M3 15h12"/>',
    alignCenter: '<path d="M3 5h14M5.5 10h9M4 15h12"/>',
    alignRight: '<path d="M3 5h14M8 10h9M5 15h12"/>',
    more: '<circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/>',
    table: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M3 8h14M3 12h14M8 4v12M12.5 4v12"/>',
    templates: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="3" width="6" height="6" rx="1"/><rect x="3" y="11" width="6" height="6" rx="1"/><path d="M14 11v6M11 14h6"/>',
    photo: '<rect x="3" y="5" width="14" height="11" rx="1.5"/><circle cx="10" cy="10.5" r="2.8"/><path d="M7.5 5l1-1.6h3l1 1.6"/>',
    alL: '<path d="M4 3.5v13"/><path d="M8 7.5h8M8 12.5h5" stroke-width="2.4"/>',
    alCH: '<path d="M10 3.5v13"/><path d="M5.5 7.5h9M7.5 12.5h5" stroke-width="2.4"/>',
    alR: '<path d="M16 3.5v13"/><path d="M4 7.5h8M7 12.5h5" stroke-width="2.4"/>',
    alT: '<path d="M3.5 4h13"/><path d="M7.5 8v8M12.5 8v5" stroke-width="2.4"/>',
    alCV: '<path d="M3.5 10h13"/><path d="M7.5 5.5v9M12.5 7.5v5" stroke-width="2.4"/>',
    alB: '<path d="M3.5 16h13"/><path d="M7.5 4v8M12.5 7v5" stroke-width="2.4"/>',
    distH: '<path d="M4 4v12M16 4v12"/><path d="M10 7v6" stroke-width="2.4"/>',
    distV: '<path d="M4 4h12M4 16h12"/><path d="M7 10h6" stroke-width="2.4"/>',
    penTool: '<path d="M10 2.5l5 7-5 8-5-8z"/><circle cx="10" cy="10" r="1.3"/><path d="M10 11.3v6.2"/>',
    corners: '<path d="M3 8V5a2 2 0 012-2h3M12 3h3a2 2 0 012 2v3M17 12v3a2 2 0 01-2 2h-3M8 17H5a2 2 0 01-2-2v-3"/>',
    eyedrop: '<path d="M13.5 3.5l3 3-2 2-3-3z"/><path d="M11.5 5.5l-7 7V16h3.5l7-7"/>',
    noPhoto: '<rect x="3" y="5" width="14" height="11" rx="1.5"/><path d="M4 4l12 13"/>',
    child: '<rect x="6.5" y="2.5" width="7" height="5" rx="1"/><rect x="6.5" y="12.5" width="7" height="5" rx="1"/><path d="M10 7.5v5"/>',
    taskAdd: '<path d="M3 6h8M3 10h11M3 14h6"/><path d="M15 12v6M12 15h6"/>',
    taskDel: '<path d="M3 6h8M3 10h11M3 14h6"/><path d="M13 13l4 4M17 13l-4 4"/>',
    frame: '<path d="M6 3v14M14 3v14M3 6h14M3 14h14"/>',
    modeBoard: '<rect x="3" y="4" width="6" height="5" rx="1"/><rect x="11" y="11" width="6" height="5" rx="1"/><path d="M9 6.5h2.5v4.5"/>',
    modeDesign: '<rect x="5.5" y="2.5" width="9" height="15" rx="2"/><path d="M9 15h2"/>',
    opacity: '<rect x="3.5" y="3.5" width="13" height="13" rx="2"/><path d="M3.5 10h13M10 3.5v13" stroke-dasharray="1.5 2"/>',
    radius: '<path d="M4 16V9a5 5 0 015-5h7"/>',
    cTL: '<path d="M5 15V9a4 4 0 014-4h6"/>',
    cTR: '<path d="M5 5h6a4 4 0 014 4v6"/>',
    cBR: '<path d="M15 5v6a4 4 0 01-4 4H5"/>',
    cBL: '<path d="M15 15H9a4 4 0 01-4-4V5"/>',
    eye: '<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/><circle cx="10" cy="10" r="2.2"/>',
    eyeOff: '<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/><path d="M3.5 3.5l13 13"/>',
    plus: '<path d="M10 4.5v11M4.5 10h11"/>',
    minus: '<path d="M4.5 10h11"/>',
    weight: '<path d="M4 5.5h12M4 10h12M4 14.5h12" /><path d="M4 14.5h12" stroke-width="2.6"/>',
    group: '<rect x="3" y="3" width="14" height="14" rx="2" stroke-dasharray="2 2"/><rect x="6" y="6" width="4" height="4" rx="1"/><rect x="10" y="10" width="4" height="4" rx="1"/>',
    ungroup: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>',
    layers: '<path d="M10 3l7 3.5-7 3.5-7-3.5z"/><path d="M3 10l7 3.5 7-3.5M3 13.5l7 3.5 7-3.5"/>',
    caret: '<path d="M7.5 5l5 5-5 5"/>',
    lineH: '<path d="M3 4h14M3 16h14"/><path d="M10 7v6M8 8.5l2-2 2 2M8 11.5l2 2 2-2"/>',
    letterS: '<path d="M3 4v12M17 4v12"/><path d="M7 14l3-8 3 8M8.2 11h3.6"/>',
    vaT: '<path d="M4 4h12"/><path d="M10 7v9M7 10l3-3 3 3"/>',
    vaM: '<path d="M4 10h12"/><path d="M10 3v4M10 13v4M8 5l2 2 2-2M8 15l2-2 2 2"/>',
    vaB: '<path d="M4 16h12"/><path d="M10 4v9M7 10l3 3 3-3"/>',
    gridFlow: '<rect x="3.5" y="3.5" width="5" height="5" rx="1"/><rect x="11.5" y="3.5" width="5" height="5" rx="1"/><rect x="3.5" y="11.5" width="5" height="5" rx="1"/><rect x="11.5" y="11.5" width="5" height="5" rx="1"/>',
    wrap: '<path d="M3 6h11a3 3 0 010 6H8"/><path d="M10 9.5L7.5 12l2.5 2.5"/><path d="M3 15h3"/>',
    cols: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M8 4v12M12 4v12"/>',
    lock: '<rect x="5" y="9" width="10" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 016 0V9"/>',
    unlock: '<rect x="5" y="9" width="10" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 015.6-1.5"/>',
    autoLayout: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M7 7h6M7 10h6M7 13h6"/>',
    free: '<rect x="3.5" y="3.5" width="5" height="5" rx="1"/><rect x="11.5" y="11.5" width="5" height="5" rx="1"/><path d="M8.5 6h3v2M11.5 14h-3v-2"/>',
    padL: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M6.5 7v6"/>',
    padR: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M13.5 7v6"/>',
    padT: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M7 7h6"/>',
    padB: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M7 13h6"/>',
    padAll: '<rect x="3" y="3" width="14" height="14" rx="1.5"/><rect x="6.5" y="6.5" width="7" height="7" rx="1" stroke-dasharray="1.5 1.5"/>',
    dirV: '<path d="M10 3v14M6 13l4 4 4-4"/>',
    dirH: '<path d="M3 10h14M13 6l4 4-4 4"/>',
    gapV: '<path d="M4 4h12M4 16h12"/><path d="M10 7v6"/>',
    gapH: '<path d="M4 4v12M16 4v12"/><path d="M7 10h6"/>',
    padH: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M6 7v6M14 7v6"/>',
    padV: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M6 7h8M6 13h8"/>',
    blurFx: '<circle cx="10" cy="10" r="6.5" stroke-dasharray="2 2.2"/><circle cx="10" cy="10" r="2.5"/>',
    link: '<path d="M8.5 11.5a3 3 0 004.2 0l2.6-2.6a3 3 0 00-4.2-4.2l-1 1"/><path d="M11.5 8.5a3 3 0 00-4.2 0l-2.6 2.6a3 3 0 004.2 4.2l1-1"/>',
    flipH: '<path d="M10 3.5v13"/><path d="M7 6.5L3.5 10 7 13.5zM13 6.5l3.5 3.5-3.5 3.5z"/>',
    flipV: '<path d="M3.5 10h13"/><path d="M6.5 7L10 3.5 13.5 7zM6.5 13l3.5 3.5 3.5-3.5z"/>',
    rot90: '<path d="M10 7.5l4 4-4 4-4-4z"/><path d="M6 6.5a5.5 5.5 0 018-.5"/><path d="M14.5 3.5v3h-3"/>',
    angle: '<path d="M4.5 15.5h11M4.5 15.5l8-8"/><path d="M9.5 15.5a5 5 0 00-1.5-3.5"/>',
  };

  function icon(name) {
    return (
      '<svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || "") + "</svg>"
    );
  }

  function uid() {
    return "e" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function clone(o) {
    return o == null ? null : JSON.parse(JSON.stringify(o));
  }

  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function r2(v) {
    return Math.round((Number(v) || 0) * 100) / 100;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function clamp(v, a, b) {
    return Math.max(a, Math.min(b, v));
  }

  // Цвет — ключ палитры (красится под тему) или точный «#rrggbb» (из Miro / FigJam — как в оригинале).
  var HEX_RE = /^#[0-9a-f]{6}$/i;

  function isHex(key) {
    return typeof key === "string" && HEX_RE.test(key);
  }

  function inkVar(key) {
    if (isHex(key)) return key;
    return "var(--lb-ink-" + (INK.indexOf(key) >= 0 || key === "white" ? key : "ink") + ")";
  }

  function fillVar(key) {
    if (!key || key === "none") return "none";
    if (isHex(key)) return key;
    return "var(--lb-fill-" + (FILLS.indexOf(key) >= 0 ? key : "white") + ")";
  }

  // Заливки палитры — одни и те же в обеих темах (board.css --lb-fill-*).
  var FILL_HEX = {
    white: "#ffffff", gray: "#f1f3f5", red: "#ffe3e3", orange: "#ffe8cc", yellow: "#fff3bf", lime: "#e9fac8", green: "#d3f9d8",
    teal: "#c3fae8", cyan: "#c5f6fa", blue: "#d0ebff", indigo: "#dbe4ff", purple: "#e5dbff", pink: "#ffdeeb", brown: "#efe3d6",
  };
  var TEXT_DARK = "#1f2328";
  var TEXT_LIGHT = "#ffffff";

  function relLum(hex) {
    var c = [1, 3, 5].map(function (i) {
      var v = parseInt(hex.slice(i, i + 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  /** Цвет текста по заливке (как во Figma): если цвет текста не выбран вручную — тёмный на светлой
   * заливке, белый на тёмной. null — заливки нет (текст по теме). */
  function autoTextColor(el) {
    var st = (el && el.style) || {};
    if (st.textColor && st.textColor !== "ink") return null;
    if (el.type === "sticky" || st.fillOff || (st.fillOpacity != null && Number(st.fillOpacity) < 0.5)) return null;
    var hex = null;
    var g = st.fillGrad && /#[0-9a-fA-F]{6}/.exec(String(st.fillGrad));
    if (g) hex = g[0];
    else if (isHex(st.fill)) hex = st.fill;
    else if (FILL_HEX[st.fill]) hex = FILL_HEX[st.fill];
    if (!hex) return null;
    var L = relLum(hex.toLowerCase());
    // Какой из двух цветов контрастнее на этой заливке (по WCAG).
    return 1.05 / (L + 0.05) > (L + 0.05) / (relLum(TEXT_DARK) + 0.05) ? TEXT_LIGHT : TEXT_DARK;
  }

  function stickyVar(key) {
    if (isHex(key)) return key;
    return "var(--lb-sticky-" + (STICKY.indexOf(key) >= 0 ? key : "yellow") + ")";
  }

  /** Толщина рамки фигуры: 0 — без рамки (у блоков из Miro бывает). */
  function strokeW(st, def) {
    var v = Number(st && st.strokeWidth);
    return st && st.strokeWidth != null && isFinite(v) ? v : def;
  }

  /** Контуры вектора (иконка из FigJam) в его системе координат vb; цвет и толщина из style — для всех. */
  function vectorPaths(el, paint) {
    var st = el.style || {};
    return (el.paths || []).map(function (p) {
      // Заливка из панели ложится и на замкнутые контуры без своей заливки (нарисованные пером).
      var closed = /Z\s*$/i.test(String(p.d));
      var fill = p.fill ? paint.fill(st.fill && st.fill !== "none" ? st.fill : p.fill) : st.fill && st.fill !== "none" && closed ? paint.fill(st.fill) : "none";
      if ((p.fill && st.fill === "none") || st.fillOff) fill = "none";
      else if (paint.grad && paint.grad.fill && fill !== "none") fill = paint.grad.fill;
      var stroke = p.stroke && !st.strokeOff && !(st.strokeWidth === 0) ? paint.ink(st.stroke || p.stroke) : null;
      if (stroke && paint.grad && paint.grad.stroke) stroke = paint.grad.stroke;
      var a = ' fill="' + (fill || "none") + '"';
      if (stroke) {
        a += ' stroke="' + stroke + '" stroke-width="' + (st.strokeWidth != null ? Number(st.strokeWidth) : p.sw || 1) + '"';
        if (p.cap) a += ' stroke-linecap="' + p.cap + '"';
        if (p.join) a += ' stroke-linejoin="' + p.join + '"';
      }
      if (p.rule === "evenodd") a += ' fill-rule="evenodd"';
      a += opacityAttrs(st);
      return '<path d="' + String(p.d).replace(/[^MLCQZmlcqz0-9.,\s-]/g, "") + '"' + a + "/>";
    }).join("");
  }

  // ---- шрифты макета: семейства с кириллицей (Google Fonts, грузятся по требованию) ----
  // w — начертания: [от, до] у вариативных, список — у обычных.
  var FONTS = [
    { f: "Inter", w: [100, 900] }, { f: "Roboto", w: [100, 900] }, { f: "Open Sans", w: [300, 800] }, { f: "Montserrat", w: [100, 900] },
    { f: "Manrope", w: [200, 800] }, { f: "Golos Text", w: [400, 900] }, { f: "Onest", w: [100, 900] }, { f: "Rubik", w: [300, 900] },
    { f: "Nunito", w: [200, 900] }, { f: "Raleway", w: [100, 900] }, { f: "PT Sans", w: "400;700" }, { f: "PT Serif", w: "400;700" },
    { f: "Lora", w: [400, 700] }, { f: "Playfair Display", w: [400, 900] }, { f: "Merriweather", w: "300;400;700;900" },
    { f: "Unbounded", w: [200, 900] }, { f: "JetBrains Mono", w: [100, 800] },
  ];
  var WEIGHTS = [[100, "Thin"], [200, "ExtraLight"], [300, "Light"], [400, "Regular"], [500, "Medium"], [600, "SemiBold"], [700, "Bold"], [800, "ExtraBold"], [900, "Black"]];
  var FONT_RE = /^[A-Za-z0-9 ]{1,40}$/;
  var fontLoads = {};

  function fontInfo(f) {
    return FONTS.filter(function (x) {
      return x.f === f;
    })[0] || null;
  }

  function fontWeights(f) {
    var fi = fontInfo(f);
    if (!fi) return WEIGHTS.map(function (w) {
      return w[0];
    });
    if (typeof fi.w === "string") return fi.w.split(";").map(Number);
    return WEIGHTS.map(function (w) {
      return w[0];
    }).filter(function (w) {
      return w >= fi.w[0] && w <= fi.w[1];
    });
  }

  function fontStack(f) {
    if (!f || !FONT_RE.test(f)) return "";
    var mono = /mono/i.test(f);
    var serif = /serif|lora|playfair|merriweather/i.test(f) && !/sans/i.test(f);
    return '"' + f + '", ' + (mono ? "ui-monospace, monospace" : serif ? "Georgia, serif" : "system-ui, sans-serif");
  }

  /** Подключить шрифт (один раз); cb — когда загрузился (пересчитать высоту текстов). */
  function fontSpecs(f) {
    var fi = fontInfo(f);
    if (fi) return [typeof fi.w === "string" ? fi.w : fi.w[0] + ".." + fi.w[1]];
    // Шрифт не из списка (пришёл из Figma): пробуем Google Fonts с разными наборами начертаний.
    return ["100..900", "300;400;500;600;700", "400;500;600;700", "400;700", ""];
  }

  function ensureFont(f, cb) {
    if (!f || !FONT_RE.test(f) || typeof document === "undefined") return;
    if (!fontLoads[f]) {
      var specs = fontSpecs(f);
      fontLoads[f] = new Promise(function (resolve) {
        var tryAt = function (i) {
          if (i >= specs.length) return resolve();
          var link = document.createElement("link");
          link.rel = "stylesheet";
          link.href = "https://fonts.googleapis.com/css2?family=" + encodeURIComponent(f).replace(/%20/g, "+") + (specs[i] ? ":wght@" + specs[i] : "") + "&display=swap";
          link.onload = function () {
            Promise.all([300, 400, 500, 600, 700].map(function (w) {
              return document.fonts && document.fonts.load ? document.fonts.load(w + ' 16px "' + f + '"', "Аб").catch(function () {}) : null;
            })).then(resolve, resolve);
          };
          link.onerror = function () {
            link.remove();
            tryAt(i + 1);
          };
          document.head.appendChild(link);
        };
        tryAt(0);
      });
    }
    if (cb) fontLoads[f].then(cb);
  }

  /** Текст с учётом регистра (Figma: ВСЕ ПРОПИСНЫЕ / строчные / Каждое Слово). */
  function casedText(text, c) {
    var t = String(text || "");
    if (c === "upper") return t.toUpperCase();
    if (c === "lower") return t.toLowerCase();
    if (c === "title") return t.replace(/(^|[\s\-«"(])([^\s])/g, function (_, a, b) {
      return a + b.toUpperCase();
    });
    return t;
  }

  /** Градиент заливки / обводки: «linear|radial УГОЛ #rrggbbaa@ПОЗИЦИЯ …» (угол 0 — слева направо). */
  var GRAD_RE = /^(linear|radial) (-?\d{1,3}) ((?:#[0-9a-f]{8}@\d{1,3} ?){2,8})$/i;
  function parseGrad(str) {
    var m = GRAD_RE.exec(String(str || "").trim());
    if (!m) return null;
    var stops = m[3].trim().split(" ").map(function (t) {
      var q = t.split("@");
      return { c: q[0].slice(0, 7).toLowerCase(), a: parseInt(q[0].slice(7), 16) / 255, p: Math.max(0, Math.min(100, Number(q[1]))) };
    }).sort(function (a, b) {
      return a.p - b.p;
    });
    return { type: m[1].toLowerCase(), angle: Number(m[2]), stops: stops };
  }

  function gradStr(g) {
    return g.type + " " + Math.round(g.angle) + " " + g.stops.slice().sort(function (a, b) {
      return a.p - b.p;
    }).map(function (t) {
      return t.c + ("0" + Math.round(Math.max(0, Math.min(1, t.a)) * 255).toString(16)).slice(-2) + "@" + Math.round(t.p);
    }).join(" ");
  }

  function gradCss(g) {
    var stops = g.stops.map(function (t) {
      var r = parseInt(t.c.slice(1, 3), 16);
      var gg = parseInt(t.c.slice(3, 5), 16);
      var b = parseInt(t.c.slice(5, 7), 16);
      return "rgba(" + r + "," + gg + "," + b + "," + Math.round(t.a * 100) / 100 + ") " + t.p + "%";
    }).join(", ");
    return g.type === "radial" ? "radial-gradient(circle, " + stops + ")" : "linear-gradient(" + (g.angle + 90) + "deg, " + stops + ")";
  }

  /** <defs> с градиентом (в долях рамки фигуры). */
  function gradDef(id, g) {
    var stops = g.stops.map(function (t) {
      return '<stop offset="' + t.p + '%" stop-color="' + t.c + '"' + (t.a < 1 ? ' stop-opacity="' + Math.round(t.a * 1000) / 1000 + '"' : "") + "/>";
    }).join("");
    if (g.type === "radial") return '<radialGradient id="' + id + '" cx="0.5" cy="0.5" r="0.5">' + stops + "</radialGradient>";
    return '<linearGradient id="' + id + '" x1="0" y1="0.5" x2="1" y2="0.5" gradientTransform="rotate(' + g.angle + ' 0.5 0.5)">' + stops + "</linearGradient>";
  }

  /** Заливка/обводка элемента градиентом: {defs, fill?, stroke?} — url(#…) вместо цвета. */
  function gradPaint(el) {
    var st = el.style || {};
    var out = { defs: "" };
    var fg = !st.fillOff && parseGrad(st.fillGrad);
    var sg = !st.strokeOff && parseGrad(st.strokeGrad);
    if (fg) {
      out.defs += gradDef("lbg-" + el.id + "-f", fg);
      out.fill = "url(#lbg-" + el.id + "-f)";
    }
    if (sg) {
      out.defs += gradDef("lbg-" + el.id + "-s", sg);
      out.stroke = "url(#lbg-" + el.id + "-s)";
    }
    if (out.defs) out.defs = "<defs>" + out.defs + "</defs>";
    return out;
  }

  /** Непрозрачность заливки и обводки отдельно от всего объекта (как во Figma). */
  function opacityAttrs(st) {
    var a = "";
    if (st.fillOpacity != null && Number(st.fillOpacity) < 1) a += ' fill-opacity="' + Math.max(0, Number(st.fillOpacity)) + '"';
    if (st.strokeOpacity != null && Number(st.strokeOpacity) < 1) a += ' stroke-opacity="' + Math.max(0, Number(st.strokeOpacity)) + '"';
    return a;
  }

  /** Обводка внутрь / наружу: отдельный контур, сдвинутый на полтолщины (по центру — обычная обводка SVG). */
  function alignedStroke(type, w, h, r, sw, align, attrs) {
    var d = (align === "inside" ? 1 : -1) * sw / 2;
    if (type === "ellipse") {
      return '<ellipse cx="' + w / 2 + '" cy="' + h / 2 + '" rx="' + Math.max(0, w / 2 - d) + '" ry="' + Math.max(0, h / 2 - d) + '" fill="none"' + attrs + ' pointer-events="none"/>';
    }
    var rr = r.map(function (v) {
      return v ? Math.max(0, v - d) : 0;
    });
    var shape = roundedShape(Math.max(0, w - 2 * d), Math.max(0, h - 2 * d), rr, ' fill="none"' + attrs + ' pointer-events="none"');
    return '<g transform="translate(' + d + " " + d + ')">' + shape + "</g>";
  }

  /** Скругления углов [левый верхний, правый верхний, правый нижний, левый нижний]:
   * style.radii «tl tr br bl» (каждый угол свой), иначе style.radius, иначе по умолчанию. */
  var RADII_RE = /^(\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)$/;
  function radiiOf(st, w, h, def) {
    var m = st && RADII_RE.exec(String(st.radii || ""));
    var r;
    if (m) r = [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4])];
    else {
      var one = st && st.radius != null && isFinite(st.radius) ? Number(st.radius) : def != null ? def : Math.min(10, w / 4, h / 4);
      r = [one, one, one, one];
    }
    var lim = Math.max(0, Math.min(w, h) / 2);
    return r.map(function (v) {
      return Math.max(0, Math.min(lim, v));
    });
  }

  /** Контур прямоугольника со своими скруглениями углов (одинаковые — обычный rect). */
  function roundedPathD(x, y, w, h, r) {
    return "M" + (x + r[0]) + " " + y + "H" + (x + w - r[1]) + (r[1] ? "A" + r[1] + " " + r[1] + " 0 0 1 " + (x + w) + " " + (y + r[1]) : "") +
      "V" + (y + h - r[2]) + (r[2] ? "A" + r[2] + " " + r[2] + " 0 0 1 " + (x + w - r[2]) + " " + (y + h) : "") +
      "H" + (x + r[3]) + (r[3] ? "A" + r[3] + " " + r[3] + " 0 0 1 " + x + " " + (y + h - r[3]) : "") +
      "V" + (y + r[0]) + (r[0] ? "A" + r[0] + " " + r[0] + " 0 0 1 " + (x + r[0]) + " " + y : "") + "Z";
  }

  function roundedShape(w, h, r, attrs) {
    if (r[0] === r[1] && r[1] === r[2] && r[2] === r[3]) {
      return '<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="' + r[0] + '"' + attrs + "/>";
    }
    return '<path d="' + roundedPathD(0, 0, w, h, r) + '"' + attrs + "/>";
  }

  /** Скругление прямоугольника: своё (style.radius) или по умолчанию. */
  function rectRadius(st, w, h) {
    var r = st && st.radius != null ? Number(st.radius) : NaN;
    return isFinite(r) ? Math.max(0, Math.min(r, w / 2, h / 2)) : Math.min(10, w / 4, h / 4);
  }

  function dashArray(dash, sw) {
    if (dash === "dashed") return sw * 4 + " " + sw * 3;
    if (dash === "dotted") return "0.1 " + sw * 2.5;
    return "";
  }

  /** Элемент в порядке полей, как его пишет сервер (board_doc._clean_element). */
  function serializeEl(el, getEl, pathOpts) {
    var o = { id: el.id, type: el.type, x: r2(el.x), y: r2(el.y), w: r2(el.w), h: r2(el.h), z: r2(el.z) };
    if (el.rot) o.rot = r2(el.rot);
    if (el.group) o.group = el.group;
    if (el.frame) o.frame = el.frame;
    if (el.grow) o.grow = el.grow;
    if (el.pin) o.pin = el.pin;
    if (el.autoW) o.autoW = true;
    if (el.hidden) o.hidden = true;
    if (el.locked) o.locked = true;
    ["minW", "maxW", "minH", "maxH"].forEach(function (k) {
      if (el[k] > 0) o[k] = r2(el[k]);
    });
    if (el.name && el.type !== "frame") o.name = String(el.name).slice(0, 120);
    if (el.ratio) o.ratio = true;
    if (el.text) o.text = String(el.text);
    if (el.style) {
      var st = {};
      ["fill", "stroke", "strokeWidth", "dash", "fontSize", "align", "textColor", "bold", "radius", "radii", "highlight", "opacity", "shadow",
        "fillOpacity", "strokeOpacity", "fillOff", "strokeOff", "strokeAlign", "fillGrad", "strokeGrad", "blur", "bgBlur",
        "fontFamily", "fontWeight", "lineHeight", "letterSpacing", "valign", "deco", "textCase"].forEach(function (k) {
        if (el.style[k] != null) st[k] = el.style[k];
      });
      if (Object.keys(st).length) o.style = st;
    }
    if (el.type === "line") {
      var res = G.resolveLine(el, getEl);
      o.points = [[r2(res.p0.x), r2(res.p0.y)], [r2(res.p1.x), r2(res.p1.y)]];
      if (el.start) o.start = el.start;
      if (el.end) o.end = el.end;
      if (el.arrowStart) o.arrowStart = true;
      if (el.arrowEnd) o.arrowEnd = true;
      o.route = el.route || "straight";
      if (el.bend) o.bend = r2(el.bend);
      if (el.labelT != null && isFinite(el.labelT)) o.labelT = Math.round(el.labelT * 1000) / 1000;
      if (el.waypoints && el.waypoints.length) {
        o.waypoints = el.waypoints.map(function (q) {
          return [r2(q[0]), r2(q[1])];
        });
      }
      o.d = G.linePath(el, res, pathOpts ? pathOpts(el) : null).d;
    } else if (el.type === "pen") {
      o.points = (el.points || []).map(function (p) {
        return [r2(p[0]), r2(p[1])];
      });
    } else if (el.type === "image") {
      o.src = el.src;
    } else if (el.type === "vector") {
      o.vb = el.vb;
      o.paths = clone(el.paths || []);
    } else if (el.type === "frame") {
      if (el.name) o.name = String(el.name);
      if (el.device) o.device = el.device;
      if (el.layout) o.layout = el.layout;
      if (el.noclip) o.noclip = true;
    } else if (el.type === "table") {
      o.html = String(el.html || "");
    } else if (el.type === "card") {
      if (el.title) o.title = String(el.title);
      if (el.name) o.name = String(el.name);
      if (el.photo) o.photo = el.photo;
    } else if (el.type === "gantt") {
      o.start = el.start;
      o.unit = el.unit === "week" ? "week" : "day";
      o.span = el.span;
      o.tasks = clone(el.tasks || []);
    }
    if (el.type === "sticky" && el.author && (el.author.id || el.author.name)) o.author = el.author;
    return o;
  }

  /** Текст элемента для ИИ, ссылок и буфера: у блоков — их поля. */
  function elText(el) {
    if (!el) return "";
    if (el.type === "card") {
      var head = [el.title, el.name].filter(Boolean).join(" — ");
      return head && el.text ? head + ": " + el.text : head || String(el.text || "");
    }
    if (el.type === "gantt") {
      return (el.tasks || []).map(function (t) {
        return (t.name || "Задача") + (t.owner ? " (" + t.owner + ")" : "") + ": " + BL.fmtDay(t.start) + "–" + BL.fmtDay(t.end);
      }).join("\n");
    }
    if (el.type === "table") {
      try {
        var doc = new DOMParser().parseFromString(String(el.html || ""), "text/html");
        return Array.prototype.map.call(doc.querySelectorAll("tr"), function (tr) {
          return Array.prototype.map.call(tr.querySelectorAll("th, td"), function (c) {
            return c.textContent.trim();
          }).join(" | ");
        }).join("\n");
      } catch (_) {
        return "";
      }
    }
    return String(el.text || "");
  }

  function parseBoard(json) {
    var data = null;
    if (json && typeof json === "object") data = json;
    else {
      try {
        data = JSON.parse(String(json || "").trim() || "{}");
      } catch (_) {
        data = {};
      }
    }
    var raw = (data && data.elements) || {};
    var out = [];
    var list = Array.isArray(raw) ? raw : Object.keys(raw).map(function (k) {
      var e = raw[k];
      if (e && !e.id) e.id = k;
      return e;
    });
    list.forEach(function (e) {
      if (!e || !e.id || !e.type) return;
      var el = clone(e);
      delete el.d;
      el.x = Number(el.x) || 0;
      el.y = Number(el.y) || 0;
      el.w = Number(el.w) || 0;
      el.h = Number(el.h) || 0;
      el.z = Number(el.z) || 0;
      out.push(el);
    });
    return out;
  }

  function readFileAsDataUrl(file) {
    return new Promise(function (resolve) {
      var reader = new FileReader();
      reader.onload = function () {
        resolve(String(reader.result || ""));
      };
      reader.onerror = function () {
        resolve("");
      };
      reader.readAsDataURL(file);
    });
  }

  function loadImage(src) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = function () {
        resolve(img);
      };
      img.onerror = function () {
        resolve(null);
      };
      img.src = src;
    });
  }

  /** Картинка в документе — data URL: ужимаем длинную сторону и в JPEG (PNG с прозрачностью — PNG). */
  function compressImage(src, maxSide) {
    return loadImage(src).then(function (img) {
      if (!img) return null;
      var w = img.naturalWidth || img.width;
      var h = img.naturalHeight || img.height;
      if (!w || !h) return null;
      var k = Math.min(1, (maxSide || IMAGE_MAX_SIDE) / Math.max(w, h));
      var cw = Math.max(1, Math.round(w * k));
      var ch = Math.max(1, Math.round(h * k));
      var out = src;
      try {
        var canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        var ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, cw, ch);
        var alpha = false;
        if (/^data:image\/(png|gif|webp)/i.test(src)) {
          var px = ctx.getImageData(0, 0, cw, ch).data;
          for (var i = 3; i < px.length; i += 16) {
            if (px[i] < 250) {
              alpha = true;
              break;
            }
          }
        }
        var enc = alpha ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.84);
        if (enc && (enc.length < src.length || !/^data:image\/(png|jpe?g|webp|gif)/i.test(src))) out = enc;
      } catch (_) {}
      return { src: out, w: cw, h: ch };
    });
  }

  function downloadBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 4000);
  }

  function create(opts) {
    var o = opts || {};
    var host = o.host;
    var readOnly = !!o.readOnly;
    var sheetId = String(o.sheetId || "");
    var viewKey = sheetId ? "leo-board-view:" + sheetId : "";

    // ---- состояние ----
    var els = new Map();
    var nodes = new Map();
    var selected = new Set();
    var undoStack = [];
    var redoStack = [];
    var tx = null;
    var dirty = new Set();
    var orderDirty = true;
    var frame = 0;
    var view = { x: 0, y: 0, z: 1 };
    var tool = "select";
    var gesture = null;
    var pointers = new Map();
    var editing = null; // {id, node}
    var spaceDown = false;
    var showGrid = true;
    var showMap = (host.getBoundingClientRect().width || window.innerWidth) > 640;
    var guides = [];
    var snapTarget = null; // {el, side}
    var marquee = null;
    var lastTap = { id: "", t: 0, x: 0, y: 0 };
    var lastWpTap = { h: "", id: "", t: 0 };
    var lastStyle = {};
    var changeTimer = 0;
    var destroyed = false;
    var mapTimer = 0;
    var remoteCursors = [];
    var yjs = null; // {doc, map, observer}
    var autoFit = false;
    var pendingY = new Set();
    var yFlushHandle = 0;
    var tables = new Map(); // id → редактор таблицы (NoteRichEditor.mountTable)
    var mode = modeOf(o.json); // «board» — доска, «design» — макет экранов
    var gSel = null; // выбранная задача Ганта: {id, task}
    var penPath = null; // перо: контуры вектора, который рисуем или правим (см. «перо» ниже)
    var lastMove = null; // последняя точка курсора над доской (для Alt без движения мыши)
    var measure = null; // Alt в макете: расстояния от выделенного {a} до объекта под курсором / экрана {b}
    var ganttOn = new Map(); // id Ганта → был ли он единственным выделенным (тогда видна строка «+ Задача»)

    function getEl(id) {
      return els.get(id) || null;
    }

    function sortedEls() {
      return Array.from(els.values()).sort(function (a, b) {
        return (a.z || 0) - (b.z || 0) || (a.id < b.id ? -1 : 1);
      });
    }

    function topZ() {
      var m = 0;
      els.forEach(function (e) {
        if ((e.z || 0) > m) m = e.z || 0;
      });
      return Math.floor(m) + 1;
    }

    function bottomZ() {
      var m = Infinity;
      els.forEach(function (e) {
        if ((e.z || 0) < m) m = e.z || 0;
      });
      return (m === Infinity ? 0 : Math.floor(m)) - 1;
    }

    // ---- DOM ----
    var root = document.createElement("div");
    root.className = "leo-board" + (readOnly ? " is-readonly" : "");
    root.innerHTML =
      '<div class="lb-stage" tabindex="0" aria-label="Доска">' +
      '<div class="lb-world"></div>' +
      '<svg class="lb-overlay" xmlns="http://www.w3.org/2000/svg"></svg>' +
      "</div>" +
      (readOnly ? "" : '<div class="lb-tools" role="toolbar" aria-label="Инструменты"></div>') +
      (readOnly ? "" : '<div class="lb-history"></div>') +
      '<div class="lb-props hidden"></div>' +
      (readOnly ? "" : '<div class="lb-inspector hidden" aria-label="Свойства"></div>') +
      (readOnly ? "" : '<div class="lb-layers hidden" aria-label="Слои"></div>') +
      '<div class="lb-pop hidden"></div>' +
      '<canvas class="lb-minimap" width="180" height="120"></canvas>' +
      '<div class="lb-zoom"></div>' +
      '<div class="lb-empty-hint hidden' + (readOnly || !TPL ? "" : " has-start") + '">' +
      (readOnly
        ? "Доска пуста"
        : TPL
          ? '<div class="lb-start-title">С чего начнём?</div><div class="lb-start-cards"></div>' +
            '<div class="lb-start-hint">или выберите инструмент и начните рисовать</div>'
          : "Выберите инструмент или дважды нажмите на доску, чтобы добавить текст") +
      "</div>";
    host.appendChild(root);
    var stage = root.querySelector(".lb-stage");
    var world = root.querySelector(".lb-world");
    var overlay = root.querySelector(".lb-overlay");
    var toolsBar = root.querySelector(".lb-tools");
    var historyBar = root.querySelector(".lb-history");
    var propsBar = root.querySelector(".lb-props");
    var inspector = root.querySelector(".lb-inspector");
    var layersEl = root.querySelector(".lb-layers");
    var pop = root.querySelector(".lb-pop");
    var minimap = root.querySelector(".lb-minimap");
    var zoomBar = root.querySelector(".lb-zoom");
    var emptyHint = root.querySelector(".lb-empty-hint");

    function stageRect() {
      return stage.getBoundingClientRect();
    }

    function toWorld(cx, cy, rect) {
      var r = rect || stageRect();
      return G.pt((cx - r.left - view.x) / view.z, (cy - r.top - view.y) / view.z);
    }

    function toScreen(p) {
      return G.pt(p.x * view.z + view.x, p.y * view.z + view.y);
    }

    // ---- изменения и отмена ----
    function begin() {
      if (!tx) tx = { before: new Map(), depth: 0 };
      tx.depth++;
    }

    function touch(id) {
      if (tx && !tx.before.has(id)) tx.before.set(id, clone(els.get(id) || null));
    }

    function commit() {
      if (!tx) return;
      // Автолэйаут: перед закрытием шага — разложить затронутые экраны (тем же шагом отмены).
      if (tx.depth === 1 && !tx.laying) {
        tx.laying = true;
        try {
          relayout(Array.from(tx.before.keys()));
        } catch (_) {}
      }
      tx.depth--;
      if (tx.depth > 0) return;
      var entry = [];
      tx.before.forEach(function (before, id) {
        var after = clone(els.get(id) || null);
        if (!same(before, after)) entry.push({ id: id, before: before, after: after });
      });
      tx = null;
      if (entry.length) {
        undoStack.push(entry);
        if (undoStack.length > 200) undoStack.shift();
        redoStack = [];
      }
      renderHistoryBar();
    }

    function markDirty(id) {
      dirty.add(id);
      schedule();
    }

    function localChanged(id) {
      if (yjs) {
        pendingY.add(id);
        scheduleYFlush();
      }
      if (changeTimer) clearTimeout(changeTimer);
      changeTimer = setTimeout(emitChange, 250);
    }

    function emitChange() {
      changeTimer = 0;
      if (destroyed || typeof o.onChange !== "function") return;
      o.onChange(getJson());
      devSchedulePreviews();
    }

    function put(el) {
      if (readOnly) return;
      touch(el.id);
      var prev = els.get(el.id);
      if (prev && (prev.z || 0) !== (el.z || 0)) orderDirty = true;
      if (!prev) orderDirty = true;
      els.set(el.id, el);
      markDirty(el.id);
      localChanged(el.id);
    }

    function patch(id, fields) {
      var el = els.get(id);
      if (!el) return null;
      var next = Object.assign({}, el, fields);
      if (fields.style) next.style = Object.assign({}, el.style || {}, fields.style);
      put(next);
      return next;
    }

    function remove(id) {
      if (!els.has(id) || readOnly) return;
      touch(id);
      els.delete(id);
      selected.delete(id);
      orderDirty = true;
      markDirty(id);
      localChanged(id);
    }

    function applyEntry(entry, useAfter) {
      entry.forEach(function (ch) {
        var val = clone(useAfter ? ch.after : ch.before);
        if (val) els.set(ch.id, val);
        else {
          els.delete(ch.id);
          selected.delete(ch.id);
        }
        orderDirty = true;
        markDirty(ch.id);
        localChanged(ch.id);
      });
    }

    /** Скрыли / показали фрейм (в т. ч. отменой) — его содержимое перерисовать. */
    function redrawKidsOf(entry) {
      entry.forEach(function (ch) {
        if (!!(ch.before && ch.before.hidden) !== !!(ch.after && ch.after.hidden)) withFrameKids([ch.id]).forEach(markDirty);
      });
    }

    function undo() {
      finishEdit();
      dropPen();
      var entry = undoStack.pop();
      if (!entry) return;
      applyEntry(entry, false);
      redrawKidsOf(entry);
      redoStack.push(entry);
      renderHistoryBar();
    }

    function redo() {
      finishEdit();
      dropPen();
      var entry = redoStack.pop();
      if (!entry) return;
      setTimeout(function () {
        redrawKidsOf(entry);
      }, 0);
      applyEntry(entry, true);
      undoStack.push(entry);
      renderHistoryBar();
    }

    // ---- рендер ----
    function schedule() {
      if (frame || destroyed) return;
      frame = requestAnimationFrame(flush);
    }

    function flush() {
      frame = 0;
      if (destroyed) return;
      // Линии, прилипшие к изменённым блокам, тоже перерисовываем.
      if (dirty.size) {
        var extra = [];
        // Сдвинули или удалили блок — ломаные с обходом могут проложиться иначе.
        var blockDirty = false;
        dirty.forEach(function (id) {
          var d = els.get(id);
          if (!d || BOXED[d.type]) blockDirty = true;
        });
        els.forEach(function (el) {
          if (el.type !== "line") return;
          var a = el.start && el.start.el;
          var b = el.end && el.end.el;
          if ((a && dirty.has(a)) || (b && dirty.has(b)) || (blockDirty && autoRouted(el))) extra.push(el.id);
        });
        extra.forEach(function (id) {
          dirty.add(id);
        });
      }
      // Экран сдвинули или растянули — обрезка его содержимого пересчитывается.
      var framesDirty = {};
      dirty.forEach(function (id) {
        var d = els.get(id);
        if (d && d.type === "frame") framesDirty[id] = 1;
      });
      if (Object.keys(framesDirty).length) {
        withFrameKids(Object.keys(framesDirty)).forEach(function (id) {
          dirty.add(id);
        });
      }
      if (gSel && !(selected.size === 1 && selected.has(gSel.id))) {
        dirty.add(gSel.id);
        gSel = null;
      }
      els.forEach(function (el) {
        if (el.type !== "gantt") return;
        var on = selected.size === 1 && selected.has(el.id);
        if (!!ganttOn.get(el.id) !== on) {
          ganttOn.set(el.id, on);
          dirty.add(el.id);
        }
      });
      dirty.forEach(renderNode);
      dirty.clear();
      if (orderDirty) {
        orderDirty = false;
        sortedEls().forEach(function (el) {
          var n = nodes.get(el.id);
          if (n) world.appendChild(n);
        });
      }
      emptyHint.classList.toggle("hidden", els.size > 0 || !!gesture);
      renderOverlay();
      renderProps();
      scheduleMinimap();
    }

    function applyView() {
      world.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.z + ")";
      var g = GRID * view.z;
      if (showGrid && g >= 6) {
        stage.style.backgroundSize = g + "px " + g + "px";
        stage.style.backgroundPosition = (view.x % g) + "px " + (view.y % g) + "px";
        stage.classList.add("has-grid");
      } else {
        stage.classList.remove("has-grid");
      }
      stage.style.setProperty("--lb-zoom", String(view.z));
      // Меню открытой таблицы — обычного размера при любом масштабе.
      if (editing && editing.kind === "table") {
        var etb = tables.get(editing.id);
        if (etb && typeof etb.relayout === "function") etb.relayout();
      }
      renderZoomBar();
      renderOverlay();
      renderProps();
      scheduleMinimap();
      saveView();
    }

    var saveViewTimer = 0;
    function saveView() {
      if (!viewKey || readOnly) return;
      if (saveViewTimer) clearTimeout(saveViewTimer);
      saveViewTimer = setTimeout(function () {
        try {
          localStorage.setItem(viewKey, JSON.stringify(view));
        } catch (_) {}
      }, 400);
    }

    function loadView() {
      if (!viewKey || readOnly) return false;
      try {
        var v = JSON.parse(localStorage.getItem(viewKey) || "null");
        if (v && isFinite(v.x) && isFinite(v.y) && isFinite(v.z) && v.z > 0) {
          view = { x: v.x, y: v.y, z: clamp(v.z, MIN_Z, MAX_Z) };
          return true;
        }
      } catch (_) {}
      return false;
    }

    function textStyle(el, div) {
      var st = el.style || {};
      var fs = Number(st.fontSize) || 16;
      div.style.fontSize = fs + "px";
      div.style.fontWeight = st.fontWeight ? String(st.fontWeight) : st.bold ? "600" : "";
      var color = autoTextColor(el) || (st.textColor ? inkVar(st.textColor) : "");
      div.style.color = color;
      div.style.textAlign = alignOf(el);
      div.style.fontFamily = fontStack(st.fontFamily);
      if (st.fontFamily) ensureFont(st.fontFamily, refitFonts);
      div.style.lineHeight = Number(st.lineHeight) > 0 ? Number(st.lineHeight) + "px" : "";
      div.style.letterSpacing = st.letterSpacing ? Number(st.letterSpacing) / 100 + "em" : "";
      div.style.textDecoration = st.deco === "underline" ? "underline" : st.deco === "strike" ? "line-through" : "";
      div.style.textTransform = st.textCase === "upper" ? "uppercase" : st.textCase === "lower" ? "lowercase" : st.textCase === "title" ? "capitalize" : "";
      div.style.justifyContent = st.valign === "top" ? "flex-start" : st.valign === "bottom" ? "flex-end" : "";
      // Текст «ширина по содержимому» (из Figma) не переносится: другой шрифт не ломает строку.
      div.style.whiteSpace = el.autoW ? "pre" : "";
      // Подпись — в «полезной» части фигуры (у треугольника — в широкой, у цилиндра — под крышкой).
      var tb = TEXT_BOX[el.type];
      if (el.type === "cylinder") {
        var cap = G.capOf(el.w, el.h);
        div.style.padding = Math.round(cap * 2 + 4) + "px 20px " + Math.round(cap + 6) + "px";
      } else div.style.padding = tb ? Math.round(8 + tb[1] * el.h) + "px " + Math.round(16 + tb[2] * el.w) + "px " + Math.round(8 + tb[3] * el.h) + "px " + Math.round(16 + tb[0] * el.w) + "px" : "";
    }

    /** Шрифт догрузился — тексты с ним перерисовать (высота текста по содержимому меняется). */
    var refitPending = false;
    function refitFonts() {
      if (refitPending || destroyed) return;
      refitPending = true;
      setTimeout(function () {
        refitPending = false;
        els.forEach(function (el) {
          if (el.style && el.style.fontFamily) dirty.add(el.id);
        });
        schedule();
      }, 30);
    }

    function shapeSvg(el) {
      var st = el.style || {};
      var sw = strokeW(st, 2);
      var w = Math.max(1, el.w);
      var h = Math.max(1, el.h);
      var stroke = inkVar(st.stroke || "ink");
      var fill = st.fillOff ? "none" : fillVar(st.fill);
      if (st.strokeOff) sw = 0;
      var dash = dashArray(st.dash, sw);
      var strokeA = ' stroke="' + (sw > 0 ? stroke : "none") + '" stroke-width="' + sw + '"' + (dash ? ' stroke-dasharray="' + dash + '" stroke-linecap="round"' : "");
      var gp = gradPaint(el);
      if (gp.fill) fill = gp.fill;
      if (gp.stroke && sw > 0) {
        stroke = gp.stroke;
        strokeA = ' stroke="' + stroke + '" stroke-width="' + sw + '"' + (dash ? ' stroke-dasharray="' + dash + '" stroke-linecap="round"' : "");
      }
      var aligned = sw > 0 && (st.strokeAlign === "inside" || st.strokeAlign === "outside") && (el.type === "rect" || el.type === "ellipse");
      var common = ' fill="' + fill + '"' + (aligned ? ' stroke="none"' : strokeA) + opacityAttrs(st) + ' pointer-events="all" class="lb-hit"';
      var inner = "";
      if (el.type === "rect") {
        inner = roundedShape(w, h, radiiOf(st, w, h), common);
        if (aligned) inner += alignedStroke("rect", w, h, radiiOf(st, w, h), sw, st.strokeAlign, strokeA + opacityAttrs(st));
      } else if (el.type === "ellipse") {
        inner = '<ellipse cx="' + w / 2 + '" cy="' + h / 2 + '" rx="' + w / 2 + '" ry="' + h / 2 + '"' + common + "/>";
        if (aligned) inner += alignedStroke("ellipse", w, h, null, sw, st.strokeAlign, strokeA + opacityAttrs(st));
      } else if (el.type === "diamond" || FLOW.indexOf(el.type) >= 0) {
        var sp = G.shapePathD(el.type, w, h);
        inner = '<path d="' + sp.d + '"' + common + ' stroke-linejoin="round"/>' +
          (sp.line && sw > 0 ? '<path d="' + sp.line + '" fill="none"' + strokeA + opacityAttrs(st) + ' pointer-events="none"/>' : "");
      } else if (el.type === "sticky") {
        inner = '<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="3" fill="' + stickyVar(st.fill) + '" pointer-events="all" class="lb-hit lb-sticky-bg"/>';
      } else if (el.type === "image") {
        inner = '<image x="0" y="0" width="' + w + '" height="' + h + '" preserveAspectRatio="none" href="' + esc(el.src || "") + '" pointer-events="all" class="lb-hit"/>';
      } else if (el.type === "vector") {
        var vb = el.vb || [w, h];
        inner = '<rect x="0" y="0" width="' + w + '" height="' + h + '" fill="transparent" pointer-events="all" class="lb-hit"/>' +
          '<g transform="scale(' + w / Math.max(0.01, vb[0]) + " " + h / Math.max(0.01, vb[1]) + ')" pointer-events="none">' +
          vectorPaths(el, { fill: fillVar, ink: inkVar, grad: gp }) + "</g>";
      } else {
        // Текст: прозрачный, а с заливкой — плашка под текстом (подсветка из Miro).
        inner = '<rect x="0" y="0" width="' + w + '" height="' + h + '" rx="2" fill="' + (gp.fill || (st.fill && st.fill !== "none" && !st.fillOff ? fillVar(st.fill) : "transparent")) + '"' + opacityAttrs(st) + ' pointer-events="all" class="lb-hit"/>';
      }
      return '<svg class="lb-shape" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" overflow="visible">' + gp.defs + inner + "</svg>";
    }

    /** Ломаная без ручных изломов обходит блоки — им нужны рамки препятствий. */
    function autoRouted(ln) {
      return ln.type === "line" && ln.route === "elbow" && !ln.bend && !(ln.waypoints && ln.waypoints.length);
    }

    function pathOptsOf(ln) {
      if (!autoRouted(ln)) return null;
      var own = {};
      if (ln.start && ln.start.el) own[ln.start.el] = 1;
      if (ln.end && ln.end.el) own[ln.end.el] = 1;
      var obstacles = [];
      els.forEach(function (el) {
        if (!BOXED[el.type]) return;
        var b = G.elBounds(el, getEl);
        if (b) obstacles.push({ x: b.x, y: b.y, w: b.w, h: b.h, own: !!own[el.id] });
      });
      return { obstacles: obstacles };
    }

    function linePathOf(ln, res) {
      return G.linePath(ln, res || G.resolveLine(ln, getEl), pathOptsOf(ln));
    }

    function lineSvg(el) {
      var st = el.style || {};
      var sw = Number(st.strokeWidth) || 2;
      var stroke = inkVar(st.stroke || "ink");
      var res = G.resolveLine(el, getEl);
      var path = linePathOf(el, res);
      var dash = dashArray(st.dash, sw);
      var size = 8 + sw * 2.5;
      var heads = "";
      if (el.arrowEnd) heads += headPoly(G.arrowHead(res.p1, path.endDir, size), stroke);
      if (el.arrowStart) heads += headPoly(G.arrowHead(res.p0, path.startDir, size), stroke);
      return {
        svg:
          '<svg class="lb-shape" width="1" height="1" overflow="visible">' +
          '<path d="' + path.d + '" fill="none" stroke="transparent" stroke-width="' + Math.max(14, sw + 12) + '" pointer-events="stroke" class="lb-hit" stroke-linecap="round" stroke-linejoin="round"/>' +
          '<path d="' + path.d + '" fill="none" stroke="' + stroke + '" stroke-width="' + sw + '"' +
          (dash ? ' stroke-dasharray="' + dash + '"' : "") + ' stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>' +
          heads + "</svg>",
        mid: G.labelPoint(el, path),
      };
    }

    function headPoly(pts, color) {
      return (
        '<polygon points="' + pts.map(function (p) {
          return Math.round(p.x * 10) / 10 + "," + Math.round(p.y * 10) / 10;
        }).join(" ") + '" fill="' + color + '" stroke="' + color + '" stroke-width="1" stroke-linejoin="round" pointer-events="none"/>'
      );
    }

    function penSvg(el) {
      var st = el.style || {};
      var sw = Number(st.strokeWidth) || 3;
      var pts = (el.points || []).map(function (p) {
        return G.pt(el.x + p[0], el.y + p[1]);
      });
      var d = G.smoothPath(pts);
      return (
        '<svg class="lb-shape" width="1" height="1" overflow="visible">' +
        '<path d="' + d + '" fill="none" stroke="transparent" stroke-width="' + Math.max(14, sw + 10) + '" pointer-events="stroke" class="lb-hit" stroke-linecap="round"/>' +
        '<path d="' + d + '" fill="none" stroke="' + inkVar(st.stroke || "ink") + '" stroke-width="' + sw + '" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>' +
        "</svg>"
      );
    }

    function ensureText(node) {
      var t = node.querySelector(":scope > .lb-text");
      if (!t) {
        t = document.createElement("div");
        t.className = "lb-text";
        node.appendChild(t);
      }
      return t;
    }

    function renderNode(id) {
      renderNodeInner(id);
      var el = els.get(id);
      var node = nodes.get(id);
      if (el && node) clipToFrame(node, el);
      if (el && node) applyLook(node, el);
      // Вектор на время правки точек скрыт: его рисует перо поверх (см. penOverlay).
      if (node) node.style.visibility = penPath && penPath.editId === id ? "hidden" : "";
      // Скрытый в слоях — не показывается и не ловит клики.
      if (node) node.style.display = el && isHidden(el) ? "none" : "";
    }

    var SHADOW_RE = /^(-?\d{1,3}) (-?\d{1,3}) (\d{1,3}) (#[0-9a-f]{8})$/i;

    /** Прозрачность и тень элемента (макет). */
    function applyLook(node, el) {
      var st = el.style || {};
      var op = st.opacity != null && isFinite(st.opacity) ? Math.max(0, Math.min(1, Number(st.opacity))) : 1;
      node.style.opacity = op < 1 ? String(op) : "";
      var m = SHADOW_RE.exec(String(st.shadow || ""));
      var fx = [];
      if (m) fx.push("drop-shadow(" + m[1] + "px " + m[2] + "px " + m[3] / 2 + "px " + m[4] + ")");
      // Размытие слоя — сам объект; размытие фона — то, что под ним (стекло), по форме объекта.
      if (Number(st.blur) > 0) fx.push("blur(" + Number(st.blur) / 2 + "px)");
      node.style.filter = fx.join(" ");
      var bg = Number(st.bgBlur) > 0 ? "blur(" + Number(st.bgBlur) / 2 + "px)" : "";
      node.style.backdropFilter = bg;
      node.style.webkitBackdropFilter = bg;
      node.style.borderRadius = bg ? (el.type === "ellipse" ? "50%" : el.type === "rect" || el.type === "frame" ? radiiOf(st, el.w, el.h, 0).map(function (r) {
        return r + "px";
      }).join(" ") : "") : "";
    }

    /** Элемент экрана обрезается по краю экрана (как во Figma); вне экрана — как есть. */
    function clipToFrame(node, el) {
      var f = frameOf(el);
      var turned = false;
      for (var q = f, gq = 0; q && gq < 20; q = frameOf(q), gq++) if (q.rot) turned = true;
      // Видимая область — пересечение внешних фреймов, кроме тех, где снят флажок «Обрезать содержимое».
      var box = null;
      for (var p = f, guard = 0; p && guard < 20 && !turned; p = frameOf(p), guard++) {
        if (p.noclip) continue;
        box = box ? { x1: Math.max(box.x1, p.x), y1: Math.max(box.y1, p.y), x2: Math.min(box.x2, p.x + p.w), y2: Math.min(box.y2, p.y + p.h) }
          : { x1: p.x, y1: p.y, x2: p.x + p.w, y2: p.y + p.h };
      }
      // Повёрнутый фрейм: прямоугольник обрезки не совпал бы с ним — не обрезаем.
      if (!box) {
        if (node.style.clipPath) node.style.clipPath = "";
        return;
      }
      // Линия и рисунок нарисованы от начала координат доски, повёрнутый объект — в своих осях:
      // для них обрезка — многоугольником в координатах узла.
      if (el.type === "line" || el.type === "pen" || el.rot) {
        var ox = el.type === "line" || el.type === "pen" ? 0 : el.x;
        var oy = el.type === "line" || el.type === "pen" ? 0 : el.y;
        var a = -((el.type === "line" || el.type === "pen" ? 0 : el.rot) || 0) * Math.PI / 180;
        var cx = el.w / 2;
        var cy = el.h / 2;
        node.style.clipPath = "polygon(" + [[box.x1, box.y1], [box.x2, box.y1], [box.x2, box.y2], [box.x1, box.y2]].map(function (q) {
          var u = q[0] - ox - cx;
          var v = q[1] - oy - cy;
          return r1(cx + u * Math.cos(a) - v * Math.sin(a)) + "px " + r1(cy + u * Math.sin(a) + v * Math.cos(a)) + "px";
        }).join(", ") + ")";
        return;
      }
      var t = box.y1 - el.y;
      var l = box.x1 - el.x;
      var r = el.x + el.w - box.x2;
      var b = el.y + el.h - box.y2;
      // Отрицательный отступ — край экрана дальше края элемента: тень и обводка видны до края экрана.
      var cut = t > 0 || l > 0 || r > 0 || b > 0;
      node.style.clipPath = cut || (el.style && el.style.shadow) ? "inset(" + t + "px " + r + "px " + b + "px " + l + "px)" : "";
    }

    function frameShapeSvg(el) {
      var st = el.style || {};
      var fsw = st.strokeOff ? 0 : strokeW(st, 0);
      var fr = radiiOf(st, el.w, el.h, 0);
      var fgp = gradPaint(el);
      var fStroke = fsw > 0 ? ' stroke="' + (fgp.stroke || inkVar(st.stroke || "ink")) + '" stroke-width="' + fsw + '"' + opacityAttrs(st) : "";
      var fAligned = fsw > 0 && (st.strokeAlign === "inside" || st.strokeAlign === "outside");
      return '<svg class="lb-shape" width="' + el.w + '" height="' + el.h + '" viewBox="0 0 ' + el.w + " " + el.h + '" overflow="visible">' +
        fgp.defs + roundedShape(el.w, el.h, fr, ' fill="' + (st.fillOff ? "transparent" : fgp.fill || fillVar(st.fill || "#ffffff")) + '"' + opacityAttrs({ fillOpacity: st.fillOpacity }) +
          ' class="lb-frame-bg' + (fsw > 0 ? " has-stroke" : "") + '"' + (fAligned ? "" : fStroke)) +
        (fAligned ? alignedStroke("rect", el.w, el.h, fr, fsw, st.strokeAlign, fStroke) : "") + "</svg>";
    }

    function renderFrame(node, el, isEditing) {
      var st = el.style || {};
      node.style.cssText = "left:" + el.x + "px;top:" + el.y + "px;width:" + Math.max(1, el.w) + "px;height:" + Math.max(1, el.h) + "px;" +
        (el.rot ? "transform:rotate(" + el.rot + "deg);" : "");
      replaceShape(node, frameShapeSvg(el));
      node.classList.toggle("is-nested", !!frameOf(el));
      var head = node.querySelector(":scope > .lb-frame-head");
      if (!head) {
        head = document.createElement("div");
        head.className = "lb-frame-head";
        head.innerHTML = '<span class="lb-frame-name" data-f="name" data-ph="Фрейм"></span><span class="lb-frame-size"></span>';
        node.appendChild(head);
      }
      var nm = head.querySelector("[data-f=name]");
      if (!isEditing) {
        nm.textContent = String(el.name || "");
        nm.classList.toggle("is-empty", !el.name);
      }
      head.querySelector(".lb-frame-size").textContent = Math.round(el.w) + " × " + Math.round(el.h);
    }

    function renderNodeInner(id) {
      var el = els.get(id);
      var node = nodes.get(id);
      if (!el) {
        if (node) node.remove();
        nodes.delete(id);
        if (editing && editing.id === id) editing = null;
        dropTable(id);
        return;
      }
      if (!node) {
        node = document.createElement("div");
        node.className = "lb-el";
        node.setAttribute("data-id", id);
        nodes.set(id, node);
        world.appendChild(node);
        orderDirty = true;
      }
      var isEditing = editing && editing.id === id;
      // Перерисовка во время правки (раскладка автолэйаута, цвет) не снимает «правится»: без него текст
      // не ловит щелчки, и щелчок по нему завершал правку.
      node.className = "lb-el lb-t-" + el.type + (el.group ? " is-grouped" : "") + (isEditing ? " is-editing" : "");
      var textEl;
      if (el.type === "line") {
        var ls = lineSvg(el);
        node.style.cssText = "left:0;top:0;width:0;height:0";
        replaceShape(node, ls.svg);
        textEl = ensureText(node);
        textEl.classList.add("lb-line-label");
        textEl.style.left = ls.mid.x + "px";
        textEl.style.top = ls.mid.y + "px";
        textEl.classList.toggle("is-empty", !el.text && !isEditing);
      } else if (el.type === "pen") {
        node.style.cssText = "left:0;top:0;width:0;height:0";
        replaceShape(node, penSvg(el));
        return;
      } else if (BLOCKS[el.type]) {
        renderBlock(node, el, isEditing);
        return;
      } else if (el.type === "frame") {
        renderFrame(node, el, isEditing);
        return;
      } else {
        node.style.cssText =
          "left:" + el.x + "px;top:" + el.y + "px;width:" + Math.max(1, el.w) + "px;height:" + Math.max(1, el.h) + "px;" +
          (el.rot ? "transform:rotate(" + el.rot + "deg);" : "");
        replaceShape(node, shapeSvg(el));
        if (el.type === "image" || el.type === "vector") return;
        textEl = ensureText(node);
        if (el.type === "sticky") renderAuthor(node, el.author);
      }
      textStyle(el, textEl);
      if (!isEditing) renderTextBody(textEl, el);
      if (el.type === "text" && !isEditing) fitTextHeight(el, textEl);
    }

    // ---- составные блоки: таблица, оргкарточка, Гант ----
    function renderBlock(node, el, isEditing) {
      var auto = el.type !== "gantt";
      node.style.cssText =
        "left:" + el.x + "px;top:" + el.y + "px;width:" + Math.max(1, el.w) + "px;" +
        (auto ? "min-height:20px" : "height:" + Math.max(1, el.h) + "px");
      var old = node.querySelector(":scope > .lb-shape");
      if (old) old.remove();
      if (el.type === "table") renderTable(node, el, isEditing);
      else if (!isEditing) {
        var html = el.type === "card"
          ? BL.cardHtml(el, { editable: !readOnly })
          : BL.ganttHtml(el, { selTask: gSel && gSel.id === el.id ? gSel.task : "", editable: !readOnly && selected.size === 1 && selected.has(el.id) });
        var box = node.querySelector(":scope > .lb-block");
        if (!box) {
          box = document.createElement("div");
          box.className = "lb-block";
          node.appendChild(box);
        }
        if (box.getAttribute("data-src") !== html) {
          box.setAttribute("data-src", html);
          box.innerHTML = html;
        }
      }
      if (auto && !isEditing) fitBlockHeight(el, node);
    }

    /** Таблица доски — таблица заметки: тот же редактор, меню и стили. */
    function renderTable(node, el, isEditing) {
      var host = node.querySelector(":scope > .lb-table");
      if (!host) {
        host = document.createElement("div");
        host.className = "lb-table";
        node.appendChild(host);
      }
      var tb = tables.get(el.id);
      var NRE = window.NoteRichEditor;
      if (!tb && NRE && typeof NRE.mountTable === "function") {
        var id = el.id;
        tb = NRE.mountTable(host, {
          html: el.html,
          editable: false,
          onUpdate: function (info) {
            onTableInput(id, info.html);
          },
          onDelete: function () {
            finishEdit();
            setSelection([id]);
            deleteSel();
          },
        });
        tables.set(el.id, tb);
      } else if (!tb) {
        // Без редактора (публичная страница) — только текст ячеек, разметку не доверяем.
        if (host.getAttribute("data-src") !== el.html) {
          host.setAttribute("data-src", el.html || "");
          host.innerHTML = "";
          host.appendChild(staticTable(el.html));
        }
      } else if (!isEditing && tb.getHtml() !== el.html) {
        tb.setHtml(el.html);
      }
    }

    function staticTable(html) {
      var t = document.createElement("table");
      t.className = "note-editor-table";
      var body = document.createElement("tbody");
      t.appendChild(body);
      try {
        var doc = new DOMParser().parseFromString(String(html || ""), "text/html");
        doc.querySelectorAll("tr").forEach(function (tr) {
          var row = document.createElement("tr");
          tr.querySelectorAll("th, td").forEach(function (c) {
            var cell = document.createElement(c.tagName === "TH" ? "th" : "td");
            cell.textContent = c.textContent;
            var span = parseInt(c.getAttribute("colspan"), 10);
            if (span > 1) cell.colSpan = span;
            row.appendChild(cell);
          });
          body.appendChild(row);
        });
      } catch (_) {}
      return t;
    }

    function dropTable(id) {
      var tb = tables.get(id);
      if (!tb) return;
      tables.delete(id);
      try {
        tb.destroy();
      } catch (_) {}
    }

    /** Правка таблицы на месте: тело — в элемент сразу (для соавторов), шаг отмены — по выходу из правки. */
    function onTableInput(id, html) {
      var el = els.get(id);
      if (!el || readOnly || el.html === html) return;
      els.set(id, Object.assign({}, el, { html: html }));
      localChanged(id);
      var node = nodes.get(id);
      if (node) fitBlockHeight(els.get(id), node);
      renderOverlay();
      positionProps();
    }

    /** Таблица и карточка растут по высоте под содержимое (без шага отмены). */
    function fitBlockHeight(el, node) {
      if (!node.isConnected || !root.offsetWidth) return;
      var h = Math.max(20, Math.ceil(node.offsetHeight));
      var w = el.type === "table" ? Math.max(el.w, Math.ceil(node.scrollWidth)) : el.w;
      if (Math.abs(h - el.h) > 1 || Math.abs(w - el.w) > 1) {
        els.set(el.id, Object.assign({}, el, { h: h, w: w }));
        localChanged(el.id);
        renderOverlay();
      }
    }

    /** Текст блока; с подсветкой (style.highlight, из Miro) — фон только под словами, как маркером. */
    function renderTextBody(textEl, el) {
      var text = String(el.text || "");
      var hl = el.style && isHex(el.style.highlight) ? el.style.highlight : "";
      var key = hl + "|" + text;
      if (textEl.getAttribute("data-k") === key && (hl || textEl.textContent === text)) return;
      textEl.setAttribute("data-k", key);
      if (hl && text) {
        textEl.innerHTML = '<div class="lb-hl-wrap"><span class="lb-hl" style="background:' + hl + '">' + esc(text) + "</span></div>";
      } else {
        textEl.textContent = text;
      }
    }

    /** Подпись автора внизу стикера: аватар (фото или инициалы) и имя; у Лео — ✨. */
    function renderAuthor(node, author) {
      var has = !!(author && (author.name || author.id));
      var key = has ? JSON.stringify(author) : "";
      var box = node.querySelector(":scope > .lb-author");
      node.classList.toggle("has-author", has);
      if (!has) {
        if (box) box.remove();
        return;
      }
      if (box && box.getAttribute("data-key") === key) return;
      if (!box) {
        box = document.createElement("div");
        box.className = "lb-author";
        node.appendChild(box);
      }
      box.setAttribute("data-key", key);
      var name = String(author.name || "Участник");
      box.title = author.ai ? "Добавил Лео" : "Автор: " + name;
      var av;
      if (author.ai) {
        av = '<span class="lb-av is-ai"><svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
          '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg></span>';
      } else {
        av = '<span class="lb-av lb-av--t' + toneOf(author.id || name) + '"><span class="lb-av-txt">' + esc(initialsOf(name)) + "</span></span>";
      }
      box.innerHTML = av + '<span class="lb-author-name">' + esc(name) + "</span>";
      if (!author.ai && author.id && o.decorateAvatar) {
        var img = document.createElement("img");
        img.alt = "";
        img.className = "lb-av-img";
        box.querySelector(".lb-av").appendChild(img);
        try {
          o.decorateAvatar(img, author);
        } catch (_) {}
      }
    }

    function initialsOf(name) {
      var parts = String(name || "").replace(/^@/, "").trim().split(/\s+/).filter(Boolean);
      if (!parts.length) return "?";
      return (parts[0].charAt(0) + (parts[1] ? parts[1].charAt(0) : "")).toUpperCase();
    }

    function toneOf(key) {
      var h = 0;
      var str = String(key || "");
      for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
      return Math.abs(h) % 6;
    }

    function replaceShape(node, html) {
      var old = node.querySelector(":scope > .lb-shape");
      if (old && old.getAttribute("data-src") === html) return;
      var tmp = document.createElement("div");
      tmp.innerHTML = html;
      var svg = tmp.firstChild;
      svg.setAttribute("data-src", html);
      if (old) node.replaceChild(svg, old);
      else node.insertBefore(svg, node.firstChild);
    }

    /** Высота самого текста (в единицах холста); null — узел не показан, мерить нечего. */
    function textHeight(textEl) {
      if (!textEl.isConnected || !root.offsetWidth) return null;
      if (!textEl.textContent) return 0;
      var range = document.createRange();
      range.selectNodeContents(textEl);
      var r = range.getBoundingClientRect();
      if (!r.width && !r.height) return null;
      // Заданный межстрочный интервал (из Figma) — высота = строки × интервал, а не высота самих букв.
      var lh = parseFloat(getComputedStyle(textEl).lineHeight);
      if (isFinite(lh) && lh > 0) {
        var tops = [];
        Array.prototype.forEach.call(range.getClientRects(), function (q) {
          if (!q.height) return;
          if (!tops.some(function (t) {
            return Math.abs(t - q.top) < (lh * view.z) / 2;
          })) tops.push(q.top);
        });
        if (tops.length) return Math.max(r.height / view.z, tops.length * lh);
      }
      return r.height / view.z;
    }

    /** Текстовый блок растёт по высоте под текст (без отдельного шага отмены). */
    function fitTextHeight(el, textEl) {
      if (el.rot) return;
      var th = textHeight(textEl);
      if (th == null) return;
      var h = Math.max(20, Math.ceil(th));
      if (Math.abs(h - el.h) > 1) {
        var next = Object.assign({}, el, { h: h });
        els.set(el.id, next);
        var n = nodes.get(el.id);
        if (n) n.style.height = h + "px";
        localChanged(el.id);
        dirty.add(el.id);
        schedule();
        // Текст в автолэйауте вырос — соседи сдвигаются (без шага отмены, как сама подгонка высоты).
        if (el.frame && !tx) {
          var fr0 = getEl(el.frame);
          if (fr0 && fr0.layout) setTimeout(function () {
            if (!tx && getEl(el.id)) relayout([el.id]);
          }, 0);
        }
      }
    }

    /** Фигура с текстом вырастает, если текст не помещается. */
    function growToText(el, textEl) {
      if ((!SHAPES[el.type] && el.type !== "sticky") || el.rot) return null;
      var th = textHeight(textEl);
      if (!th) return null;
      // Отступы подписи; у ромба и эллипса текст помещается только в средней части.
      var pad = el.type === "sticky" ? 28 : 16;
      var tb = TEXT_BOX[el.type];
      var need = Math.ceil(el.type === "diamond" ? th * 2 + pad : el.type === "ellipse" ? th * 1.45 + pad :
        el.type === "cylinder" ? th + pad + G.capOf(el.w, el.h) * 3 : tb && (tb[1] || tb[3]) ? (th + pad) / Math.max(0.3, 1 - tb[1] - tb[3]) : th + pad);
      if (need > el.h + 1) return need;
      return null;
    }

    // ---- оверлей выделения ----
    var SVGNS = "http://www.w3.org/2000/svg";

    function selList() {
      var out = [];
      selected.forEach(function (id) {
        var el = els.get(id);
        if (el) out.push(el);
      });
      return out;
    }

    function selBounds() {
      return G.unionBounds(selList().map(function (el) {
        return G.elBounds(el, getEl);
      }));
    }

    function renderOverlay() {
      if (destroyed) return;
      var r = stage.getBoundingClientRect();
      overlay.setAttribute("width", r.width);
      overlay.setAttribute("height", r.height);
      var h = [];
      var z = view.z;
      var tr = 'transform="translate(' + view.x + " " + view.y + ") scale(" + z + ')"';
      var list = selList();
      var single = list.length === 1 ? list[0] : null;
      var hs = 8;
      list.forEach(function (el) {
        if (el.type === "line") {
          var lp = linePathOf(el);
          h.push('<path ' + tr + ' d="' + lp.d + '" class="lb-sel-line" vector-effect="non-scaling-stroke"/>');
        } else if (el.type === "pen") {
          var b = G.elBounds(el, getEl);
          h.push(rectPath(b, "lb-sel-box"));
        } else {
          h.push('<polygon class="lb-sel-box" points="' + G.corners(el).map(function (p) {
            var s = toScreen(p);
            return s.x + "," + s.y;
          }).join(" ") + '"/>');
        }
      });
      if (!readOnly && !editing && !(gesture && gesture.kind === "move")) {
        if (single && BOXED[single.type]) {
          var c = G.corners(single);
          var mids = [
            mid(c[0], c[1]), mid(c[1], c[2]), mid(c[2], c[3]), mid(c[3], c[0]),
          ];
          var names = [["nw", c[0]], ["n", mids[0]], ["ne", c[1]], ["e", mids[1]], ["se", c[2]], ["s", mids[2]], ["sw", c[3]], ["w", mids[3]]];
          if (single.type === "text") names = [["w", mids[3]], ["e", mids[1]], ["nw", c[0]], ["se", c[2]]];
          // Таблица и карточка растут по высоте сами — тянутся только вширь; Гант — по данным.
          if (BLOCKS[single.type]) names = single.type === "gantt" ? [] : [["w", mids[3]], ["e", mids[1]]];
          // Поворот мышкой: чуть снаружи каждого угла — зона с курсором поворота (как во Figma).
          if (!BLOCKS[single.type]) {
            var cc = toScreen(G.center(single));
            c.forEach(function (cp) {
              var sp = toScreen(cp);
              var dx = sp.x - cc.x;
              var dy = sp.y - cc.y;
              var dl = Math.max(1, Math.sqrt(dx * dx + dy * dy));
              h.push('<circle class="lb-rotzone" data-h="rot" cx="' + (sp.x + (dx / dl) * 12) + '" cy="' + (sp.y + (dy / dl) * 12) + '" r="' + (COARSE ? 16 : 11) + '"/>');
            });
          }
          names.forEach(function (n) {
            var s = toScreen(n[1]);
            h.push('<rect class="lb-handle" data-h="' + n[0] + '" x="' + (s.x - hs / 2) + '" y="' + (s.y - hs / 2) + '" width="' + hs + '" height="' + hs + '" rx="2"/>');
          });
          if (!BLOCKS[single.type] && single.type !== "frame") {
            var top = toScreen(mids[0]);
            var up = G.rot(G.pt(0, -1), G.pt(0, 0), single.rot || 0);
            var rp = G.add(top, G.mul(up, 24));
            h.push('<line class="lb-rot-stem" x1="' + top.x + '" y1="' + top.y + '" x2="' + rp.x + '" y2="' + rp.y + '"/>');
            h.push('<circle class="lb-handle lb-rot" data-h="rot" cx="' + rp.x + '" cy="' + rp.y + '" r="6"/>');
          }
          if (single.type !== "image" || true) {
            G.SIDES.forEach(function (side) {
              var p = G.sidePoint(single, side);
              var n = G.sideNormal(single, side);
              var s = G.add(toScreen(p), G.mul(n, side === "top" ? 44 : 22));
              h.push(
                '<g class="lb-connect" data-h="c-' + side + '" transform="translate(' + s.x + " " + s.y + ')">' +
                '<circle r="9"/><path d="M-4 0H4M0 -4V4"/></g>'
              );
            });
          }
        } else if (single && single.type === "line") {
          var res = G.resolveLine(single, getEl);
          var lp2 = linePathOf(single, res);
          var wps = G.waypointsOf(single);
          var hr = COARSE ? 1.6 : 1;
          if ((single.route || "straight") === "elbow") {
            // Ломаная: ручка на каждом отрезке, тянется поперёк.
            (lp2.segs || []).forEach(function (sg) {
              if (sg.len * z < 18) return;
              var s = toScreen(sg.p);
              var lw = (sg.horiz ? 16 : 6) * hr;
              var lh = (sg.horiz ? 6 : 16) * hr;
              h.push('<rect class="lb-handle lb-seg' + (sg.horiz ? " is-h" : " is-v") + '" data-h="seg:' + sg.i + '" x="' + (s.x - lw / 2) + '" y="' + (s.y - lh / 2) + '" width="' + lw + '" height="' + lh + '" rx="3"/>');
            });
          } else {
            wps.forEach(function (q, i) {
              var s = toScreen(q);
              h.push('<circle class="lb-handle lb-wp" data-h="wp:' + i + '" cx="' + s.x + '" cy="' + s.y + '" r="' + 5 * hr + '"/>');
            });
            if (wps.length) {
              (lp2.segs || []).forEach(function (sg) {
                var s = toScreen(sg.p);
                h.push('<circle class="lb-handle lb-ins" data-h="ins:' + sg.i + '" cx="' + s.x + '" cy="' + s.y + '" r="' + 4 * hr + '"/>');
              });
            } else {
              var m = toScreen(lp2.mid);
              h.push('<rect class="lb-handle lb-bend" data-h="ins:0" x="' + (m.x - 5 * hr) + '" y="' + (m.y - 5 * hr) + '" width="' + 10 * hr + '" height="' + 10 * hr + '" rx="' + 5 * hr + '"/>');
            }
          }
          [["p0", res.p0], ["p1", res.p1]].forEach(function (n) {
            var s = toScreen(n[1]);
            h.push('<circle class="lb-handle lb-end" data-h="' + n[0] + '" cx="' + s.x + '" cy="' + s.y + '" r="' + 6 * hr + '"/>');
          });
        } else if (list.length > 1) {
          var sb = selBounds();
          if (sb) {
            h.push(rectPath(sb, "lb-sel-group"));
            var a = toScreen(G.pt(sb.x, sb.y));
            var bb = toScreen(G.pt(sb.x + sb.w, sb.y + sb.h));
            [["nw", a.x, a.y], ["ne", bb.x, a.y], ["se", bb.x, bb.y], ["sw", a.x, bb.y]].forEach(function (n) {
              h.push('<rect class="lb-handle" data-h="g' + n[0] + '" x="' + (n[1] - hs / 2) + '" y="' + (n[2] - hs / 2) + '" width="' + hs + '" height="' + hs + '" rx="2"/>');
            });
          }
        }
      }
      if (snapTarget && snapTarget.el) {
        var t = snapTarget.el;
        h.push('<polygon class="lb-snap-box" points="' + G.corners(t).map(function (p) {
          var s = toScreen(p);
          return s.x + "," + s.y;
        }).join(" ") + '"/>');
        G.SIDES.forEach(function (side) {
          var s = toScreen(G.sidePoint(t, side));
          h.push('<circle class="lb-snap-dot' + (snapTarget.side === side ? " is-on" : "") + '" cx="' + s.x + '" cy="' + s.y + '" r="5"/>');
        });
      }
      guides.forEach(function (g) {
        var p0 = toScreen(g.a);
        var p1 = toScreen(g.b);
        h.push('<line class="lb-guide' + (mode === "design" ? " is-design" : "") + '" x1="' + p0.x + '" y1="' + p0.y + '" x2="' + p1.x + '" y2="' + p1.y + '"/>');
      });
      if (marquee) h.push(rectPath(marquee, "lb-marquee"));
      if (gapHover) h.push(gapSvg(gapHover));
      if (layerHover) {
        var hb = G.unionBounds(layerIds(layerHover).map(function (id) {
          return G.elBounds(getEl(id), getEl);
        }));
        if (hb) h.push(rectPath(hb, "lb-layer-hover"));
      }
      if (measure) h.push(measureSvg(measure.a, measure.b));
      if (penPath) h.push(penOverlay());
      remoteCursors.forEach(function (c) {
        if (!c || !isFinite(c.x) || !isFinite(c.y)) return;
        var s = toScreen(G.pt(c.x, c.y));
        var color = esc(c.color || "#7b68ee");
        h.push(
          '<g class="lb-cursor" transform="translate(' + s.x + " " + s.y + ')">' +
          '<path d="M0 0L0 16L4.5 12L8 19L10.5 18L7 11L13 11Z" fill="' + color + '" stroke="#fff" stroke-width="1"/>' +
          '<text x="14" y="24" fill="' + color + '">' + esc(c.name || "") + "</text></g>"
        );
      });
      overlay.innerHTML = h.join("");
    }

    function mid(a, b) {
      return G.mul(G.add(a, b), 0.5);
    }

    // ---- перо (макет): контуры из точек и кривых → вектор; двойной клик по вектору — правка точек ----
    // penPath: {cs: [{pi, pts: [{x, y, ix, iy, ox, oy}], closed}] (мир; ручки — смещения от точки),
    //           ci — контур, который рисуем; drawing, editId, sel {c, i}, cursor, alias {путь: путь-образец}}
    var penLastDown = { t: 0, x: 0, y: 0 };
    var PEN_HIT = 7; // px экрана

    function penPt(x, y) {
      return { x: x, y: y, ix: 0, iy: 0, ox: 0, oy: 0 };
    }

    function penAct() {
      return penPath && penPath.cs[penPath.ci];
    }

    function penAll(fn) {
      if (!penPath) return;
      penPath.cs.forEach(function (c, ci) {
        c.pts.forEach(function (q, i) {
          fn(q, ci, i, c);
        });
      });
    }

    /** Shift — угол с шагом 15° от точки o (как у линий). */
    function snapAngle(o, p) {
      var v = G.sub(p, o);
      var len = G.len(v);
      var step = Math.PI / 12;
      var ang = Math.round(Math.atan2(v.y, v.x) / step) * step;
      return G.add(o, G.pt(Math.cos(ang) * len, Math.sin(ang) * len));
    }

    /** Прилипание к горизонтали / вертикали других точек — с направляющими. */
    function penSnap(p, skip) {
      var th = 6 / view.z;
      var bx = null;
      var by = null;
      penAll(function (q) {
        if (q === skip) return;
        if (Math.abs(q.x - p.x) <= th && (!bx || Math.abs(q.x - p.x) < Math.abs(bx.x - p.x))) bx = q;
        if (Math.abs(q.y - p.y) <= th && (!by || Math.abs(q.y - p.y) < Math.abs(by.y - p.y))) by = q;
      });
      var out = G.pt(bx ? bx.x : p.x, by ? by.y : p.y);
      guides = [];
      if (bx) guides.push({ a: G.pt(bx.x, Math.min(bx.y, out.y)), b: G.pt(bx.x, Math.max(bx.y, out.y)) });
      if (by) guides.push({ a: G.pt(Math.min(by.x, out.x), by.y), b: G.pt(Math.max(by.x, out.x), by.y) });
      return out;
    }

    /** Куда встанет новая точка: Shift — 15° от последней, иначе прилипание к осям других точек. */
    function penTarget(p, e) {
      var c = penAct();
      var last = c && c.pts[c.pts.length - 1];
      if (e && e.shiftKey && last) {
        guides = [];
        return snapAngle(G.pt(last.x, last.y), p);
      }
      return penSnap(p, null);
    }

    function segCtrl(a, b) {
      return [G.pt(a.x, a.y), G.pt(a.x + a.ox, a.y + a.oy), G.pt(b.x + b.ix, b.y + b.iy), G.pt(b.x, b.y)];
    }

    /** Что под курсором: ручка {c, i, h}, точка {c, i} или отрезок {c, i (начало), t}. */
    function penHit(e) {
      if (!penPath) return null;
      var r = stageRect();
      var m = G.pt(e.clientX - r.left, e.clientY - r.top);
      var best = null;
      var take = function (d, lim, hit) {
        if (d <= lim && (!best || d < best.d)) best = Object.assign({ d: d }, hit);
      };
      penAll(function (q, c, i) {
        [["out", q.ox, q.oy], ["in", q.ix, q.iy]].forEach(function (h) {
          if (h[1] || h[2]) take(G.dist(toScreen(G.pt(q.x + h[1], q.y + h[2])), m), PEN_HIT, { c: c, i: i, h: h[0] });
        });
      });
      if (best) return best;
      penAll(function (q, c, i) {
        take(G.dist(toScreen(G.pt(q.x, q.y)), m), PEN_HIT + 2, { c: c, i: i });
      });
      if (best) return best;
      // Отрезок: ищем ближайшую точку кривой (грубо по 32 шагам, потом уточняем).
      penPath.cs.forEach(function (cn, c) {
        var n = cn.pts.length;
        var segs = cn.closed ? n : n - 1;
        for (var i = 0; i < segs; i++) {
          var P = segCtrl(cn.pts[i], cn.pts[(i + 1) % n]).map(toScreen);
          var bt = 0;
          var bd = Infinity;
          for (var k = 0; k <= 32; k++) {
            var d = G.dist(G.cubicAt(P[0], P[1], P[2], P[3], k / 32), m);
            if (d < bd) {
              bd = d;
              bt = k / 32;
            }
          }
          for (var step = 1 / 64; step > 0.0005; step /= 2) {
            [bt - step, bt + step].forEach(function (t) {
              if (t < 0 || t > 1) return;
              var d2 = G.dist(G.cubicAt(P[0], P[1], P[2], P[3], t), m);
              if (d2 < bd) {
                bd = d2;
                bt = t;
              }
            });
          }
          take(bd, 6, { c: c, i: i, t: bt, seg: true });
        }
      });
      return best;
    }

    /** Новая точка на отрезке (де Кастельжо): форма кривой не меняется. */
    function penSplit(c, i, t) {
      var cn = penPath.cs[c];
      var n = cn.pts.length;
      var a = cn.pts[i];
      var b = cn.pts[(i + 1) % n];
      var P = segCtrl(a, b);
      var L = function (u, v) {
        return G.pt(u.x + (v.x - u.x) * t, u.y + (v.y - u.y) * t);
      };
      var p01 = L(P[0], P[1]);
      var p12 = L(P[1], P[2]);
      var p23 = L(P[2], P[3]);
      var p012 = L(p01, p12);
      var p123 = L(p12, p23);
      var M = L(p012, p123);
      var curved = a.ox || a.oy || b.ix || b.iy;
      var q = penPt(M.x, M.y);
      if (curved) {
        a.ox = p01.x - a.x;
        a.oy = p01.y - a.y;
        b.ix = p23.x - b.x;
        b.iy = p23.y - b.y;
        q.ix = p012.x - M.x;
        q.iy = p012.y - M.y;
        q.ox = p123.x - M.x;
        q.oy = p123.y - M.y;
      }
      cn.pts.splice(i + 1, 0, q);
      penPath.sel = { c: c, i: i + 1 };
      return i + 1;
    }

    function penEnd(c, i) {
      var cn = penPath && penPath.cs[c];
      return cn && !cn.closed && (i === 0 || i === cn.pts.length - 1);
    }

    function penDown(p, e) {
      var now = Date.now();
      var dbl = now - penLastDown.t < 350 && Math.abs(e.clientX - penLastDown.x) < 6 && Math.abs(e.clientY - penLastDown.y) < 6;
      penLastDown = { t: now, x: e.clientX, y: e.clientY };
      if (dbl && penPath && penPath.drawing) {
        // Двойной клик — рисование остановлено, правка точек продолжается (как во Figma).
        stopPenDrawing();
        return;
      }
      if (!penPath) penPath = { cs: [{ pi: 0, pts: [], closed: false }], ci: 0, drawing: true, editId: null, sel: null };
      var hit = penHit(e);
      var base = { pointerId: e.pointerId, sx: e.clientX, sy: e.clientY, p0: p, rect: stageRect(), moved: false };
      if (hit && hit.h) {
        startGesture(Object.assign(base, { kind: "vhd", c: hit.c, i: hit.i, h: hit.h }));
        return;
      }
      var act = penAct();
      if (hit && !hit.seg) {
        var n = act.pts.length;
        if (penPath.drawing && hit.c === penPath.ci && hit.i === 0 && n >= 2 && !act.closed) {
          act.closed = true;
          penPath.drawing = false;
          writePen();
          renderOverlay();
          return;
        }
        if (penPath.drawing && hit.c !== penPath.ci && penEnd(hit.c, hit.i) && n >= 1) {
          // Рисуем и кликнули в конец другого открытого контура — соединяем их в один.
          joinPen(hit.c, hit.i);
          return;
        }
        if (!penPath.drawing || hit.c !== penPath.ci || hit.i !== n - 1) {
          penPath.sel = { c: hit.c, i: hit.i };
          var q = penPath.cs[hit.c].pts[hit.i];
          startGesture(Object.assign(base, { kind: "vpt", c: hit.c, i: hit.i, q0: G.pt(q.x, q.y) }));
          renderOverlay();
          return;
        }
      }
      if (hit && hit.seg && !penPath.drawing) {
        // Клик по линии контура — новая точка на ней (её сразу можно тянуть).
        var ni = penSplit(hit.c, hit.i, hit.t);
        var nq = penPath.cs[hit.c].pts[ni];
        writePen();
        startGesture(Object.assign(base, { kind: "vpt", c: hit.c, i: ni, q0: G.pt(nq.x, nq.y) }));
        renderOverlay();
        return;
      }
      if (!penPath.drawing) {
        // Выбран конец открытого контура — продолжаем от него (как во Figma), иначе — новый контур в том же векторе.
        var sl = penPath.sel;
        if (sl && penEnd(sl.c, sl.i)) resumePen(sl.c, sl.i);
        else if (penPath.editId && !penPath.sel) {
          exitPen();
          setTool("select");
          return;
        } else {
          // Выбрана точка в середине линии — новая линия начинается из неё (ответвление, как во Figma).
          var from = sl ? penPath.cs[sl.c].pts[sl.i] : null;
          penPath.cs.push({ pi: sl ? penPath.cs[sl.c].pi : penPath.cs[penPath.cs.length - 1].pi, pts: from ? [penPt(from.x, from.y)] : [], closed: false });
          penPath.ci = penPath.cs.length - 1;
          penPath.drawing = true;
        }
      }
      var t = penTarget(p, e);
      act = penAct();
      act.pts.push(penPt(t.x, t.y));
      penPath.sel = { c: penPath.ci, i: act.pts.length - 1 };
      startGesture(Object.assign(base, { kind: "pathpt", p0: t }));
      renderOverlay();
    }

    /** Соединить рисуемый контур с концом другого открытого контура. */
    function joinPen(c, i) {
      var pp = penPath;
      var a = pp.cs[pp.ci];
      var b = pp.cs[c];
      if (i !== 0) reverseContour(b);
      a.pts = a.pts.concat(b.pts);
      pp.cs.splice(c, 1);
      if (c < pp.ci) pp.ci--;
      pp.drawing = false;
      pp.sel = null;
      writePen();
      renderOverlay();
    }

    function reverseContour(cn) {
      cn.pts.reverse().forEach(function (q) {
        var ix = q.ix;
        var iy = q.iy;
        q.ix = q.ox;
        q.iy = q.oy;
        q.ox = ix;
        q.oy = iy;
      });
    }

    /** Тянем от новой точки — ручки кривой (симметричные); Shift — угол ручки с шагом 15°. */
    function penDrag(p, e) {
      var c = penAct();
      var pt = c && c.pts[c.pts.length - 1];
      if (!pt) return;
      if (e && e.shiftKey) p = snapAngle(G.pt(pt.x, pt.y), p);
      pt.ox = p.x - pt.x;
      pt.oy = p.y - pt.y;
      pt.ix = -pt.ox;
      pt.iy = -pt.oy;
      renderOverlay();
    }

    /** Перенос точки (с её ручками): прилипание к осям других точек, Shift — только по одной оси. */
    function penMovePt(g, p, e) {
      var q = penPath && penPath.cs[g.c] && penPath.cs[g.c].pts[g.i];
      if (!q) return;
      var t = penSnap(p, q);
      if (e && e.shiftKey) {
        guides = [];
        t = Math.abs(p.x - g.q0.x) >= Math.abs(p.y - g.q0.y) ? G.pt(p.x, g.q0.y) : G.pt(g.q0.x, p.y);
      }
      q.x = t.x;
      q.y = t.y;
      renderOverlay();
    }

    /** Ручка точки: вторая — зеркально (Alt — независимо); Shift — угол 15°. */
    function penMoveHandle(g, p, e) {
      var q = penPath && penPath.cs[g.c] && penPath.cs[g.c].pts[g.i];
      if (!q) return;
      if (e && e.shiftKey) p = snapAngle(G.pt(q.x, q.y), p);
      var dx = p.x - q.x;
      var dy = p.y - q.y;
      var me = g.h === "out" ? "o" : "i";
      var other = g.h === "out" ? "i" : "o";
      q[me + "x"] = dx;
      q[me + "y"] = dy;
      if (!(e && e.altKey)) {
        q[other + "x"] = -dx;
        q[other + "y"] = -dy;
      }
      penPath.sel = { c: g.c, i: g.i };
      renderOverlay();
    }

    function penD(pts, closed, ox, oy) {
      var f = function (x, y) {
        return Math.round((x - ox) * 100) / 100 + " " + Math.round((y - oy) * 100) / 100;
      };
      var seg = function (a, b) {
        if (!a.ox && !a.oy && !b.ix && !b.iy) return "L" + f(b.x, b.y);
        return "C" + f(a.x + a.ox, a.y + a.oy) + " " + f(b.x + b.ix, b.y + b.iy) + " " + f(b.x, b.y);
      };
      if (!pts.length) return "";
      var d = "M" + f(pts[0].x, pts[0].y);
      for (var i = 1; i < pts.length; i++) d += seg(pts[i - 1], pts[i]);
      if (closed && pts.length > 1) d += seg(pts[pts.length - 1], pts[0]) + "Z";
      return d;
    }

    /** Контуры вектора → точки для правки: M/L/H/V/C/S/Q/T/Z (абсолютные и относительные). null — не разобрать. */
    function parsePen(el) {
      if (!el || el.type !== "vector" || !el.paths || !el.paths.length) return null;
      var vb = el.vb || [el.w, el.h];
      var sx = el.w / Math.max(0.01, vb[0]);
      var sy = el.h / Math.max(0.01, vb[1]);
      var c0 = G.center(el);
      var rot = ((el.rot || 0) * Math.PI) / 180;
      var W = function (x, y) {
        var wx = el.x + x * sx;
        var wy = el.y + y * sy;
        if (!rot) return G.pt(wx, wy);
        var dx = wx - c0.x;
        var dy = wy - c0.y;
        return G.pt(c0.x + dx * Math.cos(rot) - dy * Math.sin(rot), c0.y + dx * Math.sin(rot) + dy * Math.cos(rot));
      };
      var cs = [];
      var alias = {};
      var seen = {};
      for (var pi = 0; pi < el.paths.length; pi++) {
        var d = String(el.paths[pi].d || "");
        if (seen[d] != null) {
          alias[pi] = seen[d]; // заливка и обводка одной фигуры — один контур
          continue;
        }
        seen[d] = pi;
        var got = parseD(d, W, pi);
        if (!got) return null;
        cs = cs.concat(got);
      }
      if (!cs.length) return null;
      return { cs: cs, alias: alias };
    }

    function parseD(d, W, pi) {
      if (/[^MLHVCSQTZmlhvcsqtz0-9.,\s\-+e]/.test(d)) return null;
      var toks = d.match(/[MLHVCSQTZmlhvcsqtz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi) || [];
      var out = [];
      var cur = null;
      var i = 0;
      var cmd = "";
      var cx = 0;
      var cy = 0;
      var sx0 = 0;
      var sy0 = 0;
      var lastC = null; // последняя управляющая точка (для S / T), в координатах d
      var lastQ = null;
      var num = function () {
        var v = Number(toks[i++]);
        if (!isFinite(v)) throw new Error("num");
        return v;
      };
      var addPt = function (x, y) {
        var w = W(x, y);
        var q = penPt(w.x, w.y);
        cur.pts.push(q);
        return q;
      };
      var setOut = function (x, y) {
        var prev = cur.pts[cur.pts.length - 1];
        var w = W(x, y);
        prev.ox = w.x - prev.x;
        prev.oy = w.y - prev.y;
      };
      var setIn = function (q, x, y) {
        var w = W(x, y);
        q.ix = w.x - q.x;
        q.iy = w.y - q.y;
      };
      try {
        while (i < toks.length) {
          if (/[a-z]/i.test(toks[i])) cmd = toks[i++];
          else if (!cmd) return null;
          var rel = cmd === cmd.toLowerCase();
          var C = cmd.toUpperCase();
          var bx = rel ? cx : 0;
          var by = rel ? cy : 0;
          if (C === "Z") {
            if (cur) {
              cur.closed = true;
              cx = sx0;
              cy = sy0;
            }
            cur = null;
            lastC = lastQ = null;
            continue;
          }
          if (C === "M") {
            cx = num() + bx;
            cy = num() + by;
            sx0 = cx;
            sy0 = cy;
            cur = { pi: pi, pts: [], closed: false };
            out.push(cur);
            addPt(cx, cy);
            cmd = rel ? "l" : "L";
            lastC = lastQ = null;
            continue;
          }
          if (!cur) {
            // Команда после Z без M — новый контур от последней точки.
            cur = { pi: pi, pts: [], closed: false };
            out.push(cur);
            addPt(cx, cy);
          }
          if (C === "L" || C === "H" || C === "V") {
            var nx = C === "V" ? cx : num() + bx;
            var ny = C === "H" ? cy : num() + (C === "V" ? by : by);
            cx = nx;
            cy = ny;
            addPt(cx, cy);
            lastC = lastQ = null;
          } else if (C === "C" || C === "S") {
            var x1;
            var y1;
            if (C === "C") {
              x1 = num() + bx;
              y1 = num() + by;
            } else {
              x1 = lastC ? 2 * cx - lastC[0] : cx;
              y1 = lastC ? 2 * cy - lastC[1] : cy;
            }
            var x2 = num() + bx;
            var y2 = num() + by;
            var x3 = num() + bx;
            var y3 = num() + by;
            setOut(x1, y1);
            var q3 = addPt(x3, y3);
            setIn(q3, x2, y2);
            lastC = [x2, y2];
            lastQ = null;
            cx = x3;
            cy = y3;
          } else if (C === "Q" || C === "T") {
            var qx;
            var qy;
            if (C === "Q") {
              qx = num() + bx;
              qy = num() + by;
            } else {
              qx = lastQ ? 2 * cx - lastQ[0] : cx;
              qy = lastQ ? 2 * cy - lastQ[1] : cy;
            }
            var ex = num() + bx;
            var ey = num() + by;
            // Квадратичная кривая → кубическая с теми же концами.
            setOut(cx + (2 / 3) * (qx - cx), cy + (2 / 3) * (qy - cy));
            var qe = addPt(ex, ey);
            setIn(qe, ex + (2 / 3) * (qx - ex), ey + (2 / 3) * (qy - ey));
            lastQ = [qx, qy];
            lastC = null;
            cx = ex;
            cy = ey;
          } else return null;
        }
      } catch (_) {
        return null;
      }
      // Замкнутый контур, вернувшийся в первую точку: дубль убираем, ручку переносим.
      out.forEach(function (cn) {
        var f = cn.pts[0];
        var l = cn.pts[cn.pts.length - 1];
        if (cn.closed && cn.pts.length > 2 && Math.abs(l.x - f.x) < 0.01 && Math.abs(l.y - f.y) < 0.01) {
          f.ix = l.ix;
          f.iy = l.iy;
          cn.pts.pop();
        }
      });
      return out.filter(function (cn) {
        return cn.pts.length >= 2 || cn.closed;
      });
    }

    /** SVG-текст → вектор: path, rect, circle, ellipse, line, polyline, polygon с наследованием цветов от групп. */
    function svgToVector(text) {
      var doc;
      try {
        doc = new DOMParser().parseFromString(String(text).slice(0, 2000000), "image/svg+xml");
      } catch (_) {
        return null;
      }
      var svg = doc && doc.documentElement;
      if (!svg || svg.nodeName.toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) return null;
      var vbA = String(svg.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
      var W0 = parseFloat(svg.getAttribute("width"));
      var H0 = parseFloat(svg.getAttribute("height"));
      var vb = vbA.length === 4 && vbA.every(isFinite) ? vbA : [0, 0, W0 || 24, H0 || 24];
      var w = W0 || vb[2];
      var h = H0 || vb[3];
      var ID = function (x, y) {
        return G.pt(x - vb[0], y - vb[1]);
      };
      var hexColor = function (v) {
        v = String(v || "").trim().toLowerCase();
        if (/^#[0-9a-f]{6}$/.test(v)) return v;
        if (/^#[0-9a-f]{3}$/.test(v)) return "#" + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
        if (v === "white") return "#ffffff";
        if (v === "black" || v === "currentcolor") return "#000000";
        var m = /^rgba?\((\d+)[ ,]+(\d+)[ ,]+(\d+)/.exec(v);
        if (m) return "#" + [m[1], m[2], m[3]].map(function (n) {
          return ("0" + Math.min(255, Number(n)).toString(16)).slice(-2);
        }).join("");
        return v === "none" ? "none" : null;
      };
      var attr = function (node, k, inh) {
        var v = node.getAttribute(k);
        var st = node.getAttribute("style");
        if (st) {
          var m = new RegExp("(?:^|;)\\s*" + k + "\\s*:\\s*([^;]+)").exec(st);
          if (m) v = m[1].trim();
        }
        return v != null ? v : inh[k];
      };
      var num = function (node, k) {
        return parseFloat(node.getAttribute(k)) || 0;
      };
      var K = 0.5523;
      var paths = [];
      var walk = function (node, inh) {
        if (paths.length > 300 || node.nodeType !== 1) return;
        var tag = node.nodeName.toLowerCase();
        if (tag === "defs" || tag === "clippath" || tag === "mask" || tag === "lineargradient" || tag === "radialgradient" || tag === "style") return;
        var own = {};
        ["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "fill-rule", "display", "visibility"].forEach(function (k) {
          own[k] = attr(node, k, inh);
        });
        if (own.display === "none" || own.visibility === "hidden") return;
        var d = "";
        if (tag === "path") d = node.getAttribute("d") || "";
        else if (tag === "rect") {
          var x = num(node, "x");
          var y = num(node, "y");
          var rw = num(node, "width");
          var rh = num(node, "height");
          var r = Math.min(num(node, "rx") || num(node, "ry"), rw / 2, rh / 2);
          var c = r * (1 - K);
          d = r > 0
            ? "M" + (x + r) + " " + y + "L" + (x + rw - r) + " " + y + "C" + (x + rw - c) + " " + y + " " + (x + rw) + " " + (y + c) + " " + (x + rw) + " " + (y + r) +
              "L" + (x + rw) + " " + (y + rh - r) + "C" + (x + rw) + " " + (y + rh - c) + " " + (x + rw - c) + " " + (y + rh) + " " + (x + rw - r) + " " + (y + rh) +
              "L" + (x + r) + " " + (y + rh) + "C" + (x + c) + " " + (y + rh) + " " + x + " " + (y + rh - c) + " " + x + " " + (y + rh - r) +
              "L" + x + " " + (y + r) + "C" + x + " " + (y + c) + " " + (x + c) + " " + y + " " + (x + r) + " " + y + "Z"
            : "M" + x + " " + y + "L" + (x + rw) + " " + y + "L" + (x + rw) + " " + (y + rh) + "L" + x + " " + (y + rh) + "Z";
        } else if (tag === "circle" || tag === "ellipse") {
          var cx = num(node, "cx");
          var cy = num(node, "cy");
          var rx = tag === "circle" ? num(node, "r") : num(node, "rx");
          var ry = tag === "circle" ? rx : num(node, "ry");
          var P = function (a, b) {
            return a + " " + b;
          };
          d = "M" + P(cx + rx, cy) + "C" + P(cx + rx, cy + ry * K) + " " + P(cx + rx * K, cy + ry) + " " + P(cx, cy + ry) +
            "C" + P(cx - rx * K, cy + ry) + " " + P(cx - rx, cy + ry * K) + " " + P(cx - rx, cy) +
            "C" + P(cx - rx, cy - ry * K) + " " + P(cx - rx * K, cy - ry) + " " + P(cx, cy - ry) +
            "C" + P(cx + rx * K, cy - ry) + " " + P(cx + rx, cy - ry * K) + " " + P(cx + rx, cy) + "Z";
        } else if (tag === "line") {
          d = "M" + num(node, "x1") + " " + num(node, "y1") + "L" + num(node, "x2") + " " + num(node, "y2");
        } else if (tag === "polyline" || tag === "polygon") {
          var ns = String(node.getAttribute("points") || "").trim().split(/[\s,]+/).map(Number);
          for (var i = 0; i + 1 < ns.length; i += 2) d += (i ? "L" : "M") + ns[i] + " " + ns[i + 1];
          if (tag === "polygon" && d) d += "Z";
        } else {
          Array.prototype.forEach.call(node.childNodes, function (ch) {
            walk(ch, own);
          });
          return;
        }
        if (!d) return;
        // Свои команды (H, V, S, T, относительные) → M / L / C, как пишет перо; дуги (A) не поддерживаем.
        var cs = parseD(d, ID, 0);
        if (!cs || !cs.length) return;
        var nd = cs.map(function (cn) {
          return penD(cn.pts, cn.closed, 0, 0);
        }).join("");
        var fill = hexColor(own.fill == null ? "#000000" : own.fill);
        var stroke = hexColor(own.stroke);
        var sw = own["stroke-width"] != null ? parseFloat(own["stroke-width"]) : 1;
        if (fill && fill !== "none") paths.push({ d: nd, fill: fill, rule: own["fill-rule"] === "evenodd" ? "evenodd" : undefined });
        if (stroke && stroke !== "none" && sw > 0) {
          paths.push({ d: nd, stroke: stroke, sw: sw, cap: own["stroke-linecap"] === "round" || own["stroke-linecap"] === "square" ? own["stroke-linecap"] : undefined,
            join: own["stroke-linejoin"] === "round" || own["stroke-linejoin"] === "bevel" ? own["stroke-linejoin"] : undefined });
        }
      };
      walk(svg, { fill: svg.getAttribute("fill") });
      if (!paths.length) return null;
      return { id: uid(), type: "vector", x: 0, y: 0, w: w, h: h, vb: [vb[2], vb[3]], paths: paths };
    }

    /** Контуры → вектор (новый или правим существующий) одним шагом отмены. */
    function writePen() {
      var pp = penPath;
      if (!pp) return;
      var cs = pp.cs.filter(function (c) {
        return c.pts.length >= 2;
      });
      if (!cs.length) return;
      var xs = [];
      var ys = [];
      cs.forEach(function (cn) {
        var n = cn.pts.length;
        cn.pts.forEach(function (q, i) {
          xs.push(q.x);
          ys.push(q.y);
          if (i < n - 1 || cn.closed) {
            xs.push(q.x + q.ox);
            ys.push(q.y + q.oy);
          }
          if (i > 0 || cn.closed) {
            xs.push(q.x + q.ix);
            ys.push(q.y + q.iy);
          }
        });
      });
      var x0 = Math.min.apply(null, xs);
      var y0 = Math.min.apply(null, ys);
      var w = Math.max(1, Math.max.apply(null, xs) - x0);
      var h = Math.max(1, Math.max.apply(null, ys) - y0);
      var dOf = function (pi) {
        return cs.filter(function (cn) {
          return cn.pi === pi;
        }).map(function (cn) {
          return penD(cn.pts, cn.closed, x0, y0);
        }).join("");
      };
      begin();
      var cur = pp.editId ? getEl(pp.editId) : null;
      if (cur) {
        // Вектор переписан в мировых единицах (масштаб 1, без поворота) — толщины обводок пересчитываем.
        var vb = cur.vb || [cur.w, cur.h];
        var k = ((cur.w / Math.max(0.01, vb[0])) + (cur.h / Math.max(0.01, vb[1]))) / 2;
        var paths = [];
        cur.paths.forEach(function (p0, pi) {
          var d = dOf(pp.alias && pp.alias[pi] != null ? pp.alias[pi] : pi);
          if (!d) return;
          var np = Object.assign({}, p0, { d: d });
          if (np.sw && Math.abs(k - 1) > 0.001) np.sw = Math.round(np.sw * k * 100) / 100;
          paths.push(np);
        });
        if (!paths.length) return commit();
        patch(cur.id, { x: x0, y: y0, w: w, h: h, vb: [w, h], rot: undefined, paths: paths });
      } else {
        var st = styleFor("pen");
        var el = newEl("vector", G.pt(x0, y0), {
          w: w, h: h, vb: [w, h], style: undefined,
          paths: [{ d: dOf(0), stroke: isHex(st.stroke) || INK.indexOf(st.stroke) >= 0 ? st.stroke : "#000000", sw: Number(st.strokeWidth) || 2, cap: "round", join: "round" }],
        });
        delete el.style;
        put(el);
        pp.editId = el.id;
      }
      syncFrames();
      commit();
      markDirty(pp.editId);
    }

    /** Enter / Esc / двойной клик: рисование остановлено, точки остаются для правки. */
    function stopPenDrawing() {
      if (!penPath) return;
      penPath.drawing = false;
      penPath.cursor = null;
      penPath.sel = null;
      guides = [];
      // Недорисованный контур из одной точки не нужен.
      penPath.cs = penPath.cs.filter(function (c) {
        return c.pts.length >= 2;
      });
      penPath.ci = Math.max(0, penPath.cs.length - 1);
      if (!penPath.cs.length) {
        var id = penPath.editId;
        penPath = null;
        if (id) markDirty(id);
        setTool("select");
      } else writePen();
      renderOverlay();
    }

    /** Правка закончена: вектор выделен, перо выключено. */
    function exitPen() {
      var pp = penPath;
      if (!pp) return;
      if (pp.drawing) pp.cs = pp.cs.filter(function (c) {
        return c.pts.length >= 2;
      });
      writePen();
      penPath = null;
      guides = [];
      if (pp.editId) {
        markDirty(pp.editId);
        if (getEl(pp.editId)) setSelection([pp.editId]);
      }
      renderOverlay();
    }

    /** Отмена во время пера: контуры в памяти устарели — бросаем их без записи. */
    function dropPen() {
      if (!penPath) return;
      var id = penPath.editId;
      penPath = null;
      guides = [];
      if (id) markDirty(id);
      if (tool === "path") setTool("select");
      renderOverlay();
    }

    /** Двойной клик по вектору — правка его точек. */
    function editVector(id) {
      var el = getEl(id);
      var parsed = parsePen(el);
      if (!parsed) {
        notify("Этот вектор нельзя править по точкам");
        return false;
      }
      exitPen();
      setTool("path");
      penPath = { cs: parsed.cs, ci: parsed.cs.length - 1, alias: parsed.alias, drawing: false, editId: id, sel: null };
      notify("Правка точек: клик по линии — новая точка; выберите точку и кликните рядом — линия от неё; Esc — готово");
      selected.clear();
      markDirty(id);
      schedule();
      return true;
    }

    /** Продолжить рисование от конца открытого контура (от начала — разворачиваем порядок точек). */
    function resumePen(c, i) {
      var pp = penPath;
      var cn = pp && pp.cs[c];
      if (!cn || cn.closed || pp.drawing) return false;
      if (i !== 0 && i !== cn.pts.length - 1) return false;
      if (i === 0) reverseContour(cn);
      pp.ci = c;
      pp.drawing = true;
      pp.sel = { c: c, i: cn.pts.length - 1 };
      renderOverlay();
      return true;
    }

    function deletePenPoint() {
      var pp = penPath;
      if (!pp || !pp.sel || pp.drawing) return false;
      var cn = pp.cs[pp.sel.c];
      cn.pts.splice(pp.sel.i, 1);
      pp.sel = null;
      if (cn.pts.length < 3) cn.closed = false;
      if (cn.pts.length < 2) pp.cs.splice(pp.cs.indexOf(cn), 1);
      pp.ci = Math.max(0, Math.min(pp.ci, pp.cs.length - 1));
      if (!pp.cs.length) {
        var id = pp.editId;
        penPath = null;
        if (id) {
          begin();
          remove(id);
          commit();
        }
        setTool("select");
      } else writePen();
      renderOverlay();
      return true;
    }

    function penOverlay() {
      if (!penPath) return "";
      var pp = penPath;
      var tr = 'transform="translate(' + view.x + " " + view.y + ") scale(" + view.z + ')"';
      var out = [];
      var dOf = function (pi) {
        return pp.cs.filter(function (cn) {
          return cn.pi === pi && cn.pts.length;
        }).map(function (cn) {
          return penD(cn.pts, cn.closed, 0, 0);
        }).join("");
      };
      // Сам вектор — с его заливками и обводками (на время правки он скрыт).
      var el = pp.editId ? getEl(pp.editId) : null;
      if (el) {
        var st = el.style || {};
        var vb = el.vb || [el.w, el.h];
        var k = ((el.w / Math.max(0.01, vb[0])) + (el.h / Math.max(0.01, vb[1]))) / 2;
        el.paths.forEach(function (p0, pi) {
          var d = dOf(pp.alias && pp.alias[pi] != null ? pp.alias[pi] : pi);
          if (!d) return;
          var closed = /Z\s*$/i.test(d);
          var fill = st.fillOff ? "none" : p0.fill ? fillVar(st.fill && st.fill !== "none" ? st.fill : p0.fill) : st.fill && st.fill !== "none" && closed ? fillVar(st.fill) : "none";
          if (p0.fill && st.fill === "none") fill = "none";
          var stroke = p0.stroke && !st.strokeOff && st.strokeWidth !== 0 ? inkVar(st.stroke || p0.stroke) : "none";
          var sw = (st.strokeWidth != null ? Number(st.strokeWidth) : p0.sw || 1) * (st.strokeWidth != null ? 1 : k);
          out.push('<path ' + tr + ' d="' + d + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="' + sw + '"' +
            (p0.cap ? ' stroke-linecap="' + p0.cap + '"' : "") + (p0.join ? ' stroke-linejoin="' + p0.join + '"' : "") + opacityAttrs(st) + (p0.rule === "evenodd" ? ' fill-rule="evenodd"' : "") + "/>");
        });
      }
      pp.cs.forEach(function (cn) {
        if (cn.pts.length) out.push('<path ' + tr + ' d="' + penD(cn.pts, cn.closed, 0, 0) + '" class="lb-pen-draft" vector-effect="non-scaling-stroke"/>');
      });
      var act = penAct();
      var last = act && act.pts[act.pts.length - 1];
      if (pp.drawing && last && pp.cursor && !(gesture && gesture.kind === "pathpt")) {
        var a = toScreen(G.pt(last.x, last.y));
        var b = toScreen(pp.cursor);
        out.push('<line class="lb-pen-rubber" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '"/>');
      }
      // Ручки — у выбранной точки и её соседей (у всех сразу — каша на иконках), при рисовании — у последней.
      var showH = function (c, i) {
        var sl = pp.sel;
        if (!sl || sl.c !== c) return false;
        var n = pp.cs[c].pts.length;
        return i === sl.i || i === (sl.i + 1) % n || i === (sl.i - 1 + n) % n;
      };
      penAll(function (q, c, i) {
        if (!showH(c, i)) return;
        var s0 = toScreen(G.pt(q.x, q.y));
        [[q.ox, q.oy], [q.ix, q.iy]].forEach(function (hd) {
          if (!hd[0] && !hd[1]) return;
          var h1 = toScreen(G.pt(q.x + hd[0], q.y + hd[1]));
          out.push('<line class="lb-pen-handle" x1="' + s0.x + '" y1="' + s0.y + '" x2="' + h1.x + '" y2="' + h1.y + '"/>');
          out.push('<circle class="lb-pen-hdot" cx="' + h1.x + '" cy="' + h1.y + '" r="3.5"/>');
        });
      });
      penAll(function (q, c, i, cn) {
        var s0 = toScreen(G.pt(q.x, q.y));
        var sel = pp.sel && pp.sel.c === c && pp.sel.i === i;
        var cls = "lb-pen-pt" + (i === 0 && pp.drawing && c === pp.ci ? " is-first" : "") + (sel ? " is-sel" : "") +
          (!cn.closed && (i === 0 || i === cn.pts.length - 1) ? " is-end" : "");
        out.push('<rect class="' + cls + '" x="' + (s0.x - 4) + '" y="' + (s0.y - 4) + '" width="8" height="8"/>');
      });
      return out.join("");
    }

    /** Красные линии расстояний между прямоугольниками a и b (как во Figma по Alt). */
    function measureSvg(a, b) {
      var out = [];
      var line = function (x1, y1, x2, y2) {
        var d = Math.round(Math.abs(x2 - x1) + Math.abs(y2 - y1));
        if (d < 0.5) return;
        var p1 = toScreen(G.pt(x1, y1));
        var p2 = toScreen(G.pt(x2, y2));
        out.push('<line class="lb-measure" x1="' + p1.x + '" y1="' + p1.y + '" x2="' + p2.x + '" y2="' + p2.y + '"/>');
        var mx = (p1.x + p2.x) / 2;
        var my = (p1.y + p2.y) / 2;
        var t = String(d);
        var tw = 8 + t.length * 7;
        out.push('<g class="lb-measure-tag" transform="translate(' + mx + " " + my + ')"><rect x="' + -tw / 2 + '" y="-9" width="' + tw + '" height="18" rx="3"/><text y="4">' + t + "</text></g>");
      };
      var ax2 = a.x + a.w;
      var ay2 = a.y + a.h;
      var bx2 = b.x + b.w;
      var by2 = b.y + b.h;
      var cx = a.x + a.w / 2;
      var cy = a.y + a.h / 2;
      var inside = a.x >= b.x && a.y >= b.y && ax2 <= bx2 && ay2 <= by2;
      if (inside) {
        line(b.x, cy, a.x, cy);
        line(ax2, cy, bx2, cy);
        line(cx, b.y, cx, a.y);
        line(cx, ay2, cx, by2);
      } else {
        var oy = Math.max(a.y, b.y) < Math.min(ay2, by2) ? (Math.max(a.y, b.y) + Math.min(ay2, by2)) / 2 : cy;
        var ox = Math.max(a.x, b.x) < Math.min(ax2, bx2) ? (Math.max(a.x, b.x) + Math.min(ax2, bx2)) / 2 : cx;
        if (ax2 <= b.x) line(ax2, oy, b.x, oy);
        else if (bx2 <= a.x) line(bx2, oy, a.x, oy);
        if (ay2 <= b.y) line(ox, ay2, ox, b.y);
        else if (by2 <= a.y) line(ox, by2, ox, a.y);
      }
      var bb = toScreen(G.pt(b.x, b.y));
      var bb2 = toScreen(G.pt(bx2, by2));
      out.unshift('<rect class="lb-measure-box" x="' + bb.x + '" y="' + bb.y + '" width="' + Math.max(0, bb2.x - bb.x) + '" height="' + Math.max(0, bb2.y - bb.y) + '"/>');
      return out.join("");
    }

    /** Что мерить по Alt: объект под курсором (не из выделения), иначе экран, в котором лежит выделенное. */
    function updateMeasure(ev, movingIds) {
      var want = mode === "design" && ev && ev.altKey && selected.size && !editing;
      var next = null;
      if (want) {
        var ids = movingIds || withFrameKids(Array.from(selected));
        var skip = {};
        ids.forEach(function (id) {
          skip[id] = 1;
        });
        var a = selBounds();
        var p = toWorld(ev.clientX, ev.clientY);
        // Во время переноса цель не меняется: курсор теперь над самим объектом.
        var t = movingIds && measure && measure.tid && !skip[measure.tid] ? getEl(measure.tid) : shapeAt(p, skip);
        if (!t) {
          var first = getEl(Array.from(selected)[0]);
          var f = first ? frameOf(first) : null;
          if (gesture && gesture.kind === "move" && a) {
            // Перенос: экран, над серединой которого сейчас объект.
            var c = G.pt(a.x + a.w / 2, a.y + a.h / 2);
            f = null;
            els.forEach(function (fr) {
              if (fr.type === "frame" && !skip[fr.id] && G.contains(fr, c, 0) && (!f || fr.w * fr.h < f.w * f.h)) f = fr;
            });
          }
          t = f;
        }
        var b = t ? G.elBounds(t, getEl) : null;
        if (a && b) next = { a: a, b: b, tid: t.id };
      }
      if (JSON.stringify(next) !== JSON.stringify(measure)) {
        measure = next;
        renderOverlay();
      }
    }

    function rectPath(b, cls) {
      var a = toScreen(G.pt(b.x, b.y));
      return '<rect class="' + cls + '" x="' + a.x + '" y="' + a.y + '" width="' + Math.max(0, b.w * view.z) + '" height="' + Math.max(0, b.h * view.z) + '"/>';
    }

    // ---- выбор ----
    function expandGroups(ids) {
      var groups = {};
      ids.forEach(function (id) {
        var el = els.get(id);
        if (el && el.group) groups[el.group] = 1;
      });
      var out = new Set(ids);
      if (Object.keys(groups).length) {
        els.forEach(function (el) {
          if (el.group && groups[el.group]) out.add(el.id);
        });
      }
      return out;
    }

    function setSelection(ids) {
      selected = expandGroups(Array.from(ids || []));
      schedule();
    }

    function hitId(target) {
      var node = target && target.closest ? target.closest(".lb-el") : null;
      if (!node || !world.contains(node)) return "";
      var id = node.getAttribute("data-id") || "";
      // Заблокированный слой на доске не выделяется и не двигается (только в панели слоёв).
      return id && isLocked(getEl(id)) ? "" : id;
    }

    /** Скрыт / заблокирован сам или внутри скрытого / заблокированного фрейма. */
    function inherits(el, k) {
      for (var q = el, g = 0; q && g < 30; q = q.frame ? els.get(q.frame) : null, g++) if (q[k]) return true;
      return false;
    }

    function isHidden(el) {
      return !!el && inherits(el, "hidden");
    }

    function isLocked(el) {
      return !!el && inherits(el, "locked");
    }

    /** Фигура под точкой (для прилипания концов линий): верхняя по z, с запасом. */
    function shapeAt(p, exclude) {
      var list = sortedEls().reverse();
      var m = 10 / view.z;
      for (var i = 0; i < list.length; i++) {
        var el = list[i];
        if (!BOXED[el.type] || (exclude && exclude[el.id])) continue;
        if (G.contains(el, p, m)) return el;
      }
      return null;
    }

    function snapEnd(p, exclude, free) {
      if (free) return { end: null, el: null };
      var el = shapeAt(p, exclude);
      if (!el) return { end: null, el: null };
      var ns = G.nearestSide(el, p);
      if (ns.dist * view.z <= 18) return { end: { el: el.id, side: ns.side }, el: el, side: ns.side };
      return { end: { el: el.id }, el: el, side: "" };
    }

    // ---- текст ----
    function startEdit(id, opts2) {
      if (readOnly) return;
      var el = els.get(id);
      if (el && BLOCKS[el.type]) {
        startBlockEdit(id, opts2);
        return;
      }
      if (el && el.type === "frame") {
        setSelection([id]);
        flushNow();
        startFieldEdit(id, "name");
        return;
      }
      if (!el || !TEXTUAL[el.type]) return;
      finishEdit();
      setSelection([id]);
      flushNow();
      var node = nodes.get(id);
      if (!node) return;
      var t = ensureText(node);
      t.classList.remove("is-empty");
      // Подсветка — разметка вокруг текста; на время правки — просто текст.
      t.removeAttribute("data-k");
      if (t.querySelector(".lb-hl")) t.textContent = String(el.text || "");
      t.contentEditable = "plaintext-only";
      if (t.contentEditable !== "plaintext-only") t.contentEditable = "true";
      t.spellcheck = true;
      editing = { id: id, node: node, text: t, before: clone(el), isNew: !!(opts2 && opts2.isNew) };
      node.classList.add("is-editing");
      if (opts2 && typeof opts2.initial === "string") t.textContent = opts2.initial;
      t.focus({ preventScroll: true });
      var range = document.createRange();
      range.selectNodeContents(t);
      if (!(opts2 && opts2.selectAll)) range.collapse(false);
      // Двойной клик по тексту — курсор туда, куда кликнули (как во Figma), а не в конец.
      var at = opts2 && opts2.at;
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      if (at && !(opts2 && opts2.selectAll)) placeCaretAt(t, at.clientX, at.clientY);
      keepCaret(t);
      t.addEventListener("input", onEditInput);
      t.addEventListener("keydown", onEditKey);
      t.addEventListener("blur", onEditBlur);
      renderOverlay();
      renderProps();
    }

    /** Курсор в тексте — ближе всего к точке (по строке на той же высоте, иначе в конец). */
    function placeCaretAt(t, x, y) {
      var r = null;
      var tr = t.getBoundingClientRect();
      var cx = Math.max(tr.left + 1, Math.min(tr.right - 1, x));
      var cy = Math.max(tr.top + 1, Math.min(tr.bottom - 1, y));
      var pe = [];
      // То, что лежит поверх текста, на время поиска не ловит точку.
      for (var top = document.elementFromPoint(cx, cy), g = 0; top && !t.contains(top) && g < 8; top = document.elementFromPoint(cx, cy), g++) {
        pe.push([top, top.style.pointerEvents]);
        top.style.pointerEvents = "none";
      }
      try {
        if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(cx, cy);
        else if (document.caretPositionFromPoint) {
          var cp = document.caretPositionFromPoint(cx, cy);
          if (cp) {
            r = document.createRange();
            r.setStart(cp.offsetNode, cp.offset);
            r.collapse(true);
          }
        }
      } finally {
        pe.forEach(function (q) {
          q[0].style.pointerEvents = q[1];
        });
      }
      if (!r || !t.contains(r.startContainer)) {
        r = document.createRange();
        r.selectNodeContents(t);
        r.collapse(false);
      }
      if (document.activeElement !== t) t.focus({ preventScroll: true });
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
    }

    /** Курсор остаётся в новом блоке: после нажатия браузер своим действием по умолчанию (клик,
     * двойной клик — выделение слова) переставлял выделение мимо блока, и набор шёл в никуда. */
    function keepCaret(t) {
      var fix = function () {
        if (!editing || editing.text !== t || !t.isConnected) return;
        if (document.activeElement !== t) t.focus({ preventScroll: true });
        var sel = window.getSelection();
        if (sel && sel.rangeCount && t.contains(sel.anchorNode)) return;
        var r = document.createRange();
        r.selectNodeContents(t);
        r.collapse(false);
        sel.removeAllRanges();
        sel.addRange(r);
      };
      setTimeout(fix, 0);
      setTimeout(fix, 160);
    }

    function onEditInput() {
      if (!editing) return;
      var el = els.get(editing.id);
      if (!el) return;
      if (el.type === "text") {
        var th = textHeight(editing.text);
        if (th != null) editing.node.style.height = Math.max(20, Math.ceil(th)) + "px";
      } else {
        var need = growToText(el, editing.text);
        if (need) editing.node.style.height = need + "px";
      }
    }

    function onEditKey(e) {
      if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        e.stopPropagation();
        finishEdit();
        stage.focus({ preventScroll: true });
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        finishEdit();
        stage.focus({ preventScroll: true });
      }
      e.stopPropagation();
    }

    function onEditBlur() {
      setTimeout(function () {
        if (editing && document.activeElement !== editing.text) finishEdit();
      }, 0);
    }

    function finishEdit() {
      if (!editing) return;
      var ed = editing;
      editing = null;
      if (ed.kind) {
        finishBlockEdit(ed);
        return;
      }
      ed.text.removeEventListener("input", onEditInput);
      ed.text.removeEventListener("keydown", onEditKey);
      ed.text.removeEventListener("blur", onEditBlur);
      ed.text.contentEditable = "false";
      ed.text.removeAttribute("contenteditable");
      ed.node.classList.remove("is-editing");
      var text = String(ed.text.innerText || "").replace(/ /g, " ").replace(/\n+$/, "");
      var el = els.get(ed.id);
      if (!el) return;
      begin();
      if (el.type === "text" && !text.trim()) {
        remove(ed.id);
      } else {
        var fields = (el.text || "") === text ? {} : { text: text };
        if (el.type === "text") {
          var th = textHeight(ed.text);
          if (th != null && !el.rot) fields.h = Math.max(20, Math.ceil(th));
        } else {
          var need = growToText(el, ed.text);
          if (need) fields.h = need;
        }
        patch(ed.id, fields);
      }
      // Новый элемент и его текст — один шаг отмены.
      if (ed.isNew && undoStack.length && tx && tx.depth === 1) {
        var last = undoStack[undoStack.length - 1];
        if (last.length === 1 && last[0].id === ed.id && last[0].before === null) {
          undoStack.pop();
          tx.before.set(ed.id, null);
        }
      }
      commit();
      schedule();
    }

    // ---- правка блоков ----
    /** Двойное нажатие / Enter по блоку: таблица — курсор в ячейку, карточка и Гант — правка поля. */
    function startBlockEdit(id, opts2) {
      var el = els.get(id);
      if (!el) return;
      finishEdit();
      setSelection([id]);
      flushNow();
      var node = nodes.get(id);
      if (!node) return;
      var at = opts2 && opts2.at;
      var target = at ? document.elementFromPoint(at.clientX, at.clientY) : null;
      if (target && !node.contains(target)) target = null;
      if (el.type === "table") {
        var tb = tables.get(id);
        var host = node.querySelector(":scope > .lb-table");
        if (!tb || !host) return;
        tb.setEditable(true);
        editing = { id: id, kind: "table", node: node, text: host, before: clone(el) };
        node.classList.add("is-editing");
        if (at) tb.focusCellAt(at.clientX, at.clientY);
        else tb.focus();
        host.addEventListener("keydown", onBlockKey);
        host.addEventListener("focusout", onBlockBlur);
        renderOverlay();
        renderProps();
        return;
      }
      if (el.type === "card") {
        var f = target && target.closest ? target.closest("[data-f]") : null;
        startFieldEdit(id, f ? f.getAttribute("data-f") : "title");
        return;
      }
      if (el.type === "gantt") {
        var tasks = el.tasks || [];
        var cell = target && target.closest ? target.closest(".lb-g-cell") : null;
        var bar = target && target.closest ? target.closest(".lb-g-bar") : null;
        var task = cell ? cell.getAttribute("data-task") : bar ? bar.getAttribute("data-task") : tasks.length ? tasks[0].id : "";
        if (task) startFieldEdit(id, cell ? cell.getAttribute("data-col") : "name", task);
      }
    }

    /** Поле карточки (должность / имя / обязанности) или ячейка Ганта (task) — текст на месте. */
    function startFieldEdit(id, field, task) {
      var node = nodes.get(id);
      if (!node) return;
      var sel = task
        ? '.lb-g-cell[data-task="' + cssEsc(task) + '"][data-col="' + field + '"]'
        : '[data-f="' + field + '"]';
      var t = node.querySelector(sel);
      if (!t) return;
      var el = els.get(id);
      if (task) {
        gSel = { id: id, task: task };
        var tk = (el.tasks || []).filter(function (x) {
          return x.id === task;
        })[0];
        // В ячейке даты — полная дата, чтобы можно было сменить и год.
        if (tk && (field === "start" || field === "end")) t.textContent = BL.fmtDay(tk[field]);
      }
      t.classList.remove("is-empty");
      t.contentEditable = "plaintext-only";
      if (t.contentEditable !== "plaintext-only") t.contentEditable = "true";
      t.spellcheck = true;
      editing = { id: id, kind: task ? "gcell" : "field", field: field, task: task || "", node: node, text: t, before: clone(el) };
      node.classList.add("is-editing");
      t.focus({ preventScroll: true });
      var range = document.createRange();
      range.selectNodeContents(t);
      // Ячейка Ганта — текст выделен целиком (набор заменяет), поле карточки — курсор в конец.
      if (!task) range.collapse(false);
      var ws = window.getSelection();
      ws.removeAllRanges();
      ws.addRange(range);
      t.addEventListener("keydown", onBlockKey);
      keepCaret(t);
      t.addEventListener("blur", onBlockBlur);
      renderOverlay();
      renderProps();
    }

    function cssEsc(v) {
      return String(v).replace(/["\\]/g, "\\$&");
    }

    var FIELD_ORDER = { field: ["title", "name", "text"], gcell: ["name", "start", "end", "owner"] };

    function onBlockKey(e) {
      if (!editing) return;
      var ed = editing;
      var multi = ed.kind === "table" || (ed.kind === "field" && ed.field === "text");
      if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey || !multi))) {
        e.preventDefault();
        e.stopPropagation();
        finishEdit();
        stage.focus({ preventScroll: true });
        return;
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "a" || e.key === "ф") && ed.kind !== "table") {
        // ⌘A — весь текст поля, а не вся страница.
        e.preventDefault();
        e.stopPropagation();
        var all = document.createRange();
        all.selectNodeContents(ed.text);
        var ws = window.getSelection();
        ws.removeAllRanges();
        ws.addRange(all);
        return;
      }
      if (e.key === "Tab" && ed.kind !== "table") {
        // Tab — следующее поле (Shift — предыдущее), как по ячейкам таблицы.
        e.preventDefault();
        var order = FIELD_ORDER[ed.kind];
        var i = order.indexOf(ed.field) + (e.shiftKey ? -1 : 1);
        finishEdit();
        flushNow();
        if (i >= 0 && i < order.length) startFieldEdit(ed.id, order[i], ed.task || undefined);
        else stage.focus({ preventScroll: true });
      }
      e.stopPropagation();
    }

    function onBlockBlur() {
      setTimeout(function () {
        if (!editing || !editing.kind) return;
        var a = document.activeElement;
        if (a && editing.text.contains(a)) return;
        // Подтверждение «Удалить всю таблицу?» — поверх, правку не закрываем.
        if (editing.kind === "table" && document.querySelector(".note-table-confirm-overlay")) return;
        finishEdit();
      }, 0);
    }

    function finishBlockEdit(ed) {
      ed.text.removeEventListener("keydown", onBlockKey);
      ed.text.removeEventListener("blur", onBlockBlur);
      ed.text.removeEventListener("focusout", onBlockBlur);
      ed.node.classList.remove("is-editing");
      var el = els.get(ed.id);
      if (ed.kind === "table") {
        var tb = tables.get(ed.id);
        if (tb) {
          tb.setEditable(false);
          if (el) el = Object.assign({}, el, { html: tb.getHtml() });
        }
      } else {
        ed.text.contentEditable = "false";
        ed.text.removeAttribute("contenteditable");
      }
      if (!el) return;
      var text = ed.kind === "table" ? "" : String(ed.text.innerText || "").replace(/\u00a0/g, " ").replace(/\n+$/, "");
      begin();
      if (!tx.before.has(ed.id)) tx.before.set(ed.id, clone(ed.before));
      if (ed.kind === "table") {
        put(el);
      } else if (ed.kind === "field") {
        var f = {};
        f[ed.field] = ed.field === "text" ? text : text.replace(/\s*\n\s*/g, " ").trim();
        patch(ed.id, f);
      } else {
        patchTask(ed.id, ed.task, function (tk) {
          if (ed.field === "start" || ed.field === "end") {
            var d = BL.parseDay(text, tk[ed.field]);
            if (!d) {
              notify("Дата — в виде 05.11 или 05.11.2026");
              return null;
            }
            var len = BL.diffDays(tk.start, tk.end);
            var next = Object.assign({}, tk);
            next[ed.field] = d;
            // Новое начало сдвигает задачу целиком; конец раньше начала — начало подтягивается.
            if (ed.field === "start") next.end = BL.addDays(d, len);
            else if (next.end < next.start) next.start = next.end;
            return next;
          }
          var v = text.replace(/\s*\n\s*/g, " ").trim();
          var nt = Object.assign({}, tk);
          if (v) nt[ed.field] = v;
          else delete nt[ed.field];
          return nt;
        });
      }
      commit();
      // Блок перерисуется заново (поля правки — обычный текст).
      var box = ed.node.querySelector(":scope > .lb-block");
      if (box) box.removeAttribute("data-src");
      markDirty(ed.id);
    }

    /** Правка одной задачи Ганта (fn → новая задача или null — без изменений); шкала подстраивается. */
    function patchTask(id, taskId, fn) {
      var el = els.get(id);
      if (!el || el.type !== "gantt") return;
      var changed = false;
      var tasks = (el.tasks || []).map(function (t) {
        if (t.id !== taskId) return t;
        var n = fn(clone(t));
        if (!n || same(n, t)) return t;
        changed = true;
        return n;
      });
      if (changed) put(BL.ganttFit(Object.assign({}, el, { tasks: tasks })));
      else markDirty(id);
    }

    function setTasks(id, tasks) {
      var el = els.get(id);
      if (!el) return;
      put(BL.ganttFit(Object.assign({}, el, { tasks: tasks })));
    }

    function flushNow() {
      if (frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
      flush();
    }

    // ---- создание ----
    // Макет: как во Figma — серая заливка без рамки, чёрный текст (экраны белые, цвет темы тут не к месту).
    var DESIGN_STYLE = {
      rect: { fill: "#d9d9d9", stroke: "#000000", strokeWidth: 0, fontSize: 16, textColor: "#000000", radius: 0 },
      ellipse: { fill: "#d9d9d9", stroke: "#000000", strokeWidth: 0, fontSize: 16, textColor: "#000000" },
      text: { fontSize: 16, textColor: "#000000", align: "left" },
      line: { stroke: "#000000", strokeWidth: 1 },
      pen: { stroke: "#000000", strokeWidth: 2 },
      frame: { fill: "#ffffff", stroke: "#000000", strokeWidth: 0 },
    };

    function styleFor(type) {
      var base = clone((mode === "design" && DESIGN_STYLE[type]) || (DEFAULTS[type] || {}).style || {});
      return Object.assign(base, lastStyle[mode + ":" + type] || {});
    }

    function newEl(type, p, extra) {
      var d = DEFAULTS[type] || {};
      var el = Object.assign(
        { id: uid(), type: type, x: p.x, y: p.y, w: d.w || 0, h: d.h || 0, z: topZ(), style: styleFor(type) },
        extra || {}
      );
      if (type === "sticky" && !el.author && meAuthor()) el.author = meAuthor();
      return el;
    }

    /** Кто я — автор новых стикеров (подпись внизу стикера, как у комментариев). */
    function meAuthor() {
      var me = o.me;
      if (!me || !(me.id || me.name)) return null;
      var a = {};
      if (me.id) a.id = String(me.id);
      if (me.name) a.name = String(me.name);
      return a;
    }

    function createAtCenter(type, extra) {
      var r = stageRect();
      var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
      var d = DEFAULTS[type] || { w: 100, h: 100 };
      var w = (extra && extra.w) || d.w;
      var h = (extra && extra.h) || d.h;
      begin();
      var el = newEl(type, G.pt(c.x - w / 2, c.y - h / 2), extra);
      put(el);
      commit();
      setSelection([el.id]);
      return el;
    }

    function pickFile(accept, cb) {
      var input = document.createElement("input");
      input.type = "file";
      input.accept = accept;
      input.style.display = "none";
      document.body.appendChild(input);
      input.addEventListener("change", function () {
        var f = input.files && input.files[0];
        input.remove();
        if (f) cb(f);
      });
      input.click();
    }

    /** Фото в карточку: квадрат до 400 px (в карточке — круг). */
    function pickCardPhoto(id) {
      if (readOnly) return;
      pickFile("image/*", function (file) {
        if (!/^image\//i.test(String(file.type || ""))) return;
        if (file.size > 25 * 1024 * 1024) {
          notify("Картинка больше 25 МБ");
          return;
        }
        readFileAsDataUrl(file).then(function (src) {
          return compressImage(src, CARD_PHOTO_SIDE);
        }).then(function (img) {
          if (!img) {
            notify("Не удалось открыть картинку");
            return;
          }
          if (!getEl(id)) return;
          begin();
          patch(id, { photo: img.src });
          commit();
        });
      });
    }

    /** «Подчинённый»: пустая карточка под этой, со связью-ломаной. */
    function addSubordinate(id) {
      var from = getEl(id);
      if (!from || readOnly) return;
      var kids = [];
      els.forEach(function (ln) {
        if (ln.type === "line" && ln.start && ln.start.el === id && ln.end) {
          var k = getEl(ln.end.el);
          if (k && k.type === "card") kids.push(k);
        }
      });
      var gap = 60;
      var x = kids.length ? Math.max.apply(null, kids.map(function (k) {
        return k.x + k.w;
      })) + gap : from.x;
      var y = kids.length ? kids[0].y : from.y + from.h + 100;
      var fill = (from.style && from.style.fill) === "purple" ? "green" : (from.style && from.style.fill) || "green";
      begin();
      var card = newEl("card", G.pt(x, y), { w: from.w, h: DEFAULTS.card.h, style: { fill: fill } });
      put(card);
      put(newEl("line", G.pt(0, 0), {
        points: [[0, 0], [1, 1]], start: { el: id, side: "bottom" }, end: { el: card.id, side: "top" }, route: "elbow",
        style: styleFor("line"),
      }));
      commit();
      flushNow();
      revealElement(card.id);
      startFieldEdit(card.id, "title");
    }

    function addTask(id) {
      var el = getEl(id);
      if (!el || el.type !== "gantt" || readOnly) return;
      var t = BL.newTask(el, uid());
      begin();
      setTasks(id, (el.tasks || []).concat([t]));
      commit();
      gSel = { id: id, task: t.id };
      setSelection([id]);
      flushNow();
      startFieldEdit(id, "name", t.id);
    }

    /** Свободное место под блок: центр экрана, а если там уже что-то есть — под всем содержимым. */
    function freeSpot(w, h) {
      var r = stageRect();
      var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
      var box = { x: c.x - w / 2, y: c.y - h / 2, w: w, h: h };
      var busy = false;
      els.forEach(function (el) {
        if (busy || !BOXED[el.type]) return;
        var b = G.elBounds(el, getEl);
        if (b && G.intersects(b, box)) busy = true;
      });
      if (!busy) return G.pt(box.x, box.y);
      var cb = contentBounds();
      return G.pt(cb.x, cb.y + cb.h + 60);
    }

    function insertTable() {
      finishEdit();
      var d = DEFAULTS.table;
      var at = freeSpot(d.w, d.h);
      begin();
      var el = newEl("table", at, { html: TABLE_HTML });
      delete el.style;
      put(el);
      syncFrames();
      commit();
      setSelection([el.id]);
      flushNow();
      revealElement(el.id);
      setTool("select");
      flushNow();
      startBlockEdit(el.id);
    }

    // ---- шаблоны ----
    function boundsOfList(list) {
      return G.unionBounds(list.filter(function (e) {
        return e.type !== "line";
      }).map(function (e) {
        return { x: e.x, y: e.y, w: e.w, h: e.h };
      }));
    }

    /** Шаблон на мок-данных: в центр пустой доски или справа от того, что уже есть; один шаг отмены. */
    function insertTemplate(id) {
      if (readOnly || !TPL) return;
      finishEdit();
      closePop();
      var list = TPL.build(id, { uid: uid, today: BL.today() });
      var b = boundsOfList(list);
      if (!b) return;
      var cb = contentBounds();
      var dx;
      var dy;
      if (cb) {
        dx = cb.x + cb.w + 160 - b.x;
        dy = cb.y - b.y;
      } else {
        var r = stageRect();
        var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
        dx = Math.round(c.x - b.w / 2 - b.x);
        dy = Math.round(c.y - b.h / 2 - b.y);
      }
      var z = topZ();
      begin();
      list.forEach(function (el, i) {
        el.x += dx;
        el.y += dy;
        if (el.type === "line") el.points = el.points.map(function (q) {
          return [q[0] + dx, q[1] + dy];
        });
        el.z = z + i;
        put(el);
      });
      commit();
      setTool("select");
      flushNow();
      autoFit = false;
      fitBounds(G.unionBounds([cb, { x: b.x + dx, y: b.y + dy, w: b.w, h: b.h }].filter(Boolean)));
      setSelection(list.filter(function (e) {
        return e.type !== "line";
      }).map(function (e) {
        return e.id;
      }));
    }

    function notifyImported(imp) {
      var nBoxes = imp.elements.filter(function (x) {
        return x.type !== "line";
      }).length;
      notify("Вставлено из " + imp.source + ": " + nBoxes + " " + pluralBlocks(nBoxes) + (imp.skipped ? " · не перенеслось: " + imp.skipped : "") +
        (imp.imageSlots ? " · картинки — заглушки: выделите и вставьте картинку" : ""));
    }

    function pluralBlocks(n) {
      var m10 = n % 10;
      var m100 = n % 100;
      if (m10 === 1 && m100 !== 11) return "блок";
      if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return "блока";
      return "блоков";
    }

    /** Вставленная схема (Miro): в центр экрана, одним шагом отмены, видна целиком и выделена. */
    function placeImported(list, opts) {
      opts = opts || {};
      finishEdit();
      var b = boundsOfList(list);
      if (!b) return;
      var r = stageRect();
      var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
      var dx = Math.round(c.x - (b.x + b.w / 2));
      var dy = Math.round(c.y - (b.y + b.h / 2));
      // Макет из Figma — на свободное место справа от всего (не поверх чужих фреймов).
      var cb = opts.keepFrames ? contentBounds() : null;
      if (cb) {
        dx = Math.round(cb.x + cb.w + 160 - b.x);
        dy = Math.round(cb.y - b.y);
      }
      var z = topZ();
      begin();
      list.forEach(function (el, i) {
        el.x += dx;
        el.y += dy;
        if (el.waypoints) el.waypoints = shiftWaypoints(el.waypoints, dx, dy);
        el.z = z + i;
        put(el);
      });
      // Принадлежность к фреймам пришла из Figma — геометрией не пересчитываем (вылезающее за край остаётся внутри).
      if (!opts.keepFrames) syncFrames();
      commit();
      setTool("select");
      flushNow();
      autoFit = false;
      var nb = { x: b.x + dx, y: b.y + dy, w: b.w, h: b.h };
      var tl = toScreen(G.pt(nb.x, nb.y));
      var br = toScreen(G.pt(nb.x + nb.w, nb.y + nb.h));
      if (tl.x < 0 || tl.y < 0 || br.x > r.width || br.y > r.height) fitBounds(nb);
      var ids0 = {};
      list.forEach(function (e) {
        ids0[e.id] = 1;
      });
      // Выделены верхние вставленные (содержимое фреймов — вместе с ними).
      setSelection(list.filter(function (e) {
        return e.type !== "line" && !(e.frame && ids0[e.frame]);
      }).map(function (e) {
        return e.id;
      }));
    }

    /** Меню шаблона: «Пустой шаблон» — мок-данные, «На моих данных» — Лео соберёт по заметке. */
    function openTemplateMenu(anchor, id) {
      openPop(anchor, function (box) {
        box.innerHTML =
          '<button type="button" class="lb-menu-item" data-tm="empty">' + icon("templates") + "Пустой шаблон</button>" +
          (o.onTemplateAsk
            ? '<button type="button" class="lb-menu-item" data-tm="ask">' + NOTE_ACTS[0].svg + "На моих данных</button>"
            : "");
        box.onclick = function (ev) {
          var m = ev.target.closest("[data-tm]");
          if (!m) return;
          closePop();
          if (m.getAttribute("data-tm") === "empty") insertTemplate(id);
          else if (o.onTemplateAsk) o.onTemplateAsk(id);
        };
      });
    }

    /** Пустая доска: шаблоны; пустой макет — экраны по размерам устройств. */
    function renderEmptyHint() {
      if (readOnly || !emptyHint.classList.contains("has-start")) return;
      if (mode === "design") {
        emptyHint.innerHTML = '<div class="lb-start-title">Добавьте фрейм</div><div class="lb-start-devices">' +
          DEVICES.map(function (d) {
            return '<button type="button" class="lb-device" data-device="' + d.id + '"><span class="lb-device-pre" style="--ar:' + d.w / d.h + '"></span>' +
              '<span class="lb-tpl-t">' + esc(d.title) + '</span><span class="lb-tpl-h">' + d.w + " × " + d.h + "</span></button>";
          }).join("") +
          '</div><div class="lb-start-hint">или вставьте макет из Figma (⌘V) · инструмент «Фрейм» (F) — свой размер</div>';
      } else {
        emptyHint.innerHTML = '<div class="lb-start-title">С чего начнём?</div><div class="lb-start-cards">' + templateCards() + "</div>" +
          '<div class="lb-start-hint">или выберите инструмент и начните рисовать</div>';
      }
    }

    function templateCards() {
      return (TPL ? TPL.LIST : []).map(function (t) {
        return '<button type="button" class="lb-tpl" data-tpl="' + t.id + '"><span class="lb-tpl-pre">' + (TPL.PREVIEW[t.id] || "") + "</span>" +
          '<span class="lb-tpl-t">' + esc(t.title) + '</span><span class="lb-tpl-h">' + esc(t.hint) + "</span></button>";
      }).join("");
    }

    function openTemplatesPop(anchor) {
      openPop(anchor, function (box) {
        box.innerHTML = '<div class="lb-tpl-list">' + templateCards() + "</div>";
        box.onclick = function (ev) {
          var c = ev.target.closest("[data-tpl]");
          if (!c) return;
          var tid = c.getAttribute("data-tpl");
          // Второй шаг — то же меню у кнопки «Шаблоны».
          closePop();
          openTemplateMenu(anchor, tid);
        };
      });
    }

    function pickImage() {
      var input = document.createElement("input");
      input.type = "file";
      input.accept = "image/*";
      input.style.display = "none";
      document.body.appendChild(input);
      input.addEventListener("change", function () {
        var f = input.files && input.files[0];
        input.remove();
        if (f) insertImageFile(f);
      });
      input.click();
    }

    function insertImageFile(file, at) {
      if (!file || !/^image\//i.test(String(file.type || ""))) return Promise.resolve();
      if (file.size > 25 * 1024 * 1024) {
        notify("Картинка больше 25 МБ");
        return Promise.resolve();
      }
      // Выделена заглушка картинки (из Miro) — картинка встаёт на её место и в её размер.
      var slot = selList().length === 1 ? selList()[0] : null;
      if (!(slot && slot.type === "rect" && /^Картинка из (Miro|Figma)/.test(String(slot.text || slot.name || "")))) slot = null;
      return readFileAsDataUrl(file).then(compressImage).then(function (img) {
        if (!img) {
          notify("Не удалось открыть картинку");
          return;
        }
        if (slot && getEl(slot.id)) {
          begin();
          remove(slot.id);
          var pic = newEl("image", G.pt(slot.x, slot.y), { w: slot.w, h: slot.h, src: img.src, z: slot.z, style: undefined });
          delete pic.style;
          if (slot.rot) pic.rot = slot.rot;
          put(pic);
          commit();
          setSelection([pic.id]);
          setTool("select");
          return;
        }
        var k = Math.min(1, 480 / Math.max(img.w, img.h));
        var w = Math.round(img.w * k);
        var h = Math.round(img.h * k);
        if (at) {
          begin();
          var el = newEl("image", G.pt(at.x - w / 2, at.y - h / 2), { w: w, h: h, src: img.src, style: undefined });
          delete el.style;
          put(el);
          commit();
          setSelection([el.id]);
        } else {
          var el2 = createAtCenter("image", { w: w, h: h, src: img.src });
          delete el2.style;
        }
        setTool("select");
      });
    }

    function notify(text) {
      if (typeof o.onNotify === "function") o.onNotify(text);
    }

    // ---- инструменты и панели ----
    var lastShape = "rect";
    function setTool(t) {
      if (penPath && t !== "path") exitPen();
      tool = t;
      stage.setAttribute("data-tool", t);
      var sb = toolsBar && toolsBar.querySelector(".lb-shape-tool");
      if (sb && (SHAPES[t] || t === "rectSharp") && mode !== "design" && t !== lastShape) {
        lastShape = t;
        sb.setAttribute("data-tool", t);
        sb.innerHTML = icon(t) + '<span class="lb-caret" aria-hidden="true"></span>';
      }
      if (toolsBar) {
        toolsBar.querySelectorAll("[data-tool]").forEach(function (b) {
          b.classList.toggle("is-on", b.getAttribute("data-tool") === t);
        });
      }
    }

    function toolList() {
      return mode === "design" ? TOOLS_DESIGN : TOOLS;
    }

    function drawTools() {
      if (!toolsBar) return;
      var grouped = false;
      toolsBar.innerHTML = toolList().map(function (t) {
        if (t.group === "shape") {
          if (grouped) return "";
          grouped = true;
          // Одна кнопка «Фигуры»: последняя выбранная фигура; нажатие — набор всех фигур.
          return '<button type="button" class="lb-btn lb-shape-tool" data-tool="' + lastShape + '" data-act="shapes" title="Фигуры" aria-label="Фигуры" aria-haspopup="true">' +
            icon(lastShape) + '<span class="lb-caret" aria-hidden="true"></span></button>';
        }
        return '<button type="button" class="lb-btn" data-tool="' + t.id + '" title="' + esc(t.title) + '" aria-label="' + esc(t.title) + '">' + icon(t.icon) + "</button>";
      }).join("") + (TPL && mode !== "design" ? '<span class="lb-sep"></span><button type="button" class="lb-btn" data-act="templates" title="Шаблоны" aria-label="Шаблоны">' + icon("templates") + "</button>" : "");
      setTool(toolList().some(function (t) {
        return t.id === tool;
      }) ? tool : "select");
    }

    /** Набор фигур (как в Miro): сетка 3×3, выбор — инструмент; R / D / O — с клавиатуры. */
    function openShapesPop(btn) {
      openPop(btn, function (box) {
        box.innerHTML = '<div class="lb-shapes">' + SHAPE_MENU.map(function (t) {
          var key = (TOOLS.filter(function (x) {
            return x.id === t;
          })[0] || {}).key;
          var tt = SHAPE_RU[t] + (key ? " (" + key.toUpperCase() + ")" : "");
          return '<button type="button" class="lb-btn' + (t === tool ? " is-on" : "") + '" data-t="' + t + '" title="' + esc(tt) + '" aria-label="' + esc(tt) + '">' + icon(t) + "</button>";
        }).join("") + "</div>";
        box.onclick = function (ev) {
          var b2 = ev.target.closest("[data-t]");
          if (!b2) return;
          setTool(b2.getAttribute("data-t"));
          closePop();
          stage.focus({ preventScroll: true });
        };
      });
    }

    function renderTools() {
      if (!toolsBar) return;
      drawTools();
      var syncToolsEnd = function () {
        toolsBar.classList.toggle("is-scrolled-end", toolsBar.scrollLeft + toolsBar.clientWidth >= toolsBar.scrollWidth - 4);
      };
      toolsBar.addEventListener("scroll", syncToolsEnd, { passive: true });
      setTimeout(syncToolsEnd, 0);
      toolsBar.addEventListener("click", function (e) {
        var ta = e.target.closest("[data-act=templates]");
        if (ta) {
          finishEdit();
          openTemplatesPop(ta);
          return;
        }
        var sh = e.target.closest("[data-act=shapes]");
        if (sh) {
          finishEdit();
          openShapesPop(sh);
          return;
        }
        var b = e.target.closest("[data-tool]");
        if (!b) return;
        var t = b.getAttribute("data-tool");
        finishEdit();
        if (t === "image") {
          pickImage();
          return;
        }
        if (t === "table") {
          insertTable();
          return;
        }
        setTool(t);
        stage.focus({ preventScroll: true });
      });
    }

    // ---- режим «Доска» / «Макет» ----
    /** Режим — общий для всех участников (Yjs meta) и в теле листа; отменой не откатывается. */
    function applyMode(m) {
      m = m === "design" ? "design" : "board";
      if (m === mode && root.classList.contains("is-mode-" + m)) return;
      mode = m;
      root.classList.toggle("is-mode-design", m === "design");
      root.classList.toggle("is-mode-board", m !== "design");
      if (typeof o.onModeChange === "function") o.onModeChange(m);
      drawTools();
      renderEmptyHint();
      zoomBar.removeAttribute("data-pct");
      renderZoomBar();
      schedule();
    }

    function setMode(m) {
      if (readOnly || m === mode) return;
      finishEdit();
      applyMode(m);
      if (yjs) {
        yjs.doc.transact(function () {
          yjs.doc.getMap("meta").set("mode", mode);
        }, "leo-board-local");
      }
      if (changeTimer) clearTimeout(changeTimer);
      changeTimer = setTimeout(emitChange, 250);
      notify(mode === "design" ? "Режим «Макет»: фреймы, точные размеры, передача в разработку" : "Режим «Доска»");
    }

    function renderHistoryBar() {
      if (!historyBar) return;
      historyBar.innerHTML =
        '<button type="button" class="lb-btn" data-act="undo" title="Отменить (⌘Z)"' + (undoStack.length ? "" : " disabled") + ">" + icon("undo") + "</button>" +
        '<button type="button" class="lb-btn" data-act="redo" title="Повторить (⇧⌘Z)"' + (redoStack.length ? "" : " disabled") + ">" + icon("redo") + "</button>";
    }

    // Чат с Лео (кнопка «Обсуждение», как в нижней панели заметки): бейдж — число непрочитанных или точка.
    var chatBadge = "";
    var CHAT_SVG = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';

    function renderZoomBar() {
      var pct = Math.round(view.z * 100) + "%";
      var stateKey = pct + showGrid + showMap + "|" + chatBadge + "|" + mode + "|" + layersOn();
      if (zoomBar.getAttribute("data-pct") === stateKey) return;
      zoomBar.setAttribute("data-pct", stateKey);
      zoomBar.innerHTML =
        (readOnly ? "" : '<button type="button" class="lb-btn' + (layersOn() ? " is-on" : "") + '" data-act="layers" title="Слои">' + icon("layers") + "</button>" +
          '<span class="lb-mode" role="group" aria-label="Режим">' +
          '<button type="button" class="lb-btn' + (mode !== "design" ? " is-on" : "") + '" data-act="mode-board" title="Режим «Доска»: схемы, стикеры, шаблоны">' + icon("modeBoard") + "</button>" +
          '<button type="button" class="lb-btn' + (mode === "design" ? " is-on" : "") + '" data-act="mode-design" title="Режим «Макет»: фреймы для разработки">' + icon("modeDesign") + "</button>" +
          '</span><span class="lb-sep"></span>') +
        '<button type="button" class="lb-btn" data-act="zoom-out" title="Отдалить (−)">' + icon("minus") + "</button>" +
        '<button type="button" class="lb-btn lb-pct" data-act="zoom-100" title="Масштаб 100%">' + pct + "</button>" +
        '<button type="button" class="lb-btn" data-act="zoom-in" title="Приблизить (+)">' + icon("plus") + "</button>" +
        '<button type="button" class="lb-btn" data-act="fit" title="Показать всё (⇧1)">' + icon("fit") + "</button>" +
        '<button type="button" class="lb-btn lb-map-btn' + (showMap ? " is-on" : "") + '" data-act="map" title="Мини-карта">' + icon("map") + "</button>" +
        (readOnly
          ? ""
          : '<button type="button" class="lb-btn' + (showGrid ? " is-on" : "") + '" data-act="grid" title="Сетка и привязка">' + icon("grid") + "</button>" +
            '<button type="button" class="lb-btn" data-act="export" title="Экспорт">' + icon("download") + "</button>") +
        (o.onOpenChat
          ? '<span class="lb-sep"></span><button type="button" class="lb-btn lb-chat-btn" data-act="chat" title="Обсуждение с Лео" aria-label="Обсуждение">' + CHAT_SVG +
            (chatBadge ? '<span class="lb-chat-badge' + (chatBadge === "•" ? " is-dot" : "") + '">' + (chatBadge === "•" ? "" : esc(chatBadge)) + "</span>" : "") +
            "</button>"
          : "");
      minimap.classList.toggle("hidden", !showMap);
    }

    function onBarClick(e) {
      var b = e.target.closest("[data-act]");
      if (!b || b.disabled) return;
      var act = b.getAttribute("data-act");
      if (/^zoom|^fit$/.test(act)) autoFit = false;
      if (act === "chat") {
        if (editing) finishEdit();
        if (o.onOpenChat) o.onOpenChat();
        return;
      }
      if (act === "mode-board" || act === "mode-design") {
        setMode(act === "mode-design" ? "design" : "board");
        return;
      }
      if (act === "undo") undo();
      else if (act === "redo") redo();
      else if (act === "zoom-in") zoomBy(1.25);
      else if (act === "zoom-out") zoomBy(0.8);
      else if (act === "zoom-100") zoomTo(1);
      else if (act === "fit") fitAll();
      else if (act === "grid") {
        showGrid = !showGrid;
        applyView();
      } else if (act === "layers") {
        showLayers[mode] = !layersOn();
        try {
          localStorage.setItem("leo-board-layers-" + mode, showLayers[mode] ? "1" : "0");
        } catch (_) {}
        renderZoomBar();
        renderLayers();
      } else if (act === "map") {
        showMap = !showMap;
        renderZoomBar();
        scheduleMinimap();
      } else if (act === "export") {
        openPop(b, function (box) {
          box.innerHTML =
            '<button type="button" class="lb-menu-item" data-x="png">Скачать PNG</button>' +
            '<button type="button" class="lb-menu-item" data-x="svg">Скачать SVG</button>';
          box.addEventListener("click", function (ev) {
            var x = ev.target.closest("[data-x]");
            if (!x) return;
            closePop();
            if (x.getAttribute("data-x") === "png") exportPng();
            else exportSvg();
          });
        });
      }
    }

    // ---- панель свойств выделения ----
    function commonStyle(list, key) {
      var v;
      for (var i = 0; i < list.length; i++) {
        var cur = (list[i].style || {})[key];
        if (cur == null) cur = ((DEFAULTS[list[i].type] || {}).style || {})[key];
        if (i === 0) v = cur;
        else if (v !== cur) return undefined;
      }
      return v;
    }

    /** Какие действия заметки доступны для выделения: ИИ и «Обсудить» — только с текстом, ссылка — на один блок. */
    function blockActsFor(list) {
      var acts = o.blockActions;
      if (!acts || readOnly) return [];
      var hasText = list.some(function (e) {
        return mode === "design" ? !!designLabel(e) : elText(e).trim();
      });
      return NOTE_ACTS.filter(function (a) {
        if (!acts[a.id]) return false;
        if (a.id === "ai" || a.id === "discuss") return hasText;
        if (a.id === "link") return list.length === 1;
        return true;
      });
    }

    // ---- Макет и Лео: что выделено — для чипа «Обсудить», задачи и ссылки; тексты слоёв — для «ИИ» ----
    var LAYER_RU = { frame: "Фрейм", text: "Текст", rect: "Прямоугольник", ellipse: "Эллипс", image: "Картинка", vector: "Иконка",
      line: "Линия", pen: "Рисунок" };

    function inSubtree(el, top) {
      for (var q = el, g = 0; q && g < 40; q = frameOf(q), g++) if (q.id === top.id) return true;
      return false;
    }

    /** Текстовые слои внутри (или сам слой) — сверху вниз, слева направо. */
    function textLayersOf(el) {
      var out = [];
      if (el.type !== "frame") return String(el.text || "").trim() ? [el] : [];
      els.forEach(function (x) {
        if (x.type !== "frame" && !x.hidden && String(x.text || "").trim() && inSubtree(x, el)) out.push(x);
      });
      return out.sort(function (a, b) {
        return a.y - b.y || a.x - b.x;
      });
    }

    /** Подпись слоя для чипа: «Фрейм «Вход»: Войти · E-mail · …» (без id — их сервер получает отдельно). */
    function designLabel(el) {
      var kind = LAYER_RU[el.type] || SHAPE_RU[el.type] || "Слой";
      var own = String(el.name || "").trim();
      if (el.type === "frame") {
        var texts = textLayersOf(el).slice(0, 6).map(function (x) {
          return oneLine(String(x.text)).slice(0, 40);
        });
        return kind + " «" + (own || "Фрейм") + "»" + (texts.length ? ": " + texts.join(" · ") : "");
      }
      var t = oneLine(String(el.text || "")).slice(0, 120);
      if (t) return kind + " «" + t + "»" + (own && own !== t ? " (" + own + ")" : "");
      return own || el.type === "image" || el.type === "vector" ? kind + (own ? " «" + own + "»" : "") : "";
    }

    /** Выделенные элементы для действий заметки: id, вид и текст (сверху вниз, слева направо). */
    function selectionInfo() {
      if (mode === "design") {
        return selList().slice().sort(function (a, b) {
          return a.y - b.y || a.x - b.x;
        }).map(function (el) {
          return { id: el.id, type: el.type, text: designLabel(el), label: el.name || "", layer: true };
        }).filter(function (it) {
          return it.text;
        });
      }
      return selList().filter(function (el) {
        return el.type !== "pen";
      }).sort(function (a, b) {
        return a.y - b.y || a.x - b.x;
      }).map(function (el) {
        var label = el.type === "line" ? labelOfLine(el) : "";
        return { id: el.id, type: el.type, text: elText(el).trim(), label: label };
      });
    }

    function labelOfLine(ln) {
      var a = ln.start && getEl(ln.start.el);
      var b = ln.end && getEl(ln.end.el);
      if (!a || !b) return "";
      return oneLine(elText(a).split("\n")[0] || "…") + " → " + oneLine(elText(b).split("\n")[0] || "…");
    }

    /** По ссылке: показать элемент — в центр экрана, выделить и мигнуть. */
    function revealElement(id) {
      var el = getEl(id);
      if (!el) return false;
      autoFit = false;
      var r = stage.getBoundingClientRect();
      var b = G.elBounds(el, getEl);
      if (b && r.width && r.height) {
        var pad = 80;
        var z = clamp(Math.min((r.width - pad * 2) / Math.max(1, b.w), (r.height - pad * 2) / Math.max(1, b.h), Math.max(view.z, 0.8)), MIN_Z, 1.5);
        view.z = z;
        view.x = r.width / 2 - (b.x + b.w / 2) * z;
        view.y = r.height / 2 - (b.y + b.h / 2) * z;
        applyView();
      }
      setSelection([id]);
      flushNow();
      var node = nodes.get(id);
      if (node) {
        node.classList.remove("is-flash");
        void node.offsetWidth;
        node.classList.add("is-flash");
        setTimeout(function () {
          node.classList.remove("is-flash");
        }, 1600);
      }
      return true;
    }

    /** Новые тексты блоков (ИИ) — одним шагом отмены. */
    function setTexts(map) {
      if (readOnly) return 0;
      var n = 0;
      begin();
      Object.keys(map || {}).forEach(function (id) {
        var el = getEl(id);
        var t = String(map[id] == null ? "" : map[id]);
        if (!el || !TEXTUAL[el.type] || (el.text || "") === t) return;
        var f = { text: t };
        if (el.type !== "line" && el.type !== "text") {
          // Блок растёт под длинный текст — как при ручном вводе.
          var node = nodes.get(id);
          var tEl = node && node.querySelector(":scope > .lb-text");
          if (tEl) {
            var old = tEl.textContent;
            tEl.textContent = t;
            var need = growToText(el, tEl);
            tEl.textContent = old;
            tEl.removeAttribute("data-k");
            if (need) f.h = need;
          }
        }
        patch(id, f);
        n++;
      });
      commit();
      return n;
    }

    function renderProps() {
      var list = selList();
      renderInspector(list);
      renderLayers();
      // В макете свойства — в панели справа; плавающая полоска — только у таблицы, Ганта, карточки.
      if (mode === "design" && !list.some(function (e) {
        return BLOCKS[e.type];
      })) {
        var dActs = !readOnly && list.length && !(gesture && gesture.kind !== "none") && !editing ? blockActsFor(list) : [];
        if (!dActs.length) {
          propsBar.classList.add("hidden");
          return;
        }
        var dKey = "design|" + list.map(function (e) {
          return e.id;
        }).join(",");
        if (propsBar.getAttribute("data-key") !== dKey) {
          propsBar.setAttribute("data-key", dKey);
          propsBar.innerHTML = '<button type="button" class="lb-btn lb-pbtn lb-nact-trigger" data-p="noteacts" title="ИИ, обсудить с Лео, задача, ссылка">' +
            NOTE_ACTS[0].svg + "</button>";
        }
        propsBar.classList.remove("hidden");
        positionProps();
        return;
      }
      // Правка таблицы — у неё своё меню (как в заметке), панель доски ему мешает.
      if (readOnly || !list.length || (gesture && gesture.kind !== "none") || (editing && editing.kind === "table")) {
        propsBar.classList.add("hidden");
        return;
      }
      var types = {};
      list.forEach(function (el) {
        types[el.type] = 1;
      });
      var anyShape = list.some(function (e) {
        return !!SHAPES[e.type];
      });
      var anyStroke = anyShape || types.line || types.pen;
      var anyText = types.text || anyShape || types.sticky || types.line;
      var h = [];
      // Действия заметки с выделенными блоками: ИИ, обсудить с Лео, задача, ссылка (как у выделенного текста).
      var acts = o.blockActions;
      // Голые стрелки и штрихи без подписи — не блоки: им эти действия не нужны.
      if (acts && list.some(function (e) {
        return (e.type !== "line" && e.type !== "pen") || String(e.text || "").trim();
      })) {
        // Одна кнопка (✨ как «ИИ» в заметке) — по нажатию меню: ИИ · Обсудить · Задача · Ссылка.
        if (blockActsFor(list).length) {
          h.push('<button type="button" class="lb-btn lb-pbtn lb-nact-trigger" data-p="noteacts" title="ИИ, обсудить, задача, ссылка">' + NOTE_ACTS[0].svg + "</button>");
          h.push('<span class="lb-sep"></span>');
        }
      }
      var key = JSON.stringify([Object.keys(types).sort(), list.map(function (e) {
        return [e.style, e.route, e.arrowStart, e.arrowEnd, e.group, e.type, !!(e.bend || (e.waypoints && e.waypoints.length)), !!String(e.text || "").trim(), acts ? e.id : "",
          !!e.photo, e.unit, gSel && gSel.id === e.id ? gSel.task : ""];
      }), list.length]);
      if (list.length === 1 && list[0].type === "card") {
        var cd = list[0];
        h.push(swatchBtn("cardFill", fillVar((cd.style && cd.style.fill) || "purple"), "Цвет шапки"));
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="photo" title="' + (cd.photo ? "Заменить фото" : "Добавить фото") + '">' + icon("photo") + "</button>");
        if (cd.photo) h.push('<button type="button" class="lb-btn lb-pbtn" data-p="nophoto" title="Убрать фото">' + icon("noPhoto") + "</button>");
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="child" title="Добавить подчинённого">' + icon("child") + "</button>");
      }
      if (list.length === 1 && list[0].type === "gantt") {
        var gt = list[0];
        var task = gSel && gSel.id === gt.id ? (gt.tasks || []).filter(function (t) {
          return t.id === gSel.task;
        })[0] : null;
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="gadd" title="Добавить задачу">' + icon("taskAdd") + "</button>");
        if (task) {
          h.push(swatchBtn("gcolor", inkVar(task.color || "blue"), "Цвет задачи"));
          h.push('<button type="button" class="lb-btn lb-pbtn" data-p="gdel" title="Удалить задачу">' + icon("taskDel") + "</button>");
        }
        h.push('<span class="lb-sep"></span>');
        h.push('<button type="button" class="lb-btn lb-pbtn lb-txt' + (gt.unit !== "week" ? " is-on" : "") + '" data-p="gunit" data-v="day" title="Шкала по дням">Дни</button>');
        h.push('<button type="button" class="lb-btn lb-pbtn lb-txt' + (gt.unit === "week" ? " is-on" : "") + '" data-p="gunit" data-v="week" title="Шкала по неделям">Недели</button>');
      }
      if (types.sticky) h.push(swatchBtn("sticky", stickyVar(commonStyle(list.filter(isType("sticky")), "fill") || "yellow"), "Цвет стикера"));
      if (anyStroke) {
        var stroked = list.filter(isStroked);
        var noStroke = stroked.every(function (e) {
          return e.type !== "line" && e.type !== "pen" && e.style && (e.style.strokeOff || e.style.strokeWidth === 0);
        });
        h.push(swatchBtn("stroke", noStroke ? "none" : inkVar(commonStyle(stroked, "stroke") || "ink"), "Цвет линии", true));
      }
      if (list.length === 1 && list[0].type === "frame") {
        var fr = list[0];
        var dv = DEVICES.filter(function (d) {
          return d.id === fr.device && d.w === Math.round(fr.w) && d.h === Math.round(fr.h);
        })[0];
        h.push('<button type="button" class="lb-btn lb-pbtn lb-txt" data-p="device" title="Размер фрейма">' + icon("modeDesign") + "&nbsp;" + esc(dv ? dv.title : Math.round(fr.w) + " × " + Math.round(fr.h)) + "</button>");
      }
      if (anyShape || types.vector || types.frame) h.push(swatchBtn("fill", fillVar(commonStyle(list.filter(isPaintable), "fill") || "none"), "Заливка"));
      if (types.text) h.push(swatchBtn("textColor", inkVar(commonStyle(list.filter(isType("text")), "textColor") || "ink"), "Цвет текста", false, true));
      if (anyStroke) h.push('<button type="button" class="lb-btn lb-pbtn" data-p="width" title="Толщина и пунктир">' + widthIcon(commonStyle(list.filter(isStroked), "strokeWidth") || 2, commonStyle(list.filter(isStroked), "dash")) + "</button>");
      if (anyText) {
        var fs = commonStyle(list.filter(isTextual), "fontSize");
        var lbl = (FONT_SIZES.find(function (f) {
          return f.v === fs;
        }) || { t: fs ? String(fs) : "Aa" }).t;
        h.push('<button type="button" class="lb-btn lb-pbtn lb-font" data-p="font" title="Размер текста">' + esc(lbl) + "</button>");
      }
      if (list.every(isAlignable)) {
        var al = alignOf(list[0]);
        if (!list.every(function (e) {
          return alignOf(e) === al;
        })) al = "left";
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="align" title="Выравнивание">' + icon(ALIGN_ICON[al]) + "</button>");
      }
      if (anyShape && !types.line && !types.pen && !types.text && !types.sticky && !types.image) {
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="shape" title="Фигура">' + icon(list[0].type) + "</button>");
      }
      if (types.line) {
        var lines = list.filter(isType("line"));
        var as = lines.every(function (l) {
          return l.arrowStart;
        });
        var ae = lines.every(function (l) {
          return l.arrowEnd;
        });
        var route = lines.every(function (l) {
          return (l.route || "straight") === (lines[0].route || "straight");
        }) ? lines[0].route || "straight" : "straight";
        h.push('<span class="lb-sep"></span>');
        h.push('<button type="button" class="lb-btn lb-pbtn' + (as ? " is-on" : "") + '" data-p="arrowStart" title="Наконечник в начале">' + icon("arrowStart") + "</button>");
        h.push('<button type="button" class="lb-btn lb-pbtn' + (ae ? " is-on" : "") + '" data-p="arrowEnd" title="Наконечник в конце">' + icon("arrowEnd") + "</button>");
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="route" title="Вид линии">' + icon(route) + "</button>");
        if (lines.some(function (l) {
          return l.bend || (l.waypoints && l.waypoints.length);
        })) {
          h.push('<button type="button" class="lb-btn lb-pbtn" data-p="unbend" title="Сбросить изломы">' + icon("unbend") + "</button>");
        }
      }
      h.push('<span class="lb-sep"></span>');
      if (list.length > 1) {
        var grouped = list.every(function (e) {
          return e.group && e.group === list[0].group;
        });
        h.push('<button type="button" class="lb-btn lb-pbtn" data-p="' + (grouped ? "ungroup" : "group") + '" title="' + (grouped ? "Разгруппировать (⇧⌘G)" : "Сгруппировать (⌘G)") + '">' + icon(grouped ? "ungroup" : "group") + "</button>");
      }
      h.push('<button type="button" class="lb-btn lb-pbtn" data-p="more" title="Ещё">' + icon("more") + "</button>");
      h.push('<button type="button" class="lb-btn lb-pbtn lb-danger" data-p="delete" title="Удалить (Delete)">' + icon("trash") + "</button>");
      if (propsBar.getAttribute("data-key") !== key) {
        propsBar.setAttribute("data-key", key);
        propsBar.innerHTML = h.join("");
      }
      propsBar.classList.remove("hidden");
      positionProps();
    }

    // ---- панель свойств макета (справа, как во Figma) ----
    var FILLABLE = Object.assign({ frame: 1, vector: 1, sticky: 1, text: 1 }, SHAPES);
    var STROKABLE = Object.assign({ line: 1, vector: 1, pen: 1, frame: 1 }, SHAPES);
    var TEXTY = Object.assign({ text: 1, sticky: 1 }, SHAPES);
    var RADIUS = { rect: 1, frame: 1 };
    var ALIGNABLE_STROKE = { rect: 1, ellipse: 1, frame: 1 };

    /** Цвет элемента как #rrggbb (ключ палитры — в цвете текущей темы). */
    function hexOf(key, kind) {
      if (!key || key === "none") return "";
      if (isHex(key)) return key.toLowerCase();
      var v = getComputedStyle(root).getPropertyValue((kind === "fill" ? "--lb-fill-" : "--lb-ink-") + key).trim();
      if (/^#[0-9a-f]{6}$/i.test(v)) return v.toLowerCase();
      var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(v);
      return m ? "#" + [m[1], m[2], m[3]].map(function (x) {
        return ("0" + Number(x).toString(16)).slice(-2);
      }).join("") : "";
    }

    function frameOf(el) {
      var f = el && el.frame ? els.get(el.frame) : null;
      return f && f.type === "frame" ? f : null;
    }

    function common(list, fn) {
      var v;
      for (var i = 0; i < list.length; i++) {
        var c = fn(list[i]);
        if (i === 0) v = c;
        else if (v !== c) return undefined;
      }
      return v;
    }

    function r1(v) {
      return Math.round(Number(v) * 10) / 10;
    }

    function numField(k, label, val, opts) {
      opts = opts || {};
      return '<label class="lb-if' + (opts.wide ? " is-wide" : "") + '" title="' + esc(opts.title || label) + '"><span>' + (opts.html ? label : esc(label)) + "</span>" +
        '<input type="text" inputmode="decimal" data-k="' + k + '" value="' + (val === undefined ? "" : esc(String(val))) + '"' +
        (val === undefined ? ' placeholder="' + esc(opts.ph || "—") + '"' : "") + ">" + (opts.suffix ? '<i class="lb-isuf">' + opts.suffix + "</i>" : "") + "</label>";
    }

    /** Строка цвета как во Figma: образец и hex, непрозрачность, «глаз», «—» (у линии убрать обводку нельзя). */
    function paintRow(c, label, key, kind, opa, off, keep, grad) {
      var pct = Math.round((opa == null ? 1 : Number(opa)) * 100);
      var g = parseGrad(grad);
      var field = g
        ? '<div class="lb-icolor" data-c="' + c + '"><button type="button" class="lb-icolor-sw" data-pick="' + c + '" title="' + esc(label) + '" style="--c:transparent;background:' + esc(gradCss(g)) + '"></button>' +
          '<button type="button" class="lb-igrad" data-pick="' + c + '">' + (g.type === "radial" ? "Радиальный" : "Линейный") + '</button><input type="hidden" data-hex="' + c + '" value="' + g.stops[0].c + '"></div>'
        : colorField(c, label, key, kind);
      return '<div class="lb-ipaint' + (off ? " is-off" : "") + '">' + field.replace(/<\/div>$/,
        '<input type="text" inputmode="decimal" class="lb-icolor-op" data-k="' + c + 'Op" value="' + pct + '"' + (pct === "" ? ' placeholder="—"' : "") +
        ' title="Непрозрачность, %"><i class="lb-isuf">%</i></div>') +
        '<button type="button" class="lb-ibtn" data-act="' + c + 'Eye" title="' + (off ? "Показать" : "Скрыть") + '">' + icon(off ? "eyeOff" : "eye") + "</button>" +
        (keep ? "" : '<button type="button" class="lb-ibtn" data-act="' + c + 'Del" title="Убрать">' + icon("minus") + "</button>") + "</div>";
    }

    function colorField(c, label, key, kind) {
      var hex = key === undefined ? "" : hexOf(key, kind);
      var none = key !== undefined && (!key || key === "none");
      return '<div class="lb-icolor" data-c="' + c + '">' +
        '<button type="button" class="lb-icolor-sw' + (none ? " is-none" : "") + '" data-pick="' + c + '" title="' + esc(label) + '" style="--c:' + (hex || "transparent") + '"></button>' +
        '<input type="text" class="lb-icolor-hex" data-hex="' + c + '" maxlength="7" spellcheck="false" value="' + (none ? "" : esc(hex)) + '" placeholder="' + (key === undefined ? "—" : "нет") + '">' +
        "</div>";
    }

    var inspKey = "";
    var padSplit = false;
    var expFmt = "png";
    var expScale = 2;
    var inspDown = null; // {pending} — кнопка мыши нажата на панели свойств
    var inspUp = null;
    // ---- Для разработчика: вкладка «Код», ссылка для ИИ-агентов (MCP), PNG-превью фреймов ----
    // Код генерирует сервер (assistant/lib/design_spec.py) по текущему состоянию фрейма — один генератор
    // для инспектора, HTTP API и MCP. Превью фреймов клиент шлёт, только когда у листа есть ссылка разработчика.
    var dev = { tab: "design", link: null, linkLoaded: false, previews: false, sigs: {}, code: null, codeKey: "", codeTop: "", busy: false, err: "", timer: 0, pvTimer: 0, pvBusy: false, ver: 0 };
    try {
      if (localStorage.getItem("leo-board-insp-tab") === "code") dev.tab = "code";
    } catch (_) {}

    function devOn() {
      return mode === "design" && !readOnly && typeof o.design === "function";
    }

    function devRefresh() {
      dev.ver++;
      inspKey = "";
      renderInspector(selList());
    }

    function topFrameOf(el) {
      var f = el && el.type === "frame" ? el : el ? frameOf(el) : null;
      for (var g = 0; f && frameOf(f) && g < 40; g++) f = frameOf(f);
      return f || null;
    }

    function inFrame(el, top) {
      for (var q = el, g = 0; q && g < 40; q = frameOf(q), g++) if (q.id === top.id) return true;
      return false;
    }

    /** Фрейм с содержимым в формате тела листа; картинки — без данных (для кода хватит типа), если не full. */
    function frameDoc(top, full) {
      var elements = {};
      els.forEach(function (el) {
        if (!inFrame(el, top)) return;
        var s0 = serializeEl(el, getEl, pathOptsOf);
        if (el.type === "image" && !full && s0.src) s0 = Object.assign({}, s0, { src: String(s0.src).slice(0, String(s0.src).indexOf(",") + 1) });
        elements[el.id] = s0;
      });
      return JSON.stringify({ v: 1, mode: "design", elements: elements });
    }

    function devLoadLink() {
      if (!devOn() || dev.linkLoaded) return;
      dev.linkLoaded = true;
      o.design("GET", "/design-link").then(function (r) {
        dev.link = (r && r.link) || null;
        dev.previews = !!(r && r.previews);
        dev.sigs = (r && r.sigs) || {};
        if (dev.tab === "code") devRefresh();
        devSchedulePreviews(1500);
      }).catch(function () {
        dev.linkLoaded = false;
      });
    }

    function devFetchCode(top) {
      var body = frameDoc(top, false);
      var key = top.id + "|" + body;
      if (key === dev.codeKey && (dev.code || dev.busy)) return;
      dev.codeKey = key;
      dev.codeTop = top.id;
      if (dev.timer) clearTimeout(dev.timer);
      dev.timer = setTimeout(function () {
        dev.timer = 0;
        dev.busy = true;
        o.design("POST", "/design", { body: body, frame: top.id, format: "code" }).then(function (r) {
          if (dev.codeKey !== key) return;
          dev.busy = false;
          dev.code = r;
          dev.err = "";
          devRefresh();
        }).catch(function (e) {
          if (dev.codeKey !== key) return;
          dev.busy = false;
          dev.code = null;
          dev.err = (e && e.message) || "Не удалось получить код";
          devRefresh();
        });
      }, dev.code ? 300 : 0);
    }

    function copyText(text, done) {
      var ok = function () {
        notify(done || "Скопировано");
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(ok, function () {
          notify("Не удалось скопировать");
        });
      } else notify("Не удалось скопировать");
    }

    function mcpCommand(url) {
      return "claude mcp add --transport http orbita-design " + url;
    }

    function devCodeHtml(list) {
      var h = [];
      var one = list.length === 1 ? list[0] : null;
      var top = one ? topFrameOf(one) : null;
      if (one && top) {
        devFetchCode(top);
        var nd = dev.code && dev.codeTop === top.id && dev.code.nodes ? dev.code.nodes[one.id] : null;
        var nm = one.type === "frame" ? one.name || "Фрейм" : one.name || ({ text: "Текст", rect: "Прямоугольник", ellipse: "Эллипс", image: "Картинка", vector: "Вектор" }[one.type] || "Слой");
        h.push('<div class="lb-isec"><div class="lb-ilabel"><span>' + esc(nm) + '</span><span class="lb-idim">' + r1(one.w) + " × " + r1(one.h) + "</span></div>" +
          (nd ? '<pre class="lb-icode">' + esc(nd.css) + "</pre>" +
            '<button type="button" class="lb-ibtn is-wide" data-act="devCopyCss">Копировать CSS</button>'
            : '<div class="lb-inote">' + (dev.err ? esc(dev.err) : one.type === "line" || one.type === "pen" ? "Линия — внутри HTML фрейма (SVG)" : "Собираю код…") + "</div>") +
          "</div>");
        h.push('<div class="lb-isec"><div class="lb-ilabel"><span>Фрейм «' + esc(top.name || "Фрейм") + "»</span></div>" +
          '<div class="lb-irow"><button type="button" class="lb-ibtn is-wide" data-act="devCopyCode"' + (dev.code && dev.codeTop === top.id ? "" : " disabled") + ">HTML + CSS</button>" +
          '<button type="button" class="lb-ibtn is-wide" data-act="devPage">Скачать .html</button></div></div>');
      } else {
        h.push('<div class="lb-isec"><div class="lb-inote">' + (list.length > 1 ? "Выделите один слой — покажу его CSS." : one ? "Объект вне фрейма — код собирается по фреймам." : "Выделите фрейм или слой — покажу CSS, а весь фрейм можно забрать в HTML.") + "</div></div>");
      }
      // Ссылка для агентов: Claude Code / Cursor читают макет по MCP.
      devLoadLink();
      var L = dev.link;
      h.push('<div class="lb-isec"><div class="lb-ilabel"><span>ИИ-агентам и разработчикам</span></div>' +
        (L
          ? '<div class="lb-inote">Агент читает этот макет по MCP: фреймы, размеры, цвета, шрифты, HTML/CSS и картинки. Только чтение.</div>' +
            '<pre class="lb-icode is-cmd">' + esc(mcpCommand(L.mcp_url)) + "</pre>" +
            '<div class="lb-irow"><button type="button" class="lb-ibtn is-wide" data-act="devCopyCmd">Команда Claude Code</button>' +
            '<button type="button" class="lb-ibtn is-wide" data-act="devCopyUrl">Адрес MCP</button></div>' +
            '<button type="button" class="lb-ibtn is-wide lb-danger" data-act="devRevoke">Отозвать ссылку</button>'
          : '<div class="lb-inote">Ссылка для Claude Code, Cursor и других агентов: они прочитают макет и сверстают экраны. Только чтение, можно отозвать.</div>' +
            '<button type="button" class="lb-ibtn is-wide" data-act="devCreate"' + (dev.linkLoaded ? "" : " disabled") + ">Создать ссылку для агента</button>") +
        "</div>");
      return h.join("");
    }

    function devAct(a) {
      var one = selList().length === 1 ? selList()[0] : null;
      var top = one ? topFrameOf(one) : null;
      if (a === "devCopyCss") {
        var nd = dev.code && dev.code.nodes && one ? dev.code.nodes[one.id] : null;
        if (nd) copyText(nd.css, "CSS скопирован");
      } else if (a === "devCopyCode" && dev.code) {
        copyText((dev.code.font_link ? dev.code.font_link + "\n\n" : "") + dev.code.html + "\n\n<style>\n" + dev.code.css + "\n</style>\n", "HTML и CSS скопированы");
      } else if (a === "devPage" && top) {
        o.design("POST", "/design", { body: frameDoc(top, true), frame: top.id, format: "code", images: "inline" }).then(function (c) {
          var page = "<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n" +
            "<title>" + esc(c.name || "Фрейм") + "</title>\n" + (c.font_link || "") + "\n<style>\n" + c.css + "\n</style>\n</head>\n<body>\n" + c.html + "\n</body>\n</html>\n";
          downloadBlob(new Blob([page], { type: "text/html" }), (String(top.name || "frame").replace(/[\\/:*?"<>|]+/g, " ").trim() || "frame") + ".html");
        }).catch(function () {
          notify("Не удалось собрать HTML");
        });
      } else if (a === "devCreate") {
        o.design("POST", "/design-link").then(function (r) {
          dev.link = (r && r.link) || null;
          dev.previews = !!dev.link;
          devRefresh();
          if (dev.link) copyText(mcpCommand(dev.link.mcp_url), "Ссылка создана, команда для Claude Code скопирована");
          devSchedulePreviews(200);
        }).catch(function () {
          notify("Не удалось создать ссылку");
        });
      } else if (a === "devCopyCmd" && dev.link) copyText(mcpCommand(dev.link.mcp_url), "Команда скопирована");
      else if (a === "devCopyUrl" && dev.link) copyText(dev.link.mcp_url, "Адрес MCP скопирован");
      else if (a === "devRevoke" && dev.link) {
        if (!window.confirm("Отозвать ссылку? Агенты, подключённые по ней, потеряют доступ к макету.")) return;
        o.design("DELETE", "/design-link").then(function () {
          dev.link = null;
          dev.linkLoaded = false;
          devRefresh();
          notify("Ссылка отозвана");
        }).catch(function () {
          notify("Не удалось отозвать ссылку");
        });
      }
    }

    function hashStr(str) {
      var h1 = 0x811c9dc5;
      for (var i = 0; i < str.length; i++) {
        h1 ^= str.charCodeAt(i);
        h1 = Math.imul(h1, 16777619);
      }
      return (h1 >>> 0).toString(36) + "." + str.length.toString(36);
    }

    /** PNG верхних фреймов, изменившихся с прошлой отправки, — агенту по ссылке разработчика. */
    function devSchedulePreviews(delay) {
      if (!devOn() || !dev.previews) return;
      if (dev.pvTimer) clearTimeout(dev.pvTimer);
      dev.pvTimer = setTimeout(devSendPreviews, delay == null ? 5000 : delay);
    }

    function devSendPreviews() {
      dev.pvTimer = 0;
      if (dev.pvBusy || !devOn() || !dev.previews || destroyed) return;
      var todo = [];
      els.forEach(function (el) {
        if (el.type !== "frame" || frameOf(el) || el.hidden) return;
        var sig = hashStr(frameDoc(el, false) + "|" + Array.from(els.values()).filter(function (x) {
          return x.type === "image" && inFrame(x, el);
        }).map(function (x) {
          return x.id + ":" + String(x.src || "").length;
        }).join(","));
        if (dev.sigs[el.id] !== sig) todo.push({ id: el.id, sig: sig });
      });
      if (!todo.length) return;
      dev.pvBusy = true;
      var next = function () {
        var t = todo.shift();
        if (!t || destroyed) {
          dev.pvBusy = false;
          return;
        }
        var f = getEl(t.id);
        var sv = f ? selectionSvg([t.id]) : null;
        if (!sv) return next();
        var k = Math.max(0.25, Math.min(2, 2400 / Math.max(sv.w, sv.h, 1)));
        embedFonts(sv).then(function (sv2) {
          return svgToCanvas(sv2, k, false);
        }).then(function (c) {
          return o.design("POST", "/design/preview", { frame: t.id, sig: t.sig, png: c.toDataURL("image/png") });
        }).then(function () {
          dev.sigs[t.id] = t.sig;
        }).catch(function () {}).then(next);
      };
      next();
    }

    function renderInspector(list) {
      if (!inspector) return;
      var codeTab = dev.tab === "code" && devOn();
      if (mode !== "design" || (!list.length && !codeTab) || editing) {
        inspector.classList.add("hidden");
        inspKey = "";
        return;
      }
      var one = list.length === 1 ? list[0] : null;
      var key = JSON.stringify(list.map(function (e) {
        return [e.id, e.type, r1(e.x), r1(e.y), r1(e.w), r1(e.h), e.rot, e.style, e.name, e.frame, e.layout, e.noclip, e.group, e.grow, e.ratio, e.pin, padSplit];
      })) + "|" + dev.tab + (codeTab ? "|" + dev.ver : "");
      inspector.classList.remove("hidden");
      // Пока человек вводит число — панель не перерисовываем (иначе курсор слетит).
      var ae = document.activeElement;
      if (key === inspKey || (ae && ae.tagName === "INPUT" && inspector.contains(ae))) return;
      // Кнопка мыши ещё нажата на панели (человек ставит курсор в поле) — перерисуем после отпускания,
      // иначе поле под курсором заменится новым и фокус пропадёт.
      if (inspDown) {
        inspDown.pending = true;
        return;
      }
      inspKey = key;
      // Вкладки как во Figma: «Дизайн» — свойства, «Код» — CSS слоя, HTML фрейма, ссылка для агентов.
      var tabs = devOn() ? '<div class="lb-itabs" role="tablist">' +
        '<button type="button" role="tab" data-tab="design" class="' + (codeTab ? "" : "is-on") + '">Дизайн</button>' +
        '<button type="button" role="tab" data-tab="code" class="' + (codeTab ? "is-on" : "") + '">Код</button></div>' : "";
      if (codeTab) {
        inspector.innerHTML = tabs + devCodeHtml(list);
        return;
      }
      var types = {};
      list.forEach(function (e) {
        types[e.type] = 1;
      });
      var all = function (set) {
        return list.every(function (e) {
          return set[e.type];
        });
      };
      var st0 = function (e, k) {
        var v = (e.style || {})[k];
        return v == null ? ((mode === "design" && DESIGN_STYLE[e.type]) || (DEFAULTS[e.type] || {}).style || {})[k] : v;
      };
      var st = function (k) {
        return common(list, function (e) {
          var v = (e.style || {})[k];
          return v == null ? ((mode === "design" && DESIGN_STYLE[e.type]) || (DEFAULTS[e.type] || {}).style || {})[k] : v;
        });
      };
      var h = [];
      var title = one ? (one.type === "frame" ? "Фрейм" : SHAPE_RU[one.type] || { text: "Текст", line: "Линия",
        image: "Картинка", vector: "Вектор", table: "Таблица", sticky: "Стикер", pen: "Рисунок" }[one.type] || "Элемент") : "Выделено: " + list.length;
      var grp = !one && common(list, function (e) {
        return e.group || "";
      });
      if (grp) title = "Группа";
      if (tabs) h.push(tabs);
      h.push('<div class="lb-ihead">' + esc(title) + "</div>");
      if (!one) {
        // Несколько объектов (или группа) — общая рамка: X/Y двигают всё, W/H растягивают.
        var gb = selBounds();
        var gf = frameOf(list[0]);
        if (gf && !list.every(function (e) {
          return frameOf(e) === gf;
        })) gf = null;
        if (gb) h.push('<div class="lb-isec"><div class="lb-irow">' + numField("x", "X", r1(gb.x - (gf ? gf.x : 0))) + numField("y", "Y", r1(gb.y - (gf ? gf.y : 0))) +
          '</div><div class="lb-irow">' + numField("w", "W", r1(gb.w)) + numField("h", "H", r1(gb.h)) + "</div></div>");
      }
      if (one && one.type === "frame") {
        h.push('<div class="lb-isec"><label class="lb-if is-wide"><span>Название</span><input type="text" data-k="name" value="' + esc(one.name || "") + '"></label>' +
          '<button type="button" class="lb-ibtn is-wide" data-act="device">' + esc((DEVICES.filter(function (d) {
            return d.id === one.device && d.w === Math.round(one.w) && d.h === Math.round(one.h);
          })[0] || { title: "Свой размер" }).title) + "</button></div>");
      }
      if (one && one.type !== "line" && one.type !== "pen") {
        // Позиция (как во Figma): X/Y, поворот и отражения.
        var f = frameOf(one);
        h.push('<div class="lb-isec"><div class="lb-ilabel">Позиция</div><div class="lb-irow">' +
          numField("x", "X", r1(one.x - (f ? f.x : 0)), { title: f ? "X внутри фрейма" : "X" }) + numField("y", "Y", r1(one.y - (f ? f.y : 0)), { title: f ? "Y внутри фрейма" : "Y" }) +
          '</div><div class="lb-irow">' + numField("rot", icon("angle"), r1(one.rot || 0), { title: "Поворот, °", html: true, suffix: "°" }) +
            '<span class="lb-igroup">' +
            '<button type="button" class="lb-ibtn" data-act="rot90" title="Повернуть на 90°">' + icon("rot90") + "</button>" +
            '<button type="button" class="lb-ibtn" data-act="flipH" title="Отразить по горизонтали (⇧H)">' + icon("flipH") + "</button>" +
            '<button type="button" class="lb-ibtn" data-act="flipV" title="Отразить по вертикали (⇧V)">' + icon("flipV") + "</button></span></div></div>");
        // Размеры; у фрейма — автолэйаут вокруг них: поток, «фикс./обнимать», выравнивание и промежуток, отступы.
        var LA = one.type === "frame" ? parseLayout(one.layout) : null;
        // Режим размера: фикс.; «обним.» — у фрейма с автолэйаутом; «заполн.» — у ребёнка автолэйаута.
        var PL = f ? parseLayout(f.layout) : null;
        var sizeField = function (k, label, val) {
          var modes = [["fixed", "Фикс."]];
          if (LA) modes.push(["hug", "Обним."]);
          if (PL) modes.push(["fill", "Заполн."]);
          var curM = LA && LA.hug.indexOf(k) >= 0 ? "hug" : PL && grows(one, k) ? "fill" : "fixed";
          if (modes.length < 2) return numField(k, label, val, { title: k === "h" && one.type === "text" ? "Высота текста — по содержимому" : label });
          return '<div class="lb-if lb-ifsel" title="' + label + '"><span>' + label + '</span><input type="text" inputmode="decimal" data-k="' + k + '" value="' + esc(String(val)) + '">' +
            '<select data-k="size' + label + '">' + modes.map(function (m) {
              return '<option value="' + m[0] + '"' + (curM === m[0] ? " selected" : "") + ">" + m[1] + "</option>";
            }).join("") + "</select></div>";
        };
        h.push('<div class="lb-isec"><div class="lb-ilabel">' + (one.type === "frame" ? "Автолэйаут" : "Размеры") +
          (one.type === "frame" ? '<button type="button" class="lb-ibtn lb-iadd' + (LA ? " is-on" : "") + '" data-act="' + (LA ? "alDel" : "alAdd") + '" title="' +
            (LA ? "Убрать автолэйаут" : "Автолэйаут (⇧A)") + '">' + icon("autoLayout") + "</button>" : "") + "</div>");
        if (one.type === "frame") {
          var dirNow = LA ? LA.dir : "";
          h.push('<div class="lb-isub">Поток</div><div class="lb-irow"><span class="lb-iseg">' +
            '<button type="button" class="lb-ibtn' + (!LA ? " is-on" : "") + '" data-act="alDir" data-ldir="" title="Свободно">' + icon("free") + "</button>" +
            '<button type="button" class="lb-ibtn' + (dirNow === "v" ? " is-on" : "") + '" data-act="alDir" data-ldir="v" title="Вертикально">' + icon("dirV") + "</button>" +
            '<button type="button" class="lb-ibtn' + (dirNow === "h" ? " is-on" : "") + '" data-act="alDir" data-ldir="h" title="Горизонтально">' + icon("dirH") + "</button>" +
            '<button type="button" class="lb-ibtn' + (dirNow === "g" ? " is-on" : "") + '" data-act="alDir" data-ldir="g" title="Сетка">' + icon("gridFlow") + "</button></span>" +
            (dirNow === "h" ? '<button type="button" class="lb-ibtn' + (LA.wrap ? " is-on" : "") + '" data-act="alWrap" title="Перенос на новую строку">' + icon("wrap") + "</button>" : "") +
            (dirNow === "g" ? numField("lcols", icon("cols"), LA.cols, { title: "Колонки", html: true }) : "") + "</div>" +
            '<div class="lb-isub">Размеры</div>');
        }
        h.push('<div class="lb-irow">' + sizeField("w", "W", r1(one.w)) + sizeField("h", "H", r1(one.h)) +
          '<button type="button" class="lb-ibtn' + (one.ratio ? " is-on" : "") + '" data-act="ratio" title="Сохранять пропорции">' + icon(one.ratio ? "lock" : "unlock") + "</button></div>");
        if (LA) {
          var Hd = LA.dir !== "v";
          var cells = "";
          ["start", "center", "end"].forEach(function (r) {
            ["start", "center", "end"].forEach(function (c) {
              // Вертикальный: строки — главная ось, столбцы — поперечная; горизонтальный — наоборот.
              var m = Hd ? c : r;
              var cr = Hd ? r : c;
              var on = LA.align === cr && (LA.justify === m || LA.justify === "between");
              cells += '<button type="button" class="lb-lcell' + (on ? " is-on" : "") + '" data-act="alCell" data-lc="' + cr + '" data-lm="' + m + '" title="Выровнять"><i></i></button>';
            });
          });
          var auto = LA.justify === "between";
          var split = padSplit || !(LA.pad[1] === LA.pad[3] && LA.pad[0] === LA.pad[2]);
          h.push('<div class="lb-ilayout"><div><div class="lb-isub">Выравнивание</div><div class="lb-lgrid">' + cells + "</div></div>" +
            '<div class="lb-lside"><div class="lb-isub">Промежуток</div>' +
              '<div class="lb-if lb-ifsel" title="Промежуток между объектами"><span>' + icon(Hd ? "gapH" : "gapV") + '</span><input type="text" inputmode="decimal" data-k="lgap" value="' +
                (auto ? "" : esc(String(r1(LA.gap)))) + '"' + (auto ? ' placeholder="Авто"' : "") + '><select data-k="lgapm"><option value="fixed"' + (auto ? "" : " selected") +
                '>Фикс.</option><option value="auto"' + (auto ? " selected" : "") + ">Авто</option></select></div></div></div>" +
            '<div class="lb-isub">Отступы</div><div class="lb-irow">' +
              (split
                ? numField("lpl", icon("padL"), LA.pad[3], { title: "Слева", html: true }) + numField("lpt", icon("padT"), LA.pad[0], { title: "Сверху", html: true })
                : numField("lpadh", icon("padH"), LA.pad[3], { title: "Слева и справа", html: true }) + numField("lpadv", icon("padV"), LA.pad[0], { title: "Сверху и снизу", html: true })) +
              '<button type="button" class="lb-ibtn' + (split ? " is-on" : "") + '" data-act="padSplit" title="Каждая сторона отдельно">' + icon("padAll") + "</button></div>" +
              (split ? '<div class="lb-irow">' + numField("lpr", icon("padR"), LA.pad[1], { title: "Справа", html: true }) + numField("lpb", icon("padB"), LA.pad[2], { title: "Снизу", html: true }) +
                '<span class="lb-ispacer"></span></div>' : ""));
        }
        if (one.type === "frame") {
          h.push('<label class="lb-icheck"><input type="checkbox" data-k="clip"' + (one.noclip ? "" : " checked") + "> Обрезать содержимое</label>");
        }
        h.push("</div>");
      }
      // Выравнивание: несколько объектов — между собой (группа как одно целое), один в экране — по экрану.
      if (list.length > 1 || (one && frameOf(one))) {
        var grp = function (items) {
          return '<span class="lb-igroup">' + items.map(function (a) {
            return '<button type="button" class="lb-ibtn" data-align="' + a[0] + '" title="' + a[2] + '">' + icon(a[1]) + "</button>";
          }).join("") + "</span>";
        };
        // Как во Figma: по горизонтали и по вертикали — двумя группами; равные промежутки — отдельно (при 3+).
        h.push('<div class="lb-isec"><div class="lb-ilabel">Выравнивание' + (list.length > 1 ? "" : " по фрейму") + '</div><div class="lb-irow lb-ialign2">' +
          grp([["L", "alL", "По левому краю"], ["CH", "alCH", "По центру по горизонтали"], ["R", "alR", "По правому краю"]]) +
          grp([["T", "alT", "По верхнему краю"], ["CV", "alCV", "По центру по вертикали"], ["B", "alB", "По нижнему краю"]]) + "</div>" +
          (list.length > 2 ? '<div class="lb-irow lb-ialign2">' + grp([["DH", "distH", "Равные промежутки по горизонтали"], ["DV", "distV", "Равные промежутки по вертикали"]]) + "</div>" : "") +
          "</div>");
      }
      // Привязка к краям фрейма (constraints): куда объект едет, когда у фрейма меняется размер.
      var pinFr = one && frameOf(one);
      if (pinFr && !pinFr.layout) {
        var PN = pinOf(one);
        var pinSel = function (k, cur, opts, title) {
          return '<label class="lb-if lb-ifsel lb-ipin" title="' + title + '"><select data-k="' + k + '">' + opts.map(function (o2) {
            return '<option value="' + o2[0] + '"' + (cur === o2[0] ? " selected" : "") + ">" + o2[1] + "</option>";
          }).join("") + "</select></label>";
        };
        h.push('<div class="lb-isec"><div class="lb-ilabel">Привязка к фрейму</div><div class="lb-irow">' +
          pinSel("pinH", PN.h, [["l", "Слева"], ["r", "Справа"], ["lr", "Слева и справа"], ["c", "По центру"], ["s", "Масштаб"]], "По горизонтали") +
          pinSel("pinV", PN.v, [["t", "Сверху"], ["b", "Снизу"], ["tb", "Сверху и снизу"], ["c", "По центру"], ["s", "Масштаб"]], "По вертикали") +
          "</div></div>");
      }
      // Внешний вид (как во Figma): непрозрачность и скругление в одну строку, углы по отдельности — под ними.
      var op = st("opacity");
      var hasR = one && RADIUS[one.type];
      var rr = hasR ? radiiOf(one.style || {}, one.w, one.h, one.type === "frame" || mode === "design" ? 0 : undefined).map(r1) : null;
      var perCorner = hasR && one.style && one.style.radii;
      h.push('<div class="lb-isec"><div class="lb-ilabel">Внешний вид</div><div class="lb-irow">' +
        numField("opacity", icon("opacity"), op == null ? 100 : op === undefined ? undefined : Math.round(op * 100), { title: "Непрозрачность, %", html: true, suffix: "%" }) +
        (hasR ? numField("radius", icon("radius"), rr[0] === rr[1] && rr[1] === rr[2] && rr[2] === rr[3] ? rr[0] : undefined, { title: "Скругление углов", html: true, ph: "Разные" }) +
          '<button type="button" class="lb-ibtn' + (perCorner ? " is-on" : "") + '" data-act="radii" title="Каждый угол отдельно">' + icon("corners") + "</button>" : "") +
        "</div>" +
        (perCorner ? '<div class="lb-irow">' + numField("r0", icon("cTL"), rr[0], { title: "Левый верхний", html: true }) + numField("r1", icon("cTR"), rr[1], { title: "Правый верхний", html: true }) +
          '<span class="lb-ispacer"></span></div><div class="lb-irow">' + numField("r3", icon("cBL"), rr[3], { title: "Левый нижний", html: true }) +
          numField("r2", icon("cBR"), rr[2], { title: "Правый нижний", html: true }) + '<span class="lb-ispacer"></span></div>' : "") + "</div>");
      if (all(FILLABLE)) {
        var fillKey = st("fill");
        // Разные заливки у нескольких — строка есть; у одного «нет заливки» — только «+».
        var hasFill = list.some(function (e) {
          var v = st0(e, "fill");
          return v && v !== "none";
        });
        h.push('<div class="lb-isec"><div class="lb-ilabel">' + (types.text && Object.keys(types).length === 1 ? "Фон" : "Заливка") +
          (hasFill ? "" : '<button type="button" class="lb-ibtn lb-iadd" data-act="fillAdd" title="Добавить заливку">' + icon("plus") + "</button>") + "</div>" +
          (hasFill ? paintRow("fill", "Заливка", fillKey, "fill", st("fillOpacity"), st("fillOff"), false, st("fillGrad")) : "") + "</div>");
      }
      if (all(STROKABLE)) {
        var sw = st("strokeWidth");
        var lineish = types.line || types.pen;
        var hasStroke = lineish || list.some(function (e) {
          return Number(st0(e, "strokeWidth")) > 0 && st0(e, "stroke") !== "none";
        });
        h.push('<div class="lb-isec"><div class="lb-ilabel">Обводка' +
          (hasStroke ? "" : '<button type="button" class="lb-ibtn lb-iadd" data-act="strokeAdd" title="Добавить обводку">' + icon("plus") + "</button>") + "</div>" +
          (hasStroke ? paintRow("stroke", "Обводка", st("stroke"), "ink", st("strokeOpacity"), st("strokeOff"), lineish, lineish ? null : st("strokeGrad")) +
            '<div class="lb-irow">' + (all(ALIGNABLE_STROKE) ? '<label class="lb-iselect" title="Положение обводки"><select data-k="strokeAlign">' +
              [["inside", "Внутри"], ["center", "По центру"], ["outside", "Снаружи"]].map(function (o) {
                return '<option value="' + o[0] + '"' + ((st("strokeAlign") || "center") === o[0] ? " selected" : "") + ">" + o[1] + "</option>";
              }).join("") + "</select></label>" : "") +
            numField("strokeWidth", icon("weight"), sw, { title: "Толщина", html: true }) + "</div>" : "") + "</div>");
      }
      if (all(TEXTY)) {
        var al = common(list, alignOf);
        // Типографика как во Figma: шрифт, начертание и размер, интервалы, выравнивание, оформление.
        var ff = st("fontFamily");
        var fw = st("fontWeight") || (st("bold") ? 600 : 400);
        var va = st("valign") || (types.text && Object.keys(types).length === 1 ? "top" : "middle");
        var deco = st("deco");
        var tc = st("textCase");
        var fams = FONTS.map(function (x) {
          return x.f;
        });
        if (ff && fams.indexOf(ff) < 0) fams.unshift(ff);
        var segBtn = function (attr, val, cur, title, inner) {
          return '<button type="button" class="lb-ibtn' + (cur === val ? " is-on" : "") + '" data-' + attr + '="' + val + '" title="' + title + '">' + inner + "</button>";
        };
        h.push('<div class="lb-isec"><div class="lb-ilabel">Текст</div>' +
          '<label class="lb-iselect is-wide" title="Шрифт"><select data-k="fontFamily"><option value=""' + (ff ? "" : " selected") + ">Системный</option>" +
            fams.map(function (f) {
              return '<option value="' + esc(f) + '"' + (ff === f ? " selected" : "") + ' style="font-family:' + esc(fontStack(f)) + '">' + esc(f) + "</option>";
            }).join("") + "</select></label>" +
          '<div class="lb-irow"><label class="lb-iselect" title="Начертание"><select data-k="fontWeight">' + fontWeights(ff).map(function (w) {
            var name = WEIGHTS.filter(function (x) {
              return x[0] === w;
            })[0];
            return '<option value="' + w + '"' + (Number(fw) === w ? " selected" : "") + ">" + (name ? name[1] : w) + "</option>";
          }).join("") + "</select></label>" + numField("fontSize", icon("text"), st("fontSize"), { title: "Размер", html: true }) + "</div>" +
          '<div class="lb-irow">' + numField("lineHeight", icon("lineH"), Number(st("lineHeight")) > 0 ? st("lineHeight") : undefined, { title: "Межстрочный интервал, px (пусто — авто)", html: true, ph: "Авто" }) +
            numField("letterSpacing", icon("letterS"), st("letterSpacing") || 0, { title: "Межбуквенный интервал, %", html: true, suffix: "%" }) + "</div>" +
          '<div class="lb-irow"><span class="lb-iseg">' + ["left", "center", "right"].map(function (a) {
            return segBtn("al", a, al, { left: "Влево", center: "По центру", right: "Вправо" }[a], icon(ALIGN_ICON[a]));
          }).join("") + '</span><span class="lb-iseg">' + [["top", "vaT", "По верхнему краю"], ["middle", "vaM", "По центру"], ["bottom", "vaB", "По нижнему краю"]].map(function (v) {
            return segBtn("va", v[0], va, v[2], icon(v[1]));
          }).join("") + "</span></div>" +
          '<div class="lb-irow"><span class="lb-iseg">' + segBtn("deco", "underline", deco, "Подчёркнутый", "<u>U</u>") + segBtn("deco", "strike", deco, "Зачёркнутый", "<s>S</s>") +
            '</span><span class="lb-iseg">' + segBtn("tcase", "title", tc, "Каждое Слово", "Aa") + segBtn("tcase", "upper", tc, "ВСЕ ПРОПИСНЫЕ", "AA") + segBtn("tcase", "lower", tc, "строчные", "aa") + "</span></div>" +
          colorField("textColor", "Цвет текста", st("textColor"), "ink") + "</div>");
      }
      if (!types.line && !types.pen) {
        // Эффекты (как во Figma): тень, размытие слоя, размытие фона.
        var sh = SHADOW_RE.exec(String(st("shadow") || ""));
        var bl = Number(st("blur")) > 0 ? Number(st("blur")) : 0;
        var bb = Number(st("bgBlur")) > 0 ? Number(st("bgBlur")) : 0;
        var rm = function (act) {
          return '<button type="button" class="lb-ibtn lb-ifx-rm" data-act="' + act + '" title="Убрать">' + icon("minus") + "</button>";
        };
        h.push('<div class="lb-isec"><div class="lb-ilabel">Эффекты' +
          (sh && bl && bb ? "" : '<button type="button" class="lb-ibtn lb-iadd" data-act="fxAdd" title="Добавить эффект">' + icon("plus") + "</button>") + "</div>" +
          (sh ? '<div class="lb-ifx"><span>Тень</span>' + rm("shadow") + "</div>" +
            '<div class="lb-irow">' + numField("shx", "X", Number(sh[1])) + numField("shy", "Y", Number(sh[2])) + numField("shb", "Blur", Number(sh[3])) + "</div>" +
            '<div class="lb-ipaint">' + colorField("shc", "Цвет тени", sh[4].slice(0, 7), "ink").replace(/<\/div>$/,
              '<input type="text" inputmode="decimal" class="lb-icolor-op" data-k="sha" value="' + Math.round(parseInt(sh[4].slice(7), 16) / 2.55) + '" title="Непрозрачность тени, %"><i class="lb-isuf">%</i></div>') + "</div>" : "") +
          (bl ? '<div class="lb-ifx"><span>Размытие слоя</span>' + numField("blur", icon("blurFx"), bl, { title: "Радиус размытия", html: true }) + rm("blurOff") + "</div>" : "") +
          (bb ? '<div class="lb-ifx"><span>Размытие фона</span>' + numField("bgBlur", icon("blurFx"), bb, { title: "Радиус размытия фона", html: true }) + rm("bgBlurOff") + "</div>" : "") +
          "</div>");
      }
      // Экспорт выделенного (как во Figma): формат и масштаб, скачать.
      h.push('<div class="lb-isec"><div class="lb-ilabel">Экспорт</div><div class="lb-irow">' +
        '<label class="lb-iselect" title="Масштаб"><select data-exp="scale">' + [1, 2, 3].map(function (k) {
          return '<option value="' + k + '"' + (expScale === k ? " selected" : "") + ">" + k + "x</option>";
        }).join("") + "</select></label>" +
        '<label class="lb-iselect" title="Формат"><select data-exp="fmt">' + [["png", "PNG"], ["jpg", "JPG"], ["svg", "SVG"], ["pdf", "PDF"]].map(function (f) {
          return '<option value="' + f[0] + '"' + (expFmt === f[0] ? " selected" : "") + ">" + f[1] + "</option>";
        }).join("") + "</select></label></div>" +
        '<button type="button" class="lb-ibtn is-wide lb-iexport" data-act="export">Скачать ' + (one && one.type === "frame" ? "фрейм" : list.length > 1 ? "выделенное" : "объект") + "</button>" +
        '<button type="button" class="lb-ibtn is-wide lb-iexport" data-act="copyFigma" title="Вставляется во Figma векторами и текстом (⇧⌘C)">Копировать для Figma</button></div>');
      var anyGroup = list.some(function (e) {
        return e.group;
      });
      h.push('<div class="lb-isec lb-iacts">' +
        (list.length > 1 && !grp ? '<button type="button" class="lb-ibtn" data-act="group" title="Сгруппировать (⌘G)">' + icon("group") + "</button>" : "") +
        (anyGroup ? '<button type="button" class="lb-ibtn" data-act="ungroup" title="Разгруппировать (⇧⌘G)">' + icon("ungroup") + "</button>" : "") +
        (one && o.blockActions && o.blockActions.link ? '<button type="button" class="lb-ibtn" data-act="link" title="Скопировать ссылку на ' + (one.type === "frame" ? "фрейм" : "объект") + '">' + icon("link") + "</button>" : "") +
        '<button type="button" class="lb-ibtn" data-act="dup" title="Дублировать (⌘D)">' + icon("copy") + "</button>" +
        '<button type="button" class="lb-ibtn" data-act="front" title="На передний план (])">' + icon("front") + "</button>" +
        '<button type="button" class="lb-ibtn" data-act="back" title="На задний план ([)">' + icon("back") + "</button>" +
        '<button type="button" class="lb-ibtn lb-danger" data-act="delete" title="Удалить (Delete)">' + icon("trash") + "</button></div>");
      inspector.innerHTML = h.join("");
    }

    function parseNum(v) {
      var n = Number(String(v).replace(",", ".").replace(/[^\d.\-]/g, ""));
      return isFinite(n) && String(v).trim() !== "" ? n : null;
    }

    function shadowOf(el) {
      return SHADOW_RE.exec(String((el.style || {}).shadow || "")) || [null, "0", "4", "12", "#00000040"];
    }

    function hexA(n) {
      return ("0" + Math.max(0, Math.min(255, Math.round(n))).toString(16)).slice(-2);
    }

    /** Значение из панели — во все выделенные элементы одним шагом отмены. */
    function applyInspector(k, raw) {
      var list = selList();
      if (!list.length) return;
      var n = parseNum(raw);
      if (list.length > 1 && (k === "x" || k === "y" || k === "w" || k === "h")) {
        if (n == null) return;
        var gb = selBounds();
        if (!gb) return;
        var gf = frameOf(list[0]);
        if (gf && !list.every(function (e) {
          return frameOf(e) === gf;
        })) gf = null;
        begin();
        if (k === "x" || k === "y") {
          var dd = n + (gf ? gf[k] : 0) - gb[k];
          if (dd) moveBy(withFrameKids(list.map(function (e) {
            return e.id;
          })), k === "x" ? dd : 0, k === "y" ? dd : 0);
        } else if (n >= 1) scaleSel(gb, k === "w" ? n / Math.max(0.01, gb.w) : 1, k === "h" ? n / Math.max(0.01, gb.h) : 1);
        syncFrames();
        commit();
        return;
      }
      begin();
      list.forEach(function (el) {
        var cur = getEl(el.id);
        if (!cur) return;
        var f = frameOf(cur);
        if (k === "x" || k === "y") {
          if (n == null || list.length > 1) return;
          var d = n + (f ? f[k] : 0) - cur[k];
          if (d) moveBy(withFrameKids([cur.id]), k === "x" ? d : 0, k === "y" ? d : 0);
        } else if (k === "w" || k === "h") {
          if (n == null || n < 1) return;
          var fw = {};
          fw[k] = n;
          if (cur.type === "frame" && cur.device) fw.device = undefined;
          var LH = cur.type === "frame" ? parseLayout(cur.layout) : null;
          if (LH && LH.hug.indexOf(k) >= 0) {
            LH.hug = LH.hug.replace(k, "") || "none";
            fw.layout = layoutStr(LH);
          }
          // Ввели размер вручную — «заполнить» по этой оси снимается; с замком — вторая сторона пропорционально.
          if (grows(cur, k)) fw.grow = String(cur.grow).replace(k, "") || undefined;
          if (cur.ratio && cur[k] > 0) {
            var ok2 = k === "w" ? "h" : "w";
            fw[ok2] = r1(cur[ok2] * (n / cur[k]));
          }
          // Прижат к нижнему (правому) краю экрана и не к верхнему (левому) — растёт вверх (влево), как во Figma.
          var pk = k === "w" ? "x" : "y";
          if (f && !cur.rot) {
            var near = function (u, v) {
              return Math.abs(u - v) < 0.5;
            };
            var pn = pinOf(cur)[k === "w" ? "h" : "v"];
            if (pn === "r" || pn === "b" || (near(cur[pk] + cur[k], f[pk] + f[k]) && !near(cur[pk], f[pk]))) fw[pk] = cur[pk] + cur[k] - n;
            else if (pn === "c") fw[pk] = cur[pk] + (cur[k] - n) / 2;
          }
          resizeWithPins(cur.id, fw);
        } else if (k === "rot") {
          if (n == null) return;
          var nr = ((n % 360) + 360) % 360;
          if (nr > 180) nr -= 360;
          if (cur.type === "frame") {
            rotateKids(withFrameKids([cur.id]).filter(function (id) {
              return id !== cur.id;
            }).map(getEl).filter(Boolean).map(clone), G.center(cur), nr - (cur.rot || 0));
          }
          patch(cur.id, { rot: nr || undefined });
        } else if (k === "name") {
          patch(cur.id, { name: String(raw).trim() || undefined });
        } else if (k === "opacity") {
          if (n != null) patch(cur.id, { style: { opacity: Math.max(0, Math.min(100, n)) / 100 } });
        } else if (k === "fillOp" || k === "strokeOp") {
          var ok = k === "fillOp" ? "fillOpacity" : "strokeOpacity";
          var so = {};
          so[ok] = n == null ? undefined : n >= 100 ? null : Math.max(0, n) / 100;
          if (n != null) patch(cur.id, { style: so });
        } else if (k === "sizeW" || k === "sizeH") {
          var axs = k === "sizeW" ? "w" : "h";
          var own = cur.type === "frame" ? parseLayout(cur.layout) : null;
          var fz = {};
          if (own) {
            var hw0 = own.hug.indexOf("w") >= 0;
            var hh0 = own.hug.indexOf("h") >= 0;
            if (axs === "w") hw0 = raw === "hug";
            else hh0 = raw === "hug";
            own.hug = hw0 && hh0 ? "wh" : hw0 ? "w" : hh0 ? "h" : "none";
            fz.layout = layoutStr(own);
          }
          var gr = String(cur.grow || "").replace(axs, "");
          if (raw === "fill") gr += axs;
          fz.grow = gr ? (gr.indexOf("w") >= 0 ? "w" : "") + (gr.indexOf("h") >= 0 ? "h" : "") : undefined;
          patch(cur.id, fz);
        } else if (k === "lcols") {
          var LG = cur.type === "frame" ? parseLayout(cur.layout) : null;
          if (LG && n != null && n >= 1) {
            LG.cols = Math.min(24, Math.round(n));
            patch(cur.id, { layout: layoutStr(LG) });
          }
        } else if (/^(lgap|lgapm|lpadh|lpadv|lpt|lpr|lpb|lpl|hugW|hugH)$/.test(k)) {
          var LL = cur.type === "frame" ? parseLayout(cur.layout) : null;
          if (!LL) return;
          if (k === "lgapm") LL.justify = raw === "auto" ? "between" : LL.justify === "between" ? "start" : LL.justify;
          else if (k === "hugW" || k === "hugH") {
            var ax = k === "hugW" ? "w" : "h";
            var hw = LL.hug.indexOf("w") >= 0;
            var hh = LL.hug.indexOf("h") >= 0;
            if (ax === "w") hw = raw === "hug";
            else hh = raw === "hug";
            LL.hug = hw && hh ? "wh" : hw ? "w" : hh ? "h" : "none";
          } else {
            if (n == null || n < 0) return;
            if (k === "lgap") {
              LL.gap = n;
              if (LL.justify === "between") LL.justify = "start";
            } else if (k === "lpadh") LL.pad[1] = LL.pad[3] = n;
            else if (k === "lpadv") LL.pad[0] = LL.pad[2] = n;
            else LL.pad[{ lpt: 0, lpr: 1, lpb: 2, lpl: 3 }[k]] = n;
          }
          patch(cur.id, { layout: layoutStr(LL) });
        } else if (k === "clip") {
          if (cur.type === "frame") patch(cur.id, { noclip: raw ? undefined : true });
        } else if (k === "pinH" || k === "pinV") {
          var PN2 = pinOf(cur);
          PN2[k === "pinH" ? "h" : "v"] = String(raw);
          patch(cur.id, { pin: pinStr(PN2) });
        } else if (k === "fontFamily") {
          var fam = String(raw || "").trim();
          var fsx = { fontFamily: fam && FONT_RE.test(fam) ? fam : null };
          // Начертания нет у нового шрифта — ближайшее доступное.
          var curW = Number((cur.style || {}).fontWeight) || ((cur.style || {}).bold ? 600 : 400);
          var ws = fontWeights(fam);
          if (ws.indexOf(curW) < 0) fsx.fontWeight = ws.reduce(function (b, w) {
            return Math.abs(w - curW) < Math.abs(b - curW) ? w : b;
          }, ws[0]);
          if (fam) ensureFont(fam, refitFonts);
          patch(cur.id, { style: fsx });
        } else if (k === "fontWeight") {
          if (n != null) patch(cur.id, { style: { fontWeight: Math.max(100, Math.min(900, Math.round(n / 100) * 100)), bold: null } });
        } else if (k === "lineHeight") {
          patch(cur.id, { style: { lineHeight: n != null && n > 0 ? Math.min(1000, n) : null } });
        } else if (k === "letterSpacing") {
          if (n != null) patch(cur.id, { style: { letterSpacing: Math.max(-50, Math.min(200, n)) || null } });
        } else if (k === "fillGrad" || k === "strokeGrad") {
          var gs = {};
          gs[k] = raw && GRAD_RE.test(raw) ? raw : null;
          if (k === "fillGrad" && gs[k] && (!cur.style || !cur.style.fill || cur.style.fill === "none")) gs.fill = "#d9d9d9";
          patch(cur.id, { style: gs });
        } else if (k === "blur" || k === "bgBlur") {
          if (n == null || n < 0) return;
          var bs = {};
          bs[k] = Math.min(250, n) || null;
          patch(cur.id, { style: bs });
        } else if (k === "strokeAlign") {
          patch(cur.id, { style: { strokeAlign: raw === "inside" || raw === "outside" ? raw : null } });
        } else if (k === "r0" || k === "r1" || k === "r2" || k === "r3") {
          if (n == null || n < 0) return;
          var rr = radiiOf(cur.style || {}, cur.w, cur.h, 0).map(r1);
          rr[Number(k[1])] = n;
          patch(cur.id, { style: { radii: rr.join(" ") } });
        } else if (k === "radius" || k === "strokeWidth" || k === "fontSize") {
          if (n == null || n < 0) return;
          var sf = {};
          sf[k] = k === "fontSize" ? Math.max(4, n) : n;
          patch(cur.id, { style: sf });
        } else if (k === "shx" || k === "shy" || k === "shb" || k === "sha" || k === "shc") {
          var m = shadowOf(cur);
          var x = Number(m[1]);
          var y = Number(m[2]);
          var b = Number(m[3]);
          var col = m[4].slice(0, 7);
          var a = parseInt(m[4].slice(7), 16);
          if (k === "shc") col = raw;
          else if (n == null) return;
          else if (k === "shx") x = Math.round(n);
          else if (k === "shy") y = Math.round(n);
          else if (k === "shb") b = Math.max(0, Math.round(n));
          else a = Math.max(0, Math.min(100, n)) * 2.55;
          patch(cur.id, { style: { shadow: Math.max(-999, Math.min(999, x)) + " " + Math.max(-999, Math.min(999, y)) + " " + Math.min(999, b) + " " + col + hexA(a) } });
        } else if (k === "fill" || k === "stroke" || k === "textColor") {
          var fs = {};
          fs[k] = raw;
          // Сплошной цвет вместо градиента.
          if (k === "fill") fs.fillGrad = null;
          if (k === "stroke") fs.strokeGrad = null;
          if (k === "stroke" && raw !== "none" && !(Number((cur.style || {}).strokeWidth) > 0) && cur.type !== "line") fs.strokeWidth = 1;
          if (k === "stroke" && raw === "none") {
            fs.stroke = (cur.style || {}).stroke || "ink";
            fs.strokeWidth = 0;
          }
          patch(cur.id, { style: fs });
        }
      });
      syncFrames();
      commit();
    }

    /** Растянуть выделение (группу) от левого верхнего угла рамки B: по ширине sx, по высоте sy. */
    function scaleSel(B, sx, sy) {
      var X = function (x) {
        return B.x + (x - B.x) * sx;
      };
      var Y = function (y) {
        return B.y + (y - B.y) * sy;
      };
      withFrameKids(Array.from(selected)).map(getEl).filter(Boolean).forEach(function (el) {
        if (el.type === "line") {
          var res = G.resolveLine(el, getEl);
          var f = { points: [[X(res.p0.x), Y(res.p0.y)], [X(res.p1.x), Y(res.p1.y)]] };
          if (el.waypoints) f.waypoints = el.waypoints.map(function (q) {
            return [X(q[0]), Y(q[1])];
          });
          patch(el.id, f);
        } else if (el.type === "pen") {
          patch(el.id, { x: X(el.x), y: Y(el.y), w: el.w * sx, h: el.h * sy, points: (el.points || []).map(function (q) {
            return [q[0] * sx, q[1] * sy];
          }) });
        } else if (el.type === "text" || BLOCKS[el.type]) {
          patch(el.id, { x: X(el.x), y: Y(el.y), w: el.w * sx });
        } else {
          patch(el.id, { x: X(el.x), y: Y(el.y), w: el.w * sx, h: el.h * sy });
        }
      });
    }

    /** Содержимое экрана поворачивается вместе с ним: вокруг центра экрана на тот же угол (от исходных положений kids0). */
    function rotateKids(kids0, c, delta) {
      var a = (delta * Math.PI) / 180;
      var cos = Math.cos(a);
      var sin = Math.sin(a);
      var R = function (x, y) {
        var dx = x - c.x;
        var dy = y - c.y;
        return G.pt(c.x + dx * cos - dy * sin, c.y + dx * sin + dy * cos);
      };
      kids0.forEach(function (k) {
        if (!getEl(k.id)) return;
        if (k.type === "line") {
          if ((k.start && k.start.el) || (k.end && k.end.el)) return;
          var res = G.resolveLine(k, getEl);
          var a0 = R(res.p0.x, res.p0.y);
          var a1 = R(res.p1.x, res.p1.y);
          var f = { points: [[a0.x, a0.y], [a1.x, a1.y]] };
          if (k.waypoints) f.waypoints = k.waypoints.map(function (q) {
            var r = R(q[0], q[1]);
            return [r.x, r.y];
          });
          patch(k.id, f);
          return;
        }
        if (k.type === "pen") {
          var pts = (k.points || []).map(function (q) {
            return R(k.x + q[0], k.y + q[1]);
          });
          var bb = G.boundsOf(pts);
          patch(k.id, { x: bb.x, y: bb.y, w: bb.w, h: bb.h, points: pts.map(function (q) {
            return [q.x - bb.x, q.y - bb.y];
          }) });
          return;
        }
        var kc = R(k.x + k.w / 2, k.y + k.h / 2);
        var rot = (((k.rot || 0) + delta) % 360 + 540) % 360 - 180;
        patch(k.id, { x: kc.x - k.w / 2, y: kc.y - k.h / 2, rot: Math.round(rot * 10) / 10 || undefined });
      });
    }

    // ---- наведение на промежуток / отступ автолэйаута: розовая штриховка и значение (как во Figma) ----
    var gapHover = null; // {x, y, w, h, v, kind}
    function layoutZones(f) {
      var L = parseLayout(f.layout);
      if (!L || L.dir === "g" || L.wrap) return [];
      var H = L.dir === "h";
      var kids = layoutKids(f).sort(function (a, b) {
        return H ? a.x - b.x : a.y - b.y;
      });
      var out = [];
      var cx0 = f.x + L.pad[3];
      var cy0 = f.y + L.pad[0];
      var cw = f.w - L.pad[1] - L.pad[3];
      var ch = f.h - L.pad[0] - L.pad[2];
      for (var i = 1; i < kids.length; i++) {
        var a = kids[i - 1];
        var b = kids[i];
        var g = H ? b.x - (a.x + a.w) : b.y - (a.y + a.h);
        if (g <= 0.5) continue;
        out.push(H ? { x: a.x + a.w, y: cy0, w: g, h: ch, v: g, kind: "gap" } : { x: cx0, y: a.y + a.h, w: cw, h: g, v: g, kind: "gap" });
      }
      // Отступы фрейма — тоже подсвечиваются.
      if (L.pad[0] > 0.5) out.push({ x: f.x, y: f.y, w: f.w, h: L.pad[0], v: L.pad[0], kind: "pad" });
      if (L.pad[2] > 0.5) out.push({ x: f.x, y: f.y + f.h - L.pad[2], w: f.w, h: L.pad[2], v: L.pad[2], kind: "pad" });
      if (L.pad[3] > 0.5) out.push({ x: f.x, y: f.y, w: L.pad[3], h: f.h, v: L.pad[3], kind: "pad" });
      if (L.pad[1] > 0.5) out.push({ x: f.x + f.w - L.pad[1], y: f.y, w: L.pad[1], h: f.h, v: L.pad[1], kind: "pad" });
      return out;
    }

    function updateGapHover(e) {
      var next = null;
      if (mode === "design" && tool === "select" && !gesture && !editing && selected.size && e) {
        var p = toWorld(e.clientX, e.clientY);
        var cands = {};
        selList().forEach(function (el) {
          if (el.type === "frame" && el.layout) cands[el.id] = el;
          var pf = frameOf(el);
          if (pf && pf.layout) cands[pf.id] = pf;
        });
        Object.keys(cands).some(function (id) {
          var zs = layoutZones(cands[id]);
          // Промежутки важнее отступов (отступы по краям перекрываются с ними в углах).
          zs.sort(function (a, b) {
            return (a.kind === "gap" ? 0 : 1) - (b.kind === "gap" ? 0 : 1);
          });
          for (var i = 0; i < zs.length; i++) {
            var z = zs[i];
            if (p.x >= z.x && p.x <= z.x + z.w && p.y >= z.y && p.y <= z.y + z.h) {
              next = z;
              return true;
            }
          }
          return false;
        });
      }
      if (JSON.stringify(next) !== JSON.stringify(gapHover)) {
        gapHover = next;
        renderOverlay();
      }
    }

    function gapSvg(z) {
      var a = toScreen(G.pt(z.x, z.y));
      var w = Math.max(1, z.w * view.z);
      var h = Math.max(1, z.h * view.z);
      var label = String(Math.round(z.v * 10) / 10);
      var lw = 8 + label.length * 7;
      var cx = a.x + w / 2;
      var cy = a.y + h / 2;
      return '<defs><pattern id="lb-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">' +
        '<line x1="0" y1="0" x2="0" y2="6" class="lb-gap-hatch"/></pattern></defs>' +
        '<rect class="lb-gap" x="' + a.x + '" y="' + a.y + '" width="' + w + '" height="' + h + '"/>' +
        '<rect class="lb-gap-pill" x="' + (cx - lw / 2) + '" y="' + (cy - 9) + '" width="' + lw + '" height="18" rx="4"/>' +
        '<text class="lb-gap-label" x="' + cx + '" y="' + (cy + 4) + '">' + label + "</text>";
    }

    // ---- автолэйаут экрана: «dir gap pt pr pb pl align justify hug» ----
    // dir h|v; align — по поперечной оси start|center|end; justify — по главной start|center|end|between; hug none|w|h|wh.
    function parseLayout(str) {
      var p = String(str || "").trim().split(/\s+/);
      if (p[0] !== "h" && p[0] !== "v" && p[0] !== "g") return null;
      var n = function (i) {
        var v = Number(p[i]);
        return isFinite(v) && v >= 0 ? v : 0;
      };
      var ex = p[9] || "";
      return {
        dir: p[0], gap: n(1), pad: [n(2), n(3), n(4), n(5)],
        align: /^(start|center|end)$/.test(p[6]) ? p[6] : "start",
        justify: /^(start|center|end|between)$/.test(p[7]) ? p[7] : "start",
        hug: /^(none|w|h|wh)$/.test(p[8]) ? p[8] : "none",
        // Перенос строк (горизонтальный) и число колонок сетки.
        wrap: ex === "wrap",
        cols: /^c\d{1,2}$/.test(ex) ? Math.max(1, Number(ex.slice(1))) : 2,
      };
    }

    function layoutStr(L) {
      var ex = L.dir === "g" ? "c" + Math.max(1, Math.min(24, Math.round(L.cols || 2))) : L.dir === "h" && L.wrap ? "wrap" : "";
      return [L.dir, r1(L.gap), r1(L.pad[0]), r1(L.pad[1]), r1(L.pad[2]), r1(L.pad[3]), L.align, L.justify, L.hug].concat(ex ? [ex] : []).join(" ");
    }

    function layoutKids(f) {
      var out = [];
      els.forEach(function (e) {
        if (e.frame === f.id && e.type !== "line" && e.type !== "pen") out.push(e);
      });
      return out;
    }

    function grows(e, ax) {
      return String(e.grow || "").indexOf(ax) >= 0;
    }

    /** Поставить ребёнка на место (с его содержимым) и, если надо, задать размер. */
    function placeKid(k, x, y, w, h) {
      var dx = r1(x - k.x);
      var dy = r1(y - k.y);
      if (dx || dy) moveBy(withFrameKids([k.id]), dx, dy);
      var f = {};
      if (w != null && Math.abs(r1(w) - k.w) > 0.05) f.w = Math.max(1, r1(w));
      if (h != null && Math.abs(r1(h) - k.h) > 0.05) f.h = Math.max(1, r1(h));
      if (f.w != null || f.h != null) {
        if (k.type === "frame" && k.device) f.device = undefined;
        resizeWithPins(k.id, f);
        if (k.type === "frame" && k.layout) applyLayout(k.id); // свой автолэйаут — под новый размер
      }
    }

    // ---- привязка к краям фрейма (constraints, как во Figma): pin «h v» ----
    // h — l|r|lr|c|s (слева, справа, слева и справа, по центру, масштаб); v — t|b|tb|c|s. По умолчанию «l t».
    function pinOf(el) {
      var p = String((el && el.pin) || "").split(" ");
      return { h: /^(l|r|lr|c|s)$/.test(p[0]) ? p[0] : "l", v: /^(t|b|tb|c|s)$/.test(p[1]) ? p[1] : "t" };
    }

    function pinStr(P) {
      var s = P.h + " " + P.v;
      return s === "l t" ? undefined : s;
    }

    /** Новое положение и размер по одной оси: a0/s0 — ребёнок, o0/os — фрейм до, n0/ns — после. */
    function pinAxis(m, a0, s0, o0, os, n0, ns, sizable) {
      m = { t: "l", b: "r", tb: "lr" }[m] || m;
      var lo = a0 - o0;
      var hi = o0 + os - (a0 + s0);
      if (m === "r") return [n0 + ns - hi - s0, s0];
      if (m === "c") return [n0 + ns / 2 + (a0 + s0 / 2 - (o0 + os / 2)) - s0 / 2, s0];
      if (m === "lr" && sizable) return [n0 + lo, Math.max(1, ns - lo - hi)];
      if (m === "s") {
        var k = os > 0 ? ns / os : 1;
        return sizable ? [n0 + lo * k, Math.max(1, s0 * k)] : [n0 + (lo + s0 / 2) * k - s0 / 2, s0];
      }
      return [n0 + lo, s0];
    }

    /** Фрейм сменил рамку o → n: содержимое (по снимку snap: id → копия до правки) встаёт по привязкам. */
    function applyPins(fid, o, n, snap) {
      snap.forEach(function (k0) {
        if (k0.frame !== fid) return;
        var b = G.elBounds(k0, function (id) {
          return snap.get(id) || getEl(id);
        });
        if (!b) return;
        var P = pinOf(k0);
        var box = k0.type !== "line" && k0.type !== "pen";
        var sw = box && !k0.rot && !(k0.type === "text" && k0.autoW);
        var sh = box && !k0.rot && k0.type !== "text" && !BLOCKS[k0.type];
        var X = pinAxis(P.h, b.x, b.w, o.x, o.w, n.x, n.w, sw);
        var Y = pinAxis(P.v, b.y, b.h, o.y, o.h, n.y, n.h, sh);
        var dx = r1(X[0] - b.x);
        var dy = r1(Y[0] - b.y);
        if (!box) {
          var cur = getEl(k0.id);
          if (cur) patch(k0.id, k0.type === "pen" ? { x: k0.x + dx, y: k0.y + dy } : moved(k0, dx, dy, {}));
          return;
        }
        var f = { x: r1(k0.x + dx), y: r1(k0.y + dy), w: r1(X[1]), h: r1(Y[1]) };
        patch(k0.id, f);
        if (k0.type === "frame") {
          applyPins(k0.id, k0, f, snap);
          if (k0.layout && (f.w !== k0.w || f.h !== k0.h)) applyLayout(k0.id);
        }
      });
    }

    /** Снимок содержимого фрейма (для applyPins). */
    function pinSnap(fid) {
      var snap = new Map();
      withFrameKids([fid]).forEach(function (id) {
        var e = getEl(id);
        if (id !== fid && e) snap.set(id, clone(e));
      });
      return snap;
    }

    /** Правка рамки элемента; у фрейма без автолэйаута содержимое следует привязкам. */
    function resizeWithPins(id, f) {
      var cur = getEl(id);
      if (!cur || cur.type !== "frame" || cur.layout || cur.rot) return patch(id, f);
      var o = { x: cur.x, y: cur.y, w: cur.w, h: cur.h };
      var snap = pinSnap(id);
      patch(id, f);
      var c2 = getEl(id);
      if (c2 && (c2.x !== o.x || c2.y !== o.y || c2.w !== o.w || c2.h !== o.h)) applyPins(id, o, c2, snap);
    }

    /** «Обнимающий» фрейм меняет размер по содержимому: растёт от края, к которому привязан в родителе
     * (снизу — вверх, по центру — в обе стороны), как во Figma. Прижатый к низу/правому краю без привязки — тоже. */
    function hugResize(f, w, h) {
      var fx = { w: w, h: h, device: undefined };
      var par = frameOf(f);
      if (par && !par.layout && !f.rot) {
        var P = pinOf(f);
        var near = function (u, v) {
          return Math.abs(u - v) < 0.5;
        };
        [["h", "x", "w", "r", "lr"], ["v", "y", "h", "b", "tb"]].forEach(function (a) {
          var m = P[a[0]];
          var d = f[a[2]] - fx[a[2]];
          if (!d) return;
          var flush = !f.pin && near(f[a[1]] + f[a[2]], par[a[1]] + par[a[2]]) && !near(f[a[1]], par[a[1]]);
          if (m === a[3] || flush) fx[a[1]] = r1(f[a[1]] + d);
          else if (m === "c") fx[a[1]] = r1(f[a[1]] + d / 2);
        });
      }
      if (fx.x != null || fx.y != null) {
        // Не входящее в раскладку (линии, рисунки) — вместе с фреймом.
        var dx = fx.x != null ? fx.x - f.x : 0;
        var dy = fx.y != null ? fx.y - f.y : 0;
        var loose = [];
        els.forEach(function (e) {
          if (e.frame === f.id && (e.type === "line" || e.type === "pen")) loose.push(e.id);
        });
        if (loose.length) moveBy(loose, dx, dy);
      }
      return patch(f.id, fx);
    }

    /** Разложить содержимое фрейма по правилам. Порядок — по положению вдоль главной оси (или явный order). */
    function applyLayout(fid, order) {
      var f = getEl(fid);
      var L = f && f.type === "frame" ? parseLayout(f.layout) : null;
      if (!L) return;
      if (L.dir === "g" || (L.dir === "h" && L.wrap)) return applyFlow(f, L, order);
      var H = L.dir === "h";
      var MA = H ? "w" : "h"; // главная ось
      var CA = H ? "h" : "w";
      var kids = layoutKids(f).sort(function (a, b) {
        if (order) return order.indexOf(a.id) - order.indexOf(b.id);
        return H ? a.x + a.w / 2 - (b.x + b.w / 2) : a.y + a.h / 2 - (b.y + b.h / 2);
      });
      var hugM = L.hug.indexOf(MA) >= 0;
      var hugC = L.hug.indexOf(CA) >= 0;
      // «Заполнить» у ребёнка не действует по оси, где фрейм сам «обнимает» содержимое.
      var gm = function (k) {
        return !hugM && grows(k, MA);
      };
      var gc = function (k) {
        return !hugC && grows(k, CA);
      };
      var pMs = H ? L.pad[3] : L.pad[0];
      var pMe = H ? L.pad[1] : L.pad[2];
      var pCs = H ? L.pad[0] : L.pad[3];
      var pCe = H ? L.pad[2] : L.pad[1];
      var gaps = L.gap * Math.max(0, kids.length - 1);
      var sumAll = kids.reduce(function (a, e) {
        return a + e[MA];
      }, 0);
      var maxC = kids.reduce(function (a, e) {
        return Math.max(a, e[CA]);
      }, 0);
      var sz = { w: f.w, h: f.h };
      if (hugM) sz[MA] = kids.length ? sumAll + gaps + pMs + pMe : Math.max(1, pMs + pMe);
      if (hugC) sz[CA] = kids.length ? maxC + pCs + pCe : Math.max(1, pCs + pCe);
      sz.w = Math.max(1, r1(sz.w));
      sz.h = Math.max(1, r1(sz.h));
      if (sz.w !== f.w || sz.h !== f.h) f = hugResize(f, sz.w, sz.h);
      var availM = f[MA] - pMs - pMe;
      var availC = f[CA] - pCs - pCe;
      // Заполняющие делят свободное место поровну.
      var fixed = kids.reduce(function (a, e) {
        return a + (gm(e) ? 0 : e[MA]);
      }, 0);
      var nGrow = kids.filter(gm).length;
      var each = nGrow ? Math.max(1, (availM - fixed - gaps) / nGrow) : 0;
      // Мин./макс. размеры (из Figma): «заполнить» не сжимает меньше минимума и не растит больше максимума.
      var lim = function (k, ax, v) {
        var mn = k[ax === "w" ? "minW" : "minH"];
        var mx = k[ax === "w" ? "maxW" : "maxH"];
        if (mx > 0) v = Math.min(v, mx);
        if (mn > 0) v = Math.max(v, mn);
        return v;
      };
      var sizeM = function (k) {
        return gm(k) ? lim(k, MA, each) : k[MA];
      };
      var sizeC = function (k) {
        return gc(k) ? lim(k, CA, availC) : k[CA];
      };
      var sum = kids.reduce(function (a, e) {
        return a + sizeM(e);
      }, 0);
      var total = sum + gaps;
      var gap = L.gap;
      var pos = pMs;
      if (!nGrow) {
        if (L.justify === "center") pos += (availM - total) / 2;
        else if (L.justify === "end") pos += availM - total;
        else if (L.justify === "between" && kids.length > 1) gap = (availM - sum) / (kids.length - 1);
      }
      kids.forEach(function (k0) {
        var k = getEl(k0.id);
        var m = sizeM(k);
        var c = sizeC(k);
        var cpos = pCs + (L.align === "center" ? (availC - c) / 2 : L.align === "end" ? availC - c : 0);
        var size = {};
        size[MA] = gm(k) ? m : null;
        size[CA] = gc(k) ? c : null;
        placeKid(k, f.x + (H ? pos : cpos), f.y + (H ? cpos : pos), size.w, size.h);
        pos += m + gap;
      });
    }

    /** Перенос строк (горизонтальный) и сетка: строки по ширине фрейма; в сетке — колонки равной ширины. */
    function applyFlow(f, L, order) {
      var grid = L.dir === "g";
      var kids = layoutKids(f).sort(function (a, b) {
        if (order) return order.indexOf(a.id) - order.indexOf(b.id);
        // Порядок чтения: сверху вниз, слева направо (строка — по центру по вертикали с допуском).
        var dy = a.y + a.h / 2 - (b.y + b.h / 2);
        return Math.abs(dy) > Math.min(a.h, b.h) / 2 ? dy : a.x - b.x;
      });
      var hugW = L.hug.indexOf("w") >= 0;
      var hugH = L.hug.indexOf("h") >= 0;
      var cols = Math.max(1, L.cols || 2);
      var maxW = kids.reduce(function (a, e) {
        return Math.max(a, e.w);
      }, 0);
      var W = f.w;
      if (hugW) {
        // «Обнимать» по ширине: сетка — колонки по самому широкому, перенос — всё в одну строку.
        W = grid ? cols * maxW + L.gap * (cols - 1) + L.pad[1] + L.pad[3] : kids.reduce(function (a, e) {
          return a + e.w;
        }, 0) + L.gap * Math.max(0, kids.length - 1) + L.pad[1] + L.pad[3];
      }
      var availW = W - L.pad[1] - L.pad[3];
      var cellW = grid ? Math.max(1, (availW - L.gap * (cols - 1)) / cols) : 0;
      // Строки.
      var rows = [];
      var row = [];
      var used = 0;
      kids.forEach(function (k) {
        var w = grid ? (grows(k, "w") ? cellW : Math.min(k.w, cellW)) : k.w;
        var full = grid ? row.length >= cols : row.length && used + L.gap + w > availW + 0.5;
        if (full) {
          rows.push(row);
          row = [];
          used = 0;
        }
        row.push({ k: k, w: w });
        used += (row.length > 1 ? L.gap : 0) + w;
      });
      if (row.length) rows.push(row);
      var heights = rows.map(function (r) {
        return r.reduce(function (a, it) {
          return Math.max(a, it.k.h);
        }, 0);
      });
      var contentH = heights.reduce(function (a, h) {
        return a + h;
      }, 0) + L.gap * Math.max(0, rows.length - 1);
      var Hh = hugH ? contentH + L.pad[0] + L.pad[2] : f.h;
      W = Math.max(1, r1(W));
      Hh = Math.max(1, r1(Hh));
      if (W !== f.w || Hh !== f.h) f = hugResize(f, W, Hh);
      var y = f.y + L.pad[0];
      // Сетка: вертикально — по «распределению»; перенос: по горизонтали — распределение, в строке по вертикали — выравнивание.
      if (!hugH && grid) {
        var freeH = f.h - L.pad[0] - L.pad[2] - contentH;
        if (L.justify === "center") y += freeH / 2;
        else if (L.justify === "end") y += freeH;
      }
      rows.forEach(function (r, ri) {
        var rowW = r.reduce(function (a, it) {
          return a + it.w;
        }, 0) + L.gap * (r.length - 1);
        var x = f.x + L.pad[3];
        var gap = L.gap;
        if (!grid) {
          var free = availW - rowW;
          if (L.justify === "center") x += free / 2;
          else if (L.justify === "end") x += free;
          else if (L.justify === "between" && r.length > 1) gap = (availW - rowW + L.gap * (r.length - 1)) / (r.length - 1);
        }
        r.forEach(function (it, i) {
          var k = getEl(it.k.id);
          var cx = grid ? f.x + L.pad[3] + i * (cellW + L.gap) : x;
          var cw = grid ? cellW : it.w;
          var kh = grows(k, "h") ? heights[ri] : k.h;
          // В ячейке: по горизонтали — выравнивание, по вертикали — сверху строки.
          var ox = grid ? (L.align === "center" ? (cw - it.w) / 2 : L.align === "end" ? cw - it.w : 0) : 0;
          var oy = !grid && !grows(k, "h") ? (L.align === "center" ? (heights[ri] - k.h) / 2 : L.align === "end" ? heights[ri] - k.h : 0) : 0;
          // Текст шире ячейки — по ширине ячейки (он переносится по строкам), остальное сохраняет размер.
          var kw = grid && (grows(k, "w") || (k.type === "text" && k.w > cellW)) ? cellW : null;
          placeKid(k, cx + ox, y + oy, kw, grows(k, "h") ? kh : null);
          x += it.w + gap;
        });
        y += heights[ri] + L.gap;
      });
    }

    /** Стрелки у объекта в автолэйауте — поменяться местами с соседом вдоль оси (как во Figma). */
    function reorderInLayout(dx, dy) {
      var list = selList();
      var f = list.length ? frameOf(list[0]) : null;
      var L = f ? parseLayout(f.layout) : null;
      if (!L || !list.every(function (e) {
        return frameOf(e) === f;
      })) return false;
      var H = L.dir === "h";
      var flow = L.dir === "g" || (H && L.wrap);
      // Сетка: ←→ — на одну ячейку, ↑↓ — на строку; перенос — на одну позицию; иначе — только вдоль оси.
      var step = flow ? (dx ? Math.sign(dx) : Math.sign(dy) * (L.dir === "g" ? L.cols : 1)) : Math.sign(H ? dx : dy);
      if (!step) return true; // поперёк оси в автолэйауте не двигаем
      var kids = layoutKids(f).sort(function (a, b) {
        if (flow) {
          var d = a.y + a.h / 2 - (b.y + b.h / 2);
          return Math.abs(d) > Math.min(a.h, b.h) / 2 ? d : a.x - b.x;
        }
        return H ? a.x - b.x : a.y - b.y;
      }).map(function (e) {
        return e.id;
      });
      var sel = kids.filter(function (id) {
        return selected.has(id);
      });
      var rest = kids.filter(function (id) {
        return !selected.has(id);
      });
      var first = kids.indexOf(sel[0]);
      var at = Math.max(0, Math.min(rest.length, first + step));
      var order = rest.slice(0, at).concat(sel, rest.slice(at));
      if (order.join() === kids.join()) return true;
      begin();
      applyLayout(f.id, order);
      commit();
      return true;
    }

    /** Какие экраны с автолэйаутом затронула правка (и их внешние — у «обнимающих» меняется размер). */
    function relayout(ids) {
      var todo = {};
      var add = function (id) {
        for (var f = getEl(id), g = 0; f && g < 20; f = frameOf(f), g++) if (f.type === "frame" && f.layout) todo[f.id] = 1;
      };
      ids.forEach(function (id) {
        var el = getEl(id);
        if (el) {
          if (el.type === "frame") add(el.id);
          if (el.frame) add(el.frame);
        }
        var before = tx && tx.before.get(id);
        if (before && before.frame) add(before.frame); // ушёл из экрана — экран пересобрать
      });
      var list = Object.keys(todo).map(getEl).filter(Boolean).sort(function (a, b) {
        return frameDepth(b) - frameDepth(a); // сначала вложенные
      });
      list.forEach(function (f) {
        applyLayout(f.id);
      });
    }

    /** ⇧A: экрану — автолэйаут; выделенным объектам — новый экран-обёртка с автолэйаутом (как во Figma). */
    function addAutoLayout(dirForce) {
      var list = selList().filter(function (e) {
        return e.type !== "line" && e.type !== "pen";
      });
      if (!list.length || readOnly) return;
      var guess = function (items) {
        if (items.length < 2) return { dir: "v", gap: 8, align: "start" };
        // Направление — куда разбросаны центры: стопка — вертикально, ряд — горизонтально.
        var cx = items.map(function (e) {
          return e.x + e.w / 2;
        });
        var cy = items.map(function (e) {
          return e.y + e.h / 2;
        });
        var H = Math.max.apply(null, cx) - Math.min.apply(null, cx) > Math.max.apply(null, cy) - Math.min.apply(null, cy);
        var s0 = items.slice().sort(function (a, c) {
          return H ? a.x - c.x : a.y - c.y;
        });
        var gaps = [];
        for (var i = 1; i < s0.length; i++) gaps.push(H ? s0[i].x - (s0[i - 1].x + s0[i - 1].w) : s0[i].y - (s0[i - 1].y + s0[i - 1].h));
        gaps.sort(function (a, c) {
          return a - c;
        });
        // Уже выровнены между собой (левые края, центры, правые края) — так и оставляем.
        var near = function (arr) {
          return Math.max.apply(null, arr) - Math.min.apply(null, arr) <= 1.5;
        };
        var a0 = items.map(function (e) {
          return H ? e.y : e.x;
        });
        var a1 = items.map(function (e) {
          return H ? e.y + e.h / 2 : e.x + e.w / 2;
        });
        var a2 = items.map(function (e) {
          return H ? e.y + e.h : e.x + e.w;
        });
        var align = near(a0) ? "start" : near(a1) ? "center" : near(a2) ? "end" : "start";
        return { dir: H ? "h" : "v", gap: Math.max(0, Math.round(gaps[Math.floor(gaps.length / 2)] || 0)), align: align };
      };
      begin();
      if (list.length === 1 && list[0].type === "frame") {
        var f = list[0];
        if (f.layout) {
          commit();
          return;
        }
        var kids = layoutKids(f);
        var g0 = guess(kids);
        if (dirForce) g0.dir = dirForce;
        var kb = kids.length ? G.unionBounds(kids.map(function (e) {
          return G.elBounds(e, getEl);
        })) : null;
        var pad = kb ? [kb.y - f.y, f.x + f.w - (kb.x + kb.w), f.y + f.h - (kb.y + kb.h), kb.x - f.x].map(function (v) {
          return Math.max(0, Math.round(v));
        }) : [16, 16, 16, 16];
        patch(f.id, { layout: layoutStr({ dir: g0.dir, gap: g0.gap, pad: pad, align: g0.align, justify: "start", hug: "none" }) });
        relayout([f.id]);
      } else {
        // Обёртка: экран вокруг выделенного, они — внутри, размер по содержимому.
        var b = G.unionBounds(list.map(function (e) {
          return G.elBounds(e, getEl);
        }));
        var g1 = guess(list);
        var parent = frameOf(list[0]);
        var wrap = newEl("frame", G.pt(b.x, b.y), {
          w: b.w, h: b.h, name: "Автолэйаут", style: { fill: "#ffffff", fillOff: true, radius: 0 },
          layout: layoutStr({ dir: g1.dir, gap: g1.gap, pad: [0, 0, 0, 0], align: g1.align, justify: "start", hug: "wh" }),
          z: Math.min.apply(null, list.map(function (e) {
            return e.z || 0;
          })) - 0.5,
        });
        if (parent) wrap.frame = parent.id;
        put(wrap);
        list.forEach(function (e) {
          patch(e.id, { frame: wrap.id, group: undefined });
        });
        relayout([wrap.id]);
        setSelection([wrap.id]);
      }
      commit();
    }

    function layoutAct(a, b) {
      var f = selList()[0];
      if (!f || f.type !== "frame") return;
      var L = parseLayout(f.layout);
      begin();
      if (a === "alAdd") {
        commit();
        addAutoLayout();
        return;
      }
      if (a === "alDir" && !b.getAttribute("data-ldir")) a = "alDel";
      if (!L && a === "alDir") {
        commit();
        addAutoLayout(b.getAttribute("data-ldir"));
        return;
      }
      if (a === "padSplit") {
        padSplit = !padSplit;
        inspKey = "";
        commit();
        renderInspector(selList());
        return;
      }
      if (!L) return commit();
      if (a === "alDel") patch(f.id, { layout: undefined });
      else {
        if (a === "alDir") L.dir = b.getAttribute("data-ldir");
        else if (a === "alCell") {
          L.align = b.getAttribute("data-lc");
          if (L.justify !== "between") L.justify = b.getAttribute("data-lm");
        } else if (a === "alBetween") L.justify = L.justify === "between" ? "start" : "between";
        else if (a === "alWrap") L.wrap = !L.wrap;
        else if (a === "hugW" || a === "hugH") {
          var w = L.hug.indexOf("w") >= 0;
          var h = L.hug.indexOf("h") >= 0;
          if (a === "hugW") w = !w;
          else h = !h;
          L.hug = w && h ? "wh" : w ? "w" : h ? "h" : "none";
        }
        patch(f.id, { layout: layoutStr(L) });
      }
      commit();
    }

    // ---- панель слоёв (слева): экраны → группы → объекты, сверху — верхние по порядку ----
    var showLayers = { design: null, board: null };
    var layersFold = {};
    var layersKey = "";
    var layerHover = "";
    var layerDrag = null;

    function layersOn() {
      if (readOnly || !layersEl) return false;
      if (showLayers[mode] == null) {
        var saved = null;
        try {
          saved = localStorage.getItem("leo-board-layers-" + mode);
        } catch (_) {}
        // По умолчанию: в макете открыта (если место есть), на доске — закрыта.
        showLayers[mode] = saved != null ? saved === "1" : mode === "design" && root.offsetWidth > 900;
      }
      return !!showLayers[mode];
    }

    var TYPE_RU = { text: "Текст", rect: "Прямоугольник", ellipse: "Эллипс", diamond: "Ромб", pill: "Скруглённый блок", cylinder: "Цилиндр",
      triangle: "Треугольник", triangleDown: "Треугольник", parallelogram: "Параллелограмм", parallelogramL: "Параллелограмм", line: "Линия", image: "Картинка", vector: "Вектор",
      table: "Таблица", sticky: "Стикер", pen: "Рисунок", card: "Карточка", gantt: "Гант", frame: "Фрейм" };
    var TYPE_ICON = { text: "text", rect: "rect", ellipse: "ellipse", diamond: "diamond", pill: "pill", cylinder: "cylinder", triangle: "triangle",
      triangleDown: "triangleDown", parallelogram: "parallelogram", parallelogramL: "parallelogramL", line: "line", image: "image", vector: "penTool",
      table: "table", sticky: "sticky", pen: "pen", card: "child", gantt: "taskAdd", frame: "frame" };

    function layerName(el) {
      if (el.type === "frame") return el.name || "Фрейм";
      if (el.type === "line" && el.arrowEnd) return el.text ? oneLine(el.text) : "Стрелка";
      var t = el.text || el.title || el.name || "";
      return t ? oneLine(String(t).split("\n")[0]).slice(0, 48) : TYPE_RU[el.type] || "Объект";
    }

    /** Узлы одного уровня (внутри экрана pk или на холсте ""): объекты и группы, по убыванию z. */
    function layerNodes(pk, kids) {
      var groups = {};
      var out = [];
      (kids[pk] || []).forEach(function (el) {
        if (el.group) {
          var g = groups[el.group];
          if (!g) {
            g = groups[el.group] = { kind: "group", id: "g:" + el.group, gid: el.group, members: [], z: -Infinity };
            out.push(g);
          }
          g.members.push(el);
          g.z = Math.max(g.z, el.z || 0);
        } else out.push({ kind: "el", id: el.id, el: el, z: el.z || 0 });
      });
      out.forEach(function (n) {
        if (n.kind === "group") n.members.sort(function (a, b) {
          return (b.z || 0) - (a.z || 0);
        });
      });
      return out.sort(function (a, b) {
        return b.z - a.z;
      });
    }

    function layerKids() {
      var kids = {};
      els.forEach(function (el) {
        var pk = el.frame && els.get(el.frame) ? el.frame : "";
        (kids[pk] = kids[pk] || []).push(el);
      });
      return kids;
    }

    function renderLayers() {
      if (!layersEl) return;
      var on = layersOn();
      layersEl.classList.toggle("hidden", !on);
      root.classList.toggle("has-layers", on);
      if (!on) {
        layersKey = "";
        return;
      }
      var kids = layerKids();
      var key = JSON.stringify([Array.from(els.values()).map(function (e) {
        return [e.id, e.z, e.frame, e.group, e.name, e.type, e.type === "frame" ? 0 : String(e.text || "").slice(0, 48), e.hidden, e.locked];
      }), Array.from(selected), layersFold, mode]);
      if (key === layersKey || (layersEl.contains(document.activeElement) && document.activeElement.isContentEditable)) return;
      layersKey = key;
      var rows = [];
      var row = function (n, depth) {
        var isGroup = n.kind === "group";
        var el = n.el;
        var hasKids = isGroup || (el.type === "frame" && (kids[el.id] || []).length);
        var fold = layersFold[n.id];
        var sel = isGroup ? n.members.every(function (m) {
          return selected.has(m.id);
        }) : selected.has(el.id);
        var members = isGroup ? n.members : [el];
        var hid = members.every(function (m) {
          return m.hidden;
        });
        var lck = members.every(function (m) {
          return m.locked;
        });
        rows.push('<div class="lb-layer' + (sel ? " is-sel" : "") + (el && el.type === "frame" ? " is-frame" : "") + (isGroup ? " is-group" : "") +
          (hid ? " is-hidden" : "") + (lck ? " is-locked" : "") + (!hid && members.every(isHidden) ? " is-dim" : "") +
          '" data-lid="' + esc(n.id) + '" draggable="true" style="--d:' + depth + '">' +
          '<span class="lb-lcaret' + (hasKids ? "" : " is-none") + (fold ? "" : " is-open") + '" data-caret="1">' + (hasKids ? icon("caret") : "") + "</span>" +
          '<span class="lb-licon">' + icon(isGroup ? "group" : TYPE_ICON[el.type] || "rect") + "</span>" +
          '<span class="lb-lname"' + (el && el.type === "frame" ? ' data-rename="1"' : "") + ">" + esc(isGroup ? "Группа" : layerName(el)) + "</span>" +
          '<span class="lb-lacts"><button type="button" class="lb-lact' + (lck ? " is-on" : "") + '" data-lact="locked" title="' + (lck ? "Разблокировать" : "Заблокировать") + '">' + icon(lck ? "lock" : "unlock") + "</button>" +
          '<button type="button" class="lb-lact' + (hid ? " is-on" : "") + '" data-lact="hidden" title="' + (hid ? "Показать" : "Скрыть") + '">' + icon(hid ? "eyeOff" : "eye") + "</button></span></div>");
        if (!hasKids || fold) return;
        if (isGroup) n.members.forEach(function (m) {
          row({ kind: "el", id: m.id, el: m, z: m.z || 0 }, depth + 1);
        });
        else layerNodes(el.id, kids).forEach(function (c) {
          row(c, depth + 1);
        });
      };
      layerNodes("", kids).forEach(function (n) {
        row(n, 0);
      });
      layersEl.innerHTML = '<div class="lb-lhead">Слои</div><div class="lb-llist">' + (rows.join("") || '<div class="lb-lempty">Пока пусто</div>') + "</div>";
      var s0 = layersEl.querySelector(".lb-layer.is-sel");
      if (s0 && typeof s0.scrollIntoView === "function") s0.scrollIntoView({ block: "nearest" });
    }

    function layerIds(lid) {
      if (lid.indexOf("g:") === 0) {
        var gid = lid.slice(2);
        var out = [];
        els.forEach(function (el) {
          if (el.group === gid) out.push(el.id);
        });
        return out;
      }
      return getEl(lid) ? [lid] : [];
    }

    /** z по порядку панели (снизу вверх): экран, затем его содержимое; группы — подряд. Один шаг с перестановкой. */
    function normalizeZ() {
      var kids = layerKids();
      var order = [];
      var emit = function (pk) {
        layerNodes(pk, kids).reverse().forEach(function (n) {
          var list = n.kind === "group" ? n.members.slice().reverse() : [n.el];
          list.forEach(function (el) {
            order.push(el);
            if (el.type === "frame") emit(el.id);
          });
        });
      };
      emit("");
      order.forEach(function (el, i) {
        if ((el.z || 0) !== i + 1) patch(el.id, { z: i + 1 });
      });
    }

    /** Перетаскивание в панели: выше/ниже строки или внутрь экрана / группы. */
    function dropLayer(dragLid, targetLid, pos) {
      var ids = layerIds(dragLid);
      if (!ids.length || dragLid === targetLid) return;
      var tIds = layerIds(targetLid);
      if (!tIds.length) return;
      // Экран внутрь самого себя — нельзя.
      var inside = withFrameKids(ids);
      if (tIds.some(function (t) {
        return inside.indexOf(t) >= 0;
      })) return;
      var tEl = getEl(tIds[0]);
      var tGroup = targetLid.indexOf("g:") === 0 ? targetLid.slice(2) : "";
      var parent;
      var group;
      var zRef;
      var above = pos !== "after";
      if (pos === "inside" && tEl.type === "frame" && !tGroup) {
        parent = tEl.id;
        group = undefined;
        zRef = null; // наверх внутри экрана
      } else if (pos === "inside" && tGroup) {
        parent = tEl.frame && getEl(tEl.frame) ? tEl.frame : undefined;
        group = tGroup;
        zRef = null;
      } else {
        parent = tEl.frame && getEl(tEl.frame) ? tEl.frame : undefined;
        group = tGroup ? undefined : tEl.group || undefined;
        zRef = tGroup ? tIds.map(getEl).reduce(function (m, e) {
          return above ? Math.max(m, e.z || 0) : Math.min(m, e.z || 0);
        }, above ? -Infinity : Infinity) : tEl.z || 0;
      }
      begin();
      var zs = Array.from(els.values()).map(function (e) {
        return e.z || 0;
      });
      var top = Math.max.apply(null, zs.concat([0]));
      var step = 0.001;
      ids.map(getEl).filter(Boolean).sort(function (a, c) {
        return (a.z || 0) - (c.z || 0);
      }).forEach(function (el, i) {
        var f = { frame: parent, group: dragLid.indexOf("g:") === 0 && !group ? el.group : group };
        // Чуть выше (ниже) цели — порядок переносимых сохраняется; normalizeZ потом расставит целые.
        f.z = zRef == null ? top + 1 + i * step : above ? zRef + step * (i + 1) / 10 : zRef - step * (ids.length - i) / 10;
        patch(el.id, f);
      });
      // В другой экран — объект должен в нём и лежать (иначе вернётся при следующем переносе).
      var pf = parent && getEl(parent);
      if (pf) {
        var b = G.unionBounds(ids.map(function (id) {
          return G.elBounds(getEl(id), getEl);
        }));
        if (b && !(b.x + b.w / 2 >= pf.x && b.x + b.w / 2 <= pf.x + pf.w && b.y + b.h / 2 >= pf.y && b.y + b.h / 2 <= pf.y + pf.h)) {
          moveBy(withFrameKids(ids), pf.x + pf.w / 2 - (b.x + b.w / 2), pf.y + pf.h / 2 - (b.y + b.h / 2));
        }
      }
      normalizeZ();
      commit();
    }

    if (layersEl) {
      layersEl.addEventListener("pointerdown", function (e) {
        e.stopPropagation();
      });
      layersEl.addEventListener("click", function (e) {
        var r = e.target.closest("[data-lid]");
        if (!r) return;
        var lid = r.getAttribute("data-lid");
        var la = e.target.closest("[data-lact]");
        if (la && !readOnly) {
          // Глаз / замок: на объект (у группы — на всех), содержимое фрейма наследует.
          var k = la.getAttribute("data-lact");
          var ids1 = layerIds(lid);
          var on = !ids1.every(function (id) {
            return getEl(id) && getEl(id)[k];
          });
          begin();
          ids1.forEach(function (id) {
            var f = {};
            f[k] = on || undefined;
            patch(id, f);
            withFrameKids([id]).forEach(markDirty);
            if (on) selected.delete(id);
          });
          commit();
          layersKey = "";
          schedule();
          return;
        }
        if (e.target.closest("[data-caret]")) {
          layersFold[lid] = !layersFold[lid];
          renderLayers();
          return;
        }
        var ids = layerIds(lid);
        if (!ids.length) return;
        if (e.shiftKey || e.metaKey || e.ctrlKey) {
          var next = new Set(selected);
          var all = ids.every(function (id) {
            return next.has(id);
          });
          ids.forEach(function (id) {
            if (all) next.delete(id);
            else next.add(id);
          });
          selected = next;
        } else selected = new Set(ids); // объект в группе — только он (как вход в группу)
        schedule();
      });
      layersEl.addEventListener("dblclick", function (e) {
        var nm = e.target.closest("[data-rename]");
        var r = e.target.closest("[data-lid]");
        if (!nm || !r || readOnly) return;
        var id = r.getAttribute("data-lid");
        nm.contentEditable = "true";
        nm.focus();
        var rg = document.createRange();
        rg.selectNodeContents(nm);
        var ws = window.getSelection();
        ws.removeAllRanges();
        ws.addRange(rg);
        var done = function (save) {
          nm.removeEventListener("blur", onBlur);
          nm.contentEditable = "false";
          var v = nm.textContent.trim();
          layersKey = "";
          if (save && getEl(id)) {
            begin();
            patch(id, { name: v || undefined });
            commit();
          } else renderLayers();
        };
        var onBlur = function () {
          done(true);
        };
        nm.addEventListener("blur", onBlur);
        nm.addEventListener("keydown", function (ev) {
          ev.stopPropagation();
          if (ev.key === "Enter") {
            ev.preventDefault();
            nm.blur();
          } else if (ev.key === "Escape") {
            nm.removeEventListener("blur", onBlur);
            done(false);
            stage.focus({ preventScroll: true });
          }
        });
      });
      layersEl.addEventListener("mouseover", function (e) {
        var r = e.target.closest("[data-lid]");
        var lid = r ? r.getAttribute("data-lid") : "";
        if (lid !== layerHover) {
          layerHover = lid;
          renderOverlay();
        }
      });
      layersEl.addEventListener("mouseleave", function () {
        layerHover = "";
        renderOverlay();
      });
      layersEl.addEventListener("dragstart", function (e) {
        var r = e.target.closest("[data-lid]");
        if (!r || readOnly) return;
        layerDrag = r.getAttribute("data-lid");
        e.dataTransfer.effectAllowed = "move";
        try {
          e.dataTransfer.setData("text/plain", layerDrag);
        } catch (_) {}
      });
      var dropPos = function (e, r) {
        var lid = r.getAttribute("data-lid");
        var rb = r.getBoundingClientRect();
        var k = (e.clientY - rb.top) / Math.max(1, rb.height);
        var container = lid.indexOf("g:") === 0 || r.classList.contains("is-frame");
        if (container && k > 0.25 && k < 0.75) return "inside";
        return k < 0.5 ? "before" : "after";
      };
      layersEl.addEventListener("dragover", function (e) {
        var r = e.target.closest("[data-lid]");
        if (!layerDrag || !r) return;
        e.preventDefault();
        var pos = dropPos(e, r);
        layersEl.querySelectorAll(".is-drop-before, .is-drop-after, .is-drop-inside").forEach(function (x) {
          x.classList.remove("is-drop-before", "is-drop-after", "is-drop-inside");
        });
        r.classList.add("is-drop-" + pos);
      });
      layersEl.addEventListener("drop", function (e) {
        var r = e.target.closest("[data-lid]");
        if (!layerDrag || !r) return;
        e.preventDefault();
        var from = layerDrag;
        layerDrag = null;
        dropLayer(from, r.getAttribute("data-lid"), dropPos(e, r));
        layersKey = "";
        renderLayers();
      });
      layersEl.addEventListener("dragend", function () {
        layerDrag = null;
        layersEl.querySelectorAll(".is-drop-before, .is-drop-after, .is-drop-inside").forEach(function (x) {
          x.classList.remove("is-drop-before", "is-drop-after", "is-drop-inside");
        });
      });
    }

    /** «+» у эффектов: тень, размытие слоя, размытие фона (каких ещё нет). */
    function openFxMenu(anchor) {
      var list = selList();
      var has = function (k) {
        return list.some(function (x) {
          return x.style && (k === "shadow" ? SHADOW_RE.test(String(x.style.shadow || "")) : Number(x.style[k]) > 0);
        });
      };
      var items = [["shadow", "Тень"], ["blur", "Размытие слоя"], ["bgBlur", "Размытие фона"]].filter(function (it) {
        return !has(it[0]);
      });
      openPop(anchor, function (box) {
        box.innerHTML = items.map(function (it) {
          return '<button type="button" class="lb-menu-item" data-fx="' + it[0] + '">' + it[1] + "</button>";
        }).join("");
        box.onclick = function (ev) {
          var m = ev.target.closest("[data-fx]");
          if (!m) return;
          closePop();
          var k = m.getAttribute("data-fx");
          var sx = {};
          if (k === "shadow") sx.shadow = "0 4 12 #00000040";
          else sx[k] = k === "blur" ? 4 : 16;
          applyStyle(function () {
            return true;
          }, sx);
        };
      });
    }

    /** Заливка и обводка из панели: добавить, убрать, скрыть/показать (значения при скрытии сохраняются). */
    function paintAct(a) {
      var list = selList();
      if (!list.length) return;
      var off = a === "fillEye" ? !common(list, function (x) {
        return !!(x.style && x.style.fillOff);
      }) : a === "strokeEye" ? !common(list, function (x) {
        return !!(x.style && x.style.strokeOff);
      }) : false;
      begin();
      list.forEach(function (x) {
        var sx;
        if (a === "fillAdd") sx = { fill: x.type === "frame" ? "#ffffff" : "#d9d9d9", fillOff: null, fillOpacity: null };
        else if (a === "fillDel") sx = { fill: "none", fillOff: null, fillOpacity: null };
        else if (a === "fillEye") sx = { fillOff: off || null };
        else if (a === "strokeAdd") sx = { stroke: "#000000", strokeWidth: 1, strokeOff: null, strokeOpacity: null, strokeAlign: ALIGNABLE_STROKE[x.type] ? "inside" : null };
        else if (a === "strokeDel") sx = { strokeWidth: 0, strokeOff: null, strokeOpacity: null };
        else sx = { strokeOff: off || null };
        if (a.indexOf("stroke") === 0 && (x.type === "line" || x.type === "pen") && a !== "strokeEye") return;
        patch(x.id, { style: sx });
      });
      commit();
    }

    /** Отражение (⇧H — по горизонтали, ⇧V — по вертикали): каждый объект и их места внутри выделения. */
    function flipSel(horiz) {
      var ids = withFrameKids(Array.from(selected));
      var list = ids.map(getEl).filter(Boolean);
      if (!list.length) return;
      var B = G.unionBounds(list.map(function (e) {
        return G.elBounds(e, getEl);
      }));
      if (!B) return;
      var ax = B.x + B.w / 2;
      var ay = B.y + B.h / 2;
      var mx = function (x) {
        return horiz ? 2 * ax - x : x;
      };
      var my = function (y) {
        return horiz ? y : 2 * ay - y;
      };
      begin();
      list.forEach(function (el) {
        var f = {};
        if (el.type === "line") {
          if ((el.start && el.start.el && !selected.has(el.start.el)) || (el.end && el.end.el && !selected.has(el.end.el))) return;
          var res = G.resolveLine(el, getEl);
          f.points = [[mx(res.p0.x), my(res.p0.y)], [mx(res.p1.x), my(res.p1.y)]];
          if (el.waypoints) f.waypoints = el.waypoints.map(function (q) {
            return [mx(q[0]), my(q[1])];
          });
          if (el.bend) f.bend = -el.bend;
          ["start", "end"].forEach(function (k) {
            var s0 = el[k];
            if (s0 && s0.side) f[k] = Object.assign({}, s0, { side: horiz ? { left: "right", right: "left" }[s0.side] || s0.side : { top: "bottom", bottom: "top" }[s0.side] || s0.side });
          });
          patch(el.id, f);
          return;
        }
        if (el.type === "pen") {
          f.points = (el.points || []).map(function (q) {
            return horiz ? [el.w - q[0], q[1]] : [q[0], el.h - q[1]];
          });
        }
        f.x = horiz ? 2 * ax - el.x - el.w : el.x;
        f.y = horiz ? el.y : 2 * ay - el.y - el.h;
        if (el.rot) f.rot = -el.rot;
        if (el.type === "vector" && el.paths) {
          var vb = el.vb || [el.w, el.h];
          f.paths = el.paths.map(function (p) {
            return Object.assign({}, p, { d: flipPathD(p.d, horiz, horiz ? vb[0] : vb[1]) });
          });
        }
        var st0 = el.style || {};
        if (st0.radii) {
          var rr = radiiOf(st0, el.w, el.h, 0);
          // [tl tr br bl]
          f.style = { radii: (horiz ? [rr[1], rr[0], rr[3], rr[2]] : [rr[3], rr[2], rr[1], rr[0]]).map(r1).join(" ") };
        }
        patch(el.id, f);
      });
      syncFrames();
      commit();
    }

    /** Отражённый контур: абсолютные x (или y) → size − x, относительные — со сменой знака (команды M L C Q Z). */
    function flipPathD(d, horiz, size) {
      var out = [];
      var re = /([MLCQZmlcqz])|(-?\d*\.?\d+(?:e[-+]?\d+)?)/g;
      var m;
      var cmd = "M";
      var idx = 0;
      while ((m = re.exec(String(d)))) {
        if (m[1]) {
          cmd = m[1];
          idx = 0;
          out.push(cmd);
          continue;
        }
        var v = Number(m[2]);
        var isX = idx % 2 === 0;
        if (isX === horiz) v = cmd === cmd.toUpperCase() ? size - v : -v;
        out.push(Math.round(v * 100) / 100);
        idx++;
      }
      return out.join(" ").replace(/ ([MLCQZmlcqz]) /g, "$1").replace(/^([MLCQZmlcqz]) /, "$1");
    }

    /** Выравнивание и равные промежутки: единица — группа или отдельный объект (экран — с содержимым). */
    function alignSel(kind) {
      var list = selList().filter(function (e) {
        return e.type !== "line";
      });
      if (!list.length) return;
      var units = {};
      list.forEach(function (e) {
        var k = e.group ? "g:" + e.group : e.id;
        (units[k] = units[k] || []).push(e.id);
      });
      var arr = Object.keys(units).map(function (k) {
        var b = G.unionBounds(units[k].map(function (id) {
          return G.elBounds(getEl(id), getEl);
        }));
        return { ids: units[k], b: b };
      }).filter(function (u) {
        return u.b;
      });
      if (!arr.length) return;
      var T;
      if (arr.length === 1) {
        var f = frameOf(getEl(arr[0].ids[0]));
        if (!f) return;
        T = { x: f.x, y: f.y, w: f.w, h: f.h };
      } else {
        T = G.unionBounds(arr.map(function (u) {
          return u.b;
        }));
      }
      begin();
      if (kind === "DH" || kind === "DV") {
        if (arr.length < 3) {
          commit();
          return;
        }
        var horiz = kind === "DH";
        arr.sort(function (a, c) {
          return horiz ? a.b.x - c.b.x : a.b.y - c.b.y;
        });
        var total = arr.reduce(function (sum, u) {
          return sum + (horiz ? u.b.w : u.b.h);
        }, 0);
        var gap = ((horiz ? T.w : T.h) - total) / (arr.length - 1);
        var pos = horiz ? T.x : T.y;
        arr.forEach(function (u) {
          var d = Math.round((pos - (horiz ? u.b.x : u.b.y)) * 100) / 100;
          if (d) moveBy(withFrameKids(u.ids), horiz ? d : 0, horiz ? 0 : d);
          pos += (horiz ? u.b.w : u.b.h) + gap;
        });
      } else {
        arr.forEach(function (u) {
          var b = u.b;
          var dx = kind === "L" ? T.x - b.x : kind === "R" ? T.x + T.w - (b.x + b.w) : kind === "CH" ? T.x + T.w / 2 - (b.x + b.w / 2) : 0;
          var dy = kind === "T" ? T.y - b.y : kind === "B" ? T.y + T.h - (b.y + b.h) : kind === "CV" ? T.y + T.h / 2 - (b.y + b.h / 2) : 0;
          dx = Math.round(dx * 100) / 100;
          dy = Math.round(dy * 100) / 100;
          if (dx || dy) moveBy(withFrameKids(u.ids), dx, dy);
        });
        // Один объект выровняли по краю фрейма — он и привязывается к этому краю (едет за ним при смене размера).
        var pf = arr.length === 1 ? frameOf(getEl(arr[0].ids[0])) : null;
        var pk = { L: ["h", "l"], CH: ["h", "c"], R: ["h", "r"], T: ["v", "t"], CV: ["v", "c"], B: ["v", "b"] }[kind];
        if (pf && !pf.layout && pk) {
          arr[0].ids.forEach(function (id) {
            var P = pinOf(getEl(id));
            P[pk[0]] = pk[1];
            patch(id, { pin: pinStr(P) });
          });
        }
      }
      syncFrames();
      commit();
    }

    /** Цвета этого макета — точные цвета, которые уже есть на доске (для единообразия). */
    function docColors() {
      var seen = {};
      var out = [];
      els.forEach(function (el) {
        var st = el.style || {};
        ["fill", "stroke", "textColor"].forEach(function (k) {
          var v = st[k];
          if (isHex(v) && !seen[v.toLowerCase()]) {
            seen[v.toLowerCase()] = 1;
            out.push(v.toLowerCase());
          }
        });
      });
      return out.slice(0, 24);
    }

    function openColorPop(anchor, c) {
      var cur = anchor.closest(".lb-icolor").querySelector("[data-hex]").value || "#000000";
      if (!/^#[0-9a-f]{6}$/i.test(cur)) cur = "#000000";
      var canGrad = c === "fill" || c === "stroke";
      var g0 = canGrad ? parseGrad(common(selList(), function (x) {
        return (x.style || {})[c + "Grad"] || "";
      })) : null;
      var state = { mode: g0 ? g0.type : "solid", g: g0, sel: 0 };
      var applyG = function () {
        applyInspector(c + "Grad", state.mode === "solid" ? null : gradStr(state.g));
      };
      openPop(anchor, function (box) {
        var draw = function () {
          var colors = docColors();
          var h = [];
          if (canGrad) {
            h.push('<div class="lb-gtabs">' + [["solid", "Сплошной"], ["linear", "Линейный"], ["radial", "Радиальный"]].map(function (t) {
              return '<button type="button" class="lb-gtab' + (state.mode === t[0] ? " is-on" : "") + '" data-gt="' + t[0] + '">' + t[1] + "</button>";
            }).join("") + "</div>");
          }
          if (state.mode === "solid") {
            h.push('<div class="lb-cpick"><input type="color" value="' + esc(cur) + '" data-native="1">' +
              (window.EyeDropper ? '<button type="button" class="lb-btn" data-eye="1" title="Пипетка: цвет с экрана">' + icon("eyedrop") + "</button>" : "") +
              (canGrad ? '<button type="button" class="lb-btn" data-none="1" title="Без цвета">⦸</button>' : "") + "</div>");
          } else {
            var g = state.g;
            var st0 = g.stops[state.sel] || g.stops[0];
            h.push('<div class="lb-gbar" style="--g:' + esc(gradCss(Object.assign({}, g, { type: "linear", angle: 0 }))) + '">' +
              g.stops.map(function (t, i) {
                return '<span class="lb-gstop' + (i === state.sel ? " is-on" : "") + '" data-i="' + i + '" style="left:' + t.p + "%;--c:" + t.c + '"></span>';
              }).join("") + "</div>" +
              '<div class="lb-cpick"><input type="color" value="' + esc(st0.c) + '" data-stopc="1">' +
              '<input type="text" class="lb-gin" data-stopa="1" value="' + Math.round(st0.a * 100) + '" title="Непрозрачность точки, %"><i class="lb-isuf">%</i>' +
              (g.type === "linear" ? '<input type="text" class="lb-gin" data-angle="1" value="' + g.angle + '" title="Угол, °"><i class="lb-isuf">°</i>' : "") +
              (g.stops.length > 2 ? '<button type="button" class="lb-btn" data-delstop="1" title="Убрать точку">' + icon("minus") + "</button>" : "") +
              "</div><div class=\"lb-ghint\">Клик по полосе — новая точка, точку можно тянуть</div>");
          }
          if (colors.length) h.push('<div class="lb-ilabel">Цвета макета</div><div class="lb-swatches">' + colors.map(function (k) {
            return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + k + '" style="--c:' + k + '"></button>';
          }).join("") + "</div>");
          box.innerHTML = h.join("");
          var nat = box.querySelector("[data-native]");
          if (nat) nat.addEventListener("input", function () {
            cur = nat.value.toLowerCase();
            applyInspector(c, cur);
          });
          var sc = box.querySelector("[data-stopc]");
          if (sc) sc.addEventListener("input", function () {
            state.g.stops[state.sel].c = sc.value.toLowerCase();
            applyG();
            refreshBar();
          });
          var sa = box.querySelector("[data-stopa]");
          if (sa) sa.addEventListener("change", function () {
            var v = parseNum(sa.value);
            if (v == null) return;
            state.g.stops[state.sel].a = Math.max(0, Math.min(100, v)) / 100;
            applyG();
            draw();
          });
          var an = box.querySelector("[data-angle]");
          if (an) an.addEventListener("change", function () {
            var v = parseNum(an.value);
            if (v == null) return;
            state.g.angle = ((Math.round(v) % 360) + 360) % 360;
            applyG();
          });
          var bar = box.querySelector(".lb-gbar");
          if (bar) bar.addEventListener("pointerdown", function (ev) {
            ev.preventDefault();
            var r = bar.getBoundingClientRect();
            var at = function (x) {
              return Math.max(0, Math.min(100, Math.round(((x - r.left) / Math.max(1, r.width)) * 100)));
            };
            var mk = ev.target.closest("[data-i]");
            if (mk) state.sel = Number(mk.getAttribute("data-i"));
            else {
              // Новая точка — цвет как у выбранной.
              var base = state.g.stops[state.sel] || state.g.stops[0];
              state.g.stops.push({ c: base.c, a: base.a, p: at(ev.clientX) });
              state.sel = state.g.stops.length - 1;
            }
            var stop = state.g.stops[state.sel];
            var move = function (e2) {
              stop.p = at(e2.clientX);
              refreshBar();
            };
            var up = function () {
              window.removeEventListener("pointermove", move);
              window.removeEventListener("pointerup", up);
              state.g.stops.sort(function (x, y) {
                return x.p - y.p;
              });
              state.sel = state.g.stops.indexOf(stop);
              applyG();
              draw();
            };
            window.addEventListener("pointermove", move);
            window.addEventListener("pointerup", up);
            refreshBar();
          });
        };
        var refreshBar = function () {
          var bar = box.querySelector(".lb-gbar");
          if (!bar) return;
          bar.style.setProperty("--g", gradCss(Object.assign({}, state.g, { type: "linear", angle: 0, stops: state.g.stops.slice().sort(function (x, y) {
            return x.p - y.p;
          }) })));
          bar.querySelectorAll("[data-i]").forEach(function (m) {
            var t = state.g.stops[Number(m.getAttribute("data-i"))];
            if (!t) return;
            m.style.left = t.p + "%";
            m.style.setProperty("--c", t.c);
            m.classList.toggle("is-on", Number(m.getAttribute("data-i")) === state.sel);
          });
        };
        draw();
        box.onclick = function (ev) {
          var gt = ev.target.closest("[data-gt]");
          if (gt) {
            var mode2 = gt.getAttribute("data-gt");
            if (mode2 === state.mode) return;
            state.mode = mode2;
            if (mode2 !== "solid") {
              if (!state.g) state.g = { type: mode2, angle: 0, stops: [{ c: cur, a: 1, p: 0 }, { c: cur, a: 0, p: 100 }] };
              state.g.type = mode2;
              state.sel = 0;
            }
            applyG();
            draw();
            return;
          }
          var sw = ev.target.closest("[data-v]");
          if (sw) {
            if (state.mode === "solid") {
              applyInspector(c, sw.getAttribute("data-v"));
              closePop();
            } else {
              state.g.stops[state.sel].c = sw.getAttribute("data-v");
              applyG();
              draw();
            }
            return;
          }
          if (ev.target.closest("[data-delstop]") && state.g && state.g.stops.length > 2) {
            state.g.stops.splice(state.sel, 1);
            state.sel = 0;
            applyG();
            draw();
            return;
          }
          if (ev.target.closest("[data-none]")) {
            applyInspector(c, "none");
            closePop();
            return;
          }
          if (ev.target.closest("[data-eye]")) {
            closePop();
            new window.EyeDropper().open().then(function (r) {
              if (r && r.sRGBHex) applyInspector(c, String(r.sRGBHex).toLowerCase().slice(0, 7));
            }).catch(function () {});
          }
        };
      });
    }

    if (inspector) {
      inspector.addEventListener("pointerdown", function (e) {
        e.stopPropagation();
        inspDown = { pending: false };
      });
      inspUp = function () {
        if (!inspDown) return;
        var was = inspDown;
        // После click и фокуса поля: перерисовка, отложенная нажатием, — с возвратом фокуса и курсора.
        setTimeout(function () {
          if (inspDown !== was) return;
          inspDown = null;
          if (!was.pending) return;
          var ae = document.activeElement;
          var k = ae && inspector.contains(ae) ? ae.getAttribute("data-k") || ae.getAttribute("data-hex") : "";
          var sel = k && ae.selectionStart != null ? [ae.selectionStart, ae.selectionEnd] : null;
          var val = k ? ae.value : "";
          if (k) ae.blur();
          inspKey = "";
          renderInspector(selList());
          var again = k ? inspector.querySelector('[data-k="' + k + '"], [data-hex="' + k + '"]') : null;
          if (again) {
            again.value = val;
            again.focus({ preventScroll: true });
            if (sel) again.setSelectionRange(sel[0], sel[1]);
          }
        }, 0);
      };
      document.addEventListener("pointerup", inspUp, true);
      document.addEventListener("pointercancel", inspUp, true);
      inspector.addEventListener("keydown", function (e) {
        e.stopPropagation();
        if (e.key === "Enter" && e.target.tagName === "INPUT") e.target.blur();
        if (e.key === "Escape") {
          inspKey = "";
          e.target.blur();
          stage.focus({ preventScroll: true });
        }
        // ↑/↓ в числовом поле — ±1 (с Shift — ±10), как во Figma.
        if ((e.key === "ArrowUp" || e.key === "ArrowDown") && e.target.getAttribute("data-k")) {
          var v = parseNum(e.target.value);
          if (v == null) return;
          e.preventDefault();
          e.target.value = r1(v + (e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 10 : 1));
          applyInspector(e.target.getAttribute("data-k"), e.target.value);
        }
      });
      inspector.addEventListener("change", function (e) {
        var t = e.target;
        if (t.getAttribute("data-exp")) {
          if (t.getAttribute("data-exp") === "fmt") expFmt = t.value;
          else expScale = Number(t.value) || 1;
          return;
        }
        if (t.getAttribute("data-k")) applyInspector(t.getAttribute("data-k"), t.type === "checkbox" ? t.checked : t.value);
        else if (t.getAttribute("data-hex")) {
          var v = String(t.value).trim().toLowerCase();
          if (/^[0-9a-f]{6}$/.test(v)) v = "#" + v;
          if (/^#[0-9a-f]{3}$/.test(v)) v = "#" + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
          if (/^#[0-9a-f]{6}$/.test(v)) applyInspector(t.getAttribute("data-hex"), v);
          else if (!v && (t.getAttribute("data-hex") === "fill" || t.getAttribute("data-hex") === "stroke")) applyInspector(t.getAttribute("data-hex"), "none");
        }
      });
      inspector.addEventListener("focusout", function () {
        setTimeout(function () {
          if (!inspector.contains(document.activeElement)) {
            inspKey = "";
            renderInspector(selList());
          }
        }, 0);
      });
      inspector.addEventListener("click", function (e) {
        var tab = e.target.closest("[data-tab]");
        if (tab) {
          dev.tab = tab.getAttribute("data-tab") === "code" ? "code" : "design";
          try {
            localStorage.setItem("leo-board-insp-tab", dev.tab);
          } catch (_) {}
          inspKey = "";
          renderInspector(selList());
          return;
        }
        var dv = e.target.closest("[data-act^='dev']");
        if (dv) {
          devAct(dv.getAttribute("data-act"));
          return;
        }
        var pick = e.target.closest("[data-pick]");
        if (pick) {
          openColorPop(pick, pick.getAttribute("data-pick"));
          return;
        }
        var ag = e.target.closest("[data-align]");
        if (ag) {
          alignSel(ag.getAttribute("data-align"));
          return;
        }
        var al = e.target.closest("[data-al]");
        if (al) {
          applyStyle(isAlignable, { align: al.getAttribute("data-al") });
          return;
        }
        var tb = e.target.closest("[data-va], [data-deco], [data-tcase]");
        if (tb) {
          var cur0 = function (k) {
            return common(selList(), function (x) {
              return (x.style || {})[k] || "";
            });
          };
          if (tb.hasAttribute("data-va")) applyStyle(isTextual, { valign: tb.getAttribute("data-va") === "middle" ? null : tb.getAttribute("data-va") });
          else if (tb.hasAttribute("data-deco")) applyStyle(isTextual, { deco: cur0("deco") === tb.getAttribute("data-deco") ? null : tb.getAttribute("data-deco") });
          else applyStyle(isTextual, { textCase: cur0("textCase") === tb.getAttribute("data-tcase") ? null : tb.getAttribute("data-tcase") });
          return;
        }
        var b = e.target.closest("[data-act]");
        if (!b) return;
        var a = b.getAttribute("data-act");
        if (a === "radii") {
          begin();
          selList().forEach(function (x) {
            var st0 = x.style || {};
            var rr = radiiOf(st0, x.w, x.h, x.type === "frame" || mode === "design" ? 0 : undefined).map(r1);
            patch(x.id, { style: st0.radii ? { radii: null, radius: rr[0] } : { radii: rr.join(" ") } });
          });
          commit();
        } else if (a === "fillAdd" || a === "fillDel" || a === "fillEye" || a === "strokeAdd" || a === "strokeDel" || a === "strokeEye") {
          paintAct(a);
        } else if (a === "flipH" || a === "flipV") flipSel(a === "flipH");
        else if (a === "rot90") {
          var r0 = selList()[0];
          if (r0) applyInspector("rot", String((r0.rot || 0) + 90));
        }
        else if (a === "group") groupSel();
        else if (a === "fxAdd") openFxMenu(b);
        else if (/^(alAdd|alDel|alDir|alCell|alBetween|alWrap|hugW|hugH|padSplit)$/.test(a)) layoutAct(a, b);
        else if (a === "ratio") applyFields(function () {
          return true;
        }, function (x) {
          return { ratio: x.ratio ? undefined : true };
        });
        else if (a === "export") exportSelection(expFmt, expScale);
        else if (a === "copyFigma") copyForFigma();
        else if (a === "link") {
          var lk = selList()[0];
          if (lk) o.blockActions.link([{ id: lk.id, text: lk.type === "frame" ? lk.name || "Фрейм" : lk.text || "", label: lk.name || lk.text || "Объект макета" }]);
        }
        else if (a === "blurOff" || a === "bgBlurOff") applyStyle(function () {
          return true;
        }, a === "blurOff" ? { blur: null } : { bgBlur: null });
        else if (a === "ungroup") ungroupSel();
        else if (a === "dup") duplicateSel();
        else if (a === "front") reorderSel(true);
        else if (a === "back") reorderSel(false);
        else if (a === "delete") deleteSel();
        else if (a === "bold") applyStyle(isTextual, { bold: !common(selList(), function (x) {
          return !!(x.style && x.style.bold);
        }) || null });
        else if (a === "shadow") {
          var has = selList().some(function (x) {
            return SHADOW_RE.test(String((x.style || {}).shadow || ""));
          });
          applyStyle(function () {
            return true;
          }, { shadow: has ? null : "0 4 12 #00000040" });
        } else if (a === "device") {
          var fakeAnchor = b;
          openPop(fakeAnchor, function (box) {
            box.innerHTML = DEVICES.map(function (d) {
              return '<button type="button" class="lb-menu-item" data-dev="' + d.id + '">' + esc(d.title) + "<kbd>" + d.w + " × " + d.h + "</kbd></button>";
            }).join("");
            box.onclick = function (ev) {
              var m = ev.target.closest("[data-dev]");
              if (!m) return;
              var dev = DEVICES.filter(function (d) {
                return d.id === m.getAttribute("data-dev");
              })[0];
              closePop();
              begin();
              applyFields(isType("frame"), function () {
                return { w: dev.w, h: dev.h, device: dev.id };
              });
              syncFrames();
              commit();
            };
          });
        }
      });
    }

    function positionProps() {
      var b = selBounds();
      if (!b) return;
      var r = stage.getBoundingClientRect();
      var a = toScreen(G.pt(b.x, b.y));
      var c = toScreen(G.pt(b.x + b.w, b.y + b.h));
      var pw = propsBar.offsetWidth || 300;
      var ph = propsBar.offsetHeight || 40;
      var x = clamp((a.x + c.x) / 2 - pw / 2, 8, Math.max(8, r.width - pw - 8));
      var single = selList().length === 1 && BOXED[selList()[0].type];
      var y = a.y - ph - (single ? 56 : 14);
      if (y < 8) y = c.y + 40;
      if (y + ph > r.height - 8) y = Math.max(8, r.height - ph - 64);
      propsBar.style.left = x + "px";
      propsBar.style.top = y + "px";
    }

    function isType(t) {
      return function (e) {
        return e.type === t;
      };
    }

    function isStroked(e) {
      return !!STROKED[e.type];
    }

    function isShape(e) {
      return !!SHAPES[e.type];
    }

    /** Заливку меняют фигуры и векторы (у вектора — всех контуров с заливкой). */
    function isPaintable(e) {
      return !!SHAPES[e.type] || e.type === "vector" || e.type === "frame";
    }

    function isTextual(e) {
      return !!TEXTUAL[e.type];
    }

    function swatchBtn(p, color, title, ring, letter) {
      return (
        '<button type="button" class="lb-btn lb-pbtn" data-p="' + p + '" title="' + esc(title) + '">' +
        (letter
          ? '<span class="lb-swatch-a" style="color:' + color + '">A</span>'
          : '<span class="lb-swatch' + (ring ? " is-ring" : "") + (color === "none" ? " is-none" : "") + '" style="--c:' + color + '"></span>') +
        "</button>"
      );
    }

    function widthIcon(w, dash) {
      return (
        '<svg viewBox="0 0 20 20" width="20" height="20"><path d="M3 10h14" stroke="currentColor" stroke-width="' + Math.min(6, w) + '" stroke-linecap="round"' +
        (dash ? ' stroke-dasharray="' + dashArray(dash, Math.min(3, w)) + '"' : "") + "/></svg>"
      );
    }

    function applyStyle(filter, fields) {
      var list = selList().filter(filter);
      if (!list.length) return;
      begin();
      list.forEach(function (el) {
        patch(el.id, { style: fields });
        lastStyle[mode + ":" + el.type] = Object.assign({}, lastStyle[mode + ":" + el.type] || {}, fields);
      });
      commit();
    }

    function applyFields(filter, fieldsFn) {
      var list = selList().filter(filter);
      if (!list.length) return;
      begin();
      list.forEach(function (el) {
        patch(el.id, fieldsFn(el));
      });
      commit();
    }

    function onPropsClick(e) {
      var b = e.target.closest("[data-p]");
      if (!b) return;
      var p = b.getAttribute("data-p");
      if (p === "stroke" || p === "textColor") {
        openPop(b, function (box) {
          // У фигур обводку можно убрать (как «Без заливки»); у линий и рисунков — нет.
          var canOff = p === "stroke" && !selList().some(function (x) {
            return x.type === "line" || x.type === "pen";
          });
          box.innerHTML = '<div class="lb-swatches">' + (canOff ? '<button type="button" class="lb-sw is-none" data-v="none" title="Без обводки" style="--c:none"></button>' : "") +
            INK.map(function (k) {
              return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + inkVar(k) + '"></button>';
            }).join("") + "</div>";
          box.onclick = function (ev) {
            var s = ev.target.closest("[data-v]");
            if (!s) return;
            var v = s.getAttribute("data-v");
            if (p !== "stroke") {
              applyStyle(isType("text"), { textColor: v });
            } else if (v === "none") {
              applyStyle(isStroked, { strokeOff: true });
            } else {
              // Цвет возвращает обводку, если её убирали (и толщину, если была 0).
              applyFields(isStroked, function (x) {
                var st0 = x.style || {};
                return { style: { stroke: v, strokeOff: null, strokeWidth: st0.strokeWidth === 0 ? 2 : st0.strokeWidth } };
              });
            }
            closePop();
          };
        });
      } else if (p === "fill") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + FILLS.map(function (k) {
            return '<button type="button" class="lb-sw' + (k === "none" ? " is-none" : "") + '" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + fillVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s = ev.target.closest("[data-v]");
            if (!s) return;
            applyStyle(isPaintable, { fill: s.getAttribute("data-v") });
            closePop();
          };
        });
      } else if (p === "sticky") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + STICKY.map(function (k) {
            return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + stickyVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s = ev.target.closest("[data-v]");
            if (!s) return;
            applyStyle(isType("sticky"), { fill: s.getAttribute("data-v") });
            closePop();
          };
        });
      } else if (p === "width") {
        openPop(b, function (box) {
          box.innerHTML =
            '<div class="lb-row">' + WIDTHS.map(function (w) {
              return '<button type="button" class="lb-btn" data-w="' + w + '" title="' + w + ' px">' + widthIcon(w) + "</button>";
            }).join("") + "</div>" +
            '<div class="lb-row">' + [["solid", "Сплошная"], ["dashed", "Пунктир"], ["dotted", "Точки"]].map(function (d) {
              return '<button type="button" class="lb-btn" data-d="' + d[0] + '" title="' + d[1] + '">' + widthIcon(2, d[0] === "solid" ? "" : d[0]) + "</button>";
            }).join("") + "</div>";
          box.onclick = function (ev) {
            var w = ev.target.closest("[data-w]");
            var d = ev.target.closest("[data-d]");
            if (w) applyStyle(isStroked, { strokeWidth: Number(w.getAttribute("data-w")) });
            if (d) applyStyle(isStroked, { dash: d.getAttribute("data-d") === "solid" ? null : d.getAttribute("data-d") });
          };
        });
      } else if (p === "font") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + FONT_SIZES.map(function (f) {
            return '<button type="button" class="lb-btn lb-font" data-f="' + f.v + '">' + f.t + "</button>";
          }).join("") + '<button type="button" class="lb-btn lb-font" data-b="1" title="Жирный"><b>B</b></button></div>';
          box.onclick = function (ev) {
            var f = ev.target.closest("[data-f]");
            var bb = ev.target.closest("[data-b]");
            if (f) applyStyle(isTextual, { fontSize: Number(f.getAttribute("data-f")) });
            if (bb) {
              var bold = !commonStyle(selList().filter(isTextual), "bold");
              applyStyle(isTextual, { bold: bold || null });
            }
          };
        });
      } else if (p === "align") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + [["left", "По левому краю"], ["center", "По центру"], ["right", "По правому краю"]].map(function (a) {
            return '<button type="button" class="lb-btn" data-a="' + a[0] + '" title="' + a[1] + '">' + icon(ALIGN_ICON[a[0]]) + "</button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var a = ev.target.closest("[data-a]");
            if (!a) return;
            applyStyle(isAlignable, { align: a.getAttribute("data-a") });
            closePop();
          };
        });
      } else if (p === "shape") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-shapes">' + SHAPE_MENU.map(function (t) {
            return '<button type="button" class="lb-btn" data-t="' + t + '" title="' + esc(SHAPE_RU[t]) + '">' + icon(t) + "</button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var t = ev.target.closest("[data-t]");
            if (!t) return;
            var tt = t.getAttribute("data-t");
            applyFields(isShape, function () {
              if (tt === "rectSharp") return { type: "rect", style: { radius: 0, radii: null } };
              if (tt === "rect") return { type: "rect", style: { radius: null, radii: null } };
              return { type: tt };
            });
            closePop();
          };
        });
      } else if (p === "arrowStart" || p === "arrowEnd") {
        var on = !b.classList.contains("is-on");
        applyFields(isType("line"), function () {
          var f = {};
          f[p] = on || undefined;
          return f;
        });
      } else if (p === "noteacts") {
        var avail = blockActsFor(selList());
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-nacts">' + avail.map(function (a) {
            return '<button type="button" class="lb-nact' + a.cls + '" data-act="' + a.id + '" title="' + (a.id === "link" ? "Скопировать ссылку на блок" : a.label) + '">' +
              a.svg + "<span>" + a.label + "</span></button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var btn = ev.target.closest("[data-act]");
            if (!btn) return;
            var actId = btn.getAttribute("data-act");
            var fn = o.blockActions && o.blockActions[actId];
            closePop();
            if (editing) finishEdit();
            if (!fn) return;
            if (actId === "ai" && mode === "design") {
              // «ИИ» на макете переписывает текстовые слои выделенного (у фрейма — все тексты внутри).
              var seen = {};
              var items = [];
              selList().forEach(function (el) {
                textLayersOf(el).forEach(function (x) {
                  if (seen[x.id] || items.length >= 40) return;
                  seen[x.id] = 1;
                  items.push({ id: x.id, type: x.type, text: String(x.text || "") });
                });
              });
              fn(items);
            } else fn(selectionInfo());
          };
        });
      } else if (p === "unbend") {
        applyFields(isType("line"), function () {
          return { waypoints: undefined, bend: undefined };
        });
      } else if (p === "route") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-row">' + [["straight", "Прямая"], ["curve", "Кривая"], ["elbow", "Ломаная"]].map(function (r) {
            return '<button type="button" class="lb-btn" data-r="' + r[0] + '" title="' + r[1] + '">' + icon(r[0]) + "</button>";
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var r = ev.target.closest("[data-r]");
            if (!r) return;
            var route = r.getAttribute("data-r");
            applyFields(isType("line"), function () {
              return { route: route, bend: 0 };
            });
            lastStyle.lineRoute = route;
            closePop();
          };
        });
      } else if (p === "device") {
        openPop(b, function (box) {
          box.innerHTML = DEVICES.map(function (d) {
            return '<button type="button" class="lb-menu-item" data-dev="' + d.id + '">' + esc(d.title) + "<kbd>" + d.w + " × " + d.h + "</kbd></button>";
          }).join("");
          box.onclick = function (ev) {
            var m = ev.target.closest("[data-dev]");
            if (!m) return;
            var dev = DEVICES.filter(function (d) {
              return d.id === m.getAttribute("data-dev");
            })[0];
            closePop();
            begin();
            applyFields(isType("frame"), function () {
              return { w: dev.w, h: dev.h, device: dev.id };
            });
            syncFrames();
            commit();
          };
        });
      } else if (p === "cardFill") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + FILLS.filter(function (k) {
            return k !== "none";
          }).map(function (k) {
            return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + fillVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s2 = ev.target.closest("[data-v]");
            if (!s2) return;
            applyStyle(isType("card"), { fill: s2.getAttribute("data-v") });
            closePop();
          };
        });
      } else if (p === "photo") {
        pickCardPhoto(selList()[0].id);
      } else if (p === "nophoto") {
        applyFields(isType("card"), function () {
          return { photo: undefined };
        });
      } else if (p === "child") {
        addSubordinate(selList()[0].id);
      } else if (p === "gadd") {
        addTask(selList()[0].id);
      } else if (p === "gdel") {
        if (gSel) {
          var gid = gSel.id;
          var gtask = gSel.task;
          var gel = getEl(gid);
          gSel = null;
          if (gel) {
            begin();
            setTasks(gid, (gel.tasks || []).filter(function (t) {
              return t.id !== gtask;
            }));
            commit();
          }
        }
      } else if (p === "gunit") {
        var unit = b.getAttribute("data-v");
        applyFields(isType("gantt"), function (el) {
          return BL.ganttFit(Object.assign({}, el, { unit: unit, start: unit === "week" ? BL.weekStart(el.start) : el.start }));
        });
      } else if (p === "gcolor") {
        openPop(b, function (box) {
          box.innerHTML = '<div class="lb-swatches">' + BL.BAR_COLORS.map(function (k) {
            return '<button type="button" class="lb-sw" data-v="' + k + '" title="' + esc(COLOR_NAMES[k] || k) + '" style="--c:' + inkVar(k) + '"></button>';
          }).join("") + "</div>";
          box.onclick = function (ev) {
            var s3 = ev.target.closest("[data-v]");
            if (!s3 || !gSel) return;
            var color = s3.getAttribute("data-v");
            begin();
            patchTask(gSel.id, gSel.task, function (t) {
              t.color = color;
              return t;
            });
            commit();
            closePop();
          };
        });
      } else if (p === "group") groupSel();
      else if (p === "ungroup") ungroupSel();
      else if (p === "delete") deleteSel();
      else if (p === "more") {
        openPop(b, function (box) {
          box.innerHTML =
            '<button type="button" class="lb-menu-item" data-m="dup">' + icon("copy") + "Дублировать<kbd>⌘D</kbd></button>" +
            '<button type="button" class="lb-menu-item" data-m="front">' + icon("front") + "На передний план<kbd>]</kbd></button>" +
            '<button type="button" class="lb-menu-item" data-m="back">' + icon("back") + "На задний план<kbd>[</kbd></button>";
          box.onclick = function (ev) {
            var m = ev.target.closest("[data-m]");
            if (!m) return;
            var a = m.getAttribute("data-m");
            if (a === "dup") duplicateSel();
            else if (a === "front") reorderSel(true);
            else reorderSel(false);
            closePop();
          };
        });
      }
    }

    function openPop(anchor, build) {
      if (!pop.classList.contains("hidden") && pop._anchor === anchor) {
        closePop();
        return;
      }
      pop.innerHTML = "";
      pop.onclick = null;
      var box = pop;
      build(box);
      pop._anchor = anchor;
      pop.classList.remove("hidden");
      var r = root.getBoundingClientRect();
      var a = anchor.getBoundingClientRect();
      var pw = pop.offsetWidth;
      var ph = pop.offsetHeight;
      var x = clamp(a.left - r.left + a.width / 2 - pw / 2, 8, Math.max(8, r.width - pw - 8));
      var y = a.bottom - r.top + 6;
      if (y + ph > r.height - 8) y = a.top - r.top - ph - 6;
      pop.style.left = x + "px";
      pop.style.top = Math.max(8, y) + "px";
    }

    function closePop() {
      pop.classList.add("hidden");
      pop._anchor = null;
      pop.onclick = null;
    }

    // ---- действия с выделением ----
    function deleteSel() {
      var ids = withFrameKids(Array.from(selected));
      if (!ids.length) return;
      var gone = {};
      ids.forEach(function (id) {
        gone[id] = 1;
      });
      begin();
      // Стрелки, прилипшие к удаляемым блокам, удаляются вместе с ними.
      els.forEach(function (el) {
        if (el.type === "line" && ((el.start && gone[el.start.el]) || (el.end && gone[el.end.el]))) gone[el.id] = 1;
      });
      Object.keys(gone).forEach(remove);
      commit();
      selected.clear();
      closePop();
      schedule();
    }

    function copyEls(list, dx, dy) {
      var map = {};
      var groups = {};
      list.forEach(function (el) {
        map[el.id] = uid();
      });
      var z = topZ();
      var out = list.slice().sort(function (a, b) {
        return (a.z || 0) - (b.z || 0);
      }).map(function (el, i) {
        var c = clone(el);
        c.id = map[el.id];
        c.z = z + i * 0.001;
        c.x = (c.x || 0) + dx;
        c.y = (c.y || 0) + dy;
        if (c.group) {
          if (!groups[c.group]) groups[c.group] = uid();
          c.group = groups[c.group];
        }
        if (c.frame && map[c.frame]) c.frame = map[c.frame];
        if (c.type === "line") {
          var res = G.resolveLine(el, function (id) {
            return getEl(id) || null;
          });
          c.points = [[res.p0.x + dx, res.p0.y + dy], [res.p1.x + dx, res.p1.y + dy]];
          if (c.waypoints) c.waypoints = shiftWaypoints(c.waypoints, dx, dy);
          ["start", "end"].forEach(function (k) {
            if (c[k] && !map[c[k].el]) delete c[k];
            else if (c[k]) c[k] = Object.assign({}, c[k], { el: map[c[k].el] });
          });
        }
        return c;
      });
      return out;
    }

    function duplicateSel() {
      var list = withFrameKids(Array.from(selected)).map(getEl).filter(Boolean);
      if (!list.length) return;
      // Экран копируется рядом справа, остальное — со сдвигом.
      var onlyFrames = list.filter(function (e) {
        return e.type === "frame" && selected.has(e.id);
      });
      var dxDup = onlyFrames.length === 1 && selected.size === 1 ? onlyFrames[0].w + 80 : 24;
      var copies = copyEls(list, dxDup, dxDup === 24 ? 24 : 0);
      begin();
      copies.forEach(put);
      syncFrames();
      commit();
      setSelection(copies.map(function (c) {
        return c.id;
      }));
    }

    function reorderSel(front) {
      var list = selList().sort(function (a, b) {
        return (a.z || 0) - (b.z || 0);
      });
      if (!list.length) return;
      var base = front ? topZ() : bottomZ() - list.length * 0.001;
      begin();
      list.forEach(function (el, i) {
        patch(el.id, { z: base + i * 0.001 });
      });
      commit();
    }

    function groupSel() {
      var list = selList();
      if (list.length < 2) return;
      var g = uid();
      begin();
      list.forEach(function (el) {
        patch(el.id, { group: g });
      });
      commit();
    }

    function ungroupSel() {
      begin();
      selList().forEach(function (el) {
        if (el.group) patch(el.id, { group: undefined });
      });
      commit();
    }

    function nudge(dx, dy) {
      if (!selected.size) return;
      if (reorderInLayout(dx, dy)) return;
      begin();
      moveBy(withFrameKids(Array.from(selected)), dx, dy);
      syncFrames();
      commit();
      if (measure && lastMove) updateMeasure({ clientX: lastMove.clientX, clientY: lastMove.clientY, altKey: true });
    }

    /** Сдвиг набора элементов (экран — вместе с содержимым); стрелки между ними остаются прилипшими. */
    function moveBy(ids, dx, dy) {
      var moving = {};
      ids.forEach(function (id) {
        moving[id] = 1;
      });
      ids.forEach(function (id) {
        var el = getEl(id);
        if (el) patch(id, moved(el, dx, dy, moving));
      });
    }

    function shiftWaypoints(wps, dx, dy) {
      return (wps || []).map(function (q) {
        return [Math.round((q[0] + dx) * 10) / 10, Math.round((q[1] + dy) * 10) / 10];
      });
    }

    /** Сдвиг элемента; у линии сдвигаются свободные концы, прилипшие к неподвижным блокам — отлипают. */
    function moved(el0, dx, dy, movingIds) {
      if (el0.type === "line") {
        var res = G.resolveLine(el0, getEl);
        var f = { points: [[res.p0.x + dx, res.p0.y + dy], [res.p1.x + dx, res.p1.y + dy]] };
        ["start", "end"].forEach(function (k) {
          if (el0[k] && !(movingIds && movingIds[el0[k].el])) f[k] = undefined;
        });
        if (el0.waypoints && el0.waypoints.length) f.waypoints = shiftWaypoints(el0.waypoints, dx, dy);
        return f;
      }
      return { x: el0.x + dx, y: el0.y + dy };
    }

    // ---- буфер обмена ----
    function onCopy(e) {
      if (editing || !selected.size) return;
      var list = withFrameKids(Array.from(selected)).map(getEl).filter(Boolean);
      var payload = JSON.stringify({ leoBoard: 1, elements: list.map(function (el) {
        return serializeEl(el, getEl, pathOptsOf);
      }) });
      var text = list.map(function (el) {
        return elText(el);
      }).filter(Boolean).join("\n");
      try {
        e.clipboardData.setData(CLIP_MIME, payload);
        e.clipboardData.setData("text/plain", text || " ");
        e.clipboardData.setData("text/html", '<span data-leo-board="' + esc(payload) + '">' + esc(text) + "</span>");
        e.preventDefault();
      } catch (_) {}
      return true;
    }

    function onCut(e) {
      if (readOnly || editing || !selected.size) return;
      if (onCopy(e)) deleteSel();
    }

    /** Куда вставлять (как во Figma): выделен фрейм — внутрь него; выделено что-то во фрейме — рядом,
     * в тот же фрейм (в автолэйауте — сразу после выделенного). Копию самого фрейма — рядом с ним. */
    function pasteInto(src, copies, tops) {
      var sel = selList();
      if (!sel.length || !tops.length) return;
      var srcIds = {};
      src.forEach(function (el) {
        srcIds[el.id] = 1;
      });
      var parent = null;
      var after = [];
      if (sel.length === 1 && sel[0].type === "frame" && !srcIds[sel[0].id]) parent = sel[0];
      else {
        parent = frameOf(sel[0]);
        if (!parent || !sel.every(function (e) {
          return frameOf(e) === parent;
        })) return;
        after = sel.map(function (e) {
          return e.id;
        });
      }
      var b = G.unionBounds(tops.map(function (x) {
        return G.elBounds(getEl(x.id), getEl);
      }));
      if (!b) return;
      var all = withFrameKids(tops.map(function (x) {
        return x.id;
      }));
      var L = parseLayout(parent.layout);
      if (L) {
        // Автолэйаут: в начало области, порядок — сразу за выделенным (или в конец); раскладка — тем же шагом.
        moveBy(all, r1(parent.x + L.pad[3] - b.x), r1(parent.y + L.pad[0] - b.y));
      } else {
        // Обычный фрейм: на прежнее место, если оно внутри, иначе — по центру фрейма.
        var src0 = G.unionBounds(src.filter(function (el) {
          return !el.frame || !srcIds[el.frame];
        }).map(function (el) {
          return G.elBounds(el, function (id) {
            return getEl(id) || src.find(function (q) {
              return q.id === id;
            }) || null;
          });
        }).filter(Boolean));
        var inside = src0 && src0.x >= parent.x && src0.y >= parent.y && src0.x + src0.w <= parent.x + parent.w && src0.y + src0.h <= parent.y + parent.h;
        var tx0 = inside ? src0.x : parent.x + (parent.w - b.w) / 2;
        var ty0 = inside ? src0.y : parent.y + (parent.h - b.h) / 2;
        moveBy(all, r1(tx0 - b.x), r1(ty0 - b.y));
      }
      tops.forEach(function (x) {
        if (x.type !== "line" || !(x.start || x.end)) patch(x.id, { frame: parent.id });
      });
      if (L) {
        var H = L.dir === "h";
        var flow = L.dir === "g" || (H && L.wrap);
        var mine = {};
        tops.forEach(function (x) {
          mine[x.id] = 1;
        });
        var kids = layoutKids(parent).filter(function (k) {
          return !mine[k.id];
        }).sort(function (a, c) {
          if (flow) {
            var d = a.y + a.h / 2 - (c.y + c.h / 2);
            return Math.abs(d) > Math.min(a.h, c.h) / 2 ? d : a.x - c.x;
          }
          return H ? a.x - c.x : a.y - c.y;
        }).map(function (k) {
          return k.id;
        });
        var last = Math.max.apply(null, [-1].concat(after.map(function (id) {
          return kids.indexOf(id);
        })));
        var at = last >= 0 ? last + 1 : kids.length;
        var newIds = tops.filter(function (x) {
          return getEl(x.id) && getEl(x.id).frame === parent.id;
        }).map(function (x) {
          return x.id;
        });
        applyLayout(parent.id, kids.slice(0, at).concat(newIds, kids.slice(at)));
      }
    }

    function onPaste(e) {
      if (readOnly || editing) return;
      var cd = e.clipboardData;
      if (!cd) return;
      var raw = cd.getData(CLIP_MIME);
      if (!raw) {
        var html = cd.getData("text/html") || "";
        var m = html.match(/data-leo-board="([^"]*)"/);
        if (m) {
          var ta = document.createElement("textarea");
          ta.innerHTML = m[1];
          raw = ta.value;
        }
      }
      var files = Array.prototype.slice.call(cd.files || []).filter(function (f) {
        return /^image\//i.test(f.type || "");
      });
      e.preventDefault();
      if (raw) {
        try {
          var data = JSON.parse(raw);
          var list = parseBoard({ elements: data.elements || [] });
          if (list.length) {
            var tmp = new Map();
            list.forEach(function (el) {
              tmp.set(el.id, el);
            });
            var b = G.unionBounds(list.map(function (el) {
              return G.elBounds(el, function (id) {
                return tmp.get(id) || null;
              });
            }));
            var r = stageRect();
            var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
            var dx = b ? c.x - (b.x + b.w / 2) : 0;
            var dy = b ? c.y - (b.y + b.h / 2) : 0;
            // Вставка рядом с исходником — со сдвигом, если исходник на экране.
            var saved = getEl;
            var copies;
            getEl = function (id) {
              return tmp.get(id) || null;
            };
            try {
              copies = copyEls(list, Math.round(dx), Math.round(dy));
            } finally {
              getEl = saved;
            }
            var tops = copies.filter(function (x) {
              return !x.frame || !copies.some(function (f) {
                return f.id === x.frame;
              });
            });
            begin();
            copies.forEach(put);
            syncFrames();
            pasteInto(list, copies, tops);
            commit();
            setSelection(tops.map(function (x) {
              return x.id;
            }));
            return;
          }
        } catch (_) {}
      }
      // Выделена заглушка картинки и в буфере есть картинка — она и вставляется (на место заглушки).
      var one = selList().length === 1 ? selList()[0] : null;
      if (files.length && one && one.type === "rect" && /^Картинка из (Miro|Figma)/.test(String(one.text || one.name || ""))) {
        insertImageFile(files[0]);
        return;
      }
      // Схема из FigJam (webapp/board-figma.js): разбор асинхронный; не разобрали — текст как раньше.
      var htmlAll = cd.getData("text/html") || "";
      var plain = cd.getData("text/plain") || "";
      // «Copy as SVG» из Figma и любой SVG-текст — вектором, который правится по точкам.
      var svgEl = /^\s*(<\?xml[^>]*>\s*)?<svg[\s>]/i.test(plain) ? svgToVector(plain) : null;
      if (svgEl) {
        placeImported([svgEl]);
        notify("Вставлено из SVG: вектор — двойной клик, чтобы править точки");
        return;
      }
      if (window.LeoBoardFigma && window.LeoBoardFigma.isFigma(htmlAll)) {
        window.LeoBoardFigma.fromHtml(htmlAll, uid).then(function (fig) {
          if (destroyed) return;
          if (fig && fig.elements.length) {
            placeImported(fig.elements, { keepFrames: fig.source === "Figma" });
            notifyImported(fig);
          } else if (plain.trim()) {
            notify("Не удалось разобрать схему FigJam — вставлен текст");
            setSelection([createAtCenter("text", { text: plain.trim().slice(0, 20000) }).id]);
          }
        });
        return;
      }
      // Схема из Miro — как в оригинале (webapp/board-import.js); не разобрали — как раньше.
      var imp = window.LeoBoardImport ? window.LeoBoardImport.fromHtml(cd.getData("text/html") || "", uid) : null;
      if (imp && imp.elements.length) {
        placeImported(imp.elements);
        notifyImported(imp);
        return;
      }
      if (files.length) {
        insertImageFile(files[0]);
        return;
      }
      var text = (cd.getData("text/plain") || "").trim();
      if (text) {
        var el = createAtCenter("text", { text: text.slice(0, 20000) });
        setSelection([el.id]);
      }
    }

    // ---- указатель ----
    function startGesture(g) {
      gesture = g;
      closePop();
      propsBar.classList.add("hidden");
    }

    function onPointerDown(e) {
      if (destroyed) return;
      autoFit = false;
      if (editing && editing.text.contains(e.target)) return;
      // Щелчок в рамке редактируемого текста, но мимо строк (поля блока, пустое место под текстом,
      // подсветка поверх) — правка продолжается, курсор — в ближайшее место текста.
      if (editing && !editing.kind && e.button === 0 && editing.text.isConnected) {
        var edEl = getEl(editing.id);
        var ep = edEl && toWorld(e.clientX, e.clientY);
        if (edEl && (editing.node.contains(e.target) || (!edEl.rot && edEl.type !== "line" && G.contains(edEl, ep, 0)))) {
          e.preventDefault();
          placeCaretAt(editing.text, e.clientX, e.clientY);
          return;
        }
      }
      // Нажатие мимо таблицы — гасим её меню и выделение ячеек, даже если правка уже закрыта.
      tables.forEach(function (tb, tid) {
        var n = nodes.get(tid);
        if (!(n && n.contains(e.target)) && typeof tb.resetUi === "function") tb.resetUi();
      });
      if (e.button === 2) return;
      if (editing) finishEdit();
      closePop();
      try {
        stage.setPointerCapture(e.pointerId);
      } catch (_) {}
      if (document.activeElement !== stage) stage.focus({ preventScroll: true });
      pointers.set(e.pointerId, G.pt(e.clientX, e.clientY));
      var rect = stageRect();
      if (pointers.size === 2) {
        // Второй палец — щипок: прерываем начатое (создание/перемещение уже применено, фиксируем).
        endGesture(true);
        var ps = Array.from(pointers.values());
        startGesture({
          kind: "pinch", rect: rect, d0: Math.max(1, G.dist(ps[0], ps[1])), z0: view.z,
          w0: toWorld((ps[0].x + ps[1].x) / 2, (ps[0].y + ps[1].y) / 2, rect),
        });
        return;
      }
      if (pointers.size > 2) return;
      var p = toWorld(e.clientX, e.clientY, rect);
      var base = { rect: rect, sx: e.clientX, sy: e.clientY, p0: p, moved: false, pointerId: e.pointerId, pointerType: e.pointerType };
      if (e.button === 1 || spaceDown || tool === "hand" || (readOnly && e.pointerType !== "mouse")) {
        e.preventDefault();
        startGesture(Object.assign(base, { kind: "pan", vx: view.x, vy: view.y }));
        return;
      }
      if (readOnly) {
        startGesture(Object.assign(base, { kind: "pan", vx: view.x, vy: view.y }));
        return;
      }
      var hEl = e.target.closest ? e.target.closest("[data-h]") : null;
      if (hEl && tool === "select") {
        e.preventDefault();
        startHandle(hEl.getAttribute("data-h"), base, e);
        return;
      }
      if (SHAPES[tool] || tool === "rectSharp" || tool === "sticky" || tool === "text" || tool === "frame") {
        e.preventDefault();
        begin();
        // «Прямоугольник» без скруглений — тот же rect с радиусом 0.
        var ctype = tool === "rectSharp" ? "rect" : tool;
        var el = newEl(ctype, p, ctype === "frame" ? { w: 1, h: 1, name: nextFrameName(), z: bottomZ() } : { w: 1, h: 1 });
        if (tool === "rectSharp") el.style = Object.assign({}, el.style, { radius: 0 });
        if (tool === "text") el.h = DEFAULTS.text.h;
        put(el);
        startGesture(Object.assign(base, { kind: "create", id: el.id, type: ctype }));
        return;
      }
      if (tool === "line" || tool === "arrow") {
        e.preventDefault();
        var sn = snapEnd(p, null, e.altKey);
        begin();
        var ln = newEl("line", G.pt(0, 0), {
          points: [[p.x, p.y], [p.x, p.y]],
          route: lastStyle.lineRoute || "straight",
        });
        if (sn.end) ln.start = sn.end;
        if (tool === "arrow") ln.arrowEnd = true;
        put(ln);
        startGesture(Object.assign(base, { kind: "end", id: ln.id, which: "p1", created: true }));
        return;
      }
      if (tool === "path") {
        e.preventDefault();
        penDown(p, e);
        return;
      }
      if (tool === "pen") {
        e.preventDefault();
        startGesture(Object.assign(base, { kind: "pen", pts: [p], live: null }));
        drawPenPreview();
        return;
      }
      // Полоса Ганта (Гант уже выделен): тянуть — сдвиг задачи, за край — срок.
      var gbar = tool === "select" && e.target.closest ? e.target.closest(".lb-g-bar") : null;
      var gbId = gbar ? hitId(gbar) : "";
      if (gbId && selected.size === 1 && selected.has(gbId) && !(e.shiftKey || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        var edge = e.target.closest("[data-edge]");
        gSel = { id: gbId, task: gbar.getAttribute("data-task") };
        markDirty(gbId);
        begin();
        startGesture(Object.assign(base, { kind: "gbar", id: gbId, task: gSel.task, edge: edge ? edge.getAttribute("data-edge") : "", el0: clone(getEl(gbId)) }));
        return;
      }
      // Подпись стрелки тянется вдоль линии; щелчок — как по самой стрелке (двойной — правка текста).
      var lbl = tool === "select" && e.target.closest ? e.target.closest(".lb-line-label") : null;
      var lblId = lbl ? hitId(lbl) : "";
      if (lblId && !(e.shiftKey || e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (!selected.has(lblId) || selected.size !== 1) setSelection([lblId]);
        begin();
        startGesture(Object.assign(base, { kind: "label", id: lblId }));
        return;
      }
      var rawId = hitId(e.target);
      // Как во Figma: щелчок выбирает объект на текущем уровне вложенности, глубже — двойным щелчком;
      // Cmd/Ctrl — сразу самый глубокий под курсором; Shift — добавить к выделению.
      var id = rawId ? pickLevel(rawId, e.metaKey || e.ctrlKey) : "";
      if (id) {
        e.preventDefault();
        var ids;
        if (e.shiftKey) {
          var grp = expandGroups([id]);
          var nextSel = new Set(selected);
          var allIn = Array.from(grp).every(function (x) {
            return nextSel.has(x);
          });
          // Уже выделенный: снимем выделение только по щелчку без движения — с Shift его можно тащить.
          if (!allIn) {
            grp.forEach(function (x) {
              nextSel.add(x);
            });
            selected = nextSel;
            schedule();
          } else {
            base.toggleOff = Array.from(grp);
          }
        } else if (!selected.has(id)) {
          setSelection([id]);
        }
        ids = Array.from(selected);
        startMove(base, ids, rawId);
        return;
      }
      // Пустое место: мышью — рамка выделения, пальцем — панорама.
      if (e.pointerType === "touch") {
        startGesture(Object.assign(base, { kind: "pan", vx: view.x, vy: view.y, tapEmpty: true }));
        return;
      }
      if (!(e.shiftKey || e.metaKey || e.ctrlKey)) selected.clear();
      startGesture(Object.assign(base, { kind: "marquee", keep: new Set(selected) }));
      schedule();
    }

    /** Цепочка фреймов от внешнего до самого объекта. */
    function frameChain(id) {
      var chain = [];
      for (var q = getEl(id), g = 0; q && g < 30; q = frameOf(q), g++) chain.unshift(q.id);
      return chain;
    }

    /** Какой уровень выделить по щелчку: внутрь фрейма верхнего уровня — сразу; дальше — только туда,
     * где уже стоит выделение (сам фрейм выделен — ещё нет, нужен двойной щелчок). */
    function pickLevel(id, deep) {
      if (deep) return id;
      var chain = frameChain(id);
      var open = {};
      selected.forEach(function (sid) {
        var c = frameChain(sid);
        for (var i = 0; i < c.length - 1; i++) open[c[i]] = 1;
      });
      for (var i = 0; i < chain.length - 1; i++) {
        if (!(i === 0 || open[chain[i]])) return chain[i];
      }
      return chain[chain.length - 1];
    }

    /** Двойной щелчок: на уровень глубже к объекту под курсором (если выделен один из его фреймов). */
    function pickDeeper(id) {
      var chain = frameChain(id);
      for (var i = chain.length - 2; i >= 0; i--) {
        if (selected.has(chain[i])) return chain[i + 1];
      }
      return "";
    }

    // ---- экраны (режим «Макет») ----
    /** Выделение + содержимое выделенных экранов (их переносят, копируют и удаляют вместе). */
    function withFrameKids(ids) {
      var out = new Set(ids);
      var kids = frameKids();
      var stack = ids.slice();
      while (stack.length) {
        var id = stack.pop();
        (kids[id] || []).forEach(function (k) {
          if (!out.has(k)) {
            out.add(k);
            stack.push(k);
          }
        });
      }
      return Array.from(out);
    }

    /** Экран → его прямое содержимое (в т. ч. вложенные экраны). */
    function frameKids() {
      var kids = {};
      els.forEach(function (el) {
        if (el.frame) (kids[el.frame] = kids[el.frame] || []).push(el.id);
      });
      return kids;
    }

    function frameDepth(el) {
      var d = 0;
      for (var f = frameOf(el); f && d < 20; f = frameOf(f)) d++;
      return d;
    }

    function framesByZ() {
      var list = [];
      els.forEach(function (el) {
        if (el.type === "frame") list.push(el);
      });
      return list.sort(function (a, b) {
        return (b.z || 0) - (a.z || 0);
      });
    }

    /** Кто в каком экране: по центру элемента; попавший в экран — над ним. Внутри шага отмены. */
    function syncFrames() {
      var frames = framesByZ();
      if (!frames.length && !Array.from(els.values()).some(function (e) {
        return e.frame;
      })) return;
      // Сначала экраны (крупные раньше: вложенный узнаёт внешний), потом остальное.
      // Стрелка, привязанная к фигурам, ходит за ними — в экран кладём только свободную.
      var tied = function (el) {
        return el.type === "line" && ((el.start && el.start.el) || (el.end && el.end.el));
      };
      els.forEach(function (el) {
        if (el.frame && tied(el)) patch(el.id, { frame: undefined });
      });
      // Принадлежность меняется только у того, что двигали в этом шаге (как во Figma): объект, вылезающий
      // за край своего фрейма (обрезка, автолэйаут), не выпадает из него от чужих правок. Едет вместе
      // со своим фреймом — тоже остаётся. Новый фрейм забирает свободные объекты под собой.
      var touched = null;
      if (tx) {
        touched = {};
        var fresh = [];
        tx.before.forEach(function (before, id) {
          touched[id] = 1;
          var cur = getEl(id);
          if (!before && cur && cur.type === "frame") fresh.push(cur);
        });
        if (fresh.length) els.forEach(function (el) {
          if (touched[el.id] || el.frame) return;
          var bb = G.elBounds(el, getEl);
          if (!bb) return;
          var cx0 = bb.x + bb.w / 2;
          var cy0 = bb.y + bb.h / 2;
          if (fresh.some(function (f) {
            return cx0 >= f.x && cx0 <= f.x + f.w && cy0 >= f.y && cy0 <= f.y + f.h;
          })) touched[el.id] = 1;
        });
      }
      var order = Array.from(els.values()).filter(function (el) {
        if (tied(el)) return false;
        if (!touched) return true;
        return touched[el.id] && !(el.frame && touched[el.frame]);
      }).sort(function (a, b) {
        var fa = a.type === "frame" ? 0 : 1;
        var fb = b.type === "frame" ? 0 : 1;
        return fa - fb || b.w * b.h - a.w * a.h;
      });
      order.forEach(function (el0) {
        var el = getEl(el0.id);
        if (!el) return;
        var b = G.elBounds(el, getEl);
        if (!b) return;
        var cx = b.x + b.w / 2;
        var cy = b.y + b.h / 2;
        var own = el.type === "frame" ? withFrameKids([el.id]) : [el.id];
        var mine = {};
        own.forEach(function (id) {
          mine[id] = 1;
        });
        // Внутренний из подходящих экранов — с наименьшей площадью (сам и своё содержимое — не родитель).
        var f = null;
        frames.forEach(function (fr0) {
          var fr = getEl(fr0.id);
          if (!fr || mine[fr.id]) return;
          if (el.type === "frame" && fr.w * fr.h <= el.w * el.h) return;
          if (cx >= fr.x && cx <= fr.x + fr.w && cy >= fr.y && cy <= fr.y + fr.h && (!f || fr.w * fr.h < f.w * f.h)) f = fr;
        });
        var fid = f ? f.id : undefined;
        if ((el.frame || undefined) === fid && (!f || (el.z || 0) > (f.z || 0))) return;
        patch(el.id, { frame: fid });
        if (f && (el.z || 0) <= (f.z || 0)) {
          // Попавший в экран — над ним; экран — вместе со своим содержимым, порядок внутри сохраняется.
          var z = topZ();
          own.map(getEl).filter(Boolean).sort(function (a, c) {
            return (a.z || 0) - (c.z || 0);
          }).forEach(function (x, i) {
            patch(x.id, { z: z + i });
          });
        }
      });
    }

    function nextFrameName() {
      var n = 0;
      els.forEach(function (el) {
        if (el.type === "frame") n++;
      });
      return "Фрейм " + (n + 1);
    }

    /** Новый экран по размеру устройства — справа от последнего экрана (или в центре вида). */
    function insertFrame(deviceId) {
      if (readOnly) return;
      finishEdit();
      closePop();
      var dev = DEVICES.filter(function (d) {
        return d.id === deviceId;
      })[0] || DEVICES[0];
      var frames = framesByZ();
      var at;
      if (frames.length) {
        var right = frames.reduce(function (m, f) {
          return f.x + f.w > m.x + m.w ? f : m;
        }, frames[0]);
        at = G.pt(right.x + right.w + 80, right.y);
      } else {
        var r = stageRect();
        var c = toWorld(r.left + r.width / 2, r.top + r.height / 2, r);
        at = G.pt(Math.round(c.x - dev.w / 2), Math.round(c.y - dev.h / 2));
      }
      begin();
      var f = newEl("frame", at, { w: dev.w, h: dev.h, name: nextFrameName(), device: dev.id, z: bottomZ() });
      put(f);
      syncFrames();
      commit();
      setTool("select");
      flushNow();
      autoFit = false;
      fitBounds(G.unionBounds(framesByZ().map(function (x) {
        return { x: x.x, y: x.y, w: x.w, h: x.h };
      })));
      setSelection([f.id]);
    }

    function startMove(base, ids, hit) {
      ids = withFrameKids(ids).filter(function (id) {
        return !isLocked(getEl(id));
      });
      var list = ids.map(getEl).filter(Boolean);
      begin();
      var moving = {};
      list.forEach(function (el) {
        moving[el.id] = 1;
      });
      // Стрелки с изломами между двумя переносимыми блоками едут целиком.
      var carry = [];
      els.forEach(function (el) {
        if (el.type !== "line" || moving[el.id] || !(el.waypoints && el.waypoints.length)) return;
        if (el.start && moving[el.start.el] && el.end && moving[el.end.el]) carry.push(clone(el));
      });
      startGesture(Object.assign(base, {
        kind: "move", hit: hit, items: list.map(clone), moving: moving, carry: carry,
        bounds: G.unionBounds(list.map(function (el) {
          return G.elBounds(el, getEl);
        })),
      }));
    }

    function startHandle(h, base, e) {
      var single = selList().length === 1 ? selList()[0] : null;
      if (h.indexOf("c-") === 0 && single) {
        var side = h.slice(2);
        startGesture(Object.assign(base, { kind: "connect", from: single.id, side: side }));
        return;
      }
      if (/^(wp|ins|seg):\d+$/.test(h) && single && single.type === "line") {
        startBendHandle(h, base, single);
        return;
      }
      if ((h === "p0" || h === "p1" || h === "mid") && single && single.type === "line") {
        begin();
        startGesture(Object.assign(base, { kind: h === "mid" ? "bend" : "end", id: single.id, which: h, res: G.resolveLine(single, getEl) }));
        return;
      }
      if (h === "rot" && single) {
        begin();
        startGesture(Object.assign(base, { kind: "rotate", id: single.id, el0: clone(single) }));
        return;
      }
      if (h.charAt(0) === "g") {
        var list = selList();
        begin();
        startGesture(Object.assign(base, {
          kind: "gscale", h: h.slice(1), items: list.map(clone),
          bounds: G.unionBounds(list.map(function (el) {
            return G.elBounds(el, getEl);
          })),
        }));
        return;
      }
      if (single) {
        begin();
        startGesture(Object.assign(base, { kind: "resize", id: single.id, h: h, el0: clone(single), shift: e.shiftKey }));
      }
    }

    function wpArr(q) {
      return [Math.round(q.x * 10) / 10, Math.round(q.y * 10) / 10];
    }

    /** Ручки изломов: точка (wp), вставка новой точки (ins), отрезок ломаной (seg). */
    function startBendHandle(h, base, ln) {
      var kind = h.split(":")[0];
      var n = Number(h.split(":")[1]);
      var res = G.resolveLine(ln, getEl);
      var before = ln.waypoints;
      if (kind === "wp") {
        var now = Date.now();
        if (lastWpTap.h === h && lastWpTap.id === ln.id && now - lastWpTap.t < 380) {
          // Двойное нажатие по точке — убрать её.
          lastWpTap = { h: "", id: "", t: 0 };
          var rest = G.waypointsOf(ln).filter(function (_, i) {
            return i !== n;
          }).map(wpArr);
          begin();
          patch(ln.id, { waypoints: rest.length ? rest : undefined });
          commit();
          return;
        }
        lastWpTap = { h: h, id: ln.id, t: now };
        begin();
        startGesture(Object.assign(base, { kind: "wp", id: ln.id, idx: n, before: before }));
        return;
      }
      if (kind === "ins") {
        var had = G.waypointsOf(ln).length;
        var wps = G.materializeWaypoints(ln, res, pathOptsOf(ln)).map(wpArr);
        // Изгиб старого вида превращается в одну точку — её и тянем.
        var fromBend = !had && wps.length === 1;
        if (!fromBend) wps.splice(n, 0, wpArr(base.p0));
        begin();
        patch(ln.id, { waypoints: wps, bend: undefined });
        startGesture(Object.assign(base, { kind: "wp", id: ln.id, idx: fromBend ? 0 : n, before: before, inserted: true, bend0: ln.bend }));
        return;
      }
      // seg: отрезок ломаной двигается поперёк себя, соседние углы — за ним.
      var Q = linePathOf(ln, res).poly.map(function (q) {
        return G.pt(q.x, q.y);
      });
      var i = n;
      if (i < 0 || i > Q.length - 2) return;
      if (i === 0) {
        // Крайний отрезок прилип к концу: оставляем у конца короткий отросток и тянем остальное.
        var l0 = G.dist(Q[0], Q[1]);
        var st = G.add(Q[0], G.mul(G.norm(G.sub(Q[1], Q[0])), Math.min(G.STUB, l0 / 2)));
        Q.splice(1, 0, st, G.pt(st.x, st.y));
        i = 2;
      }
      if (i === Q.length - 2) {
        var m = Q.length - 1;
        var l1 = G.dist(Q[m - 1], Q[m]);
        var et = G.sub(Q[m], G.mul(G.norm(G.sub(Q[m], Q[m - 1])), Math.min(G.STUB, l1 / 2)));
        Q.splice(m, 0, et, G.pt(et.x, et.y));
      }
      var horiz = Math.abs(Q[i + 1].x - Q[i].x) >= Math.abs(Q[i + 1].y - Q[i].y);
      begin();
      startGesture(Object.assign(base, {
        kind: "seg", id: ln.id, idx: i, poly: Q, horiz: horiz, before: before, bend0: ln.bend,
        c0: horiz ? Q[i].y : Q[i].x,
      }));
    }

    function doWaypoint(g, p, shift) {
      var ln = getEl(g.id);
      if (!ln) return;
      var res = G.resolveLine(ln, getEl);
      var wps = G.waypointsOf(ln);
      var pass = [res.p0].concat(wps, [res.p1]);
      var prev = pass[g.idx];
      var next = pass[g.idx + 2];
      if (shift && prev) {
        // Shift — угол от предыдущей точки с шагом 15°.
        var v = G.sub(p, prev);
        var step = Math.PI / 12;
        var ang = Math.round(Math.atan2(v.y, v.x) / step) * step;
        p = G.add(prev, G.pt(Math.cos(ang) * G.len(v), Math.sin(ang) * G.len(v)));
      } else {
        // Прилипание к соседним точкам по осям — чтобы легко поставить ровно.
        var th = 6 / view.z;
        var x = p.x;
        var y = p.y;
        [prev, next].forEach(function (q) {
          if (!q) return;
          if (Math.abs(p.x - q.x) < th) x = q.x;
          if (Math.abs(p.y - q.y) < th) y = q.y;
        });
        if (showGrid && x === p.x) x = Math.abs(p.x - Math.round(p.x / GRID) * GRID) < th ? Math.round(p.x / GRID) * GRID : x;
        if (showGrid && y === p.y) y = Math.abs(p.y - Math.round(p.y / GRID) * GRID) < th ? Math.round(p.y / GRID) * GRID : y;
        p = G.pt(x, y);
      }
      var arr = wps.map(wpArr);
      arr[g.idx] = wpArr(p);
      patch(g.id, { waypoints: arr });
    }

    function doLabel(g, p) {
      var ln = getEl(g.id);
      if (!ln) return;
      var t = G.polyProjectT(linePathOf(ln).poly, p);
      t = Math.max(0.03, Math.min(0.97, t));
      // Около середины прилипает к ней — вернуть подпись на место легко.
      patch(g.id, { labelT: Math.abs(t - 0.5) < 0.03 ? undefined : Math.round(t * 1000) / 1000 });
    }

    function doSegment(g, p) {
      var ln = getEl(g.id);
      if (!ln) return;
      var c = g.horiz ? p.y : p.x;
      var th = 6 / view.z;
      var res = G.resolveLine(ln, getEl);
      // Прилипание к концам — чтобы вытянуть ломаную в прямую.
      [res.p0, res.p1].forEach(function (q) {
        var qc = g.horiz ? q.y : q.x;
        if (Math.abs(c - qc) < th) c = qc;
      });
      if (showGrid) {
        var gc = Math.round(c / GRID) * GRID;
        if (Math.abs(gc - c) < th) c = gc;
      }
      var Q = g.poly.map(function (q) {
        return G.pt(q.x, q.y);
      });
      [g.idx, g.idx + 1].forEach(function (k) {
        if (g.horiz) Q[k].y = c;
        else Q[k].x = c;
      });
      patch(g.id, { waypoints: Q.slice(1, -1).map(wpArr), bend: undefined });
    }

    /** После правки изломов: лишние точки на прямых участках убираем. */
    function tidyWaypoints(id) {
      var ln = getEl(id);
      if (!ln) return;
      var wps = G.waypointsOf(ln);
      if (!wps.length) return;
      if ((ln.route || "straight") === "curve") return;
      var res = G.resolveLine(ln, getEl);
      var clean = G.cleanPoly([res.p0].concat(wps, [res.p1])).slice(1, -1);
      if (clean.length === wps.length) return;
      patch(id, { waypoints: clean.length ? clean.map(wpArr) : undefined });
    }

    function onPointerMove(e) {
      if (!e.__autoPan) {
        lastMove = { clientX: e.clientX, clientY: e.clientY };
        if (penPath && penPath.drawing && !gesture) {
          penPath.cursor = penTarget(toWorld(e.clientX, e.clientY), e);
          renderOverlay();
        }
        updateMeasure(e, gesture && gesture.kind === "move" ? Object.keys(gesture.moving || {}) : null);
        updateGapHover(e);
      }
      if (!pointers.has(e.pointerId)) {
        if (o.onCursor && !readOnly) {
          var pc = toWorld(e.clientX, e.clientY);
          o.onCursor(pc.x, pc.y);
        }
        return;
      }
      pointers.set(e.pointerId, G.pt(e.clientX, e.clientY));
      if (o.onCursor && !readOnly) {
        var pw = toWorld(e.clientX, e.clientY);
        o.onCursor(pw.x, pw.y);
      }
      var g = gesture;
      if (!g) return;
      if (g.kind === "pinch") {
        var ps = Array.from(pointers.values());
        if (ps.length < 2) return;
        var d = Math.max(1, G.dist(ps[0], ps[1]));
        var z = clamp(g.z0 * (d / g.d0), MIN_Z, MAX_Z);
        var mx = (ps[0].x + ps[1].x) / 2 - g.rect.left;
        var my = (ps[0].y + ps[1].y) / 2 - g.rect.top;
        view.z = z;
        view.x = mx - g.w0.x * z;
        view.y = my - g.w0.y * z;
        applyView();
        return;
      }
      if (e.pointerId !== g.pointerId) return;
      if (!e.__autoPan) keepAutoPan(g, e);
      var dxs = e.clientX - g.sx;
      var dys = e.clientY - g.sy;
      if (!g.moved && Math.abs(dxs) + Math.abs(dys) < (e.pointerType === "touch" ? 6 : 3)) return;
      g.moved = true;
      var p = toWorld(e.clientX, e.clientY, g.rect);
      if (g.kind === "pan") {
        view.x = g.vx + dxs;
        view.y = g.vy + dys;
        applyView();
      } else if (g.kind === "marquee") {
        var a = g.p0;
        marquee = { x: Math.min(a.x, p.x), y: Math.min(a.y, p.y), w: Math.abs(p.x - a.x), h: Math.abs(p.y - a.y) };
        var hit = new Set(g.keep);
        els.forEach(function (el) {
          if (isHidden(el) || isLocked(el)) return;
          var b = G.elBounds(el, getEl);
          if (!b) return;
          // Экран — только если рамка накрыла его целиком (иначе рамкой внутри экрана не выделить содержимое).
          if (el.type === "frame") {
            if (b.x >= marquee.x && b.y >= marquee.y && b.x + b.w <= marquee.x + marquee.w && b.y + b.h <= marquee.y + marquee.h) hit.add(el.id);
          } else if (G.intersects(b, marquee)) hit.add(el.id);
        });
        selected = expandGroups(Array.from(hit));
        schedule();
      } else if (g.kind === "move") {
        doMove(g, p, e);
        updateMeasure(e, Object.keys(g.moving || {}));
      } else if (g.kind === "create") {
        doCreate(g, p, e.shiftKey);
      } else if (g.kind === "end") {
        doEnd(g, p, e.altKey, e.shiftKey);
      } else if (g.kind === "label") {
        doLabel(g, p);
      } else if (g.kind === "wp") {
        doWaypoint(g, p, e.shiftKey);
      } else if (g.kind === "seg") {
        doSegment(g, p);
      } else if (g.kind === "bend") {
        var ln = getEl(g.id);
        if (ln) patch(g.id, { bend: Math.round(G.bendFromPoint(ln, G.resolveLine(ln, getEl), p)) });
      } else if (g.kind === "rotate") {
        var c = G.center(g.el0);
        // Угол — от места, где схватили (зона у угла), а не от верха: объект не прыгает при нажатии.
        if (g.a0 == null) g.a0 = (Math.atan2(g.p0.y - c.y, g.p0.x - c.x) * 180) / Math.PI;
        var ang = (g.el0.rot || 0) + (Math.atan2(p.y - c.y, p.x - c.x) * 180) / Math.PI - g.a0;
        if (e.shiftKey) ang = Math.round(ang / 15) * 15;
        else {
          var near = Math.round(ang / 90) * 90;
          if (Math.abs(ang - near) < 4) ang = near;
        }
        ang = ((ang % 360) + 360) % 360;
        if (ang > 180) ang -= 360;
        patch(g.id, { rot: Math.round(ang * 10) / 10 || undefined });
        if (g.el0.type === "frame") {
          if (!g.kids0) g.kids0 = withFrameKids([g.id]).filter(function (id) {
            return id !== g.id;
          }).map(getEl).filter(Boolean).map(clone);
          rotateKids(g.kids0, c, (Math.round(ang * 10) / 10) - (g.el0.rot || 0));
        }
      } else if (g.kind === "pathpt") {
        penDrag(p, e);
      } else if (g.kind === "vpt") {
        penMovePt(g, p, e);
      } else if (g.kind === "vhd") {
        penMoveHandle(g, p, e);
      } else if (g.kind === "gbar") {
        doGanttBar(g, p);
      } else if (g.kind === "resize") {
        doResize(g, p, e.shiftKey, e.metaKey || e.ctrlKey);
      } else if (g.kind === "gscale") {
        doGroupScale(g, p);
      } else if (g.kind === "pen") {
        var last = g.pts[g.pts.length - 1];
        if (G.dist(last, p) * view.z >= 1.5) {
          g.pts.push(p);
          drawPenPreview();
        }
      } else if (g.kind === "connect") {
        // Тянем от «плюса»: появляется стрелка от стороны блока.
        begin();
        var from = getEl(g.from);
        if (!from) return;
        var sp = G.sidePoint(from, g.side);
        var ln2 = newEl("line", G.pt(0, 0), {
          points: [[sp.x, sp.y], [p.x, p.y]], start: { el: from.id, side: g.side }, arrowEnd: true,
          route: lastStyle.lineRoute || "curve",
        });
        put(ln2);
        gesture = Object.assign(g, { kind: "end", id: ln2.id, which: "p1", created: true });
        doEnd(gesture, p, e.altKey, e.shiftKey);
      }
    }

    // ---- автопрокрутка у края: тянем рамку, блок, стрелку — доска едет сама ----
    var AUTO_PAN_EDGE = 36;
    var AUTO_PAN_MAX = 18;
    var AUTO_PAN_KINDS = { marquee: 1, move: 1, create: 1, end: 1, resize: 1, gscale: 1, label: 1, wp: 1, seg: 1, gbar: 1, connect: 1 };
    var autoPan = null; // {frame (таймер ~60 к/с), ev}

    function keepAutoPan(g, e) {
      if (!AUTO_PAN_KINDS[g.kind]) return stopAutoPan();
      var ev = { clientX: e.clientX, clientY: e.clientY, pointerId: e.pointerId, pointerType: e.pointerType, shiftKey: e.shiftKey, altKey: e.altKey, metaKey: e.metaKey, ctrlKey: e.ctrlKey, __autoPan: true };
      if (!panSpeed(ev)) return stopAutoPan();
      if (autoPan) {
        autoPan.ev = ev;
        return;
      }
      autoPan = { ev: ev, frame: setTimeout(autoPanTick, 16) };
    }

    /** Скорость по близости к краю доски (px за кадр); null — курсор не у края. */
    function panSpeed(ev) {
      var r = stageRect();
      var sp = function (d) {
        return d < AUTO_PAN_EDGE ? Math.ceil(AUTO_PAN_MAX * (1 - Math.max(0, d) / AUTO_PAN_EDGE)) : 0;
      };
      var vx = sp(ev.clientX - r.left) - sp(r.right - ev.clientX);
      var vy = sp(ev.clientY - r.top) - sp(r.bottom - ev.clientY);
      return vx || vy ? { x: vx, y: vy } : null;
    }

    function autoPanTick() {
      if (!autoPan || destroyed) return;
      var g = gesture;
      var v = g && AUTO_PAN_KINDS[g.kind] ? panSpeed(autoPan.ev) : null;
      if (!v) return stopAutoPan();
      view.x += v.x;
      view.y += v.y;
      applyView();
      // Точка под курсором на доске сдвинулась — жест продолжается, как будто мышь двигали.
      onPointerMove(autoPan.ev);
      if (autoPan) autoPan.frame = setTimeout(autoPanTick, 16);
    }

    function stopAutoPan() {
      if (autoPan && autoPan.frame) clearTimeout(autoPan.frame);
      autoPan = null;
    }

    /** Option/Alt при перетаскивании — тянем копию, оригинал остаётся на месте (как в Miro). */
    function duplicateDragged(g) {
      g.items.forEach(function (el0) {
        put(clone(el0)); // оригиналы — на исходные места
      });
      var copies = copyEls(g.items, 0, 0);
      copies.forEach(put);
      g.items = copies.map(clone);
      g.moving = {};
      copies.forEach(function (c) {
        g.moving[c.id] = 1;
      });
      g.dup = true;
      setSelection(copies.map(function (c) {
        return c.id;
      }));
    }

    function doMove(g, p, e) {
      // Alt — тянуть копию (как в Miro и Figma). В макете — только если Alt зажат с начала переноса:
      // нажатый по ходу Alt лишь показывает расстояния.
      if (e.altKey && !g.dup && (mode !== "design" || !g.started)) duplicateDragged(g);
      g.started = true;
      var dx = p.x - g.p0.x;
      var dy = p.y - g.p0.y;
      // Shift — только по горизонтали или только по вертикали (куда сдвинули больше).
      var lock = e.shiftKey ? (Math.abs(dx) >= Math.abs(dy) ? "x" : "y") : "";
      if (lock === "x") dy = 0;
      if (lock === "y") dx = 0;
      // Cmd/Ctrl — без привязки к сетке и направляющим.
      var free = e.metaKey || e.ctrlKey;
      guides = [];
      if (g.bounds && !free) {
        var b = g.bounds;
        // Направляющие важнее сетки: иначе блоки разной ширины не встают центр к центру
        // (сетка двигает край с шагом 20, центры всегда расходятся на несколько пикселей).
        var pre = alignGuides(g, b, dx, dy);
        guides = [];
        // В макете вместо сетки — целые пиксели (как во Figma): прилипание к краям важнее.
        var step = mode === "design" ? 1 : showGrid ? GRID : 0;
        if (pre.snapX && lock !== "y") dx = pre.dx;
        else if (step && lock !== "y") dx = Math.round((b.x + dx) / step) * step - b.x;
        if (pre.snapY && lock !== "x") dy = pre.dy;
        else if (step && lock !== "x") dy = Math.round((b.y + dy) / step) * step - b.y;
        var adj = alignGuides(g, b, dx, dy);
        if (lock !== "y") dx = adj.dx;
        if (lock !== "x") dy = adj.dy;
        if (lock) {
          guides = guides.filter(function (gd) {
            // Направляющая по зафиксированной оси не действует — не рисуем её.
            return lock === "x" ? gd.a.x === gd.b.x : gd.a.y === gd.b.y;
          });
        }
      }
      g.items.forEach(function (el0) {
        var f = moved(el0, dx, dy, g.moving);
        if (el0.type === "line") {
          ["start", "end"].forEach(function (k) {
            if (el0[k] && g.moving[el0[k].el]) f[k] = el0[k];
          });
        }
        var cur = getEl(el0.id);
        if (cur) put(Object.assign({}, cur, f, el0.type === "line" ? {} : { x: el0.x + dx, y: el0.y + dy }));
      });
      (g.carry || []).forEach(function (ln0) {
        if (getEl(ln0.id)) patch(ln0.id, { waypoints: shiftWaypoints(ln0.waypoints, dx, dy) });
      });
    }

    /** Направляющие: края и центры выделения прилипают к краям и центрам соседних блоков. */
    function alignGuides(g, b, dx, dy) {
      var th = 6 / view.z;
      var xs = [b.x + dx, b.x + dx + b.w / 2, b.x + dx + b.w];
      var ys = [b.y + dy, b.y + dy + b.h / 2, b.y + dy + b.h];
      var bestX = null;
      var bestY = null;
      // Блоки, связанные стрелкой с переносимыми: центр к центру тянет сильнее и в первую очередь —
      // тогда стрелка между ними встаёт ровно.
      var linked = g.linked || (g.linked = linkedTo(g.moving));
      var thL = 14 / view.z;
      function better(cur, d, prio) {
        return !cur || prio > cur.prio || (prio === cur.prio && Math.abs(d) < Math.abs(cur.d));
      }
      els.forEach(function (el) {
        if (g.moving[el.id] || !BOXED[el.type]) return;
        var ob = G.elBounds(el, getEl);
        if (!ob) return;
        var oxs = [ob.x, ob.x + ob.w / 2, ob.x + ob.w];
        var oys = [ob.y, ob.y + ob.h / 2, ob.y + ob.h];
        var isLinked = !!linked[el.id];
        xs.forEach(function (x, i) {
          oxs.forEach(function (ox, j) {
            var d = ox - x;
            var prio = isLinked && i === 1 && j === 1 ? 1 : 0;
            if (Math.abs(d) <= (prio ? thL : th) && better(bestX, d, prio)) bestX = { d: d, x: ox, ob: ob, prio: prio };
          });
        });
        ys.forEach(function (y, i) {
          oys.forEach(function (oy, j) {
            var d = oy - y;
            var prio = isLinked && i === 1 && j === 1 ? 1 : 0;
            if (Math.abs(d) <= (prio ? thL : th) && better(bestY, d, prio)) bestY = { d: d, y: oy, ob: ob, prio: prio };
          });
        });
      });
      if (bestX) {
        dx += bestX.d;
        var y0 = Math.min(bestX.ob.y, b.y + dy);
        var y1 = Math.max(bestX.ob.y + bestX.ob.h, b.y + dy + b.h);
        guides.push({ a: G.pt(bestX.x, y0), b: G.pt(bestX.x, y1) });
      }
      if (bestY) {
        dy += bestY.d;
        var x0 = Math.min(bestY.ob.x, b.x + dx);
        var x1 = Math.max(bestY.ob.x + bestY.ob.w, b.x + dx + b.w);
        guides.push({ a: G.pt(x0, bestY.y), b: G.pt(x1, bestY.y) });
      }
      return { dx: dx, dy: dy, snapX: !!bestX, snapY: !!bestY };
    }

    /** Блоки на другом конце стрелок от переносимых (сами переносимые не в счёт). */
    function linkedTo(moving) {
      var out = {};
      els.forEach(function (el) {
        if (el.type !== "line") return;
        var a = el.start && el.start.el;
        var b = el.end && el.end.el;
        if (a && b && moving[a] && !moving[b]) out[b] = 1;
        if (a && b && moving[b] && !moving[a]) out[a] = 1;
      });
      return out;
    }

    function doCreate(g, p, square) {
      var el = getEl(g.id);
      if (!el) return;
      var a = g.p0;
      var w = Math.abs(p.x - a.x);
      var h = Math.abs(p.y - a.y);
      if (square || g.type === "sticky") {
        var s = Math.max(w, h);
        w = s;
        h = s;
      }
      var x = p.x < a.x ? a.x - w : a.x;
      var y = p.y < a.y ? a.y - h : a.y;
      if (g.type === "text") {
        put(Object.assign({}, el, { x: x, y: a.y, w: Math.max(40, w) }));
        return;
      }
      put(Object.assign({}, el, { x: x, y: y, w: Math.max(4, w), h: Math.max(4, h) }));
    }

    function doEnd(g, p, free, shift) {
      var ln = getEl(g.id);
      if (!ln) return;
      var key = g.which === "p0" ? "start" : "end";
      var other = g.which === "p0" ? "end" : "start";
      if (shift) {
        // Shift — угол линии с шагом 15°, конец свободный (прилипание сбило бы угол).
        var r0 = G.resolveLine(ln, getEl);
        var o = g.which === "p0" ? r0.p1 : r0.p0;
        var v = G.sub(p, o);
        var len = G.len(v);
        var step = Math.PI / 12;
        var ang = Math.round(Math.atan2(v.y, v.x) / step) * step;
        p = G.add(o, G.pt(Math.cos(ang) * len, Math.sin(ang) * len));
        free = true;
      }
      var exclude = {};
      exclude[ln.id] = 1;
      var sn = snapEnd(p, exclude, free);
      // Обе стороны к одной фигуре «к объекту» — бессмысленная стрелка: тогда только к стороне.
      if (sn.end && ln[other] && ln[other].el === sn.end.el && !sn.end.side) sn = { end: null, el: null };
      snapTarget = sn.el ? { el: sn.el, side: sn.side } : null;
      var res = G.resolveLine(ln, getEl);
      var pts = g.which === "p0" ? [[p.x, p.y], [res.p1.x, res.p1.y]] : [[res.p0.x, res.p0.y], [p.x, p.y]];
      var f = { points: pts };
      f[key] = sn.end || undefined;
      put(Object.assign({}, ln, f));
    }

    function doGanttBar(g, p) {
      var el0 = g.el0;
      var t0 = (el0.tasks || []).filter(function (t) {
        return t.id === g.task;
      })[0];
      if (!t0) return;
      var days = Math.round((p.x - g.p0.x) / BL.unitW(el0)) * BL.stepOf(el0);
      var t = Object.assign({}, t0);
      if (g.edge === "start") t.start = BL.addDays(t0.start, Math.min(days, BL.diffDays(t0.start, t0.end)));
      else if (g.edge === "end") t.end = BL.addDays(t0.end, Math.max(days, -BL.diffDays(t0.start, t0.end)));
      else {
        t.start = BL.addDays(t0.start, days);
        t.end = BL.addDays(t0.end, days);
      }
      var tasks = el0.tasks.map(function (x) {
        return x.id === g.task ? t : x;
      });
      // Шкала начинается не раньше, чем была при нажатии: полосы не «уезжают» под пальцем.
      var next = BL.ganttFit(Object.assign({}, el0, { tasks: tasks }));
      put(Object.assign({}, getEl(g.id) || el0, next));
    }

    var RESIZE = {
      nw: [-1, -1], n: [0, -1], ne: [1, -1], e: [1, 0], se: [1, 1], s: [0, 1], sw: [-1, 1], w: [-1, 0],
    };

    function doResize(g, p, keepRatio, free) {
      var el0 = g.el0;
      var dir = RESIZE[g.h];
      if (!dir) return;
      var lp = G.toLocal(el0, p);
      var x0 = 0;
      var y0 = 0;
      var x1 = el0.w;
      var y1 = el0.h;
      if (dir[0] < 0) x0 = Math.min(lp.x, el0.w - 8);
      if (dir[0] > 0) x1 = Math.max(lp.x, 8);
      if (dir[1] < 0) y0 = Math.min(lp.y, el0.h - 8);
      if (dir[1] > 0) y1 = Math.max(lp.y, 8);
      var w = x1 - x0;
      var h = y1 - y0;
      var ratio = el0.type === "image" || el0.type === "sticky" || el0.ratio ? !keepRatio : keepRatio;
      if (ratio && dir[0] && dir[1] && el0.w > 0 && el0.h > 0) {
        var k = Math.max(w / el0.w, h / el0.h);
        w = el0.w * k;
        h = el0.h * k;
        if (dir[0] < 0) x0 = el0.w - w;
        if (dir[1] < 0) y0 = el0.h - h;
      }
      if (el0.type === "text" || BLOCKS[el0.type]) {
        h = el0.h;
        y0 = 0;
      }
      if (BLOCKS[el0.type] && w < (el0.type === "card" ? 160 : 120)) {
        var minW = el0.type === "card" ? 160 : 120;
        if (dir[0] < 0) x0 = el0.w - minW;
        w = minW;
      }
      var cLocal = G.pt(x0 + w / 2, y0 + h / 2);
      var cWorld = G.toWorld(el0, cLocal);
      var f = { x: cWorld.x - w / 2, y: cWorld.y - h / 2, w: w, h: h };
      if (el0.type === "text" && dir[0] && dir[1]) {
        // Угол текстового блока — масштаб шрифта.
        var fs = (el0.style && el0.style.fontSize) || 18;
        f.style = { fontSize: Math.max(8, Math.round(fs * (w / Math.max(1, el0.w)))) };
      }
      guides = [];
      if (!el0.rot && !free && !(ratio && dir[0] && dir[1])) snapResize(g, f, dir, el0.type === "text" || BLOCKS[el0.type]);
      // Тянут край «обнимающего» экрана — по этой оси размер становится фиксированным (как во Figma).
      if (el0.grow) {
        var gg = String(el0.grow);
        if (dir[0]) gg = gg.replace("w", "");
        if (dir[1]) gg = gg.replace("h", "");
        f.grow = gg || undefined;
      }
      var LR = el0.type === "frame" ? parseLayout(el0.layout) : null;
      if (LR && LR.hug !== "none") {
        if (dir[0]) LR.hug = LR.hug.replace("w", "");
        if (dir[1]) LR.hug = LR.hug.replace("h", "");
        f.layout = layoutStr(Object.assign(LR, { hug: LR.hug || "none" }));
      }
      patch(g.id, f);
      // Макет: содержимое фрейма — по привязкам к его краям (Cmd — без них, как во Figma).
      if (el0.type === "frame" && !el0.layout && !el0.rot && mode === "design") {
        if (!g.pinSnap) g.pinSnap = pinSnap(g.id);
        var nf = getEl(g.id);
        g.pinSnap.forEach(function (k0, id) {
          if (getEl(id)) put(clone(k0));
        });
        if (!free && nf) applyPins(g.id, el0, nf, g.pinSnap);
      }
    }

    /** Тянем край — он прилипает к краям и центрам соседей и экрана (в макете — ещё и к целым пикселям). */
    function snapResize(g, f, dir, fixedH) {
      var th = 6 / view.z;
      var best = [null, null];
      var edges = [dir[0] < 0 ? f.x : dir[0] > 0 ? f.x + f.w : null, fixedH || !dir[1] ? null : dir[1] < 0 ? f.y : f.y + f.h];
      els.forEach(function (el) {
        if (el.id === g.id || !BOXED[el.type]) return;
        var ob = G.elBounds(el, getEl);
        if (!ob) return;
        [[ob.x, ob.x + ob.w / 2, ob.x + ob.w], [ob.y, ob.y + ob.h / 2, ob.y + ob.h]].forEach(function (cands, ax) {
          if (edges[ax] == null) return;
          cands.forEach(function (c) {
            var d = c - edges[ax];
            if (Math.abs(d) <= th && (!best[ax] || Math.abs(d) < Math.abs(best[ax].d))) best[ax] = { d: d, v: c, ob: ob };
          });
        });
      });
      [0, 1].forEach(function (ax) {
        if (edges[ax] == null) return;
        var P = ax ? "y" : "x";
        var S = ax ? "h" : "w";
        var d = best[ax] ? best[ax].d : mode === "design" ? Math.round(edges[ax]) - edges[ax] : 0;
        if (!d && !best[ax]) return;
        if (dir[ax] < 0) {
          f[P] += d;
          f[S] -= d;
        } else f[S] += d;
        if (!best[ax]) return;
        var ob = best[ax].ob;
        var Q = ax ? "x" : "y";
        var T = ax ? "w" : "h";
        var lo = Math.min(ob[Q], f[Q]);
        var hi = Math.max(ob[Q] + ob[T], f[Q] + f[T]);
        guides.push(ax ? { a: G.pt(lo, best[ax].v), b: G.pt(hi, best[ax].v) } : { a: G.pt(best[ax].v, lo), b: G.pt(best[ax].v, hi) });
      });
    }

    function doGroupScale(g, p) {
      var b = g.bounds;
      if (!b || b.w <= 0 || b.h <= 0) return;
      var ax = g.h.indexOf("w") >= 0 ? b.x + b.w : b.x;
      var ay = g.h.indexOf("n") >= 0 ? b.y + b.h : b.y;
      var k = Math.max(0.05, Math.max(Math.abs(p.x - ax) / b.w, Math.abs(p.y - ay) / b.h));
      g.items.forEach(function (el0) {
        var cur = getEl(el0.id);
        if (!cur) return;
        var sx = function (x) {
          return ax + (x - ax) * k;
        };
        var sy = function (y) {
          return ay + (y - ay) * k;
        };
        var f;
        if (el0.type === "line") {
          var res = G.resolveLine(el0, getEl);
          f = { points: [[sx(res.p0.x), sy(res.p0.y)], [sx(res.p1.x), sy(res.p1.y)]], bend: (el0.bend || 0) * k || undefined };
          if (el0.waypoints && el0.waypoints.length) {
            f.waypoints = el0.waypoints.map(function (q) {
              return [Math.round(sx(q[0]) * 10) / 10, Math.round(sy(q[1]) * 10) / 10];
            });
          }
        } else if (el0.type === "pen") {
          f = {
            x: sx(el0.x), y: sy(el0.y),
            points: (el0.points || []).map(function (q) {
              return [q[0] * k, q[1] * k];
            }),
          };
        } else if (BLOCKS[el0.type]) {
          f = { x: sx(el0.x), y: sy(el0.y) };
        } else {
          f = { x: sx(el0.x), y: sy(el0.y), w: el0.w * k, h: el0.h * k };
          if (TEXTUAL[el0.type] && el0.style && el0.style.fontSize) f.style = Object.assign({}, el0.style, { fontSize: Math.max(6, Math.round(el0.style.fontSize * k)) });
        }
        put(Object.assign({}, cur, f));
      });
    }

    var penPreview = null;
    function drawPenPreview() {
      var g = gesture;
      if (!g || g.kind !== "pen") return;
      if (!penPreview) {
        penPreview = document.createElement("div");
        penPreview.className = "lb-el lb-t-pen lb-pen-live";
        world.appendChild(penPreview);
      }
      var st = styleFor("pen");
      penPreview.innerHTML =
        '<svg class="lb-shape" width="1" height="1" overflow="visible"><path d="' + G.smoothPath(g.pts) +
        '" fill="none" stroke="' + inkVar(st.stroke) + '" stroke-width="' + st.strokeWidth + '" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }

    function onPointerUp(e) {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      var g = gesture;
      if (!g) return;
      if (g.kind === "pinch") {
        if (pointers.size < 2) {
          gesture = null;
          // Оставшийся палец — продолжаем панораму с текущего места.
          if (pointers.size === 1) {
            var rest = Array.from(pointers.entries())[0];
            gesture = { kind: "pan", rect: stageRect(), sx: rest[1].x, sy: rest[1].y, vx: view.x, vy: view.y, moved: true, pointerId: rest[0] };
          }
          schedule();
        }
        return;
      }
      if (e.pointerId !== g.pointerId) return;
      var p = toWorld(e.clientX, e.clientY, g.rect);
      endGesture(false, p, e);
    }

    function endGesture(cancelled, p, e) {
      stopAutoPan();
      var g = gesture;
      if (!g) return;
      gesture = null;
      guides = [];
      snapTarget = null;
      marquee = null;
      if (penPreview) {
        penPreview.remove();
        penPreview = null;
      }
      if (g.kind === "create") {
        var el = getEl(g.id);
        if (el && !g.moved) {
          var d = DEFAULTS[g.type];
          // Текст — от точки клика (как во Figma), фигура — по центру клика.
          put(Object.assign({}, el, { x: g.type === "text" ? g.p0.x : g.p0.x - d.w / 2, y: g.p0.y - d.h / 2, w: d.w, h: d.h }));
        } else if (el && (el.w < 12 || el.h < 12) && g.type !== "text") {
          var d2 = DEFAULTS[g.type];
          put(Object.assign({}, el, { w: Math.max(el.w, d2.w / 3), h: Math.max(el.h, d2.h / 3) }));
        }
        syncFrames();
        commit();
        setTool("select");
        if (el) {
          setSelection([g.id]);
          // В макете фигура — просто фигура (подпись не нужна); на доске блок сразу ждёт текст.
          if (!cancelled && (g.type === "text" || (mode !== "design" && (g.type === "sticky" || SHAPES[g.type])))) {
            // Новый блок сразу готов к вводу текста — без лишнего нажатия.
            startEdit(g.id, { isNew: true });
          }
        }
      } else if (g.kind === "end") {
        var ln = getEl(g.id);
        if (ln && g.created && !g.moved) {
          // Клик линией — отрезок по умолчанию вправо.
          var a = g.p0;
          put(Object.assign({}, ln, { points: [[a.x, a.y], [a.x + 160, a.y]] }));
        } else if (ln) {
          var res = G.resolveLine(ln, getEl);
          if (G.dist(res.p0, res.p1) < 4 && !ln.start && !ln.end) remove(ln.id);
        }
        commit();
        if (g.created) setTool("select");
        if (getEl(g.id)) setSelection([g.id]);
      } else if (g.kind === "pen") {
        var pts = G.simplify(g.pts, 0.8 / view.z);
        if (pts.length >= 2 || (g.pts.length && !cancelled)) {
          if (pts.length < 2) pts = [g.pts[0], G.add(g.pts[0], G.pt(0.1, 0))];
          var bb = G.boundsOf(pts);
          begin();
          var pen = newEl("pen", G.pt(bb.x, bb.y), {
            w: bb.w, h: bb.h,
            points: pts.map(function (q) {
              return [Math.round((q.x - bb.x) * 10) / 10, Math.round((q.y - bb.y) * 10) / 10];
            }),
          });
          put(pen);
          commit();
        }
      } else if (g.kind === "pathpt" || g.kind === "vhd") {
        if (penPath) writePen();
      } else if (g.kind === "vpt") {
        if (g.moved) writePen();
      } else if (g.kind === "connect") {
        if (!g.moved && !cancelled) quickAdd(g.from, g.side);
      } else if (g.kind === "label") {
        commit();
        if (!g.moved && e && !cancelled) onTap(g.id, p, e);
      } else if (g.kind === "wp" || g.kind === "seg") {
        // Нажали, но не потянули — изломы как были.
        if (!g.moved && g.inserted) patch(g.id, { waypoints: g.before, bend: g.bend0 });
        else tidyWaypoints(g.id);
        commit();
      } else if (g.kind === "gbar") {
        commit();
        if (!g.moved && e && !cancelled) onTap(g.id, p, e);
      } else if (g.kind === "move" || g.kind === "resize" || g.kind === "rotate" || g.kind === "bend" || g.kind === "gscale") {
        if (g.kind !== "bend") syncFrames();
        commit();
        if (g.kind === "move" && !g.moved && e && !cancelled) {
          if (g.toggleOff) {
            g.toggleOff.forEach(function (x) {
              selected.delete(x);
            });
          } else onTap(g.hit, p, e);
        }
      } else if (g.kind === "pan") {
        if (g.tapEmpty && !g.moved) {
          selected.clear();
          onTap("", p, e);
        }
      } else if (g.kind === "marquee") {
        if (!g.moved && e) onTap("", p, e);
      }
      schedule();
    }

    /** Двойное нажатие: по элементу — правка текста, по пустому месту — новый текст. */
    function onTap(id, p, e) {
      var blk = id && !readOnly && e && BLOCKS[(getEl(id) || {}).type] ? document.elementFromPoint(e.clientX, e.clientY) : null;
      var bnode = blk ? nodes.get(id) : null;
      if (blk && bnode && bnode.contains(blk)) {
        if (blk.closest(".lb-card-photo.is-empty")) {
          pickCardPhoto(id);
          return;
        }
        if (blk.closest("[data-gadd]")) {
          addTask(id);
          return;
        }
        var gcell = blk.closest(".lb-g-cell, .lb-g-bar");
        if (gcell) {
          gSel = { id: id, task: gcell.getAttribute("data-task") };
          markDirty(id);
        }
      }
      var now = Date.now();
      var dbl = now - lastTap.t < 380 && lastTap.id === id && Math.abs(e.clientX - lastTap.x) < 12 && Math.abs(e.clientY - lastTap.y) < 12;
      lastTap = { id: id, t: dbl ? 0 : now, x: e.clientX, y: e.clientY };
      if (!dbl || readOnly) return;
      if (id) {
        var deeper = pickDeeper(id);
        if (deeper) {
          setSelection([deeper]);
          return;
        }
        var el = getEl(id);
        // Двойной клик по объекту в группе — выделен он один (вход в группу, как во Figma); ещё раз — правка.
        if (el && el.group && selected.size > 1 && selected.has(id)) {
          selected = new Set([id]);
          schedule();
          return;
        }
        if (el && TEXTUAL[el.type]) startEdit(id, { selectAll: false, at: { clientX: e.clientX, clientY: e.clientY } });
        else if (el && (BLOCKS[el.type] || el.type === "frame")) startEdit(id, { at: e });
        else if (el && el.type === "vector" && mode === "design") editVector(id);
        return;
      }
      begin();
      var t = newEl("text", G.pt(p.x - 8, p.y - DEFAULTS.text.h / 2));
      put(t);
      syncFrames();
      commit();
      startEdit(t.id, { isNew: true });
    }

    /** «Плюс» у блока: копия блока сбоку, связанная стрелкой. */
    function quickAdd(fromId, side) {
      var from = getEl(fromId);
      if (!from) return;
      var gap = 90;
      var n = G.sideNormal(from, side);
      var horiz = side === "left" || side === "right";
      var dist = (horiz ? from.w : from.h) + gap;
      var c = G.add(G.center(from), G.mul(n, dist));
      var opp = { top: "bottom", bottom: "top", left: "right", right: "left" }[side];
      begin();
      var copy = {
        id: uid(), type: from.type === "text" || from.type === "image" || from.type === "table" || from.type === "gantt" || from.type === "vector" ? "rect" : from.type,
        x: c.x - from.w / 2, y: c.y - from.h / 2, w: from.w, h: from.h, z: topZ(),
        style: clone(from.style || styleFor("rect")),
      };
      if (from.rot) copy.rot = from.rot;
      if (copy.type === "sticky" && meAuthor()) copy.author = meAuthor();
      if (from.type === "text" || from.type === "image" || from.type === "table" || from.type === "gantt" || from.type === "vector") {
        copy.w = DEFAULTS.rect.w;
        copy.h = DEFAULTS.rect.h;
        copy.x = c.x - copy.w / 2;
        copy.y = c.y - copy.h / 2;
        copy.style = styleFor("rect");
      }
      put(copy);
      put(newEl("line", G.pt(0, 0), {
        points: [[0, 0], [1, 1]], start: { el: from.id, side: side }, end: { el: copy.id, side: opp },
        arrowEnd: true, route: lastStyle.lineRoute || "straight", z: topZ(),
      }));
      commit();
      setSelection([copy.id]);
      // Новый блок за краем экрана — подвинуть вид.
      var r = stage.getBoundingClientRect();
      var a = toScreen(G.pt(copy.x, copy.y));
      var b = toScreen(G.pt(copy.x + copy.w, copy.y + copy.h));
      if (a.x < 60 || a.y < 60 || b.x > r.width - 60 || b.y > r.height - 60) {
        var s = toScreen(G.center(copy));
        view.x += r.width / 2 - s.x;
        view.y += r.height / 2 - s.y;
        applyView();
      }
      startEdit(copy.id);
    }

    // ---- колесо и клавиши ----
    function onWheel(e) {
      e.preventDefault();
      autoFit = false;
      var r = stageRect();
      var k = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? r.height : 1;
      if (e.ctrlKey || e.metaKey) {
        // Щипок на трекпаде — мелкие шаги, колесо с Ctrl — крупные: ограничиваем шаг.
        var f = Math.exp(-clamp(e.deltaY * k, -40, 40) * 0.008);
        zoomAt(view.z * f, e.clientX - r.left, e.clientY - r.top);
      } else {
        view.x -= e.deltaX * k;
        view.y -= e.deltaY * k;
        applyView();
      }
    }

    function zoomAt(z, sx, sy) {
      z = clamp(z, MIN_Z, MAX_Z);
      var wx = (sx - view.x) / view.z;
      var wy = (sy - view.y) / view.z;
      view.z = z;
      view.x = sx - wx * z;
      view.y = sy - wy * z;
      applyView();
    }

    function zoomBy(f) {
      var r = stage.getBoundingClientRect();
      zoomAt(view.z * f, r.width / 2, r.height / 2);
    }

    function zoomTo(z) {
      var r = stage.getBoundingClientRect();
      zoomAt(z, r.width / 2, r.height / 2);
    }

    function contentBounds() {
      return G.unionBounds(Array.from(els.values()).map(function (el) {
        return G.elBounds(el, getEl);
      }));
    }

    function fitAll() {
      fitBounds(contentBounds());
    }

    function fitBounds(b) {
      var r = stage.getBoundingClientRect();
      if (!b || !r.width || !r.height) {
        view = { x: r.width / 2, y: r.height / 2, z: 1 };
        applyView();
        return;
      }
      var pad = 60;
      var z = clamp(Math.min((r.width - pad * 2) / Math.max(1, b.w), (r.height - pad * 2) / Math.max(1, b.h)), MIN_Z, 1);
      view.z = z;
      view.x = r.width / 2 - (b.x + b.w / 2) * z;
      view.y = r.height / 2 - (b.y + b.h / 2) * z;
      applyView();
    }

    function onKeyDown(e) {
      if (e.key === "Alt" && lastMove && !e.repeat) updateMeasure({ clientX: lastMove.clientX, clientY: lastMove.clientY, altKey: true });
      if (penPath && (e.key === "Enter" || e.key === "Escape")) {
        e.preventDefault();
        e.stopPropagation();
        // Первое нажатие — остановить рисование (точки можно править и продолжить), второе — выйти из правки.
        if (penPath.drawing) stopPenDrawing();
        else {
          exitPen();
          setTool("select");
        }
        return;
      }
      if (penPath && !penPath.drawing && (e.key === "Backspace" || e.key === "Delete") && penPath.sel) {
        e.preventDefault();
        e.stopPropagation();
        deletePenPoint();
        return;
      }
      if (editing || destroyed) return;
      var t = e.target;
      if (t && t !== stage && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      var mod = e.metaKey || e.ctrlKey;
      var k = e.key;
      var handled = true;
      if (k === " " && !mod) {
        spaceDown = true;
        stage.classList.add("is-panning");
      } else if (mod && (k === "z" || k === "Z" || k === "я" || k === "Я")) {
        if (readOnly) return;
        if (e.shiftKey) redo();
        else undo();
      } else if (mod && (k === "y" || k === "н")) {
        if (!readOnly) redo();
      } else if (mod && (k === "a" || k === "ф")) {
        setSelection(Array.from(els.keys()));
      } else if (mod && e.shiftKey && !e.altKey && e.code === "KeyC" && mode === "design") {
        copyForFigma();
      } else if (mod && (k === "d" || k === "в")) {
        if (!readOnly) duplicateSel();
      } else if (mod && (k === "g" || k === "п" || k === "G" || k === "П" || e.code === "KeyG")) {
        if (readOnly) return;
        if (e.shiftKey) ungroupSel();
        else groupSel();
      } else if ((k === "Delete" || k === "Backspace") && !readOnly) {
        deleteSel();
      } else if (k === "Escape") {
        if (tool !== "select") setTool("select");
        else {
          selected.clear();
          schedule();
        }
        closePop();
      } else if (k === "Enter" && !readOnly && selected.size === 1) {
        startEdit(Array.from(selected)[0], { selectAll: true });
      } else if (/^Arrow/.test(k) && selected.size && !readOnly) {
        var step = e.shiftKey ? 10 : 1;
        nudge(k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0, k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0);
      } else if ((k === "+" || k === "=") && !mod) {
        zoomBy(1.25);
      } else if ((k === "-" || k === "_") && !mod) {
        zoomBy(0.8);
      } else if (mode === "design" && e.shiftKey && !mod && !e.altKey && e.code === "KeyA" && selected.size && !readOnly) {
        addAutoLayout();
      } else if (mode === "design" && e.shiftKey && !mod && !e.altKey && (e.code === "KeyH" || e.code === "KeyV") && selected.size && !readOnly) {
        // Как во Figma: ⇧H — отразить по горизонтали, ⇧V — по вертикали.
        flipSel(e.code === "KeyH");
      } else if (k === "!" || (e.shiftKey && (e.code === "Digit1"))) {
        fitAll();
      } else if (e.shiftKey && e.code === "Digit0") {
        zoomTo(1);
      } else if (k === "]" && !readOnly) {
        reorderSel(true);
      } else if (k === "[" && !readOnly) {
        reorderSel(false);
      } else if (!mod && !e.altKey && k.length === 1 && !readOnly) {
        var byKey = toolList().find(function (x) {
          return x.key && (x.key === k.toLowerCase() || x.key === latinOf(e.code));
        });
        if (byKey && !(selected.size === 1 && e.shiftKey)) {
          setTool(byKey.id);
        } else if (selected.size === 1 && TEXTUAL[(getEl(Array.from(selected)[0]) || {}).type]) {
          // Набор по выделенному блоку — замена текста, как в Miro.
          startEdit(Array.from(selected)[0], { initial: k });
        } else handled = false;
      } else {
        handled = false;
      }
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    }

    function latinOf(code) {
      var m = /^Key([A-Z])$/.exec(code || "");
      return m ? m[1].toLowerCase() : "";
    }

    function onKeyUp(e) {
      if (e.key === " ") {
        spaceDown = false;
        stage.classList.remove("is-panning");
      }
      if (e.key === "Alt" && measure) {
        measure = null;
        renderOverlay();
      }
    }

    function onDrop(e) {
      if (readOnly) return;
      var files = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.files) || []).filter(function (f) {
        return /^image\//i.test(f.type || "");
      });
      if (!files.length) return;
      e.preventDefault();
      var p = toWorld(e.clientX, e.clientY);
      files.slice(0, 6).reduce(function (chain, f, i) {
        return chain.then(function () {
          return insertImageFile(f, G.pt(p.x + i * 30, p.y + i * 30));
        });
      }, Promise.resolve());
    }

    // ---- мини-карта ----
    function scheduleMinimap() {
      if (mapTimer || destroyed) return;
      mapTimer = setTimeout(function () {
        mapTimer = 0;
        drawMinimap();
      }, 120);
    }

    var mapXform = null;
    function drawMinimap() {
      if (!showMap || destroyed) return;
      var r = stage.getBoundingClientRect();
      if (!r.width) return;
      var vw = { x: -view.x / view.z, y: -view.y / view.z, w: r.width / view.z, h: r.height / view.z };
      var cb = contentBounds();
      var b = G.unionBounds([cb, vw]);
      var cw = minimap.width;
      var ch = minimap.height;
      var pad = 6;
      var k = Math.min((cw - pad * 2) / Math.max(1, b.w), (ch - pad * 2) / Math.max(1, b.h));
      var ox = pad + ((cw - pad * 2) - b.w * k) / 2 - b.x * k;
      var oy = pad + ((ch - pad * 2) - b.h * k) / 2 - b.y * k;
      mapXform = { k: k, ox: ox, oy: oy };
      var ctx = minimap.getContext("2d");
      ctx.clearRect(0, 0, cw, ch);
      var cs = getComputedStyle(root);
      var ink = cs.getPropertyValue("--lb-map-ink").trim() || "#888";
      sortedEls().forEach(function (el) {
        var eb = G.elBounds(el, getEl);
        if (!eb) return;
        ctx.fillStyle = el.type === "sticky" ? cs.getPropertyValue("--lb-sticky-" + ((el.style && el.style.fill) || "yellow")).trim() || ink : ink;
        if (el.type === "line" || el.type === "pen") {
          ctx.strokeStyle = ink;
          ctx.lineWidth = 1;
          ctx.strokeRect(ox + eb.x * k, oy + eb.y * k, Math.max(1, eb.w * k), Math.max(1, eb.h * k));
        } else {
          ctx.globalAlpha = el.type === "sticky" ? 0.9 : 0.55;
          ctx.fillRect(ox + eb.x * k, oy + eb.y * k, Math.max(1.5, eb.w * k), Math.max(1.5, eb.h * k));
          ctx.globalAlpha = 1;
        }
      });
      ctx.strokeStyle = cs.getPropertyValue("--lb-accent").trim() || "#529ef4";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(ox + vw.x * k, oy + vw.y * k, vw.w * k, vw.h * k);
    }

    function onMapPointer(e) {
      if (!mapXform) return;
      autoFit = false;
      e.preventDefault();
      e.stopPropagation();
      var move = function (ev) {
        var mr = minimap.getBoundingClientRect();
        var mx = ((ev.clientX - mr.left) * minimap.width) / mr.width;
        var my = ((ev.clientY - mr.top) * minimap.height) / mr.height;
        var wx = (mx - mapXform.ox) / mapXform.k;
        var wy = (my - mapXform.oy) / mapXform.k;
        var r = stage.getBoundingClientRect();
        view.x = r.width / 2 - wx * view.z;
        view.y = r.height / 2 - wy * view.z;
        applyView();
      };
      move(e);
      var up = function () {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }

    // ---- экспорт ----
    /** Экспорт — в светлых цветах (файл открывают где угодно). */
    function exportColors() {
      var wasLight = root.classList.contains("is-light");
      root.classList.add("is-light");
      var cs = getComputedStyle(root);
      var snap = {};
      ["--lb-bg", "--lb-font"].concat(
        INK.concat(["white"]).map(function (k) {
          return "--lb-ink-" + k;
        }),
        FILLS.map(function (k) {
          return "--lb-fill-" + k;
        }),
        STICKY.map(function (k) {
          return "--lb-sticky-" + k;
        })
      ).forEach(function (n) {
        snap[n] = cs.getPropertyValue(n).trim();
      });
      if (!wasLight) root.classList.remove("is-light");
      cs = {
        getPropertyValue: function (n) {
          return snap[n] || "";
        },
      };
      var get = function (name, fb) {
        return cs.getPropertyValue(name).trim() || fb;
      };
      return {
        bg: get("--lb-bg", "#ffffff"),
        ink: function (k) {
          return isHex(k) ? k : get("--lb-ink-" + (k || "ink"), "#222");
        },
        fill: function (k) {
          return !k || k === "none" ? null : isHex(k) ? k : get("--lb-fill-" + k, "#fff");
        },
        sticky: function (k) {
          return isHex(k) ? k : get("--lb-sticky-" + (k || "yellow"), "#fff3a3");
        },
        font: get("--lb-font", "system-ui, sans-serif"),
      };
    }

    function wrapLines(ctx, text, maxW) {
      var out = [];
      String(text || "").split("\n").forEach(function (para) {
        var words = para.split(/(\s+)/);
        var line = "";
        words.forEach(function (w) {
          var test = line + w;
          if (line && ctx.measureText(test).width > maxW && w.trim()) {
            out.push(line.replace(/\s+$/, ""));
            line = w.replace(/^\s+/, "");
            while (ctx.measureText(line).width > maxW && line.length > 1) {
              var cut = line.length - 1;
              while (cut > 1 && ctx.measureText(line.slice(0, cut)).width > maxW) cut--;
              out.push(line.slice(0, cut));
              line = line.slice(cut);
            }
          } else line = test;
        });
        out.push(line);
      });
      return out;
    }

    /** Раскладка подписи: строки и их положение — общая для PNG и SVG. */
    function textLayout(ctx, el, colors) {
      if (!el.text || el.type === "image") return null;
      var st = el.style || {};
      var fs = Number(st.fontSize) || 16;
      var fam = fontStack(st.fontFamily) || colors.font;
      var wt = st.fontWeight || (st.bold ? 600 : 400);
      ctx.font = wt + " " + fs + "px " + fam;
      if ("letterSpacing" in ctx) ctx.letterSpacing = st.letterSpacing ? (Number(st.letterSpacing) / 100) * fs + "px" : "0px";
      var lh = Number(st.lineHeight) > 0 ? Number(st.lineHeight) : fs * 1.3;
      var tbx = TEXT_BOX[el.type];
      var padX = el.type === "text" ? 0 : tbx ? 16 + Math.max(tbx[0], tbx[2]) * el.w : el.type === "sticky" ? 14 : 20;
      var maxW;
      var cx;
      var top;
      var align;
      if (el.type === "line") {
        var lp = linePathOf(el);
        var lm = G.labelPoint(el, lp);
        maxW = 220;
        var lines0 = wrapLines(ctx, el.text, maxW);
        return { lines: lines0, x: lm.x, y: lm.y - ((lines0.length - 1) * lh) / 2, lh: lh, align: "center", fs: fs, label: true };
      }
      maxW = Math.max(10, el.w - padX * 2);
      var lines = wrapLines(ctx, casedText(el.text, st.textCase), maxW);
      align = alignOf(el);
      cx = align === "center" ? el.x + el.w / 2 : align === "right" ? el.x + el.w - padX : el.x + padX;
      var pad = el.type === "text" ? 0 : 8;
      if (el.type === "text" || st.valign === "top") top = el.y + pad + lh / 2;
      else if (st.valign === "bottom") top = el.y + el.h - pad - lh / 2 - (lines.length - 1) * lh;
      else {
        var capT = el.type === "cylinder" ? G.capOf(el.w, el.h) : 0;
        var mid = tbx ? el.y + (tbx[1] * el.h + el.h - tbx[3] * el.h) / 2 : el.y + el.h / 2 + capT / 2;
        top = mid - ((lines.length - 1) * lh) / 2;
      }
      return { lines: lines, x: cx, y: top, lh: lh, align: align, fs: fs, fam: fam, wt: wt, ls: st.letterSpacing ? (Number(st.letterSpacing) / 100) * fs : 0, deco: st.deco };
    }

    /** Блок для экспорта: прямоугольники, тексты и фото по тому, как он нарисован сейчас (координаты холста). */
    function blockPrims(el, colors, measure) {
      var node = nodes.get(el.id);
      var out = [];
      if (!node) return out;
      var nr = node.getBoundingClientRect();
      var z = view.z || 1;
      var line = "#d0d5dd";
      function box(c) {
        var r = c.getBoundingClientRect();
        return { x: el.x + (r.left - nr.left) / z, y: el.y + (r.top - nr.top) / z, w: r.width / z, h: r.height / z };
      }
      function text(c, b, opt) {
        var t = String(c.innerText || c.textContent || "").trim();
        if (!t || c.classList.contains("is-empty")) return;
        var cs = getComputedStyle(c);
        var fs = parseFloat(cs.fontSize) || 14;
        var weight = parseInt(cs.fontWeight, 10) >= 600 ? "600" : "";
        var align = opt && opt.align ? opt.align : cs.textAlign === "center" ? "center" : cs.textAlign === "right" ? "right" : "left";
        var pad = opt && opt.pad != null ? opt.pad : 8;
        measure.font = (weight ? "600 " : "") + fs + "px " + colors.font;
        var lines = wrapLines(measure, t, Math.max(10, b.w - pad * 2));
        var lh = fs * 1.3;
        var x = align === "center" ? b.x + b.w / 2 : align === "right" ? b.x + b.w - pad : b.x + pad;
        var y0 = opt && opt.middle ? b.y + b.h / 2 - ((lines.length - 1) * lh) / 2 : b.y + pad / 2 + lh / 2 + 2;
        out.push({ k: "text", lines: lines, x: x, y: y0, lh: lh, fs: fs, weight: weight, align: align, color: (opt && opt.color) || colors.ink("ink") });
      }
      if (el.type === "table") {
        node.querySelectorAll(".note-editor-table th, .note-editor-table td").forEach(function (c) {
          var b = box(c);
          var fill = c.tagName === "TH" ? colors.fill("gray") : c.hasAttribute("data-highlight") ? colors.fill("yellow") : colors.fill("white");
          out.push({ k: "rect", x: b.x, y: b.y, w: b.w, h: b.h, fill: fill, stroke: line });
          text(c, b);
        });
      } else if (el.type === "card") {
        var st = el.style || {};
        out.push({ k: "rect", x: el.x, y: el.y, w: el.w, h: el.h, r: 10, fill: colors.fill("white"), stroke: line });
        var head = node.querySelector(".lb-card-head");
        if (head) {
          var hb = box(head);
          out.push({ k: "rect", x: hb.x, y: hb.y, w: hb.w, h: hb.h, r: 10, rTop: true, fill: colors.fill(st.fill || "purple") });
          text(head, hb, { align: "center", middle: true });
        }
        var ph = node.querySelector(".lb-card-photo");
        if (ph) {
          var pb = box(ph);
          if (el.photo) out.push({ k: "img", x: pb.x, y: pb.y, w: pb.w, h: pb.h, src: el.photo, circle: true, key: el.id + ":photo" });
          else out.push({ k: "rect", x: pb.x, y: pb.y, w: pb.w, h: pb.h, r: pb.w / 2, fill: colors.fill("gray") });
        }
        [".lb-card-name", ".lb-card-about"].forEach(function (sel, i) {
          var c = node.querySelector(sel);
          if (c) text(c, box(c), { align: "center", pad: 4, color: i ? colors.ink("gray") : colors.ink("ink") });
        });
      } else if (el.type === "gantt") {
        out.push({ k: "rect", x: el.x, y: el.y, w: el.w, h: el.h, r: 8, fill: colors.fill("white"), stroke: line });
        node.querySelectorAll(".lb-g-col.is-off").forEach(function (c) {
          var b = box(c);
          out.push({ k: "rect", x: b.x, y: b.y, w: b.w, h: b.h, fill: colors.fill("gray") });
        });
        node.querySelectorAll(".lb-g-row").forEach(function (c) {
          var b = box(c);
          out.push({ k: "rect", x: el.x, y: b.y + b.h - 0.5, w: el.w, h: 0.5, fill: line });
        });
        var hd = node.querySelector(".lb-g-head");
        if (hd) {
          var hdb = box(hd);
          out.push({ k: "rect", x: el.x, y: hdb.y + hdb.h - 0.5, w: el.w, h: 0.5, fill: line });
        }
        node.querySelectorAll(".lb-g-hcell, .lb-g-month, .lb-g-tick, .lb-g-cell").forEach(function (c) {
          var b = box(c);
          var tick = c.classList.contains("lb-g-tick");
          text(c, b, { pad: tick ? 0 : 8, align: tick ? "center" : "left", middle: !c.classList.contains("lb-g-month"), color: c.classList.contains("lb-g-cell") ? colors.ink("ink") : colors.ink("gray") });
        });
        node.querySelectorAll(".lb-g-bar").forEach(function (c) {
          var b = box(c);
          var t = (el.tasks || []).filter(function (x) {
            return x.id === c.getAttribute("data-task");
          })[0];
          out.push({ k: "rect", x: b.x, y: b.y, w: b.w, h: b.h, r: 4, fill: colors.ink((t && t.color) || "blue"), alpha: 0.8 });
        });
        var now = node.querySelector(".lb-g-now");
        if (now) {
          var nb = box(now);
          out.push({ k: "rect", x: nb.x, y: nb.y, w: Math.max(1.5, nb.w), h: nb.h, fill: colors.ink("red") });
        }
      }
      return out;
    }

    function drawPrims(ctx, prims, images) {
      prims.forEach(function (q) {
        ctx.save();
        if (q.k === "rect") {
          var path = new Path2D();
          if (q.r && path.roundRect) path.roundRect(q.x, q.y, q.w, q.h, q.rTop ? [q.r, q.r, 0, 0] : q.r);
          else path.rect(q.x, q.y, q.w, q.h);
          if (q.alpha) ctx.globalAlpha = q.alpha;
          if (q.fill) {
            ctx.fillStyle = q.fill;
            ctx.fill(path);
          }
          ctx.globalAlpha = 1;
          if (q.stroke) {
            ctx.strokeStyle = q.stroke;
            ctx.lineWidth = 1;
            ctx.stroke(path);
          }
        } else if (q.k === "img") {
          var img = images[q.key];
          if (img) {
            if (q.circle) {
              ctx.beginPath();
              ctx.arc(q.x + q.w / 2, q.y + q.h / 2, Math.min(q.w, q.h) / 2, 0, Math.PI * 2);
              ctx.clip();
            }
            var s = Math.max(q.w / img.width, q.h / img.height);
            ctx.drawImage(img, q.x + (q.w - img.width * s) / 2, q.y + (q.h - img.height * s) / 2, img.width * s, img.height * s);
          }
        } else if (q.k === "text") {
          ctx.fillStyle = q.color;
          ctx.font = (q.weight ? "600 " : "") + q.fs + "px " + exportFont;
          ctx.textAlign = q.align;
          ctx.textBaseline = "middle";
          q.lines.forEach(function (l, i) {
            ctx.fillText(l, q.x, q.y + i * q.lh);
          });
        }
        ctx.restore();
      });
    }

    function primsSvg(prims, uidBase) {
      return prims.map(function (q, i) {
        if (q.k === "rect") {
          var rx = q.r ? ' rx="' + q.r + '"' : "";
          var body = q.rTop && q.r
            ? '<path d="M' + q.x + "," + (q.y + q.h) + "V" + (q.y + q.r) + "a" + q.r + "," + q.r + " 0 0 1 " + q.r + ",-" + q.r + "h" + (q.w - 2 * q.r) + "a" + q.r + "," + q.r + " 0 0 1 " + q.r + "," + q.r + "V" + (q.y + q.h) + 'Z"'
            : '<rect x="' + q.x + '" y="' + q.y + '" width="' + q.w + '" height="' + q.h + '"' + rx;
          return body + ' fill="' + (q.fill || "none") + '"' + (q.alpha ? ' fill-opacity="' + q.alpha + '"' : "") + (q.stroke ? ' stroke="' + q.stroke + '"' : "") + "/>";
        }
        if (q.k === "img") {
          var cid = uidBase + "c" + i;
          return (q.circle ? '<clipPath id="' + cid + '"><circle cx="' + (q.x + q.w / 2) + '" cy="' + (q.y + q.h / 2) + '" r="' + Math.min(q.w, q.h) / 2 + '"/></clipPath>' : "") +
            '<image x="' + q.x + '" y="' + q.y + '" width="' + q.w + '" height="' + q.h + '" preserveAspectRatio="xMidYMid slice" href="' + esc(q.src) + '"' + (q.circle ? ' clip-path="url(#' + cid + ')"' : "") + "/>";
        }
        return '<text font-size="' + q.fs + '"' + (q.weight ? ' font-weight="600"' : "") + ' fill="' + q.color + '" text-anchor="' + (q.align === "center" ? "middle" : q.align === "right" ? "end" : "start") + '" dominant-baseline="central">' +
          q.lines.map(function (l, j) {
            return '<tspan x="' + q.x + '" y="' + (q.y + j * q.lh) + '">' + esc(l) + "</tspan>";
          }).join("") + "</text>";
      }).join("");
    }

    var exportFont = "system-ui, sans-serif";

    function drawScene(ctx, colors, images) {
      exportFont = colors.font;
      var measure = document.createElement("canvas").getContext("2d");
      sortedEls().forEach(function (el) {
        if (BLOCKS[el.type]) {
          drawPrims(ctx, blockPrims(el, colors, measure), images);
          return;
        }
        var st = el.style || {};
        var sw = el.type === "line" || el.type === "pen" ? Number(st.strokeWidth) || 2 : st.strokeOff ? 0 : strokeW(st, el.type === "frame" ? 0 : 2);
        ctx.save();
        if (el.rot && BOXED[el.type]) {
          var c = G.center(el);
          ctx.translate(c.x, c.y);
          ctx.rotate((el.rot * Math.PI) / 180);
          ctx.translate(-c.x, -c.y);
        }
        var dash = dashArray(st.dash, sw);
        ctx.setLineDash(dash ? dash.split(" ").map(Number) : []);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        if (el.type === "line" || el.type === "pen") {
          var d;
          var res = null;
          var lp = null;
          if (el.type === "line") {
            res = G.resolveLine(el, getEl);
            lp = linePathOf(el, res);
            d = lp.d;
          } else {
            d = G.smoothPath((el.points || []).map(function (q) {
              return G.pt(el.x + q[0], el.y + q[1]);
            }));
            sw = Number(st.strokeWidth) || 3;
          }
          ctx.strokeStyle = colors.ink(st.stroke);
          ctx.lineWidth = sw;
          ctx.stroke(new Path2D(d));
          if (lp) {
            ctx.setLineDash([]);
            ctx.fillStyle = colors.ink(st.stroke);
            var size = 8 + sw * 2.5;
            [[el.arrowEnd, res.p1, lp.endDir], [el.arrowStart, res.p0, lp.startDir]].forEach(function (a) {
              if (!a[0]) return;
              var pts = G.arrowHead(a[1], a[2], size);
              ctx.beginPath();
              ctx.moveTo(pts[0].x, pts[0].y);
              ctx.lineTo(pts[1].x, pts[1].y);
              ctx.lineTo(pts[2].x, pts[2].y);
              ctx.closePath();
              ctx.fill();
            });
          }
        } else if (el.type === "image") {
          var img = images[el.id];
          if (img) ctx.drawImage(img, el.x, el.y, el.w, el.h);
        } else if (el.type === "vector") {
          var vbx = el.vb || [el.w, el.h];
          var vst = el.style || {};
          ctx.translate(el.x, el.y);
          ctx.scale(el.w / Math.max(0.01, vbx[0]), el.h / Math.max(0.01, vbx[1]));
          ctx.setLineDash([]);
          (el.paths || []).forEach(function (p) {
            var path2 = new Path2D(p.d);
            if (p.fill) {
              ctx.fillStyle = colors.fill(vst.fill || p.fill) || "transparent";
              ctx.fill(path2, p.rule === "evenodd" ? "evenodd" : "nonzero");
            }
            if (p.stroke) {
              ctx.strokeStyle = colors.ink(vst.stroke || p.stroke);
              ctx.lineWidth = vst.strokeWidth != null ? Number(vst.strokeWidth) : p.sw || 1;
              ctx.lineCap = p.cap || "butt";
              ctx.lineJoin = p.join || "miter";
              ctx.stroke(path2);
            }
          });
        } else {
          var path = new Path2D();
          if (el.type === "ellipse") path.ellipse(el.x + el.w / 2, el.y + el.h / 2, el.w / 2, el.h / 2, 0, 0, Math.PI * 2);
          else if (el.type === "diamond" || FLOW.indexOf(el.type) >= 0) {
            var spc = G.shapePathD(el.type, el.w, el.h, el.x, el.y);
            path.addPath(new Path2D(spc.d));
            if (spc.line) path.addPath(new Path2D(spc.line));
          } else if (el.type === "rect" || el.type === "sticky" || el.type === "text" || el.type === "frame") {
            var rr = el.type === "sticky" ? 3 : el.type === "text" ? 2 : radiiOf(st, el.w, el.h, el.type === "frame" ? 0 : undefined);
            if (path.roundRect) path.roundRect(el.x, el.y, el.w, el.h, rr);
            else path.rect(el.x, el.y, el.w, el.h);
          }
          if (el.type === "sticky") {
            ctx.shadowColor = "rgba(0,0,0,0.18)";
            ctx.shadowBlur = 8;
            ctx.shadowOffsetY = 2;
            ctx.fillStyle = colors.sticky(st.fill);
            ctx.fill(path);
            ctx.shadowColor = "transparent";
          } else {
            var fill = colors.fill(st.fill || (el.type === "frame" ? "#ffffff" : null));
            if (fill) {
              ctx.fillStyle = fill;
              ctx.fill(path);
            }
            if (el.type !== "text" && sw > 0) {
              ctx.strokeStyle = colors.ink(st.stroke);
              ctx.lineWidth = sw;
              ctx.stroke(path);
            }
          }
        }
        ctx.setLineDash([]);
        var tl = textLayout(ctx, el, colors);
        if (tl) {
          ctx.fillStyle = el.type === "sticky" ? "#1f2328" : autoTextColor(el) || colors.ink(st.textColor || "ink");
          ctx.textAlign = tl.align === "center" || tl.align === "right" ? tl.align : "left";
          ctx.textBaseline = "middle";
          if (tl.label) {
            var wmax = 0;
            tl.lines.forEach(function (l) {
              wmax = Math.max(wmax, ctx.measureText(l).width);
            });
            ctx.save();
            ctx.fillStyle = colors.bg;
            ctx.fillRect(tl.x - wmax / 2 - 4, tl.y - tl.lh / 2, wmax + 8, tl.lh * tl.lines.length);
            ctx.restore();
          }
          if (isHex(st.highlight)) {
            ctx.save();
            ctx.fillStyle = st.highlight;
            tl.lines.forEach(function (l, i) {
              var lw = ctx.measureText(l).width;
              var lx = tl.align === "center" ? tl.x - lw / 2 : tl.align === "right" ? tl.x - lw : tl.x;
              ctx.fillRect(lx - 2, tl.y + i * tl.lh - tl.lh / 2, lw + 4, tl.lh);
            });
            ctx.restore();
          }
          tl.lines.forEach(function (l, i) {
            ctx.fillText(l, tl.x, tl.y + i * tl.lh);
          });
        }
        ctx.restore();
      });
    }

    function loadImages() {
      var out = {};
      var jobs = [];
      els.forEach(function (el) {
        if (el.type === "image" && el.src) {
          jobs.push(loadImage(el.src).then(function (img) {
            out[el.id] = img;
          }));
        }
        if (el.type === "card" && el.photo) {
          jobs.push(loadImage(el.photo).then(function (img) {
            out[el.id + ":photo"] = img;
          }));
        }
      });
      return Promise.all(jobs).then(function () {
        return out;
      });
    }

    function exportName(ext) {
      var t = String(o.title || "Доска").replace(/[\\/:*?"<>|]+/g, " ").trim() || "Доска";
      return t + "." + ext;
    }

    function exportPng() {
      var b = contentBounds();
      if (!b) {
        notify("Доска пуста");
        return Promise.resolve();
      }
      var pad = 40;
      var scale = Math.min(2, 8000 / Math.max(b.w + pad * 2, b.h + pad * 2));
      var colors = exportColors();
      return loadImages().then(function (images) {
        var canvas = document.createElement("canvas");
        canvas.width = Math.ceil((b.w + pad * 2) * scale);
        canvas.height = Math.ceil((b.h + pad * 2) * scale);
        var ctx = canvas.getContext("2d");
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.scale(scale, scale);
        ctx.translate(pad - b.x, pad - b.y);
        drawScene(ctx, colors, images);
        return new Promise(function (resolve) {
          canvas.toBlob(function (blob) {
            if (blob) downloadBlob(blob, exportName("png"));
            else notify("Не удалось сохранить PNG");
            resolve();
          }, "image/png");
        });
      });
    }

    function exportSvg() {
      var b = contentBounds();
      if (!b) {
        notify("Доска пуста");
        return;
      }
      var pad = 40;
      var colors = exportColors();
      var measure = document.createElement("canvas").getContext("2d");
      var out = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + (b.x - pad) + " " + (b.y - pad) + " " + (b.w + pad * 2) + " " + (b.h + pad * 2) +
        '" width="' + Math.ceil(b.w + pad * 2) + '" height="' + Math.ceil(b.h + pad * 2) + '" font-family="' + esc(colors.font) + '">',
        '<rect x="' + (b.x - pad) + '" y="' + (b.y - pad) + '" width="' + (b.w + pad * 2) + '" height="' + (b.h + pad * 2) + '" fill="' + colors.bg + '"/>',
      ];
      sortedEls().forEach(function (el) {
        if (BLOCKS[el.type]) {
          out.push("<g>" + primsSvg(blockPrims(el, colors, measure), "b" + el.id) + "</g>");
          return;
        }
        var st = el.style || {};
        var sw = el.type === "line" || el.type === "pen" ? Number(st.strokeWidth) || 2 : st.strokeOff ? 0 : strokeW(st, el.type === "frame" ? 0 : 2);
        var stroke = colors.ink(st.stroke);
        var dash = dashArray(st.dash, sw);
        var da = dash ? ' stroke-dasharray="' + dash + '"' : "";
        var g = el.rot && BOXED[el.type] ? '<g transform="rotate(' + el.rot + " " + (el.x + el.w / 2) + " " + (el.y + el.h / 2) + ')">' : "<g>";
        out.push(g);
        if (el.type === "line") {
          var res = G.resolveLine(el, getEl);
          var lp = linePathOf(el, res);
          out.push('<path d="' + lp.d + '" fill="none" stroke="' + stroke + '" stroke-width="' + sw + '"' + da + ' stroke-linecap="round" stroke-linejoin="round"/>');
          var size = 8 + sw * 2.5;
          if (el.arrowEnd) out.push(headPoly(G.arrowHead(res.p1, lp.endDir, size), stroke));
          if (el.arrowStart) out.push(headPoly(G.arrowHead(res.p0, lp.startDir, size), stroke));
        } else if (el.type === "pen") {
          var d = G.smoothPath((el.points || []).map(function (q) {
            return G.pt(el.x + q[0], el.y + q[1]);
          }));
          out.push('<path d="' + d + '" fill="none" stroke="' + stroke + '" stroke-width="' + (Number(st.strokeWidth) || 3) + '" stroke-linecap="round" stroke-linejoin="round"/>');
        } else if (el.type === "image") {
          out.push('<image x="' + el.x + '" y="' + el.y + '" width="' + el.w + '" height="' + el.h + '" preserveAspectRatio="none" href="' + esc(el.src || "") + '"/>');
        } else if (el.type === "vector") {
          var vbs = el.vb || [el.w, el.h];
          out.push('<svg x="' + el.x + '" y="' + el.y + '" width="' + el.w + '" height="' + el.h + '" viewBox="0 0 ' + vbs[0] + " " + vbs[1] + '" preserveAspectRatio="none" overflow="visible">' +
            vectorPaths(el, { fill: function (k) {
              return colors.fill(k) || "none";
            }, ink: colors.ink }) + "</svg>");
        } else {
          var fill = el.type === "sticky" ? colors.sticky(st.fill) : colors.fill(st.fill) || "none";
          var strokeAttr = el.type === "sticky" || el.type === "text" || sw <= 0 ? ' stroke="none"' : ' stroke="' + stroke + '" stroke-width="' + sw + '"' + da;
          if (el.type === "ellipse") out.push('<ellipse cx="' + (el.x + el.w / 2) + '" cy="' + (el.y + el.h / 2) + '" rx="' + el.w / 2 + '" ry="' + el.h / 2 + '" fill="' + fill + '"' + strokeAttr + "/>");
          else if (el.type === "diamond" || FLOW.indexOf(el.type) >= 0) {
            var spx = G.shapePathD(el.type, el.w, el.h, el.x, el.y);
            out.push('<path d="' + spx.d + '" fill="' + fill + '"' + strokeAttr + ' stroke-linejoin="round"/>');
            if (spx.line && sw > 0) out.push('<path d="' + spx.line + '" fill="none"' + strokeAttr + "/>");
          }
          else if (el.type === "rect") out.push('<path d="' + roundedPathD(el.x, el.y, el.w, el.h, radiiOf(st, el.w, el.h)) + '" fill="' + fill + '"' + strokeAttr + "/>");
          else if (el.type === "frame") out.push('<path d="' + roundedPathD(el.x, el.y, el.w, el.h, radiiOf(st, el.w, el.h, 0)) + '" fill="' + (colors.fill(st.fill || "#ffffff") || "#ffffff") + '"' + strokeAttr + "/>");
          else if (el.type !== "text" || fill !== "none") out.push('<rect x="' + el.x + '" y="' + el.y + '" width="' + el.w + '" height="' + el.h + '" rx="' + (el.type === "sticky" ? 3 : 2) + '" fill="' + fill + '"' + strokeAttr + "/>");
        }
        var tl = textLayout(measure, el, colors);
        if (tl) {
          var color = el.type === "sticky" ? "#1f2328" : autoTextColor(el) || colors.ink(st.textColor || "ink");
          if (tl.label) {
            var wmax = 0;
            tl.lines.forEach(function (l) {
              wmax = Math.max(wmax, measure.measureText(l).width);
            });
            out.push('<rect x="' + (tl.x - wmax / 2 - 4) + '" y="' + (tl.y - tl.lh / 2) + '" width="' + (wmax + 8) + '" height="' + tl.lh * tl.lines.length + '" fill="' + colors.bg + '"/>');
          }
          if (isHex(st.highlight)) {
            measure.font = (st.bold ? "600 " : "") + tl.fs + "px " + colors.font;
            tl.lines.forEach(function (l, i) {
              var lw = measure.measureText(l).width;
              var lx = tl.align === "center" ? tl.x - lw / 2 : tl.align === "right" ? tl.x - lw : tl.x;
              out.push('<rect x="' + (lx - 2) + '" y="' + (tl.y + i * tl.lh - tl.lh / 2) + '" width="' + (lw + 4) + '" height="' + tl.lh + '" fill="' + st.highlight + '"/>');
            });
          }
          out.push(
            '<text font-size="' + tl.fs + '"' + (st.bold ? ' font-weight="600"' : "") + ' fill="' + color + '" text-anchor="' + (tl.align === "center" ? "middle" : tl.align === "right" ? "end" : "start") + '" dominant-baseline="central">' +
            tl.lines.map(function (l, i) {
              return '<tspan x="' + tl.x + '" y="' + (tl.y + i * tl.lh) + '">' + esc(l) + "</tspan>";
            }).join("") + "</text>"
          );
        }
        out.push("</g>");
      });
      out.push("</svg>");
      downloadBlob(new Blob([out.join("")], { type: "image/svg+xml" }), exportName("svg"));
    }

    // ---- экспорт выделенного (макет): SVG / PNG / JPEG / PDF ----
    /** Переменные темы → цвета светлой темы (файл не знает про CSS-переменные доски). */
    function resolveVars(str, colors) {
      return String(str).replace(/var\(--lb-(ink|fill|sticky)-([\w-]+)\)/g, function (_, kind, k) {
        return kind === "ink" ? colors.ink(k) : kind === "fill" ? colors.fill(k) || "none" : colors.sticky(k);
      }).replace(/var\(--[\w-]+\)/g, "#000000");
    }

    /** SVG выделенного: те же фигуры, что на экране (градиенты, обводка внутрь/наружу, тень и размытие — фильтрами).
     * opts.figma — тексты по обычной базовой линии: импорт SVG во Figma не знает dominant-baseline. */
    function selectionSvg(ids, opts) {
      opts = opts || {};
      var set = {};
      withFrameKids(ids).forEach(function (id) {
        set[id] = 1;
      });
      var list = sortedEls().filter(function (el) {
        return set[el.id] && !isHidden(el);
      });
      if (!list.length) return null;
      var top = ids.map(getEl).filter(Boolean);
      // Один экран — ровно по его рамке (вылезающее за край обрезано, как на экране).
      var b = top.length === 1 && top[0].type === "frame" && !top[0].rot ? { x: top[0].x, y: top[0].y, w: top[0].w, h: top[0].h } : G.unionBounds(list.map(function (el) {
        return G.elBounds(el, getEl);
      }));
      if (!b) return null;
      var colors = exportColors();
      var measure = document.createElement("canvas").getContext("2d");
      var defs = [];
      var out = [];
      list.forEach(function (el) {
        var st = el.style || {};
        var attrs = "";
        var op = st.opacity != null && Number(st.opacity) < 1 ? Number(st.opacity) : 1;
        if (op < 1) attrs += ' opacity="' + op + '"';
        var sh = SHADOW_RE.exec(String(st.shadow || ""));
        if (sh || Number(st.blur) > 0) {
          var fid = "fx-" + el.id;
          defs.push('<filter id="' + fid + '" x="-50%" y="-50%" width="200%" height="200%">' +
            (Number(st.blur) > 0 ? '<feGaussianBlur in="SourceGraphic" stdDeviation="' + Number(st.blur) / 2 + '" result="b"/>' : "") +
            (sh ? '<feDropShadow dx="' + sh[1] + '" dy="' + sh[2] + '" stdDeviation="' + sh[3] / 2 + '" flood-color="' + sh[4].slice(0, 7) + '" flood-opacity="' +
              Math.round((parseInt(sh[4].slice(7), 16) / 255) * 100) / 100 + '"' + (Number(st.blur) > 0 ? ' in="b"' : "") + "/>" : "") + "</filter>");
          attrs += ' filter="url(#' + fid + ')"';
        }
        // Содержимое экрана обрезается по экрану.
        // Ближайший внешний фрейм с обрезкой (без флажка «Обрезать» — пропускаем).
        var f = frameOf(el);
        while (f && f.noclip) f = frameOf(f);
        if (f && set[f.id] && !f.rot && !el.rot) {
          var cid = "clip-" + f.id;
          if (!defs.some(function (d) {
            return d.indexOf('id="' + cid + '"') >= 0;
          })) defs.push('<clipPath id="' + cid + '"><rect x="' + f.x + '" y="' + f.y + '" width="' + f.w + '" height="' + f.h + '"/></clipPath>');
          attrs += ' clip-path="url(#' + cid + ')"';
        }
        var rot = el.rot && BOXED[el.type] ? ' transform="rotate(' + el.rot + " " + (el.x + el.w / 2) + " " + (el.y + el.h / 2) + ')"' : "";
        out.push("<g" + attrs + "><g" + rot + ">");
        if (BLOCKS[el.type]) {
          out.push(primsSvg(blockPrims(el, colors, measure), "b" + el.id));
        } else if (el.type === "line" || el.type === "pen" || el.type === "image") {
          var sw = Number(st.strokeWidth) || 2;
          var stroke = colors.ink(st.stroke);
          if (el.type === "line") {
            var res = G.resolveLine(el, getEl);
            var lp = linePathOf(el, res);
            var dash = dashArray(st.dash, sw);
            out.push('<path d="' + lp.d + '" fill="none" stroke="' + stroke + '" stroke-width="' + sw + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : "") + ' stroke-linecap="round" stroke-linejoin="round"/>');
            var size = 8 + sw * 2.5;
            if (el.arrowEnd) out.push(headPoly(G.arrowHead(res.p1, lp.endDir, size), stroke));
            if (el.arrowStart) out.push(headPoly(G.arrowHead(res.p0, lp.startDir, size), stroke));
          } else if (el.type === "pen") {
            out.push('<path d="' + G.smoothPath((el.points || []).map(function (q) {
              return G.pt(el.x + q[0], el.y + q[1]);
            })) + '" fill="none" stroke="' + stroke + '" stroke-width="' + (Number(st.strokeWidth) || 3) + '" stroke-linecap="round" stroke-linejoin="round"/>');
          } else {
            out.push('<image x="' + el.x + '" y="' + el.y + '" width="' + el.w + '" height="' + el.h + '" preserveAspectRatio="none" href="' + esc(el.src || "") + '"/>');
          }
        } else {
          var shape = el.type === "frame" ? frameShapeSvg(el) : shapeSvg(el);
          // Вложенный <svg> экрана/фигуры — на место элемента; классы и hit-слои файлу не мешают.
          out.push('<g transform="translate(' + el.x + " " + el.y + ')">' + resolveVars(shape, colors).replace(/ class="[^"]*"/g, "").replace(/ pointer-events="[^"]*"/g, "") + "</g>");
        }
        var tl = el.type !== "frame" && textLayout(measure, el, colors);
        if (tl) {
          var color = el.type === "sticky" ? "#1f2328" : autoTextColor(el) || colors.ink(st.textColor || "ink");
          // Для Figma: центр строки → базовая линия (середина em-квадрата над ней; шрифт уже выставлен в textLayout).
          var dy = 0;
          if (opts.figma) {
            var tm = measure.measureText("Hg");
            dy = tm.emHeightAscent != null ? (tm.emHeightAscent - tm.emHeightDescent) / 2 :
              tm.fontBoundingBoxAscent != null ? (tm.fontBoundingBoxAscent - tm.fontBoundingBoxDescent) / 2 : tl.fs * 0.35;
            dy = Math.round(dy * 100) / 100;
          }
          out.push('<text font-size="' + tl.fs + '" font-family="' + esc(tl.fam) + '"' + (tl.wt !== 400 ? ' font-weight="' + tl.wt + '"' : "") +
            (tl.ls ? ' letter-spacing="' + Math.round(tl.ls * 100) / 100 + '"' : "") + (tl.deco ? ' text-decoration="' + (tl.deco === "strike" ? "line-through" : "underline") + '"' : "") +
            ' fill="' + color + '" text-anchor="' + (tl.align === "center" ? "middle" : tl.align === "right" ? "end" : "start") +
            '"' + (opts.figma ? "" : ' dominant-baseline="central"') + ">" + tl.lines.map(function (l, i) {
              return '<tspan x="' + tl.x + '" y="' + (Math.round((tl.y + i * tl.lh + dy) * 100) / 100) + '">' + esc(l) + "</tspan>";
            }).join("") + "</text>");
        }
        out.push("</g></g>");
      });
      var w = Math.max(1, Math.round(b.w * 100) / 100);
      var h = Math.max(1, Math.round(b.h * 100) / 100);
      var svg = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="' + b.x + " " + b.y + " " + w + " " + h + '" width="' + w + '" height="' + h +
        '" font-family="' + esc(colors.font) + '">' + (defs.length ? "<defs>" + defs.join("") + "</defs>" : "") + out.join("") + "</svg>";
      var fams = {};
      list.forEach(function (el) {
        var f = el.style && el.style.fontFamily;
        if (f && FONT_RE.test(f) && el.text) fams[f] = 1;
      });
      return { svg: svg, w: w, h: h, fonts: Object.keys(fams) };
    }

    /** Шрифты внутрь SVG (@font-face с data:) — иначе картинка из SVG рисует текст системным шрифтом. */
    function embedFonts(sv) {
      if (!sv.fonts || !sv.fonts.length || !window.fetch) return Promise.resolve(sv);
      var toData = function (url) {
        return fetch(url).then(function (r) {
          return r.blob();
        }).then(function (b) {
          return new Promise(function (res) {
            var fr = new FileReader();
            fr.onload = function () {
              res(fr.result);
            };
            fr.onerror = function () {
              res("");
            };
            fr.readAsDataURL(b);
          });
        });
      };
      return Promise.all(sv.fonts.map(function (f) {
        var specs = fontSpecs(f);
        var getCss = function (i) {
          if (i >= specs.length) return Promise.resolve("");
          return fetch("https://fonts.googleapis.com/css2?family=" + encodeURIComponent(f).replace(/%20/g, "+") + (specs[i] ? ":wght@" + specs[i] : "") + "&display=swap").then(function (r) {
            return r.ok ? r.text() : getCss(i + 1);
          });
        };
        return getCss(0).then(function (css) {
          // Только кириллица и латиница: остальные наборы знаков тексту макета не нужны, а файл раздувают.
          var blocks = css.split("/* ").filter(function (b) {
            return /^(cyrillic|latin) \*\//.test(b);
          });
          return Promise.all(blocks.map(function (b) {
            var m = /url\((https:[^)]+)\)/.exec(b);
            if (!m) return "";
            return toData(m[1]).then(function (data) {
              return data ? b.replace(/^[^*]*\*\/\s*/, "").replace(m[1], data) : "";
            });
          }));
        }).catch(function () {
          return [];
        });
      })).then(function (parts) {
        var css = [].concat.apply([], parts).join("\n");
        if (!css) return sv;
        return Object.assign({}, sv, { svg: sv.svg.replace(/(<svg[^>]*>)/, "$1<style>" + css.replace(/</g, "") + "</style>") });
      }).catch(function () {
        return sv;
      });
    }

    function exportBaseName() {
      var one = selList().length === 1 ? selList()[0] : null;
      var t = one && one.type === "frame" ? one.name || "Фрейм" : one && one.text ? String(one.text).split("\n")[0].slice(0, 40) : o.title || "Макет";
      return String(t).replace(/[\\/:*?"<>|]+/g, " ").trim() || "Макет";
    }

    /** SVG → картинка на холсте (масштаб k); JPEG — на белом. */
    function svgToCanvas(sv, k, opaque) {
      return new Promise(function (resolve, reject) {
        var img = new Image();
        var url = URL.createObjectURL(new Blob([sv.svg], { type: "image/svg+xml" }));
        img.onload = function () {
          var c = document.createElement("canvas");
          c.width = Math.max(1, Math.round(sv.w * k));
          c.height = Math.max(1, Math.round(sv.h * k));
          var ctx = c.getContext("2d");
          if (opaque) {
            ctx.fillStyle = "#ffffff";
            ctx.fillRect(0, 0, c.width, c.height);
          }
          ctx.drawImage(img, 0, 0, c.width, c.height);
          URL.revokeObjectURL(url);
          resolve(c);
        };
        img.onerror = function () {
          URL.revokeObjectURL(url);
          reject(new Error("svg"));
        };
        img.src = url;
      });
    }

    /** Простейший PDF: одна страница размером с объект, картинка JPEG на всю страницу. */
    function jpegToPdf(bytes, pxW, pxH, ptW, ptH) {
      var enc = new TextEncoder();
      var parts = [];
      var offs = [];
      var len = 0;
      var push = function (x) {
        var b = typeof x === "string" ? enc.encode(x) : x;
        parts.push(b);
        len += b.length;
      };
      var obj = function (n, body) {
        offs[n] = len;
        push(n + " 0 obj\n" + body + "\nendobj\n");
      };
      push("%PDF-1.4\n");
      obj(1, "<< /Type /Catalog /Pages 2 0 R >>");
      obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>");
      obj(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + ptW + " " + ptH + "] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>");
      offs[4] = len;
      push("4 0 obj\n<< /Type /XObject /Subtype /Image /Width " + pxW + " /Height " + pxH + " /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length " + bytes.length + " >>\nstream\n");
      push(bytes);
      push("\nendstream\nendobj\n");
      var content = "q " + ptW + " 0 0 " + ptH + " 0 0 cm /Im0 Do Q";
      obj(5, "<< /Length " + content.length + " >>\nstream\n" + content + "\nendstream");
      var xref = len;
      var x = "xref\n0 6\n0000000000 65535 f \n";
      for (var i = 1; i <= 5; i++) x += ("0000000000" + offs[i]).slice(-10) + " 00000 n \n";
      push(x + "trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF");
      return new Blob(parts, { type: "application/pdf" });
    }

    /** Выделенное — в буфер SVG-кодом: Figma вставляет его векторами и текстовыми слоями (без автолэйаута). */
    function copyForFigma() {
      var ids = Array.from(selected);
      var sv = ids.length ? selectionSvg(ids, { figma: true }) : null;
      if (!sv) return notify("Выделите объект или фрейм");
      // Figma ждёт одно имя шрифта (не CSS-стек; системный → Inter) и не знает fill="transparent".
      var svg = sv.svg.replace(/font-family="([^"]*)"/g, function (_, stack) {
        var first = stack.split(",")[0].replace(/&quot;|['"]/g, "").trim();
        return 'font-family="' + (!first || /^(-apple-system|system-ui|sans-serif|BlinkMacSystemFont)$/i.test(first) ? "Inter" : first) + '"';
      }).replace(/(fill|stroke)="transparent"/g, '$1="none"');
      copyText(svg, "Скопировано — вставьте в Figma (⌘V)");
    }

    function exportSelection(fmt, k) {
      var ids = Array.from(selected);
      var sv = ids.length ? selectionSvg(ids) : null;
      if (!sv) {
        notify("Выделите объект или фрейм");
        return;
      }
      var name = exportBaseName();
      if (fmt === "svg") {
        downloadBlob(new Blob([sv.svg], { type: "image/svg+xml" }), name + ".svg");
        return;
      }
      embedFonts(sv).then(function (sv2) {
        return svgToCanvas(sv2, fmt === "pdf" ? Math.max(2, k) : k, fmt !== "png");
      }).then(function (c) {
        var type = fmt === "png" ? "image/png" : "image/jpeg";
        c.toBlob(function (blob) {
          if (!blob) return notify("Не удалось сохранить файл");
          if (fmt !== "pdf") {
            downloadBlob(blob, name + (fmt === "png" ? ".png" : ".jpg"));
            return;
          }
          blob.arrayBuffer().then(function (buf) {
            downloadBlob(jpegToPdf(new Uint8Array(buf), c.width, c.height, Math.round(sv.w * 0.75 * 100) / 100, Math.round(sv.h * 0.75 * 100) / 100), name + ".pdf");
          });
        }, type, 0.92);
      }).catch(function () {
        notify("Не удалось сохранить файл");
      });
    }

    // ---- документ ----
    function getJson() {
      var elements = {};
      sortedEls().forEach(function (el) {
        elements[el.id] = serializeEl(el, getEl, pathOptsOf);
      });
      return JSON.stringify(mode === "design" ? { v: 1, mode: "design", elements: elements } : { v: 1, elements: elements });
    }

    function loadElements(list) {
      finishEdit();
      var keep = new Set();
      var nextIds = {};
      list.forEach(function (el) {
        nextIds[el.id] = 1;
      });
      els.forEach(function (_, id) {
        if (!nextIds[id]) dirty.add(id);
      });
      els = new Map();
      list.forEach(function (el) {
        els.set(el.id, el);
        dirty.add(el.id);
        if (selected.has(el.id)) keep.add(el.id);
      });
      selected = keep;
      orderDirty = true;
      schedule();
    }

    function setJson(json, keepView) {
      // В совместном режиме источник — документ Yjs: чужой JSON с сервера его не заменяет.
      if (yjs && keepView) return;
      if (!keepView) autoFit = true;
      applyMode(modeOf(json));
      loadElements(parseBoard(json));
      undoStack = [];
      redoStack = [];
      renderHistoryBar();
      if (!keepView) {
        flushNow();
        fitAll();
      }
    }

    // ---- Yjs: каждый элемент — запись в Y.Map("elements") ----
    function yElements(doc) {
      return doc.getMap("elements");
    }

    function attachYjs(doc, Y) {
      detachYjs();
      var map = yElements(doc);
      var observer = function (events, tr) {
        if (tr.origin === "leo-board-local") return;
        var ids = {};
        events.forEach(function (ev) {
          if (ev.target === map) ev.keysChanged.forEach(function (k) {
            ids[k] = 1;
          });
          else {
            var p = ev.path && ev.path[0];
            if (p) ids[p] = 1;
          }
        });
        Object.keys(ids).forEach(function (id) {
          var ym = map.get(id);
          var val = ym && typeof ym.toJSON === "function" ? ym.toJSON() : null;
          if (val && val.type) {
            val.id = id;
            if (!els.has(id) || (els.get(id).z || 0) !== (val.z || 0)) orderDirty = true;
            els.set(id, val);
          } else {
            els.delete(id);
            selected.delete(id);
            orderDirty = true;
          }
          dirty.add(id);
        });
        schedule();
      };
      map.observeDeep(observer);
      var meta = doc.getMap("meta");
      var metaObserver = function (ev, tr) {
        if (tr.origin === "leo-board-local") return;
        if (ev.keysChanged.has("mode")) applyMode(meta.get("mode"));
      };
      meta.observe(metaObserver);
      if (meta.has("mode")) applyMode(meta.get("mode"));
      else if (mode === "design") doc.transact(function () {
        meta.set("mode", "design");
      }, "leo-board-local");
      yjs = { doc: doc, map: map, Y: Y, observer: observer, meta: meta, metaObserver: metaObserver };
      root.classList.add("is-live");
      // Документ уже есть — показываем его; пустой — заполняем своим.
      if (map.size) {
        var list = [];
        map.forEach(function (ym, id) {
          var val = ym && typeof ym.toJSON === "function" ? ym.toJSON() : null;
          if (val && val.type) {
            val.id = id;
            list.push(val);
          }
        });
        loadElements(list);
      } else if (els.size) {
        els.forEach(function (_, id) {
          pendingY.add(id);
        });
        flushY();
      }
    }

    function detachYjs() {
      if (!yjs) return;
      try {
        yjs.map.unobserveDeep(yjs.observer);
        if (yjs.meta) yjs.meta.unobserve(yjs.metaObserver);
      } catch (_) {}
      yjs = null;
      pendingY.clear();
      root.classList.remove("is-live");
    }

    function scheduleYFlush() {
      if (yFlushHandle) return;
      yFlushHandle = setTimeout(function () {
        yFlushHandle = 0;
        flushY();
      }, 40);
    }

    function flushY() {
      if (!yjs || !pendingY.size) return;
      var Y = yjs.Y;
      var map = yjs.map;
      var ids = Array.from(pendingY);
      pendingY.clear();
      yjs.doc.transact(function () {
        ids.forEach(function (id) {
          var el = els.get(id);
          if (!el) {
            if (map.has(id)) map.delete(id);
            return;
          }
          var ser = serializeEl(el, getEl, pathOptsOf);
          delete ser.id;
          delete ser.d;
          if (el.type === "line") ser.points = el.points;
          writeEl(Y, map, id, ser);
        });
      }, "leo-board-local");
    }

    function setRemoteCursors(list) {
      remoteCursors = Array.isArray(list) ? list : [];
      renderOverlay();
    }

    // ---- события ----
    function onResize() {
      // Вид ещё не трогали — вписываем заново: при открытии размер панели приходит не сразу.
      if (autoFit) fitAll();
      renderOverlay();
      renderProps();
      scheduleMinimap();
    }

    stage.addEventListener("pointerdown", onPointerDown);
    stage.addEventListener("pointermove", onPointerMove);
    stage.addEventListener("pointerleave", function () {
      if (gapHover) {
        gapHover = null;
        renderOverlay();
      }
    });
    stage.addEventListener("pointerup", onPointerUp);
    stage.addEventListener("pointercancel", onPointerUp);
    stage.addEventListener("wheel", onWheel, { passive: false });
    // Горизонтальный свайп над панелями доски (инструменты, слои, свойства, миникарта) — не «назад» в браузере:
    // гасим, если под пальцами нет своей горизонтальной прокрутки, которой есть куда ехать.
    root.addEventListener("wheel", function (e) {
      if (e.defaultPrevented || Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
      for (var q = e.target; q && q !== root; q = q.parentElement) {
        if (q.scrollWidth > q.clientWidth + 1) {
          var ox = getComputedStyle(q).overflowX;
          if ((ox === "auto" || ox === "scroll") &&
            (e.deltaX < 0 ? q.scrollLeft > 0 : q.scrollLeft + q.clientWidth < q.scrollWidth - 1)) return;
        }
      }
      e.preventDefault();
    }, { passive: false });
    stage.addEventListener("keydown", onKeyDown);
    stage.addEventListener("keyup", onKeyUp);
    stage.addEventListener("copy", onCopy);
    stage.addEventListener("cut", onCut);
    stage.addEventListener("paste", onPaste);
    stage.addEventListener("dragover", function (e) {
      if (!readOnly) e.preventDefault();
    });
    stage.addEventListener("drop", onDrop);
    // Мышь по доске не выделяет текст страницы (иначе двойной клик выделял слово и уводил курсор
    // из только что созданного блока); внутри правки — как обычно.
    function keepNativeSelection(e) {
      if (editing && editing.text.contains(e.target)) return;
      e.preventDefault();
    }
    stage.addEventListener("mousedown", keepNativeSelection);
    stage.addEventListener("dblclick", keepNativeSelection);
    stage.addEventListener("contextmenu", function (e) {
      if (!editing) e.preventDefault();
    });
    // Жест «назад» и масштаб страницы в Safari.
    stage.addEventListener("gesturestart", function (e) {
      e.preventDefault();
    });
    function onWinBlur() {
      spaceDown = false;
      stage.classList.remove("is-panning");
    }
    window.addEventListener("blur", onWinBlur);
    propsBar.addEventListener("click", onPropsClick);
    propsBar.addEventListener("pointerdown", function (e) {
      e.stopPropagation();
    });
    pop.addEventListener("pointerdown", function (e) {
      e.stopPropagation();
    });
    zoomBar.addEventListener("click", onBarClick);
    if (historyBar) historyBar.addEventListener("click", onBarClick);
    minimap.addEventListener("pointerdown", onMapPointer);
    var ro = typeof ResizeObserver === "function" ? new ResizeObserver(onResize) : null;
    if (ro) ro.observe(stage);
    document.addEventListener("pointerdown", onDocPointer, true);

    function onDocPointer(e) {
      if (!pop.classList.contains("hidden") && !pop.contains(e.target) && !(pop._anchor && pop._anchor.contains(e.target))) closePop();
      boardActive = root.contains(e.target);
    }

    // ⌘Z / ⇧⌘Z / ⌘Y — работают, пока последним трогали доску, даже если фокус ушёл на кнопку панели или в никуда.
    var boardActive = false;
    function isEditable(t) {
      return !!(t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)));
    }
    function onDocKey(e) {
      if (destroyed || readOnly || !boardActive || e.defaultPrevented) return;
      var t = e.target;
      if (t === stage || isEditable(t) || editing) return; // доска и поля ввода — сами
      if (t && t.closest && !root.contains(t) && t !== document.body && t !== document.documentElement) return;
      var mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      var k = e.key;
      if (k === "z" || k === "Z" || k === "я" || k === "Я" || e.code === "KeyZ") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (k === "y" || k === "н" || e.code === "KeyY") {
        e.preventDefault();
        redo();
      }
    }
    document.addEventListener("keydown", onDocKey);

    // Клик по кнопке панели (не по полю ввода) — фокус обратно на доску: горячие клавиши работают сразу.
    root.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || isEditable(t) || stage.contains(t) || editing) return;
      if (t.closest && t.closest("label, .lb-pop input, .lb-lname[contenteditable=true]")) return;
      setTimeout(function () {
        var ae = document.activeElement;
        if (!destroyed && !editing && !isEditable(ae)) stage.focus({ preventScroll: true });
      }, 0);
    }, true);

    root.classList.add(mode === "design" ? "is-mode-design" : "is-mode-board");
    renderEmptyHint();
    emptyHint.addEventListener("click", function (e) {
      var c = e.target.closest("[data-tpl]");
      if (c) openTemplateMenu(c, c.getAttribute("data-tpl"));
      var d = e.target.closest("[data-device]");
      if (d) insertFrame(d.getAttribute("data-device"));
    });
    renderTools();
    renderHistoryBar();
    loadElements(parseBoard(o.json));
    var hadView = loadView();
    flushNow();
    if (hadView) applyView();
    else {
      // Размер хоста может появиться позже (вкладка только что показана) — см. onResize.
      autoFit = true;
      fitAll();
    }

    return {
      root: root,
      getJson: getJson,
      getHtml: getJson,
      setJson: function (json) {
        setJson(json, false);
      },
      setJsonKeepView: function (json) {
        setJson(json, true);
      },
      fitAll: fitAll,
      insertTemplate: insertTemplate,
      // Редактор таблиц заметки догрузился — таблицы доски переподключаются к нему.
      refreshBlocks: function () {
        els.forEach(function (el) {
          if (el.type === "table" && !tables.has(el.id)) {
            var n = nodes.get(el.id);
            var host = n && n.querySelector(":scope > .lb-table");
            if (host) host.removeAttribute("data-src");
            dirty.add(el.id);
          }
        });
        schedule();
      },
      exportPng: exportPng,
      exportSvg: exportSvg,
      attachYjs: attachYjs,
      detachYjs: detachYjs,
      isYjsAttached: function () {
        return !!yjs;
      },
      setRemoteCursors: setRemoteCursors,
      hasElement: function (id) {
        return !!getEl(id);
      },
      revealElement: revealElement,
      setChatBadge: function (v) {
        chatBadge = String(v || "");
        renderZoomBar();
      },
      setTexts: setTexts,
      getSelection: selectionInfo,
      getMode: function () {
        return mode;
      },
      // Лео нарисовал экраны: пересобрать их автолэйаут по реальным текстам и показать первый.
      // Элементы могут прийти позже (через Yjs) — ждём их до 5 с.
      relayoutFrames: function (ids, reveal) {
        var tries = 0;
        (function go() {
          if (destroyed) return;
          var have = (ids || []).filter(function (id) {
            return !!getEl(id);
          });
          if (!have.length && tries++ < 25) return setTimeout(go, 200);
          if (!have.length || tx) return;
          // Два прохода: после первого подгоняются высоты текстов (рендер), второй раскладывает начисто.
          relayout(have);
          setTimeout(function () {
            if (!tx && !destroyed) relayout(have.filter(getEl));
            if (reveal && getEl(reveal)) revealElement(reveal);
          }, 120);
        })();
      },
      isEditing: function () {
        return !!editing || !!gesture;
      },
      focus: function () {
        stage.focus({ preventScroll: true });
      },
      destroy: function () {
        if (destroyed) return;
        finishEdit();
        if (changeTimer) {
          clearTimeout(changeTimer);
          emitChange();
        }
        flushY();
        destroyed = true;
        detachYjs();
        if (frame) cancelAnimationFrame(frame);
        if (mapTimer) clearTimeout(mapTimer);
        if (ro) ro.disconnect();
        document.removeEventListener("pointerdown", onDocPointer, true);
        document.removeEventListener("keydown", onDocKey);
        window.removeEventListener("blur", onWinBlur);
        if (inspector) {
          document.removeEventListener("pointerup", inspUp, true);
          document.removeEventListener("pointercancel", inspUp, true);
        }
        if (saveViewTimer) clearTimeout(saveViewTimer);
        Array.from(tables.keys()).forEach(dropTable);
        root.remove();
      },
    };
  }

  // ---- документ Yjs: Y.Map("elements"), элемент — вложенный Y.Map полей ----
  function docFields(el) {
    var f = clone(el);
    delete f.id;
    delete f.d;
    return f;
  }

  function writeEl(Y, map, id, fields) {
    var ym = map.get(id);
    if (!ym || typeof ym.set !== "function") {
      ym = new Y.Map();
      map.set(id, ym);
    }
    var keys = {};
    Object.keys(fields).forEach(function (k) {
      if (fields[k] === undefined) return;
      keys[k] = 1;
      if (!same(ym.get(k), fields[k])) ym.set(k, fields[k]);
    });
    ym.forEach(function (_, k) {
      if (!keys[k]) ym.delete(k);
    });
  }

  /** Посев: JSON листа → документ (первое открытие совместного холста). */
  function seedDoc(Y, doc, json, origin) {
    var map = doc.getMap("elements");
    doc.transact(function () {
      parseBoard(json).forEach(function (el) {
        writeEl(Y, map, el.id, docFields(el));
      });
      if (modeOf(json) === "design") doc.getMap("meta").set("mode", "design");
    }, origin);
  }

  /** Правка мимо Yjs (было → стало): в документ — только изменённые элементы, чужие правки остаются. */
  function applyDelta(Y, doc, oldJson, newJson, origin) {
    var before = {};
    var after = {};
    parseBoard(oldJson).forEach(function (el) {
      before[el.id] = docFields(el);
    });
    parseBoard(newJson).forEach(function (el) {
      after[el.id] = docFields(el);
    });
    var map = doc.getMap("elements");
    doc.transact(function () {
      Object.keys(before).concat(Object.keys(after)).forEach(function (id) {
        if (same(before[id], after[id])) return;
        if (!after[id]) {
          if (map.has(id)) map.delete(id);
          return;
        }
        // Поле за полем: меняем только то, что поменяла правка.
        var b = before[id] || {};
        var a = after[id];
        var ym = map.get(id);
        if (!ym || typeof ym.set !== "function") {
          writeEl(Y, map, id, a);
          return;
        }
        Object.keys(a).forEach(function (k) {
          if (!same(a[k], b[k])) ym.set(k, a[k]);
        });
        Object.keys(b).forEach(function (k) {
          if (!(k in a)) ym.delete(k);
        });
      });
      var m0 = modeOf(oldJson);
      var m1 = modeOf(newJson);
      // Режим меняют кнопкой (через meta). Тело без пометки (правка мимо Yjs, старый сервер) «Макет» в «Доску» не превращает.
      if (m0 !== m1 && m1 === "design") doc.getMap("meta").set("mode", m1);
    }, origin);
  }

  var SHAPE_RU = { rect: "блок", diamond: "ромб/решение", ellipse: "овал", sticky: "стикер", text: "текст", image: "картинка" };
  var COLOR_RU = {
    red: "красный", orange: "оранжевый", yellow: "жёлтый", green: "зелёный", blue: "синий",
    purple: "фиолетовый", pink: "розовый", gray: "серый",
  };

  function oneLine(t) {
    return String(t || "").replace(/\s+/g, " ").trim();
  }

  /** Описание доски для Лео — как board_doc.board_describe на сервере: блоки, связи, группы, рисунки. */
  function describeBoard(json, title) {
    var list = parseBoard(json);
    if (!list.length) return "Доска пустая.";
    var byId = {};
    list.forEach(function (el) {
      byId[el.id] = el;
    });
    var shapes = list.filter(function (el) {
      return BOXED[el.type];
    }).sort(function (a, b) {
      return Math.round(a.y / 40) - Math.round(b.y / 40) || a.x - b.x;
    });
    var num = {};
    shapes.forEach(function (el, i) {
      num[el.id] = i + 1;
    });
    function name(id) {
      var t = oneLine((byId[id] || {}).text);
      return num[id] ? "#" + num[id] + (t ? " «" + t + "»" : "") : "?";
    }
    var out = [title ? "Доска «" + title + "»:" : "Доска:", "Блоки (сверху вниз, слева направо):"];
    shapes.forEach(function (el) {
      var st = el.style || {};
      var color = el.type === "sticky" ? st.fill : st.fill && st.fill !== "none" ? st.fill : st.stroke;
      var kind = SHAPE_RU[el.type] || el.type;
      if (COLOR_RU[color]) kind += ", " + COLOR_RU[color];
      if (el.type === "sticky" && el.author && el.author.name) kind += ", автор " + el.author.name;
      out.push(("- #" + num[el.id] + " [" + kind + "] " + oneLine(el.text)).replace(/\s+$/, ""));
    });
    var links = [];
    var free = 0;
    list.forEach(function (el) {
      if (el.type !== "line") return;
      var a = el.start && el.start.el;
      var b = el.end && el.end.el;
      var label = oneLine(el.text);
      if (num[a] && num[b]) {
        var arrow = el.arrowEnd ? "→" : "—";
        if (el.arrowStart && el.arrowEnd) arrow = "↔";
        else if (el.arrowStart) {
          var t = a;
          a = b;
          b = t;
          arrow = "→";
        }
        var dashed = el.style && el.style.dash ? "пунктир" : "";
        var extra = label ? " («" + label + "»" + (dashed ? ", " + dashed : "") + ")" : dashed ? " (" + dashed + ")" : "";
        links.push("- " + name(a) + " " + arrow + " " + name(b) + extra);
      } else {
        free++;
        if (label) links.push("- линия с подписью «" + label + "»");
      }
    });
    if (links.length) out = out.concat(["Связи:"], links);
    var groups = {};
    shapes.forEach(function (el) {
      if (el.group) (groups[el.group] = groups[el.group] || []).push(name(el.id));
    });
    Object.keys(groups).forEach(function (g) {
      if (groups[g].length > 1) out.push("Группа: " + groups[g].join(", "));
    });
    var pens = list.filter(function (el) {
      return el.type === "pen";
    }).length;
    var extras = [];
    if (pens) extras.push("рисунков карандашом: " + pens);
    if (free) extras.push("линий без привязки: " + free);
    if (extras.length) out.push("Ещё на доске: " + extras.join(", ") + " (их содержимое текстом не описать).");
    return out.join("\n");
  }

  window.LeoBoard = {
    create: create,
    parse: parseBoard,
    describe: describeBoard,
    seedDoc: seedDoc,
    applyDelta: applyDelta,
    emptyJson: function () {
      return JSON.stringify({ v: 1, elements: {} });
    },
    plainText: function (json) {
      return parseBoard(json)
        .filter(function (el) {
          return el.text;
        })
        .sort(function (a, b) {
          return Math.round(a.y / 40) - Math.round(b.y / 40) || a.x - b.x;
        })
        .map(function (el) {
          return String(el.text);
        })
        .join("\n");
    },
  };
})();
