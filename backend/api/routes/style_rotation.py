# -*- coding: utf-8 -*-
"""风格轮动板块路由。

端点:
1. GET /api/style-rotation/quotes   — 返回指数日线原始 OHLCV(可按 index_code 过滤)
2. GET /api/style-rotation/analysis — 大小盘风格轮动主图数据(spread/ma/p90/p10)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import IndexDailyQuote
from backend.services.style_rotation_analysis import (
    InsufficientDataError,
    StyleRotationParams,
    build_style_rotation_response,
)

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


@router.get("/style-rotation/analysis")
def style_rotation_analysis(
    left_symbol: str = Query(default="399376", description="左侧指数代码(默认 399376 小盘成长)"),
    right_symbol: str = Query(default="399373", description="右侧指数代码(默认 399373 大盘价值)"),
    start_date: str | None = Query(default=None, description="起始日期 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期 YYYY-MM-DD"),
    return_window: int = Query(default=20, ge=1, le=250, description="收益率窗口(交易日)"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """大小盘风格轮动主图数据。

    返回结构: { meta, series: {dates, spread, ma, p90_dynamic, p10_dynamic}, summary }。
    """
    if left_symbol == right_symbol:
        raise HTTPException(status_code=400, detail="left_symbol 与 right_symbol 必须不同")

    params = StyleRotationParams(
        left_symbol=left_symbol,
        right_symbol=right_symbol,
        start_date=start_date,
        end_date=end_date,
        return_window=return_window,
        max_window=return_window,
    )
    try:
        return build_style_rotation_response(db, params)
    except InsufficientDataError as e:
        raise HTTPException(status_code=404, detail=f"数据不足: {e}")
