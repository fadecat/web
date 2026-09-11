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
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.services.cb_factors import FACTOR_CATALOG_BY_FIELD
from backend.services.cb_template_migration import TEMPLATE_NAME_MAX_CHARS


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
    """单条策略模板的结构校验。只校验跨层合同字段, 其余字段透传。

    合同(R3-02/R3-05):
    - ratings 缺省/显式 [] = 不限; null = 422(不接受"未选择"与"不限"混淆)
    - 归一(strip+upper)后重复 = 422(不静默去重)
    - 旧字段 excluded_ratings 是读旧文件的迁移语义, 新 POST 出现即 422
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    ratings: list[str] = Field(default_factory=list)

    @field_validator("ratings", mode="before")
    @classmethod
    def _ratings_must_be_str_list(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("ratings 不允许为 null(不限请传空数组或缺省)")
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
                raise ValueError(f"ratings 存在重复项: {token}")
            seen.add(token)
            out.append(token)
        return out

    @model_validator(mode="after")
    def _reject_legacy_fields(self) -> "StrategyTemplateModel":
        # extra="allow" 下旧字段会进 __pycache__ 之外的 extra; 显式拦截
        extra_keys = set(self.model_extra or {})
        banned = {"excluded_ratings"}
        if banned & extra_keys:
            raise ValueError("excluded_ratings 是旧配置字段, 新请求不接受(不限评级请传 ratings: [])")
        return self

    model_config = {"extra": "allow"}  # 其余字段(exclusion_rules 等)不在此层校验


class FactorsConfigModel(BaseModel):
    """POST /cb-list/factors 请求体结构校验。"""

    active_id: str | None = None
    templates: list[StrategyTemplateModel] = Field(min_length=1)

    model_config = {"extra": "allow"}  # version/updated_at 等透传


# ---------------------------------------------------------------------------
# V3 模板协议(转债选债 V3, 方案 §4; 旧 HTTP 模型与验证语义原样保留)
# ---------------------------------------------------------------------------

_StrictBool = Annotated[bool, Field(strict=True)]

# 枚举合法值域
_REDEEM_ICON_VALUES = {"R", "O", "B", "G"}
_INDUSTRY_CODE_RE = re.compile(r"^\d{6}$")
# 简单收益率/赎回价的新条件必须 exclude(§4.4), 不能配置 include
_MISSING_EXCLUDE_ONLY_FIELDS = {"simple_maturity_yield_pct", "redeem_price"}


def _strict_finite_number(value: Any, label: str) -> float:
    """strict 数值校验: 拒绝 bool 冒充/字符串/NaN/Infinity(§4.3)。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须为数值(不接受字符串/布尔)")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} 不接受 NaN/Infinity")
    return number


def _require_clean_str_list(value: Any, label: str) -> list[str]:
    """枚举值必须是字符串数组: 拒绝字符串替代数组、空项、去空白后重复项。"""
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError(f"{label} 必须为字符串数组(不接受单个字符串或非字符串项)")
    stripped = [x.strip() for x in value]
    if any(not x for x in stripped):
        raise ValueError(f"{label} 不允许空字符串项")
    if len(set(stripped)) != len(stripped):
        raise ValueError(f"{label} 存在重复项(去空白后)")
    return stripped


