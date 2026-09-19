# -*- coding: utf-8 -*-
"""组合回测引擎(P3): 份额法账本 + 三种再平衡 + 区间收益/回撤/相关性/指标。

⚠ **本文件是 `scripts/verify_against_funddb.py` 的忠实移植**（该脚本已对韭圈儿基准组合
286241 逐项核对 **6 类全 PASS**）。移植时**不"顺手优化"任何一处对齐规则** —— 那些规则是实测
拟合出来的，改一处数字就对不上。逐条对应关系见各函数 docstring。

核心口径（docs/portfolio-lab-index.md §三 速查 1~12）:
- T0 = 各标的**数据可得区间**的共同起点（= 各首个可用日的最大值）。
  ⚠ **不引入"成立日/上市日"外部元数据**（用户裁决）；代价是「成立来」与韭圈儿差 1.27pp（口径差异）。
- 复权价 **在 T0 归一为 1.0** → 建仓份额 = 初始权重（等额本金）。
- `NAV(t) = Σ n_i × P_i(t)`；交易日取**并集 + 前值填充**。
- **区间查询不重置权重**（取序列两点比值）。
- 再平衡：`REBAL_ALIGN = {'quarterly': 'next', 'yearly': 'prev'}`（实测拟合，非官方规则）。
- 交易成本：不计。
- ⚠ **近1日 ≠ 账本末两日之比**：它是 `Σ wᵢ × rᵢ(各自最新可用日)` 的**合成口径**（QDII 补偿）。
- ⚠ **相关性的区间与对齐独立于回测**：用日涨跌幅、pairwise **交集**、区间另传。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

from backend.services.series import SeriesContract

# 三种再平衡(韭圈儿无月/阈值选项)
REBALANCE_NONE = "none"
REBALANCE_QUARTERLY = "quarterly"
REBALANCE_YEARLY = "yearly"
REBALANCE_MODES = (REBALANCE_NONE, REBALANCE_QUARTERLY, REBALANCE_YEARLY)

# 调仓日对齐方式(实测拟合: 季平衡向后顺延最优; 年平衡必须向前对齐才复现)
REBAL_ALIGN = {REBALANCE_QUARTERLY: "next", REBALANCE_YEARLY: "prev"}
# 季平衡 = 1/4/7/10 月 1 日; 年平衡 = 1 月 1 日(向前对齐 → 上年末最后交易日)
REBAL_MONTHS = {REBALANCE_QUARTERLY: (1, 4, 7, 10), REBALANCE_YEARLY: (1,)}


class BacktestError(ValueError):
    """回测前置条件不满足(日线不足 / 权重非法 / 无共同起点)。"""


# ---------------------------------------------------------------------------
# 账本
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ledger:
    """份额法账本结果。`nav` 与 `dates` 一一对应(已按日期升序)。"""

    dates: tuple[date, ...]
    nav: tuple[float, ...]
    final_weights: dict[str, float]   # 末端**漂移权重**(%), = 页面「当前占比」
    latest_prices: dict[str, float]   # 各标的归一副权价的最新值
    # 每次调仓: (调仓日, 该次换手 %)。**不平衡时为空元组**。
    # 页面「累计换手」= 本字段求和(规格 §二-⑤: Σ|Δw|, 零成本口径下不参与收益计算)。
    rebalances: tuple[tuple[date, float], ...] = ()

    @property
    def series(self) -> tuple[tuple[date, float], ...]:
        return tuple(zip(self.dates, self.nav))


def normalize_on_t0(contract: SeriesContract, t0: date) -> dict[date, float]:
    """复权价**在 T0 归一为 1.0**(只保留 T0 及之后的点)。

    p0 取 `<= t0` 的最后一个点(前值填充语义): t0 恰好是某标的的交易日时就是它自己,
    否则是该标的在 t0 时的最新可用值 —— 与账本"并集 + 前值填充"一致。
    """
    p0 = None
    for d, p in zip(contract.dates, contract.prices):
        if d <= t0:
            p0 = p
        else:
            break
    if p0 in (None, 0):
        return {}
    return {d: p / p0 for d, p in zip(contract.dates, contract.prices) if d >= t0}


def _pick_prev(dates: list[date], target: date) -> date | None:
    """<= target 的最后一个交易日(向前对齐)。"""
    found = None
    for d in dates:
        if d <= target:
            found = d
        else:
            break
    return found


def _pick_next(dates: list[date], target: date) -> date | None:
    """>= target 的第一个交易日(向后顺延)。"""
    for d in dates:
        if d >= target:
            return d
    return None


def rebalance_dates(dates: list[date], rebalance: str, t0: date) -> set[date]:
    """调仓日集合(仅季/年; 不平衡返回空集)。"""
    if rebalance == REBALANCE_NONE:
        return set()
    if rebalance not in REBAL_ALIGN:
        raise BacktestError(f"未知再平衡方式: {rebalance!r}(支持 {', '.join(REBALANCE_MODES)})")
    pick = _pick_next if REBAL_ALIGN[rebalance] == "next" else _pick_prev
    out: set[date] = set()
    for year in range(t0.year, dates[-1].year + 1):
        for month in REBAL_MONTHS[rebalance]:
            hit = pick(dates, date(year, month, 1))
            if hit is not None:
                out.add(hit)
    return out


def run_ledger(
    contracts: Mapping[str, SeriesContract],
    weights: Mapping[str, float],
    rebalance: str = REBALANCE_NONE,
) -> Ledger:
    """份额法账本: `n_i = w_i / P_i(T0)`、`NAV(t) = Σ n_i × P_i(t)`。

    因为复权价已按 T0 归一为 1.0, 建仓份额**就等于初始权重**(等额本金)。
    调仓在**当日 NAV 结算之后**执行: `n_i = NAV × w_i / P_i(当日)` —— 即拉回初始比例。
    """
    symbols = list(weights)
    if not symbols:
        raise BacktestError("组合没有标的")
    if any(w is None or w < 0 for w in weights.values()):
        raise BacktestError("权重必须为非负数且不得为空")
    total_weight = sum(weights.values())
    if abs(total_weight - 100.0) > 0.01:
        raise BacktestError(f"权重合计必须为 100%（当前 {total_weight:.2f}%）—— 无现金腿")

    t0 = max(
        (c.first_date for c in contracts.values() if c.first_date is not None),
        default=None,
    )
    if t0 is None:
        raise BacktestError("没有任何标的可用数据")

    adj: dict[str, dict[date, float]] = {}
    for symbol in symbols:
        contract = contracts.get(symbol)
        if contract is None or contract.row_count == 0:
            raise BacktestError(f"标的无数据: {symbol}")
        series = normalize_on_t0(contract, t0)
        if not series:
            raise BacktestError(f"标的在共同起点 {t0} 之前没有可用值: {symbol}")
        adj[symbol] = series

    dates = sorted({d for s in adj.values() for d in s})
    # ⚠ 份额计算**必须用小数权重**(0.25)而不是百分比(25.0): 复权价已按 T0 归一为 1.0,
    # 所以建仓份额在数值上等于"权重占组合的比例"。调仓公式 `n = NAV × w / P` 若把 25.0
    # 当份额用, 每调一次仓就差 100 倍 → 季/年平衡的收益会指数级爆炸(移植时实测踩到)。
    weight_fraction = {s: float(weights[s]) / 100.0 for s in symbols}
    shares = dict(weight_fraction)
    rebal = rebalance_dates(dates, rebalance, t0)

    latest: dict[str, float] = {}
    nav_series: list[tuple[date, float]] = []
    rebalances: list[tuple[date, float]] = []
    for day in dates:
        for symbol in symbols:
            value = adj[symbol].get(day)
            if value is not None:
                latest[symbol] = value
        if len(latest) < len(symbols):
            continue  # 还没凑齐所有标的(理论上 T0 之后不会发生)
        nav = sum(shares[s] * latest[s] for s in symbols)
        nav_series.append((day, nav))
        if day in rebal:  # 拉回初始比例
            # 换手 = Σ|Δw|(规格 §二-⑤): 调仓**前**的漂移权重与目标权重之差的绝对值之和
            before = {s: shares[s] * latest[s] / nav * 100.0 for s in symbols}
            shares = {s: nav * weight_fraction[s] / latest[s] for s in symbols}
            rebalances.append((
                day,
                sum(abs(weight_fraction[s] * 100.0 - before[s]) for s in symbols),
            ))

    if not nav_series:
        raise BacktestError("账本为空（共同起点之后没有可用交易日）")
    last_nav = nav_series[-1][1]
    final_weights = {
        s: shares[s] * latest[s] / last_nav * 100.0 for s in symbols
    }
    return Ledger(
        dates=tuple(d for d, _ in nav_series),
        nav=tuple(v for _, v in nav_series),
        final_weights=final_weights,
        latest_prices=dict(latest),
        rebalances=tuple(rebalances),
    )


# ---------------------------------------------------------------------------
# 区间指标
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowReturn:
    value: float           # 区间收益(%) —— **不重置权重**, 取两点比值
    actual_start: date
    actual_end: date


def nav_at(ledger: Ledger, target: date) -> tuple[date, float] | None:
    """取 `<= target` 的最后一个 NAV 点(向前对齐)。"""
    hit = None
    for d, v in ledger.series:
        if d <= target:
            hit = (d, v)
        else:
            break
    return hit


def window_return(ledger: Ledger, start_ref: date, end_ref: date) -> WindowReturn | None:
    """区间收益: 两端都**向前对齐**到交易日, 取两点比值(规格 §三-6: 不重置权重)。

    ⚠ 起点早于建仓日 T0(即数据历史短于该区间长度)时**退化为账本首点** —— 语义是
      "自建仓以来的这段时间", 实际起点由 `actual_start` 如实回显, 而不是留一个空值。
      例: T0=2024-06-01 的组合, "近3年" 实际就是"自建仓以来"。
    """
    start = nav_at(ledger, start_ref)
    if start is None and ledger.dates:
        start = (ledger.dates[0], ledger.nav[0])
    end = nav_at(ledger, end_ref)
    if start is None or end is None or start[1] == 0:
        return None
    return WindowReturn(
        value=(end[1] / start[1] - 1.0) * 100.0, actual_start=start[0], actual_end=end[0],
    )


def max_drawdown(ledger: Ledger, start_ref: date, end_ref: date) -> float | None:
    """区间最大回撤(%); 峰从**窗口首值**起算。"""
    window = [(d, v) for d, v in ledger.series if start_ref <= d <= end_ref]
    if len(window) < 2:
        return None
    peak = window[0][1]
    mdd = 0.0
    for _, value in window:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return mdd * 100.0


def latest_day_composite(
    contracts: Mapping[str, SeriesContract], weights_now: Mapping[str, float],
) -> float | None:
    """近1日 = `Σ wᵢ × rᵢ(各自最新可用日)` —— **合成口径**, 不是账本末两日之比。

    QDII 净值滞后一天, 韭圈儿用"各自最新日的涨跌幅"按当前权重合成补齐(见验收脚本 F 节)。
    ⚠ 权重用**末端漂移权重**(页面上显示的当前占比), 不是目标权重。
    """
    total = 0.0
    for symbol, weight in weights_now.items():
        contract = contracts.get(symbol)
        if contract is None or contract.row_count < 2:
            return None
        p_last, p_prev = contract.prices[-1], contract.prices[-2]
        if p_prev == 0:
            return None
        total += (weight / 100.0) * (p_last / p_prev - 1.0)
    return total * 100.0


def daily_returns(contract: SeriesContract) -> dict[date, float]:
    """日涨跌幅(小数)。⚠ 用**相邻两点之比**, 与相关性口径一致(验收脚本用源端 percentage)。"""
    out: dict[date, float] = {}
    for (d0, p0), (d1, p1) in zip(
        zip(contract.dates, contract.prices), zip(contract.dates[1:], contract.prices[1:]),
    ):
        if p0:
            out[d1] = p1 / p0 - 1.0
    return out


def pearson(x: Iterable[float], y: Iterable[float]) -> float | None:
    xs, ys = list(x), list(y)
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    vy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if vx * vy == 0:
        return None
    return cov / (vx * vy)


def correlation(
    contracts: Mapping[str, SeriesContract], start: date, end: date,
) -> dict[str, object]:
    """相关性矩阵: 日涨跌幅皮尔森, **pairwise 交集**对齐, 区间独立于回测区间。

    ⚠ 韭圈儿各模块区间对齐全不一致(回测/回撤向前对齐, 相关性向后顺延),
      所以本函数的区间**必须由调用方单独传**, 不能复用回测区间。
    """
    returns = {s: daily_returns(c) for s, c in contracts.items()}
    symbols = sorted(returns)
    matrix: list[list[float | None]] = []
    samples: dict[str, int] = {}
    for a in symbols:
        row: list[float | None] = []
        for b in symbols:
            if a == b:
                row.append(1.0)
                continue
            shared = sorted(
                d for d in (set(returns[a]) & set(returns[b])) if start <= d <= end
            )
            if a < b:
                samples[f"{a}|{b}"] = len(shared)
            row.append(pearson([returns[a][d] for d in shared], [returns[b][d] for d in shared]))
        matrix.append(row)
    return {"start": start.isoformat(), "end": end.isoformat(), "symbols": symbols,
            "matrix": matrix, "sample_sizes": samples}


def performance_metrics(ledger: Ledger) -> dict[str, float | None]:
    """指标卡: 年化 / 最大回撤 / 波动 / 夏普 / 索提诺 / 卡玛 / 最差月度 / 最差年度 / 累计换手。

    基于**日 NAV 序列**, 年化用 252 交易日; 无风险利率取 0(与"不计成本"口径一致)。
    ⚠ `turnover`(Σ|Δw|) **不参与任何收益计算** —— 零成本口径下它只是"实盘可行性的参考值"。
    """
    _KEYS = (
        "cagr", "mdd", "vol", "sharpe", "sortino", "calmar",
        "worst_month", "worst_year", "turnover",
    )
    if len(ledger.nav) < 2:
        return dict.fromkeys(_KEYS, None)
    nav = ledger.nav
    first, last = nav[0], nav[-1]
    days = (ledger.dates[-1] - ledger.dates[0]).days
    if first <= 0 or days <= 0:
        return dict.fromkeys(_KEYS, None)
    years = days / 365.25
    total_return = last / first
    cagr = (total_return ** (1.0 / years) - 1.0) * 100.0 if years > 0 else None

    rets = [nav[i] / nav[i - 1] - 1.0 for i in range(1, len(nav))]
    n = len(rets)
    mean = sum(rets) / n
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / n) * math.sqrt(252) * 100.0
    downside = [r for r in rets if r < 0]
    dvol = (
        math.sqrt(sum(r ** 2 for r in downside) / len(downside)) * math.sqrt(252) * 100.0
        if downside else None
    )
    mdd = max_drawdown(ledger, ledger.dates[0], ledger.dates[-1])
    return {
        "cagr": cagr,
        "mdd": mdd,
        "vol": vol,
        "sharpe": (mean * 252 * 100.0 / vol) if vol else None,
        "sortino": (mean * 252 * 100.0 / dvol) if dvol else None,
        "calmar": (cagr / abs(mdd)) if (cagr is not None and mdd) else None,
        "worst_month": _worst_month(ledger),
        "worst_year": _worst_year(ledger),
        "turnover": sum(value for _, value in ledger.rebalances),
    }


def _worst_month(ledger: Ledger) -> float | None:
    """最差单月收益(%): 按月取**该月最后一天**的 NAV 环比。"""
    month_end: dict[tuple[int, int], float] = {}
    for d, v in ledger.series:
        month_end[(d.year, d.month)] = v  # 升序遍历, 保留每月最后一天
    keys = sorted(month_end)
    if len(keys) < 2:
        return None
    worst = None
    for prev, cur in zip(keys, keys[1:]):
        change = (month_end[cur] / month_end[prev] - 1.0) * 100.0
        worst = change if worst is None else min(worst, change)
    return worst


def _worst_year(ledger: Ledger) -> float | None:
    """最差单年收益(%): 按年取**该年最后一天**的 NAV 环比(与「最差月度」同一口径)。"""
    year_end: dict[int, float] = {}
    for d, v in ledger.series:
        year_end[d.year] = v  # 升序遍历, 保留每年最后一天
    keys = sorted(year_end)
    if len(keys) < 2:
        return None
    worst = None
    for prev, cur in zip(keys, keys[1:]):
        change = (year_end[cur] / year_end[prev] - 1.0) * 100.0
        worst = change if worst is None else min(worst, change)
    return worst


# ---------------------------------------------------------------------------
# 区间窗口解析(收益条)
# ---------------------------------------------------------------------------

# 收益条七格(顺序即页面顺序), 与韭圈儿一致
WINDOW_KEYS = ("d1", "w1", "m1", "ytd", "y1", "y3", "inception")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def months_back(day: date, months: int) -> date:
    """**自然月**回推(不是 N×30 天); 日号超出目标月天数时取该月最后一天。

    ⚠ 韭圈儿的"近1月 / 近1年 / 近3年"都是**同日回推 N 个自然月**:
      2026-09-18 回推 1 月 = 2026-08-18。
      若按 30 天算会得到 2026-08-19 —— 差一个交易日, 实测让"近1月"偏 0.28pp
      (真实数据上发现的, 见 docs/portfolio-lab-verification.md)。
      闰日(2/29)由"取目标月最后一天"自然兜住, 不会抛异常。
    """
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, _days_in_month(year, month)))


def resolve_window_start(key: str, end: date, *, inception: date | None) -> date | None:
    """区间键 → 起始参考日(由调用方把 end 定为"数据最新日")。

    ⚠ 全部按**自然日/自然月回推**, 真正落到哪个交易日由 `window_return` 的向前对齐决定
      (韭圈儿就是这么算的, 所以"近1月"的实际起点可能不是 30 天前那天)。
    """
    from datetime import timedelta

    if key == "d1":
        return end - timedelta(days=1)
    if key == "w1":
        return end - timedelta(days=7)
    if key == "m1":
        return months_back(end, 1)
    if key == "ytd":
        # 上年最后一天。与 `date(end.year, 1, 1)` 向前对齐后等价(1月1日恒为休市),
        # 但写"上年最后一天"意图明确, 且与参考实现逐字一致。
        return date(end.year - 1, 12, 31)
    if key == "y1":
        return months_back(end, 12)
    if key == "y3":
        return months_back(end, 36)
    if key == "inception":
        return inception
    raise BacktestError(f"未知区间键: {key!r}(支持 {', '.join(WINDOW_KEYS)})")


def resolve_windows(
    ledger: Ledger, contracts: Mapping[str, SeriesContract], *,
    end: date | None = None,
) -> dict[str, dict[str, object] | None]:
    """收益条七格。

    `d1` 用**合成口径**(`latest_day_composite`), 其余用账本区间收益 —— 这两条不是同一套算法,
    韭圈儿页面自己也标注了(F 节)。
    """
    last_day = end or ledger.dates[-1]
    # 「成立来」的起点 = **建仓日 T0**(账本首点) —— 不是"最早那只标的的首日"。
    # 规格 §二-① 第 7 格: 成立来 = 建仓日 T0(= 各标的"数据可得区间"的共同起点)→ 末端。
    inception = ledger.dates[0] if ledger.dates else None
    # ⚠ `d1` 走合成口径, 与其余六格**不是同一套算法**(韭圈儿页面自己也在 F 节标注了)
    composite = latest_day_composite(contracts, ledger.final_weights)
    out: dict[str, dict[str, object] | None] = {}
    for key in WINDOW_KEYS:
        if key == "d1":
            out[key] = None if composite is None else {
                "value": composite,
                "actual_start": None,
                "actual_end": last_day.isoformat(),
                "composite": True,
            }
            continue
        start_ref = resolve_window_start(key, last_day, inception=inception)
        if start_ref is None:
            out[key] = None
            continue
        hit = window_return(ledger, start_ref, last_day)
        out[key] = None if hit is None else {
            "value": hit.value,
            "actual_start": hit.actual_start.isoformat(),
            "actual_end": hit.actual_end.isoformat(),
            "composite": False,
        }
    return out
