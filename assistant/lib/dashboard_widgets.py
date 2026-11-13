"""Каталог виджетов дашбордов — закрытый список, как блоки презентаций (canvas_blocks).

Агент (через MCP) и человек (в приложении) выбирают только тип из WIDGET_TYPES и опции из его схемы.
Неизвестные опции отбрасываются, свои цвета/HTML/CSS не принимаются — вид задаёт Орбита.
Каждый тип знает, какой вид источника ему нужен: metric (числа во времени, необязательная серия),
table (строки с колонками), events (время + текст + ссылка) или никакого (text).
"""

from __future__ import annotations

import re
from typing import Any, Optional

DATASET_KINDS = ("metric", "table", "events")
COLUMN_TYPES = ("text", "number", "money", "percent", "date", "status", "progress", "link", "person")
PERIODS = ("7d", "30d", "90d", "365d")
DEFAULT_PERIOD = "30d"
GRID_COLUMNS = 4

_AGG = ("sum", "avg", "last", "min", "max", "count")
_FORMAT = ("number", "money", "percent", "count")
_STEP = ("auto", "hour", "day", "week", "month")
_PERIOD_OPT = ("dash",) + PERIODS
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")

# Спецификации опций: (вид, ...параметры, по умолчанию)
#   ("enum", choices, default) | ("int", lo, hi, default) | ("num", default) | ("bool", default)
#   ("str", maxlen, default) | ("field", required) | ("fields", max, required) | ("series", max)
_COMMON_METRIC = {
    "period": ("enum", _PERIOD_OPT, "dash"),
    "format": ("enum", _FORMAT, "number"),
    "unit": ("str", 12, ""),
}

