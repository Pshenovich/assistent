"""Почта как источник «Фокуса».

Синхронизация: «Входящие» (14 дней при первом подключении) и «Отправленные» (180 дней, только заголовки —
по ним «знакомые отправители» и «я ответил?»). Письма читаются через BODY.PEEK — «прочитано» не ставится.

Воронка (код до LLM, см. mail_filters): рассылки по заголовкам → знакомый отправитель → лично мне в «Кому».
LLM только размечает прошедшее: просьба ко мне / срок / моё обещание / мой вопрос. Карточки — пачкой вечером.
MAIL_FOCUS_ENABLED=0 — dry-run: разбор идёт, карточки пишутся только в лог.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from assistant.integrations import mail_imap
from assistant.services import mail_filters
from assistant.stores import mail_accounts, mail_index, mail_senders

FIRST_SYNC_DAYS = 14
SENT_SYNC_DAYS = 180
ANALYZE_WINDOW = timedelta(days=3)
MAX_FETCH = 300
MAX_BODY_SIZE = 3 * 1024 * 1024
BATCH = 8
DAILY_CAP = 60
MIN_AGE = timedelta(hours=2)
FOLLOWUP_AFTER = timedelta(days=3)
FOLLOWUP_WINDOW = timedelta(days=14)
CARD_LIMIT = 5
NOT_ME_DOMAIN_MUTE = 2

_SYNC_LOCK = threading.Lock()
_RUNNING: set[int] = set()

MAIL_SYSTEM = """Ты — помощник руководителя. Тебе дают личные рабочие письма пользователя (рассылки уже отсеяны).
Для входящих (dir=in) найди только:
- request — отправитель просит пользователя что-то сделать или задаёт ему вопрос, на который нужен ответ;
- deadline — в письме срок, который касается пользователя.
Для исходящих (dir=out, их написал сам пользователь) найди только:
- promise — пользователь пообещал что-то сделать («пришлю», «подготовлю», «сделаю до…»);
- ask — пользователь сам о чём-то попросил адресата или задал вопрос и ждёт ответа.
Ничего не придумывай, бери только из текста. Вежливости, «спасибо», «ок» и пересылки без просьбы — не пункты.
Ответ строго JSON:
{"items":[{"n":номер письма,"kind":"request|deadline|promise|ask","text":"суть до 120 символов","due":"YYYY-MM-DD или ''"}]}
Сроки «до пятницы», «завтра» переводи в даты относительно today. Если ничего — {"items":[]}."""

KINDS_IN = ("request", "deadline")
KINDS_OUT = ("promise", "ask")


def ingest_enabled() -> bool:
    return os.getenv("MAIL_INGEST_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def focus_enabled() -> bool:
    return os.getenv("MAIL_FOCUS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def poll_interval_sec() -> float:
    try:
        return max(120.0, float(os.getenv("MAIL_POLL_SEC", "720") or "720"))
    except ValueError:
        return 720.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- подключение

def credentials(uid: int, acc: dict[str, Any]) -> dict[str, str]:
    if acc.get("auth") == "oauth_yandex":
        from assistant.integrations import telemost_oauth

        return {"access_token": telemost_oauth.get_valid_access_token(int(uid))}
    return {"secret": mail_accounts.secret(uid, str(acc["account_id"]))}


def open_imap(uid: int, acc: dict[str, Any]):
    return mail_imap.connect(acc, **credentials(uid, acc))


def check_login(acc: dict[str, Any], *, secret: str = "", access_token: str = "") -> None:
    """Проверить вход до сохранения ящика. MailAuthError — с понятным текстом."""
    mail = mail_imap.connect(acc, secret=secret, access_token=access_token)
    try:
        mail_imap.select(mail, "INBOX")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


# ---------------------------------------------------------------- синхронизация

def is_filtered(msg, from_addr: str, *, mine: set[str], rules: Optional[dict[str, str]] = None,
                known: Optional[set[str]] = None) -> bool:
    """Отсеять письмо (приглашение из календаря, рассылка, робот, скрытый отправитель).
    «Всегда пропускать» сильнее воронки, но не приглашений — встречи и так в «Актуальном».
    known — адреса, которым пользователь сам писал: с info@ / support@, с которыми идёт переписка, это не робот."""
    if mail_filters.is_calendar(msg):
        return True
    if from_addr in mine:
        return False
    rule = mail_senders.match(rules or {}, from_addr)
    if rule == "allow":
        return False
    if rule == "block":
        return True
    if known and from_addr in known:
        return False
    return mail_filters.is_bulk(msg) or mail_filters.is_robot(from_addr)


def _row_from(acc: dict[str, Any], folder: str, uid: int, msg, *, mine: set[str], body: Optional[str],
              rules: Optional[dict[str, str]] = None, known: Optional[set[str]] = None) -> dict[str, Any]:
    mid = (mail_imap.msg_ids(msg.get("Message-ID")) or [""])[0]
    irt = (mail_imap.msg_ids(msg.get("In-Reply-To")) or [""])[0]
    refs = mail_imap.msg_ids(msg.get("References"))
    frm = mail_imap.addresses(msg, "From")
    from_name, from_addr = frm[0] if frm else ("", "")
    to = [a for _n, a in mail_imap.addresses(msg, "To")]
    cc = [a for _n, a in mail_imap.addresses(msg, "Cc")]
    reply_to = (mail_imap.addresses(msg, "Reply-To") or [("", "")])[0][1]
    dt = mail_imap.date_utc(msg) or _now()
    direction = "out" if from_addr in mine else "in"
    bulk = mail_filters.is_calendar(msg) or (direction == "in" and is_filtered(msg, from_addr, mine=mine, rules=rules, known=known))
    snippet = mail_imap.strip_quoted(body)[: mail_index.SNIPPET] if body else ""
    return {
        "mail_key": mail_index.mail_key(str(acc["account_id"]), mid, folder, uid),
        "account_id": acc["account_id"], "folder": folder, "uid": uid, "message_id": mid,
        "thread_key": mail_index.thread_key_for(mid, irt, refs), "in_reply_to": irt, "refs": refs,
        "direction": direction, "from_addr": from_addr, "from_name": from_name, "to": to, "cc": cc,
        "reply_to": reply_to, "subject": mail_imap.decode_hdr(msg.get("Subject")), "date_utc": dt.isoformat(),
        "snippet": snippet, "bulk": bulk,
        "has_attachments": "multipart/mixed" in str(msg.get("Content-Type") or "").lower(),
    }


def _sync_folder(uid: int, acc: dict[str, Any], mail, folder: str, *, days: int, bodies_since: datetime,
                 mine: set[str], now: datetime, memory: bool = False) -> dict[str, int]:
    aid = str(acc["account_id"])
    uv = mail_imap.select(mail, folder)
    prev_uv, last = mail_index.sync_state(uid, aid, folder)
    if uv != prev_uv:
        last = 0
    uids = mail_imap.search_uids(
        mail, since=now - timedelta(days=days), after_uid=last,
        gmail_primary=acc.get("provider") == "gmail" and folder == "INBOX",
    )[-MAX_FETCH:]
    stats = {"seen": len(uids), "bulk": 0, "new": 0}
    if not uids:
        mail_index.set_sync_state(uid, aid, folder, uv, last)
        return stats
    heads = mail_imap.fetch(mail, uids, "BODY.PEEK[HEADER]")
    rules = mail_senders.rules_map(uid)
    known = mail_index.correspondents(uid) - mine
    want_body: list[int] = []
    parsed: dict[int, Any] = {}
    for u, (size, raw) in heads.items():
        msg = mail_imap.parse(raw)
        parsed[u] = msg
        frm = (mail_imap.addresses(msg, "From") or [("", "")])[0][1]
        if is_filtered(msg, frm, mine=mine, rules=rules, known=known):
            stats["bulk"] += 1
            continue
        dt = mail_imap.date_utc(msg)
        if size <= MAX_BODY_SIZE and (dt is None or dt >= bodies_since):
            want_body.append(u)
    full = mail_imap.fetch(mail, want_body, "BODY.PEEK[]") if want_body else {}
    for u, msg in parsed.items():
        body = None
        if u in full:
            msg = mail_imap.parse(full[u][1])
            body = mail_imap.body_text(msg)
        row = _row_from(acc, folder, u, msg, mine=mine, body=body, rules=rules, known=known)
        if mail_index.upsert(uid, row):
            stats["new"] += 1
        if memory and body and not row["bulk"]:
            from assistant.services import mail_memory

            if mail_memory.add_message(uid, row, body):
                stats["memory"] = stats.get("memory", 0) + 1
    mail_index.set_sync_state(uid, aid, folder, uv, max(uids))
    return stats


def sync_account(uid: int, acc: dict[str, Any], *, now: Optional[datetime] = None) -> dict[str, int]:
    now = now or _now()
    aid = str(acc["account_id"])
    mine = {str(acc["email"]).lower()}
    try:
        mail = open_imap(uid, acc)
    except mail_imap.MailAuthError as e:
        mail_accounts.set_status(uid, aid, status="auth_error", error=str(e))
        return {}
    except Exception as e:
        mail_accounts.set_status(uid, aid, status="error", error=mail_imap.humanize_login_error(str(acc.get("provider")), e))
        return {}
    try:
        from assistant.services import mail_memory

        memory = mail_memory.enabled(uid)
        hide_calendar(uid)
        first = mail_index.sync_state(uid, aid, "INBOX")[1] == 0
        inbox = _sync_folder(uid, acc, mail, "INBOX", days=FIRST_SYNC_DAYS, bodies_since=now - timedelta(days=FIRST_SYNC_DAYS),
                             mine=mine, now=now, memory=memory)
        try:  # прочитанное в самом ящике → прочитано в Orbita
            from assistant.services import mail_sync

            recent_in = [r for r in mail_index.recent(uid, since=now - timedelta(days=FIRST_SYNC_DAYS), direction="in")
                         if r["account_id"] == aid and r.get("folder") == "INBOX"]
            mail_imap.select(mail, "INBOX")
            mail_sync.pull_seen(uid, acc, mail, "INBOX", recent_in)
        except Exception as e:
            print(f"[mail] pull seen uid={uid} acc={aid} err={e!r}")
        sent_stats: dict[str, int] = {}
        sent = mail_imap.sent_folder(mail)
        if sent:
            sent_stats = _sync_folder(uid, acc, mail, sent, days=SENT_SYNC_DAYS,
                                      bodies_since=now - (timedelta(days=FIRST_SYNC_DAYS) if memory else ANALYZE_WINDOW),
                                      mine=mine, now=now, memory=memory)
        if memory:
            if inbox.get("memory") or sent_stats.get("memory"):
                mail_memory._reindex(uid)
            try:  # история за 12 месяцев — по месяцу на папку за проход
                mail_memory.backfill_step(uid, acc, mail, ["INBOX"] + ([sent] if sent else []), now=now)
            except Exception as e:
                print(f"[mail] backfill uid={uid} acc={aid} err={e!r}")
        prev = dict(acc.get("stats") or {})
        stats = {**prev, "inbox_seen": prev.get("inbox_seen", 0) + inbox["seen"],
                 "bulk": prev.get("bulk", 0) + inbox["bulk"], "sent_seen": prev.get("sent_seen", 0) + sent_stats.get("seen", 0)}
        if first:
            stats["first_sync"] = {"seen": inbox["seen"], "bulk": inbox["bulk"]}
        mail_accounts.set_status(uid, aid, status="ok", stats=stats, synced=True)
        return {**inbox, "sent_new": sent_stats.get("new", 0)}
    except Exception as e:
        print(f"[mail] sync uid={uid} acc={aid} err={e!r}")
        mail_accounts.set_status(uid, aid, status="error", error="Не удалось прочитать почту, попробуем позже.")
        return {}
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def poll_user(uid: int) -> int:
    uid = int(uid)
    with _SYNC_LOCK:
        if uid in _RUNNING:
            return 0
        _RUNNING.add(uid)
    try:
        n = 0
        for acc in mail_accounts.list_accounts(uid):
            n += int(sync_account(uid, acc).get("new", 0))
        try:
            revive_correspondents(uid)
        except Exception as e:
            print(f"[mail] revive uid={uid} err={e!r}")
        try:
            analyze_user(uid)
        except Exception as e:
            print(f"[mail] analyze uid={uid} err={e!r}")
        return n
    finally:
        with _SYNC_LOCK:
            _RUNNING.discard(uid)


def poll_all_users() -> int:
    if not ingest_enabled():
        return 0
    total = 0
    for uid in mail_accounts.owners():
        try:
            total += poll_user(uid)
        except Exception as e:
            print(f"[mail] poll uid={uid} err={e!r}")
    try:
        from assistant.stores import mail_attachments

        mail_index.purge_old()
        mail_attachments.purge_old()
    except Exception as e:
        print(f"[mail] purge err={e!r}")
    return total


def kick(uid: int) -> None:
    """Первая синхронизация сразу после подключения — в фоне."""
    threading.Thread(target=poll_user, args=(int(uid),), daemon=True).start()


# ---------------------------------------------------------------- разбор (LLM размечает, код проверяет)

def my_addresses(uid: int) -> set[str]:
    return {str(a["email"]).lower() for a in mail_accounts.list_accounts(uid)}


_PEOPLE_CACHE: dict[int, tuple[str, set[str]]] = {}


def meeting_people(uid: int, now: datetime) -> set[str]:
    """Участники встреч пользователя за 90 дней и на 2 недели вперёд (календарь; кэш на день)."""
    day = now.date().isoformat()
    hit = _PEOPLE_CACHE.get(int(uid))
    if hit and hit[0] == day:
        return hit[1]
    out: set[str] = set()
    try:
        from assistant.integrations import google_calendar_oauth
        from assistant.services import calendar_sources

        if google_calendar_oauth.load_user_credentials(int(uid)) is not None:
            for ev in calendar_sources.list_events_in_window(int(uid), now - timedelta(days=90), now + timedelta(days=14)):
                for a in (ev.get("raw") or {}).get("attendees") or []:
                    if isinstance(a, dict) and not a.get("self") and a.get("email"):
                        out.add(str(a["email"]).strip().lower())
    except Exception as e:
        print(f"[mail] meeting people uid={uid} err={e!r}")
    try:
        from assistant.stores import meeting_links

        with meeting_links._LOCK:
            rows = meeting_links._conn().execute(
                "SELECT attendees_json FROM meeting_links WHERE owner_user_id = ? AND start_iso >= ?",
                (str(uid), (now - timedelta(days=90)).isoformat()),
            ).fetchall()
        for r in rows:
            for a in json.loads(r["attendees_json"] or "[]"):
                em = str((a or {}).get("email") or "").strip().lower()
                if em:
                    out.add(em)
    except Exception as e:
        print(f"[mail] meeting links uid={uid} err={e!r}")
    _PEOPLE_CACHE[int(uid)] = (day, out)
    return out


def muted(uid: int) -> tuple[set[str], set[str]]:
    """(заглушённые отправители, домены): «Не мне»/«Это рассылка» — отправитель; два «Не мне» с домена — домен."""
    from assistant.services import focus_items

    senders: set[str] = set()
    per_domain: dict[str, set[str]] = {}
    for r in focus_items.rows(uid):
        src = r.get("source") or {}
        if src.get("kind") != "mail" or r.get("reason") not in ("not_me", "newsletter"):
            continue
        addr = str(src.get("from") or "").lower()
        if not addr:
            continue
        senders.add(addr)
        per_domain.setdefault(mail_filters.domain_of(addr), set()).add(str(r.get("item_key")))
    domains = {d for d, keys in per_domain.items() if len(keys) >= NOT_ME_DOMAIN_MUTE}
    for pattern, rule in mail_senders.rules_map(uid).items():
        if rule != "block":
            continue
        (domains.add(pattern[1:]) if pattern.startswith("@") else senders.add(pattern))
    return senders, domains


def revive_correspondents(uid: int, *, limit: int = 20) -> int:
    """Письма от info@ / support@ и т. п., с которыми пользователь сам переписывается, — вернуть из «отсеянных»."""
    known = mail_index.correspondents(uid) - my_addresses(uid)
    rules = mail_senders.rules_map(uid)
    since = _now() - timedelta(days=FIRST_SYNC_DAYS)
    senders = []
    for r in mail_index.recent(uid, since=since, direction="in"):
        addr = str(r.get("from_addr") or "")
        if (r.get("bulk") and addr in known and addr not in senders and mail_senders.match(rules, addr) != "block"
                and not mail_filters.CALENDAR_SUBJECT.match(str(r.get("subject") or ""))):
            senders.append(addr)
    n = 0
    for addr in senders[:limit]:
        try:
            n += revive_sender(uid, addr)
        except Exception as e:
            print(f"[mail] revive correspondent uid={uid} err={e!r}")
    return n


def hide_calendar(uid: int) -> int:
    """Уже попавшие в индекс приглашения — убрать из «Почты», поиска и памяти (по теме)."""
    keys, mids = [], []
    for r in mail_index.recent(uid, since=_now() - timedelta(days=mail_index.RETENTION.days)):
        if not r.get("bulk") and mail_filters.CALENDAR_SUBJECT.match(str(r.get("subject") or "")):
            keys.append(r["mail_key"])
            if r.get("message_id"):
                mids.append(r["message_id"])
    if keys:
        mail_index.bury_keys(uid, keys)
        from assistant.services import mail_memory

        mail_memory.forget_messages(uid, mids)
    return len(keys)


# ---------------------------------------------------------------- правила отправителей

def block_sender(uid: int, pattern: str) -> dict[str, Any]:
    """«Не показывать»: правило + письма отправителя уходят из списка, поиска и памяти Лео."""
    rule = mail_senders.set_rule(uid, pattern, "block")
    keys = mail_index.bury(uid, rule["pattern"])
    try:
        from assistant.services import mail_memory

        mail_memory.forget_sender(uid, rule["pattern"])
    except Exception as e:
        print(f"[mail] forget sender uid={uid} err={e!r}")
    return {**rule, "hidden": len(keys)}


def allow_sender(uid: int, pattern: str) -> dict[str, Any]:
    """«Всегда пропускать»: правило + письма отправителя за 14 дней перечитываются и разбираются заново."""
    rule = mail_senders.set_rule(uid, pattern, "allow")
    n = 0
    try:
        n = revive_sender(uid, rule["pattern"])
    except Exception as e:
        print(f"[mail] revive sender uid={uid} err={e!r}")
    return {**rule, "revived": n}


def revive_sender(uid: int, pattern: str, *, days: int = FIRST_SYNC_DAYS) -> int:
    rows = [r for r in mail_index.filtered_rows(uid, pattern, since=_now() - timedelta(days=days))
            if not mail_filters.CALENDAR_SUBJECT.match(str(r.get("subject") or ""))]
    by_acc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_acc.setdefault(str(r["account_id"]), []).append(r)
    n = 0
    for aid, items in by_acc.items():
        acc = mail_accounts.get(uid, aid)
        if not acc:
            continue
        mail = open_imap(uid, acc)
        try:
            by_folder: dict[str, list[dict[str, Any]]] = {}
            for r in items:
                by_folder.setdefault(str(r.get("folder") or "INBOX"), []).append(r)
            for folder, frows in by_folder.items():
                mail_imap.select(mail, folder)
                got = mail_imap.fetch(mail, [int(r["uid"]) for r in frows if r.get("uid")], "BODY.PEEK[]")
                for r in frows:
                    if int(r.get("uid") or 0) not in got:
                        continue
                    full = mail_imap.parse(got[int(r["uid"])][1])
                    if mail_filters.is_calendar(full):
                        continue  # приглашения не возвращаем ни для кого
                    body = mail_imap.body_text(full)
                    mail_index.revive(uid, r["mail_key"], mail_imap.strip_quoted(body)[: mail_index.SNIPPET])
                    n += 1
        finally:
            try:
                mail.logout()
            except Exception:
                pass
    if n:
        kick(uid)
    return n


def _analyzed_today(uid: int, now: datetime) -> int:
    from assistant.services import proactive_ai

    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with proactive_ai._LOCK:
        r = proactive_ai._conn().execute(
            "SELECT COUNT(*) AS n FROM proactive_insights WHERE owner_user_id = ? AND source_key LIKE 'mail:%' AND analyzed_at >= ?",
            (str(uid), start),
        ).fetchone()
    return int(r["n"] or 0)


def candidates(uid: int, *, now: Optional[datetime] = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(письма для LLM, ключи отсеянных кодом)."""
    now = now or _now()
    rows = mail_index.unanalyzed(uid, since=now - ANALYZE_WINDOW)
    if not rows:
        return [], []
    mine = my_addresses(uid)
    corr = mail_index.correspondents(uid) - mine
    people = meeting_people(uid, now)
    my_threads = mail_index.my_thread_keys(uid)
    ms, md = muted(uid)
    rules = mail_senders.rules_map(uid)
    keep, skip = [], []
    for r in rows:
        rule = mail_senders.match(rules, r.get("from_addr") or "") if r["direction"] == "in" else None
        if rule:  # правило пользователя сильнее воронки
            ok = rule == "allow" and bool(r.get("snippet"))
        elif r["direction"] == "out":
            ok = bool(r.get("snippet")) and any(not mail_filters.is_robot(a) for a in r.get("to") or [])
        else:
            ok = bool(r.get("snippet")) and mail_filters.focus_candidate(
                {**r, "thread_mine": r.get("thread_key") in my_threads}, mine=mine, correspondents=corr,
                meeting_people=people, muted_senders=ms, muted_domains=md,
            )
        (keep if ok else skip).append(r if ok else r["mail_key"])
    return keep, skip


