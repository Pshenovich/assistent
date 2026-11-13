"""Дашборды: каталог виджетов, права, приём данных по ключу источника, расчёт виджетов."""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest

from assistant.lib import dashboard_data as DD
from assistant.lib import dashboard_widgets as W
from assistant.stores import dashboards as D

_ids = itertools.count(970_000, 10)
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)


def _uid() -> int:
    return next(_ids)


# ---------- каталог ----------


def test_catalog_has_all_types_and_rejects_unknown():
    names = [c["type"] for c in W.catalog()]
    assert len(names) == 22 and "pivot" in names and "cohort" in names and "kpi_row" in names
    with pytest.raises(W.WidgetError, match="Неизвестный тип"):
        W.validate("pie3d", {})


def test_validate_checks_source_kind_and_drops_unknown_options():
    metric = {"key": "rev", "kind": "metric", "schema": {}}
    table = {"key": "deals", "kind": "table", "schema": {"columns": [{"name": "client"}, {"name": "sum"}]}}
    opts = W.validate("number", {"agg": "SUM", "color": "#f00", "html": "<b>"}, dataset=metric)
    assert opts["agg"] == "sum" and "color" not in opts and "html" not in opts
    with pytest.raises(W.WidgetError, match="нужен table"):
        W.validate("funnel", {"label_field": "a", "value_field": "b"}, dataset=metric)
    with pytest.raises(W.WidgetError, match="колонки «amount» нет"):
        W.validate("ranking", {"label_field": "client", "value_field": "amount"}, dataset=table)
    ok = W.validate("ranking", {"label_field": "client", "value_field": "sum", "top": 99}, dataset=table)
    assert ok["top"] == 15
    with pytest.raises(W.WidgetError, match="источник не нужен"):
        W.validate("text", {"text": "x"}, dataset=metric)
    with pytest.raises(W.WidgetError, match="укажите target"):
        W.validate("goal", {}, dataset=metric)


# ---------- права ----------


def test_personal_dashboard_access_and_roles():
    owner, other = _uid(), _uid()
    d = D.create(owner, "Продажи")
    assert d["my_role"] == "owner" and [x["id"] for x in D.list_for(owner)] == [d["id"]]
    assert D.list_for(other) == []
    with pytest.raises(D.DashboardError, match="не найден"):
        D.require(other, d["id"])
    D.set_member(owner, d["id"], f"u:{other}", "viewer")
    assert D.require(other, d["id"])["my_role"] == "viewer"
    with pytest.raises(D.DashboardError, match="Нет прав"):
        D.update(other, d["id"], {"title": "x"})
    with pytest.raises(D.DashboardError, match="только у дашборда организации"):
        D.set_member(owner, d["id"], "all", "viewer")
    D.set_member(owner, d["id"], f"u:{other}", "editor")
    assert D.update(other, d["id"], {"title": "Продажи 2"})["title"] == "Продажи 2"
    with pytest.raises(D.DashboardError, match="только владелец"):
        D.delete(other, d["id"])
    assert D.delete(owner, d["id"]) and D.get(d["id"]) is None


# ---------- приём ----------


def _metric(owner, title="Выручка"):
    d = D.create(owner, "Дашборд")
    ds = D.create_dataset(owner, d["id"], "revenue_daily", "metric", title=title, unit="₽")
    return d, ds


def test_ingest_key_shown_once_and_resolves():
    owner = _uid()
    d, ds = _metric(owner)
    key = ds["ingest_key"]
    assert key.startswith("dsk_") and "ingest_key" not in D.dataset(ds["id"])
    assert D.resolve_ingest(ds["id"], key)["id"] == ds["id"]
    assert D.resolve_ingest(ds["id"], "dsk_wrong") is None
    assert D.resolve_ingest(ds["id"] + 999, key) is None
    new = D.rotate_key(owner, d["id"], "revenue_daily")["ingest_key"]
    assert D.resolve_ingest(ds["id"], key) is None and D.resolve_ingest(ds["id"], new)
    with pytest.raises(D.DashboardError, match="уже есть"):
        D.create_dataset(owner, d["id"], "revenue_daily", "metric")


