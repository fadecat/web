# -*- coding: utf-8 -*-
"""industry 服务查表测试(B4 细分行业列)。

口径: 「细分行业」= sw_cd 自身层级名称, 不做一级回退冒充三级。
"""
from __future__ import annotations

from backend.services.industry import industry_info_of, industry_name_of


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


# ---------------------------------------------------------------------------
# industry_info_of(T1: 结构化行业信息, 方案 §3.3)
# ---------------------------------------------------------------------------

def test_info_three_level_direct_hit():
    info = industry_info_of("760201")
    assert info == {
        "industry_code": "760201",
        "industry_name": "环保设备Ⅲ",
        "industry_level": 3,
        "industry_mapped_code": "760201",
        "industry_is_fallback": False,
    }


def test_info_two_level_form_hit():
    # 码本身是二级形态: 命中自身层级, 不算回退
    info = industry_info_of("610100")
    assert info == {
        "industry_code": "610100",
        "industry_name": "水泥",
        "industry_level": 2,
        "industry_mapped_code": "610100",
        "industry_is_fallback": False,
    }


def test_info_fallback_marks_level2_mapping():
    # 三级码缺失回退二级码: 名称可用但层级必须如实标注(不冒充精确三级)
    info = industry_info_of("610101")
    assert info == {
        "industry_code": "610101",
        "industry_name": "水泥",
        "industry_level": 2,
        "industry_mapped_code": "610100",
        "industry_is_fallback": True,
    }


def test_info_unknown_code_returns_none():
    assert industry_info_of("999999") is None


def test_info_blank_codes_return_none():
    # 空码调用方归一为枚举 NONE, 服务层统一返回 None
    assert industry_info_of(None) is None
    assert industry_info_of("") is None
    assert industry_info_of("   ") is None


def test_info_matches_name_of_on_all_cases():
    # 旧行为兼容: industry_name_of 与 industry_info_of 的名称口径完全一致
    for code in ("760201", "610100", "610101", "999999", None, "", "   "):
        info = industry_info_of(code)
        expected = industry_name_of(code)
        if info is None:
            assert expected is None
        else:
            assert info["industry_name"] == expected
