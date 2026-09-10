# -*- coding: utf-8 -*-
"""可转债等权指数日频接口契约测试(T2: R3 系列)。

隔离原则(同 conftest.contract_client):
- 使用内存 SQLite + 线程安全引擎, 不触达生产库/调度器。
- contract_client 提供已隔离 lifespan 的 TestClient; thread_db 是与它
  共享同一内存库的独立会话, 供测试写入测试数据。
- 不修改 backend/api/routes/cb_index.py 的解析逻辑, 只验证契约:
  降序、负收益率原值、null 不变、空表返回 []。
"""
from __future__ import annotations

import pytest
from datetime import date

from backend.models.valuation import CbIndexDaily

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
