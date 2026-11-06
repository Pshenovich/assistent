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


# ---------- вёрстка: таблицы и инлайн-стили (почтовые клиенты не знают CSS-классов и SVG) ----------

_BRAND = "#5b5fe0"  # фиолетовый орбиты, затемнён под белый текст кнопки
_BRAND_SOFT = "#eef0ff"
_INK = "#14142b"
_MUTED = "#6b6f80"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_DISPLAY = "'Unbounded','Segoe UI',Helvetica,Arial,sans-serif"


def _site() -> str:
    from assistant.lib.webapp_public import webapp_entry_url

    url = webapp_entry_url()
    return url.split("/webapp", 1)[0] if "/webapp" in url else url.rstrip("/")


def _button(url: str, label: str) -> str:
    u = html.escape(url, quote=True)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:28px 0 8px">'
        f'<tr><td align="center" bgcolor="{_BRAND}" style="border-radius:12px">'
        f'<a href="{u}" target="_blank" style="display:inline-block;padding:14px 28px;font-family:{_FONT};'
        f'font-size:16px;font-weight:600;line-height:20px;color:#ffffff;text-decoration:none;border-radius:12px">'
        f"{html.escape(label)}</a></td></tr></table>"
    )


def _layout(*, preheader: str, title: str, body_html: str, footer: str = "") -> str:
    site = _site()
    logo = html.escape(site + "/webapp/icons/icon-192.png", quote=True)
    foot = footer or "Вы получили это письмо, потому что ваш адрес указали в Орбите."
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f2f3f8">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{html.escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f2f3f8">
<tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px">
<tr><td style="padding:0 4px 20px">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="padding-right:10px"><img src="{logo}" width="36" height="36" alt="" style="display:block;border-radius:9px"></td>
<td style="font-family:{_DISPLAY};font-size:20px;font-weight:600;letter-spacing:-0.3px;color:{_INK}">Orbita</td>
</tr></table></td></tr>
<tr><td style="background:#ffffff;border-radius:20px;padding:36px 32px;font-family:{_FONT};color:{_INK}">
<div style="font-family:{_DISPLAY};font-size:22px;line-height:30px;font-weight:600;letter-spacing:-0.3px;margin:0 0 14px">{html.escape(title)}</div>
{body_html}
</td></tr>
<tr><td style="padding:20px 8px 0;font-family:{_FONT};font-size:12px;line-height:18px;color:{_MUTED}">
{html.escape(foot)}<br><a href="{html.escape(site, quote=True)}" style="color:{_MUTED}">{html.escape(site.replace('https://', ''))}</a>
</td></tr>
</table></td></tr></table></body></html>"""


def _p(text_html: str, *, muted: bool = False, size: int = 15) -> str:
    color = _MUTED if muted else _INK
    return f'<p style="margin:0 0 12px;font-size:{size}px;line-height:{size + 8}px;color:{color}">{text_html}</p>'


def _code_box(code: str) -> str:
    spaced = " ".join(code)
    return (
        f'<div style="margin:20px 0 4px;padding:18px 0;background:{_BRAND_SOFT};border-radius:14px;text-align:center;'
        f"font-family:'SF Mono',Menlo,Consolas,monospace;font-size:34px;line-height:40px;font-weight:700;"
        f'letter-spacing:6px;color:{_INK}">{html.escape(spaced)}</div>'
    )


def send_login_code(to: str, code: str, ttl_min: int = 10, login_url: str | None = None) -> None:
    """Код входа. С login_url — ещё и кнопка «Войти»: вход без ввода кода (только для входа на сайте)."""
    if login_url:
        lead = "Нажмите кнопку — и вы в Орбите. Или введите код на экране входа:"
        text = f"Войти в Орбиту: {login_url}\n\nИли введите код: {code}\n\n"
    else:
        lead = "Введите этот код в Орбите, чтобы подтвердить почту:"
        text = f"Код подтверждения почты в Орбите: {code}\n\n"
    text += f"Код действует {ttl_min} минут. Если это были не вы — просто проигнорируйте письмо."
    body = _p(html.escape(lead))
    if login_url:
        body += _button(login_url, "Войти в Орбиту")
    body += _code_box(code)
    body += _p(
        f"Код действует {ttl_min} минут и сработает один раз. Если это были не вы — просто проигнорируйте письмо.",
        muted=True, size=13,
    )
    title = "Вход в Орбиту" if login_url else "Подтверждение почты"
    send(
        to,
        f"{code} — код {'входа в Орбиту' if login_url else 'подтверждения почты'}",
        text,
        _layout(preheader=f"Ваш код: {code}", title=title, body_html=body),
    )


def send_org_invite(to: str, org_name: str, inviter: str, url: str) -> None:
    text = (
        f"{inviter} пригласил(а) вас в организацию «{org_name}» в Орбите.\n\n"
        f"Войти: {url}\n\nВход — по этой почте ({to}), код придёт письмом."
    )
    body = (
        _p(f"<b>{html.escape(inviter)}</b> пригласил(а) вас в организацию <b>«{html.escape(org_name)}»</b>.")
        + _p("Орбита собирает встречи, задачи и договорённости команды в одном месте, а Лео держит их под контролем.",
             muted=True, size=14)
        + _button(url, "Войти в Орбиту")
        + _p(f"Вход — по этой почте ({html.escape(to)}): нажмите кнопку, и мы пришлём код.", muted=True, size=13)
    )
    send(
        to,
        f"Вас пригласили в «{org_name}» — Орбита",
        text,
        _layout(preheader=f"{inviter} ждёт вас в «{org_name}»", title=f"Приглашение в «{org_name}»", body_html=body),
    )
