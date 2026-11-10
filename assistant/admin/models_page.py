"""Админка → «Модели»: какие модели ИИ видят пользователи и какие уровни усилия у каждой.

Серверный HTML без JS, как вся админка: настройка модели раскрывается в <details>, уровни — кнопки-флажки,
уровень по умолчанию — переключатель. Стили страницы — здесь же, на токенах views.CSS (светлая/тёмная).
"""

from __future__ import annotations

from typing import Any

from assistant.admin.views import Ctx, esc, page

# Быстрый поиск по провайдерам: подпись → что искать в каталоге.
PROVIDERS = (
    ("Anthropic", "anthropic/"),
    ("OpenAI", "openai/"),
    ("Google", "google/"),
    ("DeepSeek", "deepseek/"),
    ("xAI", "x-ai/"),
    ("Qwen", "qwen/"),
    ("Mistral", "mistralai/"),
    ("Moonshot", "moonshotai/"),
)
VENDOR_LABEL = {v.rstrip("/"): k for k, v in PROVIDERS} | {"meta-llama": "Meta", "z-ai": "Z.ai", "minimax": "MiniMax"}
EFFORT_SUB = {
    "none": "не думает",
    "minimal": "почти не думает",
    "low": "быстрый ответ",
    "medium": "баланс",
    "high": "трудные задачи",
    "xhigh": "сложный анализ",
    "max": "без ограничений",
}

CSS = """
.mp-hint{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 22px}
.mp-hint div{flex:1 1 260px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;color:var(--muted);font-size:13px}
.mp-hint b{display:block;color:var(--text);font-weight:600;margin-bottom:2px}
.mp-sec{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:26px 0 10px}
.mp-sec h2{margin:0}.mp-sec span{color:var(--muted);font-size:13px}
.mp-list{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.mp-item{border-top:1px solid var(--line)}.mp-item:first-child{border-top:0}
.mp-item.off .mp-name,.mp-item.off .mp-meta{opacity:.55}
.mp-row{display:flex;align-items:center;gap:14px;padding:12px 16px}
.mp-ord{display:flex;flex-direction:column;gap:2px}
.mp-ord button{width:24px;height:20px;padding:0;border:0;background:transparent;color:var(--faint);border-radius:6px;font-size:11px;line-height:1}
.mp-ord button:hover{background:var(--bg);color:var(--text)}.mp-ord form{margin:0}.mp-ord button.ghost{visibility:hidden}
.mp-main{flex:1;min-width:0}
.mp-name{font-weight:600;font-size:14.5px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mp-meta{color:var(--muted);font-size:12px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mp-lv{display:flex;gap:4px;flex-wrap:wrap;margin-top:7px}
.mp-lv span{font-size:12px;padding:2px 9px;border-radius:999px;background:var(--bg);color:var(--muted)}
.mp-lv span.def{background:var(--accent-soft);color:var(--accent);font-weight:600}
.mp-lv span.none{background:transparent;padding-left:0}
.mp-price{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;font-size:13px}
.mp-price small{display:block;color:var(--faint);font-size:11px}
.mp-tag{font-size:11px;font-weight:500;padding:1px 8px;border-radius:999px;background:var(--bg);color:var(--muted)}
.mp-tag.off{background:var(--warn-soft);color:var(--warn)}.mp-tag.think{background:var(--accent-soft);color:var(--accent)}
.mp-tag.gone{background:var(--bad-soft);color:var(--bad)}
details.mp-item>summary{list-style:none;cursor:pointer}
details.mp-item>summary::-webkit-details-marker{display:none}
details.mp-item>summary:hover{background:color-mix(in srgb,var(--accent) 4%,transparent)}
.mp-edit-btn{font-size:13px;color:var(--accent);white-space:nowrap;padding:6px 10px;border-radius:8px}
details[open] .mp-edit-btn{background:var(--accent-soft)}
details[open] .mp-edit-btn::after{content:" ▴"}details:not([open]) .mp-edit-btn::after{content:" ▾"}
.mp-form{padding:4px 16px 18px 54px;display:grid;gap:16px;background:color-mix(in srgb,var(--bg) 55%,var(--panel))}
.mp-form .lbl{font-size:12px;color:var(--muted);margin-bottom:6px;display:block}
.mp-form input[type=text]{width:min(360px,100%)}
.mp-pills{display:flex;gap:6px;flex-wrap:wrap}
.mp-pill{position:relative}
.mp-pill input{position:absolute;opacity:0;pointer-events:none}
.mp-pill span{display:inline-flex;flex-direction:column;padding:7px 12px;border-radius:10px;border:1px solid var(--line);background:var(--panel);cursor:pointer;font-size:13px;line-height:1.25;min-width:96px}
.mp-pill span small{color:var(--faint);font-size:11px}
.mp-pill input:checked+span{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.mp-pill input:checked+span small{color:var(--accent);opacity:.8}
.mp-pill input:focus-visible+span{outline:2px solid var(--accent);outline-offset:2px}
.mp-pill input:disabled+span{cursor:default;opacity:.6}
.mp-radio span{min-width:0;padding:6px 11px;border-radius:999px}
.mp-switch{display:inline-flex;align-items:center;gap:10px;cursor:pointer;font-size:13px}
.mp-switch input{position:absolute;opacity:0}
.mp-switch i{width:34px;height:20px;border-radius:999px;background:var(--line);position:relative;transition:background .15s;flex:none}
.mp-switch i::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .15s;box-shadow:0 1px 2px rgba(0,0,0,.25)}
.mp-switch input:checked+i{background:var(--ok)}.mp-switch input:checked+i::after{left:16px}
.mp-switch input:focus-visible+i{outline:2px solid var(--accent);outline-offset:2px}
.mp-actions{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.mp-note{font-size:12px;color:var(--muted);margin:6px 0 0}
.mp-search{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 10px}
.mp-search input{flex:1 1 300px;padding:10px 14px;font-size:14px;border-radius:10px}
.mp-search button{padding:10px 16px;border-radius:10px}
.mp-prov{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}
.mp-prov a{padding:4px 12px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--text);font-size:13px}
.mp-prov a.on{background:var(--accent);border-color:var(--accent);color:#fff}.mp-prov a:hover{text-decoration:none;border-color:var(--accent)}
.mp-add{margin:0}.mp-add button{white-space:nowrap}
.mp-empty{padding:28px 16px;text-align:center;color:var(--muted)}
.mp-form:not(:has(input[name=efforts]:checked)) .mp-def{display:none}
.mp-form:has(input[name=efforts]:checked) .mp-none-note{display:none}
""" + "".join(
    f'.mp-form:has(input[name=efforts][value={e}]:not(:checked)) .mp-radio[data-e={e}]{{display:none}}'
    for e in ("none", "minimal", "low", "medium", "high", "xhigh", "max")
) + """
@media (max-width:720px){.mp-row{flex-wrap:wrap;gap:10px}.mp-price{text-align:left}.mp-form{padding:4px 14px 16px}
.mp-main{flex-basis:calc(100% - 50px)}.mp-ord{flex-direction:row}.mp-meta{white-space:normal;word-break:break-word}}
"""


