# -*- coding: utf-8 -*-
"""股息率查询服务(get_latest / get_history)。

从 api/routes/valuation.py 抽取, 返回结构与旧路由完全一致。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import IndexDividendYield
from backend.services.queries.base import history_stmt, latest_stmt


def _to_dict(r: IndexDividendYield) -> dict[str, Any]:
    return {
        "index_code": r.index_code,
        "trade_date": r.trade_date.isoformat() if r.trade_date else None,
        "dividend_yield": r.dividend_yield,
        "percentile": {
            "1y": r.dividend_yield_percentile_1y,
            "3y": r.dividend_yield_percentile_3y,
            "5y": r.dividend_yield_percentile_5y,
            "10y": r.dividend_yield_percentile_10y,
        },
        "average_5y": r.dividend_yield_average_5y,
    }


def get_latest(db: Session, index_code: str | None = None) -> list[dict[str, Any]]:
    """每只指数最新一条(列表页用)。"""
    rows = db.scalars(latest_stmt(IndexDividendYield, index_code)).all()
    return [_to_dict(r) for r in rows]


def get_history(db: Session, index_code: str | None = None) -> list[dict[str, Any]]:
    """全量历史(详情页画走势用)。"""
    rows = db.scalars(history_stmt(IndexDividendYield, index_code)).all()
    return [_to_dict(r) for r in rows]
