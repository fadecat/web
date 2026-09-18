# -*- coding: utf-8 -*-
"""研究回放引擎: 逐 T 计划生成 → T+1 评价 → 汇总(零 akshare 依赖)。

设计对应 docs/superpowers/specs/2026-09-18-next-day-t-research-replay-design.md §7:
- 逐日生成计划只传入 date <= T 的 bars; 拿到 T+1 raw OHLC 之后才评价;
- 评价顺序: 先剔除 DISABLED/下一日权益事件/不完整评价日 → 开盘失效判定 → 档位触达判定;
- 开盘失效的一侧不进入该侧档位命中率分母;
- 双侧同时触达标 PATH_AMBIGUOUS, 不假设先买后卖, 不计算收益;
- 训练/验证切分由区间内交易日历前 60% 决定, 不随 λ/窗口漂移。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from sqlalchemy.orm import Session

from backend.models.research import ResearchReplayDay, ResearchReplayRun
from backend.services.research_plan import (
    ALGORITHM_VERSION,
    BarInput,
    DailyPlan,
    PlanParameters,
    generate_plan,
)
from backend.services import research_store

# 评价日类别(落库到 research_replay_day.day_category)
CATEGORY_BUY_ONLY = "BUY_ONLY"
CATEGORY_SELL_ONLY = "SELL_ONLY"
CATEGORY_BOTH_HIT = "BOTH_HIT"  # 双侧同时触达 → PATH_AMBIGUOUS, 不排序不算收益
CATEGORY_NO_HIT = "NO_HIT"
CATEGORY_DISABLED = "DISABLED"
CATEGORY_EXCLUDED_DATA = "EXCLUDED_DATA"
CATEGORY_EXCLUDED_CORP_ACTION = "EXCLUDED_CORP_ACTION"
CATEGORY_EXCLUDED_OPEN_INVALIDATION = "EXCLUDED_OPEN_INVALIDATION"
CATEGORY_CALENDAR_UNVERIFIED = "CALENDAR_UNVERIFIED"

# 默认参数比较网格
DEFAULT_LAMBDAS: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3)
DEFAULT_WINDOWS: tuple[int, ...] = (60, 120, 180)

# 开盘失效线倍数(设计 §7: 1.5*ATR_T*scale_T)
OPEN_INVALIDATION_ATR_MULTIPLE = 1.5


@dataclass(frozen=True)
class DayEvaluation:
    """单评价日结果(物化到 research_replay_day 的字段来源)。"""

    plan_date: date
    eval_date: date | None
    day_category: str
    plan: DailyPlan
    next_open: float | None = None
    next_high: float | None = None
    next_low: float | None = None
    next_close: float | None = None
    buy_hits: tuple[bool, bool, bool] | None = None
    sell_hits: tuple[bool, bool, bool] | None = None
    buy_open_invalid: bool = False
    sell_open_invalid: bool = False
    evidence: dict[str, Any] | None = None


def _next_ratio_step(bars: Sequence[BarInput], eval_index: int) -> float | None:
    """T+1 的 r_t 相对阶跃(评价日权益事件检测); 数据不足返回 None。"""
    if eval_index < 1:
        return None
    prev, current = bars[eval_index - 1], bars[eval_index]
    if prev.hfq_close <= 0 or current.hfq_close <= 0:
        return None
    ratio_prev = prev.raw_close / prev.hfq_close
    ratio_now = current.raw_close / current.hfq_close
    if ratio_prev == 0:
        return None
    return abs(ratio_now - ratio_prev) / abs(ratio_prev)


def evaluate_day(
    plan: DailyPlan,
    next_bar: BarInput | None,
    parameters: PlanParameters,
    *,
    ratio_step_next: float | None = None,
    close_raw: float | None = None,
) -> DayEvaluation:
    """按设计 §7 顺序评价一个 T 日计划的次日表现。"""
    if plan.status != "ACTIVE":
        return DayEvaluation(
            plan_date=plan.as_of_date, eval_date=None,
            day_category=CATEGORY_DISABLED, plan=plan,
        )

    if next_bar is None:
        return DayEvaluation(
            plan_date=plan.as_of_date, eval_date=None,
            day_category=CATEGORY_EXCLUDED_DATA, plan=plan,
            evidence={"reason": "无 T+1 数据(日历未覆盖或评价日缺数据)"},
        )

    # 下一日权益事件: 分子与分母均排除, 单列计数
    if ratio_step_next is not None and ratio_step_next > parameters.corporate_action_tolerance:
        return DayEvaluation(
            plan_date=plan.as_of_date, eval_date=next_bar.trade_date,
            day_category=CATEGORY_EXCLUDED_CORP_ACTION, plan=plan,
            next_open=next_bar.raw_open, next_high=next_bar.raw_high,
            next_low=next_bar.raw_low, next_close=next_bar.raw_close,
            evidence={"ratio_step_next": ratio_step_next,
                      "threshold": parameters.corporate_action_tolerance},
        )

    assert plan.buy_levels_raw is not None and plan.sell_levels_raw is not None
    assert plan.atr14 is not None and plan.scale is not None
    # 开盘失效线基准是 T 日未复权收盘; 缺失时用 T+1 开盘价退化(不产生误判的开盘失效)。
    effective_close_raw = close_raw if close_raw is not None else next_bar.raw_open
    return _evaluate_with_open(
        plan, next_bar, parameters, ratio_step_next=ratio_step_next,
        close_raw=effective_close_raw,
    )


def _evaluate_with_open(
    plan: DailyPlan,
    next_bar: BarInput,
    parameters: PlanParameters,
    *,
    ratio_step_next: float | None,
    close_raw: float,
) -> DayEvaluation:
    """开盘失效 + 档位触达判定(close_raw=T 日未复权收盘, 由调用方给出)。"""
    lower_line = close_raw - OPEN_INVALIDATION_ATR_MULTIPLE * plan.atr14 * plan.scale
    upper_line = close_raw + OPEN_INVALIDATION_ATR_MULTIPLE * plan.atr14 * plan.scale
    buy_open_invalid = next_bar.raw_open < lower_line
    sell_open_invalid = next_bar.raw_open > upper_line

    buy_levels = plan.buy_levels_raw
    sell_levels = plan.sell_levels_raw
    assert buy_levels is not None and sell_levels is not None

    buy_hits = tuple(next_bar.raw_low <= level for level in buy_levels)
    sell_hits = tuple(next_bar.raw_high >= level for level in sell_levels)
    buy_side_counted = not buy_open_invalid
    sell_side_counted = not sell_open_invalid

    buy_hit = any(buy_hits) if buy_side_counted else False
    sell_hit = any(sell_hits) if sell_side_counted else False

    if buy_open_invalid and sell_open_invalid:
        category = CATEGORY_EXCLUDED_OPEN_INVALIDATION
    elif buy_hit and sell_hit:
        category = CATEGORY_BOTH_HIT
    elif buy_hit:
        category = CATEGORY_BUY_ONLY
    elif sell_hit:
        category = CATEGORY_SELL_ONLY
    elif buy_open_invalid or sell_open_invalid:
        # 单侧开盘失效且另一侧未触达: 失效侧出分母, 有效侧计入 NO_HIT 侧别
        category = CATEGORY_NO_HIT if (buy_side_counted or sell_side_counted) else CATEGORY_EXCLUDED_OPEN_INVALIDATION
    else:
        category = CATEGORY_NO_HIT

    return DayEvaluation(
        plan_date=plan.as_of_date, eval_date=next_bar.trade_date,
        day_category=category, plan=plan,
        next_open=next_bar.raw_open, next_high=next_bar.raw_high,
        next_low=next_bar.raw_low, next_close=next_bar.raw_close,
        buy_hits=buy_hits, sell_hits=sell_hits,
        buy_open_invalid=buy_open_invalid, sell_open_invalid=sell_open_invalid,
        evidence={
            "open_invalidation_lower": lower_line,
            "open_invalidation_upper": upper_line,
            "close_raw_T": close_raw,
        },
    )


# ---------------------------------------------------------------------------
# 回放执行
# ---------------------------------------------------------------------------

def _train_split_date(open_dates: Sequence[date]) -> date | None:
    """训练/验证切分: 区间内开市日升序前 60% 位置(不随参数漂移)。"""
    if not open_dates:
        return None
    count = len(open_dates)
    index = max(0, math.floor(count * 0.6) - 1)
    return open_dates[index]


def _input_hash(bars: Sequence[BarInput]) -> str:
    digest = hashlib.sha256()
    for bar in bars:
        digest.update((
            f"{bar.trade_date.isoformat()}|{bar.raw_close}|{bar.hfq_close}"
        ).encode("utf-8"))
    return digest.hexdigest()


def run_replay(
    db: Session,
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    parameters: PlanParameters | None = None,
    tolerance: float = 0.002,
) -> list[int]:
    """对 symbol 在 [start_date, end_date] 执行 λ×窗口网格回放, 返回 run_id 列表。

    - 数据基线: latest USABLE 快照的配对 bars(全量加载, 逐 T 截断);
    - 幂等: 同一唯一键的旧 run 整体删除重建。
    """
    snapshot = research_store.latest_usable_snapshot(db, symbol)
    if snapshot is None:
        raise ValueError(f"{symbol} 无可用(USABLE)数据快照, 数据源未验收或未同步")

    paired = research_store.load_paired_bars(db, symbol, end_date=end_date)
    bars = [
        BarInput(
            trade_date=row["trade_date"],
            raw_open=row["raw_open"], raw_high=row["raw_high"],
            raw_low=row["raw_low"], raw_close=row["raw_close"],
            hfq_open=row["hfq_open"], hfq_high=row["hfq_high"],
            hfq_low=row["hfq_low"], hfq_close=row["hfq_close"],
        )
        for row in paired
    ]
    if not bars:
        raise ValueError(f"{symbol} 无配对 bars")

    calendar = dict(research_store.load_calendar(db, start_date, end_date))
    open_dates = sorted(d for d, is_open in calendar.items() if is_open)
    train_end = _train_split_date(open_dates)

    input_hash = _input_hash(bars)
    run_ids: list[int] = []
    for lambda_ in lambdas:
        for window in windows:
            base = parameters or PlanParameters()
            plan_parameters = PlanParameters(
                lambda_=float(lambda_),
                quantile_window=int(window),
                warmup_bars=base.warmup_bars,
                volatility_ratio_multiple=base.volatility_ratio_multiple,
                extreme_z_threshold=base.extreme_z_threshold,
                corporate_action_tolerance=tolerance,
            )
            day_rows: list[dict[str, Any]] = []
            for index, bar in enumerate(bars):
                if bar.trade_date < start_date or bar.trade_date > end_date:
                    continue
                # 只把 date <= T 的 bars 交给计划核心(T+1 盲视)
                plan = generate_plan(symbol, bar.trade_date, bars[: index + 1], plan_parameters)
                next_bar = bars[index + 1] if index + 1 < len(bars) else None
                # 相邻有行情日语义: T+1 取序列中的下一根 bar(而非日历上的 T+1)
                ratio_step_next = None
                if next_bar is not None:
                    ratio_step_next = _next_ratio_step(bars, index + 1)
                if plan.status == "ACTIVE" and next_bar is not None:
                    evaluation = _evaluate_with_open(
                        plan, next_bar, plan_parameters,
                        ratio_step_next=ratio_step_next,
                        close_raw=bar.raw_close,
                    )
                elif plan.status == "ACTIVE":
                    # 无 T+1 bar: 日历可信时该日为区间末尾, 否则 CALENDAR_UNVERIFIED
                    evaluation = DayEvaluation(
                        plan_date=plan.as_of_date, eval_date=None,
                        day_category=CATEGORY_EXCLUDED_DATA, plan=plan,
                        evidence={"reason": "无 T+1 相邻行情日"},
                    )
                else:
                    evaluation = evaluate_day(plan, next_bar, plan_parameters)
                day_rows.append(_day_row(evaluation))
            run_ids.append(research_store.replace_replay_run(
                db,
                symbol=symbol, param_lambda=float(lambda_), quantile_window=int(window),
                algorithm_version=ALGORITHM_VERSION,
                start_date=start_date, end_date=end_date,
                train_end_date=train_end,
                raw_snapshot_id=snapshot.id, hfq_snapshot_id=snapshot.id,
                input_hash=input_hash, day_rows=day_rows,
            ))
    return run_ids


def _day_row(evaluation: DayEvaluation) -> dict[str, Any]:
    plan = evaluation.plan
    return {
        "plan_date": evaluation.plan_date,
        "eval_date": evaluation.eval_date,
        "status": plan.status,
        "reason_codes": json.dumps(plan.to_payload()["reasons"], ensure_ascii=False)
        if plan.reasons else None,
        "z_value": plan.z,
        "atr14": plan.atr14,
        "ema20": plan.ema20,
        "scale": plan.scale,
        "buy_levels_raw": json.dumps(list(plan.buy_levels_raw)) if plan.buy_levels_raw else None,
        "sell_levels_raw": json.dumps(list(plan.sell_levels_raw)) if plan.sell_levels_raw else None,
        "next_open": evaluation.next_open,
        "next_high": evaluation.next_high,
        "next_low": evaluation.next_low,
        "next_close": evaluation.next_close,
        "buy_hits": json.dumps(list(evaluation.buy_hits)) if evaluation.buy_hits else None,
        "sell_hits": json.dumps(list(evaluation.sell_hits)) if evaluation.sell_hits else None,
        "buy_open_invalid": evaluation.buy_open_invalid,
        "sell_open_invalid": evaluation.sell_open_invalid,
        "day_category": evaluation.day_category,
        "evidence": json.dumps(evaluation.evidence, ensure_ascii=False) if evaluation.evidence else None,
    }


# ---------------------------------------------------------------------------
# 汇总(由逐日明细聚合, 可复算)
# ---------------------------------------------------------------------------

_TIERS = ("P50", "P70", "P85")


def _tier_stats(days: Sequence[ResearchReplayDay], side: str) -> list[dict[str, Any]]:
    """一侧三档的 {tier, numerator, denominator, rate}; 分母剔除开盘失效侧。"""
    stats: list[dict[str, Any]] = []
    for tier_index, tier in enumerate(_TIERS):
        numerator = denominator = 0
        for day in days:
            if day.day_category in (
                CATEGORY_DISABLED, CATEGORY_EXCLUDED_DATA, CATEGORY_EXCLUDED_CORP_ACTION,
                CATEGORY_CALENDAR_UNVERIFIED,
            ):
                continue
            if side == "buy":
                if day.buy_open_invalid:
                    continue
                hits = json.loads(day.buy_hits) if day.buy_hits else None
            else:
                if day.sell_open_invalid:
                    continue
                hits = json.loads(day.sell_hits) if day.sell_hits else None
            if hits is None:
                continue
            denominator += 1
            if hits[tier_index]:
                numerator += 1
        stats.append({
            "tier": tier,
            "numerator": numerator,
            "denominator": denominator,
            "rate": (numerator / denominator) if denominator else None,
        })
    return stats


def build_summary(
    days: Sequence[ResearchReplayDay],
    run: ResearchReplayRun,
    *,
    split_date: date | None = None,
) -> dict[str, Any]:
    """汇总: 各档分子/分母/比例、类别计数、排除分解、训练/验证两段。"""
    def _segment(segment_days: Sequence[ResearchReplayDay]) -> dict[str, Any]:
        category_counts: dict[str, int] = {}
        for day in segment_days:
            category_counts[day.day_category] = category_counts.get(day.day_category, 0) + 1
        return {
            "day_count": len(segment_days),
            "buy": _tier_stats(segment_days, "buy"),
            "sell": _tier_stats(segment_days, "sell"),
            "day_categories": category_counts,
        }

    train_days = [d for d in days if split_date is not None and d.plan_date <= split_date]
    validation_days = [d for d in days if split_date is None or d.plan_date > split_date]
    return {
        "run_id": run.id,
        "symbol": run.symbol,
        "param_lambda": run.param_lambda,
        "quantile_window": run.quantile_window,
        "algorithm_version": run.algorithm_version,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "train_end_date": run.train_end_date.isoformat() if run.train_end_date else None,
        "input_hash": run.input_hash,
        "retrospective": True,  # 回顾性回放声明(非 point-in-time)
        "overall": _segment(days),
        "train": _segment(train_days),
        "validation": _segment(validation_days),
    }
