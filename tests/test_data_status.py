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
    # 目录接入后: 每个实体带纳管/来源/预期信息, 任务带下次计划时间
    ent = status["datasets"][0]["entities"][0]
    assert ent.get("managed") is True
    assert "expected_date" in ent and "next_due_at" in ent
    assert ent.get("policy_provisional") is True
    assert all(j.get("next_run_at") for j in status["jobs"])


def test_catalog_matches_expected_lag(db):
    """易方达估值 T+1 规则: 周一晚 20 点应预期上周五(周一估值周二才到期)。"""
    from datetime import datetime

    from backend.services.data_catalog import Policy

    pe = Policy("efunds", "valuation_daily", 12, trading_day_offset=1)
    # 2026-09-07 周一 20:00 → 周一估值尚未到期, 应到日期仍是上周五 09-04
    expected, next_due = pe.expected(datetime(2026, 9, 7, 20, 0))
    assert expected.isoformat() == "2026-09-04"
    assert next_due.isoformat().startswith("2026-09-08T12:00")
    # 周二 14:00 → 周一估值已到期, 应到日期 = 09-07
    expected2, _ = pe.expected(datetime(2026, 9, 8, 14, 0))
    assert expected2.isoformat() == "2026-09-07"


def test_catalog_marks_unmanaged_legacy(db):
    """库中存在但不在目录清单里的代码 → 单列 unmanaged, 不进主清单也不误报。"""
    from datetime import datetime

    from backend.models.valuation import IndexValuationSnapshot
    from backend.services.data_catalog import apply_catalog

    d = latest_trading_day()
    db.add(IndexValuationSnapshot(index_code="999999", index_name="孤儿指数", trade_date=d, pe=1.0))
    db.commit()

    groups = get_dataset_freshness(db)
    groups = apply_catalog(groups, _freshness_state, now=datetime(2026, 9, 8, 14, 0))
    pe = next(g for g in groups if g["name"] == "指数估值(PE/PB)")
    codes = [e["index_code"] for e in pe["entities"]]
    assert "999999" not in codes, "孤儿不应混入纳管清单"
    legacy = [e for e in pe.get("unmanaged_entities", [])]
    assert any(e["label"].startswith("孤儿指数") for e in legacy)
    # 孤儿即使最新也不影响组状态
    assert pe["state"] in {"fresh", "no_data", "stale", "lagging"}


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


def test_run_with_logging_writes_running_first(log_db):
    """执行开始即持久化 running 记录, 任务完成前状态页可见真实运行态。"""
    seen = {}

    def job():
        row = log_db.query(TaskRunLog).one()
        seen["status_during"] = row.status
        seen["trigger"] = row.summary

    run_logger.run_with_logging("cb_list_daily", job, trigger="manual")

    assert seen["status_during"] == "running", "任务执行中必须能查到 running 记录"
    assert "manual" in seen["trigger"]
    row = log_db.query(TaskRunLog).one()
    assert row.status == "success"


def test_run_with_logging_all_failed_is_failed(log_db):
    """全失败(无成功子项)必须记 failed, 不能记 partial。"""
    run_logger.run_with_logging(
        "style_rotation_daily", lambda: {"success_count": 0, "fail_count": 2}
    )
    row = log_db.query(TaskRunLog).one()
    assert row.status == "failed"


def test_run_with_logging_skipped(log_db):
    run_logger.run_with_logging(
        "valuation_daily", lambda: {"status": "skipped", "fail_count": 0}
    )
    row = log_db.query(TaskRunLog).one()
    assert row.status == "skipped"


def test_runner_lock_blocks_concurrent_same_job(log_db):
    """同一任务不允许并发执行(手动+定时共用一把锁)。"""
    from backend.services.run_logger import reserve_job

    assert reserve_job("cb_list_daily") is True
    result = run_logger.run_with_logging("cb_list_daily", lambda: None)
    assert result["status"] == "busy"
    # 模拟任务结束释放
    from backend.services.run_logger import release_job

    release_job("cb_list_daily")
    result = run_logger.run_with_logging("cb_list_daily", lambda: None)
    assert result["status"] == "success"


