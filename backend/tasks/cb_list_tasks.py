# -*- coding: utf-8 -*-
"""可转债全量快照日频任务: 拉取集思录 cb_list_new → 全量落库。

调度: 交易日 15:45 CST。
也可手动调用: python -m backend.tasks.cb_list_tasks
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.cb_list import fetch_cb_list
from backend.services.cb_list_store import save_cb_snapshots
from backend.utils import is_trading_day


def run_cb_list_daily() -> None:
    """可转债全量快照日频任务。

    1. 交易日判断
    2. 集思录登录 → 拉 cb_list_new 全量转债
    3. 每只转债一行,原始字段落库(幂等)
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过可转债全量快照任务")
        return

    logger.info(f"=== 可转债全量快照任务开始 ({today}) ===")

    try:
        records = fetch_cb_list()
        logger.info(f"抓取成功: {len(records)} 只转债")
    except Exception as exc:
        logger.error(f"数据获取失败: {exc}")
        return

    db = SessionLocal()
    try:
        inserted = save_cb_snapshots(db, records, today)
        logger.info(f"落库完成: {inserted} 条新写入 (共 {len(records)} 只)")
    except Exception as exc:
        logger.error(f"落库失败: {exc}")
    finally:
        db.close()

    logger.info("=== 可转债全量快照任务完成 ===")


if __name__ == "__main__":
    run_cb_list_daily()
