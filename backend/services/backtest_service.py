# -*- coding: utf-8 -*-
"""组合回测编排层(P3): 组合定义 → 统一序列 → 份额法账本 → 结果 → Run 落库。

分层边界:
- `services/backtest.py` = **纯算法**(账本 / 区间 / 回撤 / 相关性 / 指标), 只吃序列不吃库;
- 本模块 = **编排**(取数、校验、组装结果、幂等落库), 算法一行不重写。

三条口径来自验收(docs/portfolio-lab-verification.md), 实现时**不得"顺手优化"**:

1. **账本一次算全**: 从 `T0 = max(各标的首个可用日)` 建仓, 各区间收益是同一账本上的
   **两点比值**(区间查询不重置权重); 用户所选区间只决定"曲线从哪归一", 不重建仓。
2. **相关性区间与回测对齐方向相反**: 回测/回撤把起点**向前对齐**到交易日, 相关性
   **向后顺延**(实测: 同一天回推 10 年, 韭圈儿两个模块给出不同答案) → 两处分别解析,
   绝不共用同一个区间解析函数。
3. **收益条固定左端**: 顶部七格恒用"从 T0 等权建仓、**不平衡**持有至今"那条曲线
   (page-spec §二-① 的关键约束), **不随用户切换再平衡而变化**; 再平衡只影响
   曲线区/指标区/回撤 Tab。

Run 落库走 `input_hash` 幂等: 同一组合 + 同一参数 + 同一成员快照 → 复用已有 Run,
不重复计算(规格要求 Run 是"跑那一次的快照", 可复现)。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.portfolio import BacktestRun, Portfolio, PortfolioAsset
from backend.models.research import ResearchSecurity
from backend.models.valuation import IndexDailyQuote
from backend.services import backtest, portfolio_store, series

logger = logging.getLogger(__name__)

# 默认区间 = 末端整年回推 10 年(与韭圈儿页面一致); 真正生效的起点由向前对齐决定
DEFAULT_WINDOW_YEARS = 10

# ⚙ **算法版本**: 任何会改变计算结果的改动都必须 +1。
#
# 为什么需要它: Run 靠 `input_hash` 复用(幂等)。如果只改算法而不改 hash, 同一组合再次
# 回测会命中**旧 Run** —— 用户以为"修好了", 页面上却还是旧数字。把版本号纳入 hash 后,
# 算法升级会让旧 Run 自然失配、重新计算, 旧 Run 本身仍保留可查。
#
# 版本历史:
#   v1 初版
#   v2 默认区间改为"自然年回推"(原 365×10 天会少 2 天, 撞长假后起点晚 4 个交易日);
#      `window_return` 起点早于 T0 时退化为账本首点(原返回空 → 收益条空白)
#   v3 收益条区间起点改**自然月回推**(原"近1月"按 30 天算, 比自然月回推晚 1 个交易日,
#      实测让近1月偏 0.28pp); 「今年来」显式取上年最后一天; y1/y3 闰日不再抛异常
ENGINE_VERSION = 3
# 与 portfolio_store 同一容差(权重合计 100% 判定)
_WEIGHT_SUM_TOLERANCE = 0.01
# 基准曲线最多保留的点数(超长区间按等间隔抽稀, 只为前端画图; 指标永远用全量)
_MAX_PLOT_POINTS = 1200


class BacktestServiceError(ValueError):
    """回测编排层的前置条件不满足(成员为空 / 权重没齐 / 数据缺失 / 区间无效)。"""


# ---------------------------------------------------------------------------
# 取数与校验
# ---------------------------------------------------------------------------

def _members(db: Session, portfolio_id: int) -> list[PortfolioAsset]:
    return list(db.scalars(
        select(PortfolioAsset).where(PortfolioAsset.portfolio_id == portfolio_id)
        .order_by(PortfolioAsset.sort_order, PortfolioAsset.id)
    ).all())


def _portfolio(db: Session, portfolio_id: int) -> Portfolio:
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise BacktestServiceError(f"未知组合 id: {portfolio_id}")
    return portfolio


def _weights(members: Sequence[PortfolioAsset]) -> dict[str, float]:
    """成员 → 初始比例(百分比)。**未设齐或合计 ≠ 100% 一律拒绝**, 不猜也不归一化。"""
    if not members:
        raise BacktestServiceError("组合还没有成员, 请先添加标的")
    missing = [m.symbol for m in members if m.target_weight is None]
    if missing:
        raise BacktestServiceError(
            f"以下成员尚未设置比例: {', '.join(missing)} —— 请先把权重设齐(合计 100%)",
        )
    out = {m.symbol: float(m.target_weight) for m in members}
    total = sum(out.values())
    if abs(total - 100.0) > _WEIGHT_SUM_TOLERANCE:
        raise BacktestServiceError(f"权重合计必须为 100%, 当前 {total:.2f}%")
    if any(w <= 0 for w in out.values()):
        raise BacktestServiceError("存在非正权重, 无法建仓")
    return out


def _contracts(db: Session, members: Sequence[PortfolioAsset]) -> dict[str, series.SeriesContract]:
    """统一序列层取数。空序列直接报错 —— 回测不接受"缺一只先跳过"。"""
    out: dict[str, series.SeriesContract] = {}
    empty: list[str] = []
    for member in members:
        try:
            contract = series.get_series(db, member.symbol)
        except series.SeriesError as exc:
            raise BacktestServiceError(f"{member.symbol} 序列不可用: {exc}") from None
        if contract.row_count == 0:
            empty.append(member.symbol)
        out[member.symbol] = contract
    if empty:
        raise BacktestServiceError(
            f"以下标的尚无数据, 请先在标的库同步: {', '.join(empty)}",
        )
    return out


def load_benchmark(
    db: Session, symbol: str, *, t0: date, end: date,
) -> dict[str, Any] | None:
    """对比标的序列(可选)。

    两类来源:
    - **指数**: `index_daily_quote`(库内 12 个指数, 如 000300 沪深300)。
      ⚠ 指数是**价格指数、不含股息** → price_basis 标为 `PRICE`, 前端图例必须提示口径差异;
    - **股票 / ETF / 场外基金**: 走统一序列层(HFQ / NAV_ADJ)。

    曲线以 `t0` 处的值为基准归一(与主曲线同一条日期轴), 返回 `None` 表示基准取不到
    —— 基准缺失**不阻断回测**, 只是不画对照线。
    """
    code = (symbol or "").strip()
    if not code:
        return None

    rows = db.execute(
        select(IndexDailyQuote.trade_date, IndexDailyQuote.close)
        .where(IndexDailyQuote.index_code == code, IndexDailyQuote.close.isnot(None))
        .order_by(IndexDailyQuote.trade_date)
    ).all()
    if rows:
        return _finish_benchmark(
            code, None, "PRICE", [(d, float(v)) for d, v in rows], t0=t0, end=end,
        )

    try:
        contract = series.get_series(db, code)
    except series.SeriesError:
        return None
    if contract.row_count < 2:
        return None
    name = db.scalar(select(ResearchSecurity.name).where(ResearchSecurity.symbol == code))
    return _finish_benchmark(
        code, name, contract.price_basis, list(zip(contract.dates, contract.prices)),
        t0=t0, end=end,
    )


def _finish_benchmark(
    symbol: str, name: str | None, price_basis: str,
    points: list[tuple[date, float]], *, t0: date, end: date,
) -> dict[str, Any] | None:
    """截取 [t0, end] 并**在 t0 处归一为 1.0**(与主账本同起点, 才能画在同一张图上)。"""
    window = [(d, v) for d, v in points if t0 <= d <= end]
    if len(window) < 2:
        return None
    base = window[0][1]
    if not base:
        return None
    normalized = [(d, v / base) for d, v in window]
    first, last = normalized[0][1], normalized[-1][1]
    return {
        "symbol": symbol,
        "name": name,
        "price_basis": price_basis,
        "actual_start": normalized[0][0].isoformat(),
        "actual_end": normalized[-1][0].isoformat(),
        "total_return": round((last / first - 1.0) * 100.0, 4),
        "points": normalized,
    }


# ---------------------------------------------------------------------------
# 账本切片(曲线归一的唯一入口)
# ---------------------------------------------------------------------------

def _slice_ledger(ledger: backtest.Ledger, start: date, end: date) -> backtest.Ledger:
    """把账本裁到 [start, end] 并**在起点归一为 1.0** —— 页面纵轴"以区间起点为 0%"的由来。

    ⚠ 归一化只影响曲线的纵轴刻度, 不改任何收益率: `window_return` 是两点比值,
    归一前后结果相同(这正是"区间查询不重置权重"的体现)。
    """
    points = [(d, v) for d, v in ledger.series if start <= d <= end]
    if len(points) < 2:
        raise BacktestServiceError(
            f"{start} ~ {end} 区间内交易日不足(仅 {len(points)} 天), 无法回测",
        )
    base = points[0][1]
    if not base:
        raise BacktestServiceError("区间起点净值为 0, 无法归一")
    return backtest.Ledger(
        dates=tuple(d for d, _ in points),
        nav=tuple(v / base for _, v in points),
        final_weights=dict(ledger.final_weights),
        latest_prices=dict(ledger.latest_prices),
    )


def _thin(points: list[list[Any]], limit: int = _MAX_PLOT_POINTS) -> list[list[Any]]:
    """曲线抽稀(仅用于前端画图): 等间隔取点, 强制保留首尾。

    指标与收益条永远用**全量**序列算, 抽稀只发生在写库的绘图数据上。
    """
    n = len(points)
    if n <= limit:
        return points
    step = (n - 1) / (limit - 1)
    idx = sorted({int(round(i * step)) for i in range(limit)} | {0, n - 1})
    return [points[i] for i in idx]


# ---------------------------------------------------------------------------
# 详情表 / 回撤
# ---------------------------------------------------------------------------

def _asset_rows(
    db: Session, members: Sequence[PortfolioAsset],
    contracts: dict[str, series.SeriesContract], ledger: backtest.Ledger,
) -> list[dict[str, Any]]:
    """区域⑦ 组合详情表。逐列口径见 docs/portfolio-lab-page-spec.md §二-⑦。"""
    rows: list[dict[str, Any]] = []
    for member in members:
        contract = contracts[member.symbol]
        security = db.scalar(
            select(ResearchSecurity).where(ResearchSecurity.symbol == member.symbol),
        )
        daily_return = daily_date = None
        if contract.row_count >= 2:
            prev, last = contract.prices[-2], contract.prices[-1]
            if prev:
                daily_return = round((last / prev - 1.0) * 100.0, 4)
                daily_date = contract.last_date.isoformat()
        added = portfolio_store.since_added_return(db, member)
        rows.append({
            "symbol": member.symbol,
            "name": security.name if security is not None else None,
            "security_type": security.security_type if security is not None else None,
            "price_basis": contract.price_basis,
            "target_weight": float(member.target_weight) if member.target_weight is not None else None,
            # 漂移权重: 不平衡持有至今的占比, = 页面「当前比例」
            "current_weight": round(float(ledger.final_weights.get(member.symbol, 0.0)), 4),
            "daily_return": daily_return,
            "daily_return_date": daily_date,
            "added_at": member.added_at.date().isoformat() if member.added_at else None,
            "since_added_return": added["value"] if added else None,
            # ⚠ 基金经理需要蛋卷基金详情(网络), 而回测必须可离线复现 → 暂不提供, 见待办
            "fund_manager": None,
        })
    return rows


def drawdown_detail(
    ledger: backtest.Ledger, start: date, end: date,
) -> dict[str, Any] | None:
    """最大回撤明细: 峰值日 / 谷值日 / 修复日 / 修复所需交易日数。

    与 `max_drawdown` 同口径(峰从**窗口首值**起算), 只是多带出了日期。
    """
    window = [(d, v) for d, v in ledger.series if start <= d <= end]
    if len(window) < 2:
        return None

    peak_value, peak_day = window[0][1], window[0][0]
    worst = 0.0
    worst_peak_value, worst_peak_day, worst_trough_day = peak_value, peak_day, peak_day
    for day, value in window:
        if value > peak_value:
            peak_value, peak_day = value, day
        drop = value / peak_value - 1.0
        if drop < worst:
            worst = drop
            worst_peak_value, worst_peak_day, worst_trough_day = peak_value, peak_day, day

    recovery_day: date | None = None
    recovery_days: int | None = None
    if worst < 0:
        after = [(d, v) for d, v in window if d > worst_trough_day]
        for day, value in after:
            if value >= worst_peak_value:
                recovery_day = day
                recovery_days = sum(1 for d, _ in after if worst_trough_day < d <= day)
                break
    return {
        "value": round(worst * 100.0, 4),
        "peak_date": worst_peak_day.isoformat(),
        "trough_date": worst_trough_day.isoformat(),
        "recovery_date": recovery_day.isoformat() if recovery_day else None,
        "recovery_days": recovery_days,   # None = 至区间末端仍未修复
    }


# ---------------------------------------------------------------------------
# 结果组装
# ---------------------------------------------------------------------------

def _reference_start(end: date, start: date | None) -> date:
    """区间起点参考日: 用户给了就用用户的, 否则 = **末端回推 10 个自然年**。

    ⚠ 必须用"自然月/年回推"(`backtest.months_back`)而不是 `timedelta(days=365*10)` ——
      后者 3650 天比真实的 10 年少 2 天(闰年), 起点会往后漂 2 天; 若这两天正好撞上
      长假, 向前对齐到交易日时就会**整整晚 4 个交易日**(实测 2026-09-18 回推:
      自然年 = 2016-09-18 → 对齐到 09-14; 3650 天 = 2016-09-20 → 对齐到 09-20)。
      韭圈儿的"近10年"用的就是自然年回推。
    """
    if start is not None:
        return start
    return backtest.months_back(end, DEFAULT_WINDOW_YEARS * 12)


def _correlation_start(all_days: list[date], ref_start: date) -> date | None:
    """相关性区间起点: **向后顺延**到 >= ref 的第一个交易日(与回测的向前对齐相反)。

    韭圈儿实测: 同一天(2016-09-18, 周日)回推 10 年, 回测向前对齐到 09-14、
    相关性向后顺延到 09-19 —— 两个模块的区间起点**本来就不同**, 不要统一。

    复用 `backtest._pick_next`(私有但唯一的"向后顺延"实现), 不另写一份。
    """
    return backtest._pick_next(all_days, ref_start)  # noqa: SLF001


def compute(
    db: Session, portfolio_id: int, *,
    rebalance: str = backtest.REBALANCE_NONE,
    benchmark_symbol: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """算一次回测的完整结果(**不落库**)。`run_backtest` 与缓存刷新都走这里。"""
    if rebalance not in backtest.REBALANCE_MODES:
        raise BacktestServiceError(
            f"未知再平衡方式: {rebalance!r}(支持 {', '.join(backtest.REBALANCE_MODES)})",
        )
    portfolio = _portfolio(db, portfolio_id)
    members = _members(db, portfolio_id)
    weights = _weights(members)
    contracts = _contracts(db, members)

    # 1) 不平衡账本一次算全 —— 收益条恒用它(page-spec §二-① 的关键约束)
    base_ledger = backtest.run_ledger(contracts, weights, backtest.REBALANCE_NONE)
    if len(base_ledger.dates) < 2:
        raise BacktestServiceError("各标的没有重合的交易日, 无法建仓")
    t0 = base_ledger.dates[0]

    # 2) 所选再平衡的账本(不平衡时复用上面的结果, 不重算)
    selected_ledger = base_ledger if rebalance == backtest.REBALANCE_NONE else (
        backtest.run_ledger(contracts, weights, rebalance)
    )

    last_day = end or selected_ledger.dates[-1]
    # ⚠ 起点必须夹到 T0: 默认区间是"末端回推 10 年", 而**数据历史不足 10 年的组合**
    #   (T0 晚于该日期) 若不做这一步, `nav_at` 会找不到起点而让整个回测失败。
    #   规格 §区域③ 的规则与此一致: 起始日早于共同起始日 → **自动前移**, 不报错。
    ref_start = max(_reference_start(last_day, start), t0)

    # 区间末端同样按**向前对齐**落到交易日
    hit_start = backtest.nav_at(selected_ledger, ref_start)
    hit_end = backtest.nav_at(selected_ledger, last_day)
    if hit_start is None or hit_end is None:
        raise BacktestServiceError(f"区间 {ref_start} ~ {last_day} 与数据不重叠")
    actual_start, actual_end = hit_start[0], hit_end[0]
    if actual_start >= actual_end:
        raise BacktestServiceError(f"区间 {actual_start} ~ {actual_end} 至少需要两个交易日")

    # 3) 曲线 + 指标 + 回撤: 基于**所选再平衡**的区间账本(起点归一)
    sliced = _slice_ledger(selected_ledger, actual_start, actual_end)
    metrics = backtest.performance_metrics(sliced)
    drawdown = drawdown_detail(selected_ledger, actual_start, actual_end)

    # 4) 收益条: 固定七格, 恒用不平衡账本(不随再平衡变化)
    windows = backtest.resolve_windows(base_ledger, contracts, end=actual_end)

    # 5) 相关性: 区间**向后顺延**, 且与再平衡无关(规格 §二-⑥)
    all_days = sorted({d for c in contracts.values() for d in c.dates})
    corr_start = _correlation_start(all_days, ref_start) or actual_start
    correlation = backtest.correlation(contracts, corr_start, actual_end)

    # 6) 基准(可选)
    benchmark = load_benchmark(db, benchmark_symbol, t0=actual_start, end=actual_end) if benchmark_symbol else None

    members_snapshot = [{
        "symbol": m.symbol,
        "target_weight": float(m.target_weight) if m.target_weight is not None else None,
        "added_at": m.added_at.isoformat() if m.added_at else None,
        "sort_order": m.sort_order,
    } for m in members]

    window_returns = {k: (v or {}).get("value") for k, v in windows.items()}
    result = {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "rebalance": rebalance,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "actual_start": actual_start.isoformat(),
        "actual_end": actual_end.isoformat(),
        "t0_date": t0.isoformat(),
        "data_range": {
            "t0": t0.isoformat(),
            "last": base_ledger.dates[-1].isoformat(),
            "trading_days": len(base_ledger.dates),
            "window_days": len(sliced.dates),
        },
        "windows": windows,
        "window_returns": window_returns,
        # 所选区间的整体收益(= 曲线首末比)与最大回撤, 与区域⑤指标卡同源
        "selected_return": round((sliced.nav[-1] / sliced.nav[0] - 1.0) * 100.0, 4),
        "selected_drawdown": drawdown["value"] if drawdown else None,
        "metrics": metrics,
        "drawdown": drawdown,
        "correlation": correlation,
        "assets": _asset_rows(db, members, contracts, selected_ledger),
        "benchmark": {k: v for k, v in (benchmark or {}).items() if k != "points"} if benchmark else None,
        "data_readiness": portfolio_store.data_readiness(db, portfolio_id),
        "price_basis_note": _price_basis_note(contracts, benchmark),
    }
    return {"result": result, "sliced": sliced, "benchmark": benchmark,
            "members_snapshot": members_snapshot, "weights": weights}


def _price_basis_note(
    contracts: dict[str, series.SeriesContract], benchmark: dict[str, Any] | None,
) -> list[str]:
    """把口径差异摆到台面上(前端区域⑧用): 三类资产 + 基准的价格口径互不相同。"""
    seen: dict[str, list[str]] = {}
    for symbol, contract in contracts.items():
        seen.setdefault(contract.price_basis, []).append(symbol)
    notes = [
        f"{basis}: {', '.join(sorted(symbols))}"
        for basis, symbols in sorted(seen.items())
    ]
    if benchmark and benchmark.get("price_basis") == "PRICE":
        notes.append(
            f"基准 {benchmark['symbol']} 为价格指数(不含股息), 与含分红的组合口径不同, 对照仅供方向参考",
        )
    return notes


# ---------------------------------------------------------------------------
# Run 幂等落库 / 读取
# ---------------------------------------------------------------------------

def _input_hash(
    members_snapshot: list[dict[str, Any]], *,
    rebalance: str, benchmark_symbol: str | None, start: date | None, end: date | None,
    actual_start: str, actual_end: str,
) -> str:
    """输入指纹 = 组合快照 + 参数 + **实际生效区间** + **引擎版本**。

    纳入 `actual_start/actual_end` 而不是只存用户输入: 用户输入的起点是"参考日",
    真正生效的是向前对齐后的交易日 —— 区间解析逻辑一变, 实际区间就变, hash 必须跟着变。
    再叠加 `ENGINE_VERSION` 兜住"算法本身改了但区间没变"的情况(如指标公式修正)。
    """
    payload = {
        "engine": ENGINE_VERSION,
        "members": members_snapshot,
        "rebalance": rebalance,
        "benchmark": benchmark_symbol or "",
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
        "actual_start": actual_start,
        "actual_end": actual_end,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def run_backtest(
    db: Session, portfolio_id: int, *,
    rebalance: str = backtest.REBALANCE_NONE,
    benchmark_symbol: str | None = None,
    start: date | None = None,
    end: date | None = None,
    reuse: bool = True,
) -> dict[str, Any]:
    """跑一次回测并落库。`reuse=True` 时相同输入直接复用已有 Run(幂等)。"""
    bundled = compute(
        db, portfolio_id, rebalance=rebalance, benchmark_symbol=benchmark_symbol,
        start=start, end=end,
    )
    result = bundled["result"]
    digest = _input_hash(
        bundled["members_snapshot"], rebalance=rebalance,
        benchmark_symbol=benchmark_symbol, start=start, end=end,
        actual_start=result["actual_start"], actual_end=result["actual_end"],
    )
    if reuse:
        existing = db.scalar(
            select(BacktestRun)
            .where(BacktestRun.input_hash == digest, BacktestRun.status == "success")
            .order_by(BacktestRun.id.desc())
        )
        if existing is not None:
            return get_run(db, existing.id)

    nav_payload = _nav_payload(bundled["sliced"], bundled["benchmark"])
    run = BacktestRun(
        portfolio_id=portfolio_id,
        status="success",
        rebalance=rebalance,
        benchmark_symbol=benchmark_symbol or None,
        start_date=start,
        end_date=end,
        actual_start=date.fromisoformat(result["actual_start"]),
        actual_end=date.fromisoformat(result["actual_end"]),
        t0_date=date.fromisoformat(result["t0_date"]),
        input_hash=digest,
        members_json=json.dumps(bundled["members_snapshot"], ensure_ascii=False),
        result_json=json.dumps(result, ensure_ascii=False),
        nav_json=json.dumps(nav_payload, ensure_ascii=False),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return get_run(db, run.id)


def _nav_payload(
    sliced: backtest.Ledger, benchmark: dict[str, Any] | None,
) -> dict[str, Any]:
    """曲线数据: 主曲线以区间起点归一为 1.0; 基准按**同一日期轴**前值填充对齐。"""
    dates = [d.isoformat() for d in sliced.dates]
    nav = [round(v, 8) for v in sliced.nav]
    payload: dict[str, Any] = {"dates": dates, "nav": nav, "benchmark": None}
    if benchmark is None:
        return payload

    lookup = {d: v for d, v in benchmark["points"]}
    aligned: list[float | None] = []
    last: float | None = None
    for d in sliced.dates:
        value = lookup.get(d)
        if value is not None:
            last = value
        aligned.append(None if last is None else round(last, 8))
    if all(v is None for v in aligned):
        return payload

    payload["benchmark"] = {
        "symbol": benchmark["symbol"],
        "name": benchmark["name"],
        "price_basis": benchmark["price_basis"],
        "nav": [round(v, 8) if v is not None else None for v in aligned],
    }
    return payload


def _run_payload(run: BacktestRun, *, with_result: bool, with_nav: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": run.id,
        "portfolio_id": run.portfolio_id,
        "status": run.status,
        "rebalance": run.rebalance,
        "benchmark_symbol": run.benchmark_symbol,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
        "actual_start": run.actual_start.isoformat() if run.actual_start else None,
        "actual_end": run.actual_end.isoformat() if run.actual_end else None,
        "t0_date": run.t0_date.isoformat() if run.t0_date else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "error": run.error,
        "members": json.loads(run.members_json) if run.members_json else [],
    }
    if with_result:
        payload["result"] = json.loads(run.result_json) if run.result_json else None
    if with_nav:
        nav = json.loads(run.nav_json) if run.nav_json else None
        payload["nav"] = nav
        payload["points"] = len(nav["dates"]) if nav else 0
    return payload


def get_run(db: Session, run_id: int, *, with_result: bool = True, with_nav: bool = True) -> dict[str, Any]:
    run = db.get(BacktestRun, run_id)
    if run is None:
        raise BacktestServiceError(f"未知回测 id: {run_id}")
    return _run_payload(run, with_result=with_result, with_nav=with_nav)


def _run_summary(run: BacktestRun) -> dict[str, Any]:
    """Run 列表行: 只给"对照需要的那几个数", 不把整个 result_json 抛给列表接口。"""
    result = json.loads(run.result_json) if run.result_json else {}
    metrics = result.get("metrics") or {}
    return {
        "actual_start": result.get("actual_start"),
        "actual_end": result.get("actual_end"),
        "selected_return": result.get("selected_return"),
        "selected_drawdown": result.get("selected_drawdown"),
        "cagr": metrics.get("cagr"),
        "mdd": metrics.get("mdd"),
        "sharpe": metrics.get("sharpe"),
        "window_returns": result.get("window_returns"),
        "asset_count": len(result.get("assets") or []),
    }


def list_runs(
    db: Session, *, portfolio_id: int | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Run 列表(不含曲线) —— 让"同组合 × 不同再平衡/区间/基准"能列出来做对照。"""
    stmt = select(BacktestRun).order_by(BacktestRun.id.desc()).limit(max(1, min(limit, 200)))
    if portfolio_id is not None:
        stmt = stmt.where(BacktestRun.portfolio_id == portfolio_id)
    runs = list(db.scalars(stmt).all())
    return [
        {**_run_payload(r, with_result=False, with_nav=False), "summary": _run_summary(r)}
        for r in runs
    ]


