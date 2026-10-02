# maintwin

单目标的维护窗编译与内容漂移判定库（纯 Python，仅依赖标准库 `zoneinfo`）。

## API

```python
from maintwin import WeeklyWindow, compile_windows, detect_drift
```

- `compile_windows(timezone, weekly_windows, begin, end) -> list[(start, end)]`
  将每周重复的当地维护窗（IANA 时区）编译为 `[begin, end)` 内的 UTC 区间，
  返回按时间排序、互不重叠且不相邻的最大左闭右开区间。
- `detect_drift(observations, intervals) -> list[DriftAlert]`
  输入严格递增的 `(utc_second, sha256_hex)` 观测；首条只建基线，之后每条与
  上一条哈希比较，不同且不在维护窗内才报警；无论是否报警都更新基线。

## 规则与语义

- `WeeklyWindow(weekday, start_second, end_second)`：`weekday` 0–6 表示周一至
  周日；钟面秒 0–86399 且起止不同；`end < start` 表示跨当地次日，凌晨部分
  归属于前一个当地民用日期开始的窗口。规则最多 32 条，可重叠。
- 时间统一精确到秒；查询范围限定 1970-01-01 至 2100-01-01 UTC，跨度 ≤ 100 年。
- 夏令时回拨中重复的当地时间两次都生效；前跳中不存在的当地时间不产生维护
  时段，也不顺延。因此一个当地窗口可能对应零段、一段或多段 UTC 区间。
- 维护窗不会重置基线；窗内无观测时，窗后第一次变化照常报警。

## 实现要点

- 先在查询范围内一次性求出全部 UTC 偏移跳变时刻（6 小时步进 + 二分定位到秒），
  再按当地日期 × 周规则求每个本地窗口的 UTC 原像：以窗口起止的本地时间原像
  （fold=0/1 往返校验）与附近跳变时刻为候选边界，逐段用中点成员判断取舍。
  无跳变的普通窗口走常数偏移快速路径，不逐秒/逐分钟扫描。
- `detect_drift` 对编译结果做二分成员查询，不重新编译窗口。

## 测试

```bash
python3 -m pytest tests -q -s
```

覆盖纽约 2024-11-03 回拨双段（含两段之间空隙报警）、2024-03-10 不存在的
02:10–02:50、Lord Howe 30 分钟跳时、Apia 跳过 2011-12-30，均以逐秒直接成员
判断为参考逐边界核对；另含 100 年 × 32 规则的规模与耗时测试。
