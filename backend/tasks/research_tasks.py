# -*- coding: utf-8 -*-
"""研究回放任务: 每 natural 日同步研究标的的 raw/hfq 配对日线、交易日历与权益事件日历。

对齐 valuation/index_eod「历史全量同步, 自然日跑, 幂等」先例; 无交易日闸门。
标的失败不中断其余(网络在事务外, 每标的新 Session)。
数据源由 config/research.yaml 的 data_source 决定(akshare | tencent), 显式配置不静默换源。
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
    CAP_CORPORATE_EVENT_CALENDAR,
    AdjustMode,
    MarketDataProvider,
    provider_factory,
)
from backend.utils import load_research_settings, load_research_targets

logger = logging.getLogger(__name__)

_HISTORY_START_DEFAULT = "2021-01-01"
_DATA_SOURCE_DEFAULT = "akshare"


def _resolve_history_start(config: dict[str, Any]) -> date:
    raw = str(config.get("history_start") or _HISTORY_START_DEFAULT)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("research 配置 history_start 非法(%r), 使用默认 %s", raw, _HISTORY_START_DEFAULT)
        return datetime.strptime(_HISTORY_START_DEFAULT, "%Y-%m-%d").date()


def _resolve_data_source(config: dict[str, Any]) -> str:
    """数据源路由(akshare | tencent); 显式配置驱动, 不静默换源。"""
    return str(config.get("data_source") or _DATA_SOURCE_DEFAULT).strip().lower()


def _default_provider_factory(data_source: str = _DATA_SOURCE_DEFAULT) -> MarketDataProvider:
    return provider_factory(data_source)


# 尾部发布滞后最多容忍的 bar 数(实测腾讯 ETF hfq 晚于 raw 出数 1 天; 更大差异视为数据异常)
_TRAILING_LAG_MAX_BARS = 5


def _align_trailing_publication_lag(
    raw_bars: list, hfq_bars: list,
) -> tuple[list, list, date | None]:
    """一侧末尾若干 bar 尚未发布(如腾讯 ETF hfq 晚于 raw 出数)时, 两侧截齐到共同末日。

    只处理「纯尾部滞后」: 长侧截掉尾部后与短侧仍逐日可配对(残余错位由
    publish_paired_snapshot 的严格配对校验兜底拒绝)。内部空洞/大面积错位不在
    此列, 不截齐、保持失败可见。返回 (raw, hfq, 截齐到的共同末日 or None)。
    """
    if not raw_bars or not hfq_bars:
        return raw_bars, hfq_bars, None
    raw_last = max(bar.trade_date for bar in raw_bars)
    hfq_last = max(bar.trade_date for bar in hfq_bars)
    if raw_last == hfq_last:
        return raw_bars, hfq_bars, None
    if raw_last > hfq_last:
        longer, complete_last = raw_bars, hfq_last
    else:
        longer, complete_last = hfq_bars, raw_last
    aligned = [bar for bar in longer if bar.trade_date <= complete_last]
    dropped = len(longer) - len(aligned)
    if dropped <= 0 or dropped > _TRAILING_LAG_MAX_BARS:
        return raw_bars, hfq_bars, None
    if raw_last > hfq_last:
        return aligned, hfq_bars, complete_last
    return raw_bars, aligned, complete_last


def run_research_daily_sync(
    *,
    provider_factory_fn: Any = None,
    config_path: str | None = None,
    db_factory: Any = None,
) -> dict[str, Any]:
    """每自然日: ① upsert 名单 ② 刷新日历 ③ 逐标的 raw/hfq 配对抓取→发布(+权益事件日历)。

    返回 run_logger 结果契约: status/success_count/fail_count/inserted_rows/revised_rows。
    """
    targets = load_research_targets(config_path)
    settings = load_research_settings(config_path)
    history_start = _resolve_history_start(settings)
    data_source = _resolve_data_source(settings)
    request_end = date.today()

    create_session = db_factory or SessionLocal
    make_provider = provider_factory_fn or (lambda: _default_provider_factory(data_source))

    inserted_rows = revised_rows = 0
    success_count = fail_count = 0
    errors: list[str] = []

    with create_session() as db:  # type: Session
        research_store.upsert_securities(db, targets)
        provider = make_provider()
        try:
            sessions = provider.get_trade_calendar()
            research_store.upsert_trade_calendar(db, sessions, source=provider.name)
            db.commit()  # upsert_trade_calendar 只 flush; 会话上下文退出即回滚, 须显式提交
        except Exception as exc:  # noqa: BLE001 日历失败不阻塞 bars 同步(缺日历降低回放能力)
            logger.warning("交易日历刷新失败: %s", exc)

    with create_session() as db:  # type: Session
        securities = db.scalars(
            select(ResearchSecurity).where(
                ResearchSecurity.enabled.is_(True),
                # 场外基金是单序列净值, 由 fund_nav_sync 走蛋卷处理(见 fund_tasks.py);
                # 混进来只会拿腾讯去抓基金, 每天白记一次失败。
                ResearchSecurity.security_type != "FUND",
            )
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
                # 尾部发布滞后截齐(如腾讯 ETF hfq 晚于 raw 出数): 只截纯尾部, 内部错位仍严格拒绝
                raw_bars, hfq_bars, common_last = _align_trailing_publication_lag(raw_bars, hfq_bars)
                if common_last is not None:
                    logger.warning(
                        "%s: raw/hfq 尾部发布滞后, 截齐到共同末日 %s 后发布", row["symbol"], common_last,
                    )
                result = research_store.publish_paired_snapshot(
                    db, row["symbol"], raw_bars, hfq_bars,
                    source=provider.name,
                    request_start=history_start, request_end=request_end,
                )
                # 权益事件日历(有该能力的 Provider 才抓; 失败不阻塞 bars 同步)
                if CAP_CORPORATE_EVENT_CALENDAR in getattr(provider, "capabilities", frozenset()):
                    try:
                        events = provider.get_corporate_events(row["symbol"])
                        research_store.upsert_corporate_events(
                            db, row["symbol"], events, source=provider.name,
                        )
                        db.commit()  # 事件 upsert 只 flush, 须显式提交(publish 已先行 commit)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("权益事件日历刷新失败(%s): %s", row["symbol"], exc)
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
