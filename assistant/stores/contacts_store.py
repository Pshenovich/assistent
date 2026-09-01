"""Адресная книга пользователя (contacts_user/{telegram_id}.json). bot.py + miniapp API."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Literal

_lock = threading.Lock()
from assistant.config import ROOT

_HERE = Path(__file__).resolve().parent

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

LEGACY_CONTACTS_OWNER_USERNAME = "pshenovich"
# Telegram ID владельца старого общего contacts.json на VPS (см. deploy exclude).
LEGACY_CONTACTS_OWNER_TELEGRAM_ID = 106278723

_CONTACTS_CACHE_BY_PATH: dict[str, dict[str, Any]] = {}


def legacy_contacts_path() -> Path:
    """Старый общий файл контактов в корне проекта (/opt/assistant/contacts.json)."""
    raw = os.getenv("CONTACTS_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p
    return (ROOT / "contacts.json").resolve()


def _stores_legacy_contacts_json() -> Path:
    return (_HERE / "contacts.json").resolve()


def _is_legacy_shared_contacts_owner(
    uid: int, telegram_username: str | None
) -> bool:
    if int(uid) == LEGACY_CONTACTS_OWNER_TELEGRAM_ID:
        return True
    uname = normalize_telegram_username(telegram_username or "")
    return uname == normalize_telegram_username(LEGACY_CONTACTS_OWNER_USERNAME)


def normalize_telegram_username(s: str) -> str:
    u = str(s or "").strip().lstrip("@").lower()
    return re.sub(r"[^a-z0-9_]", "", u)


def contacts_user_primary_dir() -> Path:
    """Каталог contacts_user/{telegram_id}.json (корень проекта на VPS)."""
    raw = os.getenv("CONTACTS_USER_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
    else:
        p = (ROOT / "contacts_user").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _legacy_contacts_user_dir() -> Path:
    return (_HERE / "contacts_user").resolve()


def _backup_assistant_roots() -> list[Path]:
    """Снимки /opt/assistant.backup-* на VPS (если есть)."""
    opt = Path("/opt")
    if not opt.is_dir():
        return []
    return sorted(
        (p for p in opt.glob("assistant.backup-*") if p.is_dir()),
        reverse=True,
    )


def _backup_sources_for_user(
    uid: int, telegram_username: str | None
) -> list[Path]:
    out: list[Path] = []
    for root in _backup_assistant_roots():
        out.append(root / "contacts_user" / f"{uid}.json")
        out.append(
            root / "assistant" / "stores" / "contacts_user" / f"{uid}.json"
        )
        if _is_legacy_shared_contacts_owner(uid, telegram_username):
            out.append(root / "contacts.json")
            out.append(root / "assistant" / "stores" / "contacts.json")
    return out


def resolve_contacts_path(
    *, telegram_user_id: int | None, telegram_username: str | None = None
) -> Path:
    uid = int(telegram_user_id) if telegram_user_id is not None else 0
    return contacts_user_primary_dir() / f"{uid}.json"


def _migration_sources_for_user(
    uid: int, telegram_username: str | None
) -> list[Path]:
    """Старые пути, откуда один раз копируем в contacts_user/{uid}.json."""
    sources: list[Path] = _backup_sources_for_user(uid, telegram_username) + [
        _legacy_contacts_user_dir() / f"{uid}.json",
        _stores_legacy_contacts_json(),
    ]
    if _is_legacy_shared_contacts_owner(uid, telegram_username):
        root_json = legacy_contacts_path()
        if root_json not in sources:
            sources.insert(0, root_json)
    seen: set[str] = set()
    out: list[Path] = []
    for p in sources:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _primary_file_is_empty(path: Path) -> bool:
    if not path.is_file():
        return True
    try:
        raw = path.read_bytes()
        if not raw.strip() or raw.strip() in (b"[]", b"{}"):
            return True
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list) and not data:
            return True
        if isinstance(data, dict) and not data:
            return True
    except Exception:
        return True
    return False


def _migrate_contacts_to_primary(
    primary: Path, sources: list[Path], *, uid: int
) -> bool:
    """Копирует legacy → primary, если primary отсутствует или пуст. Возвращает True если скопировали."""
    if primary.is_file() and not _primary_file_is_empty(primary):
        return False
    for src in sources:
        if not src.is_file() or src.resolve() == primary.resolve():
            continue
        try:
            raw = src.read_bytes()
            if not raw.strip() or raw.strip() in (b"[]", b"{}"):
                continue
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_bytes(raw)
            print(f"[contacts_store] migrated uid={uid} from {src}")
            return True
        except OSError as e:
            print(f"[contacts_store] migrate uid={uid} from {src} err={e!r}")
    return False


def restore_contacts_from_legacy(
    *, telegram_user_id: int, telegram_username: str | None = None
) -> bool:
    """Принудительно восстановить контакты пользователя из legacy-файлов, если primary пуст."""
    path = resolve_contacts_path(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    invalidate_cache_for_user(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    return _migrate_contacts_to_primary(
        path,
        _migration_sources_for_user(int(telegram_user_id), telegram_username),
        uid=int(telegram_user_id),
    )


def restore_all_contacts_from_legacy() -> list[int]:
    """Сканирует legacy-каталоги и восстанавливает пустые primary-файлы. Возвращает uid с восстановлением."""
    restored: list[int] = []
    seen_uids: set[int] = set()
    primary_dir = contacts_user_primary_dir()

    def _try_uid(uid: int) -> None:
        if uid <= 0 or uid in seen_uids:
            return
        seen_uids.add(uid)
        if restore_contacts_from_legacy(telegram_user_id=uid):
            restored.append(uid)

    legacy_dir = _legacy_contacts_user_dir()
    if legacy_dir.is_dir():
        for p in legacy_dir.glob("*.json"):
            try:
                _try_uid(int(p.stem))
            except ValueError:
                continue

    for backup_root in _backup_assistant_roots():
        for sub in (
            backup_root / "contacts_user",
            backup_root / "assistant" / "stores" / "contacts_user",
        ):
            if not sub.is_dir():
                continue
            for p in sub.glob("*.json"):
                try:
                    _try_uid(int(p.stem))
                except ValueError:
                    continue
        for shared in (
            backup_root / "contacts.json",
            backup_root / "assistant" / "stores" / "contacts.json",
        ):
            if shared.is_file() and not _primary_file_is_empty(shared):
                _try_uid(LEGACY_CONTACTS_OWNER_TELEGRAM_ID)

    users_dir = (ROOT / "data" / "users").resolve()
    if users_dir.is_dir():
        for p in users_dir.glob("*.json"):
            try:
                uid = int(p.stem)
            except ValueError:
                continue
            primary = primary_dir / f"{uid}.json"
            if _primary_file_is_empty(primary):
                _try_uid(uid)

    if primary_dir.is_dir():
        for p in primary_dir.glob("*.json"):
            if p.name.endswith(".tmp"):
                continue
            try:
                uid = int(p.stem)
            except ValueError:
                continue
            if _primary_file_is_empty(p):
                _try_uid(uid)

    shared = _stores_legacy_contacts_json()
    if shared.is_file() and not _primary_file_is_empty(shared):
        _try_uid(LEGACY_CONTACTS_OWNER_TELEGRAM_ID)

    root_shared = legacy_contacts_path()
    if (
        root_shared.is_file()
        and root_shared.resolve() != shared.resolve()
        and not _primary_file_is_empty(root_shared)
    ):
        _try_uid(LEGACY_CONTACTS_OWNER_TELEGRAM_ID)

    return restored


def contact_display_name(c: dict[str, Any]) -> str:
    fn = str(c.get("first_name") or "").strip()
    ln = str(c.get("last_name") or "").strip()
    if fn or ln:
        return " ".join(p for p in (fn, ln) if p).strip()
    return str(c.get("name") or "").strip()


def _normalize_row(x: dict[str, Any]) -> dict[str, Any] | None:
    fn = str(x.get("first_name") or "").strip()
    ln = str(x.get("last_name") or "").strip()
    name = str(x.get("name") or "").strip()
    if not name and (fn or ln):
        name = " ".join(p for p in (fn, ln) if p).strip()
    email = str(x.get("email") or "").strip().lower()
    if not name or not email:
        return None
    aliases_raw = x.get("aliases") or []
    aliases = (
        [str(a).strip() for a in aliases_raw if str(a).strip()]
        if isinstance(aliases_raw, list)
        else []
    )
    tg_raw = x.get("telegram_username") or x.get("tg_username") or ""
    tg = normalize_telegram_username(str(tg_raw)) if str(tg_raw).strip() else ""
    row: dict[str, Any] = {"name": name, "email": email, "aliases": aliases}
    if fn:
        row["first_name"] = fn
    if ln:
        row["last_name"] = ln
    if tg:
        row["telegram_username"] = tg
    try:
        tgid = int(x.get("telegram_user_id") or 0)
        if tgid > 0:
            row["telegram_user_id"] = tgid
    except (TypeError, ValueError):
        pass
    return row


def load_contacts(
    *, telegram_user_id: int, telegram_username: str | None = None
) -> list[dict[str, Any]]:
    path = resolve_contacts_path(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    _migrate_contacts_to_primary(
        path,
        _migration_sources_for_user(
            int(telegram_user_id), telegram_username
        ),
        uid=int(telegram_user_id),
    )
    with _lock:
        cache = _CONTACTS_CACHE_BY_PATH.setdefault(str(path), {"items": [], "mtime": 0.0})
        if not path.exists():
            cache["items"] = []
            cache["mtime"] = 0.0
            return []
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if cache.get("items") and abs(float(cache.get("mtime") or 0.0) - mtime) < 1e-6:
            return list(cache["items"])
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else []
        except Exception as e:
            print(f"[contacts_store] read_failed={e!r}")
            return []
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            for x in data:
                if not isinstance(x, dict):
                    continue
                row = _normalize_row(x)
                if row:
                    items.append(row)
        cache["items"] = items
        cache["mtime"] = mtime
        return list(items)


def save_contacts(
    items: list[dict[str, Any]],
    *,
    telegram_user_id: int,
    telegram_username: str | None = None,
) -> None:
    path = resolve_contacts_path(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    normalized: list[dict[str, Any]] = []
    for x in items:
        if isinstance(x, dict):
            row = _normalize_row(x)
            if row:
                normalized.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with _lock:
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, path)
        cache = _CONTACTS_CACHE_BY_PATH.setdefault(str(path), {"items": [], "mtime": 0.0})
        try:
            cache["mtime"] = path.stat().st_mtime
        except OSError:
            cache["mtime"] = 0.0
        cache["items"] = list(normalized)


def invalidate_cache_for_user(
    *, telegram_user_id: int, telegram_username: str | None = None
) -> None:
    path = resolve_contacts_path(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    with _lock:
        _CONTACTS_CACHE_BY_PATH.pop(str(path), None)


def add_or_update_contact(
    contacts: list[dict[str, Any]],
    name: str,
    email: str,
    *,
    telegram_username: str | None = None,
) -> Literal["added", "updated", "existing"]:
    name_clean = (name or "").strip()
    email_clean = (email or "").strip().lower()
    if not name_clean or not email_clean or not EMAIL_RE.fullmatch(email_clean):
        return "existing"
    tg_clean = normalize_telegram_username(telegram_username or "") or None
    for c in contacts:
        if str(c.get("email", "")).strip().lower() == email_clean:
            existing_name = str(c.get("name") or "").strip()
            if tg_clean and not (c.get("telegram_username") or c.get("tg_username")):
                c["telegram_username"] = tg_clean
            if existing_name.lower() == name_clean.lower():
                return "existing"
            aliases = list(c.get("aliases") or [])
            if not any(str(a).strip().lower() == name_clean.lower() for a in aliases):
                aliases.append(name_clean)
                c["aliases"] = aliases
            return "updated"
    row: dict[str, Any] = {"name": name_clean, "email": email_clean, "aliases": []}
    if tg_clean:
        row["telegram_username"] = tg_clean
    contacts.append(row)
    return "added"


def delete_contact_by_email(
    contacts: list[dict[str, Any]], email: str
) -> bool:
    email_clean = (email or "").strip().lower()
    if not email_clean:
        return False
    before = len(contacts)
    contacts[:] = [
        c for c in contacts if str(c.get("email", "")).strip().lower() != email_clean
    ]
    return len(contacts) < before


def update_contact_by_email(
    contacts: list[dict[str, Any]],
    old_email: str,
    *,
    name: str | None = None,
    email: str | None = None,
    telegram_username: str | None = None,
    aliases: list[str] | None = None,
    clear_telegram: bool = False,
) -> dict[str, Any] | None:
    old_clean = (old_email or "").strip().lower()
    if not old_clean:
        return None
    idx = None
    for i, c in enumerate(contacts):
        if str(c.get("email", "")).strip().lower() == old_clean:
            idx = i
            break
    if idx is None:
        return None
    row = dict(contacts[idx])
    new_email = (email or row.get("email") or "").strip().lower()
    new_name = (name if name is not None else row.get("name") or "").strip()
    if not new_name or not new_email or not EMAIL_RE.fullmatch(new_email):
        return None
    if new_email != old_clean:
        for c in contacts:
            if (
                str(c.get("email", "")).strip().lower() == new_email
                and c is not contacts[idx]
            ):
                return None
    row["name"] = new_name
    row["email"] = new_email
    if aliases is not None:
        row["aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
    if clear_telegram:
        row.pop("telegram_username", None)
        row.pop("tg_username", None)
    elif telegram_username is not None:
        tg = normalize_telegram_username(telegram_username)
        if tg:
            row["telegram_username"] = tg
        else:
            row.pop("telegram_username", None)
            row.pop("tg_username", None)
    contacts[idx] = row
    if new_email != old_clean:
        contacts.sort(key=lambda x: str(x.get("email") or ""))
    return dict(row)


def create_contact_for_user(
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    name: str,
    email: str,
    telegram_username_contact: str | None = None,
    aliases: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Возвращает (item, status) status: added | updated | existing | invalid | duplicate."""
    items = load_contacts(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    email_clean = (email or "").strip().lower()
    if not EMAIL_RE.fullmatch(email_clean):
        return None, "invalid"
    st = add_or_update_contact(
        items,
        name,
        email_clean,
        telegram_username=telegram_username_contact,
    )
    if st == "existing" and aliases:
        for c in items:
            if str(c.get("email", "")).strip().lower() == email_clean:
                update_contact_by_email(
                    items,
                    email_clean,
                    aliases=list(c.get("aliases") or []) + list(aliases),
                )
                st = "updated"
                break
    save_contacts(
        items,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
    )
    for c in items:
        if str(c.get("email", "")).strip().lower() == email_clean:
            return dict(c), st
    return None, st


def delete_contact_for_user(
    *, telegram_user_id: int, telegram_username: str | None, email: str
) -> bool:
    items = load_contacts(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    if not delete_contact_by_email(items, email):
        return False
    save_contacts(
        items,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
    )
    return True


def update_contact_for_user(
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    old_email: str,
    name: str | None = None,
    email: str | None = None,
    telegram_username_contact: str | None = None,
    aliases: list[str] | None = None,
    clear_telegram: bool = False,
) -> dict[str, Any] | None:
    items = load_contacts(
        telegram_user_id=telegram_user_id, telegram_username=telegram_username
    )
    row = update_contact_by_email(
        items,
        old_email,
        name=name,
        email=email,
        telegram_username=telegram_username_contact,
        aliases=aliases,
        clear_telegram=clear_telegram,
    )
    if row is None:
        return None
    save_contacts(
        items,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
    )
    return row
