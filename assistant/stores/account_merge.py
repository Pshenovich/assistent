"""Склейка двух аккаунтов одного человека: всё из ``src`` переезжает в ``dst``, ``src`` становится псевдонимом.

Зачем: человек когда-то писал боту (аккаунт = Telegram id), потом вошёл по почте — завёлся второй
аккаунт с синтетическим id. Задачи, документы и доступы расходятся по двум аккаунтам, в списках
он виден дважды. Раньше склеивали только пустой почтовый аккаунт; теперь переносится всё.

Правила:
- присоединяемый (``src``) — всегда аккаунт по почте (синтетический id ≥ 5·10^15): такой номер
  длинный и уникальный, поэтому его можно искать во ВСЕХ колонках всех баз (и внутри JSON/HTML),
  не перечисляя таблицы руками и не задев чужие данные;
- основной (``dst``) — старый аккаунт из Telegram, а если оба по почте — более ранний;
- при конфликте уникальных ключей побеждает основной; исключения — роль в организации (берём
  старшую), кредиты (складываем), профиль (дополняем пустые поля), теги (ссылки — на тег основного);
- до изменений всё, что касалось ``src``, сохраняется в data/account_merges/<время>_<src>_to_<dst>/;
- почта ``src`` остаётся входом: она основная у ``dst`` или запасная (account_emails);
- сессии и ссылки на ``src`` ведут в ``dst`` (account_aliases);
- не склеиваем: два разных Telegram, два аккаунта из Telegram, сейф у присоединяемого (ключи
  шифрования привязаны к человеку — такую пару переносим вручную).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from assistant.stores import accounts
from assistant.stores.accounts import AccountError

_MERGE_LOCK = threading.Lock()

# Таблицы, которые склейка ведёт сама (или которые нельзя трогать).
_SKIP_TABLES = {"accounts", "account_aliases", "account_emails", "email_codes", "tg_link_tokens"}
_SKIP_TABLE_RE = re.compile(r"^(sqlite_|_litestream_|_seq_bootstrap)")
_ROLE_RANK = {"owner": 3, "admin": 2, "member": 1}
_DB_ENV = (
    "ANALYTICS", "BOARD", "CALENDAR_MIRROR", "CALENDAR_TASKS", "CHAT_DIGEST", "CHAT_THREADS", "CREDITS",
    "GCAL_PUSH", "KNOWLEDGE", "MEETING_RECORDINGS", "NOTES", "NOTE_VERSIONS", "NOTE_YDOC",
    "PUSH_SUBSCRIPTIONS", "REMINDERS", "SEMANTIC", "SITE_CHAT", "USAGE", "USER_AGENTS",
)
_TOKEN_MODULES = (
    "google_calendar_oauth", "zoom_oauth", "todoist_oauth", "yandex_disk_oauth", "bitrix_mcp_token", "telemost_oauth",
)


class MergeError(AccountError):
    pass


# ---------------------------------------------------------------- пути


def _root() -> Path:
    """Корень данных — папка основной базы (на проде /opt/assistant, в тестах — песочница)."""
    from assistant.stores import notes as notes_store

    return Path(notes_store._db_path()).resolve().parent  # type: ignore[attr-defined]


def db_paths() -> list[Path]:
    """Все базы Орбиты: из *_DB_PATH, notes.sqlite и data/*.sqlite (без повторов)."""
    from assistant.stores import notes as notes_store

    root = _root()
    cands: list[Path] = [Path(notes_store._db_path())]  # type: ignore[attr-defined]
    for name in _DB_ENV:
        raw = (os.getenv(name + "_DB_PATH") or "").strip()
        if raw:
            p = Path(raw).expanduser()
            cands.append(p if p.is_absolute() else root / p)
    cands += [root / "assistant/lib/usage.sqlite"]
    for d in (root, root / "data"):
        if d.is_dir():
            cands += sorted(d.glob("*.sqlite"))
    out: list[Path] = []
    seen: set[str] = set()
    for p in cands:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if str(rp) in seen or not rp.is_file() or rp.stat().st_size == 0:
            continue
        seen.add(str(rp))
        out.append(rp)
    return out


def _token_dirs() -> list[Path]:
    import importlib

    out = []
    for m in _TOKEN_MODULES:
        try:
            mod = importlib.import_module("assistant.integrations." + m)
            out.append(Path(mod._tokens_dir()))  # type: ignore[attr-defined]
        except Exception as e:
            print(f"[account_merge] token dir {m} err={e!r}")
    return out


def _prefs_dir() -> Path:
    from assistant.stores import user_prefs

    return Path(user_prefs._dir())  # type: ignore[attr-defined]


def _contacts_dirs() -> list[Path]:
    from assistant.stores import contacts_store

    out = [contacts_store.contacts_user_primary_dir()]
    try:
        out.append(contacts_store._legacy_contacts_user_dir())  # type: ignore[attr-defined]
    except Exception:
        pass
    return [d for d in dict.fromkeys(out) if d.is_dir()]


def _misc_user_dirs() -> list[Path]:
    """Файлы <uid>.json, где побеждает основной: записи Zoom."""
    return [d for d in (_root() / "data" / "zoom_recent_meetings",) if d.is_dir()]


def _mail_attachments_dir() -> Optional[Path]:
    from assistant.stores import notes as notes_store

    d = Path(notes_store._db_path()).parent / "mail_attachments"  # type: ignore[attr-defined]
    return d if d.is_dir() else None


def _backup_root() -> Path:
    return _root() / "data" / "account_merges"


# ---------------------------------------------------------------- кто куда


def _created_at(uid: int) -> int:
    acc = accounts.get(uid)
    return int(acc["created_at"] or 0) if acc else 0


def direction(a: int | str, b: int | str) -> tuple[int, int]:
    """(присоединяемый, основной) для пары аккаунтов одного человека."""
    a, b = _live(int(a)), _live(int(b))
    if a == b:
        raise MergeError("Это один и тот же аккаунт.")
    sa, sb = accounts.is_synthetic(a), accounts.is_synthetic(b)
    if not sa and not sb:
        raise MergeError("Оба аккаунта — из Telegram (два разных Telegram). Такие пока не склеиваем.")
    if sa and not sb:
        return a, b
    if sb and not sa:
        return b, a
    ca, cb = _created_at(a), _created_at(b)
    return (b, a) if (ca, a) <= (cb, b) else (a, b)


def _live(uid: int) -> int:
    """Если id уже склеен — куда он ведёт сейчас."""
    seen = set()
    while uid not in seen:
        seen.add(uid)
        nxt = accounts.resolve_alias(uid)
        if not nxt:
            break
        uid = int(nxt)
    return uid


def _tg_of(uid: int) -> Optional[int]:
    acc = accounts.get(uid)
    if acc:
        return acc["tg_user_id"]
    return None if accounts.is_synthetic(uid) else uid


def _has_vault(uid: int) -> bool:
    from assistant.stores import notes as notes_store

    try:
        with accounts._LOCK:  # type: ignore[attr-defined]
            conn = notes_store._conn()  # type: ignore[attr-defined]
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_user_keys'").fetchone():
                return False
            return bool(conn.execute("SELECT 1 FROM vault_user_keys WHERE user_id = ?", (str(uid),)).fetchone())
    except Exception as e:
        print(f"[account_merge] vault check uid={uid} err={e!r}")
        return True  # не смогли проверить — не рискуем


def has_own_data(uid: int) -> bool:
    """У аккаунта есть что-то своё (документы, задачи, организации, доступы), а не только профиль."""
    if accounts._has_data(uid):  # type: ignore[attr-defined]
        return True
    checks = [
        ("calendar_tasks", "SELECT 1 FROM calendar_tasks WHERE owner_user_id = ? OR assignee_user_id = ? LIMIT 1", 2),
        ("org_members", "SELECT 1 FROM org_members WHERE user_id = ? LIMIT 1", 1),
        ("note_members", "SELECT 1 FROM note_members WHERE member_user_id = ? LIMIT 1", 1),
        ("chat_members", "SELECT 1 FROM chat_members WHERE member_user_id = ? LIMIT 1", 1),
    ]
    for path in db_paths():
        try:
            c = _connect(path)
            try:
                have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                for t, sql, n in checks:
                    if t in have and c.execute(sql, (str(uid),) * n).fetchone():
                        return True
            finally:
                c.close()
        except sqlite3.Error:
            continue
    return False


def _tail_problems(src: int) -> list[str]:
    if _has_vault(src):
        return ["Под старым id остались сейфы: ключи шифрования переносим вручную, напишите разработчику."]
    return []


def problems(src: int, dst: int) -> list[str]:
    """Почему эту пару нельзя склеить (пусто — можно)."""
    out: list[str] = []
    if src == dst:
        return ["Это один и тот же аккаунт."]
    if not accounts.is_synthetic(src):
        out.append("Присоединять можно только аккаунт по почте.")
    if not accounts.get(src):
        out.append("Аккаунт по почте не найден (возможно, уже склеен).")
    if accounts.resolve_alias(dst):
        out.append("Основной аккаунт сам уже склеен с другим.")
    if accounts.is_synthetic(dst) and not accounts.get(dst):
        out.append("Основной аккаунт не найден.")
    st, dt = _tg_of(src), _tg_of(dst)
    if st and dt and st != dt:
        out.append("У аккаунтов разные Telegram — это могут быть разные люди. Такие пары не склеиваем.")
    if _has_vault(src):
        out.append("В аккаунте по почте настроены сейфы: ключи шифрования переносим вручную, напишите разработчику.")
    return out


# ---------------------------------------------------------------- базы


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 30000")
    return c


def _tables(c: sqlite3.Connection) -> list[tuple[str, bool, bool]]:
    """(таблица, виртуальная, есть rowid) — без служебных и теневых таблиц FTS."""
    rows = c.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = {r["name"] for r in rows if str(r["sql"] or "").upper().startswith("CREATE VIRTUAL")}
    out = []
    for r in rows:
        n = str(r["name"])
        if n in _SKIP_TABLES or _SKIP_TABLE_RE.match(n):
            continue
        if any(n != v and n.startswith(v + "_") for v in virtual):
            continue  # теневые таблицы FTS
        out.append((n, n in virtual, "WITHOUT ROWID" not in str(r["sql"] or "").upper()))
    return out


def _columns(c: sqlite3.Connection, t: str) -> list[tuple[str, str]]:
    return [(str(r[1]), str(r[2] or "").upper()) for r in c.execute(f"PRAGMA table_info({_q(t)})")]


def _is_text(decl: str) -> bool:
    return not any(x in decl for x in ("INT", "REAL", "FLOA", "DOUB", "BLOB", "NUM"))


def _id_value(decl: str, uid: int) -> Any:
    return uid if "INT" in decl else str(uid)


def _sub(text: str, src: int, dst: int) -> str:
    return re.sub(r"(?<!\d)" + str(src) + r"(?!\d)", str(dst), text)


def _scan(c: sqlite3.Connection, src: int) -> dict[str, dict[str, Any]]:
    """Где в базе встречается src: {таблица: {"exact": {колонка: n}, "text": {колонка: n}, "rows": [...]}}.

    Один проход по таблице: все колонки проверяются в одном запросе, разбор — здесь."""
    s = str(src)
    out: dict[str, dict[str, Any]] = {}
    for t, virtual, has_rowid in _tables(c):
        cols = _columns(c, t)
        conds: list[str] = []
        args: list[Any] = []
        for col, decl in cols:
            conds.append(f"{_q(col)} IN (?, ?)")
            args += [s, src]
            if not virtual and has_rowid and _is_text(decl):
                conds.append(f"(typeof({_q(col)}) = 'text' AND instr({_q(col)}, ?) > 0)")
                args.append(s)
        if not conds:
            continue
        sel = "rowid AS _rowid_, *" if has_rowid and not virtual else "*"
        try:
            rows = [dict(r) for r in c.execute(f"SELECT {sel} FROM {_q(t)} WHERE " + " OR ".join(conds), args)]
        except sqlite3.OperationalError as e:
            if "no such" in str(e).lower():
                continue
            raise
        if not rows:
            continue
        exact: dict[str, int] = {}
        text: dict[str, int] = {}
        for r in rows:
            for col, decl in cols:
                v = r.get(col)
                if v is None:
                    continue
                if str(v) == s:
                    exact[col] = exact.get(col, 0) + 1
                elif (isinstance(v, str) and s in v and not virtual and has_rowid and _is_text(decl)
                      and re.search(r"(?<!\d)" + s + r"(?!\d)", v)):
                    text[col] = text.get(col, 0) + 1
        if exact or text:
            out[t] = {"exact": exact, "text": text, "rows": rows, "virtual": virtual, "rowid": has_rowid}
    return out


def _json_safe(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"_bytes_len": len(v)}
    return v


# Особые случаи конфликтов: вызываются до общего переноса, на одном соединении в транзакции.


def _fix_org_members(c: sqlite3.Connection, src: int, dst: int) -> None:
    for r in c.execute("SELECT id, org_id, role FROM org_members WHERE user_id = ?", (str(src),)).fetchall():
        d = c.execute("SELECT id, role FROM org_members WHERE org_id = ? AND user_id = ?", (r["org_id"], str(dst))).fetchone()
        if d and _ROLE_RANK.get(str(r["role"]), 0) > _ROLE_RANK.get(str(d["role"]), 0):
            c.execute("UPDATE org_members SET role = ? WHERE id = ?", (r["role"], d["id"]))


def _fix_user_profiles(c: sqlite3.Connection, src: int, dst: int) -> None:
    s = c.execute("SELECT * FROM user_profiles WHERE user_id = ?", (src,)).fetchone()
    d = c.execute("SELECT * FROM user_profiles WHERE user_id = ?", (dst,)).fetchone()
    if not s or not d:
        return
    fill = {k: s[k] for k in s.keys() if k != "user_id" and s[k] not in (None, "") and d[k] in (None, "")}
    if fill:
        sets = ", ".join(f"{_q(k)} = ?" for k in fill)
        c.execute(f"UPDATE user_profiles SET {sets} WHERE user_id = ?", (*fill.values(), dst))


def _fix_credit_accounts(c: sqlite3.Connection, src: int, dst: int) -> None:
    """Баланс складываем, но стартовый подарок — один на человека: подарок второго аккаунта списываем."""
    s = c.execute("SELECT balance FROM credit_accounts WHERE kind = 'user' AND owner_id = ?", (str(src),)).fetchone()
    if not s or not c.execute("SELECT 1 FROM credit_accounts WHERE kind = 'user' AND owner_id = ?", (str(dst),)).fetchone():
        return
    start = c.execute(
        "SELECT COALESCE(SUM(delta), 0) FROM credit_ledger WHERE kind = 'user' AND owner_id = ? AND reason = 'start'",
        (str(src),),
    ).fetchone()[0]
    start = float(start or 0)
    now = time.time()
    c.execute(
        "UPDATE credit_accounts SET balance = balance + ?, updated_at = ? WHERE kind = 'user' AND owner_id = ?",
        (float(s["balance"] or 0) - start, now, str(dst)),
    )
    if start:
        c.execute(
            "INSERT INTO credit_ledger(ts, day, kind, owner_id, user_id, delta, reason) VALUES (?, ?, 'user', ?, NULL, ?, ?)",
            (now, datetime.now(timezone.utc).date().isoformat(), str(dst), -start, "merge_start_dup"),
        )


def _fix_user_day_spend(c: sqlite3.Connection, src: int, dst: int) -> None:
    for r in c.execute("SELECT day, credits FROM user_day_spend WHERE user_id = ?", (str(src),)).fetchall():
        c.execute(
            "UPDATE user_day_spend SET credits = credits + ? WHERE user_id = ? AND day = ?",
            (float(r["credits"] or 0), str(dst), r["day"]),
        )


def _fix_tags(table: str, link_table: str, link_col: str) -> Callable[[sqlite3.Connection, int, int], None]:
    def fix(c: sqlite3.Connection, src: int, dst: int) -> None:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (link_table,)).fetchone():
            return
        for r in c.execute(f"SELECT id, name FROM {table} WHERE user_id = ?", (str(src),)).fetchall():
            d = c.execute(f"SELECT id FROM {table} WHERE user_id = ? AND name = ?", (str(dst), r["name"])).fetchone()
            if d:  # одноимённый тег у основного: ссылки — на него
                c.execute(f"UPDATE OR IGNORE {link_table} SET {link_col} = ?, user_id = ? WHERE {link_col} = ?",
                          (d["id"], str(dst), r["id"]))
                c.execute(f"DELETE FROM {link_table} WHERE {link_col} = ?", (r["id"],))
                c.execute(f"DELETE FROM {table} WHERE id = ?", (r["id"],))

    return fix


_FIXES: dict[str, Callable[[sqlite3.Connection, int, int], None]] = {
    "org_members": _fix_org_members,
    "user_profiles": _fix_user_profiles,
    "credit_accounts": _fix_credit_accounts,
    "user_day_spend": _fix_user_day_spend,
    "tags": _fix_tags("tags", "tag_links", "tag_id"),
    "hashtags": _fix_tags("hashtags", "hashtag_links", "hashtag_id"),
}
# Номер внутри этих колонок не меняем: channel_id Google присылает обратно, строка с новым id не найдётся.
_NO_TEXT_COLS = {("gcal_channels", "channel_id")}
# После переноса человек может оказаться «участником собственного»: такие строки убираем.
_SELF_ROWS = {
    "note_members": ("owner_user_id", "member_user_id"),
    "chat_members": ("owner_user_id", "member_user_id"),
    "org_contact_grants": ("owner_user_id", "member_user_id"),
}


def _move_db(c: sqlite3.Connection, src: int, dst: int, found: dict[str, dict[str, Any]], stats: dict[str, Any]) -> None:
    s = str(src)
    c.execute("BEGIN IMMEDIATE")
    try:
        for t, info in found.items():
            fix = _FIXES.get(t)
            if fix and info["exact"]:
                fix(c, src, dst)
            cols = dict(_columns(c, t))
            moved = dropped = texted = 0
            for col in info["exact"]:
                decl = cols.get(col, "")
                cur = c.execute(f"UPDATE OR IGNORE {_q(t)} SET {_q(col)} = ? WHERE {_q(col)} IN (?, ?)",
                                (_id_value(decl, dst), s, src))
                moved += max(cur.rowcount, 0)
                # Остались только конфликты уникального ключа: у основного такая строка уже есть.
                cur = c.execute(f"DELETE FROM {_q(t)} WHERE {_q(col)} IN (?, ?)", (s, src))
                dropped += max(cur.rowcount, 0)
            if info["text"] and info["rowid"] and not info["virtual"]:
                for col in info["text"]:
                    if (t, col) in _NO_TEXT_COLS:
                        continue
                    rows = c.execute(
                        f"SELECT rowid AS _rid, {_q(col)} AS v FROM {_q(t)} WHERE typeof({_q(col)}) = 'text' "
                        f"AND {_q(col)} != ? AND instr({_q(col)}, ?) > 0", (s, s)
                    ).fetchall()
                    for r in rows:
                        nv = _sub(str(r["v"]), src, dst)
                        if nv != r["v"]:
                            cur = c.execute(f"UPDATE OR IGNORE {_q(t)} SET {_q(col)} = ? WHERE rowid = ?", (nv, r["_rid"]))
                            texted += max(cur.rowcount, 0)
            if t in _SELF_ROWS:
                a, b = _SELF_ROWS[t]
                c.execute(f"DELETE FROM {_q(t)} WHERE {_q(a)} = {_q(b)}")
            st = stats.setdefault(t, {"moved": 0, "dropped": 0, "text": 0})
            st["moved"] += moved
            st["dropped"] += dropped
            st["text"] += texted
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------- файлы


def _deep_union(main: Any, extra: Any) -> Any:
    if isinstance(main, dict) and isinstance(extra, dict):
        out = dict(main)
        for k, v in extra.items():
            out[k] = _deep_union(main[k], v) if k in main else v
        return out
    if isinstance(main, list) and isinstance(extra, list):
        return main + [x for x in extra if x not in main]
    return main


def _user_files(d: Path, uid: int) -> list[Path]:
    pref = str(uid)
    return [p for p in sorted(d.iterdir()) if p.name == pref or re.match(re.escape(pref) + r"(\.|_)", p.name)]


def _file_plan(src: int, dst: int) -> list[tuple[str, Path, Path]]:
    """(действие, откуда, куда): union — объединить JSON, move — перенести, если у основного нет."""
    out: list[tuple[str, Path, Path]] = []
    s, d = str(src), str(dst)

    def target(p: Path) -> Path:
        return p.with_name(d + p.name[len(s):])

    try:
        pd = _prefs_dir()
        if pd.is_dir():
            for p in _user_files(pd, src):
                out.append(("union" if p.suffix == ".json" else "move", p, target(p)))
    except Exception as e:
        print(f"[account_merge] prefs dir err={e!r}")
    for cd in _contacts_dirs():
        for p in _user_files(cd, src):
            out.append(("union" if p.suffix == ".json" else "move", p, target(p)))
    for td in _token_dirs() + _misc_user_dirs():
        if td.is_dir():
            for p in _user_files(td, src):
                out.append(("move", p, target(p)))
    ma = _mail_attachments_dir()
    if ma and (ma / s).is_dir():
        out.append(("tree", ma / s, ma / d))
    return out


def _json_files_mentioning(src: int) -> list[Path]:
    """JSON-состояние в data/ (реестры, «уже уведомлён» и т. п.), где встречается src."""
    s = str(src)
    out = []
    dirs = [_root() / "data"]
    try:
        dirs.append(_prefs_dir())
    except Exception:
        pass
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                if p.stat().st_size < 20_000_000 and s in p.read_text(encoding="utf-8", errors="ignore"):
                    out.append(p)
            except OSError:
                continue
    return out


def _apply_files(src: int, dst: int, backup: Path, report: list[str]) -> None:
    fb = backup / "files"
    for action, p, t in _file_plan(src, dst):
        try:
            if not p.exists():
                continue
            rel = p.parent.name + "/" + p.name
            (fb / p.parent.name).mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                shutil.copytree(p, fb / rel, dirs_exist_ok=True)
            else:
                shutil.copy2(p, fb / rel)
            if action == "tree":
                t.mkdir(parents=True, exist_ok=True)
                for f in p.rglob("*"):
                    if f.is_file():
                        to = t / f.relative_to(p)
                        if not to.exists():
                            to.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(f), str(to))
                shutil.rmtree(p, ignore_errors=True)
                report.append(f"{rel}: вложения перенесены")
            elif action == "union" and t.exists():
                try:
                    main = json.loads(t.read_text(encoding="utf-8"))
                    extra = json.loads(_sub(p.read_text(encoding="utf-8"), src, dst))
                    t.write_text(json.dumps(_deep_union(main, extra), ensure_ascii=False, indent=2), encoding="utf-8")
                    p.unlink()
                    report.append(f"{rel}: объединён с основным")
                except ValueError:
                    report.append(f"{rel}: не JSON — оставлен в копии, у основного свой")
                    p.unlink()
            elif not t.exists():
                shutil.move(str(p), str(t))
                report.append(f"{rel}: перенесён")
            else:
                p.unlink()
                report.append(f"{rel}: у основного уже есть — оставлен основной (копия в резерве)")
        except Exception as e:
            report.append(f"{p}: ошибка {e!r}")
    for p in _json_files_mentioning(src):
        try:
            raw = p.read_text(encoding="utf-8")
            (fb / "json").mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, fb / "json" / p.name)
            new = _sub(raw, src, dst)
            if new != raw:
                try:
                    json.loads(new)
                except ValueError:
                    report.append(f"{p.name}: после замены не JSON — не трогали")
                    continue
                tmp = p.with_suffix(p.suffix + ".merge-tmp")
                tmp.write_text(new, encoding="utf-8")
                os.replace(tmp, p)
                report.append(f"{p.name}: id заменён")
        except Exception as e:
            report.append(f"{p.name}: ошибка {e!r}")
    try:
        from assistant.stores import user_prefs

        user_prefs._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- аккаунт (почта, Telegram, псевдоним)


def _switch_account(src: int, dst: int) -> dict[str, Any]:
    sa = accounts.get(src)
    if not sa:
        raise MergeError("Аккаунт по почте не найден.")
    da = accounts.get(dst)
    now = int(time.time())
    tg = (da or {}).get("tg_user_id") or sa["tg_user_id"] or (None if accounts.is_synthetic(dst) else dst)
    with accounts._LOCK:  # type: ignore[attr-defined]
        conn = accounts._conn()  # type: ignore[attr-defined]
        conn.execute("DELETE FROM accounts WHERE user_id = ?", (str(src),))
        if da:
            conn.execute("UPDATE accounts SET tg_user_id = ? WHERE user_id = ?", (str(tg) if tg else None, str(dst)))
            conn.execute(
                "INSERT OR REPLACE INTO account_emails (email, user_id, created_at) VALUES (?, ?, ?)",
                (sa["email"], str(dst), now),
            )
        else:
            conn.execute(
                "INSERT INTO accounts (user_id, email, email_verified_at, tg_user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(dst), sa["email"], sa["email_verified_at"] or now, str(tg) if tg else None, now),
            )
        conn.execute("UPDATE account_emails SET user_id = ? WHERE user_id = ?", (str(dst), str(src)))
        conn.execute(
            "INSERT OR REPLACE INTO account_aliases (old_user_id, new_user_id, created_at) VALUES (?, ?, ?)",
            (str(src), str(dst), now),
        )
        conn.execute("UPDATE account_aliases SET new_user_id = ? WHERE new_user_id = ?", (str(dst), str(src)))
        conn.execute("DELETE FROM tg_link_tokens WHERE user_id = ?", (str(src),))
        conn.commit()
    try:
        from assistant.lib import tg_account_ids

        tg_account_ids.forget(*(x for x in (src, dst, tg) if x))
    except Exception:
        pass
    return accounts.get(dst) or {}


# ---------------------------------------------------------------- план и склейка


def _tail(a: int | str, b: int | str) -> Optional[tuple[int, int]]:
    """(старый id, основной), если пара уже склеена, но под старым id что-то осталось
    (склейки до 2026-11-11 переносили только доступы к документам)."""
    try:
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        return None
    for old, other in ((a, b), (b, a)):
        new = _live(old)
        if new != old and new == _live(other) and not accounts.get(old):
            return old, new
    return None


def tails() -> list[dict[str, Any]]:
    """Старые склейки, после которых под старым id остались данные."""
    with accounts._LOCK:  # type: ignore[attr-defined]
        olds = [int(r[0]) for r in accounts._conn().execute("SELECT old_user_id FROM account_aliases").fetchall()]  # type: ignore[attr-defined]
    out = []
    for old in olds:
        if accounts.get(old):
            continue
        new = _live(old)
        if new == old:
            continue
        rows = 0
        for path in db_paths():
            try:
                c = _connect(path)
                try:
                    rows += sum(len(i["rows"]) for i in _scan(c, old).values())
                finally:
                    c.close()
            except sqlite3.Error:
                continue
        files = len(_file_plan(old, new))
        if rows or files:
            out.append({"old": old, "new": new, "rows": rows, "files": files})
    return out


def plan(a: int | str, b: int | str) -> dict[str, Any]:
    """Пробный прогон: что и куда переедет. Ничего не меняет."""
    tail = _tail(a, b)
    src, dst = tail or direction(a, b)
    tables: list[dict[str, Any]] = []
    for path in db_paths():
        try:
            c = _connect(path)
            try:
                found = _scan(c, src)
            finally:
                c.close()
        except sqlite3.Error as e:
            tables.append({"db": path.name, "table": "—", "rows": 0, "error": str(e)})
            continue
        for t, info in sorted(found.items()):
            tables.append({
                "db": path.name, "table": t, "rows": len(info["rows"]),
                "columns": sorted(set(info["exact"]) | set(info["text"])),
            })
    files = [(a_, str(p), str(t)) for a_, p, t in _file_plan(src, dst)]
    files += [("ids", str(p), str(p)) for p in _json_files_mentioning(src)]
    return {
        "src": src, "dst": dst, "src_account": accounts.get(src), "dst_account": accounts.get(dst),
        "problems": _tail_problems(src) if tail else problems(src, dst), "tail": bool(tail),
        "tables": tables, "files": files,
        "total_rows": sum(int(t.get("rows") or 0) for t in tables),
    }


def merge(a: int | str, b: int | str, *, reason: str = "", actor: str = "") -> dict[str, Any]:
    """Склеить два аккаунта одного человека. Возвращает отчёт; при отказе — MergeError."""
    with _MERGE_LOCK:
        tail = _tail(a, b)
        src, dst = tail or direction(a, b)
        bad = _tail_problems(src) if tail else problems(src, dst)
        if bad:
            raise MergeError(" ".join(bad))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = _backup_root() / f"{stamp}_{src}_to_{dst}"
        backup.mkdir(parents=True, exist_ok=True)
        stats: dict[str, Any] = {}
        errors: list[str] = []
        dumps: dict[str, Any] = {}

        def sweep(tag: str) -> None:
            for path in db_paths():
                try:
                    c = _connect(path)
                    try:
                        found = _scan(c, src)
                        if not found:
                            continue
                        dumps[f"{tag}:{path.name}"] = {
                            t: [{k: _json_safe(v) for k, v in r.items()} for r in info["rows"]] for t, info in found.items()
                        }
                        (backup / "rows.json").write_text(json.dumps(dumps, ensure_ascii=False, default=str), encoding="utf-8")
                        _move_db(c, src, dst, found, stats.setdefault(path.name, {}))
                    finally:
                        c.close()
                except Exception as e:
                    errors.append(f"{path.name}: {e!r}")
                    print(f"[account_merge] {src}->{dst} db={path} err={e!r}")

        accounts_before = {"src": accounts.get(src), "dst": accounts.get(dst)}
        (backup / "accounts.json").write_text(json.dumps(accounts_before, ensure_ascii=False, default=str), encoding="utf-8")
        sweep("pass1")
        if tail:
            # Вход уже переключён прошлой склейкой — дочищаем данные и файлы.
            acc = accounts.get(dst) or {}
        elif errors:
            # Почту не переключаем: перенесённое уже лежит на основном, повторная склейка доделает остальное.
            raise MergeError("Склейка остановлена на ошибке, вход пока не переключён: " + "; ".join(errors)[:500])
        else:
            acc = _switch_account(src, dst)
            sweep("pass2")  # то, что успели записать на старый id, пока шла склейка
        files: list[str] = []
        _apply_files(src, dst, backup, files)
        report = {
            "src": src, "dst": dst, "account": acc, "reason": reason, "actor": actor,
            "tables": stats, "files": files, "errors": errors, "backup": str(backup), "tail": bool(tail),
            "at": stamp,
        }
        (backup / "report.json").write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        moved = sum(v["moved"] + v["text"] for db in stats.values() for v in db.values())
        print(f"[account_merge] merged {src}->{dst} reason={reason!r} actor={actor!r} rows={moved} errors={len(errors)}")
        return report


# ---------------------------------------------------------------- поиск пар


def _profile(uid: int) -> dict[str, Any]:
    try:
        from assistant.stores import note_members

        return note_members.get_profile(uid) or {}
    except Exception:
        return {}


def _norm_name(p: dict[str, Any]) -> str:
    n = " ".join(str(p.get(k) or "").strip() for k in ("first_name", "last_name")).strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", n)


def _google_email(uid: int) -> str:
    try:
        from assistant.stores import user_prefs

        return str(user_prefs.load_prefs(uid).get("google_account_email") or "").strip().lower()
    except Exception:
        return ""


def candidates() -> list[dict[str, Any]]:
    """Похожие пары «аккаунт по почте + другой аккаунт». strong — доказано, что это один человек
    (сам привязал этот Telegram, или почта входа = его почта Google); остальное — подсказка."""
    from assistant.stores import notes as notes_store

    with accounts._LOCK:  # type: ignore[attr-defined]
        conn = accounts._conn()  # type: ignore[attr-defined]
        accs = [
            {"user_id": int(r[0]), "email": str(r[1] or ""), "tg_user_id": int(r[2]) if r[2] else None}
            for r in conn.execute("SELECT user_id, email, tg_user_id FROM accounts").fetchall()
        ]
        aliased = {int(r[0]) for r in conn.execute("SELECT old_user_id FROM account_aliases").fetchall()}
        try:
            nconn = notes_store._conn()  # type: ignore[attr-defined]
            profiles = {
                int(r["user_id"]): dict(r) for r in nconn.execute("SELECT * FROM user_profiles").fetchall()
                if str(r["user_id"]).lstrip("-").isdigit()
            }
        except Exception:
            profiles = {}
    by_id = {a["user_id"]: a for a in accs}
    synthetic = [a for a in accs if accounts.is_synthetic(a["user_id"]) and a["user_id"] not in aliased]
    out: dict[tuple[int, int], dict[str, Any]] = {}

    def add(src: int, dst: int, reason: str, strong: bool) -> None:
        key = (src, dst)
        cur = out.get(key)
        if cur:
            cur["reasons"].append(reason)
            cur["strong"] = cur["strong"] or strong
        else:
            out[key] = {"src": src, "dst": dst, "reasons": [reason], "strong": strong}

    # 1) аккаунту по почте привязали Telegram, у которого остался старый аккаунт с данными
    for a in synthetic:
        tg = a["tg_user_id"]
        if tg and tg not in by_id and accounts._tg_has_history(tg):  # type: ignore[attr-defined]
            add(a["user_id"], tg, "сам привязал этот Telegram, а старый аккаунт остался отдельно", True)
    # 2) почта входа = почта Google, подключённого в другом аккаунте
    google: dict[str, list[int]] = {}
    try:
        pd = _prefs_dir()
        for p in pd.glob("*.json"):
            if not re.fullmatch(r"-?\d+\.json", p.name):
                continue
            uid = int(p.stem)
            if accounts.is_synthetic(uid) and uid not in by_id:
                continue
            em = _google_email(uid)
            if em:
                google.setdefault(em, []).append(uid)
    except Exception as e:
        print(f"[account_merge] google scan err={e!r}")
    for a in synthetic:
        for other in google.get(a["email"], []):
            if other != a["user_id"] and other not in aliased:
                add(a["user_id"], other, f"почта входа {a['email']} подключена как Google в другом аккаунте", True)
    # 3) подсказки: тот же @ник или то же имя. Имя — из профиля (своё и из Telegram) и из задач:
    # у аккаунта по почте профиль часто пустой, а постановщик вписал имя в поручение.
    nick: dict[str, list[int]] = {}
    name: dict[str, list[int]] = {}

    def add_name(n: str, uid: int) -> None:
        n = re.sub(r"\s+", " ", (n or "").strip().lower().replace("ё", "е"))
        if n and " " in n and uid not in name.setdefault(n, []):
            name[n].append(uid)

    for uid, p in profiles.items():
        if uid in aliased:
            continue
        u = str(p.get("username") or "").strip().lstrip("@").lower()
        if u:
            nick.setdefault(u, []).append(uid)
        add_name(_norm_name(p), uid)
        add_name(" ".join(str(p.get(k) or "").strip() for k in ("custom_first_name", "custom_last_name")), uid)
    for uid, n in _task_assignee_names():
        if uid not in aliased:
            add_name(n, uid)
    # 4) контакт, где почта и Telegram в одной карточке: почта — у аккаунта по почте, Telegram — у другого
    for owner, c in _contact_rows():
        em = str(c.get("email") or "").strip().lower()
        acc = accounts.get_by_email(em) if em else None
        if not acc or not accounts.is_synthetic(acc["user_id"]):
            continue
        tg = _contact_tg(c)
        if tg and tg != acc["user_id"] and tg not in aliased and accounts.account_for_tg(tg) != acc["user_id"]:
            try:
                src, dst = direction(acc["user_id"], tg)
            except AccountError:
                continue
            label = str(c.get("name") or em)
            add(src, dst, f"в контактах у id {owner} одна карточка «{label}»: эта почта и этот Telegram", False)
    for groups, label in ((nick, "тот же @ник"), (name, "то же имя и фамилия")):
        for key, ids in groups.items():
            syn = [i for i in ids if accounts.is_synthetic(i) and i in by_id]
            others = [i for i in ids if i not in syn]
            for s_ in syn:
                for o in others or [i for i in syn if i != s_]:
                    try:
                        src, dst = direction(s_, o)
                    except AccountError:
                        continue
                    add(src, dst, f"{label}: {key}", False)
    res = []
    for item in out.values():
        try:
            item["problems"] = problems(item["src"], item["dst"])
        except Exception as e:
            item["problems"] = [repr(e)]
        res.append(item)
    res.sort(key=lambda x: (not x["strong"], bool(x["problems"]), x["src"]))
    return res


def _task_assignee_names() -> list[tuple[int, str]]:
    """(исполнитель, имя, как его вписали в поручение)."""
    try:
        from assistant.stores import calendar_tasks as tasks_store

        with tasks_store._LOCK:  # type: ignore[attr-defined]
            rows = tasks_store._conn().execute(  # type: ignore[attr-defined]
                "SELECT DISTINCT assignee_user_id, assignee_name FROM calendar_tasks "
                "WHERE assignee_user_id != '' AND IFNULL(assignee_name, '') != ''"
            ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows if str(r[0]).lstrip("-").isdigit()]
    except Exception as e:
        print(f"[account_merge] task names err={e!r}")
        return []


def _contact_rows() -> list[tuple[str, dict[str, Any]]]:
    """Личные контакты всех владельцев: (id владельца, карточка)."""
    out: list[tuple[str, dict[str, Any]]] = []
    try:
        from assistant.stores import contacts_store

        for d in _contacts_dirs():
            for p in sorted(d.glob("*.json")):
                try:
                    rows = contacts_store._read_contact_rows(p)  # type: ignore[attr-defined]
                except Exception:
                    continue
                out += [(p.stem, c) for c in rows if isinstance(c, dict)]
    except Exception as e:
        print(f"[account_merge] contacts err={e!r}")
    return out


def _contact_tg(c: dict[str, Any]) -> Optional[int]:
    raw = str(c.get("telegram_user_id") or "").strip()
    if raw.lstrip("-").isdigit() and int(raw) > 0:
        return int(raw)
    uname = str(c.get("telegram_username") or "").strip().lstrip("@")
    if not uname:
        return None
    try:
        from assistant.stores import telegram_registry

        found = telegram_registry.lookup_user_id(uname)
        return int(found) if found else None
    except Exception:
        return None


def strong_pair_for_email_login(email: str, uid: int) -> Optional[int]:
    """Вход по почте в аккаунт uid (по почте): если эта почта — Google другого аккаунта, вернуть его."""
    if not accounts.is_synthetic(uid):
        return None
    try:
        from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

        other = lookup_user_id_by_calendar_email(email)
    except Exception:
        other = None
    if other and int(other) != int(uid) and not accounts.resolve_alias(int(other)):
        return int(other)
    return None


def try_auto_merge(a: int, b: int, *, reason: str) -> Optional[int]:
    """Склеить, если можно; вернуть id основного или None (с записью в лог)."""
    try:
        return int(merge(a, b, reason=reason, actor="auto")["dst"])
    except AccountError as e:
        print(f"[account_merge] auto {a}+{b} refused: {e}")
    except Exception as e:
        print(f"[account_merge] auto {a}+{b} failed: {e!r}")
    return None

