# driftwin

单目标的维护窗与内容漂移判定库。探测由调用方完成；库的输入是时间严格递增的
UTC 时间戳与 SHA-256 内容哈希，输出编译后的 UTC 维护区间与需要报警的漂移事件。

## 安装与依赖

- Python 3.9+，仅依赖标准库（`zoneinfo`，需系统 IANA tzdata）。
- 运行测试：`python3 -m pytest tests/ -q -s`

## API

```python
from driftwin import WeeklyWindow, compile_windows, detect_drift
```

### `compile_windows(timezone, weekly_windows, begin, end)`

将每周重复的当地维护窗编译为 `[begin, end)` 内的 UTC 区间，返回按时间排序、
互不重叠且不相邻的最大区间（左闭右开，Unix 秒）。

- `timezone`：IANA 时区名，如 `"America/New_York"`。
- `weekly_windows`：最多 32 条 `WeeklyWindow(weekday, start_second, end_second)`
  （也接受 `dict` 或三元组）。`weekday` 0=周一..6=周日；钟面秒 0..86399 且起止
  不同；`end_second < start_second` 表示跨当地次日，凌晨部分归属于前一个当地
  民用日期。
- `begin` / `end`：int Unix 秒或带时区 `datetime`，范围 1970-01-01 至
  2100-01-01，单次跨度不超过 100 年。

正确性性质：对范围内任意 UTC 秒 `t`，`t` 属于编译结果，当且仅当 `t` 转换到
指定时区后的当地星期与钟面时间命中至少一条周规则。夏令时回拨中重复的当地
时间两遍都算；前跳中不存在的当地时间不产生任何区间。因此一个当地窗口可能
对应零段、一段或多段 UTC 区间。

### `detect_drift(observations, intervals)`

- `observations`：时间严格递增的 `(时间戳, sha256 小写十六进制)` 序列
  （也接受 `{"timestamp": ..., "hash": ...}`）。
- `intervals`：`compile_windows` 的输出。

首条观测只建立基线；之后每条与上一条哈希比较，不同且当前观测不在维护窗内
才报警；无论是否报警，每条观测都会更新基线。维护窗本身不会重置基线；窗内
没有观测时，窗后第一次变化照常报警。返回 `[(时间, 旧哈希, 新哈希), ...]`。

## 实现要点

- 不逐秒/逐分钟扫描：UTC 偏移切换点用大步扫描 + 二分定位（每年至多数次），
  再按当地日期逐条展开周规则。
- 当地时间轴在切换点处切分为原子段，对每段中点做 roundtrip 判定：
  0 个有效偏移 = 当地时间不存在（丢弃）；1 个 = 正常映射；2 个 = 回拨重复
  （映射为两段 UTC 区间）。不使用固定 UTC 偏移或固定 24 小时平移。
- 漂移判定对区间起点做二分查找，不针对每条观测重新编译窗口。

## 示例

```python
from driftwin import WeeklyWindow, compile_windows, detect_drift

# 纽约每周日 01:15-01:45 的维护窗
windows = compile_windows(
    "America/New_York",
    [WeeklyWindow(weekday=6, start_second=4500, end_second=6300)],
    1730592000, 1730678400,  # 2024-11-03 全天（UTC）
)
# 回拨当天得到两段：05:15-05:45 与 06:15-06:45（UTC）

alerts = detect_drift(
    [
        (1730610000, "aa" * 32),  # 05:00 UTC，建立基线
        (1730611800, "bb" * 32),  # 05:30 UTC，在第一段窗内，被接受
        (1730613600, "cc" * 32),  # 06:00 UTC，两段之间，报警
    ],
    windows,
)
# => [(1730613600, "bb"*32, "cc"*32)]
```
