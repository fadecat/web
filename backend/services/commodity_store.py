"""商品日线与分位快照的幂等存储。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.commodity import CommodityDailyPrice, CommodityPercentileDaily, CommoditySyncState
from backend.services.commodity_calculator import ALGORITHM_VERSION, PercentileResult, calculate_percentiles
from backend.services.commodity_source import CommodityPriceRecord


@dataclass(frozen=True)
class CommodityStoreResult:
    inserted_rows: int = 0
    revised_rows: int = 0
    percentile_rows: int = 0
    status: str = "unchanged"
    source_latest_date: date | None = None
    consecutive_failures: int = 0
    last_error: str | None = None


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc).replace(tzinfo=None)


def _state(session: Session, code: str, timestamp: datetime) -> CommoditySyncState:
    # Session.get may not flush when callers deliberately disable autoflush.
    # Reuse a pending same-key state before issuing a database lookup.
    state = next(
        (
            pending
            for pending in session.new
            if isinstance(pending, CommoditySyncState)
            and pending.instrument_code == code
        ),
        None,
    )
    if state is None:
        state = session.get(CommoditySyncState, code)
    if state is None:
        state = CommoditySyncState(instrument_code=code, status="never", consecutive_failures=0, updated_at=timestamp)
        session.add(state)
    state.last_attempt_at = timestamp
    state.updated_at = timestamp
    return state


class CommodityStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_prices(
        self,
        instrument_code: str,
        records: Iterable[CommodityPriceRecord],
        *,
        source_latest_date: date | None = None,
        ingest_run_id: int | None = None,
        now: datetime | None = None,
    ) -> CommodityStoreResult:
        """写入一个品种；调用方负责事务和 commit。"""
        timestamp = _now(now)
        records = list(records)
        if not records:
            raise ValueError("commodity source result is empty")
        # Validate the complete batch before mutating the session.  A caller
        # may catch this error, record a failure, and commit the same session.
        for record in records:
            if record.trade_date is None:
                raise ValueError(f"invalid trade date for {instrument_code}")
            if not math.isfinite(record.close) or record.close <= 0:
                raise ValueError(f"invalid close for {instrument_code} on {record.trade_date}")
        source_latest_date = source_latest_date or max(row.trade_date for row in records)
        existing = list(self.session.scalars(select(CommodityDailyPrice).where(CommodityDailyPrice.instrument_code == instrument_code)))
        db_latest = max((row.trade_date for row in existing), default=None)
        source_count = len({row.trade_date for row in records})
        if (db_latest is not None and source_latest_date < db_latest) or (
            existing and source_count < len(existing) * 0.8
        ):
            state = _state(self.session, instrument_code, timestamp)
            state.status = "suspicious"
            state.source_latest_date = source_latest_date
            state.consecutive_failures += 1
            state.last_error = "source date regressed" if db_latest and source_latest_date < db_latest else "source row count below 80% of database"
            return CommodityStoreResult(status="suspicious", source_latest_date=source_latest_date, consecutive_failures=state.consecutive_failures, last_error=state.last_error)

        by_date = {row.trade_date: row for row in records}
        inserted = revised = 0
        existing_by_date = {row.trade_date: row for row in existing}
        existing_by_date.update(
            {
                pending.trade_date: pending
                for pending in self.session.new
                if isinstance(pending, CommodityDailyPrice)
                and pending.instrument_code == instrument_code
            }
        )
        for trade_date, record in sorted(by_date.items()):
            current = existing_by_date.get(trade_date)
            values = {
                "close": record.close,
                "open": record.open,
                "high": record.high,
                "low": record.low,
                "volume": record.volume,
                "source": record.source,
            }
            if current is None:
                self.session.add(
                    CommodityDailyPrice(
                        instrument_code=instrument_code,
                        trade_date=trade_date,
                        ingest_run_id=ingest_run_id,
                        created_at=timestamp,
                        updated_at=timestamp,
                        **values,
                    )
                )
                inserted += 1
            elif any(getattr(current, key) != value for key, value in values.items()):
                for key, value in values.items():
                    setattr(current, key, value)
                if ingest_run_id is not None:
                    current.ingest_run_id = ingest_run_id
                current.updated_at = timestamp
                revised += 1

        # Flush makes the just-added history visible to the following SELECT.
        self.session.flush()
        history = list(self.session.scalars(select(CommodityDailyPrice).where(CommodityDailyPrice.instrument_code == instrument_code).order_by(CommodityDailyPrice.trade_date)))
        latest_date = max(row.trade_date for row in history)
        metrics = calculate_percentiles([row.close for row in history])
        percentile_rows = self._upsert_percentiles(instrument_code, latest_date, metrics, timestamp)
        status = "success" if inserted or revised else "unchanged"
        state = _state(self.session, instrument_code, timestamp)
        state.status = status
        state.source_latest_date = source_latest_date
        state.last_success_at = timestamp
        state.consecutive_failures = 0
        state.last_error = None
        return CommodityStoreResult(inserted, revised, percentile_rows, status, source_latest_date, 0, None)

    def _upsert_percentiles(self, code: str, trade_date: date, metrics: dict[str, PercentileResult], timestamp: datetime) -> int:
        rows = list(self.session.scalars(select(CommodityPercentileDaily).where(CommodityPercentileDaily.instrument_code == code, CommodityPercentileDaily.trade_date == trade_date, CommodityPercentileDaily.algorithm_version == ALGORITHM_VERSION)))
        by_window = {row.window_code: row for row in rows}
        count = 0
        for window_code, metric in metrics.items():
            values = {"window_code": window_code, "window_days": metric.window_days, "percentile": metric.percentile, "sample_count": metric.sample_count, "signal": metric.signal}
            current = by_window.get(window_code)
            if current is None:
                self.session.add(CommodityPercentileDaily(instrument_code=code, trade_date=trade_date, algorithm_version=ALGORITHM_VERSION, created_at=timestamp, updated_at=timestamp, **values))
            elif any(getattr(current, key) != value for key, value in values.items()):
                for key, value in values.items():
                    setattr(current, key, value)
                current.updated_at = timestamp
            count += 1
        return count

    def mark_failed(self, instrument_code: str, error: str, *, now: datetime | None = None) -> CommodityStoreResult:
        timestamp = _now(now)
        state = _state(self.session, instrument_code, timestamp)
        state.status = "failed"
        state.consecutive_failures += 1
        state.last_error = str(error)[:1000]
        return CommodityStoreResult(status="failed", consecutive_failures=state.consecutive_failures, last_error=state.last_error)

    def mark_success(self, instrument_code: str, *, source_latest_date: date | None = None, status: str = "success", now: datetime | None = None) -> CommodityStoreResult:
        timestamp = _now(now)
        state = _state(self.session, instrument_code, timestamp)
        state.status = status
        state.source_latest_date = source_latest_date or state.source_latest_date
        state.last_success_at = timestamp
        state.consecutive_failures = 0
        state.last_error = None
        return CommodityStoreResult(status=status, source_latest_date=state.source_latest_date)


def upsert_commodity_prices(session: Session, instrument_code: str, records: Iterable[CommodityPriceRecord], **kwargs) -> CommodityStoreResult:
    return CommodityStore(session).upsert_prices(instrument_code, records, **kwargs)
