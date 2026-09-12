# -*- coding: utf-8 -*-
"""股票高股息快照接口契约测试。

隔离原则(同 conftest.contract_client):
- 使用内存 SQLite + 线程安全引擎, 不触达生产库/调度器。
- contract_client 提供已隔离 lifespan 的 TestClient; thread_db 是与它
  共享同一内存库的独立会话, 供测试写入测试数据。
- 只验证契约: 最新日默认、股息率降序 null 沉底、null/负值/徽标串原样、
  raw_json/owned/holded 排除、键集合恰 48 键、enterprise_nature 标注、
  筛选预设 GET/POST 全量读写与一致性 422。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.models.jisilu_stock import StockDividendDaily

BASE = "/api/stock-dividend/latest"
PRESETS_BASE = "/api/stock-dividend/presets"

EXPECTED_KEYS = {
    "trade_date",
    "stock_id", "stock_nm", "enterprise_nature",
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
        """响应键集合恰好等于契约的 48 键(防未来字段悄悄进契约)。"""
        thread_db.add(StockDividendDaily(
            trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=4.0,
        ))
        thread_db.commit()

        data = contract_client.get(BASE).json()
        assert set(data[0].keys()) == EXPECTED_KEYS
        assert len(EXPECTED_KEYS) == 48

    def test_enterprise_nature_from_whitelist(self, thread_db, contract_client, monkeypatch):
        """enterprise_nature 由国资白名单映射注入; 未命中/缺名单为空串。"""
        from backend.services.queries import stock_dividend as svc

        monkeypatch.setattr(
            svc, "enterprise_nature_map",
            lambda: {"600001": "中央国有企业", "000002": "地方国有企业"},
        )
        thread_db.add_all([
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="600001", dividend_rate=4.0),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="000002", dividend_rate=3.0),
            StockDividendDaily(trade_date=date(2026, 9, 10), stock_id="600999", dividend_rate=2.0),
        ])
        thread_db.commit()

        by_id = {d["stock_id"]: d for d in contract_client.get(BASE).json()}
        assert by_id["600001"]["enterprise_nature"] == "中央国有企业"
        assert by_id["000002"]["enterprise_nature"] == "地方国有企业"
        assert by_id["600999"]["enterprise_nature"] == ""

    def test_order_by_sql_portable_no_nulls_last(self):
        """排序 SQL 不得使用 NULLS LAST: 需 SQLite≥3.30, ECS 系统库 3.26
        曾直接 500(near "NULLS" 语法错)。null 沉底必须走 IS NULL 布尔升序。"""
        from sqlalchemy.dialects import sqlite

        from backend.services.queries import stock_dividend as svc

        sql = str(svc._latest_stmt("2026-09-11").compile(dialect=sqlite.dialect()))
        assert "NULLS" not in sql.upper()
        assert "IS NULL" in sql.upper()


def _preset(id_, name, form):
    return {"id": id_, "name": name, "form": form}


@pytest.fixture()
def presets_file(test_artifact_dir, monkeypatch):
    """把预设落盘路径指到测试产物目录(禁止写真实 data/)。"""
    from backend.services import dividend_presets

    path = test_artifact_dir / "stock_dividend_presets.json"
    monkeypatch.setattr(dividend_presets, "PRESETS_PATH", path)
    return path


class TestDividendPresetsContract:
    def test_get_missing_file_returns_builtin_default(self, contract_client, presets_file):
        """从未保存过: GET 返回内置默认(邮件漏斗口径, 默认勾选国资)。"""
        r = contract_client.get(PRESETS_BASE)
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["version"] == 1
        assert cfg["active_id"] == "default"
        assert len(cfg["presets"]) == 1
        p = cfg["presets"][0]
        assert p["id"] == "default"
        assert p["name"] == "邮件口径"
        assert p["form"]["peMax"] == 15
        assert p["form"]["dividendMin"] == 3
        assert p["form"]["peTMax"] == 40
        assert p["form"]["pbTMax"] == 40
        assert p["form"]["roeAverageMin"] == 5
        assert p["form"]["totalValueMin"] == 200
        assert p["form"]["soeOnly"] is True

    def test_post_full_replace_roundtrip(self, contract_client, presets_file):
        """POST 全量保存 → GET 读回一致(原子落盘到指定路径)。

        表单会被规范化为全键集(未给的字段补默认), 所以读回比较的是
        POST 响应而非原始输入。
        """
        body = {
            "version": 1,
            "active_id": "p2",
            "presets": [
                _preset("p1", "宽口径", {"peMax": 20, "soeOnly": False}),
                _preset("p2", "邮件口径", {"peMax": 15, "dividendMin": 3, "soeOnly": True}),
            ],
        }
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["version"] == 1
        assert cfg["active_id"] == "p2"
        assert [p["id"] for p in cfg["presets"]] == ["p1", "p2"]
        assert [p["name"] for p in cfg["presets"]] == ["宽口径", "邮件口径"]

        form2 = cfg["presets"][1]["form"]
        assert form2["peMax"] == 15
        assert form2["dividendMin"] == 3
        assert form2["soeOnly"] is True
        assert form2["peTMax"] is None  # 未给的字段规范化为默认
        assert form2["markets"] == []
        assert form2["industry"] == ""
        assert form2["excludeIndustry"] == ""

        assert presets_file.exists()  # 落盘在测试产物目录
        assert contract_client.get(PRESETS_BASE).json() == cfg

    def test_post_duplicate_id_422(self, contract_client, presets_file):
        body = {
            "version": 1, "active_id": "a",
            "presets": [_preset("a", "一", {}), _preset("a", "二", {})],
        }
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 422
        assert "id 重复" in r.json()["detail"]

    def test_post_duplicate_name_422(self, contract_client, presets_file):
        body = {
            "version": 1, "active_id": "a",
            "presets": [_preset("a", "同名", {}), _preset("b", "同名", {})],
        }
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 422
        assert "名称重复" in r.json()["detail"]

    def test_post_active_id_not_in_presets_422(self, contract_client, presets_file):
        body = {"version": 1, "active_id": "ghost", "presets": [_preset("a", "一", {})]}
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 422
        assert "active_id" in r.json()["detail"]

    def test_post_form_extra_key_422(self, contract_client, presets_file):
        """表单键集与前端 emptyForm 严格一致: 多键直接 422(pydantic extra=forbid)。"""
        body = {
            "version": 1, "active_id": "a",
            "presets": [_preset("a", "一", {"peMax": 15, "hack": True})],
        }
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 422

    def test_post_form_invalid_market_422(self, contract_client, presets_file):
        body = {
            "version": 1, "active_id": "a",
            "presets": [_preset("a", "一", {"markets": ["bj"]})],
        }
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 422

    def test_post_empty_presets_422(self, contract_client, presets_file):
        """至少保留一个预设(min_length=1)。"""
        body = {"version": 1, "active_id": "x", "presets": []}
        r = contract_client.post(PRESETS_BASE, json=body)
        assert r.status_code == 422

    def test_load_failsoft_corrupt_file(self, presets_file):
        """落盘文件损坏 → GET 回退内置默认, 不抛异常不写盘。"""
        from backend.services import dividend_presets

        presets_file.write_text("{ not json", encoding="utf-8")
        cfg = dividend_presets.load_presets()
        assert cfg["active_id"] == "default"
        assert cfg["presets"][0]["name"] == "邮件口径"
        assert presets_file.read_text(encoding="utf-8") == "{ not json"  # 未被覆盖

    def test_load_failsoft_wrong_shape(self, presets_file):
        """落盘文件形状不符(缺 active_id) → 回退内置默认。"""
        from backend.services import dividend_presets

        presets_file.write_text('{"version": 1, "presets": []}', encoding="utf-8")
        cfg = dividend_presets.load_presets()
        assert cfg["active_id"] == "default"
