from __future__ import annotations

from datetime import date
from datetime import datetime

import pandas as pd
import pytest
from requests import Response
from requests import exceptions as requests_exceptions
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


@pytest.fixture(autouse=True)
def _commodity_tasks_run_on_trading_day(monkeypatch):
    """Keep normal-path task tests independent of the host calendar date."""
    from backend.tasks import commodity_tasks

    monkeypatch.setattr(commodity_tasks, "is_trading_day", lambda _day: True)


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
            raise ConnectionError("temporary source failure")
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


def test_non_trading_day_skips_before_listing_or_fetch(monkeypatch):
    from backend.tasks import commodity_tasks

    calls = []
    monkeypatch.setattr(commodity_tasks, "is_trading_day", lambda _day: False)
    monkeypatch.setattr(
        commodity_tasks,
        "SessionLocal",
        lambda: pytest.fail("non-trading day must not open a database session"),
    )

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=lambda *_: calls.append(1),
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )

    assert result["status"] == "skipped"
    assert result["total"] == 0
    assert result["success_count"] == 0
    assert result["fail_count"] == 0
    assert calls == []


def test_item_budget_expiry_after_first_fetch_does_not_retry(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    calls = []
    clock_values = iter([0.0, 0.0, 0.0, 21.0])
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(*_args):
        calls.append(1)
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch,
        monotonic=lambda: next(clock_values),
        started_at=0.0,
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert calls == [1]
    assert result["failed_count"] == 1
    assert db.scalar(select(CommodityDailyPrice)) is None


def test_item_budget_is_checked_before_normalizing_late_fetch(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    calls = []
    normalized = []
    clock_values = iter([0.0, 0.0, 0.0, 21.0])
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    original_normalize = commodity_tasks._normalize_result

    def fetch(*_args):
        calls.append(1)
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    def normalize(raw):
        normalized.append(raw)
        return original_normalize(raw)

    monkeypatch.setattr(commodity_tasks, "_normalize_result", normalize)
    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch,
        monotonic=lambda: next(clock_values),
        started_at=0.0,
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert calls == [1]
    assert normalized == []
    assert result["failed_count"] == 1
    assert db.scalar(select(CommodityDailyPrice)) is None


def test_total_deadline_after_fetch_does_not_commit_and_marks_remaining(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "A", "B", "C")
    fetched = []
    clock_values = iter([0.0, 0.0, 0.0, 1201.0])
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(code, _market):
        fetched.append(code)
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch,
        monotonic=lambda: next(clock_values),
        started_at=0.0,
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert fetched == ["A"]
    assert result["success_count"] == 0 and result["failed_count"] == 3
    assert db.scalars(select(CommodityDailyPrice)).all() == []
    assert all(db.get(CommoditySyncState, code).status == "failed" for code in ("A", "B", "C"))


def test_failed_state_write_is_reported_when_old_success_cannot_be_replaced(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    db.add(CommoditySyncState(instrument_code="RB", status="success", consecutive_failures=0))
    db.commit()
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    mark_attempts = []

    def fail_mark(*_args, **_kwargs):
        mark_attempts.append(1)
        raise RuntimeError("state db down")

    monkeypatch.setattr(
        commodity_tasks.CommodityStore,
        "mark_failed",
        fail_mark,
    )

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=lambda *_: (_ for _ in ()).throw(RuntimeError("source down")),
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert result["failed_count"] == 1
    assert result["state_persist_failed_count"] == 1
    assert result["state_persist_failed_codes"] == ["RB"]
    assert result["state_persist_failed_details"] == [{"code": "RB", "error": "source down"}]
    assert len(mark_attempts) == 2
    assert db.get(CommoditySyncState, "RB").status == "success"


def test_state_persist_failure_result_keeps_bounded_codes_and_details(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    codes = [f"F{i:02d}" for i in range(25)]
    _seed(db, *codes)
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    monkeypatch.setattr(
        commodity_tasks.CommodityStore,
        "mark_failed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("state db down")),
    )

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=lambda code, _market: (_ for _ in ()).throw(
            RuntimeError(f"source failure {code}")
        ),
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert result["failed_count"] == 25
    assert result["state_persist_failed_count"] == 25
    assert result["state_persist_failed_codes"] == codes[:20]
    assert len(result["state_persist_failed_details"]) == 20
    assert all(set(detail) == {"code", "error"} for detail in result["state_persist_failed_details"])


def test_data_management_uses_commodity_1550_policy_for_freshness(monkeypatch):
    import backend.services.data_management as management
    from backend.models.valuation import CnBondYield
    from backend.services.data_management import build_data_management

    _engine, _session_factory, db = _db()
    _seed(db, "RB")
    db.add(CommodityDailyPrice(
        instrument_code="RB", trade_date=date(2026, 9, 15), close=1, source="akshare"
    ))
    db.add(CnBondYield(trade_date=date(2026, 9, 15), yield_10y=2.0))
    db.commit()

    fixed_before = datetime(2026, 9, 16, 15, 30)
    fixed_after = datetime(2026, 9, 16, 15, 51)
    expected_args = []
    original_expected = management._expected_date
    monkeypatch.setattr(
        management,
        "_expected_date",
        lambda value=None: (expected_args.append(value), original_expected(value))[1],
    )
    before = build_data_management(db, now=fixed_before)
    after = build_data_management(db, now=fixed_after)
    before_group = next(g for g in before["non_index_groups"] if g["label"] == "商品价格与分位")
    after_group = next(g for g in after["non_index_groups"] if g["label"] == "商品价格与分位")
    assert before_group["entities"][0]["state"] == "fresh"
    assert after_group["entities"][0]["state"] == "stale"
    assert expected_args == [fixed_before, fixed_after]


def test_data_status_group_ignores_disabled_entities_and_marks_all_disabled(monkeypatch):
    from backend.services.data_status import build_data_status

    _engine, _session_factory, db = _db()
    _seed(db, "ON", "OFF")
    db.query(CommodityInstrument).filter_by(code="OFF").one().enabled = False
    db.commit()
    db.add(CommodityDailyPrice(
        instrument_code="ON", trade_date=date(2026, 9, 15), close=1, source="akshare"
    ))
    db.commit()
    fixed_now = datetime(2026, 9, 16, 15, 30)
    group = next(
        g for g in build_data_status(db, now=fixed_now)["datasets"]
        if g["name"] == "商品价格与分位"
    )
    assert group["state"] == "fresh"

    db.query(CommodityInstrument).update({"enabled": False})
    db.commit()
    disabled_group = next(
        g for g in build_data_status(db, now=fixed_now)["datasets"]
        if g["name"] == "商品价格与分位"
    )
    assert disabled_group["state"] == "disabled"


def test_scheduler_registers_commodity_cron_with_existing_execution_policy(monkeypatch):
    from backend import scheduler as scheduler_module

    calls = []
    monkeypatch.setattr(
        scheduler_module.scheduler,
        "add_job",
        lambda func, **kwargs: calls.append((func, kwargs)),
    )
    scheduler_module._register_daily_jobs()
    commodity = next(kwargs for _func, kwargs in calls if kwargs["id"] == "commodity_daily")
    assert "day_of_week='mon-fri'" in str(commodity["trigger"])
    assert commodity["replace_existing"] is True
    assert commodity["coalesce"] is True
    assert commodity["misfire_grace_time"] == 3600


def test_pacing_delay_crossing_total_deadline_stops_before_next_fetch(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "A", "B")
    fetched = []
    clock_values = iter([0.0, 0.0, 0.0, 0.0, 0.0, 1199.0])
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(code, _market):
        fetched.append(code)
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch,
        monotonic=lambda: next(clock_values),
        started_at=0.0,
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert fetched == ["A"]
    assert result["success_count"] == 1 and result["failed_count"] == 1
    assert db.get(CommoditySyncState, "B").status == "failed"


def test_start_scheduler_only_registers_jobs_without_running_commodity(monkeypatch):
    from backend import scheduler as scheduler_module
    from backend.tasks import commodity_tasks

    class FakeScheduler:
        running = False

        def __init__(self):
            self.registered = []
            self.started = False

        def add_job(self, func, **kwargs):
            self.registered.append(kwargs["id"])

        def start(self):
            self.started = True

    fake = FakeScheduler()
    fetched = []
    monkeypatch.setattr(scheduler_module, "scheduler", fake)
    monkeypatch.setattr(scheduler_module, "_register_daily_jobs", lambda: fake.registered.append("registered"))
    monkeypatch.setattr(scheduler_module, "_maybe_backfill_style_rotation", lambda: None)
    monkeypatch.setattr(scheduler_module, "_startup_integrity_scan", lambda: None)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: None)
    monkeypatch.setattr(commodity_tasks, "run_commodity_daily", lambda: fetched.append(1))
    monkeypatch.setattr("backend.services.run_logger.recover_interrupted_runs", lambda: 0)
    monkeypatch.setattr("backend.tasks.stock_financial_tasks.initialize_stock_financial_bootstrap", lambda: None)

    scheduler_module.start_scheduler()
    assert fake.started is True
    assert "registered" in fake.registered
    assert fetched == []


def test_registered_wrapper_and_manual_trigger_use_registry_callable_and_write_manual_log(monkeypatch):
    from backend import scheduler as scheduler_module
    from backend.api.routes import data_status as route
    from backend.models.data_status import TaskRunLog
    from backend.services import run_logger
    from backend.tasks import registry

    _engine, session_factory, db = _db()
    calls = []

    def fake_task():
        calls.append("task")
        return {"success_count": 1, "fail_count": 0}

    daily_original = registry.DAILY_JOBS
    funcs_original = registry.JOB_FUNCS["commodity_daily"]
    registry.DAILY_JOBS = [
        (*entry[:1], fake_task, *entry[2:]) if entry[0] == "commodity_daily" else entry
        for entry in daily_original
    ]
    registry.JOB_FUNCS["commodity_daily"] = fake_task
    monkeypatch.setattr(scheduler_module.scheduler, "add_job", lambda func, **kwargs: calls.append((kwargs["id"], func)))
    monkeypatch.setattr(run_logger, "SessionLocal", session_factory)
    monkeypatch.setattr(route, "threading", type("SyncThreading", (), {
        "Thread": type("SyncThread", (), {
            "__init__": lambda self, target, **_kwargs: setattr(self, "target", target),
            "start": lambda self: self.target(),
        }),
    }))
    try:
        scheduler_module._register_daily_jobs()
        wrapper = next(func for job_id, func in calls if job_id == "commodity_daily")
        wrapper()
        assert calls.count("task") == 1

        # The route captures JOB_FUNCS at call time, so it must run the same replacement.
        result = route.run_job_manually("commodity_daily")
        assert result == {"status": "started", "job_id": "commodity_daily"}
        assert calls.count("task") == 2
        row = (
            db.query(TaskRunLog)
            .filter_by(job_id="commodity_daily")
            .order_by(TaskRunLog.started_at.desc())
            .first()
        )
        assert "manual" in row.summary
    finally:
        registry.DAILY_JOBS = daily_original
        registry.JOB_FUNCS["commodity_daily"] = funcs_original


def test_fetch_runner_skips_source_factory_construction(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    factory_calls = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def broken_factory():
        factory_calls.append(1)
        raise AssertionError("fetch_runner should not construct an adapter")

    result = commodity_tasks.run_commodity_daily(
        source_factory=broken_factory,
        fetch_runner=lambda *_: pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]}),
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert result["success_count"] == 1
    assert factory_calls == []


def test_source_factory_failure_marks_one_item_and_continues_with_next_factory(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "A", "B")
    factory_calls = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    class Adapter:
        def fetch_history(self, code, _market):
            return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    def factory():
        factory_calls.append(1)
        if len(factory_calls) == 1:
            raise RuntimeError("adapter init failed")
        return Adapter()

    result = commodity_tasks.run_commodity_daily(
        source_factory=factory,
        sleep=lambda _: None,
        random_fn=lambda: 0.0,
    )
    assert factory_calls == [1, 1]
    assert result["success_count"] == 1 and result["failed_count"] == 1
    assert db.get(CommoditySyncState, "A").status == "failed"
    assert db.get(CommoditySyncState, "B").status == "success"


def test_non_retryable_type_error_is_called_once(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    calls = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(*_args):
        calls.append(1)
        raise TypeError("bad hook")

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0
    )
    assert calls == [1]
    assert result["failed_count"] == 1


def test_connection_error_retries_three_times_then_succeeds(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    attempts = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(*_args):
        attempts.append(1)
        if len(attempts) < 3:
            raise requests_exceptions.ConnectionError("upstream down")
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0
    )
    assert len(attempts) == 3
    assert result["success_count"] == 1


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: _http_error(404),
        lambda: requests_exceptions.TooManyRedirects("redirect loop"),
    ],
)
def test_http_errors_are_not_retried_by_default(monkeypatch, error_factory):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    calls = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(*_args):
        calls.append(1)
        raise error_factory()

    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0
    )
    assert calls == [1]
    assert result["failed_count"] == 1


