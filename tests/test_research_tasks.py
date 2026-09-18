# -*- coding: utf-8 -*-
"""research_tasks 测试: fake provider 注入, 不触真实 akshare。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from backend.tasks import research_tasks
from backend.services.market_data import (
    CAP_CORPORATE_EVENT_CALENDAR,
    CorporateEvent,
    DailyBar,
    TradeSession,
)


class FakeProvider:
    """可编程 fake: 按 (symbol, adjust) 返回预设数据或抛异常。"""

    name = "fake"

    def __init__(self, *, bars_by_symbol: dict[str, list[DailyBar]] | None = None,
                 fail_symbols: set[str] | None = None,
                 calendar_dates: list[date] | None = None,
                 calendar_error: bool = False,
                 events_by_symbol: dict[str, list[CorporateEvent]] | None = None,
                 events_error_symbols: set[str] | None = None) -> None:
        self.bars_by_symbol = bars_by_symbol or {}
        self.fail_symbols = fail_symbols or set()
        self.calendar_dates = calendar_dates or []
        self.calendar_error = calendar_error
        self.events_by_symbol = events_by_symbol or {}
        self.events_error_symbols = events_error_symbols or set()
        self.bar_calls: list[tuple[str, str]] = []
        self.event_calls: list[str] = []
        # 声明事件能力与否由 events_by_symbol 是否提供驱动
        self.capabilities = (
            frozenset({CAP_CORPORATE_EVENT_CALENDAR}) if events_by_symbol is not None else frozenset()
        )

    def get_trade_calendar(self) -> list[TradeSession]:
        if self.calendar_error:
            raise RuntimeError("calendar down")
        return [TradeSession(trade_date=d, is_open=True) for d in self.calendar_dates]

    def get_daily_bars(self, symbol, start, end, adjust_mode, *, security_type="STOCK"):
        if symbol in self.fail_symbols:
            raise RuntimeError(f"network error for {symbol}")
        self.bar_calls.append((symbol, adjust_mode.name))
        return self.bars_by_symbol.get(symbol, [])

    def get_corporate_events(self, symbol):
        if symbol in self.events_error_symbols:
            raise RuntimeError("events down")
        self.event_calls.append(symbol)
        return self.events_by_symbol.get(symbol, [])


def _bars(dates: list[date]) -> list[DailyBar]:
    return [
        DailyBar(trade_date=d, open=9.8, high=10.3, low=9.7, close=10.0,
                 volume=100.0, amount=1000.0, source="fake")
        for d in dates
    ]


_DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


def _run(tmp_path, provider, *, targets=None, session_factory=None):
    if targets is None:
        targets = [
            {"symbol": "600900.SH", "name": "长江电力", "type": "stock", "selection_list": "高股息"},
            {"symbol": "510300.SH", "name": "沪深300ETF", "type": "etf"},
        ]
    config = tmp_path / "research.yaml"
    lines = ["history_start: \"2026-01-01\"", "corporate_action_tolerance: 0.002", "targets:"]
    for t in targets:
        lines.append(
            f"  - {{symbol: \"{t['symbol']}\", name: \"{t['name']}\", type: \"{t['type']}\"}}"
        )
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    import shutil
    import tempfile
    from pathlib import Path

    cleanup = session_factory is None
    if session_factory is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models.database import Base
        from backend.models import (  # noqa: F401
            app_setting, data_status, jisilu_account, jisilu_stock, research, valuation,
        )

        artifact_root = Path(".test-artifacts") / f"research-tasks-{next(tempfile._get_candidate_names())}"
        artifact_root.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{(artifact_root / 'task.db').as_posix()}")
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine)

    def db_factory():
        return session_factory()

    try:
        return research_tasks.run_research_daily_sync(
            provider_factory_fn=lambda: provider,
            config_path=str(config),
            db_factory=db_factory,
        )
    finally:
        if cleanup:
            engine.dispose()
            shutil.rmtree(artifact_root, ignore_errors=True)


def test_sync_success_contract(tmp_path):
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_dates=_DATES,
    )
    result = _run(tmp_path, provider)
    assert result["status"] == "success"
    assert result["success_count"] == 2
    assert result["fail_count"] == 0
    assert result["inserted_rows"] == 12  # 2 标的 × (raw 3 + hfq 3)
    assert result["revised_rows"] == 0
    # 每标的 raw+hfq 各一次
    assert sorted(provider.bar_calls) == [
        ("510300.SH", "HFQ"), ("510300.SH", "RAW"),
        ("600900.SH", "HFQ"), ("600900.SH", "RAW"),
    ]


def test_sync_partial_failure_does_not_break_others(tmp_path):
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        fail_symbols={"510300.SH"},
        calendar_dates=_DATES,
    )
    result = _run(tmp_path, provider)
    assert result["status"] == "partial"
    assert result["success_count"] == 1
    assert result["fail_count"] == 1
    assert result["inserted_rows"] == 6
    assert any("510300.SH" in err for err in result["errors"])


def test_sync_all_failed_contract(tmp_path):
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        fail_symbols={"600900.SH", "510300.SH"},
        calendar_dates=_DATES,
    )
    result = _run(tmp_path, provider)
    assert result["status"] == "failed"
    assert result["success_count"] == 0
    assert result["fail_count"] == 2


def test_sync_calendar_failure_is_nonblocking(tmp_path):
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_error=True,
    )
    result = _run(tmp_path, provider)
    assert result["status"] == "success"  # 日历失败不阻塞 bars 同步
    assert result["success_count"] == 2


def test_sync_empty_targets_skipped(tmp_path):
    provider = FakeProvider(calendar_dates=_DATES)
    with pytest.raises(ValueError, match="无有效标的"):
        _run(tmp_path, provider, targets=[])


def test_sync_rerun_is_unchanged(tmp_path):
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_dates=_DATES,
    )
    # 两次跑共享同一库(第二次应全 unchanged)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.models.database import Base
    from backend.models import (  # noqa: F401
        app_setting, data_status, jisilu_account, jisilu_stock, research, valuation,
    )

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    first = _run(tmp_path, provider, session_factory=session_factory)
    assert first["inserted_rows"] == 12
    second = _run(tmp_path, provider, session_factory=session_factory)
    assert second["inserted_rows"] == 0
    assert second["revised_rows"] == 0
    assert second["status"] == "success"
    engine.dispose()


# ---------------------------------------------------------------------------
# 权益事件日历同步与数据源路由
# ---------------------------------------------------------------------------


def test_sync_fetches_corporate_events_when_capable(tmp_path):
    """声明事件能力的 Provider: 逐标的抓事件并落库(可再读回)。"""
    from backend.services import research_store
    from sqlalchemy import select as sa_select
    from backend.models.research import ResearchCorporateEvent

    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_dates=_DATES,
        events_by_symbol={
            "600900.SH": [CorporateEvent(event_date=date(2026, 7, 17), factor=1.5)],
            "510300.SH": [],
        },
    )
    # 共享库以便断言落库(默认 _run 用一次性库)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.models.database import Base
    from backend.models import (  # noqa: F401
        app_setting, data_status, jisilu_account, jisilu_stock, research, valuation,
    )

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    try:
        result = _run(tmp_path, provider, session_factory=session_factory)
        assert result["status"] == "success"
        assert sorted(provider.event_calls) == ["510300.SH", "600900.SH"]
        with session_factory() as db:
            dates = research_store.load_corporate_event_dates(db, "600900.SH")
            assert dates == {date(2026, 7, 17)}
            rows = db.scalars(sa_select(ResearchCorporateEvent)).all()
            assert len(rows) == 1 and rows[0].source == "fake"
    finally:
        engine.dispose()


def test_sync_events_failure_does_not_fail_bars(tmp_path):
    """事件抓取失败只告警, bars 同步结果不受影响。"""
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_dates=_DATES,
        events_by_symbol={"600900.SH": [], "510300.SH": []},
        events_error_symbols={"510300.SH"},
    )
    result = _run(tmp_path, provider)
    assert result["status"] == "success"
    assert result["success_count"] == 2
    assert result["fail_count"] == 0


def test_sync_without_events_capability_skips_gracefully(tmp_path):
    """无事件能力(如 akshare 适配器形态)的 Provider: 不调用不报错。"""
    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_dates=_DATES,
    )
    result = _run(tmp_path, provider)
    assert result["status"] == "success"
    assert provider.event_calls == []


def test_sync_persists_trade_calendar(tmp_path):
    """日历 upsert 只 flush: 任务层必须显式 commit(会话上下文退出即回滚)。"""
    from sqlalchemy import create_engine, select as sa_select
    from sqlalchemy.orm import sessionmaker
    from backend.models.database import Base
    from backend.models.research import ResearchTradeCalendar
    from backend.models import (  # noqa: F401
        app_setting, data_status, jisilu_account, jisilu_stock, research, valuation,
    )

    provider = FakeProvider(
        bars_by_symbol={"600900.SH": _bars(_DATES), "510300.SH": _bars(_DATES)},
        calendar_dates=_DATES,
    )
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    try:
        result = _run(tmp_path, provider, session_factory=session_factory)
        assert result["status"] == "success"
        with session_factory() as db:
            dates = set(db.scalars(
                sa_select(ResearchTradeCalendar.trade_date)
            ).all())
            assert dates == set(_DATES)
    finally:
        engine.dispose()


def test_default_provider_factory_routes_by_config(tmp_path):
    """默认 Provider 工厂按 config 的 data_source 路由; 未知来源报错。"""
    assert isinstance(
        research_tasks._default_provider_factory("tencent"),
        __import__("backend.services.market_data", fromlist=["TencentFqklineProvider"]).TencentFqklineProvider,
    )
    assert research_tasks._resolve_data_source({"data_source": "Tencent "}) == "tencent"
    assert research_tasks._resolve_data_source({}) == "akshare"
