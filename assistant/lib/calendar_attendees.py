"""Участники встречи: извлечение имён из текста и поиск в контактах."""

from __future__ import annotations

import re
from typing import Any

from assistant.stores import contacts_store as contacts
from assistant.stores import telegram_registry
from assistant.lib.name_equivalence import name_lookup_keys, names_equivalent
from assistant.stores.contacts_store import contact_display_name, normalize_telegram_username

_RE_MEETING_WITH = re.compile(
    r"(?:встреч\w*|созвон\w*|митинг\w*|событ\w*|переговор\w*)\s+с\s+"
    r"([А-Яа-яA-Za-z][А-Яа-яA-Za-z\-]{1,40})",
    re.IGNORECASE,
)
_RE_CALENDAR_PREFIX = re.compile(
    r"^(?:"
    r"постав(?:ить|ь)|добав(?:ить|ь)|созда(?:ть|й)|запланир(?:овать|уй)|назнач(?:ить|ь)"
    r")\s+"
    r"(?:(?:в|на)\s+)?"
    r"(?:календар(?:ь|е|я)|google\s*calendar)?\s*",
    re.IGNORECASE,
)
_RE_WITH_NAME = re.compile(
    r"(?<!\w)с\s+([А-Яа-я][а-яё]{2,30})(?=\s+(?:сегодня|завтра|послезавтра|в\s+\d|на\s+|\d{1,2}[:\.]))",
    re.IGNORECASE,
)
_RE_TG_USERNAME = re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{4,31})")
_SKIP_NAMES = frozenset(
    {
        "сегодня",
        "завтра",
        "послезавтра",
        "утром",
        "вечером",
        "днём",
        "днем",
        "мной",
        "тобой",
        "вами",
        "нами",
    }
)


