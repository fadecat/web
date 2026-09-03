# -*- coding: utf-8 -*-
"""可转债等权指数路由。

端点:
1. GET /api/cb-index/daily  — 返回可转债等权指数日频原始数据(全量)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import CbIndexDaily

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
