"""公共类型定义。"""

from __future__ import annotations

from dataclasses import dataclass

# Unix 秒：1970-01-01T00:00:00Z 与 2100-01-01T00:00:00Z
MIN_UNIX_SECOND = 0
MAX_UNIX_SECOND = 4102444800

# 单次查询最大跨度：100 年（按 100 * 365.25 天计）
MAX_SPAN_SECONDS = 100 * 36525 // 100 * 86400  # 3_155_760_000

SECONDS_PER_DAY = 86400
MAX_WEEKLY_WINDOWS = 32


@dataclass(frozen=True)
class WeeklyWindow:
    """每周重复的当地维护窗。

    weekday: 0=周一 .. 6=周日（当地民用日）。
    start_second / end_second: 当地钟面秒，0..86399，且二者不同。
    end_second < start_second 表示窗口跨越当地次日。
    """

    weekday: int
    start_second: int
    end_second: int


# UTC 区间，左闭右开，单位为 Unix 秒
Interval = tuple[int, int]

# 观测：(unix 秒, sha256 十六进制字符串)
Observation = tuple[int, str]

# 报警：(unix 秒, 旧哈希, 新哈希)
DriftAlert = tuple[int, str, str]


def to_unix_seconds(value) -> int:
    """把 int Unix 秒或带时区的 datetime 归一化为 int Unix 秒。"""
    from datetime import datetime

    if isinstance(value, bool):
        raise TypeError("时间戳必须是 int Unix 秒或带时区的 datetime")
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("datetime 必须带时区信息")
        return int(value.timestamp())
    raise TypeError(f"不支持的时间类型: {type(value)!r}")
