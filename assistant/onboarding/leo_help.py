"""Лео знает Орбиту: краткий указатель функций в системном промпте и инструмент app_help.

app_help отдаёт «где / как» из того же каталога, что и онбординг, и кладёт под ответ карточку
«Показать» (действие kind=guide): в приложении она открывает нужный экран и подсвечивает элемент,
в Telegram — ссылка на приложение с ?guide=<id>.
"""

from __future__ import annotations

import re
from typing import Any

from assistant import onboarding

TOOL_NAME = "app_help"
ACTION_KIND = "guide"


def system_addon() -> str:
    lines = [
        "Орбита — функции (id · название — где). На вопросы «где…», «как…», «что ты умеешь», «как пользоваться» "
        "вызови app_help с id функции (или с вопросом): он вернёт шаги, а под ответом появится кнопка «Показать», "
        "которая откроет нужный экран и подсветит кнопку. Отвечай коротко, названиями кнопок из интерфейса, "
        "не выдумывай функций, которых нет в списке. Не зови app_help, если вопрос не про функцию Орбиты "
        "(общий вопрос, совет, факт): кнопка «Показать» должна быть про то, о чём ответ.",
    ]
    for f in onboarding.features():
        lines.append(f"- {f['id']} · {f['title']} — {f['where']}")
    return "\n".join(lines)


# Общие слова интерфейса: «на телефоне ☰», «кнопка», «откройте»… есть в описании многих функций
# и не говорят, о чём вопрос («запись телефонных звонков» совпадала с «на телефоне» у вида календаря).
_STOP_STEMS = frozenset(
    """телеф компь кнопк нажми нажат откро откры выбер слева справ рядом сверх внизу вверх прило орбит
    показ здесь можно через после котор этого чтобы тольк будет нужно экран разде назва перей зайди
    добав сразу какой какие какая каким такой своим свои своих вашег ваших ваши своей""".split()
)


def _stems(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-zа-яё0-9]+", str(text or "").lower().replace("ё", "е")):
        if len(w) < 3:
            continue
        # Короткое слово — без окончания («тему» ~ «тема», «доску» ~ «доска»), длинное — первые 5 букв.
        st = w[:5] if len(w) > 5 else (w[:-1] if len(w) >= 4 else w)
        if w[:5] in _STOP_STEMS or w in ("где", "как", "что", "это", "все", "или", "для", "при", "под", "над", "тут", "там"):
            continue
        out.add(st)
    return out


def _feature_stems(f: dict[str, Any]) -> set[str]:
    return _stems(" ".join([f["id"].replace("_", " "), f["title"], f.get("summary", ""), f.get("where", "")] + list(f.get("ask_leo") or [])))


def _match(query: str) -> dict[str, Any] | None:
    """Функция по id или по словам вопроса. Одно случайное совпадение слова — не повод показывать
    карточку: лучше честное «не нашёл» и список, из которого модель выберет id сама."""
    q = str(query or "").strip().lower()
    if not q:
        return None
    exact = onboarding.get_feature(q)
    if exact:
        return exact
    bare = q.rstrip("?!. ")
    by_ask = [f for f in onboarding.features() if any(a.lower().rstrip("?!. ") == bare for a in f.get("ask_leo") or [])]
    if len(by_ask) == 1:
        return by_ask[0]
    words = _stems(q)
    feats = onboarding.features()
    fstems = [_feature_stems(f) for f in feats]
    # Редкое слово весит больше: «zoom» есть у одной функции, «задач» — у десятка.
    df: dict[str, int] = {}
    for st in fstems:
        for w in st & words:
            df[w] = df.get(w, 0) + 1
    scored: list[tuple[float, dict[str, Any]]] = []
    for f, st in zip(feats, fstems):
        s = sum(1.0 / df[w] for w in st & words)
        s += len(words & _stems(f["title"]))
        if f["title"].lower() in q:
            s += 3
        if s:
            scored.append((s, f))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    best_s, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_s >= 1.0 and best_s > runner_up * 1.15:
        return best
    return None


def _quoted(text: str) -> list[str]:
    return [m.strip().lower() for m in re.findall(r"«([^»]{2,40})»", str(text or "")) if m.strip()]


def guide_fits_answer(f: dict[str, Any], answer: str) -> bool:
    """Карточка «Показать» — только про то, о чём ответ: название функции, кнопка из пути
    («Zoom», «Транскрипции») или хотя бы два значимых слова из её описания."""
    low = str(answer or "").lower().replace("ё", "е")
    if not low.strip():
        return True
    title = f["title"].lower().replace("ё", "е")
    if title in low:
        return True
    if any(q.replace("ё", "е") in low for q in _quoted(f.get("where", "")) if q not in ("создать", "+")):
        return True
    return len(_stems(low) & _feature_stems(f)) >= 2


def drop_unrelated_guides(ctx: Any, answer: str) -> None:
    """Убрать из-под ответа карточки «Показать», которые не про ответ (модель позвала app_help,
    а ответила про другое)."""
    from assistant.services import gpt_calendar_tools as g

    keep: list[str] = []
    for aid in list(ctx.actions or []):
        a = g.get_action(aid)
        if a and a.get("kind") == ACTION_KIND:
            f = onboarding.get_feature(str((a.get("payload") or {}).get("feature") or ""))
            if not f or not guide_fits_answer(f, answer):
                continue
        keep.append(aid)
    ctx.actions[:] = keep


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
