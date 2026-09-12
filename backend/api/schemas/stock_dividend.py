# -*- coding: utf-8 -*-
"""高股息筛选预设(preset)模型: 服务端保存/校验用。

表单键集与前端 stockDividend.mjs 的 emptyForm() 严格一致(extra=forbid,
多键/错型一律 422); 预设支持另存为/重命名/删除/设默认, 服务端只做形状与
一致性校验, 不解释语义。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DividendPresetForm(BaseModel):
    """筛选表单值(null = 未启用; soeOnly = 仅国资白名单)。

    行业/排除行业/地域均为多选数组(sw_cd 前缀 / 省份精确); 与前端
    emptyForm() 键集严格一致(extra=forbid)。
    """

    model_config = {"extra": "forbid"}

    markets: list[Literal["sh", "sz"]] = []
    # 上限 64 ≥ 申万一级 31 个: 「全选一级行业」可整存
    industries: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=64
    )
    excludeIndustries: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=64
    )
    provinces: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        default_factory=list, max_length=64
    )
    peMax: float | None = None
    pbMax: float | None = None
    peTMax: float | None = None
    pbTMax: float | None = None
    intDebtMax: float | None = None
    dividendMin: float | None = None
    # 分红率下限(派生指标: 股息率TTM×PE-TTM, 前端计算)
    payoutMin: float | None = None
    roeMin: float | None = None
    roeAverageMin: float | None = None
    revenueAvgMin: float | None = None
    profitAvgMin: float | None = None
    epsGrowthTtmMin: float | None = None
    cashflowAvgMin: float | None = None
    totalValueMin: float | None = None
    totalValueMax: float | None = None
    floatValueMin: float | None = None
    floatValueMax: float | None = None
    soeOnly: bool = False


class DividendPreset(BaseModel):
    """单个筛选预设(名称唯一性由路由层校验)。"""

    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=40)
    form: DividendPresetForm = Field(default_factory=DividendPresetForm)


class DividendPresetsConfig(BaseModel):
    """预设配置整体(全量替换式保存)。"""

    model_config = {"extra": "forbid"}

    version: int = Field(default=1, ge=1, le=1)
    active_id: str
    presets: list[DividendPreset] = Field(min_length=1, max_length=20)