WIDGET_TYPES: dict[str, dict[str, Any]] = {
    "number": {
        "label": "Число",
        "about": "Одно главное число за период, изменение к прошлому такому же периоду и маленький график.",
        "sources": ("metric",),
        "min_width": 1,
        "options": {
            **_COMMON_METRIC,
            "agg": ("enum", _AGG, "sum"),
            "series": ("str", 60, ""),
            "good": ("enum", ("up", "down"), "up"),
            "spark": ("bool", True),
        },
    },
    "goal": {
        "label": "Цель",
        "about": "Прогресс к цели: сколько набрано из плана за месяц/квартал/год.",
        "sources": ("metric",),
        "min_width": 1,
        "options": {
            "target": ("num", None),
            "goal_period": ("enum", ("month", "quarter", "year", "dash"), "month"),
            "agg": ("enum", _AGG, "sum"),
            "series": ("str", 60, ""),
            "format": ("enum", _FORMAT, "number"),
            "unit": ("str", 12, ""),
        },
        "required": ("target",),
    },
    "line": {
        "label": "Линия",
        "about": "Динамика во времени, до 5 серий; можно линию цели.",
        "sources": ("metric",),
        "min_width": 2,
        "options": {
            **_COMMON_METRIC,
            "step": ("enum", _STEP, "auto"),
            "agg": ("enum", _AGG, "sum"),
            "series": ("series", 5),
            "target": ("num", None),
        },
    },
    "bars": {
        "label": "Столбцы",
        "about": "Значения по дням/неделям/месяцам столбцами; стопкой по сериям, линия цели.",
        "sources": ("metric",),
        "min_width": 2,
        "options": {
            **_COMMON_METRIC,
            "step": ("enum", _STEP, "auto"),
            "agg": ("enum", _AGG, "sum"),
            "series": ("series", 5),
            "stacked": ("bool", False),
            "target": ("num", None),
            "highlight_last": ("bool", True),
        },
    },
    "area": {
        "label": "Область",
        "about": "Как линия, но с заливкой; одна серия или стопкой.",
        "sources": ("metric",),
        "min_width": 2,
        "options": {
            **_COMMON_METRIC,
            "step": ("enum", _STEP, "auto"),
            "agg": ("enum", _AGG, "sum"),
            "series": ("series", 5),
            "stacked": ("bool", False),
        },
    },
    "stacked100": {
        "label": "Доли во времени",
        "about": "Как менялась доля каждой серии: каждый столбец — 100%.",
        "sources": ("metric",),
        "min_width": 2,
        "options": {
            "period": ("enum", _PERIOD_OPT, "dash"),
            "step": ("enum", _STEP, "auto"),
            "series": ("series", 6),
        },
    },
    "ranking": {
        "label": "Рейтинг",
        "about": "Горизонтальные полосы от большего к меньшему: менеджеры, товары, города.",
        "sources": ("metric", "table"),
        "min_width": 1,
        "options": {
            **_COMMON_METRIC,
            "agg": ("enum", _AGG, "sum"),
            "top": ("int", 1, 15, 10),
            "label_field": ("field", False),
            "value_field": ("field", False),
        },
        "table_required": ("label_field", "value_field"),
    },
    "share": {
        "label": "Доли",
        "about": "Кольцо: из чего складывается целое, до 6 частей, остальное — «Прочее».",
        "sources": ("metric", "table"),
        "min_width": 1,
        "options": {
            **_COMMON_METRIC,
            "agg": ("enum", _AGG, "sum"),
            "max_parts": ("int", 2, 6, 6),
            "label_field": ("field", False),
            "value_field": ("field", False),
        },
        "table_required": ("label_field", "value_field"),
    },
    "sparkgrid": {
        "label": "Мини-графики",
        "about": "Строка на каждую серию: название, маленький график, итог.",
        "sources": ("metric",),
        "min_width": 1,
        "options": {
            **_COMMON_METRIC,
            "agg": ("enum", _AGG, "sum"),
            "top": ("int", 1, 15, 8),
        },
    },
    "heatmap": {
        "label": "Тепловая карта",
        "about": "Плотность по дням недели и часам (по времени точек) или по датам и сериям.",
        "sources": ("metric",),
        "min_width": 2,
        "options": {
            "period": ("enum", _PERIOD_OPT, "dash"),
            "layout": ("enum", ("weekday_hour", "date_series"), "weekday_hour"),
            "agg": ("enum", _AGG, "sum"),
            "format": ("enum", _FORMAT, "number"),
        },
    },
    "kpi_row": {
        "label": "Ряд чисел",
        "about": "2–6 чисел в одном блоке, каждое из своего источника-метрики этого дашборда.",
        "sources": (),
        "min_width": 2,
        "options": {"period": ("enum", _PERIOD_OPT, "dash")},
        "items": True,
    },
    "funnel": {
        "label": "Воронка",
        "about": "Этапы сверху вниз с конверсией из этапа в этап.",
        "sources": ("table",),
        "min_width": 1,
        "options": {
            "label_field": ("field", True),
            "value_field": ("field", True),
            "show_conversion": ("bool", True),
        },
    },
    "grouped": {
        "label": "Сравнение",
        "about": "Сгруппированные столбцы по категориям: план и факт по отделам, год к году.",
        "sources": ("table",),
        "min_width": 2,
        "options": {
            "label_field": ("field", True),
            "value_fields": ("fields", 4, True),
            "orientation": ("enum", ("vertical", "horizontal"), "vertical"),
            "format": ("enum", _FORMAT, "number"),
            "unit": ("str", 12, ""),
        },
    },
    "bullet": {
        "label": "План / факт",
        "about": "Полоса факта и отметка плана на каждой строке, цвет по проценту выполнения.",
        "sources": ("table",),
        "min_width": 1,
        "options": {
            "label_field": ("field", True),
            "fact_field": ("field", True),
            "plan_field": ("field", True),
            "warn_below": ("int", 0, 200, 80),
            "bad_below": ("int", 0, 200, 50),
            "format": ("enum", _FORMAT, "number"),
        },
    },
    "waterfall": {
        "label": "Каскад",
        "about": "Из чего сложился итог: плюсы и минусы по статьям, итог считается сам.",
        "sources": ("table",),
        "min_width": 2,
        "options": {
            "label_field": ("field", True),
            "value_field": ("field", True),
            "total_label": ("str", 30, "Итог"),
            "format": ("enum", _FORMAT, "money"),
        },
    },
    "scatter": {
        "label": "Точки",
        "about": "Связь двух чисел: каждая строка — точка (X, Y), можно выделить одну.",
        "sources": ("table",),
        "min_width": 2,
        "options": {
            "label_field": ("field", True),
            "x_field": ("field", True),
            "y_field": ("field", True),
            "x_label": ("str", 30, ""),
            "y_label": ("str", 30, ""),
            "highlight": ("str", 80, ""),
        },
    },
    "table": {
        "label": "Таблица",
        "about": "Таблица с сортировкой, поиском, фильтром по статусу, итогами и выгрузкой в CSV.",
        "sources": ("table",),
        "min_width": 2,
        "options": {
            "sort_field": ("field", False),
            "sort_dir": ("enum", ("desc", "asc"), "desc"),
            "search": ("bool", True),
            "status_filter": ("field", False),
            "totals": ("fields", 6, False),
            "limit": ("int", 1, 200, 50),
        },
        "columns": True,
    },
    "pivot": {
        "label": "Сводная таблица",
        "about": "Строки × колонки с суммой в ячейках, подсветка одной шкалой, итоги.",
        "sources": ("table",),
        "min_width": 2,
        "options": {
            "row_field": ("field", True),
            "col_field": ("field", True),
            "value_field": ("field", True),
            "agg": ("enum", ("sum", "avg", "count", "min", "max"), "sum"),
            "format": ("enum", _FORMAT, "number"),
        },
    },
    "cohort": {
        "label": "Когорты",
        "about": "Треугольник удержания: когорта × шаг (месяц 0, 1, 2…) → процент.",
        "sources": ("table",),
        "min_width": 2,
        "options": {
            "cohort_field": ("field", True),
            "step_field": ("field", True),
            "value_field": ("field", True),
        },
    },
    "status": {
        "label": "Статусы",
        "about": "Светофор проверок: название, ok / warn / fail и подпись.",
        "sources": ("table",),
        "min_width": 1,
        "options": {
            "label_field": ("field", True),
            "state_field": ("field", True),
            "note_field": ("field", False),
        },
    },
    "feed": {
        "label": "Лента событий",
        "about": "Последние события: время, текст, ссылка.",
        "sources": ("events",),
        "min_width": 1,
        "options": {"limit": ("int", 1, 50, 10)},
    },
    "text": {
        "label": "Текст",
        "about": "Короткое пояснение: **жирный** и ссылки [текст](https://…).",
        "sources": (),
        "min_width": 1,
        "options": {"text": ("str", 2000, "")},
        "required": ("text",),
    },
}

