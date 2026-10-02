"""compile_windows 的时区边界与正确性测试。"""

from bisect import bisect_right
from datetime import datetime, timedelta, timezone

import pytest

from driftwin import WeeklyWindow, compile_windows

UTC = timezone.utc


def ts(y, mo, d, h=0, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=UTC).timestamp())


def ref_membership(tz_name, rules, t):
    """逐秒参考实现：直接做 UTC -> 当地转换后判断周规则。"""
    from zoneinfo import ZoneInfo

    local = datetime.fromtimestamp(t, UTC).astimezone(ZoneInfo(tz_name))
    wd = local.weekday()
    sec = local.hour * 3600 + local.minute * 60 + local.second
    for r in rules:
        if r.start_second < r.end_second:
            if r.weekday == wd and r.start_second <= sec < r.end_second:
                return True
        else:  # 跨午夜：凌晨部分归属于前一个当地民用日期
            if r.weekday == wd and sec >= r.start_second:
                return True
            if r.weekday == (wd - 1) % 7 and sec < r.end_second:
                return True
    return False


def assert_matches_reference(tz_name, rules, begin, end):
    """逐秒核对编译结果与参考成员判断完全一致（覆盖每一个边界）。"""
    intervals = compile_windows(tz_name, rules, begin, end)
    starts = [iv[0] for iv in intervals]
    for t in range(begin, end):
        i = bisect_right(starts, t) - 1
        actual = i >= 0 and t < intervals[i][1]
        assert actual == ref_membership(tz_name, rules, t), f"第 {t} 秒不一致"
    return intervals


class TestNewYorkFallBack:
    TZ = "America/New_York"
    RULE = WeeklyWindow(6, 1 * 3600 + 15 * 60, 1 * 3600 + 45 * 60)  # 周日 01:15-01:45
    BEGIN = ts(2024, 11, 3)
    END = ts(2024, 11, 4)

    def test_produces_two_utc_segments(self):
        intervals = compile_windows(self.TZ, [self.RULE], self.BEGIN, self.END)
        assert intervals == [
            (ts(2024, 11, 3, 5, 15), ts(2024, 11, 3, 5, 45)),   # 第一遍 01:15-01:45 (EDT)
            (ts(2024, 11, 3, 6, 15), ts(2024, 11, 3, 6, 45)),   # 第二遍 01:15-01:45 (EST)
        ]

    def test_every_second_matches_reference(self):
        assert_matches_reference(self.TZ, [self.RULE], self.BEGIN, self.END)


class TestNewYorkSpringForward:
    TZ = "America/New_York"
    RULE = WeeklyWindow(6, 2 * 3600 + 10 * 60, 2 * 3600 + 50 * 60)  # 周日 02:10-02:50
    BEGIN = ts(2024, 3, 10)
    END = ts(2024, 3, 11)

    def test_nonexistent_local_time_yields_nothing(self):
        assert compile_windows(self.TZ, [self.RULE], self.BEGIN, self.END) == []

    def test_window_spanning_gap_is_trimmed_not_shifted(self):
        # 01:30-03:30：不存在的 02:00-03:00 被挖掉，两端各自映射
        rule = WeeklyWindow(6, 1 * 3600 + 30 * 60, 3 * 3600 + 30 * 60)
        intervals = compile_windows(self.TZ, [rule], self.BEGIN, self.END)
        assert intervals == [(ts(2024, 3, 10, 6, 30), ts(2024, 3, 10, 7, 30))]

    def test_every_second_matches_reference(self):
        assert_matches_reference(self.TZ, [self.RULE], self.BEGIN, self.END)


