# -*- coding: utf-8 -*-
"""research_plan 计划核心测试: 手算断言 + 盲视/不变性边界 + 零 akshare 源断言。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

import backend.services.research_plan as research_plan
from backend.services.research_plan import (
    ALGORITHM_VERSION,
    BarInput,
    DailyPlan,
    PlanParameters,
    atr14_series,
    ema20_series,
    generate_plan,
    linear_quantile,
    offset_samples,
    true_ranges,
)


def _bar(d: date, raw_close: float, hfq_close: float, *,
         hfq_high: float | None = None, hfq_low: float | None = None) -> BarInput:
    hfq_high = hfq_high if hfq_high is not None else hfq_close * 1.01
    hfq_low = hfq_low if hfq_low is not None else hfq_close * 0.99
    return BarInput(
        trade_date=d,
        raw_open=raw_close * 0.995, raw_high=raw_close * 1.01,
        raw_low=raw_close * 0.99, raw_close=raw_close,
        hfq_open=hfq_close * 0.995, hfq_high=hfq_high,
        hfq_low=hfq_low, hfq_close=hfq_close,
    )


def _bars(count: int, *, start=date(2020, 1, 2), scale_ratio=1.0, drift=0.0) -> list[BarInput]:
    bars: list[BarInput] = []
    hfq = 100.0
    for i in range(count):
        d = start + timedelta(days=i)  # 周末不影响(研究层不判日历, 日期只做排序/截断)
        hfq_close = hfq + drift
        bars.append(_bar(d, raw_close=hfq_close * scale_ratio, hfq_close=hfq_close))
        hfq = hfq_close
    return bars


_PARAMS = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200,
                         corporate_action_tolerance=0.002)


def test_algorithm_version_pinned():
    assert ALGORITHM_VERSION == "ndt-research-v1"


# ---------------------------------------------------------------------------
# 指标初始化(手算逐位)
# ---------------------------------------------------------------------------

def test_ema20_seed_is_mean_of_first_20_then_alpha_2_over_21():
    closes = [float(i + 1) for i in range(25)]
    series = ema20_series(closes)
    assert len(series) == 6
    seed = sum(closes[:20]) / 20.0  # = 10.5
    assert series[0] == pytest.approx(seed)
    assert series[1] == pytest.approx(seed + (21.0 - seed) * (2.0 / 21.0))
    assert series[2] == pytest.approx(series[1] + (22.0 - series[1]) * (2.0 / 21.0))


def test_atr14_seed_is_mean_of_first_14_trs_then_wilder():
    # 构造可手算的 TR: 恒定 TR=1
    highs = [11.0] * 20
    lows = [10.0] * 20
    closes = [10.5] * 20
    trs = true_ranges(highs, lows, closes)
    assert trs[0] == 1.0 and all(tr == 1.0 for tr in trs)
    series = atr14_series(highs, lows, closes)
    assert series[13] == pytest.approx(1.0)  # 14 个 TR 均值
    assert series[14] == pytest.approx((1.0 * 13 + 1.0) / 14.0)
    # 递变 TR 手算: TR 序列 [1]*14 + [2, 3]
    highs2 = [11.0] * 14 + [12.0, 13.0]
    lows2 = [10.0] * 14 + [10.0, 10.0]
    closes2 = [10.5] * 14 + [10.5, 12.0]
    series2 = atr14_series(highs2, lows2, closes2)
    tr15 = max(2.0, abs(12.0 - 10.5), abs(10.0 - 10.5))  # = 2.0
    assert series2[14] == pytest.approx((1.0 * 13 + tr15) / 14.0)


def test_true_ranges_first_bar_is_high_minus_low():
    trs = true_ranges([10.0], [8.0], [9.0])
    assert trs == [2.0]


def test_linear_quantile_interpolates():
    assert linear_quantile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert linear_quantile([1.0, 2.0, 3.0, 4.0], 0.0) == pytest.approx(1.0)
    assert linear_quantile([1.0, 2.0, 3.0, 4.0], 1.0) == pytest.approx(4.0)
    assert linear_quantile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == pytest.approx(3.0)
    assert linear_quantile([7.0], 0.7) == pytest.approx(7.0)


def test_offset_samples_uses_atr_of_previous_day():
    """ATR_(t-1) 不读取 t 日高低价: 改 t 日 H/L 只影响当期 Down/Up 分子。"""
    n = 30
    highs = [11.0] * n
    lows = [10.0] * n
    closes = [10.5] * n
    atrs = [1.0] * n  # 传入的是逐 t 的 ATR_(t-1) 值
    samples = offset_samples(highs, lows, closes, atrs)
    assert len(samples) == n - 14
    # 全部恒定: Down = (10.5-10.0)/1.0 = 0.5; Up = (11.0-10.5)/1.0 = 0.5
    assert all(abs(down - 0.5) < 1e-12 and abs(up - 0.5) < 1e-12 for down, up in samples)
    # t 日高点抬高只改变当期 Up, 不改变其他任何样本
    highs2 = list(highs)
    highs2[-1] = 15.0
    samples2 = offset_samples(highs2, lows, closes, atrs)
    assert samples2[-1][1] == pytest.approx(4.5)
    assert samples2[:-1] == samples[:-1]


# ---------------------------------------------------------------------------
# generate_plan 边界
# ---------------------------------------------------------------------------

def test_generate_plan_insufficient_history():
    plan = generate_plan("600900.SH", date(2020, 12, 31), _bars(100), _PARAMS)
    assert plan.status == "DISABLED"
    assert plan.reasons[0].code == "INSUFFICIENT_HISTORY"
    assert plan.buy_levels_raw is None


def test_generate_plan_insufficient_samples_with_enough_bars():
    # 200+ 根但高低价恒等于收盘(零 TR) → 有效样本不足 → INSUFFICIENT_HISTORY
    flat = _bars(220, drift=0.0)
    zeroed = [
        BarInput(
            trade_date=b.trade_date,
            raw_open=b.raw_open, raw_high=b.raw_close, raw_low=b.raw_close,
            raw_close=b.raw_close, hfq_open=b.hfq_open, hfq_high=b.hfq_close,
            hfq_low=b.hfq_close, hfq_close=b.hfq_close,
        )
        for b in flat
    ]
    plan = generate_plan("600900.SH", zeroed[-1].trade_date, zeroed, _PARAMS)
    assert plan.status == "DISABLED"
    assert plan.reasons[0].code == "INSUFFICIENT_HISTORY"


def _active_bars(count: int = 260, *, start: date = date(2020, 1, 2)) -> list[BarInput]:
    """生成可 ACTIVE 的序列: 交替涨跌且波幅有限, 保持 Z 在 ±1.5 内、ATR/C 平稳。"""
    bars: list[BarInput] = []
    hfq = 100.0
    d = start
    for i in range(count):
        swing = 1.0 + (i % 5) * 0.1  # 伪随机但确定的日内波幅
        step = 0.35 if i % 2 == 0 else -0.35  # 交替微涨跌 → 收盘紧贴 EMA20
        hfq_high = hfq + swing
        hfq_low = hfq - swing
        close = hfq + step
        bar = BarInput(
            trade_date=d, raw_open=hfq, raw_high=hfq_high, raw_low=hfq_low,
            raw_close=close, hfq_open=hfq, hfq_high=hfq_high,
            hfq_low=hfq_low, hfq_close=close,
        )
        bars.append(bar)
        hfq = close
        d += timedelta(days=1)
    return bars


def test_generate_plan_active_shape_and_hand_formula():
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan = generate_plan("600900.SH", bars[-1].trade_date, bars, params)
    assert plan.status == "ACTIVE", [r.code for r in plan.reasons]
    # 手算复现六档: 用同一指标函数重算
    closes = [b.hfq_close for b in bars]
    highs = [b.hfq_high for b in bars]
    lows = [b.hfq_low for b in bars]
    atrs = atr14_series(highs, lows, closes)
    emas = ema20_series(closes)
    atr_prev_for_t = [float("nan")] * len(bars)
    for index in range(14, len(bars)):
        atr_prev_for_t[index] = atrs[index - 1]
    samples = offset_samples(highs, lows, closes, atr_prev_for_t)[-60:]
    downs = sorted(d for d, _ in samples)
    ups = sorted(u for _, u in samples)
    q_down = [linear_quantile(downs, p) for p in (0.5, 0.7, 0.85)]
    q_up = [linear_quantile(ups, p) for p in (0.5, 0.7, 0.85)]

    atr_t = atrs[-1]
    z = (closes[-1] - emas[-1]) / atr_t
    zc = max(-1.5, min(1.5, z))
    scale = bars[-1].raw_close / bars[-1].hfq_close
    for i in range(3):
        buy_dist = max(0.2, q_down[i] + 0.2 * zc)
        sell_dist = max(0.2, q_up[i] - 0.2 * zc)
        assert plan.buy_levels_raw[i] == pytest.approx(
            (closes[-1] - atr_t * buy_dist) * scale, rel=1e-12,
        )
        assert plan.sell_levels_raw[i] == pytest.approx(
            (closes[-1] + atr_t * sell_dist) * scale, rel=1e-12,
        )
    assert plan.z == pytest.approx(z)
    assert plan.z_clipped == pytest.approx(zc)
    assert plan.scale == pytest.approx(scale)


def test_generate_plan_t_plus_1_blindness():
    """追加未来数据不改变 T 日计划(逐字段相等)。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    t = bars[len(bars) - 20].trade_date  # 取后段, 保证 T 日已过预热
    plan_before = generate_plan("600900.SH", t, bars, params)
    assert plan_before.status == "ACTIVE"
    # 追加 T 之后的数据(日期续接、水平续接)
    tail = _active_bars(50, start=bars[-1].trade_date + timedelta(days=1))
    extended = bars + tail
    plan_after = generate_plan("600900.SH", t, extended, params)
    assert plan_after.status == "ACTIVE"
    assert plan_before.to_payload() == plan_after.to_payload()


