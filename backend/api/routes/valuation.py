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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import (
    CnBondYield,
    IndexDividendYield,
    IndexValuationSnapshot,
)
from backend.services.equity_bond import compute_equity_bond

router = APIRouter()


# ---------------------------------------------------------------------------
# 估值快照
# ---------------------------------------------------------------------------

@router.get("/valuation/snapshot")
def list_valuation_snapshot(
    index_code: str | None = Query(None, description="按指数代码过滤,如 930955"),
    latest: bool = Query(
        False,
        description="true 时每只指数只返回最新一条(列表页用, 避免拉全量历史)",
    ),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回估值快照列表(可按 index_code 过滤 / 可只取每只最新一条)。

    每行含 PE/PB/PS 当前值及各 9 个周期分位(3M/6M/1Y/2Y/3Y/5Y/10Y/YTD/Bgn)。

    latest=false(默认): 返回全量历史, 按 index_code 升序、trade_date 降序排列。
    latest=true: 每只指数只返回 trade_date 最大的一条, 按 index_code 升序。
      全量历史约 13MB(8 指数 × 2.6 万条), 列表页只需要 8 行, 用该参数把
      响应压到几 KB——否则手机端首屏要等十几兆 JSON。
    """
    if latest:
        # 每只指数的最新交易日 → 与快照表自连接取该日的那一行
        latest_date = (
            select(
                IndexValuationSnapshot.index_code,
                func.max(IndexValuationSnapshot.trade_date).label("max_date"),
            )
            .group_by(IndexValuationSnapshot.index_code)
            .subquery()
        )
        stmt = (
            select(IndexValuationSnapshot)
            .join(
                latest_date,
                (IndexValuationSnapshot.index_code == latest_date.c.index_code)
                & (IndexValuationSnapshot.trade_date == latest_date.c.max_date),
            )
            .order_by(IndexValuationSnapshot.index_code)
        )
    else:
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
# 股债收益差 / 股债收益比
# ---------------------------------------------------------------------------

@router.get("/valuation/equity-bond")
def list_equity_bond(
    index_code: str | None = Query(None, description="按指数代码过滤,如 930955;传入时附带全历史序列"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """股债收益差(EP-10Y)/股债收益比(EP/10Y) 当前值与分位。

    口径见 backend/services/equity_bond.py(移植自旧 market-daily):
    spread = 100/PE - 10Y 国债收益率, 越高股票相对债券越便宜。
    由 PE 快照全历史 × 国债收益率全历史现算(数据量 ~3 万行, 无需落库)。

    不传 index_code: 每只指数返回统计值(列表页)。
    传入 index_code: 额外附带 series 全历史序列(详情页画走势)。
    """
    pe_rows = db.execute(
        select(
            IndexValuationSnapshot.index_code,
            IndexValuationSnapshot.index_name,
            IndexValuationSnapshot.trade_date,
            IndexValuationSnapshot.pe,
        ).where(IndexValuationSnapshot.pe.isnot(None))
    ).all()
    bond_rows = db.execute(
        select(CnBondYield.trade_date, CnBondYield.yield_10y).where(
            CnBondYield.yield_10y.isnot(None)
        )
    ).all()

    bond_map = {r.trade_date.isoformat(): r.yield_10y for r in bond_rows}

    pe_by_index: dict[str, dict[str, float | None]] = {}
    name_by_index: dict[str, str] = {}
    for r in pe_rows:
        pe_by_index.setdefault(r.index_code, {})[r.trade_date.isoformat()] = r.pe
        name_by_index[r.index_code] = r.index_name

    results: list[dict[str, Any]] = []
    for code in sorted(pe_by_index):
        if index_code and code != index_code:
            continue
        stats = compute_equity_bond(
            pe_by_index[code], bond_map, include_series=bool(index_code)
        )
        if stats is None:
            continue
        stats_out = {"index_code": code, "index_name": name_by_index.get(code, code), **stats}
        results.append(stats_out)
    return results


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
