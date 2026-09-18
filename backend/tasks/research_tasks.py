# -*- coding: utf-8 -*-
"""研究回放任务: 每 natural 日同步研究标的的 raw/hfq 配对日线与交易日历。

对齐 valuation/index_eod「历史全量同步, 自然日跑, 幂等」先例; 无交易日闸门。
标的失败不中断其余(网络在事务外, 每标的新 Session)。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.database import SessionLocal
from backend.models.research import ResearchSecurity
from backend.services import research_store
from backend.services.market_data import (
    AdjustMode,
    MarketDataProvider,
    provider_factory,
)
from backend.utils import load_research_settings, load_research_targets

logger = logging.getLogger(__name__)

_HISTORY_START_DEFAULT = "2021-01-01"


def _resolve_history_start(config: dict[str, Any]) -> date:
    raw = str(config.get("history_start") or _HISTORY_START_DEFAULT)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("research 配置 history_start 非法(%r), 使用默认 %s", raw, _HISTORY_START_DEFAULT)
        return datetime.strptime(_HISTORY_START_DEFAULT, "%Y-%m-%d").date()


def _default_provider_factory() -> MarketDataProvider:
    return provider_factory("akshare")


def run_research_daily_sync(
    *,
    provider_factory_fn: Any = None,
    config_path: str | None = None,
    db_factory: Any = None,
) -> dict[str, Any]:
    """每自然日: ① upsert 名单 ② 刷新日历 ③ 逐标的 raw/hfq 配对抓取→发布。

    返回 run_logger 结果契约: status/success_count/fail_count/inserted_rows/revised_rows。
    """
    targets = load_research_targets(config_path)
    settings = load_research_settings(config_path)
    history_start = _resolve_history_start(settings)
    request_end = date.today()

    create_session = db_factory or SessionLocal
    make_provider = provider_factory_fn or _default_provider_factory

    inserted_rows = revised_rows = 0
    success_count = fail_count = 0
    errors: list[str] = []

    with create_session() as db:  # type: Session
        research_store.upsert_securities(db, targets)
        provider = make_provider()
        try:
            sessions = provider.get_trade_calendar()
            research_store.upsert_trade_calendar(db, sessions, source=provider.name)
        except Exception as exc:  # noqa: BLE001 日历失败不阻塞 bars 同步(缺日历降低回放能力)
            logger.warning("交易日历刷新失败: %s", exc)

    with create_session() as db:  # type: Session
        securities = db.scalars(
            select(ResearchSecurity).where(ResearchSecurity.enabled.is_(True))
        ).all()
        rows = [
            {"symbol": s.symbol, "security_type": s.security_type, "source": s.source}
            for s in securities
        ]

    for row in rows:
        try:
            with create_session() as db:  # type: Session
                provider = make_provider()
                raw_bars = provider.get_daily_bars(
                    row["symbol"], history_start, request_end,
                    AdjustMode.RAW, security_type=row["security_type"],
                )
                hfq_bars = provider.get_daily_bars(
                    row["symbol"], history_start, request_end,
                    AdjustMode.HFQ, security_type=row["security_type"],
                )
                result = research_store.publish_paired_snapshot(
                    db, row["symbol"], raw_bars, hfq_bars,
                    source=provider.name,
                    request_start=history_start, request_end=request_end,
                )
                if result.status == "success":
                    success_count += 1
                    inserted_rows += result.inserted_rows
                    revised_rows += result.revised_rows
                else:
                    fail_count += 1
                    errors.append(f"{row['symbol']}: {result.error}")
        except Exception as exc:  # noqa: BLE001 单标的失败不中断其余
            fail_count += 1
            errors.append(f"{row['symbol']}: {exc}")

    status = "success" if fail_count == 0 and success_count > 0 else (
        "failed" if success_count == 0 else "partial"
    )
    return {
        "status": status,
        "success_count": success_count,
        "fail_count": fail_count,
        "inserted_rows": inserted_rows,
        "revised_rows": revised_rows,
        "errors": errors[:10],
    }