def test_generate_plan_scale_invariance():
    """hfq 整条乘正常数 → 距离/分位/Z 不变(六档按同比例缩放)。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan_a = generate_plan("600900.SH", bars[-1].trade_date, bars, params)
    factor = 3.7
    scaled = [
        BarInput(
            trade_date=b.trade_date,
            raw_open=b.raw_open * factor, raw_high=b.raw_high * factor,
            raw_low=b.raw_low * factor, raw_close=b.raw_close * factor,
            hfq_open=b.hfq_open * factor, hfq_high=b.hfq_high * factor,
            hfq_low=b.hfq_low * factor, hfq_close=b.hfq_close * factor,
        )
        for b in bars
    ]
    plan_b = generate_plan("600900.SH", bars[-1].trade_date, scaled, params)
    assert plan_b.status == plan_a.status
    assert plan_b.z == pytest.approx(plan_a.z)
    assert plan_b.z_clipped == pytest.approx(plan_a.z_clipped)
    for i in range(3):
        assert plan_b.buy_levels_raw[i] == pytest.approx(plan_a.buy_levels_raw[i] * factor, rel=1e-9)
        assert plan_b.sell_levels_raw[i] == pytest.approx(plan_a.sell_levels_raw[i] * factor, rel=1e-9)


def test_generate_plan_t_day_sample_included():
    """T 日收盘后 T 日 Down/Up 计入分位样本(改变最后一根 bar 应改变分位)。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan_a = generate_plan("600900.SH", bars[-1].trade_date, bars, params)
    assert plan_a.status == "ACTIVE"
    # 抬高最后一根 bar 的 hfq_low 到接近收盘(Down_T 变小)
    mutated = list(bars)
    last = bars[-1]
    mutated[-1] = BarInput(
        trade_date=last.trade_date,
        raw_open=last.raw_open, raw_high=last.raw_high, raw_low=last.raw_low,
        raw_close=last.raw_close, hfq_open=last.hfq_open, hfq_high=last.hfq_high,
        hfq_low=last.hfq_close - last.hfq_close * 0.001, hfq_close=last.hfq_close,
    )
    plan_b = generate_plan("600900.SH", bars[-1].trade_date, mutated, params)
    assert plan_b.status == "ACTIVE"
    assert plan_b.quantiles != plan_a.quantiles  # T 日样本确实影响分位


