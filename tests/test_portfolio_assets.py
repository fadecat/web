# -*- coding: utf-8 -*-
"""组合实验室标的注册与同步(P0-2)测试: 全链路 fake provider / fake fetch, 不触真实数据源。

网络在 tests/conftest.py 由 autouse fixture 在 socket 层拦截, 因此所有取数必须注入。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from backend.api.routes import portfolio as portfolio_routes
from backend.models.research import ResearchSecurity
from backend.services import portfolio_assets
from backend.services.market_data import AdjustMode, DailyBar, ResearchSourceError

_DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


def _bars(dates: list[date]) -> list[DailyBar]:
    return [
        DailyBar(trade_date=d, open=9.8, high=10.3, low=9.7, close=10.0,
                 volume=100.0, amount=1000.0, source="fake")
        for d in dates
    ]


class FakeProvider:
    """按 adjust 返回同一组 bar; fail=True 时抛异常。"""

    name = "fake"

    def __init__(self, *, dates: list[date] | None = None, fail: bool = False) -> None:
        self._dates = dates or []
        self.fail = fail
        self.calls: list[str] = []

    def get_daily_bars(self, symbol, start, end, adjust_mode, *, security_type="STOCK"):
        self.calls.append(adjust_mode.name)
        if self.fail:
            raise RuntimeError("network down")
        return _bars(self._dates)


def _provider_factory(provider: FakeProvider):
    return lambda source: provider


# ---------------------------------------------------------------------------
# 归一化(纯函数, 不触网)
# ---------------------------------------------------------------------------

class TestNormalizeCode:
    @pytest.mark.parametrize("raw", ["600900", "sh600900", "600900.SH", "SH600900", "600900.sh"])
    def test_four_input_forms_converge(self, raw: str) -> None:
        """四种写法必须收敛到同一个规范代码。"""
        symbols = {c.symbol for c in portfolio_assets.normalize_code(raw)}
        assert "600900.SH" in symbols

    def test_explicit_exchange_yields_single_candidate(self) -> None:
        """显式给了 sh/sz 就只出场内候选, 不再并列 .OF(六位数字三义冲突只发生在裸码)。"""
        got = portfolio_assets.normalize_code("sz000001")
        assert [(c.symbol, c.security_type) for c in got] == [("000001.SZ", "STOCK")]

    def test_bare_code_includes_fund_candidate_within_cap(self) -> None:
        got = portfolio_assets.normalize_code("000001")
        assert len(got) <= portfolio_assets._MAX_CANDIDATES
        types = {c.security_type for c in got}
        assert {"STOCK", "FUND"} <= types

    def test_type_hint_narrows_to_one(self) -> None:
        got = portfolio_assets.normalize_code("000001", "fund")
        assert [(c.symbol, c.security_type, c.price_basis) for c in got] == [
            ("000001.OF", "FUND", "NAV_ADJ")
        ]

    @pytest.mark.parametrize("raw", ["12345", "abc", "6009001", ""])
    def test_unrecognized_returns_empty(self, raw: str) -> None:
        assert portfolio_assets.normalize_code(raw) == []

    def test_type_contradiction_returns_empty(self) -> None:
        """510300 是沪市 ETF, 当股票解析不成立 → 空, 不要白打一次数据源。"""
        assert portfolio_assets.normalize_code("510300", "stock") == []

    def test_describe_code_problem_distinguishes_422(self) -> None:
        assert portfolio_assets.describe_code_problem("600900") is None
        assert portfolio_assets.describe_code_problem("12345") is not None
        assert portfolio_assets.describe_code_problem("abcdef") is not None


# ---------------------------------------------------------------------------
# probe: 只解析不落库
# ---------------------------------------------------------------------------

class TestProbe:
    def test_resolved_candidate_carries_name_and_latest_date(self, db) -> None:
        def fake_fetch(tencent_code: str, count: int) -> tuple[str | None, str | None]:
            assert tencent_code == "sh600900"
            assert count <= 640  # 腾讯硬约束: count > 640 会静默返回空
            return "长江电力", "2026-09-18"

        got = portfolio_assets.probe("600900.SH", db=db, probe_fetch_fn=fake_fetch)
        stock = next(c for c in got if c["security_type"] == "STOCK")
        assert stock["resolved"] is True
        assert stock["name"] == "长江电力"
        assert stock["latest_date"] == "2026-09-18"
        assert stock["price_basis"] == "HFQ"
        assert db.query(ResearchSecurity).count() == 0  # 不落库

    def test_fund_candidate_not_resolved_but_annotated(self, db) -> None:
        got = portfolio_assets.probe("000001", db=db, probe_fetch_fn=lambda *_: (None, None))
        fund = next(c for c in got if c["security_type"] == "FUND")
        assert fund["resolved"] is False
        assert "P1" in (fund.get("note") or "")

    def test_network_error_degrades_without_raising(self, db) -> None:
        def boom(tencent_code: str, count: int) -> tuple[str | None, str | None]:
            raise RuntimeError("datasource down")

        got = portfolio_assets.probe("600900.SH", db=db, probe_fetch_fn=boom)
        assert got and got[0]["resolved"] is False  # 不向上抛

    def test_probe_reports_local_stats_when_registered(self, db) -> None:
        portfolio_assets.register("600900.SH", "STOCK", "长江电力", db=db)
        got = portfolio_assets.probe("600900.SH", db=db, probe_fetch_fn=lambda *_: (None, None))
        assert got[0]["registered"] is True


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_creates_row(self, db) -> None:
        row = portfolio_assets.register("600900.SH", "STOCK", "长江电力", db=db)
        assert row["created"] is True
        assert row["symbol"] == "600900.SH"
        assert row["exchange"] == "SSE"
        assert row["price_basis"] == "HFQ"
        assert row["selection_list"] == "组合实验室"

    def test_register_is_idempotent(self, db) -> None:
        first = portfolio_assets.register("600900.SH", "STOCK", "长江电力", db=db)
        second = portfolio_assets.register("600900.SH", "STOCK", "长江电力", db=db)
        assert first["created"] is True and second["created"] is False
        assert first["id"] == second["id"]
        assert db.query(ResearchSecurity).count() == 1

    def test_register_fund_rejected_in_p0(self, db) -> None:
        with pytest.raises(ValueError, match="场外基金链路"):
            portfolio_assets.register("000001.OF", "FUND", "华夏成长混合", db=db)

    def test_register_unknown_type_rejected(self, db) -> None:
        with pytest.raises(ValueError, match="未知标的类型"):
            portfolio_assets.register("600900.SH", "BOND", "x", db=db)

    def test_register_type_contradiction_rejected(self, db) -> None:
        with pytest.raises(ResearchSourceError):
            portfolio_assets.register("510300.SH", "STOCK", "沪深300ETF", db=db)


# ---------------------------------------------------------------------------
# sync_one: 回写 last_sync_*
# ---------------------------------------------------------------------------

class TestSyncOne:
    def _seed(self, db) -> int:
        return portfolio_assets.register("600900.SH", "STOCK", "长江电力", db=db)["id"]

    def test_success_writes_status_and_rows(self, db) -> None:
        security_id = self._seed(db)
        provider = FakeProvider(dates=_DATES)
        result = portfolio_assets.sync_one(
            security_id, db_factory=lambda: db, provider_factory_fn=_provider_factory(provider),
        )
        assert result["status"] == "success"
        assert result["last_sync_status"] == "success"
        assert result["last_sync_rows"] is not None and result["last_sync_rows"] > 0
        assert result["first_date"] == _DATES[0].isoformat()
        assert sorted(provider.calls) == ["HFQ", "RAW"]

    def test_failure_writes_failed_and_error(self, db) -> None:
        security_id = self._seed(db)
        provider = FakeProvider(fail=True)
        result = portfolio_assets.sync_one(
            security_id, db_factory=lambda: db, provider_factory_fn=_provider_factory(provider),
        )
        assert result["status"] == "failed"
        assert result["last_sync_status"] == "failed"
        assert "network down" in (result["last_sync_error"] or "")

    def test_error_message_truncated_to_column_width(self, db) -> None:
        security_id = self._seed(db)

        class Boom(FakeProvider):
            def get_daily_bars(self, *args: Any, **kwargs: Any):
                raise RuntimeError("x" * 5000)

        result = portfolio_assets.sync_one(
            security_id, db_factory=lambda: db, provider_factory_fn=_provider_factory(Boom()),
        )
        assert len(result["last_sync_error"]) <= portfolio_assets._MAX_SYNC_ERROR

    def test_unknown_id_does_not_raise(self, db) -> None:
        result = portfolio_assets.sync_one(
            999999, db_factory=lambda: db, provider_factory_fn=_provider_factory(FakeProvider()),
        )
        assert result["status"] == "failed"
        assert result["symbol"] is None

    def test_list_assets_reflects_sync_state(self, db) -> None:
        security_id = self._seed(db)
        portfolio_assets.sync_one(
            security_id, db_factory=lambda: db,
            provider_factory_fn=_provider_factory(FakeProvider(dates=_DATES)),
        )
        rows = portfolio_assets.list_assets(db)
        assert len(rows) == 1
        assert rows[0]["last_sync_status"] == "success"
        assert rows[0]["row_count"] == len(_DATES)


# ---------------------------------------------------------------------------
# 路由契约
# ---------------------------------------------------------------------------

BASE = "/api/portfolio/assets"


def _session_factory_of(engine):
    """每次调用返回**新**会话(sync_one 内部会开两次, 不能复用同一对象)。"""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine)
    return Session


@pytest.fixture()
def thread_db(thread_safe_engine):
    """与 contract_client 同一内存库的独立会话(供测试写数据/断言)。"""
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=thread_safe_engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def wire_sync(monkeypatch, thread_safe_engine):
    """把后台/前台同步全部接到注入通道: 会话工厂 + fake provider, 杜绝真实取数。"""
    monkeypatch.setattr(
        portfolio_routes, "_session_factory", _session_factory_of(thread_safe_engine),
    )
    provider = FakeProvider(dates=_DATES)
    monkeypatch.setattr(portfolio_assets, "provider_factory", _provider_factory(provider))
    return provider


class TestRoutes:
    @pytest.fixture(autouse=True)
    def _pin_probe_fetch(self, monkeypatch):
        """路由层禁止触网: probe 的取数一律注入。"""
        monkeypatch.setattr(
            portfolio_assets, "_default_probe_fetch",
            lambda tencent_code, count: ("长江电力", "2026-09-18"),
        )

    def test_probe_returns_candidates(self, contract_client) -> None:
        r = contract_client.get(f"{BASE}/probe", params={"code": "600900.SH"})
        assert r.status_code == 200
        body = r.json()
        assert body and body[0]["symbol"] == "600900.SH"

    def test_probe_invalid_code_returns_422(self, contract_client) -> None:
        assert contract_client.get(f"{BASE}/probe", params={"code": "12345"}).status_code == 422

    def test_probe_unknown_code_returns_404(self, contract_client) -> None:
        """写法合法但无候选(如 sh000001 是指数, P0 不支持) → 404。"""
        assert contract_client.get(f"{BASE}/probe", params={"code": "sh000001"}).status_code == 404

    def test_create_returns_201_and_running(self, contract_client, thread_db, monkeypatch) -> None:
        monkeypatch.setattr(portfolio_routes, "_session_factory", lambda: thread_db)
        r = contract_client.post(BASE, json={"symbol": "600900.SH", "security_type": "STOCK",
                                             "name": "长江电力"})
        assert r.status_code == 201
        assert r.json()["last_sync_status"] == "running"

    def test_create_fund_returns_501(self, contract_client) -> None:
        r = contract_client.post(BASE, json={"symbol": "000001.OF", "security_type": "FUND",
                                             "name": "华夏成长混合"})
        assert r.status_code == 501

    def test_create_duplicate_is_idempotent(self, contract_client, thread_db, monkeypatch) -> None:
        monkeypatch.setattr(portfolio_routes, "_session_factory", lambda: thread_db)
        payload = {"symbol": "600900.SH", "security_type": "STOCK", "name": "长江电力"}
        assert contract_client.post(BASE, json=payload).json()["created"] is True
        assert contract_client.post(BASE, json=payload).json()["created"] is False
        assert len(contract_client.get(BASE).json()) == 1

    def test_list_assets(self, contract_client, thread_db, monkeypatch) -> None:
        monkeypatch.setattr(portfolio_routes, "_session_factory", lambda: thread_db)
        contract_client.post(BASE, json={"symbol": "600900.SH", "security_type": "STOCK",
                                         "name": "长江电力"})
        body = contract_client.get(BASE).json()
        assert len(body) == 1
        assert body[0]["symbol"] == "600900.SH"
        assert "last_sync_status" in body[0] and "row_count" in body[0]

    def test_refresh_unknown_id_returns_404(self, contract_client) -> None:
        assert contract_client.post(f"{BASE}/999999/refresh").status_code == 404

    def test_refresh_runs_sync(self, contract_client, thread_db, monkeypatch) -> None:
        monkeypatch.setattr(portfolio_routes, "_session_factory", lambda: thread_db)
        created = contract_client.post(BASE, json={"symbol": "600900.SH", "security_type": "STOCK",
                                                   "name": "长江电力"}).json()
        r = contract_client.post(f"{BASE}/{created['id']}/refresh")
        assert r.status_code == 200
        assert r.json()["status"] in ("success", "failed")


def test_adjust_mode_used_by_sync_matches_provider_protocol() -> None:
    """sync_one 必须同时取 RAW 与 HFQ 两条(配对快照的前提)。"""
    assert {AdjustMode.RAW.name, AdjustMode.HFQ.name} == {"RAW", "HFQ"}
