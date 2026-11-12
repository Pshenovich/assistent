"""Личные правила чата «Если → То» (сценарии): хранение, условия кодом, проверка ответа модели."""

import json
from datetime import datetime, timedelta, timezone

import pytest

ME = 100
OTHER = 200
CHAT = -1009
NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import assistant.config  # noqa: F401 — перетирает NOTES_DB_PATH при импорте, задаём после
    from assistant.stores import chat_digest
    from assistant.stores import notes as notes_store
    from assistant.stores import scenarios as store

    monkeypatch.setenv("CHAT_DIGEST_DB_PATH", str(tmp_path / "digest.sqlite"))
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    chat_digest._CONN = None
    notes_store._CONN = None
    store._CONN = None
    chat_digest.upsert_chat(CHAT, title="Бухгалтерия", chat_type="supergroup")
    chat_digest.upsert_member(CHAT, ME)
    from assistant.services import scenarios as svc

    calls = []

    def fake_chat(system, user, **kw):
        calls.append({"user": json.loads(user), **kw})
        return fake_chat.answer

    fake_chat.answer = '{"hits":[]}'
    from assistant.nlu import llm

    monkeypatch.setattr(llm, "_chat", fake_chat)
    monkeypatch.setattr(llm, "_model_chat_digest", lambda: "m")
    yield chat_digest, store, svc, fake_chat, calls
    chat_digest._CONN = None
    notes_store._CONN = None
    store._CONN = None


def _msg(cd, mid, uid, text, name="Ирина"):
    cd.insert_message(chat_id=CHAT, message_id=mid, user_id=uid, username=None, display_name=name,
                      text=text, ts_utc=NOW - timedelta(minutes=100 - mid))


def test_rule_crud_and_access(env):
    cd, store, svc, _, _ = env
    _msg(cd, 1, OTHER, "привет")
    r = svc.create_rule(ME, CHAT, {"instruction": "Сообщения о поступивших оплатах", "conditions": {"from_user_ids": [OTHER, 999]}})
    assert r["title"].startswith("Сообщения о") and r["action"] == "show" and r["enabled"]
    assert r["conditions"] == {"from_user_ids": [OTHER]}  # чужой id, которого нет в чате, отброшен
    view = svc.rules_view(ME, CHAT)
    assert [x["id"] for x in view["rules"]] == [r["id"]] and view["authors"][0]["user_id"] == OTHER
    # чужие правила не видны и не меняются
    with pytest.raises(ValueError):
        svc.update_rule(OTHER, r["id"], {"enabled": False})
    with pytest.raises(ValueError):
        svc.rules_view(OTHER, CHAT)  # не участник чата
    assert svc.update_rule(ME, r["id"], {"enabled": False})["enabled"] is False
    with pytest.raises(ValueError):
        svc.create_rule(ME, CHAT, {"instruction": "x"})
    with pytest.raises(ValueError):
        svc.create_rule(ME, CHAT, {"instruction": "что-то искать", "action": "send_money"})
    svc.delete_rule(ME, r["id"])
    assert svc.rules_view(ME, CHAT)["rules"] == []


def test_run_rule_from_creation_verifies_and_dedupes(env):
    cd, store, svc, fake, calls = env
    _msg(cd, 1, OTHER, "Ромашка оплатила 50 000 (старое)")
    r = svc.create_rule(ME, CHAT, {"instruction": "Оплаты", "conditions": {"from_user_ids": [OTHER]}})
    sc = store.get(ME, r["id"])
    _msg(cd, 2, OTHER, "Пришло 120к от ООО Ромашка")
    _msg(cd, 3, ME, "ок, спасибо")  # не от Ирины — модель его не видит
    fake.answer = json.dumps({"hits": [
        {"msg": 2, "summary": "ООО Ромашка — 120 000 ₽", "fields": {"Кто": "ООО Ромашка", "Сумма": "120к"}},
        {"msg": 1, "summary": "старое — было до правила"},
        {"msg": 3, "summary": "не от Ирины"},
        {"msg": 77, "summary": "выдуманный номер"},
    ]})
    assert svc.run_rule(sc) == 1
    sent = calls[-1]["user"]["messages"]
    assert "#2 " in sent and "#1 " not in sent and "#3 " not in sent
    hits = svc.chat_hits(ME, CHAT)
    assert len(hits) == 1 and hits[0]["fields"]["Кто"] == "ООО Ромашка" and hits[0]["url"] == "https://t.me/c/9/2"
    # повторный прогон без новых сообщений — модель не зовём, дублей нет
    n = len(calls)
    assert svc.run_rule(sc) == 0 and len(calls) == n
    svc.hit_op(ME, hits[0]["id"], "hide")
    assert svc.chat_hits(ME, CHAT) == []