MAX_KPI_ITEMS = 6
MAX_TABLE_COLUMNS = 12


class WidgetError(ValueError):
    """Понятная ошибка для агента и человека."""


def _is_field(v: Any) -> bool:
    return isinstance(v, str) and bool(_FIELD_RE.match(v))


def _short(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _check_field(kind: str, name: str, v: str, columns: Optional[set[str]]) -> None:
    if columns is not None and v not in columns:
        known = ", ".join(sorted(columns)) or "—"
        raise WidgetError(f"{kind}: колонки «{v}» нет в источнике (есть: {known})")


def _opt(kind: str, name: str, spec: tuple, raw: Any, columns: Optional[set[str]]) -> Any:
    t = spec[0]
    if t == "enum":
        v = str(raw).strip().lower() if raw is not None else spec[2]
        if v not in spec[1]:
            raise WidgetError(f"{kind}.{name}: одно из {', '.join(spec[1])}")
        return v
    if t == "int":
        try:
            v = int(raw)
        except (TypeError, ValueError):
            raise WidgetError(f"{kind}.{name}: целое число {spec[1]}–{spec[2]}")
        return max(spec[1], min(spec[2], v))
    if t == "num":
        v = _num(raw)
        if v is None:
            raise WidgetError(f"{kind}.{name}: нужно число")
        return v
    if t == "bool":
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "да")
        return bool(raw)
    if t == "str":
        return _short(raw, spec[1]) if name != "text" else str(raw or "").strip()[: spec[1]]
    if t == "field":
        if not _is_field(raw):
            raise WidgetError(f"{kind}.{name}: имя колонки латиницей (a-z, 0-9, _)")
        _check_field(kind, name, raw, columns)
        return raw
    if t == "fields":
        if not isinstance(raw, list) or not raw:
            raise WidgetError(f"{kind}.{name}: список имён колонок")
        out = []
        for f in raw[: spec[1]]:
            if not _is_field(f):
                raise WidgetError(f"{kind}.{name}: имя колонки латиницей (a-z, 0-9, _)")
            _check_field(kind, name, f, columns)
            if f not in out:
                out.append(f)
        return out
    if t == "series":
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise WidgetError(f"{kind}.{name}: список названий серий")
        out = []
        for s in raw[: spec[1]]:
            s = _short(s, 60)
            if s and s not in out:
                out.append(s)
        return out
    raise WidgetError(f"{kind}.{name}: неизвестная опция")  # pragma: no cover


