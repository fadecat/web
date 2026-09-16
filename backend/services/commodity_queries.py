"""只读商品监控查询服务。

查询服务只访问商品表和任务日志，不构造数据源适配器，也不触发抓取。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.models.commodity import (
    CommodityDailyPrice,
    CommodityInstrument,
    CommodityPercentileDaily,
    CommoditySyncState,
)
from backend.models.data_status import TaskRunLog
from backend.services.commodity_calculator import ALGORITHM_VERSION, WINDOW_DAYS
from backend.services.data_catalog import Policy
from backend.services.data_status import _freshness_state

WINDOW_CODES = tuple(WINDOW_DAYS)
WINDOW_LABELS = {
    "d21": "21日",
    "d63": "63日",
    "y1": "1年",
    "y3": "3年",
    "y5": "5年",
    "y10": "10年",
}
RANGE_MONTHS = {"6m": 6, "1y": 12, "3y": 36, "5y": 60, "10y": 120}
COMMODITY_POLICY = Policy("akshare", "commodity_daily", 15, 50)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else value.isoformat()


def _sync_iso(value: datetime | None) -> str | None:
    """Serialize UTC-naive sync timestamps as explicit Beijing time.

    CommodityStore deliberately persists UTC without tzinfo.  Attaching UTC
    before conversion prevents readers from interpreting that value as local
    time and creating an eight-hour display drift.
    """
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(_SHANGHAI).isoformat(timespec="seconds")


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class _ItemData:
    instrument: CommodityInstrument
    latest_price: CommodityDailyPrice | None
    percentiles: dict[str, CommodityPercentileDaily]
    sync: CommoditySyncState | None


class CommodityQueryService:
    """商品监控的批量查询与展示状态合并。

    ``now`` 可注入，便于测试并保证 freshness 不依赖请求时刻。
    """

    def __init__(self, db: Session, now: datetime | None = None) -> None:
        self.db = db
        self.now = now

    def _expected_and_freshness(self, latest: date | None) -> str:
        expected, _ = COMMODITY_POLICY.expected(self.now)
        return _freshness_state(latest, expected)

    def _instruments(self, *, code: str | None = None) -> list[CommodityInstrument]:
        stmt = select(CommodityInstrument).where(CommodityInstrument.enabled.is_(True))
        if code is not None:
            stmt = stmt.where(CommodityInstrument.code == code)
        return list(self.db.scalars(stmt.order_by(CommodityInstrument.display_order, CommodityInstrument.code)).all())

    def _batch_items(self, instruments: list[CommodityInstrument]) -> list[_ItemData]:
        if not instruments:
            return []
        codes = [item.code for item in instruments]
        latest_dates = (
            select(
                CommodityDailyPrice.instrument_code.label("code"),
                func.max(CommodityDailyPrice.trade_date).label("latest_date"),
            )
            .where(CommodityDailyPrice.instrument_code.in_(codes))
            .group_by(CommodityDailyPrice.instrument_code)
            .subquery()
        )
        price_rows = self.db.scalars(
            select(CommodityDailyPrice)
            .join(
                latest_dates,
                and_(
                    CommodityDailyPrice.instrument_code == latest_dates.c.code,
                    CommodityDailyPrice.trade_date == latest_dates.c.latest_date,
                ),
            )
        ).all()
        prices = {row.instrument_code: row for row in price_rows}

        latest_by_code = {code: prices[code].trade_date for code in prices}
        percentile_rows = self.db.scalars(
            select(CommodityPercentileDaily)
            .join(
                latest_dates,
                and_(
                    CommodityPercentileDaily.instrument_code == latest_dates.c.code,
                    CommodityPercentileDaily.trade_date == latest_dates.c.latest_date,
                ),
            )
            .where(CommodityPercentileDaily.algorithm_version == ALGORITHM_VERSION)
        ).all()
        percentiles: dict[str, dict[str, CommodityPercentileDaily]] = {code: {} for code in codes}
        for row in percentile_rows:
            if row.trade_date == latest_by_code.get(row.instrument_code):
                percentiles[row.instrument_code][row.window_code] = row

        sync_rows = self.db.scalars(
            select(CommoditySyncState).where(CommoditySyncState.instrument_code.in_(codes))
        ).all()
        sync = {row.instrument_code: row for row in sync_rows}
        return [
            _ItemData(item, prices.get(item.code), percentiles[item.code], sync.get(item.code))
            for item in instruments
        ]

    def _windows(self, item: _ItemData) -> dict[str, dict[str, Any]]:
        return {
            code: {
                "percentile": item.percentiles[code].percentile if code in item.percentiles else None,
                "sample_count": item.percentiles[code].sample_count if code in item.percentiles else None,
                "signal": item.percentiles[code].signal if code in item.percentiles else None,
            }
            for code in WINDOW_CODES
        }

    def _combined_status(self, item: _ItemData, windows: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
        latest_date = item.latest_price.trade_date if item.latest_price else None
        data_state = self._expected_and_freshness(latest_date)
        sync_status = item.sync.status if item.sync else "never"
        if sync_status == "failed":
            return "failed", "抓取失败", data_state
        if sync_status == "suspicious":
            return "stale", "数据异常", data_state
        if sync_status == "stale":
            return "stale", "数据滞后", data_state
        if data_state == "stale":
            return "stale", "数据滞后", data_state
        if data_state == "lagging":
            return "stale", "数据滞后", data_state

        signals = {
            code: value["signal"]
            for code, value in windows.items()
            if value["signal"] in {"high", "low", "neutral"}
        }
        directions = set(signals.values())
        if {"high", "low"}.issubset(directions):
            return "divergent", "周期分化", data_state
        if "high" in directions:
            longest = max((code for code, signal in signals.items() if signal == "high"), key=WINDOW_CODES.index)
            return "high", f"{WINDOW_LABELS[longest]}高位", data_state
        if "low" in directions:
            longest = max((code for code, signal in signals.items() if signal == "low"), key=WINDOW_CODES.index)
            return "low", f"{WINDOW_LABELS[longest]}低位", data_state
        if directions and directions == {"neutral"}:
            return "neutral", "中性", data_state
        return "insufficient", "数据不足", data_state

    def _sync_dict(self, state: CommoditySyncState | None) -> dict[str, Any]:
        attempt = _sync_iso(state.last_attempt_at) if state else None
        success = _sync_iso(state.last_success_at) if state else None
        error = state.last_error if state else None
        return {
            "status": state.status if state else "never",
            "attempt_at": attempt,
            "success_at": success,
            "last_attempt_at": attempt,
            "last_success_at": success,
            "error": error,
            "last_error": error,
            "consecutive_failures": state.consecutive_failures if state else 0,
            "source_latest_date": _iso(state.source_latest_date) if state else None,
        }

    def _row(self, item: _ItemData) -> dict[str, Any]:
        windows = self._windows(item)
        signal, label, data_state = self._combined_status(item, windows)
        sync = self._sync_dict(item.sync)
        row = {
            "code": item.instrument.code,
            "name": item.instrument.name,
            "market": item.instrument.market,
            "category": item.instrument.category,
            "display_order": item.instrument.display_order,
            "source": item.instrument.source,
            "latest_price": item.latest_price.close if item.latest_price else None,
            "data_date": _iso(item.latest_price.trade_date) if item.latest_price else None,
            "windows": windows,
            "signal": signal,
            "status_label": label,
            "data_state": data_state,
            "sync": sync,
            "sync_status": sync["status"],
            "sync_attempt_at": sync["attempt_at"],
            "sync_success_at": sync["success_at"],
            "sync_error": sync["error"],
            "sync_last_attempt_at": sync["last_attempt_at"],
            "sync_last_success_at": sync["last_success_at"],
            "sync_last_error": sync["last_error"],
            "sync_source_latest_date": sync["source_latest_date"],
        }
        # Keep the six window keys flat for the compact list contract while
        # retaining ``windows`` for detail consumers and backwards
        # compatibility.  Each value is an object so percentile, sample
        # count, and persisted signal remain available without extra reads.
        row.update({code: windows[code] for code in WINDOW_CODES})
        row.update(
            {
                "current_status": signal,
                "sync_status": sync["status"],
                "last_attempt_at": sync["last_attempt_at"],
                "last_success_at": sync["last_success_at"],
                "last_error": sync["last_error"],
            }
        )
        return row

    def _all_rows(self) -> list[dict[str, Any]]:
        return [self._row(item) for item in self._batch_items(self._instruments())]

    def list_instruments(
        self,
        *,
        keyword: str | None = None,
        category: str | None = None,
        signal: str | None = None,
        window: str | None = None,
        sort_by: str = "signal",
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        rows = self._all_rows()
        if keyword:
            needle = keyword.casefold()
            rows = [row for row in rows if needle in row["code"].casefold() or needle in row["name"].casefold()]
        if category:
            rows = [row for row in rows if row["category"] == category]
        if signal:
            rows = [
                row
                for row in rows
                if (
                    row["signal"] == signal
                    if signal in {"failed", "stale", "divergent"} or window is None
                    else row["windows"][window]["signal"] == signal
                )
            ]

        if sort_by == "price":
            value = lambda row: row["latest_price"]
        elif sort_by == "date":
            value = lambda row: row["data_date"]
        elif sort_by == "signal":
            ranks = {"insufficient": 0, "neutral": 1, "low": 2, "high": 3, "divergent": 4, "stale": 5, "failed": 6}
            value = lambda row: ranks.get(row["signal"], 99)
        else:
            value = lambda row: row["windows"][sort_by]["percentile"]
        present = [row for row in rows if value(row) is not None]
        missing = [row for row in rows if value(row) is None]
        present.sort(key=lambda row: (value(row), row["display_order"], row["code"]), reverse=sort_order == "desc")
        # Reverse sorting also reverses ties; apply the required ascending tie-break explicitly.
        if len(present) > 1:
            present.sort(key=lambda row: value(row), reverse=sort_order == "desc")
            grouped: list[dict[str, Any]] = []
            index = 0
            while index < len(present):
                current = value(present[index])
                same = []
                while index < len(present) and value(present[index]) == current:
                    same.append(present[index])
                    index += 1
                grouped.extend(sorted(same, key=lambda row: (row["display_order"], row["code"])))
            present = grouped
        missing.sort(key=lambda row: (row["display_order"], row["code"]))
        return present + missing

    def overview(self) -> dict[str, Any]:
        rows = self._all_rows()
        run = self.db.scalar(
            select(TaskRunLog)
            .where(TaskRunLog.job_id == "commodity_daily")
            .order_by(TaskRunLog.started_at.desc(), TaskRunLog.id.desc())
            .limit(1)
        )
        dates = [row["data_date"] for row in rows if row["data_date"]]
        return {
            "data_date": max(dates) if dates else None,
            "instrument_total": len(rows),
            "fresh_count": sum(
                row["data_state"] == "fresh" and row["signal"] not in {"failed", "stale"}
                for row in rows
            ),
            "high_count": sum(row["signal"] == "high" for row in rows),
            "low_count": sum(row["signal"] == "low" for row in rows),
            "stale_count": sum(row["signal"] == "stale" for row in rows),
            "failed_count": sum(row["signal"] == "failed" for row in rows),
            "last_run_at": _iso((run.finished_at or run.started_at) if run else None),
            "last_run_status": run.status if run else None,
        }

    def get_detail(self, code: str) -> dict[str, Any] | None:
        instruments = self._instruments(code=code)
        if not instruments:
            return None
        row = self._row(self._batch_items(instruments)[0])
        row["metric_definition"] = {
            "percentile": "当前收盘价在对应历史窗口收盘价中的百分位，百分位越高表示价格处于高位",
            "signal": "百分位≥85为high，≤30为low，其余为neutral；样本不足窗口长度为insufficient",
            "windows": WINDOW_DAYS,
        }
        return row

    def history(self, code: str, range_code: str) -> dict[str, Any] | None:
        instruments = self._instruments(code=code)
        if not instruments:
            return None
        anchor = self.db.scalar(
            select(func.max(CommodityDailyPrice.trade_date)).where(CommodityDailyPrice.instrument_code == code)
        )
        if anchor is None:
            prices = []
            start = None
        else:
            start = _subtract_months(anchor, RANGE_MONTHS[range_code]) if range_code != "all" else None
            stmt = select(CommodityDailyPrice).where(CommodityDailyPrice.instrument_code == code)
            if start is not None:
                stmt = stmt.where(CommodityDailyPrice.trade_date >= start)
            prices = self.db.scalars(stmt.order_by(CommodityDailyPrice.trade_date)).all()

        signal_stmt = select(CommodityPercentileDaily).where(
            CommodityPercentileDaily.instrument_code == code,
            CommodityPercentileDaily.algorithm_version == ALGORITHM_VERSION,
        )
        if anchor is None:
            signals = []
        else:
            signal_stmt = signal_stmt.where(CommodityPercentileDaily.trade_date <= anchor)
            if start is not None:
                signal_stmt = signal_stmt.where(CommodityPercentileDaily.trade_date >= start)
            signals = self.db.scalars(
                signal_stmt.where(CommodityPercentileDaily.signal.in_(("high", "low"))).order_by(
                    CommodityPercentileDaily.trade_date,
                    CommodityPercentileDaily.window_days,
                    CommodityPercentileDaily.window_code,
                    CommodityPercentileDaily.id,
                )
            ).all()
        return {
            "code": code,
            "range": range_code,
            "prices": [
                {"date": _iso(price.trade_date), "close": price.close}
                for price in prices
            ],
            "signals": [
                {
                    "date": _iso(signal.trade_date),
                    "window": signal.window_code,
                    "percentile": signal.percentile,
                    "signal": signal.signal,
                    "sample_count": signal.sample_count,
                }
                for signal in signals
            ],
        }