def _http_error(status_code):
    response = Response()
    response.status_code = status_code
    return requests_exceptions.HTTPError("http failure", response=response)


def test_normalize_connection_error_is_not_retried(monkeypatch):
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, "RB")
    calls = []
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)

    def fetch(*_args):
        calls.append(1)
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    def normalize(_raw):
        raise requests_exceptions.ConnectionError("normalizer failure")

    monkeypatch.setattr(commodity_tasks, "_normalize_result", normalize)
    result = commodity_tasks.run_commodity_daily(
        fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0
    )
    assert calls == [1]
    assert result["failed_count"] == 1


def test_run_logger_bounds_failure_codes_and_excludes_exception_details(monkeypatch):
    from backend.models.data_status import TaskRunLog
    from backend.services import notifications, run_logger

    _engine, session_factory, db = _db()
    monkeypatch.setattr(run_logger, "SessionLocal", session_factory)
    monkeypatch.setattr(notifications, "notify_task_failure", lambda *_: None)
    codes = [f"C{i:02d}-{'X' * 28}" for i in range(21)]
    result = {
        "total": 21,
        "success_count": 0,
        "unchanged_count": 0,
        "failed_count": 21,
        "suspicious_count": 0,
        "inserted_rows": 0,
        "revised_rows": 0,
        "percentile_rows": 0,
        "fail_count": 21,
        "state_persist_failed_count": 21,
        "state_persist_failed_codes": codes,
        "state_persist_failed_details": [
            {"code": "C00", "error": "SECRET_EXCEPTION_DETAILS"}
        ],
    }

    run_logger.run_with_logging("commodity_daily", lambda: result)
    row = db.query(TaskRunLog).filter_by(job_id="commodity_daily").one()
    assert row.status == "failed"
    assert "state_persist_failed_count=21" in row.summary
    for code in codes[:20]:
        assert code in row.summary
    assert codes[20] not in row.summary
    assert "SECRET_EXCEPTION_DETAILS" not in row.summary
    assert len(row.summary) <= 2000


