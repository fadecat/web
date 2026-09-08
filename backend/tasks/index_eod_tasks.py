# -*- coding: utf-8 -*-
"""指数日线收盘价(eod)日频任务: 遍历 index_eod.yaml 全部标的 → 易方达全历史 → 幂等落库。

调度: 交易日 22:09 CST(scheduler.py 注册, 估值 22:06 之后错峰)。
手动调用:
    python -m backend.tasks.index_eod_tasks

说明: 易方达源单文件即全历史, 本任务无 daily/backfill 之分 ——
每次运行就是全量同步, 首次部署直接跑本任务即完成历史回补。
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.index_eod import fetch_index_eod_price
from backend.services.style_rotation_store import save_index_quotes
from backend.utils import load_index_eod_targets


def run_index_eod_daily() -> None:
    """遍历 index_eod.yaml 标的 → 易方达 eod 全历史 → 幂等写入 IndexDailyQuote。

    单标的失败不中止整体(跳过该标的继续下一个)。
    """
    today = date.today()

    logger.info(f"=== 指数 eod 日频任务开始 ({today}) ===")
    targets = load_index_eod_targets()
    logger.info(f"标的数量: {len(targets)}")

    db = SessionLocal()
    success_count = 0
    fail_count = 0

    for target in targets:
        code = target.get("code", "")
        name = target.get("name", code)

        try:
            records = fetch_index_eod_price(code)
            inserted = save_index_quotes(db, code, records)
            latest_date = records[-1]["date"] if records else "?"
            logger.info(
                f"  [{code}] {name}: 源 {len(records)} 条, 新写入 {inserted} 条, 最新={latest_date}"
            )
            success_count += 1
        except Exception as exc:
            db.rollback()
            logger.error(f"  [{code}] {name} 抓取失败: {exc}")
            fail_count += 1
            continue

    db.close()
    logger.info(f"=== 指数 eod 日频任务完成: 成功 {success_count}, 失败 {fail_count} ===")
    # 返回计数供 run_logger 判定 partial(部分标的失败)
    return {"success_count": success_count, "fail_count": fail_count}


if __name__ == "__main__":
    run_index_eod_daily()
