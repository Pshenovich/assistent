"""Внешние трекеры: Битрикс24 по вебхуку, настройки по умолчанию, описание по WWA, эндпоинты."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.services import task_wwa
from assistant.stores import notes as notes_store
from assistant.stores import trackers as store
from assistant.trackers import bitrix24 as b24
from assistant.trackers.base import TaskDraft, TrackerError
from assistant.trackers.markup import md_to_bbcode
from tests.test_canvas_endpoints import BOT_TOKEN, _headers

UID = 5151
HOOK = "https://acme.bitrix24.ru/rest/7/abc123xyz/"


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    b24._STAGES_CACHE.clear()
    yield
    notes_store._CONN = None


class FakeBitrix:
    """Ответы REST Битрикс24 и записанные вызовы."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.users = [{"ID": "42", "NAME": "Иван", "LAST_NAME": "Петров"}]

    def __call__(self, base: str, method: str, params: dict | None = None):
        params = params or {}
        self.calls.append((method, params))
        if method == "user.current":
            return {"ID": "7", "NAME": "Вова", "LAST_NAME": "П"}
        if method == "sonet_group.user.groups":
            return [
                {"GROUP_ID": "153", "GROUP_NAME": "Obuchat · Клиентские"},
                {"GROUP_ID": "12", "GROUP_NAME": "Продукт"},
                {"GROUP_ID": "153", "GROUP_NAME": "Obuchat · Клиентские"},
            ]
        if method == "task.stages.get":
            if params.get("entityId") == 0:
                return {"5": {"ID": "5", "TITLE": "Не спланированы", "SORT": "100"}}
            return {
                "1089": {"ID": "1089", "TITLE": "В работе", "SORT": "200", "COLOR": "00C4FB"},
                "1053": {"ID": "1053", "TITLE": "Оценка", "SORT": "100"},
            }
        if method == "user.search":
            return self.users
        if method == "tasks.task.add":
            return {"task": {"id": "4812"}}
        if method == "task.stages.movetask":
            return True
        raise AssertionError(f"unexpected {method}")

    def last(self, method: str) -> dict:
        return [p for m, p in self.calls if m == method][-1]


@pytest.fixture
def fake(monkeypatch):
    f = FakeBitrix()
    monkeypatch.setattr(b24, "call", f)
    return f


def test_md_to_bbcode_wwa():
    text = "**Зачем**\nКлиенту нужна **сверка**.\n\n**Что сделать**\n- Кнопка CSV\n- Фильтры\n## Критерии\n1. Файл открывается"
    out = md_to_bbcode(text)
    assert "[B]Зачем[/B]" in out and "Клиенту нужна [B]сверка[/B]." in out
    assert "[LIST]\n[*]Кнопка CSV\n[*]Фильтры\n[/LIST]" in out
    assert "[B]Критерии[/B]\n[LIST]\n[*]Файл открывается\n[/LIST]" in out


def test_md_to_bbcode_plain_sections():
    out = md_to_bbcode("Зачем\nСверка.\n\nЧто сделать\n- A\n\nКритерии приёмки:\n- B")
    assert out == "[B]Зачем[/B]\nСверка.\n\n[B]Что сделать[/B]\n[LIST]\n[*]A\n[/LIST]\n\n[B]Критерии приёмки[/B]\n[LIST]\n[*]B\n[/LIST]"
    # «Зачем» внутри предложения — не заголовок
    assert md_to_bbcode("Зачем это нужно — непонятно") == "Зачем это нужно — непонятно"


def test_wwa_clean():
    raw = "## **Зачем**\nТекст\n\n\n\n**Что сделать**\n* пункт\n• ещё"
    assert task_wwa.clean(raw) == "Зачем\nТекст\n\nЧто сделать\n- пункт\n- ещё"


def test_normalize_webhook():
    assert b24.normalize_webhook("https://acme.bitrix24.ru/rest/7/abc123xyz") == HOOK
    # Скопировали вместе с примером метода — отрезаем.
    assert b24.normalize_webhook(HOOK + "profile.json") == HOOK
    assert b24.normalize_webhook(HOOK + "tasks.task.add.json") == HOOK
    with pytest.raises(TrackerError):
        b24.normalize_webhook("https://acme.bitrix24.ru/marketplace/")
    with pytest.raises(TrackerError):
        b24.normalize_webhook("http://acme.bitrix24.ru/rest/7/abc/")


def test_connect_stores_encrypted_webhook(fake):
    t = b24.Bitrix24Tracker()
    conn = t.connect_webhook(UID, HOOK)
    assert conn == {"transport": "webhook", "portal": "https://acme.bitrix24.ru", "account_name": "Вова П"}
    assert t.is_connected(UID)
    raw = store._conn().execute("SELECT secret_enc FROM tracker_credentials").fetchone()[0]
    assert "abc123xyz" not in raw
    assert store.get_credentials(UID, "bitrix24")["secret"] == HOOK
    assert t.disconnect(UID) and not t.is_connected(UID)


