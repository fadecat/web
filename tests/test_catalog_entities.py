# -*- coding: utf-8 -*-
"""实体身份映射单元测试。

验证: 错关联实体(512040↔931052、159263↔980081)正确映射、
恒等实体不变、腾讯标的在册、双向翻译、未知 code 恒等、aliases 清单。
"""
from __future__ import annotations

from datetime import date

from backend.services.catalog_entities import (
    aliases,
    build_entity_catalog,
    is_aliased,
    to_canonical,
    to_storage,
)


def test_aliased_entities_mapped_correctly():
    """两个 ETF-code 标的应映射到真实指数代码。"""
    catalog = build_entity_catalog()
    # 中证价值100: config 512040(ETF) -> 真实指数 931052
    assert to_storage("931052") == "512040"
    assert to_canonical("512040") == "931052"
    # 国证价值100: config 159263(ETF) -> 真实指数 980081
    assert to_storage("980081") == "159263"
    assert to_canonical("159263") == "980081"
    # 两个实体都在目录里
    assert "931052" in catalog
    assert "980081" in catalog


def test_identity_entities_unchanged():
    """真实指数代码=config code 的标的, 恒等映射。"""
    for code in ["930955", "000300", "399303", "399326", "931233", "930709", "399296"]:
        assert to_storage(code) == code
        assert to_canonical(code) == code
        assert not is_aliased(code)


def test_tencent_symbols_present():
    """腾讯日线标的在册, provider=tencent。"""
    catalog = build_entity_catalog()
    assert catalog["399376"].providers == frozenset({"tencent"})
    assert catalog["399373"].providers == frozenset({"tencent"})
    assert catalog["399376"].name == "国证小盘成长"
    assert catalog["399373"].name == "国证大盘价值"


def test_is_aliased():
    assert is_aliased("931052") is True
    assert is_aliased("980081") is True
    assert is_aliased("930955") is False


def test_unknown_code_identity():
    """未收录的 code 双向翻译恒等, 不抛错。"""
    assert to_storage("999999") == "999999"
    assert to_canonical("999999") == "999999"
    assert not is_aliased("999999")


def test_aliases_listing():
    """aliases() 只列出有差异的映射, 且正好两个。"""
    result = aliases()
    assert result == {"931052": "512040", "980081": "159263"}


# ---------------------------------------------------------------------------
# 消费点: 查询接口支持 canonical_id(阶段2 收尾)
# ---------------------------------------------------------------------------

def test_snapshot_accepts_canonical_id(db):
    """传 canonical_id(931052) 与传 legacy(512040) 均能命中同一批数据。"""
    from backend.models.valuation import IndexValuationSnapshot
    from backend.api.routes.valuation import list_valuation_snapshot

    db.add(IndexValuationSnapshot(
        index_code="512040", index_name="中证价值100",
        trade_date=date(2026, 9, 4), pe=10.0,
    ))
    db.commit()

    by_canonical = list_valuation_snapshot(index_code="931052", latest=False, db=db)
    by_legacy = list_valuation_snapshot(index_code="512040", latest=False, db=db)

    assert len(by_canonical) == 1
    assert len(by_legacy) == 1
    assert by_canonical[0]["index_code"] == "512040"
    assert by_canonical[0]["pe"] == by_legacy[0]["pe"] == 10.0


def test_dividend_yield_accepts_canonical_id(db):
    """股息率接口同样支持 canonical_id 翻译。"""
    from backend.models.valuation import IndexDividendYield
    from backend.api.routes.valuation import list_dividend_yield

    db.add(IndexDividendYield(
        index_code="512040", trade_date=date(2026, 9, 4), dividend_yield=3.5,
    ))
    db.commit()

    rows = list_dividend_yield(index_code="931052", latest=False, db=db)
    assert len(rows) == 1
    assert rows[0]["index_code"] == "512040"
    assert rows[0]["dividend_yield"] == 3.5
