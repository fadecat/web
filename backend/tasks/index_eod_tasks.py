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
from backend.services.index_universe import datasets_for_job
from backend.services.style_rotation_store import save_index_quotes


def run_index_eod_daily() -> None:
    """遍历统一名单中易方达收盘价标的 → 全历史 → 幂等写入 IndexDailyQuote。

    单标的失败不中止整体(跳过该标的继续下一个)。
    """
    today = date.today()

    logger.info(f"=== 指数 eod 日频任务开始 ({today}) ===")
    targets = datasets_for_job("index_eod_daily")
    logger.info(f"标的数量: {len(targets)}")

    db = SessionLocal()
    success_count = 0
    fail_count = 0

    for index, ds in targets:
        symbol = ds.get("symbol") or index.get("code", "")
        storage = ds.get("storage_code") or index.get("code", "")
        code = index.get("code", "")
        name = index.get("name", code)

        try:
            records = fetch_index_eod_price(symbol)
            # 易方达 eod 单文件即全历史, 空返回只可能是源故障/URL 失效,
            # 必须判失败; 非空但新增 0 条(当日无新交易日)仍算成功。
            if not records:
                raise ValueError(f"易方达 eod 接口返回空数据: {code}")
            inserted = save_index_quotes(db, storage, records)
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
