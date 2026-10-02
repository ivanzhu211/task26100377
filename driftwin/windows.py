"""将每周重复的当地维护窗编译为 UTC 区间。

核心思路：
- 先用“大步扫描 + 二分”找出查询范围内所有的 UTC 偏移切换点（不逐秒扫描）。
- 每个切换点在当地（naive）时间轴上对应两个边界点；回拨时两点之间的当地
  时间出现两次，前跳时两点之间的当地时间不存在。
- 对每个当地日期、每条匹配当周星期几的规则，生成当地窗口，按边界点切分
  成原子段；对每段取中点做 roundtrip 判断：
    * 0 个有效偏移 -> 当地时间不存在，不产生 UTC 区间；
    * 1 个有效偏移 -> 正常映射为一段 UTC 区间；
    * 2 个有效偏移 -> 回拨重复时段，映射为两段 UTC 区间。
- 最后合并重叠/相邻区间并裁剪到 [begin, end)。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone as _tz
from zoneinfo import ZoneInfo

from .types import (
    Interval,
    MAX_SPAN_SECONDS,
    MAX_UNIX_SECOND,
    MAX_WEEKLY_WINDOWS,
    MIN_UNIX_SECOND,
    SECONDS_PER_DAY,
    WeeklyWindow,
    to_unix_seconds,
)

_EPOCH_DATE = date(1970, 1, 1)
_EPOCH_NAIVE = datetime(1970, 1, 1)
# 当地日期向两侧多覆盖的天数，足以容纳任何时区偏移（<=14h）与跨午夜窗口
_MARGIN_DAYS = 2
# 扫描偏移切换点的步长；真实时区在 12 小时内不会有两次切换
_TRANSITION_SCAN_STEP = SECONDS_PER_DAY // 2


def _normalize_rule(rule) -> WeeklyWindow:
    if isinstance(rule, WeeklyWindow):
        w = rule
    elif isinstance(rule, dict):
        w = WeeklyWindow(
            rule["weekday"], rule["start_second"], rule["end_second"]
        )
    else:
        weekday, start_second, end_second = rule
        w = WeeklyWindow(weekday, start_second, end_second)
    if not 0 <= w.weekday <= 6:
        raise ValueError(f"weekday 必须在 0..6: {w.weekday}")
    if not 0 <= w.start_second < SECONDS_PER_DAY:
        raise ValueError(f"start_second 必须在 0..86399: {w.start_second}")
    if not 0 <= w.end_second < SECONDS_PER_DAY:
        raise ValueError(f"end_second 必须在 0..86399: {w.end_second}")
    if w.start_second == w.end_second:
        raise ValueError("start_second 与 end_second 不能相同")
    return w


def _offset_at(tz: ZoneInfo, unix_second: int) -> int:
    """指定 UTC 秒时该时区的偏移（秒）。"""
    dt = datetime.fromtimestamp(unix_second, tz)
    return int(dt.utcoffset().total_seconds())


def _find_transitions(tz: ZoneInfo, lo: int, hi: int) -> list[tuple[int, int, int]]:
    """找出 [lo, hi) 内所有偏移切换点。

    返回 [(utc_second, offset_before, offset_after), ...]，按时间排序。
    """
    transitions: list[tuple[int, int, int]] = []
    prev_offset = _offset_at(tz, lo)
    u = lo
    while u < hi:
        v = min(u + _TRANSITION_SCAN_STEP, hi)
        offset = _offset_at(tz, v)
        if offset != prev_offset:
            # 二分：找 (u, v] 中第一个偏移不等于 prev_offset 的秒
            a, b = u, v
            while a + 1 < b:
                mid = (a + b) // 2
                if _offset_at(tz, mid) == prev_offset:
                    a = mid
                else:
                    b = mid
            transitions.append((b, prev_offset, offset))
            prev_offset = offset
        u = v
    return transitions


def _local_offsets(tz: ZoneInfo, local_second: int) -> list[int]:
    """naive 当地秒对应的所有有效 UTC 偏移（秒）。

    0 个：该当地时间不存在（前跳空隙）；1 个：正常；2 个：回拨重复。
    """
    naive = _EPOCH_NAIVE + timedelta(seconds=local_second)
    offsets: list[int] = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        offset = int(aware.utcoffset().total_seconds())
        if offset in offsets:
            continue
        utc_dt = aware.astimezone(_tz.utc)
        if utc_dt.astimezone(tz).replace(tzinfo=None) == naive:
            offsets.append(offset)
    return offsets


def _map_local_window(
    tz: ZoneInfo,
    local_start: int,
    local_end: int,
    boundaries: list[int],
) -> list[Interval]:
    """把一个当地 naive 窗口 [local_start, local_end) 映射为 0..n 段 UTC 区间。"""
    lo = bisect_right(boundaries, local_start)
    hi = bisect_left(boundaries, local_end)
    points = [local_start, *boundaries[lo:hi], local_end]
    intervals: list[Interval] = []
    for a, b in zip(points, points[1:]):
        if a >= b:
            continue
        for offset in _local_offsets(tz, (a + b) // 2):
            intervals.append((a - offset, b - offset))
    return intervals


def compile_windows(
    timezone: str,
    weekly_windows,
    begin,
    end,
) -> list[Interval]:
    """把每周重复的当地维护窗编译成 [begin, end) 内的 UTC 区间。

    timezone: IANA 时区名。
    weekly_windows: 最多 32 条 WeeklyWindow（也接受 dict 或三元组）。
    begin / end: int Unix 秒或带时区 datetime，左闭右开。
    返回按时间排序、互不重叠且不相邻的最大区间，全部裁剪到 [begin, end)。
    """
    begin_s = to_unix_seconds(begin)
    end_s = to_unix_seconds(end)
    if not MIN_UNIX_SECOND <= begin_s < end_s <= MAX_UNIX_SECOND:
        raise ValueError("查询范围必须满足 1970-01-01 <= begin < end <= 2100-01-01")
    if end_s - begin_s > MAX_SPAN_SECONDS:
        raise ValueError("单次查询跨度不能超过 100 年")

    rules = [_normalize_rule(r) for r in weekly_windows]
    if len(rules) > MAX_WEEKLY_WINDOWS:
        raise ValueError(f"周规则最多 {MAX_WEEKLY_WINDOWS} 条")
    if not rules:
        return []

    tz = ZoneInfo(timezone)

    margin = _MARGIN_DAYS * SECONDS_PER_DAY
    # 切换点搜索范围要比日期迭代范围更宽：跨午夜窗口最长可达 2 天，
    # 其 straddle 的切换点可能落在日期余量之外
    transitions = _find_transitions(
        tz, begin_s - margin - 4 * SECONDS_PER_DAY, end_s + margin + 4 * SECONDS_PER_DAY
    )
    # 每个切换点在当地时间轴上产生两个边界点
    boundaries = sorted(
        {t + off for t, before, after in transitions for off in (before, after)}
    )

    first_day = (begin_s - margin) // SECONDS_PER_DAY
    last_day = (end_s - 1 + margin) // SECONDS_PER_DAY

    intervals: list[Interval] = []
    for day in range(first_day, last_day + 1):
        weekday = (_EPOCH_DATE + timedelta(days=day)).weekday()
        day_start = day * SECONDS_PER_DAY
        for rule in rules:
            if rule.weekday != weekday:
                continue
            local_start = day_start + rule.start_second
            if rule.end_second > rule.start_second:
                local_end = day_start + rule.end_second
            else:  # 跨越当地次日
                local_end = day_start + SECONDS_PER_DAY + rule.end_second
            intervals.extend(
                _map_local_window(tz, local_start, local_end, boundaries)
            )

    return _merge_and_clip(intervals, begin_s, end_s)


def _merge_and_clip(
    intervals: list[Interval], begin: int, end: int
) -> list[Interval]:
    """合并重叠与相邻区间，并裁剪到 [begin, end)。"""
    merged: list[Interval] = []
    for start, stop in sorted(intervals):
        start = max(start, begin)
        stop = min(stop, end)
        if start >= stop:
            continue
        if merged and start <= merged[-1][1]:
            last = merged[-1]
            merged[-1] = (last[0], max(last[1], stop))
        else:
            merged.append((start, stop))
    return merged
