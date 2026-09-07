# -*- coding: utf-8 -*-
"""可转债全量快照路由。

端点:
1. GET /api/cb-list/latest  — 返回最新交易日全量转债快照
2. GET /api/cb-list/history — 返回某只转债的历史快照
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.queries import snapshots

router = APIRouter()


@router.get("/cb-list/latest")
def list_latest(
    trade_date: str | None = Query(None, description="指定交易日(YYYY-MM-DD),默认最新"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回某交易日全量转债快照(默认最新交易日)。

    按 dblow(双低值)升序排列,方便前端直接展示。
    """
    return snapshots.get_latest(db, trade_date)


@router.get("/cb-list/history")
def list_history(
    bond_id: str = Query(..., description="转债代码,如 113648"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回某只转债的历史快照(按日期升序)。"""
    return snapshots.get_history(db, bond_id)
