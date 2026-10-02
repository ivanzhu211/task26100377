"""单目标维护窗与内容漂移判定库。"""

from .types import DriftAlert, Interval, Observation, WeeklyWindow
from .windows import compile_windows
from .drift import detect_drift

__all__ = [
    "DriftAlert",
    "Interval",
    "Observation",
    "WeeklyWindow",
    "compile_windows",
    "detect_drift",
]
