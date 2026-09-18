# -*- coding: utf-8 -*-
"""研究回放 API 契约测试(contract_client + thread_db, 不触真实数据源)。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from backend.models.research import ResearchSecurity
from backend.services import research_store
from backend.services.market_data import DailyBar, TradeSession


BASE = "/api/research"


@pytest.fixture()
def thread_db(contract_client, thread_safe_engine):
    from sqlalchemy.orm import sessionmaker

    from backend.models.database import Base

    Base.metadata.create_all(bind=thread_safe_engine)
    TestSession = sessionmaker(bind=thread_safe_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _synthetic_bars(count: int = 320):
    """预热 200+ 的合成 BarInput(raw 与 hfq 同价)。"""
    from backend.services.research_plan import BarInput

    bars = []
    price = 100.0
    d = date(2021, 1, 4)
    for i in range(count):
        swing = 1.0 + (i % 5) * 0.1
        step = 0.3 if i % 2 == 0 else -0.3
        bar = BarInput(
            trade_date=d,
            raw_open=price, raw_high=price + swing, raw_low=price - swing,
            raw_close=price + step,
            hfq_open=price, hfq_high=price + swing, hfq_low=price - swing,
            hfq_close=price + step,
        )
        bars.append(bar)
        price = bar.raw_close
        d += timedelta(days=1)
    return bars


def _seed(db, symbol="600900.SH"):
    bars = _synthetic_bars()
    research_store.upsert_securities(db, [
        {"symbol": symbol, "name": "测试标的", "type": "stock", "selection_list": "高股息"},
    ])
    raw_rows = [
        DailyBar(trade_date=b.trade_date, open=b.raw_open, high=b.raw_high,
                 low=b.raw_low, close=b.raw_close, source="test")
        for b in bars
    ]
    hfq_rows = [
        DailyBar(trade_date=b.trade_date, open=b.hfq_open, high=b.hfq_high,
                 low=b.hfq_low, close=b.hfq_close, source="test")
        for b in bars
    ]
    result = research_store.publish_paired_snapshot(
        db, symbol, raw_rows, hfq_rows, source="test",
        request_start=bars[0].trade_date, request_end=bars[-1].trade_date,
        now=datetime(2026, 9, 18, 8, 0),
    )
    assert result.status == "success"
    research_store.upsert_trade_calendar(
        db,
        [TradeSession(trade_date=b.trade_date, is_open=True) for b in bars],
        source="test", now=datetime(2026, 9, 18, 8, 0),
    )
    return bars


class TestResearchApiContract:

    def test_empty_tables_return_empty_lists(self, contract_client):
        assert contract_client.get(f"{BASE}/securities").json() == []
        assert contract_client.get(f"{BASE}/data-health").json() == []
        assert contract_client.get(f"{BASE}/replays").json() == []

    def test_securities_lists_targets(self, thread_db, contract_client):
        _seed(thread_db)
        r = contract_client.get(f"{BASE}/securities")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        row = data[0]
        assert row["symbol"] == "600900.SH"
        assert row["security_type"] == "STOCK"
        assert row["selection_list"] == "高股息"
        assert row["raw_rows"] > 0 and row["hfq_rows"] > 0
        assert row["data_ready"] is True

    def test_post_replay_without_snapshot_returns_409(self, thread_db, contract_client):
        thread_db.add(ResearchSecurity(
            symbol="NOPE.SH", name="无数据", security_type="STOCK",
            exchange="SSE", source="akshare", selection_list="手动ETF", enabled=True,
        ))
        thread_db.commit()
        r = contract_client.post(f"{BASE}/replays", json={
            "symbol": "NOPE.SH", "start_date": "2024-01-01", "end_date": "2024-12-31",
        })
        assert r.status_code == 409
        assert "USABLE" in r.json()["detail"] or "快照" in r.json()["detail"]

    def test_post_replay_bad_dates_return_422(self, thread_db, contract_client):
        _seed(thread_db)
        r = contract_client.post(f"{BASE}/replays", json={
            "symbol": "600900.SH", "start_date": "not-a-date", "end_date": "2024-12-31",
        })
        assert r.status_code == 422
        r = contract_client.post(f"{BASE}/replays", json={
            "symbol": "600900.SH", "start_date": "2024-12-31", "end_date": "2024-01-01",
        })
        assert r.status_code == 422
        r = contract_client.post(f"{BASE}/replays", json={
            "symbol": "600900.SH", "start_date": "2015-01-01", "end_date": "2024-12-31",
        })
        assert r.status_code == 422  # 超过 3 年上限

    def test_post_replay_summary_days_and_comparison(self, thread_db, contract_client):
        bars = _seed(thread_db)
        start = bars[210].trade_date.isoformat()
        end = bars[300].trade_date.isoformat()
        r = contract_client.post(f"{BASE}/replays", json={
            "symbol": "600900.SH", "start_date": start, "end_date": end,
            "lambdas": [0.2], "windows": [120],
        })
        assert r.status_code == 201
        created = r.json()
        assert created["count"] == 1
        run_id = created["run_ids"][0]

        summary = contract_client.get(f"{BASE}/replays/{run_id}/summary").json()
        assert summary["retrospective"] is True
        assert summary["algorithm_version"] == "ndt-research-v1"
        for segment in ("overall", "train", "validation"):
            for side in ("buy", "sell"):
                tiers = summary[segment][side]
                assert len(tiers) == 3
                for tier in tiers:
                    assert set(tier) == {"tier", "numerator", "denominator", "rate"}
                    if tier["denominator"]:
                        assert abs(tier["rate"] - tier["numerator"] / tier["denominator"]) < 1e-12
        assert summary["train"]["day_count"] + summary["validation"]["day_count"] > 0

        days = contract_client.get(f"{BASE}/replays/{run_id}/days").json()
        assert len(days) == 300 - 210 + 1
        day = days[0]
        assert "plan_date" in day and "day_category" in day
        assert day["status"] in ("ACTIVE", "DISABLED")

        # comparison: 只跑了 1 个组合
        comparison = contract_client.get(
            f"{BASE}/replay-comparison",
            params={"symbol": "600900.SH", "start_date": start, "end_date": end},
        ).json()
        assert len(comparison) == 1
        assert comparison[0]["param_lambda"] == 0.2
        assert "train" in comparison[0] and "validation" in comparison[0]

    def test_post_replay_full_grid_12_runs_and_rerun_idempotent(self, thread_db, contract_client):
        bars = _seed(thread_db)
        start = bars[210].trade_date.isoformat()
        end = bars[260].trade_date.isoformat()
        payload = {"symbol": "600900.SH", "start_date": start, "end_date": end}
        r1 = contract_client.post(f"{BASE}/replays", json=payload)
        assert r1.status_code == 201
        assert r1.json()["count"] == 12
        r2 = contract_client.post(f"{BASE}/replays", json=payload)
        assert r2.status_code == 201
        assert r2.json()["count"] == 12
        replays = contract_client.get(f"{BASE}/replays", params={"symbol": "600900.SH"}).json()
        assert len(replays) == 12  # 幂等, 不翻倍

    def test_unknown_run_returns_404(self, contract_client):
        assert contract_client.get(f"{BASE}/replays/999999/summary").status_code == 404
        assert contract_client.get(f"{BASE}/replays/999999/days").status_code == 404

    def test_data_health_shape(self, thread_db, contract_client):
        bars = _seed(thread_db)
        health = contract_client.get(f"{BASE}/data-health").json()
        assert len(health) == 1
        row = health[0]
        assert row["symbol"] == "600900.SH"
        assert row["raw_rows"] == row["hfq_rows"] == len(bars)
        assert row["unpaired_dates"] == 0
        assert row["calendar_start"] == bars[0].trade_date.isoformat()
