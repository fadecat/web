# -*- coding: utf-8 -*-
"""日频任务真实事务回归测试(连真实库, 非 MagicMock 假绿)。

针对只读 review 的 P2-1: 原 test_task_results.py 用 MagicMock 断言
db.rollback.assert_called_once(), 只能证明「rollback 被调用」, 不能证明
未提交写真的被撤销、已提交写真的保留。本文件用共享连接的真实 SQLite 验证:

1. 部分失败路径下, 已 commit 的估值快照存活(不被 rollback 误删),
   失败的股息率不产生任何行 —— 即结果计数 {success:1, fail:1} 与库内状态一致。
2. 子流在「写入未提交即抛错」时, 任务的 db.rollback() 真的把未提交行撤销,
   而已提交的快照不受影响。

运行: pytest tests/test_task_transactions.py
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base
from backend.models.valuation import IndexDividendYield, IndexValuationSnapshot
from backend.tasks import valuation_tasks


def _shared_engine():
    """单连接内存 SQLite(StaticPool 让所有 session 共享同一库), 建全表。"""
    # 触发模型注册到 Base.metadata
    import backend.models.valuation  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _patch_valuation_run(monkeypatch, engine, *, dividend_fetch_raises=True,
                         dividend_save_uncommitted_raise=False):
    """把 valuation_tasks 的任务体接上真实库 + 受控 fetcher。

    dividend_fetch_raises: fetch_index_dividend_yield 抛错(走内层 except + rollback)。
    dividend_save_uncommitted_raise: save_dividend_yield 先 add 未提交行再抛错,
        用于验证 rollback 真的撤销未提交写。
    """
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: Session())
    # Batch B 后 valuation 任务已无 is_trading_day 门控(易方达历史同步每天跑)
    monkeypatch.setattr(
        valuation_tasks,
        "load_valuation_targets",
        lambda: [{
            "code": "399296",
            "name": "创成长",
            "index_detail_url": "detail",
            "index_dividend_yield_url": "dividend",
        }],
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_detail",
        lambda *_a, **_k: {
            "index_name": "创成长",
            "index_valuation_percentile_url": "percentile",
            "index_dividend_yield_url": "dividend",
        },
    )
    # 估值分位成功 -> save_valuation_snapshots 真实落库并提交(快照应存活)
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_valuation_percentile",
        lambda *_a, **_k: [{
            "trade_date": "2026-09-07",
            "metrics": {
                "PE(TTM)": {"current": 30.0, "percentiles": {}},
                "PB(LF)": {"current": 4.0, "percentiles": {}},
            },
        }],
    )
    monkeypatch.setattr(valuation_tasks, "save_dividend_yield_history",
                        lambda *_a, **_k: 0)
    monkeypatch.setattr(valuation_tasks, "fetch_cn_10y_bond_yield", lambda: [])
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_a, **_k: 0)

    if dividend_save_uncommitted_raise:
        # 先 add 未提交行, 再抛错(不 commit) -> 任务 db.rollback() 应撤销它
        def fake_save_dividend_yield(db, data):
            db.add(IndexDividendYield(
                index_code=data["index_code"],
                trade_date=date.fromisoformat(data["index_dividend_yield_date"]),
                dividend_yield=1.0,
            ))
            raise RuntimeError("dividend save boom")

        monkeypatch.setattr(valuation_tasks, "save_dividend_yield",
                            fake_save_dividend_yield)
        monkeypatch.setattr(
            valuation_tasks,
            "fetch_index_dividend_yield",
            lambda *_a, **_k: {
                "index_code": "399296",
                "index_dividend_yield_date": "2026-09-07",
                "index_dividend_yield": 1.5,
            },
        )
    elif dividend_fetch_raises:
        monkeypatch.setattr(
            valuation_tasks,
            "fetch_index_dividend_yield",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("dividend down")),
        )
        # save_dividend_yield 不会被调用(抛错在前), 占位避免误用真实写
        monkeypatch.setattr(valuation_tasks, "save_dividend_yield",
                            lambda *_a, **_k: None)


def test_partial_failure_keeps_committed_snapshot_and_writes_no_dividend(monkeypatch):
    """PE 成功(已提交) + 股息率拉取失败: 快照存活、股息率无行、计数 {1,1}。"""
    engine = _shared_engine()
    _patch_valuation_run(monkeypatch, engine, dividend_fetch_raises=True)

    result = valuation_tasks.run_valuation_daily()

    # Batch B 子流独立计数: PE 成功 + 国债成功 = 2, 股息失败 = 1
    assert result == {"success_count": 2, "fail_count": 1}
    # 真实回滚验证: 已提交的快照不能因 rollback 丢失
    sess = sessionmaker(bind=engine)()
    try:
        snap = sess.query(IndexValuationSnapshot).filter_by(
            index_code="399296").all()
        div = sess.query(IndexDividendYield).filter_by(
            index_code="399296").all()
    finally:
        sess.close()
    assert len(snap) == 1, "估值快照应已提交并存活(原 mock 测试无法证明此点)"
    assert len(div) == 0, "失败的股息率不应产生任何行"


def test_rollback_removes_uncommitted_dividend_but_keeps_committed_snapshot(monkeypatch):
    """save_dividend_yield 写入未提交行即抛错: rollback 真撤销未提交行, 快照仍在。"""
    engine = _shared_engine()
    _patch_valuation_run(monkeypatch, engine,
                         dividend_save_uncommitted_raise=True)

    result = valuation_tasks.run_valuation_daily()

    # Batch B 子流独立计数: PE 成功 + 国债成功 = 2, 股息失败 = 1
    assert result == {"success_count": 2, "fail_count": 1}
    sess = sessionmaker(bind=engine)()
    try:
        snap = sess.query(IndexValuationSnapshot).filter_by(
            index_code="399296").all()
        div = sess.query(IndexDividendYield).filter_by(
            index_code="399296").all()
    finally:
        sess.close()
    assert len(snap) == 1, "已提交快照不受 rollback 影响"
    assert len(div) == 0, "未提交股息率行必须被 rollback 撤销(真实 SQL)"


def test_bond_failure_marks_partial_without_losing_valuation_targets(monkeypatch):
    """8 标的全成功 + 国债失败: 整次判 partial, 但估值快照都已落库。"""
    engine = _shared_engine()
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: Session())
    # Batch B 后 valuation 任务已无 is_trading_day 门控(易方达历史同步每天跑)
    monkeypatch.setattr(
        valuation_tasks,
        "load_valuation_targets",
        lambda: [{
            "code": "399296",
            "name": "创成长",
            "index_detail_url": "detail",
            "index_dividend_yield_url": None,  # 无独立股息率, 跳过该子流
        }],
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_detail",
        lambda *_a, **_k: {"index_name": "创成长",
                           "index_valuation_percentile_url": "p"},
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_valuation_percentile",
        lambda *_a, **_k: [{
            "trade_date": "2026-09-07",
            "metrics": {"PE(TTM)": {"current": 30.0, "percentiles": {}}},
        }],
    )
    monkeypatch.setattr(valuation_tasks, "fetch_cn_10y_bond_yield",
                        lambda: (_ for _ in ()).throw(RuntimeError("bond down")))
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_a, **_k: 0)

    result = valuation_tasks.run_valuation_daily()

    # Batch B 子流独立计数: 国债失败 -> fail_count=1; PE 成功 -> success_count=1
    assert result == {"success_count": 1, "fail_count": 1}
    sess = sessionmaker(bind=engine)()
    try:
        snap = sess.query(IndexValuationSnapshot).filter_by(
            index_code="399296").all()
    finally:
        sess.close()
    assert len(snap) == 1, "国债失败不应牵连已提交的估值快照"
