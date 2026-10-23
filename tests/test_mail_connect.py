import pytest

from assistant.integrations import mail_imap
from assistant.services import mail_connect, mail_source
from assistant.stores import mail_accounts, mail_index

ME = 100


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("MAIL_SECRET_KEY", "")
    for mod in (notes_store, mail_index, mail_accounts):
        mod._CONN = None
    mail_accounts._FERNET = None
    calls = {"login": [], "kick": 0}
    monkeypatch.setattr(mail_source, "kick", lambda uid: calls.__setitem__("kick", calls["kick"] + 1))

    def login(acc, *, secret="", access_token=""):
        calls["login"].append((acc, secret, access_token))
        if secret == "bad":
            raise mail_imap.MailAuthError("Почта не приняла пароль.")

    monkeypatch.setattr(mail_source, "check_login", login)
    monkeypatch.setattr(mail_connect, "mx_provider", lambda d: {"obuchat.me": "gmail"}.get(d, ""))
    monkeypatch.setattr(mail_imap, "autoconfig", lambda e: None if e.endswith("@unknown.org") else _AUTOCONFIG(e))
    return calls


_AUTOCONFIG = mail_imap.autoconfig  # известные провайдеры — без сети; неизвестный домен подменяем


def test_detect_known_and_corporate(env):
    assert mail_connect.detect("Vova@Gmail.com")["provider"] == "gmail"
    corp = mail_connect.detect("v@obuchat.me")
    assert corp["provider"] == "gmail" and corp["corporate"] and "администратор" in corp["hint"]
    assert corp["imap_host"] == "imap.gmail.com"
    assert mail_connect.detect("v@unknown.org")["needs_server"]
    with pytest.raises(mail_connect.ConnectError) as e:
        mail_connect.detect("не почта")
    assert e.value.step == "email"


def test_connect_password_saves_encrypted_and_kicks(env):
    res = mail_connect.connect(ME, {"provider": "gmail", "email": "v@gmail.com", "password": "abcd efgh ijkl mnop"})
    assert res["ok"] and res["account"]["email"] == "v@gmail.com" and res["account"]["send_enabled"]
    assert env["login"][0][1] == "abcdefghijklmnop"  # пробелы из пароля Google убраны
    assert mail_accounts.secret(ME, res["account"]["account_id"]) == "abcdefghijklmnop"
    assert env["kick"] == 1


def test_connect_bad_password_points_to_step(env):
    with pytest.raises(mail_connect.ConnectError) as e:
        mail_connect.connect(ME, {"provider": "mailru", "email": "v@mail.ru", "password": "bad"})
    assert e.value.step == "password" and e.value.code == "auth"
    assert mail_accounts.list_accounts(ME) == []


def test_connect_unknown_server_asks_for_it(env):
    with pytest.raises(mail_connect.ConnectError) as e:
        mail_connect.connect(ME, {"provider": "imap", "email": "v@unknown.org", "password": "x"})
    assert e.value.step == "server"
    res = mail_connect.connect(ME, {"provider": "imap", "email": "v@unknown.org", "password": "x", "imap_host": "imap.unknown.org"})
    assert res["ok"] and res["send_scope"] is False


def test_connect_yandex_needs_oauth_then_works(env, monkeypatch):
    from assistant.integrations import telemost_oauth

    monkeypatch.setattr(telemost_oauth, "read_token_store", lambda uid: {})
    assert mail_connect.connect(ME, {"provider": "yandex"}) == {"ok": False, "need_oauth": True}
    monkeypatch.setattr(telemost_oauth, "read_token_store",
                        lambda uid: {"access_token": "tok", "yandex_email": "Vova@yandex.ru", "scope": "mail:imap_ro telemost-api:conferences.create"})
    monkeypatch.setattr(telemost_oauth, "get_valid_access_token", lambda uid: "tok")
    res = mail_connect.connect(ME, {"provider": "yandex", "send_enabled": True})
    assert res["ok"] and res["account"]["auth"] == "oauth_yandex"
    assert res["send_scope"] is False and not res["account"]["send_enabled"]  # без mail:smtp отвечать нельзя
    assert env["login"][-1][2] == "tok"


def test_disconnect_purges_index(env):
    res = mail_connect.connect(ME, {"provider": "gmail", "email": "v@gmail.com", "password": "p"})
    aid = res["account"]["account_id"]
    mail_index.upsert(ME, {"mail_key": f"{aid}:1", "account_id": aid, "subject": "x", "date_utc": "2026-10-22T10:00:00+00:00"})
    assert mail_connect.disconnect(ME, aid)
    assert mail_index.get(ME, f"{aid}:1") is None and mail_accounts.list_accounts(ME) == []


def test_connect_yandex_without_scope_in_token_checks_for_real(env, monkeypatch):
    """Яндекс не присылает scope при обмене кода — подключаем по факту входа в IMAP/SMTP."""
    from assistant.integrations import mail_smtp, telemost_oauth

    monkeypatch.setattr(telemost_oauth, "read_token_store", lambda uid: {"access_token": "tok", "yandex_login": "vova"})
    monkeypatch.setattr(telemost_oauth, "get_valid_access_token", lambda uid: "tok")
    monkeypatch.setattr(mail_smtp, "can_auth", lambda acc, **kw: True)
    res = mail_connect.connect(ME, {"provider": "yandex", "send_enabled": True})
    assert res["ok"] and res["account"]["email"] == "vova@yandex.ru" and res["account"]["send_enabled"]
