"""Служебные письма Орбиты (коды входа, приглашения) через SMTP-релей.

На проде — почтовый сервис Selectel (smtp.mail.selcloud.ru): обычные порты 25/465/587
наружу из Selectel закрыты, релей слушает свои (1127 — SSL, 1126 — STARTTLS).

Env: TX_MAIL_HOST, TX_MAIL_PORT, TX_MAIL_USER, TX_MAIL_PASSWORD, TX_MAIL_FROM
(«Орбита <no-reply@getorbita.me>»), TX_MAIL_TLS = ssl | starttls (по умолчанию ssl,
кроме портов 587/1126/2525). Без TX_MAIL_HOST письма не уходят — текст пишется в лог
(локальная разработка).
"""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

TIMEOUT = 20


class TxMailError(Exception):
    pass


def configured() -> bool:
    return bool((os.getenv("TX_MAIL_HOST") or "").strip())


def _sender() -> tuple[str, str]:
    raw = (os.getenv("TX_MAIL_FROM") or "").strip() or "Орбита <no-reply@getorbita.me>"
    name, addr = parseaddr(raw)
    return name or "Орбита", addr or "no-reply@getorbita.me"


def _use_starttls(port: int) -> bool:
    mode = (os.getenv("TX_MAIL_TLS") or "").strip().lower()
    if mode in ("ssl", "starttls"):
        return mode == "starttls"
    return port in (25, 587, 1126, 2525)


def send(to: str, subject: str, text: str, html_body: str | None = None) -> None:
    name, addr = _sender()
    msg = EmailMessage()
    msg["From"] = formataddr((name, addr))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=addr.split("@")[-1])
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    host = (os.getenv("TX_MAIL_HOST") or "").strip()
    if not host:
        print(f"[tx-mail] not configured; to={to} subject={subject!r}\n{text}")
        return
    port = int(os.getenv("TX_MAIL_PORT") or 1127)
    user = (os.getenv("TX_MAIL_USER") or "").strip()
    password = os.getenv("TX_MAIL_PASSWORD") or ""
    ctx = ssl.create_default_context()
    try:
        if _use_starttls(port):
            server: smtplib.SMTP = smtplib.SMTP(host, port, timeout=TIMEOUT)
            server.starttls(context=ctx)
        else:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx)
        try:
            server.ehlo()
            if user:
                server.login(user, password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except (smtplib.SMTPException, OSError) as e:
        print(f"[tx-mail] send failed to={to} err={e!r}")
        raise TxMailError("Не получилось отправить письмо. Попробуйте ещё раз через минуту.") from e


def _layout(title: str, body_html: str) -> str:
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:0 auto;'
        'padding:24px;color:#1b1b1f">'
        f'<div style="font-size:18px;font-weight:600;margin-bottom:16px">{html.escape(title)}</div>'
        f"{body_html}"
        '<div style="margin-top:24px;font-size:12px;color:#8a8a93">Орбита · getorbita.me</div></div>'
    )


def send_login_code(to: str, code: str, ttl_min: int = 10) -> None:
    text = (
        f"Код входа в Орбиту: {code}\n\n"
        f"Код действует {ttl_min} минут. Если вы не запрашивали вход — просто проигнорируйте письмо."
    )
    body = (
        '<div style="font-size:14px">Код входа в Орбиту:</div>'
        f'<div style="font-size:32px;font-weight:700;letter-spacing:6px;margin:12px 0">{html.escape(code)}</div>'
        f'<div style="font-size:13px;color:#55555e">Код действует {ttl_min} минут. '
        "Если вы не запрашивали вход — просто проигнорируйте письмо.</div>"
    )
    send(to, f"{code} — код входа в Орбиту", text, _layout("Вход в Орбиту", body))


def send_org_invite(to: str, org_name: str, inviter: str, url: str) -> None:
    text = (
        f"{inviter} добавил(а) вас в организацию «{org_name}» в Орбите.\n\n"
        f"Войти: {url}\n\nВход — по этой почте, код придёт письмом."
    )
    body = (
        f'<div style="font-size:14px">{html.escape(inviter)} добавил(а) вас в организацию '
        f"«{html.escape(org_name)}» в Орбите.</div>"
        f'<div style="margin:20px 0"><a href="{html.escape(url, quote=True)}" style="background:#5b5bd6;color:#fff;'
        'padding:10px 18px;border-radius:8px;text-decoration:none;font-size:14px">Войти в Орбиту</a></div>'
        '<div style="font-size:13px;color:#55555e">Вход — по этой почте, код придёт письмом.</div>'
    )
    send(to, f"Вас добавили в «{org_name}» — Орбита", text, _layout("Приглашение в Орбиту", body))
