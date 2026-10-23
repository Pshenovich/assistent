"""IMAP для любых почтовых ящиков: Яндекс по OAuth (XOAUTH2), остальные по паролю приложения.

Только чтение: письма берём через BODY.PEEK — флаг «прочитано» не меняется.
"""

from __future__ import annotations

import email
import html
import imaplib
import re
import socket
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Iterable, Optional

TIMEOUT = 30

# Известные провайдеры: домен → настройки. Остальное — Thunderbird autoconfig.
PROVIDERS: dict[str, dict[str, Any]] = {
    "yandex": {"imap": ("imap.yandex.ru", 993), "smtp": ("smtp.yandex.ru", 465), "label": "Яндекс"},
    "gmail": {"imap": ("imap.gmail.com", 993), "smtp": ("smtp.gmail.com", 465), "label": "Gmail"},
    "mailru": {"imap": ("imap.mail.ru", 993), "smtp": ("smtp.mail.ru", 465), "label": "Mail.ru"},
}
_DOMAINS = {
    "yandex.ru": "yandex", "ya.ru": "yandex", "yandex.com": "yandex", "yandex.by": "yandex", "yandex.kz": "yandex",
    "narod.ru": "yandex",
    "gmail.com": "gmail", "googlemail.com": "gmail",
    "mail.ru": "mailru", "inbox.ru": "mailru", "list.ru": "mailru", "bk.ru": "mailru", "internet.ru": "mailru",
}


class MailAuthError(Exception):
    """Неверный логин/пароль или выключен IMAP — понятное пользователю сообщение в str()."""


def provider_for(email_addr: str) -> str:
    domain = email_addr.rsplit("@", 1)[-1].strip().lower()
    return _DOMAINS.get(domain, "imap")


def autoconfig(email_addr: str) -> Optional[dict[str, Any]]:
    """IMAP/SMTP сервер по адресу: известные провайдеры, затем база Thunderbird (ISPDB)."""
    prov = provider_for(email_addr)
    if prov in PROVIDERS:
        p = PROVIDERS[prov]
        return {"provider": prov, "imap_host": p["imap"][0], "imap_port": p["imap"][1],
                "smtp_host": p["smtp"][0], "smtp_port": p["smtp"][1]}
    domain = email_addr.rsplit("@", 1)[-1].strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return None
    try:
        import requests
        import xml.etree.ElementTree as ET

        r = requests.get(f"https://autoconfig.thunderbird.net/v1.1/{domain}", timeout=8)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
        out: dict[str, Any] = {"provider": "imap"}
        for srv in root.iter("incomingServer"):
            if srv.get("type") == "imap" and (srv.findtext("socketType") or "") == "SSL":
                out["imap_host"], out["imap_port"] = srv.findtext("hostname"), int(srv.findtext("port") or 993)
                break
        for srv in root.iter("outgoingServer"):
            if srv.get("type") == "smtp" and (srv.findtext("socketType") or "") in ("SSL", "STARTTLS"):
                out["smtp_host"], out["smtp_port"] = srv.findtext("hostname"), int(srv.findtext("port") or 465)
                break
        return out if out.get("imap_host") else None
    except Exception:
        return None


def _xoauth2(email_addr: str, access_token: str) -> bytes:
    return f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01".encode()


def humanize_login_error(provider: str, err: Exception) -> str:
    msg = str(err).lower()
    if isinstance(err, (socket.gaierror, socket.timeout, TimeoutError, ConnectionError, OSError)) and not isinstance(
        err, imaplib.IMAP4.error
    ):
        return "Не удалось связаться с почтовым сервером. Проверьте адрес сервера или попробуйте позже."
    if provider == "yandex" and ("imap" in msg and ("disabled" in msg or "not enabled" in msg or "запрещ" in msg)):
        return "В настройках Яндекс-почты выключен доступ по IMAP."
    if "application-specific" in msg or "app password" in msg or "web login required" in msg:
        return "Нужен пароль приложения, а не обычный пароль от почты."
    if "authenticationfailed" in msg or "invalid credentials" in msg or "login failure" in msg or "auth" in msg:
        if provider == "yandex":
            return "Яндекс не пустил по IMAP: скорее всего, в настройках почты выключен доступ почтовым программам."
        return ("Почта не приняла пароль. Скорее всего, вставлен обычный пароль от почты, "
                "а нужен пароль приложения.")
    return "Почта отказала во входе. Проверьте адрес и пароль приложения."


def connect(account: dict[str, Any], *, secret: str = "", access_token: str = "") -> imaplib.IMAP4_SSL:
    host = str(account.get("imap_host") or PROVIDERS.get(str(account.get("provider")), {}).get("imap", ("",))[0])
    port = int(account.get("imap_port") or 993)
    mail = imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT)
    try:
        if access_token:
            mail.authenticate("XOAUTH2", lambda _: _xoauth2(str(account["email"]), access_token))
        else:
            mail.login(str(account["email"]), secret.replace(" ", "") if account.get("provider") == "gmail" else secret)
    except imaplib.IMAP4.error as e:
        try:
            mail.logout()
        except Exception:
            pass
        raise MailAuthError(humanize_login_error(str(account.get("provider") or ""), e)) from e
    return mail


