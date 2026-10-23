import json
from datetime import datetime, timedelta, timezone

import pytest

from assistant.services import mail_memory, mail_source
from assistant.stores import mail_accounts, mail_index, mail_senders

ME = 100
MINE = "vova@yandex.ru"
NOW = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import focus_items, proactive_ai
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("SEMANTIC_SEARCH_ENABLED", "0")
    for mod in (notes_store, focus_items, proactive_ai, mail_index, mail_accounts, mail_senders, mail_memory):
        mod._CONN = None
    mail_accounts._FERNET = None
    monkeypatch.setattr(mail_memory, "_reindex", lambda uid: None)
    mail_accounts.upsert(ME, provider="yandex", email=MINE, auth="oauth_yandex")


def _row(mid, frm, text_dir="in", *, thread="t1", subject="Новые условия", days_ago=200, name="Иван Петров", to=(MINE,)):
    return {"message_id": f"<{mid}>", "account_id": "m_1", "thread_key": thread, "direction": text_dir,
            "from_addr": frm, "from_name": name, "to": list(to), "subject": subject,
            "date_utc": (NOW - timedelta(days=days_ago)).isoformat(), "bulk": False}


def test_keeps_only_author_text_and_skips_bulk(env):
    assert mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "Предлагаем скидку 15% с ноября.\n\n> старое письмо")
    assert not mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "повтор")  # то же письмо
    assert not mail_memory.add_message(ME, {**_row("b", "news@shop.ru"), "bulk": True}, "Распродажа")
    th = mail_memory.threads(ME)
    assert len(th) == 1 and "скидку 15%" in th[0]["text"] and "старое письмо" not in th[0]["text"]
    assert th[0]["people"]["ivan@firm.ru"] == "Иван Петров"


def test_search_by_words_and_person(env):
    mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "Предлагаем новые условия сотрудничества: оплата 30 дней.")
    mail_memory.add_message(ME, _row("b", MINE, "out", to=("ivan@firm.ru",)), "Согласен на условия, подпишем в пятницу.")
    mail_memory.add_message(ME, _row("c", "olga@other.ru", thread="t2", subject="Отчёт", name="Ольга"), "Условия поставки обсудим позже.")
    hits = mail_memory.search_text(ME, "условия сотрудничества", people=["ivan@firm.ru"])
    assert [h["thread_key"] for h in hits] == ["t1"]
    assert {h["thread_key"] for h in mail_memory.search_text(ME, "условия")} == {"t1", "t2"}
    assert mail_memory.people_for(ME, "Иван") == ["ivan@firm.ru"]


def test_forget_sender_and_account(env):
    mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "Текст один")
    mail_memory.add_message(ME, _row("c", "olga@other.ru", thread="t2"), "Текст два")
    assert mail_memory.forget_sender(ME, "@firm.ru") == 1
    assert [t["thread_key"] for t in mail_memory.threads(ME)] == ["t2"]
    assert mail_memory.search_text(ME, "один") == []
    assert mail_memory.forget_account(ME, "m_1") == 1 and mail_memory.threads(ME) == []


def test_block_sender_forgets_memory(env):
    mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "Секретные условия")
    mail_source.block_sender(ME, "ivan@firm.ru")
    assert mail_memory.threads(ME) == []


def test_semantic_sources_respect_toggle(env):
    mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "Новые условия")
    src = mail_memory.semantic_sources(ME)
    assert src[0]["kind"] == "mail_thread" and src[0]["ref"]["people"] == ["ivan@firm.ru", MINE]
    mail_accounts.update_settings(ME, {"memory_enabled": False})
    assert mail_memory.semantic_sources(ME) == [] and not mail_memory.enabled(ME)


def test_mail_search_tool_finds_old_thread_in_memory(env, monkeypatch):
    from assistant.services import gpt_calendar_tools as g
    from assistant.services import mail_tools

    monkeypatch.setattr(mail_memory, "live_search", lambda *a, **k: pytest.fail("в памяти нашлось — в ящик не идём"))
    mail_memory.add_message(ME, _row("a", "ivan@firm.ru"), "Предлагаем новые условия сотрудничества: оплата 30 дней.")
    mail_memory.add_message(ME, _row("b", "ivan@firm.ru", thread="t3", subject="Новые условия, v2"), "Обновили условия сотрудничества.")
    ctx = g.GptCalendarContext(uid=ME)
    out = json.loads(mail_tools.tool_mail_search(ctx, {"query": "новые условия сотрудничества", "who": "Иван"}))
    assert out["people"] == ["ivan@firm.ru"] and {r["thread_key"] for r in out["results"]} == {"t1", "t3"}
    thread = json.loads(mail_tools.tool_mail_thread(ctx, {"thread_key": "t1"}))
    assert "оплата 30 дней" in thread["text"]


def test_mail_search_falls_back_to_mailbox(env, monkeypatch):
    from assistant.services import gpt_calendar_tools as g
    from assistant.services import mail_tools

    calls = []
    monkeypatch.setattr(mail_memory, "live_search",
                        lambda uid, addrs, q, **k: calls.append(addrs) or [{"thread_key": "old", "subject": "Условия 2023"}])
    monkeypatch.setattr(mail_memory, "people_for", lambda uid, who: ["ivan@firm.ru"])
    out = json.loads(mail_tools.tool_mail_search(g.GptCalendarContext(uid=ME), {"query": "условия", "who": "Иван"}))
    assert calls == [["ivan@firm.ru"]] and out["found_in_mailbox"][0]["thread_key"] == "old"


def test_settings_view_and_signature(env):
    assert mail_accounts.settings(ME) == {"signature": "", "memory_enabled": True, "view": "chat"}
    mail_accounts.update_settings(ME, {"view": "letters", "signature": "Вова"})
    assert mail_accounts.settings(ME)["view"] == "letters"
    mail_accounts.update_settings(ME, {"view": "bogus"})
    assert mail_accounts.settings(ME)["view"] == "letters" and mail_accounts.signature(ME) == "Вова"
