# -*- coding: utf-8 -*-
"""研究回放(次日 T 价位研究 V1)路由: 信号回放/价位触达, 全量返回不分页。"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.research_replay import DEFAULT_LAMBDAS, DEFAULT_WINDOWS, run_replay
from backend.services.queries import research as research_queries
from backend.utils import load_research_settings

router = APIRouter(prefix="/research")


class ReplayRequest(BaseModel):
    """创建回放请求; 数据源未验收(无 USABLE 快照)返回 409。"""

    symbol: str = Field(..., description="规范代码(如 600900.SH)")
    start_date: str = Field(..., description="评价日起(YYYY-MM-DD)")
    end_date: str = Field(..., description="评价日止(YYYY-MM-DD)")
    lambdas: list[float] | None = Field(None, description="λ 网格, 默认 {0,0.1,0.2,0.3}")
    windows: list[int] | None = Field(None, description="分位窗口网格, 默认 {60,120,180}")


_MAX_RANGE_DAYS = 366 * 3  # 单次区间上限约 3 年


@router.get("/securities")
def list_securities(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """研究标的名单与覆盖概况。"""
    return research_queries.list_securities(db)


@router.get("/data-health")
def list_data_health(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """逐标的 raw/hfq 数据健康度与日历覆盖。"""
    return research_queries.list_data_health(db)


@router.get("/replays")
def list_replays(
    symbol: str | None = Query(None, description="按标的过滤"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """回放 run 列表。"""
    return research_queries.list_replays(db, symbol)


@router.post("/replays", status_code=201)
def create_replays(request: ReplayRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """同步计算 λ×窗口网格回放并物化; 返回 run_id 列表。"""
    try:
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"日期格式非法: {exc}") from None
    if start > end:
        raise HTTPException(status_code=422, detail="start_date 不得晚于 end_date")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail="单次回放区间不得超过约 3 年")
    tolerance = float(load_research_settings().get("corporate_action_tolerance") or 0.002)
    lambdas = request.lambdas or list(DEFAULT_LAMBDAS)
    windows = request.windows or list(DEFAULT_WINDOWS)
    try:
        run_ids = run_replay(
            db,
            symbol=request.symbol,
            start_date=start,
            end_date=end,
            lambdas=lambdas,
            windows=windows,
            tolerance=tolerance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"run_ids": run_ids, "symbol": request.symbol, "count": len(run_ids)}


@router.get("/replays/{run_id}/summary")
def get_replay_summary(run_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """单 run 汇总: 各档分子/分母/比例、类别计数、训练/验证两段。"""
    summary = research_queries.get_summary(db, run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"未知回放 run: {run_id}")
    return summary


@router.get("/replays/{run_id}/days")
def get_replay_days(run_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """单 run 逐日明细全量(价位为未取整模型价, 非可下单报价)。"""
    days = research_queries.list_days(db, run_id)
    if days is None:
        raise HTTPException(status_code=404, detail=f"未知回放 run: {run_id}")
    return days


@router.get("/replay-comparison")
def replay_comparison(
    symbol: str = Query(..., description="规范代码"),
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """参数网格比较: train/validation 触达率并排(选参只看训练段)。"""
    try:
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"日期格式非法: {exc}") from None
    return research_queries.replay_comparison(db, symbol, start_date, end_date)
