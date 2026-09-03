# -*- coding: utf-8 -*-
"""可转债强赎列表快照存储层。

每天每只强赎相关转债一行, 幂等追加。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import CbRedeemDaily
from backend.utils import parse_float


def _parse_int(value: Any) -> int | None:
    """宽松解析整数, None/空串/'-' 返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def save_cb_redeem(
    db: Session,
    records: list[dict[str, Any]],
    trade_date: date,
) -> int:
    """批量写入强赎列表快照。

    参数:
        db: SQLAlchemy 会话
        records: fetch_redeem_list 返回的 cell 列表
        trade_date: 交易日

    返回: 新写入行数(已存在的跳过)。
    """
    inserted = 0
    for cell in records:
        bond_id = str(cell.get("bond_id", "")).strip()
        if not bond_id:
            continue

        existing = db.query(CbRedeemDaily).filter_by(
            bond_id=bond_id,
            trade_date=trade_date,
        ).first()
        if existing:
            continue

        db.add(CbRedeemDaily(
            trade_date=trade_date,
            bond_id=bond_id,
            bond_nm=str(cell.get("bond_nm") or "").strip() or None,
            stock_id=str(cell.get("stock_id") or "").strip() or None,
            stock_nm=str(cell.get("stock_nm") or "").strip() or None,
            redeem_icon=str(cell.get("redeem_icon") or "").strip() or None,
            redeem_flag=str(cell.get("redeem_flag") or "").strip() or None,
            redeem_remain_days=_parse_int(cell.get("redeem_remain_days")),
            redeem_real_days=_parse_int(cell.get("redeem_real_days")),
            redeem_count_days=_parse_int(cell.get("redeem_count_days")),
            redeem_total_days=_parse_int(cell.get("redeem_total_days")),
            redeem_price=parse_float(cell.get("redeem_price")),
            redeem_price_ratio=str(cell.get("redeem_price_ratio") or "").strip() or None,
            force_redeem_price=parse_float(cell.get("force_redeem_price")),
            redeem_dt=str(cell.get("redeem_dt") or "").strip() or None,
            recount_dt=str(cell.get("recount_dt") or "").strip() or None,
            delist_dt=str(cell.get("delist_dt") or "").strip() or None,
            force_redeem=str(cell.get("force_redeem") or "").strip() or None,
            raw_json=json.dumps(cell, ensure_ascii=False),
        ))
        inserted += 1

    db.commit()
    return inserted
