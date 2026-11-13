"""MCP дашбордов (Streamable HTTP без потоков) для ИИ-агентов по личному токену Орбиты.

dash:read — смотреть дашборды и данные; dash:write — ещё создавать дашборды, источники и виджеты
там, где человек редактор. Удалять дашборды/источники и менять доступ агент не может — только человек.
Виджеты — только из каталога (assistant/lib/dashboard_widgets.py), данные шлёт сервер компании
на POST /ingest/dashboards/<id> с ключом источника. Подробная инструкция — docs/agents/dashboards.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from assistant.lib import dashboard_data as DD
from assistant.lib import dashboard_widgets as W
from assistant.stores import dashboards as store

SERVER_NAME = "orbita-dashboards"
SERVER_VERSION = "1.0.0"
GUIDE_PATH = Path(__file__).resolve().parents[2] / "docs" / "agents" / "dashboards.md"
MAX_TEXT = 120_000

INSTRUCTIONS = (
    "Дашборды Орбиты: страницы с цифрами и графиками, которые наполняются автоматически с серверов компании. "
    "Сначала вызовите guide (полная инструкция) и widget_types (каталог из 22 видов виджетов — другие нельзя). "
    "Порядок: list_dashboards (не создавайте дубль) → create_dashboard → create_dataset (вернёт адрес приёма и ключ "
    "ОДИН раз) → add_widget → скрипт на сервере по расписанию шлёт данные на адрес приёма → push_points для проверки "
    "→ get_dashboard (last_push_at, last_error). Ключ источника — секрет: только в env-файле на сервере, не в git и "
    "не в ответе человеку целиком. Отправляйте агрегаты, а не персональные данные. Удалить дашборд или источник и "
    "менять доступ может только человек в Орбите."
)


@dataclass
class DashSource:
    user_id: int
    can_write: bool = False
    base_url: str = ""


def _text(s: str) -> dict[str, Any]:
    if len(s) > MAX_TEXT:
        s = s[:MAX_TEXT] + "\n…(обрезано)"
    return {"type": "text", "text": s}


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, indent=1, default=str)


def _base(src: DashSource) -> str:
    if src.base_url:
        return src.base_url.rstrip("/")
    try:
        from assistant.lib.webapp_public import public_base_url

        return public_base_url().rstrip("/")
    except Exception:
        return ""


def ingest_url(src_or_base: Any, dataset_id: int) -> str:
    base = src_or_base if isinstance(src_or_base, str) else _base(src_or_base)
    return f"{base}/ingest/dashboards/{int(dataset_id)}"


def dashboard_url(base: str, dash_id: int) -> str:
    return f"{base}/webapp/?dashboard={int(dash_id)}"


def examples(url: str, key: str, kind: str) -> dict[str, str]:
    """Готовые примеры отправки для источника (curl и Python без сторонних библиотек)."""
    body = {
        "metric": '{"points":[{"ts":"2026-11-13","value":123456}]}',
        "table": '{"mode":"replace","rows":[{"client":"Ромашка","sum":840000,"status":"оплачено"}]}',
        "events": '{"events":[{"ts":"2026-11-13T10:42:00+03:00","text":"Оплата от «Ромашки» — 84 000 ₽","id":"pay-1"}]}',
    }[kind]
    curl = (f"curl -sS -X POST {url} -H \"Authorization: Bearer $ORBITA_KEY\" "
            f"-H 'Content-Type: application/json' -d '{body}'")
    py = (
        "import json, os, urllib.request\n"
        f"req = urllib.request.Request({url!r}, data=json.dumps({body}).encode(), method='POST',\n"
        "    headers={'Authorization': 'Bearer ' + os.environ['ORBITA_KEY'], 'Content-Type': 'application/json'})\n"
        "print(urllib.request.urlopen(req, timeout=20).read().decode())"
    )
    return {"curl": curl, "python": py, "env": f"ORBITA_KEY={key}"}


# ---------- инструменты ----------

_DASH_ID = {"type": "integer", "description": "id дашборда (из list_dashboards / create_dashboard)"}
_DATASET = {"type": "string", "description": "key источника этого дашборда (например revenue_daily)"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "guide",
        "title": "Инструкция",
        "description": "Полная инструкция для агента: как устроены дашборды, форматы отправки, шаблоны скриптов, "
                       "безопасность, ошибки. Прочитайте перед первой работой.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "widget_types",
        "title": "Каталог виджетов",
        "description": "Все допустимые виды виджетов: назначение, какой источник нужен (metric/table/events), опции и их "
                       "значения. Других видов нет — неизвестные опции отбрасываются.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_dashboards",
        "title": "Список дашбордов",
        "description": "Дашборды, которые видит владелец токена: id, название, организация, роль, когда обновлялся.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_dashboard",
        "title": "Дашборд",
        "description": "Виджеты, источники (вид, колонки, когда последняя отправка, последняя ошибка, «давно нет данных») "
                       "и текущие цифры каждого виджета.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID,
            "period": {"type": "string", "enum": list(W.PERIODS), "default": W.DEFAULT_PERIOD},
        }, "required": ["id"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "read_dataset",
        "title": "Данные источника",
        "description": "Последние присланные данные источника: точки метрики (до limit), строки таблицы или события.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID, "dataset": _DATASET,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        }, "required": ["id", "dataset"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
]

WRITE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_dashboard",
        "title": "Создать дашборд",
        "description": "Новый дашборд. org_id — id организации (из list_dashboards или профиля); без него — личный. "
                       "Сначала проверьте list_dashboards, нет ли подходящего.",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string", "maxLength": store.TITLE_MAX},
            "description": {"type": "string", "maxLength": store.DESCRIPTION_MAX},
            "org_id": {"type": "string", "description": "id организации или пусто — личный"},
        }, "required": ["title"], "additionalProperties": False},
    },
    {
        "name": "update_dashboard",
        "title": "Переименовать дашборд",
        "description": "Поменять название или описание дашборда.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID, "title": {"type": "string"}, "description": {"type": "string"},
        }, "required": ["id"], "additionalProperties": False},
    },
    {
        "name": "create_dataset",
        "title": "Создать источник данных",
        "description": "Источник, куда сервер компании будет слать данные. kind: metric — числа во времени (ts, value, "
                       "series?); table — строки (columns объявляют колонки: name латиницей, label, type из "
                       f"{', '.join(W.COLUMN_TYPES)}); events — лента (ts, text, url?, id?). Возвращает адрес приёма "
                       "(ingest_url), ключ (ingest_key — показывается ОДИН раз) и готовые примеры curl/Python.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID,
            "key": {"type": "string", "description": "латиница, цифры, _ (например revenue_daily)"},
            "kind": {"type": "string", "enum": list(W.DATASET_KINDS)},
            "title": {"type": "string", "description": "название по-русски, его видят люди"},
            "unit": {"type": "string", "description": "₽, %, шт…"},
            "columns": {"type": "array", "items": {"type": "object"}, "description": "для table: [{name, label, type}]"},
            "expected_every_min": {"type": "integer", "description": "как часто ждём данные, минут (для метки «давно нет данных»)"},
        }, "required": ["id", "key", "kind"], "additionalProperties": False},
    },
    {
        "name": "update_dataset",
        "title": "Изменить источник",
        "description": "Название, единица, ожидаемая частота, колонки таблицы.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID, "dataset": _DATASET, "title": {"type": "string"}, "unit": {"type": "string"},
            "expected_every_min": {"type": "integer"}, "columns": {"type": "array", "items": {"type": "object"}},
        }, "required": ["id", "dataset"], "additionalProperties": False},
    },
    {
        "name": "rotate_dataset_key",
        "title": "Сменить ключ источника",
        "description": "Новый ключ (старый перестаёт работать сразу). Используйте, если ключ мог утечь.",
        "inputSchema": {"type": "object", "properties": {"id": _DASH_ID, "dataset": _DATASET},
                        "required": ["id", "dataset"], "additionalProperties": False},
    },
    {
        "name": "add_widget",
        "title": "Добавить виджет",
        "description": "Виджет из каталога (widget_types). type — вид, dataset — key источника (не нужен для text и "
                       "kpi_row), options — опции вида, width — колонок из 4, position — место с 0 (по умолчанию в конец).",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID,
            "type": {"type": "string", "enum": list(W.WIDGET_TYPES)},
            "title": {"type": "string"},
            "dataset": _DATASET,
            "options": {"type": "object"},
            "width": {"type": "integer", "minimum": 1, "maximum": W.GRID_COLUMNS},
            "position": {"type": "integer", "minimum": 0},
        }, "required": ["id", "type"], "additionalProperties": False},
    },
    {
        "name": "update_widget",
        "title": "Изменить виджет",
        "description": "Поменять вид, заголовок, источник, опции (сливаются с текущими), ширину.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID, "widget_id": {"type": "integer"},
            "type": {"type": "string", "enum": list(W.WIDGET_TYPES)}, "title": {"type": "string"},
            "dataset": _DATASET, "options": {"type": "object"},
            "width": {"type": "integer", "minimum": 1, "maximum": W.GRID_COLUMNS},
        }, "required": ["id", "widget_id"], "additionalProperties": False},
    },
    {
        "name": "move_widget",
        "title": "Переставить виджет",
        "description": "Поставить виджет на место position (с 0).",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID, "widget_id": {"type": "integer"}, "position": {"type": "integer", "minimum": 0},
        }, "required": ["id", "widget_id", "position"], "additionalProperties": False},
    },
    {
        "name": "remove_widget",
        "title": "Убрать виджет",
        "description": "Убрать виджет с дашборда (данные источника остаются).",
        "inputSchema": {"type": "object", "properties": {"id": _DASH_ID, "widget_id": {"type": "integer"}},
                        "required": ["id", "widget_id"], "additionalProperties": False},
    },
    {
        "name": "push_points",
        "title": "Пробная отправка",
        "description": "Отправить данные в источник прямо отсюда (то же тело, что у адреса приёма: points / rows+mode / "
                       "events) — чтобы проверить виджеты до настройки сервера. Постоянная отправка — со скрипта на сервере.",
        "inputSchema": {"type": "object", "properties": {
            "id": _DASH_ID, "dataset": _DATASET, "body": {"type": "object"},
        }, "required": ["id", "dataset", "body"], "additionalProperties": False},
    },
]

WRITE_NAMES = {t["name"] for t in WRITE_TOOLS}


def guide_text() -> str:
    try:
        return GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        return INSTRUCTIONS


def _ds_out(src: DashSource, ds: dict[str, Any], *, key: str = "") -> dict[str, Any]:
    out = {k: ds.get(k) for k in ("key", "kind", "title", "unit", "expected_every_min", "last_push_at",
                                  "last_push_count", "last_error", "last_error_at")}
    out["columns"] = (ds.get("schema") or {}).get("columns") or []
    out["ingest_url"] = ingest_url(src, ds["id"])
    out["stale"] = store.stale(ds)
    if key:
        out["ingest_key"] = key
        out["ingest_key_note"] = "Ключ показан один раз. Сохраните его в env-файл на сервере (права 600), не в git."
        out["examples"] = examples(out["ingest_url"], key, ds["kind"])
    else:
        out["key_hint"] = ds.get("key_hint")
    return out


def _list(src: DashSource) -> dict[str, Any]:
    base = _base(src)
    items = []
    for d in store.list_for(src.user_id):
        items.append({"id": d["id"], "title": d["title"], "org_id": d["org_id"] or None, "role": d["my_role"],
                      "updated_at": d["updated_at"], "url": dashboard_url(base, d["id"])})
    orgs = []
    try:
        from assistant.stores import orgs_store

        orgs = [{"id": str(o["id"]), "name": o.get("name"), "role": o.get("my_role")}
                for o in orgs_store.orgs_for_user(src.user_id)]
    except Exception:
        pass
    return {"dashboards": items, "my_orgs": orgs}


def _get(src: DashSource, args: dict[str, Any]) -> dict[str, Any]:
    d = store.require(src.user_id, args.get("id"))
    view = DD.dashboard_view(d, period=str(args.get("period") or W.DEFAULT_PERIOD))
    datasets = [_ds_out(src, ds) for ds in store.datasets(d["id"])]
    widgets = []
    raw = {w["id"]: w for w in store.widgets(d["id"])}
    for w in view["widgets"]:
        item = {"widget_id": w["id"], "type": w["kind"], "title": w["title"], "width": w["width"],
                "dataset": w.get("dataset"), "options": (raw.get(w["id"]) or {}).get("options")}
        if w.get("error"):
            item["error"] = w["error"]
        widgets.append(item)
    return {"id": d["id"], "title": d["title"], "description": d["description"], "role": d["my_role"],
            "url": dashboard_url(_base(src), d["id"]), "datasets": datasets, "widgets": widgets,
            "summary": DD.describe(view)}


def _read_dataset(src: DashSource, args: dict[str, Any]) -> dict[str, Any]:
    d = store.require(src.user_id, args.get("id"))
    ds = store._ds_of(d, args.get("dataset"))  # type: ignore[attr-defined]
    limit = max(1, min(500, int(args.get("limit") or 50)))
    out: dict[str, Any] = {"dataset": _ds_out(src, ds), "total": store.counts(ds["id"], ds["kind"])}
    if ds["kind"] == "metric":
        pts = store.points(ds["id"])[-limit:]
        out["points"] = [{"ts": p["ts"][:10] if p["all_day"] else p["ts"] + "Z", "series": p["series"] or None,
                          "value": p["value"]} for p in pts]
    elif ds["kind"] == "table":
        out["rows"] = store.rows(ds["id"], limit)
    else:
        out["events"] = store.events(ds["id"], limit)
    return out


def _write(src: DashSource, name: str, args: dict[str, Any]) -> dict[str, Any]:
    uid = src.user_id
    if name == "create_dashboard":
        d = store.create(uid, args.get("title"), org_id=args.get("org_id") or "", description=args.get("description") or "")
        return {"id": d["id"], "title": d["title"], "url": dashboard_url(_base(src), d["id"]),
                "next": "create_dataset → add_widget"}
    if name == "update_dashboard":
        patch = {k: args[k] for k in ("title", "description") if k in args}
        d = store.update(uid, args.get("id"), patch)
        return {"id": d["id"], "title": d["title"], "description": d["description"]}
    if name == "create_dataset":
        ds = store.create_dataset(uid, args.get("id"), args.get("key"), args.get("kind"), title=args.get("title") or "",
                                  unit=args.get("unit") or "", columns=args.get("columns"),
                                  expected_every_min=args.get("expected_every_min") or 0)
        return _ds_out(src, ds, key=ds["ingest_key"])
    if name == "update_dataset":
        patch = {k: args[k] for k in ("title", "unit", "expected_every_min", "columns") if k in args}
        return _ds_out(src, store.update_dataset(uid, args.get("id"), args.get("dataset"), patch))
    if name == "rotate_dataset_key":
        ds = store.rotate_key(uid, args.get("id"), args.get("dataset"))
        return _ds_out(src, ds, key=ds["ingest_key"])
    if name == "add_widget":
        w = store.add_widget(uid, args.get("id"), args.get("type"), title=args.get("title") or "",
                             dataset=args.get("dataset"), options=args.get("options"), width=args.get("width"),
                             position=args.get("position"))
        return {"widget_id": w["id"], "type": w["kind"], "options": w["options"], "width": w["width"],
                "position": w["position"]}
    if name == "update_widget":
        patch: dict[str, Any] = {k: args[k] for k in ("title", "dataset", "options", "width") if k in args}
        if args.get("type"):
            patch["kind"] = args["type"]
        w = store.update_widget(uid, args.get("id"), args.get("widget_id"), patch)
        return {"widget_id": w["id"], "type": w["kind"], "options": w["options"], "width": w["width"]}
    if name == "move_widget":
        ws = store.move_widget(uid, args.get("id"), args.get("widget_id"), args.get("position"))
        return {"order": [w["id"] for w in ws]}
    if name == "remove_widget":
        store.remove_widget(uid, args.get("id"), args.get("widget_id"))
        return {"ok": True}
    if name == "push_points":
        d = store.require(uid, args.get("id"), "editor")
        ds = store._ds_of(d, args.get("dataset"))  # type: ignore[attr-defined]
        try:
            res = store.ingest(ds, args.get("body"))
        except ValueError as e:
            store.record_error(ds["id"], str(e))
            raise
        return res
    raise ValueError(f"Нет инструмента {name}")


def call_tool(src: DashSource, name: str, args: dict[str, Any]) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    try:
        if name == "guide":
            return {"content": [_text(guide_text())]}
        if name == "widget_types":
            return {"content": [_text(_json({"widgets": W.catalog(), "grid_columns": W.GRID_COLUMNS,
                                             "periods": list(W.PERIODS)}))]}
        if name == "list_dashboards":
            return {"content": [_text(_json(_list(src)))]}
        if name == "get_dashboard":
            return {"content": [_text(_json(_get(src, args)))]}
        if name == "read_dataset":
            return {"content": [_text(_json(_read_dataset(src, args)))]}
        if name in WRITE_NAMES:
            if not src.can_write:
                return {"content": [_text("Токен только на чтение дашбордов — для записи выпустите токен с правом "
                                          "«Дашборды: чтение и запись» (Профиль → Доступ для ИИ)")], "isError": True}
            return {"content": [_text(_json(_write(src, name, args)))]}
        return {"content": [_text(f"Нет инструмента {name}")], "isError": True}
    except (ValueError, TypeError) as e:
        return {"content": [_text(str(e))], "isError": True}


def handle_payload(src: DashSource, payload: Any) -> Any:
    from assistant.lib import design_mcp

    def one(msg: Any) -> Optional[dict[str, Any]]:
        if isinstance(msg, dict) and msg.get("jsonrpc") == "2.0" and "id" in msg:
            method = msg.get("method")
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            if method == "initialize":
                want = str(params.get("protocolVersion") or "")
                return {"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "protocolVersion": want if want in design_mcp.PROTOCOL_VERSIONS else design_mcp.PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": "Дашборды Орбиты", "version": SERVER_VERSION},
                    "instructions": INSTRUCTIONS,
                }}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": msg["id"],
                        "result": {"tools": TOOLS + (WRITE_TOOLS if src.can_write else [])}}
            if method == "tools/call":
                return {"jsonrpc": "2.0", "id": msg["id"],
                        "result": call_tool(src, str(params.get("name") or ""), params.get("arguments") or {})}
        return design_mcp.handle(None, msg)

    if isinstance(payload, list):
        out = [r for r in (one(m) for m in payload) if r is not None]
        return out or None
    return one(payload)
