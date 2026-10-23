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
- «Что писал X про Y?», «что мы обсуждали с X про …», «найди письмо/счёт от …» — mail_search (who — с кем, query — о чём). Он ищет в памяти переписки за год по смыслу, а если не нашёл — прямо в ящике за всю историю. Нашёл ветку — дочитай её mail_thread (по thread_key или mail_key) и отвечай: что обсуждали, на чём остановились, даты, короткие цитаты. Называй тему письма и дату. Под ответом появятся карточки писем — по ним можно ответить.
- Письмо в истории («письмо … (mail key: …)») — пользователь его разбирает. Саммари переписки или вопрос по ней — mail_thread с этим mail key: кто что писал, что решили, что ждут от пользователя.
- «Подготовь ответ», «ответь, что согласен на вторник» — сначала mail_thread (там и образцы, как пользователь сам пишет этому человеку), затем mail_draft_reply с готовым текстом письма в его стиле. Адресатов не указывай — их подставит почта. Отправить письмо ты не можешь: пользователь проверит черновик в окне письма и нажмёт «Отправить» сам. В ответе коротко: «Подготовил ответ — проверьте».
- Текст письма: без темы и без подписи (подпись добавится сама), без цитаты исходного письма."""

_MAIL_RE = re.compile(r"(?i)(письм|почт|e-?mail|имейл|мейл|ответь на|ответить на|mail key|переписк|обсуждали|договаривал)")


def has_mail(uid: int) -> bool:
    try:
        from assistant.stores import mail_accounts

        return bool(mail_accounts.list_accounts(int(uid)))
    except Exception:
        return False


def looks_like_mail_question(text: str) -> bool:
    return bool(_MAIL_RE.search(str(text or "")))


def tool_mail_search(ctx: g.GptCalendarContext, args: dict[str, Any]) -> str:
    """Гибридный поиск: память переписки (по смыслу и по словам, за год) → письма за 90 дней → живой поиск в ящике."""
    from assistant.services import mail_memory, mail_reply
    from assistant.stores import mail_index

    query = str(args.get("query") or "").strip()
    who = str(args.get("who") or args.get("from") or "").strip()
    if not query and not who:
        return g._err("Нужен запрос: о чём письмо или с кем переписка.")
    addrs = mail_memory.people_for(ctx.uid, who) if who else []
    if who and not addrs:
        return g._ok({"results": [], "note": f"Не нашёл в почте и контактах адрес «{who}». Уточни имя или адрес."})
    q = query or who
    threads: dict[str, dict[str, Any]] = {}
    for r in mail_memory.search_semantic(ctx.uid, q, people=addrs) + mail_memory.search_text(ctx.uid, q, people=addrs):
        threads.setdefault(r["thread_key"], {**r, "source": "память переписки"})
    for r in mail_index.search(ctx.uid, " ".join([query, who]).strip(), limit=8):
        involved = {r.get("from_addr")} | set(r.get("to") or []) | set(r.get("cc") or [])
        if addrs and not involved & set(addrs):
            continue
        tk = r.get("thread_key") or r["mail_key"]
        threads.setdefault(tk, {"thread_key": tk, "subject": r.get("subject"), "date": str(r.get("date_utc"))[:10],
                                "from": r.get("from_name") or r.get("from_addr"), "snippet": r.get("snippet"),
                                "mail_key": r["mail_key"], "source": "почта за 90 дней"})
    results = list(threads.values())[:8]
    live: list[dict[str, Any]] = []
    if len(results) < 2 and addrs:
        live = mail_memory.live_search(ctx.uid, addrs, query)
    cards = 0
    for r in results:  # карточки — для писем, которые есть в индексе (по ним можно ответить)
        key = r.get("mail_key")
        if not key and r.get("thread_key"):
            th = mail_index.thread(ctx.uid, r["thread_key"])
            key = th[-1]["mail_key"] if th else ""
        if key and cards < 3:
            ctx.actions.append(mail_reply.thread_card(ctx.uid, key))
            cards += 1
    ctx.cards_shown = ctx.cards_shown or bool(cards)
    if not results and not live:
        return g._ok({"results": [], "people": addrs,
                      "note": "Ничего не нашлось ни в памяти переписки, ни в ящике. Уточни, о чём речь или с кем."})
    return g._ok({"people": addrs, "results": results, "found_in_mailbox": live, "cards_shown": cards,
                  "hint": "Полный текст ветки — mail_thread(thread_key=…)."})


def tool_mail_thread(ctx: g.GptCalendarContext, args: dict[str, Any]) -> str:
    from assistant.services import mail_memory, mail_reply
    from assistant.stores import mail_index

    key = str(args.get("mail_key") or "").strip()
    tk = str(args.get("thread_key") or "").strip()
    if not key and tk:
        th = mail_index.thread(ctx.uid, tk)
        if th:
            key = th[-1]["mail_key"]
        else:  # старая ветка — только в памяти переписки
            mem = mail_memory.thread_text(ctx.uid, tk)
            if not mem:
                return g._err("Ветка не найдена.")
            return g._ok({"subject": mem["subject"], "people": mem["people"], "first_date": mem["first_date"],
                          "last_date": mem["last_date"], "text": mem["text"]})
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
    g._fn("mail_search", "Поиск по переписке пользователя: память переписки с людьми за год (по смыслу), а если не нашлось — прямо в ящике за всю историю.",
          {"query": {"type": "string", "description": "О чём переписка: «новые условия сотрудничества», «счёт за октябрь»"},
           "who": {"type": "string", "description": "С кем переписка: имя или адрес (необязательно)"}}, ["query"]),
    g._fn("mail_thread", "Прочитать ветку переписки целиком (и образцы, как пользователь пишет этому человеку).",
          {"mail_key": {"type": "string", "description": "mail key письма из истории или из mail_search"},
           "thread_key": {"type": "string", "description": "thread_key из mail_search (если нет mail key)"}}),
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