def test_boards_and_stages(fake):
    t = b24.Bitrix24Tracker()
    t.connect_webhook(UID, HOOK)
    boards = t.list_boards(UID)
    assert [(b.id, b.name, b.kind) for b in boards] == [
        ("personal", "Мои задачи", "personal"),
        ("153", "Obuchat · Клиентские", "group"),
        ("12", "Продукт", "group"),
    ]
    stages = t.list_stages(UID, "153")
    assert [(s.id, s.name) for s in stages] == [("1053", "Оценка"), ("1089", "В работе")]
    assert stages[1].color == "#00C4FB"
    assert [s.name for s in t.list_stages(UID, "personal")] == ["Не спланированы"]
    n = len(fake.calls)
    t.list_stages(UID, "153")  # из кэша
    assert len(fake.calls) == n


def test_not_connected_raises():
    from assistant.trackers.base import TrackerNotConnected

    with pytest.raises(TrackerNotConnected):
        b24.Bitrix24Tracker().list_boards(UID)


def test_create_task_in_group_stage(fake):
    t = b24.Bitrix24Tracker()
    t.connect_webhook(UID, HOOK)
    created = t.create_task(
        UID,
        TaskDraft(title="Выгрузка CSV", description="**Зачем**\n- A", board_id="153", stage_id="1053",
                  responsible="Иван", deadline="2026-10-30"),
    )
    fields = fake.last("tasks.task.add")["fields"]
    assert fields["TITLE"] == "Выгрузка CSV"
    assert fields["GROUP_ID"] == 153 and fields["STAGE_ID"] == 1053
    assert fields["RESPONSIBLE_ID"] == 42
    assert fields["DEADLINE"] == "2026-10-30T19:00:00"
    assert fields["DESCRIPTION"] == "[B]Зачем[/B]\n[LIST]\n[*]A\n[/LIST]"
    assert created.external_id == "4812"
    assert created.url == "https://acme.bitrix24.ru/workgroups/group/153/tasks/task/view/4812/"
    assert created.extra["responsible_name"] == "Иван Петров"
    assert not [m for m, _ in fake.calls if m == "task.stages.movetask"]


def test_create_task_personal_defaults_to_me_and_moves_stage(fake):
    t = b24.Bitrix24Tracker()
    t.connect_webhook(UID, HOOK)
    created = t.create_task(UID, TaskDraft(title="Себе", board_id="personal", stage_id="5"))
    fields = fake.last("tasks.task.add")["fields"]
    assert "GROUP_ID" not in fields and "STAGE_ID" not in fields
    assert fields["RESPONSIBLE_ID"] == 7
    assert fake.last("task.stages.movetask") == {"id": 4812, "stageId": 5}
    assert created.url == "https://acme.bitrix24.ru/company/personal/user/7/tasks/task/view/4812/"


def test_create_task_unknown_responsible(fake):
    t = b24.Bitrix24Tracker()
    t.connect_webhook(UID, HOOK)
    fake.users = []
    with pytest.raises(TrackerError, match="Не нашёл"):
        t.create_task(UID, TaskDraft(title="X", responsible="Никто"))
    assert not [m for m, _ in fake.calls if m == "tasks.task.add"]


def test_settings_and_links():
    assert store.get_settings(UID, "bitrix24")["board_id"] == ""
    store.save_settings(UID, "bitrix24", board_id="153", board_name="Клиентские", stage_id="1053", stage_name="Оценка")
    assert store.get_settings(UID, "bitrix24") == {
        "board_id": "153", "board_name": "Клиентские", "stage_id": "1053", "stage_name": "Оценка"}
    store.add_task(UID, "bitrix24", external_id="1", url="https://x/1", title="A", calendar_task_id="77")
    store.add_task(UID, "bitrix24", external_id="2", url="https://x/2", title="B", calendar_task_id="77")
    store.add_task(UID + 1, "bitrix24", external_id="3", url="https://x/3", title="C", calendar_task_id="77")
    assert [r["external_id"] for r in store.tasks_for_calendar_task(UID, 77)] == ["1", "2"]


def test_wwa_prompt_has_note_and_focus_context(monkeypatch):
    note = notes_store.create_note(UID, "Встреча с Бустрой", "<p>Обсудили выгрузку оплат</p><p>Нужен CSV до конца месяца</p>")
    monkeypatch.setattr(task_wwa, "_focus_text", lambda uid, key: "Пункт: сделать выгрузку" if key == "k1" else "")
    seen = {}

    def fake_llm(uid, system, user, operation, max_tokens=1200):
        seen.update(uid=uid, system=system, user=user, operation=operation)
        return "**Зачем**\nСверка."

    monkeypatch.setattr("assistant.services.mail_composer._llm", fake_llm)
    out = task_wwa.describe(UID, title="Выгрузка CSV", note_id=note["id"], focus_key="k1", context="CSV")
    assert out == "Зачем\nСверка."  # разметку модели снимаем — менеджер правит простой текст
    assert seen["operation"] == "task_wwa" and "Критерии приёмки" in seen["system"]
    assert "Задача: Выгрузка CSV" in seen["user"]
    assert "Пункт: сделать выгрузку" in seen["user"]
    assert "Встреча с Бустрой" in seen["user"] and "Нужен CSV до конца месяца" in seen["user"]
    with pytest.raises(ValueError):
        task_wwa.describe(UID, title=" ")