def test_model_error_keeps_cursor(env):
    cd, store, svc, fake, calls = env
    r = svc.create_rule(ME, CHAT, {"instruction": "Оплаты"})
    sc = store.get(ME, r["id"])
    _msg(cd, 5, OTHER, "оплата 10 000")
    before = store.get_cursor(sc["id"])
    from assistant.nlu import llm

    def boom(*a, **k):
        raise RuntimeError("proxy down")

    llm._chat = boom
    with pytest.raises(RuntimeError):
        svc.run_rule(sc)
    assert store.get_cursor(sc["id"]) == before


def test_dry_run_saves_nothing(env):
    cd, store, svc, fake, calls = env
    _msg(cd, 1, OTHER, "Сидоров оплатил аванс 45 000")
    fake.answer = '{"hits":[{"msg":1,"summary":"ИП Сидоров — 45 000 ₽"}]}'
    out = svc.dry_run(ME, CHAT, {"instruction": "Оплаты"})
    assert out["hits"][0]["summary"].startswith("ИП Сидоров") and out["checked"] == 1
    assert calls[-1]["operation"] == "chat_rules_check"
    assert svc.rules_view(ME, CHAT)["rules"] == [] and svc.chat_hits(ME, CHAT) == []


def test_message_url_supergroup_and_topic():
    from assistant.services import scenarios as svc

    assert svc.message_url(-1001234, {"message_id": 7}) == "https://t.me/c/1234/7"
    assert svc.message_url(-1001234, {"message_id": 7, "thread_id": 3}) == "https://t.me/c/1234/3/7"
    assert svc.message_url(-55, {"message_id": 7}) == ""


# ---------- строка в таблицу документа ----------

TABLE_DOC = (
    "<h2>Оплаты 2026</h2><table><tbody><tr><th><p>Кто</p></th><th><p>Сумма</p></th><th><p>Дата</p></th></tr>"
    "<tr><td><p>ИП Сидоров</p></td><td><p>45 000</p></td><td><p>08.11.2026</p></td></tr></tbody></table><p>конец</p>"
)


def test_note_table_append_lib():
    from assistant.lib import note_table_append as nta

    assert nta.parse_note_ref("https://getorbita.me/webapp/?b=1&note=42&sheet=7") == (42, "7")
    assert nta.parse_note_ref("42") == (42, "")
    assert nta.parse_note_ref("https://example.com/") == (None, "")
    t = nta.tables_in(TABLE_DOC)
    assert t == [{"index": 0, "label": "Оплаты 2026", "columns": ["Кто", "Сумма", "Дата"], "rows": 1}]
    html, cols = nta.append_row(TABLE_DOC, 0, {"Кто": "ООО <Ромашка>", "Сумма": "120 000"}, item_id="iabcdef0123",
                                source=nta.source_html("https://t.me/c/1/2", "сообщение"))
    assert cols == ["Кто", "Сумма", "Дата", "Источник"]
    assert html.endswith("<p>конец</p>") and "ООО &lt;Ромашка&gt;" in html
    assert nta.tables_in(html)[0]["columns"][-1] == "Источник" and nta.tables_in(html)[0]["rows"] == 2
    assert 'data-leo-item="iabcdef0123"' in html and 'href="https://t.me/c/1/2"' in html
    back, found = nta.remove_row(html, "iabcdef0123")
    assert found and "Ромашка" not in back and "ИП Сидоров" in back
    assert nta.check_value("Сумма", "много") == "" and nta.check_value("Сумма", "120к") == "120к"
    assert nta.check_value("Кто", "Ромашка") == "Ромашка"
    with pytest.raises(LookupError):
        nta.append_row(TABLE_DOC, 3, {}, item_id="i0000000000", source="")