def _num(v: float) -> str:
    if v == 0:
        return "0"
    s = f"{v:.2f}" if v >= 0.1 else f"{v:.3f}"
    return s.rstrip("0").rstrip(".").replace(".", ",")


def _price(c: dict[str, Any] | None) -> str:
    if not c or c.get("input_usd_m") is None:
        return '<div class="mp-price"><span class="muted">—</span><small>нет цены</small></div>'
    return (f'<div class="mp-price" title="Доллары за 1 млн токенов: вход (вопрос) / выход (ответ)">'
            f'${_num(c["input_usd_m"])} / ${_num(c.get("output_usd_m") or 0)}<small>за 1 млн токенов</small></div>')


def _vendor(mid: str) -> str:
    v = mid.split("/", 1)[0]
    return VENDOR_LABEL.get(v, v)


def _ctx_label(n: int) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{_num(n / 1_000_000)} млн токенов контекста"
    return f"{n // 1000} тыс. токенов контекста"


def _supported(c: dict[str, Any] | None, efforts: list[str], all_levels: tuple[str, ...]) -> list[str]:
    """Уровни, которые можно отметить: что принимает модель по каталогу, плюс уже отмеченные."""
    if c is None:
        sup = list(all_levels)
    elif c.get("efforts") is None:
        sup = []
    else:
        sup = list(c["efforts"]) or list(all_levels)
    return [e for e in all_levels if e in sup or e in efforts]


def _levels_summary(m: dict[str, Any], labels: dict[str, str]) -> str:
    if not m["efforts"]:
        return '<div class="mp-lv"><span class="none">Усилие не выбирается</span></div>'
    chips = "".join(
        f'<span class="{"def" if e == m["default_effort"] else ""}"'
        f'{" title=" + chr(34) + "по умолчанию" + chr(34) if e == m["default_effort"] else ""}>{esc(labels[e])}</span>'
        for e in m["efforts"]
    )
    return f'<div class="mp-lv">{chips}</div>'


def _hidden_token(ctx: Ctx) -> str:
    return f'<input type="hidden" name="access_token" value="{esc(ctx.token)}">' if ctx.token else ""


