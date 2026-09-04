# -*- coding: utf-8 -*-
"""定时任务编排。

职责: 在收盘后触发各板块数据抓取并落库。
与抓取层解耦 —— 本层决定「何时」,fetchers 层决定「如何」。

首次运行自动回补: 服务启动时若发现风格轮动指数表为空,自动触发一次全量历史
回补(后台执行,不阻塞启动),保证新环境部署后图表立即可用。
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from backend.models.database import SessionLocal
from backend.tasks.valuation_tasks import run_valuation_daily
from backend.tasks.style_rotation_tasks import (
    run_style_rotation_daily,
    run_style_rotation_backfill,
)
from backend.tasks.cb_index_tasks import run_cb_index_daily
from backend.tasks.cb_list_tasks import run_cb_list_daily
from backend.tasks.cb_redeem_tasks import run_cb_redeem_daily

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _maybe_backfill_style_rotation() -> None:
    """启动检查: 风格轮动指数表为空时自动全量回补(仅一次)。

    用 APScheduler 的后台方式执行,避免阻塞服务启动;
    数据存在时(含已有少量增量)不触发 —— 增量交给日频任务滚动积累。
    """
    from backend.services.style_rotation_store import get_index_data_summary

    def _check():
        try:
            db = SessionLocal()
            try:
                summary = get_index_data_summary(db, "399376")
            finally:
                db.close()
            if summary is None:
                logger.info("检测到风格轮动指数表为空,自动执行全量历史回补...")
                run_style_rotation_backfill()
            else:
                logger.info(
                    f"风格轮动数据检查: 已有 {summary['count']} 条 "
                    f"({summary['first']} ~ {summary['last']}),跳过回补"
                )
        except Exception as exc:
            logger.error(f"风格轮动启动检查失败(不影响服务): {exc}")

    # 首次启动也注册为立即执行的后台任务,交给调度器线程池
    scheduler.add_job(
        _check,
        trigger="date",
        id="style_rotation_backfill_check",
        name="风格轮动启动回补检查",
        replace_existing=True,
    )


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
    # 可转债强赎列表日频任务: 周一至周五 15:50(全量快照之后5分钟)
    scheduler.add_job(
        run_cb_redeem_daily,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=50,
            timezone="Asia/Shanghai",
        ),
        id="cb_redeem_daily",
        name="可转债强赎列表抓取",
        replace_existing=True,
    )
    scheduler.start()
    # 启动后异步检查风格轮动数据,空表自动回补(不阻塞启动)
    _maybe_backfill_style_rotation()
    logger.info(
        "scheduler started: valuation@15:30, style_rotation@15:35, "
        "cb_index@15:40, cb_list@15:45, cb_redeem@15:50"
    )


def stop_scheduler() -> None:
    """停止调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
