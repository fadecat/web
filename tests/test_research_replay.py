# -*- coding: utf-8 -*-
"""research_replay 回放引擎测试: 合成 bars 黄金用例(零 akshare, 内存库)。"""
import json
from datetime import date, timedelta

import pytest

from backend.models.research import ResearchReplayDay, ResearchReplayRun
from backend.services import research_store
from backend.services.market_data import DailyBar
from backend.services.research_plan import PlanParameters, generate_plan
from backend.services import research_replay
from backend.services.research_replay import (
    CATEGORY_BOTH_HIT,
    CATEGORY_BUY_ONLY,
    CATEGORY_DISABLED,
    CATEGORY_EXCLUDED_CORP_ACTION,
    CATEGORY_NO_HIT,
    CATEGORY_SELL_ONLY,
    DayEvaluation,
    evaluate_day,
    run_replay,
    _train_split_date,
)
from tests.test_research_plan import _active_bars


def _seed_snapshot(db, symbol: str, bars) -> None:
    """按合成 BarInput 发布一个 USABLE 快照(raw=hfq×0.5)。"""
    raw_rows = [
        DailyBar(trade_date=b.trade_date, open=b.raw_open, high=b.raw_high,
                 low=b.raw_low, close=b.raw_close, source="test")
        for b in bars
    ]
    hfq_rows = [
        DailyBar(trade_date=b.trade_date, open=b.hfq_open, high=b.hfq_high,
                 low=b.hfq_low, close=b.hfq_close, source="test")
        for b in bars
    ]
    result = research_store.publish_paired_snapshot(
        db, symbol, raw_rows, hfq_rows, source="test",
        request_start=bars[0].trade_date, request_end=bars[-1].trade_date,
        now=__import__("datetime").datetime(2026, 9, 18, 8, 0),
    )
    assert result.status == "success"


def _seed_calendar(db, bars) -> None:
    sessions = [
        __import__("backend.services.market_data", fromlist=["TradeSession"]).TradeSession(
            trade_date=b.trade_date, is_open=True,
        )
        for b in bars
    ]
    research_store.upsert_trade_calendar(
        db, sessions, source="test", now=__import__("datetime").datetime(2026, 9, 18, 8, 0),
    )


def _bars_for_replay(count: int = 300) -> list:
    """预热 200+ 且逐日微幅震荡的序列(raw 与 hfq 同价, 简化评价手算)。"""
    from backend.services.research_plan import BarInput
    bars: list[BarInput] = []
    price = 100.0
    d = date(2021, 1, 4)
    for i in range(count):
        swing = 1.0 + (i % 5) * 0.1
        step = 0.3 if i % 2 == 0 else -0.3
        bar = BarInput(
            trade_date=d,
            raw_open=price, raw_high=price + swing, raw_low=price - swing,
            raw_close=price + step,
            hfq_open=price, hfq_high=price + swing, hfq_low=price - swing,
            hfq_close=price + step,
        )
        bars.append(bar)
        price = bar.raw_close
        d += timedelta(days=1)
    return bars


# ---------------------------------------------------------------------------
# evaluate_day 单元
# ---------------------------------------------------------------------------

def _active_plan(bars):
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan = generate_plan("X.SH", bars[-1].trade_date, bars, params)
    assert plan.status == "ACTIVE"
    return plan


def test_evaluate_day_buy_only_hit():
    bars = _bars_for_replay(260)
    plan = _active_plan(bars[:-1])
    close_raw = bars[-2].raw_close
    # 构造 T+1: 低点下探到买一档以下, 高点够不到卖一档
    from backend.services.research_plan import BarInput
    next_bar = BarInput(
        trade_date=bars[-1].trade_date,
        raw_open=close_raw, raw_high=close_raw + 0.5,
        raw_low=min(plan.buy_levels_raw) - 1.0, raw_close=close_raw - 0.5,
        hfq_open=close_raw, hfq_high=close_raw + 0.5,
        hfq_low=min(plan.buy_levels_raw) - 1.0, hfq_close=close_raw - 0.5,
    )
    evaluation = evaluate_day(plan, next_bar, PlanParameters(), close_raw=close_raw)
    assert evaluation.day_category == CATEGORY_BUY_ONLY
    assert all(evaluation.buy_hits)
    assert not any(evaluation.sell_hits)


