# -*- coding: utf-8 -*-
"""数据状态路由: 各数据集新鲜度 + 定时任务运行记录 + 手动触发。"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.data_status import build_data_status
from backend.services.run_logger import run_with_logging, reserve_job, release_job
from backend.tasks.registry import JOB_FUNCS

router = APIRouter(prefix="/data-status")

# 定时、手动触发共享 runner 的同一任务锁（仅单进程部署）。


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

    if not reserve_job(job_id):
        raise HTTPException(status_code=409, detail="该任务正在运行中")

    def _worker():
        run_with_logging(job_id, func, reserved=True, trigger="manual")

    try:
        threading.Thread(target=_worker, name=f"manual-{job_id}", daemon=True).start()
    except Exception:
        release_job(job_id)
        raise
    return {"status": "started", "job_id": job_id}
