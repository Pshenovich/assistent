"""Граничные случаи проверки initData: подделка, срок жизни, запасные токены, кодировка."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import quote, urlencode

import pytest

from assistant.lib import telegram_webapp_auth as auth

LEO = "111:LEO-TOKEN"
DONATELLO = "222:DONATELLO-TOKEN"


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", LEO)
    for key in ("TG_DONATELLO_BOT_TOKEN", "TELEGRAM_WEBAPP_AUTH_EXTRA_TOKENS", "TELEGRAM_WEBAPP_AUTH_MAX_AGE_SEC"):
        monkeypatch.delenv(key, raising=False)


def _fields(user=None, *, auth_date=None, **extra) -> dict[str, str]:
    out = {"auth_date": str(int(time.time()) if auth_date is None else auth_date), **extra}
    if user is not False:
        out["user"] = user if isinstance(user, str) else json.dumps(user or {"id": 42, "first_name": "T"})
    return out


def _sign(fields: dict[str, str], token: str = LEO) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def test_tampered_user_after_signing_is_rejected():
    signed = _sign(_fields({"id": 42, "first_name": "T"}))
    forged = signed.replace("%22id%22%3A+42", "%22id%22%3A+1")
    assert forged != signed
    with pytest.raises(ValueError, match="подпись"):
        auth.parse_and_validate_init_data(forged)


def test_missing_hash_is_rejected():
    with pytest.raises(ValueError, match="подпись"):
        auth.parse_and_validate_init_data(urlencode(_fields()))


@pytest.mark.parametrize("init_data", ["", "   "])
def test_empty_init_data_is_rejected(init_data):
    with pytest.raises(ValueError, match="Пустой"):
        auth.parse_and_validate_init_data(init_data)


def test_no_bot_token_configured_is_rejected(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="токен"):
        auth.parse_and_validate_init_data(_sign(_fields()))


def test_default_max_age_is_one_day():
    fresh = _sign(_fields(auth_date=int(time.time()) - 86400 + 60))
    stale = _sign(_fields(auth_date=int(time.time()) - 86400 - 60))
    assert auth.parse_and_validate_init_data(fresh)
    with pytest.raises(ValueError, match="Устаревший"):
        auth.parse_and_validate_init_data(stale)


@pytest.mark.parametrize(("raw", "expected"), [("3600", 3600), ("5", 60), ("abc", 86400), ("", 86400)])
def test_max_age_env(monkeypatch, raw, expected):
    monkeypatch.setenv("TELEGRAM_WEBAPP_AUTH_MAX_AGE_SEC", raw)
    assert auth._auth_max_age_sec() == expected


@pytest.mark.parametrize("auth_date", ["0", "-5", "soon"])
def test_bad_auth_date_is_rejected(auth_date):
    with pytest.raises(ValueError):
        auth.parse_and_validate_init_data(_sign(_fields(auth_date=auth_date)))


def test_donatello_and_extra_tokens_are_accepted(monkeypatch):
    monkeypatch.setenv("TG_DONATELLO_BOT_TOKEN", DONATELLO)
    monkeypatch.setenv("TELEGRAM_WEBAPP_AUTH_EXTRA_TOKENS", " '333:EXTRA-A' , \"444:EXTRA-B\" ")
    for token in (DONATELLO, "333:EXTRA-A", "444:EXTRA-B"):
        assert auth.user_payload_from_init_data(_sign(_fields(), token))["id"] == 42


def test_unknown_bot_token_is_rejected(monkeypatch):
    monkeypatch.setenv("TG_DONATELLO_BOT_TOKEN", DONATELLO)
    with pytest.raises(ValueError, match="подпись"):
        auth.parse_and_validate_init_data(_sign(_fields(), "999:STRANGER"))


def test_explicit_bot_token_is_tried_first_and_env_still_works():
    assert auth.user_payload_from_init_data(_sign(_fields(), "555:EXPLICIT"), bot_token="555:EXPLICIT")["id"] == 42
    assert auth.user_payload_from_init_data(_sign(_fields()), bot_token="555:EXPLICIT")["id"] == 42


def test_whole_string_percent_encoded_once_is_accepted():
    assert auth.user_payload_from_init_data(quote(_sign(_fields()), safe=""))["id"] == 42


@pytest.mark.parametrize(
    ("user", "message"),
    [(False, "нет поля user"), ("{not json", "не является JSON"), ("[1, 2]", "объектом")],
)
def test_user_field_must_be_json_object(user, message):
    with pytest.raises(ValueError, match=message):
        auth.user_payload_from_init_data(_sign(_fields(user)))


@pytest.mark.parametrize("user", [{"first_name": "T"}, {"id": "abc"}, {"id": None}])
def test_user_id_must_be_integer(user):
    with pytest.raises(ValueError, match="id"):
        auth.telegram_user_id_from_init_data(_sign(_fields(user)))


def test_user_id_extracted():
    assert auth.telegram_user_id_from_init_data(_sign(_fields({"id": "77"}))) == 77
