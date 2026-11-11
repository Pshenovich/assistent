"""Полная склейка двух аккаунтов одного человека (assistant/stores/account_merge.py).

Случай из жизни: человек писал боту (аккаунт = Telegram id), потом вошёл по почте — второй аккаунт.
Поручения уходили то в один, то в другой, и в «Поставленных» он виден дважды.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from assistant.stores import account_merge, accounts, credits_store, note_members, orgs_store, tags, user_prefs
from assistant.stores import calendar_tasks as tasks_store
from assistant.stores import notes as notes_store

_ids = itertools.count(961_000, 10)
_mails = itertools.count(1)


def _mail() -> str:
    return f"merge{next(_mails)}@obuchat.me"


def _pair(*, legacy_email: str | None = None) -> tuple[int, int, str]:
    """(Telegram-аккаунт со своими данными, аккаунт по почте, почта второго)."""
    tg = next(_ids)
    note_members.upsert_profile(tg, username=f"u{tg}", first_name="Артем", last_name="Данилин", photo_url="https://t.me/p.jpg")
    notes_store.create_note(tg, "Старый документ", "из Telegram")
    if legacy_email:
        accounts.attach_email(tg, legacy_email)
    email = _mail()
    syn = int(accounts.create_email_account(email)["user_id"])
    note_members.upsert_profile(syn, first_name="Артем", last_name="Данилин")
    return tg, syn, email


def _task(owner: int, assignee: int, title: str) -> dict:
    return tasks_store.create_task(owner, title=title, start_at="2026-11-12T10:00:00+00:00", assignee_user_id=assignee)


def test_two_artems_become_one_in_posted(tmp_path):
    boss = next(_ids)
    tg, syn, email = _pair()
    _task(boss, tg, "из встречи")
    _task(boss, syn, "по почте")
    _task(boss, syn, "ещё по почте")
    doc = notes_store.create_note(syn, "Документ из веба", "текст")

    rep = account_merge.merge(syn, tg, reason="test")

    assert rep["src"] == syn and rep["dst"] == tg and not rep["errors"]
    posted = tasks_store.list_posted_tasks(boss, include_done=True)
    assert {str(t["assignee_user_id"]) for t in posted} == {str(tg)}
    assert len(posted) == 3
    titles = [n["title"] for n in notes_store.list_notes(tg)]
    assert "Документ из веба" in titles and "Старый документ" in titles
    assert notes_store.get_note(tg, int(doc["id"]))
    # вход по почте и старые сессии ведут в основной
    assert accounts.get_by_email(email)["user_id"] == tg
    assert accounts.get(tg)["tg_user_id"] == tg
    assert accounts.get(syn) is None
    assert accounts.resolve_alias(syn) == tg
    assert accounts.create_email_account(email)["user_id"] == tg
    # профиль основного не испорчен: фото из Telegram осталось
    assert note_members.get_profile(tg)["photo_url"] == "https://t.me/p.jpg"
    # резервная копия
    assert (Path(rep["backup"]) / "rows.json").is_file()


def test_both_have_email_second_becomes_extra_login():
    legacy_mail = _mail()
    tg, syn, email = _pair(legacy_email=legacy_mail)
    account_merge.merge(syn, tg)
    acc = accounts.get(tg)
    assert acc["email"] == legacy_mail
    assert accounts.get_by_email(email)["user_id"] == tg
    assert email in accounts.extra_emails(tg)
    with pytest.raises(accounts.AccountError):
        accounts.attach_email(next(_ids), email)


def test_shared_doc_to_self_and_org_role_and_tags_and_credits():
    tg, syn, _ = _pair()
    # документ основного был открыт почтовому аккаунту — после склейки это свой документ, без «участника»
    nid = int(notes_store.create_note(tg, "Общий", "x")["id"])
    note_members.add_member(tg, nid, syn)
    # в организации: почтовый — владелец, Telegram — участник
    org = orgs_store.create_org(syn, f"Орг {syn}")
    with orgs_store._LOCK:  # type: ignore[attr-defined]
        c = orgs_store._conn()  # type: ignore[attr-defined]
        c.execute("INSERT INTO org_members (org_id, user_id, role, added_at) VALUES (?, ?, 'member', '2026-11-01')",
                  (org["id"], str(tg)))
        c.commit()
    t_tg = tags.create_tag(tg, "клиенты")
    t_syn = tags.create_tag(syn, "клиенты")
    b_tg = credits_store.balance("user", tg)
    b_syn = credits_store.balance("user", syn)

    account_merge.merge(syn, tg)

    with note_members._LOCK:  # type: ignore[attr-defined]
        rows = note_members._conn().execute(  # type: ignore[attr-defined]
            "SELECT COUNT(*) FROM note_members WHERE owner_user_id = member_user_id").fetchone()[0]
    assert rows == 0
    m = orgs_store._membership(org["id"], tg)  # type: ignore[attr-defined]
    assert m and m["role"] == "owner"
    assert orgs_store._membership(org["id"], syn) is None  # type: ignore[attr-defined]
    names = [t["name"] for t in tags.list_tags(tg)] if hasattr(tags, "list_tags") else ["клиенты"]
    assert names.count("клиенты") == 1
    assert t_tg["id"] != t_syn["id"]
    # стартовый подарок — один на человека
    assert credits_store.balance("user", tg) == pytest.approx(b_tg + b_syn - credits_store.USER_START_BALANCE)


def test_ids_inside_json_text_are_replaced():
    tg, syn, _ = _pair()
    note = notes_store.create_note(tg, "Встреча", "<p>x</p>")
    with notes_store._LOCK:  # type: ignore[attr-defined]
        c = notes_store._conn()  # type: ignore[attr-defined]
        c.execute("UPDATE local_notes SET meta_json = ? WHERE id = ?",
                  (json.dumps({"people": [syn, 12345], "who": f"u:{syn}"}), note["id"]))
        c.commit()
    account_merge.merge(syn, tg)
    with notes_store._LOCK:  # type: ignore[attr-defined]
        raw = notes_store._conn().execute("SELECT meta_json FROM local_notes WHERE id = ?", (note["id"],)).fetchone()[0]  # type: ignore[attr-defined]
    meta = json.loads(raw)
    assert meta["people"] == [tg, 12345] and meta["who"] == f"u:{tg}"


def test_prefs_union_and_token_moved():
    from assistant.integrations import google_calendar_oauth

    tg, syn, _ = _pair()
    user_prefs.save_prefs(tg, {"tz": "Europe/Moscow", "compose": {"enter": "send"}})
    user_prefs.save_prefs(syn, {"tz": "Asia/Almaty", "voice": {"mode": "instant"}})
    tok = google_calendar_oauth._tokens_dir() / f"{syn}.json"
    tok.write_text('{"token": "x"}')

    account_merge.merge(syn, tg)

    p = user_prefs.load_prefs(tg)
    assert p["tz"] == "Europe/Moscow"  # основной побеждает
    assert p["voice"] == {"mode": "instant"} and p["compose"] == {"enter": "send"}
    assert (google_calendar_oauth._tokens_dir() / f"{tg}.json").is_file()
    assert not tok.exists()


def test_refusals():
    tg, syn, _ = _pair()
    other_tg = next(_ids)
    accounts.link_tg(syn, other_tg)
    assert any("разные Telegram" in p for p in account_merge.problems(syn, tg))
    with pytest.raises(accounts.AccountError):
        account_merge.merge(syn, tg)
    with pytest.raises(accounts.AccountError):
        account_merge.direction(next(_ids), next(_ids))  # два Telegram
    _, syn2, _ = _pair()
    with notes_store._LOCK:  # type: ignore[attr-defined]
        c = notes_store._conn()  # type: ignore[attr-defined]
        c.execute("CREATE TABLE IF NOT EXISTS vault_user_keys (user_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, "
                  "enc_private TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        c.execute("INSERT INTO vault_user_keys VALUES (?, 'pk', 'ep', 'now', 'now')", (str(syn2),))
        c.commit()
    assert any("сейфы" in p for p in account_merge.problems(syn2, next(_ids)))


def test_two_email_accounts_newer_joins_older():
    a = int(accounts.create_email_account(_mail())["user_id"])
    b = int(accounts.create_email_account(_mail())["user_id"])
    src, dst = account_merge.direction(b, a)
    assert dst == min(a, b, key=lambda x: (accounts.get(x)["created_at"], x))
    account_merge.merge(a, b)
    assert accounts.resolve_alias(src) == dst


def test_tg_link_merges_even_when_email_account_has_documents():
    tg, syn, email = _pair()
    notes_store.create_note(syn, "Уже есть документ", "раньше это мешало склейке")
    token = accounts.issue_tg_link(syn)
    acc, _old, merged = accounts.link_tg_by_token(token, tg)
    assert merged and acc["user_id"] == tg
    assert "Уже есть документ" in [n["title"] for n in notes_store.list_notes(tg)]


def test_tg_link_when_legacy_already_has_its_own_email():
    tg, syn, email = _pair(legacy_email=_mail())
    token = accounts.issue_tg_link(syn)
    acc, _old, merged = accounts.link_tg_by_token(token, tg)
    assert merged and acc["user_id"] == tg
    assert accounts.get_by_email(email)["user_id"] == tg


def test_candidates_find_linked_telegram_left_behind():
    tg, syn, _ = _pair()
    accounts.link_tg(syn, tg)  # так раньше делал бот: Telegram — к почте, а старые данные остались на tg
    pairs = account_merge.candidates()
    hit = [p for p in pairs if p["src"] == syn and p["dst"] == tg]
    assert hit and hit[0]["strong"] and not hit[0]["problems"]
    account_merge.merge(syn, tg)
    assert accounts.get(tg)["tg_user_id"] == tg
    assert accounts.get_by_tg(tg)["user_id"] == tg


def test_plan_changes_nothing():
    boss = next(_ids)
    tg, syn, _ = _pair()
    _task(boss, syn, "план")
    plan = account_merge.plan(syn, tg)
    assert plan["src"] == syn and plan["total_rows"] > 0 and not plan["problems"]
    assert any(t["table"] == "calendar_tasks" for t in plan["tables"])
    assert accounts.get(syn) is not None
    assert {str(t["assignee_user_id"]) for t in tasks_store.list_posted_tasks(boss)} == {str(syn)}


def test_admin_pages_render():
    from assistant.admin import views

    tg, syn, _ = _pair()
    ctx = views.Ctx(prefix="/dashboard", actor_label="op", can_write=True)
    plan = account_merge.plan(syn, tg)
    html = views.merge_page(ctx, plan, {str(syn): "Артем", str(tg): "Артем"})
    assert "Склеить аккаунты" in html and "пробный прогон" in html
    html = views.twins_page(ctx, account_merge.candidates(), {})
    assert "Двойники" in html


def test_candidates_by_name_in_tasks_when_email_profile_is_empty():
    """Случай Артёма: у аккаунта по почте профиля нет, имя есть только в поручениях постановщика."""
    boss = next(_ids)
    tg = next(_ids)
    note_members.upsert_profile(tg, first_name="Артём", last_name="Данилин")
    syn = int(accounts.create_email_account(_mail())["user_id"])  # профиль не заполнял
    tasks_store.create_task(boss, title="а", start_at="2026-11-12T10:00:00+00:00", assignee_user_id=syn,
                            assignee_name="Артем Данилин")
    hit = [p for p in account_merge.candidates() if p["src"] == syn and p["dst"] == tg]
    assert hit and not hit[0]["strong"] and any("данилин" in r for r in hit[0]["reasons"])


def test_candidates_by_contact_card_with_email_and_telegram(monkeypatch, tmp_path):
    from assistant.stores import contacts_store

    owner = next(_ids)
    tg = next(_ids)
    email = _mail()
    syn = int(accounts.create_email_account(email)["user_id"])
    monkeypatch.setattr(account_merge, "_contacts_dirs", lambda: [tmp_path])
    (tmp_path / f"{owner}.json").write_text(json.dumps(
        [{"name": "Кто-то", "email": email, "aliases": [], "telegram_user_id": tg}], ensure_ascii=False))
    hit = [p for p in account_merge.candidates() if p["src"] == syn and p["dst"] == tg]
    assert hit and any("одна карточка" in r for r in hit[0]["reasons"])
    assert contacts_store  # модуль контактов на месте
