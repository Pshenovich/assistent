"""HTML админки: общий каркас (боковое меню, светлая/тёмная тема) и страницы."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlencode

from assistant.admin.data import STATUS_LABEL

NAV = (
    ("overview", "Обзор", "/"),
    ("users", "Пользователи", "/users"),
    ("orgs", "Организации", "/orgs"),
    ("spend", "Расход", "/spend"),
    ("audit", "Журнал действий", "/audit"),
    ("board", "PAIE", "/board"),
)

def _plan_labels() -> dict[str, str]:
    from assistant.stores.orgs_store import PLANS

    return dict(PLANS)


PLAN_LABEL = _plan_labels()
ROLE_LABEL = {"owner": "Владелец", "admin": "Админ", "member": "Участник"}
MEMBER_STATUS = {"active": "", "pending": "Просится", "invited": "Приглашён"}
DETAIL_LABEL = {"username": "ник", "user_id": "пользователь", "key": "указали", "plan": "тариф",
                "seats": "мест", "member": "участник №"}

ACTION_LABEL = {
    "access.approve": "Открыл доступ",
    "access.deny": "Отклонил заявку",
    "access.revoke": "Закрыл доступ",
    "org.owner": "Назначил владельца",
    "org.billing": "Изменил тариф организации",
    "org.member_plan": "Изменил тариф участника",
}

CSS = """
:root{--bg:#f5f6f8;--panel:#fff;--text:#16181d;--muted:#6b7280;--faint:#9aa1ad;--line:#e6e8ec;
--accent:#3b6ef5;--accent-soft:#e8eefe;--ok:#15803d;--ok-soft:#e6f4ea;--warn:#b45309;--warn-soft:#fdf1e1;
--bad:#b91c1c;--bad-soft:#fdeaea;--bar:#3b6ef5;--shadow:0 1px 2px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#171a21;--text:#e8eaee;--muted:#9aa1ad;--faint:#6b7280;
--line:#262a33;--accent:#7aa2ff;--accent-soft:#1d2740;--ok:#4ade80;--ok-soft:#13291c;--warn:#fbbf24;--warn-soft:#2d2412;
--bad:#f87171;--bad-soft:#2e1517;--bar:#7aa2ff;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,system-ui,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.shell{display:flex;min-height:100vh}
.side{width:220px;flex:0 0 220px;padding:20px 12px;border-right:1px solid var(--line);background:var(--panel);
position:sticky;top:0;height:100vh}
.brand{font-weight:700;font-size:16px;padding:0 10px 4px}.brand small{display:block;font-weight:400;color:var(--muted);font-size:12px}
.side nav{margin-top:18px;display:flex;flex-direction:column;gap:2px}
.side nav a{padding:8px 10px;border-radius:8px;color:var(--text)}
.side nav a:hover{background:var(--bg);text-decoration:none}
.side nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.side nav .cnt{float:right;background:var(--warn-soft);color:var(--warn);border-radius:999px;padding:0 7px;font-size:12px}
.who{position:absolute;bottom:16px;left:22px;right:12px;color:var(--muted);font-size:12px}
main{flex:1;min-width:0;padding:28px 32px 60px;max-width:1200px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 10px}
.sub{color:var(--muted);margin:0 0 18px}
.flash{padding:10px 14px;border-radius:10px;margin:0 0 16px}.flash.ok{background:var(--ok-soft);color:var(--ok)}
.flash.err{background:var(--bad-soft);color:var(--bad)}.flash.ro{background:var(--warn-soft);color:var(--warn)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
.kpi{padding:14px 16px}.kpi .l{color:var(--muted);font-size:12px}.kpi .v{font-size:24px;font-weight:650;margin-top:4px}
.kpi .d{font-size:12px;color:var(--muted);margin-top:2px}.up{color:var(--bad)}.down{color:var(--ok)}
.card{padding:16px;margin-top:12px}.card h2{margin-top:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
table{width:100%;border-collapse:collapse}th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--muted);font-weight:500;font-size:12px;white-space:nowrap}tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:hover{background:color-mix(in srgb,var(--accent) 4%,transparent)}
.tw{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:auto;box-shadow:var(--shadow)}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;background:var(--bg);color:var(--muted);white-space:nowrap}
.b-open,.b-active{background:var(--ok-soft);color:var(--ok)}.b-pending{background:var(--warn-soft);color:var(--warn)}
.b-denied,.b-none{background:var(--bad-soft);color:var(--bad)}.b-operator{background:var(--accent-soft);color:var(--accent)}
.muted{color:var(--muted)}.small{font-size:12px}
.bar{height:6px;border-radius:3px;background:var(--bar);opacity:.85}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}
.chips a{padding:5px 12px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--text)}
.chips a.on{background:var(--accent);border-color:var(--accent);color:#fff}
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 14px}
input,select{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
button,.btn{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:8px;
padding:7px 12px;cursor:pointer;white-space:nowrap}
button.pri{background:var(--accent);border-color:var(--accent);color:#fff}button.bad{color:var(--bad)}
form.inl{display:inline-flex;gap:6px;align-items:center;margin:0}
.chart{width:100%;height:160px;display:block}
.chart rect{fill:var(--bar)}.chart rect.z{fill:var(--line)}.chart text{fill:var(--faint);font-size:10px}
.kv{display:grid;grid-template-columns:160px 1fr;gap:6px 12px}.kv div:nth-child(odd){color:var(--muted)}
.empty{color:var(--muted);padding:18px;text-align:center}
@media (max-width:820px){.shell{display:block}.side{position:static;width:auto;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:12px}
.side nav{flex-direction:row;flex-wrap:wrap;margin-top:8px}.who{position:static;margin:8px 10px 0}main{padding:18px 16px 40px}
.grid2{grid-template-columns:1fr}.kv{grid-template-columns:1fr}}
"""


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def rub(v: float) -> str:
    v = float(v or 0)
    if 0 < abs(v) < 1:
        return f"{v:.2f} ₽".replace(".", ",")
    return f"{v:,.0f} ₽".replace(",", " ")


def num(v: Any) -> str:
    try:
        return f"{int(v or 0):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def ago(dt: datetime | None) -> str:
    if not dt:
        return "—"
    sec = (datetime.now(timezone.utc) - dt).total_seconds()
    if sec < 90:
        return "только что"
    if sec < 3600:
        return f"{int(sec // 60)} мин назад"
    if sec < 86400:
        return f"{int(sec // 3600)} ч назад"
    days = int(sec // 86400)
    if days < 31:
        return f"{days} дн назад"
    return dt.strftime("%d.%m.%Y")


def fmt_ts(s: str) -> str:
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except (TypeError, ValueError):
        return esc(s)


def badge(status: str, label: str | None = None) -> str:
    return f'<span class="badge b-{esc(status)}">{esc(label or STATUS_LABEL.get(status, status))}</span>'


class Ctx:
    """Что нужно каркасу: префикс, кто вошёл, можно ли менять, токен для ссылок."""

    def __init__(self, *, prefix: str, actor_label: str, can_write: bool, token: str = "", pending: int = 0) -> None:
        self.prefix = prefix.rstrip("/")
        self.actor_label = actor_label
        self.can_write = can_write
        self.token = token
        self.pending = pending

    def url(self, path: str, **params: Any) -> str:
        p = {k: v for k, v in params.items() if v not in (None, "")}
        if self.token:
            p["access_token"] = self.token
        q = ("?" + urlencode(p)) if p else ""
        return self.prefix + (path if path.startswith("/") else "/" + path) + q


def page(ctx: Ctx, *, active: str, title: str, body: str, msg: str = "", err: str = "") -> str:
    nav = []
    for key, label, path in NAV:
        cnt = f'<span class="cnt">{ctx.pending}</span>' if key == "users" and ctx.pending else ""
        nav.append(f'<a href="{esc(ctx.url(path))}" class="{"on" if key == active else ""}">{esc(label)}{cnt}</a>')
    flash = ""
    if msg:
        flash += f'<div class="flash ok">{esc(msg)}</div>'
    if err:
        flash += f'<div class="flash err">{esc(err)}</div>'
    if not ctx.can_write:
        flash += '<div class="flash ro">Только просмотр: вы вошли по токену. Чтобы менять доступ и организации, войдите в Орбиту как оператор.</div>'
    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{esc(title)} · Админка Орбиты</title><meta name=\"robots\" content=\"noindex\">"
        f"<style>{CSS}</style></head><body><div class=\"shell\"><aside class=\"side\">"
        "<div class=\"brand\">Орбита<small>Админка</small></div>"
        f"<nav>{''.join(nav)}</nav><div class=\"who\">{esc(ctx.actor_label)}</div></aside>"
        f"<main>{flash}{body}</main></div></body></html>"
    )


def bars_svg(series: list[dict[str, Any]], key: str = "rub") -> str:
    """Столбики по дням без библиотек; подпись — каждый ~7-й день."""
    if not series:
        return '<div class="empty">Нет данных</div>'
    w, h, pad = 700, 160, 18
    n = len(series)
    mx = max(float(s.get(key) or 0) for s in series) or 1.0
    bw = (w - 8) / n
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img">']
    for i, s in enumerate(series):
        v = float(s.get(key) or 0)
        bh = max(2.0, (h - pad - 6) * v / mx) if v > 0 else 2.0
        x = 4 + i * bw
        y = h - pad - bh
        tip = f"{s['date'][8:10]}.{s['date'][5:7]}: {rub(v) if key == 'rub' else num(v)}"
        zero = ' class="z"' if v <= 0 else ""
        parts.append(f'<rect x="{x + 1:.1f}" y="{y:.1f}" width="{max(1.0, bw - 2):.1f}" height="{bh:.1f}" rx="2"'
                     f'{zero}><title>{esc(tip)}</title></rect>')
        if i % max(1, n // 6) == 0:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{h - 4}" text-anchor="middle">{s["date"][8:10]}.{s["date"][5:7]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def table(head: Iterable[tuple[str, str]], rows: list[str], empty: str = "Пусто") -> str:
    th = "".join(f'<th class="{cls}">{esc(label)}</th>' for label, cls in head)
    body = "".join(rows) if rows else f'<tr><td colspan="99" class="empty">{esc(empty)}</td></tr>'
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def ops_rows(ctx: Ctx, ops: list[dict[str, Any]], *, link: bool = False) -> list[str]:
    mx = max((o["rub"] for o in ops), default=0) or 1
    out = []
    for o in ops:
        label = esc(o["label"])
        if link:
            label = f'<a href="{esc(ctx.url("/spend", op=o["op"]))}">{label}</a>'
        out.append(
            f"<tr><td>{label}<div class=\"bar\" style=\"width:{max(2, 100 * o['rub'] / mx):.0f}%\"></div></td>"
            f"<td class=\"n\">{num(o['calls'])}</td><td class=\"n\">{rub(o['rub'])}</td></tr>"
        )
    return out


def user_link(ctx: Ctx, u: dict[str, Any]) -> str:
    nick = ""
    if u.get("username") and not str(u["name"]).startswith("@"):
        nick = f' <span class="muted small">@{esc(u["username"])}</span>'

    return f'<a href="{esc(ctx.url("/user/" + u["user_id"]))}">{esc(u["name"])}</a>{nick}'


def access_buttons(ctx: Ctx, u: dict[str, Any], *, back: str) -> str:
    if not ctx.can_write or u["status"] == "operator":
        return ""
    action = esc(ctx.prefix + "/user/" + u["user_id"] + "/access")
    def btn(a: str, label: str, cls: str = "") -> str:
        return (f'<form class="inl" method="post" action="{action}"><input type="hidden" name="action" value="{a}">'
                f'<input type="hidden" name="back" value="{esc(back)}"><button class="{cls}">{esc(label)}</button></form>')
    if u["status"] == "pending":
        return btn("approve", "Открыть доступ", "pri") + " " + btn("deny", "Отклонить", "bad")
    if u["status"] == "open":
        return btn("revoke", "Закрыть доступ", "bad")
    return btn("approve", "Открыть доступ")


# ---------- страницы ----------


def overview_page(ctx: Ctx, d: dict[str, Any], **flash: str) -> str:
    delta = ""
    if 0 < d["spend_prev_rub"] < max(50.0, d["spend_rub"] / 20):
        delta = f'за прошлые 30 дней — {rub(d["spend_prev_rub"])}'
    elif d["spend_prev_rub"] > 0:
        pct = (d["spend_rub"] - d["spend_prev_rub"]) / d["spend_prev_rub"] * 100
        delta = f'<span class="{"up" if pct > 0 else "down"}">{"+" if pct > 0 else ""}{pct:.0f}%</span> к прошлым 30 дням'
    kpis = (
        f'<div class="kpi"><div class="l">Активны за 7 дней</div><div class="v">{num(d["active7"])}</div>'
        f'<div class="d">за 30 дней — {num(d["active30"])} из {num(d["users_total"])}</div></div>'
        f'<div class="kpi"><div class="l">Ждут доступа</div><div class="v">{num(len(d["pending"]))}</div>'
        f'<div class="d"><a href="{esc(ctx.url("/users", status="pending"))}">разобрать заявки</a></div></div>'
        f'<div class="kpi"><div class="l">Организаций</div><div class="v">{num(d["orgs"])}</div>'
        f'<div class="d"><a href="{esc(ctx.url("/orgs"))}">все организации</a></div></div>'
        f'<div class="kpi"><div class="l">Расход за 30 дней</div><div class="v">{rub(d["spend_rub"])}</div>'
        f'<div class="d">{delta or num(d["calls"]) + " вызовов модели"}</div></div>'
    )
    pend = ""
    if d["pending"]:
        rows = [f"<tr><td>{user_link(ctx, u)}</td><td class=\"muted\">{ago(u['last_seen'])}</td>"
                f"<td class=\"n\">{access_buttons(ctx, u, back='/')}</td></tr>" for u in d["pending"][:8]]
        pend = '<h2>Заявки на доступ</h2>' + table((("Кто", ""), ("Когда", ""), ("", "n")), rows)
    tops = [f"<tr><td>{user_link(ctx, u)}</td><td class=\"n\">{num(u['n_calls'])}</td><td class=\"n\">{rub(u['cost_rub'])}</td></tr>"
            for u in d["top_users"]]
    if d["unknown_rub"] > 0:
        tops.append(f'<tr><td class="muted">Фоновые задачи (без пользователя)</td><td class="n"></td><td class="n">{rub(d["unknown_rub"])}</td></tr>')
    body = (
        '<h1>Обзор</h1><p class="sub">Последние 30 дней, время — UTC.</p>'
        f'<div class="kpis">{kpis}</div>{pend}'
        f'<div class="card"><h2>Расход по дням</h2>{bars_svg(d["series"])}'
        f'<div class="small muted"><a href="{esc(ctx.url("/spend"))}">Подробнее о расходе →</a></div></div>'
        '<div class="grid2">'
        f'<div><h2>На что уходят деньги</h2>{table((("Операция", ""), ("Вызовов", "n"), ("₽", "n")), ops_rows(ctx, d["ops"], link=True))}</div>'
        f'<div><h2>Кто тратит больше всех</h2>{table((("Пользователь", ""), ("Вызовов", "n"), ("₽", "n")), tops)}</div>'
        "</div>"
    )
    return page(ctx, active="overview", title="Обзор", body=body, **flash)


def period_chips(ctx: Ctx, path: str, days: int, **keep: Any) -> str:
    out = []
    for n, label in ((7, "7 дней"), (30, "30 дней"), (90, "90 дней")):
        out.append(f'<a class="{"on" if n == days else ""}" href="{esc(ctx.url(path, days=n, **keep))}">{label}</a>')
    return '<div class="chips">' + "".join(out) + "</div>"


def users_page(ctx: Ctx, users: list[dict[str, Any]], *, q: str, status: str, days: int, **flash: str) -> str:
    counts: dict[str, int] = {}
    for u in users:
        counts[u["status"]] = counts.get(u["status"], 0) + 1
    shown = users
    if status:
        shown = [u for u in shown if u["status"] == status]
    if q:
        ql = q.lower().lstrip("@")
        shown = [u for u in shown if ql in u["name"].lower() or ql in u["username"].lower() or ql == u["user_id"]
                 or any(ql in o.lower() for o in u["orgs"])]
    st = [f'<a class="{"on" if not status else ""}" href="{esc(ctx.url("/users", q=q, days=days))}">Все · {len(users)}</a>']
    for key in ("pending", "open", "operator", "denied"):
        if counts.get(key):
            st.append(f'<a class="{"on" if status == key else ""}" href="{esc(ctx.url("/users", status=key, q=q, days=days))}">'
                      f'{esc(STATUS_LABEL[key])} · {counts[key]}</a>')
    tools = (f'<form class="tools" method="get" action="{esc(ctx.prefix + "/users")}">'
             f'<input type="search" name="q" value="{esc(q)}" placeholder="Имя, @ник, id или организация" style="min-width:260px">'
             f'<input type="hidden" name="status" value="{esc(status)}"><input type="hidden" name="days" value="{days}">'
             + (f'<input type="hidden" name="access_token" value="{esc(ctx.token)}">' if ctx.token else "")
             + '<button>Найти</button></form>')
    back = "/users?" + urlencode({k: v for k, v in (("status", status), ("q", q)) if v})
    rows = []
    for u in shown:
        orgs = ", ".join(esc(o) for o in u["orgs"]) or '<span class="muted">—</span>'
        rows.append(
            f"<tr><td>{user_link(ctx, u)}</td><td>{badge(u['status'])}</td><td>{orgs}</td>"
            f"<td class=\"muted\">{ago(u['last_seen'])}</td><td class=\"n\">{num(u['n_calls'])}</td>"
            f"<td class=\"n\">{rub(u['cost_rub'])}</td><td class=\"n\">{access_buttons(ctx, u, back=back)}</td></tr>"
        )
    body = (
        '<h1>Пользователи</h1><p class="sub">Все, кто открывал Орбиту, просил доступ или тратил токены. '
        f'Расход — за {days} дн.</p>'
        f'<div class="chips">{"".join(st)}</div>{tools}{period_chips(ctx, "/users", days, status=status, q=q)}'
        + table((("Пользователь", ""), ("Доступ", ""), ("Организация", ""), ("Был", ""), ("Вызовов", "n"), ("Расход", "n"), ("", "n")),
                rows, "Никого не нашли")
    )
    return page(ctx, active="users", title="Пользователи", body=body, **flash)


def user_page(ctx: Ctx, u: dict[str, Any], *, days: int, audit: list[dict[str, Any]], **flash: str) -> str:
    orgs_rows = [
        f"<tr><td><a href=\"{esc(ctx.url('/org/' + str(o['id'])))}\">{esc(o['name'])}</a></td>"
        f"<td>{esc(ROLE_LABEL.get(o['role'], o['role']))}</td><td>{esc(MEMBER_STATUS.get(o['status'], o['status'])) or badge('active', 'В организации')}</td>"
        f"<td>{esc(PLAN_LABEL.get(o['plan'], o['plan']))}</td></tr>" for o in u["orgs_full"]
    ]
    day_rows = [
        f"<tr><td><a href=\"{esc(ctx.url('/user/' + u['user_id'] + '/day/' + r['date']))}\">{r['date'][8:10]}.{r['date'][5:7]}.{r['date'][:4]}</a></td>"
        f"<td class=\"n\">{num(r['calls'])}</td><td class=\"n\">{rub(r['rub'])}</td></tr>" for r in u["days"]
    ]
    info = (
        f'<div class="kv"><div>Telegram</div><div>{("@" + esc(u["username"])) if u["username"] else "—"} · id {esc(u["user_id"])}</div>'
        f'<div>Доступ</div><div>{badge(u["status"])} {access_buttons(ctx, u, back="/user/" + u["user_id"])}</div>'
        f'<div>Был в Орбите</div><div>{ago(u["last_seen"])}</div>'
        f'<div>Расход за {days} дн</div><div>{rub(u["cost_rub"])} · {num(u["n_calls"])} вызовов</div>'
        f'<div>За всё время</div><div>{rub(u["lifetime_rub"])} · {num(u["lifetime_calls"])} вызовов</div></div>'
    )
    body = (
        f'<p class="small"><a href="{esc(ctx.url("/users"))}">← Пользователи</a></p>'
        f'<h1>{esc(u["name"])}</h1><div class="card">{info}</div>'
        f'<h2>Организации</h2>{table((("Организация", ""), ("Роль", ""), ("Статус", ""), ("Тариф", "")), orgs_rows, "Ни в одной организации")}'
        f'<div style="margin-top:22px">{period_chips(ctx, "/user/" + u["user_id"], days)}</div>'
        '<div class="grid2">'
        f'<div><h2>На что тратит</h2>{table((("Операция", ""), ("Вызовов", "n"), ("₽", "n")), ops_rows(ctx, u["ops"]), "Нет расхода")}</div>'
        f'<div><h2>По дням</h2>{table((("День", ""), ("Вызовов", "n"), ("₽", "n")), day_rows, "Нет расхода")}</div>'
        "</div>"
        f'<h2>Действия операторов</h2>{audit_table(ctx, audit, "Пока ничего")}'
    )
    return page(ctx, active="users", title=u["name"], body=body, **flash)


def orgs_page(ctx: Ctx, orgs: list[dict[str, Any]], *, q: str, **flash: str) -> str:
    shown = orgs
    if q:
        ql = q.lower().lstrip("@")
        shown = [o for o in orgs if ql in o["name"].lower() or ql in o["slug"] or any(ql in d for d in o["domains"])]
    rows = []
    for o in shown:
        owners = ", ".join(f'<a href="{esc(ctx.url("/user/" + w["user_id"]))}">{esc(w["name"])}</a>' for w in o["owners"]) \
            or badge("none", "нет владельца")
        rows.append(
            f"<tr><td><a href=\"{esc(ctx.url('/org/' + str(o['id'])))}\">{esc(o['name'])}</a> <span class=\"muted small\">{esc(o['slug'])}</span></td>"
            f"<td>{', '.join('@' + esc(d) for d in o['domains']) or '<span class=muted>—</span>'}</td><td>{owners}</td>"
            f"<td class=\"n\">{num(o['member_count'])}</td><td>{esc(PLAN_LABEL.get(o['plan'], o['plan']))}</td>"
            f"<td class=\"n\">{num(o['seats_used'])} из {num(o['seats'])}</td><td class=\"muted\">{esc(str(o['created_at'])[:10])}</td></tr>"
        )
    tools = (f'<form class="tools" method="get" action="{esc(ctx.prefix + "/orgs")}">'
             f'<input type="search" name="q" value="{esc(q)}" placeholder="Название, адрес или домен" style="min-width:260px">'
             + (f'<input type="hidden" name="access_token" value="{esc(ctx.token)}">' if ctx.token else "")
             + '<button>Найти</button></form>')
    body = (
        '<h1>Организации</h1><p class="sub">Команды пользователей. Создают их сами пользователи в «Профиль → Организация».</p>'
        + tools
        + table((("Организация", ""), ("Домен", ""), ("Владелец", ""), ("Участников", "n"), ("Тариф", ""), ("Места", "n"), ("Создана", "")),
                rows, "Организаций пока нет")
    )
    return page(ctx, active="orgs", title="Организации", body=body, **flash)


def org_page(ctx: Ctx, org: dict[str, Any], members: list[dict[str, Any]], plans: dict[str, str],
             audit: list[dict[str, Any]], **flash: str) -> str:
    base = ctx.prefix + "/org/" + str(org["id"])

    def plan_select(name: str, value: str) -> str:
        return f'<select name="{name}">' + "".join(
            f'<option value="{esc(k)}"{" selected" if k == value else ""}>{esc(v)}</option>' for k, v in plans.items()
        ) + "</select>"

    rows = []
    for m in members:
        who = (f'<a href="{esc(ctx.url("/user/" + m["user_id"]))}">{esc(m["name"])}</a>' if m["user_id"]
               else esc(str(m["name"]).replace("tg:", "@")))
        st = MEMBER_STATUS.get(m["status"], m["status"])
        plan_cell = esc(plans.get(m["plan"], m["plan"]))
        if ctx.can_write and m["status"] != "pending":
            plan_cell = (f'<form class="inl" method="post" action="{esc(base + "/member/" + str(m["id"]) + "/plan")}">'
                         f'{plan_select("plan", m["plan"])}<button>Сохранить</button></form>')
        rows.append(f"<tr><td>{who}</td><td>{esc(ROLE_LABEL.get(m['role'], m['role']))}</td>"
                    f"<td>{badge('pending', st) if st else badge('active', 'В организации')}</td><td>{plan_cell}</td></tr>")
    billing = (
        f'<div class="kv"><div>Тариф</div><div>{esc(plans.get(org["plan"], org["plan"]))}</div>'
        f'<div>Места</div><div>{num(org["seats_used"])} занято из {num(org["seats"])}</div></div>'
    )
    actions = ""
    if ctx.can_write:
        billing += (f'<form class="tools" method="post" action="{esc(base + "/billing")}" style="margin-top:12px">'
                    f'{plan_select("plan", org["plan"])}<input type="number" name="seats" min="0" max="10000" value="{org["seats"]}" style="width:110px">'
                    '<button class="pri">Сохранить тариф</button></form>'
                    '<p class="small muted">Оплата ещё не подключена — тарифы ни на что не влияют.</p>')
        actions = (
            '<div class="card"><h2>Назначить владельца</h2>'
            '<p class="small muted">Если владелец пропал (уволился, потерял аккаунт). Прежний владелец станет админом; '
            'новый, если не состоял, будет добавлен.</p>'
            f'<form class="tools" method="post" action="{esc(base + "/owner")}">'
            '<input name="key" placeholder="@ник, почта или Telegram id" style="min-width:260px" required>'
            '<button class="pri">Назначить</button></form></div>'
        )
    body = (
        f'<p class="small"><a href="{esc(ctx.url("/orgs"))}">← Организации</a></p>'
        f'<h1>{esc(org["name"])}</h1><p class="sub">{esc(org["slug"])}'
        + (" · " + ", ".join("@" + esc(d) for d in org["domains"]) if org["domains"] else "")
        + f' · вступление: {"сами" if org["join_mode"] == "auto" else "по заявке"} · создана {esc(str(org["created_at"])[:10])}</p>'
        f'<div class="grid2"><div class="card"><h2>Тариф</h2>{billing}</div>{actions}</div>'
        f'<h2>Участники · {len(members)}</h2>{table((("Участник", ""), ("Роль", ""), ("Статус", ""), ("Тариф", "")), rows)}'
        f'<h2>Действия операторов</h2>{audit_table(ctx, audit, "Пока ничего")}'
    )
    return page(ctx, active="orgs", title=org["name"], body=body, **flash)


def spend_page(ctx: Ctx, d: dict[str, Any], *, days: int, op: str, op_label: str, **flash: str) -> str:
    kpis = (
        f'<div class="kpi"><div class="l">Расход</div><div class="v">{rub(d["rub"])}</div><div class="d">${d["usd"]:.2f} · курс {d["rate"]:g} ₽</div></div>'
        f'<div class="kpi"><div class="l">Вызовов модели</div><div class="v">{num(d["calls"])}</div></div>'
        f'<div class="kpi"><div class="l">Токенов</div><div class="v">{num(d["tokens"])}</div>'
        f'<div class="d">из кэша — {num(d["cached"])}</div></div>'
    )
    day_rows = [
        f"<tr><td><a href=\"{esc(ctx.url('/day/' + s['date']))}\">{s['date'][8:10]}.{s['date'][5:7]}.{s['date'][:4]}</a></td>"
        f"<td class=\"n\">{num(s['users'])}</td><td class=\"n\">{num(s['calls'])}</td><td class=\"n\">{num(s['tokens'])}</td>"
        f"<td class=\"n\">{rub(s['rub'])}</td></tr>" for s in reversed(d["series"]) if s["calls"]
    ]
    ops = [f"<tr><td><a href=\"{esc(ctx.url('/spend', op=o['op'], days=days))}\">{esc(o['label'])}</a>"
           f"<div class=\"bar\" style=\"width:{max(2, 100 * o['rub'] / (max((x['rub'] for x in d['ops']), default=0) or 1)):.0f}%\"></div></td>"
           f"<td class=\"n\">{num(o['calls'])}</td><td class=\"n\">{num(o['tokens'])}</td><td class=\"n\">{rub(o['rub'])}</td></tr>"
           for o in d["ops"]]
    title = "Расход" + (f" · {op_label}" if op else "")
    body = (
        f'<h1>{esc(title)}</h1><p class="sub">Вызовы моделей через прокси, время — UTC.'
        + (f' <a href="{esc(ctx.url("/spend", days=days))}">Все операции</a>' if op else "") + "</p>"
        f'{period_chips(ctx, "/spend", days, op=op)}<div class="kpis">{kpis}</div>'
        f'<div class="card"><h2>По дням</h2>{bars_svg(d["series"])}</div>'
        + ("" if op else f'<h2>По операциям</h2>{table((("Операция", ""), ("Вызовов", "n"), ("Токенов", "n"), ("₽", "n")), ops)}')
        + f'<h2>Дни</h2>{table((("День", ""), ("Пользователей", "n"), ("Вызовов", "n"), ("Токенов", "n"), ("₽", "n")), day_rows, "Нет расхода")}'
        '<p class="small muted">Если выбрана операция, итоги сверху — только по ней; по дням — все операции.</p>'
    )
    return page(ctx, active="spend", title="Расход", body=body, **flash)


def audit_table(ctx: Ctx, items: list[dict[str, Any]], empty: str = "Журнал пуст") -> str:
    rows = []
    for a in items:
        detail = ", ".join(f"{esc(DETAIL_LABEL.get(k, k))}: {esc(v)}" for k, v in (a.get("detail") or {}).items() if v not in ("", None))
        target = esc(a.get("target_label") or a["target"])
        if a.get("target_path"):
            target = f'<a href="{esc(ctx.url(a["target_path"]))}">{target}</a>'
        rows.append(f"<tr><td class=\"muted\">{fmt_ts(a['ts'])}</td><td>{esc(a['actor'])}</td>"
                    f"<td>{esc(ACTION_LABEL.get(a['action'], a['action']))}</td><td>{target}</td>"
                    f"<td class=\"small muted\">{detail}</td></tr>")
    return table((("Когда", ""), ("Кто", ""), ("Что", ""), ("Над кем", ""), ("Подробности", "")), rows, empty)


def audit_page(ctx: Ctx, items: list[dict[str, Any]], **flash: str) -> str:
    body = ('<h1>Журнал действий</h1><p class="sub">Кто из операторов что менял: доступ, владельцы, тарифы.</p>'
            + audit_table(ctx, items))
    return page(ctx, active="audit", title="Журнал действий", body=body, **flash)


def login_page(*, prefix: str, webapp_url: str, reason: str = "") -> str:
    text = reason or ("Админка открывается тем, кто вошёл в Орбиту в этом браузере и указан как оператор. "
                      "Войдите в Орбиту и вернитесь на эту страницу.")
    body = (
        '<div style="max-width:440px;margin:12vh auto;text-align:center">'
        '<h1>Админка Орбиты</h1>'
        f'<p class="sub">{esc(text)}</p>'
        f'<p><a class="btn" href="{esc(webapp_url)}">Открыть Орбиту</a> '
        f'<a class="btn" href="{esc(prefix + "/")}">Я вошёл — обновить</a></p></div>'
    )
    return ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Вход · Админка Орбиты</title><meta name=\"robots\" content=\"noindex\"><style>{CSS}</style></head><body>{body}</body></html>")