def test_run_logger_persists_bounded_structured_summary_with_early_state_failure(monkeypatch):
    from backend.models.data_status import TaskRunLog
    from backend.services import run_logger
    from backend.services import notifications
    from backend.tasks import commodity_tasks

    _engine, session_factory, db = _db()
    _seed(db, *(f"C{i:02d}" for i in range(20)))
    monkeypatch.setattr(commodity_tasks, "SessionLocal", session_factory)
    monkeypatch.setattr(run_logger, "SessionLocal", session_factory)
    monkeypatch.setattr(notifications, "notify_task_failure", lambda *_: None)
    original_mark_failed = commodity_tasks.CommodityStore.mark_failed

    def fail_first_state(self, code, error):
        if code == "C00":
            raise RuntimeError("state persistence unavailable")
        return original_mark_failed(self, code, error)

    monkeypatch.setattr(commodity_tasks.CommodityStore, "mark_failed", fail_first_state)

    def fetch(code, _market):
        if code == "C00":
            raise RuntimeError("source unavailable")
        return pd.DataFrame({"date": ["2026-09-15"], "close": [1.0]})

    run_logger.run_with_logging(
        "commodity_daily",
        lambda: commodity_tasks.run_commodity_daily(
            fetch_runner=fetch, sleep=lambda _: None, random_fn=lambda: 0.0
        ),
    )
    row = db.query(TaskRunLog).filter_by(job_id="commodity_daily").one()
    assert row.status == "partial"
    assert "C00" in row.summary
    assert "state_persist_failed_count" in row.summary
    assert len(row.summary) <= 2000
