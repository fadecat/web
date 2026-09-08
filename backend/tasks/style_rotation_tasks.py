# -*- coding: utf-8 -*-
"""风格轮动日频任务: 拉取指数日线 → 原始 OHLCV 落库。

调度: 交易日 15:35 CST(scheduler.py 注册)。
手动调用:
    python -m backend.tasks.style_rotation_tasks              # 日频增量
    python -m backend.tasks.style_rotation_tasks --backfill   # 全量历史回补(2013至今)
"""
from __future__ import annotations

import sys
from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.style_rotation import (
    fetch_index_kline,
    fetch_index_kline_auto,
)
from backend.services.index_universe import datasets_for_job
from backend.services.style_rotation_store import (
    get_index_data_summary,
    save_index_quotes,
    scan_date_gaps,
)
from backend.utils import is_trading_day

# 全量回补的起始日期(国证系列指数 2012 年前后基日, 2013 起足够覆盖轮动分析)
BACKFILL_START = "2013-01-01"


def _run_pair(
    fetcher,
    label: str,
) -> None:
    """对统一名单中启用腾讯日线的指数执行同一抓取函数并落库。"""
    db = SessionLocal()
    total_inserted = 0
    success_count = fail_count = 0

    for index, ds in datasets_for_job("style_rotation_daily"):
        symbol = ds.get("symbol") or index.get("code", "")
        storage = ds.get("storage_code") or index.get("code", "")
        code = index.get("code", "")
        name = index.get("name", code)
        try:
            klines = fetcher(symbol)
            # 空返回属于异常空(源故障/代码失效): 判该指数失败,
            # 另一只指数已成功的计数不受影响。
            if not klines:
                raise ValueError(f"指数日线接口返回空数据: {code}")
            inserted = save_index_quotes(db, storage, klines)
            logger.info(f"  [{code}] {name}: 拉取 {len(klines)} 条, 新写入 {inserted} 条")
            total_inserted += inserted
            success_count += 1
        except Exception as exc:
            db.rollback()
            fail_count += 1
            logger.error(f"  [{code}] {name} 抓取失败: {exc}")

    db.close()
    logger.info(f"=== 风格轮动{label}完成: 新写入 {total_inserted} 条 ===")
    return {"success_count": success_count, "fail_count": fail_count}


def run_style_rotation_daily() -> None:
    """风格轮动日频任务: 拉 2 个指数日线 → 原始落库(增量)。

    不做收益率差值计算(计算放查询时按需做)。
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过风格轮动日频任务")
        return {"status": "skipped", "success_count": 0, "fail_count": 0}

    logger.info(f"=== 风格轮动日频任务开始 ({today}) ===")
    return _run_pair(lambda code: fetch_index_kline(code), "日频任务")


def run_style_rotation_backfill() -> None:
    """风格轮动全量历史回补: 分段抓 2013 至今 → 幂等落库 → 空洞扫描。

    适用场景: 新环境首次部署、数据缺口修复。重复执行无害(已存在的日期跳过)。
    """
    logger.info(f"=== 风格轮动全量回补开始 (起点 {BACKFILL_START}) ===")
    result = _run_pair(
        lambda code: fetch_index_kline_auto(code, start_date=BACKFILL_START),
        "全量回补",
    )
    _log_gap_report()
    return result


def _log_gap_report() -> None:
    """回补后跑空洞扫描,输出各指数数据概况与可疑缺口。"""
    db = SessionLocal()
    try:
        for index, ds in datasets_for_job("style_rotation_daily"):
            storage = ds.get("storage_code") or index.get("code", "")
            code = index.get("code", "")
            name = index.get("name", code)
            summary = get_index_data_summary(db, storage)
            if not summary:
                logger.warning(f"  [空洞扫描] [{code}] {name}: 无数据!")
                continue
            gaps = scan_date_gaps(db, storage)
            logger.info(
                f"  [空洞扫描] [{code}] {name}: {summary['count']} 条, "
                f"{summary['first']} ~ {summary['last']}, 可疑缺口 {len(gaps)} 处"
            )
            for g in gaps:
                logger.warning(
                    f"    缺口: {g['prev']} -> {g['next']} (间隔 {g['gap_days']} 天)"
                )
    finally:
        db.close()


def main() -> None:
    if "--backfill" in sys.argv:
        run_style_rotation_backfill()
    else:
        run_style_rotation_daily()


if __name__ == "__main__":
    main()
