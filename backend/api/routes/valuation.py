# -*- coding: utf-8 -*-
"""市场估值板块路由。

约定(见 docs/web-refactor.md):
- 全量返回(不做服务端分页),由前端做筛选与 ECharts 绘图。
- 后端只出 JSON,不介入绘图。
- 路由前缀 /api,由 main.py 挂载。

三个端点:
1. GET /api/valuation/snapshot      — 估值快照(PE/PB/PS + 9周期分位)
2. GET /api/valuation/dividend-yield — 股息率(含分位 + 5Y均值)
3. GET /api/valuation/bond-yield     — 国债收益率(2Y/5Y/10Y/30Y + 期限利差)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import (
    CnBondYield,
    IndexDividendYield,
    IndexValuationSnapshot,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# 估值快照
# ---------------------------------------------------------------------------

@router.get("/valuation/snapshot")
def list_valuation_snapshot(
    index_code: str | None = Query(None, description="按指数代码过滤,如 930955"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回估值快照全量列表(可按 index_code 过滤)。

    每行含 PE/PB/PS 当前值及各 9 个周期分位(3M/6M/1Y/2Y/3Y/5Y/10Y/YTD/Bgn)。
    按 index_code 升序、trade_date 降序排列(最新在前)。
    """
    stmt = select(IndexValuationSnapshot).order_by(
        IndexValuationSnapshot.index_code,
        IndexValuationSnapshot.trade_date.desc(),
    )
    if index_code:
        stmt = stmt.where(IndexValuationSnapshot.index_code == index_code)

    rows = db.scalars(stmt).all()
    return [_snapshot_to_dict(r) for r in rows]


def _snapshot_to_dict(r: IndexValuationSnapshot) -> dict[str, Any]:
    """ORM 行 -> API 响应 dict,全量输出所有字段。"""
    return {
        "index_code": r.index_code,
        "index_name": r.index_name,
        "trade_date": r.trade_date.isoformat() if r.trade_date else None,
        "pe": r.pe,
        "pb": r.pb,
        "ps": r.ps,
        "pe_percentile": {
            "3m": r.pe_percentile_3m,
            "6m": r.pe_percentile_6m,
            "1y": r.pe_percentile_1y,
            "2y": r.pe_percentile_2y,
            "3y": r.pe_percentile_3y,
            "5y": r.pe_percentile_5y,
            "10y": r.pe_percentile_10y,
            "ytd": r.pe_percentile_ytd,
            "bgn": r.pe_percentile_bgn,
        },
        "pb_percentile": {
            "3m": r.pb_percentile_3m,
            "6m": r.pb_percentile_6m,
            "1y": r.pb_percentile_1y,
            "2y": r.pb_percentile_2y,
            "3y": r.pb_percentile_3y,
            "5y": r.pb_percentile_5y,
            "10y": r.pb_percentile_10y,
            "ytd": r.pb_percentile_ytd,
            "bgn": r.pb_percentile_bgn,
        },
        "ps_percentile": {
            "3m": r.ps_percentile_3m,
            "6m": r.ps_percentile_6m,
            "1y": r.ps_percentile_1y,
            "2y": r.ps_percentile_2y,
            "3y": r.ps_percentile_3y,
            "5y": r.ps_percentile_5y,
            "10y": r.ps_percentile_10y,
            "ytd": r.ps_percentile_ytd,
            "bgn": r.ps_percentile_bgn,
        },
    }


# ---------------------------------------------------------------------------
# 股息率
# ---------------------------------------------------------------------------

@router.get("/valuation/dividend-yield")
def list_dividend_yield(
    index_code: str | None = Query(None, description="按指数代码过滤,如 930955"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回股息率全量列表(可按 index_code 过滤)。

    每行含最新股息率 + 1Y/3Y/5Y/10Y 分位 + 5Y 均值。
    """
    stmt = select(IndexDividendYield).order_by(
        IndexDividendYield.index_code,
        IndexDividendYield.trade_date.desc(),
    )
    if index_code:
        stmt = stmt.where(IndexDividendYield.index_code == index_code)

    rows = db.scalars(stmt).all()
    return [
        {
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
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 国债收益率
# ---------------------------------------------------------------------------

@router.get("/valuation/bond-yield")
def list_bond_yield(
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回国债收益率全量列表。

    每行含 2Y/5Y/10Y/30Y 收益率及 10Y-2Y 期限利差。
    按 trade_date 降序排列(最新在前)。
    """
    stmt = select(CnBondYield).order_by(CnBondYield.trade_date.desc())
    rows = db.scalars(stmt).all()
    return [
        {
            "trade_date": r.trade_date.isoformat() if r.trade_date else None,
            "yield_2y": r.yield_2y,
            "yield_5y": r.yield_5y,
            "yield_10y": r.yield_10y,
            "yield_30y": r.yield_30y,
            "spread_10y_2y": r.spread_10y_2y,
        }
        for r in rows
    ]
