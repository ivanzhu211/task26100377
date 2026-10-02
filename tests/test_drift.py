"""Tests for detect_drift, including integration with compile_windows."""

from __future__ import annotations

import calendar

import pytest

from maintwin import WeeklyWindow, compile_windows, detect_drift


def utc(y, mo, d, h=0, mi=0, s=0) -> int:
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


W = [(1000, 2000)]  # one maintenance window


class TestBaselineLogic:
    def test_first_observation_only_sets_baseline(self):
        assert detect_drift([(500, "a")], W) == []
        assert detect_drift([(1500, "a")], W) == []  # inside window, still no alert

    def test_change_outside_window_alerts(self):
        alerts = detect_drift([(100, "a"), (300, "b")], W)
        assert len(alerts) == 1
        a = alerts[0]
        assert (a.timestamp, a.old_hash, a.new_hash) == (300, "a", "b")

    def test_change_inside_window_suppressed(self):
        assert detect_drift([(100, "a"), (1500, "b")], W) == []

    def test_baseline_updates_on_every_observation(self):
        # b accepted inside the window; same b after the window must not
        # re-alert (the in-window change is not replayed afterwards).
        assert detect_drift([(100, "a"), (1500, "b"), (3000, "b")], W) == []

    def test_change_after_accepted_window_change_alerts_with_latest_baseline(self):
        alerts = detect_drift([(100, "a"), (1500, "b"), (3000, "c")], W)
        assert len(alerts) == 1
        a = alerts[0]
        assert (a.timestamp, a.old_hash, a.new_hash) == (3000, "b", "c")

    def test_window_without_observations_does_not_reset_baseline(self):
        # No observation inside the window: the first change after the
        # window is still compared against the pre-window baseline and must
        # not be silently swallowed.
        alerts = detect_drift([(100, "a"), (3000, "b")], W)
        assert len(alerts) == 1
        assert (alerts[0].old_hash, alerts[0].new_hash) == ("a", "b")

    def test_window_boundaries(self):
        # start is inclusive (inside), end is exclusive (outside).
        obs = [(0, "a"), (1000, "b"), (2000, "c")]
        alerts = detect_drift(obs, W)
        assert [a.timestamp for a in alerts] == [2000]
        assert (alerts[0].old_hash, alerts[0].new_hash) == ("b", "c")

    def test_same_hash_never_alerts(self):
        assert detect_drift([(100, "a"), (300, "a"), (5000, "a")], W) == []

    def test_empty_observations(self):
        assert detect_drift([], W) == []

    def test_multiple_windows_and_alert_chain(self):
        intervals = [(1000, 2000), (3000, 4000)]
        obs = [(0, "a"), (1500, "b"), (2500, "c"), (3500, "d"), (4500, "e")]
        alerts = detect_drift(obs, intervals)
        # 1500 in window (a->b silent), 2500 outside (b->c alert),
        # 3500 in window (c->d silent), 4500 outside (d->e alert).
        assert [(a.timestamp, a.old_hash, a.new_hash) for a in alerts] == [
            (2500, "b", "c"),
            (4500, "d", "e"),
        ]

    def test_timestamps_must_be_strictly_increasing(self):
        with pytest.raises(ValueError):
            detect_drift([(100, "a"), (100, "b")], W)
        with pytest.raises(ValueError):
            detect_drift([(200, "a"), (100, "b")], W)


class TestFallBackIntegration:
    """NY 2024-11-03: Sunday 01:15-01:45 local maps to two UTC segments
    (05:15-05:45 EDT and 06:15-06:45 EST). The 05:45-06:15 gap between the
    repeated local times is NOT maintenance time and must alert."""

    TZ = "America/New_York"
    RULE = WeeklyWindow(6, 1 * 3600 + 15 * 60, 1 * 3600 + 45 * 60)

    def intervals(self):
        return compile_windows(self.TZ, [self.RULE], utc(2024, 11, 2), utc(2024, 11, 5))

    def test_change_between_repeated_segments_alerts(self):
        intervals = self.intervals()
        assert len(intervals) == 2
        obs = [
            (utc(2024, 11, 3, 5, 0), "a"),   # before first segment
            (utc(2024, 11, 3, 5, 30), "b"),  # inside first segment: silent
            (utc(2024, 11, 3, 6, 0), "c"),   # gap between segments: ALERT
            (utc(2024, 11, 3, 6, 30), "d"),  # inside second segment: silent
            (utc(2024, 11, 3, 7, 0), "e"),   # after window: ALERT
        ]
        alerts = detect_drift(obs, intervals)
        assert [(a.timestamp, a.old_hash, a.new_hash) for a in alerts] == [
            (utc(2024, 11, 3, 6, 0), "b", "c"),
            (utc(2024, 11, 3, 7, 0), "d", "e"),
        ]

    def test_before_inside_after_sequence(self):
        intervals = self.intervals()
        obs = [
            (utc(2024, 11, 3, 4, 0), "a"),   # baseline
            (utc(2024, 11, 3, 5, 0), "b"),   # before window: ALERT a->b
            (utc(2024, 11, 3, 5, 20), "c"),  # inside: silent
            (utc(2024, 11, 3, 8, 0), "d"),   # after: ALERT c->d
        ]
        alerts = detect_drift(obs, intervals)
        assert [(a.old_hash, a.new_hash) for a in alerts] == [("a", "b"), ("c", "d")]

    def test_exact_window_edges(self):
        intervals = self.intervals()
        start, end = intervals[0]
        obs = [
            (start - 1, "a"),
            (start, "b"),      # exactly at start: inside, silent
            (end - 1, "c"),    # last second inside: silent
            (end, "d"),        # exactly at end: outside, ALERT c->d
        ]
        alerts = detect_drift(obs, intervals)
        assert [(a.timestamp, a.old_hash, a.new_hash) for a in alerts] == [
            (end, "c", "d")
        ]

    def test_no_observation_inside_window(self):
        intervals = self.intervals()
        obs = [
            (utc(2024, 11, 3, 4, 0), "a"),  # before window
            (utc(2024, 11, 3, 8, 0), "b"),  # after window, none inside
        ]
        alerts = detect_drift(obs, intervals)
        assert len(alerts) == 1
        assert (alerts[0].old_hash, alerts[0].new_hash) == ("a", "b")