def compare_runs(db: Session, run_ids: Sequence[int]) -> dict[str, Any]:
    """多 Run 对照(同持仓 × 不同再平衡 / 区间 / 基准)。

    ⚠ 交叉区间才可比: 每个 Run 的 actual_start/actual_end 可能不同, 所以**不拼接**,
    只并排给出各自的区间、收益、回撤与曲线(曲线各自归一, 前端按日期轴叠加)。
    """
    ids = [int(i) for i in run_ids]
    if not ids:
        raise BacktestServiceError("请至少指定一个回测 id")
    rows = [db.get(BacktestRun, i) for i in ids]
    runs = [_run_payload(r, with_result=True, with_nav=True) for r in rows if r is not None]
    if not runs:
        raise BacktestServiceError(f"未找到任何回测: {ids}")
    starts = [r["actual_start"] for r in runs if r["actual_start"]]
    ends = [r["actual_end"] for r in runs if r["actual_end"]]
    return {
        "runs": runs,
        # 交集区间 = 各 Run 起点的最大值 ~ 各 Run 末端的最小值, 跨组合对照必须按它对齐
        "overlap_start": max(starts) if starts else None,
        "overlap_end": min(ends) if ends else None,
    }


# ---------------------------------------------------------------------------
# L1 列表页三格缓存(P3-4)
# ---------------------------------------------------------------------------

