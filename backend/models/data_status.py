# -*- coding: utf-8 -*-
"""数据状态监控 ORM 模型。

TaskRunLog: 定时任务运行记录,每次调度执行写一行。
由 backend/services/run_logger.py 的统一包装器写入,任务函数本身零改动。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.database import Base


class TaskRunLog(Base):
    """定时任务运行日志(一行 = 一次任务执行)。

    status 三态:
    - success: 任务函数正常返回(或返回 fail_count=0)
    - partial: 任务函数返回 fail_count>0(板块内部分标的失败)
    - failed:  任务函数抛异常
    """

    __tablename__ = "task_run_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    job_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="任务ID,如 valuation_daily")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="开始时间(本地)")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="结束时间(本地)")
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True, comment="耗时(秒)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="success/partial/failed")
    summary: Mapped[str | None] = mapped_column(String(2000), nullable=True, comment="运行摘要(摘取日志末尾若干行)")
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True, comment="失败时的错误信息")

    __table_args__ = (
        Index("ix_task_run_job_started", "job_id", "started_at"),
    )
