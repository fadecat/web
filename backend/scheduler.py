# -*- coding: utf-8 -*-
"""定时任务编排。

职责: 在收盘后触发各板块数据抓取并落库。
与抓取层解耦 —— 本层决定「何时」,fetchers 层决定「如何」。
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from backend.tasks.valuation_tasks import run_valuation_daily
from backend.tasks.style_rotation_tasks import run_style_rotation_daily
from backend.tasks.cb_index_tasks import run_cb_index_daily
from backend.tasks.cb_list_tasks import run_cb_list_daily

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def start_scheduler() -> None:
    """启动调度器并注册任务。

    估值板块: 交易日 15:30 抓取全量标的 → 落库。
    """
    if scheduler.running:
        return

    # 估值板块日频任务: 周一至周五 15:30
    scheduler.add_job(
        run_valuation_daily,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=30,
            timezone="Asia/Shanghai",
        ),
        id="valuation_daily",
        name="估值板块日频抓取",
        replace_existing=True,
    )
    # 风格轮动日频任务: 周一至周五 15:35(估值任务后5分钟)
    scheduler.add_job(
        run_style_rotation_daily,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=35,
            timezone="Asia/Shanghai",
        ),
        id="style_rotation_daily",
        name="风格轮动日频抓取",
        replace_existing=True,
    )
    # 可转债等权指数日频任务: 周一至周五 15:40
    scheduler.add_job(
        run_cb_index_daily,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=40,
            timezone="Asia/Shanghai",
        ),
        id="cb_index_daily",
        name="可转债等权指数日频抓取",
        replace_existing=True,
    )
    # 可转债全量快照日频任务: 周一至周五 15:45(等权指数任务后5分钟)
    scheduler.add_job(
        run_cb_list_daily,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=45,
            timezone="Asia/Shanghai",
        ),
        id="cb_list_daily",
        name="可转债全量快照抓取",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "scheduler started: valuation@15:30, style_rotation@15:35, "
        "cb_index@15:40, cb_list@15:45"
    )


def stop_scheduler() -> None:
    """停止调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
