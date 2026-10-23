"""Отправка писем по SMTP: Яндекс — XOAUTH2 (scope mail:smtp), остальные — пароль приложения."""

from __future__ import annotations

import base64
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from assistant.integrations import mail_imap

TIMEOUT = 30


class MailSendError(Exception):
    """Понятная пользователю причина в str()."""


def _host(account: dict[str, Any]) -> tuple[str, int]:
    host = str(account.get("smtp_host") or "")
    port = int(account.get("smtp_port") or 465)
    if not host:
        p = mail_imap.PROVIDERS.get(str(account.get("provider") or ""))
        if not p:
            raise MailSendError("Для этой почты не указан SMTP-сервер — добавьте его в профиле → Почта.")
        host, port = p["smtp"]
    return host, port


def send(account: dict[str, Any], msg: EmailMessage, *, secret: str = "", access_token: str = "") -> None:
    host, port = _host(account)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
            server.starttls(context=ctx)
        try:
            server.ehlo()
            if access_token:
                token = base64.b64encode(
                    f"user={account['email']}\x01auth=Bearer {access_token}\x01\x01".encode()
                ).decode()
                code, resp = server.docmd("AUTH", "XOAUTH2 " + token)
                if code != 235:
                    raise smtplib.SMTPAuthenticationError(code, resp)
            else:
                server.login(str(account["email"]), secret)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError as e:
        if account.get("auth") == "oauth_yandex":
            raise MailSendError("Яндекс не дал права на отправку: войдите через Яндекс ещё раз в профиле → Почта.") from e
        raise MailSendError("Почта не приняла пароль для отправки — переподключите ящик в профиле → Почта.") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise MailSendError("Почта отклонила адрес получателя — проверьте его.") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailSendError("Не удалось отправить письмо — почтовый сервер не ответил. Попробуйте ещё раз.") from e
