from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from assistant.lib import mail_html
from assistant.services import mail_inbox, mail_reply, mail_source
from assistant.stores import mail_accounts, mail_index, mail_senders

ME = 100
MINE = "vova@yandex.ru"
NOW = datetime.now(timezone.utc)


def test_clean_drops_scripts_handlers_forms_and_tracking():
    out, remote = mail_html.clean(
        '<div onclick="x()"><script>alert(1)</script><p style="color:red">Привет <b>Иван</b></p>'
        '<form><input name=a></form><img src="https://t.co/pixel.gif" width="1">'
        '<a href="javascript:alert(1)">плохо</a> <a href="https://firm.ru">сайт</a></div>'
    )
    assert "script" not in out and "onclick" not in out and "style" not in out and "<form" not in out and "<input" not in out
    assert 'data-src="https://t.co/pixel.gif"' in out and " src=" not in out and remote
    assert "javascript:" not in out and 'href="https://firm.ru"' in out and 'target="_blank"' in out
    assert "<b>Иван</b>" in out


def test_inline_cid_image_becomes_data_uri():
    m = EmailMessage()
    m.set_content("text")
    m.add_alternative('<p>Схема:</p><img src="cid:img1">', subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG....", maintype="image", subtype="png", cid="<img1>")
    out = mail_html.render(m, "Схема:")
    assert 'src="data:image/png;base64,' in out["html_full"] and not out["remote_images"]


def test_quote_removed_in_chat_view_kept_in_full():
    raw = ('<div>Договорились, жду договор.</div><div class="gmail_quote"><div>On Mon, Ivan wrote:</div>'
           '<blockquote>Пришлите договор</blockquote></div>')
    full, _ = mail_html.clean(raw)
    short = mail_html.drop_quotes(full)
    assert "Договорились" in short and "Пришлите договор" not in short and "Пришлите договор" in full


def test_signature_marked():
    out, _ = mail_html.clean('<p>Ок</p><div class="gmail_signature">Иван Петров, CEO</div>')
    assert 'class="mail-sig"' in out


def test_plain_text_to_html_links_and_paragraphs():
    out = mail_html.text_to_html("Привет!\n\nСсылка: https://firm.ru/doc?a=1&b=2\nвторая строка")
    assert out.startswith("<p>Привет!</p>") and 'href="https://firm.ru/doc?a=1&amp;b=2"' in out and "<br>" in out


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import focus_items, proactive_ai
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    for mod in (notes_store, focus_items, proactive_ai, mail_index, mail_accounts, mail_senders, mail_inbox):
        mod._CONN = None
    mail_accounts._FERNET = None
    monkeypatch.setattr(mail_reply, "_fetch_full", lambda uid, rows: {})
    acc = mail_accounts.upsert(ME, provider="gmail", email=MINE, auth="password", secret="x")
    aid = acc["account_id"]

    def put(key, direction, frm, *, thread="t1", hours_ago=5, irt="", subject="Re: Договор", bulk=False, to=(MINE,)):
        mail_index.upsert(ME, {"mail_key": key, "account_id": aid, "direction": direction, "from_addr": frm,
                               "from_name": "Иван" if frm != MINE else "", "to": list(to), "subject": subject,
                               "snippet": f"текст {key}", "thread_key": thread, "message_id": f"<{key}>",
                               "in_reply_to": irt, "date_utc": (NOW - timedelta(hours=hours_ago)).isoformat(), "bulk": bulk})
    put("a", "in", "ivan@firm.ru", hours_ago=10, subject="Договор")
    put("b", "out", MINE, hours_ago=8, irt="<a>", to=("ivan@firm.ru",))
    put("c", "in", "ivan@firm.ru", hours_ago=2, irt="<a>")
    put("n", "in", "news@shop.ru", thread="t9", bulk=True, subject="Скидки")
    return aid


def test_threads_list_groups_and_counts_unread(env):
    res = mail_inbox.threads(ME)
    assert [t["thread_key"] for t in res["threads"]] == ["t1"]  # рассылка не в списке
    t = res["threads"][0]
    assert t["subject"] == "Договор" and t["count"] == 3 and t["unread"] == 2
    assert t["people"] == [{"addr": "ivan@firm.ru", "name": "Иван"}] and t["last"]["mail_key"] == "c"
    mail_inbox.set_read(ME, ["a", "c"])
    assert mail_inbox.threads(ME)["threads"][0]["unread"] == 0
    assert mail_inbox.threads(ME, filt="unread")["threads"] == []


def test_thread_replies_point_to_exact_letter(env):
    t = mail_inbox.thread(ME, "t1")
    by = {m["mail_key"]: m for m in t["messages"]}
    assert by["b"]["reply_to_key"] == "a" and by["c"]["reply_to_key"] == "a" and by["a"]["reply_to_key"] == ""
    assert by["c"]["html"] == "<p>текст c</p>" and by["c"]["unread"]


def test_open_in_gmail_url(env):
    assert mail_inbox.open_in_mail_url(ME, "c").endswith("rfc822msgid%3Ac")


def test_clean_subject():
    assert mail_inbox.clean_subject("Re: Fwd: RE: Договор") == "Договор"
    assert mail_inbox.clean_subject("") == "Без темы"


def test_replied_thread_shows_filtered_original(env):
    aid = env
    mail_index.upsert(ME, {"mail_key": "pi", "account_id": aid, "direction": "in", "from_addr": "info@paylate.info",
                           "to": [MINE], "subject": "Оплата", "snippet": "", "thread_key": "tp", "message_id": "<pi>",
                           "date_utc": (NOW - timedelta(hours=30)).isoformat(), "bulk": True})
    mail_index.upsert(ME, {"mail_key": "po", "account_id": aid, "direction": "out", "from_addr": MINE,
                           "to": ["info@paylate.info"], "subject": "Re: Оплата", "snippet": "Спасибо! А пени?",
                           "thread_key": "tp", "message_id": "<po>", "in_reply_to": "<pi>",
                           "date_utc": (NOW - timedelta(hours=20)).isoformat()})
    mail_index.upsert(ME, {"mail_key": "inv", "account_id": aid, "direction": "in", "from_addr": "ivan@firm.ru", "to": [MINE],
                           "subject": "Приглашение: созвон", "thread_key": "tp", "message_id": "<inv>",
                           "date_utc": (NOW - timedelta(hours=10)).isoformat(), "bulk": True})
    t = mail_inbox.thread(ME, "tp")
    assert [m["mail_key"] for m in t["messages"]] == ["pi", "po"]  # исходное письмо видно, приглашение — нет
    listed = {x["thread_key"]: x for x in mail_inbox.threads(ME)["threads"]}
    assert listed["tp"]["count"] == 2 and "t9" not in listed  # рассылка из чужой ветки по-прежнему скрыта


def test_rich_letter_keeps_safe_layout():
    raw = ('<table width="600" bgcolor="#ffffff" style="border-collapse:collapse"><tr><td style="color:#111;font-size:20px;'
           'background:url(https://t.co/p.gif);position:fixed" onclick="x()">Самое важное о techinformpress.ru</td>'
           '<td align="right">258</td></tr></table>')
    assert mail_html.is_rich(raw)
    out, _ = mail_html.clean(raw, rich=True)
    assert 'width="600"' in out and 'bgcolor="#ffffff"' in out and "font-size:20px" in out and 'align="right"' in out
    assert "url(" not in out and "position" not in out and "onclick" not in out
    plain, _ = mail_html.clean(raw)  # обычное письмо — без чужих стилей
    assert "style=" not in plain


def test_devnull_is_robot():
    from assistant.services import mail_filters

    assert mail_filters.is_robot("devnull@webmaster.yandex.ru")
