# -*- coding: utf-8 -*-
"""风格轮动板块路由。

端点:
1. GET /api/style-rotation/quotes  — 返回指数日线原始 OHLCV(可按 index_code 过滤)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import IndexDailyQuote

router = APIRouter()


@router.get("/style-rotation/quotes")
def list_index_quotes(
    index_code: str | None = Query(None, description="按指数代码过滤,如 399376"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回指数日线 OHLCV 全量列表(可按 index_code 过滤)。

    按 index_code 升序、trade_date 升序排列(方便前端绘制时间序列)。
    """
    stmt = select(IndexDailyQuote).order_by(
        IndexDailyQuote.index_code,
        IndexDailyQuote.trade_date,
    )
    if index_code:
        stmt = stmt.where(IndexDailyQuote.index_code == index_code)

    rows = db.scalars(stmt).all()
    return [
        {
            "index_code": r.index_code,
            "trade_date": r.trade_date.isoformat() if r.trade_date else None,
            "open": r.open,
            "close": r.close,
            "high": r.high,
            "low": r.low,
            "volume": r.volume,
        }
        for r in rows
    ]
