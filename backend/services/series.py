# -*- coding: utf-8 -*-
"""统一序列层(组合实验室 P2): 把三类资产的可回测序列收敛成一个契约。

这是整个结构里**唯一的新抽象**(docs/portfolio-lab-flow.md §三), 其余都是拼装。

为什么需要它: 三类资产的"可回测序列"来源与口径都不同, 混用会**静默出错**。
把口径显式挂在返回值上, UI 可以逐列标注, 归因也能追溯。

| 资产 | 来源表 | 取值列 | price_basis |
| --- | --- | --- | --- |
| 股票 / ETF | `research_daily_bar_adjusted` | `close`(adjust_mode=HFQ) | `HFQ` |
| 场外基金 | `fund_nav_daily` | `adj_nav`(分红再投链式) | `NAV_ADJ` |

⚠ **本层必须分支读两张表** —— 这是"场外基金单独用表"的代价(用户 2026-09-19 裁决),
统一放在这里而不是抓取层硬凑 bar 协议。

⚠ **`settle_lag` 与成交口径**: `flow.md` §三 原写"场外基金 = 1(T+1)", 但**红线项 D2
已裁决为「当日净值成交」**(实测 T+1 会让收益偏 0.6~2.8pp)。故本层三类资产一律
`settle_lag = 0`(用同日值)。⚠ 未来做**估值择时**(决策依赖当日净值)时必须改为 T+1。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.research import FundNavDaily, ResearchDailyBarAdjusted, ResearchSecurity

HFQ = "HFQ"
NAV_ADJ = "NAV_ADJ"

# 资产类别(SeriesContract.asset_class 取值)
STOCK = "stock"
ETF = "etf"
FUND = "fund"

# 成交滞后: D2 裁决"当日净值/当日价成交" → 三类均为 0
# (⚠ 与 flow.md §三 的旧表述冲突, 以红线项 D2 为准; 见模块 docstring)
SETTLE_LAG_SAME_DAY = 0


class SeriesError(ValueError):
    """序列层错误(未注册标的 / 无数据 / 口径未知)。"""


@dataclass(frozen=True)
class SeriesContract:
    """一个标的的可回测序列(已按日期升序, 无重复日)。"""

    symbol: str
    asset_class: str          # stock | etf | fund
    price_basis: str          # HFQ | NAV_ADJ
    settle_lag: int           # 见模块 docstring; 当前恒 0(D2)
    dates: tuple[date, ...]
    prices: tuple[float, ...]  # 与 dates 一一对应的可比价格(复权净值 / 后复权价)

    def __post_init__(self) -> None:
        # 两个平行元组最容易出"少个逗号 → float 被当成序列"或长度不齐这类隐蔽错误,
        # 一旦错位就是"价格串到别人的日期上", 后果比报错严重得多 → 直接 fail-fast。
        if not isinstance(self.dates, tuple) or not isinstance(self.prices, tuple):
            raise SeriesError(
                f"dates/prices 必须是 tuple: {self.symbol!r}"
                f"(收到 dates={type(self.dates).__name__}, prices={type(self.prices).__name__};"
                " 单元素元组要写 (x,))"
            )
        if len(self.dates) != len(self.prices):
            raise SeriesError(
                f"dates/prices 长度不一致: {self.symbol!r}"
                f"({len(self.dates)} 个日期 vs {len(self.prices)} 个价格)"
            )
        if any(b <= a for a, b in zip(self.dates, self.dates[1:])):
            raise SeriesError(f"dates 必须严格升序且无重复: {self.symbol!r}")

    @property
    def first_date(self) -> date | None:
        return self.dates[0] if self.dates else None

    @property
    def last_date(self) -> date | None:
        return self.dates[-1] if self.dates else None

    @property
    def row_count(self) -> int:
        return len(self.dates)

    def price_map(self) -> dict[date, float]:
        return dict(zip(self.dates, self.prices))

    def slice(self, start: date | None, end: date | None) -> "SeriesContract":
        """按区间裁剪(闭区间); 越界返回空序列而不是报错。"""
        lo = start or self.first_date
        hi = end or self.last_date
        if lo is None or hi is None:
            return self
        pairs = [(d, p) for d, p in zip(self.dates, self.prices) if lo <= d <= hi]
        return SeriesContract(
            symbol=self.symbol, asset_class=self.asset_class, price_basis=self.price_basis,
            settle_lag=self.settle_lag,
            dates=tuple(d for d, _ in pairs), prices=tuple(p for _, p in pairs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "price_basis": self.price_basis,
            "settle_lag": self.settle_lag,
            "row_count": self.row_count,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
        }


# ---------------------------------------------------------------------------
# 取数: 按 security_type 分支读两张表
# ---------------------------------------------------------------------------

def resolve_asset_class(db: Session, symbol: str) -> str:
    """规范代码 → 资产类别; 以 `research_security` 为权威, 未注册则按后缀兜底。"""
    security = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == symbol))
    if security is not None:
        mapped = {"STOCK": STOCK, "ETF": ETF, "FUND": FUND}.get(
            str(security.security_type).strip().upper()
        )
        if mapped is None:
            raise SeriesError(f"未知的 security_type: {security.security_type!r}({symbol})")
        return mapped
    upper = str(symbol).strip().upper()
    if upper.endswith(".OF"):
        return FUND
    if upper.endswith((".SH", ".SZ")):
        # 未注册时无法区分个股与 ETF(都走同一张 bar 表), 报 stock 不影响取值口径
        return STOCK
    raise SeriesError(f"无法判定资产类别: {symbol!r}(未注册且后缀不可识别)")


def _fund_series(
    db: Session, symbol: str, start: date | None, end: date | None,
) -> SeriesContract:
    stmt = select(FundNavDaily.nav_date, FundNavDaily.adj_nav).where(
        FundNavDaily.symbol == symbol,
    )
    if start is not None:
        stmt = stmt.where(FundNavDaily.nav_date >= start)
    if end is not None:
        stmt = stmt.where(FundNavDaily.nav_date <= end)
    rows = db.execute(stmt.order_by(FundNavDaily.nav_date)).all()
    return SeriesContract(
        symbol=symbol, asset_class=FUND, price_basis=NAV_ADJ, settle_lag=SETTLE_LAG_SAME_DAY,
        dates=tuple(r[0] for r in rows), prices=tuple(float(r[1]) for r in rows),
    )


def _bar_series(
    db: Session, symbol: str, asset_class: str, start: date | None, end: date | None,
) -> SeriesContract:
    stmt = select(ResearchDailyBarAdjusted.trade_date, ResearchDailyBarAdjusted.close).where(
        ResearchDailyBarAdjusted.symbol == symbol,
        ResearchDailyBarAdjusted.adjust_mode == HFQ,
    )
    if start is not None:
        stmt = stmt.where(ResearchDailyBarAdjusted.trade_date >= start)
    if end is not None:
        stmt = stmt.where(ResearchDailyBarAdjusted.trade_date <= end)
    rows = db.execute(stmt.order_by(ResearchDailyBarAdjusted.trade_date)).all()
    return SeriesContract(
        symbol=symbol, asset_class=asset_class, price_basis=HFQ, settle_lag=SETTLE_LAG_SAME_DAY,
        dates=tuple(r[0] for r in rows), prices=tuple(float(r[1]) for r in rows),
    )


def get_series(
    db: Session, symbol: str, start: date | None = None, end: date | None = None,
) -> SeriesContract:
    """统一取数入口: 返回该类资产的可回测序列(区间为闭区间, 可为 None 表示不限)。

    未入库的标的返回**空序列**(而不是报错) —— 调用方(组合页的数据就绪检查)
    需要能区分"没数据"与"出错", 并据此把问题摆出来给一个「立即同步」按钮。
    """
    asset_class = resolve_asset_class(db, symbol)
    if asset_class == FUND:
        return _fund_series(db, symbol, start, end)
    return _bar_series(db, symbol, asset_class, start, end)


# ---------------------------------------------------------------------------
# 交易日对齐: 并集 + 前值填充
# ---------------------------------------------------------------------------

def align_union(
    contracts: list[SeriesContract], *, fill_forward: bool = True,
) -> tuple[tuple[date, ...], dict[str, tuple[float | None, ...]]]:
    """多序列对齐: 取日期**并集**, 各序列在缺失日按**前值填充**。

    为什么用并集(docs/portfolio-lab-verification.md): 实测并集 3267 天 vs 交集 3180 天,
    差的 87 天全是境外假期(QDII 净值日期滞后)。用交集会丢掉这些交易日, 用并集+前值填充
    才是"组合在这些天各标的的最新可用值"。

    返回 (对齐后的日期序列, {symbol: 各日价格}); 某序列在首个可用日**之前**为 None
    (不填充"未来值"), 调用方据此决定共同起点(T0 = 各序列首个可用日的最大值)。
    """
    if not contracts:
        return (), {}
    all_dates = sorted({d for c in contracts for d in c.dates})
    if not all_dates:
        return (), {}

    aligned: dict[str, tuple[float | None, ...]] = {}
    for contract in contracts:
        price_map = contract.price_map()
        series: list[float | None] = []
        last: float | None = None  # 首个可用日之前保持 None(不填充未来值)
        for day in all_dates:
            value = price_map.get(day)
            if value is not None:
                last = value
            series.append(last if fill_forward else value)
        aligned[contract.symbol] = tuple(series)
    return tuple(all_dates), aligned


def common_start(contracts: list[SeriesContract]) -> date | None:
    """组合的共同起点 T0 = 各序列**首个可用日**的最大值(最晚那个)。

    这是全项目的建仓日口径(docs/portfolio-lab-index.md §三-1):
    ⚠ 不引入"成立日/上市日"外部元数据 —— 一律以**数据可得区间**为准。
    """
    firsts = [c.first_date for c in contracts if c.first_date is not None]
    return max(firsts) if firsts else None
