"""Apple Reminders отключены в этой сборке."""


def peek_setup_telegram_user_id(_state: str) -> int | None:
    return None


def apply_setup_form(*_a, **_k) -> tuple[bool, str]:
    return False, "Apple Reminders не подключены в этой версии ассистента."
