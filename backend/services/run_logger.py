# -*- coding: utf-8 -*-
"""定时任务运行日志包装器。

统一在 scheduler 注册处包一层,任务函数本身零改动:
- 记录开始/结束时间、耗时、状态(success/partial/failed)、错误信息
- 捕获任务运行期间 loguru 输出的 INFO 以上日志,末尾若干行作为摘要落库

partial 判定约定: 任务函数返回 {"fail_count": n} 且 n>0 时记 partial。
返回 None(现有多数任务)视为 success。
"""
from __future__ import annotations

import functools
import threading
from datetime import datetime

from loguru import logger

from backend.models.data_status import TaskRunLog
from backend.models.database import SessionLocal

# 摘要保留的日志行数(取末尾)
_SUMMARY_TAIL_LINES = 15
_SUMMARY_MAX_CHARS = 2000

# loguru sink 按线程隔离: APScheduler 线程池并发时互不串扰
_local = threading.local()


def _make_sink(buffer: list[str]):
    def _sink(message) -> None:  # noqa: ANN001
        text = str(message).rstrip("\n")
        if text:
            buffer.append(text)

    return _sink


def run_with_logging(job_id: str, func) -> None:
    """执行任务并写入 TaskRunLog。任何情况下不向外抛异常(日志已记录)。"""
    started_at = datetime.now()
    buffer: list[str] = []
    sink_id = None
    status = "success"
    error: str | None = None

    try:
        sink_id = logger.add(_make_sink(buffer), level="INFO", enqueue=False)
    except Exception:  # pragma: no cover - loguru 配置异常不应影响任务
        sink_id = None

    try:
        result = func()
        if isinstance(result, dict):
            fail_count = result.get("fail_count", 0)
            if fail_count:
                status = "partial"
                error = f"板块内 {fail_count} 个标的失败,详见日志"
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"

    finally:
        if sink_id is not None:
            try:
                logger.remove(sink_id)
            except Exception:  # pragma: no cover
                pass

        finished_at = datetime.now()
        duration = (finished_at - started_at).total_seconds()

        summary_lines = buffer[-_SUMMARY_TAIL_LINES:]
        summary = "\n".join(summary_lines)[-_SUMMARY_MAX_CHARS:] or None

        try:
            db = SessionLocal()
            try:
                db.add(
                    TaskRunLog(
                        job_id=job_id,
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_sec=round(duration, 2),
                        status=status,
                        summary=summary,
                        error=error[:_SUMMARY_MAX_CHARS] if error else None,
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception as exc:  # pragma: no cover - 落库失败只打日志,不影响调度
            logger.error(f"[run_logger] {job_id} 运行记录写入失败: {exc}")


def logged_daily_job(job_id: str, func):
    """返回包装后的任务函数,供 scheduler.add_job 直接注册。"""

    @functools.wraps(func)
    def wrapper() -> None:
        run_with_logging(job_id, func)

    return wrapper
