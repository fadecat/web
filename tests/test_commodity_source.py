from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.services.commodity_source import CommoditySourceAdapter, CommoditySourceError, normalize_price_rows


def test_source_normalizes_bilingual_columns_sorts_and_keeps_last_duplicate():
    raw = pd.DataFrame({"日期": ["2026-01-02", "2026-01-01", "2026-01-02"], "开盘": [2, 1, 3], "最高": [3, 2, 4], "最低": [1, 0.5, 2], "收盘": [2.5, 1.5, 3.5], "成交量": [10, 9, 11]})
    rows = normalize_price_rows(raw, source="test")
    assert [row.trade_date for row in rows] == [date(2026, 1, 1), date(2026, 1, 2)]
    assert rows[-1].close == 3.5 and rows[-1].volume == 11.0


def test_source_rejects_empty_nonfinite_nonpositive_and_future_rows():
    with pytest.raises(CommoditySourceError):
        normalize_price_rows(pd.DataFrame(), source="test")
    for close in [0, -1, float("nan"), float("inf")]:
        with pytest.raises(CommoditySourceError):
            normalize_price_rows(pd.DataFrame({"date": ["2026-01-01"], "close": [close]}), source="test")
    with pytest.raises(CommoditySourceError, match="future"):
        normalize_price_rows(pd.DataFrame({"date": ["2999-01-01"], "close": [1]}), source="test")


def test_source_rejects_beijing_tomorrow_as_future():
    tomorrow = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    with pytest.raises(CommoditySourceError, match="future"):
        normalize_price_rows(pd.DataFrame({"date": [tomorrow.isoformat()], "close": [1]}), source="test")


def test_source_selects_akshare_adapter_by_market_without_importing_on_construction():
    calls = []

    def fake_fetch(code, market):
        calls.append((code, market))
        return pd.DataFrame({"date": ["2026-01-01"], "close": [1]})

    rows = CommoditySourceAdapter(fetch_fn=fake_fetch).fetch("RB0", "domestic")
    assert calls == [("RB0", "domestic")]
    assert rows[0].source == "akshare:futures_zh_daily_sina"
