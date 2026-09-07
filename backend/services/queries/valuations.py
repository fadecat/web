# -*- coding: utf-8 -*-
"""估值快照查询服务(get_latest / get_history)。

从 api/routes/valuation.py 抽取, 返回结构与旧路由完全一致。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import IndexValuationSnapshot
from backend.services.queries.base import history_stmt, latest_stmt


def _to_dict(r: IndexValuationSnapshot) -> dict[str, Any]:
    """ORM 行 -> 响应 dict, 全量输出所有字段(PE/PB/PS + 各 9 周期分位)。"""
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


def get_latest(db: Session, index_code: str | None = None) -> list[dict[str, Any]]:
    """每只指数最新一条(列表页用)。"""
    rows = db.scalars(latest_stmt(IndexValuationSnapshot, index_code)).all()
    return [_to_dict(r) for r in rows]


def get_history(db: Session, index_code: str | None = None) -> list[dict[str, Any]]:
    """全量历史(详情页画走势用)。"""
    rows = db.scalars(history_stmt(IndexValuationSnapshot, index_code)).all()
    return [_to_dict(r) for r in rows]