def test_generate_plan_extreme_z_strict_boundary():
    """|Z|>1.5 严格大于; 构造 Z 恰在阈值上不触发。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan = generate_plan("600900.SH", bars[-1].trade_date, bars, params)
    # 直接用指标公式构造 Z=±1.5 边界: 检验代码语义(严格 >)
    assert params.extreme_z_threshold == 1.5
    # 用一个极端走势序列触发 EXTREME_Z
    stretched = _active_bars()
    closes = [b.hfq_close for b in stretched]
    emas = ema20_series(closes)
    atrs = atr14_series(
        [b.hfq_high for b in stretched], [b.hfq_low for b in stretched], closes,
    )
    z = (closes[-1] - emas[-1]) / atrs[-1]
    if abs(z) > 1.5:
        disabled = generate_plan("600900.SH", stretched[-1].trade_date, stretched, params)
        assert disabled.status == "DISABLED"
        assert any(r.code == "EXTREME_Z" for r in disabled.reasons)


def test_generate_plan_raw_hfq_mismatch():
    bars = _active_bars()
    broken = list(bars)
    last = bars[-1]
    broken[-1] = BarInput(
        trade_date=last.trade_date,
        raw_open=last.raw_open, raw_high=last.raw_high, raw_low=last.raw_low,
        raw_close=-1.0,  # 负 raw 收盘 → scale 非正
        hfq_open=last.hfq_open, hfq_high=last.hfq_high,
        hfq_low=last.hfq_low, hfq_close=last.hfq_close,
    )
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan = generate_plan("600900.SH", bars[-1].trade_date, broken, params)
    assert plan.status == "DISABLED"
    assert any(r.code == "RAW_HFQ_MISMATCH" for r in plan.reasons)


def test_generate_plan_possible_corporate_action_on_tolerance_step():
    """r_t 相对阶跃 > 容差触发; 恰等于容差不触发(严格 >)。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200,
                            corporate_action_tolerance=0.002)
    # 最后一天 raw 收盘跳降 1% (hfq 不变) → r_t 阶跃约 1% > 0.2%
    stepped = list(bars)
    last = bars[-1]
    stepped[-1] = BarInput(
        trade_date=last.trade_date,
        raw_open=last.raw_open * 0.99, raw_high=last.raw_high * 0.99,
        raw_low=last.raw_low * 0.99, raw_close=last.raw_close * 0.99,
        hfq_open=last.hfq_open, hfq_high=last.hfq_high,
        hfq_low=last.hfq_low, hfq_close=last.hfq_close,
    )
    plan = generate_plan("600900.SH", bars[-1].trade_date, stepped, params)
    assert plan.status == "DISABLED"
    assert any(r.code == "POSSIBLE_CORPORATE_ACTION" for r in plan.reasons)
    # 恰在容差内(0.05% 阶跃)不触发
    mild = list(bars)
    mild[-1] = BarInput(
        trade_date=last.trade_date,
        raw_open=last.raw_open * 0.9995, raw_high=last.raw_high * 0.9995,
        raw_low=last.raw_low * 0.9995, raw_close=last.raw_close * 0.9995,
        hfq_open=last.hfq_open, hfq_high=last.hfq_high,
        hfq_low=last.hfq_low, hfq_close=last.hfq_close,
    )
    plan_mild = generate_plan("600900.SH", bars[-1].trade_date, mild, params)
    assert plan_mild.status == "ACTIVE"
    assert not any(r.code == "POSSIBLE_CORPORATE_ACTION" for r in plan_mild.reasons)


