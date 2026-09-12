# -*- coding: utf-8 -*-
"""可转债等权指数日频任务: 拉取集思录 cb_index → 原始字段落库。

调度: 每个自然日 15:04 CST(集思录当日值发布偏晚, 盘后时段只能抓到昨日;
次日/周末自然日补跑 + 全历史幂等落库, 最近交易日的值最迟隔天追平)。
也可手动调用: python -m backend.tasks.cb_index_tasks
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.cb_index import fetch_cb_index_history
from backend.services.cb_index_store import save_cb_index_records


def run_cb_index_daily() -> None:
    """可转债等权指数日频任务。

    1. 集思录登录 → 拉 cb_index 页面 → 解析
    2. 原始字段全量落库(幂等; 日期由源数据决定, 周末跑即回补最近交易日)
    """
    today = date.today()

    logger.info(f"=== 可转债等权指数日频任务开始 ({today}) ===")

    try:
        records = fetch_cb_index_history()
        # 等权指数是全历史序列, 空返回=页面结构变更/未登录, 必须判失败;
        # 非空但新增 0 条(当日已写过)仍算成功。
        if not records:
            raise ValueError("可转债等权指数接口返回空数据")
        logger.info(f"抓取成功: {len(records)} 条记录")
    except Exception as exc:
        logger.error(f"数据获取失败: {exc}")
        return {"success_count": 0, "fail_count": 1}

    db = SessionLocal()
    try:
        inserted = save_cb_index_records(db, records)
        latest_date = records[-1]["date"] if records else "?"
        logger.info(f"落库完成: {inserted} 条新写入, 最新日期={latest_date}")
    except Exception as exc:
        db.rollback()
        logger.error(f"落库失败: {exc}")
        return {"success_count": 0, "fail_count": 1}
    finally:
        db.close()

    logger.info("=== 可转债等权指数日频任务完成 ===")
    return {"success_count": 1, "fail_count": 0}


if __name__ == "__main__":
    run_cb_index_daily()
