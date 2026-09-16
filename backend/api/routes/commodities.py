"""商品监控只读 API。"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.commodity_queries import CommodityQueryService

router = APIRouter()

WindowCode = Literal["d21", "d63", "y1", "y3", "y5", "y10"]
SignalCode = Literal["high", "low", "neutral", "stale", "failed"]
SortCode = Literal["signal", "price", "date", "d21", "d63", "y1", "y3", "y5", "y10"]
SortOrder = Literal["asc", "desc"]
RangeCode = Literal["6m", "1y", "3y", "5y", "10y", "all"]


@router.get("/commodities/overview")
def commodity_overview(db: Session = Depends(get_db)) -> dict:
    return CommodityQueryService(db).overview()


@router.get("/commodities")
def list_commodities(
    keyword: str | None = Query(None),
    category: str | None = Query(None),
    signal: SignalCode | None = Query(None),
    window: WindowCode | None = Query(None),
    sort_by: SortCode = Query("signal"),
    sort_order: SortOrder = Query("desc"),
    db: Session = Depends(get_db),
) -> list[dict]:
    return CommodityQueryService(db).list_instruments(
        keyword=keyword,
        category=category,
        signal=signal,
        window=window,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/commodities/{code}")
def commodity_detail(code: str, db: Session = Depends(get_db)) -> dict:
    result = CommodityQueryService(db).get_detail(code)
    if result is None:
        raise HTTPException(status_code=404, detail="商品不存在或已禁用")
    return result


@router.get("/commodities/{code}/history")
def commodity_history(
    code: str,
    range_code: Annotated[RangeCode, Query(alias="range")] = "1y",
    db: Session = Depends(get_db),
) -> dict:
    result = CommodityQueryService(db).history(code, range_code)
    if result is None:
        raise HTTPException(status_code=404, detail="商品不存在或已禁用")
    return result
