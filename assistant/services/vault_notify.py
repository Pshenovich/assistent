"""Уведомления про сейфы: уход участника, запросы доступа и ответы на них.

Каналы — Telegram-бот, Web Push и письмо (решение владельца 2026-11-08). В тексте только название сейфа
и имена людей: содержимое сейфов сервер не знает и сюда оно не попадает (правило 17).
"""

from __future__ import annotations

import html
from typing import Any


def vault_url(vault_id: int) -> str:
    from assistant.lib.webapp_public import webapp_entry_url

    base = webapp_entry_url()
    return f"{base}{'&' if '?' in base else '?'}vault={int(vault_id)}"


ROLE_WORD = {"viewer": "смотреть", "editor": "смотреть и править", "owner": "владеть сейфом"}


def message(ev: dict[str, Any]) -> tuple[str, str, str]:
    """(заголовок, текст, тема письма) для события из ``vaults.watch()``."""
    name = ev.get("vault_name") or "Сейф"
    who = ev.get("who_name") or "Участник"
    by = ev.get("by_name") or "владелец сейфа"
    kind = ev.get("kind")
    if kind == "departure":
        return (
            f"Сейф «{name}»: смените пароли",
            f"{who} больше нет в сейфе «{name}». Ключ сейфа сменится сам, а пароли, которые он(а) мог(ла) видеть, "
            "лучше сменить — список в сейфе.",
            f"Смените пароли в сейфе «{name}»",
        )
    if kind == "access_request":
        return (
            f"{who} просит доступ к сейфу «{name}»",
            f"{who} просит доступ к сейфу «{name}». Открыть или отклонить — в самом сейфе.",
            f"Запрос доступа к сейфу «{name}»",
        )
    if kind == "access_approved":
        role = ROLE_WORD.get(str(ev.get("role") or ""), "смотреть")
        return (
            f"Доступ к сейфу «{name}» открыт",
            f"{by} открыл(а) вам сейф «{name}» — можно {role}. Ключ придёт, как только кто-то из участников откроет сейфы.",
            f"Вам открыли сейф «{name}»",
        )
    return (
        f"Запрос к сейфу «{name}» отклонён",
        f"{by} отклонил(а) ваш запрос доступа к сейфу «{name}».",
        f"Запрос к сейфу «{name}» отклонён",
    )


def send_event(ev: dict[str, Any]) -> int:
    """Разослать одно событие всем адресатам. Возвращает, скольким ушло хоть что-то."""
    from assistant.lib.telegram_notify import send_message
    from assistant.services import transactional_mail
    from assistant.services.discussion_notify import send_web_push
    from assistant.stores import accounts

    title, text, subject = message(ev)
    url = vault_url(int(ev["vault_id"]))
    reached = 0
    for uid in ev.get("to") or []:
        ok = False
        try:
            if 0 < int(uid) < accounts.SYNTHETIC_BASE:  # у входа по почте Telegram нет
                ok = bool(send_message(int(uid), f"🔐 <b>{html.escape(title)}</b>\n{html.escape(text)}\n\n"
                                       f'<a href="{html.escape(url, quote=True)}">Открыть сейф</a>'))
        except Exception as e:
            print(f"[vault_notify] tg uid={uid} kind={ev.get('kind')} err={e!r}")
        try:
            ok = send_web_push(uid, {"title": title, "body": text, "url": url, "tag": f"vault-{ev['vault_id']}"}) > 0 or ok
        except Exception as e:
            print(f"[vault_notify] push uid={uid} kind={ev.get('kind')} err={e!r}")
        try:
            to = accounts.email_of(uid)
            if to and transactional_mail.configured():
                body = transactional_mail._p(html.escape(text)) + transactional_mail._button(url, "Открыть сейф")
                transactional_mail.send(
                    to, subject, f"{text}\n\nОткрыть: {url}",
                    transactional_mail._layout(preheader=text[:120], title=title, body_html=body),
                )
                ok = True
        except Exception as e:
            print(f"[vault_notify] mail uid={uid} kind={ev.get('kind')} err={e!r}")
        reached += 1 if ok else 0
    print(f"[vault_notify] kind={ev.get('kind')} vault={ev.get('vault_id')} to={len(ev.get('to') or [])} reached={reached}")
    return reached
