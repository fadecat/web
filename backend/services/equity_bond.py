# -*- coding: utf-8 -*-
"""股债收益差 / 股债收益比 计算。

口径移植自旧项目 market-daily/src/valuation/metrics.py(最早源自 monitor_drawdown):
- 盈利收益率 EP = 100 / PE(TTM), 单位 %
- 股债收益差 spread = EP - 10Y 国债收益率(百分点), 越高股票相对债券越便宜
- 股债收益比 ratio   = EP / 10Y 国债收益率
- 分位 = 窗口内 spread/ratio 严格小于当前值的天数占比 × 100, 窗口 1Y/3Y/5Y/10Y, 样本 < 20 跳过
- 5Y 均值 = 最近 5 年窗口算术平均

与旧实现的差异:
- 纯 Python(无 pandas): 数据从 SQLite 读成 {date: value} 字典, 按日期内连接
- 分位标签用小写("1y"/"3y"/"5y"/"10y"), 对齐估值快照 API 的字段风格
"""
from __future__ import annotations

from datetime import date
from typing import Any

# 分位窗口(年): 与旧实现一致取 1/3/5/10
_PERCENTILE_WINDOWS: tuple[tuple[str, int], ...] = (("1y", 1), ("3y", 3), ("5y", 5), ("10y", 10))
_MIN_SAMPLES = 20


def _shift_years(iso_date: str, years: int) -> str:
    """ISO 日期字符串减 N 年; 2/29 自动降级为 2/28。"""
    d = date.fromisoformat(iso_date)
    try:
        return d.replace(year=d.year - years).isoformat()
    except ValueError:  # 2/29 非闰年
        return d.replace(year=d.year - years, day=28).isoformat()


def compute_equity_bond(
    pe_map: dict[str, float | None],
    bond_map: dict[str, float | None],
    *,
    include_series: bool = False,
) -> dict[str, Any] | None:
    """由 PE 历史与 10Y 国债历史计算股债收益差/比。

    参数:
        pe_map: {ISO日期: PE(TTM)}; PE 为 None/<=0 的日期跳过
        bond_map: {ISO日期: 10Y 国债收益率}; 为 None/<=0 的日期跳过(比值分母)
        include_series: True 时附带全历史序列(详情页画走势用)

    返回:
        {
          "date": 最新交易日, "pe": ..., "cn_10y_bond_yield": ...,
          "spread": {"current", "percentiles", "average_5y"},
          "ratio":   {"current", "percentiles", "average_5y"},
          "series": [{"date", "spread", "ratio"}, ...]   # 仅 include_series=True
        }
        样本不足 20 天返回 None。
    """
    common_dates = sorted(set(pe_map) & set(bond_map))
    series: list[dict[str, Any]] = []
    for d in common_dates:
        pe = pe_map[d]
        y = bond_map[d]
        if pe is None or pe <= 0 or y is None or y <= 0:
            continue
        ep = 100.0 / pe
        series.append({"date": d, "spread": round(ep - y, 4), "ratio": round(ep / y, 4)})

    if len(series) < _MIN_SAMPLES:
        return None

    latest = series[-1]
    cur_spread = latest["spread"]
    cur_ratio = latest["ratio"]

    spread_pct: dict[str, float] = {}
    ratio_pct: dict[str, float] = {}
    for label, years in _PERCENTILE_WINDOWS:
        cutoff = _shift_years(latest["date"], years)
        window = [p for p in series if p["date"] >= cutoff]
        if len(window) < _MIN_SAMPLES:
            continue
        spread_pct[label] = round(
            sum(1 for p in window if p["spread"] < cur_spread) / len(window) * 100, 2
        )
        rw = [p for p in window if p["ratio"] is not None]
        if len(rw) >= _MIN_SAMPLES:
            ratio_pct[label] = round(
                sum(1 for p in rw if p["ratio"] < cur_ratio) / len(rw) * 100, 2
            )

    w5 = [p for p in series if p["date"] >= _shift_years(latest["date"], 5)]
    spread_avg_5y = round(sum(p["spread"] for p in w5) / len(w5), 4) if w5 else None
    ratio_avg_5y = round(sum(p["ratio"] for p in w5) / len(w5), 4) if w5 else None

    result: dict[str, Any] = {
        "date": latest["date"],
        "pe": pe_map[latest["date"]],
        "cn_10y_bond_yield": bond_map[latest["date"]],
        "spread": {
            "current": cur_spread,
            "percentiles": spread_pct,
            "average_5y": spread_avg_5y,
        },
        "ratio": {
            "current": cur_ratio,
            "percentiles": ratio_pct,
            "average_5y": ratio_avg_5y,
        },
    }
    if include_series:
        result["series"] = series
    return result
