# -*- coding: utf-8 -*-
"""股票高股息快照接口契约测试。

隔离原则(同 conftest.contract_client):
- 使用内存 SQLite + 线程安全引擎, 不触达生产库/调度器。
- contract_client 提供已隔离 lifespan 的 TestClient; thread_db 是与它
  共享同一内存库的独立会话, 供测试写入测试数据。
- 只验证契约: 最新日默认、股息率降序 null 沉底、null/负值/徽标串原样、
  raw_json/owned/holded 排除、键集合恰 47 键。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.models.jisilu_stock import StockDividendDaily

BASE = "/api/stock-dividend/latest"

EXPECTED_KEYS = {
    "trade_date",
    "stock_id", "stock_nm",
    "sw_cd", "industry", "industry2", "industry_nm", "industry_nm2", "province",
    "price", "pre_close", "increase_rt", "volume", "adj_rt", "price_5year",
    "total_value", "float_value", "shares",
    "pe", "pb", "roe", "roe_average", "pe_temperature", "pb_temperature",
    "dividend_rate", "dividend_rate2", "dividend_rate5", "dividend_rate_average",
    "dividend_rate_base", "accu_dividend", "aft_dividend",
    "debt_rate", "int_debt_rate", "pledge_rt", "eps_growth", "eps_growth_ttm",
    "revenue_average", "profit_average", "cashflow_average",
    "ipo_date", "last_dt", "last_time", "audit_info", "active_flg",
    "margin_flg", "pb_flag", "stdevry",
}


@pytest.fixture()
def thread_db(contract_client, thread_safe_engine):
    """与 contract_client 同一内存库的独立会话(供测试写数据)。"""
    from sqlalchemy.orm import sessionmaker

    from backend.models.database import Base

    Base.metadata.create_all(bind=thread_safe_engine)
    TestSession = sessionmaker(bind=thread_safe_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


class TestStockDividendContract:
    def test_empty_table_returns_empty_list(self, contract_client):
        """空表返回 [], 不返回 null。"""
        r = contract_client.get(BASE)
        assert r.status_code == 200
        assert r.json() == []

    def test_default_returns_latest_trade_date_only(self, thread_db, contract_client):
        """默认只返回最新交易日; 每行 trade_date 为 isoformat 字符串。"""
        thread_db.add_all([
            StockDividendDaily(trade_date=date(2026, 9, 9), stock_id="600001", dividend_rate=3.0),
            StockDividendDaily(trade_date=date(2026, 9, 9), stock_id="000002", dividend_rate=4.0),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="600003", dividend_rate=5.0),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="000004", dividend_rate=6.0),
        ])
        thread_db.commit()

        r = contract_client.get(BASE)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert {d["stock_id"] for d in data} == {"600003", "000004"}
        assert all(d["trade_date"] == "2026-09-10" for d in data)

    def test_explicit_trade_date_param(self, thread_db, contract_client):
        """?trade_date= 显式指定历史日。"""
        thread_db.add_all([
            StockDividendDaily(trade_date=date(2026, 9, 9), stock_id="600001", dividend_rate=3.0),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="600003", dividend_rate=5.0),
        ])
        thread_db.commit()

        r = contract_client.get(BASE, params={"trade_date": "2026-09-09"})
        assert r.status_code == 200
        data = r.json()
        assert [d["stock_id"] for d in data] == ["600001"]

    def test_sorted_by_dividend_rate_desc_nulls_last(self, thread_db, contract_client):
        """默认按股息率降序, null 沉底。"""
        thread_db.add_all([
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="S3", dividend_rate=3.2),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="S4", dividend_rate=None),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="S1", dividend_rate=9.1),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="S2", dividend_rate=8.5),
        ])
        thread_db.commit()

        r = contract_client.get(BASE)
        assert [d["stock_id"] for d in r.json()] == ["S1", "S2", "S3", "S4"]

    def test_tie_break_by_stock_id_asc(self, thread_db, contract_client):
        """同股息率按 stock_id 升序, 保证顺序稳定。"""
        thread_db.add_all([
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=5.0),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="000001", dividend_rate=5.0),
        ])
        thread_db.commit()

        r = contract_client.get(BASE)
        assert [d["stock_id"] for d in r.json()] == ["000001", "600001"]

    def test_null_fields_preserved(self, thread_db, contract_client):
        """null 字段原样为 null, 不补 0/空串。"""
        thread_db.add(StockDividendDaily(
            trade_date=date(2026, 9, 10), stock_id="600001",
            dividend_rate=4.0, pe=None, pe_temperature=None, audit_info=None,
        ))
        thread_db.commit()

        data = contract_client.get(BASE).json()
        assert data[0]["pe"] is None
        assert data[0]["pe_temperature"] is None
        assert data[0]["audit_info"] is None

    def test_negative_values_as_is(self, thread_db, contract_client):
        """负值(亏损 PE / 负增长 / 跌幅)原样返回。"""
        thread_db.add(StockDividendDaily(
            trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=4.0,
            pe=-151.6, roe_average=-3.1, increase_rt=-2.5,
        ))
        thread_db.commit()

        data = contract_client.get(BASE).json()
        assert data[0]["pe"] == -151.6
        assert data[0]["roe_average"] == -3.1
        assert data[0]["increase_rt"] == -2.5

    def test_raw_json_owned_holded_excluded(self, thread_db, contract_client):
        """raw_json(体积)与 owned/holded(抓取账号私有自选态)不进响应。"""
        thread_db.add(StockDividendDaily(
            trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=4.0,
            raw_json='{"x":1}', owned=1, holded=0,
        ))
        thread_db.commit()

        data = contract_client.get(BASE).json()
        for key in ("raw_json", "owned", "holded"):
            assert key not in data[0]

    def test_string_badge_columns_as_is(self, thread_db, contract_client):
        """徽标串(stdevry='buy')与标志位原样字符串返回。"""
        thread_db.add(StockDividendDaily(
            trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=4.0,
            stdevry="buy", pb_flag="Y", margin_flg="R", audit_info="非标",
        ))
        thread_db.commit()

        data = contract_client.get(BASE).json()
        assert data[0]["stdevry"] == "buy"
        assert data[0]["pb_flag"] == "Y"
        assert data[0]["margin_flg"] == "R"
        assert data[0]["audit_info"] == "非标"

    def test_response_key_set(self, thread_db, contract_client):
        """响应键集合恰好等于契约的 47 键(防未来字段悄悄进契约)。"""
        thread_db.add(StockDividendDaily(
            trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=4.0,
        ))
        thread_db.commit()

        data = contract_client.get(BASE).json()
        assert set(data[0].keys()) == EXPECTED_KEYS
        assert len(EXPECTED_KEYS) == 47
