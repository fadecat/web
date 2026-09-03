# -*- coding: utf-8 -*-
"""可转债全量快照路由。

端点:
1. GET /api/cb-list/latest  — 返回最新交易日全量转债快照
2. GET /api/cb-list/history — 返回某只转债的历史快照
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import CbDailySnapshot

router = APIRouter()


@router.get("/cb-list/latest")
def list_latest(
    trade_date: str | None = Query(None, description="指定交易日(YYYY-MM-DD),默认最新"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回某交易日全量转债快照(默认最新交易日)。

    按 dblow(双低值)升序排列,方便前端直接展示。
    """
    if trade_date:
        target_date = trade_date
    else:
        # 取最新交易日
        latest = db.query(func.max(CbDailySnapshot.trade_date)).scalar()
        if not latest:
            return []
        target_date = latest.isoformat()

    stmt = select(CbDailySnapshot).where(
        CbDailySnapshot.trade_date == target_date
    ).order_by(CbDailySnapshot.dblow.asc())

    rows = db.scalars(stmt).all()
    return [_snapshot_to_dict(r) for r in rows]


@router.get("/cb-list/history")
def list_history(
    bond_id: str = Query(..., description="转债代码,如 113648"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回某只转债的历史快照(按日期升序)。"""
    stmt = select(CbDailySnapshot).where(
        CbDailySnapshot.bond_id == bond_id
    ).order_by(CbDailySnapshot.trade_date.asc())

    rows = db.scalars(stmt).all()
    return [_snapshot_to_dict(r) for r in rows]


def _snapshot_to_dict(r: CbDailySnapshot) -> dict[str, Any]:
    return {
        "trade_date": r.trade_date.isoformat() if r.trade_date else None,
        "bond_id": r.bond_id,
        "bond_nm": r.bond_nm,
        "stock_id": r.stock_id,
        "stock_nm": r.stock_nm,
        "price": r.price,
        "sprice": r.sprice,
        "increase_rt": r.increase_rt,
        "sincrease_rt": r.sincrease_rt,
        "convert_price": r.convert_price,
        "convert_value": r.convert_value,
        "premium_rt": r.premium_rt,
        "dblow": r.dblow,
        "curr_iss_amt": r.curr_iss_amt,
        "orig_iss_amt": r.orig_iss_amt,
        "year_left": r.year_left,
        "maturity_dt": r.maturity_dt,
        "list_dt": r.list_dt,
        "rating_cd": r.rating_cd,
        "ytm_rt": r.ytm_rt,
        "pb": r.pb,
        "turnover_rt": r.turnover_rt,
        "volume": r.volume,
        "force_redeem_price": r.force_redeem_price,
        "convert_amt_ratio": r.convert_amt_ratio,
        "market_cd": r.market_cd,
        "sw_cd": r.sw_cd,
    }
