# -*- coding: utf-8 -*-
"""风格轮动板块存储层。

保存指数日线原始 OHLCV,不做计算。
幂等: 同一 (index_code, trade_date) 重复写入跳过。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import IndexDailyQuote


def save_index_quotes(
    db: Session,
    index_code: str,
    klines: list[dict[str, Any]],
) -> int:
    """批量写入指数日线 OHLCV。

    参数:
        db: SQLAlchemy 会话
        index_code: 指数代码(如 "399376")
        klines: fetch_index_kline 返回的列表 [{date, open, close, high, low, volume}, ...]

    返回: 新写入行数(已存在的跳过)。
    """
    inserted = 0
    for row in klines:
        trade_date = date.fromisoformat(row["date"])

        existing = db.query(IndexDailyQuote).filter_by(
            index_code=index_code,
            trade_date=trade_date,
        ).first()
        if existing:
            continue

        db.add(IndexDailyQuote(
            index_code=index_code,
            trade_date=trade_date,
            open=row.get("open"),
            close=row.get("close"),
            high=row.get("high"),
            low=row.get("low"),
            volume=row.get("volume"),
        ))
        inserted += 1

    db.commit()
    return inserted
