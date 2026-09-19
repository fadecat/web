# -*- coding: utf-8 -*-
"""行情研究数据源: Provider 协议与 AkShare/东方财富适配器。

设计对应 docs/superpowers/specs/2026-09-18-next-day-t-research-replay-design.md §3:
- 适配器按能力(而非厂商)声明; 不提供独立复权因子, 不合成 adj_factor;
- 规范代码(带交易所后缀)在边界剥成六位码; 股票/ETF 路由由 security_type 决定, 不靠首位猜;
- akshare 只在本模块内延迟导入(对齐 commodity_source 先例); 测试通过注入 fetch 函数隔离。

本模块是策略层与数据源的唯一边界: research_plan / research_replay / queries 不得 import akshare。
"""
from __future__ import annotations

import json
import math
import numbers
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
CAP_CORPORATE_EVENT_CALENDAR = "CORPORATE_EVENT_CALENDAR"


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
class CorporateEvent:
    """权益事件日历条目(除权除息日; 新浪 hfq.js)。

    factor=hfq 因子(信息性, 非官方复权因子, 可空);
    cumulative_dividend=累计分红(元, 仅 ETF 行有, 可空)。
    """

    event_date: date
    factor: float | None = None
    cumulative_dividend: float | None = None


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
    *, volume_factor: float | None = None,
) -> list[DailyBar]:
    """识别中英文 OHLCV 列, 校验后按日期升序去重。

    校验: 日期不得为未来; OHLC 有限且 0 < low <= min(open,close) <= max(open,close) <= high。
    volume_factor 显式覆盖按证券类型的换算(如腾讯 fqkline 股票与 ETF 均为「手」)。
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
    if volume_factor is None:
        _, volume_factor = _VOLUME_UNIT_BY_TYPE.get(security_type, ("份", 1.0))

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
    """从规范代码取交易所(仅用于后缀一致性断言, 不做品种推断)。

    场外基金形态(.OF)无交易所归属, 显式抛错, 不允许静默返回空串。
    """
    upper = str(symbol).strip().upper()
    if upper.endswith(".OF"):
        raise ResearchSourceError(f"场外基金(.OF)无交易所后缀可用: {symbol!r}")
    for suffix, exchange in _SUFFIX_EXCHANGE.items():
        if upper.endswith(suffix):
            return exchange
    return ""


def _assert_symbol_type(symbol: str, code: str, security_type: str) -> str:
    """后缀与证券类型一致性断言(不靠首位猜品种, 只拒绝明显矛盾); 返回规范化类型。"""
    normalized_type = str(security_type).strip().upper()
    if normalized_type == "FUND":
        # 场外基金: 不查交易所后缀, 只校验六位纯数字代码
        if not (len(code) == 6 and code.isdigit()):
            raise ResearchSourceError(f"FUND 代码应为 6 位纯数字: {symbol!r}")
        return "FUND"
    exchange = _symbol_exchange(symbol)
    if exchange == "SSE" and normalized_type == "ETF" and not code.startswith("5"):
        raise ResearchSourceError(f"symbol {symbol} 与 ETF 类型矛盾(沪市 ETF 应 5 开头)")
    if exchange == "SSE" and normalized_type == "STOCK" and not (code.startswith("6") or code.startswith("9")):
        raise ResearchSourceError(f"symbol {symbol} 与 STOCK 类型矛盾(沪市股票应 6/9 开头)")
    if exchange == "SZSE" and normalized_type == "ETF" and not code.startswith(("1", "0")):
        raise ResearchSourceError(f"symbol {symbol} 与 ETF 类型矛盾(深市 ETF 应 1/0 开头)")
    if exchange == "SZSE" and normalized_type == "STOCK" and not (code.startswith("0") or code.startswith("3")):
        raise ResearchSourceError(f"symbol {symbol} 与 STOCK 类型矛盾(深市股票应 0/3 开头)")
    return normalized_type


def _normalize_calendar_frame(frame: pd.DataFrame) -> list[TradeSession]:
    """新浪日历 DataFrame(sina tool_trade_date_hist) → 去重升序 TradeSession。"""
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
        normalized_type = _assert_symbol_type(symbol, code, security_type)

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
        return _normalize_calendar_frame(self._calendar_fetch_fn())


# ---------------------------------------------------------------------------
# 腾讯 fqkline + 新浪 hfq.js 适配器
#
# 换源决策记录(2026-09-18, 用户显式批准): 东财 push2* 行情 API 对阿里云 ECS IP
# 首请求后即封禁(push2his 及全部镜像子域 RST, 同域 datacenter-web/quote 不受影响),
# akshare stock_zh_a_hist / fund_etf_hist_em 走 push2his 故在 ECS 不可用。
# 腾讯 fqkline 可达性/配对/量纲/限流均已在 ECS 实测通过; 但其 hfq 比值在非事件日
# 有 0.1%~0.4% 抖动(低价高分红股), r_t 阶跃启发式不可用——权益事件检测改以
# 新浪 hfq.js 事件日历为主(股票与 ETF 统一覆盖, 已与东财官方分红表交叉验证),
# r_t 阶跃降级为宽容差 sanity 兜底。证据: data/research/poc/ + 换源验证探针报告。
# ---------------------------------------------------------------------------

TencentKlineFetchFn = Callable[..., list]
CorporateEventsFetchFn = Callable[[str], list]

# 腾讯 fqkline 单次条数上限(实测 640; 超出被静默截断, 必须分页)
TENCENT_KLINE_PAGE = 640
# 分页游标安全上限(640×30 ≈ 1.9 万根, 远超 A 股全历史; 防御游标解析失败死循环)
_TENCENT_MAX_PAGES = 30

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _tencent_code(symbol: str) -> str:
    """规范代码(600900.SH) → 腾讯代码(sh600900); 必须带交易所后缀。"""
    code = _strip_symbol_suffix(symbol)
    upper = str(symbol).strip().upper()
    if upper.endswith(".SH"):
        return f"sh{code}"
    if upper.endswith(".SZ"):
        return f"sz{code}"
    if upper.endswith(".OF"):
        raise ResearchSourceError(f"场外基金(.OF)不支持腾讯代码转换: {symbol!r}")
    raise ResearchSourceError(f"invalid symbol (expect 600900.SH form): {symbol!r}")


def _tencent_extract_rows(payload: dict[str, Any], code: str, fq: str) -> list:
    """从 fqkline 响应 data[code] 中取行; 复权键缺失时若替身键有数据直接报错(不静默退回未复权)。

    例外: 窗口内无交易日时腾讯对 hfq 请求也回 'day' 键的空列表(实测 2021-01-01..2021-01-03),
    空列表不存在口径歧义, 视为空页返回。
    """
    data = (payload or {}).get("data", {}).get(code) or {}
    key = f"{fq}day" if fq else "day"
    rows = data.get(key)
    if rows is None:
        for fallback_key in ("day", "hfqday"):
            fallback = data.get(fallback_key)
            if fallback:
                # 替身键有实际数据: 这才是要拦的静默换口径
                raise ResearchSourceError(
                    f"tencent fqkline 响应缺少 {key!r} 键但 {fallback_key!r} 有数据"
                    f"(可用键: {sorted(data)}), 拒绝静默换口径"
                )
        return []  # 全空响应(如无交易日窗口): 无口径歧义
    return rows or []


def _default_tencent_kline_fetch(code: str, fq: str, start_s: str, end_s: str, count: int) -> list:
    import requests  # noqa: PLC0415

    param = f"{code},day,{start_s},{end_s},{count},{fq}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}"
    response = requests.get(url, timeout=15, headers=_HEADERS)
    response.raise_for_status()
    return _tencent_extract_rows(response.json(), code, fq)


def _sina_parse_events(rows: list) -> list[CorporateEvent]:
    """新浪 hfq.js data 行 → CorporateEvent; 跳过 1900-01-01 哨兵行。

    行结构: {"d": 日期, "f": hfq 因子, "s": 仅 ETF 有, "u": 仅 ETF 有(累计分红)}。
    股票行只有 {d, f}; s/u 用 get 容错。
    """
    events: list[CorporateEvent] = []
    for row in rows or []:
        if not isinstance(row, dict):
            raise ResearchSourceError(f"invalid corporate event row: {row!r}")
        raw_date = str(row.get("d") or "").strip()
        if not raw_date or raw_date == "1900-01-01":  # 新浪哨兵行
            continue
        try:
            event_date = date.fromisoformat(raw_date)
        except ValueError:
            raise ResearchSourceError(f"invalid corporate event date: {raw_date!r}") from None
        events.append(CorporateEvent(
            event_date=event_date,
            factor=_optional_number(row.get("f")),
            cumulative_dividend=_optional_number(row.get("u")),
        ))
    return events


def _default_sina_events_fetch(code: str) -> list:
    import requests  # noqa: PLC0415

    url = f"https://finance.sina.com.cn/realstock/company/{code}/hfq.js"
    response = requests.get(url, timeout=15, headers=_HEADERS)
    response.raise_for_status()
    text = response.text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ResearchSourceError(f"sina hfq.js 响应无 JSON 体: {text[:80]!r}")
    payload = json.loads(text[start:end + 1])
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ResearchSourceError("sina hfq.js 响应缺少 data 数组")
    return rows


class TencentFqklineProvider:
    """腾讯 fqkline 日线 + 新浪 hfq.js 权益事件日历适配器。

    - 日线: raw('')/hfq 两模式, 640 条/页向后游标分页;
      行序为 [date, open, close, high, low, volume(手)](注意 close 在第 2 列!);
      股票与 ETF 成交量均为「手」, 统一 ×100 → 股/份(已实测);
    - 日历: 复用新浪 tool_trade_date_hist_sina(与 akshare 适配器同源);
    - 事件: 新浪 hfq.js(股票与 ETF 统一), 作为权益事件主检测来源。
    构造器注入 fetch 函数供测试隔离; 默认实现内部延迟 import requests。
    """

    def __init__(
        self,
        *,
        kline_fetch_fn: TencentKlineFetchFn | None = None,
        events_fetch_fn: CorporateEventsFetchFn | None = None,
        calendar_fetch_fn: CalendarFetchFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
        page_size: int = TENCENT_KLINE_PAGE,
        page_gap: float = 0.5,
    ) -> None:
        self.name = "tencent-fqkline"
        self.capabilities = frozenset({
            CAP_RAW_DAILY_BAR, CAP_ADJUSTED_DAILY_BAR_HFQ, CAP_TRADE_CALENDAR,
            CAP_CORPORATE_EVENT_CALENDAR,
        })
        self._kline_fetch_fn = kline_fetch_fn or _default_tencent_kline_fetch
        self._events_fetch_fn = events_fetch_fn or _default_sina_events_fetch
        self._calendar_fetch_fn = calendar_fetch_fn or _default_calendar_fetch
        self._sleep = sleep
        self._page_size = int(page_size)
        self._page_gap = float(page_gap)

    def get_daily_bars(
        self, symbol: str, start: date, end: date, adjust_mode: AdjustMode,
        *, security_type: str = "STOCK",
    ) -> list[DailyBar]:
        tencent_code = _tencent_code(symbol)
        normalized_type = _assert_symbol_type(symbol, tencent_code[2:], security_type)
        if normalized_type not in ("STOCK", "ETF"):
            raise ResearchSourceError(f"unsupported security_type: {security_type!r}")
        fq = "" if adjust_mode is AdjustMode.RAW else "hfq"

        # 向后游标分页: 每页返回窗口内最新 page_size 条(升序), 游标逐页前移到该页最早日期的前一天
        rows_by_date: dict[str, list] = {}
        end_cursor = end
        pages = 0
        while pages < _TENCENT_MAX_PAGES:
            rows = self._kline_fetch_fn(
                tencent_code, fq, start.isoformat(), end_cursor.isoformat(), self._page_size,
            )
            pages += 1
            if not rows:
                break
            for row in rows:
                if row and row[0] is not None:
                    rows_by_date[str(row[0])] = row
            # 页行数不足 = 窗口已取尽(腾讯回窗口内最新 count 条), 不再发尾窗请求
            # (尾窗常为无交易日区间, 徒增一次空请求)
            if len(rows) < self._page_size:
                break
            try:
                first_date = date.fromisoformat(str(rows[0][0]))
            except ValueError:
                break  # 游标不可解析: 停止翻页, 交由 normalize 校验已收行
            end_cursor = first_date - timedelta(days=1)
            if end_cursor < start:
                break
            self._sleep(self._page_gap)

        # 行序 [date, open, close, high, low, volume] → 英文列 DataFrame 走统一校验
        records = []
        for key in sorted(rows_by_date):
            row = rows_by_date[key]
            records.append({
                "date": row[0], "open": row[1], "close": row[2],
                "high": row[3], "low": row[4],
                "volume": row[5] if len(row) > 5 and row[5] not in (None, "") else None,
            })
        if not records:
            raise ResearchSourceError("empty source result")
        frame = pd.DataFrame(records)
        bars = normalize_research_bars(
            frame, source="tencent:fqkline", security_type=normalized_type,
            volume_factor=100.0,  # 腾讯股票与 ETF 成交量均为「手」(×100 → 股/份, 已实测)
        )
        filtered = [bar for bar in bars if start <= bar.trade_date <= end]
        if not filtered:
            raise ResearchSourceError(f"tencent fqkline 区间内无数据: {start} ~ {end}")
        self._sleep(2.0)  # 标的×模式之间的礼貌间隔(与 akshare 适配器一致)
        return filtered

    def get_trade_calendar(self) -> list[TradeSession]:
        return _normalize_calendar_frame(self._calendar_fetch_fn())

    def get_corporate_events(self, symbol: str) -> list[CorporateEvent]:
        """新浪 hfq.js 权益事件日历(股票与 ETF 统一)。"""
        tencent_code = _tencent_code(symbol)
        events = _sina_parse_events(self._events_fetch_fn(tencent_code))
        self._sleep(2.0)
        return events


def provider_factory(source: str, **kwargs: Any) -> MarketDataProvider:
    """标的级数据来源路由: 未知来源直接报错, 不静默换源。"""
    normalized = str(source).strip().lower()
    if normalized == "akshare":
        return AkShareEastmoneyProvider(**kwargs)
    if normalized == "tencent":
        return TencentFqklineProvider(**kwargs)
    raise ResearchSourceError(f"未知研究数据来源: {source!r}(不静默回退到其他来源)")
