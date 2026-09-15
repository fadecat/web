# -*- coding: utf-8 -*-
"""全市场正股财务快照：低峰窗口、可恢复行业分片、成功后原子发布。"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import desc

from backend.config import DATA_DIR
from backend.models.database import SessionLocal
from backend.models.jisilu_stock import StockFinancialSnapshotBatch
from backend.services.fetchers.stock_dividend import fetch_dividend_snapshot, fetch_industry_tree
from backend.services.jisilu import get_cookie
from backend.services.stock_dividend_store import save_stock_financial_snapshot

_CN = ZoneInfo("Asia/Shanghai")
WINDOW_START, WINDOW_END = time(2, 0), time(5, 0)
STATE_FILE = DATA_DIR / "state" / "stock_financial_monthly.json"
MAX_PARTITIONS_PER_WINDOW = 24

def in_monthly_window(now: datetime | None = None) -> bool:
    return WINDOW_START <= (now or datetime.now(_CN)).astimezone(_CN).time() < WINDOW_END

def next_monthly_window(created_at: datetime | None = None) -> datetime:
    now = (created_at or datetime.now(_CN)).astimezone(_CN)
    return datetime.combine(now.date() + timedelta(days=1), time(2, 10), tzinfo=_CN)

def initialize_stock_financial_bootstrap(created_at: datetime | None = None, state_path: Path = STATE_FILE) -> dict:
    """创建首次任务预约；9 月 15 日创建即预约 9 月 16 日 02:10。"""
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    payload = {"status": "SCHEDULED", "scheduled_for": next_monthly_window(created_at).isoformat(), "partitions": [], "completed": [], "rows": {}}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload

def _save_state(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return initialize_stock_financial_bootstrap(state_path=path)

def _partition_nodes(tree: list[dict]) -> list[dict]:
    leaves = [n for n in tree if int(n.get("level") or 0) == 3]
    return leaves or [n for n in tree if int(n.get("level") or 0) == 1]

def run_stock_financial_monthly(*, force: bool = False, state_path: Path = STATE_FILE) -> dict:
    now = datetime.now(_CN)
    state = _load_state(state_path)
    if not force and not in_monthly_window(now):
        return {"status": "skipped_window", "success_count": 0, "fail_count": 0}
    scheduled = state.get("scheduled_for")
    if not force and scheduled and now < datetime.fromisoformat(scheduled):
        return {"status": "scheduled", "success_count": 0, "fail_count": 0, "scheduled_for": scheduled}
    if not state.get("partitions"):
        cookie = get_cookie()
        tree = fetch_industry_tree(cookie)
        state["partitions"] = [n["val"] for n in _partition_nodes(tree)]
        state["status"] = "RUNNING"
        _save_state(state, state_path)
    else:
        cookie = get_cookie()
    done = set(state.get("completed") or [])
    try:
        for val in [v for v in state["partitions"] if v not in done][:MAX_PARTITIONS_PER_WINDOW]:
            part = fetch_dividend_snapshot(cookie, [{"val": val, "level": 1, "cnts": 0, "nm": val}], min_total_value=0)
            meta, rows = part.get("meta") or {}, part.get("rows") or []
            if int(meta.get("failed_queries") or 0):
                continue
            for row in rows:
                sid = str(row.get("stock_id") or "").strip()
                if sid:
                    state.setdefault("rows", {})[sid] = row
            done.add(val)
            state["completed"] = sorted(done)
            _save_state(state, state_path)
        if len(done) < len(state["partitions"]):
            state["status"] = "PAUSED_WINDOW_END"
            _save_state(state, state_path)
            return {"status": state["status"], "success_count": len(state.get("rows", {})), "remaining": len(state["partitions"]) - len(done), "fail_count": 0}
        rows = list(state.get("rows", {}).values())
        trade = next((r.get("last_dt") for r in rows if r.get("last_dt")), None)
        source_date = date.fromisoformat(trade) if trade else None
        if len(rows) < 5200:
            raise ValueError(f"全市场财务快照数量不足: {len(rows)}")
        db = SessionLocal()
        try:
            batch = save_stock_financial_snapshot(db, rows, snapshot_month=(source_date or now.date()).strftime("%Y-%m"), source_trade_date=source_date, min_count=5200)
        finally:
            db.close()
        state["status"] = "SUCCESS"
        _save_state(state, state_path)
        return {"status": "success", "success_count": batch.actual_count, "fail_count": 0, "batch_id": batch.id}
    except Exception as exc:
        logger.error(f"全市场正股财务快照失败: {exc}")
        state["status"], state["error"] = "FAILED", str(exc)
        _save_state(state, state_path)
        return {"status": "failed", "success_count": 0, "fail_count": 1, "error": str(exc)}
