# -*- coding: utf-8 -*-
"""商品监控日频任务。

商品源请求始终发生在数据库事务之外；每个品种重新创建会话并独立提交，
因此某个品种的源数据或写库失败不会回滚前面已经成功的品种。
"""
from __future__ import annotations

import inspect
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select

from backend.models.commodity import CommodityInstrument
from backend.models.database import SessionLocal
from backend.services.commodity_source import CommodityPriceRecord, CommoditySourceAdapter, normalize_price_rows
from backend.services.commodity_store import CommodityStore, CommodityStoreResult

TASK_TIMEOUT_SEC = 20 * 60
ITEM_TIMEOUT_SEC = 20
MAX_FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SEC = (2.0, 5.0)


@dataclass(frozen=True)
class _Counters:
    total: int = 0
    success_count: int = 0
    unchanged_count: int = 0
    failed_count: int = 0
    suspicious_count: int = 0
    inserted_rows: int = 0
    revised_rows: int = 0
    percentile_rows: int = 0

    def result(self) -> dict[str, int]:
        return {
            "total": self.total,
            "success_count": self.success_count,
            "unchanged_count": self.unchanged_count,
            "failed_count": self.failed_count,
            "suspicious_count": self.suspicious_count,
            "inserted_rows": self.inserted_rows,
            "revised_rows": self.revised_rows,
            "percentile_rows": self.percentile_rows,
            # run_logger uses this key to derive success/partial/failed.
            "fail_count": self.failed_count,
        }