class TestLordHoweThirtyMinuteShifts:
    TZ = "Australia/Lord_Howe"

    def test_fall_back_repeated_half_hour(self):
        # 2024-04-07 02:00 -> 01:30，偏移 +11 -> +10:30；周日 01:45-02:15
        rule = WeeklyWindow(6, 1 * 3600 + 45 * 60, 2 * 3600 + 15 * 60)
        intervals = compile_windows(self.TZ, [rule], ts(2024, 4, 6), ts(2024, 4, 8))
        assert intervals == [
            (ts(2024, 4, 6, 14, 45), ts(2024, 4, 6, 15, 0)),    # 01:45-02:00 (+11)
            (ts(2024, 4, 6, 15, 15), ts(2024, 4, 6, 15, 45)),   # 01:45-02:15 (+10:30)
        ]
        assert_matches_reference(self.TZ, [rule], ts(2024, 4, 6), ts(2024, 4, 8))

    def test_spring_forward_half_hour_gap(self):
        # 2024-10-06 02:00 -> 02:30，偏移 +10:30 -> +11；周日 01:50-02:40
        rule = WeeklyWindow(6, 1 * 3600 + 50 * 60, 2 * 3600 + 40 * 60)
        intervals = compile_windows(self.TZ, [rule], ts(2024, 10, 5), ts(2024, 10, 7))
        # 01:50-02:00 (+10:30) 与 02:30-02:40 (+11) 在 UTC 上相邻，合并为一段
        assert intervals == [(ts(2024, 10, 5, 15, 20), ts(2024, 10, 5, 15, 40))]
        assert_matches_reference(self.TZ, [rule], ts(2024, 10, 5), ts(2024, 10, 7))


class TestApiaSkippedDate:
    TZ = "Pacific/Apia"

    def test_skipped_friday_yields_nothing(self):
        # 2011-12-30（周五）整天不存在
        rule = WeeklyWindow(4, 30 * 60, 23 * 3600 + 30 * 60)
        assert compile_windows(self.TZ, [rule], ts(2011, 12, 29), ts(2011, 12, 31)) == []

    def test_thursday_window_crossing_into_skipped_day(self):
        # 周四 22:00 - 次日 02:00：凌晨部分不存在，只保留 22:00-24:00（UTC-10）
        # 当地 22:00-24:00 + UTC-10 => UTC 2011-12-30 08:00-10:00
        rule = WeeklyWindow(3, 22 * 3600, 2 * 3600)
        intervals = compile_windows(self.TZ, [rule], ts(2011, 12, 28), ts(2011, 12, 31))
        assert intervals == [(ts(2011, 12, 30, 8, 0), ts(2011, 12, 30, 10, 0))]

    def test_every_second_matches_reference(self):
        rule = WeeklyWindow(3, 22 * 3600, 2 * 3600)
        assert_matches_reference(self.TZ, [rule], ts(2011, 12, 28), ts(2011, 12, 31))


class TestMergingAndClipping:
    def test_overlapping_rules_merge_into_maximal_intervals(self):
        rules = [
            WeeklyWindow(2, 10 * 3600, 12 * 3600),
            WeeklyWindow(2, 11 * 3600, 14 * 3600),
        ]
        intervals = compile_windows("UTC", rules, ts(2024, 1, 3), ts(2024, 1, 4))
        assert intervals == [(ts(2024, 1, 3, 10), ts(2024, 1, 3, 14))]

    def test_adjacent_intervals_merge(self):
        rules = [
            WeeklyWindow(2, 10 * 3600, 12 * 3600),
            WeeklyWindow(2, 12 * 3600, 13 * 3600),
        ]
        intervals = compile_windows("UTC", rules, ts(2024, 1, 3), ts(2024, 1, 4))
        assert intervals == [(ts(2024, 1, 3, 10), ts(2024, 1, 3, 13))]

    def test_clipped_to_begin_end(self):
        rule = WeeklyWindow(2, 10 * 3600, 12 * 3600)
        intervals = compile_windows(
            "UTC", [rule], ts(2024, 1, 3, 11), ts(2024, 1, 3, 11, 30)
        )
        assert intervals == [(ts(2024, 1, 3, 11), ts(2024, 1, 3, 11, 30))]

    def test_accepts_dict_and_tuple_rules(self):
        begin, end = ts(2024, 1, 3), ts(2024, 1, 4)
        expected = compile_windows("UTC", [WeeklyWindow(2, 3600, 7200)], begin, end)
        as_dict = compile_windows(
            "UTC", [{"weekday": 2, "start_second": 3600, "end_second": 7200}], begin, end
        )
        as_tuple = compile_windows("UTC", [(2, 3600, 7200)], begin, end)
        assert as_dict == as_tuple == expected


