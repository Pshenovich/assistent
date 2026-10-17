"""Построение RRULE для Google Calendar / Tasks и следующего срабатывания напоминаний."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_WD_MAP = {
    "mo": "MO",
    "tu": "TU",
    "we": "WE",
    "th": "TH",
    "fr": "FR",
    "sa": "SA",
    "su": "SU",
    "пн": "MO",
    "вт": "TU",
    "ср": "WE",
    "чт": "TH",
    "пт": "FR",
    "сб": "SA",
    "вс": "SU",
    "weekday": "WEEKDAYS",
    "weekdays": "WEEKDAYS",
    "будни": "WEEKDAYS",
    "будние": "WEEKDAYS",
}

_FREQ_MAP = {
    "daily": "DAILY",
    "day": "DAILY",
    "ежедневно": "DAILY",
    "каждый день": "DAILY",
    "weekly": "WEEKLY",
    "week": "WEEKLY",
    "еженедельно": "WEEKLY",
    "monthly": "MONTHLY",
    "month": "MONTHLY",
    "ежемесячно": "MONTHLY",
}


def _norm_freq(raw: Any) -> str | None:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if s.upper() in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return s.upper()
    return _FREQ_MAP.get(s)


def _norm_byday(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    items: list[Any]
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("weekdays", "weekday", "будни", "будние"):
            return ["MO", "TU", "WE", "TH", "FR"]
        items = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []
    out: list[str] = []
    for it in items:
        key = str(it or "").strip().lower()
        if not key:
            continue
        mapped = _WD_MAP.get(key) or _WD_MAP.get(key[:2])
        if mapped == "WEEKDAYS":
            for d in ("MO", "TU", "WE", "TH", "FR"):
                if d not in out:
                    out.append(d)
            continue
        if mapped and mapped not in out:
            out.append(mapped)
        elif key.upper() in ("MO", "TU", "WE", "TH", "FR", "SA", "SU") and key.upper() not in out:
            out.append(key.upper())
    return out


def _parse_until(raw: Any) -> date | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def build_rrule_from_args(args: dict[str, Any] | None) -> str | None:
    """Собрать одну строку RRULE:… из аргументов инструмента или сырого rrule."""
    if not isinstance(args, dict):
        return None
    raw = str(args.get("rrule") or args.get("recurrence_rule") or "").strip()
    if raw:
        if not raw.upper().startswith("RRULE:"):
            raw = "RRULE:" + raw
        return raw
    block = args.get("recurrence")
    if isinstance(block, str) and block.strip():
        s = block.strip()
        if s.upper().startswith("RRULE:"):
            return s
        # короткая форма: "weekdays until 2026-11-30"
        return None
    if not isinstance(block, dict):
        # плоские поля на верхнем уровне
        block = {
            "freq": args.get("recur_freq") or args.get("freq"),
            "interval": args.get("recur_interval") or args.get("interval"),
            "byday": args.get("recur_byday") or args.get("byday"),
            "until": args.get("recur_until") or args.get("until"),
            "count": args.get("recur_count") or args.get("count"),
        }
    freq = _norm_freq(block.get("freq"))
    if not freq:
        return None
    parts = [f"FREQ={freq}"]
    try:
        interval = int(block.get("interval") or 1)
    except (TypeError, ValueError):
        interval = 1
    if interval > 1:
        parts.append(f"INTERVAL={min(interval, 30)}")
    byday = _norm_byday(block.get("byday"))
    if byday:
        parts.append("BYDAY=" + ",".join(byday))
    elif freq == "WEEKLY" and str(block.get("byday") or "").strip().lower() in (
        "weekdays",
        "будни",
    ):
        parts.append("BYDAY=MO,TU,WE,TH,FR")
    until = _parse_until(block.get("until"))
    if until:
        # Google: UNTIL в UTC compact
        parts.append(f"UNTIL={until.strftime('%Y%m%d')}T235959Z")
    else:
        try:
            count = int(block.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            parts.append(f"COUNT={min(count, 366)}")
    return "RRULE:" + ";".join(parts)


def recurrence_lines_from_args(args: dict[str, Any] | None) -> list[str]:
    rule = build_rrule_from_args(args)
    return [rule] if rule else []


def human_recurrence(rrule: str | None) -> str:
    s = str(rrule or "")
    if not s:
        return ""
    up = s.upper()
    bits: list[str] = []
    if "FREQ=DAILY" in up:
        bits.append("каждый день")
    elif "FREQ=WEEKLY" in up:
        if "BYDAY=MO,TU,WE,TH,FR" in up.replace(" ", ""):
            bits.append("по будням")
        else:
            bits.append("еженедельно")
    elif "FREQ=MONTHLY" in up:
        bits.append("ежемесячно")
    else:
        bits.append("повтор")
    if "UNTIL=" in up:
        try:
            raw = up.split("UNTIL=", 1)[1].split(";", 1)[0]
            bits.append("до " + date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8])).strftime("%d.%m.%Y"))
        except Exception:
            pass
    elif "COUNT=" in up:
        try:
            n = up.split("COUNT=", 1)[1].split(";", 1)[0]
            bits.append(f"{n} раз")
        except Exception:
            pass
    return ", ".join(bits)


def next_after(when: datetime, rrule_line: str, *, after: datetime | None = None) -> datetime | None:
    """Следующее срабатывание после after (по умолчанию = when)."""
    from dateutil.rrule import rrulestr

    line = str(rrule_line or "").strip()
    if not line:
        return None
    if not line.upper().startswith("RRULE:"):
        line = "RRULE:" + line
    base = when
    if base.tzinfo is None:
        base = base.replace(tzinfo=ZoneInfo("UTC"))
    anchor = after or base
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=base.tzinfo)
    try:
        rule = rrulestr(line, dtstart=base)
        nxt = rule.after(anchor, inc=False)
    except Exception:
        return None
    if nxt is None:
        return None
    if nxt.tzinfo is None and base.tzinfo is not None:
        nxt = nxt.replace(tzinfo=base.tzinfo)
    return nxt


def ensure_until_or_count(rrule: str, *, start: datetime, max_days: int = 400) -> str:
    """Если нет UNTIL/COUNT — ограничить, чтобы не создать бесконечную серию по ошибке модели."""
    up = rrule.upper()
    if "UNTIL=" in up or "COUNT=" in up:
        return rrule
    end = (start.date() if isinstance(start, datetime) else date.today()) + timedelta(days=max_days)
    sep = "" if rrule.endswith(":") else ";"
    return f"{rrule}{sep}UNTIL={end.strftime('%Y%m%d')}T235959Z"
