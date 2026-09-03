# -*- coding: utf-8 -*-
"""可转债等权指数日频任务: 拉取集思录 cb_index → 原始字段落库。

调度: 交易日 15:40 CST。
也可手动调用: python -m backend.tasks.cb_index_tasks
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.cb_index import fetch_cb_index_history
from backend.services.cb_index_store import save_cb_index_records
from backend.utils import is_trading_day


def run_cb_index_daily() -> None:
    """可转债等权指数日频任务。

    1. 交易日判断
    2. 集思录登录 → 拉 cb_index 页面 → 解析
    3. 原始字段全量落库(幂等)
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过可转债等权指数日频任务")
        return

    logger.info(f"=== 可转债等权指数日频任务开始 ({today}) ===")

    try:
        records = fetch_cb_index_history()
        logger.info(f"抓取成功: {len(records)} 条记录")
    except Exception as exc:
        logger.error(f"数据获取失败: {exc}")
        return

    db = SessionLocal()
    try:
        inserted = save_cb_index_records(db, records)
        latest_date = records[-1]["date"] if records else "?"
        logger.info(f"落库完成: {inserted} 条新写入, 最新日期={latest_date}")
    except Exception as exc:
        logger.error(f"落库失败: {exc}")
    finally:
        db.close()

    logger.info("=== 可转债等权指数日频任务完成 ===")


if __name__ == "__main__":
    run_cb_index_daily()
