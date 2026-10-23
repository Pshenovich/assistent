from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from assistant.integrations import mail_imap
from assistant.services import mail_filters, mail_source
from assistant.stores import mail_accounts, mail_index

ME = 100
NOW = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)  # 19:00 по Москве
MINE = "vova@yandex.ru"


def _msg(frm, to, subject="Договор", body="Привет", cc="", **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    if cc:
        m["Cc"] = cc
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m


# ---------------------------------------------------------------- фильтры

def test_bulk_by_headers():
    assert mail_filters.is_bulk(_msg("a@x.ru", MINE, List_Unsubscribe="<mailto:u@x.ru>"))
    assert mail_filters.is_bulk(_msg("a@x.ru", MINE, Precedence="bulk"))
    assert mail_filters.is_bulk(_msg("a@x.ru", MINE, Auto_Submitted="auto-replied"))
    assert mail_filters.is_bulk(_msg("a@x.ru", MINE, Feedback_ID="1:2:3"))
    assert not mail_filters.is_bulk(_msg("a@x.ru", MINE, Auto_Submitted="no"))
    assert not mail_filters.is_bulk(_msg("ivan@firm.ru", MINE))


@pytest.mark.parametrize("addr,robot", [
    ("noreply@bank.ru", True), ("no-reply@x.com", True), ("notifications@github.com", True),
    ("mailer-daemon@x.ru", True), ("news@shop.ru", True), ("ivan@bounce.shop.ru", True),
    ("ivan.petrov@firm.ru", False), ("info.ivan@firm.ru", True), ("anna@gmail.com", False),
])
def test_robot_addresses(addr, robot):
    assert mail_filters.is_robot(addr) is robot


def test_personal_to_me():
    assert mail_filters.personal_to_me([MINE], [], {MINE})
    assert not mail_filters.personal_to_me(["a@x.ru"], [MINE], {MINE})  # в копии
    assert not mail_filters.personal_to_me([MINE] + [f"p{i}@x.ru" for i in range(5)], [], {MINE})  # много получателей


def test_focus_candidate_requires_known_sender():
    row = {"direction": "in", "bulk": False, "from_addr": "ivan@firm.ru", "to": [MINE], "cc": []}
    kw = dict(mine={MINE}, meeting_people=set(), muted_senders=set(), muted_domains=set())
    assert not mail_filters.focus_candidate(row, correspondents=set(), **kw)
    assert mail_filters.focus_candidate(row, correspondents={"ivan@firm.ru"}, **kw)
    assert mail_filters.focus_candidate({**row, "thread_mine": True}, correspondents=set(), **kw)
    assert not mail_filters.focus_candidate(row, correspondents={"ivan@firm.ru"}, **{**kw, "muted_domains": {"firm.ru"}})


# ---------------------------------------------------------------- разбор письма

def test_strip_quoted_and_signature():
    text = "Иван, пришли смету до пятницы.\n\n--\nВова\n\n> старое письмо"
    assert mail_imap.strip_quoted(text) == "Иван, пришли смету до пятницы."
    text2 = "Ок, договорились.\n22.10.2026, 10:00, Иван Петров пишет:\n> Пришли смету"
    assert mail_imap.strip_quoted(text2) == "Ок, договорились."
    text3 = "Sure.\n\nOn Mon, Oct 20, 2026 at 10:00 AM Ivan <ivan@x.com> wrote:\n> hi"
    assert mail_imap.strip_quoted(text3) == "Sure."


def test_body_text_html_only():
    m = EmailMessage()
    m["From"], m["To"] = "a@x.ru", MINE
    m.set_content("<p>Привет&nbsp;мир</p><script>x()</script><br>Пока", subtype="html")
    assert mail_imap.body_text(m) == "Привет мир\n\nПока"


def test_row_from_threads_and_direction():
    acc = {"account_id": "m_1", "email": MINE}
    m = _msg("Иван <Ivan@Firm.ru>", f"Вова <{MINE}>", subject="=?utf-8?b?0JTQvtCz0L7QstC+0YA=?=",
             Message_ID="<b@firm>", In_Reply_To="<a@me>", References="<root@me> <a@me>")
    row = mail_source._row_from(acc, "INBOX", 7, m, mine={MINE}, body="Подпишешь?\n> цитата")
    assert row["from_addr"] == "ivan@firm.ru" and row["direction"] == "in" and row["to"] == [MINE]
    assert row["subject"] == "Договор" and row["snippet"] == "Подпишешь?"
    assert row["thread_key"] == mail_index.thread_key_for("", "", ["<root@me>"])


# ---------------------------------------------------------------- индекс и проверки кодом

@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import calendar as cal
    from assistant.services import focus_items, proactive_ai
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("MAIL_SECRET_KEY", "")
    for mod in (notes_store, focus_items, proactive_ai, mail_index, mail_accounts):
        mod._CONN = None
    mail_accounts._FERNET = None
    mail_source._PEOPLE_CACHE.clear()
    monkeypatch.setattr(cal, "_tz_for", lambda uid: timezone(timedelta(hours=3)))
    monkeypatch.setattr(focus_items, "sync_source", lambda *a, **k: None)
    monkeypatch.setattr(mail_source, "meeting_people", lambda uid, now: {"anna@partner.ru"})
    mail_accounts.upsert(ME, provider="yandex", email=MINE, auth="oauth_yandex")
    return tmp_path


def _put(key, direction, frm, to, *, hours_ago, subject="Договор", snippet="Пришли смету?", thread="t1",
         message_id="", in_reply_to="", bulk=False, cc=()):
    mail_index.upsert(ME, {
        "mail_key": key, "account_id": "m_1", "direction": direction, "from_addr": frm, "from_name": frm.split("@")[0],
        "to": list(to), "cc": list(cc), "subject": subject, "snippet": snippet, "thread_key": thread,
        "message_id": message_id or f"<{key}>", "in_reply_to": in_reply_to,
        "date_utc": (NOW - timedelta(hours=hours_ago)).isoformat(), "bulk": bulk,
    })


def test_secret_is_encrypted(env):
    acc = mail_accounts.upsert(ME, provider="gmail", email="v@gmail.com", auth="password", secret="abcd efgh")
    raw = mail_accounts._conn().execute("SELECT secret_enc FROM mail_accounts WHERE account_id = ?",
                                        (acc["account_id"],)).fetchone()[0]
    assert "abcd" not in raw
    assert mail_accounts.secret(ME, acc["account_id"]) == "abcd efgh"
    assert "secret" not in str(mail_accounts.public(acc))


def test_candidates_funnel(env):
    _put("out1", "out", MINE, ["ivan@firm.ru"], hours_ago=100, thread="t0")         # писал Ивану раньше
    _put("in1", "in", "ivan@firm.ru", [MINE], hours_ago=5)                          # знакомый → в разбор
    _put("in2", "in", "stranger@x.ru", [MINE], hours_ago=5, thread="t2")            # незнакомый → мимо
    _put("in3", "in", "anna@partner.ru", [MINE], hours_ago=5, thread="t3")          # со встречи → в разбор
    _put("in4", "in", "ivan@firm.ru", ["boss@firm.ru"], hours_ago=5, cc=[MINE], thread="t4")  # копия → мимо
    _put("in5", "in", "news@shop.ru", [MINE], hours_ago=5, bulk=True, thread="t5")  # рассылка — даже не кандидат
    keep, skip = mail_source.candidates(ME, now=NOW)
    assert {r["mail_key"] for r in keep} == {"in1", "in3"}  # out1 старше окна разбора (3 дня)
    assert set(skip) == {"in2", "in4"}  # рассылки в разбор не попадают вовсе


def test_normalize_items_checks_direction_and_dates():
    batch = [{"mail_key": "a", "direction": "in"}, {"mail_key": "b", "direction": "out"}]
    obj = {"items": [
        {"n": 1, "kind": "request", "text": "Прислать смету"},
        {"n": 1, "kind": "promise", "text": "входящее не бывает моим обещанием"},
        {"n": 2, "kind": "promise", "text": "Пришлю КП", "due": "2026-10-24"},
        {"n": 2, "kind": "ask", "text": "Жду реквизиты", "due": "2020-01-01"},
        {"n": 1, "kind": "deadline", "text": "без даты не срок"},
        {"n": 9, "kind": "request", "text": "нет такого письма"},
    ]}
    out = mail_source.normalize_items(obj, batch, date(2026, 10, 22))
    assert [i["kind"] for i in out["a"]] == ["request"]
    assert out["b"] == [{"kind": "promise", "text": "Пришлю КП", "due": "2026-10-24"},
                        {"kind": "ask", "text": "Жду реквизиты", "due": ""}]


def _save(key, items):
    from assistant.services import proactive_ai

    proactive_ai.save(ME, f"mail:{key}", key, "Договор", items)


def test_pending_requests_and_awaiting_card(env, monkeypatch):
    from assistant.services import awaiting_reply

    monkeypatch.setenv("MAIL_FOCUS_ENABLED", "1")
    _put("out0", "out", MINE, ["ivan@firm.ru"], hours_ago=200, thread="t0")
    _put("in1", "in", "ivan@firm.ru", [MINE], hours_ago=5, thread="t1")
    _put("in2", "in", "ivan@firm.ru", [MINE], hours_ago=1, thread="t2")           # свежее 2 часов
    _put("in3", "in", "ivan@firm.ru", [MINE], hours_ago=6, thread="t3")
    _put("out3", "out", MINE, ["ivan@firm.ru"], hours_ago=4, thread="t3", in_reply_to="<in3>")  # ответил
    _put("out4", "out", MINE, ["anna@partner.ru"], hours_ago=100, thread="t4")    # мой вопрос, ответа нет
    for k in ("in1", "in2", "in3"):
        _save(k, [{"kind": "request", "text": f"Пришли смету {k}", "due": "", "who": "ivan"}])
    _save("out4", [{"kind": "ask", "text": "Жду реквизиты", "due": "", "who": ""}])
    reqs = mail_source.pending_requests(ME, now=NOW)
    assert [r["mail_key"] for r in reqs] == ["in1"]
    assert [f["mail_key"] for f in mail_source.followups(ME, now=NOW)] == ["out4"]
    card = awaiting_reply.awaiting_card(ME, now=NOW, chats=False)
    assert card and card["title"] == "Ждут вашего ответа: 2 вопроса"
    assert all(i["id"].startswith("mail:") for i in card["items"])

    from assistant.services import focus_items

    items = focus_items.item_from_card(card)
    assert items[0]["source"]["kind"] == "mail" and items[0]["source"]["from"] == "ivan@firm.ru"


def test_not_me_mutes_sender_then_domain(env, monkeypatch):
    from assistant.services import focus_items

    monkeypatch.setenv("MAIL_FOCUS_ENABLED", "1")
    for i, frm in enumerate(["ivan@firm.ru", "olga@firm.ru"]):
        focus_items.remember(ME, {"key": f"mail:k{i}:h", "card_key": "c", "kind": "mail_request", "text": "x",
                                  "source": {"kind": "mail", "id": f"k{i}", "from": frm}})
    focus_items.apply(ME, "mail:k0:h", "dismiss", reason="not_me")
    senders, domains = mail_source.muted(ME)
    assert senders == {"ivan@firm.ru"} and domains == set()
    focus_items.apply(ME, "mail:k1:h", "dismiss", reason="not_me")
    assert mail_source.muted(ME)[1] == {"firm.ru"}


def test_dry_run_hides_cards(env, monkeypatch):
    monkeypatch.setenv("MAIL_FOCUS_ENABLED", "0")
    _put("out1", "out", MINE, ["ivan@firm.ru"], hours_ago=30, thread="t1")
    _save("out1", [{"kind": "promise", "text": "Пришлю КП", "due": "2026-10-24", "who": ""}])
    assert mail_source.mail_cards(ME, now=NOW) == []
    monkeypatch.setenv("MAIL_FOCUS_ENABLED", "1")
    cards = mail_source.mail_cards(ME, now=NOW)
    assert cards and cards[0]["type"] == "mail_promise"

    from assistant.services import focus_items

    assert focus_items.ingest(ME, cards, NOW) == 1


def test_search_and_people(env):
    _put("in1", "in", "ivan@firm.ru", [MINE], hours_ago=5, subject="Смета по ремонту", snippet="Прикладываю смету")
    _put("in2", "in", "news@shop.ru", [MINE], hours_ago=5, subject="Скидки на смету", bulk=True, thread="t9")
    assert [r["mail_key"] for r in mail_index.search(ME, "что писал Иван про смету")] == ["in1"]
    assert [r["mail_key"] for r in mail_index.with_people(ME, ["ivan@firm.ru"], since=NOW - timedelta(days=30))] == ["in1"]


@pytest.mark.parametrize("subject,cal", [
    ("Приглашение: Звонок проектный офис @ пт 23 окт. 2026 10:00", True),
    ("Обновленное приглашение: Зум доклад", True),
    ("Принято: Встреча с Владимиром", True),
    ("Updated invitation: Weekly sync @ Mon", True),
    ("Declined: Demo", True),
    ("Re: Приглашение на конференцию", False),
    ("Возможно, перенесём: договор", False),
    ("Договор поставки", False),
])
def test_calendar_subjects(subject, cal):
    assert mail_filters.is_calendar(_msg("ivan@firm.ru", MINE, subject=subject)) is cal


def test_calendar_attachment_detected():
    m = _msg("ivan@firm.ru", MINE, subject="Встреча")
    m.add_attachment(b"BEGIN:VCALENDAR", maintype="text", subtype="calendar", filename="invite.ics")
    assert mail_filters.is_calendar(m)
    assert mail_source.is_filtered(m, "ivan@firm.ru", mine={MINE}, rules={"ivan@firm.ru": "allow"})


def test_hide_calendar_cleans_index(env):
    _put("inv", "in", "ulyana@firm.ru", [MINE], hours_ago=5, subject="Приглашение: Звонок проектный офис", thread="t9")
    _put("ok", "in", "ivan@firm.ru", [MINE], hours_ago=5, subject="Договор", thread="t8")
    assert mail_source.hide_calendar(ME) == 1
    assert mail_index.get(ME, "inv")["bulk"] and not mail_index.get(ME, "ok")["bulk"]
