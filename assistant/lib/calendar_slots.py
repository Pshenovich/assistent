"""Слоты встреч: не предлагать время в прошлом."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def compute_earliest_bookable_start(
    *,
    now: datetime,
    day: date,
    work_start: datetime,
    work_end: datetime,
    grace_min: int = 15,
    step_min: int = 30,
) -> datetime:
    """Минимальное начало слота: для сегодня — не раньше now+grace, шаг от work_start."""
    if day < now.date():
        return work_end
    if day > now.date():
        return work_start
    step = timedelta(minutes=max(15, step_min))
    floor = (now + timedelta(minutes=max(0, grace_min))).replace(second=0, microsecond=0)
    if floor >= work_end:
        return work_end
    if floor <= work_start:
        return work_start
    elapsed = floor - work_start
    n_steps = int(elapsed // step)
    if elapsed % step:
        n_steps += 1
    return min(work_start + n_steps * step, work_end)


def filter_future_slot_starts(
    starts: list[datetime],
    *,
    not_before: datetime,
    work_end: datetime,
    duration: timedelta,
) -> list[datetime]:
    """Оставить только слоты, которые ещё не начались и помещаются до конца дня."""
    out: list[datetime] = []
    for st in starts:
        if st < not_before:
            continue
        if st + duration > work_end:
            continue
        out.append(st)
    return out
