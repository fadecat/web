# -*- coding: utf-8 -*-
"""组合实验室后台任务(P3): L1 列表页三格缓存刷新。

调度: 每自然日 23:30 CST —— **排在 `fund_nav_sync`(23:10) 之后**, 因为三格里的
「日收益 / 近一月 / 今年以来」要用到当天最新的基金净值; 先同步、再算缓存。

为什么要缓存(docs/portfolio-lab-multi-portfolio.md §L1):
- 卡片三格是**固定参数(不平衡 + 三个区间)**的结果, 天然可缓存;
- `cached_asof_date` 就是卡片上显示的「收益时间」;
- ⭐ 列表页三格与详情页收益条**必须数字一致** → 两者共用 `backtest_service.compute()`
  的同一条「区间解析 + 份额法账本」实现, 本任务只负责"遍历组合 + 落库"。

组合不具备回测条件(成员没加齐 / 权重没设 / 数据未同步)时**清空缓存**而不是留着过期数字,
列表页会显示 `—`(规格 9.3: 空组合三格显示破折号)。
"""
from __future__ import annotations

import logging
from typing import Any

from backend.models.database import SessionLocal
from backend.services import backtest_service

logger = logging.getLogger(__name__)


def run_portfolio_cache_refresh(*, db_factory: Any = None, limit: int = 500) -> dict[str, Any]:
    """遍历 active 组合 → 重算三格 → 回写。

    返回 `run_logger` 结果契约: status / success_count / fail_count。
    ⚠ `success_count` 只统计**真的具备回测条件**的组合; 条件不足的被计为 fail
    (这是有意的: 让运行记录里能看出"有组合还跑不了", 而不是静默归零)。
    """
    create_session = db_factory or SessionLocal
    with create_session() as db:  # type: Session
        outcome = backtest_service.refresh_all_cached_metrics(db, limit=limit)
    logger.info(
        "组合卡片缓存刷新: %s(成功 %s / 失败 %s / 共 %s)",
        outcome["status"], outcome["success_count"], outcome["fail_count"], outcome["total"],
    )
    return outcome


if __name__ == "__main__":  # pragma: no cover 手动整批触发
    logging.basicConfig(level=logging.INFO)
    print(run_portfolio_cache_refresh())
