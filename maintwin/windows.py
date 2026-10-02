"""Compilation of weekly local-time maintenance windows into UTC intervals."""

from __future__ import annotations

import bisect
import calendar
from datetime import datetime, timedelta, timezone as _dt_timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from .types import WeeklyWindow

_EPOCH_NAIVE = datetime(1970, 1, 1)
_MIN_BEGIN = 0
_MAX_END = calendar.timegm((2100, 1, 1, 0, 0, 0, 0, 0, 0))
_MAX_SPAN = 100 * 365 * 86400 + 25 * 86400  # 100 years incl. leap days
_MAX_RULES = 32
_DAY = 86400
_TRANSITION_STEP = 6 * 3600
_MARGIN = 2 * _DAY


def _offset_at(tz: ZoneInfo, ts: int) -> int:
    return int(datetime.fromtimestamp(ts, tz).utcoffset().total_seconds())


def _find_transitions(tz: ZoneInfo, lo: int, hi: int) -> list[int]:
    """All UTC instants in [lo, hi) where the UTC offset changes."""
    transitions: list[int] = []
    prev_ts = lo
    prev_off = _offset_at(tz, lo)
    ts = lo + _TRANSITION_STEP
    while True:
        cur_ts = min(ts, hi)
        cur_off = _offset_at(tz, cur_ts)
        if cur_off != prev_off:
            a, b = prev_ts, cur_ts
            while b - a > 1:
                mid = (a + b) // 2
                if _offset_at(tz, mid) == prev_off:
                    a = mid
                else:
                    b = mid
            transitions.append(b)
            prev_off = cur_off
        if cur_ts >= hi:
            break
        prev_ts = cur_ts
        ts += _TRANSITION_STEP
    return transitions


def _local_to_utc_preimages(tz: ZoneInfo, lt: datetime) -> list[int]:
    """All UTC seconds whose local wall clock equals naive `lt` (0, 1 or 2)."""
    out: list[int] = []
    for fold in (0, 1):
        aware = lt.replace(tzinfo=tz, fold=fold)
        ts = int(aware.timestamp())
        back = datetime.fromtimestamp(ts, tz).replace(tzinfo=None)
        if back == lt and ts not in out:
            out.append(ts)
    return out


def _window_to_utc(
    tz: ZoneInfo,
    transitions: list[int],
    ls: datetime,
    le: datetime,
) -> list[tuple[int, int]]:
    """UTC preimage of the local half-open interval [ls, le)."""
    est_a = int(ls.replace(tzinfo=tz, fold=0).timestamp())
    est_b = int(le.replace(tzinfo=tz, fold=0).timestamp())
    lo = min(est_a, est_b) - _MARGIN
    hi = max(est_a, est_b) + _MARGIN

    left = bisect.bisect_left(transitions, lo)
    right = bisect.bisect_right(transitions, hi)
    nearby = transitions[left:right]

    if not nearby:
        # No offset change anywhere near: a single plain shifted interval.
        off = _offset_at(tz, (est_a + est_b) // 2)
        start = int((ls - _EPOCH_NAIVE).total_seconds()) - off
        end = int((le - _EPOCH_NAIVE).total_seconds()) - off
        return [(start, end)] if start < end else []

    candidates = sorted(
        set(
            _local_to_utc_preimages(tz, ls)
            + _local_to_utc_preimages(tz, le)
            + nearby
        )
    )
    out: list[tuple[int, int]] = []
    for a, b in zip(candidates, candidates[1:]):
        if a >= b:
            continue
        mid = (a + b) // 2
        wall = datetime.fromtimestamp(mid, tz).replace(tzinfo=None)
        if ls <= wall < le:
            if out and out[-1][1] == a:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return out


def _validate(timezone_name: str, weekly_windows: Sequence[WeeklyWindow], begin: int, end: int) -> None:
    if not _MIN_BEGIN <= begin < end <= _MAX_END:
        raise ValueError("begin/end must satisfy 0 <= begin < end <= 2100-01-01 UTC")
    if end - begin > _MAX_SPAN:
        raise ValueError("query span exceeds 100 years")
    if len(weekly_windows) > _MAX_RULES:
        raise ValueError("at most 32 weekly windows are allowed")
    for w in weekly_windows:
        if not 0 <= w.weekday <= 6:
            raise ValueError(f"weekday out of range: {w.weekday}")
        if not (0 <= w.start_second <= 86399 and 0 <= w.end_second <= 86399):
            raise ValueError("start_second/end_second must be in [0, 86399]")
        if w.start_second == w.end_second:
            raise ValueError("start_second and end_second must differ")


def compile_windows(
    timezone: str,
    weekly_windows: Sequence[WeeklyWindow],
    begin: int,
    end: int,
) -> list[tuple[int, int]]:
    """Compile weekly local maintenance windows into sorted UTC intervals.

    Returns maximal, pairwise non-overlapping and non-adjacent half-open
    [start, end) UTC intervals, clipped to [begin, end). For every UTC
    second t in [begin, end), t is covered iff the local weekday/clock time
    of t in `timezone` falls inside at least one weekly rule.
    """
    _validate(timezone, weekly_windows, begin, end)
    tz = ZoneInfo(timezone)
    if not weekly_windows:
        return []

    transitions = _find_transitions(tz, begin - 4 * _DAY - _MARGIN, end + 4 * _DAY + _MARGIN)

    by_weekday: dict[int, list[tuple[int, int]]] = {}
    for w in weekly_windows:
        by_weekday.setdefault(w.weekday, []).append((w.start_second, w.end_second))

    first_date = (datetime.fromtimestamp(begin, _dt_timezone.utc) - timedelta(days=3)).date()
    last_date = (datetime.fromtimestamp(end, _dt_timezone.utc) + timedelta(days=3)).date()

    intervals: list[tuple[int, int]] = []
    day = first_date
    one_day = timedelta(days=1)
    while day <= last_date:
        rules = by_weekday.get(day.weekday())
        if rules:
            for start_second, end_second in rules:
                ls = datetime.combine(day, datetime.min.time()) + timedelta(seconds=start_second)
                if end_second > start_second:
                    le = datetime.combine(day, datetime.min.time()) + timedelta(seconds=end_second)
                else:
                    le = datetime.combine(day + one_day, datetime.min.time()) + timedelta(seconds=end_second)
                intervals.extend(_window_to_utc(tz, transitions, ls, le))
        day += one_day

    clipped = sorted(
        (max(s, begin), min(e, end)) for s, e in intervals if s < end and e > begin
    )
    merged: list[list[int]] = []
    for s, e in clipped:
        if s >= e:
            continue
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]
