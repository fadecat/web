# -*- coding: utf-8 -*-
"""研究回放(次日 T 价位)数据模型。

对应设计 docs/superpowers/specs/2026-09-18-next-day-t-research-replay-design.md §4/§6:
- 行情事实: research_security / research_daily_bar_raw / research_daily_bar_adjusted /
  research_data_snapshot / research_data_revision / research_trade_calendar /
  research_corporate_event;
- 回放结果: research_replay_run / research_replay_day(汇总不落库, 由逐日明细聚合)。

约定:
- symbol 全链路用规范代码(带交易所后缀, 如 600900.SH); 适配器仅在边界剥成六位码。
- 时间列为 naive UTC(与 commodity 模型一致)。
- research_daily_bar_adjusted.adjust_mode V1 恒为 "HFQ"; 该列不得理解为官方复权因子。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
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


class ResearchSecurity(Base):
    """研究标的名单(yaml 显式清单 upsert, 不做运行时推导)。"""

    __tablename__ = "research_security"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    security_type: Mapped[str] = mapped_column(String(8), nullable=False)  # STOCK | ETF
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)  # SSE | SZSE
    # 数据来源(标的级声明, 驱动 Provider 路由; 未知来源报错, 不静默换源)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="akshare")
    # 选样名单(纯标注: 高股息 | 转债正股 | 手动ETF), 不参与逻辑
    selection_list: Mapped[str] = mapped_column(String(32), nullable=False, default="手动ETF")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", name="uq_research_security_symbol"),
        Index("ix_research_security_type", "security_type"),
    )


class ResearchDataSnapshot(Base):
    """raw/hfq 配对抓取快照(append-only, 不更新)。

    仅当两侧完整且日期匹配时 status=USABLE, 否则 REJECTED 且不写任何 bar 行。
    """

    __tablename__ = "research_data_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    request_start: Mapped[date] = mapped_column(Date, nullable=False)
    request_end: Mapped[date] = mapped_column(Date, nullable=False)
    raw_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    hfq_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hfq_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dates_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # USABLE | REJECTED
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    first_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "fetched_at", "source", name="uq_research_snapshot_key"),
        Index("ix_research_snapshot_symbol", "symbol"),
    )


class ResearchDailyBarRaw(Base):
    """未复权交易价格日线(宽表对齐源字段, 量额单位在适配器转换并显式标记)。"""

    __tablename__ = "research_daily_bar_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("research_data_snapshot.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "source", name="uq_research_bar_raw_key"),
        Index("ix_research_bar_raw_symbol_date", "symbol", "trade_date"),
    )


class ResearchDailyBarAdjusted(Base):
    """后复权(hfq)连续研究价格日线。

    adjust_mode V1 恒 "HFQ"; 不得命名为官方复权因子。
    """

    __tablename__ = "research_daily_bar_adjusted"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjust_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="HFQ")
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("research_data_snapshot.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "adjust_mode", "source", name="uq_research_bar_adj_key"),
        Index("ix_research_bar_adj_symbol_date", "symbol", "trade_date"),
    )


class ResearchDataRevision(Base):
    """供应商修订记录: 保留旧观察的完整载荷(仅有哈希不足以复现)。"""

    __tablename__ = "research_data_revision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    business_key: Mapped[str] = mapped_column(String(128), nullable=False)  # 如 "600900.SH|2026-09-10|RAW"
    old_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    new_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    old_payload: Mapped[str] = mapped_column(String, nullable=False)  # 规范化 JSON 全载荷
    new_payload: Mapped[str] = mapped_column(String, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("research_data_snapshot.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("table_name", "business_key", "new_hash", name="uq_research_revision_key"),
        Index("ix_research_revision_key", "table_name", "business_key"),
    )


class ResearchTradeCalendar(Base):
    """版本化交易日历(来自 AkShare tool_trade_date_hist_sina, 不用人工节假日表)。"""

    __tablename__ = "research_trade_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)  # 抓取批次(fetched_at.isoformat)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("exchange", "trade_date", "source", name="uq_research_calendar_key"),
        Index("ix_research_calendar_date", "trade_date"),
    )


class ResearchCorporateEvent(Base):
    """标的权益事件日历(新浪 hfq.js 除权除息日; 权益事件主检测来源)。

    factor/cumulative_dividend 为信息性字段(非官方复权因子, 股票行无累计分红);
    upsert 覆盖更新因子, 事件日期本身 append-only。
    """

    __tablename__ = "research_corporate_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    cumulative_dividend: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "event_date", "source", name="uq_research_corporate_event_key"),
        Index("ix_research_corporate_event_symbol", "symbol", "event_date"),
    )


class ResearchReplayRun(Base):
    """一次回放(标的 × λ × 窗口 × 算法版本 × 区间), 结果物化到 research_replay_day。"""

    __tablename__ = "research_replay_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    param_lambda: Mapped[float] = mapped_column(Float, nullable=False)
    quantile_window: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)  # 评价日(T)区间
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    train_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    train_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hfq_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # DONE | FAILED
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "symbol", "param_lambda", "quantile_window", "algorithm_version",
            "start_date", "end_date",
            name="uq_research_replay_run_key",
        ),
        Index("ix_research_replay_run_symbol", "symbol"),
    )


class ResearchReplayDay(Base):
    """回放逐日明细: T 日计划 + T+1 评价证据(汇总由本表聚合复算)。"""

    __tablename__ = "research_replay_day"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("research_replay_run.id"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)  # T
    eval_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # T+1(相邻有行情日)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ACTIVE | DISABLED
    reason_codes: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON: [{code, trigger_date, measured, threshold, evidence}]
    z_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr14: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema20: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_levels_raw: Mapped[str | None] = mapped_column(String, nullable=True)   # JSON [3] 未取整模型价
    sell_levels_raw: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON [3] 未取整模型价
    next_open: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_hits: Mapped[str | None] = mapped_column(String, nullable=True)   # JSON [3] 布尔
    sell_hits: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON [3] 布尔
    buy_open_invalid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sell_open_invalid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # BUY_ONLY | SELL_ONLY | BOTH_HIT | NO_HIT | DISABLED |
    # EXCLUDED_DATA | EXCLUDED_CORP_ACTION | EXCLUDED_OPEN_INVALIDATION | CALENDAR_UNVERIFIED
    day_category: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON: 开盘失效线/r_t 阶跃/双侧触达证据等
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "plan_date", name="uq_research_replay_day_key"),
        Index("ix_research_replay_day_run", "run_id", "plan_date"),
    )
