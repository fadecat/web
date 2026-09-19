# -*- coding: utf-8 -*-
"""组合定义服务层测试(P2): CRUD / 成员 / 权重 / 数据就绪。全内存库, 不触网。"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.models.research import FundNavDaily, ResearchDailyBarAdjusted, ResearchSecurity
from backend.services import portfolio_store, research_store

_D = [date(2026, 1, 5), date(2026, 1, 6)]


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_securities(db, items):
    research_store.upsert_securities(db, [
        {"symbol": s, "name": n, "type": t, "source": "tencent", "selection_list": "组合实验室"}
        for s, t, n in items
    ])


def _seed_bars(db, symbol, closes=(10.0, 11.0)):
    for day, close in zip(_D, closes):
        db.add(ResearchDailyBarAdjusted(
            symbol=symbol, trade_date=day, adjust_mode="HFQ",
            open=close, high=close, low=close, close=close,
            volume=1.0, amount=1.0, volume_unit="股", source="test",
            snapshot_id=1, content_hash=f"{symbol}-{day}",
        ))
    db.commit()


def _seed_nav(db, symbol, navs=(2.0, 2.1)):
    for day, nav in zip(_D, navs):
        db.add(FundNavDaily(
            symbol=symbol, nav_date=day, unit_nav=nav, daily_return_pct=0.0,
            adj_nav=nav, source="test",
        ))
    db.commit()


class TestPortfolioCrud:
    def test_default_name_increments(self, db) -> None:
        a = portfolio_store.create_portfolio(db)
        b = portfolio_store.create_portfolio(db)
        assert a["name"] == "我的组合1"
        assert b["name"] == "我的组合2"

    def test_list_excludes_archived_by_default(self, db) -> None:
        keep = portfolio_store.create_portfolio(db, "保留")
        gone = portfolio_store.create_portfolio(db, "归档")
        portfolio_store.archive_portfolio(db, gone["id"])
        names = [p["name"] for p in portfolio_store.list_portfolios(db)]
        assert names == ["保留"]
        assert [p["name"] for p in portfolio_store.list_portfolios(db, include_archived=True)].__len__() == 2
        assert keep["id"] != gone["id"]

    def test_archive_then_restore(self, db) -> None:
        p = portfolio_store.create_portfolio(db)
        assert portfolio_store.archive_portfolio(db, p["id"])["status"] == "archived"
        assert portfolio_store.restore_portfolio(db, p["id"])["status"] == "active"

    def test_rename_rejects_blank(self, db) -> None:
        p = portfolio_store.create_portfolio(db)
        with pytest.raises(portfolio_store.PortfolioError, match="不得为空"):
            portfolio_store.rename_portfolio(db, p["id"], "   ")

    def test_unknown_portfolio_rejected(self, db) -> None:
        with pytest.raises(portfolio_store.PortfolioError, match="未知组合"):
            portfolio_store.rename_portfolio(db, 999999, "x")

    def test_default_rebalance_validated(self, db) -> None:
        p = portfolio_store.create_portfolio(db, default_rebalance="quarterly")
        assert p["default_rebalance"] == "quarterly"
        with pytest.raises(portfolio_store.PortfolioError, match="未知再平衡方式"):
            portfolio_store.create_portfolio(db, default_rebalance="monthly")

    def test_copy_carries_members_and_weights(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        src = portfolio_store.create_portfolio(db, "原组合")
        portfolio_store.add_asset(db, src["id"], "600900.SH")
        portfolio_store.set_weights(db, src["id"], {"600900.SH": 100.0})
        copied = portfolio_store.copy_portfolio(db, src["id"])
        assert copied["name"] == "复制_原组合"
        detail = portfolio_store.portfolio_detail(db, copied["id"])
        assert len(detail["assets"]) == 1
        assert detail["assets"][0]["target_weight"] == 100.0
        assert detail["ready"] is True  # 权重合计 100


class TestMembers:
    def test_add_unregistered_symbol_rejected(self, db) -> None:
        p = portfolio_store.create_portfolio(db)
        with pytest.raises(portfolio_store.PortfolioError, match="未注册"):
            portfolio_store.add_asset(db, p["id"], "600900.SH")

    def test_add_is_idempotent(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        p = portfolio_store.create_portfolio(db)
        first = portfolio_store.add_asset(db, p["id"], "600900.SH")
        second = portfolio_store.add_asset(db, p["id"], "600900.SH")
        assert first["created"] is True and second["created"] is False
        assert portfolio_store.list_assets(db, p["id"])["assets"].__len__() == 1

    def test_type_mismatch_rejected(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        p = portfolio_store.create_portfolio(db)
        with pytest.raises(portfolio_store.PortfolioError, match="类型与注册表不一致"):
            portfolio_store.add_asset(db, p["id"], "600900.SH", asset_type="ETF")

    def test_sort_order_follows_insertion(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力"), ("100018.OF", "fund", "富国天利")])
        p = portfolio_store.create_portfolio(db)
        portfolio_store.add_asset(db, p["id"], "600900.SH")
        portfolio_store.add_asset(db, p["id"], "100018.OF")
        orders = [a["sort_order"] for a in portfolio_store.list_assets(db, p["id"])["assets"]]
        assert orders == [0, 1]

    def test_remove_does_not_disable_security(self, db) -> None:
        """移除成员只删成员行, 不能停用标的(否则影响 /research 与定时同步)。"""
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        p = portfolio_store.create_portfolio(db)
        portfolio_store.add_asset(db, p["id"], "600900.SH")
        assert portfolio_store.remove_asset(db, p["id"], "600900.SH") is True
        security = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == "600900.SH"))
        assert security is not None and security.enabled is True
        assert portfolio_store.remove_asset(db, p["id"], "600900.SH") is False

    def test_add_at_appended_and_symbol_normalized(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        p = portfolio_store.create_portfolio(db)
        row = portfolio_store.add_asset(db, p["id"], "600900.SH")
        assert row["symbol"] == "600900.SH"
        assert row["added_at"] is not None  # 供「添加后的收益」用


class TestWeights:
    def _setup(self, db, weights=None):
        _seed_securities(db, [
            ("600900.SH", "stock", "长江电力"),
            ("100018.OF", "fund", "富国天利"),
        ])
        p = portfolio_store.create_portfolio(db)
        portfolio_store.add_asset(db, p["id"], "600900.SH")
        portfolio_store.add_asset(db, p["id"], "100018.OF")
        return p

    def test_partial_weights_not_ready(self, db) -> None:
        """编辑器里的正常中间态: 允许部分未设, 只是不能回测。"""
        p = self._setup(db)
        state = portfolio_store.set_weights(db, p["id"], {"600900.SH": 40.0})
        assert state["weight_sum"] == 40.0
        assert state["ready"] is False

    def test_full_weights_ready(self, db) -> None:
        p = self._setup(db)
        state = portfolio_store.set_weights(db, p["id"], {"600900.SH": 60.0, "100018.OF": 40.0})
        assert state["weight_sum"] == 100.0
        assert state["ready"] is True

    def test_sum_not_100_not_ready(self, db) -> None:
        p = self._setup(db)
        state = portfolio_store.set_weights(db, p["id"], {"600900.SH": 60.0, "100018.OF": 30.0})
        assert state["ready"] is False  # 无现金腿, 必须合计 100%

    def test_negative_weight_rejected(self, db) -> None:
        p = self._setup(db)
        with pytest.raises(portfolio_store.PortfolioError, match="不得为负"):
            portfolio_store.set_weights(db, p["id"], {"600900.SH": -1.0})

    def test_unknown_symbol_rejected(self, db) -> None:
        p = self._setup(db)
        with pytest.raises(portfolio_store.PortfolioError, match="不在该组合内"):
            portfolio_store.set_weights(db, p["id"], {"513100.SH": 100.0})

    def test_weight_vectors_skips_unset(self, db) -> None:
        p = self._setup(db)
        portfolio_store.set_weights(db, p["id"], {"600900.SH": 60.0})
        assert portfolio_store.weight_vectors(db, p["id"]) == {"600900.SH": 60.0}


class TestDataReadiness:
    def test_missing_data_reported_with_reason(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        p = portfolio_store.create_portfolio(db)
        portfolio_store.add_asset(db, p["id"], "600900.SH")
        readiness = portfolio_store.data_readiness(db, p["id"])
        assert readiness["all_ready"] is False
        assert readiness["common_start"] is None
        assert readiness["assets"][0]["reason"] == "无数据, 请先同步"

    def test_mixed_assets_common_start_is_max_of_firsts(self, db) -> None:
        """混合资产也能一起判定共同起点(T0 = 各首个可用日的最大值)。"""
        _seed_securities(db, [("600900.SH", "stock", "长江电力"), ("100018.OF", "fund", "富国天利")])
        _seed_bars(db, "600900.SH")
        _seed_nav(db, "100018.OF")
        p = portfolio_store.create_portfolio(db)
        portfolio_store.add_asset(db, p["id"], "600900.SH")
        portfolio_store.add_asset(db, p["id"], "100018.OF")
        readiness = portfolio_store.data_readiness(db, p["id"])
        assert readiness["all_ready"] is True
        assert readiness["common_start"] == "2026-01-05"
        bases = {a["price_basis"] for a in readiness["assets"]}
        assert bases == {"HFQ", "NAV_ADJ"}  # 口径逐列可见, 不混用

    def test_portfolio_detail_assembles_everything(self, db) -> None:
        _seed_securities(db, [("600900.SH", "stock", "长江电力")])
        _seed_bars(db, "600900.SH")
        p = portfolio_store.create_portfolio(db, "四资产等权")
        portfolio_store.add_asset(db, p["id"], "600900.SH")
        portfolio_store.set_weights(db, p["id"], {"600900.SH": 100.0})
        detail = portfolio_store.portfolio_detail(db, p["id"])
        assert detail["name"] == "四资产等权"
        assert detail["ready"] is True
        assert detail["data_readiness"]["all_ready"] is True
        assert detail["assets"][0]["name"] == "长江电力"

    def test_detail_of_unknown_returns_none(self, db) -> None:
        assert portfolio_store.portfolio_detail(db, 999999) is None

    def test_empty_portfolio_readiness_not_ready(self, db) -> None:
        p = portfolio_store.create_portfolio(db)
        assert portfolio_store.data_readiness(db, p["id"])["all_ready"] is False


def test_cached_metrics_writeback(db) -> None:
    """L1 三格缓存 + 「收益时间」= cached_asof_date。"""
    p = portfolio_store.create_portfolio(db)
    portfolio_store.write_cached_metrics(
        db, p["id"], day_return=1.08, month_return=-0.62, ytd_return=6.67,
        asof_date=date(2026, 9, 18),
    )
    row = next(x for x in portfolio_store.list_portfolios(db) if x["id"] == p["id"])
    assert row["cached_day_return"] == 1.08
    assert row["cached_month_return"] == -0.62
    assert row["cached_ytd_return"] == 6.67
    assert row["cached_asof_date"] == "2026-09-18"


def test_empty_portfolio_shows_none_metrics(db) -> None:
    """空组合三格显示 —(前端把 None 渲染为破折号), 不能是 0。"""
    p = portfolio_store.create_portfolio(db)
    assert p["cached_day_return"] is None
    assert p["cached_asof_date"] is None
    assert p["asset_count"] == 0
