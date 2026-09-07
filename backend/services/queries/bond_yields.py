# -*- coding: utf-8 -*-
"""国债收益率查询服务(单表, 无 index_code)。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.valuation import CnBondYield


def _to_dict(r: CnBondYield) -> dict[str, Any]:
    return {
        "trade_date": r.trade_date.isoformat() if r.trade_date else None,
        "yield_2y": r.yield_2y,
        "yield_5y": r.yield_5y,
        "yield_10y": r.yield_10y,
        "yield_30y": r.yield_30y,
        "spread_10y_2y": r.spread_10y_2y,
    }


def get_history(db: Session) -> list[dict[str, Any]]:
    """全量国债收益率, 按 trade_date 降序(最新在前)。"""
    stmt = select(CnBondYield).order_by(CnBondYield.trade_date.desc())
    rows = db.scalars(stmt).all()
    return [_to_dict(r) for r in rows]
