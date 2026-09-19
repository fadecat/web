# -*- coding: utf-8 -*-
"""回测引擎测试(P3): 份额法账本 / 三种再平衡 / 前值填充 / 区间不重置权重 / 回撤 / 合成口径 / 相关性。

全部用**可手算的小样本**, 不触网。这些是算法不变量 —— 端到端复现韭圈儿的验收另跑真实数据。
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from backend.services import backtest, series

D = date


def _contract(symbol: str, pairs: list[tuple[date, float]], basis: str = series.HFQ) -> series.SeriesContract:
    return series.SeriesContract(
        symbol=symbol, asset_class=series.STOCK, price_basis=basis, settle_lag=0,
        dates=tuple(d for d, _ in pairs), prices=tuple(p for _, p in pairs),
    )


class TestRunLedger:
    def test_equal_weight_two_assets_matches_hand_calc(self) -> None:
        """两标的各 50%: T0 归一后份额 = **小数**权重(0.5), NAV(t)=Σ n·P(t)。

        ⚠ NAV 的量纲是"1 元本金"（T0 时 = 1.0）：权重必须按 0.5 当份额用，不能用 50.0。
        """
        a = _contract("A", [(D(2026, 1, 1), 10.0), (D(2026, 1, 2), 11.0)])
        b = _contract("B", [(D(2026, 1, 1), 2.0), (D(2026, 1, 2), 2.2)])
        ledger = backtest.run_ledger({"A": a, "B": b}, {"A": 50.0, "B": 50.0})
        # T0 归一: A: 1.0 → 1.10;  B: 1.0 → 1.10
        assert ledger.dates == (D(2026, 1, 1), D(2026, 1, 2))
        assert ledger.nav[0] == pytest.approx(0.5 * 1.0 + 0.5 * 1.0)
        assert ledger.nav[1] == pytest.approx(0.5 * 1.10 + 0.5 * 1.10)

    def test_t0_is_max_of_first_available_dates(self) -> None:
        """T0 = 各标的首个可用日的**最大值**; 之前的点被丢弃。"""
        early = _contract("E", [(D(2026, 1, 1), 1.0), (D(2026, 1, 3), 1.5)])
        late = _contract("L", [(D(2026, 1, 3), 4.0), (D(2026, 1, 4), 4.4)])
        ledger = backtest.run_ledger({"E": early, "L": late}, {"E": 50.0, "L": 50.0})
        assert ledger.dates[0] == D(2026, 1, 3)  # T0 = max(01-01, 01-03)
        assert D(2026, 1, 1) not in ledger.dates

    def test_union_dates_and_forward_fill(self) -> None:
        """并集 + 前值填充: 某标的缺的那天用它的最新可用值。"""
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 3), 2.0)])
        b = _contract("B", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 1.1), (D(2026, 1, 3), 1.2)])
        ledger = backtest.run_ledger({"A": a, "B": b}, {"A": 50.0, "B": 50.0})
        assert ledger.dates == (D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 3))
        # 01-02: A 前值填充仍是 1.0
        assert ledger.nav[1] == pytest.approx(0.5 * 1.0 + 0.5 * 1.1)

    def test_final_weights_drift_without_rebalance(self) -> None:
        """不平衡 → 末端权重漂移(「当前占比」)。A 涨得多, 占比应超过 50%。"""
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 2.0)])
        b = _contract("B", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 1.0)])
        ledger = backtest.run_ledger({"A": a, "B": b}, {"A": 50.0, "B": 50.0})
        assert ledger.final_weights["A"] > 50.0
        assert ledger.final_weights["A"] + ledger.final_weights["B"] == pytest.approx(100.0)
        # A: 50×2=100, B: 50×1=50 → 100/150 = 66.67%
        assert ledger.final_weights["A"] == pytest.approx(66.6667, abs=1e-3)

    def test_weights_must_sum_to_100(self) -> None:
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 2.0)])
        with pytest.raises(backtest.BacktestError, match="100%"):
            backtest.run_ledger({"A": a}, {"A": 60.0})

    def test_empty_weights_rejected(self) -> None:
        with pytest.raises(backtest.BacktestError, match="没有标的"):
            backtest.run_ledger({}, {})

    def test_missing_series_rejected(self) -> None:
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 2.0)])
        with pytest.raises(backtest.BacktestError, match="标的无数据"):
            backtest.run_ledger({"A": a}, {"A": 50.0, "B": 50.0})

    def test_negative_weight_rejected(self) -> None:
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 2.0)])
        with pytest.raises(backtest.BacktestError, match="非负"):
            backtest.run_ledger({"A": a}, {"A": 150.0, "B": -50.0})


class TestRebalance:
    def _two_assets(self) -> dict[str, series.SeriesContract]:
        # 每个交易日都是"交易日"(简化), 覆盖 2026 年前三个季度
        days = [
            D(2026, 1, 1), D(2026, 3, 31), D(2026, 4, 1), D(2026, 6, 30),
            D(2026, 7, 1), D(2026, 10, 1), D(2026, 12, 31),
        ]
        a_prices = [1.0, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0]
        b_prices = [1.0, 1.1, 1.1, 1.2, 1.2, 1.3, 1.3]
        return {
            "A": _contract("A", list(zip(days, a_prices))),
            "B": _contract("B", list(zip(days, b_prices))),
        }

    def test_quarterly_pulls_back_to_initial_weights(self) -> None:
        contracts = self._two_assets()
        ledger = backtest.run_ledger(contracts, {"A": 50.0, "B": 50.0}, backtest.REBALANCE_QUARTERLY)
        # 季平衡每季拉回 50/50; 但最后一次调仓在 10-01, 之后 A 涨更多 → 略有漂移
        assert abs(ledger.final_weights["A"] - 50.0) < 5.0

    def test_quarterly_aligns_next_yearly_aligns_prev(self) -> None:
        """对齐方式: 季平衡向后顺延、年平衡向前对齐(实测拟合, 不得"顺手改")。

        ⚠ 季平衡: 每个季度月各自对齐一次 —— 若某季的 1 日落在数据窗口外, `pick_next` 会
          顺延到该季度之后的第一个交易日(**实测拟合出来的行为, 不要"修"成跳过**)。
        ⚠ 年平衡: 对齐的是"**次年** 1 月 1 日", 向前取 → 落在**上一年最后交易日**。
          所以判定某年年末是否调仓, 需要数据里存在次年的交易日。
        """
        days = [D(2026, 3, 30), D(2026, 4, 2), D(2026, 12, 30), D(2027, 1, 5)]
        quarterly = backtest.rebalance_dates(days, backtest.REBALANCE_QUARTERLY, days[0])
        assert D(2026, 4, 2) in quarterly      # 04-01 不是交易日 → 向后顺延到 04-02
        assert D(2026, 3, 30) in quarterly     # 01-01 早于窗口 → 顺延到窗口首个交易日
        # 年平衡: 2027-01-01 向前对齐 → 2026-12-30
        assert D(2026, 12, 30) in backtest.rebalance_dates(
            days, backtest.REBALANCE_YEARLY, days[0],
        )

    def test_none_has_no_rebalance_dates(self) -> None:
        days = [D(2026, 1, 1), D(2026, 4, 1)]
        assert backtest.rebalance_dates(days, backtest.REBALANCE_NONE, days[0]) == set()

    def test_unknown_mode_rejected(self) -> None:
        days = [D(2026, 1, 1), D(2026, 4, 1)]
        with pytest.raises(backtest.BacktestError, match="未知再平衡"):
            backtest.rebalance_dates(days, "monthly", days[0])

    def test_rebalance_beats_drift_when_loser_recovers(self) -> None:
        """调仓确实改变了结果 —— 三种模式不能算出同一个数。"""
        contracts = self._two_assets()
        none = backtest.run_ledger(contracts, {"A": 50.0, "B": 50.0}, backtest.REBALANCE_NONE)
        q = backtest.run_ledger(contracts, {"A": 50.0, "B": 50.0}, backtest.REBALANCE_QUARTERLY)
        assert none.nav[-1] != pytest.approx(q.nav[-1])

    def test_rebalanced_nav_stays_same_scale_as_no_rebalance(self) -> None:
        """⭐ 回归: 调仓不得放大 NAV 量纲(移植时踩过的 100× bug)。

        份额必须用**小数**权重: 若把 25.0(百分比)当份额代进 `n = NAV × w / P`,
        每调一次仓就放大 100 倍, 季/年平衡的收益会指数级爆炸(实测 1e79%)。
        这里用多个季度 + 多年, 只要量纲对, 两者 должны 同量级。
        """
        days = [D(y, m, 1) for y in (2026, 2027) for m in range(1, 13)]
        contracts = {
            "A": _contract("A", list(zip(days, [1.0 + 0.01 * i for i in range(len(days))]))),
            "B": _contract("B", list(zip(days, [1.0 + 0.005 * i for i in range(len(days))]))),
        }
        weights = {"A": 50.0, "B": 50.0}
        none = backtest.run_ledger(contracts, weights, backtest.REBALANCE_NONE)
        for mode in (backtest.REBALANCE_QUARTERLY, backtest.REBALANCE_YEARLY):
            ledger = backtest.run_ledger(contracts, weights, mode)
            assert ledger.nav[0] == pytest.approx(1.0)          # T0 处 = 1 元本金
            # 两年最多涨几成 → NAV 不可能超出个位数; 量纲错会到 1eN
            assert 0.5 < ledger.nav[-1] < 10.0, f"{mode} NAV 量纲异常: {ledger.nav[-1]}"
            assert ledger.nav[-1] == pytest.approx(none.nav[-1], rel=0.5)


class TestWindowAndDrawdown:
    def _ledger(self) -> backtest.Ledger:
        a = _contract("A", [
            (D(2026, 1, 1), 1.0), (D(2026, 1, 2), 1.2), (D(2026, 1, 5), 0.9), (D(2026, 1, 6), 1.0),
        ])
        return backtest.run_ledger({"A": a}, {"A": 100.0})

    def test_window_return_is_ratio_of_two_points(self) -> None:
        ledger = self._ledger()
        r = backtest.window_return(ledger, D(2026, 1, 2), D(2026, 1, 6))
        assert r.value == pytest.approx((1.0 / 1.2 - 1) * 100)

    def test_window_return_aligns_backward(self) -> None:
        """区间端点向前对齐交易日(01-03 不是交易日 → 落到 01-02)。"""
        ledger = self._ledger()
        r = backtest.window_return(ledger, D(2026, 1, 3), D(2026, 1, 6))
        assert r.actual_start == D(2026, 1, 2)

    def test_window_return_none_when_out_of_range(self) -> None:
        ledger = self._ledger()
        assert backtest.window_return(ledger, D(2025, 1, 1), D(2025, 6, 1)) is None

    def test_max_drawdown_hand_calc(self) -> None:
        """峰 1.2 → 谷 0.9 = -25%。"""
        ledger = self._ledger()
        assert backtest.max_drawdown(ledger, D(2026, 1, 1), D(2026, 1, 6)) == pytest.approx(-25.0)

    def test_max_drawdown_peak_starts_at_window_first_value(self) -> None:
        """峰从窗口首值起算(窗口外的更高点不算)。"""
        ledger = self._ledger()
        # 窗口从 01-01 起: 首值 1.0 → 峰 1.0; 01-02 的 1.2 之后跌到 0.9 → -25%
        assert backtest.max_drawdown(ledger, D(2026, 1, 1), D(2026, 1, 6)) == pytest.approx(-25.0)


class TestCompositeAndCorrelation:
    def test_latest_day_composite_is_weighted_own_latest_returns(self) -> None:
        """近1日 = Σ wᵢ × rᵢ(各自最新日) —— 不是账本末两日之比。"""
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 1.1)])
        b = _contract("B", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 0.95)])
        got = backtest.latest_day_composite({"A": a, "B": b}, {"A": 60.0, "B": 40.0})
        assert got == pytest.approx(0.6 * 10.0 + 0.4 * (-5.0))

    def test_correlation_perfectly_correlated(self) -> None:
        days = [D(2026, 1, i) for i in range(1, 6)]
        a = _contract("A", list(zip(days, [1.0, 1.1, 1.21, 1.331, 1.4641])))
        b = _contract("B", list(zip(days, [2.0, 2.2, 2.42, 2.662, 2.9282])))
        got = backtest.correlation({"A": a, "B": b}, days[0], days[-1])
        assert got["matrix"][0][1] == pytest.approx(1.0)

    def test_correlation_uses_intersection_alignment(self) -> None:
        """pairwise 交集: 只在两天都有数据的日期上算; 样本数要报出来。"""
        a = _contract("A", [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 1.1), (D(2026, 1, 5), 1.2)])
        b = _contract("B", [(D(2026, 1, 1), 1.0), (D(2026, 1, 5), 1.3), (D(2026, 1, 6), 1.4)])
        got = backtest.correlation({"A": a, "B": b}, D(2026, 1, 1), D(2026, 1, 6))
        # A 的日收益日 = {01-02, 01-05}; B 的 = {01-05, 01-06} → 交集 {01-05} → n=1 <3 → None
        assert got["matrix"][0][1] is None
        assert got["sample_sizes"]["A|B"] == 1

    def test_correlation_range_is_independent_of_backtest(self) -> None:
        days = [D(2026, 1, i) for i in range(1, 6)]
        a = _contract("A", list(zip(days, [1.0, 1.1, 1.21, 1.331, 1.4641])))
        b = _contract("B", list(zip(days, [2.0, 2.2, 2.42, 2.662, 2.9282])))
        cut = backtest.correlation({"A": a, "B": b}, D(2026, 1, 1), D(2026, 1, 3))
        assert cut["start"] == "2026-01-01" and cut["end"] == "2026-01-03"

    def test_pearson_needs_three_points(self) -> None:
        assert backtest.pearson([1.0, 2.0], [1.0, 2.0]) is None


class TestMetricsAndWindows:
    def _ledger(self) -> backtest.Ledger:
        a = _contract("A", [
            (D(2026, 1, 1), 1.0), (D(2026, 2, 2), 1.1), (D(2026, 3, 3), 0.9), (D(2026, 4, 4), 1.2),
        ])
        return backtest.run_ledger({"A": a}, {"A": 100.0})

    def test_metrics_present_and_mdd_negative(self) -> None:
        m = backtest.performance_metrics(self._ledger())
        assert m["mdd"] < 0
        assert m["vol"] is not None and m["vol"] > 0
        assert m["cagr"] is not None

    def test_worst_month(self) -> None:
        # 02 月末 1.1 → 03 月末 0.9 = -18.18%
        assert backtest._worst_month(self._ledger()) == pytest.approx((0.9 / 1.1 - 1) * 100)

    def test_metrics_on_too_short_series_is_none_not_error(self) -> None:
        a = _contract("A", [(D(2026, 1, 1), 1.0)])
        ledger = backtest.run_ledger({"A": a}, {"A": 100.0})
        m = backtest.performance_metrics(ledger)
        assert all(v is None for v in m.values())

    def test_resolve_window_start_keys(self) -> None:
        end = D(2026, 9, 18)
        assert backtest.resolve_window_start("m1", end, inception=D(2013, 4, 26)) == D(2026, 8, 19)
        assert backtest.resolve_window_start("ytd", end, inception=None) == D(2026, 1, 1)
        assert backtest.resolve_window_start("y1", end, inception=None) == D(2025, 9, 18)
        assert backtest.resolve_window_start("inception", end, inception=D(2013, 4, 26)) == D(2013, 4, 26)

    def test_resolve_windows_marks_d1_as_composite(self) -> None:
        ledger = self._ledger()
        contracts = {"A": _contract("A", [
            (D(2026, 1, 1), 1.0), (D(2026, 2, 2), 1.1), (D(2026, 3, 3), 0.9), (D(2026, 4, 4), 1.2),
        ])}
        windows = backtest.resolve_windows(ledger, contracts)
        assert set(windows) == set(backtest.WINDOW_KEYS)
        assert windows["d1"]["composite"] is True          # 合成口径
        assert windows["m1"]["actual_start"] is not None   # 实际起点要回传
        assert windows["d1"]["actual_start"] is None

    def test_resolve_window_start_rejects_unknown(self) -> None:
        with pytest.raises(backtest.BacktestError, match="未知区间键"):
            backtest.resolve_window_start("y5", D(2026, 9, 18), inception=None)


def test_normalize_on_t0_uses_fill_forward_value() -> None:
    """T0 不是该标的交易日时, p0 取 <= T0 的最后一点(与账本前值填充一致)。

    这样 01-05 相对"T0 当时持有的价值"是 +50%, 而不是忘记基准直接归一为 1.0。
    """
    c = _contract("A", [(D(2026, 1, 1), 2.0), (D(2026, 1, 5), 3.0)])
    got = backtest.normalize_on_t0(c, D(2026, 1, 3))
    assert got == {D(2026, 1, 5): pytest.approx(1.5)}


def test_normalize_equivalent_to_pct_chain_when_all_have_t0_point() -> None:
    """实用场景(所有标的在 T0 都有点)下, "归一"与"pct 链式"完全等价。

    验收脚本用的是 pct 链式(`price *= (1+pct)`, T0 处 = 1.0); 本引擎用"价格比 T0 归一"。
    两者只在**所有标的都在 T0 有点**时严格相同 —— 而 T0 = max(各首个可用日),
    取得该最大值的标的必然在 T0 有点, 其余标的在实际数据里也都有(否则脚本会 KeyError)。
    所以两条路径对全部区间收益给出同样的数字。
    """
    t0, d2, d3 = D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 5)
    # 价格序列 1.0 → 1.1 → 0.88, 对应 pct 链 +10% / -20%
    c = _contract("A", [(t0, 1.0), (d2, 1.1), (d3, 0.88)])
    normalized = backtest.normalize_on_t0(c, t0)
    chained: dict[date, float] = {t0: 1.0}
    price = 1.0
    for pct in (0.10, -0.20):
        price *= (1 + pct)
        chained[[d2, d3][len(chained) - 1]] = price
    for day in (t0, d2, d3):
        assert normalized[day] == pytest.approx(chained[day])


def test_normalize_returns_empty_before_t0() -> None:
    c = _contract("A", [(D(2026, 1, 1), 2.0)])
    assert backtest.normalize_on_t0(c, D(2026, 6, 1)) == {}
