# -*- coding: utf-8 -*-
"""可转债等权指数存储层。

原始字段全量保存,幂等追加。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import CbIndexDaily
from backend.utils import parse_float


def save_cb_index_records(
    db: Session,
    records: list[dict[str, Any]],
) -> int:
    """批量写入可转债等权指数日频数据。

    参数:
        db: SQLAlchemy 会话
        records: fetch_cb_index_history 返回的列表

    返回: 新写入行数(已存在的跳过)。
    """
    inserted = 0
    for record in records:
        trade_date_str = record.get("date", "")
        if not trade_date_str:
            continue
        trade_date = date.fromisoformat(trade_date_str)

        existing = db.query(CbIndexDaily).filter_by(trade_date=trade_date).first()
        if existing:
            continue

        db.add(CbIndexDaily(
            trade_date=trade_date,
            index_value=parse_float(record.get("index_value")),
            median_price=parse_float(record.get("median_price")),
            avg_price=parse_float(record.get("avg_price")),
            avg_ytm=parse_float(record.get("avg_ytm")),
            median_convert_value=parse_float(record.get("median_convert_value")),
            avg_dblow=parse_float(record.get("avg_dblow")),
            avg_premium=parse_float(record.get("avg_premium")),
            median_premium=parse_float(record.get("median_premium")),
            turnover_rate=parse_float(record.get("turnover_rate")),
            count=parse_float(record.get("count")),
            temperature=parse_float(record.get("temperature")),
            idx_price=parse_float(record.get("idx_price")),
            idx_increase_rt=parse_float(record.get("idx_increase_rt")),
        ))
        inserted += 1

    db.commit()
    return inserted
