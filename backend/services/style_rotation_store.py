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


def get_index_data_summary(db: Session, index_code: str) -> dict[str, Any] | None:
    """返回某指数的落库概况: 条数、最早/最晚日期。空表返回 None。

    实现已泛化到 backend.services.data_integrity, 此处保留原签名向后兼容。
    """
    from backend.services.data_integrity import get_table_summary

    return get_table_summary(
        db, IndexDailyQuote, "trade_date", "index_code", index_code,
    )


def scan_date_gaps(
    db: Session,
    index_code: str,
    max_gap_days: int = 11,
) -> list[dict[str, Any]]:
    """扫描某指数相邻交易日期间的异常空洞。

    相邻落库日期间隔超过 max_gap_days 天视为可疑(正常周末 2-3 天,长假最多约 9 天)。
    返回 [{prev, next, gap_days}, ...] 升序。

    实现已泛化到 backend.services.data_integrity, 此处保留原签名向后兼容。
    """
    from backend.services.data_integrity import scan_date_gaps_generic

    return scan_date_gaps_generic(
        db, IndexDailyQuote, "trade_date", "index_code", index_code, max_gap_days,
    )
