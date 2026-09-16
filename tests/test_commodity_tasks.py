from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.commodity import (
    CommodityDailyPrice,
    CommodityInstrument,
    CommoditySyncState,
)
from backend.models.database import Base
from backend.services.commodity_source import CommodityPriceRecord, CommoditySourceError


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    return engine, session_factory, db


def _seed(db, *codes):
    for order, code in enumerate(codes, 1):
        db.add(
            CommodityInstrument(
                code=code,
                name=code,
                market="domestic",
                category="test",
                source="akshare",
                enabled=True,
                display_order=order,
            )
        )
    db.commit()


def _records(close=1.0):
    return [CommodityPriceRecord(date(2026, 9, 15), close, source="test")]


def test_daily_processes_enabled_in_display_order_and_keeps_success_when_later_item_fails(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "B", "A")
    calls = []

    def fetch(code, market):
        calls.append(code)
        if code == "A":
            raise RuntimeError("temporary source failure")
        return pd.DataFrame({"date": ["2026-09-15"], "close": [2.0]})

    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    result = commodity_tasks.run_commodity_daily(fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0)

    assert calls == ["B", "A", "A", "A"]
    assert result["total"] == 2
    assert result["success_count"] == 1
    assert result["failed_count"] == 1
    assert result["fail_count"] == 1
    assert db.scalar(select(CommodityDailyPrice).where(CommodityDailyPrice.instrument_code == "B")) is not None
    assert db.get(CommoditySyncState, "A").status == "failed"


def test_transient_failure_retries_twice_but_validation_error_is_not_retried(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "TRANSIENT", "INVALID")
    attempts = {"TRANSIENT": 0, "INVALID": 0}
    sleeps = []

    def fetch(code, market):
        attempts[code] += 1
        if code == "TRANSIENT" and attempts[code] < 3:
            raise TimeoutError("upstream timeout")
        if code == "INVALID":
            raise CommoditySourceError("invalid close")
        return pd.DataFrame({"date": ["2026-09-15"], "close": [2.0]})

    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    result = commodity_tasks.run_commodity_daily(fetch_runner=fetch, sleep=sleeps.append, random_fn=lambda: 0.0)

    assert attempts == {"TRANSIENT": 3, "INVALID": 1}
    assert sleeps[:2] == [2.0, 5.0]
    assert result["success_count"] == 1 and result["fail_count"] == 1


def test_suspicious_counts_as_failure_and_unchanged_counts_as_success(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "UNCHANGED", "SUSPICIOUS")
    db.add(CommodityDailyPrice(
        instrument_code="UNCHANGED", trade_date=date(2026, 9, 15), close=1,
        source="akshare",
    ))
    db.add(CommodityDailyPrice(
        instrument_code="SUSPICIOUS", trade_date=date(2026, 9, 15), close=3,
        source="old",
    ))
    db.commit()

    def fetch(code, market):
        if code == "SUSPICIOUS":
            return pd.DataFrame({"date": ["2026-09-14"], "close": [2.0]})
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    result = commodity_tasks.run_commodity_daily(fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0)

    assert result["unchanged_count"] == 1
    assert result["suspicious_count"] == 1
    assert result["success_count"] == 1
    assert result["failed_count"] == 1
    assert db.get(CommoditySyncState, "SUSPICIOUS").status == "suspicious"


def test_db_write_failure_rolls_back_then_persists_failed_state(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")

    class BrokenStore:
        def __init__(self, session):
            self.session = session

        def upsert_prices(self, *args, **kwargs):
            self.session.add(CommodityDailyPrice(
                instrument_code="RB", trade_date=date(2026, 9, 15), close=4, source="test"
            ))
            raise RuntimeError("write failed")

        def mark_failed(self, code, error):
            state = CommoditySyncState(instrument_code=code, status="failed", consecutive_failures=1, last_error=error)
            self.session.add(state)

    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    monkeypatch.setattr(commodity_tasks, "CommodityStore", BrokenStore)
    result = commodity_tasks.run_commodity_daily(fetch_runner=lambda *_: pd.DataFrame({"date": ["2026-09-15"], "close": [4.0]}), sleep=lambda _: None, random_fn=lambda: 0.0)

    assert result["failed_count"] == 1
    assert db.scalar(select(CommodityDailyPrice).where(CommodityDailyPrice.instrument_code == "RB")) is None
    assert db.get(CommoditySyncState, "RB").status == "failed"


def test_registry_and_data_status_expose_commodity_schedule(monkeypatch):
    from backend.services.data_catalog import catalog
    from backend.services.data_status import JOBS, get_dataset_freshness
    from backend.tasks.registry import DAILY_JOBS, EVERYDAY_JOB_IDS, JOB_FUNCS

    entry = next(item for item in DAILY_JOBS if item[0] == "commodity_daily")
    assert entry[3:] == (15, 50)
    assert entry[0] not in EVERYDAY_JOB_IDS
    assert JOB_FUNCS["commodity_daily"] is entry[1]
    assert JOBS["commodity_daily"]["schedule"] == "交易日 15:50"
    assert "商品价格与分位" in catalog()

    _engine, _session_factory, db = _db()
    _seed(db, "RB")
    db.add(CommodityDailyPrice(
        instrument_code="RB", trade_date=date(2026, 9, 15), close=4, source="akshare"
    ))
    db.add(CommoditySyncState(
        instrument_code="RB", status="success", source_latest_date=date(2026, 9, 15),
        consecutive_failures=0,
    ))
    db.commit()
    group = next(g for g in get_dataset_freshness(db) if g["name"] == "商品价格与分位")
    assert group["entities"][0]["instrument_code"] == "RB"
    assert group["entities"][0]["latest_date"] == "2026-09-15"
    assert group["entities"][0]["sync_status"] == "success"


def test_task_does_not_fetch_when_deadline_has_expired(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    calls = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    result = commodity_tasks.run_commodity_daily(
        fetch_runner=lambda *_: calls.append(1),
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
        monotonic=lambda: 1201.0,
        started_at=0.0,
    )
    assert calls == []
    assert result["total"] == 1 and result["failed_count"] == 1
