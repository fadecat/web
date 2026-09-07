# -*- coding: utf-8 -*-
"""转债快照查询服务。

与估值/股息率不同, 这里 latest 语义是「某交易日全量快照」而非「每只债最新」,
get_history 是「某只债的历史」。故不套用 base 的 index_code 版 latest/history。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.valuation import CbDailySnapshot


def _to_dict(r: CbDailySnapshot) -> dict[str, Any]:
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


def get_latest(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    """某交易日全量快照(默认最新交易日), 按双低值升序。"""
    if not trade_date:
        latest = db.scalar(select(func.max(CbDailySnapshot.trade_date)))
        if not latest:
            return []
        trade_date = latest.isoformat()

    stmt = (
        select(CbDailySnapshot)
        .where(CbDailySnapshot.trade_date == trade_date)
        .order_by(CbDailySnapshot.dblow.asc())
    )
    rows = db.scalars(stmt).all()
    return [_to_dict(r) for r in rows]


def get_history(db: Session, bond_id: str) -> list[dict[str, Any]]:
    """某只转债的历史快照, 按 trade_date 升序。"""
    stmt = (
        select(CbDailySnapshot)
        .where(CbDailySnapshot.bond_id == bond_id)
        .order_by(CbDailySnapshot.trade_date.asc())
    )
    rows = db.scalars(stmt).all()
    return [_to_dict(r) for r in rows]
