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
from backend.services import fund_nav, portfolio_assets
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

    def test_fund_candidate_resolved_via_danjuan_detail(self, db) -> None:
        """P1: FUND 候选走蛋卷详情拿名称/类型; 详情失败降级为未解析, 不抛。"""
        def detail(code: str) -> dict:
            assert code == "000001"
            return {"data": {"fd_name": "华夏成长混合", "type_desc": "混合型-灵活"}}

        got = portfolio_assets.probe(
            "000001", db=db, probe_fetch_fn=lambda *_: (None, None), fund_detail_fetch_fn=detail,
        )
        fund = next(c for c in got if c["security_type"] == "FUND")
        assert fund["resolved"] is True
        assert fund["name"] == "华夏成长混合"
        assert fund["type_desc"] == "混合型-灵活"
        assert fund["price_basis"] == "NAV_ADJ"

        def not_on_sale(code: str) -> dict:  # 蛋卷详情对场内 ETF 等"暂不销售"
            raise RuntimeError("该基金暂不销售")

        got2 = portfolio_assets.probe(
            "000001", db=db, probe_fetch_fn=lambda *_: (None, None), fund_detail_fetch_fn=not_on_sale,
        )
        fund2 = next(c for c in got2 if c["security_type"] == "FUND")
        assert fund2["resolved"] is False
        assert fund2["name"] is None

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

    def test_register_fund_uses_otc_and_danjuan(self, db) -> None:
        """P1: 场外基金可注册, exchange=OTC, source=danjuan(不走行情源路由)。"""
        row = portfolio_assets.register("000001.OF", "FUND", "华夏成长混合", db=db)
        assert row["created"] is True
        assert row["symbol"] == "000001.OF"
        assert row["exchange"] == "OTC"
        assert row["source"] == "danjuan"
        assert row["price_basis"] == "NAV_ADJ"

    def test_register_fund_is_idempotent(self, db) -> None:
        first = portfolio_assets.register("000001.OF", "FUND", "华夏成长混合", db=db)
        second = portfolio_assets.register("000001.OF", "FUND", "华夏成长混合", db=db)
        assert first["created"] is True and second["created"] is False
        assert first["id"] == second["id"]

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


def _fund_payload(rows: list[dict]) -> dict:
    """构造蛋卷 nav/history 响应(降序, 含成立首日无 percentage)。"""
    return {"data": {"total_items": len(rows), "items": rows}}


_FUND_ITEMS = [
    {"date": "2026-01-06", "nav": 2.0, "percentage": 1.0, "value": 2.0},
    {"date": "2026-01-05", "nav": 1.9802, "percentage": 1.01, "value": 1.9802},
    {"date": "2026-01-01", "nav": 1.9604, "value": 1.9604},  # 成立首日: 无 percentage
]


class TestFundSyncOne:
    """P1 场外基金: 蛋卷净值 → 链式复权 → fund_nav_daily 幂等覆盖。"""

    def _seed(self, db) -> int:
        return portfolio_assets.register("000001.OF", "FUND", "华夏成长混合", db=db)["id"]

    def test_success_writes_nav_rows_and_status(self, db) -> None:
        security_id = self._seed(db)
        result = portfolio_assets.sync_one(
            security_id, db_factory=lambda: db,
            nav_history_fetch_fn=lambda code, size, page: _fund_payload(_FUND_ITEMS),
        )
        assert result["status"] == "success"
        assert result["last_sync_status"] == "success"
        assert result["rows"] == 3
        assert result["first_date"] == "2026-01-01"
        assert result["last_date"] == "2026-01-06"

        from backend.models.research import FundNavDaily
        rows = db.query(FundNavDaily).filter(FundNavDaily.symbol == "000001.OF").all()
        assert len(rows) == 3
        first = next(r for r in rows if r.nav_date.isoformat() == "2026-01-01")
        last = next(r for r in rows if r.nav_date.isoformat() == "2026-01-06")
        assert first.daily_return_pct is None  # 成立首日事实为 NULL, 不冒充 0
        # 链式: 1.9604 → ×1.0101 → ×1.01
        assert abs(last.adj_nav - 1.9604 * 1.0101 * 1.01) < 1e-9

    def test_resync_is_idempotent_overwrite(self, db) -> None:
        security_id = self._seed(db)
        fetch = lambda code, size, page: _fund_payload(_FUND_ITEMS)  # noqa: E731
        portfolio_assets.sync_one(security_id, db_factory=lambda: db, nav_history_fetch_fn=fetch)
        portfolio_assets.sync_one(security_id, db_factory=lambda: db, nav_history_fetch_fn=fetch)
        from backend.models.research import FundNavDaily
        assert db.query(FundNavDaily).filter(FundNavDaily.symbol == "000001.OF").count() == 3

    def test_failure_writes_failed_status(self, db) -> None:
        security_id = self._seed(db)

        def boom(code: str, size: int, page: int) -> dict:
            raise RuntimeError("danjuan down")

        result = portfolio_assets.sync_one(
            security_id, db_factory=lambda: db, nav_history_fetch_fn=boom,
        )
        assert result["status"] == "failed"
        assert result["last_sync_status"] == "failed"
        assert "danjuan down" in (result["last_sync_error"] or "")

    def test_list_assets_reads_fund_nav_table(self, db) -> None:
        security_id = self._seed(db)
        portfolio_assets.sync_one(
            security_id, db_factory=lambda: db,
            nav_history_fetch_fn=lambda code, size, page: _fund_payload(_FUND_ITEMS),
        )
        rows = portfolio_assets.list_assets(db)
        assert rows[0]["symbol"] == "000001.OF"
        assert rows[0]["row_count"] == 3
        assert rows[0]["first_date"] == "2026-01-01"
        assert rows[0]["price_basis"] == "NAV_ADJ"


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

    def test_create_fund_returns_201_and_runs_danjuan_sync(self, contract_client, wire_sync, monkeypatch) -> None:
        """P1: 场外基金注册成功且后台同步走蛋卷链路(不再 501)。"""
        from datetime import date as _d
        fake_rows = [
            fund_nav.FundNavRow(nav_date=_d(2026, 1, 1), unit_nav=1.9604, daily_return_pct=None, adj_nav=1.9604),
            fund_nav.FundNavRow(nav_date=_d(2026, 1, 5), unit_nav=1.98, daily_return_pct=1.0, adj_nav=1.980004),
        ]
        monkeypatch.setattr(fund_nav, "fetch_nav_history", lambda *a, **k: fake_rows)
        r = contract_client.post(BASE, json={"symbol": "000001.OF", "security_type": "FUND",
                                             "name": "华夏成长混合"})
        assert r.status_code == 201
        body = r.json()
        assert body["symbol"] == "000001.OF"
        assert body["source"] == "danjuan"
        assert body["last_sync_status"] == "running"

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
