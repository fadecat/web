# -*- coding: utf-8 -*-
"""公共指标函数测试(T1: cb_metrics)。

核心反例(方案 T1/§10 验收):
- 新指标 simple_maturity_yield_pct 只由 (redeem_price, price) 计算,
  绝不回退读取集思录 ytm_rt;
- 赎回价只来自 redeem_cell.redeem_price, 不得混用 force_redeem_price(强赎触发价);
- enrich_cell 返回新 dict, 不原地污染抓取结果;
- 非有限数/非正数/bool 一律 None, 不产生 NaN/inf 参与过滤排序。
"""
from __future__ import annotations

import pytest

from backend.services.cb_metrics import (
    enrich_cell,
    finite_number,
    simple_maturity_yield_pct,
)


# ---------------------------------------------------------------------------
# 公式(方案 §3.1 指定用例)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('price,redeem,expected', [(100,110,10),(125,110,-12),(110,110,0)])
def test_simple_yield_formula(price, redeem, expected):
    assert simple_maturity_yield_pct(price, redeem) == pytest.approx(expected)


@pytest.mark.parametrize('price,redeem', [(0,110),(-1,110),(100,None),(True,110),(100,float('nan')),(100,float('inf')),(100,0)])
def test_invalid_inputs_are_null(price, redeem):
    assert simple_maturity_yield_pct(price, redeem) is None


def test_negative_yield_is_preserved_not_clamped():
    # 125 买入 / 110 赎回 = -12%: 负值是合法业务结果, 不截断为 None
    assert simple_maturity_yield_pct(125, 110) == pytest.approx(-12)


def test_formula_is_unrounded():
    # 计算用未格式化数值, 截断到 2 位小数只发生在前端显示
    assert simple_maturity_yield_pct(100, 100.004) == pytest.approx(0.004)


# ---------------------------------------------------------------------------
# finite_number
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('value,expected', [
    (None, None),
    (True, None),
    (False, None),
    ("", None),
    ("   ", None),
    ("-", None),
    ("abc", None),
    (float("nan"), None),
    (float("inf"), None),
    (float("-inf"), None),
    (0, 0.0),
    (100, 100.0),
    ("100", 100.0),
    (" 110.5 ", 110.5),
    ("1,234.5", 1234.5),
    (-3.5, -3.5),
])
def test_finite_number(value, expected):
    result = finite_number(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# enrich_cell(统一字段补充)
# ---------------------------------------------------------------------------
def test_enrich_computes_yield_from_redeem_cell():
    cell = {"bond_id": "110001", "price": 100, "ytm_rt": 99}
    enriched = enrich_cell(cell, {"bond_id": "110001", "redeem_price": 110})
    # ytm_rt=99 是集思录原生 YTM: 新指标绝不能读它, 100/110 → 10
    assert enriched["simple_maturity_yield_pct"] == pytest.approx(10)
    assert enriched["redeem_price"] == pytest.approx(110)


def test_enrich_does_not_fall_back_to_force_redeem_price():
    # force_redeem_price 是强赎触发价, 与到期赎回价语义不同, 不得混用
    cell = {"price": 100, "force_redeem_price": 130}
    enriched = enrich_cell(cell, {})
    assert enriched["redeem_price"] is None
    assert enriched["simple_maturity_yield_pct"] is None


def test_enrich_does_not_mutate_input_cell():
    cell = {"price": 100}
    redeem_cell = {"redeem_price": 110}
    enriched = enrich_cell(cell, redeem_cell)
    assert enriched is not cell
    assert "redeem_price" not in cell
    assert "simple_maturity_yield_pct" not in cell
    assert redeem_cell == {"redeem_price": 110}
    assert enriched["simple_maturity_yield_pct"] == pytest.approx(10)


def test_enrich_missing_redeem_cell_marks_both_missing():
    cell = {"price": 100}
    enriched = enrich_cell(cell, None)
    assert enriched["redeem_price"] is None
    assert enriched["simple_maturity_yield_pct"] is None


def test_enrich_rejects_nonpositive_price():
    enriched = enrich_cell({"price": 0}, {"redeem_price": 110})
    assert enriched["redeem_price"] == pytest.approx(110)
    assert enriched["simple_maturity_yield_pct"] is None


def test_enrich_overwrites_stale_redeem_price_from_own_cell():
    # 原始 cell 上可能残留同名旧值(如实时源字段): 补充后以 redeem_cell 为唯一事实源
    cell = {"price": 100, "redeem_price": "999"}
    enriched = enrich_cell(cell, {"redeem_price": 110})
    assert enriched["redeem_price"] == pytest.approx(110)
    assert enriched["simple_maturity_yield_pct"] == pytest.approx(10)


def test_enrich_keeps_other_fields():
    cell = {"bond_id": "110001", "price": 100, "dblow": 150.5}
    enriched = enrich_cell(cell, {"redeem_price": 110})
    assert enriched["bond_id"] == "110001"
    assert enriched["dblow"] == pytest.approx(150.5)
