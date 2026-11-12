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
