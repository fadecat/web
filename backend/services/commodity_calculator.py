"""商品价格分位计算的无副作用函数。"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

WINDOW_DAYS = {"d21": 21, "d63": 63, "y1": 252, "y3": 756, "y5": 1260, "y10": 2520}
ALGORITHM_VERSION = "v1"


@dataclass(frozen=True)
class PercentileResult:
    window_code: str
    window_days: int
    percentile: float | None
    sample_count: int
    signal: str
    algorithm_version: str = ALGORITHM_VERSION


def calculate_percentiles(values: Sequence[float] | Iterable[float], windows: dict[str, int] | None = None) -> dict[str, PercentileResult]:
    """按日期升序的收盘价计算各窗口最新值分位。"""
    samples = [float(value) for value in values]
    if any(not math.isfinite(value) for value in samples):
        raise ValueError("percentile samples must be finite")
    selected = windows or WINDOW_DAYS
    result: dict[str, PercentileResult] = {}
    for code, days in selected.items():
        window_days = int(days)
        if window_days <= 0:
            raise ValueError(f"window {code!r} must contain a positive number of days")
        window = samples[-window_days:]
        count = len(window)
        if count < window_days:
            result[code] = PercentileResult(code, window_days, None, count, "insufficient")
            continue
        latest = window[-1]
        percentile = sum(value <= latest for value in window) / count * 100.0
        percentile = float(percentile)
        signal = "high" if percentile >= 85 else "low" if percentile <= 30 else "neutral"
        result[code] = PercentileResult(code, window_days, percentile, count, signal)
    return result


def calculate_window_percentiles(values: Sequence[float] | Iterable[float], windows: dict[str, int] | None = None) -> dict[str, PercentileResult]:
    """兼容描述性命名的公开入口。"""
    return calculate_percentiles(values, windows)