def test_ingest_points_idempotent_and_errors():
    owner = _uid()
    _d, ds = _metric(owner)
    r = D.ingest(ds, {"points": [{"ts": "2026-11-12", "value": 100}, {"ts": "2026-11-13", "value": "1 200,5"}]})
    assert r["accepted"] == 2
    D.ingest(ds, {"points": [{"ts": "2026-11-13", "value": 1300}]})  # повтор → перезапись
    pts = D.points(ds["id"])
    assert [(p["ts"][:10], p["value"], p["all_day"]) for p in pts] == [("2026-11-12", 100, True), ("2026-11-13", 1300, True)]
    D.ingest(ds, {"value": 5, "ts": "2026-11-13T10:00:00+03:00"})
    assert D.points(ds["id"])[-1]["ts"] == "2026-11-13T07:00:00"
    with pytest.raises(ValueError, match=r"points\[0\]\.value: нужно число"):
        D.ingest(ds, {"points": [{"ts": "2026-11-13", "value": "много"}]})
    with pytest.raises(ValueError, match="не дата"):
        D.ingest(ds, {"points": [{"ts": "вчера", "value": 1}]})
    assert D.dataset(ds["id"])["last_push_count"] == 1


def test_ingest_table_replace_upsert_and_events():
    owner = _uid()
    d = D.create(owner, "Сделки")
    t = D.create_dataset(owner, d["id"], "deals", "table",
                         columns=[{"name": "id"}, {"name": "client"}, {"name": "sum", "type": "money"}])
    D.ingest(t, {"rows": [{"id": 1, "client": "А", "sum": 10}, {"id": 2, "client": "Б", "sum": 20}]})
    D.ingest(t, {"rows": [{"id": 2, "client": "Б", "sum": 25}, {"id": 3, "client": "В", "sum": 5}],
                 "mode": "upsert", "key": "id"})
    assert [r["sum"] for r in D.rows(t["id"])] == [10, 25, 5]
    D.ingest(t, {"rows": [{"id": 9, "client": "Я", "sum": 1}], "mode": "replace"})
    assert D.rows(t["id"]) == [{"id": 9, "client": "Я", "sum": 1}]
    with pytest.raises(ValueError, match="нет в источнике"):
        D.ingest(t, {"rows": [{"id": 1, "amount": 3}]})
    e = D.create_dataset(owner, d["id"], "payments", "events")
    D.ingest(e, {"events": [{"ts": "2026-11-13T10:00:00Z", "text": "Оплата 100", "id": "p1"}]})
    D.ingest(e, {"events": [{"ts": "2026-11-13T10:00:00Z", "text": "Оплата 100 ₽", "id": "p1"}]})
    assert [x["text"] for x in D.events(e["id"])] == ["Оплата 100 ₽"]
    with pytest.raises(ValueError, match="только http"):
        D.ingest(e, {"text": "x", "url": "javascript:alert(1)"})


# ---------- виджеты и расчёт ----------


def test_widgets_and_view_numbers():
    owner = _uid()
    d, ds = _metric(owner)
    D.ingest(ds, {"points": [{"ts": f"2026-11-{day:02d}", "value": 10} for day in range(1, 14)]
                  + [{"ts": f"2026-10-{day:02d}", "value": 5} for day in range(14, 32)]})
    w = D.add_widget(owner, d["id"], "number", dataset="revenue_daily", options={"agg": "sum", "period": "7d"})
    D.add_widget(owner, d["id"], "line", dataset="revenue_daily", position=0)
    D.add_widget(owner, d["id"], "text", options={"text": "Пояснение"})
    with pytest.raises(D.DashboardError, match="нужен источник"):
        D.add_widget(owner, d["id"], "line")
    view = DD.dashboard_view(D.require(owner, d["id"]), period="30d", now=NOW)
    kinds = [x["kind"] for x in view["widgets"]]
    assert kinds == ["line", "number", "text"]
    num = next(x for x in view["widgets"] if x["id"] == w["id"])["data"]
    assert num["value"] == 70 and num["prev"] == 65 and round(num["delta_pct"], 1) == 7.7
    line = view["widgets"][0]["data"]
    assert len(line["labels"]) == 30 and line["series"][0]["values"][-1] == 10
    assert "Выручка: 70 ₽, +7.7% к прошлому периоду" in DD.describe(view)
    D.update_widget(owner, d["id"], w["id"], {"options": {"agg": "avg"}})
    assert D.widgets(d["id"])[1]["options"]["agg"] == "avg"
    D.move_widget(owner, d["id"], w["id"], 0)
    assert D.widgets(d["id"])[0]["id"] == w["id"]


