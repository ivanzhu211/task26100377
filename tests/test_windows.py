"""Tests for compile_windows, verified against per-second direct membership."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from maintwin import WeeklyWindow, compile_windows


def utc(y, mo, d, h=0, mi=0, s=0) -> int:
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def reference_intervals(tz_name: str, rules: list[WeeklyWindow], begin: int, end: int):
    """Per-second ground truth: t is covered iff its local weekday/clock is
    inside at least one weekly rule (cross-midnight rules attach their
    early-morning part to the previous local civil date)."""
    tz = ZoneInfo(tz_name)
    by_weekday: dict[int, list[tuple[int, int]]] = {}
    for w in rules:
        by_weekday.setdefault(w.weekday, []).append((w.start_second, w.end_second))

    def covered(ts: int) -> bool:
        local = datetime.fromtimestamp(ts, tz)
        clock = local.hour * 3600 + local.minute * 60 + local.second
        wd = local.weekday()
        for s, e in by_weekday.get(wd, ()):
            if s < e and s <= clock < e:
                return True
            if s > e and clock >= s:
                return True
        prev_wd = (wd - 1) % 7
        for s, e in by_weekday.get(prev_wd, ()):
            if s > e and clock < e:
                return True
        return False

    out = []
    t = begin
    while t < end:
        if covered(t):
            s = t
            while t < end and covered(t):
                t += 1
            out.append((s, t))
        else:
            t += 1
    return out


def check_against_reference(tz_name, rules, begin, end):
    expected = reference_intervals(tz_name, rules, begin, end)
    actual = compile_windows(tz_name, rules, begin, end)
    assert actual == expected
    return actual


class TestNewYorkFallBack:
    TZ = "America/New_York"
    RULE = WeeklyWindow(6, 1 * 3600 + 15 * 60, 1 * 3600 + 45 * 60)  # Sun 01:15-01:45

    def test_repeated_hour_yields_two_segments(self):
        begin, end = utc(2024, 11, 2), utc(2024, 11, 5)
        actual = compile_windows(self.TZ, [self.RULE], begin, end)
        assert actual == [
            (utc(2024, 11, 3, 5, 15), utc(2024, 11, 3, 5, 45)),
            (utc(2024, 11, 3, 6, 15), utc(2024, 11, 3, 6, 45)),
        ]

    def test_boundaries_match_reference(self):
        check_against_reference(self.TZ, [self.RULE], utc(2024, 11, 2), utc(2024, 11, 5))


class TestNewYorkSpringForward:
    TZ = "America/New_York"
    RULE = WeeklyWindow(6, 2 * 3600 + 10 * 60, 2 * 3600 + 50 * 60)  # Sun 02:10-02:50

    def test_nonexistent_window_yields_nothing(self):
        begin, end = utc(2024, 3, 9), utc(2024, 3, 12)
        assert compile_windows(self.TZ, [self.RULE], begin, end) == []

    def test_window_straddling_the_gap(self):
        # 01:30-03:30 local: 01:30-02:00 (EST) and 03:00-03:30 (EDT) both
        # exist; the skipped 02:00-03:00 part vanishes and the two UTC
        # segments are adjacent, merging into one 06:30-07:30 interval.
        rule = WeeklyWindow(6, 1 * 3600 + 30 * 60, 3 * 3600 + 30 * 60)
        begin, end = utc(2024, 3, 9), utc(2024, 3, 12)
        actual = compile_windows(self.TZ, [rule], begin, end)
        assert actual == [(utc(2024, 3, 10, 6, 30), utc(2024, 3, 10, 7, 30))]

    def test_boundaries_match_reference(self):
        check_against_reference(self.TZ, [self.RULE], utc(2024, 3, 9), utc(2024, 3, 12))
        straddling = WeeklyWindow(6, 1 * 3600 + 30 * 60, 3 * 3600 + 30 * 60)
        check_against_reference(self.TZ, [straddling], utc(2024, 3, 9), utc(2024, 3, 12))


class TestLordHoweHalfHour:
    TZ = "Australia/Lord_Howe"

    def test_spring_forward_30min(self):
        # 2024-10-06: clocks jump 02:00 -> 02:30. Window 02:10-02:40 local
        # keeps only the existing 02:30-02:40 part (10 min of UTC).
        rule = WeeklyWindow(6, 2 * 3600 + 10 * 60, 2 * 3600 + 40 * 60)
        begin, end = utc(2024, 10, 5), utc(2024, 10, 8)
        check_against_reference(self.TZ, [rule], begin, end)

    def test_fall_back_30min_repeated(self):
        # 2024-04-07: clocks fall back 02:00 -> 01:30. Window 01:40-02:10
        # local: 01:40-02:00 occurs twice (20+20 min), 02:00-02:10 once
        # (10 min); both UTC segments must appear, totalling 50 minutes.
        rule = WeeklyWindow(6, 1 * 3600 + 40 * 60, 2 * 3600 + 10 * 60)
        begin, end = utc(2024, 4, 6), utc(2024, 4, 9)
        actual = check_against_reference(self.TZ, [rule], begin, end)
        assert len(actual) == 2
        assert sum(e - s for s, e in actual) == 50 * 60


class TestApiaSkippedDay:
    TZ = "Pacific/Apia"

    def test_friday_2011_12_30_never_existed(self):
        # Apia skipped 2011-12-30 (Friday) entirely.
        rule = WeeklyWindow(4, 10 * 3600, 12 * 3600)  # Friday 10:00-12:00
        begin, end = utc(2011, 12, 28), utc(2012, 1, 2)
        assert compile_windows(self.TZ, [rule], begin, end) == []

    def test_cross_midnight_window_cut_by_skip(self):
        # Thursday 22:00 -> Friday 02:00: Friday never happens, so the window
        # stops at the skip instant instead of being shifted forward.
        rule = WeeklyWindow(3, 22 * 3600, 2 * 3600)
        begin, end = utc(2011, 12, 28), utc(2012, 1, 2)
        actual = check_against_reference(self.TZ, [rule], begin, end)
        assert len(actual) == 1
        s, e = actual[0]
        assert s == utc(2011, 12, 30, 8, 0)  # Thu 22:00 local at UTC-10 (DST)
        assert e - s == 2 * 3600  # only Thu 22:00..24:00 exists

    def test_window_after_skip_not_duplicated(self):
        # Saturday 00:30-01:30 local must appear exactly once, right after
        # the jump, not also 24h earlier.
        rule = WeeklyWindow(5, 30 * 60, 1 * 3600 + 30 * 60)
        begin, end = utc(2011, 12, 28), utc(2012, 1, 2)
        actual = check_against_reference(self.TZ, [rule], begin, end)
        assert len(actual) == 1
        assert actual[0][1] - actual[0][0] == 3600


class TestGeneralProperties:
    def test_sorted_merged_non_adjacent(self):
        rules = [
            WeeklyWindow(1, 8 * 3600, 10 * 3600),
            WeeklyWindow(1, 9 * 3600, 11 * 3600),  # overlaps previous
            WeeklyWindow(1, 11 * 3600, 12 * 3600),  # adjacent to merged
        ]
        begin, end = utc(2024, 6, 1), utc(2024, 7, 1)
        actual = compile_windows("UTC", rules, begin, end)
        for (s1, e1), (s2, e2) in zip(actual, actual[1:]):
            assert s1 < e1 < s2  # strictly separated, not touching
        assert actual[0] == (utc(2024, 6, 4, 8), utc(2024, 6, 4, 12))

    def test_clipped_to_begin_end(self):
        rules = [WeeklyWindow(2, 0, 12 * 3600)]
        begin, end = utc(2024, 6, 5, 6), utc(2024, 6, 5, 18)
        actual = compile_windows("UTC", rules, begin, end)
        assert actual == [(begin, utc(2024, 6, 5, 12))]

    def test_cross_midnight_utc(self):
        rules = [WeeklyWindow(0, 22 * 3600, 2 * 3600)]  # Mon 22:00 - Tue 02:00
        begin, end = utc(2024, 6, 3), utc(2024, 6, 5)
        actual = compile_windows("UTC", rules, begin, end)
        assert actual == [(utc(2024, 6, 3, 22), utc(2024, 6, 4, 2))]

    def test_overlapping_rules_random_reference(self):
        rules = [
            WeeklyWindow(3, 23 * 3600, 1 * 3600),
            WeeklyWindow(4, 0, 6 * 3600),
            WeeklyWindow(4, 5 * 3600 + 1800, 20 * 3600),
            WeeklyWindow(5, 12 * 3600, 18 * 3600),
        ]
        check_against_reference("Europe/Berlin", rules, utc(2024, 10, 24), utc(2024, 10, 29))

    def test_validation(self):
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(7, 0, 1)], 0, 100)
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 86400, 1)], 0, 100)
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 100, 100)], 0, 100)
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 1)] * 33, 0, 100)
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 1)], 100, 100)
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 1)], 0, 101 * 366 * 86400)

    def test_empty_rules(self):
        assert compile_windows("UTC", [], 0, 10**6) == []
