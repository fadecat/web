# -*- coding: utf-8 -*-
"""market_data 适配器测试: fake fetch 隔离, 不触真实 akshare/requests。"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import backend.services.market_data as market_data
from backend.services.market_data import (
    CAP_ADJUSTED_DAILY_BAR_HFQ,
    CAP_CORPORATE_EVENT_CALENDAR,
    CAP_RAW_DAILY_BAR,
    CAP_TRADE_CALENDAR,
    AdjustMode,
    AkShareEastmoneyProvider,
    CorporateEvent,
    ResearchSourceError,
    TencentFqklineProvider,
    _sina_parse_events,
    _tencent_extract_rows,
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


# ---------------------------------------------------------------------------
# 腾讯 fqkline + 新浪 hfq.js 适配器(2026-09-18 换源, fake fetch 隔离)
# ---------------------------------------------------------------------------


def _tencent_row(d: date, close: float = 10.0, volume: float = 5.0) -> list:
    """腾讯行序: [date, open, close, high, low, volume(手)]——注意 close 在第 2 列。"""
    return [d.isoformat(), close - 0.2, close, close + 0.3, close - 0.3, volume]


def test_tencent_extract_rows_key_discipline():
    """raw 走 'day' 键, hfq 走 'hfqday' 键; 复权键缺失直接报错(不静默退回 raw)。"""
    payload = {"data": {"sh600900": {"day": [_tencent_row(date(2026, 1, 5))],
                                     "hfqday": [_tencent_row(date(2026, 1, 5))]}}}
    assert len(_tencent_extract_rows(payload, "sh600900", "")) == 1
    assert len(_tencent_extract_rows(payload, "sh600900", "hfq")) == 1
    raw_only = {"data": {"sh600900": {"day": [_tencent_row(date(2026, 1, 5))]}}}
    with pytest.raises(ResearchSourceError, match="hfqday"):
        _tencent_extract_rows(raw_only, "sh600900", "hfq")


def test_tencent_provider_paginates_with_backward_cursor():
    """页上限: 每页返回窗口内最新 count 条(腾讯语义), 游标前移到该页最早日期前一天。"""
    all_dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(30)]
    calls: list[tuple[str, str, str, str, int]] = []

    def fake_kline(code, fq, start_s, end_s, count):
        calls.append((code, fq, start_s, end_s, count))
        end = date.fromisoformat(end_s)
        window = [d for d in all_dates if date.fromisoformat(start_s) <= d <= end]
        # 返回窗口内最新 count 条(升序)——腾讯 fqkline 语义
        return [_tencent_row(d) for d in window[-count:]]

    provider = TencentFqklineProvider(
        kline_fetch_fn=fake_kline, sleep=lambda _s: None, page_size=10, page_gap=0.0,
    )
    bars = provider.get_daily_bars(
        "600900.SH", all_dates[0], all_dates[-1], AdjustMode.RAW, security_type="STOCK",
    )
    assert len(calls) == 3  # 30 条 ÷ 10/页 = 3 页
    assert calls[0][3] == all_dates[-1].isoformat()       # 首页从 end 起
    assert calls[1][3] == all_dates[19].isoformat()       # 游标 = 上一页最早日期的前一天
    assert calls[2][3] == all_dates[9].isoformat()
    assert [bar.trade_date for bar in bars] == all_dates  # 升序全量
    assert bars[0].source == "tencent:fqkline"


def test_tencent_extract_rows_empty_window_is_empty_not_error():
    """无交易日窗口: 腾讯对 hfq 请求也回 'day' 空列表——空响应无口径歧义, 返回空。"""
    empty_day = {"data": {"sh600900": {"day": [], "qt": {}, "version": ""}}}
    assert _tencent_extract_rows(empty_day, "sh600900", "hfq") == []
    no_rows_keys = {"data": {"sh600900": {"qt": {}, "version": ""}}}
    assert _tencent_extract_rows(no_rows_keys, "sh600900", "hfq") == []


def test_tencent_provider_stops_on_partial_page_without_tail_request():
    """末页行数 < page_size 即取尽, 不再发尾窗请求(实测 2021-01-01 起全量翻页踩过)。"""
    all_dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(25)]
    calls: list[tuple[str, str]] = []

    def fake_kline(code, fq, start_s, end_s, count):
        calls.append((start_s, end_s))
        end = date.fromisoformat(end_s)
        window = [d for d in all_dates if date.fromisoformat(start_s) <= d <= end]
        return [_tencent_row(d) for d in window[-count:]]

    provider = TencentFqklineProvider(
        kline_fetch_fn=fake_kline, sleep=lambda _s: None, page_size=10, page_gap=0.0,
    )
    bars = provider.get_daily_bars(
        "600900.SH", all_dates[0], all_dates[-1], AdjustMode.HFQ, security_type="STOCK",
    )
    assert len(calls) == 3  # 10 + 10 + 5(末页不足 10 即止), 无第 4 次尾窗请求
    assert calls[2] == (all_dates[0].isoformat(), all_dates[4].isoformat())
    assert [bar.trade_date for bar in bars] == all_dates


def test_tencent_provider_row_order_and_volume_units():
    """行序 [date, open, close, high, low, volume(手)]: 股票与 ETF 成交量均 ×100。"""
    row = _tencent_row(date(2026, 1, 5), close=10.0, volume=5.0)  # 5 手
    provider = TencentFqklineProvider(
        kline_fetch_fn=lambda *args: [row], sleep=lambda _s: None,
    )
    stock_bar = provider.get_daily_bars(
        "600900.SH", date(2026, 1, 1), date(2026, 1, 31), AdjustMode.RAW, security_type="STOCK",
    )[0]
    assert (stock_bar.open, stock_bar.close, stock_bar.high, stock_bar.low) == (9.8, 10.0, 10.3, 9.7)
    assert stock_bar.volume == 500.0  # 5 手 × 100 → 500 股
    assert stock_bar.volume_unit == "share"
    etf_bar = provider.get_daily_bars(
        "511010.SH", date(2026, 1, 1), date(2026, 1, 31), AdjustMode.RAW, security_type="ETF",
    )[0]
    assert etf_bar.volume == 500.0  # 腾讯 ETF 也是手 → ×100 份


def test_tencent_provider_validates_and_filters():
    """区间外行被过滤; OHLC 非法/未来日期沿用统一校验拒绝。"""
    rows = [
        _tencent_row(date(2025, 12, 31)),  # 早于 start → 过滤
        _tencent_row(date(2026, 1, 5)),
    ]
    provider = TencentFqklineProvider(
        kline_fetch_fn=lambda *args: rows, sleep=lambda _s: None,
    )
    bars = provider.get_daily_bars(
        "600900.SH", date(2026, 1, 1), date(2026, 1, 31), AdjustMode.RAW, security_type="STOCK",
    )
    assert [bar.trade_date for bar in bars] == [date(2026, 1, 5)]

    bad = [["2026-01-05", 10.2, 10.0, 10.3, 10.4, 5.0]]  # low > min(open, close)
    bad_provider = TencentFqklineProvider(
        kline_fetch_fn=lambda *args: bad, sleep=lambda _s: None,
    )
    with pytest.raises(ResearchSourceError, match="OHLC"):
        bad_provider.get_daily_bars(
            "600900.SH", date(2026, 1, 1), date(2026, 1, 31), AdjustMode.RAW, security_type="STOCK",
        )


def test_tencent_provider_symbol_and_type_guards():
    """后缀/类型矛盾与无后缀代码拒绝(腾讯代码必须带交易所前缀)。"""
    provider = TencentFqklineProvider(
        kline_fetch_fn=lambda *args: [_tencent_row(date(2026, 1, 5))], sleep=lambda _s: None,
    )
    with pytest.raises(ResearchSourceError, match="矛盾"):
        provider.get_daily_bars(
            "600900.SH", date(2026, 1, 1), date(2026, 1, 31),
            AdjustMode.RAW, security_type="ETF",
        )
    with pytest.raises(ResearchSourceError, match="invalid symbol"):
        provider.get_daily_bars(
            "600900", date(2026, 1, 1), date(2026, 1, 31),
            AdjustMode.RAW, security_type="STOCK",
        )


def test_sina_parse_events_skips_sentinel_and_tolerates_key_variants():
    """1900-01-01 哨兵行跳过; 股票行只有 {d,f}, ETF 行含 {d,f,s,u}。"""
    rows = [
        {"d": "1900-01-01", "f": "1"},
        {"d": "2026-07-17", "f": "1.7166614532470703"},                      # 股票行
        {"d": "2026-09-18", "f": "1", "s": "1.0000", "u": "5.3905"},         # ETF 行
    ]
    events = _sina_parse_events(rows)
    assert [(e.event_date, e.cumulative_dividend) for e in events] == [
        (date(2026, 7, 17), None),
        (date(2026, 9, 18), 5.3905),
    ]
    assert events[0].factor == pytest.approx(1.7166614532470703)
    with pytest.raises(ResearchSourceError, match="invalid corporate event"):
        _sina_parse_events([{"d": "not-a-date", "f": "1"}])


def test_tencent_provider_get_corporate_events_wiring():
    """事件抓取走 _tencent_code 并解析为 CorporateEvent 列表。"""
    seen: list[str] = []

    def fake_events(code: str) -> list:
        seen.append(code)
        return [{"d": "2025-07-18", "f": "1.5"}]

    provider = TencentFqklineProvider(
        events_fetch_fn=fake_events, sleep=lambda _s: None,
    )
    events = provider.get_corporate_events("600900.SH")
    assert seen == ["sh600900"]
    assert events == [CorporateEvent(event_date=date(2025, 7, 18), factor=1.5)]


def test_tencent_provider_factory_and_capabilities():
    provider = provider_factory("tencent")
    assert provider.name == "tencent-fqkline"
    assert CAP_CORPORATE_EVENT_CALENDAR in provider.capabilities
    assert CAP_RAW_DAILY_BAR in provider.capabilities
    assert CAP_ADJUSTED_DAILY_BAR_HFQ in provider.capabilities
    assert CAP_TRADE_CALENDAR in provider.capabilities
    # 日历复用新浪 tool_trade_date_hist_sina 的规范化路径
    calendar_provider = TencentFqklineProvider(
        calendar_fetch_fn=lambda: pd.DataFrame({"trade_date": ["2026-01-02", "2026-01-01"]}),
        sleep=lambda _s: None,
    )
    sessions = calendar_provider.get_trade_calendar()
    assert [s.trade_date for s in sessions] == [date(2026, 1, 1), date(2026, 1, 2)]


def test_tencent_construction_does_not_import_requests():
    """默认 fetch 内部延迟 import requests; 构造器不得触发网络库加载。"""
    import subprocess
    import sys

    code = (
        "import sys;"
        "from backend.services.market_data import TencentFqklineProvider;"
        "p = TencentFqklineProvider(sleep=lambda s: None);"
        "assert 'requests' not in sys.modules, 'constructor imported requests';"
        "print('OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_source_module_has_no_stray_requests_import():
    source = (
        __import__("pathlib").Path(market_data.__file__).read_text(encoding="utf-8")
    )
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import requests"):
            assert stripped.startswith("import requests  # noqa"), stripped  # 函数内延迟 import
