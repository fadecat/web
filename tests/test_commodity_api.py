from datetime import date, datetime

from sqlalchemy import event

from backend.models.commodity import (
    CommodityDailyPrice,
    CommodityInstrument,
    CommodityPercentileDaily,
    CommoditySyncState,
)
from backend.models.data_status import TaskRunLog


def _seed(db):
    db.add_all([
        CommodityInstrument(code="A", name="甲", market="domestic", category="x", source="test", enabled=True, display_order=2),
        CommodityInstrument(code="B", name="乙", market="domestic", category="x", source="test", enabled=True, display_order=1),
        CommodityInstrument(code="OFF", name="禁用", market="domestic", category="x", source="test", enabled=False, display_order=0),
    ])
    db.add_all([
        CommodityDailyPrice(instrument_code="A", trade_date=date(2026, 9, 15), close=10, source="test"),
        CommodityDailyPrice(instrument_code="B", trade_date=date(2026, 9, 14), close=20, source="test"),
    ])
    db.add_all([
        CommodityPercentileDaily(instrument_code="A", trade_date=date(2026, 9, 15), window_code="d21", window_days=21, percentile=95, sample_count=21, signal="high"),
        CommodityPercentileDaily(instrument_code="A", trade_date=date(2026, 9, 15), window_code="y5", window_days=1260, percentile=5, sample_count=1260, signal="low"),
    ])
    db.add(CommoditySyncState(instrument_code="A", status="success", last_attempt_at=datetime(2026, 9, 15, 16), last_success_at=datetime(2026, 9, 15, 16)))
    db.add(TaskRunLog(job_id="commodity_daily", started_at=datetime(2026, 9, 15, 15, 50), finished_at=datetime(2026, 9, 15, 15, 51), status="success"))
    db.commit()


def test_commodity_query_service_pivots_state_and_history(db):
    _seed(db)
    from backend.services.commodity_queries import CommodityQueryService

    service = CommodityQueryService(db, now=datetime(2026, 9, 15, 16))
    rows = service.list_instruments()
    assert [row["code"] for row in rows] == ["A", "B"]
    assert rows[0]["windows"]["d21"]["signal"] == "high"
    assert rows[0]["windows"]["y10"]["percentile"] is None
    assert rows[0]["signal"] == "divergent"
    assert rows[0]["status_label"] == "周期分化"
    assert service.overview()["data_date"] == "2026-09-15"
    assert service.overview()["last_run_at"] == "2026-09-15T15:51:00"
    assert service.history("A", "all")["prices"][0]["date"] == "2026-09-15"


def test_commodity_routes_validate_and_hide_disabled(contract_client, thread_safe_engine):
    from sqlalchemy.orm import sessionmaker

    from backend.models.database import Base
    Base.metadata.create_all(bind=thread_safe_engine)
    db = sessionmaker(bind=thread_safe_engine)()
    try:
        _seed(db)
    finally:
        db.close()
    assert contract_client.get("/api/commodities/overview").status_code == 200
    assert contract_client.get("/api/commodities/OFF").status_code == 404
    assert contract_client.get("/api/commodities/A/history?range=bad").status_code == 422


def test_commodity_filters_sort_nulls_and_calendar_range(db):
    _seed(db)
    db.add_all([
        CommodityDailyPrice(instrument_code="A", trade_date=date(2025, 9, 15), close=8, source="test"),
        CommodityDailyPrice(instrument_code="A", trade_date=date(2026, 3, 15), close=9, source="test"),
        CommodityPercentileDaily(instrument_code="A", trade_date=date(2026, 9, 15), window_code="d63", window_days=63, percentile=90, sample_count=63, signal="high"),
    ])
    db.commit()
    from backend.services.commodity_queries import CommodityQueryService

    service = CommodityQueryService(db, now=datetime(2026, 9, 15, 16))
    assert [row["code"] for row in service.list_instruments(window="d63", signal="high")] == ["A"]
    by_price = service.list_instruments(sort_by="y10", sort_order="desc")
    assert by_price[-1]["code"] == "A"  # missing percentile is always last, then display_order
    history = service.history("A", "1y")
    assert [item["date"] for item in history["prices"]] == ["2025-09-15", "2026-03-15", "2026-09-15"]


def test_commodity_list_uses_batched_reads(db):
    _seed(db)
    from backend.services.commodity_queries import CommodityQueryService

    statements = []
    engine = db.get_bind()
    def listener(*_args):
        statements.append(1)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        CommodityQueryService(db, now=datetime(2026, 9, 15, 16)).list_instruments()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(statements) <= 5
