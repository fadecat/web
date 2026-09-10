# -*- coding: utf-8 -*-
"""可转债筛选因子目录与策略模板配置。

对齐 v2_cb_rotation 的 factors.py:
- FACTOR_CATALOG: 可用因子字段单一事实源
- 模板配置读写 data/factors.json(含三低默认策略)

设计原则(见 docs/web-refactor.md):
- 配置与打分引擎解耦,模板只存配置,不存计算结果
- 打分/筛选逻辑在 cb_screen.py,基于数据库查询
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR

# ---------------------------------------------------------------------------
# 因子目录(单一事实源, V3 类型化, 方案 §4.2)
# ---------------------------------------------------------------------------
# tab: "basic" = 转债自身因子 | "stock" = 正股因子
# V3 每项新增: type(数据类型)/operators(合法运算符)/filterable/scorable/
#   description(用途说明)/allow_negative(负数阈值允许标记, §4.3:
#   目录显式记录每个数字字段允许负数与否, 不能仅依据 type=number 判断)
# 兼容: 保留旧 field/label/unit/tab 键, 前端既有读取不受影响;
#   集思录 ytm_rt 已移除(R1), 新指标统一为 simple_maturity_yield_pct。

_NUM_OPS = ["gte", "lte", "gt", "between"]


def _num_field(
    field: str,
    label: str,
    unit: str,
    tab: str,
    *,
    allow_negative: bool = False,
    scorable: bool = True,
    description: str = "",
) -> dict[str, Any]:
    """数值型目录项: 统一 gte/lte/gt/between 运算符。"""
    return {
        "field": field,
        "label": label,
        "unit": unit,
        "tab": tab,
        "type": "number",
        "operators": list(_NUM_OPS),
        "filterable": True,
        "scorable": scorable,
        "allow_negative": allow_negative,
        "description": description or label,
    }


def _enum_field(
    field: str,
    label: str,
    unit: str,
    tab: str,
    *,
    operators: list[str],
    description: str = "",
) -> dict[str, Any]:
    """枚举/集合/布尔型目录项: 不可评分。"""
    return {
        "field": field,
        "label": label,
        "unit": unit,
        "tab": tab,
        "type": (
            "set" if operators == ["not_any"]
            else "boolean" if operators == ["eq"]
            else "enum"
        ),
        "operators": list(operators),
        "filterable": True,
        "scorable": False,
        "allow_negative": False,
        "description": description or label,
    }


FACTOR_CATALOG: list[dict[str, Any]] = [
    _num_field("dblow", "双低值", "", "basic",
               description="价格+溢价率百分点的双低复合值"),
    _num_field("premium_rt", "转股溢价率", "%", "basic", allow_negative=True,
               description="转股溢价率, 允许负值"),
    _num_field("curr_iss_amt", "剩余规模", "亿", "basic",
               description="剩余发行规模, 非负"),
    _num_field("convert_value", "转股价值", "", "basic",
               description="转股价值, 非负"),
    _num_field("year_left", "剩余年限", "年", "basic",
               description="剩余年限, 非负"),
    _num_field("price", "当前价格", "元", "basic",
               description="转债当前价格(DB 快照为收盘价), 非负"),
    _num_field("convert_amt_ratio", "转债市占比", "%", "basic",
               description="转债市值占比, 非负"),
    _num_field("volume", "成交额", "万", "basic",
               description="成交额, 非负"),
    _num_field("increase_rt", "涨跌幅", "%", "basic", allow_negative=True,
               description="当日涨跌幅, 允许负值"),
    _num_field("pb", "市净率", "倍", "stock", allow_negative=True,
               description="市净率; 负值表示净资产为负"),
    _num_field("sprice", "正股收盘价", "元", "stock",
               description="正股价格, 非负"),
    _num_field("sincrease_rt", "正股涨跌幅", "%", "stock", allow_negative=True,
               description="正股当日涨跌幅, 允许负值"),
    # V3 新增指标(方案 §3.1/§4.2)
    _num_field("simple_maturity_yield_pct", "简单到期收益率", "%", "basic",
               allow_negative=True,
               description="公共公式 (到期赎回价-现价)/现价×100, 未年化不含票息和税; 允许负值"),
    _num_field("redeem_price", "到期赎回价", "元", "basic",
               description="到期赎回价(来自强赎快照 redeem_price, 非强赎触发价), 正数"),
    _num_field("redeem_remain_days", "距强赎触发天数", "天", "basic",
               allow_negative=True, scorable=False,
               description="距强赎触发剩余天数; 数据可为负(已过触发日), 负阈值允许"),
    _num_field("listed_days", "上市天数", "天", "basic", scorable=False,
               description="上市至数据交易日/实时时区日期的自然日天数, 阈值非负"),
    _enum_field("industry_code", "细分行业", "", "basic",
                operators=["in", "not_in"],
                description="申万2021原始行业码, 空码归一为 NONE; 筛选匹配原始码"),
    _enum_field("rating_cd", "评级", "", "basic",
                operators=["in", "not_in"],
                description="评级目录, 含 NONE(无评级)与快照发现的未知评级"),
    _enum_field("redeem_icons", "强赎状态", "", "basic",
                operators=["not_any"],
                description="强赎标记集合 R/O/B/G, 命中任一所选即排除"),
    _enum_field("stock_is_st", "正股ST", "", "stock",
                operators=["eq"],
                description="正股名称含 ST/*ST(复用现有识别定义)"),
    _enum_field("code", "转债代码", "", "basic",
                operators=["not_in"],
                description="个券排除, 归一为 6 位代码"),
]

# field -> 目录项索引(schema 校验与条件引擎共用)
FACTOR_CATALOG_BY_FIELD: dict[str, dict[str, Any]] = {
    entry["field"]: entry for entry in FACTOR_CATALOG
}

FACTORS_PATH = DATA_DIR / "factors.json"

# 默认评级全集(白名单取消后仅作旧配置 excluded_ratings 反转的兜底基准)
DEFAULT_RATINGS = {"AAA", "AA+", "AA", "AA-", "A+", "A", "A-"}


# ---------------------------------------------------------------------------
# 默认模板(三低策略)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "active_id": "three_low",
    "templates": [
        {
            "id": "three_low",
            "name": "三低策略",
            "description": "双低值 + 溢价率 + 剩余规模综合评分",
            "target_count": 10,
            "hold_tolerance": 0,
            "exclusion_rules": [
                {"field": "pb",                "label": "市净率",     "op": "lt", "threshold": 1,   "unit": "倍", "enabled": True},
                {"field": "year_left",         "label": "剩余年限",   "op": "lt", "threshold": 1,   "unit": "年", "enabled": True},
                {"field": "curr_iss_amt",      "label": "剩余规模",   "op": "lt", "threshold": 1,   "unit": "亿", "enabled": True},
                {"field": "curr_iss_amt",      "label": "剩余规模",   "op": "gt", "threshold": 20,  "unit": "亿", "enabled": True},
                {"field": "convert_amt_ratio", "label": "转债市占比", "op": "gt", "threshold": 20,  "unit": "%",  "enabled": True},
                {"field": "sprice",            "label": "正股收盘价", "op": "lt", "threshold": 5,   "unit": "元", "enabled": True},
                {"field": "convert_value",     "label": "转股价值",   "op": "gt", "threshold": 127, "unit": "",   "enabled": True},
            ],
            "strategy_factors": [
                {"field": "dblow",        "label": "双低值",     "ascending": True, "weight": 1.0, "enabled": True},
                {"field": "premium_rt",   "label": "转股溢价率", "ascending": True, "weight": 1.0, "enabled": True},
                {"field": "curr_iss_amt", "label": "剩余规模",   "ascending": True, "weight": 1.0, "enabled": True},
            ],
            "excluded_redeem_icons": ["R", "O", "B"],
            "redeem_safe_days": 2,
            "excluded_bond_codes": [],
            "ratings": sorted(DEFAULT_RATINGS),
            "min_listing_days": 0,
        }
    ],
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def normalize_bond_code(code: str | None) -> str:
    """转债代码规范化为大写稳定形式(支持 6 位纯数字或带 .SH/.SZ 后缀)。"""
    if code is None:
        return ""
    normalized = str(code).strip().upper()
    if not normalized:
        return ""
    if normalized.endswith(".SH") or normalized.endswith(".SZ"):
        return normalized
    if len(normalized) == 6 and normalized.isdigit():
        if normalized.startswith("11"):
            return f"{normalized}.SH"
        if normalized.startswith("12"):
            return f"{normalized}.SZ"
    return normalized


def build_bond_code_match_set(codes: list | None) -> set[str]:
    """构建转债代码匹配集(同时接受 6 位纯数字和带 .SH/.SZ 后缀)。"""
    match_set: set[str] = set()
    for item in codes or []:
        code = item.get("code") if isinstance(item, dict) else item
        raw = str(code).strip().upper()
        if not raw:
            continue
        match_set.add(raw)
        normalized = normalize_bond_code(raw)
        if normalized:
            match_set.add(normalized)
    return match_set


def _normalize_excluded_entry(item: str | dict) -> dict | None:
    code = item.get("code") if isinstance(item, dict) else item
    normalized = normalize_bond_code(code)
    if not normalized:
        return None
    name = ""
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
    return {"code": normalized, "name": name}


def _normalize_templates(data: dict) -> dict:
    """规范化模板配置: min_listing_days 转 int、排除代码去重。"""
    normalized = json.loads(json.dumps(data))
    for tmpl in normalized.get("templates", []):
        try:
            tmpl["min_listing_days"] = max(0, int(tmpl.get("min_listing_days") or 0))
        except (TypeError, ValueError):
            tmpl["min_listing_days"] = 0
        raw_items = tmpl.get("excluded_bond_codes") or []
        deduped: list[dict] = []
        seen: set[str] = set()
        for item in raw_items:
            entry = _normalize_excluded_entry(item)
            if not entry:
                continue
            if entry["code"] in seen:
                continue
            seen.add(entry["code"])
            deduped.append(entry)
        tmpl["excluded_bond_codes"] = deduped
        # 评级筛选: 统一大写, 接受任意非空评级(AAA~A- 之外如 BBB、无评级等
        # 在抓取层放开后都会出现), 不再做白名单裁剪; 空 = 不限
        raw_ratings = tmpl.get("ratings")
        if raw_ratings is None:
            # 兼容旧配置(此前为排除语义的 excluded_ratings): 反转为保留语义
            legacy = tmpl.get("excluded_ratings") or []
            tmpl["ratings"] = sorted(
                DEFAULT_RATINGS - {str(x).strip().upper() for x in legacy}
            )
        else:
            tmpl["ratings"] = sorted({
                r
                for r in (str(x).strip().upper() for x in raw_ratings)
                if r
            })
        # R4-01: 迁移完成后删除旧字段, 使迁移结果可原样回存。
        # 读取旧文件的输出必须只含当前结构, 否则 GET → POST 原样保存会
        # 被 StrategyTemplateModel 的旧字段拦截(422)。
        tmpl.pop("excluded_ratings", None)
    return normalized


def read_config() -> dict:
    """读取策略模板配置;文件不存在或损坏时回退到默认配置。"""
    if FACTORS_PATH.exists():
        try:
            with open(FACTORS_PATH, encoding="utf-8") as f:
                return _normalize_templates(json.load(f))
        except Exception:
            pass
    return _normalize_templates(DEFAULT_CONFIG)


def write_config(data: dict) -> dict:
    """写策略模板配置到 data/factors.json,返回规范化后的配置。"""
    normalized = _normalize_templates(data)
    FACTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(FACTORS_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    return normalized


def get_active_template() -> dict | None:
    """读取当前 active 模板;无则返回第一个模板或 None。"""
    cfg = read_config()
    active_id = cfg.get("active_id")
    templates = cfg.get("templates", [])
    for tmpl in templates:
        if tmpl.get("id") == active_id:
            return tmpl
    return templates[0] if templates else None