def _default(spec: tuple) -> Any:
    t = spec[0]
    if t in ("enum", "bool", "str"):
        return spec[-1]
    if t == "int":
        return spec[3]
    return None


def table_columns(schema: Optional[dict[str, Any]]) -> Optional[set[str]]:
    """Имена колонок табличного источника, если они объявлены (иначе None — не проверяем)."""
    cols = (schema or {}).get("columns")
    if not isinstance(cols, list) or not cols:
        return None
    return {str(c.get("name")) for c in cols if isinstance(c, dict) and c.get("name")}


def clean_columns(raw: Any) -> list[dict[str, Any]]:
    """Колонки источника-таблицы или виджета table: [{name, label, type}]."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise WidgetError("columns: список {name, label, type}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in raw[:MAX_TABLE_COLUMNS * 2]:
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict) or not _is_field(c.get("name")):
            raise WidgetError("columns: у каждой колонки name латиницей (a-z, 0-9, _)")
        name = str(c["name"])
        if name in seen:
            continue
        typ = str(c.get("type") or "text").strip().lower()
        if typ not in COLUMN_TYPES:
            raise WidgetError(f"columns.{name}.type: одно из {', '.join(COLUMN_TYPES)}")
        seen.add(name)
        out.append({"name": name, "label": _short(c.get("label") or name, 40), "type": typ})
    return out


def validate(
    kind: Any,
    options: Any,
    *,
    dataset: Optional[dict[str, Any]] = None,
    datasets_by_key: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Проверить и нормализовать опции виджета. dataset — источник виджета (или None)."""
    k = str(kind or "").strip().lower()
    spec = WIDGET_TYPES.get(k)
    if not spec:
        raise WidgetError(f"Неизвестный тип виджета «{kind}». Есть: {', '.join(WIDGET_TYPES)}")
    raw = options if isinstance(options, dict) else {}
    sources = spec["sources"]
    if sources:
        if not dataset:
            raise WidgetError(f"{k}: нужен источник вида {' или '.join(sources)}")
        if dataset.get("kind") not in sources:
            raise WidgetError(
                f"{k}: источник «{dataset.get('key')}» вида {dataset.get('kind')}, а нужен {' или '.join(sources)}"
            )
    elif dataset:
        raise WidgetError(f"{k}: этому виджету источник не нужен")
    columns = table_columns(dataset.get("schema")) if dataset and dataset.get("kind") == "table" else None

    out: dict[str, Any] = {}
    for name, ospec in spec["options"].items():
        if name in raw and raw[name] not in (None, "", []):
            out[name] = _opt(k, name, ospec, raw[name], columns)
        else:
            d = _default(ospec)
            if d is not None and d != []:
                out[name] = d
            if ospec[0] == "field" and ospec[1]:
                raise WidgetError(f"{k}: укажите {name} (колонку источника)")
            if ospec[0] == "fields" and ospec[2]:
                raise WidgetError(f"{k}: укажите {name} (список колонок источника)")
    for name in spec.get("required", ()):
        if out.get(name) in (None, ""):
            raise WidgetError(f"{k}: укажите {name}")
    if dataset and dataset.get("kind") == "table":
        for name in spec.get("table_required", ()):
            if not out.get(name):
                raise WidgetError(f"{k}: для источника-таблицы укажите {name}")
    if k == "bullet" and out["bad_below"] > out["warn_below"]:
        out["bad_below"] = out["warn_below"]
    if spec.get("columns"):
        cols = clean_columns(raw.get("columns"))
        if not cols and dataset:
            cols = clean_columns((dataset.get("schema") or {}).get("columns"))
        for c in cols:
            _check_field(k, "columns", c["name"], columns)
        out["columns"] = cols[:MAX_TABLE_COLUMNS]
    if spec.get("items"):
        out["items"] = _kpi_items(raw.get("items"), datasets_by_key or {})
    return out


