# -*- coding: utf-8 -*-
"""行情研究数据源: Provider 协议与 AkShare/东方财富适配器。

设计对应 docs/superpowers/specs/2026-09-18-next-day-t-research-replay-design.md §3:
- 适配器按能力(而非厂商)声明; 不提供独立复权因子, 不合成 adj_factor;
- 规范代码(带交易所后缀)在边界剥成六位码; 股票/ETF 路由由 security_type 决定, 不靠首位猜;
- akshare 只在本模块内延迟导入(对齐 commodity_source 先例); 测试通过注入 fetch 函数隔离。

本模块是策略层与数据源的唯一边界: research_plan / research_replay / queries 不得 import akshare。
"""
from __future__ import annotations

import math
import numbers
import time
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

# ---------------------------------------------------------------------------
# 能力声明与规范化记录
# ---------------------------------------------------------------------------

CAP_RAW_DAILY_BAR = "RAW_DAILY_BAR"
CAP_ADJUSTED_DAILY_BAR_HFQ = "ADJUSTED_DAILY_BAR_HFQ"
CAP_TRADE_CALENDAR = "TRADE_CALENDAR"


class AdjustMode(str, Enum):
    """复权模式: RAW=未复权交易价, HFQ=后复权连续研究价格。"""

    RAW = "RAW"
    HFQ = "HFQ"

    @property
    def akshare_adjust(self) -> str:
        return "" if self is AdjustMode.RAW else "hfq"


class ResearchSourceError(ValueError):
    """源数据无法安全入库。"""


@dataclass(frozen=True)
class DailyBar:
    """规范化日线记录(已过 OHLC 合法性校验)。"""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None   # 统一为 股/份(股票手×100, ETF 份原值)
    amount: float | None = None   # 统一为 元
    volume_unit: str = "share"
    source: str = ""


@dataclass(frozen=True)
class TradeSession:
    """交易日历条目。"""

    trade_date: date
    is_open: bool


@dataclass(frozen=True)
class SecurityInfo:
    """研究标的元信息(来自 yaml 清单, 非独立 SECURITY_MASTER 能力)。"""

    symbol: str
    security_type: str  # STOCK | ETF


# ---------------------------------------------------------------------------
# Provider 协议
# ---------------------------------------------------------------------------

class MarketDataProvider(Protocol):
    """按能力声明提供行情数据; 不含任何指标/策略逻辑。"""

    name: str
    capabilities: frozenset[str]

    def get_daily_bars(
        self, symbol: str, start: date, end: date, adjust_mode: AdjustMode,
        *, security_type: str = "STOCK",
    ) -> list[DailyBar]: ...

    def get_trade_calendar(self) -> list[TradeSession]: ...


# ---------------------------------------------------------------------------
# 规范化(独立于 commodity_source 演化, 逻辑同源但拷贝不 import)
# ---------------------------------------------------------------------------

def _column(columns: list[Any], aliases: tuple[str, ...]) -> Any | None:
    normalized = {str(col).strip().lower(): col for col in columns}
    for alias in aliases:
        if alias.lower() in normalized:
            return normalized[alias.lower()]
    return None


