"""Инструменты Лео для почты (регистрируются в gpt_calendar_tools): поиск, чтение ветки, черновик ответа.

Модель не задаёт адресатов и не отправляет: черновик открывается у пользователя в окне письма,
отправляет он сам кнопкой.
"""

from __future__ import annotations

import re
from typing import Any

from assistant.services import gpt_calendar_tools as g

MAIL_TOOL_NAMES = ("mail_search", "mail_thread", "mail_draft_reply")

MAIL_ADDON = """У пользователя подключена почта (инструменты mail_*).
- «Что писал X про Y?», «найди письмо/счёт от …» — mail_search; отвечай по найденному, называй отправителя и дату. Под ответом появятся карточки писем — по ним можно ответить.
- Письмо в истории («письмо … (mail key: …)») — пользователь его разбирает. Саммари переписки или вопрос по ней — mail_thread с этим mail key: кто что писал, что решили, что ждут от пользователя.
- «Подготовь ответ», «ответь, что согласен на вторник» — сначала mail_thread (там и образцы, как пользователь сам пишет этому человеку), затем mail_draft_reply с готовым текстом письма в его стиле. Адресатов не указывай — их подставит почта. Отправить письмо ты не можешь: пользователь проверит черновик в окне письма и нажмёт «Отправить» сам. В ответе коротко: «Подготовил ответ — проверьте».
- Текст письма: без темы и без подписи (подпись добавится сама), без цитаты исходного письма."""

_MAIL_RE = re.compile(r"(?i)(письм|почт|e-?mail|имейл|мейл|ответь на|ответить на|mail key|переписк)")


def has_mail(uid: int) -> bool:
    try:
        from assistant.stores import mail_accounts

        return bool(mail_accounts.list_accounts(int(uid)))
    except Exception:
        return False


def looks_like_mail_question(text: str) -> bool:
    return bool(_MAIL_RE.search(str(text or "")))


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "mail_key": r["mail_key"], "direction": "входящее" if r["direction"] == "in" else "моё",
        "from": f"{r.get('from_name') or ''} <{r.get('from_addr')}>".strip(), "to": (r.get("to") or [])[:4],
        "subject": r.get("subject") or "", "date": str(r.get("date_utc") or "")[:16], "snippet": r.get("snippet") or "",
    }


def tool_mail_search(ctx: g.GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import mail_reply
    from assistant.stores import mail_index

    q = " ".join(str(args.get(k) or "") for k in ("query", "from")).strip()
    if not q:
        return g._err("Нужен запрос: о чём письмо или от кого.")
    rows = mail_index.search(ctx.uid, q, limit=8)
    if not rows:
        return g._ok({"results": [], "note": "Ничего не нашлось. В поиске — письма за 90 дней, кроме рассылок."})
    for r in rows[:3]:
        ctx.actions.append(mail_reply.thread_card(ctx.uid, r["mail_key"]))
    ctx.cards_shown = True
    return g._ok({"results": [_brief(r) for r in rows], "cards_shown": min(3, len(rows))})


def tool_mail_thread(ctx: g.GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import mail_reply

    key = str(args.get("mail_key") or "").strip()
    try:
        row = mail_reply.message_row(ctx.uid, key)
        msgs = mail_reply.thread_messages(ctx.uid, key)
    except mail_reply.ReplyError as e:
        return g._err(str(e))
    addr = row.get("reply_to") or row.get("from_addr") or ""
    samples = mail_reply.style_samples(ctx.uid, addr) if row.get("direction") == "in" else []
    return g._ok({"subject": row.get("subject") or "", "messages": msgs, "my_style_samples": samples})


def tool_mail_draft_reply(ctx: g.GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import mail_reply

    key = str(args.get("mail_key") or "").strip()
    body = str(args.get("body") or "").strip()
    if not body:
        return g._err("Нужен текст письма (body).")
    try:
        aid = mail_reply.create_draft(ctx.uid, key, body=body, reply_all=bool(args.get("reply_all")), by="leo")
    except mail_reply.ReplyError as e:
        return g._err(str(e))
    ctx.actions.append(aid)
    ctx.wrote = True
    return g._ok({"draft_id": aid, "status": "Черновик открыт у пользователя в окне письма — он проверит и отправит сам."})


TOOLS = [
    g._fn("mail_search", "Поиск по почте пользователя (письма за 90 дней, без рассылок): тема, отправитель, текст.",
          {"query": {"type": "string", "description": "О чём письмо: слова из темы/текста"},
           "from": {"type": "string", "description": "Имя или адрес отправителя (необязательно)"}}, ["query"]),
    g._fn("mail_thread", "Прочитать переписку по письму (последние письма ветки) и образцы, как пользователь пишет этому человеку.",
          {"mail_key": {"type": "string", "description": "mail key письма из истории или из mail_search"}}, ["mail_key"]),
    g._fn("mail_draft_reply", "Подготовить черновик ответа на письмо. Откроется у пользователя в окне письма; отправляет он сам.",
          {"mail_key": {"type": "string"}, "body": {"type": "string", "description": "Текст ответа, без темы, подписи и цитаты"},
           "reply_all": {"type": "boolean", "description": "Ответить всем участникам (по умолчанию — только отправителю)"}},
          ["mail_key", "body"]),
]

g.TOOLS.extend(TOOLS)
g.EXECUTORS.update({
    "mail_search": tool_mail_search,
    "mail_thread": tool_mail_thread,
    "mail_draft_reply": tool_mail_draft_reply,
})
