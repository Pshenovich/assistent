"""Биллинг: отображаемые ₽ для мини-приложения (списание с баланса отключено)."""

from __future__ import annotations

import os

# Множитель для «пользовательских» ₽ в mini-app (cost_usd * курс * RUB_MULTIPLIER).
RUB_MULTIPLIER = 5


def usd_to_rub_rate() -> float:
    """Курс USD→RUB для отображения расходов в mini-app (<code>MINIAPP_USD_TO_RUB</code>)."""
    try:
        return float(os.getenv("MINIAPP_USD_TO_RUB", "100").strip())
    except (TypeError, ValueError):
        return 100.0


def miniapp_rub_per_usd() -> float:
    """₽ за 1 USD в интерфейсе расходов мини-приложения."""
    return usd_to_rub_rate() * RUB_MULTIPLIER


def charge_cost_usd(telegram_user_id: int, cost_usd: float) -> None:
    """Списание с баланса отключено; вызов оставлен для совместимости с usage_store."""
    del telegram_user_id, cost_usd


def billing_snapshot_for_api(telegram_user_id: int) -> dict:
    del telegram_user_id
    return {"balance_rub": 0, "plan": "free", "promo_available": False}


def apply_promo_code(telegram_user_id: int, code: str) -> tuple[bool, str]:
    del telegram_user_id, code
    return False, "Промокоды не настроены."
