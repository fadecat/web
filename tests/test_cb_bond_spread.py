# -*- coding: utf-8 -*-
"""转债-国债利差计算服务测试(结构对齐 test_equity_bond.py)。"""
from __future__ import annotations

from datetime import date, timedelta

from backend.services.cb_bond_spread import compute_cb_bond_spread


def _make_maps(n=25, ytm=-3.5, y=2.0):
    """构造 n 个有效交易日的 avg_ytm/10Y 国债映射(恒定值)。"""
    start = date(2020, 1, 1)
    ytm_map, bond_map = {}, {}
    for i in range(n):
        d = (start + timedelta(days=i)).isoformat()
        ytm_map[d] = ytm
        bond_map[d] = y
    return ytm_map, bond_map


class TestCbBondSpread:
    def test_series_values_and_same_day_fields(self):
        """每个序列点带同日 avg_ytm/bond_yield, spread 为差值(负值合法)。"""
        ytm_map, bond_map = _make_maps()
        result = compute_cb_bond_spread(ytm_map, bond_map, include_series=True)
        assert result is not None
        series = result["series"]
        assert len(series) == 25
        # ytm=-3.5, y=2.0 → spread=-5.5(负值原样保留)
        assert series[0] == {
            "trade_date": "2020-01-01",
            "avg_ytm": -3.5,
            "bond_yield": 2.0,
            "spread": -5.5,
        }

    def test_uses_valid_intersection_in_date_order(self):
        """乱序输入、缺日期、None 过滤后返回有效交集且升序。"""
        start = date(2020, 1, 1)
        ytm_map, bond_map = {}, {}
        days = [start + timedelta(days=i) for i in range(6)]
        ytm_map[days[2].isoformat()] = -1.0
        ytm_map[days[0].isoformat()] = -1.0  # 乱序
        ytm_map[days[4].isoformat()] = None  # 空值跳过
        bond_map[days[0].isoformat()] = 2.0
        bond_map[days[1].isoformat()] = 2.0  # ytm 缺失的日期不参与
        bond_map[days[2].isoformat()] = 2.0
        # 有效交集仅 2 天 < 20 → None
        assert compute_cb_bond_spread(ytm_map, bond_map) is None

        # 补足 20 个样本, 混入单侧缺失日验证交集与排序
        ytm_map2, bond_map2 = _make_maps(20)
        extra_bond_day = (start + timedelta(days=100)).isoformat()
        extra_ytm_day = (start + timedelta(days=101)).isoformat()
        ytm_map2[extra_ytm_day] = -1.0  # 国债缺失 → 不参与
        bond_map2[extra_bond_day] = 2.0  # ytm 缺失 → 不参与
        result = compute_cb_bond_spread(ytm_map2, bond_map2, include_series=True)
        assert result is not None
        dates = [p["trade_date"] for p in result["series"]]
        assert dates == sorted(dates)
        assert len(dates) == 20
        assert extra_ytm_day not in dates
        assert extra_bond_day not in dates

    def test_summary_fields_and_sample_threshold(self):
        """摘要字段齐全; 样本 <20 返回 None; 不请求 series 时不附带历史载荷。"""
        ytm_map, bond_map = _make_maps(19)
        assert compute_cb_bond_spread(ytm_map, bond_map, include_series=True) is None

        ytm_map, bond_map = _make_maps(20, ytm=-3.5, y=2.0)
        result = compute_cb_bond_spread(ytm_map, bond_map, include_series=False)
        assert result is not None
        assert "series" not in result
        assert result["trade_date"] == "2020-01-20"
        assert result["avg_ytm"] == -3.5
        assert result["bond_yield"] == 2.0
        assert result["spread"]["current"] == -5.5

    def test_percentiles_and_average_5y(self):
        """利差单调递增时: 末条为窗口最大, 分位 = (n-1)/n; 5Y 均值为算术平均。"""
        ytm_map, bond_map = {}, {}
        start = date(2020, 1, 1)
        for i in range(25):
            d = (start + timedelta(days=i)).isoformat()
            ytm_map[d] = -10.0 + i * 0.5  # ytm 递增 → spread 递增
            bond_map[d] = 2.0
        result = compute_cb_bond_spread(ytm_map, bond_map, include_series=True)
        assert result is not None
        # 25 天全在 1y 窗口内, 但 3y/5y/10y 窗口同样覆盖全部 25 天(数据只到第 25 天)
        # 当前 spread 为最大: 严格小于当前值的天数 = 24 → 96.0%
        assert result["spread"]["percentiles"]["1y"] == 96.0
        assert result["spread"]["percentiles"]["3y"] == 96.0
        assert result["spread"]["percentiles"]["5y"] == 96.0
        assert result["spread"]["percentiles"]["10y"] == 96.0
        # 5Y 均值 = (-12 + ... + 0) 的 spread 均值: ytm -10..2 步长 0.5 → 均值 = -4 - 2 = -6
        expected_avg = round(sum(-10.0 + i * 0.5 - 2.0 for i in range(25)) / 25, 4)
        assert result["spread"]["average_5y"] == expected_avg

    def test_short_window_percentile_skipped(self):
        """窗口内样本 <20 时该窗口分位跳过(键不存在), 更长窗口正常返回。"""
        ytm_map, bond_map = {}, {}
        start = date(2020, 1, 1)
        # 2020 年初 30 条历史(只进入 10y 窗口)
        for i in range(30):
            d = (start + timedelta(days=i)).isoformat()
            ytm_map[d] = -1.0
            bond_map[d] = 2.0
        # 最近仅 15 条: 最新日期 2026-09-15, 1y/3y/5y 窗口下界均晚于 2020 段
        anchor = date(2026, 9, 1)
        for i in range(15):
            d = (anchor + timedelta(days=i)).isoformat()
            ytm_map[d] = -5.0 + i * 0.01
            bond_map[d] = 2.5
        result = compute_cb_bond_spread(ytm_map, bond_map)
        assert result is not None
        assert result["trade_date"] == "2026-09-15"
        # 1y/3y/5y 窗口只含最近 15 条 → 跳过; 10y 覆盖两段共 45 条 → 存在
        assert "1y" not in result["spread"]["percentiles"]
        assert "3y" not in result["spread"]["percentiles"]
        assert "5y" not in result["spread"]["percentiles"]
        assert "10y" in result["spread"]["percentiles"]
