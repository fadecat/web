# -*- coding: utf-8 -*-
"""转债-国债利差计算(结构移植自 backend/services/equity_bond.py)。

口径:
- 转债平均到期收益率 avg_ytm 取自集思录转债指数(混合剩余期限/评级的
  市场平均值); 国债基准取 10 年期, 与估值页股债收益差同期限、可直接对照
  (转债平均剩余期限约 2.5~3 年, 期限不做精确匹配, 定位为市场温度计
  而非可交易利差)。
- spread = avg_ytm - yield_10y(百分点), 越高代表转债债底相对国债越便宜
  (纯债视角保护越厚); 负值表示转债整体比国债贵。
- 分位 = 窗口内 spread 严格小于当前值的天数占比 × 100, 窗口 1Y/3Y/5Y/10Y,
  样本 < 20 跳过(与 equity_bond 完全同构)。
- 5Y 均值 = 最近 5 年窗口算术平均。

与 equity_bond 的差异:
- 只做 spread 不做 ratio: 转债 avg_ytm 常为负或趋近 0, 比值会变号失真,
  且 equity_bond 对分母 y<=0 的跳过规则在转债场景会大面积断样本。
- 无 EP 换算: 两个输入本身就是百分比, 直接相减。

纯函数模块: 不读库、不触网、无副作用。
"""
from __future__ import annotations

from datetime import date
from typing import Any

# 分位窗口(年): 与 equity_bond 一致取 1/3/5/10
_PERCENTILE_WINDOWS: tuple[tuple[str, int], ...] = (("1y", 1), ("3y", 3), ("5y", 5), ("10y", 10))
_MIN_SAMPLES = 20


def _shift_years(iso_date: str, years: int) -> str:
    """ISO 日期字符串减 N 年; 2/29 自动降级为 2/28。"""
    d = date.fromisoformat(iso_date)
    try:
        return d.replace(year=d.year - years).isoformat()
    except ValueError:  # 2/29 非闰年
        return d.replace(year=d.year - years, day=28).isoformat()


def compute_cb_bond_spread(
    ytm_map: dict[str, float | None],
    bond_map: dict[str, float | None],
    *,
    include_series: bool = False,
) -> dict[str, Any] | None:
    """由转债平均到期收益率历史与 10Y 国债收益率历史计算利差。

    参数:
        ytm_map: {ISO日期: avg_ytm(%)}; None 的日期跳过(允许 0 与负数)
        bond_map: {ISO日期: 10Y 国债收益率(%)}; None 的日期跳过
        include_series: True 时附带全历史序列(前端画走势用)

    返回:
        {
          "trade_date": 最新共同交易日, "avg_ytm": ..., "bond_yield": ...,
          "spread": {"current", "percentiles", "average_5y"},
          "series": [{"trade_date", "avg_ytm", "bond_yield", "spread"}, ...]
          # series 仅 include_series=True 时附带; 字段名对齐 cb-index/daily
        }
        两序列按日期内连接, 有效样本不足 20 天返回 None。
    """
    series: list[dict[str, Any]] = []
    for d in sorted(set(ytm_map) & set(bond_map)):
        ytm = ytm_map[d]
        y = bond_map[d]
        if ytm is None or y is None:
            continue
        series.append(
            {
                "trade_date": d,
                "avg_ytm": ytm,
                "bond_yield": y,
                "spread": round(ytm - y, 4),
            }
        )

    if len(series) < _MIN_SAMPLES:
        return None

    latest = series[-1]
    cur_spread = latest["spread"]

    spread_pct: dict[str, float] = {}
    for label, years in _PERCENTILE_WINDOWS:
        cutoff = _shift_years(latest["trade_date"], years)
        window = [p for p in series if p["trade_date"] >= cutoff]
        if len(window) < _MIN_SAMPLES:
            continue
        spread_pct[label] = round(
            sum(1 for p in window if p["spread"] < cur_spread) / len(window) * 100, 2
        )

    w5 = [p for p in series if p["trade_date"] >= _shift_years(latest["trade_date"], 5)]
    spread_avg_5y = round(sum(p["spread"] for p in w5) / len(w5), 4) if w5 else None

    result: dict[str, Any] = {
        "trade_date": latest["trade_date"],
        "avg_ytm": latest["avg_ytm"],
        "bond_yield": latest["bond_yield"],
        "spread": {
            "current": cur_spread,
            "percentiles": spread_pct,
            "average_5y": spread_avg_5y,
        },
    }
    if include_series:
        result["series"] = series
    return result
