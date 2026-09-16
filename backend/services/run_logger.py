"""Single-process task runner; persistent execution state, shared manual/scheduled lock.

Uses existing task_run_log schema; no business-data migration. Deploy one scheduler
process only. Multi-process execution requires an external lease before enabling it.
"""
from __future__ import annotations

import functools
import threading
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger
from backend.models.data_status import TaskRunLog
from backend.models.database import SessionLocal

_guard = threading.Lock()
_running: set[str] = set()

_RESULT_SUMMARY_KEYS = (
    ("total", "total"),
    ("success_count", "success"),
    ("unchanged_count", "unchanged"),
    ("failed_count", "failed"),
    ("suspicious_count", "suspicious"),
    ("inserted_rows", "inserted"),
    ("revised_rows", "revised"),
    ("percentile_rows", "percentile"),
    ("state_persist_failed_count", "state_persist_failed_count"),
)


def _format_result_summary(result: dict) -> str:
    """Return a bounded, non-sensitive summary of known task counters."""
    fields = [
        f"{label}={result[key]}"
        for key, label in _RESULT_SUMMARY_KEYS
        if key in result
    ]
    codes = result.get("state_persist_failed_codes")
    if isinstance(codes, (list, tuple)):
        safe_codes = [str(code)[:32] for code in codes[:20]]
        fields.append(f"state_persist_failed_codes=[{','.join(safe_codes)}]")
    return "结果摘要: " + ", ".join(fields) if fields else ""


def now_local():
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def reserve_job(job_id: str) -> bool:
    with _guard:
        if job_id in _running:
            return False
        _running.add(job_id)
        return True


def release_job(job_id: str):
    with _guard:
        _running.discard(job_id)


def recover_interrupted_runs():
    """Call once BEFORE this process starts accepting jobs, never while workers run."""
    with SessionLocal() as db:
        rows = db.query(TaskRunLog).filter(TaskRunLog.status == "running").all()
        for row in rows:
            row.status = "interrupted"
            row.finished_at = now_local()
            row.error = "服务重启，前次执行未记录完成；未自动认定成功"
        db.commit()
        return len(rows)


def run_with_logging(job_id: str, func, *, reserved=False, trigger="scheduled"):
    if not reserved and not reserve_job(job_id):
        return {"status": "busy", "job_id": job_id}
    started_at = now_local()
    buffer = deque(maxlen=15)
    sink_id = None
    run_id = None
    status, error = "success", None
    try:
        # Fail closed: do not execute untracked work if the initial write fails.
        with SessionLocal() as db:
            row = TaskRunLog(job_id=job_id, started_at=started_at,
                             status="running", summary=f"触发方式: {trigger}")
            db.add(row)
            db.commit()
            run_id = row.id
        thread_id = threading.get_ident()
        sink_id = logger.add(lambda m: buffer.append(str(m).rstrip()), level="INFO",
                             filter=lambda r: r["thread"].id == thread_id)
        result = None
        try:
            result = func()
            if isinstance(result, dict):
                failures = result.get("fail_count", 0)
                if result.get("status") == "skipped":
                    status = "skipped"
                elif failures:
                    status = "partial" if result.get("success_count", 0) else "failed"
                    error = f"{failures} 个数据子项失败,详见日志"
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        finished = now_local()
        with SessionLocal() as db:
            row = db.get(TaskRunLog, run_id)
            row.status = status
            row.finished_at = finished
            row.duration_sec = round((finished - started_at).total_seconds(), 2)
            result_summary = _format_result_summary(result) if isinstance(result, dict) else ""
            summary_parts = [f"触发方式: {trigger}", "\n".join(buffer)]
            if result_summary:
                summary_parts.append(result_summary)
            row.summary = "\n".join(part for part in summary_parts if part)[-2000:]
            row.error = error[:2000] if error else None
            db.commit()

        # 任务失败主动通知(自吞异常, 不拖累采集与运行记录)
        if status in ("failed", "partial"):
            from backend.services.notifications import notify_task_failure

            notify_task_failure(job_id, status, error)

        return {"status": status, "job_id": job_id, "run_id": run_id}
    except Exception as exc:
        logger.error("任务 {} 执行记录失败: {}", job_id, exc)
        return {"status": "failed", "job_id": job_id, "run_id": run_id}
    finally:
        if sink_id is not None:
            logger.remove(sink_id)
        release_job(job_id)


def logged_daily_job(job_id: str, func):
    @functools.wraps(func)
    def wrapper():
        return run_with_logging(job_id, func)
    return wrapper
