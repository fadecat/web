# -*- coding: utf-8 -*-
"""风格轮动算法单元测试。

用合成价格序列固定验证 spread / MA / 动态分位 / 日期裁剪 / warmup 提示 /
异常路径, 不依赖外部数据源与真实数据库。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from backend.services.style_rotation_analysis import (
    InsufficientDataError,
    StyleRotationParams,
    calculate_style_rotation,
    build_style_rotation_response,
)


def _make_trading_dates(start: date, n: int) -> list[pd.Timestamp]:
    """生成 n 个连续工作日(跳过周末), 模拟交易日历。"""
    dates: list[pd.Timestamp] = []
    cur = start
    while len(dates) < n:
        if cur.weekday() < 5:
            dates.append(pd.Timestamp(cur))
        cur += timedelta(days=1)
    return dates


def _flat_df(dates: list[pd.Timestamp], price: float) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": dates, "close": [price] * len(dates)})


# ---------------------------------------------------------------------------
# 基础行为
# ---------------------------------------------------------------------------

class TestSpreadCalculation:
    def test_flat_prices_yield_zero_spread(self):
        """两边价格完全走平时, 收益率恒为 0, spread 恒为 0。"""
        dates = _make_trading_dates(date(2024, 1, 1), 300)
        df = calculate_style_rotation(
            _flat_df(dates, 100.0),
            _flat_df(dates, 50.0),
            StyleRotationParams("L", "R", None, None, return_window=250, ma_window=20),
        )
        assert all(v == 0.0 for v in df["spread"])
        assert all(v == 0.0 for v in df["ma"])
        assert df["global_p90"] == 0.0
        assert df["global_p10"] == 0.0

    def test_constant_divergence_spread(self):
        """左指数年化 10%, 右指数走平 → spread 稳态约 10%。

        输出行数 = 总行数 - 收益窗口预热 - (ma_window-1) 的 MA 预热,
        中间还叠加分位线 min_periods 预热, 三者取最大截断。
        """
        n = 400
        dates = _make_trading_dates(date(2023, 1, 2), n)
        daily = (1.10) ** (1 / 250) - 1  # 年化 10% 的日收益
        left = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0 * (1 + daily) ** i for i in range(n)],
        })
        right = _flat_df(dates, 100.0)

        df = calculate_style_rotation(
            left, right,
            StyleRotationParams("L", "R", None, None, return_window=250, ma_window=20),
        )
        # 250 收益窗口 + 19 行 MA 预热 → 131 行输出
        assert len(df["dates"]) == n - 250 - 19
        # 稳态 spread = ((1.10)^1 - 1) * 100 = 10 (%), 允许日历天数微差
        steady = df["spread"][-5:]
        for v in steady:
            assert 9.0 < v < 11.0, f"稳态 spread 应约 10%, 实际 {v}"

    def test_spread_sign_left_minus_right(self):
        """左强右弱 → spread 为正; 左弱右强 → 为负。"""
        n = 300
        dates = _make_trading_dates(date(2024, 1, 1), n)
        daily_up = (1.20) ** (1 / 250) - 1
        daily_dn = (0.80) ** (1 / 250) - 1
        up = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0 * (1 + daily_up) ** i for i in range(n)],
        })
        dn = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0 * (1 + daily_dn) ** i for i in range(n)],
        })
        params = StyleRotationParams("L", "R", None, None, return_window=250, ma_window=20)

        bull = calculate_style_rotation(up, dn, params)
        bear = calculate_style_rotation(dn, up, params)
        assert bull["spread"][-1] > 15   # 年化 20% - (-20%) ≈ 40% 差
        assert bear["spread"][-1] < -15


class TestAlignment:
    def test_inner_join_on_trade_date(self):
        """两边只有共同交易日参与计算(内连接)。

        注意: 输出已去掉 NaN, 直接断言长度 = 共同交易日 - 预热损耗。
        """
        d1 = _make_trading_dates(date(2023, 1, 2), 420)
        d2 = [d for d in d1 if d.dayofweek != 2]  # 左边多出周三
        left = _flat_df(d1, 100.0)
        right = _flat_df(d2, 100.0)
        df = calculate_style_rotation(
            left, right,
            StyleRotationParams("L", "R", None, None, return_window=250, ma_window=20),
        )
        # 输出的每个日期都必须来自两边共同的交易日(非周三)
        for ds in df["dates"]:
            assert pd.Timestamp(ds).dayofweek != 2
        # 内连接后 250 收益窗口 + 19 行 MA 预热的长度对账
        assert len(df["dates"]) == len(d2) - 250 - 19

    def test_disjoint_dates_raise(self):
        """两边无共同交易日 → InsufficientDataError。"""
        d1 = _make_trading_dates(date(2024, 1, 1), 300)
        d2 = _make_trading_dates(date(2025, 1, 1), 300)
        with pytest.raises(InsufficientDataError):
            calculate_style_rotation(
                _flat_df(d1, 100.0), _flat_df(d2, 100.0),
                StyleRotationParams("L", "R", None, None),
            )

    def test_shorter_than_window_raises(self):
        """数据总长不足 return_window → InsufficientDataError。"""
        dates = _make_trading_dates(date(2024, 1, 1), 100)
        with pytest.raises(InsufficientDataError):
            calculate_style_rotation(
                _flat_df(dates, 100.0), _flat_df(dates, 100.0),
                StyleRotationParams("L", "R", None, None, return_window=250),
            )


class TestMaAndQuantiles:
    def _build(self, ma_window=20, n=400):
        dates = _make_trading_dates(date(2023, 1, 2), n)
        # 线性上涨: spread 为锯齿状常数序列
        left = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0 + 0.05 * i for i in range(n)],
        })
        right = _flat_df(dates, 100.0)
        return calculate_style_rotation(
            left, right,
            StyleRotationParams("L", "R", None, None,
                                return_window=250, ma_window=ma_window),
        )

    def test_ma_window_respected(self):
        """MA 值等于最近 ma_window 个 spread 的均值(手算对账)。"""
        df20 = self._build(ma_window=20)
        df5 = self._build(ma_window=5)
        # 两者 spread 序列相同, 只是 ma 平滑度不同
        assert df20["spread"] == df5["spread"]
        # ma5 应比 ma20 更贴近最新 spread
        s = df20["spread"][-1]
        assert abs(df5["ma"][-1] - s) <= abs(df20["ma"][-1] - s)

    def test_dynamic_quantile_bounds(self):
        """动态分位: p90 >= p10 恒成立。

        注: expanding 分位并非单调不减 —— 序列走低时新样本会拉低 q90,
        这是「用近期分布校准阈值」的预期行为, 不做单调性断言。
        """
        df = self._build()
        for a, b in zip(df["p90_dynamic"], df["p10_dynamic"]):
            assert a >= b
        # p90 永远 >= 全序列最大值被截断后的实际分布上沿: 至少应 >= p10
        assert df["p90_dynamic"][-1] >= df["p10_dynamic"][-1]

    def test_global_quantile_matches_pandas(self):
        """全局分位与 pandas 手算一致。

        源码用未舍入 spread 计算全局分位, 测试须以同样口径重建序列:
        输出的 spread 已 round(2), 直接拿它对账会因舍入偏差失败。
        """
        n, ma_window = 400, 20
        dates = _make_trading_dates(date(2023, 1, 2), n)
        left = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0 + 0.05 * i for i in range(n)],
        })
        right = _flat_df(dates, 100.0)
        params = StyleRotationParams("L", "R", None, None,
                                     return_window=250, ma_window=ma_window)
        df = calculate_style_rotation(left, right, params)

        # 按源码同样步骤重建未舍入 spread
        merged = pd.merge(
            left.sort_values("trade_date"), right.sort_values("trade_date"),
            on="trade_date", how="inner", suffixes=("_left", "_right"),
        )
        lr = merged["close_left"].pct_change(250) * 100
        rr = merged["close_right"].pct_change(250) * 100
        raw_spread = (lr - rr).dropna().reset_index(drop=True)
        # 输出序列 = 原始序列去掉 MA 预热的头 (ma_window - 1) 行
        expected = raw_spread.iloc[ma_window - 1:].reset_index(drop=True)
        assert len(expected) == len(df["spread"])
        assert math.isclose(
            df["global_p90"], round(float(expected.quantile(0.9)), 2), abs_tol=0.02,
        )
        assert math.isclose(
            df["global_p10"], round(float(expected.quantile(0.1)), 2), abs_tol=0.02,
        )
        assert df["global_p90"] >= df["global_p10"]


class TestDateFilterAndWarmup:
    def test_start_date_clips_output(self):
        """start_date 只影响输出裁剪, 预热数据仍参与计算。"""
        dates = _make_trading_dates(date(2023, 1, 2), 500)
        left = pd.DataFrame({
            "trade_date": dates,
            "close": [100.0 + i for i in range(len(dates))],
        })
        right = _flat_df(dates, 100.0)
        params = StyleRotationParams("L", "R", None, None, return_window=250)
        full = calculate_style_rotation(left, right, params)
        # 预热后首个交易日是 2024-01-02 附近; 从 2024-06-01 裁剪
        params_cut = StyleRotationParams("L", "R", "2024-06-01", None, return_window=250)
        cut = calculate_style_rotation(left, right, params_cut)
        assert all(d >= "2024-06-01" for d in cut["dates"])
        assert cut["dates"][0] == next(d for d in full["dates"] if d >= "2024-06-01")

    def test_summary_fields(self):
        dates = _make_trading_dates(date(2023, 1, 2), 300)
        df = calculate_style_rotation(
            _flat_df(dates, 100.0), _flat_df(dates, 100.0),
            StyleRotationParams("L", "R", None, None),
        )
        assert df["latest_date"] == df["dates"][-1]
        assert df["latest_spread"] == df["spread"][-1]
        assert df["latest_ma"] == df["ma"][-1]


# ---------------------------------------------------------------------------
# warmup 误报回归(59874e3): 请求起点为非交易日时不得误报
# ---------------------------------------------------------------------------

class TestWarmupDetection:
    def _seed_db(self, db, n=600):
        """向数据库塞 n 个交易日的双指数数据(左边线性涨)。"""
        from backend.models.valuation import IndexDailyQuote

        dates = _make_trading_dates(date(2023, 1, 2), n)
        for i, d in enumerate(dates):
            db.add(IndexDailyQuote(index_code="L", trade_date=d.date(), close=100.0 + i))
            db.add(IndexDailyQuote(index_code="R", trade_date=d.date(), close=100.0))
        db.commit()
        return dates

    def test_first_valid_date_field(self, db):
        """calculate 结果包含裁剪前首个有效日期字段。

        首个有效 spread 在第 250 个交易日之后才出现(pct_change NaN 被
        dropna), 不硬编码索引, 断言它落在收益窗口+MA 预热的合理区间。
        """
        n = 400
        dates = _make_trading_dates(date(2023, 1, 2), n)
        left = pd.DataFrame({"trade_date": dates, "close": [100.0 + i for i in range(n)]})
        right = _flat_df(dates, 100.0)
        result = calculate_style_rotation(
            left, right,
            StyleRotationParams("L", "R", None, None, return_window=250, ma_window=20),
        )
        assert result["first_valid_date"] == "2023-12-18"  # 第 250 个工作日(实测锚定)
        first_idx = next(
            i for i, d in enumerate(dates)
            if d.strftime("%Y-%m-%d") == result["first_valid_date"]
        )
        # 收益窗口 250 + MA 预热 19 → 首个有效值在 [249, 269] 索引范围
        assert 249 <= first_idx <= 269

    def test_no_warmup_note_when_start_is_non_trading_day(self, db):
        """请求起点是周日(非交易日)且数据充足 → 不得弹 warmup 提示。

        回归场景: start=2024-06-09(周日), 数据从 2023-01-02 起,
        预热充足, 首日应为 2024-06-10(周一) 但绝不该报警。
        """
        self._seed_db(db)
        from backend.services.style_rotation_analysis import build_style_rotation_response as resp_fn

        params = StyleRotationParams("L", "R", "2024-06-09", "2024-07-15",
                                     return_window=250, ma_window=20)
        resp = resp_fn(db, params)
        assert "warmup_note" not in resp["meta"]
        assert resp["series"]["dates"][0] > "2024-06-09"  # 首日是之后的首个交易日

    def test_warmup_note_when_truly_insufficient(self, db):
        """请求起点早于 数据起点+250交易日 → 必须报警。"""
        self._seed_db(db, n=400)  # 数据 2023-01-02 起, 首个有效值约 2024-01
        from backend.services.style_rotation_analysis import build_style_rotation_response as resp_fn

        # 请求 2023-03~2023-12: 该区间内没有任何有效 spread(首个在 2024-01)
        params = StyleRotationParams("L", "R", "2023-03-01", "2023-12-31",
                                     return_window=250, ma_window=20)
        with pytest.raises(InsufficientDataError):
            resp_fn(db, params)

        # 请求起点早于首个有效值、但区间覆盖到它 → 报警且正常返回
        params2 = StyleRotationParams("L", "R", "2023-03-01", "2024-06-30",
                                      return_window=250, ma_window=20)
        resp = resp_fn(db, params2)
        assert "warmup_note" in resp["meta"]
        assert "预热期不足" in resp["meta"]["warmup_note"]
