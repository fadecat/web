# -*- coding: utf-8 -*-
"""股票高股息快照路由。

端点:
1. GET  /api/stock-dividend/latest   — 返回最新交易日全量高股息快照
2. GET  /api/stock-dividend/presets  — 读取筛选预设(缺文件回退内置默认)
3. POST /api/stock-dividend/presets  — 全量保存筛选预设
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.schemas.stock_dividend import DividendPresetsConfig
from backend.models.database import get_db
from backend.services import dividend_presets
from backend.services.queries import stock_dividend

router = APIRouter()


@router.get("/stock-dividend/latest")
def list_latest(
    trade_date: str | None = Query(None, description="指定交易日(YYYY-MM-DD),默认最新"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """返回某交易日全量高股息股票快照(默认最新交易日)。

    按股息率(dividend_rate)降序、null 沉底, 方便前端直接展示;
    全量返回无分页, 筛选/排序交给前端。
    """
    return stock_dividend.get_latest(db, trade_date)


@router.get("/stock-dividend/presets")
def get_presets() -> dict[str, Any]:
    """返回筛选预设配置; 从未保存过时返回内置默认(邮件漏斗口径)。"""
    return dividend_presets.load_presets()


@router.post("/stock-dividend/presets")
def save_presets(body: DividendPresetsConfig) -> dict[str, Any]:
    """全量保存筛选预设(形状由 pydantic extra=forbid 校验, 一致性在此校验)。"""
    ids = [p.id for p in body.presets]
    names = [p.name for p in body.presets]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="预设 id 重复")
    if len(set(names)) != len(names):
        raise HTTPException(status_code=422, detail="预设名称重复")
    if body.active_id not in ids:
        raise HTTPException(status_code=422, detail="active_id 必须指向已有预设")
    return dividend_presets.save_presets(body.model_dump())
