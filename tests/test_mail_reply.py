from datetime import date, datetime, timedelta, timezone

import pytest

from assistant.services import mail_composer, mail_reply, mail_source
from assistant.stores import mail_accounts, mail_attachments, mail_index

ME = 100
MINE = "vova@yandex.ru"
NOW = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import gpt_calendar_tools as g
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("MAIL_SECRET_KEY", "")
    for mod in (notes_store, mail_index, mail_accounts):
        mod._CONN = None
    mail_accounts._FERNET = None
    monkeypatch.setattr(mail_reply, "UNDO_SECONDS", 0.05)
    monkeypatch.setattr(mail_reply, "_fetch_full", lambda uid, rows: {})  # IMAP в тестах не трогаем
    monkeypatch.setattr(mail_reply, "_ensure_in_sent", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mail_reply.mail_smtp, "send", lambda acc, msg, **kw: sent.append((acc, msg, kw)))
    monkeypatch.setattr(mail_source, "credentials", lambda uid, acc: {"access_token": "tok"})
    acc = mail_accounts.upsert(ME, provider="yandex", email=MINE, auth="oauth_yandex", send_enabled=True)
    mail_index.upsert(ME, {
        "mail_key": "k1", "account_id": acc["account_id"], "direction": "in", "message_id": "<b@firm>",
        "in_reply_to": "<a@me>", "refs": ["<root@me>", "<a@me>"], "thread_key": "t1", "from_addr": "ivan@firm.ru",
        "from_name": "Иван", "reply_to": "", "to": [MINE, "olga@firm.ru"], "cc": ["boss@firm.ru"],
        "subject": "Договор", "date_utc": (NOW - timedelta(hours=5)).isoformat(), "snippet": "Подпишешь до пятницы?",
    })
    return {"sent": sent, "acc": acc}


def _wait_status(aid, want, timeout=2.0):
    import time

    from assistant.services import gpt_calendar_tools as g

    t0 = time.time()
    while time.time() - t0 < timeout:
        a = g.get_action(aid)
        if a and a["status"] == want:
            return a
        time.sleep(0.02)
    return g.get_action(aid)


def test_default_recipients_reply_and_reply_all():
    row = {"direction": "in", "from_addr": "ivan@firm.ru", "reply_to": "", "to": [MINE, "olga@firm.ru"], "cc": ["boss@firm.ru"]}
    assert mail_reply.default_recipients(row, {MINE}, reply_all=False) == (["ivan@firm.ru"], [])
    assert mail_reply.default_recipients(row, {MINE}, reply_all=True) == (["ivan@firm.ru"], ["olga@firm.ru", "boss@firm.ru"])
    assert mail_reply.default_recipients({**row, "reply_to": "sales@firm.ru"}, {MINE}, reply_all=False)[0] == ["sales@firm.ru"]


def test_draft_threads_into_same_conversation(env):
    aid = mail_reply.create_draft(ME, "k1", body="Да, подпишу.")
    from assistant.services import gpt_calendar_tools as g

    p = g.get_action(aid)["payload"]
    assert p["to"] == ["ivan@firm.ru"] and p["subject"] == "Re: Договор"
    assert p["in_reply_to"] == "<b@firm>" and p["references"] == ["<root@me>", "<a@me>", "<b@firm>"]
    mail_accounts.set_signature(ME, "Вова Пшенович")
    msg = mail_reply.build_message(ME, p)
    assert msg["In-Reply-To"] == "<b@firm>" and msg["References"] == "<root@me> <a@me> <b@firm>"
    body = msg.get_content()
    assert body.startswith("Да, подпишу.\n\n--\nВова Пшенович")
    assert "Иван пишет:\n> Подпишешь до пятницы?" in body


def test_send_with_undo_window(env):
    aid = mail_reply.create_draft(ME, "k1", body="Да")
    assert mail_reply.request_send(ME, aid)["status"] == "sending"
    assert mail_reply.cancel_send(ME, aid)["status"] == "proposed"
    import time

    time.sleep(0.15)
    assert env["sent"] == []  # отменили — не ушло
    mail_reply.request_send(ME, aid)
    a = _wait_status(aid, "sent")
    assert a["status"] == "sent" and len(env["sent"]) == 1
    assert env["sent"][0][2] == {"access_token": "tok"}
    assert mail_index.replied(ME, mail_index.get(ME, "k1"))  # ответ попал в индекс — «ждут ответа» снимется


def test_new_recipient_needs_confirm(env):
    aid = mail_reply.create_draft(ME, "k1", body="Да")
    mail_reply.update_draft(ME, aid, {"cc": ["olga@firm.ru", "stranger@x.ru"]})
    with pytest.raises(mail_reply.NeedsConfirm) as e:
        mail_reply.request_send(ME, aid)
    assert e.value.addrs == ["stranger@x.ru"]
    assert mail_reply.request_send(ME, aid, confirm_new=True)["status"] == "sending"


