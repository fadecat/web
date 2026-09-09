# -*- coding: utf-8 -*-
"""可转债筛选的 Pydantic 输入模型(P2-R01/P2-R02)。

规则与前端 filterValidation.js 对齐:
- 价格/规模/年限非负; 溢价率/到期收益率允许负值
- 拒绝非有限数(query 参数由 FastAPI 转 float, Infinity/NaN 文本在此层 422)
- 区间 min <= max
- 评级只接受 list[str](NONE=无评级占位符), 空数组/缺省 = 不限
"""
from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


def _reject_non_finite(v: float | None) -> float | None:
    """inf/nan 文本经 FastAPI/pydantic float 解析会到这层, 统一拒绝。"""
    if v is not None and not math.isfinite(v):
        raise ValueError("请输入有效数字(不接受 inf/nan)")
    return v


# 非负数值字段统一约束
_NonNegative = Annotated[float, Field(ge=0)]


class IntradayFilterQuery(BaseModel):
    """盘中选债 GET /cb-list/screen/intraday 的数值筛选条件。

    ratings 不在此模型中: GET 以逗号分隔字符串传递, 由路由解析后
    经 normalize_ratings 规范化(FastAPI Depends() 查询模式与
    list 默认工厂不兼容, 见 pydantic list_type 报错)。
    """

    price_min: _NonNegative | None = None
    price_max: _NonNegative | None = None
    curr_iss_amt_max: _NonNegative | None = None
    year_left_min: _NonNegative | None = None
    year_left_max: _NonNegative | None = None
    premium_rt_max: float | None = None   # 允许负值
    ytm_min: float | None = None          # 允许负值

    @model_validator(mode="after")
    def _check(self) -> "IntradayFilterQuery":
        for key in ("price_min", "price_max", "curr_iss_amt_max",
                    "year_left_min", "year_left_max", "premium_rt_max", "ytm_min"):
            _reject_non_finite(getattr(self, key))
        for lo_key, hi_key, label in [
            ("price_min", "price_max", "转债价格"),
            ("year_left_min", "year_left_max", "剩余年限"),
        ]:
            lo = getattr(self, lo_key)
            hi = getattr(self, hi_key)
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{label}：最低值不能大于最高值")
        return self


def normalize_ratings(raw: list[str]) -> list[str]:
    """评级列表规范化: 大写去空白去重去空; NONE 保留(表示无评级)。"""
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        if not isinstance(r, str):
            raise ValueError("ratings 元素必须为字符串")
        token = r.strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


# ---------------------------------------------------------------------------
# 策略模板配置(P2-R02: ratings 只接受 list[str], 拒绝字符串/对象/空串)
# ---------------------------------------------------------------------------

class StrategyTemplateModel(BaseModel):
    """单条策略模板的结构校验。只校验跨层合同字段, 其余字段透传。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    ratings: list[str] = Field(default_factory=list)

    @field_validator("ratings", mode="before")
    @classmethod
    def _ratings_must_be_str_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            raise ValueError("ratings 必须为字符串数组")
        return v

    @field_validator("ratings")
    @classmethod
    def _ratings_no_empty_no_dup(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for x in v:
            token = x.strip().upper()
            if not token:
                raise ValueError("ratings 不允许空字符串")
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    model_config = {"extra": "allow"}  # 其余字段(exclusion_rules 等)不在此层校验


class FactorsConfigModel(BaseModel):
    """POST /cb-list/factors 请求体结构校验。"""

    active_id: str | None = None
    templates: list[StrategyTemplateModel] = Field(min_length=1)

    model_config = {"extra": "allow"}  # version/updated_at 等透传
