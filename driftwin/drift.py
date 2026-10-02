"""基于编译后维护窗的内容漂移判定。"""

from __future__ import annotations

from bisect import bisect_right

from .types import DriftAlert, Interval, Observation, to_unix_seconds


def _normalize_observation(obs) -> Observation:
    if isinstance(obs, dict):
        t, h = obs["timestamp"], obs["hash"]
    else:
        t, h = obs
    t = to_unix_seconds(t)
    if not isinstance(h, str) or len(h) != 64:
        raise ValueError("哈希必须是 64 位十六进制字符串（SHA-256）")
    if h.lower() != h or not all(c in "0123456789abcdef" for c in h):
        raise ValueError("哈希必须是小写十六进制")
    return t, h


def detect_drift(
    observations,
    intervals,
) -> list[DriftAlert]:
    """判定内容漂移，返回需要报警的 (时间, 旧哈希, 新哈希)。

    observations: 时间严格递增的 (时间戳, sha256 哈希) 序列。
    intervals: compile_windows 的输出（排序、不重叠、左闭右开）。
    首条观测只建立基线；之后每条与上一条哈希比较，不同且当前观测不在
    维护窗内才报警；无论是否报警，每条观测都会更新基线。
    """
    obs = [_normalize_observation(o) for o in observations]
    for prev, cur in zip(obs, obs[1:]):
        if cur[0] <= prev[0]:
            raise ValueError("观测时间必须严格递增")

    starts = [iv[0] for iv in intervals]
    ends = [iv[1] for iv in intervals]

    def in_maintenance(t: int) -> bool:
        i = bisect_right(starts, t) - 1
        return i >= 0 and t < ends[i]

    alerts: list[DriftAlert] = []
    baseline: str | None = None
    for t, h in obs:
        if baseline is None:
            baseline = h
            continue
        if h != baseline and not in_maintenance(t):
            alerts.append((t, baseline, h))
        baseline = h
    return alerts
