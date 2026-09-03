# -*- coding: utf-8 -*-
"""估值板块日频任务: 遍历 valuation.yaml 全部标的 → fetch → 批量落库。

调度: 交易日 15:30 CST(scheduler.py 注册)。
也可手动调用: python -m backend.tasks.valuation_tasks
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from backend.models.database import SessionLocal
from backend.services.fetchers.valuation import (
    fetch_cn_10y_bond_yield,
    fetch_index_detail,
    fetch_index_dividend_yield,
    fetch_index_valuation_percentile,
)
from backend.services.valuation_store import (
    save_bond_yields,
    save_dividend_yield,
    save_valuation_snapshots,
)
from backend.utils import is_trading_day, load_valuation_targets


def run_valuation_daily() -> None:
    """估值板块日频抓取: 遍历全部标的 + 国债 → 批量落库。

    流程:
    1. 交易日判断(非交易日跳过)
    2. 遍历 valuation.yaml 的 8 个标的:
       a. fetch_index_detail → 拿指数名称 + CDN URL
       b. fetch_index_valuation_percentile → 全部历史 PE/PB/PS 分位
       c. save_valuation_snapshots → 批量落库(幂等,已存在跳过)
       d. fetch_index_dividend_yield → 股息率(最新)
       e. save_dividend_yield → 落库
    3. fetch_cn_10y_bond_yield → 全部历史国债收益率
       save_bond_yields → 批量落库

    单标的失败不中止整体(跳过该标的继续下一个)。
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过估值板块日频任务")
        return

    logger.info(f"=== 估值板块日频任务开始 ({today}) ===")
    targets = load_valuation_targets()
    logger.info(f"标的数量: {len(targets)}")

    db = SessionLocal()
    success_count = 0
    fail_count = 0

    # 2) 遍历标的
    for target in targets:
        code = target.get("code", "")
        name = target.get("name", code)
        detail_url = target.get("index_detail_url", "")
        dividend_url = target.get("index_dividend_yield_url", "")

        try:
            # a. 指数详情(拿名称 + URL)
            detail = fetch_index_detail(code, url=detail_url)
            index_name = detail.get("index_name") or name
            if not dividend_url:
                dividend_url = detail.get("index_dividend_yield_url", "")

            # b. 估值分位(全部历史)
            val_percentile_url = detail.get("index_valuation_percentile_url", "")
            val_records = fetch_index_valuation_percentile(code, url=val_percentile_url)

            # c. 批量落库快照
            inserted = save_valuation_snapshots(db, code, index_name, val_records)
            latest_date = val_records[-1]["trade_date"] if val_records else "?"
            logger.info(f"  [{code}] {index_name}: {len(val_records)} 条历史, 新写入 {inserted} 条, 最新={latest_date}")

            # d. 股息率(并非所有标的都有独立股息率 JSON)
            if dividend_url:
                try:
                    div_data = fetch_index_dividend_yield(code, url=dividend_url)
                    div_row = save_dividend_yield(db, div_data)
                    if div_row:
                        logger.info(f"  [{code}] 股息率已写入: {div_row.dividend_yield}%")
                    else:
                        logger.info(f"  [{code}] 股息率已存在,跳过")
                except Exception as exc:
                    logger.warning(f"  [{code}] 股息率获取失败,跳过: {exc}")

            success_count += 1

        except Exception as exc:
            logger.error(f"  [{code}] {name} 抓取失败: {exc}")
            fail_count += 1
            continue

    # 3) 国债收益率(全部历史)
    try:
        bond_records = fetch_cn_10y_bond_yield()
        bond_inserted = save_bond_yields(db, bond_records)
        latest_bond = bond_records[-1]["trade_date"] if bond_records else "?"
        logger.info(f"  国债收益率: {len(bond_records)} 条历史, 新写入 {bond_inserted} 条, 最新={latest_bond}")
    except Exception as exc:
        logger.error(f"  国债收益率获取失败: {exc}")
        fail_count += 1

    db.close()
    logger.info(f"=== 估值板块日频任务完成: 成功 {success_count}, 失败 {fail_count} ===")


if __name__ == "__main__":
    run_valuation_daily()
