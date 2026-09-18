# -*- coding: utf-8 -*-
"""market_data 适配器测试: fake fetch 隔离, 不触真实 akshare。"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import backend.services.market_data as market_data
from backend.services.market_data import (
    CAP_ADJUSTED_DAILY_BAR_HFQ,
    CAP_RAW_DAILY_BAR,
    CAP_TRADE_CALENDAR,
    AdjustMode,
    AkShareEastmoneyProvider,
    ResearchSourceError,
    normalize_research_bars,
    provider_factory,
)


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _stock_frame(dates: list[str], closes: list[float] | None = None) -> pd.DataFrame:
    closes = closes or [10.0] * len(dates)
    return pd.DataFrame({
        "日期": dates,
        "开盘": [c - 0.2 for c in closes],
        "最高": [c + 0.3 for c in closes],
        "最低": [c - 0.3 for c in closes],
        "收盘": closes,
        "成交量": [100] * len(dates),
        "成交额": [1000.0] * len(dates),
    })


def test_normalize_accepts_chinese_and_english_columns_sorted_dedup():
    for columns in (("date", "open", "high", "low", "close"), ("日期", "开盘", "最高", "最低", "收盘")):
        frame = pd.DataFrame({
            columns[0]: ["2026-01-02", "2026-01-01", "2026-01-02"],
            columns[1]: [9.8, 9.8, 9.9],
            columns[2]: [10.3, 10.3, 10.4],
            columns[3]: [9.7, 9.7, 9.8],
            columns[4]: [10.0, 10.0, 10.1],
        })
        rows = normalize_research_bars(frame, source="test", security_type="STOCK")
        assert [row.trade_date for row in rows] == [date(2026, 1, 1), date(2026, 1, 2)]
        assert rows[-1].close == 10.1  # 重复日期保留最后一次


def test_normalize_rejects_empty_invalid_ohlc_and_future_dates():
    with pytest.raises(ResearchSourceError):
        normalize_research_bars(pd.DataFrame(), source="test", security_type="STOCK")
    with pytest.raises(ResearchSourceError, match="OHLC"):
        normalize_research_bars(
            pd.DataFrame({"date": ["2026-01-01"], "open": [10], "high": [10.5], "low": [10.2], "close": [10.1]}),
            source="test", security_type="STOCK",
        )  # low > min(open, close)
    with pytest.raises(ResearchSourceError, match="future"):
        normalize_research_bars(
            _stock_frame(["2999-01-01"]), source="test", security_type="STOCK",
        )
    tomorrow = date.today() + timedelta(days=1)
    with pytest.raises(ResearchSourceError, match="future"):
        normalize_research_bars(
            _stock_frame([tomorrow.isoformat()]), source="test", security_type="STOCK",
        )


def test_normalize_converts_stock_volume_from_lots_keeps_etf_shares():
    frame = _stock_frame(["2026-01-01"])
    rows = normalize_research_bars(frame, source="test", security_type="STOCK")
    assert rows[0].volume == 100 * 100.0  # 手 → 股
    assert rows[0].volume_unit == "share"
    etf_rows = normalize_research_bars(frame, source="test", security_type="ETF")
    assert etf_rows[0].volume == 100.0  # 份 原值


def test_adapter_strips_suffix_and_routes_by_security_type():
    calls: list[dict] = []

    def fake_stock(symbol, period, start_date, end_date, adjust):
        calls.append({"fn": "stock", "symbol": symbol, "adjust": adjust})
        return _stock_frame(["2026-01-01"])

    def fake_etf(symbol, period, start_date, end_date, adjust):
        calls.append({"fn": "etf", "symbol": symbol, "adjust": adjust})
        return _stock_frame(["2026-01-01"])

    provider = AkShareEastmoneyProvider(
        stock_fetch_fn=fake_stock, etf_fetch_fn=fake_etf, sleep=lambda _s: None,
    )
    bars = provider.get_daily_bars(
        "600900.SH", date(2026, 1, 1), date(2026, 1, 31),
        AdjustMode.RAW, security_type="STOCK",
    )
    assert calls[0] == {"fn": "stock", "symbol": "600900", "adjust": ""}
    assert bars[0].source == "akshare:stock_zh_a_hist"

    provider.get_daily_bars(
        "510300.SH", date(2026, 1, 1), date(2026, 1, 31),
        AdjustMode.HFQ, security_type="ETF",
    )
    assert calls[1] == {"fn": "etf", "symbol": "510300", "adjust": "hfq"}


def test_adapter_rejects_symbol_type_contradiction_and_invalid_symbol():
    provider = AkShareEastmoneyProvider(
        stock_fetch_fn=lambda **kw: _stock_frame(["2026-01-01"]),
        etf_fetch_fn=lambda **kw: _stock_frame(["2026-01-01"]),
        sleep=lambda _s: None,
    )
    with pytest.raises(ResearchSourceError, match="矛盾"):
        provider.get_daily_bars(
            "600900.SH", date(2026, 1, 1), date(2026, 1, 31),
            AdjustMode.RAW, security_type="ETF",
        )
    with pytest.raises(ResearchSourceError, match="invalid symbol"):
        provider.get_daily_bars(
            "60090.SH", date(2026, 1, 1), date(2026, 1, 31),
            AdjustMode.RAW, security_type="STOCK",
        )
    with pytest.raises(ResearchSourceError, match="unsupported security_type"):
        provider.get_daily_bars(
            "600900.SH", date(2026, 1, 1), date(2026, 1, 31),
            AdjustMode.RAW, security_type="CONVERT",
        )


def test_adapter_capabilities_and_calendar():
    def fake_calendar():
        return pd.DataFrame({"trade_date": ["2026-01-02", "2026-01-01"]})

    provider = AkShareEastmoneyProvider(calendar_fetch_fn=fake_calendar, sleep=lambda _s: None)
    assert provider.capabilities == frozenset({
        CAP_RAW_DAILY_BAR, CAP_ADJUSTED_DAILY_BAR_HFQ, CAP_TRADE_CALENDAR,
    })
    sessions = provider.get_trade_calendar()
    assert [s.trade_date for s in sessions] == [date(2026, 1, 1), date(2026, 1, 2)]
    assert all(s.is_open for s in sessions)


def test_provider_factory_unknown_source_errors():
    with pytest.raises(ResearchSourceError, match="不静默"):
        provider_factory("not-a-source")
    assert provider_factory("akshare").name == "akshare-eastmoney"


def test_construction_does_not_import_akshare():
    """模块源码与默认实现只在 fetch 内部 import akshare(策略层零依赖的结构保证)。"""
    import subprocess
    import sys

    code = (
        "import sys;"
        "from backend.services.market_data import AkShareEastmoneyProvider;"
        "p = AkShareEastmoneyProvider(sleep=lambda s: None);"
        "assert 'akshare' not in sys.modules, 'constructor imported akshare';"
        "print('OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_source_module_has_no_stray_akshare_import():
    source = (
        __import__("pathlib").Path(market_data.__file__).read_text(encoding="utf-8")
    )
    # akshare 只允许出现在函数体内的延迟 import(顶层 import 禁止)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import akshare"):
            assert stripped.startswith("import akshare as ak"), stripped  # 函数内惯用法
