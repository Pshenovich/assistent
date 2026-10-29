"""Члены организации — в контактах друг у друга.

Каждому активному участнику дописываем остальных (имя, Telegram, рабочая почта), не трогая уже
существующие контакты. Что дописали мы, помним в ``org_contact_grants``: когда человек уходит из
организации, убираем только эти записи — личные контакты остаются.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]


def _conn():
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS org_contact_grants (
            owner_user_id TEXT NOT NULL,
            member_user_id TEXT NOT NULL,
            org_id INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, member_user_id, org_id)
        )
        """
    )
    return conn


def member_card(user_id: int, *, prefer_domains: list[str] | None = None) -> dict[str, Any]:
    """Как участник выглядит в чужих контактах: имя, @ник и почта для встреч.

    Почта: рабочая на домене организации, иначе — его Google-календаря (туда уходят приглашения).
    Ящики, подключённые для чтения писем, коллегам не показываем.
    """
    from assistant.stores import note_members, orgs_store

    prof = note_members.get_profile(user_id) or {}
    name = note_members.profile_display_name(prof, fallback="")
    username = str(prof.get("username") or "").strip().lstrip("@")
    emails = orgs_store.verified_emails(user_id)
    prefer = set(prefer_domains or [])
    email = next((e for e in emails if orgs_store.domain_of(e) in prefer), "")
    if not email:
        try:
            from assistant.stores import user_prefs

            cal = str(user_prefs.load_prefs(int(user_id)).get("google_account_email") or "").strip().lower()
            email = cal if "@" in cal else ""
        except Exception:
            email = ""
    if not name or name == "Участник":
        name = ("@" + username) if username else email
    return {"user_id": int(user_id), "name": name, "telegram_username": username, "email": email}


def _remove_contact(owner: int, member: int) -> bool:
    """Убрать из книги owner запись о member (по Telegram id)."""
    from assistant.stores import contacts_store

    items = contacts_store.load_contacts(telegram_user_id=owner)
    keep = []
    removed = False
    for c in items:
        try:
            same = int(c.get("telegram_user_id") or 0) == int(member)
        except (TypeError, ValueError):
            same = False
        if same:
            removed = True
            continue
        keep.append(c)
    if removed:
        contacts_store.save_contacts(keep, telegram_user_id=owner)
    return removed


def sync(org_id: int) -> dict[str, int]:
    """Привести контакты участников организации в порядок. Можно звать сколько угодно раз."""
    from assistant.stores import contacts_store, orgs_store

    org = orgs_store.get_org(org_id)
    members = orgs_store.member_ids(org_id) if org else []
    domains = (org or {}).get("domains") or []
    cards = {m: member_card(m, prefer_domains=domains) for m in members}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    added = removed = 0

    with _LOCK:
        granted = {(str(r["owner_user_id"]), str(r["member_user_id"])) for r in _conn().execute(
            "SELECT owner_user_id, member_user_id FROM org_contact_grants WHERE org_id = ?", (int(org_id),)
        ).fetchall()}
    for owner in members:
        for m in members:
            if m == owner or (str(owner), str(m)) in granted:
                continue  # уже добавляли; если человек удалил контакт сам — не возвращаем
            card = cards[m]
            if not card["name"] or not (card["email"] or card["telegram_username"]):
                continue  # без почты и ника контакт не сохранить
            st = contacts_store.ensure_contact_for_user_if_missing(
                telegram_user_id=owner,
                name=card["name"],
                email=card["email"],
                telegram_username_contact=card["telegram_username"] or None,
                peer_telegram_user_id=m,
            )
            if st == "added":
                added += 1
                with _LOCK:
                    conn = _conn()
                    conn.execute(
                        "INSERT OR IGNORE INTO org_contact_grants VALUES (?, ?, ?, ?)",
                        (str(owner), str(m), int(org_id), now),
                    )
                    conn.commit()

    # Кто больше не вместе в этой организации — убрать то, что добавляли мы.
    member_set = {str(m) for m in members}
    with _LOCK:
        rows = _conn().execute(
            "SELECT owner_user_id, member_user_id FROM org_contact_grants WHERE org_id = ?", (int(org_id),)
        ).fetchall()
    for r in rows:
        owner, m = str(r["owner_user_id"]), str(r["member_user_id"])
        if owner in member_set and m in member_set:
            continue
        with _LOCK:
            conn = _conn()
            conn.execute(
                "DELETE FROM org_contact_grants WHERE owner_user_id = ? AND member_user_id = ? AND org_id = ?",
                (owner, m, int(org_id)),
            )
            other = conn.execute(
                "SELECT 1 FROM org_contact_grants WHERE owner_user_id = ? AND member_user_id = ?", (owner, m)
            ).fetchone()
            conn.commit()
        if not other and _remove_contact(int(owner), int(m)):
            removed += 1
    return {"added": added, "removed": removed}


def granted_orgs(owner_user_id: int) -> dict[int, list[int]]:
    """member_user_id → организации, из-за которых он в контактах owner."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT member_user_id, org_id FROM org_contact_grants WHERE owner_user_id = ?", (str(int(owner_user_id)),)
        ).fetchall()
    out: dict[int, list[int]] = {}
    for r in rows:
        out.setdefault(int(r["member_user_id"]), []).append(int(r["org_id"]))
    return out
