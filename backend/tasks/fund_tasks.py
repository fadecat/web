# -*- coding: utf-8 -*-
"""场外基金净值日频任务(组合实验室 P1, 蛋卷源)。

调度: 每自然日 23:10 CST(scheduler 注册; 晚于 index_eod 22:09, 避峰并给 QDII 留公布时间)。
手动调用: python -m backend.tasks.fund_tasks

设计(docs/portfolio-lab-data-maintenance.md §三/§四):
- 与 `research_daily_sync` **分成两个 job**: 场外基金是**单序列**(净值+日增长率),
  没有 raw/hfq 配对概念 → 合并会让失败模式互相纠缠, 且无法独立重跑。
- 落库走 `fund_nav_daily` 的 (symbol, nav_date) upsert, 幂等覆盖写;
  adj_nav 由 daily_return_pct 链式重算, 每次全量覆盖(不依赖增量递推)。
- 每标的一个独立 Session(网络在事务外), 单标的失败不中断其余。
- 进 `EVERYDAY_JOB_IDS`: 净值可能在周末/次日补发, 自然日跑 + 幂等无害。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.models.database import SessionLocal
from backend.models.research import ResearchSecurity
from backend.services import fund_nav

logger = logging.getLogger(__name__)

_ERROR_MAX = 255


def run_fund_nav_sync(*, db_factory: Any = None, nav_history_fetch_fn: Any = None) -> dict[str, Any]:
    """遍历 enabled 的场外基金标的 → 蛋卷净值全历史 → 链式复权 → 幂等覆盖写。

    返回 run_logger 结果契约: status/success_count/fail_count/inserted_rows/revised_rows。
    """
    create_session = db_factory or SessionLocal
    request_end = date.today()

    with create_session() as db:  # type: Session
        symbols = [
            s.symbol for s in db.scalars(
                select(ResearchSecurity).where(
                    ResearchSecurity.enabled.is_(True),
                    ResearchSecurity.security_type == "FUND",
                )
            ).all()
        ]

    success_count = fail_count = 0
    inserted_rows = 0
    revised_rows = 0
    errors: list[str] = []

    for symbol in symbols:
        try:
            rows = fund_nav.fetch_nav_history(symbol[:6], fetch_fn=nav_history_fetch_fn)
            with create_session() as db:  # type: Session
                stats = fund_nav.upsert_fund_nav(db, symbol, rows, source=fund_nav.DANJUAN_SOURCE)
                _mark_state(db, symbol, status="success", rows=stats["rows_written"])
            success_count += 1
            inserted_rows += stats["rows_written"]
        except Exception as exc:  # noqa: BLE001 单标的失败不中断其余
            fail_count += 1
            message = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX]
            errors.append(f"{symbol}: {message}")
            logger.warning("场外基金同步失败(%s): %s", symbol, exc)
            try:
                with create_session() as db:  # type: Session
                    _mark_state(db, symbol, status="failed", rows=0, error=message)
            except Exception:  # noqa: BLE001 状态回写失败不改变主失败语义
                logger.exception("同步状态回写失败: %s", symbol)

    status = "success" if fail_count == 0 and success_count > 0 else (
        "failed" if success_count == 0 else "partial"
    )
    logger.info(
        "场外基金净值同步完成: %s(成功 %s / 失败 %s / 写入 %s 行, 请求截止 %s)",
        status, success_count, fail_count, inserted_rows, request_end,
    )
    return {
        "status": status,
        "success_count": success_count,
        "fail_count": fail_count,
        "inserted_rows": inserted_rows,
        "revised_rows": revised_rows,
        "errors": errors[:10],
    }


def _mark_state(db: Any, symbol: str, *, status: str, rows: int, error: str | None = None) -> None:
    """回写 research_security 的同步状态。

    单标的同步**不写 TaskRunLog**(docs/portfolio-lab-data-maintenance.md §五):
    否则用户每加一只标的就刷一条 run log, 会把数据管理页的"运行记录"冲爆。
    """
    security = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == symbol))
    if security is None:
        return
    security.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)
    security.last_sync_status = status
    security.last_sync_error = error
    security.last_sync_rows = rows
    db.commit()


if __name__ == "__main__":  # pragma: no cover 手动整批触发
    logging.basicConfig(level=logging.INFO)
    print(run_fund_nav_sync())
