# -*- coding: utf-8 -*-
"""可转债筛选打分路由。

端点:
1. GET  /cb-list/factors/catalog  — 因子目录
2. GET  /cb-list/factors          — 策略模板配置
3. POST /cb-list/factors          — 保存策略模板配置
4. POST /cb-list/screen           — 按模板筛选打分(基于最新交易日快照)
5. GET  /cb-list/screen/active    — 按当前 active 模板筛选打分
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.valuation import CbDailySnapshot
from backend.services.cb_factors import (
    FACTOR_CATALOG,
    get_active_template,
    read_config,
    write_config,
)
from backend.services.cb_screen import screen_bonds

router = APIRouter()


@router.get("/cb-list/factors/catalog")
def get_factor_catalog() -> list[dict[str, str]]:
    """返回可用因子字段目录。"""
    return FACTOR_CATALOG


@router.get("/cb-list/factors")
def get_factors() -> dict[str, Any]:
    """返回当前策略模板配置。"""
    return read_config()


@router.post("/cb-list/factors")
def save_factors(body: dict[str, Any]) -> dict[str, Any]:
    """保存策略模板配置到 data/factors.json。"""
    normalized = write_config(body)
    return {"ok": True, "data": normalized}


def _load_rows(db: Session) -> list[CbDailySnapshot]:
    """加载最新交易日的全量转债快照。"""
    latest = db.query(func.max(CbDailySnapshot.trade_date)).scalar()
    if not latest:
        return []
    return db.query(CbDailySnapshot).filter(
        CbDailySnapshot.trade_date == latest
    ).all()


@router.post("/cb-list/screen")
def screen(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """按传入模板配置筛选打分。

    模板结构对齐 v2_cb_rotation(见 cb_factors.py DEFAULT_CONFIG)。
    """
    rows = _load_rows(db)
    if not rows:
        return {"total_all": 0, "total_filtered": 0, "top_n": 0, "keep_n": 0, "rows": []}

    return screen_bonds(rows, body)


@router.get("/cb-list/screen/active")
def screen_active(db: Session = Depends(get_db)) -> dict[str, Any]:
    """按当前 active 模板筛选打分。"""
    tmpl = get_active_template()
    if tmpl is None:
        raise HTTPException(status_code=400, detail="未找到可用因子模板")

    rows = _load_rows(db)
    if not rows:
        return {"total_all": 0, "total_filtered": 0, "top_n": 0, "keep_n": 0, "rows": []}

    result = screen_bonds(rows, tmpl)
    result["template_id"] = tmpl.get("id")
    result["template_name"] = tmpl.get("name")
    return result
