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
from backend.services.index_universe import datasets_for_job
from backend.services.valuation_store import (
    save_bond_yields,
    save_dividend_yield,
    save_dividend_yield_history,
    save_valuation_snapshots,
)


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

    logger.info(f"=== 估值板块日频任务开始 ({today}) ===")
    targets = datasets_for_job("valuation_daily")
    logger.info(f"标的数量: {len(targets)}")

    db = SessionLocal()
    success_count = 0
    fail_count = 0

    # 2) 遍历统一名单中启用估值的指数
    for index, ds in targets:
        # 抓取用真实指数代码(symbol), 落库用历史存储键(storage_code), 二者可不同
        # (如 中证价值100 → symbol=931052, storage=512040)。
        symbol = ds.get("symbol") or index.get("code", "")
        storage = ds.get("storage_code") or index.get("code", "")
        code = index.get("code", "")
        name = index.get("name", code)

        try:
            # a. 指数详情(拿名称 + 估值分位/股息率 URL)
            detail = fetch_index_detail(symbol)
            index_name = detail.get("index_name") or name
            dividend_url = detail.get("index_dividend_yield_url", "")

            # b. 估值分位(全部历史)
            val_percentile_url = detail.get("index_valuation_percentile_url", "")
            val_records = fetch_index_valuation_percentile(symbol, url=val_percentile_url)
            # 估值分位是「基日至今全历史」序列, 空返回只可能是源故障/URL 失效,
            # 不能静默记成功(否则该标的永远不会触发失败通知)。
            # 注意: 非空但新增 0 条是正常的(当日无新交易日), 仍算成功。
            if not val_records:
                raise ValueError(f"估值分位接口返回空数据: {code}")

            # c. 批量落库快照(落库用 storage_code, 保持历史主键不变)
            inserted = save_valuation_snapshots(db, storage, index_name, val_records)
            latest_date = val_records[-1]["trade_date"] if val_records else "?"
            logger.info(f"  [{code}] {index_name}: {len(val_records)} 条历史, 新写入 {inserted} 条, 最新={latest_date}")

            # 估值(PE/PB/PS)主数据流成功
            success_count += 1

            # d. 股息率(并非所有标的都有独立股息率 JSON)
            if dividend_url:
                try:
                    div_data = fetch_index_dividend_yield(symbol, url=dividend_url)
                    # 股息率历史同样是全历史序列(折线图数据源), 空序列属于异常空,
                    # 判失败但不牵连本标的已提交的估值快照与其他成功子流。
                    if not div_data.get("history"):
                        raise ValueError(f"股息率接口未返回历史序列: {code}")
                    # 数据源返回的 trdCode 是"真实指数代码", 可能和 config 的 code 不一致:
                    # 例如 中证价值100 → symbol=931052, 存储键=512040(ETF 代码)。
                    # 落库统一用 storage_code, 否则同一只指数在快照表用 512040、
                    # 在股息率表用 931052, 前端按 code 关联股息率时查不到。
                    div_data["index_code"] = storage
                    div_row = save_dividend_yield(db, div_data)
                    # 全历史序列批量入库(股息率折线图用), 幂等
                    hist_inserted = save_dividend_yield_history(
                        db, storage, div_data.get("history", [])
                    )
                    if div_row:
                        logger.info(
                            f"  [{code}] 股息率已写入: {div_row.dividend_yield}%, "
                            f"历史新写入 {hist_inserted} 条"
                        )
                    else:
                        logger.info(
                            f"  [{code}] 股息率最新值已存在, 历史新写入 {hist_inserted} 条"
                        )
                    # 股息率是独立数据流，成功/失败均独立计数
                    success_count += 1
                except Exception as exc:
                    db.rollback()
                    fail_count += 1
                    logger.warning(f"  [{code}] 股息率获取失败,跳过: {exc}")

        except Exception as exc:
            db.rollback()
            logger.error(f"  [{code}] {name} 抓取失败: {exc}")
            fail_count += 1
            continue

    # 3) 国债收益率(全部历史)
    try:
        bond_records = fetch_cn_10y_bond_yield()
        # 国债收益率同为全历史序列, 空返回属于异常空
        if not bond_records:
            raise ValueError("国债收益率接口返回空数据")
        bond_inserted = save_bond_yields(db, bond_records)
        latest_bond = bond_records[-1]["trade_date"] if bond_records else "?"
        logger.info(f"  国债收益率: {len(bond_records)} 条历史, 新写入 {bond_inserted} 条, 最新={latest_bond}")
        success_count += 1
    except Exception as exc:
        db.rollback()
        logger.error(f"  国债收益率获取失败: {exc}")
        fail_count += 1

    db.close()
    logger.info(f"=== 估值板块日频任务完成: 成功 {success_count}, 失败 {fail_count} ===")
    # 返回计数供 run_logger 判定 partial(部分标的失败)
    return {"success_count": success_count, "fail_count": fail_count}


if __name__ == "__main__":
    run_valuation_daily()