def test_evaluate_day_no_hit():
    bars = _bars_for_replay(260)
    plan = _active_plan(bars[:-1])
    close_raw = bars[-2].raw_close
    from backend.services.research_plan import BarInput
    next_bar = BarInput(
        trade_date=bars[-1].trade_date,
        raw_open=close_raw, raw_high=close_raw + 0.4, raw_low=close_raw - 0.4,
        raw_close=close_raw, hfq_open=close_raw, hfq_high=close_raw + 0.4,
        hfq_low=close_raw - 0.4, hfq_close=close_raw,
    )
    evaluation = evaluate_day(plan, next_bar, PlanParameters(), close_raw=close_raw)
    assert evaluation.day_category == CATEGORY_NO_HIT
    assert evaluation.buy_open_invalid is False and evaluation.sell_open_invalid is False


def test_evaluate_day_open_invalidation_excludes_denominator():
    """开盘跳空低于 raw_close_T - 1.5*ATR*scale → 买侧失效(不进买侧分母)。"""
    bars = _bars_for_replay(260)
    plan = _active_plan(bars[:-1])
    close_raw = bars[-2].raw_close
    line = close_raw - 1.5 * plan.atr14 * plan.scale
    from backend.services.research_plan import BarInput
    next_bar = BarInput(
        trade_date=bars[-1].trade_date,
        raw_open=line - 5.0, raw_high=close_raw, raw_low=line - 6.0,
        raw_close=close_raw - 3.0,
        hfq_open=line - 5.0, hfq_high=close_raw, hfq_low=line - 6.0,
        hfq_close=close_raw - 3.0,
    )
    evaluation = evaluate_day(plan, next_bar, PlanParameters(), close_raw=close_raw)
    assert evaluation.buy_open_invalid is True
    # 买侧失效 → 该日不进买侧分母(命中标记照记, 但 _tier_stats 会跳过 buy_open_invalid 日)
    assert evaluation.day_category in (CATEGORY_NO_HIT, "EXCLUDED_OPEN_INVALIDATION")
    # 卖侧未失效
    assert evaluation.sell_open_invalid is False
    # 分母排除验证: 用 _tier_stats 直接复算
    from backend.services.research_replay import _tier_stats

    class _FakeDay:
        pass

    fake = _FakeDay()
    fake.day_category = evaluation.day_category
    fake.buy_open_invalid = True
    fake.sell_open_invalid = False
    fake.buy_hits = json.dumps(list(evaluation.buy_hits))
    fake.sell_hits = json.dumps(list(evaluation.sell_hits))
    buy_stats = _tier_stats([fake], "buy")
    sell_stats = _tier_stats([fake], "sell")
    assert buy_stats[0]["denominator"] == 0  # 买侧失效 → 不进买侧分母
    assert sell_stats[0]["denominator"] == 1  # 卖侧正常计入


def test_evaluate_day_both_hit_is_path_ambiguous():
    """双侧同时触达 → BOTH_HIT(PATH_AMBIGUOUS), 不排序不算收益。"""
    bars = _bars_for_replay(260)
    plan = _active_plan(bars[:-1])
    close_raw = bars[-2].raw_close
    from backend.services.research_plan import BarInput
    next_bar = BarInput(
        trade_date=bars[-1].trade_date,
        raw_open=close_raw,
        raw_high=max(plan.sell_levels_raw) + 2.0,
        raw_low=min(plan.buy_levels_raw) - 2.0,
        raw_close=close_raw,
        hfq_open=close_raw, hfq_high=max(plan.sell_levels_raw) + 2.0,
        hfq_low=min(plan.buy_levels_raw) - 2.0, hfq_close=close_raw,
    )
    evaluation = evaluate_day(plan, next_bar, PlanParameters(), close_raw=close_raw)
    assert evaluation.day_category == CATEGORY_BOTH_HIT
    assert all(evaluation.buy_hits) and all(evaluation.sell_hits)


