# -*- coding: utf-8 -*-
"""风格轮动板块路由。

端点:
1. GET /api/style-rotation/quotes   — 返回指数日线原始 OHLCV(可按 index_code 过滤)
2. GET /api/style-rotation/analysis — 大小盘风格轮动主图数据(spread/ma/p90/p10)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import IndexDailyQuote, IndexValuationSnapshot
from backend.services.style_rotation_analysis import (
    InsufficientDataError,
    StyleRotationParams,
    build_style_rotation_response,
)
router = APIRouter()

# 指数代码 -> 简称(仅图表展示用) —— 定义移至 backend/utils.INDEX_DISPLAY_NAMES, 数据状态页共用
from backend.utils import INDEX_DISPLAY_NAMES  # noqa: E402


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


@router.get("/style-rotation/valuation")
def style_rotation_valuation(
    left_symbol: str = Query(default="399376", description="左侧指数代码"),
    right_symbol: str = Query(default="399373", description="右侧指数代码"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """读取对照组两侧的最新估值快照。

    该接口只读 IndexValuationSnapshot, 不触发抓取; 未纳入估值任务的指数返回
    ``available=false``。估值日期独立于轮动图日期, 前端必须显式展示日期。
    """
    if left_symbol == right_symbol:
        raise HTTPException(status_code=400, detail="left_symbol 与 right_symbol 必须不同")

    latest_date = (
        select(
            IndexValuationSnapshot.index_code,
            func.max(IndexValuationSnapshot.trade_date).label("max_date"),
        )
        .where(IndexValuationSnapshot.index_code.in_([left_symbol, right_symbol]))
        .group_by(IndexValuationSnapshot.index_code)
        .subquery()
    )
    stmt = (
        select(IndexValuationSnapshot)
        .join(
            latest_date,
            (IndexValuationSnapshot.index_code == latest_date.c.index_code)
            & (IndexValuationSnapshot.trade_date == latest_date.c.max_date),
        )
        .order_by(IndexValuationSnapshot.index_code)
    )
    rows = {row.index_code: row for row in db.scalars(stmt).all()}

    def to_item(code: str) -> dict[str, Any]:
        row = rows.get(code)
        if row is None:
            return {"index_code": code, "available": False}
        return {
            "index_code": row.index_code,
            "index_name": row.index_name,
            "available": True,
            "trade_date": row.trade_date.isoformat() if row.trade_date else None,
            "pe": row.pe,
            "pb": row.pb,
            "ps": row.ps,
            "pe_percentile_5y": row.pe_percentile_5y,
            "pb_percentile_5y": row.pb_percentile_5y,
        }

    return {
        "left": to_item(left_symbol),
        "right": to_item(right_symbol),
    }


@router.get("/style-rotation/analysis")
def style_rotation_analysis(
    left_symbol: str = Query(default="399376", description="左侧指数代码(默认 399376 小盘成长)"),
    right_symbol: str = Query(default="399373", description="右侧指数代码(默认 399373 大盘价值)"),
    start_date: str | None = Query(default=None, description="起始日期 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期 YYYY-MM-DD"),
    return_window: int = Query(default=250, ge=1, le=750, description="收益率计算窗口(交易日),源项目默认250"),
    ma_window: int = Query(default=20, ge=1, le=120, description="spread 的 MA 趋势线窗口"),
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
        ma_window=ma_window,
    )
    try:
        response = build_style_rotation_response(db, params)
        # 注入指数名称映射(图表图例用代码显示名称)
        response["meta"]["symbol_names"] = INDEX_DISPLAY_NAMES
        return response
    except InsufficientDataError as e:
        raise HTTPException(status_code=404, detail=f"数据不足: {e}")
