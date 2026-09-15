"""AkShare 商品数据适配器与统一清洗。"""
from __future__ import annotations

import math
import inspect
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd


class CommoditySourceError(ValueError):
    """源数据无法安全入库。"""


@dataclass(frozen=True)
class CommodityPriceRecord:
    trade_date: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    source: str = "akshare"

    @property
    def date(self) -> date:
        return self.trade_date


def _column(columns: list[Any], aliases: tuple[str, ...]) -> Any | None:
    normalized = {str(col).strip().lower(): col for col in columns}
    for alias in aliases:
        if alias.lower() in normalized:
            return normalized[alias.lower()]
    return None


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_price_rows(frame: pd.DataFrame, source: str = "akshare") -> list[CommodityPriceRecord]:
    """识别中英文 OHLCV 列，验证后按日期升序去重。"""
    if frame is None or frame.empty:
        raise CommoditySourceError("empty source result")
    date_col = _column(list(frame.columns), ("date", "日期", "交易日期", "时间"))
    close_col = _column(list(frame.columns), ("close", "收盘", "收盘价"))
    if date_col is None or close_col is None:
        raise CommoditySourceError(f"required date/close columns not found: {list(frame.columns)}")
    aliases = {
        "open": ("open", "开盘", "开盘价"),
        "high": ("high", "最高", "最高价"),
        "low": ("low", "最低", "最低价"),
        "volume": ("volume", "成交量", "成交"),
    }
    optional_cols = {name: _column(list(frame.columns), names) for name, names in aliases.items()}
    rows: list[CommodityPriceRecord] = []
    # AkShare returns exchange-local dates; the product's freshness contract
    # uses the next calendar day in Beijing time as the upper bound.
    latest_allowed = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    for index, row in frame.iterrows():
        parsed = pd.to_datetime(row[date_col], errors="coerce")
        if pd.isna(parsed):
            try:
                trade_date = datetime.fromisoformat(str(row[date_col]).strip().replace("/", "-")).date()
            except (TypeError, ValueError):
                raise CommoditySourceError(f"invalid date at row {index}") from None
        else:
            trade_date = parsed.date()
        if trade_date > latest_allowed:
            raise CommoditySourceError(f"future date at row {index}: {trade_date}")
        try:
            close = float(row[close_col])
        except (TypeError, ValueError):
            raise CommoditySourceError(f"invalid close at row {index}") from None
        if not math.isfinite(close) or close <= 0:
            raise CommoditySourceError(f"invalid close at row {index}: {close!r}")
        rows.append(CommodityPriceRecord(
            trade_date=trade_date,
            close=close,
            open=_optional_number(row[optional_cols["open"]]) if optional_cols["open"] is not None else None,
            high=_optional_number(row[optional_cols["high"]]) if optional_cols["high"] is not None else None,
            low=_optional_number(row[optional_cols["low"]]) if optional_cols["low"] is not None else None,
            volume=_optional_number(row[optional_cols["volume"]]) if optional_cols["volume"] is not None else None,
            source=source,
        ))
    if not rows:
        raise CommoditySourceError("empty normalized source result")
    # Python dict assignment intentionally keeps the last occurrence.
    deduped = {item.trade_date: item for item in rows}
    return [deduped[key] for key in sorted(deduped)]


FetchFn = Callable[..., pd.DataFrame]


class CommoditySourceAdapter:
    """按市场选择 AkShare 接口；fetch_fn 供测试和离线任务注入。"""

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self.fetch_fn = fetch_fn

    def fetch(self, code: str, market: str) -> list[CommodityPriceRecord]:
        normalized_market = str(market).strip().lower()
        if normalized_market in {"domestic", "china", "cn"}:
            endpoint = "futures_zh_daily_sina"
        elif normalized_market == "foreign":
            endpoint = "futures_foreign_hist"
        else:
            raise CommoditySourceError(f"unsupported market: {market}")
        if self.fetch_fn is None:
            import akshare as ak  # noqa: PLC0415

            raw = getattr(ak, endpoint)(symbol=code)
        else:
            # Resolve the supported one-argument test hook without swallowing
            # TypeError raised by the hook's own implementation.
            try:
                parameters = inspect.signature(self.fetch_fn).parameters.values()
                positional = [
                    parameter
                    for parameter in parameters
                    if parameter.kind
                    in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                ]
                accepts_varargs = any(
                    parameter.kind == inspect.Parameter.VAR_POSITIONAL
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                positional = []
                accepts_varargs = True
            raw = (
                self.fetch_fn(code, normalized_market)
                if accepts_varargs or len(positional) >= 2
                else self.fetch_fn(code)
            )
        return normalize_price_rows(raw, source=f"akshare:{endpoint}")

    def fetch_history(self, code: str, market: str) -> list[CommodityPriceRecord]:
        return self.fetch(code, market)


CommoditySource = CommoditySourceAdapter
normalize_ohlc = normalize_price_rows
