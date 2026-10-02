"""Scale test: 100-year span, 32 weekly rules, with timing output."""

from __future__ import annotations

import calendar
import random
import time

from maintwin import WeeklyWindow, compile_windows, detect_drift
from tests.test_windows import reference_intervals


def utc(y, mo, d, h=0, mi=0, s=0) -> int:
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def make_rules(seed: int = 7) -> list[WeeklyWindow]:
    rng = random.Random(seed)
    rules = []
    for _ in range(32):
        weekday = rng.randrange(7)
        a = rng.randrange(0, 86400)
        b = rng.randrange(0, 86400)
        if a == b:
            b = (b + 3600) % 86400
        rules.append(WeeklyWindow(weekday, a, b))
    return rules


def test_hundred_year_scale(capsys):
    tz = "America/New_York"
    rules = make_rules()
    begin, end = utc(1970, 1, 1), utc(2070, 1, 1)

    t0 = time.perf_counter()
    intervals = compile_windows(tz, rules, begin, end)
    compile_s = time.perf_counter() - t0

    assert intervals, "expected many compiled intervals"
    for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
        assert begin <= s1 < e1 < s2 <= end  # sorted, separated, clipped

    # Drift detection over a dense observation series (every 6h for 100y).
    obs = []
    digest = 0
    t = begin
    while t < end:
        digest += 1
        obs.append((t, f"{digest:064x}"))
        t += 6 * 3600
    t0 = time.perf_counter()
    alerts = detect_drift(obs, intervals)
    drift_s = time.perf_counter() - t0

    with capsys.disabled():
        print(
            f"\n[scale] 100y x 32 rules: {len(intervals)} intervals, "
            f"compile={compile_s:.3f}s; {len(obs)} observations, "
            f"{len(alerts)} alerts, detect={drift_s:.3f}s"
        )
    assert compile_s < 30.0
    assert drift_s < 10.0


def test_compiled_boundaries_match_reference_on_transition_weeks():
    """Spot-check exact boundaries against per-second reference on weeks
    containing DST transitions inside the 100-year span."""
    tz = "America/New_York"
    rules = make_rules()
    for week_start in (
        utc(1987, 4, 5),    # spring forward (pre-2007 rules)
        utc(2007, 3, 11),   # spring forward (post-2007 rules)
        utc(2039, 11, 6),   # fall back
        utc(2069, 10, 27),  # fall back
    ):
        begin = week_start - 2 * 86400
        end = week_start + 6 * 86400
        expected = reference_intervals(tz, rules, begin, end)
        actual = compile_windows(tz, rules, begin, end)
        assert actual == expected
