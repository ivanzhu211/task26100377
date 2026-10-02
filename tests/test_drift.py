"""detect_drift 的基线与维护窗语义测试。"""

import pytest

from driftwin import detect_drift

A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64

# 维护窗：UTC [1000, 2000)
WINDOW = [(1000, 2000)]


class TestBaseline:
    def test_first_observation_only_sets_baseline(self):
        assert detect_drift([(500, A)], WINDOW) == []

    def test_empty_observations(self):
        assert detect_drift([], WINDOW) == []

    def test_unchanged_hash_never_alerts(self):
        obs = [(100, A), (500, A), (1500, A), (3000, A)]
        assert detect_drift(obs, WINDOW) == []

    def test_every_observation_updates_baseline(self):
        # 3000 处 A->B 报警后基线变为 B；4000 处 B->A 是新的变化，再次报警
        obs = [(100, A), (3000, B), (4000, A)]
        alerts = detect_drift(obs, WINDOW)
        assert alerts == [(3000, A, B), (4000, B, A)]

    def test_no_alert_means_baseline_still_updated(self):
        # 窗内 A->B 被接受（基线变 B）；窗后 B->A 与基线 B 不同，必须报警
        obs = [(100, A), (1500, B), (3000, A)]
        assert detect_drift(obs, WINDOW) == [(3000, B, A)]


class TestWindowBoundaries:
    def test_change_inside_window_is_suppressed(self):
        obs = [(100, A), (1500, B)]
        assert detect_drift(obs, WINDOW) == []

    def test_change_exactly_at_window_start_is_suppressed(self):
        obs = [(100, A), (1000, B)]
        assert detect_drift(obs, WINDOW) == []

    def test_change_exactly_at_window_end_alerts(self):
        # 左闭右开：end 本身已在窗外
        obs = [(100, A), (2000, B)]
        assert detect_drift(obs, WINDOW) == [(2000, A, B)]

    def test_change_just_before_window_alerts(self):
        obs = [(100, A), (999, B)]
        assert detect_drift(obs, WINDOW) == [(999, A, B)]

    def test_change_just_after_window_alerts(self):
        obs = [(100, A), (2001, B)]
        assert detect_drift(obs, WINDOW) == [(2001, A, B)]


class TestWindowWithoutObservations:
    def test_no_observation_inside_window_does_not_absorb_later_change(self):
        # 窗内没有观测，窗后第一次变化不得被吞掉
        obs = [(100, A), (3000, B)]
        assert detect_drift(obs, WINDOW) == [(3000, A, B)]

    def test_accepted_change_inside_window_is_not_repeated_after(self):
        # 窗内 A->B 已接受；窗后仍是 B（相对新基线无变化），不得补发报警
        obs = [(100, A), (1500, B), (3000, B)]
        assert detect_drift(obs, WINDOW) == []

    def test_first_unknown_change_after_window_alerts(self):
        # 窗内接受了 A->B，窗内又变回 A（也接受），窗后 A->C 必须报警
        obs = [(100, A), (1200, B), (1800, A), (3000, C)]
        assert detect_drift(obs, WINDOW) == [(3000, A, C)]


class TestMultipleWindows:
    def test_second_window_also_suppresses(self):
        windows = [(1000, 2000), (5000, 6000)]
        obs = [(100, A), (1500, B), (5500, C)]
        assert detect_drift(obs, windows) == []

    def test_gap_between_windows_alerts(self):
        windows = [(1000, 2000), (5000, 6000)]
        obs = [(100, A), (1500, B), (3000, C)]
        assert detect_drift(obs, windows) == [(3000, B, C)]


class TestValidation:
    def test_non_increasing_timestamps_rejected(self):
        with pytest.raises(ValueError):
            detect_drift([(100, A), (100, B)], WINDOW)
        with pytest.raises(ValueError):
            detect_drift([(200, A), (100, B)], WINDOW)

    def test_bad_hash_rejected(self):
        with pytest.raises(ValueError):
            detect_drift([(100, "not-a-hash")], WINDOW)
        with pytest.raises(ValueError):
            detect_drift([(100, A.upper())], WINDOW)  # 必须小写

    def test_accepts_dict_observations(self):
        obs = [{"timestamp": 100, "hash": A}, {"timestamp": 3000, "hash": B}]
        assert detect_drift(obs, WINDOW) == [(3000, A, B)]


class TestIntegrationWithCompiledWindows:
    """用真实编译的维护窗做端到端漂移判定。"""

    def test_change_between_fall_back_segments_alerts(self):
        from datetime import datetime, timezone

        from driftwin import WeeklyWindow, compile_windows

        def ts(h, mi):
            return int(datetime(2024, 11, 3, h, mi, tzinfo=timezone.utc).timestamp())

        # 纽约 2024-11-03 回拨：01:15-01:45 当地 => 05:15-05:45 与 06:15-06:45 UTC
        windows = compile_windows(
            "America/New_York",
            [WeeklyWindow(6, 4500, 6300)],
            ts(0, 0),
            ts(12, 0),
        )
        assert len(windows) == 2
        obs = [
            (ts(4, 0), A),            # 窗前基线
            (ts(5, 30), B),           # 第一段窗内：接受
            (ts(6, 0), C),            # 两段之间：必须报警
            (ts(6, 30), D),           # 第二段窗内：接受（相对新基线 C）
            (ts(7, 0), A),            # 窗后：报警
        ]
        assert detect_drift(obs, windows) == [
            (ts(6, 0), B, C),
            (ts(7, 0), D, A),
        ]