def _norm_name(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def extract_telegram_usernames_from_text(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _RE_TG_USERNAME.finditer(text or ""):
        u = normalize_telegram_username(m.group(1) or "")
        if u and u not in seen:
            seen.add(u)
            out.append(f"@{u}")
    return out


def infer_event_title_from_text(text: str) -> str:
    """Название встречи из фразы вроде «Встреча с Мишей в понедельник в 18:00»."""
    raw = (text or "").strip()
    if not raw:
        return ""
    t = _RE_CALENDAR_PREFIX.sub("", raw).strip()
    m = _RE_MEETING_WITH.search(t)
    if m:
        title = (m.group(0) or "").strip()
        if title and title[0].islower():
            title = title[0].upper() + title[1:]
        return title
    return ""


def extract_attendee_names_from_text(text: str) -> list[str]:
    """Эвристика: «встреча с Гарей», «с Гарей сегодня в 11»."""
    out: list[str] = []
    seen: set[str] = set()
    for rx in (_RE_MEETING_WITH, _RE_WITH_NAME):
        for m in rx.finditer(text or ""):
            name = (m.group(1) or "").strip()
            key = _norm_name(name)
            if len(key) < 2 or key in _SKIP_NAMES or key in seen:
                continue
            seen.add(key)
            out.append(name)
    return out


def enrich_parsed_attendees(parsed: dict[str, Any], user_text: str) -> None:
    names = list(parsed.get("attendee_names") or [])
    seen = {_norm_name(n) for n in names}
    for n in extract_attendee_names_from_text(user_text):
        k = _norm_name(n)
        if k not in seen:
            seen.add(k)
            names.append(n)
    for u in extract_telegram_usernames_from_text(user_text):
        k = u.lower()
        if k not in seen:
            seen.add(k)
            names.append(u)
    if names:
        parsed["attendee_names"] = names


def names_match(needle: str, candidate: str) -> bool:
    if names_equivalent(needle, candidate):
        return True
    n = _norm_name(needle)
    c = _norm_name(candidate)
    if not n or not c:
        return False
    if n == c or n in c or c in n:
        return True
    if len(n) >= 3 and (c.startswith(n[:3]) or n.startswith(c[:3])):
        return True
    return False


def _contact_name_fields(contact: dict[str, Any]) -> list[str]:
    fields = [
        contact_display_name(contact),
        str(contact.get("name") or ""),
    ]
    fields.extend(str(a) for a in (contact.get("aliases") or []))
    tg = normalize_telegram_username(
        str(contact.get("telegram_username") or contact.get("tg_username") or "")
    )
    if tg:
        fields.append(f"@{tg}")
    return [f for f in fields if str(f).strip()]


def find_contact_by_name(
    user_id: int, name: str, *, telegram_username: str | None = None
) -> dict[str, Any] | None:
    needle = (name or "").strip()
    tg_needle = ""
    if needle.startswith("@"):
        tg_needle = normalize_telegram_username(needle)
    for c in contacts.load_contacts(
        telegram_user_id=user_id, telegram_username=telegram_username
    ):
        if tg_needle:
            ctg = normalize_telegram_username(
                str(c.get("telegram_username") or c.get("tg_username") or "")
            )
            if ctg and ctg == tg_needle:
                return c
        keys = name_lookup_keys(name)
        for field in _contact_name_fields(c):
            if keys & name_lookup_keys(field):
                return c
            if field and names_match(name, field):
                return c
    return None


def find_contact_by_telegram_user(
    user_id: int,
    *,
    telegram_user_id: int = 0,
    telegram_username: str | None = None,
    owner_telegram_username: str | None = None,
) -> dict[str, Any] | None:
    tg_id = int(telegram_user_id or 0)
    tg_un = normalize_telegram_username(telegram_username or "")
    for c in contacts.load_contacts(
        telegram_user_id=user_id, telegram_username=owner_telegram_username
    ):
        try:
            cid = int(c.get("telegram_user_id") or 0)
        except (TypeError, ValueError):
            cid = 0
        if tg_id and cid == tg_id:
            return c
        ctg = normalize_telegram_username(
            str(c.get("telegram_username") or c.get("tg_username") or "")
        )
        if tg_un and ctg and ctg == tg_un:
            return c
    return None


def resolve_attendee_names(
    user_id: int,
    names: list[str],
    *,
    telegram_username: str | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Возвращает (attendees для Google Calendar, имена без email в контактах)."""
    emails: list[dict[str, str]] = []
    missing: list[str] = []
    seen_emails: set[str] = set()
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        hit = find_contact_by_name(
            user_id, name, telegram_username=telegram_username
        )
        if not hit:
            missing.append(name)
            continue
        em = str(hit.get("email") or "").strip().lower()
        if not em or em in seen_emails:
            continue
        seen_emails.add(em)
        emails.append({"email": em})
    return emails, missing


def resolve_attendee_contacts(
    user_id: int,
    parsed: dict[str, Any],
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Контакты участников, найденные в адресной книге."""
    skip = {str(x).strip().lower() for x in (skip_names or set())}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    names = [
        str(n).strip()
        for n in (parsed.get("attendee_names") or [])
        if str(n).strip() and str(n).strip().lower() not in skip
    ]
    for raw in names:
        hit = find_contact_by_name(
            user_id, raw, telegram_username=telegram_username
        )
        if not hit:
            continue
        em = str(hit.get("email") or "").strip().lower()
        if em in seen:
            continue
        seen.add(em)
        out.append(hit)
    return out


def _append_calendar_user(
    out: list[tuple[int, str]],
    seen: set[int],
    *,
    owner_id: int,
    uid: int | None,
    label: str,
) -> None:
    from assistant.integrations import google_calendar_oauth

    if not uid or int(uid) == int(owner_id) or int(uid) in seen:
        return
    if not google_calendar_oauth.user_token_path(int(uid)).is_file():
        return
    seen.add(int(uid))
    out.append((int(uid), (label or "Участник").strip() or "Участник"))


def iter_attendees_for_calendar_busy_check(
    owner_id: int,
    parsed: dict[str, Any],
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> list[tuple[int, str]]:
    """Участники с подключённым Google Calendar (не организатор)."""
    from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

    skip = {str(x).strip().lower() for x in (skip_names or set())}
    out: list[tuple[int, str]] = []
    seen: set[int] = set()

    def _try_email(em: str, label: str) -> None:
        uid = lookup_user_id_by_calendar_email(em)
        _append_calendar_user(
            out, seen, owner_id=owner_id, uid=uid, label=label
        )

    for em in parsed.get("attendees") or []:
        e = str(em).strip()
        if e and "@" in e:
            _try_email(e, e)

    names = [
        str(n).strip()
        for n in (parsed.get("attendee_names") or [])
        if str(n).strip() and str(n).strip().lower() not in skip
    ]
    for raw in names:
        if raw.startswith("@"):
            uid = telegram_registry.lookup_user_id(raw)
            _append_calendar_user(
                out, seen, owner_id=owner_id, uid=uid, label=raw
            )
            continue
        hit = find_contact_by_name(
            owner_id, raw, telegram_username=telegram_username
        )
        if hit:
            label = contact_display_name(hit) or hit.get("name") or raw
            uid = attendee_calendar_user_id(hit)
            _append_calendar_user(
                out, seen, owner_id=owner_id, uid=uid, label=str(label)
            )
            em = str(hit.get("email") or "").strip()
            if em:
                _try_email(em, str(label))
            continue
        # Имя без контакта — не проверяем (нет привязки к боту)

    return out


def _attendee_was_calendar_checked(
    owner_id: int,
    raw: str,
    checked: list[tuple[int, str]],
    *,
    telegram_username: str | None = None,
) -> bool:
    """Участник из запроса уже попал в проверку freebusy (по uid или совпадению имени)."""
    from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

    name = (raw or "").strip()
    if not name:
        return False
    checked_uids = {int(uid) for uid, _ in checked}
    for _, lbl in checked:
        if names_match(name, str(lbl or "")):
            return True
    if name.startswith("@"):
        uid = telegram_registry.lookup_user_id(name)
        return bool(uid and int(uid) in checked_uids)
    hit = find_contact_by_name(
        owner_id, name, telegram_username=telegram_username
    )
    if not hit:
        return False
    for field in _contact_name_fields(hit) + [name]:
        for _, lbl in checked:
            if field and names_match(str(field), str(lbl or "")):
                return True
    uid = attendee_calendar_user_id(hit)
    if uid and int(uid) in checked_uids:
        return True
    em = str(hit.get("email") or "").strip()
    if em:
        uid_em = lookup_user_id_by_calendar_email(em)
        if uid_em and int(uid_em) in checked_uids:
            return True
    return False


def _calendar_user_ids_for_parsed_attendees(
    owner_id: int,
    parsed: dict[str, Any],
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> set[int]:
    """Все telegram_user_id участников, которых удалось связать с контактом/registry."""
    from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

    skip = {str(x).strip().lower() for x in (skip_names or set())}
    uids: set[int] = set()
    for raw in parsed.get("attendee_names") or []:
        name = str(raw).strip()
        if not name or _norm_name(name) in skip:
            continue
        if name.startswith("@"):
            uid = telegram_registry.lookup_user_id(name)
            if uid:
                uids.add(int(uid))
            continue
        hit = find_contact_by_name(
            owner_id, name, telegram_username=telegram_username
        )
        if not hit:
            continue
        uid = attendee_calendar_user_id(hit)
        if uid:
            uids.add(int(uid))
        em = str(hit.get("email") or "").strip()
        if em:
            uid_em = lookup_user_id_by_calendar_email(em)
            if uid_em:
                uids.add(int(uid_em))
    return uids


def attendee_names_missing_calendar_link(
    owner_id: int,
    parsed: dict[str, Any],
    checked: list[tuple[int, str]],
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> list[str]:
    """Имена из запроса, для которых нет календаря бота (не удалось проверить занятость)."""
    skip = {str(x).strip().lower() for x in (skip_names or set())}
    checked_uids = {int(uid) for uid, _ in checked}
    linked_uids = _calendar_user_ids_for_parsed_attendees(
        owner_id,
        parsed,
        telegram_username=telegram_username,
        skip_names=skip_names,
    )
    missing: list[str] = []
    seen_request: set[str] = set()
    seen_label: set[str] = set()
    for raw in parsed.get("attendee_names") or []:
        name = str(raw).strip()
        key = _norm_name(name)
        if not name or key in skip or key in seen_request:
            continue
        seen_request.add(key)
        if _attendee_was_calendar_checked(
            owner_id, name, checked, telegram_username=telegram_username
        ):
            continue
        hit_pre = find_contact_by_name(
            owner_id, name, telegram_username=telegram_username
        )
        if hit_pre:
            uid_pre = attendee_calendar_user_id(hit_pre)
            em_pre = str(hit_pre.get("email") or "").strip()
            if uid_pre and int(uid_pre) in checked_uids:
                continue
            if em_pre:
                from assistant.lib.calendar_user_lookup import (
                    lookup_user_id_by_calendar_email,
                )

                uid_em = lookup_user_id_by_calendar_email(em_pre)
                if uid_em and int(uid_em) in checked_uids:
                    continue
        if hit_pre and linked_uids & checked_uids:
            continue
        if name.startswith("@"):
            label = name
        else:
            hit = find_contact_by_name(
                owner_id, name, telegram_username=telegram_username
            )
            label = (contact_display_name(hit) or name) if hit else name
        lk = _norm_name(label)
        if lk in seen_label:
            continue
        seen_label.add(lk)
        missing.append(label)
    return missing


def attendee_calendar_user_id(contact: dict[str, Any]) -> int | None:
    try:
        cid = int(contact.get("telegram_user_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid > 0:
        return cid
    tg = normalize_telegram_username(
        str(contact.get("telegram_username") or contact.get("tg_username") or "")
    )
    if tg:
        return telegram_registry.lookup_user_id(tg)
    return None
