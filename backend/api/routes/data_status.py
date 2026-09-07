# -*- coding: utf-8 -*-
"""数据状态路由: 各数据集新鲜度 + 定时任务运行记录 + 手动触发。"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.data_status import build_data_status
from backend.services.run_logger import run_with_logging
from backend.tasks.registry import JOB_FUNCS

router = APIRouter(prefix="/data-status")

# 手动触发并发防护: 同一任务同时只允许一个手动实例
# (调度器自身的定时触发不经过此锁, 但任务时间错峰 + 幂等落库, 冲突无害)
_manual_running: set[str] = set()
_lock = threading.Lock()


@router.get("")
def get_data_status(db: Session = Depends(get_db)) -> dict:
    """数据状态总览: 数据新鲜度逐集展示 + 任务最近运行/耗时/成功率。"""
    return build_data_status(db)


@router.post("/run/{job_id}")
def run_job_manually(job_id: str) -> dict:
    """手动触发指定任务: 后台线程执行, 立即返回。

    任务内部自带交易日判断(非交易日直接跳过并记一条日志)。
    运行记录照常写入 task_run_log, 状态页轮询可见。
    """
    func = JOB_FUNCS.get(job_id)
    if func is None:
        raise HTTPException(status_code=404, detail=f"未知任务: {job_id}")

    with _lock:
        if job_id in _manual_running:
            raise HTTPException(status_code=409, detail="该任务正在手动运行中")
        _manual_running.add(job_id)

    def _worker():
        try:
            run_with_logging(job_id, func)
        finally:
            with _lock:
                _manual_running.discard(job_id)

    threading.Thread(target=_worker, name=f"manual-{job_id}", daemon=True).start()
    return {"status": "started", "job_id": job_id}
