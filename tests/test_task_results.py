# -*- coding: utf-8 -*-
"""日频任务结构化结果契约测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.tasks import (
    cb_index_tasks,
    cb_list_tasks,
    cb_redeem_tasks,
    index_eod_tasks,
    style_rotation_tasks,
    valuation_tasks,
)


@pytest.mark.parametrize(
    "module,func_name",
    [
        (style_rotation_tasks, "run_style_rotation_daily"),
        (cb_list_tasks, "run_cb_list_daily"),
        (cb_index_tasks, "run_cb_index_daily"),
        (cb_redeem_tasks, "run_cb_redeem_daily"),
    ],
)
def test_market_snapshot_tasks_report_non_trading_day_as_skipped(monkeypatch, module, func_name):
    """当日快照任务非交易日必须 skipped，不能返回 None 被误记 success。"""
    monkeypatch.setattr(module, "is_trading_day", lambda _day: False)

    result = getattr(module, func_name)()

    assert result == {"status": "skipped", "success_count": 0, "fail_count": 0}


@pytest.mark.parametrize(
    "module,func_name,fetch_name",
    [
        (cb_list_tasks, "run_cb_list_daily", "fetch_cb_list"),
        (cb_index_tasks, "run_cb_index_daily", "fetch_cb_index_history"),
        (cb_redeem_tasks, "run_cb_redeem_daily", "fetch_redeem_list"),
    ],
)
def test_single_stream_fetch_failure_is_reported_failed(monkeypatch, module, func_name, fetch_name):
    """转债单流任务抓取失败必须返回 fail_count，不能吞异常后假绿。"""
    monkeypatch.setattr(module, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(module, fetch_name, lambda: (_ for _ in ()).throw(RuntimeError("source down")))

    result = getattr(module, func_name)()

    assert result == {"success_count": 0, "fail_count": 1}


def test_dividend_failure_marks_valuation_task_partial_and_rolls_back(monkeypatch):
    """PE成功但股息率失败属于 partial，失败后必须回滚 Session。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        valuation_tasks,
        "load_valuation_targets",
        lambda: [
            {
                "code": "399296",
                "name": "创成长",
                "index_detail_url": "detail",
                "index_dividend_yield_url": "dividend",
            }
        ],
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_detail",
        lambda *_args, **_kwargs: {
            "index_name": "创成长",
            "index_valuation_percentile_url": "percentile",
        },
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_valuation_percentile",
        lambda *_args, **_kwargs: [{"trade_date": "2026-09-07", "metrics": {}}],
    )
    monkeypatch.setattr(valuation_tasks, "save_valuation_snapshots", lambda *_args: 1)
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_dividend_yield",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dividend down")),
    )
    monkeypatch.setattr(valuation_tasks, "fetch_cn_10y_bond_yield", lambda: [])
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 0)

    result = valuation_tasks.run_valuation_daily()

    # PE 成功 + 股息率失败 + 国债成功 = 2 个成功子流 / 1 个失败子流
    assert result == {"success_count": 2, "fail_count": 1}
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_bond_success_counts_as_valuation_substream(monkeypatch):
    """无指数标的但国债成功时不能被误判为零成功/全失败。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(valuation_tasks, "load_valuation_targets", lambda: [])
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_cn_10y_bond_yield",
        lambda: [{"trade_date": "2026-09-07", "cn_10y_bond_yield": 1.8}],
    )
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 1)

    result = valuation_tasks.run_valuation_daily()

    assert result == {"success_count": 1, "fail_count": 0}
    db.close.assert_called_once()


@pytest.mark.parametrize(
    "module,func_name,fetch_name,save_name,sample",
    [
        (cb_list_tasks, "run_cb_list_daily", "fetch_cb_list", "save_cb_snapshots", [{}]),
        (cb_index_tasks, "run_cb_index_daily", "fetch_cb_index_history", "save_cb_index_records", [{"date": "2026-09-07"}]),
        (cb_redeem_tasks, "run_cb_redeem_daily", "fetch_redeem_list", "save_cb_redeem", [{}]),
    ],
)
def test_single_stream_database_failure_rolls_back_and_reports_failed(
    monkeypatch, module, func_name, fetch_name, save_name, sample
):
    """转债任务落库失败必须 rollback 并返回 failed 计数。"""
    db = MagicMock()
    monkeypatch.setattr(module, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, fetch_name, lambda: sample)
    monkeypatch.setattr(
        module,
        save_name,
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db failed")),
    )

    result = getattr(module, func_name)()

    assert result == {"success_count": 0, "fail_count": 1}
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_real_sqlite_rollback_clears_failed_transaction_and_allows_next_write(db):
    """真实 SQLite：失败事务 rollback 后，同一 Session 能继续写下一条。"""
    from datetime import date

    from sqlalchemy.exc import IntegrityError

    from backend.models.valuation import IndexValuationSnapshot

    # nullable=False 的 index_name 触发真实 SQL 约束失败
    db.add(
        IndexValuationSnapshot(
            index_code="BAD",
            index_name=None,
            trade_date=date(2026, 9, 7),
            pe=1.0,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(
        IndexValuationSnapshot(
            index_code="399296",
            index_name="创成长",
            trade_date=date(2026, 9, 7),
            pe=34.0,
        )
    )
    db.commit()

    rows = db.query(IndexValuationSnapshot).all()
    assert [(r.index_code, r.pe) for r in rows] == [("399296", 34.0)]


def test_historical_sync_jobs_run_without_trading_day_gate(monkeypatch):
    """易方达历史同步不应被周末挡住；返回日期由源数据决定。"""
    val_db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: val_db)
    monkeypatch.setattr(valuation_tasks, "load_valuation_targets", lambda: [])
    monkeypatch.setattr(valuation_tasks, "fetch_cn_10y_bond_yield", lambda: [])
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 0)
    assert valuation_tasks.run_valuation_daily() == {"success_count": 1, "fail_count": 0}

    eod_db = MagicMock()
    monkeypatch.setattr(index_eod_tasks, "SessionLocal", lambda: eod_db)
    monkeypatch.setattr(index_eod_tasks, "load_index_eod_targets", lambda: [])
    assert index_eod_tasks.run_index_eod_daily() == {"success_count": 0, "fail_count": 0}