class TestValidation:
    def test_bad_timezone(self):
        with pytest.raises(Exception):
            compile_windows("Not/AZone", [WeeklyWindow(0, 0, 60)], 0, 86400)

    def test_equal_start_end_rejected(self):
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 60, 60)], 0, 86400)

    def test_bad_weekday_rejected(self):
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(7, 0, 60)], 0, 86400)

    def test_too_many_rules_rejected(self):
        rules = [WeeklyWindow(i % 7, 0, 60) for i in range(33)]
        with pytest.raises(ValueError):
            compile_windows("UTC", rules, 0, 86400)

    def test_empty_rules_yield_empty(self):
        assert compile_windows("UTC", [], 0, 86400) == []

    def test_range_must_be_within_1970_2100(self):
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 60)], -1, 86400)
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 60)], 0, 4102444801)

    def test_span_limit_100_years(self):
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 60)], 0, 3155760001)

    def test_begin_must_precede_end(self):
        with pytest.raises(ValueError):
            compile_windows("UTC", [WeeklyWindow(0, 0, 60)], 100, 100)


class TestPropertyAgainstReference:
    """多个时区、多组规则（含跨午夜）与逐秒参考实现对比。"""

    @pytest.mark.parametrize(
        "tz_name",
        ["America/New_York", "Australia/Lord_Howe", "Pacific/Apia", "Asia/Shanghai"],
    )
    def test_random_windows_around_transitions(self, tz_name):
        import random
        from zoneinfo import ZoneInfo

        rng = random.Random(hash(tz_name) & 0xFFFF)
        tz = ZoneInfo(tz_name)
        # 在查询范围内随机选若干天，围绕当天做逐秒核对
        for _ in range(6):
            day = datetime(1971, 1, 1, tzinfo=UTC) + timedelta(
                days=rng.randrange(0, 365 * 55)
            )
            begin = int(day.timestamp())
            end = begin + 2 * 86400
            rules = []
            for _ in range(rng.randrange(1, 6)):
                start = rng.randrange(0, 86400)
                end_sec = rng.randrange(0, 86400)
                if start != end_sec:
                    rules.append(WeeklyWindow(rng.randrange(0, 7), start, end_sec))
            if rules:
                assert_matches_reference(tz_name, rules, begin, end)


class TestRangeEdgeClipping:
    def test_window_clipped_mid_fold_at_begin(self):
        # 回拨当天的窗被 begin 从中间裁断：只保留第二遍的后半段
        rule = WeeklyWindow(6, 30 * 60, 90 * 60)  # 周日 00:30-01:30
        intervals = compile_windows(
            "America/New_York", [rule], ts(2024, 11, 3, 6, 15), ts(2024, 11, 3, 12)
        )
        assert intervals == [(ts(2024, 11, 3, 6, 15), ts(2024, 11, 3, 6, 30))]

    def test_window_clipped_mid_gap_at_end(self):
        # 前跳当天的窗被 end 裁断
        rule = WeeklyWindow(6, 3600, 4 * 3600)  # 周日 01:00-04:00
        intervals = compile_windows(
            "America/New_York", [rule], ts(2024, 3, 10), ts(2024, 3, 10, 7, 30)
        )
        assert intervals == [(ts(2024, 3, 10, 6, 0), ts(2024, 3, 10, 7, 30))]