# 卡片三格 = 日收益 / 近一月 / 今年以来(固定参数: **不平衡**)
_CACHE_WINDOW_KEYS = ("d1", "m1", "ytd")


def refresh_cached_metrics(db: Session, portfolio_id: int) -> dict[str, Any]:
    """重算并回写 L1 卡片三格。

    ⭐ 规格 9.3 的一致性约束: 列表页三格必须与详情页收益条**数字相同** →
    本函数**复用 `compute()`**(同一条区间解析 + 份额法账本), 只是取 `d1/m1/ytd` 三格落库。
    组合不具备回测条件时**清空缓存**(而不是留着过期数字)。
    """
    try:
        bundled = compute(db, portfolio_id, rebalance=backtest.REBALANCE_NONE)
    except BacktestServiceError as exc:
        logger.info("组合 %s 暂不可回测, 清空三格缓存: %s", portfolio_id, exc)
        portfolio_store.write_cached_metrics(
            db, portfolio_id, day_return=None, month_return=None,
            ytd_return=None, asof_date=None,
        )
        return {"portfolio_id": portfolio_id, "ok": False, "reason": str(exc)}

    windows = bundled["result"]["windows"]
    asof = date.fromisoformat(bundled["result"]["actual_end"])
    values = {k: (windows.get(k) or {}).get("value") for k in _CACHE_WINDOW_KEYS}
    portfolio_store.write_cached_metrics(
        db, portfolio_id,
        day_return=values["d1"], month_return=values["m1"], ytd_return=values["ytd"],
        asof_date=asof,
    )
    return {
        "portfolio_id": portfolio_id, "ok": True,
        "day_return": values["d1"], "month_return": values["m1"], "ytd_return": values["ytd"],
        "asof_date": asof.isoformat(),
    }


def refresh_all_cached_metrics(db: Session, *, limit: int = 500) -> dict[str, Any]:
    """批量刷新(每日定时任务末尾调用)。逐组合隔离失败, 不因一个组合坏掉就整批中断。"""
    portfolios = list(db.scalars(
        select(Portfolio).where(Portfolio.status == "active").order_by(Portfolio.id).limit(limit)
    ).all())
    ok = failed = 0
    errors: list[str] = []
    for portfolio in portfolios:
        try:
            outcome = refresh_cached_metrics(db, portfolio.id)
            if outcome["ok"]:
                ok += 1
            else:
                failed += 1
                errors.append(f"{portfolio.id}:{outcome.get('reason')}")
        except Exception as exc:  # noqa: BLE001 单个组合失败不影响其余
            failed += 1
            errors.append(f"{portfolio.id}:{type(exc).__name__}: {exc}")
            logger.warning("组合 %s 缓存刷新失败: %s", portfolio.id, exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("回滚失败")
    return {
        "status": "success" if not errors else ("partial" if ok else "failed"),
        "total": len(portfolios), "success_count": ok, "fail_count": failed,
        "errors": errors[:10],
    }