def _doc(owner=ME, body=TABLE_DOC):
    from assistant.stores import notes as notes_store

    return notes_store.create_note(owner, "Оплаты 2026", body)


def test_table_rule_writes_row_and_removes(env):
    cd, store, svc, fake, calls = env
    from assistant.stores import notes as notes_store

    note = _doc()
    nid = int(note["id"])
    with pytest.raises(ValueError):
        svc.create_rule(ME, CHAT, {"instruction": "Оплаты", "action": "table_row", "action_data": {}})
    with pytest.raises(ValueError):  # чужой документ без доступа
        svc.resolve_target(OTHER, nid)
    target = svc.resolve_target(ME, f"https://x/webapp/?note={nid}")
    assert target["sheets"][0]["tables"][0]["columns"] == ["Кто", "Сумма", "Дата"]
    r = svc.create_rule(ME, CHAT, {"instruction": "Оплаты", "action": "table_row",
                                   "action_data": {"note_id": nid, "sheet_id": "main", "table_index": 0,
                                                   "column_hints": {"Кто": "плательщик", "Лишний": "x"}}})
    assert r["action_data"]["columns"] == ["Кто", "Сумма", "Дата"] and r["action_data"]["column_hints"] == {"Кто": "плательщик"}
    assert r["action_label"] == "строка в «Оплаты 2026»"
    sc = store.get(ME, r["id"])
    _msg(cd, 10, OTHER, "Пришло 120к от ООО Ромашка")
    _msg(cd, 11, OTHER, "Ромашка обещала заплатить")
    fake.answer = json.dumps({"hits": [
        {"msg": 10, "summary": "Ромашка 120 000", "fields": {"кто": "ООО Ромашка", "Сумма": "120 000", "Дата": "12.11.2026", "Счёт": "45"}},
        {"msg": 11, "summary": "без суммы", "fields": {"Сумма": "скоро"}},  # пустая строка — не пишем
    ]})
    assert svc.run_rule(sc) == 1
    assert "columns" in calls[-1]["user"] and calls[-1]["user"]["column_hints"] == {"Кто": "плательщик"}
    body = notes_store.get_note(ME, nid)["body"]
    from assistant.lib import note_table_append as nta

    t = nta.tables_in(body)[0]
    assert t["columns"] == ["Кто", "Сумма", "Дата", "Источник"] and t["rows"] == 2
    assert "ООО Ромашка" in body and "Счёт" not in body and "Лео · " in body and "t.me/c/9/10" in body
    hit = svc.chat_hits(ME, CHAT)[0]
    assert hit["status"] == "done" and hit["note_id"] == nid and hit["doc_title"] == "Оплаты 2026"
    svc.hit_op(ME, hit["id"], "remove")
    assert "ООО Ромашка" not in notes_store.get_note(ME, nid)["body"]
    assert svc.chat_hits(ME, CHAT) == []


def test_table_changed_pauses_rule(env):
    cd, store, svc, fake, calls = env
    from assistant.stores import notes as notes_store

    nid = int(_doc()["id"])
    r = svc.create_rule(ME, CHAT, {"instruction": "Оплаты", "action": "table_row",
                                   "action_data": {"note_id": nid, "table_index": 0}})
    notes_store.update_note(ME, nid, body="<p>таблицу удалили</p>")
    _msg(cd, 20, OTHER, "Пришло 5 000")
    fake.answer = '{"hits":[{"msg":20,"summary":"5 000","fields":{"Сумма":"5 000"}}]}'
    svc.run_rule(store.get(ME, r["id"]))
    rule = svc.rules_view(ME, CHAT)["rules"][0]
    assert rule["enabled"] is False and "изменилась" in rule["action_data"]["error"]
    assert svc.chat_hits(ME, CHAT)[0]["status"] == "failed"
