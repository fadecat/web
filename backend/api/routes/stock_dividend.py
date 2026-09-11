# -*- coding: utf-8 -*-
"""股票高股息快照路由。

端点:
1. GET /api/stock-dividend/latest — 返回最新交易日全量高股息快照
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.queries import stock_dividend

router = APIRouter()


@router.get("/stock-dividend/latest")
def list_latest(
    trade_date: str | None = Query(None, description="指定交易日(YYYY-MM-DD),默认最新"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回某交易日全量高股息股票快照(默认最新交易日)。

    按股息率(dividend_rate)降序、null 沉底, 方便前端直接展示;
    全量返回无分页, 筛选/排序交给前端。
    """
    return stock_dividend.get_latest(db, trade_date)