def test_evaluate_day_next_day_corporate_action_double_exclusion():
    """T+1 出现权益事件阶跃 → EXCLUDED_CORP_ACTION(分子分母均排除)。"""
    bars = _bars_for_replay(260)
    plan = _active_plan(bars[:-1])
    close_raw = bars[-2].raw_close
    from backend.services.research_plan import BarInput
    # T+1 raw 整体下移 5%(hfq 不变) → r_t 阶跃 5% >> 容差
    next_bar = BarInput(
        trade_date=bars[-1].trade_date,
        raw_open=close_raw * 0.95, raw_high=close_raw * 0.96,
        raw_low=close_raw * 0.94, raw_close=close_raw * 0.95,
        hfq_open=close_raw, hfq_high=close_raw + 0.4,
        hfq_low=close_raw - 0.4, hfq_close=close_raw,
    )
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200,
                            corporate_action_tolerance=0.002)
    evaluation = evaluate_day(plan, next_bar, params, ratio_step_next=0.05, close_raw=close_raw)
    assert evaluation.day_category == CATEGORY_EXCLUDED_CORP_ACTION
    assert evaluation.buy_hits is None and evaluation.sell_hits is None


def test_evaluate_day_disabled_plan():
    bars = _bars_for_replay(100)  # 预热不足
    params = PlanParameters(lambda_=0.2, quantile_window=60, warmup_bars=200)
    plan = generate_plan("X.SH", bars[-1].trade_date, bars, params)
    assert plan.status == "DISABLED"
    evaluation = evaluate_day(plan, bars[-1], params)
    assert evaluation.day_category == CATEGORY_DISABLED


# ---------------------------------------------------------------------------
# 汇总与切分
# ---------------------------------------------------------------------------

def test_train_split_date_stable_and_no_overlap():
    """切分按开市日升序前 60% 位置; 与参数无关、无重叠。"""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
    split = _train_split_date(dates)
    assert split == dates[59]  # 前 60%(floor(100*0.6)-1 = 59)
    train = [d for d in dates if d <= split]
    validation = [d for d in dates if d > split]
    assert len(train) + len(validation) == len(dates)
    assert not (set(train) & set(validation))
    # 开市日子集变化时切分随之(由日历决定, 不随 λ/窗口)
    assert _train_split_date(dates[:50]) == dates[:50][29]


def test_run_replay_full_pipeline_and_summary_recomputation(db):
    bars = _bars_for_replay(320)
    _seed_snapshot(db, "X.SH", bars)
    _seed_calendar(db, bars)
    start = bars[210].trade_date
    end = bars[300].trade_date
    run_ids = run_replay(
        db, symbol="X.SH", start_date=start, end_date=end,
        lambdas=(0.2,), windows=(120,),
    )
    assert len(run_ids) == 1
    run = db.get(ResearchReplayRun, run_ids[0])
    assert run.status == "DONE"
    days = db.scalars(
        __import__("sqlalchemy").select(ResearchReplayDay)
        .where(ResearchReplayDay.run_id == run_ids[0])
        .order_by(ResearchReplayDay.plan_date)
    ).all()
    assert len(days) == 300 - 210 + 1  # 评价日 210..300 含端点

    from backend.services.research_replay import build_summary
    summary = build_summary(days, run, split_date=run.train_end_date)
    assert summary["retrospective"] is True
    assert summary["train"]["day_count"] + summary["validation"]["day_count"] == len(days)
    # 汇总可由 days 复算: 分母 = 非 DISABLED/EXCLUDED 且未开盘失效侧的日数
    for side in ("buy", "sell"):
        for tier in summary["overall"][side]:
            invalid_attr = "buy_open_invalid" if side == "buy" else "sell_open_invalid"
            expected_denominator = 0
            expected_numerator = 0
            tier_index = ("P50", "P70", "P85").index(tier["tier"])
            for day in days:
                if day.day_category in ("DISABLED", "EXCLUDED_DATA", "EXCLUDED_CORP_ACTION", "CALENDAR_UNVERIFIED"):
                    continue
                if getattr(day, invalid_attr):
                    continue
                hits = json.loads(getattr(day, f"{side}_hits"))
                if hits is None:
                    continue
                expected_denominator += 1
                if hits[tier_index]:
                    expected_numerator += 1
            assert tier["denominator"] == expected_denominator
            assert tier["numerator"] == expected_numerator


