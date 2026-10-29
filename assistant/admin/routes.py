"""Маршруты админки (/dashboard). Кто вошёл, решает usage_server (сессия Орбиты, локальный доступ, токен)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from assistant.admin import audit, data, views

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}


@dataclass
class Actor:
    """Кто открыл админку. ``can_write`` — оператор по сессии или локальный доступ; токен — только просмотр."""

    id: str
    label: str
    can_write: bool
    token: str = ""


class NeedLogin(Exception):
    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


def make_router(
    prefix: str,
    *,
    authenticate: Callable[[Request], Actor],
    webapp_url: Callable[[], str],
) -> APIRouter:
    """``authenticate`` возвращает Actor или бросает NeedLogin."""
    r = APIRouter(prefix=prefix)

    def _html(content: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(content, status_code=status, headers=dict(_NO_CACHE))

    def _actor(request: Request) -> Actor | Response:
        try:
            return authenticate(request)
        except NeedLogin as e:
            return _html(views.login_page(prefix=prefix, webapp_url=webapp_url(), reason=e.reason), 401)

    def _ctx(actor: Actor) -> views.Ctx:
        try:
            pending = len(data.access.list_pending(data.SCOPE))
        except Exception:
            pending = 0
        return views.Ctx(prefix=prefix, actor_label=actor.label, can_write=actor.can_write, token=actor.token, pending=pending)

    def _flash(request: Request) -> dict[str, str]:
        return {"msg": request.query_params.get("msg") or "", "err": request.query_params.get("err") or ""}

    def _days(request: Request, default: int = 30) -> int:
        try:
            d = int(request.query_params.get("days") or default)
        except ValueError:
            d = default
        return d if d in (7, 30, 90) else default

    def _writer(request: Request) -> Actor:
        """Для POST: только оператор/локально и только с этого же сайта (защита от подделки формы)."""
        actor = _actor(request)
        if not isinstance(actor, Actor):
            raise HTTPException(status_code=401, detail="Войдите в Орбиту как оператор")
        if not actor.can_write:
            raise HTTPException(status_code=403, detail="Только просмотр: войдите в Орбиту как оператор")
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if origin:
            host = urlparse(origin).netloc
            if host and host != request.headers.get("host", ""):
                raise HTTPException(status_code=403, detail="Форма отправлена с чужого сайта")
        return actor

    def _back(path: str, *, msg: str = "", err: str = "") -> RedirectResponse:
        path = path if path.startswith("/") and not path.startswith("//") else "/"
        path = path.rstrip("?") or "/"
        sep = "&" if "?" in path else "?"
        q = ""
        from urllib.parse import quote

        if msg:
            q = f"{sep}msg={quote(msg)}"
        elif err:
            q = f"{sep}err={quote(err)}"
        return RedirectResponse(prefix + path + q, status_code=303)

    # ---------- страницы ----------

    @r.get("/", response_class=HTMLResponse)
    async def admin_overview(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        d = await run_in_threadpool(data.overview)
        return _html(views.overview_page(_ctx(actor), d, **_flash(request)))

    @r.get("/users", response_class=HTMLResponse)
    async def admin_users(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        days = _days(request)
        users = await run_in_threadpool(data.users_directory, *data.period(days))
        return _html(views.users_page(
            _ctx(actor), users, q=(request.query_params.get("q") or "").strip(),
            status=(request.query_params.get("status") or "").strip(), days=days, **_flash(request),
        ))

    @r.get("/user/{uid}", response_class=HTMLResponse)
    async def admin_user(uid: str, request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        days = _days(request)
        card = await run_in_threadpool(data.user_card, uid, *data.period(days))
        if not card:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        hist = await run_in_threadpool(lambda: data.label_targets(audit.recent(limit=50, target="user:" + uid)))
        return _html(views.user_page(_ctx(actor), card, days=days, audit=hist, **_flash(request)))

    @r.post("/user/{uid}/access")
    async def admin_user_access(uid: str, request: Request, action: str = Form(...), back: str = Form("/users")) -> Response:
        actor = _writer(request)
        if not uid.isdigit():
            raise HTTPException(status_code=400, detail="Неверный id")
        from assistant.bot import access_gate

        acc = data.access
        pending = acc.get_pending_user(int(uid), scope=data.SCOPE) or {}
        username = pending.get("username") or None
        if not username:
            from assistant.stores import note_members

            username = (note_members.get_profile(uid) or {}).get("username")
        if pending:
            # имя из заявки — в профиль, иначе после одобрения в списке останется «ID …»
            await run_in_threadpool(data.remember_requester, uid, pending)
        if action == "approve":
            await run_in_threadpool(lambda: acc.add_approved_user(username=username, user_id=int(uid), scope=data.SCOPE))
            sent = await run_in_threadpool(access_gate.notify_user_sync, int(uid),
                                           "Вам открыт доступ к Orbita. Напишите /start, чтобы начать.")
            msg = "Доступ открыт" + (", пользователю отправлено сообщение" if sent else " (сообщение в Telegram не ушло)")
        elif action == "deny":
            await run_in_threadpool(lambda: acc.deny_user(int(uid), scope=data.SCOPE))
            await run_in_threadpool(access_gate.notify_user_sync, int(uid),
                                    "Администратор отклонил заявку на доступ к боту.")
            msg = "Заявка отклонена"
        elif action == "revoke":
            await run_in_threadpool(lambda: acc.remove_approved_user(int(uid), username=username, scope=data.SCOPE))
            await run_in_threadpool(lambda: acc.deny_user(int(uid), scope=data.SCOPE))
            msg = "Доступ закрыт"
        else:
            raise HTTPException(status_code=400, detail="Неизвестное действие")
        await run_in_threadpool(lambda: audit.log(actor.label, "access." + action, "user:" + uid,
                                                  username=("@" + username) if username else ""))
        return _back(back, msg=msg)

    @r.get("/orgs", response_class=HTMLResponse)
    async def admin_orgs(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.stores import orgs_store

        orgs = await run_in_threadpool(orgs_store.admin_list_orgs)
        return _html(views.orgs_page(_ctx(actor), orgs, q=(request.query_params.get("q") or "").strip(), **_flash(request)))

    @r.get("/org/{org_id}", response_class=HTMLResponse)
    async def admin_org(org_id: int, request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.stores import orgs_store

        org = await run_in_threadpool(orgs_store.get_org, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Организация не найдена")
        members = await run_in_threadpool(orgs_store.list_members, org_id)
        hist = await run_in_threadpool(lambda: data.label_targets(audit.recent(limit=50, target=f"org:{org_id}")))
        return _html(views.org_page(_ctx(actor), org, members, orgs_store.PLANS, hist, **_flash(request)))

    async def _org_action(org_id: int, fn: Callable[[], Any], *, ok: str) -> RedirectResponse:
        from assistant.stores.orgs_store import OrgError

        try:
            await run_in_threadpool(fn)
        except OrgError as e:
            return _back(f"/org/{org_id}", err=str(e))
        return _back(f"/org/{org_id}", msg=ok)

    @r.post("/org/{org_id}/owner")
    async def admin_org_owner(org_id: int, request: Request, key: str = Form(...)) -> Response:
        actor = _writer(request)
        from assistant.stores import orgs_store

        target: Optional[int] = await run_in_threadpool(orgs_store.resolve_user, actor.id if actor.id.isdigit() else 0, key)
        if not target:
            return _back(f"/org/{org_id}", err="Такого пользователя в Орбите нет — пусть сначала откроет приложение")

        def run() -> None:
            from assistant.stores import org_contacts

            orgs_store.admin_set_owner(org_id, target, by=actor.label)
            org_contacts.sync(org_id)
            audit.log(actor.label, "org.owner", f"org:{org_id}", user_id=str(target), key=key)

        return await _org_action(org_id, run, ok="Владелец назначен")

    @r.post("/org/{org_id}/billing")
    async def admin_org_billing(org_id: int, request: Request, plan: str = Form(...), seats: int = Form(0)) -> Response:
        actor = _writer(request)
        from assistant.stores import orgs_store

        def run() -> None:
            orgs_store.admin_set_billing(org_id, plan=plan, seats=seats)
            audit.log(actor.label, "org.billing", f"org:{org_id}", plan=plan, seats=seats)

        return await _org_action(org_id, run, ok="Тариф сохранён")

    @r.post("/org/{org_id}/member/{member_id}/plan")
    async def admin_org_member_plan(org_id: int, member_id: int, request: Request, plan: str = Form(...)) -> Response:
        actor = _writer(request)
        from assistant.stores import orgs_store

        def run() -> None:
            orgs_store.admin_set_member_plan(org_id, member_id, plan)
            audit.log(actor.label, "org.member_plan", f"org:{org_id}", member=member_id, plan=plan)

        return await _org_action(org_id, run, ok="Тариф участника сохранён")

    @r.get("/spend", response_class=HTMLResponse)
    async def admin_spend(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.lib import usage_store

        days = _days(request)
        op = (request.query_params.get("op") or "").strip()
        d = await run_in_threadpool(data.spend, *data.period(days), op or None)
        return _html(views.spend_page(_ctx(actor), d, days=days, op=op,
                                      op_label=usage_store.operation_label_ru(op) if op else "", **_flash(request)))

    @r.get("/days")
    async def admin_days_redirect(request: Request) -> Response:
        q = request.url.query
        return RedirectResponse(prefix + "/spend" + (("?" + q) if q else ""), status_code=307)

    @r.get("/audit", response_class=HTMLResponse)
    async def admin_audit(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        items = await run_in_threadpool(lambda: data.label_targets(audit.recent(limit=300)))
        return _html(views.audit_page(_ctx(actor), items, **_flash(request)))

    return r
