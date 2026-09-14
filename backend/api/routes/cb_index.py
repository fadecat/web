# -*- coding: utf-8 -*-
"""可转债等权指数路由。

端点:
1. GET /api/cb-index/daily  — 返回可转债等权指数日频原始数据(全量)
2. GET /api/cb-index/spread — 转债-国债利差(avg_ytm - 10Y 国债)统计与全历史序列
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import CbIndexDaily, CnBondYield
from backend.services.cb_bond_spread import compute_cb_bond_spread

router = APIRouter()


@router.get("/cb-index/daily")
def list_cb_index_daily(
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回可转债等权指数日频全量数据。

    按 trade_date 降序排列(最新在前)。
    前端可按需计算收益率差值、绘制图表。
    """
    stmt = select(CbIndexDaily).order_by(CbIndexDaily.trade_date.desc())
    rows = db.scalars(stmt).all()
    return [
        {
            "trade_date": r.trade_date.isoformat() if r.trade_date else None,
            "index_value": r.index_value,
            "median_price": r.median_price,
            "avg_price": r.avg_price,
            "avg_ytm": r.avg_ytm,
            "median_convert_value": r.median_convert_value,
            "avg_dblow": r.avg_dblow,
            "avg_premium": r.avg_premium,
            "median_premium": r.median_premium,
            "turnover_rate": r.turnover_rate,
            "count": r.count,
            "temperature": r.temperature,
            "idx_price": r.idx_price,
            "idx_increase_rt": r.idx_increase_rt,
        }
        for r in rows
    ]


@router.get("/cb-index/spread")
def list_cb_bond_spread(
    db: Session = Depends(get_db),
) -> dict[str, Any] | None:
    """转债-国债利差(avg_ytm - 10Y 国债)当前值、分位与全历史序列。

    口径见 backend/services/cb_bond_spread.py(结构移植自 equity_bond):
    spread 越高代表转债债底相对国债越便宜; 国债取 10Y, 与估值页股债差同期限。
    由转债指数全历史 × 10Y 国债全历史按日期内连接现算(数据量小, 无需落库)。
    有效样本不足 20 天时返回 null(前端空态)。
    """
    ytm_rows = db.execute(
        select(CbIndexDaily.trade_date, CbIndexDaily.avg_ytm).where(
            CbIndexDaily.avg_ytm.isnot(None)
        )
    ).all()
    bond_rows = db.execute(
        select(CnBondYield.trade_date, CnBondYield.yield_10y).where(
            CnBondYield.yield_10y.isnot(None)
        )
    ).all()

    ytm_map = {r.trade_date.isoformat(): r.avg_ytm for r in ytm_rows}
    bond_map = {r.trade_date.isoformat(): r.yield_10y for r in bond_rows}
    return compute_cb_bond_spread(ytm_map, bond_map, include_series=True)