def _accepts_two_args(fn: Callable) -> bool:
    """Determine whether an injected hook wants ``(code, market)``."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    positional = [
        p for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params) or len(positional) >= 2


def _call_fetch(fetcher: Callable, instrument: CommodityInstrument):
    if _accepts_two_args(fetcher):
        return fetcher(instrument.code, instrument.market)
    return fetcher(instrument.code)


def _normalize_result(raw) -> list[CommodityPriceRecord]:
    """Normalize raw DataFrame hooks while allowing already normalized Phase A rows."""
    if hasattr(raw, "columns") and hasattr(raw, "iterrows"):
        return normalize_price_rows(raw, source="akshare")
    rows = list(raw) if raw is not None else []
    if not rows:
        raise ValueError("commodity source result is empty")
    if not all(isinstance(row, CommodityPriceRecord) for row in rows):
        raise ValueError("commodity source hook must return normalized records or a DataFrame")
    return rows


def _fetch_with_retry(
    instrument: CommodityInstrument,
    *,
    fetcher: Callable,
    sleep: Callable[[float], None],
    random_fn: Callable[[], float],
    monotonic: Callable[[], float],
    deadline: float,
    item_timeout_sec: float,
) -> list[CommodityPriceRecord]:
    """Fetch one item; validation errors are terminal, transport errors retry."""
    item_started = monotonic()
    last_error: Exception | None = None
    for attempt in range(MAX_FETCH_ATTEMPTS):
        if monotonic() >= deadline:
            raise TimeoutError("commodity task deadline exceeded")
        try:
            raw = _call_fetch(fetcher, instrument)
            rows = _normalize_result(raw)
            elapsed = monotonic() - item_started
            if elapsed > item_timeout_sec:
                raise TimeoutError(f"commodity item exceeded {item_timeout_sec:g}s target")
            return rows
        except ValueError:
            # CommoditySourceError and all Phase A validation errors are deterministic.
            raise
        except Exception as exc:  # network/transient source failures
            last_error = exc
            if attempt == MAX_FETCH_ATTEMPTS - 1:
                raise
            base = RETRY_BACKOFF_SEC[attempt]
            delay = base + max(0.0, min(0.25, float(random_fn()) * 0.25))
            if monotonic() + delay >= deadline:
                raise TimeoutError("commodity task deadline exceeded") from exc
            sleep(delay)
    raise last_error or RuntimeError("commodity fetch failed")


def _persist_failed(code: str, error: Exception | str) -> None:
    """Persist failure using a fresh transaction after the item transaction rolled back."""
    db = SessionLocal()
    try:
        CommodityStore(db).mark_failed(code, str(error))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[%s] failed to persist commodity failure state", code)
    finally:
        db.close()


def _store_one(code: str, rows: Iterable[CommodityPriceRecord]) -> CommodityStoreResult:
    db = SessionLocal()
    try:
        result = CommodityStore(db).upsert_prices(code, rows)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_commodity_daily(
    *,
    fetch_runner: Callable | None = None,
    fetch_fn: Callable | None = None,
    source_factory: Callable[[], CommoditySourceAdapter] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    random_fn: Callable[[], float] = random.random,
    monotonic: Callable[[], float] = time.monotonic,
    started_at: float | None = None,
    task_timeout_sec: float = TASK_TIMEOUT_SEC,
    item_timeout_sec: float = ITEM_TIMEOUT_SEC,
) -> dict[str, int]:
    """按 display_order 串行同步 enabled 商品并返回统一任务计数。

    AkShare 本身不是可取消的 transport，因此生产执行采用同步调用、调用前后的
    item/deadline 边界检查；集成环境可通过 ``fetch_runner`` 注入带真实 timeout 的
    transport。任务到总 deadline 后不再启动新的请求，剩余品种持久化为 failed。
    """
    if fetch_runner is not None and fetch_fn is not None:
        raise ValueError("pass only one of fetch_runner and fetch_fn")
    fetch_runner = fetch_runner or fetch_fn
    started = monotonic() if started_at is None else started_at
    deadline = started + task_timeout_sec

    # 仅短暂读取名单，网络获取和所有每品种写事务都在该会话之外。
    listing_db = SessionLocal()
    try:
        instruments = list(
            listing_db.scalars(
                select(CommodityInstrument)
                .where(CommodityInstrument.enabled.is_(True))
                .order_by(CommodityInstrument.display_order, CommodityInstrument.code)
            )
        )
    finally:
        listing_db.close()

    counters = _Counters(total=len(instruments))
    logger.info("=== 商品价格与分位日频任务开始: {} 个品种 ===", len(instruments))
    for index, instrument in enumerate(instruments):
        if monotonic() >= deadline:
            _persist_failed(instrument.code, "commodity task deadline exceeded before fetch")
            counters = _replace(counters, failed_count=counters.failed_count + 1)
            for remaining in instruments[index + 1:]:
                _persist_failed(remaining.code, "commodity task deadline exceeded before fetch")
                counters = _replace(counters, failed_count=counters.failed_count + 1)
            break

        adapter = source_factory() if source_factory is not None else CommoditySourceAdapter()
        fetcher = fetch_runner or getattr(adapter, "fetch_history", None) or getattr(adapter, "fetch")
        try:
            rows = _fetch_with_retry(
                instrument,
                fetcher=fetcher,
                sleep=sleep,
                random_fn=random_fn,
                monotonic=monotonic,
                deadline=deadline,
                item_timeout_sec=item_timeout_sec,
            )
            stored = _store_one(instrument.code, rows)
            if stored.status == "suspicious":
                counters = _replace(
                    counters,
                    failed_count=counters.failed_count + 1,
                    suspicious_count=counters.suspicious_count + 1,
                )
            else:
                counters = _replace(
                    counters,
                    success_count=counters.success_count + 1,
                    unchanged_count=counters.unchanged_count + (stored.status == "unchanged"),
                    inserted_rows=counters.inserted_rows + stored.inserted_rows,
                    revised_rows=counters.revised_rows + stored.revised_rows,
                    percentile_rows=counters.percentile_rows + stored.percentile_rows,
                )
            logger.info("  [{}] {}: {}", instrument.code, instrument.name, stored.status)
        except Exception as exc:
            logger.error("  [{}] {} 抓取/写入失败: {}", instrument.code, instrument.name, exc)
            _persist_failed(instrument.code, exc)
            counters = _replace(counters, failed_count=counters.failed_count + 1)

        if index < len(instruments) - 1 and monotonic() < deadline:
            delay = 2.0 + max(0.0, min(2.0, float(random_fn()) * 2.0))
            if monotonic() + delay < deadline:
                sleep(delay)

    result = counters.result()
    logger.info(
        "=== 商品价格与分位日频任务完成: 成功 {}, 失败 {}, 新写入 {} ===",
        result["success_count"], result["fail_count"], result["inserted_rows"],
    )
    return result


def _replace(counters: _Counters, **changes) -> _Counters:
    values = counters.__dict__.copy()
    values.update(changes)
    return _Counters(**values)


if __name__ == "__main__":
    run_commodity_daily()
