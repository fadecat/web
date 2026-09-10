# -*- coding: utf-8 -*-
"""股债收益差/比 计算服务测试(Phase A2: 叠加十年期国债收益率)。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services.equity_bond import compute_equity_bond


def _make_maps(n=25, pe=10.0, y=2.5):
    """构造 n 个有效交易日的 PE/国债映射(PE 恒定 10, 国债恒定 2.5)。"""
    start = date(2020, 1, 1)
    pe_map, bond_map = {}, {}
    for i in range(n):
        d = (start + timedelta(days=i)).isoformat()
        pe_map[d] = pe
        bond_map[d] = y
    return pe_map, bond_map


class TestBondOverlay:
    def test_series_includes_same_day_bond_yield(self):
        """每个序列点都带同日 cn_10y_bond_yield, 且值正确。"""
        pe_map, bond_map = _make_maps()
        result = compute_equity_bond(pe_map, bond_map, include_series=True)
        assert result is not None
        series = result["series"]
        assert len(series) == 25
        # PE=10 → EP=10; y=2.5 → spread=7.5, ratio=4.0, yield=2.5
        assert series[0] == {
            "date": "2020-01-01",
            "spread": 7.5,
            "ratio": 4.0,
            "cn_10y_bond_yield": 2.5,
        }
        assert series[-1]["cn_10y_bond_yield"] == 2.5

    def test_series_uses_valid_intersection_in_date_order(self):
        """乱序输入、缺日期、空值与无效值过滤后, 返回有效交集且升序。"""
        start = date(2020, 1, 1)
        pe_map, bond_map = {}, {}
        # 乱序插入: 3 天 PE, 3 天国债, 部分重叠
        days = [start + timedelta(days=i) for i in range(6)]
        pe_map[days[2].isoformat()] = 20.0
        pe_map[days[0].isoformat()] = 20.0  # 乱序
        pe_map[days[4].isoformat()] = None  # 空值跳过
        pe_map[days[5].isoformat()] = 0.0  # 非正跳过
        bond_map[days[0].isoformat()] = 2.0
        bond_map[days[1].isoformat()] = 2.0  # PE 缺失的日期不参与
        bond_map[days[2].isoformat()] = 2.0
        bond_map[days[3].isoformat()] = 2.0  # PE 缺失
        bond_map[days[5].isoformat()] = 2.0

        # 有效交集: days[0], days[2] (days[5] 的 PE=0 被跳过)
        result = compute_equity_bond(pe_map, bond_map, include_series=True)
        assert result is None  # 只有 2 个有效样本 < 20

        # 补足到 20 个样本验证交集与排序
        pe_map2, bond_map2 = _make_maps(20, pe=20.0, y=2.0)
        # 混入一个 PE 缺失的国债日, 一个国债缺失的 PE 日
        extra_bond_day = (start + timedelta(days=100)).isoformat()
        extra_pe_day = (start + timedelta(days=101)).isoformat()
        pe_map2[extra_pe_day] = 20.0  # 国债缺失 → 不参与
        bond_map2[extra_bond_day] = 2.0  # PE 缺失 → 不参与
        # 乱序插入不影响输出
        result = compute_equity_bond(pe_map2, bond_map2, include_series=True)
        assert result is not None
        dates = [p["date"] for p in result["series"]]
        assert dates == sorted(dates)
        assert len(dates) == 20
        assert extra_pe_day not in dates
        assert extra_bond_day not in dates
        for p in result["series"]:
            assert p["cn_10y_bond_yield"] == 2.0  # 与 PE 同日一一对应
            assert p["spread"] == round(100.0 / 20.0 - 2.0, 4)  # 3.0
            assert p["ratio"] == round(100.0 / 20.0 / 2.0, 4)  # 2.5

    def test_bond_overlay_preserves_summary_and_sample_threshold(self):
        """摘要与旧口径一致; 少于 20 个样本返回 None; 不请求 series 时不增加载荷。"""
        # 少于 20 个样本 → None
        pe_map, bond_map = _make_maps(19)
        assert compute_equity_bond(pe_map, bond_map, include_series=True) is None

        # 20 个样本: 摘要与旧口径一致(无 series 字段时不含历史载荷)
        pe_map, bond_map = _make_maps(20, pe=20.0, y=2.0)
        result = compute_equity_bond(pe_map, bond_map, include_series=False)
        assert result is not None
        assert "series" not in result
        # EP=5, spread=3, ratio=2.5
        assert result["spread"]["current"] == 3.0
        assert result["ratio"]["current"] == 2.5
        assert result["cn_10y_bond_yield"] == 2.0
        # 顶层 PE 与国债值
        assert result["pe"] == 20.0

        # include_series=True 时 series 存在且长度正确
        result2 = compute_equity_bond(pe_map, bond_map, include_series=True)
        assert len(result2["series"]) == 20

    def test_sample_less_than_20_returns_none(self):
        """有效样本不足 20 天返回 None(与旧口径一致)。"""
        pe_map, bond_map = _make_maps(19)
        assert compute_equity_bond(pe_map, bond_map, include_series=True) is None
