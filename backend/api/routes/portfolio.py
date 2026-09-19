# -*- coding: utf-8 -*-
"""组合实验室标的路由(P0-2 + P1): 解析候选 / 注册 / 列表 / 单标的补抓。

职责边界(刻意 thin): 只做参数校验、错误码映射与「注册后异步触发抓取」的编排,
取数与落库全在 services/portfolio_assets.py(股票/ETF 走腾讯, 场外基金走蛋卷净值)。

错误码契约:
- code 写法非法 → 422(判定见 portfolio_assets.describe_code_problem)
- 写法合法但没有候选 / 未知标的 id → 404
- 抓取失败不是 HTTP 错误: 写 last_sync_* 后原样返回, UI 据此给「重试」
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import SessionLocal, get_db
from backend.models.portfolio import PortfolioAsset
from backend.services import portfolio_assets, portfolio_store
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
        raise HTTPException(status_code=422, detail=str(exc)) from None
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


# ---------------------------------------------------------------------------
# 组合(P2): L1 列表 / L2 详情 / 新建(含复制) / 改名与成员 / 软删
#
# ⚠ 再平衡 / 基准 / 区间**不在这些读写字段里** —— 它们属于 `backtest_run`(P3)。
#    `default_rebalance` 只是新建 Run 时的预填值, 不参与任何计算。
# ---------------------------------------------------------------------------


class PortfolioCreate(BaseModel):
    """新建组合; 传 `from_id` 即"复制现有组合"(含成员与权重)。"""

    name: str | None = Field(None, description="缺省为 我的组合N")
    note: str | None = None
    from_id: int | None = Field(None, description="复制来源组合 id")
    default_rebalance: str | None = Field(None, description="none | quarterly | yearly(仅预填)")


class PortfolioAssetItem(BaseModel):
    """成员项(target_weight 为**百分比**, 如 25.0; 允许 None 表示尚未设置)。"""

    symbol: str
    target_weight: float | None = None


class PortfolioPatch(BaseModel):
    """部分更新; 传了 `assets` 就对成员做**全量替换**(增/删/改权重都走这一条)。"""

    name: str | None = None
    note: str | None = None
    default_rebalance: str | None = None
    assets: list[PortfolioAssetItem] | None = None


def _portfolio_error(exc: portfolio_store.PortfolioError) -> HTTPException:
    message = str(exc)
    if "未知组合" in message or "未知标的" in message:
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=422, detail=message)


@router.get("/portfolios")
def list_portfolios(
    include_archived: bool = Query(False, description="是否含已归档"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """L1 组合列表(两列卡片数据源): 成立时间 + 收益时间 + 三格收益。"""
    return portfolio_store.list_portfolios(db, include_archived=include_archived)


@router.post("/portfolios", status_code=201)
def create_portfolio(request: PortfolioCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    """新建组合; 带 `from_id` 时复制来源组合的成员与权重。"""
    try:
        if request.from_id is not None:
            return portfolio_store.copy_portfolio(db, request.from_id, request.name)
        return portfolio_store.create_portfolio(
            db, request.name, request.note, default_rebalance=request.default_rebalance,
        )
    except portfolio_store.PortfolioError as exc:
        raise _portfolio_error(exc) from None


@router.get("/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """L2 详情: 组合身份 + 成员(含「添加后的收益」) + 权重状态 + 数据就绪(含共同起点 T0)。"""
    detail = portfolio_store.portfolio_detail(db, portfolio_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"未知组合: {portfolio_id}")
    return detail


@router.patch("/portfolios/{portfolio_id}")
def patch_portfolio(
    portfolio_id: int, request: PortfolioPatch, db: Session = Depends(get_db),
) -> dict[str, Any]:
    """改名 / 备注 / 设预填再平衡 / 全量替换成员与权重。"""
    try:
        if request.name is not None:
            portfolio_store.rename_portfolio(db, portfolio_id, request.name)
        if request.note is not None:
            portfolio_store.set_note(db, portfolio_id, request.note)
        if request.default_rebalance is not None:
            portfolio_store.update_rebalance_default(db, portfolio_id, request.default_rebalance)
        if request.assets is not None:
            _replace_assets(db, portfolio_id, request.assets)
    except portfolio_store.PortfolioError as exc:
        raise _portfolio_error(exc) from None
    detail = portfolio_store.portfolio_detail(db, portfolio_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"未知组合: {portfolio_id}")
    return detail


@router.delete("/portfolios/{portfolio_id}")
def delete_portfolio(portfolio_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """软删(归档, 可恢复): **不**级联删成员与 Run。"""
    try:
        return portfolio_store.archive_portfolio(db, portfolio_id)
    except portfolio_store.PortfolioError as exc:
        raise _portfolio_error(exc) from None


def _replace_assets(
    db: Session, portfolio_id: int, items: list[PortfolioAssetItem],
) -> None:
    """按传入列表全量替换成员: 先删后加(保持 sort_order = 列表顺序), 再统一设权重。

    ⚠ 只操作 `portfolio_asset` 成员行, **绝不动 `research_security`**(标的注册表是全局的,
    停用它会影响 /research 模块与 17:30 的定时同步)。
    """
    seen: set[str] = set()
    for item in items:
        canonical = str(item.symbol).strip().upper()
        if canonical in seen:
            raise portfolio_store.PortfolioError(f"同一组合内不得重复标的: {canonical}")
        seen.add(canonical)

    existing = {
        a["symbol"]: a for a in portfolio_store.list_assets(db, portfolio_id)["assets"]
    }
    for symbol in existing:
        if symbol not in seen:
            portfolio_store.remove_asset(db, portfolio_id, symbol)
    for item in items:
        canonical = str(item.symbol).strip().upper()
        if canonical not in existing:
            portfolio_store.add_asset(db, portfolio_id, canonical)
    portfolio_store.set_weights(
        db, portfolio_id, {i.symbol: i.target_weight for i in items},
    )
    # 按传入顺序重排(前端列表顺序即用户看到的顺序)
    for index, item in enumerate(items):
        member = db.scalar(
            select(PortfolioAsset).where(
                PortfolioAsset.portfolio_id == portfolio_id,
                PortfolioAsset.symbol == str(item.symbol).strip().upper(),
            ),
        )
        if member is not None:
            member.sort_order = index
    db.commit()
