# -*- coding: utf-8 -*-
"""可转债强赎列表日频任务: 拉取集思录 redeem_list → 全量落库。

调度: 交易日 15:50 CST(cb_list 全量快照之后)。
也可手动调用: python -m backend.tasks.cb_redeem_tasks
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.cb_redeem import fetch_redeem_list
from backend.services.cb_redeem_store import save_cb_redeem
from backend.utils import is_trading_day


def run_cb_redeem_daily() -> None:
    """可转债强赎列表日频任务。

    1. 交易日判断
    2. 集思录登录 → 拉 redeem_list 全量强赎数据
    3. 每只强赎相关转债一行, 原始字段落库(幂等)
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过强赎列表快照任务")
        return

    logger.info(f"=== 可转债强赎列表快照任务开始 ({today}) ===")

    try:
        records = fetch_redeem_list()
        logger.info(f"抓取成功: {len(records)} 条强赎数据")
    except Exception as exc:
        logger.error(f"数据获取失败: {exc}")
        return

    db = SessionLocal()
    try:
        inserted = save_cb_redeem(db, records, today)
        logger.info(f"落库完成: {inserted} 条新写入 (共 {len(records)} 条)")
    except Exception as exc:
        logger.error(f"落库失败: {exc}")
    finally:
        db.close()

    logger.info("=== 可转债强赎列表快照任务完成 ===")


if __name__ == "__main__":
    run_cb_redeem_daily()