def normalize_items(obj: Any, batch: list[dict[str, Any]], today: date) -> dict[str, list[dict[str, Any]]]:
    """Код проверяет разметку LLM: вид пункта соответствует направлению письма, срок — реальная дата не в прошлом."""
    import re

    out: dict[str, list[dict[str, Any]]] = {}
    for it in (obj.get("items") if isinstance(obj, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(batch):
            continue
        row = batch[n - 1]
        kind = str(it.get("kind") or "").strip()
        if kind not in (KINDS_IN if row["direction"] == "in" else KINDS_OUT):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        due = str(it.get("due") or "").strip()[:10]
        if due:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) or date.fromisoformat(due) < today:
                    due = ""
            except ValueError:
                due = ""
        if kind == "deadline" and not due:
            continue
        lst = out.setdefault(row["mail_key"], [])
        if len(lst) < 3:
            lst.append({"kind": kind, "text": text[:200], "due": due})
    return out


def _llm(batch: list[dict[str, Any]], today: date) -> Any:
    from assistant.lib.llm_json import strip_json_from_markdown
    from assistant.nlu import llm

    letters = []
    for i, r in enumerate(batch, 1):
        letters.append({
            "n": i, "dir": r["direction"], "from": f"{r.get('from_name') or ''} <{r.get('from_addr')}>".strip(),
            "to": ", ".join((r.get("to") or [])[:5]), "subject": r.get("subject") or "",
            "date": str(r.get("date_utc") or "")[:16], "text": str(r.get("snippet") or "")[:1200],
        })
    raw = llm._chat(
        MAIL_SYSTEM,
        json.dumps({"today": today.isoformat(), "letters": letters}, ensure_ascii=False),
        operation="mail_insights",
        model=llm._model_chat_digest(),
        timeout=90,
        temperature=0.1,
        max_tokens=900,
    )
    try:
        return json.loads(strip_json_from_markdown(raw))
    except (json.JSONDecodeError, TypeError):
        return {}


def analyze_user(uid: int, *, now: Optional[datetime] = None) -> int:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.services import proactive_ai
    from assistant.services.calendar import _tz_for

    now = now or _now()
    keep, skip = candidates(uid, now=now)
    mail_index.mark_analyzed(uid, skip, 2)
    budget = max(0, DAILY_CAP - _analyzed_today(uid, now))
    keep = keep[:budget]
    if not keep:
        return 0
    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    today = datetime.now(_tz_for(uid)).date()
    done = 0
    for i in range(0, len(keep), BATCH):
        batch = keep[i:i + BATCH]
        found = normalize_items(_llm(batch, today), batch, today)
        for r in batch:
            items = [{**it, "who": r.get("from_name") or r.get("from_addr") or ""} for it in found.get(r["mail_key"], [])]
            proactive_ai.save(uid, f"mail:{r['mail_key']}", r["mail_key"], str(r.get("subject") or ""), items)
            if items and not focus_enabled():
                print(f"[mail] dry-run uid={uid} {r['direction']} from={r.get('from_addr')} subj={r.get('subject')!r} items={items}")
        mail_index.mark_analyzed(uid, [r["mail_key"] for r in batch], 1)
        done += len(batch)
    if done:
        proactive_ai._LAST_ANALYZED[int(uid)] = __import__("time").time()
    return done


# ---------------------------------------------------------------- карточки

def _insights(uid: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """[(письмо, пункт)] из сохранённых разборов."""
    from assistant.services import proactive_ai

    with proactive_ai._LOCK:
        rows = proactive_ai._conn().execute(
            "SELECT source_key, items_json FROM proactive_insights WHERE owner_user_id = ? AND source_key LIKE 'mail:%'"
            " AND items_json != '[]'",
            (str(uid),),
        ).fetchall()
    out = []
    for r in rows:
        mrow = mail_index.get(uid, str(r["source_key"])[5:])
        if not mrow:
            continue
        for it in json.loads(r["items_json"] or "[]"):
            out.append((mrow, it))
    return out


def item_key(mrow: dict[str, Any], it: dict[str, Any]) -> str:
    from assistant.services import proactive_ai

    return "mail:%s:%s" % (mrow["mail_key"], proactive_ai._hash(str(it.get("kind")), str(it.get("text")))[:10])


def source_for(mrow: dict[str, Any]) -> dict[str, Any]:
    who = mrow.get("from_name") or mrow.get("from_addr") or ""
    return {"kind": "mail", "id": mrow["mail_key"], "from": mrow.get("from_addr") or "",
            "thread": mrow.get("thread_key") or "", "label": f"письмо «{mrow.get('subject') or 'без темы'}» · {who}"[:200]}


def _day_label(iso: str, tz: Any, today: date) -> str:
    try:
        d = datetime.fromisoformat(iso).astimezone(tz).date()
    except ValueError:
        return ""
    if d == today:
        return "сегодня"
    if d == today - timedelta(days=1):
        return "вчера"
    from assistant.services.proactive_ai import _RU_MON

    return f"{d.day} {_RU_MON[d.month - 1]}"


def pending_requests(uid: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Письма, где ждут ответа пользователя, — после всех проверок кодом."""
    from assistant.services import focus_items

    now = now or _now()
    hidden = focus_items.hidden_keys(uid, now)
    ms, md = muted(uid)
    out, seen = [], set()
    for mrow, it in _insights(uid):
        if it.get("kind") != "request" or mrow["direction"] != "in":
            continue
        key = item_key(mrow, it)
        if key in hidden or mrow["mail_key"] in seen:
            continue
        if mail_filters.muted(mrow.get("from_addr") or "", ms, md):
            continue
        try:
            ts = datetime.fromisoformat(str(mrow["date_utc"]))
        except ValueError:
            continue
        if now - ts < MIN_AGE or now - ts > timedelta(days=7):
            continue
        if mail_index.replied(uid, mrow):
            continue
        seen.add(mrow["mail_key"])
        out.append({"key": key, "mail_key": mrow["mail_key"], "from": mrow.get("from_addr") or "",
                    "who": mrow.get("from_name") or mrow.get("from_addr") or "", "subject": mrow.get("subject") or "",
                    "text": str(it.get("text") or ""), "asked_at": ts.isoformat()})
    out.sort(key=lambda x: x["asked_at"])
    return out


def followups(uid: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """«Слежу за ответом»: пользователь о чём-то попросил в письме, ответа нет 3+ дня."""
    from assistant.services import focus_items

    now = now or _now()
    hidden = focus_items.hidden_keys(uid, now)
    out, seen = [], set()
    for mrow, it in _insights(uid):
        if it.get("kind") != "ask" or mrow["direction"] != "out":
            continue
        key = item_key(mrow, it)
        if key in hidden or mrow.get("thread_key") in seen:
            continue
        try:
            ts = datetime.fromisoformat(str(mrow["date_utc"]))
        except ValueError:
            continue
        if not (FOLLOWUP_AFTER <= now - ts <= FOLLOWUP_WINDOW):
            continue
        if mail_index.answered_by_them(uid, mrow):
            continue
        seen.add(mrow.get("thread_key"))
        to = (mrow.get("to") or [""])[0]
        out.append({"key": key, "mail_key": mrow["mail_key"], "to": to, "subject": mrow.get("subject") or "",
                    "text": str(it.get("text") or ""), "sent_at": ts.isoformat()})
    return out


def mail_cards(uid: int, *, task_titles: Optional[list[str]] = None, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Обещания из моих писем и сроки из входящих — карточки «Фокуса» (обещания идут в реестр B1)."""
    from assistant.services import proactive_ai
    from assistant.services.calendar import _tz_for

    now = now or _now()
    tz = _tz_for(uid)
    today = now.astimezone(tz).date()
    titles = list(task_titles or []) + proactive_ai._handled(uid)
    ms, md = muted(uid)
    out = []
    for mrow, it in _insights(uid):
        kind = it.get("kind")
        if kind not in ("promise", "deadline"):
            continue
        if kind == "deadline" and mail_filters.muted(mrow.get("from_addr") or "", ms, md):
            continue
        text = str(it.get("text") or "")
        if proactive_ai._covered(text, titles):
            continue
        key = item_key(mrow, it)
        src = source_for(mrow)
        day_l = _day_label(str(mrow.get("date_utc") or ""), tz, today)
        due = str(it.get("due") or "")
        if kind == "promise":
            to = (mrow.get("to") or [""])[0]
            source = f"ваше письмо «{mrow.get('subject') or 'без темы'}» · {to} · {day_l}"
            acts = [{"label": "Напомнить себе", "act": "assistant", "primary": True,
                     "payload": {"prompt": f"Поставь мне задачу «{text}»" + (f" на {due}" if due else "")
                                 + f" — обещал в письме «{mrow.get('subject')}» ({to})."}},
                    {"label": "Разобрать письмо", "act": "mail_thread", "payload": {"mail_key": mrow["mail_key"]}},
                    {"label": "✓ Сделано", "act": "mark_done"}]
        else:
            source = f"письмо «{mrow.get('subject') or 'без темы'}» · {mrow.get('from_name') or mrow.get('from_addr')} · {day_l}"
            acts = [{"label": "Задача себе", "act": "assistant", "primary": True,
                     "payload": {"prompt": f"Поставь мне задачу: {text}" + (f" на {due}" if due else "")
                                 + f" (из письма «{mrow.get('subject')}»)."}},
                    {"label": "Разобрать письмо", "act": "mail_thread", "payload": {"mail_key": mrow["mail_key"]}}]
        out.append({
            "key": key, "type": "mail_" + str(kind), "tag": "🤝 вы обещали" if kind == "promise" else "⏰ срок",
            "urgency": 2, "title": text, "source": source, "ask": f"{text} ({source})",
            "mail": {"mail_key": mrow["mail_key"], "subject": mrow.get("subject") or "", "from": mrow.get("from_addr") or "",
                     "thread": mrow.get("thread_key") or ""},
            "actions": acts, "items": [{"id": key, "text": text}],
            "_item": {"text": text, "who": "" if kind == "promise" else (mrow.get("from_name") or ""), "due": due},
        })
    if not focus_enabled():
        for c in out:
            print(f"[mail] dry-run card uid={uid} {c['type']}: {c['title']} ({c['source']})")
        return []
    return out


def awaiting_items(uid: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Пункты для вечерней карточки «Ждут ответа»: письма без ответа + мои письма без ответа (не больше CARD_LIMIT)."""
    reqs = pending_requests(uid, now=now)
    fups = followups(uid, now=now)
    items = [{"id": q["key"], "text": f"✉️ {q['who']}: {q['text']}", "mail_key": q["mail_key"], "from": q["from"]}
             for q in reqs]
    items += [{"id": f["key"], "text": f"✉️ нет ответа от {f['to']}: {f['text']}", "mail_key": f["mail_key"], "from": f["to"]}
              for f in fups]
    if not focus_enabled():
        for i in items:
            print(f"[mail] dry-run awaiting uid={uid}: {i['text']}")
        return []
    return items


def prep_threads(uid: int, emails: list[str], *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Подготовка к встрече: последние ветки переписки с участниками (без LLM)."""
    if not emails or not mail_accounts.list_accounts(uid):
        return []
    now = now or _now()
    rows = mail_index.with_people(uid, emails, since=now - timedelta(days=30), limit=3)
    return [{"mail_key": r["mail_key"], "subject": r.get("subject") or "без темы", "date": r.get("date_utc"),
             "from": r.get("from_name") or r.get("from_addr") or ""} for r in rows]
