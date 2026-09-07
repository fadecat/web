# -*- coding: utf-8 -*-
"""数据状态监控测试: 新鲜度计算 + 任务运行日志包装器。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.models.data_status import TaskRunLog
from backend.models.valuation import CnBondYield
from backend.services import run_logger
from backend.services.data_status import (
    _freshness_state,
    _trading_days_between,
    build_data_status,
    get_dataset_freshness,
)
from backend.utils import latest_trading_day


# ---------------------------------------------------------------------------
# 交易日计数
# ---------------------------------------------------------------------------

def test_trading_days_between_counts_only_weekdays():
    # 2026-09-07 是周一, 2026-09-13 是周日 → 5 个交易日
    assert _trading_days_between(date(2026, 9, 7), date(2026, 9, 13)) == 5
    # 逆序返回 0
    assert _trading_days_between(date(2026, 9, 13), date(2026, 9, 7)) == 0
    # 单日(周一) = 1
    assert _trading_days_between(date(2026, 9, 7), date(2026, 9, 7)) == 1


def test_freshness_state_levels():
    expected = date(2026, 9, 7)
    assert _freshness_state(expected, expected) == "fresh"
    assert _freshness_state(None, expected) == "no_data"
    # 滞后 1 个交易日 → stale
    assert _freshness_state(date(2026, 9, 4), expected) == "stale"
    # 滞后超过 5 个交易日(8/31 前一周起) → lagging
    assert _freshness_state(date(2026, 8, 21), expected) == "lagging"
    # 数据日期晚于预期(异常未来数据)也按新鲜处理,不误报
    assert _freshness_state(date(2026, 9, 8), expected) == "fresh"


# ---------------------------------------------------------------------------
# 新鲜度查询(内存库)
# ---------------------------------------------------------------------------

def test_get_dataset_freshness(db):
    expected_ref = latest_trading_day()
    db.add(CnBondYield(trade_date=expected_ref, yield_10y=2.5))
    db.add(CnBondYield(trade_date=expected_ref - timedelta(days=200), yield_10y=3.0))
    db.commit()

    groups = {g["name"]: g for g in get_dataset_freshness(db)}
    bond = groups["国债收益率"]["entities"][0]
    assert bond["state"] == "fresh"
    assert bond["latest_date"] == expected_ref.isoformat()
    assert bond["count"] == 2
    assert bond["first_date"] == (expected_ref - timedelta(days=200)).isoformat()
    # 无数据的组 entities 为空, state=no_data 而不是报错
    dy = groups["指数股息率"]
    assert dy["entities"] == []
    assert dy["state"] == "no_data"


def test_freshness_per_index_detail(db):
    """估值/股息率/K线组应逐只指数一行, 带条目数与起止日期。"""
    from backend.models.valuation import IndexValuationSnapshot

    d1, d2 = latest_trading_day(), latest_trading_day() - timedelta(days=1)
    db.add(IndexValuationSnapshot(index_code="930955", index_name="红利低波100", trade_date=d1, pe=10.0))
    db.add(IndexValuationSnapshot(index_code="930955", index_name="红利低波100", trade_date=d2, pe=9.9))
    db.commit()

    groups = {g["name"]: g for g in get_dataset_freshness(db)}
    pe = groups["指数估值(PE/PB)"]
    assert pe["state"] == "fresh"
    assert len(pe["entities"]) == 1
    ent = pe["entities"][0]
    assert "红利低波100" in ent["label"] and "930955" in ent["label"]
    assert ent["count"] == 2
    assert ent["latest_date"] == d1.isoformat()
    assert ent["first_date"] == d2.isoformat()


def test_build_data_status_shape(db):
    status = build_data_status(db)
    assert set(status.keys()) == {"generated_at", "expected_date", "datasets", "jobs"}
    # 7 个分组: 估值/股息率/K线/国债/转债快照/强赎/等权指数
    assert len(status["datasets"]) == 7
    assert all("entities" in g for g in status["datasets"])
    assert len(status["jobs"]) == 6
    # 从未运行过的任务 status=never, 不报错
    job = status["jobs"][0]
    assert job["status"] == "never"
    assert job["success_rate"] is None


# ---------------------------------------------------------------------------
# 运行日志包装器
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_db(monkeypatch, db):
    """把 run_logger 的 SessionLocal 指向内存库。"""
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(run_logger, "SessionLocal", sessionmaker(bind=db.bind))
    return db


def test_run_with_logging_success(log_db):
    def job():
        from loguru import logger

        logger.info("=== 任务开始 ===")
        logger.info("新写入 3 条")

    run_logger.run_with_logging("valuation_daily", job)

    row = log_db.query(TaskRunLog).one()
    assert row.job_id == "valuation_daily"
    assert row.status == "success"
    assert row.error is None
    assert row.duration_sec is not None
    assert "新写入 3 条" in row.summary


def test_run_with_logging_partial(log_db):
    run_logger.run_with_logging(
        "valuation_daily", lambda: {"success_count": 7, "fail_count": 1}
    )
    row = log_db.query(TaskRunLog).one()
    assert row.status == "partial"
    assert "1 个标的失败" in row.error


def test_run_with_logging_failed(log_db):
    def bad():
        raise ValueError("网络超时")

    run_logger.run_with_logging("cb_list_daily", bad)
    row = log_db.query(TaskRunLog).one()
    assert row.status == "failed"
    assert "ValueError" in row.error
    assert "网络超时" in row.error


def test_run_with_logging_never_lets_exception_escape(log_db):
    def bad():
        raise RuntimeError("boom")

    # 不应向外抛异常(调度器线程被拖垮比丢一次日志严重)
    run_logger.run_with_logging("cb_index_daily", bad)
    assert log_db.query(TaskRunLog).count() == 1


def test_success_rate_window(db, log_db):
    statuses = ["success"] * 28 + ["failed", "partial"]
    for i, s in enumerate(statuses):
        db.add(
            TaskRunLog(
                job_id="valuation_daily",
                started_at=__import__("datetime").datetime(2026, 9, 1, 22, 6, i),
                finished_at=__import__("datetime").datetime(2026, 9, 1, 22, 6, i + 1),
                duration_sec=1.0,
                status=s,
            )
        )
    db.commit()

    jobs = {j["job_id"]: j for j in build_data_status(db)["jobs"]}
    assert jobs["valuation_daily"]["status"] == "partial"  # 最新一次
    assert jobs["valuation_daily"]["run_count"] == 30
    assert jobs["valuation_daily"]["success_rate"] == round(28 / 30, 4)


# ---------------------------------------------------------------------------
# 手动触发
# ---------------------------------------------------------------------------

def test_registry_covers_service_jobs():
    """注册表与状态页展示的任务清单必须一一对应, 防止加任务只改一处。"""
    from backend.services.data_status import JOBS
    from backend.tasks.registry import JOB_FUNCS

    assert set(JOBS.keys()) == set(JOB_FUNCS.keys())


def test_run_job_manually_unknown_job():
    from fastapi import HTTPException

    from backend.api.routes.data_status import run_job_manually

    with pytest.raises(HTTPException) as exc_info:
        run_job_manually("not_a_job")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 同日覆盖语义(盘中手动跑 → 盘后定时任务覆盖当天行)
# ---------------------------------------------------------------------------

def test_save_bond_yields_overwrites_same_date(db):
    from backend.services.valuation_store import save_bond_yields

    d = latest_trading_day().isoformat()
    save_bond_yields(db, [{"trade_date": d, "cn_10y_bond_yield": 2.0}])
    save_bond_yields(db, [{"trade_date": d, "cn_10y_bond_yield": 2.5}])

    from sqlalchemy import select
    rows = db.execute(select(CnBondYield)).scalars().all()
    assert len(rows) == 1, "同日不应产生第二行"
    assert rows[0].yield_10y == 2.5, "第二次写入应覆盖第一次"


def test_save_cb_index_overwrites_same_date(db):
    from backend.services.cb_index_store import save_cb_index_records

    d = latest_trading_day().isoformat()
    save_cb_index_records(db, [{"date": d, "index_value": 100.0, "avg_premium": 10.0}])
    save_cb_index_records(db, [{"date": d, "index_value": 101.5, "avg_premium": 10.8}])

    from sqlalchemy import select
    from backend.models.valuation import CbIndexDaily

    rows = db.execute(select(CbIndexDaily)).scalars().all()
    assert len(rows) == 1
    assert rows[0].index_value == 101.5
    assert rows[0].avg_premium == 10.8


def test_save_index_quotes_overwrites_same_date(db):
    from backend.services.style_rotation_store import save_index_quotes

    d = latest_trading_day().isoformat()
    save_index_quotes(db, "399376", [{"date": d, "close": 100.0, "open": 99.0}])
    save_index_quotes(db, "399376", [{"date": d, "close": 101.0, "open": 100.5}])

    from sqlalchemy import select
    from backend.models.valuation import IndexDailyQuote

    rows = db.execute(select(IndexDailyQuote)).scalars().all()
    assert len(rows) == 1
    assert rows[0].close == 101.0


def test_save_bond_yields_keeps_other_dates(db):
    """覆盖只作用于同日行, 其他日期不受影响。"""
    from backend.services.valuation_store import save_bond_yields

    d1 = latest_trading_day()
    d0 = (d1 - __import__("datetime").timedelta(days=1)).isoformat()
    save_bond_yields(db, [{"trade_date": d0, "cn_10y_bond_yield": 1.8}])
    save_bond_yields(db, [{"trade_date": d1.isoformat(), "cn_10y_bond_yield": 2.5}])

    from sqlalchemy import select

    rows = db.execute(select(CnBondYield).order_by(CnBondYield.trade_date)).scalars().all()
    assert len(rows) == 2
    assert rows[0].yield_10y == 1.8
    assert rows[1].yield_10y == 2.5