def _kpi_items(raw: Any, by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not (2 <= len(raw) <= MAX_KPI_ITEMS):
        raise WidgetError(f"kpi_row.items: от 2 до {MAX_KPI_ITEMS} чисел")
    num = WIDGET_TYPES["number"]["options"]
    out = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            raise WidgetError("kpi_row.items: каждый пункт — {dataset, label, agg, format, …}")
        key = str(it.get("dataset") or "").strip()
        ds = by_key.get(key)
        if not ds:
            raise WidgetError(f"kpi_row.items[{i}]: в дашборде нет источника «{key}»")
        if ds.get("kind") != "metric":
            raise WidgetError(f"kpi_row.items[{i}]: источник «{key}» должен быть метрикой")
        item: dict[str, Any] = {"dataset": key, "label": _short(it.get("label") or ds.get("title") or key, 40)}
        for name in ("agg", "series", "format", "unit", "good"):
            item[name] = _opt("kpi_row", name, num[name], it[name], None) if it.get(name) not in (None, "") \
                else _default(num[name])
        out.append(item)
    return out


def clamp_width(kind: str, width: Any) -> int:
    spec = WIDGET_TYPES.get(kind) or {}
    try:
        w = int(width)
    except (TypeError, ValueError):
        w = 0
    lo = int(spec.get("min_width") or 1)
    if not w:
        w = max(lo, 2 if lo < 2 and kind not in ("number", "goal", "status", "feed", "text") else lo)
    return max(lo, min(GRID_COLUMNS, w))


def catalog() -> list[dict[str, Any]]:
    """Каталог для MCP и инструкции: тип, назначение, источник, опции с допустимыми значениями."""
    out = []
    for k, spec in WIDGET_TYPES.items():
        opts = {}
        for name, o in spec["options"].items():
            t = o[0]
            if t == "enum":
                opts[name] = {"one_of": list(o[1]), "default": o[2]}
            elif t == "int":
                opts[name] = {"int": [o[1], o[2]], "default": o[3]}
            elif t == "num":
                opts[name] = {"number": True}
            elif t == "bool":
                opts[name] = {"bool": True, "default": o[1]}
            elif t == "str":
                opts[name] = {"text_max": o[1]}
            elif t == "field":
                opts[name] = {"column": True, "required": bool(o[1])}
            elif t == "fields":
                opts[name] = {"columns_max": o[1], "required": bool(o[2])}
            elif t == "series":
                opts[name] = {"series_max": o[1]}
        for name in spec.get("required", ()):
            opts[name]["required"] = True
        item = {
            "type": k,
            "label": spec["label"],
            "about": spec["about"],
            "source": list(spec["sources"]) or None,
            "min_width": spec["min_width"],
            "options": opts,
        }
        if spec.get("table_required"):
            item["table_source_requires"] = list(spec["table_required"])
        if spec.get("columns"):
            item["options"]["columns"] = {"list": "[{name, label, type}]", "types": list(COLUMN_TYPES)}
        if spec.get("items"):
            item["options"]["items"] = {"list": "[{dataset, label, agg, format, unit, good, series}] 2–6"}
        out.append(item)
    return out
