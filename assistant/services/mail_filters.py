"""Воронка почты: код отсекает рассылки и роботов до LLM (принцип «LLM предлагает, код проверяет»)."""

from __future__ import annotations

import re
from email.message import Message
from typing import Any

MAX_RECIPIENTS = 5

_BULK_HEADERS = ("List-Unsubscribe", "List-Id", "List-Post", "Feedback-ID", "X-Campaign", "X-CampaignID",
                 "X-Mailgun-Tag", "X-Mailgun-Campaign-Id", "X-SES-Outgoing", "X-SES-CONFIGURATION-SET",
                 "X-MC-User", "X-Mailchimp-Campaign", "X-Sendpulse-Id", "X-UniSender-Id", "X-Unisender-Campaign")
_ROBOT_LOCAL = re.compile(
    r"^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?|notify|alerts?|mailer[-_.]?daemon|postmaster|bounces?"
    r"|news(?:letter)?|digest|marketing|promo|info|support|billing|robot|auto|system|daemon|calendar-notification)"
    r"(?:[-_.+].*)?$",
    re.I,
)
_ROBOT_DOMAIN = re.compile(r"(?:^|\.)(?:bounce|bounces|mail\.notifications|email|e-mail|news|mailer|notify|sendgrid|mailgun|"
                           r"amazonses|sparkpost|mcsv|mcdlv|rsgsv|sendpulse|unisender)\.", re.I)


def is_bulk(msg: Message) -> bool:
    """Рассылка, уведомление или автоответ — по служебным заголовкам."""
    for h in _BULK_HEADERS:
        if msg.get(h) is not None:
            return True
    prec = str(msg.get("Precedence") or "").strip().lower()
    if prec in ("bulk", "list", "junk", "auto_reply"):
        return True
    auto = str(msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond") or str(msg.get("X-Auto-Response-Suppress") or "").lower() in ("all", "oof"):
        return True
    ctype = str(msg.get_content_type() or "").lower()
    if ctype in ("multipart/report", "message/delivery-status"):
        return True
    return False


# Приглашения и ответы из календаря: встречи и так видны в «Актуальном», в почте — шум.
CALENDAR_SUBJECT = re.compile(
    r"(?i)^\s*(приглашение|обновл[её]нное приглашение|обновлено приглашение|приглашение обновлено|принято|отклонено|"
    r"под вопросом|предварительно принято|мероприятие отменено|отменено мероприятие|встреча отменена|"
    r"invitation|updated invitation|invitation updated|accepted|declined|tentatively accepted|tentative|"
    r"canceled event|cancelled event|event canceled|event cancelled)\b[^:]{0,40}:"
)


def is_calendar(msg: Message, subject: str = "") -> bool:
    """Приглашение / ответ на приглашение / отмена встречи — по теме и вложенному text/calendar (.ics)."""
    if not subject:
        from assistant.integrations import mail_imap

        subject = mail_imap.decode_hdr(msg.get("Subject"))
    if CALENDAR_SUBJECT.match(subject or ""):
        return True
    for part in msg.walk():
        ctype = (part.get_content_type() or "").lower()
        if ctype in ("text/calendar", "application/ics"):
            return True
        if str(part.get_filename() or "").lower().endswith(".ics"):
            return True
    return False


def is_robot(addr: str) -> bool:
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return True
    local, domain = addr.rsplit("@", 1)
    return bool(_ROBOT_LOCAL.match(local) or _ROBOT_DOMAIN.search(domain + "."))


def domain_of(addr: str) -> str:
    return (addr or "").rsplit("@", 1)[-1].strip().lower()


def personal_to_me(to: list[str], cc: list[str], mine: set[str]) -> bool:
    """Пользователь в «Кому» (не в копии), получателей немного."""
    if not (set(to) & mine):
        return False
    return len(set(to) | set(cc)) <= MAX_RECIPIENTS


def known_sender(addr: str, *, correspondents: set[str], meeting_people: set[str], thread_mine: bool) -> bool:
    """Главный фильтр: пользователь сам писал этому адресу, встречался с ним или это ветка, начатая им."""
    a = (addr or "").strip().lower()
    return a in correspondents or a in meeting_people or thread_mine


def muted(addr: str, muted_senders: set[str], muted_domains: set[str]) -> bool:
    a = (addr or "").strip().lower()
    return a in muted_senders or domain_of(a) in muted_domains


def focus_candidate(row: dict[str, Any], *, mine: set[str], correspondents: set[str], meeting_people: set[str],
                    muted_senders: set[str], muted_domains: set[str]) -> bool:
    """Входящее письмо стоит показать ИИ для «Ждут ответа»/«Обещаний»: прошло все проверки кодом."""
    if row.get("direction") != "in" or row.get("bulk"):
        return False
    sender = str(row.get("from_addr") or "")
    if is_robot(sender) or sender in mine or muted(sender, muted_senders, muted_domains):
        return False
    if not personal_to_me(list(row.get("to") or []), list(row.get("cc") or []), mine):
        return False
    return known_sender(sender, correspondents=correspondents, meeting_people=meeting_people,
                        thread_mine=bool(row.get("thread_mine")))
