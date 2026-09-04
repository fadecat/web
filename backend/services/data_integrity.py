# -*- coding: utf-8 -*-
"""数据完整性校验(泛化版)。

背景(2026-09-03 回顾): 完整性校验原先只覆盖风格轮动的指数日线,
估值/转债各表的历史空洞无从发现。本模块把「条数概况 + 日期空洞扫描」
泛化为任意「日频追加式」表可用,由注册表驱动统一巡检。

两类扫描模式:
- per_entity: 按实体(如指数代码)分别扫描其日期序列的空洞。
              适用于实体集合长期稳定的表(指数不会频繁增删)。
- global:     只扫描全表 distinct 日期序列的空洞。
              适用于实体频繁进出的表(转债上市/退市),
              某天缺失即代表当天整表任务失败。

阈值: 相邻日期间隔 > max_gap_days 天视为可疑空洞
(正常周末 2-3 天, A 股长假最多约 9 天, 默认 11 留余量)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from backend.models.valuation import (
    CbDailySnapshot,
    CbIndexDaily,
    CbRedeemDaily,
    CnBondYield,
    IndexDailyQuote,
    IndexDividendYield,
    IndexValuationSnapshot,
)

# ---------------------------------------------------------------------------
# 巡检注册表: 启动巡检按此表逐项执行。新增日频表后在此登记即可被覆盖。
# ---------------------------------------------------------------------------

DAILY_TABLE_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "指数日线(风格轮动)",
        "model": IndexDailyQuote,
        "entity_attr": "index_code",
        "date_attr": "trade_date",
        "mode": "per_entity",
    },
    {
        "name": "指数估值快照",
        "model": IndexValuationSnapshot,
        "entity_attr": "index_code",
        "date_attr": "trade_date",
        "mode": "per_entity",
    },
    {
        "name": "指数股息率",
        "model": IndexDividendYield,
        "entity_attr": "index_code",
        "date_attr": "trade_date",
        "mode": "per_entity",
    },
    {
        "name": "国债收益率",
        "model": CnBondYield,
        "entity_attr": None,
        "date_attr": "trade_date",
        "mode": "global",
    },
    {
        "name": "转债等权指数",
        "model": CbIndexDaily,
        "entity_attr": None,
        "date_attr": "trade_date",
        "mode": "global",
    },
    {
        "name": "转债全量快照",
        "model": CbDailySnapshot,
        "entity_attr": None,  # 转债进出频繁,按全表交易日扫描
        "date_attr": "trade_date",
        "mode": "global",
    },
    {
        "name": "转债强赎列表",
        "model": CbRedeemDaily,
        "entity_attr": None,
        "date_attr": "trade_date",
        "mode": "global",
    },
]


def _load_dates(
    db: Session,
    model: type,
    date_attr: str,
    entity_attr: str | None,
    entity_value: str | None,
) -> list[Any]:
    """加载升序去重前的日期列表(按过滤条件)。"""
    col = getattr(model, date_attr)
    stmt = select(col)
    if entity_attr and entity_value is not None:
        stmt = stmt.where(getattr(model, entity_attr) == entity_value)
    rows = db.execute(stmt.order_by(col.asc())).all()
    return [r[0] for r in rows]


def _scan_gaps(dates: list[Any], max_gap_days: int) -> list[dict[str, Any]]:
    """对升序日期列表扫描异常空洞,返回 [{prev, next, gap_days}, ...]。"""
    gaps: list[dict[str, Any]] = []
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).days
        if gap > max_gap_days:
            gaps.append({
                "prev": dates[i - 1].isoformat(),
                "next": dates[i].isoformat(),
                "gap_days": gap,
            })
    return gaps


def get_table_summary(
    db: Session,
    model: type,
    date_attr: str = "trade_date",
    entity_attr: str | None = None,
    entity_value: str | None = None,
) -> dict[str, Any] | None:
    """通用落库概况: 条数 + 最早/最晚日期。空返回 None。"""
    dates = _load_dates(db, model, date_attr, entity_attr, entity_value)
    if not dates:
        return None
    return {"count": len(dates), "first": dates[0], "last": dates[-1]}


def scan_date_gaps_generic(
    db: Session,
    model: type,
    date_attr: str = "trade_date",
    entity_attr: str | None = None,
    entity_value: str | None = None,
    max_gap_days: int = 11,
) -> list[dict[str, Any]]:
    """通用日期空洞扫描(单表或单实体维度)。"""
    dates = _load_dates(db, model, date_attr, entity_attr, entity_value)
    return _scan_gaps(dates, max_gap_days)


def get_entity_codes(db: Session, model: type, entity_attr: str) -> list[str]:
    """列出表中全部实体值(如全部指数代码)。"""
    rows = db.execute(select(distinct(getattr(model, entity_attr)))).all()
    return sorted(r[0] for r in rows)


def scan_all_daily_tables(
    db: Session,
    max_gap_days: int = 11,
    max_entities_per_table: int = 50,
) -> dict[str, Any]:
    """按注册表全量巡检,返回结构化报告(供日志输出与前端健康页复用)。

    per_entity 表会对每个实体分别扫描; 实体数超过 max_entities_per_table
    时只汇总条数不逐个扫(防转债类大表拖慢启动)。
    """
    report: dict[str, Any] = {"tables": [], "total_gaps": 0}

    for entry in DAILY_TABLE_REGISTRY:
        model = entry["model"]
        entity_attr = entry["entity_attr"]
        date_attr = entry["date_attr"]
        table_report: dict[str, Any] = {
            "name": entry["name"],
            "mode": entry["mode"],
            "entities": [],
            "gaps": [],
        }

        if entity_attr and entry["mode"] == "per_entity":
            codes = get_entity_codes(db, model, entity_attr)
            if len(codes) > max_entities_per_table:
                table_report["skipped"] = (
                    f"实体数 {len(codes)} 超过巡检上限 {max_entities_per_table},仅统计概况"
                )
            for code in codes[:max_entities_per_table]:
                summary = get_table_summary(
                    db, model, date_attr, entity_attr, code,
                )
                gaps = scan_date_gaps_generic(
                    db, model, date_attr, entity_attr, code, max_gap_days,
                )
                table_report["entities"].append({"code": code, **(summary or {"count": 0})})
                table_report["gaps"].extend(
                    {**g, "entity": code} for g in gaps
                )
        else:
            summary = get_table_summary(db, model, date_attr)
            gaps = scan_date_gaps_generic(db, model, date_attr, None, None, max_gap_days)
            table_report["entities"] = [summary] if summary else []
            table_report["gaps"] = gaps

        report["total_gaps"] += len(table_report["gaps"])
        report["tables"].append(table_report)

    return report
