"""Shared type definitions for the maintenance-window / drift library."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeeklyWindow:
    """A weekly recurring local-time maintenance window.

    weekday: 0..6 for Monday..Sunday.
    start_second / end_second: local clock seconds in [0, 86399], must differ.
    end_second < start_second means the window crosses local midnight; the
    early-morning part belongs to the window started on the previous local
    civil date.
    """

    weekday: int
    start_second: int
    end_second: int


@dataclass(frozen=True)
class DriftAlert:
    """A content change observed outside every maintenance window."""

    timestamp: int  # UTC epoch second of the observation
    old_hash: str
    new_hash: str
