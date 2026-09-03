# -*- coding: utf-8 -*-
"""市场估值板块存储层。

职责: 接收 fetcher 的输出,写入 ORM 宽表。
设计原则:
- 幂等: 同一 (index_code, trade_date) 重复写入时跳过,不报错。
- 追加式: 不覆盖历史,全量保存。
- 批量: fetcher 返回全部历史行,一次性灌入。
- 与抓取解耦: 本层只管「写」,不管何时抓、抓什么。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import (
    CnBondYield,
    IndexDividendYield,
    IndexValuationSnapshot,
)

# ---------------------------------------------------------------------------
# 字段映射常量: 源数据分位标签 -> ORM 列名后缀
# ---------------------------------------------------------------------------

_PERCENTILE_LABEL_TO_COL: dict[str, str] = {
    "3M": "3m",
    "6M": "6m",
    "1Y": "1y",
    "2Y": "2y",
    "3Y": "3y",
    "5Y": "5y",
    "10Y": "10y",
    "今年以来": "ytd",
    "成立以来": "bgn",
}


# ---------------------------------------------------------------------------
# 估值快照落库
# ---------------------------------------------------------------------------

def save_valuation_snapshots(
    db: Session,
    index_code: str,
    index_name: str,
    records: list[dict[str, Any]],
) -> int:
    """批量写入估值分位历史数据。

    参数:
        db: SQLAlchemy 会话
        index_code: 指数代码
        index_name: 指数名称
        records: fetch_index_valuation_percentile 返回的列表
                 [{trade_date, metrics: {PE/PB/PS: {current, percentiles}}}, ...]

    返回: 新写入行数(已存在的跳过)。
    """
    inserted = 0
    for record in records:
        trade_date = date.fromisoformat(record["trade_date"])
        metrics = record["metrics"]

        existing = db.query(IndexValuationSnapshot).filter_by(
            index_code=index_code,
            trade_date=trade_date,
        ).first()
        if existing:
            continue

        row = IndexValuationSnapshot(
            index_code=index_code,
            index_name=index_name,
            trade_date=trade_date,
        )

        for metric_name, fields in metrics.items():
            prefix = _metric_prefix(metric_name)
            if prefix is None:
                continue
            setattr(row, prefix, fields.get("current"))
            percentiles: dict[str, float | None] = fields.get("percentiles", {})
            for label, value in percentiles.items():
                col_suffix = _PERCENTILE_LABEL_TO_COL.get(label)
                if col_suffix:
                    setattr(row, f"{prefix}_percentile_{col_suffix}", value)

        db.add(row)
        inserted += 1

    db.commit()
    return inserted


def _metric_prefix(metric_name: str) -> str | None:
    """指标显示名 -> ORM 列前缀。PE(TTM)->pe, PB(LF)->pb, PS(TTM)->ps。"""
    if metric_name.startswith("PE"):
        return "pe"
    if metric_name.startswith("PB"):
        return "pb"
    if metric_name.startswith("PS"):
        return "ps"
    return None


# ---------------------------------------------------------------------------
# 股息率落库
# ---------------------------------------------------------------------------

def save_dividend_yield(
    db: Session,
    data: dict[str, Any],
) -> IndexDividendYield | None:
    """将股息率数据写入表(单条,fetch_index_dividend_yield 返回最新值)。

    参数:
        db: SQLAlchemy 会话
        data: fetch_index_dividend_yield 返回的 dict

    返回: 新写入的 ORM 对象; 已存在则返回 None(跳过)。
    """
    index_code = data["index_code"]
    trade_date = date.fromisoformat(data["index_dividend_yield_date"])

    existing = db.query(IndexDividendYield).filter_by(
        index_code=index_code,
        trade_date=trade_date,
    ).first()
    if existing:
        return None

    percentiles: dict[str, float | None] = data.get("index_dividend_yield_percentiles", {})

    row = IndexDividendYield(
        index_code=index_code,
        trade_date=trade_date,
        dividend_yield=data.get("index_dividend_yield"),
        dividend_yield_percentile_1y=percentiles.get("1Y"),
        dividend_yield_percentile_3y=percentiles.get("3Y"),
        dividend_yield_percentile_5y=percentiles.get("5Y"),
        dividend_yield_percentile_10y=percentiles.get("10Y"),
        dividend_yield_average_5y=data.get("index_dividend_yield_average_5y"),
    )

    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 国债收益率落库
# ---------------------------------------------------------------------------

def save_bond_yields(
    db: Session,
    records: list[dict[str, Any]],
) -> int:
    """批量写入国债收益率历史数据。

    参数:
        db: SQLAlchemy 会话
        records: fetch_cn_10y_bond_yield 返回的列表

    返回: 新写入行数(已存在的跳过)。
    """
    inserted = 0
    for record in records:
        trade_date = date.fromisoformat(record["trade_date"])

        existing = db.query(CnBondYield).filter_by(trade_date=trade_date).first()
        if existing:
            continue

        db.add(CnBondYield(
            trade_date=trade_date,
            yield_2y=record.get("cn_2y_bond_yield"),
            yield_5y=record.get("cn_5y_bond_yield"),
            yield_10y=record.get("cn_10y_bond_yield"),
            yield_30y=record.get("cn_30y_bond_yield"),
            spread_10y_2y=record.get("cn_10y_2y_spread"),
        ))
        inserted += 1

    db.commit()
    return inserted
