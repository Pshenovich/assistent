"""Админка → «Модели»: какие модели ИИ видят пользователи и какие уровни усилия у каждой."""

from __future__ import annotations

from typing import Any

from assistant.admin.views import Ctx, esc, page, table

PAGE_HINT = (
    "Эти модели пользователи видят в переключателе Лео (приложение, документы, Telegram) и в PAIE. "
    "«Авто» всегда первым и сам выбирает модель. Расход списывается по фактической цене OpenRouter — "
    "дорогие модели и высокие уровни быстрее тратят кредиты."
)
EFFORT_HINT = (
    "Уровень усилия — сколько модель думает перед ответом: выше — точнее на трудных задачах, но дольше и дороже. "
    "Пользователь выбирает из отмеченных; по умолчанию — выделенный. Руководитель организации может ограничить "
    "сотрудникам потолок."
)


def _price(v: float | None) -> str:
    if v is None:
        return "—"
    if v == 0:
        return "0"
    return (f"{v:.2f}" if v >= 0.1 else f"{v:.3f}").rstrip("0").rstrip(".").replace(".", ",")


def _price_cell(c: dict[str, Any] | None) -> str:
    if not c:
        return '<span class="muted small">нет в каталоге</span>'
    return f'${_price(c.get("input_usd_m"))} / ${_price(c.get("output_usd_m"))}'


def _supported(c: dict[str, Any] | None, efforts: list[str], all_levels: tuple[str, ...]) -> list[str]:
    """Уровни, которые можно отметить: что принимает модель по каталогу, плюс уже отмеченные."""
    if c is None:
        sup = list(all_levels)
    elif c.get("efforts") is None:
        sup = []
    else:
        sup = list(c["efforts"]) or list(all_levels)
    return [e for e in all_levels if e in sup or e in efforts]


def _efforts_form(m: dict[str, Any], c: dict[str, Any] | None, *, labels: dict[str, str],
                  all_levels: tuple[str, ...], can_write: bool) -> str:
    levels = _supported(c, m["efforts"], all_levels)
    if not levels:
        return '<span class="muted small">не рассуждает</span>'
    dis = "" if can_write else " disabled"
    boxes = "".join(
        f'<label class="small" style="white-space:nowrap;margin-right:8px">'
        f'<input type="checkbox" name="efforts" value="{e}"{" checked" if e in m["efforts"] else ""}{dis}> {esc(labels[e])}</label>'
        for e in levels
    )
    if not m["efforts"]:
        return (f'<div>{boxes}</div><div class="small muted" style="margin-top:4px">Уровни не отмечены — '
                'пользователь не выбирает, модель думает как задано у провайдера</div>')
    opts = "".join(
        f'<option value="{e}"{" selected" if e == m["default_effort"] else ""}>{esc(labels[e])}</option>'
        for e in levels if e in m["efforts"]
    )
    return (f'<div>{boxes}</div><div class="small muted" style="margin-top:4px">по умолчанию '
            f'<select name="default_effort"{dis}>{opts}</select></div>')


