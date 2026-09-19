# -*- coding: utf-8 -*-
"""组合实验室 · 组合定义模型(P2)。

归属原则(docs/portfolio-lab-multi-portfolio.md §一, 选错要重构):
- `portfolio` 只描述**身份**与**成员**: 持有什么、各占多少。**不存**再平衡/基准/区间——
  那些属于 `backtest_run`(P3), 否则"看三种再平衡"要建 9 个组合。
  `default_rebalance` 仅作新建 Run 时的预填值, 不参与任何计算。
- `portfolio_asset` 是**成员表**: `target_weight`(初始比例) + `added_at`(供「添加后的收益」
  这条纯展示列) + `sort_order`; 唯一键 `(portfolio_id, symbol)` → **同一组合内禁止重复标的**
  (跨组合允许重复)。
- `cached_*` 三格(日收益/近一月/今年以来)是**列表页固定参数(不平衡 + 三区间)的结果**,
  天然可缓存; `cached_asof_date` 即列表页卡片上显示的「收益时间」。由每日定时任务末尾重算,
  ⚠ 列表页与详情页数字必须一致 → 两者共用同一条「区间解析 + 份额法账本」实现(P3)。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.database import Base


def _utcnow() -> datetime:
    """与既有库约定一致的 naive UTC 时间戳。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Portfolio(Base):
    """组合身份(L1 列表页的每一张卡片)。"""

    __tablename__ = "portfolio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # active | archived —— 删除走软删(归档可恢复, 不级联删标的与 Run)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # 仅作新建 backtest_run 时的预填, 不参与计算(真正归属见模块 docstring)
    default_rebalance: Mapped[str | None] = mapped_column(String(16), nullable=True)  # none | quarterly | yearly
    # L1 三格缓存: 固定「不平衡 + 日/近一月/今年以来」的结果(百分比数值, 如 1.08 表示 1.08%)
    cached_day_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_month_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_ytd_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 缓存对应的数据截止日 = 列表页卡片上的「收益时间」
    cached_asof_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_portfolio_status", "status"),
    )


class PortfolioAsset(Base):
    """组合成员: 组合内持有什么、初始比例多少、何时添加。"""

    __tablename__ = "portfolio_asset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolio.id"), nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)  # 规范代码(600900.SH / 100018.OF)
    # 初始比例(百分比, 如 25.0); 尚未设置时为 None —— 列表底部提示"权重合计 100% 才能回测"
    target_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 添加日: 供「添加后的收益」这条纯展示列用(对回测零影响)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_asset_key"),
        Index("ix_portfolio_asset_portfolio", "portfolio_id", "sort_order"),
    )
