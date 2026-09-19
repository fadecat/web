# -*- coding: utf-8 -*-
"""组合实验室标的路由(P0-2): 解析候选 / 注册 / 列表 / 单标的补抓。

职责边界(刻意 thin): 只做参数校验、错误码映射与「注册后异步触发抓取」的编排,
取数与落库全在 services/portfolio_assets.py。

错误码契约:
- code 写法非法 → 422(判定见 portfolio_assets.describe_code_problem)
- 写法合法但没有候选 / 未知标的 id → 404
- security_type=FUND → 501(P1 实现)
- 抓取失败不是 HTTP 错误: 写 last_sync_* 后原样返回, UI 据此给「重试」
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.models.database import SessionLocal, get_db
from backend.services import portfolio_assets
from backend.services.market_data import ResearchSourceError

router = APIRouter(prefix="/portfolio")

logger = logging.getLogger(__name__)

# 后台任务不能复用请求级 Session(响应返回时已关闭), 须自己开会话;
# 与 sync_one「每标的一个独立 Session」的范式一致。测试通过替换本属性隔离。
_session_factory = SessionLocal


class AssetCreate(BaseModel):
    """注册标的请求体。"""

    symbol: str = Field(..., description="标的代码(600900 / sh600900 / 600900.SH / SH600900)")
    security_type: str = Field("STOCK", description="STOCK | ETF | FUND")
    name: str | None = Field(None, description="标的名称; 缺省由代码兜底")


def _run_background_sync(security_id: int) -> None:
    """注册后的首次抓取: 后台执行, 失败状态写 last_sync_*(无响应通道)。"""
    try:
        portfolio_assets.sync_one(security_id, db_factory=_session_factory)
    except Exception:  # noqa: BLE001 后台任务异常不能打断已返回的响应
        logger.exception("组合实验室后台抓取失败: security_id=%s", security_id)


@router.get("/assets/probe")
def probe_asset(
    code: str = Query(..., description="标的代码(四种写法)"),
    type_hint: str | None = Query(None, alias="type", description="stock | etf | fund; 缺省自动"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """解析代码返回候选数组(股票/ETF 走腾讯, 场外基金候选不带真实数据), **不落库**。"""
    problem = portfolio_assets.describe_code_problem(code)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    candidates = portfolio_assets.probe(code, type_hint, db=db)
    if not candidates:
        raise HTTPException(status_code=404, detail=f"未找到该代码: {code}")
    return candidates


@router.post("/assets", status_code=201)
def create_asset(
    request: AssetCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """注册标的 + 后台异步触发首次抓取; 返回值为标的行(last_sync_status=running)。

    重复注册幂等: 返回已存在的行且不再触发抓取(created=False)。
    """
    try:
        row = portfolio_assets.register(
            request.symbol, request.security_type, request.name or "", db=db,
        )
    except ResearchSourceError as exc:  # 代码与类型矛盾等
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError as exc:
        message = str(exc)
        if "场外基金链路" in message:
            raise HTTPException(status_code=501, detail=message) from None
        raise HTTPException(status_code=422, detail=message) from None
    if row["created"]:
        background_tasks.add_task(_run_background_sync, row["id"])
        return {**row, "last_sync_status": portfolio_assets.STATUS_RUNNING}
    return row


@router.get("/assets")
def list_assets(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """已注册标的列表(按注册顺序), 含 row_count / first_date / last_date / 同步状态。"""
    return portfolio_assets.list_assets(db)


@router.post("/assets/{asset_id}/refresh")
def refresh_asset(asset_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """单标的补抓(同步执行): 抓 RAW/HFQ 配对并回写同步状态, 失败也返回 200 的结果体。"""
    if portfolio_assets.get_asset(db, asset_id) is None:
        raise HTTPException(status_code=404, detail=f"未知标的 id: {asset_id}")
    return portfolio_assets.sync_one(asset_id, db_factory=_session_factory)
