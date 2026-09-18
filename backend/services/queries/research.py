# -*- coding: utf-8 -*-
"""研究回放(次日 T 价位研究 V1)查询层: 全量 list[dict], 不分页。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.research import (
    ResearchDailyBarAdjusted,
    ResearchDailyBarRaw,
    ResearchReplayDay,
    ResearchReplayRun,
    ResearchSecurity,
    ResearchTradeCalendar,
)
from backend.services import research_store
from backend.services.research_replay import build_summary


def list_securities(db: Session) -> list[dict[str, Any]]:
    """研究标的名单 + 基础覆盖信息。"""
    securities = db.scalars(
        select(ResearchSecurity).order_by(ResearchSecurity.symbol)
    ).all()
    result: list[dict[str, Any]] = []
    for security in securities:
        raw_count = len(db.execute(
            select(ResearchDailyBarRaw.trade_date).where(ResearchDailyBarRaw.symbol == security.symbol)
        ).scalars().all())
        hfq_count = len(db.execute(
            select(ResearchDailyBarAdjusted.trade_date).where(ResearchDailyBarAdjusted.symbol == security.symbol)
        ).scalars().all())
        first_date = db.execute(
            select(func.min(ResearchDailyBarRaw.trade_date))
            .where(ResearchDailyBarRaw.symbol == security.symbol)
        ).scalar()
        last_date = db.execute(
            select(func.max(ResearchDailyBarRaw.trade_date))
            .where(ResearchDailyBarRaw.symbol == security.symbol)
        ).scalar()
        snapshot = research_store.latest_usable_snapshot(db, security.symbol)
        result.append({
            "symbol": security.symbol,
            "name": security.name,
            "security_type": security.security_type,
            "exchange": security.exchange,
            "source": security.source,
            "selection_list": security.selection_list,
            "enabled": security.enabled,
            "raw_rows": raw_count,
            "hfq_rows": hfq_count,
            "first_date": first_date.isoformat() if first_date else None,
            "last_date": last_date.isoformat() if last_date else None,
            "usable_snapshot_at": snapshot.fetched_at.isoformat() + "Z" if snapshot else None,
            "data_ready": snapshot is not None,
        })
    return result


def list_data_health(db: Session) -> list[dict[str, Any]]:
    """逐 symbol 数据健康度 + 日历覆盖。"""
    return research_store.data_health(db)


def list_replays(db: Session, symbol: str | None = None) -> list[dict[str, Any]]:
    """回放 run 列表(含逐日类别计数摘要)。"""
    statement = select(ResearchReplayRun).order_by(
        ResearchReplayRun.created_at.desc(), ResearchReplayRun.id.desc()
    )
    if symbol:
        statement = statement.where(ResearchReplayRun.symbol == symbol)
    runs = db.scalars(statement).all()
    result: list[dict[str, Any]] = []
    for run in runs:
        category_counts: dict[str, int] = {}
        days = db.scalars(
            select(ResearchReplayDay.day_category).where(ResearchReplayDay.run_id == run.id)
        ).all()
        for category in days:
            category_counts[category] = category_counts.get(category, 0) + 1
        result.append({
            "id": run.id,
            "symbol": run.symbol,
            "param_lambda": run.param_lambda,
            "quantile_window": run.quantile_window,
            "algorithm_version": run.algorithm_version,
            "start_date": run.start_date.isoformat(),
            "end_date": run.end_date.isoformat(),
            "train_end_date": run.train_end_date.isoformat() if run.train_end_date else None,
            "train_days": run.train_days,
            "validation_days": run.validation_days,
            "status": run.status,
            "created_at": run.created_at.isoformat() + "Z",
            "day_categories": category_counts,
        })
    return result


def get_run(db: Session, run_id: int) -> ResearchReplayRun | None:
    return db.get(ResearchReplayRun, run_id)


def get_summary(db: Session, run_id: int) -> dict[str, Any] | None:
    run = get_run(db, run_id)
    if run is None:
        return None
    days = db.scalars(
        select(ResearchReplayDay).where(ResearchReplayDay.run_id == run_id)
        .order_by(ResearchReplayDay.plan_date)
    ).all()
    return build_summary(days, run, split_date=run.train_end_date)


def list_days(db: Session, run_id: int) -> list[dict[str, Any]] | None:
    """逐日明细全量(价位、次日 OHLC、原因码、命中标记、开盘失效线)。"""
    run = get_run(db, run_id)
    if run is None:
        return None
    days = db.scalars(
        select(ResearchReplayDay).where(ResearchReplayDay.run_id == run_id)
        .order_by(ResearchReplayDay.plan_date)
    ).all()
    result: list[dict[str, Any]] = []
    for day in days:
        result.append({
            "plan_date": day.plan_date.isoformat(),
            "eval_date": day.eval_date.isoformat() if day.eval_date else None,
            "status": day.status,
            "reason_codes": json.loads(day.reason_codes) if day.reason_codes else [],
            "z": day.z_value,
            "atr14": day.atr14,
            "ema20": day.ema20,
            "scale": day.scale,
            "buy_levels_raw": json.loads(day.buy_levels_raw) if day.buy_levels_raw else None,
            "sell_levels_raw": json.loads(day.sell_levels_raw) if day.sell_levels_raw else None,
            "next_open": day.next_open,
            "next_high": day.next_high,
            "next_low": day.next_low,
            "next_close": day.next_close,
            "buy_hits": json.loads(day.buy_hits) if day.buy_hits else None,
            "sell_hits": json.loads(day.sell_hits) if day.sell_hits else None,
            "buy_open_invalid": day.buy_open_invalid,
            "sell_open_invalid": day.sell_open_invalid,
            "day_category": day.day_category,
            "evidence": json.loads(day.evidence) if day.evidence else None,
        })
    return result


def replay_comparison(
    db: Session, symbol: str, start_date: str, end_date: str,
) -> list[dict[str, Any]]:
    """参数网格: 同一 symbol×区间下所有 run 的 train/validation 触达率并排。"""
    from datetime import date as date_type

    runs = db.scalars(
        select(ResearchReplayRun).where(
            ResearchReplayRun.symbol == symbol,
            ResearchReplayRun.start_date == date_type.fromisoformat(start_date),
            ResearchReplayRun.end_date == date_type.fromisoformat(end_date),
        ).order_by(
            ResearchReplayRun.param_lambda, ResearchReplayRun.quantile_window
        )
    ).all()
    result: list[dict[str, Any]] = []
    for run in runs:
        summary = get_summary(db, run.id)
        if summary is None:
            continue
        result.append({
            "run_id": run.id,
            "param_lambda": run.param_lambda,
            "quantile_window": run.quantile_window,
            "train": summary["train"],
            "validation": summary["validation"],
            "sample_coverage": {
                "train_days": run.train_days,
                "validation_days": run.validation_days,
            },
        })
    return result
