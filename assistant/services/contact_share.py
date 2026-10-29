"""«Поделиться контактом»: отправить свой контакт другим пользователям Орбиты из своих контактов.

Получателю контакт сразу записывается в контакты (если такого человека у него ещё нет),
а бот пишет, кто и чем поделился.
"""

from __future__ import annotations

from typing import Any, Optional

from assistant.stores import contacts_store


def contact_user_id(row: dict[str, Any]) -> Optional[int]:
    """Пользователь Орбиты за контактом: Telegram id, @ник из реестра или почта Google-календаря."""
    try:
        tgid = int(row.get("telegram_user_id") or 0)
    except (TypeError, ValueError):
        tgid = 0
    if tgid > 0:
        return tgid
    nick = contacts_store.normalize_telegram_username(str(row.get("telegram_username") or ""))
    if nick:
        from assistant.stores import telegram_registry

        found = telegram_registry.lookup_user_id(nick)
        if found:
            return int(found)
    email = str(row.get("email") or "").strip().lower()
    if email:
        try:
            from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

            found = lookup_user_id_by_calendar_email(email)
            if found:
                return int(found)
        except Exception:
            pass
    return None


def _find(items: list[dict[str, Any]], key: str) -> Optional[dict[str, Any]]:
    norm = contacts_store.normalize_contact_key(key)
    return next((c for c in items if contacts_store.contact_key(c) == norm), None)


def targets(owner_id: int, key: str) -> list[dict[str, Any]]:
    """Кому из своих контактов можно отправить контакт ``key``."""
    items = contacts_store.load_contacts(telegram_user_id=int(owner_id))
    shared = _find(items, key)
    if not shared:
        raise ValueError("Контакт не найден")
    shared_uid = contact_user_id(shared)
    out = []
    for c in items:
        ck = contacts_store.contact_key(c)
        if not ck or c is shared:
            continue
        uid = contact_user_id(c)
        if uid and (uid == int(owner_id) or (shared_uid and uid == shared_uid)):
            continue
        nick = contacts_store.normalize_telegram_username(str(c.get("telegram_username") or ""))
        out.append({
            "key": ck,
            "name": contacts_store.contact_display_name(c) or ck,
            "sub": ("@" + nick) if nick else str(c.get("email") or ""),
            "can_receive": bool(uid),
        })
    out.sort(key=lambda t: (not t["can_receive"], t["name"].lower()))
    return out


def share(owner_id: int, sender_name: str, key: str, to_keys: list[str]) -> list[dict[str, Any]]:
    """Отправить контакт. Статусы: added — записан получателю, existing — у него уже был, not_in_orbita."""
    from assistant.bot import access_gate

    items = contacts_store.load_contacts(telegram_user_id=int(owner_id))
    shared = _find(items, key)
    if not shared:
        raise ValueError("Контакт не найден")
    name = contacts_store.contact_display_name(shared) or contacts_store.contact_key(shared)
    email = str(shared.get("email") or "")
    nick = contacts_store.normalize_telegram_username(str(shared.get("telegram_username") or ""))
    shared_uid = contact_user_id(shared)
    lines = [name] + ([email] if email else []) + (["@" + nick] if nick else [])
    results = []
    for tk in to_keys[:50]:
        target = _find(items, tk)
        if not target:
            results.append({"key": tk, "status": "not_found"})
            continue
        uid = contact_user_id(target)
        if not uid or uid == int(owner_id):
            results.append({"key": tk, "status": "not_in_orbita"})
            continue
        st = contacts_store.ensure_contact_for_user_if_missing(
            telegram_user_id=uid,
            name=name,
            email=email,
            telegram_username_contact=nick or None,
            peer_telegram_user_id=shared_uid,
        )
        if st == "invalid":
            results.append({"key": tk, "status": "invalid"})
            continue
        tail = "Он уже в ваших контактах Орбиты." if st == "added" else "Этот человек у вас в контактах уже был."
        access_gate.notify_user_sync(uid, f"{sender_name} поделился контактом:\n" + "\n".join(lines) + "\n\n" + tail)
        results.append({"key": tk, "status": st})
    return results
