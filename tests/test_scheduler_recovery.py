# -*- coding: utf-8 -*-
"""scheduler 启动回补与任务日历测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from backend import scheduler as scheduler_module
from backend.services import run_logger, style_rotation_store
from backend.services.fetchers import style_rotation as rotation_fetcher


def _capture_startup_check(monkeypatch):
    captured = {}

    def fake_add_job(func, **kwargs):
        captured["func"] = func
        captured["kwargs"] = kwargs

    monkeypatch.setattr(scheduler_module.scheduler, "add_job", fake_add_job)
    scheduler_module._maybe_backfill_style_rotation()
    return captured


def _summary(count=10):
    return {"count": count, "first": "2026-01-01", "last": "2026-09-07"}


def test_startup_backfill_checks_both_indices_inside_shared_lock(monkeypatch):
    """锁先于双指数判空；任一缺失都走 reserved runner。"""
    db = MagicMock()
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        style_rotation_store,
        "get_index_data_summary",
        lambda _db, code: _summary() if code == rotation_fetcher.LEFT_SYMBOL else None,
    )

    events = []
    monkeypatch.setattr(run_logger, "reserve_job", lambda job_id: events.append(("reserve", job_id)) or True)
    monkeypatch.setattr(run_logger, "release_job", lambda job_id: events.append(("release", job_id)))

    def fake_runner(job_id, func, *, reserved, trigger):
        events.append(("run", job_id, func, reserved, trigger))
        # 真实 runner 在 finally 释放预留锁
        run_logger.release_job(job_id)
        return {"status": "success"}

    monkeypatch.setattr(run_logger, "run_with_logging", fake_runner)
    captured = _capture_startup_check(monkeypatch)
    captured["func"]()

    assert captured["kwargs"]["id"] == "style_rotation_backfill_check"
    assert events == [
        ("reserve", "style_rotation_daily"),
        (
            "run",
            "style_rotation_daily",
            scheduler_module.run_style_rotation_backfill,
            True,
            "startup_backfill",
        ),
        ("release", "style_rotation_daily"),
    ]
    db.close.assert_called_once()


def test_startup_backfill_releases_lock_when_both_indices_exist(monkeypatch):
    """两只都有数据时不建运行记录，并由检查逻辑释放锁。"""
    db = MagicMock()
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(style_rotation_store, "get_index_data_summary", lambda *_args: _summary())

    events = []
    monkeypatch.setattr(run_logger, "reserve_job", lambda job_id: events.append(("reserve", job_id)) or True)
    monkeypatch.setattr(run_logger, "release_job", lambda job_id: events.append(("release", job_id)))
    monkeypatch.setattr(run_logger, "run_with_logging", lambda *_args, **_kwargs: events.append(("run",)))

    captured = _capture_startup_check(monkeypatch)
    captured["func"]()

    assert events == [("reserve", "style_rotation_daily"), ("release", "style_rotation_daily")]
    db.close.assert_called_once()


def test_startup_backfill_stops_before_database_check_when_lock_busy(monkeypatch):
    """已有同任务在运行时，不查库、不回补。"""
    db = MagicMock()
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(run_logger, "reserve_job", lambda _job_id: False)
    monkeypatch.setattr(run_logger, "run_with_logging", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应运行")))

    captured = _capture_startup_check(monkeypatch)
    captured["func"]()

    db.close.assert_not_called()


def test_historical_sync_jobs_are_scheduled_every_day(monkeypatch):
    """易方达历史同步包含周末，腾讯/转债当日任务仍限定周一至周五。"""
    calls = []
    monkeypatch.setattr(
        scheduler_module.scheduler,
        "add_job",
        lambda func, **kwargs: calls.append(kwargs),
    )
    scheduler_module._register_daily_jobs()

    by_id = {c["id"]: c for c in calls}
    assert str(by_id["valuation_daily"]["trigger"]).startswith("cron[day_of_week='*'")
    assert str(by_id["index_eod_daily"]["trigger"]).startswith("cron[day_of_week='*'")
    assert "day_of_week='mon-fri'" in str(by_id["style_rotation_daily"]["trigger"])
    assert "day_of_week='mon-fri'" in str(by_id["cb_list_daily"]["trigger"])