# ---------------------------------------------------------------- папки

_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+"?(?P<delim>[^"\s]*)"?\s+(?P<name>.+)$')


def folders(mail: imaplib.IMAP4_SSL) -> list[tuple[str, str]]:
    """[(флаги, имя)] — имя в том виде, как его надо передавать в SELECT (в кавычках, UTF-7)."""
    typ, data = mail.list()
    out = []
    for raw in data or []:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        m = _LIST_RE.match(line)
        if m:
            name = m.group("name").strip()
            if not name.startswith('"'):
                name = f'"{name}"'
            out.append((m.group("flags"), name))
    return out


def sent_folder(mail: imaplib.IMAP4_SSL) -> Optional[str]:
    items = folders(mail)
    for flags, name in items:
        if "\\Sent" in flags:
            return name
    for _flags, name in items:
        if name.strip('"').lower() in ("sent", "sent items", "sent messages", "отправленные", "inbox.sent"):
            return name
    return None


def select(mail: imaplib.IMAP4_SSL, folder: str) -> int:
    """Открыть папку только на чтение; вернуть UIDVALIDITY."""
    typ, _ = mail.select(folder, readonly=True)
    if typ != "OK":
        raise RuntimeError(f"select {folder} failed")
    resp = mail.response("UIDVALIDITY")[1]
    try:
        return int((resp or [b"0"])[0])
    except (TypeError, ValueError):
        return 0


def select_rw(mail: imaplib.IMAP4_SSL, folder: str) -> None:
    """Открыть папку на запись (отметки «прочитано», перенос в спам). Без прав — ошибка."""
    try:
        typ, data = mail.select(folder, readonly=False)  # imaplib сам бросает readonly, если папка только на чтение
    except imaplib.IMAP4.readonly as e:
        raise PermissionError(f"{folder} read-only") from e
    if typ != "OK":
        raise PermissionError(f"select {folder}: {data!r}")


def set_seen(mail: imaplib.IMAP4_SSL, uids: list[int], seen: bool) -> None:
    if not uids:
        return
    typ, data = mail.uid("STORE", ",".join(str(u) for u in uids), "+FLAGS.SILENT" if seen else "-FLAGS.SILENT", "(\\Seen)")
    if typ != "OK":
        raise PermissionError(f"store: {data!r}")


def seen_uids(mail: imaplib.IMAP4_SSL, uids: list[int]) -> set[int]:
    """Какие из писем уже прочитаны в ящике (флаг \\Seen)."""
    out: set[int] = set()
    for part in _chunks(uids, 200):
        typ, data = mail.uid("FETCH", ",".join(str(u) for u in part), "(UID FLAGS)")
        if typ != "OK":
            continue
        for item in data or []:
            line = item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item[0] if isinstance(item, tuple) else item)
            m = re.search(r"UID (\d+)", line)
            if m and "\\Seen" in line:
                out.add(int(m.group(1)))
    return out


def junk_folder(mail: imaplib.IMAP4_SSL) -> Optional[str]:
    items = folders(mail)
    for flags, name in items:
        if "\\Junk" in flags:
            return name
    for _flags, name in items:
        if name.strip('"').lower() in ("spam", "junk", "[gmail]/spam", "спам", "junk e-mail", "inbox.spam"):
            return name
    return None


def move(mail: imaplib.IMAP4_SSL, uids: list[int], dest: str) -> None:
    """Перенести письма (UID MOVE; без него — копия и пометка в исходной папке, без EXPUNGE чужого)."""
    if not uids:
        return
    ids = ",".join(str(u) for u in uids)
    caps = " ".join(c.decode() if isinstance(c, bytes) else str(c) for c in (mail.capabilities or ())).upper()
    if "MOVE" in caps:
        typ, data = mail.uid("MOVE", ids, dest)
        if typ != "OK":
            raise PermissionError(f"move: {data!r}")
        return
    typ, data = mail.uid("COPY", ids, dest)
    if typ != "OK":
        raise PermissionError(f"copy: {data!r}")
    mail.uid("STORE", ids, "+FLAGS.SILENT", "(\\Deleted)")
    if "UIDPLUS" in caps:
        mail.uid("EXPUNGE", ids)


def search_uids(mail: imaplib.IMAP4_SSL, *, since: datetime, after_uid: int = 0, gmail_primary: bool = False) -> list[int]:
    crit = ["SINCE", since.strftime("%d-%b-%Y")]
    if after_uid:
        crit = ["UID", f"{after_uid + 1}:*"]
    if gmail_primary:
        crit += ["X-GM-RAW", '"category:primary"']
    typ, data = mail.uid("SEARCH", *crit)
    if typ != "OK" or not data or not data[0]:
        return []
    uids = sorted(int(x) for x in data[0].split() if x.isdigit())
    return [u for u in uids if u > after_uid]