def test_wwa_window_around_anchor():
    text = "А" * 10000 + "ЯКОРЬ" + "Б" * 10000
    win = task_wwa._window(text, "ЯКОРЬ", size=1000)
    assert "ЯКОРЬ" in win and len(win) == 1000


@pytest.fixture
def client(monkeypatch, fake):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    return TestClient(usage_server.app, raise_server_exceptions=False)


def test_endpoints_flow(client, fake):
    h = _headers(UID)
    r = client.get("/api/miniapp/trackers", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["trackers"][0]["id"] == "bitrix24" and r.json()["trackers"][0]["connected"] is False
    assert client.get("/api/miniapp/trackers/bitrix24/boards", headers=h).status_code == 409
    assert client.get("/api/miniapp/trackers/jira/boards", headers=h).status_code == 404

    r = client.post("/api/miniapp/trackers/bitrix24/webhook", json={"url": "nope"}, headers=h)
    assert r.status_code == 400
    r = client.post("/api/miniapp/trackers/bitrix24/webhook", json={"url": HOOK}, headers=h)
    assert r.status_code == 200 and r.json()["connected"] is True

    boards = client.get("/api/miniapp/trackers/bitrix24/boards", headers=h).json()["boards"]
    assert boards[1] == {"id": "153", "name": "Obuchat · Клиентские", "kind": "group"}
    stages = client.get("/api/miniapp/trackers/bitrix24/boards/153/stages", headers=h).json()["stages"]
    assert stages[0]["name"] == "Оценка"

    r = client.put("/api/miniapp/trackers/bitrix24/settings", json={
        "board_id": "153", "board_name": "Клиентские", "stage_id": "1053", "stage_name": "Оценка"}, headers=h)
    assert r.json()["defaults"]["stage_id"] == "1053"
    integ = client.get("/api/miniapp/integrations", headers=h).json()
    tr = integ["trackers"][0]
    assert tr["connected"] is True and tr["defaults"]["board_id"] == "153"

    r = client.post("/api/miniapp/trackers/bitrix24/tasks", json={
        "title": "Выгрузка", "description": "x", "board_id": "153", "board_name": "Клиентские",
        "stage_id": "1053", "stage_name": "Оценка", "calendar_task_id": "77", "note_id": "5"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["external"] == {
        "provider": "bitrix24", "id": "4812",
        "url": "https://acme.bitrix24.ru/workgroups/group/153/tasks/task/view/4812/"}
    assert body["chip_label"] == "Выгрузка"
    links = client.get("/api/miniapp/trackers/links?calendar_task_id=77", headers=h).json()["links"]
    assert [(l["external_id"], l["stage_name"]) for l in links] == [("4812", "Оценка")]

    assert client.post("/api/miniapp/trackers/bitrix24/tasks", json={"title": " "}, headers=h).status_code == 400
    assert client.delete("/api/miniapp/trackers/bitrix24/webhook", headers=h).json()["disconnected"] is True


def test_wwa_endpoint(client, monkeypatch):
    monkeypatch.setattr(task_wwa, "describe", lambda uid, **kw: "**Зачем**\n" + kw["title"])
    r = client.post("/api/miniapp/trackers/describe-wwa", json={"title": "Т"}, headers=_headers(UID))
    assert r.status_code == 200 and r.json()["description"] == "**Зачем**\nТ"

    def boom(uid, **kw):
        raise ValueError("Сначала напишите, что нужно сделать")

    monkeypatch.setattr(task_wwa, "describe", boom)
    r = client.post("/api/miniapp/trackers/describe-wwa", json={"title": ""}, headers=_headers(UID))
    assert r.status_code == 400 and "Сначала" in r.json()["detail"]


class _Resp:
    def __init__(self, status: int, data: dict) -> None:
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def test_call_scope_error_with_401_is_not_reported_as_bad_webhook(monkeypatch):
    monkeypatch.setattr(b24.requests, "post", lambda *a, **k: _Resp(401, {
        "error": "insufficient_scope", "error_description": "The request requires higher privileges"}))
    with pytest.raises(TrackerError, match="не хватает прав «Пользователи»"):
        b24.call(HOOK, "user.current")


def test_call_auth_error_shows_bitrix_reason(monkeypatch):
    monkeypatch.setattr(b24.requests, "post", lambda *a, **k: _Resp(401, {
        "error": "INVALID_CREDENTIALS", "error_description": "Invalid request credentials"}))
    with pytest.raises(TrackerError, match=r"не принят.*Invalid request credentials"):
        b24.call(HOOK, "user.current")
