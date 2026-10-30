"""Биллинг Орбиты в кредитах ИИ (assistant/stores/credits_store.py).

Каждый вызов ИИ (usage_store.insert_usage_event → charge_cost_usd) и каждая минута
расшифровки (charge_transcription) списываются в кредитах:
- участник организации — до дневного лимита «свои», сверх — со счёта организации;
- без организации — с личного баланса.

Людям показываем кредиты и «реальные рубли» по цене тарифа Solo (1,3 ₽ за кредит).
Оплаты пока нет: «Пополнить» — тестовое пополнение.
"""

from __future__ import annotations

from typing import Any

from assistant.stores import credits_store


def usd_to_rub_rate() -> float:
    return credits_store.usd_to_rub()


def miniapp_rub_per_usd() -> float:
    """₽ по цене Solo за 1 USD себестоимости — для экрана «Расходы»."""
    return credits_store.credits_to_rub(credits_store.credits_for_cost_usd(1.0))


def org_id_for_user(telegram_user_id: int | str) -> str | None:
    """Организация, с чьего счёта платит пользователь (текущая), или None."""
    try:
        from assistant.stores import orgs_store

        org = orgs_store.current_org(telegram_user_id)
    except Exception as e:
        print(f"[billing_store] current_org failed uid={telegram_user_id} err={e!r}")
        return None
    return str(org["id"]) if org else None


def charge_cost_usd(telegram_user_id: int, cost_usd: float, *, operation: str = "") -> None:
    """Списание за вызов ИИ (зовётся из usage_store после записи расхода)."""
    credits = credits_store.credits_for_cost_usd(cost_usd)
    if credits <= 0:
        return
    credits_store.charge(
        telegram_user_id,
        credits,
        org_id=org_id_for_user(telegram_user_id),
        reason_meta={"op": operation, "usd": round(float(cost_usd), 6)} if operation else {"usd": round(float(cost_usd), 6)},
    )


def transcript_minutes(segments: Any) -> float:
    """Длительность расшифровки по сегментам Obuchat (конец последнего), в минутах."""
    end = 0.0
    for seg in segments or ():
        try:
            val = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", None)
            end = max(end, float(val or 0))
        except (TypeError, ValueError):
            continue
    return end / 60.0


def charge_transcription(telegram_user_id: int | str | None, segments: Any, *, operation: str) -> float:
    """2 кредита за минуту расшифровки. Возвращает списанные кредиты; ошибки не роняют расшифровку."""
    if not telegram_user_id:
        return 0.0
    try:
        minutes = transcript_minutes(segments)
        credits = credits_store.credits_for_minutes(minutes)
        if credits > 0:
            credits_store.charge(
                telegram_user_id,
                credits,
                org_id=org_id_for_user(telegram_user_id),
                reason_meta={"op": operation, "minutes": round(minutes, 2)},
            )
        return credits
    except Exception as e:
        print(f"[billing_store] transcription charge failed uid={telegram_user_id} err={e!r}")
        return 0.0


def credits_status(telegram_user_id: int | str) -> dict[str, Any]:
    return credits_store.status(telegram_user_id, org_id=org_id_for_user(telegram_user_id))


MSG_PERSONAL_EMPTY = "Баланс закончился — пополните его в Профиле, и я продолжу."
MSG_ORG_EMPTY = (
    "Вы израсходовали дневной лимит, а на счёте организации закончились кредиты. "
    "Попросите руководителя пополнить счёт — и я продолжу."
)
MSG_OVER_LIMIT = (
    "Вы израсходовали дневной лимит. Я продолжаю работу, "
    "но кредиты будут списываться со счёта организации."
)
LOCKED_LABEL = "Закрыто руководителем организации"


def enforce() -> bool:
    """Блокировать ответы при пустом балансе / счёте (CREDITS_ENFORCE=1); иначе только учёт."""
    import os

    return os.getenv("CREDITS_ENFORCE", "").strip().lower() in ("1", "true", "yes", "on")


def allowed_models_for(telegram_user_id: int | str) -> list[str] | None:
    """Модели, которые руководитель открыл участнику; None — без ограничений."""
    org_id = org_id_for_user(telegram_user_id)
    if org_id is None:
        return None
    return credits_store.member_settings(org_id, telegram_user_id)["allowed_models"]


def mark_locked(telegram_user_id: int | str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Список моделей для экрана: закрытые руководителем — с locked и подписью."""
    allowed = allowed_models_for(telegram_user_id)
    if allowed is None:
        return models
    from assistant.nlu.auto_model import is_auto

    out = []
    for m in models:
        mid = str((m or {}).get("id") or "")
        if mid and mid not in allowed and not is_auto(mid):  # «Авто» сам выбирает из разрешённых
            out.append({**m, "locked": True, "locked_reason": LOCKED_LABEL})
        else:
            out.append(m)
    return out


def chat_gate(telegram_user_id: int | str, model: str | None) -> dict[str, Any]:
    """Перед ответом Лео: можно ли отвечать, какой моделью и что сказать.

    model — выбранная модель; закрытая руководителем заменяется первой разрешённой.
    notice — одно предупреждение в день, когда участник ушёл за свой лимит.
    """
    org_id = org_id_for_user(telegram_user_id)
    st = credits_store.status(telegram_user_id, org_id=org_id)
    run_model = model
    from assistant.nlu.auto_model import is_auto

    if org_id is not None and model and not is_auto(model):  # «Авто» учтёт ограничения сам (auto_model.resolve)
        allowed = credits_store.member_settings(org_id, telegram_user_id)["allowed_models"]
        if allowed is not None and model not in allowed:
            run_model = allowed[0] if allowed else model
    if not enforce():
        # Пока не выкачены экраны (пополнение, настройки руководителя) — только учёт, без блокировки.
        st = {**st, "can_answer": True}
    message = ""
    if not st["can_answer"]:
        message = MSG_PERSONAL_EMPTY if st["mode"] == "personal" else MSG_ORG_EMPTY
    notice = ""
    if st["can_answer"] and st["state"] == "over_limit" and credits_store.take_notice(telegram_user_id, "over_limit"):
        notice = MSG_OVER_LIMIT
    return {"model": run_model, "can_answer": st["can_answer"], "message": message, "notice": notice, "status": st}


def billing_snapshot_for_api(telegram_user_id: int) -> dict:
    st = credits_status(telegram_user_id)
    if st["mode"] == "personal":
        return {
            **st,
            "show_balance": True,
            "balance_credits": st["balance"],
            "balance_rub": round(st["balance_rub"], 2),
            "tariff_label": "Solo",
            "can_top_up": True,
            "plan": "solo",
            "promo_available": False,
        }
    return {
        **st,
        "show_balance": False,
        "tariff_label": "Организация",
        "can_top_up": False,
        "plan": "org",
        "promo_available": False,
    }


def top_up_personal(telegram_user_id: int) -> dict:
    credits_store.top_up("user", telegram_user_id, by_user=telegram_user_id)
    return billing_snapshot_for_api(telegram_user_id)


def apply_promo_code(telegram_user_id: int, code: str) -> tuple[bool, str]:
    del telegram_user_id, code
    return False, "Промокоды не настроены."
