# -*- coding: utf-8 -*-
"""全市场正股财务快照：低峰窗口两晚均匀铺开、可恢复行业分片、成功后原子发布。

进度规划: PLAN_TOTAL_NIGHTS 晚跑完全部行业分片, 每晚配额
ceil(剩余/剩余夜数), 分片间隔按「窗口剩余时间 / 今晚剩余配额」动态均摊,
在 02:10 触发后均匀铺到 04:50 硬停(窗口末 10 分钟安全余量);
夜数耗尽仍有剩余时兜底「一晚跑完剩余全部」, 不会饿死。
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import monotonic, sleep
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
HARD_STOP = time(4, 50)  # 到点即停, 给窗口末留 10 分钟余量
STATE_FILE = DATA_DIR / "state" / "stock_financial_monthly.json"
PLAN_TOTAL_NIGHTS = 2  # 全量计划夜数(旧状态迁移时也按此补默认)

def in_monthly_window(now: datetime | None = None) -> bool:
    return WINDOW_START <= (now or datetime.now(_CN)).astimezone(_CN).time() < WINDOW_END

def next_monthly_window(created_at: datetime | None = None) -> datetime:
    now = (created_at or datetime.now(_CN)).astimezone(_CN)
    return datetime.combine(now.date() + timedelta(days=1), time(2, 10), tzinfo=_CN)

def initialize_stock_financial_bootstrap(created_at: datetime | None = None, state_path: Path = STATE_FILE) -> dict:
    """创建首次任务预约；9 月 15 日创建即预约 9 月 16 日 02:10。"""
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    scheduled = next_monthly_window(created_at)
    payload = {
        "target_month": scheduled.strftime("%Y-%m"), "status": "SCHEDULED",
        "scheduled_for": scheduled.isoformat(), "partitions": [], "completed": [],
        "rows": {}, "request_count": 0, "failed_queries": 0,
        "total_nights": PLAN_TOTAL_NIGHTS, "run_dates": [],
    }
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


def _state_for_month(state: dict, now: datetime, path: Path) -> dict:
    """状态文件只承载当前月游标；历史 SUCCESS 批次永久留在数据库。"""
    target = now.strftime("%Y-%m")
    if state.get("target_month") == target and state.get("status") != "SUCCESS":
        return state
    if state.get("target_month") == target and state.get("status") == "SUCCESS":
        return state
    # 跨月创建新的周期任务，立即允许当前凌晨窗口执行。
    fresh = {"target_month": target, "status": "SCHEDULED", "scheduled_for": now.replace(hour=2, minute=10, second=0, microsecond=0).isoformat(), "partitions": [], "completed": [], "rows": {}, "request_count": 0, "failed_queries": 0}
    _save_state(fresh, path)
    return fresh


def _ensure_plan_fields(state: dict, path: Path) -> dict:
    """旧状态迁移: 补两晚计划字段(已落库的进度原样保留)。"""
    changed = False
    if "total_nights" not in state:
        state["total_nights"] = PLAN_TOTAL_NIGHTS
        changed = True
    if "run_dates" not in state:
        state["run_dates"] = []
        changed = True
    if changed:
        _save_state(state, path)
    return state


def _nights_quota(state: dict, today: str, remaining: int) -> int:
    """今晚配额 = ceil(剩余分片 / 剩余夜数); 夜数耗尽兜底一晚跑完, 不会饿死。"""
    nights_used = sum(1 for d in state.get("run_dates") or [] if d < today)
    nights_left = max(1, int(state.get("total_nights") or PLAN_TOTAL_NIGHTS) - nights_used)
    return max(1, min(remaining, math.ceil(remaining / nights_left)))


def _even_pace_sleep(quota_left: int, work_sec: float) -> None:
    """匀速铺开: 把今晚剩余配额均摊到 04:50 前的剩余时间上。

    每片 sleep = 剩余时间/剩余配额 - 本片实际耗时 —— 按实时进度重新配平,
    中途有慢片(市值二分)或补跑偏差都会被后续间隔自动吸收。
    """
    if quota_left <= 0:
        return
    now = datetime.now(_CN)
    stop_at = datetime.combine(now.date(), HARD_STOP, tzinfo=_CN)
    budget = (stop_at - now).total_seconds() / quota_left
    if budget > 0:
        sleep(max(0.0, budget - work_sec))

def _partition_nodes(tree: list[dict]) -> list[dict]:
    leaves = [n for n in tree if int(n.get("level") or 0) == 3]
    return leaves or [n for n in tree if int(n.get("level") or 0) == 1]

def _majority_trade_date(dates: list[str]) -> str | None:
    valid = [d for d in dates if d]
    return Counter(valid).most_common(1)[0][0] if valid else None

def run_stock_financial_monthly(*, force: bool = False, state_path: Path = STATE_FILE) -> dict:
    now = datetime.now(_CN)
    state = _ensure_plan_fields(
        _state_for_month(_load_state(state_path), now, state_path), state_path
    )
    if not force and state.get("status") == "SUCCESS":
        return {"status": "already_done", "success_count": 0, "fail_count": 0}
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
    # 今晚的日期计入已跑夜数(先计后跑, 中断也消耗一个窗口)
    today = now.date().isoformat()
    if today not in (state.get("run_dates") or []):
        state.setdefault("run_dates", []).append(today)
        _save_state(state, state_path)
    done = set(state.get("completed") or [])
    quota = _nights_quota(state, today, len(state["partitions"]) - len(done))
    processed = 0
    trade_dates: list[str] = []
    try:
        for val in [v for v in state["partitions"] if v not in done]:
            if not force and processed >= quota:
                break
            if not force and datetime.now(_CN).time() >= HARD_STOP:
                break
            t0 = monotonic()
            # 行业树页面使用一次 cookie；数据分片不传 cookie，让网关按账号池自主选择账号。
            part = fetch_dividend_snapshot(None, [{"val": val, "level": 1, "cnts": 0, "nm": val}], min_total_value=0)
            meta, rows = part.get("meta") or {}, part.get("rows") or []
            state["request_count"] = int(state.get("request_count") or 0) + int(meta.get("request_count") or 0)
            state["failed_queries"] = int(state.get("failed_queries") or 0) + int(meta.get("failed_queries") or 0)
            if int(meta.get("failed_queries") or 0):
                _save_state(state, state_path)
                processed += 1
                if not force:
                    _even_pace_sleep(quota - processed, monotonic() - t0)
                continue
            for row in rows:
                sid = str(row.get("stock_id") or "").strip()
                if sid:
                    state.setdefault("rows", {})[sid] = row
            if meta.get("trade_date"):
                trade_dates.append(str(meta["trade_date"]))
            done.add(val)
            state["completed"] = sorted(done)
            _save_state(state, state_path)
            processed += 1
            if not force:
                _even_pace_sleep(quota - processed, monotonic() - t0)
        if len(done) < len(state["partitions"]):
            state["status"] = "PAUSED_WINDOW_END"
            _save_state(state, state_path)
            return {"status": state["status"], "success_count": len(state.get("rows", {})), "remaining": len(state["partitions"]) - len(done), "fail_count": 0}
        rows = list(state.get("rows", {}).values())
        trade = _majority_trade_date(trade_dates) or _majority_trade_date([str(r.get("last_dt") or "") for r in rows])
        source_date = date.fromisoformat(trade) if trade else None
        if len(rows) < 5200:
            raise ValueError(f"全市场财务快照数量不足: {len(rows)}")
        db = SessionLocal()
        try:
            batch = save_stock_financial_snapshot(db, rows, snapshot_month=(source_date or now.date()).strftime("%Y-%m"), source_trade_date=source_date, request_count=int(state.get("request_count") or 0), failed_queries=int(state.get("failed_queries") or 0), min_count=5200)
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
