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
    IndexValuationSnapshot,
)
from backend.services.equity_bond import compute_equity_bond
from backend.services.catalog_entities import to_storage
from backend.services.queries import bond_yields, dividends, valuations

router = APIRouter()


def _normalize_index_code(index_code: str | None) -> str | None:
    """查询入口代码归一化: canonical_id → 历史存储 key(缺省恒等)。

    传真实指数代码(如 931052)与传历史存储 key(如 512040)均可命中;
    未收录代码恒等返回, 不改变旧接口行为。
    """
    return to_storage(index_code) if index_code else None


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
        return valuations.get_latest(db, index_code)
    return valuations.get_history(db, index_code)


# ---------------------------------------------------------------------------
# 股息率
# ---------------------------------------------------------------------------

@router.get("/valuation/dividend-yield")
def list_dividend_yield(
    index_code: str | None = Query(None, description="按指数代码过滤,如 930955"),
    latest: bool = Query(
        False,
        description="true 时每只指数只返回最新一条(列表页用, 避免拉全量历史 3.7MB)",
    ),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回股息率列表(可按 index_code 过滤 / 可只取每只最新一条)。

    每行含股息率 + 1Y/3Y/5Y/10Y 分位 + 5Y 均值。

    latest=false(默认): 返回全量历史(画折线用, 8 指数约 2.6 万行/3.7MB)。
    latest=true: 每只指数只返回 trade_date 最大的一条(列表页只展示当前值+分位,
      8 行约 15KB; 与 snapshot 的 latest 同款方案)。
    """
    if latest:
        return dividends.get_latest(db, index_code)
    return dividends.get_history(db, index_code)


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
    index_code = _normalize_index_code(index_code)
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
    return bond_yields.get_history(db)
