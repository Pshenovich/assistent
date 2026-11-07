"""Зеркало внешних календарей и нагрузка календаря."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from assistant.services import calendar_load as cl
from assistant.stores import calendar_mirror as cm

TZ = ZoneInfo("Europe/Moscow")
MON = datetime(2026, 11, 2, tzinfo=TZ)  # понедельник


@pytest.fixture(autouse=True)
def mirror_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CALENDAR_MIRROR_DB_PATH", str(tmp_path / "mirror.sqlite"))
    yield
    cm._CONN = None  # type: ignore[attr-defined]


def ev(eid, start, mins, *, cal="primary", others=("a@x.ru",), me="accepted", **raw):
    st = start
    body = {"id": eid, "summary": eid, "start": {"dateTime": st.isoformat()},
            "end": {"dateTime": (st + timedelta(minutes=mins)).isoformat()},
            "attendees": [{"email": "me@x.ru", "self": True, "responseStatus": me}] + [{"email": o} for o in others]}
    if not others:
        body["attendees"] = []
    body.update(raw)
    return {"calendar_id": cal, "event_id": eid, "start": st, "end": st + timedelta(minutes=mins), "raw": body}


def test_record_window_upserts_and_marks_removed_as_cancelled() -> None:
    w0, w1 = MON, MON + timedelta(days=7)
    a, b = ev("A", MON.replace(hour=10), 60), ev("B", MON.replace(hour=12), 30, cal="work")
    assert cm.record_window(1, [a, b], time_min=w0, time_max=w1, calendar_ids=["primary", "work"]) == 2
    moved = ev("A", MON.replace(hour=11), 60)
    # календарь work не ответил — его события не трогаем
    cm.record_window(1, [moved], time_min=w0, time_max=w1, calendar_ids=["primary"])
    got = {e["event_id"]: e for e in cm.events(1, w0, w1)}
    assert set(got) == {"A", "B"} and got["A"]["start"].astimezone(TZ).hour == 11
    cm.record_window(1, [], time_min=w0, time_max=w1, calendar_ids=["primary", "work"])
    assert cm.events(1, w0, w1) == []
    # прошлое остаётся: окно позже не трогает старые события
    cm.record_window(1, [a], time_min=w0, time_max=w1, calendar_ids=["primary"])
    cm.record_window(1, [], time_min=w1, time_max=w1 + timedelta(days=7), calendar_ids=["primary"])
    assert [e["event_id"] for e in cm.events(1, w0, w1)] == ["A"]
    assert cm.synced_from(1) == w0.astimezone(timezone.utc)


def test_listing_window_feeds_mirror_but_search_does_not(monkeypatch) -> None:
    from assistant.services import calendar_sources

    raw = ev("Планёрка", MON.replace(hour=10), 60)["raw"]

    class Svc:
        def events(self):
            return self

        def list(self, **kw):
            return self

        def execute(self):
            return {"items": [raw]}

    monkeypatch.setattr(calendar_sources, "_service", lambda uid: Svc())
    monkeypatch.setattr(calendar_sources, "get_active_calendar_ids", lambda uid: ["primary"])
    import assistant.services.calendar as cal

    monkeypatch.setattr(cal, "_tz_for", lambda uid: TZ)
    calendar_sources.list_events_in_window(7, MON, MON + timedelta(days=1), query="план")
    assert cm.events(7, MON, MON + timedelta(days=1)) == []
    calendar_sources.list_events_in_window(7, MON, MON + timedelta(days=1))
    assert [e["title"] for e in cm.events(7, MON, MON + timedelta(days=1))] == ["Планёрка"]


def items(*evs):
    out = []
    for e in evs:
        it = cl._from_gcal("google", e["raw"], e["start"], e["end"])
        if it:
            out.append(it)
    return out


def test_day_stats_union_focus_after_hours_and_exclusions() -> None:
    d = MON
    its = items(
        ev("Планёрка", d.replace(hour=10), 60),
        ev("Пересекается", d.replace(hour=10, minute=30), 60),  # объединение 10:00–11:30
        ev("Отклонил", d.replace(hour=14), 120, me="declined"),
        ev("Свободен", d.replace(hour=15), 60, transparency="transparent"),
        ev("Свой блок", d.replace(hour=16), 120, others=()),
        ev("Вечером", d.replace(hour=19, minute=30), 60),
    )
    st = cl.day_stats(its, d.date(), TZ)
    assert st["meeting"] == 150 and st["after_hours"] == 60
    # окна без встреч в 9–19: 11:30–19:00 = 450 мин (9:00–10:00 — меньше 2 часов)
    assert st["focus"] == 450


def test_summary_counts_workdays_skips_absence_ooo_and_weekend(monkeypatch) -> None:
    week = [ev(f"M{i}", (MON + timedelta(days=i)).replace(hour=10), 480) for i in range(5)]
    week.append(ev("Суббота", (MON + timedelta(days=5)).replace(hour=12), 120))
    ooo = ev("Нет на месте", MON + timedelta(days=3), 24 * 60, others=(), eventType="outOfOffice")
    ooo["raw"]["start"] = {"date": "2026-11-05"}
    data = {"items": items(*week, ooo), "google": True, "covered_from": MON - timedelta(days=30)}
    s = cl.summary(1, start=MON, end=MON + timedelta(days=7), now=MON + timedelta(days=7), tz=TZ,
                   absences=[{"from": "2026-11-06", "to": "2026-11-06"}], data=data)
    # четверг — «нет на месте», пятница — отпуск: остаются пн–ср
    assert s["workdays"] == 3 and s["meeting_hours_per_day"] == 8.0
    assert s["low_focus_days"] == 3 and s["after_hours"] == 2.0 and s["covered_from"] is None
    assert s["enough_data"] is False


def test_summary_without_history_and_without_calendar() -> None:
    assert cl.summary(1, start=MON, end=MON + timedelta(days=7), now=MON + timedelta(days=7), tz=TZ,
                      data={"items": [], "google": False, "covered_from": None}) == {"has_calendar": False}
    late = cl.summary(1, start=MON, end=MON + timedelta(days=7), now=MON + timedelta(days=7), tz=TZ,
                      data={"items": [], "google": True, "covered_from": MON + timedelta(days=2)})
    assert late["workdays"] == 3 and late["covered_from"]


def test_mirror_job_backfills_once(monkeypatch) -> None:
    from assistant.bot import calendar_mirror_job as job
    from assistant.services import calendar_sources

    seen = []
    monkeypatch.setattr(calendar_sources, "list_events_in_window",
                        lambda uid, a, b: seen.append((b - a).days) or [])
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    job.sync_user(5, now=now)
    cm.record_window(5, [], time_min=now - timedelta(days=91), time_max=now, calendar_ids=["primary"])
    job.sync_user(5, now=now)
    assert seen == [91 + 14, 14 + 14]


def test_mirror_keeps_event_content() -> None:
    e = ev("Демо", MON.replace(hour=10), 60, description="Повестка: цены", location="Переговорка 2",
           htmlLink="https://calendar.google.com/e/1", hangoutLink="https://meet.google.com/abc-defg-hij",
           organizer={"email": "Boss@X.ru"})
    e["raw"]["attendees"][1].update(displayName="Аня", responseStatus="tentative")
    cm.record_window(1, [e], time_min=MON, time_max=MON + timedelta(days=1), calendar_ids=["primary"])
    got = cm.events(1, MON, MON + timedelta(days=1))[0]
    assert got["description"] == "Повестка: цены" and got["location"] == "Переговорка 2"
    assert got["meeting_url"] == "https://meet.google.com/abc-defg-hij" and got["organizer_email"] == "boss@x.ru"
    assert got["html_link"].endswith("/e/1")
    assert {"email": "a@x.ru", "displayName": "Аня", "responseStatus": "tentative"} in got["attendees"]