def _model_item(ctx: Ctx, m: dict[str, Any], c: dict[str, Any] | None, *, labels: dict[str, str],
                all_levels: tuple[str, ...], first: bool, last: bool) -> str:
    base = ctx.prefix + "/models"
    mid = esc(m["id"])
    tags = ""
    if not m["enabled"]:
        tags += '<span class="mp-tag off">скрыта</span>'
    if c is None:
        tags += '<span class="mp-tag gone" title="OpenRouter больше не отдаёт эту модель или каталог недоступен">нет в каталоге</span>'
    meta = " · ".join(x for x in (_vendor(m["id"]), m["id"]) if x)

    order = ""
    if ctx.can_write:
        def mv(delta: int, sym: str, dis: bool, title: str) -> str:
            return (f'<form method="post" action="{esc(base + "/move")}"><input type="hidden" name="id" value="{mid}">'
                    f'<input type="hidden" name="delta" value="{delta}"><button title="{title}"'
                    f'{" disabled class=" + chr(34) + "ghost" + chr(34) if dis else ""}>{sym}</button></form>')

        order = f'<div class="mp-ord">{mv(-1, "▲", first, "Выше")}{mv(1, "▼", last, "Ниже")}</div>'

    head = (f'<div class="mp-row">{order}<div class="mp-main"><div class="mp-name">{esc(m["label"])}{tags}</div>'
            f'<div class="mp-meta">{esc(meta)}</div>{_levels_summary(m, labels)}</div>{_price(c)}'
            + ('<span class="mp-edit-btn">Настроить</span>' if ctx.can_write else "") + "</div>")
    if not ctx.can_write:
        return f'<div class="mp-item{"" if m["enabled"] else " off"}">{head}</div>'

    levels = _supported(c, m["efforts"], all_levels)
    fid = "f-" + "".join(ch if ch.isalnum() else "-" for ch in m["id"])
    if levels:
        boxes = "".join(
            f'<label class="mp-pill"><input type="checkbox" name="efforts" value="{e}"'
            f'{" checked" if e in m["efforts"] else ""}><span>{esc(labels[e])}<small>{esc(EFFORT_SUB.get(e, ""))}</small></span></label>'
            for e in levels
        )
        default = m["default_effort"] or ("medium" if "medium" in levels else levels[0])
        radios = "".join(
            f'<label class="mp-pill mp-radio" data-e="{e}"><input type="radio" name="default_effort" value="{e}"'
            f'{" checked" if e == default else ""}><span>{esc(labels[e])}</span></label>'
            for e in levels
        )
        effort_html = (
            '<div><span class="lbl">Какие уровни усилия может выбрать пользователь</span>'
            f'<div class="mp-pills">{boxes}</div>'
            '<p class="mp-note mp-none-note">Ничего не отмечено — пользователь уровень не выбирает, модель думает как задано у провайдера.</p></div>'
            '<div class="mp-def"><span class="lbl">Уровень по умолчанию — с ним Лео отвечает, пока человек не выбрал свой</span>'
            f'<div class="mp-pills">{radios}</div></div>'
        )
    else:
        effort_html = ('<div><span class="lbl">Уровень усилия</span>'
                       '<p class="mp-note" style="margin:0">Эта модель не рассуждает перед ответом — выбирать нечего.</p></div>')
    form = (
        f'<form id="{fid}" class="mp-form" method="post" action="{esc(base + "/save")}">'
        f'<input type="hidden" name="id" value="{mid}">'
        f'<div><label class="lbl" for="{fid}-n">Название для пользователей</label>'
        f'<input type="text" id="{fid}-n" name="label" value="{esc(m["label"])}" maxlength="60"></div>'
        f'{effort_html}'
        '<div class="mp-actions">'
        f'<label class="mp-switch"><input type="checkbox" name="enabled" value="1"{" checked" if m["enabled"] else ""}><i></i>'
        'Показывать пользователям</label>'
        '<span style="display:flex;gap:8px;flex-wrap:wrap">'
        f'<button class="bad" formaction="{esc(base + "/remove")}" '
        'title="У тех, кто её выбрал, Лео ответит моделью по умолчанию. Вернуть можно через «Добавить модель».">'
        'Убрать из списка</button>'
        '<button class="pri">Сохранить</button></span></div>'
        "</form>"
    )
    return f'<details class="mp-item{"" if m["enabled"] else " off"}"><summary>{head}</summary>{form}</details>'


