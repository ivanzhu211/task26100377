"""Content-drift detection against compiled maintenance windows."""

from __future__ import annotations

import bisect
from typing import Sequence

from .types import DriftAlert


def detect_drift(
    observations: Sequence[tuple[int, str]],
    intervals: Sequence[tuple[int, int]],
) -> list[DriftAlert]:
    """Compare each observation's hash with the running baseline.

    The first observation only establishes the baseline. A hash differing
    from the baseline raises an alert only when the observation timestamp is
    outside every maintenance interval. Every observation replaces the
    baseline, whether or not it alerted; windows never reset the baseline.
    """
    ts_list = [ts for ts, _ in observations]
    if any(b <= a for a, b in zip(ts_list, ts_list[1:])):
        raise ValueError("observation timestamps must be strictly increasing")

    starts = [s for s, _ in intervals]
    ends = [e for _, e in intervals]

    def in_window(ts: int) -> bool:
        i = bisect.bisect_right(starts, ts) - 1
        return i >= 0 and ts < ends[i]

    alerts: list[DriftAlert] = []
    if not observations:
        return alerts
    baseline = observations[0][1]
    for ts, digest in observations[1:]:
        if digest != baseline and not in_window(ts):
            alerts.append(DriftAlert(timestamp=ts, old_hash=baseline, new_hash=digest))
        baseline = digest
    return alerts
