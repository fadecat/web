# -*- coding: utf-8 -*-
"""查询服务层单元测试。

验证: get_latest 取每只指数最新行、get_history 全量、返回字段结构、
canonical_id 翻译、路由切换到查询层后返回与旧结构一致。
"""
from __future__ import annotations

from datetime import date

from backend.models.valuation import IndexDividendYield, IndexValuationSnapshot
from backend.services.queries import dividends, valuations


def test_valuations_get_latest_and_history(db):
    db.add_all([
        IndexValuationSnapshot(index_code="930955", index_name="红利低波100", trade_date=date(2026, 9, 3), pe=9.0),
        IndexValuationSnapshot(index_code="930955", index_name="红利低波100", trade_date=date(2026, 9, 4), pe=9.1),
        IndexValuationSnapshot(index_code="000300", index_name="沪深300", trade_date=date(2026, 9, 4), pe=12.0),
    ])
    db.commit()

    # latest: 每只指数取最新一行
    latest = valuations.get_latest(db)
    assert len(latest) == 2
    by_code = {r["index_code"]: r for r in latest}
    assert by_code["930955"]["pe"] == 9.1
    assert by_code["930955"]["trade_date"] == "2026-09-04"

    # history: 全量 3 行
    hist = valuations.get_history(db)
    assert len(hist) == 3
    # 字段结构完整(PE/PB/PS + 分位)
    assert set(hist[0].keys()) == {
        "index_code", "index_name", "trade_date", "pe", "pb", "ps",
        "pe_percentile", "pb_percentile", "ps_percentile",
    }


def test_valuations_canonical_id_translation(db):
    """查询层内部做 canonical_id → storage key 翻译。"""
    db.add(IndexValuationSnapshot(index_code="512040", index_name="中证价值100", trade_date=date(2026, 9, 4), pe=10.0))
    db.commit()

    by_canonical = valuations.get_history(db, "931052")
    assert len(by_canonical) == 1
    assert by_canonical[0]["index_code"] == "512040"


def test_dividends_get_latest_and_history(db):
    db.add_all([
        IndexDividendYield(index_code="930955", trade_date=date(2026, 9, 3), dividend_yield=4.0),
        IndexDividendYield(index_code="930955", trade_date=date(2026, 9, 4), dividend_yield=4.2),
        IndexDividendYield(index_code="000300", trade_date=date(2026, 9, 4), dividend_yield=2.5),
    ])
    db.commit()

    latest = dividends.get_latest(db)
    assert len(latest) == 2
    by_code = {r["index_code"]: r for r in latest}
    assert by_code["930955"]["dividend_yield"] == 4.2

    hist = dividends.get_history(db)
    assert len(hist) == 3
    assert set(hist[0].keys()) == {
        "index_code", "trade_date", "dividend_yield", "percentile", "average_5y",
    }


def test_dividends_canonical_id_translation(db):
    db.add(IndexDividendYield(index_code="512040", trade_date=date(2026, 9, 4), dividend_yield=3.5))
    db.commit()

    rows = dividends.get_history(db, "931052")
    assert len(rows) == 1
    assert rows[0]["index_code"] == "512040"


# ---------------------------------------------------------------------------
# 价格查询(轮动读取抽取)
# ---------------------------------------------------------------------------

def test_prices_get_history(db):
    from backend.models.valuation import IndexDailyQuote
    from backend.services.queries.prices import get_history

    db.add_all([
        IndexDailyQuote(index_code="399376", trade_date=date(2026, 9, 3), close=100.0),
        IndexDailyQuote(index_code="399376", trade_date=date(2026, 9, 4), close=101.0),
    ])
    db.commit()

    rows = get_history(db, "399376")
    assert len(rows) == 2
    assert (rows[0][0], rows[0][1]) == (date(2026, 9, 3), 100.0)  # 升序
    assert rows[1][1] == 101.0

    # 日期范围过滤(轮动预热窗口)
    filtered = get_history(db, "399376", start_date=date(2026, 9, 4))
    assert len(filtered) == 1
    assert filtered[0][1] == 101.0


# ---------------------------------------------------------------------------
# 实时选债 gateway
# ---------------------------------------------------------------------------

def test_fetch_live_snapshot_redeem_failure_degraded(monkeypatch):
    """强赎拉取失败降级为空, 不阻塞主数据。"""
    from backend.services.queries import live

    def ok_list():
        return [{"bond_id": "1", "price": "100"}]

    def fail_redeem():
        raise ConnectionError("timeout")

    monkeypatch.setattr(live, "fetch_cb_list", ok_list)
    monkeypatch.setattr(live, "fetch_redeem_list", fail_redeem)

    records, redeem_cells = live.fetch_live_snapshot()
    assert records == [{"bond_id": "1", "price": "100"}]
    assert redeem_cells == []


def test_fetch_live_snapshot_list_failure_raises(monkeypatch):
    """转债列表(主数据)失败直接抛。"""
    import pytest

    from backend.services.queries import live

    def fail_list():
        raise RuntimeError("boom")

    monkeypatch.setattr(live, "fetch_cb_list", fail_list)

    with pytest.raises(RuntimeError):
        live.fetch_live_snapshot()


# ---------------------------------------------------------------------------
# 国债收益率查询
# ---------------------------------------------------------------------------

def test_bond_yields_get_history(db):
    from backend.models.valuation import CnBondYield
    from backend.services.queries import bond_yields

    db.add_all([
        CnBondYield(trade_date=date(2026, 9, 3), yield_10y=2.5),
        CnBondYield(trade_date=date(2026, 9, 4), yield_10y=2.6),
    ])
    db.commit()

    rows = bond_yields.get_history(db)
    assert len(rows) == 2
    assert rows[0]["trade_date"] == "2026-09-04"  # 降序
    assert rows[0]["yield_10y"] == 2.6


# ---------------------------------------------------------------------------
# 转债快照查询
# ---------------------------------------------------------------------------

def test_snapshots_get_latest_and_history(db):
    from backend.models.valuation import CbDailySnapshot
    from backend.services.queries import snapshots

    db.add_all([
        CbDailySnapshot(trade_date=date(2026, 9, 3), bond_id="113648", bond_nm="巨星转债", price=120.0, dblow=110.0),
        CbDailySnapshot(trade_date=date(2026, 9, 4), bond_id="113648", bond_nm="巨星转债", price=121.0, dblow=111.0),
        CbDailySnapshot(trade_date=date(2026, 9, 4), bond_id="127000", bond_nm="测试转债", price=100.0, dblow=105.0),
    ])
    db.commit()

    # latest: 最新交易日(09-04)全量 2 条
    latest = snapshots.get_latest(db)
    assert len(latest) == 2
    assert {r["bond_id"] for r in latest} == {"113648", "127000"}

    # history: 单只债 2 条, 升序
    hist = snapshots.get_history(db, "113648")
    assert len(hist) == 2
    assert hist[0]["price"] == 120.0
    assert hist[1]["price"] == 121.0