def _chunks(seq: list[int], n: int) -> Iterable[list[int]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch(mail: imaplib.IMAP4_SSL, uids: list[int], what: str) -> dict[int, tuple[int, bytes]]:
    """{uid: (размер, байты)} — what: 'BODY.PEEK[HEADER]' или 'BODY.PEEK[]'."""
    out: dict[int, tuple[int, bytes]] = {}
    for part in _chunks(uids, 40):
        typ, data = mail.uid("FETCH", ",".join(str(u) for u in part), f"(UID RFC822.SIZE {what})")
        if typ != "OK":
            continue
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            head = item[0].decode("utf-8", errors="replace") if isinstance(item[0], bytes) else str(item[0])
            m_uid, m_size = re.search(r"UID (\d+)", head), re.search(r"RFC822\.SIZE (\d+)", head)
            if m_uid:
                out[int(m_uid.group(1))] = (int(m_size.group(1)) if m_size else 0, bytes(item[1] or b""))
    return out


def search_people(mail: imaplib.IMAP4_SSL, addrs: list[str], *, inbox: bool) -> list[int]:
    """UID писем от/к этим адресам за всю историю папки (FROM — во «Входящих», TO/CC — в «Отправленных»)."""
    uids: set[int] = set()
    for a in addrs[:5]:
        a = a.strip().lower()
        if not a or '"' in a:
            continue
        for key in (("FROM",) if inbox else ("TO", "CC")):
            typ, data = mail.uid("SEARCH", key, f'"{a}"')
            if typ == "OK" and data and data[0]:
                uids.update(int(x) for x in data[0].split() if x.isdigit())
    return sorted(uids)


def find_by_message_id(mail: imaplib.IMAP4_SSL, message_id: str) -> Optional[int]:
    mid = message_id.strip()
    if not mid:
        return None
    typ, data = mail.uid("SEARCH", "HEADER", "Message-ID", f'"{mid}"')
    if typ != "OK" or not data or not data[0]:
        return None
    uids = [int(x) for x in data[0].split() if x.isdigit()]
    return uids[-1] if uids else None


# ---------------------------------------------------------------- разбор письма

def decode_hdr(raw: Any) -> str:
    if not raw:
        return ""
    parts: list[str] = []
    try:
        for chunk, enc in decode_header(str(raw)):
            if isinstance(chunk, bytes):
                try:
                    parts.append(chunk.decode(enc or "utf-8", errors="replace"))
                except LookupError:
                    parts.append(chunk.decode("utf-8", errors="replace"))
            else:
                parts.append(str(chunk))
    except Exception:
        return str(raw)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def addresses(msg: Message, *headers: str) -> list[tuple[str, str]]:
    """[(имя, адрес в нижнем регистре)]."""
    raw = [decode_hdr(v) for h in headers for v in (msg.get_all(h) or [])]
    out, seen = [], set()
    for name, addr in getaddresses(raw):
        a = addr.strip().lower()
        if "@" in a and a not in seen:
            seen.add(a)
            out.append((name.strip().strip('"'), a))
    return out


def msg_ids(raw: Any) -> list[str]:
    return re.findall(r"<[^<>\s]+>", str(raw or ""))


def date_utc(msg: Message) -> Optional[datetime]:
    try:
        d = parsedate_to_datetime(str(msg.get("Date") or ""))
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def body_text(msg: Message) -> str:
    plain, rich = [], []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        if "attachment" in str(part.get("Content-Disposition") or "").lower():
            continue
        ctype = (part.get_content_type() or "").lower()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        try:
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (plain if ctype == "text/plain" else rich).append(text)
    if plain:
        return "\n".join(plain).strip()
    return _html_to_text("\n".join(rich))


_QUOTE_HEAD = re.compile(
    r"^\s*(?:On .{5,200} wrote:|.{0,200}(?:пишет|написал|написала)\s*:|-{2,}\s*(?:Original Message|Исходное сообщение|Пересылаемое сообщение)\s*-{2,}"
    r"|From:\s.+|От:\s.+)\s*$",
    re.I,
)


def strip_quoted(text: str) -> str:
    """Только то, что написал автор: без цитаты предыдущих писем и подписи."""
    out = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">") or _QUOTE_HEAD.match(line):
            break
        if line.strip() in ("--", "-- ", "—"):
            break
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def attachments(msg: Message) -> list[dict[str, Any]]:
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        fn = part.get_filename()
        if fn or "attachment" in str(part.get("Content-Disposition") or "").lower():
            payload = part.get_payload(decode=True) or b""
            out.append({"name": decode_hdr(fn) or "файл", "size": len(payload), "type": part.get_content_type()})
    return out


def parse(raw: bytes) -> Message:
    return email.message_from_bytes(raw)

