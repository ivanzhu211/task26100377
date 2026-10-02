"""规模与性能测试：100 年跨度、32 条周规则，输出耗时。"""

import random
import time
from bisect import bisect_right
from datetime import datetime, timezone

from driftwin import WeeklyWindow, compile_windows, detect_drift

from test_windows import ref_membership

UTC = timezone.utc


def ts(y, mo=1, d=1):
    return int(datetime(y, mo, d, tzinfo=UTC).timestamp())


def make_32_rules(seed=7):
    rng = random.Random(seed)
    rules = []
    for i in range(32):
        start = rng.randrange(0, 86400)
        end = rng.randrange(0, 86400)
        if end == start:
            end = (end + 3600) % 86400
        rules.append(WeeklyWindow(rng.randrange(0, 7), start, end))
    return rules


def test_hundred_year_span_32_rules_performance():
    begin, end = ts(2000), ts(2100)
    rules = make_32_rules()

    t0 = time.perf_counter()
    intervals = compile_windows("America/New_York", rules, begin, end)
    compile_s = time.perf_counter() - t0

    # 结构性质：排序、互不重叠、互不相邻、裁剪在范围内
    for prev, cur in zip(intervals, intervals[1:]):
        assert prev[1] < cur[0], "区间必须既不重叠也不相邻"
    assert all(begin <= s < e <= end for s, e in intervals)

    # 漂移判定：10 万条观测，逐条二分查窗
    rng = random.Random(1)
    obs = []
    t = begin
    for i in range(100_000):
        t += rng.randrange(1, 60)
        obs.append((t, f"{i % 50:064x}"))
    t0 = time.perf_counter()
    alerts = detect_drift(obs, intervals)
    drift_s = time.perf_counter() - t0

    print(
        f"\n[规模] 100 年 x 32 条周规则: 编译 {compile_s:.3f}s, "
        f"区间数 {len(intervals)}; 10 万条观测漂移判定 {drift_s:.3f}s, "
        f"报警 {len(alerts)} 条"
    )
    assert compile_s < 30, "编译耗时异常（疑似逐秒/逐分钟扫描）"


def test_compiled_windows_match_reference_on_sampled_days():
    """100 年编译结果在随机抽样日期上与逐秒参考完全一致。"""
    begin, end = ts(2000), ts(2100)
    rules = make_32_rules(13)
    intervals = compile_windows("Australia/Lord_Howe", rules, begin, end)
    starts = [iv[0] for iv in intervals]

    rng = random.Random(99)
    for _ in range(12):
        day0 = begin + rng.randrange(0, (end - begin) // 86400) * 86400
        for t in range(day0, day0 + 86400):
            i = bisect_right(starts, t) - 1
            actual = i >= 0 and t < intervals[i][1]
            assert actual == ref_membership("Australia/Lord_Howe", rules, t), (
                f"第 {t} 秒不一致"
            )


def test_randomized_property_against_reference():
    """随机时区/规则/范围，与逐秒参考实现对比。"""
    zones = [
        "America/New_York",
        "Australia/Lord_Howe",
        "Pacific/Apia",
        "Europe/London",
        "Asia/Tehran",
        "Australia/Sydney",
    ]
    rng = random.Random(2024)
    for tz_name in zones:
        rules = []
        for _ in range(rng.randrange(1, 8)):
            start = rng.randrange(0, 86400)
            end = rng.randrange(0, 86400)
            if start != end:
                rules.append(WeeklyWindow(rng.randrange(0, 7), start, end))
        # 随机取 3 天窗口
        base = ts(rng.randrange(1971, 2098), rng.randrange(1, 13), 1)
        base += rng.randrange(0, 20) * 86400
        intervals = compile_windows(tz_name, rules, base, base + 3 * 86400)
        starts = [iv[0] for iv in intervals]
        for t in range(base, base + 3 * 86400):
            i = bisect_right(starts, t) - 1
            actual = i >= 0 and t < intervals[i][1]
            assert actual == ref_membership(tz_name, rules, t), (
                f"{tz_name} 第 {t} 秒不一致"
            )
