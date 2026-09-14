# -*- coding: utf-8 -*-
"""可转债等权指数日频接口契约测试(T2: R3 系列)。

隔离原则(同 conftest.contract_client):
- 使用内存 SQLite + 线程安全引擎, 不触达生产库/调度器。
- contract_client 提供已隔离 lifespan 的 TestClient; thread_db 是与它
  共享同一内存库的独立会话, 供测试写入测试数据。
- 不修改 backend/api/routes/cb_index.py 的解析逻辑, 只验证契约:
  降序、负收益率原值、null 不变、空表返回 [];
  利差端点: 交集现算、样本不足返回 null。
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta

from backend.models.valuation import CbIndexDaily, CnBondYield

BASE = "/api/cb-index/daily"


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


class TestCbIndexDailyContract:
    def test_out_of_order_insert_returns_descending(self, thread_db, contract_client):
        """乱序插入 09-09 / 09-07 / 09-08, 接口必须按日期降序。"""
        thread_db.add_all([
            CbIndexDaily(trade_date=date(2026, 9, 9), median_price=132.5, avg_ytm=-8.25, count=400.0),
            CbIndexDaily(trade_date=date(2026, 9, 7), median_price=130.0, avg_ytm=2.1, count=398.0),
            CbIndexDaily(trade_date=date(2026, 9, 8), median_price=None, avg_ytm=0.0, count=399.0),
        ])
        thread_db.commit()

        r = contract_client.get(BASE)
        assert r.status_code == 200
        data = r.json()
        assert [d["trade_date"] for d in data] == ["2026-09-09", "2026-09-08", "2026-09-07"]

    def test_negative_ytm_returned_as_is(self, thread_db, contract_client):
        """负收益率(-8.25)原样返回, 不缩放、不丢失。"""
        thread_db.add(CbIndexDaily(trade_date=date(2026, 9, 9), median_price=132.5, avg_ytm=-8.25, count=400.0))
        thread_db.commit()

        r = contract_client.get(BASE)
        assert r.status_code == 200
        data = r.json()
        assert data[0]["avg_ytm"] == -8.25
        # 允许 0(不为 null)
        thread_db.add(CbIndexDaily(trade_date=date(2026, 9, 8), median_price=131.0, avg_ytm=0.0, count=399.0))
        thread_db.commit()
        r2 = contract_client.get(BASE)
        assert r2.json()[1]["avg_ytm"] == 0.0

    def test_null_field_preserved(self, thread_db, contract_client):
        """null 字段(median_price 等)原样为 null, 不补 0。"""
        thread_db.add(CbIndexDaily(trade_date=date(2026, 9, 9), median_price=None, avg_ytm=-1.0, count=400.0))
        thread_db.commit()

        r = contract_client.get(BASE)
        assert r.status_code == 200
        data = r.json()
        assert data[0]["median_price"] is None
        assert data[0]["avg_ytm"] == -1.0

    def test_count_returned_as_stored(self, thread_db, contract_client):
        """count 按落库值原样返回(后端存储为 Float, 前端再归一化)。"""
        thread_db.add(CbIndexDaily(trade_date=date(2026, 9, 9), median_price=132.5, avg_ytm=-8.25, count=400.0))
        thread_db.commit()

        r = contract_client.get(BASE)
        assert r.json()[0]["count"] == 400.0

    def test_empty_table_returns_empty_list(self, contract_client):
        """空表返回 [], 不返回 null 或缺失字段。"""
        r = contract_client.get(BASE)
        assert r.status_code == 200
        assert r.json() == []


SPREAD_URL = "/api/cb-index/spread"


class TestCbIndexSpreadContract:
    def _seed(self, thread_db, n=20):
        """写入 n 天转债指数 + 10Y 国债交集数据(avg_ytm=-3.5, 10Y=2.0)。"""
        start = date(2026, 8, 1)
        for i in range(n):
            d = start + timedelta(days=i)
            thread_db.add(CbIndexDaily(trade_date=d, avg_ytm=-3.5))
            thread_db.add(CnBondYield(trade_date=d, yield_10y=2.0))
        thread_db.commit()

    def test_spread_summary_and_series(self, thread_db, contract_client):
        """20 天交集: 返回统计块与全历史序列, spread = avg_ytm - 10Y。"""
        self._seed(thread_db)
        r = contract_client.get(SPREAD_URL)
        assert r.status_code == 200
        data = r.json()
        assert data["trade_date"] == "2026-08-20"
        assert data["avg_ytm"] == -3.5
        assert data["bond_yield"] == 2.0
        assert data["spread"]["current"] == -5.5
        assert isinstance(data["spread"]["percentiles"], dict)
        assert data["spread"]["average_5y"] == -5.5
        assert len(data["series"]) == 20
        assert data["series"][0] == {
            "trade_date": "2026-08-01",
            "avg_ytm": -3.5,
            "bond_yield": 2.0,
            "spread": -5.5,
        }

    def test_spread_uses_date_intersection(self, thread_db, contract_client):
        """单侧缺失的日期不参与: 转债 20 天 + 国债 19 天(缺首日) → 交集 19 天 < 20 → null。"""
        start = date(2026, 8, 1)
        for i in range(20):
            thread_db.add(CbIndexDaily(trade_date=start + timedelta(days=i), avg_ytm=-1.0))
        for i in range(1, 20):  # 国债缺 08-01
            thread_db.add(CnBondYield(trade_date=start + timedelta(days=i), yield_10y=2.0))
        thread_db.commit()
        r = contract_client.get(SPREAD_URL)
        assert r.status_code == 200
        assert r.json() is None

    def test_spread_null_on_empty_tables(self, contract_client):
        """两表为空 → 样本不足返回 null。"""
        r = contract_client.get(SPREAD_URL)
        assert r.status_code == 200
        assert r.json() is None
