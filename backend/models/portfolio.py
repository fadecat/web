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
    Text,
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


class BacktestRun(Base):
    """一次回测 = 一个**不可变 Run**(组合快照 + 参数 + 结果), P3。

    三条设计要点:

    1. **组合快照**: `members_json` 存"跑那一次时的成员与权重"。组合后续被编辑/覆盖后,
       本 Run 仍能独立复现 —— 这是多组合下"旧结果不被改配置毁掉"的保证。
    2. **参数归属**: `rebalance` / `benchmark_symbol` / `start_date` / `end_date` 属于 Run,
       **不属于 portfolio**(见 docs/portfolio-lab-flow.md 第二节归属修正)。所以同一组合可以
       有"不平衡/季平衡/年平衡"三个 Run 并存, 不需要建 9 个组合。
    3. **结果内联为 JSON**: Run 是写完不再改的快照, 数据量是"3267 个净值点 × 1 个组合"级别,
       JSON 最自然, 也避免为"按日期查序列"再建一张表。`nav_json` 除了本组合曲线, 还带上
       对比基准的曲线(同一条日期轴), 前端画对照图不必二次取数。

    `input_hash` = 组合快照 + 参数的摘要 → 相同输入直接复用已有 Run(幂等, 不重复计算)。
    """

    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio.id"), nullable=False, index=True,
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")  # success|failed
    rebalance: Mapped[str] = mapped_column(String(16), nullable=False, default="none")  # none|quarterly|yearly
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 用户选择(可为 None = 采用默认区间); actual_* 是**向前对齐后**真正生效的交易日
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    t0_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 建仓日 = 各标的数据可得区间共同起点

    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    members_json: Mapped[str] = mapped_column(Text, nullable=False)          # 组合快照
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)     # 收益条/指标/回撤/相关性/详情表
    nav_json: Mapped[str | None] = mapped_column(Text, nullable=True)        # 曲线(本组合 + 基准)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_backtest_run_portfolio_created", "portfolio_id", "created_at"),
    )