def _catalog_row(ctx: Ctx, c: dict[str, Any], *, listed: bool, labels: dict[str, str]) -> str:
    tags = ""
    if c["efforts"] is not None:
        tags += '<span class="mp-tag think" title="Умеет думать перед ответом — можно выбрать уровень усилия">думает</span>'
    meta = " · ".join(x for x in (_vendor(c["id"]), c["id"], _ctx_label(c.get("context") or 0)) if x)
    if listed:
        act = '<span class="mp-tag">уже в списке</span>'
    elif ctx.can_write:
        act = (f'<form class="mp-add" method="post" action="{esc(ctx.prefix + "/models/add")}">'
               f'<input type="hidden" name="id" value="{esc(c["id"])}"><button class="pri">Добавить</button></form>')
    else:
        act = ""
    if c["efforts"]:
        ef = c["efforts"]
        rng = labels[ef[0]] if len(ef) == 1 else f"от «{labels[ef[0]]}» до «{labels[ef[-1]]}»"
        meta2 = f"Уровни усилия: {rng}"
    elif c["efforts"] == []:
        meta2 = "Уровни усилия: любые"
    else:
        meta2 = ""
    lv = f'<div class="mp-meta">{esc(meta2)}</div>' if meta2 else ""
    return (f'<div class="mp-item"><div class="mp-row"><div class="mp-main"><div class="mp-name">{esc(c["name"])}{tags}</div>'
            f'<div class="mp-meta">{esc(meta)}</div>{lv}</div>{_price(c)}{act}</div></div>')


def models_page(ctx: Ctx, models: list[dict[str, Any]], catalog: list[dict[str, Any]], *, q: str,
                labels: dict[str, str], all_levels: tuple[str, ...], **flash: str) -> str:
    by_id = {c["id"]: c for c in catalog}
    shown = sum(1 for m in models if m["enabled"])
    items = "".join(
        _model_item(ctx, m, by_id.get(m["id"]) if catalog else {"efforts": m["efforts"] or None},
                    labels=labels, all_levels=all_levels, first=i == 0, last=i == len(models) - 1)
        for i, m in enumerate(models)
    ) or '<div class="mp-empty">Список пуст — пользователи увидят только «Авто».</div>'

    listed = {m["id"] for m in models}
    prov = "".join(
        f'<a href="{esc(ctx.url("/models", q=needle))}#add" class="{"on" if q == needle else ""}">{esc(name)}</a>'
        for name, needle in PROVIDERS
    )
    search = (f'<form class="mp-search" method="get" action="{esc(ctx.prefix + "/models")}#add">'
              f'<input type="search" name="q" value="{esc(q)}" placeholder="Название модели: sonnet, gemini 3, deepseek, grok…" autocomplete="off">'
              f'{_hidden_token(ctx)}<button class="pri">Найти</button></form>')
    found = ""
    if q:
        ql = q.lower()
        hits = [c for c in catalog if ql in c["id"].lower() or ql in c["name"].lower()]
        hits.sort(key=lambda c: c["id"] in listed)  # ещё не добавленные — выше
        rows = "".join(_catalog_row(ctx, c, listed=c["id"] in listed, labels=labels) for c in hits[:40])
        if not catalog:
            rows = '<div class="mp-empty">Каталог OpenRouter сейчас недоступен — попробуйте позже.</div>'
        elif not rows:
            rows = f'<div class="mp-empty">По запросу «{esc(q)}» ничего не нашлось.</div>'
        more = f'<p class="mp-note">Показаны первые 40 из {len(hits)} — уточните запрос.</p>' if len(hits) > 40 else ""
        clear = f'<a href="{esc(ctx.url("/models"))}">Очистить</a>'
        found = (f'<div class="mp-sec"><h2>Найдено · {len(hits)}</h2><span>{clear}</span></div>'
                 f'<div class="mp-list">{rows}</div>{more}')

    body = (
        f"<style>{CSS}</style>"
        '<h1>Модели</h1><p class="sub">Какие модели ИИ пользователи видят в Лео (приложение, документы, Telegram) и в PAIE.</p>'
        '<div class="mp-hint">'
        '<div><b>«Авто» всегда первым</b>Сам выбирает модель под вопрос, его здесь не настраивают.</div>'
        '<div><b>Уровень усилия</b>Сколько модель думает перед ответом: выше — точнее на трудных задачах, но дольше и дороже. '
        'Пользователь выбирает из отмеченных, руководитель организации может ограничить.</div>'
        '<div><b>Цена</b>Кредиты списываются по фактической цене OpenRouter: дорогие модели и высокие уровни тратят их быстрее.</div>'
        "</div>"
        f'<div class="mp-sec"><h2>В переключателе у пользователей</h2><span>показываются {shown} из {len(models)} · '
        'порядок — стрелками</span></div>'
        f'<div class="mp-list">{items}</div>'
        f'<div class="mp-sec" id="add"><h2>Добавить модель</h2><span>каталог OpenRouter · {len(catalog)} моделей</span></div>'
        f"{search}<div class=\"mp-prov\">{prov}</div>{found}"
    )
    return page(ctx, active="models", title="Модели", body=body, **flash)

