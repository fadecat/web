# -*- coding: utf-8 -*-
"""fund_nav_sync 任务测试(P1): 只抓 FUND、单标的失败隔离、状态回写、股票任务不误抓基金。

全部用内存库 + 注入 fetch, 不触网。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.models.research import FundNavDaily, ResearchSecurity
from backend.services import research_store
from backend.tasks import fund_tasks, research_tasks

_ITEMS = [
    {"date": "2026-01-06", "nav": 2.0, "percentage": 1.0, "value": 2.0},
    {"date": "2026-01-05", "nav": 1.9802, "percentage": 1.01, "value": 1.9802},
    {"date": "2026-01-01", "nav": 1.9604, "value": 1.9604},  # 成立首日无 percentage
]


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _seed(session_factory, rows: list[tuple[str, str, str]]) -> None:
    """rows: [(symbol, type, name)]"""
    with session_factory() as db:
        research_store.upsert_securities(db, [
            {"symbol": s, "name": n, "type": t, "source": "tencent", "selection_list": "组合实验室"}
            for s, t, n in rows
        ])


def _fetch_ok(code: str, size: int, page: int) -> dict:
    return {"data": {"total_items": len(_ITEMS), "items": _ITEMS}}


def _fetch_fail(code: str, size: int, page: int) -> dict:
    raise RuntimeError(f"danjuan down for {code}")


class TestFundNavSync:
    def test_only_fund_securities_are_synced(self, session_factory) -> None:
        _seed(session_factory, [
            ("000001.OF", "fund", "华夏成长混合"),
            ("600900.SH", "stock", "长江电力"),
            ("513100.SH", "etf", "纳指ETF国泰"),
        ])
        calls: list[str] = []

        def fetch(code: str, size: int, page: int) -> dict:
            calls.append(code)
            return _fetch_ok(code, size, page)

        result = fund_tasks.run_fund_nav_sync(
            db_factory=session_factory, nav_history_fetch_fn=fetch,
        )
        assert result["status"] == "success"
        assert result["success_count"] == 1
        assert calls == ["000001"]  # 只请求场外基金, 且用 6 位码
        with session_factory() as db:
            assert db.query(FundNavDaily).count() == 3
            assert db.query(FundNavDaily).first().symbol == "000001.OF"

    def test_writes_sync_state_back_to_security(self, session_factory) -> None:
        _seed(session_factory, [("000001.OF", "fund", "华夏成长混合")])
        fund_tasks.run_fund_nav_sync(
            db_factory=session_factory, nav_history_fetch_fn=_fetch_ok,
        )
        with session_factory() as db:
            row = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == "000001.OF"))
            assert row.last_sync_status == "success"
            assert row.last_sync_rows == 3
            assert row.last_sync_at is not None
            assert row.last_sync_error is None

    def test_single_failure_isolated_and_status_partial(self, session_factory) -> None:
        _seed(session_factory, [
            ("000001.OF", "fund", "华夏成长混合"),
            ("100018.OF", "fund", "富国天利增长债券A"),
        ])

        def fetch(code: str, size: int, page: int) -> dict:
            if code == "000001":
                return _fetch_ok(code, size, page)
            return _fetch_fail(code, size, page)

        result = fund_tasks.run_fund_nav_sync(
            db_factory=session_factory, nav_history_fetch_fn=fetch,
        )
        assert result["status"] == "partial"
        assert result["success_count"] == 1 and result["fail_count"] == 1
        assert "100018.OF" in result["errors"][0]
        with session_factory() as db:
            good = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == "000001.OF"))
            bad = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == "100018.OF"))
            assert good.last_sync_status == "success"
            assert bad.last_sync_status == "failed"
            assert "danjuan down" in (bad.last_sync_error or "")

    def test_no_fund_securities_is_failed_not_crash(self, session_factory) -> None:
        _seed(session_factory, [("600900.SH", "stock", "长江电力")])
        result = fund_tasks.run_fund_nav_sync(
            db_factory=session_factory, nav_history_fetch_fn=_fetch_ok,
        )
        assert result["success_count"] == 0 and result["status"] == "failed"

    def test_research_sync_skips_fund_symbols(self, session_factory, tmp_path) -> None:
        """场外基金不能进 raw/hfq 任务(否则每天拿腾讯抓基金、白记一次失败)。

        判据: 循环确实跑了(股票标的被处理), 但**从未**出现 FUND 标的的错误。
        """
        _seed(session_factory, [("000001.OF", "fund", "华夏成长混合")])
        config = tmp_path / "research.yaml"
        config.write_text(
            'history_start: "2026-01-01"\ntargets:\n  - {symbol: "600900.SH", name: "长江电力", type: "stock"}\n',
            encoding="utf-8",
        )
        seen: list[str] = []

        class Recorder:
            name = "recorder"

            def get_trade_calendar(self):
                return []

            def get_daily_bars(self, symbol, *a, **k):
                seen.append(symbol)
                raise RuntimeError("stop here")  # 不需要真的产出 bar

        result = research_tasks.run_research_daily_sync(
            provider_factory_fn=lambda: Recorder(),
            config_path=str(config),
            db_factory=session_factory,
        )
        assert seen == ["600900.SH"]  # 只处理了股票
        assert not any("000001.OF" in e for e in result.get("errors", []))