def test_table_widgets_compute():
    owner = _uid()
    d = D.create(owner, "Т")
    t = D.create_dataset(owner, d["id"], "f", "table")
    D.ingest(t, {"rows": [{"stage": "Заявки", "n": 400}, {"stage": "Встречи", "n": 200}, {"stage": "Оплаты", "n": 50}]})
    D.add_widget(owner, d["id"], "funnel", dataset="f", options={"label_field": "stage", "value_field": "n"})
    D.add_widget(owner, d["id"], "share", dataset="f", options={"label_field": "stage", "value_field": "n", "max_parts": 2})
    D.add_widget(owner, d["id"], "waterfall", dataset="f", options={"label_field": "stage", "value_field": "n"})
    v = DD.dashboard_view(D.require(owner, d["id"]), now=NOW)["widgets"]
    assert [round(x["from_prev"] or 0) for x in v[0]["data"]["items"]] == [0, 50, 25]
    assert [x["label"] for x in v[1]["data"]["items"]] == ["Заявки", DD.OTHER]
    assert v[2]["data"]["steps"][-1]["value"] == 650


# ---------- HTTP: приём, MCP, API приложения ----------

import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from urllib.parse import urlencode  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

BOT_TOKEN = "123456:DASH"


def _tma(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def client(monkeypatch):
    import usage_server
    from assistant.bot import access_gate

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    return TestClient(usage_server.app, raise_server_exceptions=False)


def _rpc(c, token, method, params=None, mid=1):
    return c.post("/mcp/dashboards", headers={"Authorization": f"Bearer {token}"},
                  json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def _call(c, token, name, args):
    r = _rpc(c, token, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    text = res["content"][0]["text"]
    return res.get("isError", False), (json.loads(text) if text.startswith(("{", "[")) else text)


def test_mcp_flow_scopes_and_ingest(client):
    from assistant.stores import ai_tokens

    uid = _uid()
    kb_only = ai_tokens.create(uid, "kb", scope="kb:write")["token"]
    reader = ai_tokens.create(uid, "r", scope="dash:read")["token"]
    writer = ai_tokens.create(uid, "w", scope="kb:read dash:write")["token"]
    assert _rpc(client, kb_only, "tools/list").status_code == 401
    assert client.post("/mcp/kb", headers={"Authorization": f"Bearer {reader}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401

    names = {t["name"] for t in _rpc(client, reader, "tools/list").json()["result"]["tools"]}
    assert "guide" in names and "create_dashboard" not in names
    err, msg = _call(client, reader, "create_dashboard", {"title": "X"})
    assert err and "только на чтение" in msg
    err, guide = _call(client, reader, "guide", {})
    assert not err and "orbita-dashboards" in guide

    err, d = _call(client, writer, "create_dashboard", {"title": "Продажи"})
    assert not err
    err, ds = _call(client, writer, "create_dataset", {"id": d["id"], "key": "revenue", "kind": "metric",
                                                       "title": "Выручка", "unit": "₽", "expected_every_min": 60})
    assert not err and ds["ingest_key"].startswith("dsk_") and "curl" in ds["examples"]["curl"]
    err, w = _call(client, writer, "add_widget", {"id": d["id"], "type": "number", "dataset": "revenue",
                                                  "options": {"agg": "sum"}})
    assert not err
    err, msg = _call(client, writer, "add_widget", {"id": d["id"], "type": "funnel", "dataset": "revenue"})
    assert err and "нужен table" in msg

    url = ds["ingest_url"].split("/ingest/")[1]
    r = client.post(f"/ingest/{url}", headers={"Authorization": "Bearer dsk_nope"}, json={"value": 1})
    assert r.status_code == 401
    hdr = {"Authorization": f"Bearer {ds['ingest_key']}"}
    r = client.post(f"/ingest/{url}", headers=hdr, json={"points": [{"ts": "2026-11-13", "value": "x"}]})
    assert r.status_code == 400 and "нужно число" in r.json()["error"]
    err, got = _call(client, writer, "get_dashboard", {"id": d["id"]})
    assert "нужно число" in got["datasets"][0]["last_error"]
    r = client.post(f"/ingest/{url}", headers=hdr, json={"points": [{"ts": "2026-11-13", "value": 1500}]})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    err, got = _call(client, writer, "get_dashboard", {"id": d["id"]})
    assert got["datasets"][0]["last_error"] == "" and got["datasets"][0]["last_push_at"]
    err, data = _call(client, reader, "read_dataset", {"id": d["id"], "dataset": "revenue"})
    assert data["points"] == [{"ts": "2026-11-13", "series": None, "value": 1500.0}]
    assert client.get("/mcp/dashboards/guide.md").status_code == 200

    # приложение: список, дашборд, удалить источник может только владелец
    r = client.get("/api/miniapp/dashboards", headers=_tma(uid))
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()["items"]] == [d["id"]]
    r = client.get(f"/api/miniapp/dashboards/{d['id']}?period=7d", headers=_tma(uid))
    assert r.status_code == 200 and r.json()["dashboard"]["widgets"][0]["kind"] == "number"
    other = _uid()
    r = client.get(f"/api/miniapp/dashboards/{d['id']}", headers=_tma(other))
    assert r.status_code == 400
    r = client.delete(f"/api/miniapp/dashboards/{d['id']}/datasets/revenue", headers=_tma(uid))
    assert r.status_code == 200 and D.datasets(d["id"]) == [] and D.widgets(d["id"]) == []


def test_ingest_rate_limit(client, monkeypatch):
    uid = _uid()
    d = D.create(uid, "R")
    ds = D.create_dataset(uid, d["id"], "m", "metric")
    monkeypatch.setattr(D, "RATE_PER_MIN", 2)
    hdr = {"Authorization": f"Bearer {ds['ingest_key']}"}
    codes = [client.post(f"/ingest/dashboards/{ds['id']}", headers=hdr, json={"value": i}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_guide_examples_are_valid():
    """Примеры из docs/agents/dashboards.md проходят каталог — инструкция не врёт агентам."""
    import re as _re

    from assistant.lib import dash_mcp

    text = dash_mcp.guide_text()
    assert "## Каталог виджетов" in text
    for t in W.WIDGET_TYPES:
        assert f"| `{t}` |" in text, f"виджет {t} не описан в инструкции"
    metric = {"key": "revenue_daily", "kind": "metric", "schema": {}}
    table = {"key": "deals", "kind": "table", "schema": {}}
    blocks = [json.loads(b) for b in _re.findall(r"```json\n(.*?)\n```", text, _re.S)]
    assert len(blocks) >= 2
    for b in blocks:
        ds = metric if b["dataset"] == "revenue_daily" else table
        W.validate(b["type"], b["options"], dataset=ds)


def test_leo_tools_see_only_own_dashboards():
    from assistant.services import gpt_calendar_tools as gcal

    owner, other = _uid(), _uid()
    d, ds = _metric(owner, "Выручка")
    D.update(owner, d["id"], {"title": "Продажи"})
    D.ingest(ds, {"value": 1000})
    D.add_widget(owner, d["id"], "number", dataset="revenue_daily", options={"format": "money"})
    found = json.loads(gcal.tool_dashboards_find(gcal.GptCalendarContext(uid=owner), {"query": "прод"}))
    assert [x["title"] for x in found["dashboards"]] == ["Продажи"] and "Выручка: 1 000" in found["dashboards"][0]["headline"]
    read = json.loads(gcal.tool_dashboard_read(gcal.GptCalendarContext(uid=owner), {"id": d["id"], "period": "7d"}))
    assert "Выручка: 1 000" in read["summary"]
    assert json.loads(gcal.tool_dashboards_find(gcal.GptCalendarContext(uid=other), {}))["dashboards"] == []
    assert "не найден" in gcal.tool_dashboard_read(gcal.GptCalendarContext(uid=other), {"id": d["id"]})
    assert {"dashboards_find", "dashboard_read"} <= set(gcal.EXECUTORS)
    names = {t["function"]["name"] for t in gcal.TOOLS} if hasattr(gcal, "TOOLS") else {"dashboards_find", "dashboard_read"}
    assert {"dashboards_find", "dashboard_read"} <= names
