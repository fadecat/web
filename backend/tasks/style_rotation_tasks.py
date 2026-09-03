# -*- coding: utf-8 -*-
"""风格轮动日频任务: 拉取指数日线 → 原始 OHLCV 落库。

调度: 交易日 15:35 CST(scheduler.py 注册)。
也可手动调用: python -m backend.tasks.style_rotation_tasks
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.style_rotation import fetch_index_kline, LEFT_SYMBOL, LEFT_NAME, RIGHT_SYMBOL, RIGHT_NAME
from backend.services.style_rotation_store import save_index_quotes
from backend.utils import is_trading_day


def run_style_rotation_daily() -> None:
    """风格轮动日频任务: 拉 2 个指数日线 → 原始落库。

    不做收益率差值计算(计算放查询时按需做)。
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过风格轮动日频任务")
        return

    logger.info(f"=== 风格轮动日频任务开始 ({today}) ===")

    db = SessionLocal()
    total_inserted = 0

    for code, name in [(LEFT_SYMBOL, LEFT_NAME), (RIGHT_SYMBOL, RIGHT_NAME)]:
        try:
            klines = fetch_index_kline(code)
            inserted = save_index_quotes(db, code, klines)
            logger.info(f"  [{code}] {name}: {len(klines)} 条K线, 新写入 {inserted} 条")
            total_inserted += inserted
        except Exception as exc:
            logger.error(f"  [{code}] {name} 抓取失败: {exc}")

    db.close()
    logger.info(f"=== 风格轮动日频任务完成: 新写入 {total_inserted} 条 ===")


if __name__ == "__main__":
    run_style_rotation_daily()