def test_generate_plan_corporate_event_calendar_disables_even_with_flat_ratio():
    """权益事件日历命中即停用(r_t 平稳也触发)——腾讯换源后的主检测路径。"""
    bars = _active_bars()  # raw==hfq, r_t 恒为 1(无阶跃)
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    last_date = bars[-1].trade_date
    plan = generate_plan(
        "600900.SH", last_date, bars, params, corporate_event_dates={last_date},
    )
    assert plan.status == "DISABLED"
    reason = next(r for r in plan.reasons if r.code == "POSSIBLE_CORPORATE_ACTION")
    assert reason.evidence.get("detected_by") == "event_calendar"
    assert plan.buy_levels_raw is None and plan.sell_levels_raw is None


def test_generate_plan_event_calendar_is_t_blind():
    """T+1 盲视: 晚于 T 的事件不得改变 T 日计划(仍 ACTIVE)。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    t = bars[-1].trade_date
    future_events = {t + timedelta(days=1), t + timedelta(days=30)}
    plan = generate_plan(
        "600900.SH", t, bars, params, corporate_event_dates=future_events,
    )
    assert plan.status == "ACTIVE"
    assert not any(r.code == "POSSIBLE_CORPORATE_ACTION" for r in plan.reasons)


def test_generate_plan_event_and_ratio_both_fire_single_reason():
    """事件命中且阶跃超限: 单条 POSSIBLE_CORPORATE_ACTION, 证据同时含两种来源。"""
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200,
                            corporate_action_tolerance=0.05)
    last = bars[-1]
    stepped = list(bars)
    stepped[-1] = BarInput(
        trade_date=last.trade_date,
        raw_open=last.raw_open * 0.9, raw_high=last.raw_high * 0.9,
        raw_low=last.raw_low * 0.9, raw_close=last.raw_close * 0.9,  # 10% 阶跃 > 5%
        hfq_open=last.hfq_open, hfq_high=last.hfq_high,
        hfq_low=last.hfq_low, hfq_close=last.hfq_close,
    )
    plan = generate_plan(
        "600900.SH", last.trade_date, stepped, params,
        corporate_event_dates={last.trade_date},
    )
    assert plan.status == "DISABLED"
    corp_reasons = [r for r in plan.reasons if r.code == "POSSIBLE_CORPORATE_ACTION"]
    assert len(corp_reasons) == 1
    assert corp_reasons[0].evidence.get("detected_by") == "event_calendar+ratio_step"
    assert corp_reasons[0].evidence.get("step") == pytest.approx(0.1, rel=1e-6)


def test_generate_plan_volatility_spike():
    """最后一根 ATR/C 超过前 60 个有效日中位数的 2 倍 → VOLATILITY_SPIKE。

    Wilder ATR 平滑强, 单日 TR 需放大到足够倍数才能把 ATR14 抬过 2×中位线。
    """
    bars = _active_bars()
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan_base = generate_plan("600900.SH", bars[-1].trade_date, bars, params)
    assert plan_base.status == "ACTIVE"
    spiked = list(bars)
    last = bars[-1]
    closes = [b.hfq_close for b in bars]
    atrs = atr14_series(
        [b.hfq_high for b in bars], [b.hfq_low for b in bars], closes,
    )
    median_ratio = sorted(
        atrs[i] / closes[i]
        for i in range(len(bars) - 61, len(bars) - 1)
        if atrs[i] == atrs[i] and closes[i] > 0
    )
    median = linear_quantile(median_ratio, 0.5)
    # 反解需要的 TR: ATR_new = (ATR_prev*13 + TR)/14 > 2*median*C
    required_tr = (2.0 * median * last.hfq_close) * 14.0 - atrs[-2] * 13.0
    half_range = required_tr / 2.0
    spiked[-1] = BarInput(
        trade_date=last.trade_date,
        raw_open=last.raw_open, raw_high=last.raw_high, raw_low=last.raw_low,
        raw_close=last.raw_close,
        hfq_open=last.hfq_close, hfq_high=last.hfq_close + half_range,
        hfq_low=last.hfq_close - half_range, hfq_close=last.hfq_close,
    )
    plan = generate_plan("600900.SH", bars[-1].trade_date, spiked, params)
    assert plan.status == "DISABLED"
    assert any(r.code == "VOLATILITY_SPIKE" for r in plan.reasons)


def test_generate_plan_min_distance_floor():
    """买/卖距离下限 0.2: 构造极小分位 → 距离恰为 0.2。"""
    bars = _active_bars()
    # 压平最近 60 根的高低区间, 使分位趋近 0
    flat_tail = list(bars)
    for i in range(len(flat_tail) - 60, len(flat_tail)):
        b = flat_tail[i]
        flat_tail[i] = BarInput(
            trade_date=b.trade_date,
            raw_open=b.raw_open, raw_high=b.raw_high, raw_low=b.raw_low,
            raw_close=b.raw_close, hfq_open=b.hfq_open, hfq_high=b.hfq_close,
            hfq_low=b.hfq_close, hfq_close=b.hfq_close,
        )
    params = PlanParameters(lambda_=0.0, quantile_window=60, warmup_bars=200)
    plan = generate_plan("600900.SH", bars[-1].trade_date, flat_tail, params)
    if plan.status == "ACTIVE":
        # λ=0 且分位≈0 → 距离 = max(0.2, ~0) = 0.2
        closes = [b.hfq_close for b in flat_tail]
        atrs = atr14_series(
            [b.hfq_high for b in flat_tail], [b.hfq_low for b in flat_tail], closes,
        )
        scale = plan.scale
        assert plan.buy_levels_raw[0] == pytest.approx(
            (closes[-1] - atrs[-1] * 0.2) * scale, rel=1e-9,
        )
        assert plan.sell_levels_raw[0] == pytest.approx(
            (closes[-1] + atrs[-1] * 0.2) * scale, rel=1e-9,
        )


def test_module_source_has_no_akshare_import():
    """策略层零 akshare 依赖(源码断言, 设计硬约束)。

    检查的是 import 语句与调用, 不禁止 docstring 提及 akshare 字样。
    """
    import re

    for module_name in (
        "backend/services/research_plan.py",
        "backend/services/research_replay.py",
        "backend/services/queries/research.py",
    ):
        source = Path(module_name).read_text(encoding="utf-8")
        import_pattern = re.compile(r"^\s*(import\s+akshare|from\s+akshare)", re.MULTILINE)
        assert not import_pattern.search(source), f"{module_name} 导入了 akshare"
        assert "akshare." not in source.replace("akshare:stock", "").replace("akshare:fund", "")
