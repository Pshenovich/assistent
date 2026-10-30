"""Лео знает Орбиту: краткий указатель функций в системном промпте и инструмент app_help.

app_help отдаёт «где / как» из того же каталога, что и онбординг, и кладёт под ответ карточку
«Показать» (действие kind=guide): в приложении она открывает нужный экран и подсвечивает элемент,
в Telegram — ссылка на приложение с ?guide=<id>.
"""

from __future__ import annotations

from typing import Any

from assistant import onboarding

TOOL_NAME = "app_help"
ACTION_KIND = "guide"


def system_addon() -> str:
    lines = [
        "Орбита — функции (id · название — где). На вопросы «где…», «как…», «что ты умеешь», «как пользоваться» "
        "вызови app_help с id функции (или с вопросом): он вернёт шаги, а под ответом появится кнопка «Показать», "
        "которая откроет нужный экран и подсветит кнопку. Отвечай коротко, названиями кнопок из интерфейса, "
        "не выдумывай функций, которых нет в списке.",
    ]
    for f in onboarding.features():
        lines.append(f"- {f['id']} · {f['title']} — {f['where']}")
    return "\n".join(lines)


def _match(query: str) -> dict[str, Any] | None:
    q = str(query or "").strip().lower()
    if not q:
        return None
    exact = onboarding.get_feature(q)
    if exact:
        return exact
    best, score = None, 0
    words = [w for w in q.replace("?", " ").replace(",", " ").split() if len(w) > 2]
    for f in onboarding.features():
        hay = " ".join([f["id"], f["title"], f.get("summary", ""), f.get("where", "")] + list(f.get("ask_leo") or [])).lower()
        s = sum(1 for w in words if w[:5] in hay)
        if f["title"].lower() in q:
            s += 3
        if s > score:
            best, score = f, s
    return best if score > 0 else None


def public_feature(f: dict[str, Any], used: bool) -> dict[str, Any]:
    return {
        "id": f["id"],
        "title": f["title"],
        "summary": f.get("summary", ""),
        "where": f.get("where", ""),
        "how": list(f.get("how") or []),
        "try_asking_leo": list(f.get("ask_leo") or []),
        "user_has_tried": used,
    }


def tool_app_help(ctx: Any, args: dict[str, Any]) -> str:
    from assistant.onboarding import state as ob_state
    from assistant.services import gpt_calendar_tools as g

    f = _match(str(args.get("feature") or "")) or _match(str(args.get("query") or ""))
    if not f:
        return g._ok({
            "found": False,
            "features": [{"id": x["id"], "title": x["title"], "where": x["where"]} for x in onboarding.features()],
            "hint": "Подходящей функции нет в списке — скажи честно, что такой возможности нет, не выдумывай.",
        })
    try:
        used = f["id"] in (ob_state.get_state(ctx.uid).get("used") or {})
    except Exception:
        used = False
    aid = g.record_action(
        ctx.uid,
        ACTION_KIND,
        "done",
        title=f["title"],
        payload={"feature": f["id"], "detail": f.get("where", "")},
    )
    ctx.actions.append(aid)
    return g._ok({
        "found": True,
        **public_feature(f, used),
        "note": "Под ответом уже есть кнопка «Показать» — упомяни её одной фразой, не пересказывай путь дважды.",
    })


def guide_open_ref(payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "guide", "id": str(payload.get("feature") or "")}


def guide_link(feature_id: str) -> str:
    from urllib.parse import quote

    from assistant.lib.webapp_public import webapp_entry_url

    return f"{webapp_entry_url()}&guide={quote(feature_id)}"


TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Справка по Орбите: где находится функция и как ей пользоваться. Под ответом появится кнопка "
            "«Показать» — приложение откроет нужный экран и подсветит кнопку."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "feature": {"type": "string", "description": "id функции из списка «Орбита — функции» (board, create_task, …)"},
                "query": {"type": "string", "description": "Вопрос своими словами, если id не очевиден"},
            },
            "required": [],
        },
    },
}


def register(g: Any) -> None:
    if not any(t["function"]["name"] == TOOL_NAME for t in g.TOOLS):
        g.TOOLS.append(TOOL)
    g.EXECUTORS[TOOL_NAME] = tool_app_help
