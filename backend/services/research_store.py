# -*- coding: utf-8 -*-
"""研究回放存储层: 名单/日历/配对快照发布/修订记录/配对 bars 加载/回放物化。

设计对应 docs/superpowers/specs/2026-09-18-next-day-t-research-replay-design.md §4:
- raw/hfq 配对抓取只在两边完整且日期匹配后发布为 USABLE 快照; 拒绝日期错位与部分成功;
- 供应商修订写 research_data_revision(完整旧/新载荷, 非仅哈希), 不覆盖旧观察;
- 同侧四价来自同一次配对快照。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.research import (
    ResearchCorporateEvent,
    ResearchDailyBarAdjusted,
    ResearchDailyBarRaw,
    ResearchDataRevision,
    ResearchDataSnapshot,
    ResearchReplayDay,
    ResearchReplayRun,
    ResearchSecurity,
    ResearchTradeCalendar,
)
from backend.services.market_data import AdjustMode, CorporateEvent, DailyBar, TradeSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 标的名单与交易日历
# ---------------------------------------------------------------------------

_TYPE_TO_SECURITY_TYPE = {"stock": "STOCK", "etf": "ETF"}
_EXCHANGE_BY_SUFFIX = {"SH": "SSE", "SZ": "SZSE"}


def _target_exchange(symbol: str) -> str:
    suffix = symbol.rsplit(".", 1)[-1]
    exchange = _EXCHANGE_BY_SUFFIX.get(suffix)
    if exchange is None:
        raise ValueError(f"无法识别的交易所后缀: {symbol}")
    return exchange


def upsert_securities(db: Session, targets: Sequence[dict[str, Any]]) -> int:
    """按 yaml 清单 upsert research_security; 返回新增条数。"""
    added = 0
    for target in targets:
        symbol = str(target["symbol"]).strip().upper()
        security_type = _TYPE_TO_SECURITY_TYPE.get(str(target.get("type", "stock")).strip().lower())
        if security_type is None:
            raise ValueError(f"未知研究标的类型: {target.get('type')!r}({symbol})")
        existing = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == symbol))
        if existing is None:
            db.add(ResearchSecurity(
                symbol=symbol,
                name=str(target.get("name") or symbol),
                security_type=security_type,
                exchange=_target_exchange(symbol),
                source=str(target.get("source") or "akshare").strip().lower(),
                selection_list=str(target.get("selection_list") or "手动ETF"),
                enabled=True,
            ))
            added += 1
        else:
            existing.name = str(target.get("name") or existing.name)
            existing.security_type = security_type
            existing.source = str(target.get("source") or existing.source).strip().lower()
            existing.selection_list = str(target.get("selection_list") or existing.selection_list)
    db.commit()  # 任务用独立会话逐标的同步, 名单落库须立即可见
    return added


def upsert_trade_calendar(
    db: Session, sessions: Sequence[TradeSession], source: str, now: datetime | None = None,
) -> int:
    """版本化 upsert 交易日历(同一日期覆盖最新观察, version 记录抓取批次)。"""
    observed_at = now or _utcnow()
    version = observed_at.isoformat()
    added = 0
    for session in sessions:
        existing = db.scalar(
            select(ResearchTradeCalendar).where(
                ResearchTradeCalendar.exchange == "SSE",
                ResearchTradeCalendar.trade_date == session.trade_date,
                ResearchTradeCalendar.source == source,
            )
        )
        if existing is None:
            db.add(ResearchTradeCalendar(
                exchange="SSE", trade_date=session.trade_date,
                is_open=session.is_open, source=source, version=version,
            ))
            added += 1
        elif existing.is_open != session.is_open:
            existing.is_open = session.is_open
            existing.version = version
    db.flush()
    return added


def load_calendar(db: Session, start: date, end: date) -> list[tuple[date, bool]]:
    """读取日历区间(升序); 无数据的日期不在结果中(调用方据此判 CALENDAR_UNVERIFIED)。"""
    rows = db.execute(
        select(ResearchTradeCalendar.trade_date, ResearchTradeCalendar.is_open)
        .where(
            ResearchTradeCalendar.trade_date >= start,
            ResearchTradeCalendar.trade_date <= end,
        )
        .order_by(ResearchTradeCalendar.trade_date)
    ).all()
    return [(row[0], row[1]) for row in rows]


# ---------------------------------------------------------------------------
# 权益事件日历(腾讯换源后的主检测来源)
# ---------------------------------------------------------------------------


def upsert_corporate_events(
    db: Session,
    symbol: str,
    events: Sequence[CorporateEvent],
    *,
    source: str,
    now: datetime | None = None,
) -> int:
    """按 (symbol, event_date, source) upsert 权益事件; 事件日期为准, 因子覆盖更新。"""
    observed_at = now or _utcnow()
    added = 0
    for event in events:
        existing = db.scalar(
            select(ResearchCorporateEvent).where(
                ResearchCorporateEvent.symbol == symbol,
                ResearchCorporateEvent.event_date == event.event_date,
                ResearchCorporateEvent.source == source,
            )
        )
        if existing is None:
            db.add(ResearchCorporateEvent(
                symbol=symbol, event_date=event.event_date,
                factor=event.factor, cumulative_dividend=event.cumulative_dividend,
                source=source, fetched_at=observed_at,
            ))
            added += 1
        else:
            existing.factor = event.factor
            existing.cumulative_dividend = event.cumulative_dividend
            existing.fetched_at = observed_at
    db.flush()
    return added


def load_corporate_event_dates(db: Session, symbol: str) -> set[date]:
    """读取标的全部权益事件日期(计划/回放据此做事件停用与评价日排除)。"""
    return set(db.execute(
        select(ResearchCorporateEvent.event_date).where(ResearchCorporateEvent.symbol == symbol)
    ).scalars())


# ---------------------------------------------------------------------------
# 配对快照发布
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SyncResult:
    """单标的配对同步结果(run_logger 计数契约的粒度来源)。"""

    status: str  # "success" | "rejected" | "failed"
    inserted_rows: int = 0
    revised_rows: int = 0
    unchanged_rows: int = 0
    error: str | None = None


def _bar_payload(bar: DailyBar) -> dict[str, Any]:
    return {
        "trade_date": bar.trade_date.isoformat(),
        "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
        "volume": bar.volume, "amount": bar.amount,
        "volume_unit": bar.volume_unit, "source": bar.source,
    }


def _bars_hash(bars: Sequence[DailyBar]) -> str:
    """对规范化载荷做稳定内容哈希(排序后逐行序列化)。"""
    digest = hashlib.sha256()
    for bar in bars:
        digest.update(json.dumps(_bar_payload(bar), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _row_payload(row: ResearchDailyBarRaw | ResearchDailyBarAdjusted) -> dict[str, Any]:
    return {
        "trade_date": row.trade_date.isoformat(),
        "open": row.open, "high": row.high, "low": row.low, "close": row.close,
        "volume": row.volume, "amount": row.amount,
        "volume_unit": row.volume_unit, "source": row.source,
    }


def _upsert_bar_side(
    db: Session,
    model: type[ResearchDailyBarRaw] | type[ResearchDailyBarAdjusted],
    symbol: str,
    bars: Sequence[DailyBar],
    source: str,
    snapshot_id: int,
    *,
    adjust_mode: str | None,
    now: datetime,
) -> tuple[int, int, int]:
    """按内容哈希 upsert 单侧 bars; 返回 (inserted, revised, unchanged)。"""
    inserted = revised = unchanged = 0
    for bar in bars:
        conditions = [
            model.symbol == symbol,
            model.trade_date == bar.trade_date,
            model.source == source,
        ]
        if adjust_mode is not None:
            conditions.append(model.adjust_mode == adjust_mode)
        existing = db.scalar(select(model).where(*conditions))
        new_hash = hashlib.sha256(
            json.dumps(_bar_payload(bar), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if existing is None:
            kwargs: dict[str, Any] = {
                "symbol": symbol, "trade_date": bar.trade_date,
                "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
                "volume": bar.volume, "amount": bar.amount,
                "volume_unit": bar.volume_unit, "source": source,
                "snapshot_id": snapshot_id, "content_hash": new_hash,
            }
            if adjust_mode is not None:
                kwargs["adjust_mode"] = adjust_mode
            db.add(model(**kwargs))
            inserted += 1
        elif existing.content_hash != new_hash:
            # 供应商修订: 先留痕旧载荷, 再更新行(修订行 append-only)。
            old_payload = _row_payload(existing)
            business_key = f"{symbol}|{bar.trade_date.isoformat()}|{adjust_mode or 'RAW'}"
            db.add(ResearchDataRevision(
                table_name=model.__tablename__,
                business_key=business_key,
                old_hash=existing.content_hash,
                new_hash=new_hash,
                old_payload=json.dumps(old_payload, ensure_ascii=False, sort_keys=True),
                new_payload=json.dumps(_bar_payload(bar), ensure_ascii=False, sort_keys=True),
                first_observed_at=now,
                last_observed_at=now,
                snapshot_id=snapshot_id,
            ))
            existing.open, existing.high = bar.open, bar.high
            existing.low, existing.close = bar.low, bar.close
            existing.volume, existing.amount = bar.volume, bar.amount
            existing.snapshot_id = snapshot_id
            existing.content_hash = new_hash
            revised += 1
        else:
            unchanged += 1
    return inserted, revised, unchanged


def publish_paired_snapshot(
    db: Session,
    symbol: str,
    raw_rows: Sequence[DailyBar],
    hfq_rows: Sequence[DailyBar],
    *,
    source: str,
    request_start: date,
    request_end: date,
    now: datetime | None = None,
) -> SyncResult:
    """配对发布: 两边完整且日期匹配才置 USABLE 并写 bars; 否则 REJECTED 且零 bar 写入。"""
    observed_at = now or _utcnow()
    raw_dates = {bar.trade_date for bar in raw_rows}
    hfq_dates = {bar.trade_date for bar in hfq_rows}
    dates_match = bool(raw_rows) and bool(hfq_rows) and raw_dates == hfq_dates

    reject_reason: str | None = None
    if not raw_rows or not hfq_rows:
        reject_reason = f"raw_rows={len(raw_rows)}, hfq_rows={len(hfq_rows)}(单侧为空)"
    elif not dates_match:
        only_raw = sorted(raw_dates - hfq_dates)
        only_hfq = sorted(hfq_dates - raw_dates)
        reject_reason = (
            f"日期错位: 仅raw {len(only_raw)} 天(如 {only_raw[:3]}), "
            f"仅hfq {len(only_hfq)} 天(如 {only_hfq[:3]})"
        )

    snapshot = ResearchDataSnapshot(
        symbol=symbol, source=source, fetched_at=observed_at,
        request_start=request_start, request_end=request_end,
        raw_rows=len(raw_rows), hfq_rows=len(hfq_rows),
        raw_hash=_bars_hash(raw_rows), hfq_hash=_bars_hash(hfq_rows),
        dates_match=dates_match,
        status="REJECTED" if reject_reason else "USABLE",
        reject_reason=reject_reason,
        first_date=min(raw_dates) if raw_dates else None,
        last_date=max(raw_dates) if raw_dates else None,
    )
    db.add(snapshot)
    db.flush()  # 拿到 snapshot.id

    if reject_reason:
        db.commit()
        return SyncResult(status="rejected", error=reject_reason)

    raw_inserted, raw_revised, raw_unchanged = _upsert_bar_side(
        db, ResearchDailyBarRaw, symbol, raw_rows, source, snapshot.id,
        adjust_mode=None, now=observed_at,
    )
    hfq_inserted, hfq_revised, hfq_unchanged = _upsert_bar_side(
        db, ResearchDailyBarAdjusted, symbol, hfq_rows, source, snapshot.id,
        adjust_mode="HFQ", now=observed_at,
    )
    db.commit()
    return SyncResult(
        status="success",
        inserted_rows=raw_inserted + hfq_inserted,
        revised_rows=raw_revised + hfq_revised,
        unchanged_rows=raw_unchanged + hfq_unchanged,
    )


def latest_usable_snapshot(db: Session, symbol: str) -> ResearchDataSnapshot | None:
    """最近一次 USABLE 快照(回放的数据基线)。"""
    return db.scalar(
        select(ResearchDataSnapshot)
        .where(
            ResearchDataSnapshot.symbol == symbol,
            ResearchDataSnapshot.status == "USABLE",
        )
        .order_by(ResearchDataSnapshot.fetched_at.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------
# 配对 bars 加载(计划核心/回放的统一输入)
# ---------------------------------------------------------------------------

def load_paired_bars(db: Session, symbol: str, *, end_date: date) -> list[dict[str, Any]]:
    """加载 raw/hfq 配对 bars(同日两侧都存在), 升序。

    返回 dict 列表(trade_date + raw_*/hfq_* 字段), 供构造 BarInput;
    不含任何一侧缺失的日期(配对缺口由 data-health 报告)。
    """
    raw_rows = {
        row.trade_date: row
        for row in db.execute(
            select(ResearchDailyBarRaw).where(
                ResearchDailyBarRaw.symbol == symbol,
                ResearchDailyBarRaw.trade_date <= end_date,
            )
        ).scalars()
    }
    hfq_rows = {
        row.trade_date: row
        for row in db.execute(
            select(ResearchDailyBarAdjusted).where(
                ResearchDailyBarAdjusted.symbol == symbol,
                ResearchDailyBarAdjusted.trade_date <= end_date,
            )
        ).scalars()
    }
    paired: list[dict[str, Any]] = []
    for trade_date in sorted(raw_dates := (set(raw_rows) & set(hfq_rows))):
        raw, hfq = raw_rows[trade_date], hfq_rows[trade_date]
        paired.append({
            "trade_date": trade_date,
            "raw_open": raw.open, "raw_high": raw.high,
            "raw_low": raw.low, "raw_close": raw.close,
            "hfq_open": hfq.open, "hfq_high": hfq.high,
            "hfq_low": hfq.low, "hfq_close": hfq.close,
        })
    return paired


# ---------------------------------------------------------------------------
# 数据健康度
# ---------------------------------------------------------------------------

def data_health(db: Session) -> list[dict[str, Any]]:
    """逐 symbol raw/hfq 条数、配对缺失数、USABLE 快照与日历覆盖范围。"""
    securities = db.scalars(
        select(ResearchSecurity).order_by(ResearchSecurity.symbol)
    ).all()
    report: list[dict[str, Any]] = []
    for security in securities:
        raw_count = db.scalar(
            select(func.count()).select_from(ResearchDailyBarRaw).where(ResearchDailyBarRaw.symbol == security.symbol)
        ) or 0
        hfq_count = db.scalar(
            select(func.count()).select_from(ResearchDailyBarAdjusted).where(ResearchDailyBarAdjusted.symbol == security.symbol)
        ) or 0
        # 配对缺失数: 两侧日期对称差的大小
        raw_dates = set(db.execute(
            select(ResearchDailyBarRaw.trade_date).where(ResearchDailyBarRaw.symbol == security.symbol)
        ).scalars())
        hfq_dates = set(db.execute(
            select(ResearchDailyBarAdjusted.trade_date).where(ResearchDailyBarAdjusted.symbol == security.symbol)
        ).scalars())
        snapshot = latest_usable_snapshot(db, security.symbol)
        report.append({
            "symbol": security.symbol,
            "name": security.name,
            "security_type": security.security_type,
            "raw_rows": raw_count,
            "hfq_rows": hfq_count,
            "unpaired_dates": len(raw_dates ^ hfq_dates),
            "usable_snapshot_at": snapshot.fetched_at.isoformat() + "Z" if snapshot else None,
            "usable_snapshot_last_date": snapshot.last_date.isoformat() if snapshot and snapshot.last_date else None,
        })
    calendar_range = db.execute(
        select(func.min(ResearchTradeCalendar.trade_date), func.max(ResearchTradeCalendar.trade_date))
    ).one()
    calendar_rows = db.scalar(select(func.count()).select_from(ResearchTradeCalendar)) or 0
    return [
        {**item, "calendar_start": calendar_range[0].isoformat() if calendar_range[0] else None,
         "calendar_end": calendar_range[1].isoformat() if calendar_range[1] else None,
         "calendar_rows": calendar_rows}
        for item in report
    ] if report else []


# ---------------------------------------------------------------------------
# 回放物化(run + days)
# ---------------------------------------------------------------------------

def replace_replay_run(
    db: Session,
    *,
    symbol: str,
    param_lambda: float,
    quantile_window: int,
    algorithm_version: str,
    start_date: date,
    end_date: date,
    train_end_date: date | None,
    raw_snapshot_id: int | None,
    hfq_snapshot_id: int | None,
    input_hash: str,
    day_rows: Sequence[dict[str, Any]],
) -> int:
    """幂等物化: 同一唯一键的旧 run 整体删除重建; 返回 run_id。"""
    existing = db.scalar(
        select(ResearchReplayRun).where(
            ResearchReplayRun.symbol == symbol,
            ResearchReplayRun.param_lambda == param_lambda,
            ResearchReplayRun.quantile_window == quantile_window,
            ResearchReplayRun.algorithm_version == algorithm_version,
            ResearchReplayRun.start_date == start_date,
            ResearchReplayRun.end_date == end_date,
        )
    )
    if existing is not None:
        db.execute(ResearchReplayDay.__table__.delete().where(ResearchReplayDay.run_id == existing.id))
        db.delete(existing)
        db.flush()
    train_days = sum(1 for row in day_rows if train_end_date is not None and row["plan_date"] <= train_end_date)
    validation_days = len(day_rows) - train_days
    run = ResearchReplayRun(
        symbol=symbol, param_lambda=param_lambda, quantile_window=quantile_window,
        algorithm_version=algorithm_version, start_date=start_date, end_date=end_date,
        train_end_date=train_end_date, train_days=train_days, validation_days=validation_days,
        raw_snapshot_id=raw_snapshot_id, hfq_snapshot_id=hfq_snapshot_id,
        input_hash=input_hash, status="DONE",
    )
    db.add(run)
    db.flush()
    for row in day_rows:
        db.add(ResearchReplayDay(run_id=run.id, **row))
    db.commit()
    return run.id
