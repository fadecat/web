# -*- coding: utf-8 -*-
"""次日 T 价位计划核心(纯函数, 零 DB / 零 akshare 依赖)。

设计对应 docs/superpowers/specs/2026-09-18-next-day-t-research-replay-design.md §5/§6:
- 全部指标在 hfq 连续价格空间计算; 整条 hfq 序列乘同一正数时结果尺度不变;
- generate_plan 第一步即过滤 date <= T 的 bars(T+1 盲视的结构性保证);
- 研究模式 portfolio=None 只输出价位, 不伪造股数;
- 价位为未取整"模型价", 不做 tick 取整, 不得显示为可下单报价。

线上与历史回放共用本模块的一份计算逻辑; 指标初始化/公式任何变更必须升 ALGORITHM_VERSION。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence


ALGORITHM_VERSION = "ndt-research-v1"


# ---------------------------------------------------------------------------
# 输入与参数
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BarInput:
    """配对日线输入(同日 raw/hfq 四价)。"""

    trade_date: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    hfq_open: float
    hfq_high: float
    hfq_low: float
    hfq_close: float


@dataclass(frozen=True)
class PlanParameters:
    """计划参数(默认为研究基线; λ/窗口参与参数比较)。"""

    lambda_: float = 0.2
    quantile_window: int = 120
    warmup_bars: int = 200
    volatility_ratio_multiple: float = 2.0
    extreme_z_threshold: float = 1.5
    corporate_action_tolerance: float = 0.002


@dataclass(frozen=True)
class Reason:
    """停用原因: 触发日期/测量值/阈值/证据。"""

    code: str
    trigger_date: date
    measured: float | None = None
    threshold: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyPlan:
    """T 日收盘后生成的 T+1 计划。"""

    symbol: str
    as_of_date: date
    status: str  # ACTIVE | DISABLED
    reasons: tuple[Reason, ...] = ()
    ema20: float | None = None
    atr14: float | None = None
    z: float | None = None
    z_clipped: float | None = None
    quantiles: dict[str, list[float]] | None = None  # {"down": [p50,p70,p85], "up": [...]}
    scale: float | None = None  # raw_close_T / hfq_close_T(同日收盘比值, 非复权因子)
    buy_levels_raw: tuple[float, float, float] | None = None   # 未取整模型价
    sell_levels_raw: tuple[float, float, float] | None = None  # 未取整模型价

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of_date": self.as_of_date.isoformat(),
            "status": self.status,
            "reasons": [
                {
                    "code": reason.code,
                    "trigger_date": reason.trigger_date.isoformat(),
                    "measured": reason.measured,
                    "threshold": reason.threshold,
                    "evidence": reason.evidence,
                }
                for reason in self.reasons
            ],
            "ema20": self.ema20,
            "atr14": self.atr14,
            "z": self.z,
            "z_clipped": self.z_clipped,
            "quantiles": self.quantiles,
            "scale": self.scale,
            "buy_levels_raw": list(self.buy_levels_raw) if self.buy_levels_raw else None,
            "sell_levels_raw": list(self.sell_levels_raw) if self.sell_levels_raw else None,
        }


# ---------------------------------------------------------------------------
# 指标(固定初始化方法)
# ---------------------------------------------------------------------------

def ema20_series(closes: Sequence[float]) -> list[float]:
    """EMA20: 种子=前 20 根收盘算术平均, 此后 alpha=2/21。"""
    if len(closes) < 20:
        return []
    values: list[float] = []
    seed = sum(closes[:20]) / 20.0
    values.append(seed)
    ema = seed
    for close in closes[20:]:
        ema = ema + (close - ema) * (2.0 / 21.0)
        values.append(ema)
    return values


def true_ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    """TR_t = max(H_t-L_t, |H_t-C_(t-1)|, |L_t-C_(t-1)|); 首根只有 H-L。"""
    if not highs:
        return []
    values = [highs[0] - lows[0]]
    for index in range(1, len(highs)):
        values.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    return values


def atr14_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    """Wilder ATR14: 首个 ATR=前 14 个 TR 算术平均, 此后 (ATR*13+TR)/14。

    返回与输入等长(前 13 个位置为 NaN 占位用 float("nan") 不对外暴露,
    调用方以 index>=13 处的值为有效 ATR)。
    """
    count = len(highs)
    if count < 14:
        return [float("nan")] * count
    trs = true_ranges(highs, lows, closes)
    values = [float("nan")] * count
    atr = sum(trs[:14]) / 14.0
    values[13] = atr
    for index in range(14, count):
        atr = (atr * 13.0 + trs[index]) / 14.0
        values[index] = atr
    return values


def linear_quantile(sorted_values: Sequence[float], probability: float) -> float:
    """线性插值经验分位: idx=(n-1)*p, 两端点线性插值(固定方法)。"""
    if not sorted_values:
        raise ValueError("empty sample for quantile")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[int(position)])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def offset_samples(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    atrs: Sequence[float],
) -> list[tuple[float, float]]:
    """逐历史 t 的 (Down_t, Up_t) 偏移样本(atr 传入的是逐 t 的 ATR_(t-1) 序列)。

    Down_t = max(0, C_(t-1)-L_t)/ATR_(t-1); Up_t = max(0, H_t-C_(t-1))/ATR_(t-1)。
    ATR_(t-1) 不读取 t 日高低价(由调用方构造 atrs[t] = ATR 序列在 t-1 处的值保证)。
    从 index=14 开始(需要前一收盘与有效 ATR_(t-1))。
    """
    samples: list[tuple[float, float]] = []
    for index in range(14, len(highs)):
        prev_close = closes[index - 1]
        atr_prev = atrs[index]
        if not math.isfinite(atr_prev) or atr_prev <= 0:
            continue
        down = max(0.0, prev_close - lows[index]) / atr_prev
        up = max(0.0, highs[index] - prev_close) / atr_prev
        samples.append((down, up))
    return samples


# ---------------------------------------------------------------------------
# 计划生成
# ---------------------------------------------------------------------------

def _r_t_ratio_series(bars: Sequence[BarInput]) -> list[float]:
    """r_t = raw_close_t / hfq_close_t(用于权益事件阶跃检测; 非官方复权因子)。"""
    ratios: list[float] = []
    for bar in bars:
        if bar.hfq_close <= 0 or not math.isfinite(bar.hfq_close):
            ratios.append(float("nan"))
        else:
            ratios.append(bar.raw_close / bar.hfq_close)
    return ratios


def generate_plan(
    symbol: str,
    as_of_date: date,
    bars: Sequence[BarInput],
    parameters: PlanParameters,
    *,
    portfolio: Any = None,
) -> DailyPlan:
    """对 as_of_date=T 生成 T+1 计划(线上与历史回放共用)。

    结构性盲视: 函数第一步过滤 date <= T, 任何 T+1 数据不会进入计算。
    portfolio=None(研究模式)只输出价位, 不输出股数。
    """
    del portfolio  # 研究模式显式忽略持仓参数, 不伪造股数
    usable = [bar for bar in bars if bar.trade_date <= as_of_date]
    usable.sort(key=lambda bar: bar.trade_date)

    reasons: list[Reason] = []

    # --- 尺度比(同日收盘): 须有限且为正 ---
    if not usable:
        return DailyPlan(symbol=symbol, as_of_date=as_of_date, status="DISABLED", reasons=(
            Reason(code="INSUFFICIENT_HISTORY", trigger_date=as_of_date, evidence={"valid_bars": 0}),
        ))
    last = usable[-1]
    scale: float | None = None
    if last.hfq_close > 0 and math.isfinite(last.raw_close) and math.isfinite(last.hfq_close):
        candidate = last.raw_close / last.hfq_close
        if math.isfinite(candidate) and candidate > 0:
            scale = candidate
    if scale is None:
        reasons.append(Reason(
            code="RAW_HFQ_MISMATCH", trigger_date=as_of_date,
            measured=(last.raw_close / last.hfq_close) if last.hfq_close else None,
            evidence={"raw_close": last.raw_close, "hfq_close": last.hfq_close},
        ))

    # --- 权益事件检测: r_t 相对前一交易日阶跃超过容差 ---
    ratios = _r_t_ratio_series(usable)
    if len(ratios) >= 2 and math.isfinite(ratios[-1]) and math.isfinite(ratios[-2]) and ratios[-2] != 0:
        step = abs(ratios[-1] - ratios[-2]) / abs(ratios[-2])
        if step > parameters.corporate_action_tolerance:
            reasons.append(Reason(
                code="POSSIBLE_CORPORATE_ACTION", trigger_date=last.trade_date,
                measured=step, threshold=parameters.corporate_action_tolerance,
                evidence={"r_today": ratios[-1], "r_prev": ratios[-2]},
            ))

    # --- 预热与指标 ---
    highs = [bar.hfq_high for bar in usable]
    lows = [bar.hfq_low for bar in usable]
    closes = [bar.hfq_close for bar in usable]
    atrs = atr14_series(highs, lows, closes)
    emas = ema20_series(closes)

    insufficient = len(usable) < parameters.warmup_bars
    # 偏移样本: T 日收盘后 T 日本身计入最近 quantile_window 个样本
    atr_prev_for_t = [float("nan")] * len(usable)
    for index in range(14, len(usable)):
        atr_prev_for_t[index] = atrs[index - 1]  # ATR_(t-1) 不读 t 日高低价
    samples = offset_samples(highs, lows, closes, atr_prev_for_t)
    if insufficient or len(samples) < parameters.quantile_window:
        reasons.append(Reason(
            code="INSUFFICIENT_HISTORY", trigger_date=as_of_date,
            measured=float(len(usable)),
            threshold=float(parameters.warmup_bars),
            evidence={"valid_bars": len(usable), "offset_samples": len(samples),
                      "required_samples": parameters.quantile_window},
        ))
        return DailyPlan(symbol=symbol, as_of_date=as_of_date, status="DISABLED", reasons=tuple(reasons))

    atr_t = atrs[-1]
    ema_t = emas[-1]
    close_t = closes[-1]
    if not math.isfinite(atr_t) or atr_t <= 0:
        reasons.append(Reason(
            code="INSUFFICIENT_HISTORY", trigger_date=as_of_date,
            measured=atr_t, evidence={"reason": "ATR14 非正或非有限"},
        ))
        return DailyPlan(symbol=symbol, as_of_date=as_of_date, status="DISABLED", reasons=tuple(reasons))

    # --- Z 与保险丝 ---
    z = (close_t - ema_t) / atr_t
    if abs(z) > parameters.extreme_z_threshold:
        reasons.append(Reason(
            code="EXTREME_Z", trigger_date=as_of_date, measured=z,
            threshold=parameters.extreme_z_threshold,
            evidence={"close": close_t, "ema20": ema_t, "atr14": atr_t},
        ))

    # VOLATILITY_SPIKE: ATR14/C 超过前 60 个有效交易日该比值中位数的 2 倍
    ratio_today = atr_t / close_t
    historical_ratios = [
        atrs[index] / closes[index]
        for index in range(max(0, len(usable) - 61), len(usable) - 1)  # t < T
        if math.isfinite(atrs[index]) and atrs[index] > 0 and closes[index] > 0
    ]
    if len(historical_ratios) >= 60 and historical_ratios:
        sorted_ratios = sorted(historical_ratios)
        median_ratio = linear_quantile(sorted_ratios, 0.5)
        if median_ratio > 0 and ratio_today > parameters.volatility_ratio_multiple * median_ratio:
            reasons.append(Reason(
                code="VOLATILITY_SPIKE", trigger_date=as_of_date,
                measured=ratio_today,
                threshold=parameters.volatility_ratio_multiple * median_ratio,
                evidence={"median_ratio_60d": median_ratio, "sample_count": len(historical_ratios)},
            ))

    if reasons or scale is None:
        return DailyPlan(symbol=symbol, as_of_date=as_of_date, status="DISABLED", reasons=tuple(reasons))

    # --- 分位(历史距离分布, 非命中概率) ---
    window = samples[-parameters.quantile_window:]
    downs = sorted(down for down, _ in window)
    ups = sorted(up for _, up in window)
    quantiles = {
        "down": [linear_quantile(downs, p) for p in (0.5, 0.7, 0.85)],
        "up": [linear_quantile(ups, p) for p in (0.5, 0.7, 0.85)],
    }

    # --- 六档价位(未取整模型价) ---
    z_clipped = max(-parameters.extreme_z_threshold, min(parameters.extreme_z_threshold, z))
    lambda_ = parameters.lambda_
    buy_levels_hfq: list[float] = []
    sell_levels_hfq: list[float] = []
    for i in range(3):
        buy_distance = max(0.2, quantiles["down"][i] + lambda_ * z_clipped)
        sell_distance = max(0.2, quantiles["up"][i] - lambda_ * z_clipped)
        buy_levels_hfq.append(close_t - atr_t * buy_distance)
        sell_levels_hfq.append(close_t + atr_t * sell_distance)

    buy_levels_raw = tuple(value * scale for value in buy_levels_hfq)
    sell_levels_raw = tuple(value * scale for value in sell_levels_hfq)

    return DailyPlan(
        symbol=symbol,
        as_of_date=as_of_date,
        status="ACTIVE",
        reasons=(),
        ema20=ema_t,
        atr14=atr_t,
        z=z,
        z_clipped=z_clipped,
        quantiles=quantiles,
        scale=scale,
        buy_levels_raw=buy_levels_raw,
        sell_levels_raw=sell_levels_raw,
    )