def test_run_replay_idempotent_rerun_replaces(db):
    bars = _bars_for_replay(300)
    _seed_snapshot(db, "X.SH", bars)
    _seed_calendar(db, bars)
    start, end = bars[210].trade_date, bars[260].trade_date
    args = dict(symbol="X.SH", start_date=start, end_date=end, lambdas=(0.0, 0.2), windows=(60, 120))
    first = run_replay(db, **args)
    assert len(first) == 4
    day_count = db.query(ResearchReplayDay).count()
    run_count = db.query(ResearchReplayRun).count()
    assert run_count == 4
    second = run_replay(db, **args)
    assert len(second) == 4
    # 幂等: 同唯一键删重建, 不翻倍
    assert db.query(ResearchReplayRun).count() == 4
    assert db.query(ResearchReplayDay).count() == day_count


def test_run_replay_split_not_drifting_across_params(db):
    """同一 symbol 的 12 个 run 切分一致(train_end_date 不随 λ/窗口漂移)。"""
    bars = _bars_for_replay(320)
    _seed_snapshot(db, "X.SH", bars)
    _seed_calendar(db, bars)
    start, end = bars[210].trade_date, bars[300].trade_date
    run_ids = run_replay(db, symbol="X.SH", start_date=start, end_date=end)
    assert len(run_ids) == 12
    runs = db.query(ResearchReplayRun).all()
    splits = {run.train_end_date for run in runs}
    assert len(splits) == 1


def test_run_replay_without_snapshot_raises(db):
    with pytest.raises(ValueError, match="USABLE"):
        run_replay(db, symbol="NOPE.SH", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))


def test_run_replay_days_include_categories(db):
    bars = _bars_for_replay(320)
    _seed_snapshot(db, "X.SH", bars)
    _seed_calendar(db, bars)
    start, end = bars[210].trade_date, bars[300].trade_date
    run_ids = run_replay(db, symbol="X.SH", start_date=start, end_date=end, lambdas=(0.2,), windows=(120,))
    days = db.query(ResearchReplayDay).filter_by(run_id=run_ids[0]).all()
    categories = {day.day_category for day in days}
    assert categories <= {
        CATEGORY_BUY_ONLY, CATEGORY_SELL_ONLY, CATEGORY_BOTH_HIT, CATEGORY_NO_HIT,
        CATEGORY_DISABLED, "EXCLUDED_DATA", CATEGORY_EXCLUDED_CORP_ACTION,
        "EXCLUDED_OPEN_INVALIDATION", "CALENDAR_UNVERIFIED",
    }
    # 未 DISABLED 的日子应有价位与命中标记
    for day in days:
        if day.day_category in (CATEGORY_BUY_ONLY, CATEGORY_SELL_ONLY, CATEGORY_BOTH_HIT, CATEGORY_NO_HIT):
            assert day.buy_levels_raw and day.sell_levels_raw
            assert day.buy_hits and day.sell_hits