def _required_number(value: Any, field: str, index: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ResearchSourceError(f"invalid {field} at row {index}") from None
    if not math.isfinite(number):
        raise ResearchSourceError(f"non-finite {field} at row {index}")
    return number


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_trade_date(value: Any, index: Any) -> date:
    """解析日期, 不让数值被当成 Unix 时间戳(对齐 commodity_source 的防护)。"""
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ResearchSourceError(f"invalid date at row {index}")
        digits = str(int(numeric))
        if len(digits) != 8:
            raise ResearchSourceError(f"invalid numeric date at row {index}")
        try:
            return datetime.strptime(digits, "%Y%m%d").date()
        except ValueError:
            raise ResearchSourceError(f"invalid date at row {index}") from None
    parsed = pd.to_datetime(value, errors="coerce")
    if not pd.isna(parsed):
        return parsed.date()
    try:
        return datetime.fromisoformat(str(value).strip().replace("/", "-")).date()
    except (TypeError, ValueError):
        raise ResearchSourceError(f"invalid date at row {index}") from None


# 东财接口股票/ETF 的成交量单位不同: 股票为"手", ETF 为"份"。
_VOLUME_UNIT_BY_TYPE = {"STOCK": ("手", 100.0), "ETF": ("份", 1.0)}


def normalize_research_bars(
    frame: pd.DataFrame, source: str, security_type: str,
) -> list[DailyBar]:
    """识别中英文 OHLCV 列, 校验后按日期升序去重。

    校验: 日期不得为未来; OHLC 有限且 0 < low <= min(open,close) <= max(open,close) <= high。
    """
    if frame is None or frame.empty:
        raise ResearchSourceError("empty source result")
    date_col = _column(list(frame.columns), ("date", "日期", "交易日期", "时间"))
    open_col = _column(list(frame.columns), ("open", "开盘", "开盘价"))
    high_col = _column(list(frame.columns), ("high", "最高", "最高价"))
    low_col = _column(list(frame.columns), ("low", "最低", "最低价"))
    close_col = _column(list(frame.columns), ("close", "收盘", "收盘价"))
    missing = [
        name for name, col in (
            ("date", date_col), ("open", open_col), ("high", high_col),
            ("low", low_col), ("close", close_col),
        ) if col is None
    ]
    if missing:
        raise ResearchSourceError(f"required columns not found {missing}: {list(frame.columns)}")
    volume_col = _column(list(frame.columns), ("volume", "成交量", "成交"))
    amount_col = _column(list(frame.columns), ("amount", "成交额"))
    unit_label, volume_factor = _VOLUME_UNIT_BY_TYPE.get(security_type, ("份", 1.0))

    latest_allowed = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rows: list[DailyBar] = []
    for index, row in frame.iterrows():
        trade_date = _parse_trade_date(row[date_col], index)
        if trade_date > latest_allowed:
            raise ResearchSourceError(f"future date at row {index}: {trade_date}")
        open_ = _required_number(row[open_col], "open", index)
        high = _required_number(row[high_col], "high", index)
        low = _required_number(row[low_col], "low", index)
        close = _required_number(row[close_col], "close", index)
        if not (0 < low <= min(open_, close) <= max(open_, close) <= high):
            raise ResearchSourceError(
                f"invalid OHLC at row {index}: o={open_!r} h={high!r} l={low!r} c={close!r}"
            )
        raw_volume = _optional_number(row[volume_col]) if volume_col is not None else None
        volume = raw_volume * volume_factor if raw_volume is not None else None
        rows.append(DailyBar(
            trade_date=trade_date,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            amount=_optional_number(row[amount_col]) if amount_col is not None else None,
            volume_unit="share",  # 已按证券类型换算成 股/份, 显式标记
            source=source,
        ))
    if not rows:
        raise ResearchSourceError("empty normalized source result")
    # Python dict 赋值有意保留最后一次出现(与 commodity_source 一致)。
    deduped = {item.trade_date: item for item in rows}
    return [deduped[key] for key in sorted(deduped)]


# ---------------------------------------------------------------------------
# AkShare/东方财富适配器
# ---------------------------------------------------------------------------

StockFetchFn = Callable[..., pd.DataFrame]
EtfFetchFn = Callable[..., pd.DataFrame]
CalendarFetchFn = Callable[..., pd.DataFrame]


def _default_stock_fetch(symbol: str, period: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    import akshare as ak  # noqa: PLC0415

    return ak.stock_zh_a_hist(
        symbol=symbol, period=period, start_date=start_date, end_date=end_date, adjust=adjust,
    )


def _default_etf_fetch(symbol: str, period: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    import akshare as ak  # noqa: PLC0415

    return ak.fund_etf_hist_em(
        symbol=symbol, period=period, start_date=start_date, end_date=end_date, adjust=adjust,
    )


def _default_calendar_fetch() -> pd.DataFrame:
    import akshare as ak  # noqa: PLC0415

    return ak.tool_trade_date_hist_sina()


def _strip_symbol_suffix(symbol: str) -> str:
    """规范代码(600900.SH / 001286.SZ) → 六位码; 已是六位码则原样返回。"""
    code = str(symbol).strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    if not (len(code) == 6 and code.isdigit()):
        raise ResearchSourceError(f"invalid symbol (expect 600900.SH form): {symbol!r}")
    return code


_SUFFIX_EXCHANGE = {".SH": "SSE", ".SZ": "SZSE"}


def _symbol_exchange(symbol: str) -> str:
    """从规范代码取交易所(仅用于后缀一致性断言, 不做品种推断)。"""
    upper = str(symbol).strip().upper()
    for suffix, exchange in _SUFFIX_EXCHANGE.items():
        if upper.endswith(suffix):
            return exchange
    return ""


class AkShareEastmoneyProvider:
    """东方财富 K 线适配器(AkShare 封装)。

    构造器注入 fetch 函数供测试隔离; 默认实现内部延迟 import akshare。
    """

    def __init__(
        self,
        *,
        stock_fetch_fn: StockFetchFn | None = None,
        etf_fetch_fn: EtfFetchFn | None = None,
        calendar_fetch_fn: CalendarFetchFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = "akshare-eastmoney"
        self.capabilities = frozenset({
            CAP_RAW_DAILY_BAR, CAP_ADJUSTED_DAILY_BAR_HFQ, CAP_TRADE_CALENDAR,
        })
        self._stock_fetch_fn = stock_fetch_fn or _default_stock_fetch
        self._etf_fetch_fn = etf_fetch_fn or _default_etf_fetch
        self._calendar_fetch_fn = calendar_fetch_fn or _default_calendar_fetch
        self._sleep = sleep

    def get_daily_bars(
        self, symbol: str, start: date, end: date, adjust_mode: AdjustMode,
        *, security_type: str = "STOCK",
    ) -> list[DailyBar]:
        code = _strip_symbol_suffix(symbol)
        normalized_type = str(security_type).strip().upper()
        # 后缀与证券类型一致性断言(不靠首位猜品种, 只拒绝明显矛盾)。
        exchange = _symbol_exchange(symbol)
        if exchange == "SSE" and normalized_type == "ETF" and not code.startswith("5"):
            raise ResearchSourceError(f"symbol {symbol} 与 ETF 类型矛盾(沪市 ETF 应 5 开头)")
        if exchange == "SSE" and normalized_type == "STOCK" and not (code.startswith("6") or code.startswith("9")):
            raise ResearchSourceError(f"symbol {symbol} 与 STOCK 类型矛盾(沪市股票应 6/9 开头)")
        if exchange == "SZSE" and normalized_type == "ETF" and not code.startswith(("1", "0")):
            raise ResearchSourceError(f"symbol {symbol} 与 ETF 类型矛盾(深市 ETF 应 1/0 开头)")
        if exchange == "SZSE" and normalized_type == "STOCK" and not (code.startswith("0") or code.startswith("3")):
            raise ResearchSourceError(f"symbol {symbol} 与 STOCK 类型矛盾(深市股票应 0/3 开头)")

        if normalized_type == "STOCK":
            fetch, endpoint = self._stock_fetch_fn, "stock_zh_a_hist"
        elif normalized_type == "ETF":
            fetch, endpoint = self._etf_fetch_fn, "fund_etf_hist_em"
        else:
            raise ResearchSourceError(f"unsupported security_type: {security_type!r}")

        frame = fetch(
            symbol=code, period="daily",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust=adjust_mode.akshare_adjust,
        )
        bars = normalize_research_bars(frame, source=f"akshare:{endpoint}", security_type=normalized_type)
        self._sleep(2.0)  # 标的×模式之间的礼貌间隔
        return bars

    def get_trade_calendar(self) -> list[TradeSession]:
        frame = self._calendar_fetch_fn()
        if frame is None or frame.empty:
            raise ResearchSourceError("empty trade calendar result")
        date_col = _column(list(frame.columns), ("trade_date", "日期", "交易日", "date"))
        if date_col is None:
            raise ResearchSourceError(f"calendar date column not found: {list(frame.columns)}")
        sessions: list[TradeSession] = []
        for index, row in frame.iterrows():
            sessions.append(TradeSession(trade_date=_parse_trade_date(row[date_col], index), is_open=True))
        if not sessions:
            raise ResearchSourceError("empty normalized calendar result")
        # 去重升序
        deduped = {s.trade_date: s for s in sessions}
        return [deduped[key] for key in sorted(deduped)]


def provider_factory(source: str, **kwargs: Any) -> MarketDataProvider:
    """标的级数据来源路由: 未知来源直接报错, 不静默换源。"""
    normalized = str(source).strip().lower()
    if normalized == "akshare":
        return AkShareEastmoneyProvider(**kwargs)
    raise ResearchSourceError(f"未知研究数据来源: {source!r}(不静默回退到其他来源)")
