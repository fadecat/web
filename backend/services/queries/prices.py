# -*- coding: utf-8 -*-
"""指数日线收盘价查询服务(close only)。

易方达源只有 close(无 OHLCV), 轮动计算只需 close, 不做伪 OHLCV。
返回 (trade_date, close) 升序行, 供 analytics 层转 DataFrame 后计算。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.valuation import IndexDailyQuote
from backend.services.catalog_entities import to_storage


def get_history(
    db: Session,
    index_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list:
    """返回 (trade_date, close) 升序行列表。

    支持日期范围过滤(轮动预热窗口会向前多取数据再裁剪展示)。
    """
    stmt = (
        select(IndexDailyQuote.trade_date, IndexDailyQuote.close)
        .where(IndexDailyQuote.index_code == to_storage(index_code))
    )
    if start_date:
        stmt = stmt.where(IndexDailyQuote.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(IndexDailyQuote.trade_date <= end_date)
    stmt = stmt.order_by(IndexDailyQuote.trade_date.asc())
    return db.execute(stmt).all()
