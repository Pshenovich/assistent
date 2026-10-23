from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from assistant.services import mail_source
from assistant.stores import mail_accounts, mail_index, mail_senders

ME = 100
MINE = "vova@yandex.ru"
NOW = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import calendar as cal
    from assistant.services import focus_items, proactive_ai
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    for mod in (notes_store, focus_items, proactive_ai, mail_index, mail_accounts, mail_senders):
        mod._CONN = None
    mail_accounts._FERNET = None
    monkeypatch.setattr(cal, "_tz_for", lambda uid: timezone(timedelta(hours=3)))
    monkeypatch.setattr(mail_source, "meeting_people", lambda uid, now: set())
    monkeypatch.setattr(mail_source, "_now", lambda: NOW)
    mail_accounts.upsert(ME, provider="yandex", email=MINE, auth="oauth_yandex")


def _put(key, frm, *, bulk=False, snippet="Пришли смету?", hours_ago=5, to=(MINE,)):
    mail_index.upsert(ME, {"mail_key": key, "account_id": "m_1", "direction": "in", "from_addr": frm, "to": list(to),
                           "subject": "Тема " + key, "snippet": snippet, "thread_key": key, "message_id": f"<{key}>",
                           "date_utc": (NOW - timedelta(hours=hours_ago)).isoformat(), "bulk": bulk, "uid": 1})


@pytest.mark.parametrize("raw,want", [
    ("Ivan@Firm.ru", "ivan@firm.ru"), ("firm.ru", "@firm.ru"), ("@firm.ru", "@firm.ru"), ("*@firm.ru", "@firm.ru"),
])
def test_normalize(raw, want):
    assert mail_senders.normalize(raw) == want


def test_normalize_rejects_garbage():
    with pytest.raises(ValueError):
        mail_senders.normalize("иван")


def test_address_beats_domain():
    rules = {"@firm.ru": "block", "boss@firm.ru": "allow"}
    assert mail_senders.match(rules, "olga@firm.ru") == "block"
    assert mail_senders.match(rules, "boss@firm.ru") == "allow"
    assert mail_senders.match(rules, "x@other.ru") is None


def test_allow_beats_funnel_at_sync(env):
    m = EmailMessage()
    m["From"], m["To"], m["List-Unsubscribe"] = "noreply@bank.ru", MINE, "<mailto:u@bank.ru>"
    assert mail_source.is_filtered(m, "noreply@bank.ru", mine={MINE}, rules={})
    assert not mail_source.is_filtered(m, "noreply@bank.ru", mine={MINE}, rules={"@bank.ru": "allow"})
    assert mail_source.is_filtered(EmailMessage(), "ivan@firm.ru", mine={MINE}, rules={"ivan@firm.ru": "block"})


def test_candidates_respect_rules(env):
    _put("a", "stranger@x.ru")             # незнакомый — воронка не пропускает
    _put("b", "ivan@firm.ru")
    mail_senders.set_rule(ME, "stranger@x.ru", "allow")
    mail_senders.set_rule(ME, "@firm.ru", "block")
    keep, skip = mail_source.candidates(ME, now=NOW)
    assert [r["mail_key"] for r in keep] == ["a"] and skip == ["b"]


def test_block_hides_from_search_and_filtered_list(env):
    _put("a", "ivan@firm.ru", snippet="Смета по ремонту")
    assert mail_index.search(ME, "смета")
    res = mail_source.block_sender(ME, "ivan@firm.ru")
    assert res["hidden"] == 1 and mail_index.search(ME, "смета") == []
    senders, _ = mail_source.muted(ME)
    assert "ivan@firm.ru" in senders


def test_filtered_senders_and_revive(env):
    _put("n1", "news@shop.ru", bulk=True, snippet="")
    _put("n2", "news@shop.ru", bulk=True, snippet="", hours_ago=30)
    got = mail_index.filtered_senders(ME, since=NOW - timedelta(days=14))
    assert got[0]["addr"] == "news@shop.ru" and got[0]["count"] == 2
    rows = mail_index.filtered_rows(ME, "@shop.ru", since=NOW - timedelta(days=14))
    assert {r["mail_key"] for r in rows} == {"n1", "n2"}
    mail_index.revive(ME, "n1", "Ваш заказ готов")
    r = mail_index.get(ME, "n1")
    assert not r["bulk"] and r["analyzed"] == 0 and mail_index.search(ME, "заказ")


def test_correspondent_is_not_a_robot(env):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "info@paylate.info", MINE, "Re: Оплата"
    assert mail_source.is_filtered(m, "info@paylate.info", mine={MINE}, rules={})
    assert not mail_source.is_filtered(m, "info@paylate.info", mine={MINE}, rules={}, known={"info@paylate.info"})
    m2 = EmailMessage()
    m2["From"], m2["To"], m2["Subject"] = "ulyana@firm.ru", MINE, "Приглашение: Звонок"
    assert mail_source.is_filtered(m2, "ulyana@firm.ru", mine={MINE}, rules={}, known={"ulyana@firm.ru"})  # приглашения — всегда нет


def test_revive_correspondents_picks_only_known_non_calendar(env, monkeypatch):
    calls = []
    monkeypatch.setattr(mail_source, "revive_sender", lambda uid, p: calls.append(p) or 1)
    mail_index.upsert(ME, {"mail_key": "o1", "account_id": "m_1", "direction": "out", "from_addr": MINE, "to": ["info@paylate.info", "ulyana@firm.ru"],
                           "subject": "Оплата", "snippet": "x", "thread_key": "t1", "date_utc": (NOW - timedelta(days=2)).isoformat()})
    _put("p1", "info@paylate.info", bulk=True, snippet="")
    _put("c1", "ulyana@firm.ru", bulk=True, snippet="")
    mail_index._conn().execute("UPDATE mail_messages SET subject = 'Приглашение: Звонок' WHERE mail_key = 'c1'")
    _put("n1", "news@shop.ru", bulk=True, snippet="")
    assert mail_source.revive_correspondents(ME) == 1 and calls == ["info@paylate.info"]
