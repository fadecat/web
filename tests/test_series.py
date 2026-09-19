# -*- coding: utf-8 -*-
"""统一序列层测试(P2): 两类资产分支取数 / 口径标注 / 并集对齐 / 共同起点 T0。

全内存库, 不触网。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.models.research import FundNavDaily, ResearchDailyBarAdjusted
from backend.services import research_store, series

_D = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


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


def _seed_security(db, items):
    research_store.upsert_securities(db, [
        {"symbol": s, "name": n, "type": t, "source": "tencent", "selection_list": "组合实验室"}
        for s, t, n in items
    ])


def _add_bars(db, symbol, dates, closes):
    for day, close in zip(dates, closes):
        db.add(ResearchDailyBarAdjusted(
            symbol=symbol, trade_date=day, adjust_mode="HFQ",
            open=close, high=close, low=close, close=close,
            volume=100.0, amount=1000.0, volume_unit="股", source="test",
            snapshot_id=1, content_hash=f"{symbol}-{day}",
        ))
    db.commit()


def _add_nav(db, symbol, dates, navs):
    for day, nav in zip(dates, navs):
        db.add(FundNavDaily(
            symbol=symbol, nav_date=day, unit_nav=nav, daily_return_pct=0.0,
            adj_nav=nav, source="test",
        ))
    db.commit()


class TestGetSeries:
    def test_fund_reads_fund_nav_daily_with_nav_adj(self, db) -> None:
        _seed_security(db, [("100018.OF", "fund", "富国天利")])
        _add_nav(db, "100018.OF", _D, [1.0, 1.1, 1.2])
        contract = series.get_series(db, "100018.OF")
        assert contract.asset_class == series.FUND
        assert contract.price_basis == series.NAV_ADJ
        assert contract.dates == tuple(_D)
        assert contract.prices == (1.0, 1.1, 1.2)
        assert contract.settle_lag == 0  # D2 裁决: 当日净值成交

    def test_stock_reads_bar_table_with_hfq(self, db) -> None:
        _seed_security(db, [("600900.SH", "stock", "长江电力")])
        _add_bars(db, "600900.SH", _D, [10.0, 10.5, 11.0])
        contract = series.get_series(db, "600900.SH")
        assert contract.asset_class == series.STOCK
        assert contract.price_basis == series.HFQ
        assert contract.prices == (10.0, 10.5, 11.0)

    def test_etf_uses_same_bar_table(self, db) -> None:
        _seed_security(db, [("513100.SH", "etf", "纳指ETF国泰")])
        _add_bars(db, "513100.SH", _D, [1.0, 1.0, 1.0])
        contract = series.get_series(db, "513100.SH")
        assert contract.asset_class == series.ETF
        assert contract.price_basis == series.HFQ

    def test_range_is_closed_interval(self, db) -> None:
        _seed_security(db, [("600900.SH", "stock", "长江电力")])
        _add_bars(db, "600900.SH", _D, [10.0, 10.5, 11.0])
        contract = series.get_series(db, "600900.SH", _D[1], _D[1])
        assert contract.dates == (_D[1],)

    def test_unregistered_symbol_returns_empty_not_error(self, db) -> None:
        """组合页要能区分"没数据"与"出错" → 空序列而非抛异常。"""
        contract = series.get_series(db, "600900.SH")
        assert contract.row_count == 0
        assert contract.first_date is None
        assert contract.price_basis == series.HFQ  # 口径仍可判定(后缀兜底)

    def test_fund_suffix_fallback_without_registry(self, db) -> None:
        contract = series.get_series(db, "100018.OF")
        assert contract.asset_class == series.FUND
        assert contract.price_basis == series.NAV_ADJ

    def test_unresolvable_symbol_raises(self, db) -> None:
        with pytest.raises(series.SeriesError):
            series.get_series(db, "600900")  # 无后缀且未注册

    def test_to_dict_reports_basis_and_range(self, db) -> None:
        _seed_security(db, [("100018.OF", "fund", "富国天利")])
        _add_nav(db, "100018.OF", _D, [1.0, 1.1, 1.2])
        d = series.get_series(db, "100018.OF").to_dict()
        assert d["price_basis"] == "NAV_ADJ"
        assert d["row_count"] == 3
        assert d["first_date"] == "2026-01-05" and d["last_date"] == "2026-01-07"


class TestAlignUnion:
    def test_union_dates_and_forward_fill(self, db) -> None:
        """QDII 净值日期滞后: 并集 + 前值填充(用交集会丢掉这些交易日)。"""
        a = series.SeriesContract(
            symbol="A.SH", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
            prices=(10.0, 11.0, 12.0),
        )
        b = series.SeriesContract(
            symbol="B.SH", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2026, 1, 5), date(2026, 1, 7)),  # 缺 01-06(境外假期)
            prices=(1.0, 2.0),
        )
        dates, aligned = series.align_union([a, b])
        assert dates == (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
        assert aligned["A.SH"] == (10.0, 11.0, 12.0)
        assert aligned["B.SH"] == (1.0, 1.0, 2.0)  # 01-06 前值填充

    def test_no_fill_before_first_available(self) -> None:
        """首个可用日之前必须是 None, 不能填充未来值。"""
        a = series.SeriesContract(
            symbol="A.SH", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2026, 1, 5), date(2026, 1, 6)), prices=(10.0, 11.0),
        )
        late = series.SeriesContract(
            symbol="LATE.SH", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2026, 1, 6),), prices=(5.0,),
        )
        dates, aligned = series.align_union([a, late])
        assert aligned["LATE.SH"] == (None, 5.0)

    def test_fill_forward_false_keeps_gaps(self) -> None:
        """并集的"空位"由别的序列提供; fill_forward=False 时留 None 不补。"""
        short = series.SeriesContract(
            symbol="SHORT.SH", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2026, 1, 5), date(2026, 1, 7)), prices=(1.0, 2.0),
        )
        full = series.SeriesContract(
            symbol="FULL.SH", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)), prices=(9.0, 9.0, 9.0),
        )
        dates, aligned = series.align_union([short, full], fill_forward=False)
        assert dates == (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
        assert aligned["SHORT.SH"] == (1.0, None, 2.0)  # 01-06 留空
        assert aligned["FULL.SH"] == (9.0, 9.0, 9.0)

    def test_empty_input(self) -> None:
        assert series.align_union([]) == ((), {})


class TestCommonStart:
    def test_t0_is_max_of_first_available_dates(self) -> None:
        """T0 = 各序列首个可用日的**最大值**(基准组合 2013-04-26 即此口径)。"""
        a = series.SeriesContract(
            symbol="A", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2003, 12, 2), date(2016, 9, 14)), prices=(1.0, 2.0),
        )
        b = series.SeriesContract(
            symbol="B", asset_class=series.ETF, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2013, 4, 26), date(2016, 9, 14)), prices=(1.0, 2.0),
        )
        assert series.common_start([a, b]) == date(2013, 4, 26)

    def test_empty_series_ignored(self) -> None:
        a = series.SeriesContract(
            symbol="A", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
            dates=(date(2016, 9, 14),), prices=(1.0,),
        )
        empty = series.SeriesContract(
            symbol="E", asset_class=series.FUND, price_basis=series.NAV_ADJ, settle_lag=0,
            dates=(), prices=(),
        )
        assert series.common_start([a, empty]) == date(2016, 9, 14)

    def test_all_empty_returns_none(self) -> None:
        assert series.common_start([]) is None


class TestSeriesContractGuard:
    """两个平行元组最容易出隐蔽错位, 契约必须 fail-fast。"""

    def test_single_float_prices_rejected(self) -> None:
        with pytest.raises(series.SeriesError, match="必须是 tuple"):
            series.SeriesContract(
                symbol="A", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
                dates=(date(2026, 1, 5),), prices=(1.0),  # noqa: PT011 少逗号 = float
            )

    def test_length_mismatch_rejected(self) -> None:
        with pytest.raises(series.SeriesError, match="长度不一致"):
            series.SeriesContract(
                symbol="A", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
                dates=(date(2026, 1, 5), date(2026, 1, 6)), prices=(1.0,),
            )

    def test_unsorted_dates_rejected(self) -> None:
        with pytest.raises(series.SeriesError, match="严格升序"):
            series.SeriesContract(
                symbol="A", asset_class=series.STOCK, price_basis=series.HFQ, settle_lag=0,
                dates=(date(2026, 1, 6), date(2026, 1, 5)), prices=(1.0, 2.0),
            )

    def test_empty_contract_is_valid(self) -> None:
        c = series.SeriesContract(
            symbol="A", asset_class=series.FUND, price_basis=series.NAV_ADJ, settle_lag=0,
            dates=(), prices=(),
        )
        assert c.row_count == 0 and c.first_date is None


def test_mixed_assets_align_on_union(db) -> None:
    """股票/ETF(HFQ) 与场外基金(NAV_ADJ) 能同时对齐 —— 这是"统一序列层"的核心用处。"""
    _seed_security(db, [("600900.SH", "stock", "长江电力"), ("100018.OF", "fund", "富国天利")])
    _add_bars(db, "600900.SH", _D, [10.0, 11.0, 12.0])
    _add_nav(db, "100018.OF", _D, [2.0, 2.1, 2.2])
    stock = series.get_series(db, "600900.SH")
    fund = series.get_series(db, "100018.OF")
    dates, aligned = series.align_union([stock, fund])
    assert len(dates) == 3
    assert aligned["600900.SH"] == (10.0, 11.0, 12.0)
    assert aligned["100018.OF"] == (2.0, 2.1, 2.2)
    assert series.common_start([stock, fund]) == date(2026, 1, 5)
