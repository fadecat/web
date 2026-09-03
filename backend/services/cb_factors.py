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
# 因子目录(单一事实源)
# ---------------------------------------------------------------------------
# tab: "basic" = 转债自身因子 | "stock" = 正股因子
FACTOR_CATALOG: list[dict[str, str]] = [
    {"field": "dblow",             "label": "双低值",     "unit": "",   "tab": "basic"},
    {"field": "premium_rt",        "label": "转股溢价率", "unit": "%",  "tab": "basic"},
    {"field": "curr_iss_amt",      "label": "剩余规模",   "unit": "亿", "tab": "basic"},
    {"field": "convert_value",     "label": "转股价值",   "unit": "",   "tab": "basic"},
    {"field": "year_left",         "label": "剩余年限",   "unit": "年", "tab": "basic"},
    {"field": "price",             "label": "收盘价",     "unit": "元", "tab": "basic"},
    {"field": "convert_amt_ratio", "label": "转债市占比", "unit": "%",  "tab": "basic"},
    {"field": "volume",            "label": "成交额",     "unit": "万", "tab": "basic"},
    {"field": "increase_rt",       "label": "涨跌幅",     "unit": "%",  "tab": "basic"},
    {"field": "ytm_rt",            "label": "到期收益率", "unit": "%",  "tab": "basic"},
    {"field": "pb",                "label": "市净率",     "unit": "倍", "tab": "stock"},
    {"field": "sprice",            "label": "正股收盘价", "unit": "元", "tab": "stock"},
    {"field": "sincrease_rt",      "label": "正股涨跌幅", "unit": "%",  "tab": "stock"},
]

FACTORS_PATH = DATA_DIR / "factors.json"


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
