# -*- coding: utf-8 -*-
"""盘中选债 HTTP 合同测试(P2-R01: 服务端强制输入约束)。

约束: mock 抓取函数, 不访问真实 jisilu.cn;
非法输入必须 422 且不触发抓取(mock 调用次数为 0)。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture()
def client(db):
    """TestClient, 覆盖 get_db 为线程安全的内存库会话工厂。

    TestClient 在独立线程执行请求, 内存 SQLite 需关掉同线程检查;
    每次请求建新连接共享同一内存库(StaticPool)。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.models.database import Base, get_db

    from backend.models import app_setting, data_status, valuation  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    # 注意: override 必须是可调用函数而非 sessionmaker 类,
    # FastAPI 0.141 把类当依赖签名分析会生成 local_kw 必填参数导致 422
    app.dependency_overrides[get_db] = lambda: TestSession()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


INTRADAY = "/api/cb-list/screen/intraday"


def _screen_mock(records=None):
    """返回 (mock, 期望返回值), 替代 screen_bonds_intraday 避免真实抓取。"""
    result = {
        "total_all": 2, "total_filtered": 1, "rows": [{"code": "110001"}],
        "intraday": {"fetched_at": "10:00:00", "quote_time": "10:00:00",
                     "total_live": 2, "redeem_loaded": True},
    }
    return patch("backend.api.routes.cb_screen.screen_bonds_intraday",
                 return_value=result)


class TestIntradayValidation:
    """非法输入 → 422 且不触发抓取。"""

    def test_negative_price_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"price_min": -1})
        assert r.status_code == 422
        assert m.call_count == 0

    def test_negative_year_left_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"year_left_min": -2})
        assert r.status_code == 422
        assert m.call_count == 0

    def test_negative_curr_iss_amt_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"curr_iss_amt_max": -0.5})
        assert r.status_code == 422
        assert m.call_count == 0

    def test_inverted_price_range_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"price_min": 130, "price_max": 120})
        assert r.status_code == 422
        assert "转债价格" in r.json()["detail"]
        assert m.call_count == 0

    def test_inverted_year_range_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"year_left_min": 5, "year_left_max": 1})
        assert r.status_code == 422
        assert "剩余年限" in r.json()["detail"]
        assert m.call_count == 0

    def test_infinity_text_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"price_max": "inf"})
        assert r.status_code == 422
        assert m.call_count == 0

    def test_nan_text_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"price_max": "nan"})
        assert r.status_code == 422
        assert m.call_count == 0

    def test_non_numeric_text_rejected(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"price_max": "abc"})
        assert r.status_code == 422
        assert m.call_count == 0


class TestIntradayAccepted:
    """合法输入(含负溢价/负收益率) → 200 且抓取恰好一次。"""

    def test_valid_query_passes(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={
                "price_min": 100, "price_max": 120,
                "premium_rt_max": -3.5, "ytm_min": -1,
                "ratings": "AA,AA+,NONE",
            })
        assert r.status_code == 200
        assert m.call_count == 1
        # 评级规范化: 大写去重, NONE 保留
        sent = m.call_args.args[0] if m.call_args.args else m.call_args[0][0]
        assert sent["ratings"] == ["AA", "AA+", "NONE"]
        assert sent["price_max"] == 120
        assert sent["premium_rt_max"] == -3.5

    def test_negative_premium_and_ytm_allowed(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"premium_rt_max": -10, "ytm_min": -5})
        assert r.status_code == 200
        assert m.call_count == 1

    def test_zero_is_valid(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY, params={"price_max": 0, "year_left_min": 0})
        assert r.status_code == 200
        assert m.call_count == 1

    def test_no_filters_calls_once(self, client):
        with _screen_mock() as m:
            r = client.get(INTRADAY)
        assert r.status_code == 200
        assert m.call_count == 1

    def test_blacklist_exclusion_applied(self, client):
        # 直接用 client 覆盖后的同一个内存库写黑名单
        from backend.models.database import get_db
        from backend.services.cb_blacklist_store import add_to_blacklist

        db = app.dependency_overrides[get_db]()
        try:
            add_to_blacklist(db, "110001", bond_nm="测试债")
        finally:
            db.close()
        with _screen_mock():
            r = client.get(INTRADAY)
        assert r.status_code == 200
        assert r.json()["rows"] == []
        assert r.json()["blacklisted_count"] == 1
