# -*- coding: utf-8 -*-
"""industry 服务查表测试(B4 细分行业列)。

口径: 「细分行业」= sw_cd 自身层级名称, 不做一级回退冒充三级。
"""
from __future__ import annotations

from backend.services.industry import industry_name_of


def test_three_level_code_returns_level3_name():
    # 正常三级码 -> 三级名(细分行业本义)
    assert industry_name_of("760201") == "环保设备Ⅲ"
    assert industry_name_of("640107") == "仪器仪表"


def test_second_level_form_returns_level2_name():
    # 码本身是二级形态 -> 该级名称(不冒充三级)
    assert industry_name_of("610100") == "水泥"


def test_unknown_code_returns_none():
    assert industry_name_of("999999") is None


def test_three_level_missing_falls_back_to_level2():
    # B4 兜底: 三级码缺失但二级码已收录时回退(610101 -> 610100 水泥)
    # 冀东转债 127025 的 sw_cd=610101 未在三级映射, 但二级 610100 已收录
    assert industry_name_of("610101") == "水泥"


def test_fallback_does_not_trigger_for_level2_ending_00():
    # 已是二级形态(以 00 结尾)不二次回退, 直接按自身层级查
    assert industry_name_of("610100") == "水泥"


def test_blank_inputs_return_none():
    assert industry_name_of(None) is None
    assert industry_name_of("") is None
    assert industry_name_of("   ") is None


def test_lookup_is_repeatable_and_cached():
    # lru_cache 下重复查询稳定
    assert industry_name_of("760201") == industry_name_of("760201")
