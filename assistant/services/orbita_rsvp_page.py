"""Публичная страница ответа на приглашение (/rsvp/<token>) для внешнего гостя встречи Орбиты.

Ответ — кнопками (POST), а не ссылками: почтовые антивирусы сами открывают ссылки из писем,
и ответ по ссылке ставился бы сам.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Optional

ANSWERS = {"accepted": "Приду", "tentative": "Возможно", "declined": "Не приду"}


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def answer(token: str, response: str) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    """Сохранить ответ гостя; организатору — сообщение бота. None — ссылка недействительна."""
    from assistant.stores import orbita_events

    found = orbita_events.get_by_token(token)
    if not found or response not in ANSWERS:
        return None
    ev, guest = found
    if ev["status"] == "cancelled":
        return ev, guest
    orbita_events.set_response(ev["id"], email=guest["email"], response=response)
    if guest.get("response") != response:
        _tell_organizer(ev, guest["email"], response)
    return orbita_events.get_by_token(token)


def _tell_organizer(ev: dict[str, Any], email: str, response: str) -> None:
    from assistant.bot import access_gate

    try:
        access_gate.notify_user_sync(int(ev["owner_user_id"]),
                                     f"{email} ответил на «{ev['title']}»: {ANSWERS.get(response, response).lower()}.")
    except Exception as e:
        print(f"[orbita_rsvp] notify organizer err={e!r}")


def _when(ev: dict[str, Any]) -> str:
    from assistant.services.calendar import format_event_when

    owner = int(ev["owner_user_id"])
    text = format_event_when(datetime.fromisoformat(ev["start_utc"]), datetime.fromisoformat(ev["end_utc"]), owner)
    tz = ev.get("tz") or ""
    return f"{text} ({tz})" if tz else text


def _organizer(ev: dict[str, Any]) -> str:
    from assistant.stores import note_members

    prof = note_members.get_profile(ev["owner_user_id"])
    name = note_members.profile_display_name(prof, fallback="") if prof else ""
    name = "" if name == "Участник" else name
    email = ev.get("organizer_email") or ""
    return f"{name} · {email}" if name and email else (name or email)


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#16181d;--muted:#6b7280;--line:#e6e8ec;--accent:#3b6ef5;--ok:#15803d;--bad:#b91c1c}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#e8eaee;--muted:#9aa1ad;--line:#262a33;--accent:#7aa2ff;--ok:#4ade80;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,system-ui,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:460px;margin:8vh auto;padding:0 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px}
h1{font-size:20px;margin:0 0 6px}.muted{color:var(--muted)}.row{margin:10px 0}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
button,.btn{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:10px;padding:10px 16px;cursor:pointer;text-decoration:none}
button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.note{margin-top:14px;padding:10px 12px;border-radius:10px;background:color-mix(in srgb,var(--ok) 12%,transparent);color:var(--ok)}
.bad{background:color-mix(in srgb,var(--bad) 12%,transparent);color:var(--bad)}
.foot{margin-top:16px;font-size:13px;color:var(--muted)}.foot a{color:var(--accent)}
"""


def render(token: str, *, done: str = "") -> tuple[str, int]:
    from assistant.stores import orbita_events

    found = orbita_events.get_by_token(token)
    if not found:
        body = ('<div class="card"><h1>Приглашение не найдено</h1>'
                '<p class="muted">Ссылка устарела или встреча удалена. Уточните у организатора.</p></div>')
        return _page("Приглашение не найдено", body), 404
    ev, guest = found
    rows = [f'<div class="row">{_esc(_when(ev))}</div>']
    if ev.get("location"):
        rows.append(f'<div class="row">Где: {_esc(ev["location"])}</div>')
    org = _organizer(ev)
    if org:
        rows.append(f'<div class="row muted">Организатор: {_esc(org)}</div>')
    if ev.get("description"):
        rows.append(f'<div class="row muted">{_esc(ev["description"])}</div>')
    if ev["status"] == "cancelled":
        body = (f'<div class="card"><h1>{_esc(ev["title"])}</h1>{"".join(rows)}'
                '<div class="note bad">Встреча отменена.</div></div>')
        return _page(ev["title"], body), 200
    cur = guest.get("response") or "needsAction"
    btns = "".join(
        f'<button name="r" value="{k}" class="{"on" if cur == k else ""}">{_esc(v)}</button>' for k, v in ANSWERS.items()
    )
    note = f'<div class="note">Ответ сохранён: {_esc(ANSWERS.get(cur, ""))}. Организатор увидит его.</div>' if done else ""
    body = (
        f'<div class="card"><h1>{_esc(ev["title"])}</h1>{"".join(rows)}'
        f'<p class="muted">Вас приглашают как {_esc(guest["email"])}. Придёте?</p>'
        f'<form method="post" class="btns">{btns}</form>{note}'
        f'<div class="foot"><a href="/rsvp/{_esc(token)}/invite.ics">Добавить в свой календарь (.ics)</a></div></div>'
    )
    return _page(ev["title"], body), 200


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"robots\" content=\"noindex\">"
            f"<title>{_esc(title)}</title><style>{CSS}</style></head><body><div class=\"wrap\">{body}</div></body></html>")


def ics_file(token: str) -> Optional[str]:
    from assistant.lib import ics
    from assistant.stores import orbita_events

    found = orbita_events.get_by_token(token)
    if not found:
        return None
    ev, guest = found
    method = "CANCEL" if ev["status"] == "cancelled" else "PUBLISH"
    return ics.build(ev, method=method, attendees=[guest])