def test_recover_interrupted_runs(log_db):
    """启动结转: 遗留 running 记录标 interrupted, 不算成功也不永久转圈。"""
    from backend.services.run_logger import recover_interrupted_runs

    db = log_db
    db.add(TaskRunLog(job_id="valuation_daily", started_at=__import__("datetime").datetime(2026, 9, 6, 22, 6), status="running"))
    db.commit()

    n = recover_interrupted_runs()
    assert n == 1
    row = db.query(TaskRunLog).one()
    assert row.status == "interrupted"
    assert row.finished_at is not None
    # 幂等: 再跑一次不再产生新变化
    assert recover_interrupted_runs() == 0


def test_partial_still_works(log_db):
    run_logger.run_with_logging(
        "valuation_daily", lambda: {"success_count": 7, "fail_count": 1}
    )
    row = log_db.query(TaskRunLog).one()
    assert row.status == "partial"
    assert "1 个数据子项失败" in row.error


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


def test_failed_run_triggers_notification(log_db, monkeypatch):
    """任务全失败后应触发一次失败通知(通知失败自吞, 不影响运行记录)。"""
    from backend.services import notifications

    calls = []
    monkeypatch.setattr(
        notifications, "notify_task_failure",
        lambda job_id, status, error: calls.append((job_id, status, error)),
    )

    run_logger.run_with_logging(
        "style_rotation_daily", lambda: {"success_count": 0, "fail_count": 2}
    )

    assert calls == [("style_rotation_daily", "failed", "2 个数据子项失败,详见日志")]


def test_success_run_does_not_notify(log_db, monkeypatch):
    """任务成功不触发通知。"""
    from backend.services import notifications

    calls = []
    monkeypatch.setattr(
        notifications, "notify_task_failure",
        lambda job_id, status, error: calls.append((job_id, status, error)),
    )

    run_logger.run_with_logging("valuation_daily", lambda: None)

    assert calls == []


def test_everyday_jobs_next_run_on_weekend():
    """周六已过触发时刻：易方达历史同步顺延周日，市场快照顺延周一。"""
    from datetime import datetime

    from backend.services.data_status import _next_run_times

    times = _next_run_times(datetime(2026, 9, 12, 23, 0))  # 周六
    assert times["valuation_daily"].startswith("2026-09-13T22:06")
    assert times["index_eod_daily"].startswith("2026-09-13T22:09")
    assert times["style_rotation_daily"].startswith("2026-09-14T22:03")
    assert times["cb_list_daily"].startswith("2026-09-14T15:06")


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


# ---------------------------------------------------------------------------
# 系统配置(SMTP 通知) 存储层
# ---------------------------------------------------------------------------

def test_app_settings_defaults_and_masking(db):
    from backend.services.app_settings import get_settings_dict, get_settings_masked

    # 无配置时返回 QQ 邮箱默认值
    values = get_settings_dict(db)
    assert values["smtp_host"] == "smtp.qq.com"
    assert values["smtp_port"] == "465"

    # 掩码视图: 敏感项不回明文, configured=False
    masked = get_settings_masked(db)
    assert masked["smtp_password"]["value"] == ""
    assert masked["smtp_password"]["configured"] is False
    assert masked["smtp_password"]["sensitive"] is True
    assert masked["smtp_host"]["value"] == "smtp.qq.com"


def test_app_settings_save_and_sensitive_blank_keeps(db):
    from backend.services.app_settings import get_settings_dict, save_settings

    save_settings(db, {"smtp_user": "a@qq.com", "smtp_password": "secret123"})
    assert get_settings_dict(db)["smtp_password"] == "secret123"

    # 敏感项留空 = 保持原值; 非敏感项可更新
    save_settings(db, {"smtp_user": "b@qq.com", "smtp_password": ""})
    values = get_settings_dict(db)
    assert values["smtp_password"] == "secret123"
    assert values["smtp_user"] == "b@qq.com"


def test_app_settings_unknown_key_ignored(db):
    from backend.services.app_settings import get_settings_dict, save_settings

    save_settings(db, {"hacker_key": "x", "smtp_host": "smtp.163.com"})
    values = get_settings_dict(db)
    assert values["smtp_host"] == "smtp.163.com"
    assert "hacker_key" not in values