def models_page(ctx: Ctx, models: list[dict[str, Any]], catalog: list[dict[str, Any]], *, q: str,
                labels: dict[str, str], all_levels: tuple[str, ...], **flash: str) -> str:
    by_id = {c["id"]: c for c in catalog}
    base = ctx.prefix + "/models"
    rows = []
    for i, m in enumerate(models):
        c = by_id.get(m["id"])
        state = ("" if m["enabled"] else ' <span class="badge b-none">скрыта</span>')
        dis = "" if ctx.can_write else " disabled"
        form_id = f"m{i}"
        save = (f'<form id="{form_id}" method="post" action="{esc(base + "/save")}">'
                f'<input type="hidden" name="id" value="{esc(m["id"])}">'
                f'<input name="label" value="{esc(m["label"])}" maxlength="60" style="width:100%"{dis}>'
                f'<div class="small muted" style="margin-top:4px">{esc(m["id"])}{state}</div>'
                f'<div style="margin-top:8px">{_efforts_form(m, c, labels=labels, all_levels=all_levels, can_write=ctx.can_write)}</div>'
                + (f'<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">'
                   f'<label class="small"><input type="checkbox" name="enabled" value="1"{" checked" if m["enabled"] else ""}> показывать</label>'
                   '<button class="pri">Сохранить</button></div>' if ctx.can_write else "")
                + "</form>")
        acts = ""
        if ctx.can_write:
            def btn(action: str, label: str, cls: str = "", extra: str = "") -> str:
                return (f'<form class="inl" method="post" action="{esc(base + "/" + action)}">'
                        f'<input type="hidden" name="id" value="{esc(m["id"])}">{extra}'
                        f'<button{(" class=" + chr(34) + cls + chr(34)) if cls else ""}>{label}</button></form>')

            acts = (btn("move", "↑", extra='<input type="hidden" name="delta" value="-1">')
                    + btn("move", "↓", extra='<input type="hidden" name="delta" value="1">')
                    + btn("remove", "Убрать", "bad"))
        rows.append(f"<tr><td style=\"min-width:320px\">{save}</td><td class=\"n\">{_price_cell(c)}</td>"
                    f"<td style=\"white-space:nowrap\">{acts}</td></tr>")

    listed = {m["id"] for m in models}
    found_rows = []
    if q:
        ql = q.lower()
        hits = [c for c in catalog if ql in c["id"].lower() or ql in c["name"].lower()]
        for c in hits[:40]:
            if c["efforts"] is None:
                think = '<span class="muted small">не рассуждает</span>'
            elif c["efforts"]:
                think = esc(", ".join(labels[e] for e in c["efforts"]))
            else:
                think = "любой уровень"
            add = ""
            if c["id"] in listed:
                add = '<span class="muted small">уже в списке</span>'
            elif ctx.can_write:
                add = (f'<form class="inl" method="post" action="{esc(base + "/add")}">'
                       f'<input type="hidden" name="id" value="{esc(c["id"])}"><button class="pri">Добавить</button></form>')
            ctx_k = f'{c["context"] // 1000:,} тыс.'.replace(",", " ") if c["context"] else "—"
            found_rows.append(
                f"<tr><td>{esc(c['name'])}<div class=\"small muted\">{esc(c['id'])}</div></td>"
                f"<td class=\"n\">{_price_cell(c)}</td><td class=\"n\">{ctx_k}</td><td class=\"small\">{think}</td><td>{add}</td></tr>"
            )
    search = (f'<form class="tools" method="get" action="{esc(base)}">'
              f'<input type="search" name="q" value="{esc(q)}" placeholder="claude, gemini, deepseek, grok, qwen…" style="min-width:300px">'
              + (f'<input type="hidden" name="access_token" value="{esc(ctx.token)}">' if ctx.token else "")
              + '<button>Найти в каталоге</button></form>')
    found = ""
    if q:
        found = table((("Модель", ""), ("$ за 1 млн: вход / выход", "n"), ("Контекст", "n"), ("Уровни усилия", ""), ("", "")),
                      found_rows, "В каталоге ничего не нашлось" if catalog else "Каталог OpenRouter сейчас недоступен")
    body = (
        f'<h1>Модели</h1><p class="sub">{esc(PAGE_HINT)}</p>'
        f'<p class="small muted">{esc(EFFORT_HINT)}</p>'
        + table((("Модель и уровни усилия", ""), ("$ за 1 млн: вход / выход", "n"), ("", "")), rows, "Список пуст — пользователи увидят только «Авто»")
        + f'<h2>Добавить из каталога OpenRouter · {len(catalog)} моделей</h2>{search}{found}'
    )
    return page(ctx, active="models", title="Модели", body=body, **flash)
