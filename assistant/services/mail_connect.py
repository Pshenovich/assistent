"""Подключение почты из профиля: определить провайдера, проверить вход, сохранить ящик, запустить первую синхронизацию."""

from __future__ import annotations

import re
from typing import Any

from assistant.integrations import mail_imap
from assistant.services import mail_source
from assistant.stores import mail_accounts, mail_index

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConnectError(Exception):
    """Понятная пользователю ошибка + шаг мастера, к которому вернуться (step)."""

    def __init__(self, message: str, *, step: str = "", code: str = "") -> None:
        super().__init__(message)
        self.step = step
        self.code = code


def mx_provider(domain: str) -> str:
    """Чей почтовый сервер у домена (по MX через DNS-over-HTTPS): gmail / yandex / mailru / ''."""
    try:
        import requests

        r = requests.get("https://dns.google/resolve", params={"name": domain, "type": "MX"}, timeout=5)
        answers = " ".join(str(a.get("data") or "") for a in (r.json().get("Answer") or [])).lower()
    except Exception:
        return ""
    if "google.com" in answers or "googlemail.com" in answers:
        return "gmail"
    if "yandex" in answers:
        return "yandex"
    if "mail.ru" in answers:
        return "mailru"
    return ""


def detect(email: str) -> dict[str, Any]:
    """Мастер по адресу: какой провайдер, сервер IMAP/SMTP, подсказки (корпоративный Google и т.п.)."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ConnectError("Похоже, в адресе опечатка — проверьте, пожалуйста.", step="email")
    domain = email.rsplit("@", 1)[1]
    prov = mail_imap.provider_for(email)
    hint = ""
    corporate = False
    if prov == "imap":
        via_mx = mx_provider(domain)
        if via_mx:
            prov, corporate = via_mx, True
    if prov == "gmail" and corporate:
        hint = ("Это корпоративная почта Google Workspace. Если в настройках Google не будет раздела «Пароли приложений», "
                "их отключил администратор — попросите его включить или подключите другую почту.")
    elif prov == "yandex" and corporate:
        hint = "Это почта Яндекс 360 на домене компании — подключается так же, как обычный Яндекс."
    cfg = mail_imap.autoconfig(email if not corporate else f"x@{_provider_domain(prov)}") or {}
    out = {"email": email, "provider": prov, "corporate": corporate, "hint": hint,
           "imap_host": cfg.get("imap_host") or "", "imap_port": cfg.get("imap_port") or 993,
           "smtp_host": cfg.get("smtp_host") or "", "smtp_port": cfg.get("smtp_port") or 465}
    out["needs_server"] = not out["imap_host"]
    return out


def _provider_domain(prov: str) -> str:
    return {"gmail": "gmail.com", "yandex": "yandex.ru", "mailru": "mail.ru"}.get(prov, "example.com")


def yandex_oauth_state(uid: int) -> dict[str, Any]:
    """Есть ли у пользователя Яндекс-токен с правами на почту (через подключение Телемоста)."""
    from assistant.integrations import telemost_oauth

    store = telemost_oauth.read_token_store(int(uid)) or {}
    scopes = str(store.get("scope") or "").split()
    return {
        "connected": bool(store),
        "email": str(store.get("yandex_email") or store.get("yandex_login") or "").strip().lower(),
        "read": "mail:imap_ro" in scopes or "mail:imap_full" in scopes,
        "send": "mail:smtp" in scopes,
    }


def connect(uid: int, body: dict[str, Any]) -> dict[str, Any]:
    uid = int(uid)
    provider = str(body.get("provider") or "").strip().lower()
    send = bool(body.get("send_enabled", True))
    if provider == "yandex" and not body.get("password"):
        st = yandex_oauth_state(uid)
        if not st["connected"] or not st["read"] or not st["email"]:
            return {"ok": False, "need_oauth": True}
        acc = {"provider": "yandex", "email": st["email"], "auth": "oauth_yandex",
               **{k: v for k, v in zip(("imap_host", "imap_port"), mail_imap.PROVIDERS["yandex"]["imap"])},
               **{k: v for k, v in zip(("smtp_host", "smtp_port"), mail_imap.PROVIDERS["yandex"]["smtp"])}}
        from assistant.integrations import telemost_oauth

        try:
            mail_source.check_login(acc, access_token=telemost_oauth.get_valid_access_token(uid))
        except mail_imap.MailAuthError as e:
            raise ConnectError(str(e), step="imap", code="imap_disabled") from e
        except Exception as e:
            raise ConnectError(mail_imap.humanize_login_error("yandex", e), step="imap") from e
        saved = mail_accounts.upsert(uid, send_enabled=send and st["send"], **acc)
        mail_source.kick(uid)
        return {"ok": True, "account": mail_accounts.public(saved), "send_scope": st["send"]}

    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "").strip()
    if not EMAIL_RE.match(email):
        raise ConnectError("Похоже, в адресе опечатка — проверьте, пожалуйста.", step="email")
    if not password:
        raise ConnectError("Вставьте пароль приложения.", step="password")
    cfg: dict[str, Any] = {}
    if body.get("imap_host"):
        cfg = {"imap_host": str(body["imap_host"]).strip(), "imap_port": int(body.get("imap_port") or 993),
               "smtp_host": str(body.get("smtp_host") or "").strip(), "smtp_port": int(body.get("smtp_port") or 465)}
    else:
        d = detect(email)
        if d["needs_server"]:
            raise ConnectError("Не удалось определить сервер почты — укажите его вручную.", step="server", code="server")
        cfg = {k: d[k] for k in ("imap_host", "imap_port", "smtp_host", "smtp_port")}
        provider = provider if provider in mail_imap.PROVIDERS else d["provider"]
    if provider not in mail_imap.PROVIDERS:
        provider = "imap"
    if provider == "gmail":
        password = password.replace(" ", "")
    acc = {"provider": provider, "email": email, "auth": "password", **cfg}
    try:
        mail_source.check_login(acc, secret=password)
    except mail_imap.MailAuthError as e:
        raise ConnectError(str(e), step="password", code="auth") from e
    except Exception as e:
        raise ConnectError(mail_imap.humanize_login_error(provider, e), step="server" if body.get("imap_host") else "password") from e
    saved = mail_accounts.upsert(uid, secret=password, send_enabled=send and bool(cfg.get("smtp_host")), **acc)
    mail_source.kick(uid)
    return {"ok": True, "account": mail_accounts.public(saved), "send_scope": bool(cfg.get("smtp_host"))}


def disconnect(uid: int, account_id: str) -> bool:
    mail_index.purge_account(uid, account_id)
    return mail_accounts.remove(uid, account_id)


def accounts(uid: int) -> list[dict[str, Any]]:
    return [mail_accounts.public(a) for a in mail_accounts.list_accounts(uid)]
