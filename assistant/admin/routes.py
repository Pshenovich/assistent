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

    @r.get("/product", response_class=HTMLResponse)
    async def admin_product(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.admin import product_data
        from assistant.lib import analytics_forward

        days = _days(request)
        d = await run_in_threadpool(product_data.product, days)
        return _html(views.product_page(_ctx(actor), d, posthog=analytics_forward.enabled(), **_flash(request)))

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
        if not card and uid.isdigit():
            # старый id: склеен с другим аккаунтом или это Telegram, привязанный к почте
            from assistant.stores import accounts

            target = accounts.resolve_alias(uid) or (accounts.get_by_tg(int(uid)) or {}).get("user_id")
            if target and str(target) != uid:
                return RedirectResponse(_ctx(actor).url("/user/" + str(target)), status_code=303)
        if not card:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        hist = await run_in_threadpool(lambda: data.label_targets(audit.recent(limit=50, target="user:" + uid)))
        from assistant.admin import product_data

        try:
            activity = await run_in_threadpool(product_data.user_activity, uid)
        except Exception as e:
            print(f"[admin] user_activity uid={uid} err={e!r}")
            activity = None
        return _html(views.user_page(_ctx(actor), card, days=days, audit=hist, activity=activity, **_flash(request)))

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

    @r.post("/user/{uid}/merge")
    async def admin_user_merge(uid: str, request: Request) -> Response:
        """Старая кнопка со строки пользователя: ведёт на пробный прогон."""
        _writer(request)
        if not uid.isdigit():
            raise HTTPException(status_code=400, detail="Неверный id")
        chk = await run_in_threadpool(data.merge_check, uid)
        if isinstance(chk, str):
            return _back("/user/" + uid, err=chk)
        return _back(f"/merge?a={chk[0]}&b={chk[1]}")

    @r.get("/twins", response_class=HTMLResponse)
    async def admin_twins(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.stores import account_merge

        pairs = await run_in_threadpool(account_merge.candidates)
        tails = await run_in_threadpool(account_merge.tails)
        names = await run_in_threadpool(
            data.names_for, [p["src"] for p in pairs] + [p["dst"] for p in pairs] + [t["new"] for t in tails]
        )
        return _html(views.twins_page(_ctx(actor), pairs, names, tails, **_flash(request)))

    @r.get("/merge", response_class=HTMLResponse)
    async def admin_merge_preview(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.stores import account_merge
        from assistant.stores.accounts import AccountError

        a = (request.query_params.get("a") or "").strip()
        b = (request.query_params.get("b") or "").strip()
        if not (a.isdigit() and b.isdigit()):
            return _back("/twins", err="Укажите два числовых id")
        try:
            plan = await run_in_threadpool(account_merge.plan, a, b)
        except AccountError as e:
            return _back("/twins", err=str(e))
        names = await run_in_threadpool(data.names_for, [plan["src"], plan["dst"]])
        return _html(views.merge_page(_ctx(actor), plan, names, **_flash(request)))

    @r.post("/merge")
    async def admin_merge(request: Request, src: str = Form(...), dst: str = Form(...)) -> Response:
        actor = _writer(request)
        from assistant.stores import account_merge
        from assistant.stores.accounts import AccountError

        if not (src.isdigit() and dst.isdigit()):
            raise HTTPException(status_code=400, detail="Неверный id")
        try:
            rep = await run_in_threadpool(lambda: account_merge.merge(int(src), int(dst), reason="admin", actor=actor.label))
        except AccountError as e:
            return _back(f"/merge?a={src}&b={dst}", err=str(e))
        moved = sum(v["moved"] + v["text"] for db in rep["tables"].values() for v in db.values())
        await run_in_threadpool(lambda: audit.log(actor.label, "account.merge", f"user:{rep['dst']}",
                                                  merged=str(rep["src"]), rows=str(moved)))
        msg = (f"Старая склейка дочищена: перенесено {moved} записей" if rep.get("tail")
               else f"Аккаунты склеены: перенесено {moved} записей, вход по почте ведёт сюда")
        if rep["errors"]:
            return _back("/user/" + str(rep["dst"]), err=msg + ". Ошибки: " + "; ".join(rep["errors"])[:400])
        return _back("/user/" + str(rep["dst"]), msg=msg)

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

    # ---------- модели ИИ ----------

    def _catalog() -> list[dict[str, Any]]:
        from assistant.integrations.openrouter_client import openrouter_catalog

        try:
            return openrouter_catalog()
        except Exception as e:
            print(f"[admin] openrouter catalog failed err={e!r}")
            return []

    @r.get("/models", response_class=HTMLResponse)
    async def admin_models(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        from assistant.admin import models_page
        from assistant.stores import llm_models

        models = await run_in_threadpool(llm_models.list_models)
        catalog = await run_in_threadpool(_catalog)
        return _html(models_page.models_page(
            _ctx(actor), models, catalog, q=(request.query_params.get("q") or "").strip(),
            labels=llm_models.EFFORT_LABELS, all_levels=llm_models.EFFORTS, **_flash(request),
        ))

    @r.post("/models/add")
    async def admin_models_add(request: Request, id: str = Form(...)) -> Response:
        actor = _writer(request)
        from assistant.stores import llm_models

        cat = {c["id"]: c for c in await run_in_threadpool(_catalog)}
        c = cat.get(id.strip())
        if c is None:
            return _back("/models", err="Такой модели нет в каталоге OpenRouter")
        # Уровни по умолчанию: «быстро / обычно / тщательно» из тех, что принимает модель.
        efforts: list[str] = []
        if c["efforts"] is not None:
            sup = c["efforts"] or list(llm_models.EFFORTS)
            efforts = [e for e in llm_models.SEED_EFFORTS if e in sup] or sup[:1]
        default = c["default_effort"] if c["default_effort"] in efforts else ""
        try:
            m = await run_in_threadpool(lambda: llm_models.save(
                c["id"], label=c["name"], enabled=True, efforts=efforts, default_effort=default, by=actor.label))
        except llm_models.ModelError as e:
            return _back("/models", err=str(e))
        await run_in_threadpool(lambda: audit.log(actor.label, "models.add", "model:" + m["id"],
                                                  efforts=", ".join(m["efforts"])))
        return _back("/models", msg=f"«{m['label']}» добавлена — проверьте название и уровни")

    @r.post("/models/save")
    async def admin_models_save(request: Request) -> Response:
        actor = _writer(request)
        from assistant.stores import llm_models

        form = await request.form()
        mid = str(form.get("id") or "").strip()
        cur = await run_in_threadpool(llm_models.get, mid)
        if cur is None:
            return _back("/models", err="Модели нет в списке")
        efforts = [str(x) for x in form.getlist("efforts")]
        try:
            m = await run_in_threadpool(lambda: llm_models.save(
                mid, label=str(form.get("label") or ""), enabled=bool(form.get("enabled")),
                efforts=efforts, default_effort=str(form.get("default_effort") or ""), by=actor.label))
        except llm_models.ModelError as e:
            return _back("/models", err=str(e))
        await run_in_threadpool(lambda: audit.log(
            actor.label, "models.save", "model:" + mid, label=m["label"], enabled="да" if m["enabled"] else "нет",
            efforts=", ".join(m["efforts"]) or "—", default_effort=m["default_effort"]))
        return _back("/models", msg=f"«{m['label']}» сохранена")

    @r.post("/models/remove")
    async def admin_models_remove(request: Request, id: str = Form(...)) -> Response:
        actor = _writer(request)
        from assistant.stores import llm_models

        if not await run_in_threadpool(llm_models.remove, id.strip()):
            return _back("/models", err="Модели нет в списке")
        await run_in_threadpool(lambda: audit.log(actor.label, "models.remove", "model:" + id.strip()))
        return _back("/models", msg="Модель убрана из списка. У тех, кто её выбрал, Лео ответит моделью по умолчанию")

    @r.post("/models/move")
    async def admin_models_move(request: Request, id: str = Form(...), delta: int = Form(0)) -> Response:
        actor = _writer(request)
        from assistant.stores import llm_models

        try:
            await run_in_threadpool(llm_models.move, id.strip(), max(-1, min(1, int(delta))))
        except llm_models.ModelError as e:
            return _back("/models", err=str(e))
        await run_in_threadpool(lambda: audit.log(actor.label, "models.move", "model:" + id.strip()))
        return _back("/models")

    @r.get("/audit", response_class=HTMLResponse)
    async def admin_audit(request: Request) -> Response:
        actor = _actor(request)
        if not isinstance(actor, Actor):
            return actor
        items = await run_in_threadpool(lambda: data.label_targets(audit.recent(limit=300)))
        return _html(views.audit_page(_ctx(actor), items, **_flash(request)))

    return r