def test_send_refused_when_disabled_or_empty(env):
    aid = mail_reply.create_draft(ME, "k1", body="")
    with pytest.raises(mail_reply.ReplyError):
        mail_reply.request_send(ME, aid)
    mail_reply.update_draft(ME, aid, {"body": "Ок"})
    mail_accounts.set_send_enabled(ME, env["acc"]["account_id"], False)
    with pytest.raises(mail_reply.ReplyError, match="Отвечать из Orbita"):
        mail_reply.request_send(ME, aid)


def test_bad_address_rejected(env):
    aid = mail_reply.create_draft(ME, "k1", body="Да")
    with pytest.raises(mail_reply.ReplyError, match="опечатка"):
        mail_reply.update_draft(ME, aid, {"to": ["ivan@firm"]})


def test_failed_send_reports_reason(env, monkeypatch):
    def boom(acc, msg, **kw):
        raise mail_reply.mail_smtp.MailSendError("Почта отклонила адрес получателя — проверьте его.")

    monkeypatch.setattr(mail_reply.mail_smtp, "send", boom)
    aid = mail_reply.create_draft(ME, "k1", body="Да")
    mail_reply.request_send(ME, aid)
    a = _wait_status(aid, "failed")
    assert a["status"] == "failed" and "адрес получателя" in a["result"]["error"]


def test_attachments_roundtrip_and_limit(env):
    meta = mail_attachments.save(ME, "смета.pdf", "application/pdf", b"%PDF-1")
    aid = mail_reply.create_draft(ME, "k1", body="Смета во вложении")
    mail_reply.update_draft(ME, aid, {"attachments": [meta["id"]]})
    from assistant.services import gpt_calendar_tools as g

    msg = mail_reply.build_message(ME, g.get_action(aid)["payload"])
    att = [p for p in msg.iter_attachments()]
    assert att[0].get_filename() == "смета.pdf" and att[0].get_content() == b"%PDF-1"
    with pytest.raises(mail_attachments.AttachmentError):
        mail_attachments.save(ME, "big.bin", "", b"x" * (mail_attachments.MAX_TOTAL + 1))


def test_public_action_carries_draft(env):
    from assistant.services import gpt_calendar_tools as g

    aid = mail_reply.create_draft(ME, "k1", body="Да")
    pub = g.public_action(g.get_action(aid), viewer_uid=ME)
    assert pub["draft"]["to"] == ["ivan@firm.ru"] and pub["draft"]["accounts"][0]["email"] == MINE
    tid = mail_reply.thread_card(ME, "k1")
    pub = g.public_action(g.get_action(tid), viewer_uid=ME)
    assert pub["kind"] == "email_thread" and pub["mail"]["gist"] == "Подпишешь до пятницы?"


@pytest.mark.parametrize("q,want", [
    ("", None),
    ("слоты", None),
    ("слоты я и Мыздриков следующий вторник", date(2026, 11, 3)),   # ближайший вт — 27.10, следующий — 03.11
    ("слоты Анна вторник", date(2026, 10, 27)),
    ("слоты завтра", date(2026, 10, 23)),
    ("слоты 28.10 30 минут", date(2026, 10, 28)),
    ("слоты 5 ноября", date(2026, 11, 5)),
])
def test_parse_slot_query(q, want):
    r = mail_composer.parse_query(q, date(2026, 10, 22))
    assert r["date"] == want


def test_parse_slot_query_people_and_duration():
    r = mail_composer.parse_query("слоты я и Мыздриков следующий вторник полчаса", date(2026, 10, 22))
    assert r["people"] == ["Мыздриков"] and r["duration"] == 30
    assert mail_composer.parse_query("+слоты с Анной и Олегом 1,5 часа", date(2026, 10, 22))["duration"] == 90


def test_slots_text():
    assert mail_composer.slots_text(["вт 28.10, 11:00–12:00", "вт 28.10, 15:00–16:00"], "МСК") == \
        "Предлагаю: вт 28.10, 11:00–12:00 или 15:00–16:00 (МСК)"
    assert mail_composer.slots_text(["вт 28.10, 11:00–12:00"], "") == "Предлагаю: вт 28.10, 11:00–12:00"


def test_mail_tools_hidden_without_mail(env):
    from assistant.services import gpt_calendar_tools as g

    names = [t["function"]["name"] for t in g.active_tools(ME)]
    assert "mail_search" in names
    assert "mail_search" not in [t["function"]["name"] for t in g.active_tools(999)]
