# -*- coding: utf-8 -*-
"""回测编排层测试(P3): 前置校验 / 结果正确性 / 口径不变量 / Run 幂等 / 端点契约。

手法与既有测试一致: 内存库 + 手工构造序列, **不触网**。
序列用场外基金(`fund_nav_daily` 单表, `adj_nav` 即分红再投净值) —— 构造最简单,
且它在产品路径里正是贡献 3000+ 点长历史的那一类。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from backend.models.portfolio import Portfolio, PortfolioAsset
from backend.models.research import FundNavDaily
from backend.services import backtest, backtest_service, portfolio_store, research_store

BASE = "/api/portfolio"

# 五个连续交易日(A: 涨; B: 先涨后跌) —— 全部用例共用, 便于手算
D = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9)]


# ---------------------------------------------------------------------------
# 夹具与构造
# ---------------------------------------------------------------------------

def _register(db, symbol: str, name: str, sec_type: str = "FUND") -> None:
    research_store.upsert_securities(db, [{
        "symbol": symbol, "name": name, "type": sec_type.lower(),
        "source": "danjuan" if sec_type == "FUND" else "tencent",
        "selection_list": "组合实验室",
    }])


def _nav(db, symbol: str, points: dict[date, float]) -> None:
    """写基金净值序列。测试只关心 adj_nav(链式复权净值), daily_return_pct 不影响回测。"""
    for day, value in points.items():
        db.add(FundNavDaily(
            symbol=symbol, nav_date=day, unit_nav=value,
            daily_return_pct=None, adj_nav=value, source="danjuan",
        ))
    db.commit()


def _portfolio(db, weights: dict[str, float], *, name: str = "测试组合",
               added_at: date | None = None) -> int:
    portfolio = Portfolio(name=name, status="active")
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    stamp = datetime.combine(added_at or D[0], datetime.min.time())
    for index, (symbol, weight) in enumerate(weights.items()):
        db.add(PortfolioAsset(
            portfolio_id=portfolio.id, symbol=symbol, target_weight=weight,
            sort_order=index, added_at=stamp,
        ))
    db.commit()
    return portfolio.id


def _seed_two_funds(db, *, weights: dict[str, float] | None = None) -> int:
    """A: 1.0→1.1→1.21(全程 +21%);  B: 2.0→2.2→2.0(先涨后跌)。

    各 50% → 份额 n_A = 0.5, n_B = 0.25, NAV = 1.0 / 1.1 / 1.105。
    """
    _register(db, "100001.OF", "测试基金A")
    _register(db, "100002.OF", "测试基金B")
    _nav(db, "100001.OF", {D[0]: 1.0, D[1]: 1.1, D[2]: 1.21})
    _nav(db, "100002.OF", {D[0]: 2.0, D[1]: 2.2, D[2]: 2.0})
    return _portfolio(db, weights or {"100001.OF": 50.0, "100002.OF": 50.0})


# ---------------------------------------------------------------------------
# 前置校验
# ---------------------------------------------------------------------------

class TestGuards:
    def test_empty_portfolio_is_rejected(self, db) -> None:
        pid = _portfolio(db, {})
        with pytest.raises(backtest_service.BacktestServiceError, match="还没有成员"):
            backtest_service.compute(db, pid)

    def test_missing_weight_is_rejected(self, db) -> None:
        _register(db, "100001.OF", "测试基金A")
        _nav(db, "100001.OF", {D[0]: 1.0, D[1]: 1.1})
        portfolio = Portfolio(name="缺权重", status="active")
        db.add(portfolio)
        db.commit()
        db.add(PortfolioAsset(portfolio_id=portfolio.id, symbol="100001.OF", target_weight=None))
        db.commit()
        with pytest.raises(backtest_service.BacktestServiceError, match="尚未设置比例"):
            backtest_service.compute(db, portfolio.id)

    def test_weight_sum_must_be_100(self, db) -> None:
        pid = _seed_two_funds(db, weights={"100001.OF": 60.0, "100002.OF": 30.0})
        with pytest.raises(backtest_service.BacktestServiceError, match="合计必须为 100%"):
            backtest_service.compute(db, pid)

    def test_zero_weight_is_rejected(self, db) -> None:
        pid = _seed_two_funds(db, weights={"100001.OF": 100.0, "100002.OF": 0.0})
        with pytest.raises(backtest_service.BacktestServiceError):
            backtest_service.compute(db, pid)

    def test_empty_series_is_rejected(self, db) -> None:
        _register(db, "100001.OF", "测试基金A")
        _register(db, "100003.OF", "无数据基金")
        _nav(db, "100001.OF", {D[0]: 1.0, D[1]: 1.1})
        pid = _portfolio(db, {"100001.OF": 50.0, "100003.OF": 50.0})
        with pytest.raises(backtest_service.BacktestServiceError, match="尚无数据"):
            backtest_service.compute(db, pid)

    def test_unknown_rebalance_is_rejected(self, db) -> None:
        pid = _seed_two_funds(db)
        with pytest.raises(backtest_service.BacktestServiceError, match="未知再平衡方式"):
            backtest_service.compute(db, pid, rebalance="monthly")

    def test_unknown_portfolio_is_rejected(self, db) -> None:
        with pytest.raises(backtest_service.BacktestServiceError, match="未知组合"):
            backtest_service.compute(db, 999999)


# ---------------------------------------------------------------------------
# 结果正确性(全部可手算)
# ---------------------------------------------------------------------------

class TestCompute:
    def test_selected_return_matches_hand_calc(self, db) -> None:
        """50/50 建仓: NAV 1.0 → 1.105, 区间收益 = +10.5%。"""
        pid = _seed_two_funds(db)
        result = backtest_service.compute(db, pid)["result"]
        assert result["selected_return"] == pytest.approx(10.5, abs=1e-6)
        assert result["t0_date"] == D[0].isoformat()
        assert result["actual_start"] == D[0].isoformat()
        assert result["actual_end"] == D[2].isoformat()

    def test_t0_is_max_of_first_available_dates(self, db) -> None:
        """B 从 D[1] 才有数据 → T0 = D[1], D[0] 被丢弃。"""
        _register(db, "100001.OF", "测试基金A")
        _register(db, "100002.OF", "测试基金B")
        _nav(db, "100001.OF", {D[0]: 1.0, D[1]: 1.1, D[2]: 1.21})
        _nav(db, "100002.OF", {D[1]: 2.0, D[2]: 2.2})
        pid = _portfolio(db, {"100001.OF": 50.0, "100002.OF": 50.0})
        result = backtest_service.compute(db, pid)["result"]
        assert result["t0_date"] == D[1].isoformat()
        assert result["actual_start"] == D[1].isoformat()

    def test_start_before_t0_moves_forward_without_error(self, db) -> None:
        """用户选的起始日早于 T0 → **前移**到 T0(不报错、不静默改模式)。"""
        pid = _seed_two_funds(db)
        result = backtest_service.compute(db, pid, start=date(2000, 1, 1))["result"]
        assert result["actual_start"] == D[0].isoformat()
        assert result["start_date"] == "2000-01-01"  # 用户选择原样回显

    def test_windows_seven_cells_with_composite_d1(self, db) -> None:
        """收益条七格齐全; 「近1日」是合成口径(composite=True), 不是账本末两日之比。"""
        pid = _seed_two_funds(db)
        result = backtest_service.compute(db, pid)["result"]
        assert set(result["windows"]) == set(backtest.WINDOW_KEYS)
        assert result["windows"]["d1"]["composite"] is True
        assert result["windows"]["w1"]["composite"] is False
        # 手算: 漂移权重 A=0.605/1.105, B=0.5/1.105; r_A=+10%, r_B=-9.0909%
        expected = (0.605 / 1.105) * 0.10 + (0.5 / 1.105) * (2.0 / 2.2 - 1.0)
        assert result["windows"]["d1"]["value"] == pytest.approx(expected * 100.0, abs=1e-6)

    def test_windows_shorter_than_data_fall_back_to_t0(self, db) -> None:
        """⭐ 数据历史短于区间长度时(如 T0=2024-06 的"近3年")**退化为自 T0 起算**。

        不这么做的话, 用户只要往组合里加一只新标的, 收益条就会大面积空白。
        实际起点由 `actual_start` 如实回显, 不隐瞒。
        """
        pid = _seed_two_funds(db)
        result = backtest_service.compute(db, pid)["result"]
        for key in ("w1", "m1", "ytd", "y1", "y3", "inception"):
            cell = result["windows"][key]
            assert cell is not None, f"{key} 不应为空"
            assert cell["actual_start"] == D[0].isoformat()   # 全部退化为 T0
            assert cell["value"] == pytest.approx(10.5, abs=1e-6)

    def test_windows_do_not_follow_selected_rebalance(self, db) -> None:
        """⭐ 收益条恒用**不平衡**账本 —— 切换再平衡不得改变顶部七格。"""
        pid = _seed_two_funds(db)
        none_windows = backtest_service.compute(db, pid, rebalance="none")["result"]["windows"]
        quarterly = backtest_service.compute(db, pid, rebalance="quarterly")["result"]["windows"]
        assert none_windows == quarterly

    def test_rebalance_changes_curve_but_not_windows(self, db) -> None:
        """再平衡改的是曲线/指标。数据跨季平衡调仓日(4/1)且"先跌后涨", 故两者必然不同。

        手算(各 50%, 价格 A: 1.0→0.5→0.5→1.5→1.5;  B: 1.0→1.5→1.5→1.5→1.5):
        - 不平衡: NAV 始终 = 0.5·A + 0.5·B → 末值 1.5 (+50%)
        - 季平衡: 4/1 调回 50/50(n_A=1.0, n_B=1/3) → 末值 2.0 (+100%)
        """
        q = [date(2026, 3, 30), date(2026, 3, 31), date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]
        _register(db, "100001.OF", "测试基金A")
        _register(db, "100002.OF", "测试基金B")
        _nav(db, "100001.OF", {q[0]: 1.0, q[1]: 0.5, q[2]: 0.5, q[3]: 1.5, q[4]: 1.5})
        _nav(db, "100002.OF", {q[0]: 1.0, q[1]: 1.5, q[2]: 1.5, q[3]: 1.5, q[4]: 1.5})
        pid = _portfolio(db, {"100001.OF": 50.0, "100002.OF": 50.0}, added_at=q[0])

        none_run = backtest_service.compute(db, pid, rebalance="none")["result"]
        quarter_run = backtest_service.compute(db, pid, rebalance="quarterly")["result"]
        assert none_run["selected_return"] == pytest.approx(50.0, abs=1e-6)
        assert quarter_run["selected_return"] == pytest.approx(100.0, abs=1e-6)
        assert none_run["windows"] == quarter_run["windows"]      # 顶部七格不变
        assert none_run["metrics"] != quarter_run["metrics"]      # 指标区随再平衡变

    def test_correlation_start_aligns_backward(self, db) -> None:
        """⭐ 相关性起点**向后顺延**, 与回测的向前对齐相反(韭圈儿实测的怪癖)。"""
        days = [D[0], D[2], D[4]]
        assert backtest_service._correlation_start(days, D[1]) == D[2]   # 向后顺延
        assert backtest._pick_prev(days, D[1]) == D[0]                    # 向前对齐(对照)

    def test_correlation_ignores_rebalance(self, db) -> None:
        pid = _seed_two_funds(db)
        a = backtest_service.compute(db, pid, rebalance="none")["result"]["correlation"]
        b = backtest_service.compute(db, pid, rebalance="yearly")["result"]["correlation"]
        assert a == b

    def test_drawdown_detail_peak_trough_recovery(self, db) -> None:
        """单标的 100%: NAV 1.0 → 1.2 → 0.9 → 1.05 → 1.3 → 回撤 -25%, 谷后第 2 个交易日修复。"""
        _register(db, "100004.OF", "单边基金")
        _nav(db, "100004.OF", {D[0]: 1.0, D[1]: 1.2, D[2]: 0.9, D[3]: 1.05, D[4]: 1.3})
        pid = _portfolio(db, {"100004.OF": 100.0})
        detail = backtest_service.compute(db, pid)["result"]["drawdown"]
        assert detail["value"] == pytest.approx(-25.0, abs=1e-6)
        assert detail["peak_date"] == D[1].isoformat()
        assert detail["trough_date"] == D[2].isoformat()
        assert detail["recovery_date"] == D[4].isoformat()
        assert detail["recovery_days"] == 2

    def test_drawdown_unrecovered_gives_none(self, db) -> None:
        _register(db, "100004.OF", "单边基金")
        _nav(db, "100004.OF", {D[0]: 1.0, D[1]: 1.2, D[2]: 0.9})
        pid = _portfolio(db, {"100004.OF": 100.0})
        detail = backtest_service.compute(db, pid)["result"]["drawdown"]
        assert detail["value"] == pytest.approx(-25.0, abs=1e-6)
        assert detail["recovery_date"] is None
        assert detail["recovery_days"] is None

    def test_asset_rows_carry_basis_and_drift_weight(self, db) -> None:
        pid = _seed_two_funds(db)
        result = backtest_service.compute(db, pid)["result"]
        rows = {r["symbol"]: r for r in result["assets"]}
        assert rows["100001.OF"]["price_basis"] == "NAV_ADJ"
        assert rows["100001.OF"]["target_weight"] == 50.0
        # 漂移权重: A 涨到 0.605/1.105 = 54.75%
        assert rows["100001.OF"]["current_weight"] == pytest.approx(0.605 / 1.105 * 100, abs=1e-3)
        assert rows["100002.OF"]["current_weight"] == pytest.approx(0.5 / 1.105 * 100, abs=1e-3)
        # 日涨幅: B 末两日 2.0/2.2-1 = -9.09%
        assert rows["100002.OF"]["daily_return"] == pytest.approx(-9.0909, abs=1e-3)
        assert rows["100002.OF"]["daily_return_date"] == D[2].isoformat()

    def test_since_added_return_uses_own_start(self, db) -> None:
        """「添加后的收益」= 该标的自身自其添加日的收益, **与权重无关**。"""
        pid = _seed_two_funds(db)
        result = backtest_service.compute(db, pid)["result"]
        rows = {r["symbol"]: r for r in result["assets"]}
        assert rows["100001.OF"]["since_added_return"] == pytest.approx(21.0, abs=1e-6)  # 1.0→1.21
        assert rows["100002.OF"]["since_added_return"] == pytest.approx(0.0, abs=1e-6)   # 2.0→2.0

    def test_benchmark_index_and_symbol_styles(self, db) -> None:
        """基准可为指数(价格指数)或普通标的; 取不到时**不阻断回测**, 只是没有对照线。"""
        from backend.models.valuation import IndexDailyQuote
        pid = _seed_two_funds(db)
        for day, close in ((D[0], 3000.0), (D[1], 3300.0), (D[2], 3600.0)):
            db.add(IndexDailyQuote(index_code="000300", trade_date=day, close=close))
        db.commit()

        result = backtest_service.compute(db, pid, benchmark_symbol="000300")["result"]
        assert result["benchmark"]["price_basis"] == "PRICE"
        assert result["benchmark"]["total_return"] == pytest.approx(20.0, abs=1e-6)
        assert any("价格指数" in note for note in result["price_basis_note"])

        missing = backtest_service.compute(db, pid, benchmark_symbol="999999.SH")["result"]
        assert missing["benchmark"] is None

    def test_nav_curve_is_normalized_at_window_start(self, db) -> None:
        """曲线在区间起点归一为 1.0(页面纵轴"以区间起点为 0%"), 长度 = 区间交易日数。"""
        pid = _seed_two_funds(db)
        bundled = backtest_service.compute(db, pid)
        assert bundled["sliced"].nav[0] == pytest.approx(1.0)
        assert len(bundled["sliced"].dates) == 3


# ---------------------------------------------------------------------------
# Run 落库与幂等
# ---------------------------------------------------------------------------

class TestRunPersistence:
    def test_run_is_persisted_with_snapshot(self, db) -> None:
        pid = _seed_two_funds(db)
        run = backtest_service.run_backtest(db, pid)
        assert run["status"] == "success"
        assert run["t0_date"] == D[0].isoformat()
        assert [m["symbol"] for m in run["members"]] == ["100001.OF", "100002.OF"]
        assert run["result"]["selected_return"] == pytest.approx(10.5, abs=1e-6)
        assert run["nav"]["nav"][0] == pytest.approx(1.0)
        assert run["points"] == 3

    def test_same_input_reuses_run(self, db) -> None:
        """相同组合 + 相同参数 → 复用同一个 Run(幂等, 不重复计算)。"""
        pid = _seed_two_funds(db)
        first = backtest_service.run_backtest(db, pid)
        second = backtest_service.run_backtest(db, pid)
        assert first["id"] == second["id"]
        assert len(backtest_service.list_runs(db, portfolio_id=pid)) == 1

    def test_rebalance_change_creates_new_run(self, db) -> None:
        pid = _seed_two_funds(db)
        a = backtest_service.run_backtest(db, pid, rebalance="none")
        b = backtest_service.run_backtest(db, pid, rebalance="yearly")
        assert a["id"] != b["id"]
        assert len(backtest_service.list_runs(db, portfolio_id=pid)) == 2

    def test_force_recompute_creates_new_run(self, db) -> None:
        pid = _seed_two_funds(db)
        a = backtest_service.run_backtest(db, pid)
        b = backtest_service.run_backtest(db, pid, reuse=False)
        assert a["id"] != b["id"]

    def test_member_change_invalidates_reuse(self, db) -> None:
        """成员或权重变了 → input_hash 变 → 必须是新 Run(不能复用旧快照)。"""
        pid = _seed_two_funds(db)
        a = backtest_service.run_backtest(db, pid)
        portfolio_store.set_weights(db, pid, {"100001.OF": 60.0, "100002.OF": 40.0})
        b = backtest_service.run_backtest(db, pid)
        assert a["id"] != b["id"]
        # 旧 Run 仍保留旧快照, 仍可复现
        assert [m["target_weight"] for m in a["members"]] == [50.0, 50.0]

    def test_get_unknown_run_raises(self, db) -> None:
        with pytest.raises(backtest_service.BacktestServiceError, match="未知回测"):
            backtest_service.get_run(db, 999999)

    def test_list_runs_filters_by_portfolio(self, db) -> None:
        pid = _seed_two_funds(db)
        other = _portfolio(db, {}, name="空组合")
        backtest_service.run_backtest(db, pid)
        rows = backtest_service.list_runs(db)
        assert len(rows) == 1
        assert rows[0]["portfolio_id"] == pid
        assert backtest_service.list_runs(db, portfolio_id=other) == []

    def test_compare_runs_gives_overlap_window(self, db) -> None:
        pid = _seed_two_funds(db)
        a = backtest_service.run_backtest(db, pid, rebalance="none")
        b = backtest_service.run_backtest(db, pid, rebalance="quarterly")
        compared = backtest_service.compare_runs(db, [a["id"], b["id"]])
        assert len(compared["runs"]) == 2
        assert compared["overlap_start"] == D[0].isoformat()
        assert compared["overlap_end"] == D[2].isoformat()

    def test_compare_empty_ids_raises(self, db) -> None:
        with pytest.raises(backtest_service.BacktestServiceError, match="至少指定一个"):
            backtest_service.compare_runs(db, [])


# ---------------------------------------------------------------------------
# L1 三格缓存(规格 9.3 的一致性约束)
# ---------------------------------------------------------------------------

class TestCachedMetrics:
    def test_cached_equals_detail_windows(self, db) -> None:
        """⭐ 列表页三格必须与详情页收益条**数字完全一致**(规格 9.3 验收点)。"""
        pid = _seed_two_funds(db)
        cached = backtest_service.refresh_cached_metrics(db, pid)
        windows = backtest_service.compute(db, pid)["result"]["windows"]
        assert cached["ok"] is True
        assert cached["day_return"] == windows["d1"]["value"]
        assert cached["month_return"] == windows["m1"]["value"]
        assert cached["ytd_return"] == windows["ytd"]["value"]
        assert cached["asof_date"] == D[2].isoformat()

    def test_cached_is_persisted_on_portfolio(self, db) -> None:
        pid = _seed_two_funds(db)
        backtest_service.refresh_cached_metrics(db, pid)
        detail = portfolio_store.portfolio_detail(db, pid)
        assert detail["cached_day_return"] is not None
        assert detail["cached_asof_date"] == D[2].isoformat()

    def test_unbacktestable_portfolio_clears_cache(self, db) -> None:
        """不具备回测条件 → **清空**缓存, 不留过期数字(列表页显示 —)。"""
        pid = _portfolio(db, {}, name="空组合")
        portfolio_store.write_cached_metrics(
            db, pid, day_return=1.0, month_return=2.0, ytd_return=3.0, asof_date=D[0],
        )
        outcome = backtest_service.refresh_cached_metrics(db, pid)
        assert outcome["ok"] is False
        detail = portfolio_store.portfolio_detail(db, pid)
        assert detail["cached_day_return"] is None
        assert detail["cached_asof_date"] is None

    def test_batch_refresh_isolates_failure(self, db) -> None:
        good = _seed_two_funds(db)
        _portfolio(db, {}, name="空组合")
        outcome = backtest_service.refresh_all_cached_metrics(db)
        assert outcome["total"] == 2
        assert outcome["success_count"] == 1
        assert outcome["fail_count"] == 1
        assert outcome["status"] == "partial"
        assert portfolio_store.portfolio_detail(db, good)["cached_day_return"] is not None


# ---------------------------------------------------------------------------
# 端点契约
# ---------------------------------------------------------------------------

class TestBacktestEndpoints:
    @pytest.fixture()
    def seeded(self, thread_db):
        # ⚠ 端点走 contract_client, 它绑的是 thread_db(不是 db fixture) —— 数据必须建在同一库
        return _seed_two_funds(thread_db)

    def test_create_run_returns_201(self, contract_client, seeded) -> None:
        r = contract_client.post(f"{BASE}/backtests", json={"portfolio_id": seeded})
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "success"
        assert body["points"] == 3
        assert body["result"]["windows"]["d1"]["composite"] is True

    def test_create_run_bad_rebalance_returns_422(self, contract_client, seeded) -> None:
        r = contract_client.post(
            f"{BASE}/backtests", json={"portfolio_id": seeded, "rebalance": "monthly"},
        )
        assert r.status_code == 422

    def test_create_run_with_unfinished_weights_returns_422(self, contract_client, thread_db) -> None:
        _register(thread_db, "100001.OF", "测试基金A")
        _nav(thread_db, "100001.OF", {D[0]: 1.0, D[1]: 1.1})
        portfolio = Portfolio(name="缺权重", status="active")
        thread_db.add(portfolio)
        thread_db.commit()
        thread_db.add(PortfolioAsset(portfolio_id=portfolio.id, symbol="100001.OF"))
        thread_db.commit()
        r = contract_client.post(f"{BASE}/backtests", json={"portfolio_id": portfolio.id})
        assert r.status_code == 422
        assert "尚未设置比例" in r.json()["detail"]

    def test_list_runs_by_portfolio(self, contract_client, seeded) -> None:
        contract_client.post(f"{BASE}/backtests", json={"portfolio_id": seeded})
        rows = contract_client.get(f"{BASE}/backtests", params={"portfolio_id": seeded}).json()
        assert len(rows) == 1
        assert rows[0]["summary"]["selected_return"] == pytest.approx(10.5, abs=1e-6)
        assert "nav" not in rows[0]   # 列表不带曲线

    def test_get_run_detail_and_404(self, contract_client, seeded) -> None:
        run_id = contract_client.post(
            f"{BASE}/backtests", json={"portfolio_id": seeded},
        ).json()["id"]
        body = contract_client.get(f"{BASE}/backtests/{run_id}").json()
        assert body["result"]["assets"][0]["symbol"] == "100001.OF"
        assert len(body["nav"]["dates"]) == 3
        assert contract_client.get(f"{BASE}/backtests/999999").status_code == 404

    def test_compare_route_is_not_shadowed_by_run_id(self, contract_client, seeded) -> None:
        """`/backtests/compare` 必须注册在 `/backtests/{run_id}` 之前, 否则会被 int 解析拦下。"""
        a = contract_client.post(f"{BASE}/backtests", json={"portfolio_id": seeded}).json()["id"]
        b = contract_client.post(
            f"{BASE}/backtests", json={"portfolio_id": seeded, "rebalance": "yearly"},
        ).json()["id"]
        r = contract_client.get(f"{BASE}/backtests/compare", params={"ids": f"{a},{b}"})
        assert r.status_code == 200
        assert len(r.json()["runs"]) == 2

    def test_compare_bad_id_returns_422(self, contract_client) -> None:
        r = contract_client.get(f"{BASE}/backtests/compare", params={"ids": "abc"})
        assert r.status_code == 422

    def test_cached_metrics_endpoint(self, contract_client, seeded) -> None:
        r = contract_client.post(f"{BASE}/portfolios/{seeded}/cached-metrics")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # 落库后从详情接口能读到, 且与收益条一致
        detail = contract_client.get(f"{BASE}/portfolios/{seeded}").json()
        windows = contract_client.post(
            f"{BASE}/backtests", json={"portfolio_id": seeded},
        ).json()["result"]["windows"]
        assert detail["cached_day_return"] == windows["d1"]["value"]