def _normalize_code6(value: str) -> str:
    """转债代码归一为 6 位(去交易所后缀)。"""
    text = value.strip().upper()
    for suffix in (".SH", ".SZ"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


class ConditionModel(BaseModel):
    """V3 单条筛选条件(§4.1/§4.3)。

    强校验合同:
    - 字段与运算符必须来自服务端因子目录(cb_factors, 唯一事实源);
    - 数值 strict: 拒绝 bool 冒充数值、字符串数字、NaN/Infinity;
    - between 两端有限且 lo<=hi; 目录 allow_negative=false 的字段拒绝负阈值;
    - 枚举值必须是字符串数组, 启用的 in/not_in/not_any 不能为空集合;
    - enabled=false 也要求结构有效(不伪装为合法停用条件);
    - negative=include 仅允许 redeem_remain_days 的迁移条件;
    - 简单收益率/赎回价条件 missing 必须为 exclude。
    """

    id: str
    field: str
    op: str
    value: Any = None
    enabled: _StrictBool = True
    missing: Literal["exclude", "include"] = "exclude"
    negative: Literal["compare", "include"] = "compare"

    @field_validator("id")
    @classmethod
    def _id_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("条件 id 不能为空白(必须为稳定唯一 ID)")
        return v

    @model_validator(mode="after")
    def _validate_against_catalog(self) -> "ConditionModel":
        entry = FACTOR_CATALOG_BY_FIELD.get(self.field)
        if entry is None:
            raise ValueError(f"未知筛选字段: {self.field}")
        if self.op not in entry["operators"]:
            raise ValueError(
                f"字段 {self.field}({entry['type']} 类型)不支持运算符 {self.op},"
                f" 可用: {entry['operators']}"
            )
        ftype = entry["type"]
        if ftype == "number":
            self._validate_number_value(entry)
        elif ftype in ("enum", "set"):
            self._validate_enum_value(entry)
        else:  # boolean
            if not isinstance(self.value, bool):
                raise ValueError("布尔条件 value 必须为 true/false(不接受 0/1)")
        if self.negative == "include" and self.field != "redeem_remain_days":
            raise ValueError("negative=include 仅允许 redeem_remain_days 的迁移条件使用")
        if self.missing == "include" and self.field in _MISSING_EXCLUDE_ONLY_FIELDS:
            raise ValueError(
                f"字段 {self.field} 的新条件必须 missing=exclude(缺失不放行), 不能配置 include"
            )
        return self

    def _validate_number_value(self, entry: dict[str, Any]) -> None:
        label = entry["label"]
        allow_negative = entry["allow_negative"]

        def _check_sign(num: float) -> None:
            if num < 0 and not allow_negative:
                raise ValueError(f"{label} 不允许负数阈值")

        if self.op == "between":
            if (
                not isinstance(self.value, list)
                or len(self.value) != 2
            ):
                raise ValueError("between 条件 value 必须为 [下限, 上限] 二元数组")
            lo = _strict_finite_number(self.value[0], f"{label}下限")
            hi = _strict_finite_number(self.value[1], f"{label}上限")
            if lo > hi:
                raise ValueError(f"{label}区间下限不能大于上限")
            _check_sign(lo)
            _check_sign(hi)
            self.value = [lo, hi]
            return
        number = _strict_finite_number(self.value, label)
        _check_sign(number)
        self.value = number

    def _validate_enum_value(self, entry: dict[str, Any]) -> None:
        label = entry["label"]
        items = _require_clean_str_list(self.value, label)
        if self.enabled and not items:
            raise ValueError(f"启用的 {self.op} 条件不允许空集合({label})")
        if self.field == "redeem_icons":
            uppered = [x.upper() for x in items]
            bad = [x for x in uppered if x not in _REDEEM_ICON_VALUES]
            if bad:
                raise ValueError(f"强赎标记只接受 R/O/B/G, 收到: {bad}")
            items = uppered
        elif self.field == "industry_code":
            bad = [x for x in items if x != "NONE" and not _INDUSTRY_CODE_RE.match(x)]
            if bad:
                raise ValueError(
                    f"行业条件只接受 6 位行业代码或 NONE, 不接受行业名称: {bad}"
                )
        elif self.field == "code":
            normalized = [_normalize_code6(x) for x in items]
            bad = [x for x in normalized if not _INDUSTRY_CODE_RE.match(x)]
            if bad:
                raise ValueError(
                    f"转债代码必须为 6 位代码或带 .SH/.SZ 后缀: {bad}"
                )
            items = normalized
        self.value = items


class ScoringFactorModel(BaseModel):
    """V3 单条评分因子(§4.3): 只允许 scorable 字段, weight 为有限正数。"""

    field: str
    ascending: _StrictBool = True
    weight: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    enabled: _StrictBool = True

    @model_validator(mode="after")
    def _field_must_be_scorable(self) -> "ScoringFactorModel":
        entry = FACTOR_CATALOG_BY_FIELD.get(self.field)
        if entry is None:
            raise ValueError(f"未知评分字段: {self.field}")
        if not entry["scorable"]:
            raise ValueError(f"字段 {self.field} 不可评分(目录 scorable=false)")
        return self


class _ConditionsTemplateBase(BaseModel):
    """V3 模板公共字段(保存模板与执行请求共用, §4.1)。

    extra="forbid": V3 拒绝未知业务字段, 不再 extra=allow 放行。
    """

    id: str
    name: str
    description: str = ""
    conditions: list[ConditionModel] = Field(default_factory=list)
    strategy_factors: list[ScoringFactorModel] = Field(default_factory=list)
    target_count: Annotated[int, Field(strict=True, ge=1, le=50)] = 10
    hold_tolerance: Annotated[int, Field(strict=True, ge=0, le=20)] = 0

    model_config = {"extra": "forbid"}

    @field_validator("id", "name")
    @classmethod
    def _not_blank(cls, v: str, info) -> str:
        v = v.strip()
        if not v:
            label = "模板 id" if info.field_name == "id" else "模板名"
            raise ValueError(f"{label}不能为空白")
        if info.field_name == "name" and len(v) > TEMPLATE_NAME_MAX_CHARS:
            # §2.3: 名称 trim 后 1~40 Unicode 字符
            raise ValueError(
                f"模板名 trim 后不能超过 {TEMPLATE_NAME_MAX_CHARS} 个字符(当前 {len(v)})"
            )
        return v

    @model_validator(mode="after")
    def _no_duplicate_scoring_fields(self) -> "_ConditionsTemplateBase":
        fields = [f.field for f in self.strategy_factors if f.enabled]
        if len(set(fields)) != len(fields):
            raise ValueError("同一字段不能重复评分(避免覆盖旧 score_key)")
        return self


class SelectionTemplateModel(_ConditionsTemplateBase):
    """V3 保存配置中的单条模板(§4.1)。"""

    migration_issues: list[dict[str, Any]] = Field(default_factory=list)


class SelectionRunModel(_ConditionsTemplateBase):
    """V3 执行请求(§6.2: 保留平铺模板+source 形状, 顶层 schema_version=3)。

    执行前先校验再抓取: 校验失败时任何数据源都不应被调用。
    migration_issues 随模板平铺携带(迁移产物回存形状); 路由检查到
    status=pending 的条目时以 409 TEMPLATE_REVIEW_REQUIRED 拒绝执行(§5.2)。
    """

    schema_version: Annotated[int, Field(strict=True)]
    source: Literal["db", "live"] = "db"
    migration_issues: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _schema_version_must_be_3(cls, v: int) -> int:
        if v != 3:
            raise ValueError("schema_version 必须为 3")
        return v


class SelectionConfigModel(BaseModel):
    """V3 整份配置保存请求(§4.1/§5.3)。

    - version=3; revision 必填(§5.3: V3 保存必须携带 revision);
    - 模板 id 唯一, active_id 必须存在;
    - updated_at 为存储产物(GET 回读携带), 显式声明以兼容原样回存,
      其余未知顶层业务字段一律拒绝。
    """

    version: Annotated[int, Field(strict=True)]
    revision: str
    active_id: str
    templates: list[SelectionTemplateModel] = Field(min_length=1)
    updated_at: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("version")
    @classmethod
    def _version_must_be_3(cls, v: int) -> int:
        if v != 3:
            raise ValueError("配置 version 必须为 3")
        return v

    @field_validator("revision", "active_id")
    @classmethod
    def _revision_and_active_not_blank(cls, v: str, info) -> str:
        v = v.strip()
        if not v:
            label = "revision" if info.field_name == "revision" else "active_id"
            raise ValueError(f"{label}不能为空白")
        return v

    @model_validator(mode="after")
    def _ids_unique_and_active_exists(self) -> "SelectionConfigModel":
        ids = [t.id for t in self.templates]
        if len(set(ids)) != len(ids):
            dupes = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"模板 id 必须唯一, 重复: {dupes}")
        if self.active_id not in ids:
            raise ValueError(f"active_id 必须指向存在的模板, 收到: {self.active_id}")
        return self

    @model_validator(mode="after")
    def _names_unique_casefold(self) -> "SelectionConfigModel":
        # §2.3: 后端用 casefold() 检查集合内重复(trim 已由字段校验完成)
        folded = [t.name.casefold() for t in self.templates]
        if len(set(folded)) != len(folded):
            dupes = sorted(
                {t.name for t, f in zip(self.templates, folded) if folded.count(f) > 1}
            )
            raise ValueError(f"模板名(忽略大小写)不能重复: {dupes}")
        return self
