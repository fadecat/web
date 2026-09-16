# -*- coding: utf-8 -*-
"""商品监控数据模型。"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    """Return a naive UTC timestamp for the existing database convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CommodityInstrument(Base):
    __tablename__ = "commodity_instrument"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class CommodityDailyPrice(Base):
    __tablename__ = "commodity_daily_price"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_code: Mapped[str] = mapped_column(String(16), ForeignKey("commodity_instrument.code"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    ingest_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("instrument_code", "trade_date", name="uq_commodity_price_code_date"),
        CheckConstraint("close > 0", name="ck_commodity_price_close_positive"),
        Index("ix_commodity_price_code_date", "instrument_code", "trade_date"),
        Index("ix_commodity_price_date", "trade_date"),
    )


class CommodityPercentileDaily(Base):
    __tablename__ = "commodity_percentile_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_code: Mapped[str] = mapped_column(String(16), ForeignKey("commodity_instrument.code"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_code: Mapped[str] = mapped_column(String(8), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    signal: Mapped[str] = mapped_column(String(16), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("instrument_code", "trade_date", "window_code", "algorithm_version", name="uq_commodity_percentile_key"),
        CheckConstraint("percentile IS NULL OR (percentile >= 0 AND percentile <= 100)", name="ck_commodity_percentile_range"),
        Index("ix_commodity_percentile_code_date", "instrument_code", "trade_date"),
        Index("ix_commodity_percentile_date", "trade_date"),
    )


class CommoditySyncState(Base):
    __tablename__ = "commodity_sync_state"

    instrument_code: Mapped[str] = mapped_column(String(16), ForeignKey("commodity_instrument.code"), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_latest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="never")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
