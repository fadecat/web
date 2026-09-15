# -*- coding: utf-8 -*-
"""全市场正股财务快照任务。

任务在北京时间 02:00-05:00 低峰窗口运行。首次初始化由创建时间决定，
默认安排到下一自然日凌晨；结果只有完整抓取并通过校验后才发布。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from backend.models.database import SessionLocal
from backend.models.jisilu_stock import StockFinancialSnapshotBatch
from sqlalchemy import desc
from backend.services.fetchers.stock_dividend import fetch_dividend_snapshot
from backend.services.stock_dividend_store import save_stock_financial_snapshot

_CN = ZoneInfo("Asia/Shanghai")
WINDOW_START = time(2, 0)
WINDOW_END = time(5, 0)


def in_monthly_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(_CN)
    local = now.astimezone(_CN).time()
    return WINDOW_START <= local < WINDOW_END


def next_monthly_window(created_at: datetime | None = None) -> datetime:
    """返回创建后的下一个凌晨窗口 02:10。"""
    now = (created_at or datetime.now(_CN)).astimezone(_CN)
    day = now.date() + timedelta(days=1)
    return datetime.combine(day, time(2, 10), tzinfo=_CN)


def run_stock_financial_monthly(*, force: bool = False) -> dict:
    """抓取一次全市场数据并原子发布；由 scheduler 在凌晨窗口调用。"""
    now = datetime.now(_CN)
    if not force and not in_monthly_window(now):
        return {"status": "skipped_window", "success_count": 0, "fail_count": 0}
    if not force:
        db = SessionLocal()
        try:
            current_month = now.strftime("%Y-%m")
            existing = db.query(StockFinancialSnapshotBatch).filter(
                StockFinancialSnapshotBatch.snapshot_month == current_month,
                StockFinancialSnapshotBatch.status == "SUCCESS",
            ).order_by(desc(StockFinancialSnapshotBatch.published_at)).first()
        finally:
            db.close()
        if existing:
            return {"status": "already_published", "success_count": existing.actual_count, "fail_count": 0, "batch_id": existing.id}
    try:
        snapshot = fetch_dividend_snapshot(min_total_value=0)
        meta = snapshot.get("meta") or {}
        rows = snapshot.get("rows") or []
        trade = meta.get("trade_date")
        source_date = date.fromisoformat(trade) if trade else None
        if not rows:
            raise ValueError("全市场财务快照返回空数据")
        # 全市场的保守保护线；后续按实际成功批次调整。
        if len({str(r.get("stock_id") or "").strip() for r in rows}) < 5200:
            raise ValueError(f"全市场财务快照数量不足: {len(rows)}")
        db = SessionLocal()
        try:
            batch = save_stock_financial_snapshot(
                db, rows, snapshot_month=(source_date or now.date()).strftime("%Y-%m"),
                source_trade_date=source_date,
                request_count=int(meta.get("request_count") or 0),
                failed_queries=int(meta.get("failed_queries") or 0), min_count=5200,
            )
        finally:
            db.close()
        logger.info(f"全市场正股财务快照发布: batch={batch.id}, count={batch.actual_count}")
        return {"status": "success", "success_count": batch.actual_count, "fail_count": 0, "batch_id": batch.id}
    except Exception as exc:
        logger.error(f"全市场正股财务快照失败: {exc}")
        return {"status": "failed", "success_count": 0, "fail_count": 1, "error": str(exc)}
